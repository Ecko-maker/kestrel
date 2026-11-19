"""Live check of the Google opt-in: the real Agent, ApprovalGate, a terminal approver and workspace-mcp 2.3.0
(read only), driven by a scripted model. No LLM: no Groq or Gemini call, no model quota.

    uv run --no-sync python scripts/live/google_smoke.py                 # the run (you approve each card)
    uv run --no-sync python scripts/live/google_smoke.py --leak-check    # afterwards: search logs/

The server is the installed one named by KESTREL_GOOGLE_COMMAND (docs/google.md). Without that line the script
stops rather than let uvx download workspace-mcp, unless --allow-download is given.

The run makes three calls, each through an approval card:
1. google__search_gmail_messages, query "newer_than:2d", at most 3 results;
2. google__get_gmail_message_content on the first result (skipped if there is none);
3. google__get_events from now to 7 days from now.

It prints only counts and lengths: never a subject, body, sender or event title. Your address never appears
either: the settings and the tools mask it (docs/google.md).

First sign-in: the server answers the first call with a Google sign-in link instead of results (the model and
the trace get it with the link removed). The script takes the link from the server's raw answer, never prints it
(it holds the client ID and your address), and prints the scopes and the redirect it asks for. If anything beyond
read Gmail, read Calendar, openid, email and profile is requested, it stops. workspace-mcp opens the link in your
browser itself; the script opens it only when the server says it didn't, and only when you type "yes" (a second
tab of the same link fails with "expired state"). Check the consent screen: if Google shows a box per permission,
tick both Gmail and Calendar (a partial grant is asked for again). Click Allow only if it lists exactly those,
then type "done". The script repeats the call, with a new card.

--leak-check searches every file under logs/ (traces.db, approvals.jsonl, the MCP logs) for USER_GOOGLE_EMAIL
(as written, lowercase, URL-encoded once or twice, JSON-escaped, HTML entity) and the client secret, as raw
bytes. It prints yes/no and counts per file.
logs/mcp-google.log holding your address is a known issue (KI-pending-google-server-log), reported apart.
Exit codes: 0 clean, 1 found (outside that known issue), 2 settings missing, 3 run stopped.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from telegram_leak_check import count

from kestrel import google_tools
from kestrel.tools import ExternalToolError

ROOT = Path(__file__).resolve().parents[2]
QUERY = "newer_than:2d"
PAGE_SIZE = 3
DAYS = 7
# What --read-only with --tools gmail calendar requests (docs/google-step-plan.md 1.1, item 2), short names.
EXPECTED_SCOPES = frozenset({"gmail.readonly", "calendar.readonly", "openid", "userinfo.email", "userinfo.profile"})
_SHORT = {"email": "userinfo.email", "profile": "userinfo.profile"}
SIGN_IN_HOST = "accounts.google.com"
SERVER_OPENED = "automatically opened in your browser"  # workspace-mcp 2.3.0's words when it opened the link
AUTH_URL = re.compile(r"https://accounts\.google\.com/[^\s\"'<>()\[\]*]+")
MESSAGE_ID = re.compile(r"Message ID:\s*([A-Za-z0-9_-]{6,})")
ID_FIELD = re.compile(r"\bID:\s*\S+")
KNOWN_ADDRESS_LOG = "mcp-google.log"  # KI-pending-google-server-log: workspace-mcp logs the address itself


# --- the sign-in link ---------------------------------------------------------------------------------


@dataclass
class SignInLink:
    scopes: list[str]
    extra: list[str]  # requested but not expected: any of these stops the run
    missing: list[str]
    redirect: str


def scope_name(scope: str) -> str:
    name = scope.rsplit("/", 1)[-1] if scope.startswith("https://") else scope
    return _SHORT.get(name, name)


def read_link(url: str) -> SignInLink:
    """The scopes and redirect a Google sign-in link asks for. Refuses a link to anywhere but Google's."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != SIGN_IN_HOST:
        raise ValueError(f"the sign-in link doesn't go to {SIGN_IN_HOST}")
    query = parse_qs(parts.query)
    scopes = sorted({scope_name(s) for value in query.get("scope", []) for s in value.split()})
    return SignInLink(
        scopes=scopes,
        extra=[s for s in scopes if s not in EXPECTED_SCOPES],
        missing=sorted(EXPECTED_SCOPES - set(scopes)),
        redirect=(query.get("redirect_uri") or ["(none)"])[0],
    )


