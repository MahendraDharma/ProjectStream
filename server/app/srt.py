"""Minimal SRT subtitle parser for the quote-search index.

Parses the classic SRT block format:

    12
    00:01:23,456 --> 00:01:26,000
    Hello there.
    General Kenobi!

Only the first two text lines per cue are kept (enough for search).
"""

from __future__ import annotations

import re
from pathlib import Path

CUE_HEADER = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")

TagStrip = re.compile(r"<[^>]+>")


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_srt(path: Path) -> list[tuple[int, float, float, str]]:
    """Return (index, start_seconds, end_seconds, text) tuples."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    lines_out: list[tuple[int, float, float, str]] = []
    blocks = re.split(r"\n\s*\n", raw.replace("\r\n", "\n"))
    idx = 0

    for block in blocks:
        lines = [ln for ln in block.strip().split("\n") if ln.strip()]
        if not lines:
            continue

        # first line may be the numeric cue id
        body_start = 1 if lines[0].strip().isdigit() else 0
        if body_start >= len(lines):
            continue

        match = CUE_HEADER.search(lines[body_start])
        if not match:
            continue

        g = match.groups()
        start = _seconds(g[0], g[1], g[2], g[3])
        end = _seconds(g[4], g[5], g[6], g[7])

        text_lines = [TagStrip.sub("", ln).strip() for ln in lines[body_start + 1 : body_start + 3]]
        text = " ".join(ln for ln in text_lines if ln).strip()

        if text:
            idx += 1
            lines_out.append((idx, round(start, 2), round(end, 2), text[:300]))

    return lines_out


def sibling_subtitle_files(video_path: Path) -> list[Path]:
    """Find .srt files that belong to a video file (same stem, same directory)."""
    directory = video_path.parent
    if not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.glob(f"{Path(video_path).stem}*.srt")
        if p.is_file()
    )
