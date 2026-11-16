"""JSON API: watchlist, progress, library rescans, and media streaming."""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse

from ..db import (
    get_conn,
    library_get,
    library_prune,
    library_upsert,
    progress_all,
    progress_get,
    progress_put,
    watchlist_add,
    watchlist_all,
    watchlist_has,
    watchlist_remove,
)
from ..library import match_to_tmdb, parse_filename, scan_directories
from ..state import get_tmdb
from ..streams import file_response
from ..youtube import TrailerResolver, TrailerUnavailable, stream_trailer

router = APIRouter(prefix="/api")

trailer_resolver = TrailerResolver()


# --- watchlist -----------------------------------------------------------------


@router.get("/watchlist")
async def api_watchlist(conn: sqlite3.Connection = Depends(get_conn)):
    rows = watchlist_all(conn)
    return [{"tmdb_id": r["tmdb_id"], "media_type": r["media_type"], "added_at": r["added_at"]} for r in rows]


@router.post("/watchlist")
async def api_watchlist_add(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    body = await request.json()
    tmdb_id, media_type = int(body["tmdb_id"]), body["media_type"]
    if media_type not in ("movie", "tv"):
        raise HTTPException(422, "media_type must be movie or tv")
    watchlist_add(conn, tmdb_id, media_type)
    return {"ok": True, "in_watchlist": True}


@router.delete("/watchlist/{media_type}/{tmdb_id}")
async def api_watchlist_remove(media_type: str, tmdb_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    watchlist_remove(conn, tmdb_id, media_type)
    return {"ok": True, "in_watchlist": False}


@router.get("/watchlist/has/{media_type}/{tmdb_id}")
async def api_watchlist_has(media_type: str, tmdb_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return {"in_watchlist": watchlist_has(conn, tmdb_id, media_type)}


# --- progress ------------------------------------------------------------------


@router.post("/progress")
async def api_progress_put(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    body = await request.json()
    tmdb_id, media_type = int(body["tmdb_id"]), body["media_type"]
    position, duration = float(body["position"]), float(body["duration"])
    progress_put(conn, tmdb_id, media_type, position, duration)
    return {"ok": True}


@router.get("/progress")
async def api_progress(conn: sqlite3.Connection = Depends(get_conn)):
    return [
        {
            "tmdb_id": r["tmdb_id"],
            "media_type": r["media_type"],
            "position": r["position"],
            "duration": r["duration"],
            "updated_at": r["updated_at"],
        }
        for r in progress_all(conn)
    ]


@router.get("/progress/{media_type}/{tmdb_id}")
async def api_progress_get(media_type: str, tmdb_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    row = progress_get(conn, tmdb_id, media_type)
    if row is None:
        return {"position": 0, "duration": 0}
    return {"position": row["position"], "duration": row["duration"]}


# --- library -------------------------------------------------------------------


@router.post("/library/rescan")
async def api_library_rescan(conn: sqlite3.Connection = Depends(get_conn)):
    from ..config import get_settings

    settings = get_settings()
    files = scan_directories(settings.library_dirs)
    scanned = 0
    matched = 0

    for path in files:
        parsed = parse_filename(path.name)
        tmdb_id = await match_to_tmdb(parsed, get_tmdb())
        library_upsert(
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

    pruned = library_prune(conn, {str(p) for p in files})
    return {"ok": True, "scanned": scanned, "matched": matched, "pruned": pruned}


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
