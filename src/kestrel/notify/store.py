"""logs/notify.db: everything the notifier knows, so a restart carries on (design sections 1.2, 1.4).

Three tables:
- reminders: the owner's text, due time (UTC) + the zone it was set in, status, snoozes, and the
  Telegram message that carries its buttons;
- deliveries: one row per message sent, under a UNIQUE dedupe key, so two ticks can't both send it;
- watch_state: the last tick, so a pause (sleep, hibernation) shows as a gap.

Delivery protocol (at-least-once, honestly labelled): `begin` inserts the row as `sending` (a second
insert of the same key fails), the caller sends, then `mark_sent` or `mark_failed`. A failed send is
retried on later ticks, at most MAX_ATTEMPTS in all, then given up. A row left `sending` by a crash
is sent once more at the next start, labelled "may be a repeat".
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

MAX_PENDING = 100
MAX_ATTEMPTS = 3
RETRY_AFTER = (timedelta(seconds=20), timedelta(seconds=60))  # before attempt 2, attempt 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    due_utc TEXT NOT NULL,
    zone TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'done', 'cancelled', 'missed', 'undelivered')),
    snoozes INTEGER NOT NULL DEFAULT 0,
    message_id INTEGER
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders (status, due_utc);
CREATE TABLE IF NOT EXISTS deliveries (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('sending', 'sent', 'failed', 'given_up')),
    attempts INTEGER NOT NULL,
    message_id INTEGER,
    error TEXT,
    updated_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watch_state (
    name TEXT PRIMARY KEY,
    last_check_utc TEXT NOT NULL
);
"""


class TooMany(RuntimeError):
    pass


@dataclass(frozen=True)
class Reminder:
    id: int
    chat_id: int
    text: str
    due: datetime  # UTC
    zone: str
    created: datetime  # UTC
    status: str
    snoozes: int
    message_id: int | None

    @property
    def key(self) -> str:
        """The dedupe key of this reminder's delivery at its current due time (a snooze is a new key)."""
        return f"reminder:{self.id}:{iso(self.due)}"


