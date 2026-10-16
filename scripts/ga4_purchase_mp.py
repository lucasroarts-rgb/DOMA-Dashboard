"""Send paid GoHighLevel orders to GA4 as "purchase" events (Measurement Protocol).

The membership checkout is a GHL payment link (up.dentalmanagerconnect.com) that
can't load the GA4 tag, so purchases never reached GA4. Lucas chose (2026-10-15)
to send them server side without touching the checkout or any GHL workflow: this
script reads the orders the dashboard already syncs from the public API and posts
each new paid order once. GA4 only accepts events up to 72 hours old, so it looks
back 3 days and keeps the sent order ids in data/ga4_purchase_sent.json.

No personal data goes to GA4: the client_id is a hash of the GHL contact id, and
the event carries only order id, product name, amount and currency.

Usage: python scripts/ga4_purchase_mp.py [--validate]
  --validate  send to GA4's debug endpoint only (nothing is recorded)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402
from scripts.sync_ghl import API_BASE, _headers  # noqa: E402

STATE = ROOT / "data" / "ga4_purchase_sent.json"
LOOKBACK = timedelta(hours=71)  # GA4 rejects events older than 72 hours
MP_URL = "https://www.google-analytics.com/mp/collect"
MP_DEBUG_URL = "https://www.google-analytics.com/debug/mp/collect"


def recent_paid_orders(env: dict[str, str]) -> list[dict]:
    since = datetime.now(timezone.utc) - LOOKBACK
    orders, offset = [], 0
    while True:
        r = requests.get(
            f"{API_BASE}/payments/orders",
            headers=_headers(env),
            params={"altId": env["GHL_LOCATION_ID"], "altType": "location", "limit": 50, "offset": offset},
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json().get("data") or []
        for o in batch:
            created = datetime.fromisoformat(str(o.get("createdAt", "")).replace("Z", "+00:00"))
            paid = (o.get("paymentStatus") or "").lower() in ("paid", "completed", "succeeded") or (o.get("status") or "").lower() == "completed"
            if o.get("liveMode") and paid and created >= since:
                orders.append(o)
        if len(batch) < 50 or offset > 500:
            return orders
        offset += 50


def client_id_for(contact_id: str) -> str:
    digest = hashlib.sha256(contact_id.encode()).hexdigest()
    return f"{int(digest[:8], 16)}.{int(digest[8:16], 16)}"


def purchase_payload(order: dict) -> dict:
    created = datetime.fromisoformat(str(order["createdAt"]).replace("Z", "+00:00"))
    amount = float(order.get("amount") or 0)
    product = order.get("sourceName") or "GHL order"
    return {
        "client_id": client_id_for(str(order.get("contactId") or order["_id"])),
        "timestamp_micros": int(created.timestamp() * 1_000_000),
        "non_personalized_ads": True,
        "events": [{
            "name": "purchase",
            "params": {
                "transaction_id": order["_id"],
                "value": amount,
                "currency": (order.get("currency") or "USD").upper(),
                "items": [{"item_id": order.get("sourceId") or product, "item_name": product, "price": amount, "quantity": 1}],
                "source": "ghl_payment_link",
                "engagement_time_msec": 1,
            },
        }],
    }


def run(validate: bool = False) -> int:
    """Callable from the 10-minute ebook_lead_delivery loop (no argv parsing)."""
    env = load_env_file()
    params = {"measurement_id": env["GA4_MEASUREMENT_ID"], "api_secret": env["GA4_MP_API_SECRET"]}
    sent = set(json.loads(STATE.read_text(encoding="utf-8"))) if STATE.exists() else set()
    orders = [o for o in recent_paid_orders(env) if o["_id"] not in sent]
    for order in orders:
        payload = purchase_payload(order)
        if validate:
            r = requests.post(MP_DEBUG_URL, params=params, json=payload, timeout=30)
            print("validate", order["_id"], r.json().get("validationMessages"))
            continue
        r = requests.post(MP_URL, params=params, json=payload, timeout=30)
        r.raise_for_status()
        sent.add(order["_id"])
    if not validate:
        STATE.write_text(json.dumps(sorted(sent)), encoding="utf-8")
        dashboard_app.init_db()
        log_sync(dashboard_app, "ga4_purchases", "ok", f"{len(orders)} new paid order(s) sent to GA4")
    print(f"{len(orders)} new paid order(s){' validated' if validate else ' sent'}")
    return len(orders)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    run(validate=ap.parse_args().validate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
