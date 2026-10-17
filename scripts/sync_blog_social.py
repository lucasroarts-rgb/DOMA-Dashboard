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

import re
import sys
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import instagram_creative  # noqa: E402
import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402
from scripts import social_endcard  # noqa: E402

META_API_VERSION = "v21.0"
META_API_BASE = f"https://graph.facebook.com/{META_API_VERSION}"
FIRESTORE_PROJECT_ID = "doma-dshboard"
FIRESTORE_BASE = f"https://firestore.googleapis.com/v1/projects/{FIRESTORE_PROJECT_ID}/databases/(default)/documents"
CALENDAR_COLLECTION = "content_calendar_items"


class SocialSyncError(RuntimeError):
    pass


# Sponsor names from the calendar, filled in main(); own posts get the community CTA and end card.
SPONSOR_WORDS: list[str] = []


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
                    "owner": _field(fields, "owner") or "",
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
                    # Instagram art when image_url may be replaced later (an ebook cover uploaded
                    # by the team after the social art is ready); falls back to image_url.
                    "image_url_instagram": _field(fields, "image_url_instagram"),
                    "ig_dm_workflow": _field(fields, "ig_dm_workflow"),
                    # 1080x1920 art; posted as an Instagram story and a Facebook page story.
                    "image_url_story": _field(fields, "image_url_story"),
                    # Story that asks for a reply with a word: held until its GHL DM workflow is published.
                    "story_keyword": _field(fields, "story_keyword"),
                    # Carousel: image URLs for Instagram and the Facebook version (last slide differs).
                    "carousel_urls": [v.get("stringValue") for v in fields.get("carousel_urls", {}).get("arrayValue", {}).get("values", []) if v.get("stringValue")],
                    "carousel_urls_facebook": [v.get("stringValue") for v in fields.get("carousel_urls_facebook", {}).get("arrayValue", {}).get("values", []) if v.get("stringValue")],
                    # HH:MM New York; when empty, feed items of the same day get staggered slots.
                    "post_time_et": _field(fields, "post_time_et"),
                    # Vertical video (MP4): Instagram Reel + Facebook video post.
                    "video_url": _field(fields, "video_url"),
                    "created_at": int(fields.get("created_at", {}).get("integerValue", 0) or 0),
                    "story_dm_workflow": _field(fields, "story_dm_workflow"),
                    "ig_story_posted": fields.get("ig_story_posted", {}).get("booleanValue", False),
                    "fb_story_posted": fields.get("fb_story_posted", {}).get("booleanValue", False),
                    # 35,000+ end card already appended to video_url (own posts only, social_endcard.py).
                    "endcard_applied": fields.get("endcard_applied", {}).get("booleanValue", False),
                    # Facebook page limit (fb_allowance): this item was kept off the Facebook page.
                    "facebook_skipped": fields.get("facebook_skipped", {}).get("booleanValue", False),
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


def set_item_fields(item_id: str, strings: dict[str, str], flags: dict[str, bool]) -> None:
    fields = {k: {"stringValue": v} for k, v in strings.items()}
    fields.update({k: {"booleanValue": v} for k, v in flags.items()})
    update_mask = "&".join(f"updateMask.fieldPaths={name}" for name in fields)
    r = requests.patch(f"{FIRESTORE_BASE}/{CALENDAR_COLLECTION}/{item_id}?{update_mask}", json={"fields": fields}, timeout=30)
    if r.status_code != 200:
        raise SocialSyncError(f"Firestore update failed for {item_id} ({r.status_code}): {r.text[:300]}")


