"""scripts/phase4_power.py reproduces docs/phase4-plan.md section 8.3 (frozen 2026-10-30) on the pinned
after-run file. The full simulation takes minutes, so this checks the model and a few table cells; every
other cell still takes its draws in order (simulate's `evaluate`), so these are the full run's numbers."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("phase4_power_script", ROOT / "scripts" / "phase4_power.py")
assert spec is not None and spec.loader is not None
power = importlib.util.module_from_spec(spec)
sys.modules["phase4_power_script"] = power
spec.loader.exec_module(power)

# (metric, scenario, fresh teacher repeats, R) -> (P(shown) at 0.95, P(shown) at 0.90), as section 8.3 prints
EXPECTED = {
    ("checks", "identical (ratio 1.00)", 0, 3): ("78%", "100%"),
    ("checks", "identical (ratio 1.00)", 0, 5): ("87%", "100%"),
    ("checks", "loses 2 tasks (~0.98)", 0, 5): ("14%", "98%"),
    ("checks", "loses 4 tasks (~0.96)", 0, 5): ("0%", "60%"),
    ("checks", "diffuse x0.95 (boundary: false-pass rate)", 0, 5): ("2%", "77%"),
    ("checks", "identical (ratio 1.00)", 3, 5): ("82%", "100%"),
    ("full", "identical (ratio 1.00)", 0, 5): ("79%", "100%"),
}


def test_the_inputs_are_the_pinned_files() -> None:
    power.check_inputs()  # exits if a hash differs from section 8's
    assert {name: expected for name, (_, expected) in power.INPUTS.items()} == {
        "after": "8948b434fdcf",
        "baseline": "bfd6bdeb6c3c",
        "run2": "29a607859aee",
    }


def test_the_fitted_model_is_section_8s() -> None:
    m = power.model("checks")
    assert (round(m["a"], 2), round(m["b"], 2)) == (1.48, 0.12)
    assert round(m["teacher"], 3) == 0.923 and round(m["posterior_mean"], 3) == 0.924 and m["with_a_fail"] == 15
    assert m["flips_observed"] == 8 and round(m["flips_predicted"], 1) == 10.5
    full = power.model("full")
    assert full["flips_observed"] == 9 and round(full["flips_predicted"], 1) == 13.0


def test_the_power_table_cells_are_section_8s() -> None:
    wanted = {(metric, (name, fresh, r)) for metric, name, fresh, r in EXPECTED}
    results = power.run_all(evaluate=lambda metric, cell: (metric, cell) in wanted)
    for (metric, name, fresh, r), (at95, at90) in EXPECTED.items():
        cell = results[metric][(name, fresh, r)]
        assert (f"{cell['power']:.0%}", f"{cell['power_at_0.90']:.0%}") == (at95, at90), (metric, name, fresh, r)
