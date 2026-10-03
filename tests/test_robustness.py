"""Bad model output, tool limits, parallel tools, context trimming, and clean endings."""

import json
import time

import pytest

from kestrel.agent import Agent, estimate_tokens
from kestrel.llm import LLMError
from kestrel.tools import ToolRegistry


def make_registry(**kwargs) -> ToolRegistry:
    reg = ToolRegistry(**kwargs)

    @reg.register
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @reg.register
    def scale(x: float, label: str = "") -> str:
        """Scale a number."""
        return f"{label}{x * 2}"

    @reg.register
    def nap(seconds: float, tag: str) -> str:
        """Sleep, then echo the tag."""
        time.sleep(seconds)
        return tag

    @reg.register
    def big() -> str:
        """A huge result."""
        return "x" * 50_000

    return reg


# --- bad model output ---------------------------------------------------------

@pytest.mark.parametrize("arguments, expected", [
    ('{"a": 1, "b": ', "not valid JSON"),
    ("[1, 2]", "must be a JSON object"),
    ('{"a": 1}', "missing required argument(s) ['b']"),
    ('{"a": 1, "b": 2, "c": 3}', "unexpected argument(s) ['c']"),
    ('{"a": "1", "b": 2}', "'a' must be a integer, got str"),
    ('{"a": true, "b": 2}', "'a' must be a integer, got bool"),
    ('{"a": 1.5, "b": 2}', "'a' must be a integer, got float"),
])
def test_bad_arguments_become_readable_errors(arguments, expected):
    result = make_registry().execute("add", arguments)
    assert result.startswith("Error:") and expected in result


def test_valid_argument_edge_cases():
    reg = make_registry()
    assert reg.execute("scale", '{"x": 2}') == "4"  # int is fine where float is expected
    assert reg.execute("add", json.dumps(json.dumps({"a": 1, "b": 2}))) == "3"  # double-encoded JSON
    assert reg.execute("big", "") == reg.execute("big", None) == reg.execute("big", "{}")  # no-arg calls


# --- tool limits --------------------------------------------------------------

def test_slow_tool_times_out():
    reg = make_registry(timeout=0.2)
    start = time.perf_counter()
    result = reg.execute("nap", {"seconds": 2, "tag": "late"})
    assert result.startswith("Error: TimeoutError") and time.perf_counter() - start < 1


def test_long_results_are_truncated_with_a_note():
    result = make_registry(max_result_chars=1000).execute("big", {})
    assert result.startswith("x" * 1000)
    assert "[truncated: showing 1,000 of 50,000 characters]" in result
    assert len(result) < 1100


# --- agent behaviour ----------------------------------------------------------

def calls_reply(*calls) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
        for i, (n, a) in enumerate(calls)
    ]}


class ScriptedLLM:
    def __init__(self, replies, provider="fake"):
        self.replies = list(replies)
        self.requests = []
        self.last_provider = provider

    def chat(self, messages, tools=None):
        self.requests.append([dict(m) for m in messages])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_parallel_tool_calls_run_concurrently_and_keep_order():
    llm = ScriptedLLM([
        calls_reply(("nap", {"seconds": 0.4, "tag": "first"}), ("nap", {"seconds": 0.1, "tag": "second"}),
                    ("nap", {"seconds": 0.4, "tag": "third"})),
        {"role": "assistant", "content": "done"},
    ])
    seen = []
    agent = Agent(llm, tools=make_registry(), on_tool_step=lambda n, a, r: seen.append(r))
    start = time.perf_counter()
    agent.run("go")
    elapsed = time.perf_counter() - start

    assert elapsed < 0.8  # sequential would take 0.9s
    tool_msgs = [m for m in llm.requests[1] if m["role"] == "tool"]
    assert [(m["tool_call_id"], m["content"]) for m in tool_msgs] == [("c0", "first"), ("c1", "second"), ("c2", "third")]
    assert seen == ["first", "second", "third"]


def test_model_can_recover_from_a_bad_call():
    llm = ScriptedLLM([
        calls_reply(("add", {"a": "two", "b": 3})),
        calls_reply(("add", {"a": 2, "b": 3})),
        {"role": "assistant", "content": "5"},
    ])
    result = Agent(llm, tools=make_registry()).run("2+3")
    assert result.text == "5" and result.steps == 3
    assert llm.requests[1][-1]["content"].startswith("Error: invalid arguments for add")


def test_provider_errors_end_with_error_reason_and_roll_back_history():
    llm = ScriptedLLM([calls_reply(("add", {"a": 1, "b": 1})), LLMError("all providers", "gemini: 429 | groq: 503")])
    agent = Agent(llm, tools=make_registry())
    result = agent.run("hi")
    assert result.stop_reason == "error" and "gemini: 429" in result.text
    assert agent.messages == [agent.messages[0]]  # back to just the system prompt


def test_unexpected_exceptions_do_not_crash():
    llm = ScriptedLLM([{"role": "assistant", "content": None, "tool_calls": "garbage"}])
    result = Agent(llm, tools=make_registry()).run("hi")
    assert result.stop_reason == "error"


def test_result_reports_providers_used():
    llm = ScriptedLLM([{"role": "assistant", "content": "hey"}], provider="groq")
    result = Agent(llm, tools=make_registry()).run("hi")
    assert (result.stop_reason, result.providers, result.steps) == ("answered", ["groq"], 1)


# --- context window -----------------------------------------------------------

def test_trim_drops_oldest_whole_turns_and_keeps_system_prompt():
    agent = Agent(ScriptedLLM([]), tools=make_registry(), max_context_tokens=400)
    padding = "y" * 600  # ~150 tokens per message
    for turn in range(4):
        agent.messages += [
            {"role": "user", "content": f"q{turn} {padding}"},
            calls_reply(("add", {"a": 1, "b": 1})) | {"turn": turn},
            {"role": "tool", "tool_call_id": "c0", "content": "2"},
            {"role": "assistant", "content": f"a{turn}"},
        ]
    agent.trim_history()

    assert agent.messages[0]["role"] == "system"
    assert agent.messages[1]["role"] == "user"  # history starts at a turn boundary
    assert agent.messages[-1]["content"] == "a3"  # newest turn kept
    assert estimate_tokens(agent.messages) <= 400
    for i, m in enumerate(agent.messages):  # every tool call still has its result right after it
        if m.get("tool_calls"):
            assert agent.messages[i + 1]["role"] == "tool"


def test_trim_never_drops_the_current_turn_even_if_too_big():
    agent = Agent(ScriptedLLM([]), tools=make_registry(), max_context_tokens=10)
    agent.messages.append({"role": "user", "content": "z" * 1000})
    agent.trim_history()
    assert len(agent.messages) == 2


def test_agent_trims_before_calling_model():
    llm = ScriptedLLM([{"role": "assistant", "content": "a"}] * 3)
    agent = Agent(llm, tools=make_registry(), max_context_tokens=300)
    for _ in range(3):
        agent.run("w" * 500)
    assert sum(m["role"] == "user" for m in llm.requests[-1]) == 1
