"""Check a trained adapter's lineage.json before export (distill/KAGGLE.md step 10; no model calls).

    uv run --no-sync python scripts/check_lineage.py ADAPTER_DIR\\lineage.json --code-commit <the code zip's COMMIT>

Each line is OK or FAIL; exit 1 on any FAIL. What it checks, against what is pinned in git:
- a real run (not --smoke), on the pinned base model revision (train_config.toml);
- trained on the option a training set: dataset.manifest_sha256 is the SHA-256 of the pinned build manifest
  (distill/collection-a/), and dataset.teachers is the config's one teacher;
- the training code: code_commit equals --code-commit (the COMMIT inside the uploaded code zip) and is a
  commit of this repository;
- the run finished: finite losses, at least one epoch, train and val example counts as in the build manifest.
The tool schemas are checked by `export.py --verify` (against what Kestrel sends now).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BUILD_MANIFEST = ROOT / "distill" / "collection-a" / "build-manifest-d1.2-groq-gpt-oss-120b.json"


def checks(lineage: dict[str, Any], code_commit: str | None) -> list[tuple[bool, str]]:
    cfg = tomllib.loads((ROOT / "distill" / "train_config.toml").read_text(encoding="utf-8"))
    build = json.loads(BUILD_MANIFEST.read_text(encoding="utf-8"))
    ds, base, res = lineage.get("dataset") or {}, lineage.get("base_model") or {}, lineage.get("results") or {}
    want_manifest = hashlib.sha256(BUILD_MANIFEST.read_bytes()).hexdigest()
    commit = lineage.get("code_commit") or ""
    known = bool(commit) and subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=ROOT).returncode == 0
    losses = [res.get("train_loss"), res.get("best_eval_loss")]
    return [
        (lineage.get("smoke") is False, f"a real run, not --smoke (smoke = {lineage.get('smoke')})"),
        (
            base.get("id") == cfg["base_model"]["id"] and base.get("revision") == cfg["base_model"]["revision"],
            f"base model {base.get('id')} @ {str(base.get('revision'))[:8]} "
            f"(pinned {cfg['base_model']['revision'][:8]})",
        ),
        (
            ds.get("manifest_sha256") == want_manifest,
            f"dataset manifest {str(ds.get('manifest_sha256'))[:12]} = pinned build",
        ),
        (ds.get("teachers") == cfg["data"]["teachers"], f"teachers {ds.get('teachers')}"),
        (
            (ds.get("train_examples"), ds.get("val_examples")) == (build["stats"]["train"], build["stats"]["val"]),
            f"examples {ds.get('train_examples')} train / {ds.get('val_examples')} val "
            f"(build: {build['stats']['train']} / {build['stats']['val']})",
        ),
        (
            known and (code_commit is None or commit.startswith(code_commit) or code_commit.startswith(commit)),
            f"code commit {commit[:12] or 'missing'}"
            + (f" = the code zip's {code_commit[:12]}" if code_commit else " (no --code-commit given)")
            + ("" if known else ": not a commit of this repository"),
        ),
        (all(isinstance(x, float) and math.isfinite(x) for x in losses), f"losses finite: {losses}"),
        ((res.get("epochs") or 0) >= 1, f"epochs {res.get('epochs')}, steps {res.get('steps')}"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("lineage", type=Path)
    parser.add_argument("--code-commit", help="the COMMIT inside the uploaded code zip (required for a real check)")
    args = parser.parse_args(argv)
    lineage = json.loads(args.lineage.read_text(encoding="utf-8"))
    results = checks(lineage, args.code_commit)
    for ok, what in results:
        print(f"{'OK  ' if ok else 'FAIL'} {what}")
    if args.code_commit is None:
        print("FAIL --code-commit not given: pass the COMMIT from the code zip")
        return 1
    print(
        "lineage OK: export it (distill/EXPORT.md)" if all(ok for ok, _ in results) else "lineage NOT OK: don't export"
    )
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
