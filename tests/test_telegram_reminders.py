"""Reminders in the Telegram channel, offline (docs/notifications-design.md, section 2): a fake Bot
records what would be sent, a fake clock moves time, a scripted model proves the Agent is never
asked. No network, no token, no model calls; the database is a scratch file."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from test_notify_notifier import FakeClock
from test_telegram import OWNER, STRANGER, FakeBot, FakeQuery, ScriptedLLM, message, say

from kestrel import tools
from kestrel.notify.parse import HELP as REMIND_FORMS
from kestrel.notify.parse import NO_TIME
from kestrel.notify.settings import NotifySettings
from kestrel.telegram.bot import HELP, REMINDER_HELP, TelegramBot, TelegramTracer
from kestrel.tools import ToolRegistry

ZONE = "America/New_York"


@pytest.fixture
def registry():
    return ToolRegistry()  # no tools: none of these tests should reach one


def make_core(tmp_path, llm, registry, *, on=True, clock=None):
    tracer = TelegramTracer(tmp_path / "traces.db", record_content=True, prices={})
    notify = NotifySettings(True, tmp_path / "notify.db", timedelta(hours=12), ZONE) if on else None
    return TelegramBot(
        OWNER,
        llm,
        tracer,
        tools=registry,
        audit_log=tmp_path / "approvals.jsonl",
        approval_timeout=10.0,
        notify=notify,
        clock=clock or FakeClock(),
    )


async def started(core):
    """Attach a fake bot and start the reminders' ticker as `started()` does (no greeting)."""
    bot = FakeBot()
    core.attach(bot, asyncio.get_running_loop())
    if core.reminders is not None:
        core.reminders.start(core.outbox)
        await asyncio.sleep(0)  # let the ticker's first tick run
    return bot


async def tick(core):
    await core.reminders.notifier.tick()


def reminder_message(bot):
    return next(m for m in reversed(bot.sent) if m.kwargs.get("reply_markup") is not None)


def data(card, label):
    row = card.kwargs["reply_markup"].inline_keyboard[0]
    return next(b.callback_data for b in row if b.text == label)


# --- off by default: nothing changes ------------------------------------------------------------


def test_with_notify_off_remind_gets_the_old_help_and_remind_me_goes_to_the_agent(tmp_path, registry):
    llm = ScriptedLLM(say("I can't set reminders."))

    async def go():
        core = make_core(tmp_path, llm, registry, on=False)
        bot = await started(core)
        await core.on_message(message("/remind 15:00 call"))
        await core.on_message(message("remind me at 3pm to call"))
        await asyncio.wait_for(core.chats[OWNER].task, 10)
        return core, bot

    core, bot = asyncio.run(go())
    assert core.reminders is None and not (tmp_path / "notify.db").exists()
    assert bot.texts()[0] == HELP and "/remind" not in HELP
    assert llm.seen[0][-1] == {"role": "user", "content": "remind me at 3pm to call"}


# --- commands -----------------------------------------------------------------------------------


def test_remind_reminders_and_cancel_never_reach_the_agent(tmp_path, registry):
    llm = ScriptedLLM()  # any model call would fail: there are no replies

    async def go():
        core = make_core(tmp_path, llm, registry)
        bot = await started(core)
        for text in (
            "/remind 15:00 call the dentist",
            "/remind in 20m to stretch",
            "/reminders",
            "/cancel 1",
            "/cancel 1",
            "/cancel x",
            "/remind",
            "/remind sometime call mum",
            "/help",
        ):
            await core.on_message(message(text))
        core.shutdown()
        return bot

    texts = asyncio.run(go()).texts()
    assert llm.seen == []
    assert texts[0].startswith("Reminder #1 set: Fri 13 Nov 15:00 (America/New_York): call the dentist\n")
    assert texts[0].endswith("Kestrel can only remind you while this laptop is awake and kestrel telegram is running.")
    assert texts[1].startswith("Reminder #2 set: Fri 13 Nov 14:20 (America/New_York): stretch")
    assert texts[2].splitlines()[:3] == [
        "Pending reminders (America/New_York):",
        "#2  Fri 13 Nov 14:20  stretch",
        "#1  Fri 13 Nov 15:00  call the dentist",
    ]
    assert texts[3] == "Cancelled #1: call the dentist"
    assert texts[4] == "No pending reminder #1. /reminders lists them."
    assert texts[5].startswith("Which one?")
    assert texts[6] == REMIND_FORMS
    assert texts[7] == NO_TIME
    assert texts[8] == f"{HELP}\n{REMINDER_HELP}"


