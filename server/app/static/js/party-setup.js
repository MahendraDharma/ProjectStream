// Watch party setup page: create a room or join by code.

const createBtn = document.getElementById("party-create");
if (createBtn) {
  createBtn.addEventListener("click", async () => {
    const name = document.getElementById("party-name").value.trim() || "Host";
    const res = await fetch("/api/party/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (res.ok) {
      const data = await res.json();
      sessionStorage.setItem("ps_party_name", name);
      window.location.href = `/party/${data.code}`;
    }
  });
}

const joinBtn = document.getElementById("party-join");
if (joinBtn) {
  joinBtn.addEventListener("click", async () => {
    const code = document.getElementById("party-code").value.trim().toUpperCase();
    if (code.length !== 6) return;

    const peek = await fetch(`/api/party/peek?code=${code}`);
    if (!peek.ok) {
      alert("Party tidak ditemukan.");
      return;
    }

    let name = sessionStorage.getItem("ps_party_name") || "";
    if (!name) {
      name = prompt("Nama kamu:") || "Tamu";
      sessionStorage.setItem("ps_party_name", name);
    }
    window.location.href = `/party/${code}`;
  });
}
