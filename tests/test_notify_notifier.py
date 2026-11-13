"""The store and the ticker (docs/notifications-design.md, sections 1.2-1.4), with a fake clock and a
fake send: restart, catch-up after a gap, dedupe, a crash between send and mark, failed sends.
No Telegram, no network; the database is a scratch file."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from kestrel.notify.notifier import LATE_AFTER, REPEAT, TICK, Notifier
from kestrel.notify.store import MAX_PENDING, Store, TooMany

ZONE = "America/New_York"
CHAT = 424242
START = datetime(2026, 11, 13, 19, 0, tzinfo=UTC)  # 14:00 in New York


class FakeClock:
    def __init__(self, now: datetime = START):
        self.t = now

    def now(self) -> datetime:
        return self.t

    def advance(self, **kwargs) -> None:
        self.t += timedelta(**kwargs)


class FakeSend:
    def __init__(self):
        self.sent: list[tuple[str, list | None]] = []
        self.fail = 0  # this many next sends raise
        self.crash = False  # raise CancelledError after "sending": the process died mid-send
        self._id = 500

    async def __call__(self, text, buttons):
        if self.crash:
            self.sent.append((text, buttons))
            raise asyncio.CancelledError
        if self.fail:
            self.fail -= 1
            raise ConnectionError("network down")
        self.sent.append((text, buttons))
        self._id += 1
        return self._id

    def texts(self) -> list[str]:
        return [t for t, _ in self.sent]


@pytest.fixture
def setup(tmp_path):
    clock, send = FakeClock(), FakeSend()
    store = Store(tmp_path / "notify.db")
    notifier = Notifier(store, send, clock, late_grace=timedelta(hours=12))
    yield store, notifier, clock, send
    store.close()


def add(store, clock, minutes: float, text: str = "call the dentist"):
    return store.add(CHAT, text, clock.now() + timedelta(minutes=minutes), ZONE, clock.now())


def run(coro):
    return asyncio.run(coro)


def test_a_due_reminder_is_sent_once_with_done_and_snooze_buttons(setup):
    store, notifier, clock, send = setup
    r = add(store, clock, 5)
    run(notifier.tick())
    assert send.sent == []  # not due yet
    clock.advance(minutes=5)
    run(notifier.tick())
    run(notifier.tick())  # a second tick: no second send
    assert len(send.sent) == 1
    text, buttons = send.sent[0]
    assert text == "⏰ Reminder (you set this on Fri 13 Nov 14:00): call the dentist"
    assert buttons == [("Done", f"rd:{r.id}"), ("Snooze 10 min", f"rs:{r.id}")]
    after = store.get(r.id)
    assert after.status == "sent" and after.message_id == 501


def test_a_reminder_survives_a_restart(tmp_path):
    clock, send = FakeClock(), FakeSend()
    first = Store(tmp_path / "notify.db")
    add(first, clock, 60 * 24)  # tomorrow
    first.close()
    clock.advance(hours=24)
    second = Store(tmp_path / "notify.db")
    run(Notifier(second, send, clock, late_grace=timedelta(hours=12)).tick())
    second.close()
    assert send.texts() == ["⏰ Reminder (you set this on Fri 13 Nov 14:00): call the dentist"]


def test_catch_up_after_a_3h_gap_labels_late_reminders(setup):
    store, notifier, clock, send = setup
    run(notifier.tick())
    add(store, clock, 30, "stretch")
    add(store, clock, 90, "drink water")
    clock.advance(hours=3)  # asleep: no ticks
    run(notifier.tick())
    assert send.texts() == [
        "Late: due Fri 13 Nov 14:30 (the laptop was asleep or Kestrel was off)\n"
        "⏰ Reminder (you set this on Fri 13 Nov 14:00): stretch",
        "Late: due Fri 13 Nov 15:30 (the laptop was asleep or Kestrel was off)\n"
        "⏰ Reminder (you set this on Fri 13 Nov 14:00): drink water",
    ]


def test_a_reminder_on_time_by_one_tick_is_not_labelled_late(setup):
    store, notifier, clock, send = setup
    add(store, clock, 1)
    clock.advance(minutes=1, seconds=TICK.total_seconds())
    run(notifier.tick())
    assert not send.texts()[0].startswith("Late") and LATE_AFTER > TICK


def test_reminders_older_than_the_grace_come_in_one_missed_message(setup):
    store, notifier, clock, send = setup
    old = [add(store, clock, 10, "one"), add(store, clock, 20, "two")]
    recent = add(store, clock, 60 * 13, "three")
    clock.advance(hours=14)  # the first two are 13h50 and 13h40 late; the third 1h
    run(notifier.tick())
    run(notifier.tick())
    assert len(send.sent) == 2
    missed, late = send.sent
    assert missed == ("Missed while Kestrel was off:\n- Fri 13 Nov 14:10: one\n- Fri 13 Nov 14:20: two", None)
    assert late[0].startswith("Late: due Sat 14 Nov 03:00") and late[0].endswith(": three")
    assert [store.get(r.id).status for r in old] == ["missed", "missed"] and store.get(recent.id).status == "sent"


def test_a_crash_between_send_and_mark_is_sent_once_more_at_start_labelled(setup, tmp_path):
    store, notifier, clock, send = setup
    r = add(store, clock, 1)
    clock.advance(minutes=1)
    send.crash = True
    with pytest.raises(asyncio.CancelledError):
        run(notifier.tick())
    assert store.delivery(r.key).status == "sending"
    send.crash = False
    run(notifier.tick())  # the same process (or a quick restart): the `sending` row blocks a second send
    assert len(send.sent) == 1
    run(notifier.startup())  # restart within 2 minutes: too soon to call it stale
    assert len(send.sent) == 1
    clock.advance(minutes=3)
    restarted = Notifier(store, send, clock, late_grace=timedelta(hours=12))
    run(restarted.startup())
    run(restarted.startup())  # only once
    run(restarted.tick())
    assert len(send.sent) == 2
    assert send.texts()[1].startswith(REPEAT + "\nLate: due")
    assert store.get(r.id).status == "sent" and store.delivery(r.key).status == "sent"


def test_a_failed_send_is_retried_then_given_up_after_3_and_listed_as_not_delivered(setup, caplog):
    store, notifier, clock, send = setup
    r = add(store, clock, 1)
    clock.advance(minutes=1)
    send.fail = 5
    run(notifier.tick())  # attempt 1
    run(notifier.tick())  # too soon to retry
    assert store.delivery(r.key).attempts == 1
    clock.advance(seconds=20)
    run(notifier.tick())  # attempt 2
    clock.advance(seconds=30)
    run(notifier.tick())  # too soon (60 s before attempt 3)
    clock.advance(seconds=30)
    run(notifier.tick())  # attempt 3: given up
    clock.advance(minutes=10)
    run(notifier.tick())
    assert send.sent == [] and send.fail == 2
    assert store.delivery(r.key).status == "given_up" and store.get(r.id).status == "undelivered"
    assert [x.id for x in store.listing(CHAT)] == [r.id]
    assert "gave up" in caplog.text and "network down" in caplog.text


def test_a_failed_send_that_recovers_is_delivered_once(setup):
    store, notifier, clock, send = setup
    add(store, clock, 1)
    clock.advance(minutes=1)
    send.fail = 1
    run(notifier.tick())
    clock.advance(seconds=20)
    run(notifier.tick())
    run(notifier.tick())
    assert len(send.sent) == 1


def test_a_snooze_is_a_new_delivery_and_cancelled_ones_never_fire(setup):
    store, notifier, clock, send = setup
    r = add(store, clock, 1)
    gone = add(store, clock, 2, "cancelled one")
    assert store.set_status(gone.id, "cancelled", only_from=("pending", "undelivered"))
    clock.advance(minutes=2)
    run(notifier.tick())
    assert store.snooze(r.id, clock.now() + timedelta(minutes=10))
    assert not store.snooze(r.id, clock.now())  # only a delivered reminder can be snoozed
    clock.advance(minutes=10)
    run(notifier.tick())
    assert send.texts() == [send.texts()[0]] * 2 and len(send.sent) == 2
    assert store.get(r.id).snoozes == 1


def test_at_most_100_pending(setup):
    store, _, clock, _ = setup
    for i in range(MAX_PENDING):
        add(store, clock, 10 + i)
    with pytest.raises(TooMany):
        add(store, clock, 500)


def test_the_run_loop_survives_a_failing_tick(setup, monkeypatch, caplog):
    _, notifier, _, _ = setup
    calls = []

    async def broken_tick():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("disk full")
        raise asyncio.CancelledError

    monkeypatch.setattr(notifier, "tick", broken_tick)
    notifier.tick_every = timedelta(0)
    with pytest.raises(asyncio.CancelledError):
        run(notifier.run())
    assert len(calls) == 2 and "disk full" in caplog.text
