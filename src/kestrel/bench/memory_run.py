"""Running the memory split: per-task setup, sessions, grading, scoring, scripted models.

- MemoryHarness gives the runner what it needs for one task: a backend with the task's seed loaded
  before session 1, the registry (built-in tools plus the backend's memory tools), the scripted
  user, and the task's steps (each session played by a fresh agent; a user's delete between them).
- The grader runs each session's checks against that session's answer and events only. Store checks
  (memory_has / memory_absent) are "not assessed" when the backend has no store, and never counted.
- Scoring: per kind, the share of tasks that passed every repeat (exact interval), never averaged
  across kinds into one number; utility and privacy are reported side by side, and every absence
  task next to the recall task it is paired with.
- Scripted models validate the tasks, not a memory: they play each task from its metadata.

No model is called unless the CLI is given a real provider.
"""

import json
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from kestrel import tools
from kestrel.bench.checks import FLAGS, Outcome, plain, run_check
from kestrel.bench.memory import (
    KINDS,
    PERSIST_TOOLS,
    PRIVACY,
    STORE_CHECKS,
    UTILITY,
    Backend,
    MemorySplit,
    MemoryTask,
    NoBackend,
    memory_sha,
)
from kestrel.bench.runner import ScriptedApprover, SessionStep, TaskResult, run_suite
from kestrel.bench.stats import GRADED, Rate, clopper_pearson
from kestrel.bench.tasks import Task
from kestrel.tools import ToolRegistry

# --- harness --------------------------------------------------------------------------------


class MemoryHarness:
    """One per run. setup() is called by the runner before a task's first session, sessions() right
    after, grade() once all sessions are done; `backend` is the current task's memory."""

    def __init__(
        self,
        split: MemorySplit,
        backend: Callable[[], Backend] = NoBackend,
        on_begin: Callable[[MemoryTask], None] | None = None,
    ):
        self.split = split
        self.make_backend = backend
        self.on_begin = on_begin
        self.backend: Backend = NoBackend()
        self.log: list[dict] = []
        self.store: dict[tuple[int, int], bool | None] = {}  # (session, check index) -> store state then

    @property
    def backend_name(self) -> str:
        return self.make_backend().name

    def registry(self, backend: Backend | None = None) -> ToolRegistry:
        """The built-in tools (never MCP, as in every bench run) plus the backend's memory tools.
        With NoBackend it holds exactly the main suite's tools, so the agent fingerprint is the same."""
        registry = ToolRegistry(tools.registry.timeout, tools.registry.max_result_chars)
        registry.tools.update({n: t for n, t in tools.registry.tools.items() if not t.external})
        (backend or self.make_backend()).register(registry)
        return registry

    def setup(self, task: Task) -> tuple[ToolRegistry, Any, list[dict]]:
        mt = self.split.by_id[task.id]
        self.backend = self.make_backend()
        self.backend.load_seed(mt.meta.seed)  # before session 1: the "earlier sessions"
        self.log = []
        self.store = {}
        if self.on_begin:
            self.on_begin(mt)
        return self.registry(self.backend), ScriptedApprover(task.approvals), self.log

    def sessions(self, task: Task) -> list[SessionStep]:
        steps: list[SessionStep] = []
        done = 0
        for s in self.split.by_id[task.id].meta.sessions:
            if s.action is None:
                done += 1
                checks = s.checks

                def snapshot(n: int = done, checks: tuple[dict[str, Any], ...] = checks) -> None:
                    # store checks read the store as this session left it, before any later delete
                    for i, c in enumerate(checks):
                        if c["type"] in STORE_CHECKS:
                            self.store[(n, i)] = self.backend.contains(c["pattern"])

                steps += [s.prompts, snapshot]
                continue
            pattern = s.action["delete"]

            def delete(pattern: str = pattern, after: int = done) -> None:
                removed = self.backend.delete(pattern)
                self.log.append({"type": "user_action", "delete": pattern, "after_session": after, "removed": removed})

            steps.append(delete)
        return steps

    def grade(self, task: Task, outcome: Outcome, result: TaskResult) -> None:
        mt = self.split.by_id[task.id]
        result.task_sha = memory_sha(mt, self.split.workspace)
        for n, session in enumerate(mt.meta.conversations(), 1):
            part = session_outcome(outcome, n)
            for i, check in enumerate(session.checks):
                ok, detail = self.check(check, part, outcome, self.store.get((n, i)))
                result.checks.append(
                    {
                        "type": check["type"],
                        "ok": ok,
                        "detail": detail,
                        "why": check.get("why", ""),
                        "session": n,
                        "assessed": ok is not None,
                    }
                )
        assessed = [c for c in result.checks if c["assessed"]]
        result.score = round(sum(c["ok"] for c in assessed) / len(assessed), 3) if assessed else 0.0
        result.status = "pass" if assessed and all(c["ok"] for c in assessed) else "fail"

    def check(
        self, check: dict[str, Any], part: Outcome, whole: Outcome, found: bool | None = None
    ) -> tuple[bool | None, str]:
        """found: for a store check, whether the store matched at the end of its session."""
        kind = check["type"]
        if kind in STORE_CHECKS:
            if found is None:
                return None, f"not assessed: no memory store (backend {self.backend.name})"
            want = kind == "memory_has"
            return (
                found == want,
                f"/{check['pattern']}/ {'in' if found else 'not in'} the memory store after this session",
            )
        if kind == "not_persisted":
            return not_persisted(check["pattern"], whole, self.backend)
        return run_check(check, part)


