"""The student's prompt format, rendered once in Python for training and once in Go for Ollama.

The format is Qwen3's (special tokens <|im_start|> / <|im_end|>, tools in the system turn, <tool_call>
JSON blocks, tool results as <tool_response> blocks in a user turn) with one serialization rule taken from
Ollama: tool schemas and tool-call arguments are JSON as Go's encoding/json writes them (compact, map keys
sorted, struct fields in Ollama's order, <>& escaped, keys Ollama doesn't know dropped). Ollama can only
produce that serialization, so training uses it too: the student sees, byte for byte, what it will see
when served (scripts/template_parity.py checks it against Ollama's own renderer).

- render(): the conversation as text, with the character spans of the assistant turns to learn.
- OLLAMA_TEMPLATE: the same format as an Ollama Modelfile TEMPLATE (Go text/template).
- encode(): tokens and labels. Every token is -100 (ignored by the loss) except those of assistant turns with
  weight 1, from after the "assistant" header through the turn's <|im_end|>. Longer than max_length raises
  TooLong: a truncated trajectory would teach a broken turn.

Records are {"messages", "tools", "metadata"} in the chat format of `kestrel export` (OpenAI-style tool
calls with JSON-string arguments; "weight" on assistant messages). This file runs on Kaggle, where
Kestrel can't be installed: standard library only (plus a tokenizer passed in), Python 3.11 syntax.
"""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

IGNORE = -100
_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

TOOLS_INTRO = (
    "# Tools\n\nYou may call one or more functions to assist with the user query.\n\n"
    "You are provided with function signatures within <tools></tools> XML tags:\n<tools>"
)
TOOLS_OUTRO = (
    "\n</tools>\n\nFor each function call, return a json object with function name and arguments within "
    '<tool_call></tool_call> XML tags:\n<tool_call>\n{"name": <function-name>, "arguments": <args-json-object>}'
    "\n</tool_call>"
)

# The same format for Ollama (Go text/template, rendered by Ollama's template package after its OpenAI
# layer has turned argument strings into maps). Every newline is written as {{ "\n" }} so whitespace
# trimming can't move one. Ollama joins all system messages into .System and merges consecutive messages
# of the same role (except tool) before rendering; render() does the same.
OLLAMA_TEMPLATE = (
    '{{- if .Tools }}<|im_start|>system{{ "\\n" }}'
    '{{- if .System }}{{ .System }}{{ "\\n\\n" }}{{ end }}'
    + "".join(f'{{{{- "{json.dumps(line)[1:-1]}" }}}}' for line in [TOOLS_INTRO])
    + '{{- range .Tools }}{{ "\\n" }}{{ json . }}{{ end }}'
    + "".join(f'{{{{- "{json.dumps(line)[1:-1]}" }}}}' for line in [TOOLS_OUTRO])
    + '<|im_end|>{{ "\\n" }}'
    '{{- else if .System }}<|im_start|>system{{ "\\n" }}{{ .System }}<|im_end|>{{ "\\n" }}{{ end }}'
    '{{- $prev := "" }}'
    "{{- range $i, $m := .Messages }}"
    "{{- $rest := slice $.Messages $i }}"
    "{{- $nextTool := false }}"
    '{{- if gt (len $rest) 1 }}{{ if eq (index $rest 1).Role "tool" }}{{ $nextTool = true }}{{ end }}{{ end }}'
    '{{- if eq $m.Role "user" }}<|im_start|>user{{ "\\n" }}{{ $m.Content }}<|im_end|>{{ "\\n" }}'
    '{{- else if eq $m.Role "assistant" }}<|im_start|>assistant{{ "\\n" }}{{ $m.Content }}'
    "{{- range $j, $c := $m.ToolCalls }}"
    '{{- if or $j $m.Content }}{{ "\\n" }}{{ end }}'
    '<tool_call>{{ "\\n" }}{"name": "{{ $c.Function.Name }}", "arguments": {{ $c.Function.Arguments }}}'
    '{{ "\\n" }}</tool_call>'
    '{{- end }}<|im_end|>{{ "\\n" }}'
    '{{- else if eq $m.Role "tool" }}'
    '{{- if ne $prev "tool" }}<|im_start|>user{{ end }}'
    '{{ "\\n" }}<tool_response>{{ "\\n" }}{{ $m.Content }}{{ "\\n" }}</tool_response>'
    '{{- if not $nextTool }}<|im_end|>{{ "\\n" }}{{ end }}'
    "{{- end }}"
    "{{- $prev = $m.Role }}"
    "{{- end }}"
    '{{- if $prev }}{{ if ne $prev "assistant" }}<|im_start|>assistant{{ "\\n" }}{{ end }}{{ end }}'
)


