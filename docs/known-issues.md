# Known issues (end of Phase 1)

An honest list of rough edges, shortcuts and known bugs, each written so it can be pasted into a GitHub issue. Severity: **high** = can give wrong results or weaken a safety property; **medium** = noticeable limitation; **low** = polish.

---

## Not yet verified

### 1. Gemini and Groq have never been tested live
**Labels:** `verification`, `high`
All live testing so far used local Ollama models and the demo provider; no Gemini or Groq key was available during development. Unit tests cover the request/response handling with fakes, but provider-specific behavior is unverified: Gemini's OpenAI-compatible tool calling, streaming with `stream_options.include_usage`, and especially the `skip_thought_signature_validator` placeholder used when Groq-made tool calls are sent to Gemini 3 after a fallback.
**Done when:** the six demo prompts and a fallback (Gemini rate-limited, Groq answers, then back) have been run against both providers, and any fixes have tests.

### 2. `docker compose up` has not been run on a developer machine
**Labels:** `verification`, `docker`, `medium`
Docker isn't installed on the development machine, so the image and compose file are verified only by the CI job (build, demo-mode start, `/healthz`, 401 without token, non-root user). Not yet verified: Docker Desktop on Windows, persistence across restarts, MCP servers starting inside the container (`uvx` downloads at startup), and reaching Ollama via `host.docker.internal`.
**Done when:** the Quickstart steps have been run on Windows with Docker Desktop, including Ollama on the host.

### 3. List prices in `prices.toml` are unverified
**Labels:** `data`, `medium`
The paid reference prices (Gemini 3 Flash, Gemini 2.5 Flash/Flash-Lite, gpt-oss-120b on Groq) were written from memory and are marked "verify". Every list-price number in stats depends on them.
**Done when:** each price is checked against the provider's pricing page, with the date recorded.

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

## Process

### 20. `.env` hygiene on the development machine
**Labels:** `security`, `chore`
During Phase 1, real-looking keys ended up in `.env.example` (tracked) and were staged by accident twice before being caught; `.env` itself held Python code instead of `KEY=value` lines. Both are local only and were never committed (gitleaks finds nothing in the history). The pre-commit gitleaks hook now blocks this.
**Done when:** the exposed keys are revoked, `.env.example` is restored to empty values, and `.env` holds only `KEY=value` lines.
