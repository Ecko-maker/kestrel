"""Which benchmark results were touched by rate limits, timeouts or step limits? (read-only)

Usage: uv run python scripts/audit_rate_limits.py <results.json>... [--overlap 2026-10-05T17:05 2026-10-05T17:20]

For every task it reads the task's traces in logs/bench.db and reports:
- retries: model calls that were retried (429 / rate limit / server errors) but then answered;
- rate_limited: a model call that gave up on a rate limit (the task is then 'error', never graded);
- tool_timeout: a tool call that timed out; max_steps: the agent hit its step limit;
- judge_error: the judge call failed (the task is then 'error');
- overlap: the task ran inside the given time window (e.g. while a duplicate job ran).
The key question: is any GRADED task (pass/fail) affected? Those are listed separately.
"""

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DB = Path("logs") / "bench.db"
RATE = ("429", "rate limit", "rate_limit", "ratelimit")


def ts(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp()


def audit(path: Path, con: sqlite3.Connection, window: tuple[float, float] | None) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for t in data["tasks"]:
        flags: dict[str, object] = {}
        for trace_id in t.get("trace_ids", []):
            trace = con.execute("SELECT start_time, stop_reason FROM traces WHERE trace_id = ?", (trace_id,)).fetchone()
            if trace is None:
                flags["trace_missing"] = True
                continue
            if window and window[0] <= trace[0] <= window[1]:
                flags["overlap"] = True
            if trace[1] == "max_steps":
                flags["max_steps"] = True
            for name, error, attrs in con.execute(
                "SELECT name, error, attributes FROM spans WHERE trace_id = ?", (trace_id,)
            ):
                a = json.loads(attrs or "{}")
                text = f"{error or ''} {a.get('gen_ai.tool.call.result', '') if name == 'tool_call' else ''}".lower()
                if name == "llm_call":
                    if a.get("kestrel.retries"):
                        flags["retries"] = int(flags.get("retries", 0)) + int(a["kestrel.retries"])  # type: ignore[call-overload]
                    if any(r in text for r in RATE):
                        flags["rate_limited"] = True
                if name == "tool_call" and ("timed out" in text or "timeout" in text):
                    flags["tool_timeout"] = True
        if any(r in (t.get("error") or "").lower() for r in RATE):
            flags["rate_limited"] = True
        verdict = t.get("judge") or {}
        if verdict and verdict.get("score") is None:
            flags["judge_error"] = True
        if flags:
            rows.append({"id": t["id"], "status": t["status"], **flags})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", type=Path, nargs="+")
    parser.add_argument("--overlap", nargs=2, metavar=("START", "END"), help="UTC times, e.g. 2026-10-05T17:05")
    args = parser.parse_args()
    window = (ts(args.overlap[0]), ts(args.overlap[1])) if args.overlap else None
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    for path in args.files:
        rows = audit(path, con, window)
        graded = [r for r in rows if r["status"] in ("pass", "fail")]
        print(f"== {path.name}: {len(rows)} task(s) flagged, {len(graded)} of them graded")
        for r in rows:
            flags = ", ".join(f"{k}={v}" if v is not True else k for k, v in r.items() if k not in ("id", "status"))
            print(f"  {'GRADED ' if r in graded else '       '}{r['status']:8} {r['id']:<32} {flags}")
    con.close()


if __name__ == "__main__":
    main()
