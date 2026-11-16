"""TMDB client tests against a mocked transport."""

import httpx
import pytest

from app.tmdb import TmdbClient, TmdbUnavailable, extract_trailer, image_url, watch_providers


@pytest.mark.anyio
async def test_request_includes_api_key_and_language():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": [{"id": 1, "title": "Foo", "media_type": "movie"}]})

    client = TmdbClient("k123", language="id-ID")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    results = await client.trending()
    assert seen["params"]["api_key"] == "k123"
    assert seen["params"]["language"] == "id-ID"
    assert results[0]["title"] == "Foo"


@pytest.mark.anyio
async def test_caching_avoids_second_request():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"results": [{"id": 2, "name": "Bar", "media_type": "tv"}]})

    client = TmdbClient("k123")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await client.popular_tv()
    await client.popular_tv()
    assert calls["n"] == 1


@pytest.mark.anyio
async def test_http_error_raises_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = TmdbClient("k123")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(TmdbUnavailable):
        await client.trending()


@pytest.mark.anyio
async def test_not_ready_raises_unavailable():
    client = TmdbClient("")
    with pytest.raises(TmdbUnavailable):
        await client.trending()


def test_card_shapes_movie_and_tv():
    movie = TmdbClient.card(
        {"id": 5, "title": "A", "release_date": "2020-01-01", "poster_path": "/p.jpg", "vote_average": 7.5}
    )
    tv = TmdbClient.card({"id": 6, "name": "B", "first_air_date": "2021-02-02", "backdrop_path": "/b.jpg"})

    assert movie["media_type"] == "movie"
    assert movie["year"] == "2020"
    assert movie["poster"].endswith("/w342/p.jpg")
    assert tv["media_type"] == "tv"
    assert tv["title"] == "B"
    assert tv["backdrop"].endswith("/w780/b.jpg")


def test_extract_trailer_prefers_official_trailer():
    detail = {
        "videos": {
            "results": [
                {"key": "teaser1", "site": "YouTube", "type": "Teaser", "official": False},
                {"key": "official1", "site": "YouTube", "type": "Trailer", "official": True},
            ]
        }
    }
    assert extract_trailer(detail) == "official1"


def test_extract_trailer_none_when_missing():
    assert extract_trailer({}) is None
    assert extract_trailer({"videos": {"results": [{"key": "k", "site": "Vimeo"}]}}) is None


def test_watch_providers_dedup_by_name():
    detail = {
        "watch/providers": {
            "results": {
                "ID": {
                    "flatrate": [
                        {"provider_name": "Netflix", "logo_path": "/n.png"},
                        {"provider_name": "Netflix", "logo_path": "/n.png"},
                        {"provider_name": "Disney Plus", "logo_path": "/d.png"},
                    ]
                }
            }
        }
    }
    providers = watch_providers(detail, "ID")
    assert [p["name"] for p in providers] == ["Netflix", "Disney Plus"]
    assert providers[0]["logo"].endswith("/w92/n.png")


def test_image_url_empty_path():
    assert image_url(None) is None
    assert image_url("") is None
