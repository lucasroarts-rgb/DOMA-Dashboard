"""Log one paid-ad competitive-intelligence finding into the dashboard's
"Ad Spy" panel (Competitors tab).

This is NOT an automated sync - Meta Ads Library, Google Ads Transparency
Center, TikTok Creative Center, and LinkedIn Ads Library don't have public
APIs for bulk/scheduled collection of competitor ads. The workflow is
manual: browse one of those libraries by hand, find a real ad, then log it
here with this script. See README.md "Ad Spy" for the full research
process (based on a Notion guide the user provided).

Usage (run once per ad you want logged):
    python scripts/add_ad_spy_entry.py \
        --competitor "AADOM" \
        --platform Meta \
        --date-found 2026-08-22 \
        --format Video \
        --hook "Are you tired of being the only one who knows how the office runs?" \
        --offer "Free conference ticket giveaway" \
        --cta "Enter now" \
        --link "https://www.facebook.com/ads/library/?id=..." \
        --hypothesis "Try a giveaway-entry hook for our own AI Ready launch"

Only --competitor, --platform, and --date-found are required - fill in
whatever else you actually observed. Leave a field out rather than
guessing; "not identified" is a valid, expected value per the source
guide's own rule (never invent data that isn't observable).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402

PLATFORMS = ["Meta", "Google", "TikTok", "LinkedIn"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--competitor", required=True, help="Competitor/advertiser name")
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    parser.add_argument("--date-found", required=True, help="YYYY-MM-DD, when you found this ad")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD, when the ad library says it started (if shown)")
    parser.add_argument("--status", default=None, help="Active / Inactive / Not identified")
    parser.add_argument("--format", default=None, help="Image / Video / Carousel / Text")
    parser.add_argument("--hook", default=None)
    parser.add_argument("--angle", default=None, help="e.g. Pain, Curiosity, Urgency, Authority, Social proof")
    parser.add_argument("--pain-or-desire", default=None)
    parser.add_argument("--promise", default=None)
    parser.add_argument("--offer", default=None)
    parser.add_argument("--proof", default=None)
    parser.add_argument("--cta", default=None)
    parser.add_argument("--tone", default=None, dest="tone_of_voice")
    parser.add_argument("--visual-style", default=None)
    parser.add_argument("--landing-page", default=None)
    parser.add_argument("--hypothesis", default=None, dest="strategic_hypothesis")
    parser.add_argument("--link", default=None, help="Public link to the ad or the library search")
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()

    dashboard_app.init_db()

    entry_id = f"{args.platform.upper()}-{args.competitor.upper().replace(' ', '-')}-{args.date_found}"

    with dashboard_app.db() as con:
        con.execute(
            """
            INSERT INTO ad_spy_entries
                (entry_id, competitor, platform, date_found, start_date, status_observed, format,
                 hook, angle, pain_or_desire, promise, offer, proof, cta, tone_of_voice, visual_style,
                 landing_page, strategic_hypothesis, link, notes, last_reviewed, created_at)
            VALUES
                (:entry_id, :competitor, :platform, :date_found, :start_date, :status_observed, :format,
                 :hook, :angle, :pain_or_desire, :promise, :offer, :proof, :cta, :tone_of_voice, :visual_style,
                 :landing_page, :strategic_hypothesis, :link, :notes, :date_found, CURRENT_TIMESTAMP)
            """,
            {
                "entry_id": entry_id,
                "competitor": args.competitor,
                "platform": args.platform,
                "date_found": args.date_found,
                "start_date": args.start_date,
                "status_observed": args.status,
                "format": args.format,
                "hook": args.hook,
                "angle": args.angle,
                "pain_or_desire": args.pain_or_desire,
                "promise": args.promise,
                "offer": args.offer,
                "proof": args.proof,
                "cta": args.cta,
                "tone_of_voice": args.tone_of_voice,
                "visual_style": args.visual_style,
                "landing_page": args.landing_page,
                "strategic_hypothesis": args.strategic_hypothesis,
                "link": args.link,
                "notes": args.notes,
            },
        )

    print(f"Logged: {entry_id}")
    print("Run scripts/generate_public_site.py to publish, or it'll go out with the next daily_sync.py run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
