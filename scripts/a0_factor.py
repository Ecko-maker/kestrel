"""A0's schema factor f: the Google step's after-side billable per default billable (docs/google-step-plan.md 6).

    gh run download 44793332365 -n kestrelbench-ci -D tmp/ci-pr       # #111's final PR run
    gh run download 44800457846 -n kestrelbench-ci -D tmp/ci-merge    # #111's merge run
    gh run download 44670584543 -n kestrelbench-ci -D tmp/ci-pr1      # #111's first PR run (robustness only)
    uv run --no-sync python scripts/a0_factor.py ..\\kestrel-runs\\evals\\results\\google-a0-calibration.json \\
        --primary tmp/ci-pr/ci.json tmp/ci-merge/ci.json --extra tmp/ci-pr1/ci.json

Read only, no model calls. A0 is the CI subset x1 with `--tools google`; the defaults are CI-subset runs of the
default agent on the same tasks. Only the tasks A0 graded (pass or fail) are paired. Per task, the default
billable is the mean over the runs. Three pairings:
- primary: against the mean of the --primary runs (the reported f; 2026-11-23: 2.07, agent tokens 2.19);
- all: against the mean of every run given (2026-11-23, with #111's first PR run: 2.01);
- each run alone (2026-11-23: 1.90 / 2.05 / 2.08).
f is the ratio of sums (what a whole run costs); the per-task ratios give min / median / max. "Agent only" leaves
out the judge's tokens: the figure for the safety runs, which have no judge. Billable = tokens + judge tokens -
cached tokens, as the results' billable_tokens_total. Prints task ids, statuses and token numbers, never answers.
"""

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path


def billable(row: dict, *, judge: bool = True) -> int:
    return int(row["tokens"]) + (int(row.get("judge_tokens") or 0) if judge else 0) - int(row.get("cached_tokens") or 0)


@dataclass
class Factor:
    a0: int  # A0's billable on the paired tasks
    default: float  # the defaults' mean billable on the same tasks
    f: float  # ratio of sums
    per_task: dict[str, float]  # task id -> A0 / default mean

    @property
    def spread(self) -> tuple[float, float, float]:
        values = list(self.per_task.values())
        return min(values), statistics.median(values), max(values)


def graded_ids(a0: dict) -> list[str]:
    return sorted(r["id"] for r in a0["tasks"] if r["status"] in ("pass", "fail"))


def factor(a0: dict, defaults: list[dict], *, judge: bool = True) -> Factor:
    """f of A0 against the per-task mean of the default runs, on the tasks A0 graded."""
    ids = graded_ids(a0)
    mine = {r["id"]: r for r in a0["tasks"]}
    rows = [{r["id"]: r for r in d["tasks"]} for d in defaults]
    for i in ids:
        if any(i not in r for r in rows):
            raise SystemExit(f"STOP: {i} is not in every default run")
    base = {i: statistics.mean(billable(r[i], judge=judge) for r in rows) for i in ids}
    own = {i: billable(mine[i], judge=judge) for i in ids}
    if any(v <= 0 for v in base.values()):
        raise SystemExit("STOP: a task with no billable tokens in the default runs")
    total = sum(base.values())
    return Factor(sum(own.values()), total, sum(own.values()) / total, {i: own[i] / base[i] for i in ids})


def passes(data: dict, ids: list[str]) -> int:
    rows = {r["id"]: r for r in data["tasks"]}
    return sum(rows[i]["status"] == "pass" for i in ids)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def line(name: str, fa: Factor, fj: Factor) -> str:
    lo, med, hi = fa.spread
    return (
        f"{name}: f {fj.f:.3f} ({fj.a0:,} vs {fj.default:,.0f} billable); agent only {fa.f:.3f}; "
        f"per task min {lo:.2f}, median {med:.2f}, max {hi:.2f} (agent only)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("a0", type=Path, help="A0's results file")
    parser.add_argument("--primary", type=Path, nargs="+", required=True, help="the default runs f is reported on")
    parser.add_argument("--extra", type=Path, nargs="*", default=[], help="more default runs, for the 'all' pairing")
    args = parser.parse_args(argv)
    a0 = load(args.a0)
    primary = [load(p) for p in args.primary]
    extra = [load(p) for p in args.extra]
    ids = graded_ids(a0)
    print(f"A0: agent {a0['meta']['agent']['sha']}, {len(ids)} graded of {len(a0['tasks'])} tasks")
    for p, d in zip([*args.primary, *args.extra], [*primary, *extra], strict=True):
        print(f"default {p}: agent {d['meta']['agent']['sha']}, git {d['meta'].get('git')}")
    fj, fa = factor(a0, primary), factor(a0, primary, judge=False)
    print(f"\n{'task':32} {'A0':>7} {'default':>8} {'f':>6}")
    for i in ids:
        a0_row = next(r for r in a0["tasks"] if r["id"] == i)
        print(f"{i:32} {billable(a0_row):7d} {billable(a0_row) / fj.per_task[i]:8.0f} {fj.per_task[i]:6.2f}")
    print()
    print(line("primary (mean of --primary)", fa, fj))
    if extra:
        everything = primary + extra
        print(line("all (mean of every run)", factor(a0, everything, judge=False), factor(a0, everything)))
    for p, d in zip([*args.primary, *args.extra], [*primary, *extra], strict=True):
        print(line(f"alone: {p}", factor(a0, [d], judge=False), factor(a0, [d])))
    print(f"\npass on the {len(ids)}: A0 {passes(a0, ids)}/{len(ids)}", end="")
    for p, d in zip([*args.primary, *args.extra], [*primary, *extra], strict=True):
        print(f"; {p.parent.name or p.name} {passes(d, ids)}/{len(ids)}", end="")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
