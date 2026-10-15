"""Plug the Leaks (Archy event, Fri 2026-10-23 6 PM, North Charleston): reminders for whoever
clicks the registration link in our emails (Kyle, 2026-10-14).

Email links go through https://dentalofficemanagers.com/go/plug-the-leaks/?c={{contact.id}},
a WP snippet that logs the GHL contact id and redirects to Archy's registration page. This
script reads that log (GET /wp-json/doma/v1/go-clicks), tags each new clicker
`plug-the-leaks-clicked` and later adds the tags that fire the GHL workflows
"Plug the Leaks Reminder 1/2/3" (send the email, remove the tag):
  1. a day after the first click, if that is at least 12 hours before reminder 2;
  2. Wed Oct 21, 10 AM New York;
  3. Fri Oct 23, 10 AM New York (the day of the event, until 5 PM).
We can't see who registered on Archy, so the emails say "already registered? see you there".
Tags only go out between 9 AM and 6 PM New York time; contacts with DND are skipped.

Called at the end of scripts/ebook_lead_delivery.py (every 10 minutes).
Run alone with --apply to process now, without it to see what would happen.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ebook_pipeline.ghl_client import GhlClient  # noqa: E402
from scripts.env_utils import load_env_file  # noqa: E402
from scripts.ebook_lead_delivery import API_BASE, UA, load_json, save_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "plug_the_leaks_state.json"
NY = timezone(timedelta(hours=-4))  # EDT; no tzdata on this box and the whole campaign is before Nov 1
SLUG = "plug-the-leaks"
REMINDER_2 = datetime(2026, 10, 21, 10, tzinfo=NY)
REMINDER_3 = datetime(2026, 10, 23, 10, tzinfo=NY)
LAST_CALL = datetime(2026, 10, 23, 17, tzinfo=NY)


def due_reminders(first: datetime, now: datetime) -> list[tuple[int, datetime, datetime]]:
    """(number, send from, give up at) for this contact."""
    out = []
    r1 = first + timedelta(days=1)
    if r1 <= REMINDER_2 - timedelta(hours=12):
        out.append((1, r1, REMINDER_2 - timedelta(hours=12)))
    if first < REMINDER_2:
        out.append((2, REMINDER_2, datetime(2026, 10, 22, 18, tzinfo=NY)))
    if first < LAST_CALL:
        out.append((3, max(REMINDER_3, first + timedelta(hours=2)), LAST_CALL))
    return out


def clicks(env: dict) -> list[dict]:
    r = requests.get(env["WP_URL"].rstrip("/") + "/wp-json/doma/v1/go-clicks", headers=UA,
                     auth=(env["WP_USERNAME"], env["WP_APP_PASSWORD"]), timeout=60)
    r.raise_for_status()
    return [c for c in r.json() if c.get("slug") == SLUG]


def run(apply: bool) -> int:
    env = load_env_file()
    now = datetime.now(timezone.utc)
    if now > LAST_CALL:
        return 0
    ghl = GhlClient(env)
    state = load_json(STATE_PATH, {"contacts": {}, "unknown": []})
    contacts, errors = state["contacts"], 0

    for c in clicks(env):
        cid = c["c"]
        if cid in contacts:
            contacts[cid]["last_click"] = max(contacts[cid]["last_click"], c["t"])
            continue
        if cid in state["unknown"]:
            continue
        r = requests.get(f"{API_BASE}/contacts/{cid}", headers=ghl._headers(), timeout=30)
        if r.status_code != 200:
            print(f"unknown contact id in click log: {cid} ({r.status_code})")
            if apply:
                state["unknown"].append(cid)
                save_json(STATE_PATH, state)
            continue
        email = r.json().get("contact", {}).get("email")
        print(f"click {email} via {c['s']} ({c['t'][:16]})")
        if apply:
            ghl.add_tag_to_contact(cid, "plug-the-leaks-clicked")
            contacts[cid] = {"email": email, "first_click": c["t"], "last_click": c["t"], "source": c["s"], "sent": []}
            save_json(STATE_PATH, state)

    ny_hour = now.astimezone(NY).hour
    if not 9 <= ny_hour < 18:
        return errors
    for cid, info in contacts.items():
        first = datetime.fromisoformat(info["first_click"])
        for n, start, stop in due_reminders(first, now):
            if n in info["sent"] or not start <= now < stop:
                continue
            r = requests.get(f"{API_BASE}/contacts/{cid}", headers=ghl._headers(), timeout=30)
            if r.status_code != 200 or r.json().get("contact", {}).get("dnd"):
                print(f"skip {info['email']}: {'dnd' if r.status_code == 200 else r.status_code}")
                errors += r.status_code != 200
                break
            print(f"reminder {n} -> {info['email']}")
            if apply:
                ghl.add_tag_to_contact(cid, f"plug-the-leaks-reminder-{n}")
                info["sent"].append(n)
                save_json(STATE_PATH, state)
            break  # one email per contact per run
    return 1 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    return run(ap.parse_args().apply)


if __name__ == "__main__":
    sys.exit(main())
