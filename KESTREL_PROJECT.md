# Kestrel — Project Brief

Upload this file to the Claude Project's knowledge. Update the "Progress" section after each step and re-upload it, so every new chat knows where the build stands.

## What Kestrel is

A voice-first personal AI agent, built as a flagship portfolio project for AI/LLM engineering roles.

**Tagline:** Hunts on its own. Comes back when called. Gets sharper every night.

**Design idea:** the best of three Marvel AIs, without the rogue part:
- **Jarvis's control:** a human approves every risky action (send, delete, pay, post).
- **Friday's autonomy:** handles long, multi-step tasks on its own with tools, memory, and sub-agents.
- **Ultron's self-improvement, made safe:** nightly, successful traces train a small model; it deploys only if it beats the current model on KestrelBench and passes every safety test.

**Headline result to prove:** a fine-tuned small open model handles most requests at near-frontier quality, at a fraction of the cost and latency.

## Key decisions (do not change without discussing)

1. **$0 budget first.** Every feature must work on free tools. Paid services are optional upgrades that improve speed or quality, never required for a feature.
2. **Provider-agnostic model layer.** All code calls `llm.complete()`; providers (Gemini, Groq, Ollama, later Claude) are a config choice.
3. **Distillation teacher = open-weight model** (e.g., gpt-oss-120b or a large Qwen via Groq's free tier). Never train on outputs from Claude, GPT, or Gemini, whose terms restrict it.
4. **Own agent loop**, no LangChain. Tools exposed through MCP.
5. **Evals from early on** (KestrelBench), run in CI.
6. **Secrets in `.env` only**, never committed.

## Environment

- Windows laptop, VS Code, PowerShell, `uv` for Python, Git + GitHub.
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

- [x] Step 1: model layer + chat loop (`src/kestrel/llm.py`, `src/kestrel/__init__.py`)
- [ ] Step 2: tool calling

## Current file layout

```
kestrel/
├── .env                 # API keys (never committed)
├── .gitignore
├── .vscode/launch.json  # F5 runs Kestrel
├── pyproject.toml
└── src/kestrel/
    ├── __init__.py      # CLI + chat loop
    ├── __main__.py      # lets `python -m kestrel` run it
    └── llm.py           # provider-agnostic LLM interface
```

## How I want help in this project

- Guided build: one step at a time, explain the why, I write and run the code.
- Code that runs on Windows + PowerShell.
- After each step: what it shows an interviewer, and a suggested git commit message.
