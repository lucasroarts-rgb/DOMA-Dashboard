"""Seed the Library and SOP Development tabs so they open with real content.

Both tabs read Firestore collections that need their own security rule
(library_articles, sop_items) - until those exist every write here returns 403.

    python scripts/seed_library_and_sops.py --dry-run     # counts only, writes nothing
    python scripts/seed_library_and_sops.py               # seed both
    python scripts/seed_library_and_sops.py --only sops   # or --only library

Idempotent: documents get stable ids and anything already in Firestore is
skipped, so re-running never overwrites edits made in the dashboard.

Library: published WordPress posts (title, author, categories as topics, link).
Podcast episodes and the "Downloadable Forms" category are left out by default
because they are not articles; --include-all brings them in.

SOPs: the ten procedures from the "DOMA Team SOPs" document (compiled from the
Aug 24 - Sep 28 team recaps), marked "Draft ready for review", plus the two
open items that came out of the Oct 5 conversation. Owners are filled in only
where a recap names one.
"""

from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.env_utils import load_env_file  # noqa: E402

PROJECT = "doma-dshboard"
DOCS = f"projects/{PROJECT}/databases/(default)/documents"
BASE = f"https://firestore.googleapis.com/v1/{DOCS}"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
EXCLUDED_CATEGORY_SLUGS = {"podcast", "downloadable-forms"}

DRAFT_NOTE = "From the DOMA Team SOPs document (compiled from the Aug 24 - Sep 28 recaps). Needs the team's review."

# (title, area, owner, source meetings)
SEED_SOPS = [
    ("Meeting rhythm", "Team", None, "Monday 9am ET check-in. The last meeting of each month is a full to-do review. Since Sep 21, Sep 28."),
    ("Dashboard habits", "Dashboard", None, "Start moves a task to In Progress, Complete only when fully done, Remove when no longer relevant. Specific task names. Share links and ideas in WhatsApp. Since Sep 28."),
    ("Content calendar cadence", "Content", "Juli", "Mon Engagement Question, Tue Teach It Tuesday, Wed Sponsor, Thu Blog or Ebook, Fri Community Reshare. Calendar kept filled a month ahead."),
    ("Ebook pipeline", "Ebooks", "Juli", "Three topics supplied every Monday from the dashboard's content suggestions. No sign-off from Kyle needed since Sep 28. Ebook, then video, then blog article, then newsletter."),
    ("Social and sponsor posting rotation", "Social", "Michelle", "Ebook before video on the same day (video ~12:30 PM ET). Dental Menu and Roya alternate weekly. Rotate Mon/Wed/Fri. Link goes in the first comment. Since Sep 28."),
    ("Video pipeline", "Content", "Lucas", "Presenter films raw footage, Lucas edits and cuts, presenter reviews before it goes out. Framed as personal experience."),
    ("New sponsor onboarding", "Sponsors", None, "Logo on the homepage carousel and Offers page, Canva welcome image, post to the Facebook page and group, welcome email with deliverables."),
    ("Facebook group moderation", "Social", "Juli", "Remove bad comments if there is time, otherwise screenshot and flag in WhatsApp. Pages commenting are removed. Competitor promotions: remove the comment and the person."),
    ("Email platform", "Newsletter", None, "ActiveCampaign is the platform of record for newsletter sends (decided Sep 11)."),
    ("Onboarding a new teammate", "Team", None, "Nothing major on day one, extra context on handoffs, walk through the dashboard, content calendar and ebook and newsletter pipeline."),
]

SEED_EXTRA_SOPS = [
    {
        "title": "Monthly content volume and posting days",
        "area": "Content",
        "owner": None,
        "status": "to_build",
        "needs": "A target for how much content goes out each month, and which days.",
        "notes": "Kyle's Sep 28 note: start building SOPs for content volume per month, posting days, etc. No number has been set in any recap yet.",
    },
    {
        "title": "Review and update the SOPs currently in ClickUp",
        "area": "Team",
        "owner": "Juli",
        "status": "needs_update",
        "needs": "Juli to send the list of SOPs in ClickUp (names and links) so they can be pasted in with Paste a list.",
        "notes": "From Juli's Oct 5 message: start by bringing in the SOPs from ClickUp, which need updating.",
    },
]


def fv(value):
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, list):
        return {"arrayValue": {"values": [fv(v) for v in value]}}
    return {"stringValue": str(value)}


def doc_write(collection: str, doc_id: str, data: dict) -> dict:
    return {
        "update": {
            "name": f"{DOCS}/{collection}/{doc_id}",
            "fields": {key: fv(val) for key, val in data.items()},
        }
    }


