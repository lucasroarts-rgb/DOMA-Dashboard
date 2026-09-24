"""Tiny .env reader shared by every sync script - no external dependency."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def load_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    """Merges a local .env file (if any) with real process environment
    variables, the latter winning on overlap - a hosting platform like
    Render injects config as actual env vars, not a .env file on disk, so
    without this merge every script/endpoint that only ever called
    load_env_file() would see an empty dict there and silently lose config
    that's genuinely set (confirmed live 2026-09-24: CORS and the ebook
    pipeline both read config this way, and both broke identically on
    Render until this fix - .env-file-only lookups are the one pattern to
    watch for when adding a new config value anywhere in this project)."""
    values: dict[str, str] = {}
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    values.update(os.environ)
    return values


def log_sync(dashboard_app, source: str, status: str, detail: str = "") -> None:
    with dashboard_app.db() as con:
        con.execute(
            "INSERT INTO sync_log (source, status, detail) VALUES (?, ?, ?)",
            (source, status, detail[:500]),
        )


if __name__ == "__main__":
    print(load_env_file())
    sys.exit(0)
