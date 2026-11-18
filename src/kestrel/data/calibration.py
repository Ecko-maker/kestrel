"""Offline calibration of the confidence label (docs/data-rca-design.md 3.3): no model, no network.

The same analysis code the tools run (analysis.py), on series aggregated from the generator's rows in numpy instead
of SQL (tests/test_data_calibration.py checks the two give the same measures). Seeds:
- **dev** 1-1000 per scenario: the only seeds `tune` may read;
- **held-out** 10001-11000 per scenario: reported once, with the frozen cut-offs; never tuned on;
- **task** 20001-: the `rca` split's datasets (evals/kestrelbench/rca).

What is measured, per scenario, over the candidates of the decompositions on the planted cause's own dimensions
(truth `cause_candidates`; every decomposition for no-change):
- P(planted cause | high) >= 0.90; P(planted cause | medium) in [0.50, 0.90]; P(planted cause | low) < 0.50;
- no-change: P(any candidate high or medium) <= 0.05 (per dataset).
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from kestrel.data import analysis as an
from kestrel.data import generator as gen

DEV = range(1, 1001)
HELD_OUT = range(10001, 11001)
TASK_START = 20001
DIMENSIONS = ("customer", "region", "channel", "product")


def seeds_for(split: str) -> range:
    return {"dev": DEV, "held-out": HELD_OUT}[split]


def _names(dim: str) -> tuple[str, ...]:
    return {"customer": gen.CUSTOMERS, "region": gen.REGIONS, "channel": gen.CHANNELS, "product": gen.PRODUCTS}[dim]


def aggregate(ds: gen.Dataset, values: np.ndarray, dim: str | None = None) -> dict[str, an.Series]:
    """Daily sums of `values` per value of dim (or one series under ''), as the tools' SQL GROUP BY does."""
    cols = ds.columns
    weekday0 = ds.day0.weekday()
    if dim is None:
        arr = np.zeros(gen.DAYS)
        np.add.at(arr, cols["day"], values)
        return {"": an.Series(arr, weekday0)}
    keys = np.asarray(ds.truth["_region_of"])[cols["customer"]] if dim == "region" else cols[dim]
    names = _names(dim)
    out = {}
    for k in np.unique(keys):
        arr = np.zeros(gen.DAYS)
        mask = keys == k
        np.add.at(arr, cols["day"][mask], values[mask])
        out[names[int(k)]] = an.Series(arr, weekday0)
    return dict(sorted(out.items()))


def revenue(ds: gen.Dataset) -> np.ndarray:
    return np.round(ds.columns["units"] * ds.columns["price"], 2)


@dataclass
class Row:
    scenario: str
    seed: int
    decomposition: str
    candidate: str
    cause: bool
    z_total: float | None
    share: float
    z: float | None
    consistency: float


def measure(ds: gen.Dataset) -> list[Row]:
    """Every candidate's measures (labels are applied later, so cut-offs can be tuned without recomputing)."""
    rev = revenue(ds)
    total_s = aggregate(ds, rev)[""]
    day0 = ds.day0
    from datetime import date, timedelta

    b_start = date.fromisoformat(ds.truth["period_a"] + "-01")
    c_start = date.fromisoformat(ds.truth["period_b"] + "-01")
    wb = an.window(day0, b_start, c_start - timedelta(days=1))
    wc = an.window(day0, c_start, ds.end)
    tot = an.total_measures(total_s, wb, wc)
    causes: dict[str, list[str]] = ds.truth["cause_candidates"]
    dims = list(causes) if ds.truth["planted"] else [*DIMENSIONS, "price_volume_mix"]
    rows = []
    for dim in dims:
        if dim == "price_volume_mix":
            units = aggregate(ds, ds.columns["units"].astype(float), "product")
            revs = aggregate(ds, rev, "product")
            keys = sorted(units)
            cands = an.pvm_candidates([units[k] for k in keys], [revs[k] for k in keys], total_s, wb, wc, tot)
            names = [c.name for c in cands]
        else:
            cands = an.dimension_candidates(aggregate(ds, rev, dim), dim, wb, wc, tot)
            names = [c.name.split(" = ", 1)[1] for c in cands]
        for cand, name in zip(cands, names, strict=True):
            rows.append(
                Row(
                    ds.scenario,
                    ds.seed,
                    dim,
                    name,
                    name in causes.get(dim, []),
                    tot.z,
                    cand.share,
                    cand.z,
                    cand.consistency,
                )
            )
    return rows


def collect(scenarios: Iterable[str], seeds: Iterable[int]) -> list[Row]:
    seeds = list(seeds)
    return [r for s in scenarios for seed in seeds for r in measure(gen.generate(s, seed))]


