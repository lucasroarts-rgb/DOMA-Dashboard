const STATIC_DATA = window.DOMA_STATIC_DATA || null;
const IS_STATIC = Boolean(STATIC_DATA);

let dashboard = null;

function number(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("en-US");
}

function percent(value) {
  if (value === null || value === undefined) return "—";
  return `${Number(value).toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;
}

function shortDate(isoDate) {
  if (!isoDate) return "—";
  const d = new Date(isoDate + "T00:00:00");
  if (Number.isNaN(d.getTime())) return isoDate;
  return d.toLocaleDateString("en-US", { day: "2-digit", month: "short" });
}

async function loadDashboard(days) {
  if (IS_STATIC) {
    dashboard = STATIC_DATA.dashboard;
    return;
  }
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - Number(days || 90));
  const params = new URLSearchParams({
    start: start.toISOString().slice(0, 10),
    end: end.toISOString().slice(0, 10),
  });
  try {
    dashboard = await fetch(`/api/dashboard?${params}`).then((r) => r.json());
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

  const legend = validSeries
    .map((s, i) => `<span><i class="${i === 0 ? "a" : "b"}"></i>${s.label}</span>`)
    .join("");

  el.innerHTML = `<div class="chart-legend">${legend}</div><svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img">${grid}${lines}${labels}</svg>`;
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
        <div class="card-value">${item.value}</div>
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

/* ---------- view renderers ---------- */

function renderOverview() {
  const gsc = dashboard.search_console;
  const ga4 = dashboard.ga4;
  const ghl = dashboard.ghl;

  renderCards("overviewCards", [
    { label: "Organic clicks (GSC)", value: number(gsc.clicks), hint: `${number(gsc.impressions)} impressions` },
    { label: "Average position", value: gsc.position || "—", hint: "lower is better" },
    { label: "Sessions (GA4)", value: number(ga4.sessions), hint: `${number(ga4.active_users)} active users` },
    { label: "New leads (GoHighLevel)", value: number(ghl.total_leads), hint: `${dashboard.start_date} to ${dashboard.end_date}` },
  ]);

  const gscByDate = new Map(gsc.daily.map((d) => [d.report_date, d.clicks]));
  const ga4ByDate = new Map(ga4.daily.map((d) => [d.report_date, d.sessions]));
  const dates = [...new Set([...gscByDate.keys(), ...ga4ByDate.keys()])].sort();

  svgLineChart("overviewChart", [
    { label: "Organic clicks", points: dates.map((d) => ({ date: d, value: gscByDate.get(d) || 0 })) },
    { label: "Sessions (GA4)", points: dates.map((d) => ({ date: d, value: ga4ByDate.get(d) || 0 })) },
  ]);
}

function renderSeo() {
  const gsc = dashboard.search_console;
  renderCards("seoCards", [
    { label: "Clicks", value: number(gsc.clicks) },
    { label: "Impressions", value: number(gsc.impressions) },
    { label: "Average CTR", value: percent(gsc.ctr) },
    { label: "Average position", value: gsc.position || "—" },
  ]);

  if (!gsc.available) {
    chartEmpty("seoChart", "No Search Console data yet. Run scripts/sync_gsc.py.");
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
}

function renderBlog() {
  const ga4 = dashboard.ga4;
  renderCards("blogCards", [
    { label: "Sessions", value: number(ga4.sessions) },
    { label: "Active users", value: number(ga4.active_users) },
    { label: "New users", value: number(ga4.new_users) },
    { label: "Engaged sessions", value: number(ga4.engaged_sessions) },
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
    (p) => `<tr><td>${p.page_title || p.page_path}</td><td>${number(p.sessions)}</td></tr>`
  );
}

function renderLeads() {
  const ghl = dashboard.ghl;
  renderCards("leadsCards", [
    { label: "New leads", value: number(ghl.total_leads) },
    { label: "Active sources", value: number(ghl.by_source.length) },
    { label: "Email campaigns", value: ghl.email_available ? number(ghl.email_campaigns.length) : "—" },
  ]);

  if (!ghl.available) {
    chartEmpty("leadsChart", "No GoHighLevel data yet. Run scripts/sync_ghl.py.");
  } else {
    svgLineChart("leadsChart", [
      { label: "New leads", points: ghl.daily.map((d) => ({ date: d.report_date, value: d.lead_count })) },
    ]);
  }

  renderTable("leadsSourceTable", ghl.by_source, (s) => `<tr><td>${s.source}</td><td>${number(s.lead_count)}</td></tr>`);

  document.getElementById("emailUnavailable").style.display = ghl.email_available ? "none" : "block";
  renderTable(
    "leadsEmailTable",
    ghl.email_campaigns,
    (c) => `<tr><td>${c.campaign_name}</td><td>${number(c.recipients)}</td><td>${percent(c.open_rate)}</td><td>${percent(c.click_rate)}</td></tr>`
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

  const synced = [
    dashboard.search_console.last_synced_at,
    dashboard.ga4.last_synced_at,
    dashboard.ghl.last_synced_at,
  ].filter(Boolean).sort().at(-1);
  const lastSyncedEl = document.getElementById("lastSynced");
  if (lastSyncedEl) {
    lastSyncedEl.textContent = synced ? `Synced ${new Date(synced).toLocaleString("en-US")}` : "Not synced yet";
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
  if (IS_STATIC) {
    select.disabled = true;
    select.title = "On the published site the range is fixed (set by the last sync).";
    return;
  }
  select.addEventListener("change", async () => {
    await loadDashboard(select.value);
    renderAll();
  });
}

(async function init() {
  initTabs();
  initRangeSelect();
  await loadDashboard(document.getElementById("rangeSelect")?.value || 90);
  renderAll();
})();
