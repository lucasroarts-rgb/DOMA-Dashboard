"""Posts Content Calendar items to Facebook and Instagram on their scheduled
date, using the image already uploaded to that calendar item - Thalles
prepares the graphic himself (Content Calendar upload, or any tool he
likes) and this script only writes the caption and publishes it, once the
item's date arrives. Earlier drafts tried auto-generating the image itself
(Canva photos, then a pure-Pillow design) and neither matched what he
wanted - see git history on this file and instagram_creative.py for that
dead end. Caption and per-channel posting mechanics are unchanged from that
work; only the trigger and the image source changed.

Runs on the same 30-min scheduled cadence as the ebook pipeline. Firestore
IS the state (content_calendar_items gets facebook_posted/instagram_posted
fields patched directly), no separate local state file - the calendar item
already is the single source of truth for what's scheduled and what's gone
out, so a second file would just be one more thing that could drift out of
sync with it.

A calendar item is eligible once: it has image_url set, its date is today
or earlier (covers a missed run), and it isn't already posted on both
channels. Facebook gets a real photo post (image_url + caption), not a bare
link post. Instagram publishes the same image_url directly - no re-upload,
no image generation - then, for Sponsor and Blog/Ebook items only, drops
any link from the item's `links` field as a follow-up comment (Instagram
doesn't hyperlink caption text, and matches the real DOMA posting pattern
of "I'll drop the full article in the comments"). Other types (Engagement
Question, Teach It Tuesday, Community Reshare, Other) don't get a link
comment even when the item happens to carry one - Thalles confirmed
2026-09-24 that's only a sponsor/article thing.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import instagram_creative  # noqa: E402
from scripts.env_utils import load_env_file  # noqa: E402

META_API_VERSION = "v21.0"
META_API_BASE = f"https://graph.facebook.com/{META_API_VERSION}"
FIRESTORE_PROJECT_ID = "doma-dshboard"
FIRESTORE_BASE = f"https://firestore.googleapis.com/v1/projects/{FIRESTORE_PROJECT_ID}/databases/(default)/documents"
CALENDAR_COLLECTION = "content_calendar_items"


class SocialSyncError(RuntimeError):
    pass


def _field(fields: dict, key: str) -> str | None:
    value = fields.get(key, {})
    return value.get("stringValue")


def fetch_calendar_items() -> list[dict]:
    """All content_calendar_items - filtering (has an image, due, not yet
    posted) happens in Python rather than a Firestore query, same as the
    October-sponsor-scheduling work earlier this session; the collection is
    small enough (dozens of items) that this is simpler than building a
    structured query against the REST API."""
    items: list[dict] = []
    page_token = None
    while True:
        params = {"pageSize": 300}
        if page_token:
            params["pageToken"] = page_token
        r = requests.get(f"{FIRESTORE_BASE}/{CALENDAR_COLLECTION}", params=params, timeout=30)
        if r.status_code != 200:
            raise SocialSyncError(f"Firestore fetch failed ({r.status_code}): {r.text[:300]}")
        data = r.json()
        for doc in data.get("documents", []):
            fields = doc.get("fields", {})
            items.append(
                {
                    "id": doc["name"].split("/")[-1],
                    "date": _field(fields, "date"),
                    "type": _field(fields, "type") or "",
                    "title": _field(fields, "title") or "",
                    "headline": _field(fields, "headline"),
                    "direction": _field(fields, "direction"),
                    "notes": _field(fields, "notes"),
                    "image_url": _field(fields, "image_url"),
                    "facebook_posted": fields.get("facebook_posted", {}).get("booleanValue", False),
                    "instagram_posted": fields.get("instagram_posted", {}).get("booleanValue", False),
                    "links": fields.get("links", {}).get("arrayValue", {}).get("values", []),
                }
            )
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return items


def mark_posted(item_id: str, **flags: bool) -> None:
    fields = {name: {"booleanValue": value} for name, value in flags.items()}
    update_mask = "&".join(f"updateMask.fieldPaths={name}" for name in flags)
    r = requests.patch(
        f"{FIRESTORE_BASE}/{CALENDAR_COLLECTION}/{item_id}?{update_mask}",
        json={"fields": fields},
        timeout=30,
    )
    if r.status_code != 200:
        raise SocialSyncError(f"Firestore update failed for {item_id} ({r.status_code}): {r.text[:300]}")


LINK_COMMENT_TYPES = {"sponsor", "blog/ebook", "blog post"}


def first_link(item: dict) -> str | None:
    if item.get("type", "").strip().lower() not in LINK_COMMENT_TYPES:
        return None
    for value in item.get("links") or []:
        map_fields = value.get("mapValue", {}).get("fields", {})
        url = map_fields.get("url", {}).get("stringValue")
        if url:
            return url
    return None


def build_caption(item: dict) -> str:
    title = item["headline"] or item["title"]
    body_lines = [line.strip() for line in (item.get("direction") or item.get("notes") or "").splitlines() if line.strip()]
    return instagram_creative.generate_caption(title, body_lines[:5])


def post_to_facebook_photo(page_id: str, token: str, image_url: str, caption: str) -> str:
    r = requests.post(
        f"{META_API_BASE}/{page_id}/photos",
        data={"url": image_url, "caption": caption, "access_token": token},
        timeout=60,
    )
    payload = r.json()
    if r.status_code != 200 or "error" in payload:
        raise SocialSyncError(f"Facebook photo post failed: {payload.get('error', {}).get('message', r.text[:200])}")
    return str(payload.get("post_id") or payload.get("id"))


def post_to_instagram(ig_id: str, token: str, image_url: str, caption: str) -> str:
    container = requests.post(
        f"{META_API_BASE}/{ig_id}/media",
        data={"image_url": image_url, "caption": caption, "access_token": token},
        timeout=60,
    )
    container_payload = container.json()
    if container.status_code != 200 or "error" in container_payload:
        raise SocialSyncError(
            f"Instagram media create failed: {container_payload.get('error', {}).get('message', container.text[:200])}"
        )
    creation_id = container_payload["id"]

    publish = requests.post(
        f"{META_API_BASE}/{ig_id}/media_publish",
        data={"creation_id": creation_id, "access_token": token},
        timeout=30,
    )
    publish_payload = publish.json()
    if publish.status_code != 200 or "error" in publish_payload:
        raise SocialSyncError(
            f"Instagram publish failed: {publish_payload.get('error', {}).get('message', publish.text[:200])}"
        )
    return publish_payload["id"]


def post_ig_comment(media_id: str, token: str, message: str) -> None:
    r = requests.post(
        f"{META_API_BASE}/{media_id}/comments",
        data={"message": message, "access_token": token},
        timeout=30,
    )
    payload = r.json()
    if r.status_code != 200 or "error" in payload:
        raise SocialSyncError(f"Instagram comment failed: {payload.get('error', {}).get('message', r.text[:200])}")


def process_item(env: dict, item: dict) -> None:
    title = item["title"]
    caption = build_caption(item)
    link = first_link(item)

    page_id = env.get("META_PAGE_ID")
    page_token = env.get("META_PAGE_ACCESS_TOKEN")
    ig_id = env.get("META_IG_ACCOUNT_ID")

    if not item["facebook_posted"]:
        if page_id and page_token:
            try:
                fb_id = post_to_facebook_photo(page_id, page_token, item["image_url"], caption)
                mark_posted(item["id"], facebook_posted=True)
                print(f"Facebook: posted '{title}' ({fb_id})")
            except SocialSyncError as error:
                print(f"WARNING: Facebook post failed for '{title}': {error}", file=sys.stderr)
        else:
            print("Facebook: META_PAGE_ID/META_PAGE_ACCESS_TOKEN not set, skipping.", file=sys.stderr)

    if not item["instagram_posted"]:
        if ig_id and page_token:
            try:
                ig_post_id = post_to_instagram(ig_id, page_token, item["image_url"], caption)
                if link:
                    try:
                        post_ig_comment(ig_post_id, page_token, link)
                    except SocialSyncError as comment_error:
                        print(f"WARNING: Instagram comment (link) failed for '{title}': {comment_error}", file=sys.stderr)
                mark_posted(item["id"], instagram_posted=True)
                print(f"Instagram: posted '{title}' ({ig_post_id})")
            except SocialSyncError as error:
                print(f"WARNING: Instagram post failed for '{title}': {error}", file=sys.stderr)
        else:
            print("Instagram: META_IG_ACCOUNT_ID/META_PAGE_ACCESS_TOKEN not set, skipping.", file=sys.stderr)


def main() -> int:
    env = load_env_file()
    today = date.today().isoformat()

    try:
        items = fetch_calendar_items()
    except SocialSyncError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    due = [
        item
        for item in items
        if item["image_url"] and item["date"] and item["date"] <= today
        and not (item["facebook_posted"] and item["instagram_posted"])
    ]
    if not due:
        print("No calendar items due for posting.")
        return 0

    for item in due:
        process_item(env, item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
