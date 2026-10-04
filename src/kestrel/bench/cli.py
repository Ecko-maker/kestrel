"""`kestrel bench ...`: run KestrelBench, label answers, calibrate the judge, print a report."""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from kestrel.bench.calibrate import LABELS, calibrate, load_labels
from kestrel.bench.judge import Judge
from kestrel.bench.report import markdown, pct, write_results
from kestrel.bench.runner import TaskResult, run_suite
from kestrel.bench.tasks import load_tasks, select
from kestrel.llm import LLM, PROVIDERS, LLMError, build_llm
from kestrel.tracing import Tracer

RESULTS_DIR = Path(__file__).resolve().parents[3] / "evals" / "results"
BENCH_DB = Path("logs") / "bench.db"  # kept apart from your personal traces and training data


def add_parser(sub: argparse._SubParsersAction) -> None:
    bench = sub.add_parser("bench", help="KestrelBench: run the eval suite, label answers, calibrate the judge")
    actions = bench.add_subparsers(dest="bench_command", required=True)

    run = actions.add_parser("run", help="run tasks and score them")
    run.add_argument("--subset", default="all", help="'all' or a tag, e.g. 'ci' (default all)")
    run.add_argument("--category")
    run.add_argument("--task", action="append", help="run only these task ids (repeatable)")
    run.add_argument("--provider", default="groq", help="model under test (default groq)")
    run.add_argument("--model", help="override the provider's default model")
    run.add_argument("--judge", default="groq", help="judge provider, or 'none' to skip rubric grading")
    run.add_argument("--judge-model", help="override the judge's model (default: the provider's default)")
    run.add_argument("--skip-network", action="store_true", help="skip tasks tagged 'network' (web search/fetch)")
    run.add_argument("--pause", type=float, default=0.0, help="seconds between tasks (free-tier rate limits)")
    run.add_argument("--out", type=Path, help="results JSON path (default evals/results/<time>-<provider>.json)")
    run.add_argument("--min-pass-rate", type=float, help="exit 1 if the pass rate is below this (CI gate)")
    run.add_argument("--max-errors", type=int, help="exit 1 if more tasks than this errored (CI gate)")
    run.add_argument(
        "--token-budget",
        type=int,
        help="start no new task once billable (uncached) agent + judge tokens reach this; the rest are 'skipped' "
        "(Groq's free tier allows 200,000 tokens/day for gpt-oss-120b)",
    )
    run.add_argument(
        "--resume", type=Path, help="continue an earlier results file: keep its pass/fail/excluded tasks, run the rest"
    )
    run.add_argument("--shard", help="run every n-th task, e.g. 1/2 and 2/2 on different days")

    label = actions.add_parser("label", help="score judged answers yourself, for calibration")
    label.add_argument("results", type=Path, nargs="?", help="results JSON (default: the newest)")

    actions.add_parser("calibrate", help="compare your labels with the judge's scores")

    report = actions.add_parser("report", help="print the Markdown report for a results file")
    report.add_argument("results", type=Path, nargs="?", help="results JSON (default: the newest)")


def _newest_results() -> Path:
    files = sorted(RESULTS_DIR.glob("*.json"))
    if not files:
        sys.exit("No results yet. Run: uv run kestrel bench run")
    return files[-1]


def _git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except OSError:
        return None


TOKENS_PER_TASK_ESTIMATE = 3_300  # agent + judge, raw; on Groq ~70-80% of input is cached (2026-10-04)
DONE = ("pass", "fail", "excluded")  # results a resumed run keeps


def _progress(r: TaskResult) -> None:
    mark = {"pass": "PASS", "fail": "FAIL", "error": "ERR ", "excluded": "EXCL", "skipped": "SKIP"}[r.status]
    extra = f"  judge {r.judge['score']}" if r.judge else ""
    print(f"  {mark} {r.id:<40} {r.latency_ms / 1000:5.1f}s {r.tokens:6,} tok{extra}", flush=True)


