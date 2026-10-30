"""train_format (dataset JSONL -> student tokens and labels) and the train.py smoke run.

The tokenizer tests need the optional `train` extra and the student's tokenizer files in the Hugging
Face cache (no model weights): `uv sync --inexact --no-install-project --extra train`, then load the
tokenizer once (distill/TRAINING.md, "Local smoke test"). CI installs neither, so these tests skip
there with that reason; the syntax test below always runs.
"""

import ast
import importlib.util
import json
import math
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from kestrel.distill import train_format as tf

ROOT = Path(__file__).resolve().parents[1]
TRAIN_PY = ROOT / "distill" / "train.py"
CONFIG = tomllib.loads((ROOT / "distill" / "train_config.toml").read_text(encoding="utf-8"))
BASE = CONFIG["base_model"]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    }
]


def test_kaggle_files_parse_as_python_311() -> None:
    """Kaggle runs Python 3.11/3.12, and Kestrel's code is 3.14: these two must not use newer syntax."""
    for path in (TRAIN_PY, ROOT / "src" / "kestrel" / "distill" / "train_format.py"):
        ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 11))
        assert "import kestrel" not in path.read_text(encoding="utf-8") and "from kestrel" not in path.read_text(
            encoding="utf-8"
        ), path


@pytest.fixture(scope="module")
def tok() -> Any:
    transformers = pytest.importorskip("transformers", reason="needs the optional `train` extra")
    try:
        return transformers.AutoTokenizer.from_pretrained(BASE["id"], revision=BASE["revision"], local_files_only=True)
    except OSError:
        pytest.skip(f"tokenizer of {BASE['id']}@{BASE['revision'][:8]} not in the Hugging Face cache")


def record(**extra: Any) -> dict[str, Any]:
    call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "read_file", "arguments": json.dumps({"path": "a.txt"})},
    }
    messages = [
        {"role": "system", "content": "SYSTEM-TEXT"},
        {"role": "user", "content": "USER-ONE"},
        {"role": "assistant", "content": "OLD-ANSWER", "weight": 0},
        {"role": "user", "content": "USER-TWO"},
        {"role": "assistant", "content": "", "tool_calls": [call], "weight": 1},
        {"role": "tool", "tool_call_id": "c1", "content": "TOOL-RESULT"},
        {"role": "assistant", "content": "FINAL-ANSWER", "weight": 1},
    ]
    return {"messages": messages, "tools": TOOLS, "metadata": {"trace_id": "t1"}} | extra


def learned(tok: Any, encoded: dict[str, list[int]]) -> str:
    return tok.decode(
        [t for t, label in zip(encoded["input_ids"], encoded["labels"], strict=True) if label != tf.IGNORE]
    )


def test_tool_calls_round_trip(tok: Any) -> None:
    encoded = tf.encode(tok, record(), 4096)
    calls = tf.parse_tool_calls(learned(tok, encoded))
    assert calls == [{"name": "read_file", "arguments": {"path": "a.txt"}}]
    full = tok.decode(encoded["input_ids"])
    assert "<tool_response>\nTOOL-RESULT\n</tool_response>" in full  # the result is there, as context
    assert '{"name":"read_file","description"' in full.split("<|im_end|>")[0]  # the schema (Go JSON), system turn


def test_only_weight_one_assistant_turns_are_learned(tok: Any) -> None:
    text = learned(tok, tf.encode(tok, record(), 4096))
    assert "FINAL-ANSWER" in text and "<tool_call>" in text
    for context in ("SYSTEM-TEXT", "USER-ONE", "USER-TWO", "TOOL-RESULT", "OLD-ANSWER", '"description":"Read a file."'):
        assert context not in text, context
    assert text.count("<|im_end|>") == 2  # each learned turn includes its end-of-turn token
    assert "<|im_start|>" not in text  # but never the role header


def test_weight_zero_turns_are_masked(tok: Any) -> None:
    rec = record()
    for m in rec["messages"]:
        if m["role"] == "assistant":
            m["weight"] = 0
    rec["messages"][-1]["weight"] = 1
    assert learned(tok, tf.encode(tok, rec, 4096)).strip() == "FINAL-ANSWER<|im_end|>"


def test_nothing_to_learn_raises(tok: Any) -> None:
    rec = record()
    for m in rec["messages"]:
        m["weight"] = 0
    with pytest.raises(tf.NothingToLearn):
        tf.encode(tok, rec, 4096)


def test_too_long_raises_instead_of_truncating(tok: Any, tmp_path: Path) -> None:
    n = len(tf.encode(tok, record(), 4096)["input_ids"])
    assert len(tf.encode(tok, record(), n)["input_ids"]) == n  # exactly at the limit is fine
    with pytest.raises(tf.TooLong, match=f"{n} tokens > max_length {n - 1}"):
        tf.encode(tok, record(), n - 1)
    path = tmp_path / "train.jsonl"
    path.write_text(json.dumps(record()) + "\n", encoding="utf-8")
    with pytest.raises(tf.TooLong, match=r"line 1 \(trace t1\)"):
        tf.encode_file(tok, path, n - 1)


def test_the_smoke_dataset_encodes(tok: Any) -> None:
    for name in ("train.jsonl", "val.jsonl"):
        rows = tf.encode_file(tok, ROOT / "distill" / "smoke" / name, CONFIG["data"]["max_length"])
        assert rows and all(any(label != tf.IGNORE for label in r["labels"]) for r in rows)


