"""One-time setup: get a long-lived OAuth refresh token for the Search
Console domain property (sc-domain:dentalofficemanagers.com).

Why this exists: the service account used for GA4/GSC (see
google_service_account.json) hits a real, documented Google Search Console
bug - "Add user" rejects service-account emails specifically on Domain
properties ("email not found"), even though the same account already works
fine on the URL-prefix property and on GA4. A regular Google account (here,
lcs.roesler@gmail.com, added as a user on the domain property through the
GSC UI) doesn't hit that bug, so this script authenticates as that account
via OAuth instead of a service-account key.

Run this once, interactively - it opens a real browser window and asks you
to log in as lcs.roesler@gmail.com and grant read-only Search Console
access. The resulting refresh token is printed so it can be saved to .env;
it does not expire on its own (only if revoked or unused for 6 months).

Requires google_oauth_client.json in the project root (Google Cloud
Console > APIs & Services > Credentials > OAuth client, type "Desktop app"
> Download JSON) - gitignored, never commit it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = Path(__file__).resolve().parent.parent
CLIENT_SECRET_FILE = ROOT / "google_oauth_client.json"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def main() -> int:
    if not CLIENT_SECRET_FILE.exists():
        print(f"Missing {CLIENT_SECRET_FILE} - download it from Google Cloud Console first.", file=sys.stderr)
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")

    print("\nAuthorization complete.")
    print("Add these to .env:\n")
    print(f"GSC_OAUTH_CLIENT_ID={creds.client_id}")
    print(f"GSC_OAUTH_CLIENT_SECRET={creds.client_secret}")
    print(f"GSC_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
