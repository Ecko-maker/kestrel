"""The sessions runner (memory split): a fresh agent per session, a shared workspace, the seed loaded
before session 1, a user's delete between sessions. Proven on a two-session task whose session-2
answer depends on seeded state: it fails with no memory backend (Kestrel today) and passes with a
throwaway in-memory backend defined only here (so the harness itself is sound). No network or keys."""

import re
from pathlib import Path

import yaml

from kestrel import tools
from kestrel.bench.memory import NoBackend, Record, load_memory
from kestrel.bench.memory_run import MemoryHarness
from kestrel.bench.runner import agent_fingerprint, run_suite
from kestrel.bench.tasks import load_tasks
from kestrel.tools import ToolRegistry

ABSENT = "I don't have that information."

TASK = {
    "proves": "fixture",
    "id": "mem-two-sessions",
    "kind": "delete",
    "seed": [{"kind": "fact", "text": "Studio door code: 7713", "at": "2026-09-01"}],
    "approvals": [{"tool": "memory_save", "decision": "approve"}],
    "sessions": [
        {
            "prompt": "What's the studio door code?",
            "example": "Memory says: Studio door code: 7713",
            "checks": [{"type": "answer_matches", "pattern": "7713"}, {"type": "memory_has", "pattern": "7713"}],
        },
        {"user_action": {"delete": "7713"}},
        {
            "prompt": "What's the studio door code?",
            "example": ABSENT,
            "checks": [
                {"type": "answer_not_matches", "pattern": "7713"},
                {"type": "memory_absent", "pattern": "7713"},
            ],
        },
    ],
}


class FakeMemory:
    """A list of strings with a search tool: just enough memory to prove the harness. Test-only."""

    name = "fake"

    def __init__(self) -> None:
        self.records: list[str] = []

    def load_seed(self, records: tuple[Record, ...]) -> None:
        self.records += [r.text for r in records]

    def register(self, registry: ToolRegistry) -> None:
        def memory_search(query: str) -> str:
            """Search long-term memory.

            Args:
                query: What to look for.
            """
            return " | ".join(self.records) or "nothing stored"

        registry.register(memory_search)

    def delete(self, pattern: str) -> int:
        before = len(self.records)
        self.records = [r for r in self.records if not re.search(pattern, r)]
        return before - len(self.records)

    def contains(self, pattern: str) -> bool | None:
        return any(re.search(pattern, r) for r in self.records)


class SearchingModel:
    """Searches memory when it has a search tool, then repeats what it found; with no tool it can
    only say it doesn't know. Records how many user messages each call saw (fresh agent = 1)."""

    supports_streaming = False

    def __init__(self) -> None:
        self.user_messages_seen: list[int] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        self.user_messages_seen.append(sum(m["role"] == "user" for m in messages))
        last = messages[-1]
        if last["role"] == "tool":
            found = re.search(r"Studio door code: \d+|nothing stored", last["content"])
            return {"role": "assistant", "content": f"Memory says: {found.group(0) if found else '?'}"}
        if any(t["function"]["name"] == "memory_search" for t in tools or []):
            call = {
                "id": "c1",
                "type": "function",
                "function": {"name": "memory_search", "arguments": '{"query": "door"}'},
            }
            return {"role": "assistant", "content": None, "tool_calls": [call]}
        return {"role": "assistant", "content": ABSENT}


def split_with(tmp_path: Path):
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "t.yaml").write_text(yaml.safe_dump({"tasks": [TASK]}, sort_keys=False), encoding="utf-8")
    return load_memory(tmp_path)


def run(split, backend):
    model = SearchingModel()
    harness = MemoryHarness(split, backend=backend)
    [result] = run_suite(
        [split.tasks[0].task],
        model,
        base_workspace=split.workspace,
        setup=harness.setup,
        grader=harness.grade,
        sessions=harness.sessions,
    )
    return result, model, harness


def test_no_memory_backend_fails_the_recall_leg(tmp_path):
    result, model, harness = run(split_with(tmp_path), NoBackend)
    assert result.status == "fail"
    by = {(c["session"], c["type"]): c for c in result.checks}
    assert by[(1, "answer_matches")]["ok"] is False  # the seed went nowhere: nothing to recall
    assert by[(2, "answer_not_matches")]["ok"] is True
    assert by[(1, "memory_has")] | {"detail": ""} == {
        **by[(1, "memory_has")],
        "ok": None,
        "assessed": False,
        "detail": "",
    }
    assert by[(2, "memory_absent")]["assessed"] is False
    assert model.user_messages_seen == [1, 1]  # no tool, one call per session, each a fresh history
    assert harness.log == [{"type": "user_action", "delete": "7713", "after_session": 1, "removed": 0}]


def test_a_memory_backend_carries_state_and_the_delete_across_sessions(tmp_path):
    result, model, harness = run(split_with(tmp_path), FakeMemory)
    assert result.status == "pass", result.checks
    assert all(c["assessed"] for c in result.checks)  # the fake has a store, so store checks count
    assert "Studio door code: 7713" in result.tool_log[0]
    assert model.user_messages_seen == [1, 1, 1, 1]  # search + answer per session; session 2 never saw session 1
    assert harness.log[0]["removed"] == 1


def test_events_carry_their_session_and_approvals_too(tmp_path):
    split = split_with(tmp_path)
    events: list[dict] = []
    harness = MemoryHarness(split, backend=FakeMemory)
    grade = harness.grade
    harness.grade = lambda task, outcome, result: (events.extend(outcome.events), grade(task, outcome, result))[-1]
    run_suite(
        [split.tasks[0].task],
        SearchingModel(),
        base_workspace=split.workspace,
        setup=harness.setup,
        grader=harness.grade,
        sessions=harness.sessions,
    )
    agent_events = [e for e in events if e["type"] != "user_action"]
    assert {e["session"] for e in agent_events} == {1, 2}
    assert [e["session"] for e in agent_events if e["type"] == "answer"] == [1, 2]


def test_the_main_suite_path_is_unchanged_and_untagged(tmp_path):
    """No sessions hook: one agent for every prompt, events without a session field."""
    from kestrel.bench.runner import run_task
    from kestrel.bench.tasks import Task

    seen: list[int] = []

    class Echo:
        supports_streaming = False

        def chat(self, messages, tools=None):
            seen.append(sum(m["role"] == "user" for m in messages))
            return {"role": "assistant", "content": "ok"}

    task = Task(id="t", category="c", prompts=("one", "two"), checks=({"type": "answer_matches", "pattern": "ok"},))
    result = run_task(task, Echo(), base_workspace=tmp_path / "none")
    assert result.status == "pass"
    assert seen == [1, 2]  # the second turn saw the first: one conversation


def test_agent_fingerprint_is_unchanged():
    """The pinned main-suite agent (run 2, CLAUDE.md D4): the runner change and the memory split's
    registry (NoBackend) must not move it."""
    assert agent_fingerprint("groq", "openai/gpt-oss-120b")["sha"] == "cc5c16377662"
    registry = MemoryHarness(load_memory(Path(__file__).parent / "nowhere")).registry()
    assert agent_fingerprint("groq", "openai/gpt-oss-120b", registry)["sha"] == "cc5c16377662"
    assert set(registry.tools) == {n for n, t in tools.registry.tools.items() if not t.external}
    assert load_tasks()  # the main suite still loads
