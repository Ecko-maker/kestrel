"""TelegramEvents: the Agent's events, shown in a Telegram chat (design section 2).

Few messages, final answers only, never tool results or arguments: Telegram is a phone, and every
message puts text on Telegram's servers.
- step_started  "typing", at most once every 4 seconds
- tool_result   one line per step at most: "🔧 web_search ✓", "🔧 send_message ✗ rejected"
- answer        the final text, split over Telegram's limit
- error         "Error: ...", redacted, at most 500 characters
Everything else (text_delta, tool_call, llm_call, done) shows nothing: no streaming, since editing
a message per chunk runs into Telegram's flood limits. Sends happen one at a time from the Agent's
thread; a failed send is logged and dropped, never raised into the Agent.
"""

import logging
import time
from collections.abc import Callable

from kestrel.telegram.outbox import Outbox
from kestrel.telegram.text import split_message
from kestrel.tracing import redact

log = logging.getLogger("kestrel.telegram")

TYPING_EVERY = 4.0  # seconds; Telegram shows "typing" for about 5
MAX_ERROR = 500
STOPPED = "Stopped."


def tool_line(event: dict) -> str:
    """The name and how it went, never the arguments or the result."""
    if event.get("ok"):
        return f"🔧 {event.get('name')} ✓"
    decision = event.get("decision") or ""
    how = "rejected" if decision == "rejected" else "blocked" if decision in ("forbidden", "blocked") else "error"
    return f"🔧 {event.get('name')} ✗ {how}"


class TelegramEvents:
    def __init__(
        self,
        outbox: Outbox,
        chat_id: int,
        stopped: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.outbox = outbox
        self.chat_id = chat_id
        self.stopped = stopped  # /stop was sent: the run's error is shown as "Stopped." instead
        self.clock = clock
        self._last_typing: float | None = None
        self._tools: list[str] = []  # this step's tool lines, sent as one message
        self._error_sent = False

    def _send(self, text: str) -> None:
        try:
            self.outbox.call_from_thread("send_message", chat_id=self.chat_id, text=text)
        except Exception as e:
            log.warning("couldn't send a message to Telegram: %s", type(e).__name__)

    def _flush_tools(self) -> None:
        if self._tools:
            self._send("\n".join(self._tools))
            self._tools = []

    def __call__(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "step_started":
            self._flush_tools()
            now = self.clock()
            if self._last_typing is None or now - self._last_typing >= TYPING_EVERY:
                self._last_typing = now
                try:
                    self.outbox.call_from_thread("send_chat_action", chat_id=self.chat_id, action="typing")
                except Exception as e:
                    log.warning("couldn't send 'typing': %s", type(e).__name__)
        elif kind == "tool_result":
            self._tools.append(tool_line(event))
        elif kind == "error":
            self._flush_tools()
            self._error_sent = True
            self._send(STOPPED if self.stopped() else "Error: " + redact(str(event.get("message", "")))[:MAX_ERROR])
        elif kind == "answer":
            self._flush_tools()
            if event.get("stop_reason") != "error" or not self._error_sent:
                for part in split_message(str(event.get("text") or ""), event.get("trace_id")):
                    self._send(part)
        elif kind == "done":
            self._tools, self._error_sent, self._last_typing = [], False, None
