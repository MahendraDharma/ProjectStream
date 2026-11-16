"""Process-wide singletons: the TMDB client and the Jinja2 template env."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from .config import Settings, get_settings
from .tmdb import TmdbClient

_templates: Jinja2Templates | None = None
_tmdb: TmdbClient | None = None
_settings: Settings | None = None


def templates_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"


def get_templates() -> Jinja2Templates:
    global _templates
    if _templates is None:
        _templates = Jinja2Templates(directory=str(templates_dir()))

        def tmdb_img(url: str | None) -> str | None:
            """Route TMDB artwork through the server-side proxy (flystream.net lesson)."""
            from urllib.parse import quote

            if url and url.startswith("https://image.tmdb.org/t/p/"):
                return "/api/tmdb-image?url=" + quote(url, safe="")
            return url

        _templates.env.filters["tmdb_img"] = tmdb_img
    return _templates


def get_settings_cached() -> Settings:
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings


def get_tmdb() -> TmdbClient:
    global _tmdb
    settings = get_settings_cached()
    if _tmdb is None or _tmdb.api_key != settings.tmdb_api_key:
        _tmdb = TmdbClient(settings.tmdb_api_key, settings.tmdb_language, settings.tmdb_region)
    return _tmdb


templates = get_templates()


def reset_state() -> None:
    """Drop cached singletons — used by the test suite."""
    global _templates, _tmdb, _settings
    _templates = None
    _tmdb = None
    _settings = None
