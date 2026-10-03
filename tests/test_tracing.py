"""Tracing: span trees, token/cost roll-ups, errors, redaction, content-off, feedback, export, stats."""

import json
import sqlite3
from contextlib import closing

import pytest

from kestrel import parse_feedback, trace_report
from kestrel.agent import Agent
from kestrel.approval import ApprovalGate, Decision
from kestrel.llm import CallInfo, LLMError
from kestrel.tools import ToolRegistry
from kestrel.tracing import REDACTED, Tracer, redact

PRICES = {
    "fakeprov": {"fake-model": {"actual_input": 0.0, "actual_output": 0.0, "list_input": 1.0, "list_output": 10.0}}
}
SECRET = "gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def call(call_id, name, **args):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
        "extra_content": {"google": {"thought_signature": "sig"}},
    }


class FakeLLM:
    """Scripted replies; reports usage like a real provider unless usage is None."""

    def __init__(self, *replies, usage=((100, 10),), provider="fakeprov", model="fake-model"):
        self.replies = list(replies)
        self.usage = list(usage)
        self.provider_name, self.model = provider, model
        self.last_call = None
        self.last_provider = provider

    def chat(self, messages, tools=None):
        reply = self.replies.pop(0)
        tokens = self.usage.pop(0) if self.usage else (None, None)
        self.last_call = CallInfo(
            self.provider_name, self.model, *tokens, finish_reason="stop", attempts=[self.provider_name]
        )
        if isinstance(reply, Exception):
            raise reply
        return reply


class Approve:
    def __init__(self, *decisions):
        self.decisions = list(decisions)

    def review(self, tool_name, args, preview, *, allow_session=False):
        return self.decisions.pop(0)


@pytest.fixture
def registry():
    reg = ToolRegistry()

    @reg.register
    def lookup(q: str) -> str:
        """Look something up."""
        return f"found {q}"

    @reg.register(risk="confirm")
    def save(text: str) -> str:
        """Save text."""
        return "saved"

    return reg


@pytest.fixture
def tracer(tmp_path):
    return Tracer(tmp_path / "traces.db", record_content=True, prices=PRICES)


def make_agent(llm, registry, tracer, tmp_path, *decisions):
    gate = ApprovalGate(Approve(*decisions), log_path=tmp_path / "approvals.jsonl")
    return Agent(llm, tools=registry, tracer=tracer, gate=gate)


def dump_db(path) -> str:
    with closing(sqlite3.connect(path)) as conn:
        return "\n".join(conn.iterdump())


def rows(tracer, sql, *params):
    with tracer.connect() as conn:
        return conn.execute(sql, params).fetchall()


def spans_of(tracer, trace_id):
    return [
        dict(r) | {"attributes": json.loads(r["attributes"])}
        for r in rows(tracer, "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time", trace_id)
    ]


def tool_turn(tmp_path, registry, tracer, *decisions):
    llm = FakeLLM(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [call("c1", "lookup", q="x"), call("c2", "save", text="hi")],
        },
        {"role": "assistant", "content": "All done."},
        usage=[(100, 10), (200, 20)],
    )
    return make_agent(llm, registry, tracer, tmp_path, *decisions).run("look up x and save hi")


def test_run_creates_the_right_span_tree(tmp_path, registry, tracer):
    result = tool_turn(tmp_path, registry, tracer, Decision("approved"))
    spans = spans_of(tracer, result.trace_id)
    by_id = {s["span_id"]: s for s in spans}
    shape = sorted((s["name"], by_id[s["parent_id"]]["name"] if s["parent_id"] else None) for s in spans)
    assert shape == sorted(
        [
            ("agent_run", None),
            ("llm_call", "agent_run"),
            ("llm_call", "agent_run"),
            ("tool_call", "agent_run"),
            ("tool_call", "agent_run"),
            ("approval", "tool_call"),
        ]
    )
    assert len({s["trace_id"] for s in spans}) == 1
    assert all(s["duration_ms"] is not None and s["end_time"] >= s["start_time"] for s in spans)

    llm = next(s for s in spans if s["name"] == "llm_call")["attributes"]
    assert llm["gen_ai.provider.name"] == "fakeprov" and llm["gen_ai.request.model"] == "fake-model"
    assert llm["gen_ai.response.finish_reasons"] == ["stop"]
    save = next(s for s in spans if s["attributes"].get("gen_ai.tool.name") == "save")["attributes"]
    assert save["kestrel.tool.risk"] == "confirm" and save["kestrel.tool.ran"] is True
    approval = next(s for s in spans if s["name"] == "approval")["attributes"]
    assert approval["kestrel.approval.decision"] == "approved"


