// Watch party room: WebSocket sync — host controls playback, everyone follows.
// Mirrors flystream.net's /api/party/ws flow: hello -> joined(role/state/chat),
// host sends play/pause/seek/state, server relays; host departure promotes a successor.

const code = window.location.pathname.split("/").pop();
const player = document.getElementById("party-player");
const roleTag = document.getElementById("party-role");
const nowLine = document.getElementById("party-now");
const chatLog = document.getElementById("chat-log");
const chatInput = document.getElementById("chat-text");
const hostControls = document.querySelector(".party-host-controls");

let myId = null;
let myRole = "guest";
let ws = null;
let applyingRemote = false;
let driftTimer = null;

const PROXY = "/api/tmdb-image?url=";

function addChat(entry) {
  const div = document.createElement("div");
  div.className = "chat-line";
  div.textContent = `${entry.name}: ${entry.text}`;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function setRole(role) {
  myRole = role;
  roleTag.textContent = role === "host" ? "Host" : "Tamu";
  if (hostControls) hostControls.style.display = role === "host" ? "" : "none";
}

function applyState(state) {
  // Load the shared source when the host picks one (local library only).
  if (state.tmdb_id && state.library_id && player.dataset.src !== String(state.library_id)) {
    player.dataset.src = String(state.library_id);
    player.src = `/api/stream/local/${state.library_id}`;
    nowLine.textContent = `Memutar dari perpustakaan (TMDB #${state.tmdb_id}).`;
  }
  applyingRemote = true;
  player.currentTime = state.position || 0;
  if (state.playing) {
    player.play().catch(() => {});
  } else {
    player.pause();
  }
  setTimeout(() => (applyingRemote = false), 300);
}

function send(payload) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
}

function connect() {
  const proto = window.location.protocol === "https:" ? "wss://" : "ws://";
  ws = new WebSocket(`${proto}${window.location.host}/api/party/ws?code=${encodeURIComponent(code)}`);

  ws.onopen = () => {
    const name = sessionStorage.getItem("ps_party_name") || "Tamu";
    send({ type: "hello", name });
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    switch (msg.type) {
      case "joined":
        myId = msg.you;
        setRole(msg.role);
        (msg.members || []).forEach((m) => {
          if (m.role === "host") roleTag.textContent = `Host: ${m.name}`;
        });
        (msg.chat || []).forEach(addChat);
        if (msg.state && (msg.state.playing || msg.state.position)) applyState(msg.state);
        startDriftCheck();
        break;
      case "members": {
        const host = (msg.members || []).find((m) => m.role === "host");
        if (host) roleTag.textContent = `Host: ${host.name}`;
        break;
      }
      case "host":
        if (msg.member_id === myId) setRole("host");
        break;
      case "state":
      case "play":
      case "pause":
      case "seek":
        if (msg.tmdb_id !== undefined && msg.type === "state") {
          applyState(msg);
        } else if (msg.type === "play") {
          applyingRemote = true;
          player.currentTime = msg.position || player.currentTime;
          player.play().catch(() => {});
          setTimeout(() => (applyingRemote = false), 300);
        } else if (msg.type === "pause") {
          applyingRemote = true;
          player.pause();
          setTimeout(() => (applyingRemote = false), 300);
        } else if (msg.type === "seek") {
          applyingRemote = true;
          player.currentTime = msg.position || 0;
          setTimeout(() => (applyingRemote = false), 300);
        }
        break;
      case "chat":
      case "emoji":
        addChat({ name: msg.name || "?", text: msg.text || msg.emoji });
        break;
    }
  };

  ws.onclose = () => setTimeout(connect, 2000);
}

// Host-side drift control: everyone re-syncs against the host's position.
function startDriftCheck() {
  if (driftTimer) clearInterval(driftTimer);
  driftTimer = setInterval(() => {
    if (myRole === "host" && player.duration) {
      send({ type: "sync", position: player.currentTime });
    }
  }, 10000);
}

// Host controls: pick a library item to load into the party.
async function loadLibraryPicker() {
  if (!hostControls) return;
  const picker = document.getElementById("party-pick");
  const res = await fetch("/api/library").catch(() => null);
  if (!res || !res.ok) return;
  const items = await res.json();
  picker.innerHTML = '<option value="">Pilih dari perpustakaan…</option>';
  items.forEach((item) => {
    const label = item.title + (item.season ? ` S${String(item.season).padStart(2, "0")}E${String(item.episode).padStart(2, "0")}` : "");
    const opt = document.createElement("option");
    opt.value = item.id;
    opt.textContent = label;
    picker.appendChild(opt);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  connect();
  loadLibraryPicker();

  const loadBtn = document.getElementById("party-load");
  if (loadBtn) {
    loadBtn.addEventListener("click", () => {
      const id = Number(document.getElementById("party-pick").value);
      if (!id) return;
      send({
        type: "state",
        tmdb_id: id,
        library_id: id,
        media_type: "movie",
        playing: true,
        position: 0,
      });
      player.src = `/api/stream/local/${id}`;
      player.dataset.src = String(id);
      player.play().catch(() => {});
    });
  }

  const chatSend = document.getElementById("chat-send");
  if (chatSend) {
    chatSend.addEventListener("click", () => {
      const text = chatInput.value.trim();
      if (!text) return;
      send({ type: "chat", text });
      chatInput.value = "";
    });
    chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") chatSend.click();
    });
  }

  document.querySelectorAll(".emoji-btn").forEach((btn) => {
    btn.addEventListener("click", () => send({ type: "emoji", text: btn.dataset.emoji }));
  });

  window.addEventListener("pagehide", () => {
    if (ws) {
      try {
        ws.close();
      } catch {}
    }
  });
});
