# Arsitektur ProjectStream — Penjelasan Sistem Keseluruhan

> Dokumen referensi untuk memahami **bagaimana sistem berjalan secara detail**, dari request
> browser sampai byte video keluar. Ditulis setelah build paritas flystream.net lolos uji
> E2E live (39 cek) + 61 test unit, CI hijau. Untuk peta fitur flystream.net → ProjectStream,
> lihat README; untuk sumber bedah, lihat `~/Documents/rea-flystream/LAPORAN-BEDAH-FLYSTREAM.md`.

---

## 1. Gambar besar

```
 Browser / TV / Ponsel (HTML + vanilla JS)
        │  http / websocket
        ▼
 [Caddy :8081]  gerbang secret-link (/masuk?k=<PS_KEY> → cookie ps_auth 1 tahun)  ← deployment
        │  reverse_proxy
        ▼
 [uvicorn :8080]  satu proses FastAPI
        ├── routes/pages.py   → HTML (Jinja2, SSR + JS enhancement)
        ├── routes/api.py     → JSON API + 2 WebSocket (/api/party/ws, /api/tv/ws)
        ├── tmdb.py           → klien TMDB (proksi + cache TTL 5 menit)
        ├── library.py        → scan folder + parser nama file + matching TMDB
        ├── streams.py        → HTTP Range streaming file lokal
        ├── youtube.py        → resolver trailer yt-dlp + proksi googlevideo
        ├── archive.py        → katalog film public domain Internet Archive
        ├── seals.py          → seal HMAC 10 menit (attestasi ala X-FS-Seal)
        ├── resolver.py       → playback-search (state machine, sumber = perpustakaan sendiri)
        ├── party.py / tv.py  → registry room party & sesi TV (in-memory)
        ├── srt.py            → parser .srt untuk quote-search
        └── db.py             → SQLite (WAL) — satu-satunya state persisten
        ▼
 SQLite (data/projectstream.db) + folder video (PM_LIBRARY_DIRS)
```

**Prinsip desain** (diambil dari bedah flystream.net, disesuaikan untuk satu server pribadi):
1. **Semua layanan pihak ketiga diproksi server** — TMDB, AniList-analoga, gambar, YouTube.
   Browser tidak pernah bicara langsung ke pihak ketiga; API key tidak pernah keluar.
2. **Server-rendered untuk SEO/kesederhanaan** + vanilla JS per-fitur — tanpa framework,
   tanpa build step.
3. **State dibagi dua**: persisten (SQLite) untuk data pengguna; in-memory untuk hal realtime
   (party, TV) karena satu proses = satu sumber kebenaran.

---

## 2. Siklus hidup sebuah request

1. Browser meminta `GET /` → Caddy memeriksa cookie `ps_auth` (tanpa itu, 403 kecuali
   `/masuk?k=KEY` → cookie ditukar). Dalam dev lokal, langkah ini dilewati.
2. FastAPI me-resolve route; dependency `get_conn()` membuka koneksi SQLite baru per request
   (`check_same_thread=False` karena sync deps berjalan di thread worker berbeda).
3. Handler membaca cookie `ps_profile` (profil aktif) bila relevan, memanggil TMDB/cache/DB,
   me-render template Jinja2 → HTML kembali.
4. JS halaman kemudian "menghidupkan" interaksi: live search, watchlist toggle, player,
   watch-minutes, party/TV WebSocket.

---

## 3. Subsistem, satu per satu

### 3.1 Katalog TMDB (tmdb.py)
- Satu klien `httpx.AsyncClient` + cache in-memory `(key → (timestamp, data))` TTL 300 detik.
- Endpoint yang dipakai: `/trending/all/week`, `/movie/popular|top_rated|upcoming`, `/tv/popular|on_the_air`,
  `/search/multi|movie|tv`, `/{type}/{id}?append_to_response=credits,similar,videos,watch/providers`.
- Tanpa `TMDB_API_KEY`, klien raise `TmdbUnavailable` → halaman menampilkan pemberitahuan dan
  bagian Public Domain/Perpustakaan tetap hidup.
- `card()` menormalkan hasil movie/tv ke satu bentuk untuk template; `image_url()` membuat URL
  poster — lalu **difilter Jinja `tmdb_img`** menulis ulang ke `/api/tmdb-image?url=...` (allowlist
  regex host/size/ekstensi) sehingga byte gambar keluar dari server kita, bukan dari image.tmdb.org.

### 3.2 Perpustakaan lokal (library.py)
- `scan_directories()`: rglob folder `PM_LIBRARY_DIRS` (dipisah `:`), filter ekstensi video.
- `parse_filename()`: regex SxxExx (judul TV = teks **sebelum** marker), tahun `(19|20)dd`,
  buang token release (1080p, BluRay, x264, …), dots → spasi.
