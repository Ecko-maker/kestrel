"""Statistics for KestrelBench: how sure can we be about a score?

A pass rate from 100 tasks is an estimate. If the suite had happened to contain slightly different
tasks of the same kind, the score would move. The bootstrap measures how much: resample the task
results with replacement many times, recompute the score each time, and read the middle 95% of
those scores as the confidence interval. No new model calls are needed.

Comparisons use a *paired* bootstrap: both runs answered the same tasks, so we resample tasks and
look at the per-task difference. Task difficulty cancels out, which makes the interval much
tighter than comparing two separate intervals.

With repeats (the same task run several times), each task contributes its mean over the repeats,
and tasks, not individual runs, are resampled: repeats of one task are not independent evidence.
"""

import hashlib
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

RESAMPLES = 10_000
SEED = 2026  # fixed, so the same results always give the same interval
LEVEL = 0.95
MIN_TASKS_TO_COMPARE = 10  # fewer graded tasks than this: an interval is too wide to compare on
GRADED = ("pass", "fail")


@dataclass(frozen=True)
class Estimate:
    value: float  # the observed mean (pass rate, or a difference of pass rates)
    low: float
    high: float
    n: int  # tasks resampled

    def fmt(self, signed: bool = False) -> str:
        """71% (95% CI 62-79%, n=100), with an en dash; signed for differences: +3 points (95% CI -2 to +8, n=40)."""
        if signed:
            return (
                f"{self.value * 100:+.0f} points (95% CI {self.low * 100:+.0f} to {self.high * 100:+.0f}, n={self.n})"
            )
        return f"{self.value:.0%} (95% CI {self.low * 100:.0f}–{self.high * 100:.0f}%, n={self.n})"


def bootstrap_ci(
    values: Sequence[float], resamples: int = RESAMPLES, seed: int = SEED, level: float = LEVEL
) -> Estimate | None:
    """Percentile bootstrap of the mean. values: one number per task (1/0 pass, a score, a difference)."""
    n = len(values)
    if n == 0:
        return None
    rng = random.Random(seed)
    data = list(values)
    means = sorted(sum(rng.choices(data, k=n)) / n for _ in range(resamples))
    tail = (1 - level) / 2
    low = means[int(tail * resamples)]
    high = means[min(resamples - 1, int((1 - tail) * resamples))]
    return Estimate(round(sum(data) / n, 4), round(low, 4), round(high, 4), n)


def per_task_pass(results: list[dict[str, Any]]) -> dict[str, float]:
    """Task id -> fraction of its graded runs that passed (1.0 or 0.0 without repeats).
    Errors, exclusions and skips aren't graded and are left out."""
    runs: dict[str, list[float]] = defaultdict(list)
    for r in results:
        if r["status"] in GRADED:
            runs[r["id"]].append(1.0 if r["status"] == "pass" else 0.0)
    return {task: sum(v) / len(v) for task, v in runs.items()}


def pass_rate_ci(results: list[dict[str, Any]], **kw: Any) -> Estimate | None:
    return bootstrap_ci(list(per_task_pass(results).values()), **kw)


def category_cis(results: list[dict[str, Any]], **kw: Any) -> dict[str, dict[str, Any]]:
    """Per category: the interval, and whether there are too few tasks to compare on."""
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)
    out = {}
    for cat, rs in sorted(by_cat.items()):
        est = pass_rate_ci(rs, **kw)
        out[cat] = {"ci": est, "too_few": est is None or est.n < MIN_TASKS_TO_COMPARE}
    return out