@pytest.mark.parametrize(
    "text", ["remind me what we discussed about Sam", "Remind me at 3pm to call the dentist", "remind me"]
)
def test_with_notify_on_remind_me_text_goes_to_the_agent_unchanged(tmp_path, registry, text):
    """Only /remind creates reminders (owner decision 2026-11-13): plain "remind me ..." text is an
    ordinary message, exactly as with notify off, and no reminder is stored."""
    llm = ScriptedLLM(say("Here's what we discussed."))

    async def go():
        core = make_core(tmp_path, llm, registry)
        bot = await started(core)
        await core.on_message(message(text))
        await asyncio.wait_for(core.chats[OWNER].task, 10)
        pending = core.reminders.store.listing(OWNER)
        core.shutdown()
        return bot, pending

    bot, pending = asyncio.run(go())
    assert llm.seen[0][-1] == {"role": "user", "content": text}
    assert pending == [] and not any(t.startswith(("Reminder #", "I couldn't read")) for t in bot.texts())


def test_a_passed_time_says_tomorrow(tmp_path, registry):
    async def go():
        core = make_core(tmp_path, ScriptedLLM(), registry)
        bot = await started(core)
        await core.on_message(message("/remind 9:00 stretch"))
        core.shutdown()
        return bot

    text = asyncio.run(go()).texts()[0]
    assert text.startswith("Reminder #1 set: Sat 14 Nov 09:00") and "That's tomorrow" in text


def test_reminder_commands_work_while_a_run_is_in_progress(tmp_path, registry):
    async def go():
        core = make_core(tmp_path, ScriptedLLM(), registry)
        bot = await started(core)
        chat = core.new_chat(OWNER)
        chat.task = asyncio.get_running_loop().create_future()  # a run that hasn't finished
        await core.on_message(message("/remind in 5m stretch"))
        await core.on_message(message("what's the time?"))
        chat.task.cancel()
        core.shutdown()
        return bot

    texts = asyncio.run(go()).texts()
    assert texts[0].startswith("Reminder #1 set") and texts[1] == "Still working on your last message."


def test_strangers_and_forwarded_reminders_are_refused_as_before(tmp_path, registry):
    async def go():
        core = make_core(tmp_path, ScriptedLLM(), registry)
        bot = await started(core)
        await core.on_message(message("/remind in 5m x", user=STRANGER))
        await core.on_message(message("/remind in 5m x", forward=object()))
        await core.on_message(message("/reminders"))
        core.shutdown()
        return bot

    texts = asyncio.run(go()).texts()
    assert len(texts) == 2 and texts[1].startswith("No pending reminders.")


# --- delivery and buttons --------------------------------------------------------------------------


def test_a_due_reminder_arrives_as_plain_text_with_done_and_snooze(tmp_path, registry):
    clock = FakeClock()

    async def go():
        core = make_core(tmp_path, ScriptedLLM(), registry, clock=clock)
        bot = await started(core)
        await core.on_message(message("/remind in 5m stretch"))
        clock.advance(minutes=5)
        await tick(core)
        await tick(core)
        core.shutdown()
        return bot

    bot = asyncio.run(go())
    card = reminder_message(bot)
    assert card.text == "⏰ Reminder (you set this on Fri 13 Nov 14:00): stretch"
    assert [b.text for b in card.kwargs["reply_markup"].inline_keyboard[0]] == ["Done", "Snooze 10 min"]
    assert "parse_mode" not in card.kwargs and card.kwargs["link_preview_options"].is_disabled
    assert len(bot.cards()) == 1


