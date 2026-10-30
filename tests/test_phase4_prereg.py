"""Section 8 of docs/phase4-plan.md (the Phase 4 pre-registration, frozen 2026-10-30) in `kestrel bench
compare --metric checks`: the check-pass metric, D = routed - 0.95 x teacher, the paired bootstrap bound,
the 0.90 context bound and section 8.5's outcome. Synthetic results files only; no model calls."""

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from kestrel.bench import cli
from kestrel.bench.stats import (
    FAIL,
    NOT_SHOWN,
    PASS,
    PENDING,
    ROUTER_STOP,
    phase4_gate,
    preregistered_quality,
    run_passes,
)

TASKS = [f"t{i:02d}" for i in range(40)]
OK = [{"type": "answer_matches", "ok": True}]
BAD = [{"type": "answer_matches", "ok": False}]


def row(task: str, repeat: int, passed: bool, **extra: Any) -> dict[str, Any]:
    return {
        "id": task,
        "repeat": repeat,
        "status": "pass" if passed else "fail",
        "checks": OK if passed else BAD,
    } | extra


def results(outcomes: dict[str, list[bool]], **meta: Any) -> dict[str, Any]:
    rows = [row(t, k + 1, p) for t, ps in outcomes.items() for k, p in enumerate(ps)]
    return {"meta": {"suite_version": "1.1", "judge": "groq/m", "judge_version": "v2", **meta}, "tasks": rows}


def teacher(fail: tuple[str, ...] = ()) -> dict[str, Any]:
    return results({t: [t not in fail] * 3 for t in TASKS}, agent={"sha": "teacher"})


def routed(lose: int = 0, repeats: int = 5) -> dict[str, Any]:
    """The routed side: passes everything except the first `lose` tasks, which it always fails."""
    return results({t: [i >= lose] * repeats for i, t in enumerate(TASKS)}, agent={"sha": "routed"})


# --- the three outcomes ---------------------------------------------------------------------------


def test_pass_when_the_whole_interval_is_above_zero() -> None:
    res = preregistered_quality(teacher(), routed())
    assert res.outcome == PASS and res.problems == []
    assert res.d is not None and res.d.value == pytest.approx(0.05) and res.d.low > 0
    assert res.context is not None and res.context.value == pytest.approx(0.10)


def test_fail_when_the_whole_interval_is_below_zero() -> None:
    res = preregistered_quality(teacher(), routed(lose=20))
    assert res.d is not None and res.d.high < 0
    assert res.outcome == FAIL


def test_not_shown_when_the_interval_contains_zero() -> None:
    # 2 of 40 tasks lost for good: mean(D) = (38 x 0.05 - 2 x 0.95) / 40 = 0, the interval straddles it
    res = preregistered_quality(teacher(), routed(lose=2))
    assert res.d is not None and res.d.value == pytest.approx(0.0) and res.d.low < 0 < res.d.high
    assert res.outcome == NOT_SHOWN


# --- the metric -----------------------------------------------------------------------------------


def test_checks_ignore_the_judge_and_a_pass_past_max_steps_is_a_fail() -> None:
    judged_out = {"status": "fail", "checks": OK}  # every check ok, the judge said no
    extended = {"status": "pass", "checks": OK, "routing": {"over_max_steps": True}}
    assert run_passes(judged_out, "checks") and not run_passes(judged_out, "pass")
    assert not run_passes(extended, "checks") and not run_passes(extended, "pass")


def test_routed_errors_count_as_fails() -> None:
    r = routed()
    r["tasks"][0] = {"id": TASKS[0], "repeat": 1, "status": "error", "checks": []}
    res = preregistered_quality(teacher(), r)
    assert res.d is not None and res.d.value == pytest.approx(0.05 - 0.2 / 40)


def test_passes_past_max_steps_are_removed_before_the_bound() -> None:
    r = routed()
    for x in r["tasks"]:
        if x["id"] in TASKS[:2]:
            x["routing"] = {"over_max_steps": True}
    assert preregistered_quality(teacher(), r).outcome == NOT_SHOWN  # = routed(lose=2)


# --- the design -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("make_routed", "problem"),
    [
        (lambda: routed(repeats=3), "exactly 5 routed runs"),
        (lambda: results({t: [True] * 5 for t in TASKS[1:]}), "no routed run"),
    ],
)
def test_anything_but_the_preregistered_design_never_passes(make_routed, problem: str) -> None:
    res = preregistered_quality(teacher(), make_routed())
    assert res.outcome == NOT_SHOWN and any(problem in p for p in res.problems)


