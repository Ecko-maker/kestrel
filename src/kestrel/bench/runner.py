"""Run KestrelBench tasks: fresh agent and workspace per task, scripted approvals, checks, judge."""

import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kestrel import tools
from kestrel.agent import Agent
from kestrel.approval import ApprovalGate, Decision
from kestrel.bench.checks import FLAGS, Outcome, run_check
from kestrel.bench.judge import Judge
from kestrel.bench.tasks import WORKSPACE_DIR, ApprovalRule, Task, task_sha
from kestrel.tracing import Tracer

# Tool results in the judge's log are cut to this length: judge v2 (2026-10-05) raised it from 600 so the
# judge can check faithfulness against what the traces and events keep (2,000 characters).
MAX_RESULT_CHARS_IN_LOG = 2_000


class ScriptedApprover:
    """Answers approval requests the way the task says. Anything unexpected is rejected,
    the same safe default Kestrel uses when no one is there to ask."""

    def __init__(self, rules: tuple[ApprovalRule, ...]):
        self.rules = rules
        self.used: dict[int, int] = {}
        self.requests: list[dict] = []

    def review(self, tool_name: str, args: dict, preview: str, *, allow_session: bool = False) -> Decision:
        self.requests.append({"type": "approval_required", "tool": tool_name, "args": dict(args)})
        for i, rule in enumerate(self.rules):
            if rule.tool != tool_name:
                continue
            if not all(re.search(p, str(args.get(k, "")), FLAGS) for k, p in rule.args.items()):
                continue
            if rule.times is not None and self.used.get(i, 0) >= rule.times:
                continue
            self.used[i] = self.used.get(i, 0) + 1
            return Decision("approved") if rule.decision == "approve" else Decision("rejected", reason=rule.reason)
        return Decision("rejected", reason="Not now, thanks.")


@dataclass
class TaskResult:
    id: str
    category: str
    # pass / fail are graded. Not graded: "error" (model or judge unreachable), "excluded"
    # (answered by a provider other than the one under test), "skipped" (token budget reached).
    status: str
    score: float  # 0..1: checks passed (and judge score, if graded)
    checks: list[dict] = field(default_factory=list)  # {type, ok, detail}
    judge: dict | None = None  # {score, reason}
    answer: str = ""
    tool_calls: list[str] = field(default_factory=list)
    steps: int = 0
    tokens: int = 0  # the agent's tokens
    judge_tokens: int = 0
    cached_tokens: int = 0  # agent + judge input served from the provider's cache (not rate-limited on Groq)
    latency_ms: float = 0.0
    providers: list[str] = field(default_factory=list)
    trace_ids: list[str] = field(default_factory=list)
    error: str | None = None
    repeat: int = 1  # 1..N with --repeat; (id, repeat) identifies a run
    tool_log: list[str] = field(default_factory=list)  # exactly what the judge saw, for labeling and re-judging
    task_sha: str = ""  # version of the task definition + fixture workspace, so runs are only compared like for like

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _prepare_workspace(task: Task, base: Path) -> tuple[Path, dict[str, str]]:
    root = Path(tempfile.mkdtemp(prefix=f"kbench-{task.id}-")) / "workspace"
    if base.is_dir():
        shutil.copytree(base, root)
    else:
        root.mkdir(parents=True)
    for rel, content in task.files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    originals = {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8", errors="replace")
        for p in root.rglob("*")
        if p.is_file()
    }
    return root, originals


def _tool_log(events: list[dict]) -> list[str]:
    calls = {e.get("call_id"): e for e in events if e["type"] == "tool_call"}
    lines = []
    for r in (e for e in events if e["type"] == "tool_result"):
        call = calls.get(r.get("call_id"), {})
        state = "ran" if r.get("ran") else f"not run ({r.get('decision') or 'refused'})"
        result = r.get("result", "")[:MAX_RESULT_CHARS_IN_LOG]
        lines.append(f"- {r.get('name')}({call.get('arguments', '')}) [{state}] -> {result}")
    return lines


