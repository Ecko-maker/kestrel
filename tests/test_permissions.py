"""Permission tiers v2, stage 1: capabilities and the one function that derives approval from them.
With nothing read yet, every tool must behave exactly as its old tier did. No network, no keys."""

import hashlib
import json
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from kestrel import tools
from kestrel.mcp_client import MCPManager, load_config
from kestrel.permissions import ALL, CAPABILITIES, NETWORK_ONLY, base_level, check_capabilities, required_approval
from kestrel.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "tests" / "frozen"

BUILT_IN = {  # docs/tiers-v2-design.md, section 2
    "get_current_time": (set(), "safe"),
    "calculator": (set(), "safe"),
    "list_files": ({"reads_local"}, "safe"),
    "read_file": ({"reads_local", "reads_untrusted"}, "safe"),
    "web_search": ({"network_egress", "reads_untrusted"}, "safe"),
    "write_file": ({"writes_local"}, "confirm"),
    "append_to_file": ({"writes_local"}, "confirm"),
    "create_note": ({"writes_local"}, "confirm"),
    "send_message": ({"sends"}, "confirm"),
    "delete_file": ({"deletes_local"}, "forbidden"),
}


def test_every_built_in_tool_declares_the_capabilities_of_the_design_table():
    assert set(tools.registry.tools) == set(BUILT_IN)
    for name, (caps, level) in BUILT_IN.items():
        t = tools.registry.tools[name]
        assert (set(t.capabilities), t.risk) == (caps, level), name
        assert required_approval(t.capabilities, t.external).level == level, name
        assert t.untrusted_output == ("reads_untrusted" in caps), name


def test_with_nothing_read_every_built_in_tool_keeps_its_tier_from_5b1d900():
    old = json.loads((FROZEN / "main_registry_5b1d900.json").read_text(encoding="utf-8"))["tools"]
    for name, t in tools.registry.tools.items():
        assert required_approval(t.capabilities, t.external).level == old[name]["risk"], name


@pytest.mark.parametrize(
    ("caps", "external", "level"),
    [
        (set(), False, "safe"),
        ({"reads_local", "reads_untrusted"}, False, "safe"),
        ({"network_egress"}, False, "safe"),  # stage 2 escalates it after taint
        ({"writes_local"}, False, "confirm"),
        ({"sends"}, False, "confirm"),
        ({"sends", "network_egress"}, False, "confirm"),
        ({"deletes_local"}, False, "forbidden"),
        ({"deletes_local", "writes_local"}, False, "forbidden"),
        (set(CAPABILITIES), True, "confirm"),  # an unconfigured MCP tool "might delete": it asks, as before
        ({"deletes_local"}, True, "confirm"),
        ({"network_egress", "reads_untrusted"}, True, "safe"),
    ],
)
def test_the_approval_table(caps, external, level):
    assert base_level(frozenset(caps), external) == level
    assert required_approval(frozenset(caps), external).level == level


def test_unknown_capabilities_and_mixed_declarations_are_refused():
    with pytest.raises(ValueError, match="unknown capabilities"):
        check_capabilities(["reads_local", "telepathy"])
    reg = ToolRegistry()
    with pytest.raises(ValueError, match="capabilities or risk, not both"):
        reg.register(lambda: None, capabilities=(), risk="safe")
    with pytest.raises(ValueError, match="unknown capabilities"):
        reg.register(lambda: None, capabilities={"root"})


def test_risk_shorthand_gives_the_smallest_set_with_that_tier():
    reg = ToolRegistry()

    @reg.register(risk="confirm")
    def save(text: str) -> str:
        """Save."""
        return text

    assert reg.tools["save"].capabilities == {"writes_local"} and reg.tools["save"].risk == "confirm"


# --- MCP: default everything, config can only narrow, annotations never lower ------------------


