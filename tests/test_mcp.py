"""MCP: namespacing, tiers for external tools, crashes, config, and Kestrel's own server."""

import json
import sys
from pathlib import Path

import pytest
from mcp import StdioServerParameters
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from kestrel import tools
from kestrel.agent import Agent
from kestrel.approval import ApprovalGate, Decision
from kestrel.mcp_client import MCPManager, load_config, tool_name
from kestrel.mcp_server import EXPOSED, build_server
from kestrel.tools import ExternalToolError, ToolRegistry
from kestrel.tracing import Tracer

CRASHY = Path(__file__).with_name("crashing_mcp_server.py")


def make_test_server() -> MCPServer:
    server = MCPServer("test")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
    def echo(text: str) -> str:
        """Echo text back. (Claims to be read-only.)"""
        return f"echo: {text}"

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @server.tool()
    def fail() -> str:
        """Always raises."""
        raise ValueError("nope")

    return server


@pytest.fixture
def manager():
    problems = []
    m = MCPManager({"test-srv": make_test_server()}, on_problem=lambda s, msg: problems.append((s, msg)))
    m.start(timeout=30)
    m.problems = problems
    yield m
    m.close()


@pytest.fixture
def registry(manager):
    reg = ToolRegistry()
    manager.register_tools(reg, safe_tools={"test-srv__add"})
    return reg


class ScriptedLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def chat(self, messages, tools=None):
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        return self.replies.pop(0)


def calls(*items):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(items)
        ],
    }


class RecordingApprover:
    def __init__(self, *decisions):
        self.decisions, self.previews = list(decisions), []

    def review(self, tool_name, args, preview, *, allow_session=False, notice=None):
        self.previews.append(preview)
        return self.decisions.pop(0)


# --- client ---------------------------------------------------------------------


def test_tool_names_are_namespaced(registry):
    assert set(registry.tools) == {"test-srv__echo", "test-srv__add", "test-srv__fail"}
    schema = registry.tools["test-srv__add"].schema["function"]
    assert schema["name"] == "test-srv__add" and "MCP server 'test-srv'" in schema["description"]
    assert schema["parameters"]["required"] == ["a", "b"]
    assert tool_name("my server", "do.thing!") == "my_server__do_thing_"
    assert len(tool_name("s" * 50, "t" * 50)) == 64


def test_external_tools_default_to_confirm_and_allowlist_makes_safe(registry):
    assert registry.tools["test-srv__add"].risk == "safe"  # in safe_tools
    assert registry.tools["test-srv__fail"].risk == "confirm"
    assert all(t.external and t.server == "test-srv" and t.untrusted_output for t in registry.tools.values())


def test_annotations_never_lower_the_tier(registry):
    echo = registry.tools["test-srv__echo"]
    assert echo.annotations["readOnlyHint"] is True  # the server claims read-only...
    assert echo.risk == "confirm"  # ...but it still needs approval
    assert "needs the user's approval" in registry.execute("test-srv__echo", {"text": "hi"})
    preview = echo.preview({"text": "hi"})
    assert "readOnlyHint=True" in preview and "unverified" in preview


def test_calls_work_and_results_are_untrusted(registry):
    result = registry.execute("test-srv__add", {"a": 2, "b": 3})
    assert result.startswith('<untrusted_data source="test-srv__add">') and "5" in result
    assert "echo: hi" in registry.execute("test-srv__echo", {"text": "hi"}, approved=True)


def test_external_argument_checks(registry):
    assert "missing required argument(s) ['b']" in registry.execute("test-srv__add", {"a": 1})
    assert "'a' must be integer" in registry.execute("test-srv__add", {"a": "1", "b": 2})


def test_tool_errors_come_back_as_text_and_server_stays_up(manager, registry):
    result = registry.execute("test-srv__fail", {}, approved=True)
    assert result.startswith("Error: test-srv__fail reported a failure")
    assert '<untrusted_data source="test-srv__fail">' in result  # a server's error text is untrusted too
    assert manager.connections["test-srv"].alive and manager.problems == []


def test_agent_uses_mcp_tools_with_approval_and_traces_them(registry, tmp_path):
    llm = ScriptedLLM(
        calls(("test-srv__add", {"a": 2, "b": 2}), ("test-srv__echo", {"text": "x"})),
        {"role": "assistant", "content": "done"},
    )
    approver = RecordingApprover(Decision("rejected", reason="no echo"))
    tracer = Tracer(tmp_path / "t.db", record_content=True, prices={})
    agent = Agent(llm, tools=registry, tracer=tracer, gate=ApprovalGate(approver, log_path=tmp_path / "a.jsonl"))
    result = agent.run("go")
    tool_msgs = [m["content"] for m in llm.requests[1]["messages"] if m["role"] == "tool"]
    assert "4" in tool_msgs[0] and "REJECTED" in tool_msgs[1]
    assert "External MCP tool 'echo'" in approver.previews[0]
    with tracer.connect() as conn:
        spans = [
            json.loads(r[0])
            for r in conn.execute(
                "SELECT attributes FROM spans WHERE trace_id = ? AND name = 'tool_call'", (result.trace_id,)
            )
        ]
    assert all(s["kestrel.tool.external"] is True and s["kestrel.tool.server"] == "test-srv" for s in spans)


