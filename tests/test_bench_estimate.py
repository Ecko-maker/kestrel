"""The dry-run token estimate (`kestrel bench run --dry-run`) decides whether a run fits Groq's free
day, so it must not under-count. It once assumed 32% of tokens were billable (Groq caching the rest);
measured runs bill 64-75%, so the CI subset cost 1.6x its estimate (run 37504156469)."""

import json

from kestrel.bench import cli as bench_cli
from kestrel.bench.tasks import Task, load_tasks, select

PINNED = bench_cli.PINNED_REFERENCE


def billable(rows):
    return sum(r["tokens"] + r.get("judge_tokens", 0) - r.get("cached_tokens", 0) for r in rows)


def test_billable_share_is_not_below_the_pinned_runs_measured_share():
    rows = [r for r in json.loads(PINNED.read_text(encoding="utf-8"))["tasks"] if r["tokens"]]
    raw = sum(r["tokens"] + r.get("judge_tokens", 0) for r in rows)
    assert billable(rows) / raw <= bench_cli.BILLABLE_SHARE  # 0.69 measured


def test_main_split_estimate_reproduces_the_pinned_runs_measured_cost():
    meta = json.loads(PINNED.read_text(encoding="utf-8"))["meta"]
    refs = bench_cli._default_reference("main", meta["provider"], meta["model"])
    assert refs == [PINNED]
    ci = select(load_tasks(), "ci")
    pinned = {r["id"]: r for r in json.loads(PINNED.read_text(encoding="utf-8"))["tasks"]}
    est = bench_cli._estimate([(t, 1) for t in ci], refs)
    assert est["measured"] == len(ci) == 16
    assert est["billable"] == billable(pinned[t.id] for t in ci)  # 26,479; the CI run used 27,713


def test_no_pinned_reference_for_other_models_or_the_safety_split():
    meta = json.loads(PINNED.read_text(encoding="utf-8"))["meta"]
    assert bench_cli._default_reference("safety", meta["provider"], meta["model"]) == []
    assert bench_cli._default_reference("main", "ollama", "qwen2.5:0.5b") == []


def row(task_id, tokens, cached=0, judge=0):
    return {"id": task_id, "category": "c", "status": "pass", "score": 1.0, "repeat": 1, "checks": [],
            "answer": "ok", "tokens": tokens, "judge_tokens": judge, "cached_tokens": cached, "latency_ms": 0.0,
            "steps": 2}  # fmt: skip


def test_zero_cache_counts_as_fully_billable_only_in_files_that_record_caching(tmp_path):
    a, b = (Task(id=i, category="c", prompts=("go",), checks=()) for i in ("a", "b"))
    recorded = tmp_path / "recorded.json"  # caching recorded: b really had no cache hit
    recorded.write_text(json.dumps({"meta": {}, "tasks": [row("a", 2000, cached=1000), row("b", 2000)]}))
    old = tmp_path / "old.json"  # from before cache recording: 0 means unknown
    old.write_text(json.dumps({"meta": {}, "tasks": [row("a", 2000), row("b", 2000)]}))

    assert bench_cli._estimate([(a, 1), (b, 1)], [recorded])["billable"] == 1000 + 2000
    assert bench_cli._estimate([(a, 1), (b, 1)], [old])["billable"] == round(2 * 2000 * bench_cli.BILLABLE_SHARE)
    # a recorded measurement wins over an old one for the same task
    assert bench_cli._estimate([(b, 1)], [old, recorded])["billable"] == 2000
