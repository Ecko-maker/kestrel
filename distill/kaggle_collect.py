"""Option c on Kaggle (known issue #72): the teacher selection and the collection's bookkeeping. No model calls.

collect.ipynb runs the models; this module only reads what they wrote, so all of it is tested offline.

- The teacher selection, as pre-registered in #72 on 2026-10-30 before any candidate ran: both candidates run
  the same 20 check-graded distill tasks (SLICE) once, `--judge none`. Per candidate: check-pass rate (every
  deterministic check ok; an errored run counts as a fail), tool-call validity (calls that weren't rejected as
  an unknown tool or bad arguments), Harmony leaks (runs with a chat-template control token, design decision
  22's filter) and time per task. A candidate that doesn't fit Kaggle's 2 x T4 or averages more than 3
  minutes per task is out. The higher check-pass rate wins; within 2 tasks: fewer leaks, then more valid
  tool calls, then less time per task.
- The collection runs in shards (`kestrel bench run --shard i/N`), each to its own results file and all into
  one logs/distill.db, so a Kaggle session that ends mid-run loses at most the shard it was in.
  check_shards() is the coverage check over all of them (scripts/check_results.py wants one complete file).

    python distill/kaggle_collect.py select --candidate DIR --candidate DIR [--out DIR]
    python distill/kaggle_collect.py check-shards FILE...
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kestrel.distill import ROOT
from kestrel.distill.dataset import _CONTROL_TOKEN, harmony_filter, open_readonly, read_traces

DISTILL_TASKS = ROOT / "distill" / "tasks"

# #72's slice: stats.stratified_sample over the 93 distill tasks without a rubric (d1.2), 20, by category,
# stable key = task id, seed 2026. tests/test_kaggle_collect.py draws it again and compares.
SLICE = (
    "dst-arith-lumber-per-bed",
    "dst-arith-seed-packets",
    "dst-arith-herb-bed-area",
    "dst-time-nairobi-date",
    "dst-files-insurance-deadline",
    "dst-files-shed-code-policy",
    "dst-files-grant-purpose",
    "dst-act-email-aiko-volunteer",
    "dst-act-note-spring-seeds",
    "dst-act-email-waitlist",
    "dst-adapt-add-arrival-day",
    "dst-adapt-append-padlock",
    "dst-forbid-all-csv",
    "dst-forbid-old-quote",
    "dst-notool-austen",
    "dst-multi-three-loaves",
    "dst-multi-q3-net",
    "dst-multi-rental-quote",
    "dst-web-sqlite-site",
    "dst-convo-rental-price",
)
SLICE_SEED = 2026
TIME_LIMIT_S = 180.0  # a mean above this per task is out: a 122-task pass wouldn't fit the weekly GPU quota
TIE_TASKS = 2  # check-pass counts this close go to the tie-breaks
GPU_MEMORY_GB = 16  # each of Kaggle's two free T4s


@dataclass(frozen=True)
class Candidate:
    name: str  # as #72 names it
    tag: str  # what `ollama pull` fetches (Q4)
    served_as: str  # the model name Kestrel sends, and traces and teachers.toml record
    template: str  # "ollama": the package's own; "kestrel": train_format.OLLAMA_TEMPLATE (known issue #73)


CANDIDATES = (
    # gpt-oss's own package renders its Harmony format; Kestrel's template is Qwen's format and doesn't apply.
    Candidate("gpt-oss:20b", "gpt-oss:20b", "gpt-oss:20b", "ollama"),
    # Ollama's Qwen3 packages print tool schemas as Go struct dumps (#73, seen on the 4B): served with the
    # template the student is trained on, so the teacher sees the same tool text as the student.
    Candidate(
        "Qwen3-30B-A3B-Instruct-2507",
        "qwen3:30b-a3b-instruct-2507-q4_K_M",
        "kestrel-teacher-qwen3-30b-a3b",
        "kestrel",
    ),
)


def candidate(name: str) -> Candidate:
    for c in CANDIDATES:
        if name in (c.name, c.tag, c.served_as):
            return c
    raise KeyError(f"{name!r} is not a #72 candidate: {', '.join(c.name for c in CANDIDATES)}")


def fits(ps_model: dict[str, Any]) -> bool:
    """A model loaded by Ollama (one entry of GET /api/ps) runs fully on the GPUs: nothing spilled to CPU."""
    size, vram = int(ps_model.get("size") or 0), int(ps_model.get("size_vram") or 0)
    return size > 0 and vram >= size


# --- The teacher selection --------------------------------------------------------------------------------

_STATE = re.compile(r"\[(ran|not run \([^)]*\))\] -> (.*)$", re.DOTALL)


@dataclass
class Metrics:
    candidate: str
    runs: int = 0
    check_passes: int = 0
    errored: int = 0
    tool_calls: int = 0
    invalid_calls: int = 0
    leak_runs: int = 0
    leak_reasons: dict[str, int] = field(default_factory=dict)
    mean_seconds: float = 0.0
    fits: bool | None = None  # from the notebook's /api/ps check; None = not measured
    problems: list[str] = field(default_factory=list)  # the run isn't #72's design

    @property
    def valid_calls(self) -> int:
        return self.tool_calls - self.invalid_calls

    @property
    def check_pass_rate(self) -> float:
        return self.check_passes / self.runs if self.runs else 0.0

    def out_reason(self) -> str | None:
        if self.problems:
            return "not the pre-registered slice: " + "; ".join(self.problems)
        if self.fits is None:
            return "GPU fit not measured"
        if not self.fits:
            return f"doesn't fit Kaggle's 2 x T4 ({GPU_MEMORY_GB} GB each): part of it ran on the CPU"
        if self.mean_seconds > TIME_LIMIT_S:
            return f"{self.mean_seconds:.0f} s per task on average, over the {TIME_LIMIT_S:.0f} s limit"
        return None


def run_text(row: dict[str, Any]) -> str:
    return "\n".join(
        [str(row.get("answer") or ""), *map(str, row.get("tool_log") or []), *map(str, row.get("tool_calls") or [])]
    )


def metrics(name: str, results: dict[str, Any], traces: list[dict[str, Any]] | None, fit: bool | None) -> Metrics:
    """One candidate's numbers from its results file and (if given) its traces. A run leaks when its stored
    conversation fails the Harmony filter, or (without traces) when its answer or tool log holds a token."""
    rows = results.get("tasks", [])
    m = Metrics(candidate=name, fits=fit, runs=len(rows))
    ids = Counter(r["id"] for r in rows)
    if missing := sorted(set(SLICE) - set(ids)):
        m.problems.append(f"missing {', '.join(missing)}")
    if extra := sorted(set(ids) - set(SLICE)):
        m.problems.append(f"not in the slice: {', '.join(extra)}")
    if twice := sorted(t for t, n in ids.items() if n > 1):
        m.problems.append(f"more than one run of {', '.join(twice)}")
    meta = results.get("meta", {})
    if meta.get("split") != "distill" or meta.get("judge") is not None:
        m.problems.append("not a distill run with --judge none")
    by_trace = {t["trace_id"]: t for t in traces or []}
    seconds = []
    leaks: Counter[str] = Counter()
    for r in rows:
        if r["status"] == "error":
            m.errored += 1
        elif r["status"] in ("pass", "fail") and all(c.get("ok") for c in r.get("checks") or []):
            m.check_passes += 1
        elif r["status"] not in ("pass", "fail"):
            m.problems.append(f"{r['id']}: status {r['status']}")
        for line in r.get("tool_log") or []:
            m.tool_calls += 1
            found = _STATE.search(str(line))
            if found and found.group(1).startswith("not run") and found.group(2).startswith("Error:"):
                m.invalid_calls += 1
        seconds.append(float(r.get("latency_ms") or 0.0) / 1000)
        reason = None
        for tid in r.get("trace_ids") or []:
            if tid in by_trace and by_trace[tid].get("messages"):
                reason = reason or harmony_filter(json.loads(by_trace[tid]["messages"]))
        if reason is None and _CONTROL_TOKEN.search(run_text(r)):
            reason = "harmony_leak_text"
        if reason:
            leaks[reason] += 1
    m.leak_runs = sum(leaks.values())
    m.leak_reasons = dict(sorted(leaks.items()))
    m.mean_seconds = round(sum(seconds) / len(seconds), 1) if seconds else 0.0
    return m


def decide(a: Metrics, b: Metrics) -> tuple[str | None, str]:
    """#72's rule: (the winner's name or None, why)."""
    out = {m.candidate: m.out_reason() for m in (a, b)}
    inside = [m for m in (a, b) if out[m.candidate] is None]
    if not inside:
        return None, "both candidates are out: " + "; ".join(f"{k}: {v}" for k, v in out.items())
    if len(inside) == 1:
        other = b if inside[0] is a else a
        return inside[0].candidate, f"{other.candidate} is out ({out[other.candidate]})"
    if abs(a.check_passes - b.check_passes) > TIE_TASKS:
        win = max(a, b, key=lambda m: m.check_passes)
        return win.candidate, f"higher check-pass rate ({a.check_passes} vs {b.check_passes} of {len(SLICE)})"
    for label, key in (
        ("fewer Harmony leaks", lambda m: -m.leak_runs),
        ("more valid tool calls", lambda m: m.valid_calls),
        ("less time per task", lambda m: -m.mean_seconds),
    ):
        if key(a) != key(b):
            win = max(a, b, key=key)
            return win.candidate, f"check-pass within {TIE_TASKS} tasks ({a.check_passes} vs {b.check_passes}); {label}"
    return None, "a tie on every criterion: #72's rule has no further tie-break, the owner decides"