def test_a_student_health_stop_never_passes() -> None:
    r = routed()
    r["tasks"].append({"id": "t99", "repeat": 1, "status": "skipped", "error": f"{ROUTER_STOP}: 3 of 20 turns"})
    res = preregistered_quality(teacher(), r)
    assert res.outcome == NOT_SHOWN and any("student-health" in p for p in res.problems)


def test_the_teacher_side_needs_three_runs_except_for_check_b() -> None:
    four = results({t: [True] * 4 for t in TASKS})
    assert preregistered_quality(four, routed()).outcome == NOT_SHOWN
    assert preregistered_quality(four, routed(), teacher_repeats=None).outcome == PASS


# --- section 8.5's outcome ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((PASS, PASS, True, True, True), PASS),
        ((FAIL, PASS, True, True, True), FAIL),
        ((PASS, PASS, True, False, True), FAIL),  # a safety failure overrides everything
        ((NOT_SHOWN, NOT_SHOWN, True, True, True), NOT_SHOWN),
        ((PASS, NOT_SHOWN, True, True, True), NOT_SHOWN),  # (a) and (b) disagree
        ((PASS, PASS, False, True, True), NOT_SHOWN),  # quality shown, cost not
        ((PASS, PASS, True, True, False), NOT_SHOWN),  # the drift guard shows drift
        ((PASS, None, True, True, True), PENDING),
        ((PASS, PASS, True, None, True), PENDING),
        ((NOT_SHOWN, None, None, None, None), PENDING),  # safety unknown: it could still be a FAIL
        ((FAIL, None, None, None, None), FAIL),
    ],
)
def test_the_gate_follows_section_8_5(args: tuple, expected: str) -> None:
    a, b, cost, safety, drift = args
    assert phase4_gate(a, b, cost_shown=cost, safety_held=safety, drift_ok=drift) == expected


# --- the command ----------------------------------------------------------------------------------


def write(tmp_path: Path, name: str, data: dict[str, Any]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def compare_args(a: Path, b: Path, **kw: Any) -> argparse.Namespace:
    base = {"margin": 0.05, "second_reference": None, "metric": "checks", "b_references": None}
    return argparse.Namespace(a=a, b=b, **(base | {"cost_shown": None, "safety": None, "drift": None} | kw))


def test_the_command_prints_both_metrics_and_waits_for_the_other_conditions(tmp_path: Path, capsys) -> None:
    a, b = write(tmp_path, "teacher.json", teacher()), write(tmp_path, "routed.json", routed())
    assert cli.cmd_compare(compare_args(a, b)) == 0
    out = capsys.readouterr().out
    assert "primary, deterministic checks: mean(routed - 0.95 x teacher) +0.0500" in out and "-> PASS" in out
    assert "secondary, full rule" in out and "at 0.90 for context" in out
    assert "Outcome (section 8.5): PENDING (still needed: (b) --b-references, --cost-shown, --safety, --drift)" in out


def test_the_command_gives_each_final_outcome(tmp_path: Path, capsys) -> None:
    a = write(tmp_path, "teacher.json", teacher())
    base1 = write(tmp_path, "base1.json", results({t: [True] for t in TASKS}, agent={"sha": "older"}))
    base2 = write(tmp_path, "base2.json", results({t: [True] for t in TASKS}))
    done = {"b_references": [base1, base2], "cost_shown": "yes", "safety": "held", "drift": "none"}
    for lose, expected in ((0, PASS), (2, NOT_SHOWN), (20, FAIL)):
        b = write(tmp_path, f"routed-{lose}.json", routed(lose=lose))
        assert cli.cmd_compare(compare_args(a, b, **done)) == 0
        out = capsys.readouterr().out
        assert "(b) against A + 2 more reference(s)" in out
        assert f"Outcome (section 8.5): {expected}\n" in out


def test_check_b_merges_references_of_another_agent_but_rule_c_still_refuses(tmp_path: Path) -> None:
    from kestrel.bench.stats import merge_references

    older = results({t: [True] for t in TASKS}, agent={"sha": "older"})
    with pytest.raises(ValueError, match="different agents"):
        merge_references(teacher(), older)
    assert len(merge_references(teacher(), older, other_agents=True)["tasks"]) == 4 * len(TASKS)
