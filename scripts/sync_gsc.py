"""Pull organic search performance from Google Search Console into local SQLite.

Only aggregated day-level and query-level counts are stored - Search Console
data is already aggregated by nature, no user-level or PII data exists in
this API at all.

Requires GSC_SITE_URL and GA4_SERVICE_ACCOUNT_FILE in .env - the same
service account used for GA4 must also be added as a user on the Search
Console property (Settings > Users and permissions > Add user, "Restricted"
permission is enough for API read access), and the Search Console API must
be enabled on the Google Cloud project.

GSC_SITE_URL must match exactly how the property is registered in Search
Console - either "https://example.com/" (URL-prefix property) or
"sc-domain:example.com" (Domain property). Check Search Console > Settings
if unsure; the wrong form returns an empty result set, not an error.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402
from scripts import sitemap_utils  # noqa: E402

LOOKBACK_DAYS = 180
TOP_QUERY_LIMIT = 50

# Index-coverage checks (redirects, "not indexed", etc) use the separate URL
# Inspection API, which is per-URL and quota-limited - so instead of every
# page on the site, this checks the site's static pages (all of them, there
# are few) plus the most recently published posts, sourced from the Yoast
# SEO XML sitemap (WordPress).
MAX_PAGES_TO_INSPECT = 60
MAX_POSTS_TO_INSPECT = 25
MAX_RECENT_POSTS = 15
CONTENT_GAP_MIN_IMPRESSIONS = 15
CONTENT_GAP_MIN_POSITION = 15


class GscSyncError(RuntimeError):
    pass


def _client(env: dict[str, str]):
    try:
        from googleapiclient.discovery import build
    except ImportError as error:
        raise GscSyncError(
            "google-api-python-client is not installed. Run: pip install -r requirements.txt"
        ) from error

    site_url = env.get("GSC_SITE_URL")
    if not site_url:
        raise GscSyncError("Missing GSC_SITE_URL in .env")

    # Prefer OAuth (a regular Google account added as a user on the property)
    # over the service account when GSC_OAUTH_REFRESH_TOKEN is set. This is
    # required for Domain properties (sc-domain:...) specifically - Search
    # Console's "Add user" rejects service-account emails there with a real,
    # documented Google bug ("email not found"), even though the same
    # account works fine on URL-prefix properties and on GA4. See
    # scripts/gsc_oauth_setup.py for the one-time setup that produces these.
    refresh_token = env.get("GSC_OAUTH_REFRESH_TOKEN")
    if refresh_token:
        from google.oauth2.credentials import Credentials

        client_id = env.get("GSC_OAUTH_CLIENT_ID")
        client_secret = env.get("GSC_OAUTH_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise GscSyncError("GSC_OAUTH_REFRESH_TOKEN is set but GSC_OAUTH_CLIENT_ID/SECRET is missing in .env")
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
        )
    else:
        from google.oauth2 import service_account

        key_file = env.get("GA4_SERVICE_ACCOUNT_FILE")
        if not key_file:
            raise GscSyncError("Missing GA4_SERVICE_ACCOUNT_FILE in .env (or set GSC_OAUTH_REFRESH_TOKEN instead)")
        key_path = ROOT / key_file
        if not key_path.exists():
            raise GscSyncError(f"Service account file not found: {key_path}")
        creds = service_account.Credentials.from_service_account_file(
            str(key_path), scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
        )

    service = build("searchconsole", "v1", credentials=creds)
    return service, site_url


def _date_range() -> tuple[str, str]:
    end = date.today() - timedelta(days=2)  # GSC data lags ~2 days
    start = end - timedelta(days=LOOKBACK_DAYS)
    return start.isoformat(), end.isoformat()


def fetch_daily(service, site_url: str) -> list[tuple[str, int, int, float, float]]:
    start_date, end_date = _date_range()
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["date"],
        "rowLimit": 25000,
    }
    response = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows: list[tuple[str, int, int, float, float]] = []
    for row in response.get("rows", []):
        report_date = row["keys"][0]
        clicks = int(row.get("clicks", 0))
        impressions = int(row.get("impressions", 0))
        ctr = round(float(row.get("ctr", 0.0)) * 100, 2)
        position = round(float(row.get("position", 0.0)), 1)
        rows.append((report_date, clicks, impressions, ctr, position))
    return rows


def fetch_top_queries(service, site_url: str) -> list[tuple[str, int, int, float, float]]:
    start_date, end_date = _date_range()
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["query"],
        "rowLimit": TOP_QUERY_LIMIT,
    }
    response = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows: list[tuple[str, int, int, float, float]] = []
    for row in response.get("rows", []):
        query = row["keys"][0]
        clicks = int(row.get("clicks", 0))
        impressions = int(row.get("impressions", 0))
        ctr = round(float(row.get("ctr", 0.0)) * 100, 2)
        position = round(float(row.get("position", 0.0)), 1)
        rows.append((query, clicks, impressions, ctr, position))
    return rows


def fetch_top_countries(service, site_url: str) -> list[tuple[str, int, int, float, float]]:
    start_date, end_date = _date_range()
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["country"],
        "rowLimit": 25,
    }
    response = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows: list[tuple[str, int, int, float, float]] = []
    for row in response.get("rows", []):
        country = row["keys"][0]
        clicks = int(row.get("clicks", 0))
        impressions = int(row.get("impressions", 0))
        ctr = round(float(row.get("ctr", 0.0)) * 100, 2)
        position = round(float(row.get("position", 0.0)), 1)
        rows.append((country, clicks, impressions, ctr, position))
    return rows


def fetch_device_breakdown(service, site_url: str) -> list[tuple[str, int, int, float, float]]:
    start_date, end_date = _date_range()
    body = {"startDate": start_date, "endDate": end_date, "dimensions": ["device"], "rowLimit": 10}
    response = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows: list[tuple[str, int, int, float, float]] = []
    for row in response.get("rows", []):
        device = row["keys"][0]
        clicks = int(row.get("clicks", 0))
        impressions = int(row.get("impressions", 0))
        ctr = round(float(row.get("ctr", 0.0)) * 100, 2)
        position = round(float(row.get("position", 0.0)), 1)
        rows.append((device, clicks, impressions, ctr, position))
    return rows


def store_device_breakdown(rows: list[tuple[str, int, int, float, float]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM gsc_devices")
        con.executemany(
            "INSERT INTO gsc_devices (device, clicks, impressions, ctr, position, synced_at) "
            "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            rows,
        )


def store_top_countries(rows: list[tuple[str, int, int, float, float]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM gsc_countries")
        con.executemany(
            "INSERT INTO gsc_countries (country, clicks, impressions, ctr, position, synced_at) "
            "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            rows,
        )


def fetch_urls_to_inspect(real_site_url: str) -> list[str]:
    """Discover URLs from the WordPress/Yoast XML sitemap: every static page,
    plus the most recently published posts (most likely to have fresh
    indexing problems - a brand-new post not indexed yet, a redirect from a
    slug change, etc). Takes the real https:// site URL, not GSC's siteUrl
    (which is `sc-domain:...` for Domain properties and not a fetchable URL)."""
    urls = list(sitemap_utils.fetch_pages(real_site_url, limit=MAX_PAGES_TO_INSPECT))
    urls += [loc for loc, _ in sitemap_utils.fetch_recent_posts(real_site_url, limit=MAX_POSTS_TO_INSPECT)]

    seen = set()
    deduped = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def fetch_content_gaps(service, site_url: str) -> list[tuple[str, str, float, int, int]]:
    """Queries that get real search impressions but where DOMA's best-ranking
    page still sits outside the top 15 - i.e. Google knows the site is
    somewhat relevant but isn't confident enough to rank it well. That gap
    is a content opportunity: either the topic has no dedicated page, or the
    existing page doesn't target the query clearly enough."""
    start_date, end_date = _date_range()
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["query", "page"],
        "rowLimit": 1000,
    }
    response = service.searchanalytics().query(siteUrl=site_url, body=body).execute()

    best_by_query: dict[str, tuple[str, float, int, int]] = {}
    for row in response.get("rows", []):
        query, page = row["keys"]
        position = float(row.get("position", 0.0))
        impressions = int(row.get("impressions", 0))
        clicks = int(row.get("clicks", 0))
        existing = best_by_query.get(query)
        if existing is None or position < existing[1]:
            best_by_query[query] = (page, position, impressions, clicks)
        else:
            best_by_query[query] = (existing[0], existing[1], existing[2] + impressions, existing[3] + clicks)

    gaps = [
        (query, page, round(position, 1), impressions, clicks)
        for query, (page, position, impressions, clicks) in best_by_query.items()
        if position > CONTENT_GAP_MIN_POSITION and impressions >= CONTENT_GAP_MIN_IMPRESSIONS
    ]
    gaps.sort(key=lambda row: row[3], reverse=True)
    return gaps[:30]