class LinkCapture:
    """Keeps the newest sign-in link from the server's raw answer, before the address is masked (the link's
    login_hint may hold it). Never printed: the link holds the client ID."""

    def __init__(self) -> None:
        self.url: str | None = None
        self.server_opened = False  # the server already opened the link in a browser

    def see(self, text: str) -> None:
        if m := AUTH_URL.search(text):
            self.url = m.group(0).rstrip(".,;")
            self.server_opened = SERVER_OPENED in text

    def wrap(self, call: Callable[..., str]) -> Callable[..., str]:
        def spy(*args: Any, **kwargs: Any) -> str:
            try:
                text = call(*args, **kwargs)
            except ExternalToolError as e:
                self.see(str(e))
                raise
            self.see(text)
            return text

        return spy


def needs_sign_in(text: str) -> bool:
    """The masked answer (what the model sees) asks for sign-in: google_tools.mask replaced its link."""
    return google_tools.LINK_MASK in text or SIGN_IN_HOST in text


def sign_in(
    capture: LinkCapture,
    input_fn: Callable[[str], str] = input,
    open_fn: Callable[[str], Any] = webbrowser.open,
    print_fn: Callable[..., None] = print,
) -> bool:
    """Check the link, open it on "yes" unless the server did, and wait for "done". False stops the run."""
    if capture.url is None:
        print_fn("[sign-in] The server asked for sign-in, but its answer had no Google link. Stopping.")
        return False
    try:
        link = read_link(capture.url)
    except ValueError as e:
        print_fn(f"[sign-in] {e}. Stopping.")
        return False
    print_fn("\n[sign-in] The server needs your consent. The link (not printed: it holds the client ID) asks for:")
    for s in link.scopes:
        print_fn(f"  - {s}{'' if s in EXPECTED_SCOPES else '   <-- NOT EXPECTED'}")
    print_fn(f"  redirect: {link.redirect}")
    if link.extra:
        where = "the server opened it in your browser: close that tab" if capture.server_opened else "nothing opened"
        print_fn(f"[sign-in] ABORT: it asks for {', '.join(link.extra)}, beyond read-only. {where.capitalize()}.")
        return False
    if link.missing:
        print_fn(f"[sign-in] Note: it doesn't ask for {', '.join(link.missing)} (fewer is not a risk).")
    if capture.server_opened:
        print_fn("[sign-in] The server already opened this page in your browser: use that tab, not a second one.")
    else:
        answer = input_fn("Open Google's sign-in page in your browser now? Type yes (anything else stops): ")
        if answer.strip().lower() != "yes":
            return False
        open_fn(capture.url)
    print_fn(
        "[sign-in] In the browser: choose your account. Google may warn that the app isn't verified (it's yours, in\n"
        "  Testing): Continue. STOP at the consent screen. It must list only: read your email (Gmail), view your\n"
        "  calendars, and your name/email/profile. If it shows a box per permission, tick both Gmail and Calendar\n"
        "  (a partial grant is asked for again). Click Allow only then; otherwise close the tab and type abort."
    )
    answer = input_fn("When the page says authentication succeeded, type done (or abort): ")
    return answer.strip().lower() == "done"


# --- the scripted model ---------------------------------------------------------------------------------


@dataclass
class Reading:
    tool: str
    chars: int
    lines: int
    ids: int  # message IDs for a search, "ID:" fields otherwise
    error: bool


