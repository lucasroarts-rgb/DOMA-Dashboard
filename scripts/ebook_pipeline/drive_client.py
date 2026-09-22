"""Lists and downloads the cover image + PDF pair Michelle/Lucas drop into
the shared Google Drive folder. Reuses the same service account as
sync_ga4.py/sync_gsc.py - just needs the Drive API enabled on the project and
the folder shared with the service account as Viewer.
"""

from __future__ import annotations

from pathlib import Path

PDF_MIME = "application/pdf"
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}


class DriveError(RuntimeError):
    pass


def _service(env: dict[str, str]):
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as error:
        raise DriveError(
            "google-api-python-client is not installed. Run: pip install -r requirements.txt"
        ) from error

    key_file = env.get("GA4_SERVICE_ACCOUNT_FILE")
    if not key_file:
        raise DriveError("Missing GA4_SERVICE_ACCOUNT_FILE in .env (reused for Drive access)")

    root = Path(__file__).resolve().parent.parent.parent
    key_path = root / key_file
    if not key_path.exists():
        raise DriveError(f"Service account file not found: {key_path}")

    creds = service_account.Credentials.from_service_account_file(
        str(key_path), scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_folder_files(env: dict[str, str]) -> list[dict]:
    """Returns all non-trashed files currently in DRIVE_EBOOKS_FOLDER_ID."""
    folder_id = env.get("DRIVE_EBOOKS_FOLDER_ID")
    if not folder_id:
        raise DriveError("Missing DRIVE_EBOOKS_FOLDER_ID in .env")

    from googleapiclient.errors import HttpError

    service = _service(env)
    files: list[dict] = []
    page_token = None
    try:
        while True:
            response = (
                service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed=false",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                    pageToken=page_token,
                )
                .execute()
            )
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
    except HttpError as error:
        reason = getattr(error, "reason", str(error))
        raise DriveError(f"Drive API call failed: {reason}") from error
    return files


def find_new_pairs(files: list[dict], processed_ids: set[str]) -> list[dict]:
    """Groups unseen files into PDF+image pairs. Ignores anything already
    processed or that doesn't form a complete pair yet (e.g. only the image
    has landed so far - wait for the next run)."""
    pdfs = [f for f in files if f["mimeType"] == PDF_MIME and f["id"] not in processed_ids]
    images = [f for f in files if f["mimeType"] in IMAGE_MIMES and f["id"] not in processed_ids]

    pairs = []
    for pdf in pdfs:
        if not images:
            break
        image = images.pop(0)
        pairs.append({"pdf": pdf, "image": image})
    return pairs


def download_file(env: dict[str, str], file_id: str, dest: Path) -> Path:
    from googleapiclient.http import MediaIoBaseDownload

    service = _service(env)
    request = service.files().get_media(fileId=file_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as handle:
        downloader = MediaIoBaseDownload(handle, request)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
    return dest
