# Kestrel on Telegram

Type to Kestrel from your phone and answer its approval cards with buttons. Telegram is a channel,
like the terminal and the web console: the same Agent, the same approval rules, the same traces.
Design: [telegram-google-design.md](telegram-google-design.md) (sections 1-5 and the owner decisions of
2026-11-11); decision `D-pending-telegram-channel`.

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
  - No answer in 5 minutes = rejected.
- `/new` starts a new conversation (the only way to clear "local data was read" for network calls).
- `/stop` rejects a waiting card and stops after the current step.
- `/good`, `/bad [note]` rate the last answer, as in the terminal.

**What it ignores:** other users, groups, and chats that aren't your private chat with the bot (no reply at all).
**What it refuses:** forwarded messages (paste the part you mean as your own words: Kestrel trusts what you type,
so a forwarded email must not count as you), and anything that isn't text.

## Privacy

**Telegram bot chats are not end-to-end encrypted.** Everything below passes through and is stored by Telegram:
- your prompts and Kestrel's answers;
- the one-line tool status;
- every approval card: the preview (a message body, a file diff) and local text found in an outgoing call.

Tool results, tool arguments (except on cards), traces, the audit log and `.env` never go to Telegram.
`KESTREL_TRACE_CONTENT=off` only stops Kestrel keeping text locally; Telegram and the model provider still have it.
Traces record the channel (`kestrel.channel = telegram`), not your user or chat id.

## Tests

`uv run pytest tests/test_telegram.py`: a fake bot and fake updates, no network, no token, no model calls. They
cover strangers, groups, forwarded and quoted messages, presses from another user and on old cards, the timeout,
no session approval, splitting long answers, the gate overruling a buggy approver, a demo-mode run end to end, and
the agent fingerprint pinned unchanged (`8a8174d8f17d`).
