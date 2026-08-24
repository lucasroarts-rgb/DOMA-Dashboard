"""Set one team action item's status during a weekly review.

Usage:
    python scripts/toggle_action_item.py --id 7 --done
    python scripts/toggle_action_item.py --id 7 --in-progress
    python scripts/toggle_action_item.py --id 7 --open

Same effect as clicking the checkmark in the dashboard's Team & Meetings
tab (which cycles open -> in_progress -> done -> open) - this CLI lets you
jump straight to a specific status instead of clicking through.

Run scripts/generate_public_site.py afterward to publish. To see item
IDs, check the "Team & Meetings" tab locally (RUN_DASHBOARD.bat) or
query the team_action_items table directly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402


class ToggleError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--id", type=int, required=True, help="team_action_items.id")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--done", action="store_true")
    group.add_argument("--in-progress", action="store_true")
    group.add_argument("--open", action="store_true")
    args = parser.parse_args()

    status = "done" if args.done else "in_progress" if args.in_progress else "open"

    dashboard_app.init_db()

    with dashboard_app.db() as con:
        existing = con.execute("SELECT description, owner FROM team_action_items WHERE id = ?", (args.id,)).fetchone()
        if not existing:
            raise ToggleError(f"No action item with id={args.id}")

        if status == "done":
            con.execute(
                "UPDATE team_action_items SET status = 'done', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (args.id,),
            )
        else:
            con.execute(
                "UPDATE team_action_items SET status = ?, completed_at = NULL WHERE id = ?",
                (status, args.id),
            )
        print(f"Status set to '{status}': [{existing[1]}] {existing[0]}")

    print("Run scripts/generate_public_site.py to publish.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
