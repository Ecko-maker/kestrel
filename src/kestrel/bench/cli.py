"""`kestrel bench ...`: run KestrelBench, compare runs, label answers, calibrate and re-run the judge."""

import argparse
import fnmatch
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from kestrel.bench import calibrate as cal
from kestrel.bench.guards import make_grader
from kestrel.bench.judge import JUDGE_VERSION, Judge, build_request
from kestrel.bench.lock import LockError, results_lock, unlock
from kestrel.bench.report import markdown, pct, summarize, write_results
from kestrel.bench.runner import TaskResult, agent_fingerprint, billable_tokens, run_suite
from kestrel.bench.safety import (
    SAFETY_DIR,
    SAFETY_VERSION,
    SafetyMeta,
    SafetySplit,
    load_safety,
    score,
    score_dict,
    score_lines,
)
from kestrel.bench.scripted import MODELS, ScriptedSafetyModel
from kestrel.bench.stats import SEED, compare, judge_label, stratified_sample
from kestrel.bench.stubs import USERS, build_registry, make_setup
from kestrel.bench.tasks import SUITE_VERSION, Task, load_tasks, select, task_sha
from kestrel.llm import LLM, PROVIDERS, LLMError, build_llm
from kestrel.tracing import Tracer

RESULTS_DIR = Path(__file__).resolve().parents[3] / "evals" / "results"
BENCH_DB = Path("logs") / "bench.db"  # kept apart from your personal traces and training data

# Token estimates, measured on Groq gpt-oss-120b on 2026-10-04 (16 fully recorded tasks):
TOKENS_PER_TASK_ESTIMATE = 3_300  # raw agent tokens per task (the full-suite mean was 3,103)
JUDGE_TOKENS_ESTIMATE = 500  # raw judge tokens per rubric task (measured 489)
JUDGE_OUTPUT_ESTIMATE = 150  # judge output tokens per call, including gpt-oss reasoning (measured ~90-200)
BILLABLE_SHARE = 0.32  # uncached share of raw agent + judge tokens (Groq caches the repeated prompt)
GROQ_FREE_DAILY = {"tokens": 200_000, "requests": 1_000}  # gpt-oss-120b free tier, cached tokens excluded
DONE = ("pass", "fail", "excluded")  # results a resumed run keeps
SPLITS = ("main", "safety")
REUSABLE = ("pass", "fail")


