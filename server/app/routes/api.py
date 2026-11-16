"""JSON API: profiles, watchlist, progress, parties, TV pairing, seals, streaming.

Organized per subsystem, mirroring the flystream.net surface where the shapes
are educational (seal-attested playback-search, party REST+WS, tv-pair/claim)
while every source stays legal (own library only).
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, StreamingResponse

from ..config import get_settings
from ..db import (
    device_pair_create,
    get_conn,
    intro_marker_get,
    intro_marker_put,
    library_for_tmdb,
    library_get,
    library_prune,
    library_upsert,
    playback_error_add,
    profile_create,
    profile_delete,
    profile_get,
    profiles_all,
    progress_all,
    progress_get,
    progress_put,
    subtitle_search,
    watch_minutes_add,
    watchlist_add,
    watchlist_all,
    watchlist_has,
    watchlist_remove,
)
from ..library import match_to_tmdb, parse_filename, scan_directories
from ..party import ALLOWED_ACTIONS, Member
from ..party import registry as party_registry
from ..resolver import STATUS_REBIND
from ..resolver import resolve as resolve_sources
from ..seals import issue as issue_seal
from ..seals import verify as verify_seal
from ..state import get_tmdb
from ..streams import file_response
from ..tv import tv_registry
from ..youtube import TrailerResolver, TrailerUnavailable, stream_trailer

router = APIRouter(prefix="/api")

logger = logging.getLogger("projectstream")

trailer_resolver = TrailerResolver()

BOOT_TIME = time.time()


def _active_profile(request: Request) -> int:
    try:
        return max(0, int(request.cookies.get("ps_profile", "0") or 0))
    except ValueError:
        return 0


# --- profiles -----------------------------------------------------------------


@router.get("/profiles")
async def api_profiles(conn: sqlite3.Connection = Depends(get_conn)):
    return [
        {"id": r["id"], "name": r["name"], "avatar": r["avatar"], "has_pin": bool(r["pin_hash"])}
        for r in profiles_all(conn)
    ]


@router.post("/profiles")
async def api_profile_create(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    body = await request.json()
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "name is required")
    avatar = str(body.get("avatar") or "🎬")
    pin = str(body.get("pin") or "").strip() or None
    row = profile_create(conn, name, avatar, pin)
    return {
        "ok": True,
        "profile": {"id": row["id"], "name": row["name"], "avatar": row["avatar"], "has_pin": bool(row["pin_hash"])},
    }


@router.post("/profiles/select")
async def api_profile_select(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    body = await request.json()
    profile_id = int(body.get("id") or 0)
    row = profile_get(conn, profile_id)
    if row is None:
        raise HTTPException(404, "profile not found")

    pin = str(body.get("pin") or "").strip()
    from ..db import hash_pin

    if row["pin_hash"] and (not pin or hash_pin(pin) != row["pin_hash"]):
        raise HTTPException(403, "wrong pin")

    response = {"ok": True, "profile": {"id": row["id"], "name": row["name"], "avatar": row["avatar"]}}
    return response


@router.delete("/profiles/{profile_id}")
async def api_profile_delete(profile_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    profile_delete(conn, profile_id)
    return {"ok": True}


# --- watchlist (profile-scoped) -----------------------------------------------


@router.get("/watchlist")
async def api_watchlist(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    rows = watchlist_all(conn, _active_profile(request))
    return [
        {"tmdb_id": r["tmdb_id"], "media_type": r["media_type"], "added_at": r["added_at"]} for r in rows
    ]


@router.post("/watchlist")
async def api_watchlist_add(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    body = await request.json()
    tmdb_id, media_type = int(body["tmdb_id"]), body["media_type"]
    if media_type not in ("movie", "tv"):
        raise HTTPException(422, "media_type must be movie or tv")
    watchlist_add(conn, tmdb_id, media_type, _active_profile(request))
    return {"ok": True, "in_watchlist": True}


@router.delete("/watchlist/{media_type}/{tmdb_id}")
async def api_watchlist_remove(media_type: str, tmdb_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    watchlist_remove(conn, tmdb_id, media_type, _active_profile(request))
    return {"ok": True, "in_watchlist": False}


@router.get("/watchlist/has/{media_type}/{tmdb_id}")
async def api_watchlist_has(media_type: str, tmdb_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return {"in_watchlist": watchlist_has(conn, tmdb_id, media_type, _active_profile(request))}


# --- progress (profile-scoped) -------------------------------------------------


@router.post("/progress")
async def api_progress_put(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    body = await request.json()
    tmdb_id, media_type = int(body["tmdb_id"]), body["media_type"]
    position, duration = float(body["position"]), float(body["duration"])
    progress_put(conn, tmdb_id, media_type, position, duration, _active_profile(request))
    return {"ok": True}


@router.get("/progress")
async def api_progress(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return [
        {
            "tmdb_id": r["tmdb_id"],
            "media_type": r["media_type"],
            "position": r["position"],
            "duration": r["duration"],
            "updated_at": r["updated_at"],
        }
        for r in progress_all(conn, _active_profile(request))
    ]


@router.get("/progress/{media_type}/{tmdb_id}")
async def api_progress_get(media_type: str, tmdb_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    row = progress_get(conn, tmdb_id, media_type, _active_profile(request))
    if row is None:
        return {"position": 0, "duration": 0}
    return {"position": row["position"], "duration": row["duration"]}


@router.post("/watched/{media_type}/{tmdb_id}")
async def api_watched(media_type: str, tmdb_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    """Mark a title as watched (the eye button on the detail page)."""
    if media_type not in ("movie", "tv"):
        raise HTTPException(422, "media_type must be movie or tv")
    progress_put(conn, tmdb_id, media_type, 1, 1, _active_profile(request))
    return {"ok": True, "watched": True}


# --- watch minutes (Wrapped telemetry) ------------------------------------------


@router.post("/watch-minutes")
async def api_watch_minutes(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    body = await request.json()
    seconds = int(body.get("seconds") or 0)
    tmdb_id = int(body["tmdb_id"]) if body.get("tmdb_id") is not None else None
    media_type = body.get("media_type")
    watch_minutes_add(conn, _active_profile(request), tmdb_id, media_type, seconds)
    return {"ok": True}


# --- seal attestation + playback-search (flystream.net flow, legal sources) ------


@router.post("/guard-attest")
async def api_guard_attest(request: Request):
    """Issue a short-lived seal binding the calling client id."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    client_id = str(body.get("client_id") or "")[:64] or f"anon-{time.time_ns()}"
    return issue_seal(client_id)


