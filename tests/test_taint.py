"""Permission tiers v2, stage 2: taint (defense a) and user-named egress (e1).

Once a reads_local tool has run in a conversation, a network_egress call needs the user's approval,
unless the user typed its URL or query. Per conversation, never cleared. No network, no keys."""

import json

import pytest

from kestrel import tools
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


# --- taint only from reads that returned local data (known issue #45, rules tiers-v2.1) -------------


@pytest.fixture
def real_reads(monkeypatch, tmp_path, ran):
    """The real read_file and list_files on a sandboxed workspace, plus a stand-in search."""
    ws = tmp_path / "workspace"
    (ws / "empty").mkdir(parents=True)
    (ws / "notes.txt").write_text("the meeting moved to Thursday", encoding="utf-8")
    (tmp_path / ".env").write_text("NOT_A_REAL_KEY=1\n", encoding="utf-8")  # exists, but outside the workspace
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    reg = ToolRegistry()
    reg.tools["read_file"] = tools.registry.tools["read_file"]
    reg.tools["list_files"] = tools.registry.tools["list_files"]

    @reg.register(capabilities=EGRESS)
    def search(query: str) -> str:
        """Searches the web."""
        ran.append(("search", query))
        return f"results for {query}"

    return reg


def read_then_search(reg, tmp_path, tool, **args):
    """The model calls `tool`, then a search in the next step; returns the cards asked and the session."""
    llm = ScriptedLLM(calls(call("1", tool, **args)), calls(call("2", "search", query="tokyo weather")), say("ok"))
    approver = FakeApprover(Decision("rejected", reason="no"))
    a = agent(llm, reg, approver, tmp_path)
    a.run("What's the weather in Tokyo?")
    return [r["tool"] for r in approver.requests], a.session


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("read_file", {"path": "missing.txt"}),  # no such file
        ("read_file", {"path": "https://temperatures.com/weather/tokyo"}),  # a URL as a path (web-weather-tokyo)
        ("read_file", {"path": "Groq - Wikipedia"}),  # a page title as a path (web-what-is-groq)
        ("read_file", {"path": "../.env"}),  # refused: outside the workspace
        ("list_files", {"path": "nowhere"}),  # not a folder
    ],
)
def test_a_failed_read_does_not_taint(real_reads, ran, tmp_path, tool, args):
    cards, session = read_then_search(real_reads, tmp_path, tool, **args)
    assert cards == [] and not session.tainted and session.local_texts == []
    assert ran == [("search", "tokyo weather")]


def test_a_successful_read_taints_and_the_next_search_gets_a_card(real_reads, ran, tmp_path):
    cards, session = read_then_search(real_reads, tmp_path, "read_file", path="notes.txt")
    assert cards == ["search"] and session.taint_sources == ["read_file"] and ran == []


def test_list_files_taints_when_it_lists_something_but_not_for_an_empty_folder(real_reads, ran, tmp_path):
    """Decision: an empty listing shows no workspace content (the folder's name came from the model), so
    nothing local entered the conversation and there is nothing to leak. A listing with names does taint:
    file names are local data."""
    cards, session = read_then_search(real_reads, tmp_path, "list_files", path="empty")
    assert cards == [] and not session.tainted
    cards, session = read_then_search(real_reads, tmp_path, "list_files", path=".")
    assert cards == ["search"] and session.taint_sources == ["list_files"]


@pytest.mark.parametrize(
    ("output", "local"),
    [
        ("Error: FileNotFoundError: [Errno 2] No such file or directory", False),
        ("Error: srv__get reported a failure. Its message:\n<untrusted_data>...", False),  # MCP failure
        ('<untrusted_data source="read_file">\n\n</untrusted_data>\nnote', False),  # an empty file
        ("[]", False),  # an empty folder
        ('<untrusted_data source="read_file">\nhello\n</untrusted_data>\nnote', True),
        ('["notes.txt"]', True),
        ("Error is my favourite word", True),  # only the registry's "Error:" prefix marks a failure
    ],
)
def test_returned_local_data(output, local):
    s = Session()
    s.note_ran("read_file", frozenset({"reads_local", "reads_untrusted"}), {}, output)
    assert s.tainted is local