def add_parser(sub: argparse._SubParsersAction) -> None:
    bench = sub.add_parser("bench", help="KestrelBench: run the eval suite, compare runs, calibrate the judge")
    actions = bench.add_subparsers(dest="bench_command", required=True)

    run = actions.add_parser("run", help="run tasks and score them")
    run.add_argument("--subset", default="all", help="'all' or a tag, e.g. 'ci' (default all)")
    run.add_argument("--category")
    run.add_argument("--task", action="append", help="run only this task id (repeatable)")
    run.add_argument("--tasks", help="comma-separated task ids or patterns, e.g. 'inject-*,arith-percent'")
    run.add_argument("--sample", type=int, help="a stratified sample of N tasks (proportional per category)")
    run.add_argument("--seed", type=int, default=SEED, help=f"seed for --sample (default {SEED})")
    run.add_argument("--repeat", type=int, default=1, help="run every task N times, to measure run-to-run variance")
    run.add_argument(
        "--reuse",
        type=Path,
        help="count graded runs from an earlier results file as repeat 1 (same model, judge version and task "
        "definitions only), so --repeat 3 costs two new runs per task",
    )
    run.add_argument(
        "--split", choices=SPLITS, default="main", help="main (the 100 v1.1 tasks) or safety (attacks + benign)"
    )
    run.add_argument("--split-dir", type=Path, help="safety split folder (default evals/kestrelbench/safety)")
    run.add_argument(
        "--scripted",
        choices=MODELS,
        help="safety split: a scripted fake model instead of a provider, to validate the checks (no model calls)",
    )
    run.add_argument(
        "--user",
        choices=sorted(USERS),
        default="strict",
        help="safety split: who answers approvals. strict = the task's rules, rejects the rest (default); "
        "rubber-stamp = approves everything (approval fatigue)",
    )
    run.add_argument("--provider", default="groq", help="model under test (default groq)")
    run.add_argument("--model", help="override the provider's default model")
    run.add_argument("--judge", default="groq", help="judge provider, or 'none' to skip rubric grading")
    run.add_argument("--judge-model", help="override the judge's model (default: the provider's default)")
    run.add_argument("--skip-network", action="store_true", help="skip tasks tagged 'network' (web search/fetch)")
    run.add_argument("--pause", type=float, default=0.0, help="seconds between tasks (free-tier rate limits)")
    run.add_argument("--out", type=Path, help="results JSON path (default evals/results/<time>-<provider>.json)")
    run.add_argument("--min-pass-rate", type=float, help="exit 1 if the pass rate is below this (CI gate)")
    run.add_argument(
        "--max-errors", type=int, help="exit 1 if more runs than this errored or were skipped after errors (CI gate)"
    )
    run.add_argument(
        "--token-budget",
        type=int,
        help="start no new task once billable (uncached) agent + judge tokens reach this; the rest are 'skipped' "
        "(Groq's free tier allows 200,000 tokens/day for gpt-oss-120b)",
    )
    run.add_argument(
        "--stop-after-errors",
        type=int,
        default=3,
        help="skip the rest after this many errors in a row, e.g. a daily rate limit (default 3, 0 = never)",
    )
    run.add_argument(
        "--resume", type=Path, help="continue an earlier results file: keep its pass/fail/excluded runs, run the rest"
    )
    run.add_argument("--shard", help="run every n-th task, e.g. 1/2 and 2/2 on different days")
    run.add_argument("--dry-run", action="store_true", help="show what would run and the expected tokens; no calls")
    run.add_argument(
        "--estimate-from",
        type=Path,
        action="append",
        help="results file(s) with measured tokens per task for the estimate (repeatable; default: --reuse/--resume)",
    )

    label = actions.add_parser("label", help="label stored answers pass/fail yourself, to calibrate the judge")
    label.add_argument("results", type=Path, nargs="?", help="results JSON (default: the newest)")

    calibrate = actions.add_parser("calibrate", help="compare your labels with the judge")
    calibrate.add_argument(
        "--split", choices=cal.SPLITS, default="heldout", help="heldout (report this) or dev (to improve the judge)"
    )
    calibrate.add_argument("--judge", help="re-grade the stored answers with this judge provider (cached)")
    calibrate.add_argument("--judge-model", help="model for --judge, e.g. a candidate judge")

    rejudge = actions.add_parser("rejudge", help="re-grade a results file's stored answers (never re-runs Kestrel)")
    rejudge.add_argument("results", type=Path)
    rejudge.add_argument("--judge", default="groq")
    rejudge.add_argument("--judge-model")
    rejudge.add_argument("--pause", type=float, default=0.0)
    rejudge.add_argument("--out", type=Path, help="default: <results>.rejudged-<version>.json")
    rejudge.add_argument("--dry-run", action="store_true", help="count the judge calls and expected tokens; no calls")
    rejudge.add_argument(
        "--only-missing",
        action="store_true",
        help="re-grade only answers without a valid verdict from the current judge version (e.g. after a rate limit)",
    )

    comp = actions.add_parser("compare", help="score difference between two runs, with a paired 95%% interval")
    comp.add_argument("a", type=Path, help="baseline results (e.g. the frontier model)")
    comp.add_argument("b", type=Path, help="results to compare against it (e.g. the small model)")
    comp.add_argument("--margin", type=float, default=0.05, help="non-inferiority margin, absolute (default 0.05)")

    unlock_cmd = actions.add_parser("unlock", help="remove a stale lock left by a crashed run (checks its PID is gone)")
    unlock_cmd.add_argument("results", type=Path)

    report = actions.add_parser("report", help="print the Markdown report for a results file")
    report.add_argument("results", type=Path, nargs="?", help="results JSON (default: the newest)")


def _newest_results() -> Path:
    files = sorted(p for p in RESULTS_DIR.glob("*.json") if ".rejudged-" not in p.name)
    if not files:
        sys.exit("No results yet. Run: uv run kestrel bench run")
    return files[-1]


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        sys.exit(f"No such results file: {path}")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except OSError:
        return None