def apply_own_post_cta(env: dict, item: dict, fb_caption: str, ig_caption: str, failures: list[str]) -> tuple[str, str]:
    """Own DOMA posts only (never sponsor or co-branded): group line in both captions and the
    35,000+ end card on the Reel. A failed end card never blocks the post; it is reported in the
    run's failures (logs/blog_social.log and sync_log). The end card only goes on a video that
    hasn't gone out anywhere yet, so Facebook and Instagram get the same file."""
    if not social_endcard.is_own_post(item, SPONSOR_WORDS):
        return fb_caption, ig_caption
    # The 35,000+ community Reel already is the end card.
    community_reel = "35,000" in f"{item.get('title') or ''} {item.get('caption_instagram') or ''}"
    untouched = not item.get("facebook_posted") and not item.get("instagram_posted")
    if item.get("video_url") and not item.get("endcard_applied") and not community_reel and untouched:
        from scripts.ebook_pipeline.wp_client import WpClient

        try:
            original = item["video_url"]
            new_url = social_endcard.video_with_endcard(
                env, original, lambda data, name: WpClient(env).upload_media_bytes(data, name, "video/mp4")["url"]
            )
            set_item_fields(item["id"], {"video_url": new_url, "video_url_original": original}, {"endcard_applied": True})
            item["video_url"] = new_url
        except Exception as error:  # noqa: BLE001
            failures.append(f"'{item['title']}': end card skipped, posted without it ({error})")
    return social_endcard.add_group_cta(fb_caption), social_endcard.add_group_cta(ig_caption)


LINK_COMMENT_TYPES = {"sponsor", "blog/ebook", "blog post", "blog", "ebook", "community reshare", "event"}
# Posts that promise "link in the comments" to a page on the site: held until the item has it
# (the ebook capture page or the published blog post). Art and captions can be ready earlier.
SITE_LINK_TYPES = {"ebook", "blog", "blog post", "blog/ebook"}
SITE = "https://dentalofficemanagers.com/"


def first_link(item: dict) -> str | None:
    kind = item.get("type", "").strip().lower()
    if kind not in LINK_COMMENT_TYPES:
        return None
    for value in item.get("links") or []:
        map_fields = value.get("mapValue", {}).get("fields", {})
        url = (map_fields.get("url", {}).get("stringValue") or "").strip()
        if not url.startswith("http"):
            continue  # labels typed into the url box ("Companion Guides")
        if kind in SITE_LINK_TYPES and (not url.startswith(SITE) or "thank-you" in url):
            continue  # the site page, not a Drive draft or the thank-you page
        return url
    return None


def with_utm(url: str | None, source: str, item: dict) -> str | None:
    """Site links in the Facebook / Instagram comments get UTM tags (GA4 review 2026-10-17:
    visits from these posts arrived as facebook.com / referral or direct, with no post name)."""
    if not url or not url.startswith(SITE) or "utm_" in url:
        return url
    parts = urlsplit(url)

    def slug(text: str | None, size: int) -> str:
        return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:size]

    query = dict(parse_qsl(parts.query))
    query.update({
        "utm_source": source,
        "utm_medium": "social",
        "utm_campaign": slug(item.get("type"), 30) or "post",
        "utm_content": f"{item.get('date') or ''}_{slug(item.get('title'), 40)}".strip("_"),
    })
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def build_caption(item: dict) -> str:
    title = item["headline"] or item["title"]
    body_lines = [line.strip() for line in (item.get("direction") or item.get("notes") or "").splitlines() if line.strip()]
    return instagram_creative.generate_caption(title, body_lines[:5])


def post_to_facebook_photo(page_id: str, token: str, image_url: str, caption: str) -> str:
    # A photo posted straight to /photos only showed under the page's Photos tab, not in
    # the feed (10 Reports, 2026-10-12). Same route as the carousel: one feed post.
    return post_fb_carousel(page_id, token, [image_url], caption)


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


def ensure_jpeg(env: dict, url: str) -> str:
    """The Instagram API only publishes JPEG. A WebP/PNG image (Michelle's uploads are
    WebP) is converted and uploaded to WordPress once; the JPEG URL is returned."""
    if not url or url.lower().split("?")[0].endswith((".jpg", ".jpeg")):
        return url
    import io
    import tempfile
    from PIL import Image
    from scripts.ebook_pipeline.wp_client import WpClient

    raw = requests.get(url, headers={"User-Agent": "Mozilla/5.0 Chrome/124"}, timeout=60).content
    name = url.rsplit("/", 1)[-1].rsplit(".", 1)[0] + "-ig.jpg"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        Image.open(io.BytesIO(raw)).convert("RGB").save(path, quality=92)
        return WpClient(env).upload_media(path, name)["url"]