def test_token_and_cost_totals_add_up(tmp_path, registry, tracer):
    result = tool_turn(tmp_path, registry, tracer, Decision("approved"))
    [t] = rows(tracer, "SELECT * FROM traces WHERE trace_id = ?", result.trace_id)
    assert (t["input_tokens"], t["output_tokens"]) == (300, 30)
    assert t["cost_usd"] == 0.0
    assert t["list_price_usd"] == pytest.approx((300 * 1.0 + 30 * 10.0) / 1_000_000)
    assert (t["steps"], t["stop_reason"], t["status"]) == (2, "answered", "ok")
    assert result.tokens == 330 and t["tokens_estimated"] == 0


def test_missing_usage_is_estimated_and_marked(tmp_path, registry, tracer):
    llm = FakeLLM({"role": "assistant", "content": "hello"}, usage=[(None, None)])
    result = make_agent(llm, registry, tracer, tmp_path).run("hi")
    [llm_span] = [s for s in spans_of(tracer, result.trace_id) if s["name"] == "llm_call"]
    assert llm_span["attributes"]["kestrel.usage.estimated"] is True
    assert llm_span["attributes"]["gen_ai.usage.input_tokens"] > 0
    assert rows(tracer, "SELECT tokens_estimated FROM traces")[0][0] == 1


def test_unknown_model_has_no_list_price(tmp_path, registry, tracer):
    llm = FakeLLM({"role": "assistant", "content": "hi"}, provider="ollama", model="tiny")
    result = make_agent(llm, registry, tracer, tmp_path).run("hi")
    [t] = rows(tracer, "SELECT cost_usd, list_price_usd FROM traces WHERE trace_id = ?", result.trace_id)
    assert (t["cost_usd"], t["list_price_usd"]) == (None, None)


def test_errors_are_recorded(tmp_path, registry, tracer):
    llm = FakeLLM(
        {"role": "assistant", "content": None, "tool_calls": [call("c1", "nope")]},
        LLMError("all providers", "gemini: 429 | groq: 503"),
        usage=[(50, 5), (None, None)],
    )
    result = make_agent(llm, registry, tracer, tmp_path).run("hi")
    spans = spans_of(tracer, result.trace_id)
    status = {(s["name"], s["status"]) for s in spans}
    assert ("agent_run", "error") in status and ("llm_call", "error") in status and ("tool_call", "error") in status
    failed_llm = next(s for s in spans if s["name"] == "llm_call" and s["status"] == "error")
    assert "gemini: 429" in failed_llm["error"]
    [t] = rows(tracer, "SELECT status, stop_reason FROM traces")
    assert (t["status"], t["stop_reason"]) == ("error", "error")


def test_rejected_and_fallback_are_recorded(tmp_path, registry, tracer):
    result = tool_turn(tmp_path, registry, tracer, Decision("rejected", reason="not now"))
    spans = spans_of(tracer, result.trace_id)
    approval = next(s for s in spans if s["name"] == "approval")["attributes"]
    assert approval["kestrel.approval.decision"] == "rejected" and approval["kestrel.approval.reason"] == "not now"
    save = next(s for s in spans if s["attributes"].get("gen_ai.tool.name") == "save")["attributes"]
    assert save["kestrel.tool.ran"] is False


def test_redaction():
    assert SECRET not in redact(f"my key is {SECRET} ok")
    fake_google_key = "AIza" + "SyD-1234567890abcdefghijklmnopqrstu"  # split so secret scanners ignore it
    assert fake_google_key not in redact(fake_google_key)
    assert redact("GEMINI_API_KEY=whatever-value-123") == f"GEMINI_API_KEY={REDACTED}"
    assert redact('{"api_key": "hunter2hunter2"}') == '{"api_key": "' + REDACTED + '"}'
    assert redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123") == f"Authorization: {REDACTED}"
    assert redact("the 2,340 tokens cost $0.01") == "the 2,340 tokens cost $0.01"  # ordinary text untouched


def test_secrets_never_reach_the_database(tmp_path, registry, tracer, monkeypatch):
    monkeypatch.setenv("MY_SERVICE_TOKEN", "plain-looking-value-42")
    llm = FakeLLM({"role": "assistant", "content": f"echo {SECRET} and plain-looking-value-42"})
    make_agent(llm, registry, tracer, tmp_path).run(f"remember {SECRET}")
    dump = dump_db(tracer.db_path)
    assert SECRET not in dump and "plain-looking-value-42" not in dump
    assert REDACTED in dump


def test_content_off_stores_no_text(tmp_path, registry):
    tracer = Tracer(tmp_path / "t.db", record_content=False, prices=PRICES)
    llm = FakeLLM(
        {"role": "assistant", "content": None, "tool_calls": [call("c1", "lookup", q="pineapple")]},
        {"role": "assistant", "content": "Found the pineapple."},
        usage=[(100, 10), (200, 20)],
    )
    result = make_agent(llm, registry, tracer, tmp_path).run("find the pineapple please")
    tracer.rate(result.trace_id, "good", "pineapple was great")
    dump = dump_db(tracer.db_path)
    assert "pineapple" not in dump
    [t] = rows(tracer, "SELECT * FROM traces")
    assert t["user_message"] is None and t["messages"] is None and t["input_tokens"] == 300
    assert t["rating"] == "good"


