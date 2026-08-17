"""Tiny .env reader shared by every sync script - no external dependency."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def load_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
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
