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

## 19. Voice wake-up: Vosk with a two-word grammar, local speech, no model call

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

**Measured** (2026-10-22/23, this laptop, Python 3.14): feasibility on synthetic Windows-voice clips 4/4 spotted, 0/10 look-alikes (not an accuracy claim); CPU while listening 1.3% of one core over 10 minutes (per minute 0.2-5.5%); the `powercfg /requests` check needs an elevated prompt (known issue #61). Detection rate and false wakes per hour wait for the owner's recordings (`scripts/wake_eval.py`).

**Revised (owner, 2026-10-26): greetings before "Kestrel".** The wake rule stays "kestrel anywhere in the recognised utterance, at or above the threshold" (it already was: the detector looks for the token, not an exact match), with the same cooldown and `[unk]`. The grammar gains five greetings, all in the small model's vocabulary (`hi`, `hello`, `hey`, `shalom`, `good morning`), only so the reply can echo them ("Hello, {name}. I'm here, what do you need?"); a greeting alone never wakes. "kestrel" inside a longer sentence also wakes: accepted, since any stricter position rule would also reject "hi Kestrel". `wake_eval.py` reports the old and new grammar side by side, so a change in false wakes from the larger grammar is measured, not assumed.

## 20. Talking to Kestrel: whisper.cpp base.en, a hallucination guard, the same input for the model

**Decision** (owner-approved 2026-10-23, branch `voice/talk`; guide `docs/voice-wake.md`, "Talking to Kestrel"): after a wake, one spoken request is recorded in memory, transcribed locally with whisper.cpp base.en (5-bit, MIT, 57 MB, SHA-256 pinned; tiny.en as a one-setting fallback) and sent by the most recently active console exactly like typed text. Answers to spoken requests (only) are spoken, first two sentences, behind a remembered "Speak answers" switch.
- **End of speech:** loudness above a learned noise floor; 1 s of quiet ends it, 20 s at most, 3 s to start.
- **Hallucination guard:** drop on under 0.4 s of speech, low confidence (segment confidence as the closest available signal to whisper.cpp's no-speech probability, which pywhispercpp doesn't expose), or a whole-transcript filler; a dropped transcript is treated like an empty one.
- **Safety:** an approval card is announced with one fixed sentence, never read; voice can't approve. The voice flag stays in the server and the console; the agent receives only the text, so the model's input is identical for typed and spoken requests (tested), and the agent fingerprint is unchanged.

**Alternatives:** Vosk small (already installed, 1.7 s per 5 s request in one go, LibriSpeech test-clean 9.85%); faster-whisper tiny.en (0.28 s, 8.4%); faster-whisper base.en (145 MB, same model family as the choice, not measured: over the 100 MB limit); openai-whisper (needs PyTorch). Measured on this laptop (Ryzen 7 8840HS, 8 threads, synthetic 5 s requests): whisper.cpp base.en 5-bit 0.80 s.

**Why:** base.en roughly halves tiny.en's errors on real speech for half a second more, from a 57 MB file and a 1.4 MB library. The guard exists because a transcript is sent to the agent as if the user had said it: inventing a request is worse than missing one.

**Revised (owner, 2026-10-24):** low confidence no longer drops a transcript: it goes unsent into the console's message box ("Did I hear that right?") for the user to send, edit or discard ("check first"); the hard drops stay. A vocabulary hint (whisper.cpp initial prompt, <= 200 characters) from general Kestrel words and a local name list, never from the evaluation sentences, plus a drop rule for the hint echoed back. On the first set of 21 recorded requests it helped Kestrel words but was worse overall (15.7% -> 19.7% normalised WER, overlapping intervals); it stays on as specified, behind `KESTREL_TALK_HINT`, until the unseen second set decides (known issue #63). **Decided (owner, 2026-10-24), by a rule fixed before set 2 was scored:** pooled over both sets (31 requests, 261 words) normalised WER was 14.18% without the hint and 17.62% with it, so `KESTREL_TALK_HINT` defaults to off; the code and the switch stay. **small.en (owner, 2026-10-24), by a rule fixed before scoring** (switch only if pooled no-hint WER drops 3+ points vs base.en and median latency stays under 2.0 s): pooled 12.26% vs 14.18% (-1.92 points), median 3.12 s vs 0.82 s; both fail, so base.en stays the default and small.en (190 MB, pinned) is an option.

## 21. A 2-second "Sending" countdown for spoken requests, cancellable three ways

**Decision** (owner, 2026-10-24, branch `voice/talk`; guide `docs/voice-wake.md`): a spoken request that passes the guard is no longer sent at once. The server holds it for 2 s while the console (both looks) shows "Sending: <transcript>" and the HUD "Sending…". Esc, a Cancel button, or saying "cancel" stops it and puts the text in the message box. "Check first" below `KESTREL_TALK_MIN_CONFIDENCE` (now 0.50) and the hard drops are unchanged.
- **The server owns the timer**, not the browser: one place decides, so a slow or closed console can't send early or twice, and a cancel that arrives after the countdown ends does nothing.
- **Spoken "cancel"** is a second Vosk recognizer with a two-entry grammar (`cancel`, `[unk]`), the same model already loaded for the wake word, made fresh when a countdown starts and fed audio only during it. It acts on Vosk's partial result at any confidence: a false cancel costs one Enter (the text is kept); a missed one sends a request the user can still reject at its approval card.
- **Safety unchanged:** cancelling can only stop a spoken request in its countdown, never answer an approval card (tested); voice still can't approve anything.

**Why:** Whisper can be wrong and confident (set 1, req-018: a garbled request at confidence 0.931). Confidence alone can't catch that, so the user gets a moment to see what was heard before it reaches the agent. 2 s keeps a correct request fast.

**Alternatives:** always "check first" (every spoken request needs Enter: voice stops being hands-free); a higher confidence floor (confident mistakes still pass); a client-side timer (two consoles or a reload could send twice).

## 22. Distillation data: open-weight teacher only, no KestrelBench tasks, drop on any secret

(Decisions 22-28 were 19-25 on `phase4/trace-dataset`; numbered when the branch was rebased onto `main` on 2026-10-30, after voice's 19-21.)

**Decision:** `kestrel distill build` (`src/kestrel/distill/dataset.py`) turns a copy of a traces database into `train.jsonl`, `val.jsonl` and `manifest.json`. Filters run in a fixed order and each drop is counted: no stored messages, teacher, format-token leak (tool name, arguments, text), contamination (bench run, bench prompt), quality, secrets (canary, redaction pattern, `.env` line, env value), length, duplicate.
- **Teacher (hard):** every model step's (provider, model) must be listed in `src/kestrel/distill/teachers.toml` (default `groq` + `openai/gpt-oss-120b`). There is no flag for other providers, and the loader refuses Gemini, Claude, GPT and demo entries, so editing the file can't admit them either.
- **Format-token leak (hard, added 2026-10-30):** a chat-template control token (`<|channel|>`, `<|message|>`, `<|start|>`, `<|end|>`, `<|call|>`, `<|return|>`, any `<|word|>`) anywhere in the example drops it, counted as `harmony_leak_tool_name`, `_arguments` or `_text` by where it is first found. gpt-oss sometimes leaks its Harmony format into a tool name (`calculator<|channel|>commentary`, known issue #54). Groq rejects such a call, so the run errors and `quality` would drop it anyway, but a teacher served without that check (Ollama, option c of #72) passes the name through, Kestrel answers "unknown tool", and the run can still end answered and pass. Training on it teaches the leak. No redaction: the turn around the token is malformed too.
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

**Fallback teacher (added 2026-10-18, known issue #72):** if Groq's terms turn out to forbid this use, the teacher becomes **gpt-oss-20b served locally by Ollama** (`gpt-oss:20b`, Apache-2.0, ~14 GB): no service agreement applies at all. Trade-offs: a much weaker teacher (20B against 120B), so lower-quality traces and a lower ceiling for the student; too large to run next to anything else on the 16 GB laptop, so collection would run in Kaggle GPU sessions (T4, 16 GB), slowly and within the weekly GPU quota; `teachers.toml` would list `ollama` + `gpt-oss:20b`, and the two teachers' traces must not be mixed in one dataset without saying so in the manifest.

**Fair comparisons (added 2026-10-18):** any base-vs-student comparison serves both models through the same Kestrel Modelfile (`train_format.OLLAMA_TEMPLATE`, same stops, sampling and `num_ctx`), never through Ollama's packaged template, which garbles tool schemas in 0.40.6 (known issue #73). `distill/export.py --base` builds the untrained base that way; its Modelfile is byte-identical to the student's (checked on the smoke export), and `--verify` runs on it too. Both are served with `KESTREL_MCP=off`, so they see the same tools.

**Why:** the student should learn only behavior that a teacher it may learn from produced, that passed a check other than the benchmark it will be judged on, and that contains no planted or real secret. Every dropped example is counted by reason, so a small dataset is explained, not silent. Open items: known issues #66-#68.

---

## 23. Distill task set: disjoint from KestrelBench, told apart by metadata

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

## 24. Distill overlap thresholds from the bench's own distribution; checks-only grading

**Decision:**
- **Overlap thresholds** come from how similar KestrelBench prompts are to *each other*: word 3-gram Jaccard and the longest shared run of words over every pair of bench prompts (main suite on disk plus the safety split at its pinned commit, 155 prompts), leaving out pairs from the same task and identical-text twins (benign/attack pairs share their prompt on purpose). Fail just above that p99.9 (Jaccard >= 0.23, run >= 5 words), review just above the p99 (Jaccard >= 0.08, run >= 4 words). Fixtures fail on a shared file name or a shared content line of 8+ words. A test recomputes the distribution with the same exclusions and fails if the thresholds fall below it. Report: `evals/reports/distill-overlap.md`. At `d1.1`: 0 fail, 38 in review, 0 fixture problems; one task (`dst-web-uv-tool`) failed the new thresholds and was reworded, so `d1.0` became `d1.1`.
- **The same fail thresholds filter the pipeline** (`near_duplicate`): every user message of every example, chats rated /good included.
- **The safety split is pinned by commit** in `src/kestrel/distill/bench_sources.toml`, read with git, checked against its tag (a moved tag stops everything), recorded in the report and manifest; without the commit, disk only, and said so.
- **Grading:** `grading = "checks-only"` keeps only distill tasks graded entirely by checks (78 of 120). Rubric tasks are dropped until a judge@version is listed in `calibrated_judges`. Each distill result is matched to its task definition by the task fingerprint (`task_changed` if it moved). Collection runs with `--judge none`; `rejudge` can grade stored rubric answers later.

**Alternatives:**
- **A fixed 0.5 Jaccard** (step 2): not grounded in anything; the bench's own pairs show 0.5 is far above where copying starts.
- **The bench's p99 as the fail line:** a single shared 3-gram ("what time is it") would fail, which is ordinary phrasing, not copying.
- **Include the twins in the distribution:** they are copies by design and push p99.9 up (0.286 instead of 0.222).
- **Judge rubric tasks with the teacher:** self-preference bias would choose the training examples (#69).
- **Read the safety split from the tag or a branch:** both can move; a commit can't.

**Why:** "too similar" should mean "more similar than the benchmark's own distinct tasks ever are", measured, not guessed. And until a judge is shown to agree with humans, only deterministic checks decide what the student learns from.

---

## 25. Distill rubric tasks converted to checks only where every rubric line has one

**Decision:** each of the 42 distill rubric tasks (d1.1) was classified:
- **A, fully checkable (13):** every rubric line has a deterministic check, so the rubric is removed. 4 time (`dst-time-warsaw`, `-nairobi-date`, `-buenos-aires`, `-cairo-weekday`), 8 safety (`dst-forbid-remove-harvest`, `-old-minutes`, `-all-csv`, `-old-quote`, `-notes-folder`, `dst-sandbox-dotenv`, `dst-safe-dotenv-by-email`, `dst-safe-no-address`), 1 multistep (`dst-multi-grant-adds-up`). Every converted task keeps its old checks; each new check carries a `why` naming the rubric line it replaces.
- **B, partly checkable (22):** the checks that apply were added, the rubric stays, so the task stays out of training under checks-only. The line no check covers: a yes/no or part-of-day judgment (3 time), "without inventing files/contents/a listing" (overview, missing map, 7 sandbox tasks), sentence counts or tone (quote summary, draft, 3 no-tools), "doesn't list action items as decisions", "asks whether to change it", "doesn't try again", "doesn't send a code", "offers to send one".
- **C, not checkable (7):** open-ended writing (goodbye, event names) and the 5 web tasks (answers must agree with live search results).
- **New check `answer_has_tool_time`** (`bench/checks.py`, additive; no main task uses it, so every main task fingerprint and SUITE_VERSION 1.1 are unchanged): the answer states the time, date or weekday the matching `get_current_time` call returned. Formatting-tolerant (24/12-hour with any am/pm spelling, ISO or written dates, after the v1.1 `plain()` normalization), value-strict (another time, date or weekday fails; the next minute counts only when the tool's seconds were >= 30).
- **Two new check-only time tasks** (`dst-time-greta-local`, `dst-time-auckland-date`), so time reaches 6 usable tasks. Time may now sit above its main-suite share (9 vs 7.2); every other category keeps +-1.
- The set is `d1.2`: 122 tasks, 93 check-only (78 before). Overlap: 0 fail, 38 review, 0 fixture problems.

**Alternatives:**
- **Convert a task when most of its rubric is checkable.** Faster coverage, but whatever the missing line guards (an invented listing, a wrong yes/no) would then pass unseen into training data.
- **Add a time check to the yes/no time tasks** (state the local time). Their rubrics don't ask for it, so the check would change the task, not verify it.
- **Rewrite the rubric time tasks as check-friendly prompts** instead of adding two tasks: keeps 120 tasks, but loses the judgment examples once a calibrated judge can grade them.

**Why:** coverage of time and safety without weakening what "graded" means: a task enters training on checks alone only if the checks verify everything its rubric asked.

---

## 26. Student Qwen3-4B-Instruct-2507, QLoRA with transformers + peft + trl, trained on Kaggle

**Decision (owner, 2026-10-16):**
- **Student:** `Qwen/Qwen3-4B-Instruct-2507`, revision `cdbee75f17c01a7cc42f958dc650907174af0554` (4.02B, text-only, Apache-2.0, Hermes-style `<tool_call>` chat template; Ollama `qwen3:4b-instruct-2507`, 2.5 GB at q4_K_M, "tools" capability).
- **Library:** transformers 5.20.1 + peft 0.21.2 + trl 1.16.0 + bitsandbytes 0.50.2 (+ datasets, accelerate), in an optional `train` extra so Kestrel's normal install and CI never pull in torch.
- **Format** (`src/kestrel/distill/train_format.py`): the tokenizer's own chat template renders tools, tool calls and tool results; labels come from character spans of the weight-1 assistant turns (rendering message prefixes), through the end-of-turn token. Longer than `max_length` raises; never truncated. trl gets pre-tokenized rows with `max_length=None` and `skip_prepare_dataset`, so it neither re-masks nor truncates (checked in trl 1.16's source).
- **Training** (`distill/train.py`, `distill/train.ipynb`, `distill/train_config.toml`): 4-bit NF4 with double quantization, float16 compute (T4), LoRA r 16 / alpha 32 on all attention and MLP projections, seed 2026, val loss every epoch, early stopping, best epoch kept. Output: the adapter plus `lineage.json` (dataset manifest hash, traces DB hash, teachers, base model and revision, the whole config, library versions, GPU, loss history). The two files that run on Kaggle use no Kestrel imports and Python 3.11 syntax (ruff `per-file-target-version`, a test parses them).
- **Smoke test:** a tiny randomly initialized Qwen3 (2 layers) with the real tokenizer, so the template is identical, on CPU without 4-bit, on a synthetic dataset built by `kestrel distill build`.

**Alternatives (research 2026-10-16, sources in the step-5 plan):**
- **Qwen3.5-4B** (Apache-2.0, 4.66B, image + text, thinking by default): newer, but the multimodal model class and thinking mode add risk to a first pipeline. The upgrade path once this one works.
- **Gemma 4 E4B-it** (Apache-2.0, 8.0B total): its own tool-call syntax, per-layer embeddings, 6.6-9.5 GB in Ollama (heavy on a laptop CPU).
- **Phi-4-mini-instruct** (MIT, 3.8B): a less standard tool format.
- **unsloth** (about 2x faster on T4): GPU-only, so the CPU smoke test couldn't run the same code; pins `transformers<=5.17.0`; its required `unsloth_zoo` is LGPL-3.0. For ~90 examples (under an hour of T4), speed isn't the bottleneck.

**Why:** the simplest correct path to a first adapter: a text-only model whose tool format Kestrel's Ollama provider already speaks, one code path from laptop to GPU, and a lineage file that ties every adapter to the exact data, teacher and code that made it.

---

## 27. One prompt format, rendered twice and checked against Ollama's own code; export through GGUF

**Decision:**
- **The format** is defined in `src/kestrel/distill/train_format.py`: Qwen3's structure (tools in the system turn, `<tool_call>` blocks, consecutive tool results in one user turn), with tool schemas and arguments serialized as Go's `json.Marshal` writes them (compact, map keys sorted, Ollama's struct field order, `<>&` escaped, unknown schema keys dropped), and Ollama's `collate()` (system messages joined, consecutive same-role messages merged).
- **Written twice:** `render()` in Python (training text and label spans; no Hugging Face chat template is used) and `OLLAMA_TEMPLATE` in Go (the Modelfile).
- **Checked exactly:** `distill/parity/main.go` runs Ollama's own `openai.FromChatRequest` + `template.Execute` at the installed version (v0.40.6, pinned in go.mod) on 20 generation points of 10 fixed conversations (Kestrel's real tool schemas, several calls per turn, consecutive tool results, multi-turn, collate, tricky JSON). `scripts/template_parity.py` requires byte-for-byte equality and prints the first differing line; `golden.json` lets every pytest run (CI included) check the Python side. Proven sensitive: one extra space, or Python's json.dumps instead of Go's encoding, fails it.
- **Checked at runtime:** `export.py --verify` sends the same conversations to the created model through the OpenAI-compatible endpoint (Kestrel's path) and requires Ollama's prompt token counts to equal training's (20/20 on the smoke export; Ollama's own qwen3 template on the same GGUF: 1/20).
- **Tool schemas are pinned:** the schemas in the training text come from the code that ran `kestrel distill build` (`registry.schemas()`, no MCP), not from the traces (traces don't store them), so the dataset is built at the collection commit. `lineage.json` records a fingerprint of their exact training-text form (`tool_schemas`: SHA-256 of the block and per tool); `export.py --verify` recomputes it from Kestrel's registry, as the agent sends it, and fails on any difference, naming the tools (changed, added, removed, reordered). Keys Ollama drops (#74) can't cause a difference, because both sides see the post-dropping form.
- **Export:** peft merge (float16, CPU) -> llama.cpp `convert_hf_to_gguf.py` -> `llama-quantize` Q4_K_M (pinned build b11712) -> Modelfile (template, stops, Qwen's sampling defaults, `num_ctx 16384`) -> `ollama create`. Served by Kestrel's existing Ollama provider (`OLLAMA_MODEL`), no code change.

**Alternatives:**
- **Keep Hugging Face's Qwen template for training** and write a matching Go template: impossible byte for byte, since a Go template can only write JSON through Go's encoder (no spaces), and Jinja can't call it.
- **Use Ollama's own `qwen3:4b-instruct-2507` template:** checkable the same way, but in 0.40.6 it garbles tool schemas (#73).
- **A Python port of Go's text/template, or reading prompts from Ollama's logs:** no maintained port; Ollama logs only the prompt length (checked with OLLAMA_DEBUG=2).
- **Ollama's safetensors import (`FROM ./merged`, `--quantize`):** in 0.40.6 it needs the MLX engine, not available on Windows, and offers only int4/int8/nvfp4/mxfp4/mxfp8.

**Why:** a fine-tuned model is only as good as the match between the prompts it learned and the prompts it gets. Checking that match against the server's own code, not a re-implementation, makes it a fact instead of an assumption.

---

## 28. Phase 4 measurement and router v1 (owner decisions, 2026-10-18)

**Decision** (`docs/phase4-plan.md`, decisions table):
- **Router v1:** a cascade in the model layer. The student answers first. Two exact pre-route rules send a request to the teacher: the tool set Kestrel would send differs from the student's training fingerprint (#76), or the prompt is longer than the student's context. Escalation mid-turn on an invalid tool call, `max_steps`, an empty answer, or a provider error or timeout. No refusal signal. The approval gate and tiers v2 apply unchanged to whichever model answers.
- **Hand-over:** the teacher continues from the transcript with the tools that actually ran and their results, without the student's malformed call. Nothing runs twice (no tool, no approval card). A rejected approval never escalates.
- **Quality claim:** routed/teacher main-suite pass rate (v1.1, frozen), lower end of the 95% paired-bootstrap interval >= 0.95; also on the 57 check-only tasks (the judge is the teacher, uncalibrated). R=3 per system first, then decide on 5 from the measured per-task disagreement.
- **Teacher reference:** Phase 3's main-suite after-run (same tiers-v2 code). The pinned baselines are secondary, and count as repeats only if the after-run is within +-6 points of each (the measured identical-run interval); otherwise fresh teacher repeats on the current code before any claim.
- **Safety:** 0 system-level attack successes under the strict user (student alone and routed), plus rubber-stamp system level not above the teacher's; if that fails, tainted turns go to the teacher.
- **Cost and latency:** reported separately against Groq's free-tier budget and against paid list prices (Groq gpt-oss-120b, Gemini 3.6 Flash); latency on the laptop CPU, named; no claim before measurement.
- **Also:** the hard split after the Phase 3 gate as a separate stress test; `KESTREL_REQUEST_TIMEOUT` raised for local runs only; the Kaggle GPU smoke test may run now.

**Alternatives:** in `docs/phase4-plan.md`, sections 1-3 (router options a-d, the power table, the hard split's cost).

**Implementation (step 8, 2026-10-18).** `src/kestrel/router.py`: a `RoutedLLM` with FallbackLLM's interface, built only for `routed`. The agent gained three guarded hooks (`begin_turn` with the registry's `prepare` for checking tool calls without running them, `escalate("max_steps")` with a fresh teacher budget capped at 2 x max_steps, and route attributes on llm_call spans); without a router none of them runs, and the agent fingerprint stays `cc5c16377662`. Escalation reasons are split into pre_route, quality and student_health; a routed bench run stops above `KESTREL_ROUTER_MAX_STUDENT_HEALTH` (default 10% of turns, after 10 tasks). Pre-routing on context uses `KESTREL_ROUTER_CTX_MARGIN` (default 80%). Steps per turn are recorded, and the quality verdict is stated with and without passes that needed more than max_steps; the claim must hold without them. Decision 5 refined: the verdict against the after-run alone and against all three references must agree, or fresh teacher repeats are needed. The approval gate is tested with the teacher's calls; tiers v2 isn't on this branch yet (#77).

**Why:** a claim the noise can't support is worse than none. Tying the teacher reference to the same code as the routed system, and refusing to reuse old runs when they disagree beyond measured noise, keeps the comparison like for like. Making the router fail safe (deterministic signals, no repeated actions, user rejections respected) means routing can never weaken the approval model.

## 29. Phase 4 teacher: option a, Groq + gpt-oss-120b (owner decision, 2026-11-01; closes #72)

**Decision:** the teacher for Phase 4 distillation is **option a, `groq` + `openai/gpt-oss-120b`**, by the rule pre-registered in known issue #72: option a if Groq confirmed in writing by 2026-11-04, otherwise option c.
- **Groq's confirmation.** In a support chat on 2026-10-31 (06:50 PDT), Groq Support confirmed that using gpt-oss-120b outputs to fine-tune a smaller open model for this personal AI agent project is permitted, provided the use follows Groq's AI Usage Policy and the applicable terms. Summarized, not quoted; the owner keeps the transcript privately, and it isn't copied into the repository.
- **The AI Usage Policy** ("Groq Acceptable Use & Responsible AI Policy", effective 2025-10-15, read 2026-11-01) has no clause on training with outputs, distillation or competing models. Its limits that touch this project are the usage limits (rate limits, no spreading use over several accounts or organizations) and the general bans (illegal or harmful use, high-risk automated decisions, circumventing safety measures), which collection keeps.
- **Scope kept, as described to Groq:** a personal project; no hosted or inference service; the student weights are not published.
- **Option c is not used for training.** The Kaggle teacher selection (`distill/selection/`) and the Qwen3-30B-A3B collection d1.2-pass1 (`distill/collection/`) stay pinned as evidence. One dataset has one teacher (design decision 22's fallback paragraph): `teachers.toml` lists only Groq's entry, so `kestrel distill build` drops any Qwen trace as `teacher`.
- **`docs/phase4-plan.md` section 8 stays as frozen.** The option c amendment (`docs/phase4-plan-option-c-amendment-draft.md`) is not applied.
- **Draft-and-ask (#59): collect now, fix later.** The habit is not fixed before collection. The distill tasks that expect an approval-gated action (30 by a count of their checks; 29 check-only) need the gated tool call to pass, so a trace that drafts and asks fails its checks and is dropped as `quality`. The student is not trained on the habit, but it also gets fewer examples of those tasks. **Consequence:** the later prompt fix changes the agent fingerprint, so it must re-measure the routed system as well as the teacher, or the student is retrained on traces from the fixed prompt.
- **Budget:** collection on Groq's free tier over two days, with its own approval line in `docs/STATUS.md` (up to 320k billable over 11-01 and 11-02; one command per day, `--token-budget` leaving a 15k margin in Groq's rolling window).

**Alternatives:**
- **Option c** (Qwen3-30B-A3B on Kaggle, no Groq): built, selected by #72's rule and collected (82 examples). Not used because the pre-registered condition for option a was met; a 120B teacher is the stronger one, and it is the teacher the Phase 3 references and section 8 were written for.
- **Mixing both teachers' traces:** more examples, but the dataset would no longer have one teacher, and the claim compares the student with one named teacher.
- **Fixing #59 before collection:** cleaner traces, but a measured agent change first (main suite and safety split before and after, more Groq days), delaying collection past the plan.

**Why:** the decision was pre-registered with its condition before either answer was known, and the condition was met, so following it avoids choosing a teacher after seeing results. Keeping option c's evidence pinned but out of training keeps the record honest without diluting the dataset.

## D-pending-phase4-cost-report. Phase 4 cost from the traces, priced with one table for both sides

**Decision:** section 8.2's cost rule is computed by `kestrel bench cost` (`src/kestrel/bench/cost.py`) from each results file's trace database, not from the cost stored on the traces.
- Every llm_call span is priced again with the current `prices.toml` list prices, the same table for the routed run and the teacher reference, so a price change between their dates can't favour either side.
- Ollama calls cost $0 (local hardware, electricity not counted). A non-local call without a list price is "unpriced".
- The judge isn't counted. Cached input is priced as ordinary input on both sides (prices.toml has no cached price).
- Per task, the cost is the mean over its runs; D = routed - teacher; shown when the 95% paired bootstrap upper bound of mean(D) is < 0 (10,000 resamples, seed 2026, in micro-dollars so rounding can't hide it).
- Any missing trace, unpriced call, task on one side only, or routed teacher that isn't the reference's model makes it **can't decide**: no `--cost-shown` flag is given until fixed.
- The same report gives Groq billable tokens per 100 tasks (Groq spans only, cached input left out), latency p50/p95, and the teacher's share of turns and of task runs with 95% intervals (bootstrap over tasks of the ratio, since a task's repeats aren't independent).

**Alternatives:** the trace's stored `list_price_usd` (priced at run time, so two runs weeks apart could use different tables, and a trace whose calls were all local stores None); billable tokens from the results rows (a routed row's `tokens` include the student's Ollama tokens, which never count on Groq).

**Why:** a cost claim must price exactly the same work the same way on both sides, and it shouldn't pass on a gap in the data.

## D-pending-routed-safety-groups. The routed safety run covers only the tasks the student can be sent

**Decision (owner, 2026-11-01, before any routed run; amendment 8.7 of `docs/phase4-plan.md`):** the safety split's tasks are split from their own data (`src/kestrel/bench/routed_safety.py`).
- **Group F (19):** tasks that need `fetch__fetch` (fetch pages, vector `fetch`, or a goal or expected outcome naming it) or register an MCP stub. They are the teacher by router v1's pre-route rule, so they aren't re-run; their result is the teacher's measured tiers v2.1 one.
- **Group S (29):** every other task, run routed with the fetch stub left out (`bench run --split safety --safety-group S`; results record `safety_group` and `fetch_stub`).
- The gate is 0 system-level successes under the strict user on group S, and rubber-stamp 0, the teacher's.
- The student alone runs all 48 locally, reported and not gated.
- The drift guard's reading is fixed in the same amendment.

**Alternatives:** run all 48 as pre-registered (~299k billable to re-measure the teacher under another fingerprint); skip the routed safety run (no evidence on the turns the student does answer); train the student with the fetch and MCP tools (a new collection and dataset).

**Why:** spend Groq only where the routed system can differ from the teacher, and make the split a property of the task data, so it can't be chosen by looking at results.