def test_a_note_from_the_gate_does_not_hide_a_failure(tmp_path, ran):
    """The session sees the tool's own output. An MCP tool (every capability, so reads_local) the user
    edited and approved gets "Note: the user edited this call..." in front of its result; when it failed,
    that note must not turn the error into local data."""
    reg = ToolRegistry()

    def get(key: str) -> str:
        raise RuntimeError("no such key")

    reg.register_external(
        "srv__get", get, "Gets a value.", {"type": "object", "properties": {"key": {"type": "string"}}}, server="srv"
    )

    @reg.register(capabilities=EGRESS)
    def search(query: str) -> str:
        """Searches the web."""
        ran.append(("search", query))
        return "results"

    llm = ScriptedLLM(calls(call("1", "srv__get", key="a")), calls(call("2", "search", query="q")), say("ok"))
    approver = FakeApprover(Decision("edited", args={"key": "b"}), Decision("approved"))
    a = agent(llm, reg, approver, tmp_path)
    a.run("go")
    assert [r["tool"] for r in approver.requests] == ["srv__get", "srv__get"]  # the edit, then the approval
    result = next(m["content"] for m in a.messages if m.get("role") == "tool" and m.get("tool_call_id") == "1")
    assert result.startswith("Note: the user edited this call") and "Error:" in result  # the model saw both...
    assert not a.session.tainted and ran == [("search", "q")]  # ...but the failure didn't taint


# The real tool sequences of the 2026-10-21 main-suite after-run (main-after-v1.1.json, tiers-v2 rules
# before the fix): each got a card from a read_file that had failed, and the strict user rejected it.
# Results shortened; workspace paths removed from the error messages.
REAL_RUNS = {
    "web-weather-tokyo": [
        ("web_search", {"max_results": 5, "query": "Tokyo weather today"}, True, "[{...}]"),
        ("web_search", {"max_results": 3, "query": "Tokyo weather today temperature"}, True, "[{...}]"),
        ("web_search", {"max_results": 5, "query": "Tokyo temperature today 2024-10-21"}, True, "[{...}]"),
        ("web_search", {"max_results": 3, "query": "Tokyo weather now temperature"}, True, "[{...}]"),
        (
            "web_search",
            {"max_results": 1, "query": "Tokyo weather now temperature 22°C site:temperatures.com"},
            True,
            "[{...}]",
        ),
        (
            "read_file",
            {"path": "https://temperatures.com/weather/tokyo"},
            True,
            "Error: OSError: [Errno 22] Invalid argument: '<workspace>\\https:\\temperatures.com\\weather\\tokyo'",
        ),
        ("web_search", {"max_results": 3, "query": "Tokyo weather now 72°F 22°C"}, False, ""),
    ],
    "web-what-is-groq": [
        ("web_search", {"max_results": 5, "query": "Groq AI inference company"}, True, "Error: TimeoutException"),
        ("web_search", {"max_results": 3, "query": "Groq AI inference"}, True, "Error: DDGSException: No results."),
        ("web_search", {"max_results": 5, "query": "Groq company artificial intelligence inference"}, True, "[{}]"),
        (
            "read_file",
            {"path": "Groq - Wikipedia"},
            True,
            "Error: FileNotFoundError: [Errno 2] No such file or directory: '<workspace>\\Groq - Wikipedia'",
        ),
        ("web_search", {"max_results": 1, "query": "Groq Inc Wikipedia"}, False, ""),
    ],
}


def replay(steps, old_rule=False):
    """The offline replay (scripts/tiers_v2_offline.py): every call through required_approval, in order.
    old_rule: taint from every reads_local call that ran, as the rules did before the fix."""
    session = Session()
    escalated = []
    for name, args, did_run, result in steps:
        tool = tools.registry.tools[name]
        need = required_approval(tool.capabilities, tool.external, session, args)
        if need.escalated_by:
            escalated.append((name, need.escalated_by))
        if did_run:
            if old_rule and "reads_local" in tool.capabilities:
                session.taint_sources.append(name)
            session.note_ran(name, tool.capabilities, args, result)
    return escalated


@pytest.mark.parametrize("task", sorted(REAL_RUNS))
def test_the_real_after_run_sequences_get_no_card_after_the_fix(task):
    assert replay(REAL_RUNS[task], old_rule=True) == [("web_search", ["taint:read_file"])]  # what happened
    assert replay(REAL_RUNS[task]) == []  # with the fix


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
