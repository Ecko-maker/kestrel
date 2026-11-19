# Google tools (opt-in): Gmail and Calendar, read only

Kestrel can search your Gmail, read a message and read your calendar, through the
[`workspace-mcp`](https://github.com/taylorwilsdon/google_workspace_mcp) MCP server, pinned to **2.3.0** (MIT).

- **Plan:** [google-step-plan.md](google-step-plan.md), decision `D-pending-google-step`. Its decision rule is frozen
  there.
- **Not measured yet:** until the plan's runs are done and its outcome is PASS or not shown, these tools are
  **built but not shipped** (step 2a).
- **Off by default.** Without the opt-in, Kestrel is byte for byte the default agent (fingerprint
  `8a8174d8f17d`, 10 tools), pinned by `tests/test_google_tools.py`.

## What's opt-in, and what it gives the model

Turn it on per run with `uv run kestrel --tools google ...`, or always with `KESTREL_TOOLS=google` in `.env`. It works
in every front end: the terminal, `web` and `telegram`.

**The model gets exactly 3 tools:**

| Tool | What it does |
|---|---|
| `google__search_gmail_messages` | search your mail with Gmail's query syntax |
| `google__get_gmail_message_content` | read one message |
| `google__get_events` | read your calendar (a time range, a keyword or one event) |

**How they're restricted:**
- **Read only, twice over:**
  - the server runs with `--read-only`, which requests only `gmail.readonly` and `calendar.readonly` (plus sign-in);
  - every other Gmail and Calendar tool is disabled on the server, and Kestrel registers only these 3 names.

  A server upgrade that changes a tool's schema is refused until the pin is checked again.
- **Every call is a card** (owner decision Q7): no "approve for this session" in any front end.
- **Untrusted and tainting.** What comes back is wrapped as untrusted data. After a read, any network call needs a
  card unless you typed its URL, query or address yourself. Anyone can send you an email, so email is an attacker's
  best way in.
- **Your address stays out of it.** The model never sees the `user_google_email` parameter. Kestrel fills it in
  from `USER_GOOGLE_EMAIL` after the approval card. It also replaces your address with `[your Google account]` in
  every result and error. Tests check that the address never reaches the model's messages, a trace, a card or the
  audit log.

**Not included: sending, drafts, labels, Calendar create and Drive.** They need write access, and at 2.3.0 the
Calendar create tool is the same tool that edits and deletes. They would come back as their own measured step.

## Set it up (once, free)

**1. In the [Google Cloud console](https://console.cloud.google.com/)**, signed in with the account Kestrel should
read:
1. **Project:** the project picker, then **New project**, named e.g. `kestrel`. No billing account.
2. **APIs:** APIs & Services, then Library. Enable **Gmail API** and **Google Calendar API**. Nothing else.
3. **Google Auth Platform:**
   - **Branding:** app name `Kestrel`, your email as support and developer contact.
   - **Audience:** **External**, status **Testing**, and add **your own account** as the only test user.
   - **Data access:** add exactly `https://www.googleapis.com/auth/gmail.readonly`,
     `https://www.googleapis.com/auth/calendar.readonly`, `openid`, `.../auth/userinfo.email` and
     `.../auth/userinfo.profile`. **Not** `gmail.send`, `gmail.modify`, `gmail.compose`, `calendar`,
     `calendar.events` or any Drive scope.
4. **Clients:** **Create client**, type **Desktop app**, name `kestrel-agent`.
   - A Desktop client accepts the server's local sign-in redirect (`http://localhost:8000/oauth2callback`) with
     nothing to register.
   - Copy the client ID and secret into `.env` (step 2).
   - If you download the JSON, keep it outside this folder (e.g. `%USERPROFILE%\.kestrel\`). `.gitignore` also
     refuses `client_secret*.json`.
5. Leave the "AI-powered agent" client setting alone: workspace-mcp calls Google's REST APIs directly and doesn't
   need it.

**2. `.env` lines** (names in `.env.example`):

```
GOOGLE_OAUTH_CLIENT_ID=<the client ID>
GOOGLE_OAUTH_CLIENT_SECRET=<the client secret>
USER_GOOGLE_EMAIL=<the Google account's address>
KESTREL_TOOLS=google            # optional: on for every run
```

**Optional settings:**
- `KESTREL_GOOGLE_CREDENTIALS_DIR`: where your sign-in tokens go. Default `%LOCALAPPDATA%\kestrel\google-agent`.
  Kestrel refuses a folder inside the project or the workspace.
- `KESTREL_GOOGLE_COMMAND`: an installed `workspace-mcp` 2.3.0 executable. Without it Kestrel runs
  `uvx --python 3.13 workspace-mcp==2.3.0`, and the first start downloads it (~190 MB installed; measured
  2026-11-17).

**3. First use:** `uv run kestrel --tools google`, then ask about your mail. The first call opens a browser for
Google's consent screen on the computer Kestrel runs on (the server opens it; the model, traces and cards get the
answer with the link removed, so the console and Telegram can't show it). It must list only reading your email,
viewing your calendars and your basic profile; if it asks for anything more, stop and report it. If Google shows a
box per permission, tick both Gmail and Calendar: a grant without them is saved but asked for again on the next
call. Use the one tab the server opened: a second tab of the same link fails with "invalid or expired state". The
tokens are saved in the credentials folder. While the app is in Testing, Google may make you consent again after 7
days (known issue `KI-pending-google-testing-consent`).

Missing lines stop Kestrel before anything starts, with a message naming them. It never prints their values.

## Privacy

- **What goes where:**
  - Google sees what Google already holds.
  - Your model provider (e.g. Groq) sees the mail and events the model reads: they are in the conversation.
  - On Telegram, cards show each call's arguments (a search query, a message ID).
- **What stays local:**
  - **Traces** keep tool results unless `KESTREL_TRACE_CONTENT=off`: email text read in a conversation ends up in
    `logs/traces.db` (git-ignored).
  - **Distillation** never trains on these traces. `kestrel distill build`'s `google_tool` filter drops any trace
    that called a Google tool or was tainted by one, and every later trace in that session, even with
    `KESTREL_TRACE_CONTENT=off` (it reads the spans).
  - **The server's own log** (`logs/mcp-google.log`, git-ignored) contains your address, because workspace-mcp
    logs it. Kestrel can't filter it (known issue `KI-pending-google-server-log`).
  - **Tokens** live in the credentials folder, outside the project, and are never in git.

## For the bench (no Google, no keys)

- **Stubs:** `kestrel bench run --tools google` registers the 3 tools as stubs: the same names, descriptions and
  schemas (the pinned copy, `src/kestrel/google_tools_schemas.json`) and the same gating.
- **Deny by default:** a stub answers only from the task's fixtures and otherwise fails. Every call is logged.
- **Email tasks:** the safety split's 16 email tasks (s1.1) run only with `--tools google`, and only once their
  emails are written (`kestrel bench validate --split safety` lists them as TODO).
- **Not leak channels:** Google calls don't count as ways out. A Gmail query goes to your own account, not to an
  attacker (known issue `KI-pending-google-leak-channels`).
- **Re-pinning:** a new pin means listing the tools from the new server, replacing the JSON, and re-checking every
  item in the plan's section 1.1.
