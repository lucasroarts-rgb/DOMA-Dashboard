"""Replies under Instagram comments that asked for an ebook with its keyword
("REPORTS"), saying the guide went by DM. The DM itself comes from the GHL
"<ebook> Instagram DM" workflow; this only answers in public so other people
see it works. Liking the comment is not possible: the Instagram Graph API has
no endpoint to like comments.

Which posts: Content Calendar items with ig_keyword, posted on Instagram in
the last 14 days (found by "Comment <KEYWORD> " in the caption). Each comment
is answered once (data/ig_comment_replies.json), and only when GHL shows an
Instagram DM sent to that person after the comment (a second comment by the
same person gets no new DM, the workflow lets each contact in once); comments
by the DOMA account are skipped. Runs from ebook_lead_delivery.py, every 10 minutes.

Dry run by default; --apply writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.env_utils import load_env_file  # noqa: E402
from scripts import sync_blog_social as social  # noqa: E402

STATE = ROOT / "data" / "ig_comment_replies.json"
DAYS = 14
# A few wordings so the replies don't all look identical.
REPLIES = [
    "Just sent it to your DMs! \U0001F4E9 If you don't see it, check your message requests.",
    "It's in your DMs now \U0001F4E9 Peek at message requests if it's not in your inbox.",
    "Sent! Check your DMs \U0001F4E9 (sometimes it lands in message requests).",
]


def dm_sent_after(ghl, username: str, comment_time: str) -> bool:
    from datetime import datetime
    from scripts.ebook_pipeline.ghl_client import API_BASE

    when = datetime.fromisoformat(comment_time.replace("+0000", "+00:00"))
    convs = requests.get(f"{API_BASE}/conversations/search", headers=ghl._headers(),
                         params={"locationId": ghl.location_id, "query": username, "limit": 5}, timeout=30).json()
    for conv in convs.get("conversations", []):
        msgs = requests.get(f"{API_BASE}/conversations/{conv['id']}/messages", headers=ghl._headers(),
                            params={"limit": 20}, timeout=30).json().get("messages", {}).get("messages", [])
        for m in msgs:
            if (m.get("messageType") == "TYPE_INSTAGRAM" and m.get("direction") == "outbound"
                    and when <= datetime.fromisoformat(m["dateAdded"].replace("Z", "+00:00")) <= when + timedelta(minutes=3)):
                return True
    return False


def keyword_items() -> list[dict]:
    since = (date.today() - timedelta(days=DAYS)).isoformat()
    return [it for it in social.fetch_calendar_items()
            if it.get("ig_keyword") and it["instagram_posted"] and (it["date"] or "") >= since]


def run(apply: bool) -> int:
    env = load_env_file()
    token, base, ig_id = env["META_PAGE_ACCESS_TOKEN"], social.META_API_BASE, env["META_IG_ACCOUNT_ID"]
    items = keyword_items()
    if not items:
        return 0
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"replied": []}
    replied = set(state["replied"])
    me = requests.get(f"{base}/{ig_id}", params={"access_token": token, "fields": "username"}, timeout=30).json().get("username")
    media = requests.get(f"{base}/{ig_id}/media", params={"access_token": token, "fields": "id,caption", "limit": 50}, timeout=30).json().get("data", [])
    from scripts.ebook_pipeline.ghl_client import GhlClient
    ghl = GhlClient(env)
    errors = 0
    for item in items:
        kw = item["ig_keyword"].lower()
        post = next((m for m in media if f"comment {kw} " in (m.get("caption") or "").lower()), None)
        if not post:
            continue
        comments = requests.get(f"{base}/{post['id']}/comments",
                                params={"access_token": token, "fields": "id,text,username,timestamp", "limit": 100},
                                timeout=30).json().get("data", [])
        for c in comments:
            if c["id"] in replied or c.get("username") == me or kw not in (c.get("text") or "").lower():
                continue
            if not dm_sent_after(ghl, c.get("username") or "", c["timestamp"]):
                continue
            message = REPLIES[len(replied) % len(REPLIES)]
            print(f"{item['ig_keyword']}: @{c.get('username')} \"{c.get('text')}\" -> {message}")
            if not apply:
                continue
            r = requests.post(f"{base}/{c['id']}/replies", data={"message": message, "access_token": token}, timeout=30)
            if r.status_code != 200 or "error" in r.json():
                print(f"  FAILED: {r.text[:200]}", file=sys.stderr)
                errors += 1
                continue
            replied.add(c["id"])
            state["replied"] = sorted(replied)
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return 1 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    return run(ap.parse_args().apply)


if __name__ == "__main__":
    raise SystemExit(main())
