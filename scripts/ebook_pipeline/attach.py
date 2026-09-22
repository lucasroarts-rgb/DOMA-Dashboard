"""Shared logic for attaching a real GHL form embed to an already-created
WordPress capture-page draft, once the form has been duplicated/renamed in
GHL. Used by both:
- scripts/attach_ebook_form.py (manual one-off run)
- scripts/sync_ebook_pipeline.py (automatic recheck every 30min - so Lucas
  never has to tell anyone the form is ready, the scheduled run just notices)
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.ebook_pipeline import copy_generator
from scripts.ebook_pipeline.wp_client import WpClient, WpError
from scripts.ebook_pipeline.ghl_client import GhlClient, GhlError


def try_attach(env: dict, package_json_path: Path) -> dict:
    """Returns {"message": str, "attached": bool, "slug": str, "edit_url": str|None}.
    Never raises - callers loop over many packages and one bad/missing form
    shouldn't stop the rest."""
    slug = package_json_path.parent.name
    try:
        saved = json.loads(package_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"message": f"{slug}: skipped (couldn't read package.json: {error})", "attached": False, "slug": slug, "edit_url": None}

    if saved.get("form_attached"):
        return {"message": f"{slug}: already attached, nothing to do", "attached": False, "slug": slug, "edit_url": None}

    try:
        ghl = GhlClient(env)
        matched_form = ghl.find_form_by_title(saved["form_name"])
    except GhlError as error:
        return {"message": f"{slug}: GHL lookup failed ({error})", "attached": False, "slug": slug, "edit_url": None}

    if not matched_form:
        return {
            "message": f'{slug}: form "{saved["form_name"]}" not created in GHL yet - still pending',
            "attached": False,
            "slug": slug,
            "edit_url": None,
        }
    ghl_domain = env.get("GHL_WIDGET_DOMAIN", "")
    package = copy_generator.build_package(
        saved["extracted"], saved["cover_url"], saved["pdf_url"], ghl_domain, matched_form
    )

    try:
        wp = WpClient(env)
        wp.update_page_content(saved["capture_page_id"], package["capture_html"])
    except WpError as error:
        return {"message": f"{slug}: found the form but WP update failed ({error})", "attached": False, "slug": slug, "edit_url": None}

    package_json_path.parent.joinpath("capture_page.html").write_text(package["capture_html"], encoding="utf-8")
    saved["form_attached"] = True
    package_json_path.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")

    wp_base = (env.get("WP_URL") or "").rstrip("/")
    edit_url = f"{wp_base}/wp-admin/post.php?post={saved['capture_page_id']}&action=edit"

    return {
        "message": f'{slug}: attached form "{matched_form["name"]}" ({matched_form["id"]}) to the capture page draft',
        "attached": True,
        "slug": slug,
        "edit_url": edit_url,
    }
