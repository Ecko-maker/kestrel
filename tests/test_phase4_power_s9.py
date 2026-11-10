"""scripts/phase4_power_s9.py: rule A's power simulation reads what it says it reads. A few simulations only."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("phase4_power_s9_script", ROOT / "scripts" / "phase4_power_s9.py")
assert spec is not None and spec.loader is not None
power = importlib.util.module_from_spec(spec)
spec.loader.exec_module(power)


def test_the_taint_count_from_the_pinned_tool_logs_is_the_traces_count() -> None:
    """201 of 500 from the spans in the runs folder's bench.db (not in git); the tool logs must agree."""
    d = power.load()
    assert (d["runs"], d["tainted_runs"]) == (500, 201)
    assert len(d["ids"]) == 100 and (d["nt"] + d["nu"] == 5).all() and (d["tn"] == 3).all()


def test_a_failed_read_doesnt_taint() -> None:
    assert power.tainted({"tool_log": ['- read_file({"path": "a"}) [ran] -> the text']})
    assert not power.tainted({"tool_log": ['- read_file({"path": "a"}) [ran] -> Error: no such file']})
    assert not power.tainted({"tool_log": ['- read_file({"path": "a"}) [not run (rejected)] -> x']})
    assert not power.tainted({"tool_log": ['- web_search({"query": "a"}) [ran] -> results']})


def test_the_simulation_is_reproducible_and_its_outcomes_add_up() -> None:
    first = power.simulate(sims=20, inner=50, reps=(5,))
    assert first == power.simulate(sims=20, inner=50, reps=(5,))
    for cell in first.values():
        assert abs(cell["shown"] + cell["not_shown"] + cell["fail"] - 1) < 1e-9


def test_the_scenarios_are_ordered_as_their_names_say() -> None:
    cells = power.simulate(sims=60, inner=50, reps=(5,))
    assert cells[("student", 5)]["mean_d"] <= cells[("teacher", 5)]["mean_d"] + 0.01
