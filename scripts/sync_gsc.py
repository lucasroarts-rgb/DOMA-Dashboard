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
        # The OAuth app's consent screen is in "Testing", so Google revokes
        # this token every ~7 days (it died in September, on 2026-10-05 and
        # 2026-10-12). Instead of the whole GSC tab freezing until someone
        # re-runs gsc_oauth_setup.py, fall back to the service account on the
        # URL-prefix property (https://dentalofficemanagers.com/), which it
        # can read. Numbers are close to the Domain property: www, http and
        # app.* all 301 to that prefix. The fallback is logged as a gsc_auth
        # error so the SEO digest keeps flagging the expired token.
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request

        try:
            creds.refresh(Request())
        except RefreshError as error:
            global FALLBACK_NOTE
            fallback_url = env.get("GSC_FALLBACK_SITE_URL") or _url_prefix_for(site_url)
            FALLBACK_NOTE = (
                f"OAuth token expired ({error.args[0] if error.args else error}); using the service account "
                f"on {fallback_url}. Re-run scripts/gsc_oauth_setup.py, or set the OAuth consent screen to "
                f"'In production' in Google Cloud so the token stops expiring."
            )
            creds, site_url = _service_account_creds(env), fallback_url
    else:
        creds = _service_account_creds(env)

    service = build("searchconsole", "v1", credentials=creds)
    return service, site_url


FALLBACK_NOTE = ""


def _url_prefix_for(site_url: str) -> str:
    if site_url.startswith("sc-domain:"):
        return f"https://{site_url.split(':', 1)[1]}/"
    return site_url


def _service_account_creds(env: dict[str, str]):
    from google.oauth2 import service_account

    key_file = env.get("GA4_SERVICE_ACCOUNT_FILE")
    if not key_file:
        raise GscSyncError("Missing GA4_SERVICE_ACCOUNT_FILE in .env (or set GSC_OAUTH_REFRESH_TOKEN instead)")
    key_path = ROOT / key_file
    if not key_path.exists():
        raise GscSyncError(f"Service account file not found: {key_path}")
    return service_account.Credentials.from_service_account_file(
        str(key_path), scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
    )


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


API_ROW_LIMIT = 25000  # max rows per Search Analytics request
MAX_API_PAGES = 40  # safety cap: 1M rows per breakdown
PAGE_QUERY_DAYS = 90

# dimension -> per-day table filled by store_daily_breakdown
DAILY_BREAKDOWNS = {
    "query": ("search_console_query_daily", "query"),
    "country": ("gsc_country_daily", "country"),
    "device": ("gsc_device_daily", "device"),
    "page": ("gsc_page_daily", "page"),
}


def _query_all(service, site_url: str, body: dict) -> list[dict]:
    """Every row of a Search Analytics query, paging with startRow until a
    page comes back short (the API caps one response at 25,000 rows)."""
    rows: list[dict] = []
    for page in range(MAX_API_PAGES):
        response = service.searchanalytics().query(
            siteUrl=site_url, body={**body, "rowLimit": API_ROW_LIMIT, "startRow": page * API_ROW_LIMIT}
        ).execute()
        batch = response.get("rows", [])
        rows += batch
        if len(batch) < API_ROW_LIMIT:
            break
    return rows


def fetch_daily_breakdown(service, site_url: str, dimension: str) -> list[tuple[str, str, int, int, float]]:
    """[date, <dimension>] rows for the whole lookback, so the dashboard can
    rank queries/countries/devices/pages for whatever range is picked."""
    start_date, end_date = _date_range()
    rows = _query_all(service, site_url, {"startDate": start_date, "endDate": end_date, "dimensions": ["date", dimension]})
    return [
        (row["keys"][0], row["keys"][1], int(row.get("clicks", 0)), int(row.get("impressions", 0)), round(float(row.get("position", 0.0)), 2))
        for row in rows
    ]


def store_daily_breakdown(dimension: str, rows: list[tuple[str, str, int, int, float]]) -> None:
    table, key = DAILY_BREAKDOWNS[dimension]
    if not rows:
        return
    with dashboard_app.db() as con:
        # the synced window is rebuilt as a whole: GSC revises recent days
        con.execute(f"DELETE FROM {table} WHERE report_date >= ?", (min(r[0] for r in rows),))
        con.executemany(
            f"INSERT OR REPLACE INTO {table} (report_date, {key}, clicks, impressions, position) VALUES (?, ?, ?, ?, ?)",
            rows,
        )


