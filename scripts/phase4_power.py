"""Power of the Phase 4 claim "routed >= 0.95 x teacher" (analysis only, no model calls).

The simulation behind docs/phase4-plan.md section 8.3 (pre-registration, frozen 2026-10-30).

Statistic (section 8.2): per task, D = routed pass fraction - 0.95 x teacher pass fraction, each over its
repeats; the claim is shown when the percentile-bootstrap 95% lower bound of mean(D) over the 100 tasks is
> 0. Primary metric "checks": a graded run passes when every deterministic check is ok (the judge
ignored). Secondary "full": the full pass rule.

Teacher side: the real 10-28 after-run x3 (agent 35fac5ba6277), fixed, optionally plus F fresh simulated
teacher repeats. Routed side: per-task pass probabilities drawn from the posterior of a beta-binomial fitted
to the after-run x3 (scripts/main_suite_power.py's fit), then a scenario (k tasks lost for good, k gained,
or every probability times f), then R pass/fail runs. Cross-checks: flips between the pinned baseline and
run 2, and (if given) the CI subset x3 of 2026-10-07, which lives outside git.

Seeds: one numpy Generator(2026) for every draw, consumed in the order below (metric, scenario, fresh
repeats, R, simulation), so the numbers depend on that order; the bootstrap resamples come from a separate
Generator(2026). 2,000 simulations per cell, 2,000 bootstrap resamples.

    uv run python scripts/phase4_power.py [--ci-repeats ../kestrel-runs/evals/results/ci-repeat3-2026-10-07.json]
"""

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
INPUTS = {  # path, sha256 prefix as section 8 records it
    "after": (ROOT / "evals" / "after-runs" / "main-after-v21-x3.json", "8948b434fdcf"),
    "baseline": (ROOT / "evals" / "baselines" / "baseline-v1.1-judge-v2-final.json", "bfd6bdeb6c3c"),
    "run2": (ROOT / "evals" / "baselines" / "run2-v1.1-judge-v2-final.json", "29a607859aee"),
}
CI_REPEATS_SHA = "4750d773e346"  # ci-repeat3-2026-10-07.json, optional (not in git)
RATIO, CONTEXT_RATIO = 0.95, 0.90
SIMS = INNER = 2000
SEED = 2026
REPS = (1, 3, 5, 8)
FRESH = (0, 3)
METRICS = ("checks", "full")
# name -> (lose, gain): lose > 0 = that many tasks the teacher nearly always passes fail for good; lose < 0
# = every probability times -lose; gain = that many tasks the teacher often fails always pass
SCENARIOS: dict[str, tuple[float, int]] = {
    "identical (ratio 1.00)": (0, 0),
    "loses 2 tasks (~0.98)": (2, 0),
    "loses 3 tasks (~0.97)": (3, 0),
    "loses 4 tasks (~0.96)": (4, 0),
    "swaps 3 lost / 3 gained": (3, 3),
    "loses 6 tasks (~0.94, should fail)": (6, 0),
    "diffuse x0.98": (-0.98, 0),
    "diffuse x0.97": (-0.97, 0),
    "diffuse x0.95 (boundary: false-pass rate)": (-0.95, 0),
}
Cell = tuple[str, int, int]  # (scenario, fresh teacher repeats, routed repeats R)


