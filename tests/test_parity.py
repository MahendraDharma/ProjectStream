"""Tests for the flystream.net functional-parity subsystems:
profiles, seals, playback-search, watch minutes/wrapped, quote search,
intro markers, TV pairing, and watch-party WebSockets.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import reset_settings
from app.db import connect, library_upsert, subtitle_index_replace
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


# --- profiles ---------------------------------------------------------------------


def test_profile_lifecycle(client):
    created = client.post("/api/profiles", json={"name": "Anak", "avatar": "🐸", "pin": "1234"}).json()
    assert created["ok"] is True
    profile = created["profile"]
    assert profile["has_pin"] is True

    listing = client.get("/api/profiles").json()
    assert listing == [{"id": profile["id"], "name": "Anak", "avatar": "🐸", "has_pin": True}]

    wrong = client.post("/api/profiles/select", json={"id": profile["id"], "pin": "9999"})
    assert wrong.status_code == 403

    ok = client.post("/api/profiles/select", json={"id": profile["id"], "pin": "1234"})
    assert ok.status_code == 200

    assert client.delete(f"/api/profiles/{profile['id']}").json()["ok"] is True
    assert client.get("/api/profiles").json() == []


# --- seals + playback-search ---------------------------------------------------------


def test_seal_issue_and_verify():
    from app.seals import issue, reset_secret, verify

    reset_secret()
    seal = issue("client-abc")["seal"]
    assert verify(seal, "client-abc") is True
    assert verify(seal, "other-client") is False
    assert verify("garbage") is False
    assert verify(None) is False


def test_seal_expiry_rejected():
    import time

    from app import seals

    seals.reset_secret()
    forged = f"client-abc|{int(time.time()) - 10}|deadbeef|x"
    assert seals.verify(forged) is False


def test_playback_search_requires_seal(client):
    response = client.post("/api/playback-search", json={"tmdb_id": 1, "media_type": "movie"})
    assert response.json()["status"] == "SESSION_REBIND_REQUIRED"


def test_playback_search_state_machine(client):
    from app.config import get_settings

    conn = connect(get_settings().db_path)
    fake = "/tmp/ps-nonexistent-movie.mp4"
    library_upsert(
        conn,
        path=fake,
        title="Known Movie",
        year=2024,
        media_type="movie",
        season=None,
        episode=None,
        size_bytes=10,
        tmdb_id=77,
    )

    seal = client.post("/api/guard-attest", json={"client_id": "c1"}).json()["seal"]
    headers = {"X-FS-Seal": seal}

    missing_id = client.post("/api/playback-search", json={}, headers=headers).json()
    assert missing_id["status"] == "NO_STREAM_LOOKUP_ID"

    unavailable = client.post("/api/playback-search", json={"tmdb_id": 999, "media_type": "movie"}, headers=headers).json()
    assert unavailable["status"] == "STREAM_UNAVAILABLE"

    ok = client.post("/api/playback-search", json={"tmdb_id": 77, "media_type": "movie"}, headers=headers).json()
    assert ok["status"] == "OK"
    assert ok["sources"][0]["library_id"] > 0


# --- status endpoint -------------------------------------------------------------------


def test_status_endpoint(client):
    stats = client.get("/api/status").json()
    assert stats["ok"] is True
    assert stats["version"] == "0.1.0"
    assert stats["uptime_seconds"] >= 0


# --- quote search ----------------------------------------------------------------------


def test_quote_search_roundtrip(client, tmp_path):
    from app.config import get_settings

    conn = connect(get_settings().db_path)
    row = library_upsert(
        conn,
        path=str(tmp_path / "sample.mp4"),
        title="Sample",
        year=None,
        media_type="movie",
        season=None,
        episode=None,
        size_bytes=1,
        tmdb_id=None,
    )
    subtitle_index_replace(conn, row["id"], [(1, 12.5, 14.0, "May the Force be with you")])

    results = client.get("/api/quote-search", params={"q": "force"}).json()
    assert results["query"] == "force"
    assert len(results["results"]) == 1
    assert results["results"][0]["start"] == 12.5
    assert "/watch/local/" in results["results"][0]["watch_url"]

    short = client.get("/api/quote-search", params={"q": "fo"}).json()
    assert short["results"] == []


# --- intro markers -----------------------------------------------------------------------


def test_intro_markers(client):
    put = client.post("/api/intro", json={"tmdb_id": 9, "media_type": "tv", "start": 5, "end": 65})
    assert put.json()["ok"] is True

    got = client.get("/api/intro", params={"tmdb": 9, "type": "tv"}).json()
    assert got == {"has_intro": True, "start": 5, "end": 65}

    empty = client.get("/api/intro", params={"tmdb": 10, "type": "tv"}).json()
    assert empty == {"has_intro": False}


# --- TV pairing ---------------------------------------------------------------------------


def test_tv_pair_claim_and_ws_relay(client):
    code = client.post("/api/tv-pair", json={"device_name": "Ruang Tamu"}).json()["code"]

    claimed = client.post("/api/tv/claim", json={"code": code})
    assert claimed.status_code == 200

    screen_url = f"/api/tv/ws?code={code}&role=screen"
    controller_url = f"/api/tv/ws?code={code}&role=controller"

    with client.websocket_connect(screen_url) as screen:
        assert screen.receive_json()["type"] == "paired"

        with client.websocket_connect(controller_url) as controller:
            assert controller.receive_json()["type"] == "paired"
            assert screen.receive_json()["type"] == "controller-joined"

            controller.send_json({"type": "play"})
            assert screen.receive_json() == {"type": "play"}

            controller.send_json({"type": "seek", "position": 42})
            assert screen.receive_json()["type"] == "seek"

            screen.send_json({"type": "state", "playing": True, "position": 42})
            assert controller.receive_json()["type"] == "state"


def test_tv_claim_unknown_code(client):
    assert client.post("/api/tv/claim", json={"code": "0000"}).status_code == 404


# --- watch party ----------------------------------------------------------------------------


def test_party_create_join_ws_sync(client):
    created = client.post("/api/party/create", json={"name": "Host"}).json()
    code = created["code"]

    assert client.post("/api/party/join", json={"code": code}).status_code == 200
    assert client.get("/api/party/peek", params={"code": code}).json()["members"] == 0

    ws_url = f"/api/party/ws?code={code}"
    with client.websocket_connect(ws_url) as host:
        host.send_json({"type": "hello", "name": "Host"})
        joined = host.receive_json()
        assert joined["role"] == "host"

        # host gets its own promotion event right after joining
        assert host.receive_json()["type"] == "host"

        with client.websocket_connect(ws_url) as guest:
            guest.send_json({"type": "hello", "name": "Tamu"})
            guest_joined = guest.receive_json()
            assert guest_joined["role"] == "guest"

            # guest join broadcasts the member list to everyone else (the host)
            members = host.receive_json()
            assert members["type"] == "members"
            assert len(members["members"]) == 2

            # host loads a source into the room; guests follow
            host.send_json(
                {"type": "state", "tmdb_id": 5, "library_id": 3, "media_type": "movie", "playing": True, "position": 0}
            )
            relayed = guest.receive_json()
            assert relayed["type"] == "state"
            assert relayed["library_id"] == 3

            # chat is relayed to the other side
            guest.send_json({"type": "chat", "text": "halo"})
            chat = host.receive_json()
            assert chat["type"] == "chat"
            assert chat["text"] == "halo"

            # guests cannot control playback
            guest.send_json({"type": "play", "position": 1})
            assert guest.receive_json()["type"] == "error"


def test_party_peek_unknown(client):
    assert client.get("/api/party/peek", params={"code": "ZZZZZZ"}).status_code == 404


# --- pages render ------------------------------------------------------------------------


def test_parity_pages_render(client):
    for path in ("/profiles", "/party", "/tv", "/remote", "/calendar", "/wrapped"):
        assert client.get(path).status_code == 200, path
