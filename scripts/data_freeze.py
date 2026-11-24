"""Make the data queue's frozen copy outside the repo (scripts/data_queue.ps1), as google_after_freeze.py does.

    uv run --no-sync python scripts/data_freeze.py --commit main --dest C:\\kestrel-data-queue

Copies data_queue.ps1 and the two helpers it calls (groq_quota.py, data_gate.py) from one commit, byte for byte as git
stores them, into one folder, with MANIFEST.txt. It refuses a helper that isn't the one the queue pins, a folder inside
a git repo, and a folder that already holds different files. No network, no model calls.
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # google_after_freeze.py, when this file is loaded by path

from google_after_freeze import check, git_show, pins, sha256, write

QUEUE = "data_queue.ps1"
FILES = (f"scripts/{QUEUE}", "scripts/groq_quota.py", "scripts/data_gate.py")
LABEL = ("data queue", "scripts/data_freeze.py")

__all__ = ["FILES", "LABEL", "QUEUE", "check", "pins", "sha256", "write"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", required=True, help="the commit to copy from, e.g. main after the build merges")
    parser.add_argument("--dest", type=Path, required=True, help=r"a folder outside any repo: C:\kestrel-data-queue")
    args = parser.parse_args(argv)
    commit = subprocess.run(
        ["git", "rev-parse", f"{args.commit}^{{commit}}"], capture_output=True, text=True, check=True
    )
    full = commit.stdout.strip()
    contents = {Path(p).name: git_show(full, p) for p in FILES}
    if problems := check(contents, QUEUE):
        print("STOP: " + "; ".join(problems))
        return 1
    print(write(contents, args.dest, full, LABEL).read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
