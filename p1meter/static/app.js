"use strict";

const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const DAY = 86400;
const nf = (digits) => new Intl.NumberFormat("nl-NL", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const fmt0 = nf(0), fmt1 = nf(1), fmt2 = nf(2);
const euro = new Intl.NumberFormat("nl-NL", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const SERIES = [
  { name: "Afname", color: "var(--series-1)" },
  { name: "Teruglevering", color: "var(--series-2)" },
];

async function api(path) {
  const response = await fetch(path);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function svg(tag, attrs = {}, parent) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.appendChild(el);
  return el;
}

function label(parent, x, y, text, anchor = "middle") {
  svg("text", { x, y, "text-anchor": anchor }, parent).textContent = text;
}

/* ---------- tooltip ---------- */

const tooltip = $("tooltip");

function showTooltip(event, title, rows) {
  tooltip.replaceChildren();
  const head = document.createElement("div");
  head.className = "t";
  head.textContent = title;
  tooltip.appendChild(head);
  for (const [color, value, name] of rows) {
    const row = document.createElement("div");
    row.className = "row";
    const swatch = document.createElement("i");
    swatch.style.background = color;
    const strong = document.createElement("b");
    strong.textContent = value;
    const span = document.createElement("span");
    span.textContent = name;
    row.append(swatch, strong, span);
    tooltip.appendChild(row);
  }
  tooltip.hidden = false;
  const box = tooltip.getBoundingClientRect();
  let x = event.clientX + 14;
  if (x + box.width > window.innerWidth - 8) x = event.clientX - box.width - 14;
  tooltip.style.left = Math.max(8, x) + "px";
  tooltip.style.top = Math.max(8, event.clientY - box.height - 10) + "px";
}

function hideTooltip() { tooltip.hidden = true; }

/* ---------- chart scaffolding ---------- */

function niceStep(range, target) {
  const raw = range / target;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  return [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw);
}

// Creates the svg, draws horizontal gridlines for [lo, hi] and returns helpers.
function frame(container, lo, hi, format, height = 280) {
  const width = Math.max(320, container.clientWidth);
  const m = { l: 52, r: 12, t: 12, b: 26 };
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
  const step = niceStep(hi - lo || 1, 5);
  lo = Math.floor(lo / step) * step;
  hi = Math.ceil(hi / step) * step;
  if (hi === lo) hi = lo + step;
  const y = (v) => m.t + (hi - v) / (hi - lo) * (height - m.t - m.b);
  for (let v = lo; v <= hi + step / 2; v += step) {
    svg("line", { x1: m.l, x2: width - m.r, y1: y(v), y2: y(v), class: Math.abs(v) < step / 1e6 ? "zero" : "grid" }, root);
    label(root, m.l - 8, y(v) + 4, format(Math.abs(v) < step / 1e6 ? 0 : v), "end");
  }
  container.replaceChildren(root);
  return { root, width, height, m, y, left: m.l, right: width - m.r, bottom: height - m.b };
}

function emptyChart(container, text) {
  const root = svg("svg", { viewBox: "0 0 600 120" });
  svg("text", { x: 300, y: 64, "text-anchor": "middle", class: "empty" }, root).textContent = text;
  container.replaceChildren(root);
}

/* ---------- live tiles ---------- */

async function refreshLive() {
  try {
    const live = await api("api/live");
    if (!live.available) {
      $("status").textContent = "Wachten op de eerste meting van de P1-poort…";
      return;
    }
    $("live-net").textContent = fmt0.format(Math.abs(live.net_w));
    $("live-direction").textContent = live.net_w < 0 ? "teruglevering aan het net" : "afname van het net";
    $("meter-import").textContent = fmt0.format(live.import_kwh);
    $("meter-export").textContent = fmt0.format(live.export_kwh);
    $("status").textContent = live.age > 60
      ? `Laatste meting ${fmt0.format(live.age / 60)} min geleden – controleer de P1-kabel`
      : "Live verbonden met de meter";
  } catch (error) {
    $("status").textContent = "Geen verbinding met de Raspberry Pi";
  }
}

/* ---------- power chart ---------- */

const view = { days: 1, start: midnight(new Date()) };

function midnight(date) { return new Date(date.getFullYear(), date.getMonth(), date.getDate()); }
function addDays(date, n) { return new Date(date.getFullYear(), date.getMonth(), date.getDate() + n); }
function unix(date) { return Math.floor(date.getTime() / 1000); }
const dayFmt = new Intl.DateTimeFormat("nl-NL", { weekday: "short", day: "numeric", month: "short" });
const timeFmt = new Intl.DateTimeFormat("nl-NL", { hour: "2-digit", minute: "2-digit" });

function timeTicks(start, end, max) {
  const hours = (end - start) / 3600e3;
  const ticks = [];
  if (hours <= 48) {
    const step = [3, 6, 12, 24].find((s) => hours / s <= max) || 24;
    for (let d = new Date(start); d <= end; d = new Date(d.getFullYear(), d.getMonth(), d.getDate(), d.getHours() + step)) {
      ticks.push([d, timeFmt.format(d)]);
    }
  } else {
    const step = Math.ceil(hours / 24 / Math.min(max, 8));
    for (let d = new Date(start); d < end; d = addDays(d, step)) {
      ticks.push([d, new Intl.DateTimeFormat("nl-NL", { day: "numeric", month: "short" }).format(d)]);
    }
  }
  return ticks;
}

async function refreshPower() {
  const start = view.start, end = addDays(start, view.days);
  $("period-label").textContent = view.days === 1
    ? dayFmt.format(start)
    : `${dayFmt.format(start)} – ${dayFmt.format(addDays(end, -1))}`;
  $("next").disabled = end > new Date();
  const container = $("power-chart");
  let data;
  try {
    data = await api(`api/series?start=${unix(start)}&end=${unix(end)}`);
  } catch (error) {
    return emptyChart(container, "Kon de metingen niet laden");
  }
  $("period-totals").textContent =
    `Afname ${fmt1.format(data.import_kwh)} kWh · teruglevering ${fmt1.format(data.export_kwh)} kWh`;
  const points = data.points;
  if (!points.length) return emptyChart(container, "Geen metingen in deze periode");

  const hi = Math.max(100, ...points.map((p) => p[1]));
  const lo = -Math.max(100, ...points.map((p) => p[2]));
  const kw = hi - lo > 4000;
  const f = frame(container, lo, hi, (v) => kw ? fmt1.format(Math.abs(v) / 1000) + " kW" : fmt0.format(Math.abs(v)) + " W");
  const t0 = unix(start), t1 = unix(end);
  const x = (t) => f.left + (t - t0) / (t1 - t0) * (f.right - f.left);

  for (const [date, text] of timeTicks(start, end, Math.floor((f.right - f.left) / 64))) label(f.root, x(unix(date)), f.height - 8, text);

  // Import is drawn above zero, export below; a gap in the data breaks the area.
  const res = data.resolution;
  [1, 2].forEach((col, i) => {
    const sign = col === 1 ? 1 : -1;
    let area = "", line = "", open = null, prev = null, lastZero = false;
    const close = () => {
      if (open === null) return;
      area += `L${x(prev + res)},${f.y(0)}L${x(open)},${f.y(0)}Z`;
      open = null;
    };
    for (const p of points) {
      if (prev !== null && p[0] - prev > res * 1.5) close();
      // no stroke along the baseline while this series is zero
      const cmd = open === null || p[col] === 0 || lastZero ? "M" : "L";
      lastZero = p[col] === 0;
      // step shape: each bucket is an average over its own interval
      const seg = `${x(p[0])},${f.y(sign * p[col])}L${x(p[0] + res)},${f.y(sign * p[col])}`;
      area += (open === null ? `M${x(p[0])},${f.y(0)}L` : "L") + seg;
      if (p[col] !== 0) line += cmd + seg;
      if (open === null) open = p[0];
      prev = p[0];
    }
    close();
    svg("path", { d: area, fill: SERIES[i].color, "fill-opacity": 0.25 }, f.root);
    svg("path", { d: line, fill: "none", stroke: SERIES[i].color, "stroke-width": 1.5, "stroke-linejoin": "round" }, f.root);
  });

  const cross = svg("line", { y1: f.m.t, y2: f.bottom, class: "cross", visibility: "hidden" }, f.root);
  const hit = svg("rect", { x: f.left, y: f.m.t, width: f.right - f.left, height: f.bottom - f.m.t, fill: "transparent" }, f.root);
  hit.addEventListener("pointermove", (event) => {
    const box = hit.getBoundingClientRect();
    const t = t0 + (event.clientX - box.left) / box.width * (t1 - t0);
    let lo = 0, hi = points.length - 1;
    while (lo < hi) {  // last point starting at or before t
      const mid = (lo + hi + 1) >> 1;
      if (points[mid][0] <= t) lo = mid; else hi = mid - 1;
    }
    const p = points[lo];
    if (t < p[0] || t > p[0] + res) { cross.setAttribute("visibility", "hidden"); return hideTooltip(); }
    const cx = x(p[0] + res / 2);
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
    const when = new Date(p[0] * 1000);
    showTooltip(event, `${dayFmt.format(when)} ${timeFmt.format(when)}`, [
      [SERIES[0].color, fmt0.format(p[1]) + " W", "afname"],
      [SERIES[1].color, fmt0.format(p[2]) + " W", "teruglevering"],
    ]);
  });
  hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); hideTooltip(); });
}

