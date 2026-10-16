"""Real, computed competitor "share of voice" - for DOMA's own top Search
Console queries, who else shows up in the top 10 results, how often, and
at what position. This is deliberately NOT a third-party authority score
(domain rating, etc) - Ahrefs' equivalent is blocked account-side (see
README.md), and Google's Custom Search JSON API requires a billing account
linked to the Cloud project even to stay within its free daily quota,
which the user chose not to set up.

Two providers, picked at run time:

1. Brave Search API (used when BRAVE_SEARCH_API_KEY is set in .env).
   GET https://api.search.brave.com/res/v1/web/search with the
   X-Subscription-Token header, q / country=us / count=20. Free plan is
   2,000 queries/month at 1 query/second.

   Budget math: the query pool is DOMA's top non-brand Search Console
   queries (app.BRAND_QUERY_RE removes brand searches), capped at 50. Each
   run checks the 15 queries whose last Brave check is oldest (never-checked
   first), 1.1 s apart. daily_sync.py runs once a day, so that is
   15 x 31 = 465 calls in the longest month, under a quarter of the 2,000
   free calls, and the whole 50-query pool is refreshed every 4 days. On top
   of that a hard monthly cap (MONTHLY_CALL_CAP = 900, counted in the
   serp_api_calls table) stops the script before it can ever reach 2,000,
   even if it is run by hand many times. --limit can raise one run to at
   most 50 queries.

   Per-query results are kept in serp_query_results (latest top 10 per
   query) and serp_competitors is rebuilt from all of them, so the dashboard
   table always covers the whole pool even though each day only refreshes
   part of it.

2. DuckDuckGo HTML results (fallback, no key): the original path, top 20
   queries by clicks in one go. Answers 202 (rate-limited) to every query
   since August 2026, in which case the run fails and the table is left
   alone.

In both paths a run that produces no competitor domains never touches
serp_competitors - the previous snapshot stays (the August bug wiped it).

Usage:
    python scripts/sync_serp_competitors.py                # normal run
    python scripts/sync_serp_competitors.py --limit 30     # bigger Brave batch (max 50)
    python scripts/sync_serp_competitors.py --dry-run      # Brave path, sample JSON, no network
    python scripts/sync_serp_competitors.py --dry-run --sample path.json
"""

from __future__ import annotations

import argparse
import json
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

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_POOL_SIZE = 50          # top non-brand GSC queries kept in rotation
BRAVE_QUERIES_PER_RUN = 15    # daily batch, see budget math above
BRAVE_MAX_PER_RUN = 50        # hard ceiling for --limit
BRAVE_DELAY_SECONDS = 1.1     # free plan allows 1 query/second
MONTHLY_CALL_CAP = 900        # Brave Search gives $5 free credit a month = 1,000 calls at $5/1,000; stay under it
DEFAULT_SAMPLE = ROOT / "scripts" / "fixtures" / "brave_web_search_sample.json"

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


class BraveQuotaError(SerpError):
    """Monthly cap reached - nothing is called, previous data stays."""


# ------------------------------------------------------------------ shared

def _aggregate(serps: dict[str, list[str]], self_domain: str) -> tuple[dict[str, list[int]], dict[str, int]]:
    """serps: query -> ordered list of result domains (top 10)."""
    domain_positions: dict[str, list[int]] = {}
    domain_beats_doma: dict[str, int] = {}
    for domains in serps.values():
        doma_position = next((i + 1 for i, d in enumerate(domains) if self_domain in d), None)
        for i, domain in enumerate(domains):
            if self_domain in domain or _is_noise_domain(domain):
                continue
            position = i + 1
            domain_positions.setdefault(domain, []).append(position)
            if doma_position is None or position < doma_position:
                domain_beats_doma[domain] = domain_beats_doma.get(domain, 0) + 1
    return domain_positions, domain_beats_doma


def _write_competitors(domain_positions: dict[str, list[int]], domain_beats_doma: dict[str, int]) -> None:
    """Replace serp_competitors. An empty result never reaches the DELETE -
    the previous snapshot is kept instead."""
    if not domain_positions:
        raise SerpError("No competitor domains in this run - keeping previous data, not overwriting.")
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


# -------------------------------------------------------------- DuckDuckGo

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


