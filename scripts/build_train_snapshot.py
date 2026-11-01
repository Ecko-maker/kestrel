"""Build the Kaggle training code upload (the kestrel-distill-code dataset) from a commit (no model calls).

    uv run --no-sync python scripts/build_train_snapshot.py                 # HEAD -> dist/kaggle-train/
    uv run --no-sync python scripts/build_train_snapshot.py --commit <sha> --out <folder>

The zip holds, flat (Kaggle datasets have no subfolders): train.py, train_format.py, train_config.toml, the
synthetic smoke train.jsonl (the GPU smoke test's data, never training data) and a COMMIT file with the
full commit. Every file is read from the commit with `git show`, not from the working tree, so the zip is
exactly that commit's code, and the commit must be clean. train.py records COMMIT in lineage.json and
refuses a real run without it. SHA256SUMS lists each file's hash. No dataset goes in: the training data
is a separate private dataset (distill/KAGGLE.md, "Training run").
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {  # name in the zip -> path in the repo
    "train.py": "distill/train.py",
    "train_format.py": "src/kestrel/distill/train_format.py",
    "train_config.toml": "distill/train_config.toml",
    "train.jsonl": "distill/smoke/train.jsonl",
}
FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # zip entry times: the same commit always gives the same bytes


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def build(commit: str, out: Path) -> Path:
    full = git("rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
    if commit == "HEAD" and git("status", "--porcelain").strip():
        raise SystemExit("the working tree isn't clean: commit first, so the snapshot is a commit")
    contents = {name: git("show", f"{full}:{path}") for name, path in FILES.items()}
    contents["COMMIT"] = (full + "\n").encode()
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(contents.items()))
    contents["SHA256SUMS"] = sums.encode()
    out.mkdir(parents=True, exist_ok=True)
    zpath = out / f"kestrel-train-code-{full[:12]}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(contents):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, contents[name])
    return zpath


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", default="HEAD", help="the commit to snapshot (default HEAD, which must be clean)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "kaggle-train", help="folder for the zip")
    args = parser.parse_args(argv)
    zpath = build(args.commit, args.out)
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    print(f"{zpath}\n{zpath.stat().st_size:,} bytes, sha256 {digest}")
    print(zipfile.ZipFile(zpath).read("COMMIT").decode().strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
