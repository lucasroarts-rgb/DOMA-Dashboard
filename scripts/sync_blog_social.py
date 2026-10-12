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
from datetime import date, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import instagram_creative  # noqa: E402
import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402

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
                    # Ebook posts (2026-10-12): Instagram asks people to comment a keyword and the
                    # GHL "<ebook> Instagram DM" workflow sends the link by DM; Facebook says the
                    # link is in the comments and this script comments it. Either caption may be
                    # written by hand; when empty the generated one is used.
                    "ig_keyword": _field(fields, "ig_keyword"),
                    "caption_instagram": _field(fields, "caption_instagram"),
                    "caption_facebook": _field(fields, "caption_facebook"),
                    # Optional Facebook version of the image (ebook posts: "link in the comments"
                    # instead of "comment the word"); falls back to image_url.
                    "image_url_facebook": _field(fields, "image_url_facebook"),
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


def post_fb_comment(post_id: str, token: str, message: str) -> None:
    # Same "link in the comments" as Instagram; Lucas used to paste it by hand
    # on Facebook (2026-10-12). Needs pages_manage_engagement on the page token.
    r = requests.post(
        f"{META_API_BASE}/{post_id}/comments",
        data={"message": message, "access_token": token},
        timeout=30,
    )
    payload = r.json()
    if r.status_code != 200 or "error" in payload:
        raise SocialSyncError(f"Facebook comment failed: {payload.get('error', {}).get('message', r.text[:200])}")


def process_item(env: dict, item: dict) -> tuple[list[str], list[str]]:
    """Returns (posted, failures) as short human-readable lines."""
    posted: list[str] = []
    failures: list[str] = []
    title = item["title"]
    caption = build_caption(item)
    fb_caption = item.get("caption_facebook") or caption
    ig_caption = item.get("caption_instagram") or caption
    link = first_link(item)
    # With a keyword the link goes out by DM, so no link comment on Instagram.
    ig_link = None if item.get("ig_keyword") else link

    page_id = env.get("META_PAGE_ID")
    page_token = env.get("META_PAGE_ACCESS_TOKEN")
    ig_id = env.get("META_IG_ACCOUNT_ID")

    if not item["facebook_posted"]:
        if page_id and page_token:
            try:
                fb_id = post_to_facebook_photo(page_id, page_token, item.get("image_url_facebook") or item["image_url"], fb_caption)
                if link:
                    try:
                        post_fb_comment(fb_id, page_token, link)
                    except SocialSyncError as comment_error:
                        print(f"WARNING: Facebook comment (link) failed for '{title}': {comment_error}", file=sys.stderr)
                mark_posted(item["id"], facebook_posted=True)
                print(f"Facebook: posted '{title}' ({fb_id})")
                posted.append(f"FB '{title}'")
            except SocialSyncError as error:
                print(f"WARNING: Facebook post failed for '{title}': {error}", file=sys.stderr)
                failures.append(f"FB '{title}': {error}")
        else:
            print("Facebook: META_PAGE_ID/META_PAGE_ACCESS_TOKEN not set, skipping.", file=sys.stderr)

    if not item["instagram_posted"]:
        if ig_id and page_token:
            try:
                ig_post_id = post_to_instagram(ig_id, page_token, item["image_url"], ig_caption)
                if ig_link:
                    try:
                        post_ig_comment(ig_post_id, page_token, ig_link)
                    except SocialSyncError as comment_error:
                        print(f"WARNING: Instagram comment (link) failed for '{title}': {comment_error}", file=sys.stderr)
                mark_posted(item["id"], instagram_posted=True)
                print(f"Instagram: posted '{title}' ({ig_post_id})")
                posted.append(f"IG '{title}'")
            except SocialSyncError as error:
                print(f"WARNING: Instagram post failed for '{title}': {error}", file=sys.stderr)
                failures.append(f"IG '{title}': {error}")
        else:
            print("Instagram: META_IG_ACCOUNT_ID/META_PAGE_ACCESS_TOKEN not set, skipping.", file=sys.stderr)
    return posted, failures


def record_run(posted: list[str], failures: list[str]) -> None:
    # Runs under pythonw every 30 min, so stderr goes nowhere. Failures land in
    # logs/blog_social.log and sync_log (the dashboard shows sync_log).
    stamp = datetime.now().isoformat(timespec="seconds")
    log_path = ROOT / "logs" / "blog_social.log"
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for line in posted:
            handle.write(f"{stamp} posted {line}\n")
        for line in failures:
            handle.write(f"{stamp} FAILED {line}\n")
    try:
        dashboard_app.init_db()
        if failures:
            log_sync(dashboard_app, "blog_social", "error", "; ".join(failures))
        elif posted:
            log_sync(dashboard_app, "blog_social", "ok", "; ".join(posted))
    except Exception as error:  # noqa: BLE001 - the log file above already has the run
        print(f"WARNING: could not write sync_log: {error}", file=sys.stderr)


# Items dated today wait until this time in New York (DOMA's audience is in the US).
# DOMA's own posts, Aug to Oct 2026 (about 45 per network): Facebook did best
# between 12:00 and 15:00 ET, Instagram between 9:00 and 12:00 and 15:00 to 18:00 ET.
# Noon ET serves both. SOCIAL_POST_TIME_ET overrides (HH:MM).
DEFAULT_POST_TIME_ET = "12:00"


def now_new_york() -> datetime:
    """US Eastern time without tzdata (not installed on this Windows box): EDT
    (UTC-4) from the second Sunday of March to the first Sunday of November,
    switching at 2:00 local, else EST (UTC-5)."""
    from datetime import timedelta, timezone

    utc = datetime.now(timezone.utc)
    year = utc.year
    march_1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    nov_1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_start = march_1 + timedelta(days=(6 - march_1.weekday()) % 7 + 7, hours=7)  # 2:00 EST
    dst_end = nov_1 + timedelta(days=(6 - nov_1.weekday()) % 7, hours=6)  # 2:00 EDT
    offset = -4 if dst_start <= utc < dst_end else -5
    return utc.astimezone(timezone(timedelta(hours=offset)))


def main() -> int:
    env = load_env_file()
    if (env.get("SOCIAL_POSTING_PAUSED") or "").strip() in ("1", "true", "yes"):
        print("Social posting paused (SOCIAL_POSTING_PAUSED in .env).")
        return 0
    ny = now_new_york()
    today = ny.date().isoformat()
    hour, minute = (int(x) for x in (env.get("SOCIAL_POST_TIME_ET") or DEFAULT_POST_TIME_ET).split(":"))
    before_post_time = (ny.hour, ny.minute) < (hour, minute)

    try:
        items = fetch_calendar_items()
    except SocialSyncError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    due = [
        item
        for item in items
        if item["image_url"] and item["date"]
        and (item["date"] < today or (item["date"] == today and not before_post_time))
        and not (item["facebook_posted"] and item["instagram_posted"])
    ]
    if not due:
        print("No calendar items due for posting.")
        return 0

    posted: list[str] = []
    failures: list[str] = []
    for item in due:
        ok, failed = process_item(env, item)
        posted += ok
        failures += failed
    record_run(posted, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
