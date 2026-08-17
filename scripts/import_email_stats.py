"""Manually import GoHighLevel email campaign stats from a CSV file.

Why this exists: GoHighLevel's email campaign performance endpoints
(/emails/campaigns, /emails/schedule/{id}/stats) return 401 "not authorized
for this scope" for Private Integration tokens even with every scope
selected - confirmed 2026-08-17, see scripts/sync_ghl.py:fetch_email_campaigns
for the full story. That data is only reachable via a reviewed Marketplace
OAuth app, which is overkill for an internal dashboard. This script is the
pragmatic alternative: copy the numbers from GoHighLevel's own Reporting /
Marketing > Emails screen (or export a CSV from there, if the account
offers that) into a CSV with the columns below, then run this script.

CSV columns (header row required):
    campaign_name, sent_at, recipients, opens, clicks

    campaign_name   free text, e.g. "Newsletter - September"
    sent_at         ISO date, e.g. 2026-08-15 (blank is fine for drafts)
    recipients      integer
    opens           integer
    clicks          integer

Usage:
    python scripts/import_email_stats.py path\\to\\email_stats.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402


def parse_csv(path: Path) -> list[dict[str, object]]:
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"campaign_name", "sent_at", "recipients", "opens", "clicks"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"CSV is missing columns: {', '.join(sorted(missing))}")
        for i, raw in enumerate(reader, start=2):
            name = (raw.get("campaign_name") or "").strip()
            if not name:
                continue
            rows.append(
                {
                    "campaign_id": f"manual-{i}-{name[:40]}",
                    "campaign_name": name,
                    "sent_at": (raw.get("sent_at") or "").strip() or None,
                    "recipients": int(raw.get("recipients") or 0),
                    "opens": int(raw.get("opens") or 0),
                    "clicks": int(raw.get("clicks") or 0),
                }
            )
    return rows


def store(rows: list[dict[str, object]]) -> None:
    with dashboard_app.db() as con:
        con.executemany(
            """
            INSERT INTO ghl_email_campaigns
                (campaign_id, campaign_name, sent_at, recipients, opens, clicks, synced_at)
            VALUES (:campaign_id, :campaign_name, :sent_at, :recipients, :opens, :clicks, CURRENT_TIMESTAMP)
            ON CONFLICT(campaign_id) DO UPDATE SET
                campaign_name = excluded.campaign_name,
                sent_at = excluded.sent_at,
                recipients = excluded.recipients,
                opens = excluded.opens,
                clicks = excluded.clicks,
                synced_at = CURRENT_TIMESTAMP
            """,
            rows,
        )


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/import_email_stats.py path\\to\\email_stats.csv", file=sys.stderr)
        return 1

    dashboard_app.init_db()
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    rows = parse_csv(path)
    if not rows:
        print("No campaign rows found in the CSV.", file=sys.stderr)
        return 1

    store(rows)
    print(f"Imported {len(rows)} email campaign(s) from {path.name}.")
    print("Run scripts/generate_public_site.py to publish the update.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
