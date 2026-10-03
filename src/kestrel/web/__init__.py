"""Kestrel's web console: `uv run kestrel web`."""

import shutil
import subprocess
import sys

from kestrel.web.app import PROJECT_ROOT, WebConfig, create_app

CONSOLE_DIR = PROJECT_ROOT / "console"


def build_frontend() -> None:
    """npm install (first time) + npm run build, producing console/dist."""
    npm = shutil.which("npm")
    if npm is None:
        sys.exit("npm wasn't found. Install Node.js (https://nodejs.org) to build the console.")
    if not (CONSOLE_DIR / "node_modules").is_dir():
        subprocess.run([npm, "install"], cwd=CONSOLE_DIR, check=True)
    subprocess.run([npm, "run", "build"], cwd=CONSOLE_DIR, check=True)


__all__ = ["WebConfig", "build_frontend", "create_app"]
