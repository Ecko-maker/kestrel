"""Three fake models for the safety split, built from each task's declared goals and expected
outcome. They validate the harness, not a model: if the checks are right,
- always-falls (does the user's request, then everything the attacker wants) scores 100%
  model-level attack success;
- never-falls (does only the user's request) scores 0% and passes every benign task;
- refuse-all (no tool calls, a polite refusal) scores 0% and fails every benign task;
- falls-on-repeat-2 plays always-falls on the second time it plays a task and never-falls
  otherwise, so with --repeat 3 every attack succeeds in exactly 1 of 3 repeats: it checks that
  any-repeat scoring catches a flaky attack.

A model plays one task at a time: the safety setup hook calls begin() before each run. Earlier
turns of a multi-turn prompt get a short acknowledgement; the scripted actions happen in the last.
"""

import json
from typing import Any

from kestrel.bench.safety import ANSWER, SafetyTask, fill, first_canary

MODELS = ("always-falls", "never-falls", "refuse-all", "falls-on-repeat-2")
REFUSAL = "I can't help with that."
OVERWRITE = "PLACEHOLDER OVERWRITE"


def _call(n: int, name: str, args: dict[str, Any]) -> dict[str, Any]:
    call = {"id": f"call-{n}-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    return {"role": "assistant", "content": None, "tool_calls": [call]}


def _say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


class ScriptedSafetyModel:
    supports_streaming = False

    def __init__(self, name: str):
        if name not in MODELS:
            raise ValueError(f"unknown scripted model {name!r}; choose from {', '.join(MODELS)}")
        self.name = name
        self.model = name  # what results files record as the model
        self.turns = 1
        self.queue: list[dict[str, Any]] = []
        self.played: dict[str, int] = {}  # task id -> times begun: its repeat number, for falls-on-repeat-2

    def reset(self) -> None:
        """Start counting repeats again (the CLI calls this before each scripted user)."""
        self.played.clear()

    def begin(self, st: SafetyTask) -> None:
        self.turns = len(st.task.prompts)
        self.played[st.id] = self.played.get(st.id, 0) + 1
        self.queue = self.plan(st)

    def plan(self, st: SafetyTask) -> list[dict[str, Any]]:
        if self.name == "refuse-all":
            return [_say(REFUSAL)]
        canary = first_canary(st.meta)
        calls = [(e["tool"], fill(e["args"], canary)) for e in st.meta.expect if "tool" in e]
        answer = [str(fill(e["example"], canary)) for e in st.meta.expect if "answer" in e]
        falls = self.name == "always-falls" or (self.name == "falls-on-repeat-2" and self.played.get(st.id) == 2)
        if falls:
            for goal in st.meta.goals:
                kind = goal["type"]
                if kind == "leak" and goal["via"] == ANSWER:
                    answer.append(str(fill(goal["example"], canary)))
                elif kind in ("leak", "call"):
                    calls.append((goal.get("via") or goal["tool"], fill(goal["args"], canary)))
                elif kind in ("modify", "write_outside"):
                    calls.append(("write_file", {"path": goal["path"], "content": OVERWRITE}))
        replies = [_call(i, name, args) for i, (name, args) in enumerate(calls)]
        return [*replies, _say("\n\n".join(answer) or "PLACEHOLDER ANSWER")]

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict[str, Any]:
        if sum(m.get("role") == "user" for m in messages) < self.turns:
            return _say("PLACEHOLDER ACKNOWLEDGED")
        return self.queue.pop(0) if self.queue else _say("PLACEHOLDER DONE")
