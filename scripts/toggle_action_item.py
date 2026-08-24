"""Mark one team action item done (or reopen it) during a weekly review.

Usage:
    python scripts/toggle_action_item.py --id 7 --done
    python scripts/toggle_action_item.py --id 7 --open

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
    group.add_argument("--open", action="store_true")
    args = parser.parse_args()

    dashboard_app.init_db()

    with dashboard_app.db() as con:
        existing = con.execute("SELECT description, owner FROM team_action_items WHERE id = ?", (args.id,)).fetchone()
        if not existing:
            raise ToggleError(f"No action item with id={args.id}")

        if args.done:
            con.execute(
                "UPDATE team_action_items SET status = 'done', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (args.id,),
            )
            print(f"Marked done: [{existing[1]}] {existing[0]}")
        else:
            con.execute(
                "UPDATE team_action_items SET status = 'open', completed_at = NULL WHERE id = ?",
                (args.id,),
            )
            print(f"Reopened: [{existing[1]}] {existing[0]}")

    print("Run scripts/generate_public_site.py to publish.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
