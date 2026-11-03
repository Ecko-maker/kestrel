"""How much Groq quota is free now? (read-only; no model calls)

Usage: uv run --no-sync python scripts/groq_quota.py [--runs ..\\kestrel-runs] [--need 55000]

Groq's free tier allows 200,000 tokens a day for gpt-oss-120b; cached input doesn't count. The project's
rule (CLAUDE.md) keeps a 15,000 margin: free = 200,000 - billable used in the last 24 h - 15,000. Used is:
- local: every Groq llm_call span of the last 24 h in the trace databases (`logs/*.db` here and in the runs
  folder), input - cached + output;
- local judge: the judge's calls aren't traced, so a run's `judge_tokens` (results files in `evals/results`
  here and in the runs folder, judge on groq) count when the run's trace started in the window. Found
  2026-11-03: the drift control's summary said 49,953 billable, the traces alone 45,034; the 4,919 between
  them are its judge tokens. Re-judged files (`*.rejudged-*`) are listed, not counted: their judge ran later
  than the traces they point to;
- CI: every GitHub Actions run created in the last 24 h whose KestrelBench job graded tasks, read from the
  job log's "Pass rate ... (N billable)" line. A job whose log says "KestrelBench not run" (or that GitHub
  skipped) used nothing, however long it took. Needs `gh`.
A CI job that can't be counted (still running, or cancelled or unreadable without the "not run" line or a total)
is listed, and the result is marked not final.
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


def trace_starts(db: Path) -> dict[str, float]:
    """trace id -> start time, from one trace database (read-only); empty if it isn't one."""
    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        return dict(con.execute("SELECT trace_id, start_time FROM traces").fetchall())
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()


def judge_billable(results: Path, starts: dict[str, float], since: datetime) -> tuple[int, int]:
    """(judged runs, judge tokens) in one results file whose judge is on groq, for runs whose first trace
    started since a time. Judge prompts aren't cached, so their tokens are all billable."""
    try:
        data = json.loads(results.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return 0, 0
    if not isinstance(data, dict) or not str((data.get("meta") or {}).get("judge") or "").startswith("groq"):
        return 0, 0
    runs = tokens = 0
    for row in data.get("tasks") or []:
        judged = int(row.get("judge_tokens") or 0)
        ids = row.get("trace_ids") or []
        if judged and ids and starts.get(ids[0], 0.0) >= since.timestamp():
            runs += 1
            tokens += judged
    return runs, tokens


def billable_from_log(log: str) -> int | None:
    """The billable tokens a KestrelBench CI job graded with, or None if it graded nothing."""
    m = BILLABLE.search(log)
    return int(m.group(1).replace(",", "")) if m else None


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


NOT_RUN = "KestrelBench not run"  # what the job prints when no eval was needed (scripts/ci/evals_needed.py)
NOT_RUN_KIND, COUNTED, UNKNOWN = "not run", "counted", "unknown"


def classify_job(run_status: str, job: dict, log: str | None) -> tuple[str, int, str]:
    """(kind, billable tokens, reason) for one KestrelBench CI job, from its log, never from its duration
    (2026-11-03: two "not run" jobs took 32-33 s and an earlier duration rule counted them as unknown).
    - not run: the log says "KestrelBench not run", or GitHub skipped the job (no step ran);
    - counted: the log has the run's "Pass rate ... (N billable)" line;
    - unknown (makes the result not final): the job is still running, or it was cancelled or its log can't be
      read without that line, or it finished without either line (it may have spent tokens before stopping)."""
    if run_status != "completed" or job.get("status") not in (None, "completed"):
        return UNKNOWN, 0, "still running"
    conclusion = job.get("conclusion")
    if conclusion == "skipped":
        return NOT_RUN_KIND, 0, "skipped"
    if log is None:
        return UNKNOWN, 0, f"{conclusion}, log unavailable"
    if NOT_RUN in log:
        return NOT_RUN_KIND, 0, NOT_RUN
    if (tokens := billable_from_log(log)) is not None:
        return COUNTED, tokens, "graded"
    if conclusion == "cancelled":
        return UNKNOWN, 0, "cancelled without a 'KestrelBench not run' line or a total"
    return UNKNOWN, 0, f"{conclusion} without a 'KestrelBench not run' line or a total"


def ci_billable(since: datetime) -> tuple[list[tuple[str, str, int]], list[str]]:
    """([(run id, created, billable)], [jobs that can't be counted, with why]) for runs created since a time."""
    runs = json.loads(gh("run", "list", "--limit", "100", "--json", "databaseId,createdAt,status,headBranch"))
    counted, unknown = [], []
    for r in runs:
        created = datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))
        if created < since:
            continue
        jobs = json.loads(gh("run", "view", str(r["databaseId"]), "--json", "jobs"))["jobs"]
        for job in (j for j in jobs if j["name"].startswith("KestrelBench")):
            log = None
            if r["status"] == "completed" and job.get("conclusion") != "skipped":
                try:
                    log = gh("run", "view", "--job", str(job["databaseId"]), "--log")
                except subprocess.CalledProcessError:
                    log = None  # e.g. a job cancelled before it wrote a log
            kind, tokens, why = classify_job(r["status"], job, log)
            if kind == COUNTED:
                counted.append((str(r["databaseId"]), r["createdAt"], tokens))
            elif kind == UNKNOWN:
                unknown.append(f"{r['databaseId']} ({r['headBranch']}): {why}")
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
    root = Path(__file__).resolve().parents[1]
    folders = [root / "logs", args.runs / "logs"]
    starts: dict[str, float] = {}
    for db in sorted({p.resolve() for f in folders if f.is_dir() for p in f.glob("*.db")}):
        calls, tokens = local_billable(db, since)
        if calls:
            print(f"  local {db}: {calls} Groq calls, {tokens:,} billable")
        used += tokens
        starts |= trace_starts(db)
    result_dirs = [root / "evals" / "results", args.runs / "evals" / "results"]
    for path in sorted({p.resolve() for d in result_dirs if d.is_dir() for p in d.glob("*.json")}):
        if ".rejudged-" in path.name:
            if path.stat().st_mtime >= since.timestamp():
                print(f"  re-judged {path.name}: written in the window, its judge tokens NOT counted (check by hand)")
            continue
        if path.stat().st_mtime < since.timestamp():
            continue  # unchanged since before the window: none of its runs can be in it
        runs, tokens = judge_billable(path, starts, since)
        if runs:
            print(f"  judge {path.name}: {runs} judged runs, {tokens:,} billable")
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
