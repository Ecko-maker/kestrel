"""scripts/check_lineage.py on synthetic lineage files (KAGGLE.md step 10): no adapter, no model."""

import hashlib
import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_lineage_script", ROOT / "scripts" / "check_lineage.py")
assert spec is not None and spec.loader is not None
script = importlib.util.module_from_spec(spec)
sys.modules["check_lineage_script"] = script
spec.loader.exec_module(script)

CFG = tomllib.loads((ROOT / "distill" / "train_config.toml").read_text(encoding="utf-8"))
HEAD = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def good() -> dict[str, Any]:
    build = json.loads(script.BUILD_MANIFEST.read_text(encoding="utf-8"))
    return {
        "smoke": False,
        "code_commit": HEAD,
        "base_model": dict(CFG["base_model"], random_tiny=False),
        "dataset": {
            "manifest_sha256": hashlib.sha256(script.BUILD_MANIFEST.read_bytes()).hexdigest(),
            "teachers": CFG["data"]["teachers"],
            "train_examples": build["stats"]["train"],
            "val_examples": build["stats"]["val"],
        },
        "results": {"train_loss": 0.81, "best_eval_loss": 0.92, "epochs": 3.0, "steps": 27},
    }


def run(tmp_path: Path, lineage: dict[str, Any], *extra: str) -> int:
    path = tmp_path / "lineage.json"
    path.write_text(json.dumps(lineage), encoding="utf-8")
    return int(script.main([str(path), *extra]))


def test_a_correct_lineage_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path, good(), "--code-commit", HEAD) == 0
    assert "lineage OK" in capsys.readouterr().out


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x.update(smoke=True),
        lambda x: x["base_model"].update(revision="0" * 40),
        lambda x: x["dataset"].update(manifest_sha256="0" * 64),  # e.g. option c's d1.2-pass1 build
        lambda x: x["dataset"].update(teachers=[{"provider": "ollama", "model": "kestrel-teacher-qwen3-30b-a3b"}]),
        lambda x: x["dataset"].update(val_examples=8),
        lambda x: x.update(code_commit="f" * 40),  # not a commit of this repository
        lambda x: x["results"].update(best_eval_loss=float("nan")),
    ],
)
def test_any_wrong_field_fails(tmp_path: Path, change: Any) -> None:
    lineage = good()
    change(lineage)
    assert run(tmp_path, lineage, "--code-commit", lineage["code_commit"]) == 1


def test_the_code_zips_commit_must_match_and_be_given(tmp_path: Path) -> None:
    assert run(tmp_path, good(), "--code-commit", "a" * 40) == 1
    assert run(tmp_path, good()) == 1
