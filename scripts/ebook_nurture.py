"""E-book download -> DOMA membership nurture (Kyle, 2026-10-13).

Everyone who submits any e-book form from START on gets the `ebook-lead` tag and
three emails: day 2, day 5 and day 9 after their first download. The emails
themselves live in GoHighLevel: workflows "Ebook Nurture 1/2/3" fire on the tags
ebook-nurture-1/2/3, send the template (with the unsubscribe footer) and remove
the tag again. This script only decides who gets which tag and when.

Skipped: contacts with DND on, members (any tag in MEMBER_TAGS), internal
addresses. Tags only go out between 9:00 and 18:00 New York time.

Called at the end of scripts/ebook_lead_delivery.py (every 10 minutes).
Run alone with --apply to process now, without it to see what would happen.
"""

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ebook_pipeline.ghl_client import GhlClient  # noqa: E402
from scripts.env_utils import load_env_file  # noqa: E402
from scripts.ebook_lead_delivery import API_BASE, INTERNAL_DOMAIN, load_json, save_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "ebook_nurture_state.json"
START = "2026-10-14T13:00:00Z"  # only downloads from the day the funnel went live
OFFSETS = {1: 2, 2: 5, 3: 9}  # email number -> days after the first download
FORM_NAME = re.compile(r"e-?book", re.I)
MEMBER_TAGS = {"member-paid", "member-lifetime", "member-elite-monthly", "member-elite-early",
               "acces-doma-elite-monthly", "access-doma-elite-early", "access-doma-lifetime",
               "elite member", "manager elite", "manager pro", "pro member", "dia member",
               "wix member doma elite", "wix-active-member"}


def ny_hour(now: datetime) -> int:
    from scripts.sync_blog_social import now_new_york
    return now_new_york().hour


def submissions_since(ghl: GhlClient, form_id: str, since: str) -> list[dict]:
    out, page = [], 1
    while True:
        r = requests.get(f"{API_BASE}/forms/submissions", headers=ghl._headers(), timeout=30,
                         params={"locationId": ghl.location_id, "formId": form_id, "limit": 100, "page": page,
                                 "startAt": since[:10],
                                 "endAt": datetime.now(timezone.utc).strftime("%Y-%m-%d") + "T23:59:59Z"})
        r.raise_for_status()
        data = r.json()
        out += [s for s in data.get("submissions", []) if s.get("createdAt", "") >= since]
        if not data.get("meta", {}).get("nextPage"):
            return out
        page += 1


def run(apply: bool) -> int:
    ghl = GhlClient(load_env_file())
    state = load_json(STATE_PATH, {"leads": {}, "seen": []})
    leads, seen = state["leads"], set(state["seen"])
    errors = 0

    for form in ghl.list_forms(""):
        if not FORM_NAME.search(form["name"]):
            continue
        try:
            subs = submissions_since(ghl, form["id"], START)
        except requests.RequestException as error:
            print(f"SKIP form {form['name']}: {error}", file=sys.stderr)
            errors += 1
            continue
        for s in subs:
            cid = s.get("contactId")
            if s["id"] in seen or not cid:
                continue
            lead = leads.setdefault(cid, {"first": s["createdAt"], "email": s.get("email"), "sent": [], "tagged": False})
            lead["first"] = min(lead["first"], s["createdAt"])
            print(f"lead {s.get('email')} via {form['name']} ({s['createdAt'][:16]})")
            if apply:
                if not lead["tagged"]:
                    ghl.add_tag_to_contact(cid, "ebook-lead")
                    lead["tagged"] = True
                state["seen"].append(s["id"])
                save_json(STATE_PATH, state)

    now = datetime.now(timezone.utc)
    in_window = 9 <= ny_hour(now) < 18
    for cid, lead in leads.items():
        if lead.get("stopped"):
            continue
        first = datetime.fromisoformat(lead["first"].replace("Z", "+00:00"))
        due = [n for n, days in OFFSETS.items() if n not in lead["sent"] and now >= first + timedelta(days=days)]
        if not due:
            continue
        n = min(due)
        # If the script was down for days, never send two emails less than a day apart.
        last = lead.get("last_sent_at")
        if not in_window or (last and now - datetime.fromisoformat(last) < timedelta(days=1)):
            continue
        r = requests.get(f"{API_BASE}/contacts/{cid}", headers=ghl._headers(), timeout=30)
        if r.status_code != 200:
            print(f"SKIP {lead['email']}: contact lookup {r.status_code}", file=sys.stderr)
            errors += 1
            continue
        contact = r.json().get("contact", {})
        tags = {t.lower() for t in contact.get("tags", [])}
        email = (contact.get("email") or "").lower()
        reason = ("dnd" if contact.get("dnd") else "member" if tags & MEMBER_TAGS
                  else "internal" if email.endswith(INTERNAL_DOMAIN) else None)
        if reason:
            print(f"stop {lead['email']}: {reason}")
            if apply:
                lead["stopped"] = reason
                save_json(STATE_PATH, state)
            continue
        print(f"nurture {n} -> {lead['email']}")
        if apply:
            ghl.add_tag_to_contact(cid, f"ebook-nurture-{n}")
            lead["sent"].append(n)
            lead["last_sent_at"] = now.isoformat()
            save_json(STATE_PATH, state)
    return 1 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    return run(ap.parse_args().apply)


if __name__ == "__main__":
    sys.exit(main())
