/* ProjectStream front-end helpers (flystream.net-style UI):
   search overlay + live results, hero carousel, time chips, watchlist,
   library rescan, progress + watch-minutes, skip-intro. */

// --- search overlay ------------------------------------------------------------

const overlay = document.getElementById("search-overlay");
const searchOpenBtn = document.getElementById("search-open");
const liveInput = document.getElementById("live-search");
const drop = document.getElementById("search-drop");
let searchTimer = null;

function openSearch() {
  if (!overlay) return;
  overlay.classList.add("open");
  liveInput?.focus();
}
function closeSearch() {
  overlay?.classList.remove("open");
}
searchOpenBtn?.addEventListener("click", openSearch);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeSearch();
});
overlay?.addEventListener("click", (e) => {
  if (e.target === overlay) closeSearch();
});
liveInput?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && liveInput.value.trim()) {
    window.location.href = `/search?q=${encodeURIComponent(liveInput.value.trim())}`;
  }
});

if (liveInput && drop) {
  liveInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = liveInput.value.trim();
    if (q.length < 2) {
      drop.innerHTML = "";
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
        a.addEventListener("click", closeSearch);
        drop.appendChild(a);
      });
    }, 250);
  });
}

// --- hero carousel ---------------------------------------------------------------

const heroSection = document.getElementById("hero");
if (heroSection) {
  const featuredData = JSON.parse(document.getElementById("featured-data")?.textContent || "[]");
  const dots = [...heroSection.querySelectorAll(".hero-dot")];
  const slides = [...heroSection.querySelectorAll(".hero-slide")];
  let current = 0;
  let timer = null;

  function show(index) {
    current = index % slides.length;
    slides.forEach((s, i) => (s.style.display = i === current ? "" : "none"));
    dots.forEach((d, i) => d.classList.toggle("active", i === current));

    const item = featuredData[current];
    if (item) {
      document.getElementById("hero-title").textContent = item.title;
      document.getElementById("hero-rating").textContent = (item.rating ?? 0).toFixed(1);
      document.getElementById("hero-year").textContent = item.year || "";
      document.getElementById("hero-overview").textContent = item.overview || "";
      document.getElementById("hero-play").href = `/detail/${item.media_type}/${item.tmdb_id}`;
      document.getElementById("hero-info").href = `/detail/${item.media_type}/${item.tmdb_id}`;
      const add = document.getElementById("hero-add");
      add.dataset.mediaType = item.media_type;
      add.dataset.tmdbId = item.tmdb_id;
    }
  }

  function startAuto() {
    timer = setInterval(() => show(current + 1), 7000);
  }
  dots.forEach((d) =>
    d.addEventListener("click", () => {
      clearInterval(timer);
      show(Number(d.dataset.index));
      startAuto();
    })
  );
  if (slides.length) startAuto();

  // hero "+" adds the visible item to the watchlist
  document.getElementById("hero-add")?.addEventListener("click", async (e) => {
    e.preventDefault();
    const add = document.getElementById("hero-add");
    await fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tmdb_id: Number(add.dataset.tmdbId), media_type: add.dataset.mediaType }),
    });
    add.textContent = "✓";
  });
}

// --- time chips ("Got some time?") ------------------------------------------------

document.querySelectorAll(".time-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const mins = Number(chip.dataset.mins);
    document.querySelectorAll(".time-chip").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");

    // filter library cards by the estimated duration heuristic (data-mins)
    let anyVisible = false;
    document.querySelectorAll(".card[data-mins]").forEach((card) => {
      const show = Number(card.dataset.mins) <= mins;
      card.style.display = show ? "" : "none";
      anyVisible = anyVisible || show;
    });
    if (!anyVisible) {
      const target = document.getElementById("archive-rail");
      target?.scrollIntoView({ behavior: "smooth" });
    }
  });
});

// --- watchlist toggle ---------------------------------------------------------

document.querySelectorAll(".watchlist-toggle").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const { mediaType, tmdbId } = btn.dataset;
    const active = btn.dataset.active === "1";

    if (active) {
      await fetch(`/api/watchlist/${mediaType}/${tmdbId}`, { method: "DELETE" });
      btn.textContent = "＋";
      btn.dataset.active = "";
    } else {
      await fetch("/api/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tmdb_id: Number(tmdbId), media_type: mediaType }),
      });
      btn.textContent = "✓";
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

  fetch(`/api/intro?tmdb=${tmdbId}&type=${mediaType}`)
    .then((r) => r.json())
    .then((marker) => {
      const skip = document.getElementById("skip-intro");
      if (!marker.has_intro || !skip) return;
      skip.style.display = "";

      player.addEventListener("timeupdate", () => {
        const inWindow = player.currentTime >= marker.start && player.currentTime <= marker.end;
        skip.style.display = inWindow ? "" : "none";
      });
      skip.addEventListener("click", () => {
        player.currentTime = marker.end + 0.2;
        skip.style.display = "none";
      });
    })
    .catch(() => {});
}