def fetch_recent_post_performance(service, site_url: str, real_site_url: str) -> list[tuple[str, str, int, int, float, float]]:
    """Search performance for the most recently published posts specifically
    (not just whatever happens to be in the global top-clicks list) - a
    3-week-old post can have real impressions/clicks that never show up in
    fetch_top_queries because it's not a top-50 query yet. `site_url` is
    GSC's siteUrl (used for the search query); `real_site_url` is the actual
    https:// site (used to fetch the sitemap) - not the same value when the
    GSC property is a Domain property (`sc-domain:...`)."""
    recent_posts = sitemap_utils.fetch_recent_posts(real_site_url, limit=MAX_RECENT_POSTS)
    if not recent_posts:
        return []

    start_date, end_date = _date_range()
    body = {"startDate": start_date, "endDate": end_date, "dimensions": ["page"], "rowLimit": 5000}
    response = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    by_page = {row["keys"][0]: row for row in response.get("rows", [])}

    rows: list[tuple[str, str, int, int, float, float]] = []
    for url, lastmod in recent_posts:
        match = by_page.get(url)
        clicks = int(match.get("clicks", 0)) if match else 0
        impressions = int(match.get("impressions", 0)) if match else 0
        ctr = round(float(match.get("ctr", 0.0)) * 100, 2) if match else 0.0
        position = round(float(match.get("position", 0.0)), 1) if match else 0.0
        rows.append((url, lastmod, clicks, impressions, ctr, position))
    return rows


