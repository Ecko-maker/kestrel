"""Stage 2: alerts before your appointments, rules only (docs/notifications-design.md section 3; docs/telegram.md,
"Calendar alerts").

No model, no Agent, no tool: the watcher reads your primary calendar's next 24 hours every 5 minutes through the
notifier's own read-only server (google_source.py) and, on the stage 1 ticker (every 20 s), sends what is due:

- **When:** at start - lead. Lead = the event's own popup reminder if it has custom ones (the largest), else
  KESTREL_NOTIFY_CAL_LEAD (default 30 min). An event whose custom reminders are an empty list (you turned them off)
  gets no alert.
- **Which:** timed events you own, accepted or marked maybe. Skipped: cancelled, declined, out-of-office and
  working-location entries, and invitations you haven't answered (anyone can put one in your calendar), unless the
  organizer is in notify.toml's `[calendar] trusted_organizers`.
- **All-day events:** one summary a day, when quiet hours end (or at KESTREL_NOTIFY_CAL_SUMMARY, default 07:00, if
  quiet hours are off), or later that day if the laptop was asleep then.
- **Quiet hours** (KESTREL_NOTIFY_QUIET, default off): calendar alerts wait until they end; /remind reminders don't.
- **Sleep and missed alerts:** an event that has already started gets no alert; one still ahead alerts on wake.
- **Once each:** the dedupe key is `calendar:<event id>:<start UTC>` in `deliveries`, so a restart never repeats
  one, each instance of a recurring event (its own id) alerts once, and a moved event alerts once at its new time.
- **At most KESTREL_NOTIFY_MAX_PER_HOUR** (default 6) calendar messages in any hour; more due at once go in one
  "more coming up" message.

**Event text is untrusted** (anyone who invites you writes it): titles and locations are defanged (defang.py),
quoted and cut, after a fixed "📅 From your calendar" label; descriptions, attendees and organizers are never
shown; no message has buttons.

**The reply is parsed strictly** (workspace-mcp 2.3.0's `get_events` text, `detailed=True`). That text copies event
fields as they are, line breaks included, so an event's description could imitate the reply's own lines. So:
- the event count in the first line must equal the blocks found; a block starts only after another block's
  `ID: ... | Link: ...` line, and the last block ends the reply. Anything else and the whole reply is refused
  (alerts pause and you're told; known issue KI-pending-notify-calendar-text);
- inside a block the server's own fields are read from the end, where its fixed order puts them after the free text
  (`Attendees`, `Attendee Details`, `ID`); a block that doesn't fit is skipped and counted;
- forged lines can only change their own event, which its author controls anyway.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from kestrel.google_tools import LINK_MASK, MASK
from kestrel.notify.clock import Clock
from kestrel.notify.defang import URL, defang
from kestrel.notify.notifier import REPEAT, TICK, describe
from kestrel.notify.store import MAX_ATTEMPTS, Store, iso, parse_iso

log = logging.getLogger("kestrel.telegram")

REFRESH = timedelta(minutes=5)
STALE = timedelta(minutes=15)  # no alert from a list older than this
PAUSE_AFTER = 3  # failed reads in a row before you're told
STALE_SENDING = timedelta(minutes=2)
TITLE_MAX = 120
LOCATION_MAX = 80
SUMMARY_MAX = 10
KIND = "calendar"
DAY_KIND = "calendar-day"
SKIP_TYPES = {"outOfOffice", "workingLocation"}
LABEL = "📅 From your calendar"
VIDEO = "Where: [video link in your calendar]"

SIGN_IN = (
    "Calendar alerts are off: the notifier's Google sign-in is missing or has expired (while the Google app is in "
    "Testing, Google asks again about every 7 days). At the laptop, run: uv run kestrel notify signin, then restart "
    "kestrel telegram. Reminders still work."
)
PAUSED = (
    "Calendar alerts are paused: Kestrel couldn't read your calendar 3 times in a row ({why}). They resume by "
    "themselves. Reminders still work."
)
BACK = "Calendar alerts are back."
OFF = "Calendar alerts are off: {why}. Reminders still work."


# --- the reply ---------------------------------------------------------------------------------


class Unreadable(ValueError):
    """The reply as a whole can't be trusted; nothing in it is used."""


class SignInNeeded(Unreadable):
    """The server answered with a sign-in request instead of events."""


