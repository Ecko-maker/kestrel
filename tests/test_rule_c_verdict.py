"""scripts/rule_c_verdict.py on small fake results files: the rule C table (docs/tiers-v2-design.md
section 5), A(3)'s card test, and the partial-file guard."""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from kestrel.bench.stats import compare, merge_references

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rule_c_verdict_script", ROOT / "scripts" / "rule_c_verdict.py")
assert spec is not None and spec.loader is not None
rc = importlib.util.module_from_spec(spec)
sys.modules["rule_c_verdict_script"] = rc
spec.loader.exec_module(rc)

TASKS = [f"t{i:02d}" for i in range(30)]


def results(statuses: dict[str, list[str]], agent: str | None = None, cards: dict[tuple[str, int], str] | None = None):
    rows = []
    for t, ss in statuses.items():
        for k, s in enumerate(ss, start=1):
            approvals = []
            if cards and (t, k) in cards:
                approvals = [{"tool": "send_message", "decision": cards[(t, k)], "escalated_by": ["taint:read_file"]}]
            rows.append(
                {
                    "id": t,
                    "category": "c",
                    "repeat": k,
                    "status": s,
                    "approvals": approvals,
                    "error": None,
                    "task_sha": f"sha-{t}",
                }
            )
    meta = {"split": "main", "suite_version": "1.1", "judge": "groq/m", "judge_version": "v2"}
    if agent:
        meta["agent"] = {"sha": agent}
    return {"meta": meta, "tasks": rows}


def write(tmp: Path, name: str, data: dict[str, Any]) -> Path:
    p = tmp / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.fixture
def refs(tmp_path: Path) -> tuple[Path, Path]:
    # every task passes in both references, except t00 fails in run 2
    base = results({t: ["pass"] for t in TASKS})
    run2 = results({t: ["fail" if t == "t00" else "pass"] for t in TASKS}, agent="cc5c16377662")
    return write(tmp_path, "ref1.json", base), write(tmp_path, "ref2.json", run2)


def after(tmp: Path, statuses: dict[str, list[str]], **kw: Any) -> Path:
    full = {t: ["pass"] * 3 for t in TASKS} | statuses
    return write(tmp, "after.json", results(full, agent=rc.AGENT, **kw))


def run(tmp: Path, refs: tuple[Path, Path], statuses: dict[str, list[str]], review=None, **kw: Any):
    return rc.verdict(after(tmp, statuses, **kw), review or {}, *refs)


def test_unchanged_agent_passes(tmp_path, refs):
    v = run(tmp_path, refs, {})
    assert v.partial == [] and v.worse == [] and v.primary == "pass" and v.secondary_pass
    assert v.outcome == "PASS"


def test_secondary_is_the_compare_commands_number(tmp_path, refs):
    """The same paired interval as `kestrel bench compare A B --second-reference A2`."""
    statuses = {"t01": ["fail", "pass", "pass"], "t02": ["fail", "fail", "fail"]}
    v = run(tmp_path, refs, statuses)
    a = merge_references(*(json.loads(p.read_text(encoding="utf-8")) for p in refs))
    c = compare(a, json.loads((tmp_path / "after.json").read_text(encoding="utf-8")))
    assert v.secondary == c.diff


def test_worse_flip_without_a_card_is_not_card_caused(tmp_path, refs):
    v = run(tmp_path, refs, {"t03": ["fail", "pass", "pass"]})
    assert [(f.task, f.after, f.cause) for f in v.worse] == [("t03", "2/3", "no card")]
    assert v.primary == "pass"


def test_rejected_escalated_card_is_a_candidate_until_reviewed(tmp_path, refs):
    statuses = {"t04": ["pass", "fail", "pass"]}
    cards = {("t04", 2): "rejected"}
    v = run(tmp_path, refs, statuses, cards=cards)
    assert v.candidates == ["t04"] and v.outcome.startswith("PENDING")
    assert v.worse[0].cards == ["run 2: send_message (taint:read_file)"]
    assert run(tmp_path, refs, statuses, {"t04": "caused"}, cards=cards).outcome == "FAIL"
    assert run(tmp_path, refs, statuses, {"t04": "not-caused"}, cards=cards).outcome == "PASS"


def test_an_approved_card_or_a_card_in_a_passing_run_is_not_a_candidate(tmp_path, refs):
    v = run(tmp_path, refs, {"t05": ["pass", "fail", "pass"]}, cards={("t05", 2): "approved", ("t05", 1): "rejected"})
    assert v.worse[0].cause == "no card"
    assert len(v.escalations) == 2  # both still listed


def test_review_of_a_task_that_isnt_a_candidate_is_refused(tmp_path, refs):
    with pytest.raises(SystemExit, match="aren't candidate"):
        run(tmp_path, refs, {}, {"t06": "caused"})


def test_large_drop_without_cards_is_gate_not_met(tmp_path, refs):
    v = run(tmp_path, refs, {t: ["fail"] * 3 for t in TASKS[:6]})
    assert v.primary == "pass" and not v.secondary_pass
    assert v.outcome.startswith("gate not met (a): 0 escalations; merge after #14")


DROP = {t: ["fail"] * 3 for t in TASKS[:6]}  # secondary fails: gate not met, unless the primary fails


