"""Community CTA for DOMA's own social posts (Lucas, 2026-10-17).

Two additions, only on posts that are DOMA's own:
- caption: "Search Dental Office Managers on Facebook to join our free group." at the end of
  the CTA lines, before the hashtags;
- video: the 3-second "35,000+ members" end card (social_templates/endcard/motion.mp4,
  rendered by the doma-motion skill) appended to the Reel.

Never on sponsor posts or posts made together with a sponsor (Janie, DentalMenu, Roya and the
rest of the sponsor list, Archy events): type Sponsor or Event, or a sponsor name in the item.

Run alone for a dry run over the calendar:  python scripts/social_endcard.py --dry-run
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
ENDCARD = ROOT / "social_templates" / "endcard" / "motion.mp4"
GROUP_CTA = "Search Dental Office Managers on Facebook to join our free group."
EXCLUDED_TYPES = {"sponsor", "event", "community reshare"}  # reshares are other people's content
# Partners that appear in co-branded posts even when the item isn't typed Sponsor.
STATIC_PARTNERS = ["janie", "dentalmenu", "dental menu", "roya", "archy", "clipboard", "daydream", "carecredit"]


PREFIX = re.compile(r"^\s*(reel|sponsor\s+highlight|sponsor\s+spotlight|sponsor)\s*[-:–—�]*\s*", re.I)


def sponsor_words(items: list[dict]) -> list[str]:
    """Sponsor names from the Sponsor items of the calendar: "Reel - Janie AI: ..." -> "janie ai",
    "Sponsor Highlight - Cherry  When the..." -> "cherry"."""
    words = set(STATIC_PARTNERS)
    for item in items:
        if (item.get("type") or "").strip().lower() != "sponsor":
            continue
        name = PREFIX.sub("", (item.get("title") or "").strip())
        name = re.split(r"[:–—�|]|\s-\s|\s{2,}", name)[0]
        name = re.sub(r"\.(com|ai)$", "", name.strip().lower())
        if 3 <= len(name) <= 40:
            words.add(name)
    return sorted(words)


def is_own_post(item: dict, words: list[str]) -> bool:
    if (item.get("type") or "").strip().lower() in EXCLUDED_TYPES:
        return False
    if re.match(r"\s*sponsor\b", item.get("title") or "", re.I):
        return False
    text = " ".join(str(item.get(k) or "") for k in ("title", "headline", "caption_instagram", "caption_facebook", "direction", "notes")).lower()
    return not any(re.search(r"\b" + re.escape(w) + r"\b", text) for w in words)


def add_group_cta(caption: str) -> str:
    """Insert the group line after the CTA lines, before a trailing hashtag block."""
    if not caption or "dental office managers on facebook" in caption.lower():
        return caption
    lines = caption.rstrip().split("\n")
    tail: list[str] = []
    while lines and (lines[-1].strip().startswith("#") or not lines[-1].strip()):
        tail.insert(0, lines.pop())
    while tail and not tail[0].strip():
        tail.pop(0)
    body = "\n".join(lines).rstrip()
    out = body + "\n\n" + GROUP_CTA
    if tail:
        out += "\n\n" + "\n".join(tail)
    return out


# The social task runs under pythonw: no console window for ffmpeg (closing one would kill it).
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
SITE_MEDIA = "https://dentalofficemanagers.com/wp-content/uploads/"


def _has_audio(path: Path) -> bool:
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True, **NO_WINDOW)
    return bool(probe.stdout.strip())


def append_endcard(video: Path, out: Path) -> None:
    """Fit the Reel into 1080x1920 (navy padding), then the end card; silent audio under the card."""
    fit = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x070f26,setsar=1,fps=30"
    if _has_audio(video):
        graph = (f"[0:v]{fit}[v0];[1:v]scale=1080:1920,setsar=1,fps=30[v1];"
                 "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[a0];"
                 "anullsrc=r=44100:cl=stereo,atrim=duration=3.1[a1];"
                 "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
        maps = ["-map", "[v]", "-map", "[a]", "-c:a", "aac"]
    else:
        graph = f"[0:v]{fit}[v0];[1:v]scale=1080:1920,setsar=1,fps=30[v1];[v0][v1]concat=n=2:v=1:a=0[v]"
        maps = ["-map", "[v]"]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(ENDCARD),
                    "-filter_complex", graph, *maps, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "20", "-movflags", "+faststart", str(out)], check=True, capture_output=True, **NO_WINDOW)


def video_with_endcard(env: dict, video_url: str, upload) -> str:
    """Download the Reel, append the end card, upload it (upload(bytes, name) -> url).
    Only videos already on DOMA's own media library: the calendar is editable from the
    published dashboard, so a URL from anywhere else is never downloaded or processed."""
    if not video_url.startswith(SITE_MEDIA):
        raise ValueError(f"video is not on {SITE_MEDIA}, end card not applied")
    with tempfile.TemporaryDirectory(prefix="endcard_") as tmp:
        src, dst = Path(tmp) / "in.mp4", Path(tmp) / "out.mp4"
        r = requests.get(video_url, timeout=120, headers={"User-Agent": BROWSER_UA})
        r.raise_for_status()
        src.write_bytes(r.content)
        append_endcard(src, dst)
        name = Path(video_url.split("?")[0]).stem + "-endcard.mp4"
        return upload(dst.read_bytes(), name)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_blog_social as social  # noqa: E402

    items = social.fetch_calendar_items()
    words = sponsor_words(items)
    print("Sponsor words:", ", ".join(words))
    for item in sorted(items, key=lambda i: i.get("date") or ""):
        if item.get("instagram_posted") and item.get("facebook_posted"):
            continue
        own = is_own_post(item, words)
        print(f"{item.get('date')} | {'OWN    ' if own else 'skip   '} | {'video' if item.get('video_url') else 'image'} | {(item.get('title') or '')[:70]}")
