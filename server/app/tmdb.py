"""TMDB API client with a small in-memory TTL cache."""

from __future__ import annotations

import time
from typing import Any

import httpx

API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"

_CACHE_TTL_SECONDS = 300


class TmdbUnavailable(Exception):
    """Raised when TMDB is not configured or the API call fails."""


def image_url(path: str | None, size: str = "w500") -> str | None:
    if not path:
        return None
    return f"{IMAGE_BASE}/{size}{path}"


class TmdbClient:
    def __init__(self, api_key: str, language: str = "en-US", region: str = "ID") -> None:
        self.api_key = api_key
        self.language = language
        self.region = region
        self._http = httpx.AsyncClient(timeout=15)
        self._cache: dict[str, tuple[float, Any]] = {}

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.ready:
            raise TmdbUnavailable("TMDB_API_KEY is not set")

        cache_key = path + "|" + str(sorted((params or {}).items()))
        hit = self._cache.get(cache_key)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]

        query = {"api_key": self.api_key, "language": self.language, **(params or {})}
        try:
            response = await self._http.get(f"{API_BASE}{path}", params=query)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TmdbUnavailable(f"TMDB request failed: {exc}") from exc

        data = response.json()
        self._cache[cache_key] = (time.monotonic(), data)
        return data

    # --- catalog ---------------------------------------------------------------

    async def trending(self) -> list[dict[str, Any]]:
        return (await self._get("/trending/all/week")).get("results", [])

    async def popular_movies(self) -> list[dict[str, Any]]:
        return (await self._get("/movie/popular")).get("results", [])

    async def top_rated_movies(self) -> list[dict[str, Any]]:
        return (await self._get("/movie/top_rated")).get("results", [])

    async def popular_tv(self) -> list[dict[str, Any]]:
        return (await self._get("/tv/popular")).get("results", [])

    async def search(self, query: str) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        return (await self._get("/search/multi", {"query": query, "include_adult": "false"})).get("results", [])

    async def search_movie(self, query: str, year: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query": query, "include_adult": "false"}
        if year:
            params["year"] = year
        return (await self._get("/search/movie", params)).get("results", [])

    async def search_tv(self, query: str, year: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query": query, "include_adult": "false"}
        if year:
            params["first_air_date_year"] = year
        return (await self._get("/search/tv", params)).get("results", [])

    async def detail(self, tmdb_id: int, media_type: str) -> dict[str, Any]:
        return await self._get(
            f"/{media_type}/{tmdb_id}",
            {"append_to_response": "credits,similar,videos,watch/providers"},
        )

    # --- view shaping -----------------------------------------------------------

    @staticmethod
    def card(row: dict[str, Any]) -> dict[str, Any]:
        """Normalize a TMDB result into the shape templates expect."""
        media_type = row.get("media_type") or ("movie" if "title" in row else "tv")
        return {
            "tmdb_id": row["id"],
            "media_type": media_type,
            "title": row.get("title") or row.get("name") or "Tanpa judul",
            "year": (row.get("release_date") or row.get("first_air_date") or "")[:4],
            "poster": image_url(row.get("poster_path"), "w342"),
            "backdrop": image_url(row.get("backdrop_path"), "w780"),
            "rating": row.get("vote_average"),
        }


def extract_trailer(detail: dict[str, Any]) -> str | None:
    """Pick the best official YouTube trailer key from a detail payload."""
    videos = detail.get("videos") or {}
    results = videos.get("results") or []
    official = [v for v in results if v.get("site") == "YouTube" and v.get("official")]
    for pool in (official, [v for v in results if v.get("site") == "YouTube"]):
        for kind in ("Trailer", "Teaser"):
            for video in pool:
                if video.get("type") == kind:
                    return video["key"]
    return next((v["key"] for v in results if v.get("site") == "YouTube"), None)


def watch_providers(detail: dict[str, Any], region: str = "ID") -> list[dict[str, str]]:
    """Flatten TMDB watch/providers into a label + link list for one region."""
    providers = ((detail.get("watch/providers") or {}).get("results") or {}).get(region) or {}
    flat = providers.get("flatrate") or []
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for provider in flat:
        name = provider.get("provider_name", "")
        if name and name not in seen:
            seen.add(name)
            out.append({"name": name, "logo": image_url(provider.get("logo_path"), "w92")})
    return out
