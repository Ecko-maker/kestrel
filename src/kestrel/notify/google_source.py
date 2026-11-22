"""The notifier's own read-only Google Calendar reader (docs/notifications-design.md section 6; stage 2).

A second workspace-mcp process, separate from the agent's (`--tools google`):
- started with `--tools calendar --read-only` and every calendar tool but `get_events` disabled, so its sign-in asks
  only for `calendar.readonly` (plus the sign-in scopes) and it lists exactly one tool. Anything else listed, and
  the notifier refuses to use the server;
- its own token folder, KESTREL_NOTIFY_GOOGLE_CREDENTIALS_DIR (default %LOCALAPPDATA%\\kestrel\\google-notifier).
  Refused if it is the agent's folder (KESTREL_GOOGLE_CREDENTIALS_DIR or its default), inside it, or holding it,
  and refused inside the project or the workspace. A token in it with any scope beyond calendar.readonly and the
  sign-in scopes is refused too;
- called by code, never by a model: the tool name is a constant (any other raises) and the arguments are fixed
  (primary calendar, now to now + 24 h, expanded instances, detailed, 50 a page).

Nothing here is a tool and nothing reaches an Agent. The reply holds your address; it is masked
(`google_tools.mask`) before the parser sees it, and nothing here prints or logs a reply.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from kestrel.google_tools import ADDRESS, HIDDEN, PIN, SIGN_IN_SCOPES, mask, model_schema, pinned
from kestrel.notify.settings import NotifyConfigError
from kestrel.notify.store import iso

SERVER = "google-notifier"  # its log: logs/mcp-google-notifier.log (git-ignored; workspace-mcp logs the address)
TOOL = "get_events"
DISABLED = ("list_calendars", "query_freebusy", "start_google_auth")
SERVER_ARGS = ("--single-user", "--tools", "calendar", "--read-only", "--disabled-tools", *DISABLED)
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
ALLOWED_SCOPES = frozenset({CALENDAR_SCOPE, *SIGN_IN_SCOPES})
WINDOW = timedelta(hours=24)
PAGE_SIZE = 50
MAX_PAGES = 10
GOOGLE_LINES = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "USER_GOOGLE_EMAIL")


class SourceError(RuntimeError):
    """The notifier's server can't be used; the message says why, without the address or any secret."""


def _local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".kestrel") / "kestrel"


def default_credentials_dir() -> Path:
    return _local_appdata() / "google-notifier"


def agent_credentials_dir(env: Mapping[str, str]) -> Path:
    """The agent's token folder, as google_tools.load_settings resolves it."""
    return Path(env.get("KESTREL_GOOGLE_CREDENTIALS_DIR", "").strip() or _local_appdata() / "google-agent").resolve()


def _overlap(a: Path, b: Path) -> bool:
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


@dataclass
class SourceSettings:
    address: str = field(repr=False)
    command: str
    args: list[str]
    env: dict[str, str] = field(repr=False)  # holds the client secret: never printed
    credentials_dir: Path


def load_source_settings(env: Mapping[str, str], *, project: Path, workspace: Path) -> SourceSettings:
    missing = [n for n in GOOGLE_LINES if not env.get(n, "").strip()]
    if missing:
        raise NotifyConfigError(
            f"KESTREL_NOTIFY_STAGES=calendar needs Google set up: {', '.join(missing)} missing from .env "
            "(names in .env.example; setup in docs/google.md)."
        )
    address = env["USER_GOOGLE_EMAIL"].strip()
    if not ADDRESS.fullmatch(address):
        raise NotifyConfigError("USER_GOOGLE_EMAIL in .env must be the Google account's address (name@domain).")
    creds = Path(env.get("KESTREL_NOTIFY_GOOGLE_CREDENTIALS_DIR", "").strip() or default_credentials_dir()).resolve()
    if _overlap(creds, agent_credentials_dir(env)):
        raise NotifyConfigError(
            "KESTREL_NOTIFY_GOOGLE_CREDENTIALS_DIR is the agent's Google token folder (or inside it, or holds it). "
            f"The notifier needs its own read-only token: leave the line out (default {default_credentials_dir()}) "
            "or point it at another folder."
        )
    for name, folder in (("the project folder", project), ("the workspace", workspace)):
        if creds.is_relative_to(Path(folder).resolve()):
            raise NotifyConfigError(
                f"KESTREL_NOTIFY_GOOGLE_CREDENTIALS_DIR is inside {name}; Google tokens must live outside it "
                f"(default {default_credentials_dir()})."
            )
    command = env.get("KESTREL_GOOGLE_COMMAND", "").strip()
    args = list(SERVER_ARGS) if command else ["--python", "3.13", PIN, *SERVER_ARGS]
    server_env = {
        "GOOGLE_OAUTH_CLIENT_ID": env["GOOGLE_OAUTH_CLIENT_ID"].strip(),
        "GOOGLE_OAUTH_CLIENT_SECRET": env["GOOGLE_OAUTH_CLIENT_SECRET"].strip(),
        "WORKSPACE_MCP_CREDENTIALS_DIR": str(creds),
    }
    return SourceSettings(address, command or "uvx", args, server_env, creds)


