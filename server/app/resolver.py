"""Playback source resolution — the legal equivalent of flystream.net's
/api/playback-search.

flystream.net resolves playback from third-party providers server-side and
answers the client with a lookup id plus error states (NO_STREAM_LOOKUP_ID,
SESSION_REBIND_REQUIRED, SITE_AUTH_REQUIRED, STREAM_UNAVAILABLE). ProjectStream
keeps the exact same *interface shape* but resolves only from sources the owner
has the right to play: the local library and nothing else. When nothing
matches, the client gets STREAM_UNAVAILABLE and the detail page offers the
legal alternatives instead (public domain, trailers, watch providers).
"""

from __future__ import annotations

import sqlite3
from typing import Any

STATUS_OK = "OK"
STATUS_NO_LOOKUP = "NO_STREAM_LOOKUP_ID"
STATUS_REBIND = "SESSION_REBIND_REQUIRED"
STATUS_AUTH = "SITE_AUTH_REQUIRED"
STATUS_UNAVAILABLE = "STREAM_UNAVAILABLE"


def resolve(conn: sqlite3.Connection, tmdb_id: int | None, media_type: str | None) -> dict[str, Any]:
    """Resolve playable sources for a TMDB id from the local library."""
    from .db import library_for_tmdb

    if not tmdb_id or media_type not in ("movie", "tv"):
        return {"status": STATUS_NO_LOOKUP, "sources": []}

    rows = library_for_tmdb(conn, int(tmdb_id), media_type)
    if not rows:
        return {"status": STATUS_UNAVAILABLE, "sources": []}

    sources = []
    for row in rows:
        label = row["title"]
        if row["season"] is not None:
            label = f"S{row['season']:02d}E{row['episode']:02d} — {row['title']}"
        sources.append(
            {
                "kind": "local",
                "library_id": row["id"],
                "label": label,
                "size_bytes": row["size_bytes"],
            }
        )

    return {"status": STATUS_OK, "sources": sources}
