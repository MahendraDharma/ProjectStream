// TV screen: pair, show the code big, then wait for controller commands.
// Flow mirrors flystream.net's /api/tv-pair + WebSocket screen role.

const tvPlayer = document.getElementById("tv-player");
const tvCode = document.getElementById("tv-code");
const tvStatus = document.getElementById("tv-status");
const tvStepCode = document.getElementById("tv-step-code");
const tvStepPlayer = document.getElementById("tv-step-player");
const tvTitle = document.getElementById("tv-title");

let ws = null;

function connectAsScreen(code) {
  const proto = window.location.protocol === "https:" ? "wss://" : "ws://";
  ws = new WebSocket(`${proto}${window.location.host}/api/tv/ws?code=${encodeURIComponent(code)}&role=screen`);

  ws.onopen = () => {
    tvStatus.textContent = `Kode aktif: ${code} — tunggu remote terhubung…`;
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    switch (msg.type) {
      case "paired":
        tvStatus.textContent = "Menunggu remote…";
        break;
      case "controller-joined":
        tvStatus.textContent = "Remote terhubung ✔";
        break;
      case "load":
        showPlayer();
        if (msg.library_id) {
          tvTitle.textContent = msg.title || `Library #${msg.library_id}`;
          tvPlayer.src = `/api/stream/local/${msg.library_id}`;
          tvPlayer.play().catch(() => {});
        } else if (msg.src) {
          tvTitle.textContent = msg.title || "Trailer";
          tvPlayer.src = msg.src;
          tvPlayer.play().catch(() => {});
        }
        break;
      case "play":
        tvPlayer.play().catch(() => {});
        break;
      case "pause":
        tvPlayer.pause();
        break;
      case "seek":
        tvPlayer.currentTime = msg.position || tvPlayer.currentTime;
        break;
    }
  };

  ws.onclose = () => {
    tvStatus.textContent = "Sesi berakhir — memuat ulang kode…";
    setTimeout(() => window.location.reload(), 2000);
  };
}

function showPlayer() {
  tvStepCode.style.display = "none";
  tvStepPlayer.style.display = "";
}

document.addEventListener("DOMContentLoaded", async () => {
  const res = await fetch("/api/tv-pair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_name: "TV " + new Date().toLocaleDateString("id-ID") }),
  });
  const data = await res.json();
  tvCode.textContent = data.code.split("").join(" ");
  connectAsScreen(data.code);

  // keep-alive: tell the controller our playback state occasionally
  setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN && tvPlayer.duration) {
      ws.send(
        JSON.stringify({
          type: "state",
          playing: !tvPlayer.paused,
          position: tvPlayer.currentTime,
          duration: tvPlayer.duration,
        })
      );
    }
  }, 5000);
});
