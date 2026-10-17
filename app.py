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
import html
import hmac
import io
import json
import mimetypes
import os
import re
import sqlite3
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from scripts.env_utils import load_env_file
from scripts.ebook_pipeline import copy_generator, pdf_extract
from scripts.ebook_pipeline.ghl_client import GhlClient, GhlError
from scripts.ebook_pipeline.wp_client import WpClient, WpError
from scripts.sync_ebook_pipeline import write_package

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "doma.db")))
STATIC_DIR = BASE_DIR / "static"
CREDENTIALS_PATH = BASE_DIR / "data" / "admin_credentials.txt"

# WordPress media library doubles as file storage for content-calendar
# uploads (ebook PDFs/images) - reuses the same Application Password already
# configured for SEO/content work, no separate storage bucket needed. See
# scripts/ebook_pipeline/wp_client.py for the sibling upload used by the
# ebook pipeline; this is a plainer inline version since it needs to accept
# both images and PDFs and app.py has no reason to depend on that module.
_wp_env = load_env_file(BASE_DIR / ".env")
WP_URL = (_wp_env.get("WP_URL") or os.getenv("WP_URL") or "").rstrip("/")
WP_USERNAME = _wp_env.get("WP_USERNAME") or os.getenv("WP_USERNAME")
WP_APP_PASSWORD = _wp_env.get("WP_APP_PASSWORD") or os.getenv("WP_APP_PASSWORD")
# Same spoofed UA as wp_client.py - Bluehost's WAF (Mod_Security) 406s the
# default `requests` UA on some content-types.
WP_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
UPLOAD_MAX_BYTES = 20 * 1024 * 1024

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
ADMIN_USER = os.getenv("ADMIN_USER") or _local_credentials.get("ADMIN_USER") or _wp_env.get("ADMIN_USER") or "doma"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD") or _local_credentials.get("ADMIN_PASSWORD") or _wp_env.get("ADMIN_PASSWORD") or ""


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

