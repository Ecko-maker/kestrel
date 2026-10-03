import json

from kestrel.agent import Agent
from kestrel.tools import ToolRegistry


def tool_call(call_id: str, name: str, **args) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)}}],
    }


class FakeLLM:
    """Plays back scripted replies and records what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return self.replies.pop(0) if self.replies else tool_call("again", "add", a=1, b=1)


def make_registry():
    reg = ToolRegistry()

    @reg.register
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    return reg


def test_tool_runs_and_result_goes_back_to_model():
    llm = FakeLLM([tool_call("c1", "add", a=2, b=3), {"role": "assistant", "content": "It's 5."}])
    steps = []
    agent = Agent(llm, tools=make_registry(), on_tool_step=lambda *s: steps.append(s))

    result = agent.run("what is 2+3?")
    assert (result.text, result.stop_reason, result.steps) == ("It's 5.", "answered", 2)
    assert steps == [("add", '{"a": 2, "b": 3}', "5")]
    assert llm.calls[0]["tools"][0]["function"]["name"] == "add"

    second_request = llm.calls[1]["messages"]
    assert second_request[-1] == {"role": "tool", "tool_call_id": "c1", "content": "5"}
    assert second_request[-2]["tool_calls"][0]["id"] == "c1"


def test_plain_answer_needs_no_tools():
    llm = FakeLLM([{"role": "assistant", "content": "Hi!"}])
    assert Agent(llm, tools=make_registry()).run("hello").text == "Hi!"
    assert len(llm.calls) == 1


def test_max_steps_stops_endless_tool_calls():
    llm = FakeLLM([])  # every reply is another tool call
    agent = Agent(llm, tools=make_registry(), max_steps=3)
    result = agent.run("loop forever")
    assert result.stop_reason == "max_steps" and result.steps == 3
    assert "limit of 3 steps" in result.text and "add x3" in result.text
    assert len(llm.calls) == 3


def test_tool_error_is_passed_back_not_raised():
    llm = FakeLLM([tool_call("c1", "missing_tool"), {"role": "assistant", "content": "Sorry."}])
    assert Agent(llm, tools=make_registry()).run("x").text == "Sorry."
    assert llm.calls[1]["messages"][-1]["content"].startswith("Error: unknown tool")


def test_history_kept_across_turns():
    llm = FakeLLM([{"role": "assistant", "content": "A"}, {"role": "assistant", "content": "B"}])
    agent = Agent(llm, tools=make_registry())
    agent.run("first")
    agent.run("second")
    contents = [m["content"] for m in llm.calls[1]["messages"]]
    assert contents[1:] == ["first", "A", "second"]
