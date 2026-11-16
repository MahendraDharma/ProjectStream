"""SQLite storage for the local library, watchlist, and watch progress."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS library (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id     INTEGER,
    media_type  TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
    path        TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    year        INTEGER,
    season      INTEGER,
    episode     INTEGER,
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    added_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_library_tmdb ON library (tmdb_id, media_type);

CREATE TABLE IF NOT EXISTS watchlist (
    tmdb_id    INTEGER NOT NULL,
    media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
    added_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (tmdb_id, media_type)
);

CREATE TABLE IF NOT EXISTS progress (
    tmdb_id    INTEGER NOT NULL,
    media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
    position   REAL NOT NULL DEFAULT 0,
    duration   REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (tmdb_id, media_type)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with the schema applied and row access by name.

    check_same_thread is disabled because FastAPI runs sync dependencies
    and endpoints on (possibly different) worker threads; every request
    builds its own short-lived connection, so there is no cross-thread
    sharing to guard against.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def get_conn() -> sqlite3.Connection:
    """FastAPI dependency: one connection per request."""
    from .config import get_settings

    return connect(get_settings().db_path)


# --- watchlist ---------------------------------------------------------------


def watchlist_add(conn: sqlite3.Connection, tmdb_id: int, media_type: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO watchlist (tmdb_id, media_type) VALUES (?, ?)",
        (tmdb_id, media_type),
    )
    conn.commit()


def watchlist_remove(conn: sqlite3.Connection, tmdb_id: int, media_type: str) -> None:
    conn.execute(
        "DELETE FROM watchlist WHERE tmdb_id = ? AND media_type = ?",
        (tmdb_id, media_type),
    )
    conn.commit()


def watchlist_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()


def watchlist_has(conn: sqlite3.Connection, tmdb_id: int, media_type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM watchlist WHERE tmdb_id = ? AND media_type = ?",
        (tmdb_id, media_type),
    ).fetchone()
    return row is not None


# --- progress ----------------------------------------------------------------


def progress_put(
    conn: sqlite3.Connection,
    tmdb_id: int,
    media_type: str,
    position: float,
    duration: float,
) -> None:
    conn.execute(
        """
        INSERT INTO progress (tmdb_id, media_type, position, duration, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT (tmdb_id, media_type)
        DO UPDATE SET position = excluded.position,
                      duration = excluded.duration,
                      updated_at = datetime('now')
        """,
        (tmdb_id, media_type, position, duration),
    )
    conn.commit()


def progress_get(conn: sqlite3.Connection, tmdb_id: int, media_type: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM progress WHERE tmdb_id = ? AND media_type = ?",
        (tmdb_id, media_type),
    ).fetchone()


def progress_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM progress WHERE duration > 0 AND position > 1 ORDER BY updated_at DESC LIMIT 20"
    ).fetchall()


# --- library -----------------------------------------------------------------


def library_upsert(
    conn: sqlite3.Connection,
    path: str,
    title: str,
    year: int | None,
    media_type: str,
    season: int | None,
    episode: int | None,
    size_bytes: int,
    tmdb_id: int | None,
) -> sqlite3.Row:
    conn.execute(
        """
        INSERT INTO library (path, title, year, media_type, season, episode, size_bytes, tmdb_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (path) DO UPDATE SET
            title = excluded.title,
            year = excluded.year,
            media_type = excluded.media_type,
            season = excluded.season,
            episode = excluded.episode,
            size_bytes = excluded.size_bytes,
            tmdb_id = excluded.tmdb_id
        """,
        (path, title, year, media_type, season, episode, size_bytes, tmdb_id),
    )
    conn.commit()
    return conn.execute("SELECT * FROM library WHERE path = ?", (path,)).fetchone()


def library_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM library ORDER BY title, season, episode").fetchall()


def library_get(conn: sqlite3.Connection, library_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM library WHERE id = ?", (library_id,)).fetchone()


def library_for_tmdb(conn: sqlite3.Connection, tmdb_id: int, media_type: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM library WHERE tmdb_id = ? AND media_type = ? ORDER BY season, episode",
        (tmdb_id, media_type),
    ).fetchall()


def library_prune(conn: sqlite3.Connection, existing_paths: set[str]) -> int:
    """Remove rows whose files no longer exist on disk. Returns rows removed."""
    rows = conn.execute("SELECT id, path FROM library").fetchall()
    removed = 0
    for row in rows:
        if row["path"] not in existing_paths:
            conn.execute("DELETE FROM library WHERE id = ?", (row["id"],))
            removed += 1
    conn.commit()
    return removed
