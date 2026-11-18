"""The evidence measures and the confidence label (src/kestrel/data/analysis.py), and the frozen cut-offs."""

from __future__ import annotations

import itertools
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from kestrel.data import analysis as an  # noqa: E402
from kestrel.data import calibration as cal  # noqa: E402
from kestrel.data import generator as gen  # noqa: E402

TUNING = Path(__file__).resolve().parents[1] / "evals" / "kestrelbench" / "rca" / "calibration" / "tuning.json"


def flat(days: int, value: float = 100.0, weekday0: int = 0) -> an.Series:
    return an.Series(np.full(days, value), weekday0)


def test_a_flat_series_has_no_gap() -> None:
    s = flat(1300)
    b, c = an.Window(1200, 1230), an.Window(1231, 1261)
    est = an.estimate(s, b, c)
    assert est.method == an.LAST_YEAR and est.gap == pytest.approx(0.0)


def test_less_than_a_year_falls_back_to_the_weekday_profile() -> None:
    s = flat(200)
    est = an.estimate(s, an.Window(100, 130), an.Window(131, 161))
    assert est.method == an.WEEKDAY and est.gap == pytest.approx(0.0)


def test_too_little_history_means_no_noise_estimate_and_every_label_low() -> None:
    rng = np.random.default_rng(1)
    s = an.Series(rng.normal(100, 5, 500), 0)
    tot = an.total_measures(s, an.Window(400, 430), an.Window(431, 461))
    assert tot.sigma is None and tot.z is None
    assert an.label(tot.z, 1.0, 10.0, 1.0) == "low"


def test_backtest_uses_up_to_26_non_overlapping_current_windows() -> None:
    s = flat(gen.DAYS)
    c = an.Window(gen.DAYS - 31, gen.DAYS - 1)
    b = c.shift(31)
    gaps = an.backtest(s, b, c, an.LAST_YEAR)
    assert len(gaps) == an.MAX_PAIRS
    currents = [c.shift(k * c.days) for k in range(1, len(gaps) + 1)]
    assert all(x.end < y.start for y, x in itertools.pairwise(currents))  # no two overlap


def test_subperiods_are_weeks_when_there_are_four() -> None:
    subs = an.subperiods(an.Window(0, 30))
    assert [w.days for w in subs] == [7, 7, 7, 10]
    assert len(an.subperiods(an.Window(0, 20))) == 21  # under 4 weeks: days


@pytest.mark.parametrize(
    ("z_total", "share", "z", "cons", "expected"),
    [
        (2.0, 0.50, 3.0, 0.75, "high"),
        (2.0, 0.49, 3.0, 0.75, "medium"),
        (2.0, 0.50, 2.9, 0.75, "medium"),
        (2.0, 0.50, 3.0, 0.74, "medium"),
        (2.0, 0.25, 2.0, 0.50, "medium"),
        (2.0, 0.24, 2.0, 0.50, "low"),
        (1.9, 1.00, 9.0, 1.00, "low"),  # no real change: every candidate is low
        (-3.0, 0.60, -4.0, 1.0, "high"),  # signs don't matter for |z|
        (None, 1.0, 9.0, 1.0, "low"),
        (3.0, 1.0, None, 1.0, "low"),
    ],
)
def test_every_measure_must_clear_its_bar(z_total, share, z, cons, expected) -> None:
    assert an.label(z_total, share, z, cons, an.START) == expected


def test_the_vectorised_label_is_the_same_rule() -> None:
    rows = cal.measure(gen.generate("stockout", 3))
    codes = cal.label_codes(cal.Arrays.of(rows), an.CUTOFFS)
    names = {2: "high", 1: "medium", 0: "low"}
    assert [names[int(c)] for c in codes] == [an.label(r.z_total, r.share, r.z, r.consistency) for r in rows]


def _dataset_series(ds: gen.Dataset) -> tuple[an.Series, an.Window, an.Window, an.Total]:
    rev = cal.revenue(ds)
    s = cal.aggregate(ds, rev)[""]
    wb = an.window(ds.day0, date(2025, 12, 1), date(2025, 12, 31))
    wc = an.window(ds.day0, date(2026, 1, 1), date(2026, 1, 31))
    return s, wb, wc, an.total_measures(s, wb, wc)


@pytest.mark.parametrize("scenario", gen.SCENARIOS)
def test_price_volume_mix_sums_exactly_to_the_gap(scenario: str) -> None:
    ds = gen.generate(scenario, 7)
    s, wb, wc, tot = _dataset_series(ds)
    units = cal.aggregate(ds, ds.columns["units"].astype(float), "product")
    revs = cal.aggregate(ds, cal.revenue(ds), "product")
    keys = sorted(units)
    cands = an.pvm_candidates([units[k] for k in keys], [revs[k] for k in keys], s, wb, wc, tot)
    assert [c.name for c in cands] == list(an.PVM)
    assert sum(c.contribution for c in cands) == pytest.approx(tot.gap, rel=1e-9, abs=1e-6)


def test_shares_clip_against_the_gap() -> None:
    assert an.share_of(-50, -100) == 0.5
    assert an.share_of(30, -100) == 0.0  # moves against the gap: explains none of it
    assert an.share_of(-300, -100) == 1.0
    assert an.share_of(5, 0) == 0.0


def test_cutoffs_are_frozen_from_the_dev_tuning() -> None:
    """CUTOFFS are what scripts/rca_calibrate.py tune chose on the dev seeds (1-1000 per scenario), and nothing else."""
    tuning = json.loads(TUNING.read_text(encoding="utf-8"))
    assert tuning["split"] == "dev" and tuning["seeds"] == [cal.DEV[0], cal.DEV[-1]]
    assert asdict(an.CUTOFFS) == tuning["cutoffs"]
    assert an.FROZEN


def test_tuning_never_reads_held_out_seeds() -> None:
    assert not set(cal.DEV) & set(cal.HELD_OUT)
    assert max(cal.HELD_OUT) < cal.TASK_START
