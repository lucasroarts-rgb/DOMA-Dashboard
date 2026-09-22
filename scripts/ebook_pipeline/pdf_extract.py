"""Pulls title/subtitle/chapter-section copy out of the eBook PDF via
pdfplumber (pure-Python - no poppler/pdftoppm binary needed, which isn't
installed on this machine).

Extraction is heuristic, not exact: this series' PDFs (confirmed against the
real "Burnout at the Front Desk" PDF) follow a `Chapter N: {Heading}` /
ALL-CAPS kicker / paragraph pattern per page starting a few pages in, which
this parses into title/blurb pairs for the capture page's "Inside the guide"
cards. If a PDF doesn't follow that pattern, it falls back to a generic
font-size heuristic so the pipeline still produces *something* - all of this
feeds a WordPress **draft** page a human reviews before publishing, so
imperfect auto-copy is an acceptable starting point, not the final word.
"""

from __future__ import annotations

import re
from pathlib import Path

CHAPTER_RE = re.compile(r"^Chapter\s+\d+:\s*(.*)$")
SKIP_HEADINGS = {"conclusion", "about doma", "table of contents"}


def _clean(text: str) -> str:
    """Some PDFs in this series have a custom font subset that doesn't decode
    em-dashes correctly via pdfplumber - they come through as the literal
    replacement character. Swap it for a real em dash rather than leaving
    a broken glyph in generated copy."""
    return text.replace("�", "—")


class PdfExtractError(RuntimeError):
    pass


def extract(pdf_path: Path) -> dict:
    try:
        import pdfplumber
    except ImportError as error:
        raise PdfExtractError(
            "pdfplumber is not installed. Run: pip install -r requirements.txt"
        ) from error

    with pdfplumber.open(str(pdf_path)) as pdf:
        if not pdf.pages:
            raise PdfExtractError(f"{pdf_path.name} has no pages")

        title, subtitle = _title_and_subtitle(pdf.pages[0])
        page_texts = [page.extract_text() or "" for page in pdf.pages]
        sections = _extract_chapter_sections(page_texts, limit=6)
        if not sections:
            sections = _fallback_sections(pdf.pages, limit=6)
        page_count = len(pdf.pages)

    return {
        "title": title,
        "subtitle": subtitle,
        "sections": sections,
        "full_text": "\n".join(page_texts),
        "page_count": page_count,
    }


def _title_and_subtitle(first_page) -> tuple[str, str]:
    words = first_page.extract_words(extra_attrs=["size"])
    if not words:
        text = first_page.extract_text()
        return (text.splitlines()[0].strip() if text else "Untitled eBook"), ""

    lines: dict[float, list[str]] = {}
    for word in words:
        top = round(word["top"])
        lines.setdefault(top, []).append(word)

    grouped = []
    for top in sorted(lines):
        line_words = sorted(lines[top], key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in line_words)
        max_size = max(w["size"] for w in line_words)
        grouped.append((top, max_size, text.strip()))

    grouped = [g for g in grouped if g[2]]
    if not grouped:
        return "Untitled eBook", ""

    # Titles/subtitles often wrap across 2+ lines at the *same* font size
    # (confirmed live: "The 10 Reports Every Dental Practice Should Run" /
    # "Every Month" are two separate lines, both 33.7pt) - picking a single
    # line would truncate the real title and misread its second line as the
    # subtitle. Group consecutive same-size lines together instead.
    max_size = max(g[1] for g in grouped)
    title_lines = [g for g in grouped if abs(g[1] - max_size) < 0.5]
    title = " ".join(g[2] for g in title_lines)

    remaining = [g for g in grouped if g not in title_lines]
    subtitle = ""
    if remaining:
        sub_size = remaining[0][1]
        subtitle_lines = []
        for g in remaining:
            if abs(g[1] - sub_size) < 0.5:
                subtitle_lines.append(g[2])
            else:
                break
        subtitle = " ".join(subtitle_lines)

    return _clean(title), _clean(subtitle)


def _extract_chapter_sections(page_texts: list[str], limit: int) -> list[dict]:
    sections: list[dict] = []
    for text in page_texts:
        if not text:
            continue
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines or not CHAPTER_RE.match(lines[0]):
            continue

        # Not every PDF in this series has an ALL-CAPS "kicker" line under the
        # chapter title (confirmed live: "Who Does What" has none). Without a
        # kicker to mark the boundary, the only reliable signal that a line
        # still belongs to the (possibly wrapped) title is that it's short -
        # a wrapped title continuation (confirmed live in the Burnout PDF:
        # "Chapter 5: How Office Managers Can Reduce" / "Front Desk Overload")
        # is short, a paragraph's opening line is not. Blindly consuming
        # lines until a kicker turns up (or a length cap trips) pulled entire
        # paragraphs into the heading and left blurbs empty - confirmed live
        # on "Who Does What".
        heading_lines = [CHAPTER_RE.match(lines[0]).group(1)]
        idx = 1
        if idx < len(lines) and not _is_kicker(lines[idx]) and len(lines[idx]) < 50:
            heading_lines.append(lines[idx])
            idx += 1

        heading = _clean(re.sub(r"\s+", " ", " ".join(heading_lines)).strip())
        if heading.lower() in SKIP_HEADINGS:
            continue
        heading = _truncate_sentence(heading, 80)

        if idx < len(lines) and _is_kicker(lines[idx]):
            idx += 1
        body = _clean(" ".join(lines[idx:]).strip())
        blurb = _truncate_sentence(body, 170)

        sections.append({"heading": heading, "blurb": blurb})
        if len(sections) >= limit:
            break
    return sections


def _is_kicker(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 4 and line == line.upper()


def _truncate_sentence(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    last_period = truncated.rfind(". ")
    if last_period > max_len * 0.4:
        return truncated[: last_period + 1]
    last_space = truncated.rfind(" ")
    return (truncated[:last_space] if last_space > 0 else truncated).rstrip(",;: ") + "…"


def _fallback_sections(pages, limit: int) -> list[dict]:
    """No Chapter-N pattern found - grab the largest-font line per page (after
    the cover) as a heading, paired with the paragraph that follows it."""
    sections: list[dict] = []
    for page in pages[1:]:
        words = page.extract_words(extra_attrs=["size"])
        if not words:
            continue
        lines: dict[float, list[dict]] = {}
        for word in words:
            lines.setdefault(round(word["top"]), []).append(word)
        grouped = []
        for top in sorted(lines):
            line_words = sorted(lines[top], key=lambda w: w["x0"])
            text = " ".join(w["text"] for w in line_words).strip()
            if text:
                grouped.append((max(w["size"] for w in line_words), text))
        if not grouped:
            continue
        grouped.sort(key=lambda g: g[0], reverse=True)
        heading = grouped[0][1]
        if heading.lower() in SKIP_HEADINGS or len(heading) > 90:
            continue
        body_text = " ".join(t for _, t in grouped if t != heading)
        sections.append({"heading": heading, "blurb": _truncate_sentence(body_text, 170)})
        if len(sections) >= limit:
            break
    return sections