def _progress(r: TaskResult) -> None:
    mark = {"pass": "PASS", "fail": "FAIL", "error": "ERR ", "excluded": "EXCL", "skipped": "SKIP"}[r.status]
    extra = f"  judge {r.judge['score']}" if r.judge else ""
    if r.safety and r.safety["kind"] == "attack" and r.status in ("pass", "fail"):
        mark = "FELL" if r.safety["model_success"] else "SAFE"
        extra = f"  system: {'HARMED' if r.safety['system_success'] else 'no harm'}"
    run = f" #{r.repeat}" if r.repeat > 1 else ""
    print(f"  {mark} {r.id + run:<40} {r.latency_ms / 1000:5.1f}s {r.tokens:6,} tok{extra}", flush=True)


def _judge(provider: str, model: str | None) -> Judge:
    llm = LLM(provider, model)
    return Judge(llm, name=f"{provider}/{llm.model}")


# --- run ----------------------------------------------------------------------------------


def _safety(args: argparse.Namespace) -> SafetySplit:
    """The safety split, loaded once per command (cached on args)."""
    if getattr(args, "_safety_split", None) is None:
        args._safety_split = load_safety(args.split_dir or SAFETY_DIR)
    split: SafetySplit = args._safety_split
    return split


def _pick_tasks(args: argparse.Namespace) -> list[Task]:
    pool = [st.task for st in _safety(args).tasks] if args.split == "safety" else load_tasks()
    tasks = select(pool, args.subset, args.category, args.task, skip_tags=("network",) if args.skip_network else ())
    if args.tasks:
        patterns = [p.strip() for p in args.tasks.split(",") if p.strip()]
        tasks = [t for t in tasks if any(fnmatch.fnmatchcase(t.id, p) for p in patterns)]
    if args.sample:
        tasks = stratified_sample(tasks, args.sample, lambda t: t.category, lambda t: t.id, seed=args.seed)
    if args.shard:
        try:
            part, parts = (int(x) for x in args.shard.split("/"))
            assert 1 <= part <= parts
        except ValueError, AssertionError:
            sys.exit("--shard must look like 1/2")
        tasks = [t for i, t in enumerate(tasks) if i % parts == part - 1]
    return tasks


def _reusable(path: Path, provider: str, model: str, judge: str | None, tasks: list[Task]) -> list[TaskResult]:
    """Graded runs from an earlier file that may stand in for repeat 1: same model, same judge and
    judge version, and an identical task definition (fingerprint), so nothing is mixed."""
    old = _load(path)
    m = old.get("meta", {})
    if (m.get("provider"), m.get("model")) != (provider, model):
        sys.exit(f"--reuse: {path.name} was run on {m.get('provider')}/{m.get('model')}, not {provider}/{model}.")
    if judge_label(m) != (f"{judge}@{JUDGE_VERSION}" if judge else "none"):
        sys.exit(f"--reuse: {path.name} was graded by {judge_label(m)}; this run uses {judge}@{JUDGE_VERSION}.")
    shas = {t.id: task_sha(t) for t in tasks}
    usable, stale = [], 0
    for d in old["tasks"]:
        if d["id"] in shas and d["status"] in REUSABLE and d.get("repeat", 1) == 1:
            if d.get("task_sha") != shas[d["id"]]:
                stale += 1  # older file, or the task changed since: run it again
                continue
            usable.append(TaskResult(**d))
    if stale:
        print(f"--reuse: {stale} run(s) in {path.name} have no or a different task fingerprint; running them again.")
    return usable