def test_gate_not_met_branch_b_waits_for_review_of_each_escalated_task(tmp_path, refs):
    # t10 passes with an approved escalated card; t11 fails a run with one (not a worse-flip candidate:
    # its card was approved, so the primary stays pass)
    cards = {("t10", 1): "approved", ("t11", 2): "approved"}
    statuses = DROP | {"t11": ["pass", "fail", "pass"]}
    v = run(tmp_path, refs, statuses, cards=cards)
    assert v.primary == "pass" and not v.secondary_pass
    assert v.escalated_tasks == ["t10", "t11"]
    assert v.outcome == "gate not met (b): PENDING review of the escalated tasks (t10, t11)"
    partly = rc.verdict(tmp_path / "after.json", {}, *refs, {"t10": "unchanged"})
    assert partly.outcome.endswith("(t11)")


def test_gate_not_met_branch_b_card_changed_an_outcome_means_no_merge(tmp_path, refs):
    cards = {("t10", 1): "approved", ("t11", 2): "approved"}
    after(tmp_path, DROP | {"t11": ["pass", "fail", "pass"]}, cards=cards)
    v = rc.verdict(tmp_path / "after.json", {}, *refs, {"t10": "unchanged", "t11": "changed"})
    assert v.outcome == "gate not met (b): a card plausibly changed t11; no merge, fix and re-measure"


def test_gate_not_met_branch_b_no_card_changed_anything_is_as_a(tmp_path, refs):
    cards = {("t10", 1): "approved", ("t11", 2): "approved"}
    after(tmp_path, DROP | {"t11": ["pass", "fail", "pass"]}, cards=cards)
    v = rc.verdict(tmp_path / "after.json", {}, *refs, {"t10": "unchanged", "t11": "unchanged"})
    assert v.outcome.startswith("gate not met (b): no card changed an outcome; as (a), merge after #14")


def test_escalation_review_of_a_task_without_entries_is_refused(tmp_path, refs):
    with pytest.raises(SystemExit, match="no escalated_by entry"):
        rc.verdict(after(tmp_path, DROP), {}, *refs, {"t03": "unchanged"})


def test_report_counts_escalations_and_lists_them_for_review(tmp_path, refs):
    v = run(tmp_path, refs, DROP | {"t11": ["pass", "fail", "pass"]}, cards={("t11", 2): "approved"})
    text = rc.render(v)
    assert "| pass | fail | 1 in 1 tasks | **gate not met (b): PENDING" in text
    assert "| `t11` | 1 | approved x1 | 0/1 escalated runs | 100% | to review |" in text
    clean = rc.render(run(tmp_path, refs, DROP))
    assert "| 0 in 0 tasks |" in clean and "none: 0 entries, so branch (a) applies" in clean


def test_escalations_dont_change_a_pass(tmp_path, refs):
    assert run(tmp_path, refs, {}, cards={("t10", 1): "approved"}).outcome == "PASS"


def test_partial_file_is_labelled_and_never_written(tmp_path, refs, capsys):
    p = after(tmp_path, {"t07": ["pass", "skipped", "skipped"]})
    out = tmp_path / "report.md"
    with pytest.raises(SystemExit, match=rc.PARTIAL):
        rc.main([str(p), "--baseline", str(refs[0]), "--run2", str(refs[1]), "--out", str(out)])
    assert not out.exists()
    rc.main([str(p), "--baseline", str(refs[0]), "--run2", str(refs[1])])
    text = capsys.readouterr().out
    assert text.startswith(rc.PARTIAL) and text.rstrip().endswith(rc.PARTIAL)
    assert "| – | – | 0 in 0 tasks | **no verdict (partial file)** |" in text
    assert "| pass |" not in text and "| fail |" not in text  # no secondary result on a partial file


def test_a_lock_beside_the_file_means_partial(tmp_path, refs):
    p = after(tmp_path, {})
    p.with_name(p.name + ".lock").write_text("1", encoding="utf-8")
    assert "still live" in rc.verdict(p, {}, *refs).partial[0]


def test_baseline_that_doesnt_match_its_pin_is_refused(tmp_path, refs):
    fake = tmp_path / rc.BASELINE.name
    fake.write_text(refs[0].read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(SystemExit, match="pinned SHA-256"):
        rc.verdict(after(tmp_path, {}), {}, fake, refs[1])


def test_the_real_baselines_match_their_pins():
    assert rc.sha256(rc.BASELINE) == rc.PINNED[rc.BASELINE.name]
    assert rc.sha256(rc.RUN2) == rc.PINNED[rc.RUN2.name]


def test_template_and_report_share_their_sections(tmp_path, refs):
    def headings(text: str) -> list[str]:
        return [line for line in text.splitlines() if line.startswith("#")]

    filled = rc.render(run(tmp_path, refs, {}))
    template = rc.render(None)
    assert headings(filled) == headings(template)
    assert rc.TBD in template and rc.TBD not in filled


def test_committed_template_is_current():
    """evals/reports/rule-c-verdict.md stays the generated template until the run's real report replaces it."""
    text = (ROOT / "evals" / "reports" / "rule-c-verdict.md").read_text(encoding="utf-8")
    if "Status: **template**" in text:
        assert text == rc.render(None)