def cmd_run(args: argparse.Namespace) -> int:
    tasks = select(
        load_tasks(), args.subset, args.category, args.task, skip_tags=("network",) if args.skip_network else ()
    )
    if args.shard:
        try:
            part, parts = (int(x) for x in args.shard.split("/"))
            assert 1 <= part <= parts
        except ValueError, AssertionError:
            sys.exit("--shard must look like 1/2")
        tasks = [t for i, t in enumerate(tasks) if i % parts == part - 1]
    previous: dict[str, TaskResult] = {}
    if args.resume:
        old = json.loads(args.resume.read_text(encoding="utf-8"))
        old_meta = old.get("meta", {})
        wanted_model = args.model or PROVIDERS[args.provider].default_model
        if (old_meta.get("provider"), old_meta.get("model")) != (args.provider, wanted_model):
            sys.exit(
                f"{args.resume.name} was run on {old_meta.get('provider')}/{old_meta.get('model')}; "
                f"resuming on {args.provider}/{wanted_model} would mix models in one score."
            )
        previous = {d["id"]: TaskResult(**d) for d in old["tasks"] if d["status"] in DONE}
    todo = [t for t in tasks if t.id not in previous]
    if not tasks:
        sys.exit("No tasks match.")
    try:
        llm, _ = build_llm([args.provider], args.model)
        judge = None
        if args.judge != "none":
            judge_llm = LLM(args.judge, args.judge_model)
            judge = Judge(judge_llm, name=f"{args.judge}/{judge_llm.model}")
    except LLMError as e:
        sys.exit(f"Can't start the benchmark: {e}")
    model = llm.llms[0].model
    print(
        f"KestrelBench: {len(todo)} tasks on {args.provider}/{model}, judge {judge.name if judge else 'none'}"
        + (f" ({len(previous)} kept from {args.resume.name})" if previous else "")
    )
    estimate = len(todo) * TOKENS_PER_TASK_ESTIMATE
    print(f"Expected tokens: ~{estimate:,}" + (f" (budget {args.token_budget:,})" if args.token_budget else ""))

    fresh = run_suite(
        todo,
        llm,
        judge,
        Tracer(BENCH_DB),
        pause=args.pause,
        on_result=_progress,
        expected_provider=args.provider,
        token_budget=args.token_budget,
    )
    by_id = {**previous, **{r.id: r for r in fresh}}
    results = [by_id[t.id] for t in tasks]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.out or args.resume or RESULTS_DIR / f"{stamp}-{args.provider}.json"
    meta = {
        "provider": args.provider,
        "model": model,
        "judge": judge.name if judge else None,
        "subset": args.subset,
        "category": args.category,
        "shard": args.shard,
        "git": _git_sha(),
    }
    data = write_results(out, results, meta)
    report = markdown(data)
    out.with_suffix(".md").write_text(report, encoding="utf-8")
    s = data["summary"]
    print(
        f"\nPass rate {pct(s['pass_rate'])} ({s['passed']}/{s['graded']} graded; {s['errors']} errored, "
        f"{s['excluded']} excluded, {s['skipped']} skipped), mean score {s['mean_score']}, "
        f"{s['tokens_total'] + s['judge_tokens_total']:,} tokens ({s['billable_tokens_total']:,} billable)"
        f"\nResults: {out}\nReport:  {out.with_suffix('.md')}"
    )

    failed = []
    if args.min_pass_rate is not None and (s["pass_rate"] or 0) < args.min_pass_rate:
        failed.append(f"pass rate {pct(s['pass_rate'])} is below the minimum {pct(args.min_pass_rate)}")
    if args.max_errors is not None and s["errors"] > args.max_errors:
        failed.append(f"{s['errors']} tasks errored (max {args.max_errors})")
    if s["skipped"] or s["errors"]:
        print(f"To finish later: uv run kestrel bench run --resume {out} [same options]")
    for reason in failed:
        print(f"GATE FAILED: {reason}")
    return 1 if failed else 0


def cmd_label(args: argparse.Namespace) -> int:
    path = args.results or _newest_results()
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = {t.id: t for t in load_tasks()}
    done = {(lab["task_id"], lab["answer_sha"]) for lab in load_labels()}
    todo = [t for t in data["tasks"] if t.get("judge") and t["judge"].get("score") is not None]
    print(f"{len(todo)} judged answers in {path.name}. Score each: 1 = fully right, 0.5 = partly, 0 = wrong.")
    print("Don't look for the judge's score; it's hidden so it can't bias you. s = skip, q = quit.\n")
    LABELS.parent.mkdir(parents=True, exist_ok=True)
    added = 0
    for t in todo:
        sha = hashlib.sha256(t["answer"].encode("utf-8")).hexdigest()[:16]
        task = tasks.get(t["id"])
        if (t["id"], sha) in done or task is None:
            continue
        print("=" * 72)
        print(f"[{t['id']}] " + " / ".join(task.prompts))
        print(f"Rubric: {task.rubric}")
        print(f"Tools: {', '.join(t['tool_calls']) or 'none'}")
        print(f"Answer:\n{t['answer']}\n")
        while True:
            choice = input("Your score [1 / 0.5 / 0 / s / q]: ").strip().lower()
            if choice in ("1", "0.5", ".5", "0", "s", "q"):
                break
        if choice == "q":
            break
        if choice == "s":
            continue
        entry = {
            "task_id": t["id"],
            "answer_sha": sha,
            "run": path.name,
            "human_score": float(choice),
            "judge_score": t["judge"]["score"],
            "judge_reason": t["judge"]["reason"],
            "judge": t["judge"].get("judge"),
        }
        with LABELS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        added += 1
    print(f"\nSaved {added} label(s) to {LABELS}. Next: uv run kestrel bench calibrate")
    return 0


def cmd_calibrate(_: argparse.Namespace) -> int:
    c = calibrate(load_labels())
    if c.n == 0:
        print("No labels yet. Run: uv run kestrel bench label")
        return 0
    print(f"Judge vs. you on {c.n} answers")
    print(f"  exact agreement   {pct(c.agreement)}")
    print(f"  within 0.5        {pct(c.within_half)}")
    print(f"  Cohen's kappa     {c.kappa if c.kappa is not None else 'n/a (all labels identical)'}")
    print("  (you, judge): count  " + ", ".join(f"{k}: {v}" for k, v in sorted(c.confusion.items())))
    if c.n < 30:
        print(f"  Label at least 30 answers for a meaningful number ({c.n} so far).")
    for d in c.disagreements[:10]:
        print(f"  - {d['task_id']}: you {d['human_score']}, judge {d['judge_score']} ({d['judge_reason'][:100]})")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    path = args.results or _newest_results()
    print(markdown(json.loads(path.read_text(encoding="utf-8"))))
    return 0


def main(args: argparse.Namespace) -> int:
    return {"run": cmd_run, "label": cmd_label, "calibrate": cmd_calibrate, "report": cmd_report}[args.bench_command](
        args
    )