class BadEvent(ValueError):
    """One block doesn't fit the format; that event is skipped."""


_FOR = re.escape(f" for {MASK}")
FIRST = re.compile(rf"Successfully retrieved (\d+) events from calendar 'primary'{_FOR}:")
NONE = re.compile(rf"No events found in calendar 'primary'{_FOR} for the specified time range\.")
EMPTY_PAGE = re.compile(
    rf"No events on this page in calendar 'primary'{_FOR} for the specified time range, and more pages remain\."
)
TRAILER = re.compile(r"\n\n?Next page token: (\S+)\nPagination time_min: (\S+)\Z")
HEADER = re.compile(r'- "(?P<title>.*)" \(Starts: (?P<start>[^,]*), Ends: (?P<end>[^,]*)\)')
ID_LINE = re.compile(r"  ID: (?P<id>[A-Za-z0-9_.@-]{1,1024}) \| Link: \S+")
FIELD = re.compile(r"  (?P<key>[A-Z][A-Za-z ]{1,30}): (?P<value>.*)")
STAMP = re.compile(r"(?P<stamp>\S+) \[[^\]]*\]")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
RESPONSES = ("needsAction", "declined", "tentative", "accepted")
OWNER_ENTRY = re.compile(rf"{re.escape(MASK)}: (?P<status>{'|'.join(RESPONSES)}|unknown)(?: .*)?")


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    start: datetime | None = None  # timed events: UTC
    day: date | None = None  # all-day events: first day
    day_end: date | None = None  # all-day events: day after the last (Google's exclusive end)
    status: str = "confirmed"
    event_type: str = "default"
    response: str | None = None  # your answer, if you're on the guest list
    organizer: str | None = None  # an address, lowercased, or MASK for you
    has_guests: bool = False
    popup: int | None = None  # the largest custom popup reminder, minutes
    reminders_off: bool = False  # custom reminders set to none
    location: str = ""
    video: bool = False


@dataclass
class Page:
    events: list[Event]
    bad: int  # blocks skipped as unreadable
    next_token: str | None = None
    next_time_min: str | None = None


def _boundary(text: str) -> tuple[datetime | None, date | None]:
    m = STAMP.fullmatch(text.strip())
    stamp = m.group("stamp") if m else ""
    if DAY.fullmatch(stamp):
        return None, date.fromisoformat(stamp)
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        raise BadEvent("start") from None
    if moment.tzinfo is None:
        raise BadEvent("start")
    return moment.astimezone(UTC), None


def _email(person: str) -> str | None:
    m = re.search(r"<([^<>]*)>\Z", person.strip())
    if not m:
        return None
    return MASK if m.group(1) == MASK else m.group(1).strip().lower()


def parse_block(lines: list[str]) -> Event:
    head = HEADER.fullmatch(lines[0])
    last = ID_LINE.fullmatch(lines[-1])
    if head is None or last is None or len(lines) < 5 or not lines[1].startswith("  Description: "):
        raise BadEvent("shape")
    details = max((i for i, line in enumerate(lines) if line.startswith("  Attendee Details: ")), default=-1)
    if details < 2 or not lines[details - 1].startswith("  Attendees: "):
        raise BadEvent("attendees")
    tail = lines[details + 1 : -1]
    if any(not line.startswith("    ") for line in tail):
        raise BadEvent("attendees")
    fields: dict[str, str] = {}
    for line in lines[2 : details - 1]:  # the last copy of each field is the server's (fixed order, after the text)
        if m := FIELD.fullmatch(line):
            fields[m.group("key")] = m.group("value")

    start, day = _boundary(head.group("start"))
    end, day_end = _boundary(head.group("end"))
    if (start is None) != (end is None):
        raise BadEvent("start")

    entries = [lines[details][len("  Attendee Details: ") :], *(line.strip() for line in tail)]
    has_guests = entries != ["None"]
    response = None
    for entry in entries if has_guests else []:
        if m := OWNER_ENTRY.fullmatch(entry):
            response = m.group("status")

    popup, off = None, False
    if "Reminders" in fields:
        try:
            overrides = json.loads(fields["Reminders"])
            minutes = [int(r["minutes"]) for r in overrides if r.get("method") == "popup"]
        except ValueError, TypeError, KeyError, AttributeError:
            raise BadEvent("reminders") from None
        off = overrides == []
        popup = max(minutes) if minutes else None

    location = fields.get("Location", "No Location")
    return Event(
        id=last.group("id"),
        title=head.group("title"),
        start=start,
        day=day,
        day_end=day_end,
        status=fields.get("Status", "confirmed"),
        event_type=fields.get("Event Type", "default"),
        response=response,
        organizer=_email(fields["Organizer"]) if "Organizer" in fields else None,
        has_guests=has_guests,
        popup=popup,
        reminders_off=off,
        location="" if location == "No Location" else location,
        video="Meeting Link" in fields,
    )


