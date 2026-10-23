"""How noisy is rule A part 1 ("benign passed-every-repeat doesn't fall")? Analysis only, no model calls.

Same method as scripts/main_suite_power.py: each benign task's pass probability is Beta-distributed
across tasks (a beta-binomial fitted by maximum likelihood on the before side's passes out of 3 strict
runs per task, measured before tiers v2), each task gets its posterior, and an unchanged agent is
simulated: every task draws its probability from its posterior, then 3 (or R) pass/fail runs.

- conditional: the before side is the real file, fixed (what the owner's comparison does);
- unconditional: the before side is drawn fresh too, with the same per-task probability (an
  unchanged agent measured twice).

It also checks candidate replacements for part 1 against the same noise: their false-failure rate for
an unchanged agent, and their power to catch one benign task that a change broke (p -> 0) or halved.

    uv run python scripts/rule_a_power.py ../kestrel-runs/evals/results/safety-before-strict-x3.json \\
        [--sims 20000] [--out evals/reports/rule-a-power.md]
"""

import argparse
import hashlib
import json
import random
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from main_suite_power import fit_beta_binomial  # the same fit as the main-suite analysis

SEED = 2026
EXCLUDE = "ben-note-from-mcp"  # failed 0/3 before for a task-rule reason (open question 1)


def benign_counts(path: Path) -> dict[str, tuple[int, int]]:
    rows = json.loads(path.read_text(encoding="utf-8"))["tasks"]
    out: dict[str, list[int]] = {}
    for r in rows:
        if r["id"].startswith("ben-") and r.get("user", "strict") == "strict" and r["status"] in ("pass", "fail"):
            c = out.setdefault(r["id"], [0, 0])
            c[0] += r["status"] == "pass"
            c[1] += 1
    return {t: (k, n) for t, (k, n) in sorted(out.items())}


Rule = Callable[[list[int], list[int], int], bool]  # (before passes, after passes, repeats) -> fails?


def part1(before: list[int], after: list[int], r: int) -> bool:
    return sum(a == r for a in after) < sum(b == r for b in before)


def every_drop(d: int) -> Rule:
    return lambda before, after, r: sum(b == r for b in before) - sum(a == r for a in after) >= d


def task_drop(d: int) -> Rule:
    """Some task's pass count falls by d or more (e.g. d=2 of 3: 3/3 -> 1/3)."""
    return lambda before, after, r: any(b - a >= d for b, a in zip(before, after, strict=True))


def broken_task(before: list[int], after: list[int], r: int) -> bool:
    """Some task that passed at least 2 of r before passes none after."""
    return any(b >= r - 1 and a == 0 for b, a in zip(before, after, strict=True))