def fetch_url_inspections(service, site_url: str, urls: list[str]) -> list[dict[str, str]]:
    results = []
    for url in urls:
        try:
            response = (
                service.urlInspection()
                .index()
                .inspect(body={"inspectionUrl": url, "siteUrl": site_url})
                .execute()
            )
        except Exception as error:  # noqa: BLE001 - one bad URL should not abort the whole batch
            results.append({"url": url, "verdict": "ERROR", "coverage_state": str(error)[:200], "indexing_state": "", "robots_txt_state": "", "page_fetch_state": "", "last_crawl_time": ""})
            continue

        status = response.get("inspectionResult", {}).get("indexStatusResult", {})
        results.append(
            {
                "url": url,
                "verdict": status.get("verdict", "UNKNOWN"),
                "coverage_state": status.get("coverageState", ""),
                "indexing_state": status.get("indexingState", ""),
                "robots_txt_state": status.get("robotsTxtState", ""),
                "page_fetch_state": status.get("pageFetchState", ""),
                "last_crawl_time": status.get("lastCrawlTime", ""),
            }
        )
    return results


def store_daily(rows: list[tuple[str, int, int, float, float]]) -> None:
    with dashboard_app.db() as con:
        con.executemany(
            """
            INSERT INTO search_console_daily
                (report_date, clicks, impressions, ctr, position, synced_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(report_date) DO UPDATE SET
                clicks = excluded.clicks,
                impressions = excluded.impressions,
                ctr = excluded.ctr,
                position = excluded.position,
                synced_at = CURRENT_TIMESTAMP
            """,
            rows,
        )


