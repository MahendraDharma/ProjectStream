"""HTML pages rendered with Jinja2."""

from __future__ import annotations

import sqlite3

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
    watchlist_all,
    watchlist_has,
)
from ..state import get_tmdb, templates
from ..tmdb import TmdbUnavailable, extract_trailer, watch_providers

router = APIRouter()


def _ctx(request: Request, **extra) -> dict:
    return {"request": request, "tmdb_ready": get_tmdb().ready, **extra}


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
    continue_rows = progress_all(conn)

    return templates.TemplateResponse(
        request,
        "home.html",
        _ctx(
            request,
            rows=rows,
            archive_films=archive_films,
            library=library_rows,
            continue_rows=continue_rows,
            tmdb_error=tmdb_error,
        ),
    )


@router.get("/detail/{media_type}/{tmdb_id}")
async def detail(request: Request, media_type: str, tmdb_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    if media_type not in ("movie", "tv"):
        return RedirectResponse("/", status_code=302)

    try:
        data = await get_tmdb().detail(tmdb_id, media_type)
    except TmdbUnavailable as exc:
        return templates.TemplateResponse(request, "detail.html", _ctx(request, unavailable=str(exc)), status_code=503)

    card = get_tmdb().card(data)
    trailer_key = extract_trailer(data)
    providers = watch_providers(data, get_settings().tmdb_region)
    local_files = library_for_tmdb(conn, tmdb_id, media_type)
    in_watchlist = watchlist_has(conn, tmdb_id, media_type)
    resume = progress_get(conn, tmdb_id, media_type)
    cast = (data.get("credits") or {}).get("cast", [])[:8]
    similar = [get_tmdb().card(r) for r in (data.get("similar") or {}).get("results", [])[:12]]

    return templates.TemplateResponse(
        request,
        "detail.html",
        _ctx(
            request,
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
async def search(request: Request, q: str = ""):
    query = q.strip()
    results: list[dict] = []
    error = None

    if query and get_tmdb().ready:
        try:
            results = [get_tmdb().card(r) for r in await get_tmdb().search(query)]
            results = [r for r in results if r["media_type"] in ("movie", "tv")]
        except TmdbUnavailable as exc:
            error = str(exc)

    return templates.TemplateResponse(request, "search.html", _ctx(request, q=query, results=results, error=error))


@router.get("/watchlist")
async def watchlist(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    entries = watchlist_all(conn)
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
    return templates.TemplateResponse(request, "watchlist.html", _ctx(request, items=items))


@router.get("/library")
async def library_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return templates.TemplateResponse(request, "library.html", _ctx(request, items=library_all(conn)))


@router.get("/watch/local/{library_id}")
async def watch_local(request: Request, library_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    row = library_get(conn, library_id)
    if row is None:
        return RedirectResponse("/library", status_code=302)
    resume = progress_get(conn, row["tmdb_id"], row["media_type"]) if row["tmdb_id"] else None
    return templates.TemplateResponse(
        request,
        "player.html",
        _ctx(
            request,
            title=row["title"],
            subtitle=f"S{row['season']:02d}E{row['episode']:02d}" if row["season"] else None,
            src=f"/api/stream/local/{library_id}",
            kind="local",
            ref_id=library_id,
            tmdb_id=row["tmdb_id"],
            media_type=row["media_type"],
            resume_to=resume["position"] if resume else 0,
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
