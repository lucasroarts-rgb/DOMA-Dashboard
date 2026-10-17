const STATIC_DATA = window.DOMA_STATIC_DATA || null;
// Don't trust STATIC_DATA alone - data.js is a ~1.5MB payload loaded with
// onerror="void 0" (silently swallowed), so any load hiccup leaves
// STATIC_DATA null on the published site too, which used to flip IS_STATIC
// to false and show the ebook upload fields there - guaranteed 405 (no
// backend on GitHub Pages), confirmed live 2026-09-23. Hostname is a second,
// independent signal: only localhost/127.0.0.1 (where app.py actually runs)
// counts as "local", so a data.js failure now fails safe (upload hidden)
// instead of failing open (upload shown, guaranteed to break).
const IS_LOCALHOST = ["localhost", "127.0.0.1"].includes(location.hostname);
const IS_STATIC = Boolean(STATIC_DATA) || !IS_LOCALHOST;

// Where the Content Calendar upload/create-pages calls go. Local: same
// origin, no auth needed beyond being on your own machine. Published site:
// a separately-deployed copy of this app (see generate_public_site.py's
// api_base/upload_auth) when one's configured, else null - no working
// upload target, dashboard.js falls back to explaining that instead of
// showing a control that can only fail.
const CALENDAR_API_BASE = IS_STATIC ? (STATIC_DATA && STATIC_DATA.api_base) || null : "";
const CALENDAR_UPLOAD_AUTH = IS_STATIC ? (STATIC_DATA && STATIC_DATA.upload_auth) || null : null;
const CALENDAR_UPLOAD_AVAILABLE = CALENDAR_API_BASE !== null;

let dashboard = null;

/* ---------- password gate (published site only) ----------
   NOT real security - this is a client-side deterrent only. The full
   dataset is still in this page's HTML/data.js and downloadable by anyone
   who knows the URL, view-source, or calls the Graph/GA4/GSC endpoints
   directly. Chosen deliberately (see README.md) over paying for GitHub Pro
   or setting up Cloudflare Access. To change the password: replace
   PASSWORD_HASH below with the SHA-256 hex digest of the new password
   (e.g. in a browser console: crypto.subtle.digest("SHA-256", new
   TextEncoder().encode("newpassword")) then hex-encode the result, or use
   any "sha256 hash" tool). */
const PASSWORD_HASH = "e384f71129f78362b4fbb4edfc53b56f70223d04a148278ef4d9040002442a48"; // the plain password is not kept in the code
const PASSWORD_SESSION_KEY = "doma_dashboard_unlocked";

async function sha256Hex(text) {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function initPasswordGate() {
  if (!IS_STATIC) return true; // local dashboard: no gate, you're already on your own machine
  if (sessionStorage.getItem(PASSWORD_SESSION_KEY) === "1") return true;

  const gate = document.getElementById("passwordGate");
  const form = document.getElementById("passwordGateForm");
  const input = document.getElementById("passwordGateInput");
  const error = document.getElementById("passwordGateError");
  if (!gate || !form) return true;

  gate.style.display = "flex";
  document.querySelector(".shell").style.display = "none";

  return new Promise((resolve) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const hash = await sha256Hex(input.value);
      if (hash === PASSWORD_HASH) {
        sessionStorage.setItem(PASSWORD_SESSION_KEY, "1");
        gate.style.display = "none";
        document.querySelector(".shell").style.display = "";
        resolve(true);
      } else {
        error.style.display = "block";
        input.value = "";
        input.focus();
      }
    });
  });
}

function number(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("en-US");
}

function percent(value) {
  if (value === null || value === undefined) return "-";
  return `${Number(value).toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;
}

function duration(seconds) {
  if (!seconds) return "-";
  const s = Math.round(Number(seconds));
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return m > 0 ? `${m}m ${rem}s` : `${rem}s`;
}

function shortDate(isoDate) {
  if (!isoDate) return "-";
  const d = new Date(isoDate + "T00:00:00");
  if (Number.isNaN(d.getTime())) return isoDate;
  return d.toLocaleDateString("en-US", { day: "2-digit", month: "short" });
}

function fullDate(isoDate) {
  if (!isoDate) return "-";
  const d = new Date(isoDate + "T00:00:00");
  if (Number.isNaN(d.getTime())) return isoDate;
  return d.toLocaleDateString("en-US", { day: "2-digit", month: "short", year: "numeric" });
}

// One date-range format everywhere: "Jul 17 - Oct 15, 2026" (year shown
// once when both ends share it, on both ends when they don't).
function dateRange(startIso, endIso) {
  if (!startIso || !endIso) return fullDate(startIso || endIso);
  const a = new Date(String(startIso).slice(0, 10) + "T00:00:00");
  const b = new Date(String(endIso).slice(0, 10) + "T00:00:00");
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return `${startIso} - ${endIso}`;
  const sameYear = a.getFullYear() === b.getFullYear();
  const startLabel = a.toLocaleDateString("en-US", sameYear ? { day: "2-digit", month: "short" } : { day: "2-digit", month: "short", year: "numeric" });
  return `${startLabel} - ${fullDate(String(endIso).slice(0, 10))}`;
}

function money(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `$${Number(value).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

// Small inline warning shown under a card value or panel title. Icon + text,
// never color alone.
function warnNote(text) {
  return `<span class="card-warn" role="note"><span aria-hidden="true">&#9888;</span> ${text}</span>`;
}

// Average position: a higher number is worse, so the change is shown in
// positions ("+0.7 worse") instead of a % that reads like growth.
function positionDeltaBadge(curr, prev) {
  if (curr === null || curr === undefined || prev === null || prev === undefined || !prev) return "";
  const diff = Math.round((Number(curr) - Number(prev)) * 10) / 10;
  if (!Number.isFinite(diff)) return "";
  if (diff === 0) return `<span class="delta delta-flat">±0 positions</span>`;
  const worse = diff > 0;
  return `<span class="delta ${worse ? "delta-bad" : "delta-good"}" title="Average position went from ${prev} to ${curr}">${worse ? "+" : "-"}${Math.abs(diff)} ${worse ? "worse" : "better"}</span>`;
}

// GSC/GA4 breakdown tables (queries, countries, devices) used to be fixed at
// 180 days. The device rows cover every click, so when they add up to the
// range's total the breakdowns follow the selected range.
function breakdownFollowsRange(rows, total, key) {
  if (!rows || !rows.length || !total) return false;
  const sum = rows.reduce((s, r) => s + (Number(r[key]) || 0), 0);
  return Math.abs(sum - total) / total <= 0.05;
}

// Shared by Content Suggestions, the calendar chips and the SEO gap table:
// queries the backend marks relevant === false (sponsor/offer searches that
// land on the offers page) stay hidden until this is switched on.
let showSponsorQueries = false;

function visibleGaps(gaps) {
  return (gaps || []).filter((g) => showSponsorQueries || g.relevant !== false);
}

function hiddenGapCount(gaps) {
  return (gaps || []).filter((g) => g.relevant === false).length;
}

// The ebook list used to be matched on "guide" in the title, which pulled in
// salary/management pages. Until the backend list is clean, only treat a row
// as an ebook when its path or title says so.
function isEbookRow(p) {
  return /ebook/i.test(`${p.page_path || ""} ${p.page_title || ""}`);
}

function gapActionLookup() {
  const map = new Map();
  (dashboard.content_suggestions?.content_gaps || []).forEach((g) => {
    if (g.action) map.set(g.query, g);
  });
  return map;
}

// Only used for long free-text fields pasted in verbatim (meeting minutes) -
// most of this dashboard's text fields are short and already trusted as-is,
// but a full pasted recap is exactly the kind of text likely to contain a
// stray "<" or "&" that would otherwise break the markup around it.
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// Task descriptions, context notes, and comments often have a raw URL pasted
// straight into them (a Google Doc link, a GHL post link) - this turns those
// into a real one-click-open link plus a copy button instead of a long dead
// string of text the team has to hand-select (2026-09-29, Lucas: "make the
// dashboard links easier to copy or open with a single click"). Everything
// that isn't a URL is still run through escapeHtml, same as before.
const URL_PATTERN = /(https?:\/\/[^\s<>"']+)/g;

function linkifyText(text) {
  if (!text) return "";
  return String(text)
    .split(URL_PATTERN)
    .map((part, i) => {
      if (i % 2 === 0) return escapeHtml(part);
      const url = part.replace(/[).,;]+$/, ""); // trailing sentence punctuation often gets swept into the paste
      const trailing = part.slice(url.length);
      let display;
      try {
        const u = new URL(url);
        const path = u.pathname === "/" ? "" : u.pathname.length > 22 ? u.pathname.slice(0, 22) + "…" : u.pathname;
        display = u.hostname.replace(/^www\./, "") + path;
      } catch {
        display = url.length > 42 ? url.slice(0, 42) + "…" : url;
      }
      return (
        `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="desc-link" title="${escapeHtml(url)}">&#128279; ${escapeHtml(display)}</a>` +
        `<button type="button" class="desc-link-copy" data-url="${escapeHtml(url)}" title="Copy link">⧉</button>${escapeHtml(trailing)}`
      );
    })
    .join("");
}

// One shared, document-level handler for every ".desc-link-copy" button -
// they show up in Team & Meetings, To Do, and the monthly recap, so this is
// wired once in init() rather than per-view.
function initDescLinkCopy() {
  document.addEventListener("click", async (event) => {
    const btn = event.target.closest(".desc-link-copy");
    if (!btn) return;
    try {
      await navigator.clipboard.writeText(btn.dataset.url);
      const original = btn.textContent;
      btn.textContent = "✓";
      btn.classList.add("copied");
      setTimeout(() => {
        btn.textContent = original;
        btn.classList.remove("copied");
      }, 1200);
    } catch (error) {
      console.error("Failed to copy link:", error);
    }
  });
}

function titleFromUrl(url) {
  try {
    const path = new URL(url).pathname.replace(/\/$/, "");
    const slug = path.split("/").filter(Boolean).pop();
    if (!slug) return url.replace(/^https?:\/\/[^/]+/, "") || "/";
    return slug
      .replace(/[-_]+/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  } catch {
    return url;
  }
}

function daysBetween(isoA, isoB) {
  const a = new Date(isoA);
  const b = new Date(isoB);
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return null;
  return Math.round((b - a) / 86400000);
}

/* ---------- period-over-period comparison ---------- */

function computeDeltaPct(curr, prev) {
  if (prev === null || prev === undefined || prev === 0) return null;
  if (curr === null || curr === undefined) return null;
  const pct = ((curr - prev) / Math.abs(prev)) * 100;
  return Number.isFinite(pct) ? pct : null;
}

// True when the source already had data on the first day of the previous window
// (app.py data_start_dates). Without it a % change compares with a mostly empty window.
function prevCovered(key) {
  const first = dashboard?.data_start?.[key];
  if (!first) return true;
  const prevStart = key === "gsc" ? dashboard.search_console?.previous_window_start || dashboard.previous_start_date : dashboard.previous_start_date;
  return !prevStart || first <= prevStart;
}

function deltaBadge(curr, prev, { lowerIsBetter = false } = {}) {
  const pct = computeDeltaPct(curr, prev);
  if (pct === null) return "";
  const rounded = Math.round(pct * 10) / 10;
  if (rounded === 0) return `<span class="delta delta-flat">±0%</span>`;
  const isUp = rounded > 0;
  const good = lowerIsBetter ? !isUp : isUp;
  const arrow = isUp ? "▲" : "▼";
  const cls = good ? "delta-good" : "delta-bad";
  return `<span class="delta ${cls}">${arrow} ${Math.abs(rounded)}%</span>`;
}

async function loadDashboard(days) {
  if (IS_STATIC) {
    // IS_STATIC can now be true off the hostname alone (see its definition
    // above) even when data.js itself failed to load, so STATIC_DATA can
    // still be null here - don't assume it loaded just because we're static.
    dashboard = STATIC_DATA ? (STATIC_DATA.dashboards && STATIC_DATA.dashboards[String(days)]) || STATIC_DATA.dashboard : null;
    return;
  }
  try {
    dashboard = await fetch(`/api/dashboard?days=${Number(days || 90)}`).then((r) => r.json());
  } catch (error) {
    console.error("Failed to load dashboard:", error);
    dashboard = null;
  }
}

/* ---------- SVG line chart (hand-rolled, no external library) ---------- */

function chartEmpty(containerId, message = "No data for this period.") {
  const el = document.getElementById(containerId);
  if (el) el.innerHTML = `<div class="chart-empty">${message}</div>`;
}

// zeroBased:false fits the y-axis to the data (used for follower counts,
// where a 0-based axis turns a 7,674 -> 7,700 month into a flat line).
// A series can carry its own color class (cls) so an entity keeps the same
// color across charts, e.g. Instagram is always "b".
function svgLineChart(containerId, series, { formatter = number, zeroBased = true, height: chartHeight = 260, width: chartWidth = 900, ariaLabel = "" } = {}) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const validSeries = series.filter((s) => s.points && s.points.length > 1);
  if (!validSeries.length) {
    chartEmpty(containerId);
    return;
  }

  const width = chartWidth;
  const height = chartHeight;
  const left = 46;
  const right = 16;
  const top = 16;
  const bottom = 30;
  const plotW = width - left - right;
  const plotH = height - top - bottom;

  const hasValue = (p) => p.value !== null && p.value !== undefined;
  const allValues = validSeries.flatMap((s) => s.points.filter(hasValue).map((p) => p.value));
  let maxValue = Math.max(1, ...allValues);
  let minValue = 0;
  if (!zeroBased && allValues.length) {
    const lo = Math.min(...allValues);
    const hi = Math.max(...allValues);
    const pad = Math.max(1, Math.ceil((hi - lo) * 0.15));
    minValue = Math.max(0, lo - pad);
    maxValue = hi + pad;
  }
  const span = Math.max(1, maxValue - minValue);
  const pointCount = validSeries[0].points.length;
  const clsOf = (s, i) => s.cls || (i === 0 ? "a" : "b");

  const x = (i) => left + (pointCount <= 1 ? 0 : (i / (pointCount - 1)) * plotW);
  const y = (v) => top + plotH - ((v - minValue) / span) * plotH;

  // Points with no synced value (null) break the line into segments instead
  // of drawing a misleading flat 0 - e.g. GSC clicks before a property has
  // backfilled shouldn't look identical to a real, confirmed zero-click day.
  function segmentsOf(points) {
    const segments = [];
    let current = [];
    points.forEach((p, i) => {
      if (hasValue(p)) {
        current.push(i);
      } else if (current.length) {
        segments.push(current);
        current = [];
      }
    });
    if (current.length) segments.push(current);
    return segments;
  }

  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const grid = ticks
    .map((t) => {
      const yy = top + plotH - t * plotH;
      return `<line x1="${left}" y1="${yy}" x2="${width - right}" y2="${yy}" class="chart-grid"/><text x="${left - 8}" y="${yy + 4}" class="chart-axis" text-anchor="end">${formatter(span >= 10 ? Math.round(minValue + span * t) : Math.round((minValue + span * t) * 10) / 10)}</text>`;
    })
    .join("");

  const labelEvery = Math.max(1, Math.ceil(pointCount / (width < 700 ? 5 : 8)));
  const labels = validSeries[0].points
    .map((p, i) => (i % labelEvery === 0 || i === pointCount - 1 ? `<text x="${x(i)}" y="${height - 8}" class="chart-axis" text-anchor="middle">${shortDate(p.date)}</text>` : ""))
    .join("");

  const lines = validSeries
    .map((s, seriesIndex) => {
      const cls = clsOf(s, seriesIndex);
      return segmentsOf(s.points)
        .map((indices) => {
          const points = indices.map((i) => `${x(i).toFixed(1)},${y(s.points[i].value).toFixed(1)}`).join(" ");
          return `<polyline points="${points}" class="chart-line ${cls}"/>`;
        })
        .join("");
    })
    .join("");

  const dotEvery = pointCount > 60 ? Math.ceil(pointCount / 60) : 1;
  const dots = validSeries
    .map((s, seriesIndex) => {
      const cls = clsOf(s, seriesIndex);
      return s.points
        .map((p, i) => (hasValue(p) && (i % dotEvery === 0 || i === pointCount - 1) ? `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="2.6" class="chart-dot ${cls}"/>` : ""))
        .join("");
    })
    .join("");

  const legend = validSeries
    .map((s, i) => {
      const last = [...s.points].reverse().find(hasValue) || s.points[s.points.length - 1];
      const valueLabel = hasValue(last) ? formatter(last.value) : "No data yet";
      return `<span><i class="${clsOf(s, i)}"></i>${s.label}: <strong>${valueLabel}</strong> <span class="legend-date">(${fullDate(last.date)})</span></span>`;
    })
    .join("");

  el.innerHTML = `<div class="chart-legend">${legend}</div><div class="chart-scroll"><svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escAttr(ariaLabel || validSeries.map((s) => s.label).join(", ") + " per day")}">${grid}${lines}${dots}<line class="chart-crosshair" x1="0" y1="${top}" x2="0" y2="${top + plotH}" style="display:none"/>${labels}<rect class="chart-overlay" x="${left}" y="${top}" width="${plotW}" height="${plotH}" fill="transparent"/></svg></div>`;

  // Custom hover tooltip: find the nearest point by mouse x-position and show
  // every series' exact value for that date, instead of relying on the tiny
  // native SVG <title> tooltip which is easy to miss.
  const svg = el.querySelector(".chart-svg");
  const overlay = el.querySelector(".chart-overlay");
  const crosshair = el.querySelector(".chart-crosshair");
  const tooltip = document.getElementById("chartTooltip");

  function pointerToChartX(clientX) {
    const rect = svg.getBoundingClientRect();
    const svgX = ((clientX - rect.left) / rect.width) * width;
    return svgX;
  }

  function showAt(clientX, clientY) {
    const svgX = pointerToChartX(clientX);
    const ratio = pointCount <= 1 ? 0 : (svgX - left) / plotW;
    const index = Math.max(0, Math.min(pointCount - 1, Math.round(ratio * (pointCount - 1))));
    const xPos = x(index);

    crosshair.setAttribute("x1", xPos);
    crosshair.setAttribute("x2", xPos);
    crosshair.style.display = "block";

    const rows = validSeries
      .map((s, i) => {
        const point = s.points[index];
        if (!point) return "";
        const valueLabel = point.value === null || point.value === undefined ? "No data yet" : formatter(point.value);
        return `<div class="chart-tooltip-row"><i class="${clsOf(s, i)}"></i>${s.label}: <strong>${valueLabel}</strong></div>`;
      })
      .join("");
    const dateLabel = fullDate(validSeries[0].points[index]?.date);
    tooltip.innerHTML = `<div class="chart-tooltip-date">${dateLabel}</div>${rows}`;
    tooltip.style.display = "block";
    const tw = tooltip.offsetWidth;
    const th = tooltip.offsetHeight;
    const tooltipLeft = Math.min(clientX + 14, window.innerWidth - tw - 10);
    const tooltipTop = Math.min(clientY + 14, window.innerHeight - th - 10);
    tooltip.style.left = `${Math.max(10, tooltipLeft)}px`;
    tooltip.style.top = `${Math.max(10, tooltipTop)}px`;
  }

  function hide() {
    crosshair.style.display = "none";
    tooltip.style.display = "none";
  }

  overlay.addEventListener("mousemove", (e) => showAt(e.clientX, e.clientY));
  overlay.addEventListener("mouseleave", hide);
  overlay.addEventListener(
    "touchmove",
    (e) => {
      const touch = e.touches[0];
      if (touch) showAt(touch.clientX, touch.clientY);
    },
    { passive: true }
  );
  overlay.addEventListener("touchend", hide);
}

/* ---------- donut + horizontal bar charts ---------- */

const SEGMENT_CLASSES = ["a", "b", "c"];

function topSlicesWithOther(items, labelKey, valueKey, maxSlices = 3) {
  const sorted = [...items].sort((x, y) => y[valueKey] - x[valueKey]);
  const top = sorted.slice(0, maxSlices);
  const rest = sorted.slice(maxSlices);
  const otherValue = rest.reduce((sum, r) => sum + r[valueKey], 0);
  const slices = top.map((item, i) => ({ label: item[labelKey], value: item[valueKey], cls: SEGMENT_CLASSES[i] }));
  if (otherValue > 0) slices.push({ label: "Other", value: otherValue, cls: "other" });
  return slices;
}

function svgDonutChart(containerId, items, { labelKey, valueKey, formatter = number, centerLabel = "Total" } = {}) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const validItems = (items || []).filter((i) => i[valueKey] > 0);
  if (!validItems.length) {
    el.innerHTML = "";
    return;
  }

  const slices = topSlicesWithOther(validItems, labelKey, valueKey);
  const total = slices.reduce((sum, s) => sum + s.value, 0);

  const size = 180;
  const cx = size / 2;
  const cy = size / 2;
  const r = 70;
  const strokeWidth = 26;
  const circumference = 2 * Math.PI * r;
  const gapDeg = 2.2;

  let cursorDeg = -90;
  const arcs = slices
    .map((s) => {
      const shareDeg = (s.value / total) * 360;
      const drawDeg = Math.max(0, shareDeg - gapDeg);
      const dash = (drawDeg / 360) * circumference;
      const gap = circumference - dash;
      const offset = -((cursorDeg + 90) / 360) * circumference;
      cursorDeg += shareDeg;
      return `<circle class="donut-seg ${s.cls}" cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--series-${s.cls})" stroke-width="${strokeWidth}" stroke-dasharray="${dash.toFixed(1)} ${gap.toFixed(1)}" stroke-dashoffset="${offset.toFixed(1)}" data-label="${s.label}" data-value="${s.value}" data-share="${((s.value / total) * 100).toFixed(1)}"/>`;
    })
    .join("");

  const svg = `<svg class="donut-svg" viewBox="0 0 ${size} ${size}" role="img">${arcs}<text x="${cx}" y="${cy - 4}" text-anchor="middle" class="donut-center-value">${formatter(total)}</text><text x="${cx}" y="${cy + 14}" text-anchor="middle" class="donut-center-label">${centerLabel}</text></svg>`;

  const legend = slices
    .map(
      (s) =>
        `<div class="donut-legend-row" data-cls="${s.cls}"><span class="donut-legend-name"><i class="${s.cls}"></i><span>${s.label}</span></span><span><span class="donut-legend-value">${formatter(s.value)}</span><span class="donut-legend-share">${((s.value / total) * 100).toFixed(1)}%</span></span></div>`
    )
    .join("");

  el.innerHTML = `${svg}<div class="donut-legend">${legend}</div>`;

  const tooltip = document.getElementById("chartTooltip");
  el.querySelectorAll(".donut-seg").forEach((seg) => {
    seg.addEventListener("mousemove", (e) => {
      tooltip.innerHTML = `<div class="chart-tooltip-row"><i class="${seg.classList[1]}"></i>${seg.dataset.label}: <strong>${formatter(Number(seg.dataset.value))}</strong> (${seg.dataset.share}%)</div>`;
      tooltip.style.display = "block";
      tooltip.style.left = `${Math.min(e.clientX + 14, window.innerWidth - 250)}px`;
      tooltip.style.top = `${Math.min(e.clientY + 14, window.innerHeight - 60)}px`;
    });
    seg.addEventListener("mouseleave", () => (tooltip.style.display = "none"));
  });
}

function svgHBarChart(containerId, items, { labelKey, valueKey, formatter = number, maxBars = 6 } = {}) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const validItems = (items || []).filter((i) => i[valueKey] > 0);
  if (!validItems.length) {
    el.innerHTML = "";
    return;
  }
  const sorted = [...validItems].sort((x, y) => y[valueKey] - x[valueKey]).slice(0, maxBars);
  const maxValue = Math.max(...sorted.map((i) => i[valueKey]));

  el.innerHTML = sorted
    .map((item, i) => {
      const cls = SEGMENT_CLASSES[i % SEGMENT_CLASSES.length];
      const pct = Math.max(2, (item[valueKey] / maxValue) * 100);
      return `<div class="hbar-row"><span class="hbar-name" title="${item[labelKey]}">${item[labelKey]}</span><span class="hbar-track"><span class="hbar-fill" style="width:${pct}%;background:var(--series-${cls})"></span></span><span class="hbar-value">${formatter(item[valueKey])}</span></div>`;
    })
    .join("");
}

/* ---------- theme toggle ---------- */

function initThemeToggle() {
  const wrap = document.getElementById("themeToggle");
  if (!wrap) return;
  const buttons = wrap.querySelectorAll("button");
  const apply = (theme) => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("domaTheme", theme);
    buttons.forEach((b) => b.classList.toggle("active", b.dataset.themeChoice === theme));
  };
  buttons.forEach((b) => b.addEventListener("click", () => apply(b.dataset.themeChoice)));
  apply(document.documentElement.getAttribute("data-theme") || "dark");
}

/* ---------- cards ---------- */

function renderCards(containerId, items) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = items
    .map(
      (item) => `
      <div class="card">
        <div class="card-label">${item.label}</div>
        <div class="card-value${item.noMotion ? " no-motion" : ""}">${item.value} ${item.delta || ""}</div>
        ${item.hint ? `<div class="card-hint">${item.hint}</div>` : ""}
      </div>`
    )
    .join("");
}

