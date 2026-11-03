"""scripts/phase4_result.py, the Phase 4 results kit, end to end on fake files (scripts/phase4_dryrun.py --kit):
one fake set per section 8.5 outcome, and the refusals. No model calls, no Groq."""

import hashlib
import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dryrun = load("phase4_dryrun")
kit = dryrun.load_kit()


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, tuple[int, str]]]:
    out = tmp_path_factory.mktemp("kit")
    return out, dryrun.kit_dry_run(out)


@pytest.mark.parametrize("scenario", [s for s in dryrun.KIT_SCENARIOS if s != "incomplete"])
def test_each_outcome_is_written_as_pre_registered(runs: tuple[Path, dict], scenario: str) -> None:
    out, done = runs
    code, printed = done[scenario]
    expected = dryrun.KIT_EXPECTED[scenario]
    assert code == 0, printed
    report = (out / scenario / "phase4-result.md").read_text(encoding="utf-8")
    assert report.startswith(f"# Phase 4 result: {expected} — FAKE")
    assert "| **Outcome** | PASS only if all hold" in report and f"| **{expected}** |" in report
    assert f"Outcome (section 8.5): {expected}" in report  # the cross-check: bench compare's own line
    assert "Secondary metric (full rule, judge `gpt-oss-120b` v2): **not run**" in report
    for heading in ("## Verdict (section 8.5)", "## Safety", "## Cost", "## Cost vs quality", "## Inputs"):
        assert heading in report
    assert "**Group F (19 tasks), the teacher by the deterministic pre-route rule**" in report
    assert "| Teacher's share of turns | 100% |" in report and "| `max_steps` |" in report
    chart = ET.parse(out / scenario / kit.CHART_NAME).getroot()  # well-formed SVG
    labels = [t.text for t in chart.iter("{http://www.w3.org/2000/svg}text") if t.text]
    for system in ("Teacher alone", "Routed", "Student alone"):
        assert any(label.startswith(system) for label in labels), labels


def test_which_condition_decides_each_outcome(runs: tuple[Path, dict]) -> None:
    out, _ = runs

    def row(scenario: str, condition: str) -> str:
        text = (out / scenario / "phase4-result.md").read_text(encoding="utf-8")
        return next(line for line in text.splitlines() if line.startswith(f"| {condition}"))

    assert row("notshown-quality", "Quality (a)").endswith("**NOT SHOWN** |")
    assert row("notshown-drift", "Quality (a)").endswith("**PASS** |")
    assert row("notshown-drift", "Drift guard").endswith("**drift** |")
    assert row("fail-quality", "Quality (a)").endswith("**FAIL** |")
    assert row("fail-safety", "Quality (a)").endswith("**PASS** |")
    assert row("fail-safety", "Safety").endswith("**failed** |")
    assert row("pass", "Cost").endswith("**yes** |")


def test_an_unfinished_routed_run_gets_no_verdict(runs: tuple[Path, dict]) -> None:
    out, done = runs
    code, printed = done["incomplete"]
    assert code == 2
    assert "INCOMPLETE: no verdict written" in printed and "7 task(s) have fewer than 5 runs done" in printed
    assert not (out / "incomplete" / "phase4-result.md").exists()
    assert not (out / "incomplete" / kit.CHART_NAME).exists()


def test_missing_real_files_are_listed_and_nothing_is_written(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    report = tmp_path / "phase4-result.md"
    assert kit.main(["--runs", str(tmp_path / "no-runs-folder"), "--out", str(report)]) == 2
    printed = capsys.readouterr().out
    for key in (
        "routed: no file",
        "drift_control: no file",
        "safety_s_strict: no file",
        "teacher_safety_strict: no file",
    ):
        assert key in printed, printed
    assert not report.exists()


def test_real_and_fake_inputs_are_never_mixed(tmp_path: Path) -> None:
    paths = dryrun.make_kit_files(tmp_path / "inputs", 0, False, False, False)
    paths["student_safety_strict"] = ROOT / "evals" / "after-runs" / "student-safety-strict-x3.json"  # a real file
    inp = kit.read_inputs(paths)
    assert any("real and FAKE inputs are mixed" in m for m in inp.missing)


def test_the_routed_agent_may_differ_from_the_teachers_only_by_the_router_block() -> None:
    teacher_agent = {"sha": "a", "provider": "groq", "model": "m", "system_prompt": "p", "request_timeout_s": 60.0}
    routed_agent = teacher_agent | {"sha": "b", "provider": "routed", "router": {"student": "ollama"}}
    teacher = {"meta": {"suite_version": "1.1", "agent": teacher_agent}, "tasks": [{"id": "t", "status": "pass"}]}
    rows = [{"id": "t", "status": "pass"}] * 5
    meta = {"provider": "routed", "split": "main", "suite_version": "1.1", "repeat": 5}
    ok = kit.Inputs(
        {}, {"teacher": teacher, "routed": {"meta": meta | {"agent": routed_agent}, "tasks": rows}}, False, []
    )
    assert kit.check_routed(ok) == []
    slower = routed_agent | {"request_timeout_s": 300.0}  # 8.7 (11-03): the timeout stays at 60 s
    bad = kit.Inputs({}, {"teacher": teacher, "routed": {"meta": meta | {"agent": slower}, "tasks": rows}}, False, [])
    assert kit.check_routed(bad) == [
        "routed: the agent differs from the teacher's beyond the router block: request_timeout_s"
    ]


def test_the_pinned_references_are_the_files_in_git() -> None:
    files = {
        "teacher": ROOT / "evals" / "after-runs" / "main-after-v21-x3.json",
        "baseline": ROOT / "evals" / "baselines" / "baseline-v1.1-judge-v2-final.json",
        "run2": ROOT / "evals" / "baselines" / "run2-v1.1-judge-v2-final.json",
        "drift_reference": ROOT / "evals" / "reports" / "ci-repeat3-2026-10-07.json",
        "student_safety_strict": ROOT / "evals" / "after-runs" / "student-safety-strict-x3.json",
        "student_safety_rubber": ROOT / "evals" / "after-runs" / "student-safety-rubber-attacks.json",
    }
    for key, path in files.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == kit.PINNED[key], key


def test_nice_axis_steps() -> None:
    assert [kit.nice_step(x) for x in (0.27, 0.6, 1.3, 0.004, 0)] == [0.5, 1.0, 2.0, 0.005, 0.25]
