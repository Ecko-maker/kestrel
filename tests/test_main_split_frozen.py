"""The main KestrelBench v1.1 suite must not move while other splits are added: same 100 task ids,
same fingerprints (task definition + fixture workspace), same CI subset and CI gate. The frozen
values come from commit 5b1d900, where Phase 3 started. If this fails, a change touched the main
suite: undo it, or make it a deliberate new suite version (evals/CHANGELOG.md) and refreeze."""

import json
import re
from pathlib import Path

from kestrel.bench.tasks import SUITE_VERSION, load_tasks, select, task_sha

ROOT = Path(__file__).resolve().parents[1]
FROZEN = json.loads((ROOT / "tests" / "frozen" / "main_split_v1.1.json").read_text(encoding="utf-8"))


def test_main_suite_ids_and_fingerprints_are_frozen():
    tasks = load_tasks()
    assert FROZEN["suite_version"] == SUITE_VERSION
    assert {t.id: task_sha(t) for t in tasks} == FROZEN["tasks"]


def test_ci_subset_is_frozen():
    assert [t.id for t in select(load_tasks(), "ci")] == FROZEN["ci_subset"]


def test_ci_gate_is_unchanged():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"bench run --subset ci .*\n\s*--min-pass-rate 0\.75 --max-errors 2", ci)
