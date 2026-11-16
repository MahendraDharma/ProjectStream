// Profile selection + creation (flystream.net profiles parity, simplified).

document.querySelectorAll(".profile-card:not(.add)").forEach((card) => {
  card.addEventListener("click", async () => {
    const { id, name, pin } = card.dataset;

    if (pin === "1") {
      document.getElementById("pin-dialog").style.display = "";
      document.getElementById("pin-name").textContent = name;
      document.getElementById("pin-input").dataset.id = id;
      document.getElementById("pin-input").focus();
      return;
    }

    document.cookie = `ps_profile=${id}; Path=/; Max-Age=31536000; SameSite=Lax`;
    window.location.href = "/";
  });
});

const pinSubmit = document.getElementById("pin-submit");
if (pinSubmit) {
  pinSubmit.addEventListener("click", async () => {
    const input = document.getElementById("pin-input");
    const res = await fetch("/api/profiles/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: Number(input.dataset.id), pin: input.value }),
    });
    if (res.ok) {
      document.cookie = `ps_profile=${input.dataset.id}; Path=/; Max-Age=31536000; SameSite=Lax`;
      window.location.href = "/";
    } else {
      document.getElementById("pin-error").textContent = "PIN salah.";
    }
  });
}

const addBtn = document.getElementById("add-profile-btn");
if (addBtn) {
  addBtn.addEventListener("click", () => {
    document.getElementById("new-profile-dialog").style.display = "";
  });
}

const npCreate = document.getElementById("np-create");
if (npCreate) {
  npCreate.addEventListener("click", async () => {
    const name = document.getElementById("np-name").value.trim();
    if (!name) return;
    const res = await fetch("/api/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        avatar: document.getElementById("np-avatar").value,
        pin: document.getElementById("np-pin").value.trim() || null,
      }),
    });
    if (res.ok) window.location.reload();
  });
}
