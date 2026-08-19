"""Pull site-wide visitor counts from Google Analytics (GA4) into local SQLite.

Only aggregated day-level, channel-level and page-level counts are stored -
no user-level or PII data is requested from the GA4 Data API at all (GA4
itself is aggregated by design).

Requires GA4_PROPERTY_ID and GA4_SERVICE_ACCOUNT_FILE in .env - the service
account must be added as a Viewer on the GA4 property (Admin > Property
Access Management > Add users).

Unlike the sibling PreSubs project, no landing-page filter is applied here -
DOMA wants whole-site organic/blog traffic, not just one funnel page.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402
from scripts import sitemap_utils  # noqa: E402

LOOKBACK_DAYS = 180
TOP_PAGE_LIMIT = 30
MAX_RECENT_POSTS = 15


class Ga4SyncError(RuntimeError):
    pass


def _client(env: dict[str, str]):
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.oauth2 import service_account
    except ImportError as error:
        raise Ga4SyncError(
            "google-analytics-data is not installed. Run: pip install -r requirements.txt"
        ) from error

    property_id = env.get("GA4_PROPERTY_ID")
    key_file = env.get("GA4_SERVICE_ACCOUNT_FILE")
    if not property_id or not key_file:
        raise Ga4SyncError("Missing GA4_PROPERTY_ID or GA4_SERVICE_ACCOUNT_FILE in .env")

    key_path = ROOT / key_file
    if not key_path.exists():
        raise Ga4SyncError(f"GA4 service account file not found: {key_path}")

    creds = service_account.Credentials.from_service_account_file(str(key_path))
    return BetaAnalyticsDataClient(credentials=creds), property_id


def fetch_daily_traffic(client, property_id: str) -> list[tuple[str, int, int, int, int]]:
    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="date")],
        metrics=[
            Metric(name="activeUsers"),
            Metric(name="newUsers"),
            Metric(name="sessions"),
            Metric(name="engagedSessions"),
        ],
        date_ranges=[DateRange(start_date=f"{LOOKBACK_DAYS}daysAgo", end_date="today")],
    )
    response = client.run_report(request)
    rows: list[tuple[str, int, int, int, int]] = []
    for row in response.rows:
        raw_date = row.dimension_values[0].value  # YYYYMMDD
        report_date = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        active_users = int(row.metric_values[0].value or 0)
        new_users = int(row.metric_values[1].value or 0)
        sessions = int(row.metric_values[2].value or 0)
        engaged_sessions = int(row.metric_values[3].value or 0)
        rows.append((report_date, active_users, new_users, sessions, engaged_sessions))
    return rows


def fetch_daily_channel(client, property_id: str) -> list[tuple[str, str, int]]:
    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="date"), Dimension(name="sessionDefaultChannelGroup")],
        metrics=[Metric(name="sessions")],
        date_ranges=[DateRange(start_date=f"{LOOKBACK_DAYS}daysAgo", end_date="today")],
    )
    response = client.run_report(request)
    rows: list[tuple[str, str, int]] = []
    for row in response.rows:
        raw_date = row.dimension_values[0].value
        report_date = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        channel_group = row.dimension_values[1].value or "(unassigned)"
        sessions = int(row.metric_values[0].value or 0)
        rows.append((report_date, channel_group, sessions))
    return rows


def fetch_top_pages(client, property_id: str) -> list[tuple[str, str, int, int]]:
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        OrderBy,
        RunReportRequest,
    )

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="activeUsers"),
            Metric(name="screenPageViews"),
            Metric(name="userEngagementDuration"),
            Metric(name="bounceRate"),
        ],
        date_ranges=[DateRange(start_date=f"{LOOKBACK_DAYS}daysAgo", end_date="today")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
        limit=TOP_PAGE_LIMIT,
    )
    response = client.run_report(request)
    rows: list[tuple[str, str, int, int, int, float, float]] = []
    for row in response.rows:
        page_path = row.dimension_values[0].value
        page_title = row.dimension_values[1].value
        sessions = int(row.metric_values[0].value or 0)
        active_users = int(row.metric_values[1].value or 0)
        page_views = int(row.metric_values[2].value or 0)
        engagement_duration = float(row.metric_values[3].value or 0)
        bounce_rate = round(float(row.metric_values[4].value or 0) * 100, 1)
        avg_engagement_seconds = round(engagement_duration / page_views, 1) if page_views else 0.0
        rows.append((page_path, page_title, sessions, active_users, page_views, avg_engagement_seconds, bounce_rate))
    return rows


def fetch_top_countries(client, property_id: str) -> list[tuple[str, int, int]]:
    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, OrderBy, RunReportRequest

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="country")],
        metrics=[Metric(name="activeUsers"), Metric(name="sessions")],
        date_ranges=[DateRange(start_date=f"{LOOKBACK_DAYS}daysAgo", end_date="today")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="activeUsers"), desc=True)],
        limit=25,
    )
    response = client.run_report(request)
    rows: list[tuple[str, int, int]] = []
    for row in response.rows:
        country = row.dimension_values[0].value or "(not set)"
        active_users = int(row.metric_values[0].value or 0)
        sessions = int(row.metric_values[1].value or 0)
        rows.append((country, active_users, sessions))
    return rows


def fetch_device_breakdown(client, property_id: str) -> list[tuple[str, int, int]]:
    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="deviceCategory")],
        metrics=[Metric(name="activeUsers"), Metric(name="sessions")],
        date_ranges=[DateRange(start_date=f"{LOOKBACK_DAYS}daysAgo", end_date="today")],
    )
    response = client.run_report(request)
    rows: list[tuple[str, int, int]] = []
    for row in response.rows:
        device = row.dimension_values[0].value or "(not set)"
        active_users = int(row.metric_values[0].value or 0)
        sessions = int(row.metric_values[1].value or 0)
        rows.append((device, active_users, sessions))
    return rows


def fetch_demographics(client, property_id: str) -> list[tuple[str, str, int]]:
    """Gender + age bracket - only populated once Google Signals is enabled
    on the property (Admin > Data Collection); until then GA4 correctly
    returns zero rows rather than erroring, which is not a bug here."""
    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    rows: list[tuple[str, str, int]] = []
    for dimension_name, label in (("userGender", "gender"), ("userAgeBracket", "age")):
        request = RunReportRequest(
            property=f"properties/{property_id}",
            dimensions=[Dimension(name=dimension_name)],
            metrics=[Metric(name="activeUsers")],
            date_ranges=[DateRange(start_date=f"{LOOKBACK_DAYS}daysAgo", end_date="today")],
        )
        response = client.run_report(request)
        for row in response.rows:
            value = row.dimension_values[0].value or "(not set)"
            active_users = int(row.metric_values[0].value or 0)
            rows.append((label, value, active_users))
    return rows


def fetch_recent_post_metrics(client, property_id: str, site_url: str) -> list[tuple[str, str, int, int, int, float]]:
    """GA4 metrics for the most recently published blog posts specifically -
    fetch_top_pages only returns the top 30 by session count, so a
    brand-new post with modest traffic so far would never show up there."""
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Filter,
        FilterExpression,
        Metric,
        RunReportRequest,
    )
    from urllib.parse import urlparse

    recent_posts = sitemap_utils.fetch_recent_posts(site_url, limit=MAX_RECENT_POSTS)
    if not recent_posts:
        return []
    paths = [urlparse(url).path for url, _ in recent_posts]

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="pagePath")],
        metrics=[Metric(name="sessions"), Metric(name="screenPageViews"), Metric(name="activeUsers"), Metric(name="userEngagementDuration")],
        date_ranges=[DateRange(start_date=f"{LOOKBACK_DAYS}daysAgo", end_date="today")],
        dimension_filter=FilterExpression(
            filter=Filter(field_name="pagePath", in_list_filter=Filter.InListFilter(values=paths))
        ),
    )
    response = client.run_report(request)
    by_path = {row.dimension_values[0].value: row for row in response.rows}

    rows: list[tuple[str, str, int, int, int, float]] = []
    for url, lastmod in recent_posts:
        path = urlparse(url).path
        match = by_path.get(path)
        sessions = int(match.metric_values[0].value or 0) if match else 0
        page_views = int(match.metric_values[1].value or 0) if match else 0
        active_users = int(match.metric_values[2].value or 0) if match else 0
        engagement_duration = float(match.metric_values[3].value or 0) if match else 0.0
        avg_engagement_seconds = round(engagement_duration / page_views, 1) if page_views else 0.0
        rows.append((url, lastmod, sessions, page_views, active_users, avg_engagement_seconds))
    return rows


def store_daily_traffic(rows: list[tuple[str, int, int, int, int]]) -> None:
    with dashboard_app.db() as con:
        con.executemany(
            """
            INSERT INTO ga4_traffic_daily
                (report_date, active_users, new_users, sessions, engaged_sessions, synced_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(report_date) DO UPDATE SET
                active_users = excluded.active_users,
                new_users = excluded.new_users,
                sessions = excluded.sessions,
                engaged_sessions = excluded.engaged_sessions,
                synced_at = CURRENT_TIMESTAMP
            """,
            rows,
        )


def store_daily_channel(rows: list[tuple[str, str, int]]) -> None:
    with dashboard_app.db() as con:
        con.executemany(
            """
            INSERT INTO ga4_channel_daily (report_date, channel_group, sessions)
            VALUES (?, ?, ?)
            ON CONFLICT(report_date, channel_group) DO UPDATE SET
                sessions = excluded.sessions
            """,
            rows,
        )


def store_top_pages(rows: list[tuple[str, str, int, int, int, float, float]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM ga4_top_pages")
        con.executemany(
            """
            INSERT INTO ga4_top_pages
                (page_path, page_title, sessions, active_users, page_views, avg_engagement_seconds, bounce_rate, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(page_path) DO UPDATE SET
                page_title = excluded.page_title,
                sessions = excluded.sessions,
                active_users = excluded.active_users,
                page_views = excluded.page_views,
                avg_engagement_seconds = excluded.avg_engagement_seconds,
                bounce_rate = excluded.bounce_rate,
                synced_at = CURRENT_TIMESTAMP
            """,
            rows,
        )


def store_top_countries(rows: list[tuple[str, int, int]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM ga4_countries")
        con.executemany(
            "INSERT INTO ga4_countries (country, active_users, sessions, synced_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            rows,
        )


def store_devices(rows: list[tuple[str, int, int]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM ga4_devices")
        con.executemany(
            "INSERT INTO ga4_devices (device_category, active_users, sessions, synced_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            rows,
        )


def store_recent_post_metrics(rows: list[tuple[str, str, int, int, int, float]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM content_posts_ga4")
        con.executemany(
            "INSERT INTO content_posts_ga4 (url, published_at, sessions, page_views, active_users, avg_engagement_seconds, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            rows,
        )


def store_demographics(rows: list[tuple[str, str, int]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM ga4_demographics")
        con.executemany(
            "INSERT INTO ga4_demographics (dimension_type, dimension_value, active_users, synced_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            rows,
        )


def main() -> int:
    env = load_env_file()
    dashboard_app.init_db()

    try:
        client, property_id = _client(env)
        traffic = fetch_daily_traffic(client, property_id)
        channels = fetch_daily_channel(client, property_id)
        top_pages = fetch_top_pages(client, property_id)
        countries = fetch_top_countries(client, property_id)
        demographics = fetch_demographics(client, property_id)
        devices = fetch_device_breakdown(client, property_id)
    except Ga4SyncError as error:
        log_sync(dashboard_app, "ga4", "error", str(error))
        raise

    store_daily_traffic(traffic)
    store_daily_channel(channels)
    store_top_pages(top_pages)
    store_top_countries(countries)
    store_demographics(demographics)
    store_devices(devices)
    log_sync(
        dashboard_app,
        "ga4",
        "ok",
        f"{len(traffic)} traffic-days, {len(channels)} channel-day rows, {len(top_pages)} top pages, "
        f"{len(countries)} countries, {len(demographics)} demographic rows",
    )

    print(
        f"GA4 sync complete: {len(traffic)} traffic-days, {len(channels)} channel-day rows, "
        f"{len(top_pages)} top pages, {len(countries)} countries, {len(demographics)} demographic rows."
    )

    site_url = env.get("GSC_SITE_URL")
    if site_url:
        try:
            recent_posts = fetch_recent_post_metrics(client, property_id, site_url)
            store_recent_post_metrics(recent_posts)
            log_sync(dashboard_app, "ga4_recent_posts", "ok", f"{len(recent_posts)} posts")
            print(f"Recent-post GA4 performance complete: {len(recent_posts)} posts.")
        except Exception as error:  # noqa: BLE001 - best-effort, must not break the core GA4 sync
            log_sync(dashboard_app, "ga4_recent_posts", "skipped", str(error))
            print(f"WARNING: recent-post GA4 performance skipped ({error})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
