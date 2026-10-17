"""Gets an ebook's social posts ready after Michelle adds the ebook to the
Content Calendar (steps 7 to 10 of the ebook flow, 2026-10-12):

  7. art in the DOMA look: Instagram version ("comment the word KEYWORD") and
     Facebook version ("link in the comments"), JPEG, uploaded to WordPress;
  8. captions: Instagram asks for the keyword, Facebook says the link is in
     the comments;
  9. the "<ebook> Instagram DM" workflow in GHL (ghl_dm_workflow.py);
 10. the calendar item filled in: image_url (Instagram art; Michelle's image is
     kept in image_url_original), image_url_facebook, caption_instagram,
     caption_facebook, ig_keyword, ig_dm_workflow.
sync_blog_social.py then posts it on its date at 12:00 New York time, comments
the link on Facebook, and holds the Instagram post while ig_dm_workflow is not
published.

Which items: type Ebook, dated from today to LOOKAHEAD_DAYS ahead, not posted,
no ig_keyword yet, and a link to the ebook page on dentalofficemanagers.com.

Text comes from the item and the PDF only (title and subtitle of the PDF, the
item's headline). An item may carry `social_spec`, a JSON string that overrides
any of: keyword, title, headline_white, headline_gold, subtitle, points (up to
4 short lines for the art), caption_instagram, caption_facebook.

--check (step 12) lists, for ebook posts of the last 7 days, the Instagram
comments with the keyword and the form submissions since the post.

Dry run by default; --apply writes. --only <item id> limits the run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.env_utils import load_env_file  # noqa: E402
from scripts.ebook_pipeline import pdf_extract  # noqa: E402
from scripts.ebook_pipeline.ghl_client import GhlClient, _normalize  # noqa: E402
from scripts.ebook_pipeline.wp_client import WpClient  # noqa: E402
from scripts.ebook_pipeline.social_art import render  # noqa: E402
from scripts.ebook_pipeline.notify import notify  # noqa: E402
from scripts import sync_blog_social as social  # noqa: E402

FIRESTORE = social.FIRESTORE_BASE + "/" + social.CALENDAR_COLLECTION
KEYWORDS_PATH = ROOT / "data" / "ig_keywords.json"
LOOKAHEAD_DAYS = 10
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
# Keywords already answered by GHL DM workflows (Team ebook, the 3 of 2026-10-12, partner DMs).
SEED_KEYWORDS = ["TEAM", "REPORTS", "90DAYS", "HIRING", "ARCHY", "CARECREDIT", "DAYDREAM", "DENTALMENU",
                 "REVENUEWELL", "ROYA", "ARTICLE"]
STOPWORDS = set("""a an the and or but of to in on for with at by from your you our we is are be it its this that
what why how when who which not do does did don't doesn't isn't aren't can can't will just so if then than
stop start get getting make making build building more less new one two three into out up down""".split())
HASHTAGS = "#DentalOfficeManagers #DentalPracticeManagement #DentalLeadership #PracticeOperations #DentalTeam #DOMA"


def load_keywords() -> set[str]:
    if KEYWORDS_PATH.exists():
        return set(json.loads(KEYWORDS_PATH.read_text(encoding="utf-8")))
    return set(SEED_KEYWORDS)


def save_keyword(keyword: str) -> None:
    words = load_keywords() | {keyword}
    KEYWORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEYWORDS_PATH.write_text(json.dumps(sorted(words), indent=1), encoding="utf-8")


def pick_keyword(title: str, taken: set[str]) -> str:
    """Shortest of the first three meaningful words of the title, upper case,
    letters and digits only; a digit is added if every candidate is taken."""
    words = [re.sub(r"[^A-Za-z0-9]", "", w) for w in title.split()]
    cands = [w.upper() for w in words if len(w) >= 4 and w.lower() not in STOPWORDS][:3]
    for w in sorted(cands, key=len):
        if w not in taken and len(w) <= 12:
            return w
    base = (sorted(cands, key=len) or ["GUIDE"])[0][:10]
    n = 2
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"


def capture_link(item: dict) -> str | None:
    for value in item.get("links") or []:
        url = value.get("mapValue", {}).get("fields", {}).get("url", {}).get("stringValue") or ""
        if url.startswith("https://dentalofficemanagers.com/") and "thank-you" not in url:
            return url.split("?")[0]
    return None


def fetch_items() -> list[dict]:
    docs, token = [], None
    while True:
        params = {"pageSize": 300, **({"pageToken": token} if token else {})}
        data = requests.get(FIRESTORE, params=params, timeout=30).json()
        docs += data.get("documents", [])
        token = data.get("nextPageToken")
        if not token:
            return docs


def sval(fields: dict, key: str) -> str:
    return fields.get(key, {}).get("stringValue") or ""


def pdf_text(pdf_url: str) -> dict:
    r = requests.get(pdf_url, headers=UA, timeout=60)
    r.raise_for_status()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ebook.pdf"
        path.write_bytes(r.content)
        return pdf_extract.extract(path)


def captions(spec: dict, page_url: str) -> tuple[str, str]:
    hook = spec.get("hook") or spec["title"]
    body = [hook, ""]
    if spec.get("subtitle") and spec["subtitle"] != hook:
        body += [spec["subtitle"], ""]
    if spec.get("points"):
        body += [f"✅ {p}" for p in spec["points"]] + [""]
    body += ["It's all in our free guide." if hook == spec["title"] else f"Our free guide: {spec['title']}", ""]
    ig = "\n".join(body + [f"Comment {spec['keyword']} and we'll send it straight to your DMs. \U0001F447", "", HASHTAGS])
    fb = "\n".join(body + ["The link to the free guide is in the comments. \U0001F447"])
    return spec.get("caption_instagram") or ig, spec.get("caption_facebook") or fb


def patch_item(doc_id: str, values: dict[str, str]) -> None:
    fields = {k: {"stringValue": v} for k, v in values.items()}
    mask = "&".join(f"updateMask.fieldPaths={k}" for k in values)
    r = requests.patch(f"{FIRESTORE}/{doc_id}?{mask}", json={"fields": fields}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Firestore update failed ({r.status_code}): {r.text[:200]}")


def prepare(env: dict, doc: dict, apply: bool) -> str:
    f = doc["fields"]
    doc_id = doc["name"].split("/")[-1]
    page = capture_link({"links": f.get("links", {}).get("arrayValue", {}).get("values", [])})
    pdf_url = sval(f, "pdf_url")
    extracted = pdf_text(pdf_url) if pdf_url else {}
    spec = {"title": extracted.get("title") or re.sub(r"^\d+\.\s*", "", sval(f, "title")).strip(),
            "subtitle": extracted.get("subtitle") or ""}
    headline = sval(f, "headline")
    if headline and len(headline) <= 220 and _normalize(headline) != _normalize(spec["title"]):
        spec["hook"] = headline
    if sval(f, "social_spec"):
        spec.update(json.loads(sval(f, "social_spec")))
    if sval(f, "ig_keyword"):  # art and captions done on an earlier run; only the DM workflow is missing
        spec["keyword"] = sval(f, "ig_keyword")
        page_url = f"{page}?utm_source=instagram&utm_medium=dm&utm_campaign={re.sub(r'[^a-z0-9]+', '-', spec['keyword'].lower())}"
        print(f"  resuming DM workflow for {spec['keyword']}")
        if not apply:
            return spec["keyword"]
        from scripts.ebook_pipeline.ghl_dm_workflow import create_dm_workflow
        wid = create_dm_workflow(GhlClient(env), f"{spec['title'][:60].strip()} Instagram DM", spec["keyword"], spec["title"], page_url)
        patch_item(doc_id, {"ig_dm_workflow": wid})
        print(f"  ready: DM workflow {wid}")
        return spec["keyword"]
    taken = load_keywords() | {w["name"].split()[0].upper() for w in GhlClient(env).list_workflows() if w["name"].endswith("Instagram DM")}
    spec["keyword"] = (spec.get("keyword") or pick_keyword(spec["title"], taken)).upper()
    if spec["keyword"] in load_keywords():
        raise RuntimeError(f"keyword {spec['keyword']} is already used by another DM workflow")
    url = f"{page}?utm_source=instagram&utm_medium=dm&utm_campaign={re.sub(r'[^a-z0-9]+', '-', spec['keyword'].lower())}"
    ig_caption, fb_caption = captions(spec, page)
    print(f"  title: {spec['title']}\n  keyword: {spec['keyword']}\n  page: {page}")
    if not apply:
        print("  (dry run)\n" + "\n".join("  | " + l for l in ig_caption.splitlines()))
        return spec["keyword"]

    with tempfile.TemporaryDirectory() as tmp:
        wp = WpClient(env)
        slug = re.sub(r"[^a-z0-9]+", "-", spec["title"].lower()).strip("-")[:60]
        ig_img = wp.upload_media(render(spec, "instagram", Path(tmp) / f"doma-ebook-{slug}.jpg"), f"doma-ebook-{slug}.jpg")
        fb_img = wp.upload_media(render(spec, "facebook", Path(tmp) / f"doma-ebook-{slug}-fb.jpg"), f"doma-ebook-{slug}-fb.jpg")
    values = {"image_url": ig_img["url"], "image_url_facebook": fb_img["url"],
              "caption_instagram": ig_caption, "caption_facebook": fb_caption, "ig_keyword": spec["keyword"]}
    if sval(f, "image_url") and not sval(f, "image_url_original"):
        values["image_url_original"] = sval(f, "image_url")
    patch_item(doc_id, values)
    save_keyword(spec["keyword"])

    from scripts.ebook_pipeline.ghl_dm_workflow import create_dm_workflow
    name = f"{spec['title'][:60].strip()} Instagram DM"
    wid = create_dm_workflow(GhlClient(env), name, spec["keyword"], spec["title"], url)
    patch_item(doc_id, {"ig_dm_workflow": wid})
    print(f"  ready: art, captions, DM workflow {wid}")
    return spec["keyword"]


def story_lines(title: str, url: str) -> list[str]:
    return ["Hi {{contact.first_name}}!", "", "Thanks for jumping in 👋", "",
            f"Here's your copy of {title}:", url, "", "Hope it helps!"]


def prepare_story(env: dict, doc: dict, apply: bool) -> None:
    """Instagram/Facebook story for an ebook post: 1080x1920 art asking for a reply with
    the keyword, plus the "<ebook> Story DM" workflow that answers that reply."""
    f = doc["fields"]
    doc_id = doc["name"].split("/")[-1]
    kw = sval(f, "ig_keyword")
    page = capture_link({"links": f.get("links", {}).get("arrayValue", {}).get("values", [])})
    spec = {"title": re.sub(r"^\d+\.\s*", "", sval(f, "title")).strip(), "subtitle": ""}
    if sval(f, "pdf_url"):
        extracted = pdf_text(sval(f, "pdf_url"))
        spec.update({k: v for k, v in (("title", extracted.get("title")), ("subtitle", extracted.get("subtitle"))) if v})
    if sval(f, "social_spec"):
        spec.update(json.loads(sval(f, "social_spec")))
    spec["keyword"] = kw
    url = f"{page}?utm_source=instagram&utm_medium=story_dm&utm_campaign={re.sub(r'[^a-z0-9]+', '-', kw.lower())}"
    print(f"  story: {spec['title']} / {kw}")
    if not apply:
        return
    with tempfile.TemporaryDirectory() as tmp:
        slug = re.sub(r"[^a-z0-9]+", "-", spec["title"].lower()).strip("-")[:60]
        img = WpClient(env).upload_media(render(spec, "story", Path(tmp) / f"doma-ebook-{slug}-story.jpg"), f"doma-ebook-{slug}-story.jpg")
    patch_item(doc_id, {"image_url_story": img["url"], "story_keyword": kw})
    from scripts.ebook_pipeline.ghl_dm_workflow import create_story_dm_workflow
    wid = create_story_dm_workflow(GhlClient(env), f"{spec['title'][:60].strip()} Story DM", kw, story_lines(spec["title"], url), url)
    patch_item(doc_id, {"story_dm_workflow": wid})
    print(f"  story ready: workflow {wid}")


def run(apply: bool, only: str | None, days: int = LOOKAHEAD_DAYS) -> int:
    env = load_env_file()
    today = social.now_new_york().date()
    last = today + timedelta(days=days)
    errors = []
    for doc in fetch_items():
        f = doc["fields"]
        doc_id = doc["name"].split("/")[-1]
        if only and doc_id != only:
            continue
        d = sval(f, "date")
        if sval(f, "type").lower() != "ebook" or not d or not (today.isoformat() <= d <= last.isoformat()):
            continue
        if f.get("facebook_posted", {}).get("booleanValue"):
            continue
        if sval(f, "ig_dm_workflow"):
            if not sval(f, "story_dm_workflow") and capture_link({"links": f.get("links", {}).get("arrayValue", {}).get("values", [])}):
                print(f"{d} {sval(f, 'title')[:70]} ({doc_id})")
                try:
                    prepare_story(env, doc, apply)
                except Exception as error:
                    print(f"  FAILED (story): {error}", file=sys.stderr)
                    errors.append(f"{d} story {sval(f, 'title')[:40]}: {error}")
            continue
        if not capture_link({"links": f.get("links", {}).get("arrayValue", {}).get("values", [])}):
            continue
        print(f"{d} {sval(f, 'title')[:70]} ({doc_id})")
        try:
            prepare(env, doc, apply)
        except Exception as error:  # one bad item must not stop the others
            print(f"  FAILED: {error}", file=sys.stderr)
            errors.append(f"{d} {sval(f, 'title')[:50]}: {error}")
    if errors and apply:
        notify("Posts de ebook: falta algo", "\n".join(errors)[:900])
    return 1 if errors else 0


def check() -> int:
    """Step 12: keyword comments on Instagram and form submissions since each ebook post."""
    env = load_env_file()
    token, B = env["META_PAGE_ACCESS_TOKEN"], social.META_API_BASE
    since = (date.today() - timedelta(days=7)).isoformat()
    ghl = GhlClient(env)
    forms = {_normalize(fm["name"]): fm["id"] for fm in ghl.list_forms("Ebook")}
    for doc in fetch_items():
        f = doc["fields"]
        kw = sval(f, "ig_keyword")
        if not kw or sval(f, "date") < since or not f.get("instagram_posted", {}).get("booleanValue"):
            continue
        title = sval(f, "title")
        media = requests.get(f"{B}/{env['META_IG_ACCOUNT_ID']}/media", params={"access_token": token, "fields": "id,caption,timestamp", "limit": 50}, timeout=30).json().get("data", [])
        post = next((m for m in media if f"Comment {kw} " in (m.get("caption") or "")), None)
        comments = 0
        if post:
            data = requests.get(f"{B}/{post['id']}/comments", params={"access_token": token, "fields": "text", "limit": 100}, timeout=30).json().get("data", [])
            comments = sum(1 for c in data if kw.lower() in (c.get("text") or "").lower())
        form_id = forms.get(_normalize(f"Ebook - {title}"))
        subs = 0
        if form_id:
            r = requests.get("https://services.leadconnectorhq.com/forms/submissions", headers=ghl._headers(),
                             params={"locationId": ghl.location_id, "formId": form_id, "limit": 100,
                                     "startAt": sval(f, "date"), "endAt": date.today().isoformat() + "T23:59:59Z"}, timeout=30).json()
            subs = r.get("meta", {}).get("total", len(r.get("submissions", [])))
        print(f"{sval(f, 'date')} {kw}: {comments} comments with the keyword, {subs if form_id else 'no form found'} form submissions")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--check", action="store_true")
    # Getting a later week ready by hand (2026-10-17): look further than LOOKAHEAD_DAYS.
    ap.add_argument("--days", type=int, default=LOOKAHEAD_DAYS)
    args = ap.parse_args()
    return check() if args.check else run(args.apply, args.only, args.days)


if __name__ == "__main__":
    raise SystemExit(main())
