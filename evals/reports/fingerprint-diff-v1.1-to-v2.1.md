# Agent fingerprint diff: `cc5c16377662` (v1.1 baselines) -> `35fac5ba6277` (tiers-v2.1 after-run)

Written 2026-10-27 on `phase3/tiers-v2`, offline: no model calls, nothing read from `..\kestrel-runs`, no live
results read. Diagnosis only, nothing fixed. It serves the rule C "gate not met" consequence recorded in
`docs/tiers-v2-design.md` section 5 (2026-10-27): "diff the agent fingerprints, fix what changed besides the
cards". The habit column below is a **hypothesis to test later, not a finding**.

- `cc5c16377662`: main `c7c0d4f`, the fingerprint of run 2 (`evals/baselines/run2-v1.1-judge-v2-final.json`);
  the baseline file itself predates fingerprints (`evals/baselines/README.md`).
- `35fac5ba6277`: tag `safety-after-v2.1` = `75ce54c`, the runs folder of the live main-suite after-run x3.

## Summary

- **Both fingerprints reproduce** from the source trees.
- **Nothing in what the model is given before its first tool call changed:** the system prompt is
  byte-identical, and all 10 tool schemas (names, descriptions, parameters) and risk tiers are identical.
  The model name, context budget, tool timeout, result cap, request timeout and sampling are the same.
- The fingerprint moved for two reasons, neither of them sent to the model:
  - capability tags on 8 of the 10 tools;
  - the new `approval_rules: tiers-v2.1` field.
- The only change the model can see is **in the middle of a conversation**: a network call
  (`web_search`) made after a successful local read now goes to an approval card. That's the tiers-v2 card
  itself. In the main suite the scripted user rejects every card a task doesn't list, so the model gets a
  rejection where it used to get search results. Every such card shows up as `escalated_by` in the results.
- **Consequence for the "gate not met" path:** the code diff finds no change besides the cards to fix.
  Anything else would have to come from outside the code (section 5), which the fingerprint doesn't cover.

## 1. How the fingerprint is computed

`agent_fingerprint(provider, model, registry)` in `src/kestrel/bench/runner.py` hashes a dict with `sort_keys`
(SHA-256, first 12 hex characters). The fields:

| Input | `c7c0d4f` | `75ce54c` | Sent to the model? |
|---|---|---|---|
| `provider`, `model` | groq, openai/gpt-oss-120b | same | the model is the endpoint |
| `system_prompt` (hash of `SYSTEM_PROMPT`) | `36d57c7e8a82` | `36d57c7e8a82` | yes |
| `tools` (hash of the sorted tool list) | `22ab98e45865` | `ede3efa38c54` | partly: see below |
| — each tool's `schema` (name, description, parameters) | in the hash | in the hash | yes |
| — each tool's `risk` | in the hash | in the hash | no (only through the description suffix) |
| — each tool's `capabilities` | **not hashed** | **hashed (new)** | no |
| `tool_count` | 10 | 10 | yes (number of tools) |
| `max_context_tokens` | 16000 | 16000 | yes (how much history is kept) |
| `tool_timeout_s` | 20.0 | 20.0 | indirectly (a timeout result) |
| `tool_result_chars` | 8000 | 8000 | yes (result cut length) |
| `request_timeout_s` | 60.0 | 60.0 | no |
| `sampling` | provider default (no temperature or seed sent) | same | no parameters sent |
| `approval_rules` | **absent** | **`tiers-v2.1` (new)** | no (the rules act on calls) |

The bench never starts MCP servers, so the main split's tool list is the built-in registry.

## 2. Reproduced

| Commit | Rebuilt fingerprint | Expected |
|---|---|---|
| `c7c0d4f` | `cc5c16377662` | `cc5c16377662` (run 2 meta; recomputed on 2026-10-06) |
| `75ce54c` | `35fac5ba6277` | `35fac5ba6277` (rule C, `docs/tiers-v2-design.md` section 5) |

## 3. What differs

### System prompt

Identical: 1,017 characters, 5 lines, same hash `36d57c7e8a82`. There's no line diff to show.

