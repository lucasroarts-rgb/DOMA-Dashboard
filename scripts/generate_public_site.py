"""Build the static GitHub Pages export in docs/ from the same summary
functions the local dashboard uses (single source of truth - see app.py).

The exported data.js only ever contains aggregated counts (search totals,
GA4 totals, GHL lead counts by day/source) - never individual lead PII -
so it is safe to publish to a public GitHub Pages site.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
DOCS_DIR = ROOT / "docs"

sys.path.insert(0, str(ROOT))
import app as dashboard_app  # noqa: E402


def clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


RANGE_OPTIONS = [7, 15, 30, 90, 180]


def main() -> int:
    dashboard_app.init_db()
    dashboards = {}
    for days in RANGE_OPTIONS:
        start_date, end_date = dashboard_app.default_date_range(days)
        dashboards[str(days)] = dashboard_app.dashboard_with_comparison(start_date, end_date)

    default_days = 90
    payload = clean_json(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "default_range": default_days,
            "dashboards": dashboards,
            "dashboard": dashboards[str(default_days)],
        }
    )

    if DOCS_DIR.exists():
        shutil.rmtree(DOCS_DIR)
    DOCS_DIR.mkdir(parents=True)

    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    index_html = index_html.replace("/static/styles.css", "styles.css")
    index_html = index_html.replace("/static/dashboard.js", "dashboard.js")
    index_html = index_html.replace("/static/firebase-team-sync.js", "firebase-team-sync.js")
    index_html = index_html.replace("/static/assets/", "assets/")
    (DOCS_DIR / "index.html").write_text(index_html, encoding="utf-8")
    (DOCS_DIR / "styles.css").write_text(
        (STATIC_DIR / "styles.css").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (DOCS_DIR / "dashboard.js").write_text(
        (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (DOCS_DIR / "firebase-team-sync.js").write_text(
        (STATIC_DIR / "firebase-team-sync.js").read_text(encoding="utf-8"), encoding="utf-8"
    )
    if (STATIC_DIR / "assets").exists():
        shutil.copytree(STATIC_DIR / "assets", DOCS_DIR / "assets")

    json_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    (DOCS_DIR / "data.js").write_text("window.DOMA_STATIC_DATA=" + json_text + ";\n", encoding="utf-8")
    (DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")

    summary = {
        "generated_at": payload["generated_at"],
        "search_console_clicks": payload["dashboard"]["search_console"]["clicks"],
        "ga4_sessions": payload["dashboard"]["ga4"]["sessions"],
        "ghl_leads": payload["dashboard"]["ghl"]["total_leads"],
    }
    (DOCS_DIR / "export-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("")
    print("Public site generated successfully.")
    print(f"Folder: {DOCS_DIR}")
    print(f"Ranges exported: {RANGE_OPTIONS} days")
    print("Only aggregated counts were exported; the SQLite database stays local.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