function renderTable(tableId, rows, renderRow, emptyMessage = "No data for this period.") {
  const table = document.getElementById(tableId);
  if (!table) return;
  const tbody = table.querySelector("tbody");
  if (!rows.length) {
    const colCount = table.querySelectorAll("thead th").length;
    tbody.innerHTML = `<tr><td colspan="${colCount}" class="empty">${emptyMessage}</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map(renderRow).join("");
}

/* ---------- sortable tables ---------- */

// Clicking any table header sorts that column (first click: highest first,
// second click: lowest first). The choice is kept per table and re-applied
// whenever a render replaces the rows, so it survives range changes.
const tableSortState = new WeakMap();
let applyingSort = false;

function cellSortValue(cell) {
  const text = (cell?.dataset?.sort ?? cell?.textContent ?? "").trim();
  if (!text || text === "-" || text === "—") return null;
  const numeric = text.replace(/[$,%+\s]/g, "").match(/^-?\d+(\.\d+)?/);
  if (numeric && /^[-+$]?\d/.test(text.replace(/\s/g, ""))) return parseFloat(numeric[0]);
  const asDate = Date.parse(text);
  if (!Number.isNaN(asDate) && /\d{4}/.test(text)) return asDate;
  return text.toLowerCase();
}

function applyTableSort(table) {
  const state = tableSortState.get(table);
  const tbody = table.tBodies[0];
  if (!state || !tbody) return;
  const rows = [...tbody.rows].filter((r) => !r.querySelector("td.empty, td[colspan]"));
  if (rows.length < 2) return;
  rows.sort((a, b) => {
    const x = cellSortValue(a.cells[state.col]);
    const y = cellSortValue(b.cells[state.col]);
    if (x === null && y === null) return 0;
    if (x === null) return 1; // empty cells always last
    if (y === null) return -1;
    const cmp = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
    return state.dir === "asc" ? cmp : -cmp;
  });
  // MutationObserver callbacks run later than this function, so a flag alone
  // can't stop the observer from re-sorting forever; only touch the DOM when
  // the order actually changes.
  const current = [...tbody.rows].filter((r) => !r.querySelector("td.empty, td[colspan]"));
  if (rows.some((r, i) => r !== current[i])) {
    applyingSort = true;
    rows.forEach((r) => tbody.appendChild(r));
    applyingSort = false;
  }
  [...table.tHead.rows[0].cells].forEach((th, i) => th.setAttribute("aria-sort", i === state.col ? (state.dir === "asc" ? "ascending" : "descending") : "none"));
}

document.addEventListener("click", (event) => {
  const th = event.target.closest("thead th");
  const table = th?.closest("table");
  if (!th || !table || table.classList.contains("no-sort")) return;
  const col = th.cellIndex;
  const prev = tableSortState.get(table);
  const dir = prev && prev.col === col && prev.dir === "desc" ? "asc" : "desc";
  tableSortState.set(table, { col, dir });
  applyTableSort(table);
});

// Renders replace tbody rows; put the saved order back right after.
new MutationObserver((mutations) => {
  if (applyingSort) return;
  const tables = new Set(mutations.map((m) => m.target.closest?.("table")).filter((t) => t && tableSortState.has(t)));
  tables.forEach(applyTableSort);
}).observe(document.documentElement, { childList: true, subtree: true });

/* ---------- issues / data health panel ---------- */

// Google Search Console normally trails real time by 2-3 days, so only call it
// stale once the last successful sync is clearly older than that.
function gscSyncAgeDays() {
  const last = dashboard.search_console && dashboard.search_console.last_synced_at;
  return last ? daysBetween(last, new Date().toISOString()) : null;
}

function isGscStale() {
  const age = gscSyncAgeDays();
  return age !== null && age >= 4;
}

// The cookie-consent banner went live 2026-09-11 with Consent Mode defaulting
// analytics_storage to "denied" for everyone, so GA4 only counted visitors who
// clicked Accept: every channel fell 70-95% overnight while Search Console
// clicks (Google's own count) stayed flat. Fixed 2026-10-05 (region-aware
// default: analytics counted outside EEA/UK/CH). Days in between are
// undercounted for good - GA4 can't backfill them.
const GA4_UNDERCOUNT_START = "2026-09-11";
const GA4_UNDERCOUNT_END = "2026-10-04";

// "none" | "partial" | "full": how much of a date range sits inside the
// undercounted window.
function ga4UndercountOverlap(start, end) {
  if (!start || !end) return "none";
  if (end < GA4_UNDERCOUNT_START || start > GA4_UNDERCOUNT_END) return "none";
  return start >= GA4_UNDERCOUNT_START && end <= GA4_UNDERCOUNT_END ? "full" : "partial";
}

function ga4SpansConsentChange() {
  return (
    ga4UndercountOverlap(dashboard.start_date, dashboard.end_date) !== "none" ||
    ga4UndercountOverlap(dashboard.previous_start_date, dashboard.previous_end_date) !== "none"
  );
}

// A period-over-period % is only fair when both periods were measured the
// same way: both entirely inside the undercounted window, or both outside it.
function ga4ComparisonUnreliable() {
  const now = ga4UndercountOverlap(dashboard.start_date, dashboard.end_date);
  const before = ga4UndercountOverlap(dashboard.previous_start_date, dashboard.previous_end_date);
  // Two "partial" periods can hold very different shares of undercounted days (30 days on
  // 2026-10-17: 18 vs 6), so only none/none and full/full compare fairly.
  return !((now === "none" && before === "none") || (now === "full" && before === "full"));
}

// Search Console lags 2-3 days, so its numbers cover a shifted window (app.py
// _align_search_console_window). Say which one, or the card disagrees with the range picker.
function gscWindowNote() {
  const gsc = dashboard.search_console || {};
  if (!gsc.window_start || !gsc.window_end || (gsc.window_start === dashboard.start_date && gsc.window_end === dashboard.end_date)) return "";
  return ` · Search Console ${dateRange(gsc.window_start, gsc.window_end)}`;
}

function ga4UndercountOverlaps() {
  return ga4UndercountOverlap(dashboard.start_date, dashboard.end_date) !== "none";
}

function computeIssues() {
  const issues = [];
  const gsc = dashboard.search_console;
  const ga4 = dashboard.ga4;
  const ghl = dashboard.ghl;
  const social = dashboard.social;
  const prev = dashboard.previous;
  const gscStale = isGscStale();
  const consentAffected = ga4SpansConsentChange();

  if (gscStale) {
    issues.push({
      severity: "error",
      text: `Search Console hasn't synced in ${gscSyncAgeDays()} days (last good sync: ${fullDate(gsc.last_synced_at.slice(0, 10))}). Google clicks, impressions and their % changes here are incomplete - this is missing data, not a real drop. Fix: run scripts/gsc_oauth_setup.py to re-authorize.`,
    });
  }
  if (consentAffected && ga4.available) {
    issues.push({
      severity: "warn",
      text: `GA4 undercounted visitors from ${fullDate(GA4_UNDERCOUNT_START)} to ${fullDate(GA4_UNDERCOUNT_END)}: the cookie banner only let it count visitors who clicked Accept (every channel fell 70-95% overnight while Search Console clicks stayed flat). Fixed ${fullDate("2026-10-05")} - visitors outside the EU/UK/Switzerland are counted again. Sessions in that window are low for good, and % changes across it aren't comparable; use Search Console clicks as the real organic trend.`,
    });
  }

  if (!gsc.available && !gscStale) {
    issues.push({
      severity: "info",
      text: "Search Console has no data for this period yet. New/re-verified properties can take Google 1-2 days to backfill - re-run scripts/sync_gsc.py after that.",
    });
  }
  if (!ga4.available) {
    issues.push({ severity: "warn", text: "No GA4 traffic data synced yet. Run scripts/sync_ga4.py." });
  }
  if (!ghl.available) {
    issues.push({ severity: "warn", text: "No GoHighLevel lead data synced yet. Run scripts/sync_ghl.py." });
  }
  if (!social.available) {
    issues.push({ severity: "warn", text: "No Facebook/Instagram data synced yet. Run scripts/sync_meta_organic.py." });
  }
  if (gsc.available && gsc.ctr < 2) {
    issues.push({
      severity: "warn",
      text: `Organic CTR is low (${percent(gsc.ctr)}) for ${number(gsc.impressions)} impressions - titles/meta descriptions may need work on the top pages below.`,
    });
  }
  if (gsc.available && gsc.position > 20) {
    issues.push({
      severity: "warn",
      text: `Average search position is ${gsc.position} - most clicks come from positions 1-10, so ranking is likely capping organic traffic.`,
    });
  }
  if (ga4.available && ga4.sessions > 0) {
    const engagedRate = ga4.engaged_sessions / ga4.sessions;
    if (engagedRate < 0.4) {
      issues.push({
        severity: "warn",
        text: `Only ${percent(engagedRate * 100)} of sessions are "engaged" (GA4 default: 10s+ or 2+ pageviews) - most visitors are bouncing quickly.`,
      });
    }
  }
  const highBounce = (ga4.top_pages || []).filter((p) => p.bounce_rate >= 70 && p.sessions >= 5);
  if (highBounce.length) {
    issues.push({
      severity: "warn",
      text: `${highBounce.length} page(s) with 70%+ bounce rate and meaningful traffic: ${highBounce.slice(0, 3).map((p) => p.page_title || p.page_path).join(", ")}.`,
    });
  }

  // Lead source concentration risk - if one channel drives most leads, losing
  // it (algorithm change, form breaking, etc) becomes a single point of failure.
  if (ghl.available && ghl.total_leads >= 10 && ghl.by_source.length) {
    const top = ghl.by_source[0];
    const share = top.lead_count / ghl.total_leads;
    if (share >= 0.6) {
      issues.push({
        severity: "warn",
        text: `${percent(share * 100)} of leads come from a single source ("${top.source}") - that's a concentration risk if it slows down.`,
      });
    }
  }

  // Posting cadence - stale social presence is easy to miss without a metric for it.
  if (social.available && social.posts.length) {
    const latestPost = social.posts.reduce((latest, p) => (!latest || (p.published_at || "") > (latest.published_at || "") ? p : latest), null);
    if (latestPost?.published_at) {
      const gap = daysBetween(latestPost.published_at, new Date().toISOString());
      if (gap !== null && gap >= 7) {
        issues.push({
          severity: "warn",
          text: `No new Facebook/Instagram post in ${gap} days (last one: ${fullDate(latestPost.published_at.slice(0, 10))}).`,
        });
      }
    }
  }

  // Period-over-period trend degradation - only meaningful once there's a
  // real previous period to compare against.
  if (prev) {
    // A stale GSC feed and the GA4 consent change both make the current period
    // look like a collapse when it isn't - those get their own explanation
    // above, so don't also raise a "dropped X%" alarm for the same numbers.
    const trendChecks = [
      { curr: gsc.available && !gscStale ? gsc.clicks : null, prevVal: prev.search_console.available ? prev.search_console.clicks : null, label: "Organic clicks (GSC)" },
      { curr: ga4.available && !ga4ComparisonUnreliable() ? ga4.sessions : null, prevVal: prev.ga4.available ? prev.ga4.sessions : null, label: "Sessions (GA4)" },
      { curr: ghl.available ? ghl.total_leads : null, prevVal: prev.ghl.available ? prev.ghl.total_leads : null, label: "New leads (GoHighLevel)" },
    ];
    trendChecks.forEach(({ curr, prevVal, label }) => {
      const pct = computeDeltaPct(curr, prevVal);
      if (pct !== null && pct <= -25 && prevVal >= 5) {
        issues.push({
          severity: "warn",
          text: `${label} dropped ${Math.abs(Math.round(pct))}% vs the previous period (${number(prevVal)} → ${number(curr)}).`,
        });
      }
    });
  }

  // ghl_email is a known, expected gap (GoHighLevel doesn't expose this
  // endpoint for this sub-account) - not worth repeating as a warning every
  // time the dashboard loads.
  (dashboard.sync_status || [])
    .filter((s) => s.status !== "ok" && s.source !== "ghl_email")
    .forEach((s) => issues.push({ severity: "error", text: `${s.source}: ${s.status}${s.detail ? " - " + s.detail : ""}` }));

  // Index coverage - redirects, noindex, "crawled but not indexed" etc are
  // exactly the kind of thing easy to miss without checking Search Console by hand.
  const coverage = gsc.index_coverage;
  if (coverage?.available && coverage.issues.length) {
    const grouped = {};
    coverage.issues.forEach((i) => {
      grouped[i.coverage_state] = (grouped[i.coverage_state] || 0) + 1;
    });
    const summary = Object.entries(grouped)
      .sort((a, b) => b[1] - a[1])
      .map(([state, count]) => `${count} ${state}`)
      .join(", ");
    issues.push({
      severity: "warn",
      text: `${coverage.issues.length} of ${coverage.total_checked} checked URLs need attention in Search Console: ${summary}. See SEO > Index coverage.`,
    });
  }

  const gaps = gsc.content_gaps;
  if (gaps?.available && gaps.gaps.length) {
    const top = gaps.gaps[0];
    issues.push({
      severity: "info",
      text: `${gaps.gaps.length} content gap(s) found - e.g. "${top.query}" gets ${number(top.impressions)} impressions but ranks at position ${top.position}. See SEO > Content gap opportunities.`,
    });
  }

  const onpage = gsc.onpage_audit;
  if (onpage?.available && onpage.pages.length) {
    issues.push({
      severity: "warn",
      text: `${onpage.pages.length} of ${onpage.total_checked} pages have on-page SEO issues (title/meta/headings/alt text). See SEO > On-page audit.`,
    });
  }

  const pagespeed = gsc.pagespeed;
  if (pagespeed?.available) {
    const slow = pagespeed.pages.filter((p) => p.performance_score !== null && p.performance_score < 50);
    if (slow.length) {
      issues.push({
        severity: "warn",
        text: `${slow.length} page(s) score below 50 on mobile performance (Google PageSpeed) - worst: ${slow[0].url.replace(/^https?:\/\/[^/]+/, "") || "/"} (${slow[0].performance_score}). See SEO > Page speed.`,
      });
    }
    const consoleErrorPages = pagespeed.pages.filter((p) => p.console_errors?.length);
    if (consoleErrorPages.length) {
      issues.push({
        severity: "info",
        text: `${consoleErrorPages.length} page(s) throw real browser console errors during load (Lighthouse). See SEO > Page speed.`,
      });
    }
  }

  return issues;
}

function renderIssues() {
  const el = document.getElementById("issuesList");
  if (!el) return;
  const issues = computeIssues();
  if (!issues.length) {
    el.innerHTML = `<div class="empty">No issues detected for this period.</div>`;
    return;
  }
  el.innerHTML = issues
    .map((issue) => `<div class="issue issue-${issue.severity}"><span class="issue-dot"></span>${issue.text}</div>`)
    .join("");
}

/* ---------- view renderers ---------- */

function renderOverview() {
  const gsc = dashboard.search_console;
  const ga4 = dashboard.ga4;
  const ghl = dashboard.ghl;
  const prev = dashboard.previous;

  renderCards("overviewCards", [
    {
      label: "Organic clicks (GSC)",
      value: number(gsc.clicks),
      hint: isGscStale()
        ? `${number(gsc.impressions)} impressions · data stale, last sync ${fullDate(gsc.last_synced_at.slice(0, 10))}`
        : `${number(gsc.impressions)} impressions${gscWindowNote()}`,
      delta: prev && !isGscStale() && prevCovered("gsc") ? deltaBadge(gsc.clicks, prev.search_console.clicks) : "",
    },
    { label: "Average position", noMotion: true, value: gsc.position || "-", hint: "lower is better", delta: prev && !isGscStale() && prevCovered("gsc") ? positionDeltaBadge(gsc.position, prev.search_console.position) : "" },
    {
      label: "Sessions (GA4)",
      value: number(ga4.sessions),
      hint: ga4SpansConsentChange()
        ? `${number(ga4.active_users)} active users · undercounted ${dateRange(GA4_UNDERCOUNT_START, GA4_UNDERCOUNT_END)}`
        : `${number(ga4.active_users)} active users`,
      delta: prev && !ga4ComparisonUnreliable() && prevCovered("ga4") ? deltaBadge(ga4.sessions, prev.ga4.sessions) : "",
    },
    {
      label: "New leads (GoHighLevel)",
      value: number(ghl.total_leads),
      hint: dateRange(dashboard.start_date, dashboard.end_date),
      delta: prev && prevCovered("leads") ? deltaBadge(ghl.total_leads, prev.ghl.total_leads) : "",
    },
  ]);

  const gscByDate = new Map(gsc.daily.map((d) => [d.report_date, d.clicks]));
  const ga4ByDate = new Map(ga4.daily.map((d) => [d.report_date, d.sessions]));
  const dates = [...new Set([...gscByDate.keys(), ...ga4ByDate.keys()])].sort();

  svgLineChart("overviewChart", [
    { label: "Organic clicks", points: dates.map((d) => ({ date: d, value: gscByDate.has(d) ? gscByDate.get(d) : null })) },
    { label: "Sessions (GA4)", points: dates.map((d) => ({ date: d, value: ga4ByDate.get(d) || 0 })) },
  ]);

  renderIssues();
}

function renderSeo() {
  const gsc = dashboard.search_console;
  const prev = dashboard.previous;
  const stale = isGscStale();
  const staleHint = stale ? `data stale, last sync ${fullDate(gsc.last_synced_at.slice(0, 10))}` : undefined;
  renderCards("seoCards", [
    { label: "Clicks", value: number(gsc.clicks), hint: staleHint || gscWindowNote().replace(/^ · /, ""), delta: prev && !stale && prevCovered("gsc") ? deltaBadge(gsc.clicks, prev.search_console.clicks) : "" },
    { label: "Impressions", value: number(gsc.impressions), hint: staleHint, delta: prev && !stale && prevCovered("gsc") ? deltaBadge(gsc.impressions, prev.search_console.impressions) : "" },
    { label: "Average CTR", value: percent(gsc.ctr), hint: staleHint, delta: prev && !stale && prevCovered("gsc") ? deltaBadge(gsc.ctr, prev.search_console.ctr) : "" },
    { label: "Average position", noMotion: true, value: gsc.position || "-", hint: staleHint || "lower is better", delta: prev && !stale && prevCovered("gsc") ? positionDeltaBadge(gsc.position, prev.search_console.position) : "" },
    ...(gsc.nonbrand
      ? [
          {
            label: "Non-brand clicks",
            value: number(gsc.nonbrand.clicks),
            delta: prev && !stale && prev.search_console?.nonbrand ? deltaBadge(gsc.nonbrand.clicks, prev.search_console.nonbrand.clicks) : "",
            hint: `${gsc.clicks ? percent((gsc.nonbrand.clicks / gsc.clicks) * 100) : "-"} of clicks · ${number(gsc.nonbrand.impressions)} impressions${gsc.nonbrand.position ? `, avg. position ${gsc.nonbrand.position}` : ""}`,
          },
        ]
      : []),
  ]);
  const brandNote = document.getElementById("seoBrandNote");
  if (brandNote) {
    brandNote.hidden = !gsc.nonbrand;
    brandNote.textContent = gsc.nonbrand
      ? "Brand searches (DOMA, Dental Office Managers Alliance, Kyle Summerford, the site name) come from people who already know DOMA. Non-brand clicks show whether new people are finding the site through search."
      : "";
  }

  if (!gsc.available) {
    chartEmpty("seoChart", "No Search Console data yet - either scripts/sync_gsc.py hasn't run, or the property is still processing (new/re-verified properties take Google 1-2 days).");
  } else {
    svgLineChart("seoChart", [
      { label: "Clicks", points: gsc.daily.map((d) => ({ date: d.report_date, value: d.clicks })) },
    ]);
  }

  // The 180-day notes stay until the rows actually follow the range picker.
  const gscFollows = breakdownFollowsRange(gsc.devices, gsc.clicks, "clicks");
  ["seoQueriesNote", "seoCountriesNote", "seoDevicesNote"].forEach((id) => {
    const note = document.getElementById(id);
    if (note) note.hidden = gscFollows;
  });

  renderTable(
    "seoQueriesTable",
    gsc.top_queries,
    (q) => `<tr><td>${q.query}</td><td>${number(q.clicks)}</td><td>${number(q.impressions)}</td><td>${percent(q.ctr)}</td><td>${q.position}</td></tr>`
  );

  const coverage = gsc.index_coverage;
  document.getElementById("indexCoverageEmpty").style.display = coverage.available ? "none" : "block";
  if (coverage.available) {
    renderCards("indexCoverageCards", [
      { label: "URLs checked", value: number(coverage.total_checked) },
      { label: "Healthy", value: number(coverage.healthy_count) },
      { label: "Need attention", value: number(coverage.issues.length) },
    ]);
  } else {
    document.getElementById("indexCoverageCards").innerHTML = "";
  }
  const humanizeIndexingState = (state) =>
    !state || state === "INDEXING_STATE_UNSPECIFIED" ? "-" : state.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
  renderTable(
    "seoCountriesTable",
    gsc.countries,
    (c) => `<tr><td>${c.country_name || (c.country || "").toUpperCase()}</td><td>${number(c.clicks)}</td><td>${number(c.impressions)}</td><td>${percent(c.ctr)}</td><td>${c.position}</td></tr>`
  );

  const DEVICE_LABELS = { DESKTOP: "Desktop", MOBILE: "Mobile", TABLET: "Tablet" };
  renderTable(
    "seoDeviceTable",
    gsc.devices,
    (d) => `<tr><td>${DEVICE_LABELS[d.device] || d.device}</td><td>${number(d.clicks)}</td><td>${number(d.impressions)}</td><td>${percent(d.ctr)}</td><td>${d.position}</td></tr>`
  );

  const gaps = gsc.content_gaps;
  document.getElementById("contentGapsEmpty").style.display = gaps.available ? "none" : "block";
  const actions = gapActionLookup();
  const seoGaps = (gaps.gaps || []).map((g) => {
    const extra = actions.get(g.query);
    return { ...g, action: g.action || extra?.action, relevant: g.relevant ?? extra?.relevant };
  });
  renderSponsorToggle("contentGapsToggle", seoGaps);
  renderTable(
    "contentGapsTable",
    visibleGaps(seoGaps),
    (g) => `<tr><td>${escapeHtml(g.query)}</td><td class="gap-action">${g.action ? escapeHtml(g.action) : "-"}</td><td><a href="${escAttr(g.page)}" target="_blank" rel="noopener">${escapeHtml(g.page.replace(/^https?:\/\/[^/]+/, "") || "/")}</a></td><td>${g.position}</td><td>${number(g.impressions)}</td><td>${number(g.clicks)}</td></tr>`
  );

  const byPage = gsc.queries_by_page;
  const byPagePanel = document.getElementById("seoQueriesByPagePanel");
  if (byPagePanel) byPagePanel.hidden = !(byPage && byPage.length);
  if (byPage && byPage.length) {
    renderTable(
      "seoQueriesByPageTable",
      byPage,
      (p) => {
        const path = String(p.page || "").replace(/^https?:\/\/[^/]+/, "") || "/";
        const queries = (p.queries || [])
          .slice(0, 5)
          .map((q) => `<li>${escapeHtml(q.query)} <span class="muted-inline">${number(q.clicks)} clicks · pos. ${q.position ?? "-"}</span></li>`)
          .join("");
        return `<tr><td><a href="${escAttr(p.page)}" target="_blank" rel="noopener">${escapeHtml(path)}</a></td><td>${number(p.clicks)}</td><td>${number(p.impressions)}</td><td>${queries ? `<ul class="query-list">${queries}</ul>` : "-"}</td></tr>`;
      }
    );
  }

  const onpage = gsc.onpage_audit;
  document.getElementById("onpageEmpty").style.display = onpage.available ? "none" : "block";
  if (onpage.available) {
    renderCards("onpageCards", [
      { label: "Pages checked", value: number(onpage.total_checked) },
      { label: "Healthy", value: number(onpage.healthy_count) },
      { label: "Need attention", value: number(onpage.pages.length) },
    ]);
  } else {
    document.getElementById("onpageCards").innerHTML = "";
  }
  renderTable(
    "onpageTable",
    onpage.pages || [],
    (p) => `<tr><td><a href="${p.url}" target="_blank" rel="noopener">${p.url.replace(/^https?:\/\/[^/]+/, "")}</a></td><td>${p.findings.join("; ")}</td></tr>`,
    "Every checked page passes the on-page basics."
  );

  const pagespeed = gsc.pagespeed;
  document.getElementById("pagespeedEmpty").style.display = pagespeed?.available ? "none" : "block";
  if (pagespeed?.available) {
    renderCards("pagespeedCards", [
      { label: "Avg. performance (mobile)", value: pagespeed.avg_performance_score },
      { label: "Pages checked", value: number(pagespeed.pages.length) },
    ]);
  } else {
    document.getElementById("pagespeedCards").innerHTML = "";
  }
  const scoreClass = (s) => (s == null ? "" : s >= 90 ? "delta-good" : s >= 50 ? "delta-flat" : "delta-bad");
  renderTable(
    "pagespeedTable",
    pagespeed?.pages || [],
    (p) => `<tr><td><a href="${p.url}" target="_blank" rel="noopener">${p.url.replace(/^https?:\/\/[^/]+/, "") || "/"}</a></td><td><span class="delta ${scoreClass(p.performance_score)}">${p.performance_score ?? "-"}</span></td><td>${p.lcp_ms ? (p.lcp_ms / 1000).toFixed(1) + "s" : "-"}</td><td>${p.cls != null ? p.cls.toFixed(3) : "-"}</td><td>${p.accessibility_score ?? "-"}</td><td>${p.top_opportunities?.[0] ? `${p.top_opportunities[0].title} (${Math.round(p.top_opportunities[0].savings_ms)}ms)` : "-"}</td></tr>`
  );

  renderTable(
    "indexCoverageTable",
    coverage.issues || [],
    (i) => `<tr><td><a href="${i.url}" target="_blank" rel="noopener">${i.url.replace(/^https?:\/\/[^/]+/, "")}</a></td><td>${i.coverage_state}</td><td>${humanizeIndexingState(i.indexing_state)}</td><td>${i.last_crawl_time ? fullDate(i.last_crawl_time.slice(0, 10)) : "-"}</td></tr>`,
    "Every checked URL is indexed cleanly - nothing needs attention."
  );
}

// Competitors tab, insight-first layout (2026-10-17). Data: app.py competitor_insights.
// Every status and takeaway kind carries an icon and a word, never color alone.
const CI_KINDS = {
  opportunity: { icon: "&#8599;", label: "Opportunity" },
  strength: { icon: "&#10003;", label: "Strength" },
  risk: { icon: "!", label: "Watch" },
  rival: { icon: "&#9873;", label: "Rival" },
  ctr: { icon: "&#9678;", label: "Clicks" },
  content: { icon: "&#9654;", label: "Content" },
};
const CI_STATUS_ICON = { leading: "&#10003;", page1: "&#9650;", page2: "&#8599;", behind: "&#10005;" };

function ciStatusChip(status, label) {
  return `<span class="ci-status status-${escAttr(status)}">${CI_STATUS_ICON[status] || ""} ${escapeHtml(label)}</span>`;
}

function ciPosition(pos) {
  if (pos === null || pos === undefined) return "-";
  const page = Math.ceil(pos / 10);
  return `${pos}<span class="ci-sub">page ${page}</span>`;
}

function renderCompetitorInsights() {
  const ci = dashboard.competitor_insights || { available: false };
  const box = document.getElementById("ciTakeaways");
  if (!box) return;
  document.getElementById("ciEmpty").style.display = ci.available ? "none" : "block";
  if (!ci.available) {
    box.innerHTML = "";
    ["ciTopicsTable", "ciScorecardTable", "ciQueriesTable"].forEach((id) => renderTable(id, [], () => "", "No ranking checks yet."));
    return;
  }

  const [gStart, gEnd] = ci.gsc_window || [];
  const checked = ci.checked_at ? fullDate(String(ci.checked_at).slice(0, 10)) : "";
  document.getElementById("ciTakeawaysNote").textContent =
    `(${ci.market_searches} market searches · Google ${gStart && gEnd ? dateRange(gStart, gEnd) : ""} · Brave checked ${checked})`;

  // Six cards fill two rows of three; app.py orders them by priority.
  box.innerHTML = (ci.takeaways || []).slice(0, 6)
    .map((t) => {
      const kind = CI_KINDS[t.kind] || { icon: "&#8226;", label: "Note" };
      return `<article class="ci-card ci-${escAttr(t.kind)}">
        <div class="ci-kind"><span class="ci-kind-icon" aria-hidden="true">${kind.icon}</span>${kind.label}</div>
        <h3>${escapeHtml(t.title)}</h3>
        <p>${escapeHtml(t.detail)}</p>
        <p class="ci-action"><strong>Next step:</strong> ${escapeHtml(t.action)}</p>
      </article>`;
    })
    .join("");

  document.getElementById("ciTopicsNote").textContent = "(market searches grouped by topic, most searched first)";
  renderTable("ciTopicsTable", ci.topics || [], (t) => {
    const source = t.status_source === "brave" ? `<span class="ci-sub">no Google data, Brave #${t.best_brave ?? "-"}</span>` : "";
    const split = t.brave_disagrees ? `<span class="ci-sub">Brave has DOMA at #${t.best_brave}</span>` : "";
    const queries = (t.queries || []).slice(0, 4).map(escapeHtml).join(" · ") + ((t.queries || []).length > 4 ? ` · +${t.queries.length - 4} more` : "");
    return `<tr>
      <td><strong>${escapeHtml(t.topic)}</strong><span class="ci-sub">${queries}</span></td>
      <td>${ciStatusChip(t.status, t.status_label)}${source}</td>
      <td>${ciPosition(t.google_position)}${split}</td>
      <td>${number(t.impressions)} seen<span class="ci-sub">${number(t.clicks)} clicks${t.ctr !== null && t.ctr !== undefined ? ` (${t.ctr}%)` : ""}</span></td>
      <td>${(t.owners || []).map(escapeHtml).join("<br>") || "-"}</td>
      <td class="ci-next">${escapeHtml(t.action)}</td>
    </tr>`;
  });

  renderTable("ciScorecardTable", ci.scorecard || [], (c) => {
    const speed = c.pagespeed === null || c.pagespeed === undefined ? "-"
      : `${c.pagespeed}${ci.doma_pagespeed ? `<span class="ci-sub">DOMA ${ci.doma_pagespeed}</span>` : ""}`;
    const pod = c.podcast_last_30d ? `${c.podcast_last_30d} episode${c.podcast_last_30d === 1 ? "" : "s"}`
      : c.podcast_last ? `none<span class="ci-sub">last ${fullDate(c.podcast_last)}</span>` : "no podcast";
    return `<tr>
      <td><strong>${escapeHtml(c.name)}</strong><span class="ci-sub">${escapeHtml(c.domain)}</span></td>
      <td>${c.ahead_of_doma ? `${c.ahead_of_doma} of ${ci.market_searches} searches` : "none"}<span class="ci-sub">in top 10 on ${c.on_our_searches}</span></td>
      <td>${number(c.pages)}</td>
      <td>${(c.sections || []).map((x) => escapeHtml(x.replace(/-/g, " "))).join(", ") || "-"}</td>
      <td>${pod}</td>
      <td>${speed}</td>
      <td>${(c.tools || []).map(escapeHtml).join(", ") || "-"}</td>
    </tr>`;
  });

  renderTable("ciQueriesTable", ci.queries || [], (q) => `<tr>
      <td>${escapeHtml(q.query)}</td>
      <td>${escapeHtml(q.topic)}</td>
      <td>${ciPosition(q.google_position)}</td>
      <td>${number(q.impressions)}</td>
      <td>${number(q.clicks)}</td>
      <td>${q.brave_position ?? "not in top 10"}</td>
      <td>${(q.ahead || []).slice(0, 3).map(escapeHtml).join(", ") || "-"}</td>
    </tr>`);
  const other = ci.other_searches || [];
  document.getElementById("ciOtherSearches").textContent = other.length
    ? `${other.length} of ${ci.tracked_searches} tracked searches are brand or sponsor checks, not market searches, and are left out above: ${other.map((q) => q.replace(/\s+-site:\S+/g, "")).join("; ")}.`
    : "";
}

