"""Which GHL contacts are still engaged with our email.

Postmaster Tools (2026-10-10) showed Gmail spam reports above 0.3% on the days the
big sends went out, and the "New Article" email was going to every contact in the
location (7224). This builds the list the blog notification should use instead.

A contact counts as engaged when, inside the last WINDOW_DAYS, they opened or clicked
an email we sent, or wrote to us (any inbound message). New contacts (added in the
last NEW_CONTACT_DAYS) are let in by notify_new_blog_post.py itself, since they have
not had the chance to open anything yet.

GHL has no "opened in the last N days" filter in the public API, but each sent email
carries a status (delivered/opened/clicked/...). The script walks the conversations
with recent activity and reads email statuses, newest first, until it finds an
engagement for that contact or MAX_EMAILS_PER_CONTACT unopened ones. Results are
cached in data/ghl_engagement.json, so after the first full pass a daily run only
looks at conversations that changed since the previous run.

Run by daily_sync.py. Manual: python scripts/ghl_engagement.py [--full]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.env_utils import load_env_file  # noqa: E402

API = "https://services.leadconnectorhq.com"
CACHE_PATH = ROOT / "data" / "ghl_engagement.json"
WINDOW_DAYS = 90
NEW_CONTACT_DAYS = 30
ENGAGED_STATUSES = {"opened", "clicked"}
# "delivered" can still turn into "opened" later, so only final statuses are cached.
FINAL_STATUSES = ENGAGED_STATUSES | {"bounced", "failed", "complained", "unsubscribed", "undelivered"}
REQUESTS_PER_SECOND = 9  # GHL allows 100 requests / 10 s per location
WORKERS = 6
# Stop reading a contact's statuses after this many unopened emails in the window.
MAX_EMAILS_PER_CONTACT = 8


def parse_time(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {"built_at": None, "engaged": {}, "email_status": {}}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")


def engaged_contact_ids(max_age_days: int = 14) -> set[str]:
    """Contacts engaged inside the window, for notify_new_blog_post.py. Raises if the
    cache was never built; a stale cache is still used, with a warning."""
    if not CACHE_PATH.exists():
        raise RuntimeError(f"{CACHE_PATH.name} missing; run scripts/ghl_engagement.py first")
    cache = load_cache()
    built = parse_time(cache.get("built_at"))
    now = datetime.now(timezone.utc)
    if built is None or now - built > timedelta(days=max_age_days):
        print(f"WARNING: engagement list last built {cache.get('built_at')}", file=sys.stderr)
    cutoff = now - timedelta(days=WINDOW_DAYS)
    return {cid for cid, when in cache["engaged"].items() if parse_time(when) >= cutoff}


class RateLimiter:
    """Spaces requests across threads to stay under GHL's 100 requests / 10 s."""

    def __init__(self, per_second: float):
        self.interval = 1 / per_second
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = self.next_at - now
            self.next_at = max(now, self.next_at) + self.interval
        if delay > 0:
            time.sleep(delay)


class Ghl:
    def __init__(self, env: dict):
        self.location_id = env["GHL_LOCATION_ID"]
        self.headers = {
            "Authorization": f"Bearer {env['GHL_API_KEY']}",
            "Version": "2021-07-28",
            "Accept": "application/json",
        }
        self.local = threading.local()
        self.limiter = RateLimiter(REQUESTS_PER_SECOND)

    def get(self, path: str, **params) -> dict:
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            self.local.session.headers.update(self.headers)
        for attempt in range(5):
            self.limiter.wait()
            response = self.local.session.get(f"{API}{path}", params=params, timeout=30)
            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.json()
        response.raise_for_status()
        return {}

    def conversations_since(self, since: datetime) -> list[dict]:
        """Conversations whose last message is newer than `since`, newest first."""
        found, start_after = [], None
        while True:
            params = {"locationId": self.location_id, "limit": 100, "sort": "desc", "sortBy": "last_message_date"}
            if start_after:
                params["startAfterDate"] = start_after
            batch = self.get("/conversations/search", **params).get("conversations", [])
            if not batch:
                return found
            for conv in batch:
                if (parse_time(conv.get("lastMessageDate")) or since) < since:
                    return found
                found.append(conv)
            start_after = batch[-1].get("sort", [batch[-1].get("lastMessageDate")])[0]

    def messages(self, conversation_id: str, since: datetime):
        """Messages of one conversation newer than `since`, newest first."""
        last_id = None
        for _ in range(10):
            params = {"limit": 100}
            if last_id:
                params["lastMessageId"] = last_id
            page = self.get(f"/conversations/{conversation_id}/messages", **params).get("messages", {})
            for msg in page.get("messages", []):
                if (parse_time(msg.get("dateAdded")) or since) < since:
                    return
                yield msg
            if not page.get("nextPage"):
                return
            last_id = page.get("lastMessageId")

    def email_status(self, message_id: str) -> str | None:
        data = self.get(f"/conversations/messages/email/{message_id}")
        return (data.get("emailMessage") or data).get("status")


