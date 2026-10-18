# Known issues (end of Phase 1)

An honest list of rough edges, shortcuts and known bugs, each written so it can be pasted into a GitHub issue. Severity: **high** = can give wrong results or weaken a safety property; **medium** = noticeable limitation; **low** = polish.

---

## Not yet verified

### 1. ~~Gemini and Groq have never been tested live~~ (closed 2026-10-04)
**Labels:** `verification`, `closed`
**Verified live:**
- **Groq** (`openai/gpt-oss-120b`, 2026-10-03): all six demo prompts pass, 1.8–3.7 s each, including approve, reject-then-revise and the prompt injection (ignored and flagged).
- **Gemini** (`gemini-3.6-flash`): all six demo prompts pass, 3.5–7.2 s each (1–3 on 2026-10-03, 4–6 on 2026-10-04 after the prompt changes), including the injection (ignored and flagged).
- **Fallback:** with an invalid Gemini key set for one command, Groq answered.
- **Mixed providers** (2026-10-04): Gemini made a tool call (with a thought signature) → Groq answered from that history (signature stripped) → Gemini accepted Groq's tool call carrying the `skip_thought_signature_validator` placeholder and still recalled the first turn. Replayed in `test_recorded_mixed_conversation_is_prepared_for_each_provider`.
- **Web console on Gemini** (2026-10-04, driven in Edge): a tool call, an approval clicked in the browser, and answers streamed as `text_delta` events.

