"""Log one team meeting + its action-item checklist into the dashboard's
"Team & Meetings" tab.

There's no meeting-transcription API wired in here - Claude reads the
pasted recap/transcript at meeting time and extracts the checklist,
same "manual research, structured entry" pattern as the Ad Spy panel.
This script just persists what was already extracted.

Usage:
    python scripts/add_meeting.py \
        --date 2026-08-24 \
        --title "DOMA Team Meeting Recap" \
        --summary "One-paragraph recap of the meeting" \
        --items-json path/to/items.json

items.json is a JSON list of objects:
    [{"owner": "Lucas", "description": "...", "context": "optional extra detail",
      "topic": "optional subject tag, e.g. SEO / Content / Dashboard"}, ...]

Run scripts/generate_public_site.py afterward to publish.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402


class AddMeetingError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary", default=None)
    parser.add_argument("--items-json", required=True, help="Path to a JSON file with the action-item list")
    parser.add_argument("--raw-notes", default=None, help="Optional path to the full transcript/recap text file")
    args = parser.parse_args()

    items_path = Path(args.items_json)
    if not items_path.exists():
        raise AddMeetingError(f"--items-json file not found: {items_path}")
    items = json.loads(items_path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise AddMeetingError("--items-json must contain a JSON list of {owner, description, context} objects")

    raw_notes = None
    if args.raw_notes:
        raw_notes_path = Path(args.raw_notes)
        if raw_notes_path.exists():
            raw_notes = raw_notes_path.read_text(encoding="utf-8")

    dashboard_app.init_db()

    with dashboard_app.db() as con:
        cur = con.execute(
            "INSERT INTO team_meetings (meeting_date, title, summary, raw_notes, created_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(meeting_date, title) DO UPDATE SET summary=excluded.summary, raw_notes=excluded.raw_notes "
            "RETURNING id",
            (args.date, args.title, args.summary, raw_notes),
        )
        meeting_id = cur.fetchone()[0]

        # Re-adding the same meeting replaces its checklist rather than
        # duplicating it - lets a meeting be re-logged if the recap gets
        # corrected without leaving stale rows behind.
        con.execute("DELETE FROM team_action_items WHERE meeting_id = ?", (meeting_id,))
        for item in items:
            con.execute(
                "INSERT INTO team_action_items (meeting_id, owner, description, status, context, topic, created_at) "
                "VALUES (?, ?, ?, 'open', ?, ?, CURRENT_TIMESTAMP)",
                (meeting_id, item["owner"], item["description"], item.get("context"), item.get("topic")),
            )

    print(f"Logged meeting '{args.title}' ({args.date}) with {len(items)} action item(s), id={meeting_id}")
    print("Run scripts/generate_public_site.py to publish.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
