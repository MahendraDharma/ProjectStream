"""HTTP Range parsing and file streaming tests."""

import anyio
import pytest

from app.streams import file_response, parse_range


def test_no_header_returns_none():
    assert parse_range(None, 100) is None
    assert parse_range("", 100) is None


def test_full_open_range():
    r = parse_range("bytes=0-", 100)
    assert (r.start, r.end) == (0, 99)


def test_partial_range():
    r = parse_range("bytes=10-19", 100)
    assert (r.start, r.end, r.length) == (10, 19, 10)


def test_suffix_range():
    r = parse_range("bytes=-25", 100)
    assert (r.start, r.end) == (75, 99)


def test_suffix_larger_than_file():
    r = parse_range("bytes=-500", 100)
    assert (r.start, r.end) == (0, 99)


def test_end_clamped_to_size():
    r = parse_range("bytes=90-500", 100)
    assert (r.start, r.end) == (90, 99)


def test_start_beyond_size_is_none():
    assert parse_range("bytes=100-", 100) is None


def test_invalid_headers_return_none():
    assert parse_range("bytes=abc-def", 100) is None
    assert parse_range("bytes=-", 100) is None
    assert parse_range("items=0-5", 100) is None
    assert parse_range("bytes=50-20", 100) is None


@pytest.fixture
def media_file(tmp_path):
    path = tmp_path / "sample.mp4"
    path.write_bytes(bytes(range(256)) * 16)  # 4096 bytes with known content
    return str(path)


def test_response_200_without_range(media_file):
    response = file_response(media_file, None)
    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"


def test_response_206_with_range(media_file):
    response = file_response(media_file, "bytes=0-99")
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-99/4096"
    assert response.headers["content-length"] == "100"


def test_streamed_bytes_match_requested_range(media_file):
    response = file_response(media_file, "bytes=3-12")

    async def collect():
        return b"".join([chunk async for chunk in response.body_iterator])

    data = anyio.run(collect)
    assert data == (bytes(range(256)) * 16)[3:13]
