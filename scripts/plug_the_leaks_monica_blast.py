"""One-off send of Monica's Plug the Leaks video email to every contact with an email
(Lucas, 2026-10-14: "disparar o email amanhã às 10h na GHL para todos contatos").

Adds the tag plug-the-leaks-monica-email to each contact; the GHL workflow
"Plug the Leaks Monica Email" sends template "Plug the Leaks - Monica video invite"
(GHL skips DND/unsubscribed contacts) and removes the tag. Progress is kept in
data/plug_the_leaks_blast_state.json, so a rerun only picks up contacts not tagged yet.
Run by the one-time Windows task DOMA_PlugTheLeaks_Monica_Email (2026-10-15 10:00 BRT).
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ebook_pipeline.ghl_client import GhlClient  # noqa: E402
from scripts.env_utils import load_env_file  # noqa: E402
from scripts.ebook_lead_delivery import API_BASE, load_json, save_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "plug_the_leaks_blast_state.json"
TAG = "plug-the-leaks-monica-email"
ALREADY = {"XB85CKvacCWqnAliWt0r"}  # lucas@joindoma.com got the test on 2026-10-14


def all_contacts(ghl: GhlClient) -> list[dict]:
    headers = {**ghl._headers(), "Content-Type": "application/json"}
    out, page = [], 1
    while True:
        r = requests.post(f"{API_BASE}/contacts/search", headers=headers, timeout=60,
                          json={"locationId": ghl.location_id, "page": page, "pageLimit": 100,
                                "filters": [{"field": "email", "operator": "exists"}]})
        r.raise_for_status()
        batch = r.json().get("contacts", [])
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def main() -> int:
    ghl = GhlClient(load_env_file())
    state = load_json(STATE_PATH, {"tagged": [], "failed": {}})
    done = set(state["tagged"]) | ALREADY
    contacts = [c for c in all_contacts(ghl) if c.get("email") and c["id"] not in done]
    print(f"{datetime.now(timezone.utc).isoformat()} sending to {len(contacts)} contacts", flush=True)
    for i, c in enumerate(contacts, 1):
        try:
            ghl.add_tag_to_contact(c["id"], TAG)
            state["tagged"].append(c["id"])
        except Exception as error:  # keep going; failures are listed in the state file
            state["failed"][c["id"]] = str(error)[:200]
            time.sleep(2)
        if i % 50 == 0:
            save_json(STATE_PATH, state)
            print(f"{i}/{len(contacts)}", flush=True)
        time.sleep(0.15)
    save_json(STATE_PATH, state)
    print(f"done: {len(state['tagged'])} tagged, {len(state['failed'])} failed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