function renderCompetitors() {
  renderCompetitorInsights();
  const ahrefs = dashboard.ahrefs || { competitors: { available: false, domains: [] }, issues: { available: false, issues: [] } };
  const competitors = ahrefs.competitors || { available: false, domains: [] };
  const issues = ahrefs.issues || { available: false, issues: [] };

  document.getElementById("ahrefsUnavailable").style.display = competitors.available || issues.available ? "none" : "block";
  document.getElementById("competitorsEmpty").style.display = competitors.available ? "none" : "block";
  document.getElementById("ahrefsIssuesEmpty").style.display = issues.available ? "none" : "block";

  renderTable(
    "competitorsTable",
    competitors.domains || [],
    (d) => `<tr><td>${d.role === "self" ? `<strong>${d.domain}</strong>` : d.domain}</td><td>${d.domain_rating ?? "-"}</td><td>${number(d.organic_traffic)}</td><td>${number(d.organic_keywords)}</td><td>${number(d.backlinks)}</td><td>${number(d.referring_domains)}</td></tr>`
  );

  const sevClass = (s) => (s === "Error" ? "delta-bad" : s === "Warning" ? "delta-flat" : "");
  renderTable(
    "ahrefsIssuesTable",
    issues.issues || [],
    (i) => `<tr><td>${i.issue}</td><td><span class="delta ${sevClass(i.severity)}">${i.severity}</span></td><td>${number(i.affected_pages)}</td><td>${i.change_vs_prev > 0 ? "+" + i.change_vs_prev : i.change_vs_prev}</td></tr>`
  );

  const serp = dashboard.serp_competitors || { available: false, competitors: [] };
  document.getElementById("serpCompetitorsEmpty").style.display = serp.available ? "none" : "block";
  renderTable(
    "serpCompetitorsTable",
    serp.competitors || [],
    (c) => `<tr><td>${c.domain}</td><td>${number(c.appearances)}</td><td>${c.avg_position ?? "-"}</td><td>${c.best_position ?? "-"}</td><td>${number(c.queries_beating_doma)}</td></tr>`
  );

  const content = dashboard.competitor_content || { available: false, recent_pages: [], totals: [] };
  document.getElementById("competitorContentEmpty").style.display = content.available ? "none" : "block";
  const trackedEl = document.getElementById("competitorTrackedList");
  const trackedNames = (content.totals || []).map((t) => t.competitor_name).filter(Boolean);
  if (trackedEl && trackedNames.length) {
    const list = trackedNames.length > 1 ? `${trackedNames.slice(0, -1).join(", ")} and ${trackedNames.at(-1)}` : trackedNames[0];
    trackedEl.textContent = `${trackedNames.length} competitor${trackedNames.length === 1 ? "" : "s"} (${list})`;
  }
  if (content.available) {
    renderCards(
      "competitorContentCards",
      (content.totals || []).map((t) => ({ label: t.competitor_name, value: number(t.total_pages_tracked), hint: "pages tracked" }))
    );
  } else {
    document.getElementById("competitorContentCards").innerHTML = "";
  }
  // New pages (first time a URL shows up in a competitor's sitemap) instead
  // of lastmod: Dental A Team re-stamps every podcast page daily, which
  // flooded the old "recently updated" list. Falls back to lastmod rows
  // until the backend sends new_pages.
  const hasNewPages = Array.isArray(content.new_pages);
  const contentTitle = document.getElementById("competitorContentTitle");
  if (contentTitle) {
    contentTitle.innerHTML = hasNewPages
      ? `New pages from competitors <span class="h2-note">(last 30 days, first time each URL appeared in their sitemap)</span>`
      : `Recently updated competitor pages <span class="h2-note">(by the competitor's own sitemap date, which some of them re-stamp daily)</span>`;
  }
  const dateHeader = document.getElementById("competitorContentDateHeader");
  if (dateHeader) dateHeader.textContent = hasNewPages ? "First seen" : "Updated";
  renderTable(
    "competitorContentTable",
    hasNewPages ? content.new_pages : content.recent_pages || [],
    (p) => {
      const when = hasNewPages ? p.first_seen_at : p.lastmod;
      return `<tr><td>${escapeHtml(p.competitor_name || "")}</td><td><a href="${escAttr(p.url)}" target="_blank" rel="noopener">${escapeHtml(p.title || titleFromUrl(p.url))}</a></td><td>${when ? fullDate(String(when).slice(0, 10)) : "-"}</td></tr>`;
    },
    hasNewPages ? "No new competitor pages in the last 30 days." : "No data for this period."
  );

  const speed = content.pagespeed;
  const speedPanel = document.getElementById("competitorSpeedPanel");
  if (speedPanel) speedPanel.hidden = !(speed && speed.length);
  if (speed && speed.length) {
    const scoreCls = (v) => (v == null ? "" : v >= 90 ? "delta-good" : v >= 50 ? "delta-flat" : "delta-bad");
    renderTable(
      "competitorSpeedTable",
      [...speed].sort((x, y) => (y.performance_score ?? -1) - (x.performance_score ?? -1)),
      (r) => `<tr><td>${escapeHtml(r.competitor_name || "")}</td><td><a href="${escAttr(r.url)}" target="_blank" rel="noopener">${escapeHtml(String(r.url || "").replace(/^https?:\/\/(www\.)?/, ""))}</a></td><td><span class="delta ${scoreCls(r.performance_score)}">${r.performance_score ?? "-"}</span></td><td>${r.lcp_ms ? (r.lcp_ms / 1000).toFixed(1) + "s" : "-"}</td><td>${r.checked_at ? fullDate(String(r.checked_at).slice(0, 10)) : "-"}</td></tr>`
    );
  }

  const podcasts = content.podcasts;
  const podcastPanel = document.getElementById("competitorPodcastPanel");
  if (podcastPanel) podcastPanel.hidden = !(podcasts && podcasts.length);
  if (podcasts && podcasts.length) {
    renderTable(
      "competitorPodcastTable",
      [...podcasts].sort((x, y) => String(y.published_at || "").localeCompare(String(x.published_at || ""))).slice(0, 30),
      (e) => `<tr><td>${escapeHtml(e.competitor_name || "")}</td><td>${e.url ? `<a href="${escAttr(e.url)}" target="_blank" rel="noopener">${escapeHtml(e.title || "(untitled)")}</a>` : escapeHtml(e.title || "(untitled)")}</td><td>${e.published_at ? fullDate(String(e.published_at).slice(0, 10)) : "-"}</td></tr>`
    );
  }

  const tech = dashboard.competitor_tech || { available: false, entries: [] };
  document.getElementById("competitorTechEmpty").style.display = tech.available ? "none" : "block";
  renderTable(
    "competitorTechTable",
    tech.entries || [],
    (t) => `<tr><td>${t.competitor_name}</td><td>${t.signals.length ? t.signals.join(", ") : "(no known signatures matched)"}</td></tr>`
  );

  const wayback = dashboard.competitor_wayback || { available: false, entries: [] };
  document.getElementById("competitorWaybackEmpty").style.display = wayback.available ? "none" : "block";
  renderTable(
    "competitorWaybackTable",
    wayback.entries || [],
    (w) => `<tr><td>${w.competitor_name}</td><td>${number(w.total_snapshots)}</td><td>${w.last_snapshot ? fullDate(`${w.last_snapshot.slice(0, 4)}-${w.last_snapshot.slice(4, 6)}-${w.last_snapshot.slice(6, 8)}`) : "-"}</td><td>${number(w.snapshots_last_90d)}</td></tr>`
  );

  const adSpy = dashboard.ad_spy || { available: false, entries: [], by_competitor: [] };
  document.getElementById("adSpyEmpty").style.display = adSpy.available ? "none" : "block";
  if (adSpy.available) {
    renderCards(
      "adSpyCards",
      (adSpy.by_competitor || []).map((c) => ({ label: c.competitor, value: number(c.count), hint: "ads logged" }))
    );
  } else {
    document.getElementById("adSpyCards").innerHTML = "";
  }
  renderTable(
    "adSpyTable",
    adSpy.entries || [],
    (e) => `<tr><td>${e.competitor}</td><td>${e.platform}</td><td>${e.date_found ? fullDate(e.date_found) : "-"}</td><td>${e.format || "-"}</td><td>${e.hook ? (e.link ? `<a href="${e.link}" target="_blank" rel="noopener">${e.hook}</a>` : e.hook) : "-"}</td><td>${e.offer || "-"}</td><td>${e.cta || "-"}</td><td>${e.strategic_hypothesis || "-"}</td></tr>`
  );
}

function renderBlog() {
  const ga4 = dashboard.ga4;
  const prev = dashboard.previous;
  // Same GA4 consent undercount the Overview warns about: the backend flags
  // it per range (undercount_overlap); the local date check is the fallback.
  const undercount = ga4.undercount_overlap ?? ga4SpansConsentChange();
  const unfairDelta = ga4ComparisonUnreliable() || (ga4.undercount_overlap === true && prev?.ga4?.undercount_overlap === false);
  const undercountHint = undercount ? warnNote(`GA4 undercounted ${dateRange(GA4_UNDERCOUNT_START, GA4_UNDERCOUNT_END)}, so this is low and the % change is hidden`) : "";
  const gaDelta = (curr, before) => (prev && !unfairDelta ? deltaBadge(curr, before) : "");
  renderCards("blogCards", [
    { label: "Sessions", value: number(ga4.sessions), delta: gaDelta(ga4.sessions, prev?.ga4?.sessions), hint: undercountHint },
    { label: "Active users", value: number(ga4.active_users), delta: gaDelta(ga4.active_users, prev?.ga4?.active_users) },
    { label: "New users", value: number(ga4.new_users), delta: gaDelta(ga4.new_users, prev?.ga4?.new_users) },
    { label: "Engaged sessions", value: number(ga4.engaged_sessions), delta: gaDelta(ga4.engaged_sessions, prev?.ga4?.engaged_sessions), hint: undercountHint },
  ]);

  if (!ga4.available) {
    chartEmpty("blogChart", "No GA4 data yet. Run scripts/sync_ga4.py.");
  } else {
    svgLineChart("blogChart", [
      { label: "Sessions", points: ga4.daily.map((d) => ({ date: d.report_date, value: d.sessions })) },
    ]);
  }

  svgDonutChart("blogChannelsDonut", ga4.channels, { labelKey: "channel_group", valueKey: "sessions", centerLabel: "Sessions" });
  renderTable("blogChannelsTable", ga4.channels, (c) => `<tr><td>${c.channel_group}</td><td>${number(c.sessions)}</td></tr>`);
  renderTable(
    "blogPagesTable",
    ga4.top_pages,
    (p) =>
      `<tr title="${number(p.page_views)} views, ${duration(p.avg_engagement_seconds)} avg. time on page"><td>${p.page_path === "/" ? "Home" : escapeHtml(p.page_title || p.page_path)}</td><td>${number(p.sessions)}</td><td>${percent(p.bounce_rate)}</td></tr>`
  );
  renderTable(
    "blogCountriesTable",
    ga4.countries,
    (c) => `<tr><td>${c.country}</td><td>${number(c.active_users)}</td><td>${number(c.sessions)}</td></tr>`
  );

  const deviceTotal = (ga4.devices || []).reduce((sum, d) => sum + d.sessions, 0);
  const ga4Follows = breakdownFollowsRange(ga4.devices, ga4.sessions, "sessions");
  ["blogCountriesNote", "blogDevicesNote"].forEach((id) => {
    const note = document.getElementById(id);
    if (note) note.hidden = ga4Follows;
  });
  const DEVICE_LABELS_GA = { desktop: "Desktop", mobile: "Mobile", tablet: "Tablet" };
  renderTable(
    "blogDeviceTable",
    ga4.devices,
    (d) => `<tr><td>${DEVICE_LABELS_GA[d.device] || d.device}</td><td>${number(d.active_users)}</td><td>${number(d.sessions)}</td><td>${deviceTotal ? percent((d.sessions / deviceTotal) * 100) : "-"}</td></tr>`
  );

  const GA_GENDER_LABELS = { female: "Female", male: "Male" };
  document.getElementById("blogDemographicsEmpty").style.display = ga4.demographics_available ? "none" : "block";
  const genderTotal = (ga4.demographics?.gender || []).reduce((sum, g) => sum + g.active_users, 0);
  const ageTotal = (ga4.demographics?.age || []).reduce((sum, a) => sum + a.active_users, 0);
  renderTable(
    "blogGenderTable",
    ga4.demographics?.gender || [],
    (g) => `<tr><td>${GA_GENDER_LABELS[g.value] || g.value}</td><td>${number(g.active_users)}</td><td>${genderTotal ? percent((g.active_users / genderTotal) * 100) : "-"}</td></tr>`
  );
  renderTable(
    "blogAgeTable",
    ga4.demographics?.age || [],
    (a) => `<tr><td>${a.value}</td><td>${number(a.active_users)}</td><td>${ageTotal ? percent((a.active_users / ageTotal) * 100) : "-"}</td></tr>`
  );

  const content = dashboard.content;
  document.getElementById("recentPostsEmpty").style.display = content?.available ? "none" : "block";
  renderTable(
    "recentPostsTable",
    content?.posts || [],
    // Post title (falls back to a readable version of the URL slug) instead
    // of the raw URL path, and down to the 4 numbers that actually answer
    // "is this post doing anything" - views/time/impressions/position moved
    // into the row's title tooltip instead of taking up 4 more columns.
    (p) => {
      const detail = `${number(p.page_views)} views, ${duration(p.avg_engagement_seconds)} avg. time, ${number(p.impressions)} impressions, position ${p.position || "-"}`;
      // sessions/clicks null = no data for that post yet (shown as "-"),
      // a real 0 stays 0.
      return `<tr title="${escAttr(detail)}"><td><a href="${escAttr(p.url)}" target="_blank" rel="noopener">${escapeHtml(p.title || titleFromUrl(p.url))}</a></td><td>${fullDate((p.published_at || "").slice(0, 10))}</td><td>${number(p.sessions)}</td><td>${number(p.clicks)}</td></tr>`;
    }
  );

  // GA4 "AI Assistant" channel (ChatGPT, Perplexity, Gemini...) per day.
  const aiDaily = ga4.ai_assistant_daily;
  const hasEmail = ga4.email_sessions !== undefined && ga4.email_sessions !== null;
  const extrasPanel = document.getElementById("blogExtrasGrid");
  if (extrasPanel) extrasPanel.hidden = !(Array.isArray(aiDaily) || hasEmail);
  const aiTotal = (aiDaily || []).reduce((sum, d) => sum + (Number(d.sessions) || 0), 0);
  const aiTotalEl = document.getElementById("blogAiTotal");
  if (aiTotalEl) aiTotalEl.textContent = Array.isArray(aiDaily) ? `${number(aiTotal)} sessions in this range` : "";
  if (Array.isArray(aiDaily) && aiDaily.length > 1) {
    svgLineChart("blogAiChart", [{ label: "AI assistant sessions", cls: "c", points: aiDaily.map((d) => ({ date: d.report_date, value: d.sessions })) }], { height: 200, width: 520 });
  } else {
    chartEmpty("blogAiChart", "No AI assistant sessions recorded in this range.");
  }
  const emailEl = document.getElementById("blogEmailSessions");
  if (emailEl) {
    emailEl.innerHTML = hasEmail
      ? `<div class="community-stat"><div class="community-stat-value">${number(ga4.email_sessions)}</div><div class="community-stat-label">sessions from email</div></div>
         <p class="panel-intro">GHL emails go out without UTM tags, so most clicks from them land in Direct or Unassigned. This number only counts the links that do carry a tag.</p>`
      : `<div class="empty">Email sessions appear after the next GA4 sync.</div>`;
  }

  const conv = ga4.conversions_by_page;
  const convPanel = document.getElementById("blogConversionsPanel");
  if (convPanel) convPanel.hidden = !Array.isArray(conv);
  if (Array.isArray(conv)) {
    renderTable(
      "blogConversionsTable",
      conv,
      (r) => `<tr><td>${r.page_path === "/" ? "Home" : escapeHtml(r.title || r.page_path || "")}</td><td>${number(r.sessions)}</td><td>${number(r.key_events)}</td><td>${r.sessions ? percent((Number(r.key_events) / Number(r.sessions)) * 100) : "-"}</td></tr>`,
      "No key events recorded in this range."
    );
  }
}

let communityListenersAttached = false;

function ensureCommunityListeners() {
  if (communityListenersAttached) return;
  communityListenersAttached = true;
  document.getElementById("communityEditBtn")?.addEventListener("click", async () => {
    const current = document.getElementById("communityMemberCount").textContent;
    const input = prompt("Update DOMA Free Community member count (read from the GHL Communities panel):", current === "-" ? "" : current);
    if (input === null) return;
    const count = parseInt(String(input).replace(/[,.\s]/g, ""), 10); // prefill is formatted ("35,000")
    if (Number.isNaN(count) || count < 0) {
      alert("Enter a whole number.");
      return;
    }
    try {
      if (!window.domaCommunityStats) throw new Error("Firestore sync not ready yet");
      await window.domaCommunityStats.setMemberCount(count);
    } catch (error) {
      console.error("Failed to update community member count:", error);
      alert("Could not save - check the browser console for details.");
    }
  });

  whenFirestoreReady(() => {
    window.domaCommunityStats.subscribe((data) => {
      const countEl = document.getElementById("communityMemberCount");
      const updatedEl = document.getElementById("communityUpdatedAt");
      if (!countEl) return;
      countEl.textContent = data ? number(data.member_count) : "-";
      if (!data?.updated_at) {
        updatedEl.textContent = "Not set yet";
        return;
      }
      const updated = new Date(data.updated_at);
      const age = daysBetween(updated.toISOString(), new Date().toISOString());
      updatedEl.innerHTML =
        `Updated ${fullDate(updated.toISOString().slice(0, 10))}` +
        (age !== null && age > 30 ? ` ${warnNote(`count is ${age} days old, update it from GHL Communities`)}` : "");
    });
  });
}

function renderLeads() {
  ensureCommunityListeners();
  const ghl = dashboard.ghl;
  const prev = dashboard.previous;
  const bySource = ghl.by_source || [];
  // Raw GHL source names are messy ("02. Partner With Us!", "payment_link",
  // one per ebook form), so the backend groups them (Starter Kit, Ebooks,
  // Partner, Social DM...). Falls back to raw sources until by_group exists.
  const byGroup = Array.isArray(ghl.by_group) && ghl.by_group.length ? ghl.by_group : null;
  const topRow = byGroup ? byGroup[0] : bySource[0];
  renderCards("leadsCards", [
    { label: "New leads", value: number(ghl.total_leads), delta: prev && prevCovered("leads") ? deltaBadge(ghl.total_leads, prev.ghl.total_leads) : "", hint: dateRange(dashboard.start_date, dashboard.end_date) },
    { label: byGroup ? "Source groups" : "Active sources", value: number(byGroup ? byGroup.length : bySource.length), hint: byGroup ? `${number(bySource.length)} raw sources` : "" },
    {
      label: "Top source share",
      value: topRow && ghl.total_leads ? percent((topRow.lead_count / ghl.total_leads) * 100) : "-",
      hint: topRow ? escapeHtml(byGroup ? topRow.group : topRow.source) : "",
    },
  ]);

  if (!ghl.available) {
    chartEmpty("leadsChart", "No GoHighLevel data yet. Run scripts/sync_ghl.py.");
  } else {
    svgLineChart("leadsChart", [
      { label: "New leads", points: ghl.daily.map((d) => ({ date: d.report_date, value: d.lead_count })) },
    ]);
  }

  const sourceTitle = document.getElementById("leadsSourceTitle");
  if (sourceTitle) sourceTitle.textContent = byGroup ? "Leads by source group" : "Leads by source";
  svgHBarChart("leadsSourceBars", byGroup || bySource, { labelKey: byGroup ? "group" : "source", valueKey: "lead_count", maxBars: byGroup ? 10 : 6 });
  renderTable(
    "leadsSourceTable",
    [...bySource].sort((x, y) => String(x.group || "").localeCompare(String(y.group || "")) || y.lead_count - x.lead_count),
    (s) => `<tr><td>${escapeHtml(s.source || "")}</td><td>${escapeHtml(s.group || "-")}</td><td>${number(s.lead_count)}</td><td>${ghl.total_leads ? percent((s.lead_count / ghl.total_leads) * 100) : "-"}</td></tr>`
  );

  renderEbookRanking(bySource, prev?.ghl?.by_source || []);
  renderOrders(ghl, prev);
  renderLeadFunnels(ghl);
}

// Funnel rule from the motion guide: every later stage must be <= the one
// above it (same cohort), otherwise the funnel is withheld instead of clamped.
function renderFunnel(containerId, stages, note, emptyMessage) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const ok = stages.length >= 2 && stages.every((s) => Number.isFinite(s.value)) && stages[0].value > 0
    && stages.every((s, i) => i === 0 || s.value <= stages[i - 1].value);
  if (!ok) {
    el.innerHTML = `<div class="empty">${escapeHtml(emptyMessage)}</div>`;
    return;
  }
  const first = stages[0].value;
  const rows = stages
    .map((s, i) => {
      const width = Math.max(2, (s.value / first) * 100);
      const prevValue = i === 0 ? 0 : stages[i - 1].value;
      const step = i === 0 ? "" : prevValue > 0 ? `${percent((s.value / prevValue) * 100)} of ${escapeHtml(stages[i - 1].label.toLowerCase())}` : "-";
      return `<div class="funnel-row"><div class="funnel-head"><span class="funnel-label">${escapeHtml(s.label)}</span><span class="funnel-value">${number(s.value)}</span></div><div class="hbar-track" title="${escAttr(s.label + ": " + number(s.value))}"><span class="funnel-fill" style="width:${width}%"></span></div><div class="funnel-step">${step}</div></div>`;
    })
    .join("");
  el.innerHTML = rows + (note ? `<div class="funnel-note">${note}</div>` : "");
}

function renderLeadFunnels(ghl) {
  const gf = ghl.guide_funnel || {};
  renderFunnel(
    "guideFunnel",
    [
      { label: "Sessions that started on a guide page", value: gf.sessions || 0 },
      { label: "Guide form leads", value: gf.leads || 0 },
    ],
    "GA4 landing sessions on free-guide pages and GHL leads from guide forms in this range. A lead can come from a session that started elsewhere, so read the rate as approximate."
      + (ga4UndercountOverlaps() ? " GA4 undercounted Sep 11 - Oct 04, 2026, so sessions in this range are low and the rate is too high." : ""),
    gf.reason || "No guide data in this range."
  );
  const latest = (ghl.email_campaigns || []).find((c) => (c.recipients || 0) > 0);
  if (!latest) {
    renderFunnel("emailFunnel", [], "", "No email campaign in this range.");
    return;
  }
  renderFunnel(
    "emailFunnel",
    [
      { label: "Recipients", value: latest.recipients ?? NaN },
      { label: "Delivered", value: latest.delivered ?? NaN },
      { label: "Opened", value: latest.opens ?? NaN },
      { label: "Clicked", value: latest.clicks ?? NaN },
    ],
    `${escapeHtml(latest.campaign_name || "Campaign")}${latest.sent_at ? ", sent " + escapeHtml(latest.sent_at) : ""}. Opens are what GHL reports (Apple Mail privacy can inflate them).`,
    "This campaign is missing a count or its counts do not narrow stage by stage, so the funnel is hidden."
  );
}

// Free guides ranked by the leads their GHL form brought in. Form names come
// in a few spellings ("Ebook - X", "Ebook -X", hyphenated slugs), so they're
// cleaned up for display and matched to the previous period by that name.
function ebookName(source) {
  return String(source || "")
    .replace(/^ebook\s*-\s*/i, "")
    .replace(/(\w)-(?=\w)/g, "$1 ")
    .trim();
}

function renderEbookRanking(bySource, prevBySource) {
  const isEbook = (s) => s.group === "Ebooks" || /^ebook\b/i.test(s.source || "");
  const prevByName = new Map(prevBySource.filter(isEbook).map((s) => [ebookName(s.source), s.lead_count]));
  const rows = bySource.filter(isEbook).map((s) => ({ name: ebookName(s.source), leads: s.lead_count, prev: prevByName.get(ebookName(s.source)) ?? 0 }));
  rows.sort((x, y) => y.leads - x.leads);
  const total = rows.reduce((sum, r) => sum + r.leads, 0);
  renderTable(
    "leadsEbookRankTable",
    rows,
    (r, i) => `<tr><td>${i + 1}</td><td>${escapeHtml(r.name)}</td><td>${number(r.leads)}</td><td>${total ? percent((r.leads / total) * 100) : "-"}</td><td>${number(r.prev)}</td></tr>`,
    "No ebook leads in this period."
  );
}

// GHL orders/memberships (payments). Aggregates only, never customer names.
function renderOrders(ghl, prev) {
  const section = document.getElementById("leadsOrdersSection");
  if (!section) return;
  const orders = ghl.orders;
  const empty = document.getElementById("ordersEmpty");
  const body = document.getElementById("ordersBody");
  if (!orders || orders.available === false) {
    if (empty) empty.style.display = "block";
    if (body) body.hidden = true;
    return;
  }
  if (empty) empty.style.display = "none";
  if (body) body.hidden = false;

  const prevOrders = prev?.ghl?.orders;
  const leadToOrder = ghl.total_leads ? (orders.total_orders / ghl.total_leads) * 100 : null;
  const pending = Number(orders.pending) || 0;
  renderCards("ordersCards", [
    {
      label: "Orders",
      value: number(orders.total_orders),
      delta: prevOrders && prevCovered("orders") ? deltaBadge(orders.total_orders, prevOrders.total_orders) : "",
      hint: `${number(orders.completed)} completed${leadToOrder !== null ? ` · ${percent(leadToOrder)} of new leads` : ""}`,
    },
    {
      label: "Revenue",
      value: money(orders.revenue),
      delta: prevOrders && prevCovered("orders") ? deltaBadge(orders.revenue, prevOrders.revenue) : "",
      hint: orders.refunded ? `${money(orders.refunded)} refunded` : "",
    },
    { label: "Active subscriptions", value: number(orders.active_subscriptions) },
    { label: "MRR", value: money(orders.mrr), hint: "monthly recurring revenue" },
    {
      label: "Pending",
      value: number(pending),
      hint: pending > 0 ? warnNote("pending orders usually mean a failed charge, check GHL Payments") : "no failed charges",
    },
  ]);

  const daily = orders.daily || [];
  if (daily.length > 1) {
    svgLineChart("ordersChart", [{ label: "Orders", points: daily.map((d) => ({ date: d.report_date, value: d.orders })) }], { height: 180 });
  } else {
    chartEmpty("ordersChart", "Not enough order history in this range for a chart.");
  }
  renderTable(
    "ordersProductTable",
    orders.by_product || [],
    (r) => `<tr><td>${escapeHtml(r.product || "(no product)")}</td><td>${number(r.orders)}</td><td>${money(r.revenue)}</td></tr>`,
    "No orders in this range."
  );
  renderTable(
    "ordersSourceTable",
    orders.by_lead_source || [],
    (r) => `<tr><td>${escapeHtml(r.source_group || "(unknown)")}</td><td>${number(r.orders)}</td></tr>`,
    "No orders in this range."
  );
}

