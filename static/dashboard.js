const STATIC_DATA = window.DOMA_STATIC_DATA || null;
const IS_STATIC = Boolean(STATIC_DATA);

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
const PASSWORD_HASH = "e384f71129f78362b4fbb4edfc53b56f70223d04a148278ef4d9040002442a48"; // "doma2026"
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
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("en-US");
}

function percent(value) {
  if (value === null || value === undefined) return "—";
  return `${Number(value).toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;
}

function duration(seconds) {
  if (!seconds) return "—";
  const s = Math.round(Number(seconds));
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return m > 0 ? `${m}m ${rem}s` : `${rem}s`;
}

function shortDate(isoDate) {
  if (!isoDate) return "—";
  const d = new Date(isoDate + "T00:00:00");
  if (Number.isNaN(d.getTime())) return isoDate;
  return d.toLocaleDateString("en-US", { day: "2-digit", month: "short" });
}

function fullDate(isoDate) {
  if (!isoDate) return "—";
  const d = new Date(isoDate + "T00:00:00");
  if (Number.isNaN(d.getTime())) return isoDate;
  return d.toLocaleDateString("en-US", { day: "2-digit", month: "short", year: "numeric" });
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
    dashboard = (STATIC_DATA.dashboards && STATIC_DATA.dashboards[String(days)]) || STATIC_DATA.dashboard;
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

function svgLineChart(containerId, series, { formatter = number } = {}) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const validSeries = series.filter((s) => s.points && s.points.length > 1);
  if (!validSeries.length) {
    chartEmpty(containerId);
    return;
  }

  const width = 900;
  const height = 260;
  const left = 46;
  const right = 16;
  const top = 16;
  const bottom = 30;
  const plotW = width - left - right;
  const plotH = height - top - bottom;

  const hasValue = (p) => p.value !== null && p.value !== undefined;
  const allValues = validSeries.flatMap((s) => s.points.filter(hasValue).map((p) => p.value));
  const maxValue = Math.max(1, ...allValues);
  const pointCount = validSeries[0].points.length;

  const x = (i) => left + (pointCount <= 1 ? 0 : (i / (pointCount - 1)) * plotW);
  const y = (v) => top + plotH - (v / maxValue) * plotH;

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
      return `<line x1="${left}" y1="${yy}" x2="${width - right}" y2="${yy}" class="chart-grid"/><text x="${left - 8}" y="${yy + 4}" class="chart-axis" text-anchor="end">${formatter(maxValue * t)}</text>`;
    })
    .join("");

  const labelEvery = Math.max(1, Math.ceil(pointCount / 8));
  const labels = validSeries[0].points
    .map((p, i) => (i % labelEvery === 0 || i === pointCount - 1 ? `<text x="${x(i)}" y="${height - 8}" class="chart-axis" text-anchor="middle">${shortDate(p.date)}</text>` : ""))
    .join("");

  const lines = validSeries
    .map((s, seriesIndex) => {
      const cls = seriesIndex === 0 ? "a" : "b";
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
      const cls = seriesIndex === 0 ? "a" : "b";
      return s.points
        .map((p, i) => (hasValue(p) && (i % dotEvery === 0 || i === pointCount - 1) ? `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="2.6" class="chart-dot ${cls}"/>` : ""))
        .join("");
    })
    .join("");

  const legend = validSeries
    .map((s, i) => {
      const last = [...s.points].reverse().find(hasValue) || s.points[s.points.length - 1];
      const valueLabel = hasValue(last) ? formatter(last.value) : "No data yet";
      return `<span><i class="${i === 0 ? "a" : "b"}"></i>${s.label}: <strong>${valueLabel}</strong> <span class="legend-date">(${fullDate(last.date)})</span></span>`;
    })
    .join("");

  el.innerHTML = `<div class="chart-legend">${legend}</div><div class="chart-scroll"><svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img">${grid}${lines}${dots}<line class="chart-crosshair" x1="0" y1="${top}" x2="0" y2="${top + plotH}" style="display:none"/>${labels}<rect class="chart-overlay" x="${left}" y="${top}" width="${plotW}" height="${plotH}" fill="transparent"/></svg></div>`;

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
        return `<div class="chart-tooltip-row"><i class="${i === 0 ? "a" : "b"}"></i>${s.label}: <strong>${valueLabel}</strong></div>`;
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
        <div class="card-value">${item.value} ${item.delta || ""}</div>
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

/* ---------- issues / data health panel ---------- */

function computeIssues() {
  const issues = [];
  const gsc = dashboard.search_console;
  const ga4 = dashboard.ga4;
  const ghl = dashboard.ghl;
  const social = dashboard.social;
  const prev = dashboard.previous;

  if (!gsc.available) {
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
    const trendChecks = [
      { curr: gsc.available ? gsc.clicks : null, prevVal: prev.search_console.available ? prev.search_console.clicks : null, label: "Organic clicks (GSC)" },
      { curr: ga4.available ? ga4.sessions : null, prevVal: prev.ga4.available ? prev.ga4.sessions : null, label: "Sessions (GA4)" },
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
    .forEach((s) => issues.push({ severity: "error", text: `${s.source}: ${s.status}${s.detail ? " — " + s.detail : ""}` }));

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
      hint: `${number(gsc.impressions)} impressions`,
      delta: prev ? deltaBadge(gsc.clicks, prev.search_console.clicks) : "",
    },
    { label: "Average position", value: gsc.position || "—", hint: "lower is better", delta: prev ? deltaBadge(gsc.position, prev.search_console.position, { lowerIsBetter: true }) : "" },
    {
      label: "Sessions (GA4)",
      value: number(ga4.sessions),
      hint: `${number(ga4.active_users)} active users`,
      delta: prev ? deltaBadge(ga4.sessions, prev.ga4.sessions) : "",
    },
    {
      label: "New leads (GoHighLevel)",
      value: number(ghl.total_leads),
      hint: `${dashboard.start_date} to ${dashboard.end_date}`,
      delta: prev ? deltaBadge(ghl.total_leads, prev.ghl.total_leads) : "",
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
  renderCards("seoCards", [
    { label: "Clicks", value: number(gsc.clicks), delta: prev ? deltaBadge(gsc.clicks, prev.search_console.clicks) : "" },
    { label: "Impressions", value: number(gsc.impressions), delta: prev ? deltaBadge(gsc.impressions, prev.search_console.impressions) : "" },
    { label: "Average CTR", value: percent(gsc.ctr), delta: prev ? deltaBadge(gsc.ctr, prev.search_console.ctr) : "" },
    { label: "Average position", value: gsc.position || "—", delta: prev ? deltaBadge(gsc.position, prev.search_console.position, { lowerIsBetter: true }) : "" },
  ]);

  if (!gsc.available) {
    chartEmpty("seoChart", "No Search Console data yet - either scripts/sync_gsc.py hasn't run, or the property is still processing (new/re-verified properties take Google 1-2 days).");
  } else {
    svgLineChart("seoChart", [
      { label: "Clicks", points: gsc.daily.map((d) => ({ date: d.report_date, value: d.clicks })) },
    ]);
  }

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
    !state || state === "INDEXING_STATE_UNSPECIFIED" ? "—" : state.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
  renderTable(
    "seoCountriesTable",
    gsc.countries,
    (c) => `<tr><td>${(c.country || "").toUpperCase()}</td><td>${number(c.clicks)}</td><td>${number(c.impressions)}</td><td>${percent(c.ctr)}</td><td>${c.position}</td></tr>`
  );

  const DEVICE_LABELS = { DESKTOP: "Desktop", MOBILE: "Mobile", TABLET: "Tablet" };
  renderTable(
    "seoDeviceTable",
    gsc.devices,
    (d) => `<tr><td>${DEVICE_LABELS[d.device] || d.device}</td><td>${number(d.clicks)}</td><td>${number(d.impressions)}</td><td>${percent(d.ctr)}</td><td>${d.position}</td></tr>`
  );

  const gaps = gsc.content_gaps;
  document.getElementById("contentGapsEmpty").style.display = gaps.available ? "none" : "block";
  renderTable(
    "contentGapsTable",
    gaps.gaps || [],
    (g) => `<tr><td>${g.query}</td><td><a href="${g.page}" target="_blank" rel="noopener">${g.page.replace(/^https?:\/\/[^/]+/, "")}</a></td><td>${g.position}</td><td>${number(g.impressions)}</td><td>${number(g.clicks)}</td></tr>`
  );

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
    (p) => `<tr><td><a href="${p.url}" target="_blank" rel="noopener">${p.url.replace(/^https?:\/\/[^/]+/, "") || "/"}</a></td><td><span class="delta ${scoreClass(p.performance_score)}">${p.performance_score ?? "—"}</span></td><td>${p.lcp_ms ? (p.lcp_ms / 1000).toFixed(1) + "s" : "—"}</td><td>${p.cls != null ? p.cls.toFixed(3) : "—"}</td><td>${p.accessibility_score ?? "—"}</td><td>${p.top_opportunities?.[0] ? `${p.top_opportunities[0].title} (${Math.round(p.top_opportunities[0].savings_ms)}ms)` : "—"}</td></tr>`
  );

  renderTable(
    "indexCoverageTable",
    coverage.issues || [],
    (i) => `<tr><td><a href="${i.url}" target="_blank" rel="noopener">${i.url.replace(/^https?:\/\/[^/]+/, "")}</a></td><td>${i.coverage_state}</td><td>${humanizeIndexingState(i.indexing_state)}</td><td>${i.last_crawl_time ? fullDate(i.last_crawl_time.slice(0, 10)) : "—"}</td></tr>`,
    "Every checked URL is indexed cleanly - nothing needs attention."
  );
}

function renderCompetitors() {
  const ahrefs = dashboard.ahrefs || { competitors: { available: false, domains: [] }, issues: { available: false, issues: [] } };
  const competitors = ahrefs.competitors || { available: false, domains: [] };
  const issues = ahrefs.issues || { available: false, issues: [] };

  document.getElementById("ahrefsUnavailable").style.display = competitors.available || issues.available ? "none" : "block";
  document.getElementById("competitorsEmpty").style.display = competitors.available ? "none" : "block";
  document.getElementById("ahrefsIssuesEmpty").style.display = issues.available ? "none" : "block";

  renderTable(
    "competitorsTable",
    competitors.domains || [],
    (d) => `<tr><td>${d.role === "self" ? `<strong>${d.domain}</strong>` : d.domain}</td><td>${d.domain_rating ?? "—"}</td><td>${number(d.organic_traffic)}</td><td>${number(d.organic_keywords)}</td><td>${number(d.backlinks)}</td><td>${number(d.referring_domains)}</td></tr>`
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
    (c) => `<tr><td>${c.domain}</td><td>${number(c.appearances)}</td><td>${c.avg_position ?? "—"}</td><td>${c.best_position ?? "—"}</td><td>${number(c.queries_beating_doma)}</td></tr>`
  );

  const content = dashboard.competitor_content || { available: false, recent_pages: [], totals: [] };
  document.getElementById("competitorContentEmpty").style.display = content.available ? "none" : "block";
  if (content.available) {
    renderCards(
      "competitorContentCards",
      (content.totals || []).map((t) => ({ label: t.competitor_name, value: number(t.total_pages_tracked), hint: "pages tracked" }))
    );
  } else {
    document.getElementById("competitorContentCards").innerHTML = "";
  }
  renderTable(
    "competitorContentTable",
    content.recent_pages || [],
    (p) => `<tr><td>${p.competitor_name}</td><td><a href="${p.url}" target="_blank" rel="noopener">${titleFromUrl(p.url)}</a></td><td>${p.lastmod ? fullDate(p.lastmod.slice(0, 10)) : "—"}</td></tr>`
  );

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
    (w) => `<tr><td>${w.competitor_name}</td><td>${number(w.total_snapshots)}</td><td>${w.last_snapshot ? fullDate(`${w.last_snapshot.slice(0, 4)}-${w.last_snapshot.slice(4, 6)}-${w.last_snapshot.slice(6, 8)}`) : "—"}</td><td>${number(w.snapshots_last_90d)}</td></tr>`
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
    (e) => `<tr><td>${e.competitor}</td><td>${e.platform}</td><td>${e.date_found ? fullDate(e.date_found) : "—"}</td><td>${e.format || "—"}</td><td>${e.hook ? (e.link ? `<a href="${e.link}" target="_blank" rel="noopener">${e.hook}</a>` : e.hook) : "—"}</td><td>${e.offer || "—"}</td><td>${e.cta || "—"}</td><td>${e.strategic_hypothesis || "—"}</td></tr>`
  );
}

function renderBlog() {
  const ga4 = dashboard.ga4;
  const prev = dashboard.previous;
  renderCards("blogCards", [
    { label: "Sessions", value: number(ga4.sessions), delta: prev ? deltaBadge(ga4.sessions, prev.ga4.sessions) : "" },
    { label: "Active users", value: number(ga4.active_users), delta: prev ? deltaBadge(ga4.active_users, prev.ga4.active_users) : "" },
    { label: "New users", value: number(ga4.new_users), delta: prev ? deltaBadge(ga4.new_users, prev.ga4.new_users) : "" },
    { label: "Engaged sessions", value: number(ga4.engaged_sessions), delta: prev ? deltaBadge(ga4.engaged_sessions, prev.ga4.engaged_sessions) : "" },
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
    (p) => `<tr><td>${p.page_title || p.page_path}</td><td>${number(p.sessions)}</td><td>${number(p.page_views)}</td><td>${duration(p.avg_engagement_seconds)}</td><td>${percent(p.bounce_rate)}</td></tr>`
  );
  renderTable(
    "blogCountriesTable",
    ga4.countries,
    (c) => `<tr><td>${c.country}</td><td>${number(c.active_users)}</td><td>${number(c.sessions)}</td></tr>`
  );

  const deviceTotal = (ga4.devices || []).reduce((sum, d) => sum + d.sessions, 0);
  const DEVICE_LABELS_GA = { desktop: "Desktop", mobile: "Mobile", tablet: "Tablet" };
  renderTable(
    "blogDeviceTable",
    ga4.devices,
    (d) => `<tr><td>${DEVICE_LABELS_GA[d.device] || d.device}</td><td>${number(d.active_users)}</td><td>${number(d.sessions)}</td><td>${deviceTotal ? percent((d.sessions / deviceTotal) * 100) : "—"}</td></tr>`
  );

  const GA_GENDER_LABELS = { female: "Female", male: "Male" };
  document.getElementById("blogDemographicsEmpty").style.display = ga4.demographics_available ? "none" : "block";
  const genderTotal = (ga4.demographics?.gender || []).reduce((sum, g) => sum + g.active_users, 0);
  const ageTotal = (ga4.demographics?.age || []).reduce((sum, a) => sum + a.active_users, 0);
  renderTable(
    "blogGenderTable",
    ga4.demographics?.gender || [],
    (g) => `<tr><td>${GA_GENDER_LABELS[g.value] || g.value}</td><td>${number(g.active_users)}</td><td>${genderTotal ? percent((g.active_users / genderTotal) * 100) : "—"}</td></tr>`
  );
  renderTable(
    "blogAgeTable",
    ga4.demographics?.age || [],
    (a) => `<tr><td>${a.value}</td><td>${number(a.active_users)}</td><td>${ageTotal ? percent((a.active_users / ageTotal) * 100) : "—"}</td></tr>`
  );

  const content = dashboard.content;
  document.getElementById("recentPostsEmpty").style.display = content?.available ? "none" : "block";
  renderTable(
    "recentPostsTable",
    content?.posts || [],
    (p) => `<tr><td><a href="${p.url}" target="_blank" rel="noopener">${p.url.replace(/^https?:\/\/[^/]+/, "")}</a></td><td>${fullDate((p.published_at || "").slice(0, 10))}</td><td>${number(p.sessions)}</td><td>${number(p.page_views)}</td><td>${duration(p.avg_engagement_seconds)}</td><td>${number(p.clicks)}</td><td>${number(p.impressions)}</td><td>${p.position || "—"}</td></tr>`
  );
}

let communityListenersAttached = false;

function ensureCommunityListeners() {
  if (communityListenersAttached) return;
  communityListenersAttached = true;
  document.getElementById("communityEditBtn")?.addEventListener("click", async () => {
    const current = document.getElementById("communityMemberCount").textContent;
    const input = prompt("Update DOMA Free Community member count (read from the GHL Communities panel):", current === "—" ? "" : current);
    if (input === null) return;
    const count = parseInt(input, 10);
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
      countEl.textContent = data ? number(data.member_count) : "—";
      updatedEl.textContent = data?.updated_at ? `Updated ${new Date(data.updated_at).toLocaleString("en-US")}` : "Not set yet";
    });
  });
}

