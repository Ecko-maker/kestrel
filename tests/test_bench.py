"""KestrelBench machinery: task loading, checks, runner, judge parsing, calibration, report, CLI.
Offline: scripted fake models and the demo provider."""

import json
import re
import sys
from pathlib import Path

import pytest

from kestrel import tools
from kestrel.bench import calibrate as cal
from kestrel.bench.checks import Outcome, run_check
from kestrel.bench.judge import Judge, parse_verdict
from kestrel.bench.report import markdown, summarize
from kestrel.bench.runner import ScriptedApprover, TaskResult, run_task
from kestrel.bench.tasks import ApprovalRule, Task, TaskError, load_tasks, select


def call(name, **args):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": f"c-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        ],
    }


def say(text):
    return {"role": "assistant", "content": text}


class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, messages, tools=None):
        return self.replies.pop(0) if self.replies else say("done")


@pytest.fixture
def base(tmp_path):
    ws = tmp_path / "base"
    ws.mkdir()
    (ws / "todo.md").write_text("- [ ] Book dentist appointment\n", encoding="utf-8")
    (ws / "budget.csv").write_text("item,amount\nRent,100\n", encoding="utf-8")
    return ws


def task(**kw):
    defaults = {"id": "t", "category": "c", "prompts": ("go",), "checks": ()}
    return Task(**{**defaults, **kw})


# --- the shipped tasks ------------------------------------------------------------


def test_shipped_suite_is_valid():
    tasks = load_tasks()
    assert len(tasks) == 100
    assert len({t.id for t in tasks}) == 100
    known = set(tools.registry.tools)
    for t in tasks:
        for c in t.checks:
            assert c.get("tool", "calculator") in known, (t.id, c)
            for pattern in [c.get("pattern"), *(c.get("args") or {}).values()]:
                if pattern:
                    re.compile(pattern)
        assert all(r.tool in known for r in t.approvals), t.id
    ci = select(tasks, "ci")
    assert 10 <= len(ci) <= 20 and not any("network" in t.tags for t in ci)


def test_loader_rejects_bad_tasks(tmp_path):
    def load(text):
        (tmp_path / "x.yaml").write_text(text, encoding="utf-8")
        return load_tasks(tmp_path)

    with pytest.raises(TaskError, match="unknown check type"):
        load("- {id: a, category: c, prompt: hi, checks: [{type: nope}]}")
    with pytest.raises(TaskError, match="missing \\['tool'\\]"):
        load("- {id: a, category: c, prompt: hi, checks: [{type: tool_called}]}")
    with pytest.raises(TaskError, match="needs at least one check or a rubric"):
        load("- {id: a, category: c, prompt: hi}")
    with pytest.raises(TaskError, match="duplicate task id"):
        load("- {id: a, category: c, prompt: hi, rubric: r}\n- {id: a, category: c, prompt: hi, rubric: r}")
    with pytest.raises(TaskError, match="unknown fields"):
        load("- {id: a, category: c, prompt: hi, rubric: r, colour: red}")


def test_select_filters():
    ts = [task(id="a", tags=("ci",)), task(id="b", tags=("network",)), task(id="c", category="x")]
    assert [t.id for t in select(ts, "ci")] == ["a"]
    assert [t.id for t in select(ts, skip_tags=("network",))] == ["a", "c"]
    assert [t.id for t in select(ts, category="x")] == ["c"]
    assert [t.id for t in select(ts, ids=["b"])] == ["b"]


# --- checks -----------------------------------------------------------------------


def outcome(tmp_path, events=(), answer="", **kw):
    return Outcome(
        events=list(events),
        answer=answer,
        stop_reason=kw.get("stop", "answered"),
        steps=kw.get("steps", 1),
        workspace=tmp_path,
        original_files=kw.get("originals", {}),
    )


def ev_call(name, **args):
    return {"type": "tool_call", "call_id": name, "name": name, "arguments": json.dumps(args)}


def ev_result(name, ran=True, ok=True):
    return {"type": "tool_result", "call_id": name, "name": name, "ran": ran, "ok": ok}


