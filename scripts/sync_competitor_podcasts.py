"""Latest podcast episodes of competitors that publish a public podcast RSS.

Feeds were found on 2026-10-15 through the public iTunes Search API
(feedUrl of the show the competitor itself publishes). Front Office Rocks,
Kwikly and DentalPost had no podcast listed there, so they are not here.
Only the episode title, date and link are stored, newest EPISODES_KEPT per
competitor; nothing is downloaded.
"""

from __future__ import annotations

import sys
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402

PODCAST_FEEDS = [
    {"name": "Dental A Team", "feed": "https://rss.libsyn.com/shows/166040/destinations/1103258.xml"},
    {"name": "AADOM", "feed": "https://feeds.simplecast.com/O4WXMiFQ"},
    {"name": "Dental Intelligence", "feed": "https://fast.wistia.com/channels/zxtfbrehye/rss"},
    {"name": "Dental Claim Support", "feed": "https://api.riverside.com/hosting/dS5JkcQ3.rss"},
    {"name": "Weave", "feed": "https://anchor.fm/s/97195130/podcast/rss"},
]
EPISODES_KEPT = 5


def fetch_episodes(feed_url: str) -> list[tuple[str, str | None, str]]:
    """(title, published ISO timestamp, link) of the newest episodes."""
    import requests

    response = requests.get(feed_url, timeout=30, headers={"User-Agent": "doma-dashboard-sync"})
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    episodes = []
    seen: set[str] = set()
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not link or link in seen:
            # AADOM's feed gives every episode the show page as <link>; the
            # audio file is the only per-episode URL there
            enclosure = item.find("enclosure")
            link = enclosure.get("url", "") if enclosure is not None else ""
        seen.add(link)
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").isoformat()
        except (TypeError, ValueError):
            published = None
        if title and link:
            episodes.append((title, published, link))
    episodes.sort(key=lambda e: e[1] or "", reverse=True)
    return episodes[:EPISODES_KEPT]


def main() -> int:
    load_env_file()
    dashboard_app.init_db()
    stored = 0
    failed = []
    for show in PODCAST_FEEDS:
        try:
            episodes = fetch_episodes(show["feed"])
        except Exception as error:  # noqa: BLE001 - one dead feed must not stop the others
            failed.append(show["name"])
            print(f"  WARNING: {show['name']} feed skipped ({error})", file=sys.stderr)
            continue
        if not episodes:
            continue
        with dashboard_app.db() as con:
            con.execute("DELETE FROM competitor_podcast_episodes WHERE competitor_name = ?", (show["name"],))
            con.executemany(
                "INSERT OR REPLACE INTO competitor_podcast_episodes (competitor_name, title, published_at, url, synced_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                [(show["name"], title, published, link) for title, published, link in episodes],
            )
        stored += len(episodes)
        print(f"  {show['name']}: {len(episodes)} episodes, newest {episodes[0][1]}")

    detail = f"{stored} episodes from {len(PODCAST_FEEDS) - len(failed)} feeds" + (f", failed: {', '.join(failed)}" if failed else "")
    log_sync(dashboard_app, "competitor_podcasts", "ok" if stored else "error", detail)
    print(f"Competitor podcasts complete: {detail}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
