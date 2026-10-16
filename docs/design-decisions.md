# Design decisions

Short records of the choices that shape Kestrel: what we decided, what else we considered, and why. Newest last.

---

## 1. Our own agent loop, not a framework

**Decision:** The agent loop (`agent.py`) is ~400 lines of our own code: call the model with tool schemas, run the tool calls, feed results back, repeat until a plain answer or `max_steps`.

**Alternatives:** LangChain / LangGraph, LlamaIndex agents, the OpenAI Agents SDK.

**Why:** The loop is the part we most need to control and explain: where the approval gate sits, how parallel tool calls keep their order, how history is trimmed without splitting a tool call from its result, what happens on every kind of failure. Frameworks hide exactly those decisions behind abstractions that change between versions. Our loop is small enough to read in one sitting and fully covered by tests with a fake model. The cost is that we write features (streaming, retries) ourselves; so far each has been tens of lines, not hundreds.

---

## 2. One provider interface, with fallback

**Decision:** Every provider is reached through the OpenAI-compatible API behind one `LLM.chat()` method. `FallbackLLM` wraps a chain of providers (`KESTREL_PROVIDERS=gemini,groq,ollama`) with the same interface, so the agent never knows which one answered.

**Alternatives:** Each vendor's own SDK; a multi-provider library such as LiteLLM; a single provider.

**Why:** Gemini, Groq and Ollama all speak the OpenAI-compatible protocol, so one client covers them and switching is configuration, not code. Free tiers rate-limit often, so falling back is a requirement, not a nice-to-have. Errors are sorted into temporary (429, timeouts, 5xx: retry with backoff, then fall back) and permanent (bad key, unknown model: fail fast with a clear message). Provider quirks are contained in one place, e.g. Gemini's thought signatures, which are stripped for other providers and placeholder-filled for Gemini.

---

## 3. Risk tiers fixed in code

**Decision:** Each tool is declared `safe`, `confirm` or `forbidden` in its decorator. `Tool` is a frozen dataclass, and the registry itself refuses to run a `confirm` tool unless the approval gate passes `approved=True`, a Python argument model output cannot reach.

**Alternatives:** Asking the model to request permission; a tier per call decided at runtime; a policy file.

**Why:** Anything the model controls can be manipulated, by a mistake or by a prompt injection. Putting the tier in code, checked at the lowest layer, means a fooled model can only *propose* a risky action. Arguments are validated against the function signature, so a model adding `"risk": "safe"` just gets an error. The default with no approver connected is "reject everything", so forgetting to wire one up fails safe.

---

## 4. An `Approver` interface separate from the gate's rules

**Decision:** `Approver.review(tool_name, args, preview) -> Decision` is the only thing a UI implements (`TerminalApprover`, `WebApprover`). `ApprovalGate` owns the rules: tiers, the edit-and-re-preview loop, session approvals (never for `send_message`), and the audit log.

**Alternatives:** One class doing both; approval logic inside each UI.

**Why:** UIs will multiply (terminal, web, later phone and voice) and each must not be able to weaken the rules. With this split a new approver is ~50 lines, and an approver that wrongly returns "approve for session" for `send_message` is overruled by the gate. The web approver blocks the agent's worker thread until the browser answers; a disconnect, timeout or malformed answer is a rejection, so nothing ever fails open.

---

## 5. Our own tracer, with OpenTelemetry names

**Decision:** A small tracer (`tracing.py`) records each request as a tree of spans in SQLite, using OpenTelemetry GenAI attribute names (`gen_ai.provider.name`, `gen_ai.usage.input_tokens`, ...).

**Alternatives:** The OpenTelemetry SDK with an exporter; a hosted service (Langfuse, LangSmith, Helicone); plain log lines.

**Why:** A $0, offline, single-file store that the CLI, the console and the export command can all query directly. Hosted services cost money or need accounts, and the full OpenTelemetry SDK is heavy for one process. Using the standard attribute names keeps the door open: exporting to OpenTelemetry or Langfuse later means writing an exporter, not changing call sites. Traces also carry what training needs: the conversation, user ratings, and list prices for the cost curve.

---

## 6. MCP: external servers are untrusted by default

**Decision:** Tools from MCP servers are `confirm` unless listed in `safe_tools` in `kestrel.mcp.json`. Server annotations (`readOnlyHint`, ...) are shown in the approval preview but never lower a tier. Their results and error messages are wrapped as untrusted data. Kestrel's own MCP server exposes only safe tools plus `create_note`.

**Alternatives:** Trusting annotations; all external tools safe; not supporting MCP.

**Why:** A server can claim anything about itself, and its output can contain injections. The only trustworthy signal is the user's own allowlist. Exposing write or send tools through `kestrel-mcp` would bypass Kestrel's gate in someone else's client, so they are simply not offered. The MCP SDK is async; it runs on its own event loop thread behind a blocking call, so the synchronous agent didn't have to change.