def test_done_removes_the_buttons_and_a_second_press_does_nothing(tmp_path, registry):
    clock = FakeClock()

    async def go():
        core = make_core(tmp_path, ScriptedLLM(), registry, clock=clock)
        bot = await started(core)
        await core.on_message(message("/remind in 5m stretch"))
        clock.advance(minutes=5)
        await tick(core)
        card = reminder_message(bot)
        first, second = FakeQuery(data(card, "Done"), card), FakeQuery(data(card, "Done"), card)
        await core.on_callback(first)
        await core.on_callback(second)
        status = core.reminders.store.get(1).status
        core.shutdown()
        return bot, first, second, status

    bot, first, second, status = asyncio.run(go())
    assert first.answered and not second.answered and status == "done"
    assert len(bot.edits) == 1 and "reply_markup" not in bot.edits[0]
    assert bot.edits[0]["text"].endswith("stretch\n\nDone.")


def test_snooze_brings_it_back_ten_minutes_later_as_a_new_message(tmp_path, registry):
    clock = FakeClock()

    async def go():
        core = make_core(tmp_path, ScriptedLLM(), registry, clock=clock)
        bot = await started(core)
        await core.on_message(message("/remind in 5m stretch"))
        clock.advance(minutes=5)
        await tick(core)
        card = reminder_message(bot)
        await core.on_callback(FakeQuery(data(card, "Snooze 10 min"), card))
        clock.advance(minutes=9)
        await tick(core)
        before = len(bot.cards())
        clock.advance(minutes=1)
        await tick(core)
        again = reminder_message(bot)
        stale = FakeQuery(data(card, "Done"), card)  # the first message's button, after the snooze
        await core.on_callback(stale)
        core.shutdown()
        return bot, before, again, card, stale

    bot, before, again, card, stale = asyncio.run(go())
    assert bot.edits[0]["text"].endswith("Snoozed until Fri 13 Nov 14:15.")
    assert before == 1 and len(bot.cards()) == 2 and again.message_id != card.message_id
    assert not stale.answered and len(bot.edits) == 1


@pytest.mark.parametrize("forgery", ["stranger", "other chat", "unknown id", "not a number", "cancelled"])
def test_forged_or_foreign_presses_are_ignored_and_not_answered(tmp_path, registry, forgery):
    clock = FakeClock()

    async def go():
        core = make_core(tmp_path, ScriptedLLM(), registry, clock=clock)
        bot = await started(core)
        await core.on_message(message("/remind in 5m stretch"))
        clock.advance(minutes=5)
        await tick(core)
        card = reminder_message(bot)
        query = {
            "stranger": FakeQuery("rd:1", card, user=STRANGER),
            "other chat": FakeQuery("rd:1", card, chat_id=STRANGER),
            "unknown id": FakeQuery("rd:99", card),
            "not a number": FakeQuery("rd:1x", card),
            "cancelled": FakeQuery("rd:1", card),
        }[forgery]
        if forgery == "cancelled":
            core.reminders.store.set_status(1, "cancelled")
        await core.on_callback(query)
        status = core.reminders.store.get(1).status
        core.shutdown()
        return bot, query, status

    bot, query, status = asyncio.run(go())
    assert not query.answered and bot.edits == []
    assert status == ("cancelled" if forgery == "cancelled" else "sent")


# --- nothing tappable (design 5.2) ------------------------------------------------------------------

HOSTILE = "confirm at https://bank.example/x or evil.example, /stop, @kestrel_admin, +1 555 123 4567, a@b.example"


def test_reminder_text_is_never_tappable_wherever_it_is_shown(tmp_path, registry):
    """Confirmation, /reminders, the delivered reminder, the Done edit, /cancel and the missed list
    all show the defanged text; the store keeps the original."""
    from test_notify_defang import telegram_like_entities

    clock = FakeClock()

    async def go():
        core = make_core(tmp_path, ScriptedLLM(), registry, clock=clock)
        bot = await started(core)
        await core.on_message(message(f"/remind in 5m {HOSTILE}"))  # 1: delivered, then Done
        await core.on_message(message(f"/remind in 1d {HOSTILE}"))  # 2: cancelled
        await core.on_message(message(f"/remind in 30m {HOSTILE}"))  # 3: missed
        await core.on_message(message("/reminders"))
        await core.on_message(message("/cancel 2"))
        clock.advance(minutes=5)
        await tick(core)
        card = reminder_message(bot)
        await core.on_callback(FakeQuery(data(card, "Done"), card))
        clock.advance(hours=13)
        await tick(core)
        stored = core.reminders.store.get(1).text
        core.shutdown()
        return bot, stored

    bot, stored = asyncio.run(go())
    assert stored == HOSTILE
    shown = [*bot.texts(), *(e["text"] for e in bot.edits)]
    assert len(shown) == 8  # 3 confirmations, the listing, the cancel, the reminder, the Done edit, the missed list
    assert sum("bank[.]example" in t for t in shown) == 0  # the URL went whole
    for text in shown:
        reminder_part = text.replace("/cancel <number>", "")  # Kestrel's own help line in the listing
        assert telegram_like_entities(reminder_part) == set(), text
    assert any(t.startswith("Missed while Kestrel was off:") for t in shown)


