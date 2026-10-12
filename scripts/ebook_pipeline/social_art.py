"""Social post art for an ebook, in the look approved on 2026-10-12 (navy
background, DOMA badge, "FREE GUIDE", white + gold headline, gold keyword pill).

Two variants of the same art:
- instagram: "Comment the word <KEYWORD>, we'll send the free guide to your DMs"
  (the GHL "<ebook> Instagram DM" workflow answers the comment);
- facebook: "LINK IN THE COMMENTS" (sync_blog_social.py comments the link).

Rendered with headless Chromium at 1080x1350 and saved as JPEG, since the
Instagram Graph API only publishes JPEG.
"""

from __future__ import annotations

import html
import re
import tempfile
from pathlib import Path

from PIL import Image

BADGE = Path(__file__).resolve().parent / "templates" / "doma-badge.png"
WIDTH, HEIGHT = 1080, 1350

CHECK = ('<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#e5b24a" stroke-width="2.5" '
         'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"></path></svg>')

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;600;700;800;900&display=swap" rel="stylesheet">
<style>
body{{margin:0}}
.bg{{width:1080px;height:1350px;box-sizing:border-box;position:relative;overflow:hidden;font-family:'Montserrat',sans-serif;color:#fff;
display:flex;flex-direction:column;align-items:center;padding:64px 80px 0;
background:radial-gradient(ellipse 70% 45% at 85% 8%,rgba(56,92,214,.45),transparent 70%),radial-gradient(ellipse 60% 40% at 10% 95%,rgba(56,92,214,.35),transparent 70%),linear-gradient(180deg,#0a1633 0%,#0d1d47 55%,#0a1633 100%)}}
.gold{{background:linear-gradient(180deg,#f9dc8a 0%,#e5b24a 55%,#c8902c 100%);-webkit-background-clip:text;background-clip:text;color:transparent}}
.swoosh{{position:absolute;background:linear-gradient(90deg,transparent 0%,#e5b24a 35%,#f9dc8a 50%,#e5b24a 65%,transparent 100%)}}
.pt{{display:flex;align-items:center;gap:16px;padding:20px 24px;border:2px solid rgba(229,178,74,.55);border-radius:18px;background:rgba(10,22,51,.6);font-size:26px;font-weight:600;line-height:1.25}}
.pill{{padding:18px 64px;border-radius:999px;background:linear-gradient(180deg,#f9dc8a 0%,#e5b24a 60%,#c8902c 100%);color:#0a1633;font-weight:900;box-shadow:0 0 40px rgba(229,178,74,.45);text-align:center}}
</style></head><body><div class="bg">
<img src="{badge}" alt="" style="width:168px;height:168px;display:block">
<div style="margin-top:30px;display:flex;align-items:center;gap:18px"><div style="width:70px;height:2px;background:#e5b24a"></div>
<div style="font-size:24px;font-weight:700;letter-spacing:8px;color:#e5b24a">FREE GUIDE</div><div style="width:70px;height:2px;background:#e5b24a"></div></div>
<div style="flex:1;width:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px 0">
<div style="text-align:center;font-weight:900;line-height:1.04;font-size:{h1}px;text-transform:uppercase">{white}</div>
<div class="gold" style="text-align:center;font-weight:900;line-height:1.04;font-size:{h2}px;text-transform:uppercase;margin-top:6px">{gold}</div>
{subtitle}
{points}
</div>
<div style="margin-bottom:96px;display:flex;flex-direction:column;align-items:center;gap:14px">
<div style="font-size:30px;font-weight:600;color:#dbe3f7">{cta_top}</div>
<div class="pill" style="font-size:{pill_size}px;letter-spacing:{pill_ls}px">{pill}</div>
<div style="font-size:28px;font-weight:500;color:#dbe3f7">{cta_bottom}</div></div>
<div class="swoosh" style="left:0;right:0;bottom:70px;height:3px"></div>
<div class="swoosh" style="left:120px;right:120px;bottom:54px;height:2px;opacity:.6"></div>
</div></body></html>"""


def split_headline(title: str) -> tuple[str, str]:
    """White part + gold part. Splits after the first sentence or colon when the
    title has one ("Stop Collecting Ideas." / "Start Creating Results."), else
    puts the last third of the words in gold."""
    title = re.sub(r"\s+", " ", title).strip()
    m = re.match(r"^(.{6,}?[.:?!])\s+(.{4,})$", title)
    if m:
        return m.group(1).rstrip(".:"), m.group(2).rstrip(".")
    words = title.split()
    if len(words) < 3:
        return "", title
    cut = max(1, len(words) - max(1, len(words) // 3))
    return " ".join(words[:cut]), " ".join(words[cut:])


def _size(text: str, big: int, small: int) -> int:
    n = len(text)
    if n <= 14:
        return big
    if n <= 24:
        return int(big * 0.82)
    if n <= 40:
        return int(big * 0.68)
    if n <= 60:
        return max(small, int(big * 0.58))
    return small


def render(spec: dict, variant: str, out_path: Path) -> Path:
    """spec: title, subtitle (optional), points (0-4 short lines), keyword.
    variant: "instagram" or "facebook". Writes a 1080x1350 JPEG."""
    white, gold = spec.get("headline_white"), spec.get("headline_gold")
    if not (white or gold):
        white, gold = split_headline(spec["title"])
    points = [p for p in (spec.get("points") or []) if p][:4]
    subtitle = spec.get("subtitle") or ""
    if points and len(subtitle) > 90:
        subtitle = ""
    if variant == "instagram":
        cta_top, pill, cta_bottom = "Comment the word", spec["keyword"], "and we'll send the free guide to your DMs"
        pill_size = 64 if len(spec["keyword"]) <= 8 else 54
    else:
        cta_top, pill, cta_bottom = "Get the free guide", "LINK IN THE COMMENTS", "It's free. Tap the link below the post."
        pill_size = 46
    e = html.escape
    page = PAGE.format(
        badge=BADGE.resolve().as_uri(),
        h1=_size(white or "", 92, 48), h2=_size(gold or "", 80, 44),
        white=e(white or ""), gold=e(gold or ""),
        subtitle=(f'<p style="margin:28px 0 0;max-width:840px;text-align:center;font-size:30px;line-height:1.4;'
                  f'font-weight:500;color:#dbe3f7">{e(subtitle)}</p>') if subtitle else "",
        points=(('<div style="margin-top:40px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;width:100%">'
                 + "".join(f'<div class="pt">{CHECK}<div>{e(p)}</div></div>' for p in points) + "</div>") if points else ""),
        cta_top=e(cta_top), pill=e(pill), cta_bottom=e(cta_bottom),
        pill_size=pill_size, pill_ls=4 if variant == "instagram" else 2,
    )
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as tmp:
        page_path = Path(tmp) / "art.html"
        page_path.write_text(page, encoding="utf-8")
        png = Path(tmp) / "art.png"
        with sync_playwright() as p:
            browser = p.chromium.launch()
            tab = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
            tab.goto(page_path.as_uri())
            tab.wait_for_timeout(2500)
            overflow = tab.evaluate("document.querySelector('.bg').scrollHeight > 1350")
            tab.screenshot(path=str(png), clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT})
            browser.close()
        if overflow:
            raise ValueError("text does not fit the 1080x1350 art; shorten the title, subtitle or points")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.open(png).convert("RGB").save(out_path, quality=92)
    return out_path
