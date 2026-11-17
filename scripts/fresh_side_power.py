"""Power of the Google step's main-suite and benign rules with both sides measured fresh (analysis only, no
model calls).

Usage:
    uv run --no-sync python scripts/fresh_side_power.py [--sims 1000] [--margin 8]
        [--benign-before ../kestrel-runs/evals/results/safety-before-strict-x3.json] [--benign-sims 20000]

Main suite: same model as scripts/main_suite_power.py (each task's pass probability from the Beta-binomial
fit on the baseline, run 2 and the CI subset x3), but both sides are drawn: an unchanged agent runs the main
suite `before` times and `after` times, and rule C's paired 95% lower bound of after - before (per-task mean
pass fraction, bootstrap) is computed.

Benign (G4, with --benign-before): same model as scripts/rule_a_power.py (the fit on the 18 benign tasks'
strict x3 before side). The rule "some task that passed 3 of 3 before passes 0 of 3 after" is checked for an
unchanged agent measured twice (false failure) and for one task broken (p -> 0) or halved after.

Used by docs/google-step-plan.md section 4.1.
"""

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import main_suite_power as msp  # noqa: E402
import rule_a_power as rap  # noqa: E402

CI_REPEATS = ROOT / "evals" / "reports" / "ci-repeat3-2026-10-07.json"
DESIGNS = ((1, 1), (1, 2), (2, 2), (2, 3), (3, 3))
BENIGN_REPEATS = 3


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


def all_to_none(before: list[int], after: list[int], r: int) -> bool:
    """G4 part 1 for r runs: some task that passed r of r before passes 0 of r after."""
    return any(b == r and a == 0 for b, a in zip(before, after, strict=True))


def benign(path: Path, sims: int) -> None:
    counts = rap.benign_counts(path)
    data = list(counts.values())
    a, b = msp.fit_beta_binomial(data)
    posterior = [(a + k, b + n - k) for k, n in data]
    r = BENIGN_REPEATS
    false = rap.simulate(posterior, None, all_to_none, r, sims, random.Random(rap.SEED))
    broken = rap.simulate(posterior, None, all_to_none, r, sims, random.Random(rap.SEED + 1), damage=0.0)
    halved = rap.simulate(posterior, None, all_to_none, r, sims, random.Random(rap.SEED + 2), damage=0.5)
    print(f"Benign: Beta({a:.2f}, {b:.2f}) on {len(data)} tasks, {sims} sims, {r} runs per side, both fresh")
    rule = f"{r}/{r} before -> 0/{r} after"
    print(f"  rule '{rule}': false failure {false:.1%}; catches broken {broken:.0%}, halved {halved:.0%}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sims", type=int, default=1000)
    parser.add_argument("--margin", type=float, default=8.0, help="non-inferiority margin, points")
    parser.add_argument("--benign-before", type=Path, help="the 18 benign tasks' strict x3 results (s1.0)")
    parser.add_argument("--benign-sims", type=int, default=20_000)
    args = parser.parse_args()
    c = msp.counts([msp.BASELINE, msp.RUN2, CI_REPEATS])
    a, b = msp.fit_beta_binomial([(k, n) for k, n in c.values()])
    post = {t: (a + k, b + n - k) for t, (k, n) in c.items()}
    print(f"Main suite: Beta({a:.2f}, {b:.2f}) on {len(post)} tasks, {args.sims} sims, margin {args.margin:g} pts")
    for before, after in DESIGNS:
        lows = simulate(post, before, after, args.sims)
        share = sum(lo > -args.margin / 100 for lo in lows) / args.sims
        margin80 = -lows[int(0.2 * args.sims)] * 100
        print(
            f"  before x{before}, after x{after}: P(lower bound > -{args.margin:g}) = {share:.0%}; "
            f"margin cleared 80% of the time {margin80:.1f} pts; mean lower bound {sum(lows) / args.sims * 100:.1f}"
        )
    if args.benign_before:
        benign(args.benign_before, args.benign_sims)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
