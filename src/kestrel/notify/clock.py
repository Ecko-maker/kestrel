"""The wall clock, as one small object so tests can move time without waiting.

Scheduling always compares the wall clock with due times on each tick; it never sleeps until a due
time, because a long sleep is what breaks across Modern Standby (design section 1.2).
"""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, timezone-aware, in UTC."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
