# ProjectStream

**Personal movie & TV streaming app — TMDB catalog, your own media, YouTube trailers, public-domain films.**

[![CI](https://github.com/MahendraDharma/ProjectStream/actions/workflows/ci.yml/badge.svg)](https://github.com/MahendraDharma/ProjectStream/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11--3.13-3776ab.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Inspired by the experience of flystream.net — catalog browsing, trending rows, watchlists,
watch parties — but built to stream **only from sources you have the right to play**:

| Source | What it gives you |
|---|---|
| **TMDB** (free API key) | Trending, popular, top-rated, search, cast, posters, trailers, official watch providers |
| **Your own video files** | Drop files in a folder on your server → auto-matched to TMDB by filename → streamed with HTTP Range (resume support) |
| **YouTube official trailers** | Played through a yt-dlp proxy (the proven ProjectMusic pattern, optional cookies.txt) |
| **Internet Archive feature films** | A "Public Domain" section that is 100% legal and playable out of the box — no key needed |

> It deliberately does **not** aggregate unauthorized third-party streams. Playback comes from
> your files, official trailers, and public-domain works; everything else links out to legitimate
> providers (Netflix, Prime, and friends, per-region data from TMDB).

## Features

- Dark, fast, mobile-friendly UI (Jinja2 + vanilla JS, no build step)
- Trending / Popular / Top-rated / TV rows from TMDB
- Full-text multi-search (movies + TV)
- Detail pages: backdrop, cast, similar titles, official trailer, watch providers
- Local library: filename parsing (`Show.S01E02.mkv`, `Movie.2023.1080p.mkv`), TMDB matching, rescan button
- HTML5 player with HTTP Range streaming (seek = instant)
- Watchlist + Continue Watching (position saved every few seconds)
- Public Domain section powered by archive.org
- Caddy secret-link gate for private hosting (the ProjectMusic deployment pattern)

## Quick start (local dev)

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn app.main:create_app --factory --port 8080
```

Works immediately with the Public Domain section; add a TMDB key to `.env` (see
[`.env.example`](.env.example)) for the catalog features.

## Deploy on victus (Docker + Caddy gate)

```bash
# on the Mac
ssh victus
mkdir -p ~/servers/projectstream && cd ~/servers/projectstream
git clone https://github.com/MahendraDharma/ProjectStream .

# put your secrets in .env (see .env.example): TMDB_API_KEY, PS_KEY, PM_COOKIES (optional)
mkdir -p data cache media

# put some video files in media/ then:
docker compose build && docker compose up -d

# first login per device uses the secret link (1-year cookie), same as ProjectMusic:
#   http://victus:8081/masuk?k=<PS_KEY>
```

Expose publicly with Tailscale funnel if desired (same pattern as ProjectMusic):
`tailscale funnel --bg --https=443 http://127.0.0.1:8081`.

## Configuration

| Env var | Meaning | Default |
|---|---|---|
| `TMDB_API_KEY` | TMDB v3 API key (free account) | — (catalog disabled) |
| `TMDB_LANGUAGE` | Metadata language | `en-US` |
| `TMDB_REGION` | Watch-provider region | `ID` |
| `PM_LIBRARY_DIRS` | `:`-separated video folders | — |
| `PM_DB_PATH` | SQLite path | `data/projectstream.db` |
| `PM_CACHE_DIR` | Cache dir (reserved) | `server/.cache` |
| `PM_COOKIES` | cookies.txt for YouTube (optional) | — |
| `PS_KEY` | Caddy gate secret | — |

## API overview

| Endpoint | Purpose |
|---|---|
| `GET /api/watchlist` · `POST /api/watchlist` · `DELETE /api/watchlist/{type}/{id}` | Watchlist |
| `GET/POST /api/progress` | Continue-watching positions |
| `POST /api/library/rescan` | Scan folders, match TMDB, prune deleted |
| `GET /api/stream/local/{id}` | Range-stream a library file |
| `GET /api/stream/trailer/{videoId}` | yt-dlp proxy for official trailers |
| `GET /healthz` | Health check |

## Development

```bash
pip install -e '.[dev]'
ruff check .        # lint
pytest -q           # tests (40)
```

CI runs ruff + pytest on Python 3.11–3.13.

## Mulai Cepat (Bahasa Indonesia)

ProjectStream adalah aplikasi streaming film/TV pribadi: katalog & metadata dari TMDB,
pemutaran dari **file video milik Anda sendiri**, trailer YouTube resmi, dan film
public domain dari Internet Archive. Tidak mengagregasi sumber bajakan.

1. Ambil API key gratis di TMDB → isi `TMDB_API_KEY` di `.env`.
2. Letakkan video di folder `media/`, set `PM_LIBRARY_DIRS=/media/movies`.
3. `docker compose up -d` → buka `http://victus:8081/masuk?k=<PS_KEY>` sekali per perangkat.
4. Tombol **Pindai Ulang** di halaman Perpustakaan untuk memetakan file ke TMDB.

## Roadmap ideas

- Watch parties (synced playback between devices)
- Subtitle support (external `.srt` per file)
- People pages (actor filmography)
- year-in-review "Wrapped" page
- Transcoding (ffmpeg) for non-browser-friendly codecs (mkv)

## License

[MIT](LICENSE) — © Mahendra Dharma
