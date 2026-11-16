"""HTTP Range streaming for local media files."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from fastapi.responses import StreamingResponse

_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")

CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
}

CHUNK_SIZE = 256 * 1024


@dataclass
class ByteRange:
    start: int
    end: int  # inclusive

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_range(header: str | None, size: int) -> ByteRange | None:
    """Parse a Range header into an inclusive ByteRange, or None.

    Supports the single-range forms browsers send: "bytes=start-end",
    "bytes=start-" and "bytes=-suffix". Multi-range and unsatisfiable
    ranges return None (caller then serves 200 with the whole file, which
    browsers handle fine).
    """
    if not header or size <= 0:
        return None

    match = _RANGE.match(header.strip())
    if not match:
        return None

    raw_start, raw_end = match.group(1), match.group(2)

    if raw_start == "" and raw_end == "":
        return None

    if raw_start == "":
        # suffix form: last N bytes
        suffix = int(raw_end)
        if suffix <= 0:
            return None
        length = min(suffix, size)
        return ByteRange(size - length, size - 1)

    start = int(raw_start)
    if start >= size:
        return None

    end = int(raw_end) if raw_end else size - 1
    end = min(end, size - 1)

    if end < start:
        return None

    return ByteRange(start, end)


def content_type_for(path: str) -> str:
    return CONTENT_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def file_response(path: str, range_header: str | None) -> StreamingResponse:
    """Build a 200/206 streaming response for a media file."""
    size = os.path.getsize(path)
    content_type = content_type_for(path)
    selected = parse_range(range_header, size)

    if selected is None:
        return StreamingResponse(
            _iter_file(path, 0, size - 1),
            media_type=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(size),
            },
        )

    return StreamingResponse(
        _iter_file(path, selected.start, selected.end),
        status_code=206,
        media_type=content_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {selected.start}-{selected.end}/{size}",
            "Content-Length": str(selected.length),
        },
    )


def _iter_file(path: str, start: int, end: int):
    """Yield the file between start and end (inclusive) in chunks."""
    remaining = end - start + 1
    with open(path, "rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
