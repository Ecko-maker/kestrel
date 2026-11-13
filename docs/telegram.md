# Kestrel on Telegram

Type to Kestrel from your phone and answer its approval cards with buttons. Telegram is a channel,
like the terminal and the web console: the same Agent, the same approval rules, the same traces.
Design: [telegram-google-design.md](telegram-google-design.md) (sections 1-5 and the owner decisions of
2026-11-11); decision 32.

## Set it up (once)

**1. Install the extra** (python-telegram-bot 22.8, under 1 MB):

```bash
uv sync --extra telegram
```

**2. Create the bot with @BotFather**, in any Telegram app:
1. Search for **@BotFather** (it has a blue check mark) and open the chat. Press **Start**.
2. Send `/newbot`.
3. Send a display name, e.g. `Kestrel`.
4. Send a username. It must end in `bot` and be unused, e.g. `noah_kestrel_bot`.
5. BotFather replies with the **token**: digits, a colon, then about 35 letters and digits. It is a password for
   the bot: anyone with it can read the bot's messages. Don't paste it anywhere but `.env`.
6. Send `/setjoingroups`, pick your bot, choose **Disable**, so the bot can't be added to groups. (Kestrel ignores
   groups anyway; this is a second lock.)
7. Open your bot (the `t.me/<username>` link BotFather gave you) and press **Start**. A bot can't message you
   until you have.

If the token ever leaks: BotFather `/revoke`, pick the bot, and put the new token in `.env`.

**3. Find your numeric user id.** It is a number (like `123456789`), not your @username. Kestrel shows it to you:
1. Add both lines to `.env` (below) with `TELEGRAM_ALLOWED_USER_ID=1` for now. 1 is nobody's account, so the
   bot answers no one yet.
2. Run `uv run kestrel telegram`. It can't greet user 1, so it prints
   `[telegram] couldn't send the start message to TELEGRAM_ALLOWED_USER_ID: BadRequest: Chat not found (check ...)`
   and `Still polling.`: expected here, it keeps running. Send your bot any message.
3. The terminal prints `[telegram] ignored update from user id 123456789`: that number is yours. Only the id is
   logged, never your text.
4. Stop Kestrel (Ctrl+C), put the number in `.env`, and start it again.

**4. The two `.env` lines:**

```
TELEGRAM_BOT_TOKEN=<the token @BotFather gave you>
TELEGRAM_ALLOWED_USER_ID=<your numeric user id>
```

Without both, `kestrel telegram` stops with a message naming them. It never falls back to "anyone".

## Use it

```bash
uv run kestrel telegram                    # the KESTREL_PROVIDERS chain, as in the terminal
uv run kestrel --provider demo telegram    # demo mode: no API keys
```

It polls Telegram (no webhook, no open port, nothing exposed from your laptop). Messages you sent while it was off
are skipped. Ctrl+C stops it. A message Telegram refuses (a start message, an answer, a card) is logged with
Telegram's reason and never stops the bot; a card that can't be delivered counts as **Reject**. If the start
message fails with your real id, check `TELEGRAM_ALLOWED_USER_ID` and that you pressed **Start** in the bot's chat. Only one `kestrel telegram` can run on this machine (`logs/telegram.lock`).

**In the chat:**
- Type a request. You get the final answer, and one short line per step with the tools used (`🔧 read_file ✓`),
  never their results.
