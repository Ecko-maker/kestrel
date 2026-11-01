"""distill/export.py's Modelfile and train.py's GPU-smoke checks: the parts that need no GPU, torch or Ollama."""

import importlib.util
import json
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
        ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 11))
    train_source = (ROOT / "distill" / "train.py").read_text(encoding="utf-8")
    assert "from kestrel" not in train_source and "import kestrel" not in train_source  # runs on Kaggle
    export_source = (ROOT / "distill" / "export.py").read_text(encoding="utf-8")  # runs locally: one lazy import
    assert export_source.count("from kestrel") == 1 and "    from kestrel.tools import registry" in export_source


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


@pytest.mark.parametrize("out", ["/kaggle/input/run", "/kaggle/input/datasets/someone/kestrel-distill-code/run"])
def test_train_refuses_an_output_folder_under_kaggle_input(out: str) -> None:
    with pytest.raises(SystemExit, match="read-only: use a folder under /kaggle/working"):
        train.main(["--gpu-smoke", "--data", "anything", "--out", out])  # stops before torch is imported


def test_train_accepts_kaggle_working() -> None:
    train.check_out(Path("/kaggle/working/run"))
    train.check_out(Path("/kaggle/working/input"))  # only a path that starts /kaggle/input is refused


def test_training_checks_its_three_data_files_before_loading_a_model(tmp_path: Path) -> None:
    (tmp_path / "train.jsonl").write_text("{}\n", encoding="utf-8")  # e.g. only the smoke file
    with pytest.raises(SystemExit, match=r"missing val\.jsonl, manifest\.json"):
        train.main(["--data", str(tmp_path), "--out", str(tmp_path / "out")])
    train.check_data(ROOT / "distill" / "smoke")  # a complete folder passes


# --- Tool-schema pinning ------------------------------------------------------------


def served() -> list[dict]:
    return export.served_tool_schemas()


def test_served_schemas_are_the_agents() -> None:
    from kestrel.tools import registry

    assert served() == registry.schemas() and len(served()) >= 5


def test_identical_tools_pass() -> None:
    lineage = {"tool_schemas": tf.tools_fingerprint(served())}
    assert export.check_tools(lineage, served()) == []


def test_a_changed_tool_description_fails_and_names_the_tool() -> None:
    lineage = {"tool_schemas": tf.tools_fingerprint(served())}
    changed = json.loads(json.dumps(served()))
    target = changed[1]["function"]
    target["description"] += " (now also reads PDFs)"
    problems = export.check_tools(lineage, changed)
    assert problems == [f"{target['name']}: schema differs (description, parameters or type)"]


def test_added_removed_and_reordered_tools() -> None:
    tools = served()
    lineage = {"tool_schemas": tf.tools_fingerprint(tools)}
    extra = {"type": "function", "function": {"name": "new_tool", "description": "x", "parameters": {"type": "object"}}}
    assert export.check_tools(lineage, [*tools, extra]) == ["new_tool: sent at serve time, never seen in training"]
    gone = tools[0]["function"]["name"]
    assert export.check_tools(lineage, tools[1:]) == [f"{gone}: seen in training, not sent at serve time"]
    assert export.check_tools(lineage, list(reversed(tools)))[0].startswith("same tools, different order")
    assert "no tool_schemas" in export.check_tools({}, tools)[0]


def test_a_dropped_key_is_not_a_difference() -> None:
    """Ollama never sends keys it doesn't know (#74), so they can't make training and serving differ."""
    tools = json.loads(json.dumps(served()))
    lineage = {"tool_schemas": tf.tools_fingerprint(tools)}
    tools[0]["function"]["parameters"]["properties"] = {
        k: v | {"default": "ignored"} for k, v in tools[0]["function"]["parameters"]["properties"].items()
    }
    assert export.check_tools(lineage, tools) == []


def test_the_smoke_dataset_uses_todays_tools() -> None:
    """A changed tool schema makes every dataset built before it stale: the training text would show the old
    one. If this fails, rebuild distill/smoke with `kestrel distill build` (and any real dataset)."""
    sets = train.dataset_tool_sets(tf, ROOT / "distill" / "smoke")
    assert len(sets) == 1
    assert tf.compare_tools(sets[0], tf.tools_fingerprint(served())) == []


def test_base_and_student_get_the_same_modelfile() -> None:
    """--base and the student both go through modelfile(): only the GGUF they point to differs."""
    assert export.modelfile(tf.OLLAMA_TEMPLATE, "model-q4_K_M.gguf") == export.modelfile(
        tf.OLLAMA_TEMPLATE, "model-q4_K_M.gguf"
    )
    source = (ROOT / "distill" / "export.py").read_text(encoding="utf-8")
    assert source.count("modelfile(template, gguf.name)") == 1  # one call, used for --base and the student


def test_gguf_mode_writes_the_same_modelfile_next_to_an_existing_gguf(tmp_path: Path) -> None:
    """--gguf (serve a GGUF built earlier) skips merge and conversion and writes the student's Modelfile."""
    gguf = tmp_path / "model-q4_K_M.gguf"
    gguf.write_bytes(b"GGUF")
    assert export.main(["--gguf", str(gguf)]) == 0
    assert (tmp_path / "Modelfile").read_text(encoding="utf-8") == export.modelfile(tf.OLLAMA_TEMPLATE, gguf.name)


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--base"], "leave out --adapter and --base"),
        (["--out", "elsewhere"], "leave out --out"),
        (["--verify"], "needs --lineage"),
        (["--verify", "--lineage", "lineage.json"], "no tokenizer folder"),
    ],
)
def test_gguf_mode_refuses_what_it_cant_honour(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str], message: str
) -> None:
    gguf = tmp_path / "model-q4_K_M.gguf"
    gguf.write_bytes(b"GGUF")
    with pytest.raises(SystemExit):
        export.main(["--gguf", str(gguf), *extra])
    assert message in capsys.readouterr().err
    assert not (tmp_path / "Modelfile").exists()
