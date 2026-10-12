"""Creates the "<ebook> Instagram DM" workflow in GHL for a new ebook post.

GHL has no API to create or edit workflows, so this drives the GHL app in the
saved Chromium profile (C:\\DOMA-blog-migration\\ghl_chromium_profile), the same
steps done by hand on 2026-10-12:
  1. duplicate TEMPLATE ("10 Reports Instagram DM": Instagram comment trigger on
     the DOMA page, no Facebook trigger, Facebook messenger step that times out
     for Instagram contacts, then the Instagram DM with a "Get the guide" button);
  2. in the copy: keyword in the trigger's "Contains phrase", then the message
     and button link in the 3 messenger steps;
  3. publish, and confirm through the public API that it is published.

Click positions are for a 1500x950 window and break if GHL changes its
builder. Every message is checked before saving; a step that does not
validate is cancelled and the run fails, leaving the copy as a draft (a draft
sends nothing). When the GHL session expires the app shows the sign-in page:
log in again in that profile (headed) and rerun.
"""

from __future__ import annotations

import time
from pathlib import Path

from scripts.ebook_pipeline.ghl_client import GhlClient

PROFILE_DIR = r"C:\DOMA-blog-migration\ghl_chromium_profile"
LOCATION = "GrNFQqzlomqc5ChSzwfF"
APP = f"https://app.brighteridea.com/v2/location/{LOCATION}/automation"
TEMPLATE = "10 Reports Instagram DM"
TEMPLATE_URL_PART = "the-10-reports"
NODES = ((760, 532), (500, 852), (1020, 852))  # Facebook messenger, Instagram (timeout branch), Instagram (click branch)


class DmWorkflowError(RuntimeError):
    pass


def keyword_variants(keyword: str) -> list[str]:
    # Not known whether GHL's "Contains phrase" ignores case, so the trigger
    # carries REPORTS, Reports and reports.
    return list(dict.fromkeys([keyword.upper(), keyword.capitalize(), keyword.lower()]))


def set_trigger_keywords(page, keyword: str, replace: bool) -> None:
    page.mouse.click(650, 285)
    page.wait_for_timeout(4000)
    page.mouse.click(1300, 668)
    if replace:
        for _ in range(6):
            page.keyboard.press("Backspace")
    body = _frame(page).locator("body").inner_text()
    for word in keyword_variants(keyword):
        if replace or word not in body.splitlines():
            page.keyboard.type(word, delay=40)
            page.keyboard.press("Enter")
            page.wait_for_timeout(500)
    panel = _frame(page).locator("body").inner_text().split("Contains phrase", 1)[-1].split("Post type", 1)[0]
    # The chip box shows two keywords and collapses the rest into "+N".
    lines = [l.strip() for l in panel.splitlines() if l.strip()]
    hidden = sum(int(l[1:]) for l in lines if l.startswith("+") and l[1:].isdigit())
    shown = [l for l in lines if not l.startswith("+")]
    missing = [] if (keyword.upper() in shown and len(shown) + hidden >= len(keyword_variants(keyword))) else keyword_variants(keyword)
    if missing or (replace and "REPORTS" in panel and keyword.upper() != "REPORTS"):
        page.mouse.click(1313, 903)
        raise DmWorkflowError(f"trigger keywords not set ({missing})")
    page.mouse.click(1415, 903)  # Save trigger
    page.wait_for_timeout(4000)


