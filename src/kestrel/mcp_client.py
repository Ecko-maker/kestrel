"""MCP client: use tools from any MCP server as if they were Kestrel's own.

Servers are listed in kestrel.mcp.json in the same "mcpServers" format Claude Desktop
and Claude Code use, plus Kestrel's own "safe_tools" allowlist:

    {
      "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}},
      "safe_tools": ["fetch__fetch"]
    }

The SDK is async; the rest of Kestrel is not (yet). So MCPManager runs one asyncio
event loop on a background thread, keeps every server connection open there, and
offers a plain blocking call() that tools can use. When the web console needs async
the Agent can move onto that loop, and nothing here has to change.

Security: an external tool is "confirm" unless its name is in safe_tools. What the
server says about itself (annotations like readOnlyHint) is shown in the approval
preview but never lowers the tier, and every result is treated as untrusted data.
"""

import asyncio
import contextlib
import json
import os
import re
import threading
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from kestrel.tools import ExternalToolError, Tool, ToolRegistry

CONFIG_FILE = Path("kestrel.mcp.json")
STARTUP_TIMEOUT = 90.0  # the first start downloads servers with uvx
CALL_TIMEOUT = 18.0  # just under the registry's 20s tool timeout, so we cancel cleanly first
NAME_SEPARATOR = "__"

# Called once per server when it fails to start or stops working: (server, message)
ProblemCallback = Callable[[str, str], None]


# --- Config -------------------------------------------------------------------


@dataclass
class ServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None


@dataclass
class MCPConfig:
    servers: list[ServerConfig]
    safe_tools: set[str]


def _expand(value: str) -> str:
    """${VAR} -> the environment variable's value, as in Claude Code's .mcp.json."""
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)