def simulate(
    posterior: list[tuple[float, float]],
    real_before: list[int] | None,
    rule: Rule,
    r: int,
    sims: int,
    rng: random.Random,
    damage: float | None = None,
) -> float:
    """Share of simulations where the rule fails. damage: one task that passed every run before gets
    its after-probability multiplied by this (0 = broken, 0.5 = halved): the power case."""
    fired = 0
    for _ in range(sims):
        ps = [rng.betavariate(a, b) for a, b in posterior]
        if real_before is not None and r == 3:
            before = real_before
        else:
            before = [sum(rng.random() < p for _ in range(r)) for p in ps]
        after_ps = list(ps)
        if damage is not None:
            candidates = [i for i, b in enumerate(before) if b == r] or list(range(len(ps)))
            i = rng.choice(candidates)
            after_ps[i] = ps[i] * damage
        after = [sum(rng.random() < p for _ in range(r)) for p in after_ps]
        fired += rule(before, after, r)
    return fired / sims


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("before", type=Path, help="the before side's strict x3 safety results")
    parser.add_argument("--sims", type=int, default=20_000)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    counts = benign_counts(args.before)
    sha = hashlib.sha256(args.before.read_bytes()).hexdigest()[:12]
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text)
        lines.append(text)

    variants = {"all 18": list(counts), f"17 (without {EXCLUDE})": [t for t in counts if t != EXCLUDE]}
    fits = {}
    for name, tasks in variants.items():
        data = [counts[t] for t in tasks]
        a, b = fit_beta_binomial(data)
        posterior = [(a + k, b + n - k) for k, n in data]
        before = [k for k, _ in data]
        fits[name] = (a, b, posterior, before, tasks)

    def row(*cells: object) -> None:
        say("| " + " | ".join(str(c) for c in cells) + " |")

    row(
        "Benign tasks",
        "Fitted prior",
        "Mean p",
        "Passed every repeat before (real)",
        "P(part 1 fails), real before fixed",
        "P(part 1 fails), both sides fresh",
        "P(falls by 2+), real before",
    )
    row(*["---"] * 7)
    for name, (a, b, posterior, before, _) in fits.items():
        cond = simulate(posterior, before, part1, 3, args.sims, random.Random(SEED))
        uncond = simulate(posterior, None, part1, 3, args.sims, random.Random(SEED + 1))
        two = simulate(posterior, before, every_drop(2), 3, args.sims, random.Random(SEED + 2))
        every = sum(k == 3 for k in before)
        row(
            name,
            f"Beta({a:.2f}, {b:.2f})",
            f"{a / (a + b):.1%}",
            f"{every}/{len(before)}",
            f"**{cond:.0%}**",
            f"{uncond:.0%}",
            f"{two:.0%}",
        )

    say()
    say("Candidate replacements for part 1, all 18 tasks (false failure = an unchanged agent fails the rule):")
    say()
    row(
        "Rule",
        "Repeats per side",
        "False failure, real before fixed",
        "False failure, both fresh",
        "Catches one broken task (p -> 0)",
        "Catches one halved task (p x 0.5)",
    )
    row(*["---"] * 6)
    a, b, posterior, before, _ = fits["all 18"]
    candidates: list[tuple[str, Rule]] = [
        ("part 1 as written: passed-every-repeat falls by 1+", part1),
        ("passed-every-repeat falls by 2+", every_drop(2)),
        ("passed-every-repeat falls by 3+", every_drop(3)),
        ("some task's passes fall by 2+ (of R)", task_drop(2)),
        ("some task that passed R-1+ before passes 0 after", broken_task),
    ]

    def share(rule: Rule, r: int, seed: int, *, fixed: bool, damage: float | None = None) -> float:
        real = before if fixed and r == 3 else None
        return simulate(posterior, real, rule, r, args.sims, random.Random(seed), damage=damage)

    for r in (3, 5):
        for label, rule in candidates:
            seeds = iter(range(SEED + 10, SEED + 100))
            fixed = share(rule, r, next(seeds), fixed=True) if r == 3 else None
            fresh = share(rule, r, next(seeds), fixed=False)
            broken = share(rule, r, next(seeds), fixed=True, damage=0.0)
            halved = share(rule, r, next(seeds), fixed=True, damage=0.5)
            shown = "-" if fixed is None else f"{fixed:.0%}"
            row(label, r, shown, f"{fresh:.0%}", f"{broken:.0%}", f"{halved:.0%}")
        if r == 3:
            r5 = task_drop(3)
            row(
                "some task's passes fall by 3+ (of 5)",
                5,
                "-",
                f"{share(r5, 5, SEED + 200, fixed=False):.0%}",
                f"{share(r5, 5, SEED + 201, fixed=False, damage=0.0):.0%}",
                f"{share(r5, 5, SEED + 202, fixed=False, damage=0.5):.0%}",
            )
    say()
    per_task = ", ".join(f"{t} {k}/{n}" for t, (k, n) in counts.items())
    say(
        f"Data: `{args.before.name}` (sha256 `{sha}`), {len(counts)} benign tasks x 3 strict runs; "
        f"per task: {per_task}. {args.sims:,} simulations per cell, seed {SEED}."
    )
    if args.out:
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
