"""ProjectStream FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import connect, library_prune, library_upsert
from .library import match_to_tmdb, parse_filename, scan_directories
from .routes import api, pages
from .state import get_tmdb, reset_state

logger = logging.getLogger("projectstream")
logging.basicConfig(level=logging.INFO)


async def _initial_scan() -> None:
    """Scan configured library dirs once at startup, matching to TMDB."""
    cfg = get_settings()
    tmdb = get_tmdb()
    conn = connect(cfg.db_path)
    try:
        files = scan_directories(cfg.library_dirs)
        for path in files:
            parsed = parse_filename(path.name)
            tmdb_id = await match_to_tmdb(parsed, tmdb)
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
        library_prune(conn, {str(p) for p in files})
        logger.info("library scan: %d files", len(files))
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    cfg = get_settings()
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(cfg.db_path)
    conn.close()

    task = None
    if cfg.library_dirs:
        task = asyncio.create_task(_initial_scan())

    yield

    if task is not None:
        task.cancel()


def create_app() -> FastAPI:
    reset_state()

    app = FastAPI(title="ProjectStream", version="0.1.0", lifespan=lifespan)

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(pages.router)
    app.include_router(api.router)

    return app