def store_top_queries(rows: list[tuple[str, int, int, float, float]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM search_console_queries")
        con.executemany(
            """
            INSERT INTO search_console_queries
                (query, clicks, impressions, ctr, position, synced_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(query) DO UPDATE SET
                clicks = excluded.clicks,
                impressions = excluded.impressions,
                ctr = excluded.ctr,
                position = excluded.position,
                synced_at = CURRENT_TIMESTAMP
            """,
            rows,
        )


def store_content_gaps(rows: list[tuple[str, str, float, int, int]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM gsc_content_gaps")
        con.executemany(
            "INSERT INTO gsc_content_gaps (query, page, position, impressions, clicks, synced_at) "
            "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            rows,
        )


def store_recent_post_performance(rows: list[tuple[str, str, int, int, float, float]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM content_posts_gsc")
        con.executemany(
            "INSERT INTO content_posts_gsc (url, published_at, clicks, impressions, ctr, position, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            rows,
        )


def store_url_inspections(rows: list[dict[str, str]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM gsc_url_inspections")
        con.executemany(
            """
            INSERT INTO gsc_url_inspections
                (url, verdict, coverage_state, indexing_state, robots_txt_state, page_fetch_state, last_crawl_time, checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [
                (r["url"], r["verdict"], r["coverage_state"], r["indexing_state"], r["robots_txt_state"], r["page_fetch_state"], r["last_crawl_time"])
                for r in rows
            ],
        )


def main() -> int:
    env = load_env_file()
    dashboard_app.init_db()

    try:
        service, site_url = _client(env)
        # A Domain property's siteUrl (`sc-domain:...`) isn't a fetchable
        # URL, so sitemap discovery always needs the real https:// site -
        # WP_URL when set, otherwise site_url itself (URL-prefix property).
        real_site_url = env.get("WP_URL") or site_url
        daily = fetch_daily(service, site_url)
        top_queries = fetch_top_queries(service, site_url)
        countries = fetch_top_countries(service, site_url)
        devices = fetch_device_breakdown(service, site_url)
    except GscSyncError as error:
        log_sync(dashboard_app, "gsc", "error", str(error))
        raise

    store_daily(daily)
    store_top_queries(top_queries)
    store_top_countries(countries)
    store_device_breakdown(devices)
    log_sync(dashboard_app, "gsc", "ok", f"{len(daily)} daily rows, {len(top_queries)} top queries, {len(countries)} countries, {len(devices)} devices")
    print(f"Search Console sync complete: {len(daily)} daily rows, {len(top_queries)} top queries, {len(countries)} countries, {len(devices)} devices.")

    try:
        urls = fetch_urls_to_inspect(real_site_url)
        inspections = fetch_url_inspections(service, site_url, urls)
        store_url_inspections(inspections)
        log_sync(dashboard_app, "gsc_indexing", "ok", f"{len(inspections)} URLs inspected")
        print(f"Index coverage check complete: {len(inspections)} URLs inspected.")
    except Exception as error:  # noqa: BLE001 - indexing check is best-effort, must not break the core GSC sync
        log_sync(dashboard_app, "gsc_indexing", "skipped", str(error))
        print(f"WARNING: index coverage check skipped ({error})", file=sys.stderr)

    try:
        gaps = fetch_content_gaps(service, site_url)
        store_content_gaps(gaps)
        log_sync(dashboard_app, "gsc_content_gaps", "ok", f"{len(gaps)} gaps found")
        print(f"Content gap analysis complete: {len(gaps)} opportunities found.")
    except Exception as error:  # noqa: BLE001 - best-effort, must not break the core GSC sync
        log_sync(dashboard_app, "gsc_content_gaps", "skipped", str(error))
        print(f"WARNING: content gap analysis skipped ({error})", file=sys.stderr)

    try:
        recent_posts = fetch_recent_post_performance(service, site_url, real_site_url)
        store_recent_post_performance(recent_posts)
        log_sync(dashboard_app, "gsc_recent_posts", "ok", f"{len(recent_posts)} posts")
        print(f"Recent-post search performance complete: {len(recent_posts)} posts.")
    except Exception as error:  # noqa: BLE001 - best-effort, must not break the core GSC sync
        log_sync(dashboard_app, "gsc_recent_posts", "skipped", str(error))
        print(f"WARNING: recent-post search performance skipped ({error})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
