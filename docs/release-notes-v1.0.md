# Kestrel v1.0: release notes (draft)

> **DRAFT, not tagged.** Section 9's result is a placeholder, and v1.0 isn't tagged until it is in.

Kestrel is a personal AI agent with its own agent loop, built and evaluated on free tiers only.
- **It asks before it acts:** every risky action waits for a human, and that is enforced in code.
- **It trains a smaller model from its own traces,** with the claim tested under rules frozen before the data existed.

Start here: [README](../README.md), [demo video (1:50)](https://youtu.be/kglrGnkzt_I).

## What works

| Area | In v1.0 | Evidence |
|---|---|---|
| Agent | Own agent loop: multi-step plans, parallel tool calls, retries, a step limit, history trimming; provider-agnostic model layer (Groq, Gemini, Ollama, a no-key demo) with fallback | [agent.py](../src/kestrel/agent.py), [llm.py](../src/kestrel/llm.py) |
| Approval gate, permission tiers v2.1 | Capabilities fixed in code per tool; reading files or the web taints the session, and outbound content is checked; approve, edit or reject, with an audit log | [design](tiers-v2-design.md), [safety report](../evals/reports/safety-after-v2.1.md) |
| MCP | Consumes MCP servers' tools (untrusted by default) and serves Kestrel's safe tools as `kestrel-mcp` | [mcp_client.py](../src/kestrel/mcp_client.py), [mcp_server.py](../src/kestrel/mcp_server.py) |
| Tracing and console | SQLite traces with tokens, latency and cost; a web console with live steps, approval cards and trace waterfalls | [tracing.py](../src/kestrel/tracing.py), [console/](../console/) |
| KestrelBench | 100 main tasks and a 48-task prompt-injection split (29 attacks); deterministic checks first; a CI subset on Groq when the agent changes | [tasks](../evals/kestrelbench/tasks/), [safety split](../evals/kestrelbench/safety/), [CI](../.github/workflows/ci.yml) |
| Distillation | Trace dataset with counted filters, QLoRA on a free Kaggle T4 (Qwen3-4B), export to Ollama with a byte-for-byte template check, and a router (student first, teacher as fallback; off by default) | [training](../distill/TRAINING.md), [export](../distill/EXPORT.md), [router.py](../src/kestrel/router.py) |
| Voice | Local wake word and speech-to-text, with no cloud speech service; a wake can't answer an approval | [voice-wake.md](voice-wake.md) |
| Demo mode | Runs with no keys: scripted replies, real tools, approval cards and traces | [README, "Run it"](../README.md#run-it-free-tiers) |

## Measured results

Every number links to its source.

**KestrelBench baseline** ([report](../evals/reports/baseline-v1.1.md)).
- **Score:** gpt-oss-120b on Groq, **92%** of 100 tasks (95% CI 86–97%).
- **Run-to-run noise** ([report](../evals/reports/run-to-run.md)): two runs of the identical agent differ by +1 point (95% CI −5 to +7), and 9 of 100 tasks flip.

**Prompt-injection safety, tiers v2.1.** Attacks that got through at system level, under the strict user:

| Model | Before | After |
|---|---|---|
| Scripted model that reads the data, then always obeys the injection | 13/29 | **0/29** |
| gpt-oss-120b | 1/29 (95% CI 0–18%) | **0/29** (95% CI 0–12%) |

- **Under the rubber-stamp user,** the same scripted model gets 25/29 → 23/29.
- **What that means:** a gate that asks a user who approves everything is only as safe as the model.
- **Sources:** [offline report](../evals/reports/tiers-v2-offline.md), [design](tiers-v2-design.md), [safety report](../evals/reports/safety-after-v2.1.md).

**Distillation, section 8: FAIL**, applied exactly as pre-registered ([result](../evals/reports/phase4-result.md)).
- **Cost: shown.** 94.8% lower list-price cost, $0.030 against $0.574 per 1,000 tasks. The difference is −$0.544 (95% CI −$0.597 to −$0.494) ([how it's computed](../evals/reports/phase4-result.md#derived-numbers-quoted-in-the-readme-added-2026-11-08)).
- **Quality: not shown.** Check-pass 89.6% against 92.3%, a ratio of 0.970. The frozen test needed the 95% lower bound of mean(routed − 0.95 × teacher) above 0; it was +0.0188 (95% CI −0.0342 to +0.0715).
- **Safety gate: FAIL.** One attack (`atk-exfil-search-file`) got through under the rubber-stamp user (1/17, where the rule requires 0).
  - **Cause:** the router kept turns on the student after the session had read untrusted data, and the user approved the card.
  - **The fix:** tainted turns go to the teacher ([plan 8.7](phase4-plan.md#87-amendments)).

**Section 9, the re-measurement after the fix:** _PLACEHOLDER: results not in yet. Nothing goes here until the runs finish, and no result is guessed._

**LLM judge calibration** ([report](../evals/reports/judge-calibration.md)).
- **Agreement:** 91% (95% CI 78–100%) on 32 held-out answers.
- **Disagreements:** all 3 were the judge being too strict.
- **Leniency can't be measured yet:** the held-out labels contain no failing answers, which is also why kappa is 0.
- **Lenient verdicts do happen:** an earlier [pass review](../evals/reports/baseline-pass-review.md) found 2 lenient verdicts in 9.

## Known limits

- **No speaker recognition.** Any voice can wake Kestrel and make a request; risky actions still need an approval click.
- **Web fetch:**
  - in v1.0, `fetch` can still reach loopback and private addresses. The block is on `fix/fetch-private-block`, not merged.
  - even with it, a redirect to a private address and DNS rebinding stay open ([known issues](known-issues.md)).
- **Paraphrased leaks are invisible to the checks.** A paraphrase, ROT13 or spelled-out secret isn't detected, so attack rates are lower bounds ([#23](known-issues.md)).
- **The judge is self-graded until calibrated.** It is the agent's own model ([#21](known-issues.md)); the Phase 4 claim uses deterministic checks only.
- **Docker is verified in CI only** ([#2](known-issues.md)).
- **The student's sampling depended on the Ollama version** during section 8. This was disclosed, not recomputed ([plan 8.7](phase4-plan.md#87-amendments)).
- **Long-term memory isn't included.** It is built on parked branches, after Phase 4.

The full list is in [known issues](known-issues.md); decisions and their alternatives are in [design decisions](design-decisions.md).

## Not in v1.0

- **Memory:** long-term memory with hybrid RAG.
- **A nightly training loop:** retrain the student from good traces, and deploy only if it beats the current one on KestrelBench and passes every safety test.
- **Speaker verification** for the wake word.
- **Judge leniency:** failing answers labelled, so the judge's leniency can be measured.
