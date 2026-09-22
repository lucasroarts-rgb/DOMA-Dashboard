"""Manual one-off: force-check a single ebook's form-attach status right now
instead of waiting for the next scheduled run (sync_ebook_pipeline.py already
rechecks every 30min automatically, see README.md "Automação de eBooks").

Usage:
    python scripts/attach_ebook_form.py <slug>
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.env_utils import load_env_file  # noqa: E402
from scripts.ebook_pipeline.attach import try_attach  # noqa: E402

PACKAGES_DIR = ROOT / "ebook_packages"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/attach_ebook_form.py <slug>", file=sys.stderr)
        return 1
    slug = sys.argv[1]

    package_path = PACKAGES_DIR / slug / "package.json"
    if not package_path.exists():
        print(f"ERROR: no ebook_packages/{slug}/package.json - run sync_ebook_pipeline.py first.", file=sys.stderr)
        return 1

    env = load_env_file()
    print(try_attach(env, package_path)["message"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