def test_feedback_attaches_to_the_right_trace(tmp_path, registry, tracer):
    llm = FakeLLM({"role": "assistant", "content": "a"}, {"role": "assistant", "content": "b"}, usage=[(1, 1), (1, 1)])
    agent = make_agent(llm, registry, tracer, tmp_path)
    first, second = agent.run("one"), agent.run("two")
    tracer.rate(tracer.find_trace_id(first.trace_id[:6]), "bad", "too short")
    ratings = {r["trace_id"]: (r["rating"], r["rating_note"]) for r in rows(tracer, "SELECT * FROM traces")}
    assert ratings == {first.trace_id: ("bad", "too short"), second.trace_id: (None, None)}
    with pytest.raises(LookupError):
        tracer.find_trace_id("zzzz")


def test_parse_feedback():
    assert parse_feedback("/good") == ("good", "")
    assert parse_feedback("/BAD  way too long ") == ("bad", "way too long")
    assert parse_feedback("/help") is None and parse_feedback("good") is None


def test_export_writes_valid_chat_jsonl(tmp_path, registry, tracer):
    good = tool_turn(tmp_path, registry, tracer, Decision("approved"))
    tracer.rate(good.trace_id, "good")
    llm = FakeLLM({"role": "assistant", "content": "meh"})
    make_agent(llm, registry, tracer, tmp_path).run("unrated")

    out = tmp_path / "data" / "traces.jsonl"
    with tracer.connect() as conn:
        written, skipped = trace_report.export(conn, "good", out, registry.schemas())
    assert (written, skipped) == (1, 0)
    [line] = out.read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    roles = [m["role"] for m in record["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "tool", "assistant"]
    assert record["messages"][-1]["content"] == "All done."
    assert all("extra_content" not in tc for tc in record["messages"][2]["tool_calls"])  # provider extras stripped
    assert {t["function"]["name"] for t in record["tools"]} == {"lookup", "save"}
    assert record["metadata"]["rating"] == "good"


def test_stats_and_reports(tmp_path, registry, tracer):
    for i in range(3):
        llm = FakeLLM({"role": "assistant", "content": f"answer {i}"}, usage=[(100 * (i + 1), 10)])
        r = make_agent(llm, registry, tracer, tmp_path).run(f"question {i}")
        tracer.rate(r.trace_id, "good" if i else "bad")
    with tracer.connect() as conn:
        s = trace_report.compute_stats(conn)
        lines = []
        trace_report.print_traces(conn, 20, lines.append)
        trace_report.print_trace(conn, r.trace_id, lines.append)
        trace_report.print_stats(conn, lines.append)
    assert s["traces"] == 3 and s["tokens_total"] == 600 + 30
    assert s["good_share"] == pytest.approx(2 / 3) and s["error_rate"] == 0
    assert s["latency_p95_ms"] >= s["latency_p50_ms"]
    text = "\n".join(lines)
    assert "question 2" in text and "agent_run" in text and "└─ llm_call fakeprov" in text


def test_percentile():
    assert trace_report.percentile([5, 1, 3, 2, 4], 50) == 3
    assert trace_report.percentile(list(range(1, 101)), 95) == 95
    assert trace_report.percentile([], 50) is None


def test_tracing_failure_never_breaks_the_agent(tmp_path, registry, capsys):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    tracer = Tracer(blocker / "traces.db", record_content=True, prices=PRICES)  # can't create a db here
    llm = FakeLLM({"role": "assistant", "content": "still works"})
    assert make_agent(llm, registry, tracer, tmp_path).run("hi").text == "still works"
    assert "could not save trace" in capsys.readouterr().err


def test_export_only_trains_on_the_rated_turn(tmp_path, registry, tracer):
    llm = FakeLLM(
        {"role": "assistant", "content": "bad old answer"},
        {"role": "assistant", "content": "good answer"},
        usage=[(1, 1), (1, 1)],
    )
    agent = make_agent(llm, registry, tracer, tmp_path)
    tracer.rate(agent.run("first").trace_id, "bad")
    tracer.rate(agent.run("second").trace_id, "good")
    out = tmp_path / "out.jsonl"
    with tracer.connect() as conn:
        trace_report.export(conn, "good", out, [])
    messages = json.loads(out.read_text(encoding="utf-8"))["messages"]
    weights = {m["content"]: m.get("weight") for m in messages if m["role"] == "assistant"}
    assert weights == {"bad old answer": 0, "good answer": 1}
