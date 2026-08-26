"""Daily per-person email digest of open Team & Meetings tickets.

Team ticket status lives in Firestore now (see static/firebase-team-sync.js
- click-to-cycle status and manually-added tickets are both live-synced
there, not in SQLite), so this reads the same Firestore project directly
over its public REST API rather than duplicating that state in SQLite.
Firestore rules are deliberately open (read/write, no auth) - see
README.md - so no credentials are needed for these reads either.

Each of the 3 team members gets their own email with only their own open
(not-done) tickets - no shared "everyone sees everyone's list" email.

Requires SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/EMAIL_FROM in .env
(same SMTP account as send_seo_digest.py).
"""

from __future__ import annotations

import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402

FIRESTORE_PROJECT_ID = "doma-dshboard"
FIRESTORE_BASE = f"https://firestore.googleapis.com/v1/projects/{FIRESTORE_PROJECT_ID}/databases/(default)/documents"

# Only these 3 have a known email today - anyone else (e.g. Mariannel) is
# skipped rather than guessing an address. Add to this map once known.
OWNER_EMAILS = {
    "Lucas": "lucas@joindoma.com",
    "Kyle": "kyle@joindoma.com",
    "Juli": "juli@joindoma.com",
}


class TaskDigestError(RuntimeError):
    pass


def _firestore_value(field: dict[str, Any]) -> Any:
    if "stringValue" in field:
        return field["stringValue"]
    if "integerValue" in field:
        return int(field["integerValue"])
    if "nullValue" in field:
        return None
    return None


def _fetch_collection(collection: str) -> list[dict[str, Any]]:
    """One unauthenticated GET, no pagination - these collections are small
    (dozens of docs, not thousands), same assumption the dashboard's own
    onSnapshot listeners make."""
    resp = requests.get(f"{FIRESTORE_BASE}/{collection}", timeout=30)
    if resp.status_code != 200:
        raise TaskDigestError(f"Firestore read failed for {collection}: {resp.status_code} {resp.text[:300]}")
    docs = resp.json().get("documents", [])
    results = []
    for doc in docs:
        doc_id = doc["name"].rsplit("/", 1)[-1]
        fields = {key: _firestore_value(value) for key, value in doc.get("fields", {}).items()}
        fields["id"] = doc_id
        results.append(fields)
    return results


def build_open_tickets_by_owner() -> dict[str, list[dict[str, Any]]]:
    """Merges baked SQLite tickets + live Firestore status overrides + live
    Firestore manual tickets - same merge logic as dashboard.js's
    teamMergedMeetings()/teamEffectiveStatus(), just in Python."""
    with dashboard_app.db() as con:
        rows = con.execute(
            "SELECT id, owner, description, status, topic FROM team_action_items ORDER BY owner, id"
        ).fetchall()
    baked = [{"id": str(row["id"]), "owner": row["owner"], "description": row["description"], "status": row["status"], "topic": row["topic"] or "General"} for row in rows]

    live_statuses = {doc["id"]: doc.get("status") for doc in _fetch_collection("team_action_item_status")}
    manual_items = [
        {
            "id": doc["id"],
            "owner": doc.get("owner"),
            "description": doc.get("description"),
            "status": doc.get("status") or "open",
            "topic": doc.get("topic") or "General",
        }
        for doc in _fetch_collection("team_manual_items")
    ]

    all_items = baked + manual_items
    by_owner: dict[str, list[dict[str, Any]]] = {}
    for item in all_items:
        effective_status = live_statuses.get(item["id"]) or item["status"] or "open"
        if effective_status == "done":
            continue
        by_owner.setdefault(item["owner"], []).append({**item, "status": effective_status})

    return by_owner


def build_email_for_owner(owner: str, tickets: list[dict[str, Any]], dashboard_url: str | None) -> tuple[str, str]:
    """Returns (subject, plain_text_body)."""
    open_count = sum(1 for t in tickets if t["status"] == "open")
    in_progress_count = sum(1 for t in tickets if t["status"] == "in_progress")
    subject = f"DOMA tasks - {len(tickets)} open for {owner} ({open_count} to do, {in_progress_count} in progress)"

    lines = [f"Open tickets for {owner}", "=" * 40, ""]
    for status_label, status_value in (("TO DO", "open"), ("IN PROGRESS", "in_progress")):
        matching = [t for t in tickets if t["status"] == status_value]
        if not matching:
            continue
        lines.append(status_label)
        for t in matching:
            lines.append(f"  - [{t['topic']}] {t['description']} (#{t['id']})")
        lines.append("")

    if dashboard_url:
        lines.append("-" * 40)
        lines.append(f"Update status or add tickets: {dashboard_url.rstrip('/')}/ (Team & Meetings tab)")
    return subject, "\n".join(lines)


def send_email(subject: str, body: str, to_addr: str, env: dict[str, str]) -> None:
    host = env.get("SMTP_HOST")
    port = env.get("SMTP_PORT")
    user = env.get("SMTP_USER")
    password = env.get("SMTP_PASSWORD")
    from_addr = env.get("EMAIL_FROM") or user

    if not all([host, port, user, password]):
        raise TaskDigestError("Missing SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD in .env")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, int(port), timeout=30, context=context) as server:
        server.login(user, password)
        server.send_message(msg)


def main() -> int:
    env = load_env_file()
    dashboard_app.init_db()

    by_owner = build_open_tickets_by_owner()
    dashboard_url = env.get("GITHUB_PAGES_URL")

    sent = []
    for owner, email in OWNER_EMAILS.items():
        tickets = by_owner.get(owner, [])
        if not tickets:
            print(f"Skipped {owner} ({email}): no open tickets.")
            continue
        subject, body = build_email_for_owner(owner, tickets, dashboard_url)
        send_email(subject, body, email, env)
        sent.append(f"{owner} ({len(tickets)})")
        print(f"Sent to {owner} <{email}>: {subject}")

    unmapped_owners = set(by_owner) - set(OWNER_EMAILS)
    if unmapped_owners:
        print(f"NOTE: open tickets exist for owner(s) with no known email, skipped: {', '.join(sorted(unmapped_owners))}")

    log_sync(dashboard_app, "task_digest_email", "ok", f"Sent to: {', '.join(sent) if sent else 'nobody (all clear)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
