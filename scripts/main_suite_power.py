"""Power of the main-suite non-inferiority check for an unchanged agent (analysis only, no model calls).

Question: if the agent under test is identical to the reference agent, how often does
`kestrel bench compare REF AFTER` give a paired 95% lower bound above -MARGIN points?

Data: runs of the reference agent (cc5c16377662, suite 1.1, judge v2) measured before 2026-10-21:
the pinned baseline and run 2 (all 100 tasks) and the CI subset x 3 (16 tasks). Each task's pass
probability is modelled as Beta-distributed across tasks (a beta-binomial fitted by maximum likelihood
on every task's passes out of runs), so a task that passed every measured run can still fail now and
then, as real runs show. Each simulated after-run draws every task's pass probability from its
posterior, then R pass/fail runs; the reference is the real file(s), fixed (the after-run is compared
with those, not with fresh draws). The statistic is exactly compare's: per-task pass fraction, paired
differences, percentile bootstrap (stats.bootstrap_ci), with fewer inner resamples for speed.

    uv run python scripts/main_suite_power.py --ci-repeats ../kestrel-runs/evals/results/ci-repeat3-2026-10-07.json \\
        [--sims 2000] [--out evals/reports/main-suite-power.md]
"""

import argparse
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from kestrel.bench.stats import GRADED, bootstrap_ci

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "evals" / "baselines" / "baseline-v1.1-judge-v2-final.json"
RUN2 = ROOT / "evals" / "baselines" / "run2-v1.1-judge-v2-final.json"
REFERENCE_AGENT = "cc5c16377662"
MARGIN = 0.05
REPEATS = (1, 2, 3, 4, 6, 8)  # 1-3 asked for; more to find the smallest count with >= 80% (if any)
INNER_RESAMPLES = 2_000
SEED = 2026
BILLABLE_PER_RUN = 174_130  # main-after-v1.1 (2026-10-21), all 100 tasks in one part
DAILY_BUDGET = 180_000  # --token-budget per Groq day (200k rolling 24 h, minus headroom)


def statuses(path: Path) -> list[dict[str, Any]]:
    return [r for r in json.loads(path.read_text(encoding="utf-8"))["tasks"] if r["status"] in GRADED]


def counts(files: list[Path]) -> dict[str, list[int]]:
    """Task id -> [passes, graded runs] over every file."""
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for f in files:
        for r in statuses(f):
            out[r["id"]][0] += r["status"] == "pass"
            out[r["id"]][1] += 1
    return dict(out)


def log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def fit_beta_binomial(data: list[tuple[int, int]]) -> tuple[float, float]:
    """Maximum marginal likelihood on a log grid, refined once around the best point."""

    def loglik(a: float, b: float) -> float:
        return sum(log_beta(a + k, b + n - k) - log_beta(a, b) for k, n in data)

    best = max((loglik(a, b), a, b) for a in _grid(0.05, 200, 60) for b in _grid(0.01, 50, 60))
    _, a0, b0 = best
    best = max((loglik(a, b), a, b) for a in _grid(a0 / 1.5, a0 * 1.5, 30) for b in _grid(b0 / 1.5, b0 * 1.5, 30))
    return best[1], best[2]


def _grid(lo: float, hi: float, n: int) -> list[float]:
    return [lo * (hi / lo) ** (i / (n - 1)) for i in range(n)]


def lower_bound(diffs: list[float], seed: int) -> float:
    est = bootstrap_ci(diffs, resamples=INNER_RESAMPLES, seed=seed)
    assert est is not None
    return est.low


def per_task_fraction(files: list[Path]) -> dict[str, float]:
    c = counts(files)
    return {t: k / n for t, (k, n) in c.items()}


