"""Weekly PageSpeed Insights (mobile) for each tracked competitor's homepage.

Same API and key as sync_pagespeed.py (PAGESPEED_API_KEY). A competitor's
homepage barely changes week to week and each check takes 20-60 seconds,
so a URL checked in the last SKIP_IF_CHECKED_DAYS days is skipped: the
daily sync runs this every day, but each homepage is tested about weekly.
The competitor list comes from sync_competitors_content.COMPETITORS so the
two stay in step.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402
from scripts.sync_competitors_content import COMPETITORS  # noqa: E402
from scripts.sync_pagespeed import PageSpeedError, fetch_report, parse_report  # noqa: E402

SKIP_IF_CHECKED_DAYS = 6


def due_competitors() -> list[dict[str, str]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SKIP_IF_CHECKED_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with dashboard_app.db() as con:
        recent = {row[0] for row in con.execute("SELECT url FROM competitor_pagespeed WHERE checked_at >= ?", (cutoff,))}
    return [c for c in COMPETITORS if c["url"].rstrip("/") + "/" not in recent]


def main() -> int:
    env = load_env_file()
    dashboard_app.init_db()
    api_key = env.get("PAGESPEED_API_KEY")
    if not api_key:
        raise PageSpeedError("Missing PAGESPEED_API_KEY in .env")

    due = due_competitors()
    if not due:
        print(f"Competitor PageSpeed: every homepage checked in the last {SKIP_IF_CHECKED_DAYS} days, skipping.")
        return 0

    checked = 0
    for competitor in due:
        url = competitor["url"].rstrip("/") + "/"
        try:
            result = parse_report(url, fetch_report(url, api_key))
        except PageSpeedError as error:
            print(f"  WARNING: skipped {competitor['name']} ({error})", file=sys.stderr)
            continue
        with dashboard_app.db() as con:
            con.execute(
                "INSERT OR REPLACE INTO competitor_pagespeed (competitor_name, url, performance_score, lcp_ms, checked_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (competitor["name"], url, result["performance_score"], result["lcp_ms"]),
            )
        checked += 1
        print(f"  {competitor['name']}: performance {result['performance_score']}, LCP {result['lcp_ms']} ms")

    log_sync(dashboard_app, "competitor_pagespeed", "ok" if checked else "error", f"{checked} of {len(due)} homepages checked")
    print(f"Competitor PageSpeed complete: {checked} of {len(due)} homepages checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
