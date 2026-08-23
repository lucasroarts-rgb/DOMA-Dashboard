"""Two free, no-key ways to inspect tracked competitors beyond SEO/content:

1. Tech/marketing stack fingerprint - fetch each competitor's own public
   homepage HTML and match known signatures (WordPress, HubSpot, Klaviyo,
   Meta Pixel, etc), the same technique a Wappalyzer-style browser
   extension uses. Reveals what marketing tools a competitor invests in.
2. Wayback Machine history - web.archive.org's public CDX API (no key)
   tells you how many times, and how recently, a competitor's homepage
   has been archived - a rough proxy for how actively they maintain their
   site, not a direct measure of traffic or revenue.

Uses the same COMPETITORS list as sync_competitors_content.py - edit that
file's list to add/remove tracked competitors, both scripts share it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402
from scripts.sync_competitors_content import COMPETITORS  # noqa: E402

REQUEST_TIMEOUT = 20

# (display name, substring(s) to match in lowercased HTML - any match counts)
TECH_SIGNATURES = [
    ("WordPress", ["wp-content", "wp-json"]),
    ("Shopify", ["cdn.shopify.com"]),
    ("Elementor", ["elementor"]),
    ("HubSpot", ["hs-scripts", "hubspot", "hs-analytics"]),
    ("Klaviyo", ["klaviyo"]),
    ("Mailchimp", ["mailchimp", "list-manage.com"]),
    ("ActiveCampaign", ["activecampaign"]),
    ("ConvertKit", ["convertkit"]),
    ("Google Analytics / GTM", ["googletagmanager.com", "gtag("]),
    ("Meta Pixel", ["connect.facebook.net", "fbq("]),
    ("TikTok Pixel", ["analytics.tiktok.com"]),
    ("LinkedIn Insight Tag", ["snap.licdn.com"]),
    ("Intercom", ["intercom"]),
    ("Drift chat", ["drift.com", "driftt.com"]),
    ("Tawk.to chat", ["tawk.to"]),
    ("Stripe", ["js.stripe.com"]),
    ("ClickFunnels", ["clickfunnels"]),
    ("Kajabi", ["kajabi"]),
    ("Thinkific", ["thinkific"]),
    ("Teachable", ["teachable"]),
    ("Circle community", ["circle.so"]),
    ("Skool community", ["skool.com"]),
    ("Calendly", ["calendly"]),
    ("Typeform", ["typeform"]),
]


class CompetitorIntelError(RuntimeError):
    pass


def fetch_tech_signals(url: str) -> list[str]:
    import requests

    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) doma-dashboard-competitor-check"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as error:
        raise CompetitorIntelError(f"{url} errored: {error}") from error
    if response.status_code != 200:
        raise CompetitorIntelError(f"{url} returned {response.status_code}")
    text = response.text.lower()
    return [name for name, needles in TECH_SIGNATURES if any(needle in text for needle in needles)]


def fetch_wayback_history(domain: str) -> dict:
    import requests
    from datetime import date, timedelta

    try:
        response = requests.get(
            "http://web.archive.org/cdx/search/cdx",
            params={"url": domain, "output": "json", "collapse": "timestamp:8", "limit": 5000},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) doma-dashboard-competitor-check"},
            timeout=60,
        )
    except requests.exceptions.RequestException as error:
        raise CompetitorIntelError(f"Wayback CDX for {domain} errored: {error}") from error
    if response.status_code != 200:
        raise CompetitorIntelError(f"Wayback CDX for {domain} returned {response.status_code}")
    rows = response.json()
    if len(rows) <= 1:  # first row is the header
        return {"total_snapshots": 0, "first_snapshot": None, "last_snapshot": None, "snapshots_last_90d": 0}

    timestamps = [row[1] for row in rows[1:]]  # YYYYMMDDhhmmss strings, already sorted by the API
    cutoff = (date.today() - timedelta(days=90)).strftime("%Y%m%d")
    recent = sum(1 for ts in timestamps if ts[:8] >= cutoff)
    return {
        "total_snapshots": len(timestamps),
        "first_snapshot": timestamps[0][:8],
        "last_snapshot": timestamps[-1][:8],
        "snapshots_last_90d": recent,
    }


def main() -> int:
    load_env_file()
    dashboard_app.init_db()

    tech_rows = []
    wayback_rows = []
    for competitor in COMPETITORS:
        name, url = competitor["name"], competitor["url"]
        domain = urlparse(url).netloc

        try:
            signals = fetch_tech_signals(url)
            tech_rows.append({"competitor_name": name, "domain": domain, "signals": json.dumps(signals)})
            print(f"  {name}: {', '.join(signals) if signals else '(no known signatures matched)'}")
        except CompetitorIntelError as error:
            print(f"  WARNING: tech fingerprint skipped for {name} ({error})", file=sys.stderr)

        try:
            history = fetch_wayback_history(domain)
            wayback_rows.append({"competitor_name": name, "domain": domain, **history})
            print(f"  {name}: {history['total_snapshots']} Wayback snapshots, last {history['last_snapshot']}")
        except CompetitorIntelError as error:
            print(f"  WARNING: Wayback history skipped for {name} ({error})", file=sys.stderr)

    if tech_rows:
        with dashboard_app.db() as con:
            con.execute("DELETE FROM competitor_tech_stack")
            con.executemany(
                "INSERT INTO competitor_tech_stack (competitor_name, domain, signals, checked_at) "
                "VALUES (:competitor_name, :domain, :signals, CURRENT_TIMESTAMP)",
                tech_rows,
            )

    if wayback_rows:
        with dashboard_app.db() as con:
            con.execute("DELETE FROM competitor_wayback_history")
            con.executemany(
                """
                INSERT INTO competitor_wayback_history
                    (competitor_name, domain, total_snapshots, first_snapshot, last_snapshot, snapshots_last_90d, checked_at)
                VALUES (:competitor_name, :domain, :total_snapshots, :first_snapshot, :last_snapshot, :snapshots_last_90d, CURRENT_TIMESTAMP)
                """,
                wayback_rows,
            )

    log_sync(dashboard_app, "competitor_intel", "ok", f"{len(tech_rows)} tech fingerprints, {len(wayback_rows)} Wayback histories")
    print(f"Competitor intel sync complete: {len(tech_rows)} tech fingerprints, {len(wayback_rows)} Wayback histories.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