@dataclass
class SmokeModel:
    """Calls the three tools in turn, reading only what it needs (the first message ID). No LLM."""

    sign_in: Callable[[], bool]
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    supports_streaming = False
    model = "scripted-google-smoke"
    last_provider = "scripted"
    issued: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    readings: list[Reading] = field(default_factory=list)
    sign_ins: int = 0
    stopped: str | None = None

    def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.issued.append((tool, args))
        name = google_tools.kestrel_name(tool)
        call = {"id": f"call-{len(self.issued)}", "type": "function", "function": {"name": name}}
        call["function"]["arguments"] = json.dumps(args)
        return {"role": "assistant", "content": None, "tool_calls": [call]}

    def _say(self, text: str) -> dict[str, Any]:
        return {"role": "assistant", "content": text}

    def _stop(self, why: str) -> dict[str, Any]:
        self.stopped = why
        return self._say(f"Stopped: {why}.")

    def _week(self) -> dict[str, Any]:
        def stamp(t: datetime) -> str:
            return t.strftime("%Y-%m-%dT%H:%M:%SZ")

        return {"time_min": stamp(self.now), "time_max": stamp(self.now + timedelta(days=DAYS))}

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict[str, Any]:
        last_user = max(i for i, m in enumerate(messages) if m.get("role") == "user")
        results = [str(m.get("content") or "") for m in messages[last_user:] if m.get("role") == "tool"]
        if not results:
            return self._call("search_gmail_messages", {"query": QUERY, "page_size": PAGE_SIZE})
        text = results[-1]
        tool, args = self.issued[-1]
        if needs_sign_in(text):
            if self.sign_ins:
                return self._stop("the server asked for sign-in again right after it")
            self.sign_ins += 1
            if not self.sign_in():
                return self._stop("sign-in not completed")
            return self._call(tool, args)  # the same call again: a new card
        ids = MESSAGE_ID.findall(text) if tool == "search_gmail_messages" else ID_FIELD.findall(text)
        error = "reported a failure" in text or "did not run" in text[:400]
        self.readings.append(Reading(tool, len(text), text.count("\n") + 1, len(ids), error))
        if tool == "search_gmail_messages":
            if ids and not error:
                return self._call("get_gmail_message_content", {"message_id": ids[0]})
            return self._call("get_events", self._week())
        if tool == "get_gmail_message_content":
            return self._call("get_events", self._week())
        return self._say(f"Smoke check finished: {len(self.readings)} results read.")


