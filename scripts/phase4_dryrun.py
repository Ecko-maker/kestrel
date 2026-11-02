"""Dry run of the Phase 4 report path (distill/EVAL.md, step 6) on fake files: no model calls, no Groq.

    uv run --no-sync python scripts/phase4_dryrun.py --out <empty folder>

Writes a fake teacher reference (100 tasks x 3, groq), two fake older references (x1 each, for check (b))
and three fake routed runs (x5, the student on ollama, the teacher on groq for about 1 task run in 5), each with
a trace database shaped like the real one (kestrel.tracing.SCHEMA, llm_call spans). Token counts vary
by task and escalations are drawn at random (seed 2026), so the intervals have a width. Then, per routed run,
the commands EVAL.md gives for the real files:

    kestrel bench cost ROUTED --db ROUTED.db --teacher TEACHER --teacher-db TEACHER.db --json COST.json
    kestrel bench compare TEACHER ROUTED --metric checks --b-references REF1 REF2 \\
        --cost-shown <from COST.json> --safety held --drift none

The three routed runs are built to give section 8.5's three outcomes: "pass" matches the teacher task for
task (PASS), "notshown" loses 3 tasks for good (the interval contains 0: NOT SHOWN), "fail" loses 25
(FAIL). A fourth call leaves out --safety and --drift (PENDING). The fake numbers prove the rendering and
the plumbing, nothing about any model: every file says FAKE in its meta.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import random
import sqlite3
import sys
from pathlib import Path
from typing import Any

from kestrel.bench import cli
from kestrel.tracing import SCHEMA

TASKS = [f"fake-{i:03d}" for i in range(100)]
TEACHER_FAILS = frozenset(TASKS[-7:])  # the teacher passes 93 of 100, every repeat
SCENARIOS = {"pass": 0, "notshown": 3, "fail": 25}  # tasks the routed system loses for good
ESCALATE = 0.2  # share of routed task runs that go to the teacher (drawn per run, seed 2026)
REASONS = ("invalid_tool_call", "empty_reply", "max_steps")
TEACHER_CALL = {"provider": "groq", "model": "openai/gpt-oss-120b", "input": 1500, "output": 150, "cached": 300}
STUDENT_CALL = {"provider": "ollama", "model": "kestrel-student", "input": 1400, "output": 120, "cached": 0}
META = {"suite_version": "1.1", "split": "main", "judge": "groq/openai/gpt-oss-120b", "judge_version": "v2"}


class TraceDB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.n = 0

    def trace(self, calls: list[tuple[dict[str, Any], str | None, str | None]]) -> str:
        """One trace with an llm_call span per (call, route, reason)."""
        self.n += 1
        tid = f"trace{self.n:06d}"
        self.conn.execute("INSERT INTO traces (trace_id, start_time, status) VALUES (?, ?, 'ok')", (tid, self.n))
        for k, (call, route, reason) in enumerate(calls):
            attrs: dict[str, Any] = {
                "gen_ai.provider.name": call["provider"],
                "gen_ai.request.model": call["model"],
                "gen_ai.usage.input_tokens": call["input"],
                "gen_ai.usage.output_tokens": call["output"],
                "kestrel.usage.cached_input_tokens": call["cached"],
            }
            if route:
                attrs |= {"kestrel.route": route, "kestrel.route_reason": reason}
            self.conn.execute(
                "INSERT INTO spans (span_id, trace_id, name, start_time, attributes) VALUES (?, ?, 'llm_call', ?, ?)",
                (f"{tid}-{k}", tid, self.n + k / 10, json.dumps(attrs)),
            )
        return tid

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


def row(task: str, repeat: int, passed: bool, trace_id: str, latency: float, **extra: Any) -> dict[str, Any]:
    return {
        "id": task,
        "category": "fake",
        "repeat": repeat,
        "status": "pass" if passed else "fail",
        "checks": [{"type": "answer_matches", "ok": passed}],
        "task_sha": f"sha-{task}",
        "trace_ids": [trace_id],
        "latency_ms": latency,
    } | extra


def sized(call: dict[str, Any], i: int) -> dict[str, Any]:
    """The call for task i: prompts grow with the task, so tasks differ in cost."""
    return call | {"input": call["input"] + 20 * i, "output": call["output"] + i}


def teacher_file(db: TraceDB, repeats: int, agent: str) -> dict[str, Any]:
    rows = [
        row(t, k, t not in TEACHER_FAILS, db.trace([(sized(TEACHER_CALL, i), None, None)] * 2), 4000.0 + 10 * i)
        for i, t in enumerate(TASKS)
        for k in range(1, repeats + 1)
    ]
    meta = META | {"provider": "groq", "model": "openai/gpt-oss-120b", "repeat": repeats, "agent": {"sha": agent}}
    return {"meta": meta | {"FAKE": True}, "tasks": rows}


def routed_file(db: TraceDB, lose: int) -> dict[str, Any]:
    lost = frozenset(t for t in TASKS if t not in TEACHER_FAILS)
    lost = frozenset(sorted(lost)[:lose])
    rows, rng = [], random.Random(2026)
    for i, t in enumerate(TASKS):
        for k in range(1, 6):
            escalated = rng.random() < ESCALATE
            reason = rng.choice(REASONS)
            category = "quality"
            student = sized(STUDENT_CALL, i)
            calls: list[tuple[dict[str, Any], str | None, str | None]] = [(student, "student", None)]
            calls.append((sized(TEACHER_CALL, i), "teacher", reason) if escalated else (student, "student", None))
            routing = {
                "turns": 1,
                "escalated_turns": int(escalated),
                "reasons": {reason: 1} if escalated else {},
                "categories": {category: 1} if escalated else {},
                "steps_per_turn": [2],
                "over_max_steps": False,
            }
            passed = t not in TEACHER_FAILS and t not in lost
            rows.append(row(t, k, passed, db.trace(calls), 9000.0 + 25 * i, routing=routing))
    meta = META | {"provider": "routed", "model": "kestrel-student -> openai/gpt-oss-120b", "repeat": 5}
    return {"meta": meta | {"agent": {"sha": "fake-routed"}, "FAKE": True}, "tasks": rows}


def make_files(out: Path) -> dict[str, Path]:
    """Every fake input, written into `out` (which must be empty or not exist yet)."""
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise SystemExit(f"{out} isn't empty: give a new folder")
    paths: dict[str, Path] = {}

    def save(name: str, data: dict[str, Any]) -> None:
        paths[name] = out / f"{name}.json"
        paths[name].write_text(json.dumps(data, indent=1), encoding="utf-8")

    tdb = TraceDB(out / "teacher.db")
    save("teacher", teacher_file(tdb, 3, "fake-teacher"))
    save("ref-baseline", teacher_file(tdb, 1, "fake-older"))
    save("ref-run2", teacher_file(tdb, 1, "fake-older"))
    tdb.close()
    rdb = TraceDB(out / "routed.db")
    for name, lose in SCENARIOS.items():
        save(f"routed-{name}", routed_file(rdb, lose))
    rdb.close()
    paths["teacher_db"], paths["routed_db"] = out / "teacher.db", out / "routed.db"
    return paths


def bench(argv: list[str]) -> str:
    """`kestrel bench ARGV`, in-process, its output returned."""
    parser = argparse.ArgumentParser(prog="kestrel")
    cli.add_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["bench", *argv])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(args)
    return buf.getvalue()


def dry_run(out: Path) -> dict[str, dict[str, str]]:
    """Every scenario through both commands: {scenario: {"cost": markdown, "compare": text, "cost_shown": ...}}."""
    p = make_files(out)
    teacher, refs = str(p["teacher"]), [str(p["ref-baseline"]), str(p["ref-run2"])]
    done: dict[str, dict[str, str]] = {}
    for name in SCENARIOS:
        routed, cost_json = str(p[f"routed-{name}"]), out / f"cost-{name}.json"
        sides = ["--db", str(p["routed_db"]), "--teacher", teacher, "--teacher-db", str(p["teacher_db"])]
        cost_md = bench(["cost", routed, *sides, "--out", str(out / f"cost-{name}.md"), "--json", str(cost_json)])
        shown = json.loads(cost_json.read_text(encoding="utf-8"))["comparison"]["cost_shown"]
        flags = ["--metric", "checks", "--b-references", *refs, "--cost-shown", shown, "--safety", "held"]
        compare = bench(["compare", teacher, routed, *flags, "--drift", "none"])
        done[name] = {"cost": cost_md, "compare": compare, "cost_shown": shown}
    pending = bench(["compare", teacher, str(p["routed-pass"]), "--metric", "checks", "--b-references", *refs])
    done["pending"] = {"cost": "", "compare": pending, "cost_shown": ""}
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="a new, empty folder for the fake files")
    args = parser.parse_args(argv)
    done = dry_run(args.out)
    print(done["pass"]["cost"].split("## Per task")[0])
    for name, d in done.items():
        outcome = next(line for line in d["compare"].splitlines() if "Outcome (section 8.5)" in line)
        print(f"{name:9} --cost-shown {d['cost_shown'] or '(none)':6} {outcome.strip()}")
    print(f"\nFake files and the full reports: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
