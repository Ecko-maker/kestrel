"""Web console backend: event streaming, approvals over WebSocket, auth, REST, ratings."""

import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kestrel.agent import Agent
from kestrel.approval import ApprovalGate
from kestrel.tools import ToolRegistry
from kestrel.tracing import Tracer
from kestrel.web.app import COOKIE, WebConfig, create_app

TOKEN = "test-token-123"
BASE = "http://127.0.0.1:8765"


class StreamingFakeLLM:
    """Scripted replies; streams text in small pieces like a real provider."""

    supports_streaming = True
    last_provider = "fake"
    model = "fake-model"

    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, messages, tools=None, on_text=None):
        reply = self.replies.pop(0)
        if on_text and reply.get("content"):
            text = reply["content"]
            for i in range(0, len(text), 4):
                on_text(text[i : i + 4])
        return reply


def call(name, **args):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": f"call-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        ],
    }


def say(text):
    return {"role": "assistant", "content": text}


@pytest.fixture
def ran():
    return []


@pytest.fixture
def registry(ran):
    reg = ToolRegistry()

    @reg.register
    def lookup(q: str) -> str:
        """Look something up."""
        return f"found {q}"

    @reg.register(risk="confirm", preview=lambda a: f"--- notes.txt (current)\n+++ notes.txt (after)\n+{a['text']}")
    def save(text: str) -> str:
        """Save text."""
        ran.append(text)
        return "saved"

    return reg


@pytest.fixture
def setup(tmp_path, registry):
    """Returns (make_client, tracer, audit_log); make_client(*replies, timeout=...) builds an app."""
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})
    audit = tmp_path / "approvals.jsonl"

    def make_client(*replies, timeout=5.0, static_dir=None):
        llm = StreamingFakeLLM(*replies)

        def make_agent(on_event, approver):
            return Agent(
                llm, tools=registry, on_event=on_event, tracer=tracer, gate=ApprovalGate(approver, log_path=audit)
            )

        config = WebConfig(
            token=TOKEN, port=8765, static_dir=static_dir, approval_timeout=timeout, extra_hosts=("testserver",)
        )  # what the test client sends for WebSockets
        return TestClient(create_app(make_agent, tracer, config), base_url=BASE)

    return make_client, tracer, audit


def events_until(ws, stop: str, limit: int = 200) -> list[dict]:
    events = []
    for _ in range(limit):
        event = ws.receive_json()
        events.append(event)
        if event["type"] == stop:
            return events
    raise AssertionError(f"no {stop} event; got {[e['type'] for e in events]}")


def connect(client):
    return client.websocket_connect(f"/ws?token={TOKEN}")


# --- streaming --------------------------------------------------------------------


def test_websocket_streams_events_in_order(setup):
    make_client, _, _ = setup
    client = make_client(call("lookup", q="x"), say("Here is the answer."))
    with connect(client) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "look it up"})
        events = events_until(ws, "done")

    kinds = [e["type"] for e in events]
    collapsed = [k for i, k in enumerate(kinds) if i == 0 or k != kinds[i - 1]]
    assert collapsed == [
        "step_started",
        "llm_call",
        "tool_call",
        "tool_result",
        "step_started",
        "text_delta",
        "llm_call",
        "answer",
        "done",
    ]
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == "Here is the answer."
    assert kinds.count("text_delta") > 1  # streamed in pieces
    trace_ids = {e["trace_id"] for e in events}
    assert len(trace_ids) == 1 and None not in trace_ids
    result = next(e for e in events if e["type"] == "tool_result")
    assert (result["name"], result["ok"], result["ran"], result["result"]) == ("lookup", True, True, "found x")
    answer = next(e for e in events if e["type"] == "answer")
    assert answer["text"] == "Here is the answer." and answer["steps"] == 2


