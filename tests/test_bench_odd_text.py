"""Odd characters in model-written text must never stop a run (found by the routed main suite, 2026-11-04):
U+2028 in a tool argument crashed the run in the bench's own audit reader. Offline: scripted models."""

import json
import sys
from types import SimpleNamespace

import pytest

from kestrel.approval import ApprovalGate
from kestrel.bench import calibrate as cal
from kestrel.bench.quota import QuotaWait
from kestrel.bench.report import write_results
from kestrel.bench.runner import _audit, run_suite, run_task
from kestrel.bench.tasks import ApprovalRule, Task
from kestrel.tracing import Tracer

# Every character str.splitlines() breaks on that json.dumps(ensure_ascii=False) leaves as it is in a string.
LINE_BREAKS = [" ", " ", "\x85"]


def call(name, **args):
    arguments = json.dumps(args)
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": f"c-{name}", "type": "function", "function": {"name": name, "arguments": arguments}}],
    }


class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, messages, tools=None):
        return self.replies.pop(0) if self.replies else {"role": "assistant", "content": "Done."}


@pytest.fixture
def base(tmp_path):
    ws = tmp_path / "base"
    ws.mkdir()
    (ws / "todo.md").write_text("- [ ] Book dentist appointment\n", encoding="utf-8")
    return ws


def gated_task(id="t"):
    return Task(id=id, category="c", prompts=("go",), checks=(), approvals=(ApprovalRule("append_to_file", "approve"),))


@pytest.mark.parametrize("odd", [*LINE_BREAKS, "\ud83d"], ids=["u2028", "u2029", "u0085", "lone-surrogate"])
def test_odd_text_in_a_gated_tool_argument_does_not_stop_the_task(base, odd):
    """The 11-04 crash: the task reaches a verdict, and its audit entry says what happened."""
    llm = Scripted(call("append_to_file", path="todo.md", content=f"- [ ] one{odd}two"))
    result = run_task(gated_task(), llm, base_workspace=base)
    assert result.status == "pass" and result.error is None
    assert [(a["tool"], a["decision"]) for a in result.approvals] == [("append_to_file", "approved")]


@pytest.mark.parametrize("odd", [*LINE_BREAKS, "\ud83d"], ids=["u2028", "u2029", "u0085", "lone-surrogate"])
def test_odd_text_reaches_the_trace_and_the_results_file(base, tmp_path, odd):
    """The rest of a real run's path: the traces database and the results file, in the argument and the answer."""
    llm = Scripted(
        call("append_to_file", path="todo.md", content=f"- [ ] one{odd}two"),
        {"role": "assistant", "content": f"Added one{odd}two."},
    )
    result = run_task(gated_task(), llm, tracer=Tracer(tmp_path / "traces.db"), base_workspace=base)
    assert result.status == "pass" and result.trace_ids
    out = tmp_path / "results.json"
    write_results(out, [result], {})
    row = json.loads(out.read_text(encoding="utf-8"))["tasks"][0]
    assert row["status"] == "pass" and row["answer"] == f"Added one{odd}two."  # read back unchanged


@pytest.mark.parametrize("odd", LINE_BREAKS)
def test_the_audit_log_keeps_one_entry_per_line_and_every_argument(tmp_path, odd):
    """Any reader can split the log on lines, and the text comes back unchanged."""
    log = tmp_path / "approvals.jsonl"
    gate = ApprovalGate(log_path=log)
    gate.record("append_to_file", {"content": f"one{odd}two"}, "approved")
    gate.record("append_to_file", {"content": "three"}, "rejected")
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["args"]["content"] == f"one{odd}two"


def test_the_audit_reader_handles_logs_written_before_the_fix(tmp_path):
    """Those hold a raw U+2028 inside a string: one entry, not two broken ones."""
    log = tmp_path / "approvals.jsonl"
    raw = json.dumps({"tool": "send", "args": {"text": "a b"}, "decision": "approved"}, ensure_ascii=False)
    log.write_text(raw + "\n", encoding="utf-8")
    assert _audit(log) == [{"tool": "send", "decision": "approved"}]


