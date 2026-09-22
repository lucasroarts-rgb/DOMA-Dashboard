"""Turns pdf_extract.py's output into the copy + HTML for one eBook package:
slug, GHL tag name, excerpt, delivery email, capture page, thank-you page.

The templates mirror the real, hand-built "Burnout at the Front Desk" pages
(shared as the reference example) - navy/gold DOMA styling, hero + form card,
"Inside the guide" section with numbered cards pulled from the PDF's chapters,
fixed Kyle Summerford author bio, final CTA. Copy is auto-generated from the
PDF's actual chapter headings/text, so it's a solid first draft, not
guaranteed final marketing copy - the WordPress page stays a **draft** for a
human to review/polish before publishing.

Naming conventions follow the user's own CLAUDE.md rules (39-50):
- tag: ebook-{slug}
- form/workflow name (created manually in GHL, not by this script):
  "Ebook - {Title}" - confirmed live via the GHL API against existing
  ebooks (e.g. "Ebook - Burnout at the Front Desk"), matching what the
  account actually uses rather than the "- Delivery" suffix the rules doc
  suggests.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

GENERIC_SECTIONS = [
    {"heading": "What to watch for", "blurb": "Practical, ready-to-use guidance drawn straight from the guide."},
    {"heading": "Why it matters", "blurb": "Real-world context for dental office managers and front desk teams."},
]


def slugify(title: str) -> str:
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def build_package(
    extracted: dict,
    cover_url: str,
    pdf_url: str,
    ghl_domain: str,
    matched_form: dict | None = None,
) -> dict:
    title = extracted["title"].strip() or "Untitled eBook"
    subtitle = extracted.get("subtitle", "").strip()
    sections = extracted.get("sections") or GENERIC_SECTIONS
    slug = slugify(title)
    tag = f"ebook-{slug}"
    form_name = f"Ebook - {title}"

    hero_bullets_html = "\n".join(
        f'<li>&#10003; {s["heading"]}</li>' for s in sections[:4]
    )

    benefit_cards_html = "\n".join(
        f'<article class="card">\n<div class="num">{i:02d}</div>\n'
        f'<h3>{s["heading"]}</h3>\n<p>{s["blurb"]}</p>\n</article>'
        for i, s in enumerate(sections[:6], start=1)
    )

    if matched_form:
        form_id = matched_form["id"]
        form_embed = (
            f'<iframe data-activation-type="alwaysActivated" data-activation-value="" '
            f'data-deactivation-type="neverDeactivate" data-deactivation-value="" '
            f'data-form-id="{form_id}" data-form-name="{form_name}" data-height="500" '
            f"data-layout=\"{{'id':'INLINE'}}\" data-layout-iframe-id=\"inline-{form_id}\" "
            f'data-trigger-type="alwaysShow" data-trigger-value="" id="inline-{form_id}" '
            f'src="https://{ghl_domain}/widget/form/{form_id}" '
            f'style="width:100%;height:100%;border:none;border-radius:0px" title="{form_name}">'
            f"</iframe>"
        )
    else:
        form_id = "PASTE_FORM_ID_HERE"
        form_embed = (
            f"<!-- Form \"{form_name}\" doesn't exist in GHL yet. Duplicate an existing "
            f"\"Ebook - ...\" form, rename it to that, then replace this comment with its "
            f"embed code (GHL form builder > Add to Website > copy the iframe snippet). -->"
        )

    capture_html = _render(
        "capture_page.html",
        {
            "SLUG": slug,
            "TITLE": title,
            "SUBTITLE": subtitle or f"A free guide for dental office managers: {title}.",
            "HEADER_NOTE": "Free guide for dental office managers",
            "EYEBROW": "Free Guide",
            "COVER_URL": cover_url,
            "HERO_BULLETS": hero_bullets_html,
            "SECTION_HEADLINE": f"What's inside {title}",
            "SECTION_INTRO": (
                subtitle
                or f"A practical, chapter-by-chapter walkthrough of {title.lower()} for dental office managers and front desk teams."
            ),
            "BENEFIT_CARDS": benefit_cards_html,
            "AUTHOR_CLOSING": f"put the ideas in this guide to work in their own practice, one small change at a time.",
            "FINAL_HEADLINE": "Don't wait to put this guide to work.",
            "FORM_EMBED": form_embed,
            "FORM_ID": form_id,
            "GHL_DOMAIN": ghl_domain,
        },
    )

    ty_html = _render(
        "thank_you_page.html",
        {
            "TITLE": title,
            "COVER_URL": cover_url,
            "PDF_URL": pdf_url,
            "TY_COPY": (
                "Use this guide to put these ideas to work step by step, with practical "
                "tools you can apply right away in your practice."
            ),
        },
    )

    excerpt = (subtitle or (sections[0]["blurb"] if sections else f"A free guide for dental office managers: {title}."))[:155]

    email_subject = f"Your Free Guide: {title}"
    email_preview = f"{sections[0]['heading']} - and what to do about it." if sections else f"A free guide: {title}"
    bullet_lines = "\n".join(f"- {s['heading']}" for s in sections)
    email_body = (
        f"Hi {{{{contact.first_name}}}},\n\n"
        f"Your free copy of {title} is ready.\n\n"
        f"{subtitle or 'This guide gives you a practical framework you can put to use right away.'}\n\n"
        f"Inside this guide, you'll find:\n\n"
        f"{bullet_lines}\n\n"
        f"Download your free guide here:\n\n"
        f"{pdf_url}\n\n"
        f"Kyle Summerford\nFounder, DOMA"
    )

    return {
        "title": title,
        "subtitle": subtitle,
        "slug": slug,
        "tag": tag,
        "form_name": form_name,
        "workflow_name": form_name,
        "excerpt": excerpt,
        "cover_url": cover_url,
        "pdf_url": pdf_url,
        "capture_html": capture_html,
        "thank_you_html": ty_html,
        "email_subject": email_subject,
        "email_preview": email_preview,
        "email_body": email_body,
        "form_matched": bool(matched_form),
    }


def _render(template_name: str, values: dict) -> str:
    text = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text
