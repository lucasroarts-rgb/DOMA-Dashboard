"""Pull aggregated lead counts from GoHighLevel (internally "Twilead") into
local SQLite, using a Private Integration API key.

Privacy rule for this project: never store or expose an individual lead's
email, phone or name - only day/source aggregated counts. The contact
payload from the API is discarded right after counting; nothing from it is
persisted except the count.

Setup:
1. In the GoHighLevel sub-account (location) for DOMA, go to
   Settings > Private Integrations > Create new integration.
2. Grant at minimum the "View Contacts" scope (read-only is enough).
3. Copy the generated token into GHL_API_KEY in .env, and the location's
   ID (Settings > Business Profile, or the URL when viewing the sub-account)
   into GHL_LOCATION_ID.

Email campaign stats (GHL_EMAIL_CAMPAIGNS below) are best-effort: GoHighLevel's
public v2 API does not have a universally documented "email campaign
performance" endpoint the way Mailchimp does - it depends on which
Marketing features are enabled on the sub-account. If this call fails,
the sync still completes (leads are the priority metric) and the error is
logged to sync_log so the dashboard can surface "email data unavailable"
instead of silently showing zero.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402

API_BASE = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"
LOOKBACK_DAYS = 180
PAGE_LIMIT = 100
MAX_PAGES = 50  # safety cap: 5,000 contacts scanned per sync


class GhlSyncError(RuntimeError):
    pass


def _headers(env: dict[str, str]) -> dict[str, str]:
    api_key = env.get("GHL_API_KEY")
    if not api_key:
        raise GhlSyncError("Missing GHL_API_KEY in .env")
    return {
        "Authorization": f"Bearer {api_key}",
        "Version": API_VERSION,
        "Accept": "application/json",
    }


def fetch_contacts_created_since(env: dict[str, str], since: date) -> list[dict[str, Any]]:
    """Fetch contacts, stopping once a page is entirely older than `since`.
    Only dateAdded/source/id are kept from each contact - never email/phone/name."""
    try:
        import requests
    except ImportError as error:
        raise GhlSyncError("requests is not installed. Run: pip install -r requirements.txt") from error

    location_id = env.get("GHL_LOCATION_ID")
    if not location_id:
        raise GhlSyncError("Missing GHL_LOCATION_ID in .env")

    headers = _headers(env)
    since_dt = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)

    kept: list[dict[str, Any]] = []
    start_after: str | None = None
    start_after_id: str | None = None

    for _ in range(MAX_PAGES):
        params: dict[str, Any] = {"locationId": location_id, "limit": PAGE_LIMIT}
        if start_after and start_after_id:
            params["startAfter"] = start_after
            params["startAfterId"] = start_after_id

        response = requests.get(f"{API_BASE}/contacts/", headers=headers, params=params, timeout=30)
        if response.status_code != 200:
            raise GhlSyncError(f"GoHighLevel contacts request failed: {response.status_code} {response.text[:300]}")

        payload = response.json()
        contacts = payload.get("contacts", [])
        if not contacts:
            break

        stop = False
        for contact in contacts:
            date_added_raw = contact.get("dateAdded")
            if not date_added_raw:
                continue
            try:
                added_at = datetime.fromisoformat(str(date_added_raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            if added_at < since_dt:
                stop = True
                continue
            kept.append(
                {
                    "id": contact.get("id"),
                    "dateAdded": added_at.date().isoformat(),
                    "source": (contact.get("source") or "").strip() or "(not set)",
                }
            )

        if stop or len(contacts) < PAGE_LIMIT:
            break

        last = contacts[-1]
        start_after = str(last.get("dateAdded"))
        start_after_id = str(last.get("id"))

    return kept


def fetch_email_campaigns(env: dict[str, str], since: date) -> list[dict[str, Any]]:
    """Best-effort: not all GHL sub-accounts expose a campaign-stats endpoint
    to Private Integrations. Returns an empty list (not an error) if the
    endpoint is unavailable, so lead sync isn't blocked by it."""
    try:
        import requests
    except ImportError as error:
        raise GhlSyncError("requests is not installed. Run: pip install -r requirements.txt") from error

    location_id = env.get("GHL_LOCATION_ID")
    headers = _headers(env)
    response = requests.get(
        f"{API_BASE}/marketing/campaigns",
        headers=headers,
        params={"locationId": location_id, "limit": 50},
        timeout=30,
    )
    if response.status_code != 200:
        raise GhlSyncError(
            f"Email campaign stats unavailable ({response.status_code}). "
            "This endpoint may need a different scope or isn't enabled for this sub-account - "
            "confirm with GoHighLevel support which report to use, then adjust "
            "scripts/sync_ghl.py:fetch_email_campaigns."
        )

    payload = response.json()
    campaigns = payload.get("campaigns", payload.get("data", []))
    rows: list[dict[str, Any]] = []
    for campaign in campaigns:
        rows.append(
            {
                "campaign_id": str(campaign.get("id") or campaign.get("_id") or ""),
                "campaign_name": campaign.get("name") or "(untitled campaign)",
                "sent_at": campaign.get("sentAt") or campaign.get("createdAt"),
                "recipients": int(campaign.get("recipientCount") or campaign.get("sent") or 0),
                "opens": int(campaign.get("openCount") or campaign.get("opens") or 0),
                "clicks": int(campaign.get("clickCount") or campaign.get("clicks") or 0),
            }
        )
    return [row for row in rows if row["campaign_id"]]


