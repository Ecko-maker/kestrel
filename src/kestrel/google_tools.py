"""Opt-in Google tools: Gmail and Calendar, read only, through workspace-mcp (docs/google.md; the plan and its
frozen decision rule: docs/google-step-plan.md).

Off by default. They load only with `kestrel --tools google ...` or `KESTREL_TOOLS=google`, and the bench
registers stubs only with `kestrel bench run --tools google`. Without either, nothing here runs and the default
agent is byte for byte what it was (fingerprint 8a8174d8f17d, pinned by tests/test_google_tools.py).

What the model gets, whatever the server offers:
- exactly 3 tools, `google__search_gmail_messages`, `google__get_gmail_message_content` and
  `google__get_events` (the server is started with --read-only and every other read tool disabled, and Kestrel
  registers only these names);
- undeclared capabilities, so every call is confirm tier, taints the conversation and is untrusted, and session
  approval is off, so every call is a card in every front end;
- schemas without `user_google_email`: Kestrel fills it in from USER_GOOGLE_EMAIL after the gate, and replaces
  the address in every result and error with "[your Google account]", in every case and encoding (URL-encoded,
  JSON-escaped, HTML entity), after removing any Google sign-in link (its query holds the client ID and the
  address as login_hint). So the address never reaches the model, a trace, an approval card or the audit log
  (all of them see only the model's arguments and the masked result).

The server's own log (logs/mcp-google.log, git-ignored) does contain the address: workspace-mcp logs it.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kestrel.tools import ExternalToolError, Tool, ToolRegistry

SERVER = "google"
PACKAGE = "workspace-mcp"
VERSION = "2.3.0"
PIN = f"{PACKAGE}=={VERSION}"
EXPOSED = ("search_gmail_messages", "get_gmail_message_content", "get_events")
# Everything else --read-only leaves for gmail and calendar at 2.3.0 (checked with list_tools, 2026-11-17).
DISABLED = (
    "get_gmail_messages_content_batch",
    "get_gmail_thread_content",
    "get_gmail_threads_content_batch",
    "get_gmail_attachment_content",
    "list_gmail_labels",
    "list_calendars",
    "query_freebusy",
    "start_google_auth",
)
SERVER_ARGS = ("--single-user", "--tools", "gmail", "calendar", "--read-only", "--disabled-tools", *DISABLED)
# What --read-only asks for at 2.3.0 (auth/scopes.py): the two read scopes plus the sign-in scopes.
READ_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
)
SIGN_IN_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
)
HIDDEN = "user_google_email"
MASK = "[your Google account]"
SCHEMAS_FILE = Path(__file__).with_name("google_tools_schemas.json")
ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
ENV_NAMES = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "USER_GOOGLE_EMAIL")


class GoogleConfigError(ValueError):
    """A missing or bad setting; the message names the .env line to fix."""


def kestrel_name(tool: str) -> str:
    return f"{SERVER}__{tool}"


def requested(cli: list[str] | None = None, env: Mapping[str, str] = os.environ) -> bool:
    """`--tools google` on the command line, or KESTREL_TOOLS=google (the config flag)."""
    from_env = [t.strip().lower() for t in env.get("KESTREL_TOOLS", "").replace(",", " ").split()]
    return "google" in [*(cli or []), *from_env]


# --- the pinned schemas -------------------------------------------------------------------------


def pinned() -> dict[str, dict[str, Any]]:
    """The 3 tools as workspace-mcp 2.3.0 lists them (name -> description, input_schema, annotations)."""
    data = json.loads(SCHEMAS_FILE.read_text(encoding="utf-8"))
    return {t["name"]: t for t in data["tools"]}


def model_schema(input_schema: dict[str, Any]) -> dict[str, Any]:
    """The schema the model sees: the server's, without user_google_email."""
    schema = copy.deepcopy(input_schema)
    schema.get("properties", {}).pop(HIDDEN, None)
    if HIDDEN in schema.get("required", []):
        schema["required"] = [r for r in schema["required"] if r != HIDDEN]
    return schema


