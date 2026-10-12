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
# New ebook forms are copied from this one ("Ebook - Getting Your Doctor to
# Invest in Your Development", 2026-10-11); GHL_EBOOK_FORM_TEMPLATE_ID overrides.
EBOOK_FORM_TEMPLATE_ID = "yuK8Sq6xlzVPLNQzZAaR"


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
        self.form_template_id = env.get("GHL_EBOOK_FORM_TEMPLATE_ID") or EBOOK_FORM_TEMPLATE_ID
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
        # 93 forms on 2026-10-12 against a page size of 100, so page with skip.
        forms: list[dict] = []
        while True:
            response = requests.get(
                f"{API_BASE}/forms/",
                headers=self._headers(),
                params={"locationId": self.location_id, "limit": 100, "skip": len(forms)},
                timeout=30,
            )
            if response.status_code != 200:
                raise GhlError(f"GHL forms list failed ({response.status_code}): {response.text[:300]}")
            page = response.json().get("forms", [])
            if forms and page and page[0].get("id") == forms[0].get("id"):
                break  # skip ignored, same page again
            forms.extend(page)
            if len(page) < 100:
                break
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

    def get_form(self, form_id: str) -> dict:
        response = requests.get(
            f"{API_BASE}/forms/{form_id}",
            headers=self._headers(),
            params={"locationId": self.location_id},
            timeout=30,
        )
        if response.status_code != 200:
            raise GhlError(f"GHL form get failed ({response.status_code}): {response.text[:300]}")
        return response.json()["form"]

    def create_form_from_template(self, template_form_id: str, name: str, redirect_url: str) -> dict:
        """Copies an existing ebook form (fields, style, button) under a new name,
        redirecting to this ebook's thank-you page. POST /forms/ with the full
        formData creates it (confirmed live 2026-10-12). There is no update
        endpoint for a private integration token (PUT/PATCH 404, POST /forms/{id}
        needs a user session), so the redirect has to be right at creation."""
        form_data = self.get_form(template_form_id)["formData"]
        form_data["form"]["formAction"]["redirectUrl"] = redirect_url
        response = requests.post(
            f"{API_BASE}/forms/",
            headers={**self._headers(), "Content-Type": "application/json"},
            params={"locationId": self.location_id},
            json={"locationId": self.location_id, "name": name, "formData": form_data},
            timeout=60,
        )
        if response.status_code not in (200, 201):
            raise GhlError(f"GHL form create failed ({response.status_code}): {response.text[:300]}")
        created = self.find_form_by_title(name)
        if not created:
            raise GhlError(f'GHL form "{name}" was created but is not listed yet')
        return created

    def ensure_ebook_form(self, name: str, redirect_url: str) -> dict:
        return self.find_form_by_title(name) or self.create_form_from_template(self.form_template_id, name, redirect_url)

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

    # --- Blog "new post" notification helpers ---
    # Custom Values API and Contacts tag API are both standard (non-premium)
    # GHL v2 endpoints, confirmed live 2026-09-25. Unlike forms/workflows,
    # these ARE writable via the public API - no manual GHL UI step needed
    # for this part.

    def list_custom_values(self) -> list[dict]:
        response = requests.get(
            f"{API_BASE}/locations/{self.location_id}/customValues",
            headers=self._headers(),
            timeout=30,
        )
        if response.status_code != 200:
            raise GhlError(f"GHL custom values list failed ({response.status_code}): {response.text[:300]}")
        return response.json().get("customValues", [])

    def update_custom_value(self, custom_value_id: str, name: str, value: str) -> None:
        # GHL's PUT requires `name` in the body even though it's not
        # changing - omitting it 422s with "name should not be empty".
        response = requests.put(
            f"{API_BASE}/locations/{self.location_id}/customValues/{custom_value_id}",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"name": name, "value": value},
            timeout=30,
        )
        if response.status_code != 200:
            raise GhlError(f"GHL custom value update failed ({response.status_code}): {response.text[:300]}")

    def find_contact_by_email(self, email: str) -> dict | None:
        response = requests.get(
            f"{API_BASE}/contacts/",
            headers=self._headers(),
            params={"locationId": self.location_id, "query": email, "limit": 5},
            timeout=30,
        )
        if response.status_code != 200:
            raise GhlError(f"GHL contact search failed ({response.status_code}): {response.text[:300]}")
        for contact in response.json().get("contacts", []):
            if contact.get("email", "").lower() == email.lower():
                return contact
        return None

    def add_tag_to_contact(self, contact_id: str, tag: str) -> None:
        response = requests.post(
            f"{API_BASE}/contacts/{contact_id}/tags",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"tags": [tag]},
            timeout=30,
        )
        if response.status_code not in (200, 201):
            raise GhlError(f"GHL add tag failed ({response.status_code}): {response.text[:300]}")

    def remove_tag_from_contact(self, contact_id: str, tag: str) -> None:
        response = requests.delete(
            f"{API_BASE}/contacts/{contact_id}/tags",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"tags": [tag]},
            timeout=30,
        )
        if response.status_code not in (200, 201):
            raise GhlError(f"GHL remove tag failed ({response.status_code}): {response.text[:300]}")

    def iter_all_contact_ids(self, page_size: int = 100):
        """Paginate through every contact in the location. Used for the full
        (non-test) blog-notify run - 7224 contacts as of 2026-09-25."""
        for contact in self.iter_all_contacts(page_size):
            yield contact["id"]

    def iter_all_contacts(self, page_size: int = 100):
        """Same pagination as iter_all_contact_ids, yielding the contact objects
        (the blog notify needs dateAdded to let new contacts in)."""
        start_after = None
        start_after_id = None
        while True:
            params = {"locationId": self.location_id, "limit": page_size}
            if start_after is not None:
                params["startAfter"] = start_after
                params["startAfterId"] = start_after_id
            response = requests.get(f"{API_BASE}/contacts/", headers=self._headers(), params=params, timeout=30)
            if response.status_code != 200:
                raise GhlError(f"GHL contacts list failed ({response.status_code}): {response.text[:300]}")
            payload = response.json()
            contacts = payload.get("contacts", [])
            if not contacts:
                return
            yield from contacts
            meta = payload.get("meta", {})
            start_after = meta.get("nextPage") and meta.get("startAfter")
            start_after_id = meta.get("startAfterId")
            if not start_after:
                return