function renderSocial() {
  const social = dashboard.social;
  const prev = dashboard.previous;
  const totalEngagement = social.posts.reduce((sum, p) => sum + p.engagement_total, 0);
  // Engagement per reach (posts that report reach only), a fairer rate than
  // engagement over total followers.
  const reachedPosts = social.posts.filter((p) => Number(p.reach) > 0);
  const reachedEngagement = reachedPosts.reduce((sum, p) => sum + (Number(p.engagement_total) || 0), 0);
  const reachedTotal = reachedPosts.reduce((sum, p) => sum + Number(p.reach), 0);
  const engagementRate = reachedTotal ? (reachedEngagement / reachedTotal) * 100 : null;

  // Daily reach summed over the range (people reached each day, so the same
  // person on two days counts twice). Facebook reach is page_total_media_view_unique,
  // Meta's replacement for page_impressions_unique.
  const sumMetric = (data, platform, metric) =>
    (data?.metrics_daily || []).filter((m) => m.platform === platform && m.metric === metric).reduce((sum, m) => sum + m.value, 0);
  const fbReach = sumMetric(social, "facebook", "page_total_media_view_unique");
  const igReach = sumMetric(social, "instagram", "ig_reach");

  renderCards("socialCards", [
    {
      label: "Facebook reach",
      value: number(fbReach),
      delta: prev && prevCovered("fb_reach") ? deltaBadge(fbReach, sumMetric(prev.social, "facebook", "page_total_media_view_unique")) : "",
      hint: "daily reach, summed",
    },
    {
      label: "Instagram reach",
      value: number(igReach),
      delta: prev && prevCovered("ig_reach") ? deltaBadge(igReach, sumMetric(prev.social, "instagram", "ig_reach")) : "",
      hint: "daily reach, summed",
    },
    {
      label: "Facebook followers",
      value: number(social.followers.facebook),
      delta: prev ? deltaBadge(social.followers.facebook, prev.social.followers.facebook) : "",
    },
    {
      label: "Instagram followers",
      value: number(social.followers.instagram),
      delta: prev ? deltaBadge(social.followers.instagram, prev.social.followers.instagram) : "",
    },
    { label: "Posts tracked", value: number(social.posts.length), hint: "last 20 per platform" },
    {
      label: "Total engagement",
      value: number(totalEngagement),
      hint: engagementRate !== null ? `${percent(engagementRate)} of post reach (${number(reachedPosts.length)} posts with reach data)` : "likes + comments + shares",
    },
  ]);

  // Two small charts instead of one shared axis: Facebook (~7.7k) and
  // Instagram (~1.2k) on one 0-based scale both read as flat lines.
  const followerCharts = [
    { id: "socialFollowersFbChart", platform: "facebook", label: "Facebook followers", cls: "a" },
    { id: "socialFollowersIgChart", platform: "instagram", label: "Instagram followers", cls: "b" },
  ];
  followerCharts.forEach((c) => {
    if (!social.available) {
      chartEmpty(c.id, "No Facebook/Instagram data yet. Run scripts/sync_meta_organic.py.");
      return;
    }
    const points = (social.followers_daily || [])
      .filter((d) => d.platform === c.platform && d.follower_count !== null && d.follower_count !== undefined)
      .sort((x, y) => (x.report_date < y.report_date ? -1 : 1))
      .map((d) => ({ date: d.report_date, value: d.follower_count }));
    if (points.length < 2) {
      chartEmpty(c.id, "Follower history builds up day by day - check back after a few daily syncs.");
      return;
    }
    const change = points[points.length - 1].value - points[0].value;
    const changeEl = document.getElementById(`${c.id}Change`);
    if (changeEl) changeEl.textContent = `${change > 0 ? "+" : ""}${number(change)} since ${fullDate(points[0].date)}`;
    svgLineChart(c.id, [{ label: c.label, cls: c.cls, points }], { zeroBased: false, height: 220, width: 520 });
  });

  // Same split as the follower charts: Facebook reach (~1k/day) would flatten
  // Instagram (~50/day) on a shared axis.
  [
    { id: "socialReachFbChart", label: "Facebook reach", metric: "page_total_media_view_unique", platform: "facebook", cls: "a" },
    { id: "socialReachIgChart", label: "Instagram reach", metric: "ig_reach", platform: "instagram", cls: "b" },
  ].forEach((r) => {
    const points = (social.metrics_daily || [])
      .filter((m) => m.platform === r.platform && m.metric === r.metric)
      .sort((x, y) => (x.report_date < y.report_date ? -1 : 1))
      .map((m) => ({ date: m.report_date, value: m.value }));
    if (points.length > 1) {
      svgLineChart(r.id, [{ label: r.label, cls: r.cls, points }], { height: 220, width: 520 });
    } else {
      chartEmpty(r.id, "Reach history builds up after the daily Meta sync.");
    }
  });

  renderTable(
    "socialPostsTable",
    social.posts,
    (p) => `<tr><td>${p.platform === "facebook" ? "Facebook" : "Instagram"}</td><td>${p.permalink ? `<a href="${p.permalink}" target="_blank" rel="noopener">${(p.caption || "(no caption)").slice(0, 60)}</a>` : (p.caption || "(no caption)").slice(0, 60)}</td><td>${fullDate((p.published_at || "").slice(0, 10))}</td><td>${p.reach ? number(p.reach) : "-"}</td><td>${number(p.likes)}</td><td>${number(p.comments)}</td><td>${number(p.shares)}</td><td>${p.reach ? percent(((Number(p.engagement_total) || 0) / p.reach) * 100) : "-"}</td></tr>`
  );

  const GENDER_LABELS = { F: "Female", M: "Male", U: "Not specified" };
  const igGender = social.audience?.instagram?.gender || [];
  const igCountry = social.audience?.instagram?.country || [];
  const genderTotal = igGender.reduce((sum, g) => sum + g.follower_count, 0);
  const countryTotal = igCountry.reduce((sum, c) => sum + c.follower_count, 0);

  document.getElementById("socialGenderEmpty").style.display = igGender.length ? "none" : "block";
  svgDonutChart(
    "socialGenderDonut",
    igGender.map((g) => ({ label: GENDER_LABELS[g.value] || g.value, follower_count: g.follower_count })),
    { labelKey: "label", valueKey: "follower_count", centerLabel: "Followers" }
  );
  renderTable(
    "socialGenderTable",
    igGender,
    (g) => `<tr><td>${GENDER_LABELS[g.value] || g.value}</td><td>${number(g.follower_count)}</td><td>${genderTotal ? percent((g.follower_count / genderTotal) * 100) : "-"}</td></tr>`
  );
  svgHBarChart("socialCountryBars", igCountry, { labelKey: "value", valueKey: "follower_count", maxBars: 8 });
  renderTable(
    "socialCountryTable",
    igCountry.slice(0, 15),
    (c) => `<tr><td>${c.value}</td><td>${number(c.follower_count)}</td><td>${countryTotal ? percent((c.follower_count / countryTotal) * 100) : "-"}</td></tr>`
  );

  const bestTimes = social.best_times || { available: false, by_day: [], by_period: [] };
  document.getElementById("socialBestTimesEmpty").style.display = bestTimes.available ? "none" : "block";
  renderTable(
    "socialBestDayTable",
    bestTimes.by_day || [],
    (d) => `<tr><td>${d.day}</td><td>${d.avg_engagement}</td><td>${d.post_count}</td></tr>`
  );
  renderTable(
    "socialBestPeriodTable",
    bestTimes.by_period || [],
    (p) => `<tr><td>${p.period}</td><td>${p.avg_engagement}</td><td>${p.post_count}</td></tr>`
  );
}

let teamFilters = { owner: "all", topic: "all", date: "all", status: "all" };
// Collapse state persists across re-renders (status clicks re-render the
// whole tab) so opening/closing a status section doesn't reset on every
// click - keyed by "meetingDate|status", collapsed = true.
let teamCollapsed = {};
// Same idea, one level up: collapses a whole meeting panel (keyed by
// meeting_date) so scrolling past old meetings to reach a recent one isn't
// necessary. Starts empty - every panel begins expanded, same as before.
let teamPanelCollapsed = {};
let teamListenersAttached = false;
let teamAddFormAttached = false;
// Firestore is authoritative for status once a doc exists (itemId -> status);
// manually-added tickets live entirely in Firestore, keyed by their own doc
// id. Both are merged onto the SQLite/data.js-baked meetings fresh on every
// render - never mutated onto the baked objects - so a live update can't
// accumulate duplicates across repeated onSnapshot-triggered re-renders.
let teamLiveStatuses = new Map();
let teamManualItems = [];
// itemId -> {owner, topic, description, context} field overrides for
// meeting-derived tickets, saved via updateActionItem() and merged onto the
// baked item at render time - same live-merge pattern as status.
let teamLiveOverrides = new Map();
// Populated fresh every renderTeam() call from the current merged item set,
// so the edit-button click handler can look up either a manual or a
// meeting-derived item by id without keeping two separate lookups in sync.
let teamAllItemsById = new Map();
// Same pattern for the To Do board (2026-09-14: Lucas asked for meeting
// tickets to show up here too, not just on Team & Meetings) - populated
// fresh every renderTodoBoard() call, covers both manual and meeting-
// derived items so the click handlers can look either up by id.
let todoAllItemsById = new Map();

const TEAM_STATUS_ORDER = ["open", "in_progress", "done"];
// Always offered as owner options (filter pills + the add-ticket datalist)
// even before anyone has a ticket assigned to them yet - e.g. Michelle
// starting to log tickets shouldn't require her first ticket to exist
// before her name is selectable.
const TEAM_KNOWN_OWNERS = ["Kyle", "Juli", "Lucas", "Michelle"];
const TEAM_STATUS_LABELS = { open: "To do", in_progress: "In progress", done: "Completed" };

function teamEffectiveStatus(item) {
  const live = teamLiveStatuses.get(String(item.id));
  return (live && live.status) || item.status || "open";
}

// When a ticket was last marked done (ms epoch), or null if it was never
// toggled through Firestore (e.g. still showing its baked default status).
function teamStatusUpdatedAt(item) {
  const live = teamLiveStatuses.get(String(item.id));
  return (live && live.updated_at) || null;
}

// Team & Meetings shows meeting-derived tickets ONLY - manually-added ones
// live entirely on the To Do board now (2026-08-31: Lucas asked for the two
// to stop crossing over). Field overrides (from editing a meeting ticket's
// text) still merge onto the baked action items here, same as before.
function teamMergedMeetings(team) {
  return team.meetings
    .map((m) => ({
      ...m,
      action_items: (m.action_items || [])
        .map((item) => {
          const override = teamLiveOverrides.get(String(item.id));
          return { ...item, ...(override || {}) };
        })
        .filter((item) => !item.deleted),
    }))
    .sort((a, b) => (a.meeting_date < b.meeting_date ? 1 : a.meeting_date > b.meeting_date ? -1 : 0));
}

function buildFilterPills(containerId, values, labelFn, onSelect) {
  const el = document.getElementById(containerId);
  el.innerHTML = ["all", ...values]
    .map((v) => `<button type="button" data-value="${v}">${v === "all" ? "All" : labelFn ? labelFn(v) : v}</button>`)
    .join("");
  el.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => onSelect(b.dataset.value)));
}

function buildTeamFilterPills(containerId, key, values, labelFn) {
  buildFilterPills(containerId, values, labelFn, (value) => {
    teamFilters[key] = value;
    applyTeamFilters();
  });
}

function applyTeamFilters() {
  document.querySelectorAll("#teamMeetings .meeting-panel").forEach((panel) => {
    if (teamFilters.date !== "all" && panel.dataset.date !== teamFilters.date) {
      panel.style.display = "none";
      return;
    }
    panel.style.display = "";

    let anySectionVisible = false;
    panel.querySelectorAll(".status-section").forEach((section) => {
      let visibleCount = 0;
      section.querySelectorAll(".checklist-item").forEach((item) => {
        const ownerMatch = teamFilters.owner === "all" || item.dataset.owner === teamFilters.owner;
        const topicMatch = teamFilters.topic === "all" || item.dataset.topic === teamFilters.topic;
        const statusMatch = teamFilters.status === "all" || item.dataset.status === teamFilters.status;
        const visible = ownerMatch && topicMatch && statusMatch;
        item.classList.toggle("hidden", !visible);
        if (visible) visibleCount += 1;
      });
      // The count badge reflects what's actually shown under the active
      // filters, not the section's full unfiltered size.
      const countEl = section.querySelector(".status-count");
      if (countEl) countEl.textContent = visibleCount;
      // A status section with every item filtered out disappears entirely
      // too, instead of showing an empty accordion tab.
      section.classList.toggle("hidden", visibleCount === 0);
      if (visibleCount > 0) anySectionVisible = true;
    });
    if (!anySectionVisible) panel.style.display = "none";
  });

  document.querySelectorAll("#teamOwnerFilter button").forEach((b) => b.classList.toggle("active", b.dataset.value === teamFilters.owner));
  document.querySelectorAll("#teamTopicFilter button").forEach((b) => b.classList.toggle("active", b.dataset.value === teamFilters.topic));
  document.querySelectorAll("#teamDateFilter button").forEach((b) => b.classList.toggle("active", b.dataset.value === teamFilters.date));
  document.querySelectorAll("#teamStatusFilter button").forEach((b) => b.classList.toggle("active", b.dataset.value === teamFilters.status));
}

function whenFirestoreReady(callback) {
  if (window.domaTeamSyncReady) return callback();
  window.addEventListener("doma-team-sync-ready", callback, { once: true });
}

async function setTeamActionItemStatus(itemId, status) {
  try {
    if (!window.domaTeamSync) throw new Error("Firestore sync not ready yet");
    await window.domaTeamSync.setStatus(itemId, status);
    return true;
  } catch (error) {
    console.error("Failed to update action item status:", error);
    return false;
  }
}

function ensureTeamListeners() {
  if (teamListenersAttached) return;
  teamListenersAttached = true;
  document.getElementById("teamMeetings").addEventListener("click", async (event) => {
    if (handleCommentsToggle(event)) return;

    const panelHeader = event.target.closest(".meeting-panel-header");
    if (panelHeader) {
      const panel = panelHeader.closest(".meeting-panel");
      const key = panel.dataset.date;
      teamPanelCollapsed[key] = !teamPanelCollapsed[key];
      panel.classList.toggle("collapsed", teamPanelCollapsed[key]);
      return;
    }

    const header = event.target.closest(".status-header");
    if (header) {
      const section = header.closest(".status-section");
      const key = section.dataset.statusKey;
      teamCollapsed[key] = !teamCollapsed[key];
      section.classList.toggle("collapsed", teamCollapsed[key]);
      return;
    }

    const editBtn = event.target.closest(".checklist-edit");
    if (editBtn) {
      const itemEl = editBtn.closest(".checklist-item");
      const item = teamAllItemsById.get(itemEl.dataset.id);
      if (item) openTeamEditForm(item);
      return;
    }

    const mark = event.target.closest(".checklist-mark");
    if (!mark || mark.disabled) return;
    const itemEl = mark.closest(".checklist-item");
    const itemId = itemEl.dataset.id;
    const next = TEAM_STATUS_ORDER[(TEAM_STATUS_ORDER.indexOf(itemEl.dataset.status) + 1) % TEAM_STATUS_ORDER.length];
    mark.disabled = true;
    // No optimistic local mutation needed: our own write lands in the same
    // onSnapshot stream everyone else's does (usually well under a second),
    // which re-renders via teamLiveStatuses. Just guard against a double
    // click while the write is in flight.
    const ok = await setTeamActionItemStatus(itemId, next);
    if (!ok) mark.disabled = false;
  });

  document.getElementById("teamMeetings").addEventListener("submit", async (event) => {
    const form = event.target.closest(".ticket-comment-form");
    if (!form) return;
    event.preventDefault();
    const comment = readCommentForm(form);
    if (!comment) return;
    const itemId = form.closest(".ticket-comments").dataset.id;
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    try {
      if (!window.domaTeamSync) throw new Error("Firestore sync not ready yet");
      await window.domaTeamSync.addActionItemComment(itemId, comment);
    } catch (error) {
      console.error("Failed to post comment:", error);
      alert("Could not post the comment - check the browser console for details.");
    } finally {
      submitBtn.disabled = false;
    }
  });

  // Live sync: any click or added ticket (from this tab, another tab, or a
  // different person entirely - local dashboard or the published site, same
  // Firestore project either way) shows up here within a second or two.
  whenFirestoreReady(() => {
    // One shared set of Firestore listeners drives Team & Meetings, the To
    // Do board, and Weekly Recap - status/manual-items/overrides all affect
    // at least one of the three.
    window.domaTeamSync.subscribeAll((liveStatuses) => {
      teamLiveStatuses = liveStatuses;
      safeRender("Team & Meetings", renderTeam);
      safeRender("To Do", renderTodoBoard);
      safeRender("Weekly Recap", renderWeeklyRecap);
    });
    window.domaTeamSync.subscribeManualItems((items) => {
      teamManualItems = items;
      safeRender("Team & Meetings", renderTeam);
      safeRender("To Do", renderTodoBoard);
    });
    window.domaTeamSync.subscribeOverrides((overrides) => {
      teamLiveOverrides = overrides;
      safeRender("Team & Meetings", renderTeam);
      safeRender("To Do", renderTodoBoard);
      safeRender("Weekly Recap", renderWeeklyRecap);
    });
  });
}

// Firestore doc id of the meeting-derived ticket currently being edited (a
// field override on its status doc), or null when the form is closed.
// Team & Meetings has no "add new" flow any more - all new tasks go on the
// To Do board - so this form only ever edits.
let editingTeamItemId = null;

function openTeamEditForm(item) {
  const form = document.getElementById("teamEditForm");
  if (!form) return;
  editingTeamItemId = item.id;
  form.querySelector('[name="owner"]').value = item.owner || "";
  form.querySelector('[name="topic"]').value = item.topic || "";
  form.querySelector('[name="description"]').value = item.description || "";
  form.querySelector('[name="context"]').value = item.context || "";
  form.classList.remove("hidden");
  form.scrollIntoView({ behavior: "smooth", block: "center" });
}

function closeTeamEditForm(form) {
  editingTeamItemId = null;
  form.reset();
  form.classList.add("hidden");
}

function ensureTeamEditForm() {
  if (teamAddFormAttached) return;
  teamAddFormAttached = true;
  const form = document.getElementById("teamEditForm");
  if (!form) return;

  document.getElementById("teamEditCancel")?.addEventListener("click", () => closeTeamEditForm(form));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!editingTeamItemId) return;
    const fd = new FormData(form);
    const owner = String(fd.get("owner") || "").trim();
    const description = String(fd.get("description") || "").trim();
    if (!owner || !description) return;
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    try {
      if (!window.domaTeamSync) throw new Error("Firestore sync not ready yet");
      await window.domaTeamSync.updateActionItem(editingTeamItemId, {
        owner,
        topic: String(fd.get("topic") || "").trim() || "General",
        description,
        context: String(fd.get("context") || "").trim() || null,
      });
      closeTeamEditForm(form);
    } catch (error) {
      console.error("Failed to save ticket:", error);
      alert("Could not save the ticket - check the browser console for details.");
    } finally {
      submitBtn.disabled = false;
    }
  });
}

function teamStatusIcon(status) {
  if (status === "done") return "✓";
  if (status === "in_progress") return "●";
  return "";
}

// Shared by Team & Meetings tickets and To Do cards - a "what was done"
// comment thread. Comments live as a plain array field on the item's own
// Firestore doc (see addActionItemComment / addManualItemComment), appended
// with arrayUnion so concurrent commenters can't clobber each other.
function commentsHtml(item) {
  const comments = item.comments || [];
  const list =
    comments
      .map(
        (c) =>
          `<div class="ticket-comment"><span class="ticket-comment-author">${escapeHtml(c.author || "Someone")}</span>${linkifyText(c.text)}<span class="ticket-comment-date">${fullDate(new Date(c.created_at).toISOString().slice(0, 10))}</span></div>`
      )
      .join("") || `<div class="ticket-comments-empty">No comments yet.</div>`;
  return `
    <div class="ticket-comments" data-id="${item.id}">
      <button type="button" class="ticket-comments-toggle">&#128172; ${comments.length ? comments.length : "Comment"}</button>
      <div class="ticket-comments-body hidden">
        <div class="ticket-comments-list">${list}</div>
        <form class="ticket-comment-form">
          <input type="text" name="author" placeholder="Your name" value="${(localStorage.getItem("domaCommenterName") || "").replace(/"/g, "&quot;")}" />
          <input type="text" name="text" placeholder="What did you do?" required />
          <button type="submit">Post</button>
        </form>
      </div>
    </div>`;
}

// Toggling a comment thread open, and remembering the commenter's name in
// localStorage so it doesn't need retyping every time, are identical for
// both Team tickets and To Do cards - shared here, wired into each tab's
// own click/submit handler since where the comment gets SAVED differs
// (STATUS_COLLECTION for meeting tickets, MANUAL_ITEMS_COLLECTION for
// manual ones).
function handleCommentsToggle(event) {
  const toggle = event.target.closest(".ticket-comments-toggle");
  if (!toggle) return false;
  toggle.closest(".ticket-comments").querySelector(".ticket-comments-body").classList.toggle("hidden");
  return true;
}

function readCommentForm(form) {
  const fd = new FormData(form);
  const author = String(fd.get("author") || "").trim();
  const text = String(fd.get("text") || "").trim();
  if (!text) return null;
  if (author) localStorage.setItem("domaCommenterName", author);
  return { author: author || null, text, created_at: Date.now() };
}

function teamChecklistItemHtml(item) {
  const status = teamEffectiveStatus(item);
  const editBtn = `<button type="button" class="checklist-edit" title="Edit">&#9998;</button>`;
  return `
    <div class="checklist-item status-${status}" data-id="${item.id}" data-owner="${item.owner}" data-topic="${item.topic || "General"}" data-status="${status}">
      <div class="checklist-row">
        <button type="button" class="checklist-mark" title="Click to change status">${teamStatusIcon(status)}</button>
        <span class="checklist-text">
          <span class="checklist-owner">${item.owner}</span><span class="checklist-topic">${item.topic || "General"}</span>${linkifyText(item.description)}${item.context ? `<span class="checklist-context">${linkifyText(item.context)}</span>` : ""}<span class="checklist-id">#${item.id}</span>
        </span>
        ${editBtn}
      </div>
      ${commentsHtml(item)}
    </div>`;
}

function renderTeam() {
  ensureTeamListeners();
  const team = dashboard.team_meetings || { available: false, meetings: [], open_by_owner: [], total_open: 0, total_in_progress: 0, total_done: 0, total_all: 0 };
  document.getElementById("teamEmpty").style.display = team.available ? "none" : "block";

  const mergedMeetings = teamMergedMeetings(team);
  const allItems = mergedMeetings.flatMap((m) => m.action_items || []);
  teamAllItemsById = new Map(allItems.map((i) => [String(i.id), i]));

  // Derived live from the items themselves (not the backend's total_* fields)
  // so a status click updates these cards instantly, without waiting for
  // the next full /api/dashboard reload.
  const countByStatus = { open: 0, in_progress: 0, done: 0 };
  const openByOwner = new Map();
  allItems.forEach((item) => {
    const status = teamEffectiveStatus(item);
    countByStatus[status] = (countByStatus[status] || 0) + 1;
    if (status === "open") openByOwner.set(item.owner, (openByOwner.get(item.owner) || 0) + 1);
  });
  const ownerCards = [...openByOwner.entries()].map(([owner, count]) => ({ label: `${owner} - to do`, value: number(count) }));
  renderCards("teamCards", [
    { label: "To do", value: number(countByStatus.open) },
    { label: "In progress", value: number(countByStatus.in_progress) },
    { label: "Completed", value: number(countByStatus.done), hint: allItems.length ? `${percent((countByStatus.done / allItems.length) * 100)} of all items logged` : "" },
    ...ownerCards,
  ]);

  ensureTeamEditForm();

  const container = document.getElementById("teamMeetings");
  const ownerFilterEl = document.getElementById("teamOwnerFilter");
  const topicFilterEl = document.getElementById("teamTopicFilter");
  const dateFilterEl = document.getElementById("teamDateFilter");
  const statusFilterEl = document.getElementById("teamStatusFilter");
  if (!allItems.length) {
    container.innerHTML = "";
    ownerFilterEl.innerHTML = "";
    topicFilterEl.innerHTML = "";
    dateFilterEl.innerHTML = "";
    statusFilterEl.innerHTML = "";
    return;
  }

  const allOwners = [...new Set([...TEAM_KNOWN_OWNERS, ...allItems.map((i) => i.owner)])];
  const allTopics = [...new Set(allItems.map((i) => i.topic || "General"))];
  const allDates = [...new Set(mergedMeetings.map((m) => m.meeting_date))];
  buildTeamFilterPills("teamOwnerFilter", "owner", allOwners);
  buildTeamFilterPills("teamTopicFilter", "topic", allTopics);
  buildTeamFilterPills("teamDateFilter", "date", allDates, (d) => fullDate(d));
  buildTeamFilterPills("teamStatusFilter", "status", TEAM_STATUS_ORDER, (s) => TEAM_STATUS_LABELS[s]);

  container.innerHTML = mergedMeetings
    .map((m) => {
      const byStatus = { open: [], in_progress: [], done: [] };
      (m.action_items || []).forEach((item) => {
        const status = teamEffectiveStatus(item);
        (byStatus[status] || byStatus.open).push(item);
      });

      const sections = TEAM_STATUS_ORDER.filter((status) => byStatus[status].length)
        .map((status) => {
          const key = `${m.meeting_date}|${status}`;
          const collapsed = !!teamCollapsed[key];
          return `
            <div class="status-section status-section-${status}${collapsed ? " collapsed" : ""}" data-status-key="${key}">
              <button type="button" class="status-header">
                <span class="status-chevron">${collapsed ? "▸" : "▾"}</span>
                <span class="status-dot"></span>
                <span class="status-label">${TEAM_STATUS_LABELS[status]}</span>
                <span class="status-count">${byStatus[status].length}</span>
              </button>
              <div class="status-body">${byStatus[status].map(teamChecklistItemHtml).join("")}</div>
            </div>`;
        })
        .join("");

      const panelCollapsed = !!teamPanelCollapsed[m.meeting_date];
      return `
        <div class="panel meeting-panel${panelCollapsed ? " collapsed" : ""}" data-date="${m.meeting_date}">
          <button type="button" class="meeting-panel-header">
            <span class="status-chevron">${panelCollapsed ? "▸" : "▾"}</span>
            <h2>${m.title}</h2>
            <span class="panel-meta">${fullDate(m.meeting_date)}</span>
          </button>
          <div class="meeting-panel-body">
            ${m.summary ? `<div class="panel-summary">${m.summary}</div>` : ""}
            <div class="status-grid">${sections}</div>
            ${
              m.raw_notes
                ? `<details class="meeting-minutes">
              <summary>&#128196; View meeting minutes</summary>
              <pre class="meeting-minutes-text">${escapeHtml(m.raw_notes)}</pre>
            </details>`
                : ""
            }
          </div>
        </div>`;
    })
    .join("");

  applyTeamFilters();
}

/* ---------- to do board (manually-added tasks, split off from Team &
   Meetings 2026-08-31 so ad-hoc tasks stop mixing with meeting recaps) ---------- */

let todoFilters = { owner: "all", topic: "all", month: "all" };
let todoListenersAttached = false;
let todoAddFormAttached = false;
let editingTodoItemId = null;

