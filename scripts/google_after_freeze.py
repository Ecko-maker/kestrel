"""Make the after-side queue's frozen copy outside the repo (docs/google-step-plan.md section 6, after side).

    uv run --no-sync python scripts/google_after_freeze.py --commit main --dest C:\\kestrel-queue

Copies scripts/google_after_queue.ps1 and the two helpers it calls (groq_quota.py, google_gate.py) from one commit,
byte for byte as git stores them (`git show <commit>:<path>`, so no line-ending conversion), into one folder, and
writes MANIFEST.txt (the commit and each file's SHA-256). It refuses when a helper's SHA-256 isn't the one the
queue pins, and when the folder already holds different files (move it away first): a copy is never patched in
place. The queue checks the same pins each time it starts. No network, no model calls.
"""

import argparse
import hashlib
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

QUEUE = "google_after_queue.ps1"
FILES = (f"scripts/{QUEUE}", "scripts/groq_quota.py", "scripts/google_gate.py")
PIN = re.compile(r'^\s*"(?P<name>[\w.]+\.py)"\s*=\s*"(?P<sha>[0-9a-f]{64})"\s*$', re.MULTILINE)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pins(queue_text: str) -> dict[str, str]:
    """The helper pins in the queue's $Pins table: file name -> SHA-256."""
    return {m["name"]: m["sha"] for m in PIN.finditer(queue_text)}


def check(contents: dict[str, bytes]) -> list[str]:
    """Problems with a set of files (name -> bytes): every helper present and equal to its pin."""
    pinned = pins(contents[QUEUE].decode("utf-8"))
    helpers = [name for name in contents if name != QUEUE]
    problems = [f"{name}: not pinned in {QUEUE}" for name in helpers if name not in pinned]
    problems += [f"{name}: pinned but not copied" for name in pinned if name not in contents]
    problems += [
        f"{name}: SHA-256 {sha256(contents[name])}, pinned {pinned[name]}"
        for name in helpers
        if name in pinned and sha256(contents[name]) != pinned[name]
    ]
    return problems


def write(contents: dict[str, bytes], dest: Path, commit: str) -> Path:
    """Write the files and MANIFEST.txt into dest; refuse if dest holds anything different."""
    manifest = "".join(
        [
            f"Frozen copy of the Google after-side queue, from commit {commit}\n",
            f"Made {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by scripts/google_after_freeze.py\n",
            *(f"{sha256(data)}  {name}\n" for name, data in sorted(contents.items())),
        ]
    ).encode("utf-8")
    if any((d / ".git").exists() for d in (dest.resolve(), *dest.resolve().parents)):
        raise SystemExit(f"STOP: {dest} is inside a git folder; the frozen copy lives outside the repo")
    if dest.exists():
        present = {p.name for p in dest.iterdir()}
        extra = present - set(contents) - {"MANIFEST.txt"}
        changed = [n for n in contents if n in present and (dest / n).read_bytes() != contents[n]]
        if extra or changed:
            raise SystemExit(
                f"STOP: {dest} already holds other files ({sorted(extra | set(changed))}); move it away first"
            )
        if set(contents) <= present:
            print(f"{dest} already holds these files, byte for byte: nothing written")
            return dest / "MANIFEST.txt"
    dest.mkdir(parents=True, exist_ok=True)
    for name, data in contents.items():
        (dest / name).write_bytes(data)
    (dest / "MANIFEST.txt").write_bytes(manifest)
    return dest / "MANIFEST.txt"


def git_show(commit: str, path: str) -> bytes:
    return subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True, check=True).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", required=True, help="the commit to copy from, e.g. main after the PR merges")
    parser.add_argument(
        "--dest", type=Path, required=True, help=r"a folder outside any git repo, e.g. C:\kestrel-queue"
    )
    args = parser.parse_args()
    commit = subprocess.run(
        ["git", "rev-parse", f"{args.commit}^{{commit}}"], capture_output=True, text=True, check=True
    )
    full = commit.stdout.strip()
    contents = {Path(p).name: git_show(full, p) for p in FILES}
    if problems := check(contents):
        print("STOP: " + "; ".join(problems))
        return 1
    manifest = write(contents, args.dest, full)
    print(manifest.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
