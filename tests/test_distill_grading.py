"""Phase 4 step 3: which distill passes count (grading.toml, task fingerprints), the near-duplicate
filter on chats, and the scripts and rejudge path for distill results. Synthetic databases only; no
model calls; nothing reads logs/ (rejudge runs in a tmp working directory).
"""

import argparse
import json
import runpy
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from test_distill_dataset import TraceDB, convo  # tests/ is on sys.path under pytest

from kestrel.bench import cli as bench_cli
from kestrel.bench.tasks import TASKS_DIR, load_tasks, task_sha
from kestrel.distill import DISTILL_DB, DISTILL_DIR, DISTILL_VERSION, ROOT, overlap
from kestrel.distill import dataset as ds
from kestrel.tracing import SCHEMA

TEACHERS = frozenset({("groq", "openai/gpt-oss-120b")})
DISTILL_TASKS = {t.id: t for t in load_tasks(DISTILL_DIR / "tasks")}
CHECKS_ONLY = "dst-arith-member-discount"
RUBRIC = "dst-time-ravi-hours"  # still graded by a rubric in d1.2
ROOT_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def sha(task_id: str) -> str:
    return task_sha(DISTILL_TASKS[task_id], DISTILL_DIR / "workspace")


def result(task_id: str, trace_id: str, *, status: str = "pass", judge: dict | None = None, **extra: Any) -> dict:
    return {
        "id": task_id,
        "status": status,
        "checks": [{"ok": status == "pass"}],
        "judge": judge,
        "trace_ids": [trace_id],
        "task_sha": sha(task_id) if task_id in DISTILL_TASKS else "x",
        "tool_log": ["- read_file(...) -> ok"],
        "answer": "done",
        "tool_calls": [],
    } | extra


def distill_file(path: Path, tasks: list[dict], **meta: Any) -> Path:
    base = {
        "split": "distill",
        "suite_version": DISTILL_VERSION,
        "task_dir": "distill/tasks",
        "workspace_dir": "distill/workspace",
    }
    path.write_text(json.dumps({"meta": base | meta, "tasks": tasks}), encoding="utf-8")
    return path


def kept_and_dropped(db: TraceDB, files: list[Path], grading: ds.Grading = ds.DEFAULT_GRADING) -> tuple[set[str], dict]:
    built = ds.build(db.traces(), ds.load_verdicts(files), [], TEACHERS, [], grading=grading)
    return {e.record["metadata"]["trace_id"] for e in built.train + built.val}, {
        k: v for k, v in built.dropped.items() if v
    }


@pytest.fixture
def db(tmp_path: Path) -> TraceDB:
    return TraceDB(tmp_path / "copy.db")


def test_the_task_split_under_checks_only() -> None:
    """What the README states: how many distill tasks are graded by checks alone, per category."""
    checks_only = [t for t in DISTILL_TASKS.values() if t.rubric is None]
    assert len(DISTILL_TASKS) == 122 and len(checks_only) == 93
    per = {c: sum(t.category == c for t in checks_only) for c in sorted({t.category for t in DISTILL_TASKS.values()})}
    assert per == {
        "actions": 16,
        "adapt": 8,
        "arithmetic": 12,
        "conversation": 7,
        "files": 13,
        "multistep": 14,
        "no_tools": 5,
        "safety": 10,
        "time": 6,
        "web": 2,
    }
    assert per["time"] >= 6 and per["safety"] >= 6  # the step-4 targets


# --- Quality path -------------------------------------------------------------


def test_a_distill_pass_is_kept(tmp_path: Path, db: TraceDB) -> None:
    t = db.add(convo("q1", "a1"), rating=None)
    assert kept_and_dropped(db, [distill_file(tmp_path / "r.json", [result(CHECKS_ONLY, t)])]) == ({t}, {})


def test_a_distill_fail_is_dropped(tmp_path: Path, db: TraceDB) -> None:
    t = db.add(convo("q1", "a1"), rating=None)
    files = [distill_file(tmp_path / "r.json", [result(CHECKS_ONLY, t, status="fail")])]
    assert kept_and_dropped(db, files) == (set(), {"quality": 1})


def test_bad_always_drops_even_a_distill_pass(tmp_path: Path, db: TraceDB) -> None:
    t = db.add(convo("q1", "a1"), rating="bad")
    assert kept_and_dropped(db, [distill_file(tmp_path / "r.json", [result(CHECKS_ONLY, t)])]) == (
        set(),
        {"quality": 1},
    )


def test_one_non_distill_id_drops_the_whole_file(tmp_path: Path, db: TraceDB) -> None:
    a, b = db.add(convo("q1", "a1"), rating=None), db.add(convo("q2", "a2"), rating=None)
    files = [distill_file(tmp_path / "r.json", [result(CHECKS_ONLY, a), result("arith-percent", b)])]
    assert kept_and_dropped(db, files) == (set(), {"contamination_run": 2})


# --- Grading policy -----------------------------------------------------------


def test_rubric_tasks_are_dropped_under_checks_only(tmp_path: Path, db: TraceDB) -> None:
    unjudged = db.add(convo("q1", "a1"), rating=None)  # --judge none: "pass" on its checks alone
    judged = db.add(convo("q2", "a2"), rating=None)
    tasks = [result(RUBRIC, unjudged), result(RUBRIC, judged, judge={"score": 1.0, "judge": "groq/x", "version": "v2"})]
    assert kept_and_dropped(db, [distill_file(tmp_path / "r.json", tasks)]) == (set(), {"rubric_task": 2})