function todoCardHtml(item) {
  const status = teamEffectiveStatus(item);
  const isMeeting = item.source === "meeting";
  return `
    <div class="todo-card" data-id="${item.id}" data-owner="${item.owner}" data-topic="${item.topic || "General"}" data-source="${item.source || "manual"}" data-date="${item.meeting_date || ""}">
      <div class="todo-card-top">
        <span class="checklist-owner">${item.owner}</span><span class="checklist-topic">${item.topic || "General"}</span>
        ${isMeeting ? `<span class="todo-card-meeting-badge" title="${item.meeting_title || "Team meeting"}${item.meeting_date ? " · " + fullDate(item.meeting_date) : ""}">&#128197; From meeting</span>` : ""}
      </div>
      <div class="todo-card-desc">${linkifyText(item.description)}</div>
      ${item.context ? `<div class="checklist-context">${linkifyText(item.context)}</div>` : ""}
      <div class="todo-card-actions">
        ${status !== "done" ? `<button type="button" class="todo-card-advance" title="Move to ${status === "open" ? "In Progress" : "Completed"}">${status === "open" ? "Start →" : "Complete →"}</button>` : `<button type="button" class="todo-card-advance" title="Move back to To Do">↺ Reopen</button>`}
        ${isMeeting ? "" : `<button type="button" class="checklist-edit" title="Edit">&#9998;</button>`}
        <button type="button" class="checklist-delete" title="Remove">&times;</button>
      </div>
      ${commentsHtml(item)}
    </div>`;
}

function applyTodoFilters() {
  document.querySelectorAll(".todo-card").forEach((card) => {
    const ownerMatch = todoFilters.owner === "all" || card.dataset.owner === todoFilters.owner;
    const topicMatch = todoFilters.topic === "all" || card.dataset.topic === todoFilters.topic;
    const monthMatch = todoFilters.month === "all" || (card.dataset.date || "").slice(0, 7) === todoFilters.month;
    card.classList.toggle("hidden", !(ownerMatch && topicMatch && monthMatch));
  });
  document.querySelectorAll("#todoOwnerFilter button").forEach((b) => b.classList.toggle("active", b.dataset.value === todoFilters.owner));
  document.querySelectorAll("#todoTopicFilter button").forEach((b) => b.classList.toggle("active", b.dataset.value === todoFilters.topic));

  const label = document.getElementById("todoMonthLabel");
  if (label) {
    label.textContent =
      todoFilters.month === "all" ? "All time" : new Date(`${todoFilters.month}-01T00:00:00`).toLocaleDateString("en-US", { month: "long", year: "numeric" });
  }
  document.getElementById("todoAllTimeBtn")?.classList.toggle("active", todoFilters.month === "all");

  TEAM_STATUS_ORDER.forEach((status) => {
    const listEl = document.getElementById(`todoList${status}`);
    if (!listEl) return;
    const visibleCount = listEl.querySelectorAll(".todo-card:not(.hidden)").length;
    const emptyEl = listEl.querySelector(".todo-empty-col");
    if (!visibleCount && !emptyEl) listEl.insertAdjacentHTML("beforeend", `<div class="todo-empty-col">Nothing here</div>`);
    if (visibleCount && emptyEl) emptyEl.remove();
  });
}

function ensureTodoMonthNav() {
  if (document.getElementById("todoPrevMonth")?.dataset.bound) return;
  const prevBtn = document.getElementById("todoPrevMonth");
  const nextBtn = document.getElementById("todoNextMonth");
  const allTimeBtn = document.getElementById("todoAllTimeBtn");
  if (!prevBtn || !nextBtn || !allTimeBtn) return;
  prevBtn.dataset.bound = "1";

  const currentMonthAnchor = () => (todoFilters.month === "all" ? new Date().toISOString().slice(0, 7) : todoFilters.month);
  prevBtn.addEventListener("click", () => {
    todoFilters.month = shiftMonth(currentMonthAnchor(), -1);
    applyTodoFilters();
  });
  nextBtn.addEventListener("click", () => {
    todoFilters.month = shiftMonth(currentMonthAnchor(), 1);
    applyTodoFilters();
  });
  allTimeBtn.addEventListener("click", () => {
    todoFilters.month = "all";
    applyTodoFilters();
  });
}

function openTodoEditForm(item) {
  const form = document.getElementById("todoAddForm");
  if (!form) return;
  editingTodoItemId = item.id;
  form.querySelector('[name="owner"]').value = item.owner || "";
  form.querySelector('[name="topic"]').value = item.topic || "";
  form.querySelector('[name="meeting_date"]').value = item.meeting_date || "";
  form.querySelector('[name="description"]').value = item.description || "";
  form.querySelector('[name="context"]').value = item.context || "";
  form.querySelector('button[type="submit"]').textContent = "Save changes";
  form.classList.remove("hidden");
  form.scrollIntoView({ behavior: "smooth", block: "center" });
}

function resetTodoAddForm(form, dateInput) {
  editingTodoItemId = null;
  form.reset();
  dateInput.value = new Date().toISOString().slice(0, 10);
  form.classList.add("hidden");
  form.querySelector('button[type="submit"]').textContent = "Add task";
}

function ensureTodoAddForm(allOwners, allTopics) {
  const ownerOptions = document.getElementById("todoOwnerOptions");
  const topicOptions = document.getElementById("todoTopicOptions");
  if (ownerOptions) ownerOptions.innerHTML = allOwners.map((o) => `<option value="${o}">`).join("");
  if (topicOptions) topicOptions.innerHTML = allTopics.map((t) => `<option value="${t}">`).join("");

  if (todoAddFormAttached) return;
  todoAddFormAttached = true;
  const toggle = document.getElementById("todoAddToggle");
  const form = document.getElementById("todoAddForm");
  if (!toggle || !form) return;
  const dateInput = form.querySelector('[name="meeting_date"]');
  if (dateInput && !dateInput.value) dateInput.value = new Date().toISOString().slice(0, 10);

  toggle.addEventListener("click", () => form.classList.toggle("hidden"));
  document.getElementById("todoAddCancel")?.addEventListener("click", () => resetTodoAddForm(form, dateInput));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    const owner = String(fd.get("owner") || "").trim();
    const description = String(fd.get("description") || "").trim();
    const meetingDate = String(fd.get("meeting_date") || "").trim();
    if (!owner || !description || !meetingDate) return;
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    try {
      if (!window.domaTeamSync) throw new Error("Firestore sync not ready yet");
      const fields = {
        meeting_date: meetingDate,
        owner,
        topic: String(fd.get("topic") || "").trim() || "General",
        description,
        context: String(fd.get("context") || "").trim() || null,
      };
      if (editingTodoItemId) {
        await window.domaTeamSync.updateManualItem(editingTodoItemId, fields);
      } else {
        await window.domaTeamSync.addManualItem(fields);
      }
      resetTodoAddForm(form, dateInput);
    } catch (error) {
      console.error("Failed to save task:", error);
      alert("Could not save the task - check the browser console for details.");
    } finally {
      submitBtn.disabled = false;
    }
  });
}

function ensureTodoListeners() {
  if (todoListenersAttached) return;
  todoListenersAttached = true;
  document.getElementById("todoBoard").addEventListener("click", async (event) => {
    if (handleCommentsToggle(event)) return;

    const editBtn = event.target.closest(".checklist-edit");
    if (editBtn) {
      const cardEl = editBtn.closest(".todo-card");
      const item = todoAllItemsById.get(cardEl.dataset.id);
      if (item) openTodoEditForm(item);
      return;
    }

    const del = event.target.closest(".checklist-delete");
    if (del) {
      const cardEl = del.closest(".todo-card");
      if (!confirm("Remove this task?")) return;
      del.disabled = true;
      try {
        const isMeeting = cardEl.dataset.source === "meeting";
        if (isMeeting) {
          if (!window.domaTeamSync?.deleteActionItem) throw new Error("Delete not available");
          await window.domaTeamSync.deleteActionItem(cardEl.dataset.id);
        } else {
          if (!window.domaTeamSync?.deleteManualItem) throw new Error("Delete not available");
          await window.domaTeamSync.deleteManualItem(cardEl.dataset.id);
        }
      } catch (error) {
        console.error("Failed to delete task:", error);
        del.disabled = false;
      }
      return;
    }

    const advance = event.target.closest(".todo-card-advance");
    if (advance) {
      const cardEl = advance.closest(".todo-card");
      const itemId = cardEl.dataset.id;
      const item = todoAllItemsById.get(itemId);
      const currentStatus = teamEffectiveStatus(item || { id: itemId, status: "open" });
      const isReopen = currentStatus === "done";
      const next = isReopen ? "open" : TEAM_STATUS_ORDER[(TEAM_STATUS_ORDER.indexOf(currentStatus) + 1) % TEAM_STATUS_ORDER.length];
      advance.disabled = true;
      const ok = await setTeamActionItemStatus(itemId, next);
      if (!ok) advance.disabled = false;
      return;
    }
  });

  document.getElementById("todoBoard").addEventListener("submit", async (event) => {
    const form = event.target.closest(".ticket-comment-form");
    if (!form) return;
    event.preventDefault();
    const comment = readCommentForm(form);
    if (!comment) return;
    const itemId = form.closest(".ticket-comments").dataset.id;
    const item = todoAllItemsById.get(itemId);
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    try {
      if (!window.domaTeamSync) throw new Error("Firestore sync not ready yet");
      if (item?.source === "meeting") {
        await window.domaTeamSync.addActionItemComment(itemId, comment);
      } else {
        await window.domaTeamSync.addManualItemComment(itemId, comment);
      }
    } catch (error) {
      console.error("Failed to post comment:", error);
      alert("Could not post the comment - check the browser console for details.");
    } finally {
      submitBtn.disabled = false;
    }
  });
}

/* ---------- weekly recap (meetings grouped Monday-Sunday) ---------- */

let weeklyRecapCollapsed = {};
let weeklyRecapListenersAttached = false;

function isoWeekStart(dateStr) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  const day = d.getUTCDay(); // 0 = Sunday
  const diff = day === 0 ? -6 : 1 - day; // days back to that week's Monday
  d.setUTCDate(d.getUTCDate() + diff);
  return d.toISOString().slice(0, 10);
}

function isoWeekEnd(weekStart) {
  const d = new Date(`${weekStart}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 6);
  return d.toISOString().slice(0, 10);
}

function ensureWeeklyRecapListeners() {
  if (weeklyRecapListenersAttached) return;
  weeklyRecapListenersAttached = true;
  document.getElementById("weeklyRecapList").addEventListener("click", (event) => {
    const header = event.target.closest(".weekly-recap-header");
    if (!header) return;
    const week = header.closest(".weekly-recap-week");
    const key = week.dataset.week;
    weeklyRecapCollapsed[key] = !weeklyRecapCollapsed[key];
    week.classList.toggle("collapsed", weeklyRecapCollapsed[key]);
  });
}

function renderWeeklyRecap() {
  ensureWeeklyRecapListeners();
  const team = dashboard.team_meetings || { available: false, meetings: [] };
  // Reuses the same status/override-aware merge Team & Meetings uses, so a
  // completed item shows as done here too without a second data pipeline.
  const mergedMeetings = teamMergedMeetings(team);
  document.getElementById("weeklyRecapEmpty").style.display = mergedMeetings.length ? "none" : "block";

  const byWeek = new Map();
  mergedMeetings.forEach((m) => {
    const weekStart = isoWeekStart(m.meeting_date);
    if (!byWeek.has(weekStart)) byWeek.set(weekStart, []);
    byWeek.get(weekStart).push(m);
  });

  const container = document.getElementById("weeklyRecapList");
  container.innerHTML = [...byWeek.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([weekStart, meetings]) => {
      const weekEnd = isoWeekEnd(weekStart);
      const collapsed = !!weeklyRecapCollapsed[weekStart];
      const totalItems = meetings.reduce((sum, m) => sum + (m.action_items || []).length, 0);
      const doneItems = meetings.reduce(
        (sum, m) => sum + (m.action_items || []).filter((i) => teamEffectiveStatus(i) === "done").length,
        0
      );
      const progressItems = meetings.reduce(
        (sum, m) => sum + (m.action_items || []).filter((i) => teamEffectiveStatus(i) === "in_progress").length,
        0
      );
      const meetingsHtml = [...meetings]
        .sort((a, b) => (a.meeting_date < b.meeting_date ? 1 : -1))
        .map((m) => {
          const items = m.action_items || [];
          const doneCount = items.filter((i) => teamEffectiveStatus(i) === "done").length;
          return `
            <div class="weekly-recap-meeting">
              <div class="weekly-recap-meeting-header">
                <strong>${m.title}</strong>
                <span class="panel-meta">${fullDate(m.meeting_date)}</span>
                <span class="status-count">${doneCount}/${items.length} done</span>
              </div>
              ${m.summary ? `<p class="weekly-recap-summary">${m.summary}</p>` : ""}
              ${
                m.raw_notes
                  ? `<details class="meeting-minutes">
                <summary>&#128196; View full recap</summary>
                <pre class="meeting-minutes-text">${escapeHtml(m.raw_notes)}</pre>
              </details>`
                  : ""
              }
            </div>`;
        })
        .join("");
      return `
        <div class="panel weekly-recap-week${collapsed ? " collapsed" : ""}" data-week="${weekStart}">
          <button type="button" class="weekly-recap-header">
            <span class="status-chevron">${collapsed ? "▸" : "▾"}</span>
            <h3>Week of ${dateRange(weekStart, weekEnd)}</h3>
            <span class="status-count">${meetings.length} meeting${meetings.length === 1 ? "" : "s"} &middot; ${doneItems}/${totalItems} items done${progressItems ? ` &middot; ${progressItems} in progress` : ""}</span>
          </button>
          <div class="weekly-recap-body">${meetingsHtml}</div>
        </div>`;
    })
    .join("");
}

function renderTodoBoard() {
  ensureTodoListeners();
  ensureTodoMonthNav();
  const manualItems = teamManualItems.map((mi) => ({
    id: mi.id,
    owner: mi.owner,
    topic: mi.topic || "General",
    description: mi.description,
    context: mi.context || null,
    meeting_date: mi.meeting_date,
    status: mi.status || "open",
    source: "manual",
  }));

  // Meeting tickets now show here too (2026-09-14, per Lucas) - reuses the
  // same merged/override-aware item set Team & Meetings builds, so status,
  // edits, and comments made on either tab stay in sync automatically.
  const team = dashboard.team_meetings || { meetings: [] };
  const meetingItems = teamMergedMeetings(team).flatMap((m) =>
    (m.action_items || []).map((item) => ({
      ...item,
      topic: item.topic || "General",
      source: "meeting",
      meeting_title: m.title,
      meeting_date: item.meeting_date || m.meeting_date,
    }))
  );

  const items = [...manualItems, ...meetingItems];
  todoAllItemsById = new Map(items.map((i) => [String(i.id), i]));

  document.getElementById("todoEmpty").style.display = items.length ? "none" : "block";

  const byStatus = { open: [], in_progress: [], done: [] };
  items.forEach((item) => {
    const status = teamEffectiveStatus(item);
    (byStatus[status] || byStatus.open).push(item);
  });

  renderCards("todoCards", [
    { label: "To do", value: number(byStatus.open.length) },
    { label: "In progress", value: number(byStatus.in_progress.length) },
    { label: "Completed", value: number(byStatus.done.length) },
  ]);

  const allOwners = [...new Set([...TEAM_KNOWN_OWNERS, ...items.map((i) => i.owner)])];
  const allTopics = [...new Set(items.map((i) => i.topic || "General"))];
  ensureTodoAddForm(allOwners, allTopics);

  TEAM_STATUS_ORDER.forEach((status) => {
    const listEl = document.getElementById(`todoList${status}`);
    const countEl = document.getElementById(`todoCount${status}`);
    if (countEl) countEl.textContent = byStatus[status].length;
    if (listEl) listEl.innerHTML = byStatus[status].map(todoCardHtml).join("") || `<div class="todo-empty-col">Nothing here</div>`;
  });

  buildFilterPills("todoOwnerFilter", allOwners, null, (value) => {
    todoFilters.owner = value;
    applyTodoFilters();
  });
  buildFilterPills("todoTopicFilter", allTopics, null, (value) => {
    todoFilters.topic = value;
    applyTodoFilters();
  });
  applyTodoFilters();
  renderTodoMonthlyRecap(items);
}

// "Pull everything that got done and put a summary" - Sept 28 meeting note
// said the last meeting of each month would be a full to-do review. This is
// that review, but always live: it recaps the current month's completed
// tickets by owner, so it already reads as the full month once the month
// actually ends. Starts collapsed (2026-09-28, Lucas asked for it as a
// toggleable tab rather than always taking up board space) - state persists
// across re-renders via the module-level flag, same pattern as the Weekly
// Recap weeks and Team & Meetings panels.
let todoRecapCollapsed = true;
let todoRecapListenersAttached = false;

function ensureTodoRecapListeners() {
  if (todoRecapListenersAttached) return;
  todoRecapListenersAttached = true;
  document.getElementById("todoMonthlyRecap").addEventListener("click", (event) => {
    if (!event.target.closest(".recap-header")) return;
    todoRecapCollapsed = !todoRecapCollapsed;
    document.getElementById("todoMonthlyRecap").classList.toggle("collapsed", todoRecapCollapsed);
  });
}

function renderTodoMonthlyRecap(items) {
  const el = document.getElementById("todoMonthlyRecap");
  if (!el) return;
  ensureTodoRecapListeners();
  el.classList.toggle("collapsed", todoRecapCollapsed);
  const chevron = `<span class="status-chevron">${todoRecapCollapsed ? "▸" : "▾"}</span>`;

  const monthKey = new Date().toISOString().slice(0, 7);
  const monthLabel = new Date(`${monthKey}-01T00:00:00`).toLocaleDateString("en-US", { month: "long", year: "numeric" });

  const doneThisMonth = items.filter((item) => {
    if (teamEffectiveStatus(item) !== "done") return false;
    const completedAt = teamStatusUpdatedAt(item);
    return completedAt && new Date(completedAt).toISOString().slice(0, 7) === monthKey;
  });

  if (!doneThisMonth.length) {
    el.innerHTML = `
      <button type="button" class="recap-header">
        ${chevron}<h2>Completed in ${monthLabel}</h2>
        <span class="panel-meta">Nothing marked done yet this month</span>
      </button>`;
    return;
  }

  const byOwner = new Map();
  doneThisMonth.forEach((item) => {
    if (!byOwner.has(item.owner)) byOwner.set(item.owner, []);
    byOwner.get(item.owner).push(item);
  });

  const ownerBlocks = [...byOwner.entries()]
    .sort((a, b) => b[1].length - a[1].length)
    .map(
      ([owner, ownerItems]) => `
      <div class="recap-owner-block">
        <div class="recap-owner-header">${owner} <span class="status-count">${ownerItems.length}</span></div>
        <ul class="recap-owner-list">
          ${ownerItems.map((i) => `<li>${linkifyText(i.description)}</li>`).join("")}
        </ul>
      </div>`
    )
    .join("");

  el.innerHTML = `
    <button type="button" class="recap-header">
      ${chevron}<h2>Completed in ${monthLabel}</h2>
      <span class="status-count">${doneThisMonth.length} task${doneThisMonth.length === 1 ? "" : "s"}</span>
    </button>
    <div class="recap-body"><div class="recap-owner-grid">${ownerBlocks}</div></div>`;
}

/* ---------- content calendar ---------- */

const CALENDAR_STATUS_LABELS = { open: "Planned", in_progress: "In progress", done: "Published" };
// Matches the real weekly content cadence Juli confirmed (2026-08-26):
// Mon = Engagement Question, Tue = Teach It Tuesday, Wed = Sponsor,
// Thu = Blog/Ebook, Fri = Community Reshare (Blog and Ebook split into
// separate types 2026-09-21 - they were sharing one filter). "Other" covers anything
// outside that cadence (podcast promo, one-off announcements, etc).
const CALENDAR_TYPES = ["Engagement Question", "Teach It Tuesday", "Sponsor", "Blog", "Ebook", "Community Reshare", "Other"];

let calendarItems = [];
let calendarLiveStatuses = new Map();
let calendarFilters = { owner: "all", type: "all", status: "all" };
let calendarListenersAttached = false;
let calendarAddFormAttached = false;
let calendarViewMonth = new Date().toISOString().slice(0, 7); // "YYYY-MM", the month currently shown

function buildCalendarFilterPills(containerId, key, values, labelFn) {
  buildFilterPills(containerId, values, labelFn, (value) => {
    calendarFilters[key] = value;
    applyCalendarFilters();
  });
}

function applyCalendarFilters() {
  document.querySelectorAll("#calendarGrid .cal-item, #calendarUnscheduledList .cal-item").forEach((item) => {
    const ownerMatch = calendarFilters.owner === "all" || item.dataset.owner === calendarFilters.owner;
    const typeMatch = calendarFilters.type === "all" || item.dataset.type === calendarFilters.type;
    const statusMatch = calendarFilters.status === "all" || item.dataset.status === calendarFilters.status;
    item.classList.toggle("hidden", !(ownerMatch && typeMatch && statusMatch));
  });
  document.querySelectorAll("#calendarOwnerFilter button").forEach((b) => b.classList.toggle("active", b.dataset.value === calendarFilters.owner));
  document.querySelectorAll("#calendarTypeFilter button").forEach((b) => b.classList.toggle("active", b.dataset.value === calendarFilters.type));
  document.querySelectorAll("#calendarStatusFilter button").forEach((b) => b.classList.toggle("active", b.dataset.value === calendarFilters.status));
}

function ensureCalendarListeners() {
  if (calendarListenersAttached) return;
  calendarListenersAttached = true;

  const handleGridClick = async (event) => {
    const editBtn = event.target.closest(".cal-item-edit");
    if (editBtn) {
      const itemEl = editBtn.closest(".cal-item");
      const item = calendarItems.find((i) => String(i.id) === itemEl.dataset.id);
      if (item) openCalendarEditForm(item);
      return;
    }

    const del = event.target.closest(".cal-item-delete");
    if (del) {
      const itemEl = del.closest(".cal-item");
      if (!confirm("Remove this calendar item?")) return;
      del.disabled = true;
      try {
        await window.domaContentCalendar.deleteItem(itemEl.dataset.id);
      } catch (error) {
        console.error("Failed to delete calendar item:", error);
        del.disabled = false;
      }
      return;
    }

    // The pencil icon only reveals on hover (easy to miss, and unusable on
    // touch), so clicking the title text itself - the main visible part of
    // the item - opens the same edit form. This is the primary way to open
    // an item; the pencil stays as a smaller, always-labeled alternative.
    const titleClick = event.target.closest(".cal-item-title");
    if (titleClick) {
      const itemEl = titleClick.closest(".cal-item");
      const item = calendarItems.find((i) => String(i.id) === itemEl.dataset.id);
      if (item) openCalendarEditForm(item);
      return;
    }

    const mark = event.target.closest(".cal-item-mark");
    if (!mark || mark.disabled) return;
    const itemEl = mark.closest(".cal-item");
    const itemId = itemEl.dataset.id;
    const next = TEAM_STATUS_ORDER[(TEAM_STATUS_ORDER.indexOf(itemEl.dataset.status) + 1) % TEAM_STATUS_ORDER.length];
    mark.disabled = true;
    try {
      await window.domaContentCalendar.setStatus(itemId, next);
    } catch (error) {
      console.error("Failed to update calendar item status:", error);
    }
    mark.disabled = false;
  };
  document.getElementById("calendarGrid").addEventListener("click", handleGridClick);
  document.getElementById("calendarUnscheduledList").addEventListener("click", handleGridClick);

  document.getElementById("calendarPrevMonth")?.addEventListener("click", () => {
    calendarViewMonth = shiftMonth(calendarViewMonth, -1);
    renderContentCalendar();
  });
  document.getElementById("calendarNextMonth")?.addEventListener("click", () => {
    calendarViewMonth = shiftMonth(calendarViewMonth, 1);
    renderContentCalendar();
  });
  document.getElementById("calendarTodayBtn")?.addEventListener("click", () => {
    calendarViewMonth = new Date().toISOString().slice(0, 7);
    renderContentCalendar();
  });

  whenFirestoreReady(() => {
    window.domaContentCalendar.subscribeItems((items) => {
      calendarItems = items;
      renderContentCalendar();
    });
    window.domaContentCalendar.subscribeStatuses((statuses) => {
      calendarLiveStatuses = statuses;
      renderContentCalendar();
    });
  });
}

function renderCalendarSuggestions() {
  const container = document.getElementById("calendarSuggestions");
  if (!container) return;
  const suggestions = dashboard.content_suggestions || { content_gaps: [], top_ebooks: [] };
  const gapChips = visibleGaps(suggestions.content_gaps)
    .slice(0, 6)
    .map(
      (g) =>
        `<button type="button" class="calendar-suggestion-chip" data-title="${escAttr(g.query)}" data-type="Blog">${escapeHtml(g.query)}<span class="cal-suggestion-meta">${g.action ? `${escapeHtml(g.action)} · ` : ""}${number(g.impressions)} impressions, position ${Number(g.position).toFixed(1)}</span></button>`
    );
  // "Repurpose into a blog post" only makes sense for real ebooks.
  const ebookChips = (suggestions.top_ebooks || [])
    .filter(isEbookRow)
    .slice(0, 4)
    .map(
      (e) =>
        `<button type="button" class="calendar-suggestion-chip" data-title="Blog post based on: ${e.page_title || titleFromUrl(e.page_path)}" data-type="Blog">${e.page_title || titleFromUrl(e.page_path)}<span class="cal-suggestion-meta">${number(e.sessions)} sessions - repurpose into a blog post</span></button>`
    );
  const toggle = document.getElementById("calendarGapsToggle");
  if (toggle) renderSponsorToggle("calendarGapsToggle", suggestions.content_gaps);
  container.innerHTML = [...gapChips, ...ebookChips].join("") || `<div class="empty">No suggestions yet - sync Search Console/GA4 data first.</div>`;
}

// Same reuse pattern as the Team ticket form: null means "add new", a
// Firestore doc id means the form is editing that item in place.
let editingCalendarItemId = null;

// Content Calendar links, one per line: "url" or "url - label". Falls back
// to the old singular `link` string field for items created before
// 2026-09-21 (Juli asked for multiple links per entry).
function calendarItemLinks(item) {
  if (Array.isArray(item.links) && item.links.length) return item.links;
  if (item.link) return [{ url: item.link, label: null }];
  return [];
}

function parseLinksTextarea(raw) {
  return String(raw || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const sepIdx = line.indexOf(" - ");
      if (sepIdx === -1) return { url: line, label: null };
      return { url: line.slice(0, sepIdx).trim(), label: line.slice(sepIdx + 3).trim() || null };
    });
}

function linksToTextarea(links) {
  return links.map((l) => (l.label ? `${l.url} - ${l.label}` : l.url)).join("\n");
}

// Set on the edit form while an item is open, so submit can fall back to
// the item's existing pdf_url/image_url when no replacement file is chosen -
// <input type="file"> can't be prefilled, so those URLs live here instead.
let editingCalendarItemFiles = { pdf_url: null, image_url: null };

function openCalendarEditForm(item) {
  const form = document.getElementById("calendarAddForm");
  if (!form) return;
  editingCalendarItemId = item.id;
  editingCalendarItemFiles = { pdf_url: item.pdf_url || null, image_url: item.image_url || null };
  form.querySelector('[name="date"]').value = item.date || "";
  form.querySelector('[name="type"]').value = item.type || "Blog";
  form.querySelector('[name="owner"]').value = item.owner || "";
  form.querySelector('[name="title"]').value = item.title || "";
  form.querySelector('[name="headline"]').value = item.headline || "";
  form.querySelector('[name="direction"]').value = item.direction || "";
  form.querySelector('[name="graphic"]').value = item.graphic || "";
  form.querySelector('[name="resource"]').value = item.resource || "";
  form.querySelector('[name="links"]').value = linksToTextarea(calendarItemLinks(item));
  form.querySelector('[name="notes"]').value = item.notes || "";
  renderCalendarUploadStatus();
  form.querySelector('button[type="submit"]').textContent = "Save changes";
  form.classList.remove("hidden");
  form.scrollIntoView({ behavior: "smooth", block: "center" });
}

function resetCalendarAddForm(form, dateInput) {
  editingCalendarItemId = null;
  editingCalendarItemFiles = { pdf_url: null, image_url: null };
  form.reset();
  dateInput.value = new Date().toISOString().slice(0, 10);
  form.classList.add("hidden");
  form.querySelector('button[type="submit"]').textContent = "Add to calendar";
  renderCalendarUploadStatus();
}

// Shows what's already uploaded (when editing) below the file pickers, and
// doubles as the "uploading..." indicator during submit.
function renderCalendarUploadStatus(message) {
  const el = document.getElementById("calendarUploadStatus");
  if (!el) return;
  if (message) {
    el.textContent = message;
    el.hidden = false;
    return;
  }
  const parts = [];
  if (editingCalendarItemFiles.pdf_url) parts.push(`<a href="${editingCalendarItemFiles.pdf_url}" target="_blank" rel="noopener noreferrer">Current PDF &#8599;</a>`);
  if (editingCalendarItemFiles.image_url) parts.push(`<a href="${editingCalendarItemFiles.image_url}" target="_blank" rel="noopener noreferrer">Current image &#8599;</a>`);
  el.innerHTML = parts.join(" · ");
  el.hidden = parts.length === 0;
}

// Uploads to this app's own backend, which relays to the WordPress media
// library (see /api/content-calendar/upload in app.py) - either the local
// FastAPI dashboard (same origin, no auth header needed) or, on the
// published site, a separately-deployed copy of this same app.py
// (CALENDAR_API_BASE, with the Basic-Auth header its protect_writes
// middleware requires on every write - see CALENDAR_UPLOAD_AUTH above).
async function uploadCalendarFile(file) {
  const body = new FormData();
  body.append("file", file);
  const headers = CALENDAR_UPLOAD_AUTH ? { Authorization: `Basic ${CALENDAR_UPLOAD_AUTH}` } : {};
  const response = await fetch(`${CALENDAR_API_BASE}/api/content-calendar/upload`, {
    method: "POST",
    body,
    headers,
    credentials: CALENDAR_API_BASE ? "omit" : "same-origin",
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`Upload failed (${response.status}): ${text.slice(0, 200)}`);
  }
  return response.json(); // {url, id, filename}
}

// Builds the capture + thank-you WordPress draft pages for a freshly
// uploaded ebook (see /api/content-calendar/create-pages in app.py) - only
// called right after a NEW pdf upload, never on a plain edit/save, so
// re-saving an existing ebook item doesn't recreate its pages.
async function createCalendarEbookPages({ title, pdf_url, image_url, image_media_id }) {
  const headers = { "Content-Type": "application/json" };
  if (CALENDAR_UPLOAD_AUTH) headers.Authorization = `Basic ${CALENDAR_UPLOAD_AUTH}`;
  const response = await fetch(`${CALENDAR_API_BASE}/api/content-calendar/create-pages`, {
    method: "POST",
    headers,
    body: JSON.stringify({ title, pdf_url, image_url, image_media_id }),
    credentials: CALENDAR_API_BASE ? "omit" : "same-origin",
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`Page creation failed (${response.status}): ${text.slice(0, 300)}`);
  }
  return response.json(); // {slug, capture_url, thank_you_url, capture_edit_url, thank_you_edit_url, form_attached}
}

