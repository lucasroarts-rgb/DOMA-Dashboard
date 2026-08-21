"""DOMA Dashboard - local FastAPI app.

Single source of truth: every summary function here reads straight from
SQLite. scripts/generate_public_site.py calls the same functions to build
the static GitHub Pages export, so the local dashboard and the published
site never disagree.

No paid-traffic data lives here on purpose - DOMA does not run Meta Ads or
Google Ads today. Only organic acquisition (Search Console, GA4, GoHighLevel
leads) is tracked. See README.md before removing this comment.
"""

from __future__ import annotations

import base64
import hmac
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "doma.db")))
STATIC_DIR = BASE_DIR / "static"
CREDENTIALS_PATH = BASE_DIR / "data" / "admin_credentials.txt"

DEFAULT_LOOKBACK_DAYS = 90


def read_local_credentials() -> dict[str, str]:
    values: dict[str, str] = {}
    if not CREDENTIALS_PATH.exists():
        return values
    for line in CREDENTIALS_PATH.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


_local_credentials = read_local_credentials()
ADMIN_USER = os.getenv("ADMIN_USER") or _local_credentials.get("ADMIN_USER") or "doma"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD") or _local_credentials.get("ADMIN_PASSWORD") or ""


def has_valid_admin_credentials(request: Request) -> bool:
    if not ADMIN_PASSWORD:
        return False
    authorization = request.headers.get("Authorization", "")
    if not authorization.lower().startswith("basic "):
        return False
    try:
        encoded = authorization.split(" ", 1)[1].strip()
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return False
    return hmac.compare_digest(username, ADMIN_USER) and hmac.compare_digest(password, ADMIN_PASSWORD)


app = FastAPI(title="DOMA Dashboard")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def protect_writes(request: Request, call_next):
    """No admin/write surface exists yet (read-only dashboard), but this stays
    in place so any future POST/DELETE route is protected the same way the
    sibling PreSubs project protects its admin routes."""
    is_write = request.url.path.startswith("/api/") and request.method.upper() not in {
        "GET",
        "HEAD",
        "OPTIONS",
    }
    if is_write:
        if not ADMIN_PASSWORD:
            return JSONResponse(
                {"detail": "ADMIN_PASSWORD is not configured in .env."}, status_code=503
            )
        if not has_valid_admin_credentials(request):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="DOMA Admin"'})
    return await call_next(request)


