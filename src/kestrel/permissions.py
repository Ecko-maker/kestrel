"""Permission tiers v2: what a tool *can do* (capabilities), and the one function that decides
whether a call may run, must be approved, or never runs.

Each tool declares its capabilities in code (a frozen Tool, like the old tiers):
    reads_local      returns workspace data
    reads_untrusted  returns text an attacker may control (files, web, MCP results)
    network_egress   its arguments leave the machine
    writes_local     creates or changes workspace files
    sends            sends a message to a person
    deletes_local    removes data

required_approval() turns capabilities plus the conversation's state into a decision. With nothing
read yet it gives exactly the old tiers: deletes -> forbidden, writes or sends -> confirm, the rest
runs at once. Design and owner decisions: docs/tiers-v2-design.md.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

Capability = Literal["reads_local", "reads_untrusted", "network_egress", "writes_local", "sends", "deletes_local"]
CAPABILITIES: tuple[Capability, ...] = (
    "reads_local",
    "reads_untrusted",
    "network_egress",
    "writes_local",
    "sends",
    "deletes_local",
)
ALL: frozenset[str] = frozenset(CAPABILITIES)
NONE: frozenset[str] = frozenset()
NETWORK_ONLY: frozenset[str] = frozenset({"network_egress"})  # what `safe_tools` means in kestrel.mcp.json
GATED: frozenset[str] = frozenset({"writes_local", "sends", "deletes_local"})  # always need the user

Level = Literal["safe", "confirm", "forbidden"]  # the old tier names: what a call needs right now

# Shorthand for tools registered with only `risk=` (tests, quick tools): the smallest capability set
# that gives that tier with no taint.
RISK_SHORTHAND: dict[str, frozenset[str]] = {
    "safe": NONE,
    "confirm": frozenset({"writes_local"}),
    "forbidden": frozenset({"deletes_local"}),
}


def check_capabilities(caps: Iterable[str]) -> frozenset[str]:
    """Validate capability names; unknown names are an error, never silently ignored."""
    found = frozenset(caps)
    if unknown := sorted(found - ALL):
        raise ValueError(f"unknown capabilities {unknown}; known: {list(CAPABILITIES)}")
    return found


def base_level(caps: frozenset[str], external: bool = False) -> Level:
    """The tier a tool has before anything is read in the conversation (= today's tiers).
    An external tool's deletes_local means "might delete" (the default for an unconfigured MCP
    tool), so it asks instead of being forbidden."""
    if "deletes_local" in caps and not external:
        return "forbidden"
    if caps & GATED:
        return "confirm"
    return "safe"


@dataclass
class Requirement:
    """What a call needs: run at once (safe), the user's approval (confirm), or never (forbidden)."""

    level: Level
    escalated_by: list[str] = field(default_factory=list)  # why a call above its base level needs a card


def required_approval(caps: frozenset[str], external: bool = False) -> Requirement:
    """The one place approval is decided from capabilities."""
    return Requirement(base_level(caps, external))
