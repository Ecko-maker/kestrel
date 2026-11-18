"""The evidence measures and the confidence label (docs/data-rca-design.md section 2), on daily series.

Pure numpy, no I/O: the tools feed it series read from DuckDB, the offline calibration feeds it series from the
generator, and both get the same numbers. A series is an array of one metric per day, day 0 = the dataset's first
date (every day present, zero where nothing happened). A window is a day range, both ends included.

As built (decisions the design left open, recorded in docs/data.md):
- **One year = 364 days** (52 weeks), so last year's window starts on the same weekday.
- **Seasonal expected value** `Ŷ_C = Y_B x r`, `r = Y(C - 1y) / Y(B - 1y)` ("same periods last year"); without a
  year of history before B (or with a zero last-year total) `r` comes from the weekday profile: the mean per weekday
  over all days before C, summed over C's days and over B's days.
- **Noise:** the same estimator on earlier window pairs, pair k = (B, C) shifted back by k x len(C) days for
  k = 1..26 (the current windows of the pairs don't overlap), keeping only pairs with the history the main estimate's
  method needs. `sigma = 1.4826 x MAD` of their gaps. Fewer than 8 pairs (or sigma = 0): no noise estimate, every
  label is low and the tool says why. The generator's 3.5 years give all 26 pairs for a month (owner, 2026-11-17).
- **Sub-periods** for consistency: 7-day blocks from C's start (the remainder joins the last block) when C has at
  least 4 of them, else single days. A candidate's expected value for a block is its Ŷ_C spread by last year's shape
  over C (or by the weekday profile), so the block gaps sum to its gap.
- **Price, volume, mix** on revenue = Σ pᵢqᵢ over products, base quantities scaled by the revenue ratio r: price
  = Σ (p_C,i - p_B,i) q_C,i, volume = (Q_C - Q̃_B) p̄_B, mix = Σ p_B,i q_C,i - p̄_B Q_C; they sum exactly to G. A product
  with no units in B takes its C price as p_B (no price effect; its revenue shows in mix).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np

YEAR = 364
MAX_PAIRS = 26
MIN_PAIRS = 8
MAD_SCALE = 1.4826
MIN_WEEKS = 4
LAST_YEAR = "same periods last year"
WEEKDAY = "weekday profile (less than a year of history)"
PVM = ("price", "volume", "mix")


@dataclass(frozen=True)
class Cutoffs:
    """Every measure must clear its bar (no weighted sum): docs/data-rca-design.md 2.3."""

    z_total: float
    high_share: float
    high_z: float
    high_consistency: float
    medium_share: float
    medium_z: float
    medium_consistency: float


# The design's starting values (2.3). Tuned on the generator's development seeds only (seeds 1-1000 per scenario,
# scripts/rca_calibrate.py tune), then frozen here; tests/test_data_analysis.py pins them to the tuning record in
# evals/kestrelbench/rca/calibration/tuning.json. Held-out seeds (10001-11000) are for the report only.
# Tuning (2026-11-17) kept the starting values: they met all four targets on the dev seeds (high 4,787/4,789,
# medium 312/400, low 1,901/68,776, no-change any 28/1,000), so the closest grid point is the start itself.
START = Cutoffs(2.0, 0.50, 3.0, 0.75, 0.25, 2.0, 0.50)
CUTOFFS = Cutoffs(2.0, 0.50, 3.0, 0.75, 0.25, 2.0, 0.50)
FROZEN = "2026-11-17"  # the date CUTOFFS was frozen (scripts/rca_calibrate.py tune)


@dataclass(frozen=True)
class Window:
    start: int
    end: int  # inclusive

    @property
    def days(self) -> int:
        return self.end - self.start + 1

    def shift(self, days: int) -> Window:
        return Window(self.start - days, self.end - days)


def window(day0: date, start: date, end: date) -> Window:
    return Window((start - day0).days, (end - day0).days)


class Series:
    """A daily series with prefix sums, for O(1) window totals and weekday means."""

    def __init__(self, values: np.ndarray, weekday0: int):
        self.y = np.asarray(values, dtype=float)
        self.n = len(self.y)
        self.weekday0 = weekday0  # weekday of day 0, Monday = 0
        self.cs = np.concatenate([[0.0], np.cumsum(self.y)])
        wd = (np.arange(self.n) + weekday0) % 7
        self.wd = wd
        # per weekday: prefix sums of values and counts, for "mean per weekday over days before d"
        self.wcs = np.zeros((7, self.n + 1))
        self.wcnt = np.zeros((7, self.n + 1))
        for w in range(7):
            mask = wd == w
            self.wcs[w, 1:] = np.cumsum(np.where(mask, self.y, 0.0))
            self.wcnt[w, 1:] = np.cumsum(mask)

    def total(self, w: Window) -> float:
        return float(self.cs[w.end + 1] - self.cs[w.start])

    def has(self, w: Window) -> bool:
        return 0 <= w.start <= w.end < self.n

    def weekday_means(self, before: int) -> np.ndarray:
        """Mean per weekday over days [0, before)."""
        cnt = self.wcnt[:, before]
        return np.divide(self.wcs[:, before], cnt, out=np.zeros(7), where=cnt > 0)

    def weekday_weight(self, w: Window, means: np.ndarray) -> float:
        days = (np.arange(w.start, w.end + 1) + self.weekday0) % 7
        return float(means[days].sum())


@dataclass(frozen=True)
class Estimate:
    expected: float
    ratio: float
    method: str
    actual: float
    base: float

    @property
    def gap(self) -> float:
        return self.actual - self.expected


def method_for(s: Series, b: Window, c: Window) -> str:
    if b.start - YEAR >= 0 and c.start - YEAR >= 0 and s.total(b.shift(YEAR)) > 0:
        return LAST_YEAR
    return WEEKDAY


def estimate(s: Series, b: Window, c: Window, method: str | None = None) -> Estimate:
    method = method or method_for(s, b, c)
    base, actual = s.total(b), s.total(c)
    ratio = float("nan")
    if method == LAST_YEAR:
        denom = s.total(b.shift(YEAR))
        if denom > 0:
            ratio = s.total(c.shift(YEAR)) / denom
    if ratio != ratio:  # weekday profile (also when last year's base was zero for this series)
        means = s.weekday_means(c.start)
        wb = s.weekday_weight(b, means)
        ratio = s.weekday_weight(c, means) / wb if wb > 0 else c.days / b.days
    return Estimate(base * ratio, ratio, method, actual, base)


def backtest(s: Series, b: Window, c: Window, method: str, step: int | None = None) -> list[float]:
    """Gaps of the same estimator on up to 26 earlier pairs, shifted back by k x step days (default len(C))."""
    step = step or c.days
    gaps = []
    for k in range(1, MAX_PAIRS + 1):
        bb, cc = b.shift(k * step), c.shift(k * step)
        if bb.start < 0 or cc.start < 0:
            break
        if method == LAST_YEAR and (bb.start - YEAR < 0 or cc.start - YEAR < 0):
            break
        if method == WEEKDAY and min(bb.start, cc.start) < 7:
            break
        gaps.append(estimate(s, bb, cc, method).gap)
    return gaps


def robust_sigma(gaps: list[float]) -> float | None:
    if len(gaps) < MIN_PAIRS:
        return None
    g = np.asarray(gaps)
    sigma = MAD_SCALE * float(np.median(np.abs(g - np.median(g))))
    return sigma if sigma > 0 else None


def subperiods(c: Window) -> list[Window]:
    weeks = c.days // 7
    if weeks >= MIN_WEEKS:
        out = [Window(c.start + 7 * i, c.start + 7 * i + 6) for i in range(weeks)]
        out[-1] = Window(out[-1].start, c.end)
        return out
    return [Window(d, d) for d in range(c.start, c.end + 1)]


def sub_expected(s: Series, b: Window, c: Window, est: Estimate, subs: list[Window]) -> list[float]:
    """est.expected spread over C's sub-periods by last year's shape (or the weekday profile)."""
    if est.method == LAST_YEAR:
        shares = [s.total(w.shift(YEAR)) for w in subs]
    else:
        means = s.weekday_means(c.start)
        shares = [s.weekday_weight(w, means) for w in subs]
    total = sum(shares)
    if total <= 0:
        shares, total = [float(w.days) for w in subs], float(c.days)
    return [est.expected * x / total for x in shares]


def consistency(sub_gaps: list[float], total_gap: float) -> tuple[int, int]:
    """(sub-periods whose gap has the total gap's sign, all sub-periods)."""
    if total_gap == 0 or not sub_gaps:
        return 0, len(sub_gaps)
    sign = np.sign(total_gap)
    return int(sum(1 for g in sub_gaps if np.sign(g) == sign)), len(sub_gaps)


# --- candidates and labels ----------------------------------------------------------------------------------------


@dataclass
class Candidate:
    name: str  # "customer = Acorn Supply", "price", ...
    contribution: float
    share: float  # clip(g / G, 0, 1)
    z: float | None
    same: int  # sub-periods with the total gap's sign
    subs: int
    label: str = "low"
    score: float = 0.0

    @property
    def consistency(self) -> float:
        return self.same / self.subs if self.subs else 0.0


@dataclass
class Total:
    estimate: Estimate
    sigma: float | None
    pairs: int
    sub_gaps: list[float] = field(default_factory=list)

    @property
    def gap(self) -> float:
        return self.estimate.gap

    @property
    def z(self) -> float | None:
        return self.gap / self.sigma if self.sigma else None

    @property
    def significant(self) -> bool:
        return self.z is not None and abs(self.z) >= CUTOFFS.z_total


def total_measures(s: Series, b: Window, c: Window) -> Total:
    est = estimate(s, b, c)
    gaps = backtest(s, b, c, est.method)
    subs = subperiods(c)
    exp = sub_expected(s, b, c, est, subs)
    return Total(est, robust_sigma(gaps), len(gaps), [s.total(w) - e for w, e in zip(subs, exp, strict=True)])


def label(z_total: float | None, share: float, z: float | None, cons: float, cut: Cutoffs | None = None) -> str:
    cut = cut or CUTOFFS
    if z_total is None or z is None or abs(z_total) < cut.z_total:
        return "low"
    if share >= cut.high_share and abs(z) >= cut.high_z and cons >= cut.high_consistency:
        return "high"
    if share >= cut.medium_share and abs(z) >= cut.medium_z and cons >= cut.medium_consistency:
        return "medium"
    return "low"


def finish(cands: list[Candidate], total: Total, cut: Cutoffs | None = None) -> list[Candidate]:
    for cand in cands:
        cand.label = label(total.z, cand.share, cand.z, cand.consistency, cut)
        cand.score = cand.share * min(1.0, abs(cand.z) / 3.0 if cand.z is not None else 0.0) * cand.consistency
    return cands


def share_of(g: float, total_gap: float) -> float:
    return float(np.clip(g / total_gap, 0.0, 1.0)) if total_gap else 0.0


def dimension_candidates(groups: dict[str, Series], dim: str, b: Window, c: Window, total: Total) -> list[Candidate]:
    """One candidate per value of a dimension, each measured on its own series."""
    out = []
    subs = subperiods(c)
    for value, s in groups.items():
        est = estimate(s, b, c)
        sigma = robust_sigma(backtest(s, b, c, est.method))
        exp = sub_expected(s, b, c, est, subs)
        same, k = consistency([s.total(w) - e for w, e in zip(subs, exp, strict=True)], total.gap)
        g = est.gap
        out.append(Candidate(f"{dim} = {value}", g, share_of(g, total.gap), g / sigma if sigma else None, same, k))
    return out


# --- price, volume, mix ---------------------------------------------------------------------------------------------


def _pvm(
    units: list[Series],
    revenue: list[Series],
    b: Window,
    c: Window,
    ratio: float,
    w: float = 1.0,
    cw: Window | None = None,
) -> tuple[float, float, float]:
    """(price, volume, mix) for current window cw (default c), base quantities = ratio x w x B's."""
    cw = cw or c
    q_b = np.array([u.total(b) for u in units])
    r_b = np.array([r.total(b) for r in revenue])
    q_c = np.array([u.total(cw) for u in units])
    r_c = np.array([r.total(cw) for r in revenue])
    with np.errstate(divide="ignore", invalid="ignore"):
        p_c = np.where(q_c > 0, r_c / q_c, 0.0)
        p_b = np.where(q_b > 0, r_b / q_b, p_c)
    q_tilde = ratio * w * q_b
    big_q = q_tilde.sum()
    p_bar = float((p_b * q_tilde).sum() / big_q) if big_q > 0 else 0.0
    price = float(((p_c - p_b) * q_c).sum())
    volume = float((q_c.sum() - big_q) * p_bar)
    mix = float((p_b * q_c).sum() - p_bar * q_c.sum())
    return price, volume, mix


def pvm_candidates(
    units: list[Series], revenue: list[Series], total_rev: Series, b: Window, c: Window, total: Total
) -> list[Candidate]:
    ratio = total.estimate.ratio
    parts = _pvm(units, revenue, b, c, ratio)
    # noise: the same decomposition on the backtest pairs, each with its own revenue ratio
    back: list[tuple[float, float, float]] = []
    for k in range(1, MAX_PAIRS + 1):
        bb, cc = b.shift(k * c.days), c.shift(k * c.days)
        if bb.start < 0 or cc.start < 0:
            break
        if total.estimate.method == LAST_YEAR and (bb.start - YEAR < 0 or cc.start - YEAR < 0):
            break
        if total.estimate.method == WEEKDAY and min(bb.start, cc.start) < 7:
            break
        back.append(_pvm(units, revenue, bb, cc, estimate(total_rev, bb, cc, total.estimate.method).ratio))
    subs = subperiods(c)
    exp = sub_expected(total_rev, b, c, total.estimate, subs)
    weights = [e / total.estimate.expected if total.estimate.expected else 1 / len(subs) for e in exp]
    sub_parts = [_pvm(units, revenue, b, c, ratio, wj, wk) for wj, wk in zip(weights, subs, strict=True)]
    out = []
    for i, name in enumerate(PVM):
        sigma = robust_sigma([p[i] for p in back])
        same, k = consistency([p[i] for p in sub_parts], total.gap)
        g = parts[i]
        out.append(Candidate(name, g, share_of(g, total.gap), g / sigma if sigma else None, same, k))
    return out


def top(cands: list[Candidate], n: int = 15) -> tuple[list[Candidate], float, int]:
    """The n candidates with the largest absolute contribution, the rest's sum, and how many the rest holds."""
    ranked = sorted(cands, key=lambda x: (-abs(x.contribution), x.name))
    shown = ranked[:n]
    return shown, float(sum(x.contribution for x in ranked[n:])), len(ranked) - len(shown)


def order(cands: list[Candidate]) -> list[Candidate]:
    """Within a label, by score; labels high, medium, low."""
    rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(cands, key=lambda x: (rank[x.label], -x.score, -abs(x.contribution), x.name))
