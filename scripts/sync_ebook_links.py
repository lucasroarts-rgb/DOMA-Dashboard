"""Finds newly-published ebook/free-guide capture pages on the live
WordPress site and adds them to the "Useful Links" tab (Firestore
`useful_links` collection) automatically, so nobody has to remember to add
a link by hand every time a new one goes live.

Runs as part of scripts/daily_sync.py. Safe to re-run: every candidate is
checked against every URL already in Firestore before adding, so nothing
gets duplicated on a second run.

Detection: a capture page is any published WP page or post whose title
contains "ebook" or "free guide" (case-insensitive), or whose slug starts
with "ebook-" - excluding thank-you/confirmation pages and "-download"
variant URLs (those point at the same ebook under an alternate slug, not a
new one). This is a heuristic over the site's actual page-title convention,
not a dedicated content type - see CLAUDE.md for how ebook pages are made
(scripts/sync_ebook_pipeline.py generates drafts; a human still reviews and
publishes them in WordPress).

New links are auto-categorized into one of the 6 existing "Ebooks: <topic>"
buckets by keyword match against the title, falling back to "Ebooks:
Practice Operations" when nothing matches - not perfect, but keeps a human
from having to run this by hand just to get a link on the page. Worth a
periodic glance to re-file anything mis-bucketed.
"""

from __future__ import annotations

import html
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
FIRESTORE_PROJECT_ID = "doma-dshboard"
FIRESTORE_LINKS_URL = f"https://firestore.googleapis.com/v1/projects/{FIRESTORE_PROJECT_ID}/databases/(default)/documents/useful_links"

TITLE_PATTERN = re.compile(r"\b(ebook|free guide)\b", re.I)
EXCLUDE_SLUG_SUFFIXES = ("-thank-you", "-confirmation")
EXCLUDE_SLUG_SUBSTRINGS = ("-download",)
EXCLUDE_TITLE_SUBSTRINGS = ("confirmation",)

TOPIC_KEYWORDS = [
    ("Ebooks: Case Acceptance & Patient Communication", ("treatment acceptance", "case acceptance", "treatment finances", "insurance presentation")),
    ("Ebooks: Insurance & Billing", ("insurance", "billing", "aging report", "collection", "benefits", "revenue")),
    ("Ebooks: Career", ("raise", "90-day", "90 day", "career", "promotion")),
    ("Ebooks: AI", (" ai ", "ai-", "ai for", "ai powered")),
    ("Ebooks: Leadership", ("delegation", "team", "accountability", "policy", "burnout", "hiring", "wont listen", "doctor")),
    ("Ebooks: Practice Operations", ()),  # fallback bucket, matched last
]


def guess_topic(title: str) -> str:
    lowered = f" {title.lower()} "
    for category, keywords in TOPIC_KEYWORDS:
        if any(k in lowered for k in keywords):
            return category
    return "Ebooks: Practice Operations"


def fetch_published(wp_url: str, endpoint: str) -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        r = requests.get(
            f"{wp_url}/wp-json/wp/v2/{endpoint}",
            params={"per_page": 100, "page": page, "status": "publish", "_fields": "id,link,slug,title"},
            headers={"User-Agent": BROWSER_UA},
            timeout=30,
        )
        if r.status_code != 200:
            break
        batch = r.json()
        if not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def find_candidates(wp_url: str) -> list[dict]:
    all_items = fetch_published(wp_url, "pages") + fetch_published(wp_url, "posts")
    candidates = []
    for item in all_items:
        slug = item["slug"]
        title = html.unescape(item["title"]["rendered"])
        if slug.endswith(EXCLUDE_SLUG_SUFFIXES):
            continue
        if any(s in slug for s in EXCLUDE_SLUG_SUBSTRINGS):
            continue
        if any(s in title.lower() for s in EXCLUDE_TITLE_SUBSTRINGS):
            continue
        if not (TITLE_PATTERN.search(title) or slug.startswith("ebook-")):
            continue
        candidates.append({"title": title, "url": item["link"]})
    return candidates


def existing_urls() -> set[str]:
    r = requests.get(FIRESTORE_LINKS_URL, params={"pageSize": 300}, timeout=30)
    r.raise_for_status()
    docs = r.json().get("documents", [])
    urls = set()
    for doc in docs:
        url = doc.get("fields", {}).get("url", {}).get("stringValue")
        if url:
            urls.add(url.rstrip("/"))
    return urls


def add_link(title: str, url: str, category: str) -> bool:
    body = {
        "fields": {
            "title": {"stringValue": title},
            "url": {"stringValue": url},
            "category": {"stringValue": category},
            "created_at": {"integerValue": str(int(time.time() * 1000))},
        }
    }
    r = requests.post(FIRESTORE_LINKS_URL, json=body, timeout=30)
    return r.status_code == 200


def main() -> int:
    env = load_env_file()
    wp_url = (env.get("WP_URL") or "").rstrip("/")
    if not wp_url:
        print("WP_URL not set - skipping ebook link sync.", file=sys.stderr)
        return 0

    candidates = find_candidates(wp_url)
    known = existing_urls()
    new_ones = [c for c in candidates if c["url"].rstrip("/") not in known]

    added = 0
    for c in new_ones:
        category = guess_topic(c["title"])
        # Strip a leading "Ebook – " / "Ebook - " prefix and any trailing
        # " | DOMA" suffix - the category badge already says "Ebook", no
        # need to repeat it in every title in the list.
        clean_title = re.sub(r"^ebook\s*[-–]\s*", "", c["title"], flags=re.I).strip()
        clean_title = re.sub(r"\s*\|\s*DOMA\s*$", "", clean_title, flags=re.I).strip()
        clean_title = re.sub(r"\s*[-–]\s*Free Guide$", "", clean_title, flags=re.I).strip()
        ok = add_link(clean_title, c["url"], category)
        print(f"{'OK' if ok else 'FAILED'}: {clean_title} -> {category}")
        if ok:
            added += 1

    if not new_ones:
        print("No new ebook/free-guide pages found.")

    log_sync(dashboard_app, "ebook_links", "ok", f"{added} new link(s) added" if added else "no new ebooks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
