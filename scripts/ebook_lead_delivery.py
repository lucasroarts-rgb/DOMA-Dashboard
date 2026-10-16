"""Tag and email ebook leads for GHL forms that have no workflow.

Each ebook used to need its own GHL workflow (form submitted -> add tag ->
send the delivery email). Workflows can't be created through the public API,
so for the forms listed in data/ebook_delivery_forms.json this script does
the same job through the API:
  1. reads new submissions of each form;
  2. adds the tag ebook-<slug of the title>;
  3. sends the delivery email from the location's default sender
     (POST /conversations/messages, type Email).

The download link is never typed by hand: it is the PDF linked on the
thank-you page the form redirects to (formAction.redirectUrl), and the email
is only sent while that link answers 200. Lucas approved sending on 2026-10-12.

Submissions older than the form's "since" date only get the tag (they saw
the PDF on the thank-you page; a late email would be odd).
New forms created by sync_ebook_pipeline.py are added to the list there.

Dry run by default; --apply writes. --test-contact <id> sends one email per
form to that contact only (no tags, no state).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.env_utils import load_env_file  # noqa: E402
from scripts.ebook_pipeline.copy_generator import slugify  # noqa: E402
from scripts.ebook_pipeline.ghl_client import API_BASE, GhlClient, GhlError  # noqa: E402

FORMS_PATH = ROOT / "data" / "ebook_delivery_forms.json"
STATE_PATH = ROOT / "data" / "ebook_delivery_state.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
PDF_LINK = re.compile(r"https?://[^\"'\s<>]+?\.pdf", re.I)
INTERNAL_DOMAIN = "@joindoma.com"
AUTO_SINCE = "2026-10-12"  # forms created before this either have a workflow or were listed by hand


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def add_form(form_id: str, since: str | None = None) -> None:
    """Called by sync_ebook_pipeline.py when it creates a form with no workflow."""
    forms = load_json(FORMS_PATH, [])
    if any(f["form_id"] == form_id for f in forms):
        return
    forms.append({"form_id": form_id, "since": since or datetime.now(timezone.utc).isoformat()})
    save_json(FORMS_PATH, forms)


def ebook_for_form(ghl: GhlClient, form_id: str) -> dict:
    form = ghl.get_form(form_id)
    title = re.sub(r"^\s*ebook\s*-\s*", "", form["name"], flags=re.I).strip()
    thank_you = form["formData"]["form"]["formAction"].get("redirectUrl", "")
    pdfs = []
    if thank_you:
        page = requests.get(thank_you, headers=UA, timeout=30)
        if page.ok:
            pdfs = sorted(set(PDF_LINK.findall(page.text)))
    if len(pdfs) != 1:
        raise GhlError(f'{form["name"]}: expected one PDF on {thank_you or "(no redirect)"}, found {len(pdfs)}')
    pdf_url = html.unescape(pdfs[0])
    check = requests.head(pdf_url, headers=UA, timeout=30, allow_redirects=True)
    if check.status_code != 200:
        raise GhlError(f"{form['name']}: PDF answers {check.status_code}: {pdf_url}")
    return {"title": title, "tag": f"ebook-{slugify(title)}", "pdf_url": pdf_url, "thank_you": thank_you}


def email_html(title: str, pdf_url: str) -> str:
    t, u = html.escape(title), html.escape(pdf_url, quote=True)
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.6;color:#243244;max-width:600px;">
<p style="margin:0 0 18px;font-size:12px;color:#6b7280;">You're getting this because you downloaded a free guide from DOMA. Not useful right now? You can unsubscribe anytime with the link at the bottom of this email.</p>
<p>Hi {{{{contact.first_name}}}},</p>
<p>Your free copy of <strong>{t}</strong> is ready.</p>
<p style="margin:24px 0;"><a href="{u}" style="background:#0b2545;color:#ffffff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:bold;">Download your free guide</a></p>
<p>If the button doesn't work, copy this link into your browser:<br><a href="{u}">{u}</a></p>
<p><strong>Which part of this guide are you going to try first at your office? Hit reply and tell me.</strong></p>
<p>Kyle Summerford<br>Founder, DOMA</p>
</div>"""


def send_email(ghl: GhlClient, contact_id: str, ebook: dict) -> str:
    response = requests.post(
        f"{API_BASE}/conversations/messages",
        headers={**ghl._headers(), "Content-Type": "application/json"},
        json={
            "type": "Email",
            "contactId": contact_id,
            "subject": f"Your Free Guide: {ebook['title']}",
            "html": email_html(ebook["title"], ebook["pdf_url"]),
        },
        timeout=60,
    )
    if response.status_code not in (200, 201):
        raise GhlError(f"email send failed ({response.status_code}): {response.text[:300]}")
    return response.json().get("messageId", "")