def repeat_spread(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Run-to-run variance: pass rate of each repeat, and the tasks whose outcome changed between
    repeats (flaky). None without repeats."""
    repeats = sorted({r.get("repeat", 1) for r in results})
    if len(repeats) < 2:
        return None
    rates = {}
    for k in repeats:
        graded = [r for r in results if r.get("repeat", 1) == k and r["status"] in GRADED]
        rates[k] = round(sum(r["status"] == "pass" for r in graded) / len(graded), 4) if graded else None
    flaky = sorted(task for task, p in per_task_pass(results).items() if 0 < p < 1)
    return {"repeats": repeats, "pass_rates": rates, "flaky": flaky}


@dataclass
class Comparison:
    a_rate: Estimate | None
    b_rate: Estimate | None
    diff: Estimate | None  # B - A on the shared tasks, paired
    better_in_b: list[str]  # tasks whose pass rate went up from A to B
    worse_in_b: list[str]
    only_in_one: list[str]  # graded in one file but not the other: left out of the comparison
    changed_tasks: list[str]  # task definition differs between the runs: left out
    warnings: list[str]

    def within(self, margin: float) -> bool | None:
        """Non-inferiority: B is at most `margin` (absolute, 0.05 = 5 points) worse than A, with 95%
        confidence, when the whole interval of B - A lies above -margin."""
        return None if self.diff is None else self.diff.low > -margin


def compare(a: dict[str, Any], b: dict[str, Any], **kw: Any) -> Comparison:
    """Compare two results files (as loaded JSON) on the tasks graded in both."""
    warnings = []
    ma, mb = a.get("meta", {}), b.get("meta", {})
    va, vb = ma.get("suite_version"), mb.get("suite_version")
    if va != vb:
        warnings.append(
            f"different suite versions ({va or 'not recorded'} vs {vb or 'not recorded'}): checks may have "
            "changed between the runs (evals/CHANGELOG.md); tasks with changed definitions are left out "
            "when both files have task fingerprints"
        )
    if judge_label(ma) != judge_label(mb):
        warnings.append(
            f"different judges ({judge_label(ma)} vs {judge_label(mb)}): part of the difference may come from "
            "grading; re-judge one file with `kestrel bench rejudge` first"
        )
    pa, pb = per_task_pass(a["tasks"]), per_task_pass(b["tasks"])
    sha_a = {r["id"]: r.get("task_sha") for r in a["tasks"]}
    sha_b = {r["id"]: r.get("task_sha") for r in b["tasks"]}
    shared = sorted(set(pa) & set(pb))
    changed = [t for t in shared if sha_a.get(t) and sha_b.get(t) and sha_a[t] != sha_b[t]]
    if any(not (sha_a.get(t) and sha_b.get(t)) for t in shared):
        warnings.append("a file predates task versioning: can't verify both runs used identical task definitions")
    shared = [t for t in shared if t not in changed]
    diffs = [pb[t] - pa[t] for t in shared]
    return Comparison(
        a_rate=bootstrap_ci([pa[t] for t in shared], **kw),
        b_rate=bootstrap_ci([pb[t] for t in shared], **kw),
        diff=bootstrap_ci(diffs, **kw),
        better_in_b=[t for t in shared if pb[t] > pa[t]],
        worse_in_b=[t for t in shared if pb[t] < pa[t]],
        only_in_one=sorted(set(pa) ^ set(pb)),
        changed_tasks=changed,
        warnings=warnings,
    )


def judge_label(meta: dict[str, Any]) -> str:
    return f"{meta.get('judge')}@{meta.get('judge_version') or 'v1'}" if meta.get("judge") else "none"


# --- stable, stratified choices (sampling tasks, dev/held-out split) ---------------------


def stable_order[T](items: list[T], key: Callable[[T], str], seed: int = SEED) -> list[T]:
    """A shuffle that depends only on each item's key and the seed, not on the other items, so
    adding a task never reshuffles the rest."""
    return sorted(items, key=lambda x: hashlib.sha256(f"{seed}:{key(x)}".encode()).hexdigest())


def stratified_sample[T](
    items: list[T], n: int, category: Callable[[T], str], key: Callable[[T], str], seed: int = SEED
) -> list[T]:
    """n items spread over categories in proportion to their size (largest remainder), at least one
    per category when n allows; within a category, a stable seeded choice."""
    groups: dict[str, list[T]] = defaultdict(list)
    for it in items:
        groups[category(it)].append(it)
    if n >= len(items):
        return list(items)
    total = len(items)
    quota = {c: n * len(g) / total for c, g in groups.items()}
    alloc = {c: int(q) for c, q in quota.items()}
    if n >= len(groups):
        for c in alloc:
            alloc[c] = max(alloc[c], 1)
    for c in sorted(quota, key=lambda c: quota[c] - int(quota[c]), reverse=True):
        if sum(alloc.values()) >= n:
            break
        if alloc[c] < len(groups[c]):
            alloc[c] += 1
    while sum(alloc.values()) > n:  # the at-least-one rule overshot: trim the largest
        biggest = max(alloc, key=lambda c: alloc[c])
        alloc[biggest] -= 1
    picked = {id(x) for c, g in groups.items() for x in stable_order(g, key, seed)[: alloc[c]]}
    return [it for it in items if id(it) in picked]  # keep the suite's order


def split_half[T](
    items: list[T], category: Callable[[T], str], key: Callable[[T], str], seed: int = SEED
) -> dict[str, str]:
    """key -> "dev" or "heldout": within each category, a stable seeded order alternates the two."""
    groups: dict[str, list[T]] = defaultdict(list)
    for it in items:
        groups[category(it)].append(it)
    halves = ("dev", "heldout")
    return {key(x): halves[i % 2] for g in groups.values() for i, x in enumerate(stable_order(g, key, seed))}
