"""Public-domain films from the Internet Archive (100% legal to stream).

The archive.org feature-films collection is queried live; if the network
call fails, a small curated fallback keeps the section usable.
"""

from __future__ import annotations

from typing import Any

import httpx

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/{identifier}"
DOWNLOAD_URL = "https://archive.org/download/{identifier}/{filename}"

VIDEO_EXTENSIONS = (".mp4", ".m4v", ".webm", ".ogv")

_FALLBACK = [
    {"identifier": "his_girl_friday", "title": "His Girl Friday", "year": "1940"},
    {"identifier": "charlie_chaplin_film_fest", "title": "Charlie Chaplin Festival", "year": "1938"},
    {"identifier": "TheFastandtheFuriousJohnIreland1954goofyrip", "title": "The Fast and the Furious", "year": "1955"},
    {"identifier": "VoyagetothePlanetofPrehistoricWomen", "title": "Voyage to the Planet of Prehistoric Women", "year": "1968"},
    {"identifier": "sex_madness", "title": "Sex Madness", "year": "1938"},
]


async def feature_films(limit: int = 24) -> list[dict[str, Any]]:
    """Return public-domain feature films, most downloaded first."""
    params = {
        # format filter keeps out items that only carry torrents/metadata
        "q": "collection:(feature_films) AND mediatype:(movies) AND format:(h.264)",
        "fl[]": ["identifier", "title", "year"],
        "rows": str(limit),
        "sort[]": "downloads desc",
        "output": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(SEARCH_URL, params=params)
            response.raise_for_status()
            docs = response.json().get("response", {}).get("docs", [])
    except (httpx.HTTPError, ValueError):
        return list(_FALLBACK)

    out: list[dict[str, Any]] = []
    for doc in docs:
        out.append(
            {
                "identifier": doc.get("identifier", ""),
                "title": doc.get("title") or doc.get("identifier", ""),
                "year": str(doc.get("year", "") or ""),
            }
        )
    return out or list(_FALLBACK)


async def playable_url(identifier: str) -> str | None:
    """Find the first streamable video file for an archive.org item."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(METADATA_URL.format(identifier=identifier))
            response.raise_for_status()
            files = response.json().get("files", [])
    except (httpx.HTTPError, ValueError):
        return None

    best: str | None = None
    for entry in files:
        name = entry.get("name", "")
        lower = name.lower()
        if lower.endswith(VIDEO_EXTENSIONS):
            # prefer format= h.264 / smaller derivative names
            best = name
            if entry.get("format") == "h.264":
                break

    if best is None:
        return None

    return DOWNLOAD_URL.format(identifier=identifier, filename=best)
