"""On-page SEO audit: crawl the site's own pages (WordPress + Elementor +
custom code) and check the on-page basics that Search Console itself
doesn't check - title/meta description length, missing or duplicate H1s,
images without alt text, thin content, missing canonical tag, missing
structured data. This is a plain HTTP GET + HTML parse of DOMA's own public
pages, no API or credentials involved.

Findings (thresholds, why-picked) are computed in app.py:seo_onpage_summary
from the raw fields stored here - this script only measures, it doesn't
judge, so the thresholds can be tuned in one place without re-crawling.

Some DOMA page templates (the /post/ blog template in particular) inject
the H1 and article body via client-side JavaScript, so a plain HTTP GET
sees an empty heading and ~259 words even though a real visitor (and
Googlebot) sees the full article. To avoid flagging those as false
positives, any page that fails the H1 or thin-content check gets a single
headless-browser recheck (Playwright/Chromium) before being recorded - the
JS-rendered numbers replace the raw-HTML ones when they're available. If
Playwright isn't installed, the script still runs fine on the raw-HTML
numbers alone (see `_recheck_with_browser`).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402
from scripts import sitemap_utils  # noqa: E402

MAX_PAGES_TO_AUDIT = 60
MAX_POSTS_TO_AUDIT = 25
REQUEST_TIMEOUT = 20


class SeoAuditError(RuntimeError):
    pass


def _fetch_page(url: str) -> dict[str, object] | None:
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as error:
        raise SeoAuditError("requests/beautifulsoup4 not installed. Run: pip install -r requirements.txt") from error

    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "doma-dashboard-seo-audit"})
    except requests.RequestException as error:
        return {"url": url, "http_status": 0, "fetch_error": str(error)[:200]}

    if response.status_code != 200 or "text/html" not in response.headers.get("Content-Type", ""):
        return {"url": url, "http_status": response.status_code, "fetch_error": ""}

    soup = BeautifulSoup(response.text, "html.parser")

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    meta_description = (meta_desc_tag.get("content") or "").strip() if meta_desc_tag else ""

    h1_tags = soup.find_all("h1")
    h1_count = len(h1_tags)

    def _is_tracking_pixel(img) -> bool:
        # 1x1 / display:none images (Facebook Pixel noscript fallback, etc)
        # are invisible on purpose - they should have no alt text, and
        # flagging them isn't a real finding.
        w, h = img.get("width"), img.get("height")
        if w in ("1", 1) and h in ("1", 1):
            return True
        style = (img.get("style") or "").replace(" ", "")
        if "display:none" in style:
            return True
        # aria-hidden="true" (directly on the image, or its immediate parent -
        # a common pattern for decorative hero background images) means an
        # empty alt is the *correct* accessibility choice, not a finding.
        if img.get("aria-hidden") == "true":
            return True
        parent = img.parent
        if parent is not None and parent.get("aria-hidden") == "true":
            return True
        return False

    images = [img for img in soup.find_all("img") if not _is_tracking_pixel(img)]
    images_total = len(images)
    images_missing_alt = sum(1 for img in images if not (img.get("alt") or "").strip())

    canonical_tag = soup.find("link", attrs={"rel": "canonical"})
    canonical_url = (canonical_tag.get("href") or "").strip() if canonical_tag else ""

    has_schema = bool(soup.find("script", attrs={"type": "application/ld+json"}))

    # Word count last - decomposing scripts/styles must happen after every
    # check above, or has_schema (and anything else reading <script>/<style>
    # content) would always see an empty tree.
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    body = soup.find("body")
    word_count = len(body.get_text(separator=" ", strip=True).split()) if body else 0

    return {
        "url": url,
        "http_status": response.status_code,
        "fetch_error": "",
        "title": title,
        "title_length": len(title),
        "meta_description": meta_description,
        "meta_length": len(meta_description),
        "h1_count": h1_count,
        "images_total": images_total,
        "images_missing_alt": images_missing_alt,
        "word_count": word_count,
        "has_canonical": bool(canonical_url),
        "canonical_url": canonical_url,
        "has_schema": has_schema,
    }


def _needs_js_recheck(result: dict[str, object]) -> bool:
    return result.get("http_status") == 200 and (result.get("h1_count") == 0 or (result.get("word_count") or 0) < 300)


def _recheck_with_browser(browser, url: str) -> dict[str, object] | None:
    from bs4 import BeautifulSoup

    try:
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) doma-dashboard-seo-audit")
        # "domcontentloaded" + a fixed buffer, not "networkidle" - pages with
        # a chat widget/analytics beacon never go fully idle and would time
        # out here even though the content we care about rendered long ago.
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        html = page.content()
        page.close()
    except Exception as error:  # noqa: BLE001 - a failed recheck just falls back to the raw-HTML numbers
        print(f"  WARNING: JS recheck failed for {url} ({error})", file=sys.stderr)
        return None

    soup = BeautifulSoup(html, "html.parser")
    h1_count = len(soup.find_all("h1"))
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    body = soup.find("body")
    word_count = len(body.get_text(separator=" ", strip=True).split()) if body else 0
    return {"h1_count": h1_count, "word_count": word_count}


def fetch_urls_to_audit(site_url: str) -> list[str]:
    urls = list(sitemap_utils.fetch_pages(site_url, limit=MAX_PAGES_TO_AUDIT))
    urls += [loc for loc, _ in sitemap_utils.fetch_recent_posts(site_url, limit=MAX_POSTS_TO_AUDIT)]
    seen: set[str] = set()
    deduped = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def audit_pages(urls: list[str]) -> list[dict[str, object]]:
    results = []
    for url in urls:
        result = _fetch_page(url)
        if result is not None:
            result["js_rechecked"] = False
            results.append(result)

    to_recheck = [r for r in results if _needs_js_recheck(r)]
    if to_recheck:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print(
                f"  NOTE: playwright not installed - skipping JS recheck for {len(to_recheck)} "
                "page(s) that may be false positives (H1/thin content). Run: "
                "pip install playwright && playwright install chromium",
                file=sys.stderr,
            )
        else:
            print(f"  Rechecking {len(to_recheck)} page(s) with a real browser (H1/thin-content false-positive guard)...")
            with sync_playwright() as p:
                browser = p.chromium.launch()
                for row in to_recheck:
                    recheck = _recheck_with_browser(browser, row["url"])
                    if recheck is not None:
                        row["h1_count"] = recheck["h1_count"]
                        row["word_count"] = recheck["word_count"]
                        row["js_rechecked"] = True
                browser.close()

    return results


def store_audit(rows: list[dict[str, object]]) -> None:
    with dashboard_app.db() as con:
        con.execute("DELETE FROM seo_onpage_audit")
        con.executemany(
            """
            INSERT INTO seo_onpage_audit
                (url, http_status, fetch_error, title, title_length, meta_description, meta_length,
                 h1_count, images_total, images_missing_alt, word_count, has_canonical, canonical_url,
                 has_schema, js_rechecked, checked_at)
            VALUES (:url, :http_status, :fetch_error, :title, :title_length, :meta_description, :meta_length,
                    :h1_count, :images_total, :images_missing_alt, :word_count, :has_canonical, :canonical_url,
                    :has_schema, :js_rechecked, CURRENT_TIMESTAMP)
            """,
            [
                {
                    "url": r.get("url", ""),
                    "http_status": r.get("http_status", 0),
                    "fetch_error": r.get("fetch_error", ""),
                    "title": r.get("title", ""),
                    "title_length": r.get("title_length", 0),
                    "meta_description": r.get("meta_description", ""),
                    "meta_length": r.get("meta_length", 0),
                    "h1_count": r.get("h1_count", 0),
                    "images_total": r.get("images_total", 0),
                    "images_missing_alt": r.get("images_missing_alt", 0),
                    "word_count": r.get("word_count", 0),
                    "has_canonical": int(bool(r.get("has_canonical", False))),
                    "canonical_url": r.get("canonical_url", ""),
                    "has_schema": int(bool(r.get("has_schema", False))),
                    "js_rechecked": int(bool(r.get("js_rechecked", False))),
                }
                for r in rows
            ],
        )


def main() -> int:
    env = load_env_file()
    dashboard_app.init_db()
    site_url = env.get("GSC_SITE_URL")
    if not site_url:
        raise SeoAuditError("Missing GSC_SITE_URL in .env")

    urls = fetch_urls_to_audit(site_url)
    if not urls:
        raise SeoAuditError("Could not read the XML sitemap - no pages to audit.")

    results = audit_pages(urls)
    store_audit(results)
    log_sync(dashboard_app, "seo_onpage_audit", "ok", f"{len(results)} pages audited")
    print(f"On-page SEO audit complete: {len(results)} pages checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
