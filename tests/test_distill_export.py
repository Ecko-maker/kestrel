"""distill/export.py's Modelfile and train.py's GPU-smoke checks: the parts that need no GPU, torch or Ollama."""

import importlib.util
import math
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

from kestrel.distill import train_format as tf

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


export = load("kestrel_export_script", ROOT / "distill" / "export.py")
train = load("kestrel_train_script_for_checks", ROOT / "distill" / "train.py")
CONFIG = tomllib.loads((ROOT / "distill" / "train_config.toml").read_text(encoding="utf-8"))


def test_modelfile() -> None:
    text = export.modelfile(tf.OLLAMA_TEMPLATE, "model-q4_K_M.gguf")
    lines = text.splitlines()
    assert lines[0] == "FROM ./model-q4_K_M.gguf"
    assert f'TEMPLATE """{tf.OLLAMA_TEMPLATE}"""' in text  # the exact template, nothing added
    assert "PARAMETER stop <|im_start|>" in lines and "PARAMETER stop <|im_end|>" in lines
    assert f"PARAMETER num_ctx {export.NUM_CTX}" in lines and export.NUM_CTX >= 16000
    with pytest.raises(ValueError, match="triple quotes"):
        export.modelfile('x """ y', "m.gguf")


def test_llama_cpp_is_pinned() -> None:
    assert export.LLAMA_CPP_BUILD.startswith("b") and export.LLAMA_CPP_BUILD[1:].isdigit()
    assert export.default_llama_cpp().name == f"llama.cpp-{export.LLAMA_CPP_BUILD}"


def test_kaggle_and_export_scripts_parse_as_python_311() -> None:
    import ast

    for path in (ROOT / "distill" / "export.py", ROOT / "distill" / "train.py"):
        source = path.read_text(encoding="utf-8")
        ast.parse(source, feature_version=(3, 11))
        assert "from kestrel" not in source and "import kestrel" not in source


# --- GPU smoke checks ------------------------------------------------------------


def test_overfit_check() -> None:
    assert train.check_overfit([2.0, 1.5, 0.9], 0.5)[0]
    assert not train.check_overfit([2.0, 1.5, 1.1], 0.5)[0]
    assert not train.check_overfit([2.0, math.nan, 0.1], 0.5)[0]
    assert not train.check_overfit([2.0, math.inf], 0.5)[0]
    assert not train.check_overfit([], 0.5)[0]
    assert "45% drop" in train.check_overfit([2.0, 1.1], 0.5)[1]


def test_trainable_tokens_check() -> None:
    assert train.check_trainable([12, 30])[0]
    assert not train.check_trainable([12, 0])[0]


def test_version_report() -> None:
    pins = CONFIG["pins"]
    found = {name: pins.get(name) for name in train.LIBRARIES} | {
        "torch": "2.6.0+cu124",
        "python": "3.11.13",
        "cuda": "12.4",
        "gpu": "Tesla T4",
    }
    lines, mismatched = train.version_report(found, pins)
    assert mismatched == []
    assert any("torch" in line and "not pinned" in line for line in lines)
    _, mismatched = train.version_report(found | {"trl": "1.0.0"}, pins)
    assert mismatched == ["trl"]


def test_pins_match_the_lock() -> None:
    lock = {p["name"]: p["version"] for p in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]}
    assert all(lock[name] == version for name, version in CONFIG["pins"].items()), CONFIG["pins"]
    assert set(CONFIG["pins"]) == {"transformers", "peft", "trl", "datasets", "accelerate", "bitsandbytes"}


def test_the_smoke_data_has_a_tool_prompt() -> None:
    record = tf.load_jsonl(ROOT / "distill" / "smoke" / "train.jsonl")[0]
    messages, names = train.first_tool_prompt(record)
    assert messages[-1]["role"] == "user" and "read_file" in names
    prompt, _ = tf.render(messages, record["tools"], generation_prompt=True)
    assert prompt.endswith("<|im_start|>assistant\n")
    with pytest.raises(SystemExit):
        train.first_tool_prompt({"messages": [{"role": "user", "content": "hi"}], "tools": []})


def test_gpu_smoke_settings() -> None:
    s = CONFIG["gpu_smoke"]
    assert s["steps"] >= 20 and 0 < s["min_loss_drop"] < 1 and s["batch_examples"] >= 2