def parse_reply(text: str) -> Page:
    """One get_events page. Raises SignInNeeded or Unreadable for the whole reply; skips (and counts) one bad
    block."""
    if "ACTION REQUIRED" in text or LINK_MASK in text:
        raise SignInNeeded("sign-in needed")
    token = time_min = None
    if m := TRAILER.search(text):
        token, time_min = m.group(1), m.group(2)
        text = text[: m.start()]
    if NONE.fullmatch(text) and token is None:
        return Page([], 0)
    if EMPTY_PAGE.fullmatch(text) and token is not None:
        return Page([], 0, token, time_min)
    first, _, body = text.partition("\n")
    m = FIRST.fullmatch(first)
    if m is None or not body:
        raise Unreadable("not a get_events reply")
    lines = body.split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    for i, line in enumerate(lines):
        if not current and not line.startswith('- "'):
            raise Unreadable("an event block doesn't start where one ended")
        current.append(line)
        if ID_LINE.fullmatch(line) and (i + 1 == len(lines) or lines[i + 1].startswith('- "')):
            blocks.append(current)
            current = []
    if current or len(blocks) != int(m.group(1)):
        raise Unreadable("the event count doesn't match the events found")
    events, bad = [], 0
    for block in blocks:
        try:
            events.append(parse_block(block))
        except BadEvent:
            bad += 1
    return Page(events, bad, token, time_min)


# --- the rules ---------------------------------------------------------------------------------


def skip_reason(event: Event, trusted: frozenset[str]) -> str | None:
    if event.status == "cancelled":
        return "cancelled"
    if event.event_type in SKIP_TYPES:
        return "not an appointment"
    if event.response == "declined":
        return "declined"
    if event.response == "needsAction" or (event.has_guests and event.response is None and event.organizer != MASK):
        return None if event.organizer in trusted and event.organizer != MASK else "unanswered invitation"
    return None


def lead_for(event: Event, default: timedelta) -> timedelta | None:
    if event.reminders_off:
        return None
    if event.popup is not None:
        return min(timedelta(minutes=event.popup), timedelta(hours=24))
    return default


def in_quiet(moment: time, quiet: tuple[time, time] | None) -> bool:
    if quiet is None:
        return False
    begin, end = quiet
    return begin <= moment < end if begin < end else moment >= begin or moment < end


def event_key(event: Event) -> str:
    assert event.start is not None
    return f"{KIND}:{event.id}:{iso(event.start)}"


def _field(text: str, limit: int) -> str:
    clean = defang(text).replace('"', "'").strip()
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _when(start: datetime, now: datetime, zone: ZoneInfo) -> str:
    at = start.astimezone(zone)
    return at.strftime("%H:%M") if at.date() == now.astimezone(zone).date() else at.strftime("%a %d %b %H:%M")


def _until(start: datetime, now: datetime) -> str:
    minutes = max(1, math.ceil((start - now).total_seconds() / 60))
    return f"in {minutes} min" if minutes < 120 else f"in {minutes // 60} h {minutes % 60} min"


def alert_text(
    event: Event, now: datetime, zone: ZoneInfo, *, moved_from: str | None = None, repeat: bool = False
) -> str:
    assert event.start is not None
    note = f"{_until(event.start, now)}; moved from {moved_from}" if moved_from else _until(event.start, now)
    lines = [REPEAT] if repeat else []
    lines.append(f'{LABEL}: "{_field(event.title, TITLE_MAX)}" starts at {_when(event.start, now, zone)} ({note})')
    if event.location and not URL.search(event.location):
        lines.append(f'Where: "{_field(event.location, LOCATION_MAX)}"')
    elif event.video or event.location:
        lines.append(VIDEO)
    return "\n".join(lines)