def simulate(
    posterior: dict[str, tuple[float, float]], reference: dict[str, float], repeats: int, sims: int, rng: random.Random
) -> tuple[float, float, float]:
    """(share of simulated after-runs that pass, mean lower bound, the margin an unchanged agent clears
    80% of the time = minus the 20th percentile of the lower bounds)."""
    tasks = sorted(reference)
    passed, lows = 0, []
    for s in range(sims):
        diffs = []
        for t in tasks:
            a, b = posterior[t]
            p = rng.betavariate(a, b)
            after = sum(rng.random() < p for _ in range(repeats)) / repeats
            diffs.append(after - reference[t])
        low = lower_bound(diffs, seed=SEED + s)
        lows.append(low)
        passed += low > -MARGIN
    lows.sort()
    return passed / sims, sum(lows) / sims, -lows[int(0.2 * sims)]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ci-repeats", type=Path, required=True, help="the CI subset x 3 results (2026-10-07)")
    parser.add_argument("--sims", type=int, default=2000)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    files = [BASELINE, RUN2, args.ci_repeats]
    for f in (RUN2, args.ci_repeats):
        agent = (json.loads(f.read_text(encoding="utf-8"))["meta"].get("agent") or {}).get("sha")
        if agent != REFERENCE_AGENT:
            sys.exit(f"{f.name}: agent {agent}, expected {REFERENCE_AGENT}")
    c = counts(files)
    a, b = fit_beta_binomial([(k, n) for k, n in c.values()])
    posterior = {t: (a + k, b + n - k) for t, (k, n) in c.items()}
    mean_p = sum(x / (x + y) for x, y in posterior.values()) / len(posterior)
    flips = sum(2 * (x / (x + y)) * (1 - x / (x + y)) for x, y in posterior.values())  # rough: E[p] plug-in
    one = [BASELINE]
    two = [BASELINE, RUN2]
    observed = sum(
        per_task_fraction([BASELINE]).get(t) != per_task_fraction([RUN2]).get(t)
        for t in per_task_fraction([BASELINE])
        if t in per_task_fraction([RUN2])
    )

    expected = {t: x / (x + y) for t, (x, y) in posterior.items()}
    bias = {}
    for refs in (one, two):
        ref = per_task_fraction(refs)
        bias[len(refs)] = sum(expected[t] - ref[t] for t in ref) / len(ref)
        print(f"expected after - reference ({len(refs)} ref): {bias[len(refs)] * 100:+.1f} pts")
    print(f"model: Beta({a:.2f}, {b:.2f}), mean p {mean_p:.1%}, predicted flips {flips:.1f}, observed {observed}")

    rng = random.Random(SEED)
    rows = []
    for refs, label in ((one, "1 reference (baseline)"), (two, "2 references (baseline + run 2, per-task mean)")):
        reference = per_task_fraction(refs)
        for r in REPEATS:
            share, mean_low, margin80 = simulate(posterior, reference, r, args.sims, rng)
            rows.append((label, r, share, mean_low, margin80))
            print(
                f"{label}, {r} after-run(s): passes {share:.0%} (mean lower bound {mean_low * 100:+.1f} pts; "
                f"80% clear a margin of {margin80 * 100:.1f} pts)"
            )

    # The check against a fixed reference also depends on how lucky that reference was: as a sensitivity
    # check, draw the reference fresh too (an unconditional power), for one reference and one after-run.
    rng2 = random.Random(SEED + 1)
    uncond = 0
    for s in range(args.sims):
        diffs = []
        for t in sorted(posterior):
            x, y = posterior[t]
            p_ref, p_after = rng2.betavariate(x, y), rng2.betavariate(x, y)
            diffs.append((rng2.random() < p_after) - (rng2.random() < p_ref))
        uncond += lower_bound(diffs, seed=SEED + 50_000 + s) > -MARGIN
    print(f"unconditional (fresh reference and after-run each time), 1 vs 1: passes {uncond / args.sims:.0%}")

    lines = [
        "# Main suite: power of the 5-point non-inferiority check",
        "",
        "Analysis only, no model calls (`scripts/main_suite_power.py`, 2026-10-21). Question: if the agent under test "
        "is identical to the reference agent, how often does `kestrel bench compare` give a paired 95% lower bound "
        f"above -{MARGIN * 100:.0f} points?",
        "",
        f"**Data** (reference agent `{REFERENCE_AGENT}`, suite 1.1, judge v2, all measured before 2026-10-21):",
        f"- `{BASELINE.name}` (sha256 `{sha(BASELINE)}`) and `{RUN2.name}` (`{sha(RUN2)}`): 100 tasks each;",
        f"- `{args.ci_repeats.name}` (`{sha(args.ci_repeats)}`): the 16-task CI subset x 3.",
        "",
        f"**Model.** Each task's pass probability ~ Beta({a:.2f}, {b:.2f}), fitted by maximum likelihood on "
        "every task's passes out of its runs (84 tasks with 2 runs, 16 with 5). Mean pass probability "
        f"{mean_p:.1%}; it predicts about {flips:.0f} tasks flipping between two runs of the same agent, against "
        f"{observed} observed (baseline vs run 2). For an identical agent the after-run is expected "
        f"{bias[1] * 100:+.1f} points from the baseline and {bias[2] * 100:+.1f} from baseline + run 2: a task that "
        "passed every measured run still has a true pass probability a little below 1, so a fixed reference that "
        "happened to pass is slightly optimistic (regression to the mean). "
        "Each simulated after-run draws every task's probability from its posterior and then R pass/fail runs; "
        "the reference is the real file (or both files, as repeats), fixed. "
        f"The statistic is compare's own (per-task pass fraction, paired percentile bootstrap) with "
        f"{INNER_RESAMPLES:,} inner resamples; {args.sims:,} simulated after-sides per row, seed {SEED}.",
        "",
        "| Reference | After-runs | P(lower bound > -5) for an unchanged agent | Mean lower bound | "
        "Margin an unchanged agent clears 80% of the time | Billable (after side) | Groq days |",
        "|---|---|---|---|---|---|---|",
    ]
    for label, r, share, mean_low, margin80 in rows:
        cost = r * BILLABLE_PER_RUN
        days = math.ceil(cost / DAILY_BUDGET)
        lines.append(
            f"| {label} | {r} | **{share:.0%}** | {mean_low * 100:+.1f} pts | {margin80 * 100:.1f} pts "
            f"| ~{cost / 1000:.0f}k | {days} |"
        )
    lines += [
        "",
        f"Unconditional check (the reference drawn fresh too, 1 vs 1): passes {uncond / args.sims:.0%}.",
        "",
        f"Cost: {BILLABLE_PER_RUN:,} billable per main run (measured 2026-10-21, one Groq day at "
        f"`--token-budget {DAILY_BUDGET}`). A second reference costs nothing: run 2 is already measured. Runs of "
        "the main suite and the safety split go on separate days.",
        "",
        "Limits: 84 tasks have only two measured runs, so each task's probability leans on the fitted prior; the "
        "judge's own run-to-run noise is inside the measured passes, not modelled apart; tasks are treated as "
        "independent.",
    ]
    text = "\n".join(lines) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"Report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
