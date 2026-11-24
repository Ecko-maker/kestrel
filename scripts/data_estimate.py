"""The data step's token estimates (docs/data-rca-design.md 5.3), re-done with what the Google before side measured.
Offline: no model calls.

    uv run --no-sync python scripts/data_estimate.py                      # the table
    uv run --no-sync python scripts/data_estimate.py --a1 <A1's results>  # the same, with A1's measured f

Billable = dry run x measured overrun x schema factor f (the judge and the default-agent run carry no f).
- Dry runs (`kestrel bench run --dry-run`, 2026-11-23, on feature/kestrel-data-numbers): main suite 176,764
  (--estimate-from main-after-v1.1.json); the 30 old attacks strict x3 189,000 (--estimate-from
  safety-after-v21-strict-x3.json). The 5 data attacks and twins (75,000 each) and rca have no measured runs, so the
  design's planning figures stand: the tool's own default there (3,300 tokens per run) is far too low for them.
- Overruns: safety x1.178 (B2: 111,462 against 94,569). Main x1.31 is the owner's figure; B4 measured x1.534 (271,287
  against 176,764), so the main-suite rows here use B4's own tokens, and the table prints x1.31 beside them.
- f: A0 measured 2.185 on agent tokens for 3 Google schemas (8,440 characters). The 5 data schemas are 4,421, so the
  planning f is 1 + 1.185 x 4,421 / 8,440 = 1.62. Low 1.00 (all cached), high 2.185 (as A0). A0's f also holds the
  extra calls the model made, so scaling by schema size is an assumption: A1's measured f replaces it.
- A1's f: its 30 old attacks' billable against the same tasks' in safety-after-v21-strict-x3 (136,469) x B2's drift
  against that file's benign runs (111,462 / 93,746).
- Proposed cap: the planning estimate x 1.15, rounded up to 1,000.
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

MAIN_DRY, ATTACKS_DRY, NEW_DRY = 176_764, 189_000, 75_000
RCA_DRY = (9_500, 15_500, 21_500)  # per run with the judge, low / planning / high (design 3.5)
B4_AGENT, B4_JUDGE = 238_120, 33_167
SAFETY_OVERRUN, MAIN_OVERRUN = 1.178, 1.31
F_GOOGLE, GOOGLE_CHARS, DATA_CHARS = 2.185, 8_440, 4_421
F_PLAN = 1 + (F_GOOGLE - 1) * DATA_CHARS / GOOGLE_CHARS
V21_ATTACKS, B2, V21_BENIGN = 136_469, 111_462, 93_746
PER_DAY = 185_000
CAP = 1.15


@dataclass
class Row:
    run: str
    dry: int
    how: str
    low: float
    plan: float
    high: float

    @property
    def cap(self) -> int:
        return math.ceil(self.plan * CAP / 1000) * 1000


def rows(f: tuple[float, float, float] = (1.0, F_PLAN, F_GOOGLE)) -> list[Row]:
    def safety(dry: int) -> list[float]:
        return [dry * SAFETY_OVERRUN * x for x in f]

    def rca(n: int) -> list[float]:
        return [n * x * MAIN_OVERRUN for x in RCA_DRY]  # the design's figures include the schemas: no f

    b1 = B4_AGENT + B4_JUDGE
    return [
        Row("B1 main x1, default agent", MAIN_DRY, "B4 as measured", MAIN_DRY * MAIN_OVERRUN, b1, b1),
        Row("A1 attacks strict x3 (105)", ATTACKS_DRY + NEW_DRY, "x1.178 x f", *safety(ATTACKS_DRY + NEW_DRY)),
        Row("A1b 5 twins strict x3 (15)", NEW_DRY, "x1.178 x f", *safety(NEW_DRY)),
        Row("A2 main x1, --tools data", MAIN_DRY, "B4 agent x f + judge", *(B4_AGENT * x + B4_JUDGE for x in f)),
        Row("A3 rca, first 5", 5 * RCA_DRY[1], "x1.31", *rca(5)),
        Row("A3 rca, other 25", 25 * RCA_DRY[1], "x1.31", *rca(25)),
    ]


def measured_f(a1: dict) -> float:
    """A1's 30 old attacks against the default agent's expected cost on them now."""
    old = [r for r in a1["tasks"] if not r["id"].startswith("atk-data-")]
    if len(old) != 90 or any(r["status"] not in ("pass", "fail") for r in old):
        raise SystemExit("STOP: A1's 30 old attacks x3 aren't all graded")
    spent = sum(int(r["tokens"]) - int(r.get("cached_tokens") or 0) for r in old)
    return spent / (V21_ATTACKS * B2 / V21_BENIGN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--a1", type=Path, help="A1's results file, once it is fully graded")
    args = parser.parse_args(argv)
    f = (1.0, F_PLAN, F_GOOGLE)
    if args.a1:
        m = measured_f(json.loads(args.a1.read_text(encoding="utf-8")))
        f = (m, m, m)
        print(f"A1's measured f: {m:.2f}")
    print(f"f low / planning / high: {f[0]:.2f} / {f[1]:.2f} / {f[2]:.2f}")
    print("| Run | Dry run | Method | Low | Planning | High | Proposed cap |")
    print("|---|---|---|---|---|---|---|")
    table = rows(f)
    for r in table:
        print(f"| {r.run} | {r.dry:,} | {r.how} | {r.low:,.0f} | {r.plan:,.0f} | {r.high:,.0f} | {r.cap:,} |")
    low, plan, high = (sum(getattr(r, k) for r in table) for k in ("low", "plan", "high"))
    days = " / ".join(f"{x / PER_DAY:.1f}" for x in (low, plan, high))
    print(f"| **Total** | | | {low:,.0f} | {plan:,.0f} | {high:,.0f} | {sum(r.cap for r in table):,} |")
    print(f"Groq days at {PER_DAY:,} a day: {days}. -CapA3 is the two A3 caps added.")
    print(f"Main suite at x1.31 of the dry run: {MAIN_DRY * MAIN_OVERRUN:,.0f}; B4 measured {B4_AGENT + B4_JUDGE:,}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
