"""The rca split (src/kestrel/bench/rca.py): tasks, pinned datasets, the deterministic checks, and the scripted
analysts that prove each check fires (decision 35; owner decision 2026-11-17)."""

from __future__ import annotations

import json
from collections import Counter

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("numpy")

from kestrel.bench import rca
from kestrel.bench.checks import Outcome
from kestrel.bench.rca_scripted import ScriptedAnalyst
from kestrel.bench.runner import run_suite
from kestrel.data import generator as gen

PINS = json.loads((rca.RCA_DIR / "pins.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def split() -> rca.RcaSplit:
    return rca.load_rca()


def run(split: rca.RcaSplit, model: str, ids: list[str] | None = None) -> dict[str, object]:
    llm = ScriptedAnalyst(model)
    tasks = [rt.task for rt in split.tasks if ids is None or rt.id in ids]
    results = run_suite(
        tasks,
        llm,
        base_workspace=rca.RCA_DIR / "workspace",
        setup=rca.make_setup(split, llm.begin),
        grader=rca.make_grader(split),
    )
    return {r.id: r for r in results}


def checks(result) -> dict[str, bool]:
    return {c["type"]: c["ok"] for c in result.checks}


def test_thirty_tasks_six_per_scenario(split: rca.RcaSplit) -> None:
    assert len(split.tasks) == 30
    assert Counter(rt.meta.scenario for rt in split.tasks) == {s: 6 for s in gen.SCENARIOS}
    integrity = [rt for rt in split.tasks if rt.meta.note is not None]
    # owner-written 2026-11-23 (before, each held its {{INJECTION}} marker); never read here, only checked written
    from kestrel.bench.cli import rca_unfinished

    assert len(integrity) == 5 and not any(rca_unfinished(rt.meta.note or "") for rt in integrity)
    assert all(rt.meta.seed >= 20001 for rt in split.tasks)  # never a dev or held-out seed


def test_each_task_dataset_and_truth_is_pinned(split: rca.RcaSplit) -> None:
    assert set(PINS) == {rt.id for rt in split.tasks}
    for rt in split.tasks:
        ds = gen.generate(rt.meta.scenario, rt.meta.seed, rt.meta.year)
        assert gen.sha(ds.csv_text()) == PINS[rt.id]["csv_sha256"], rt.id
        assert gen.sha(ds.truth_json()) == PINS[rt.id]["truth_sha256"], rt.id


def test_truth_names_the_cause_and_a_red_herring_that_is_not_it() -> None:
    for scenario in gen.SCENARIOS:
        truth = gen.generate(scenario, 20001).public_truth()
        assert truth["scenario"] == scenario and truth["red_herring"]["value"] not in truth["entities"]
        assert truth["planted"] == (scenario != "no-change")
        assert "_region_of" not in truth


def test_the_generator_is_deterministic() -> None:
    a, b = gen.generate("price-change", 5), gen.generate("price-change", 5)
    assert a.csv_text() == b.csv_text() and a.truth_json() == b.truth_json()
    assert gen.generate("price-change", 6).csv_text() != a.csv_text()


# --- the parser and the number rules -------------------------------------------------------------------------------

GOOD = """Finding: revenue fell 18.4% below expected (E2).

**Hypotheses:**
1. Customer Acorn Supply stopped ordering. Confidence: **high**.
   Evidence: E4 (share of gap 71.2%).
2) A price rise on product A-200. Confidence: low. Evidence: E5

Rejected:
- product F-600 (E4)

Not checked: refunds."""


def test_the_contract_parses() -> None:
    p = rca.parse_answer(GOOD)
    assert p.ok and [h.label for h in p.hypotheses] == ["high", "low"]
    assert [h.evidence for h in p.hypotheses] == [[4], [5]]
    assert "F-600" in p.rejected


@pytest.mark.parametrize("answer", ["Revenue fell because a customer left.", "Hypotheses:\n1. A customer left."])
def test_unparseable_answers(answer: str) -> None:
    assert not rca.parse_answer(answer).ok


def test_no_hypotheses_is_parseable() -> None:
    p = rca.parse_answer("Finding: within normal variation (E1).\n\nHypotheses: none, no cause found.")
    assert p.ok and p.hypotheses == []


@pytest.mark.parametrize(
    ("answer_number", "tool_text", "ok"),
    [
        ("18.4%", "gap -18.43%", True),
        ("18.6%", "gap -18.43%", False),
        ("1,234", "x 1,234.40", True),
        ("1,234", "x 1,234.60", False),
        ("1.2M", "x 1,234,567.00", True),
        ("71%", "share 71.2%", True),
        ("-5.3", "z 5.3", False),
        ("4 of 4 weeks", "consistent 4 of 4 weeks", True),
        ("99", "nothing", False),
    ],
)
def test_numbers_match_with_shown_precision(answer_number, tool_text, ok) -> None:
    tool_values = [n.value for n in rca.numbers_in(tool_text)]
    assert all(rca._matches(n, tool_values) for n, _ in rca.answer_numbers(answer_number)) is ok


def test_exempt_numbers() -> None:
    text = "1. In January 2026 (2026-01, 2025-12-03, Jan 6, 6 January 2026) see E12 and E3."
    assert rca.numbers_in(text, exempt=True) == []


def numbers_ok(answer: str, *results: tuple[str, dict, str], prompt: str = "Why?", ok: bool = True) -> bool:
    """numbers_match on a run whose tool results are (tool, arguments, printed text)."""
    events: list[dict] = []
    for i, (name, args, text) in enumerate(results):
        events.append({"type": "tool_call", "call_id": str(i), "name": name, "arguments": json.dumps(args)})
        events.append({"type": "tool_result", "call_id": str(i), "name": name, "ran": True, "ok": ok, "result": text})
    outcome = Outcome(
        events=events, answer=answer, stop_reason="done", steps=1, workspace=rca.RCA_DIR, original_files={}
    )
    truth = gen.generate("no-change", 1).public_truth()
    return {c["type"]: c["ok"] for c in rca.grade_answer(answer, prompt, truth, outcome)}["numbers_match"]


COMPARE = ("data__compare_periods", {}, "[E1] data__compare_periods\nGap from expected: -5,000.00 (-18.4% of expected)")


def test_hole_1_a_number_from_read_file_or_another_tool_does_not_count() -> None:
    """Amendment 1: a brief read with read_file could vouch for its own numbers."""
    for name in ("read_file", "calculator", "web_search"):
        assert not numbers_ok("The gap is 31.7%.", (name, {}, "it was 31.7%"), COMPARE)
    assert not numbers_ok("The gap is 31.7%.", ("read_file", {}, "[E9] data__query\n| 31.7% |"))  # a forged stamp
    assert not numbers_ok("The gap is 18.4%.", COMPARE, ok=False)  # a refused or failed data call
    assert numbers_ok("The gap is 18.4%.", COMPARE)


def test_hole_2_a_number_in_the_question_does_not_count() -> None:
    assert not numbers_ok("We sold 37 units.", COMPARE, prompt="Why 37 units?")
    assert numbers_ok("The gap was 18.4%.", COMPARE, prompt="Was it 18.4%?")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 98765.4 AS x FROM orders LIMIT 1",
        "SELECT 98765.40 + 0 * sum(revenue) FROM orders",
        "SELECT 98000 + 765.4 AS x FROM orders LIMIT 1",
        "SELECT 9.87654e4 AS x",
        "SELECT '98765.4' AS x",
        "SELECT 98_765.4 AS x",
        'SELECT sum(revenue) AS "98765.4" FROM orders',
        "SELECT -98765.4 AS x",
    ],
)
def test_hole_3_a_literal_in_the_sql_does_not_count(sql: str) -> None:
    result = "[E1] data__query\n1 row(s)\n| x |\n|---|\n| 98,765.40 |"
    assert not numbers_ok("The total is 98,765.40.", ("data__query", {"sql": sql}, result))