class TooLong(ValueError):
    """An example has more tokens than max_length. It is never truncated."""


class NothingToLearn(ValueError):
    """An example has no assistant turn with weight 1."""


# --- JSON as Go's encoding/json writes it --------------------------------------


def _go_string(s: str) -> str:
    out = ['"']
    for ch in s:
        code = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif code < 0x20 or ch in "<>&\u2028\u2029":
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _go_float(f: float) -> str:
    if not math.isfinite(f):
        raise ValueError(f"JSON can't hold {f}")
    if f == int(f) and abs(f) < 1e21:
        return str(int(f))
    if 1e-6 <= abs(f) < 1e21:
        return format(Decimal(repr(f)), "f")
    mantissa, exponent = repr(f).split("e")
    return f"{mantissa}e{exponent[0]}{exponent[1:].lstrip('0') or '0'}"


def go_json(value: Any, *, sort_keys: bool = True) -> str:
    """JSON text exactly as Go's json.Marshal writes it. Maps are sorted (Go map[string]any); pass
    sort_keys=False for an ordered structure. Numbers become float64 in Go, as they do when Ollama parses
    them: 2.0 -> 2, 1e-07 -> 1e-7."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _go_string(value)
    if isinstance(value, int):
        return _go_float(float(value))
    if isinstance(value, float):
        return _go_float(value)
    if isinstance(value, dict):
        items = sorted(value.items()) if sort_keys else list(value.items())
        return "{" + ",".join(f"{_go_string(str(k))}:{go_json(v, sort_keys=sort_keys)}" for k, v in items) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(go_json(v, sort_keys=sort_keys) for v in value) + "]"
    raise TypeError(f"can't serialize {type(value).__name__}")


def _ordered(pairs: list[tuple[str, str]]) -> str:
    """A Go struct: fields in declaration order, values already serialized."""
    return "{" + ",".join(f"{_go_string(k)}:{v}" for k, v in pairs) + "}"


def _property(p: dict[str, Any]) -> str:
    """api.ToolProperty: anyOf, type, items, description, enum, properties, required (all omitempty).
    Other keys (e.g. "default") don't exist in Ollama's struct and are dropped."""
    fields: list[tuple[str, str]] = []
    if p.get("anyOf"):
        fields.append(("anyOf", "[" + ",".join(_property(a) for a in p["anyOf"]) + "]"))
    t = p.get("type")
    if t:
        fields.append(("type", go_json(t if isinstance(t, str) else (t[0] if len(t) == 1 else list(t)))))
    if p.get("items") is not None:
        fields.append(("items", go_json(p["items"])))
    if p.get("description"):
        fields.append(("description", go_json(p["description"])))
    if p.get("enum"):
        fields.append(("enum", go_json(p["enum"])))
    if p.get("properties"):  # an ordered map: insertion order is kept
        fields.append(
            ("properties", "{" + ",".join(f"{_go_string(k)}:{_property(v)}" for k, v in p["properties"].items()) + "}")
        )
    if p.get("required"):
        fields.append(("required", go_json(p["required"])))
    return _ordered(fields)


def tool_json(tool: dict[str, Any]) -> str:
    """One OpenAI-style tool as Ollama's template writes it ({{ json . }} on its templateTool)."""
    fn = tool.get("function") or {}
    params = fn.get("parameters") or {}
    param_fields: list[tuple[str, str]] = [("type", go_json(params.get("type", "")))]
    if params.get("$defs") is not None:
        param_fields.append(("$defs", go_json(params["$defs"])))
    if params.get("items") is not None:
        param_fields.append(("items", go_json(params["items"])))
    if params.get("required"):
        param_fields.append(("required", go_json(params["required"])))
    props = params.get("properties")
    param_fields.append(
        (
            "properties",
            "{" + ",".join(f"{_go_string(k)}:{_property(v)}" for k, v in sorted(props.items())) + "}"
            if props
            else "null",
        )
    )
    fields: list[tuple[str, str]] = [("type", go_json(tool.get("type", "")))]
    if tool.get("items") is not None:
        fields.append(("items", go_json(tool["items"])))
    fn_fields = [
        ("name", go_json(fn.get("name", ""))),
        ("description", go_json(fn.get("description", ""))),
        ("parameters", _ordered(param_fields)),
    ]
    fields.append(("function", _ordered(fn_fields)))
    return _ordered(fields)


def arguments_json(arguments: Any) -> str:
    """Tool-call arguments as Ollama renders them: the JSON string parsed into a map, then marshaled."""
    if isinstance(arguments, str):
        arguments = json.loads(arguments) if arguments.strip() else {}
    return go_json(arguments) if arguments else "{}"


# --- Rendering -------------------------------------------------------------------


