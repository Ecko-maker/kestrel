"""A pinnable manifest of a distill collection run on a provider (option a): no answers, no traces.

    uv run --no-sync python scripts/collection_manifest.py RESULTS --db DB --out distill/collection-a/FILE.json

From the results file: its meta (the agent fingerprint holds hashes and settings only) and summary counts,
and per task its id, status, number of traces and the session they belong to. From the trace database
(opened read-only, a copy, never the live file): its SHA-256 and trace count. The answers, tool logs and
message text are left out, so the manifest can go in git while the data stays outside it.

The sessions make the split checkable from the manifest alone: `kestrel distill build` assigns train or val
by a hash of the session (dataset.in_val), so one session per task means one side per task
(tests/test_distill_collection_a.py rebuilds the build manifest's train/val counts from this file).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(results: Path, db: Path) -> dict[str, Any]:
    data = json.loads(results.read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        session = {t: s for t, s in conn.execute("SELECT trace_id, session_id FROM traces")}
        traces_in_db = len(session)
    finally:
        conn.close()
    tasks = []
    for r in data["tasks"]:
        sessions = sorted({session.get(t) or "?" for t in r.get("trace_ids") or []})
        tasks.append(
            {
                "id": r["id"],
                "category": r.get("category"),
                "repeat": r.get("repeat"),
                "status": r["status"],
                "task_sha": r.get("task_sha"),
                "traces": len(r.get("trace_ids") or []),
                "sessions": sessions,
                "providers": r.get("providers"),
            }
        )
    s = data.get("summary", {})
    return {
        "results_file": results.name,
        "results_sha256": sha256(results),
        "traces_db_sha256": sha256(db),
        "traces_in_db": traces_in_db,
        "traces_named_by_results": sum(t["traces"] for t in tasks),
        "meta": data["meta"],
        "summary": {k: s.get(k) for k in ("runs", "graded", "passed", "errors", "skipped", "excluded")}
        | {"billable_tokens_total": s.get("billable_tokens_total")},
        "tasks": tasks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("results", type=Path)
    parser.add_argument("--db", type=Path, required=True, help="a copy of the run's trace database")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest(args.results, args.db), indent=1, ensure_ascii=False) + "\n"
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"{args.out}  sha256 {sha256(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
