"""Shared WordPress/Yoast SEO sitemap reader - used by sync_gsc.py (index
coverage + content gaps) and sync_ga4.py (recent-post performance) so both
scripts agree on which URLs count as "pages" vs "recent posts" without
depending on each other's run order or database state."""

from __future__ import annotations

from urllib.parse import urljoin
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
    """(url, lastmod) for the most recently modified/published blog posts."""
    for sitemap_url in _sub_sitemaps(site_url):
        if "post-sitemap" in sitemap_url:
            posts = _urls_from_sitemap(sitemap_url)
            posts.sort(key=lambda entry: entry[1], reverse=True)
            return posts[:limit]
    return []
