"""GoHighLevel (LeadConnector) helper for the eBook pipeline.

Uses GHL_EBOOK_API_KEY (a separate Private Integration token from the
read-only GHL_API_KEY used by sync_ghl.py) - keeps the dashboard's
contacts-read scope untouched from this write-capable pipeline.

Endpoint contract for /medias/upload-file was verified live on 2026-08-20
(not just from docs, which disagree with each other on the Version header):
POST, `Version: 2021-07-28`, `locationId` as a query param, multipart body
with `file` + `name`. Response: {"fileId": ..., "url": "https://assets.cdn.filesafe.space/..."}.

Form and workflow creation have **no public API endpoint** in GHL v2 as of
this writing - list_forms()/list_workflows() below are read-only, used to
warn if an ebook's form/workflow don't exist yet (duplicating them is a
manual/Claude-Browser-assisted step, see README.md).
"""

from __future__ import annotations

import re
from pathlib import Path

import requests

API_BASE = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"


class GhlError(RuntimeError):
    pass


def _normalize(name: str) -> str:
    """GHL's form builder silently drops punctuation from names typed into
    it (confirmed live: "Ebook - Production vs. Collection: The Gap That's
    Costing You" was saved as "Ebook - Production vs Collection The Gap
    Thats Costing You") - an exact/substring match on the PDF-derived title
    then misses a form a human just created by hand. Compare with
    punctuation stripped instead of relying on exact text."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


class GhlClient:
    def __init__(self, env: dict[str, str]):
        self.api_key = env.get("GHL_EBOOK_API_KEY")
        self.location_id = env.get("GHL_LOCATION_ID")
        if not self.api_key or not self.location_id:
            raise GhlError("Missing GHL_EBOOK_API_KEY or GHL_LOCATION_ID in .env")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Version": API_VERSION}

    def upload_media(self, file_path: Path, filename: str) -> dict:
        # Explicit content-type is required - a bare (filename, handle) 2-tuple
        # left requests to send a generic/wrong type, and GHL stored the PDF
        # in a way its own viewer couldn't open (confirmed live: "Falha ao
        # carregar documento PDF" on the delivered download link).
        with open(file_path, "rb") as handle:
            response = requests.post(
                f"{API_BASE}/medias/upload-file",
                headers=self._headers(),
                params={"locationId": self.location_id},
                files={"file": (filename, handle, "application/pdf")},
                data={"name": filename},
                timeout=120,
            )
        if response.status_code not in (200, 201):
            raise GhlError(f"GHL media upload failed ({response.status_code}): {response.text[:300]}")
        payload = response.json()
        return {"file_id": payload["fileId"], "url": payload["url"]}

    def list_forms(self, name_contains: str = "") -> list[dict]:
        response = requests.get(
            f"{API_BASE}/forms/",
            headers=self._headers(),
            params={"locationId": self.location_id, "limit": 100},
            timeout=30,
        )
        if response.status_code != 200:
            raise GhlError(f"GHL forms list failed ({response.status_code}): {response.text[:300]}")
        forms = response.json().get("forms", [])
        if name_contains:
            forms = [f for f in forms if name_contains.lower() in f.get("name", "").lower()]
        return forms

    def list_workflows(self, name_contains: str = "") -> list[dict]:
        response = requests.get(
            f"{API_BASE}/workflows/",
            headers=self._headers(),
            params={"locationId": self.location_id},
            timeout=30,
        )
        if response.status_code != 200:
            raise GhlError(f"GHL workflows list failed ({response.status_code}): {response.text[:300]}")
        workflows = response.json().get("workflows", [])
        if name_contains:
            workflows = [w for w in workflows if name_contains.lower() in w.get("name", "").lower()]
        return workflows

    def find_form_by_title(self, expected_name: str) -> dict | None:
        target = _normalize(expected_name)
        for form in self.list_forms("Ebook"):
            if _normalize(form.get("name", "")) == target:
                return form
        return None

    def find_workflow_by_title(self, expected_name: str) -> dict | None:
        target = _normalize(expected_name)
        for workflow in self.list_workflows("Ebook"):
            if _normalize(workflow.get("name", "")) == target:
                return workflow
        return None
