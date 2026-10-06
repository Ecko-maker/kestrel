"""scripts/ci/evals_needed.py decides whether CI spends Groq quota on the KestrelBench subset."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "evals_needed.py"
_spec = importlib.util.spec_from_file_location("evals_needed", SCRIPT)
assert _spec and _spec.loader
evals_needed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evals_needed)

PR = {"event": "pull_request", "ref": "refs/pull/11/merge", "labeled": False, "author": "Ecko-maker"}
MAIN = {"event": "push", "ref": "refs/heads/main", "labeled": False, "author": ""}


@pytest.mark.parametrize(
    "changed",
    [
        ["docs/known-issues.md", "CLAUDE.md", "README.md"],
        ["uv.lock", "pyproject.toml"],  # dependency-only: the manual full run covers it
        ["evals/kestrelbench/safety/tasks/x.yaml"],  # not in the main suite
        ["console/src/App.tsx", "tests/test_agent.py", ".github/workflows/ci.yml"],
        [],
    ],
)
def test_changes_that_dont_affect_the_agent_skip_evals(changed):
    assert evals_needed.decide(changed, **PR) == (False, "no agent-affecting changes")
    assert evals_needed.decide(changed, **MAIN)[0] is False


@pytest.mark.parametrize(
    "path",
    [
        "src/kestrel/agent.py",
        "src/kestrel/bench/checks.py",
        "evals/kestrelbench/tasks/a.yaml",
        "evals/kestrelbench/workspace/b.txt",
    ],
)
def test_agent_affecting_changes_need_evals_on_prs_and_main(path):
    for event in (PR, MAIN):
        needed, reason = evals_needed.decide(["docs/x.md", path], **event)
        assert needed and any(path.startswith(p) and p in reason for p in evals_needed.AGENT_PATHS)


def test_label_forces_evals_even_for_docs_and_dependabot():
    assert evals_needed.decide(["docs/x.md"], **{**PR, "labeled": True})[0] is True
    assert evals_needed.decide(["uv.lock"], **{**PR, "labeled": True, "author": "dependabot[bot]"})[0] is True


def test_dependabot_prs_skip_evals_even_when_touching_src():
    needed, reason = evals_needed.decide(["src/kestrel/llm.py"], **{**PR, "author": "dependabot[bot]"})
    assert not needed and "Dependabot" in reason


def test_branch_pushes_leave_evals_to_the_pull_request():
    branch = {"event": "push", "ref": "refs/heads/x", "labeled": False, "author": ""}
    assert evals_needed.decide(["src/kestrel/agent.py"], **branch)[0] is False


def test_unknown_changes_on_main_run_evals_to_be_safe():
    assert evals_needed.decide(None, **MAIN)[0] is True


def test_cli_writes_output_and_summary(tmp_path):
    changed = tmp_path / "changed.txt"
    changed.write_text("docs/known-issues.md\n", encoding="utf-8")
    out, summary = tmp_path / "out", tmp_path / "summary"
    env = {**os.environ, "EVENT": "pull_request", "LABELED": "false", "AUTHOR": "Ecko-maker"}
    env |= {"GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)}
    subprocess.run([sys.executable, str(SCRIPT), "--changed", str(changed)], env=env, check=True)
    assert out.read_text(encoding="utf-8") == "needed=false\n"
    assert summary.read_text(encoding="utf-8") == "KestrelBench not run: no agent-affecting changes\n"


def test_ci_job_has_no_job_level_if_and_fails_loudly_without_a_key():
    ci = (SCRIPT.parents[2] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci.split("\n  kestrelbench:\n", 1)[1]
    assert "\n    if:" not in job  # a required check must never be left skipped or pending
    assert "scripts/ci/evals_needed.py" in job and "exit 1" in job