def post_ig_reel(ig_id: str, token: str, video_url: str, caption: str) -> str:
    import time as _time

    container = requests.post(f"{META_API_BASE}/{ig_id}/media",
                              data={"media_type": "REELS", "video_url": video_url, "caption": caption,
                                    "share_to_feed": "true", "access_token": token}, timeout=60).json()
    if "error" in container:
        raise SocialSyncError(f"Instagram reel create failed: {container['error'].get('message')}")
    for _ in range(40):  # video processing takes a while
        st = requests.get(f"{META_API_BASE}/{container['id']}",
                          params={"fields": "status_code,status", "access_token": token}, timeout=30).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise SocialSyncError(f"Instagram reel processing failed: {st.get('status')}")
        _time.sleep(10)
    publish = requests.post(f"{META_API_BASE}/{ig_id}/media_publish",
                            data={"creation_id": container["id"], "access_token": token}, timeout=60).json()
    if "error" in publish:
        raise SocialSyncError(f"Instagram reel publish failed: {publish['error'].get('message')}")
    return publish["id"]


def post_fb_video(page_id: str, token: str, video_url: str, description: str) -> str:
    # Vertical videos go through the Reels publishing API. Posted through /videos the
    # DentalMenu reel came out as a reel nobody could open (2026-10-13).
    import time as _time

    start = requests.post(f"{META_API_BASE}/{page_id}/video_reels",
                          data={"upload_phase": "start", "access_token": token}, timeout=60).json()
    if "error" in start:
        raise SocialSyncError(f"Facebook reel start failed: {start['error'].get('message')}")
    vid = start["video_id"]
    up = requests.post(start["upload_url"], headers={"Authorization": f"OAuth {token}", "file_url": video_url},
                       timeout=300).json()
    if not up.get("success"):
        raise SocialSyncError(f"Facebook reel upload failed: {up}")
    for _ in range(60):
        status = requests.get(f"{META_API_BASE}/{vid}", params={"fields": "status", "access_token": token},
                              timeout=30).json().get("status", {})
        if status.get("uploading_phase", {}).get("status") == "complete":
            break
        _time.sleep(5)
    fin = requests.post(f"{META_API_BASE}/{page_id}/video_reels",
                        data={"upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
                              "description": description, "access_token": token}, timeout=60).json()
    if "error" in fin or not fin.get("post_id"):
        raise SocialSyncError(f"Facebook reel publish failed: {fin.get('error', {}).get('message', fin)}")
    # The link comment goes on the post right after this, so wait until it's live.
    for _ in range(40):
        status = requests.get(f"{META_API_BASE}/{vid}", params={"fields": "status", "access_token": token},
                              timeout=30).json().get("status", {})
        if status.get("video_status") == "error":
            raise SocialSyncError(f"Facebook reel processing failed: {status}")
        if status.get("publishing_phase", {}).get("status") == "complete":
            break
        _time.sleep(6)
    return f"{page_id}_{fin['post_id']}"


def post_ig_carousel(ig_id: str, token: str, image_urls: list[str], caption: str) -> str:
    import time as _time

    children = []
    for url in image_urls[:10]:
        c = requests.post(f"{META_API_BASE}/{ig_id}/media",
                          data={"image_url": url, "is_carousel_item": "true", "access_token": token}, timeout=60).json()
        if "error" in c:
            raise SocialSyncError(f"Instagram carousel item failed: {c['error'].get('message')}")
        children.append(c["id"])
    container = requests.post(f"{META_API_BASE}/{ig_id}/media",
                              data={"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption,
                                    "access_token": token}, timeout=60).json()
    if "error" in container:
        raise SocialSyncError(f"Instagram carousel create failed: {container['error'].get('message')}")
    for _ in range(10):  # wait until the container is ready
        st = requests.get(f"{META_API_BASE}/{container['id']}",
                          params={"fields": "status_code", "access_token": token}, timeout=30).json()
        if st.get("status_code") in ("FINISHED", None):
            break
        _time.sleep(3)
    publish = requests.post(f"{META_API_BASE}/{ig_id}/media_publish",
                            data={"creation_id": container["id"], "access_token": token}, timeout=60).json()
    if "error" in publish:
        raise SocialSyncError(f"Instagram carousel publish failed: {publish['error'].get('message')}")
    return publish["id"]


