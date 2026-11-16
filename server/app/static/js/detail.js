// Detail page interactions: ends-at clock, watched marker.
// (Server picker is plain links; watchlist handled by app.js.)

(function () {
  const endsAt = document.getElementById("ends-at");
  if (endsAt) {
    const runtimeMatch = document.querySelector(".detail-meta")?.textContent.match(/(\d+)m/);
    const minutes = runtimeMatch ? parseInt(runtimeMatch[1], 10) : 0;
    if (minutes > 0) {
      const end = new Date(Date.now() + minutes * 60000);
      endsAt.textContent = "berakhir " + end.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
    }
  }

  const watchedBtn = document.querySelector(".watched-btn");
  if (watchedBtn) {
    watchedBtn.addEventListener("click", async () => {
      const { mediaType, tmdbId } = watchedBtn.dataset;
      const res = await fetch(`/api/watched/${mediaType}/${tmdbId}`, { method: "POST" });
      if (res.ok) {
        watchedBtn.style.background = "rgba(127, 174, 107, 0.35)";
        watchedBtn.title = "Sudah ditonton";
      }
    });
  }
})();
