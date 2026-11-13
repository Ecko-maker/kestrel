"""The notifier's `.env` settings (design section 9), checked before anything connects.

Off by default: with KESTREL_NOTIFY unset or `off`, nothing else is read and `kestrel telegram`
behaves exactly as without this package. Only the `reminders` stage exists; calendar and email
come with the Google step and are refused here until then.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DB = Path("logs") / "notify.db"
DEFAULT_GRACE = timedelta(hours=12)
MAX_GRACE = timedelta(days=7)
STAGES = {"reminders"}
NOT_BUILT = {"calendar": "stage 2", "email": "stage 3"}
DURATION = re.compile(r"(\d{1,4})([mhd])")
UNITS = {"m": "minutes", "h": "hours", "d": "days"}


class NotifyConfigError(RuntimeError):
    """A malformed notifier setting. The message names the .env line to fix."""


@dataclass(frozen=True)
class NotifySettings:
    enabled: bool
    db_path: Path = DEFAULT_DB
    late_grace: timedelta = DEFAULT_GRACE
    zone: str = "UTC"  # an IANA name: due times are stored in UTC with it, shown in it


def parse_duration(value: str) -> timedelta:
    m = DURATION.fullmatch(value.strip().lower())
    if not m:
        raise ValueError(value)
    return timedelta(**{UNITS[m.group(2)]: int(m.group(1))})


def local_zone_name() -> str:
    """The laptop's IANA zone (America/New_York), not Windows' "Eastern Standard Time", which has no
    DST rules in Python. tzlocal maps one to the other."""
    import tzlocal

    return str(tzlocal.get_localzone_name())


def load_notify_settings(env: Mapping[str, str] | None = None) -> NotifySettings:
    env = os.environ if env is None else env
    switch = (env.get("KESTREL_NOTIFY") or "off").strip().lower()
    if switch == "off":
        return NotifySettings(enabled=False)
    if switch != "on":
        raise NotifyConfigError("KESTREL_NOTIFY must be on or off (default off).")

    stages = {s.strip().lower() for s in (env.get("KESTREL_NOTIFY_STAGES") or "reminders").split(",") if s.strip()}
    for stage in sorted(stages - STAGES):
        if stage in NOT_BUILT:
            raise NotifyConfigError(
                f"KESTREL_NOTIFY_STAGES: {stage} isn't built yet ({NOT_BUILT[stage]}, with the Google step). "
                "Use KESTREL_NOTIFY_STAGES=reminders."
            )
        raise NotifyConfigError(f"KESTREL_NOTIFY_STAGES: unknown stage {stage!r}. Use KESTREL_NOTIFY_STAGES=reminders.")
    if not stages:
        raise NotifyConfigError("KESTREL_NOTIFY_STAGES is empty. Use KESTREL_NOTIFY_STAGES=reminders.")

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
    return NotifySettings(True, Path(db) if db else DEFAULT_DB, grace, zone)
