"""How much Groq quota is free now? (read-only; no model calls)

Usage: uv run --no-sync python scripts/groq_quota.py [--runs ..\\kestrel-runs] [--need 55000]

Groq's free tier allows 200,000 tokens a day for gpt-oss-120b; cached input doesn't count. The project's
rule (CLAUDE.md) keeps a 15,000 margin: free = 200,000 - billable used in the last 24 h - 15,000. Used is:
- local: every Groq llm_call span of the last 24 h in the trace databases (`logs/*.db` here and in the runs
  folder), input - cached + output;
- CI: every GitHub Actions run created in the last 24 h whose KestrelBench job graded tasks, read from the
  job log's "Pass rate ... (N billable)" line (a job that wasn't needed prints no such line). Needs `gh`.
A CI run that can't be counted (still running, or a KestrelBench job that ran over 30 s without a readable
total, e.g. cancelled partway) is listed, and the result is marked not final.
"""

import argparse
import json
import re
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

LIMIT, MARGIN = 200_000, 15_000
BILLABLE = re.compile(r"Pass rate .*\(([\d,]+) billable\)")


def local_billable(db: Path, since: datetime) -> tuple[int, int]:
    """(Groq calls, billable tokens) in one trace database since a time; opened read-only."""
    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT start_time, attributes FROM spans WHERE name = 'llm_call'").fetchall()
    except sqlite3.OperationalError:  # not a trace database
        return 0, 0
    finally:
        con.close()
    calls = billable = 0
    for start, attrs in rows:
        a = json.loads(attrs)
        if start < since.timestamp() or a.get("gen_ai.provider.name") != "groq":
            continue
        tokens_in = int(a.get("gen_ai.usage.input_tokens") or 0)
        cached = int(a.get("kestrel.usage.cached_input_tokens") or 0)
        billable += tokens_in - cached + int(a.get("gen_ai.usage.output_tokens") or 0)
        calls += 1
    return calls, billable


def billable_from_log(log: str) -> int | None:
    """The billable tokens a KestrelBench CI job graded with, or None if it graded nothing."""
    m = BILLABLE.search(log)
    return int(m.group(1).replace(",", "")) if m else None


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


NOT_RUN_S = 30  # a KestrelBench job that wasn't needed finishes in 4-17 s; grading 16 tasks takes minutes


def _seconds(job: dict) -> float:
    try:
        start = datetime.fromisoformat(job["startedAt"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(job["completedAt"].replace("Z", "+00:00"))
    except KeyError, TypeError, ValueError:
        return 0.0
    return max(0.0, (end - start).total_seconds())


def ci_billable(since: datetime) -> tuple[list[tuple[str, str, int]], list[str]]:
    """([(run id, created, billable)], [runs that can't be counted]) for runs created since a time. Can't be
    counted: still running, or a job that ran long enough to grade but has no readable total (e.g. cancelled
    partway: its tokens were spent, but its log is gone)."""
    runs = json.loads(gh("run", "list", "--limit", "100", "--json", "databaseId,createdAt,status,headBranch"))
    counted, unknown = [], []
    for r in runs:
        created = datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))
        if created < since:
            continue
        jobs = json.loads(gh("run", "view", str(r["databaseId"]), "--json", "jobs"))["jobs"]
        for job in (j for j in jobs if j["name"].startswith("KestrelBench")):
            label = f"{r['databaseId']} ({r['headBranch']})"
            if r["status"] != "completed":
                unknown.append(f"{label}: still running")
                continue
            if _seconds(job) <= NOT_RUN_S:
                continue  # skipped, cancelled before grading, or "KestrelBench not run"
            try:
                tokens = billable_from_log(gh("run", "view", "--job", str(job["databaseId"]), "--log"))
            except subprocess.CalledProcessError:
                tokens = None
            if tokens:
                counted.append((str(r["databaseId"]), r["createdAt"], tokens))
            else:
                unknown.append(f"{label}: KestrelBench ran {_seconds(job):.0f} s ({job.get('conclusion')}), no total")
    return counted, unknown


def free(used: int) -> int:
    return LIMIT - used - MARGIN


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=Path, default=Path(__file__).resolve().parents[2] / "kestrel-runs")
    parser.add_argument("--need", type=int, help="exit 1 unless at least this much is free")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    since = now - timedelta(hours=24)
    print(f"Window: {since:%Y-%m-%d %H:%M} to {now:%Y-%m-%d %H:%M} UTC")
    used = 0
    folders = [Path(__file__).resolve().parents[1] / "logs", args.runs / "logs"]
    for db in sorted({p.resolve() for f in folders if f.is_dir() for p in f.glob("*.db")}):
        calls, tokens = local_billable(db, since)
        if calls:
            print(f"  local {db}: {calls} Groq calls, {tokens:,} billable")
        used += tokens
    counted, pending = ci_billable(since)
    for run_id, created, tokens in counted:
        print(f"  CI run {run_id} ({created}): {tokens:,} billable")
        used += tokens
    for run in pending:
        print(f"  CI run {run}: NOT counted")
    left = free(used)
    print(f"Free: {LIMIT:,} - {used:,} used in the last 24 h - {MARGIN:,} margin = {left:,}")
    if pending:
        print("Not final: a CI run can't be counted (wait for a running one; ask the owner about a cancelled one).")
    if args.need is not None:
        ok = left >= args.need and not pending
        print(f"{'OK' if ok else 'NOT ENOUGH'}: need {args.need:,}")
        return 0 if ok else 1
    return 1 if pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
