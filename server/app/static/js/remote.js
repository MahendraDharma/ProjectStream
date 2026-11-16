// Remote controller: claim a paired TV code, then send play/pause/seek/load.
// Mirrors flystream.net's remote/claim + WS relay pattern.

const claimBtn = document.getElementById("remote-claim");
const statusLine = document.getElementById("remote-status");
const pad = document.getElementById("remote-pad");
const stateLine = document.getElementById("remote-state");

let ws = null;
let claimedCode = null;

function connectAsController(code) {
  const proto = window.location.protocol === "https:" ? "wss://" : "ws://";
  ws = new WebSocket(`${proto}${window.location.host}/api/tv/ws?code=${encodeURIComponent(code)}&role=controller`);

  ws.onopen = () => {
    document.getElementById("remote-connect").style.display = "none";
    pad.style.display = "";
    document.getElementById("remote-device").textContent = `TV (${code})`;
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "paired") {
      statusLine.textContent = "Terhubung ✔";
    } else if (msg.type === "state") {
      stateLine.textContent = `TV: ${msg.playing ? "memutar" : "jeda"} @ ${Math.floor(msg.position / 60)}m ${Math.floor(msg.position % 60)}s`;
    }
  };

  ws.onclose = () => {
    statusLine.textContent = "Koneksi TV terputus.";
    pad.style.display = "none";
    document.getElementById("remote-connect").style.display = "";
  };
}

function send(payload) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
}

claimBtn.addEventListener("click", async () => {
  const code = document.getElementById("remote-code").value.trim();
  const res = await fetch("/api/tv/claim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!res.ok) {
    statusLine.textContent = "Kode tidak ditemukan / sudah diklaim.";
    return;
  }
  claimedCode = code;
  connectAsController(code);
  loadFilePicker();
});

async function loadFilePicker() {
  const picker = document.getElementById("remote-file-picker");
  picker.style.display = "";
  const res = await fetch("/api/library");
  const items = await res.json();
  const holder = document.getElementById("remote-files");
  holder.innerHTML = "";
  if (!items.length) {
    holder.innerHTML = "<em>Perpustakaan kosong.</em>";
    return;
  }
  items.slice(0, 30).forEach((item) => {
    const btn = document.createElement("button");
    btn.className = "btn small";
    btn.style.margin = "4px";
    btn.textContent = item.title + (item.season ? ` S${String(item.season).padStart(2, "0")}E${String(item.episode).padStart(2, "0")}` : "");
    btn.addEventListener("click", () => {
      send({ type: "load", library_id: item.id, title: item.title });
    });
    holder.appendChild(btn);
  });
}

document.querySelectorAll(".remote-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (!claimedCode) return;
    const cmd = btn.dataset.cmd;
    if (cmd === "play") send({ type: "play" });
    else if (cmd === "pause") send({ type: "pause" });
    else if (cmd === "seek-back") send({ type: "seek", position: (tvPosition() || 0) - 30 });
    else if (cmd === "seek-fwd") send({ type: "seek", position: (tvPosition() || 0) + 30 });
  });
});

// The TV reports state every 5s; remember it for relative seeking.
let lastKnownPosition = 0;
const _origStateHandler = null;
function tvPosition() {
  return lastKnownPosition || null;
}
