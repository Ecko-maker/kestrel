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
- Ask before any download over 100 MB (models, tools and their caches included), with the estimated size; afterwards report the actual size and where it went.
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

**Roadmap change (owner decision 2026-10-26, option B of `docs/memory-rag-design.md`):**
- **Phase 3's memory item** (long-term memory + hybrid RAG) **moves after Phase 4.**
- **Phase 3 closes on tiers v2.1:** the safety gate (passed: strict system-level 0/29), the decision on rule A, and the rule C main-suite verdict. Then the merge.
- **Phase 4 order:**
  1. Phase 3 merge;
  2. teacher decision (#72);
  3. rebase `phase4/trace-dataset`;
  4. collection;
  5. training;
  6. measurement.
- **Router rule:** until a student is retrained with the memory tools, every routed setup runs with memory off (known issue #78; `docs/phase4-plan.md`). "Retrain with memory" is the planned follow-up.
- **Parked branches, not merged until after the Phase 4 measurements:** `phase3/memory-rag` (design), `phase3/memory`, `phase3/memory-evals`, `phase3/search-spike`.

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

## Phase 4: Distillation

Branch `phase4/trace-dataset` (off main; no PR yet, so no CI evals). Plan steps below; the Phase 4 gate is in the roadmap.

- [x] Step 1, trace dataset (`ae420c5`): `kestrel distill build --db <copy> [--results ...] --out data/distill` (`src/kestrel/distill/dataset.py`). Filters in order, each counted in `manifest.json`: no_messages, teacher (`src/kestrel/distill/teachers.toml`, default groq + openai/gpt-oss-120b; the loader refuses Gemini/Claude/GPT/demo, no override flag), contamination (any KestrelBench run, any user turn equal to a bench prompt), quality (answered, not /bad; /good or a passed distill task), secrets (drop, never redact: KCAN canaries incl. encoded, redact() patterns and [REDACTED], .env lines, secret env values), too_long (8,000, never truncated), duplicate. Records via `trace_report.export_record` (one formatter). Split by session hash (salt 2026). Refuses the live `logs/*.db`. Byte-identical rebuilds. Design decision 22; known issues 66-69.
- [x] Step 2, distill task set: `distill/tasks` (120 tasks, `d1.0`, tag `distill`, ids `dst-`; main-suite category shares x1.2; over-weights workspace lookup 12, calculator 14, gated tool 12; no injection text) + `distill/workspace` (garden and bike co-op, example domains only, disjoint from the bench workspace). `kestrel bench run --split distill [--split-dir D]` (traces to `logs/distill.db`, meta `split`/`split_version`/`task_dir`/`workspace_dir`); main runs unchanged apart from `"split": "main"` (dry run and 100 task fingerprints identical, agent fingerprint `cc5c16377662`). `kestrel distill build` takes a results file as distill only if `meta.split == "distill"` and every id starts with `dst-`; anything else is KestrelBench. Overlap check `scripts/distill_overlap.py` (`src/kestrel/distill/overlap.py`): exact or 3-gram Jaccard >= 0.5 fails, >= 0.3 review; 0 / 0 against main and the Phase 3 safety split. Design decision 23.
- [x] Step 3, overlap thresholds + grading policy: thresholds from the bench's own pair distribution without twins and same-task pairs (fail Jaccard >= 0.23 or a 5-word run, review >= 0.08 or 4 words; report `evals/reports/distill-overlap.md`: 0 fail, 38 review, 0 fixture problems; `dst-web-uv-tool` reworded, set now `d1.1`). Safety split read from the commit pinned in `src/kestrel/distill/bench_sources.toml` (tag checked). Pipeline: `near_duplicate`, `task_changed`, `rubric_task`; `grading.toml` checks-only by default (78 of 120 tasks usable). `rejudge`, `check_results.py`, `audit_rate_limits.py --db` handle distill files (main path identical). Cost ~250k billable, 2 days (`distill/README.md`); commands for after the Phase 3 merge in `distill/COLLECTION.md`. Design decision 24; known issues 66, 68-70.
- [x] Step 4, check conversions: the 42 rubric tasks classified (A 13 converted, B 22 checks added + rubric kept, C 7 unchanged; design decision 25). New check `answer_has_tool_time` (`bench/checks.py`, additive, no main task uses it). Two new check-only time tasks. Set `d1.2`: 122 tasks, 93 check-only (time 0 -> 6, safety 2 -> 10, multistep 13 -> 14); web stays 2 of 7 (the router can send web questions to the teacher). Overlap 0 fail. Cost ~253k billable, 2 days.
- [x] Step 5, QLoRA training: student `Qwen/Qwen3-4B-Instruct-2507` @ `cdbee75f` (Apache-2.0), transformers + peft + trl in an optional `train` extra (`uv sync --inexact --no-install-project --extra train`). `src/kestrel/distill/train_format.py` (chat template renders tools; labels only on weight-1 assistant turns; too long raises), `distill/train.py` + `train.ipynb` + `train_config.toml` (QLoRA NF4, val loss per epoch, early stopping, adapter + `lineage.json`), `distill/TRAINING.md` (Kaggle steps, <1 GPU-hour). CPU smoke run on `distill/smoke/` passes; GPU path not run (#71). Groq §6.3(e) to confirm before collecting (#72). Design decision 26.
- [x] Step 6, export + template parity: `train.py --gpu-smoke` (real base in 4-bit overfits 4 synthetic examples for 30 steps: finite loss falling >= 50%, trainable tokens per example, a parsable tool call from a greedy generation; versions vs `[pins]`). One prompt format in `train_format.py` (`render()` for training, `OLLAMA_TEMPLATE` for the Modelfile; Go-JSON for tools and arguments), checked byte for byte against Ollama 0.40.6's own renderer (`distill/parity/main.go`, `scripts/template_parity.py`, 20/20; `golden.json` checked in CI) and at runtime by token counts (`export.py --verify`). Export: merge -> llama.cpp b11712 GGUF -> Q4_K_M -> `ollama create` (Ollama's safetensors import needs MLX, not on Windows); whole chain run on the CPU smoke adapter (20/20). Student served as `OLLAMA_MODEL=<name>`, ~5.5-6 GB RAM at 16k context. `distill/EXPORT.md`. GPU smoke test passed on Kaggle 2026-10-20 (T4, torch 2.11.0+cu128; notebook finds both mount layouts, stops on no internet or a version off its pin, full training only with `RUN_TRAINING = True`). Collection blocked on #72. Design decision 27; known issues 72, 73-75.
- [x] Step 8, router v1 (`src/kestrel/router.py`, off by default: `KESTREL_PROVIDERS=routed`): cascade with pre-route rules (tool set vs the student's lineage fingerprint; prompt over 80% of the student's context) and escalation on invalid tool call, empty reply, max_steps (fresh teacher budget, cap 2x; steps per turn recorded) or student error/timeout (own `student_health` kind; a routed bench run stops above 10% of turns after 10 tasks); no escalation on refusals or rejected approvals; malformed calls never enter the transcript; nothing runs twice; student replies buffered. Route recorded on spans and in results (`routing`); routed reports give escalation rates per reason and the pass rate with/without passes past max_steps; `bench compare` gives the quality verdict both ways. Agent fingerprint unchanged (`cc5c16377662`). 28 tests with scripted models. Plan decision 5 refined; design decision 28; known issue 77 (tiers-v2 tests after the rebase).
- [x] Pre-PR hardening (2026-10-30, no Groq calls): `distill build` drops traces with a chat-template control token (`<|channel|>` etc., #54) in a tool name, its arguments or any text (`harmony_leak_tool_name` / `_arguments` / `_text`, after `teacher`; design decision 22). Routed path run end to end on local Ollama only (student Ollama's `qwen3:4b-instruct-2507-q4_K_M` with Kestrel's template, teacher `qwen2.5:7b`, `--judge none`, scratch files): 6 main tasks all on the student; step-limit, pre-route (`tool_set`) and student-health escalations each recorded on spans, per task and in `summary.routing`; no bugs found. `distill/COLLECTION.md` is a checklist for either teacher (#72: option a Groq, option c Kaggle-hosted gpt-oss:20b; one setting in `teachers.toml`); distill dry run ~417k tokens, ~313k billable (high side; measured-based ~253k). Fingerprint `35fac5ba6277`.
- [x] Pre-registration tooling (2026-10-30, no model calls): `docs/phase4-plan.md` section 8 (frozen) is computed by `scripts/phase4_power.py` (inputs by sha256, one Generator(2026), reproduced exactly; `tests/test_phase4_power.py` checks the fit and 7 table cells on the pinned after-run). `kestrel bench compare A B --metric checks [--b-references REF...] [--cost-shown] [--safety] [--drift]` prints section 8's statistic (check-pass metric, passes past max_steps and routed errors count as fails, D = routed - 0.95 x teacher, paired bootstrap 10,000 / seed 2026, 0.90 for context, secondary full rule) and section 8.5's outcome (`stats.preregistered_quality`, `stats.phase4_gate`; PENDING until every condition is given). Fingerprint `35fac5ba6277`.
- [x] Option c built (2026-10-30, `phase4/kaggle-teacher`, no Groq, not run on Kaggle yet): `distill/collect.ipynb` (compare mode = #72's pre-registered selection on its 20 listed distill tasks; collect mode = the 122 tasks in 8 resumable shards; uv 0.12.22 + Python 3.14 + Ollama 0.40.13 pinned; no provider keys in the environment; fit check from `/api/ps`; manifest with teacher digest and GPUs), logic in `distill/kaggle_collect.py` (selection metrics and rule, shard coverage check; `tests/test_kaggle_collect.py`), owner steps `distill/KAGGLE.md`, option c candidates commented in `teachers.toml` (Qwen served as `kestrel-teacher-qwen3-30b-a3b` with Kestrel's template, #73). Section 8 amendment drafted, not applied: `docs/phase4-plan-option-c-amendment-draft.md`.
- [x] Option c run on Kaggle (10-31 to 11-01, `phase4/kaggle-teacher`, no Groq): selection winner Qwen3-30B-A3B (pinned `distill/selection/`), collection d1.2-pass1 (122/122 tasks, 82 examples; pinned `distill/collection/`), backup `C:\kestrel-backup\2026-10-31\`. **Evidence only:** never trained on.
- [x] Teacher decision (2026-11-01, design decision 29, #72 closed): **option a, groq + openai/gpt-oss-120b** (Groq's written confirmation in a support chat, 10-31). `teachers.toml` lists only it. Section 8 unchanged; option c amendment not applied. #59 (draft-and-ask): collect now, fix later; the fix must re-measure the routed system or retrain. Fingerprint `35fac5ba6277`.
- [x] Student-eval tooling (2026-11-01, `phase4/student-eval` off `phase4/kaggle-teacher`, no Groq, not merged): `kestrel bench cost RESULTS --db DB [--teacher T --teacher-db TDB] [--out MD] [--json J]` (`src/kestrel/bench/cost.py`): list-price cost per task run re-priced from the llm_call spans with one prices.toml for both sides (Ollama $0, judge not counted), Groq billable per 100 tasks, latency p50/p95, teacher share of turns and task runs with 95% intervals, escalations by reason/kind; with `--teacher` section 8.2's paired bound -> `--cost-shown yes|no` or CAN'T DECIDE (missing traces, unpriced calls, one-sided tasks, another teacher). `export.py --gguf` registers an existing GGUF (same Modelfile, `--create --verify`). `distill/EVAL.md`: section 8's runs in order with Groq cost and approval lines. `scripts/phase4_dryrun.py`: fake files through cost + compare (PASS / NOT SHOWN / FAIL / PENDING; `tests/test_bench_cost.py`). Found: every safety task registers `fetch__fetch`, so the router pre-routes 48/48 safety tasks to the teacher (KI-pending-routed-safety-preroute, owner decision in EVAL.md step 4); drift-guard reading open (EVAL.md step 5). D-pending-phase4-cost-report.
- [x] Amendment 8.7 (2026-11-01, `phase4/student-eval`, no Groq, not merged): routed safety run split by task data (`src/kestrel/bench/routed_safety.py`): group F 19 (need `fetch__fetch` or an MCP stub; teacher by the pre-route rule, the teacher's v2.1 result, not re-run), group S 29 (routed, `bench run --split safety --safety-group S`, fetch stub left out; meta `safety_group`, `fetch_stub`); student alone on all 48 locally, reported. Drift reading for #58 fixed (paired 95% interval of this week's control − 10-07 entirely below 0 = gross drift). `tests/test_routed_safety.py` (pre-route proved per task, plan lists checked against the data). `distill/EVAL.md` step 4: group S ~54k at e = 30%, ceiling ~180k. D-pending-routed-safety-groups.
- [ ] Next (order fixed 2026-10-26): Phase 3 merge (tiers v2.1, no memory), the teacher decision (#72, done 11-01: option a), a rebase, then collect one pass (`distill/COLLECTION.md`, owner's OK for quota), build the dataset, then QLoRA fine-tune on free GPUs.
