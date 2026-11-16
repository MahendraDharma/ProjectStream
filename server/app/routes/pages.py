"""HTML pages rendered with Jinja2."""

from __future__ import annotations

import json
import sqlite3
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..archive import feature_films
from ..config import get_settings
from ..db import (
    get_conn,
    library_all,
    library_for_tmdb,
    library_get,
    progress_all,
    progress_get,
    subtitle_count,
    subtitle_search,
    watchlist_all,
    watchlist_has,
    wrapped_stats,
)
from ..state import get_tmdb, templates
from ..tmdb import TmdbUnavailable, extract_trailer, watch_providers

router = APIRouter()


def _profile_id(request: Request) -> int:
    try:
        return max(0, int(request.cookies.get("ps_profile", "0") or 0))
    except ValueError:
        return 0


def _active_profile_row(conn: sqlite3.Connection, request: Request):
    from ..db import profile_get

    pid = _profile_id(request)
    return profile_get(conn, pid) if pid else None


def _announcement() -> dict:
    path = get_settings().db_path.parent / "announcement.json"
    if not path.is_file():
        return {"active": False}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"active": False}
    until = data.get("until")
    if until and str(until) < time.strftime("%Y-%m-%d"):
        return {"active": False}
    return {"active": bool(data.get("text")), "text": data.get("text", ""), "url": data.get("url")}


def _ctx(request: Request, conn: sqlite3.Connection | None = None, **extra) -> dict:
    ctx = {"request": request, "tmdb_ready": get_tmdb().ready, "profile_name": None}
    if conn is not None:
        row = _active_profile_row(conn, request)
        ctx["profile_name"] = row["name"] if row else None
    ctx.update(extra)
    return ctx