def test_busy_agent_refuses_a_second_message(setup, ran):
    make_client, _, _ = setup
    client = make_client(call("save", text="a"), say("ok"))
    with connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "text": "save a"})
        approval = events_until(ws, "approval_required")[-1]
        ws.send_json({"type": "user_message", "text": "another"})
        assert ws.receive_json() == {"type": "error", "message": "Kestrel is still working on your last message."}
        ws.send_json({"type": "approval_response", "approval_id": approval["approval_id"], "decision": "approve"})
        events_until(ws, "done")
    assert ran == ["a"]


# --- approvals --------------------------------------------------------------------


def run_until_approval(ws, text="save it"):
    ws.receive_json()  # ready
    ws.send_json({"type": "user_message", "text": text})
    return events_until(ws, "approval_required")[-1]


def test_approve_round_trip(setup, ran):
    make_client, _, _ = setup
    with connect(make_client(call("save", text="hello"), say("Saved."))) as ws:
        approval = run_until_approval(ws)
        assert approval["tool"] == "save" and approval["kind"] == "diff" and "+hello" in approval["preview"]
        assert approval["editable_field"] == "text" and approval["trace_id"]
        assert ran == []  # nothing runs while waiting
        ws.send_json({"type": "approval_response", "approval_id": approval["approval_id"], "decision": "approve"})
        events = events_until(ws, "done")
    assert ran == ["hello"]
    resolved = next(e for e in events if e["type"] == "approval_resolved")
    assert resolved["decision"] == "approved"
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ran"] is True and result["decision"] == "approved"


def test_edit_round_trip_previews_again_and_runs_the_edit(setup, ran):
    make_client, _, audit = setup
    with connect(make_client(call("save", text="draft"), say("Saved."))) as ws:
        first = run_until_approval(ws)
        ws.send_json(
            {
                "type": "approval_response",
                "approval_id": first["approval_id"],
                "decision": "edit",
                "args": {"text": "edited by me"},
            }
        )
        events = events_until(ws, "approval_required")
        assert next(e for e in events if e["type"] == "approval_resolved")["decision"] == "edited"
        second = events[-1]
        assert second["approval_id"] != first["approval_id"] and "+edited by me" in second["preview"]
        ws.send_json({"type": "approval_response", "approval_id": second["approval_id"], "decision": "approve"})
        events_until(ws, "done")
    assert ran == ["edited by me"]
    assert json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])["decision"] == "edited"


def test_reject_round_trip_sends_reason_to_the_model(setup, ran):
    make_client, _tracer, _ = setup
    with connect(make_client(call("save", text="x"), say("Okay, I won't."))) as ws:
        approval = run_until_approval(ws)
        ws.send_json(
            {
                "type": "approval_response",
                "approval_id": approval["approval_id"],
                "decision": "reject",
                "reason": "not today",
            }
        )
        events = events_until(ws, "done")
    assert ran == []
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ran"] is False and result["decision"] == "rejected" and "not today" in result["result"]


def test_unexpected_answer_counts_as_rejection(setup, ran):
    make_client, _, _ = setup
    with connect(make_client(call("save", text="x"), say("ok"))) as ws:
        approval = run_until_approval(ws)
        ws.send_json({"type": "approval_response", "approval_id": approval["approval_id"], "decision": "yolo"})
        events_until(ws, "done")
    assert ran == []


