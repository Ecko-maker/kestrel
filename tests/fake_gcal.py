"""A fake Google Calendar behind a fake notifier source, for tests/test_notify_calendar.py.

`render()` writes workspace-mcp 2.3.0's `get_events` text (detailed, expanded instances) for Google-style event dicts.
It is a copy of the server's formatting for the fields these tests use, and test_the_fake_matches_the_real_server
checks it byte for byte against the server's own output (tests/fixtures/gcal/*.txt, scripts/gcal_goldens.py).
`FakeCalendar` filters, sorts and pages events like the Calendar API's events.list, and records every call.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from kestrel.google_tools import mask

OWNER = "owner@example.com"
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
LINK = "https://www.google.com/calendar/event?eid=ZXhhbXBsZQ"


def _boundary(item: dict, field: str) -> str:
    b = item[field]
    if "date" in b:
        day = datetime.fromisoformat(b["date"]).date()
        evidence = [f"weekday: {WEEKDAYS[day.weekday()]}", f"ISO weekday: {day.isoweekday()}"]
        if field == "end":
            evidence.append("exclusive all-day end")
        return f"{b['date']} [{'; '.join(evidence)}]"
    moment = datetime.fromisoformat(b["dateTime"].replace("Z", "+00:00"))
    evidence = []
    if b.get("timeZone"):
        moment = moment.astimezone(ZoneInfo(b["timeZone"]))
        evidence.append(b["timeZone"])
    evidence += [f"weekday: {WEEKDAYS[moment.weekday()]}", f"ISO weekday: {moment.isoweekday()}"]
    stamp = moment.isoformat() if b.get("timeZone") else b["dateTime"]
    return f"{stamp} [{'; '.join(evidence)}]"


def _person(p: dict | None) -> str | None:
    if not p:
        return None
    name, email = (p.get("displayName") or "").strip(), (p.get("email") or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return name or (f"<{email}>" if email else None)


def _block(item: dict) -> str:
    lines = [
        f"  Description: {item.get('description', 'No Description')}",
        f"  Location: {item.get('location', 'No Location')}",
        f"  Color ID: {item.get('colorId', 'None')}",
    ]
    if item.get("recurringEventId"):
        lines.append(f"  Recurring Event ID: {item['recurringEventId']}")
    if item.get("eventType", "default") != "default":
        lines.append(f"  Event Type: {item['eventType']}")
    if item.get("status", "confirmed") != "confirmed":
        lines.append(f"  Status: {item['status']}")
    reminders = item.get("reminders") or {}
    if not reminders.get("useDefault", True):
        lines.append(f"  Reminders: {json.dumps(reminders.get('overrides', []))}")
    if organizer := _person(item.get("organizer")):
        lines.append(f"  Organizer: {organizer}")
    if item.get("hangoutLink"):
        lines.append(f"  Meeting Link: {item['hangoutLink']}")
    attendees = item.get("attendees", [])
    lines.append(f"  Attendees: {', '.join(a.get('email', '') for a in attendees) if attendees else 'None'}")
    details = []
    for a in attendees:
        parts = [f"{a.get('email', 'unknown')}: {a.get('responseStatus', 'unknown')}"]
        if a.get("organizer"):
            parts.append("(organizer)")
        if a.get("optional"):
            parts.append("(optional)")
        details.append(" ".join(parts))
    joined = "\n    ".join(details)
    lines.append(f"  Attendee Details: {joined if details else 'None'}")
    head = (
        f'- "{item.get("summary", "No Title")}" (Starts: {_boundary(item, "start")}, Ends: {_boundary(item, "end")})\n'
    )
    return (
        head
        + "".join(f"{line}\n" for line in lines)
        + f"  ID: {item.get('id', 'No ID')} | Link: {item.get('htmlLink', LINK)}"
    )


def render(items: list[dict], *, next_page_token: str | None = None, time_min: str = "", address: str = OWNER) -> str:
    pagination = f"Next page token: {next_page_token}\nPagination time_min: {time_min}" if next_page_token else ""
    if not items:
        if next_page_token:
            return (
                f"No events on this page in calendar 'primary' for {address} for the specified time range, and more "
                f"pages remain.\n{pagination}"
            )
        return f"No events found in calendar 'primary' for {address} for the specified time range."
    text = f"Successfully retrieved {len(items)} events from calendar 'primary' for {address}:\n" + "\n".join(
        _block(i) for i in items
    )
    return text + (f"\n\n{pagination}" if next_page_token else "")


# --- events for scenario tests ------------------------------------------------------------------


def timed(eid: str, title: str, start: datetime, minutes: int = 60, **extra: Any) -> dict:
    zone = "America/New_York"
    item = {
        "id": eid,
        "status": "confirmed",
        "summary": title,
        "start": {"dateTime": start.astimezone(ZoneInfo(zone)).isoformat(), "timeZone": zone},
        "end": {
            "dateTime": (start + timedelta(minutes=minutes)).astimezone(ZoneInfo(zone)).isoformat(),
            "timeZone": zone,
        },
        "htmlLink": LINK,
    }
    item.update(extra)
    return item


def all_day(eid: str, title: str, day: str, days: int = 1, **extra: Any) -> dict:
    first = datetime.fromisoformat(day).date()
    item = {
        "id": eid,
        "status": "confirmed",
        "summary": title,
        "start": {"date": day},
        "end": {"date": (first + timedelta(days=days)).isoformat()},
        "htmlLink": LINK,
    }
    item.update(extra)
    return item


def invited(status: str, organizer: str = "dana@example.org") -> dict:
    return {
        "organizer": {"email": organizer},
        "attendees": [
            {"email": organizer, "responseStatus": "accepted", "organizer": True},
            {"email": OWNER, "responseStatus": status, "self": True},
        ],
    }


def _instant(b: dict, zone: str = "America/New_York") -> datetime:
    if "date" in b:
        return datetime.fromisoformat(b["date"]).replace(tzinfo=ZoneInfo(zone)).astimezone(UTC)
    return datetime.fromisoformat(b["dateTime"]).astimezone(UTC)


class FakeCalendar:
    """events.list over `items`: end after time_min, start before time_max, by start time, `page_size` a page."""

    def __init__(self, items: list[dict] | None = None, page_size: int = 50):
        self.items = list(items or [])
        self.page_size = page_size
        self.calls: list[tuple[str, dict]] = []
        self.reply: str | None = None  # a fixed reply instead (a sign-in request, garbage)
        self.fail = 0  # this many next calls raise

    def call(self, tool: str, args: dict) -> str:
        self.calls.append((tool, dict(args)))
        if self.fail:
            self.fail -= 1
            raise ConnectionError("the server didn't answer")
        if self.reply is not None:
            return mask(self.reply, OWNER)
        low = datetime.fromisoformat(args["time_min"].replace("Z", "+00:00"))
        high = datetime.fromisoformat(args["time_max"].replace("Z", "+00:00"))
        found = [i for i in self.items if _instant(i["end"]) > low and _instant(i["start"]) < high]
        found.sort(key=lambda i: _instant(i["start"]))
        offset = int(args.get("page_token") or 0)
        page = found[offset : offset + self.page_size]
        more = offset + self.page_size < len(found)
        token = str(offset + self.page_size) if more else None
        return mask(render(page, next_page_token=token, time_min=args["time_min"]), OWNER)


class FakeSource:
    """The watcher's Source: a token state, a start, and the fake calendar's replies (already masked)."""

    def __init__(self, calendar: FakeCalendar, token: str = "ok"):
        self.calendar = calendar
        self.token = token
        self.started = 0
        self.closed = 0
        self.start_error: Exception | None = None

    def check(self) -> str:
        if isinstance(self.token, Exception):
            raise self.token
        return self.token

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started += 1

    def call(self, tool: str, args: dict) -> str:
        return self.calendar.call(tool, args)

    def close(self) -> None:
        self.closed += 1