- `match_to_tmdb()`: cari TMDB movie/tv by title(+year), ambil hasil pertama → `tmdb_id`.
- Rescan dijalankan: otomatis di startup (lifespan task) dan manual via tombol. Upsert per path
  (UNIQUE), prune baris yang filenya hilang. Sambil jalan, `.srt` sebelah video diindeks (3.6).

### 3.3 Streaming (streams.py)
- `GET /api/stream/local/{id}` dengan header `Range: bytes=start-end|-suffix`.
- `parse_range()` mengembalikan ByteRange inklusif; salah/unsatisfiable → 200 full (browser
  tetap bisa seek). Valid → **206** + `Content-Range` + `Content-Length` persis potongan itu.
- Pembacaan file: seek + chunk 256 KB sampai `end`. Inilah yang membuat seek instan di player.
- Trailer YouTube: `/api/stream/trailer/{video_id}` → yt-dlp resolve (cache 1 jam, opsional
  cookies.txt) → URL googlevideo di-proksi dengan UA/Referer YouTube (pola ProjectMusic:
  cookies + plain yt-dlp, tanpa plugin POT).

### 3.4 Profil (db.profiles + cookie ps_profile)
- Tabel `profiles` (nama, avatar emoji, pin_hash sha256 opsional).
- `POST /api/profiles/select` memvalidasi PIN lalu JS menaruh cookie `ps_profile=<id>` (1 tahun).
- Semua query watchlist/progress menerima `profile_id` (0 = legacy). Tabel `progress` punya
  UNIQUE `(tmdb_id, media_type, profile_id)` — DB lama di-rebuild otomatis oleh `_migrate_progress`.
- PIN disimpan sebagai hash (bukan plaintext); enkripsi penuh ala vault flystream = roadmap.

### 3.5 Seal / attestasi (seals.py) — ala `X-FS-Seal` flystream
- `POST /api/guard-attest {client_id}` → seal `client_id|expires|nonce|HMAC-SHA256`.
- Secret: `PS_SECRET` env atau random per boot. TTL 10 menit.
- `/api/playback-search` menolak tanpa/seal-expired → `SESSION_REBIND_REQUIRED` (client minta
  seal baru). Ini menaikkan biaya abuse API oleh non-browser, persis motivasi flystream.

### 3.6 Quote-search (srt.py)
- Saat rescan, `.srt` sebelah video (glob `<stem>*.srt`) di-parse: blok `index → timecode → teks`,
  strip tag HTML, simpan `(library_id, idx, start, end, text)` ke `subtitle_lines`.
- `GET /api/quote-search?q=` → LIKE `%q%` join library → hasil dengan `watch_url` yang
  mengandung `?t=<detik>` (player langsung seek ke dialog).

### 3.7 Watch party (party.py + WS)
- Registry in-memory: `code(6 hex) → Room{members, state, chat, lock}`.
- REST: `create` (buat kode) · `join`/`peek` (validasi) · `leave`.
- WS `/api/party/ws?code=`:
  1. Client wajib mengirim `{"type":"hello","name"}` **sebelum** didaftarkan ke room
     (bug E2E: join-dulu-bikin-broadcast-datang-sebelum-joined — sudah diperbaiki).
  2. Server balas `joined {you, role, members, state, chat}`; role = `host` bila room kosong
     dari host, selain itu `guest`.
  3. Host boleh kirim `state|play|pause|seek` → server update `room.state` lalu relay ke
     semua kecuali pengirim. Guest yang coba → `error`.
  4. `chat|emoji` → disimpan ke riwayat (max 100) + relay ke semua kecuali pengirim.
  5. Disconnect → leave; bila host pergi → **member pertama dipromosikan jadi host** dan event
     `host` di-broadcast (alur "claim" flystream).
- Client (party-room.js): guest menerapkan state/posisi dengan proteksi `applyingRemote`
  (abaikan echo timeupdate sendiri), host mengirim `sync` periodik 10 dtk.
- Kenapa in-memory: satu proses uvicorn = satu sumber kebenaran; multi-worker butuh Redis.

### 3.8 TV pairing + remote (tv.py + WS)
- TV buka `/tv` → `POST /api/tv-pair` → kode 4-digit ditampilkan besar → WS role `screen`.
- Ponsel buka `/remote` → `POST /api/tv/claim {code}` (slot controller harus kosong) → WS role
  `controller`.
- Relay satu-arah ber-command whitelist: controller → screen: `play|pause|seek|load|ping`;
  screen → controller: `state|pong`. Screen disconnect = sesi hangus; controller disconnect =
  slot bebas lagi.
