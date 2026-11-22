"""The notifier's `.env` settings (design section 9), checked before anything connects.

Off by default: with KESTREL_NOTIFY unset or `off`, nothing else is read and `kestrel telegram`
behaves exactly as without this package.

Stages (KESTREL_NOTIFY_STAGES, comma-separated, default `reminders`):
- `reminders` (stage 1): /remind and friends. On whenever KESTREL_NOTIFY=on, listed or not, as in stage 1.
- `calendar` (stage 2): alerts before your appointments, read from your primary Google Calendar by the
  notifier's own read-only workspace-mcp instance. Refused unless Google is set up (the three Google lines of
  docs/google.md) and the notifier's token folder is its own (google_source.py).
- `email` (stage 3) isn't built and is refused.
"""

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from kestrel.notify.google_source import SourceSettings

DEFAULT_DB = Path("logs") / "notify.db"
DEFAULT_CONFIG = Path("notify.toml")
DEFAULT_GRACE = timedelta(hours=12)
MAX_GRACE = timedelta(days=7)
DEFAULT_LEAD = timedelta(minutes=30)
MAX_LEAD = timedelta(hours=24)  # the watcher reads the next 24 hours
DEFAULT_SUMMARY = time(7, 0)
DEFAULT_MAX_PER_HOUR = 6
STAGES = {"reminders", "calendar"}
NOT_BUILT = {"email": "stage 3"}
DURATION = re.compile(r"(\d{1,4})([mhd])")
UNITS = {"m": "minutes", "h": "hours", "d": "days"}
CLOCK = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class NotifyConfigError(RuntimeError):
    """A malformed notifier setting. The message names the .env line to fix."""


@dataclass(frozen=True)
class NotifySettings:
    enabled: bool
    db_path: Path = DEFAULT_DB
    late_grace: timedelta = DEFAULT_GRACE
    zone: str = "UTC"  # an IANA name: due times are stored in UTC with it, shown in it
    stages: frozenset[str] = frozenset({"reminders"})
    quiet: tuple[time, time] | None = None  # calendar alerts wait while inside it; reminders don't
    cal_lead: timedelta = DEFAULT_LEAD
    cal_summary: time = DEFAULT_SUMMARY  # the all-day summary, when quiet hours are off
    max_per_hour: int = DEFAULT_MAX_PER_HOUR
    trusted_organizers: frozenset[str] = frozenset()  # notify.toml: whose unanswered invitations still alert
    calendar_source: SourceSettings | None = field(default=None, repr=False)

    @property
    def calendar(self) -> bool:
        return "calendar" in self.stages


def parse_duration(value: str) -> timedelta:
    m = DURATION.fullmatch(value.strip().lower())
    if not m:
        raise ValueError(value)
    return timedelta(**{UNITS[m.group(2)]: int(m.group(1))})


def parse_clock(value: str) -> time:
    m = CLOCK.fullmatch(value.strip())
    if not m:
        raise ValueError(value)
    return time(int(m.group(1)), int(m.group(2)))


def parse_quiet(value: str) -> tuple[time, time] | None:
    """`off` (the default) or `HH:MM-HH:MM`, which may run past midnight (22:00-07:00)."""
    text = value.strip().lower()
    if text in ("", "off"):
        return None
    start, sep, end = text.partition("-")
    if not sep:
        raise ValueError(value)
    begin, finish = parse_clock(start), parse_clock(end)
    if begin == finish:
        raise ValueError(value)
    return begin, finish


def parse_lead(value: str) -> timedelta:
    """Minutes (`30`) or a duration (`30m`, `1h`), more than 0 and at most 24 hours."""
    text = value.strip().lower()
    lead = timedelta(minutes=int(text)) if text.isascii() and text.isdigit() else parse_duration(text)
    if not timedelta(0) < lead <= MAX_LEAD:
        raise ValueError(value)
    return lead


def local_zone_name() -> str:
    """The laptop's IANA zone (America/New_York), not Windows' "Eastern Standard Time", which has no
    DST rules in Python. tzlocal maps one to the other."""
    import tzlocal

    return str(tzlocal.get_localzone_name())


def load_trusted(path: Path) -> frozenset[str]:
    """`[calendar] trusted_organizers` from notify.toml (git-ignored; notify.example.toml shows the shape).
    No file means nobody: every unanswered invitation is skipped."""
    if not path.exists():
        return frozenset()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise NotifyConfigError(f"{path} isn't valid TOML ({type(e).__name__}); see notify.example.toml.") from None
    section = data.get("calendar", {})
    found = section.get("trusted_organizers", []) if isinstance(section, dict) else None
    if not isinstance(found, list) or not all(isinstance(a, str) and ADDRESS.fullmatch(a.strip()) for a in found):
        raise NotifyConfigError(
            f"{path}: [calendar] trusted_organizers must be a list of email addresses; see notify.example.toml."
        )
    return frozenset(a.strip().lower() for a in found)


