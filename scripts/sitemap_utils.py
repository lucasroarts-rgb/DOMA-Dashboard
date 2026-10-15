"""Shared WordPress/Yoast SEO sitemap reader - used by sync_gsc.py (index
coverage + content gaps) and sync_ga4.py (recent-post performance) so both
scripts agree on which URLs count as "pages" vs "recent posts" without
depending on each other's run order or database state."""

from __future__ import annotations

import html
import re
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

SITEMAP_INDEX_PATH = "/sitemap_index.xml"
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class SitemapError(RuntimeError):
    pass


def _fetch_xml(url: str) -> ElementTree.Element | None:
    try:
        import requests
    except ImportError as error:
        raise SitemapError("requests is not installed. Run: pip install -r requirements.txt") from error

    response = requests.get(url, timeout=20, headers={"User-Agent": "doma-dashboard-sync"})
    if response.status_code != 200:
        return None
    try:
        return ElementTree.fromstring(response.content)
    except ElementTree.ParseError:
        return None


def _urls_from_sitemap(sitemap_url: str) -> list[tuple[str, str]]:
    root = _fetch_xml(sitemap_url)
    if root is None:
        return []
    entries = []
    for url_node in root.findall(".//sm:url", SITEMAP_NS):
        loc = url_node.find("sm:loc", SITEMAP_NS)
        lastmod = url_node.find("sm:lastmod", SITEMAP_NS)
        if loc is not None and loc.text:
            entries.append((loc.text, lastmod.text if lastmod is not None else ""))
    return entries


def _sub_sitemaps(site_url: str) -> list[str]:
    index_root = _fetch_xml(urljoin(site_url, SITEMAP_INDEX_PATH))
    if index_root is None:
        return []
    return [node.text for node in index_root.findall(".//sm:sitemap/sm:loc", SITEMAP_NS) if node.text]


def fetch_pages(site_url: str, limit: int = 60) -> list[str]:
    """Static pages (About, Pricing, etc) - order as they appear in the sitemap."""
    for sitemap_url in _sub_sitemaps(site_url):
        if "page-sitemap" in sitemap_url:
            return [loc for loc, _ in _urls_from_sitemap(sitemap_url)[:limit]]
    return []


def fetch_recent_posts(site_url: str, limit: int = 20) -> list[tuple[str, str]]:
    """(url, published date) for the most recently published blog posts.
    See fetch_recent_posts_with_titles for the version that keeps the title.
    """
    return [(url, published) for url, published, _ in fetch_recent_posts_with_titles(site_url, limit)]


def fetch_recent_posts_with_titles(site_url: str, limit: int = 20) -> list[tuple[str, str, str | None]]:
    """(url, published date, title) for the most recently published blog posts.
    Title is the rendered WordPress title, HTML entities decoded; None when
    the sitemap fallback was used (it has no titles).

    Uses the WordPress REST API (real publish date). The sitemap's lastmod
    changes on every edit, so on 2026-10-14 a bulk edit of old sponsor posts
    made them show up as "published Oct 14" and pushed the real new posts
    out of the Blog tab. The sitemap is only the fallback now."""
    posts = _recent_posts_from_wordpress(site_url, limit)
    if posts:
        return posts
    for sitemap_url in _sub_sitemaps(site_url):
        if "post-sitemap" in sitemap_url:
            entries = _urls_from_sitemap(sitemap_url)
            entries.sort(key=lambda entry: entry[1], reverse=True)
            return [(loc, lastmod, None) for loc, lastmod in entries[:limit]]
    return []


def _wp_session(site_url: str):
    """requests session + REST base for the DOMA WordPress site. The browser
    User-Agent is needed: the host's firewall blocks the default one."""
    import requests

    from scripts.env_utils import load_env_file

    env = load_env_file()
    session = requests.Session()
    session.headers["User-Agent"] = BROWSER_UA
    if env.get("WP_USERNAME") and env.get("WP_APP_PASSWORD"):
        session.auth = (env["WP_USERNAME"], env["WP_APP_PASSWORD"])
    return session, (env.get("WP_URL") or site_url).rstrip("/") + "/wp-json"


BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def wp_title(raw: str | None) -> str | None:
    return html.unescape(raw).strip() if raw else None


def fetch_all_posts(site_url: str) -> list[tuple[str, str | None, str]]:
    """(path, title, published date) for every published post. Used to tell
    real blog posts apart from the speaker directory and events, which also
    live under /post/."""
    session, api = _wp_session(site_url)
    posts: list[tuple[str, str | None, str]] = []
    page = 1
    while True:
        response = session.get(
            f"{api}/wp/v2/posts",
            params={"per_page": 100, "page": page, "status": "publish", "_fields": "link,title,date_gmt"},
            timeout=60,
        )
        if response.status_code != 200:
            break
        items = response.json()
        posts += [(urlparse(p["link"]).path, wp_title(p["title"]["rendered"]), p["date_gmt"] + "+00:00") for p in items]
        if len(items) < 100 or page >= int(response.headers.get("X-WP-TotalPages", page)):
            break
        page += 1
    return posts