def test_tool_checks(tmp_path):
    o = outcome(
        tmp_path,
        [
            ev_call("read_file", path="todo.md"),
            ev_result("read_file"),
            ev_call("send_message", to="x@y.z"),
            ev_result("send_message", ran=False, ok=False),
        ],
    )
    assert run_check({"type": "tool_called", "tool": "read_file", "args": {"path": "todo"}}, o)[0]
    assert not run_check({"type": "tool_called", "tool": "read_file", "args": {"path": "budget"}}, o)[0]
    assert run_check({"type": "tool_ran", "tool": "read_file"}, o)[0]
    assert not run_check({"type": "tool_ran", "tool": "send_message"}, o)[0]  # attempted, rejected
    assert run_check({"type": "tool_not_ran", "tool": "send_message"}, o)[0]
    assert not run_check({"type": "tool_not_called", "tool": "send_message"}, o)[0]  # but it was attempted
    assert not run_check({"type": "no_tools"}, o)[0]


def test_answer_file_and_outbox_checks(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "groceries.md").write_text("milk, eggs and bread", encoding="utf-8")
    (tmp_path / "outbox").mkdir()
    (tmp_path / "outbox" / "1.md").write_text("To: sam@example.com\nSubject: hi\n\nWednesday!", encoding="utf-8")
    (tmp_path / "todo.md").write_text("changed", encoding="utf-8")
    o = outcome(tmp_path, answer="It's 2,282.79 in total.", originals={"todo.md": "original"})
    assert run_check({"type": "answer_matches", "pattern": r"2,?282\.79"}, o)[0]
    assert run_check({"type": "answer_not_matches", "pattern": "canary"}, o)[0]
    assert run_check({"type": "file_contains", "path": "notes/*.md", "pattern": "(?=.*milk)(?=.*bread)"}, o)[0]
    assert not run_check({"type": "file_contains", "path": "missing.md", "pattern": "x"}, o)[0]
    assert run_check({"type": "file_absent", "path": "notes/pwned.md"}, o)[0]
    assert not run_check({"type": "file_unchanged", "path": "todo.md"}, o)[0]
    assert run_check({"type": "outbox_count", "count": 1, "to": r"sam@example\.com"}, o)[0]
    assert run_check({"type": "outbox_count", "count": 0, "to": "priya"}, o)[0]
    assert run_check({"type": "max_steps", "steps": 1}, o)[0]
    assert not run_check({"type": "answer_matches", "pattern": "("}, o)[0]  # bad regex fails, doesn't crash


# --- runner -------------------------------------------------------------------------


def test_scripted_approver_rules():
    approver = ScriptedApprover(
        (
            ApprovalRule("send_message", "reject", "too formal", times=1),
            ApprovalRule("send_message", "approve", args={"to": "sam"}),
        )
    )
    assert approver.review("send_message", {"to": "sam@x"}, "").status == "rejected"
    assert approver.review("send_message", {"to": "sam@x"}, "").status == "approved"
    assert approver.review("send_message", {"to": "eve@x"}, "").status == "rejected"  # unexpected: safe default
    assert approver.review("write_file", {}, "").status == "rejected"
    assert len(approver.requests) == 4


def test_run_task_passes_and_isolates_the_workspace(base, monkeypatch):
    real = tools.WORKSPACE
    t = task(
        prompts=("add it",),
        files={"extra.txt": "hello"},
        approvals=(ApprovalRule("append_to_file", "approve"),),
        checks=(
            {"type": "tool_ran", "tool": "append_to_file"},
            {"type": "file_contains", "path": "todo.md", "pattern": "call the bank"},
            {"type": "file_contains", "path": "extra.txt", "pattern": "hello"},
        ),
    )
    llm = Scripted(call("append_to_file", path="todo.md", content="- [ ] Call the bank"), say("Added."))
    result = run_task(t, llm, base_workspace=base)
    assert result.status == "pass" and result.score == 1.0, result.checks
    assert (base / "todo.md").read_text(encoding="utf-8") == "- [ ] Book dentist appointment\n"  # fixture untouched
    assert real == tools.WORKSPACE  # restored


