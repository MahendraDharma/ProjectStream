"""SQLite storage: library, profiles, watchlist, progress, parties support data.

Schema notes (flystream.net functional-parity build, 2026-11-17):
- profiles        : Netflix-style multi-profile with optional PIN lock
- watchlist/progress : scoped per profile via profile_id (0 = legacy/global)
- watch_sessions  : watch-minutes telemetry feeding the "Wrapped" page
- subtitle_lines  : .srt index for quote-search
- intro_markers   : per-title skip-intro windows
- device_pairs    : TV pairing codes (screen role joins parties)
"""

from __future__ import annotations

import hashlib
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
    profile_id INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tmdb_id, media_type, profile_id)
);

CREATE TABLE IF NOT EXISTS playback_errors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id    INTEGER,
    media_type TEXT CHECK (media_type IN ('movie', 'tv') OR media_type IS NULL),
    src_kind   TEXT NOT NULL DEFAULT 'unknown',
    message    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS profiles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    avatar     TEXT NOT NULL DEFAULT '🎬',
    pin_hash   TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS watch_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL DEFAULT 0,
    tmdb_id    INTEGER,
    media_type TEXT,
    seconds    INTEGER NOT NULL DEFAULT 0,
    day        TEXT NOT NULL DEFAULT (date('now'))
);