function setRange(days) {
  view.days = days;
  view.start = addDays(midnight(new Date()), 1 - days);
  document.querySelectorAll(".segmented button").forEach((b) => b.classList.toggle("active", +b.dataset.range === days));
  refreshPower();
}

/* ---------- daily bars ---------- */

async function refreshDays() {
  const container = $("days-chart");
  let days;
  try { days = await api("api/days"); } catch (error) { return emptyChart(container, "Kon de dagtotalen niet laden"); }
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const current = days.find((d) => d.day === today);
  $("today-import").textContent = fmt1.format(current ? current.import_kwh : 0);
  $("today-export").textContent = fmt1.format(current ? current.export_kwh : 0);
  if (!days.length) return emptyChart(container, "Nog geen metingen");

  days = days.slice(-62);
  const hi = Math.max(1, ...days.map((d) => d.import_kwh));
  const lo = -Math.max(1, ...days.map((d) => d.export_kwh));
  const f = frame(container, lo, hi, (v) => fmt0.format(Math.abs(v)), 240);
  const slot = (f.right - f.left) / days.length;
  const width = Math.max(2, Math.min(28, slot - 2));
  const every = Math.ceil(days.length / Math.max(1, Math.floor((f.right - f.left) / 64)));
  const rounded = (x, y0, y1) => {  // rounded at the data end, square at the baseline
    const r = Math.min(4, width / 2, Math.abs(y1 - y0)), s = y1 < y0 ? 1 : -1;
    return `M${x},${y0}V${y1 + s * r}Q${x},${y1} ${x + r},${y1}H${x + width - r}Q${x + width},${y1} ${x + width},${y1 + s * r}V${y0}Z`;
  };
  days.forEach((d, i) => {
    const x = f.left + slot * i + (slot - width) / 2;
    const date = new Date(d.day + "T00:00:00");
    const group = svg("g", { class: "bar", tabindex: 0 }, f.root);
    svg("path", { d: rounded(x, f.y(0) - 1, f.y(d.import_kwh)), fill: SERIES[0].color }, group);
    svg("path", { d: rounded(x, f.y(0) + 1, f.y(-d.export_kwh)), fill: SERIES[1].color }, group);
    svg("rect", { x: f.left + slot * i, y: f.m.t, width: slot, height: f.bottom - f.m.t, fill: "transparent" }, group);
    if (i % every === 0) label(f.root, x + width / 2, f.height - 8, new Intl.DateTimeFormat("nl-NL", { day: "numeric", month: "short" }).format(date));
    const tip = (event) => showTooltip(event, dayFmt.format(date) + (d.coverage < 0.9 ? " (onvolledig)" : ""), [
      [SERIES[0].color, fmt1.format(d.import_kwh) + " kWh", "afname"],
      [SERIES[1].color, fmt1.format(d.export_kwh) + " kWh", "teruglevering"],
    ]);
    group.addEventListener("pointermove", tip);
    group.addEventListener("pointerleave", hideTooltip);
    const open = () => { setRange(1); view.start = date; refreshPower(); $("power-chart").scrollIntoView({ behavior: "smooth", block: "center" }); };
    group.addEventListener("click", open);
    group.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); });
  });
}

