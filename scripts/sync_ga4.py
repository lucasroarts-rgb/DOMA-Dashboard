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

from typing import Any

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402
from scripts import sitemap_utils  # noqa: E402

LOOKBACK_DAYS = 180
TOP_PAGE_LIMIT = 250  # rows asked from GA4 (path x title)
TOP_PAGE_KEEP = 100  # pages kept after adding the rows up per path
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
    # One page can come back as several rows (one per title it had over the
    # window), and ga4_top_pages is keyed by path - storing them one by one
    # let the smaller row overwrite the bigger one. Add the rows up per path,
    # keep the title with the most sessions, then keep the top pages.
    by_path: dict[str, dict[str, Any]] = {}
    for row in response.rows:
        page_path = row.dimension_values[0].value
        page_title = row.dimension_values[1].value
        sessions = int(row.metric_values[0].value or 0)
        agg = by_path.setdefault(page_path, {"title": page_title, "title_sessions": -1, "sessions": 0, "users": 0, "views": 0, "duration": 0.0, "bounced": 0.0})
        if sessions > agg["title_sessions"]:
            agg["title"], agg["title_sessions"] = page_title, sessions
        agg["sessions"] += sessions
        agg["users"] += int(row.metric_values[1].value or 0)
        agg["views"] += int(row.metric_values[2].value or 0)
        agg["duration"] += float(row.metric_values[3].value or 0)
        agg["bounced"] += float(row.metric_values[4].value or 0) * sessions
    rows: list[tuple[str, str, int, int, int, float, float]] = []
    for page_path, agg in sorted(by_path.items(), key=lambda item: item[1]["sessions"], reverse=True)[:TOP_PAGE_KEEP]:
        avg_engagement_seconds = round(agg["duration"] / agg["views"], 1) if agg["views"] else 0.0
        bounce_rate = round(agg["bounced"] / agg["sessions"] * 100, 1) if agg["sessions"] else 0.0
        rows.append((page_path, agg["title"], agg["sessions"], agg["users"], agg["views"], avg_engagement_seconds, bounce_rate))
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


def fetch_page_conversions(client, property_id: str) -> list[tuple[str, str, int, float]]:
    """Sessions and key events (guide sign-ups, membership) per landing page
    per day, so the dashboard can rank pages by what they convert in any
    range. Paged with offset: a day x page report runs past one 100k page
    only on a much bigger site, but the loop keeps it correct anyway."""
    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    rows: list[tuple[str, str, int, float]] = []
    offset = 0
    while True:
        request = RunReportRequest(
            property=f"properties/{property_id}",
            dimensions=[Dimension(name="date"), Dimension(name="landingPage")],
            metrics=[Metric(name="sessions"), Metric(name="keyEvents")],
            date_ranges=[DateRange(start_date=f"{LOOKBACK_DAYS}daysAgo", end_date="today")],
            limit=100000,
            offset=offset,
        )
        response = client.run_report(request)
        for row in response.rows:
            raw_date = row.dimension_values[0].value
            page = row.dimension_values[1].value or "(not set)"
            if page == "(not set)":
                continue
            path = page.split("?", 1)[0]
            # landingPage drops the trailing slash pagePath keeps; match pagePath
            if not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
                path += "/"
            rows.append((
                f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}",
                path,
                int(row.metric_values[0].value or 0),
                float(row.metric_values[1].value or 0),
            ))
        offset += len(response.rows)
        if not response.rows or offset >= response.row_count:
            break
    # landingPage can differ only by query string; add those rows up
    merged: dict[tuple[str, str], list] = {}
    for report_date, page, sessions, key_events in rows:
        agg = merged.setdefault((report_date, page), [0, 0.0])
        agg[0] += sessions
        agg[1] += key_events
    return [(d, page, agg[0], agg[1]) for (d, page), agg in merged.items()]


# ---------- sponsor offers (/offers/) and site-wide button clicks ----------
# Daily rows so the Partners tab's Sponsors panel follows the range picker.

def _run_paged(client, request) -> list:
    rows = []
    offset = 0
    while True:
        request.offset = offset
        response = client.run_report(request)
        rows.extend(response.rows)
        offset += len(response.rows)
        if not response.rows or offset >= response.row_count:
            return rows


def _iso(raw_date: str) -> str:
    return f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"


def _offers_path_filter():
    from google.analytics.data_v1beta.types import Filter, FilterExpression

    return FilterExpression(
        filter=Filter(
            field_name="pagePath",
            string_filter=Filter.StringFilter(match_type=Filter.StringFilter.MatchType.BEGINS_WITH, value="/offers"),
        )
    )


def _event_filter(event_name: str):
    from google.analytics.data_v1beta.types import Filter, FilterExpression

    return FilterExpression(
        filter=Filter(
            field_name="eventName",
            string_filter=Filter.StringFilter(match_type=Filter.StringFilter.MatchType.EXACT, value=event_name),
        )
    )