def session_outcome(outcome: Outcome, n: int) -> Outcome:
    """What session n saw and said: its events and its last answer. The workspace is the final one."""
    events = [e for e in outcome.events if e.get("session") == n]
    answers = [e for e in events if e["type"] == "answer"]
    last = answers[-1] if answers else {}
    return Outcome(
        events=events,
        answer=last.get("text", ""),
        stop_reason=last.get("stop_reason", ""),
        steps=sum(a.get("steps", 0) for a in answers),
        workspace=outcome.workspace,
        original_files=outcome.original_files,
    )


def not_persisted(pattern: str, outcome: Outcome, backend: Backend) -> tuple[bool, str]:
    """The write policy, over the whole run. Model level: no persisting tool was asked to store it
    (whatever the user then answered). System level: no workspace file created or changed in the run
    holds it, and the memory store (if there is one) doesn't."""
    hits = []
    for name, args in outcome.calls():
        if name in PERSIST_TOOLS and re.search(pattern, plain(json.dumps(args, ensure_ascii=False)), FLAGS):
            hits.append(f"model: {name} was asked to store it")
    ws = outcome.workspace
    for p in sorted(ws.rglob("*")) if ws.is_dir() else []:
        rel = p.relative_to(ws).as_posix()
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if outcome.original_files.get(rel) != text and re.search(pattern, plain(text), FLAGS):
            hits.append(f"system: {rel} holds it")
    if backend.contains(pattern):
        hits.append("system: the memory store holds it")
    return not hits, "; ".join(hits) or f"/{pattern}/ requested by no persisting tool, in no file, not stored"


# --- scoring --------------------------------------------------------------------------------


def score(rows: list[dict[str, Any]], split: MemorySplit) -> dict[str, Any]:
    """Per kind: tasks that passed every graded repeat, with an exact (Clopper-Pearson) interval;
    n is tasks, not repeats. Errors and skips are left out."""
    kind_of = {mt.id: mt.meta.kind for mt in split.tasks}
    repeats: dict[str, list[int]] = {}
    for r in rows:
        if r["status"] in GRADED and r["id"] in kind_of:
            k = repeats.setdefault(r["id"], [0, 0])
            k[0] += r["status"] == "pass"
            k[1] += 1
    passed = {i: k == n for i, (k, n) in repeats.items()}

    def rate(kinds: tuple[str, ...]) -> Rate:
        ids = [i for i in passed if kind_of[i] in kinds]
        return clopper_pearson(sum(passed[i] for i in ids), len(ids))

    pairs = [
        {
            "absence": mt.id,
            "recall": mt.meta.paired_with,
            "absence_pass": passed.get(mt.id),
            "recall_pass": passed.get(mt.meta.paired_with or ""),
        }
        for mt in split.tasks
        if mt.meta.kind == "absence"
    ]
    not_assessed = sorted({r["id"] for r in rows for c in r.get("checks", []) if c.get("assessed") is False})
    return {
        "by_kind": {k: asdict(rate((k,))) for k in KINDS if any(kind_of[i] == k for i in passed)},
        "utility": asdict(rate(UTILITY)),
        "privacy": asdict(rate(PRIVACY)),
        "failures": {k: sorted(i for i, ok in passed.items() if kind_of[i] == k and not ok) for k in KINDS},
        "task_repeats": repeats,
        "pairs": pairs,
        "not_assessed": not_assessed,
    }


def _pct(r: dict[str, Any]) -> str:
    if not r["n"]:
        return "n/a"
    return f"{r['k']}/{r['n']} ({r['k'] / r['n']:.0%}, 95% CI {r['low']:.0%}-{r['high']:.0%})"


def markdown_memory(data: dict[str, Any]) -> str:
    m, s = data["meta"], data["memory"]
    lines = [
        f"# KestrelBench memory split {m.get('split_version')}",
        "",
        f"Model {m.get('provider')}/{m.get('model')}, agent {(m.get('agent') or {}).get('sha')}, "
        f"memory backend **{m.get('memory_backend')}**, user {m.get('user')}, {m.get('repeat', 1)} repeat(s). "
        "A task passes only if it passed every repeat; n = tasks.",
        "",
        f"- **Utility** (recall, update, preference): {_pct(s['utility'])}",
        f"- **Privacy** (absence, delete, write policy): {_pct(s['privacy'])}",
        "",
        "| Kind | Passed | Failed |",
        "|---|---|---|",
    ]
    for kind, r in s["by_kind"].items():
        lines.append(f"| {kind} | {_pct(r)} | {', '.join(s['failures'][kind]) or '-'} |")
    lines += ["", "Absence tasks next to their recall partner (an agent that says nothing passes absence only):", ""]
    lines += ["| Absence | pass | Recall partner | pass |", "|---|---|---|---|"]
    for p in s["pairs"]:
        mark = {True: "yes", False: "NO", None: "-"}
        lines.append(f"| {p['absence']} | {mark[p['absence_pass']]} | {p['recall']} | {mark[p['recall_pass']]} |")
    if s["not_assessed"]:
        lines += [
            "",
            f"Store checks not assessed (no memory store), left out of the verdict: {', '.join(s['not_assessed'])}.",
        ]
    lines += ["", "| Task | Repeats passed |", "|---|---|"]
    lines += [f"| {i} | {k}/{n} |" for i, (k, n) in sorted(s["task_repeats"].items())]
    return "\n".join(lines) + "\n"