# Lets the published GitHub Pages dashboard call this app's Content Calendar
# upload/create-pages endpoints when it's deployed somewhere publicly
# reachable (see scripts/generate_public_site.py's api_base/upload_auth) -
# only when this app itself IS that public deployment; a purely local
# instance has nothing to allow cross-origin, so GITHUB_PAGES_URL is simply
# unset there and this becomes a no-op. allow_credentials requires an exact
# origin, not "*" - and a CORS origin is scheme+host only, never a path, so
# this can't just rstrip("/") off GITHUB_PAGES_URL (which is the page URL,
# "https://.../DOMA-Dashboard/") - the browser's actual Origin header for
# that page is "https://lucasroarts-rgb.github.io", with no path at all.
_github_pages_origin = urlsplit(_wp_env.get("GITHUB_PAGES_URL") or "")
_github_pages_origin = f"{_github_pages_origin.scheme}://{_github_pages_origin.netloc}" if _github_pages_origin.netloc else ""
if _github_pages_origin:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[_github_pages_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )


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

    CREATE TABLE IF NOT EXISTS competitor_tech_stack (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        competitor_name TEXT NOT NULL,
        domain TEXT NOT NULL,
        signals TEXT NOT NULL DEFAULT '[]',
        checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(domain)
    );

    CREATE TABLE IF NOT EXISTS competitor_wayback_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        competitor_name TEXT NOT NULL,
        domain TEXT NOT NULL,
        total_snapshots INTEGER NOT NULL DEFAULT 0,
        first_snapshot TEXT,
        last_snapshot TEXT,
        snapshots_last_90d INTEGER NOT NULL DEFAULT 0,
        checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(domain)
    );

    CREATE TABLE IF NOT EXISTS team_meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_date TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT,
        raw_notes TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(meeting_date, title)
    );

    CREATE TABLE IF NOT EXISTS team_action_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id INTEGER NOT NULL REFERENCES team_meetings(id),
        owner TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        context TEXT,
        topic TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS ad_spy_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id TEXT,
        competitor TEXT NOT NULL,
        platform TEXT NOT NULL,
        date_found TEXT NOT NULL,
        start_date TEXT,
        status_observed TEXT,
        format TEXT,
        hook TEXT,
        angle TEXT,
        pain_or_desire TEXT,
        promise TEXT,
        offer TEXT,
        proof TEXT,
        cta TEXT,
        tone_of_voice TEXT,
        visual_style TEXT,
        landing_page TEXT,
        strategic_hypothesis TEXT,
        link TEXT,
        notes TEXT,
        last_reviewed TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        source TEXT NOT NULL DEFAULT 'ghl',
        sent_at TEXT,
        sent_time TEXT,
        scheduled_at TEXT,
        recipients INTEGER NOT NULL DEFAULT 0,
        opens INTEGER NOT NULL DEFAULT 0,
        clicks INTEGER NOT NULL DEFAULT 0,
        delivered INTEGER,
        delivered_rate REAL,
        unsubscribes INTEGER,
        unsubscribe_rate REAL,
        hard_bounces INTEGER,
        soft_bounces INTEGER,
        bounce_rate REAL,
        skipped INTEGER,
        skipped_rate REAL,
        total_clicks INTEGER,
        ctor REAL,
        top_links TEXT,
        notes TEXT,
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

    -- Per-day GSC breakdowns (2026-10-15) so the SEO tab's top lists follow
    -- the chosen range instead of a fixed 180-day snapshot. The old
    -- search_console_queries / gsc_countries / gsc_devices tables stay as a
    -- fallback while these are empty.
    CREATE TABLE IF NOT EXISTS search_console_query_daily (
        report_date TEXT NOT NULL,
        query TEXT NOT NULL,
        clicks INTEGER NOT NULL DEFAULT 0,
        impressions INTEGER NOT NULL DEFAULT 0,
        position REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (report_date, query)
    );

    CREATE TABLE IF NOT EXISTS gsc_country_daily (
        report_date TEXT NOT NULL,
        country TEXT NOT NULL,
        clicks INTEGER NOT NULL DEFAULT 0,
        impressions INTEGER NOT NULL DEFAULT 0,
        position REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (report_date, country)
    );

    CREATE TABLE IF NOT EXISTS gsc_device_daily (
        report_date TEXT NOT NULL,
        device TEXT NOT NULL,
        clicks INTEGER NOT NULL DEFAULT 0,
        impressions INTEGER NOT NULL DEFAULT 0,
        position REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (report_date, device)
    );

    CREATE TABLE IF NOT EXISTS gsc_page_daily (
        report_date TEXT NOT NULL,
        page TEXT NOT NULL,
        clicks INTEGER NOT NULL DEFAULT 0,
        impressions INTEGER NOT NULL DEFAULT 0,
        position REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (report_date, page)
    );

    -- [page, query] over the last 90 days, one snapshot (not per day): a
    -- [date, page, query] pull is several hundred thousand rows for little gain.
    CREATE TABLE IF NOT EXISTS gsc_page_queries (
        page TEXT NOT NULL,
        query TEXT NOT NULL,
        clicks INTEGER NOT NULL DEFAULT 0,
        impressions INTEGER NOT NULL DEFAULT 0,
        position REAL NOT NULL DEFAULT 0,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (page, query)
    );

    -- Every published WordPress post (path + real title), so "blog post"
    -- means an actual post, not anything under /post/ (speaker directory and
    -- events also live there).
    CREATE TABLE IF NOT EXISTS wp_posts (
        page_path TEXT PRIMARY KEY,
        title TEXT,
        published_at TEXT,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    -- Free-guide landing pages = WP pages that embed a GoHighLevel form (same
    -- rule as C:\\DOMA-blog-migration\\75_resources_free_guides.py).
    CREATE TABLE IF NOT EXISTS free_guide_pages (
        page_path TEXT PRIMARY KEY,
        title TEXT,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS ga4_page_conversions_daily (
        report_date TEXT NOT NULL,
        page_path TEXT NOT NULL,
        sessions INTEGER NOT NULL DEFAULT 0,
        key_events REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (report_date, page_path)
    );

    -- Sponsor offers pages (/offers/ and /offers/<sponsor>/) and the outbound
    -- clicks from them (GA4 "click" event by linkDomain), per day.
    CREATE TABLE IF NOT EXISTS ga4_offers_pages_daily (
        report_date TEXT NOT NULL,
        page_path TEXT NOT NULL,
        page_views INTEGER NOT NULL DEFAULT 0,
        sessions INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (report_date, page_path)
    );

    CREATE TABLE IF NOT EXISTS ga4_offers_clicks_daily (
        report_date TEXT NOT NULL,
        page_path TEXT NOT NULL,
        link_domain TEXT NOT NULL,
        clicks INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (report_date, page_path, link_domain)
    );

    -- Site-wide "cta_click" GA4 event (tracking started 2026-10-15).
    CREATE TABLE IF NOT EXISTS ga4_cta_clicks_daily (
        report_date TEXT NOT NULL,
        page_path TEXT NOT NULL,
        link_text TEXT NOT NULL,
        link_url TEXT NOT NULL,
        clicks INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (report_date, page_path, link_text, link_url)
    );

    CREATE TABLE IF NOT EXISTS competitor_pagespeed (
        competitor_name TEXT NOT NULL,
        url TEXT NOT NULL,
        performance_score INTEGER,
        lcp_ms REAL,
        checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (url)
    );

    CREATE TABLE IF NOT EXISTS competitor_podcast_episodes (
        competitor_name TEXT NOT NULL,
        title TEXT NOT NULL,
        published_at TEXT,
        url TEXT NOT NULL,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (competitor_name, url)
    );

    -- GoHighLevel payments, aggregates only (no contact data). source_group
    -- is the buyer's lead-source group, looked up in memory during the sync.
    CREATE TABLE IF NOT EXISTS ghl_orders_daily (
        report_date TEXT NOT NULL,
        product TEXT NOT NULL,
        status TEXT NOT NULL,
        source_group TEXT NOT NULL DEFAULT 'Other',
        orders INTEGER NOT NULL DEFAULT 0,
        amount REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (report_date, product, status, source_group)
    );

    CREATE TABLE IF NOT EXISTS ghl_refunds_daily (
        report_date TEXT NOT NULL,
        product TEXT NOT NULL,
        refunded REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (report_date, product)
    );

    CREATE TABLE IF NOT EXISTS ghl_subscriptions_snapshot (
        snapshot_date TEXT NOT NULL,
        product TEXT NOT NULL,
        status TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 0,
        monthly_amount REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (snapshot_date, product, status)
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
        for table in ("content_posts_gsc", "content_posts_ga4"):
            if "title" not in {row["name"] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}:
                con.execute(f"ALTER TABLE {table} ADD COLUMN title TEXT")
        action_item_columns = {row["name"] for row in con.execute("PRAGMA table_info(team_action_items)").fetchall()}
        if "topic" not in action_item_columns:
            con.execute("ALTER TABLE team_action_items ADD COLUMN topic TEXT")
        email_columns = {row["name"] for row in con.execute("PRAGMA table_info(ghl_email_campaigns)").fetchall()}
        for col, coltype in [
            ("source", "TEXT NOT NULL DEFAULT 'ghl'"),
            ("sent_time", "TEXT"),
            ("scheduled_at", "TEXT"),
            ("delivered", "INTEGER"),
            ("delivered_rate", "REAL"),
            ("unsubscribes", "INTEGER"),
            ("unsubscribe_rate", "REAL"),
            ("hard_bounces", "INTEGER"),
            ("soft_bounces", "INTEGER"),
            ("bounce_rate", "REAL"),
            ("skipped", "INTEGER"),
            ("skipped_rate", "REAL"),
            ("total_clicks", "INTEGER"),
            ("ctor", "REAL"),
            ("top_links", "TEXT"),
            ("notes", "TEXT"),
        ]:
            if col not in email_columns:
                con.execute(f"ALTER TABLE ghl_email_campaigns ADD COLUMN {col} {coltype}")


def default_date_range(days: int = DEFAULT_LOOKBACK_DAYS) -> tuple[str, str]:
    # Exactly `days` days ending yesterday: GA4, GHL and Meta have no full day for today
    # yet, so ending today made every current period one data day short (and 31 days long).
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def search_console_summary(con: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, Any]:
    totals_row = con.execute(
        "SELECT COALESCE(SUM(clicks),0), COALESCE(SUM(impressions),0), "
        "COALESCE(SUM(position*impressions),0) "
        "FROM search_console_daily WHERE report_date BETWEEN ? AND ?",
        (start_date, end_date),
    ).fetchone()
    clicks, impressions, position_weighted = (
        int(totals_row[0] or 0),
        int(totals_row[1] or 0),
        float(totals_row[2] or 0),
    )
    # Table-wide, not range-limited: a range that sits past the newest synced
    # day (e.g. the last 7 days while the sync is down) has no rows, and
    # last_synced_at must still say when the sync last worked or the
    # dashboard can't tell "no data yet" from "sync is broken".
    last_synced_at = con.execute("SELECT MAX(synced_at) FROM search_console_daily").fetchone()[0]
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

    top_queries = _gsc_range_breakdown(con, "search_console_query_daily", "query", start_date, end_date, 25)
    if top_queries is None:
        top_queries = [
            {"query": row[0], "clicks": int(row[1] or 0), "impressions": int(row[2] or 0), "ctr": float(row[3] or 0), "position": float(row[4] or 0)}
            for row in con.execute(
                "SELECT query, clicks, impressions, ctr, position FROM search_console_queries ORDER BY clicks DESC LIMIT 25"
            ).fetchall()
        ]
    countries = _gsc_range_breakdown(con, "gsc_country_daily", "country", start_date, end_date, 15)
    if countries is None:
        countries = [
            {"country": row[0], "clicks": int(row[1] or 0), "impressions": int(row[2] or 0), "ctr": float(row[3] or 0), "position": float(row[4] or 0)}
            for row in con.execute(
                "SELECT country, clicks, impressions, ctr, position FROM gsc_countries ORDER BY clicks DESC LIMIT 15"
            ).fetchall()
        ]
    for row in countries:
        row["country_name"] = COUNTRY_NAMES.get(str(row["country"]).upper(), str(row["country"]).upper())
    devices = _gsc_range_breakdown(con, "gsc_device_daily", "device", start_date, end_date, 10)
    if devices is None:
        devices = [
            {"device": row[0], "clicks": int(row[1] or 0), "impressions": int(row[2] or 0), "ctr": float(row[3] or 0), "position": float(row[4] or 0)}
            for row in con.execute(
                "SELECT device, clicks, impressions, ctr, position FROM gsc_devices ORDER BY clicks DESC"
            ).fetchall()
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
        "countries": countries,
        "devices": devices,
        "nonbrand": _gsc_nonbrand(con, start_date, end_date, clicks, impressions),
        "queries_by_page": _gsc_queries_by_page(con, start_date, end_date),
    }


# Brand searches inflate clicks without saying anything about SEO growth.
# Matched case-insensitively against the GSC query text: the DOMA name and
# its long forms, the domain typed as words, Kyle Summerford (founder) and
# Dental Manager Connect (DOMA's old name).
BRAND_QUERY_RE = re.compile(
    r"\bdoma\b|dental\s*office\s*managers?\s*(alliance|association)|dentalofficemanagers"
    r"|kyle\s+summerford|summerford|dental\s+manager\s+connect",
    re.IGNORECASE,
)

# GSC reports countries as lowercase ISO-3166 alpha-3 codes.
COUNTRY_NAMES = {
    "USA": "United States", "CAN": "Canada", "GBR": "United Kingdom", "AUS": "Australia", "IND": "India",
    "PHL": "Philippines", "PAK": "Pakistan", "NGA": "Nigeria", "KEN": "Kenya", "ZAF": "South Africa",
    "MEX": "Mexico", "BRA": "Brazil", "ARG": "Argentina", "DEU": "Germany", "FRA": "France", "ESP": "Spain",
    "ITA": "Italy", "NLD": "Netherlands", "IRL": "Ireland", "NZL": "New Zealand", "ROU": "Romania",
    "POL": "Poland", "PRT": "Portugal", "GRC": "Greece", "HRV": "Croatia", "EST": "Estonia", "CHE": "Switzerland",
    "SWE": "Sweden", "NOR": "Norway", "DNK": "Denmark", "FIN": "Finland", "BEL": "Belgium", "AUT": "Austria",
    "ISR": "Israel", "EGY": "Egypt", "DZA": "Algeria", "MAR": "Morocco", "GHA": "Ghana", "ARE": "United Arab Emirates",
    "SAU": "Saudi Arabia", "TUR": "Turkey", "CHN": "China", "JPN": "Japan", "KOR": "South Korea", "SGP": "Singapore",
    "MYS": "Malaysia", "IDN": "Indonesia", "VNM": "Vietnam", "THA": "Thailand", "BGD": "Bangladesh", "LKA": "Sri Lanka",
    "NPL": "Nepal", "PRI": "Puerto Rico", "BRB": "Barbados", "JAM": "Jamaica", "TTO": "Trinidad and Tobago",
    "DOM": "Dominican Republic", "COL": "Colombia", "PER": "Peru", "CHL": "Chile", "VEN": "Venezuela",
    "ECU": "Ecuador", "UKR": "Ukraine", "RUS": "Russia", "ATA": "Antarctica", "ZZZ": "Unknown region",
    "GUY": "Guyana", "LCA": "Saint Lucia", "BHS": "Bahamas", "BLZ": "Belize", "CRI": "Costa Rica", "PAN": "Panama",
    "GTM": "Guatemala", "HND": "Honduras", "SLV": "El Salvador", "CUB": "Cuba", "HTI": "Haiti", "BMU": "Bermuda",
    "CYM": "Cayman Islands", "VIR": "U.S. Virgin Islands", "GUM": "Guam", "ETH": "Ethiopia", "UGA": "Uganda",
    "TZA": "Tanzania", "ZWE": "Zimbabwe", "ZMB": "Zambia", "CMR": "Cameroon", "TUN": "Tunisia", "JOR": "Jordan",
    "LBN": "Lebanon", "IRQ": "Iraq", "IRN": "Iran", "KWT": "Kuwait", "QAT": "Qatar", "OMN": "Oman", "BHR": "Bahrain",
    "HKG": "Hong Kong", "TWN": "Taiwan", "CZE": "Czechia", "HUN": "Hungary", "SVK": "Slovakia", "SVN": "Slovenia",
    "SRB": "Serbia", "BGR": "Bulgaria", "LTU": "Lithuania", "LVA": "Latvia", "ISL": "Iceland", "CYP": "Cyprus",
    "MLT": "Malta", "LUX": "Luxembourg", "ALB": "Albania", "BIH": "Bosnia and Herzegovina", "MKD": "North Macedonia",
    "GEO": "Georgia", "ARM": "Armenia", "AZE": "Azerbaijan", "KAZ": "Kazakhstan", "UZB": "Uzbekistan", "AFG": "Afghanistan",
    "MMR": "Myanmar", "KHM": "Cambodia", "URY": "Uruguay", "PRY": "Paraguay", "BOL": "Bolivia", "NIC": "Nicaragua",
}


def _gsc_range_breakdown(con: sqlite3.Connection, table: str, key: str, start_date: str, end_date: str, limit: int) -> list[dict[str, Any]] | None:
    """Top rows of a per-day GSC table summed over the range. Returns None
    while the table is empty, so the caller can fall back to the old
    180-day snapshot table."""
    if not con.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
        return None
    rows = con.execute(
        f"SELECT {key}, SUM(clicks), SUM(impressions), SUM(position * impressions) FROM {table} "
        f"WHERE report_date BETWEEN ? AND ? GROUP BY {key} ORDER BY SUM(clicks) DESC, SUM(impressions) DESC LIMIT ?",
        (start_date, end_date, limit),
    ).fetchall()
    out = []
    for value, clicks, impressions, weighted in rows:
        clicks, impressions = int(clicks or 0), int(impressions or 0)
        out.append({
            key: value,
            "clicks": clicks,
            "impressions": impressions,
            "ctr": round(clicks / impressions * 100, 2) if impressions else 0.0,
            "position": round(float(weighted or 0) / impressions, 1) if impressions else 0.0,
        })
    return out


def _gsc_nonbrand(con: sqlite3.Connection, start_date: str, end_date: str, total_clicks: int, total_impressions: int) -> dict[str, Any] | None:
    """Clicks/impressions without brand searches. GSC hides rare (anonymized)
    queries from the per-query report, and those are almost never brand, so
    non-brand = site total minus brand queries, not the sum of the visible
    non-brand queries (that would undercount by the anonymized share).
    Position is impression-weighted over the visible non-brand queries."""
    if not con.execute("SELECT 1 FROM search_console_query_daily LIMIT 1").fetchone():
        return None
    brand_clicks = brand_impressions = 0
    nb_impressions = 0
    nb_weighted = 0.0
    for query, clicks, impressions, weighted in con.execute(
        "SELECT query, SUM(clicks), SUM(impressions), SUM(position * impressions) FROM search_console_query_daily "
        "WHERE report_date BETWEEN ? AND ? GROUP BY query",
        (start_date, end_date),
    ):
        if BRAND_QUERY_RE.search(query or ""):
            brand_clicks += int(clicks or 0)
            brand_impressions += int(impressions or 0)
        else:
            nb_impressions += int(impressions or 0)
            nb_weighted += float(weighted or 0)
    clicks = max(total_clicks - brand_clicks, 0)
    impressions = max(total_impressions - brand_impressions, 0)
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": round(clicks / impressions * 100, 2) if impressions else 0.0,
        "position": round(nb_weighted / nb_impressions, 1) if nb_impressions else 0.0,
        "brand_clicks": brand_clicks,
    }


def _gsc_queries_by_page(con: sqlite3.Connection, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Top 10 pages by impressions in the range (gsc_page_daily), each with
    its top 5 queries. The queries come from the 90-day [page, query]
    snapshot (gsc_page_queries), not the range: see the table comment."""
    pages = con.execute(
        "SELECT page, SUM(clicks), SUM(impressions), SUM(position * impressions) FROM gsc_page_daily "
        "WHERE report_date BETWEEN ? AND ? GROUP BY page ORDER BY SUM(impressions) DESC LIMIT 10",
        (start_date, end_date),
    ).fetchall()
    out = []
    for page, clicks, impressions, weighted in pages:
        impressions = int(impressions or 0)
        queries = [
            {"query": q, "clicks": int(c or 0), "impressions": int(i or 0), "position": round(float(p or 0), 1)}
            for q, c, i, p in con.execute(
                "SELECT query, clicks, impressions, position FROM gsc_page_queries WHERE page = ? "
                "ORDER BY impressions DESC LIMIT 5",
                (page,),
            )
        ]
        out.append({
            "page": page,
            "clicks": int(clicks or 0),
            "impressions": impressions,
            "position": round(float(weighted or 0) / impressions, 1) if impressions else 0.0,
            "queries": queries,
        })
    return out


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


def _fetch_recent_social_posts(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT platform, post_id, caption, published_at, permalink, likes, comments, shares, reach, engagement_total "
        "FROM social_posts ORDER BY published_at DESC LIMIT 40"
    ).fetchall()
    return [
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
        for row in rows
    ]


def content_suggestions_summary(con: sqlite3.Connection) -> dict[str, Any]:
    """Content Suggestions tab - doesn't sync anything new, just pulls
    together signals that already live in other tabs (content gaps from
    SEO, top-performing ebooks/blog posts from GA4, best posting times from
    Social) so planning the next piece of content doesn't mean hopping
    between four different tabs. Requested after a real meeting where Kyle
    and Juli read these exact numbers off separate tabs by hand."""
    gaps = content_gap_summary(con)

    def _top_pages(where_clause: str, params: tuple, limit: int = 10) -> list[dict[str, Any]]:
        rows = con.execute(
            f"SELECT page_path, page_title, sessions, page_views FROM ga4_top_pages "
            f"WHERE {where_clause} ORDER BY sessions DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [
            {"page_path": row[0], "page_title": row[1], "sessions": int(row[2] or 0), "page_views": int(row[3] or 0)}
            for row in rows
        ]

    # Free guides = WP pages with a GHL form (free_guide_pages, filled by
    # sync_ga4). Matching "guide" in the title pulled in the management guide,
    # the salary guide, D4260 and a sponsor review (review of 2026-10-15).
    # The title match stays only as a fallback until that table is filled.
    if con.execute("SELECT 1 FROM free_guide_pages LIMIT 1").fetchone():
        top_ebooks = _top_pages("page_path IN (SELECT page_path FROM free_guide_pages)", ())
    else:
        top_ebooks = _top_pages(
            "(page_path LIKE ? OR page_title LIKE ? OR page_title LIKE ?) AND page_path NOT LIKE '%thank-you%'",
            ("%ebook%", "%ebook%", "%guide%"),
        )
    # Real posts only: /post/speaker-directory/* and /post/events/* are other
    # post types that share the /post/ prefix.
    if con.execute("SELECT 1 FROM wp_posts LIMIT 1").fetchone():
        top_blog_posts = _top_pages("page_path IN (SELECT page_path FROM wp_posts)", ())
    else:
        top_blog_posts = _top_pages(
            "page_path LIKE '/post/%' AND page_path NOT LIKE '/post/speaker-directory/%' AND page_path NOT LIKE '/post/events/%'", ()
        )
    titles = _page_titles(con)
    for row in top_ebooks + top_blog_posts:
        row["page_title"] = titles.get(row["page_path"]) or clean_page_title(row["page_path"], row["page_title"])
    best_post_times = _instagram_best_times(_fetch_recent_social_posts(con))

    return {
        "available": bool(gaps["gaps"] or top_ebooks or top_blog_posts),
        "content_gaps": gaps["gaps"][:10],
        "content_gaps_checked_at": gaps["checked_at"],
        "top_ebooks": top_ebooks,
        "top_blog_posts": top_blog_posts,
        "best_post_times": best_post_times,
    }


def content_gap_summary(con: sqlite3.Connection) -> dict[str, Any]:
    """Queries with real impressions where DOMA's best-ranking page still
    isn't in the top 15 - content opportunities, see sync_gsc.py:fetch_content_gaps
    for the exact thresholds."""
    rows = con.execute(
        "SELECT query, page, position, impressions, clicks FROM gsc_content_gaps "
        "ORDER BY impressions DESC LIMIT 30"
    ).fetchall()
    titles = _page_titles(con)
    gaps = []
    for query, page, position, impressions, clicks in rows:
        position, impressions, clicks = float(position or 0), int(impressions or 0), int(clicks or 0)
        path = urlsplit(page or "").path or "/"
        path = path if path.endswith("/") else path + "/"  # GSC mixes /offers and /offers/
        gaps.append({
            "query": query,
            "page": page,
            "position": position,
            "impressions": impressions,
            "clicks": clicks,
            "relevant": _gap_is_relevant(query, path),
            "action": _gap_action(path, position, clicks, impressions, titles),
        })
    # relevant gaps first, so the top 10 in Content Suggestions is real pauta
    gaps.sort(key=lambda g: (not g["relevant"], -g["impressions"]))
    checked_at_row = con.execute("SELECT MAX(synced_at) FROM gsc_content_gaps").fetchone()
    return {"available": bool(gaps), "gaps": gaps, "checked_at": checked_at_row[0] if checked_at_row else None}


# Pages that rank for vendor searches because sponsors are listed there, not
# because DOMA wrote about the topic: /offers/ (sponsor deals), the partner
# pages, and sponsor review posts ("...-review", "...-review-2026").
SPONSOR_PATH_RE = re.compile(r"^/(offers|partner|influence-alliance|sponsors?)(/|$)|-review(-\d{4})?/?$", re.IGNORECASE)
# Searches for a vendor's marketing package or price ("dental cmo pricing",
# "all in one dental marketing package") are not DOMA topics.
VENDOR_QUERY_RE = re.compile(r"\b(pricing|prices?|packages?|marketing|cmo|agency|gift cards?)\b", re.IGNORECASE)
# Listing pages: when one of these is the best page, no page targets the query.
LISTING_PATHS = {"/", "/home/", "/blog/", "/events/", "/offers/", "/forms/", "/speaker-directory/", "/podcast/", "/courses/"}


def _gap_is_relevant(query: str, path: str) -> bool:
    return not (SPONSOR_PATH_RE.search(path) or VENDOR_QUERY_RE.search(query or ""))


def _gap_action(path: str, position: float, clicks: int, impressions: int, titles: dict[str, str]) -> str:
    """What to do with a gap. Past position 10, a listing page as best page
    means nothing targets the query (new post); a dedicated page gets
    strengthened, also past 30, since it still beats starting over."""
    if position <= 10:
        ctr = clicks / impressions * 100 if impressions else 0.0
        return "Improve title/meta" if ctr < 2 else "Keep"
    targeted = path not in LISTING_PATHS and "/category/" not in path and "/tag/" not in path
    if not targeted:
        return "New post"
    return f"Strengthen {titles.get(path) or title_from_path(path)}"


def _page_titles(con: sqlite3.Connection) -> dict[str, str]:
    """path -> best known title: WordPress title first, then the cleaned GA4 one."""
    titles = {row[0]: clean_page_title(row[0], row[1]) for row in con.execute("SELECT page_path, page_title FROM ga4_top_pages")}
    titles.update({row[0]: row[1] for row in con.execute("SELECT page_path, title FROM free_guide_pages WHERE title IS NOT NULL")})
    titles.update({row[0]: row[1] for row in con.execute("SELECT page_path, title FROM wp_posts WHERE title IS NOT NULL")})
    return titles


def title_from_path(path: str) -> str:
    slug = [part for part in (path or "").split("/") if part]
    if not slug:
        return "Home"
    return re.sub(r"[-_]+", " ", slug[-1]).strip().capitalize()


# GA4 keeps the <title> a page had when it was visited, site suffix included.
# "/" was once "Dental AI Event Anaheim 2026 | ...", so it is always "Home".
TITLE_SUFFIX_RE = re.compile(r"\s*(-\s*Dental Office Managers Alliance\s*)?\|\s*DOMA\s*$", re.IGNORECASE)


def clean_page_title(path: str, title: str | None) -> str | None:
    if path in ("/", "/home/"):
        return "Home"
    if not title:
        return title
    cleaned = TITLE_SUFFIX_RE.sub("", html.unescape(title)).strip()
    return cleaned or title


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
                # Lighthouse sometimes records blank entries; a list of empty
                # strings used to raise a "throws console errors" alert.
                "console_errors": [e for e in (json.loads(row[10]) if row[10] else []) if str(e).strip()],
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


def team_meetings_summary(con: sqlite3.Connection, weeks: int = 6) -> dict[str, Any]:
    """Weekly team meeting recaps turned into a trackable action-item
    checklist - who owns what, and whether it's been marked done by the
    next meeting. Meetings and their action items are entered manually
    (scripts/add_meeting.py) since there's no meeting-transcription API
    wired in here - Claude extracts the checklist from a pasted recap/
    transcript at meeting time, same "manual research, structured entry"
    pattern as the Ad Spy panel."""
    cutoff = (date.today() - timedelta(weeks=weeks)).isoformat()
    meetings_rows = con.execute(
        "SELECT id, meeting_date, title, summary, raw_notes FROM team_meetings WHERE meeting_date >= ? ORDER BY meeting_date DESC",
        (cutoff,),
    ).fetchall()
    meetings = []
    for m_id, m_date, title, summary, raw_notes in meetings_rows:
        items = con.execute(
            "SELECT id, owner, description, status, context, completed_at, topic FROM team_action_items "
            "WHERE meeting_id = ? ORDER BY owner, id",
            (m_id,),
        ).fetchall()
        meetings.append(
            {
                "id": m_id,
                "meeting_date": m_date,
                "title": title,
                "summary": summary,
                "raw_notes": raw_notes,
                "action_items": [
                    {
                        "id": row[0],
                        "owner": row[1],
                        "description": row[2],
                        "status": row[3],
                        "context": row[4],
                        "completed_at": row[5],
                        "topic": row[6] or "General",
                    }
                    for row in items
                ],
            }
        )

    open_rows = con.execute(
        "SELECT owner, COUNT(*) FROM team_action_items WHERE status = 'open' GROUP BY owner ORDER BY COUNT(*) DESC"
    ).fetchall()
    total_open = sum(row[1] for row in open_rows)
    total_all = con.execute("SELECT COUNT(*) FROM team_action_items").fetchone()[0]
    total_in_progress = con.execute("SELECT COUNT(*) FROM team_action_items WHERE status = 'in_progress'").fetchone()[0]
    total_done = con.execute("SELECT COUNT(*) FROM team_action_items WHERE status = 'done'").fetchone()[0]

    return {
        "available": bool(meetings),
        "meetings": meetings,
        "open_by_owner": [{"owner": row[0], "count": row[1]} for row in open_rows],
        "total_open": total_open,
        "total_in_progress": total_in_progress,
        "total_done": total_done,
        "total_all": total_all,
    }


def competitor_tech_summary(con: sqlite3.Connection) -> dict[str, Any]:
    """Marketing/tech stack fingerprint per tracked competitor - detected
    by fetching each competitor's own public homepage HTML and matching
    known signatures (WordPress, HubSpot, Klaviyo, Meta Pixel, etc), the
    same technique a browser extension like Wappalyzer uses. Not a guess
    or a paid lookup - a live check of what that competitor's own page
    actually loads."""
    import json

    rows = con.execute(
        "SELECT competitor_name, domain, signals, checked_at FROM competitor_tech_stack ORDER BY competitor_name"
    ).fetchall()
    entries = [
        {"competitor_name": row[0], "domain": row[1], "signals": json.loads(row[2]) if row[2] else [], "checked_at": row[3]}
        for row in rows
    ]
    return {"available": bool(entries), "entries": entries}


def competitor_wayback_summary(con: sqlite3.Connection) -> dict[str, Any]:
    """How often a tracked competitor's site has actually changed, per the
    Internet Archive's Wayback Machine (web.archive.org) - a free, public,
    no-key API. Snapshot count/frequency is a rough proxy for how actively
    a competitor maintains their site, not a direct measure of traffic or
    revenue."""
    rows = con.execute(
        "SELECT competitor_name, domain, total_snapshots, first_snapshot, last_snapshot, snapshots_last_90d, checked_at "
        "FROM competitor_wayback_history ORDER BY competitor_name"
    ).fetchall()
    entries = [
        {
            "competitor_name": row[0],
            "domain": row[1],
            "total_snapshots": row[2],
            "first_snapshot": row[3],
            "last_snapshot": row[4],
            "snapshots_last_90d": row[5],
            "checked_at": row[6],
        }
        for row in rows
    ]
    return {"available": bool(entries), "entries": entries}


def ad_spy_summary(con: sqlite3.Connection) -> dict[str, Any]:
    """Paid-ads competitive intelligence - manually researched (Meta Ads
    Library, Google Ads Transparency Center, TikTok Creative Center,
    LinkedIn Ads Library don't have public APIs for this), entered via
    scripts/add_ad_spy_entry.py after browsing those libraries by hand.
    Not auto-synced like the rest of this dashboard - see README.md
    "Ad Spy" section."""
    rows = con.execute(
        "SELECT competitor, platform, date_found, status_observed, format, hook, offer, cta, "
        "proof, strategic_hypothesis, link, notes FROM ad_spy_entries ORDER BY date_found DESC, id DESC"
    ).fetchall()
    entries = [
        {
            "competitor": row[0],
            "platform": row[1],
            "date_found": row[2],
            "status_observed": row[3],
            "format": row[4],
            "hook": row[5],
            "offer": row[6],
            "cta": row[7],
            "proof": row[8],
            "strategic_hypothesis": row[9],
            "link": row[10],
            "notes": row[11],
        }
        for row in rows
    ]
    by_competitor = con.execute(
        "SELECT competitor, COUNT(*) FROM ad_spy_entries GROUP BY competitor ORDER BY COUNT(*) DESC"
    ).fetchall()
    checked_at_row = con.execute("SELECT MAX(created_at) FROM ad_spy_entries").fetchone()
    return {
        "available": bool(entries),
        "entries": entries,
        "by_competitor": [{"competitor": row[0], "count": row[1]} for row in by_competitor],
        "last_updated": checked_at_row[0] if checked_at_row else None,
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


def competitor_content_summary(con: sqlite3.Connection, per_competitor: int = 8) -> dict[str, Any]:
    """Most recently updated/published pages per tracked competitor, sorted
    by the competitor's own sitemap `lastmod` rather than `first_seen_at`.
    `lastmod` is missing on some sites, so those pages sort last within
    their own competitor rather than being dropped. Capped per competitor so
    one big sitemap can't crowd out the others.

    Bulk-stamped lastmods are dropped: when more than BULK_LASTMOD_MIN URLs
    of one competitor share the same lastmod day, that day is a sitemap
    rebuild (Dental A Team stamps every podcast page with the fetch day), not
    real edits. This is a bit wider than "lastmod = fetch day": Weave stamped
    136 pages with the day before our fetch.

    `new_pages` is the cleaner signal: pages first seen after the
    competitor's baseline, see _competitor_baseline_days."""
    rows = con.execute(
        """
        WITH bulk AS (
            SELECT competitor_name, substr(lastmod, 1, 10) AS day FROM competitor_pages
            WHERE lastmod IS NOT NULL AND lastmod <> ''
            GROUP BY competitor_name, substr(lastmod, 1, 10) HAVING COUNT(*) > ?
        )
        SELECT competitor_name, domain, url, lastmod, first_seen_at FROM (
            SELECT p.*, ROW_NUMBER() OVER (
                PARTITION BY p.competitor_name
                -- a lastmod in the future (Dental A Team stamps 2020 podcasts with
                -- next month's date) or before 2000 (1970 epoch) counts as missing
                ORDER BY (p.lastmod IS NULL OR p.lastmod = '' OR substr(p.lastmod, 1, 10) > date('now')
                          OR substr(p.lastmod, 1, 4) < '2000'), p.lastmod DESC
            ) AS rn
            FROM competitor_pages p
            WHERE NOT EXISTS (
                SELECT 1 FROM bulk b WHERE b.competitor_name = p.competitor_name AND b.day = substr(p.lastmod, 1, 10)
            )
        )
        WHERE rn <= ?
        ORDER BY competitor_name, rn
        """,
        (BULK_LASTMOD_MIN, per_competitor),
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
        "new_pages": _competitor_new_pages(con),
        "totals": [{"competitor_name": row[0], "total_pages_tracked": row[1]} for row in totals],
        "pagespeed": [
            {"competitor_name": r[0], "url": r[1], "performance_score": r[2], "lcp_ms": r[3], "checked_at": r[4]}
            for r in con.execute(
                "SELECT competitor_name, url, performance_score, lcp_ms, checked_at FROM competitor_pagespeed "
                "ORDER BY performance_score DESC"
            )
        ],
        "podcasts": [
            {"competitor_name": r[0], "title": r[1], "published_at": r[2], "url": r[3]}
            for r in con.execute(
                "SELECT competitor_name, title, published_at, url FROM ("
                " SELECT *, ROW_NUMBER() OVER (PARTITION BY competitor_name ORDER BY published_at DESC) AS rn"
                " FROM competitor_podcast_episodes) WHERE rn <= 5 ORDER BY competitor_name, published_at DESC"
            )
        ],
        "checked_at": checked_at_row[0] if checked_at_row else None,
    }


BULK_LASTMOD_MIN = 20
# A day on which one competitor gained more than this many "new" URLs is a
# crawl change on our side (MAX_URLS went from 800 to 4000 on 2026-10-15 and
# Dental A Team got 1,100 old pages at once), not new content.
BULK_DISCOVERY_MIN = 50
NEW_PAGES_DAYS = 30


def _competitor_baseline_days(con: sqlite3.Connection) -> dict[str, set[str]]:
    """Days whose first-seen pages are baseline, per competitor: the first
    sync day, plus any day with a bulk discovery (see BULK_DISCOVERY_MIN)."""
    days: dict[str, set[str]] = {}
    for name, day, count, first_day in con.execute(
        "SELECT competitor_name, date(first_seen_at), COUNT(*), "
        "(SELECT MIN(date(first_seen_at)) FROM competitor_pages p2 WHERE p2.competitor_name = p.competitor_name) "
        "FROM competitor_pages p GROUP BY competitor_name, date(first_seen_at)"
    ):
        if day == first_day or count > BULK_DISCOVERY_MIN:
            days.setdefault(name, set()).add(day)
    return days


def _competitor_new_pages(con: sqlite3.Connection, limit: int = 40) -> list[dict[str, Any]]:
    """Pages first seen in the last NEW_PAGES_DAYS days, outside baseline days, newest first."""
    baseline = _competitor_baseline_days(con)
    since = (date.today() - timedelta(days=NEW_PAGES_DAYS)).isoformat()
    out = []
    for name, domain, url, lastmod, first_seen in con.execute(
        "SELECT competitor_name, domain, url, lastmod, first_seen_at FROM competitor_pages "
        "WHERE date(first_seen_at) >= ? ORDER BY first_seen_at DESC",
        (since,),
    ):
        if first_seen[:10] in baseline.get(name, set()):
            continue
        out.append({
            "competitor_name": name,
            "domain": domain,
            "url": url,
            "title": title_from_path(urlsplit(url).path),
            "first_seen_at": first_seen,
            "lastmod": lastmod,
        })
        if len(out) >= limit:
            break
    return out


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


DOMA_DOMAIN = "dentalofficemanagers.com"


def _where_doma_action(position: float | None) -> str:
    if position is None or position > 20:
        return "Build a dedicated page"
    if position <= 3:
        return "Defend: keep the page fresh"
    if position <= 10:
        return "Improve title and meta, add FAQ schema"
    return "Strengthen the page and its internal links"


def serp_where_doma_stands(con: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, Any]:
    """One row per search checked on Brave (latest check per query, table
    serp_query_results written by scripts/sync_serp_competitors.py): DOMA's
    Google position for the range (impression-weighted Search Console average,
    falling back to the last 90 days of data when the range has none), DOMA's
    Brave position, the domains ranking above DOMA on Brave, and what to do."""
    try:
        results = con.execute(
            "SELECT r.query, r.position, r.domain, r.checked_at FROM serp_query_results r "
            "JOIN (SELECT query, MAX(checked_at) AS last FROM serp_query_results WHERE provider = 'brave' GROUP BY query) m "
            "ON m.query = r.query AND m.last = r.checked_at WHERE r.provider = 'brave' ORDER BY r.query, r.position"
        ).fetchall()
    except sqlite3.OperationalError:  # table not created yet (first SERP sync not run)
        return {"available": False, "rows": []}

    by_query: dict[str, dict[str, Any]] = {}
    for query, position, domain, checked_at in results:
        entry = by_query.setdefault(query, {"results": [], "checked_at": checked_at})
        entry["results"].append((int(position), (domain or "").lower().removeprefix("www.")))

    last_gsc_day = con.execute("SELECT MAX(report_date) FROM search_console_query_daily").fetchone()[0]
    fallback_start = (date.fromisoformat(last_gsc_day) - timedelta(days=89)).isoformat() if last_gsc_day else None

    def google(query: str, start: str, end: str) -> tuple[float | None, int, int]:
        row = con.execute(
            "SELECT SUM(position * impressions), SUM(impressions), SUM(clicks) FROM search_console_query_daily "
            "WHERE lower(query) = lower(?) AND report_date BETWEEN ? AND ?",
            (query, start, end),
        ).fetchone()
        impressions = int(row[1] or 0)
        return (round(row[0] / impressions, 1) if impressions else None), impressions, int(row[2] or 0)

    rows = []
    for query, entry in by_query.items():
        position, impressions, clicks = google(query, start_date, end_date)
        window = "range"
        if position is None and fallback_start:
            position, impressions, clicks = google(query, fallback_start, last_gsc_day)
            window = "last_90_days" if position is not None else "none"
        brave = next((pos for pos, domain in entry["results"] if domain == DOMA_DOMAIN), None)
        ahead: list[str] = []
        for pos, domain in entry["results"]:
            if brave is not None and pos >= brave:
                break
            if domain and domain != DOMA_DOMAIN and domain not in ahead:
                ahead.append(domain)
            if len(ahead) == 3:
                break
        rows.append(
            {
                "query": query,
                "google_position": position,
                "google_window": window,
                "impressions": impressions,
                "clicks": clicks,
                "brave_position": brave,
                "ahead": ahead,
                "action": _where_doma_action(position),
                "checked_at": entry["checked_at"],
            }
        )
    rows.sort(key=lambda r: (-r["impressions"], r["query"]))
    return {"available": bool(rows), "rows": rows, "checked_at": max((r["checked_at"] for r in rows), default=None)}


# --- Competitor insights (Competitors tab, 2026-10-17) -------------------------------
# Searches tracked on Brave (serp_query_results) grouped into topics a marketer acts on.
# DOMA's position comes from Search Console (real Google), who holds the top 3 from Brave.
COMPETITOR_TOPICS = [
    ("Core term: dental office manager", re.compile(r"^dental office managers?$", re.I)),
    ("Courses & training", re.compile(r"course|training|seminar|certif|class", re.I)),
    ("Conferences & events", re.compile(r"conference|event|summit", re.I)),
    ("SRP narratives", re.compile(r"\bsrp\b|narrative", re.I)),
    ("Salary", re.compile(r"salary|wage", re.I)),
    ("Community & membership", re.compile(r"community|group|association|membership|resources|network", re.I)),
    ("AI in the practice", re.compile(r"\bai\b|reactivation|chatgpt", re.I)),
    ("Running the office", re.compile(r"managing|management|operations|manual", re.I)),
]
# Brand and sponsor research searches that sit in the tracker but say nothing about the market.
NOT_MARKET = re.compile(r"janie|pain.?free|kyle|vaysman|affiliate|^in person$|^for hygienist$", re.I)
TOPIC_STATUS = {
    "leading": ("Leading", "Defend: refresh the page and keep related posts linking to it"),
    "page1": ("Page 1, not top 3", "Win the click: sharper title and description, add an FAQ block"),
    "page2": ("Page 2: close", "Push to page 1: expand the page that ranks and link to it from related posts"),
    "behind": ("Behind", "Build a dedicated page for this topic"),
}


def _topic_for(query: str) -> str | None:
    if NOT_MARKET.search(query):
        return None
    for name, pattern in COMPETITOR_TOPICS:
        if pattern.search(query):
            return name
    return None


def _status_for(position: float | None) -> str:
    if position is None or position > 20.5:
        return "behind"
    if position <= 3.5:
        return "leading"
    return "page1" if position <= 10.5 else "page2"


def _thousands(n: int) -> str:
    return f"{n:,}"


def competitor_insights(con: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, Any]:
    """Topics, rivals, a per-competitor scorecard and the takeaways shown first on the
    Competitors tab. Brave gives who ranks; Search Console gives where DOMA really is."""
    try:
        results = con.execute(
            "SELECT r.query, r.position, r.domain, r.checked_at FROM serp_query_results r "
            "JOIN (SELECT query, MAX(checked_at) AS last FROM serp_query_results WHERE provider = 'brave' GROUP BY query) m "
            "ON m.query = r.query AND m.last = r.checked_at WHERE r.provider = 'brave' ORDER BY r.query, r.position"
        ).fetchall()
    except sqlite3.OperationalError:
        return {"available": False}
    if not results:
        return {"available": False}

    serp: dict[str, list[tuple[int, str]]] = {}
    checked_at = max(r[3] for r in results)
    for query, position, domain, _ in results:
        serp.setdefault(query, []).append((int(position), (domain or "").lower().removeprefix("www.")))

    # Search Console lags 2-3 days: same-length window ending on its newest day.
    last_gsc = con.execute("SELECT MAX(report_date) FROM search_console_query_daily").fetchone()[0]
    span = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1
    gsc_end = min(end_date, last_gsc) if last_gsc else end_date
    gsc_start = (date.fromisoformat(gsc_end) - timedelta(days=span - 1)).isoformat()

    def google(query: str) -> tuple[float | None, int, int]:
        row = con.execute(
            "SELECT SUM(position * impressions), SUM(impressions), SUM(clicks) FROM search_console_query_daily "
            "WHERE lower(query) = lower(?) AND report_date BETWEEN ? AND ?",
            (query, gsc_start, gsc_end),
        ).fetchone()
        imp = int(row[1] or 0)
        return (round(row[0] / imp, 1) if imp else None), imp, int(row[2] or 0)

    queries, other = [], []
    for query, ranked in serp.items():
        topic = _topic_for(query)
        if not topic:
            other.append(query)
            continue
        position, impressions, clicks = google(query)
        brave = next((pos for pos, dom in ranked if dom == DOMA_DOMAIN), None)
        top3 = [dom for pos, dom in sorted(ranked)[:3]]
        ahead = [dom for pos, dom in sorted(ranked) if dom != DOMA_DOMAIN and (brave is None or pos < brave)]
        queries.append({"query": query, "topic": topic, "google_position": position, "impressions": impressions,
                        "clicks": clicks, "brave_position": brave, "top3": top3, "ahead": list(dict.fromkeys(ahead))})

    topics = []
    for name, _ in COMPETITOR_TOPICS:
        rows = [q for q in queries if q["topic"] == name]
        if not rows:
            continue
        imp = sum(r["impressions"] for r in rows)
        clicks = sum(r["clicks"] for r in rows)
        weighted = sum((r["google_position"] or 0) * r["impressions"] for r in rows)
        position = round(weighted / imp, 1) if imp else None
        owners = Counter(d for r in rows for d in r["top3"] if d != DOMA_DOMAIN)
        best_brave = min((r["brave_position"] for r in rows if r["brave_position"]), default=None)
        # Google decides when it has impressions; with none, Brave is the only signal and says so.
        source = "google" if imp else "brave"
        status = _status_for(position if imp else best_brave)
        topics.append({
            "topic": name, "queries": [r["query"] for r in sorted(rows, key=lambda r: -r["impressions"])],
            "impressions": imp, "clicks": clicks, "ctr": round(clicks / imp * 100, 1) if imp else None,
            "google_position": position, "best_brave": best_brave, "status_source": source,
            "brave_disagrees": bool(imp and best_brave and best_brave <= 3 and position and position > 10.5),
            "owners": [d for d, _ in owners.most_common(3)], "status": status,
            "status_label": TOPIC_STATUS[status][0], "action": TOPIC_STATUS[status][1],
        })
    topics.sort(key=lambda t: -t["impressions"])

    # Who outranks DOMA most often on market searches (Brave).
    beats = Counter(d for q in queries for d in q["ahead"])
    rivals = [{"domain": d, "searches_ahead": n} for d, n in beats.most_common(6)]

    # Competitor scorecard from the other trackers.
    names = con.execute("SELECT competitor_name, domain, COUNT(*) FROM competitor_pages GROUP BY 1, 2").fetchall()
    sections: dict[str, Counter] = {}
    for name, url in con.execute("SELECT competitor_name, url FROM competitor_pages"):
        seg = re.sub(r"^https?://[^/]+/", "", url or "").split("/")[0] or "home"
        sections.setdefault(name, Counter())[seg] += 1
    pods = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT competitor_name, SUM(published_at >= date('now','-30 day')), MAX(published_at) "
        "FROM competitor_podcast_episodes GROUP BY 1")}
    speed = {r[0]: r[1] for r in con.execute("SELECT competitor_name, performance_score FROM competitor_pagespeed")}
    tools: dict[str, list[str]] = {}
    for name, signals in con.execute("SELECT competitor_name, signals FROM competitor_tech_stack"):
        try:
            tools[name] = json.loads(signals or "[]")
        except ValueError:
            tools[name] = []
    scorecard = []
    for name, domain, pages in names:
        dom = (domain or "").lower().removeprefix("www.")
        on_ours = sum(1 for q in queries if dom in {d for _, d in serp[q["query"]]})
        ahead = sum(1 for q in queries if dom in q["ahead"])
        pod = pods.get(name, (0, None))
        scorecard.append({
            "name": name, "domain": dom, "pages": pages,
            "sections": [k for k, _ in sections.get(name, Counter()).most_common(2)],
            "podcast_last_30d": int(pod[0] or 0), "podcast_last": (pod[1] or "")[:10] or None,
            "pagespeed": speed.get(name), "tools": tools.get(name, []),
            "on_our_searches": on_ours, "ahead_of_doma": ahead,
        })
    scorecard.sort(key=lambda r: (-r["ahead_of_doma"], -r["on_our_searches"], r["name"]))
    row = con.execute(
        "SELECT performance_score FROM pagespeed_audit WHERE url = ? AND strategy = 'mobile' ORDER BY rowid DESC LIMIT 1",
        ("https://dentalofficemanagers.com/",),
    ).fetchone()
    doma_speed = row[0] if row else None

    # Takeaways: the few sentences to read first, each with one action.
    takeaways = []
    close = [t for t in topics if t["status"] in ("page2", "page1") and t["impressions"]]
    if close:
        t = max(close, key=lambda t: t["impressions"])
        takeaways.append({
            "kind": "opportunity", "title": f"Biggest opening: {t['topic'].lower()}",
            "detail": f"DOMA sits at position {t['google_position']} on Google for these searches "
                      f"({_thousands(t['impressions'])} impressions, {t['clicks']} clicks)."
                      + (f" The top spots on Brave go to {' and '.join(t['owners'][:2])}." if t["owners"] else ""),
            "action": t["action"]})
    wins = sorted((q for q in queries if q["google_position"] and q["google_position"] <= 5.5 and q["impressions"] >= 10),
                  key=lambda q: -q["impressions"])[:3]
    if wins:
        takeaways.append({
            "kind": "strength", "title": "Where DOMA already wins on Google",
            "detail": "Top 5 for " + ", ".join(f"\"{q['query']}\" ({q['google_position']})" for q in wins) + ".",
            "action": TOPIC_STATUS["leading"][1]})
    split = [t for t in topics if t["brave_disagrees"]]
    if split:
        t = max(split, key=lambda t: t["impressions"])
        takeaways.append({
            "kind": "risk", "title": f"Strong on Brave, weak on Google: {t['topic'].lower()}",
            "detail": f"Brave puts DOMA at #{t['best_brave']}, Google at {t['google_position']} ({_thousands(t['impressions'])} impressions).",
            "action": "Improve the page Google shows for these searches; Google sends most of the traffic."})
    if rivals:
        top = rivals[0]["searches_ahead"]
        tied = [r["domain"] for r in rivals if r["searches_ahead"] == top]
        takeaways.append({
            "kind": "rival", "title": "Main rivals in search: " + ", ".join(tied),
            "detail": f"{'Each ranks' if len(tied) > 1 else 'It ranks'} above DOMA on {top} of {len(queries)} market searches on Brave.",
            "action": "Compare their top pages with ours on the topics they hold (table below)."})
    low_ctr = [t for t in topics if t["impressions"] >= 50 and (t["ctr"] or 0) < 2
               and t["google_position"] and t["google_position"] <= 10.5]
    if low_ctr:
        t = max(low_ctr, key=lambda t: t["impressions"])
        takeaways.append({
            "kind": "ctr", "title": f"Seen but not clicked: {t['topic'].lower()}",
            "detail": f"{_thousands(t['impressions'])} impressions and {t['ctr']}% click-through at position {t['google_position']}.",
            "action": "Rewrite the title and description of the ranking page around the search wording."})
    active_pods = [c for c in scorecard if c["podcast_last_30d"]]
    if active_pods:
        takeaways.append({
            "kind": "content", "title": "Competitors publishing podcasts",
            "detail": ", ".join(f"{c['name']} ({c['podcast_last_30d']} episode{'s' if c['podcast_last_30d'] != 1 else ''})" for c in active_pods)
                      + " in the last 30 days.",
            "action": "Keep the DOMA podcast on a steady schedule; a stale feed looks inactive next to theirs."})
    comp_speeds = sorted(v for v in speed.values() if v is not None)
    if doma_speed is not None and comp_speeds:
        better = sum(1 for v in comp_speeds if doma_speed > v)
        takeaways.append({
            "kind": "strength" if better >= len(comp_speeds) / 2 else "risk", "title": "Home page speed on phones",
            "detail": f"DOMA scores {doma_speed} on Google PageSpeed (mobile), ahead of {better} of {len(comp_speeds)} "
                      f"competitors (best competitor: {comp_speeds[-1]}).",
            "action": "Keep it: every new embed or script on the home page costs speed."})

    order = {"opportunity": 0, "risk": 1, "rival": 2, "ctr": 3, "strength": 4, "content": 5}
    takeaways.sort(key=lambda t: order.get(t["kind"], 9) + (2 if t["title"].startswith("Home page speed") else 0))
    return {
        "available": True,
        "checked_at": checked_at,
        "gsc_window": [gsc_start, gsc_end],
        "takeaways": takeaways,
        "topics": topics,
        "queries": sorted(queries, key=lambda q: (q["topic"], -q["impressions"])),
        "rivals": rivals,
        "scorecard": scorecard,
        "doma_pagespeed": doma_speed,
        "tracked_searches": len(serp),
        "market_searches": len(queries),
        "other_searches": sorted(other),
    }


def recent_posts_summary(con: sqlite3.Connection) -> dict[str, Any]:
    """Latest blog posts (WordPress REST API) joined with their GA4 + Search
    Console performance - answers "how is the post I published last week
    actually doing" without needing it to already be a top-30/top-50 entry
    elsewhere. A post missing from one side comes back with null for that
    side's numbers (not 0), so the UI can show "-" instead of a fake zero."""
    ga4_rows = {
        row[0]: {"sessions": int(row[2] or 0), "page_views": int(row[3] or 0), "avg_engagement_seconds": float(row[5] or 0), "title": row[6]}
        for row in con.execute(
            "SELECT url, published_at, sessions, page_views, active_users, avg_engagement_seconds, title FROM content_posts_ga4"
        ).fetchall()
    }
    gsc_rows = {
        row[0]: {"clicks": int(row[2] or 0), "impressions": int(row[3] or 0), "position": float(row[5] or 0), "title": row[6]}
        for row in con.execute(
            "SELECT url, published_at, clicks, impressions, ctr, position, title FROM content_posts_gsc"
        ).fetchall()
    }
    published_dates = {
        row[0]: row[1]
        for row in con.execute("SELECT url, published_at FROM content_posts_gsc UNION SELECT url, published_at FROM content_posts_ga4").fetchall()
    }
    wp_titles = {row[0]: row[1] for row in con.execute("SELECT page_path, title FROM wp_posts WHERE title IS NOT NULL")}

    posts = []
    for url, published_at in published_dates.items():
        ga4 = ga4_rows.get(url)
        gsc = gsc_rows.get(url)
        title = (ga4 or {}).get("title") or (gsc or {}).get("title") or wp_titles.get(urlsplit(url).path)
        posts.append(
            {
                "url": url,
                "title": title,
                "published_at": published_at,
                "sessions": ga4["sessions"] if ga4 else None,
                "page_views": ga4["page_views"] if ga4 else None,
                "avg_engagement_seconds": ga4["avg_engagement_seconds"] if ga4 else None,
                "clicks": gsc["clicks"] if gsc else None,
                "impressions": gsc["impressions"] if gsc else None,
                "position": gsc["position"] if gsc else None,
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
            "page_title": clean_page_title(row[0], row[1]),
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
        "conversions_by_page": _ga4_conversions_by_page(con, start_date, end_date),
        "ai_assistant_daily": [
            {"report_date": r[0], "sessions": int(r[1] or 0)}
            for r in con.execute(
                "SELECT report_date, sessions FROM ga4_channel_daily WHERE channel_group = 'AI Assistant' "
                "AND report_date BETWEEN ? AND ? ORDER BY report_date",
                (start_date, end_date),
            )
        ],
        "email_sessions": int(con.execute(
            "SELECT COALESCE(SUM(sessions), 0) FROM ga4_channel_daily WHERE channel_group = 'Email' AND report_date BETWEEN ? AND ?",
            (start_date, end_date),
        ).fetchone()[0]),
        "undercount_overlap": start_date <= GA4_UNDERCOUNT_END and end_date >= GA4_UNDERCOUNT_START,
        "offers": _ga4_offers(con, start_date, end_date),
        "cta_clicks": _ga4_cta_clicks(con, start_date, end_date),
    }


# The cookie banner (2026-09-11) only let GA4 count visitors who clicked
# Accept until Consent Mode was fixed on 2026-10-05. Same window as
# GA4_UNDERCOUNT_START/END in static/dashboard.js; keep the two in sync.
GA4_UNDERCOUNT_START = "2026-09-11"
GA4_UNDERCOUNT_END = "2026-10-04"


def _ga4_conversions_by_page(con: sqlite3.Connection, start_date: str, end_date: str, limit: int = 15) -> list[dict[str, Any]]:
    """Key events (guide sign-ups, membership) by landing page for the range,
    so a page is judged by what it converts and not only by sessions."""
    rows = con.execute(
        "SELECT page_path, SUM(sessions), SUM(key_events) FROM ga4_page_conversions_daily "
        "WHERE report_date BETWEEN ? AND ? AND page_path NOT IN ('(not set)', '') "
        "GROUP BY page_path HAVING SUM(key_events) > 0 ORDER BY SUM(key_events) DESC, SUM(sessions) DESC LIMIT ?",
        (start_date, end_date, limit),
    ).fetchall()
    titles = _page_titles(con)
    return [
        {
            "page_path": path,
            "page_title": titles.get(path) or title_from_path(path),
            "sessions": int(sessions or 0),
            "key_events": round(float(key_events or 0), 1),
            "rate": round(float(key_events or 0) / sessions * 100, 1) if sessions else 0.0,
        }
        for path, sessions, key_events in rows
    ]


# Outbound link domains on /offers/ pages -> sponsor name. Unknown domains
# are shown as they come from GA4.
SPONSOR_DOMAINS = {
    "dentalmenu.com": "DentalMenu",
    "pbhs.com": "PBHS",
    "nadapayments.com": "NADA",
    "roya.com": "Roya",
    "velmeni.ai": "Velmeni",
    "vynedental.com": "Vyne Dental",
    "adit.com": "Adit",
    "archy.com": "Archy",
    "blackdiamondradio.com": "Black Diamond Radio",
    "get-clipboard.com": "Clipboard",
    "getweave.com": "Weave",
    "janie.ai": "Janie AI",
    "revenuewell.com": "RevenueWell",
    "techspedient.com": "Techspedient",
    "withcherry.com": "Cherry",
    "mangovoice.com": "Mango Voice",
    "getvwire.com": "VWire",
    "pearl.com": "Pearl",
    "hellopearl.com": "Pearl",
    "traynar.com": "Traynar",
}


def sponsor_for_domain(domain: str) -> str:
    domain = (domain or "").lower().removeprefix("www.")
    if domain in SPONSOR_DOMAINS:
        return SPONSOR_DOMAINS[domain]
    for known, name in SPONSOR_DOMAINS.items():
        if domain.endswith("." + known):  # e.g. app.getweave.com
            return name
    return domain or "(not set)"


def _ga4_offers(con: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, Any] | None:
    """Views/sessions of the /offers/ pages and outbound clicks per sponsor for
    the range. None until scripts/sync_ga4.py has filled the tables once.
    Sessions are summed per day, so a visit that crosses midnight counts twice."""
    synced = con.execute("SELECT 1 FROM sync_log WHERE source = 'ga4_offers_pages' AND status = 'ok' LIMIT 1").fetchone()
    if not synced:
        return None
    titles = _page_titles(con)
    pages = [
        {
            "page_path": path,
            "page_title": titles.get(path) or title_from_path(path),
            "page_views": int(views or 0),
            "sessions": int(sessions or 0),
        }
        for path, views, sessions in con.execute(
            "SELECT page_path, SUM(page_views), SUM(sessions) FROM ga4_offers_pages_daily "
            "WHERE report_date BETWEEN ? AND ? GROUP BY page_path ORDER BY SUM(page_views) DESC",
            (start_date, end_date),
        )
    ]
    by_sponsor: dict[str, dict[str, Any]] = {}
    for domain, page_path, clicks in con.execute(
        "SELECT link_domain, page_path, SUM(clicks) FROM ga4_offers_clicks_daily "
        "WHERE report_date BETWEEN ? AND ? GROUP BY link_domain, page_path",
        (start_date, end_date),
    ):
        name = sponsor_for_domain(domain)
        entry = by_sponsor.setdefault(name, {"sponsor": name, "domains": set(), "clicks": 0, "from_hub": 0, "from_sponsor_page": 0})
        entry["domains"].add(domain)
        entry["clicks"] += int(clicks or 0)
        if page_path == "/offers/":
            entry["from_hub"] += int(clicks or 0)
        else:
            entry["from_sponsor_page"] += int(clicks or 0)
    sponsor_clicks = sorted(
        ({**e, "domains": sorted(e["domains"]), "known": e["sponsor"] in SPONSOR_DOMAINS.values()} for e in by_sponsor.values()),
        key=lambda e: (-e["clicks"], e["sponsor"].lower()),
    )
    return {
        "pages": pages,
        "sponsor_clicks": sponsor_clicks,
        "total_views": sum(p["page_views"] for p in pages),
        "total_clicks": sum(e["clicks"] for e in sponsor_clicks),
    }


def _ga4_cta_clicks(con: sqlite3.Connection, start_date: str, end_date: str, limit: int = 50) -> dict[str, Any]:
    """Most clicked buttons (GA4 cta_click event) for the range. `available`
    is False until a sync has stored the event at least once."""
    synced = con.execute("SELECT 1 FROM sync_log WHERE source = 'ga4_cta_clicks' AND status = 'ok' LIMIT 1").fetchone()
    titles = _page_titles(con)
    rows = [
        {
            "page_path": path,
            "page_title": "Home" if path == "/" else (titles.get(path) or title_from_path(path)),
            "link_text": text,
            "link_url": url,
            "clicks": int(clicks or 0),
        }
        for path, text, url, clicks in con.execute(
            "SELECT page_path, link_text, link_url, SUM(clicks) FROM ga4_cta_clicks_daily "
            "WHERE report_date BETWEEN ? AND ? GROUP BY page_path, link_text, link_url "
            "ORDER BY SUM(clicks) DESC LIMIT ?",
            (start_date, end_date, limit),
        )
    ]
    return {"available": bool(synced), "tracking_start": "2026-10-15", "rows": rows}


def ghl_summary(con: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, Any]:
    import json

    # The 2026-07-20 ActiveCampaign CSV import is the old list moved into GHL,
    # not new leads, so it stays out of every lead count.
    total_row = con.execute(
        "SELECT COALESCE(SUM(lead_count),0), MAX(synced_at) FROM ghl_leads_daily "
        "WHERE report_date BETWEEN ? AND ? AND source <> 'Imported (ActiveCampaign CSV)'",
        (start_date, end_date),
    ).fetchone()
    total_leads, last_synced_at = int(total_row[0] or 0), total_row[1]

    daily_rows = con.execute(
        "SELECT report_date, SUM(lead_count) FROM ghl_leads_daily "
        "WHERE report_date BETWEEN ? AND ? AND source <> 'Imported (ActiveCampaign CSV)' GROUP BY report_date ORDER BY report_date",
        (start_date, end_date),
    ).fetchall()
    daily = [{"report_date": row[0], "lead_count": int(row[1] or 0)} for row in daily_rows]

    source_rows = con.execute(
        "SELECT source, SUM(lead_count) FROM ghl_leads_daily "
        "WHERE report_date BETWEEN ? AND ? AND source <> 'Imported (ActiveCampaign CSV)' GROUP BY source ORDER BY SUM(lead_count) DESC",
        (start_date, end_date),
    ).fetchall()
    by_source = [{"source": row[0], "lead_count": int(row[1] or 0), "group": lead_source_group(row[0])} for row in source_rows]
    group_totals: dict[str, int] = {}
    for row in by_source:
        group_totals[row["group"]] = group_totals.get(row["group"], 0) + row["lead_count"]
    by_group = [{"group": g, "lead_count": n} for g, n in sorted(group_totals.items(), key=lambda kv: -kv[1])]

    email_rows = con.execute(
        """
        SELECT campaign_id, campaign_name, source, sent_at, sent_time, scheduled_at,
               recipients, opens, clicks, delivered, delivered_rate,
               unsubscribes, unsubscribe_rate, hard_bounces, soft_bounces, bounce_rate,
               skipped, skipped_rate, total_clicks, ctor, top_links, notes
        FROM ghl_email_campaigns
        WHERE sent_at IS NULL OR sent_at BETWEEN ? AND ?
        ORDER BY sent_at DESC LIMIT 25
        """,
        (start_date, end_date),
    ).fetchall()
    email_campaigns = []
    for row in email_rows:
        (
            campaign_id, campaign_name, source, sent_at, sent_time, scheduled_at,
            recipients, opens, clicks, delivered, delivered_rate,
            unsubscribes, unsubscribe_rate, hard_bounces, soft_bounces, bounce_rate,
            skipped, skipped_rate, total_clicks, ctor, top_links, notes,
        ) = row
        recipients = int(recipients or 0)
        opens = int(opens or 0)
        clicks = int(clicks or 0)
        try:
            parsed_links = json.loads(top_links) if top_links else []
        except (TypeError, ValueError):
            parsed_links = []
        email_campaigns.append(
            {
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "source": source or "ghl",
                "sent_at": sent_at,
                "sent_time": sent_time,
                "scheduled_at": scheduled_at,
                "recipients": recipients,
                "opens": opens,
                "clicks": clicks,
                "delivered": int(delivered) if delivered is not None else None,
                "delivered_rate": round(delivered_rate, 2) if delivered_rate is not None else None,
                "open_rate": round((opens / recipients) * 100, 1) if recipients else None,
                "click_rate": round((clicks / recipients) * 100, 1) if recipients else None,
                "unsubscribes": int(unsubscribes) if unsubscribes is not None else None,
                "unsubscribe_rate": round(unsubscribe_rate, 2) if unsubscribe_rate is not None else None,
                "hard_bounces": int(hard_bounces) if hard_bounces is not None else None,
                "soft_bounces": int(soft_bounces) if soft_bounces is not None else None,
                "bounce_rate": round(bounce_rate, 2) if bounce_rate is not None else None,
                "skipped": int(skipped) if skipped is not None else None,
                "skipped_rate": round(skipped_rate, 2) if skipped_rate is not None else None,
                "total_clicks": int(total_clicks) if total_clicks is not None else None,
                "ctor": round(ctor, 2) if ctor is not None else None,
                "top_links": parsed_links,
                "notes": notes,
            }
        )

    return {
        "available": total_leads > 0,
        "total_leads": total_leads,
        "daily": daily,
        "by_source": by_source,
        "by_group": by_group,
        "orders": ghl_orders_summary(con, start_date, end_date),
        "email_available": len(email_campaigns) > 0,
        "email_campaigns": email_campaigns,
        "guide_funnel": guide_funnel(con, start_date, end_date, by_source),
        "last_synced_at": last_synced_at,
    }


def guide_funnel(con: sqlite3.Connection, start_date: str, end_date: str, by_source: list[dict[str, Any]]) -> dict[str, Any]:
    """Sessions that landed on a free-guide page (GA4 landingPage, daily) and
    leads from guide forms (GHL "Ebooks" group) for the same range. Leads can
    come from a session that started elsewhere, so this is an approximate
    funnel; it is withheld when leads exceed sessions (not one cohort)."""
    sessions = con.execute(
        "SELECT COALESCE(SUM(sessions), 0) FROM ga4_page_conversions_daily "
        "WHERE report_date BETWEEN ? AND ? AND page_path IN (SELECT page_path FROM free_guide_pages)",
        (start_date, end_date),
    ).fetchone()[0]
    leads = sum(row["lead_count"] for row in by_source if row["group"] == "Ebooks")
    out: dict[str, Any] = {"available": False, "sessions": int(sessions or 0), "leads": int(leads)}
    if not sessions:
        out["reason"] = "No GA4 landing sessions on guide pages in this range."
    elif leads > sessions:
        out["reason"] = "Guide leads exceed guide-page sessions in this range, so the two counts are not one cohort."
    else:
        out["available"] = True
        out["rate_percent"] = round(leads / sessions * 100, 2)
    return out


# Lead source labels from GoHighLevel are form/funnel names typed by hand
# ("02. Partner With Us!", "Ebook -Creating...", "payment_link"), so the
# Leads tab groups them by type. Checked in order; first match wins.
LEAD_SOURCE_GROUPS = [
    # free starter kit form, incl. the home page pop-up version
    ("Free Starter Kit", re.compile(r"starter kit", re.I)),
    # partner/affiliate forms (the affiliate "Success Kit" form is a partner lead)
    ("Partner / Affiliate", re.compile(r"partner|affiliate", re.I)),
    # every guide form ("Ebook - ...", "...-eBook Form"), AI Guides and the insurance benefits guide
    # \b keeps "Facebook Messenger" out ("Fac-ebook" matched the bare pattern)
    ("Ebooks", re.compile(r"\be-?book|ai guides|maximizing dental insurance", re.I)),
    # DMs that the GHL automations turn into contacts
    ("Social DM", re.compile(r"instagram|facebook messenger|messenger|\bdm\b", re.I)),
    # Wix community, community funnel/portal, "Join The Community"
    ("Community", re.compile(r"community", re.I)),
    # GHL payment links and membership checkouts
    ("Purchase", re.compile(r"payment_link|membership|order form|checkout", re.I)),
    ("Email / Phone", re.compile(r"inbound email|phone|sms|\bcall\b", re.I)),
]


def lead_source_group(source: str | None) -> str:
    """Group for a raw GHL lead source label; events, imports and blanks are Other."""
    for group, pattern in LEAD_SOURCE_GROUPS:
        if pattern.search(source or ""):
            return group
    return "Other"


def ghl_orders_summary(con: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, Any]:
    """GoHighLevel orders for the range (live mode only, see sync_ghl.py).
    Revenue counts completed orders; refunds come from transactions on the
    day of the refund charge. Subscriptions are the latest snapshot taken on
    or before the end of the range."""
    totals = con.execute(
        "SELECT COALESCE(SUM(orders), 0), "
        "COALESCE(SUM(CASE WHEN status = 'completed' THEN orders END), 0), "
        "COALESCE(SUM(CASE WHEN status = 'pending' THEN orders END), 0), "
        "COALESCE(SUM(CASE WHEN status = 'completed' THEN amount END), 0) "
        "FROM ghl_orders_daily WHERE report_date BETWEEN ? AND ?",
        (start_date, end_date),
    ).fetchone()
    refunded = con.execute(
        "SELECT COALESCE(SUM(refunded), 0) FROM ghl_refunds_daily WHERE report_date BETWEEN ? AND ?",
        (start_date, end_date),
    ).fetchone()[0]
    snapshot = con.execute(
        "SELECT MAX(snapshot_date) FROM ghl_subscriptions_snapshot WHERE snapshot_date <= ?", (end_date,)
    ).fetchone()[0]
    active = con.execute(
        "SELECT COALESCE(SUM(count), 0), COALESCE(SUM(monthly_amount), 0) FROM ghl_subscriptions_snapshot "
        "WHERE snapshot_date = ? AND status IN ('active', 'trialing')",
        (snapshot,),
    ).fetchone()
    return {
        "available": bool(con.execute("SELECT 1 FROM ghl_orders_daily LIMIT 1").fetchone()),
        "total_orders": int(totals[0]),
        "completed": int(totals[1]),
        "pending": int(totals[2]),
        "revenue": round(float(totals[3]), 2),
        "refunded": round(float(refunded or 0), 2),
        "by_product": [
            {"product": r[0], "orders": int(r[1]), "revenue": round(float(r[2] or 0), 2)}
            for r in con.execute(
                "SELECT product, SUM(orders), SUM(CASE WHEN status = 'completed' THEN amount ELSE 0 END) FROM ghl_orders_daily "
                "WHERE report_date BETWEEN ? AND ? GROUP BY product ORDER BY SUM(orders) DESC",
                (start_date, end_date),
            )
        ],
        "daily": [
            {"report_date": r[0], "orders": int(r[1]), "revenue": round(float(r[2] or 0), 2)}
            for r in con.execute(
                "SELECT report_date, SUM(orders), SUM(CASE WHEN status = 'completed' THEN amount ELSE 0 END) FROM ghl_orders_daily "
                "WHERE report_date BETWEEN ? AND ? GROUP BY report_date ORDER BY report_date",
                (start_date, end_date),
            )
        ],
        "active_subscriptions": int(active[0]),
        "mrr": round(float(active[1]), 2),
        "subscriptions_snapshot_date": snapshot,
        "by_lead_source": [
            {"source_group": r[0], "orders": int(r[1])}
            for r in con.execute(
                "SELECT source_group, SUM(orders) FROM ghl_orders_daily WHERE report_date BETWEEN ? AND ? "
                "GROUP BY source_group ORDER BY SUM(orders) DESC",
                (start_date, end_date),
            )
        ],
    }


def social_summary(con: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, Any]:
    followers: dict[str, Any] = {}
    for platform in ("facebook", "instagram"):
        row = con.execute(
            # newest value up to the end of the range - without the date bound
            # the previous period got today's number too and the delta was always 0%
            "SELECT follower_count, synced_at FROM social_followers_daily "
            "WHERE platform = ? AND report_date <= ? ORDER BY report_date DESC LIMIT 1",
            (platform, end_date),
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

    posts = _fetch_recent_social_posts(con)
    best_times = _instagram_best_times(posts)

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
        "best_times": best_times,
        "last_synced_at": last_synced_row[0] if last_synced_row else None,
    }


DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TIME_PERIODS = [
    ("Night (12am-6am)", 0, 6),
    ("Morning (6am-12pm)", 6, 12),
    ("Afternoon (12pm-6pm)", 12, 18),
    ("Evening (6pm-12am)", 18, 24),
]


def _instagram_best_times(posts: list[dict[str, Any]]) -> dict[str, Any]:
    """When Instagram posts get the most engagement, by day-of-week and
    rough time-of-day - a real (if small-sample) signal from this
    account's own post history, not a generic "best time to post on
    Instagram" industry stat. All times are UTC (Instagram's API doesn't
    return a post-level timezone) - note that in the UI rather than
    silently implying local time."""
    ig_posts = [p for p in posts if p["platform"] == "instagram" and p.get("published_at")]
    if len(ig_posts) < 5:
        return {"available": False, "sample_size": len(ig_posts), "by_day": [], "by_period": []}

    by_day: dict[str, list[int]] = {d: [] for d in DAY_NAMES}
    by_period: dict[str, list[int]] = {p[0]: [] for p in TIME_PERIODS}

    for post in ig_posts:
        try:
            dt = datetime.strptime(post["published_at"], "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            continue
        engagement = post["engagement_total"] or (post["likes"] + post["comments"] + post["shares"])
        by_day[DAY_NAMES[dt.weekday()]].append(engagement)
        for label, start_hour, end_hour in TIME_PERIODS:
            if start_hour <= dt.hour < end_hour:
                by_period[label].append(engagement)
                break

    day_stats = [
        {"day": day, "avg_engagement": round(sum(vals) / len(vals), 1), "post_count": len(vals)}
        for day, vals in by_day.items()
        if vals
    ]
    day_stats.sort(key=lambda d: d["avg_engagement"], reverse=True)

    period_stats = [
        {"period": label, "avg_engagement": round(sum(vals) / len(vals), 1), "post_count": len(vals)}
        for label, vals in by_period.items()
        if vals
    ]
    period_stats.sort(key=lambda d: d["avg_engagement"], reverse=True)

    return {
        "available": True,
        "sample_size": len(ig_posts),
        "by_day": day_stats,
        "by_period": period_stats,
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
            "content_suggestions": content_suggestions_summary(con),
            "ahrefs": {
                "competitors": ahrefs_competitors_summary(con),
                "issues": ahrefs_issues_summary(con),
            },
            "competitor_content": competitor_content_summary(con),
            "serp_competitors": serp_competitors_summary(con),
            "serp_where_doma_stands": serp_where_doma_stands(con, start_date, end_date),
            "competitor_insights": competitor_insights(con, start_date, end_date),
            "ad_spy": ad_spy_summary(con),
            "team_meetings": team_meetings_summary(con),
            "competitor_tech": competitor_tech_summary(con),
            "competitor_wayback": competitor_wayback_summary(con),
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


def _align_search_console_window(current: dict[str, Any], previous: dict[str, Any], start_date: str, end_date: str) -> None:
    """Search Console data lags ~2-3 days, so a window that ends today always
    holds fewer days than the full window it's compared with. That built a
    standing negative bias (about -15%, and -38% on 2026-10-15 while the sync
    was down). Same-length windows ending on the newest day with data fix it."""
    with db() as con:
        last_day = con.execute("SELECT MAX(report_date) FROM search_console_daily").fetchone()[0]
        if not last_day or last_day >= end_date:
            return
        span = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1
        gsc_end = date.fromisoformat(last_day)
        gsc_start = gsc_end - timedelta(days=span - 1)
        prev_start, prev_end = previous_period_range(gsc_start.isoformat(), gsc_end.isoformat())
        current["search_console"] = search_console_summary(con, gsc_start.isoformat(), gsc_end.isoformat())
        previous["search_console"] = search_console_summary(con, prev_start, prev_end)
    current["search_console"]["window_start"], current["search_console"]["window_end"] = gsc_start.isoformat(), gsc_end.isoformat()
    current["search_console"]["previous_window_start"], current["search_console"]["previous_window_end"] = prev_start, prev_end


def data_start_dates() -> dict[str, str | None]:
    """First day each source has data. The front end shows a % change only when the
    previous window starts on or after it: Facebook reach starts 2026-09-16, so 30-day
    reach was compared with one day (+13,819%)."""
    queries = {
        "gsc": "SELECT MIN(report_date) FROM search_console_daily",
        "ga4": "SELECT MIN(report_date) FROM ga4_traffic_daily",
        "leads": "SELECT MIN(report_date) FROM ghl_leads_daily",
        "orders": "SELECT MIN(report_date) FROM ghl_orders_daily",
        "fb_reach": "SELECT MIN(report_date) FROM social_metrics_daily WHERE platform='facebook' AND metric='page_total_media_view_unique'",
        "ig_reach": "SELECT MIN(report_date) FROM social_metrics_daily WHERE platform='instagram' AND metric='ig_reach'",
    }
    out: dict[str, str | None] = {}
    with db() as con:
        for key, sql in queries.items():
            try:
                out[key] = con.execute(sql).fetchone()[0]
            except sqlite3.Error:
                out[key] = None
    return out


def dashboard_with_comparison(start_date: str, end_date: str) -> dict[str, Any]:
    previous_start, previous_end = previous_period_range(start_date, end_date)
    current = full_dashboard(start_date, end_date)
    previous = full_dashboard(previous_start, previous_end)
    _align_search_console_window(current, previous, start_date, end_date)
    return {
        **current,
        "previous_start_date": previous_start,
        "previous_end_date": previous_end,
        "previous": previous,
        "data_start": data_start_dates(),
    }


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.post("/api/content-calendar/upload")
async def upload_content_calendar_file(file: UploadFile = File(...)):
    """Uploads an ebook PDF/image to the WordPress media library and returns
    its public URL, for the Content Calendar's upload field to store on a
    calendar item. Local dashboard only - the published static site has no
    backend to call this. Protected by the same admin Basic auth as every
    other /api/* write (see protect_writes middleware)."""
    if not (WP_URL and WP_USERNAME and WP_APP_PASSWORD):
        raise HTTPException(503, "WP_URL / WP_USERNAME / WP_APP_PASSWORD not configured in .env")

    content = await file.read()
    if len(content) > UPLOAD_MAX_BYTES:
        raise HTTPException(400, "File too large (max 20MB)")
    filename = file.filename or "upload"
    content_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    # Michelle's cover images come out of her phone/Canva at several MB each -
    # re-encode to WebP before it ever reaches WordPress, same size reduction
    # PageSpeed's own fixes relied on all session. PDFs pass through untouched.
    if content_type.startswith("image/") and content_type != "image/webp":
        try:
            with Image.open(io.BytesIO(content)) as img:
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGBA")
                else:
                    img = img.convert("RGB")
                buffer = io.BytesIO()
                img.save(buffer, format="WEBP", quality=82, method=6)
                content = buffer.getvalue()
            filename = str(Path(filename).with_suffix(".webp"))
            content_type = "image/webp"
        except Exception as error:  # noqa: BLE001 - fall back to the original file rather than failing the upload
            print(f"WebP conversion failed for {filename}, uploading original: {error}")

    response = requests.post(
        f"{WP_URL}/wp-json/wp/v2/media",
        auth=(WP_USERNAME, WP_APP_PASSWORD),
        headers={"User-Agent": WP_BROWSER_UA},
        files={"file": (filename, content, content_type)},
        timeout=60,
    )
    if response.status_code not in (200, 201):
        raise HTTPException(502, f"WordPress upload failed ({response.status_code}): {response.text[:300]}")
    payload = response.json()
    return {"url": payload["source_url"], "id": payload["id"], "filename": filename}


@app.post("/api/content-calendar/create-pages")
async def create_content_calendar_pages(request: Request):
    """Builds the capture + thank-you WordPress draft pages for an ebook
    uploaded through the Content Calendar, reusing the same pipeline
    scripts/sync_ebook_pipeline.py uses for the Drive-based flow - the PDF is
    just downloaded back from its WordPress URL instead of Google Drive.
    Writes ebook_packages/{slug}/package.json same as that pipeline does, so
    the existing Mon/Wed/Fri scheduled run (AGENDAR_AUTOMACAO_EBOOKS.bat)
    picks it up automatically and attaches the real GHL form the moment
    Lucas duplicates+renames it - no separate polling needed here.
    """
    env = load_env_file(BASE_DIR / ".env")
    if not (WP_URL and WP_USERNAME and WP_APP_PASSWORD):
        raise HTTPException(503, "WP_URL / WP_USERNAME / WP_APP_PASSWORD not configured in .env")

    body = await request.json()
    title = (body.get("title") or "").strip()
    pdf_url = (body.get("pdf_url") or "").strip()
    cover_url = (body.get("image_url") or "").strip()
    cover_media_id = body.get("image_media_id")
    if not (title and pdf_url and cover_url and cover_media_id):
        raise HTTPException(400, "title, pdf_url, image_url, and image_media_id are all required")

    pdf_response = requests.get(pdf_url, headers={"User-Agent": WP_BROWSER_UA}, timeout=60)
    if pdf_response.status_code != 200:
        raise HTTPException(502, f"Could not download the PDF back from {pdf_url}")

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "ebook.pdf"
        pdf_path.write_bytes(pdf_response.content)
        try:
            extracted = pdf_extract.extract(pdf_path)
        except pdf_extract.PdfExtractError as error:
            raise HTTPException(422, f"Could not read the PDF: {error}") from None

    if not extracted.get("title"):
        extracted["title"] = title
    form_name = f"Ebook - {extracted['title'].strip()}"

    # Order matters: thank-you page first (its real URL, WordPress may add -2 to
    # a slug), then the GHL form redirecting to it, then the capture page with
    # that form embedded. The form's leads are tagged and emailed by
    # scripts/ebook_lead_delivery.py, so no per-ebook GHL workflow is needed.
    ghl_domain = env.get("GHL_WIDGET_DOMAIN", "")
    package = copy_generator.build_package(extracted, cover_url, pdf_url, ghl_domain, None)
    try:
        wp = WpClient(env)
        # Published straight away (not draft) - Content-Calendar-triggered
        # pages are reviewed by the person uploading the PDF before they
        # upload it, so there's no separate draft-review step here unlike
        # the Drive-based pipeline's create_draft_page default.
        ty_page = wp.create_draft_page(
            f"{package['title']} - Thank You", f"{package['slug']}-thank-you", package["thank_you_html"],
            excerpt=package["excerpt"], featured_media=cover_media_id,
            noindex=True,
            status="publish",
        )
    except WpError as error:
        raise HTTPException(502, f"WordPress page creation failed: {error}") from None

    try:
        ghl = GhlClient(env)
        ty_url = ty_page.get("preview_url") or f"{WP_URL.rstrip('/')}/{package['slug']}-thank-you/"
        matched_form = ghl.ensure_ebook_form(form_name, ty_url)
        matched_workflow = ghl.find_workflow_by_title(form_name)
    except GhlError as error:
        # A missing/broken GHL connection shouldn't block the capture page; it is
        # created with the placeholder and attach.py embeds the form later.
        print(f"GHL form step failed, continuing without a form: {error}")
        matched_form, matched_workflow = None, None

    package = copy_generator.build_package(extracted, cover_url, pdf_url, ghl_domain, matched_form)
    try:
        capture_page = wp.create_draft_page(
            f"{package['title']} - Free Guide", package["slug"], package["capture_html"],
            excerpt=package["excerpt"], featured_media=cover_media_id,
            meta_description=package["excerpt"],
            status="publish",
        )
    except WpError as error:
        raise HTTPException(502, f"WordPress page creation failed: {error}") from None

    write_package(
        env, package, extracted,
        cover={"url": cover_url, "id": cover_media_id},
        pdf_upload={"url": pdf_url},
        capture_page=capture_page, ty_page=ty_page,
        matched_form=matched_form, matched_workflow=matched_workflow,
    )

    return {
        "slug": package["slug"],
        "title": package["title"],
        "capture_url": capture_page["preview_url"],
        "thank_you_url": ty_page["preview_url"],
        "capture_edit_url": capture_page["edit_url"],
        "thank_you_edit_url": ty_page["edit_url"],
        "form_attached": bool(matched_form),
    }


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