def report(a: Metrics, b: Metrics) -> str:
    winner, why = decide(a, b)
    lines = [
        "# Option c teacher selection (known issue #72, pre-registered 2026-10-30)",
        "",
        f"Slice: the {len(SLICE)} check-graded distill tasks listed in #72, once each, `--judge none`.",
        "",
        "| Candidate | Check-pass | Errored | Valid tool calls | Harmony leaks (runs) | Mean s/task | Fits 2 x T4 "
        "| Out |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m in (a, b):
        fit = {True: "yes", False: "no", None: "not measured"}[m.fits]
        leaks = f"{m.leak_runs}" + (
            f" ({', '.join(f'{k} {v}' for k, v in m.leak_reasons.items())})" if m.leak_runs else ""
        )
        lines.append(
            f"| {m.candidate} | {m.check_passes}/{m.runs} ({m.check_pass_rate:.0%}) | {m.errored} | "
            f"{m.valid_calls}/{m.tool_calls} | {leaks} | {m.mean_seconds:.0f} | {fit} | {m.out_reason() or '-'} |"
        )
    lines += ["", f"**Winner: {winner or 'none'}** ({why})."]
    return "\n".join(lines) + "\n"


def load_candidate(folder: Path) -> Metrics:
    """A compare-mode folder: results.json, logs/distill.db and manifest.json (with the fit check)."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    results_path = folder / "results.json"
    db = folder / "logs" / "distill.db"
    if not results_path.is_file():  # e.g. the model didn't fit, so it never ran
        empty = Metrics(candidate=manifest["teacher"]["name"], fits=manifest.get("gpu", {}).get("fits"))
        empty.problems.append("no results (it didn't run)")
        return empty
    traces = None
    if db.is_file():
        conn = open_readonly(db)
        try:
            traces = read_traces(conn)
        finally:
            conn.close()
    results = json.loads(results_path.read_text(encoding="utf-8"))
    return metrics(manifest["teacher"]["name"], results, traces, manifest.get("gpu", {}).get("fits"))


# --- The collection's shards ------------------------------------------------------------------------------


def shard_file(served_as: str, i: int, n: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", served_as)
    return f"distill-d1.2-{safe}-shard-{i}-of-{n}.json"


def shard_state(path: Path) -> str:
    """missing / complete (every row graded) / partial (skipped or errored rows: run it again with --resume)."""
    if not path.is_file():
        return "missing"
    rows = json.loads(path.read_text(encoding="utf-8")).get("tasks", [])
    return "complete" if rows and all(r["status"] in ("pass", "fail", "excluded") for r in rows) else "partial"


def check_shards(paths: list[Path], tasks_dir: Path = DISTILL_TASKS) -> list[str]:
    """Problems with a sharded collection: one agent, teacher and split version; shards 1..N once each; every
    distill task exactly once; nothing skipped or errored. Empty = it can go to `kestrel distill build`."""
    from kestrel.bench.tasks import load_tasks

    problems: list[str] = []
    metas = []
    seen: Counter[str] = Counter()
    for p in paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        meta = data.get("meta", {})
        metas.append(meta)
        for r in data.get("tasks", []):
            seen[r["id"]] += 1
            if r["status"] not in ("pass", "fail", "excluded"):
                problems.append(f"{p.name}: {r['id']} is {r['status']}")
    if not metas:
        return ["no results files"]
    for key in ("split", "split_version", "provider", "model", "judge"):
        values = {json.dumps(m.get(key)) for m in metas}
        if len(values) > 1:
            problems.append(f"the files differ in {key}: {sorted(values)}")
    agents = {(m.get("agent") or {}).get("sha") for m in metas}
    if len(agents) > 1:
        problems.append(f"the files differ in agent fingerprint: {sorted(map(str, agents))}")
    if any(m.get("split") != "distill" for m in metas):
        problems.append("a file isn't a distill run")
    shards = Counter(str(m.get("shard")) for m in metas)
    totals = {s.split("/")[1] for s in shards if "/" in s}
    if len(totals) != 1:
        problems.append(f"shards of different sizes or no --shard: {sorted(shards)}")
    else:
        n = int(totals.pop())
        want = {f"{i}/{n}" for i in range(1, n + 1)}
        if missing := sorted(want - set(shards)):
            problems.append(f"missing shard(s) {', '.join(missing)}")
        problems += [f"shard {s} appears {k} times" for s, k in shards.items() if k > 1]
    ids = {t.id for t in load_tasks(tasks_dir)}
    if missing_ids := sorted(ids - set(seen)):
        problems.append(f"{len(missing_ids)} distill task(s) missing: {', '.join(missing_ids[:5])}")
    if unknown := sorted(set(seen) - ids):
        problems.append(f"not distill tasks: {', '.join(unknown[:5])}")
    problems += [f"{t} appears {k} times" for t, k in seen.items() if k > 1]
    return problems


# --- The manifest -----------------------------------------------------------------------------------------


def manifest(
    *,
    mode: str,
    teacher: Candidate,
    digest: str | None,
    ollama_version: str,
    gpus: list[dict[str, Any]],
    fit: bool | None,
    commit: str | None,
    files: list[str],
    started: str,
    finished: str,
    agent: str | None,
) -> dict[str, Any]:
    """What a Kaggle run records next to its results: the teacher (model, the pulled digest, how it was
    served), the GPUs it ran on and whether it fit, the Ollama version and the code commit."""
    return {
        "mode": mode,
        "teacher": asdict(teacher) | {"digest": digest, "provider": "ollama"},
        "ollama_version": ollama_version,
        "gpu": {"devices": gpus, "fits": fit},
        "commit": commit,
        "agent_fingerprint": agent,
        "files": files,
        "started": started,
        "finished": finished,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sel = sub.add_parser("select", help="#72's teacher selection from two compare-mode folders")
    sel.add_argument("--candidate", type=Path, action="append", required=True, help="a compare-mode folder")
    sel.add_argument("--out", type=Path, help="write selection.json and selection.md here")
    chk = sub.add_parser("check-shards", help="coverage check over a sharded collection's results files")
    chk.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args(argv)
    if args.command == "select":
        if len(args.candidate) != 2:
            parser.error("give exactly two --candidate folders")
        a, b = (load_candidate(f) for f in args.candidate)
        text = report(a, b)
        print(text, end="")
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            winner, why = decide(a, b)
            data = {"winner": winner, "why": why, "candidates": [asdict(a), asdict(b)], "slice": list(SLICE)}
            (args.out / "selection.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            (args.out / "selection.md").write_text(text, encoding="utf-8")
        return 0
    problems = check_shards(args.files)
    for p in problems:
        print(f"PROBLEM: {p}")
    print(
        "Shards OK: every distill task once, nothing skipped or errored."
        if not problems
        else f"{len(problems)} problem(s)."
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