function renderLeads() {
  ensureCommunityListeners();
  const ghl = dashboard.ghl;
  const prev = dashboard.previous;
  renderCards("leadsCards", [
    { label: "New leads", value: number(ghl.total_leads), delta: prev ? deltaBadge(ghl.total_leads, prev.ghl.total_leads) : "" },
    { label: "Active sources", value: number(ghl.by_source.length) },
    { label: "Email campaigns", value: ghl.email_available ? number(ghl.email_campaigns.length) : "—" },
    {
      label: "Top source share",
      value: ghl.by_source.length ? percent((ghl.by_source[0].lead_count / ghl.total_leads) * 100) : "—",
      hint: ghl.by_source.length ? ghl.by_source[0].source : "",
    },
  ]);

  if (!ghl.available) {
    chartEmpty("leadsChart", "No GoHighLevel data yet. Run scripts/sync_ghl.py.");
  } else {
    svgLineChart("leadsChart", [
      { label: "New leads", points: ghl.daily.map((d) => ({ date: d.report_date, value: d.lead_count })) },
    ]);
  }

  svgHBarChart("leadsSourceBars", ghl.by_source, { labelKey: "source", valueKey: "lead_count" });
  renderTable(
    "leadsSourceTable",
    ghl.by_source,
    (s) => `<tr><td>${s.source}</td><td>${number(s.lead_count)}</td><td>${percent((s.lead_count / ghl.total_leads) * 100)}</td></tr>`
  );

  document.getElementById("emailUnavailable").style.display = ghl.email_available ? "none" : "block";
  renderTable(
    "leadsEmailTable",
    ghl.email_campaigns,
    (c) => `<tr><td>${c.campaign_name}</td><td>${number(c.recipients)}</td><td>${percent(c.open_rate)}</td><td>${percent(c.click_rate)}</td></tr>`
  );
}

