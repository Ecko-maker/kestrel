"""The pinned "before" baselines (evals/baselines/) are what Phase 3 is compared against. They must
never change: if this fails, restore the file from git instead of updating the hash."""

import hashlib
from pathlib import Path

import pytest

BASELINES = Path(__file__).resolve().parents[1] / "evals" / "baselines"
PINNED = {
    "baseline-v1.1-judge-v2-final.json": "bfd6bdeb6c3ce6b456bf184a5d99ed9eea45dbedbc25df164f5aaa65a5bc8ba3",
    "run2-v1.1-judge-v2-final.json": "29a607859aee5595cf1ce1d7768e6e7caa601598c7d75447e9bca59b0d03afa4",
}


@pytest.mark.parametrize("name", sorted(PINNED))
def test_baseline_file_is_unchanged(name):
    assert hashlib.sha256((BASELINES / name).read_bytes()).hexdigest() == PINNED[name]


def test_readme_lists_the_pinned_hashes():
    readme = (BASELINES / "README.md").read_text(encoding="utf-8")
    assert all(sha in readme for sha in PINNED.values())