@app.middleware("http")
async def disable_browser_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in {"/"} or request.url.path.startswith(("/static/", "/api/")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    schema = """
    CREATE TABLE IF NOT EXISTS search_console_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date TEXT NOT NULL,
        clicks INTEGER NOT NULL DEFAULT 0,
        impressions INTEGER NOT NULL DEFAULT 0,
        ctr REAL NOT NULL DEFAULT 0,
        position REAL NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(report_date)
    );

    CREATE TABLE IF NOT EXISTS search_console_queries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT NOT NULL,
        clicks INTEGER NOT NULL DEFAULT 0,
        impressions INTEGER NOT NULL DEFAULT 0,
        ctr REAL NOT NULL DEFAULT 0,
        position REAL NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(query)
    );

    CREATE TABLE IF NOT EXISTS gsc_countries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        country TEXT NOT NULL,
        clicks INTEGER NOT NULL DEFAULT 0,
        impressions INTEGER NOT NULL DEFAULT 0,
        ctr REAL NOT NULL DEFAULT 0,
        position REAL NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(country)
    );

    CREATE TABLE IF NOT EXISTS ga4_countries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        country TEXT NOT NULL,
        active_users INTEGER NOT NULL DEFAULT 0,
        sessions INTEGER NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(country)
    );

    CREATE TABLE IF NOT EXISTS ga4_devices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_category TEXT NOT NULL,
        active_users INTEGER NOT NULL DEFAULT 0,
        sessions INTEGER NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(device_category)
    );

    CREATE TABLE IF NOT EXISTS gsc_devices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device TEXT NOT NULL,
        clicks INTEGER NOT NULL DEFAULT 0,
        impressions INTEGER NOT NULL DEFAULT 0,
        ctr REAL NOT NULL DEFAULT 0,
        position REAL NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(device)
    );

    CREATE TABLE IF NOT EXISTS gsc_content_gaps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT NOT NULL,
        page TEXT NOT NULL,
        position REAL NOT NULL DEFAULT 0,
        impressions INTEGER NOT NULL DEFAULT 0,
        clicks INTEGER NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS content_posts_gsc (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL,
        published_at TEXT,
        clicks INTEGER NOT NULL DEFAULT 0,
        impressions INTEGER NOT NULL DEFAULT 0,
        ctr REAL NOT NULL DEFAULT 0,
        position REAL NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(url)
    );

    CREATE TABLE IF NOT EXISTS content_posts_ga4 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL,
        published_at TEXT,
        sessions INTEGER NOT NULL DEFAULT 0,
        page_views INTEGER NOT NULL DEFAULT 0,
        active_users INTEGER NOT NULL DEFAULT 0,
        avg_engagement_seconds REAL NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(url)
    );

    CREATE TABLE IF NOT EXISTS seo_onpage_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL,
        http_status INTEGER NOT NULL DEFAULT 0,
        fetch_error TEXT,
        title TEXT,
        title_length INTEGER NOT NULL DEFAULT 0,
        title_tag_count INTEGER NOT NULL DEFAULT 1,
        meta_description TEXT,
        meta_length INTEGER NOT NULL DEFAULT 0,
        meta_desc_tag_count INTEGER NOT NULL DEFAULT 1,
        h1_count INTEGER NOT NULL DEFAULT 0,
        images_total INTEGER NOT NULL DEFAULT 0,
        images_missing_alt INTEGER NOT NULL DEFAULT 0,
        word_count INTEGER NOT NULL DEFAULT 0,
        has_canonical INTEGER NOT NULL DEFAULT 0,
        canonical_url TEXT,
        has_schema INTEGER NOT NULL DEFAULT 0,
        js_rechecked INTEGER NOT NULL DEFAULT 0,
        checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS pagespeed_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL,
        strategy TEXT NOT NULL DEFAULT 'mobile',
        performance_score INTEGER,
        accessibility_score INTEGER,
        best_practices_score INTEGER,
        seo_score INTEGER,
        lcp_ms REAL,
        cls REAL,
        tbt_ms REAL,
        fcp_ms REAL,
        speed_index_ms REAL,
        console_errors TEXT,
        top_opportunities TEXT,
        checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(url, strategy)
    );

    CREATE TABLE IF NOT EXISTS competitor_pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        competitor_name TEXT NOT NULL,
        domain TEXT NOT NULL,
        url TEXT NOT NULL,
        lastmod TEXT,
        first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(url)
    );

    CREATE TABLE IF NOT EXISTS serp_competitors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        domain TEXT NOT NULL,
        appearances INTEGER NOT NULL DEFAULT 0,
        avg_position REAL,
        best_position INTEGER,
        queries_beating_doma INTEGER NOT NULL DEFAULT 0,
        checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(domain)
    );

    CREATE TABLE IF NOT EXISTS ahrefs_domains (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        domain TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'competitor',
        domain_rating INTEGER,
        organic_traffic INTEGER,
        organic_keywords INTEGER,
        backlinks INTEGER,
        referring_domains INTEGER,
        checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(domain)
    );

    CREATE TABLE IF NOT EXISTS ahrefs_site_audit_issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue TEXT NOT NULL,
        severity TEXT NOT NULL,
        affected_pages INTEGER NOT NULL DEFAULT 0,
        change_vs_prev INTEGER NOT NULL DEFAULT 0,
        checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(issue)
    );

    CREATE TABLE IF NOT EXISTS ga4_demographics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dimension_type TEXT NOT NULL,
        dimension_value TEXT NOT NULL,
        active_users INTEGER NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(dimension_type, dimension_value)
    );

    CREATE TABLE IF NOT EXISTS social_audience (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        dimension_type TEXT NOT NULL,
        dimension_value TEXT NOT NULL,
        follower_count INTEGER NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(platform, dimension_type, dimension_value)
    );

    CREATE TABLE IF NOT EXISTS ga4_traffic_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date TEXT NOT NULL,
        active_users INTEGER NOT NULL DEFAULT 0,
        new_users INTEGER NOT NULL DEFAULT 0,
        sessions INTEGER NOT NULL DEFAULT 0,
        engaged_sessions INTEGER NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(report_date)
    );

    CREATE TABLE IF NOT EXISTS ga4_channel_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date TEXT NOT NULL,
        channel_group TEXT NOT NULL,
        sessions INTEGER NOT NULL DEFAULT 0,
        UNIQUE(report_date, channel_group)
    );

    CREATE TABLE IF NOT EXISTS ga4_top_pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        page_path TEXT NOT NULL,
        page_title TEXT,
        sessions INTEGER NOT NULL DEFAULT 0,
        active_users INTEGER NOT NULL DEFAULT 0,
        page_views INTEGER NOT NULL DEFAULT 0,
        avg_engagement_seconds REAL NOT NULL DEFAULT 0,
        bounce_rate REAL NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(page_path)
    );

    CREATE TABLE IF NOT EXISTS ghl_leads_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '(not set)',
        lead_count INTEGER NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(report_date, source)
    );

    CREATE TABLE IF NOT EXISTS ghl_email_campaigns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id TEXT NOT NULL,
        campaign_name TEXT NOT NULL,
        sent_at TEXT,
        recipients INTEGER NOT NULL DEFAULT 0,
        opens INTEGER NOT NULL DEFAULT 0,
        clicks INTEGER NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(campaign_id)
    );

    CREATE TABLE IF NOT EXISTS gsc_url_inspections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL,
        verdict TEXT NOT NULL DEFAULT '',
        coverage_state TEXT NOT NULL DEFAULT '',
        indexing_state TEXT NOT NULL DEFAULT '',
        robots_txt_state TEXT NOT NULL DEFAULT '',
        page_fetch_state TEXT NOT NULL DEFAULT '',
        last_crawl_time TEXT,
        checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS social_followers_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date TEXT NOT NULL,
        platform TEXT NOT NULL,
        follower_count INTEGER NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(report_date, platform)
    );

    CREATE TABLE IF NOT EXISTS social_metrics_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date TEXT NOT NULL,
        platform TEXT NOT NULL,
        metric TEXT NOT NULL,
        value REAL NOT NULL DEFAULT 0,
        UNIQUE(report_date, platform, metric)
    );

    CREATE TABLE IF NOT EXISTS social_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        post_id TEXT NOT NULL,
        caption TEXT,
        published_at TEXT,
        permalink TEXT,
        likes INTEGER NOT NULL DEFAULT 0,
        comments INTEGER NOT NULL DEFAULT 0,
        shares INTEGER NOT NULL DEFAULT 0,
        reach INTEGER NOT NULL DEFAULT 0,
        engagement_total INTEGER NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(platform, post_id)
    );

    CREATE TABLE IF NOT EXISTS sync_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        status TEXT NOT NULL,
        detail TEXT,
        ran_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """
    with db() as con:
        con.executescript(schema)
        page_columns = {row["name"] for row in con.execute("PRAGMA table_info(ga4_top_pages)").fetchall()}
        if "page_views" not in page_columns:
            con.execute("ALTER TABLE ga4_top_pages ADD COLUMN page_views INTEGER NOT NULL DEFAULT 0")
        if "avg_engagement_seconds" not in page_columns:
            con.execute("ALTER TABLE ga4_top_pages ADD COLUMN avg_engagement_seconds REAL NOT NULL DEFAULT 0")
        if "bounce_rate" not in page_columns:
            con.execute("ALTER TABLE ga4_top_pages ADD COLUMN bounce_rate REAL NOT NULL DEFAULT 0")
        onpage_columns = {row["name"] for row in con.execute("PRAGMA table_info(seo_onpage_audit)").fetchall()}
        if "js_rechecked" not in onpage_columns:
            con.execute("ALTER TABLE seo_onpage_audit ADD COLUMN js_rechecked INTEGER NOT NULL DEFAULT 0")
        if "title_tag_count" not in onpage_columns:
            con.execute("ALTER TABLE seo_onpage_audit ADD COLUMN title_tag_count INTEGER NOT NULL DEFAULT 1")
        if "meta_desc_tag_count" not in onpage_columns:
            con.execute("ALTER TABLE seo_onpage_audit ADD COLUMN meta_desc_tag_count INTEGER NOT NULL DEFAULT 1")


def default_date_range(days: int = DEFAULT_LOOKBACK_DAYS) -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def search_console_summary(con: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, Any]:
    totals_row = con.execute(
        "SELECT COALESCE(SUM(clicks),0), COALESCE(SUM(impressions),0), "
        "COALESCE(SUM(position*impressions),0), MAX(synced_at) "
        "FROM search_console_daily WHERE report_date BETWEEN ? AND ?",
        (start_date, end_date),
    ).fetchone()
    clicks, impressions, position_weighted, last_synced_at = (
        int(totals_row[0] or 0),
        int(totals_row[1] or 0),
        float(totals_row[2] or 0),
        totals_row[3],
    )
    ctr = round((clicks / impressions) * 100, 2) if impressions else 0.0
    position = round(position_weighted / impressions, 1) if impressions else 0.0

    daily_rows = con.execute(
        "SELECT report_date, clicks, impressions, ctr, position FROM search_console_daily "
        "WHERE report_date BETWEEN ? AND ? ORDER BY report_date",
        (start_date, end_date),
    ).fetchall()
    daily = [
        {
            "report_date": row[0],
            "clicks": int(row[1] or 0),
            "impressions": int(row[2] or 0),
            "ctr": float(row[3] or 0),
            "position": float(row[4] or 0),
        }
        for row in daily_rows
    ]

    query_rows = con.execute(
        "SELECT query, clicks, impressions, ctr, position FROM search_console_queries "
        "ORDER BY clicks DESC LIMIT 25"
    ).fetchall()
    top_queries = [
        {
            "query": row[0],
            "clicks": int(row[1] or 0),
            "impressions": int(row[2] or 0),
            "ctr": float(row[3] or 0),
            "position": float(row[4] or 0),
        }
        for row in query_rows
    ]

    return {
        "available": impressions > 0,
        "clicks": clicks,
        "impressions": impressions,
        "ctr": ctr,
        "position": position,
        "daily": daily,
        "top_queries": top_queries,
        "last_synced_at": last_synced_at,
        "index_coverage": index_coverage_summary(con),
        "content_gaps": content_gap_summary(con),
        "onpage_audit": seo_onpage_summary(con),
        "pagespeed": pagespeed_summary(con),
        "countries": [
            {"country": row[0], "clicks": int(row[1] or 0), "impressions": int(row[2] or 0), "ctr": float(row[3] or 0), "position": float(row[4] or 0)}
            for row in con.execute(
                "SELECT country, clicks, impressions, ctr, position FROM gsc_countries ORDER BY clicks DESC LIMIT 15"
            ).fetchall()
        ],
        "devices": [
            {"device": row[0], "clicks": int(row[1] or 0), "impressions": int(row[2] or 0), "ctr": float(row[3] or 0), "position": float(row[4] or 0)}
            for row in con.execute(
                "SELECT device, clicks, impressions, ctr, position FROM gsc_devices ORDER BY clicks DESC"
            ).fetchall()
        ],
    }


# Coverage states from the URL Inspection API that mean the page is fine
# (indexed, or Google's paginated duplicate-handling for near-identical URLs
# it doesn't need every variant of) - anything else is worth a human look.
HEALTHY_COVERAGE_STATES = {
    "Submitted and indexed",
    "Indexed, not submitted in sitemap",
    "Duplicate, Google chose different canonical",
}


def index_coverage_summary(con: sqlite3.Connection) -> dict[str, Any]:
    rows = con.execute(
        "SELECT url, verdict, coverage_state, indexing_state, robots_txt_state, page_fetch_state, last_crawl_time "
        "FROM gsc_url_inspections ORDER BY coverage_state, url"
    ).fetchall()
    total = len(rows)
    issues = [
        {
            "url": row[0],
            "verdict": row[1],
            "coverage_state": row[2],
            "indexing_state": row[3],
            "robots_txt_state": row[4],
            "page_fetch_state": row[5],
            "last_crawl_time": row[6],
        }
        for row in rows
        if row[2] not in HEALTHY_COVERAGE_STATES
    ]
    by_state: dict[str, int] = {}
    for row in rows:
        by_state[row[2]] = by_state.get(row[2], 0) + 1
    checked_at_row = con.execute("SELECT MAX(checked_at) FROM gsc_url_inspections").fetchone()
    return {
        "available": total > 0,
        "total_checked": total,
        "healthy_count": total - len(issues),
        "issues": issues,
        "by_state": [{"state": state, "count": count} for state, count in sorted(by_state.items(), key=lambda kv: -kv[1])],
        "checked_at": checked_at_row[0] if checked_at_row else None,
    }


def content_gap_summary(con: sqlite3.Connection) -> dict[str, Any]:
    """Queries with real impressions where DOMA's best-ranking page still
    isn't in the top 15 - content opportunities, see sync_gsc.py:fetch_content_gaps
    for the exact thresholds."""
    rows = con.execute(
        "SELECT query, page, position, impressions, clicks FROM gsc_content_gaps "
        "ORDER BY impressions DESC LIMIT 30"
    ).fetchall()
    gaps = [
        {"query": row[0], "page": row[1], "position": float(row[2] or 0), "impressions": int(row[3] or 0), "clicks": int(row[4] or 0)}
        for row in rows
    ]
    checked_at_row = con.execute("SELECT MAX(synced_at) FROM gsc_content_gaps").fetchone()
    return {"available": bool(gaps), "gaps": gaps, "checked_at": checked_at_row[0] if checked_at_row else None}


# Findings a human should look at - anything short of "healthy" on any of
# these axes. Thresholds are deliberately loose (few false positives) since
# this feeds a "things to fix" list, not a pass/fail score.
def _onpage_findings(row: dict[str, Any]) -> list[str]:
    findings = []
    if row["fetch_error"] or (row["http_status"] and row["http_status"] != 200):
        findings.append(f"Page did not load cleanly (HTTP {row['http_status'] or 'error'})")
        return findings  # other checks are meaningless if the page didn't load
    if row["title_tag_count"] > 1:
        findings.append(f"{row['title_tag_count']} <title> tags on this page (should be exactly 1)")
    if not row["title"]:
        findings.append("Missing <title> tag")
    elif row["title_length"] < 30 or row["title_length"] > 60:
        findings.append(f"Title length {row['title_length']} chars (ideal ~30-60)")
    if row["meta_desc_tag_count"] > 1:
        findings.append(f"{row['meta_desc_tag_count']} meta description tags on this page (should be exactly 1)")
    if not row["meta_description"]:
        findings.append("Missing meta description")
    elif row["meta_length"] < 70 or row["meta_length"] > 160:
        findings.append(f"Meta description length {row['meta_length']} chars (ideal ~70-160)")
    if row["h1_count"] == 0:
        if row["js_rechecked"]:
            findings.append("No H1 even after a real-browser (JS-rendered) recheck - confirmed missing")
        else:
            findings.append(
                "No H1 in server-rendered HTML - if this page injects its heading via "
                "client-side JavaScript (some DOMA post templates do), this may be a false "
                "alarm; verify with a real browser before treating it as missing"
            )
    elif row["h1_count"] > 1:
        findings.append(f"{row['h1_count']} H1 headings (should be exactly 1)")
    if row["images_missing_alt"] > 0:
        findings.append(f"{row['images_missing_alt']} of {row['images_total']} images missing alt text")
    if row["word_count"] < 300:
        if row["js_rechecked"]:
            findings.append(
                f"Only {row['word_count']} words even after a real-browser (JS-rendered) recheck - confirmed thin content"
            )
        else:
            findings.append(
                f"Only {row['word_count']} words in server-rendered HTML - same JS-rendering "
                "caveat as the H1 check above; DOMA's blog post template injects the full "
                "article body client-side, so this is very likely a false alarm for /post/ URLs"
            )
    if not row["has_canonical"]:
        findings.append("Missing canonical tag")
    if not row["has_schema"]:
        findings.append("No structured data (JSON-LD) found")
    return findings


def seo_onpage_summary(con: sqlite3.Connection) -> dict[str, Any]:
    rows = con.execute(
        "SELECT url, http_status, fetch_error, title, title_length, meta_description, meta_length, "
        "h1_count, images_total, images_missing_alt, word_count, has_canonical, canonical_url, has_schema, "
        "js_rechecked, title_tag_count, meta_desc_tag_count FROM seo_onpage_audit ORDER BY url"
    ).fetchall()
    pages = []
    for row in rows:
        page = {
            "url": row[0],
            "http_status": int(row[1] or 0),
            "fetch_error": row[2],
            "title": row[3],
            "title_length": int(row[4] or 0),
            "meta_description": row[5],
            "meta_length": int(row[6] or 0),
            "h1_count": int(row[7] or 0),
            "images_total": int(row[8] or 0),
            "images_missing_alt": int(row[9] or 0),
            "word_count": int(row[10] or 0),
            "has_canonical": bool(row[11]),
            "canonical_url": row[12],
            "has_schema": bool(row[13]),
            "js_rechecked": bool(row[14]),
            "title_tag_count": int(row[15] or 1),
            "meta_desc_tag_count": int(row[16] or 1),
        }
        page["findings"] = _onpage_findings(page)
        pages.append(page)

    pages_with_issues = [p for p in pages if p["findings"]]
    checked_at_row = con.execute("SELECT MAX(checked_at) FROM seo_onpage_audit").fetchone()
    return {
        "available": bool(pages),
        "total_checked": len(pages),
        "healthy_count": len(pages) - len(pages_with_issues),
        "pages": pages_with_issues,
        "checked_at": checked_at_row[0] if checked_at_row else None,
    }


def pagespeed_summary(con: sqlite3.Connection) -> dict[str, Any]:
    import json

    rows = con.execute(
        "SELECT url, performance_score, accessibility_score, best_practices_score, seo_score, "
        "lcp_ms, cls, tbt_ms, fcp_ms, speed_index_ms, console_errors, top_opportunities, checked_at "
        "FROM pagespeed_audit WHERE strategy = 'mobile' ORDER BY performance_score ASC"
    ).fetchall()
    pages = []
    for row in rows:
        pages.append(
            {
                "url": row[0],
                "performance_score": row[1],
                "accessibility_score": row[2],
                "best_practices_score": row[3],
                "seo_score": row[4],
                "lcp_ms": row[5],
                "cls": row[6],
                "tbt_ms": row[7],
                "fcp_ms": row[8],
                "speed_index_ms": row[9],
                "console_errors": json.loads(row[10]) if row[10] else [],
                "top_opportunities": json.loads(row[11]) if row[11] else [],
                "checked_at": row[12],
            }
        )
    avg_performance = round(sum(p["performance_score"] for p in pages) / len(pages)) if pages else None
    return {
        "available": bool(pages),
        "pages": pages,
        "avg_performance_score": avg_performance,
        "checked_at": pages[0]["checked_at"] if pages else None,
    }


def ahrefs_competitors_summary(con: sqlite3.Connection) -> dict[str, Any]:
    rows = con.execute(
        "SELECT domain, role, domain_rating, organic_traffic, organic_keywords, "
        "backlinks, referring_domains, checked_at FROM ahrefs_domains "
        "ORDER BY (role != 'self'), organic_traffic DESC"
    ).fetchall()
    domains = [
        {
            "domain": row[0],
            "role": row[1],
            "domain_rating": row[2],
            "organic_traffic": row[3],
            "organic_keywords": row[4],
            "backlinks": row[5],
            "referring_domains": row[6],
            "checked_at": row[7],
        }
        for row in rows
    ]
    checked_at_row = con.execute("SELECT MAX(checked_at) FROM ahrefs_domains").fetchone()
    return {
        "available": bool(domains),
        "domains": domains,
        "checked_at": checked_at_row[0] if checked_at_row else None,
    }


def ahrefs_issues_summary(con: sqlite3.Connection) -> dict[str, Any]:
    rows = con.execute(
        "SELECT issue, severity, affected_pages, change_vs_prev, checked_at "
        "FROM ahrefs_site_audit_issues ORDER BY "
        "CASE severity WHEN 'Error' THEN 0 WHEN 'Warning' THEN 1 ELSE 2 END, affected_pages DESC"
    ).fetchall()
    issues = [
        {
            "issue": row[0],
            "severity": row[1],
            "affected_pages": row[2],
            "change_vs_prev": row[3],
            "checked_at": row[4],
        }
        for row in rows
    ]
    checked_at_row = con.execute("SELECT MAX(checked_at) FROM ahrefs_site_audit_issues").fetchone()
    return {
        "available": bool(issues),
        "issues": issues,
        "checked_at": checked_at_row[0] if checked_at_row else None,
    }


def competitor_content_summary(con: sqlite3.Connection, recent_days: int = 30) -> dict[str, Any]:
    """New pages found on tracked competitor sites since they were first
    seen - a page's first_seen_at is when this dashboard's own crawl first
    noticed it, not when the competitor actually published it, so this is
    "new to us" not a guaranteed publish date (most sites don't expose a
    reliable creation date via sitemap lastmod alone)."""
    cutoff = (date.today() - timedelta(days=recent_days)).isoformat()
    rows = con.execute(
        "SELECT competitor_name, domain, url, lastmod, first_seen_at FROM competitor_pages "
        "WHERE date(first_seen_at) >= ? ORDER BY first_seen_at DESC LIMIT 40",
        (cutoff,),
    ).fetchall()
    pages = [
        {"competitor_name": row[0], "domain": row[1], "url": row[2], "lastmod": row[3], "first_seen_at": row[4]}
        for row in rows
    ]
    totals = con.execute(
        "SELECT competitor_name, COUNT(*) FROM competitor_pages GROUP BY competitor_name"
    ).fetchall()
    checked_at_row = con.execute("SELECT MAX(first_seen_at) FROM competitor_pages").fetchone()
    return {
        "available": bool(pages) or bool(totals),
        "recent_pages": pages,
        "totals": [{"competitor_name": row[0], "total_pages_tracked": row[1]} for row in totals],
        "checked_at": checked_at_row[0] if checked_at_row else None,
    }


def serp_competitors_summary(con: sqlite3.Connection) -> dict[str, Any]:
    """Real, computed "share of voice" against DOMA's own top Search Console
    queries - who else shows up in the top 10 for the same searches, and
    how often - built from live Google results (Custom Search API), not a
    third-party authority score."""
    rows = con.execute(
        "SELECT domain, appearances, avg_position, best_position, queries_beating_doma, checked_at "
        "FROM serp_competitors ORDER BY appearances DESC LIMIT 15"
    ).fetchall()
    competitors = [
        {
            "domain": row[0],
            "appearances": row[1],
            "avg_position": row[2],
            "best_position": row[3],
            "queries_beating_doma": row[4],
            "checked_at": row[5],
        }
        for row in rows
    ]
    checked_at_row = con.execute("SELECT MAX(checked_at) FROM serp_competitors").fetchone()
    return {
        "available": bool(competitors),
        "competitors": competitors,
        "checked_at": checked_at_row[0] if checked_at_row else None,
    }


def recent_posts_summary(con: sqlite3.Connection) -> dict[str, Any]:
    """Latest blog posts (from the sitemap) joined with their GA4 + Search
    Console performance - answers "how is the post I published last week
    actually doing" without needing it to already be a top-30/top-50 entry
    elsewhere."""
    ga4_rows = {
        row[0]: {"sessions": int(row[2] or 0), "page_views": int(row[3] or 0), "active_users": int(row[4] or 0), "avg_engagement_seconds": float(row[5] or 0)}
        for row in con.execute(
            "SELECT url, published_at, sessions, page_views, active_users, avg_engagement_seconds FROM content_posts_ga4"
        ).fetchall()
    }
    gsc_rows = {
        row[0]: {"clicks": int(row[2] or 0), "impressions": int(row[3] or 0), "ctr": float(row[4] or 0), "position": float(row[5] or 0)}
        for row in con.execute(
            "SELECT url, published_at, clicks, impressions, ctr, position FROM content_posts_gsc"
        ).fetchall()
    }
    published_dates = {
        row[0]: row[1]
        for row in con.execute("SELECT url, published_at FROM content_posts_gsc UNION SELECT url, published_at FROM content_posts_ga4").fetchall()
    }

    posts = []
    for url, published_at in published_dates.items():
        ga4 = ga4_rows.get(url, {})
        gsc = gsc_rows.get(url, {})
        posts.append(
            {
                "url": url,
                "published_at": published_at,
                "sessions": ga4.get("sessions", 0),
                "page_views": ga4.get("page_views", 0),
                "avg_engagement_seconds": ga4.get("avg_engagement_seconds", 0),
                "clicks": gsc.get("clicks", 0),
                "impressions": gsc.get("impressions", 0),
                "position": gsc.get("position", 0),
            }
        )
    posts.sort(key=lambda p: p["published_at"] or "", reverse=True)
    return {"available": bool(posts), "posts": posts}


def ga4_summary(con: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, Any]:
    totals_row = con.execute(
        "SELECT COALESCE(SUM(active_users),0), COALESCE(SUM(new_users),0), "
        "COALESCE(SUM(sessions),0), COALESCE(SUM(engaged_sessions),0), MAX(synced_at) "
        "FROM ga4_traffic_daily WHERE report_date BETWEEN ? AND ?",
        (start_date, end_date),
    ).fetchone()
    active_users, new_users, sessions, engaged_sessions, last_synced_at = (
        int(totals_row[0] or 0),
        int(totals_row[1] or 0),
        int(totals_row[2] or 0),
        int(totals_row[3] or 0),
        totals_row[4],
    )

    daily_rows = con.execute(
        "SELECT report_date, active_users, sessions FROM ga4_traffic_daily "
        "WHERE report_date BETWEEN ? AND ? ORDER BY report_date",
        (start_date, end_date),
    ).fetchall()
    daily = [
        {"report_date": row[0], "active_users": int(row[1] or 0), "sessions": int(row[2] or 0)}
        for row in daily_rows
    ]

    channel_rows = con.execute(
        "SELECT channel_group, SUM(sessions) FROM ga4_channel_daily "
        "WHERE report_date BETWEEN ? AND ? GROUP BY channel_group ORDER BY SUM(sessions) DESC",
        (start_date, end_date),
    ).fetchall()
    channels = [{"channel_group": row[0], "sessions": int(row[1] or 0)} for row in channel_rows]

    page_rows = con.execute(
        "SELECT page_path, page_title, sessions, active_users, page_views, avg_engagement_seconds, bounce_rate "
        "FROM ga4_top_pages ORDER BY sessions DESC LIMIT 15"
    ).fetchall()
    top_pages = [
        {
            "page_path": row[0],
            "page_title": row[1],
            "sessions": int(row[2] or 0),
            "active_users": int(row[3] or 0),
            "page_views": int(row[4] or 0),
            "avg_engagement_seconds": float(row[5] or 0),
            "bounce_rate": float(row[6] or 0),
        }
        for row in page_rows
    ]

    countries = [
        {"country": row[0], "active_users": int(row[1] or 0), "sessions": int(row[2] or 0)}
        for row in con.execute(
            "SELECT country, active_users, sessions FROM ga4_countries ORDER BY active_users DESC LIMIT 15"
        ).fetchall()
    ]

    demographics: dict[str, list[dict[str, Any]]] = {"gender": [], "age": []}
    for dimension_type, dimension_value, users in con.execute(
        "SELECT dimension_type, dimension_value, active_users FROM ga4_demographics ORDER BY active_users DESC"
    ).fetchall():
        if dimension_type in demographics:
            demographics[dimension_type].append({"value": dimension_value, "active_users": int(users or 0)})

    return {
        "available": sessions > 0,
        "active_users": active_users,
        "new_users": new_users,
        "sessions": sessions,
        "engaged_sessions": engaged_sessions,
        "daily": daily,
        "channels": channels,
        "top_pages": top_pages,
        "countries": countries,
        "demographics": demographics,
        "demographics_available": bool(demographics["gender"] or demographics["age"]),
        "devices": [
            {"device": row[0], "active_users": int(row[1] or 0), "sessions": int(row[2] or 0)}
            for row in con.execute(
                "SELECT device_category, active_users, sessions FROM ga4_devices ORDER BY active_users DESC"
            ).fetchall()
        ],
        "last_synced_at": last_synced_at,
    }


def ghl_summary(con: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, Any]:
    total_row = con.execute(
        "SELECT COALESCE(SUM(lead_count),0), MAX(synced_at) FROM ghl_leads_daily "
        "WHERE report_date BETWEEN ? AND ?",
        (start_date, end_date),
    ).fetchone()
    total_leads, last_synced_at = int(total_row[0] or 0), total_row[1]

    daily_rows = con.execute(
        "SELECT report_date, SUM(lead_count) FROM ghl_leads_daily "
        "WHERE report_date BETWEEN ? AND ? GROUP BY report_date ORDER BY report_date",
        (start_date, end_date),
    ).fetchall()
    daily = [{"report_date": row[0], "lead_count": int(row[1] or 0)} for row in daily_rows]

    source_rows = con.execute(
        "SELECT source, SUM(lead_count) FROM ghl_leads_daily "
        "WHERE report_date BETWEEN ? AND ? GROUP BY source ORDER BY SUM(lead_count) DESC",
        (start_date, end_date),
    ).fetchall()
    by_source = [{"source": row[0], "lead_count": int(row[1] or 0)} for row in source_rows]

    email_rows = con.execute(
        "SELECT campaign_id, campaign_name, sent_at, recipients, opens, clicks FROM ghl_email_campaigns "
        "WHERE sent_at IS NULL OR sent_at BETWEEN ? AND ? ORDER BY sent_at DESC LIMIT 25",
        (start_date, end_date),
    ).fetchall()
    email_campaigns = [
        {
            "campaign_id": row[0],
            "campaign_name": row[1],
            "sent_at": row[2],
            "recipients": int(row[3] or 0),
            "opens": int(row[4] or 0),
            "clicks": int(row[5] or 0),
            "open_rate": round((row[4] / row[3]) * 100, 1) if row[3] else None,
            "click_rate": round((row[5] / row[3]) * 100, 1) if row[3] else None,
        }
        for row in email_rows
    ]

    return {
        "available": total_leads > 0,
        "total_leads": total_leads,
        "daily": daily,
        "by_source": by_source,
        "email_available": len(email_campaigns) > 0,
        "email_campaigns": email_campaigns,
        "last_synced_at": last_synced_at,
    }


def social_summary(con: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, Any]:
    followers: dict[str, Any] = {}
    for platform in ("facebook", "instagram"):
        row = con.execute(
            "SELECT follower_count, synced_at FROM social_followers_daily "
            "WHERE platform = ? ORDER BY report_date DESC LIMIT 1",
            (platform,),
        ).fetchone()
        followers[platform] = int(row[0]) if row else 0

    followers_daily_rows = con.execute(
        "SELECT report_date, platform, follower_count FROM social_followers_daily "
        "WHERE report_date BETWEEN ? AND ? ORDER BY report_date",
        (start_date, end_date),
    ).fetchall()
    followers_daily = [
        {"report_date": row[0], "platform": row[1], "follower_count": int(row[2] or 0)}
        for row in followers_daily_rows
    ]

    metrics_rows = con.execute(
        "SELECT report_date, platform, metric, value FROM social_metrics_daily "
        "WHERE report_date BETWEEN ? AND ? ORDER BY report_date",
        (start_date, end_date),
    ).fetchall()
    metrics_daily = [
        {"report_date": row[0], "platform": row[1], "metric": row[2], "value": float(row[3] or 0)}
        for row in metrics_rows
    ]

    posts_rows = con.execute(
        "SELECT platform, post_id, caption, published_at, permalink, likes, comments, shares, reach, engagement_total "
        "FROM social_posts ORDER BY published_at DESC LIMIT 40"
    ).fetchall()
    posts = [
        {
            "platform": row[0],
            "post_id": row[1],
            "caption": row[2],
            "published_at": row[3],
            "permalink": row[4],
            "likes": int(row[5] or 0),
            "comments": int(row[6] or 0),
            "shares": int(row[7] or 0),
            "reach": int(row[8] or 0),
            "engagement_total": int(row[9] or 0),
        }
        for row in posts_rows
    ]

    audience_rows = con.execute(
        "SELECT platform, dimension_type, dimension_value, follower_count FROM social_audience ORDER BY follower_count DESC"
    ).fetchall()
    audience: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for platform, dimension_type, dimension_value, follower_count in audience_rows:
        audience.setdefault(platform, {}).setdefault(dimension_type, []).append(
            {"value": dimension_value, "follower_count": int(follower_count or 0)}
        )

    last_synced_row = con.execute(
        "SELECT MAX(synced_at) FROM social_followers_daily"
    ).fetchone()

    return {
        "available": bool(posts) or followers["facebook"] > 0 or followers["instagram"] > 0,
        "followers": followers,
        "followers_daily": followers_daily,
        "metrics_daily": metrics_daily,
        "posts": posts,
        "audience": audience,
        "last_synced_at": last_synced_row[0] if last_synced_row else None,
    }


def sync_status(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT source, status, detail, ran_at FROM sync_log "
        "WHERE id IN (SELECT MAX(id) FROM sync_log GROUP BY source) ORDER BY source"
    ).fetchall()
    return [
        {"source": row[0], "status": row[1], "detail": row[2], "ran_at": row[3]}
        for row in rows
    ]


def full_dashboard(start_date: str, end_date: str) -> dict[str, Any]:
    with db() as con:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "search_console": search_console_summary(con, start_date, end_date),
            "ga4": ga4_summary(con, start_date, end_date),
            "ghl": ghl_summary(con, start_date, end_date),
            "social": social_summary(con, start_date, end_date),
            "content": recent_posts_summary(con),
            "ahrefs": {
                "competitors": ahrefs_competitors_summary(con),
                "issues": ahrefs_issues_summary(con),
            },
            "competitor_content": competitor_content_summary(con),
            "serp_competitors": serp_competitors_summary(con),
            "sync_status": sync_status(con),
        }


def previous_period_range(start_date: str, end_date: str) -> tuple[str, str]:
    """Immediately preceding period of the same length, for period-over-period
    comparison - e.g. "last 7 days" compares against the 7 days before that."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    span = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=span - 1)
    return previous_start.isoformat(), previous_end.isoformat()


def dashboard_with_comparison(start_date: str, end_date: str) -> dict[str, Any]:
    previous_start, previous_end = previous_period_range(start_date, end_date)
    current = full_dashboard(start_date, end_date)
    previous = full_dashboard(previous_start, previous_end)
    return {
        **current,
        "previous_start_date": previous_start,
        "previous_end_date": previous_end,
        "previous": previous,
    }


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/dashboard")
def dashboard(start: str | None = None, end: str | None = None, days: int | None = None):
    if days:
        start_date, end_date = default_date_range(days)
    else:
        default_start, default_end = default_date_range()
        start_date = start or default_start
        end_date = end or default_end
    if start_date > end_date:
        raise HTTPException(400, "start must be before end")
    return dashboard_with_comparison(start_date, end_date)


if __name__ == "__main__":
    import uvicorn

    init_db()
    uvicorn.run(app, host="127.0.0.1", port=8000)
