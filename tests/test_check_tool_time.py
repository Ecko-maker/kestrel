"""The answer_has_tool_time check: the answer states the time, date or weekday get_current_time
returned. Formatting-tolerant, value-strict."""

import json
from pathlib import Path

import pytest

from kestrel.bench.checks import Outcome, run_check

FRIDAY_EVENING = "Friday 2026-10-16 23:05:12 CEST (UTC+0200)"


def time_events(*calls: tuple[str, str], ok: bool = True) -> list[dict]:
    """tool_call + tool_result events for get_current_time calls of (timezone, result)."""
    events: list[dict] = []
    for i, (tz, result) in enumerate(calls):
        call_id = f"call_{i}"
        events.append(
            {
                "type": "tool_call",
                "call_id": call_id,
                "name": "get_current_time",
                "arguments": json.dumps({"timezone": tz}),
            }
        )
        events.append(
            {
                "type": "tool_result",
                "call_id": call_id,
                "name": "get_current_time",
                "ok": ok,
                "ran": ok,
                "result": result,
            }
        )
    return events


def check(answer: str, show: str, events: list[dict] | None = None, args: dict | None = None) -> bool:
    o = Outcome(
        events=time_events(("Europe/Warsaw", FRIDAY_EVENING)) if events is None else events,
        answer=answer,
        stop_reason="answered",
        steps=2,
        workspace=Path("."),
    )
    c: dict = {"type": "answer_has_tool_time", "show": show} | ({"args": args} if args else {})
    return run_check(c, o)[0]


@pytest.mark.parametrize(
    "answer",
    [
        "It's 23:05 in Warsaw.",
        "It is 11:05 pm there.",
        "Right now: 11:05PM (Friday).",
        "It's 11:05 p.m. in Warsaw",
    ],
)
def test_time_in_any_format(answer: str) -> None:
    assert check(answer, "time")


def test_a_different_time_fails() -> None:
    assert not check("It's 21:05 in Warsaw.", "time")  # two hours off
    assert not check("It's 23:15 in Warsaw.", "time")
    assert not check("It's 11:05 am in Warsaw.", "time")  # right digits, wrong half of the day
    assert not check("It's 23:06 in Warsaw.", "time")  # seconds were 12: no rounding up


def test_rounding_up_only_from_30_seconds() -> None:
    events = time_events(("Europe/Warsaw", "Friday 2026-10-16 23:59:45 CEST (UTC+0200)"))
    assert check("It's 00:00 in Warsaw, just past midnight.", "time", events)
    assert check("It's 23:59 in Warsaw.", "time", events)
    assert not check("It's 00:01 in Warsaw.", "time", events)


def test_leading_zeros_and_mornings() -> None:
    events = time_events(("Africa/Cairo", "Monday 2026-10-19 07:04:00 EEST (UTC+0300)"))
    for answer in ("07:04", "7:04", "7:04 am", "7:04 A.M."):
        assert check(f"It is {answer} in Cairo.", "time", events), answer
    assert not check("It is 17:04 in Cairo.", "time", events)  # "7:04" inside 17:04 must not count


def test_dates() -> None:
    for answer in (
        "Today is 2026-10-16.",
        "It's 16 October there.",
        "Friday, October 16th.",
        "the 16th of Oct.",
        "Oct 16",
    ):
        assert check(answer, "date"), answer
    for answer in ("It's 15 October there.", "Today is 2026-10-17.", "October 6", "16 November"):
        assert not check(answer, "date"), answer


def test_weekdays() -> None:
    assert check("It's Friday in Warsaw.", "weekday")
    assert not check("It's Saturday in Warsaw.", "weekday")


def test_the_right_call_is_used() -> None:
    events = time_events(
        ("Asia/Tokyo", "Saturday 2026-10-17 06:05:12 JST (UTC+0900)"), ("Europe/Warsaw", FRIDAY_EVENING)
    )
    assert check("In Warsaw it's 23:05.", "time", events, {"timezone": "Warsaw"})
    assert not check("In Warsaw it's 06:05.", "time", events, {"timezone": "Warsaw"})  # Tokyo's time
    assert check("In Tokyo it's 06:05.", "time", events, {"timezone": "Tokyo"})


def test_no_usable_tool_result_fails() -> None:
    assert not check("It's 23:05.", "time", [])
    assert not check("It's 23:05.", "time", time_events(("Europe/Warsaw", FRIDAY_EVENING), ok=False))
    assert not check("It's 23:05.", "time", None, {"timezone": "Tokyo"})  # no call for that zone


def test_unknown_show_fails_with_a_reason() -> None:
    o = Outcome(
        events=time_events(("UTC", FRIDAY_EVENING)), answer="x", stop_reason="answered", steps=1, workspace=Path(".")
    )
    ok, detail = run_check({"type": "answer_has_tool_time", "show": "year"}, o)
    assert not ok and "unknown show" in detail
