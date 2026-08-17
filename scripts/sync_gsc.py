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
from urllib.parse import urljoin
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402

LOOKBACK_DAYS = 180
TOP_QUERY_LIMIT = 50

# Index-coverage checks (redirects, "not indexed", etc) use the separate URL
# Inspection API, which is per-URL and quota-limited - so instead of every
# page on the site, this checks the site's static pages (all of them, there
# are few) plus the most recently published posts, sourced from the Yoast
# SEO XML sitemap (WordPress).
SITEMAP_INDEX_PATH = "/sitemap_index.xml"
MAX_PAGES_TO_INSPECT = 60
MAX_POSTS_TO_INSPECT = 25
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class GscSyncError(RuntimeError):
    pass


def _client(env: dict[str, str]):
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as error:
        raise GscSyncError(
            "google-api-python-client is not installed. Run: pip install -r requirements.txt"
        ) from error

    site_url = env.get("GSC_SITE_URL")
    key_file = env.get("GA4_SERVICE_ACCOUNT_FILE")
    if not site_url or not key_file:
        raise GscSyncError("Missing GSC_SITE_URL or GA4_SERVICE_ACCOUNT_FILE in .env")

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


def store_top_countries(rows: list[tuple[str, int, int, float, float]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM gsc_countries")
        con.executemany(
            "INSERT INTO gsc_countries (country, clicks, impressions, ctr, position, synced_at) "
            "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            rows,
        )


def _fetch_xml(url: str) -> ElementTree.Element | None:
    try:
        import requests
    except ImportError as error:
        raise GscSyncError("requests is not installed. Run: pip install -r requirements.txt") from error

    response = requests.get(url, timeout=20, headers={"User-Agent": "doma-dashboard-sync"})
    if response.status_code != 200:
        return None
    try:
        return ElementTree.fromstring(response.content)
    except ElementTree.ParseError:
        return None


def fetch_urls_to_inspect(site_url: str) -> list[str]:
    """Discover URLs from the WordPress/Yoast XML sitemap: every static page,
    plus the most recently published posts (most likely to have fresh
    indexing problems - a brand-new post not indexed yet, a redirect from a
    slug change, etc)."""
    index_root = _fetch_xml(urljoin(site_url, SITEMAP_INDEX_PATH))
    if index_root is None:
        return []

    sub_sitemaps = [node.text for node in index_root.findall(".//sm:sitemap/sm:loc", SITEMAP_NS) if node.text]

    def urls_from_sitemap(sitemap_url: str) -> list[tuple[str, str]]:
        root = _fetch_xml(sitemap_url)
        if root is None:
            return []
        entries = []
        for url_node in root.findall(".//sm:url", SITEMAP_NS):
            loc = url_node.find("sm:loc", SITEMAP_NS)
            lastmod = url_node.find("sm:lastmod", SITEMAP_NS)
            if loc is not None and loc.text:
                entries.append((loc.text, lastmod.text if lastmod is not None else ""))
        return entries

    urls: list[str] = []
    for sitemap_url in sub_sitemaps:
        if "page-sitemap" in sitemap_url:
            pages = urls_from_sitemap(sitemap_url)
            urls.extend(loc for loc, _ in pages[:MAX_PAGES_TO_INSPECT])
        elif "post-sitemap" in sitemap_url:
            posts = urls_from_sitemap(sitemap_url)
            posts.sort(key=lambda entry: entry[1], reverse=True)
            urls.extend(loc for loc, _ in posts[:MAX_POSTS_TO_INSPECT])

    seen = set()
    deduped = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


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
        daily = fetch_daily(service, site_url)
        top_queries = fetch_top_queries(service, site_url)
        countries = fetch_top_countries(service, site_url)
    except GscSyncError as error:
        log_sync(dashboard_app, "gsc", "error", str(error))
        raise

    store_daily(daily)
    store_top_queries(top_queries)
    store_top_countries(countries)
    log_sync(dashboard_app, "gsc", "ok", f"{len(daily)} daily rows, {len(top_queries)} top queries, {len(countries)} countries")
    print(f"Search Console sync complete: {len(daily)} daily rows, {len(top_queries)} top queries, {len(countries)} countries.")

    try:
        urls = fetch_urls_to_inspect(site_url)
        inspections = fetch_url_inspections(service, site_url, urls)
        store_url_inspections(inspections)
        log_sync(dashboard_app, "gsc_indexing", "ok", f"{len(inspections)} URLs inspected")
        print(f"Index coverage check complete: {len(inspections)} URLs inspected.")
    except Exception as error:  # noqa: BLE001 - indexing check is best-effort, must not break the core GSC sync
        log_sync(dashboard_app, "gsc_indexing", "skipped", str(error))
        print(f"WARNING: index coverage check skipped ({error})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