def test_run_task_fails_with_details(base):
    t = task(checks=({"type": "tool_called", "tool": "calculator"}, {"type": "answer_matches", "pattern": "42"}))
    result = run_task(t, Scripted(say("It's 41.")), base_workspace=base)
    assert result.status == "fail" and result.score == 0.0
    assert all(not c["ok"] for c in result.checks) and "not called" in result.checks[0]["detail"]


def test_model_outage_is_an_error_not_a_failure(base):
    from kestrel.llm import LLMError

    class Down:
        def chat(self, messages, tools=None):
            raise LLMError("groq", "rate limited (429)")

    result = run_task(task(checks=({"type": "no_tools"},)), Down(), base_workspace=base)
    assert result.status == "error" and "429" in result.error


class FakeJudge(Judge):
    def __init__(self, reply):
        super().__init__(Scripted(say(reply)), name="fake")
        self.requests = []

    def grade(self, prompts, rubric, tool_log, answer):
        self.requests.append((prompts, rubric, tool_log, answer))
        return super().grade(prompts, rubric, tool_log, answer)


def test_judge_combines_with_checks(base):
    t = task(rubric="Mentions Canberra.", checks=({"type": "no_tools"},))
    judge = FakeJudge('{"score": 0.5, "reason": "close"}')
    result = run_task(t, Scripted(say("Sydney? No, Canberra.")), judge=judge, base_workspace=base)
    assert result.status == "pass" and result.score == 0.75 and result.judge["score"] == 0.5
    assert judge.requests[0][1] == "Mentions Canberra." and judge.requests[0][3].endswith("Canberra.")

    bad = run_task(t, Scripted(say("Sydney.")), judge=FakeJudge('{"score": 0, "reason": "wrong"}'), base_workspace=base)
    assert bad.status == "fail"
    broken = run_task(t, Scripted(say("x")), judge=FakeJudge("I think it's fine"), base_workspace=base)
    assert broken.status == "error" and "no JSON" in broken.error


def test_parse_verdict():
    assert parse_verdict('```json\n{"score": 1, "reason": "ok"}\n```').score == 1.0
    assert parse_verdict('{"score": 0.5, "reason": "partly"} trailing').score == 0.5
    assert parse_verdict('{"score": 7}').score is None
    assert parse_verdict("no json here").score is None


# --- calibration and report -----------------------------------------------------------


def test_cohens_kappa():
    perfect = [(True, True), (False, False), (True, True), (False, False)]
    assert cal.cohens_kappa(perfect) == 1.0
    assert cal.cohens_kappa([(True, True)] * 5) is None  # undefined: no variation
    always_pass = [(True, True), (True, True), (False, True), (False, True)]
    assert cal.cohens_kappa(always_pass) == 0.0  # 50% agreement, all of it chance


def pair(human, judge, category="c", task_id="t"):
    return cal.Pair(task_id, category, human, judge, 1.0 if judge else 0.0, "because")


def test_calibration_confusion_and_categories():
    c = cal.calibrate(
        [
            pair(True, True, "a"),
            pair(False, False, "a"),
            pair(False, True, "a", "lenient-one"),  # judge passed what you failed
            pair(True, False, "b", "strict-one"),
            pair(True, True, "b"),
        ]
    )
    assert c.n == 5 and c.agreement is not None and c.agreement.value == 0.6
    assert c.confusion == {"both_pass": 2, "both_fail": 1, "too_lenient": 1, "too_strict": 1}
    assert c.by_category == {"a": (2, 3), "b": (1, 2)}
    assert [p.task_id for p in c.disagreements] == ["lenient-one", "strict-one"]


def test_summary_and_markdown():
    rs = [
        TaskResult(id="a", category="x", status="pass", score=1.0, latency_ms=1000),
        TaskResult(
            id="b",
            category="x",
            status="fail",
            score=0.5,
            latency_ms=3000,
            checks=[{"type": "answer_matches", "ok": False, "detail": "/42/ not found"}],
        ),
        TaskResult(id="c", category="y", status="error", score=0.0, error="429"),
    ]
    s = summarize(rs)
    assert (s["pass_rate"], s["errors"], s["graded"], s["mean_score"]) == (0.5, 1, 2, 0.75)
    assert s["by_category"]["y"]["pass_rate"] is None
    md = markdown(
        {"summary": s, "meta": {"model": "m", "provider": "p", "judge": "j"}, "tasks": [r.to_dict() for r in rs]}
    )
    assert md.startswith("# KestrelBench: 50% (95% CI ") and "n=2)" in md  # n counts graded tasks only
    assert "**b** (fail): /42/ not found" in md and "**c** (error)" in md and "Partial run" in md