- A risky action shows a **card**: why it appeared, any address you never typed (marked ⚠), local text found in an
  outgoing call, and the full preview. A preview too long for one message comes first as `preview.txt`.
  - **Approve** or **Reject**. Every card asks: there is no "approve for this session" on Telegram.
  - **Reply** to the card with text to reject it with a reason. Kestrel sees the reason.
  - No answer in 5 minutes = rejected: the card ends "Expired: no answer in 5 min, not done". The model may then
    propose the same action again on a new card, with its own 5 minutes (known issue
    #88).
- `/new` starts a new conversation (the only way to clear "local data was read" for network calls).
- `/stop` rejects a waiting card and stops after the current step.
- `/good`, `/bad [note]` rate the last answer, as in the terminal.

**What it ignores:** other users, groups, and chats that aren't your private chat with the bot (no reply at all).
**What it refuses:** forwarded messages (paste the part you mean as your own words: Kestrel trusts what you type,
so a forwarded email must not count as you), and anything that isn't text.

## Reminders (optional, off by default)

Kestrel can remind you at a time you set (stage 1 of `docs/notifications-design.md`). Add to `.env`:

```
KESTREL_NOTIFY=on
```

Optional: `KESTREL_NOTIFY_TZ=America/New_York` (default: this laptop's zone), `KESTREL_NOTIFY_DB` (default
`logs/notify.db`), `KESTREL_NOTIFY_LATE_GRACE` (default `12h`). `kestrel telegram` checks them at start and names
the line to fix. With `KESTREL_NOTIFY` unset or `off`, the bot behaves exactly as without reminders.

**Set one:**

| Type | Due |
|---|---|
| `/remind 15:00 call the dentist` | today 15:00, or tomorrow if 15:00 has passed (the reply says so) |
| `/remind 3pm call the dentist` | the same (`3:30pm`, `3 pm` too) |
| `/remind in 20m stretch` | in 20 minutes (`m`, `h`, `d`; `min`, `hours`, `days` too) |
| `/remind tomorrow 9:00 send the form` | tomorrow 09:00 |
| `/remind 2026-11-20 09:30 renew passport` | that date and time |

`/remind at 3pm to call the dentist` reads too ("at" and "to" are optional). Anything else gets "I couldn't read the
time. Try: /remind 15:00 <text>", never a guess; `/remind` alone shows the forms. The reply shows the time, the time
zone and the reminder's number.

**Only `/remind` sets a reminder.** A plain message such as "remind me what we discussed about Sam" goes to the
model as any other message, exactly as with reminders off. The model has no reminder tool, so it can't set one, even
if its answer says it will (known issue `KI-pending-agent-claims-reminder`). On a day the clocks change, a time that doesn't exist (02:30 when they go forward) is refused, and
one that happens twice (when they go back) is the first, with a note. Limits: 300 characters, 100 pending, a year
ahead.

**Manage them:** `/reminders` lists pending ones with their numbers; `/cancel <number>` cancels one. These work even
while Kestrel is busy with a request: no model is involved.

**When it's due** you get `⏰ Reminder (you set this on Fri 13 Nov 14:02): <your text>` with **Done** and
**Snooze 10 min**. A press removes the buttons. Snooze sends it again 10 minutes later as a new message, and the old
message's buttons stop working. Reminders also fire during quiet hours: you picked the time.

**Only while this laptop is awake and `kestrel telegram` is running.** Asleep (Modern Standby), hibernating or off,
nothing arrives on time. When Kestrel runs again:
- reminders up to 12 hours late arrive with `Late: due 15:00 (the laptop was asleep or Kestrel was off)`;
- older ones arrive in one message, `Missed while Kestrel was off:`, with each one's time and text.

If Telegram refuses a reminder 3 times, `/reminders` shows it as NOT DELIVERED and the console log says why. If
Kestrel stopped while sending one, it is sent once more at the next start, marked
`(may be a repeat: Kestrel restarted while sending)`.

**Nothing in a reminder is tappable.** Telegram apps turn links, domains, email addresses, `/commands`, `@names` and
phone numbers into taps even in plain text, and a tappable `/stop` would send that command as you. So wherever a
reminder's text is shown (the reply, `/reminders`, `/cancel`, the reminder itself, the missed list) it is defanged:
links become `[link removed]`, dots in domains `[.]`, `/` before a word `∕`, `@` `＠`, and phone-like numbers are
regrouped in pairs (`+1 555 123 4567` -> `15 55 12 34 56 7`). Dates and times stay as typed; `logs/notify.db` keeps
your original text. If Telegram still finds a tappable part in a delivered reminder, the console logs
`a reminder went out with tappable ...: defang bug`.

Reminders are not a tool: the model can't create, read or cancel one, and only your own typed words become a
reminder's text. Everything is in `logs/notify.db` (git-ignored).

## Privacy

**Telegram bot chats are not end-to-end encrypted.** Everything below passes through and is stored by Telegram:
- your prompts and Kestrel's answers;
- the one-line tool status;
- every approval card: the preview (a message body, a file diff) and local text found in an outgoing call;
- your reminders' text and times, if reminders are on.

Tool results, tool arguments (except on cards), traces, the audit log and `.env` never go to Telegram.
`KESTREL_TRACE_CONTENT=off` only stops Kestrel keeping text locally; Telegram and the model provider still have it.
Traces record the channel (`kestrel.channel = telegram`), not your user or chat id.

## Tests

`uv run pytest tests/test_telegram.py`: a fake bot and fake updates, no network, no token, no model calls. They
cover strangers, groups, forwarded and quoted messages, presses from another user and on old cards, the timeout,
no session approval, splitting long answers, the gate overruling a buggy approver, a demo-mode run end to end, and
the agent fingerprint pinned unchanged (`8a8174d8f17d`).

Reminders: `uv run pytest tests/test_notify_parse.py tests/test_notify_notifier.py tests/test_notify_defang.py
tests/test_telegram_reminders.py`, with a fake clock and the same fake bot. They cover every form and refusal, DST
in two zones, a restart, catch-up after a 3-hour and a 14-hour gap, no double send from two ticks, a crash between
send and mark, 3 failed sends, forged and stale button presses, "remind me ..." text reaching the model unchanged,
nothing tappable in any message showing a reminder's text, notify off changing nothing, and the fingerprint pinned
again after importing the reminder modules.
