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

import json
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
    start_after: int | None = None
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
        last_added_raw = str(last.get("dateAdded"))
        try:
            last_added_at = datetime.fromisoformat(last_added_raw.replace("Z", "+00:00"))
        except ValueError:
            break
        start_after = int(last_added_at.timestamp() * 1000)
        start_after_id = str(last.get("id"))

    _label_missing_sources(env, kept)
    return kept


# Contacts with no form `source` (imports, Instagram/Messenger DMs, community
# portal, inbound email, calls) used to land in one "(not set)" bucket - 143
# of 428 leads in the 90-day view on 2026-10-15. The contact list endpoint
# doesn't return attributionSource/createdBy, so look those up one by one for
# the blank ones and keep the label (only the label, no personal data) in a
# local cache so each contact is fetched once.
LABEL_CACHE = ROOT / "data" / "ghl_source_labels.json"


def label_for(contact: dict[str, Any]) -> str:
    source = (contact.get("source") or "").strip()
    if source:
        return source
    tags = {str(t).lower() for t in (contact.get("tags") or [])}
    attribution = contact.get("attributionSource") or {}
    medium = (attribution.get("medium") or "").lower()
    created = contact.get("createdBy") or {}
    created_source = (created.get("source") or "").upper()
    created_id = (created.get("sourceId") or "").lower()
    if medium == "csv_import" or "activecampaign-imported" in tags:
        return "Imported (ActiveCampaign CSV)"
    if created_id == "instagram" or medium == "instagram":
        return "Instagram DM keyword" if "lead-instagram" in tags else "Instagram DM"
    if created_id == "facebook" or medium == "facebook":
        return "Facebook Messenger"
    if created_source == "CLIENTPORTAL" or "joined_doma_community" in tags:
        return "DOMA Community portal"
    if created_source in ("CONVERSATIONS", "NOTIFICATION"):
        return "Inbound email"
    if created_source == "LC-PHONE-API" or medium == "other":
        return "Phone / SMS"
    if any(t.startswith("ebook-") for t in tags):
        return "Ebook (tag)"
    if attribution.get("utmSource"):
        return f"UTM: {attribution['utmSource']}"
    return "Manual / unknown"


def _label_missing_sources(env: dict[str, str], kept: list[dict[str, Any]]) -> None:
    try:
        cache = json.loads(LABEL_CACHE.read_text(encoding="utf-8")) if LABEL_CACHE.exists() else {}
    except ValueError:
        cache = {}
    import requests

    headers = _headers(env)
    for row in kept:
        if row["source"] != "(not set)" or not row.get("id"):
            continue
        if row["id"] not in cache:
            try:
                response = requests.get(f"{API_BASE}/contacts/{row['id']}", headers=headers, timeout=30)
                response.raise_for_status()
                cache[row["id"]] = label_for(response.json().get("contact") or {})
            except Exception:  # noqa: BLE001 - one failed lookup keeps the old label
                continue
        row["source"] = cache[row["id"]]
    LABEL_CACHE.write_text(json.dumps(cache), encoding="utf-8")


