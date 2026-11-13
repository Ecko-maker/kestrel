"""Reminders in the Telegram channel (docs/notifications-design.md, section 2): `/remind`, "remind me",
`/reminders`, `/cancel <n>`, and the Done / Snooze buttons. Only with KESTREL_NOTIFY=on.

All of it runs in the bot, before any Agent: no model reads these messages and none of it is a tool,
so the agent fingerprint is unchanged. A reminder's text is always the owner's own typed words (the
bot already refuses forwarded messages and never passes quoted text). A "remind me" message the
parser can't read gets a refusal; it is not passed to the Agent, which has no reminder tool.

Button presses are checked like card presses: the allowed user (checked by the bot), a private chat
that is the user's, a known reminder of that chat, and the message that carries its buttons now.
"""

import asyncio
import contextlib
import logging
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from kestrel.notify.clock import Clock
from kestrel.notify.notifier import AWAKE, Buttons, Notifier, reminder_text, snooze_until
from kestrel.notify.parse import HELP, ParseError, local, parse_reminder, remind_me_body
from kestrel.notify.settings import NotifySettings
from kestrel.notify.store import Store, TooMany
from kestrel.telegram.outbox import Outbox, describe

log = logging.getLogger("kestrel.telegram")

COMMANDS = ("/remind", "/reminders", "/cancel")


def markup(keyboard: Buttons | None) -> InlineKeyboardMarkup | None:
    if not keyboard:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in keyboard]])


class Reminders:
    def __init__(self, settings: NotifySettings, chat_id: int, clock: Clock):
        self.settings = settings
        self.zone = ZoneInfo(settings.zone)
        self.chat_id = chat_id  # the allowed user's private chat: reminders go only there
        self.clock = clock
        self.store = Store(settings.db_path)
        self.notifier: Notifier | None = None
        self.task: asyncio.Task | None = None
        self._outbox: Outbox | None = None

    # --- lifecycle ------------------------------------------------------------------------

    def start(self, outbox: Outbox) -> None:
        self._outbox = outbox
        self.notifier = Notifier(self.store, self.send, self.clock, late_grace=self.settings.late_grace)
        self.task = asyncio.create_task(self.notifier.run())

    def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
        self.store.close()

    @property
    def outbox(self) -> Outbox:
        if self._outbox is None:
            raise RuntimeError("reminders aren't started yet")
        return self._outbox

    async def send(self, text: str, keyboard: Buttons | None) -> int | None:
        """The notifier's send: one plain-text message to the owner. Errors go back to the notifier."""
        sent = await self.outbox.send(self.chat_id, text, reply_markup=markup(keyboard))
        return getattr(sent, "message_id", None)

    async def say(self, chat_id: int, text: str) -> None:
        try:
            await self.outbox.send(chat_id, text)
        except Exception as e:
            log.warning("couldn't send a message to Telegram: %s", describe(e))

    # --- messages ---------------------------------------------------------------------------

    async def handle(self, chat_id: int, text: str) -> bool:
        """True if the message was a reminder command or a "remind me" message (and was answered)."""
        if text.startswith("/"):
            name, _, rest = text.partition(" ")
            name = name.split("@")[0].lower()
            if name not in COMMANDS:
                return False
            if name == "/remind":
                await (self.remind(chat_id, rest) if rest.strip() else self.say(chat_id, HELP))
            elif name == "/reminders":
                await self.say(chat_id, self.listing(chat_id))
            else:
                await self.cancel(chat_id, rest)
            return True
        body = remind_me_body(text)
        if body is None:
            return False
        await self.remind(chat_id, body)
        return True

    async def remind(self, chat_id: int, body: str) -> None:
        now = self.clock.now()
        try:
            parsed = parse_reminder(body, now, self.zone)
            reminder = self.store.add(chat_id, parsed.text, parsed.due, self.settings.zone, now)
        except (ParseError, TooMany) as e:
            await self.say(chat_id, str(e))
            return
        zone = self.settings.zone
        lines = [f"Reminder #{reminder.id} set: {local(parsed.due, zone)} ({zone}): {parsed.text}"]
        if parsed.rolled:
            lines.append("That's tomorrow: the time has passed today.")
        if parsed.repeated:
            lines.append("The clocks go back that night, so that time happens twice: this is the first one.")
        lines.append(AWAKE)
        await self.say(chat_id, "\n".join(lines))

    def listing(self, chat_id: int) -> str:
        found = self.store.listing(chat_id)
        if not found:
            return "No pending reminders.\n" + AWAKE
        lines = [f"Pending reminders ({self.settings.zone}):"]
        for r in found:
            note = "  NOT DELIVERED (Telegram refused it 3 times)" if r.status == "undelivered" else ""
            lines.append(f"#{r.id}  {local(r.due, r.zone)}  {r.text}{note}")
        lines += ["Cancel one with /cancel <number>.", AWAKE]
        return "\n".join(lines)

    async def cancel(self, chat_id: int, rest: str) -> None:
        number = rest.strip().lstrip("#")
        if not number.isascii() or not number.isdigit():
            await self.say(chat_id, "Which one? /reminders lists them; then /cancel <number>.")
            return
        reminder = self.store.get(int(number))
        if (
            reminder is None
            or reminder.chat_id != chat_id
            or not self.store.set_status(reminder.id, "cancelled", only_from=("pending", "undelivered"))
        ):
            await self.say(chat_id, f"No pending reminder #{number}. /reminders lists them.")
            return
        await self.say(chat_id, f"Cancelled #{reminder.id}: {reminder.text}")

    # --- buttons --------------------------------------------------------------------------

    @staticmethod
    def is_reminder_button(data: str | None) -> bool:
        return data is not None and data.startswith(("rd:", "rs:"))

    async def press(self, chat_id: int, message_id: int | None, data: str) -> bool:
        """Done or Snooze. False (ignored, not answered) unless it is a button on the message that
        carries this reminder's buttons now, in the owner's chat."""
        action, _, number = data.partition(":")
        if chat_id != self.chat_id or not number.isascii() or not number.isdigit():
            return False
        reminder = self.store.get(int(number))
        if (
            reminder is None
            or reminder.chat_id != chat_id
            or reminder.status != "sent"
            or reminder.message_id is None
            or reminder.message_id != message_id
        ):
            return False
        now = self.clock.now()
        if action == "rd":
            if not self.store.set_status(reminder.id, "done", only_from=("sent",)):
                return False
            outcome = "Done."
        else:
            until = snooze_until(now)
            if not self.store.snooze(reminder.id, until):
                return False
            outcome = f"Snoozed until {local(until, reminder.zone)}."
        with contextlib.suppress(Exception):  # the buttons go; a failed edit changes nothing stored
            await self.outbox.call(
                "edit_message_text",
                chat_id=chat_id,
                message_id=message_id,
                text=f"{reminder_text(reminder)}\n\n{outcome}",
            )
        return True
