"""ProjectStream configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _split_dirs(value: str | None) -> list[Path]:
    if not value:
        return []
    return [Path(p) for p in value.split(":") if p.strip()]


@dataclass
class Settings:
    tmdb_api_key: str = field(default_factory=lambda: os.getenv("TMDB_API_KEY", ""))
    tmdb_language: str = field(default_factory=lambda: os.getenv("TMDB_LANGUAGE", "en-US"))
    tmdb_region: str = field(default_factory=lambda: os.getenv("TMDB_REGION", "ID"))

    library_dirs: list[Path] = field(default_factory=lambda: _split_dirs(os.getenv("PM_LIBRARY_DIRS")))
    db_path: Path = field(default_factory=lambda: Path(os.getenv("PM_DB_PATH", "data/projectstream.db")))
    cache_dir: Path = field(default_factory=lambda: Path(os.getenv("PM_CACHE_DIR", "server/.cache")))
    cookies_path: Path | None = field(default_factory=lambda: _optional(os.getenv("PM_COOKIES")))

    app_title: str = "ProjectStream"

    @property
    def tmdb_ready(self) -> bool:
        return bool(self.tmdb_api_key)


def _optional(value: str | None) -> Path | None:
    if not value or not value.strip():
        return None
    return Path(value)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the cached settings instance (re-reads when SPM reload is requested)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Drop the cached settings — used by the test suite."""
    global _settings
    _settings = None