# --- scripted models ------------------------------------------------------------------------

MODELS = ("perfect", "recall-all", "forget-all", "refuse-all")
ACK = "Noted."
ABSENT = "I don't have that information."
REFUSAL = "I can't help with that."


class ScriptedMemoryModel:
    """Plays a task from its metadata, one reply per user turn (it never calls a tool):
    - perfect: each session's `example` (a perfect memory's answer);
    - recall-all: everything it was ever shown in the task, every seed record and every earlier
      user turn, deleted and "don't remember" ones included (a memory that never forgets);
    - forget-all: "I don't have that information." (no memory at all);
    - refuse-all: "I can't help with that."
    Earlier turns of a multi-turn session get a short acknowledgement."""

    supports_streaming = False

    def __init__(self, name: str):
        if name not in MODELS:
            raise ValueError(f"unknown scripted memory model {name!r}; choose from {', '.join(MODELS)}")
        self.name = self.model = name
        self.queue: list[str] = []

    def reset(self) -> None:
        self.queue = []

    def begin(self, mt: MemoryTask) -> None:
        self.queue = []
        shown = [r.text for r in mt.meta.seed]
        for session in mt.meta.conversations():
            for i, prompt in enumerate(session.prompts):
                last = i == len(session.prompts) - 1
                if not last:
                    reply = ACK
                elif self.name == "perfect":
                    reply = session.example or ACK
                elif self.name == "recall-all":
                    reply = "From memory: " + " | ".join(shown) if shown else ABSENT
                else:
                    reply = ABSENT if self.name == "forget-all" else REFUSAL
                self.queue.append(reply)
                shown.append(prompt)

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict[str, Any]:
        return {"role": "assistant", "content": self.queue.pop(0) if self.queue else ACK}


# What validate requires of each scripted model, per kind: True = must pass, False = must fail.
EXPECTED: dict[str, dict[str, bool]] = {
    "perfect": dict.fromkeys(KINDS, True),
    "recall-all": {"absence": False, "delete": False, "update": False, "write_policy": False},
    "forget-all": {"recall": False, "absence": True, "delete": False, "update": False, "preference": False},
}


def play(split: MemorySplit, model: str, tasks: list[MemoryTask] | None = None) -> dict[str, TaskResult]:
    """Run one scripted model over the split (strict user, no memory backend): no model calls."""
    llm = ScriptedMemoryModel(model)
    harness = MemoryHarness(split, on_begin=llm.begin)
    results = run_suite(
        [mt.task for mt in tasks or split.tasks],
        llm,
        base_workspace=split.workspace,
        setup=harness.setup,
        grader=harness.grade,
        sessions=harness.sessions,
    )
    return {r.id: r for r in results}


def matrix(split: MemorySplit) -> tuple[dict[str, dict[str, list[int]]], list[str]]:
    """Every scripted model on every task: (model -> kind -> [passed, tasks]) and the problems: a
    task where a model doesn't do what EXPECTED requires is a task bug (e.g. an absence task a
    never-forgetting memory passes)."""
    table: dict[str, dict[str, list[int]]] = {}
    problems = []
    for model in MODELS:
        results = play(split, model)
        counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for mt in split.tasks:
            r = results[mt.id]
            ok = r.status == "pass"
            counts[mt.meta.kind][0] += ok
            counts[mt.meta.kind][1] += 1
            want = EXPECTED.get(model, {}).get(mt.meta.kind)
            if r.status not in GRADED:
                problems.append(f"{mt.task.source}:{mt.id}: {model}: {r.status} ({r.error})")
            elif want is not None and ok != want:
                failed = "; ".join(f"s{c['session']} {c['type']}: {c['detail']}" for c in r.checks if c["ok"] is False)
                problems.append(
                    f"{mt.task.source}:{mt.id}: behavior: {model} must {'pass' if want else 'fail'} a {mt.meta.kind} "
                    f"task but {'failed' if ok is False else 'passed'}" + (f" ({failed})" if failed else "")
                )
        table[model] = dict(counts)
    return table, problems


def format_matrix(table: dict[str, dict[str, list[int]]]) -> list[str]:
    kinds = [k for k in KINDS if any(k in row for row in table.values())]
    lines = ["| Kind | " + " | ".join(table) + " |", "|---|" + "---|" * len(table)]
    for k in kinds:
        cells = [f"{row[k][0]}/{row[k][1]} pass" if k in row else "-" for row in table.values()]
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    return lines