def _estimate(jobs: list[tuple[Task, int]], refs: list[Path]) -> dict[str, int]:
    """Expected raw tokens, billable (uncached) tokens and requests for these jobs, from measured
    per-task numbers where a reference file has them, else from the averages above."""
    measured: dict[str, dict[str, Any]] = {}
    for path in refs:
        for d in _load(path)["tasks"]:
            if d["status"] in REUSABLE and d["tokens"]:
                best = measured.get(d["id"])
                has_cache = d.get("cached_tokens", 0) > 0
                if best is None or (has_cache and not best.get("cached_tokens", 0)):
                    measured[d["id"]] = d
    raw = billable = requests = 0
    for task, _ in jobs:
        d = measured.get(task.id)
        judge_raw = JUDGE_TOKENS_ESTIMATE if task.rubric else 0
        if d and d.get("cached_tokens", 0) > 0:  # fully recorded: agent + judge + cache
            raw += d["tokens"] + d.get("judge_tokens", 0)
            billable += billable_tokens(TaskResult(**d))
        else:
            agent = d["tokens"] if d else TOKENS_PER_TASK_ESTIMATE
            raw += agent + judge_raw
            billable += round((agent + judge_raw) * BILLABLE_SHARE)
        requests += (d["steps"] if d else 3) + (1 if task.rubric else 0)
    return {"raw": raw, "billable": billable, "requests": requests, "measured": sum(t.id in measured for t, _ in jobs)}


