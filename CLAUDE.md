# Kestrel

A voice-first personal AI agent, built step by step as a flagship portfolio project for AI/LLM engineering roles. Full brief: [KESTREL_PROJECT.md](KESTREL_PROJECT.md).

**Tagline:** Hunts on its own. Comes back when called. Gets sharper every night.

**Design idea:** the best of three Marvel AIs, without the rogue part:
- **Jarvis's control:** a human approves every risky action (send, delete, pay, post).
- **Friday's autonomy:** long, multi-step tasks on its own with tools, memory, and sub-agents.
- **Ultron's self-improvement, made safe:** nightly, successful traces train a small model; it deploys only if it beats the current model on KestrelBench and passes every safety test.

**Headline result to prove:** a fine-tuned small open model handles most requests at near-frontier quality, at a fraction of the cost and latency.

## Key decisions (do not change without discussing)

1. **$0 budget first.** Every feature must work on free tools. Paid services are optional upgrades, never required.
2. **Provider-agnostic model layer.** All code calls `llm.complete()`; providers (Gemini, Groq, Ollama, later Claude) are a config choice.
3. **Distillation teacher = open-weight model** (e.g. gpt-oss-120b or a large Qwen via Groq's free tier). Never train on outputs from Claude, GPT, or Gemini.
4. **Own agent loop**, no LangChain. Tools exposed through MCP.
5. **Evals from early on** (KestrelBench), run in CI.
6. **Secrets in `.env` only**, never committed.

## Working rules

- The user is learning: before writing code, give a short plan; after, explain what each new piece does and why, in plain language.
- Windows + PowerShell. Use `uv` for everything (`uv add`, `uv run`). Run tests with `uv run pytest`.
- Before using a library API, check the installed version's actual API (e.g. `mcp` 2.x renamed FastMCP to MCPServer).
- Frontend: `cd console; npm run typecheck; npm run build`. The Bash tool mangles `
` inside heredocs: write multi-line edit scripts with the Write tool.
- Never open, print, or edit `.env`. It holds the user's API keys. If a key is missing, say exactly what line to add.
- Free tools only unless the user says otherwise.
- After each step: run it, fix any errors, then give a suggested git commit message and one sentence on what the step shows an interviewer.

## Environment

- Windows laptop, VS Code, PowerShell, `uv`, Git + GitHub (`origin` = github.com/Ecko-maker/kestrel, branches `dev` and `main`).
- Free providers: Gemini API (free tier), Groq (free tier, ~1,000 requests/day), Ollama (local).
- Free GPUs for fine-tuning: Kaggle (~30 h/week), Google Colab.

## Roadmap (about 12 weeks at ~15 h/week)

| Phase | Weeks | Contents | Gate to move on |
|---|---|---|---|
| 1 Foundation | 1–2 | Model layer, agent loop with tool calling, approval gate, 3 MCP tools, web console, tracing, Docker | Does one real task daily |
| 2 Evals | 3–4 | KestrelBench v1 (100 tasks), judge calibration, evals in CI | Baseline score in README |
| 3 Memory and safety | 5–6 | Long-term memory + hybrid RAG, permission tiers, prompt-injection suite | 0% attack success on suite |
| 4 Distillation | 7–9 | Trace dataset, QLoRA fine-tune on free GPUs, serving, router, cost curve | Routed beats frontier on cost at ≥95% quality |
| 5 Voice and launch | 10–12 | Streaming voice + barge-in, demo video, 2 MCP servers, blog posts | Repo, video, posts public |

### Phase 1 steps
1. Model layer + chat loop (provider switch: gemini / groq / ollama)
2. Tool calling: the model can run Python functions (time, files, web search)
3. Agent loop: multi-step plans, retries, step limit
4. Approval gate for risky tools
5. Tracing: every step logged with tokens, latency, cost
6. MCP: tools exposed and consumed through MCP
7. Web console (first version)
8. Docker + first tests in CI

## Progress

- [x] Step 1: model layer + chat loop (`src/kestrel/llm.py`, `src/kestrel/__init__.py`, `src/kestrel/__main__.py`, `.vscode/launch.json`). Chat loop verified with Ollama; live Gemini call waits on `GEMINI_API_KEY` in `.env`.
- [x] Step 2: tool calling (`tools.py` registry + 5 tools, `agent.py` loop with max_steps, `LLM.chat()`, `--debug`, 34 pytest tests). Verified end-to-end on Ollama; live Gemini/Groq runs wait on `GEMINI_API_KEY` / `GROQ_API_KEY` in `.env`. Lesson: small local models (deepseek-r1:8b) may claim tool use without calling tools; the `[tool]` trace exposes it.
- [x] Step 3: robust loop. Retries with backoff + Retry-After, fail-fast on 401/403/404/400, `FallbackLLM` chain (`KESTREL_PROVIDERS`) with 60s cooldown, argument validation, 20s tool timeout, 8,000-char result cap, parallel tool calls, history trimmed by whole turns, `AgentResult` (answered / max_steps / error). 71 tests. Verified live: real Groq 401 -> fallback to Ollama; unknown model -> clean error. Retries/outages covered by tests only.
- [x] Step 4: approval gate. Risk tiers fixed in code (`@tool(risk="safe"|"confirm"|"forbidden")`, frozen `Tool`), action tools (write_file, append_to_file, create_note, send_message simulated to workspace/outbox/, delete_file forbidden), `approval.py` (`Approver` interface + `TerminalApprover` with diff/message previews, approve/edit/reject/session; `ApprovalGate` owns the rules + audit log `logs/approvals.jsonl`). File/web results wrapped as `<untrusted_data>`; system prompt treats tool output as data. Injection demo: `workspace/suspicious_email.txt`. 99 tests. Demoed with a scripted model + real gate/approver (injected send_message shown and rejected); real-model run waits on cloud keys (qwen2.5:0.5b too weak to call tools).
- [x] Step 5: tracing. `tracing.py` (traces of nested spans agent_run > llm_call / tool_call > approval, OTel GenAI attribute names, SQLite `logs/traces.db`, redaction of key-like strings + secret env values, `KESTREL_TRACE_CONTENT=off`), `pricing.py` + `prices.toml` (actual vs list price per model), `/good` `/bad [note]` ratings, `kestrel traces | trace <id> | stats | export` (`trace_report.py`). Export is chat JSONL with `weight: 0` on earlier turns so only the rated turn is trained on. 115 tests. Live on Ollama qwen2.5:0.5b: 6 traces incl. web search and a rejected approval. List prices unverified and only shown for Gemini/Groq models.
- [x] Step 6: MCP (official `mcp` SDK 2.x: FastMCP is now `MCPServer`, fields are snake_case). Client `mcp_client.py`: servers from `kestrel.mcp.json` (Claude-style `mcpServers` + Kestrel `safe_tools` allowlist), one asyncio loop on a background thread (Agent stays sync for now), tools registered as `<server>__<tool>`, external = confirm unless allowlisted, annotations shown but never lower the tier, results and error messages wrapped as untrusted, dead servers' tools hidden. Servers: `mcp-server-fetch` (fetch__fetch safe) and `mcp-server-time` (confirm), pinned to 2026.8.18; kept our own get_current_time/web_search. Server `mcp_server.py` (`uv run kestrel-mcp`): exposes only safe tools + create_note through the registry; `.mcp.json` lets Claude Code use it. 129 tests. Live: fetch of modelcontextprotocol.io on Ollama, and kestrel-mcp called over real stdio.
- [x] Step 7: web console. Agent emits typed events (`on_event`: step_started, llm_call, text_delta, tool_call, tool_result, answer, error, done; WebApprover adds approval_required/approval_resolved) consumed by both the terminal (`TerminalEvents`) and the browser. `LLM.chat(on_text=)` streams tokens (falls back to non-streaming if rejected or if a streamed reply comes back empty, seen with Ollama). Backend `src/kestrel/web/` (FastAPI): WebSocket chat with one Agent per connection on a worker thread, `WebApprover` (5 min timeout, disconnect = reject), REST traces/stats/rating reusing `trace_report` query functions, 127.0.0.1 only + startup token -> HttpOnly SameSite=Strict cookie, Host check, WS Origin check, CORS only for Vite dev. Frontend `console/` (React 19, Vite 8, TS 7, Tailwind 4, react-markdown, lucide-react; hand-made SVG charts). `uv run kestrel web [--build]`. 146 tests. Browser demo via `scripts/demo_console.py --shots` (scripted model, real everything else; Playwright + installed Edge) -> `docs/screenshots/`. Traces now carry `session_id` (one conversation).
- [ ] Step 8: Docker + first tests in CI

## Configuration (.env)

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY`, `GROQ_API_KEY` | provider keys |
| `KESTREL_PROVIDERS` | fallback chain, e.g. `gemini,groq,ollama` (default `gemini`); `--provider` pins one |
| `<PROVIDER>_MODEL` | override a provider's default model, e.g. `OLLAMA_MODEL=qwen2.5:0.5b` |
| `KESTREL_MAX_CONTEXT_TOKENS` | history budget (default 16000) |
| `KESTREL_REQUEST_TIMEOUT` | seconds per model request (default 60) |
| `KESTREL_TRACE_CONTENT` | `off` = record timings and token counts only, no message text |
| `KESTREL_PRICES` | path to a custom prices.toml |
| `KESTREL_WORKSPACE` | workspace folder (default `./workspace`; kestrel-mcp defaults to the project's) |

## File layout

```
kestrel/
├── .env                 # API keys (never committed, never opened by Claude)
├── .env.example         # variable names only, no values
├── .gitignore
├── .vscode/launch.json  # F5 runs Kestrel (Gemini or Groq)
├── .mcp.json           # lets Claude Code use kestrel-mcp
├── CLAUDE.md
├── kestrel.mcp.json    # MCP servers Kestrel uses + safe_tools allowlist
├── console/            # web console frontend (npm run dev / build -> console/dist)
├── docs/screenshots/
├── scripts/demo_console.py  # console demo with a scripted model (no keys needed)
├── KESTREL_PROJECT.md
├── pyproject.toml
└── src/kestrel/
    ├── __init__.py      # CLI + chat loop
    ├── __main__.py      # lets `python -m kestrel` run it
    ├── llm.py           # LLM (retries, fail-fast), FallbackLLM, build_llm()
    ├── tools.py         # @tool registry: schemas, risk tiers, validation, timeout, truncation + tools
    ├── approval.py      # Approver interface, TerminalApprover, ApprovalGate + audit log
    ├── tracing.py       # Tracer/Span, SQLite storage, redaction
    ├── trace_report.py  # kestrel traces / trace / stats / export
    ├── pricing.py       # cost from prices.toml (actual vs list price)
    ├── mcp_client.py    # MCPManager: external MCP tools into the registry
    ├── mcp_server.py    # kestrel-mcp: Kestrel's safe tools over MCP
    └── web/             # FastAPI app (app.py) + WebApprover (approver.py)
    └── agent.py         # Agent loop: parallel tools, history trimming, AgentResult
tests/                   # pytest, no network or keys: `uv run pytest`
workspace/               # the only folder file tools can touch; notes.txt + suspicious_email.txt tracked
logs/                    # approvals.jsonl, traces.db, mcp-<server>.log (git-ignored)
data/                    # exported training JSONL (git-ignored)
```