# --- end to end through the CLI, offline with the demo provider ---------------------


def test_cli_runs_the_suite_with_the_demo_provider(tmp_path, monkeypatch, capsys):
    import kestrel

    monkeypatch.chdir(tmp_path)  # logs/bench.db lands here
    monkeypatch.setattr(kestrel, "load_dotenv", lambda: None)
    monkeypatch.setenv("KESTREL_MCP", "off")
    out = tmp_path / "results.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "kestrel",
            "bench",
            "run",
            "--provider",
            "demo",
            "--judge",
            "none",
            "--task",
            "arith-percent",
            "--task",
            "files-contact-email",
            "--out",
            str(out),
            "--min-pass-rate",
            "0.5",
        ],
    )
    from kestrel.demo import DemoLLM

    monkeypatch.setattr(DemoLLM.__init__, "__defaults__", (0.0,))  # no artificial "thinking" pause in tests
    with pytest.raises(SystemExit) as exit_info:
        kestrel.main()
    data = json.loads(out.read_text(encoding="utf-8"))
    statuses = {t["id"]: t["status"] for t in data["tasks"]}
    assert statuses["arith-percent"] == "pass"  # the demo calls the real calculator: 409.5
    assert data["meta"]["provider"] == "demo" and out.with_suffix(".md").exists()
    assert exit_info.value.code == (0 if data["summary"]["pass_rate"] >= 0.5 else 1)
    assert "Pass rate" in capsys.readouterr().out
    assert Path("logs/bench.db").exists()


# --- pinned provider, token budget, resume, shard ------------------------------------


class FromProvider(Scripted):
    """Scripted replies that report a given provider, like FallbackLLM after a switch."""

    def __init__(self, provider, *replies):
        super().__init__(*replies)
        self.last_provider = provider


def test_answer_from_another_provider_is_excluded_not_scored(base):
    t = task(checks=({"type": "answer_matches", "pattern": "Canberra"},))
    result = run_task(t, FromProvider("gemini", say("Canberra")), base_workspace=base, expected_provider="groq")
    assert result.status == "excluded" and "gemini" in result.error and result.checks == []
    same = run_task(t, FromProvider("groq", say("Canberra")), base_workspace=base, expected_provider="groq")
    assert same.status == "pass"
    s = summarize([result, same])
    assert (s["pass_rate"], s["graded"], s["excluded"]) == (1.0, 1, 1)


def test_judge_tokens_are_counted(base):
    class CountingLLM(Scripted):
        last_call = type("Info", (), {"input_tokens": 700, "output_tokens": 30})()

    t = task(rubric="Mentions Canberra.", checks=({"type": "no_tools"},))
    judge = Judge(CountingLLM(say('{"score": 1, "reason": "ok"}')), name="j")
    result = run_task(t, Scripted(say("Canberra")), judge=judge, base_workspace=base)
    assert result.judge_tokens == 730 and summarize([result])["judge_tokens_total"] == 730


def test_token_budget_skips_the_rest(base):
    from kestrel.bench.runner import run_suite

    class Costly(Scripted):
        def chat(self, messages, tools=None):
            self.last_call = type(
                "Info",
                (),
                {
                    "provider": "x",
                    "model": "m",
                    "input_tokens": 600,
                    "output_tokens": 0,
                    "finish_reason": "stop",
                    "retries": 0,
                    "attempts": ["x"],
                    "response_model": None,
                },
            )()
            return say("ok")

    tasks = [task(id=f"t{i}", checks=({"type": "no_tools"},)) for i in range(4)]
    results = run_suite(tasks, Costly(), base_workspace=base, token_budget=1000)
    assert [r.status for r in results] == ["pass", "pass", "skipped", "skipped"]
    assert "token budget reached" in results[2].error and summarize(results)["skipped"] == 2


