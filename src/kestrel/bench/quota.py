"""Wait-for-quota mode (`kestrel bench run --wait-for-quota`): sit out a provider's daily limit.

A free tier's daily limit (Groq: tokens or requests per day) ends a long run early: every task after it
errors, and the run stops after 3 errors in a row. In this mode a run that errored on a daily limit is
not recorded and not counted toward that stop. The runner waits until the limit should free, then runs
the same (task, repeat) again. Each finished run is recorded exactly once.

How long to wait: the server's Retry-After (or Groq's "try again in 7m12s") when the error carries one,
plus a small margin; otherwise retry every 15 minutes. Per-minute limits are not daily limits: the LLM
layer already waits those out, and if it gives up the run errors as before.

A dropped attempt can spend tokens before the limit stops it (the first model calls succeed, a later
one gets the 429). Those tokens fill the rolling 24 h window again, so retrying on the server's hint
alone can livelock (10-26/27: ~225k spent on dropped attempts; known issue #53). So the wait is at least
the time the window needs to free what that attempt spent, at the limit's daily rate, and the runner
checks --token-budget between attempts too.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from kestrel.llm import DAILY_LIMIT
from kestrel.tracing import redact

DEFAULT_WAIT = 15 * 60  # no hint from the server: try again every 15 minutes
MARGIN = 30  # added to the server's hint, so the first try after a wait doesn't land a second early
MAX_TOTAL_WAIT = 26 * 3600  # Groq's token budget is a rolling 24 h window: past this, something else is wrong
DAY = 24 * 3600
_LIMIT_TEXT = re.compile(r"on (?:tokens|requests) per day \((?:TPD|RPD)\): Limit (\d+), Used \d+, Requested \d+")


def daily_limit_wait(error: str | None) -> float | None:
    """Seconds to wait if this run's error was a daily limit, else None."""
    if not error or DAILY_LIMIT not in error:
        return None
    match = re.search(r"asks to wait (\d+(?:\.\d+)?)s", error)
    return float(match.group(1)) + MARGIN if match else DEFAULT_WAIT


def limit_text(error: str | None) -> str | None:
    """Groq's words for the daily limit it hit ("on tokens per day (TPD): Limit 200000, Used ..."), redacted."""
    match = _LIMIT_TEXT.search(error or "")
    return redact(match.group(0)) if match else None


def freeing_wait(error: str | None, spent: int) -> float:
    """Seconds for a rolling daily token window to free `spent` tokens at its daily rate (Limit per 24 h);
    0 when the error names no token limit."""
    match = _LIMIT_TEXT.search(error or "")
    if not match or "TPD" not in match.group(0) or spent <= 0:
        return 0.0
    return spent * DAY / int(match.group(1))


@dataclass
class QuotaWait:
    """What run_suite needs to wait out a daily limit. done_before/total: runs finished before this
    run_suite call (resume, an earlier scripted user) and runs in the whole invocation, for the status
    line. checkpoint: called with this call's finished results before each wait, so the results file is
    resumable if the process dies while it sleeps."""

    total: int
    done_before: int = 0
    checkpoint: Callable[[list], None] | None = None
    log: Callable[[str], None] = field(default=lambda line: print(line, flush=True))
    sleep: Callable[[float], None] = field(default=lambda seconds: time.sleep(seconds))
    now: Callable[[], datetime] = field(default=lambda: datetime.now().astimezone())
    max_total_wait: float = MAX_TOTAL_WAIT
    waited: float = 0.0  # seconds slept so far, over every wait

    def wait_for(self, error: str | None, spent: int = 0) -> float | None:
        """Seconds to wait before retrying this errored run (which spent `spent` billable tokens), or
        None: record the error as usual."""
        seconds = daily_limit_wait(error)
        if seconds is None:
            return None
        seconds = max(seconds, freeing_wait(error, spent))
        if self.waited + seconds > self.max_total_wait:
            self.log(f"[quota] gave up waiting after {self.waited / 3600:.1f} h in total; recording the error")
            return None
        return seconds

    def wait(
        self, seconds: float, done_here: int, task_id: str, repeat: int, error: str | None = None, spent: int = 0
    ) -> None:
        done = self.done_before + done_here
        at = self.now() + timedelta(seconds=seconds)
        spending = f"this attempt spent {spent:,} tokens; " if spent > 0 else ""
        limit = limit_text(error)
        self.log(
            f"[quota] daily limit at {task_id} #{repeat}: {done} done, {self.total - done} remaining; {spending}"
            f"waiting {_duration(seconds)}, next attempt {at:%Y-%m-%d %H:%M %Z}" + (f"; groq: {limit}" if limit else "")
        )
        self.sleep(seconds)
        self.waited += seconds


def _duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{max(minutes, 1)} min"
