"""Permission tiers v2, stage 2: taint (defense a) and user-named egress (e1).

Once a reads_local tool has run in a conversation, a network_egress call needs the user's approval,
unless the user typed its URL or query. Per conversation, never cleared. No network, no keys."""

import json

import pytest

from kestrel.agent import Agent
from kestrel.approval import ApprovalGate, Decision
from kestrel.permissions import ALL, Session, required_approval
from kestrel.tools import ToolRegistry

EGRESS = frozenset({"network_egress", "reads_untrusted"})


class FakeApprover:
    def __init__(self, *decisions: Decision):
        self.decisions = list(decisions)
        self.requests: list[dict] = []

    def review(self, tool_name, args, preview, *, allow_session=False, notice=None):
        self.requests.append({"tool": tool_name, "args": args, "preview": preview, "allow_session": allow_session})
        return self.decisions.pop(0)


class ScriptedLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, messages, tools=None):
        return self.replies.pop(0)


def call(call_id, name, **args):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def calls(*tool_calls):
    return {"role": "assistant", "content": None, "tool_calls": list(tool_calls)}


def say(text):
    return {"role": "assistant", "content": text}


@pytest.fixture
def ran():
    return []


@pytest.fixture
def registry(ran):
    reg = ToolRegistry()

    @reg.register(capabilities={"reads_local", "reads_untrusted"})
    def read(path: str) -> str:
        """Reads a local file."""
        ran.append(("read", path))
        return f"contents of {path}"

    @reg.register(capabilities=EGRESS)
    def search(query: str) -> str:
        """Searches the web."""
        ran.append(("search", query))
        return f"results for {query}"

    @reg.register(capabilities=EGRESS)
    def fetch(url: str, raw: bool = False) -> str:
        """Fetches a page."""
        ran.append(("fetch", url))
        return f"page {url}"

    return reg


def agent(llm, registry, approver, tmp_path):
    return Agent(llm, tools=registry, gate=ApprovalGate(approver, log_path=tmp_path / "approvals.jsonl"))


# --- the pure function ----------------------------------------------------------------------------


def test_no_taint_no_card_and_reads_untrusted_does_not_taint():
    s = Session(user_texts=["hi"])
    assert required_approval(EGRESS, session=s, args={"query": "x"}).level == "safe"
    s.note_ran("search", EGRESS, {"query": "x"}, "results")  # web results are reads_untrusted only
    assert not s.tainted
    assert required_approval(EGRESS, session=s, args={"query": "y"}).level == "safe"


def test_after_a_local_read_egress_needs_approval_and_says_why():
    s = Session(user_texts=["summarize my notes"])
    s.note_ran("read_file", frozenset({"reads_local", "reads_untrusted"}), {"path": "n.txt"}, "secret")
    need = required_approval(EGRESS, session=s, args={"query": "anything"})
    assert need.level == "confirm" and need.escalated_by == ["taint:read_file"]
    assert "read_file" in need.reasons[0]
    # tools that don't send anything out are untouched
    assert required_approval(frozenset({"reads_local"}), session=s, args={"path": "x"}).level == "safe"
    assert required_approval(frozenset({"writes_local"}), session=s, args={"path": "x"}).escalated_by == []


def test_forbidden_stays_forbidden_and_a_gated_mcp_tool_is_marked_escalated():
    s = Session(taint_sources=["read_file"])
    assert required_approval(frozenset({"deletes_local", "network_egress"}), session=s, args={}).level == "forbidden"
    need = required_approval(ALL, external=True, session=s, args={"q": "x"})
    assert need.level == "confirm" and need.escalated_by == ["taint:read_file"]


@pytest.mark.parametrize(
    ("args", "named"),
    [
        ({"url": "https://example.com/page"}, True),
        ({"url": "HTTPS://Example.com/page"}, True),  # case doesn't matter
        ({"query": "best  hiking\ttrails"}, True),  # nor runs of whitespace
        ({"url": "https://example.com/page?d=secret"}, False),  # something added
        ({"query": "hiking trails near me"}, False),
        ({"url": "https://example.com/page", "raw": True}, True),  # only text arguments count
        ({"max_results": 5}, False),  # no text argument: not named by the user
        ({"query": "   "}, False),
    ],
)
def test_user_named_egress_needs_every_text_argument_verbatim(args, named):
    s = Session(user_texts=["Open https://example.com/page please", "then search best hiking trails"])
    s.taint_sources.append("read_file")
    assert s.user_named(args) is named
    assert required_approval(EGRESS, session=s, args=args).level == ("safe" if named else "confirm")