def test_smoke_training_run(tok: Any, tmp_path: Path) -> None:
    pytest.importorskip("trl", reason="needs the optional `train` extra")
    spec = importlib.util.spec_from_file_location("kestrel_train_script", TRAIN_PY)
    assert spec is not None and spec.loader is not None
    train = importlib.util.module_from_spec(spec)
    sys.modules["kestrel_train_script"] = train
    spec.loader.exec_module(train)
    assert train.main(["--smoke", "--data", str(ROOT / "distill" / "smoke"), "--out", str(tmp_path)]) == 0
    assert (tmp_path / "adapter" / "adapter_model.safetensors").is_file()
    assert (tmp_path / "adapter" / "adapter_config.json").is_file()
    lineage = json.loads((tmp_path / "lineage.json").read_text(encoding="utf-8"))
    manifest = (ROOT / "distill" / "smoke" / "manifest.json").read_bytes()
    assert lineage["smoke"] is True and lineage["base_model"]["revision"] == BASE["revision"]
    assert lineage["dataset"]["manifest_sha256"] == __import__("hashlib").sha256(manifest).hexdigest()
    assert lineage["dataset"]["traces_db_sha256"] == json.loads(manifest)["input_db_sha256"]
    assert lineage["dataset"]["teachers"] == [{"provider": "groq", "model": "openai/gpt-oss-120b"}]
    assert lineage["config"] == CONFIG and lineage["libraries"]["transformers"]
    assert lineage["distinct_tool_sets"] == 1 and len(lineage["tool_schemas"]["sha256"]) == 64
    assert lineage["tool_schemas"]["names"][:2] == ["get_current_time", "calculator"]
    for loss in (lineage["results"]["train_loss"], lineage["results"]["final_eval_loss"]):
        assert math.isfinite(loss)
    assert any("eval_loss" in row for row in lineage["results"]["history"])


def test_notebook_pins_match_the_lock_and_call_the_script() -> None:
    """The Kaggle notebook installs exactly the versions uv.lock resolved, and only runs train.py."""
    import re

    notebook = json.loads((ROOT / "distill" / "train.ipynb").read_text(encoding="utf-8"))
    source = "".join("".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code")
    pins = dict(re.findall(r'"([a-z_-]+)==([^"]+)"', source))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {p["name"]: p["version"] for p in lock["package"]}
    assert set(pins) == {"transformers", "peft", "trl", "datasets", "accelerate", "bitsandbytes"}
    assert all(locked[name] == version for name, version in pins.items()), (pins, locked)
    flat = " ".join(source.split())  # ruff may put one argument per line
    assert '"--gpu-smoke", "--data", CODE,' in flat  # the smoke set sits next to train.py
    assert '"--data", DATA, "--out", OUT,' in flat and "train_config.toml" in source
    assert "/smoke" not in source  # Kaggle uploads are flat: the smoke train.jsonl sits next to train.py
    assert "hf_" not in source.lower().replace("hf_token", "")  # no token literal, only the secret's name


def notebook_cells() -> list[str]:
    notebook = json.loads((ROOT / "distill" / "train.ipynb").read_text(encoding="utf-8"))
    return ["".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"]


def test_notebook_install_cell_stops_on_a_wrong_version_or_no_internet() -> None:
    install = next(c for c in notebook_cells() if "REQUIREMENTS = [" in c)
    assert install.index("socket.getaddrinfo") < install.index('"pip", "install"')  # internet checked first
    assert "Phone verification" in install and "Internet" in install
    assert install.index('"pip", "install"') < install.index("metadata.version(name)")  # versions read after pip
    assert "if wrong:\n    raise RuntimeError" in install


def test_notebook_full_training_is_off_unless_asked_and_checks_the_data() -> None:
    cells = notebook_cells()
    train = next(c for c in cells if '"--data", DATA' in c)
    assert train.startswith("RUN_TRAINING = False")
    assert train.index("if not RUN_TRAINING:") < train.index("missing = [") < train.index('"--data", DATA')
    package = next(c for c in cells if "make_archive" in c)
    assert "if not RUN_TRAINING:" in package
    smoke = next(c for c in cells if '"--gpu-smoke"' in c)
    assert "RUN_TRAINING" not in smoke and cells.index(smoke) < cells.index(train)


@pytest.mark.parametrize("layout", ["kaggle/input/{slug}", "kaggle/input/datasets/someone/{slug}"])
def test_notebook_finds_the_datasets_in_both_kaggle_layouts(tmp_path: Path, layout: str) -> None:
    """The paths cell, run against a fake /kaggle/input: the old and the new mount layout, flat files."""
    paths_cell = next(c for c in notebook_cells() if "def folders_with" in c)
    code = tmp_path / layout.format(slug="kestrel-distill-code")
    data = tmp_path / layout.format(slug="kestrel-distill-data")
    for folder, names in ((code, ["train.py", "train_format.py", "train_config.toml", "train.jsonl"]),
                          (data, ["train.jsonl", "val.jsonl", "manifest.json"])):  # fmt: skip
        folder.mkdir(parents=True)
        for name in names:
            (folder / name).write_text("x", encoding="utf-8")
    source = paths_cell.replace('Path("/kaggle/input")', f"Path({str(tmp_path / 'kaggle' / 'input')!r})")
    namespace: dict[str, Any] = {}
    exec(compile(source, "paths_cell", "exec"), namespace)
    assert namespace["CODE"] == str(code) and namespace["DATA"] == str(data)
    assert namespace["OUT"].startswith("/kaggle/working/") and namespace["SMOKE_OUT"].startswith("/kaggle/working/")

    (data / "val.jsonl").unlink()  # no complete training set: fine for the smoke test
    exec(compile(source, "paths_cell", "exec"), namespace)
    assert namespace["CODE"] == str(code) and namespace["DATA"] is None
