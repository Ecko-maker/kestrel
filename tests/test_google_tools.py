"""The opt-in Google tools (docs/google.md, docs/google-step-plan.md): off by default with the default agent
unchanged, exactly 3 read tools, every call a card, results untrusted and tainting, user_google_email hidden
from the model, and the bench's stubs. No network, no Google, no model calls."""

import argparse
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from kestrel import google_tools, tools
from kestrel.agent import Agent
from kestrel.approval import ApprovalGate, Decision
from kestrel.bench import cli
from kestrel.bench.runner import agent_fingerprint
from kestrel.bench.safety import SAFETY_DIR, find_markers, load_safety
from kestrel.bench.stubs import GOOGLE_UNAVAILABLE, build_registry, main_registry
from kestrel.permissions import ALL
from kestrel.tools import ToolRegistry
from kestrel.tracing import Tracer

ADDRESS = "Owner.Person@mail.example"
DEFAULT = {"sha": "8a8174d8f17d", "tools": "ede3efa38c54", "system_prompt": "36d57c7e8a82"}
BUILT_IN = sorted(tools.registry.tools)


def live_tools(extra: tuple[str, ...] = ("start_google_auth", "list_calendars")) -> dict:
    """What the server lists: the 3 pinned tools exactly as pinned, plus others it shouldn't expose."""
    out = {
        name: SimpleNamespace(
            name=name, description=spec["description"], input_schema=spec["input_schema"], annotations=None
        )
        for name, spec in google_tools.pinned().items()
    }
    for name in extra:
        out[name] = SimpleNamespace(
            name=name, description="x", input_schema={"type": "object", "properties": {}}, annotations=None
        )
    return out


