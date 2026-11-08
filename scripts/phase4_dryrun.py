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

    uv run --no-sync python scripts/phase4_dryrun.py --kit --out <empty folder>

runs the results kit (scripts/phase4_result.py) end to end instead, one fake set per outcome (KIT_SCENARIOS):
PASS; NOT SHOWN by quality and by drift; FAIL by quality and by safety; and an unfinished routed run, which the
kit must refuse (exit 2, nothing written). Each set adds a fake drift reference and control (the CI subset), fake
safety files on the real group F / S task ids (the teacher's, routed group S, the student alone) and a fake
student-alone main-suite run for the chart.
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


# --- the results kit (scripts/phase4_result.py) -------------------------------------------------------------

# scenario: (tasks the routed run loses, drift in the control, an attack breaks group S's strict gate, unfinished)
KIT_SCENARIOS = {
    "pass": (0, False, False, False),
    "notshown-quality": (3, False, False, False),
    "notshown-drift": (0, True, False, False),
    "fail-quality": (25, False, False, False),
    "fail-safety": (0, False, True, False),
    "incomplete": (0, False, False, True),
}
KIT_EXPECTED = {
    "pass": "PASS",
    "notshown-quality": "NOT SHOWN",
    "notshown-drift": "NOT SHOWN",
    "fail-quality": "FAIL",
    "fail-safety": "FAIL",
    "incomplete": "INCOMPLETE",
}
CI_TASKS = [f"fake-ci-{i:02d}" for i in range(16)]


def load_kit() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("phase4_result", Path(__file__).with_name("phase4_result.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["phase4_result"] = module
    spec.loader.exec_module(module)
    return module


def ci_file(repeats: int, fails: int, agent: str) -> dict[str, Any]:
    """The CI subset, `fails` of its 16 tasks failing every repeat (the drift reference and control)."""
    rows = [
        row(t, k, i >= fails, f"ci-{agent}-{i}-{k}", 3000.0)
        for i, t in enumerate(CI_TASKS)
        for k in range(1, repeats + 1)
    ]
    meta = META | {"provider": "groq", "model": "openai/gpt-oss-120b", "subset": "ci", "repeat": repeats}
    return {"meta": meta | {"agent": {"sha": agent}, "FAKE": True}, "tasks": rows}


def safety_rows(
    ids: list[str], kinds: dict[str, str], probes: dict[str, str | None], user: str, repeats: int, broken: str | None
) -> list[dict]:
    rows = []
    for t in ids:
        for k in range(1, repeats + 1):
            attack = kinds[t] == "attack"
            hit = attack and t == broken
            rows.append(
                {
                    "id": t,
                    "category": "fake",
                    "repeat": k,
                    "user": user,
                    "status": "fail" if hit else "pass",
                    "safety": {
                        "kind": kinds[t],
                        "probe": probes[t],
                        "model_success": hit,
                        "system_success": hit,
                        "legit_ok": None,
                    },
                }
            )
    return rows


def safety_file(rows: list[dict], user: str, repeats: int, provider: str, group: str | None) -> dict[str, Any]:
    meta = {
        "split": "safety",
        "split_version": "s1.0",
        "provider": provider,
        "model": "fake",
        "user": user,
        "users": [user],
        "repeat": repeats,
        "kinds": "attack" if user == "rubber-stamp" else "all",
        "safety_group": group,
        "fetch_stub": group is None,
        "FAKE": True,
    }
    return {"meta": meta, "tasks": rows}


def student_main_file(db: TraceDB) -> dict[str, Any]:
    """The student alone on the main suite: it loses 10 tasks the teacher passes (for the chart)."""
    lost = frozenset(sorted(t for t in TASKS if t not in TEACHER_FAILS)[:10])
    rows = [
        row(
            t, k, t not in TEACHER_FAILS and t not in lost, db.trace([(sized(STUDENT_CALL, i), None, None)] * 2), 8000.0
        )
        for i, t in enumerate(TASKS)
        for k in range(1, 4)
    ]
    meta = META | {"provider": "ollama", "model": "kestrel-student", "repeat": 3, "agent": {"sha": "fake-student"}}
    return {"meta": meta | {"FAKE": True}, "tasks": rows}


def make_kit_files(out: Path, lose: int, drifted: bool, unsafe: bool, unfinished: bool) -> dict[str, Any]:
    """One scenario's inputs for scripts/phase4_result.py, as its keyword paths."""
    from kestrel.bench.routed_safety import F, S, groups
    from kestrel.bench.safety import SAFETY_DIR, load_safety

    p = make_files(out)
    split = load_safety(SAFETY_DIR)
    kinds = {st.id: st.meta.kind for st in split.tasks}
    probes = {st.id: st.meta.probe for st in split.tasks}
    g = groups(split)
    all_ids, s_ids = [*g[F], *g[S]], list(g[S])
    attacks = [i for i in all_ids if kinds[i] == "attack"]
    s_attacks = [i for i in s_ids if kinds[i] == "attack"]
    broken = next(i for i in s_attacks if not probes[i]) if unsafe else None

    def rows(ids: list[str], user: str, repeats: int, hit: str | None = None) -> list[dict]:
        return safety_rows(ids, kinds, probes, user, repeats, hit)

    def save(name: str, data: dict[str, Any]) -> Path:
        path = out / f"{name}.json"
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return path

    routed = load_json(p["routed-notshown"] if lose == 3 else p["routed-fail"] if lose == 25 else p["routed-pass"])
    if unfinished:  # the last repeat of 7 tasks not run yet (a day's budget ran out)
        late = set(TASKS[:7])
        routed["tasks"] = [r for r in routed["tasks"] if not (r["id"] in late and r["repeat"] == 5)]
    sdb = TraceDB(out / "student.db")
    student_main = save("student-main", student_main_file(sdb))
    sdb.close()
    return {
        "routed": save("routed", routed),
        "routed_db": p["routed_db"],
        "teacher": p["teacher"],
        "teacher_db": p["teacher_db"],
        "references": [p["ref-baseline"], p["ref-run2"]],
        "drift_reference": save("ci-reference", ci_file(3, 2, "fake-old")),
        "drift_control": save("ci-control", ci_file(2, 10 if drifted else 2, "fake-old")),
        "safety_s_strict": save("s-strict", safety_file(rows(s_ids, "strict", 3, broken), "strict", 3, "routed", S)),
        "safety_s_rubber": save(
            "s-rubber", safety_file(rows(s_attacks, "rubber-stamp", 1), "rubber-stamp", 1, "routed", S)
        ),
        "teacher_safety_strict": save("t-strict", safety_file(rows(all_ids, "strict", 3), "strict", 3, "groq", None)),
        "teacher_safety_rubber": save(
            "t-rubber", safety_file(rows(attacks, "rubber-stamp", 1), "rubber-stamp", 1, "groq", None)
        ),
        "student_safety_strict": save("a-strict", safety_file(rows(all_ids, "strict", 3), "strict", 3, "ollama", None)),
        "student_safety_rubber": save(
            "a-rubber", safety_file(rows(attacks, "rubber-stamp", 1), "rubber-stamp", 1, "ollama", None)
        ),
        "student_main": student_main,
        "student_db": out / "student.db",
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def kit_args(paths: dict[str, Any], report: Path) -> list[str]:
    argv = []
    for key, value in paths.items():
        if key == "references":
            argv += ["--references", *map(str, value)]
        else:
            argv += [f"--{key.replace('_', '-')}", str(value)]
    return [*argv, "--out", str(report)]


def kit_dry_run(out: Path) -> dict[str, tuple[int, str]]:
    """Every kit scenario: {scenario: (exit code, the kit's printed output)}; reports in out/<scenario>/."""
    kit = load_kit()
    done = {}
    for name, (lose, drifted, unsafe, unfinished) in KIT_SCENARIOS.items():
        folder = out / name
        paths = make_kit_files(folder / "inputs", lose, drifted, unsafe, unfinished)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = kit.main(kit_args(paths, folder / "phase4-result.md"))
        done[name] = (code, buf.getvalue())
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="a new, empty folder for the fake files")
    parser.add_argument("--kit", action="store_true", help="run scripts/phase4_result.py on one fake set per outcome")
    args = parser.parse_args(argv)
    if args.kit:
        for name, (code, text) in kit_dry_run(args.out).items():
            first = text.splitlines()[0] if text else ""
            print(f"{name:18} exit {code}  {first}  (expected {KIT_EXPECTED[name]})")
        print(f"\nFake files and the reports: {args.out}")
        return 0
    done = dry_run(args.out)
    print(done["pass"]["cost"].split("## Per task")[0])
    for name, d in done.items():
        outcome = next(line for line in d["compare"].splitlines() if "Outcome (section 8.5)" in line)
        print(f"{name:9} --cost-shown {d['cost_shown'] or '(none)':6} {outcome.strip()}")
    print(f"\nFake files and the full reports: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
