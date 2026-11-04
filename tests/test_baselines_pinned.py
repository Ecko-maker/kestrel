"""The pinned "before" baselines (evals/baselines/) are what Phase 3 is compared against, and the
pinned after-runs (evals/after-runs/) are the inputs of its verdict reports, and the pinned controls
(evals/controls/) are the inputs of drift checks (known issue #58). They must never change:
if this fails, restore the file from git instead of updating the hash."""

import hashlib
import json
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parents[1] / "evals"
PINNED = {
    "baselines": {
        "baseline-v1.1-judge-v2-final.json": "bfd6bdeb6c3ce6b456bf184a5d99ed9eea45dbedbc25df164f5aaa65a5bc8ba3",
        "run2-v1.1-judge-v2-final.json": "29a607859aee5595cf1ce1d7768e6e7caa601598c7d75447e9bca59b0d03afa4",
    },
    "after-runs": {
        "main-after-v21-x3.json": "8948b434fdcf77d0f5deab051dfb40b96fe564a066695fd4930fa81f57dfbcbb",
        # the student-alone safety runs (docs/phase4-plan.md 8.7), reported alongside section 8
        "student-safety-strict-x3.json": "61f35e664ae5087840125f5bbe09c16ef7eb12744456831be07e7ef208a54cc1",
        "student-safety-rubber-attacks.json": "de1a273faff2cf439e2835f8a7ee47e5a8a1becd553819697c439818b698d8d4",
        # the student-alone main suite (distill/EVAL.md section 5's extra), context only, not the claim
        "student-main-x1.json": "781efe2c5fba544417de3ea275465a37ff0fc0a150ced06ad156083af618957b",
    },
    "controls": {
        "control-old-ci-2026-10-29.json": "0c48e60decdfb3a368ba2736fec5bf810f1656a72d2b694d57018b9dec3d2b8c",
        # the same-week drift control of docs/phase4-plan.md 8.3 (#58's reading pre-registered 2026-11-01)
        "control-old-ci-2026-11-03.json": "5a587c8c37b3d921b1843959dfd502eb1c625671d0b83367c60f9530fbf83d20",
    },
}


@pytest.mark.parametrize(("folder", "name"), sorted((f, n) for f, files in PINNED.items() for n in files))
def test_pinned_file_is_unchanged(folder, name):
    assert hashlib.sha256((EVALS / folder / name).read_bytes()).hexdigest() == PINNED[folder][name]


@pytest.mark.parametrize(
    ("name", "user", "kinds", "rows"),
    [
        ("student-safety-strict-x3.json", "strict", "all", 144),
        ("student-safety-rubber-attacks.json", "rubber-stamp", "attack", 30),
    ],
)
def test_the_student_alone_pins_are_what_8_7_planned(name, user, kinds, rows):
    """The served student alone on local Ollama, safety split s1.0, no judge, the v2.1 answer-URL policy,
    every run graded."""
    data = json.loads((EVALS / "after-runs" / name).read_text(encoding="utf-8"))
    m = data["meta"]
    assert (m["provider"], m["model"], m["split"], m["split_version"]) == (
        "ollama",
        "kestrel-student",
        "safety",
        "s1.0",
    )
    assert (m["user"], m["kinds"], m["judge"], m["answer_policy"]["image_policy"]) == (user, kinds, None, "allowlist")
    assert len(data["tasks"]) == rows and all(r["status"] in ("pass", "fail") for r in data["tasks"])


def test_the_student_alone_main_pin_is_checks_only_x1_on_the_routed_runs_commit():
    """The served student alone on local Ollama, main suite v1.1, x1, no judge (checks only), every task graded,
    runs folder at a59439b (tag phase4-routed-runs). Context for section 8, never its claim."""
    data = json.loads((EVALS / "after-runs" / "student-main-x1.json").read_text(encoding="utf-8"))
    m = data["meta"]
    assert (m["provider"], m["model"], m["split"], m["suite_version"]) == ("ollama", "kestrel-student", "main", "1.1")
    assert (m["repeat"], m["judge"], m["git"]) == (1, None, "a59439b")
    assert len({r["id"] for r in data["tasks"]}) == len(data["tasks"]) == 100
    assert all(r["status"] in ("pass", "fail") for r in data["tasks"])


@pytest.mark.parametrize("folder", sorted(PINNED))
def test_readme_lists_the_pinned_hashes(folder):
    readme = (EVALS / folder / "README.md").read_text(encoding="utf-8")
    assert all(sha in readme for sha in PINNED[folder].values())
