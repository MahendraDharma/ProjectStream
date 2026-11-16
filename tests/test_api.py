"""API endpoint tests with a temporary database."""

import pytest
from fastapi.testclient import TestClient

from app.config import reset_settings
from app.db import connect, library_upsert
from app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PM_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PM_LIBRARY_DIRS", "")
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    reset_settings()

    import app.state as state

    state.reset_state()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    reset_settings()
    state.reset_state()


def test_healthz(client):
    assert client.get("/healthz").status_code == 200


def test_watchlist_roundtrip(client):
    assert client.post("/api/watchlist", json={"tmdb_id": 42, "media_type": "movie"}).json() == {
        "ok": True,
        "in_watchlist": True,
    }

    data = client.get("/api/watchlist").json()
    assert len(data) == 1
    assert data[0]["tmdb_id"] == 42
    assert data[0]["media_type"] == "movie"

    assert client.delete("/api/watchlist/movie/42").json()["in_watchlist"] is False
    assert client.get("/api/watchlist").json() == []


def test_watchlist_invalid_media_type_rejected(client):
    response = client.post("/api/watchlist", json={"tmdb_id": 1, "media_type": "album"})
    assert response.status_code == 422


def test_watchlist_add_is_idempotent(client):
    client.post("/api/watchlist", json={"tmdb_id": 7, "media_type": "tv"})
    client.post("/api/watchlist", json={"tmdb_id": 7, "media_type": "tv"})
    assert len(client.get("/api/watchlist").json()) == 1


def test_progress_roundtrip(client):
    client.post(
        "/api/progress",
        json={"tmdb_id": 99, "media_type": "movie", "position": 600.5, "duration": 7200},
    )
    client.post(
        "/api/progress",
        json={"tmdb_id": 99, "media_type": "movie", "position": 1200.0, "duration": 7200},
    )

    data = client.get("/api/progress/movie/99").json()
    assert data["position"] == 1200.0
    assert data["duration"] == 7200


def test_progress_missing_returns_zero(client):
    assert client.get("/api/progress/movie/404").json() == {"position": 0, "duration": 0}


def test_stream_local_serves_file(client, tmp_path):
    media = tmp_path / "file.mp4"
    media.write_bytes(b"0123456789abcdef")

    from app.config import get_settings

    conn = connect(get_settings().db_path)
    row = library_upsert(
        conn,
        path=str(media),
        title="file",
        year=None,
        media_type="movie",
        season=None,
        episode=None,
        size_bytes=media.stat().st_size,
        tmdb_id=None,
    )

    response = client.get(f"/api/stream/local/{row['id']}", headers={"Range": "bytes=0-4"})
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-4/16"
    assert response.content == b"01234"


def test_stream_local_missing_file_gone(client, tmp_path):
    from app.config import get_settings

    conn = connect(get_settings().db_path)
    row = library_upsert(
        conn,
        path=str(tmp_path / "deleted.mp4"),
        title="deleted",
        year=None,
        media_type="movie",
        season=None,
        episode=None,
        size_bytes=10,
        tmdb_id=None,
    )

    assert client.get(f"/api/stream/local/{row['id']}").status_code == 410


def test_stream_local_unknown_id_404(client):
    assert client.get("/api/stream/local/9999").status_code == 404


def test_home_page_renders_without_tmdb(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "TMDB API key" in response.text


def test_library_rescan_empty_dirs(client):
    response = client.post("/api/library/rescan")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "scanned": 0, "matched": 0, "pruned": 0}


def test_tmdb_image_rejects_foreign_url(client):
    response = client.get("/api/tmdb-image", params={"url": "https://evil.example.com/p/w342/x.jpg"})
    assert response.status_code == 400


def test_tmdb_image_rejects_missing_url(client):
    # FastAPI's native query validation rejects a missing required param with 422.
    response = client.get("/api/tmdb-image")
    assert response.status_code == 422


def test_tmdb_image_rejects_wrong_size(client):
    response = client.get("/api/tmdb-image", params={"url": "https://image.tmdb.org/t/p/w666/x.jpg"})
    assert response.status_code == 400


def test_tmdb_image_proxies_tmdb_url(client, monkeypatch):
    import httpx

    import app.routes.api as api_module

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "image.tmdb.org"
        return httpx.Response(200, content=b"IMGDATA", headers={"content-type": "image/jpeg"})

    monkeypatch.setattr(api_module, "_image_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    response = client.get("/api/tmdb-image", params={"url": "https://image.tmdb.org/t/p/w342/abc.jpg"})
    assert response.status_code == 200
    assert response.content == b"IMGDATA"
    assert response.headers["content-type"].startswith("image/")
    assert response.headers["cache-control"].startswith("public")


def test_playback_error_report_roundtrip(client):
    response = client.post(
        "/api/playback-error-report",
        json={"tmdb_id": 5, "media_type": "movie", "src_kind": "local", "message": "file gone"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    from app.config import get_settings
    from app.db import connect, playback_errors_all

    conn = connect(get_settings().db_path)
    rows = playback_errors_all(conn)
    assert len(rows) == 1
    assert rows[0]["tmdb_id"] == 5
    assert rows[0]["src_kind"] == "local"
    assert rows[0]["message"] == "file gone"


def test_playback_error_report_allows_partial_payload(client):
    response = client.post("/api/playback-error-report", json={"src_kind": "trailer", "message": "502"})
    assert response.status_code == 200


def test_playback_error_report_rejects_bad_media_type(client):
    response = client.post("/api/playback-error-report", json={"media_type": "album", "message": "x"})
    assert response.status_code == 422


def test_pwa_manifest_served(client):
    response = client.get("/static/site.webmanifest")
    assert response.status_code == 200
    assert "ProjectStream" in response.text