### Tools, side by side

The schema columns hold at both commits. "Schema" covers name, description and parameters; the description
is shown cut to its first line.

| Tool | Description (first line) | Parameters | Schema old vs new | Risk (both) | Capabilities: none -> new |
|---|---|---|---|---|---|
| `append_to_file` | Add text to the end of a file in the workspace (creates the file if missing)… | content: string, path: string (required: path, content) | same | confirm | writes_local |
| `calculator` | Evaluate a math expression exactly. Supports + - * / // % **, parentheses… | expression: string (required: expression) | same | safe | none |
| `create_note` | Save a new markdown note in the workspace notes/ folder. Requires the user's approval… | body: string, title: string (required: title, body) | same | confirm | writes_local |
| `delete_file` | Delete a file from the workspace. Disabled: always refused. | path: string (required: path) | same | forbidden | deletes_local |
| `get_current_time` | Get the current date and time in a timezone. | timezone: string (required: timezone) | same | safe | none |
| `list_files` | List the files and folders in the user's workspace folder. | path: string (required: none) | same | safe | reads_local |
| `read_file` | Read a text file from the user's workspace folder. Long files are truncated… | path: string (required: path) | same | safe | reads_local, reads_untrusted |
| `send_message` | Send an email on the user's behalf. (Simulated for now: saved to workspace/outbox/.)… | body: string, subject: string, to: string (required: to, subject, body) | same | confirm | sends |
| `web_search` | Search the web (DuckDuckGo) and return titles, URLs and snippets. | max_results: integer, query: string (required: query) | same | safe | network_egress, reads_untrusted |
| `write_file` | Create a text file in the workspace, or overwrite it completely. Requires the user's approval… | content: string, path: string (required: path, content) | same | confirm | writes_local |

At `75ce54c`, the "Requires the user's approval…" / "Disabled: always refused." suffixes come from the tier
derived from the capabilities (`base_level`). At `c7c0d4f` they came from `risk=`. Every tier came out the
same, so the text is the same.

### What the model sees but the fingerprint doesn't hash (code diff `c7c0d4f..75ce54c`)

Five files that shape a main-suite run changed: `agent.py`, `approval.py`, `tools.py`, `llm.py` and
`bench/runner.py`. These didn't change between the two commits:
- the task files and the fixture workspace;
- `bench/checks.py`, `bench/judge.py` and `bench/tasks.py`;
- `pyproject.toml` and `uv.lock` (so the same package versions).

The messages the gate returns to the model are unchanged too: every `GateResult(...)` line in `approval.py`
is identical. So a rejection still reads as before.

## 4. Classification