function renderSocial() {
  const social = dashboard.social;
  const prev = dashboard.previous;
  const totalEngagement = social.posts.reduce((sum, p) => sum + p.engagement_total, 0);
  const totalFollowers = (social.followers.facebook || 0) + (social.followers.instagram || 0);
  const engagementRate = totalFollowers ? (totalEngagement / totalFollowers) * 100 : null;

  renderCards("socialCards", [
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
      hint: engagementRate !== null ? `${percent(engagementRate)} of combined followers` : "likes + comments + shares",
    },
  ]);

  if (!social.available) {
    chartEmpty("socialFollowersChart", "No Facebook/Instagram data yet. Run scripts/sync_meta_organic.py.");
  } else {
    const fbByDate = new Map(social.followers_daily.filter((d) => d.platform === "facebook").map((d) => [d.report_date, d.follower_count]));
    const igByDate = new Map(social.followers_daily.filter((d) => d.platform === "instagram").map((d) => [d.report_date, d.follower_count]));
    const dates = [...new Set([...fbByDate.keys(), ...igByDate.keys()])].sort();
    if (dates.length < 2) {
      chartEmpty("socialFollowersChart", "Follower history builds up day by day - check back after a few daily syncs.");
    } else {
      svgLineChart("socialFollowersChart", [
        { label: "Facebook", points: dates.map((d) => ({ date: d, value: fbByDate.get(d) ?? null })).filter((p) => p.value !== null) },
        { label: "Instagram", points: dates.map((d) => ({ date: d, value: igByDate.get(d) ?? null })).filter((p) => p.value !== null) },
      ]);
    }
  }

  renderTable(
    "socialPostsTable",
    social.posts,
    (p) => `<tr><td>${p.platform === "facebook" ? "Facebook" : "Instagram"}</td><td>${p.permalink ? `<a href="${p.permalink}" target="_blank" rel="noopener">${(p.caption || "(no caption)").slice(0, 60)}</a>` : (p.caption || "(no caption)").slice(0, 60)}</td><td>${fullDate((p.published_at || "").slice(0, 10))}</td><td>${p.reach ? number(p.reach) : "—"}</td><td>${number(p.likes)}</td><td>${number(p.comments)}</td><td>${number(p.shares)}</td></tr>`
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
    (g) => `<tr><td>${GENDER_LABELS[g.value] || g.value}</td><td>${number(g.follower_count)}</td><td>${genderTotal ? percent((g.follower_count / genderTotal) * 100) : "—"}</td></tr>`
  );
  svgHBarChart("socialCountryBars", igCountry, { labelKey: "value", valueKey: "follower_count", maxBars: 8 });
  renderTable(
    "socialCountryTable",
    igCountry.slice(0, 15),
    (c) => `<tr><td>${c.value}</td><td>${number(c.follower_count)}</td><td>${countryTotal ? percent((c.follower_count / countryTotal) * 100) : "—"}</td></tr>`
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

const TEAM_STATUS_ORDER = ["open", "in_progress", "done"];
// Always offered as owner options (filter pills + the add-ticket datalist)
// even before anyone has a ticket assigned to them yet - e.g. Mariannel
// starting to log tickets shouldn't require her first ticket to exist
// before her name is selectable.
const TEAM_KNOWN_OWNERS = ["Kyle", "Juli", "Lucas", "Mariannel"];
const TEAM_STATUS_LABELS = { open: "To do", in_progress: "In progress", done: "Completed" };

function teamEffectiveStatus(item) {
  return teamLiveStatuses.get(String(item.id)) || item.status || "open";
}

// Combines the baked meetings (from SQLite/data.js) with any manually-added
// tickets from Firestore, grouping ad-hoc tickets into the matching meeting
// by date, or a synthetic "Manually added" panel for a date with no real
// meeting. Recomputed fresh every render, so nothing is ever double-added.
function teamMergedMeetings(team) {
  const manualByDate = new Map();
  teamManualItems.forEach((mi) => {
    const list = manualByDate.get(mi.meeting_date) || [];
    list.push({
      id: mi.id,
      owner: mi.owner,
      topic: mi.topic || "General",
      description: mi.description,
      context: mi.context || null,
      status: mi.status || "open",
      is_manual: true,
      meeting_date: mi.meeting_date,
    });
    manualByDate.set(mi.meeting_date, list);
  });

  const meetings = team.meetings.map((m) => ({
    ...m,
    // Merge any saved field overrides onto the baked action items, and
    // stamp the parent meeting's date onto each one - meeting-derived items
    // don't carry their own meeting_date, but the edit form needs it to
    // show (read-only) which real meeting a ticket belongs to.
    action_items: (m.action_items || []).map((item) => {
      const override = teamLiveOverrides.get(String(item.id));
      return { ...item, ...(override || {}), meeting_date: m.meeting_date };
    }),
  }));
  const seenDates = new Set(meetings.map((m) => m.meeting_date));
  manualByDate.forEach((_items, mdate) => {
    if (!seenDates.has(mdate)) {
      meetings.push({ id: `manual-${mdate}`, meeting_date: mdate, title: "Manually added tickets", summary: null, action_items: [] });
      seenDates.add(mdate);
    }
  });

  return meetings
    .map((m) => ({ ...m, action_items: [...(m.action_items || []), ...(manualByDate.get(m.meeting_date) || [])] }))
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

  // Live sync: any click or added ticket (from this tab, another tab, or a
  // different person entirely - local dashboard or the published site, same
  // Firestore project either way) shows up here within a second or two.
  whenFirestoreReady(() => {
    window.domaTeamSync.subscribeAll((liveStatuses) => {
      teamLiveStatuses = liveStatuses;
      renderTeam();
    });
    window.domaTeamSync.subscribeManualItems((items) => {
      teamManualItems = items;
      renderTeam();
    });
    window.domaTeamSync.subscribeOverrides((overrides) => {
      teamLiveOverrides = overrides;
      renderTeam();
    });
  });
}

// Firestore doc id of the manual ticket currently being edited, or null
// when the form is in "add new" mode. The same form/fields are reused for
// both - only the submit handler's behavior (create vs. merge-update) and
// button label change.
let editingTeamItemId = null;
// Manual tickets live in their own Firestore doc (meeting_date is a real,
// changeable field on it). Meeting-derived tickets don't have that doc -
// their meeting_date comes from the real logged meeting they belong to, so
// it's shown for context but disabled: moving it wouldn't actually move the
// ticket, since which meeting-panel it renders under is fixed by the baked
// data, not by anything in the override doc.
let editingTeamIsManual = false;

function openTeamEditForm(item) {
  const form = document.getElementById("teamAddForm");
  if (!form) return;
  editingTeamItemId = item.id;
  editingTeamIsManual = !!item.is_manual;
  const dateField = form.querySelector('[name="meeting_date"]');
  form.querySelector('[name="owner"]').value = item.owner || "";
  form.querySelector('[name="topic"]').value = item.topic || "";
  dateField.value = item.meeting_date || "";
  dateField.disabled = !editingTeamIsManual;
  dateField.title = editingTeamIsManual ? "" : "This ticket is tied to a real logged meeting date and can't be moved.";
  form.querySelector('[name="description"]').value = item.description || "";
  form.querySelector('[name="context"]').value = item.context || "";
  form.querySelector('button[type="submit"]').textContent = "Save changes";
  form.classList.remove("hidden");
  form.scrollIntoView({ behavior: "smooth", block: "center" });
}

function resetTeamAddForm(form, dateInput) {
  editingTeamItemId = null;
  editingTeamIsManual = false;
  form.reset();
  dateInput.value = new Date().toISOString().slice(0, 10);
  dateInput.disabled = false;
  dateInput.title = "";
  form.classList.add("hidden");
  form.querySelector('button[type="submit"]').textContent = "Add ticket";
}

function ensureTeamAddForm(allOwners, allTopics) {
  const ownerOptions = document.getElementById("teamOwnerOptions");
  const topicOptions = document.getElementById("teamTopicOptions");
  if (ownerOptions) ownerOptions.innerHTML = allOwners.map((o) => `<option value="${o}">`).join("");
  if (topicOptions) topicOptions.innerHTML = allTopics.map((t) => `<option value="${t}">`).join("");

  if (teamAddFormAttached) return;
  teamAddFormAttached = true;
  const toggle = document.getElementById("teamAddToggle");
  const form = document.getElementById("teamAddForm");
  if (!toggle || !form) return;
  const dateInput = form.querySelector('[name="meeting_date"]');
  if (dateInput && !dateInput.value) dateInput.value = new Date().toISOString().slice(0, 10);

  toggle.addEventListener("click", () => form.classList.toggle("hidden"));
  document.getElementById("teamAddCancel")?.addEventListener("click", () => resetTeamAddForm(form, dateInput));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    const owner = String(fd.get("owner") || "").trim();
    const description = String(fd.get("description") || "").trim();
    // The date field is disabled (and so excluded from FormData) while
    // editing a meeting-derived ticket - only required for a new ticket or
    // an edit to a manual one, both of which own a real meeting_date field.
    const meetingDate = String(fd.get("meeting_date") || "").trim();
    const dateRequired = !editingTeamItemId || editingTeamIsManual;
    if (!owner || !description || (dateRequired && !meetingDate)) return;
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    try {
      if (!window.domaTeamSync) throw new Error("Firestore sync not ready yet");
      const fields = {
        owner,
        topic: String(fd.get("topic") || "").trim() || "General",
        description,
        context: String(fd.get("context") || "").trim() || null,
      };
      if (dateRequired) fields.meeting_date = meetingDate;
      if (editingTeamItemId) {
        if (editingTeamIsManual) {
          await window.domaTeamSync.updateManualItem(editingTeamItemId, fields);
        } else {
          await window.domaTeamSync.updateActionItem(editingTeamItemId, fields);
        }
      } else {
        await window.domaTeamSync.addManualItem(fields);
      }
      resetTeamAddForm(form, dateInput);
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

function teamChecklistItemHtml(item) {
  const status = teamEffectiveStatus(item);
  const editBtn = `<button type="button" class="checklist-edit" title="Edit">&#9998;</button>`;
  return `
    <div class="checklist-item status-${status}" data-id="${item.id}" data-owner="${item.owner}" data-topic="${item.topic || "General"}" data-status="${status}">
      <button type="button" class="checklist-mark" title="Click to change status">${teamStatusIcon(status)}</button>
      <span class="checklist-text">
        <span class="checklist-owner">${item.owner}</span><span class="checklist-topic">${item.topic || "General"}</span>${item.description}${item.context ? `<span class="checklist-context">${item.context}</span>` : ""}<span class="checklist-id">#${item.id}</span>
      </span>
      ${editBtn}
    </div>`;
}

function renderTeam() {
  ensureTeamListeners();
  const team = dashboard.team_meetings || { available: false, meetings: [], open_by_owner: [], total_open: 0, total_in_progress: 0, total_done: 0, total_all: 0 };
  document.getElementById("teamEmpty").style.display = team.available || teamManualItems.length ? "none" : "block";

  const mergedMeetings = teamMergedMeetings(team);
  const allItems = mergedMeetings.flatMap((m) => m.action_items || []);
  teamAllItemsById = new Map(allItems.map((i) => [String(i.id), i]));

  // Derived live from the items themselves (not the backend's total_* fields)
  // so a status click or a new manual ticket updates these cards instantly,
  // without waiting for the next full /api/dashboard reload.
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

  const allOwners = [...new Set([...TEAM_KNOWN_OWNERS, ...allItems.map((i) => i.owner)])];
  const allTopics = [...new Set(allItems.map((i) => i.topic || "General"))];
  ensureTeamAddForm(allOwners, allTopics);

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
          </div>
        </div>`;
    })
    .join("");

  applyTeamFilters();
}

/* ---------- content calendar ---------- */

const CALENDAR_STATUS_LABELS = { open: "Planned", in_progress: "In progress", done: "Published" };
// Matches the real weekly content cadence Juli confirmed (2026-08-26):
// Mon = Engagement Question, Tue = Teach It Tuesday, Wed = Sponsor,
// Thu = Blog/Ebook, Fri = Community Reshare. "Other" covers anything
// outside that cadence (podcast promo, one-off announcements, etc).
const CALENDAR_TYPES = ["Engagement Question", "Teach It Tuesday", "Sponsor", "Blog/Ebook", "Community Reshare", "Other"];

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
  const gapChips = (suggestions.content_gaps || [])
    .slice(0, 6)
    .map(
      (g) =>
        `<button type="button" class="calendar-suggestion-chip" data-title="${g.query}" data-type="Blog post">${g.query}<span class="cal-suggestion-meta">${number(g.impressions)} impressions, no ranking content yet</span></button>`
    );
  const ebookChips = (suggestions.top_ebooks || [])
    .slice(0, 4)
    .map(
      (e) =>
        `<button type="button" class="calendar-suggestion-chip" data-title="Blog post based on: ${e.page_title || titleFromUrl(e.page_path)}" data-type="Blog post">${e.page_title || titleFromUrl(e.page_path)}<span class="cal-suggestion-meta">${number(e.sessions)} sessions - repurpose into a blog post</span></button>`
    );
  container.innerHTML = [...gapChips, ...ebookChips].join("") || `<div class="empty">No suggestions yet - sync Search Console/GA4 data first.</div>`;
}

// Same reuse pattern as the Team ticket form: null means "add new", a
// Firestore doc id means the form is editing that item in place.
let editingCalendarItemId = null;

function openCalendarEditForm(item) {
  const form = document.getElementById("calendarAddForm");
  if (!form) return;
  editingCalendarItemId = item.id;
  form.querySelector('[name="date"]').value = item.date || "";
  form.querySelector('[name="type"]').value = item.type || "Blog/Ebook";
  form.querySelector('[name="owner"]').value = item.owner || "";
  form.querySelector('[name="title"]').value = item.title || "";
  form.querySelector('[name="headline"]').value = item.headline || "";
  form.querySelector('[name="direction"]').value = item.direction || "";
  form.querySelector('[name="graphic"]').value = item.graphic || "";
  form.querySelector('[name="resource"]').value = item.resource || "";
  form.querySelector('[name="link"]').value = item.link || "";
  form.querySelector('[name="notes"]').value = item.notes || "";
  form.querySelector('button[type="submit"]').textContent = "Save changes";
  form.classList.remove("hidden");
  form.scrollIntoView({ behavior: "smooth", block: "center" });
}

function resetCalendarAddForm(form, dateInput) {
  editingCalendarItemId = null;
  form.reset();
  dateInput.value = new Date().toISOString().slice(0, 10);
  form.classList.add("hidden");
  form.querySelector('button[type="submit"]').textContent = "Add to calendar";
}

function ensureCalendarAddForm(allOwners) {
  const ownerOptions = document.getElementById("calendarOwnerOptions");
  if (ownerOptions) ownerOptions.innerHTML = allOwners.map((o) => `<option value="${o}">`).join("");

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
      const type = String(fd.get("type") || "Blog post");
      const owner = String(fd.get("owner") || "").trim();
      const notes = String(fd.get("notes") || "").trim();
      const headline = String(fd.get("headline") || "").trim();
      const direction = String(fd.get("direction") || "").trim();
      const graphic = String(fd.get("graphic") || "").trim();
      const resource = String(fd.get("resource") || "").trim();
      const link = String(fd.get("link") || "").trim();
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
        link: link || null,
      };
      if (editingCalendarItemId) {
        await window.domaContentCalendar.updateItem(editingCalendarItemId, fields);
      } else {
        await window.domaContentCalendar.addItem(fields);
        // Every NEW calendar item also gets a matching Team & Meetings
        // ticket, so "what's scheduled" and "what everyone's working on"
        // don't live in two places that can drift apart. Edits don't
        // re-mirror - that would create a duplicate ticket per edit.
        if (owner && window.domaTeamSync) {
          try {
            await window.domaTeamSync.addManualItem({
              meeting_date: date,
              owner,
              topic: type,
              description: title,
              context: notes || "Added via Content Calendar",
            });
          } catch (ticketError) {
            console.error("Calendar item saved, but failed to create the matching Team & Meetings ticket:", ticketError);
          }
        }
      }
      resetCalendarAddForm(form, dateInput);
    } catch (error) {
      console.error("Failed to save calendar item:", error);
      alert("Could not save the calendar item - check the browser console for details.");
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
    form.querySelector('[name="type"]').value = chip.dataset.type || "Blog post";
    form.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

function shiftMonth(monthStr, delta) {
  const [year, month] = monthStr.split("-").map(Number);
  const d = new Date(Date.UTC(year, month - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

function calItemHtml(item) {
  // The compact per-day row can't show all the content-planning fields Juli
  // asked for (headline, direction, graphic idea, resource, link, notes) -
  // they all go into the native title tooltip instead, so hovering a row
  // surfaces the full brief without needing a separate detail view.
  const tooltipLines = [item.title];
  if (item.owner) tooltipLines.push(`Owner: ${item.owner}`);
  if (item.headline) tooltipLines.push(`Headline/Hook: ${item.headline}`);
  if (item.direction) tooltipLines.push(`Direction: ${item.direction}`);
  if (item.graphic) tooltipLines.push(`Graphic idea: ${item.graphic}`);
  if (item.resource) tooltipLines.push(`Resource: ${item.resource}`);
  if (item.notes) tooltipLines.push(`Notes: ${item.notes}`);
  if (item.link) tooltipLines.push(`Link: ${item.link}`);
  const tooltip = tooltipLines.join("\n");

  return `
    <div class="cal-item status-${item.status}" data-id="${item.id}" data-owner="${item.owner || ""}" data-type="${item.type}" data-status="${item.status}" title="${tooltip}">
      <button type="button" class="cal-item-mark" title="Click to change status">${teamStatusIcon(item.status)}</button>
      <span class="cal-item-title">${item.title}</span>
      ${item.link ? `<a href="${item.link}" target="_blank" rel="noopener noreferrer" class="cal-item-link" title="Open link">&#128279;</a>` : ""}
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
// Which category sections start closed - "Ebooks" has ~20 links and would
// otherwise dominate the tab; anything not listed here starts open. Once a
// category is manually toggled, its state persists across re-renders here
// too (same pattern as teamCollapsed).
let linksCollapsedCategories = {
  "Partner Offers": true,
  "Ebooks: Leadership": true,
  "Ebooks: Career": true,
  "Ebooks: Case Acceptance & Patient Communication": true,
  "Ebooks: Insurance & Billing": true,
  "Ebooks: Practice Operations": true,
  "Ebooks: AI": true,
};

function ensureLinksListeners() {
  if (linksListenersAttached) return;
  linksListenersAttached = true;
  document.getElementById("linksList").addEventListener("click", async (event) => {
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

  whenFirestoreReady(() => {
    window.domaUsefulLinks.subscribeLinks((links) => {
      linksItems = links;
      renderLinks();
    });
  });
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

function renderLinks() {
  ensureLinksListeners();
  document.getElementById("linksEmpty").style.display = linksItems.length ? "none" : "block";

  const allCategories = [...new Set(linksItems.map((l) => l.category || "General"))];
  ensureLinksAddForm(allCategories);

  const byCategory = new Map();
  linksItems.forEach((link) => {
    const cat = link.category || "General";
    if (!byCategory.has(cat)) byCategory.set(cat, []);
    byCategory.get(cat).push(link);
  });

  const container = document.getElementById("linksList");
  container.innerHTML = [...byCategory.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([category, links]) => {
      const collapsed = !!linksCollapsedCategories[category];
      return `
        <div class="links-category${collapsed ? " collapsed" : ""}" data-category="${category}">
          <button type="button" class="links-category-header">
            <span class="status-chevron">${collapsed ? "▸" : "▾"}</span>
            <h3>${category}</h3>
            <span class="status-count">${links.length}</span>
          </button>
          <div class="links-grid">
            ${links
              .map(
                (link) => {
                  const contact = [link.phone, link.email].filter(Boolean).join(" · ");
                  return `
              <div class="link-card" data-id="${link.id}">
                <div class="link-card-main">
                  <a href="${link.url}" target="_blank" rel="noopener noreferrer" title="${link.url}">${link.title}</a>
                  ${contact ? `<span class="link-contact">${contact}</span>` : ""}
                </div>
                <button type="button" class="link-copy" data-url="${link.url}" title="Copy link">⧉</button>
                <button type="button" class="checklist-delete" title="Remove">&times;</button>
              </div>`;
                }
              )
              .join("")}
          </div>
        </div>`;
    })
    .join("");
}

function renderContentIdeas() {
  const suggestions = dashboard.content_suggestions || { available: false, content_gaps: [], top_ebooks: [], top_blog_posts: [], best_post_times: { available: false, by_day: [], by_period: [] } };
  document.getElementById("contentIdeasEmpty").style.display = suggestions.available ? "none" : "block";

  renderTable(
    "contentGapsTable",
    suggestions.content_gaps,
    (g) => `<tr><td>${g.query}</td><td>${titleFromUrl(g.page)}</td><td>${g.position.toFixed(1)}</td><td>${number(g.impressions)}</td></tr>`,
    "No content gaps found yet - see SEO tab."
  );
  renderTable(
    "topEbooksTable",
    suggestions.top_ebooks,
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

function renderAll() {
  if (!dashboard) {
    document.getElementById("app").innerHTML = `<div class="panel"><div class="empty">Could not load the dashboard. Make sure the local server is running (RUN_DASHBOARD.bat) and data has been synced.</div></div>`;
    return;
  }
  renderOverview();
  renderSeo();
  renderCompetitors();
  renderBlog();
  renderContentIdeas();
  renderContentCalendar();
  renderLeads();
  renderSocial();
  renderTeam();
  renderLinks();

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
    comparisonEl.textContent = dashboard.previous_start_date
      ? `vs ${fullDate(dashboard.previous_start_date)} – ${fullDate(dashboard.previous_end_date)}`
      : "";
  }
}

/* ---------- tabs + range ---------- */

function initTabs() {
  const tabs = document.querySelectorAll(".tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
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
  initRangeSelect();
  const initialDays = IS_STATIC ? String(STATIC_DATA.default_range || 90) : (document.getElementById("rangeSelect")?.value || 90);
  const select = document.getElementById("rangeSelect");
  if (select) select.value = initialDays;
  await loadDashboard(initialDays);
  renderAll();
})();
