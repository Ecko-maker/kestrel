"""Permission tiers v2: what a tool *can do* (capabilities), and the one function that decides
whether a call may run, must be approved, or never runs.

Each tool declares its capabilities in code (a frozen Tool, like the old tiers):
    reads_local      returns workspace data
    reads_untrusted  returns text an attacker may control (files, web, MCP results)
    network_egress   its arguments leave the machine
    writes_local     creates or changes workspace files
    sends            sends a message to a person
    deletes_local    removes data

required_approval() turns capabilities plus the conversation's state (Session) into a decision.
With nothing read yet it gives exactly the old tiers: deletes -> forbidden, writes or sends ->
confirm, the rest runs at once. Design and owner decisions: docs/tiers-v2-design.md.
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
    # Why a network call needs a card it wouldn't need with nothing read: "taint:<tool>". Safe to log.
    escalated_by: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)  # the same, in words for the approval card


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


@dataclass
class Session:
    """What the approval rules know about one conversation. Lives as long as the Agent, so taint
    is never cleared within a conversation (owner decision 3), even when old turns are trimmed."""

    user_texts: list[str] = field(default_factory=list)  # the user's own messages
    taint_sources: list[str] = field(default_factory=list)  # reads_local tools that ran, in order

    def note_user(self, text: str) -> None:
        self.user_texts.append(text)

    def note_ran(self, name: str, caps: frozenset[str], args: dict, output: str) -> None:
        """Record a tool call that ran (successfully or not)."""
        if "reads_local" in caps:  # taint source = reads_local only (owner decision 2)
            self.taint_sources.append(name)

    @property
    def tainted(self) -> bool:
        return bool(self.taint_sources)

    def user_named(self, args: dict) -> bool:
        """e1: every text argument of the call (a URL, a search query) appears verbatim in one of the
        user's messages, ignoring case and runs of whitespace. A call with no text argument isn't."""
        values = [_norm(v) for v in args.values() if isinstance(v, str) and v.strip()]
        said = [_norm(t) for t in self.user_texts]
        return bool(values) and all(any(v in s for s in said) for v in values)


def required_approval(
    caps: frozenset[str], external: bool = False, session: Session | None = None, args: dict | None = None
) -> Requirement:
    """The one place approval is decided: capabilities plus the conversation so far.

    a + e1: once a reads_local tool has run in this conversation, a network_egress call needs the
    user's approval, unless the user typed its URL or query themselves. Forbidden stays forbidden.
    A tool that needs approval anyway (an unconfigured MCP tool) is marked escalated too, so a
    "yes for this session" given before can't skip the card."""
    need = Requirement(base_level(caps, external))
    if need.level == "forbidden" or "network_egress" not in caps or session is None or not session.tainted:
        return need
    if session.user_named(args or {}):
        return need
    source = session.taint_sources[0]
    need.level = "confirm"
    need.escalated_by.append(f"taint:{source}")
    need.reasons.append(f"{source} read your local data earlier in this conversation, and this call sends data out")
    return need
