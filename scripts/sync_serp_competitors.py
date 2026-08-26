"""Real, computed competitor "share of voice" - for DOMA's own top Search
Console queries, who else shows up in the top 10 results, how often, and
at what position. This is deliberately NOT a third-party authority score
(domain rating, etc) - Ahrefs' equivalent is blocked account-side (see
README.md), and Google's Custom Search JSON API requires a billing account
linked to the Cloud project even to stay within its free daily quota,
which the user chose not to set up.

Uses DuckDuckGo's public HTML results endpoint (html.duckduckgo.com/html/)
instead - no API key, no billing account, a real search engine's top 10 for
each query. Not Google's own index, so treat this as a real but
approximate competitive signal, not a Google-ranking guarantee. Kept to a
light, identified, low-frequency request pattern (one request per tracked
query, ~20/day, real User-Agent) - this reads DuckDuckGo's own public,
non-JS results page as intended, not working around any bot/CAPTCHA wall.

No API key required.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402

SEARCH_URL = "https://html.duckduckgo.com/html/"
MAX_QUERIES = 20
REQUEST_TIMEOUT = 20
RESULT_LINK_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"')

# Big general platforms show up for almost any B2B query and aren't real
# competitors (a LinkedIn company page, a YouTube video, a Facebook group) -
# they'd otherwise dominate a "share of voice" table meant to answer "which
# businesses are we actually losing search visibility to."
NON_COMPETITOR_DOMAINS = {
    "facebook.com", "linkedin.com", "youtube.com", "instagram.com",
    "twitter.com", "x.com", "pinterest.com", "tiktok.com", "reddit.com",
    "quora.com", "wikipedia.org", "amazon.com", "indeed.com", "glassdoor.com",
    "yelp.com", "google.com", "apple.com", "medium.com",
}


def _is_noise_domain(domain: str) -> bool:
    bare = domain.lower().removeprefix("www.")
    return any(bare == d or bare.endswith("." + d) for d in NON_COMPETITOR_DOMAINS)


class SerpError(RuntimeError):
    pass


def top_doma_queries() -> list[str]:
    with dashboard_app.db() as con:
        rows = con.execute(
            "SELECT query FROM search_console_queries ORDER BY clicks DESC LIMIT ?", (MAX_QUERIES,)
        ).fetchall()
    return [row[0] for row in rows]


def _resolve_result_url(raw_href: str) -> str:
    """DuckDuckGo's HTML results wrap the real URL as
    //duckduckgo.com/l/?uddg=<url-encoded-target>&rut=... - unwrap it."""
    href = raw_href if raw_href.startswith("http") else f"https:{raw_href}"
    parsed = urlparse(href)
    qs = parse_qs(parsed.query)
    target = qs.get("uddg", [None])[0]
    return unquote(target) if target else href


def fetch_serp(query: str) -> list[str]:
    import requests

    response = requests.get(
        SEARCH_URL,
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) doma-dashboard-serp-check"},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise SerpError(f"query '{query}' failed: {response.status_code}")

    hrefs = RESULT_LINK_RE.findall(response.text)
    domains = []
    for href in hrefs[:10]:
        target = _resolve_result_url(href)
        domain = urlparse(target).netloc
        if domain:
            domains.append(domain)
    return domains


def main() -> int:
    env = load_env_file()
    dashboard_app.init_db()

    # self_domain needs to be a real hostname for the domain-vs-domain SERP
    # comparison below - GSC_SITE_URL is `sc-domain:...` for a Domain
    # property, which urlparse() can't turn into a hostname at all.
    site_url = env.get("WP_URL") or env.get("GSC_SITE_URL")
    if not site_url:
        raise SerpError("Missing WP_URL (or GSC_SITE_URL) in .env")
    self_domain = urlparse(site_url).netloc or site_url.strip("/")

    queries = top_doma_queries()
    if not queries:
        raise SerpError("No Search Console queries synced yet - run scripts/sync_gsc.py first.")

    domain_positions: dict[str, list[int]] = {}
    domain_beats_doma: dict[str, int] = {}
    checked = 0
    for query in queries:
        try:
            domains = fetch_serp(query)
        except SerpError as error:
            print(f"  WARNING: skipped '{query}' ({error})", file=sys.stderr)
            continue
        checked += 1

        doma_position = next((i + 1 for i, d in enumerate(domains) if self_domain in d), None)
        for i, domain in enumerate(domains):
            if self_domain in domain or _is_noise_domain(domain):
                continue
            position = i + 1
            domain_positions.setdefault(domain, []).append(position)
            if doma_position is None or position < doma_position:
                domain_beats_doma[domain] = domain_beats_doma.get(domain, 0) + 1
        time.sleep(1.0)  # be polite - one request per second, this is a free public page, not an API

    if checked == 0:
        # DuckDuckGo rate-limited every single query this run - keep whatever
        # data is already in the table rather than wiping it out with an
        # empty result. A partial run (some queries got through) still
        # replaces the table below, same as before.
        raise SerpError("All queries were rate-limited (202) - keeping previous data, not overwriting.")

    with dashboard_app.db() as con:
        con.execute("DELETE FROM serp_competitors")
        for domain, positions in domain_positions.items():
            con.execute(
                """
                INSERT INTO serp_competitors (domain, appearances, avg_position, best_position, queries_beating_doma, checked_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (domain, len(positions), round(sum(positions) / len(positions), 1), min(positions), domain_beats_doma.get(domain, 0)),
            )

    log_sync(dashboard_app, "serp_competitors", "ok", f"{checked} queries checked, {len(domain_positions)} competitor domains found")
    print(f"SERP competitor sync complete: {checked} queries checked, {len(domain_positions)} competitor domains found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
