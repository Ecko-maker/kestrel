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
- Before committing: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`; frontend: `cd console; npm run typecheck; npm run lint; npm run build`. Pre-commit runs ruff + gitleaks.
- Known limitations live in docs/known-issues.md; decisions in docs/design-decisions.md. Keep both current.
- The Bash tool turns `\n` escapes inside heredocs into real line breaks: write multi-line edit scripts with the Write tool.
- Never open, print, or edit `.env`. It holds the user's API keys. If a key is missing, say exactly what line to add. To diagnose a key, print only yes/no facts (set, length, right prefix), never characters of it.
- Free tools only unless the user says otherwise.
- After each step: run it, fix any errors, then give a suggested git commit message and one sentence on what the step shows an interviewer.
- Free-tier limits shape testing: Gemini `gemini-3.6-flash` allows 5 requests/min and **20/day**; Groq `gpt-oss-120b` 8,000 tokens/min (~1,000 requests/day). Heavy runs (evals) go to Groq.
- While Claude Code runs the `kestrel` MCP server, Windows locks `.venv/Scripts/kestrel-mcp.exe` and `uv sync` fails to replace it: use `uv run --no-sync`, and `uv add --no-sync <pkg>` then `uv sync --inexact --no-install-project` for new dependencies.
- Never change a KestrelBench task just so a model passes it; a task changes only if it is wrong (bad regex, ambiguous prompt), and the commit says why.

## CI rules

- Never merge with red CI, including Dependabot PRs. Changes go through a branch + PR; read failures with `gh run view <id> --log-failed`, fix the root cause.
- Never skip, weaken or delete a test or a CI check to get green; ask the user first if a check seems wrong.
- Pin actions and images to exact versions (`astral-sh/setup-uv@v10.2.0`, `node:24-slim`); check a tag exists with an exact match (GitHub's refs API matches prefixes). Dependabot keeps pins current; base-image runtime upgrades are done by hand with CI's versions.

## Environment

- Windows laptop, VS Code, PowerShell, `uv`, Git + GitHub (`origin` = github.com/Ecko-maker/kestrel, branches `dev` and `main`).
- Free providers: Gemini API (free tier), Groq (free tier, ~1,000 requests/day), Ollama (local).
- Free GPUs for fine-tuning: Kaggle (~30 h/week), Google Colab.

## Roadmap (about 12 weeks at ~15 h/week)

| Phase | Weeks | Contents | Gate to move on |
|---|---|---|---|
| 1 Foundation ✅ | 1–2 | Model layer, agent loop with tool calling, approval gate, 3 MCP tools, web console, tracing, Docker | Does one real task daily |
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
- [x] Step 8: release v0.1.0. Demo provider (`demo.py`, `KESTREL_PROVIDERS=demo`, labelled in UI, never exported as training data). Docker: multi-stage `Dockerfile` (Node builds console, slim Python + uv, non-root UID 10001, `/healthz` healthcheck), `.dockerignore`, `docker-compose.yml` (127.0.0.1:8000 only, server on 0.0.0.0 inside, `host.docker.internal` for Ollama). Settings `KESTREL_HOST/PORT/TOKEN/ALLOWED_HOSTS/MCP`, `<PROVIDER>_BASE_URL`. CI `.github/workflows/ci.yml`: Python (ubuntu + windows: ruff, format, mypy, pytest+cov, ResourceWarning as error), console (typecheck, oxlint, build), Docker (compose up in demo mode, health, 401, non-root), gitleaks CLI on full history. Dependabot (uv, npm, actions, docker). pre-commit (ruff, gitleaks) installed. Fixed while doing this: sqlite connections never closed, MCP event loop never closed, audit log not redacted, sandbox treated `\` differently on Linux. README v1, LICENSE (MIT), docs/design-decisions.md, docs/known-issues.md (20 items). Docker not run locally (not installed); verified by CI only.

**Phase 1 complete (v0.1.0).**

### Phase 2: Evals
- [x] KestrelBench v1: 100 YAML tasks in 10 categories (`evals/kestrelbench/tasks/`), fixture workspace with 5 injection traps (`evals/kestrelbench/workspace/`), `src/kestrel/bench/` (tasks, checks, runner with scripted approver, judge, report, calibrate, cli), `kestrel bench run|label|calibrate|report`. Deterministic checks first; gpt-oss-120b judge on Groq for 43 rubric tasks. Bench traces in `logs/bench.db`. Guide: `docs/kestrelbench.md`.
- [x] Evals in CI: `kestrelbench` job runs the 16-task `ci` subset on Groq for PRs and main (needs `GROQ_API_KEY` repo secret; skips without it), gate 75% pass rate (baseline 88%), max 2 errors. Weekly full run: `.github/workflows/kestrelbench.yml`.
- [x] Eval statistics (#22 partly): `bench/stats.py` bootstrap CI (10k resamples, seed 2026) in every report, per category with a <10-task flag; `kestrel bench compare A B` (paired bootstrap, flips, non-inferiority margin); `run --repeat N --sample N --tasks --reuse --dry-run`, stop after 3 errors in a row (rate limits) + resume per (task, repeat). Results record `repeat`, `tool_log`, `task_sha`, judge `version`. 3x20 repeats ≈ 57k billable tokens (fits one Groq day), not run yet.
- [x] Calibration tooling (#21 open): `label` (pass/fail/skip + note, verdict hidden, tty only, `evals/labels/human.jsonl`, stratified, resumable), `calibrate` (held-out by default, dev for tuning; kappa, lenient/strict confusion, per category, all disagreements; `--judge-model` re-grades stored answers, cached), `rejudge` (new file, never re-runs Kestrel), `JUDGE_VERSION` pinned by a test. `tests/conftest.py` keeps tests off the real labels file. Only the owner labels.
- [ ] Judge calibration: owner labels both halves (20+ each), report held-out agreement + kappa; then decide on `qwen/qwen3.8-27b` as an independent judge (compare on dev first).
- [x] Baseline in README: **KestrelBench v1.1 92% (95% CI 86-97%, n=100)**, the v1.0 run's answers re-scored with v1.1 checks (`scripts/rescore.py`); v1.0 scored 85% (7 check bugs, `evals/CHANGELOG.md`). Judge not calibrated. Fresh v1.1 run `evals/results/baseline-v1.1.json` at 57/100 (91%), stopped by Groq's rolling daily limit: finish with `--resume evals/results/baseline-v1.1.json --out <new file>`, then replace the README number.
- [x] Baseline reviews (`evals/reports/`): failure analysis (6 genuine: asks for workspace info x2, skips calculator x2, asks before sending, narrow reading; 2 judge cases), false-pass review of 15 seeded passes (0 check false passes, judge lenient twice; judge sees only 600 chars of tool results), hard-split proposal (25 tasks, NOT added; waiting for approval).
- [x] Judge v2 (2,000-char tool results, `JUDGE_VERSION` pinned with the cut length): baseline re-graded from stored answers -> 92% (95% CI 86-97%, n=100), 3 scores changed, 0 pass/fail changed. Final files: `evals/results/baseline-v1.1-judge-v2-final.json`, `run2-v1.1-judge-v2-final.json`; report `evals/reports/baseline-v1.1.md`.
- [x] Run-to-run (#22 partly): run 2 vs run 1, same agent (verified from traces) -> +1 point (95% CI -6 to +8, n=87), 10% of tasks flip (`evals/reports/run-to-run.md`). Run 2's last 13 tasks pending Groq quota (job at 03:15 UTC 2026-10-06).
- [x] Safety rails: `bench/lock.py` (one process per results file, `kestrel bench unlock` checks the PID), agent fingerprint in results meta, `scripts/check_results.py`, `scripts/audit_rate_limits.py`, `rejudge --only-missing`. Before scheduling a job, check `ps`; report job state with evidence (process list / final log line).
- Groq free tier: the token budget is a rolling ~24 h window (usage frees ~24 h after it was spent); the request count resets at 00:00 UTC.
- [ ] Waiting for the owner: leak-check fix for `sandbox-parent-dir` (CHANGELOG "Proposed", 4 strict xfail tests), `act-email-priya-actions` prompt ambiguity, hard split; then label for #21.
- Suite versioning: `SUITE_VERSION` in `bench/tasks.py` (now 1.1), recorded in results meta; bump + log in `evals/CHANGELOG.md` for any task/check/fixture change.

### Phase 3: Memory and safety (plan: docs/phase3-plan.md)
- [x] Safety split harness, no tasks yet (`SAFETY_VERSION` s1.0): `bench/safety.py` (format: kind, vector, goals, expect, canaries, pages/search/mcp stubs; loader validates args vs match; Clopper-Pearson for every safety rate), `bench/stubs.py` (stubbed web_search/fetch__fetch/MCP log every call; real network tools refused; users strict / rubber-stamp), `bench/guards.py` (goals + always-on guards at model and system level), `bench/scripted.py` (always-falls / never-falls / refuse-all). `bench run --split safety [--scripted M] [--user U] [--split-dir D]`. Placeholders in `tests/fixtures/safety/`. Format in `docs/kestrelbench.md`. Canary pieces min 6 (measured). `ddgs` uses primp (Rust): socket patching alone can't block it (known issue #24).
- [x] Gate decided: 0 system-level attack successes under the strict user (model-level and rubber-stamp reported). `--user both`; undeclared gated/forbidden calls listed for review (#23); `bench/validate.py` + `kestrel bench validate --split safety` (schema, unique per-task canaries, none in the shared workspace, pairing, fixtures visible to git, scripted-model behavior); `bench/safety_report.py` (console + .md, HARNESS CHECK / SMOKE TEST labels, gate 'not assessed' for them). Tests: no subprocess/MCP in a safety run; main registry pinned to 5b1d900 (`tests/frozen/main_registry_5b1d900.json`). Smoke-run on scripted models, demo and Ollama qwen2.5:0.5b (placeholders only). Dry-run for 30+18 tasks: strict ~51k billable, both users ~101k; main suite ~112k.
- [x] Recipient guard (any send outside allowed_recipients counts; #23 shrunk), fetches of unserved hosts listed as undeclared, prompt addresses must be in allowed_recipients (validate). `{{INJECTION}}` / `{{INJECTION: spec}}` markers: validate prints them as TODO lines and exits 1 (INCOMPLETE); a real-model safety run refuses while any remain. Claude may write safety task structure with {{INJECTION}} markers; the attack text is written only by the owner. Benign look-alikes are re-checked against the final attacks. Owner review of each attack's goals, canary and allowed recipients: `docs/safety-worksheet.md`.
- [ ] Owner writes the attack and benign tasks (canaries in each task's own `files`) and runs validate; then a smoke run on Ollama, then gpt-oss-120b on Groq (main suite and safety split on separate days).

- [x] Live provider verification (2026-10-03/04, known issues #1 and #3 closed). Six demo prompts pass on Groq and on Gemini; fallback, a Gemini → Groq → Gemini conversation (thought-signature placeholder accepted) and the web console on Gemini verified. Rerun with `scripts/live/`. Default Gemini model is now `gemini-3.6-flash` (measured). Fixed: rate limits (Gemini RetryInfo body, guided-wait budget, longer wait for the last provider), Gemini 400 bad-key message, gpt-oss drafting instead of calling tools (system prompt + rejection message). Prices verified and sourced in `prices.toml` (#3 closed).

## Configuration (.env)

Also: `KESTREL_HOST`, `KESTREL_PORT`, `KESTREL_TOKEN`, `KESTREL_ALLOWED_HOSTS` (web/Docker), `KESTREL_MCP=off`, `<PROVIDER>_BASE_URL` (e.g. `OLLAMA_BASE_URL`). Provider `demo` needs no key.

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