def aggregate_by_day_and_source(contacts: list[dict[str, Any]]) -> list[tuple[str, str, int]]:
    counts: dict[tuple[str, str], int] = {}
    for contact in contacts:
        key = (contact["dateAdded"], contact["source"])
        counts[key] = counts.get(key, 0) + 1
    return [(report_date, source, count) for (report_date, source), count in counts.items()]


def store_leads(rows: list[tuple[str, str, int]]) -> None:
    with dashboard_app.db() as con:
        con.executemany(
            """
            INSERT INTO ghl_leads_daily (report_date, source, lead_count, synced_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(report_date, source) DO UPDATE SET
                lead_count = excluded.lead_count,
                synced_at = CURRENT_TIMESTAMP
            """,
            rows,
        )


def store_email_campaigns(rows: list[dict[str, Any]]) -> None:
    with dashboard_app.db() as con:
        con.executemany(
            """
            INSERT INTO ghl_email_campaigns
                (campaign_id, campaign_name, sent_at, recipients, opens, clicks, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(campaign_id) DO UPDATE SET
                campaign_name = excluded.campaign_name,
                sent_at = excluded.sent_at,
                recipients = excluded.recipients,
                opens = excluded.opens,
                clicks = excluded.clicks,
                synced_at = CURRENT_TIMESTAMP
            """,
            [
                (r["campaign_id"], r["campaign_name"], r["sent_at"], r["recipients"], r["opens"], r["clicks"])
                for r in rows
            ],
        )


def main() -> int:
    env = load_env_file()
    dashboard_app.init_db()
    since = date.today() - timedelta(days=LOOKBACK_DAYS)

    contacts = fetch_contacts_created_since(env, since)
    leads = aggregate_by_day_and_source(contacts)
    store_leads(leads)
    log_sync(dashboard_app, "ghl_leads", "ok", f"{len(contacts)} contacts, {len(leads)} day/source rows")
    print(f"GoHighLevel lead sync complete: {len(contacts)} contacts scanned, {len(leads)} day/source rows.")

    try:
        campaigns = fetch_email_campaigns(env, since)
        store_email_campaigns(campaigns)
        log_sync(dashboard_app, "ghl_email", "ok", f"{len(campaigns)} campaigns")
        print(f"GoHighLevel email campaign sync complete: {len(campaigns)} campaigns.")
    except GhlSyncError as error:
        log_sync(dashboard_app, "ghl_email", "skipped", str(error))
        print(f"WARNING: email campaign sync skipped ({error})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
