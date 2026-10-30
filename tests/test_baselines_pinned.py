"""The pinned "before" baselines (evals/baselines/) are what Phase 3 is compared against, and the
pinned after-runs (evals/after-runs/) are the inputs of its verdict reports, and the pinned controls
(evals/controls/) are the inputs of drift checks (known issue #58). They must never change:
if this fails, restore the file from git instead of updating the hash."""

import hashlib
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
    },
    "controls": {
        "control-old-ci-2026-10-29.json": "0c48e60decdfb3a368ba2736fec5bf810f1656a72d2b694d57018b9dec3d2b8c",
    },
}


@pytest.mark.parametrize(("folder", "name"), sorted((f, n) for f, files in PINNED.items() for n in files))
def test_pinned_file_is_unchanged(folder, name):
    assert hashlib.sha256((EVALS / folder / name).read_bytes()).hexdigest() == PINNED[folder][name]


@pytest.mark.parametrize("folder", sorted(PINNED))
def test_readme_lists_the_pinned_hashes(folder):
    readme = (EVALS / folder / "README.md").read_text(encoding="utf-8")
    assert all(sha in readme for sha in PINNED[folder].values())