def fetch_offers_pages(client, property_id: str) -> list[tuple[str, str, int, int]]:
    """Views and sessions of /offers/ and every /offers/<sponsor>/ page, per day."""
    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="date"), Dimension(name="pagePath")],
        metrics=[Metric(name="screenPageViews"), Metric(name="sessions")],
        date_ranges=[DateRange(start_date=f"{LOOKBACK_DAYS}daysAgo", end_date="today")],
        dimension_filter=_offers_path_filter(),
        limit=100000,
    )
    merged: dict[tuple[str, str], list[int]] = {}
    for row in _run_paged(client, request):
        path = row.dimension_values[1].value.split("?", 1)[0]
        if not path.endswith("/"):
            path += "/"
        agg = merged.setdefault((_iso(row.dimension_values[0].value), path), [0, 0])
        agg[0] += int(row.metric_values[0].value or 0)
        agg[1] += int(row.metric_values[1].value or 0)
    return [(d, path, v[0], v[1]) for (d, path), v in merged.items()]


def fetch_offers_clicks(client, property_id: str) -> list[tuple[str, str, str, int]]:
    """Outbound clicks (GA4 enhanced-measurement "click" event) from /offers
    pages, per day, page and link domain."""
    from google.analytics.data_v1beta.types import DateRange, Dimension, FilterExpression, FilterExpressionList, Metric, RunReportRequest

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="date"), Dimension(name="pagePath"), Dimension(name="linkDomain")],
        metrics=[Metric(name="eventCount")],
        date_ranges=[DateRange(start_date=f"{LOOKBACK_DAYS}daysAgo", end_date="today")],
        dimension_filter=FilterExpression(and_group=FilterExpressionList(expressions=[_event_filter("click"), _offers_path_filter()])),
        limit=100000,
    )
    merged: dict[tuple[str, str, str], int] = {}
    for row in _run_paged(client, request):
        path = row.dimension_values[1].value.split("?", 1)[0]
        if not path.endswith("/"):
            path += "/"
        domain = (row.dimension_values[2].value or "(not set)").lower()
        if domain.startswith("www."):
            domain = domain[4:]
        key = (_iso(row.dimension_values[0].value), path, domain)
        merged[key] = merged.get(key, 0) + int(row.metric_values[0].value or 0)
    return [(d, path, domain, clicks) for (d, path, domain), clicks in merged.items()]


def fetch_cta_clicks(client, property_id: str) -> list[tuple[str, str, str, str, int]]:
    """Site-wide "cta_click" event (added 2026-10-15) by page, button text and
    destination. Needs the event-scoped custom dimensions customEvent:link_text
    and customEvent:link_url registered in GA4; until then GA4 rejects the
    request and the caller skips this step."""
    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[
            Dimension(name="date"),
            Dimension(name="pagePath"),
            Dimension(name="customEvent:link_text"),
            Dimension(name="customEvent:link_url"),
        ],
        metrics=[Metric(name="eventCount")],
        date_ranges=[DateRange(start_date=f"{LOOKBACK_DAYS}daysAgo", end_date="today")],
        dimension_filter=_event_filter("cta_click"),
        limit=100000,
    )
    merged: dict[tuple[str, str, str, str], int] = {}
    for row in _run_paged(client, request):
        path = row.dimension_values[1].value.split("?", 1)[0] or "/"
        text = " ".join((row.dimension_values[2].value or "(not set)").split())[:120]
        url = (row.dimension_values[3].value or "(not set)")[:300]
        key = (_iso(row.dimension_values[0].value), path, text, url)
        merged[key] = merged.get(key, 0) + int(row.metric_values[0].value or 0)
    return [(*key, clicks) for key, clicks in merged.items()]


def _replace_daily(table: str, columns: str, rows: list[tuple]) -> None:
    placeholders = ", ".join("?" for _ in columns.split(","))
    with dashboard_app.db() as con:
        con.execute(f"DELETE FROM {table} WHERE report_date >= date('now', ?)", (f"-{LOOKBACK_DAYS + 1} days",))
        con.executemany(f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})", rows)


def sync_offers_and_ctas(client, property_id: str) -> None:
    """Best-effort steps: each one logs its own sync_log row and never breaks
    the core GA4 sync."""
    steps = [
        ("ga4_offers_pages", fetch_offers_pages, "ga4_offers_pages_daily", "report_date, page_path, page_views, sessions"),
        ("ga4_offers_clicks", fetch_offers_clicks, "ga4_offers_clicks_daily", "report_date, page_path, link_domain, clicks"),
        ("ga4_cta_clicks", fetch_cta_clicks, "ga4_cta_clicks_daily", "report_date, page_path, link_text, link_url, clicks"),
    ]
    for source, fetch, table, columns in steps:
        try:
            rows = fetch(client, property_id)
            _replace_daily(table, columns, rows)
            log_sync(dashboard_app, source, "ok", f"{len(rows)} day rows")
            print(f"GA4 {source} complete: {len(rows)} day rows.")
        except Exception as error:  # noqa: BLE001 - e.g. custom dimensions not registered yet
            log_sync(dashboard_app, source, "skipped", f"{type(error).__name__}: {str(error)[:300]}")
            print(f"WARNING: GA4 {source} skipped ({str(error)[:200]})", file=sys.stderr)


