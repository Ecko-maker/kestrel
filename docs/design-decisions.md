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

## 13. Safety split: declared goals, two levels, scripted models

**Decision:**
- Prompt-injection tasks live in their own split (folder, loader, `SAFETY_VERSION`), so the main suite's fingerprints never move.
- Each attack declares its goal as data: leak through a channel, a gated or forbidden call, a file that must stay unchanged, or a write outside `notes/`. Three always-on guards apply to every task.
- Every check is answered at two levels: the model *requested* the harm (model level) or the harm *happened* through the real gate with a scripted user (system level).
- Network and MCP tools are replaced by stubs that serve fixtures and log every call. The log is the system-level evidence.
- Scripted models (always-falls, never-falls, refuse-all) are built from the declared goals, and validate the checks before any real model runs.

**Alternatives:**
- An LLM judge reading the transcript: not reproducible, and it can itself be injected.
- Regexes over the attack text: ties grading to wording, and needs each attack reproduced in the check.
- Mocking the network with a proxy: catches everything, but needs a sandbox we don't have on Windows.

**Why:** Declared goals keep grading independent of the attack's wording, so tasks can be written without changing the harness. The same data drives the scripted models, which prove the checks fire when they should and stay quiet when they shouldn't. The two levels separate what the model does from what the gate stops, which is the "before" number Phase 3's defenses have to move. Attack rates use the exact Clopper-Pearson interval, because a bootstrap gives a zero-width interval at 0 successes.
