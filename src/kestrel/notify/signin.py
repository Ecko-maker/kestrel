"""`kestrel notify signin`: the notifier's one-time Google sign-in, at the laptop (stage 2).

Starts the notifier's own read-only server (google_source.py) with its own token folder and reads the next 24 hours
once. Without a saved sign-in, the server opens Google's consent screen in this laptop's browser; you allow it and
press Enter here. It prints only counts (events read, kept, skipped per rule), the token's scope names and the
folder: never an event title, your address or any part of a token.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from kestrel.notify.calendar import Page, SignInNeeded, Unreadable, parse_reply, skip_reason
from kestrel.notify.google_source import (
    MAX_PAGES,
    TOOL,
    CalendarSource,
    SourceError,
    check_token,
    load_source_settings,
    page_args,
    token_scopes,
)
from kestrel.notify.settings import DEFAULT_CONFIG, NotifyConfigError, load_trusted

CONSENT = (
    "A browser window should have opened with Google's consent screen (on this laptop). It must ask only to view "
    "your calendars (and your basic profile): if it asks for anything more, close it and stop here. Allow it, then "
    "come back and press Enter."
)


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


def signin(
    env: os._Environ[str] | dict[str, str] | None = None,
    *,
    print_line: Callable[[str], None] = print,
    ask: Callable[[str], str] = input,
    source: CalendarSource | None = None,
) -> int:
    env = os.environ if env is None else env
    try:
        settings = load_source_settings(
            env, project=Path.cwd(), workspace=Path(env.get("KESTREL_WORKSPACE") or "workspace")
        )
        trusted = load_trusted(Path((env.get("KESTREL_NOTIFY_CONFIG") or "").strip() or DEFAULT_CONFIG))
        state = check_token(settings.credentials_dir)
    except (NotifyConfigError, SourceError) as e:
        print_line(str(e))
        return 2
    print_line(f"Notifier token folder: {settings.credentials_dir} (saved sign-in: {'yes' if state == 'ok' else 'no'})")
    source = source or CalendarSource(settings)
    try:
        source.start()
        for attempt in range(3):
            try:
                events, bad = read_all(source)
                break
            except SignInNeeded:
                if attempt == 2:
                    print_line("Still not signed in after 3 tries. Close the old sign-in tabs and run this again.")
                    return 1
                ask(CONSENT)
        else:  # pragma: no cover - the loop always breaks or returns
            return 1
    except (SourceError, Unreadable) as e:
        print_line(f"Couldn't read the calendar: {e}")
        return 1
    finally:
        source.close()
    try:
        check_token(settings.credentials_dir)
    except SourceError as e:
        print_line(str(e))
        return 1
    scopes = sorted(s.rsplit("/", 1)[-1] for s in token_scopes(settings.credentials_dir) or [])
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
