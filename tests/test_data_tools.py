"""The five data tools (src/kestrel/data/tools.py) on a generated dataset, their registration (safe tier, untrusted,
tainting), and the kestrel-data MCP server (the shipped path) giving the same results."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("numpy")

from kestrel.bench.rca import RcaMeta, dataset_for
from kestrel.data import tools as dt
from kestrel.permissions import Session, required_approval
from kestrel.tools import ToolRegistry

PERIODS = {"metric": "revenue", "period_a": "2025-12", "period_b": "2026-01"}


@pytest.fixture(scope="module")
def stockout() -> tuple[Path, dict]:
    folder, truth, _, _ = dataset_for(RcaMeta("stockout", 1))
    return folder, truth


def test_every_result_starts_with_an_evidence_id_in_call_order(stockout) -> None:
    tools = dt.DataTools(stockout[0])
    first = tools.call("describe_dataset", {"dataset": "shop"})
    second = tools.call("query", {"dataset": "shop", "sql": "SELECT count(*) FROM orders"})
    assert first.startswith("[E1] data__describe_dataset\n") and second.startswith("[E2] data__query\n")
    with pytest.raises(dt.DataError):
        tools.call("query", {"dataset": "shop", "sql": "DROP TABLE orders"})
    assert tools.call("describe_dataset", {"dataset": "shop"}).startswith("[E3] ")  # a refusal gets no id


def test_describe_lists_tables_columns_and_top_values(stockout) -> None:
    text = dt.DataTools(stockout[0]).call("describe_dataset", {"dataset": "shop"})
    for part in (
        "| orders |",
        "| customer | VARCHAR | dimension | 40 |",
        "| revenue | DOUBLE | measure |",
        "Top values of orders.product",
    ):
        assert part in text
    assert len(text) <= 4_000


def test_decompose_names_the_planted_cause_with_a_computed_label(stockout) -> None:
    folder, truth = stockout
    text = dt.DataTools(folder).call("decompose_change", {"dataset": "shop", **PERIODS, "by": "product"})
    product = truth["entities"][0]
    row = next(line for line in text.splitlines() if f"product = {product} |" in line)
    assert row.split("|")[6].strip() in ("high", "medium")
    assert "Contributions sum to the total gap" in text and "never an opinion" in text


def test_contributions_and_other_sum_to_the_gap(stockout) -> None:
    text = dt.DataTools(stockout[0]).call("decompose_change", {"dataset": "shop", **PERIODS, "by": "customer"})
    gap = float(re.search(r"Gap from expected: (-?[\d,]+\.\d+)", text).group(1).replace(",", ""))
    rows = [
        float(line.split("|")[2].strip().replace(",", ""))
        for line in text.splitlines()
        if line.startswith("| customer = ")
    ]
    other = re.search(r"remainder\): contribution (-?[\d,]+\.\d+)", text)
    total = sum(rows) + (float(other.group(1).replace(",", "")) if other else 0.0)
    assert len(rows) == dt.TOP and total == pytest.approx(gap, abs=0.02)


def test_price_volume_mix_needs_revenue_and_is_one_table(stockout) -> None:
    tools = dt.DataTools(stockout[0])
    text = tools.call("decompose_change", {"dataset": "shop", **PERIODS, "by": dt.PVM_BY})
    assert [
        line.split("|")[1].strip()
        for line in text.splitlines()
        if line.split("|")[1:2] and line.split("|")[1].strip() in ("price", "volume", "mix")
    ] != []
    with pytest.raises(dt.DataError, match="explains revenue"):
        tools.call("decompose_change", {"dataset": "shop", **PERIODS, "metric": "units", "by": dt.PVM_BY})


def test_compare_and_anomalies(stockout) -> None:
    folder, truth = stockout
    tools = dt.DataTools(folder)
    compare = tools.call("compare_periods", {"dataset": "shop", **PERIODS, "by": "region"})
    assert "Seasonal expected value for period_b" in compare and "| region | period_a |" in compare
    anomalies = tools.call(
        "detect_anomalies", {"dataset": "shop", "metric": "revenue", "period": "2026-01", "by": "product"}
    )
    assert truth["entities"][0] in anomalies  # the stockout's product has days far below expected


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"period_a": "2019-01"}, "outside the data"),
        ({"period_a": "last month"}, "can't read the period"),
        ({"metric": "nope"}, "no table has a date column and a measure"),
        ({"by": "units"}, "must be a dimension column"),
        ({"dataset": "../shop"}, "dataset must be a name"),
    ],
)
def test_bad_arguments_are_explained(stockout, args, message) -> None:
    call = {"dataset": "shop", **PERIODS, "by": "product", **args}
    with pytest.raises(dt.DataError, match=message):
        dt.DataTools(stockout[0]).call("decompose_change", call)


def test_registered_safe_untrusted_and_tainting(stockout) -> None:
    registry = ToolRegistry()
    dt.register_local(registry, stockout[0])
    assert sorted(registry.tools) == sorted(dt.kestrel_name(n) for n in dt.NAMES)
    for tool in registry.tools.values():
        assert tool.risk == "safe" and tool.untrusted_output and tool.external and tool.server == "data"
        assert tool.capabilities == frozenset({"reads_local", "reads_untrusted"})
    out = registry.execute("data__describe_dataset", {"dataset": "shop"})
    assert out.startswith('<untrusted_data source="data__describe_dataset">\n[E1]')
    err = registry.execute("data__query", {"dataset": "shop", "sql": "DROP TABLE orders"})
    assert "<untrusted_data" in err and "only SELECT" in err
    session = Session()
    session.note_user("Why did revenue drop?")
    fetch_caps = frozenset({"network_egress", "reads_untrusted"})
    assert required_approval(fetch_caps, True, session, {"url": "https://x.example/"}).level == "safe"
    session.note_ran("data__describe_dataset", registry.tools["data__describe_dataset"].capabilities, {}, out)
    need = required_approval(fetch_caps, True, session, {"url": "https://x.example/"})
    assert need.level == "confirm" and need.escalated_by == ["taint:data__describe_dataset"]


def test_a_refusal_does_not_taint(stockout) -> None:
    registry = ToolRegistry()
    dt.register_local(registry, stockout[0])
    err = registry.execute("data__query", {"dataset": "nope", "sql": "SELECT 1"})
    session = Session()
    session.note_ran("data__query", registry.tools["data__query"].capabilities, {}, err)
    assert not session.tainted


def test_the_mcp_server_serves_the_same_tools(stockout) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    from kestrel.data.server import build_server

    server = build_server(stockout[0])
    listed = asyncio.run(server.list_tools())
    assert {t.name for t in listed} == set(dt.NAMES)
    for t in listed:
        a = t.annotations
        assert (a.read_only_hint, a.destructive_hint, a.open_world_hint) == (True, False, False)
    got = asyncio.run(server.call_tool("decompose_change", {"dataset": "shop", **PERIODS, "by": "product"}))
    text = got.content[0].text
    local = dt.DataTools(stockout[0]).call("decompose_change", {"dataset": "shop", **PERIODS, "by": "product"})
    assert text == local  # E1 in both: a fresh server, a fresh DataTools
    with pytest.raises(ToolError, match="only SELECT"):
        asyncio.run(server.call_tool("query", {"dataset": "shop", "sql": "DELETE FROM orders"}))


def test_through_mcpmanager_the_registry_wraps_the_server(stockout) -> None:
    """The shipped path: MCPManager to the kestrel-data server, registered by kestrel.data.tools.register."""
    from kestrel.data.server import build_server
    from kestrel.mcp_client import MCPManager

    manager = MCPManager({"data": build_server(stockout[0])})
    manager.start()
    try:
        registry = ToolRegistry()
        dt.register(registry, lambda tool, args: manager.call("data", tool, args))
        out = registry.execute("data__describe_dataset", {"dataset": "shop"})
        assert out.startswith('<untrusted_data source="data__describe_dataset">\n[E1] data__describe_dataset')
        err = registry.execute("data__query", {"dataset": "shop", "sql": "INSERT INTO orders VALUES (1)"})
        assert "only SELECT" in err and "<untrusted_data" in err
    finally:
        manager.close()