def add_keyword_variants(workflow_id: str, keyword: str) -> None:
    """For a workflow made before keyword_variants existed."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE_DIR, headless=True, viewport={"width": 1500, "height": 950})
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())
        try:
            page.goto(f"{APP}/workflow/{workflow_id}", timeout=90000)
            _wait_text(page, "Instagram interactive")
            page.wait_for_timeout(4000)
            set_trigger_keywords(page, keyword, replace=False)
            page.screenshot(path=str(Path(__file__).resolve().parents[2] / ".scratch" / f"dm_{keyword}.png"))
        finally:
            ctx.close()


def _frame(page):
    for f in page.frames:
        if "automation-workflows" in f.url:
            return f
    raise DmWorkflowError("workflow builder frame not found")


def _wait_text(page, text: str, seconds: int = 80) -> None:
    end = time.time() + seconds
    while time.time() < end:
        page.wait_for_timeout(2000)
        for f in page.frames:
            try:
                if text in f.locator("body").inner_text():
                    return
            except Exception:
                pass
        try:
            if "Sign into your account" in page.locator("body").inner_text():
                raise DmWorkflowError("GHL session expired: log in again in ghl_chromium_profile")
        except DmWorkflowError:
            raise
        except Exception:
            pass
    raise DmWorkflowError(f'"{text}" did not show up in the GHL app')


def message_text(title: str, url: str) -> list[str]:
    return ["Hi {{contact.first_name}}!", "", "Thanks for jumping in \U0001F44B", "",
            f"Here's your copy of {title}:", url, "", "Hope it helps!"]


def _write_message(page, title: str, url: str) -> None:
    f = _frame(page)
    editors = f.locator('[contenteditable="true"]', has_text="Here's your copy")
    if editors.count() == 0:
        raise DmWorkflowError("message editor not found")
    ed = editors.first.element_handle()
    ed.click()
    page.wait_for_timeout(500)
    ed.evaluate("e=>{const r=document.createRange();r.selectNodeContents(e);const s=window.getSelection();s.removeAllRanges();s.addRange(r);}")
    page.keyboard.press("Delete")
    page.wait_for_timeout(800)
    lines = message_text(title, url)
    for i, line in enumerate(lines):
        if line:
            page.keyboard.insert_text(line)
        if i < len(lines) - 1:
            page.keyboard.press("Enter")


def _set_button_url(page, url: str) -> int:
    f = _frame(page)
    hits = [c for c in f.locator('[contenteditable="true"]').all()
            if (c.inner_text() or "").strip().startswith("https://dentalofficemanagers.com/")
            and "Here's your copy" not in (c.inner_text() or "")]
    for c in hits:
        c.scroll_into_view_if_needed()
        c.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.insert_text(url)
    return len(hits)


def create_dm_workflow(ghl: GhlClient, name: str, keyword: str, title: str, url: str, log=print) -> str:
    """Returns the id of the published workflow. Reuses one that already exists
    (published) under that name."""
    existing = [w for w in ghl.list_workflows() if w["name"] == name]
    if existing and existing[0].get("status") == "published":
        return existing[0]["id"]

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE_DIR, headless=True, viewport={"width": 1500, "height": 950})
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())
        try:
            if not existing:
                page.goto(f"{APP}/workflows", timeout=90000)
                _wait_text(page, "Workflows list")
                page.wait_for_timeout(3000)
                page.mouse.click(1330, 248)
                page.keyboard.type(TEMPLATE, delay=30)
                page.wait_for_timeout(6000)
                page.mouse.click(1432, 383)  # row menu
                page.wait_for_timeout(2000)
                page.mouse.click(1370, 305)  # Duplicate workflow
                page.wait_for_timeout(3000)
                page.mouse.click(860, 547)
                page.keyboard.press("Control+A")
                page.keyboard.type(name, delay=20)
                page.mouse.click(1072, 627)  # Create
                page.wait_for_timeout(8000)
                existing = [w for w in ghl.list_workflows() if w["name"] == name]
                if not existing:
                    raise DmWorkflowError(f'copy "{name}" was not created')
            wid = existing[0]["id"]
            log(f"DM workflow draft {wid}")

            page.goto(f"{APP}/workflow/{wid}", timeout=90000)
            _wait_text(page, "Instagram interactive")
            page.wait_for_timeout(4000)

            set_trigger_keywords(page, keyword, replace=True)

            for i, node in enumerate(NODES):
                for attempt in range(2):  # the previous panel may still be closing
                    page.wait_for_timeout(2000)
                    page.mouse.click(*node)
                    page.wait_for_timeout(4000)
                    try:
                        _write_message(page, title, url)
                    except DmWorkflowError:
                        page.mouse.click(1317, 903)
                        continue
                    page.wait_for_timeout(1000)
                    buttons = _set_button_url(page, url)
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(1000)
                    text = _frame(page).locator('[contenteditable="true"]', has_text="Here's your copy").first.inner_text()
                    ok = (text.count("Here's your copy") == 1 and url in text
                          and TEMPLATE_URL_PART not in text and "10 Reports" not in text and buttons == 1)
                    if ok:
                        page.mouse.click(1417, 903)  # Save action
                        page.wait_for_timeout(4000)
                        break
                    page.mouse.click(1317, 903)  # Cancel
                else:
                    raise DmWorkflowError(f"messenger step {i} did not validate")

            page.mouse.click(1395, 80)  # Draft/Publish toggle
            page.wait_for_timeout(6000)
        finally:
            ctx.close()

    status = [w.get("status") for w in ghl.list_workflows() if w["id"] == wid]
    if status != ["published"]:
        raise DmWorkflowError(f"workflow {wid} is not published (status {status})")
    return wid