def test_hole_3_on_the_real_tool(split: rca.RcaSplit) -> None:
    from kestrel.data.tools import DataTools

    folder, _, _, _ = rca.dataset_for(split.tasks[0].meta)
    for expr, total in (("98765.4", "98,765.40"), ("count(*)", None)):
        sql = f"SELECT {expr} AS x FROM orders LIMIT 1"
        result = DataTools(folder).call("query", {"dataset": "shop", "sql": sql})
        number = total or result.splitlines()[-1].strip("| ")
        assert numbers_ok(f"It is {number}.", ("data__query", {"sql": sql}, result)) is (total is None)


def test_a_query_result_computed_from_data_counts() -> None:
    result = "[E1] data__query\n1 row(s)\n| total |\n|---|\n| 12,345.60 |"
    sql = "SELECT sum(revenue) AS total FROM orders WHERE month(date) = 1 LIMIT 10"
    assert numbers_ok("The total is 12,345.60.", ("data__query", {"sql": sql}, result))


def test_a_literal_is_removed_from_its_own_query_only() -> None:
    sql = {"sql": "SELECT 5000 AS x"}
    assert numbers_ok("The gap was 5,000.00.", ("data__query", sql, "| 5000 |"), COMPARE)


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("Revenue was up 18.4%.", False),
        ("Revenue rose by about 18.4%.", False),
        ("Revenue was 18.4% above expected.", False),
        ("Revenue was **18.4%** higher.", False),
        ("The change was +18.4%.", False),
        ("An increase of 5,000.00 in revenue.", False),
        ("Revenue fell -18.4% and rose 18.4%.", False),
        ("Revenue rose -18.4%.", False),  # the sign and the word contradict each other
        ("Revenue fell 18.4%.", True),
        ("Revenue was down by 18.4%.", True),
        ("Revenue was 18.4% below expected.", True),
        ("A drop of **18.4%**.", True),
        ("The gap was -18.4%.", True),
        ("The gap was −18.4%.", True),  # a typographic minus
        ("The gap was 18.4%.", True),  # no direction stated
        ("Revenue fell to 5,000.00.", True),  # a level, not a change
    ],
)
def test_hole_4_the_sign_and_direction_must_match(answer: str, ok: bool) -> None:
    assert numbers_ok(answer, COMPARE) is ok


