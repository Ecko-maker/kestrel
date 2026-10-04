"""Turn task results into a summary, a JSON file, and a Markdown report.

The summary is recomputed from the task list (not trusted from the file), so older results files
get confidence intervals too, without being rewritten.
"""

import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kestrel.bench.runner import TaskResult
from kestrel.bench.stats import Estimate, category_cis, pass_rate_ci, per_task_pass, repeat_spread


def _rate(rs: list[dict[str, Any]]) -> float | None:
    """Pass rate: the mean over tasks of each task's pass fraction (= passed/graded without repeats)."""
    p = per_task_pass(rs)
    return round(sum(p.values()) / len(p), 3) if p else None


def _est(e: Estimate | None) -> dict[str, Any] | None:
    return None if e is None else {"value": e.value, "low": e.low, "high": e.high, "n": e.n}


def summarize(results: list[TaskResult] | list[dict[str, Any]]) -> dict[str, Any]:
    rows = [r.to_dict() if isinstance(r, TaskResult) else r for r in results]
    graded = [r for r in rows if r["status"] in ("pass", "fail")]
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    cis = category_cis(rows)

    def mean_score(rs: list[dict[str, Any]]) -> float | None:
        ok = [r["score"] for r in rs if r["status"] in ("pass", "fail")]
        return round(statistics.mean(ok), 3) if ok else None

    latencies = [r["latency_ms"] for r in graded]
    return {
        "tasks": len({r["id"] for r in rows}),
        "runs": len(rows),
        "graded": len(graded),
        "errors": sum(r["status"] == "error" for r in rows),
        "excluded": sum(r["status"] == "excluded" for r in rows),  # answered by another provider
        "skipped": sum(r["status"] == "skipped" for r in rows),  # token budget or repeated errors
        "passed": sum(r["status"] == "pass" for r in rows),
        "pass_rate": _rate(rows),
        "pass_rate_ci": _est(pass_rate_ci(rows)),
        "mean_score": mean_score(rows),
        "by_category": {
            c: {
                "tasks": len({r["id"] for r in rs}),
                "pass_rate": _rate(rs),
                "pass_rate_ci": _est(cis[c]["ci"]),
                "too_few": cis[c]["too_few"],
                "mean_score": mean_score(rs),
            }
            for c, rs in sorted(by_cat.items())
        },
        "repeats": repeat_spread(rows),
        "tokens_total": sum(r["tokens"] for r in rows),
        "judge_tokens_total": sum(r.get("judge_tokens", 0) for r in rows),
        "cached_tokens_total": sum(r.get("cached_tokens", 0) for r in rows),
        "billable_tokens_total": sum(r["tokens"] + r.get("judge_tokens", 0) - r.get("cached_tokens", 0) for r in rows),
        "tokens_per_task": round(statistics.mean(r["tokens"] for r in graded)) if graded else None,
        "latency_p50_ms": round(statistics.median(latencies)) if latencies else None,
    }


def write_results(path: Path, results: list[TaskResult], meta: dict[str, Any]) -> dict[str, Any]:
    data = {
        "meta": {**meta, "finished": datetime.now(UTC).isoformat(timespec="seconds")},
        "summary": summarize(results),
        "tasks": [r.to_dict() for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return data


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def fmt_ci(ci: dict[str, Any] | None) -> str:
    """71% (95% CI 62-79%, n=100), with an en dash."""
    return "n/a (nothing graded)" if ci is None else Estimate(**ci).fmt()


def markdown(data: dict[str, Any]) -> str:
    s, m = summarize(data["tasks"]), data["meta"]
    judge = f"`{m.get('judge')}` ({m.get('judge_version') or 'v1'})" if m.get("judge") else "none"
    lines = [
        f"# KestrelBench: {fmt_ci(s['pass_rate_ci'])}",
        "",
        f"Model `{m.get('model')}` on {m.get('provider')}, judge {judge}, "
        f"{s['tasks']} tasks ({m.get('subset') or 'all'}), {m.get('finished')}.",
        f"Mean score {s['mean_score']}, {s['errors']} errored, {s['excluded']} excluded (other provider), "
        f"{s['skipped']} skipped (budget or repeated errors), {s['tokens_per_task']} tokens/task "
        f"(+{s['judge_tokens_total']:,} judge tokens in total), "
        f"p50 latency {(s['latency_p50_ms'] or 0) / 1000:.1f} s.",
        "",
        "The interval is a bootstrap over tasks: how far the score could move with a different draw of "
        "similar tasks. Errors, exclusions and skips are not graded and not in n.",
    ]
    if s["errors"] or s["skipped"]:
        lines += ["", f"**Partial run:** {s['graded']} of {s['runs']} runs were graded; the score covers only those."]
    if rep := s["repeats"]:
        rates = ", ".join(f"run {k}: {pct(v)}" for k, v in rep["pass_rates"].items())
        lines += [
            "",
            f"**Run to run** ({len(rep['repeats'])} repeats): {rates}. "
            f"{len(rep['flaky'])} tasks changed outcome between repeats"
            + (f": {', '.join(rep['flaky'])}." if rep["flaky"] else "."),
        ]
    lines += ["", "| Category | Tasks | Pass rate (95% CI) | Mean score |", "|---|---:|---|---:|"]
    flat = False
    for cat, c in s["by_category"].items():
        note = " *too few tasks to compare*" if c["too_few"] else ""
        if (ci := c["pass_rate_ci"]) and ci["low"] == ci["high"]:
            note, flat = note + " †", True
        lines.append(f"| {cat} | {c['tasks']} | {fmt_ci(c['pass_rate_ci'])}{note} | {c['mean_score']} |")
    if flat:
        lines += [
            "",
            "† Every task in this category had the same outcome, so resampling can't vary the score and the "
            "interval has zero width. That understates the uncertainty: with 0 failures in n tasks, the true "
            "failure rate could still be up to about 3/n (the 'rule of three').",
        ]
    failures = [t for t in data["tasks"] if t["status"] != "pass"]
    if failures:
        lines += ["", "## Not passed", ""]
        for t in failures:
            reasons = [c["detail"] for c in t["checks"] if not c["ok"]]
            if t.get("judge") and (t["judge"].get("score") or 0) < 0.5:
                reasons.append(f"judge {t['judge'].get('score')}: {t['judge'].get('reason')}")
            if t.get("error"):
                reasons.append(f"error: {t['error']}")
            run = f" run {t['repeat']}" if s["repeats"] else ""
            lines.append(f"- **{t['id']}**{run} ({t['status']}): " + "; ".join(r[:200] for r in reasons))
    return "\n".join(lines) + "\n"
