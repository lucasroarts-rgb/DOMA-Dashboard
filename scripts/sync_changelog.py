# -*- coding: utf-8 -*-
"""Push scripts/changelog_data.py into the site_changelog Firestore
collection (backs the dashboard's Changelog tab). Only writes the fields
listed in FIELDS below - never touches `comments`, so it's safe to re-run
any time, even for items already synced.

Usage: python scripts/sync_changelog.py
"""
import sys
import requests

sys.path.insert(0, ".")
from scripts.changelog_data import CHANGELOG

BASE = "https://firestore.googleapis.com/v1/projects/doma-dshboard/databases/(default)/documents/site_changelog"
FIELDS = ["date", "week_label", "category", "title", "detail", "impact", "link", "status", "order"]


def to_firestore_value(value):
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    return {"stringValue": value}


def main():
    updated, failed = 0, 0
    for item in CHANGELOG:
        doc_id = item["id"]
        fields = {f: to_firestore_value(item.get(f)) for f in FIELDS}
        params = [("updateMask.fieldPaths", f) for f in FIELDS]
        r = requests.patch(f"{BASE}/{doc_id}", json={"fields": fields}, params=params)
        if r.status_code == 200:
            updated += 1
        else:
            failed += 1
            print("FAILED", doc_id, r.status_code, r.text[:200])

    print(f"synced {updated} of {len(CHANGELOG)} ({failed} failed)")


if __name__ == "__main__":
    main()