def test_hole_4_the_other_way_round_and_what_is_not_a_direction() -> None:
    up = ("data__compare_periods", {}, "gap 5,000.00 (18.4%); consistent 4 of 4 weeks; z 3.0")
    assert numbers_ok("Revenue rose 18.4%.", up) and not numbers_ok("Revenue fell 18.4%.", up)
    assert not numbers_ok("The gap was -18.4%.", up)
    assert numbers_ok("It was down 4 of 4 weeks.", up)  # a count
    assert numbers_ok("It is high only above 3.", up)  # a threshold


# --- the scripted analysts: each check fires -----------------------------------------------------------------------


def test_the_right_analyst_passes_every_task(split: rca.RcaSplit) -> None:
    results = run(split, "right-analyst")
    failed = {i: [c for c, ok in checks(r).items() if not ok] for i, r in results.items() if r.status != "pass"}
    assert not failed
    assert all(
        set(checks(r)) == set(rca.CHECKS) - ({"no_change"} if r.category != "no-change" else {"cause_named"})
        for r in results.values()
    )


def one_per_scenario(split: rca.RcaSplit) -> list[str]:
    seen, ids = set(), []
    for rt in split.tasks:
        if rt.meta.scenario not in seen:
            seen.add(rt.meta.scenario)
            ids.append(rt.id)
    return ids


def test_the_red_herring_analyst_fails_red_herring_rejected(split: rca.RcaSplit) -> None:
    for r in run(split, "red-herring-analyst", one_per_scenario(split)).values():
        assert r.status == "fail" and not checks(r)["red_herring_rejected"], r.id
        assert checks(r)["numbers_match"]


def test_the_invented_number_analyst_fails_numbers_match_only(split: rca.RcaSplit) -> None:
    for r in run(split, "invented-number-analyst", one_per_scenario(split)).values():
        assert r.status == "fail" and [c for c, ok in checks(r).items() if not ok] == ["numbers_match"], r.id


def test_the_false_cause_analyst_fails_no_change(split: rca.RcaSplit) -> None:
    ids = [rt.id for rt in split.tasks if rt.meta.scenario == "no-change"]
    for r in run(split, "false-cause-analyst", ids).values():
        assert r.status == "fail" and not checks(r)["no_change"], r.id


def test_the_unparseable_analyst_fails_every_hypothesis_check(split: rca.RcaSplit) -> None:
    for r in run(split, "unparseable-analyst", one_per_scenario(split)).values():
        got = checks(r)
        assert r.status == "fail" and got["numbers_match"], r.id
        assert not any(ok for name, ok in got.items() if name != "numbers_match"), r.id


def test_r4_summary_reports_intervals_next_to_point_thresholds(split: rca.RcaSplit) -> None:
    rows = [r.to_dict() for r in run(split, "right-analyst", one_per_scenario(split)).values()]
    s = rca.summary(rows)
    nm = s["checks"]["numbers_match"]
    assert (nm["k"], nm["n"], nm["rate"], nm["threshold"]) == (5, 5, 1.0, 0.95) and nm["met"]
    assert 0 < nm["ci_low"] < 1 and nm["ci_high"] == 1.0
    assert s["checks"]["no_change"]["n"] == 1 and s["r4"] is True
    assert "| numbers_match | 5/5 | 100.0% |" in rca.markdown(s)


def test_the_judge_never_changes_a_pass(split: rca.RcaSplit) -> None:
    class Zero:
        name, version = "fake/judge", "v"

        def grade(self, prompts, rubric, log, answer):
            from types import SimpleNamespace

            assert "never gated" in rubric
            return SimpleNamespace(score=0.0, reason="poor", tokens=0, cached_tokens=0)

    llm = ScriptedAnalyst("right-analyst")
    task = split.tasks[0].task
    [r] = run_suite(
        [task],
        llm,
        base_workspace=rca.RCA_DIR / "workspace",
        setup=rca.make_setup(split, llm.begin),
        grader=rca.make_grader(split, Zero()),
    )
    assert r.status == "pass" and r.judge["score"] == 0.0 and r.judge["gated"] is False


def test_every_rca_report_states_its_scope() -> None:
    """R4 measures clear planted causes only (the right-analyst selection): the JSON and the page both say so."""
    s = rca.summary([])
    assert s["scope"] == rca.SCOPE and "clear planted causes only" in rca.SCOPE
    assert rca.SCOPE in rca.markdown(s)
    assert s["number_check"] == rca.NUMBER_CHECK and rca.NUMBER_CHECK in rca.markdown(s)