---

## 7. One stream of typed events

**Decision:** The agent emits plain-dict events (`step_started`, `llm_call`, `text_delta`, `tool_call`, `tool_result`, `answer`, `error`, `done`, plus `approval_required` / `approval_resolved` from the web approver). The terminal and the web console are both just consumers.

**Alternatives:** Return values only; separate callbacks per UI; making the whole agent async.

**Why:** Showing the agent's work live (and streaming tokens) needs events, and having one stream means the terminal and the browser can't drift apart. Plain dicts serialize straight to JSON for the WebSocket and are trivial to assert on in tests. A failing listener can never break the agent. The agent stays synchronous on a worker thread; the web server forwards events through a thread-safe queue.

---

## 8. React + Vite, not Next.js

**Decision:** The console is a static single-page app (React, Vite, TypeScript, Tailwind) built into `console/dist` and served by the same FastAPI process.

**Alternatives:** Next.js; server-rendered templates (Jinja/HTMX); Streamlit or Gradio.

**Why:** Kestrel already has a backend in Python; the console only needs to render events and call a few REST endpoints. Next.js would add a second server runtime, routing and rendering modes we don't need, and a Node process in production. Vite gives a fast dev server with a proxy to the backend, and the build is three static files, so production is one Python process on one port. Streamlit/Gradio would be quicker to start but can't do a live, product-quality approval flow. Charts are small hand-written SVG components rather than a chart library, keeping the bundle lean.

---

## 9. Free first, with an open-weight teacher

**Decision:** Every feature works on free tiers (Gemini free tier, Groq free tier, local Ollama, a no-key demo mode). Paid services are optional upgrades. For distillation in Phase 4, the "teacher" whose outputs become training data is an open-weight model (e.g. gpt-oss-120b on Groq).

**Alternatives:** Build on a paid frontier API; distill from Claude, GPT or Gemini.

**Why:** A portfolio project should run for anyone, including reviewers, without a credit card; demo mode removes even the need for a free key. Recording a list price per request anyway lets us show what each conversation *would* cost on paid models, which is the headline cost curve later. Claude, GPT and Gemini terms restrict using their outputs to train other models, so training data must come from open-weight models whose licenses allow it. Traces from the demo provider are excluded from export for the same reason: they aren't model outputs at all.

---

## 10. KestrelBench: checks first, a judge only where needed

**Decision:** Each of the 100 tasks is scored mostly by deterministic checks on what *happened* (tools called with which arguments, whether they ran, files written, the outbox, numbers in the answer). An LLM judge (gpt-oss-120b on Groq) grades only open-ended parts against a written rubric, and is calibrated against human labels. Tasks run against a fixed fixture workspace with a scripted user for approvals. CI runs a 16-task subset on every PR; the full suite runs weekly.

