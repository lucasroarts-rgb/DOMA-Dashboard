"""WordPress REST helper for the eBook pipeline - media upload + draft pages.

Reuses the exact auth pattern documented in README.md ("Editar SEO do
WordPress via API"): HTTP Basic auth with an Application Password (not the
login password), plus a spoofed browser User-Agent because Bluehost's WAF
(Mod_Security) 406s the default `requests` UA.

Pages must actually be Elementor pages, Elementor Canvas template, with a
featured image and excerpt set (the site's real standard, confirmed live
2026-08-21 against page 4841 on dentalofficemanagers.com):
- `template=elementor_canvas` is a normal, REST-writable core field.
- `_elementor_data` **is** REST-writable on this install (unusual - core
  WordPress doesn't expose it, and Elementor doesn't register it for REST by
  default either; this site has some existing bridge, same pattern as the
  Yoast meta fields documented in the main README's WP section). Confirmed
  by writing a custom widget structure and reading it back unchanged.
- Don't rely on any auto-conversion from `content` - on first touch this site
  auto-wrapped raw content into a Text Editor widget, which runs everything
  through wpautop() and can mangle embedded <style>/<script> tags. Write an
  explicit `html` widget instead, which outputs the string verbatim.

Pages are always created as drafts - this pipeline never publishes to the
live site on its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class WpError(RuntimeError):
    pass


def _elementor_meta(html: str) -> dict:
    data = [
        {
            "id": "c0000001",
            "elType": "container",
            "settings": {"content_width": "full"},
            "elements": [
                {
                    "id": "w0000001",
                    "elType": "widget",
                    "widgetType": "html",
                    "settings": {"html": html},
                    "elements": [],
                }
            ],
            "isInner": False,
        }
    ]
    return {
        "_elementor_data": json.dumps(data),
        "_elementor_edit_mode": "builder",
        "_elementor_template_type": "wp-page",
    }


class WpClient:
    def __init__(self, env: dict[str, str]):
        self.base_url = (env.get("WP_URL") or "").rstrip("/")
        username = env.get("WP_USERNAME")
        app_password = env.get("WP_APP_PASSWORD")
        if not self.base_url or not username or not app_password:
            raise WpError("Missing WP_URL / WP_USERNAME / WP_APP_PASSWORD in .env")
        self.auth = (username, app_password)

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"User-Agent": BROWSER_UA}
        if extra:
            headers.update(extra)
        return headers

    def upload_media(self, file_path: Path, filename: str) -> dict:
        # Multipart form-data, not a raw binary POST with a Content-Disposition
        # header - the raw-body approach 406s on Bluehost's Mod_Security WAF
        # for some content-types (confirmed live on a .webp upload); standard
        # multipart never triggered it.
        content_type = "image/webp" if filename.endswith(".webp") else "image/jpeg"
        with open(file_path, "rb") as handle:
            response = requests.post(
                f"{self.base_url}/wp-json/wp/v2/media",
                auth=self.auth,
                headers=self._headers(),
                files={"file": (filename, handle, content_type)},
                timeout=60,
            )
        if response.status_code not in (200, 201):
            raise WpError(f"Media upload failed ({response.status_code}): {response.text[:300]}")
        payload = response.json()
        return {"id": payload["id"], "url": payload["source_url"]}

    def create_draft_page(
        self,
        title: str,
        slug: str,
        html: str,
        excerpt: str = "",
        featured_media: int | None = None,
        meta_description: str = "",
        noindex: bool = False,
    ) -> dict:
        # Yoast fields, confirmed REST-writable on this install (Code Snippet
        # #9 registers them with show_in_rest - same bridge that already
        # exposes _elementor_data). Every ebook page pair was missing these:
        # capture pages had no meta description, thank-you pages stayed
        # indexable (thin/duplicate content Google shouldn't rank) - flagged
        # live by another session working the same site's SEO.
        meta = _elementor_meta(html)
        if meta_description:
            meta["_yoast_wpseo_metadesc"] = meta_description
        if noindex:
            meta["_yoast_wpseo_meta-robots-noindex"] = "1"

        body = {
            "title": title,
            "slug": slug,
            "content": html,
            "excerpt": excerpt,
            "status": "draft",
            "template": "elementor_canvas",
            "meta": meta,
        }
        if featured_media:
            body["featured_media"] = featured_media

        response = requests.post(
            f"{self.base_url}/wp-json/wp/v2/pages",
            auth=self.auth,
            headers=self._headers({"Content-Type": "application/json"}),
            json=body,
            timeout=60,
        )
        if response.status_code not in (200, 201):
            raise WpError(f"Page create failed ({response.status_code}): {response.text[:300]}")
        payload = response.json()
        edit_url = f"{self.base_url}/wp-admin/post.php?post={payload['id']}&action=edit"
        return {"id": payload["id"], "edit_url": edit_url, "preview_url": payload.get("link", "")}

    def update_page_content(self, page_id: int, html: str) -> None:
        response = requests.post(
            f"{self.base_url}/wp-json/wp/v2/pages/{page_id}",
            auth=self.auth,
            headers=self._headers({"Content-Type": "application/json"}),
            json={"content": html, "meta": _elementor_meta(html)},
            timeout=60,
        )
        if response.status_code not in (200, 201):
            raise WpError(f"Page update failed ({response.status_code}): {response.text[:300]}")
