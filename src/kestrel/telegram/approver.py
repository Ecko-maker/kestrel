"""TelegramApprover: the approval gate's questions, answered with buttons in a Telegram chat.

Same Approver interface as TerminalApprover and WebApprover, so the Agent and the ApprovalGate don't
change: the gate still decides which calls need a card, and calls review() only for those. The
Agent runs on a worker thread; review() posts a card to the bot's loop and blocks that thread until
the user answers (design section 1).

Nothing runs without an explicit Approve press on that card:
- no answer within the timeout, a send that fails, /stop or shutdown -> rejected;
- a text reply to the card -> rejected, with that text as the reason;
- presses from anyone else, on another chat's card, on an old card, or with odd data -> ignored.
No session approval on Telegram, for any tool (owner decision Q1): for_session is always False,
whatever allow_session says. No edits in v1: reject with a reason instead.
"""

import logging
import re
import threading
import uuid
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import InlineKeyboardButtonLimit

from kestrel.approval import Decision, Notice
from kestrel.telegram.outbox import Outbox
from kestrel.telegram.text import card_text

log = logging.getLogger("kestrel.telegram")

APPROVAL_TIMEOUT = 300.0  # 5 minutes, the web console's value
MAX_REASON = 500
CALLBACK = re.compile(r"([ar]):([0-9a-f]{12})")  # a:<approval_id> approves, r:<approval_id> rejects
CALLBACK_DATA_LIMIT = InlineKeyboardButtonLimit.MAX_CALLBACK_DATA  # 64 bytes; ours are 14, with no tool data


def keyboard(approval_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Approve", callback_data=f"a:{approval_id}"),
                InlineKeyboardButton("Reject", callback_data=f"r:{approval_id}"),
            ]
        ]
    )


@dataclass
class Pending:
    chat_id: int
    future: Future = field(default_factory=Future)
    message_id: int | None = None  # set on the bot's loop as soon as the card exists


class TelegramApprover:
    """Bound to one chat. `press` and `reply` are called on the bot's loop; `review` on the Agent's thread."""

    def __init__(self, outbox: Outbox, chat_id: int, timeout: float = APPROVAL_TIMEOUT):
        self.outbox = outbox
        self.chat_id = chat_id
        self.timeout = timeout
        self._pending: dict[str, Pending] = {}
        self._lock = threading.Lock()
        self._closed = False

    @property
    def waiting(self) -> bool:
        """A card is waiting for the user."""
        with self._lock:
            return any(not p.future.done() for p in self._pending.values())

    def review(
        self, tool_name: str, args: dict, preview: str, *, allow_session: bool = False, notice: Notice | None = None
    ) -> Decision:
        approval_id = uuid.uuid4().hex[:12]
        pending = Pending(self.chat_id)
        with self._lock:
            if self._closed:
                return Decision("rejected", reason="Kestrel's Telegram channel is shutting down")
            self._pending[approval_id] = pending
        text, document = card_text(tool_name, preview, notice)
        try:
            self.outbox.run_from_thread(self._post(pending, approval_id, text, document))
            try:
                decision = pending.future.result(self.timeout)
                label = "Approved" if decision.status == "approved" else "Rejected"
            except FutureTimeout:
                decision = Decision("rejected", reason=f"no answer within {self.timeout / 60:g} minutes")
                label = "Expired: rejected"
        except Exception as e:  # the card couldn't be sent (network, flood, Telegram refused): nothing runs
            log.warning("approval card for %s not delivered: %s", tool_name, type(e).__name__)
            decision, label = Decision("rejected", reason="the approval card couldn't be delivered"), ""
        finally:
            with self._lock:
                self._pending.pop(approval_id, None)
        if label and pending.message_id is not None:
            self._mark(pending.message_id, f"{text}\n\n{label}")
        return Decision(decision.status, reason=decision.reason[:MAX_REASON])  # never edited, never for_session

    async def _post(self, pending: Pending, approval_id: str, text: str, document: str | None) -> None:
        if document is not None:  # nobody should approve what they can't see: the whole preview first
            await self.outbox.call(
                "send_document",
                chat_id=self.chat_id,
                document=document.encode("utf-8"),
                filename="preview.txt",
            )
        message = await self.outbox.call(
            "send_message", chat_id=self.chat_id, text=text, reply_markup=keyboard(approval_id)
        )
        pending.message_id = message.message_id  # on the loop, before any press can be handled

    def _mark(self, message_id: int, text: str) -> None:
        """Remove the buttons and record the outcome on the card. Best effort: the decision stands."""
        try:
            self.outbox.call_from_thread("edit_message_text", chat_id=self.chat_id, message_id=message_id, text=text)
        except Exception as e:
            log.warning("couldn't update an approval card: %s", type(e).__name__)

    def _find(self, chat_id: int, message_id: int | None, approval_id: str | None = None) -> Pending | None:
        with self._lock:
            for key, p in self._pending.items():
                if approval_id is not None and key != approval_id:
                    continue
                if p.chat_id == chat_id == self.chat_id and p.message_id == message_id and not p.future.done():
                    return p
        return None

    def press(self, chat_id: int, message_id: int | None, data: str | None) -> bool:
        """A button press, already checked to come from the allowed user. True if it decided a card.
        It must be this chat's card, on the message it was sent as, and still waiting."""
        m = CALLBACK.fullmatch(data or "")
        if m is None or (pending := self._find(chat_id, message_id, m[2])) is None:
            return False
        return _settle(pending, Decision("approved") if m[1] == "a" else Decision("rejected", reason=""))

    def reply(self, chat_id: int, reply_to_message_id: int | None, text: str) -> bool:
        """A text reply to a waiting card: reject it, with the text as the reason. True if it did."""
        if (pending := self._find(chat_id, reply_to_message_id)) is None:
            return False
        return _settle(pending, Decision("rejected", reason=text.strip()[:MAX_REASON]))

    def reject_all(self, reason: str) -> int:
        """Reject every waiting card (/stop). Later cards are still asked."""
        with self._lock:
            waiting = [p for p in self._pending.values() if not p.future.done()]
        return sum(_settle(p, Decision("rejected", reason=reason)) for p in waiting)

    def close(self, reason: str = "Kestrel's Telegram channel stopped") -> None:
        """Reject everything waiting, and anything asked from now on (shutdown)."""
        with self._lock:
            self._closed = True
        self.reject_all(reason)


def _settle(pending: Pending, decision: Decision) -> bool:
    try:
        pending.future.set_result(decision)
    except Exception:  # decided a moment ago by the timeout or another press
        return False
    return True