@router.post("/playback-search")
async def api_playback_search(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    """Resolve playback sources; requires a valid X-FS-Seal like flystream.net.

    Status codes mirror their state machine:
    NO_STREAM_LOOKUP_ID / SESSION_REBIND_REQUIRED / STREAM_UNAVAILABLE / OK.
    """
    seal = request.headers.get("x-fs-seal")
    try:
        body = await request.json()
    except Exception:
        body = {}

    if not verify_seal(seal):
        return {"status": STATUS_REBIND, "sources": [], "hint": "call /api/guard-attest again"}

    tmdb_id = body.get("tmdb_id")
    media_type = body.get("media_type")
    if tmdb_id is None or media_type not in ("movie", "tv"):
        return {"status": "NO_STREAM_LOOKUP_ID", "sources": []}

    return resolve_sources(conn, int(tmdb_id), media_type)


# --- intro markers ---------------------------------------------------------------


@router.get("/intro")
async def api_intro_get(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    tmdb_id = request.query_params.get("tmdb")
    media_type = request.query_params.get("type")
    if not tmdb_id or media_type not in ("movie", "tv"):
        raise HTTPException(422, "tmdb and type are required")
    row = intro_marker_get(conn, int(tmdb_id), media_type)
    if row is None:
        return {"has_intro": False}
    return {"has_intro": True, "start": row["start_s"], "end": row["end_s"]}


@router.post("/intro")
async def api_intro_put(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    body = await request.json()
    tmdb_id, media_type = int(body["tmdb_id"]), body["media_type"]
    if media_type not in ("movie", "tv"):
        raise HTTPException(422, "media_type must be movie or tv")
    intro_marker_put(conn, tmdb_id, media_type, float(body["start"]), float(body["end"]))
    return {"ok": True}


# --- quote search (own subtitles only) ---------------------------------------------


@router.get("/quote-search")
async def api_quote_search(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    q = (request.query_params.get("q") or "").strip()
    if len(q) < 3:
        return {"query": q, "results": []}

    rows = subtitle_search(conn, q, limit=30)
    results = []
    for row in rows:
        results.append(
            {
                "library_id": row["library_id"],
                "title": row["lib_title"],
                "year": row["lib_year"],
                "season": row["lib_season"],
                "episode": row["lib_episode"],
                "start": row["start_s"],
                "end": row["end_s"],
                "text": row["text"],
                "watch_url": f"/watch/local/{row['library_id']}?t={int(row['start_s'])}",
            }
        )
    return {"query": q, "results": results}


# --- search (JSON for live dropdown) ------------------------------------------------


@router.get("/search")
async def api_search(request: Request, q: str = ""):
    query = q.strip()
    if not query or not get_tmdb().ready:
        return {"query": query, "results": []}

    results = []
    try:
        raw = await get_tmdb().search(query)
        results = [get_tmdb().card(r) for r in raw if r.get("media_type") in ("movie", "tv")]
    except Exception as exc:  # TMDB flakiness must not break the dropdown
        logger.warning("live search failed: %s", exc)
    return {"query": query, "results": results[:8]}


# --- announcements + status ----------------------------------------------------------


@router.get("/announcement")
async def api_announcement():
    path = get_settings().db_path.parent / "announcement.json"
    if not path.is_file():
        return {"active": False}
    try:
        import json

        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"active": False}

    until = data.get("until")
    if until and str(until) < time.strftime("%Y-%m-%d"):
        return {"active": False}
    return {"active": bool(data.get("text")), "text": data.get("text", ""), "url": data.get("url")}


@router.get("/status")
async def api_status(conn: sqlite3.Connection = Depends(get_conn)):
    db_path = get_settings().db_path
    return {
        "ok": True,
        "version": "0.1.0",
        "uptime_seconds": int(time.time() - BOOT_TIME),
        "db_bytes": db_path.stat().st_size if db_path.is_file() else 0,
        "tmdb_ready": get_tmdb().ready,
    }


# --- library -------------------------------------------------------------------


@router.get("/library")
async def api_library(conn: sqlite3.Connection = Depends(get_conn)):
    from ..db import library_all

    return [
        {
            "id": r["id"],
            "title": r["title"],
            "year": r["year"],
            "season": r["season"],
            "episode": r["episode"],
            "media_type": r["media_type"],
        }
        for r in library_all(conn)
    ]


@router.post("/library/rescan")
async def api_library_rescan(conn: sqlite3.Connection = Depends(get_conn)):
    from ..db import subtitle_index_replace
    from ..srt import parse_srt, sibling_subtitle_files

    settings = get_settings()
    files = scan_directories(settings.library_dirs)
    scanned = 0
    matched = 0
    subtitles = 0

    for path in files:
        parsed = parse_filename(path.name)
        tmdb_id = await match_to_tmdb(parsed, get_tmdb())
        row = library_upsert(
            conn,
            path=str(path),
            title=parsed.title,
            year=parsed.year,
            media_type=parsed.media_type,
            season=parsed.season,
            episode=parsed.episode,
            size_bytes=path.stat().st_size,
            tmdb_id=tmdb_id,
        )
        scanned += 1
        matched += 1 if tmdb_id else 0

        srts = sibling_subtitle_files(path)
        if srts:
            lines = parse_srt(srts[0])
            if lines:
                subtitle_index_replace(conn, row["id"], lines)
                subtitles += len(lines)

    pruned = library_prune(conn, {str(p) for p in files})
    return {"ok": True, "scanned": scanned, "matched": matched, "pruned": pruned, "subtitle_lines": subtitles}


# --- image proxy (flystream.net lesson: third-party artwork never touches the browser) ---

_IMAGE_ALLOW = re.compile(
    r"^https://image\.tmdb\.org/t/p/(original|w92|w154|w185|w342|w500|w780|w1280)/[A-Za-z0-9_\-\.]+\.(?:jpg|jpeg|png|webp)$",
)

_image_http: httpx.AsyncClient | None = None


def _image_client() -> httpx.AsyncClient:
    global _image_http
    if _image_http is None or _image_http.is_closed:
        _image_http = httpx.AsyncClient(timeout=20, follow_redirects=False)
    return _image_http


@router.get("/tmdb-image")
async def api_tmdb_image(url: str):
    """Proxy TMDB artwork through this server with a strict allowlist."""
    if not _IMAGE_ALLOW.match(url or ""):
        raise HTTPException(400, "image URL not allowed")

    try:
        upstream = await _image_client().get(url)
        upstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"image upstream failed: {exc}") from exc

    return StreamingResponse(
        iter([upstream.content]),
        media_type=upstream.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400", "Accept-Ranges": "none"},
    )


# --- playback error report (flystream.net lesson: client-side failures feed ops) ---


@router.post("/playback-error-report")
async def api_playback_error_report(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    body = await request.json()
    media_type = body.get("media_type")
    if media_type not in (None, "movie", "tv"):
        raise HTTPException(422, "media_type must be movie, tv, or null")

    playback_error_add(
        conn,
        media_type=media_type,
        tmdb_id=int(body["tmdb_id"]) if body.get("tmdb_id") is not None else None,
        src_kind=str(body.get("src_kind") or "unknown")[:40],
        message=str(body.get("message") or "")[:500],
    )
    return {"ok": True}


# --- TV pairing + remote ----------------------------------------------------------


@router.post("/tv-pair")
async def api_tv_pair(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    """TV side: register a pairing code (the TV page shows it large on screen)."""
    body = await request.json()
    device_name = str(body.get("device_name") or "TV")
    session = tv_registry.create(device_name)
    device_pair_create(conn, session.code, device_name)
    return {"ok": True, "code": session.code}


@router.post("/tv/claim")
async def api_tv_claim(request: Request):
    """Controller side: claim a paired TV by its code."""
    body = await request.json()
    code = str(body.get("code") or "")
    session = tv_registry.claim(code)
    if session is None:
        raise HTTPException(404, "pairing code not found or already claimed")
    return {"ok": True, "code": session.code, "device_name": session.device_name}


@router.websocket("/tv/ws")
async def tv_ws(ws: WebSocket):
    """Relay controller commands to a paired TV screen (both connect here)."""
    code = ws.query_params.get("code", "")
    role = ws.query_params.get("role", "")

    await ws.accept()
    session = tv_registry.get(code)

    if session is None or role not in ("screen", "controller"):
        await ws.send_json({"type": "error", "message": "invalid code or role"})
        await ws.close()
        return

    if role == "screen":
        session.screen = ws
        await ws.send_json({"type": "paired", "role": "screen"})
    else:
        session.controller = ws
        await ws.send_json({"type": "paired", "role": "controller"})
        if session.screen is not None:
            await session.screen.send_json({"type": "controller-joined"})

    try:
        while True:
            message = await ws.receive_json()
            if role == "controller" and session.screen is not None:
                # only known command types are relayed to the screen
                if message.get("type") in ("play", "pause", "seek", "load", "ping"):
                    await session.screen.send_json(message)
            elif role == "screen" and session.controller is not None:
                if message.get("type") in ("state", "pong"):
                    await session.controller.send_json(message)
    except WebSocketDisconnect:
        pass
    finally:
        if role == "screen" and tv_registry.get(code) is not None:
            tv_registry.drop(code)
        elif role == "controller" and tv_registry.get(code) is not None:
            tv_registry.get(code).controller = None


# --- watch party REST ----------------------------------------------------------------


@router.post("/party/create")
async def party_create(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    name = str(body.get("name") or "Host").strip()[:24] or "Host"
    room = party_registry.create(name)
    return {"ok": True, "code": room.code}


@router.get("/party/peek")
async def api_party_peek(request: Request, code: str):
    info = party_registry.peek(code)
    if info is None:
        raise HTTPException(404, "party not found")
    return info


@router.post("/party/join")
async def api_party_join(request: Request):
    body = await request.json()
    code = str(body.get("code") or "").upper()
    room = party_registry.get(code)
    if room is None:
        raise HTTPException(404, "party not found")
    return {"ok": True, "code": room.code, "members": len(room.members)}


@router.post("/party/leave")
async def api_party_leave(request: Request):
    body = await request.json()
    code = str(body.get("code") or "").upper()
    member_id = str(body.get("member_id") or "")
    room = party_registry.get(code)
    if room is not None and member_id:
        party_registry.leave(room, member_id)
    return {"ok": True}


# --- watch party WebSocket -------------------------------------------------------------


@router.websocket("/party/ws")
async def party_ws(ws: WebSocket):
    code = ws.query_params.get("code", "")
    room = party_registry.get(code)
    await ws.accept()

    if room is None:
        await ws.send_json({"type": "error", "message": "party not found"})
        await ws.close()
        return

    member_id = secrets_token()
    member = Member(member_id=member_id, name="", role="guest", send=ws.send_json)
    # NOTE: the member is registered into the room only AFTER its hello —
    # joining on connect let broadcasts reach a client before its own
    # "joined" message and put un-hello'd ghosts into member snapshots.

    async def send(payload: dict) -> None:
        await ws.send_json(payload)

    try:
        # first message must be hello
        hello = await ws.receive_json()
        if hello.get("type") != "hello":
            await ws.send_json({"type": "error", "message": "expected hello"})
            await ws.close()
            return

        member.name = str(hello.get("name") or "Tamu")[:24]
        async with room.lock:
            if room.host is None:
                member.role = "host"
        party_registry.join(room, member)
        await send(
            {
                "type": "joined",
                "you": member_id,
                "role": member.role,
                "members": party_registry.members_snapshot(room),
                "state": room.state,
                "chat": room.chat[-30:],
            }
        )
        await _broadcast(room, {"type": "members", "members": party_registry.members_snapshot(room)}, exclude=member_id)
        if member.role == "host":
            await _broadcast(room, {"type": "host", "member_id": member_id})

        while True:
            raw = await ws.receive_json()
            action = raw.get("type")
            if action not in ALLOWED_ACTIONS:
                continue

            if action in ("play", "pause", "seek", "state"):
                if member.role != "host":
                    await send({"type": "error", "message": "only the host controls playback"})
                    continue
                async with room.lock:
                    if action == "state":
                        room.state = {
                            "tmdb_id": raw.get("tmdb_id"),
                            "media_type": raw.get("media_type"),
                            "library_id": raw.get("library_id"),
                            "playing": bool(raw.get("playing", False)),
                            "position": float(raw.get("position") or 0),
                            "updated_at": time.time(),
                        }
                    elif action in ("play", "pause"):
                        room.state["playing"] = action == "play"
                        room.state["position"] = float(raw.get("position") or room.state.get("position") or 0)
                        room.state["updated_at"] = time.time()
                    elif action == "seek":
                        room.state["position"] = float(raw.get("position") or 0)
                        room.state["updated_at"] = time.time()
                await _broadcast(room, {"type": action, **{k: v for k, v in raw.items() if k != "type"}}, exclude=member_id)

            elif action in ("chat", "emoji"):
                entry = {
                    "type": action,
                    "member_id": member_id,
                    "name": member.name,
                    "text": str(raw.get("text") or raw.get("emoji") or "")[:200],
                    "at": time.time(),
                }
                room.chat.append(entry)
                room.chat[:] = room.chat[-100:]
                await _broadcast(room, entry, exclude=member_id)

            elif action == "sync":
                # guests may ask for the current state to re-sync
                await send({"type": "state", **room.state})

    except WebSocketDisconnect:
        pass
    finally:
        was_host = member.role == "host"
        party_registry.leave(room, member_id)
        await _broadcast(room, {"type": "members", "members": party_registry.members_snapshot(room)})
        if was_host:
            new_host = room.host
            if new_host is not None:
                await _broadcast(room, {"type": "host", "member_id": new_host.member_id})


def secrets_token() -> str:
    import secrets

    return secrets.token_hex(8)


_ = asyncio


async def _broadcast(room, payload: dict, exclude: str | None = None) -> None:
    """Send a payload to every member; drop members whose connection died."""
    for member in list(room.members.values()):
        if member.member_id == exclude:
            continue
        try:
            await member.send(payload)
        except Exception:
            party_registry.leave(room, member.member_id)


# --- streaming -----------------------------------------------------------------


@router.get("/stream/local/{library_id}")
async def api_stream_local(library_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    row = library_get(conn, library_id)
    if row is None:
        raise HTTPException(404, "library item not found")

    path = row["path"]
    try:
        return file_response(path, request.headers.get("range"))
    except FileNotFoundError:
        raise HTTPException(410, "file no longer on disk") from None


@router.get("/download/local/{library_id}")
async def api_download_local(library_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    """Attachment download of a library file (the legal analog of their downloads)."""
    import os
    from urllib.parse import quote

    row = library_get(conn, library_id)
    if row is None:
        raise HTTPException(404, "library item not found")

    path = row["path"]
    if not os.path.isfile(path):
        raise HTTPException(410, "file no longer on disk")

    filename = quote(os.path.basename(path))
    return StreamingResponse(
        _iter_download(path),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Content-Length": str(os.path.getsize(path)),
        },
    )


async def _iter_download(path: str):
    import os

    remaining = os.path.getsize(path)
    with open(path, "rb") as handle:
        while remaining > 0:
            chunk = handle.read(min(256 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/stream/trailer/{video_id}")
async def api_stream_trailer(video_id: str):
    logger = logging.getLogger("projectstream.youtube")

    try:
        await trailer_resolver.resolve(video_id)  # fail with a proper status before streaming starts
    except TrailerUnavailable as exc:
        raise HTTPException(502, f"trailer unavailable: {exc}") from exc

    async def relay():
        try:
            async for chunk in stream_trailer(trailer_resolver, video_id):
                yield chunk
        except Exception:
            logger.warning("trailer stream aborted for %s", video_id, exc_info=True)

    return StreamingResponse(relay(), media_type="video/mp4")


# --- misc ----------------------------------------------------------------------


@router.get("/redirect/remote")
async def api_redirect_remote(url: str):
    """Only allow redirecting to archive.org download links (player fallback)."""
    if not url.startswith("https://archive.org/download/"):
        return RedirectResponse("/", status_code=302)
    return RedirectResponse(url, status_code=302)


# keep library_for_tmdb import used even if a future edit drops its direct use
_ = library_for_tmdb