def load_notify_settings(
    env: Mapping[str, str] | None = None, project: Path | None = None, workspace: Path | None = None
) -> NotifySettings:
    env = os.environ if env is None else env
    switch = (env.get("KESTREL_NOTIFY") or "off").strip().lower()
    if switch == "off":
        return NotifySettings(enabled=False)
    if switch != "on":
        raise NotifyConfigError("KESTREL_NOTIFY must be on or off (default off).")

    fix = "Use KESTREL_NOTIFY_STAGES=reminders or reminders,calendar."
    stages = {s.strip().lower() for s in (env.get("KESTREL_NOTIFY_STAGES") or "reminders").split(",") if s.strip()}
    for stage in sorted(stages - STAGES):
        if stage in NOT_BUILT:
            raise NotifyConfigError(f"KESTREL_NOTIFY_STAGES: {stage} isn't built yet ({NOT_BUILT[stage]}). {fix}")
        raise NotifyConfigError(f"KESTREL_NOTIFY_STAGES: unknown stage {stage!r}. {fix}")
    if not stages:
        raise NotifyConfigError(f"KESTREL_NOTIFY_STAGES is empty. {fix}")
    stages.add("reminders")  # stage 1 is on with KESTREL_NOTIFY=on, as before stage 2 existed

    db = (env.get("KESTREL_NOTIFY_DB") or "").strip()
    grace_text = (env.get("KESTREL_NOTIFY_LATE_GRACE") or "").strip()
    try:
        grace = parse_duration(grace_text) if grace_text else DEFAULT_GRACE
    except ValueError:
        raise NotifyConfigError("KESTREL_NOTIFY_LATE_GRACE must look like 30m, 12h or 2d (default 12h).") from None
    if not timedelta(0) < grace <= MAX_GRACE:
        raise NotifyConfigError("KESTREL_NOTIFY_LATE_GRACE must be more than 0 and at most 7d (default 12h).")

    zone = (env.get("KESTREL_NOTIFY_TZ") or "").strip()
    if not zone:
        try:
            zone = local_zone_name()
        except Exception:
            raise NotifyConfigError(
                "Couldn't read this laptop's time zone. Add a line like KESTREL_NOTIFY_TZ=America/New_York to .env."
            ) from None
    try:
        ZoneInfo(zone)
    except ZoneInfoNotFoundError, ValueError:
        raise NotifyConfigError(
            f"KESTREL_NOTIFY_TZ={zone!r} isn't an IANA time zone name (for example America/New_York)."
        ) from None

    try:
        quiet = parse_quiet(env.get("KESTREL_NOTIFY_QUIET") or "off")
    except ValueError:
        raise NotifyConfigError(
            "KESTREL_NOTIFY_QUIET must be off (the default) or two different times like 22:00-07:00."
        ) from None
    try:
        lead = parse_lead(env.get("KESTREL_NOTIFY_CAL_LEAD") or "30")
    except ValueError:
        raise NotifyConfigError(
            "KESTREL_NOTIFY_CAL_LEAD must be minutes from 1 to 1440, like 30 (the default), or 45m or 2h."
        ) from None
    try:
        summary = parse_clock(env.get("KESTREL_NOTIFY_CAL_SUMMARY") or "07:00")
    except ValueError:
        raise NotifyConfigError("KESTREL_NOTIFY_CAL_SUMMARY must be a time like 07:00 (the default).") from None
    per_hour_text = (env.get("KESTREL_NOTIFY_MAX_PER_HOUR") or str(DEFAULT_MAX_PER_HOUR)).strip()
    if not (per_hour_text.isascii() and per_hour_text.isdigit() and 1 <= int(per_hour_text) <= 60):
        raise NotifyConfigError("KESTREL_NOTIFY_MAX_PER_HOUR must be a whole number from 1 to 60 (default 6).")

    source = None
    trusted: frozenset[str] = frozenset()
    if "calendar" in stages:
        from kestrel.notify.google_source import load_source_settings

        project = Path.cwd() if project is None else project
        if workspace is None:
            workspace = Path(env.get("KESTREL_WORKSPACE") or "workspace")
        source = load_source_settings(env, project=project, workspace=workspace)
        trusted = load_trusted(Path((env.get("KESTREL_NOTIFY_CONFIG") or "").strip() or DEFAULT_CONFIG))

    return NotifySettings(
        True,
        Path(db) if db else DEFAULT_DB,
        grace,
        zone,
        frozenset(stages),
        quiet,
        lead,
        summary,
        int(per_hour_text),
        trusted,
        source,
    )
