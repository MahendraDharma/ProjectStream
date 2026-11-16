"""Trailer playback via yt-dlp, following the proven ProjectMusic setup.

Direct googlevideo URLs are resolved with yt-dlp (optionally with a
cookies.txt file) and proxied through this server so the browser sees a
plain video stream. Resolutions are cached with a TTL because the URLs
expire after a few hours.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

log = logging.getLogger("projectstream.youtube")

_RESOLVE_TTL_SECONDS = 60 * 60
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


class TrailerUnavailable(Exception):
    pass


class TrailerResolver:
    def __init__(self, cookies_path: str | None = None) -> None:
        self.cookies_path = cookies_path
        self._cache: dict[str, tuple[float, str]] = {}
        self._lock = asyncio.Lock()

    async def resolve(self, video_id: str) -> str:
        """Return a playable googlevideo URL for a YouTube video id."""
        async with self._lock:
            hit = self._cache.get(video_id)
            if hit and time.monotonic() - hit[0] < _RESOLVE_TTL_SECONDS:
                return hit[1]
            url = await asyncio.to_thread(self._resolve_sync, video_id)
            self._cache[video_id] = (time.monotonic(), url)
            return url

    def _resolve_sync(self, video_id: str) -> str:
        try:
            import yt_dlp
        except ImportError as exc:  # pragma: no cover - Docker image always has it
            raise TrailerUnavailable("yt-dlp is not installed") from exc

        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            # mp4 works directly in <video>; otherwise muxed/best fallback.
            "format": "best[ext=mp4][protocol^=http]/best[protocol^=http]/best",
            "noplaylist": True,
            "socket_timeout": 20,
        }
        if self.cookies_path:
            options["cookiefile"] = self.cookies_path

        url = f"https://www.youtube.com/watch?v={video_id}"
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise TrailerUnavailable("yt-dlp returned no info")

        direct = info.get("url")
        if not direct:
            formats = info.get("formats") or []
            playable = [
                f
                for f in formats
                if f.get("url") and f.get("protocol", "").startswith("http") and f.get("acodec") != "none"
            ]
            if not playable:
                raise TrailerUnavailable("no direct format found")
            direct = playable[-1]["url"]

        return direct


def proxy_headers() -> dict[str, str]:
    """Headers googlevideo expects when the URL is played from another host."""
    return {
        "User-Agent": _USER_AGENT,
        "Referer": "https://www.youtube.com/",
    }


async def stream_trailer(resolver: TrailerResolver, video_id: str):
    """Async generator proxying the trailer bytes to the browser."""
    url = await resolver.resolve(video_id)
    headers = proxy_headers()

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", url, headers=headers) as upstream:
            if upstream.status_code >= 400:
                raise TrailerUnavailable(f"upstream returned {upstream.status_code}")
            async for chunk in upstream.aiter_bytes(256 * 1024):
                yield chunk
