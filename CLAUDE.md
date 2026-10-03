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
- [ ] Step 4: approval gate for risky tools

## Configuration (.env)

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY`, `GROQ_API_KEY` | provider keys |
| `KESTREL_PROVIDERS` | fallback chain, e.g. `gemini,groq,ollama` (default `gemini`); `--provider` pins one |
| `<PROVIDER>_MODEL` | override a provider's default model, e.g. `OLLAMA_MODEL=qwen2.5:0.5b` |
| `KESTREL_MAX_CONTEXT_TOKENS` | history budget (default 16000) |
| `KESTREL_REQUEST_TIMEOUT` | seconds per model request (default 60) |

## File layout

```
kestrel/
├── .env                 # API keys (never committed, never opened by Claude)
├── .env.example         # variable names only, no values
├── .gitignore
├── .vscode/launch.json  # F5 runs Kestrel (Gemini or Groq)
├── CLAUDE.md
├── KESTREL_PROJECT.md
├── pyproject.toml
└── src/kestrel/
    ├── __init__.py      # CLI + chat loop
    ├── __main__.py      # lets `python -m kestrel` run it
    ├── llm.py           # LLM (retries, fail-fast), FallbackLLM, build_llm()
    ├── tools.py         # @tool registry: schemas, arg validation, timeout, truncation + tools
    └── agent.py         # Agent loop: parallel tools, history trimming, AgentResult
tests/                   # pytest, no network or keys: `uv run pytest`
workspace/               # the only folder file tools can touch; only notes.txt is tracked
```