# --- failures -------------------------------------------------------------------


def test_server_that_fails_to_start_is_reported_once_and_skipped():
    problems = []
    m = MCPManager(
        {"ghost": StdioServerParameters(command="definitely-not-a-real-command-xyz")},
        on_problem=lambda s, msg: problems.append(s),
    )
    try:
        m.start(timeout=30)
        reg = ToolRegistry()
        assert m.register_tools(reg) == [] and problems == ["ghost"]
    finally:
        m.close()


def test_crashed_server_disables_its_tools_and_agent_keeps_going(tmp_path):
    problems = []
    m = MCPManager(
        {"crashy": StdioServerParameters(command=sys.executable, args=[str(CRASHY)])},
        on_problem=lambda s, msg: problems.append((s, msg)),
        log_dir=tmp_path,
    )
    try:
        m.start(timeout=60)
        reg = ToolRegistry()
        m.register_tools(reg, safe_tools={"crashy__ok", "crashy__crash"})
        assert "fine" in reg.execute("crashy__ok", {})

        llm = ScriptedLLM(
            calls(("crashy__crash", {})),
            calls(("crashy__ok", {})),
            {"role": "assistant", "content": "the server died, sorry"},
        )
        result = Agent(llm, tools=reg).run("crash it")

        assert result.text == "the server died, sorry"
        assert reg.tools["crashy__ok"].is_available() is False
        assert [s["function"]["name"] for s in llm.requests[1]["tools"]] == []  # hidden from the model
        assert "unavailable" in [m for m in llm.requests[2]["messages"] if m["role"] == "tool"][-1]["content"]
        assert [s for s, _ in problems] == ["crashy"]  # told once
    finally:
        m.close()


# --- config -------------------------------------------------------------------


def test_load_config(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "abc123")
    path = tmp_path / "kestrel.mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]},
                    "gh": {"command": "gh-mcp", "env": {"TOKEN": "${MY_TOKEN}"}},
                    "off": {"command": "x", "disabled": True},
                },
                "safe_tools": ["fetch__fetch"],
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert [s.name for s in cfg.servers] == ["fetch", "gh"]
    assert cfg.servers[1].env == {"TOKEN": "abc123"} and cfg.safe_tools == {"fetch__fetch"}
    assert load_config(tmp_path / "missing.json") is None
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_config(path)


def test_project_config_allowlists_only_fetch():
    cfg = load_config(Path(__file__).parents[1] / "kestrel.mcp.json")
    assert {s.name for s in cfg.servers} == {"fetch", "time"} and cfg.safe_tools == {"fetch__fetch"}


# --- Kestrel as a server --------------------------------------------------------


@pytest.fixture
def kestrel_server(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "notes.txt").write_text("hello from the workspace", encoding="utf-8")
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    m = MCPManager({"kestrel": build_server(audit_log=tmp_path / "approvals.jsonl")})
    m.start(timeout=30)
    yield m, ws, tmp_path / "approvals.jsonl"
    m.close()


def test_kestrel_server_lists_only_intended_tools(kestrel_server):
    m, _, _ = kestrel_server
    names = {t.name for t in m.connections["kestrel"].tools}
    assert (
        names
        == set(EXPOSED)
        == {"get_current_time", "calculator", "list_files", "read_file", "web_search", "create_note"}
    )
    assert not names & {"write_file", "append_to_file", "send_message", "delete_file"}


def test_kestrel_server_tools_keep_kestrel_safeguards(kestrel_server):
    m, ws, audit = kestrel_server
    assert "hello from the workspace" in m.call("kestrel", "read_file", {"path": "notes.txt"})
    with pytest.raises(ExternalToolError, match="escapes the workspace"):
        m.call("kestrel", "read_file", {"path": "../secret.txt"})
    assert m.call("kestrel", "calculator", {"expression": "0.175 * 2340"}) == "409.5"
    assert "Saved note" in m.call("kestrel", "create_note", {"title": "From MCP", "body": "hi"})
    assert (ws / "notes" / "from-mcp.md").exists()
    entry = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert (entry["tool"], entry["decision"]) == ("create_note", "approved-by-mcp-client")


def test_kestrel_server_refuses_to_expose_risky_tools(monkeypatch):
    from kestrel import mcp_server

    monkeypatch.setitem(mcp_server.EXPOSED, "send_message", ToolAnnotations())
    with pytest.raises(RuntimeError, match="refusing to expose 'send_message'"):
        build_server()