def server() -> MCPServer:
    s = MCPServer("test")

    @s.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
    def lookup(topic: str) -> str:
        """Look something up. (Claims to be read-only.)"""
        return f"about {topic}"

    @s.tool()
    def fetch(url: str) -> str:
        """Fetch a page."""
        return f"page {url}"

    @s.tool()
    def clock() -> str:
        """The time."""
        return "noon"

    return s


@pytest.fixture
def manager():
    m = MCPManager({"srv": server()}, on_problem=lambda s, msg: None)
    m.start(timeout=30)
    yield m
    m.close()


def test_mcp_tools_default_to_every_capability_and_annotations_never_lower_it(manager):
    reg = ToolRegistry()
    manager.register_tools(reg)
    lookup = reg.tools["srv__lookup"]
    assert lookup.annotations["readOnlyHint"] is True  # the server claims read-only...
    assert lookup.capabilities == ALL and lookup.risk == "confirm"  # ...and can still do anything


def test_config_narrows_mcp_tools_and_safe_tools_means_network_egress_only(manager):
    reg = ToolRegistry()
    manager.register_tools(reg, safe_tools={"srv__fetch"}, capabilities={"srv__clock": frozenset()})
    fetch, clock = reg.tools["srv__fetch"], reg.tools["srv__clock"]
    assert fetch.capabilities == NETWORK_ONLY | {"reads_untrusted"} and fetch.risk == "safe"
    assert clock.capabilities == {"reads_untrusted"} and clock.risk == "safe"  # output is always untrusted
    assert reg.tools["srv__lookup"].capabilities == ALL  # not named: everything


def test_a_config_cannot_widen_or_touch_built_in_tools(tmp_path, manager):
    def config(data):
        path = tmp_path / "kestrel.mcp.json"
        path.write_text(json.dumps({"mcpServers": {}, **data}), encoding="utf-8")
        return load_config(path)

    with pytest.raises(ValueError, match="unknown capabilities"):
        config({"capabilities": {"srv__fetch": ["network_egress", "admin"]}})
    with pytest.raises(ValueError, match="in both safe_tools and capabilities"):
        config({"safe_tools": ["srv__fetch"], "capabilities": {"srv__fetch": []}})
    with pytest.raises(ValueError, match="must be a list"):
        config({"capabilities": {"srv__fetch": "network_egress"}})

    # naming a built-in tool changes nothing: the config only reaches MCP tools
    cfg = config({"capabilities": {"read_file": [], "delete_file": []}})
    reg = ToolRegistry()
    reg.tools.update(tools.registry.tools)
    manager.register_tools(reg, cfg.safe_tools, cfg.capabilities)
    assert reg.tools["delete_file"].risk == "forbidden" and reg.tools["read_file"].capabilities == {
        "reads_local",
        "reads_untrusted",
    }
    # an MCP tool can't get more than everything, and a declared set is a subset of everything
    assert all(t.capabilities <= ALL for t in reg.tools.values())


def test_project_config_keeps_fetch_network_only():
    cfg = load_config(ROOT / "kestrel.mcp.json")
    assert cfg is not None and cfg.declared() == {"fetch__fetch": NETWORK_ONLY}


# --- frozen snapshot for this branch ----------------------------------------------------------


def snapshot() -> dict:
    return {
        n: {
            "risk": t.risk,
            "capabilities": sorted(t.capabilities),
            "external": t.external,
            "untrusted_output": t.untrusted_output,
            "schema_sha": hashlib.sha256(json.dumps(t.schema, sort_keys=True).encode()).hexdigest()[:12],
        }
        for n, t in sorted(tools.registry.tools.items())
    }


def test_main_registry_matches_the_tiers_v2_snapshot():
    """tests/frozen/main_registry_5b1d900.json still pins names, tiers and schemas (unchanged by
    tiers v2). This snapshot adds the capabilities, so a change to them is deliberate and reviewed."""
    frozen = json.loads((FROZEN / "main_registry_tiers_v2.json").read_text(encoding="utf-8"))
    assert snapshot() == frozen["tools"]