def fetch_page_queries(service, site_url: str) -> list[tuple[str, str, int, int, float]]:
    """[page, query] over the last PAGE_QUERY_DAYS days, one snapshot. Links
    each top page to the searches that bring it impressions; a per-day
    version would be several hundred thousand rows for little gain."""
    _, end_date = _date_range()
    start_date = (date.fromisoformat(end_date) - timedelta(days=PAGE_QUERY_DAYS)).isoformat()
    rows = _query_all(service, site_url, {"startDate": start_date, "endDate": end_date, "dimensions": ["page", "query"]})
    return [
        (row["keys"][0], row["keys"][1], int(row.get("clicks", 0)), int(row.get("impressions", 0)), round(float(row.get("position", 0.0)), 1))
        for row in rows
    ]


def store_page_queries(rows: list[tuple[str, str, int, int, float]]) -> None:
    if not rows:
        return
    with dashboard_app.db() as con:
        con.execute("DELETE FROM gsc_page_queries")
        con.executemany(
            "INSERT OR REPLACE INTO gsc_page_queries (page, query, clicks, impressions, position, synced_at) "
            "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            rows,
        )


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


def fetch_recent_post_performance(service, site_url: str, real_site_url: str) -> list[tuple[str, str, int, int, float, float, str | None]]:
    """Search performance for the most recently published posts specifically
    (not just whatever happens to be in the global top-clicks list) - a
    3-week-old post can have real impressions/clicks that never show up in
    fetch_top_queries because it's not a top-50 query yet. `site_url` is
    GSC's siteUrl (used for the search query); `real_site_url` is the actual
    https:// site (used to fetch the sitemap) - not the same value when the
    GSC property is a Domain property (`sc-domain:...`)."""
    recent_posts = sitemap_utils.fetch_recent_posts_with_titles(real_site_url, limit=MAX_RECENT_POSTS)
    if not recent_posts:
        return []

    start_date, end_date = _date_range()
    body = {"startDate": start_date, "endDate": end_date, "dimensions": ["page"], "rowLimit": 5000}
    response = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    by_page = {row["keys"][0]: row for row in response.get("rows", [])}

    rows: list[tuple[str, str, int, int, float, float, str | None]] = []
    for url, lastmod, title in recent_posts:
        match = by_page.get(url)
        clicks = int(match.get("clicks", 0)) if match else 0
        impressions = int(match.get("impressions", 0)) if match else 0
        ctr = round(float(match.get("ctr", 0.0)) * 100, 2) if match else 0.0
        position = round(float(match.get("position", 0.0)), 1) if match else 0.0
        rows.append((url, lastmod, clicks, impressions, ctr, position, title))
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


def store_recent_post_performance(rows: list[tuple[str, str, int, int, float, float, str | None]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM content_posts_gsc")
        con.executemany(
            "INSERT INTO content_posts_gsc (url, published_at, clicks, impressions, ctr, position, title, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
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
    except Exception as error:  # noqa: BLE001 - log then re-raise; an expired OAuth token raises google's RefreshError, not GscSyncError, and used to vanish without a sync_log row
        log_sync(dashboard_app, "gsc", "error", f"{type(error).__name__}: {error}")
        raise

    store_daily(daily)
    store_top_queries(top_queries)
    store_top_countries(countries)
    store_device_breakdown(devices)
    if FALLBACK_NOTE:
        log_sync(dashboard_app, "gsc_auth", "error", FALLBACK_NOTE)
    elif env.get("GSC_OAUTH_REFRESH_TOKEN"):
        # clears the "token expired" alert once the token works again
        log_sync(dashboard_app, "gsc_auth", "ok", f"OAuth token valid ({site_url})")
    log_sync(dashboard_app, "gsc", "ok", f"{len(daily)} daily rows, {len(top_queries)} top queries, {len(countries)} countries, {len(devices)} devices")
    print(f"Search Console sync complete: {len(daily)} daily rows, {len(top_queries)} top queries, {len(countries)} countries, {len(devices)} devices.")

    for dimension in DAILY_BREAKDOWNS:
        try:
            breakdown = fetch_daily_breakdown(service, site_url, dimension)
            store_daily_breakdown(dimension, breakdown)
            log_sync(dashboard_app, f"gsc_{dimension}_daily", "ok", f"{len(breakdown)} day/{dimension} rows")
            print(f"Search Console per-day {dimension} rows: {len(breakdown)}.")
        except Exception as error:  # noqa: BLE001 - best-effort, the summary falls back to the 180-day tables
            log_sync(dashboard_app, f"gsc_{dimension}_daily", "skipped", str(error))
            print(f"WARNING: per-day {dimension} breakdown skipped ({error})", file=sys.stderr)

    try:
        page_queries = fetch_page_queries(service, site_url)
        store_page_queries(page_queries)
        log_sync(dashboard_app, "gsc_page_queries", "ok", f"{len(page_queries)} page/query rows")
        print(f"Search Console page/query snapshot: {len(page_queries)} rows.")
    except Exception as error:  # noqa: BLE001 - best-effort
        log_sync(dashboard_app, "gsc_page_queries", "skipped", str(error))
        print(f"WARNING: page/query snapshot skipped ({error})", file=sys.stderr)

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
