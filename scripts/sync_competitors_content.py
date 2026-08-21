"""Tracks new content published by named DOMA competitors, via their public
sitemaps - no paid API, same technique already used for DOMA's own site
(sitemap_utils.py), generalized to handle arbitrary (non-WordPress) sites.

Competitor list is manually curated, not auto-discovered (see
sync_serp_competitors.py for the auto-discovered "who ranks alongside us"
view - a different, complementary signal). Add/remove entries in
COMPETITORS below as the list changes.

dalefoundation.org was considered but is excluded: its /sitemap.xml serves
a bot-detection "Client Challenge" page instead of real sitemap content -
respecting that rather than trying to work around it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts import sitemap_utils  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402

COMPETITORS = [
    {"name": "AADOM", "url": "https://www.dentalmanagers.com"},
    {"name": "Dental A Team", "url": "https://www.thedentalateam.com"},
]


class CompetitorSyncError(RuntimeError):
    pass


def sync_competitor(name: str, url: str) -> int:
    entries = sitemap_utils.fetch_all_urls_generic(url)
    if not entries:
        print(f"  WARNING: no sitemap entries found for {name} ({url})", file=sys.stderr)
        return 0

    from urllib.parse import urlparse

    domain = urlparse(url).netloc

    new_count = 0
    with dashboard_app.db() as con:
        for page_url, lastmod in entries:
            existing = con.execute("SELECT 1 FROM competitor_pages WHERE url = ?", (page_url,)).fetchone()
            if existing:
                continue
            con.execute(
                "INSERT INTO competitor_pages (competitor_name, domain, url, lastmod, first_seen_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (name, domain, page_url, lastmod),
            )
            new_count += 1
    return new_count


def main() -> int:
    load_env_file()
    dashboard_app.init_db()

    total_new = 0
    for competitor in COMPETITORS:
        new_count = sync_competitor(competitor["name"], competitor["url"])
        print(f"  {competitor['name']}: {new_count} new page(s)")
        total_new += new_count

    log_sync(dashboard_app, "competitor_content", "ok", f"{total_new} new pages across {len(COMPETITORS)} competitors")
    print(f"Competitor content sync complete: {total_new} new pages found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