def wait_for_audit(audit, timeout=5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if audit.exists() and audit.read_text(encoding="utf-8").strip():
            return json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
        time.sleep(0.05)
    raise AssertionError("no audit entry")


def test_disconnect_during_approval_rejects(setup, ran):
    make_client, _, audit = setup
    with connect(make_client(call("save", text="x"), say("ok"))) as ws:
        run_until_approval(ws)
    # the browser is gone; the waiting agent must be told "rejected"
    entry = wait_for_audit(audit)
    assert entry["decision"] == "rejected" and "disconnected" in entry["reason"]
    time.sleep(0.2)
    assert ran == []


def test_no_answer_times_out_as_rejection(setup, ran):
    make_client, _, _audit = setup
    with connect(make_client(call("save", text="x"), say("ok"), timeout=0.3)) as ws:
        run_until_approval(ws)
        events = events_until(ws, "done")
    assert ran == []
    assert next(e for e in events if e["type"] == "approval_resolved")["reason"].startswith("no answer within")


# --- security ---------------------------------------------------------------------


def test_requests_without_the_token_are_refused(setup):
    make_client, _, _ = setup
    client = make_client()
    assert client.get("/api/traces").status_code == 401
    assert client.get("/api/traces", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/").status_code == 401
    assert client.get("/api/traces", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()


def test_token_in_url_becomes_a_cookie_and_is_removed_from_the_url(setup, tmp_path):
    make_client, _, _ = setup
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>console</html>", encoding="utf-8")
    client = make_client(static_dir=dist)
    response = client.get(f"/?token={TOKEN}", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/"
    assert (
        "httponly" in response.headers["set-cookie"].lower()
        and "samesite=strict" in response.headers["set-cookie"].lower()
    )
    assert client.cookies.get(COOKIE) == TOKEN
    assert client.get("/").text == "<html>console</html>"  # cookie now works
    assert client.get("/traces/abc").text == "<html>console</html>"  # client-side route


def test_wrong_host_and_foreign_origin_are_refused(setup):
    make_client, _, _ = setup
    client = make_client()
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/traces", headers={**auth, "host": "evil.example:8765"}).status_code == 400
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws?token={TOKEN}", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    with client.websocket_connect(f"/ws?token={TOKEN}", headers={"origin": "http://127.0.0.1:5173"}) as ws:
        assert ws.receive_json()["type"] == "ready"  # the Vite dev server is allowed


def test_cors_only_allows_the_dev_server(setup):
    make_client, _, _ = setup
    client = make_client()
    ok = client.options(
        "/api/traces", headers={"origin": "http://localhost:5173", "access-control-request-method": "GET"}
    )
    bad = client.options(
        "/api/traces", headers={"origin": "https://evil.example", "access-control-request-method": "GET"}
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in bad.headers


# --- REST ---------------------------------------------------------------------


def test_traces_stats_and_rating(setup):
    make_client, _tracer, _ = setup
    client = make_client(say("First answer"))
    with connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "text": "hello there"})
        trace_id = events_until(ws, "done")[-1]["trace_id"]

    auth = {"Authorization": f"Bearer {TOKEN}"}
    listing = client.get("/api/traces", params={"q": "hello"}, headers=auth).json()
    assert [t["trace_id"] for t in listing] == [trace_id]
    assert client.get("/api/traces", params={"q": "nothing-matches"}, headers=auth).json() == []

    detail = client.get(f"/api/traces/{trace_id[:8]}", headers=auth).json()
    assert detail["trace_id"] == trace_id and {s["name"] for s in detail["spans"]} == {"agent_run", "llm_call"}
    assert "messages" not in detail

    response = client.post(f"/api/traces/{trace_id}/rating", json={"rating": "good", "note": "nice"}, headers=auth)
    assert response.json() == {"ok": True}
    assert client.get(f"/api/traces/{trace_id}", headers=auth).json()["rating"] == "good"
    assert client.post(f"/api/traces/{trace_id}/rating", json={"rating": "meh"}, headers=auth).status_code == 422
    assert client.post("/api/traces/ffff0000/rating", json={"rating": "good"}, headers=auth).status_code == 404

    stats = client.get("/api/stats", headers=auth).json()
    assert stats["traces"] == 1 and stats["requests_today"] == 1 and stats["good_share"] == 1.0
    assert len(stats["series"]) == 1 and stats["list_price_usd_per_conversation"] is None


def test_chat_unavailable_still_serves_traces(tmp_path):
    tracer = Tracer(tmp_path / "t.db", record_content=True, prices={})

    def make_agent(on_event, approver):
        raise RuntimeError("missing GEMINI_API_KEY")

    client = TestClient(
        create_app(make_agent, tracer, WebConfig(token=TOKEN, static_dir=None, extra_hosts=("testserver",))),
        base_url=BASE,
    )
    with connect(client) as ws:
        event = ws.receive_json()
    assert event["type"] == "error" and "missing GEMINI_API_KEY" in event["message"]
    assert client.get("/api/traces", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
