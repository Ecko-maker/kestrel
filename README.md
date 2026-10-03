# Kestrel

A personal AI agent that runs on free tools: it calls tools, recovers from failures, asks before it changes anything, and records every request as a trace.

![Approval card for an email in the Kestrel console](docs/screenshots/chat-approval-email.png)

## Run it

Requires Python with [uv](https://docs.astral.sh/uv/) and, for the web console, [Node.js](https://nodejs.org).

```powershell
copy .env.example .env      # then put your keys in .env (GEMINI_API_KEY, GROQ_API_KEY)
uv run kestrel              # chat in the terminal
uv run kestrel web --build  # build the web console once, then serve it
uv run kestrel web          # serve it; open the link it prints
```

`kestrel web` listens on `127.0.0.1` only. The link it prints contains a random access token; without it, every page, API call and WebSocket is refused.

Other commands: `kestrel traces`, `kestrel trace <id>`, `kestrel stats`, `kestrel export --rated good`, `uv run kestrel-mcp` (Kestrel's safe tools as an MCP server).

## Developing the console

The frontend lives in `console/` (React, Vite, TypeScript, Tailwind). For hot reload, run the backend and the Vite dev server side by side:

```powershell
uv run kestrel web          # terminal 1: backend on 127.0.0.1:8765, prints a token
cd console
npm install                 # first time only
npm run dev                 # terminal 2: open http://127.0.0.1:5173/?token=<token from terminal 1>
```

Vite proxies `/api` and `/ws` to the backend, so the browser talks to one origin. `npm run typecheck` checks types; `npm run build` (or `kestrel web --build`) writes `console/dist`, which `kestrel web` serves.

To see the console without API keys, `uv run python scripts/demo_console.py` runs it with a scripted model (real tools, approvals and tracing) on a temporary workspace.

## Screenshots

| | |
|---|---|
| ![Prompt injection stopped at the approval gate](docs/screenshots/chat-approval-injection.png) | ![Trace waterfall](docs/screenshots/trace-waterfall.png) |
| ![Live agent trace under an answer](docs/screenshots/chat-agent-trace.png) | ![Stats page](docs/screenshots/stats.png) |

## Tests

```powershell
uv run pytest
```