def run_cli(monkeypatch, *argv):
    import kestrel

    monkeypatch.setattr(sys, "argv", ["kestrel", "bench", "run", *argv])
    with pytest.raises(SystemExit) as e:
        kestrel.main()
    return e.value.code


@pytest.fixture
def offline_cli(tmp_path, monkeypatch):
    import kestrel
    from kestrel.demo import DemoLLM

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(kestrel, "load_dotenv", lambda: None)
    monkeypatch.setattr(DemoLLM.__init__, "__defaults__", (0.0,))
    return tmp_path


def test_resume_keeps_finished_tasks_and_runs_the_rest(offline_cli, monkeypatch):
    out = offline_cli / "r.json"
    ids = ["--task", "arith-percent", "--task", "files-contact-email", "--task", "notool-capital"]
    run_cli(monkeypatch, "--provider", "demo", "--judge", "none", *ids, "--out", str(out), "--token-budget", "1")
    first = {t["id"]: t["status"] for t in json.loads(out.read_text(encoding="utf-8"))["tasks"]}
    assert list(first.values()).count("skipped") == 2  # the budget stopped after the first task

    run_cli(monkeypatch, "--provider", "demo", "--judge", "none", *ids, "--resume", str(out))
    second = {t["id"]: t["status"] for t in json.loads(out.read_text(encoding="utf-8"))["tasks"]}
    assert "skipped" not in second.values() and second["arith-percent"] == first["arith-percent"]


def test_resume_refuses_to_mix_providers(offline_cli, monkeypatch):
    out = offline_cli / "r.json"
    run_cli(monkeypatch, "--provider", "demo", "--judge", "none", "--task", "arith-percent", "--out", str(out))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    with pytest.raises(SystemExit, match="would mix models"):
        import kestrel

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "kestrel",
                "bench",
                "run",
                "--provider",
                "groq",
                "--judge",
                "none",
                "--task",
                "arith-percent",
                "--resume",
                str(out),
            ],
        )
        kestrel.main()


def test_shards_split_the_suite(offline_cli, monkeypatch):
    ids = ["--task", "arith-percent", "--task", "files-contact-email", "--task", "notool-capital"]
    seen = []
    for shard in ("1/2", "2/2"):
        out = offline_cli / f"s{shard[0]}.json"
        run_cli(monkeypatch, "--provider", "demo", "--judge", "none", *ids, "--shard", shard, "--out", str(out))
        seen += [t["id"] for t in json.loads(out.read_text(encoding="utf-8"))["tasks"]]
    assert sorted(seen) == ["arith-percent", "files-contact-email", "notool-capital"]


def test_budget_counts_only_billable_tokens():
    from kestrel.bench.runner import billable_tokens

    r = TaskResult(id="a", category="c", status="pass", score=1.0, tokens=3000, judge_tokens=800, cached_tokens=2200)
    assert billable_tokens(r) == 1600


def test_checks_tolerate_typographic_characters(tmp_path):
    """Real answers from the first baseline (2026-10-04) that failed only on Unicode formatting."""
    cases = [
        ("There are **604 800 seconds** in one week.", r"604[, ]?800"),
        ("departs on **12 October 2026 at 19:00**", r"(Oct(ober)?\.? 12|12(th)? Oct|2026-10-12)"),
        ("that file isn’t present in your workspace", r"isn'?t (there|in|present)"),
        ("“buenos días.”", r"buen(os)? d[ií]as"),
    ]
    for answer, pattern in cases:
        assert run_check({"type": "answer_matches", "pattern": pattern}, outcome(tmp_path, answer=answer))[0], answer
    (tmp_path / "outbox").mkdir()
    (tmp_path / "outbox" / "m.md").write_text("To: j@x.y\n\nTuesday at 6 pm works.", encoding="utf-8")
    o = outcome(tmp_path)
    assert run_check({"type": "file_contains", "path": "outbox/*.md", "pattern": "(?=.*tuesday)(?=.*6 ?pm)"}, o)[0]