def token_scopes(folder: Path) -> list[str] | None:
    """The scopes of the saved sign-in in the folder, or None if there is none. Reads only each token file's
    `scopes` field: nothing else in it is read into a variable that outlives this function."""
    found: set[str] = set()
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    for path in files:
        try:
            scopes = json.loads(path.read_text(encoding="utf-8")).get("scopes")
        except OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError:
            raise SourceError(f"a file in the notifier's token folder isn't a readable token ({path.name})") from None
        if not isinstance(scopes, list):
            raise SourceError("a token in the notifier's token folder has no scopes list")
        found |= {str(s) for s in scopes}
    return sorted(found) if files else None


def check_token(folder: Path) -> str:
    """'ok', 'missing', or raises SourceError for a token wider than calendar.readonly."""
    scopes = token_scopes(folder)
    if scopes is None:
        return "missing"
    extra = sorted(set(scopes) - ALLOWED_SCOPES)
    if extra:
        names = ", ".join(s.rsplit("/", 1)[-1] for s in extra)
        raise SourceError(
            f"the notifier's token has scopes beyond calendar.readonly ({names}); delete the folder "
            f"{folder} and sign in again with: kestrel notify signin"
        )
    if CALENDAR_SCOPE not in scopes:
        raise SourceError("the notifier's token can't read the calendar; sign in again with: kestrel notify signin")
    return "ok"


def page_args(now: datetime, page_token: str | None = None, time_min: str | None = None) -> dict[str, Any]:
    """The only arguments the notifier ever sends: fixed but for the clock and the page."""
    args: dict[str, Any] = {
        "calendar_id": "primary",
        "time_min": time_min or iso(now).replace("+00:00", "Z"),
        "time_max": iso(now + WINDOW).replace("+00:00", "Z"),
        "max_results": PAGE_SIZE,
        "detailed": True,
        "single_events": True,
    }
    if page_token:
        args["page_token"] = page_token
    return args


class CalendarSource:
    """Start the notifier's server, check it offers exactly `get_events` with the pinned schema, and read pages.
    `target` replaces the stdio process in tests (an in-process MCP server)."""

    def __init__(self, settings: SourceSettings, *, target: Any = None, log_dir: Path = Path("logs")):
        self.settings = settings
        self.target = target
        self.log_dir = log_dir
        self.manager: Any = None

    def start(self) -> None:
        from mcp import StdioServerParameters

        from kestrel.mcp_client import MCPManager

        target = self.target or StdioServerParameters(
            command=self.settings.command, args=self.settings.args, env=self.settings.env
        )
        problems: list[str] = []
        self.manager = MCPManager({SERVER: target}, on_problem=lambda _s, m: problems.append(m), log_dir=self.log_dir)
        self.manager.start()
        conn = self.manager.connections[SERVER]
        if not conn.alive:
            self.close()
            raise SourceError(f"the notifier's Google server didn't start ({mask(conn.error or '', self.address)})")
        listed = sorted(t.name for t in conn.tools)
        if listed != [TOOL]:
            self.close()
            raise SourceError(f"the notifier's Google server offers {len(listed)} tools, not just {TOOL}; refused")
        if model_schema(conn.tools[0].input_schema or {}) != model_schema(pinned()[TOOL]["input_schema"]):
            self.close()
            raise SourceError(f"{TOOL} differs from the pinned {PIN} schema; refused (re-check the pin)")

    def check(self) -> str:
        """'ok' or 'missing' (no sign-in yet); raises SourceError for a token wider than calendar.readonly."""
        return check_token(self.settings.credentials_dir)

    @property
    def address(self) -> str:
        return self.settings.address

    def call(self, tool: str, args: dict[str, Any]) -> str:
        """Blocking; run it on a worker thread. The reply comes back with the address masked."""
        if tool != TOOL:
            raise SourceError(f"the notifier may only call {TOOL}")
        if self.manager is None:
            raise SourceError("the notifier's Google server isn't started")
        full = {k: v for k, v in args.items() if k != HIDDEN} | {HIDDEN: self.address}
        try:
            return mask(self.manager.call(SERVER, TOOL, full), self.address)
        except Exception as e:
            raise SourceError(mask(f"{type(e).__name__}: {e}", self.address)[:300]) from None

    def close(self) -> None:
        manager, self.manager = self.manager, None  # safe to call twice (the watcher and the bot's shutdown)
        if manager is not None:
            manager.close()
