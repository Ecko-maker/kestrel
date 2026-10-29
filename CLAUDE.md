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
- Known limitations live in docs/known-issues.md; decisions in docs/design-decisions.md. Keep both current. On a feature branch, a new decision or issue is named `D-pending-<slug>` / `KI-pending-<slug>`; `main` gives it a number when the branch merges (pending names and today's clashes: docs/STATUS.md).
- The Bash tool turns `\n` escapes inside heredocs into real line breaks: write multi-line edit scripts with the Write tool.
- Never open, print, or edit `.env`. It holds the user's API keys. If a key is missing, say exactly what line to add. To diagnose a key, print only yes/no facts (set, length, right prefix), never characters of it.
- Free tools only unless the user says otherwise.
- After each step: run it, fix any errors, then give a suggested git commit message and one sentence on what the step shows an interviewer.
- Free-tier limits shape testing: Gemini `gemini-3.6-flash` allows 5 requests/min and **20/day**; Groq `openai/gpt-oss-120b` 30 requests/min, 1,000 requests/day, 8,000 tokens/min, **200,000 tokens/day**, per organization; cached input tokens don't count (subtracted after processing, so parallel bursts can still hit TPM) (console.groq.com/docs/rate-limits, checked 2026-10-06). Heavy runs (evals) go to Groq.
- API quota needs the owner's OK each time: no `kestrel bench run` (except `--dry-run`), rejudge, `calibrate --judge-model`, `scripts/live/`, or chat/web on a real provider without it. Past grants and the runs they covered are in docs/progress.md (the Phase 3 before side was allowed on 2026-10-15). A PR with the `run-evals` label or agent-affecting changes runs the CI subset on Groq, so that counts too: about 56k per agent-affecting PR (the PR run plus the merge-to-main run), and again on every push to an open PR.
- Ask before any download over 100 MB (models, tools and their caches included), with the estimated size; afterwards report the actual size and where it went.
- Owner decisions (2026-10-06):
  - **D1** Main suite frozen at v1.1 for Phase 3 comparisons; v1.2 changes batched until after the Phase 3 gate.
  - **D2** CI evals run only when needed (agent-affecting paths or the `run-evals` label) and fail loudly if needed but the key is missing.
  - **D3** Weekly full run is manual-only during Phase 3 (`gh workflow run kestrelbench.yml`); re-enable the cron after the gate.
  - **D4** One labelled CI-subset run on the pin-baselines PR to prove the gate end to end. Done on PR #11, run 37504156469: 81% (13/16), gate passed, 0 errors, 27.7k billable tokens, fingerprint `cc5c16377662`.
- Eval trigger rule (`scripts/ci/evals_needed.py`): needed when a PR has the `run-evals` label, or a PR / push to main changes `src/kestrel/`, `evals/kestrelbench/tasks/` or `evals/kestrelbench/workspace/`. Dependency files, docs, tests, console, the safety split and Dependabot PRs don't trigger it; the manual full run covers dependencies. Not needed = "KestrelBench not run" in the job summary and a pass. Changing the gate itself (ci.yml): add the label to prove it.
- While Claude Code runs the `kestrel` MCP server, Windows locks `.venv/Scripts/kestrel-mcp.exe` and `uv sync` fails to replace it: use `uv run --no-sync`, and `uv add --no-sync <pkg>` then `uv sync --inexact --no-install-project` for new dependencies.
- Never change a KestrelBench task just so a model passes it; a task changes only if it is wrong (bad regex, ambiguous prompt), and the commit says why.

## CI rules

- Never merge with red CI, including Dependabot PRs. Changes go through a branch + PR; read failures with `gh run view <id> --log-failed`, fix the root cause.
- Never skip, weaken or delete a test or a CI check to get green; ask the user first if a check seems wrong.
- Pin actions and images to exact versions (`astral-sh/setup-uv@v10.2.0`, `node:24-slim`); check a tag exists with an exact match (GitHub's refs API matches prefixes). Dependabot keeps pins current; base-image runtime upgrades are done by hand with CI's versions.

## Environment

- Windows laptop, VS Code, PowerShell, `uv`, Git + GitHub (`origin` = github.com/Ecko-maker/kestrel, branches `dev` and `main`).
- Development happens here: one VS Code window on this folder, one Claude tab at a time. `.env` is present in this folder. Which branch holds which work: docs/STATUS.md.
- Measurements (any `kestrel bench run` on a real provider, i.e. every Groq run) happen only in `..\kestrel-runs`, never from VS Code: a standalone PowerShell window there, started from the Start menu, so closing VS Code or a Claude tab can't stop it. `..\kestrel-runs` is the only git worktree, detached at a tag or commit, with its own `.venv`, created or moved by `.\scripts\runs_folder.ps1 <tag-or-commit>`. The script never creates, copies or reads `.env`; the owner copies it in by hand (check only that it exists). Never edit files in the runs folder: move it to a new commit instead. Long runs use `--wait-for-quota`. While a run is live, keep that PowerShell window open (closing it stops the run) and the laptop on mains power and awake (runs pause in Modern Standby).
- Free providers: Gemini API (free tier), Groq (free tier, 1,000 requests and 200k tokens/day for gpt-oss-120b), Ollama (local).
- Free GPUs for fine-tuning: Kaggle (~30 h/week), Google Colab.

## Roadmap (about 12 weeks at ~15 h/week)

| Phase | Weeks | Contents | Gate to move on |
|---|---|---|---|
| 1 Foundation | 1–2 | Model layer, agent loop with tool calling, approval gate, 3 MCP tools, web console, tracing, Docker | Does one real task daily |
| 2 Evals | 3–4 | KestrelBench v1 (100 tasks), judge calibration, evals in CI | Baseline score in README |
| 3 Memory and safety | 5–6 | Long-term memory + hybrid RAG, permission tiers, prompt-injection suite | 0% attack success on suite |
| 4 Distillation | 7–9 | Trace dataset, QLoRA fine-tune on free GPUs, serving, router, cost curve | Routed beats frontier on cost at ≥95% quality |
| 5 Voice and launch | 10–12 | Streaming voice + barge-in, demo video, 2 MCP servers, blog posts | Repo, video, posts public |

Where each phase stands, the live run, branches, Groq calendar and open owner decisions: [docs/STATUS.md](docs/STATUS.md).

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

History, step by step (Phases 1-2 from `main`, Phase 3 tiers v2 merged with `phase3/tiers-v2`, the rest from the branches, marked not merged): [docs/progress.md](docs/progress.md).

## Configuration (.env)

Also: `KESTREL_HOST`, `KESTREL_PORT`, `KESTREL_TOKEN`, `KESTREL_ALLOWED_HOSTS` (web/Docker), `KESTREL_MCP=off`, `KESTREL_IMAGE_ALLOWLIST` / `KESTREL_LINKS_AUTOLOAD` / `KESTREL_IMAGE_POLICY` (answer-URL policy; the console reads the allowlist; `KESTREL_IMAGE_POLICY=allowlist` only for tiers-v2 after-runs), `<PROVIDER>_BASE_URL` (e.g. `OLLAMA_BASE_URL`). Provider `demo` needs no key.

Other branches add their own (memory: `KESTREL_MEMORY_DB`; phase4: router settings; voice: wake and talk settings); see each branch's `.env.example`.

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
├── docs/STATUS.md      # where things stand (changed only by docs PRs to main)
├── docs/progress.md    # history, step by step
├── docs/screenshots/
├── scripts/demo_console.py  # console demo with a scripted model (no keys needed)
├── KESTREL_PROJECT.md
├── pyproject.toml
└── src/kestrel/
    ├── __init__.py      # CLI + chat loop
    ├── __main__.py      # lets `python -m kestrel` run it
    ├── llm.py           # LLM (retries, fail-fast), FallbackLLM, build_llm()
    ├── tools.py         # @tool registry: schemas, risk tiers, validation, timeout, truncation + tools
    ├── approval.py      # Approver interface, TerminalApprover, ApprovalGate + audit log, card notices
    ├── permissions.py   # tiers v2: capabilities, Session (taint), required_approval()
    ├── egress.py        # tiers v2: outbound-content check
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

`..\kestrel-runs\` (outside this folder): the runs worktree; its `evals/results/` and `logs/` are git-ignored and backed up by hand (docs/STATUS.md, "Data outside git").

## Session routine

- **Start of every session:** read docs/STATUS.md, run `git status` and `git branch --show-current`, then tell the owner in 5 lines where we are, which branch this session works on, and the next step. Work on one branch per session.
- **End of every session:**
  1. Run the checks from Working rules.
  2. Commit, and push the branch.
  3. Write a 3-line STATUS update for the owner to apply via a docs PR.
  4. List anything not saved in git.
- **Feature branches never edit docs/STATUS.md or CLAUDE.md's rules.** When a branch merges, its CLAUDE.md changes are moved into main's docs by hand (progress into docs/progress.md, rules into CLAUDE.md, pending decision and issue names numbered then).
