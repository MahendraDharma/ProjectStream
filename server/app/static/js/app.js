/* ProjectStream front-end helpers: watchlist toggle, rescan, progress saving. */

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
      result.textContent = `${data.scanned} file, ${data.matched} cocok ke TMDB, ${data.pruned} dibersihkan`;
      if (data.scanned > 0) setTimeout(() => window.location.reload(), 1200);
    } catch {
      result.textContent = "gagal memindai";
    }
    rescanBtn.disabled = false;
  });
}

// --- progress reporting ----------------------------------------------------------

const player = document.getElementById("player");
if (player && player.dataset && player.dataset.tmdbId !== undefined) {
  // handled via data attributes below (set in template when tmdb_id present)
}

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
}