def cmd_run(args: argparse.Namespace) -> int:
    """Lock the output (and resumed) results file for the whole run, so a second process can't
    run the same benchmark at the same time. A dry run makes no calls and takes no lock."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.split == "main" and (args.scripted or args.split_dir or args.user != "strict"):
        sys.exit("--scripted, --split-dir and --user apply to --split safety only.")
    if args.split == "safety":
        args.judge = "none"  # graded by deterministic guards only
        if args.reuse:
            sys.exit("--reuse is for the main split; use --resume to continue a safety run.")
        if args.scripted:
            args.provider = "scripted"
    label = f"safety-{args.scripted or args.provider}" if args.split == "safety" else args.provider
    args.out = args.out or args.resume or RESULTS_DIR / f"{stamp}-{label}.json"
    if args.dry_run:
        return _run(args)
    try:
        with results_lock(args.out, args.resume):
            return _run(args)
    except LockError as e:
        sys.exit(str(e))


def _run(args: argparse.Namespace) -> int:
    tasks = _pick_tasks(args)
    if not tasks:
        sys.exit("No tasks match.")
    if args.repeat < 1:
        sys.exit("--repeat must be at least 1")
    for name in (args.provider, args.judge):
        if name not in ("none", "scripted") and name not in PROVIDERS:
            sys.exit(f"Unknown provider {name!r}. Choose from: {', '.join(PROVIDERS)}")
    model = args.scripted or args.model or PROVIDERS[args.provider].default_model
    judge_model = (args.judge_model or PROVIDERS[args.judge].default_model) if args.judge != "none" else None
    judge_name = f"{args.judge}/{judge_model}" if judge_model else None

    previous: dict[tuple[str, int], TaskResult] = {}
    if args.resume:
        old = _load(args.resume)
        old_meta = old.get("meta", {})
        if old_meta.get("split", "main") != args.split:
            sys.exit(f"{args.resume.name} is a {old_meta.get('split', 'main')} split run, not {args.split}.")
        if old_meta.get("split") == "safety" and old_meta.get("user") != args.user:
            sys.exit(f"{args.resume.name} was run with user {old_meta.get('user')!r}, not {args.user!r}.")
        if (old_meta.get("provider"), old_meta.get("model")) != (args.provider, model):
            sys.exit(
                f"{args.resume.name} was run on {old_meta.get('provider')}/{old_meta.get('model')}; "
                f"resuming on {args.provider}/{model} would mix models in one score."
            )
        if args.judge != "none" and judge_label(old_meta) != f"{judge_name}@{JUDGE_VERSION}":
            sys.exit(
                f"{args.resume.name} was graded by {judge_label(old_meta)}; this run would grade with "
                f"{judge_name}@{JUDGE_VERSION} and mix two judges in one score. Re-grade it first: "
                f"uv run kestrel bench rejudge {args.resume}"
            )
        previous = {(d["id"], d.get("repeat", 1)): TaskResult(**d) for d in old["tasks"] if d["status"] in DONE}
    if args.reuse:
        for r in _reusable(args.reuse, args.provider, model, judge_name, tasks):
            previous.setdefault((r.id, 1), r)

    all_jobs = [(t, k) for k in range(1, args.repeat + 1) for t in tasks]  # repeat by repeat: spread over time
    todo = [(t, k) for t, k in all_jobs if (t.id, k) not in previous]
    est = _estimate(todo, args.estimate_from or [p for p in (args.reuse, args.resume) if p])
    print(
        f"KestrelBench: {len(tasks)} tasks x {args.repeat} = {len(all_jobs)} runs on {args.provider}/{model}, "
        f"judge {judge_name or 'none'} ({JUDGE_VERSION}); {len(todo)} to run, {len(all_jobs) - len(todo)} kept"
    )
    print(
        f"Expected: ~{est['raw']:,} tokens, ~{est['billable']:,} billable (uncached), ~{est['requests']} requests"
        f" ({est['measured']} of {len(todo)} runs from measured tasks)"
        + (f"; budget {args.token_budget:,}" if args.token_budget else "")
    )
    if args.provider == "groq" and args.judge in ("groq", "none"):
        fits = est["billable"] <= GROQ_FREE_DAILY["tokens"] and est["requests"] <= GROQ_FREE_DAILY["requests"]
        print(
            f"Groq free tier: {GROQ_FREE_DAILY['tokens']:,} tokens and {GROQ_FREE_DAILY['requests']:,} requests a day "
            "per model -> " + ("fits in one day (if nothing else used it today)" if fits else "needs more than a day")
        )
    if judge_name and judge_name == f"{args.provider}/{model}":
        print(
            "Note: the judge is the model under test, so it grades its own answers (self-preference bias). "
            "See docs/kestrelbench.md, 'Judge independence'."
        )
    if args.dry_run:
        for t, k in todo:
            print(f"  would run {t.id} ({t.category})" + (f" #{k}" if args.repeat > 1 else ""))
        return 0

    llm: Any
    try:
        if args.scripted:
            llm = ScriptedSafetyModel(args.scripted)
            model = llm.model
        else:
            llm, _ = build_llm([args.provider], args.model)
            model = llm.llms[0].model
        judge = _judge(args.judge, args.judge_model) if args.judge != "none" else None
    except LLMError as e:
        sys.exit(f"Can't start the benchmark: {e}")

    safety = args.split == "safety"
    split = _safety(args) if safety else None
    extra: dict[str, Any] = {}
    if split is not None:
        on_begin = llm.begin if isinstance(llm, ScriptedSafetyModel) else None
        extra = {
            "base_workspace": split.workspace,
            "setup": make_setup(split, args.user, on_begin),
            "grader": make_grader(split),
        }
    fresh = run_suite(
        [t for t, _ in todo],
        llm,
        judge,
        Tracer(BENCH_DB),
        pause=args.pause,
        on_result=_progress,
        expected_provider=None if args.scripted else args.provider,
        token_budget=args.token_budget,
        repeats=[k for _, k in todo],
        stop_after_errors=args.stop_after_errors or None,
        **extra,
    )
    by_key = {**previous, **{(r.id, r.repeat): r for r in fresh}}
    results = [by_key[(t.id, k)] for t, k in all_jobs]
    out = args.out
    meta = {
        "suite_version": SUITE_VERSION,
        "split": args.split,
        "split_version": SAFETY_VERSION if safety else SUITE_VERSION,
        # the safety registry's common tools; per-task MCP stubs are part of each task's fingerprint
        "agent": agent_fingerprint(args.provider, model, build_registry(SafetyMeta(), []) if safety else None),
        "provider": args.provider,
        "model": model,
        "user": args.user if safety else None,
        "judge": judge.name if judge else None,
        "judge_version": JUDGE_VERSION if judge else None,
        "judge_independent": None if judge is None else judge.name != f"{args.provider}/{model}",
        "subset": args.subset,
        "category": args.category,
        "tasks_filter": args.tasks,
        "sample": args.sample,
        "seed": args.seed if args.sample else None,
        "repeat": args.repeat,
        "reused_from": args.reuse.name if args.reuse else None,
        "shard": args.shard,
        "git": _git_sha(),
    }
    if safety:
        scored = score([r.to_dict() for r in results])
        write_results(out, results, meta, extra={"safety": score_dict(scored)})
        print("\n" + "\n".join(score_lines(scored, args.user)) + f"\nResults: {out}")
        if any(r.status in ("skipped", "error") for r in results):
            print(f"To finish later: uv run kestrel bench run --split safety --resume {out} [same options]")
        return 0
    data = write_results(out, results, meta)
    out.with_suffix(".md").write_text(markdown(data), encoding="utf-8")
    s = data["summary"]
    ci = s["pass_rate_ci"]
    print(
        f"\nPass rate {pct(s['pass_rate'])}"
        + (f" (95% CI {ci['low']:.0%}-{ci['high']:.0%}, n={ci['n']} tasks)" if ci else "")
        + f"; {s['passed']}/{s['graded']} graded runs passed; {s['errors']} errored, {s['excluded']} excluded, "
        f"{s['skipped']} skipped; mean score {s['mean_score']}; "
        f"{s['tokens_total'] + s['judge_tokens_total']:,} tokens ({s['billable_tokens_total']:,} billable)"
        f"\nResults: {out}\nReport:  {out.with_suffix('.md')}"
    )

    failed = []
    if args.min_pass_rate is not None and (s["pass_rate"] or 0) < args.min_pass_rate:
        failed.append(f"pass rate {pct(s['pass_rate'])} is below the minimum {pct(args.min_pass_rate)}")
    stopped = sum(r.status == "skipped" and "errors in a row" in (r.error or "") for r in results)
    if args.max_errors is not None and s["errors"] + stopped > args.max_errors:
        failed.append(f"{s['errors']} runs errored and {stopped} were skipped after errors (max {args.max_errors})")
    if s["skipped"] or s["errors"]:
        print(f"To finish later: uv run kestrel bench run --resume {out} [same options]")
    for reason in failed:
        print(f"GATE FAILED: {reason}")
    return 1 if failed else 0


# --- labels and calibration ---------------------------------------------------------------


def cmd_label(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        sys.exit("Labels come from a person at the keyboard: run this in a terminal, not from a script or pipe.")
    path = args.results or _newest_results()
    data = _load(path)
    tasks = load_tasks()
    by_id = {t.id: t for t in tasks}
    split = cal.splits(tasks)
    queue = cal.label_queue(data, tasks, cal.load_labels())
    print(
        f"{len(queue)} judged answers in {path.name} still to label (stratified across categories).\n"
        "For each: does the answer do what's expected? p = pass, f = fail, s = skip, q = quit (resume any time).\n"
        "You can add a short note after the letter, e.g. 'f made up a revenue figure'. "
        "The judge's verdict and the checks are hidden so they can't sway you.\n"
    )
    saved = 0
    for i, r in enumerate(queue, 1):
        task = by_id[r["id"]]
        log, source = cal.tool_log_for(r, BENCH_DB)
        print("=" * 72)
        print(f"[{i}/{len(queue)}] {r['id']}  ({r['category']})")
        for turn in task.prompts:
            print(f"USER: {turn}")
        print(f"\nExpected: {task.rubric}")
        print(f"\nTool steps ({source}):" if log else "\nTool steps: none")
        for line in log:
            print(f"  {line}")
        print(f"\nKestrel's answer:\n{r['answer']}\n")
        while True:
            raw = input("[p / f / s] [note], or q: ").strip()
            choice, note = (raw[:1].lower(), raw[1:].strip()) if raw else ("", "")
            if choice in ("p", "f", "s", "q"):
                break
        if choice == "q":
            break
        cal.append_label(cal.make_label(r, path, choice, note, split[r["id"]]))
        saved += 1
    print(f"\nSaved {saved} label(s) to {cal.LABELS}. Next: uv run kestrel bench calibrate")
    return 0


def _print_calibration(title: str, c: cal.Calibration) -> None:
    print(f"\n{title}: {c.n} labelled answers")
    if c.n == 0:
        return
    assert c.agreement is not None
    print(f"  agreement       {c.agreement.fmt()}")
    print(f"  Cohen's kappa   {c.kappa if c.kappa is not None else 'n/a (no variation in the labels)'}")
    k = c.confusion
    print("                  judge pass  judge fail")
    print(f"  you: pass       {k['both_pass']:10}  {k['too_strict']:10}   <- judge too strict: {k['too_strict']}")
    print(f"  you: fail       {k['too_lenient']:10}  {k['both_fail']:10}   <- judge too lenient: {k['too_lenient']}")
    print("  by category:    " + ", ".join(f"{cat} {a}/{n}" for cat, (a, n) in c.by_category.items()))
    for p in c.disagreements:
        lean = "too lenient" if p.judge else "too strict"
        you = "pass" if p.human else "fail"
        print(f"  - {p.task_id} ({lean}): you {you}" + (f" ({p.note})" if p.note else ""))
        print(f"      judge {p.judge_score}: {p.judge_reason}")


def cmd_calibrate(args: argparse.Namespace) -> int:
    labels = cal.labelled(cal.load_labels(), args.split)
    heading = (
        "HELD-OUT (report this number)"
        if args.split == "heldout"
        else "DEV (use these to improve the judge prompt; never report them)"
    )
    total = len(cal.labelled(cal.load_labels(), "dev")) + len(cal.labelled(cal.load_labels(), "heldout"))
    if total == 0:
        print("No labels yet. Run: uv run kestrel bench label")
        return 0
    stored, problems, judges = cal.stored_pairs(labels)
    _print_calibration(f"{heading}\nStored verdicts ({', '.join(sorted(judges)) or 'none'})", cal.calibrate(stored))
    if args.judge:
        try:
            judge = _judge(args.judge, args.judge_model)
        except LLMError as e:
            sys.exit(f"Can't start the judge: {e}")
        pairs, more, calls = cal.regraded_pairs(labels, load_tasks(), judge, BENCH_DB)
        problems += more
        _print_calibration(
            f"Re-graded by {judge.name}@{judge.version} (stored answers, {calls} new judge calls)", cal.calibrate(pairs)
        )
    for p in problems:
        print(f"  ! {p}")
    if len(labels) < 20:
        print(f"\n  {len(labels)} labels in this half: the interval is wide. Aim for 20+ per half.")
    return 0


def cmd_rejudge(args: argparse.Namespace) -> int:
    out = args.out or args.results.with_name(f"{args.results.stem}.rejudged-{JUDGE_VERSION}.json")
    if args.dry_run:
        return _rejudge(args, out)
    try:
        with results_lock(out, args.results):
            return _rejudge(args, out)
    except LockError as e:
        sys.exit(str(e))


def cmd_unlock(args: argparse.Namespace) -> int:
    try:
        print(unlock(args.results))
    except LockError as e:
        sys.exit(str(e))
    return 0


def _rejudge(args: argparse.Namespace, out: Path) -> int:
    """Grade the stored answers again (new judge prompt or model) and write a NEW results file.
    Kestrel is never re-run; checks keep their stored outcome; only the judge's part changes."""
    data = _load(args.results)
    if out.resolve() == args.results.resolve():
        sys.exit("rejudge writes a new file; it never overwrites the original results.")
    by_id = {t.id: t for t in load_tasks()}

    def wanted(d: dict[str, Any]) -> bool:
        task = by_id.get(d["id"])
        judge_failed = d["status"] == "error" and bool(d.get("judge")) and d["judge"].get("score") is None
        if not (task and task.rubric and (d["status"] in REUSABLE or judge_failed)):
            return False
        current = (d.get("judge") or {}).get("version") == JUDGE_VERSION and (d.get("judge") or {}).get(
            "score"
        ) is not None
        return not (args.only_missing and current)

    todo = [d for d in data["tasks"] if wanted(d)]
    sources = Counter(cal.tool_log_for(d, BENCH_DB)[1] for d in todo)
    request_tokens = sum(
        sum(
            len(m["content"])
            for m in build_request(list(t.prompts), t.rubric, cal.tool_log_for(d, BENCH_DB)[0], d["answer"])
        )
        // 4
        for d in todo
        if (t := by_id[d["id"]]).rubric
    )
    estimate = request_tokens + len(todo) * JUDGE_OUTPUT_ESTIMATE
    print(
        f"Re-grade {len(todo)} stored answers with judge {JUDGE_VERSION} (tool steps: "
        + ", ".join(f"{n} {s}" for s, n in sources.items())
        + f"). Expected ~{estimate:,} tokens ({request_tokens:,} in + ~{JUDGE_OUTPUT_ESTIMATE} out per call), "
        f"{len(todo)} requests; nothing is re-run except the judge."
    )
    if args.dry_run:
        return 0
    try:
        judge = _judge(args.judge, args.judge_model)
    except LLMError as e:
        sys.exit(f"Can't start the judge: {e}")
    results, graded, changed = [], 0, []
    for d in data["tasks"]:
        r = TaskResult(**d)
        task = by_id.get(r.id)
        if wanted(d):
            assert task is not None and task.rubric
            if graded and args.pause:
                time.sleep(args.pause)
            log, _ = cal.tool_log_for(d, BENCH_DB)
            before = (r.status, (r.judge or {}).get("score"), (r.judge or {}).get("version") or "v1")
            v = judge.grade(list(task.prompts), task.rubric, log, r.answer)
            graded += 1
            r.judge = {"score": v.score, "reason": v.reason, "judge": judge.name, "version": judge.version}
            r.judge_tokens, r.tool_log = v.tokens, log
            if v.score is None:
                r.status, r.error = "error", v.reason
            else:
                checks = sum(c["ok"] for c in r.checks) / len(r.checks) if r.checks else 1.0
                r.score = round((checks + v.score) / 2, 3)
                r.status = "pass" if all(c["ok"] for c in r.checks) and v.score >= 0.5 else "fail"
            print(f"  {r.status.upper():5} {r.id:<40} judge {before[2]} {before[1]} -> {judge.version} {v.score}")
            if (before[0], before[1]) != (r.status, v.score):
                changed.append(f"{r.id}: judge {before[1]} -> {v.score}, {before[0]} -> {r.status} ({v.reason})")
        results.append(r)
    meta = {
        **data["meta"],
        "judge": judge.name,
        "judge_version": judge.version,
        "rejudged_from": args.results.name,
    }
    written = write_results(out, results, meta)
    print(f"\nRe-graded {graded} stored answers with {judge.name}@{judge.version}: {out}")
    print(f"Before: {pct(summarize(data['tasks'])['pass_rate'])}  after: {pct(written['summary']['pass_rate'])}")
    print(f"{len(changed)} verdict(s) changed" + (":" if changed else "."))
    for line in changed:
        print(f"  - {line}")
    return 0


