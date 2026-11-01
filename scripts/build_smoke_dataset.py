"""Rebuild distill/smoke (the synthetic training set) with today's `kestrel distill build` (no model calls).

    uv run --no-sync python scripts/build_smoke_dataset.py            # rewrite distill/smoke/*.jsonl + manifest
    uv run --no-sync python scripts/build_smoke_dataset.py --check    # build into a temp folder and compare

The source is distill/smoke/traces.json: nine made-up conversations about a garden and a bike co-op, as
trace rows (rated /good, every step on groq + openai/gpt-oss-120b, the teacher in teachers.toml). They go
into a temporary trace database with kestrel.tracing.SCHEMA, and `distill build` turns them into
train.jsonl, val.jsonl and manifest.json exactly as it will the real collection, every filter included.
So the smoke set the CPU smoke run and the Kaggle GPU smoke test train on is in the format a d1.2 build
writes today. It never reads logs/ or any collected data (the option c pass-1 data is evidence only).

--check compares the two JSONL files byte for byte (line endings aside) and the manifest without its `git` and
`input_db_sha256` fields (the commit changes with every commit; the database bytes with SQLite's version).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

from kestrel.distill import dataset as ds
from kestrel.tracing import SCHEMA

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "distill" / "smoke"
TEACHER = ("groq", "openai/gpt-oss-120b")
VAL_FRAC = 0.3  # 9 sessions: about 3 in val, so both files have examples
VOLATILE = ("git", "input_db_sha256")


def write_db(path: Path, rows: list[dict[str, Any]]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        with conn:
            for r in rows:
                user = next(m["content"] for m in reversed(r["messages"]) if m["role"] == "user")
                conn.execute(
                    "INSERT INTO traces (trace_id, start_time, status, stop_reason, steps, user_message, providers,"
                    " messages, rating, session_id) VALUES (?, ?, 'ok', 'answered', 1, ?, ?, ?, 'good', ?)",
                    (
                        r["trace_id"],
                        r["start_time"],
                        user,
                        json.dumps([TEACHER[0]]),
                        json.dumps(r["messages"]),
                        r["session_id"],
                    ),
                )
                attrs = {"gen_ai.provider.name": TEACHER[0], "gen_ai.request.model": TEACHER[1]}
                conn.execute(
                    "INSERT INTO spans (span_id, trace_id, parent_id, name, start_time, attributes)"
                    " VALUES (?, ?, ?, 'llm_call', ?, ?)",
                    (f"{r['trace_id']}-0", r["trace_id"], r["trace_id"], r["start_time"], json.dumps(attrs)),
                )
    finally:
        conn.close()


def build(out: Path) -> int:
    rows = json.loads((SMOKE / "traces.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "smoke-traces.db"
        write_db(db, rows)
        args = argparse.Namespace(db=db, results=[], out=out, max_tokens=8000, val_frac=VAL_FRAC)
        return ds.main(args)


def _text(path: Path) -> bytes:
    """The file's bytes with line endings normalised (a Windows checkout may hold CRLF)."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def stable_manifest(path: Path) -> dict[str, Any]:
    return {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k not in VOLATILE}


def check() -> list[str]:
    """What differs between distill/smoke and a fresh build (empty: up to date)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "smoke"
        if build(out) != 0:
            return ["distill build failed"]
        diffs = [n for n in ("train.jsonl", "val.jsonl") if _text(out / n) != _text(SMOKE / n)]
        if stable_manifest(out / "manifest.json") != stable_manifest(SMOKE / "manifest.json"):
            diffs.append("manifest.json")
        return diffs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="compare with a fresh build; write nothing")
    args = parser.parse_args(argv)
    if args.check:
        diffs = check()
        print("distill/smoke is up to date." if not diffs else f"Out of date: {', '.join(diffs)}. Rebuild it.")
        return 1 if diffs else 0
    return build(SMOKE)


if __name__ == "__main__":
    sys.exit(main())
