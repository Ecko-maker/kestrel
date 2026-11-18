"""Synthetic order data with a planted cause, for the `rca` split and the offline calibration (docs/data-rca-design.md
3.2). Deterministic from (scenario, seed): numpy `default_rng(seed)`; nothing generated is committed, a test pins
each task's dataset and `truth.json` hashes.

Shape: 3.5 years of daily orders ending on the last day of January (owner, 2026-11-17: enough history for 26
non-overlapping noise pairs), columns `date, customer, region, channel, product, units, price, revenue`; 40
customers, 4 regions, 3 channels, 12 products; about 170k rows. Every dataset has:
- seasonality: a weekday profile, a yearly cycle with a December peak and a January trough, a slow trend;
- noise: negative-binomial units (gamma-Poisson, shape 20), so counts are overdispersed;
- a red herring: one small product drops 30-50% for one week inside January, then recovers (as built: products
  only; every region is too large for a one-week dip to stay under 10% of the gap);
- the seasonal trap: the question compares January with December, so most of the raw drop is seasonal.

Scenarios (the planted cause, in the current period C = January):
- lost-customer: one of the top 5 customers stops ordering from a day in C's first week;
- price-change: one of the top 4 products' price rises 10-20%, units fall with elasticity 1.5-2.5;
- stockout: one of the top 3 (product, region) pairs sells nothing for 7-14 days of C, then demand returns;
- channel-shift: 20-35% of retail units move online at the online price; units stay flat, revenue falls;
- no-change: nothing planted.

`truth.json` holds what the checks grade against: the scenario, the cause kind, the planted entities, the
candidates that are the cause in each decomposition, the red-herring product, whether a change was planted.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

SCENARIOS = ("lost-customer", "price-change", "stockout", "channel-shift", "no-change")
KIND = {
    "lost-customer": "customer",
    "price-change": "price",
    "stockout": "stockout",
    "channel-shift": "channel-shift",
    "no-change": "none",
}
# Fictional names: no word of a region, channel or product, and no name inside another.
CUSTOMERS = (
    "Acorn Supply",
    "Bluefin Traders",
    "Cobalt Kitchens",
    "Dunmore Foods",
    "Elmstead Market",
    "Fernhill Grocers",
    "Granite Provisions",
    "Harbor Lane Deli",
    "Ironwood Cafe",
    "Juniper Pantry",
    "Kestwick Bakery",
    "Larkspur Foods",
    "Maple Row Store",
    "Nettlefield Co",
    "Oakhaven Larder",
    "Pinecrest Mart",
    "Quarry Hill Shop",
    "Rosewood Fare",
    "Saltmarsh Goods",
    "Thistle Bistro",
    "Umberly Fresh",
    "Valebrook Hall",
    "Willowmere Inn",
    "Yarrow Kitchen",
    "Zephyr Diner",
    "Amberly Commons",
    "Birchgate Hotel",
    "Cedarvale Club",
    "Driftwood Grill",
    "Emberly Tavern",
    "Foxglove Eatery",
    "Glenmoor Canteen",
    "Hollyford Lodge",
    "Ivybridge Cafe",
    "Jasper Point Deli",
    "Kingfisher Bar",
    "Lindenhurst Mess",
    "Moorland Diner",
    "Northcote Brasserie",
    "Orchard Vale Kitchen",
)
REGIONS = ("North", "South", "East", "West")
CHANNELS = ("retail", "online", "wholesale")
CHANNEL_PRICE = {"retail": 1.0, "online": 0.75, "wholesale": 0.7}
PRODUCTS = tuple(f"{c}-{n}" for c, n in zip("ABCDEFGHJKLM", range(100, 1300, 100), strict=True))
WEEKDAY = (1.05, 1.1, 1.1, 1.05, 1.0, 0.7, 0.6)  # Monday first
MONTH = (0.78, 0.86, 0.95, 0.98, 1.0, 1.0, 0.97, 0.96, 1.0, 1.03, 1.12, 1.32)  # December peak, January trough
TREND = 0.03  # a year
SHAPE = 100.0  # gamma shape of the negative binomial
DAYS = 1279  # 3.5 years
MEAN_UNITS = 60.0


@dataclass
class Dataset:
    scenario: str
    seed: int
    end: date
    day0: date
    columns: dict[str, np.ndarray]  # day index, customer, product, channel (indices) and units, price per row
    truth: dict[str, Any] = field(default_factory=dict)

    @property
    def rows(self) -> int:
        return len(self.columns["units"])

    def csv_text(self) -> str:
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["date", "customer", "region", "channel", "product", "units", "price", "revenue"])
        cols = self.columns
        dates = [(self.day0 + timedelta(days=int(d))).isoformat() for d in range(DAYS)]
        region_of = self.truth["_region_of"]
        for d, c, p, ch, u, pr in zip(
            cols["day"], cols["customer"], cols["product"], cols["channel"], cols["units"], cols["price"], strict=True
        ):
            w.writerow(
                [
                    dates[d],
                    CUSTOMERS[c],
                    REGIONS[region_of[c]],
                    CHANNELS[ch],
                    PRODUCTS[p],
                    int(u),
                    f"{pr:.2f}",
                    f"{u * pr:.2f}",
                ]
            )
        return out.getvalue()

    def public_truth(self) -> dict[str, Any]:
        return {k: v for k, v in self.truth.items() if not k.startswith("_")}

    def truth_json(self) -> str:
        return json.dumps(self.public_truth(), indent=2, sort_keys=True) + "\n"


HARMONICS = 3


def yearly_curve(day_of_year: np.ndarray) -> np.ndarray:
    """A smooth yearly cycle: the least-squares fit of 3 harmonics through the MONTH values at mid-month. Smooth, so
    a window moved by a day (a 364-day year) sees almost the same seasonality; a step per month would not."""
    anchors = np.array([15.0 + 30.44 * m for m in range(12)])

    def basis(t: np.ndarray) -> np.ndarray:
        cols = [np.ones_like(t)]
        for h in range(1, HARMONICS + 1):
            w = 2 * np.pi * h * t / 365.25
            cols += [np.cos(w), np.sin(w)]
        return np.stack(cols, axis=-1)

    coef, *_ = np.linalg.lstsq(basis(anchors), np.array(MONTH), rcond=None)
    return np.asarray(basis(day_of_year) @ coef)


def _end_date(year: int) -> date:
    return date(year, 1, 31)


def generate(scenario: str, seed: int, year: int = 2026) -> Dataset:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {SCENARIOS}")
    rng = np.random.default_rng(seed)
    end = _end_date(year)
    day0 = end - timedelta(days=DAYS - 1)
    n_c, n_p = len(CUSTOMERS), len(PRODUCTS)

    size = np.sort(rng.lognormal(0.0, 0.5, n_c))[::-1]  # customer 0 is the largest
    region_of = rng.integers(0, len(REGIONS), n_c)
    main_channel = rng.choice(len(CHANNELS), n_c, p=[0.5, 0.3, 0.2])
    popularity = rng.dirichlet(np.full(n_p, 4.0))
    base_price = np.round(rng.uniform(15.0, 80.0, n_p), 2)

    series = []  # (customer, product, channel, mean multiplier)
    for c in range(n_c):
        basket = rng.choice(n_p, size=int(rng.integers(3, 5)), replace=False, p=popularity)
        for p in basket:
            series.append((c, int(p), int(main_channel[c]), size[c] * popularity[p] * n_p / 4))
    days = np.arange(DAYS)
    dates = [day0 + timedelta(days=int(d)) for d in days]
    yearly = yearly_curve(np.array([d.timetuple().tm_yday for d in dates], dtype=float))
    season = yearly * np.array([WEEKDAY[d.weekday()] for d in dates]) * (1 + TREND) ** (days / 365.0)

    c_start = DAYS - 31  # January of the end year
    truth: dict[str, Any] = {
        "scenario": scenario,
        "seed": seed,
        "kind": KIND[scenario],
        "metric": "revenue",
        "period_a": f"{year - 1}-12",
        "period_b": f"{year}-01",
        "planted": scenario != "no-change",
        "entities": [],
        "cause_candidates": {},
        "_region_of": [int(r) for r in region_of],
    }

    mean = np.array([m for *_, m in series])[:, None] * season[None, :] * MEAN_UNITS
    price = (
        np.tile(base_price[[p for _, p, _, _ in series]][:, None], (1, DAYS))
        * np.array([CHANNEL_PRICE[CHANNELS[ch]] for _, _, ch, _ in series])[:, None]
    )
    revenue_of = (mean * price).sum(axis=1)

    def start_in_c(latest: int) -> int:
        return int(c_start + rng.integers(0, latest + 1))

    # red herring: a small product (bottom 4 by expected revenue), one week inside C, -30% to -50%
    by_product = np.zeros(n_p)
    for i, (_, p, _, _) in enumerate(series):
        by_product[p] += revenue_of[i]

    if scenario == "lost-customer":
        c = int(rng.integers(0, 5))
        d0 = start_in_c(6)
        for i, (sc, *_rest) in enumerate(series):
            if sc == c:
                mean[i, d0:] = 0.0
        truth["entities"] = [CUSTOMERS[c]]
        truth["cause_candidates"] = {"customer": [CUSTOMERS[c]]}
        truth["start"] = dates[d0].isoformat()
    elif scenario == "price-change":
        p = int(np.argsort(by_product)[::-1][rng.integers(0, 2)])
        rise, elasticity = float(rng.uniform(0.10, 0.20)), float(rng.uniform(1.5, 2.5))
        d0 = start_in_c(6)
        for i, (_, sp, _, _) in enumerate(series):
            if sp == p:
                price[i, d0:] *= 1 + rise
                mean[i, d0:] *= (1 + rise) ** (-elasticity)
        truth["entities"] = [PRODUCTS[p]]
        # not the price/volume/mix "price" component: a price rise with elastic demand shows as lost volume
        truth["cause_candidates"] = {"product": [PRODUCTS[p]]}
        truth["start"] = dates[d0].isoformat()
        truth["rise"] = round(rise, 4)
        truth["elasticity"] = round(elasticity, 4)
    elif scenario == "stockout":
        pair = np.zeros((n_p, len(REGIONS)))
        for i, (sc, sp, _, _) in enumerate(series):
            pair[sp, region_of[sc]] += revenue_of[i]
        flat = np.argsort(pair, axis=None)[::-1][:2]
        p, r = (int(x) for x in np.unravel_index(flat[rng.integers(0, 2)], pair.shape))
        length = int(rng.integers(7, 15))
        d0 = int(c_start + rng.integers(0, 31 - length + 1))
        for i, (sc, sp, _, _) in enumerate(series):
            if sp == p and region_of[sc] == r:
                mean[i, d0 : d0 + length] = 0.0
        truth["entities"] = [PRODUCTS[p], REGIONS[r]]
        truth["cause_candidates"] = {"product": [PRODUCTS[p]], "region": [REGIONS[r]]}
        truth["start"] = dates[d0].isoformat()
        truth["days"] = length
    elif scenario == "channel-shift":
        moved = float(rng.uniform(0.20, 0.35))
        d0 = start_in_c(6)
        truth["entities"] = ["retail", "online"]
        truth["cause_candidates"] = {"channel": ["retail", "online"], "price_volume_mix": ["price"]}
        truth["start"] = dates[d0].isoformat()
        truth["moved"] = round(moved, 4)
    else:
        d0 = c_start

    small = [int(p) for p in np.argsort(by_product)[:4] if PRODUCTS[int(p)] not in truth["entities"]]
    herring = small[int(rng.integers(0, len(small)))]
    week = int(c_start + rng.integers(0, 31 - 7 + 1))
    dip = float(rng.uniform(0.30, 0.50))
    for i, (_, sp, _, _) in enumerate(series):
        if sp == herring:
            mean[i, week : week + 7] *= 1 - dip
    truth["red_herring"] = {
        "dimension": "product",
        "value": PRODUCTS[herring],
        "start": dates[week].isoformat(),
        "dip": round(dip, 4),
    }

    gamma = rng.gamma(SHAPE, np.maximum(mean, 1e-12) / SHAPE)
    units = rng.poisson(gamma)
    rows_series, rows_day = np.nonzero(units)
    cols: dict[str, list[Any]] = {"day": [], "customer": [], "product": [], "channel": [], "units": [], "price": []}

    def add(si: np.ndarray, di: np.ndarray, u: np.ndarray, pr: np.ndarray, channel: np.ndarray) -> None:
        cols["day"].append(di)
        cols["customer"].append(np.array([series[s][0] for s in si], dtype=int))
        cols["product"].append(np.array([series[s][1] for s in si], dtype=int))
        cols["channel"].append(channel)
        cols["units"].append(u)
        cols["price"].append(np.round(pr, 2))

    u = units[rows_series, rows_day]
    ch = np.array([series[s][2] for s in rows_series], dtype=int)
    pr = price[rows_series, rows_day]
    if scenario == "channel-shift":
        retail = CHANNELS.index("retail")
        shift = (ch == retail) & (rows_day >= d0)
        moved_units = np.zeros_like(u)
        moved_units[shift] = rng.binomial(u[shift], truth["moved"])
        keep = u - moved_units
        stay = keep > 0
        add(rows_series[stay], rows_day[stay], keep[stay], pr[stay], ch[stay])
        go = moved_units > 0
        online = CHANNELS.index("online")
        online_price = pr[go] / CHANNEL_PRICE["retail"] * CHANNEL_PRICE["online"]
        add(rows_series[go], rows_day[go], moved_units[go], online_price, np.full(int(go.sum()), online))
    else:
        add(rows_series, rows_day, u, pr, ch)

    merged = {k: np.concatenate(v) for k, v in cols.items()}
    order = np.lexsort((merged["channel"], merged["product"], merged["customer"], merged["day"]))
    merged = {k: v[order] for k, v in merged.items()}
    return Dataset(scenario, seed, end, day0, merged, truth)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write(ds: Dataset, folder: Path, name: str) -> Path:
    """Write the CSV and truth.json into folder and build `<name>.duckdb` there (the build path, unlocked)."""
    from kestrel.data.build import build

    folder.mkdir(parents=True, exist_ok=True)
    csv_path = folder / "orders.csv"
    csv_path.write_text(ds.csv_text(), encoding="utf-8", newline="\n")
    (folder / "truth.json").write_text(ds.truth_json(), encoding="utf-8", newline="\n")
    path = build(folder, name, [csv_path], replace=True)
    csv_path.unlink()
    return path