# --- compare and report -------------------------------------------------------------------


def cmd_compare(args: argparse.Namespace) -> int:
    a, b = _load(args.a), _load(args.b)
    sa, sb = a.get("meta", {}).get("split", "main"), b.get("meta", {}).get("split", "main")
    if sa != sb:
        sys.exit(f"Can't compare a {sa} split run with a {sb} split run: they contain different tasks.")
    c = compare(a, b)
    if c.diff is None or c.a_rate is None or c.b_rate is None:
        print("No task was graded in both files.")
        return 1
    ma, mb = a["meta"], b["meta"]
    print(f"A  {args.a.name}: {ma.get('provider')}/{ma.get('model')}  {c.a_rate.fmt()}")
    print(f"B  {args.b.name}: {mb.get('provider')}/{mb.get('model')}  {c.b_rate.fmt()}")
    print(f"B - A: {c.diff.fmt(signed=True)}  (paired bootstrap over the {c.diff.n} tasks graded in both)")
    print(f"Flipped: {len(c.better_in_b)} better in B, {len(c.worse_in_b)} worse in B")
    for label, ids in (("better in B", c.better_in_b), ("worse in B", c.worse_in_b)):
        if ids:
            print(f"  {label}: {', '.join(ids)}")
    within = c.within(args.margin)
    print(
        f"Within {args.margin * 100:.0f} points of A (95% confidence): "
        + ("yes" if within else "no, or not yet shown with this many tasks")
    )
    if c.only_in_one:
        print(f"Left out, graded in only one file: {len(c.only_in_one)} ({', '.join(c.only_in_one[:10])})")
    if c.changed_tasks:
        print(f"Left out, task definition changed between runs: {', '.join(c.changed_tasks)}")
    for w in c.warnings:
        print(f"Warning: {w}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    path = args.results or _newest_results()
    print(markdown(_load(path)))
    return 0


def main(args: argparse.Namespace) -> int:
    commands = {
        "run": cmd_run,
        "label": cmd_label,
        "calibrate": cmd_calibrate,
        "rejudge": cmd_rejudge,
        "compare": cmd_compare,
        "report": cmd_report,
        "unlock": cmd_unlock,
    }
    return commands[args.bench_command](args)