@dataclass(frozen=True)
class Delivery:
    key: str
    kind: str
    status: str
    attempts: int
    updated: datetime


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="seconds")


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)  # autocommit: each statement is durable
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- reminders ----------------------------------------------------------------------

    @staticmethod
    def _reminder(row: tuple) -> Reminder:
        rid, chat_id, text, due, zone, created, status, snoozes, message_id = row
        return Reminder(rid, chat_id, text, parse_iso(due), zone, parse_iso(created), status, snoozes, message_id)

    def _select(self, where: str, args: tuple = ()) -> list[Reminder]:
        rows = self.db.execute(
            "SELECT id, chat_id, text, due_utc, zone, created_utc, status, snoozes, message_id "
            f"FROM reminders WHERE {where} ORDER BY due_utc, id",
            args,
        ).fetchall()
        return [self._reminder(r) for r in rows]

    def add(self, chat_id: int, text: str, due: datetime, zone: str, now: datetime) -> Reminder:
        pending = self.db.execute("SELECT COUNT(*) FROM reminders WHERE status = 'pending'").fetchone()[0]
        if pending >= MAX_PENDING:
            raise TooMany(f"You have {pending} pending reminders, the most allowed. Cancel one first.")
        cursor = self.db.execute(
            "INSERT INTO reminders (chat_id, text, due_utc, zone, created_utc, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (chat_id, text, iso(due), zone, iso(now)),
        )
        found = self.get(int(cursor.lastrowid or 0))
        assert found is not None
        return found

    def get(self, reminder_id: int) -> Reminder | None:
        found = self._select("id = ?", (reminder_id,))
        return found[0] if found else None

    def listing(self, chat_id: int) -> list[Reminder]:
        """What /reminders shows: pending ones, and ones Telegram refused (not delivered)."""
        return self._select("chat_id = ? AND status IN ('pending', 'undelivered')", (chat_id,))

    def due(self, now: datetime) -> list[Reminder]:
        return self._select("status = 'pending' AND due_utc <= ?", (iso(now),))

    def set_status(self, reminder_id: int, status: str, *, only_from: tuple[str, ...] = ()) -> bool:
        """True if the row changed. `only_from` limits which statuses may change (a stale press can't
        undo a cancel)."""
        sql, args = "UPDATE reminders SET status = ? WHERE id = ?", [status, reminder_id]
        if only_from:
            sql += f" AND status IN ({', '.join('?' * len(only_from))})"
            args += list(only_from)
        return self.db.execute(sql, args).rowcount == 1

    def mark_delivered(self, reminder_id: int, message_id: int | None) -> None:
        self.db.execute(
            "UPDATE reminders SET status = 'sent', message_id = ? WHERE id = ? AND status = 'pending'",
            (message_id, reminder_id),
        )

    def snooze(self, reminder_id: int, until: datetime) -> bool:
        return (
            self.db.execute(
                "UPDATE reminders SET status = 'pending', due_utc = ?, snoozes = snoozes + 1, message_id = NULL "
                "WHERE id = ? AND status = 'sent'",
                (iso(until), reminder_id),
            ).rowcount
            == 1
        )

    # --- deliveries ---------------------------------------------------------------------

    def delivery(self, key: str) -> Delivery | None:
        row = self.db.execute(
            "SELECT key, kind, status, attempts, updated_utc FROM deliveries WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else Delivery(row[0], row[1], row[2], row[3], parse_iso(row[4]))

    def begin(self, key: str, kind: str, now: datetime) -> bool:
        """Claim a send. False if it was sent, is being sent, was given up, or failed too recently."""
        try:
            self.db.execute(
                "INSERT INTO deliveries (key, kind, status, attempts, updated_utc) VALUES (?, ?, 'sending', 1, ?)",
                (key, kind, iso(now)),
            )
            return True
        except sqlite3.IntegrityError:
            pass
        found = self.delivery(key)
        if found is None or found.status != "failed" or found.attempts >= MAX_ATTEMPTS:
            return False
        if now - found.updated < RETRY_AFTER[found.attempts - 1]:
            return False
        return (
            self.db.execute(
                "UPDATE deliveries SET status = 'sending', attempts = attempts + 1, updated_utc = ? "
                "WHERE key = ? AND status = 'failed'",
                (iso(now), key),
            ).rowcount
            == 1
        )

    def mark_sent(self, key: str, message_id: int | None, now: datetime) -> None:
        self.db.execute(
            "UPDATE deliveries SET status = 'sent', message_id = ?, error = NULL, updated_utc = ? WHERE key = ?",
            (message_id, iso(now), key),
        )

    def mark_failed(self, key: str, error: str, now: datetime) -> int:
        """Record a failed send; returns the attempts so far. At MAX_ATTEMPTS the row is given up."""
        self.db.execute(
            "UPDATE deliveries SET status = CASE WHEN attempts >= ? THEN 'given_up' ELSE 'failed' END, "
            "error = ?, updated_utc = ? WHERE key = ?",
            (MAX_ATTEMPTS, error, iso(now), key),
        )
        found = self.delivery(key)
        return found.attempts if found else MAX_ATTEMPTS

    def stale_sending(self, now: datetime, older_than: timedelta) -> list[Delivery]:
        rows = self.db.execute(
            "SELECT key, kind, status, attempts, updated_utc FROM deliveries "
            "WHERE status = 'sending' AND updated_utc <= ?",
            (iso(now - older_than),),
        ).fetchall()
        return [Delivery(r[0], r[1], r[2], r[3], parse_iso(r[4])) for r in rows]

    def retake(self, key: str, now: datetime) -> bool:
        """Claim a stale `sending` row again (after a crash between send and mark)."""
        return (
            self.db.execute(
                "UPDATE deliveries SET updated_utc = ? WHERE key = ? AND status = 'sending'", (iso(now), key)
            ).rowcount
            == 1
        )

    # --- watch state ----------------------------------------------------------------------

    def last_tick(self, name: str = "reminders") -> datetime | None:
        row = self.db.execute("SELECT last_check_utc FROM watch_state WHERE name = ?", (name,)).fetchone()
        return None if row is None else parse_iso(row[0])

    def set_last_tick(self, now: datetime, name: str = "reminders") -> None:
        self.db.execute(
            "INSERT INTO watch_state (name, last_check_utc) VALUES (?, ?) "
            "ON CONFLICT (name) DO UPDATE SET last_check_utc = excluded.last_check_utc",
            (name, iso(now)),
        )
