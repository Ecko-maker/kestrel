"""The pinned "before" baselines (evals/baselines/) are what Phase 3 is compared against, and the
pinned after-runs (evals/after-runs/) are the inputs of its verdict reports, and the pinned controls
(evals/controls/) are the inputs of drift checks (known issue #58), and the pinned Phase 3 evidence
(evals/after-runs/phase3/) is the safety gate's before and after runs. They must never change:
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
    # Phase 3's safety runs and the tiers v2 main-suite after-run, each with its report
    "after-runs/phase3": {
        "safety-before-smoke.json": "beba90a97ed0a4ae77cfc8f069667f0917b742bd5cdd9814aff2021ed09f7b0a",
        "safety-before-smoke.md": "c7908a41f55aee01fe10e62072124b6c12783baa8746eeb861200e8bd5995e94",
        # redacted copy: one model-written email address (README); the original is efc1ca17...
        "safety-before-strict-x3.json": "f0d5c2a3efe695988ee2724a3d42a9090d98bdd3962e2b907d5deff5c4f1d2db",
        "safety-before-strict-x3.md": "248b5ecf79179d9fac42610aad7964a6d6b88440a0402bb8c7b1addd072637c5",
        "safety-before-rubber-attacks.json": "f96cd786d24862200f3891c746e05a73934171ff463df1dca949bec5c9e12fed",
        "safety-before-rubber-attacks.md": "eb9f1742aa74967dc1892f97e9a4dd4183be7c63872cd10cc7a02dbe56225e6c",
        "safety-after-strict-x3.json": "2ec8d7f619d3111d8f165aecf5c9600e8444dc1261b4632862a60c62b334826d",
        "safety-after-strict-x3.md": "4ec8ef80385347e6d64f25f862af304a89eda6c2c27d6aa33042f67313c24c25",
        "safety-after-rubber-attacks.json": "39d0db1839b1233c32965533273ceb595ddd43d168e0cc37bf76dcd73fac1e8f",
        "safety-after-rubber-attacks.md": "9e61fe1371f8400f16af471b580de783942b4cd09f61c9be822e0d63934f8214",
        "safety-after-v21-strict-x3.json": "bca96d70b9f2aa1c62cf68afd7807b5232c09fccf5d1b82c15a732717aa3031d",
        "safety-after-v21-strict-x3.md": "7310a3bb9e644bc447342d4efbc56dc3d6af867d7717009e56b817c940e83356",
        "safety-after-v21-rubber-attacks.json": "d663322c94e01a260e0f8a51d560082287b51f85c476c9e5a67bb48b7423d81f",
        "safety-after-v21-rubber-attacks.md": "a2c2da1c89a9c4ea4524c83e4df37b2d275053c69ee7d70b444b50ea4c3d83be",
        "main-after-v1.1.json": "8cb09a90aae510b9295c49ef3704c7edecdc6b812dac74c866e10979e2eb818a",
        "main-after-v1.1.md": "3b2435a6bde94960a307f8d2b749a081f296e3baa7e1948ebcd9e6109cb7412a",
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


@pytest.mark.parametrize(
    ("name", "git", "split", "user", "rows"),
    [
        ("safety-before-smoke.json", "6f58ee5", "safety", "strict", 5),
        ("safety-before-strict-x3.json", "6f58ee5", "safety", "strict", 144),
        ("safety-before-rubber-attacks.json", "6f58ee5", "safety", "rubber-stamp", 30),
        ("safety-after-strict-x3.json", "165f725", "safety", "strict", 144),
        ("safety-after-rubber-attacks.json", "165f725", "safety", "rubber-stamp", 30),
        ("safety-after-v21-strict-x3.json", "75ce54c", "safety", "strict", 144),
        ("safety-after-v21-rubber-attacks.json", "75ce54c", "safety", "rubber-stamp", 30),
        ("main-after-v1.1.json", "165f725", "main", None, 100),
    ],
)
def test_the_phase3_pins_are_the_teacher_runs_at_their_tags(name, git, split, user, rows):
    """gpt-oss-120b on Groq, runs folder at the commit of the safety-before / safety-after / safety-after-v2.1 tag."""
    data = json.loads((EVALS / "after-runs" / "phase3" / name).read_text(encoding="utf-8"))
    m = data["meta"]
    assert (m["provider"], m["model"], m["git"], m["split"], m["user"]) == (
        "groq",
        "openai/gpt-oss-120b",
        git,
        split,
        user,
    )
    assert len(data["tasks"]) == rows


def test_the_redacted_phase3_copy_changes_one_answer_only():
    text = (EVALS / "after-runs" / "phase3" / "safety-before-strict-x3.json").read_text(encoding="utf-8")
    assert text.count("[redacted-email]") == 1
    row = next(r for r in json.loads(text)["tasks"] if "[redacted-email]" in json.dumps(r))
    assert (row["id"], row["repeat"], row["status"]) == ("owner-4", 2, "pass")
    assert "[redacted-email]" in row["answer"]


@pytest.mark.parametrize("folder", sorted(PINNED))
def test_readme_lists_the_pinned_hashes(folder):
    readme = (EVALS / folder / "README.md").read_text(encoding="utf-8")
    assert all(sha in readme for sha in PINNED[folder].values())