def post_fb_carousel(page_id: str, token: str, image_urls: list[str], message: str) -> str:
    """Facebook multi-photo post: photos uploaded unpublished, then one feed post with all of them."""
    data = {"message": message, "access_token": token}
    for i, url in enumerate(image_urls[:10]):
        ph = requests.post(f"{META_API_BASE}/{page_id}/photos",
                           data={"url": url, "published": "false", "access_token": token}, timeout=60).json()
        if "error" in ph:
            raise SocialSyncError(f"Facebook carousel photo failed: {ph['error'].get('message')}")
        data[f"attached_media[{i}]"] = '{"media_fbid":"' + ph["id"] + '"}'
    r = requests.post(f"{META_API_BASE}/{page_id}/feed", data=data, timeout=60).json()
    if "error" in r:
        raise SocialSyncError(f"Facebook carousel post failed: {r['error'].get('message')}")
    return r["id"]


def post_ig_story(ig_id: str, token: str, image_url: str) -> str:
    container = requests.post(f"{META_API_BASE}/{ig_id}/media",
                              data={"image_url": image_url, "media_type": "STORIES", "access_token": token}, timeout=60).json()
    if "error" in container:
        raise SocialSyncError(f"Instagram story create failed: {container['error'].get('message')}")
    publish = requests.post(f"{META_API_BASE}/{ig_id}/media_publish",
                            data={"creation_id": container["id"], "access_token": token}, timeout=60).json()
    if "error" in publish:
        raise SocialSyncError(f"Instagram story publish failed: {publish['error'].get('message')}")
    return publish["id"]


def post_fb_story(page_id: str, token: str, image_url: str) -> str:
    photo = requests.post(f"{META_API_BASE}/{page_id}/photos",
                          data={"url": image_url, "published": "false", "access_token": token}, timeout=60).json()
    if "error" in photo:
        raise SocialSyncError(f"Facebook story photo upload failed: {photo['error'].get('message')}")
    story = requests.post(f"{META_API_BASE}/{page_id}/photo_stories",
                          data={"photo_id": photo["id"], "access_token": token}, timeout=60).json()
    if "error" in story:
        raise SocialSyncError(f"Facebook story failed: {story['error'].get('message')}")
    return str(story.get("post_id") or story.get("id"))


def post_stories(env: dict, item: dict, posted: list[str], failures: list[str]) -> None:
    url, title = ensure_jpeg(env, item.get("image_url_story")), item["title"]
    if not url:
        return
    if item.get("story_keyword") and not dm_workflow_live(env, item.get("story_dm_workflow")):
        failures.append(f"Stories '{title}': waiting for the {item['story_keyword']} DM workflow")
        return
    token = env.get("META_PAGE_ACCESS_TOKEN")
    if not item.get("ig_story_posted") and env.get("META_IG_ACCOUNT_ID"):
        try:
            post_ig_story(env["META_IG_ACCOUNT_ID"], token, url)
            mark_posted(item["id"], ig_story_posted=True)
            posted.append(f"IG story '{title}'")
        except SocialSyncError as error:
            failures.append(f"IG story '{title}': {error}")
    if not item.get("fb_story_posted") and env.get("META_PAGE_ID"):
        try:
            post_fb_story(env["META_PAGE_ID"], token, url)
            mark_posted(item["id"], fb_story_posted=True)
            posted.append(f"FB story '{title}'")
        except SocialSyncError as error:
            failures.append(f"FB story '{title}': {error}")


