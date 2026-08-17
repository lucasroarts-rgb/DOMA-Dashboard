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

LOOKBACK_DAYS = 180
TOP_QUERY_LIMIT = 50


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


def main() -> int:
    env = load_env_file()
    dashboard_app.init_db()

    try:
        service, site_url = _client(env)
        daily = fetch_daily(service, site_url)
        top_queries = fetch_top_queries(service, site_url)
    except GscSyncError as error:
        log_sync(dashboard_app, "gsc", "error", str(error))
        raise

    store_daily(daily)
    store_top_queries(top_queries)
    log_sync(dashboard_app, "gsc", "ok", f"{len(daily)} daily rows, {len(top_queries)} top queries")

    print(f"Search Console sync complete: {len(daily)} daily rows, {len(top_queries)} top queries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
