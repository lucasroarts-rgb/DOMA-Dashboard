"""Google PageSpeed Insights (Lighthouse) for the homepage plus the site's
highest-traffic pages (from GA4's own top_pages table - no point testing
pages nobody visits). Mobile strategy only: it's the harder threshold and
the one Google uses for mobile-first indexing, and desktop tends to score
near-perfect regardless on this site.

Requires PAGESPEED_API_KEY in .env - the public no-key quota is shared
globally and can hit 0/day; enable "PageSpeed Insights API" on the same
Google Cloud project used for GA4/GSC and create an API key (Credentials >
Create Credentials > API key), no OAuth/service-account needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402

API_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
MAX_PAGES = 7
REQUEST_TIMEOUT = 120


class PageSpeedError(RuntimeError):
    pass


def urls_to_test(site_url: str) -> list[str]:
    with dashboard_app.db() as con:
        rows = con.execute(
            "SELECT page_path FROM ga4_top_pages ORDER BY sessions DESC LIMIT ?", (MAX_PAGES,)
        ).fetchall()
    from urllib.parse import urljoin

    urls = [site_url]
    for (path,) in rows:
        urls.append(urljoin(site_url, path))
    seen: set[str] = set()
    deduped = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def fetch_report(url: str, api_key: str) -> dict:
    try:
        import requests
    except ImportError as error:
        raise PageSpeedError("requests is not installed. Run: pip install -r requirements.txt") from error

    try:
        response = requests.get(
            API_URL,
            params={
                "url": url,
                "strategy": "mobile",
                "category": ["performance", "accessibility", "best-practices", "seo"],
                "key": api_key,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as error:
        raise PageSpeedError(f"PageSpeed request errored for {url}: {error}") from error
    if response.status_code != 200:
        raise PageSpeedError(f"PageSpeed request failed for {url}: {response.status_code} {response.text[:200]}")
    return response.json()


def parse_report(url: str, report: dict) -> dict:
    lh = report.get("lighthouseResult", {})
    categories = lh.get("categories", {})
    audits = lh.get("audits", {})

    def score(cat: str) -> int | None:
        c = categories.get(cat)
        return round(c["score"] * 100) if c and c.get("score") is not None else None

    def metric_ms(audit_id: str) -> float | None:
        value = audits.get(audit_id, {}).get("numericValue")
        return round(value, 1) if value is not None else None

    console_errors = []
    errors_audit = audits.get("errors-in-console", {})
    for item in (errors_audit.get("details", {}) or {}).get("items", [])[:5]:
        desc = str(item.get("description", ""))[:200]
        if desc:
            console_errors.append(desc)

    opportunities = []
    for audit_id, audit in audits.items():
        details = audit.get("details", {})
        if details.get("type") == "opportunity" and audit.get("score") is not None and audit["score"] < 0.9:
            savings = details.get("overallSavingsMs", 0)
            opportunities.append({"title": audit.get("title", audit_id), "savings_ms": savings})
    opportunities.sort(key=lambda o: o["savings_ms"], reverse=True)

    return {
        "url": url,
        "performance_score": score("performance"),
        "accessibility_score": score("accessibility"),
        "best_practices_score": score("best-practices"),
        "seo_score": score("seo"),
        "lcp_ms": metric_ms("largest-contentful-paint"),
        "cls": audits.get("cumulative-layout-shift", {}).get("numericValue"),
        "tbt_ms": metric_ms("total-blocking-time"),
        "fcp_ms": metric_ms("first-contentful-paint"),
        "speed_index_ms": metric_ms("speed-index"),
        "console_errors": console_errors,
        "top_opportunities": opportunities[:6],
    }


def store(rows: list[dict]) -> None:
    import json

    with dashboard_app.db() as con:
        con.executemany(
            """
            INSERT INTO pagespeed_audit
                (url, strategy, performance_score, accessibility_score, best_practices_score, seo_score,
                 lcp_ms, cls, tbt_ms, fcp_ms, speed_index_ms, console_errors, top_opportunities, checked_at)
            VALUES (:url, 'mobile', :performance_score, :accessibility_score, :best_practices_score, :seo_score,
                    :lcp_ms, :cls, :tbt_ms, :fcp_ms, :speed_index_ms, :console_errors, :top_opportunities, CURRENT_TIMESTAMP)
            ON CONFLICT(url, strategy) DO UPDATE SET
                performance_score = excluded.performance_score,
                accessibility_score = excluded.accessibility_score,
                best_practices_score = excluded.best_practices_score,
                seo_score = excluded.seo_score,
                lcp_ms = excluded.lcp_ms,
                cls = excluded.cls,
                tbt_ms = excluded.tbt_ms,
                fcp_ms = excluded.fcp_ms,
                speed_index_ms = excluded.speed_index_ms,
                console_errors = excluded.console_errors,
                top_opportunities = excluded.top_opportunities,
                checked_at = CURRENT_TIMESTAMP
            """,
            [
                {**row, "console_errors": json.dumps(row["console_errors"]), "top_opportunities": json.dumps(row["top_opportunities"])}
                for row in rows
            ],
        )


def main() -> int:
    env = load_env_file()
    dashboard_app.init_db()

    api_key = env.get("PAGESPEED_API_KEY")
    site_url = env.get("GSC_SITE_URL")
    if not api_key or not site_url:
        raise PageSpeedError("Missing PAGESPEED_API_KEY or GSC_SITE_URL in .env")

    urls = urls_to_test(site_url)
    results = []
    for url in urls:
        try:
            report = fetch_report(url, api_key)
            results.append(parse_report(url, report))
            print(f"  checked {url} - performance {results[-1]['performance_score']}")
        except PageSpeedError as error:
            print(f"  WARNING: skipped {url} ({error})", file=sys.stderr)

    if not results:
        raise PageSpeedError("No pages could be checked - see warnings above.")

    store(results)
    log_sync(dashboard_app, "pagespeed", "ok", f"{len(results)} pages checked")
    print(f"PageSpeed audit complete: {len(results)} pages checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
