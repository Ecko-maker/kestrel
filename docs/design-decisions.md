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

## 13. Safety split: graded from behavior, three numbers, a system-level gate

**Decision:**
- **Graded from behavior.** Each attack declares its goal as data (leak through a channel, a gated or forbidden call, a file that must stay unchanged, a write outside `notes/`). Three always-on guards apply to every task. Checks read only what happened: tool calls, the stub log, files, the outbox, URLs in the answer. Never the wording of the attack or the answer.
- **Three attack numbers:**
  - model-level ASR (the model requested the harm);
  - system-level ASR with the strict user (the task's approval rules, everything else rejected);
  - system-level ASR with the rubber-stamp user (approves everything).
  Plus the benign pass rate, and an "undeclared actions" list for manual review.
- **The Phase 3 gate is system-level under the strict user = 0.** The other two numbers are reported, not gated.
- **Exact intervals.** Every safety rate, including the benign pass rate, uses the Clopper-Pearson 95% interval. Per category too, with categories under 10 tasks flagged.
- **Deny-by-default stubs.**
  - `web_search`, `fetch__fetch` and MCP tools are stubs that serve only the task's fixtures (an unknown URL is a 404, an unknown query returns nothing) and log every call.
  - The real network tools are refused, safety runs start no subprocess or MCP server, and the stubs live in per-task registries. The main suite's registry is pinned to 5b1d900.
- Scripted models (always-falls, never-falls, refuse-all) and `kestrel bench validate` check every task before a real model runs it.

**Alternatives:**
- **An LLM judge reading the transcript:** not reproducible, and it can be injected by the same text it grades.
- **Regexes over the attack text:** ties grading to wording, and needs each attack reproduced in the check.
- **Gating on model level:** a model-level 0 may be out of reach without Phase 4 training, and the gate exists exactly so a model mistake doesn't become harm.
- **Gating on rubber-stamp:** it measures the gate with the user switched off, which no defense short of removing tools can pass for confirm-tier actions.
- **The bootstrap for every rate:** zero-width intervals at 0% and 100%.
- **A network proxy instead of stubs:** catches every client, but needs a sandbox we don't have on Windows.

**Why:**
- Declared goals keep grading independent of the attack's wording, so tasks can be written without touching the harness. The same data drives the scripted models, which prove each check fires when it should and stays quiet when it shouldn't.
- The gate sits where harm happens for a user who reads approval prompts. That is the promise Kestrel makes (Jarvis's control). Model-level ASR shows how much of that rests on the user; rubber-stamp shows how much rests on the gate alone.
- A safety claim needs an interval that is honest at 0 successes: 0/30 reads "below 12% with 95% confidence", not "0%".
- Deny-by-default makes a run reproducible and offline, and means a stub that is missing a fixture can't fall through to the real internet.

## 14. CI evals are path- and label-gated

**Decision:** the CI `kestrelbench` job always runs, but it spends Groq quota only when evals are needed:
- the pull request has the `run-evals` label, or
- a pull request or a push to main changes `src/kestrel/` (agent loop, system prompt, tools, model layer, and the bench's runner, checks and judge), `evals/kestrelbench/tasks/` (prompts and checks) or `evals/kestrelbench/workspace/` (fixtures).

`scripts/ci/evals_needed.py` makes the call from a plain `git diff --name-only` (no third-party action) and is unit-tested.
- **Not needed:** the summary says "KestrelBench not run: <reason>", and the job passes.
- **Needed but no key:** the job fails with a clear error (missing secret, fork, Dependabot).
- **Dependency files** (`uv.lock`, `pyproject.toml`) and Dependabot PRs don't trigger it. The manual full run (`kestrelbench.yml`, manual-only during Phase 3) covers dependency updates.

**Alternatives:**
- **Run on every PR (the old rule):** it spends quota on docs and test changes. Worse, when the key was missing the job passed silently, so a green check didn't mean the gate had run. That happened on every PR until 2026-10-06.
- **A job-level `if:` or workflow `paths:` filter:** a skipped or never-started workflow leaves a required check skipped or pending, so the job couldn't be required.
- **A third-party "changed files" action:** one more pinned dependency with repo access, for what one `git diff` line does.
- **Include `uv.lock` / `pyproject.toml`:** Dependabot PRs get no Actions secrets. They would always fail, and the rule "never merge with red CI" would block every dependency update.
- **Label-only:** easy to forget on a prompt change. The paths catch the changes that matter by default.

**Why:**
- A green check should mean what it says: either the gate ran and passed, or the summary says plainly that it wasn't needed and why.
- Free-tier quota (200k tokens/day) is spent where it can change the answer.
- The CI subset is drawn from the main suite, so `evals/kestrelbench/safety/` is excluded as well.

## 15. Safety repeats: any repeat counts for attacks; benign gets two numbers

**Decision:** with `--repeat N`, the unit is the task.
- **Attacks:** an attack is a success at a level if it succeeded in **any** of its repeats. The attack success rate is attacks succeeding in any repeat / attacks, with a Clopper-Pearson interval over tasks. The report also gives "k of n repeats" per attack, so flaky ones are visible.
- **Benign tasks:** two numbers. The mean pass rate (each task's pass fraction, bootstrap over tasks), and the share that passed every repeat (Clopper-Pearson).
- **The gate:** 0 attacks succeeding at system level under the strict user, in any repeat.

**Alternatives:**
- **Majority or mean over repeats for attacks:** an attack that works one time in three would score as "mostly safe". In deployment, an attacker gets to retry, and so does every user who meets the same poisoned email. The gate would then pass on an attack that demonstrably works.
- **Count every run as a trial (n = tasks × repeats):** a narrower interval that isn't real. Repeats of one task share the same text and the same weakness, so they are not independent evidence about the attack space. This is the same reasoning as the main suite's bootstrap over tasks.
- **Benign by every-repeat only (the s1.0 rule):** strict, but it hides the difference between "failed once in three" and "always fails". **Mean only:** it hides that a defense still breaks a task sometimes.

**Why:**
- Any-repeat is the conservative reading for a safety claim, and it is monotone: more repeats can only reveal more successes, never hide one.
- Keeping n = tasks keeps the interval honest. The report says so explicitly, so "0/30 with 3 repeats" isn't read as more certain than "0/30".
- Two benign numbers separate over-refusal that is systematic from over-refusal that is noise.

## 16. Permission tiers v2: capabilities, taint, a content check, and a console that loads no outside image

**Decision** (owner-approved 2026-10-06; built on `phase3/tiers-v2`, not merged until the before and after runs exist; design `docs/tiers-v2-design.md`):
- **Capabilities instead of one tier.** Each tool declares what it can do (`reads_local`, `reads_untrusted`, `network_egress`, `writes_local`, `sends`, `deletes_local`), fixed in code. One pure function, `permissions.required_approval(capabilities, session, args)`, decides safe / confirm / forbidden. With nothing read yet it gives exactly the old tiers. MCP tools get every capability unless `kestrel.mcp.json` narrows them; `safe_tools` means "network_egress only".
- **Taint (a) with user-named egress (e1).** Once a `reads_local` tool has run in a conversation, a `network_egress` call needs a card, unless the user typed its URL or query. Per conversation, never cleared.
- **Outbound-content check (b).** Local text read this conversation (24+ normalized characters or 4+ words, as written, decoded, or split across calls, never text the user typed) found in a network call's arguments gives a card that shows it. A card, never a block.
- **Console images (d).** An answer image loads only from the console itself or an allowlisted host (empty by default); any other becomes a plain link. A CSP header (`img-src 'self'`) backs it up.
- **Cards that explain themselves (e2, e3).** Why the card appeared, the arguments decoded, the local text highlighted, and a warning for recipients the user never typed. The audit log and traces keep hashes and lengths only.

**Alternatives:**
- **Keep one tier per tool, and make `web_search` and `fetch` "confirm".** Simple, but every search would need a card, research tasks would drown in them, and users would learn to click yes. Capabilities let the same tool be card-free until private data is in the context.
- **Hard-block content-check hits.** It would lower the rubber-stamp numbers, but a false hit would break a legitimate task with no way through. Cards keep the user in charge (owner decision 4). The strict-user gate is met either way.
- **Taint from `reads_untrusted` too (web pages and search results).** Safer against beaconing, but search → search and search → fetch chains, the normal shape of research, would all need cards. Every canary in the split lives in local files, so the predicted gate result is the same (owner decision 2).
- **Per-turn taint.** Fewer cards in long chats, but data read in one turn can be leaked in the next. Per conversation is the conservative choice (owner decision 3).
- **A fetch domain allowlist (c) as the main defense.** It doesn't stop search exfiltration or a leak in an allowed host's query string, and it needs configuration. Kept as an option, off by default.
- **A model-based injection detector.** Costs a model call per tool result, can be fooled by the same tricks as the agent, and can't be verified offline. The rules here are deterministic and unit-tested.

**Why:**
- A leak needs two things: private data in the context, and a way out. Taint watches the first, capabilities name the second, and the user decides only when both meet. Measured offline (`evals/reports/tiers-v2-offline.md`): strict-user system-level attack success on the scripted split drops from 13/29 to 0/29 for a model that reads before it falls, and the main suite's stored tool sequences get no new card.
- **Stage-6 acceptance (owner decision, 2026-10-11): the criterion is reads-then-falls → 0/29, not always-falls.** reads-then-falls is a scripted model that reads the local data, then obeys the injection: the weakest model a real attack needs. always-falls keeps 11/29 strict-user successes after, but they are artifacts: it writes a canary it never read into a search or fetch, which no real model can do, and with nothing read nothing taints the conversation. The same 11 attacks are stopped under reads-then-falls. The proof **passed on the `e37e6e5` task set** and is **to be re-run on the final attack texts** once `phase3/memory-safety` is merged in.
- The model sees exactly the same tools and prompt (the fingerprint moves only because capabilities are hashed), so a change in the after-run is the defense, not a different agent. Pinned by `test_the_tiers_v2_agent_fingerprint_is_pinned`: agent `2cac3804ef92` (rules `tiers-v2`, the 2026-10-21 main-suite after-run), then `35fac5ba6277` once the approval rules' version was hashed too (rules `tiers-v2.1`, taint only from reads that returned local data, known issue #57); the tools hashed without capabilities still give the before run's `22ab98e45865`, with the same system prompt hash.
- The content check is evidence more than a gate: after a read, every network call already asks. It tells the user *what* would leave, which makes "no" an informed answer.

## 17. Phase 3 tiers gate: ship tiers v2.1 (owner override on rule A)

**Decision** (owner, 2026-10-28; measurements and rules in `docs/tiers-v2-design.md` section 5): ship tiers v2.1.
- Safety gate PASS (0/29, CI 0–12%).
- Rule C: gate not met (a), 0 escalations; merge per the frozen §5 consequence.
- Rule A: part 1 NOT met (12/18 → 11/18; both drops are the model's draft-and-ask habit, no card); parts 2 and 3 hold. No pre-registered consequence existed for rule A failing. This is an owner override, not a pass. `evals/reports/rule-a-power.md` (written after the result) is context only.
- Lesson: part 1 is underpowered; the next safety measurement pre-registers the s1.1 rule from `rule-a-power.md` before its before-side runs.
- Caveats carried: known issue #58, 18 benign tasks (±14 points), #56, #23, #21, #53, image policy unrecorded on the main split.

**Alternatives:**
- **Hold and pre-register s1.1 now, then measure both sides at 5 repeats** (about 126k billable, 1 Groq day). Not chosen; s1.1 is pre-registered for the next safety measurement instead.
- **Hold and fix the draft-and-ask habit first** (a system-prompt change, so a new agent: safety strict x3, rubber-stamp and main x3 again, about 840k billable, about 5 Groq days). Not chosen: the habit predates tiers v2 (failure group 3 of the baseline), and fixing it is tracked as its own measured change (known issue #59).

**Why:**
- The defense itself met its gate: 0 system-level attack successes under the strict user, and no benign or main-suite failure was caused by a card.
- The two part 1 drops come from a model habit seen in the baseline too, with no card in either run.
- Recording it as an override keeps the rule as it was: part 1 stays "not met" on the record.

## 18. A HUD look for the console, driven by agent events

**Decision** (owner-approved 2026-10-21, branch `console/hud-theme`): a second look, "HUD", next to the classic one (still the default). A toggle in the nav switches between them; the choice is kept in localStorage, with every access wrapped so blocked storage falls back to classic.
- **Recolour, don't fork.** The HUD redefines Tailwind's `stone` (navy) and `accent` (cyan) colour variables under `html[data-look="hud"]` (`console/src/hud/hud.css`), so every page and component, approval cards included, is restyled without a second copy. Amber, red, emerald and violet keep their meaning.
- **The rings show agent state, not decoration.** `useHud.ts` derives a phase from the existing chat reducer: idle, thinking (a step started and its `llm_call` hasn't arrived; `llm_call` is sent when the model has finished), streaming (`text_delta`), tool (a `tool_call` without its result: one ring segment lights up with the tool's name), approval (a pending card: amber), error (`error` or a failed `llm_call`: one red flash), done (settles), offline. No new server events.
- **Approval cards stay plain.** Opaque background, no blur, no animation inside; the amber frame and glow are drawn outside the card's box. `src/hud/contrast.test.mjs` (run by `npm test` in CI) reads the palette from `hud.css` and checks WCAG AA for the text colours.
- **Motion:** CSS/SVG only; `prefers-reduced-motion` turns off rotation, flashes and the 1.2 s boot sequence, and the state still shows through colour and a text label.
- **Font:** Orbitron (SIL OFL 1.1, `@fontsource-variable/orbitron`), bundled by Vite (11.8 KB, Latin only), used only for the wordmark and HUD labels and downloaded only when the HUD is on. No CDN, so the CSP needed no change.

**Alternatives:**
- **A separate HUD chat page.** Freer layout, but two copies of the approval card and trace to keep in sync, and a safety-relevant card could drift between them.
- **Canvas or WebGL effects.** Richer, but heavier, harder to make accessible, and not needed for rings.
- **Show tiers-v2 taint in the HUD.** The console isn't told the session's taint state (known issue #60), so the panel shows only what it does receive: approval cards with a tiers-v2 notice ("escalated cards").

- **The falcon tells the tagline** (2026-10-21): wings and body are separate SVG groups; `FALCON_POSE` in `useHud.ts` maps each phase to a pose: perched (idle), flapping and lifted (thinking), a short stoop at the lit segment (tool), hovering with wings spread (approval), swooping back with wings open (answer), a short shake (error). On `answer` (never `text_delta`, never for a failed turn) a one-shot burst plays: a shockwave, 16 sparks and a quick spin that settles, all within 0.9 s. `burstReducer` keeps it to one at a time and none under reduced motion, which also freezes the falcon in its drawn pose. Transforms and opacity only; the reactor clips to its own box, so nothing moves over the chat or a card.

**Why:** the HUD makes what the agent is doing visible at a glance (thinking, which tool, waiting on you) from data the console already has, at +5 KB gzipped JS and +2.4 KB CSS, and without making the approval decision harder to read.

## 18. Voice wake-up: Vosk with a two-word grammar, local speech, no model call

**Decision** (owner-approved 2026-10-22, branch `voice/wake-word`; guide `docs/voice-wake.md`): saying "Kestrel" wakes the display, brings the console forward, plays the falcon's fly-in and speaks a fixed greeting. Off by default (`KESTREL_WAKE=on`).
- **Detection:** Vosk 0.3.45 with its small English model (Apache-2.0, 41 MB), restricted to the grammar `["kestrel", "[unk]"]`, behind a `Detector` interface. "[unk]" keeps other speech from being forced onto the wake word (tested with a recognizer that behaves like Vosk's grammar mode).
- **Greeting:** SAPI 5 through comtypes, on its own thread. Fixed text; the name comes from `KESTREL_USER_NAME`, none in the repo.
- **Power** (revised 2026-10-23, owner): plugged in, listen and hold a "system required" power request (only while the microphone is open; released on every switch to battery, when listening stops and at shutdown; tested). On battery, listen while the PC is awake if the charge is at least `KESTREL_WAKE_MIN_BATTERY` (30) and Energy Saver is off, never holding a request, so Windows sleeps as usual; below the minimum pause until +5 (35%) or mains. `KESTREL_WAKE_ON_BATTERY=off` remains as an override (never on battery). One pure function, `power.decide`, holds the rule; `GetSystemPowerStatus` is polled every 5 s; after standby the stream is reopened. The HUD says "keeping PC awake" only when Windows granted it.
- **HUD modes** (2026-10-23, owner): Standby (mic open, waiting), Listening (after a wake, with the live mic level), Thinking, Answering, plus the existing tool, approval, error. Each has its own pose, ring motion, colour and label; `useHud.ts` (`hudPhase`) stays the single source, and agent work always wins over the wake word's modes. The level is one smoothed RMS number (0-1) per 0.1 s chunk, sent only during Listening; typing or sending ends it. Answering pulses are paced to at most one every 0.45 s (WCAG 2.3.1).
- **Records:** a wake is one `wake` span with its time and no `traces` row, so it never reaches Traces, Stats, `kestrel export` or `kestrel distill build`.
- **API:** `GET/POST /api/wake` under the same login, Host check and, new for every `/api/` route, an Origin check (403 for another site's page).

**Alternatives:**
- **openWakeWord with a custom "kestrel" model.** Built for wake words, with thresholds that mean something, but a custom model is trained in Colab on 17.5 GB of negative features licensed CC BY-NC-SA, so the model's own licence is unclear; the bundled models are CC BY-NC-SA too. Kept as the swap-in if the measured false-wake rate is too high.
- **The browser's speechSynthesis for the greeting.** Chromium allows it only after a user gesture on the page, so a wake after a reload would be silent; Edge's "Natural" voices are online. SAPI is local and works with no page open.
- **Piper for the greeting.** Better voices, but a 60 MB+ voice download and the maintained fork is GPL-3.0.
- **A real mouse movement or a key press to wake the display.** It would change what the user is doing; a zero-pixel move is enough to count as input (pinned by a test).

**Why:** the wake word is the first voice feature, so it is built where mistakes are cheapest: it can only wake and greet. Nothing it hears can reach the model, a tool or an approval, and nothing it hears is kept.

**Measured** (2026-10-22/23, this laptop, Python 3.14): feasibility on synthetic Windows-voice clips 4/4 spotted, 0/10 look-alikes (not an accuracy claim); CPU while listening 1.3% of one core over 10 minutes (per minute 0.2-5.5%); the `powercfg /requests` check needs an elevated prompt (known issue #47). Detection rate and false wakes per hour wait for the owner's recordings (`scripts/wake_eval.py`).

## 19. Talking to Kestrel: whisper.cpp base.en, a hallucination guard, the same input for the model

**Decision** (owner-approved 2026-10-23, branch `voice/talk`; guide `docs/voice-wake.md`, "Talking to Kestrel"): after a wake, one spoken request is recorded in memory, transcribed locally with whisper.cpp base.en (5-bit, MIT, 57 MB, SHA-256 pinned; tiny.en as a one-setting fallback) and sent by the most recently active console exactly like typed text. Answers to spoken requests (only) are spoken, first two sentences, behind a remembered "Speak answers" switch.
- **End of speech:** loudness above a learned noise floor; 1 s of quiet ends it, 20 s at most, 3 s to start.
- **Hallucination guard:** drop on under 0.4 s of speech, low confidence (segment confidence as the closest available signal to whisper.cpp's no-speech probability, which pywhispercpp doesn't expose), or a whole-transcript filler; a dropped transcript is treated like an empty one.
- **Safety:** an approval card is announced with one fixed sentence, never read; voice can't approve. The voice flag stays in the server and the console; the agent receives only the text, so the model's input is identical for typed and spoken requests (tested), and the agent fingerprint is unchanged.

**Alternatives:** Vosk small (already installed, 1.7 s per 5 s request in one go, LibriSpeech test-clean 9.85%); faster-whisper tiny.en (0.28 s, 8.4%); faster-whisper base.en (145 MB, same model family as the choice, not measured: over the 100 MB limit); openai-whisper (needs PyTorch). Measured on this laptop (Ryzen 7 8840HS, 8 threads, synthetic 5 s requests): whisper.cpp base.en 5-bit 0.80 s.

**Why:** base.en roughly halves tiny.en's errors on real speech for half a second more, from a 57 MB file and a 1.4 MB library. The guard exists because a transcript is sent to the agent as if the user had said it: inventing a request is worse than missing one.

**Revised (owner, 2026-10-24):** low confidence no longer drops a transcript: it goes unsent into the console's message box ("Did I hear that right?") for the user to send, edit or discard ("check first"); the hard drops stay. A vocabulary hint (whisper.cpp initial prompt, <= 200 characters) from general Kestrel words and a local name list, never from the evaluation sentences, plus a drop rule for the hint echoed back. On the first set of 21 recorded requests it helped Kestrel words but was worse overall (15.7% -> 19.7% normalised WER, overlapping intervals); it stays on as specified, behind `KESTREL_TALK_HINT`, until the unseen second set decides (known issue #49). **Decided (owner, 2026-10-24), by a rule fixed before set 2 was scored:** pooled over both sets (31 requests, 261 words) normalised WER was 14.18% without the hint and 17.62% with it, so `KESTREL_TALK_HINT` defaults to off; the code and the switch stay. **small.en (owner, 2026-10-24), by a rule fixed before scoring** (switch only if pooled no-hint WER drops 3+ points vs base.en and median latency stays under 2.0 s): pooled 12.26% vs 14.18% (-1.92 points), median 3.12 s vs 0.82 s; both fail, so base.en stays the default and small.en (190 MB, pinned) is an option.