def load_config(path: str | Path = CONFIG_FILE) -> MCPConfig | None:
    """Read kestrel.mcp.json; None if there isn't one. Raises ValueError if it's malformed."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON: {e}") from None
    servers = []
    for name, spec in (data.get("mcpServers") or {}).items():
        if spec.get("disabled"):
            continue
        if not isinstance(spec.get("command"), str):
            raise ValueError(f"{path}: server '{name}' needs a \"command\"")
        servers.append(
            ServerConfig(
                name=name,
                command=_expand(spec["command"]),
                args=[_expand(str(a)) for a in spec.get("args") or []],
                env={k: _expand(str(v)) for k, v in (spec.get("env") or {}).items()},
                cwd=spec.get("cwd"),
            )
        )
    return MCPConfig(servers, set(data.get("safe_tools") or []))


def tool_name(server: str, tool: str) -> str:
    """'<server>__<tool>', limited to what model APIs accept: [A-Za-z0-9_-], max 64 chars."""

    def clean(s: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "_", s)

    return f"{clean(server)}{NAME_SEPARATOR}{clean(tool)}"[:64]


# --- Results and previews -----------------------------------------------------


def format_result(result: Any) -> str:
    """Turn an MCP CallToolResult into text for the model."""
    parts = []
    for item in getattr(result, "content", None) or []:
        kind = getattr(item, "type", "")
        if kind == "text":
            parts.append(item.text)
        elif kind == "resource":
            resource = item.resource
            parts.append(getattr(resource, "text", None) or f"[resource {resource.uri}]")
        elif kind == "resource_link":
            parts.append(f"[resource link {item.uri}]")
        else:
            parts.append(f"[{kind} content: {getattr(item, 'mimeType', 'unknown type')}]")
    text = "\n".join(parts)
    if not text and getattr(result, "structured_content", None) is not None:
        text = json.dumps(result.structured_content, ensure_ascii=False)
    if getattr(result, "is_error", False):
        raise ExternalToolError(text or "the tool reported an error")
    return text


def external_preview(server: str, tool: str, description: str, annotations: dict | None) -> Callable[[dict], str]:
    def preview(args: dict) -> str:
        lines = [f"External MCP tool '{tool}' from server '{server}'"]
        if description:
            lines.append(f"Server's description: {' '.join(description.split())[:300]}")
        if annotations:
            claims = ", ".join(f"{k}={v}" for k, v in annotations.items())
            lines.append(f"Server claims (unverified, does not change the tier): {claims}")
        lines.append("Arguments:")
        lines.append(json.dumps(args, indent=2, ensure_ascii=False))
        return "\n".join(lines)

    return preview


# --- Connections --------------------------------------------------------------


@dataclass
class Connection:
    name: str
    target: Any  # StdioServerParameters, or in tests an in-process server object
    tools: list = field(default_factory=list)
    client: Client | None = None
    alive: bool = False
    error: str | None = None
    ready: threading.Event = field(default_factory=threading.Event)
    stop: asyncio.Event | None = None

    def is_alive(self) -> bool:
        return self.alive


class MCPManager:
    def __init__(
        self,
        targets: dict[str, Any],
        on_problem: ProblemCallback | None = None,
        call_timeout: float = CALL_TIMEOUT,
        log_dir: Path = Path("logs"),
    ):
        """targets: server name -> StdioServerParameters (or an in-process server, for tests)."""
        self.connections = {name: Connection(name, target) for name, target in targets.items()}
        self.on_problem = on_problem
        self.call_timeout = call_timeout
        self.log_dir = log_dir
        self._reported: set[str] = set()
        self._closing = False
        self._tasks: list = []
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-loop", daemon=True)
        self._thread.start()

    @classmethod
    def from_config(cls, config: MCPConfig, **kwargs) -> MCPManager:
        targets = {
            s.name: StdioServerParameters(command=s.command, args=s.args, env=s.env or None, cwd=s.cwd)
            for s in config.servers
        }
        return cls(targets, **kwargs)

    # Lifecycle ---------------------------------------------------------------

    def start(self, timeout: float = STARTUP_TIMEOUT) -> None:
        """Connect to every server concurrently. Servers that fail are reported, not fatal."""
        for conn in self.connections.values():
            self._tasks.append(asyncio.run_coroutine_threadsafe(self._serve(conn), self._loop))
        for conn in self.connections.values():
            if not conn.ready.wait(timeout):
                conn.error = f"did not start within {timeout:g}s"
            if not conn.alive:
                self._report(conn, f"could not start: {conn.error}")

    async def _serve(self, conn: Connection) -> None:
        """Hold one server connection open until close(). Entering and leaving the SDK's
        context managers in this one task is required by its cancel scopes."""
        conn.stop = asyncio.Event()
        log = None
        try:
            target = conn.target
            if isinstance(target, StdioServerParameters):
                self.log_dir.mkdir(parents=True, exist_ok=True)
                # Closed in `finally` below: it must stay open for the whole connection.
                log = open(self.log_dir / f"mcp-{conn.name}.log", "a", encoding="utf-8")  # noqa: SIM115
                target = stdio_client(target, errlog=log)  # server's stderr goes to a log, not the chat
            async with Client(target) as client:
                conn.tools = (await client.list_tools()).tools
                conn.client, conn.alive = client, True
                conn.ready.set()
                await conn.stop.wait()
        except BaseException as e:  # includes ExceptionGroup from the SDK's task groups
            conn.error = _describe(e)
        finally:
            was_alive = conn.alive
            conn.alive, conn.client = False, None
            conn.ready.set()
            if log:
                log.close()
            if was_alive and not self._closing:
                self._report(conn, f"stopped working ({conn.error or 'connection closed'}); its tools are disabled")

    def close(self, timeout: float = 10.0) -> None:
        self._closing = True
        for conn in self.connections.values():
            if conn.stop is not None:
                self._loop.call_soon_threadsafe(conn.stop.set)
        for task in self._tasks:
            with contextlib.suppress(Exception):
                task.result(timeout)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout)
        if not self._thread.is_alive():
            self._loop.close()  # releases the loop's own sockets and pipes

    def _report(self, conn: Connection, message: str) -> None:
        if conn.name not in self._reported and self.on_problem:
            self._reported.add(conn.name)
            self.on_problem(conn.name, message)

    # Calls -------------------------------------------------------------------

    def call(self, server: str, tool: str, args: dict) -> str:
        """Blocking tool call, safe to use from any thread."""
        conn = self.connections[server]
        if not conn.alive or conn.client is None:
            raise ConnectionError(f"MCP server '{server}' is unavailable")
        future = asyncio.run_coroutine_threadsafe(conn.client.call_tool(tool, args), self._loop)
        try:
            result = future.result(self.call_timeout)
        except FutureTimeout:
            future.cancel()
            raise TimeoutError(f"MCP server '{server}' did not answer within {self.call_timeout:g}s") from None
        except Exception as e:  # tool-level errors come back as results; exceptions mean the link broke
            conn.alive = False
            conn.error = _describe(e)
            self._report(conn, f"stopped working ({conn.error}); its tools are disabled")
            raise ConnectionError(f"MCP server '{server}' stopped working: {conn.error}") from None
        return format_result(result)

    def register_tools(self, registry: ToolRegistry, safe_tools: frozenset[str] | set[str] = frozenset()) -> list[Tool]:
        """Add every connected server's tools to the registry as "<server>__<tool>"."""
        added = []
        for conn in self.connections.values():
            if not conn.alive:
                continue
            for t in conn.tools:
                name = tool_name(conn.name, t.name)
                annotations = t.annotations.model_dump(exclude_none=True, by_alias=True) if t.annotations else None
                description = t.description or ""

                def call(_server=conn.name, _tool=t.name, **kwargs):
                    return self.call(_server, _tool, kwargs)

                try:
                    added.append(
                        registry.register_external(
                            name,
                            call,
                            f"[MCP server '{conn.name}'] {description}".strip(),
                            t.input_schema or {},
                            risk="safe" if name in safe_tools else "confirm",  # annotations never decide this
                            server=conn.name,
                            annotations=annotations,
                            preview=external_preview(conn.name, t.name, description, annotations),
                            available=conn.is_alive,
                        )
                    )
                except ValueError as e:
                    self._report(conn, f"tool '{t.name}' skipped: {e}")
        return added


def _describe(error: BaseException) -> str:
    """Readable text for an error, unwrapping ExceptionGroups from anyio task groups."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    text = str(error).strip()
    return f"{type(error).__name__}: {text}" if text else type(error).__name__