CREATE TABLE IF NOT EXISTS subtitle_lines (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id INTEGER NOT NULL,
    idx        INTEGER NOT NULL DEFAULT 0,
    start_s    REAL NOT NULL DEFAULT 0,
    end_s      REAL NOT NULL DEFAULT 0,
    text       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subtitle_text ON subtitle_lines (text);

CREATE TABLE IF NOT EXISTS intro_markers (
    tmdb_id    INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    start_s    REAL NOT NULL DEFAULT 0,
    end_s      REAL NOT NULL DEFAULT 0,
    UNIQUE (tmdb_id, media_type)
);

CREATE TABLE IF NOT EXISTS device_pairs (
    code        TEXT PRIMARY KEY,
    device_name TEXT NOT NULL DEFAULT 'TV',
    role        TEXT NOT NULL DEFAULT 'screen',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Add a column when it is missing (upgrade path for pre-profile databases)."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _migrate_progress(conn: sqlite3.Connection) -> None:
    """Rebuild progress when it predates profile scoping.

    The old table had UNIQUE (tmdb_id, media_type); profile-aware rows need
    UNIQUE (tmdb_id, media_type, profile_id), which ALTER cannot express —
    so copy rows into a freshly created table.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(progress)").fetchall()}
    if "profile_id" in cols:
        return

    conn.execute("ALTER TABLE progress RENAME TO progress_old")
    conn.execute(
        """
        CREATE TABLE progress (
            tmdb_id    INTEGER NOT NULL,
            media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
            position   REAL NOT NULL DEFAULT 0,
            duration   REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            profile_id INTEGER NOT NULL DEFAULT 0,
            UNIQUE (tmdb_id, media_type, profile_id)
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO progress (tmdb_id, media_type, position, duration, updated_at, profile_id)
        SELECT tmdb_id, media_type, position, duration, updated_at, 0 FROM progress_old
        """
    )
    conn.execute("DROP TABLE progress_old")
    conn.commit()


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
    # upgrade pre-profile databases in place
    _ensure_column(conn, "watchlist", "profile_id", "INTEGER NOT NULL DEFAULT 0")
    _migrate_progress(conn)
    return conn


def get_conn() -> sqlite3.Connection:
    """FastAPI dependency: one connection per request."""
    from .config import get_settings

    return connect(get_settings().db_path)


def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()


# --- profiles -------------------------------------------------------------------


def profile_create(conn: sqlite3.Connection, name: str, avatar: str, pin: str | None) -> sqlite3.Row:
    cur = conn.execute(
        "INSERT INTO profiles (name, avatar, pin_hash) VALUES (?, ?, ?)",
        (name[:40], avatar[:8], hash_pin(pin) if pin else None),
    )
    conn.commit()
    return conn.execute("SELECT * FROM profiles WHERE id = ?", (cur.lastrowid,)).fetchone()


def profiles_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM profiles ORDER BY id").fetchall()


def profile_get(conn: sqlite3.Connection, profile_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()


def profile_delete(conn: sqlite3.Connection, profile_id: int) -> None:
    conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    conn.commit()


# --- watchlist (profile-scoped) ---------------------------------------------------


def watchlist_add(conn: sqlite3.Connection, tmdb_id: int, media_type: str, profile_id: int = 0) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO watchlist (tmdb_id, media_type, profile_id) VALUES (?, ?, ?)",
        (tmdb_id, media_type, profile_id),
    )
    conn.commit()


def watchlist_remove(conn: sqlite3.Connection, tmdb_id: int, media_type: str, profile_id: int = 0) -> None:
    conn.execute(
        "DELETE FROM watchlist WHERE tmdb_id = ? AND media_type = ? AND profile_id = ?",
        (tmdb_id, media_type, profile_id),
    )
    conn.commit()


def watchlist_all(conn: sqlite3.Connection, profile_id: int | None = None) -> list[sqlite3.Row]:
    if profile_id is None:
        return conn.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()
    return conn.execute(
        "SELECT * FROM watchlist WHERE profile_id = ? ORDER BY added_at DESC", (profile_id,)
    ).fetchall()


def watchlist_has(conn: sqlite3.Connection, tmdb_id: int, media_type: str, profile_id: int = 0) -> bool:
    row = conn.execute(
        "SELECT 1 FROM watchlist WHERE tmdb_id = ? AND media_type = ? AND profile_id = ?",
        (tmdb_id, media_type, profile_id),
    ).fetchone()
    return row is not None


# --- progress (profile-scoped) ----------------------------------------------------


def progress_put(
    conn: sqlite3.Connection,
    tmdb_id: int,
    media_type: str,
    position: float,
    duration: float,
    profile_id: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO progress (tmdb_id, media_type, position, duration, updated_at, profile_id)
        VALUES (?, ?, ?, ?, datetime('now'), ?)
        ON CONFLICT (tmdb_id, media_type, profile_id)
        DO UPDATE SET position = excluded.position,
                      duration = excluded.duration,
                      updated_at = datetime('now')
        """,
        (tmdb_id, media_type, position, duration, profile_id),
    )
    conn.commit()


def progress_get(
    conn: sqlite3.Connection, tmdb_id: int, media_type: str, profile_id: int = 0
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM progress WHERE tmdb_id = ? AND media_type = ? AND profile_id = ?",
        (tmdb_id, media_type, profile_id),
    ).fetchone()


def progress_all(conn: sqlite3.Connection, profile_id: int | None = None) -> list[sqlite3.Row]:
    base = "SELECT * FROM progress WHERE duration > 0 AND position > 1"
    if profile_id is None:
        return conn.execute(base + " ORDER BY updated_at DESC LIMIT 20").fetchall()
    return conn.execute(base + " AND profile_id = ? ORDER BY updated_at DESC LIMIT 20", (profile_id,)).fetchall()


# --- watch sessions (Wrapped telemetry) ---------------------------------------------


def watch_minutes_add(
    conn: sqlite3.Connection, profile_id: int, tmdb_id: int | None, media_type: str | None, seconds: int
) -> None:
    conn.execute(
        "INSERT INTO watch_sessions (profile_id, tmdb_id, media_type, seconds) VALUES (?, ?, ?, ?)",
        (profile_id, tmdb_id, media_type, max(0, int(seconds))),
    )
    conn.commit()


def wrapped_stats(conn: sqlite3.Connection, profile_id: int) -> dict:
    total = conn.execute(
        "SELECT COALESCE(SUM(seconds), 0) AS s, COUNT(*) AS n FROM watch_sessions WHERE profile_id = ?",
        (profile_id,),
    ).fetchone()
    top = conn.execute(
        """
        SELECT tmdb_id, media_type, SUM(seconds) AS minutes_sum
        FROM watch_sessions WHERE profile_id = ? AND tmdb_id IS NOT NULL
        GROUP BY tmdb_id, media_type ORDER BY minutes_sum DESC LIMIT 8
        """,
        (profile_id,),
    ).fetchall()
    by_day = conn.execute(
        """
        SELECT day, SUM(seconds) AS s FROM watch_sessions WHERE profile_id = ?
        GROUP BY day ORDER BY day DESC LIMIT 30
        """,
        (profile_id,),
    ).fetchall()
    return {
        "total_seconds": total["s"],
        "sessions": total["n"],
        "top": [{"tmdb_id": r["tmdb_id"], "media_type": r["media_type"], "minutes": r["minutes_sum"] // 60} for r in top],
        "by_day": [{"day": r["day"], "minutes": r["s"] // 60} for r in by_day],
    }


# --- playback error reports ----------------------------------------------------


def playback_error_add(
    conn: sqlite3.Connection,
    *,
    media_type: str | None,
    tmdb_id: int | None,
    src_kind: str,
    message: str,
) -> None:
    conn.execute(
        "INSERT INTO playback_errors (tmdb_id, media_type, src_kind, message) VALUES (?, ?, ?, ?)",
        (tmdb_id, media_type, src_kind, message),
    )
    conn.commit()


def playback_errors_all(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM playback_errors ORDER BY created_at DESC LIMIT ?", (limit,)
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


# --- subtitles (quote search) ----------------------------------------------------


def subtitle_index_replace(conn: sqlite3.Connection, library_id: int, lines: list[tuple[int, float, float, str]]) -> None:
    """Replace the subtitle index for one library item."""
    conn.execute("DELETE FROM subtitle_lines WHERE library_id = ?", (library_id,))
    conn.executemany(
        "INSERT INTO subtitle_lines (library_id, idx, start_s, end_s, text) VALUES (?, ?, ?, ?, ?)",
        [(library_id, idx, start, end, text) for idx, start, end, text in lines],
    )
    conn.commit()


def subtitle_search(conn: sqlite3.Connection, query: str, limit: int = 30) -> list[sqlite3.Row]:
    pattern = f"%{query.strip()}%"
    return conn.execute(
        """
        SELECT s.*, l.title AS lib_title, l.year AS lib_year, l.tmdb_id AS lib_tmdb_id,
               l.media_type AS lib_media_type, l.season AS lib_season, l.episode AS lib_episode
        FROM subtitle_lines s JOIN library l ON l.id = s.library_id
        WHERE s.text LIKE ? ORDER BY l.title, l.season, s.start_s LIMIT ?
        """,
        (pattern, limit),
    ).fetchall()


def subtitle_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM subtitle_lines").fetchone()["n"]


# --- intro markers ------------------------------------------------------------


def intro_marker_put(conn: sqlite3.Connection, tmdb_id: int, media_type: str, start_s: float, end_s: float) -> None:
    conn.execute(
        """
        INSERT INTO intro_markers (tmdb_id, media_type, start_s, end_s) VALUES (?, ?, ?, ?)
        ON CONFLICT (tmdb_id, media_type) DO UPDATE SET start_s = excluded.start_s, end_s = excluded.end_s
        """,
        (tmdb_id, media_type, start_s, end_s),
    )
    conn.commit()


def intro_marker_get(conn: sqlite3.Connection, tmdb_id: int, media_type: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM intro_markers WHERE tmdb_id = ? AND media_type = ?", (tmdb_id, media_type)
    ).fetchone()


# --- device pairing (TV screen role) ---------------------------------------------


def device_pair_create(conn: sqlite3.Connection, code: str, device_name: str, role: str = "screen") -> None:
    conn.execute(
        "INSERT INTO device_pairs (code, device_name, role) VALUES (?, ?, ?) "
        "ON CONFLICT (code) DO UPDATE SET device_name = excluded.device_name, last_seen = datetime('now')",
        (code, device_name[:40], role),
    )
    conn.commit()


def device_pair_get(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM device_pairs WHERE code = ?", (code,)).fetchone()


def device_pair_touch(conn: sqlite3.Connection, code: str) -> None:
    conn.execute("UPDATE device_pairs SET last_seen = datetime('now') WHERE code = ?", (code,))
    conn.commit()