function ensureCalendarAddForm(allOwners) {
  const ownerOptions = document.getElementById("calendarOwnerOptions");
  if (ownerOptions) ownerOptions.innerHTML = allOwners.map((o) => `<option value="${o}">`).join("");

  // Show the file pickers whenever there's an actual backend to upload to -
  // local always has one (same origin); the published site only does once
  // CALENDAR_API_BASE is configured (see generate_public_site.py). Explain
  // why otherwise, rather than a silent disappearance (that's exactly what
  // confused Thalles 2026-09-24 - "não estou achando mais a parte para
  // fazer o upload").
  const uploadRow = document.getElementById("calendarUploadRow");
  if (uploadRow) uploadRow.hidden = !CALENDAR_UPLOAD_AVAILABLE;
  const uploadStaticNote = document.getElementById("calendarUploadStaticNote");
  if (uploadStaticNote) uploadStaticNote.hidden = CALENDAR_UPLOAD_AVAILABLE;

  if (calendarAddFormAttached) return;
  calendarAddFormAttached = true;
  const toggle = document.getElementById("calendarAddToggle");
  const form = document.getElementById("calendarAddForm");
  if (!toggle || !form) return;
  const dateInput = form.querySelector('[name="date"]');
  if (dateInput && !dateInput.value) dateInput.value = new Date().toISOString().slice(0, 10);

  toggle.addEventListener("click", () => form.classList.toggle("hidden"));
  document.getElementById("calendarAddCancel")?.addEventListener("click", () => resetCalendarAddForm(form, dateInput));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    const title = String(fd.get("title") || "").trim();
    const date = String(fd.get("date") || "").trim();
    if (!title || !date) return;
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    try {
      if (!window.domaContentCalendar) throw new Error("Firestore sync not ready yet");

      let pdf_url = editingCalendarItemFiles.pdf_url;
      let image_url = editingCalendarItemFiles.image_url;
      let image_media_id = null;
      const pdfFile = fd.get("pdf_file");
      const imageFile = fd.get("image_file");
      const isFreshPdfUpload = pdfFile instanceof File && pdfFile.size > 0;
      if (isFreshPdfUpload) {
        renderCalendarUploadStatus("Please wait a few moments...");
        pdf_url = (await uploadCalendarFile(pdfFile)).url;
      }
      if (imageFile instanceof File && imageFile.size > 0) {
        renderCalendarUploadStatus("Please wait a few moments...");
        const uploaded = await uploadCalendarFile(imageFile);
        image_url = uploaded.url;
        image_media_id = uploaded.id;
      }

      const type = String(fd.get("type") || "Blog");
      const owner = String(fd.get("owner") || "").trim();
      const notes = String(fd.get("notes") || "").trim();
      const headline = String(fd.get("headline") || "").trim();
      const direction = String(fd.get("direction") || "").trim();
      const graphic = String(fd.get("graphic") || "").trim();
      const resource = String(fd.get("resource") || "").trim();
      const links = parseLinksTextarea(fd.get("links"));
      const fields = {
        date,
        type,
        title,
        owner: owner || null,
        notes: notes || null,
        headline: headline || null,
        direction: direction || null,
        graphic: graphic || null,
        resource: resource || null,
        links: links.length ? links : null,
        link: null, // superseded by `links` - clears the legacy single-link field on save
        pdf_url: pdf_url || null,
        image_url: image_url || null,
      };
      let savedItemId = editingCalendarItemId;
      if (editingCalendarItemId) {
        await window.domaContentCalendar.updateItem(editingCalendarItemId, fields);
      } else {
        savedItemId = await window.domaContentCalendar.addItem(fields);
        // No longer auto-creates a matching Team & Meetings ticket - Lucas
        // asked to keep the two lists from crossing over (2026-08-31
        // meeting). Content Calendar and Team tickets are independent now.
      }

      // A fresh PDF + cover together, on an Ebook-type entry, is the signal
      // to build the WordPress pages - triggered right here at upload time
      // rather than polling for it later, since the upload IS the event.
      // Pages publish live immediately (not draft) - the calendar item IS
      // the review step, so there's no separate draft-review pass here.
      if (type === "Ebook" && isFreshPdfUpload && image_url && image_media_id) {
        renderCalendarUploadStatus("Please wait a few moments...");
        try {
          const pages = await createCalendarEbookPages({ title, pdf_url, image_url, image_media_id });
          const pageLinks = [
            { url: pages.capture_url, label: "Capture page" },
            { url: pages.thank_you_url, label: "Thank you page" },
          ];
          await window.domaContentCalendar.updateItem(savedItemId, {
            links: [...links, ...pageLinks],
          });
          alert(
            `Pages published for "${pages.title}". Check the Content Calendar item's Links again - the capture page link is there now.\n\n` +
              (pages.form_attached
                ? "GHL form already matched and embedded."
                : `GHL form not found yet - duplicate an "Ebook - ..." form, rename it to "Ebook - ${pages.title}", it'll attach automatically on the next scheduled run.`)
          );
        } catch (error) {
          console.error("Failed to build ebook pages:", error);
          alert(`Calendar item saved, but building the pages failed: ${error.message}`);
        }
      }

      resetCalendarAddForm(form, dateInput);
    } catch (error) {
      console.error("Failed to save calendar item:", error);
      alert(`Could not save the calendar item: ${error.message || "check the browser console for details."}`);
      renderCalendarUploadStatus();
    } finally {
      submitBtn.disabled = false;
    }
  });

  // Suggestion chips prefill the form instead of submitting directly, so
  // date/owner/notes can still be adjusted before saving. Always starts from
  // a clean "add new" state first - otherwise clicking a suggestion while
  // the form still held a previously-opened edit (fields + editingCalendarItemId
  // left over from clicking a pencil icon without hitting Cancel) would only
  // overwrite the title/type, silently turning the suggestion into an edit
  // of that other, unrelated item instead of creating a new one.
  document.getElementById("calendarSuggestions")?.addEventListener("click", (event) => {
    const chip = event.target.closest(".calendar-suggestion-chip");
    if (!chip) return;
    resetCalendarAddForm(form, dateInput);
    form.classList.remove("hidden");
    form.querySelector('[name="title"]').value = chip.dataset.title || "";
    form.querySelector('[name="type"]').value = chip.dataset.type || "Blog";
    form.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

function shiftMonth(monthStr, delta) {
  const [year, month] = monthStr.split("-").map(Number);
  const d = new Date(Date.UTC(year, month - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

function calItemHtml(item) {
  // The compact per-day row can't show every content-planning field inline
  // (headline, direction, graphic idea, resource, notes) - they go into the
  // native title tooltip on hover, AND clicking the row (or the pencil)
  // opens the full edit form with everything expanded and editable, which
  // is the reliable way to see it all without depending on hover.
  const links = calendarItemLinks(item);
  const tooltipLines = [item.title];
  if (item.owner) tooltipLines.push(`Owner: ${item.owner}`);
  if (item.headline) tooltipLines.push(`Headline/Hook: ${item.headline}`);
  if (item.direction) tooltipLines.push(`Direction: ${item.direction}`);
  if (item.graphic) tooltipLines.push(`Graphic idea: ${item.graphic}`);
  if (item.resource) tooltipLines.push(`Resource: ${item.resource}`);
  if (item.notes) tooltipLines.push(`Notes: ${item.notes}`);
  links.forEach((l) => tooltipLines.push(`Link${l.label ? " (" + l.label + ")" : ""}: ${l.url}`));
  const tooltip = tooltipLines.join("\n");

  const linkIcons = links
    .map((l) => `<a href="${safeUrl(l.url)}" target="_blank" rel="noopener noreferrer" class="cal-item-link" title="${escAttr(l.label || "Open link")}">&#128279;</a>`)
    .join("");

  return `
    <div class="cal-item status-${escAttr(item.status)}" data-id="${escAttr(item.id)}" data-owner="${escAttr(item.owner || "")}" data-type="${escAttr(item.type)}" data-status="${escAttr(item.status)}" title="${escAttr(tooltip)}">
      <button type="button" class="cal-item-mark" title="Click to change status">${teamStatusIcon(item.status)}</button>
      <span class="cal-item-title">${escapeHtml(item.title || "")}</span>
      ${item.notes ? `<span class="cal-item-link" title="Has notes - click the title to read them">&#128221;</span>` : ""}
      ${linkIcons}
      ${item.pdf_url ? `<a href="${safeUrl(item.pdf_url)}" target="_blank" rel="noopener noreferrer" class="cal-item-link" title="Ebook PDF">&#128196;</a>` : ""}
      ${item.image_url ? `<a href="${safeUrl(item.image_url)}" target="_blank" rel="noopener noreferrer" class="cal-item-link" title="Cover image">&#128247;</a>` : ""}
      <button type="button" class="cal-item-edit" title="Edit">&#9998;</button>
      <button type="button" class="cal-item-delete" title="Remove">&times;</button>
    </div>`;
}

function renderContentCalendar() {
  ensureCalendarListeners();
  renderCalendarSuggestions();

  const items = calendarItems.map((item) => ({ ...item, status: calendarLiveStatuses.get(String(item.id)) || item.status || "open" }));

  const countByStatus = { open: 0, in_progress: 0, done: 0 };
  items.forEach((item) => {
    countByStatus[item.status] = (countByStatus[item.status] || 0) + 1;
  });
  renderCards("calendarCards", [
    { label: "Planned", value: number(countByStatus.open) },
    { label: "In progress", value: number(countByStatus.in_progress) },
    { label: "Published", value: number(countByStatus.done) },
  ]);

  document.getElementById("calendarEmpty").style.display = items.length ? "none" : "block";

  const allOwners = [...new Set([...TEAM_KNOWN_OWNERS, ...items.map((i) => i.owner).filter(Boolean)])];
  ensureCalendarAddForm(allOwners);

  const grid = document.getElementById("calendarGrid");
  const unscheduledPanel = document.getElementById("calendarUnscheduledPanel");
  const unscheduledList = document.getElementById("calendarUnscheduledList");
  const monthLabelEl = document.getElementById("calendarMonthLabel");
  const ownerFilterEl = document.getElementById("calendarOwnerFilter");
  const typeFilterEl = document.getElementById("calendarTypeFilter");
  const statusFilterEl = document.getElementById("calendarStatusFilter");

  if (!items.length) {
    grid.innerHTML = "";
    unscheduledPanel.style.display = "none";
    ownerFilterEl.innerHTML = "";
    typeFilterEl.innerHTML = "";
    statusFilterEl.innerHTML = "";
    if (monthLabelEl) monthLabelEl.textContent = new Date(`${calendarViewMonth}-01T00:00:00`).toLocaleDateString("en-US", { month: "long", year: "numeric" });
    return;
  }

  const allTypes = [...new Set([...CALENDAR_TYPES, ...items.map((i) => i.type)])];
  buildCalendarFilterPills("calendarOwnerFilter", "owner", allOwners);
  buildCalendarFilterPills("calendarTypeFilter", "type", allTypes);
  buildCalendarFilterPills("calendarStatusFilter", "status", TEAM_STATUS_ORDER, (s) => CALENDAR_STATUS_LABELS[s]);

  // Real month-grid: 7 columns (Sun-Sat), one cell per day, items shown
  // inside the day they're scheduled for. Items with no date at all can't
  // go in a cell - those get their own "Unscheduled" list below the grid.
  const byDate = new Map();
  const unscheduled = [];
  items.forEach((item) => {
    if (!item.date) {
      unscheduled.push(item);
      return;
    }
    if (!byDate.has(item.date)) byDate.set(item.date, []);
    byDate.get(item.date).push(item);
  });

  const [year, month] = calendarViewMonth.split("-").map(Number);
  const firstOfMonth = new Date(Date.UTC(year, month - 1, 1));
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const startOffset = firstOfMonth.getUTCDay(); // 0 = Sunday
  const totalCells = Math.ceil((startOffset + daysInMonth) / 7) * 7;
  const todayIso = new Date().toISOString().slice(0, 10);

  if (monthLabelEl) monthLabelEl.textContent = firstOfMonth.toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });

  const cells = [];
  for (let i = 0; i < totalCells; i++) {
    const dayNum = i - startOffset + 1;
    if (dayNum < 1 || dayNum > daysInMonth) {
      cells.push(`<div class="calendar-day calendar-day-outside"></div>`);
      continue;
    }
    const dateIso = `${calendarViewMonth}-${String(dayNum).padStart(2, "0")}`;
    const dayItems = byDate.get(dateIso) || [];
    cells.push(`
      <div class="calendar-day${dateIso === todayIso ? " calendar-day-today" : ""}" data-date="${dateIso}">
        <div class="calendar-day-num">${dayNum}</div>
        <div class="calendar-day-items">${dayItems.map(calItemHtml).join("")}</div>
      </div>`);
  }
  grid.innerHTML = cells.join("");

  if (unscheduled.length) {
    unscheduledPanel.style.display = "";
    unscheduledList.innerHTML = unscheduled.map(calItemHtml).join("");
  } else {
    unscheduledPanel.style.display = "none";
    unscheduledList.innerHTML = "";
  }

  applyCalendarFilters();
}

/* ---------- useful links ---------- */

let linksItems = [];
let linksListenersAttached = false;
let linksAddFormAttached = false;
// Which sections start closed, keyed by "Parent" for a top-level category
// or "Parent: Child" for a sub-category nested inside one (see renderLinks -
// any category containing ": " nests under its "Parent" prefix). Ebooks and
// Partner Offers each have ~20 links across many topics and would otherwise
// dominate the tab; anything not listed here starts open. Once a section is
// manually toggled, its state persists across re-renders here too (same
// pattern as teamCollapsed).
let linksCollapsedCategories = { "Partner Offers": true, Ebooks: true };

function ensureLinksListeners() {
  if (linksListenersAttached) return;
  linksListenersAttached = true;
  document.getElementById("linksList").addEventListener("click", async (event) => {
    // Checked before the generic .links-category-header case below - a
    // sub-category header carries BOTH classes, and .closest(".links-category")
    // from it would find the outer parent (e.g. "Ebooks") instead of the
    // sub-section (e.g. "Ebooks: AI") that was actually clicked.
    const subHeader = event.target.closest(".links-subcategory-header");
    if (subHeader) {
      const section = subHeader.closest(".links-subcategory");
      const category = section.dataset.category;
      linksCollapsedCategories[category] = !linksCollapsedCategories[category];
      section.classList.toggle("collapsed", linksCollapsedCategories[category]);
      return;
    }

    const header = event.target.closest(".links-category-header");
    if (header) {
      const section = header.closest(".links-category");
      const category = section.dataset.category;
      linksCollapsedCategories[category] = !linksCollapsedCategories[category];
      section.classList.toggle("collapsed", linksCollapsedCategories[category]);
      return;
    }

    const copyBtn = event.target.closest(".link-copy");
    if (copyBtn) {
      try {
        await navigator.clipboard.writeText(copyBtn.dataset.url);
        const original = copyBtn.textContent;
        copyBtn.textContent = "✓";
        copyBtn.classList.add("copied");
        setTimeout(() => {
          copyBtn.textContent = original;
          copyBtn.classList.remove("copied");
        }, 1200);
      } catch (error) {
        console.error("Failed to copy link:", error);
      }
      return;
    }

    const del = event.target.closest(".checklist-delete");
    if (!del) return;
    if (!confirm("Remove this link?")) return;
    const card = del.closest(".link-card");
    del.disabled = true;
    try {
      await window.domaUsefulLinks.deleteLink(card.dataset.id);
    } catch (error) {
      console.error("Failed to delete link:", error);
      del.disabled = false;
    }
  });

  document.getElementById("linksSearch")?.addEventListener("input", applyLinksSearch);

  whenFirestoreReady(() => {
    window.domaUsefulLinks.subscribeLinks((links) => {
      linksItems = links;
      renderLinks();
    });
  });
}

// Filters every link-card by the search box's value against each card's
// data-search blob (built in renderLinks - covers fields the card doesn't
// even display, like an offer's original site badge/description, so
// "phone service" finds Mango Voice by what it does, not just its name).
// Matching categories auto-expand for the duration of the search via a CSS
// class rather than touching linksCollapsedCategories, so clearing the
// search box restores whatever collapse state the user had manually set.
function applyLinksSearch() {
  const query = (document.getElementById("linksSearch")?.value || "").trim().toLowerCase();
  const emptyEl = document.getElementById("linksSearchEmpty");

  // Pass 1: every card gets matched against the query, regardless of
  // nesting depth (flat under a top-level category, or inside a sub-category).
  let anyVisible = false;
  document.querySelectorAll(".link-card").forEach((card) => {
    const match = !query || (card.dataset.search || "").includes(query);
    card.classList.toggle("hidden", !match);
    if (match) anyVisible = true;
  });

  // Pass 2: a sub-category is visible if any of its own cards matched.
  document.querySelectorAll(".links-subcategory").forEach((sub) => {
    const hasMatch = !!sub.querySelector(".link-card:not(.hidden)");
    sub.classList.toggle("hidden", Boolean(query) && !hasMatch);
    sub.classList.toggle("search-expanded", Boolean(query) && hasMatch);
  });

  // Pass 3: a top-level category is visible if any direct card OR any
  // sub-category (already resolved in pass 2) matched - querySelector finds
  // cards at any depth, so this covers both flat and nested categories.
  document.querySelectorAll(".links-category").forEach((section) => {
    const hasMatch = !!section.querySelector(".link-card:not(.hidden)");
    section.classList.toggle("hidden", Boolean(query) && !hasMatch);
    section.classList.toggle("search-expanded", Boolean(query) && hasMatch);
  });

  if (emptyEl) emptyEl.style.display = query && !anyVisible ? "block" : "none";
}

function ensureLinksAddForm(allCategories) {
  const categoryOptions = document.getElementById("linksCategoryOptions");
  if (categoryOptions) categoryOptions.innerHTML = allCategories.map((c) => `<option value="${c}">`).join("");

  if (linksAddFormAttached) return;
  linksAddFormAttached = true;
  const toggle = document.getElementById("linksAddToggle");
  const form = document.getElementById("linksAddForm");
  if (!toggle || !form) return;

  toggle.addEventListener("click", () => form.classList.toggle("hidden"));
  document.getElementById("linksAddCancel")?.addEventListener("click", () => {
    form.reset();
    form.classList.add("hidden");
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    const title = String(fd.get("title") || "").trim();
    const url = String(fd.get("url") || "").trim();
    if (!title || !url) return;
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    try {
      if (!window.domaUsefulLinks) throw new Error("Firestore sync not ready yet");
      await window.domaUsefulLinks.addLink({ title, url, category: String(fd.get("category") || "").trim() || "General" });
      form.reset();
      form.classList.add("hidden");
    } catch (error) {
      console.error("Failed to add link:", error);
      alert("Could not add the link - check the browser console for details.");
    } finally {
      submitBtn.disabled = false;
    }
  });
}

function linkCardHtml(link, category) {
  const contact = [link.phone, link.email].filter(Boolean).join(" · ");
  // Everything a search should be able to match, even fields never shown on
  // the card itself (tag/description) - e.g. typing "phone service" should
  // find Mango Voice even though the card only displays its name and discount.
  const searchBlob = [category, link.title, link.tag, link.discount, link.description, contact]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return `
    <div class="link-card" data-id="${link.id}" data-search="${searchBlob.replace(/"/g, "&quot;")}">
      <div class="link-card-main">
        <a href="${link.url}" target="_blank" rel="noopener noreferrer" title="${link.url}">${link.title}</a>
        ${link.discount ? `<span class="link-discount">${link.discount}</span>` : ""}
        ${contact ? `<span class="link-contact">${contact}</span>` : ""}
      </div>
      <button type="button" class="link-copy" data-url="${link.url}" title="Copy link">⧉</button>
      <button type="button" class="checklist-delete" title="Remove">&times;</button>
    </div>`;
}

function renderLinks() {
  ensureLinksListeners();
  document.getElementById("linksEmpty").style.display = linksItems.length ? "none" : "block";

  const allCategories = [...new Set(linksItems.map((l) => l.category || "General"))];
  ensureLinksAddForm(allCategories);

  // Categories named "Parent: Child" (e.g. "Ebooks: AI") nest under a single
  // top-level "Parent" section with each "Child" as its own collapsible
  // sub-section inside - keeps a flat list of 15+ near-identical top-level
  // rows (every ebook/sponsor topic) from burying the handful of real
  // top-level categories (DOMA Site, SEO & Analytics, ...).
  const groups = new Map();
  linksItems.forEach((link) => {
    const cat = link.category || "General";
    const sepIdx = cat.indexOf(": ");
    const parent = sepIdx === -1 ? cat : cat.slice(0, sepIdx);
    const child = sepIdx === -1 ? null : cat.slice(sepIdx + 2);
    if (!groups.has(parent)) groups.set(parent, { flat: [], subgroups: new Map() });
    const group = groups.get(parent);
    if (child === null) {
      group.flat.push(link);
    } else {
      if (!group.subgroups.has(child)) group.subgroups.set(child, []);
      group.subgroups.get(child).push(link);
    }
  });

  const container = document.getElementById("linksList");
  container.innerHTML = [...groups.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([parentName, group]) => {
      const totalCount = group.flat.length + [...group.subgroups.values()].reduce((sum, links) => sum + links.length, 0);
      const collapsed = !!linksCollapsedCategories[parentName];
      const flatHtml = group.flat.length
        ? `<div class="links-grid">${group.flat.map((link) => linkCardHtml(link, parentName)).join("")}</div>`
        : "";
      const subgroupsHtml = [...group.subgroups.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([childName, links]) => {
          const subKey = `${parentName}: ${childName}`;
          const subCollapsed = !!linksCollapsedCategories[subKey];
          return `
            <div class="links-subcategory${subCollapsed ? " collapsed" : ""}" data-category="${subKey}">
              <button type="button" class="links-category-header links-subcategory-header">
                <span class="status-chevron">${subCollapsed ? "▸" : "▾"}</span>
                <h4>${childName}</h4>
                <span class="status-count">${links.length}</span>
              </button>
              <div class="links-grid">${links.map((link) => linkCardHtml(link, subKey)).join("")}</div>
            </div>`;
        })
        .join("");
      return `
        <div class="links-category${collapsed ? " collapsed" : ""}" data-category="${parentName}">
          <button type="button" class="links-category-header">
            <span class="status-chevron">${collapsed ? "▸" : "▾"}</span>
            <h3>${parentName}</h3>
            <span class="status-count">${totalCount}</span>
          </button>
          <div class="links-category-body">${flatHtml}${subgroupsHtml}</div>
        </div>`;
    })
    .join("");

  applyLinksSearch();
}

/* ---------- changelog ---------- */

let changelogItems = [];
let changelogListenersAttached = false;
let changelogCollapsedWeeks = {};

function ensureChangelogListeners() {
  if (changelogListenersAttached) return;
  changelogListenersAttached = true;

  document.getElementById("changelogList").addEventListener("click", async (event) => {
    const header = event.target.closest(".changelog-week-header");
    if (header) {
      const section = header.closest(".changelog-week");
      const week = section.dataset.week;
      changelogCollapsedWeeks[week] = !changelogCollapsedWeeks[week];
      section.classList.toggle("collapsed", changelogCollapsedWeeks[week]);
      return;
    }

    const commentToggle = event.target.closest(".changelog-comment-toggle");
    if (commentToggle) {
      const row = commentToggle.closest(".changelog-row");
      const commentsRow = row.nextElementSibling;
      commentsRow.classList.toggle("hidden");
      return;
    }
  });

  document.getElementById("changelogList").addEventListener("submit", async (event) => {
    const form = event.target.closest(".ticket-comment-form");
    if (!form) return;
    event.preventDefault();
    const comment = readCommentForm(form);
    if (!comment) return;
    const itemId = form.closest(".ticket-comments-body").dataset.id;
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    try {
      if (!window.domaChangelog) throw new Error("Firestore sync not ready yet");
      await window.domaChangelog.addComment(itemId, comment);
      form.reset();
    } catch (error) {
      console.error("Failed to add changelog comment:", error);
      alert("Could not post the comment - check the browser console for details.");
    } finally {
      submitBtn.disabled = false;
    }
  });

  whenFirestoreReady(() => {
    window.domaChangelog.subscribeItems((items) => {
      changelogItems = items;
      renderChangelog();
    });
  });
}

function changelogStatusPill(status) {
  return status === "in_progress" ? `<span class="pill info">In progress</span>` : `<span class="pill muted">Done</span>`;
}

function changelogRowHtml(item) {
  const linkCell = item.link
    ? `<a href="${safeUrl(item.link)}" target="_blank" rel="noopener noreferrer" class="changelog-link" title="${escAttr(item.link)}">View &#8599;</a>`
    : `<span class="changelog-link-empty">-</span>`;
  const commentCount = (item.comments || []).length;
  return `
    <tr class="changelog-row" data-id="${item.id}">
      <td class="changelog-date">${fullDate(item.date)}</td>
      <td><span class="checklist-topic">${escapeHtml(item.category || "General")}</span></td>
      <td class="changelog-title">${escapeHtml(item.title || "")}${item.detail ? `<span class="changelog-detail">${escapeHtml(item.detail)}</span>` : ""}</td>
      <td class="changelog-impact">${escapeHtml(item.impact || "-")}</td>
      <td>${changelogStatusPill(item.status)}</td>
      <td>${linkCell}</td>
      <td><button type="button" class="changelog-comment-toggle" title="Comment">&#128172;${commentCount ? ` ${commentCount}` : ""}</button></td>
    </tr>
    <tr class="changelog-comments-row hidden" data-id="${item.id}">
      <td colspan="7">${changelogCommentsBodyHtml(item)}</td>
    </tr>`;
}

function changelogCommentsBodyHtml(item) {
  const comments = item.comments || [];
  const list =
    comments
      .map(
        (c) =>
          `<div class="ticket-comment"><span class="ticket-comment-author">${escapeHtml(c.author || "Someone")}</span>${linkifyText(c.text)}<span class="ticket-comment-date">${fullDate(new Date(c.created_at).toISOString().slice(0, 10))}</span></div>`
      )
      .join("") || `<div class="ticket-comments-empty">No comments yet.</div>`;
  return `
    <div class="ticket-comments-body" data-id="${item.id}">
      <div class="ticket-comments-list">${list}</div>
      <form class="ticket-comment-form">
        <input type="text" name="author" placeholder="Your name" value="${(localStorage.getItem("domaCommenterName") || "").replace(/"/g, "&quot;")}" />
        <input type="text" name="text" placeholder="Question or note" required />
        <button type="submit">Post</button>
      </form>
    </div>`;
}

function renderChangelog() {
  ensureChangelogListeners();
  document.getElementById("changelogEmpty").style.display = changelogItems.length ? "none" : "block";

  const weeks = new Map();
  changelogItems.forEach((item) => {
    const week = item.week_label || "Undated";
    if (!weeks.has(week)) weeks.set(week, []);
    weeks.get(week).push(item);
  });

  // Newest week first, newest item first inside each week.
  const latest = (items) => items.reduce((m, i) => (String(i.date || "") > m ? String(i.date || "") : m), "");
  weeks.forEach((items) => items.sort((a, b) => String(b.date || "").localeCompare(String(a.date || ""))));
  const ordered = [...weeks.entries()].sort((a, b) => latest(b[1]).localeCompare(latest(a[1])));
  const container = document.getElementById("changelogList");
  container.innerHTML = ordered
    .map(([week, items]) => {
      const collapsed = !!changelogCollapsedWeeks[week];
      return `
        <div class="links-category changelog-week${collapsed ? " collapsed" : ""}" data-week="${week}">
          <button type="button" class="links-category-header changelog-week-header">
            <span class="status-chevron">${collapsed ? "▸" : "▾"}</span>
            <h3>${week}</h3>
            <span class="status-count">${items.length}</span>
          </button>
          <div class="links-category-body">
            <div class="changelog-table-wrap">
              <table class="changelog-table">
                <thead>
                  <tr>
                    <th>Date</th><th>Category</th><th>What we did</th><th>Impact</th><th>Status</th><th>Link</th><th></th>
                  </tr>
                </thead>
                <tbody>${items.map(changelogRowHtml).join("")}</tbody>
              </table>
            </div>
          </div>
        </div>`;
    })
    .join("");
}

// "Show sponsor/offer queries" checkbox. Renders nothing when the backend
// hasn't flagged any query as not relevant (or hasn't sent the flag yet).
function renderSponsorToggle(containerId, gaps) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const hidden = hiddenGapCount(gaps);
  if (!hidden) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = `<label class="gap-toggle"><input type="checkbox" data-sponsor-toggle ${showSponsorQueries ? "checked" : ""}> Show sponsor/offer queries (${hidden})</label>`;
}

let sponsorToggleAttached = false;
function ensureSponsorToggleListener() {
  if (sponsorToggleAttached) return;
  sponsorToggleAttached = true;
  document.addEventListener("change", (event) => {
    if (!event.target.matches("[data-sponsor-toggle]")) return;
    showSponsorQueries = event.target.checked;
    // The re-render replaces the checkbox; keep keyboard focus where it was.
    const host = event.target.closest("[id]");
    safeRender("SEO", renderSeo);
    safeRender("Content Suggestions", renderContentIdeas);
    safeRender("Calendar suggestions", renderCalendarSuggestions);
    if (host) document.querySelector(`#${host.id} [data-sponsor-toggle]`)?.focus();
  });
}

function renderContentIdeas() {
  ensureSponsorToggleListener();
  const suggestions = dashboard.content_suggestions || { available: false, content_gaps: [], top_ebooks: [], top_blog_posts: [], best_post_times: { available: false, by_day: [], by_period: [] } };
  document.getElementById("contentIdeasEmpty").style.display = suggestions.available ? "none" : "block";

  renderSponsorToggle("ideasGapsToggle", suggestions.content_gaps);
  renderTable(
    "ideasContentGapsTable",
    visibleGaps(suggestions.content_gaps),
    (g) => `<tr><td>${escapeHtml(g.query)}</td><td class="gap-action">${g.action ? escapeHtml(g.action) : "-"}</td><td>${escapeHtml(titleFromUrl(g.page))}</td><td>${Number(g.position).toFixed(1)}</td><td>${number(g.impressions)}</td></tr>`,
    "No content gaps found yet - see SEO tab."
  );
  renderTable(
    "topEbooksTable",
    (suggestions.top_ebooks || []).filter(isEbookRow),
    (p) => `<tr><td>${p.page_title || titleFromUrl(p.page_path)}</td><td>${number(p.sessions)}</td><td>${number(p.page_views)}</td></tr>`,
    "No ebook page data yet."
  );
  renderTable(
    "topBlogPostsTable",
    suggestions.top_blog_posts,
    (p) => `<tr><td>${p.page_title || titleFromUrl(p.page_path)}</td><td>${number(p.sessions)}</td><td>${number(p.page_views)}</td></tr>`,
    "No blog post data yet."
  );

  const bestTimes = suggestions.best_post_times || { available: false, by_day: [], by_period: [] };
  document.getElementById("contentBestTimesEmpty").style.display = bestTimes.available ? "none" : "block";
  renderTable(
    "contentBestDayTable",
    bestTimes.by_day || [],
    (d) => `<tr><td>${d.day}</td><td>${d.avg_engagement}</td><td>${d.post_count}</td></tr>`
  );
  renderTable(
    "contentBestPeriodTable",
    bestTimes.by_period || [],
    (p) => `<tr><td>${p.period}</td><td>${p.avg_engagement}</td><td>${p.post_count}</td></tr>`
  );
}

// Each tab's render is independent - one throwing (a bad reference, a null
// element lookup) shouldn't blank out every tab that happens to run after
// it in the list below, the way an uncaught error did once already.
function safeRender(name, fn) {
  try {
    fn();
  } catch (error) {
    console.error(`Failed to render ${name}:`, error);
  }
}

function renderAll() {
  if (!dashboard) {
    document.getElementById("app").innerHTML = `<div class="panel"><div class="empty">Could not load the dashboard. Make sure the local server is running (RUN_DASHBOARD.bat) and data has been synced.</div></div>`;
    return;
  }
  safeRender("Overview", renderOverview);
  safeRender("SEO", renderSeo);
  safeRender("Competitors", renderCompetitors);
  safeRender("Where DOMA stands", renderWhereDomaStands);
  safeRender("Blog", renderBlog);
  safeRender("Content Suggestions", renderContentIdeas);
  safeRender("Content Calendar", renderContentCalendar);
  safeRender("Leads", renderLeads);
  safeRender("Partners", renderPartners);
  safeRender("Most clicked buttons", renderCtaClicks);
  safeRender("Social", renderSocial);
  safeRender("Team & Meetings", renderTeam);
  safeRender("Weekly Recap", renderWeeklyRecap);
  safeRender("To Do", renderTodoBoard);
  safeRender("Useful Links", renderLinks);
  safeRender("Library", renderLibrary);
  safeRender("SOPs", renderSops);
  safeRender("Changelog", renderChangelog);

  const synced = [
    dashboard.search_console.last_synced_at,
    dashboard.ga4.last_synced_at,
    dashboard.ghl.last_synced_at,
    dashboard.social.last_synced_at,
  ].filter(Boolean).sort().at(-1);
  const lastSyncedEl = document.getElementById("lastSynced");
  if (lastSyncedEl) {
    lastSyncedEl.textContent = synced ? `Synced ${new Date(synced).toLocaleString("en-US")}` : "Not synced yet";
  }

  const comparisonEl = document.getElementById("comparisonNote");
  if (comparisonEl) {
    // Say which days the numbers cover, then what they are compared with.
    comparisonEl.textContent = dashboard.previous_start_date
      ? `${dateRange(dashboard.start_date, dashboard.end_date)} · compared with ${dateRange(dashboard.previous_start_date, dashboard.previous_end_date)}`
      : dateRange(dashboard.start_date, dashboard.end_date);
  }
}

/* ---------- shared bits for the Library and SOPs tabs ---------- */

function escAttr(text) {
  return escapeHtml(String(text ?? "")).replace(/"/g, "&quot;");
}

// Only http(s) links from Firestore/API data go into href (no javascript: URLs).
function safeUrl(url) {
  const u = String(url ?? "").trim();
  return /^https?:\/\//i.test(u) ? escAttr(u) : "#";
}

// A brand-new Firestore collection has no rule yet and fails with
// permission-denied; say so on the tab instead of showing a silently empty list.
const syncErrors = new Map();
window.addEventListener("doma-sync-error", (event) => {
  syncErrors.set(event.detail.collection, event.detail.code);
  safeRender("Library", renderLibrary);
  safeRender("SOPs", renderSops);
  safeRender("Partners", renderPartnerBoard);
});

function showSyncNotice(elementId, collectionName) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const code = syncErrors.get(collectionName);
  if (!code) {
    el.hidden = true;
    return;
  }
  el.innerHTML =
    code === "permission-denied"
      ? `This tab needs one Firestore rule before it can save anything. In the Firebase console (Firestore, then Rules) add <code>match /${collectionName}/{doc} { allow read, write: if true; }</code> and publish.`
      : `Live sync for this tab stopped (${escapeHtml(code)}). Reload the page to retry.`;
  el.hidden = false;
}

function uniqueCaseInsensitive(values) {
  const seen = new Map();
  values.forEach((v) => {
    const text = String(v || "").trim();
    if (text && !seen.has(text.toLowerCase())) seen.set(text.toLowerCase(), text);
  });
  return [...seen.values()].sort((a, b) => a.localeCompare(b));
}

function markActivePills(containerId, activeValue) {
  document.querySelectorAll(`#${containerId} button`).forEach((b) => b.classList.toggle("active", b.dataset.value === activeValue));
}

function linkedTitleHtml(title, url) {
  if (!url) return escapeHtml(title);
  return (
    `<a href="${escAttr(url)}" target="_blank" rel="noopener noreferrer" title="${escAttr(url)}">${escapeHtml(title)}</a>` +
    `<button type="button" class="desc-link-copy" data-url="${escAttr(url)}" title="Copy link">⧉</button>`
  );
}

function shortDateFromMs(ms) {
  return ms ? fullDate(new Date(ms).toISOString().slice(0, 10)) : "";
}

/* ---------- library (articles collected ahead of time) ---------- */

const LIBRARY_STATUSES = [
  ["idea", "Idea"],
  ["drafting", "Drafting"],
  ["ready", "Ready to publish"],
  ["scheduled", "Scheduled"],
  ["published", "Published"],
];
const LIBRARY_STATUS_LABELS = Object.fromEntries(LIBRARY_STATUSES);
const LIBRARY_PAGE_SIZE = 50;

let libraryItems = [];
let libraryFilters = { topic: "all", author: "all", status: "all", search: "", sort: "newest" };
let libraryEditingId = null;
let libraryShown = LIBRARY_PAGE_SIZE;
let libraryListenersAttached = false;

function libraryTopics(item) {
  return Array.isArray(item.topics) ? item.topics : [];
}

function resetLibraryForm() {
  const form = document.getElementById("libraryAddForm");
  libraryEditingId = null;
  form.reset();
  form.querySelector('[name="status"]').value = "idea";
  form.classList.add("hidden");
  form.querySelector('button[type="submit"]').textContent = "Add article";
}

function openLibraryEdit(itemId) {
  const item = libraryItems.find((i) => i.id === itemId);
  const form = document.getElementById("libraryAddForm");
  if (!item || !form) return;
  libraryEditingId = itemId;
  form.querySelector('[name="title"]').value = item.title || "";
  form.querySelector('[name="author"]').value = item.author || "";
  form.querySelector('[name="topics"]').value = libraryTopics(item).join(", ");
  form.querySelector('[name="status"]').value = item.status || "idea";
  form.querySelector('[name="target_date"]').value = item.target_date || "";
  form.querySelector('[name="url"]').value = item.url || "";
  form.querySelector('[name="notes"]').value = item.notes || "";
  form.querySelector('button[type="submit"]').textContent = "Save changes";
  form.classList.remove("hidden");
  form.scrollIntoView({ behavior: "smooth", block: "center" });
}

function ensureLibraryListeners() {
  if (libraryListenersAttached) return;
  libraryListenersAttached = true;
  const form = document.getElementById("libraryAddForm");
  form.querySelector('[name="status"]').innerHTML = LIBRARY_STATUSES.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");

  document.getElementById("libraryAddToggle").addEventListener("click", () => form.classList.toggle("hidden"));
  document.getElementById("libraryAddCancel").addEventListener("click", resetLibraryForm);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    const title = String(fd.get("title") || "").trim();
    if (!title) return;
    const fields = {
      title,
      author: String(fd.get("author") || "").trim() || null,
      topics: uniqueCaseInsensitive(String(fd.get("topics") || "").split(",")),
      status: String(fd.get("status") || "idea"),
      url: String(fd.get("url") || "").trim() || null,
      target_date: String(fd.get("target_date") || "") || null,
      notes: String(fd.get("notes") || "").trim() || null,
    };
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    try {
      if (!window.domaLibrary) throw new Error("Firestore sync not ready yet");
      if (libraryEditingId) await window.domaLibrary.updateArticle(libraryEditingId, fields);
      else await window.domaLibrary.addArticle(fields);
      resetLibraryForm();
    } catch (error) {
      console.error("Failed to save article:", error);
      alert("Could not save the article - check the browser console for details.");
    } finally {
      submitBtn.disabled = false;
    }
  });

  document.getElementById("librarySearch").addEventListener("input", (event) => {
    libraryFilters.search = event.target.value;
    libraryShown = LIBRARY_PAGE_SIZE;
    renderLibrary();
  });
  document.getElementById("librarySort").addEventListener("change", (event) => {
    libraryFilters.sort = event.target.value;
    renderLibrary();
  });
  document.getElementById("libraryMore").addEventListener("click", () => {
    libraryShown += LIBRARY_PAGE_SIZE;
    renderLibrary();
  });

  const list = document.getElementById("libraryList");
  list.addEventListener("click", async (event) => {
    const row = event.target.closest(".lib-row");
    if (!row) return;
    if (event.target.closest(".lib-edit")) return openLibraryEdit(row.dataset.id);
    if (event.target.closest(".lib-delete")) {
      const item = libraryItems.find((i) => i.id === row.dataset.id);
      if (!confirm(`Remove "${item ? item.title : "this article"}" from the Library?`)) return;
      try {
        await window.domaLibrary.deleteArticle(row.dataset.id);
      } catch (error) {
        console.error("Failed to delete article:", error);
      }
    }
  });
  list.addEventListener("change", async (event) => {
    const select = event.target.closest(".lib-status-select");
    if (!select) return;
    try {
      await window.domaLibrary.updateArticle(select.closest(".lib-row").dataset.id, { status: select.value });
    } catch (error) {
      console.error("Failed to change article status:", error);
    }
  });

  whenFirestoreReady(() => {
    window.domaLibrary.subscribeArticles((items) => {
      libraryItems = items;
      renderLibrary();
    });
  });
}

