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

## Functional parity with flystream.net (learning build)

ProjectStream now implements the flystream.net feature map (dissected with REA — see
`~/Documents/rea-flystream/LAPORAN-BEDAH-FLYSTREAM.md`) with legal sources only:

| flystream.net | ProjectStream | Status |
|---|---|---|
| Watch party: `/api/party/*` REST + `/api/party/ws` WS (play/pause/seek/sync/chat/gif), host promotion | `/api/party/create|join|leave|peek` + `/api/party/ws` (chat + emoji reactions instead of GIF service) | ✅ |
| TV pairing + remote: `/api/tv-pair`, `remote/claim`, WS relay | `/api/tv-pair`, `/api/tv/claim`, `/api/tv/ws` (screen/controller roles), `/tv` + `/remote` pages | ✅ |
| Seal attestation: `/api/guard-attest` + `X-FS-Seal` on sensitive calls | HMAC seal (10 min TTL) gating `/api/playback-search`; error states `NO_STREAM_LOOKUP_ID` / `SESSION_REBIND_REQUIRED` / `STREAM_UNAVAILABLE` | ✅ |
| Source resolution server-side (`/api/playback-search`) | Same shape; resolves **your library only** — piracy sources intentionally not replicated | ✅ (legal) |
| Profiles: avatars, lock, per-profile data | `/profiles` + `/api/profiles` (emoji avatars, optional PIN, per-profile watchlist/progress via `ps_profile` cookie) | ✅ |
| Wrapped (watch-minutes telemetry) | `/api/watch-minutes` + `/wrapped` page (top titles, 30-day bars) | ✅ |
| Quote search | `/api/quote-search` indexing your own `.srt` files found next to library videos | ✅ |
| Calendar / "Leaks" | `/calendar` = TMDB upcoming + on-the-air (legal equivalent) | ✅ |
| Intro skip (`/api/intro?tmdb=`) | `/api/intro` markers + "Lewati Intro" button in the player | ✅ |
| Announcements, `/api/health` | `announcement.json` banner + `/api/status` (uptime, db size) + client latency | ✅ |
| Live search | navbar dropdown via `/api/search` | ✅ |
| PWA + TV mode + i18n | manifest/icon ✅; 10-foot TV layout on `/tv` ✅; i18n client-side = roadmap | ⚠️ sebagian |
| Downloads ber-passkey, vault terenkripsi, edge buckets, ads, Discord widget | tidak diimplementasikan — butuh WebAuthn/Redis/multi-region atau tidak relevan (non-komersial) | ❌ roadmap |

Party & TV state live in-process (single uvicorn worker) — a Redis store would be the next step
for multi-worker deployments.

## Lessons applied from dissecting flystream.net (REA, 2026-11-17)

A full architecture dissection of flystream.net (see `~/Documents/rea-flystream/LAPORAN-BEDAH-FLYSTREAM.md`)
informed these changes, applied to ProjectStream where they fit a legal, single-server app:

- **`/api/tmdb-image` proxy** — TMDB artwork is streamed through the server behind a strict
  allowlist (flystream.net proxies `/api/tmdb-image` + `/api/anime-image` the same way), so the
  browser never talks to `image.tmdb.org` directly.
- **PWA manifest + theme-color + SVG icon** — installable like flystream.net.
- **`POST /api/playback-error-report`** — client playback failures land in SQLite for ops
  (mirrors their playback-error pipeline).
- **OpenGraph meta on detail pages** — proper share cards like their SSR SEO templates.

## License

[MIT](LICENSE) — © Mahendra Dharma
