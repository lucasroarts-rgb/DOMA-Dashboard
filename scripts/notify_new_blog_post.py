"""Blog "new post" email notification via GHL.

How it works (see README/CLAUDE.md context for the full design discussion):
1. Fetch a WordPress post (by id, or the most recent published one).
2. Build a subject/preheader for that specific post from its Yoast SEO
   fields (falls back to title/excerpt if Yoast isn't set).
3. Push those into 5 GHL Custom Values (blog_notify_subject, _preheader,
   _post_title, _post_excerpt, _post_url) - confirmed writable via the
   public API 2026-09-25, no manual GHL step needed for this part.
4. Apply the `blog-notify-new-post` tag to the target contact(s). That tag
   is the trigger for a GHL workflow (Contact Tag Added -> Send Email using
   the custom values above -> Remove Tag) that must be created manually in
   the GHL UI first - GHL's public API has no endpoint to create workflows
   (same limitation already documented for ebook forms in this project).

Three modes:
  --test-email you@example.com   Tags ONLY that one contact. Use this until
                                  the GHL workflow is confirmed live.
  --all                          Tags every contact in the location (7224 as
                                  of 2026-09-25) for ONE specific post (or the
                                  most recent one if --post-id is omitted).
                                  This is a manual real send.
  --auto                         The actual "always fire" mode. Compares the
                                  most recent published WP post(s) against
                                  data/blog_notify_state.json's
                                  last_notified_post_id. Any post newer than
                                  that gets the full --all treatment, oldest
                                  first, and the state file is updated after
                                  EACH one succeeds (so a crash mid-run never
                                  re-notifies a post that already went out).
                                  No new post -> prints "nothing new" and
                                  exits 0. Meant to run unattended on a
                                  recurring Windows Scheduled Task, same
                                  pattern as the ebook pipeline's safety-net
                                  poll.

Nothing sends an email by itself - tagging a contact is inert until the GHL
workflow exists and is published. Safe to run before that workflow is set up.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.env_utils import load_env_file  # noqa: E402
from scripts.ebook_pipeline.ghl_client import GhlClient, GhlError  # noqa: E402

STATE_PATH = ROOT / "data" / "blog_notify_state.json"
PROGRESS_PATH = ROOT / "data" / "blog_notify_progress.json"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
NOTIFY_TAG = "blog-notify-new-post"

# Custom Value ids created live 2026-09-25 (see ghl_client.py docstring) -
# fixed here rather than re-listed every run, since they don't change.
CUSTOM_VALUE_IDS = {
    "subject": "y3fD9cERH388EtFW6Ikm",
    "preheader": "Iu8JfIerfiyHzNV1W3VF",
    "post_title": "0QUN0ceZ9cdOXXO6nP3v",
    "post_excerpt": "fC2wXq17DWm1ppKifynB",
    "post_url": "Bo1mxeZiq3Xa5vl6ZlVQ",
    "featured_image": "z5yBQY6gqoidZxXczStA",
}


def fetch_featured_image_url(env: dict, post: dict) -> str:
    media_id = post.get("featured_media")
    if not media_id:
        return ""
    base = env["WP_URL"].rstrip("/")
    r = requests.get(f"{base}/wp-json/wp/v2/media/{media_id}", headers={"User-Agent": BROWSER_UA}, timeout=30)
    if r.status_code != 200:
        return ""
    return r.json().get("source_url", "")


def fetch_post(env: dict, post_id: int | None) -> dict:
    base = env["WP_URL"].rstrip("/")
    headers = {"User-Agent": BROWSER_UA}
    if post_id:
        r = requests.get(f"{base}/wp-json/wp/v2/posts/{post_id}", headers=headers, timeout=30)
    else:
        r = requests.get(
            f"{base}/wp-json/wp/v2/posts",
            params={"per_page": 1, "orderby": "date", "order": "desc", "status": "publish"},
            headers=headers,
            timeout=30,
        )
        posts = r.json()
        if not posts:
            raise RuntimeError("No published posts found")
        return posts[0]
    if r.status_code != 200:
        raise RuntimeError(f"Failed to fetch post {post_id}: {r.status_code} {r.text[:200]}")
    return r.json()


def fetch_new_posts_since(env: dict, last_notified_id: int) -> list[dict]:
    """Every published post with id > last_notified_id, oldest first. Caps at
    50 per run - if more than that publish between two --auto checks
    something else is very wrong and a human should look, not silently mass-
    email 50 backlogged posts."""
    base = env["WP_URL"].rstrip("/")
    headers = {"User-Agent": BROWSER_UA}
    r = requests.get(
        f"{base}/wp-json/wp/v2/posts",
        params={"per_page": 50, "orderby": "date", "order": "desc", "status": "publish"},
        headers=headers,
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Failed to list posts: {r.status_code} {r.text[:200]}")
    posts = [p for p in r.json() if p["id"] > last_notified_id]
    posts.sort(key=lambda p: p["id"])
    return posts


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def strip_html(text: str) -> str:
    import html
    import re

    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def build_email_fields(env: dict, post: dict) -> dict:
    title = strip_html(post["title"]["rendered"])
    yoast_title = (post.get("meta", {}) or {}).get("_yoast_wpseo_title", "")
    yoast_desc = (post.get("meta", {}) or {}).get("_yoast_wpseo_metadesc", "")
    excerpt = strip_html(post["excerpt"]["rendered"])

    subject = f"New Article: {yoast_title or title}"
    preheader = yoast_desc or excerpt[:140]

    return {
        "subject": subject,
        "preheader": preheader,
        "post_title": title,
        "post_excerpt": excerpt,
        "post_url": post["link"],
        "featured_image": fetch_featured_image_url(env, post),
    }


def push_custom_values(ghl: GhlClient, fields: dict) -> None:
    for key, value in fields.items():
        name = f"blog_notify_{key}"
        ghl.update_custom_value(CUSTOM_VALUE_IDS[key], name, value)
        print(f"  custom value set: {key} = {value[:80]!r}")


def send_to_email(ghl: GhlClient, email: str) -> int:
    contact = ghl.find_contact_by_email(email)
    if not contact:
        print(f"ERROR: no GHL contact found with email {email}", file=sys.stderr)
        return 1
    ghl.add_tag_to_contact(contact["id"], NOTIFY_TAG)
    print(f"Tagged test contact {contact['id']} ({email}) with '{NOTIFY_TAG}'.")
    print("If the GHL workflow (Contact Tag Added -> Send Email -> Remove Tag) is published, check inbox now.")
    return 0


def load_progress(post_id: int | None) -> set[str]:
    """Contact ids already tagged for this post by an earlier run that died
    mid-blast (the 2026-10-01 run was killed ~partway through 7224 contacts).
    The GHL workflow removes the tag after sending, so re-tagging those
    contacts would email them twice."""
    if post_id is None or not PROGRESS_PATH.exists():
        return set()
    data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    if data.get("post_id") != post_id:
        return set()
    return set(data.get("tagged", []))


def save_progress(post_id: int | None, tagged: set[str]) -> None:
    if post_id is None:
        return
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps({"post_id": post_id, "tagged": sorted(tagged)}), encoding="utf-8")


def send_to_all_contacts(ghl: GhlClient, post_id: int | None = None) -> tuple[int, int]:
    count = 0
    errors = 0
    tagged = load_progress(post_id)
    if tagged:
        print(f"  Resuming post {post_id}: skipping {len(tagged)} contacts already tagged.")
    for contact_id in ghl.iter_all_contact_ids():
        if contact_id in tagged:
            continue
        try:
            ghl.add_tag_to_contact(contact_id, NOTIFY_TAG)
            count += 1
            tagged.add(contact_id)
        except GhlError as error:
            errors += 1
            print(f"  WARNING: failed to tag {contact_id}: {error}", file=sys.stderr)
        if count and count % 50 == 0:
            print(f"  ...tagged {count} contacts so far")
            save_progress(post_id, tagged)
        time.sleep(0.12)  # ~8 req/sec, safe under GHL's burst limit
    save_progress(post_id, tagged)
    print(f"Done. Tagged {count} contacts ({errors} failures).")
    return count, errors


def run_auto(env: dict, ghl: GhlClient) -> int:
    state = load_state()
    last_id = state.get("last_notified_post_id")
    if last_id is None:
        # First-ever run: don't blast the whole list about every post
        # that's already live. Seed on the current latest post and start
        # watching from the NEXT one that publishes.
        current = fetch_post(env, None)
        save_state({"last_notified_post_id": current["id"]})
        print(f"First run: seeded state on post {current['id']} ({current['slug']}). Nothing sent.")
        return 0

    new_posts = fetch_new_posts_since(env, last_id)
    if not new_posts:
        print(f"Nothing new since post {last_id}.")
        return 0

    # One post per run. The email reads the post from global GHL custom values,
    # so pushing a second post's values while the first workflow is still
    # sending would swap the content mid-send. The rest go out on later runs.
    post = new_posts[0]
    if len(new_posts) > 1:
        print(f"{len(new_posts)} new posts; sending the oldest now, the rest on the next runs.")
    print(f"New post detected: {post['title']['rendered']} -> {post['link']} (id {post['id']})")
    fields = build_email_fields(env, post)
    push_custom_values(ghl, fields)
    count, errors = send_to_all_contacts(ghl, post["id"])
    save_state({"last_notified_post_id": post["id"]})
    print(f"  state updated -> last_notified_post_id={post['id']} ({count} tagged, {errors} failed)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--post-id", type=int, default=None, help="WP post id; defaults to most recent published post")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--test-email", help="Tag only this one contact (safe, use first)")
    group.add_argument("--all", action="store_true", help="Tag every contact for one post (manual real send)")
    group.add_argument("--auto", action="store_true", help="Check for new posts since last run, send if found (unattended mode)")
    args = parser.parse_args()

    env = load_env_file()
    ghl = GhlClient(env)

    if args.auto:
        return run_auto(env, ghl)

    post = fetch_post(env, args.post_id)
    print(f"Post: {post['title']['rendered']} -> {post['link']} (id {post['id']})")

    fields = build_email_fields(env, post)
    print("Pushing custom values...")
    push_custom_values(ghl, fields)

    if args.test_email:
        return send_to_email(ghl, args.test_email)

    send_to_all_contacts(ghl)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
