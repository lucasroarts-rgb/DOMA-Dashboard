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

  const allValues = validSeries.flatMap((s) => s.points.map((p) => p.value));
  const maxValue = Math.max(1, ...allValues);
  const pointCount = validSeries[0].points.length;

  const x = (i) => left + (pointCount <= 1 ? 0 : (i / (pointCount - 1)) * plotW);
  const y = (v) => top + plotH - (v / maxValue) * plotH;

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
      const points = s.points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
      const cls = seriesIndex === 0 ? "a" : "b";
      return `<polyline points="${points}" class="chart-line ${cls}"/>`;
    })
    .join("");

  const dotEvery = pointCount > 60 ? Math.ceil(pointCount / 60) : 1;
  const dots = validSeries
    .map((s, seriesIndex) => {
      const cls = seriesIndex === 0 ? "a" : "b";
      return s.points
        .map((p, i) => (i % dotEvery === 0 || i === pointCount - 1 ? `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="2.6" class="chart-dot ${cls}"/>` : ""))
        .join("");
    })
    .join("");

  const legend = validSeries
    .map((s, i) => {
      const last = s.points[s.points.length - 1];
      return `<span><i class="${i === 0 ? "a" : "b"}"></i>${s.label}: <strong>${formatter(last.value)}</strong> <span class="legend-date">(${fullDate(last.date)})</span></span>`;
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
        return `<div class="chart-tooltip-row"><i class="${i === 0 ? "a" : "b"}"></i>${s.label}: <strong>${formatter(point.value)}</strong></div>`;
      })
      .join("");
    const dateLabel = fullDate(validSeries[0].points[index]?.date);
    tooltip.innerHTML = `<div class="chart-tooltip-date">${dateLabel}</div>${rows}`;
    tooltip.style.display = "block";
    const tw = tooltip.offsetWidth;
    const th = tooltip.offsetHeight;
    const left = Math.min(clientX + 14, window.innerWidth - tw - 10);
    const top = Math.min(clientY + 14, window.innerHeight - th - 10);
    tooltip.style.left = `${Math.max(10, left)}px`;
    tooltip.style.top = `${Math.max(10, top)}px`;
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
    { label: "Organic clicks", points: dates.map((d) => ({ date: d, value: gscByDate.get(d) || 0 })) },
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

  renderTable(
    "indexCoverageTable",
    coverage.issues || [],
    (i) => `<tr><td><a href="${i.url}" target="_blank" rel="noopener">${i.url.replace(/^https?:\/\/[^/]+/, "")}</a></td><td>${i.coverage_state}</td><td>${humanizeIndexingState(i.indexing_state)}</td><td>${i.last_crawl_time ? fullDate(i.last_crawl_time.slice(0, 10)) : "—"}</td></tr>`,
    "Every checked URL is indexed cleanly - nothing needs attention."
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

function renderLeads() {
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
  renderTable(
    "socialGenderTable",
    igGender,
    (g) => `<tr><td>${GENDER_LABELS[g.value] || g.value}</td><td>${number(g.follower_count)}</td><td>${genderTotal ? percent((g.follower_count / genderTotal) * 100) : "—"}</td></tr>`
  );
  renderTable(
    "socialCountryTable",
    igCountry.slice(0, 15),
    (c) => `<tr><td>${c.value}</td><td>${number(c.follower_count)}</td><td>${countryTotal ? percent((c.follower_count / countryTotal) * 100) : "—"}</td></tr>`
  );
}

function renderAll() {
  if (!dashboard) {
    document.getElementById("app").innerHTML = `<div class="panel"><div class="empty">Could not load the dashboard. Make sure the local server is running (RUN_DASHBOARD.bat) and data has been synced.</div></div>`;
    return;
  }
  renderOverview();
  renderSeo();
  renderBlog();
  renderLeads();
  renderSocial();

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
  await initPasswordGate();
  initTabs();
  initRangeSelect();
  const initialDays = IS_STATIC ? String(STATIC_DATA.default_range || 90) : (document.getElementById("rangeSelect")?.value || 90);
  const select = document.getElementById("rangeSelect");
  if (select) select.value = initialDays;
  await loadDashboard(initialDays);
  renderAll();
})();
