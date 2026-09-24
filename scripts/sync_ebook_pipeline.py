"""eBook capture-page pipeline entry point.

Run manually right after uploading a new cover+PDF pair to the shared Drive
folder (see RODAR_EBOOK_AGORA.bat), or let the Mon/Wed/Fri scheduled task
(AGENDAR_AUTOMACAO_EBOOKS.bat) catch it as a safety net.

For each new pair found in DRIVE_EBOOKS_FOLDER_ID:
  1. Download the PDF + cover image.
  2. Extract title/subtitle/benefits from the PDF (pdf_extract.py).
  3. Upload the cover to WordPress Media Library, the PDF to GHL Media
     Storage (wp_client.py / ghl_client.py).
  4. Generate the capture page, thank-you page, delivery email, tag/slug/form
     name (copy_generator.py).
  5. Create the two pages in WordPress as **drafts** - never published
     automatically.
  6. Write everything to ebook_packages/{slug}/ for Lucas to pick up:
     duplicating the GHL form/workflow and publishing the pages is a manual
     (or Claude-Browser-assisted) step from here, see package_details.md.

Already-processed Drive file IDs are tracked in data/ebook_pipeline_state.json
so re-running (e.g. the scheduled safety-net run) never reprocesses the same
files or creates duplicate WP/GHL uploads.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.env_utils import load_env_file  # noqa: E402
from scripts.ebook_pipeline import drive_client, pdf_extract, copy_generator  # noqa: E402
from scripts.ebook_pipeline.wp_client import WpClient, WpError  # noqa: E402
from scripts.ebook_pipeline.ghl_client import GhlClient, GhlError  # noqa: E402
from scripts.ebook_pipeline.attach import try_attach  # noqa: E402
from scripts.ebook_pipeline.notify import notify  # noqa: E402

STATE_PATH = ROOT / "data" / "ebook_pipeline_state.json"
PACKAGES_DIR = ROOT / "ebook_packages"
DOWNLOAD_DIR = ROOT / "data" / "ebook_downloads"

FIRESTORE_PROJECT_ID = "doma-dshboard"
FIRESTORE_EMAILS_URL = f"https://firestore.googleapis.com/v1/projects/{FIRESTORE_PROJECT_ID}/databases/(default)/documents/ebook_email_deliveries"


def save_email_to_firestore(package: dict, capture_url: str, thank_you_url: str, pdf_url: str) -> None:
    """Durable copy of the delivery email, independent of which machine
    built it - the local ebook_packages/{slug}/ folder only exists on
    whichever disk wrote it, and when that's Render's (Content-Calendar-
    triggered pages), the disk is ephemeral and wiped on every redeploy, so
    the email was otherwise gone the moment anyone needed it (confirmed
    live 2026-09-24, "They Didn't Say No" ebook - rebuilt by hand from the
    published page). Firestore's the one place both the local machine and
    Render already write to, so every ebook's email lands somewhere durable
    regardless of which one created it. Keyed by slug so a re-run overwrites
    instead of duplicating."""
    body = {
        "fields": {
            "slug": {"stringValue": package["slug"]},
            "title": {"stringValue": package["title"]},
            "email_subject": {"stringValue": package["email_subject"]},
            "email_preview": {"stringValue": package["email_preview"]},
            "email_body": {"stringValue": package["email_body"]},
            "capture_url": {"stringValue": capture_url},
            "thank_you_url": {"stringValue": thank_you_url},
            "pdf_url": {"stringValue": pdf_url},
            "created_at": {"integerValue": str(int(datetime.now(timezone.utc).timestamp() * 1000))},
        }
    }
    try:
        response = requests.patch(f"{FIRESTORE_EMAILS_URL}/{package['slug']}", json=body, timeout=30)
        if response.status_code != 200:
            print(f"WARNING: could not save email to Firestore ({response.status_code}): {response.text[:200]}", file=sys.stderr)
    except requests.RequestException as error:
        print(f"WARNING: could not save email to Firestore: {error}", file=sys.stderr)


class PipelineError(RuntimeError):
    pass


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"processed_file_ids": []}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def process_pair(env: dict, pair: dict) -> dict:
    pdf_meta, image_meta = pair["pdf"], pair["image"]

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = DOWNLOAD_DIR / pdf_meta["name"]
    image_path = DOWNLOAD_DIR / image_meta["name"]
    drive_client.download_file(env, pdf_meta["id"], pdf_path)
    drive_client.download_file(env, image_meta["id"], image_path)

    extracted = pdf_extract.extract(pdf_path)
    slug = copy_generator.slugify(extracted["title"])
    form_name = f"Ebook - {extracted['title'].strip()}"

    wp = WpClient(env)
    image_ext = Path(image_meta["name"]).suffix.lstrip(".") or "jpeg"
    wp_filename = f"{slug}-img.{image_ext}"
    cover = wp.upload_media(image_path, wp_filename)

    ghl = GhlClient(env)
    pdf_filename = f"{slug}.pdf"
    pdf_upload = ghl.upload_media(pdf_path, pdf_filename)

    # Check *before* generating the capture page HTML - if this ebook's form
    # already exists in GHL (e.g. it was set up before this pipeline existed),
    # embed the real working form instead of leaving a placeholder.
    matched_form = ghl.find_form_by_title(form_name)
    matched_workflow = ghl.find_workflow_by_title(form_name)

    ghl_domain = env.get("GHL_WIDGET_DOMAIN", "")
    package = copy_generator.build_package(extracted, cover["url"], pdf_upload["url"], ghl_domain, matched_form)

    capture_page = wp.create_draft_page(
        f"{package['title']} - Free Guide", package["slug"], package["capture_html"],
        excerpt=package["excerpt"], featured_media=cover["id"],
        meta_description=package["excerpt"],
    )
    ty_page = wp.create_draft_page(
        f"{package['title']} - Thank You", f"{package['slug']}-thank-you", package["thank_you_html"],
        excerpt=package["excerpt"], featured_media=cover["id"],
        noindex=True,
    )

    write_package(env, package, extracted, cover, pdf_upload, capture_page, ty_page, matched_form, matched_workflow)

    notify(
        "Novo eBook processado",
        f"{package['title']}\nDraft da capture page pronto pra revisar:\n{capture_page['edit_url']}",
    )

    return {
        "slug": package["slug"],
        "title": package["title"],
        "pdf_file_id": pdf_meta["id"],
        "image_file_id": image_meta["id"],
    }


def write_package(env, package, extracted, cover, pdf_upload, capture_page, ty_page, matched_form, matched_workflow) -> None:
    out_dir = PACKAGES_DIR / package["slug"]
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "capture_page.html").write_text(package["capture_html"], encoding="utf-8")
    (out_dir / "thank_you_page.html").write_text(package["thank_you_html"], encoding="utf-8")
    email_delivery_text = (
        f"Subject: {package['email_subject']}\n"
        f"Preview: {package['email_preview']}\n\n"
        f"{package['email_body']}\n"
    )
    (out_dir / "email_delivery.md").write_text(email_delivery_text, encoding="utf-8")
    # Plain .txt alongside the .md - Thalles pastes this straight into the
    # GHL automation's email step each time and wants a Notepad-openable
    # copy ready without digging through package.json/capture_page.html.
    (out_dir / "email_delivery.txt").write_text(email_delivery_text, encoding="utf-8")
    save_email_to_firestore(
        package,
        capture_page.get("preview_url", ""),
        ty_page.get("preview_url", ""),
        pdf_upload["url"],
    )

    wp_base = (env.get("WP_URL") or "").rstrip("/")
    ty_future_url = f"{wp_base}/{package['slug']}-thank-you/"

    # Structured data for scripts/attach_ebook_form.py to pick up later, once
    # the form has been duplicated in GHL - avoids re-downloading/re-extracting
    # the PDF just to patch one placeholder.
    (out_dir / "package.json").write_text(
        json.dumps(
            {
                "slug": package["slug"],
                "title": package["title"],
                "form_name": package["form_name"],
                "tag": package["tag"],
                "cover_url": cover["url"],
                "pdf_url": pdf_upload["url"],
                "capture_page_id": capture_page["id"],
                "thank_you_page_id": ty_page["id"],
                "thank_you_future_url": ty_future_url,
                "extracted": extracted,
                "form_attached": bool(matched_form),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    form_status = (
        f"already exists and is embedded live on the capture page draft: {matched_form['id']}"
        if matched_form
        else "NOT created yet - duplicate one of the existing Ebook forms, rename it, then run: "
        f"python scripts/attach_ebook_form.py {package['slug']}"
    )
    workflow_status = (
        f"already exists: {matched_workflow['id']}"
        if matched_workflow
        else "NOT created yet - duplicate one of the existing Ebook workflows and rename it"
    )

    details = f"""# {package['title']} - package details

