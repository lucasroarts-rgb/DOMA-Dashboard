"""Single daily automation entry point: sync every data source, rebuild the
static site, and publish it to GitHub Pages via git.

Each source sync is isolated and resilient - if one source fails (bad
credentials, API outage), the others still run and the site still
publishes with whatever data is available. Failures are recorded to
sync_log (see app.py) so the dashboard can show "last synced" / "error"
per source instead of failing silently.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.generate_public_site import main as generate_public_site  # noqa: E402
from scripts.sync_ga4 import main as sync_ga4  # noqa: E402
from scripts.sync_ghl import main as sync_ghl  # noqa: E402
from scripts.sync_gsc import main as sync_gsc  # noqa: E402
from scripts.sync_meta_organic import main as sync_meta_organic  # noqa: E402
from scripts.sync_seo_audit import main as sync_seo_audit  # noqa: E402
from scripts.sync_pagespeed import main as sync_pagespeed  # noqa: E402
from scripts.sync_ahrefs import main as sync_ahrefs  # noqa: E402
from scripts.sync_competitors_content import main as sync_competitors_content  # noqa: E402
from scripts.sync_serp_competitors import main as sync_serp_competitors  # noqa: E402
from scripts.sync_competitor_intel import main as sync_competitor_intel  # noqa: E402
from scripts.send_seo_digest import main as send_seo_digest  # noqa: E402
from scripts.send_task_digest import main as send_task_digest  # noqa: E402
from scripts.sync_ebook_links import main as sync_ebook_links  # noqa: E402

LOGS_DIR = ROOT / "logs"


class AutomationError(RuntimeError):
    pass


def run_git(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", "-C", str(ROOT), *args], text=True, capture_output=True)
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise AutomationError(f"Git command failed: git {' '.join(args)}\n{detail}")
    return result


def prepare_git() -> None:
    if not (ROOT / ".git").exists():
        raise AutomationError(
            "This folder is not a git repository yet. Run 'git init' and connect it to "
            "GitHub before enabling AUTO_PUBLISH - see README.md."
        )
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if branch == "HEAD":
        raise AutomationError("Git is in detached HEAD mode - fix that before the scheduled sync.")

    run_git(["fetch", "origin"], check=False)
    pull = run_git(["pull", "--rebase", "--autostash", "origin", branch], check=False)
    if pull.returncode != 0:
        detail = pull.stderr.strip() or pull.stdout.strip()
        raise AutomationError("Could not sync the local folder with GitHub before publishing.\n" + detail)


def publish_docs(message: str) -> dict[str, object]:
    run_git(["add", "docs", ".gitignore"])
    diff = run_git(["diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 0:
        return {"changed": False, "pushed": False}

    run_git(["commit", "-m", message])
    push = run_git(["push", "origin", "HEAD"], check=False)
    if push.returncode != 0:
        run_git(["fetch", "origin"])
        branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        rebase = run_git(["pull", "--rebase", "--autostash", "origin", branch], check=False)
        if rebase.returncode != 0:
            detail = rebase.stderr.strip() or rebase.stdout.strip()
            raise AutomationError("Data was committed, but GitHub changed at the same time.\n" + detail)
        run_git(["push", "origin", "HEAD"])
    return {"changed": True, "pushed": True}


def run_source(name: str, func) -> str:
    print(f"Syncing {name}...")
    try:
        func()
        return "ok"
    except Exception as error:  # noqa: BLE001 - deliberately resilient per source
        print(f"WARNING: {name} sync skipped ({error})", file=sys.stderr)
        return f"skipped: {error}"


def main() -> int:
    dashboard_app.init_db()
    from scripts.env_utils import load_env_file

    env = {**os.environ, **load_env_file()}
    auto_publish = str(env.get("AUTO_PUBLISH", "true")).strip().lower() != "false"

    print("")
    print("DOMA daily automation")
    print("----------------------")
    print(f"Started at: {datetime.now().isoformat(timespec='seconds')}")
    print("")

    if auto_publish:
        prepare_git()

    statuses = {
        "gsc": run_source("Google Search Console", sync_gsc),
        "ga4": run_source("Google Analytics (GA4)", sync_ga4),
        "ghl": run_source("GoHighLevel", sync_ghl),
        "meta_organic": run_source("Facebook/Instagram (organic)", sync_meta_organic),
        "seo_audit": run_source("On-page SEO audit", sync_seo_audit),
        "pagespeed": run_source("PageSpeed Insights", sync_pagespeed),
        "ahrefs": run_source("Ahrefs (Site Audit + competitors)", sync_ahrefs),
        "competitors_content": run_source("Competitor content (sitemaps)", sync_competitors_content),
        "serp_competitors": run_source("SERP competitor ranking", sync_serp_competitors),
        "competitor_intel": run_source("Competitor tech stack + Wayback history", sync_competitor_intel),
        "ebook_links": run_source("New ebook links (Useful Links)", sync_ebook_links),
    }

    statuses["seo_digest_email"] = run_source("SEO email digest", send_seo_digest)
    statuses["task_digest_email"] = run_source("Task digest email (per-person open tickets)", send_task_digest)

    print("Generating the static site...")
    if generate_public_site() != 0:
        raise AutomationError("The public site generator failed.")

    git_result: dict[str, object] = {"changed": False, "pushed": False, "skipped": True}
    if auto_publish:
        git_result = publish_docs(f"Daily dashboard update {date.today().isoformat()}")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / ("daily_sync_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    log_path.write_text(
        json.dumps(
            {
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "sources": statuses,
                "git": git_result,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("")
    print("Daily automation completed.")
    print(json.dumps(statuses, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AutomationError as exc:
        print("")
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
