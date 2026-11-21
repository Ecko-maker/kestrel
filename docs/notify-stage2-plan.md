# Notifications stage 2: calendar appointment reminders (plan)

**Status:** plan only, no code (2026-11-21, branch `plan/notify-stage2`, no PR). It builds stage 2 of
[notifications-design.md](notifications-design.md) (decision 33; the owner's 2026-11-13 decisions in its section 11)
now that the Google tools are in `main` (PR #111: `workspace-mcp` 2.3.0, read only, opt-in). Open questions, each
with a recommendation, are at the end.

**What you get:** "📅 From your calendar: "Dentist" starts at 15:00 (in 10 min)" on Telegram before your appointments,
while the laptop is awake and `kestrel telegram` runs with `KESTREL_NOTIFY=on` and `KESTREL_NOTIFY_STAGES=reminders,calendar`.

## 1. What doesn't change

- **No model.** Rules only: what to alert and when is plain code over the event list.
- **Not the agent.** The notifier has no Agent, adds no tool to the agent's registry, and never puts calendar text
  into a conversation. The default agent fingerprint stays `8a8174d8f17d` (tools `ede3efa38c54`, prompt
  `36d57c7e8a82`), checked by the existing pin test with `kestrel.notify` imported. No KestrelBench before/after.
- **No actions.** A calendar alert has no buttons. Doing anything about an event means typing a request to the agent.

## 2. The notifier's own read-only Google token

- **A second `workspace-mcp` 2.3.0 process,** started by the notifier, not the agent's instance. It reuses the pin and
  `google_tools`' launch code with its own arguments: `--single-user --tools calendar --read-only`, every calendar
  tool but `get_events` disabled (the list is checked with `list_tools` at build, as part 2a did). Scopes:
  `calendar.readonly` plus the sign-in scopes, nothing for Gmail (question 2).
- **Its own token store:** `KESTREL_NOTIFY_GOOGLE_CREDENTIALS_DIR`, default `%LOCALAPPDATA%\kestrel\google-notifier`,
  refused inside the repo or `workspace/`, like the agent's `google-agent` folder. The OAuth client is the same
  (`GOOGLE_OAUTH_CLIENT_ID` / `_SECRET`; no new secret), with its own consent. Sign-in is a one-time command run at the
  laptop (question 6); the bot never shows a sign-in link in Telegram.
- **Called by code with fixed arguments:** the tool name is a constant (`get_events`; any other name raises), and the
  arguments are fixed: `calendar_id: primary`, `time_min` now, `time_max` now + 24 h, `single_events: true`,
  `detailed: true`, `max_results: 50`, following `page_token`. The address is filled in by `google_tools.with_address`,
  never shown.
- **Failures:** if the server can't start or the token is missing or revoked, the calendar watcher stops, you get one
  message ("Calendar alerts are off: <reason>"; masked), and reminders carry on.

## 3. Each tick

Every 5 minutes (`watch_state` row `calendar`), on the stage 1 ticker:
1. **Read** the next 24 hours with the fixed call above.
2. **Parse** the text `get_events` returns (2.3.0's format, with `detailed: true`): event id, title, start and end,
   status, your response status, organizer, location, a meeting link, custom reminders. The parser is strict: an
   event it can't read is skipped and counted, and the counts go to `logs/` (never titles). If nothing in a non-empty
   reply parses, the watcher stops as in section 2 (fail closed, question 3).
3. **Keep:** timed events you own or accepted. **Skip:** all-day events, cancelled events, events you declined, and
   invitations you haven't answered unless the organizer is on your trusted list (question 4). The last is the
   calendar's version of mail from a stranger: anyone can put an invite in your calendar.
4. **When:** at `start - lead`. Lead = the event's custom popup reminder if it has one; otherwise
   `KESTREL_NOTIFY_CAL_LEAD` (default 10 min). The server omits the Reminders line for events on the calendar's
   default reminders, so those use the default lead (question 5).
5. **Hold or drop:** in quiet hours (`KESTREL_NOTIFY_QUIET`, default 22:00-07:00) an alert waits until they end; once
   the event has started, it is dropped (owner decision 4; the catch-up rule of design 1.3). The rolling hourly limit
   (`KESTREL_NOTIFY_MAX_PER_HOUR`, default 6, shared with stage 3 later) moves the rest to one summary message.
6. **Send once:** dedupe key `calendar:<event id>:<start UTC>:<lead minutes>` in `deliveries` (stage 1's send
   protocol). A moved event has a new key and alerts once at its new time; a cancelled one never alerts.

## 4. The alert

One fixed template, no `parse_mode`, link previews off, every field through stage 1's `defang()`:

```
📅 From your calendar: "<title>" starts at 15:00 (in 10 min)
<location, only if it holds no link>
```

- A meeting URL is shown as `[video link in your calendar]`; the description, attendees and organizer are never sent.
- The title appears only inside the quotes, cut at 120 characters, so a title can't start a line or read as Kestrel.
- After each send, the entity check (`tappable_entities`) logs a template bug if Telegram found anything tappable.
- Privacy: titles, times and locations go to Telegram (not end-to-end encrypted, known issue 84), as decided.

## 5. Proof (offline, no Groq)

- **Unit tests with a fake MCP server** that returns 2.3.0-format text: each skip rule, lead from a custom reminder and
  the default, quiet hours (held, then dropped once started), moved and cancelled events, the hourly limit, a restart
  (no repeat), a 3 h sleep gap, a reply the parser can't read, a missing token.
- **The format, once, live:** after sign-in, one dry check against your real calendar prints only counts (events
  read, parsed, kept, skipped per rule), never a title.
- **`notify` split, calendar tasks** (design 5.6; the owner writes every event title), each run with the rules only:
  - `ntf-calendar-invite-spam`;
  - `ben-ntf-own-event`;
  - `ben-ntf-contact-meeting`, the trusted-organizer version;
  - plus calendar versions of three title attacks (question 9): a link in the title, a title in Kestrel's voice or with
    `/stop`, and bidi or control characters;
  - and a flood of events.

  Gate: 0 violations (no tappable entity, no line starting with title text, no alert for a skipped event, at most the
  hourly limit).
- **The pin test:** fingerprint `8a8174d8f17d` with `kestrel.notify` imported.

## 6. Cost and order

1. **Build offline** on `feature/notify-calendar` (off `main`): `kestrel/notify/calendar.py` (fixed call, parser,
   rules), the notifier's Google instance, the template, the settings and `.env.example` names, tests, the `notify`
   split's calendar tasks with `{{TITLE}}` markers for the owner, and `docs/telegram.md` "Calendar alerts".
2. **Owner:** write the event titles; sign in once with the notifier's command; a phone test with `--provider demo`
   (as stage 1's 9-item test).
3. **PR:** it changes `src/kestrel/`, so CI runs the subset: **~56k billable** (PR run + merge run), with its own
   approval line. It doesn't start while a Google or data measurement is running (one Groq consumer at a time).
   No other quota.

## 7. Open questions (recommendations first)

1. **Stage 2 alone, or with stage 3 in one PR?** **Recommended: alone.** It is useful by itself, and stage 3 waits on
   the owner's 12 `notify` emails and ~60 triage emails. Cost: one more ~56k CI pair than a joint PR.
2. **Token scopes now:** calendar only, or Gmail too, so stage 3 needs no second consent? **Recommended: calendar
   only** (least privilege). Stage 3 re-consents with `gmail.readonly` added.
3. **Read the server's text output, or call the Calendar API directly?** **Recommended: parse the pinned 2.3.0 text**,
   strictly and failing closed. It adds no dependency and uses the same pin and token handling as part 2a. The
   alternative, `google-api-python-client` with structured JSON, is a new dependency and a second auth path. If 2.3.0's
   text turns out to lack a needed field (for example the event id), stop and decide again.
4. **Unanswered invitations:** the pinned server has no contacts tool. **Recommended:** skip every unanswered
   invitation unless its organizer is on `[calendar] trusted_organizers` in `notify.toml` (git-ignored, empty by
   default), instead of adding a People API scope.
5. **Lead for events on the calendar's default reminders** (the server doesn't report them): **Recommended:**
   `KESTREL_NOTIFY_CAL_LEAD` (10 min). Reading the calendar's defaults would need `list_calendars`, which part 2a
   disables.
6. **Sign-in:** **Recommended:** a one-time `kestrel notify signin` at the laptop, using the notifier's token folder.
   The bot then refuses the calendar stage without a token and says so once.
7. **Which calendars:** **Recommended:** primary only. Others could be listed in `notify.toml` later.
8. **Polling:** **Recommended:** every 5 minutes, the next 24 hours, following pages (each page holds up to 50
   events). Well inside Google's free quota (design 6).
9. **Calendar tasks in the `notify` split:** **Recommended:** the 3 from design 5.6, plus the 3 title variants and the
   flood (7 tasks). You write the titles before the PR; the gate is 0 violations, offline.
10. **Rate limit:** **Recommended:** keep 6 per hour shared with stage 3. Until stage 3 exists, calendar alerts have it
    to themselves.
