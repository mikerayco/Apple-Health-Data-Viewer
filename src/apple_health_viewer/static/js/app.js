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
    if (event.target.closest("a") && window.matchMedia("(max-width: 780px)").matches) {
      setMenu(false);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setMenu(false);
      menuButton.focus();
    }
  });

  document.addEventListener("click", (event) => {
    if (
      menuButton.getAttribute("aria-expanded") === "true" &&
      !sidebar.contains(event.target) &&
      !menuButton.contains(event.target)
    ) {
      setMenu(false);
    }
  });
}