/* ---------- battery ---------- */

function quantile(sorted, q) {
  if (!sorted.length) return 0;
  const pos = (sorted.length - 1) * q, base = Math.floor(pos);
  return sorted[base] + (sorted[Math.min(base + 1, sorted.length - 1)] - sorted[base]) * (pos - base);
}

async function refreshBattery() {
  const form = $("battery-form");
  const params = new URLSearchParams({
    power: form.power.value,
    efficiency: form.efficiency.value / 100,
    import_price: form.import_price.value,
    export_price: form.export_price.value,
  });
  const container = $("battery-chart"), body = $("battery-table").tBodies[0];
  let report;
  try {
    report = await api("api/battery?" + params);
  } catch (error) {
    $("battery-summary").textContent = "Berekening mislukt: " + error.message;
    return;
  }
  const results = report.results;
  body.replaceChildren();
  if (!report.days) {
    $("battery-summary").textContent = "Nog geen metingen om mee te rekenen.";
    return emptyChart(container, "Nog geen metingen");
  }

  // Smallest battery that reaches 90% of what the largest one saves.
  const top = Math.max(...results.map((r) => r.saving_per_year));
  const knee = top > 0 ? results.find((r) => r.saving_per_year >= 0.9 * top) : null;
  const shiftable = [...report.daily_shiftable_kwh].sort((a, b) => a - b);

  $("battery-summary").textContent = knee
    ? `Met ${fmt0.format(knee.capacity_kwh)} kWh bespaar je bij dit verbruik ongeveer ${euro.format(knee.saving_per_year)} per jaar; een grotere batterij voegt daar weinig aan toe.`
    : "Bij deze prijzen en dit verbruik levert een batterij geen besparing op.";
  $("battery-note").textContent =
    `Gebaseerd op ${fmt1.format(report.days)} gemeten dagen (afname ${fmt0.format(report.import_kwh)} kWh, teruglevering ${fmt0.format(report.export_kwh)} kWh). ` +
    (shiftable.length
      ? `Op een doorsnee dag valt ${fmt1.format(quantile(shiftable, 0.5))} kWh te verschuiven, op de beste dagen ${fmt1.format(quantile(shiftable, 0.9))} kWh. `
      : "") +
    "‘Per jaar’ rekent het gemeten patroon door naar 365 dagen: meet over meerdere seizoenen voor een betrouwbaar beeld. " +
    "Eigen verbruik van de batterij en dynamische tarieven zijn niet meegerekend.";

  const f = frame(container, Math.min(0, ...results.map((r) => r.saving_per_year)), Math.max(10, top), (v) => fmt0.format(v), 220);
  const slot = (f.right - f.left) / results.length;
  const width = Math.min(48, slot * 0.6);
  results.forEach((r, i) => {
    const x = f.left + slot * i + (slot - width) / 2;
    const y0 = f.y(0), y1 = f.y(r.saving_per_year);
    const radius = Math.min(4, Math.abs(y1 - y0)), s = y1 < y0 ? 1 : -1;
    const group = svg("g", { class: "bar" + (knee && r !== knee ? " dim" : ""), tabindex: 0 }, f.root);
    svg("path", {
      d: `M${x},${y0}V${y1 + s * radius}Q${x},${y1} ${x + radius},${y1}H${x + width - radius}Q${x + width},${y1} ${x + width},${y1 + s * radius}V${y0}Z`,
      fill: SERIES[0].color,
    }, group);
    svg("rect", { x: f.left + slot * i, y: f.m.t, width: slot, height: f.bottom - f.m.t, fill: "transparent" }, group);
    label(f.root, x + width / 2, f.height - 8, fmt0.format(r.capacity_kwh) + (slot >= 56 ? " kWh" : ""));
    group.addEventListener("pointermove", (event) => showTooltip(event, `${fmt0.format(r.capacity_kwh)} kWh batterij`, [
      [SERIES[0].color, euro.format(r.saving_per_year), "besparing per jaar"],
    ]));
    group.addEventListener("pointerleave", hideTooltip);

    const row = body.insertRow();
    if (r === knee) row.className = "best";
    for (const text of [
      fmt0.format(r.capacity_kwh) + " kWh",
      `${fmt1.format(r.import_after_kwh)} kWh`,
      `${fmt1.format(r.export_after_kwh)} kWh`,
      `${fmt1.format(r.discharged_kwh)} kWh`,
      fmt1.format(r.cycles),
      "€ " + fmt2.format(r.saving),
      euro.format(r.saving_per_year),
    ]) row.insertCell().textContent = text;
  });
}

