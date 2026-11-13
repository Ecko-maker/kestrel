"""The Telegram bot: who may talk to Kestrel, one Agent per chat, and the python-telegram-bot wiring.

Access (design section 4), checked in code on every update, on top of python-telegram-bot's filters:
- messages only from TELEGRAM_ALLOWED_USER_ID, in a private chat whose id is that user's; anyone
  else gets no reply, no Agent and no trace, just one local log line with their numeric id;
- forwarded messages are refused (owner decision Q3): forwarded text is someone else's words, and
  the gate trusts the user's own text (typed URLs need no card, typed addresses aren't warned about);
- only the user's own message text reaches the Agent, never a quoted or replied-to message;
- button presses only from the same user, on a card still waiting in that chat.

Threading (design section 3): the Agent is synchronous and blocks inside review() while a card
waits. The message handler never awaits a run; it starts it on a worker thread and returns, so the
button press that answers the card is handled while the run waits for it. One run per chat at a time.

Transport: long polling only (no webhook, no open port), message and callback_query updates only,
messages sent while Kestrel was off dropped at start.
"""

import asyncio
import contextlib
import json
import logging
import os
import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kestrel import parse_feedback
from kestrel.agent import MAX_CONTEXT_TOKENS, Agent, ChatModel
from kestrel.approval import ApprovalGate
from kestrel.bench.lock import pid_alive
from kestrel.telegram.approver import APPROVAL_TIMEOUT, TelegramApprover
from kestrel.telegram.events import TelegramEvents
from kestrel.telegram.outbox import Outbox, describe
from kestrel.tools import ToolRegistry
from kestrel.tools import registry as default_registry
from kestrel.tracing import Span, Tracer, redact

log = logging.getLogger("kestrel.telegram")

LOCK_FILE = Path("logs") / "telegram.lock"
ALLOWED_UPDATES = ["message", "callback_query"]

E2E_NOTICE = "Telegram is not end-to-end encrypted: prompts, answers and approval cards are stored by Telegram."
STARTED = "Kestrel is on. Messages sent while it was off were skipped. Send /help for commands."
FORWARD_REFUSED = "Forwarded messages aren't supported; paste the part you mean as your own words."
TEXT_ONLY = "Text only for now."
WORKING = "Still working on your last message."
WAITING = "Waiting for your answer on the card above."
HELP = (
    "Type a request. Risky actions show a card with Approve and Reject; reply to a card to reject it "
    "with a reason.\n"
    "/new  start a new conversation\n"
    "/stop  reject waiting cards and stop after the current step\n"
    "/good or /bad [note]  rate the last answer"
)


class AlreadyRunning(RuntimeError):
    """Another `kestrel telegram` holds the lock on this machine."""


class StopRequested(Exception):
    """Raised instead of the next model call after /stop."""


class StoppableLLM:
    """The model, unchanged, except that after stop() its next call raises: a run then ends after
    the current step (the Agent reports an error, which the chat shows as "Stopped."). Everything
    else (router hooks, provider names, call info) is the wrapped model's own."""

    def __init__(self, llm: Any):
        self._llm = llm
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def reset(self) -> None:
        self._stop.clear()

    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def chat(self, messages: list[dict], tools: list[dict] | None = None, **kwargs: Any) -> dict:
        if self._stop.is_set():
            raise StopRequested("stopped with /stop")
        return self._llm.chat(messages, tools, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)


class TelegramTracer(Tracer):
    """Records each run as usual, marked kestrel.channel = "telegram". The user and chat ids aren't
    stored: with one allowed user they add nothing, and they're personal data."""

    def start_trace(self, name: str = "agent_run") -> Span:
        span = super().start_trace(name)
        span.set("kestrel.channel", "telegram")
        return span


@dataclass
class Chat:
    agent: Agent
    approver: TelegramApprover
    llm: StoppableLLM
    task: asyncio.Task | None = None
    last_trace: str | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()