def submissions(ghl: GhlClient, form_id: str) -> list[dict]:
    # The endpoint filters by date when startAt/endAt are missing, so always pass a wide range.
    out, page = [], 1
    while True:
        response = requests.get(
            f"{API_BASE}/forms/submissions",
            headers=ghl._headers(),
            params={"locationId": ghl.location_id, "formId": form_id, "limit": 100, "page": page,
                    "startAt": "2024-01-01", "endAt": datetime.now(timezone.utc).strftime("%Y-%m-%d") + "T23:59:59Z"},
            timeout=30,
        )
        if response.status_code != 200:
            raise GhlError(f"submissions failed ({response.status_code}): {response.text[:300]}")
        payload = response.json()
        out.extend(payload.get("submissions", []))
        if not payload.get("meta", {}).get("nextPage"):
            return out
        page += 1


def discover_new_forms(ghl: GhlClient, state: dict) -> None:
    """Forms the pipeline creates on Render never reach the local list (its disk
    is wiped on deploy), so any "Ebook - " form created from AUTO_SINCE on with
    no workflow of the same name joins the list here. Each form id is looked at once."""
    from scripts.ebook_pipeline.ghl_client import _normalize

    known = {f["form_id"] for f in load_json(FORMS_PATH, [])}
    seen = set(state.setdefault("seen_forms", []))
    workflows = None
    for form in ghl.list_forms("Ebook"):
        if form["id"] in known or form["id"] in seen or not form["name"].lower().startswith("ebook - "):
            continue
        added = ghl.get_form(form["id"]).get("dateAdded", "")
        if added >= AUTO_SINCE:
            workflows = workflows or {_normalize(w["name"]) for w in ghl.list_workflows()}
            if _normalize(form["name"]) not in workflows:
                add_form(form["id"], since=added)
                print(f"new form joined the delivery list: {form['name']}")
        state["seen_forms"].append(form["id"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--test-contact")
    ap.add_argument("--only", help="process just this form id")
    args = ap.parse_args()

    ghl = GhlClient(load_env_file())
    state = load_json(STATE_PATH, {"done": {}})
    if args.apply and not args.test_contact:
        discover_new_forms(ghl, state)
        save_json(STATE_PATH, state)
    forms = [f for f in load_json(FORMS_PATH, []) if not args.only or f["form_id"] == args.only]
    done: dict = state["done"]
    errors = 0

    for entry in forms:
        try:
            ebook = ebook_for_form(ghl, entry["form_id"])
        except (GhlError, requests.RequestException) as error:
            print(f"SKIP form {entry['form_id']}: {error}", file=sys.stderr)
            errors += 1
            continue

        if args.test_contact:
            print(f"test email: {ebook['title']} -> {ebook['pdf_url']}")
            if args.apply:
                print("  sent", send_email(ghl, args.test_contact, ebook))
            continue

        for sub in submissions(ghl, entry["form_id"]):
            if sub["id"] in done or not sub.get("contactId"):
                continue
            internal = (sub.get("email") or "").lower().endswith(INTERNAL_DOMAIN)
            send = sub["createdAt"] >= entry["since"] and not internal
            print(f"{ebook['tag']} <- {sub.get('email')} ({sub['createdAt'][:10]}){' + email' if send else ''}")
            if not args.apply:
                continue
            try:
                ghl.add_tag_to_contact(sub["contactId"], ebook["tag"])
                message_id = send_email(ghl, sub["contactId"], ebook) if send else None
            except (GhlError, requests.RequestException) as error:
                print(f"  FAILED: {error}", file=sys.stderr)
                errors += 1
                continue
            done[sub["id"]] = {"form_id": entry["form_id"], "contact_id": sub["contactId"], "tag": ebook["tag"],
                               "email_message_id": message_id, "at": datetime.now(timezone.utc).isoformat()}
            save_json(STATE_PATH, state)

    # Same 10-minute cadence: public reply under Instagram keyword comments.
    if args.apply and not args.test_contact:
        from scripts import ig_comment_replies
        try:
            ig_comment_replies.run(apply=True)
        except Exception as error:
            print(f"WARNING: Instagram comment replies failed: {error}", file=sys.stderr)
        # E-book download -> membership nurture emails (day 2, 5, 9).
        from scripts import ebook_nurture
        try:
            ebook_nurture.run(apply=True)
        except Exception as error:
            print(f"WARNING: e-book nurture failed: {error}", file=sys.stderr)
        # Paid GHL orders -> GA4 "purchase" (checkout is a payment link without the GA4 tag).
        from scripts import ga4_purchase_mp
        try:
            ga4_purchase_mp.run()
        except Exception as error:
            print(f"WARNING: GA4 purchase events failed: {error}", file=sys.stderr)
        # Plug the Leaks (Archy, Oct 23): reminders for people who clicked the registration link.
        from scripts import plug_the_leaks_reminders
        try:
            plug_the_leaks_reminders.run(apply=True)
        except Exception as error:
            print(f"WARNING: Plug the Leaks reminders failed: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
