"""The command line: provider chain, terminal event printing, a demo chat session, reports."""

import sys

import pytest

import kestrel
from kestrel import TerminalEvents, main, provider_chain, tools


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    """Run in an empty folder (fresh logs/, a tiny workspace) with no keys and no MCP."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("Notes\n- one\n", encoding="utf-8")
    monkeypatch.setattr(tools, "WORKSPACE", (tmp_path / "workspace").resolve())
    monkeypatch.setattr(kestrel, "load_dotenv", lambda: None)
    for var in ("GEMINI_API_KEY", "GROQ_API_KEY", "KESTREL_PROVIDERS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KESTREL_MCP", "off")
    return tmp_path


def run_cli(monkeypatch, *args, inputs=()):
    monkeypatch.setattr(sys, "argv", ["kestrel", *args])
    answers = iter(inputs)

    def fake_input(prompt=""):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", fake_input)
    main()


def test_provider_chain(monkeypatch):
    monkeypatch.delenv("KESTREL_PROVIDERS", raising=False)
    assert provider_chain(None) == ["gemini"]
    monkeypatch.setenv("KESTREL_PROVIDERS", "")
    assert provider_chain(None) == ["gemini"]  # empty means default, e.g. from docker compose
    monkeypatch.setenv("KESTREL_PROVIDERS", " Groq, ollama ")
    assert provider_chain(None) == ["groq", "ollama"]
    assert provider_chain("demo") == ["demo"]
    monkeypatch.setenv("KESTREL_PROVIDERS", "groq,nope")
    with pytest.raises(SystemExit):
        provider_chain(None)


def test_terminal_events_stream_text_and_show_tools(capsys):
    show = TerminalEvents()
    for event in [
        {"type": "step_started", "step": 1},
        {"type": "tool_call", "call_id": "c1", "name": "calculator", "arguments": '{"expression": "2*3"}'},
        {"type": "tool_result", "call_id": "c1", "name": "calculator", "result": "6"},
        {"type": "step_started", "step": 2},
        {"type": "text_delta", "step": 2, "text": "It's "},
        {"type": "text_delta", "step": 2, "text": "6."},
        {"type": "answer", "text": "It's 6."},
    ]:
        show(event)
    out = capsys.readouterr().out
    assert 'calculator(expression="2*3") -> 6' in out and "kestrel > It's 6." in out
    assert out.count("It's 6.") == 1  # streamed once, not printed again at the end


def test_terminal_events_print_unstreamed_answers(capsys):
    TerminalEvents()({"type": "answer", "text": "I hit my limit of 8 steps"})
    assert "kestrel > I hit my limit of 8 steps" in capsys.readouterr().out


def test_demo_chat_session_with_rating_and_reports(in_tmp, monkeypatch, capsys):
    run_cli(monkeypatch, "--provider", "demo", inputs=["What's 17.5% of 2,340?", "/good", "/oops", "exit"])
    out = capsys.readouterr().out
    assert "Demo mode" in out and "409.5" in out and "rated good" in out and "Commands: /good" in out
    assert (in_tmp / "logs" / "traces.db").exists()

    run_cli(monkeypatch, "traces")
    assert "What's 17.5% of 2,340?" in capsys.readouterr().out
    run_cli(monkeypatch, "stats")
    assert "Rated good" in capsys.readouterr().out
    run_cli(monkeypatch, "export", "--rated", "good", "--out", "data/x.jsonl")
    assert "Wrote 0 trace(s)" in capsys.readouterr().out  # demo traces are never training data


def test_trace_command_prints_a_tree(in_tmp, monkeypatch, capsys):
    run_cli(monkeypatch, "--provider", "demo", inputs=["What files do I have, and summarize notes.txt"])
    capsys.readouterr()
    from kestrel.tracing import Tracer

    with Tracer().connect() as conn:
        trace_id = conn.execute("SELECT trace_id FROM traces").fetchone()[0]
    run_cli(monkeypatch, "trace", trace_id[:6])
    out = capsys.readouterr().out
    assert "agent_run" in out and "tool_call list_files" in out and "tool_call read_file" in out
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "trace", "zzzz")


def test_no_usable_provider_exits_with_a_clear_message(in_tmp, monkeypatch):
    with pytest.raises(SystemExit, match="missing GEMINI_API_KEY"):
        run_cli(monkeypatch)
