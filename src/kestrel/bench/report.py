"""Turn task results into a summary, a JSON file, and a Markdown report."""

import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kestrel.bench.runner import TaskResult


def summarize(results: list[TaskResult]) -> dict[str, Any]:
    graded = [r for r in results if r.status in ("pass", "fail")]
    by_cat: dict[str, list[TaskResult]] = defaultdict(list)
    for r in results:
        by_cat[r.category].append(r)

    def rate(rs: list[TaskResult]) -> float | None:
        ok = [r for r in rs if r.status in ("pass", "fail")]
        return round(sum(r.status == "pass" for r in ok) / len(ok), 3) if ok else None

    latencies = [r.latency_ms for r in graded]
    return {
        "tasks": len(results),
        "graded": len(graded),
        "errors": sum(r.status == "error" for r in results),
        "excluded": sum(r.status == "excluded" for r in results),  # answered by another provider
        "skipped": sum(r.status == "skipped" for r in results),  # token budget reached
        "passed": sum(r.status == "pass" for r in results),
        "pass_rate": rate(results),
        "mean_score": round(statistics.mean(r.score for r in graded), 3) if graded else None,
        "by_category": {
            c: {
                "tasks": len(rs),
                "pass_rate": rate(rs),
                "mean_score": round(statistics.mean(r.score for r in rs if r.status in ("pass", "fail")), 3)
                if any(r.status in ("pass", "fail") for r in rs)
                else None,
            }
            for c, rs in sorted(by_cat.items())
        },
        "tokens_total": sum(r.tokens for r in results),
        "judge_tokens_total": sum(r.judge_tokens for r in results),
        "cached_tokens_total": sum(r.cached_tokens for r in results),
        "billable_tokens_total": sum(r.tokens + r.judge_tokens - r.cached_tokens for r in results),
        "tokens_per_task": round(statistics.mean(r.tokens for r in graded)) if graded else None,
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


def markdown(data: dict[str, Any]) -> str:
    s, m = data["summary"], data["meta"]
    lines = [
        f"# KestrelBench: {pct(s['pass_rate'])} pass rate",
        "",
        f"Model `{m.get('model')}` on {m.get('provider')}, judge `{m.get('judge')}`, "
        f"{s['tasks']} tasks ({m.get('subset') or 'all'}), {m.get('finished')}.",
        f"Mean score {s['mean_score']}, {s['errors']} errored, {s.get('excluded', 0)} excluded (other provider), "
        f"{s.get('skipped', 0)} skipped (token budget), {s['tokens_per_task']} tokens/task "
        f"(+{s.get('judge_tokens_total', 0):,} judge tokens in total), "
        f"p50 latency {(s['latency_p50_ms'] or 0) / 1000:.1f} s.",
        "",
        "| Category | Tasks | Pass rate | Mean score |",
        "|---|---:|---:|---:|",
    ]
    for cat, c in s["by_category"].items():
        lines.append(f"| {cat} | {c['tasks']} | {pct(c['pass_rate'])} | {c['mean_score']} |")
    failures = [t for t in data["tasks"] if t["status"] != "pass"]
    if failures:
        lines += ["", "## Not passed", ""]
        for t in failures:
            reasons = [c["detail"] for c in t["checks"] if not c["ok"]]
            if t.get("judge") and (t["judge"].get("score") or 0) < 0.5:
                reasons.append(f"judge {t['judge'].get('score')}: {t['judge'].get('reason')}")
            if t.get("error"):
                reasons.append(f"error: {t['error']}")
            lines.append(f"- **{t['id']}** ({t['status']}): " + "; ".join(r[:200] for r in reasons))
    return "\n".join(lines) + "\n"