def existing_ids(collection: str) -> set[str]:
    ids: set[str] = set()
    token = None
    while True:
        params = {"pageSize": 300}
        if token:
            params["pageToken"] = token
        response = requests.get(f"{BASE}/{collection}", params=params, timeout=30)
        if response.status_code == 403:
            raise SystemExit(
                f"403 on {collection}: add this rule in the Firebase console (Firestore > Rules) and publish:\n"
                f"  match /{collection}/{{doc}} {{ allow read, write: if true; }}"
            )
        response.raise_for_status()
        body = response.json()
        ids.update(d["name"].rsplit("/", 1)[-1] for d in body.get("documents", []))
        token = body.get("nextPageToken")
        if not token:
            return ids


def commit(writes: list[dict]) -> None:
    for start in range(0, len(writes), 400):
        chunk = writes[start : start + 400]
        response = requests.post(f"{BASE}:commit", json={"writes": chunk}, timeout=60)
        response.raise_for_status()


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def build_sop_writes() -> list[tuple[str, dict]]:
    stamp = now_ms()
    items: list[tuple[str, dict]] = []
    for index, (title, area, owner, summary) in enumerate(SEED_SOPS, start=1):
        slug = f"seed-sop-{index:02d}"
        items.append(
            (
                slug,
                {
                    "title": title,
                    "area": area,
                    "owner": owner,
                    "status": "review",
                    "source_url": None,
                    "needs": None,
                    "notes": f"{summary} {DRAFT_NOTE}",
                    "status_changed_at": stamp,
                    "completed_at": None,
                    "created_at": stamp,
                    "updated_at": stamp,
                },
            )
        )
    for index, extra in enumerate(SEED_EXTRA_SOPS, start=1):
        items.append(
            (
                f"seed-sop-extra-{index:02d}",
                {**extra, "source_url": None, "status_changed_at": stamp, "completed_at": None, "created_at": stamp, "updated_at": stamp},
            )
        )
    return items


def wp_get(path: str, params: dict, auth: tuple[str, str]):
    wp_url = load_env_file()["WP_URL"].rstrip("/")
    response = requests.get(f"{wp_url}/wp-json/wp/v2/{path}", params=params, headers={"User-Agent": BROWSER_UA}, auth=auth, timeout=40)
    response.raise_for_status()
    return response.json()


def build_library_writes(include_all: bool) -> list[tuple[str, dict]]:
    env = load_env_file()
    auth = (env["WP_USERNAME"], env["WP_APP_PASSWORD"])
    categories = {c["id"]: c for c in wp_get("categories", {"per_page": 100}, auth)}
    users = {u["id"]: html.unescape(u["name"]) for u in wp_get("users", {"per_page": 100, "_fields": "id,name"}, auth)}

    items: list[tuple[str, dict]] = []
    page = 1
    while True:
        batch = wp_get(
            "posts",
            {"per_page": 100, "page": page, "status": "publish", "orderby": "id", "order": "asc", "_fields": "id,date_gmt,link,title,author,categories"},
            auth,
        )
        if not batch:
            break
        for post in batch:
            cats = [categories[c] for c in post.get("categories", []) if c in categories]
            if not include_all and any(c["slug"] in EXCLUDED_CATEGORY_SLUGS for c in cats):
                continue
            topics = [html.unescape(c["name"]) for c in cats if c["slug"] != "uncategorized"]
            posted = datetime.fromisoformat(post["date_gmt"]).replace(tzinfo=timezone.utc)
            stamp = int(posted.timestamp() * 1000)
            items.append(
                (
                    f"wp-{post['id']}",
                    {
                        "title": html.unescape(post["title"]["rendered"]),
                        "author": users.get(post["author"]),
                        "topics": topics,
                        "status": "published",
                        "url": post["link"],
                        "target_date": posted.date().isoformat(),
                        "notes": None,
                        "added_at": stamp,
                        "updated_at": stamp,
                    },
                )
            )
        if len(batch) < 100:
            break
        page += 1
    return items


def seed(collection: str, items: list[tuple[str, dict]], dry_run: bool) -> None:
    if dry_run:
        print(f"{collection}: {len(items)} documents would be considered (dry run, nothing checked or written)")
        return
    present = existing_ids(collection)
    fresh = [(doc_id, data) for doc_id, data in items if doc_id not in present]
    commit([doc_write(collection, doc_id, data) for doc_id, data in fresh])
    print(f"{collection}: wrote {len(fresh)} new documents, skipped {len(items) - len(fresh)} already there")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=["library", "sops"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-all", action="store_true", help="also import podcast episodes and Downloadable Forms posts")
    args = parser.parse_args()

    if args.only != "library":
        seed("sop_items", build_sop_writes(), args.dry_run)
    if args.only != "sops":
        library = build_library_writes(args.include_all)
        if args.dry_run:
            authors = sorted({d["author"] for _, d in library if d["author"]})
            print(f"library authors found ({len(authors)}): {', '.join(authors)}")
            topics = sorted({t for _, d in library for t in d["topics"]})
            print(f"library topics found ({len(topics)}): {', '.join(topics)}")
        seed("library_articles", library, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
