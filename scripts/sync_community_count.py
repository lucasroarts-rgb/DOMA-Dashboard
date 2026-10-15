"""Daily member count for the DOMA Free Community card (Leads tab).

GHL Communities has no public API, so the count used to be typed in by hand
and sat at 195 from 2026-08-26 on. Lucas chose (2026-10-15) to count the
contacts carrying the `joined_doma_community` tag, which the community
portal sign-up applies - 207 on that day. The number goes to the same
Firestore doc the "Update count" button writes (community_stats/
doma_free_community), so the card and the manual button keep working.

Usage: python scripts/sync_community_count.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402
from scripts.sync_ghl import API_BASE, _headers  # noqa: E402

COMMUNITY_TAG = "joined_doma_community"
FIRESTORE_DOC = (
    "https://firestore.googleapis.com/v1/projects/doma-dshboard/databases/(default)/documents/"
    "community_stats/doma_free_community"
)


def count_tagged_contacts(env: dict[str, str]) -> int:
    response = requests.post(
        f"{API_BASE}/contacts/search",
        headers=_headers(env),
        json={
            "locationId": env["GHL_LOCATION_ID"],
            "pageLimit": 1,
            "filters": [{"field": "tags", "operator": "contains", "value": COMMUNITY_TAG}],
        },
        timeout=30,
    )
    response.raise_for_status()
    return int(response.json()["total"])


def store_count(count: int) -> None:
    fields = {
        "member_count": {"integerValue": str(count)},
        "updated_at": {"integerValue": str(int(time.time() * 1000))},
        "source": {"stringValue": f"GHL tag {COMMUNITY_TAG} (daily sync)"},
    }
    params = [("updateMask.fieldPaths", f) for f in fields]
    response = requests.patch(FIRESTORE_DOC, json={"fields": fields}, params=params, timeout=30)
    response.raise_for_status()


def main() -> int:
    env = load_env_file()
    dashboard_app.init_db()
    count = count_tagged_contacts(env)
    store_count(count)
    log_sync(dashboard_app, "community_count", "ok", f"{count} contacts tagged {COMMUNITY_TAG}")
    print(f"Community count: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