class TelegramBot:
    """Everything except the transport. Updates arrive as python-telegram-bot objects (or fakes with
    the same attributes); `attach` gives it the bot to send with and the loop it runs on."""

    def __init__(
        self,
        allowed_user_id: int,
        llm: ChatModel,
        tracer: Tracer,
        *,
        tools: ToolRegistry = default_registry,
        audit_log: Path | None = None,
        approval_timeout: float = APPROVAL_TIMEOUT,
        max_context_tokens: int = MAX_CONTEXT_TOKENS,
    ):
        self.allowed_user_id = allowed_user_id
        self.llm = llm
        self.tracer = tracer
        self.tools = tools
        self.audit_log = audit_log  # None: ApprovalGate's default, approval.AUDIT_LOG
        self.approval_timeout = approval_timeout
        self.max_context_tokens = max_context_tokens
        self.chats: dict[int, Chat] = {}  # one Agent per chat_id (one in practice: private chats, one user)
        self._outbox: Outbox | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def outbox(self) -> Outbox:
        if self._outbox is None:
            raise RuntimeError("the bot isn't attached to Telegram yet")
        return self._outbox

    def attach(self, bot: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._outbox = Outbox(bot, loop)

    def new_chat(self, chat_id: int) -> Chat:
        llm = StoppableLLM(self.llm)
        approver = TelegramApprover(self.outbox, chat_id, self.approval_timeout)
        agent = Agent(
            llm,
            tools=self.tools,
            on_event=TelegramEvents(self.outbox, chat_id, stopped=llm.stop_requested),
            gate=ApprovalGate(approver, log_path=self.audit_log),
            tracer=self.tracer,
            max_context_tokens=self.max_context_tokens,
        )
        self.chats[chat_id] = chat = Chat(agent, approver, llm)
        return chat

    async def say(self, chat_id: int, text: str) -> None:
        try:
            await self.outbox.send(chat_id, text)
        except Exception as e:  # logged, never raised: a failed send must not stop the bot
            log.warning("couldn't send a message to Telegram: %s", describe(e))

    # --- updates -------------------------------------------------------------------

    def allowed(self, message: Any) -> bool:
        user, chat = getattr(message, "from_user", None), getattr(message, "chat", None)
        return (
            user is not None
            and chat is not None
            and user.id == self.allowed_user_id
            and chat.type == "private"
            and chat.id == user.id
        )

    @staticmethod
    def log_ignored(user_id: int | None) -> None:
        log.info("ignored update from user id %s", user_id if user_id is not None else "unknown")

    async def on_message(self, message: Any) -> None:
        if not self.allowed(message):  # no reply: replying would confirm the bot exists to a stranger
            self.log_ignored(getattr(getattr(message, "from_user", None), "id", None))
            return
        chat_id = message.chat.id
        if message.forward_origin is not None:
            await self.say(chat_id, FORWARD_REFUSED)
            return
        if message.text is None:  # photos, files, voice notes, stickers, locations
            await self.say(chat_id, TEXT_ONLY)
            return
        chat = self.chats.get(chat_id) or self.new_chat(chat_id)
        reply_to = getattr(message, "reply_to_message", None)
        if reply_to is not None and chat.approver.reply(chat_id, reply_to.message_id, message.text):
            return  # "reject, and here's why" in one action
        text = message.text.strip()  # only the user's own words: never the quoted or replied-to text
        if not text:
            return
        if text.startswith("/"):
            await self.command(chat, chat_id, text)
            return
        if chat.running:  # dropped, not queued: a queued message could run after you changed your mind
            await self.say(chat_id, WAITING if chat.approver.waiting else WORKING)
            return
        chat.task = asyncio.create_task(self._run(chat, chat_id, text))
        self._tasks.add(chat.task)
        chat.task.add_done_callback(self._tasks.discard)

    async def _run(self, chat: Chat, chat_id: int, text: str) -> None:
        chat.llm.reset()
        try:
            result = await asyncio.to_thread(chat.agent.run, text)  # the Agent blocks here, not the loop
            chat.last_trace = result.trace_id
        except Exception as e:  # Agent.run reports its own errors; this is a bug in the channel
            log.warning("a run failed: %s", type(e).__name__)
            await self.say(chat_id, f"Error: {type(e).__name__}")

    async def command(self, chat: Chat, chat_id: int, text: str) -> None:
        name = text.split()[0].split("@")[0].lower()
        if name == "/stop":
            if not chat.running:
                await self.say(chat_id, "Nothing is running.")
                return
            chat.llm.stop()
            rejected = chat.approver.reject_all("stopped by the user (/stop)")
            await self.say(
                chat_id, "Stopping after the current step." + (" Waiting card rejected." if rejected else "")
            )
        elif name == "/new":
            if chat.running:
                await self.say(chat_id, "Still working on your last message. Send /stop first.")
                return
            self.new_chat(chat_id)  # a new Agent: the only way taint ends (owner decision 3)
            await self.say(chat_id, "New conversation.")
        elif (feedback := parse_feedback(text)) is not None:
            if chat.last_trace is None:
                await self.say(chat_id, "Nothing to rate yet.")
                return
            self.tracer.rate(chat.last_trace, *feedback)
            await self.say(chat_id, f"Rated {feedback[0]}.")
        else:  # /start, /help, anything else
            await self.say(chat_id, HELP)

    async def on_callback(self, query: Any) -> None:
        """A button press. Anything not from the allowed user, or not on a waiting card in that chat, is
        ignored without even answering the query."""
        user, message = getattr(query, "from_user", None), getattr(query, "message", None)
        if user is None or user.id != self.allowed_user_id or message is None:
            self.log_ignored(getattr(user, "id", None))
            return
        chat = self.chats.get(message.chat.id)
        if chat is None or not chat.approver.press(message.chat.id, message.message_id, query.data):
            return
        with contextlib.suppress(Exception):
            await query.answer()  # stops the phone's spinner

    # --- lifecycle -----------------------------------------------------------------

    async def started(self, bot: Any) -> None:
        """Attach, then greet the allowed user. If the greeting fails (TELEGRAM_ALLOWED_USER_ID=1 while
        you look up your id, or you never pressed Start), say so and keep polling: the "ignored update
        from user id" line is how you find your id (docs/telegram.md)."""
        self.attach(bot, asyncio.get_running_loop())
        try:
            await self.outbox.send(self.allowed_user_id, STARTED)  # a private chat's id is the user's id
        except Exception as e:
            log.warning("couldn't send the start message to TELEGRAM_ALLOWED_USER_ID: %s", describe(e))
            log.warning("Still polling. A message from anyone else shows here as 'ignored update from user id N'.")

    def shutdown(self) -> None:
        for chat in self.chats.values():
            chat.llm.stop()
            chat.approver.close()


# --- one poller per machine ----------------------------------------------------------


@contextlib.contextmanager
def single_instance(path: Path = LOCK_FILE) -> Iterator[None]:
    """Refuse a second `kestrel telegram` on this machine. A lock left by a process that is gone is
    replaced. (A second poller elsewhere shows up as Telegram's 409 Conflict instead.)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                held = json.loads(path.read_text(encoding="utf-8"))
            except ValueError, OSError:
                held = {}
            if held.get("host") == socket.gethostname() and not pid_alive(int(held.get("pid", -1))):
                path.unlink(missing_ok=True)  # stale: that process is gone
                continue
            raise AlreadyRunning(
                f"Another `kestrel telegram` is running (PID {held.get('pid', '?')} since {held.get('started', '?')}). "
                "Only one may poll the bot at a time."
            ) from None
    else:
        raise AlreadyRunning(f"Couldn't take {path}.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(
            {
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "started": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            f,
        )
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


# --- python-telegram-bot wiring and the command ------------------------------------------


class RedactingFormatter(logging.Formatter):
    """Log lines (tracebacks included) pass through redact(): Bot API URLs carry the token."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


LOGGERS = {  # name -> level; each gets one redacting handler, the root logger is left alone
    "kestrel.telegram": logging.INFO,  # "ignored update from user id <n>": how you find your id
    "telegram": logging.WARNING,  # python-telegram-bot itself
    "httpx": logging.WARNING,  # httpx logs every request URL at INFO, and Bot API URLs carry the token
    "httpcore": logging.WARNING,
}


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("[telegram] %(message)s"))
    for name, level in LOGGERS.items():
        logger = logging.getLogger(name)
        logger.setLevel(level)
        if not any(isinstance(h.formatter, RedactingFormatter) for h in logger.handlers):
            logger.addHandler(handler)