def test_a_label_note_with_a_line_separator_loads(tmp_path):
    labels = tmp_path / "human.jsonl"
    labels.write_text(json.dumps({"id": "t", "note": "a b"}, ensure_ascii=False) + "\n", encoding="utf-8")
    assert cal.load_labels(labels) == [{"id": "t", "note": "a b"}]


def test_a_run_that_crashes_saves_what_finished_and_still_stops(base, monkeypatch):
    """Finished runs were lost with the process (30.5k tokens on 11-04). The crash stays loud: it isn't
    turned into a graded row, and the crashed task isn't in the checkpoint, so --resume runs it again."""
    from kestrel.bench import runner

    real = runner.run_task

    def crash_on_second(task, *args, **kwargs):
        if task.id == "second":
            raise RuntimeError("a bug in the bench")
        return real(task, *args, **kwargs)

    monkeypatch.setattr(runner, "run_task", crash_on_second)
    saved: list[list[str]] = []
    quota = QuotaWait(total=3, checkpoint=lambda done: saved.append([r.id for r in done]))
    tasks = [Task(id=i, category="c", prompts=("go",), checks=()) for i in ("first", "second", "third")]
    with pytest.raises(RuntimeError, match="a bug in the bench"):
        run_suite(tasks, Scripted(), base_workspace=base, quota=quota)
    assert saved == [["first"]]


def test_a_crash_without_a_checkpoint_still_stops(base, monkeypatch):
    from kestrel.bench import runner

    def crash(*args, **kwargs):
        raise RuntimeError("a bug in the bench")

    monkeypatch.setattr(runner, "run_task", crash)
    with pytest.raises(RuntimeError, match="a bug in the bench"):
        run_suite([Task(id="t", category="c", prompts=("go",), checks=())], Scripted(), base_workspace=base)


def test_the_cli_saves_a_resumable_file_on_a_crash_and_resume_runs_only_the_rest(tmp_path, monkeypatch):
    """What 11-04 lacked: no results file existed, so nothing could be resumed."""
    import kestrel
    from kestrel.bench import cli as bench_cli
    from kestrel.bench import runner

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(kestrel, "load_dotenv", lambda: None)
    monkeypatch.setenv("KESTREL_MCP", "off")
    llm = Scripted()
    llm.llms = [SimpleNamespace(model="openai/gpt-oss-120b")]
    monkeypatch.setattr(bench_cli, "build_llm", lambda providers, model=None: (llm, []))
    real, ran = runner.run_task, []

    def crash_on_second_task(task, *args, **kwargs):
        if task.id == "time-weekday-newyork":
            raise RuntimeError("a bug in the bench")
        return real(task, *args, **kwargs)

    def counted(task, *args, **kwargs):
        ran.append(task.id)
        return real(task, *args, **kwargs)

    out = tmp_path / "r.json"
    ids = "time-weekday-newyork,arith-percent"
    argv = ["kestrel", "bench", "run", "--tasks", ids, "--judge", "none", "--wait-for-quota"]
    monkeypatch.setattr(runner, "run_task", crash_on_second_task)
    monkeypatch.setattr(sys, "argv", [*argv, "--out", str(out)])
    with pytest.raises(RuntimeError, match="a bug in the bench"):
        kestrel.main()
    saved = {t["id"]: t["status"] for t in json.loads(out.read_text(encoding="utf-8"))["tasks"]}
    assert saved["time-weekday-newyork"] == "skipped" and saved["arith-percent"] in ("pass", "fail")

    monkeypatch.setattr(runner, "run_task", counted)
    monkeypatch.setattr(sys, "argv", [*argv, "--resume", str(out)])
    with pytest.raises(SystemExit) as e:
        kestrel.main()
    assert e.value.code == 0 and ran == ["time-weekday-newyork"]
    final = {t["id"]: t["status"] for t in json.loads(out.read_text(encoding="utf-8"))["tasks"]}
    assert final["arith-percent"] == saved["arith-percent"] and final["time-weekday-newyork"] != "skipped"
