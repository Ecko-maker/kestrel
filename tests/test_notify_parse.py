"""The /remind parser and the notifier settings (docs/notifications-design.md, sections 2.2 and 9).
No I/O: the clock and the time zone are arguments."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from kestrel.notify import parse
from kestrel.notify.parse import MAX_TEXT, NO_TIME, ParseError, local, parse_reminder
from kestrel.notify.settings import DEFAULT_DB, NotifyConfigError, load_notify_settings

NY = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 13, 14, 2, tzinfo=NY)  # a Friday, 14:02 local


def at(body: str, now: datetime = NOW, zone: ZoneInfo = NY):
    return parse_reminder(body, now, zone)


def local_of(due: datetime, zone: ZoneInfo = NY) -> datetime:
    return due.astimezone(zone).replace(tzinfo=None)


# --- the five forms -----------------------------------------------------------------------


def test_a_24h_time_later_today():
    p = at("15:00 call the dentist")
    assert local_of(p.due) == datetime(2026, 11, 13, 15, 0) and p.text == "call the dentist"
    assert p.due.tzinfo == UTC and not p.rolled and not p.repeated


def test_a_time_that_has_passed_today_is_tomorrow_and_says_so():
    p = at("09:00 stretch")
    assert local_of(p.due) == datetime(2026, 11, 14, 9, 0) and p.rolled
    assert at("14:02 now exactly").rolled  # equal to now counts as passed


@pytest.mark.parametrize(
    ("body", "hour", "minute"),
    [("3pm call", 15, 0), ("3:30pm call", 15, 30), ("3 pm call", 15, 0), ("12am call", 0, 0), ("12pm call", 12, 0)],
)
def test_12h_times(body, hour, minute):
    p = at(body)
    assert local_of(p.due).time().hour == hour and local_of(p.due).minute == minute and p.text == "call"


@pytest.mark.parametrize(
    ("body", "delta"),
    [
        ("in 20m stretch", timedelta(minutes=20)),
        ("in 20 min stretch", timedelta(minutes=20)),
        ("in 2h stretch", timedelta(hours=2)),
        ("in 3 days stretch", timedelta(days=3)),
        ("in 1 hour stretch", timedelta(hours=1)),
    ],
)
def test_in_durations(body, delta):
    p = at(body)
    assert p.due == NOW.astimezone(UTC) + delta and p.text == "stretch"


def test_tomorrow_and_a_date():
    assert local_of(at("tomorrow 9:00 send the form").due) == datetime(2026, 11, 14, 9, 0)
    p = at("2026-11-20 09:30 renew passport")
    assert local_of(p.due) == datetime(2026, 11, 20, 9, 30) and p.text == "renew passport"
    assert local(p.due, "America/New_York") == "Fri 20 Nov 09:30"


def test_optional_at_and_to():
    p = at("at 3pm to call the dentist")
    assert local_of(p.due).hour == 15 and p.text == "call the dentist"
    assert at("in 20m to stretch").text == "stretch"


def test_the_parser_has_no_plain_text_trigger():
    """Only /remind creates reminders (owner decision 2026-11-13): no "remind me" entry point."""
    assert not any("remind_me" in name.lower() for name in dir(parse))


def test_text_keeps_a_word_that_looks_like_am():
    p = at("15:00 amend the doc")
    assert local_of(p.due).hour == 15 and p.text == "amend the doc"


# --- refusals: never a guess ----------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "",
        "call the dentist",
        "call the dentist at 3pm",  # time at the end: not a form
        "15:00",  # no text
        "25:00 x",
        "13pm x",
        "0am x",
        "15:60 x",
        "in 0m x",
        "in 20 fortnights x",
        "next friday x",
        "tomorrow x",
        "2026-13-01 09:00 x",
        "2026-02-30 09:00 x",
    ],
)
def test_anything_else_is_refused(body):
    with pytest.raises(ParseError):
        at(body)


def test_unreadable_times_get_the_try_line():
    with pytest.raises(ParseError) as e:
        at("call the dentist at 3pm")
    assert str(e.value) == NO_TIME


def test_limits_text_length_past_and_a_year_ahead():
    assert at("15:00 " + "x" * MAX_TEXT).text == "x" * MAX_TEXT
    with pytest.raises(ParseError, match="300 characters"):
        at("15:00 " + "x" * (MAX_TEXT + 1))
    with pytest.raises(ParseError, match="already passed"):
        at("2026-11-12 09:00 x")
    with pytest.raises(ParseError, match="a year ahead"):
        at("2027-11-14 09:00 x")
    with pytest.raises(ParseError, match="a year ahead"):
        at("in 400d x")
    assert at("2027-11-12 09:00 x")  # just under a year


# --- DST, two zones ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("zone", "gap_day", "now"),
    [
        (NY, "2027-03-14", datetime(2027, 3, 1, 12, 0, tzinfo=NY)),  # US: 02:00 -> 03:00
        (BERLIN, "2027-03-28", datetime(2027, 3, 1, 12, 0, tzinfo=BERLIN)),  # EU: 02:00 -> 03:00
    ],
)
def test_a_time_that_doesnt_exist_on_spring_forward_day_is_refused(zone, gap_day, now):
    with pytest.raises(ParseError, match="doesn't exist"):
        at(f"{gap_day} 02:30 x", now, zone)
    p = at(f"{gap_day} 03:30 x", now, zone)  # just after the gap: fine
    assert local_of(p.due, zone) == datetime.fromisoformat(f"{gap_day} 03:30") and not p.repeated


@pytest.mark.parametrize(
    ("zone", "back_day", "now", "summer_offset"),
    [
        (NY, "2027-11-07", datetime(2027, 11, 1, 12, 0, tzinfo=NY), timedelta(hours=-4)),
        (BERLIN, "2027-10-31", datetime(2027, 10, 20, 12, 0, tzinfo=BERLIN), timedelta(hours=2)),
    ],
)
def test_a_time_that_happens_twice_takes_the_first(zone, back_day, now, summer_offset):
    p = at(f"{back_day} 01:30 x" if zone is NY else f"{back_day} 02:30 x", now, zone)
    assert p.repeated
    assert p.due.astimezone(zone).utcoffset() == summer_offset  # the first one: still summer time


def test_a_reminder_across_the_dst_change_keeps_its_wall_time():
    """Set in summer time for a day in winter time: 09:00 local, not 08:00 or 10:00."""
    p = at("2026-11-02 09:00 x", datetime(2026, 10, 30, 12, 0, tzinfo=NY))
    assert local_of(p.due) == datetime(2026, 11, 2, 9, 0)
    assert p.due == datetime(2026, 11, 2, 14, 0, tzinfo=UTC)  # EST is UTC-5


# --- settings -------------------------------------------------------------------------------------


def test_notify_is_off_by_default_and_reads_nothing_else():
    assert not load_notify_settings({}).enabled
    assert not load_notify_settings({"KESTREL_NOTIFY": "off", "KESTREL_NOTIFY_TZ": "Not/AZone"}).enabled


def test_notify_on_with_defaults_and_overrides():
    s = load_notify_settings({"KESTREL_NOTIFY": "on", "KESTREL_NOTIFY_TZ": "Europe/Berlin"})
    assert s.enabled and s.db_path == DEFAULT_DB and s.late_grace == timedelta(hours=12) and s.zone == "Europe/Berlin"
    env = {"KESTREL_NOTIFY": "on", "KESTREL_NOTIFY_TZ": "UTC", "KESTREL_NOTIFY_LATE_GRACE": "30m"}
    s = load_notify_settings({**env, "KESTREL_NOTIFY_DB": "x.db"})
    assert s.late_grace == timedelta(minutes=30) and str(s.db_path) == "x.db"


def test_the_laptop_zone_is_an_iana_name(monkeypatch):
    s = load_notify_settings({"KESTREL_NOTIFY": "on"})
    ZoneInfo(s.zone)  # Windows' "Eastern Standard Time" would fail here
    assert "/" in s.zone or s.zone == "UTC"


@pytest.mark.parametrize(
    ("env", "says"),
    [
        ({"KESTREL_NOTIFY": "yes"}, "on or off"),
        ({"KESTREL_NOTIFY_STAGES": "reminders,calendar"}, "isn't built yet"),
        ({"KESTREL_NOTIFY_STAGES": "email"}, "isn't built yet"),
        ({"KESTREL_NOTIFY_STAGES": "pager"}, "unknown stage"),
        ({"KESTREL_NOTIFY_STAGES": " , "}, "empty"),
        ({"KESTREL_NOTIFY_LATE_GRACE": "12 hours"}, "30m, 12h or 2d"),
        ({"KESTREL_NOTIFY_LATE_GRACE": "8d"}, "at most 7d"),
        ({"KESTREL_NOTIFY_TZ": "Eastern Standard Time"}, "IANA"),
    ],
)
def test_bad_settings_name_the_line_to_fix(env, says):
    with pytest.raises(NotifyConfigError, match=says):
        load_notify_settings({"KESTREL_NOTIFY": "on", "KESTREL_NOTIFY_TZ": "UTC", **env})
