/* ProjectStream front-end helpers:
   - watchlist toggle, library rescan, progress saving
   - live search dropdown (flystream.net parity)
   - seal attestation + playback-search availability chip
   - watch-minutes telemetry (Wrapped) and skip-intro markers */

// --- live search dropdown -------------------------------------------------------

const liveInput = document.getElementById("live-search");
const drop = document.getElementById("search-drop");
let searchTimer = null;

if (liveInput && drop) {
  liveInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = liveInput.value.trim();
    if (q.length < 2) {
      drop.innerHTML = "";
      drop.style.display = "none";
      return;
    }
    searchTimer = setTimeout(async () => {
      const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`).catch(() => null);
      if (!res || !res.ok) return;
      const data = await res.json();
      drop.innerHTML = "";
      (data.results || []).forEach((r) => {
        const a = document.createElement("a");
        a.href = `/detail/${r.media_type}/${r.tmdb_id}`;
        a.className = "search-hit";
        a.textContent = `${r.title}${r.year ? " (" + r.year + ")" : ""}`;
        drop.appendChild(a);
      });
      drop.style.display = data.results && data.results.length ? "" : "none";
    }, 250);
  });

  document.addEventListener("click", (e) => {
    if (!drop.contains(e.target) && e.target !== liveInput) drop.style.display = "none";
  });
}

// --- seal attestation + playback availability (detail page) -----------------------

async function getClientId() {
  let id = localStorage.getItem("fs_client_id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("fs_client_id", id);
  }
  return id;
}

async function fetchSeal() {
  const clientId = await getClientId();
  const res = await fetch("/api/guard-attest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: clientId }),
  });
  return res.json();
}

// On detail pages: ask the server which legal sources exist (mirrors the
// flystream.net playback-search flow including its error states).
if (window.location.pathname.startsWith("/detail/")) {
  const match = window.location.pathname.match(/\/detail\/(movie|tv)\/(\d+)/);
  if (match) {
    fetchSeal().then(({ seal }) => {
      fetch("/api/playback-search", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-FS-Seal": seal },
        body: JSON.stringify({ tmdb_id: Number(match[2]), media_type: match[1] }),
      })
        .then((r) => r.json())
        .then((data) => {
          const anchor = document.querySelector(".hero-info");
          if (!anchor) return;
          const chip = document.createElement("div");
          chip.className = "source-chip";
          if (data.status === "OK") {
            chip.classList.add("ok");
            chip.textContent = `✔ Tersedia di perpustakaan (${data.sources.length} sumber)`;
          } else if (data.status === "SESSION_REBIND_REQUIRED") {
            chip.textContent = "⟳ Sesi seal kedaluwarsa — muat ulang halaman";
          } else {
            chip.textContent = "Belum ada di perpustakaan — coba trailer / sumber legal di bawah";
          }
          anchor.prepend(chip);
        })
        .catch(() => {});
    });
  }
}

// --- watchlist toggle ---------------------------------------------------------

document.querySelectorAll(".watchlist-toggle").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const { mediaType, tmdbId } = btn.dataset;
    const active = btn.dataset.active === "1";

    if (active) {
      await fetch(`/api/watchlist/${mediaType}/${tmdbId}`, { method: "DELETE" });
      btn.textContent = "+ Watchlist";
      btn.dataset.active = "";
    } else {
      await fetch("/api/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tmdb_id: Number(tmdbId), media_type: mediaType }),
      });
      btn.textContent = "✓ Di Watchlist";
      btn.dataset.active = "1";
    }
  });
});

// --- library rescan ------------------------------------------------------------

const rescanBtn = document.getElementById("rescan-btn");
if (rescanBtn) {
  rescanBtn.addEventListener("click", async () => {
    rescanBtn.disabled = true;
    const result = document.getElementById("rescan-result");
    result.textContent = "Memindai...";
    try {
      const res = await fetch(rescanBtn.dataset.endpoint, { method: "POST" });
      const data = await res.json();
      result.textContent = `${data.scanned} file, ${data.matched} cocok ke TMDB, ${data.pruned} dibersihkan, ${data.subtitle_lines || 0} baris subtitle terindeks`;
      if (data.scanned > 0) setTimeout(() => window.location.reload(), 1200);
    } catch {
      result.textContent = "gagal memindai";
    }
    rescanBtn.disabled = false;
  });
}

// --- player page: progress, watch-minutes, skip-intro -----------------------------

const player = document.getElementById("player");

if (player && player.dataset.tmdbId) {
  const tmdbId = player.dataset.tmdbId;
  const mediaType = player.dataset.mediaType;

  const save = () => {
    if (!player.duration) return;
    fetch("/api/progress", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      keepalive: true,
      body: JSON.stringify({
        tmdb_id: Number(tmdbId),
        media_type: mediaType,
        position: player.currentTime,
        duration: player.duration,
      }),
    });
  };
  player.addEventListener("timeupdate", () => {
    if (player.currentTime % 5 < 0.25) save();
  });
  player.addEventListener("pause", save);
  window.addEventListener("pagehide", save);

  // watch-minutes: a delta ping every 30s feeds the Wrapped page
  let minutesAnchor = player.currentTime;
  const minutesTimer = setInterval(() => {
    const delta = Math.round(player.currentTime - minutesAnchor);
    minutesAnchor = player.currentTime;
    if (delta > 3 && delta < 120 && !player.paused) {
      fetch("/api/watch-minutes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        keepalive: true,
        body: JSON.stringify({ tmdb_id: Number(tmdbId), media_type: mediaType, seconds: delta }),
      }).catch(() => {});
    }
  }, 30000);
  window.addEventListener("pagehide", () => clearInterval(minutesTimer));

  // skip-intro: per-title markers (flystream.net /api/intro?tmdb= flow)
  fetch(`/api/intro?tmdb=${tmdbId}&type=${mediaType}`)
    .then((r) => r.json())
    .then((marker) => {
      const skip = document.getElementById("skip-intro");
      if (!marker.has_intro || !skip) return;
      skip.style.display = "";

      player.addEventListener("timeupdate", () => {
        const inWindow =
          player.currentTime >= marker.start && player.currentTime <= marker.end;
        skip.style.display = inWindow ? "" : "none";
      });
      skip.addEventListener("click", () => {
        player.currentTime = marker.end + 0.2;
        skip.style.display = "none";
      });
    })
    .catch(() => {});
}