def _fit() -> Callable[[list[tuple[int, int]]], tuple[float, float]]:
    spec = importlib.util.spec_from_file_location("main_suite_power", Path(__file__).with_name("main_suite_power.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fit: Callable[[list[tuple[int, int]]], tuple[float, float]] = module.fit_beta_binomial
    return fit


fit_beta_binomial = _fit()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def check_inputs() -> None:
    for path, expected in INPUTS.values():
        if sha(path) != expected:
            sys.exit(f"{path.name}: sha256 {sha(path)}, but section 8 was computed on {expected}")


def counts(paths: list[Path], metric: str) -> dict[str, list[int]]:
    """Task id -> [passes, graded runs]."""
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for p in paths:
        for r in json.loads(p.read_text(encoding="utf-8"))["tasks"]:
            if r["status"] in ("pass", "fail"):
                ok = all(c["ok"] for c in r["checks"]) if metric == "checks" else r["status"] == "pass"
                out[r["id"]][0] += ok
                out[r["id"]][1] += 1
    return dict(out)


def model(metric: str) -> dict[str, Any]:
    """The fitted beta-binomial on the after-run x3 and the cross-checks."""
    c = counts([INPUTS["after"][0]], metric)
    tasks = sorted(c)
    k = np.array([c[t][0] for t in tasks], float)
    n = np.array([c[t][1] for t in tasks], float)
    a, b = fit_beta_binomial([(int(x), int(y)) for x, y in zip(k, n, strict=True)])
    pa, pb = a + k, b + n - k
    pm = pa / (pa + pb)
    cb, c2 = counts([INPUTS["baseline"][0]], metric), counts([INPUTS["run2"][0]], metric)
    observed = sum((cb[t][0] / cb[t][1]) != (c2[t][0] / c2[t][1]) for t in cb if t in c2)
    return {
        "tasks": tasks, "k": k, "n": n, "a": a, "b": b, "pa": pa, "pb": pb,
        "teacher": float((k / n).mean()), "posterior_mean": float(pm.mean()), "with_a_fail": int((k < n).sum()),
        "flips_observed": observed, "flips_predicted": float(np.sum(2 * pm * (1 - pm))),
    }  # fmt: skip


def lower_bounds(d: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Each row's percentile-bootstrap 2.5% bound of the mean, one row at a time with d[idx].mean(axis=1),
    exactly as section 8's numbers were computed. Batching (a matrix product, or one 3-D gather) sums in
    another order, about 1e-16 apart, which flips the bounds that sit exactly at 0."""
    return np.array([np.quantile(row[idx].mean(axis=1), 0.025) for row in d])


def simulate(
    metric: str, rng: np.random.Generator, evaluate: Callable[[Cell], bool] | None = None
) -> dict[Cell, dict[str, float]]:
    """Every cell's draws are taken from rng in a fixed order; the bootstrap runs only for the cells
    `evaluate` selects (all by default), so a check of a few cells reproduces the full run's numbers."""
    m = model(metric)
    k, n, pa, pb = m["k"], m["n"], m["pa"], m["pb"]
    teacher_real = k / n
    T = len(k)
    idx = np.random.default_rng(SEED).integers(0, T, size=(INNER, T))
    pm = pa / (pa + pb)
    high, low = np.where(pm >= 0.9)[0], np.where(pm < 0.8)[0]
    out: dict[Cell, dict[str, float]] = {}
    for name, (lose, gain) in SCENARIOS.items():
        for fresh in FRESH:
            for R in REPS:
                d95, d90, ratios = np.empty((SIMS, T)), np.empty((SIMS, T)), np.empty(SIMS)
                for s in range(SIMS):
                    p = rng.beta(pa, pb)
                    q = p.copy()
                    if lose < 0:
                        q = p * -lose
                    elif lose:
                        q[rng.choice(high, int(lose), replace=False)] = 0.0
                    if gain:
                        q[rng.choice(low, gain, replace=False)] = 1.0
                    routed = rng.binomial(R, q) / R
                    teacher = teacher_real
                    if fresh:
                        pt = rng.beta(pa, pb)
                        teacher = (k + rng.binomial(fresh, pt)) / (n + fresh)
                    d95[s], d90[s] = routed - RATIO * teacher, routed - CONTEXT_RATIO * teacher
                    ratios[s] = q.mean() / p.mean()
                cell = (name, fresh, R)
                if evaluate is not None and not evaluate(cell):
                    continue
                lb95, lb90 = lower_bounds(d95, idx), lower_bounds(d90, idx)
                out[cell] = {
                    "power": float(np.mean(lb95 > 0)),
                    "mean_lower_bound": float(lb95.mean()),
                    "true_ratio": float(ratios.mean()),
                    "power_at_0.90": float(np.mean(lb90 > 0)),
                }
    return out


def run_all(evaluate: Callable[[str, Cell], bool] | None = None) -> dict[str, dict[Cell, dict[str, float]]]:
    """Both metrics in section 8's order, from one Generator(SEED)."""
    rng = np.random.default_rng(SEED)
    out = {}
    for metric in METRICS:

        def selected(cell: Cell, metric: str = metric) -> bool:
            return evaluate is None or evaluate(metric, cell)

        out[metric] = simulate(metric, rng, selected)
    return out


def table(cells: dict[Cell, dict[str, float]]) -> list[str]:
    lines = [
        "| routed scenario [true ratio] | fresh teacher | "
        + " | ".join(f"R={R}" for R in REPS)
        + " | 0.90, R=3 / R=5 |",
        "|---|---|" + "---|" * len(REPS) + "---|",
    ]
    for name in SCENARIOS:
        for fresh in FRESH:
            row = [cells[(name, fresh, R)] for R in REPS]
            alt = f"{cells[(name, fresh, 3)]['power_at_0.90']:.0%} / {cells[(name, fresh, 5)]['power_at_0.90']:.0%}"
            power = " | ".join(f"{c['power']:.0%}" for c in row)
            lines.append(f"| {name} [{row[0]['true_ratio']:.3f}] | {fresh} | {power} | {alt} |")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ci-repeats", type=Path, help="the CI subset x3 of 2026-10-07 (cross-check only)")
    args = parser.parse_args()
    check_inputs()
    for name, (path, _) in INPUTS.items():
        print(f"{name}: {path.name} {sha(path)}")
    results = run_all()
    for metric in METRICS:
        m = model(metric)
        print(
            f"\n## metric={metric}: teacher after-run x3 = {m['teacher']:.3f}; Beta({m['a']:.2f},{m['b']:.2f}); "
            f"posterior mean {m['posterior_mean']:.3f}; tasks with a fail in 3: {m['with_a_fail']}"
        )
        print(
            f"flips baseline vs run2 (observed): {m['flips_observed']}; "
            f"predicted for two runs of the after-run agent: {m['flips_predicted']:.1f}"
        )
        if args.ci_repeats:
            if sha(args.ci_repeats) != CI_REPEATS_SHA:
                sys.exit(f"{args.ci_repeats.name}: sha256 {sha(args.ci_repeats)}, expected {CI_REPEATS_SHA}")
            cc = counts([args.ci_repeats], metric)
            var = np.mean([(x / y) * (1 - x / y) * y / (y - 1) for x, y in cc.values()])
            print(f"CI subset x3: {len(cc)} tasks, mean within-task run variance {var:.3f}")
        print("\n".join(table(results[metric])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