# Free-guide landing page = published WP page that embeds a GoHighLevel form.
# Same rule as C:\DOMA-blog-migration\75_resources_free_guides.py, keep them
# in sync: thank-you pages and these non-guide pages with forms are skipped.
NOT_GUIDES = {"affiliate-program", "book-speaker", "doma-coaches-consultants-network", "community", "home", "ai-guides",
              "influence-alliance", "membership", "partner", "reviews"}
GHL_FORM_RE = re.compile(r"leadconnectorhq\.com/widget/form|msgsndr|link\.dentalofficemanagers\.com/widget/form|form_embed", re.I)


def fetch_free_guide_pages(site_url: str) -> list[tuple[str, str | None]]:
    """(path, title) of every free-guide landing page. Needs the WP
    Application Password (context=edit returns the Elementor data)."""
    session, api = _wp_session(site_url)
    guides: list[tuple[str, str | None]] = []
    page = 1
    while True:
        response = session.get(
            f"{api}/wp/v2/pages",
            params={"context": "edit", "per_page": 100, "page": page, "status": "publish"},
            timeout=120,
        )
        if response.status_code != 200:
            break
        items = response.json()
        for p in items:
            body = (p["content"]["raw"] or "") + ((p.get("meta") or {}).get("_elementor_data") or "")
            if "thank-you" in p["slug"] or p["slug"] in NOT_GUIDES or p["link"].rstrip("/").endswith(".com") or not GHL_FORM_RE.search(body):
                continue
            guides.append((urlparse(p["link"]).path, wp_title(p["title"]["raw"])))
        if len(items) < 100:
            break
        page += 1
    return guides


def _recent_posts_from_wordpress(site_url: str, limit: int) -> list[tuple[str, str, str | None]]:
    try:
        import requests

        from scripts.env_utils import load_env_file

        env = load_env_file()
        base = (env.get("WP_URL") or site_url).rstrip("/")
        auth = (env["WP_USERNAME"], env["WP_APP_PASSWORD"]) if env.get("WP_USERNAME") and env.get("WP_APP_PASSWORD") else None
        response = requests.get(
            f"{base}/wp-json/wp/v2/posts",
            params={"per_page": min(limit, 100), "orderby": "date", "order": "desc", "status": "publish", "_fields": "link,date_gmt,title"},
            auth=auth,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
        )
        response.raise_for_status()
        return [(post["link"], post["date_gmt"] + "+00:00", wp_title((post.get("title") or {}).get("rendered"))) for post in response.json()][:limit]
    except Exception:  # noqa: BLE001 - any failure falls back to the sitemap
        return []


def fetch_all_urls_generic(site_url: str, max_urls: int = 800, max_sub_sitemaps: int = 15) -> list[tuple[str, str]]:
    """(url, lastmod) for every URL in a site's sitemap - unlike fetch_pages/
    fetch_recent_posts above, this doesn't assume WordPress/Yoast sitemap
    naming ("page-sitemap", "post-sitemap") since it's used for arbitrary
    competitor sites on unknown platforms. Tries /sitemap.xml first (most
    common), falls back to /sitemap_index.xml, and recurses into a sitemap
    index one level (real-world indexes aren't nested deeper than that in
    practice). Capped at max_urls/max_sub_sitemaps so one huge competitor
    site can't blow up the sync."""
    root = _fetch_xml(urljoin(site_url, "/sitemap.xml"))
    if root is None:
        root = _fetch_xml(urljoin(site_url, SITEMAP_INDEX_PATH))
    if root is None:
        root = _fetch_xml(urljoin(site_url, "/sitemap-index.xml"))  # e.g. DentalPost
    if root is None:
        return []

    direct_urls = [
        (loc.text, (lastmod.text if lastmod is not None else ""))
        for url_node in root.findall(".//sm:url", SITEMAP_NS)
        for loc in [url_node.find("sm:loc", SITEMAP_NS)]
        for lastmod in [url_node.find("sm:lastmod", SITEMAP_NS)]
        if loc is not None and loc.text
    ]
    if direct_urls:
        return direct_urls[:max_urls]

    sub_sitemaps = [node.text for node in root.findall(".//sm:sitemap/sm:loc", SITEMAP_NS) if node.text]
    # Blog/post sitemaps first - that's where new content shows up, and on a
    # large site the max_urls cap would otherwise truncate before ever
    # reaching them if they happened to sort later (e.g. after
    # category/tag/podcast sitemaps).
    sub_sitemaps.sort(key=lambda u: 0 if "post" in u.lower() or "blog" in u.lower() else 1)
    sub_sitemaps = sub_sitemaps[:max_sub_sitemaps]
    all_urls: list[tuple[str, str]] = []
    for sitemap_url in sub_sitemaps:
        all_urls.extend(_urls_from_sitemap(sitemap_url))
        if len(all_urls) >= max_urls:
            break
    return all_urls[:max_urls]
