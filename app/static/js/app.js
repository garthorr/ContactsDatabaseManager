"use strict";

// ── Toast helper ──────────────────────────────────────────────────────────
function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  if (!container) return;
  const id = `toast-${Date.now()}`;
  const colorMap = { success: "bg-success", danger: "bg-danger", info: "bg-primary", warning: "bg-warning" };
  const bg = colorMap[type] || "bg-secondary";
  const html = `
    <div id="${id}" class="toast align-items-center text-white ${bg} border-0" role="alert">
      <div class="d-flex">
        <div class="toast-body">${message}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
      </div>
    </div>`;
  container.insertAdjacentHTML("beforeend", html);
  const el = document.getElementById(id);
  const toast = new bootstrap.Toast(el, { delay: 4000 });
  toast.show();
  el.addEventListener("hidden.bs.toast", () => el.remove());
}

// ── Loading overlay ───────────────────────────────────────────────────────
function showLoading(msg = "Processing…") {
  let ov = document.getElementById("loading-overlay");
  if (!ov) {
    ov = document.createElement("div");
    ov.id = "loading-overlay";
    ov.innerHTML = `
      <div class="text-center">
        <div class="spinner-border text-primary mb-3" style="width:3rem;height:3rem;"></div>
        <div id="loading-msg" class="fw-semibold text-muted">${msg}</div>
      </div>`;
    document.body.appendChild(ov);
  } else {
    document.getElementById("loading-msg").textContent = msg;
    ov.style.display = "flex";
  }
}
function hideLoading() {
  const ov = document.getElementById("loading-overlay");
  if (ov) ov.style.display = "none";
}

// ── Debounce ──────────────────────────────────────────────────────────────
function debounce(fn, delay = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

// ── Generic JSON fetch ────────────────────────────────────────────────────
async function postJSON(url, data) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return { ok: resp.ok, status: resp.status, data: await resp.json() };
}
