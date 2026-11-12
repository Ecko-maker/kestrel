"""Telegram as a channel: type to Kestrel from your phone and answer its approval cards there.

Telegram is a front end, like the terminal and the web console: the Agent, the ApprovalGate and the
rules are unchanged (docs/telegram-google-design.md, sections 1-5, and its owner decisions of
2026-11-11). Long polling only, one allowed user, full cards, no session approval, forwarded
messages refused.

    settings.py   TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_USER_ID, checked at startup
    outbox.py     sends to Telegram from the bot's loop or the Agent's worker thread; flood waits
    text.py       the card text and splitting long messages
    approver.py   TelegramApprover (the Approver interface): a card with Approve / Reject
    events.py     TelegramEvents: few messages, final answers only, never tool results
    bot.py        the access rules, one Agent per chat, commands, and the python-telegram-bot wiring

Needs the optional extra: `uv sync --extra telegram`. Importing this package registers no tool.
"""
