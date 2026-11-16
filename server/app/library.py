"""Local media scanner: filename parsing and TMDB matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mkv", ".avi", ".mov", ".webm"}

_SEASON_EPISODE = re.compile(r"(?i)\bS(\d{1,2})\s?E(\d{1,3})\b")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

# Release-craft tokens dropped from the cleaned title.
_NOISE = re.compile(
    r"(?i)\b(480p|720p|1080p|2160p|4k|hdr|dv|remux|web[-_. ]?dl|webrip|bluray|blu[-_. ]?ray|"
    r"brrip|dvdrip|hdtv|cam|hdts|x264|x265|h\.?264|h\.?265|hevc|aac|ac3|eac3|dts|ddp?|"
    r"truehd|atmos|10bit|8bit|proper|repack|extended|unrated|remastered|amzn|nf|netflix|"
    r"dsnp|atvp|hulu|itunes|multiproper| INTERNAL|prosumer)\b"
)


@dataclass
class ParsedName:
    title: str
    year: int | None
    season: int | None
    episode: int | None
    media_type: str  # 'movie' | 'tv'


def parse_filename(filename: str) -> ParsedName:
    """Extract title, year, and season/episode info from a media file name.

    Understands the common release-name shapes:

        "Oppenheimer.2023.1080p.BluRay.x264.mkv"        -> movie, 2023
        "The.Last.of.Us.S02E03.1080p.WEB.mkv"           -> tv S02E03
        "Breaking Bad - S05E14 - Ozymandias.mp4"        -> tv S05E14
        "Interstellar (2014)/movie.mkv"                 -> movie, 2014 (name of parent used)
    """
    stem = Path(filename).stem

    season = episode = None
    match = _SEASON_EPISODE.search(stem)
    if match:
        season = int(match.group(1))
        episode = int(match.group(2))
        stem = stem[: match.start()].strip(" ._--")

    year = None
    match = _YEAR.search(stem)
    if match:
        year = int(match.group(1))
        stem = stem[: match.start()].strip(" ._--()")

    stem = _NOISE.sub(" ", stem)
    title = re.sub(r"[._]+", " ", stem)
    title = re.sub(r"\s+", " ", title).strip(" -_")

    if not title:
        title = Path(filename).stem

    return ParsedName(
        title=title,
        year=year,
        season=season,
        episode=episode,
        media_type="tv" if season is not None else "movie",
    )


def scan_directories(dirs: list[Path]) -> list[Path]:
    """Return every video file under the given directories (non-recursive scan per dir)."""
    files: list[Path] = []
    for directory in dirs:
        if not directory.is_dir():
            continue
        for child in sorted(directory.rglob("*")):
            if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS:
                files.append(child)
    return files


async def match_to_tmdb(parsed: ParsedName, tmdb) -> int | None:
    """Find the best TMDB id for a parsed filename; None when nothing matches."""
    from .tmdb import TmdbUnavailable

    try:
        if parsed.media_type == "tv":
            results = await tmdb.search_tv(parsed.title, parsed.year)
        else:
            results = await tmdb.search_movie(parsed.title, parsed.year)
    except TmdbUnavailable:
        return None

    if not results:
        return None

    return int(results[0]["id"])