def fetch_email_campaigns(env: dict[str, str], since: date) -> list[dict[str, Any]]:
    """Confirmed unavailable to Private Integration tokens (2026-08-17):
    /emails/campaigns and /emails/schedule/{id}/stats both return 401 "token
    not authorized for this scope" even with every scope selected on the
    Private Integration (including emails/campaigns.readonly and
    emails/stats.readonly) and a freshly regenerated token. That 401 shape
    (auth-guard rejection, not a 404) plus it surviving a full-scope retry
    strongly suggests these are gated to reviewed Marketplace OAuth apps,
    not exposed to location-level Private Integrations at all - not a
    config mistake on this project's side. Returns an empty list (not an
    error) so lead sync isn't blocked by it."""
    try:
        import requests
    except ImportError as error:
        raise GhlSyncError("requests is not installed. Run: pip install -r requirements.txt") from error

    location_id = env.get("GHL_LOCATION_ID")
    headers = _headers(env)
    response = requests.get(
        f"{API_BASE}/emails/campaigns",
        headers=headers,
        params={"locationId": location_id, "limit": 50},
        timeout=30,
    )
    if response.status_code != 200:
        raise GhlSyncError(
            f"Email campaign stats unavailable ({response.status_code}). "
            "Confirmed as a Private Integration limitation, not a scope/config issue - "
            "see the docstring on this function before re-investigating."
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
        # The synced window is rebuilt as a whole, so a label that changed
        # (e.g. "(not set)" becoming "Instagram DM") doesn't stay summed in.
        if rows:
            con.execute("DELETE FROM ghl_leads_daily WHERE report_date >= ?", (min(r[0] for r in rows),))
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


# --- Payments (orders, transactions, subscriptions) -------------------------
# Same privacy rule as leads: only day/product/status aggregates are stored.
# The payment payloads carry name, email, phone and IP; none of it is read
# except contactId, which is used in memory to find the buyer's lead source
# (contact source or the label cache) and then dropped. Test-mode records
# (liveMode false) are skipped.
PAYMENTS_PAGE_LIMIT = 100
PAYMENTS_MAX_PAGES = 50


def _fetch_payments(env: dict[str, str], kind: str) -> list[dict[str, Any]]:
    import requests

    location_id = env.get("GHL_LOCATION_ID")
    if not location_id:
        raise GhlSyncError("Missing GHL_LOCATION_ID in .env")
    headers = _headers(env)
    items: list[dict[str, Any]] = []
    for page in range(PAYMENTS_MAX_PAGES):
        response = requests.get(
            f"{API_BASE}/payments/{kind}",
            headers=headers,
            params={"altId": location_id, "altType": "location", "limit": PAYMENTS_PAGE_LIMIT, "offset": page * PAYMENTS_PAGE_LIMIT},
            timeout=60,
        )
        if response.status_code != 200:
            raise GhlSyncError(f"GoHighLevel payments/{kind} failed: {response.status_code} {response.text[:200]}")
        payload = response.json()
        batch = payload.get("data") or []
        items += [item for item in batch if item.get("liveMode") is True]
        if len(batch) < PAYMENTS_PAGE_LIMIT or (page + 1) * PAYMENTS_PAGE_LIMIT >= int(payload.get("totalCount") or 0):
            break
    return items


def _buyer_source_groups(env: dict[str, str], contact_ids: set[str]) -> dict[str, str]:
    """contactId -> lead source group, in memory only. Uses the contact's own
    form source, or the same fallback label leads get (label cache first)."""
    import requests

    try:
        cache = json.loads(LABEL_CACHE.read_text(encoding="utf-8")) if LABEL_CACHE.exists() else {}
    except ValueError:
        cache = {}
    headers = _headers(env)
    groups: dict[str, str] = {}
    for contact_id in contact_ids:
        label = cache.get(contact_id)
        if label is None:
            try:
                response = requests.get(f"{API_BASE}/contacts/{contact_id}", headers=headers, timeout=30)
                response.raise_for_status()
                label = label_for(response.json().get("contact") or {})
            except Exception:  # noqa: BLE001 - unknown source falls into Other
                label = ""
        groups[contact_id] = dashboard_app.lead_source_group(label)
    return groups


def _day(raw: str | None) -> str | None:
    return str(raw)[:10] if raw else None


def sync_payments(env: dict[str, str]) -> str:
    orders = _fetch_payments(env, "orders")
    transactions = _fetch_payments(env, "transactions")
    subscriptions = _fetch_payments(env, "subscriptions")
    groups = _buyer_source_groups(env, {o["contactId"] for o in orders if o.get("contactId")})

    order_rows: dict[tuple[str, str, str, str], list] = {}
    for order in orders:
        report_date = _day(order.get("createdAt"))
        if not report_date:
            continue
        key = (report_date, order.get("sourceName") or "(unnamed product)", order.get("status") or "unknown",
               groups.get(order.get("contactId") or "", "Other"))
        agg = order_rows.setdefault(key, [0, 0.0])
        agg[0] += 1
        agg[1] += float(order.get("amount") or 0)

    refund_rows: dict[tuple[str, str], float] = {}
    for txn in transactions:
        refunded = float(txn.get("amountRefunded") or 0)
        # refund day is not in the payload; updatedAt is when the refund landed
        report_date = _day(txn.get("updatedAt") or txn.get("createdAt"))
        if refunded and report_date:
            key = (report_date, txn.get("entitySourceName") or "(unnamed product)")
            refund_rows[key] = refund_rows.get(key, 0.0) + refunded

    # monthly value: yearly plans count 1/12 of the price per month
    sub_rows: dict[tuple[str, str], list] = {}
    for sub in subscriptions:
        recurring = ((sub.get("recurringProduct") or {}).get("price") or {}).get("recurring") or {}
        interval, every = recurring.get("interval") or "month", int(recurring.get("intervalCount") or 1)
        per_month = {"day": 30.0, "week": 52 / 12, "month": 1.0, "year": 1 / 12}.get(interval, 1.0) / every
        key = (sub.get("entitySourceName") or "(unnamed product)", sub.get("status") or "unknown")
        agg = sub_rows.setdefault(key, [0, 0.0])
        agg[0] += 1
        agg[1] += float(sub.get("amount") or 0) * per_month

    today = date.today().isoformat()
    with dashboard_app.db() as con:
        # orders and refunds are small, so the tables are rebuilt each run
        con.execute("DELETE FROM ghl_orders_daily")
        con.executemany(
            "INSERT INTO ghl_orders_daily (report_date, product, status, source_group, orders, amount) VALUES (?, ?, ?, ?, ?, ?)",
            [(*key, agg[0], round(agg[1], 2)) for key, agg in order_rows.items()],
        )
        con.execute("DELETE FROM ghl_refunds_daily")
        con.executemany(
            "INSERT INTO ghl_refunds_daily (report_date, product, refunded) VALUES (?, ?, ?)",
            [(*key, round(value, 2)) for key, value in refund_rows.items()],
        )
        # one snapshot per day; older days stay so past ranges keep their MRR
        con.execute("DELETE FROM ghl_subscriptions_snapshot WHERE snapshot_date = ?", (today,))
        con.executemany(
            "INSERT INTO ghl_subscriptions_snapshot (snapshot_date, product, status, count, monthly_amount) VALUES (?, ?, ?, ?, ?)",
            [(today, *key, agg[0], round(agg[1], 2)) for key, agg in sub_rows.items()],
        )
    return f"{len(orders)} orders, {len(transactions)} transactions, {len(subscriptions)} subscriptions (live mode)"


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

    try:
        detail = sync_payments(env)
        log_sync(dashboard_app, "ghl_payments", "ok", detail)
        print(f"GoHighLevel payments sync complete: {detail}.")
    except Exception as error:  # noqa: BLE001 - payments are best-effort, leads come first
        log_sync(dashboard_app, "ghl_payments", "error", f"{type(error).__name__}: {error}")
        print(f"WARNING: payments sync skipped ({error})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