def batch_text(events: list[Event], now: datetime, zone: ZoneInfo, limit: int) -> str:
    lines = [f"{LABEL}, {len(events)} more coming up (Kestrel sends at most {limit} calendar messages an hour):"]
    for event in events:
        assert event.start is not None
        lines.append(f'- {_when(event.start, now, zone)}: "{_field(event.title, TITLE_MAX)}"')
    return "\n".join(lines)


def day_text(events: list[Event]) -> str:
    lines = [f"{LABEL}, all day today:"]
    lines += [f'- "{_field(e.title, TITLE_MAX)}"' for e in events[:SUMMARY_MAX]]
    if len(events) > SUMMARY_MAX:
        lines.append(f"- and {len(events) - SUMMARY_MAX} more")
    return "\n".join(lines)


# --- the watcher -------------------------------------------------------------------------------


class Source(Protocol):
    def check(self) -> str: ...  # 'ok' or 'missing'; raises for a bad token
    def start(self) -> None: ...
    def call(self, tool: str, args: dict[str, Any]) -> str: ...
    def close(self) -> None: ...


Send = Callable[[str, Any], Awaitable[int | None]]


@dataclass
class Item:
    key: str
    kind: str
    text: str
    event: Event | None = None


class CalendarWatcher:
    def __init__(
        self,
        store: Store,
        send: Send,
        clock: Clock,
        source: Source,
        *,
        zone: str,
        lead: timedelta,
        quiet: tuple[time, time] | None,
        summary_at: time,
        max_per_hour: int,
        trusted: frozenset[str] = frozenset(),
        tick: timedelta = TICK,
    ):
        self.store = store
        self.send = send
        self.clock = clock
        self.source = source
        self.zone = ZoneInfo(zone)
        self.lead = lead
        self.quiet = quiet
        self.summary_at = quiet[1] if quiet else summary_at
        self.max_per_hour = max_per_hour
        self.trusted = trusted
        self.tick_every = tick
        self.events: list[Event] | None = None
        self.read_at: datetime | None = None
        self.next_read: datetime | None = None
        self.failures = 0
        self.paused = False
        self.off: str | None = None
        self.repeats: set[str] = set()
        self.counts: Counter[str] = Counter()

    # --- lifecycle ----------------------------------------------------------------------

    async def run(self) -> None:
        if not await self.startup():
            return
        try:
            while self.off is None:
                try:
                    await self.tick()
                except Exception as e:
                    log.warning("calendar tick failed: %s", describe(e))
                await asyncio.sleep(self.tick_every.total_seconds())
        finally:
            await asyncio.to_thread(self.source.close)

    async def startup(self) -> bool:
        """Check the token, start the server, and turn sends a crash left half-done into retries labelled as
        possible repeats. False (and you're told once) if calendar alerts can't run."""
        now = self.clock.now()
        for delivery in self.store.stale_sending(now, STALE_SENDING):
            if delivery.kind in (KIND, DAY_KIND):
                self.store.mark_failed(delivery.key, "interrupted: Kestrel stopped while sending", now)
                self.repeats.add(delivery.key)
        try:
            state = await asyncio.to_thread(self.source.check)
            if state != "ok":
                await self._stop(SIGN_IN)
                return False
            await asyncio.to_thread(self.source.start)
        except Exception as e:
            await self._stop(OFF.format(why=defang(str(e))[:300]))
            return False
        return True

    async def _stop(self, message: str) -> None:
        self.off = message
        log.warning("calendar alerts off: %s", message)
        await self._notice(message)

    async def _notice(self, text: str) -> None:
        try:
            await self.send(text, None)
        except Exception as e:
            log.warning("couldn't send a calendar notice: %s", describe(e))

    # --- reading -----------------------------------------------------------------------

    async def read(self, now: datetime) -> None:
        from kestrel.notify.google_source import MAX_PAGES, TOOL, page_args

        self.next_read = now + REFRESH
        events: list[Event] = []
        bad = 0
        token = time_min = None
        try:
            for _ in range(MAX_PAGES):
                args = page_args(now, token, time_min)
                page = parse_reply(await asyncio.to_thread(self.source.call, TOOL, args))
                events += page.events
                bad += page.bad
                if page.next_token is None:
                    break
                token, time_min = page.next_token, page.next_time_min
            else:
                raise Unreadable(f"more than {MAX_PAGES} pages")
        except SignInNeeded:
            await self._stop(SIGN_IN)
            return
        except Exception as e:
            self.failures += 1
            why = "the reply couldn't be read safely" if isinstance(e, Unreadable) else "the server didn't answer"
            log.warning("calendar read failed (%d in a row): %s", self.failures, why)
            if self.failures >= PAUSE_AFTER and not self.paused:
                self.paused = True
                await self._notice(PAUSED.format(why=why))
            return
        self.events, self.read_at, self.failures = events, now, 0
        reasons = Counter(r for e in events if (r := skip_reason(e, self.trusted)))
        log.info(
            "calendar: %d events read, %d kept, %d unreadable, skipped %s",
            len(events),
            len(events) - sum(reasons.values()),
            bad,
            dict(sorted(reasons.items())) or "none",
        )
        self.counts = Counter({"read": len(events), "unreadable": bad, **reasons})
        if self.paused:
            self.paused = False
            await self._notice(BACK)

    # --- each tick ---------------------------------------------------------------------

    async def tick(self) -> None:
        if self.off is not None:
            return
        now = self.clock.now()
        if self.next_read is None or now >= self.next_read:
            await self.read(now)
        if self.off is not None or self.events is None or self.read_at is None or now - self.read_at > STALE:
            return
        await self.deliver(now)
        self.store.set_last_tick(now, KIND)

    def due(self, now: datetime) -> list[Item]:
        assert self.events is not None
        local_now = now.astimezone(self.zone)
        if in_quiet(local_now.time(), self.quiet):
            return []
        kept = [e for e in self.events if skip_reason(e, self.trusted) is None]
        items: list[Item] = []
        today = local_now.date()
        all_day = [e for e in kept if e.day is not None and e.day <= today < (e.day_end or e.day + timedelta(days=1))]
        if all_day and local_now.time() >= self.summary_at:
            items.append(Item(f"{DAY_KIND}:{today.isoformat()}", DAY_KIND, day_text(all_day)))
        timed = sorted((e for e in kept if e.start is not None), key=lambda e: (e.start, e.id))
        for event in timed:
            lead = lead_for(event, self.lead)
            assert event.start is not None
            if lead is None or now >= event.start or now < event.start - lead:
                continue
            key = event_key(event)
            moved = self._moved_from(event)
            text = alert_text(event, now, self.zone, moved_from=moved, repeat=key in self.repeats)
            items.append(Item(key, KIND, text, event))
        open_items = []
        for item in items:
            found = self.store.delivery(item.key)
            if found is None or (found.status == "failed" and found.attempts < MAX_ATTEMPTS):
                open_items.append(item)
        return open_items

    def _moved_from(self, event: Event) -> str | None:
        prefix = f"{KIND}:{event.id}:"
        assert event.start is not None
        for key in reversed(self.store.sent_keys(prefix)):  # the latest earlier time
            earlier = parse_iso(key[len(prefix) :])
            if earlier != event.start:
                return _when(earlier, self.clock.now(), self.zone)
        return None

    async def deliver(self, now: datetime) -> None:
        items = self.due(now)
        if not items:
            return
        budget = self.max_per_hour - self.store.sent_count(KIND, now - timedelta(hours=1))
        if budget <= 0:
            return
        if len(items) > budget:
            single, rest = items[: budget - 1], items[budget - 1 :]
        else:
            single, rest = items, []
        for item in single:
            if self.store.begin(item.key, item.kind, now):
                await self._send([item], item.text)
        batch = [i for i in rest if i.event is not None]
        claimed = [i for i in batch if self.store.begin(i.key, i.kind, now)]
        if claimed:
            events = [i.event for i in claimed if i.event is not None]
            await self._send(claimed, batch_text(events, now, self.zone, self.max_per_hour))

    async def _send(self, items: list[Item], text: str) -> None:
        try:
            message_id = await self.send(text, None)
        except Exception as e:
            for item in items:
                attempts = self.store.mark_failed(item.key, describe(e), self.clock.now())
                if attempts >= MAX_ATTEMPTS:
                    log.warning("gave up on a calendar alert after %d attempts: %s", attempts, describe(e))
            return
        for item in items:
            self.store.mark_sent(item.key, message_id, self.clock.now())
            self.repeats.discard(item.key)
