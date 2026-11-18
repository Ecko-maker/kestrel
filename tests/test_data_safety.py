"""The safety split's data vector (s1.2) and the rca split from the CLI: opt-in selection, scoped refusal, unchanged
s1.0 fingerprints, TODO markers, and an offline scripted run end to end."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("numpy")

from kestrel.bench import cli
from kestrel.bench.safety import SAFETY_VERSION, find_markers, load_safety, safety_sha
from kestrel.bench.tasks import _workspace_sha
from kestrel.bench.validate import markers_of
from kestrel.data.calibration import clopper_pearson as cp_large

SPLIT = load_safety()
DATA_TASKS = sorted(st.id for st in SPLIT.tasks if st.meta.datasets)


def args(**kw) -> argparse.Namespace:
    base = dict(
        split="safety",
        split_dir=None,
        kinds="all",
        safety_group=None,
        subset="all",
        category=None,
        task=None,
        tasks=None,
        sample=None,
        shard=None,
        skip_network=False,
        scripted=None,
        tools=[],
    )
    return argparse.Namespace(**(base | kw))


def test_ten_data_tasks_five_pairs() -> None:
    assert SAFETY_VERSION == "s1.2"
    assert len(DATA_TASKS) == 10
    kinds = {st.id: st.meta.kind for st in SPLIT.tasks if st.meta.datasets}
    assert sorted(kinds.values()).count("attack") == 5
    for st in SPLIT.tasks:
        if st.meta.datasets and st.meta.kind == "benign":
            assert st.meta.paired_with in kinds and kinds[st.meta.paired_with] == "attack"


def test_data_tasks_run_only_with_tools_data() -> None:
    without = {t.id for t in cli._pick_tasks(args())}
    with_data = {t.id for t in cli._pick_tasks(args(tools=["data"]))}
    assert not set(DATA_TASKS) & without and set(DATA_TASKS) <= with_data
    assert with_data - without == set(DATA_TASKS) and len(without) == 48


def test_unwritten_injections_refuse_only_runs_that_select_them() -> None:
    a = args(tools=["data"])
    with pytest.raises(SystemExit, match="unfilled"):
        cli._refuse_incomplete(a, cli._pick_tasks(a))
    plain = args()
    cli._refuse_incomplete(plain, cli._pick_tasks(plain))  # the 48 s1.0 tasks: complete, so no refusal


def test_validate_lists_each_attack_marker_as_todo() -> None:
    todo = [m for m in find_markers() if m.where.startswith("datasets/")]
    assert len(todo) == 6  # 5 attacks, the multistep one has two notes
    owners = {task for task in DATA_TASKS for m in markers_of(SPLIT, todo, task)}
    assert owners == {t for t in DATA_TASKS if t.startswith("atk-")}


def test_s10_task_fingerprints_are_unchanged() -> None:
    """An empty `datasets` drops out of the hash: every s1.0 task keeps the fingerprint its results recorded."""
    import hashlib

    for st in SPLIT.tasks:
        if st.meta.datasets:
            continue
        definition = {k: v for k, v in asdict(st.task).items() if k != "source"}
        meta = {k: v for k, v in asdict(st.meta).items() if k != "datasets" and not (k == "probe" and v is None)}
        blob = json.dumps([definition, meta], sort_keys=True, default=str) + _workspace_sha(SPLIT.workspace)
        assert safety_sha(st, SPLIT.workspace) == hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12], st.id


def test_canaries_live_in_the_private_column_only() -> None:
    from kestrel.bench import canaries as cn

    for st in SPLIT.tasks:
        if not st.meta.datasets:
            continue
        [text] = [t for tables in st.meta.datasets.values() for t in tables.values()]
        rows = [line.split(",") for line in text.splitlines()[1:]]
        hits = {i for row in rows for i, v in enumerate(row) if cn.find_canaries(v)}
        assert hits == {2}, st.id  # column 2 is `contact`
        assert not any(cn.find_canaries(p) for p in st.task.prompts)


def test_the_committed_fixtures_are_the_scripts_output() -> None:
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("make_safety_datasets", root / "scripts" / "make_safety_datasets.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for task_id in mod.TASKS:
        committed = (mod.ROOT / task_id / "orders.csv").read_text(encoding="utf-8")
        assert committed == mod.render(task_id), task_id
        assert mod.canary(task_id) in committed


def test_the_large_n_interval_agrees_with_the_exact_one() -> None:
    from kestrel.bench.stats import clopper_pearson

    for k, n in [(0, 29), (5, 29), (17, 24), (4, 6), (312, 400), (29, 30)]:
        exact = clopper_pearson(k, n)
        low, high = cp_large(k, n)
        assert (round(low, 4), round(high, 4)) == (exact.low, exact.high)


def test_rca_cli_runs_a_scripted_analyst_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "BENCH_DB", tmp_path / "bench.db")
    out = tmp_path / "rca.json"
    parser = argparse.ArgumentParser()
    cli.add_parser(parser.add_subparsers(dest="command"))
    ns = parser.parse_args(
        [
            "bench",
            "run",
            "--split",
            "rca",
            "--tools",
            "data",
            "--scripted",
            "right-analyst",
            "--tasks",
            "rca-lost-customer-1,rca-no-change-1",
            "--out",
            str(out),
        ]
    )
    assert cli.main(ns) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    meta = data["meta"]
    assert (meta["split"], meta["split_version"], meta["tools"], meta["judge"]) == ("rca", "r1.0", ["data"], None)
    assert meta["agent"]["data"]["tools"][0] == "data__describe_dataset"
    assert [t["status"] for t in data["tasks"]] == ["pass", "pass"]
    assert data["rca"]["checks"]["no_change"]["n"] == 1 and data["rca"]["r4"] is True


def test_rca_needs_tools_data_and_refuses_unwritten_notes_for_a_real_model() -> None:
    parser = argparse.ArgumentParser()
    cli.add_parser(parser.add_subparsers(dest="command"))
    ns = parser.parse_args(["bench", "run", "--split", "rca", "--dry-run"])
    with pytest.raises(SystemExit, match="needs --tools data"):
        cli.main(ns)
    ns = parser.parse_args(
        ["bench", "run", "--split", "rca", "--tools", "data", "--dry-run", "--tasks", "rca-price-change-6-integrity"]
    )
    with pytest.raises(SystemExit, match="unfilled"):
        cli.main(ns)
    ns = parser.parse_args(
        ["bench", "run", "--split", "rca", "--tools", "data", "--dry-run", "--tasks", "rca-price-change-1"]
    )
    assert cli.main(ns) == 0
