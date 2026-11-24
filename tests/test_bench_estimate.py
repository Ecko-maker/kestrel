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


# --- --token-budget: no task starts if its expected cost would pass it ----------------------------------------


class Costs:
    """A model whose nth call costs the nth number."""

    def __init__(self, *costs):
        self.costs, self.calls = list(costs), 0

    def chat(self, messages, tools=None):
        cost = self.costs[min(self.calls, len(self.costs) - 1)]
        self.calls += 1
        self.last_call = type("Info", (), {"provider": "x", "model": "m", "input_tokens": cost, "output_tokens": 0,
            "finish_reason": "stop", "retries": 0, "attempts": ["x"], "response_model": None})()  # fmt: skip
        return {"role": "assistant", "content": "ok"}


def budget_run(tmp_path, llm, ids, budget, costs=None):
    from kestrel.bench.runner import run_suite

    tasks = [Task(id=i, category="c", prompts=("go",), checks=({"type": "no_tools"},)) for i in ids]
    return run_suite(tasks, llm, base_workspace=tmp_path, token_budget=budget, costs=costs)


def test_a_task_expected_to_pass_the_budget_is_not_started_nor_the_rest(tmp_path):
    llm = Costs(600)
    results = budget_run(tmp_path, llm, ["a", "big", "c"], 1000, {"a": 600, "big": 500, "c": 100})
    assert [r.status for r in results] == ["pass", "skipped", "skipped"] and llm.calls == 1
    assert results[1].error == "token budget reached (600 of 1,000; big is expected to cost 500)"
    assert results[2].error == results[1].error  # c would fit: skipped too, so a partial file isn't the cheap tasks


def test_a_first_task_bigger_than_the_budget_is_not_started(tmp_path):
    llm = Costs(600)
    results = budget_run(tmp_path, llm, ["a", "b"], 500, {"a": 501, "b": 1})
    assert [r.status for r in results] == ["skipped", "skipped"] and llm.calls == 0


def test_a_task_that_exactly_fits_starts(tmp_path):
    assert [r.status for r in budget_run(tmp_path, Costs(500), ["a", "b"], 1000, {"a": 500, "b": 500})] == ["pass"] * 2


def test_what_a_task_cost_earlier_in_the_call_counts(tmp_path):
    llm = Costs(600)
    results = budget_run(tmp_path, llm, ["a", "a"], 1000, {"a": 100})  # expected 100, measured 600
    assert [r.status for r in results] == ["pass", "skipped"] and llm.calls == 1


def test_the_b4_top_up_would_have_stopped_inside_its_budget(tmp_path):
    """2026-11-22: 18,208 spent against a budget of 5,674, on one task expected at about 18k."""
    llm = Costs(18_208)
    assert budget_run(tmp_path, llm, ["web-weather-tokyo"], 5_674, {"web-weather-tokyo": 9_000})[0].status == "skipped"
    assert llm.calls == 0


def test_a_dropped_attempt_is_not_retried_past_the_budget(tmp_path, monkeypatch):
    from kestrel.bench import runner
    from kestrel.bench.quota import QuotaWait
    from kestrel.llm import DAILY_LIMIT

    runs = []

    def limited(task, *args):
        runs.append(task.id)
        error = f"{DAILY_LIMIT} (asks to wait 60s)"
        return runner.TaskResult(id=task.id, category="c", status="error", score=0.0, tokens=400, error=error)

    monkeypatch.setattr(runner, "run_task", limited)
    quota = QuotaWait(total=1, log=lambda line: None, sleep=lambda s: None)
    task = Task(id="a", category="c", prompts=("go",), checks=())
    # 400 spent after the first attempt: a second one is expected to reach 1,100, so it isn't made
    results = runner.run_suite([task], None, token_budget=1000, costs={"a": 700}, quota=quota)
    assert runs == ["a"] and results[0].status == "skipped" and "while waiting for quota" in results[0].error


def test_expected_costs_use_the_worst_measured_run_and_the_average_for_the_rest(tmp_path):
    a, b = (Task(id=i, category="c", prompts=("go",), checks=()) for i in ("a", "b"))
    cheap, dear = tmp_path / "cheap.json", tmp_path / "dear.json"
    cheap.write_text(json.dumps({"meta": {}, "tasks": [row("a", 2000, cached=1500)]}))
    dear.write_text(json.dumps({"meta": {}, "tasks": [row("a", 3000, cached=100)]}))
    costs = bench_cli._task_costs([a, b], [dear, cheap])
    assert costs == {"a": 2900, "b": round(bench_cli.TOKENS_PER_TASK_ESTIMATE * bench_cli.BILLABLE_SHARE)}
    later = bench_cli.TaskResult(id="b", category="c", status="pass", score=1.0, tokens=9000)
    assert bench_cli._task_costs([a, b], [cheap], [later]) == {"a": 500, "b": 9000}