def test_calibrated_judges_unlock_rubric_tasks(tmp_path: Path, db: TraceDB) -> None:
    listed, other, none = (db.add(convo(f"q{i}", f"a{i}"), rating=None) for i in range(3))
    tasks = [
        result(RUBRIC, listed, judge={"score": 0.9, "judge": "groq/qwen/qwen3.8-27b", "version": "v2"}),
        result(RUBRIC, other, judge={"score": 0.9, "judge": "groq/openai/gpt-oss-120b", "version": "v2"}),
        result(RUBRIC, none),
    ]
    grading = ds.Grading("calibrated-judge", frozenset({"groq/qwen/qwen3.8-27b@v2"}))
    assert kept_and_dropped(db, [distill_file(tmp_path / "r.json", tasks)], grading) == ({listed}, {"rubric_task": 2})


def test_changed_or_unknown_tasks_are_dropped(tmp_path: Path, db: TraceDB) -> None:
    stale, unknown, nodir = (db.add(convo(f"q{i}", f"a{i}"), rating=None) for i in range(3))
    files = [
        distill_file(tmp_path / "a.json", [result(CHECKS_ONLY, stale, task_sha="0" * 12), result("dst-gone", unknown)]),
        distill_file(tmp_path / "b.json", [result(CHECKS_ONLY, nodir)], task_dir=None),
    ]
    assert kept_and_dropped(db, files) == (set(), {"task_changed": 3})


def test_grading_config() -> None:
    assert ds.load_grading() == ds.Grading("checks-only", frozenset())
    assert ds.load_grading() == ds.DEFAULT_GRADING


def test_an_unknown_grading_mode_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "g.toml"
    path.write_text('grading = "trust-me"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="checks-only"):
        ds.load_grading(path)


# --- Near-duplicates in chats -------------------------------------------------


def test_a_reworded_bench_prompt_rated_good_is_dropped(db: TraceDB) -> None:
    bench = "We had dinner for $187.40 and split it evenly between 4 people. How much does each person pay?"
    db.add(convo("We had lunch for $92 and split it evenly between 3 friends. What does each pay?", "30.67"))
    built = ds.build(db.traces(), {}, [bench], TEACHERS, [])
    assert {k: v for k, v in built.dropped.items() if v} == {"near_duplicate": 1}


def test_ordinary_chat_against_the_real_bench(db: TraceDB) -> None:
    """The numbers the overlap report gives for everyday requests rated /good."""
    outcomes = dict(overlap.chat_probe(overlap.load_bench()))
    assert outcomes == {
        "What time is it in Tokyo?": "contamination_prompt",
        "Add milk to my groceries note.": "kept",
        "What's 15% of 80?": "kept",
        "Email Sam that I'm running late.": "kept",
        "Summarize notes.txt for me.": "contamination_prompt",
        "What's the weather like in Paris today?": "kept",
        "Search the web for the latest Python version.": "near_duplicate",
        "Create a note called 'Groceries' with milk and eggs.": "near_duplicate",
        "What's on my todo list?": "kept",
        "Translate 'good night' into French.": "kept",
        "How many days until Christmas?": "kept",
        "Read my meeting notes and list the action items.": "kept",
    }


def test_the_synthetic_examples_are_not_near_duplicates(db: TraceDB) -> None:
    """The seven examples the step-1 end-to-end test keeps are all kept against the real bench."""
    for i in range(6):
        db.add(convo(f"chat {i}", f"reply {i}"), session=f"s{i // 2}")
    db.add(convo("blocked", "ok"), approvals=("rejected",))
    built = ds.build(db.traces(), {}, [p.text for p in overlap.load_bench().prompts], TEACHERS, [])
    assert len(built.train + built.val) == 7 and not built.dropped


# --- rejudge, check_results and audit for distill runs ------------------------


def test_rejudge_sources() -> None:
    assert bench_cli._rejudge_sources({}) == (TASKS_DIR, bench_cli.BENCH_DB)
    assert bench_cli._rejudge_sources({"split": "main"}) == (TASKS_DIR, bench_cli.BENCH_DB)
    meta = {"split": "distill", "task_dir": "distill/tasks"}
    assert bench_cli._rejudge_sources(meta) == (ROOT / "distill" / "tasks", DISTILL_DB)


def test_rejudge_dry_run_finds_the_stored_rubric_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # no logs/ here, so no trace database is opened
    path = distill_file(tmp_path / "r.json", [result(RUBRIC, "t1"), result(CHECKS_ONLY, "t2")], judge=None)
    parser = argparse.ArgumentParser()
    bench_cli.add_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["bench", "rejudge", str(path), "--dry-run"])
    assert bench_cli.main(args) == 0
    assert "Re-grade 1 stored answers" in capsys.readouterr().out
    assert not (tmp_path / "logs").exists()


def _run_script(name: str, argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "argv", [name, *argv])
    try:
        runpy.run_path(str(ROOT_SCRIPTS / name), run_name="__main__")
    except SystemExit as e:
        return int(e.code or 0)
    return 0


def test_check_results_knows_distill_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tasks = [result(t, f"tr-{t}") for t in DISTILL_TASKS]
    good = distill_file(tmp_path / "good.json", tasks, judge=None, judge_version=None)
    assert _run_script("check_results.py", [str(good)], monkeypatch) == 0
    short = distill_file(tmp_path / "short.json", tasks[:-1], judge=None, judge_version=None)
    assert _run_script("check_results.py", [str(short)], monkeypatch) == 1
    assert "missing task" in capsys.readouterr().out


def test_audit_reads_the_given_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "copy.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
    path = distill_file(tmp_path / "r.json", [result(CHECKS_ONLY, "missing-trace")])
    monkeypatch.chdir(tmp_path)  # the default logs/bench.db doesn't exist here
    assert _run_script("audit_rate_limits.py", [str(path), "--db", str(db)], monkeypatch) == 0
    assert "trace_missing" in capsys.readouterr().out
