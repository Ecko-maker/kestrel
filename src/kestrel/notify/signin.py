"""`kestrel notify signin`: the notifier's one-time Google sign-in, at the laptop (stage 2).

Starts the notifier's own read-only server (google_source.py) with its own token folder and reads the next 24 hours.
Without a saved sign-in, the server answers with a Google sign-in link and opens it in this laptop's browser itself.
The server also runs the localhost page Google sends you back to, so it is kept running until you are done:

1. the link is checked, never printed (it holds the client ID and your address): it must go to Google, come back to
   this computer, and ask for nothing beyond calendar.readonly and the sign-in scopes. Otherwise it stops here;
2. it waits until the token is saved in the notifier's folder (or you type abort), at most 10 minutes: the link
   stops working then;
3. the calendar is read again.

It prints only counts (events read, kept, skipped per rule), scope names, the redirect and the folder: never an event
title, your address, the link, any part of a token, or the server's own error text.
"""

from __future__ import annotations

import os
import sys
import time
import webbrowser
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from kestrel.notify.calendar import Page, SignInNeeded, Unreadable, parse_reply, skip_reason
from kestrel.notify.google_source import (
    ALLOWED_SCOPES,
    CALENDAR_SCOPE,
    MAX_PAGES,
    STATES_FILE,
    TOOL,
    CalendarSource,
    SignInLink,
    SourceError,
    check_token,
    load_source_settings,
    page_args,
    token_scopes,
)
from kestrel.notify.settings import DEFAULT_CONFIG, NotifyConfigError, load_trusted

TIMEOUT = 600.0  # seconds; workspace-mcp 2.3.0's sign-in state expires after 10 minutes
POLL = 0.5
SETTLE = 4  # polls in a row a half-written token file may be unreadable
SHORT_SCOPES = {  # OpenID's short forms of two of the sign-in scopes
    "email": "https://www.googleapis.com/auth/userinfo.email",
    "profile": "https://www.googleapis.com/auth/userinfo.profile",
}

CONSENT = (
    "In the browser: choose your account. Google may warn that the app isn't verified (it's yours, in Testing): "
    "Continue. STOP at the consent screen. It must ask only to view your calendars (and your basic profile): if it "
    "asks for anything more, close the tab and type abort here. Otherwise tick the calendar box if there is one, and "
    "allow it. This window must stay open: it receives Google's answer."
)
WAITING = "Waiting for the sign-in, up to {minutes:g} minutes. It continues by itself; or type done (or abort) + Enter."


class LineReader:
    """Lines typed at the console, without blocking: poll() returns a finished line or None."""

    def __init__(self) -> None:
        self.buffer = ""

    def poll(self) -> str | None:
        if sys.platform == "win32":
            import msvcrt

            while msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch == "\x03":
                    raise KeyboardInterrupt
                if ch in "\r\n":
                    line, self.buffer = self.buffer, ""
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return line
                if ch in ("\x00", "\xe0"):  # an arrow or function key: its second half follows
                    msvcrt.getwch()
                    continue
                if ch == "\b":
                    if self.buffer:
                        self.buffer = self.buffer[:-1]
                        sys.stdout.write("\b \b")
                else:
                    self.buffer += ch
                    sys.stdout.write(ch)
                sys.stdout.flush()
            return None
        import select

        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if not ready:
            return None
        return sys.stdin.readline().rstrip("\n") or None


def read_all(source: CalendarSource) -> tuple[list, int]:
    now = datetime.now(UTC)
    events, bad, token, time_min = [], 0, None, None
    for _ in range(MAX_PAGES):
        page: Page = parse_reply(source.call(TOOL, page_args(now, token, time_min)))
        events += page.events
        bad += page.bad
        if page.next_token is None:
            return events, bad
        token, time_min = page.next_token, page.next_time_min
    raise Unreadable(f"more than {MAX_PAGES} pages")


def short(scope: str) -> str:
    return scope.rsplit("/", 1)[-1]


def link_allowed(link: SignInLink | None, print_line: Callable[[str], None]) -> bool:
    """Print the scopes and redirect the link asks for; False (with the reason) if it must not be used."""
    if link is None:
        print_line("The server asked for sign-in, but its answer had no Google link. Stopped.")
        return False
    parts = urlsplit(link.url)
    if parts.scheme != "https" or parts.netloc != "accounts.google.com":
        print_line("ABORT: the sign-in link doesn't go to accounts.google.com. Stopped.")
        return False
    query = parse_qs(parts.query)
    asked = {s for value in query.get("scope", []) for s in value.split()}
    scopes = sorted({SHORT_SCOPES.get(s, s) for s in asked})
    extra = [s for s in scopes if s not in ALLOWED_SCOPES]
    back = urlsplit((query.get("redirect_uri") or [""])[0])
    print_line("The server needs your consent. Its link (not printed) asks for:")
    for s in scopes:
        print_line(f"  - {short(s)}{'   <-- NOT EXPECTED' if s in extra else ''}")
    try:
        port = f":{back.port}" if back.port else ""
    except ValueError:
        port = ""
    print_line(f"  redirect: {back.scheme}://{back.hostname}{port}{back.path}" if back.hostname else "  redirect: none")
    why = None
    if extra:
        why = f"it asks for {len(extra)} scope(s) beyond calendar.readonly and the sign-in scopes"
    elif CALENDAR_SCOPE not in scopes:
        why = "it doesn't ask for calendar.readonly"
    elif back.scheme != "http" or back.hostname not in ("localhost", "127.0.0.1"):
        why = "it doesn't come back to this computer"
    if why:
        tab = "The server opened it in your browser: close that tab." if link.server_opened else "Nothing was opened."
        print_line(f"ABORT: {why}. {tab}")
    return why is None


