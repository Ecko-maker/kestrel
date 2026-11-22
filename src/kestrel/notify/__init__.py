"""Proactive notifications: stage 1, reminders the owner sets, and stage 2, calendar alerts
(docs/notifications-design.md, sections 2-3; docs/telegram.md).

Outside the agent loop: nothing here is a tool, nothing here calls a model, and nothing here puts
text into an Agent's conversation. The agent fingerprint is unchanged (8a8174d8f17d).

    clock.py      the wall clock (UTC), replaceable by a fake one in tests
    settings.py   KESTREL_NOTIFY and friends, checked at startup; off by default
    parse.py      "/remind 15:00 call the dentist" -> a due time in UTC; refuses anything it can't read
    store.py      logs/notify.db: reminders, deliveries (unique dedupe key), the last tick
    notifier.py   the ticker: due reminders out, late ones labelled, old ones in one "missed" list
    calendar.py   stage 2: the strict get_events parser, the skip rules, quiet hours, the watcher
    google_source.py  the notifier's own read-only workspace-mcp (its own token folder, get_events only)
    signin.py     `kestrel notify signin`: that token's one-time sign-in, printing counts only

No Telegram import: the channel (kestrel.telegram.reminders) gives the notifier a send function.
"""
