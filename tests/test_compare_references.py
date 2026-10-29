"""compare with two reference files (rule C, docs/tiers-v2-design.md section 5): the references count as
per-task repeats, exactly as scripts/main_suite_power.py assumes."""

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from kestrel.bench import cli
from kestrel.bench.stats import bootstrap_ci, compare, merge_references, per_task_pass

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "evals" / "baselines" / "baseline-v1.1-judge-v2-final.json"
RUN2 = ROOT / "evals" / "baselines" / "run2-v1.1-judge-v2-final.json"

spec = importlib.util.spec_from_file_location("main_suite_power_script", ROOT / "scripts" / "main_suite_power.py")
assert spec is not None and spec.loader is not None
power = importlib.util.module_from_spec(spec)
sys.modules["main_suite_power_script"] = power
spec.loader.exec_module(power)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_file(statuses: dict[str, list[str]], **meta: Any) -> dict[str, Any]:
    rows = [{"id": t, "repeat": k + 1, "status": s} for t, ss in statuses.items() for k, s in enumerate(ss)]
    return {"meta": {"suite_version": "1.1", "judge": "groq/m", "judge_version": "v2", **meta}, "tasks": rows}


def test_on_the_real_references_it_is_the_power_reports_reference():
    """The merged baseline + run 2 give each task exactly the reference the power simulation used."""
    merged = per_task_pass(merge_references(load(BASELINE), load(RUN2))["tasks"])
    assert merged == power.per_task_fraction([BASELINE, RUN2])
    assert len(merged) == 100 and sorted(set(merged.values())) == [0.0, 0.5, 1.0]


def test_the_statistic_is_the_power_reports(tmp_path):
    """compare(merged, after) gives the same paired interval as the power report's statistic: per-task pass
    fraction of the after-run minus the references' per-task mean, percentile bootstrap."""
    after = run_file({t: ["pass", "fail", "pass"] for t in power.per_task_fraction([BASELINE])})
    c = compare(merge_references(load(BASELINE), load(RUN2)), after)
    ref = power.per_task_fraction([BASELINE, RUN2])
    expected = bootstrap_ci([2 / 3 - ref[t] for t in sorted(ref)])
    assert c.diff == expected


def test_a_task_graded_in_one_reference_only_uses_that_one():
    merged = merge_references(run_file({"t1": ["pass"], "t2": ["pass"]}), run_file({"t1": ["fail"], "t2": ["error"]}))
    assert per_task_pass(merged["tasks"]) == {"t1": 0.5, "t2": 1.0}
    assert sorted(r["repeat"] for r in merged["tasks"] if r["id"] == "t1") == [1, 2]


def test_repeats_inside_a_reference_are_kept_apart():
    merged = merge_references(run_file({"t": ["pass", "fail"]}), run_file({"t": ["pass"]}))
    assert sorted(r["repeat"] for r in merged["tasks"]) == [1, 2, 3]
    assert per_task_pass(merged["tasks"]) == {"t": pytest.approx(2 / 3)}


@pytest.mark.parametrize(
    ("second", "match"),
    [
        ({"suite_version": "1.2"}, "suite version"),
        ({"judge": "groq/other"}, "judge"),
        ({"split": "safety"}, "split"),
        ({"agent": {"sha": "bbb"}}, "different agents"),
    ],
)
def test_references_that_are_not_repeats_of_one_run_are_refused(second, match):
    with pytest.raises(ValueError, match=match):
        merge_references(run_file({"t": ["pass"]}, agent={"sha": "aaa"}), run_file({"t": ["pass"]}, **second))


def test_the_cli_prints_both_references(tmp_path, capsys):
    after = tmp_path / "after.json"
    after.write_text(json.dumps(run_file({t: ["pass"] for t in power.per_task_fraction([BASELINE])})))
    args = argparse.Namespace(a=BASELINE, b=after, margin=0.06, second_reference=RUN2)
    assert cli.cmd_compare(args) == 0
    out = capsys.readouterr().out
    assert "2 references, per-task mean of their runs" in out and "B - A:" in out
