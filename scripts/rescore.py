"""Re-score a stored KestrelBench run with the current suite's checks, without calling any model.

Usage: uv run python scripts/rescore.py evals/results/baseline-full.json evals/results/<out>.json

Only checks that can be re-run from what a results file stores are re-run:
- answer checks (answer_matches, answer_not_matches) on the stored answer;
- file_contains on outbox/*.md, against the send_message calls that actually ran (the outbox file is
  written from their subject and body; rejected calls write nothing). Which calls ran comes from the
  stored tool log or the bench traces; without either, the stored outcome is kept.
Every other check (tool calls, other files, approvals) keeps its stored outcome, so this is only valid
when those checks' logic didn't change between the suite versions (true for v1.0 -> v1.1; see
evals/CHANGELOG.md). The judge's verdicts are kept as they were. The input file is never modified.
"""

import json
import re
import sys
from pathlib import Path

from kestrel.bench.calibrate import tool_log_for
from kestrel.bench.checks import FLAGS, Outcome, plain, run_check
from kestrel.bench.report import write_results
from kestrel.bench.runner import TaskResult
from kestrel.bench.tasks import SUITE_VERSION, load_tasks

ANSWER_CHECKS = ("answer_matches", "answer_not_matches")
BENCH_DB = Path("logs") / "bench.db"
SENT = re.compile(r"^- send_message\((\{.*\})\) \[ran\] -> ")


def sent_texts(result: dict) -> list[str] | None:
    """Subject + body of every send_message call that ran; None if that can't be known."""
    log, source = tool_log_for(result, BENCH_DB)
    if source == "calls only":
        return None
    texts = []
    for line in log:
        if m := SENT.match(line):
            args = json.loads(m.group(1))
            texts.append(f"{args.get('subject', '')}\n{args.get('body', '')}")
    return texts


def main(src: Path, out: Path) -> None:
    if out.resolve() == src.resolve():
        sys.exit("Write the re-scored run to a new file.")
    data = json.loads(src.read_text(encoding="utf-8"))
    tasks = {t.id: t for t in load_tasks()}
    results, changed = [], []
    for d in data["tasks"]:
        r = TaskResult(**d)
        task = tasks[r.id]
        if r.status in ("pass", "fail"):
            outcome = Outcome([], r.answer, "answered", r.steps, Path("."), {})
            for stored, check in zip(r.checks, task.checks, strict=True):
                if check["type"] in ANSWER_CHECKS:
                    ok, detail = run_check(check, outcome)
                elif check["type"] == "file_contains" and check["path"] == "outbox/*.md":
                    sent = sent_texts(d)
                    if sent is None:
                        continue
                    ok = any(re.search(check["pattern"], plain(t), FLAGS) for t in sent)
                    detail = f"re-checked on the {len(sent)} send_message call(s) that ran"
                else:
                    continue
                if ok != stored["ok"]:
                    changed.append(f"{r.id}: {check['type']} {stored['ok']} -> {ok}")
                stored.update(ok=ok, detail=detail)
            checks = sum(c["ok"] for c in r.checks) / len(r.checks) if r.checks else 1.0
            judge = r.judge["score"] if r.judge else None
            r.score = round(checks if judge is None else (checks + judge) / 2, 3)
            r.status = "pass" if all(c["ok"] for c in r.checks) and (judge is None or judge >= 0.5) else "fail"
        results.append(r)
    meta = {**data["meta"], "suite_version": SUITE_VERSION, "rescored_from": src.name}
    summary = write_results(out, results, meta)["summary"]
    print("\n".join(changed) or "no check changed")
    print(f"{src.name} re-scored with suite v{SUITE_VERSION}: {summary['passed']}/{summary['graded']} -> {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