def run_duckduckgo(self_domain: str) -> int:
    queries = top_doma_queries()
    if not queries:
        raise SerpError("No Search Console queries synced yet - run scripts/sync_gsc.py first.")

    serps: dict[str, list[str]] = {}
    for query in queries:
        try:
            serps[query] = fetch_serp(query)
        except SerpError as error:
            print(f"  WARNING: skipped '{query}' ({error})", file=sys.stderr)
            continue
        time.sleep(1.0)  # be polite - one request per second, this is a free public page, not an API

    if not serps:
        # DuckDuckGo rate-limited every single query this run - keep whatever
        # data is already in the table rather than wiping it out.
        raise SerpError("All queries were rate-limited (202) - keeping previous data, not overwriting.")

    domain_positions, domain_beats_doma = _aggregate(serps, self_domain)
    _write_competitors(domain_positions, domain_beats_doma)
    checked = len(serps)
    log_sync(dashboard_app, "serp_competitors", "ok", f"duckduckgo: {checked} queries checked, {len(domain_positions)} competitor domains found")
    print(f"SERP competitor sync complete (DuckDuckGo): {checked} queries checked, {len(domain_positions)} competitor domains found.")
    return 0


# ------------------------------------------------------------------- Brave

def _ensure_brave_tables() -> None:
    with dashboard_app.db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS serp_query_results (
                query TEXT NOT NULL,
                position INTEGER NOT NULL,
                domain TEXT NOT NULL,
                url TEXT,
                provider TEXT NOT NULL,
                checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (query, position)
            );
            CREATE TABLE IF NOT EXISTS serp_query_checks (
                query TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                result_count INTEGER NOT NULL DEFAULT 0,
                checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS serp_api_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                query TEXT NOT NULL,
                status INTEGER,
                called_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


def nonbrand_query_pool(limit: int = BRAVE_POOL_SIZE) -> list[str]:
    brand_re = getattr(dashboard_app, "BRAND_QUERY_RE", None)
    with dashboard_app.db() as con:
        rows = con.execute(
            "SELECT query FROM search_console_queries ORDER BY clicks DESC, impressions DESC"
        ).fetchall()
    pool = [row[0] for row in rows if row[0] and not (brand_re and brand_re.search(row[0]))]
    return pool[:limit]


def pick_batch(pool: list[str], size: int) -> list[str]:
    """Oldest-checked first; never-checked queries go first, in pool order."""
    with dashboard_app.db() as con:
        last = dict(con.execute("SELECT query, checked_at FROM serp_query_checks").fetchall())
    order = {q: i for i, q in enumerate(pool)}
    return sorted(pool, key=lambda q: (last.get(q) or "", order[q]))[:size]


def calls_this_month(provider: str = "brave") -> int:
    with dashboard_app.db() as con:
        row = con.execute(
            "SELECT COUNT(*) FROM serp_api_calls WHERE provider = ? AND strftime('%Y-%m', called_at) = strftime('%Y-%m', 'now')",
            (provider,),
        ).fetchone()
    return int(row[0] or 0)


def _record_call(query: str, status: int | None) -> None:
    with dashboard_app.db() as con:
        con.execute("INSERT INTO serp_api_calls (provider, query, status) VALUES ('brave', ?, ?)", (query, status))


def parse_brave_results(payload: dict) -> list[tuple[str, str]]:
    """[(domain, url), ...] for the organic web results, in rank order, top 10."""
    out: list[tuple[str, str]] = []
    for item in ((payload or {}).get("web") or {}).get("results") or []:
        url = item.get("url") or ""
        domain = urlparse(url).netloc.lower()
        if domain:
            out.append((domain, url))
        if len(out) == 10:
            break
    return out


def fetch_brave(query: str, api_key: str) -> tuple[int, dict]:
    import requests

    response = requests.get(
        BRAVE_SEARCH_URL,
        params={"q": query, "country": "us", "count": 20},
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code == 429:
        return 429, {}
    if response.status_code != 200:
        raise SerpError(f"query '{query}' failed: {response.status_code} {response.text[:200]}")
    return 200, response.json()


def _store_query(query: str, results: list[tuple[str, str]], provider: str) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM serp_query_results WHERE query = ?", (query,))
        for i, (domain, url) in enumerate(results, start=1):
            con.execute(
                "INSERT INTO serp_query_results (query, position, domain, url, provider) VALUES (?, ?, ?, ?, ?)",
                (query, i, domain, url, provider),
            )
        con.execute(
            "INSERT INTO serp_query_checks (query, provider, result_count, checked_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(query) DO UPDATE SET provider = excluded.provider, result_count = excluded.result_count, checked_at = excluded.checked_at",
            (query, provider, len(results)),
        )


def _stored_serps(pool: list[str]) -> dict[str, list[str]]:
    """Latest stored top 10 per query, only for queries still in the pool."""
    wanted = set(pool)
    serps: dict[str, list[str]] = {}
    with dashboard_app.db() as con:
        for query, domain in con.execute("SELECT query, domain FROM serp_query_results ORDER BY query, position"):
            if query in wanted:
                serps.setdefault(query, []).append(domain)
    return serps


def run_brave(self_domain: str, api_key: str, limit: int, sample: Path | None = None) -> int:
    dry_run = sample is not None
    _ensure_brave_tables()

    pool = nonbrand_query_pool()
    if not pool:
        raise SerpError("No non-brand Search Console queries synced yet - run scripts/sync_gsc.py first.")

    size = max(1, min(limit, BRAVE_MAX_PER_RUN))
    if not dry_run:
        remaining = MONTHLY_CALL_CAP - calls_this_month()
        if remaining <= 0:
            raise BraveQuotaError(f"Monthly cap of {MONTHLY_CALL_CAP} Brave calls reached - keeping previous data.")
        size = min(size, remaining)
    batch = pick_batch(pool, size)

    sample_payload = json.loads(sample.read_text(encoding="utf-8")) if dry_run else None
    checked = 0
    for n, query in enumerate(batch):
        if dry_run:
            payload = sample_payload
        else:
            if n:
                time.sleep(BRAVE_DELAY_SECONDS)
            try:
                status, payload = fetch_brave(query, api_key)
            except SerpError as error:
                _record_call(query, None)
                print(f"  WARNING: skipped '{query}' ({error})", file=sys.stderr)
                continue
            _record_call(query, status)
            if status == 429:
                print("  WARNING: Brave answered 429 (rate limit or monthly quota) - stopping this run.", file=sys.stderr)
                break
        results = parse_brave_results(payload)
        if not results:
            # Zero results for one query: keep that query's previous top 10,
            # only mark it checked so the rotation moves on instead of
            # spending a call on it again every day.
            print(f"  WARNING: no web results for '{query}' - keeping its previous snapshot.", file=sys.stderr)
            with dashboard_app.db() as con:
                con.execute(
                    "INSERT INTO serp_query_checks (query, provider, result_count, checked_at) VALUES (?, ?, 0, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(query) DO UPDATE SET result_count = 0, checked_at = excluded.checked_at",
                    (query, "brave-sample" if dry_run else "brave"),
                )
            continue
        _store_query(query, results, "brave-sample" if dry_run else "brave")
        checked += 1

    serps = _stored_serps(pool)
    domain_positions, domain_beats_doma = _aggregate(serps, self_domain)
    if not domain_positions:
        raise SerpError(f"Brave: {checked} queries checked but no competitor domains stored - keeping previous data, not overwriting.")
    _write_competitors(domain_positions, domain_beats_doma)

    detail = (
        f"brave{' (dry-run sample)' if dry_run else ''}: {checked}/{len(batch)} queries refreshed, "
        f"{len(serps)}/{len(pool)} pool queries covered, {len(domain_positions)} competitor domains, "
        f"{calls_this_month()} calls this month"
    )
    log_sync(dashboard_app, "serp_competitors", "ok", detail)
    print(f"SERP competitor sync complete - {detail}.")
    return 0


# -------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ranking share-of-voice sync (Brave Search API, DuckDuckGo fallback).")
    parser.add_argument("--limit", type=int, default=BRAVE_QUERIES_PER_RUN, help=f"Brave queries this run (max {BRAVE_MAX_PER_RUN})")
    parser.add_argument("--dry-run", action="store_true", help="Brave path with a saved sample response, no network calls")
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE, help="sample Brave JSON for --dry-run")
    # daily_sync.py calls main() with no arguments; don't parse its argv.
    if argv is None and __name__ != "__main__":
        argv = []
    args = parser.parse_args(argv)

    env = load_env_file()
    dashboard_app.init_db()

    # self_domain needs to be a real hostname for the domain-vs-domain SERP
    # comparison - GSC_SITE_URL is `sc-domain:...` for a Domain property,
    # which urlparse() can't turn into a hostname at all.
    site_url = env.get("WP_URL") or env.get("GSC_SITE_URL")
    if not site_url:
        raise SerpError("Missing WP_URL (or GSC_SITE_URL) in .env")
    self_domain = urlparse(site_url).netloc or site_url.strip("/")

    if args.dry_run:
        return run_brave(self_domain, api_key="", limit=args.limit, sample=args.sample)

    api_key = (env.get("BRAVE_SEARCH_API_KEY") or "").strip()
    if api_key:
        return run_brave(self_domain, api_key, args.limit)
    return run_duckduckgo(self_domain)


if __name__ == "__main__":
    raise SystemExit(main())