**Alternatives:** An off-the-shelf benchmark (generic, doesn't test Kestrel's gate or tools); judge-only grading (cheaper to write, but noisy and gameable); exact-match only (can't grade summaries); running everything in CI (too slow and too much free-tier quota).

**Why:** Deterministic checks are free, reproducible and explain themselves when they fail; most of what matters for an agent (did it call the tool, did it refuse the injected send) is checkable exactly. Safety tasks fail on the *attempt*, so the score measures the model, not the gate behind it. The judge is open-weight for the same reason as the teacher (decision 9) and runs on Groq because Gemini's free tier allows only 20 requests a day. Model outages count as errors, not failures, so a rate limit can't masquerade as a regression. The CI threshold sits below the baseline by a margin, because model outputs vary run to run.

---

## 11. Eval statistics: bootstrap intervals, paired comparisons, a held-out judge check

**Decision:**
- Scores carry a 95% percentile-bootstrap interval over tasks (10,000 resamples, fixed seed). Repeats are averaged per task and tasks are resampled, never individual runs.
- Two runs are compared with a paired bootstrap on shared tasks, plus flip counts. "Within 5 points" means the whole interval of the difference lies above −5 points.
- Judge calibration uses binary human labels (pass/fail) and a fixed dev/held-out split of rubric tasks: the prompt is tuned on dev, and only held-out agreement is reported. The judge prompt is versioned, and re-judging uses stored answers only.

**Alternatives:**
- Normal-approximation or Wilson intervals: simpler, but not reusable for paired differences or per-task means.
- Comparing two separate intervals: much too conservative, since task difficulty doesn't cancel.
- 0/0.5/1 human labels: finer, but people disagree with themselves on "half right", and pass/fail is what the benchmark decides.
- Re-running the agent after each judge change: costs quota, and changes the answers being graded.

**Why:**
- One bootstrap covers everything we need: overall, per category, paired differences, and agreement.
- Pure Python is fast enough at this size (no numpy).
- The paired design is what makes a 100-task suite able to support Phase 4's claim at all.
- The held-out split stops a judge prompt being tuned until it agrees with the very labels used to report its agreement.
- Versioning and stored answers keep every number traceable to the judge that produced it.
- Known weakness: the bootstrap gives zero-width intervals when every task agrees; the report marks those.

**Suite versions (added with v1.1).** Every check change is reviewed in both directions: failures for false failures, a seeded sample of passes for false passes. It is then logged with its evidence and its effect on stored results, and bumps `SUITE_VERSION`. The README reports the score under the current version and says what the previous version scored and why it changed. Re-scoring stored answers, instead of re-running the agent, keeps the comparison to the same outputs.

---

## 12. Smaller choices worth knowing

- **SQLite via `sqlite3`, no ORM.** One file, zero setup, enough for one user. Connections are opened and closed per operation by `Tracer.connect()`.
- **Hand-made markdown/SVG over libraries where it's small.** Fewer dependencies to audit; `react-markdown` is the exception because markdown parsing is not small.
- **oxlint for the console.** typescript-eslint doesn't yet support TypeScript 7; oxlint parses TypeScript itself and includes the React hooks rules.
- **gitleaks as a tool, not the GitHub Action.** The tool is MIT; the action is commercially licensed for organizations.

---

## 19. Distillation data: open-weight teacher only, no KestrelBench tasks, drop on any secret

(Numbered after the Phase 3 branches' decisions, which reach 18, so the merge doesn't collide.)

**Decision:** `kestrel distill build` (`src/kestrel/distill/dataset.py`) turns a copy of a traces database into `train.jsonl`, `val.jsonl` and `manifest.json`. Filters run in a fixed order and each drop is counted: no stored messages, teacher, contamination (bench run, bench prompt), quality, secrets (canary, redaction pattern, `.env` line, env value), length, duplicate.
- **Teacher (hard):** every model step's (provider, model) must be listed in `src/kestrel/distill/teachers.toml` (default `groq` + `openai/gpt-oss-120b`). There is no flag for other providers, and the loader refuses Gemini, Claude, GPT and demo entries, so editing the file can't admit them either.
- **Contamination (hard):** any trace listed in a bench results file is dropped, and so is any trace whose user message equals a KestrelBench prompt from any task folder under `evals/kestrelbench/` (case, punctuation and whitespace ignored). The command refuses the live `logs/traces.db` and any `bench.db`.
- **Quality:** answered (not `max_steps` or error), not rated `/bad`, and either rated `/good` or part of a bench task that passed (all checks, judge >= 0.5). Since bench traces are always dropped, the second path only serves future graded sources that aren't KestrelBench runs.
- **Secrets (hard):** drop, never redact, if any part of the example (system prompt, user turns, tool arguments, tool results, answer) holds a `KCAN-` canary (also URL, base64 or hex encoded, or its prefix alone), a `tracing.redact()` match or `[REDACTED]` marker, a `.env`-style secret line, or a secret environment variable's value.
- **Format:** records come from `trace_report.export_record`, the function `kestrel export` uses, so there is one formatter. Over-long examples are dropped, not truncated. The split hashes the session id (salt 2026), so a conversation is never on both sides. The manifest holds the input SHA-256, the git commit, the teachers, the settings, the drops and the stats; no timestamps, so a rebuild is byte-identical.

**Alternatives:**
- **Allow other teachers by flag.** Convenient, but one mistaken run would put closed-model outputs into training data (key decision 3). A config file the loader validates keeps the choice reviewable in git.
- **Train on passing KestrelBench runs.** The most graded data we have, but KestrelBench is the student's deploy gate: a student that has seen its tasks passes the gate for the wrong reason.
- **Redact secrets and keep the example.** The conversation around the secret still teaches the model to fetch and repeat it, and a canary the redactor misses would be memorized.
- **Truncate long examples.** A cut-off tool call or tool result teaches a broken trajectory.
- **Split by trace.** Turns of one conversation share context, so val would partly measure memorization.

**Why:** the student should learn only behavior that a teacher it may learn from produced, that passed a check other than the benchmark it will be judged on, and that contains no planted or real secret. Every dropped example is counted by reason, so a small dataset is explained, not silent. Open items: known issues #30-#32.

---

## 20. Distill task set: disjoint from KestrelBench, told apart by metadata

**Decision:** graded teacher data comes from a separate task set, `distill/` (120 tasks in the main format, version `d1.0`, its own fixture workspace), run with `kestrel bench run --split distill`.
- **Disjoint by construction:** it lives outside `evals/kestrelbench/`, with a different setting (a community garden and bike co-op), people, addresses (all on example domains), numbers and file names. `scripts/distill_overlap.py` compares every distill prompt with every KestrelBench prompt (all splits; case, punctuation and whitespace ignored): an exact match or word 3-gram Jaccard >= 0.5 fails, >= 0.3 is listed for review. At `d1.0`: 0 fail, 0 in review (closest 0.23), also against the Phase 3 safety split.
- **Same 10 categories in the main suite's proportions (x1.2), over-weighting three baseline failure modes** (`evals/reports/baseline-failures.md`): finding facts in the workspace instead of asking (12 tasks, `beh-lookup`), using the calculator (14, `beh-calc`), and calling the approval-gated tool instead of drafting and asking (12, `beh-gated`). The `safety` category holds risky requests the user makes themself (delete, outside paths, `.env`, skipped approvals, false claims); there is no injection or attack text in the set.
- **Runner change, additive:** `--split {main,distill}` (default `main`) and `--split-dir`. A distill run reads `distill/tasks` and `distill/workspace`, writes traces to `logs/distill.db` (never `bench.db`), and records `split`, `split_version`, `task_dir` and `workspace_dir` in its results meta. Main runs gain only `"split": "main"`. Proof: the main suite's dry run is byte-identical before and after (with and without `--split main`), all 100 task fingerprints are identical, and the agent fingerprint stays `cc5c16377662`.
- **Pipeline rule (deny unless proven):** `kestrel distill build` treats a results file as a distill run only if `meta.split == "distill"` and every task id starts with `dst-`. Anything else (no `split` key, as in every older file, `main`, `safety`, an unknown split, a mix) is KestrelBench and its traces are dropped. A trace listed by both kinds keeps the KestrelBench verdict. The file's location is never used. The manifest records each results file's hash and how it was classified.

**Alternatives:**
- **Hold out part of KestrelBench for training.** The held-out part would stop being a fair test, and the remaining suite would be smaller than its confidence intervals can bear.
- **Tell runs apart by folder or file name.** A copied or renamed file would change its meaning; metadata travels with the file.
- **Put the set under `evals/kestrelbench/`.** Its prompts would then count as KestrelBench prompts, and step 1's contamination filter would drop its own traces.
- **Paraphrase bench tasks for the set.** Cheap, but it trains the student on the exam's wording; the overlap check exists to catch exactly that.

**Why:** the student needs graded, teacher-written examples of the behaviors the baseline gets wrong, without ever seeing KestrelBench, which stays the deploy gate.

---

## 21. Distill overlap thresholds from the bench's own distribution; checks-only grading

**Decision:**
- **Overlap thresholds** come from how similar KestrelBench prompts are to *each other*: word 3-gram Jaccard and the longest shared run of words over every pair of bench prompts (main suite on disk plus the safety split at its pinned commit, 155 prompts), leaving out pairs from the same task and identical-text twins (benign/attack pairs share their prompt on purpose). Fail just above that p99.9 (Jaccard >= 0.23, run >= 5 words), review just above the p99 (Jaccard >= 0.08, run >= 4 words). Fixtures fail on a shared file name or a shared content line of 8+ words. A test recomputes the distribution with the same exclusions and fails if the thresholds fall below it. Report: `evals/reports/distill-overlap.md`. At `d1.1`: 0 fail, 38 in review, 0 fixture problems; one task (`dst-web-uv-tool`) failed the new thresholds and was reworded, so `d1.0` became `d1.1`.
- **The same fail thresholds filter the pipeline** (`near_duplicate`): every user message of every example, chats rated /good included.
- **The safety split is pinned by commit** in `src/kestrel/distill/bench_sources.toml`, read with git, checked against its tag (a moved tag stops everything), recorded in the report and manifest; without the commit, disk only, and said so.
- **Grading:** `grading = "checks-only"` keeps only distill tasks graded entirely by checks (78 of 120). Rubric tasks are dropped until a judge@version is listed in `calibrated_judges`. Each distill result is matched to its task definition by the task fingerprint (`task_changed` if it moved). Collection runs with `--judge none`; `rejudge` can grade stored rubric answers later.

**Alternatives:**
- **A fixed 0.5 Jaccard** (step 2): not grounded in anything; the bench's own pairs show 0.5 is far above where copying starts.
- **The bench's p99 as the fail line:** a single shared 3-gram ("what time is it") would fail, which is ordinary phrasing, not copying.
- **Include the twins in the distribution:** they are copies by design and push p99.9 up (0.286 instead of 0.222).
- **Judge rubric tasks with the teacher:** self-preference bias would choose the training examples (#33).
- **Read the safety split from the tag or a branch:** both can move; a commit can't.

**Why:** "too similar" should mean "more similar than the benchmark's own distinct tasks ever are", measured, not guessed. And until a judge is shown to agree with humans, only deterministic checks decide what the student learns from.