- Slug: `{package['slug']}`
- GHL tag: `{package['tag']}`
- Cover (WordPress): {cover['url']}
- PDF (GoHighLevel): {pdf_upload['url']}
- Capture page (WP draft): {capture_page['edit_url']}
- Thank-you page (WP draft): {ty_page['edit_url']}
- Thank-you page future public URL (use this as the form's "redirect after
  submit" target in GHL - it will resolve once the page is published):
  {ty_future_url}
- GHL form "{package['form_name']}": {form_status}
- GHL workflow "{package['workflow_name']}": {workflow_status}

## Remaining manual steps (see CLAUDE.md rules 41-48)

1. If the form doesn't exist yet: duplicate an existing "Ebook - ..." form in
   GHL, rename it to "{package['form_name']}", set its "redirect after
   submit" to the Thank-you future URL above.
2. Run `python scripts/attach_ebook_form.py {package['slug']}` - it finds the
   new form by name and patches the real embed into the capture page draft
   (no more placeholder), no need to touch HTML by hand.
3. If the workflow doesn't exist yet: duplicate an existing "Ebook - ..."
   workflow, rename it, point its trigger at the new form, confirm it adds
   the `{package['tag']}` tag and sends the delivery email below with the PDF
   URL above.
4. Review both WP draft pages in Elementor, style-match them to the rest of
   the site, then publish.
5. Run the full automation test (real Drive upload disabled here - use a real
   test lead submission) before calling this ebook live.

## Delivery email

Subject: {package['email_subject']}

{package['email_body']}
"""
    (out_dir / "package_details.md").write_text(details, encoding="utf-8")


def main() -> int:
    env = load_env_file()
    state = load_state()
    processed_ids = set(state.get("processed_file_ids", []))

    try:
        files = drive_client.list_folder_files(env)
    except drive_client.DriveError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    pairs = drive_client.find_new_pairs(files, processed_ids)
    if not pairs:
        print("No new eBook pairs found in the Drive folder.")
    else:
        results = []
        for pair in pairs:
            try:
                result = process_pair(env, pair)
                results.append(result)
                processed_ids.add(result["pdf_file_id"])
                processed_ids.add(result["image_file_id"])
                print(f"OK: {result['title']} -> ebook_packages/{result['slug']}/")
            except (PipelineError, WpError, GhlError, pdf_extract.PdfExtractError) as error:
                print(f"WARNING: skipped a pair ({pair['pdf']['name']}): {error}", file=sys.stderr)

        state["processed_file_ids"] = sorted(processed_ids)
        state["last_run_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        print(f"Done. Processed {len(results)} new eBook(s).")

    # Always recheck pending forms, even on a run with no new Drive files -
    # this is what lets Lucas skip telling anyone the GHL form is ready.
    recheck_pending_forms(env)
    return 0


def recheck_pending_forms(env: dict) -> None:
    """Every run also checks every past ebook that's still waiting on its
    GHL form - the moment Lucas duplicates+renames it in GHL, the next
    scheduled run (within 30min) notices and patches the draft page
    automatically. He never has to tell anyone it's ready."""
    if not PACKAGES_DIR.exists():
        return
    for package_json in sorted(PACKAGES_DIR.glob("*/package.json")):
        result = try_attach(env, package_json)
        if "already attached" not in result["message"]:
            print(result["message"])
        if result["attached"]:
            notify(
                "eBook pronto pra revisar",
                f"{result['slug']}: form conectado, capture page pronta:\n{result['edit_url']}",
            )


if __name__ == "__main__":
    raise SystemExit(main())
