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

function groupedBy(items, keyFor) {
  const groups = new Map();
  items.forEach((item) => {
    const key = keyFor(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });
  return groups;
}

function renderRoute(container, payload) {
  const routeSvg = container.querySelector("[data-route-svg]");
  const elevationSvg = container.querySelector("[data-elevation-svg]");
  const status = container.querySelector("[data-route-status]");
  const summary = container.querySelector("[data-route-summary]");
  const points = (payload.points || []).filter((point) => Number.isFinite(point.latitude) && Number.isFinite(point.longitude));
  if (points.length < 2) {
    status.textContent = "Route points are unavailable.";
    return;
  }
  const width = 720;
  const height = 400;
  const padding = 28;
  const bounds = payload.bounds || {};
  const minLat = Number(bounds.min_latitude);
  const maxLat = Number(bounds.max_latitude);
  const minLon = Number(bounds.min_longitude);
  const maxLon = Number(bounds.max_longitude);
  const latitudeScale = Math.cos((minLat + maxLat) / 2 * Math.PI / 180);
  const lonSpan = (maxLon - minLon) * latitudeScale;
  const latSpan = maxLat - minLat;
  const availableWidth = width - padding * 2;
  const availableHeight = height - padding * 2;
  const scale = Math.min(
    lonSpan ? availableWidth / lonSpan : Number.POSITIVE_INFINITY,
    latSpan ? availableHeight / latSpan : Number.POSITIVE_INFINITY,
  );
  const safeScale = Number.isFinite(scale) ? scale : 1;
  const drawnWidth = lonSpan * safeScale;
  const drawnHeight = latSpan * safeScale;
  const xOffset = padding + (availableWidth - drawnWidth) / 2;
  const yOffset = padding + (availableHeight - drawnHeight) / 2;
  const x = (value) => xOffset + (value - minLon) * latitudeScale * safeScale;
  const y = (value) => yOffset + (maxLat - value) * safeScale;
  const segments = groupedBy(points, (point) => point.segment || 0);
  routeSvg.replaceChildren();
  routeSvg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  routeSvg.setAttribute("aria-label", `Workout route using ${points.length} rendered points across ${segments.size} segments without map tiles.`);
  segments.forEach((segmentPoints) => {
    const line = segmentPoints.map((point, index) => `${index ? "L" : "M"}${x(point.longitude).toFixed(2)},${y(point.latitude).toFixed(2)}`).join(" ");
    routeSvg.append(svgElement("path", { d: line, class: "route-line" }));
  });
  const startMarker = svgElement("circle", {
    cx: x(points[0].longitude), cy: y(points[0].latitude), r: 7,
    class: "route-marker route-start",
  });
  startMarker.append(svgElement("title", {}, "Route start"));
  const endX = x(points.at(-1).longitude);
  const endY = y(points.at(-1).latitude);
  const endMarker = svgElement("rect", {
    x: endX - 7, y: endY - 7, width: 14, height: 14,
    class: "route-marker route-end",
  });
  endMarker.append(svgElement("title", {}, "Route end"));
  routeSvg.append(startMarker, endMarker);
  routeSvg.hidden = false;

  const elevationStatus = container.querySelector("[data-elevation-status]");
  const elevation = points.filter((point) => Number.isFinite(point.elevation_m));
  if (elevation.length > 1) {
    const chartHeight = 150;
    const values = elevation.map((point) => point.elevation_m);
    const low = Math.min(...values);
    const high = Math.max(...values);
    const span = high - low || 1;
    const maximumSequence = Math.max(payload.original_point_count - 1, 1);
    const runs = [];
    let run = [];
    let priorSegment = null;
    points.forEach((point) => {
      const hasElevation = Number.isFinite(point.elevation_m);
      const breaksRun = priorSegment !== null && (
        point.segment !== priorSegment || point.elevation_gap_before || !hasElevation
      );
      if (breaksRun && run.length) {
        runs.push(run);
        run = [];
      }
      if (hasElevation) run.push(point);
      priorSegment = point.segment;
    });
    if (run.length) runs.push(run);
    elevationSvg.replaceChildren();
    elevationSvg.setAttribute("viewBox", `0 0 ${width} ${chartHeight}`);
    elevationSvg.setAttribute("aria-label", `Elevation profile from ${chartNumber(low)} to ${chartNumber(high)} metres with missing intervals left unconnected.`);
    runs.forEach((runPoints) => {
      if (runPoints.length < 2) return;
      const elevationLine = runPoints.map((point, index) => {
        const pointX = 10 + point.route_sequence / maximumSequence * (width - 20);
        const pointY = 10 + (high - point.elevation_m) / span * (chartHeight - 30);
        return `${index ? "L" : "M"}${pointX.toFixed(2)},${pointY.toFixed(2)}`;
      }).join(" ");
      elevationSvg.append(svgElement("path", { d: elevationLine, class: "elevation-line" }));
    });
    elevationSvg.hidden = false;
    elevationStatus.hidden = true;
  } else {
    elevationStatus.textContent = "Elevation data was not exported for this route.";
  }
  status.hidden = true;
  summary.textContent = `${payload.original_point_count.toLocaleString()} original points; ${payload.returned_point_count.toLocaleString()} rendered. ${payload.map_note}`;
}

document.querySelectorAll("[data-route-view]").forEach(async (container) => {
  const status = container.querySelector("[data-route-status]");
  try {
    const response = await fetch(container.dataset.routeUrl, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("Route request failed");
    renderRoute(container, await response.json());
  } catch (_error) {
    status.textContent = "Route visualization unavailable. Exported totals remain visible.";
    status.classList.add("chart-state-error");
    const elevationStatus = container.querySelector("[data-elevation-status]");
    elevationStatus.textContent = "Elevation visualization unavailable.";
    elevationStatus.classList.add("chart-state-error");
  }
});

function renderECG(container, payload) {
  const svg = container.querySelector("[data-ecg-svg]");
  const status = container.querySelector("[data-ecg-status]");
  const summary = container.querySelector("[data-ecg-summary]");
  const samples = (payload.samples || []).filter((sample) => Number.isFinite(sample.time_seconds) && Number.isFinite(sample.amplitude));
  if (samples.length < 2) {
    status.textContent = "Waveform samples are unavailable.";
    return;
  }
  const width = 1000;
  const height = 360;
  const padding = { top: 25, right: 20, bottom: 38, left: 58 };
  const values = samples.map((sample) => sample.amplitude);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low || 1;
  const firstTime = samples[0].time_seconds;
  const lastTime = samples.at(-1).time_seconds;
  const duration = lastTime - firstTime || 1;
  const x = (value) => padding.left + (value - firstTime) / duration * (width - padding.left - padding.right);
  const y = (value) => padding.top + (high - value) / span * (height - padding.top - padding.bottom);
  const line = samples.map((sample, index) => `${index ? "L" : "M"}${x(sample.time_seconds).toFixed(2)},${y(sample.amplitude).toFixed(2)}`).join(" ");
  svg.replaceChildren();
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", width);
  svg.setAttribute("aria-label", `ECG waveform with ${samples.length} rendered samples in ${payload.amplitude_unit || "exported units"}.`);
  for (let index = 0; index <= 10; index += 1) {
    const gridX = padding.left + index / 10 * (width - padding.left - padding.right);
    svg.append(svgElement("line", { x1: gridX, y1: padding.top, x2: gridX, y2: height - padding.bottom, class: "wave-grid-line" }));
  }
  for (let index = 0; index <= 6; index += 1) {
    const gridY = padding.top + index / 6 * (height - padding.top - padding.bottom);
    svg.append(svgElement("line", { x1: padding.left, y1: gridY, x2: width - padding.right, y2: gridY, class: "wave-grid-line" }));
  }
  svg.append(svgElement("path", { d: line, class: "wave-line" }));
  svg.append(
    svgElement("text", { x: padding.left, y: height - 10, class: "trend-axis-label" }, `${chartNumber(firstTime)} s`),
    svgElement("text", { x: width - padding.right, y: height - 10, class: "trend-axis-label", "text-anchor": "end" }, `${chartNumber(lastTime)} s`),
  );
  svg.hidden = false;
  status.hidden = true;
  summary.textContent = `${payload.sample_count.toLocaleString()} original samples; ${samples.length.toLocaleString()} rendered. Amplitude range ${chartNumber(low)}–${chartNumber(high)} ${payload.amplitude_unit || ""}.`;

  const zoom = container.querySelector("[data-wave-zoom]");
  const applyZoom = () => svg.setAttribute("width", width * Number(zoom.value));
  zoom.addEventListener("input", applyZoom);
  container.querySelectorAll("button[data-wave-zoom]").forEach((button) => {
    button.addEventListener("click", () => {
      const change = button.dataset.waveZoom === "in" ? 1 : -1;
      zoom.value = String(Math.max(Number(zoom.min), Math.min(Number(zoom.max), Number(zoom.value) + change)));
      applyZoom();
    });
  });
}

document.querySelectorAll("[data-ecg-view]").forEach(async (container) => {
  const status = container.querySelector("[data-ecg-status]");
  try {
    const response = await fetch(container.dataset.ecgUrl, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("ECG request failed");
    renderECG(container, await response.json());
  } catch (_error) {
    status.textContent = "Waveform unavailable. Exported metadata remains visible.";
    status.classList.add("chart-state-error");
  }
});