| # | Change | Kind | Commit(s) | Model-visible? | Habit it could plausibly touch (hypothesis) |
|---|---|---|---|---|---|
| 1 | Capability tags on 8 tools (table above), now hashed | deliberate tiers-v2 | `8f3ef19` (stage 1) | no | none expected |
| 2 | `approval_rules: tiers-v2.1` in the fingerprint | deliberate tiers-v2 (bookkeeping) | `8f3ef19`, `4a09213` | no | none expected |
| 3 | **Taint card** (rules a + e1): after a `reads_local` tool returned local data, a `network_egress` call (`web_search`) needs a card unless the user typed its query. v2.1: a read that failed or returned nothing doesn't taint (#57). The main-suite scripted user rejects unlisted cards ("Not now, thanks."), so the model gets a rejection instead of results. | deliberate tiers-v2 | `64ce8ca` (stage 2), `4a09213` (v2.1) | yes, mid-conversation, only after a card | **asking for info that's in the workspace / giving up:** after a rejected search the model may ask the user or stop (the v2 run's `web-weather-tokyo`, `docs/tiers-v2-design.md` section 5). Not calculator skipping or drafting, which happen before any card. Visible as `escalated_by` |
| 4 | **Content card** (rule b): a network call that carries local text read in the conversation needs a card | deliberate tiers-v2 | `711717a` (stage 3) | yes, same way as 3 | same as 3; also visible as `escalated_by` (`content:…`) |
| 5 | An escalated call can't be approved "for the session", and an earlier session approval doesn't cover it | deliberate tiers-v2 | `24140b7` (stage 5) | no in the main suite (the scripted user never approves for the session) | none expected |
| 6 | An escalated `web_search` runs in the sequential "actions" phase instead of the parallel "safe" phase. Results reach the model in call order either way | side effect of tiers-v2 | `64ce8ca` | no (same results, same order) | none expected |
| 7 | `execute(approved=...)` now comes from the gate's decision instead of `risk == "confirm"` | side effect of tiers-v2 | `64ce8ca` | no (same calls run for the main suite) | none expected |
| 8 | The card's notice (why, decoded arguments, highlighted local text, "You never typed this address") | deliberate tiers-v2 | `24140b7` | no: shown to the approver only, and `ScriptedApprover` ignores it (it decides on the tool and argument regexes, unchanged) | none expected |
| 9 | Audit entries (`tool`, `decision`, `escalated_by`) copied into each result | side effect of tiers-v2 (measurement) | `165f725` | no | none expected |
| 10 | `llm.py` labels Groq's daily-limit 429 and reads "try again in…". `run_suite --wait-for-quota` drops a run that errored on the daily limit and re-runs the same (task, repeat) from the start after the wait | unrelated to tiers-v2 (bench tooling) | `642e8c7` | no: a re-run starts a fresh conversation, as `--resume` did for the baselines | none expected |

## 5. Not covered by the fingerprint, outside the code (untested)

These could differ between the baselines (2026-10-04/05) and the after-run (from 2026-10-23), and neither
the fingerprint nor this diff can see them:
- **Groq's serving of `openai/gpt-oss-120b`:** model weights, quantization, chat template or default
  reasoning settings on the provider side. The fingerprint records only the model name. *Could touch:* any
  habit, uniformly across categories.
- **Live web results:** the main split's `web_search` is real DuckDuckGo, not a stub, so the `web` tasks see
  different pages on different days. *Could touch:* web tasks only, not the three habits.
- **The date:** time tasks read the real clock. *Could touch:* none of the three habits.
- **The baseline's own code:** the baseline file was the v1.0 run re-scored and re-judged, and it has no
  fingerprint. Its agent is taken to equal run 2's by the README's account. Between run 2's commit (`353ffb1`)
  and `c7c0d4f`, only `bench/runner.py` changed, adding the setup/grader hooks the main suite doesn't use.

Ways to test the hypotheses later (none run here): count `escalated_by` entries and their outcomes per flipped
task; compare the flip list with the failure groups in `evals/reports/baseline-failures.md`; and for the
provider side, compare run 2 with a fresh run of `c7c0d4f` (agent `cc5c16377662`) on the same day as a
tiers-v2.1 run. That last one needs Groq quota and an approval line.

## Reproduce

PowerShell, from the development folder. It reads only git objects and writes to a temp folder.

```powershell
$t = Join-Path $env:TEMP "kestrel-fp"; Remove-Item -Recurse -Force $t -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$t\old", "$t\new" | Out-Null
git archive c7c0d4f src | tar -x -C "$t\old"
git archive 75ce54c src | tar -x -C "$t\new"
uv run --no-sync python -I scripts/fingerprint_inputs.py dump "$t\old\src" "$t\old.json"   # cc5c16377662
uv run --no-sync python -I scripts/fingerprint_inputs.py dump "$t\new\src" "$t\new.json"   # 35fac5ba6277
uv run --no-sync python -I scripts/fingerprint_inputs.py diff "$t\old.json" "$t\new.json"
git diff --stat c7c0d4f 75ce54c -- src/kestrel/agent.py src/kestrel/approval.py src/kestrel/tools.py src/kestrel/llm.py src/kestrel/bench/runner.py
git diff --stat c7c0d4f 75ce54c -- evals/kestrelbench/tasks evals/kestrelbench/workspace src/kestrel/bench/checks.py src/kestrel/bench/judge.py src/kestrel/bench/tasks.py pyproject.toml uv.lock   # empty
```