def store_page_conversions(rows: list[tuple[str, str, int, float]]) -> None:
    with dashboard_app.db() as con:
        if rows:
            con.execute("DELETE FROM ga4_page_conversions_daily WHERE report_date >= ?", (min(r[0] for r in rows),))
        con.executemany(
            "INSERT OR REPLACE INTO ga4_page_conversions_daily (report_date, page_path, sessions, key_events) VALUES (?, ?, ?, ?)",
            rows,
        )


def store_wp_inventory(site_url: str) -> tuple[int, int]:
    """Real post list and free-guide landing pages from WordPress, used by
    Content Suggestions to filter top blog posts and top ebooks. Each list
    is replaced only when the fetch returned something, so a WP hiccup
    keeps yesterday's list instead of emptying the tab."""
    posts = sitemap_utils.fetch_all_posts(site_url)
    guides = sitemap_utils.fetch_free_guide_pages(site_url)
    with dashboard_app.db() as con:
        if posts:
            con.execute("DELETE FROM wp_posts")
            con.executemany(
                "INSERT OR REPLACE INTO wp_posts (page_path, title, published_at, synced_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                posts,
            )
        if guides:
            con.execute("DELETE FROM free_guide_pages")
            con.executemany(
                "INSERT OR REPLACE INTO free_guide_pages (page_path, title, synced_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                [(path, re.sub(r"^\s*e-?book\s*-\s*|\s*-\s*free (guide|ebook)( \| doma)?\s*$", "", title or "", flags=re.I) or title)
                 for path, title in guides],
            )
    return len(posts), len(guides)


def fetch_recent_post_metrics(client, property_id: str, site_url: str) -> list[tuple[str, str, int, int, int, float, str | None]]:
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

    recent_posts = sitemap_utils.fetch_recent_posts_with_titles(site_url, limit=MAX_RECENT_POSTS)
    if not recent_posts:
        return []
    paths = [urlparse(url).path for url, _, _ in recent_posts]

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

    rows: list[tuple[str, str, int, int, int, float, str | None]] = []
    for url, lastmod, title in recent_posts:
        path = urlparse(url).path
        match = by_path.get(path)
        sessions = int(match.metric_values[0].value or 0) if match else 0
        page_views = int(match.metric_values[1].value or 0) if match else 0
        active_users = int(match.metric_values[2].value or 0) if match else 0
        engagement_duration = float(match.metric_values[3].value or 0) if match else 0.0
        avg_engagement_seconds = round(engagement_duration / page_views, 1) if page_views else 0.0
        rows.append((url, lastmod, sessions, page_views, active_users, avg_engagement_seconds, title))
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
    # Replace the fetched window: GA4 later moves sessions out of "Unassigned" /
    # "Cross-network", and an upsert-only write kept the old rows next to the new ones
    # (channel sums ran 43% above total sessions in Sep-Oct 2026).
    if not rows:
        return
    with dashboard_app.db() as con:
        con.execute("DELETE FROM ga4_channel_daily WHERE report_date >= ?", (min(r[0] for r in rows),))
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


def store_recent_post_metrics(rows: list[tuple[str, str, int, int, int, float, str | None]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM content_posts_ga4")
        con.executemany(
            "INSERT INTO content_posts_ga4 (url, published_at, sessions, page_views, active_users, avg_engagement_seconds, title, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
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
    except Exception as error:  # noqa: BLE001 - log then re-raise, so auth/transport errors that aren't Ga4SyncError still leave a sync_log row
        log_sync(dashboard_app, "ga4", "error", f"{type(error).__name__}: {error}")
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

    # fetch_recent_post_metrics() fetches the sitemap over plain HTTP -
    # GSC_SITE_URL is `sc-domain:...` for a Domain property (not a URL).
    site_url = env.get("WP_URL") or env.get("GSC_SITE_URL")
    try:
        conversions = fetch_page_conversions(client, property_id)
        store_page_conversions(conversions)
        log_sync(dashboard_app, "ga4_conversions", "ok", f"{len(conversions)} day/page rows")
        print(f"GA4 key events by landing page complete: {len(conversions)} day/page rows.")
    except Exception as error:  # noqa: BLE001 - best-effort, must not break the core GA4 sync
        log_sync(dashboard_app, "ga4_conversions", "skipped", str(error))
        print(f"WARNING: GA4 key events by page skipped ({error})", file=sys.stderr)

    sync_offers_and_ctas(client, property_id)

    if site_url:
        try:
            post_count, guide_count = store_wp_inventory(site_url)
            log_sync(dashboard_app, "wp_inventory", "ok", f"{post_count} posts, {guide_count} free-guide pages")
            print(f"WordPress inventory complete: {post_count} posts, {guide_count} free-guide pages.")
        except Exception as error:  # noqa: BLE001 - best-effort
            log_sync(dashboard_app, "wp_inventory", "skipped", str(error))
            print(f"WARNING: WordPress inventory skipped ({error})", file=sys.stderr)
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