def check_conversation(ghl: Ghl, conv: dict, known: datetime | None, status_cache: dict,
                       window_start: datetime, recent_floor: datetime) -> tuple[str | None, dict, int]:
    """Newest engagement date for this conversation's contact (or None), the email
    statuses worth caching, and how many statuses were read from GHL."""
    new_status, reads = {}, 0
    for msg in ghl.messages(conv["id"], window_start):
        when = parse_time(msg.get("dateAdded"))
        if known and when <= known:
            break  # older messages were already accounted for
        if msg.get("direction") == "inbound":
            return msg["dateAdded"], new_status, reads
        if msg.get("messageType") != "TYPE_EMAIL":
            continue
        mids = (msg.get("meta") or {}).get("email", {}).get("messageIds", [])
        if not mids:
            continue
        status = status_cache.get(mids[0])
        if status is None:
            if reads >= MAX_EMAILS_PER_CONTACT:
                break  # several recent emails unopened: not engaged
            status = ghl.email_status(mids[0])
            reads += 1
            if status in FINAL_STATUSES or when < recent_floor:
                new_status[mids[0]] = status
        if status in ENGAGED_STATUSES:
            return msg["dateAdded"], new_status, reads
    return None, new_status, reads


def build(full: bool = False) -> dict:
    env = load_env_file()
    ghl = Ghl(env)
    cache = {"built_at": None, "engaged": {}, "email_status": {}} if full else load_cache()
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=WINDOW_DAYS)
    previous = parse_time(cache.get("built_at"))
    since = max(previous - timedelta(days=1), window_start) if previous else window_start
    recent_floor = now - timedelta(days=14)

    conversations = [c for c in ghl.conversations_since(since) if c.get("contactId")]
    print(f"  {len(conversations)} conversations with activity since {since.date()}")
    reads = done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(check_conversation, ghl, conv, parse_time(cache["engaged"].get(conv["contactId"])),
                        cache["email_status"], window_start, recent_floor): conv["contactId"]
            for conv in conversations
        }
        for future in as_completed(futures):
            cid = futures[future]
            done += 1
            try:
                when, new_status, n = future.result()
            except requests.RequestException as error:
                print(f"  WARNING: contact {cid} skipped ({error})", file=sys.stderr)
                continue
            reads += n
            cache["email_status"].update(new_status)
            if when and (cid not in cache["engaged"] or parse_time(when) > parse_time(cache["engaged"][cid])):
                cache["engaged"][cid] = when
            if done % 250 == 0:
                print(f"  ...{done}/{len(conversations)} conversations, {len(cache['engaged'])} engaged, {reads} status reads")
                save_cache(cache)

    cache["engaged"] = {c: w for c, w in cache["engaged"].items() if parse_time(w) >= window_start}
    cache["built_at"] = now.isoformat()
    cache["window_days"] = WINDOW_DAYS
    save_cache(cache)
    print(f"Engagement list: {len(cache['engaged'])} contacts engaged since {window_start.date()} "
          f"({len(conversations)} conversations checked, {reads} email statuses read).")
    return cache


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="ignore the cache and rebuild from scratch")
    args = parser.parse_args()
    build(full=args.full)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
