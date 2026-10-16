"""Turn `kestrel distill build` JSONL into token ids and labels for the student's own chat template.

Each record is {"messages", "tools", "metadata"} in the chat format of `kestrel export`: assistant
messages carry a "weight" (1 = learn this turn, 0 = context only), tool calls are OpenAI-style
{"id", "type", "function": {"name", "arguments": <JSON string>}}, and tool results are "tool" messages.
The tokenizer's chat template renders all of it in the student's native format (for Qwen3:
<tool_call> JSON blocks and <tool_response> turns), so nothing here hard-codes a model's syntax.

Labels: every token is -100 (ignored by the loss) except the tokens of assistant turns with weight 1,
from the first token after the "assistant" header through the turn's end-of-turn token, so the student
also learns when to stop. An example longer than max_length raises TooLong: a truncated trajectory
(a tool call cut in half, an answer without its end) would teach a broken turn.

This file runs on Kaggle, where Kestrel itself can't be installed (it needs Python 3.14): it uses
only the standard library and a transformers tokenizer, and keeps to Python 3.11 syntax.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

IGNORE = -100
_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


class TooLong(ValueError):
    """An example has more tokens than max_length. It is never truncated."""


class NothingToLearn(ValueError):
    """An example has no assistant turn with weight 1."""


def _for_template(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages as the chat template expects them: no Kestrel-only keys, content never None."""
    out = []
    for m in messages:
        m = {k: v for k, v in m.items() if k != "weight"}
        if m.get("content") is None:
            m["content"] = ""
        out.append(m)
    return out


def render(
    tokenizer: Any, messages: list[dict[str, Any]], tools: list[dict] | None, *, generation_prompt: bool = False
) -> str:
    return tokenizer.apply_chat_template(
        _for_template(messages), tools=tools or None, tokenize=False, add_generation_prompt=generation_prompt
    )


def trained_spans(
    tokenizer: Any, messages: list[dict[str, Any]], tools: list[dict] | None
) -> tuple[str, list[tuple[int, int]]]:
    """(full rendered text, character spans of the assistant turns to learn).

    A span starts where the template's assistant header ends (rendering the earlier messages with a
    generation prompt) and ends after the turn's end-of-turn token (rendering through this message, minus
    the newline that follows). Both renderings must be prefixes of the full text, or the template isn't
    one this method can split; that raises instead of guessing."""
    full = render(tokenizer, messages, tools)
    spans = []
    for i, m in enumerate(messages):
        if m.get("role") != "assistant" or m.get("weight", 1) == 0:
            continue
        before = render(tokenizer, messages[:i], tools, generation_prompt=True)
        through = render(tokenizer, messages[: i + 1], tools).rstrip("\n")
        if not (full.startswith(before) and full.startswith(through) and len(through) > len(before)):
            raise ValueError(f"the chat template doesn't render message {i} as a prefix; can't mark its tokens")
        spans.append((len(before), len(through)))
    return full, spans


def encode(tokenizer: Any, record: dict[str, Any], max_length: int) -> dict[str, list[int]]:
    """{"input_ids", "labels"} for one record. Raises TooLong or NothingToLearn."""
    messages, tools = record["messages"], record.get("tools") or []
    full, spans = trained_spans(tokenizer, messages, tools)
    if not spans:
        raise NothingToLearn("no assistant turn with weight 1")
    enc = tokenizer(full, add_special_tokens=False, return_offsets_mapping=True)
    ids, offsets = enc["input_ids"], enc["offset_mapping"]
    if len(ids) > max_length:
        raise TooLong(f"{len(ids)} tokens > max_length {max_length}")
    labels = [
        token if any(start <= a and b <= end for start, end in spans) and b > a else IGNORE
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
    """The {"name", "arguments"} objects of every <tool_call> block in a rendered assistant turn."""
    return [json.loads(block) for block in _TOOL_CALL.findall(text)]
