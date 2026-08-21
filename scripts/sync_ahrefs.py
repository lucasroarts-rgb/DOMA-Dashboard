"""Ahrefs Site Audit issues + competitor comparison.

STATUS (2026-08-21): written but UNVERIFIED end-to-end. Every Ahrefs API v3
call from this account currently returns 401 Unauthorized regardless of
endpoint or API key - confirmed to be an account-side entitlement problem,
not a code/key/network issue (see README.md "Ahrefs" section for the full
diagnosis, including a request trace-id). The `site-audit/issues` request
shape below is copied directly from a real, working example the account
owner pulled from the Ahrefs UI itself, so that part is solid. The
`site-explorer/metrics` and `site-explorer/competing-domains` endpoint
paths are this project's best understanding of Ahrefs' v3 REST API
structure, but have never returned a real response - re-verify both against
current Ahrefs API docs (or trial-and-error against real responses) once
the account's 401 is resolved, before trusting this in production.

Requires AHREFS_API_KEY + AHREFS_PROJECT_ID in .env.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402

API_BASE = "https://api.ahrefs.com/v3"
REQUEST_TIMEOUT = 30
MAX_COMPETITORS = 5


class AhrefsError(RuntimeError):
    pass


def _get(path: str, api_key: str, params: dict) -> dict:
    import requests

    response = requests.get(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise AhrefsError(f"{path} failed: {response.status_code} {response.text[:200]}")
    return response.json()


def sync_site_audit_issues(api_key: str, project_id: str) -> int:
    today = date.today()
    last_week = today - timedelta(days=7)
    data = _get(
        "/site-audit/issues",
        api_key,
        {
            "project_id": project_id,
            "date": f"{today.isoformat()}T00:00:00Z",
            "date_compared": f"{last_week.isoformat()}T00:00:00Z",
        },
    )
    issues = data.get("issues", data if isinstance(data, list) else [])
    rows = [
        {
            "issue": item.get("title") or item.get("issue", ""),
            "severity": item.get("severity", ""),
            "affected_pages": int(item.get("pages", item.get("affected_pages", 0)) or 0),
            "change_vs_prev": int(item.get("change", item.get("change_vs_prev", 0)) or 0),
        }
        for item in issues
    ]
    with dashboard_app.db() as con:
        con.execute("DELETE FROM ahrefs_site_audit_issues")
        con.executemany(
            """
            INSERT INTO ahrefs_site_audit_issues (issue, severity, affected_pages, change_vs_prev, checked_at)
            VALUES (:issue, :severity, :affected_pages, :change_vs_prev, CURRENT_TIMESTAMP)
            """,
            rows,
        )
    return len(rows)


def _domain_metrics(domain: str, api_key: str) -> dict:
    data = _get(
        "/site-explorer/metrics",
        api_key,
        {"target": domain, "date": date.today().isoformat(), "mode": "domain"},
    )
    metrics = data.get("metrics", data)
    return {
        "domain_rating": metrics.get("domain_rating"),
        "organic_traffic": metrics.get("org_traffic"),
        "organic_keywords": metrics.get("org_keywords"),
        "backlinks": metrics.get("backlinks"),
        "referring_domains": metrics.get("refdomains"),
    }


def sync_competitors(api_key: str, site_url: str) -> int:
    from urllib.parse import urlparse

    self_domain = urlparse(site_url).netloc or site_url.strip("/")

    competing = _get(
        "/site-explorer/competing-domains",
        api_key,
        {"target": self_domain, "date": date.today().isoformat(), "limit": MAX_COMPETITORS},
    )
    competitor_domains = [
        item.get("domain") for item in competing.get("competing_domains", competing.get("domains", [])) if item.get("domain")
    ][:MAX_COMPETITORS]

    rows = []
    self_metrics = _domain_metrics(self_domain, api_key)
    rows.append({"domain": self_domain, "role": "self", **self_metrics})
    for domain in competitor_domains:
        try:
            rows.append({"domain": domain, "role": "competitor", **_domain_metrics(domain, api_key)})
        except AhrefsError as error:
            print(f"  WARNING: skipped competitor {domain} ({error})", file=sys.stderr)

    with dashboard_app.db() as con:
        con.execute("DELETE FROM ahrefs_domains")
        con.executemany(
            """
            INSERT INTO ahrefs_domains
                (domain, role, domain_rating, organic_traffic, organic_keywords, backlinks, referring_domains, checked_at)
            VALUES (:domain, :role, :domain_rating, :organic_traffic, :organic_keywords, :backlinks, :referring_domains, CURRENT_TIMESTAMP)
            """,
            rows,
        )
    return len(rows)


def main() -> int:
    env = load_env_file()
    dashboard_app.init_db()

    api_key = env.get("AHREFS_API_KEY")
    project_id = env.get("AHREFS_PROJECT_ID")
    site_url = env.get("GSC_SITE_URL")
    if not api_key or not project_id or not site_url:
        raise AhrefsError("Missing AHREFS_API_KEY, AHREFS_PROJECT_ID, or GSC_SITE_URL in .env")

    issues_count = sync_site_audit_issues(api_key, project_id)
    print(f"  {issues_count} Site Audit issue types synced")

    domains_count = sync_competitors(api_key, site_url)
    print(f"  {domains_count} domains synced (self + competitors)")

    log_sync(dashboard_app, "ahrefs", "ok", f"{issues_count} issue types, {domains_count} domains")
    print(f"Ahrefs sync complete: {issues_count} issue types, {domains_count} domains.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
