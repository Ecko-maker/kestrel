"""Replay stored tool logs offline: what tiers v2 would have done with calls a real model already
made. No model is called.

A results file keeps each task's `tool_log` (runner._tool_log): one line per call,
    - tool(args JSON) [ran] -> result
    - tool(args JSON) [not run (rejected)] -> message
with results cut at 2,000 characters. Turns aren't recorded, so a replay treats every call as its
own step (two calls the model made in one turn are replayed as if the second came after the
first): that can only add cards, never hide one.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

_HEAD = re.compile(r"^- (?P<tool>[^(\s]+)\(")
_STATE = re.compile(r"\) \[(?P<state>ran|not run \([^)]*\))\] -> ")
_WRAP_OPEN = re.compile(r'^<untrusted_data source="[^"]*">\n')


@dataclass(frozen=True)
class Step:
    tool: str
    args: dict[str, Any]
    ran: bool
    state: str  # "ran" or "not run (<decision>)"
    result: str  # without the <untrusted_data> wrapper; may be cut


def unwrap(result: str) -> str:
    """The data inside an <untrusted_data> wrapper, even when the closing tag was cut off."""
    if not _WRAP_OPEN.match(result):
        return result
    body = _WRAP_OPEN.sub("", result, count=1)
    return body.split("\n</untrusted_data>", 1)[0]


def parse_line(line: str) -> Step | None:
    head = _HEAD.match(line)
    if head is None:
        return None
    rest = line[head.end() :]
    for m in _STATE.finditer(rest):  # the arguments are JSON: the first split that parses is the one
        try:
            args = json.loads(rest[: m.start()] or "{}")
        except json.JSONDecodeError:
            continue
        state = m.group("state")
        return Step(
            head.group("tool"), args if isinstance(args, dict) else {}, state == "ran", state, unwrap(rest[m.end() :])
        )
    return None


def parse_tool_log(entries: list[str]) -> list[Step]:
    """Every call in a tool_log (one entry per call; a result may contain newlines)."""
    return [step for entry in entries if (step := parse_line(entry))]
