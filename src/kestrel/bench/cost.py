"""Phase 4 cost report (docs/phase4-plan.md sections 3 and 8.2), from a results file and its trace database.

Per task run, from the llm_call spans of its traces: the list-price cost (prices.toml `list_input` /
`list_output`, the same table for both sides, so a price change can't favour one), Groq billable tokens
(input + output - cached input, the free tier's budget) and who answered (span `kestrel.route`). From the
results rows: latency, and for a routed run the turns the teacher answered and why (report.routing_summary,
the same numbers `bench report` prints; this adds their 95% intervals).

- Local providers (Ollama) cost $0 at the margin: the student's hardware, electricity not counted.
- A non-local call with no list price is "unpriced": the report says so and makes no cost decision.
- The judge isn't in the cost: section 8.2 compares the systems, and the primary metric uses no judge.
- prices.toml has no cached-input price, so cached input is priced as ordinary input on both sides.

With a teacher reference (--teacher), section 8.2's cost rule: per task, D = routed mean cost - teacher mean
cost (each the mean over the task's runs); cost is shown when the 95% paired bootstrap upper bound of
mean(D) is < 0 (10,000 resamples, seed 2026). That is the `--cost-shown` of `bench compare --metric checks`.
The database is opened read-only; nothing is written to it.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kestrel.bench.report import routing_summary
from kestrel.bench.stats import Estimate, bootstrap_ci, ratio_ci
from kestrel.pricing import Prices, cost

LOCAL_PROVIDERS = frozenset({"ollama"})  # own hardware: no per-token price
METERED = "groq"  # the free tier whose daily budget section 8.2 reports
RUN_STATUSES = ("pass", "fail", "error")  # rows that ran; skipped and excluded rows did not
MICRO = 1_000_000  # bootstrap in micro-dollars: a task costs fractions of a cent, and Estimate rounds to 4 places
SHOWN, NOT_SHOWN, UNDECIDED = "yes", "no", "can't decide"


@dataclass
class RunCost:
    id: str
    repeat: int
    status: str
    list_usd: float = 0.0
    billable: int = 0  # METERED tokens counting toward its limits
    calls: dict[str, int] = field(default_factory=dict)  # model calls by route ("student" / "teacher" / "model")
    providers: set[tuple[str, str]] = field(default_factory=set)  # (provider, model) of the non-local calls
    unpriced: set[str] = field(default_factory=set)  # provider/model with no list price
    estimated: bool = False  # some token count was estimated, not reported by the API
    missing_traces: int = 0
    latency_ms: float = 0.0


@dataclass
class CostReport:
    path: str
    meta: dict[str, Any]
    runs: list[RunCost]
    not_run: int  # skipped / excluded rows
    routing: dict[str, Any] | None
    turn_share: Estimate | None  # teacher-answered turns / turns, bootstrap over tasks
    run_share: Estimate | None  # task runs the teacher took part in / task runs
    routing_by_task: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # the rows' routing records

    def per_task(self) -> dict[str, list[RunCost]]:
        tasks: dict[str, list[RunCost]] = defaultdict(list)
        for r in self.runs:
            tasks[r.id].append(r)
        return dict(sorted(tasks.items()))

    def task_mean(self, key: str) -> dict[str, float]:
        return {t: sum(getattr(r, key) for r in rs) / len(rs) for t, rs in self.per_task().items()}

    def problems(self) -> list[str]:
        out = []
        if missing := sum(r.missing_traces for r in self.runs):
            out.append(f"{missing} trace(s) named in the results are not in the database")
        if no_trace := [r for r in self.runs if not r.calls and not r.missing_traces]:
            out.append(f"{len(no_trace)} run(s) have no model call in the traces ({no_trace[0].id})")
        if unpriced := sorted({u for r in self.runs for u in r.unpriced}):
            out.append(f"no list price in prices.toml for {', '.join(unpriced)}")
        return out


def _spans(conn: sqlite3.Connection, trace_id: str) -> list[dict[str, Any]] | None:
    if conn.execute("SELECT 1 FROM traces WHERE trace_id = ?", (trace_id,)).fetchone() is None:
        return None
    rows = conn.execute(
        "SELECT attributes FROM spans WHERE trace_id = ? AND name = 'llm_call' ORDER BY start_time", (trace_id,)
    )
    return [json.loads(a or "{}") for (a,) in rows]


def _run_cost(row: dict[str, Any], conn: sqlite3.Connection, prices: Prices) -> RunCost:
    run = RunCost(row["id"], row.get("repeat", 1), row["status"], latency_ms=row.get("latency_ms") or 0.0)
    for trace_id in row.get("trace_ids") or []:
        spans = _spans(conn, trace_id)
        if spans is None:
            run.missing_traces += 1
            continue
        for a in spans:
            provider, model = a.get("gen_ai.provider.name") or "?", a.get("gen_ai.request.model") or "?"
            route = a.get("kestrel.route") or "model"
            run.calls[route] = run.calls.get(route, 0) + 1
            tokens_in = int(a.get("gen_ai.usage.input_tokens") or 0)
            tokens_out = int(a.get("gen_ai.usage.output_tokens") or 0)
            run.estimated |= bool(a.get("kestrel.usage.estimated"))
            if provider == METERED:
                run.billable += tokens_in + tokens_out - int(a.get("kestrel.usage.cached_input_tokens") or 0)
            if provider in LOCAL_PROVIDERS:
                continue
            run.providers.add((provider, model))
            price = cost(prices, provider, model, tokens_in, tokens_out).list_usd
            if price is None:
                run.unpriced.add(f"{provider}/{model}")
            else:
                run.list_usd += price
    return run


def build(results: dict[str, Any], db: Path, prices: Prices, path: str = "") -> CostReport:
    """The cost of every run in `results`, from the traces in `db` (opened read-only)."""
    if not db.is_file():
        raise FileNotFoundError(f"no trace database at {db}")
    rows = results["tasks"]
    ran = [r for r in rows if r["status"] in RUN_STATUSES]
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        runs = [_run_cost(r, conn, prices) for r in ran]
    finally:
        conn.close()
    routing = routing_summary(ran)
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in ran:
        if r.get("routing"):
            by_task[r["id"]].append(r["routing"])
    turn_share = run_share = None
    if routing:
        ids = sorted(by_task)
        turn_share = ratio_ci(
            [sum(x["turns"] for x in by_task[t]) for t in ids],
            [sum(x["escalated_turns"] for x in by_task[t]) for t in ids],
        )
        run_share = ratio_ci(
            [len(by_task[t]) for t in ids], [sum(1 for x in by_task[t] if x["escalated_turns"]) for t in ids]
        )
    meta = results.get("meta", {})
    return CostReport(path, meta, runs, len(rows) - len(ran), routing, turn_share, run_share, dict(by_task))


@dataclass
class CostComparison:
    diff: Estimate | None  # routed - teacher, micro-dollars per task, paired over the shared tasks
    cost_shown: str  # SHOWN / NOT_SHOWN / UNDECIDED: the --cost-shown of bench compare
    only_teacher: list[str]
    only_routed: list[str]
    problems: list[str]


def compare_cost(teacher: CostReport, routed: CostReport) -> CostComparison:
    """Section 8.2: shown when the 95% paired bootstrap upper bound of mean(routed - teacher) is < 0. Any
    problem on either side (missing traces, unpriced calls, tasks on one side only, a routed teacher that
    isn't the reference's model) leaves it undecided: a cost claim must price exactly the same work."""
    t, r = teacher.task_mean("list_usd"), routed.task_mean("list_usd")
    shared = sorted(set(t) & set(r))
    problems = [f"teacher: {p}" for p in teacher.problems()] + [f"routed: {p}" for p in routed.problems()]
    only_t, only_r = sorted(set(t) - set(r)), sorted(set(r) - set(t))
    if only_t or only_r:
        problems.append(f"tasks on one side only: {len(only_t)} teacher, {len(only_r)} routed")
    ref = {p for x in teacher.runs for p in x.providers}
    if extra := sorted({p for x in routed.runs for p in x.providers} - ref):
        problems.append(f"the routed run called {', '.join('/'.join(p) for p in extra)}, not the teacher reference's")
    diff = bootstrap_ci([(r[k] - t[k]) * MICRO for k in shared])
    verdict = UNDECIDED if diff is None or problems else SHOWN if diff.high < 0 else NOT_SHOWN
    return CostComparison(diff, verdict, only_t, only_r, problems)


# --- rendering ---------------------------------------------------------------------------------------


def _usd(x: float) -> str:
    return f"{'-' if x < 0 else ''}${abs(x):,.4f}"


def _per_1k(micro: float) -> str:
    """Micro-dollars per task as dollars per 1,000 tasks (the same number, scaled to be readable)."""
    return f"{'-' if micro < 0 else ''}${abs(micro) / 1000:,.3f}"


def _p(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(q * len(s) + 0.5) - 1))]


def _share(e: Estimate | None) -> str:
    return "n/a" if e is None else f"{e.value:.1%} (95% CI {e.low:.1%}–{e.high:.1%}, n={e.n} tasks)"


def summary(rep: CostReport) -> dict[str, Any]:
    tasks = rep.per_task()
    usd = rep.task_mean("list_usd")
    billable = rep.task_mean("billable")
    latency = [r.latency_ms for r in rep.runs]
    calls: dict[str, int] = defaultdict(int)
    for r in rep.runs:
        for k, v in r.calls.items():
            calls[k] += v
    return {
        "results": rep.path,
        "provider": rep.meta.get("provider"),
        "model": rep.meta.get("model"),
        "agent": (rep.meta.get("agent") or {}).get("sha"),
        "tasks": len(tasks),
        "task_runs": len(rep.runs),
        "not_run": rep.not_run,
        "list_usd_total": round(sum(r.list_usd for r in rep.runs), 6),
        "list_usd_per_1000_tasks": round(1000 * sum(usd.values()) / len(usd), 6) if usd else None,
        "groq_billable_per_100_tasks": round(100 * sum(billable.values()) / len(billable)) if billable else None,
        "latency_p50_ms": round(statistics.median(latency)) if latency else None,
        "latency_p95_ms": round(p) if (p := _p(latency, 0.95)) is not None else None,
        "model_calls": dict(sorted(calls.items())),
        "estimated_token_runs": sum(r.estimated for r in rep.runs),
        "routing": rep.routing,
        "teacher_turn_share": None if rep.turn_share is None else vars(rep.turn_share),
        "teacher_task_run_share": None if rep.run_share is None else vars(rep.run_share),
        "problems": rep.problems(),
        "per_task": {
            t: {
                "runs": len(rs),
                "list_usd_mean": round(usd[t], 8),
                "groq_billable_mean": round(billable[t]),
                "teacher_turns": sum(x["escalated_turns"] for x in rep.routing_by_task.get(t, [])),
                "turns": sum(x["turns"] for x in rep.routing_by_task.get(t, [])),
            }
            for t, rs in tasks.items()
        },
    }


def markdown(rep: CostReport, cmp: CostComparison | None = None, teacher: CostReport | None = None) -> str:
    s = summary(rep)
    lines = [
        f"# Cost report: {Path(rep.path).name or 'results'}",
        "",
        f"> {s['provider']}/{s['model']}, agent {s['agent']}; {s['tasks']} tasks, {s['task_runs']} task runs"
        + (f" ({s['not_run']} rows not run: skipped or excluded, not costed)" if s["not_run"] else "")
        + ".",
        "> List prices from prices.toml (re-check its verified date before quoting). Local calls cost $0 "
        "(electricity not counted); the judge isn't counted; cached input is priced as ordinary input.",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| List-price cost, all runs | {_usd(s['list_usd_total'])} |",
        f"| List-price cost per 1,000 tasks (mean over tasks of the per-run mean) | "
        f"{_usd(s['list_usd_per_1000_tasks'] or 0)} |",
        f"| Groq billable tokens per 100 tasks (agent only) | {s['groq_billable_per_100_tasks'] or 0:,} |",
        f"| Latency p50 / p95 per task run | {(s['latency_p50_ms'] or 0) / 1000:.1f} s / "
        f"{(s['latency_p95_ms'] or 0) / 1000:.1f} s |",
        f"| Model calls by route | {', '.join(f'{k} {v}' for k, v in s['model_calls'].items()) or 'none'} |",
    ]
    if rep.routing:
        r = rep.routing
        lines += [
            f"| Turns the teacher answered | {r['escalated_turns']} of {r['turns']}: {_share(rep.turn_share)} |",
            f"| Task runs the teacher took part in | {r['escalated_task_runs']} of {r['task_runs']}: "
            f"{_share(rep.run_share)} |",
            f"| Escalations by reason | {', '.join(f'{k} {v}' for k, v in r['by_reason'].items()) or 'none'} |",
            f"| Escalations by kind | {', '.join(f'{k} {v}' for k, v in r['by_category'].items()) or 'none'} |",
        ]
    else:
        lines.append("| Routing | not a routed run: every call is the model under test's |")
    if s["estimated_token_runs"]:
        lines.append(f"| Runs with estimated token counts | {s['estimated_token_runs']} |")
    lines.append("")
    if cmp is not None and teacher is not None:
        lines += comparison_lines(cmp, teacher, rep)
    for p in s["problems"]:
        lines.append(f"**Problem:** {p}")
    if s["problems"]:
        lines.append("")
    tmean = teacher.task_mean("list_usd") if teacher is not None else {}
    head = "| Task | Runs | $ per 1,000 runs | Groq billable per run | Teacher turns |"
    sep = "|---|---|---|---|---|"
    if tmean:
        head, sep = head + " Teacher reference, $ per 1,000 runs |", sep + "---|"
    lines += ["## Per task", "", head, sep]
    for t, runs in rep.per_task().items():
        usd = sum(r.list_usd for r in runs) / len(runs)
        bill = sum(r.billable for r in runs) / len(runs)
        rr = rep.routing_by_task.get(t, [])
        turns = f"{sum(x['escalated_turns'] for x in rr)} of {sum(x['turns'] for x in rr)}" if rr else "–"
        cells = [t, str(len(runs)), _per_1k(usd * MICRO), f"{bill:,.0f}", turns]
        if tmean:
            cells.append(_per_1k(tmean[t] * MICRO) if t in tmean else "–")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def comparison_lines(cmp: CostComparison, teacher: CostReport, routed: CostReport) -> list[str]:
    ts, rs = summary(teacher), summary(routed)
    lines = [
        "## Cost against the teacher (docs/phase4-plan.md section 8.2)",
        "",
        f"Teacher reference {Path(teacher.path).name}: {_usd(ts['list_usd_per_1000_tasks'] or 0)} per 1,000 tasks, "
        f"{ts['groq_billable_per_100_tasks'] or 0:,} Groq billable per 100 tasks.",
        f"Routed: {_usd(rs['list_usd_per_1000_tasks'] or 0)} per 1,000 tasks, "
        f"{rs['groq_billable_per_100_tasks'] or 0:,} Groq billable per 100 tasks.",
    ]
    if cmp.diff is not None:
        d = cmp.diff
        lines.append(
            f"Routed - teacher, per 1,000 tasks: {_per_1k(d.value)} (95% CI {_per_1k(d.low)} to {_per_1k(d.high)}, "
            f"n={d.n} tasks, paired bootstrap 10,000 / seed 2026)."
        )
    label = {SHOWN: "SHOWN (upper bound < 0)", NOT_SHOWN: "NOT SHOWN (upper bound >= 0)", UNDECIDED: "CAN'T DECIDE"}
    lines.append(f"**Cost lower than the teacher's: {label[cmp.cost_shown]}.**")
    if cmp.cost_shown == UNDECIDED:
        lines.append("Fix the problems below and run this again; don't pass --cost-shown until it decides.")
    else:
        lines.append(f"For `kestrel bench compare --metric checks`: `--cost-shown {cmp.cost_shown}`")
    lines.append("")
    return lines
