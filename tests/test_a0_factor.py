"""scripts/a0_factor.py: the schema factor f, on synthetic results with known answers (no network)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("a0_factor", ROOT / "scripts" / "a0_factor.py")
assert spec is not None and spec.loader is not None
a0f = importlib.util.module_from_spec(spec)
sys.modules["a0_factor"] = a0f
spec.loader.exec_module(a0f)


def row(tid: str, tokens: int, *, judge: int = 0, cached: int = 0, status: str = "pass") -> dict:
    return {"id": tid, "status": status, "tokens": tokens, "judge_tokens": judge, "cached_tokens": cached}


def run(*rows: dict, sha: str = "8a8174d8f17d") -> dict:
    return {"meta": {"agent": {"sha": sha}, "git": "x"}, "tasks": list(rows)}


A0 = run(
    row("a", 3000, judge=200, cached=1000),  # billable 2,200; agent only 2,000
    row("b", 1000, judge=100),  # 1,100; 1,000
    row("c", 4000, judge=500, status="fail"),  # 4,500; 4,000
    row("skipped-one", 0, status="skipped"),  # not graded: not paired
    sha="b76a43765a4f",
)
PR = run(row("a", 1000, judge=200), row("b", 600, judge=100), row("c", 2000, judge=500), row("skipped-one", 900))
MERGE = run(row("a", 1400, judge=200, cached=400), row("b", 400, judge=100), row("c", 2000, judge=500))


def test_f_is_the_ratio_of_sums_against_the_per_task_mean() -> None:
    f = a0f.factor(A0, [PR, MERGE])
    # default means: a (1,200 + 1,200) / 2 = 1,200; b 600; c 2,500 -> 4,300. A0: 2,200 + 1,100 + 4,500 = 7,800.
    assert (f.a0, f.default) == (7800, 4300)
    assert f.f == pytest.approx(7800 / 4300)
    assert f.per_task == pytest.approx({"a": 2200 / 1200, "b": 1100 / 600, "c": 4500 / 2500})
    assert f.spread == pytest.approx((1.8, 2200 / 1200, 2200 / 1200))


def test_agent_only_leaves_the_judge_out() -> None:
    f = a0f.factor(A0, [PR, MERGE], judge=False)
    # a (1,000 + 1,000) / 2 = 1,000; b 500; c 2,000 -> 3,500. A0: 2,000 + 1,000 + 4,000 = 7,000.
    assert f.f == pytest.approx(7000 / 3500)


def test_a_failed_task_is_paired_and_a_skipped_one_is_not() -> None:
    assert set(a0f.factor(A0, [PR]).per_task) == {"a", "b", "c"}


def test_a_task_missing_from_a_default_run_stops() -> None:
    with pytest.raises(SystemExit, match="not in every default run"):
        a0f.factor(A0, [run(row("a", 1), row("b", 1))])


def test_the_cli_prints_the_three_pairings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = {}
    for name, data in {"a0": A0, "pr": PR, "merge": MERGE, "first": PR}.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(data), encoding="utf-8")
    argv = [str(paths["a0"]), "--primary", str(paths["pr"]), str(paths["merge"]), "--extra", str(paths["first"])]
    assert a0f.main(argv) == 0
    out = capsys.readouterr().out
    assert "primary (mean of --primary): f 1.814 (7,800 vs 4,300 billable); agent only 2.000" in out
    assert "all (mean of every run)" in out
    assert out.count("alone: ") == 3
    assert "pass on the 3: A0 2/3" in out
    assert "answer" not in out
