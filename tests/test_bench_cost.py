"""`kestrel bench cost` (src/kestrel/bench/cost.py): list-price cost, Groq billable tokens and the teacher's
share of turns from a results file's traces, and section 8.2's paired cost rule; then the whole Phase 4 report
path on fake files (scripts/phase4_dryrun.py). Synthetic files only; no model calls."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from kestrel.bench import cost
from kestrel.bench.stats import FAIL, NOT_SHOWN, PASS, PENDING
from kestrel.pricing import load_prices

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("phase4_dryrun_script", ROOT / "scripts" / "phase4_dryrun.py")
assert spec is not None and spec.loader is not None
dryrun = importlib.util.module_from_spec(spec)
sys.modules["phase4_dryrun_script"] = dryrun
spec.loader.exec_module(dryrun)

PRICES = load_prices(ROOT / "src" / "kestrel" / "prices.toml")
GROQ = {"provider": "groq", "model": "openai/gpt-oss-120b", "input": 1_000_000, "output": 100_000, "cached": 400_000}
OLLAMA = {"provider": "ollama", "model": "kestrel-student", "input": 5_000, "output": 500, "cached": 0}


def results(rows: list[dict[str, Any]], **meta: Any) -> dict[str, Any]:
    return {"meta": {"provider": "groq", "model": "openai/gpt-oss-120b", **meta}, "tasks": rows}


def run_row(task: str, trace_id: str | None, status: str = "pass", **extra: Any) -> dict[str, Any]:
    ids = [trace_id] if trace_id else []
    return {"id": task, "repeat": 1, "status": status, "trace_ids": ids, "latency_ms": 1000} | extra


def test_list_price_billable_tokens_and_routes_come_from_the_spans(tmp_path: Path) -> None:
    db = dryrun.TraceDB(tmp_path / "t.db")
    routed_trace = db.trace([(OLLAMA, "student", None), (GROQ, "teacher", "invalid_tool_call")])
    db.close()
    rep = cost.build(results([run_row("a", routed_trace)]), tmp_path / "t.db", PRICES)
    (run,) = rep.runs
    assert run.list_usd == pytest.approx(1.0 * 0.15 + 0.1 * 0.60)  # groq list price; ollama $0
    assert run.billable == 1_000_000 + 100_000 - 400_000  # groq only, cached input left out
    assert run.calls == {"student": 1, "teacher": 1}
    assert rep.problems() == []


def test_rows_that_did_not_run_are_not_costed_and_missing_traces_are_a_problem(tmp_path: Path) -> None:
    db = dryrun.TraceDB(tmp_path / "t.db")
    ok = db.trace([(GROQ, None, None)])
    db.close()
    rows = [run_row("a", ok), run_row("b", "nope"), run_row("c", None, status="skipped")]
    rep = cost.build(results(rows), tmp_path / "t.db", PRICES)
    assert [r.id for r in rep.runs] == ["a", "b"] and rep.not_run == 1
    assert any("1 trace(s) named in the results are not in the database" in p for p in rep.problems())


def test_a_non_local_call_without_a_list_price_is_unpriced(tmp_path: Path) -> None:
    db = dryrun.TraceDB(tmp_path / "t.db")
    t = db.trace([(GROQ | {"provider": "gemini", "model": "unlisted"}, None, None)])
    db.close()
    rep = cost.build(results([run_row("a", t)]), tmp_path / "t.db", PRICES)
    assert rep.runs[0].list_usd == 0 and any("gemini/unlisted" in p for p in rep.problems())


def test_the_database_is_only_read(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    db = dryrun.TraceDB(db_path)
    t = db.trace([(GROQ, None, None)])
    db.close()
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    cost.build(results([run_row("a", t)]), db_path, PRICES)
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before
    with pytest.raises(FileNotFoundError):
        cost.build(results([]), tmp_path / "missing.db", PRICES)


def pair(tmp_path: Path, teacher_calls: list[dict], routed_calls: list[dict], tasks: int = 20) -> cost.CostComparison:
    db = dryrun.TraceDB(tmp_path / "pair.db")
    t_rows = [run_row(f"t{i}", db.trace([(c, None, None) for c in teacher_calls])) for i in range(tasks)]
    r_rows = [run_row(f"t{i}", db.trace([(c, None, None) for c in routed_calls])) for i in range(tasks)]
    db.close()
    teacher = cost.build(results(t_rows), tmp_path / "pair.db", PRICES)
    routed = cost.build(results(r_rows), tmp_path / "pair.db", PRICES)
    return cost.compare_cost(teacher, routed)


def test_cost_is_shown_only_when_the_upper_bound_is_below_zero(tmp_path: Path) -> None:
    cheaper = pair(tmp_path, [GROQ, GROQ], [OLLAMA, GROQ])
    assert cheaper.cost_shown == cost.SHOWN and cheaper.diff is not None and cheaper.diff.high < 0
    (tmp_path / "pair.db").unlink()
    dearer = pair(tmp_path, [GROQ], [GROQ, GROQ])
    assert dearer.cost_shown == cost.NOT_SHOWN


def test_any_problem_leaves_the_cost_rule_undecided(tmp_path: Path) -> None:
    other = GROQ | {"provider": "gemini", "model": "gemini-3.6-flash"}
    cmp = pair(tmp_path, [GROQ, GROQ], [OLLAMA, other])  # cheaper, but not the reference's teacher
    assert cmp.cost_shown == cost.UNDECIDED and any("not the teacher reference's" in p for p in cmp.problems)


# --- the whole report path on fake files (distill/EVAL.md, step 6) --------------------------------


@pytest.fixture(scope="module")
def out(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("dryrun")


@pytest.fixture(scope="module")
def done(out: Path) -> dict[str, dict[str, str]]:
    return dryrun.dry_run(out)


@pytest.mark.parametrize(("scenario", "outcome"), [("pass", PASS), ("notshown", NOT_SHOWN), ("fail", FAIL)])
def test_dry_run_gives_each_outcome_with_the_cost_from_the_cost_report(
    done: dict[str, dict[str, str]], scenario: str, outcome: str
) -> None:
    d = done[scenario]
    assert d["cost_shown"] == cost.SHOWN
    assert f"Outcome (section 8.5): {outcome}\n" in d["compare"]
    assert "(b) against A + 2 more reference(s)" in d["compare"]
    assert "**Cost lower than the teacher's: SHOWN (upper bound < 0).**" in d["cost"]
    assert "`--cost-shown yes`" in d["cost"]


def test_dry_run_without_the_other_flags_is_pending(done: dict[str, dict[str, str]]) -> None:
    assert (
        f"Outcome (section 8.5): {PENDING} (still needed: --cost-shown, --safety, --drift)"
        in done["pending"]["compare"]
    )


def test_dry_run_cost_table_renders(done: dict[str, dict[str, str]]) -> None:
    md = done["pass"]["cost"]
    for line in ("| Turns the teacher answered |", "| Escalations by reason |", "| Latency p50 / p95 per task run |"):
        assert line in md
    table = md.split("## Per task")[1].strip().splitlines()
    assert table[0].endswith("| Teacher reference, $ per 1,000 runs |")
    assert len(table) == 2 + len(dryrun.TASKS)  # header, separator, one row per task
    assert "**Problem:**" not in md


def test_dry_run_json_feeds_compare(out: Path, done: dict[str, dict[str, str]]) -> None:
    data = json.loads((out / "cost-pass.json").read_text(encoding="utf-8"))
    assert data["comparison"]["cost_shown"] == cost.SHOWN and data["comparison"]["problems"] == []
    assert data["routed"]["routing"]["by_category"] == {"quality": data["routed"]["routing"]["escalated_turns"]}
    assert 0 < data["routed"]["teacher_turn_share"]["value"] < 0.5
