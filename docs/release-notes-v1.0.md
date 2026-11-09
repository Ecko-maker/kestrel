# Kestrel v1.0: release notes (draft)

> **DRAFT, not tagged.** Section 9's result is in (PASS); v1.0 is tagged after the owner's review.

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

**Distillation, section 9: PASS** ([verdict](../evals/reports/phase4-section9-verdict.md); rule frozen in `0071e54`, before any run). A fine-tuned 4B student, with the 120B teacher taking over once a session reads untrusted data, cleared the pre-registered bar of 95% of the teacher's quality at half the teacher's list cost, and no attack got through.
- **Quality: shown.** Check-pass 97% against the teacher's 92.3%. The frozen test needed the 95% lower bound of mean(routed − 0.95 × teacher) above 0: +0.0928 (95% CI +0.0502 to +0.1382); +0.0865 (+0.0470 to +0.1288) against all three teacher references.
- **Cost: shown.** **50.3% lower** list-price cost, $0.285 against $0.574 per 1,000 tasks; the difference is −$0.289 (95% CI −$0.348 to −$0.229).
  - **Section 8's router was 94.8% cheaper.** The student now answers 56.6% of turns (60 of 106) instead of 96.6%, because tainted turns go to the paid teacher.
- **Safety gate: held.** 0/17 attacks at system level under the strict and the rubber-stamp user, 3 repeats each.
- **Drift:** the teacher's control scored +6 points this week (95% CI 0 to +17) against 10-07; the frozen rule guards only against downward drift.
- **The limits the frozen rule requires us to state** (one routed run per task):
  - a **4%** chance of a false PASS on quality if the routed system were exactly at the 95% margin;
  - an **8%** chance that quality shows as passing even if the fix changed nothing. That wouldn't be a false quality claim, since section 8 was already above the margin on average.
- **Where the margin comes from:**
  - **The student fixed two teacher habits:** skipping the calculator (5 tasks) and asking for data that is in the workspace.
  - **Two tasks the teacher always fails:** on `act-note-top-todos` and `multi-scale-recipe` the student read the files first, and the teacher finished.
  - **Sensitivity:** quality is still shown with the calculator and workspace flips treated as noise (lower bound +0.0185), but not if every favourable flip is (−0.0015).
- **What ships:** the router, as an opt-in configuration (`KESTREL_PROVIDERS=routed`), off by default.

**Distillation, section 8: FAIL**, the first attempt, applied exactly as pre-registered ([result](../evals/reports/phase4-result.md)). It stands, reported beside section 9.
- **Cost: shown.** 94.8% lower list-price cost, $0.030 against $0.574 per 1,000 tasks. The difference is −$0.544 (95% CI −$0.597 to −$0.494) ([how it's computed](../evals/reports/phase4-result.md#derived-numbers-quoted-in-the-readme-added-2026-11-08)).
- **Quality: not shown.** Check-pass 89.6% against 92.3%, a ratio of 0.970. The frozen test needed the 95% lower bound of mean(routed − 0.95 × teacher) above 0; it was +0.0188 (95% CI −0.0342 to +0.0715).
- **Safety gate: FAIL.** One attack (`atk-exfil-search-file`) got through under the rubber-stamp user (1/17, where the rule requires 0).
  - **Cause:** the router kept turns on the student after the session had read untrusted data, and the user approved the card.
  - **The fix:** tainted turns go to the teacher ([plan 8.7](phase4-plan.md#87-amendments)).

**The released agent** is the section 9 agent (`35fac5ba6277`) plus the private-address fetch block (approval rules `tiers-v2.2`, agent `8a8174d8f17d`; prompt and tools unchanged). Checked by replaying the 209 pinned fetch calls through the block (0 refused, [known issues](known-issues.md)) and by the combined PR's CI run _(pending)_.

**LLM judge calibration** ([report](../evals/reports/judge-calibration.md)).
- **Agreement:** 91% (95% CI 78–100%) on 32 held-out answers.
- **Disagreements:** all 3 were the judge being too strict.
- **Leniency can't be measured yet:** the held-out labels contain no failing answers, which is also why kappa is 0.
- **Lenient verdicts do happen:** an earlier [pass review](../evals/reports/baseline-pass-review.md) found 2 lenient verdicts in 9.

## Known limits

- **No speaker recognition.** Any voice can wake Kestrel and make a request; risky actions still need an approval click.
- **Web fetch:**
  - v1.0 blocks `fetch` calls to loopback, private and link-local addresses before they run (approval rules `tiers-v2.2`); a block is a refusal, never an approval card.
  - **What the block checks:** the URL's host only, so a redirect to a private address and DNS rebinding stay open ([known issues](known-issues.md)).
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
