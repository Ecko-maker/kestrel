"""Sending to Telegram from either side of the thread boundary.

The bot (python-telegram-bot) runs on an asyncio loop; the Agent runs on a worker thread and blocks
inside review(). Everything the Agent's side sends goes through `call_from_thread`, which runs the
request on the bot's loop and waits for it. Every send is plain text with link previews off: a
preview makes Telegram's servers fetch a URL with no click (design section 2).
"""

import asyncio
import warnings
from collections.abc import Awaitable, Callable, Coroutine
from datetime import timedelta
from typing import Any

from telegram import LinkPreviewOptions
from telegram.error import RetryAfter

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
FLOOD_RETRIES = 3  # RetryAfter waits honoured per request before giving up
SEND_TIMEOUT = 60.0  # seconds the Agent's thread waits for one request (flood waits included)


def retry_seconds(error: RetryAfter) -> float:
    with warnings.catch_warnings():  # PTB 22 deprecates the int form; either form is fine here
        warnings.simplefilter("ignore")
        wait = error.retry_after
    return wait.total_seconds() if isinstance(wait, timedelta) else float(wait)


class Outbox:
    """One bot, one loop. `bot` is a telegram.Bot, or a fake with the same coroutine methods."""

    def __init__(
        self,
        bot: Any,
        loop: asyncio.AbstractEventLoop,
        *,
        send_timeout: float = SEND_TIMEOUT,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ):
        self.bot = bot
        self.loop = loop
        self.send_timeout = send_timeout
        self.sleep = sleep

    async def call(self, method: str, **kwargs: Any) -> Any:
        """One Bot API request on the loop, waiting out flood control (RetryAfter) a few times."""
        if method in ("send_message", "edit_message_text"):
            kwargs.setdefault("link_preview_options", NO_PREVIEW)
        for attempt in range(FLOOD_RETRIES + 1):
            try:
                return await getattr(self.bot, method)(**kwargs)
            except RetryAfter as e:
                if attempt == FLOOD_RETRIES:
                    raise
                await self.sleep(retry_seconds(e) + 0.5)
        raise AssertionError("unreachable")

    async def send(self, chat_id: int, text: str, **kwargs: Any) -> Any:
        return await self.call("send_message", chat_id=chat_id, text=text, **kwargs)

    def run_from_thread[T](self, coro: Coroutine[Any, Any, T]) -> T:
        """Run a coroutine on the bot's loop and wait for it (worker threads only)."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(self.send_timeout)

    def call_from_thread(self, method: str, **kwargs: Any) -> Any:
        return self.run_from_thread(self.call(method, **kwargs))