function libraryRowHtml(item) {
  const topics = libraryTopics(item).map((t) => `<span class="checklist-topic">${escapeHtml(t)}</span>`).join("");
  const options = LIBRARY_STATUSES.map(([v, l]) => `<option value="${v}"${v === item.status ? " selected" : ""}>${l}</option>`).join("");
  const dates = [
    item.target_date ? `Planned ${fullDate(item.target_date)}` : "",
    item.added_at ? `Added ${shortDateFromMs(item.added_at)}` : "",
  ].filter(Boolean);
  return `
    <div class="lib-row status-${escAttr(item.status || "idea")}" data-id="${escAttr(item.id)}">
      <div class="lib-main">
        <div class="lib-title">${linkedTitleHtml(item.title || "Untitled", item.url)}</div>
        <div class="lib-meta">
          ${item.author ? `<span class="checklist-owner">${escapeHtml(item.author)}</span>` : ""}${topics}
          ${dates.length ? `<span class="lib-date">${dates.join(" · ")}</span>` : ""}
        </div>
        ${item.notes ? `<div class="checklist-context">${linkifyText(item.notes)}</div>` : ""}
      </div>
      <select class="lib-status-select" aria-label="Status">${options}</select>
      <button type="button" class="checklist-edit lib-edit" title="Edit">&#9998;</button>
      <button type="button" class="checklist-delete lib-delete" title="Remove">&times;</button>
    </div>`;
}

function sortLibraryItems(items, sort) {
  const byAdded = (a, b) => (b.added_at || 0) - (a.added_at || 0);
  const sorted = [...items];
  if (sort === "title") return sorted.sort((a, b) => String(a.title || "").localeCompare(String(b.title || "")));
  if (sort === "planned") {
    return sorted.sort((a, b) => {
      if (a.target_date && b.target_date) return a.target_date < b.target_date ? -1 : a.target_date > b.target_date ? 1 : byAdded(a, b);
      if (a.target_date) return -1;
      if (b.target_date) return 1;
      return byAdded(a, b);
    });
  }
  return sorted.sort(byAdded);
}

function renderLibrary() {
  ensureLibraryListeners();
  showSyncNotice("libraryNotice", "library_articles");

  const items = libraryItems;
  const countStatus = (...statuses) => items.filter((i) => statuses.includes(i.status)).length;
  renderCards("libraryCards", [
    { label: "Articles", value: number(items.length) },
    { label: "Ready to publish", value: number(countStatus("ready")) },
    { label: "In the pipeline", value: number(countStatus("idea", "drafting", "scheduled")), hint: "idea, drafting or scheduled" },
    { label: "Published", value: number(countStatus("published")) },
  ]);

  const topics = uniqueCaseInsensitive(items.flatMap(libraryTopics));
  const authors = uniqueCaseInsensitive(items.map((i) => i.author));
  document.getElementById("libraryAuthorOptions").innerHTML = authors.map((a) => `<option value="${escAttr(a)}">`).join("");
  document.getElementById("libraryTopicOptions").innerHTML = topics.map((t) => `<option value="${escAttr(t)}">`).join("");

  buildFilterPills("libraryTopicFilter", topics, null, (value) => {
    libraryFilters.topic = value;
    libraryShown = LIBRARY_PAGE_SIZE;
    renderLibrary();
  });
  buildFilterPills("libraryAuthorFilter", authors, null, (value) => {
    libraryFilters.author = value;
    libraryShown = LIBRARY_PAGE_SIZE;
    renderLibrary();
  });
  buildFilterPills("libraryStatusFilter", LIBRARY_STATUSES.map(([v]) => v), (v) => LIBRARY_STATUS_LABELS[v], (value) => {
    libraryFilters.status = value;
    libraryShown = LIBRARY_PAGE_SIZE;
    renderLibrary();
  });
  markActivePills("libraryTopicFilter", libraryFilters.topic);
  markActivePills("libraryAuthorFilter", libraryFilters.author);
  markActivePills("libraryStatusFilter", libraryFilters.status);

  const query = libraryFilters.search.trim().toLowerCase();
  const filtered = items.filter((item) => {
    if (libraryFilters.topic !== "all" && !libraryTopics(item).some((t) => t.toLowerCase() === libraryFilters.topic.toLowerCase())) return false;
    if (libraryFilters.author !== "all" && String(item.author || "").toLowerCase() !== libraryFilters.author.toLowerCase()) return false;
    if (libraryFilters.status !== "all" && item.status !== libraryFilters.status) return false;
    if (!query) return true;
    return [item.title, item.author, libraryTopics(item).join(" "), item.notes].join(" ").toLowerCase().includes(query);
  });
  const sorted = sortLibraryItems(filtered, libraryFilters.sort);
  const visible = sorted.slice(0, libraryShown);

  const emptyEl = document.getElementById("libraryEmpty");
  emptyEl.textContent = items.length ? "No articles match these filters." : "No articles yet. Add the first one above.";
  emptyEl.style.display = sorted.length ? "none" : "block";
  document.getElementById("libraryList").innerHTML = visible.map(libraryRowHtml).join("");
  const moreBtn = document.getElementById("libraryMore");
  moreBtn.hidden = sorted.length <= visible.length;
  moreBtn.textContent = `Show more (${sorted.length - visible.length} left)`;
}

/* ---------- SOP development (ongoing project, reviewed in the weekly call) ---------- */

const SOP_STATUSES = [
  ["to_build", "To build"],
  ["in_progress", "In progress"],
  ["review", "Draft ready for review"],
  ["needs_update", "Needs update"],
  ["done", "Up to date"],
];
const SOP_STATUS_LABELS = Object.fromEntries(SOP_STATUSES);
const SOP_SORT_ORDER = ["in_progress", "review", "needs_update", "to_build", "done"];
const SOP_DEFAULT_AREAS = ["Content", "Social", "Sponsors", "Ebooks", "Newsletter", "Team", "Dashboard", "Website"];

let sopItems = [];
let sopFilters = { area: "all", owner: "all", status: "all" };
let sopEditingId = null;
let sopReviewCollapsed = false;
let sopListenersAttached = false;

function sopOptionsHtml(selected) {
  return SOP_STATUSES.map(([v, l]) => `<option value="${v}"${v === selected ? " selected" : ""}>${l}</option>`).join("");
}

function resetSopForms() {
  const form = document.getElementById("sopAddForm");
  sopEditingId = null;
  form.reset();
  form.querySelector('[name="status"]').value = "to_build";
  form.classList.add("hidden");
  form.querySelector('button[type="submit"]').textContent = "Add SOP";
  const bulk = document.getElementById("sopBulkForm");
  bulk.reset();
  bulk.querySelector('[name="status"]').value = "needs_update";
  bulk.classList.add("hidden");
}

function openSopEdit(itemId) {
  const item = sopItems.find((i) => i.id === itemId);
  const form = document.getElementById("sopAddForm");
  if (!item || !form) return;
  sopEditingId = itemId;
  form.querySelector('[name="title"]').value = item.title || "";
  form.querySelector('[name="area"]').value = item.area || "";
  form.querySelector('[name="owner"]').value = item.owner || "";
  form.querySelector('[name="status"]').value = item.status || "to_build";
  form.querySelector('[name="source_url"]').value = item.source_url || "";
  form.querySelector('[name="needs"]').value = item.needs || "";
  form.querySelector('[name="notes"]').value = item.notes || "";
  form.querySelector('button[type="submit"]').textContent = "Save changes";
  document.getElementById("sopBulkForm").classList.add("hidden");
  form.classList.remove("hidden");
  form.scrollIntoView({ behavior: "smooth", block: "center" });
}

function parseSopLines(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [first, ...rest] = line.split("|");
      const maybeUrl = rest.join("|").trim();
      return /^https?:\/\//i.test(maybeUrl) ? { title: first.trim(), source_url: maybeUrl } : { title: line, source_url: null };
    })
    .filter((entry) => entry.title);
}

function ensureSopListeners() {
  if (sopListenersAttached) return;
  sopListenersAttached = true;
  const form = document.getElementById("sopAddForm");
  const bulk = document.getElementById("sopBulkForm");
  form.querySelector('[name="status"]').innerHTML = sopOptionsHtml("to_build");
  bulk.querySelector('[name="status"]').innerHTML = sopOptionsHtml("needs_update");

  document.getElementById("sopAddToggle").addEventListener("click", () => {
    bulk.classList.add("hidden");
    form.classList.toggle("hidden");
  });
  document.getElementById("sopBulkToggle").addEventListener("click", () => {
    form.classList.add("hidden");
    bulk.classList.toggle("hidden");
  });
  document.getElementById("sopAddCancel").addEventListener("click", resetSopForms);
  document.getElementById("sopBulkCancel").addEventListener("click", resetSopForms);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    const title = String(fd.get("title") || "").trim();
    if (!title) return;
    const fields = {
      title,
      area: String(fd.get("area") || "").trim() || null,
      owner: String(fd.get("owner") || "").trim() || null,
      status: String(fd.get("status") || "to_build"),
      source_url: String(fd.get("source_url") || "").trim() || null,
      needs: String(fd.get("needs") || "").trim() || null,
      notes: String(fd.get("notes") || "").trim() || null,
    };
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    try {
      if (!window.domaSops) throw new Error("Firestore sync not ready yet");
      if (sopEditingId) {
        const existing = sopItems.find((i) => i.id === sopEditingId);
        // Leave status out when it didn't change, so editing a finished SOP's
        // notes doesn't restamp its completed date.
        if (existing && existing.status === fields.status) delete fields.status;
        await window.domaSops.updateSop(sopEditingId, fields);
      } else {
        await window.domaSops.addSop(fields);
      }
      resetSopForms();
    } catch (error) {
      console.error("Failed to save SOP:", error);
      alert("Could not save the SOP - check the browser console for details.");
    } finally {
      submitBtn.disabled = false;
    }
  });

  bulk.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(bulk);
    const entries = parseSopLines(fd.get("lines"));
    if (!entries.length) return;
    const shared = {
      area: String(fd.get("area") || "").trim() || null,
      owner: String(fd.get("owner") || "").trim() || null,
      status: String(fd.get("status") || "needs_update"),
    };
    const submitBtn = bulk.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    try {
      if (!window.domaSops) throw new Error("Firestore sync not ready yet");
      await Promise.all(entries.map((entry) => window.domaSops.addSop({ ...shared, ...entry, needs: null, notes: null })));
      resetSopForms();
    } catch (error) {
      console.error("Failed to add SOPs:", error);
      alert("Could not add the SOPs - check the browser console for details.");
    } finally {
      submitBtn.disabled = false;
    }
  });

  document.getElementById("sopReview").addEventListener("click", (event) => {
    if (!event.target.closest(".recap-header")) return;
    sopReviewCollapsed = !sopReviewCollapsed;
    document.getElementById("sopReview").classList.toggle("collapsed", sopReviewCollapsed);
    const chevron = document.querySelector("#sopReview .status-chevron");
    if (chevron) chevron.textContent = sopReviewCollapsed ? "▸" : "▾";
  });

  const list = document.getElementById("sopList");
  list.addEventListener("click", async (event) => {
    const row = event.target.closest(".sop-row");
    if (!row) return;
    if (event.target.closest(".sop-edit")) return openSopEdit(row.dataset.id);
    if (event.target.closest(".sop-delete")) {
      const item = sopItems.find((i) => i.id === row.dataset.id);
      if (!confirm(`Remove "${item ? item.title : "this SOP"}" from SOP Development?`)) return;
      try {
        await window.domaSops.deleteSop(row.dataset.id);
      } catch (error) {
        console.error("Failed to delete SOP:", error);
      }
    }
  });
  list.addEventListener("change", async (event) => {
    const select = event.target.closest(".sop-status-select");
    if (!select) return;
    try {
      await window.domaSops.updateSop(select.closest(".sop-row").dataset.id, { status: select.value });
    } catch (error) {
      console.error("Failed to change SOP status:", error);
    }
  });

  whenFirestoreReady(() => {
    window.domaSops.subscribeSops((items) => {
      sopItems = items;
      renderSops();
    });
  });
}

function sopRowHtml(item) {
  const updated = shortDateFromMs(item.status_changed_at || item.updated_at || item.created_at);
  return `
    <div class="sop-row status-${escAttr(item.status || "to_build")}" data-id="${escAttr(item.id)}">
      <div class="lib-main">
        <div class="lib-title">${linkedTitleHtml(item.title || "Untitled", item.source_url)}</div>
        <div class="lib-meta">
          ${item.area ? `<span class="checklist-topic">${escapeHtml(item.area)}</span>` : ""}
          ${item.owner ? `<span class="checklist-owner">${escapeHtml(item.owner)}</span>` : ""}
          ${updated ? `<span class="lib-date">${item.status === "done" ? "Finished" : "Updated"} ${updated}</span>` : ""}
        </div>
        ${item.needs ? `<div class="sop-needs"><strong>Needs:</strong> ${linkifyText(item.needs)}</div>` : ""}
        ${item.notes ? `<div class="checklist-context">${linkifyText(item.notes)}</div>` : ""}
      </div>
      <select class="lib-status-select sop-status-select" aria-label="Status">${sopOptionsHtml(item.status)}</select>
      <button type="button" class="checklist-edit sop-edit" title="Edit">&#9998;</button>
      <button type="button" class="checklist-delete sop-delete" title="Remove">&times;</button>
    </div>`;
}

function sopReviewBlock(title, entries, emptyText) {
  const rows = entries.length ? entries.map((html) => `<li>${html}</li>`).join("") : `<li class="recap-none">${emptyText}</li>`;
  return `
    <div class="recap-owner-block">
      <div class="recap-owner-header">${title} <span class="status-count">${entries.length}</span></div>
      <ul class="recap-owner-list">${rows}</ul>
    </div>`;
}

function renderSopReview() {
  const el = document.getElementById("sopReview");
  if (!el) return;
  el.classList.toggle("collapsed", sopReviewCollapsed);
  const weekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const label = (item) => `${escapeHtml(item.title)}${item.owner ? ` <span class="recap-owner-tag">${escapeHtml(item.owner)}</span>` : ""}`;
  const by = (status) => sopItems.filter((i) => i.status === status);

  const doneThisWeek = sopItems.filter((i) => i.status === "done" && (i.completed_at || 0) >= weekAgo);
  const upNext = [...by("needs_update"), ...by("to_build")];
  const needs = sopItems.filter((i) => i.needs && i.status !== "done");

  el.innerHTML = `
    <button type="button" class="recap-header">
      <span class="status-chevron">${sopReviewCollapsed ? "▸" : "▾"}</span><h2>Weekly review</h2>
      <span class="panel-meta">What to go through on the Monday call</span>
    </button>
    <div class="recap-body"><div class="recap-owner-grid">
      ${sopReviewBlock("Finished in the last 7 days", doneThisWeek.map(label), "Nothing finished yet")}
      ${sopReviewBlock("In progress", by("in_progress").map(label), "Nothing in progress")}
      ${sopReviewBlock("Draft ready for review", by("review").map(label), "No drafts waiting")}
      ${sopReviewBlock("Up next", upNext.slice(0, 8).map(label), "Nothing queued")}
      ${sopReviewBlock("What we need from each other", needs.map((i) => `<strong>${escapeHtml(i.title)}</strong>: ${linkifyText(i.needs)}`), "Nothing needed right now")}
    </div></div>`;
}

function renderSops() {
  ensureSopListeners();
  showSyncNotice("sopNotice", "sop_items");

  const countStatus = (status) => sopItems.filter((i) => i.status === status).length;
  renderCards(
    "sopCards",
    SOP_STATUSES.map(([value, text]) => ({ label: text, value: number(countStatus(value)) }))
  );

  const areas = uniqueCaseInsensitive([...SOP_DEFAULT_AREAS, ...sopItems.map((i) => i.area)]);
  const usedAreas = uniqueCaseInsensitive(sopItems.map((i) => i.area));
  const owners = uniqueCaseInsensitive([...TEAM_KNOWN_OWNERS, ...sopItems.map((i) => i.owner)]);
  document.getElementById("sopAreaOptions").innerHTML = areas.map((a) => `<option value="${escAttr(a)}">`).join("");
  document.getElementById("sopOwnerOptions").innerHTML = owners.map((o) => `<option value="${escAttr(o)}">`).join("");

  buildFilterPills("sopAreaFilter", usedAreas, null, (value) => {
    sopFilters.area = value;
    renderSops();
  });
  buildFilterPills("sopOwnerFilter", owners, null, (value) => {
    sopFilters.owner = value;
    renderSops();
  });
  buildFilterPills("sopStatusFilter", SOP_STATUSES.map(([v]) => v), (v) => SOP_STATUS_LABELS[v], (value) => {
    sopFilters.status = value;
    renderSops();
  });
  markActivePills("sopAreaFilter", sopFilters.area);
  markActivePills("sopOwnerFilter", sopFilters.owner);
  markActivePills("sopStatusFilter", sopFilters.status);

  renderSopReview();

  const filtered = sopItems
    .filter((item) => {
      if (sopFilters.area !== "all" && String(item.area || "").toLowerCase() !== sopFilters.area.toLowerCase()) return false;
      if (sopFilters.owner !== "all" && String(item.owner || "").toLowerCase() !== sopFilters.owner.toLowerCase()) return false;
      if (sopFilters.status !== "all" && item.status !== sopFilters.status) return false;
      return true;
    })
    .sort((a, b) => {
      const order = SOP_SORT_ORDER.indexOf(a.status) - SOP_SORT_ORDER.indexOf(b.status);
      return order || String(a.title || "").localeCompare(String(b.title || ""));
    });

  const emptyEl = document.getElementById("sopEmpty");
  emptyEl.textContent = sopItems.length ? "No SOPs match these filters." : "No SOPs yet. Add one above, or paste a list.";
  emptyEl.style.display = filtered.length ? "none" : "block";
  document.getElementById("sopList").innerHTML = filtered.map(sopRowHtml).join("");
}

