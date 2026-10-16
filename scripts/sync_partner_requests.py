"""Import sponsor and partner requests from GoHighLevel into the Partners tab.

Every GHL contact tagged `partner-request` becomes one Firestore document in
`partner_requests` (doc id `ghl-<contactId>`), starting at stage "New request".
The team then moves it through Juli vetting, Packet sent, Call with Kyle and
Signed / Declined from the dashboard.

Privacy: the static site is public (client-side password only), so only the
company name, request date, form source, website and a link to the contact in
GHL are stored. No email, phone or person name is read into Firestore.

Re-sync safety: a document that already exists only gets its GHL fields
refreshed (company, website, form source, link) through an update mask. Stage,
owner, notes, next step and stage dates are never written after creation, and
a request removed from the board stays as a hidden document so it is not
re-imported.

    python scripts/sync_partner_requests.py              # last 90 days
    python scripts/sync_partner_requests.py --days 30
    python scripts/sync_partner_requests.py --dry-run    # print, write nothing

Needs the Firestore rule `match /partner_requests/{doc} { allow read, write: if true; }`.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.env_utils import load_env_file  # noqa: E402
from scripts.sync_ghl import API_BASE, _headers  # noqa: E402

TAG = "partner-request"
COLLECTION = "partner_requests"
DOCS = "projects/doma-dshboard/databases/(default)/documents"
BASE = f"https://firestore.googleapis.com/v1/{DOCS}"
GHL_CONTACT_URL = "https://app.brighteridea.com/v2/location/{loc}/contacts/detail/{cid}"
# GHL custom fields (Settings > Custom Fields), used only as fallbacks.
FIELD_LAST_OPTIN = "zwscouCUEqH9fsK2oAFW"  # "Last Opt-in For Form or Survey" (DATE)
FIELD_COMPANY = "bAAb7bWG3VasLBwxxIjJ"  # "Company Name_"
FIELD_WEBSITE = "YojORQxICjuzTwfVJLKm"  # "Website, LinkedIn, or examples of your work"
PARTNER_FORM_ID = "KIKKZfmVAVdr9mzkiJSb"  # the "02. Partner With Us!" form (attribution mediumId)
GHL_FIELDS = ["company", "website", "form_source", "ghl_url"]


def fv(value: Any) -> dict:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {k: fv(v) for k, v in value.items()}}}
    return {"stringValue": str(value)}


def clean(text: Any) -> str | None:
    value = " ".join(str(text or "").replace(" ", " ").split())
    return value or None


def fetch_tagged_contacts(env: dict[str, str]) -> list[dict]:
    contacts: list[dict] = []
    page = 1
    while True:
        response = requests.post(
            f"{API_BASE}/contacts/search",
            headers=_headers(env),
            json={
                "locationId": env["GHL_LOCATION_ID"],
                "page": page,
                "pageLimit": 100,
                "filters": [{"field": "tags", "operator": "contains", "value": TAG}],
            },
            timeout=30,
        )
        response.raise_for_status()
        batch = response.json().get("contacts", [])
        contacts += batch
        if len(batch) < 100 or page >= 20:
            return contacts
        page += 1


def request_date(contact: dict, custom: dict[str, Any]) -> str | None:
    """The day the partner form was sent. A contact that already existed (an
    old lead, a CSV import) and later filled the Partner With Us form keeps
    its old dateAdded, so when the last form it filled was on a /partner page
    the "last opt-in" date is the better answer."""
    added = str(contact.get("dateAdded") or "")[:10] or None
    last_url = str((contact.get("lastAttributionSource") or {}).get("url") or "")
    optin = str(custom.get(FIELD_LAST_OPTIN) or "")[:10]
    if "/partner" in last_url and len(optin) == 10 and (not added or optin > added):
        return optin
    return added


def form_source(contact: dict) -> str | None:
    """contact.source is the form that created the contact. An older contact
    who later sent the partner form still says "Added by you" or an ebook
    form there, so name the partner form when it was the last one filled."""
    source = clean(contact.get("source"))
    last_form = (contact.get("lastAttributionSource") or {}).get("mediumId")
    if last_form == PARTNER_FORM_ID and "partner" not in (source or "").lower():
        return f"Partner With Us form (contact first added via {source or 'unknown'})"
    return source


def to_record(contact: dict, location_id: str) -> dict[str, Any] | None:
    """Keep only the five allowed fields. Everything else in the contact
    (email, phone, names, address) is dropped here."""
    custom = {f.get("id"): f.get("value") for f in contact.get("customFields") or []}
    cid = contact.get("id")
    when = request_date(contact, custom)
    if not cid or not when:
        return None
    return {
        "id": f"ghl-{cid}",
        "company": clean(contact.get("companyName")) or clean(custom.get(FIELD_COMPANY)),
        "website": clean(contact.get("website")) or clean(custom.get(FIELD_WEBSITE)),
        "form_source": form_source(contact),
        "ghl_url": GHL_CONTACT_URL.format(loc=location_id, cid=cid),
        "request_date": when,
    }


def existing_ids() -> set[str]:
    ids: set[str] = set()
    token = None
    while True:
        params: dict[str, Any] = {"pageSize": 300, "mask.fieldPaths": "request_date"}
        if token:
            params["pageToken"] = token
        response = requests.get(f"{BASE}/{COLLECTION}", params=params, timeout=30)
        if response.status_code == 403:
            # RuntimeError, not SystemExit: daily_sync.run_source only catches Exception
            raise RuntimeError(
                f"403 on {COLLECTION}: add this rule in the Firebase console (Firestore > Rules) and publish:\n"
                f"  match /{COLLECTION}/{{doc}} {{ allow read, write: if true; }}"
            )
        response.raise_for_status()
        body = response.json()
        ids.update(d["name"].rsplit("/", 1)[-1] for d in body.get("documents", []))
        token = body.get("nextPageToken")
        if not token:
            return ids


def new_doc(record: dict[str, Any], now_ms: int) -> dict:
    data = {k: v for k, v in record.items() if k != "id"}
    data.update(
        {
            "source": "ghl",
            "stage": "new",
            "owner": "Juli",
            "next_step": "Juli to vet the company",
            "notes": None,
            "stage_dates": {"new": now_ms},
            "hidden": False,
            "created_at": now_ms,
            "updated_at": now_ms,
        }
    )
    return {"update": {"name": f"{DOCS}/{COLLECTION}/{record['id']}", "fields": {k: fv(v) for k, v in data.items()}}}


def refresh_doc(record: dict[str, Any]) -> dict:
    """GHL-owned fields only, so the team's stage/notes edits are untouched.
    request_date is left out too: the team may correct it by hand."""
    return {
        "update": {"name": f"{DOCS}/{COLLECTION}/{record['id']}", "fields": {k: fv(record[k]) for k in GHL_FIELDS}},
        "updateMask": {"fieldPaths": GHL_FIELDS},
        "currentDocument": {"exists": True},
    }


def sync(days: int = 90, dry_run: bool = False) -> str:
    env = load_env_file()
    since = (date.today() - timedelta(days=days)).isoformat()
    records = [r for r in (to_record(c, env["GHL_LOCATION_ID"]) for c in fetch_tagged_contacts(env)) if r]
    records = [r for r in records if r["request_date"] >= since]
    records.sort(key=lambda r: r["request_date"], reverse=True)

    if dry_run:
        for r in records:
            print(r["request_date"], r["company"] or "(company not given)", "|", r["form_source"])
        return f"dry run: {len(records)} partner requests since {since}"

    present = existing_ids()
    fresh = [r for r in records if r["id"] not in present]
    known = [r for r in records if r["id"] in present]
    now_ms = int(time.time() * 1000)
    writes = [new_doc(r, now_ms) for r in fresh] + [refresh_doc(r) for r in known]
    for start in range(0, len(writes), 400):
        response = requests.post(f"{BASE}:commit", json={"writes": writes[start : start + 400]}, timeout=60)
        response.raise_for_status()
    for r in fresh:
        print("new:", r["request_date"], r["company"] or "(company not given)")
    return f"{len(fresh)} new partner requests, {len(known)} already on the board (since {since})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(sync(args.days, args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
