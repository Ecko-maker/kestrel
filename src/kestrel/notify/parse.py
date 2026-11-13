"""Reminder text -> a due time, deterministically (design section 2.2). No model, no guessing.

Forms (after `/remind`, the only way to create a reminder; plain text such as "remind me ..." is
never read here and goes to the Agent as any message does, owner decision 2026-11-13):

    15:00 call the dentist           today, or tomorrow if 15:00 has passed (the reply says so)
    3pm call the dentist             the same; 3:30pm and "3 pm" too
    in 20m stretch                   now + 20 minutes (m, h or d)
    tomorrow 9:00 send the form      tomorrow
    2026-11-20 09:30 renew passport  that date

An optional "at" before a clock time and "to" before the text are allowed, so "/remind at 3pm to
call the dentist" reads. Anything else raises ParseError with the help text.

Local times are turned into UTC with the zone's real rules: a time that doesn't exist on a
spring-forward day is refused (owner's choice, 2026-11-13), and one that happens twice when the
clocks go back takes the first, with a note in the reply.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MAX_TEXT = 300
MAX_AHEAD = timedelta(days=365)

HELP = (
    "Reminders:\n"
    "/remind 15:00 call the dentist\n"
    "/remind 3pm call the dentist\n"
    "/remind in 20m stretch  (m, h or d)\n"
    "/remind tomorrow 9:00 send the form\n"
    "/remind 2026-11-20 09:30 renew passport\n"
    "/reminders  list pending ones\n"
    "/cancel <number>  cancel one"
)
NO_TIME = "I couldn't read the time. Try: /remind 15:00 <text>"

CLOCK_24 = r"(?P<h24>\d{1,2}):(?P<m24>\d{2})"
CLOCK_12 = r"(?P<h12>\d{1,2})(?::(?P<m12>\d{2}))?\s?(?P<ampm>am|pm)"
CLOCK = rf"(?:at\s+)?(?:{CLOCK_12}|{CLOCK_24})"
TEXT = r"(?:to\s+)?(?P<text>\S.*)"
UNIT = {
    **dict.fromkeys(("m", "min", "mins", "minute", "minutes"), "minutes"),
    **dict.fromkeys(("h", "hr", "hrs", "hour", "hours"), "hours"),
    **dict.fromkeys(("d", "day", "days"), "days"),
}
UNITS = "|".join(sorted(UNIT, key=len, reverse=True))
IN_FORM = re.compile(rf"in\s+(?P<n>\d{{1,4}})\s?(?P<unit>{UNITS})\s+{TEXT}", re.I | re.S)
TOMORROW_FORM = re.compile(rf"tomorrow\s+{CLOCK}\s+{TEXT}", re.I | re.S)
DATE_FORM = re.compile(rf"(?P<date>\d{{4}}-\d{{2}}-\d{{2}})\s+{CLOCK}\s+{TEXT}", re.I | re.S)
TIME_FORM = re.compile(rf"{CLOCK}\s+{TEXT}", re.I | re.S)


class ParseError(ValueError):
    """`str(error)` is the reply to send."""


@dataclass(frozen=True)
class Parsed:
    due: datetime  # UTC
    text: str
    rolled: bool = False  # the time had passed today, so it's tomorrow
    repeated: bool = False  # the clocks go back that night and the time happens twice: the first one


def clock_time(m: re.Match[str]) -> time:
    if m.group("ampm"):
        hour, minute = int(m.group("h12")), int(m.group("m12") or 0)
        if not 1 <= hour <= 12 or minute > 59:
            raise ParseError(NO_TIME)
        hour = hour % 12 + (12 if m.group("ampm").lower() == "pm" else 0)
        return time(hour, minute)
    hour, minute = int(m.group("h24")), int(m.group("m24"))
    if hour > 23 or minute > 59:
        raise ParseError(NO_TIME)
    return time(hour, minute)


def to_utc(day: date, at: time, zone: ZoneInfo) -> tuple[datetime, bool]:
    """(the UTC instant, whether that local time happens twice). Refuses a local time that doesn't exist."""
    naive = datetime.combine(day, at)
    first = naive.replace(tzinfo=zone, fold=0)
    if first.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != naive:
        raise ParseError(
            f"{at:%H:%M} doesn't exist on {day:%a %d %b} in {zone.key}: the clocks go forward that night. "
            "Pick another time."
        )
    second = naive.replace(tzinfo=zone, fold=1)
    return first.astimezone(UTC), first.utcoffset() != second.utcoffset()


def checked_text(text: str) -> str:
    text = text.strip()
    if not text:
        raise ParseError(NO_TIME)
    if len(text) > MAX_TEXT:
        raise ParseError(f"Reminder text is limited to {MAX_TEXT} characters (this one has {len(text)}).")
    return text


def parse_reminder(body: str, now: datetime, zone: ZoneInfo) -> Parsed:
    """`body` is what follows `/remind`; `now` is aware (any zone)."""
    body = body.strip()
    local_now = now.astimezone(zone)
    if m := IN_FORM.fullmatch(body):
        n = int(m.group("n"))
        if n == 0:
            raise ParseError(NO_TIME)
        delta = timedelta(**{UNIT[m.group("unit").lower()]: n})
        if delta > MAX_AHEAD:
            raise ParseError("Reminders can be set at most a year ahead.")
        return Parsed(now.astimezone(UTC) + delta, checked_text(m.group("text")))

    rolled = False
    if m := TOMORROW_FORM.fullmatch(body):
        day = local_now.date() + timedelta(days=1)
    elif m := DATE_FORM.fullmatch(body):
        try:
            day = date.fromisoformat(m.group("date"))
        except ValueError:
            raise ParseError(f"{m.group('date')} isn't a date.") from None
    elif m := TIME_FORM.fullmatch(body):
        day = local_now.date()
        if datetime.combine(day, clock_time(m)) <= local_now.replace(tzinfo=None):
            day += timedelta(days=1)
            rolled = True
    else:
        raise ParseError(NO_TIME)

    text = checked_text(m.group("text"))
    due, repeated = to_utc(day, clock_time(m), zone)
    if due <= now:
        raise ParseError("That time has already passed.")
    if due - now > MAX_AHEAD:
        raise ParseError("Reminders can be set at most a year ahead.")
    return Parsed(due, text, rolled, repeated)


def local(due: datetime, zone: str) -> str:
    """'Thu 20 Nov 09:30' in that zone."""
    return due.astimezone(ZoneInfo(zone)).strftime("%a %d %b %H:%M")