def dm_workflow_live(env: dict, workflow_id: str | None) -> bool:
    if not workflow_id:
        return False
    from scripts.ebook_pipeline.ghl_client import GhlClient, GhlError
    try:
        return any(w["id"] == workflow_id and w.get("status") == "published" for w in GhlClient(env).list_workflows())
    except GhlError:
        return False


def process_item(env: dict, item: dict, fb_allowed: bool = True) -> tuple[list[str], list[str]]:
    """Returns (posted, failures) as short human-readable lines. fb_allowed=False keeps the
    item off the Facebook page (fb_allowance); Instagram and stories go out as usual."""
    posted: list[str] = []
    failures: list[str] = []
    title = item["title"]
    caption = build_caption(item)
    fb_caption = item.get("caption_facebook") or caption
    ig_caption = item.get("caption_instagram") or caption
    link = first_link(item)
    # With a keyword the link goes out by DM, so no link comment on Instagram.
    ig_link = None if item.get("ig_keyword") else with_utm(link, "instagram", item)
    fb_link = with_utm(link, "facebook", item)

    page_id = env.get("META_PAGE_ID")
    page_token = env.get("META_PAGE_ACCESS_TOKEN")
    ig_id = env.get("META_IG_ACCOUNT_ID")

    if not item["image_url"]:  # story-only item
        post_stories(env, item, posted, failures)
        return posted, failures

    if item.get("type", "").strip().lower() in SITE_LINK_TYPES and not link:
        print(f"Held '{title}': no {SITE} link in the item yet.", file=sys.stderr)
        failures.append(f"'{title}': waiting for the ebook page / blog post link in the item")
        return posted, failures

    # After the hold checks: a held item is not rendered or uploaded.
    fb_caption, ig_caption = apply_own_post_cta(env, item, fb_caption, ig_caption, failures)

    fb_video_paused = item.get("video_url") and (env.get("FB_VIDEO_PAUSED") or "").strip() in ("1", "true", "yes")
    if fb_video_paused and not item["facebook_posted"]:
        # Facebook kept hiding the DentalMenu reel (2026-10-13); videos wait here until that's understood.
        print(f"Facebook video held for '{title}' (FB_VIDEO_PAUSED in .env).", file=sys.stderr)
    if not item["facebook_posted"] and not fb_video_paused and fb_allowed:
        if page_id and page_token:
            try:
                fb_slides = item.get("carousel_urls_facebook") or item.get("carousel_urls")
                fb_id = (post_fb_video(page_id, page_token, item["video_url"], fb_caption) if item.get("video_url")
                         else post_fb_carousel(page_id, page_token, fb_slides, fb_caption) if fb_slides
                         else post_to_facebook_photo(page_id, page_token, item.get("image_url_facebook") or item["image_url"], fb_caption))
                if fb_link:
                    try:
                        # A bare-URL comment from the page gets hidden from viewers as spam
                        # (10 Reports, 2026-10-12); a sentence before the link shows normally.
                        post_fb_comment(fb_id, page_token, f"Here's the link \U0001F447\n\n{fb_link}")
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

    if not item["instagram_posted"] and item.get("ig_keyword") and not dm_workflow_live(env, item.get("ig_dm_workflow")):
        # "Comment KEYWORD" with no live DM workflow would leave people without the link.
        print(f"Instagram: held '{title}', DM workflow for {item['ig_keyword']} is not published yet.", file=sys.stderr)
        failures.append(f"IG '{title}': waiting for the {item['ig_keyword']} DM workflow")
    elif not item["instagram_posted"]:
        if ig_id and page_token:
            try:
                ig_post_id = (post_ig_reel(ig_id, page_token, item["video_url"], ig_caption) if item.get("video_url")
                              else post_ig_carousel(ig_id, page_token, [ensure_jpeg(env, u) for u in item["carousel_urls"]], ig_caption)
                              if item.get("carousel_urls")
                              else post_to_instagram(ig_id, page_token, ensure_jpeg(env, item.get("image_url_instagram") or item["image_url"]), ig_caption))
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
    post_stories(env, item, posted, failures)
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