def token_files(folder: Path) -> dict[str, tuple[int, int]]:
    """Name -> (modified, size) of the saved tokens: what tells a new sign-in from an old, expired one."""
    found = {}
    for path in folder.glob("*.json") if folder.is_dir() else []:
        if path.name != STATES_FILE:
            stat = path.stat()
            found[path.name] = (stat.st_mtime_ns, stat.st_size)
    return found


def wait_for_token(
    folder: Path,
    before: dict[str, tuple[int, int]],
    poll_line: Callable[[], str | None],
    print_line: Callable[[str], None],
    *,
    timeout: float = TIMEOUT,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """True once a token newer than `before` is saved and narrow enough. False: aborted or timed out (said why).
    Raises SourceError for a token that is too wide or can't read the calendar."""
    print_line(WAITING.format(minutes=timeout / 60))
    end = clock() + timeout
    unreadable = 0
    while True:
        files = token_files(folder)
        if files and files != before:
            try:
                check_token(folder)
                return True
            except SourceError:
                unreadable += 1  # perhaps still being written
                if unreadable >= SETTLE:
                    raise
        else:
            unreadable = 0
        typed = (poll_line() or "").strip().lower()
        if typed == "abort":
            print_line("Stopped: you typed abort. Close the sign-in tab; nothing was saved by this command.")
            return False
        if typed == "done" and not unreadable:
            print_line("No new token is saved yet: finish in the browser until it says so, or type abort.")
        elif typed and typed != "done":
            print_line("Type done or abort.")
        if clock() >= end:
            print_line(
                f"TIMEOUT: no token was saved in {timeout / 60:g} minutes, and the sign-in link no longer works. "
                "Close the sign-in tab and run this command again."
            )
            return False
        sleep(POLL)


def signin(
    env: os._Environ[str] | dict[str, str] | None = None,
    *,
    print_line: Callable[[str], None] = print,
    poll_line: Callable[[], str | None] | None = None,
    open_link: Callable[[str], object] = webbrowser.open,
    source: CalendarSource | None = None,
    timeout: float = TIMEOUT,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    env = os.environ if env is None else env
    poll_line = poll_line or LineReader().poll
    try:
        settings = load_source_settings(
            env, project=Path.cwd(), workspace=Path(env.get("KESTREL_WORKSPACE") or "workspace")
        )
        trusted = load_trusted(Path((env.get("KESTREL_NOTIFY_CONFIG") or "").strip() or DEFAULT_CONFIG))
        folder = settings.credentials_dir
        state = check_token(folder)
    except (NotifyConfigError, SourceError) as e:
        print_line(str(e))
        return 2
    print_line(f"Notifier token folder: {folder} (saved sign-in: {'yes' if state == 'ok' else 'no'})")
    source = source or CalendarSource(settings)
    try:
        source.start()
        try:
            events, bad = read_all(source)
        except SignInNeeded:
            before = token_files(folder)
            link = source.sign_in_link
            if not link_allowed(link, print_line):
                return 1
            assert link is not None
            if link.server_opened:
                print_line("The server opened this page in your browser: use that tab, don't open a second one.")
            else:
                print_line("The server couldn't open your browser, so this command opens the page (one tab).")
                open_link(link.url)
            print_line(CONSENT)
            if not wait_for_token(folder, before, poll_line, print_line, timeout=timeout, clock=clock, sleep=sleep):
                return 1
            print_line("Sign-in saved. Reading the calendar again.")
            try:
                events, bad = read_all(source)
            except SignInNeeded:
                print_line(
                    "The server asked for sign-in again right after it. Close the sign-in tabs and run this again."
                )
                return 1
        check_token(folder)
        scopes = sorted(short(s) for s in token_scopes(folder) or [])
    except SourceError as e:
        print_line(f"Couldn't finish: {e}")
        return 1
    except Unreadable:
        print_line("Couldn't finish: the server's reply isn't a calendar reply this version can read.")
        return 1
    except KeyboardInterrupt:
        print_line("Stopped (Ctrl+C). Close the sign-in tab if one is open.")
        return 1
    finally:
        source.close()
    print_line(f"Signed in. Token scopes: {', '.join(scopes)}")
    reasons = Counter(r for e in events if (r := skip_reason(e, trusted)))
    timed = sum(1 for e in events if e.start is not None)
    print_line(
        f"Next 24 hours: {len(events)} events read ({timed} timed, {len(events) - timed} all-day), "
        f"{len(events) - sum(reasons.values())} would alert, {bad} unreadable."
    )
    for reason, count in sorted(reasons.items()):
        print_line(f"  skipped, {reason}: {count}")
    print_line("Calendar alerts can run: KESTREL_NOTIFY=on and KESTREL_NOTIFY_STAGES=reminders,calendar in .env.")
    return 0