def collate(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Ollama's collate(): all system contents joined by a blank line, and consecutive messages of the same
    role (except tool) merged with a blank line. Merging two assistant turns with different weights
    would blur what is learned, so it raises."""
    system = [str(m.get("content") or "") for m in messages if m.get("role") == "system"]
    merged: list[dict[str, Any]] = []
    for m in messages:
        m = dict(m)
        m["content"] = m.get("content") or ""
        if merged and merged[-1]["role"] == m["role"] and m["role"] != "tool":
            last = merged[-1]
            if m["role"] == "assistant" and (last.get("weight", 1) != m.get("weight", 1) or last.get("tool_calls")):
                raise ValueError("two assistant messages in a row can't be rendered the way Ollama merges them")
            last["content"] = last["content"] + "\n\n" + m["content"]
            last["tool_calls"] = (last.get("tool_calls") or []) + (m.get("tool_calls") or [])
        else:
            merged.append(m)
    return "\n\n".join(system), merged


def render(
    messages: list[dict[str, Any]], tools: list[dict] | None, *, generation_prompt: bool = False
) -> tuple[str, list[tuple[int, int]]]:
    """(prompt text, character spans of the weight-1 assistant turns). With generation_prompt, an
    "assistant" header is added when the last message isn't an assistant turn, as Ollama does."""
    system, msgs = collate(messages)
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    pos = 0

    def emit(text: str) -> None:
        nonlocal pos
        parts.append(text)
        pos += len(text)

    if tools:
        emit("<|im_start|>system\n" + (system + "\n\n" if system else "") + TOOLS_INTRO)
        for tool in tools:
            emit("\n" + tool_json(tool))
        emit(TOOLS_OUTRO + "<|im_end|>\n")
    elif system:
        emit(f"<|im_start|>system\n{system}<|im_end|>\n")
    prev = ""
    for i, m in enumerate(msgs):
        role = m["role"]
        next_tool = i + 1 < len(msgs) and msgs[i + 1]["role"] == "tool"
        if role == "user":
            emit(f"<|im_start|>user\n{m['content']}<|im_end|>\n")
        elif role == "assistant":
            emit("<|im_start|>assistant\n")
            start = pos
            emit(m["content"])
            for j, call in enumerate(m.get("tool_calls") or []):
                fn = call.get("function") or call
                if j or m["content"]:
                    emit("\n")
                args = arguments_json(fn.get("arguments"))
                emit(f'<tool_call>\n{{"name": "{fn["name"]}", "arguments": {args}}}\n</tool_call>')
            emit("<|im_end|>")
            if m.get("weight", 1):
                spans.append((start, pos))
            emit("\n")
        elif role == "tool":
            if prev != "tool":
                emit("<|im_start|>user")
            emit(f"\n<tool_response>\n{m['content']}\n</tool_response>")
            if not next_tool:
                emit("<|im_end|>\n")
        prev = role
    if generation_prompt and prev and prev != "assistant":
        emit("<|im_start|>assistant\n")
    return "".join(parts), spans


def encode(tokenizer: Any, record: dict[str, Any], max_length: int) -> dict[str, list[int]]:
    """{"input_ids", "labels"} for one record. Raises TooLong or NothingToLearn."""
    text, spans = render(record["messages"], record.get("tools") or [])
    if not spans:
        raise NothingToLearn("no assistant turn with weight 1")
    enc = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    ids, offsets = enc["input_ids"], enc["offset_mapping"]
    if len(ids) > max_length:
        raise TooLong(f"{len(ids)} tokens > max_length {max_length}")
    labels = [
        token if b > a and any(start <= a and b <= end for start, end in spans) else IGNORE
        for token, (a, b) in zip(ids, offsets, strict=True)
    ]
    if all(label == IGNORE for label in labels):
        raise NothingToLearn("the assistant turns produced no tokens")
    return {"input_ids": list(ids), "labels": labels}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def encode_file(tokenizer: Any, path: Path, max_length: int) -> list[dict[str, list[int]]]:
    """Every record of a dataset file. A record that is too long or has nothing to learn stops the
    whole run with its trace id: rebuild the dataset (e.g. a lower --max-tokens) rather than lose it
    silently."""
    out = []
    for n, record in enumerate(load_jsonl(path), 1):
        try:
            out.append(encode(tokenizer, record, max_length))
        except (TooLong, NothingToLearn) as e:
            trace = (record.get("metadata") or {}).get("trace_id", "?")
            raise type(e)(f"{path.name} line {n} (trace {trace}): {e}") from e
    return out


def parse_tool_calls(text: str) -> list[dict[str, Any]]:
    """The {"name", "arguments"} objects of every <tool_call> block in a model's output."""
    return [json.loads(block) for block in _TOOL_CALL.findall(text)]