def describe() -> dict[str, Any]:
    """For the agent fingerprint of an opt-in run: what the Google tools are and how they're gated."""
    return {
        "server": PIN,
        "tools": [kestrel_name(t) for t in EXPOSED],
        "session_approval": False,
        "hidden_args": [HIDDEN],
        "schemas": schemas_sha(),
    }


def schemas_sha() -> str:
    """The pinned schemas' hash, of the parsed JSON (canonical), so a checkout's line endings don't change it."""
    data = json.loads(SCHEMAS_FILE.read_text(encoding="utf-8"))
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


# --- the address: filled in after the gate, masked in everything that comes back ----------------


# The server's sign-in answer (a result or an error) carries a Google sign-in link whose query holds the client
# ID, a state, a PKCE challenge and login_hint=<the address, URL-encoded>. Nothing downstream needs it: the server
# opens it in a browser itself. The whole link is replaced before the address is masked.
SIGN_IN_LINK = re.compile(r"https?://accounts\.google\.com/[^\s\"'<>()\[\]*]*", re.IGNORECASE)
LINK_MASK = "[Google sign-in link removed: finish signing in in the browser on this computer]"


@functools.lru_cache(maxsize=8)
def address_pattern(address: str) -> re.Pattern[str]:
    """The address in any case and any encoding a result can carry it in: each character other than a letter or
    digit (@ . + - _) as itself, URL-encoded (%40), double URL-encoded (%2540), JSON-escaped (\\u0040) or an HTML
    entity (&#64; &#x40;). So a link's login_hint, a mailto: or an unsubscribe link in a message are masked too."""
    parts = []
    for ch in address:
        if ch.isalnum():
            parts.append(re.escape(ch))
            continue
        code = ord(ch)
        forms = [re.escape(ch), f"%{code:02x}", f"%25{code:02x}", f"\\\\u{code:04x}", f"&#0*{code};", f"&#x0*{code:x};"]
        parts.append(f"(?:{'|'.join(forms)})")
    return re.compile("".join(parts), re.IGNORECASE)


def mask(text: str, address: str) -> str:
    """The text with any Google sign-in link removed and the address, in every form, replaced by MASK."""
    text = SIGN_IN_LINK.sub(LINK_MASK, text)
    return address_pattern(address).sub(MASK, text) if address else text


def with_address(call: Callable[..., str], address: str) -> Callable[..., str]:
    """The tool function the registry runs: the model's arguments plus the address, and the result (or the
    error) with the address replaced. The gate, the trace and the audit log only ever see the model's own
    arguments, which can't hold the address (the schema forbids extra arguments)."""

    def run(**args: Any) -> str:
        args = {k: v for k, v in args.items() if k != HIDDEN} | {HIDDEN: address}
        try:
            return mask(str(call(**args)), address)
        except ExternalToolError as e:
            raise ExternalToolError(mask(str(e), address)) from None
        except Exception as e:  # the message may quote the arguments: same type when it can be rebuilt
            try:
                masked = type(e)(mask(str(e), address))
            except TypeError:
                masked = ExternalToolError(mask(str(e), address))
            raise masked from None

    return run


# --- settings -------------------------------------------------------------------------------------


def _default_credentials_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".kestrel") / "kestrel" / "google-agent"


@dataclass
class GoogleSettings:
    address: str
    command: str
    args: list[str]
    env: dict[str, str] = field(repr=False)  # holds the client secret: never printed
    credentials_dir: Path