- Session TV stale setelah 1 jam (auto-drop saat diakses).

### 3.9 Wrapped (telemetri)
- Player mengirim `POST /api/watch-minutes {seconds(delta)}` tiap 30 dtk → tabel
  `watch_sessions (profile_id, tmdb_id, media_type, seconds, day)`.
- `/wrapped` mengagregasi: total jam, 8 judul teratas (diperkaya poster/judul TMDB), bar 30 hari.
- Analogi: inilah pipa data yang membuat fitur "Wrapped" flystream (dengan tanda tangan) bisa
  dibangun nanti — datanya sudah terkumpul.

### 3.10 Pelengkap
- `announcement.json` di sebelah DB → banner beranda + `/api/announcement` (auto-expire `until`).
- `/api/status` → uptime, ukuran DB, tmdb_ready; `/healthz` untuk healthcheck Docker.
- Intro markers: `/api/intro` (get/post) → tombol "Lewati Intro" di player saat currentTime
  berada di window.
- Kalender `/calendar`: TMDB upcoming + on_the_air (padanan legal bagian "Leaks"/kalender).

---

## 4. Data model (SQLite)

| Tabel | Isi | Catatan |
|---|---|---|
| `library` | file video: path UNIQUE, title/year/season/episode, tmdb_id, size | sumber kebenaran "apa yang bisa diputar" |
| `profiles` | nama, avatar, pin_hash | identitas ringan |
| `watchlist` | (tmdb_id, media_type, profile_id) | per profil |
| `progress` | posisi/durasi per (tmdb_id, media_type, **profile_id**) | UNIQUE 3 kolom; migrasi otomatis |
| `watch_sessions` | delta detik per sesi per hari | bahan Wrapped |
| `subtitle_lines` | baris .srt per library_id | bahan quote-search |
| `intro_markers` | window start/end per judul | skip intro |
| `device_pairs` | kode TV, nama, last_seen | audit pairing |
| `playback_errors` | laporan kegagalan playback dari client | ops/learning |
| party & TV | **in-memory** (party.py/tv.py) | sengaja; Redis bila multi-worker |

---

## 5. Keamanan (berlapis, ala flystream.net yang disederhanakan)

| Lapis | flystream.net | ProjectStream |
|---|---|---|
| Edge anti-bot | Anubis PoW + DDoS-Guard | Caddy secret-link gate (pola ProjectMusic) — cukup untuk server pribadi |
| Auth form | Cloudflare Turnstile | — (tidak ada login publik; profil + PIN lokal) |
| Attestasi API | `X-FS-Seal` + `/api/guard-attest` | seal HMAC identik konsep, gating `playback-search` |
| Kunci data | vault terenkripsi + passkey per profil | PIN hash per profil (vault/passkey = roadmap) |
| Proxy pihak ketiga | /api/tmdb-image dll. | /api/tmdb-image (allowlist) ✅ |

---

## 6. Uji sistem

| Lapis | Isi | Hasil |
|---|---|---|
| Unit/feature (pytest, 61 test) | parser nama file, Range, TMDB mock, API CRUD, proxy, seal, resolver, quote, intro, TV WS relay, party WS relay (TestClient) | ✅ |
| E2E live (`scripts/e2e_live.py`, 39 cek) | server asli + WebSockets asli: profil+PIN, rescan+`srt`, quote-search deep link, seal→state machine lengkap, scoping watchlist 2 profil, party 2 client (state/chat/anti-guest-control), TV pair→claim→load→pause→state, watch-minutes, wrapped, intro, announcement, 8 halaman, image-proxy reject | ✅ ALL PASSED (dua kali beruntun) |
| CI (GitHub Actions, Python 3.11–3.13) | ruff + pytest | ✅ |

Bug yang ditangkap E2E live dan sudah diperbaiki: **registrasi member party sebelum hello**
membuat broadcast `members`/`host` tiba sebelum `joined` sendiri (KeyError 'role' di client)
— kini join terjadi tepat setelah hello tervalidasi.

---

## 7. Batasan yang diketahui (jujur)

- Party/TV state hilang jika proses restart (in-memory). Flystream.net mungkin mengalami hal
  yang sama per-edge; solusi umum: Redis pub/sub.
- `mkv` mungkin tak bisa diputar di beberapa browser (codec) — butuh transcoding (roadmap).
- Seal HMAC dengan secret random-per-boot berarti semua seal hangus saat restart — perilaku
  yang diharapkan (client otomatis re-attest).
- Sumber konten: hanya perpustakaan Anda + trailer + public domain; resolusi provider pihak
  ketiga ala flystream.net **sengaja tidak ada**.