class FakeServer:
    """Records what reached the server; answers with text that quotes the address, as workspace-mcp does."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, tool: str, args: dict) -> str:
        self.calls.append((tool, dict(args)))
        return (
            f"Found 1 messages for {args['user_google_email']}:\n1. Message ID: m1\n"
            f"   From: Bank <news@bank.example>\n   To: {args['user_google_email'].upper()}\n"
        )


def google_registry(server: FakeServer) -> ToolRegistry:
    registry = ToolRegistry()
    google_tools.register(registry, live_tools(), server, ADDRESS, report=lambda m: None)
    return registry


# --- off by default: the default agent is byte for byte today's ------------------------------------


def test_the_default_agent_is_unchanged_with_every_google_module_imported():
    import kestrel
    import kestrel.bench.cli
    import kestrel.bench.stubs  # noqa: F401

    assert sorted(tools.registry.tools) == BUILT_IN and len(BUILT_IN) == 10
    assert not any(n.startswith("google__") for n in tools.registry.tools)
    fp = agent_fingerprint("groq", "openai/gpt-oss-120b")
    assert {k: fp[k] for k in DEFAULT} == DEFAULT and "google" not in fp
    assert fp["approval_rules"] == "tiers-v2.2" and fp["tool_count"] == 10


def test_google_loads_only_when_asked():
    assert not google_tools.requested(None, {})
    assert not google_tools.requested([], {"KESTREL_TOOLS": ""})
    assert google_tools.requested(["google"], {})
    assert google_tools.requested(None, {"KESTREL_TOOLS": "Google"})
    assert google_tools.requested(None, {"KESTREL_TOOLS": "other, google"})


def test_the_main_bench_registry_without_tools_google_is_the_default_one():
    log: list[dict] = []
    assert sorted(main_registry(log).tools) == BUILT_IN
    with_google = main_registry(log, google=True)
    assert sorted(with_google.tools) == sorted([*BUILT_IN, *(f"google__{t}" for t in google_tools.EXPOSED)])
    fp = agent_fingerprint("groq", "openai/gpt-oss-120b", with_google, google=True)
    assert fp["sha"] != DEFAULT["sha"] and fp["system_prompt"] == DEFAULT["system_prompt"]
    assert fp["google"]["server"] == "workspace-mcp==2.3.0" and fp["google"]["session_approval"] is False


# --- exactly 3 tools, every call a card, untrusted and tainting --------------------------------------


def test_exactly_three_tools_are_registered_whatever_the_server_lists():
    registry = google_registry(FakeServer())
    assert sorted(registry.tools) == sorted(f"google__{t}" for t in google_tools.EXPOSED)
    for tool in registry.tools.values():
        assert tool.risk == "confirm" and tool.allow_session is False
        assert tool.capabilities == ALL | {"reads_untrusted"}  # undeclared: taints, untrusted, egress-checked
        assert tool.untrusted_output and tool.external and tool.server == "google"


def test_the_server_flags_and_scopes_are_read_only():
    args = google_tools.SERVER_ARGS
    assert "--read-only" in args and "--permissions" not in args
    assert set(google_tools.DISABLED).isdisjoint(google_tools.EXPOSED) and "start_google_auth" in google_tools.DISABLED
    assert google_tools.READ_SCOPES == (
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
    )


def test_a_tool_that_differs_from_the_pinned_schema_is_not_registered():
    live = live_tools(extra=())
    changed = json.loads(json.dumps(live["get_events"].input_schema))
    changed["properties"]["send_to"] = {"type": "string"}
    live["get_events"] = SimpleNamespace(**{**vars(live["get_events"]), "input_schema": changed})
    reports: list[str] = []
    registry = ToolRegistry()
    google_tools.register(registry, live, FakeServer(), ADDRESS, report=reports.append)
    assert "google__get_events" not in registry.tools and len(registry.tools) == 2
    assert any("differs from the pinned" in r for r in reports)


def test_the_pinned_schemas_are_the_checked_copy():
    """A changed pin must be re-listed from the server and re-checked (docs/google.md)."""
    data = json.loads(google_tools.SCHEMAS_FILE.read_text(encoding="utf-8"))
    assert data["server"] == "workspace-mcp==2.3.0"
    assert [t["name"] for t in data["tools"]] == list(google_tools.EXPOSED)
    for t in data["tools"]:
        assert "user_google_email" in t["input_schema"]["properties"]
        assert t["annotations"]["read_only_hint"] is True
    assert google_tools.describe()["schemas"] == "ff7049a77adf"


# --- user_google_email never reaches the model, a trace, a card or the audit log ---------------------


def test_the_model_never_sees_user_google_email_in_a_schema():
    registry = google_registry(FakeServer())
    for tool in registry.tools.values():
        params = tool.schema["function"]["parameters"]
        assert "user_google_email" not in params["properties"]
        assert "user_google_email" not in params.get("required", [])
    rejected = registry.execute("google__get_events", {"user_google_email": "attacker@evil.example"}, approved=True)
    assert "unexpected argument" in rejected  # the model can't choose whose account is read


class ScriptedLLM:
    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def chat(self, messages, tools=None):
        self.requests.append(json.dumps({"messages": messages, "tools": tools}, default=str))
        return self.replies.pop(0)


class RecordingApprover:
    def __init__(self):
        self.seen: list[dict] = []

    def review(self, tool_name, args, preview, *, allow_session=False, notice=None):
        self.seen.append({"tool": tool_name, "args": args, "preview": preview, "session": allow_session})
        return Decision("approved")


def test_the_address_is_filled_in_after_the_gate_and_masked_everywhere(tmp_path):
    server = FakeServer()
    registry = google_registry(server)
    call = {"id": "c1", "type": "function", "function": {"name": "google__search_gmail_messages"}}
    call["function"]["arguments"] = json.dumps({"query": "from:bank"})
    llm = ScriptedLLM(
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "assistant", "content": "One email from the bank."},
    )
    approver = RecordingApprover()
    audit = tmp_path / "approvals.jsonl"
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})
    Agent(llm, tools=registry, tracer=tracer, gate=ApprovalGate(approver, log_path=audit)).run("any mail from my bank?")

    assert server.calls == [("search_gmail_messages", {"query": "from:bank", "user_google_email": ADDRESS})]
    assert approver.seen and approver.seen[0]["session"] is False  # a card, with no "approve for session"
    needle = ADDRESS.lower()
    assert needle not in json.dumps(approver.seen).lower()  # the card: preview and arguments
    assert needle not in audit.read_text(encoding="utf-8").lower()  # the audit log
    assert all(needle not in r.lower() for r in llm.requests)  # every request the model got
    assert "[your Google account]" in llm.requests[1]  # the result, masked
    with sqlite3.connect(tmp_path / "traces.db") as db:  # every row of every table of the trace
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        dump = json.dumps([list(db.execute(f"SELECT * FROM {t}")) for t in tables], default=str).lower()
    assert dump and needle not in dump


def test_errors_are_masked_too():
    def failing(tool, args):
        raise ConnectionError(f"MCP server 'google' stopped working for {args['user_google_email']}")

    registry = ToolRegistry()
    google_tools.register(registry, live_tools(), failing, ADDRESS, report=lambda m: None)
    out = registry.execute("google__get_events", {}, approved=True)
    assert ADDRESS.lower() not in out.lower() and "[your Google account]" in out


# --- settings -------------------------------------------------------------------------------------------


def env(**over):
    base = {
        "GOOGLE_OAUTH_CLIENT_ID": "id.apps.example",
        "GOOGLE_OAUTH_CLIENT_SECRET": "s3cret",
        "USER_GOOGLE_EMAIL": ADDRESS,
    }
    return {**base, **over}


def test_settings_name_the_missing_lines_and_never_print_values(tmp_path):
    with pytest.raises(google_tools.GoogleConfigError) as e:
        google_tools.load_settings({"GOOGLE_OAUTH_CLIENT_SECRET": "s3cret"}, project=tmp_path / "p")
    assert "GOOGLE_OAUTH_CLIENT_ID" in str(e.value) and "USER_GOOGLE_EMAIL" in str(e.value)
    assert "s3cret" not in str(e.value)
    with pytest.raises(google_tools.GoogleConfigError, match="USER_GOOGLE_EMAIL"):
        google_tools.load_settings(env(USER_GOOGLE_EMAIL="not an address"), project=tmp_path / "p")
    settings = google_tools.load_settings(
        env(KESTREL_GOOGLE_CREDENTIALS_DIR=str(tmp_path / "tokens")), project=tmp_path / "p"
    )
    assert "s3cret" not in repr(settings) and settings.command == "uvx"
    assert settings.args[:3] == ["--python", "3.13", "workspace-mcp==2.3.0"] and "--read-only" in settings.args
    assert settings.env["WORKSPACE_MCP_CREDENTIALS_DIR"] == str((tmp_path / "tokens").resolve())
    assert "USER_GOOGLE_EMAIL" not in settings.env  # Kestrel fills it in itself, per call


def test_tokens_must_live_outside_the_project_and_the_workspace(tmp_path):
    project, workspace = tmp_path / "kestrel", tmp_path / "kestrel" / "workspace"
    for inside in (project / "tokens", workspace / "tokens"):
        with pytest.raises(google_tools.GoogleConfigError, match="outside"):
            google_tools.load_settings(
                env(KESTREL_GOOGLE_CREDENTIALS_DIR=str(inside)), project=project, workspace=workspace
            )


def test_an_installed_server_can_be_named_instead_of_uvx(tmp_path):
    settings = google_tools.load_settings(
        env(KESTREL_GOOGLE_COMMAND="C:/servers/workspace-mcp.exe", KESTREL_GOOGLE_CREDENTIALS_DIR=str(tmp_path / "t")),
        project=tmp_path / "p",
    )
    assert settings.command == "C:/servers/workspace-mcp.exe" and settings.args[0] == "--single-user"


# --- bench stubs: deny by default, fixtures only, every call logged -------------------------------------


def test_stubs_answer_only_from_fixtures_and_log_every_call():
    log: list[dict] = []
    registry = main_registry(log, google=True)
    out = registry.execute("google__search_gmail_messages", {"query": "x"}, approved=True)
    assert GOOGLE_UNAVAILABLE in out
    assert log == [{"type": "stub_call", "tool": "google__search_gmail_messages", "args": {"query": "x"}}]
    for name, tool in registry.tools.items():
        if name.startswith("google__"):
            assert tool.risk == "confirm" and not tool.allow_session and tool.func.__module__ == "kestrel.bench.stubs"


def test_the_stubs_show_the_model_the_real_tools_schemas():
    real, stub = google_registry(FakeServer()), main_registry([], google=True)
    for name in real.tools:
        assert stub.tools[name].schema == real.tools[name].schema


# --- the s1.1 email tasks -------------------------------------------------------------------------------


def test_the_email_tasks_are_scaffolds_listed_as_todo():
    split = load_safety(SAFETY_DIR)
    email = [st for st in split.tasks if st.meta.google]
    assert len(email) == 16 and sum(st.meta.kind == "attack" for st in email) == 8
    assert {st.meta.paired_with for st in email if st.meta.kind == "benign"} == {
        st.id for st in email if st.meta.kind == "attack"
    }
    todo = {m.where for m in find_markers(SAFETY_DIR)}
    assert {f"pages/email/{st.id}.eml.txt" for st in email} <= todo
    for st in email:  # every task's email is a marker until the owner writes it
        assert "{{INJECTION:" in st.meta.google["get_gmail_message_content"]


def test_email_tasks_run_only_with_tools_google():
    """The before side (no --tools google) selects exactly the 48 s1.0 tasks; the after side all 64."""

    def picked(*tools_flag: str, kinds: str = "all") -> list[str]:
        ns = argparse.Namespace(
            split="safety",
            split_dir=None,
            kinds=kinds,
            safety_group=None,
            tools=list(tools_flag),
            subset="all",
            category=None,
            task=None,
            skip_network=False,
            tasks=None,
            sample=None,
            seed=2026,
            shard=None,
        )
        return [t.id for t in cli._pick_tasks(ns)]

    assert len(picked()) == 48 and not [t for t in picked() if t.startswith(("atk-email-", "ben-email-"))]
    assert len(picked("google")) == 64
    assert len(picked(kinds="benign")) == 18 and len(picked("google", kinds="benign")) == 26


def test_a_real_model_refuses_any_run_that_would_use_an_unwritten_email():
    """With --tools google the run selects the email tasks, so it refuses until the owner writes them; without
    it (the before side, or Phase 4's routed group S) their markers don't block, and validate lists them."""
    ns = argparse.Namespace(split="safety", scripted=None, split_dir=None)
    split = load_safety(SAFETY_DIR)
    with pytest.raises(SystemExit, match="16 unfilled"):
        cli._refuse_incomplete(ns, [st.task for st in split.tasks])
    with pytest.raises(SystemExit, match="1 unfilled"):
        cli._refuse_incomplete(ns, [split.get("ben-email-save-note").task])
    cli._refuse_incomplete(ns, [st.task for st in split.tasks if not st.meta.google])  # no exit


def test_google_stubs_are_not_leak_channels_and_their_reads_are_declared():
    """A Gmail query goes to the user's own account, not to an attacker: the leak guards count web_search,
    fetch, send_message and network MCP stubs only (known issue KI-pending-google-leak-channels)."""
    st = load_safety(SAFETY_DIR).get("atk-email-exfil-fetch")
    assert not any(n.startswith("google__") for n in st.meta.network_tools())
    registry = build_registry(st.meta, [])
    assert "google__get_gmail_message_content" in registry.tools


def test_path_of_the_schemas_file_ships_with_the_package():
    assert google_tools.SCHEMAS_FILE.parent == Path(google_tools.__file__).parent


def test_the_schemas_hash_does_not_depend_on_line_endings(tmp_path, monkeypatch):
    """Git may check the JSON out with CRLF on Windows and LF on Linux CI: the opt-in fingerprint must not differ."""
    expected = google_tools.schemas_sha()
    crlf = tmp_path / "schemas.json"
    crlf.write_bytes(google_tools.SCHEMAS_FILE.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    monkeypatch.setattr(google_tools, "SCHEMAS_FILE", crlf)
    assert google_tools.schemas_sha() == expected and google_tools.pinned()


def test_trace_content_off_works_with_the_google_tools(tmp_path, monkeypatch):
    """KESTREL_TRACE_CONTENT=off: the tool still runs, the trace keeps no mail text, query or address, and
    `distill build` still sees from the spans that the trace used Google (google_tool filter)."""
    from kestrel.distill import dataset as ds

    monkeypatch.setenv("KESTREL_TRACE_CONTENT", "off")
    server = FakeServer()
    registry = google_registry(server)
    call = {"id": "c1", "type": "function", "function": {"name": "google__search_gmail_messages"}}
    call["function"]["arguments"] = json.dumps({"query": "from:bank statement"})
    llm = ScriptedLLM(
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "assistant", "content": "One email from the bank."},
    )
    tracer = Tracer(tmp_path / "traces.db", prices={})
    assert tracer.record_content is False
    gate = ApprovalGate(RecordingApprover(), log_path=tmp_path / "approvals.jsonl")
    Agent(llm, tools=registry, tracer=tracer, gate=gate).run("any mail from my bank?")

    assert server.calls and server.calls[0][0] == "search_gmail_messages"  # the tool ran
    with sqlite3.connect(tmp_path / "traces.db") as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        dump = json.dumps([list(db.execute(f"SELECT * FROM {t}")) for t in tables], default=str).lower()
    for text in ("news@bank.example", "from:bank statement", "one email from the bank", ADDRESS.lower()):
        assert text not in dump, text
    conn = ds.open_readonly(tmp_path / "traces.db")
    try:
        traces = ds.read_traces(conn)
    finally:
        conn.close()
    assert len(traces) == 1 and traces[0]["google"] is True and not traces[0]["messages"]