def load_settings(
    env: Mapping[str, str] = os.environ, project: Path | None = None, workspace: Path | None = None
) -> GoogleSettings:
    """Read the opt-in's settings, or raise GoogleConfigError naming the line to add. The client ID and
    secret are only checked for presence and passed to the server; nothing prints them."""
    missing = [n for n in ENV_NAMES if not env.get(n, "").strip()]
    if missing:
        raise GoogleConfigError(
            f"--tools google needs {', '.join(missing)} in .env (names in .env.example; setup in docs/google.md)"
        )
    address = env["USER_GOOGLE_EMAIL"].strip()
    if not ADDRESS.fullmatch(address):
        raise GoogleConfigError("USER_GOOGLE_EMAIL in .env must be the Google account's address (name@domain)")
    creds = Path(env.get("KESTREL_GOOGLE_CREDENTIALS_DIR", "").strip() or _default_credentials_dir()).resolve()
    for name, folder in (("the project folder", project or Path.cwd()), ("the workspace", workspace)):
        if folder is not None and creds.is_relative_to(Path(folder).resolve()):
            raise GoogleConfigError(
                f"KESTREL_GOOGLE_CREDENTIALS_DIR is inside {name}; Google tokens must live outside it "
                f"(default {_default_credentials_dir()})"
            )
    command = env.get("KESTREL_GOOGLE_COMMAND", "").strip()
    args = list(SERVER_ARGS) if command else ["--python", "3.13", PIN, *SERVER_ARGS]
    server_env = {
        "GOOGLE_OAUTH_CLIENT_ID": env["GOOGLE_OAUTH_CLIENT_ID"].strip(),
        "GOOGLE_OAUTH_CLIENT_SECRET": env["GOOGLE_OAUTH_CLIENT_SECRET"].strip(),
        "WORKSPACE_MCP_CREDENTIALS_DIR": str(creds),
    }
    return GoogleSettings(address, command or "uvx", args, server_env, creds)


# --- registration -------------------------------------------------------------------------------


def register(
    registry: ToolRegistry,
    live: dict[str, Any],
    call: Callable[[str, dict[str, Any]], str],
    address: str,
    available: Callable[[], bool] | None = None,
    report: Callable[[str], None] = print,
) -> list[Tool]:
    """Register the 3 exposed tools from the server's live list (`live`: name -> its MCP Tool). A tool
    whose schema differs from the pinned copy is not registered: an upgrade must be checked first."""
    from kestrel.mcp_client import external_preview

    expected = pinned()
    added = []
    for name in EXPOSED:
        tool = live.get(name)
        if tool is None:
            report(f"[google] the server has no tool '{name}'; not registered")
            continue
        schema = model_schema(tool.input_schema or {})
        if schema != model_schema(expected[name]["input_schema"]):
            report(f"[google] '{name}' differs from the pinned {PIN} schema; not registered (re-check the pin)")
            continue
        description = tool.description or ""
        annotations = tool.annotations.model_dump(exclude_none=True, by_alias=True) if tool.annotations else None

        def server_call(_name: str = name, **args: Any) -> str:
            return call(_name, args)

        added.append(
            registry.register_external(
                kestrel_name(name),
                with_address(server_call, address),
                f"[MCP server '{SERVER}'] {description}".strip(),
                schema,
                capabilities=None,  # undeclared: every capability, so confirm, taint and untrusted
                server=SERVER,
                annotations=annotations,
                preview=external_preview(SERVER, name, description, annotations),
                available=available,
                allow_session=False,  # every call is a card
            )
        )
    return added


def start(settings: GoogleSettings, registry: ToolRegistry, on_problem: Callable[[str, str], None]) -> Any:
    """Start the Google server (its own MCPManager) and register its 3 tools. Returns the manager to close."""
    from mcp import StdioServerParameters

    from kestrel.mcp_client import MCPManager

    params = StdioServerParameters(command=settings.command, args=settings.args, env=settings.env)
    manager = MCPManager({SERVER: params}, on_problem=on_problem)
    manager.start()
    conn = manager.connections[SERVER]
    if conn.alive:
        register(
            registry,
            {t.name: t for t in conn.tools},
            lambda tool, args: manager.call(SERVER, tool, args),
            settings.address,
            available=conn.is_alive,
            report=lambda message: on_problem(SERVER, message),
        )
    return manager
