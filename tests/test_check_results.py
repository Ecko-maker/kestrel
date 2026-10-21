"""scripts/check_results.py on safety split files (the main-suite path is unchanged)."""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from kestrel.bench.safety import SAFETY_VERSION, load_safety

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_results_script", ROOT / "scripts" / "check_results.py")
assert spec is not None and spec.loader is not None
check_results = importlib.util.module_from_spec(spec)
sys.modules["check_results_script"] = check_results
spec.loader.exec_module(check_results)

SPLIT = {st.id: st.meta.kind for st in load_safety().tasks}


def safety_file(tmp_path: Path, kinds: str = "all", repeat: int = 2, users: tuple[str, ...] = ("strict",)) -> Path:
    rows = [
        {"id": tid, "repeat": k, "user": u, "status": "pass", "task_sha": f"sha-{tid}", "safety": {"kind": kind}}
        for u in users
        for k in range(1, repeat + 1)
        for tid, kind in SPLIT.items()
        if kinds in ("all", kind)
    ]
    meta = {
        "split": "safety",
        "split_version": SAFETY_VERSION,
        "suite_version": "1.1",
        "judge": None,
        "judge_version": None,
        "user": users[0] if len(users) == 1 else "both",
        "users": list(users),
        "kinds": kinds,
        "repeat": repeat,
        "subset": "all",
    }
    path = tmp_path / "safety.json"
    path.write_text(json.dumps({"meta": meta, "tasks": rows}), encoding="utf-8")
    return path


def edit(path: Path, change: Any) -> Path:
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_complete_safety_file_passes(tmp_path: Path) -> None:
    assert check_results.check(safety_file(tmp_path), "1.1", "v2") == []
    rubber = safety_file(tmp_path, kinds="attack", repeat=1, users=("rubber-stamp",))
    assert check_results.check(rubber, "1.1", "v2") == []


def test_missing_and_duplicate_runs_are_found(tmp_path: Path) -> None:
    def drop_and_repeat(d: dict[str, Any]) -> None:
        d["tasks"].pop()  # the last task of repeat 2
        d["tasks"].append(dict(d["tasks"][0]))

    problems = check_results.check(edit(safety_file(tmp_path), drop_and_repeat), "1.1", "v2")
    first = next(iter(SPLIT))
    assert f"{first} (repeat 1, user strict) appears 2 times" in problems
    assert sum(p.startswith("missing ") and "(repeat 2, user strict)" in p for p in problems) == 1


def test_a_filtered_run_skips_coverage_but_not_the_rest(tmp_path: Path) -> None:
    def keep_three(d: dict[str, Any]) -> None:
        d["tasks"] = d["tasks"][:3]

    path = edit(safety_file(tmp_path, repeat=1), keep_three)
    assert any(p.startswith("missing ") for p in check_results.check(path, "1.1", "v2"))  # --task isn't in meta
    assert check_results.check(path, "1.1", "v2", partial=True) == []
    edit(path, lambda d: d["meta"].update(tasks_filter="atk-*"))
    assert check_results.check(path, "1.1", "v2") == []


def test_mixed_settings_within_a_file_are_problems(tmp_path: Path) -> None:
    def mix(d: dict[str, Any]) -> None:
        d["tasks"][0]["task_sha"] = "other"  # another task definition, same id
        d["tasks"][1]["user"] = "rubber-stamp"  # a user the meta doesn't name
        d["tasks"][2]["repeat"] = 3  # beyond meta.repeat
        d["tasks"][3]["status"] = "graded"
        d["tasks"][4]["judge"] = {"version": "v2"}
        d["meta"]["split_version"] = "s0.9"

    problems = check_results.check(edit(safety_file(tmp_path), mix), "1.1", "v2")
    joined = "\n".join(problems)
    for expected in (
        "2 task fingerprints in one file",
        "user not in meta.users",
        "repeat outside 1..2",
        "unknown status 'graded'",
        "has a judge verdict",
        "meta.split_version is 's0.9'",
    ):
        assert expected in joined, expected


def test_an_errored_row_keeps_its_own_fingerprint_without_a_problem(tmp_path: Path) -> None:
    """Known issue #43: an error never reaches the safety grader, so its task_sha is the main suite's kind."""

    def error_row(d: dict[str, Any]) -> None:
        d["tasks"][0].update(status="error", task_sha="main-kind-sha")

    assert check_results.check(edit(safety_file(tmp_path), error_row), "1.1", "v2") == []


def test_a_kind_that_contradicts_the_task_is_a_problem(tmp_path: Path) -> None:
    benign = next(tid for tid, kind in SPLIT.items() if kind == "benign")

    def relabel(d: dict[str, Any]) -> None:
        next(r for r in d["tasks"] if r["id"] == benign)["safety"]["kind"] = "attack"

    problems = check_results.check(edit(safety_file(tmp_path, repeat=1), relabel), "1.1", "v2")
    assert problems == [f"{benign} (repeat 1, user strict): safety kind 'attack', the task is 'benign'"]