class EntityBot(FakeBot):
    """Telegram accepted the message and found a bot command in it (a defang bug)."""

    async def send_message(self, chat_id, text, **kwargs):
        sent = await super().send_message(chat_id, text, **kwargs)
        sent.entities = [SimpleNamespace(type="bot_command")]
        return sent


def test_a_delivered_reminder_with_a_tappable_entity_is_logged_as_a_defang_bug(tmp_path, registry, caplog):
    clock = FakeClock()

    async def go():
        core = make_core(tmp_path, ScriptedLLM(), registry, clock=clock)
        core.attach(EntityBot(), asyncio.get_running_loop())
        core.reminders.start(core.outbox)
        await core.on_message(message("/remind in 5m stretch"))
        clock.advance(minutes=5)
        await tick(core)
        core.shutdown()

    asyncio.run(go())
    assert "a reminder went out with tappable bot_command: defang bug" in caplog.text


def test_approval_card_buttons_still_go_to_the_approver(tmp_path, registry):
    """A press that isn't rd:/rs: is the approver's, as before (here: no waiting card, so ignored)."""

    async def go():
        core = make_core(tmp_path, ScriptedLLM(), registry)
        await started(core)
        card = SimpleNamespace(message_id=1, chat=SimpleNamespace(id=OWNER))
        query = FakeQuery("a:deadbeef", card)
        await core.on_callback(query)
        core.shutdown()
        return query

    assert not asyncio.run(go()).answered


def test_shutdown_stops_the_ticker(tmp_path, registry):
    async def go():
        core = make_core(tmp_path, ScriptedLLM(), registry)
        await started(core)
        task = core.reminders.task
        core.shutdown()
        await asyncio.sleep(0)
        return task

    assert asyncio.run(go()).cancelled()


# --- the agent is unchanged ---------------------------------------------------------------------------


def test_reminders_leave_the_agent_fingerprint_unchanged(monkeypatch):
    """Reminders are bot commands, not tools: importing them registers nothing, and the main-suite
    agent's fingerprint, tools hash, system prompt hash and tool names are main's (design 8.1)."""
    import importlib

    from kestrel.bench.runner import agent_fingerprint

    before = sorted(tools.registry.tools)
    for module in (
        "notify",
        "notify.clock",
        "notify.settings",
        "notify.parse",
        "notify.store",
        "notify.notifier",
        "telegram.reminders",
        "telegram.bot",
    ):
        importlib.import_module(f"kestrel.{module}")
    assert sorted(tools.registry.tools) == before and len(before) == 10
    monkeypatch.delenv("KESTREL_REQUEST_TIMEOUT", raising=False)
    fingerprint = agent_fingerprint("groq", "openai/gpt-oss-120b")
    assert fingerprint["sha"] == "8a8174d8f17d"
    assert fingerprint["tools"] == "ede3efa38c54"
    assert fingerprint["system_prompt"] == "36d57c7e8a82"


def test_kestrel_telegram_refuses_bad_notify_settings_before_connecting(monkeypatch):
    from kestrel import run_telegram

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", str(OWNER))
    monkeypatch.setenv("KESTREL_NOTIFY", "on")
    monkeypatch.setenv("KESTREL_NOTIFY_STAGES", "email")
    with pytest.raises(SystemExit) as e:
        run_telegram(SimpleNamespace(provider="demo", model=None, no_mcp=True))
    assert "isn't built yet" in str(e.value)
