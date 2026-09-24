"""Generates the Instagram/Facebook caption for a Content Calendar item -
the image itself is prepared by Thalles and uploaded to the calendar item
directly (see sync_blog_social.py), so this module only owns the text.

Caption follows the real DOMA caption pattern Thalles pasted 2026-09-24:
hook line, short setup, an emoji-bullet list (from whatever short lines are
in the calendar item's `direction`/`notes` field), a transition line, an
article-teaser line, an engagement question, "I'll drop the full article in
the comments" (the link itself goes in a follow-up IG comment, not the
caption - see sync_blog_social.post_ig_comment), then the hashtag block.

An earlier version of this module also generated the image (first a
Canva-sourced photo, then a pure-Pillow design) - neither matched what
Thalles actually wanted, and he asked to just prepare images himself
instead. That code is gone; see git history if it's ever worth revisiting.
"""

from __future__ import annotations

import re

BULLET_EMOJI = ["\U0001f9b7", "\U0001f4c8", "\U0001f4c5", "❌", "\U0001f465", "\U0001f4b0", "\U0001f4cb", "⚠️"]
ACRONYMS = {"KPI", "KPIS", "AI", "DOMA", "SOP", "SOPS", "ROI", "AR"}
CORE_HASHTAGS = [
    "#DentalOfficeManagers",
    "#DentalPracticeManagement",
    "#DentalLeadership",
    "#PracticeOperations",
    "#DentalTeam",
    "#DOMA",
]
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "for", "and", "or", "but", "to", "of", "in", "on",
    "your", "you", "it", "what", "why", "how", "not", "do", "does", "did", "should", "can",
}


def _sentence_case(text: str) -> str:
    words = text.split()
    out = []
    for i, word in enumerate(words):
        bare = re.sub(r"[^A-Za-z]", "", word).upper()
        if bare in ACRONYMS:
            out.append(word)
        elif i == 0:
            out.append(word[0].upper() + word[1:].lower() if word else word)
        else:
            out.append(word.lower())
    return " ".join(out)


def _hook_line(title: str) -> str:
    """Splits on an em dash into two short sentences when the title has one
    (matches the real pattern: "Production Is Down — But Why?" becomes
    "Production is down. But why?"); otherwise sentence-cases the whole
    title as one line."""
    if "—" in title:
        first, _, second = title.partition("—")
        first = _sentence_case(first.strip()).rstrip(".!?") + "."
        second = _sentence_case(second.strip())
        return f"{first} {second}"
    text = _sentence_case(title.strip())
    if not text.endswith((".", "!", "?")):
        text += "."
    return text


def _topic_hashtags(title: str, limit: int = 2) -> list[str]:
    words = re.findall(r"[A-Za-z]+", title)
    seen: list[str] = []
    for word in words:
        if word.lower() in STOPWORDS or len(word) < 4:
            continue
        tag = "#Dental" + word[0].upper() + word[1:].lower()
        if tag not in seen and tag not in CORE_HASHTAGS:
            seen.append(tag)
        if len(seen) >= limit:
            break
    return seen


def generate_caption(title: str, bullets_source: list[str]) -> str:
    """Matches the real DOMA caption pattern: hook line, short setup, an
    optional emoji-bullet list (pass the calendar item's short direction/
    notes lines, or a post's own headings when there's a natural list of
    sub-topics), a transition line, an article-teaser line, an engagement
    question, the fixed "drop it in the comments" line, then hashtags."""
    hook = _hook_line(title)

    bullets = "\n".join(
        f"{BULLET_EMOJI[i % len(BULLET_EMOJI)]} {b}" for i, b in enumerate(bullets_source)
    )

    hashtags = " ".join(CORE_HASHTAGS[:-1] + _topic_hashtags(title) + [CORE_HASHTAGS[-1]])

    parts = [hook]
    if bullets:
        parts += ["", bullets]
    parts += [
        "",
        "Our latest article breaks down what's actually going on.",
        "",
        "What's the first thing you'd check in your own practice? \U0001f447",
        "",
        "I'll drop the full article in the comments.",
        "",
        hashtags,
    ]
    return "\n".join(parts)


if __name__ == "__main__":
    # Quick manual check: python scripts/instagram_creative.py "Some Title"
    import sys

    test_title = sys.argv[1] if len(sys.argv) > 1 else "Production Is Down — But Why?"
    test_bullets = ["Diagnosis is down", "Case acceptance is down", "Treatment isn't getting scheduled"]
    print(generate_caption(test_title, test_bullets))