def build_application(core: TelegramBot, token: str) -> Any:
    from telegram import Update
    from telegram.error import Conflict
    from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

    async def message_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is not None:
            await core.on_message(update.message)

    async def ignored_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        core.log_ignored(update.effective_user.id if update.effective_user else None)

    async def callback_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.callback_query is not None:
            await core.on_callback(update.callback_query)

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(context.error, Conflict):
            log.error("Another program is polling this bot (Telegram said 409 Conflict). Stop it and start again.")
            context.application.stop_running()
            return
        log.warning("Telegram error: %s", redact(str(context.error))[:300])

    async def post_init(app: Application) -> None:
        await core.started(app.bot)

    async def post_shutdown(app: Application) -> None:
        core.shutdown()

    app = Application.builder().token(token).post_init(post_init).post_shutdown(post_shutdown).build()
    mine = filters.User(user_id=core.allowed_user_id) & filters.ChatType.PRIVATE  # the code checks again
    app.add_handler(MessageHandler(mine, message_update))
    app.add_handler(MessageHandler(filters.ALL, ignored_update))  # everyone else: one log line, no reply
    app.add_handler(CallbackQueryHandler(callback_update))  # every press is checked in on_callback
    app.add_error_handler(on_error)
    return app


def run(
    llm: ChatModel,
    *,
    print_line: Callable[[str], None] = print,
    max_context_tokens: int = MAX_CONTEXT_TOKENS,
) -> None:
    """`kestrel telegram`: settings first (nothing connects without both .env lines), then poll."""
    from kestrel.telegram.settings import load_settings

    settings = load_settings()
    setup_logging()
    tracer = TelegramTracer()
    core = TelegramBot(settings.allowed_user_id, llm, tracer, max_context_tokens=max_context_tokens)
    with single_instance():
        app = build_application(core, settings.token)
        print_line(E2E_NOTICE)
        if not tracer.record_content:
            print_line("KESTREL_TRACE_CONTENT=off: nothing is kept locally, but Telegram still has the text.")
        print_line("Polling Telegram (no open port). Messages sent while Kestrel was off are skipped. Ctrl+C to stop.")
        app.run_polling(allowed_updates=ALLOWED_UPDATES, drop_pending_updates=True)