class CountingApprover:
    """The terminal approver, counting the cards it showed."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.cards = 0
        self.session_offered = 0

    def review(self, tool_name: str, args: dict, preview: str, *, allow_session: bool = False, notice: Any = None):
        self.cards += 1
        self.session_offered += bool(allow_session)
        return self.inner.review(tool_name, args, preview, allow_session=allow_session, notice=notice)


def report(model: SmokeModel, approver: CountingApprover, credentials: Path, print_fn=print) -> bool:
    """Counts and lengths only. True when every call showed a card and none offered session approval."""
    print_fn("\n== Google smoke check ==")
    for r in model.readings:
        what = "message IDs" if r.tool == "search_gmail_messages" else "ID fields"
        print_fn(
            f"{r.tool}: {r.chars:,} chars, {r.lines} lines, {r.ids} {what}, error/rejected {'yes' if r.error else 'no'}"
        )
    if not any(r.tool == "get_gmail_message_content" for r in model.readings) and model.readings:
        print_fn("get_gmail_message_content: not called (no message in the last 2 days, or the search failed)")
    ok = approver.cards == len(model.issued) and approver.session_offered == 0
    print_fn(
        f"cards: {approver.cards} shown for {len(model.issued)} calls; session approval offered "
        f"{approver.session_offered} times -> {'OK' if ok else 'MISMATCH'}"
    )
    print_fn(f"sign-ins: {model.sign_ins}" + (f"; stopped: {model.stopped}" if model.stopped else ""))
    files = [p for p in credentials.rglob("*") if p.is_file()] if credentials.is_dir() else []
    print_fn(f"token folder: {credentials} exists {'yes' if credentials.is_dir() else 'no'}, {len(files)} file(s)")
    return ok and model.stopped is None


# --- leak check -------------------------------------------------------------------------------------------


def needles(address: str, secret: str) -> dict[str, list[bytes]]:
    forms = set()
    for a in (address, address.lower()):
        forms |= {a.replace("@", at) for at in ("@", "%40", "%2540", "\\u0040", "&#64;", "&#x40;")}
        forms.add(quote(a, safe=""))
    return {
        "address": sorted({f.encode("utf-8") for f in forms}),
        "secret": [secret.encode("utf-8")],
    }


def leak_check(logs: Path, address: str, secret: str, print_fn=print) -> int:
    if not logs.is_dir():
        print_fn(f"{logs} doesn't exist: nothing to search.")
        return 0
    found = known = 0
    files = sorted(p for p in logs.rglob("*") if p.is_file())
    for path in files:
        name = path.relative_to(logs).as_posix()
        try:
            hits = {k: sum(count(path, n) for n in ns) for k, ns in needles(address, secret).items()}
        except OSError as e:
            print_fn(f"{name}: could not be read ({type(e).__name__}; stop Kestrel and run again)")
            continue
        line = ", ".join(f"{k} {'YES' if v else 'no'} ({v})" for k, v in hits.items())
        if path.name == KNOWN_ADDRESS_LOG and hits["address"] and not hits["secret"]:
            known += 1
            line += "  [known: KI-pending-google-server-log]"
        elif any(hits.values()):
            found += 1
        print_fn(f"{name}: {line}")
    print_fn(
        f"\n{len(files)} file(s) searched, {found} with the address or the secret"
        + (f" (plus {known} known: the server's own log)" if known else "")
        + "."
    )
    return 1 if found else 0


# --- main -------------------------------------------------------------------------------------------------


def _settings(env: Mapping[str, str]) -> google_tools.GoogleSettings | None:
    from kestrel import tools

    try:
        return google_tools.load_settings(env, project=ROOT, workspace=tools.WORKSPACE)
    except google_tools.GoogleConfigError as e:
        print(e)
        return None


def server_source(settings: google_tools.GoogleSettings) -> str:
    """Which server runs: the installed one (KESTREL_GOOGLE_COMMAND), or uvx, which may download it."""
    if settings.command == "uvx":
        return "uvx (may download ~190 MB on first use)"
    return f"the installed command {settings.command} (KESTREL_GOOGLE_COMMAND; no download)"


def download_refused(settings: google_tools.GoogleSettings, allow_download: bool) -> str | None:
    """Without KESTREL_GOOGLE_COMMAND the server comes from uvx, which may download it: only on request."""
    if settings.command == "uvx" and not allow_download:
        return (
            "KESTREL_GOOGLE_COMMAND isn't set, so the server would come from uvx and may be downloaded "
            "(~190 MB). Add the line to .env (docs/google.md), or pass --allow-download."
        )
    return None


def run(settings: google_tools.GoogleSettings) -> int:
    from kestrel.agent import Agent
    from kestrel.approval import ApprovalGate, TerminalApprover
    from kestrel.mcp_client import MCPManager
    from kestrel.tools import registry
    from kestrel.tracing import Tracer

    capture = LinkCapture()
    MCPManager.call = capture.wrap(MCPManager.call)  # type: ignore[method-assign]  # see the raw answer
    print(f"[google] starting {google_tools.PIN} (read only) with {server_source(settings)}")
    manager = google_tools.start(
        settings, registry, lambda server, message: print(f"[google] {google_tools.mask(message, settings.address)}")
    )
    try:
        added = sorted(t.name for t in registry.tools.values() if t.server == google_tools.SERVER)
        print(f"[google] registered: {', '.join(added) or 'none'}")
        if len(added) != len(google_tools.EXPOSED):
            print("[google] expected the 3 read tools; stopping.")
            return 3
        approver = CountingApprover(TerminalApprover())
        model = SmokeModel(sign_in=lambda: sign_in(capture))
        agent = Agent(model, gate=ApprovalGate(approver), tracer=Tracer(), max_steps=10)
        result = agent.run("Google smoke check: search recent mail, read one message, list this week's events.")
        print(f"\nstop={result.stop_reason} steps={result.steps} trace={(result.trace_id or '')[:8]}")
        ok = report(model, approver, settings.credentials_dir)
    finally:
        manager.close()
    print("\nNext: uv run --no-sync python scripts/live/google_smoke.py --leak-check")
    return 0 if ok else 3


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--leak-check", action="store_true", help="search logs/ instead of running")
    parser.add_argument("--logs", type=Path, default=ROOT / "logs", help="folder to search (default logs/)")
    parser.add_argument(
        "--allow-download", action="store_true", help="without KESTREL_GOOGLE_COMMAND, let uvx fetch the server"
    )
    args = parser.parse_args(argv)
    if env is None:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")  # into this process's memory only
        env = os.environ
    settings = _settings(env)
    if settings is None:
        return 2
    if args.leak_check:
        return leak_check(args.logs, settings.address, settings.env["GOOGLE_OAUTH_CLIENT_SECRET"])
    if refused := download_refused(settings, args.allow_download):
        print(refused)
        return 2
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