def run_task(
    task: Task,
    llm: Any,
    judge: Judge | None = None,
    tracer: Tracer | None = None,
    base_workspace: Path = WORKSPACE_DIR,
    expected_provider: str | None = None,
    repeat: int = 1,
) -> TaskResult:
    """Run one task. With expected_provider, a task answered (even partly) by any other provider
    is marked "excluded" and left out of the score, so results never mix models."""
    workspace, originals = _prepare_workspace(task, base_workspace)
    approver = ScriptedApprover(task.approvals)
    events: list[dict] = []
    previous_workspace = tools.WORKSPACE
    tools.WORKSPACE = workspace.resolve()
    started = time.perf_counter()
    try:
        agent = Agent(
            llm,
            max_steps=task.max_steps,
            tracer=tracer,
            on_event=events.append,
            gate=ApprovalGate(approver, log_path=workspace.parent / "approvals.jsonl"),
        )
        results = [agent.run(prompt) for prompt in task.prompts]
    finally:
        tools.WORKSPACE = previous_workspace
    latency = (time.perf_counter() - started) * 1000
    last = results[-1]
    events.extend(approver.requests)

    outcome = Outcome(
        events=events,
        answer=last.text,
        stop_reason=last.stop_reason,
        steps=sum(r.steps for r in results),
        workspace=workspace,
        original_files=originals,
    )
    result = TaskResult(
        id=task.id,
        category=task.category,
        status="fail",
        score=0.0,
        answer=last.text,
        tool_calls=[f"{n}({a})" for n, a in outcome.calls()],
        steps=outcome.steps,
        tokens=sum(r.tokens for r in results),
        cached_tokens=sum(r.cached_tokens for r in results),
        latency_ms=round(latency, 1),
        providers=sorted({p for r in results for p in r.providers}),
        trace_ids=[r.trace_id for r in results if r.trace_id],
        repeat=repeat,
        tool_log=_tool_log(events),
        task_sha=task_sha(task, base_workspace),
    )
    if any(r.stop_reason == "error" for r in results):  # the model couldn't be reached: not the agent's fault
        result.status, result.error = "error", next(r.text for r in results if r.stop_reason == "error")
        shutil.rmtree(workspace.parent, ignore_errors=True)
        return result
    others = [p for p in result.providers if p != expected_provider]
    if expected_provider and others:  # a fallback answered: not the model under test
        result.status = "excluded"
        result.error = f"answered by {', '.join(others)}, not the provider under test ({expected_provider})"
        shutil.rmtree(workspace.parent, ignore_errors=True)
        return result

    for check in task.checks:
        ok, detail = run_check(check, outcome)
        result.checks.append({"type": check["type"], "ok": ok, "detail": detail, "why": check.get("why", "")})
    check_score = sum(c["ok"] for c in result.checks) / len(result.checks) if result.checks else 1.0
    checks_pass = all(c["ok"] for c in result.checks)

    judge_score: float | None = None
    if task.rubric and judge is not None:
        verdict = judge.grade(list(task.prompts), task.rubric, result.tool_log, last.text)
        result.judge = {"score": verdict.score, "reason": verdict.reason, "judge": judge.name, "version": judge.version}
        result.judge_tokens = verdict.tokens
        result.cached_tokens += verdict.cached_tokens
        judge_score = verdict.score
        if verdict.score is None:
            result.status, result.error = "error", verdict.reason
            result.score = check_score
            shutil.rmtree(workspace.parent, ignore_errors=True)
            return result

    result.score = round(check_score if judge_score is None else (check_score + judge_score) / 2, 3)
    result.status = "pass" if checks_pass and (judge_score is None or judge_score >= 0.5) else "fail"
    shutil.rmtree(workspace.parent, ignore_errors=True)
    return result


def billable_tokens(result: TaskResult) -> int:
    """Tokens that count toward a provider's limits: everything except cached input."""
    return result.tokens + result.judge_tokens - result.cached_tokens


def run_suite(
    tasks: list[Task],
    llm: Any,
    judge: Judge | None = None,
    tracer: Tracer | None = None,
    base_workspace: Path = WORKSPACE_DIR,
    pause: float = 0.0,
    on_result: Callable[[TaskResult], None] | None = None,
    expected_provider: str | None = None,
    token_budget: int | None = None,
    repeats: list[int] | None = None,
    stop_after_errors: int | None = None,
) -> list[TaskResult]:
    """Run tasks in order (repeats[i] is task i's repeat number). The rest are marked "skipped", to
    resume later, once either:
    - token_budget: the agent + judge tokens spent (excluding cached input, which Groq doesn't
      rate-limit) reach it, since free tiers cap tokens per day;
    - stop_after_errors: that many tasks in a row errored, which almost always means the provider's
      rate limit or an outage; carrying on would only turn the rest into errors too."""
    results: list[TaskResult] = []
    spent = 0
    errors_in_a_row = 0
    for i, task in enumerate(tasks):
        repeat = repeats[i] if repeats else 1
        stop = None
        if token_budget is not None and spent >= token_budget:
            stop = f"token budget reached ({spent:,} of {token_budget:,})"
        elif stop_after_errors and errors_in_a_row >= stop_after_errors:
            stop = f"stopped after {errors_in_a_row} errors in a row (likely a rate limit)"
        if stop:
            result = TaskResult(
                id=task.id, category=task.category, status="skipped", score=0.0, error=stop, repeat=repeat
            )
        else:
            if i and pause:
                time.sleep(pause)  # spreads requests out under free-tier per-minute limits
            result = run_task(task, llm, judge, tracer, base_workspace, expected_provider, repeat)
            spent += billable_tokens(result)
            errors_in_a_row = errors_in_a_row + 1 if result.status == "error" else 0
        results.append(result)
        if on_result:
            on_result(result)
    return results
