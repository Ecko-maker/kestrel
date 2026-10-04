"""Shared test setup."""

import pytest

from kestrel.bench import calibrate


@pytest.fixture(autouse=True)
def _no_real_labels(tmp_path_factory, monkeypatch):
    """Human labels are created only by a person running `kestrel bench label`. No test may read or
    write the real evals/labels/human.jsonl (or the judge cache), whatever it forgets to patch."""
    scratch = tmp_path_factory.mktemp("labels")
    monkeypatch.setattr(calibrate, "LABELS", scratch / "human.jsonl")
    monkeypatch.setattr(calibrate, "VERDICT_CACHE", scratch / "judge-cache.jsonl")
