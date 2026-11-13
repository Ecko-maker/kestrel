"""Power of rule C's statistic when the before side is measured fresh too (analysis only, no model calls).

Usage: uv run --no-sync python scripts/fresh_side_power.py [--sims 1000]

Same model as scripts/main_suite_power.py (each task's pass probability from the Beta-binomial fit on the
baseline, run 2 and the CI subset x3), but both sides are drawn: an unchanged agent runs the main suite
`before` times and `after` times, and rule C's paired 95% lower bound of after - before (per-task mean pass
fraction, bootstrap) is computed. Used by docs/google-step-plan.md section 4.1 (2026-11-13: 1,000 sims).
"""

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import main_suite_power as msp  # noqa: E402

CI_REPEATS = ROOT / "evals" / "reports" / "ci-repeat3-2026-10-07.json"
DESIGNS = ((1, 1), (1, 2), (2, 2), (2, 3), (3, 3))


def simulate(post: dict[str, tuple[float, float]], before: int, after: int, sims: int) -> list[float]:
    rng = random.Random(msp.SEED)
    lows = []
    for s in range(sims):
        diffs = []
        for t in sorted(post):
            p = rng.betavariate(*post[t])
            b = sum(rng.random() < p for _ in range(before)) / before
            a = sum(rng.random() < p for _ in range(after)) / after
            diffs.append(a - b)
        lows.append(msp.lower_bound(diffs, seed=msp.SEED + s))
    return sorted(lows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sims", type=int, default=1000)
    args = parser.parse_args()
    c = msp.counts([msp.BASELINE, msp.RUN2, CI_REPEATS])
    a, b = msp.fit_beta_binomial([(k, n) for k, n in c.values()])
    post = {t: (a + k, b + n - k) for t, (k, n) in c.items()}
    print(f"Beta({a:.2f}, {b:.2f}) on {len(post)} tasks, {args.sims} sims")
    for before, after in DESIGNS:
        lows = simulate(post, before, after, args.sims)
        share = sum(lo > -0.06 for lo in lows) / args.sims
        margin = -lows[int(0.2 * args.sims)] * 100
        print(f"before x{before}, after x{after}: P(lower bound > -6) = {share:.0%}; 80% margin {margin:.1f} pts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