@dataclass
class Arrays:
    """The rows as numpy arrays, so a grid of cut-offs is cheap to evaluate. A missing z is 0 (it labels low)."""

    z_total: np.ndarray
    share: np.ndarray
    z: np.ndarray
    cons: np.ndarray
    cause: np.ndarray
    nochange: np.ndarray
    dataset: np.ndarray  # index of (scenario, seed)

    @staticmethod
    def of(rows: list[Row]) -> Arrays:
        keys: dict[tuple[str, int], int] = {}
        return Arrays(
            np.array([abs(r.z_total) if r.z_total is not None else 0.0 for r in rows]),
            np.array([r.share for r in rows]),
            np.array([abs(r.z) if r.z is not None else 0.0 for r in rows]),
            np.array([r.consistency for r in rows]),
            np.array([r.cause for r in rows], dtype=bool),
            np.array([r.scenario == "no-change" for r in rows], dtype=bool),
            np.array([keys.setdefault((r.scenario, r.seed), len(keys)) for r in rows]),
        )


def label_codes(a: Arrays, cut: an.Cutoffs) -> np.ndarray:
    """2 high, 1 medium, 0 low: the same rule as analysis.label, vectorised (tests check they agree)."""
    sig = a.z_total >= cut.z_total
    high = sig & (a.share >= cut.high_share) & (a.z >= cut.high_z) & (a.cons >= cut.high_consistency)
    med = sig & (a.share >= cut.medium_share) & (a.z >= cut.medium_z) & (a.cons >= cut.medium_consistency)
    return np.where(high, 2, np.where(med, 1, 0))


def targets(rows: list[Row] | Arrays, cut: an.Cutoffs) -> dict[str, Any]:
    """The four targets with their counts (k of n)."""
    a = rows if isinstance(rows, Arrays) else Arrays.of(rows)
    codes = label_codes(a, cut)
    out: dict[str, Any] = {}
    planted = ~a.nochange
    for level, code in (("high", 2), ("medium", 1), ("low", 0)):
        sel = planted & (codes == code)
        out[level] = {"k": int(a.cause[sel].sum()), "n": int(sel.sum())}
    nc = a.nochange
    datasets = np.unique(a.dataset[nc])
    flagged = np.unique(a.dataset[nc & (codes >= 1)])
    out["no_change_any"] = {"k": len(flagged), "n": len(datasets)}
    return out


def clopper_pearson(k: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """The exact interval for large n (bench.stats.clopper_pearson overflows past n of a few thousand: it sums
    exact binomial terms). Same definition, binomial tails summed in log space; tests check the two agree."""
    import math

    if n == 0:
        return 0.0, 1.0
    tail = (1 - level) / 2

    def cdf(x: int, p: float) -> float:  # P(X <= x), X ~ Binomial(n, p)
        if x < 0:
            return 0.0
        if x >= n:
            return 1.0
        lp, lq = math.log(p), math.log1p(-p)
        logs = [
            math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq
            for i in range(x + 1)
        ]
        top = max(logs)
        return min(1.0, math.exp(top) * sum(math.exp(v - top) for v in logs))

    def solve(f: Any, target: float) -> float:  # f decreasing in p
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            if f(mid) > target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    low = 0.0 if k == 0 else solve(lambda p: cdf(k - 1, p), 1 - tail)
    high = 1.0 if k == n else solve(lambda p: cdf(k, p), tail)
    return low, high


def _rate(x: dict[str, int]) -> float | None:
    return x["k"] / x["n"] if x["n"] else None


def met(t: dict[str, Any]) -> dict[str, bool]:
    high, medium, low, nc = (_rate(t[k]) for k in ("high", "medium", "low", "no_change_any"))
    return {
        "high >= 0.90": high is not None and high >= 0.90,
        "0.50 <= medium <= 0.90": medium is not None and 0.50 <= medium <= 0.90,
        "low < 0.50": low is None or low < 0.50,
        "no-change any <= 0.05": nc is not None and nc <= 0.05,
    }


GRID = {
    "z_total": (2.0, 2.5, 3.0),
    "high_share": (0.4, 0.5, 0.6),
    "high_z": (3.0, 4.0, 5.0),
    "high_consistency": (0.5, 0.75, 1.0),
    "medium_share": (0.15, 0.25, 0.35),
    "medium_z": (2.0, 2.5, 3.0),
    "medium_consistency": (0.25, 0.5, 0.75),
}


def tune(rows: list[Row]) -> dict[str, Any]:
    """The grid point that meets the most targets, then is closest to the design's starting values (relative L1),
    then the smallest in grid order. Reads dev rows only (the caller's seeds)."""
    a = Arrays.of(rows)
    start = asdict(an.START)
    best: tuple[Any, ...] | None = None
    best_cut = an.START
    for values in itertools.product(*GRID.values()):
        cut = an.Cutoffs(*values)
        d = asdict(cut)
        if cut.medium_share > cut.high_share or cut.medium_z > cut.high_z:
            continue
        if cut.medium_consistency > cut.high_consistency:
            continue
        ok = sum(met(targets(a, cut)).values())
        distance = sum(abs(d[k] - start[k]) / start[k] for k in start)
        key = (-ok, round(distance, 9), values)
        if best is None or key < best:
            best, best_cut = key, cut
    t = targets(a, best_cut)
    return {
        "cutoffs": asdict(best_cut),
        "targets": t,
        "met": met(t),
        "start_targets": targets(a, an.START),
        "start_met": met(targets(a, an.START)),
    }
