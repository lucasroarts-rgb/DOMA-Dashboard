"""Pull organic (unpaid) Facebook Page and Instagram Business Account activity
into local SQLite - follower counts, page/account engagement, and per-post
performance. No ad spend, no Ads API, no paid reach - this is Meta's organic
Graph API only, matching the project's "no paid traffic" rule.

Requires META_PAGE_ID, META_PAGE_ACCESS_TOKEN, META_IG_ACCOUNT_ID in .env.
See README.md for how the Page Access Token was generated (Graph API
Explorer -> "Get Page Access Token" -> exchanged for a long-lived token that
does not expire, via /oauth/access_token?grant_type=fb_exchange_token).

Facebook dropped nearly all Page-level reach/impressions metrics from the
Graph API (privacy-driven deprecations) - only engagement-style metrics
(page_post_engagements, page_views_total) and per-post
likes/comments/shares/reactions remain. Instagram still exposes reach at
both the account and per-post level, so Instagram numbers are richer here
by nature of what Meta's API still offers, not a bug in this script.

Follower counts have no historical-trend endpoint left either - this script
just snapshots the current count each day, and the trend is built from our
own daily history over time (same pattern as everything else in this repo).
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402

API_VERSION = "v21.0"
API_BASE = f"https://graph.facebook.com/{API_VERSION}"
POST_LIMIT = 20
LOOKBACK_DAYS = 30  # daily account-level metrics (reach, engagement) - Meta's own API caps this window


class MetaOrganicSyncError(RuntimeError):
    pass


def _require_env(env: dict[str, str]) -> tuple[str, str, str]:
    page_id = env.get("META_PAGE_ID")
    page_token = env.get("META_PAGE_ACCESS_TOKEN")
    ig_id = env.get("META_IG_ACCOUNT_ID")
    missing = [
        name
        for name, value in [("META_PAGE_ID", page_id), ("META_PAGE_ACCESS_TOKEN", page_token), ("META_IG_ACCOUNT_ID", ig_id)]
        if not value
    ]
    if missing:
        raise MetaOrganicSyncError("Missing in .env: " + ", ".join(missing))
    return page_id, page_token, ig_id


def _get(path: str, token: str, **params: Any) -> dict[str, Any]:
    try:
        import requests
    except ImportError as error:
        raise MetaOrganicSyncError("requests is not installed. Run: pip install -r requirements.txt") from error

    response = requests.get(f"{API_BASE}/{path}", params={**params, "access_token": token}, timeout=30)
    payload = response.json()
    if response.status_code != 200 or "error" in payload:
        detail = payload.get("error", {}).get("message", response.text[:200])
        raise MetaOrganicSyncError(f"Graph API request failed for {path}: {detail}")
    return payload


def fetch_facebook_snapshot(page_id: str, token: str) -> dict[str, Any]:
    return _get(page_id, token, fields="followers_count,fan_count")


def fetch_facebook_daily_metrics(page_id: str, token: str) -> list[tuple[str, str, float]]:
    since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    until = date.today().isoformat()
    rows: list[tuple[str, str, float]] = []
    for metric in ("page_post_engagements", "page_views_total"):
        try:
            payload = _get(f"{page_id}/insights", token, metric=metric, period="day", since=since, until=until)
        except MetaOrganicSyncError:
            continue  # metric may not be available for every Page - skip, don't fail the whole sync
        for series in payload.get("data", []):
            for point in series.get("values", []):
                report_date = str(point.get("end_time", ""))[:10]
                if report_date:
                    rows.append((report_date, metric, float(point.get("value") or 0)))
    return rows


def fetch_facebook_posts(page_id: str, token: str) -> list[dict[str, Any]]:
    payload = _get(
        f"{page_id}/posts",
        token,
        fields="id,message,created_time,likes.summary(true),comments.summary(true),shares",
        limit=POST_LIMIT,
    )
    posts = []
    for post in payload.get("data", []):
        likes = int((post.get("likes") or {}).get("summary", {}).get("total_count", 0))
        comments = int((post.get("comments") or {}).get("summary", {}).get("total_count", 0))
        shares = int((post.get("shares") or {}).get("count", 0))
        posts.append(
            {
                "platform": "facebook",
                "post_id": post["id"],
                "caption": (post.get("message") or "")[:300],
                "published_at": post.get("created_time"),
                "permalink": f"https://www.facebook.com/{post['id']}",
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "reach": 0,
                "engagement_total": likes + comments + shares,
            }
        )
    return posts


def fetch_instagram_snapshot(ig_id: str, token: str) -> dict[str, Any]:
    return _get(ig_id, token, fields="followers_count,media_count,username")


def fetch_instagram_daily_metrics(ig_id: str, token: str) -> list[tuple[str, str, float]]:
    since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    until = date.today().isoformat()
    rows: list[tuple[str, str, float]] = []
    for metric in ("reach", "follower_count"):
        try:
            payload = _get(f"{ig_id}/insights", token, metric=metric, period="day", since=since, until=until)
        except MetaOrganicSyncError:
            continue
        for series in payload.get("data", []):
            for point in series.get("values", []):
                report_date = str(point.get("end_time", ""))[:10]
                if report_date:
                    rows.append((report_date, f"ig_{metric}", float(point.get("value") or 0)))
    return rows


def fetch_instagram_demographics(ig_id: str, token: str) -> list[tuple[str, str, int]]:
    """Follower gender + country breakdown - Instagram still exposes this
    (via follower_demographics), unlike Facebook Page fan demographics which
    Meta deprecated along with the rest of the Page impressions metrics."""
    rows: list[tuple[str, str, int]] = []
    for dimension in ("gender", "country"):
        try:
            payload = _get(
                f"{ig_id}/insights",
                token,
                metric="follower_demographics",
                period="lifetime",
                metric_type="total_value",
                breakdown=dimension,
            )
        except MetaOrganicSyncError:
            continue
        for series in payload.get("data", []):
            breakdowns = series.get("total_value", {}).get("breakdowns", [])
            for breakdown in breakdowns:
                for result in breakdown.get("results", []):
                    value_key = (result.get("dimension_values") or ["(not set)"])[0]
                    rows.append((dimension, value_key, int(result.get("value") or 0)))
    return rows


def fetch_instagram_posts(ig_id: str, token: str) -> list[dict[str, Any]]:
    payload = _get(
        f"{ig_id}/media",
        token,
        fields="id,caption,timestamp,like_count,comments_count,permalink",
        limit=POST_LIMIT,
    )
    posts = []
    for media in payload.get("data", []):
        likes = int(media.get("like_count") or 0)
        comments = int(media.get("comments_count") or 0)
        reach = 0
        shares = 0
        try:
            insights = _get(f"{media['id']}/insights", token, metric="reach,shares")
            for series in insights.get("data", []):
                value = (series.get("values") or [{}])[0].get("value") or 0
                if series.get("name") == "reach":
                    reach = int(value)
                elif series.get("name") == "shares":
                    shares = int(value)
        except MetaOrganicSyncError:
            pass  # per-post insights can fail for reels/stories - keep the post with reach=0
        posts.append(
            {
                "platform": "instagram",
                "post_id": media["id"],
                "caption": (media.get("caption") or "")[:300],
                "published_at": media.get("timestamp"),
                "permalink": media.get("permalink"),
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "reach": reach,
                "engagement_total": likes + comments + shares,
            }
        )
    return posts


def store_followers(rows: list[tuple[str, str, int]]) -> None:
    with dashboard_app.db() as con:
        con.executemany(
            """
            INSERT INTO social_followers_daily (report_date, platform, follower_count, synced_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(report_date, platform) DO UPDATE SET
                follower_count = excluded.follower_count,
                synced_at = CURRENT_TIMESTAMP
            """,
            rows,
        )


def store_daily_metrics(rows: list[tuple[str, str, float]], *, platform: str) -> None:
    with dashboard_app.db() as con:
        con.executemany(
            """
            INSERT INTO social_metrics_daily (report_date, platform, metric, value)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(report_date, platform, metric) DO UPDATE SET
                value = excluded.value
            """,
            [(report_date, platform, metric, value) for report_date, metric, value in rows],
        )


def store_demographics(rows: list[tuple[str, str, int]], *, platform: str) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM social_audience WHERE platform = ?", (platform,))
        con.executemany(
            "INSERT INTO social_audience (platform, dimension_type, dimension_value, follower_count, synced_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            [(platform, dim, value, count) for dim, value, count in rows],
        )


def store_posts(posts: list[dict[str, Any]]) -> None:
    with dashboard_app.db() as con:
        con.executemany(
            """
            INSERT INTO social_posts
                (platform, post_id, caption, published_at, permalink, likes, comments, shares, reach, engagement_total, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(platform, post_id) DO UPDATE SET
                caption = excluded.caption,
                likes = excluded.likes,
                comments = excluded.comments,
                shares = excluded.shares,
                reach = excluded.reach,
                engagement_total = excluded.engagement_total,
                synced_at = CURRENT_TIMESTAMP
            """,
            [
                (
                    p["platform"], p["post_id"], p["caption"], p["published_at"], p["permalink"],
                    p["likes"], p["comments"], p["shares"], p["reach"], p["engagement_total"],
                )
                for p in posts
            ],
        )


def main() -> int:
    env = load_env_file()
    dashboard_app.init_db()
    page_id, page_token, ig_id = _require_env(env)

    today = date.today().isoformat()

    fb_snapshot = fetch_facebook_snapshot(page_id, page_token)
    fb_followers = int(fb_snapshot.get("followers_count") or fb_snapshot.get("fan_count") or 0)
    fb_daily = fetch_facebook_daily_metrics(page_id, page_token)
    fb_posts = fetch_facebook_posts(page_id, page_token)

    ig_snapshot = fetch_instagram_snapshot(ig_id, page_token)
    ig_followers = int(ig_snapshot.get("followers_count") or 0)
    ig_daily = fetch_instagram_daily_metrics(ig_id, page_token)
    ig_posts = fetch_instagram_posts(ig_id, page_token)
    ig_demographics = fetch_instagram_demographics(ig_id, page_token)

    store_followers([(today, "facebook", fb_followers), (today, "instagram", ig_followers)])
    store_daily_metrics(fb_daily, platform="facebook")
    store_daily_metrics(ig_daily, platform="instagram")
    store_posts(fb_posts + ig_posts)
    store_demographics(ig_demographics, platform="instagram")

    print(
        f"Meta organic sync complete: Facebook {fb_followers} followers "
        f"({len(fb_posts)} posts), Instagram {ig_followers} followers ({len(ig_posts)} posts)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
