"""The reminder ticker (design sections 1.2-1.4 and 2.2).

Every TICK it reads the wall clock and sends what is due. It never sleeps until a due time, so a
pause (Modern Standby, hibernation, Kestrel off) just means the next tick finds reminders past due:
- up to `late_grace` late: sent now, labelled "Late: due 15:00";
- older: one "Missed while Kestrel was off" message listing them all, never one message each.

Sending goes through the store's delivery protocol: a unique key per reminder and due time, so two
ticks can't send the same one; a failed send is retried at most 3 times in all, then the reminder is
"not delivered" (shown by /reminders). A send that a crash interrupted is sent once more at start,
labelled as a possible repeat.

`send(text, buttons)` is the channel's: it sends one plain-text message and returns its message id.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from kestrel.notify.clock import Clock
from kestrel.notify.parse import local
from kestrel.notify.store import MAX_ATTEMPTS, Reminder, Store
from kestrel.tracing import redact

log = logging.getLogger("kestrel.telegram")

TICK = timedelta(seconds=20)
GAP = 3 * TICK  # a longer silence between ticks means the process was paused
LATE_AFTER = timedelta(minutes=2)  # later than this gets the "Late" label
STALE_SENDING = timedelta(minutes=2)
SNOOZE = timedelta(minutes=10)
REPEAT = "(may be a repeat: Kestrel restarted while sending)"
AWAKE = "Kestrel can only remind you while this laptop is awake and kestrel telegram is running."

Buttons = list[tuple[str, str]]  # (label, callback_data)
Send = Callable[[str, Buttons | None], Awaitable[int | None]]


def buttons(reminder: Reminder) -> Buttons:
    return [("Done", f"rd:{reminder.id}"), ("Snooze 10 min", f"rs:{reminder.id}")]


def reminder_text(reminder: Reminder, *, late: bool = False, repeat: bool = False) -> str:
    lines = [REPEAT] if repeat else []
    if late:
        lines.append(f"Late: due {local(reminder.due, reminder.zone)} (the laptop was asleep or Kestrel was off)")
    lines.append(f"⏰ Reminder (you set this on {local(reminder.created, reminder.zone)}): {reminder.text}")
    return "\n".join(lines)


def missed_text(reminders: list[Reminder], *, repeat: bool = False) -> str:
    lines = [REPEAT] if repeat else []
    lines.append("Missed while Kestrel was off:")
    lines += [f"- {local(r.due, r.zone)}: {r.text}" for r in reminders]
    return "\n".join(lines)


def describe(error: BaseException) -> str:
    text = redact(str(error)).strip()[:200]
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


class Notifier:
    def __init__(self, store: Store, send: Send, clock: Clock, *, late_grace: timedelta, tick: timedelta = TICK):
        self.store = store
        self.send = send
        self.clock = clock
        self.late_grace = late_grace
        self.tick_every = tick

    async def run(self) -> None:
        """The background task: catch up once, then tick until cancelled. A failing tick is logged and
        the loop carries on."""
        await self.startup()
        while True:
            try:
                await self.tick()
            except Exception as e:
                log.warning("reminder tick failed: %s", describe(e))
            await asyncio.sleep(self.tick_every.total_seconds())

    async def startup(self) -> None:
        """Re-send, once, anything a crash left between `sending` and `sent`."""
        now = self.clock.now()
        for delivery in self.store.stale_sending(now, STALE_SENDING):
            if not self.store.retake(delivery.key, now):
                continue
            if delivery.kind == "reminder":
                found = self.store.get(int(delivery.key.split(":", 2)[1]))
                if found is None or found.status != "pending":
                    self.store.mark_sent(delivery.key, None, now)  # nothing left to repeat
                    continue
                late = now - found.due > LATE_AFTER
                await self._send(delivery.key, reminder_text(found, late=late, repeat=True), found, buttons(found))
            elif delivery.kind == "missed":
                ids = [int(i) for i in delivery.key.split(":", 1)[1].split(",")]
                missed = [r for i in ids if (r := self.store.get(i)) is not None and r.status == "pending"]
                if missed:
                    await self._send_missed(delivery.key, missed, repeat=True)
                else:
                    self.store.mark_sent(delivery.key, None, now)

    async def tick(self) -> None:
        now = self.clock.now()
        last = self.store.last_tick()
        if last is not None and now - last > GAP:
            log.info("reminders: %s since the last check (asleep or off); catching up", now - last)
        due = self.store.due(now)
        missed = [r for r in due if now - r.due > self.late_grace]
        if missed:  # oldest first: the missed list, then the late and on-time ones
            key = "missed:" + ",".join(str(r.id) for r in missed)
            if self.store.begin(key, "missed", now):
                await self._send_missed(key, missed)
        for reminder in due:
            if reminder in missed:
                continue
            late = now - reminder.due > LATE_AFTER
            if self.store.begin(reminder.key, "reminder", now):
                await self._send(reminder.key, reminder_text(reminder, late=late), reminder, buttons(reminder))
        self.store.set_last_tick(now)

    async def _send(self, key: str, text: str, reminder: Reminder, keyboard: Buttons) -> None:
        try:
            message_id = await self.send(text, keyboard)
        except Exception as e:
            self._failed(key, e, [reminder])
            return
        self.store.mark_sent(key, message_id, self.clock.now())
        self.store.mark_delivered(reminder.id, message_id)

    async def _send_missed(self, key: str, missed: list[Reminder], *, repeat: bool = False) -> None:
        try:
            message_id = await self.send(missed_text(missed, repeat=repeat), None)
        except Exception as e:
            self._failed(key, e, missed)
            return
        self.store.mark_sent(key, message_id, self.clock.now())
        for reminder in missed:
            self.store.set_status(reminder.id, "missed", only_from=("pending",))

    def _failed(self, key: str, error: Exception, reminders: list[Reminder]) -> None:
        attempts = self.store.mark_failed(key, describe(error), self.clock.now())
        log.warning("couldn't send a reminder (attempt %d of %d): %s", attempts, MAX_ATTEMPTS, describe(error))
        if attempts >= MAX_ATTEMPTS:
            for reminder in reminders:
                self.store.set_status(reminder.id, "undelivered", only_from=("pending",))
            log.warning("gave up on %s after %d attempts; /reminders lists it as not delivered", key, attempts)


def snooze_until(now: datetime) -> datetime:
    return now + SNOOZE