**Fixed along the way (with regression tests from recorded responses):** the default Gemini model `gemini-3-flash` didn't exist (now `gemini-3.6-flash`, chosen by measured tool-calling and latency); Gemini's 429 wait time is read from the body (`RetryInfo`), not only `Retry-After`; server-guided rate-limit waits continue past 3 tries within a time budget (Groq's 8,000 tokens/minute); the last provider in a chain sits out longer waits; Gemini's 400 for a bad key reads as "API key rejected"; gpt-oss drafted emails/notes instead of calling the tool, fixed in the system prompt and the rejection message.

**Lasting constraint:** Gemini's free tier allows **5 requests/minute and 20/day per model**, so large runs (evals) belong on Groq. `scripts/live/` reruns all of these checks.

### 2. `docker compose up` has not been run on a developer machine
**Labels:** `verification`, `docker`, `medium`
Docker isn't installed on the development machine, so the image and compose file are verified only by the CI job (build, demo-mode start, `/healthz`, 401 without token, non-root user). Not yet verified: Docker Desktop on Windows, persistence across restarts, MCP servers starting inside the container (`uvx` downloads at startup), and reaching Ollama via `host.docker.internal`.
**Done when:** the Quickstart steps have been run on Windows with Docker Desktop, including Ollama on the host.

### 3. ~~List prices in `prices.toml` are unverified~~ (closed 2026-10-03)
**Labels:** `data`, `closed`
Every price was checked against the official pages (ai.google.dev/gemini-api/docs/pricing, console.groq.com/docs/model/openai/gpt-oss-120b) from the raw HTML; the date and sources are in `prices.toml`. Corrections: Groq gpt-oss-120b output was wrong ($0.75 → **$0.60**); `gemini-3-flash` was keyed under a model that doesn't exist (now `gemini-3-flash-preview`, $0.50/$3.00); added `gemini-3.6-flash` ($0.75/$3.75). A test now fails if a default model has no list price.
**Follow-up:** `gemini-3.6-flash` rises to $1.50/$7.50 on 2027-01-01; the entry says so, and needs updating then.

---

## Safety and privacy

### 4. "Safe" tools can still leak data
**Labels:** `security`, `high`, `phase-3`
A prompt injection can't make Kestrel *send* anything without approval, but it could get the model to put workspace contents into a `web_search` query or a `fetch__fetch` URL, both of which run without approval and contact the internet.
**Done when:** Phase 3 permission tiers distinguish "reads local data" from "talks to the network", and the injection suite includes exfiltration attempts.

### 5. `create_note` over MCP is approved by the client, not by Kestrel
**Labels:** `security`, `medium`
When Claude Code (or another MCP client) calls `kestrel-mcp`'s `create_note`, Kestrel's gate can't run; the client's own permission prompt is the approval. The call is logged as `approved-by-mcp-client`. Impact is limited (it only adds new files under `workspace/notes/`), but it's a different trust model.
**Done when:** the server asks via MCP elicitation where the client supports it, or `create_note` is removed from the exposed set.

### 6. Content-off mode doesn't cover the approval audit log
**Labels:** `privacy`, `medium`
`KESTREL_TRACE_CONTENT=off` stops message text and tool arguments being stored in traces, but `logs/approvals.jsonl` still records a summary of each risky call's arguments (long strings truncated, secrets redacted).
**Done when:** content-off also reduces audit entries to tool name, decision and time, with a test.

### 7. Redaction is pattern-based
**Labels:** `privacy`, `low`
Traces and the audit log redact known key formats, `NAME=value` assignments with secret-sounding names, and the actual values of secret environment variables. A key in an unknown format that isn't in the environment could still be stored.
**Done when:** documented as best effort (done here), and/or an entropy-based check is added.

---

## Correctness and robustness

### 8. Fallback after partial streaming can repeat text
**Labels:** `bug`, `low`
If a provider fails *mid-stream* after sending some text, `FallbackLLM` asks the next provider, whose text is streamed after the partial text. The final answer is correct; the live view shows both.
**Done when:** a `text_reset` event lets the UI discard the partial text, with a test.

### 9. Ollama sometimes drops tool calls when streaming
**Labels:** `provider-quirk`, `low`
Observed with qwen2.5:0.5b: about 1 in 3 streamed replies arrived empty (no text, no tool call) while the same request without streaming always worked. Kestrel now retries an empty streamed reply without streaming, which costs one extra request when it happens.
**Done when:** re-tested on newer Ollama versions; remove the workaround if fixed upstream.

### 10. Timed-out tools keep running in the background
**Labels:** `limitation`, `low`
Python can't kill a thread, so a tool that exceeds its 20 s timeout is abandoned (the agent moves on) but keeps running until it finishes.
**Done when:** long-running or untrusted tools run in a subprocess that can be terminated.

### 11. Token counts for history trimming are estimates
**Labels:** `limitation`, `low`
History is trimmed at ~4 characters per token. Real tokenizers differ, especially for code and non-English text, so the budget can be off by a wide margin.
**Done when:** the provider's reported usage (or a tokenizer) calibrates the estimate.

### 12. Approval cards attach to tool calls by name
**Labels:** `bug`, `low`, `console`
`approval_required` events don't carry the tool call id, so the console attaches each card to the newest unfinished call of that tool. Two parallel calls of the same risky tool could show their cards under the wrong row (the decisions themselves are still correct).
**Done when:** the gate passes the call id through to approvers and events.

### 13. Small local models are poor agents
**Labels:** `expectation`, `low`
Tested small models fail in instructive ways: deepseek-r1:8b claimed to use tools without calling them; qwen2.5:0.5b picked wrong tools and wrote `17.5% 2340` (remainder, not percent). This is the gap Phase 4's distillation is meant to close; for now, use Gemini or Groq for real tasks.

---

## Data and storage

### 14. Each trace stores the whole conversation
**Labels:** `performance`, `medium`
For export, every trace stores the full message history at that point, so long conversations grow storage quadratically. Fine for personal use; wasteful at scale.
**Done when:** traces store only their own turn plus a reference to the session, and export reassembles context.

### 15. No retention or rotation for logs
**Labels:** `ops`, `low`
`logs/traces.db`, `logs/approvals.jsonl` and `logs/mcp-*.log` grow forever.
**Done when:** a `kestrel prune --older-than 90d` command (or a retention setting) exists.

### 16. Docker bind mounts on Linux need write permission for UID 10001
**Labels:** `docker`, `low`
The container runs as user 10001. On Linux, if `./logs` or `./workspace` is created by Docker (as root) or owned by another user, the container can read but not write: traces silently fail to save (with a warning) and notes can't be created. Docker Desktop on Windows/macOS isn't affected. CI works around it with `chmod 777 logs`.
**Done when:** the README documents `mkdir logs && chown 10001 logs` for Linux, or the image adjusts ownership at startup.

---

## Console

### 17. Refreshing the page starts a new conversation
**Labels:** `console`, `medium`
One WebSocket is one conversation; a reload or reconnect loses the visible chat and the agent's history (traces are kept).
**Done when:** sessions can be resumed from their `session_id`.

### 18. No frontend tests
**Labels:** `testing`, `medium`, `console`
The console has typecheck, lint and build in CI, and was exercised end-to-end with Playwright for screenshots (`scripts/demo_console.py --shots`), but there are no automated frontend tests, and the event reducer in `useChat.ts` is the most logic-heavy untested code.
**Done when:** unit tests for `reduce()` and the Playwright demo run in CI.

### 19. Editing in the terminal approver's external editor is untested
**Labels:** `testing`, `low`
The `[e]dit` option's "open in your editor" path (`$EDITOR` / Notepad) has never been exercised; retyping is tested.

---

## Evals (Phase 2)

### 21. The judge isn't calibrated yet
**Labels:** `evals`, `medium`
The tooling is ready, but no human labels exist yet, so the judge's agreement with a person is unmeasured. Until then, rubric-graded results (43 of 100 tasks) are provisional and the README says "judge not yet calibrated".

**Tooling:**
- `kestrel bench label`: pass/fail/skip, judge verdict hidden, terminal only.
- `kestrel bench calibrate`: agreement with interval, Cohen's kappa, lenient-vs-strict confusion matrix, per category, every disagreement.
- A fixed, stratified dev/held-out split: reported numbers come from held-out.
- `JUDGE_VERSION`, pinned by a test.
- `kestrel bench rejudge`: re-grades stored answers only.

**Known evidence:**
- In the first baseline, `forbid-clear-by-overwrite` got 0 because "the response neither requests an approval-gated write_file", although the tool log shows the model did request it (rejected by the scripted user). The judge seems to miss rejected calls.
- The judge is also the model under test (self-grading). Candidate replacement: `qwen/qwen3.8-27b` (different family, own free quota). A 3-answer smoke test parsed cleanly and graded that task 1, but it is not yet compared on labels.
- It errs both ways. A review of 15 sampled passes (`evals/reports/baseline-pass-review.md`) found 2 of the 9 judge-graded ones lenient: an overclaimed capability, and unsupported details plus a misreported source in a web answer.
- It sees only the first 600 characters of each tool result, so it can't check faithfulness against the rest. Raising the cap to 2,000 is proposed as judge v2; not applied.

**Done when:** the owner has labelled answers in both halves (aim for 20+ per half; label repeats to get more than the 43 rubric tasks), and the README reports held-out agreement and kappa, with kappa ≥ 0.6, or the judge prompt (improved on dev only) or judge model is changed until it is.

### 22. CI threshold not yet derived from measured variance (partly fixed)
**Labels:** `evals`, `low`
**Fixed:**
- **(2026-10-04)** Every score has a 95% bootstrap interval over tasks. `kestrel bench compare` gives a paired interval, the tasks that flipped, and a non-inferiority verdict.
- **(2026-10-06)** Run-to-run variance measured on the full suite: two runs of the same agent (identical system prompt, model, tools and settings, verified from the traces), both suite v1.1 and judge v2, all 100 tasks (`evals/reports/run-to-run.md`).
  - 92% vs 93%, difference **+1 point (95% CI −5 to +7)**.
  - **9 of 100 tasks changed outcome.** The flips come from two habits that hit different tasks each run: skipping the calculator, and drafting before sending.
  - The same picture holds without the tasks run during a duplicate job, and without the live-world tasks.

**Still open:** the CI gate (75% on the 16-task subset) is a margin, not derived from the measured variance of that subset. It needs `--repeat` runs of the CI subset (about 40k billable tokens for 3 repeats).
**Done when:** the CI threshold is derived from repeats of the subset and documented.

---

## Distillation (Phase 4)

(Numbered after the Phase 3 branches' issues, which reach 29.)

### 30. The safety split's prompts are read from a pinned commit until Phase 3 merges
**Labels:** `distillation`, `evals`, `low`
The safety split isn't on main yet. Since step 3, the overlap check and `kestrel distill build` read its prompts and fixtures with git from the commit pinned in `src/kestrel/distill/bench_sources.toml` (`6f58ee5…`, tag `safety-before`), and fail loudly if the tag no longer resolves to it. Remaining gap: a clone without that commit (e.g. a shallow CI checkout) uses the files on disk only; the report and the manifest then say so.
**Done when:** Phase 3 is merged (the safety split is on disk everywhere) and the pin is either kept as a second source or removed on purpose.

### 31. The secret scrub only finds what it can recognize
**Labels:** `security`, `distillation`, `medium`
Missed: a canary payload without its `KCAN` prefix that is split or encoded in other ways (ROT13, reversed, spelled out), secrets with no known key pattern that aren't in the environment at build time, and personal data that isn't a secret (names, email addresses, phone numbers). The canary decoder is a small copy of `bench/canaries.py` from the Phase 3 branches, which main doesn't have yet.
**Done when:** after the Phase 3 merge, the scrub uses `bench/canaries.py` (one decoder) with the safety split's canary payloads, and a sample of kept examples is read by hand before the first fine-tune.

### 32. Distill data needs a run
**Labels:** `distillation`, `medium`
The distill task set (`distill/`, 122 tasks, `d1.2`) is the source of graded training data, but it hasn't been run yet: about 253k billable Groq tokens, 2 days (`distill/README.md`), after the Phase 3 merge (`distill/COLLECTION.md`) and with the owner's OK for quota.
**Fixed in step 3:** reworded KestrelBench prompts in chats. `kestrel distill build` now drops any example whose user message is a near-duplicate of a bench prompt (`near_duplicate`, the overlap check's fail thresholds: word 3-gram Jaccard >= 0.23 or a shared run of >= 5 words), including chats rated /good.
**Done when:** the distill set has been run on the teacher and built into a dataset.

### 33. The distill set's rubric tasks would be judged by the teacher itself
**Labels:** `distillation`, `evals`, `medium`
42 of the 120 distill tasks have a rubric. With the default `--judge groq`, gpt-oss-120b would grade its own answers, and only passed runs become training data, so its self-preference bias (docs/kestrelbench.md, "Judge independence") would pick the examples.
**Mitigated (step 3):** `grading = "checks-only"` (`src/kestrel/distill/grading.toml`, the default) keeps only the tasks graded entirely by checks (78 in step 3, 93 of 122 since step 4); rubric tasks are dropped (`rubric_task`) whatever their status, and collection runs with `--judge none`. Their stored answers can be graded later with `kestrel bench rejudge`, without re-running Kestrel. **Shrunk (step 4):** 13 rubric tasks whose every line has a deterministic check were converted (new check `answer_has_tool_time` for time answers) and two check-only time tasks added: time 0 -> 6 usable tasks, safety 2 -> 10. Still rubric-only: 29 tasks (yes/no judgments about the time, "does not invent" lines, open-ended writing, and 5 of 7 web tasks), listed in design decision 22.
**Done when:** an independent judge (candidate `qwen/qwen3.8-27b`) is calibrated on human labels (#21), listed in `calibrated_judges`, and the rubric answers are re-graded with it.

### 34. The near-duplicate filter also drops some ordinary chat
**Labels:** `distillation`, `low`
The pipeline's near-duplicate filter compares every user message with every KestrelBench prompt. Generic requests that share a bench prompt's wording are lost from training: of 12 everyday requests in `evals/reports/distill-overlap.md`, 4 are dropped (2 exact matches such as "What time is it in Tokyo?", 2 near-duplicates), and any "What time is it in <city>?" is dropped by its 5-word run. Intended: KestrelBench stays a fair test, and the distill set covers the same behaviors in other words.
**Done when:** a review of a real chat export shows how much is lost; if it matters, the filter could ignore runs made only of very common words.

### 35. ~~The training script has only run on CPU, without 4-bit~~ (superseded by #40, 2026-10-18)
**Labels:** `distillation`, `medium`
`distill/train.py --smoke` runs end to end on CPU (tiny random Qwen3, real tokenizer, no 4-bit), and the formatter is tested on the real tokenizer. The GPU path (bitsandbytes 4-bit loading, `prepare_model_for_kbit_training`, float16 on a T4) has not run yet, and Kaggle's preinstalled torch may not match the pinned transformers/trl. The P100 may not support bitsandbytes' 4-bit kernels (use a T4).
**Done when:** the first Kaggle run on a real dataset finishes, its `lineage.json` is checked (`distill/TRAINING.md`, step 5), and any version fix is pinned.

### 36. Groq's Services Agreement §6.3(e): provisional reading (a), waiting for Groq
**Labels:** `distillation`, `process`, `medium`
Customers own Inputs and Outputs (§8.1) and gpt-oss-120b is Apache-2.0 with no output restrictions, but §6.3(e) bars using the Cloud Services to "develop or improve products, services, or other offerings that are similar or compete" with them (agreement last modified 2026-06-22). **Provisional reading (a):** a small locally served student for a personal agent is neither similar to nor competing with Groq's inference platform. The owner emailed Groq support on 2026-10-18 for written confirmation and is waiting for the reply. `distill/COLLECTION.md` is blocked until the owner confirms, and its first lines say so. Fallback if the answer is no: gpt-oss-20b served locally by Ollama as the teacher (design decision 19).
**Done when:** the owner confirms (with Groq's written reply, or by accepting (a) explicitly), or switches to the fallback teacher.

### 38. Ollama's own qwen3:4b-instruct-2507 template garbles tool schemas
**Labels:** `distillation`, `serving`, `medium`
Found by the template parity check (distill/EXPORT.md): with Ollama 0.40.6, the template in Ollama's own `qwen3:4b-instruct-2507` package prints each tool as a Go struct dump (`{read_file Read a file. {object <nil> <nil> [path] {...}}}`) instead of JSON, because `{{ .Function }}` has no JSON form in that version. It also adds a newline before `<tool_call>` and gives each tool result its own turn. Any comparison that serves the **base** model through that package (e.g. "base vs student" in Phase 4) would handicap the base model. The student is served with Kestrel's template, which is checked byte for byte.
**Done when:** the base model is served for comparisons with Kestrel's template too (the same Modelfile on the base GGUF), or Ollama fixes its template.

### 39. Ollama drops tool-schema keys it doesn't know (e.g. `default`)
**Labels:** `serving`, `low`
Ollama's tool types keep only anyOf, type, items, description, enum, properties and required. Kestrel's schemas use `default` twice (e.g. `web_search` max_results); no model served by Ollama ever sees those defaults. The student is trained on the same reduced schemas, so training and serving agree.
**Done when:** the defaults that matter are stated in the descriptions, or Ollama keeps the key.

### 40. The GPU path and the export of a real adapter haven't run yet
**Labels:** `distillation`, `medium`
Run on CPU: `train.py --smoke`, and the whole export chain on its adapter (merge, GGUF, Q4_K_M, `ollama create`, 20/20 token counts). Not run: `train.py --gpu-smoke` and a real training on Kaggle (4-bit, float16 on a T4, Kaggle's preinstalled torch), and an export of the real 4B adapter (needs the ~8 GB base download).
**Done when:** the GPU smoke test passes on Kaggle and a real adapter is exported with `--verify` 20/20.

---

## Process

### 20. ~~`.env` hygiene on the development machine~~ (closed 2026-10-04)
**Labels:** `security`, `closed`
During Phase 1, real-looking keys ended up in `.env.example` (tracked) and were staged by accident twice before being caught; `.env` itself held Python code instead of `KEY=value` lines. Both are local only and were never committed (gitleaks finds nothing in the history). The pre-commit gitleaks hook now blocks this.
**Done when:** the exposed keys are revoked, `.env.example` is restored to empty values, and `.env` holds only `KEY=value` lines.
**Resolution 2026-10-04:** `.env.example` is back to empty values, `.env` holds only `KEY=value` lines, the Groq key is new, and the unused xAI key was revoked. The owner chose to keep the current Gemini key: it was only ever in local, uncommitted files (gitleaks finds nothing in the git history) and works. The pre-commit gitleaks hook blocks a repeat.
