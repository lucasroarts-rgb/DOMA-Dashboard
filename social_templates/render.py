"""Exports canvas artboards (project/*.dc.html, copied from the "DOMA post templates"
canvas https://claude.ai/artifact/PNfCo5dFprZio6FGaijcVv) to JPEG.

Usage: python social_templates/render.py archy/A1.dc.html carousel/E1.dc.html ...
Size comes from each artboard's $preview (1080x1350 feed, 1080x1920 story); /_blob/<id>
images map to files in assets/ through BLOBS. Output: social_templates/out/<path-with-dashes>.jpg
"""

import json
import pathlib
import re
import sys

from PIL import Image
from playwright.sync_api import sync_playwright

T = pathlib.Path(__file__).resolve().parent
OUT = T / "out"
BLOBS = {
    "f9587cd6c521c2d288b086227f763289": "doma-badge.png",
    "102f36d59777a87df3cdb06f3d8b162f": "getvwire.png",
    "b9d48171b5f91202298ceed36f77567b": "domc-logo.png",
    "e67b79a0ee51e9f1b565cc9dca2e25f7": "kyle-circle.png",
    "923518d0ab0c5eb14d7401bb234e3add": "monica-circle.png",
    "58526341ac97ffda6e559522ca6c926f": "kyle-poster.jpg",
    "324476149466d3ee75063bb93bb40cd0": "archy-logo.png",
    "fe6a0b51421eb7ded61c46d851ea6e34": "cherry-logo.png",
}


def main(paths: list[str]) -> None:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1920})
        for rel in paths:
            h = (T / "project" / rel).read_text(encoding="utf-8")
            head = re.search(r"<helmet>(.*?)</helmet>", h, re.S).group(1)
            body = re.search(r"</helmet>(.*?)</x-dc>", h, re.S).group(1)
            for blob, name in BLOBS.items():
                body = body.replace(f"/_blob/{blob}", (T / "assets" / name).resolve().as_uri())
            props = json.loads(re.search(r"data-props='([^']*)'", h).group(1).replace("&amp;", "&"))
            size = props.get("$preview", {"width": 1080, "height": 1350})
            name = rel.replace("/", "-").replace(".dc.html", "")
            html = OUT / f"{name}.html"
            html.write_text(f"<!doctype html><html><head><meta charset='utf-8'>{head}</head><body>{body}</body></html>",
                            encoding="utf-8")
            page.goto(html.resolve().as_uri())
            page.wait_for_timeout(2200)
            png = OUT / f"{name}.png"
            page.screenshot(path=str(png), clip={"x": 0, "y": 0, "width": size["width"], "height": size["height"]})
            Image.open(png).convert("RGB").save(OUT / f"{name}.jpg", quality=92)
            print(OUT / f"{name}.jpg")
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1:])
