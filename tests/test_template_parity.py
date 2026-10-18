"""The student's prompt format: training text = Ollama's rendering, byte for byte.

Runs everywhere (pure Python): the training renderer against distill/parity/golden.json, which Ollama's own
renderer wrote (scripts/template_parity.py --write-golden). Where that renderer is built
(%LOCALAPPDATA%/kestrel-tools/ollama-render.exe or $KESTREL_OLLAMA_RENDER), it is also compared live.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from kestrel.distill import train_format as tf

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("template_parity", ROOT / "scripts" / "template_parity.py")
assert spec is not None and spec.loader is not None
tp = importlib.util.module_from_spec(spec)
sys.modules["template_parity"] = tp
spec.loader.exec_module(tp)

POINTS = tp.points(json.loads(tp.CASES.read_text(encoding="utf-8")))
GOLDEN = json.loads(tp.GOLDEN.read_text(encoding="utf-8"))


def test_golden_is_current() -> None:
    """Editing OLLAMA_TEMPLATE or bumping Ollama means re-rendering with Ollama (--write-golden)."""
    assert GOLDEN["template_sha256"] == tp.template_sha(), (
        "template changed: run scripts/template_parity.py --write-golden"
    )
    assert GOLDEN["ollama"] == tp.ollama_version()
    assert set(GOLDEN["prompts"]) == {name for name, _, _ in POINTS}


@pytest.mark.parametrize(("name", "messages", "tools"), POINTS, ids=[p[0] for p in POINTS])
def test_training_text_equals_ollamas(name: str, messages: list[dict], tools: list[dict]) -> None:
    text, _ = tf.render(messages, tools, generation_prompt=True)
    assert text == GOLDEN["prompts"][name], tp.first_difference(text, GOLDEN["prompts"][name])


def test_the_cases_cover_the_risky_shapes() -> None:
    cases = {c["name"]: c for c in json.loads(tp.CASES.read_text(encoding="utf-8"))}
    calls = [len(m.get("tool_calls") or []) for c in cases.values() for m in c["messages"]]
    assert max(calls) >= 2  # several calls in one turn
    assert any(  # consecutive tool results
        a["role"] == b["role"] == "tool"
        for c in cases.values()
        for a, b in zip(c["messages"], c["messages"][1:], strict=False)
    )
    assert sum(m["role"] == "system" for m in cases["collate"]["messages"]) == 2
    assert "default" in json.dumps(cases["system-tools-user"]["tools"])  # Kestrel's real schemas, keys Ollama drops


def test_live_against_ollamas_renderer() -> None:
    path = Path(
        os.environ.get("KESTREL_OLLAMA_RENDER")
        or Path(os.environ.get("LOCALAPPDATA", "")) / "kestrel-tools" / "ollama-render.exe"
    )
    if not path.is_file():
        pytest.skip("Ollama's renderer isn't built here (distill/EXPORT.md, 'Template parity')")
    assert tp.compare(tp.render_python(POINTS), tp.render_ollama(POINTS), "Ollama") == 0


def test_first_difference_names_the_line() -> None:
    assert tp.first_difference("a\nb\nc", "a\nB\nc") == "line 2:\n  training: 'b'\n  ollama:   'B'"


# --- The Go-JSON rules Ollama imposes ---------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"b": 1, "a": [True, None, "x"]}, '{"a":[true,null,"x"],"b":1}'),
        (2.0, "2"),
        (2.5, "2.5"),
        (1e-07, "1e-7"),
        (0.000001, "0.000001"),
        (1e21, "1e+21"),
        ("<b>&</b>", '"\\u003cb\\u003e\\u0026\\u003c/b\\u003e"'),
        ("tab\there\nnew\r", '"tab\\there\\nnew\\r"'),
        ("é ü —  ", '"é ü — \\u2028"'),
        ('quote " and \\', '"quote \\" and \\\\"'),
        ("\x01", '"\\u0001"'),
    ],
)
def test_go_json(value: object, expected: str) -> None:
    assert tf.go_json(value) == expected


def test_tool_json_keeps_ollamas_fields_and_order() -> None:
    tool = {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "q"},
                    "max_results": {"type": "integer", "default": 5, "description": "n"},
                },
                "required": ["query"],
            },
        },
    }
    assert tf.tool_json(tool) == (
        '{"type":"function","function":{"name":"web_search","description":"Search.","parameters":{"type":"object",'
        '"required":["query"],"properties":{"max_results":{"type":"integer","description":"n"},'
        '"query":{"type":"string","description":"q"}}}}}'
    )  # "default" is dropped: Ollama's ToolProperty has no such field


def test_arguments_json() -> None:
    assert tf.arguments_json('{"path": "a.txt", "a": 2.0}') == '{"a":2,"path":"a.txt"}'
    assert tf.arguments_json("{}") == "{}" and tf.arguments_json("") == "{}" and tf.arguments_json(None) == "{}"


# --- Rendering ---------------------------------------------------------------------


def test_spans_cover_exactly_the_learned_turns() -> None:
    call = {"id": "c", "type": "function", "function": {"name": "f", "arguments": "{}"}}
    messages = [
        {"role": "user", "content": "U"},
        {"role": "assistant", "content": "OLD", "weight": 0},
        {"role": "user", "content": "U2"},
        {"role": "assistant", "content": "", "tool_calls": [call], "weight": 1},
        {"role": "tool", "content": "R"},
        {"role": "assistant", "content": "NEW", "weight": 1},
    ]
    text, spans = tf.render(messages, [])
    assert [text[a:b] for a, b in spans] == [
        '<tool_call>\n{"name": "f", "arguments": {}}\n</tool_call><|im_end|>',
        "NEW<|im_end|>",
    ]
    assert not text.endswith("<|im_start|>assistant\n")
    assert tf.render(messages[:-1], [], generation_prompt=True)[0].endswith(
        "</tool_response><|im_end|>\n<|im_start|>assistant\n"
    )


def test_collate_matches_ollama() -> None:
    system, merged = tf.collate(
        [
            {"role": "system", "content": "A"},
            {"role": "system", "content": "B"},
            {"role": "user", "content": "1"},
            {"role": "user", "content": "2"},
            {"role": "tool", "content": "x"},
            {"role": "tool", "content": "y"},
        ]
    )
    assert system == "A\n\nB"
    assert [(m["role"], m["content"]) for m in merged] == [
        ("system", "A\n\nB"),
        ("user", "1\n\n2"),
        ("tool", "x"),
        ("tool", "y"),
    ]
    with pytest.raises(ValueError, match="two assistant messages"):
        tf.collate(
            [{"role": "assistant", "content": "a", "weight": 0}, {"role": "assistant", "content": "b", "weight": 1}]
        )


def test_the_template_fits_a_modelfile() -> None:
    assert '"""' not in tf.OLLAMA_TEMPLATE
    assert "{{ json . }}" in tf.OLLAMA_TEMPLATE and ".Function.Arguments" in tf.OLLAMA_TEMPLATE
