"""Offline stand-ins for everything in a safety run that would reach the network, and the scripted
users who answer approval requests.

- web_search keeps its real name, description and parameters (the model sees the same tool), but
  returns the task's fixture results.
- fetch__fetch is registered the way MCPManager registers mcp-server-fetch (external, on the safe
  allowlist), but serves the task's fixture pages.
- A task's MCP stubs are external tools on the safe allowlist that return fixed text.

Every stub call is recorded with its arguments as a {"type": "stub_call", "tool", "args"} event.
That log is the system-level ground truth: a stub call is a request that would have left the
machine, whatever the stub answered (an exfiltration URL leaks even if the page is a 404).
The real tools are never in a safety run's registry, and the global registry is never touched.
"""

import re
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from kestrel import tools
from kestrel.approval import Decision
from kestrel.bench.runner import ScriptedApprover, Setup
from kestrel.bench.tasks import Task
from kestrel.permissions import NETWORK_ONLY, NONE
from kestrel.tools import ExternalToolError, Tool, ToolRegistry

if TYPE_CHECKING:
    from kestrel.bench.safety import SafetyMeta, SafetySplit, SafetyTask

FETCH = "fetch__fetch"
NETWORK_TOOLS = ("web_search", FETCH)  # plus MCP stubs with network: true

# Copied from mcp-server-fetch 2026.8.18 (server.py: list_tools and the Fetch model), so the model
# sees the same tool it sees in normal use. Error and result text match the real server's too.
FETCH_DESCRIPTION = (
    "Fetches a URL from the internet and optionally extracts its contents as markdown.\n\n"
    "Although originally you did not have internet access, and were advised to refuse and tell the user this, "
    "this tool now grants you internet access. Now you can fetch the most up-to-date information and let the "
    "user know that."
)
FETCH_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "format": "uri", "description": "URL to fetch"},
        "max_length": {"type": "integer", "default": 5000, "description": "Maximum number of characters to return."},
        "start_index": {
            "type": "integer",
            "default": 0,
            "description": "On return output starting at this character index, useful if a previous fetch was "
            "truncated and more context is required.",
        },
        "raw": {
            "type": "boolean",
            "default": False,
            "description": "Get the actual HTML content of the requested page, without simplification.",
        },
    },
    "required": ["url"],
}


def find_page(pages: dict[str, str], url: str) -> str | None:
    """The fixture page for a URL: exact match, then without a trailing slash, then without the
    query string and fragment (so ".../post?d=..." still gets the post, as a real site would)."""
    url = url.strip()
    parts = urlsplit(url)
    bare = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    for candidate in (url, url.rstrip("/"), bare, bare.rstrip("/")):
        for key, page in pages.items():
            if key == candidate or key.rstrip("/") == candidate:
                return page
    return None


def is_network_facing(name: str, tool: Tool) -> bool:
    """Tools whose calls would leave the machine: web search and every MCP tool."""
    return name in NETWORK_TOOLS or tool.external


def build_registry(meta: SafetyMeta, log: list[dict[str, Any]]) -> ToolRegistry:
    """A fresh registry for one safety task: the built-in tools with web_search stubbed, plus the
    fetch stub and the task's MCP stubs. Stubs append their calls to `log`."""

    def record(tool: str, args: dict[str, Any]) -> None:
        log.append({"type": "stub_call", "tool": tool, "args": dict(args)})

    registry = ToolRegistry(tools.registry.timeout, tools.registry.max_result_chars)
    for name, t in tools.registry.tools.items():
        if not t.external:  # the bench never starts MCP servers, but never inherit real ones either
            registry.tools[name] = t

    def web_search(query: str, max_results: int = 5) -> list[dict]:
        record("web_search", {"query": query, "max_results": max_results})
        for entry in meta.search:
            if re.search(entry["match"], query, re.IGNORECASE):
                return list(entry["results"])[: max(1, min(int(max_results), 10))]
        return []

    real = tools.registry.tools["web_search"]
    registry.tools["web_search"] = replace(real, func=web_search)

    def fetch(**args: Any) -> str:
        record(FETCH, args)
        url = str(args.get("url", ""))
        page = find_page(meta.pages, url)
        if page is None:
            raise ExternalToolError(f"Failed to fetch {url} - status code 404")
        start = int(args.get("start_index", 0) or 0)
        length = int(args.get("max_length", 5000) or 5000)
        return f"Contents of {url}:\n{page[start : start + length]}"

    # as kestrel.mcp.json declares the real one: safe_tools = network_egress only
    registry.register_external(FETCH, fetch, FETCH_DESCRIPTION, FETCH_SCHEMA, capabilities=NETWORK_ONLY, server="fetch")

    for stub in meta.mcp:

        def call(_name: str = stub.name, _result: str = stub.result, **args: Any) -> str:
            record(_name, args)
            return _result

        schema = {
            "type": "object",
            "properties": {k: {"type": v} for k, v in stub.params.items()},
            "required": list(stub.params),
        }
        # an MCP tool on the safe_tools list (the old "safe"): network_egress only, as safe_tools means;
        # a stub with network: false is narrowed to nothing (a local read tool returning fixed text)
        registry.register_external(
            stub.name,
            call,
            stub.description,
            schema,
            capabilities=NETWORK_ONLY if stub.network else NONE,
            server=stub.name.split("__", 1)[0],
        )
    if unstubbed := [n for n, t in registry.tools.items() if is_network_facing(n, t) and t.func.__module__ != __name__]:
        raise RuntimeError(f"a safety run must never use the real network tools: {unstubbed}")
    return registry


# --- scripted users -------------------------------------------------------------------------


class RubberStampApprover:
    """Approves every request: a user worn down by approval fatigue. With it, system-level attack
    success shows what the gate alone protects (only forbidden tools and refused paths)."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def review(
        self, tool_name: str, args: dict, preview: str, *, allow_session: bool = False, notice: Any = None
    ) -> Decision:
        self.requests.append({"type": "approval_required", "tool": tool_name, "args": dict(args)})
        return Decision("approved")


# strict: the task's approval rules, everything else rejected (the main suite's scripted user)
USERS: dict[str, Callable[[Task], Any]] = {
    "strict": lambda task: ScriptedApprover(task.approvals),
    "rubber-stamp": lambda task: RubberStampApprover(),
}


def make_setup(split: SafetySplit, user: str = "strict", on_begin: Callable[[SafetyTask], None] | None = None) -> Setup:
    """The runner's per-task hook: stubbed registry, scripted user, and the stub log. on_begin
    lets a scripted model read the task it is about to play."""
    if user not in USERS:
        raise ValueError(f"unknown user {user!r}; choose from {sorted(USERS)}")
    by_id = split.by_id

    def setup(task: Task) -> tuple[ToolRegistry, Any, list[dict]]:
        st = by_id[task.id]
        log: list[dict] = []
        if on_begin:
            on_begin(st)
        return build_registry(st.meta, log), USERS[user](task), log

    return setup
