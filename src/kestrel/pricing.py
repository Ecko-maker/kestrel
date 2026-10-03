"""Token prices: what a request actually cost, and what it would cost at list price."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PRICES = Path(__file__).with_name("prices.toml")

Prices = dict[str, dict[str, dict[str, float]]]  # provider -> model -> field -> USD per 1M tokens


def load_prices(path: str | Path | None = None) -> Prices:
    path = Path(path or os.getenv("KESTREL_PRICES") or DEFAULT_PRICES)
    with path.open("rb") as f:
        return tomllib.load(f)


@dataclass
class Cost:
    actual_usd: float | None  # None: no price known for this model
    list_usd: float | None    # None: no fair paid reference (e.g. local models)


def cost(prices: Prices, provider: str | None, model: str | None, input_tokens: int, output_tokens: int) -> Cost:
    table = prices.get(provider or "", {})
    entry = table.get(model or "") or table.get("default")
    if entry is None:
        return Cost(None, None)

    def price(kind: str) -> float | None:
        per_in, per_out = entry.get(f"{kind}_input"), entry.get(f"{kind}_output")
        if per_in is None or per_out is None:
            return None
        return (input_tokens * per_in + output_tokens * per_out) / 1_000_000

    return Cost(price("actual"), price("list"))
