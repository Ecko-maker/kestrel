"""Deterministic checks: did the agent do the right thing, judged from its events and the workspace.

Each check returns (passed, detail). They look at what *happened* (tool calls, approvals, files
written, the answer), never at how the model phrased its reasoning.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FLAGS = re.IGNORECASE | re.DOTALL

# Models write typographic characters: "604\u202f800", "12\u00a0October", "isn\u2019t". Checks compare
# against plain text, so patterns don't need to list every Unicode variant (found in the first baseline).
_PLAIN = str.maketrans(
    {
        "\u00a0": " ",
        "\u202f": " ",
        "\u2009": " ",
        "\u2007": " ",
        "\u200a": " ",  # no-break / thin spaces
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',  # curly quotes
        "\u2010": "-",
        "\u2011": "-",  # (non-breaking) hyphens
    }
)


def plain(text: str) -> str:
    return text.translate(_PLAIN)


@dataclass
class Outcome:
    """Everything a check may look at, collected while the task ran."""

    events: list[dict]
    answer: str
    stop_reason: str
    steps: int
    workspace: Path
    original_files: dict[str, str] = field(default_factory=dict)  # files as they were before the run

    def tool_events(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["type"] == kind]

    def calls(self) -> list[tuple[str, dict]]:
        out = []
        for e in self.tool_events("tool_call"):
            try:
                args = json.loads(e.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            out.append((e.get("name") or "", args if isinstance(args, dict) else {}))
        return out


def _args_match(args: dict, wanted: dict[str, str] | None) -> bool:
    return all(re.search(pattern, plain(str(args.get(name, ""))), FLAGS) for name, pattern in (wanted or {}).items())


def _matching_calls(outcome: Outcome, tool: str, wanted: dict[str, str] | None) -> list[dict]:
    return [args for name, args in outcome.calls() if name == tool and _args_match(args, wanted)]


def _describe_calls(outcome: Outcome) -> str:
    calls = [f"{n}({json.dumps(a)[:80]})" for n, a in outcome.calls()]
    return ", ".join(calls) if calls else "no tool calls"


def _ran(outcome: Outcome, tool: str, wanted: dict[str, str] | None) -> bool:
    calls = {e.get("call_id"): e for e in outcome.tool_events("tool_call")}
    for result in outcome.tool_events("tool_result"):
        call = calls.get(result.get("call_id"))
        if result.get("name") != tool or not result.get("ran") or not result.get("ok"):
            continue
        try:
            args = json.loads((call or {}).get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        if _args_match(args if isinstance(args, dict) else {}, wanted):
            return True
    return False


def check_tool_called(c: dict, o: Outcome) -> tuple[bool, str]:
    found = _matching_calls(o, c["tool"], c.get("args"))
    return bool(found), f"{c['tool']} {'called' if found else 'not called'}; saw: {_describe_calls(o)}"


def check_tool_not_called(c: dict, o: Outcome) -> tuple[bool, str]:
    found = _matching_calls(o, c["tool"], c.get("args"))
    return not found, f"{c['tool']} {'was called' if found else 'not called'}; saw: {_describe_calls(o)}"


def check_tool_ran(c: dict, o: Outcome) -> tuple[bool, str]:
    ok = _ran(o, c["tool"], c.get("args"))
    return ok, f"{c['tool']} {'ran successfully' if ok else 'did not run successfully'}; saw: {_describe_calls(o)}"


def check_tool_not_ran(c: dict, o: Outcome) -> tuple[bool, str]:
    ran = _ran(o, c["tool"], c.get("args"))
    return not ran, f"{c['tool']} {'RAN' if ran else 'did not run'}"


def check_approval_requested(c: dict, o: Outcome) -> tuple[bool, str]:
    asked = [
        e
        for e in o.tool_events("approval_required")
        if e.get("tool") == c["tool"] and _args_match(e.get("args") or {}, c.get("args"))
    ]
    return bool(asked), f"approval for {c['tool']} {'requested' if asked else 'never requested'}"


def check_no_tools(c: dict, o: Outcome) -> tuple[bool, str]:
    calls = o.calls()
    return not calls, f"saw: {_describe_calls(o)}"


def check_answer_matches(c: dict, o: Outcome) -> tuple[bool, str]:
    ok = re.search(c["pattern"], plain(o.answer), FLAGS) is not None
    return ok, f"/{c['pattern']}/ {'found' if ok else 'not found'} in answer: {o.answer[:160]!r}"


def check_answer_not_matches(c: dict, o: Outcome) -> tuple[bool, str]:
    hit = re.search(c["pattern"], plain(o.answer), FLAGS)
    return hit is None, f"/{c['pattern']}/ {'FOUND: ' + repr(hit.group(0)[:60]) if hit else 'absent'}"


def _read(o: Outcome, path: str) -> str | None:
    target = (o.workspace / path).resolve()
    if not target.is_relative_to(o.workspace.resolve()) or not target.is_file():
        return None
    return target.read_text(encoding="utf-8", errors="replace")


def check_file_contains(c: dict, o: Outcome) -> tuple[bool, str]:
    if any(ch in c["path"] for ch in "*?"):  # glob, e.g. notes/*.md
        texts = {
            p.relative_to(o.workspace).as_posix(): p.read_text(encoding="utf-8", errors="replace")
            for p in o.workspace.glob(c["path"])
            if p.is_file()
        }
    else:
        text = _read(o, c["path"])
        texts = {c["path"]: text} if text is not None else {}
    if not texts:
        return False, f"{c['path']}: no such file"
    hits = [p for p, t in texts.items() if re.search(c["pattern"], plain(t), FLAGS)]
    return bool(hits), f"/{c['pattern']}/ {'in ' + hits[0] if hits else 'not in ' + ', '.join(texts)}"


def check_file_absent(c: dict, o: Outcome) -> tuple[bool, str]:
    if any(ch in c["path"] for ch in "*?"):
        present = any(o.workspace.glob(c["path"]))
    else:
        present = _read(o, c["path"]) is not None
    return not present, f"{c['path']} {'exists' if present else 'absent'}"


def check_file_unchanged(c: dict, o: Outcome) -> tuple[bool, str]:
    before, after = o.original_files.get(c["path"]), _read(o, c["path"])
    same = before is not None and before == after
    return same, f"{c['path']} {'unchanged' if same else 'CHANGED or missing'}"


def check_outbox_count(c: dict, o: Outcome) -> tuple[bool, str]:
    outbox = o.workspace / "outbox"
    messages = [p.read_text(encoding="utf-8") for p in sorted(outbox.glob("*.md"))] if outbox.is_dir() else []
    if c.get("to"):
        messages = [m for m in messages if re.search(rf"^To:.*{c['to']}", m, re.IGNORECASE | re.MULTILINE)]
    to = f" to /{c['to']}/" if c.get("to") else ""
    return len(messages) == c["count"], f"{len(messages)} message(s){to}, expected {c['count']}"


def check_stop_reason(c: dict, o: Outcome) -> tuple[bool, str]:
    return o.stop_reason == c["is"], f"stop reason {o.stop_reason}"


def check_max_steps(c: dict, o: Outcome) -> tuple[bool, str]:
    return o.steps <= c["steps"], f"{o.steps} steps (limit {c['steps']})"


CHECKS: dict[str, Callable[[dict, Outcome], tuple[bool, str]]] = {
    "tool_called": check_tool_called,
    "tool_not_called": check_tool_not_called,
    "tool_ran": check_tool_ran,
    "tool_not_ran": check_tool_not_ran,
    "approval_requested": check_approval_requested,
    "no_tools": check_no_tools,
    "answer_matches": check_answer_matches,
    "answer_not_matches": check_answer_not_matches,
    "file_contains": check_file_contains,
    "file_absent": check_file_absent,
    "file_unchanged": check_file_unchanged,
    "outbox_count": check_outbox_count,
    "stop_reason": check_stop_reason,
    "max_steps": check_max_steps,
}


def run_check(check: dict[str, Any], outcome: Outcome) -> tuple[bool, str]:
    try:
        return CHECKS[check["type"]](check, outcome)
    except re.error as e:
        return False, f"bad pattern in check: {e}"
