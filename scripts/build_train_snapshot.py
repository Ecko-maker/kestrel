"""Build the Kaggle training code upload (the kestrel-distill-code dataset) from a commit (no model calls).

    uv run --no-sync python scripts/build_train_snapshot.py                 # HEAD -> dist/kaggle-train/
    uv run --no-sync python scripts/build_train_snapshot.py --commit <sha> --out <folder>
    uv run --no-sync python scripts/build_train_snapshot.py --dataset DIR --out <folder>   # the data upload

The zip holds, flat (Kaggle datasets have no subfolders): train.py, train_format.py, train_config.toml, the
synthetic smoke train.jsonl (the GPU smoke test's data, never training data) and a COMMIT file with the
full commit. Every file is read from the commit with `git show`, not from the working tree, so the zip is
exactly that commit's code, and the commit must be clean. train.py records COMMIT in lineage.json and
refuses a real run without it. SHA256SUMS lists each file's hash. No dataset goes in: the training data
is a separate private dataset (distill/KAGGLE.md, "Training run").

--dataset DIR zips that upload instead: a `kestrel distill build` folder's train.jsonl, val.jsonl and
manifest.json, flat, with SHA256SUMS, named after the manifest's hash. It refuses a manifest train.py would
refuse (another teacher, another filter pipeline: train_config.toml [data]), so the wrong data (e.g. option c's
evidence-only d1.2-pass1) can't be packed. It's teacher output: upload it as a PRIVATE Kaggle dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
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
DATA_FILES = ("train.jsonl", "val.jsonl", "manifest.json")


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def write_zip(zpath: Path, contents: dict[str, bytes]) -> Path:
    zpath.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(contents):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, contents[name])
    return zpath


def sums(contents: dict[str, bytes]) -> bytes:
    return "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in sorted(contents.items())).encode()


def build_dataset(data: Path, out: Path) -> Path:
    """The training-data upload: the three files of a build folder, checked as train.py checks them."""
    missing = [n for n in DATA_FILES if not (data / n).is_file()]
    if missing:
        raise SystemExit(f"{data} is missing {', '.join(missing)}")
    cfg = tomllib.loads((ROOT / "distill" / "train_config.toml").read_text(encoding="utf-8"))["data"]
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("teachers") != cfg["teachers"]:
        raise SystemExit(f"{data}: the manifest's teachers are {manifest.get('teachers')}, not {cfg['teachers']}")
    if (manifest.get("settings") or {}).get("filters") != cfg["filters"]:
        raise SystemExit(f"{data}: not built by today's `kestrel distill build` pipeline")
    contents = {n: (data / n).read_bytes() for n in DATA_FILES}
    contents["SHA256SUMS"] = sums(contents)
    tag = hashlib.sha256(contents["manifest.json"]).hexdigest()[:12]
    return write_zip(out / f"kestrel-train-data-{data.name}-{tag}.zip", contents)


def build(commit: str, out: Path) -> Path:
    full = git("rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
    if commit == "HEAD" and git("status", "--porcelain").strip():
        raise SystemExit("the working tree isn't clean: commit first, so the snapshot is a commit")
    contents = {name: git("show", f"{full}:{path}") for name, path in FILES.items()}
    contents["COMMIT"] = (full + "\n").encode()
    contents["SHA256SUMS"] = sums(contents)
    return write_zip(out / f"kestrel-train-code-{full[:12]}.zip", contents)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", default="HEAD", help="the commit to snapshot (default HEAD, which must be clean)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "kaggle-train", help="folder for the zip")
    parser.add_argument("--dataset", type=Path, help="zip this distill build folder (the data upload) instead")
    args = parser.parse_args(argv)
    zpath = build_dataset(args.dataset, args.out) if args.dataset else build(args.commit, args.out)
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    print(f"{zpath}\n{zpath.stat().st_size:,} bytes, sha256 {digest}")
    z = zipfile.ZipFile(zpath)
    print(z.read("COMMIT").decode().strip() if "COMMIT" in z.namelist() else z.read("SHA256SUMS").decode().strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