/* ---------- where DOMA stands (Competitors tab) ---------- */

function renderWhereDomaStands() {
  const data = dashboard.serp_where_doma_stands;
  const rows = data?.rows || [];
  const table = document.getElementById("whereDomaTable");
  if (!table) return;
  document.getElementById("whereDomaEmpty").style.display = rows.length ? "none" : "block";
  table.closest(".table-wrap").hidden = !rows.length;
  const anyFallback = rows.some((r) => r.google_window === "last_90_days");
  const note = document.getElementById("whereDomaNote");
  if (note) {
    note.textContent = `(our main searches: DOMA on Google from Search Console for the selected range${anyFallback ? ", or the last 90 days where marked *" : ""}; who ranks on Brave from the latest daily check${data?.checked_at ? `, ${fullDate(String(data.checked_at).slice(0, 10))}` : ""})`;
  }
  renderTable("whereDomaTable", rows, (r) => {
    const google = r.google_position === null || r.google_position === undefined ? "-" : `${Number(r.google_position).toFixed(1)}${r.google_window === "last_90_days" ? "*" : ""}`;
    const googleTitle = r.google_window === "last_90_days" ? "No impressions in this range, so this is the last 90 days" : "";
    return `<tr><td>${escapeHtml(r.query)}</td><td title="${escAttr(googleTitle)}">${google}</td><td>${number(r.impressions)}</td><td>${number(r.clicks)}</td><td>${r.brave_position ?? "-"}</td><td>${r.ahead?.length ? escapeHtml(r.ahead.join(", ")) : "-"}</td><td>${escapeHtml(r.action)}</td></tr>`;
  });
}

/* ---------- partners (sponsor and partner request pipeline) ---------- */

// Stages from the 2026-10-12 team meeting. Kyle (2026-10-15): "this should be
// our SOP for handling sponsor requests going forward". Signed and Declined
// share the last column.
const PARTNER_STAGES = [
  ["new", "New request"],
  ["vetting", "Juli vetting"],
  ["packet_sent", "Packet sent"],
  ["call", "Call with Kyle"],
  ["signed", "Signed"],
  ["declined", "Declined"],
];
const PARTNER_COLUMNS = [
  { key: "new", label: "New request", stages: ["new"] },
  { key: "vetting", label: "Juli vetting", stages: ["vetting"] },
  { key: "packet_sent", label: "Packet sent", stages: ["packet_sent"] },
  { key: "call", label: "Call with Kyle", stages: ["call"] },
  { key: "closed", label: "Signed / Declined", stages: ["signed", "declined"] },
];
// Who usually holds a request at each stage, and the usual next step. Applied
// only when the current owner / next step is still the previous stage's
// default, so a hand-typed owner or next step is never overwritten.
const PARTNER_STAGE_DEFAULTS = {
  new: { owner: "Juli", next_step: "Juli to vet the company" },
  vetting: { owner: "Juli", next_step: "Juli to vet the company" },
  packet_sent: { owner: "Michelle", next_step: "Wait for their reply to the packet" },
  call: { owner: "Kyle", next_step: "Set the call date with Kyle" },
  signed: { owner: "Michelle", next_step: "Follow the New sponsor onboarding SOP" },
  declined: { owner: null, next_step: null },
};
const PARTNER_CLOSED_DAYS = 30;

let partnerItems = [];
let partnerFilters = { owner: "all", showOld: false };
let partnerEditingId = null;
let partnerListenersAttached = false;

// partner_requests is writable by anyone with the page open, so only http(s)
// links are ever turned into an href.
function safeHttpUrl(raw, { allowBare = false } = {}) {
  let text = String(raw || "").trim();
  if (!text) return null;
  if (allowBare && !/^[a-z]+:/i.test(text)) text = `https://${text}`;
  try {
    const url = new URL(text);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function partnerStageOptionsHtml(selected) {
  return PARTNER_STAGES.map(([v, l]) => `<option value="${v}"${v === selected ? " selected" : ""}>${l}</option>`).join("");
}

function partnerStageChangeFields(item, stage) {
  const fields = { stage };
  const before = PARTNER_STAGE_DEFAULTS[item.stage || "new"] || {};
  const after = PARTNER_STAGE_DEFAULTS[stage] || {};
  if (!item.owner || item.owner === before.owner) fields.owner = after.owner ?? null;
  if (!item.next_step || item.next_step === before.next_step) fields.next_step = after.next_step ?? null;
  return fields;
}

function partnerStageAgeDays(item) {
  const dates = item.stage_dates || {};
  const ms = dates[item.stage || "new"] || item.created_at;
  return ms ? Math.floor((Date.now() - ms) / 86400000) : null;
}

function resetPartnerForm() {
  const form = document.getElementById("partnerForm");
  partnerEditingId = null;
  form.reset();
  form.querySelector('[name="stage"]').value = "new";
  form.querySelector('[name="owner"]').value = "Juli";
  form.querySelector('[name="next_step"]').value = PARTNER_STAGE_DEFAULTS.new.next_step;
  form.querySelector('[name="request_date"]').value = new Date().toISOString().slice(0, 10);
  form.querySelector('button[type="submit"]').textContent = "Add request";
  form.classList.add("hidden");
}

function openPartnerEdit(itemId) {
  const item = partnerItems.find((i) => i.id === itemId);
  const form = document.getElementById("partnerForm");
  if (!item || !form) return;
  partnerEditingId = itemId;
  ["company", "website", "request_date", "owner", "next_step", "notes"].forEach((name) => {
    form.querySelector(`[name="${name}"]`).value = item[name] || "";
  });
  form.querySelector('[name="stage"]').value = item.stage || "new";
  form.querySelector('button[type="submit"]').textContent = "Save changes";
  form.classList.remove("hidden");
  form.scrollIntoView({ behavior: "smooth", block: "center" });
  form.querySelector('[name="company"]').focus();
}

function ensurePartnerListeners() {
  if (partnerListenersAttached) return;
  partnerListenersAttached = true;
  const form = document.getElementById("partnerForm");
  form.querySelector('[name="stage"]').innerHTML = partnerStageOptionsHtml("new");
  resetPartnerForm();

  document.getElementById("partnerAddToggle").addEventListener("click", () => {
    const opening = form.classList.contains("hidden") || partnerEditingId;
    resetPartnerForm();
    if (opening) {
      form.classList.remove("hidden");
      form.querySelector('[name="company"]').focus();
    }
  });
  document.getElementById("partnerFormCancel").addEventListener("click", resetPartnerForm);
  document.getElementById("partnerShowOld").addEventListener("change", (event) => {
    partnerFilters.showOld = event.target.checked;
    renderPartnerBoard();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    const text = (name) => String(fd.get(name) || "").trim() || null;
    const fields = {
      company: text("company"),
      website: text("website"),
      request_date: text("request_date"),
      owner: text("owner"),
      next_step: text("next_step"),
      notes: text("notes"),
      stage: String(fd.get("stage") || "new"),
    };
    if (!fields.company) return;
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    try {
      if (!window.domaPartners) throw new Error("Firestore sync not ready yet");
      if (partnerEditingId) {
        const existing = partnerItems.find((i) => i.id === partnerEditingId);
        // Unchanged stage stays out, so editing notes does not restamp the stage date.
        if (existing && (existing.stage || "new") === fields.stage) delete fields.stage;
        await window.domaPartners.updateRequest(partnerEditingId, fields);
      } else {
        await window.domaPartners.addRequest(fields);
      }
      resetPartnerForm();
    } catch (error) {
      console.error("Failed to save partner request:", error);
      alert("Could not save the request. Check the browser console for details.");
    } finally {
      submitBtn.disabled = false;
    }
  });

  const board = document.getElementById("partnerBoard");
  board.addEventListener("click", async (event) => {
    const card = event.target.closest(".partner-card");
    if (!card) return;
    if (event.target.closest(".partner-edit")) return openPartnerEdit(card.dataset.id);
    if (event.target.closest(".partner-delete")) {
      const item = partnerItems.find((i) => i.id === card.dataset.id);
      if (!confirm(`Remove "${item?.company || "this request"}" from the Partners board?`)) return;
      try {
        await window.domaPartners.hideRequest(card.dataset.id);
      } catch (error) {
        console.error("Failed to remove partner request:", error);
      }
    }
  });
  board.addEventListener("change", async (event) => {
    const select = event.target.closest(".partner-stage-select");
    if (!select) return;
    const item = partnerItems.find((i) => i.id === select.closest(".partner-card").dataset.id);
    if (!item) return;
    select.disabled = true;
    try {
      await window.domaPartners.updateRequest(item.id, partnerStageChangeFields(item, select.value));
    } catch (error) {
      console.error("Failed to change partner stage:", error);
      alert("Could not change the stage. Check the browser console for details.");
      select.value = item.stage || "new";
      select.disabled = false;
    }
  });

  whenFirestoreReady(() => {
    window.domaPartners.subscribeRequests((items) => {
      partnerItems = items;
      renderPartnerBoard();
    });
  });
}

function partnerStageHistoryHtml(item) {
  const dates = item.stage_dates || {};
  const steps = PARTNER_STAGES.filter(([v]) => dates[v]).map(([v, l]) => `${l} ${shortDateFromMs(dates[v])}`);
  return steps.length ? `<div class="partner-history">${escapeHtml(steps.join(" · "))}</div>` : "";
}

function partnerCardHtml(item, duplicates) {
  const stage = item.stage || "new";
  const site = safeHttpUrl(item.website, { allowBare: true });
  const ghl = safeHttpUrl(item.ghl_url);
  const age = partnerStageAgeDays(item);
  const company = item.company || "Company not given";
  const others = (duplicates.get(String(item.company || "").trim().toLowerCase()) || []).filter((o) => o.id !== item.id);
  const closedBadge =
    stage === "signed"
      ? `<span class="partner-badge partner-badge-signed">&#10003; Signed</span>`
      : stage === "declined"
        ? `<span class="partner-badge partner-badge-declined">&#10005; Declined</span>`
        : "";
  return `
    <article class="partner-card stage-${escAttr(stage)}" data-id="${escAttr(item.id)}">
      <div class="partner-card-head">
        <h4 class="partner-company">${linkedTitleHtml(company, site)}</h4>
        ${closedBadge}
      </div>
      <div class="lib-meta">
        ${item.owner ? `<span class="checklist-owner">${escapeHtml(item.owner)}</span>` : ""}
        ${item.request_date ? `<span class="lib-date">Requested ${escapeHtml(fullDate(item.request_date))}</span>` : ""}
      </div>
      ${item.form_source ? `<div class="partner-source">Source: ${escapeHtml(item.form_source)}</div>` : ""}
      ${others.length ? `<div class="partner-dup">Same company also requested ${escapeHtml(others.map((o) => fullDate(o.request_date || "")).join(", "))}</div>` : ""}
      ${item.next_step ? `<div class="partner-next"><span>Next:</span> ${linkifyText(item.next_step)}</div>` : ""}
      ${item.notes ? `<div class="partner-notes">${linkifyText(item.notes)}</div>` : ""}
      ${partnerStageHistoryHtml(item)}
      ${age !== null && !["signed", "declined"].includes(stage) && age >= 7 ? `<div class="partner-age">${age} days in this stage</div>` : ""}
      <div class="partner-actions">
        <select class="lib-status-select partner-stage-select" aria-label="Stage for ${escAttr(company)}">${partnerStageOptionsHtml(stage)}</select>
        ${ghl ? `<a class="partner-ghl" href="${escAttr(ghl)}" target="_blank" rel="noopener noreferrer">Open in GHL</a>` : ""}
        <button type="button" class="checklist-edit partner-edit" aria-label="Edit ${escAttr(company)}" title="Edit">&#9998;</button>
        <button type="button" class="checklist-delete partner-delete" aria-label="Remove ${escAttr(company)}" title="Remove">&times;</button>
      </div>
    </article>`;
}

function renderPartnerBoard() {
  const board = document.getElementById("partnerBoard");
  if (!board) return;
  showSyncNotice("partnerNotice", "partner_requests");
  const visible = partnerItems.filter((i) => !i.hidden);

  const count = (stages) => visible.filter((i) => stages.includes(i.stage || "new")).length;
  const waitingNew = visible.filter((i) => (i.stage || "new") === "new").map(partnerStageAgeDays).filter((d) => d !== null);
  renderCards("partnerCards", [
    { label: "New requests", value: number(count(["new"])), hint: waitingNew.length ? (Math.max(...waitingNew) ? `Oldest waiting ${Math.max(...waitingNew)} days` : "All arrived today") : "Nothing waiting" },
    { label: "Juli vetting", value: number(count(["vetting"])) },
    { label: "Packet sent", value: number(count(["packet_sent"])) },
    { label: "Call with Kyle", value: number(count(["call"])) },
    { label: "Signed", value: number(count(["signed"])), hint: `${number(count(["declined"]))} declined` },
  ]);

  const owners = uniqueCaseInsensitive([...TEAM_KNOWN_OWNERS, ...visible.map((i) => i.owner)]);
  document.getElementById("partnerOwnerOptions").innerHTML = owners.map((o) => `<option value="${escAttr(o)}">`).join("");
  buildFilterPills("partnerOwnerFilter", owners.map(escAttr), null, (value) => {
    partnerFilters.owner = value;
    renderPartnerBoard();
  });
  markActivePills("partnerOwnerFilter", partnerFilters.owner);

  const duplicates = new Map();
  visible.forEach((i) => {
    const key = String(i.company || "").trim().toLowerCase();
    if (key) duplicates.set(key, [...(duplicates.get(key) || []), i]);
  });

  const cutoff = Date.now() - PARTNER_CLOSED_DAYS * 86400000;
  const shown = visible.filter((i) => {
    if (partnerFilters.owner !== "all" && String(i.owner || "").toLowerCase() !== partnerFilters.owner.toLowerCase()) return false;
    const stage = i.stage || "new";
    if (!partnerFilters.showOld && (stage === "signed" || stage === "declined")) {
      const closedAt = (i.stage_dates || {})[stage] || i.updated_at || 0;
      if (closedAt < cutoff) return false;
    }
    return true;
  });

  const emptyEl = document.getElementById("partnerEmpty");
  emptyEl.textContent = visible.length
    ? "No requests match this filter."
    : "No requests yet. They arrive from GoHighLevel after the morning sync, or add one above.";
  emptyEl.style.display = shown.length ? "none" : "block";

  board.innerHTML = PARTNER_COLUMNS.map((col) => {
    const cards = shown
      .filter((i) => col.stages.includes(i.stage || "new"))
      .sort((a, b) => String(b.request_date || "").localeCompare(String(a.request_date || "")));
    return `
      <section class="partner-column" data-column="${col.key}" aria-label="${escAttr(col.label)}">
        <h3>${escapeHtml(col.label)} <span class="status-count">${cards.length}</span></h3>
        <div class="partner-column-list">${cards.map((c) => partnerCardHtml(c, duplicates)).join("") || `<div class="partner-column-empty">None</div>`}</div>
      </section>`;
  }).join("");
}

/* ---------- sponsors panel (GA4 /offers/ pages and outbound clicks) ---------- */

// One series, so one hue (series-a) and no legend: the heading names it.
// Values sit in ink beside each bar; hover shows the domains behind the name,
// and the table under the chart carries the same numbers.
function singleBarList(containerId, items, { labelKey, valueKey, unit, detail, maxBars = 10 }) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const all = (items || []).filter((i) => i[valueKey] > 0);
  const rows = all.slice(0, maxBars);
  if (!rows.length) {
    el.innerHTML = `<div class="chart-empty">No clicks in this range.</div>`;
    return;
  }
  const max = Math.max(...rows.map((i) => i[valueKey]));
  // Plain rows: the visible "name, track, value" text already reads in order
  // for screen readers, and the table below has the same numbers.
  el.innerHTML = rows
    .map((item, idx) => {
      const pct = Math.max(2, (item[valueKey] / max) * 100);
      return `<div class="hbar-row" data-idx="${idx}"><span class="hbar-name" title="${escAttr(item[labelKey])}">${escapeHtml(item[labelKey])}</span><span class="hbar-track" aria-hidden="true"><span class="hbar-fill" style="width:${pct}%;background:var(--series-a)"></span></span><span class="hbar-value">${number(item[valueKey])} <span class="sr-only">${unit}</span></span></div>`;
    })
    .join("") + (all.length > rows.length ? `<div class="hbar-more">Top ${rows.length} of ${all.length}. All of them are in the table below.</div>` : "");
  const tooltip = document.getElementById("chartTooltip");
  if (!tooltip) return;
  el.querySelectorAll(".hbar-row").forEach((row) => {
    const item = rows[Number(row.dataset.idx)];
    row.addEventListener("mousemove", (e) => {
      tooltip.innerHTML = `<div class="chart-tooltip-date">${escapeHtml(item[labelKey])}</div><div class="chart-tooltip-row"><i class="a"></i>${number(item[valueKey])} ${unit}</div>${detail ? `<div class="chart-tooltip-row">${escapeHtml(detail(item))}</div>` : ""}`;
      tooltip.style.display = "block";
      tooltip.style.left = `${Math.min(e.clientX + 14, window.innerWidth - 250)}px`;
      tooltip.style.top = `${Math.min(e.clientY + 14, window.innerHeight - 80)}px`;
    });
    row.addEventListener("mouseleave", () => (tooltip.style.display = "none"));
  });
}

function offersPageLabel(page) {
  // Paths read better than titles guessed from slugs ("Roya com").
  return page.page_path === "/offers/" ? "Offers hub (/offers/)" : page.page_path;
}

function renderSponsors() {
  const offers = dashboard.ga4?.offers;
  const prevOffers = dashboard.previous?.ga4?.offers;
  const empty = document.getElementById("sponsorsEmpty");
  const body = document.getElementById("sponsorsBody");
  if (!empty || !body) return;
  if (!offers) {
    empty.style.display = "block";
    body.hidden = true;
    renderCards("sponsorCards", []);
    return;
  }
  empty.style.display = "none";
  body.hidden = false;

  const hub = (o) => (o?.pages || []).find((p) => p.page_path === "/offers/") || { page_views: 0, sessions: 0 };
  const sponsorPageViews = (o) => (o?.pages || []).filter((p) => p.page_path !== "/offers/").reduce((sum, p) => sum + p.page_views, 0);
  // Same GA4 consent undercount as the Blog tab (2026-09-11 to 2026-10-04).
  const undercount = dashboard.ga4.undercount_overlap ?? ga4SpansConsentChange();
  const unfair = ga4ComparisonUnreliable();
  const delta = (curr, before) => (prevOffers && !unfair ? deltaBadge(curr, before) : "");
  const hint = undercount ? warnNote(`GA4 undercounted ${dateRange(GA4_UNDERCOUNT_START, GA4_UNDERCOUNT_END)}, so this is low and the % change is hidden`) : "";
  renderCards("sponsorCards", [
    { label: "/offers/ views", value: number(hub(offers).page_views), delta: delta(hub(offers).page_views, hub(prevOffers).page_views), hint },
    { label: "/offers/ sessions", value: number(hub(offers).sessions), delta: delta(hub(offers).sessions, hub(prevOffers).sessions) },
    { label: "Sponsor page views", value: number(sponsorPageViews(offers)), delta: delta(sponsorPageViews(offers), sponsorPageViews(prevOffers)) },
    { label: "Clicks to sponsors", value: number(offers.total_clicks), delta: delta(offers.total_clicks, prevOffers?.total_clicks), hint },
  ]);

  singleBarList("sponsorClicksChart", offers.sponsor_clicks, {
    labelKey: "sponsor",
    valueKey: "clicks",
    unit: "clicks",
    detail: (s) => `${s.domains.join(", ")}: ${number(s.from_hub)} from /offers/, ${number(s.from_sponsor_page)} from the sponsor page`,
  });
  renderTable(
    "sponsorClicksTable",
    offers.sponsor_clicks,
    (s) => `<tr><td title="${escAttr(s.domains.join(", "))}">${escapeHtml(s.sponsor)}</td><td>${number(s.clicks)}</td><td>${number(s.from_hub)}</td><td>${number(s.from_sponsor_page)}</td></tr>`,
    "No clicks to sponsors in this range."
  );
  renderTable(
    "offersPagesTable",
    offers.pages,
    (p) => `<tr><td><a href="https://dentalofficemanagers.com${escAttr(p.page_path)}" target="_blank" rel="noopener">${escapeHtml(offersPageLabel(p))}</a></td><td>${number(p.page_views)}</td><td>${number(p.sessions)}</td></tr>`,
    "No visits to the offers pages in this range."
  );
}

function renderPartners() {
  ensurePartnerListeners();
  renderPartnerBoard();
  safeRender("Sponsors", renderSponsors);
}

/* ---------- most clicked buttons (GA4 cta_click, Blog tab) ---------- */

function renderCtaClicks() {
  const rows = dashboard.ga4?.cta_clicks?.rows || [];
  const table = document.getElementById("blogCtaTable");
  if (!table) return;
  document.getElementById("blogCtaEmpty").style.display = rows.length ? "none" : "block";
  table.closest(".table-wrap").hidden = !rows.length;
  renderTable("blogCtaTable", rows, (r) => {
    const dest = safeHttpUrl(r.link_url) || (String(r.link_url || "").startsWith("/") ? r.link_url : null);
    const destLabel = r.link_url && r.link_url.length > 60 ? `${r.link_url.slice(0, 60)}…` : r.link_url || "";
    const destHtml = dest
      ? `<a href="${escAttr(dest)}" target="_blank" rel="noopener" title="${escAttr(r.link_url)}">${escapeHtml(destLabel)}</a>`
      : escapeHtml(destLabel);
    return `<tr><td title="${escAttr(r.page_path)}">${escapeHtml(r.page_title || r.page_path)}</td><td>${escapeHtml(r.link_text)}</td><td>${destHtml}</td><td>${number(r.clicks)}</td></tr>`;
  });
}

/* ---------- tabs + range ---------- */

// Work finished early is shown on the day it's reported to Kyle (week plan in
// C:\DOMA-blog-migration\seo_audit\plano_semana_*.md). Elements with
// data-release="YYYY-MM-DD" stay hidden before that date (New York time).
function applyReleaseDates() {
  // ?preview=1 shows everything early in this browser (Lucas checks before
  // Kyle sees it); ?preview=0 turns it off. Not access control: anyone with
  // the parameter sees the same thing.
  try {
    const flag = new URLSearchParams(location.search).get("preview");
    if (flag === "1") localStorage.setItem("doma_preview", "1");
    if (flag === "0") localStorage.removeItem("doma_preview");
    if (localStorage.getItem("doma_preview") === "1") return;
  } catch (error) {
    /* storage blocked: fall through to the release dates */
  }
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  document.querySelectorAll("[data-release]").forEach((el) => {
    if (el.dataset.release > today) el.hidden = true;
  });
}

applyReleaseDates(); // also before the password gate: the markup is already in the page

// One line at the top of each tab that opens with numbers: the question the tab answers.
const VIEW_LEADS = {
  overview: "How the site is doing: search clicks, visits and new leads for the range above, plus what needs attention.",
  seo: "How DOMA shows up on Google: clicks, positions, the searches and pages that bring people in, and what to fix.",
  blog: "Who visits the site and what they read: visits, top pages and how recent posts are doing.",
  "content-calendar": "What goes out on social and when, and where each post stands.",
  leads: "Where new leads come from, which free guides bring them in, and orders.",
  social: "Reach, followers and the posts that worked on Facebook and Instagram.",
  team: "Notes and action items from the team meetings.",
  todo: "Open tasks by person, and what was finished this month.",
};

function addViewLeads() {
  Object.entries(VIEW_LEADS).forEach(([view, text]) => {
    const section = document.getElementById(`view-${view}`);
    if (!section || section.querySelector(".view-lead")) return;
    const lead = document.createElement("p");
    lead.className = "view-lead";
    lead.textContent = text;
    section.prepend(lead);
  });
}

// Sidebar icons, drawn inline so the published site loads nothing from outside.
const TAB_ICONS = {
  overview: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  seo: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  competitors: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  blog: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
  leads: '<circle cx="9" cy="8" r="4"/><path d="M2 21v-1a6 6 0 0 1 12 0v1M19 8v6M16 11h6"/>',
  social: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4"/>',
  "content-ideas": '<path d="M9 18h6M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.3h6c0-1 .4-1.8 1-2.3A7 7 0 0 0 12 2z"/>',
  "content-calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  "weekly-recap": '<rect x="5" y="4" width="14" height="18" rx="2"/><path d="M9 2h6v4H9zM9 11h6M9 15h6"/>',
  todo: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="m8 12 3 3 5-6"/>',
  team: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><circle cx="17" cy="9" r="2.5"/><path d="M16 14.2A5 5 0 0 1 21.5 19"/>',
  partners: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  library: '<path d="M4 19.5V5a2 2 0 0 1 2-2h14v16H6a2 2 0 0 0-2 2z"/><path d="M8 7h8M8 11h6"/>',
  sops: '<path d="M10 6h11M10 12h11M10 18h11M4 6h1v4M4 10h2M6 18H4c0-1 2-2 2-3s-1-1.5-2-1"/>',
  links: '<path d="M15 3h6v6M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  changelog: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
};

function decorateTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    if (tab.querySelector(".tab-label")) return;
    const label = tab.textContent.trim();
    const icon = TAB_ICONS[tab.dataset.view] || '<circle cx="12" cy="12" r="3"/>';
    tab.innerHTML = `<svg class="tab-icon" viewBox="0 0 24 24" aria-hidden="true">${icon}</svg><span class="tab-label">${escapeHtml(label)}</span>`;
    tab.title = label; // tooltip when the sidebar is collapsed to icons
  });
}

// Desktop: collapse to icons (remembered). Phone: the sidebar opens over the page.
function initSidebar() {
  const body = document.body;
  const collapseBtn = document.getElementById("collapseBtn");
  const menuBtn = document.getElementById("menuBtn");
  const backdrop = document.getElementById("sidebarBackdrop");
  const setCollapsed = (on) => {
    body.classList.toggle("nav-collapsed", on);
    collapseBtn?.setAttribute("aria-pressed", on ? "true" : "false");
    collapseBtn?.setAttribute("aria-label", on ? "Expand the menu" : "Collapse the menu");
    try { localStorage.setItem("domaNavCollapsed", on ? "1" : "0"); } catch (e) { /* private mode */ }
  };
  let saved = "0";
  try { saved = localStorage.getItem("domaNavCollapsed") || "0"; } catch (e) { /* private mode */ }
  setCollapsed(saved === "1");
  collapseBtn?.addEventListener("click", () => setCollapsed(!body.classList.contains("nav-collapsed")));

  const setOpen = (on) => {
    body.classList.toggle("nav-open", on);
    menuBtn?.setAttribute("aria-expanded", on ? "true" : "false");
    if (backdrop) backdrop.hidden = !on;
    if (on) document.querySelector(".tab.active")?.focus();
  };
  menuBtn?.addEventListener("click", () => setOpen(!body.classList.contains("nav-open")));
  backdrop?.addEventListener("click", () => setOpen(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && body.classList.contains("nav-open")) {
      setOpen(false);
      menuBtn?.focus();
    }
  });
  const title = document.getElementById("topbarTitle");
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
    setOpen(false);
    if (title) title.textContent = tab.title || tab.textContent.trim();
    window.scrollTo({ top: 0 });
  }));
}

function initTabs() {
  applyReleaseDates();
  addViewLeads();
  decorateTabs();
  const tabs = document.querySelectorAll(".tab");
  tabs.forEach((tab) => {
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", tab.classList.contains("active") ? "true" : "false");
    tab.addEventListener("click", () => {
      tabs.forEach((t) => {
        t.classList.remove("active");
        t.setAttribute("aria-selected", "false");
      });
      tab.classList.add("active");
      tab.setAttribute("aria-selected", "true");
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
      document.getElementById(`view-${tab.dataset.view}`).classList.add("active");
    });
  });
}

function initRangeSelect() {
  const select = document.getElementById("rangeSelect");
  if (!select) return;
  select.addEventListener("change", async () => {
    await loadDashboard(select.value);
    renderAll();
  });
}

(async function init() {
  initThemeToggle();
  await initPasswordGate();
  initTabs();
  initSidebar();
  initRangeSelect();
  initDescLinkCopy();
  const initialDays = IS_STATIC ? String((STATIC_DATA && STATIC_DATA.default_range) || 90) : (document.getElementById("rangeSelect")?.value || 90);
  const select = document.getElementById("rangeSelect");
  if (select) select.value = initialDays;
  await loadDashboard(initialDays);
  renderAll();
})();
