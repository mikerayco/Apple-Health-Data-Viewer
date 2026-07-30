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

const SVG_NS = "http:" + "//www.w3.org/2000/svg";

function svgElement(name, attributes = {}, text = "") {
  const element = document.createElementNS(SVG_NS, name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
  if (text) element.textContent = text;
  return element;
}

function chartNumber(value) {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value);
}

function renderTrendChart(container, payload) {
  const svg = container.querySelector("[data-chart-svg]");
  const status = container.querySelector("[data-chart-status]");
  const summary = container.querySelector("[data-chart-summary]");
  const valueKey = container.dataset.chartValue;
  const unit = container.dataset.chartUnit;
  const label = container.dataset.chartLabel;
  const compact = container.dataset.chartCompact === "true";
  const points = [];
  let pendingGap = false;
  (payload.trend || []).forEach((point) => {
    pendingGap = pendingGap || Boolean(point.gap_before);
    const numericValue = point[valueKey] === null || point[valueKey] === undefined
      ? Number.NaN
      : Number(point[valueKey]);
    if (!Number.isFinite(numericValue)) {
      pendingGap = true;
      return;
    }
    points.push({ ...point, numericValue, gap_before: pendingGap });
    pendingGap = false;
  });

  if (!points.length) {
    status.textContent = "No observations to chart in this range.";
    return;
  }

  const width = 720;
  const height = compact ? 92 : 260;
  const padding = compact ? { top: 7, right: 4, bottom: 7, left: 4 } : { top: 18, right: 18, bottom: 35, left: 58 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const values = points.map((point) => point.numericValue);
  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  if (minimum === maximum) {
    const offset = Math.abs(minimum) * 0.1 || 1;
    minimum -= offset;
    maximum += offset;
  }
  const timestamps = points.map((point) => Date.parse(`${point.date}T00:00:00Z`));
  const firstTime = timestamps[0];
  const lastTime = timestamps.at(-1);
  const x = (index) => padding.left + (
    points.length === 1 || lastTime === firstTime
      ? chartWidth / 2
      : (timestamps[index] - firstTime) * chartWidth / (lastTime - firstTime)
  );
  const y = (value) => padding.top + (maximum - value) * chartHeight / (maximum - minimum);
  const segments = [];
  let segment = [];
  points.forEach((point, index) => {
    if (index && point.gap_before) {
      segments.push(segment);
      segment = [];
    }
    segment.push({ point, index });
  });
  if (segment.length) segments.push(segment);

  svg.replaceChildren();
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("aria-label", `${label} trend with ${points.length} grouped observations.`);

  if (!compact) {
    [0, 0.5, 1].forEach((fraction) => {
      const axisY = padding.top + fraction * chartHeight;
      const axisValue = maximum - fraction * (maximum - minimum);
      svg.append(
        svgElement("line", { x1: padding.left, y1: axisY, x2: width - padding.right, y2: axisY, class: "trend-grid-line" }),
        svgElement("text", { x: padding.left - 10, y: axisY + 4, class: "trend-axis-label", "text-anchor": "end" }, chartNumber(axisValue)),
      );
    });
  }

  segments.forEach((items) => {
    const line = items.map(({ point, index }, itemIndex) => `${itemIndex ? "L" : "M"}${x(index).toFixed(2)},${y(point.numericValue).toFixed(2)}`).join(" ");
    const firstIndex = items[0].index;
    const lastIndex = items.at(-1).index;
    const area = `${line} L${x(lastIndex).toFixed(2)},${(padding.top + chartHeight).toFixed(2)} L${x(firstIndex).toFixed(2)},${(padding.top + chartHeight).toFixed(2)} Z`;
    svg.append(
      svgElement("path", { d: area, class: "trend-area" }),
      svgElement("path", { d: line, class: "trend-line" }),
    );
  });

  if (!compact && points.length <= 80) {
    points.forEach((point, index) => {
      const circle = svgElement("circle", {
        cx: x(index), cy: y(point.numericValue), r: 3.5, class: "trend-point", tabindex: "0",
        "aria-label": `${point.date}: ${chartNumber(point.numericValue)} ${unit}`,
      });
      circle.append(svgElement("title", {}, `${point.date}: ${chartNumber(point.numericValue)} ${unit}`));
      svg.append(circle);
    });
    const first = points[0];
    const last = points[points.length - 1];
    svg.append(
      svgElement("text", { x: padding.left, y: height - 8, class: "trend-axis-label" }, first.date),
      svgElement("text", { x: width - padding.right, y: height - 8, class: "trend-axis-label", "text-anchor": "end" }, last.end_date || last.date),
    );
  }

  status.hidden = true;
  svg.hidden = false;
  summary.textContent = `${points.length} ${payload.granularity || "grouped"} observations. Range ${chartNumber(Math.min(...values))}–${chartNumber(Math.max(...values))} ${unit}; latest ${chartNumber(values.at(-1))} ${unit}.`;
}

document.querySelectorAll("[data-trend-chart]").forEach(async (container) => {
  const status = container.querySelector("[data-chart-status]");
  status.hidden = false;
  try {
    const response = await fetch(container.dataset.chartUrl, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("Chart request failed");
    const payload = await response.json();
    if (payload.status !== "ready") throw new Error("Chart data unavailable");
    renderTrendChart(container, payload);
  } catch (_error) {
    status.textContent = container.dataset.chartHasTable === "true"
      ? "Chart unavailable. The grouped data table remains available below."
      : "Chart unavailable. Open the linked category page for its grouped data table.";
    status.classList.add("chart-state-error");
  }
});