# Two feed posts on the same day never go out together (Lucas, 2026-10-12): the first one
# takes the default time, the next ones these New York times, in this order.
EXTRA_SLOTS = ["16:00", "10:00", "18:00", "14:00", "09:00"]


def post_slots(items: list[dict], default_time: str) -> dict[str, tuple[int, int]]:
    """item id -> (hour, minute) New York. post_time_et on the item wins; the other feed items
    of a date fill the default time and then EXTRA_SLOTS, by creation order. Story-only items
    use the default time."""
    def hm(t: str) -> tuple[int, int]:
        h, m = t.split(":")
        return int(h), int(m)

    out: dict[str, tuple[int, int]] = {}
    by_date: dict[str, list[dict]] = {}
    for it in items:
        if it.get("post_time_et"):
            out[it["id"]] = hm(it["post_time_et"])
        elif it["image_url"] and it["date"]:
            by_date.setdefault(it["date"], []).append(it)
        else:
            out[it["id"]] = hm(default_time)
    for date, group in by_date.items():
        taken = {out[i["id"]] for i in items if i["date"] == date and i.get("post_time_et")}
        free = [hm(t) for t in [default_time] + EXTRA_SLOTS if hm(t) not in taken]
        free += [(20, 0)] * len(group)
        for it, slot in zip(sorted(group, key=lambda x: x.get("created_at", 0)), free):
            out[it["id"]] = slot
    return out


# Facebook page limits (Lucas, 2026-10-17). From 12/10 the page got about 3 posts a day,
# half of them sponsor posts; median reach per post fell from 130 to 50 and the page lost
# followers (seo_audit/facebook_seguidores_20261017.md). Instagram is not limited.
# Counted by the item's calendar date (sponsor: its Monday-to-Sunday week), with what is
# already on the page. Free guide, blog, other own posts, sponsor posts, then post time; the others get
# facebook_skipped and are not offered to Facebook again.
FB_LIMIT_DEFAULTS = {"FB_MAX_FEED_PER_DAY": 1, "FB_MAX_REELS_PER_DAY": 1, "FB_MAX_SPONSOR_PER_WEEK": 2}


def is_sponsor_post(item: dict) -> bool:
    if (item.get("type") or "").strip().lower() == "sponsor":
        return True
    # is_own_post also excludes events and community reshares; only the sponsor checks count here.
    return not social_endcard.is_own_post({**item, "type": ""}, SPONSOR_WORDS)


def fb_allowance(env: dict, items: list[dict], due: list[dict], slots: dict[str, tuple[int, int]]) -> dict[str, str]:
    """item id -> reason, for due items that stay off the Facebook page."""
    limit = {k: int((env.get(k) or "").strip() or v) for k, v in FB_LIMIT_DEFAULTS.items()}
    feed: dict[str, int] = {}
    reels: dict[str, int] = {}
    sponsor: dict[str, int] = {}

    def week(d: str) -> str:
        y, w, _ = date.fromisoformat(d).isocalendar()
        return f"{y}-W{w:02d}"

    for it in items:
        if it["facebook_posted"] and it["date"] and it["image_url"]:
            bucket = reels if it.get("video_url") else feed
            bucket[it["date"]] = bucket.get(it["date"], 0) + 1
            if is_sponsor_post(it):
                sponsor[week(it["date"])] = sponsor.get(week(it["date"]), 0) + 1
    def rank(i: dict) -> int:
        # Free guides first (email list goal), then blog posts (site visits), then DOMA's
        # other posts, then sponsor posts.
        kind = (i.get("type") or "").strip().lower()
        if kind == "ebook":
            return 0
        if kind.startswith("blog"):
            return 1
        return 3 if is_sponsor_post(i) else 2

    held: dict[str, str] = {}
    for it in sorted(due, key=lambda i: (i["date"], rank(i), slots.get(i["id"], (12, 0)))):
        if it["facebook_posted"] or not it["image_url"]:
            continue
        is_reel = bool(it.get("video_url"))
        bucket, cap = (reels, limit["FB_MAX_REELS_PER_DAY"]) if is_reel else (feed, limit["FB_MAX_FEED_PER_DAY"])
        wk = week(it["date"])
        if bucket.get(it["date"], 0) >= cap:
            held[it["id"]] = f"Facebook already has {cap} {'reel' if is_reel else 'feed post'}{'s' if cap != 1 else ''} on {it['date']}"
            continue
        if is_sponsor_post(it) and sponsor.get(wk, 0) >= limit["FB_MAX_SPONSOR_PER_WEEK"]:
            held[it["id"]] = f"Facebook already has {limit['FB_MAX_SPONSOR_PER_WEEK']} sponsor posts in the week of {it['date']}"
            continue
        bucket[it["date"]] = bucket.get(it["date"], 0) + 1
        if is_sponsor_post(it):
            sponsor[wk] = sponsor.get(wk, 0) + 1
    return held


