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

async function loadDashboard(days) {
  if (IS_STATIC) {
    dashboard = (STATIC_DATA.dashboards && STATIC_DATA.dashboards[String(days)]) || STATIC_DATA.dashboard;
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

  // Dots on every point with a native tooltip, so the exact number is always
  // one hover/tap away instead of only readable off the grid lines.
  const dotEvery = pointCount > 60 ? Math.ceil(pointCount / 60) : 1;
  const dots = validSeries
    .map((s, seriesIndex) => {
      const cls = seriesIndex === 0 ? "a" : "b";
      return s.points
        .map((p, i) => {
          if (i % dotEvery !== 0 && i !== pointCount - 1) return "";
          return `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="3" class="chart-dot ${cls}"><title>${s.label} — ${fullDate(p.date)}: ${formatter(p.value)}</title></circle>`;
        })
        .join("");
    })
    .join("");

  const legend = validSeries
    .map((s, i) => {
      const last = s.points[s.points.length - 1];
      return `<span><i class="${i === 0 ? "a" : "b"}"></i>${s.label}: <strong>${formatter(last.value)}</strong> <span class="legend-date">(${fullDate(last.date)})</span></span>`;
    })
    .join("");

  el.innerHTML = `<div class="chart-legend">${legend}</div><svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img">${grid}${lines}${dots}${labels}</svg>`;
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

/* ---------- issues / data health panel ---------- */

function computeIssues() {
  const issues = [];
  const gsc = dashboard.search_console;
  const ga4 = dashboard.ga4;
  const ghl = dashboard.ghl;

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
  if (!dashboard.social.available) {
    issues.push({ severity: "warn", text: "No Facebook/Instagram data synced yet. Run scripts/sync_meta_organic.py." });
  }
  if (ghl.available && !ghl.email_available) {
    issues.push({
      severity: "info",
      text: "Email campaign stats aren't available from GoHighLevel for this sub-account (the /marketing/campaigns endpoint isn't enabled) - lead counts are unaffected.",
    });
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
  (dashboard.sync_status || [])
    .filter((s) => s.status !== "ok")
    .forEach((s) => issues.push({ severity: "error", text: `${s.source}: ${s.status}${s.detail ? " — " + s.detail : ""}` }));

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

  renderIssues();
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
    (p) => `<tr><td>${p.page_title || p.page_path}</td><td>${number(p.sessions)}</td><td>${number(p.page_views)}</td><td>${duration(p.avg_engagement_seconds)}</td><td>${percent(p.bounce_rate)}</td></tr>`
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

function renderSocial() {
  const social = dashboard.social;
  renderCards("socialCards", [
    { label: "Facebook followers", value: number(social.followers.facebook) },
    { label: "Instagram followers", value: number(social.followers.instagram) },
    { label: "Posts tracked", value: number(social.posts.length), hint: "last 20 per platform" },
    {
      label: "Total engagement",
      value: number(social.posts.reduce((sum, p) => sum + p.engagement_total, 0)),
      hint: "likes + comments + shares",
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
  initTabs();
  initRangeSelect();
  const initialDays = IS_STATIC ? String(STATIC_DATA.default_range || 90) : (document.getElementById("rangeSelect")?.value || 90);
  const select = document.getElementById("rangeSelect");
  if (select) select.value = initialDays;
  await loadDashboard(initialDays);
  renderAll();
})();