/* ---------- wiring ---------- */

async function init() {
  document.querySelectorAll(".segmented button").forEach((b) => b.addEventListener("click", () => setRange(+b.dataset.range)));
  $("prev").addEventListener("click", () => { view.start = addDays(view.start, -view.days); refreshPower(); });
  $("next").addEventListener("click", () => { view.start = addDays(view.start, view.days); refreshPower(); });
  $("battery-form").addEventListener("submit", (event) => { event.preventDefault(); refreshBattery(); });

  try {
    const config = await api("api/config");
    const form = $("battery-form");
    form.power.value = config.battery_power_w;
    form.efficiency.value = Math.round(config.battery_efficiency * 100);
    form.import_price.value = config.import_price;
    form.export_price.value = config.export_price;
  } catch (error) { /* status line reports the connection problem */ }

  refreshLive(); refreshPower(); refreshDays(); refreshBattery();
  setInterval(refreshLive, 2000);
  setInterval(() => {
    if (!tooltip.hidden) return;  // do not redraw under the pointer
    if (addDays(view.start, view.days) > new Date()) refreshPower();
    refreshDays();
  }, 60000);
  let resize;
  window.addEventListener("resize", () => {
    clearTimeout(resize);
    resize = setTimeout(() => { refreshPower(); refreshDays(); refreshBattery(); }, 200);
  });
}

init();