@router.get("/")
async def home(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    rows: dict[str, list] = {}
    tmdb_error = None

    if get_tmdb().ready:
        try:
            rows["trending"] = [get_tmdb().card(r) for r in (await get_tmdb().trending())[:12]]
            rows["popular_movies"] = [get_tmdb().card(r) for r in (await get_tmdb().popular_movies())[:12]]
            rows["top_rated"] = [get_tmdb().card(r) for r in (await get_tmdb().top_rated_movies())[:12]]
            rows["popular_tv"] = [get_tmdb().card(r) for r in (await get_tmdb().popular_tv())[:12]]
        except TmdbUnavailable as exc:
            tmdb_error = str(exc)

    archive_films = await feature_films(12)
    library_rows = library_all(conn)
    continue_rows = progress_all(conn, _profile_id(request))

    return templates.TemplateResponse(
        request,
        "home.html",
        _ctx(
            request,
            conn,
            rows=rows,
            archive_films=archive_films,
            library=library_rows,
            continue_rows=continue_rows,
            tmdb_error=tmdb_error,
            announcement=_announcement(),
            subtitle_lines=subtitle_count(conn),
        ),
    )


@router.get("/detail/{media_type}/{tmdb_id}")
async def detail(request: Request, media_type: str, tmdb_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    if media_type not in ("movie", "tv"):
        return RedirectResponse("/", status_code=302)

    try:
        data = await get_tmdb().detail(tmdb_id, media_type)
    except TmdbUnavailable as exc:
        return templates.TemplateResponse(request, "detail.html", _ctx(request, conn, unavailable=str(exc)), status_code=503)

    pid = _profile_id(request)
    card = get_tmdb().card(data)
    trailer_key = extract_trailer(data)
    providers = watch_providers(data, get_settings().tmdb_region)
    local_files = library_for_tmdb(conn, tmdb_id, media_type)
    in_watchlist = watchlist_has(conn, tmdb_id, media_type, pid)
    resume = progress_get(conn, tmdb_id, media_type, pid)
    cast = (data.get("credits") or {}).get("cast", [])[:8]
    similar = [get_tmdb().card(r) for r in (data.get("similar") or {}).get("results", [])[:12]]

    return templates.TemplateResponse(
        request,
        "detail.html",
        _ctx(
            request,
            conn,
            item=card,
            overview=data.get("overview"),
            runtime=data.get("runtime") or (data.get("episode_run_time") or [None])[0],
            genres=[g["name"] for g in data.get("genres", [])],
            cast=cast,
            similar=similar,
            trailer_key=trailer_key,
            providers=providers,
            local_files=local_files,
            in_watchlist=in_watchlist,
            resume=resume,
        ),
    )


@router.get("/search")
async def search(request: Request, q: str = "", conn: sqlite3.Connection = Depends(get_conn)):
    query = q.strip()
    results: list[dict] = []
    error = None

    if query and get_tmdb().ready:
        try:
            results = [get_tmdb().card(r) for r in await get_tmdb().search(query)]
            results = [r for r in results if r["media_type"] in ("movie", "tv")]
        except TmdbUnavailable as exc:
            error = str(exc)

    quote_results = []
    if query and len(query) >= 3:
        quote_results = [
            {
                "title": r["lib_title"],
                "season": r["lib_season"],
                "episode": r["lib_episode"],
                "start": r["start_s"],
                "text": r["text"],
                "watch_url": f"/watch/local/{r['library_id']}?t={int(r['start_s'])}",
            }
            for r in subtitle_search(conn, query, limit=10)
        ]

    return templates.TemplateResponse(
        request,
        "search.html",
        _ctx(request, conn, q=query, results=results, error=error, quote_results=quote_results),
    )


@router.get("/watchlist")
async def watchlist(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    pid = _profile_id(request)
    entries = watchlist_all(conn, pid)
    items: list[dict] = []
    if get_tmdb().ready:
        for row in entries:
            try:
                data = await get_tmdb().detail(row["tmdb_id"], row["media_type"])
                card = get_tmdb().card(data)
                card["in_watchlist"] = True
                items.append(card)
            except TmdbUnavailable:
                continue
    return templates.TemplateResponse(request, "watchlist.html", _ctx(request, conn, items=items))


@router.get("/library")
async def library_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return templates.TemplateResponse(request, "library.html", _ctx(request, conn, items=library_all(conn)))


# --- profiles ---------------------------------------------------------------------


@router.get("/profiles")
async def profiles_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    rows = [
        {"id": r["id"], "name": r["name"], "avatar": r["avatar"], "has_pin": bool(r["pin_hash"])}
        for r in _all_profiles(conn)
    ]
    return templates.TemplateResponse(
        request,
        "profiles.html",
        _ctx(request, conn, profiles=rows, active_id=_profile_id(request), active_row=_active_profile_row(conn, request)),
    )


def _all_profiles(conn):
    from ..db import profiles_all

    return profiles_all(conn)


# --- watch party ------------------------------------------------------------------


@router.get("/party")
async def party_page(request: Request):
    return templates.TemplateResponse(request, "party.html", _ctx(request))


@router.get("/party/{code}")
async def party_room(request: Request, code: str):
    from ..party import party_registry as registry

    room = registry.get(code)
    if room is None:
        return templates.TemplateResponse(request, "party.html", _ctx(request, missing_code=code), status_code=404)
    return templates.TemplateResponse(request, "party_room.html", _ctx(request, code=room.code))


# --- TV pairing + remote -------------------------------------------------------------


@router.get("/tv")
async def tv_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return templates.TemplateResponse(request, "tv.html", _ctx(request, library=library_all(conn)))


@router.get("/remote")
async def remote_page(request: Request):
    return templates.TemplateResponse(request, "remote.html", _ctx(request))


# --- wrapped ---------------------------------------------------------------------


@router.get("/wrapped")
async def wrapped_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    stats = wrapped_stats(conn, _profile_id(request))
    top: list[dict] = []
    for entry in stats["top"]:
        card = {
            "tmdb_id": entry["tmdb_id"],
            "media_type": entry["media_type"],
            "title": f"TMDB #{entry['tmdb_id']}",
            "poster": None,
            "minutes": entry["minutes"],
        }
        if get_tmdb().ready:
            try:
                data = await get_tmdb().detail(entry["tmdb_id"], entry["media_type"])
                shaped = get_tmdb().card(data)
                card.update({"title": shaped["title"], "poster": shaped["poster"]})
            except TmdbUnavailable:
                pass
        top.append(card)

    return templates.TemplateResponse(
        request,
        "wrapped.html",
        _ctx(request, conn, stats=stats, top=top, hours=round(stats["total_seconds"] / 3600, 1)),
    )


# --- calendar (upcoming + on the air; flystream "Leaks"/calendar equivalent, legal) ---


@router.get("/calendar")
async def calendar_page(request: Request):
    upcoming: list[dict] = []
    on_air: list[dict] = []
    error = None
    if get_tmdb().ready:
        try:
            upcoming = [get_tmdb().card(r) for r in (await get_tmdb().upcoming_movies())[:18]]
            on_air = [get_tmdb().card(r) for r in (await get_tmdb().on_the_air_tv())[:18]]
        except TmdbUnavailable as exc:
            error = str(exc)
    return templates.TemplateResponse(request, "calendar.html", _ctx(request, upcoming=upcoming, on_air=on_air, error=error))


# --- players ---------------------------------------------------------------------


@router.get("/watch/local/{library_id}")
async def watch_local(request: Request, library_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    row = library_get(conn, library_id)
    if row is None:
        return RedirectResponse("/library", status_code=302)
    pid = _profile_id(request)
    resume = progress_get(conn, row["tmdb_id"], row["media_type"], pid) if row["tmdb_id"] else None
    seek_to = request.query_params.get("t") or resume["position"] if resume else request.query_params.get("t") or 0
    return templates.TemplateResponse(
        request,
        "player.html",
        _ctx(
            request,
            conn,
            title=row["title"],
            subtitle=f"S{row['season']:02d}E{row['episode']:02d}" if row["season"] else None,
            src=f"/api/stream/local/{library_id}",
            kind="local",
            ref_id=library_id,
            tmdb_id=row["tmdb_id"],
            media_type=row["media_type"],
            resume_to=seek_to,
        ),
    )


@router.get("/watch/trailer/{video_id}")
async def watch_trailer(request: Request, video_id: str):
    return templates.TemplateResponse(
        request,
        "player.html",
        _ctx(
            request,
            title="Trailer",
            subtitle=None,
            src=f"/api/stream/trailer/{video_id}",
            kind="trailer",
            ref_id=video_id,
            tmdb_id=None,
            media_type=None,
            resume_to=0,
        ),
    )


@router.get("/watch/archive/{identifier}")
async def watch_archive(request: Request, identifier: str):
    from ..archive import playable_url

    url = await playable_url(identifier)
    if not url:
        return templates.TemplateResponse(
            request,
            "player.html",
            _ctx(
                request,
                title="Tidak tersedia",
                subtitle=None,
                src=None,
                kind="archive",
                ref_id=identifier,
                tmdb_id=None,
                media_type=None,
                resume_to=0,
            ),
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "player.html",
        _ctx(
            request,
            title=identifier.replace("_", " "),
            subtitle=None,
            src=url,
            kind="archive",
            ref_id=identifier,
            tmdb_id=None,
            media_type=None,
            resume_to=0,
        ),
    )


@router.get("/healthz")
async def healthz():
    return {"ok": True}