# --- through the agent loop -----------------------------------------------------------------------


def test_read_then_search_gets_a_card_and_the_strict_user_stops_it(registry, ran, tmp_path):
    llm = ScriptedLLM(
        calls(call("1", "read", path="notes.txt")),
        calls(call("2", "search", query="contents of notes.txt")),
        say("done"),
    )
    approver = FakeApprover(Decision("rejected", reason="no"))
    agent(llm, registry, approver, tmp_path).run("What's in my notes?")
    assert [r["tool"] for r in approver.requests] == ["search"]
    assert approver.requests[0]["allow_session"] is False  # an escalated card is never for the session
    assert ran == [("read", "notes.txt")]


def test_search_then_search_needs_no_card(registry, ran, tmp_path):
    llm = ScriptedLLM(calls(call("1", "search", query="a")), calls(call("2", "search", query="b")), say("ok"))
    approver = FakeApprover()
    agent(llm, registry, approver, tmp_path).run("look up a")
    assert approver.requests == [] and ran == [("search", "a"), ("search", "b")]


def test_a_url_the_user_typed_needs_no_card_even_after_taint(registry, ran, tmp_path):
    llm = ScriptedLLM(
        calls(call("1", "read", path="notes.txt")),
        calls(call("2", "fetch", url="https://docs.example/guide")),
        say("ok"),
    )
    approver = FakeApprover()
    agent(llm, registry, approver, tmp_path).run("Compare notes.txt with https://docs.example/guide")
    assert approver.requests == [] and ran[-1] == ("fetch", "https://docs.example/guide")


def test_a_read_and_a_search_in_the_same_turn_do_not_escalate(registry, ran, tmp_path):
    """The search was written before the model saw the file, so it can't carry the file's data."""
    llm = ScriptedLLM(calls(call("1", "read", path="n.txt"), call("2", "search", query="q")), say("ok"))
    approver = FakeApprover()
    a = agent(llm, registry, approver, tmp_path)
    a.run("go")
    assert approver.requests == [] and a.session.taint_sources == ["read"]


def test_taint_lasts_the_whole_conversation_and_a_new_one_starts_clean(registry, ran, tmp_path):
    llm = ScriptedLLM(
        calls(call("1", "read", path="n.txt")),
        say("read it"),
        calls(call("2", "search", query="weather")),
        say("ok"),
        calls(call("3", "search", query="weather")),
        say("ok"),
    )
    approver = FakeApprover(Decision("approved"))
    first = agent(llm, registry, approver, tmp_path)
    first.run("read n.txt")
    first.messages[1:] = first.messages[-1:]  # even if old turns are trimmed away...
    first.run("what's it like outside?")
    assert [r["tool"] for r in approver.requests] == ["search"]  # ...the taint stays

    second = agent(llm, registry, approver, tmp_path)  # a new conversation (new console tab, new task)
    second.run("what's it like outside?")
    assert len(approver.requests) == 1 and not second.session.tainted


def test_an_earlier_session_approval_does_not_cover_an_escalated_call(tmp_path):
    reg = ToolRegistry()

    @reg.register(capabilities={"reads_local"})
    def read(path: str) -> str:
        """Reads."""
        return "data"

    reg.register_external("srv__ask", lambda **a: "ok", "Asks a remote server.", {"type": "object"}, server="srv")
    llm = ScriptedLLM(
        calls(call("1", "srv__ask")),
        calls(call("2", "read", path="x")),
        calls(call("3", "srv__ask")),
        say("ok"),
    )
    approver = FakeApprover(Decision("approved", for_session=True), Decision("rejected"))
    agent(llm, reg, approver, tmp_path).run("go")
    assert [r["allow_session"] for r in approver.requests] == [True, False]  # asked again after the read