def main() -> int:
    env = load_env_file()
    if (env.get("SOCIAL_POSTING_PAUSED") or "").strip() in ("1", "true", "yes"):
        print("Social posting paused (SOCIAL_POSTING_PAUSED in .env).")
        return 0
    ny = now_new_york()
    today = ny.date().isoformat()
    default_time = env.get("SOCIAL_POST_TIME_ET") or DEFAULT_POST_TIME_ET

    try:
        items = fetch_calendar_items()
    except SocialSyncError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    SPONSOR_WORDS[:] = social_endcard.sponsor_words(items)
    slots = post_slots(items, default_time)
    now_hm = (ny.hour, ny.minute)
    due = [
        item
        for item in items
        if (item["image_url"] or item.get("image_url_story")) and item["date"]
        # Michelle's sponsor highlights are hers to post (Lucas, 2026-10-14: Vyne went out and was deleted).
        # Her sponsor reels (Mon/Wed/Fri in the group) still go to our Instagram and Facebook (2026-10-16).
        and not (item["owner"].strip().lower() == "michelle" and item["type"].strip().lower() == "sponsor"
                 and not item.get("video_url"))
        and (item["date"] < today or (item["date"] == today and now_hm >= slots.get(item["id"], (12, 0))))
        and not ((not item["image_url"] or ((item["facebook_posted"] or item["facebook_skipped"]) and item["instagram_posted"]))
                 and (not item.get("image_url_story") or (item.get("ig_story_posted") and item.get("fb_story_posted"))))
    ]
    # One free guide per day at most (Lucas, 2026-10-14: two went out the same day).
    # A second guide dated the same day waits and shows up as a failure, so the calendar gets fixed.
    held: list[str] = []
    guide_days = {i["date"] for i in items if i["type"].strip().lower() == "ebook" and i["facebook_posted"]}
    for item in sorted(due, key=lambda i: slots.get(i["id"], (12, 0))):
        if item["type"].strip().lower() != "ebook" or item["facebook_posted"] or item["instagram_posted"]:
            continue
        if item["date"] in guide_days:
            due.remove(item)
            held.append(f"Ebook '{item['title']}': another free guide is already on {item['date']}, move it to another day")
        else:
            guide_days.add(item["date"])
    for line in held:
        print(f"Held {line}", file=sys.stderr)
    if not due:
        print("No calendar items due for posting.")
        return 1 if held else 0

    fb_held = fb_allowance(env, items, due, slots)
    posted: list[str] = []
    failures: list[str] = []
    for item in due:
        reason = fb_held.get(item["id"]) if not item["facebook_skipped"] else "kept off Facebook earlier"
        if reason and not item["facebook_posted"] and not item["facebook_skipped"]:
            try:
                set_item_fields(item["id"], {"facebook_skip_reason": reason}, {"facebook_skipped": True})
                print(f"Facebook: kept '{item['title']}' off the page ({reason}).")
            except SocialSyncError as error:
                print(f"WARNING: could not mark '{item['title']}' as kept off Facebook: {error}", file=sys.stderr)
        ok, failed = process_item(env, item, fb_allowed=not reason)
        posted += ok
        failures += failed
    record_run(posted, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
