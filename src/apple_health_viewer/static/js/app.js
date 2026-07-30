"use strict";

const sidebar = document.querySelector("#sidebar");
const menuButton = document.querySelector("[data-menu-toggle]");

function setMenu(open) {
  if (!sidebar || !menuButton) return;
  sidebar.classList.toggle("is-open", open);
  menuButton.setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("menu-open", open);
}

if (menuButton && sidebar) {
  menuButton.addEventListener("click", () => {
    setMenu(menuButton.getAttribute("aria-expanded") !== "true");
  });
  sidebar.addEventListener("click", (event) => {
    if (event.target.closest("a") && window.matchMedia("(max-width: 780px)").matches) setMenu(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setMenu(false);
      menuButton.focus();
    }
  });
  document.addEventListener("click", (event) => {
    if (menuButton.getAttribute("aria-expanded") === "true" && !sidebar.contains(event.target) && !menuButton.contains(event.target)) setMenu(false);
  });
}

document.querySelectorAll("[data-period-select]").forEach((select) => {
  const form = select.closest("form");
  const custom = form?.querySelector("[data-custom-range]");
  select.addEventListener("change", () => {
    if (custom) custom.hidden = select.value !== "custom";
  });
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

const uploadProgress = document.querySelector("[data-upload-progress]");
document.querySelectorAll("[data-upload-form]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.XMLHttpRequest || !uploadProgress) return;
    event.preventDefault();
    const request = new XMLHttpRequest();
    const meter = uploadProgress.querySelector("progress");
    const label = uploadProgress.querySelector("strong");
    uploadProgress.hidden = false;
    form.querySelectorAll("button, input").forEach((control) => { control.disabled = true; });
    request.upload.addEventListener("progress", (progressEvent) => {
      if (!progressEvent.lengthComputable) return;
      const percent = Math.round((progressEvent.loaded / progressEvent.total) * 100);
      meter.value = percent;
      label.textContent = `${percent}%`;
    });
    request.addEventListener("load", () => {
      if (request.status >= 200 && request.status < 400) {
        window.location.assign(request.responseURL);
      } else {
        window.location.reload();
      }
    });
    request.addEventListener("error", () => window.location.reload());
    request.open("POST", form.action);
    request.send(new FormData(form));
  });
});

const importPage = document.querySelector("[data-import-page]");
if (importPage && !["succeeded", "failed", "cancelled"].includes(importPage.dataset.importState)) {
  const statusUrl = importPage.dataset.importStatusUrl;
  const meter = importPage.querySelector("[data-import-meter]");
  const percent = importPage.querySelector("[data-import-percent]");
  const items = importPage.querySelector("[data-import-items]");
  const phase = importPage.querySelector("[data-import-phase]");

  async function pollImport() {
    try {
      const response = await fetch(statusUrl, { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const job = await response.json();
      meter.value = job.progress;
      percent.textContent = `${Math.round(job.progress * 100)}%`;
      items.textContent = `${job.processed_items.toLocaleString()} items processed`;
      phase.textContent = job.phase;
      if (["succeeded", "failed", "cancelled"].includes(job.status)) {
        window.location.reload();
        return;
      }
      window.setTimeout(pollImport, 650);
    } catch (_error) {
      window.setTimeout(pollImport, 1500);
    }
  }

  window.setTimeout(pollImport, 500);
}
