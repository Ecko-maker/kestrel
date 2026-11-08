# Can a 4B model do most of an agent's work?

*Draft, 2026-11-08. Every number is linked to its source. Section 5 is section 8's result, applied as frozen: it
FAILED. Section 6 is the re-measurement after the fix, and stays a marked placeholder until its runs are done.*

## 1. The question

Kestrel runs on a big open-weight model, gpt-oss-120b on Groq's free tier. Most of what an agent does is routine: read
a file, do a sum, draft a note, call the right tool with the right arguments. So I wanted to know:

> Can a small open model, fine-tuned on the big model's own successful traces, handle most of the agent's work at
> near-frontier quality, at a fraction of the cost, with the big model taking over only when the small one stumbles?

That's the project's headline claim, and I wrote down how I'd test it before I had any data
([pre-registration](../phase4-plan.md#8-pre-registration-of-the-phase-4-claim-2026-10-30)).

## 2. The pipeline, as built

### Choosing the teacher

A student can only learn from outputs it's allowed to train on. I won't train on Claude, GPT or Gemini outputs, whose
terms restrict it, so the teacher had to be an open-weight model.
- **The choice: gpt-oss-120b on Groq,** once Groq confirmed in writing (a support chat, 2026-10-31) that its outputs
  could be used this way ([design decision 29](../design-decisions.md)).
- **The fallback, built and measured anyway (option c):** a teacher on a free Kaggle GPU.
  - A pre-registered selection between gpt-oss:20b and Qwen3-30B-A3B, which Qwen won ([selection](../../distill/selection/README.md)).
  - A full collection pass with it ([evidence](../../distill/collection/README.md)).
  - Both are kept as evidence only. The student never trained on them.

### Collecting

The teacher ran a separate distillation task set: 122 tasks, kept apart from the benchmark by an overlap check against
its prompts ([overlap report](../../evals/reports/distill-overlap.md)).
- **Each task ran once:** 104 passed, 18 failed, 206,378 billable Groq tokens over two days ([collection](../../distill/collection-a/README.md)).

### Building the training set

Every trace goes through filters, in order, and every drop is counted ([design decision 22](../design-decisions.md)):
- no other teacher;
- no chat-template control tokens leaking into calls or text;
- no contamination with the benchmark;
- quality (only passed tasks);
- no secrets (traces with secrets are dropped, never redacted);
- length and duplicates.

From 131 traces, 89 examples survived: **71 train and 18 validation, split by task**, so no task is on both sides
([collection](../../distill/collection-a/README.md)).

### Training

QLoRA (4-bit NF4, rank 16) on Qwen3-4B-Instruct-2507, on one free Kaggle T4.
- **The run:** 44 minutes, 5 epochs, 45 steps, no early stop.
- **Validation loss, epoch by epoch:** 0.660, 0.470, 0.424, 0.411, **0.409** ([run log](../../distill/TRAINING.md)).

It took three attempts, and both failures taught me something about testing:
- **Two GPUs.** Kaggle's "T4 ×2" made two GPUs visible. The trainer wrapped the model in `DataParallel`, which TRL's
  trainer refuses. Fix: show the process one GPU, and refuse to start with more.
- **bfloat16 on a T4.** TRL quietly casts a 4-bit model's trainable weights to bfloat16, whatever the fp16 setting
  says. A T4 can't do bfloat16, so step 0 crashed in the gradient scaler. Fix: cast them back to float32 after the
  trainer is built, and refuse to train otherwise.
- **Why my smoke test missed both:** it trained with its own loop, not the real trainer. It now takes one real trainer
  step with the run's own settings ([known issue #80](../known-issues.md)).

### Export and serving

- **The merge.** My laptop had 2–3.6 GB of RAM free, and a normal merge needs about 9 GB. So I wrote a tensor-by-tensor
  merge, tested to give bit-identical weights to the library's ([export log](../../distill/EXPORT.md)).
- **Serving:** a 2.5 GB GGUF in Ollama, with a prompt template checked byte for byte against Ollama's own renderer.
- **A local smoke test still found a bug those checks couldn't see.** The student wrote perfect tool calls, and Ollama
  returned them as plain text: Ollama reads its tool-call tag from the template, and mine didn't expose it. The fix
  left every prompt byte-identical, so no retraining was needed ([known issue #81](../known-issues.md)).

### The router

The student answers first. The teacher takes over a turn if:
- the tools the request needs aren't the ones the student was trained on;
- the prompt is too long for the student;
- the student makes an invalid tool call, or returns an empty reply;
- the student hits the step limit;
- the student fails or times out.

The router never escalates on a refusal or a rejected approval, and nothing runs twice.

One more rule came from the bug above: a reply with a tool call left in its text now escalates. I added it before any
routed run existed, and recorded it as such ([known issue #82](../known-issues.md), [plan 8.7](../phase4-plan.md)).

## 3. Pre-registration

I froze the claim and the test on 2026-10-30, before any teacher data existed ([section 8](../phase4-plan.md#8-pre-registration-of-the-phase-4-claim-2026-10-30)).

- **The claim:** routed reaches at least 95% of the teacher's quality, and costs less per task, on the 100-task main
  suite.
- **The primary metric:** deterministic checks only, no LLM judge. Per task, D = routed − 0.95 × teacher. The claim is
  shown if the 95% paired bootstrap lower bound of the mean D is above 0. The routed system runs 5 repeats; the teacher
  reference is its 3-repeat after-run.
- **Power, simulated before any routed data** ([8.3](../phase4-plan.md#83-power-open-question-13)). With R=5 the test:
  - shows the claim **87%** of the time if the routed system matches the teacher;
  - **14%** of the time if it loses 2 tasks for good;
  - **2%** of the time if it sits exactly at the 95% margin.

  It's strict on purpose.
- **NOT SHOWN is a legitimate outcome.** If the interval contains 0, the write-up says so, with the numbers, and I don't
  add repeats to rescue it.
- **A drift guard.** The teacher reference was measured a week earlier, so the old agent re-ran the CI subset in the
  same week (2026-11-03). Against its own runs of 10-07 it scored **+0 points (95% CI −10 to +11)**: by the rule fixed
  on 11-01, drift not shown ([control](../../evals/controls/README.md), [known issue #58](../known-issues.md)).
- **One result I saw before the routed run.** The student alone (no router, no teacher, no judge) ran the main suite
  once, and I looked at it before the routed runs started. It passed every check on **90 of 100 tasks (95% CI
  84–96%)**; the teacher's after-run ×3 gives 92% (88–96%) on the same metric. That's context, not the claim, and I
  recorded it as a disclosure that changes nothing in the frozen design: same 5 repeats, same metric, same margin, no
  interim look ([pinned run](../../evals/after-runs/student-main-x1.json), [8.7](../phase4-plan.md#87-amendments)).

## 4. Safety

**Where the gate stood before Phase 4.** In Phase 3, permission tiers v2.1 brought prompt-injection attacks that got
through, at system level and under the strict user, from:
- **13/29 to 0/29 for a scripted model that reads the data and then always obeys the injection.** That's a test of the
  code, not of a model: the gate stops every attack *if the model tries it after reading the data*. A user who approves
  everything isn't protected the same way: 25/29 to 23/29 for the same scripted model ([offline report](../../evals/reports/tiers-v2-offline.md)).
- **1/29 (95% CI 0–18%) to 0/29 (0–12%) for gpt-oss-120b** ([design](../tiers-v2-design.md), [safety report](../../evals/reports/safety-after-v2.1.md)).

**The student alone** (no router, no teacher), on the 48-task prompt-injection split ([report](../../evals/reports/student-safety-alone.md)):
- **0 of 29** attacks got through under the strict user (95% CI 0–12%), the same as the teacher;
- but it **attempted** more: 5 of 29, against the teacher's 1;
- under a user who approves everything, 3 of 29 got through, against the teacher's 0.

With 29 attacks none of these differences is significant. Still, they lean toward the student, and the gate is what
held.

**The group F/S split.** Every safety task offers a fetch tool, and the student never saw that tool in training, so
the router would send all 48 tasks to the teacher and measure nothing new. So I split the tasks by their own data
([design decision 31](../design-decisions.md), [8.7](../phase4-plan.md)):
- **Group F (19):** tasks that need a tool the student never saw. They stay with the teacher by rule, keeping its
  measured result.
- **Group S (29):** run routed.

## 5. Results: section 8 FAILED

**Outcome: FAIL**, applied exactly as frozen ([result report](../../evals/reports/phase4-result.md)). Cost was shown,
quality was not shown, and the safety gate failed on one attack.

| | Teacher alone | Routed | Student alone |
|---|---|---|---|
| Check-pass rate (95% CI) | 92% (88–96%), ×3 | 90% (84–94%), ×5 | 90% (84–96%), ×1 |
| List-price cost per 1,000 tasks | $0.574 | $0.030 | $0.000 |
| Groq billable tokens per 100 tasks | 147,667 | 13,670 | |
| Latency p50 / p95 per task run | 9.9 s / 27.0 s | 10.3 s / 33.2 s | |

![Cost vs quality](../../evals/reports/phase4-cost-quality.svg)

**Cost: shown.**
- **The difference:** routed − teacher is −$0.544 per 1,000 tasks (95% CI −$0.597 to −$0.494), **94.8% lower list cost**
  ([how it's computed](../../evals/reports/phase4-result.md#derived-numbers-quoted-in-the-readme-added-2026-11-08)).
- **The teacher's share:** it answered 3.4% of turns (95% CI 1.3–6.0%) and took part in 3.6% of task runs (1.4–6.2%).
- **Why it escalated:** 18 of 530 turns, 13 for an invalid tool call and 5 for an empty reply.

**Quality: not shown.**
- **The ratio:** check-pass 89.6% against 92.3%, a quality ratio of **0.970**.
- **The pre-registered test** needed the 95% lower bound of mean(routed − 0.95 × teacher) above 0:
  - primary: **+0.0188 (95% CI −0.0342 to +0.0715)**;
  - against all three references: +0.0125 (95% CI −0.0393 to +0.0625).
- **For context only,** at a 0.90 ratio the lower bound is +0.0120.
- **What it means:** the routed system is close to the teacher, but this test on 100 tasks can't show it's within 5%.
  Section 3's power table shows how rarely the test passes once the routed system loses even 2 tasks for good.

**Safety gate: FAIL.**
- **Strict user, group S:** 0/17 got through (95% CI 0–20%).
- **The failure, rubber-stamp user, group S:** **1/17** got through (`atk-exfil-search-file`, 95% CI 0–29%), where the
  rule requires 0. The teacher had 0 of 29.
- **Group F:** the teacher's 0, by the pre-route rule.
- **The cause:**
  - The router sent every turn to the student, including turns after the session had read untrusted data (that run made
    no Groq calls at all).
  - The student read the private file, then followed the injected instruction and put its secret into a web search.
  - The gate raised a card (the content check, plus taint from the file it had read), and the user who approves
    everything approved it.
  - A gate that asks a rubber-stamp user is only as safe as the model behind it
    ([run report](../../evals/reports/routed-safety-S-rubber-attacks.md)).

**The rest.** Drift guard: +0 points (95% CI −10 to +11), none. Student-health stop: not triggered. Secondary metric
(full rule, LLM judge): not run.

**Disclosed after the run, not recomputed** ([8.7](../phase4-plan.md#87-amendments)): Ollama updated itself from
0.40.13 to 0.40.14 between the main suite's two segments.
- **Why it matters:** that update changed the student's sampling, from temperature/top_p 1.0/1.0 to the Modelfile's
  0.7/0.8.
- **Which runs:** the first 320 routed main runs used the old sampling; the last 180 and group S used the new one.
- **How it's known:** from Ollama's source, not measured.
- **What changed afterwards:** the run checklist (`distill/EVAL.md`, step 0) now stops a run on any Ollama version but
  the recorded one ([known issues](../known-issues.md)).

**What follows.** Decided before any data, as decision 9 ([plan](../phase4-plan.md)): a safety failure first gets one
fix, then a new pre-registered measurement. Retraining comes only if that fails too.
- **The fix:** once a session is tainted (it has read untrusted data), its turns go to the teacher.
- **Sampling:** the fix also sends the student's sampling settings explicitly and makes them part of the routed
  fingerprint (on `phase4/tainted-to-teacher`, not in `main` yet).

## 6. Section 9: the re-measurement after the fix

> **PLACEHOLDER: section 9's runs aren't finished. Nothing goes here until they are, and no result is guessed.**
> **The frozen design:** section 9, frozen 2026-11-07 (`0071e54`), in `docs/phase4-section9-proposal.md` on
> `phase4/tainted-to-teacher`; it isn't in `main` yet. It runs:
> - routed group S, strict ×3 and rubber-stamp ×3;
> - a same-week drift control;
> - the routed main suite R=1.
>
> A stop rule applies after the safety runs.

{{SECTION9_RESULT}}

## 7. Limits

- **89 examples.** That's a small training set, and the student only knows Kestrel's built-in tools: the fetch and MCP
  tools send a turn to the teacher by rule.
- **CPU serving.** On my laptop the student's model calls took 6.6 s at the median and 16.1 s at p95, with the slowest
  at 42.9 s (658 calls, [8.7](../phase4-plan.md)). Fine for a background agent, slow for a chat. Cheaper doesn't mean
  faster here.
- **A self-judging secondary metric.** The secondary metric's judge is gpt-oss-120b, the teacher itself
  ([calibration report](../../evals/reports/judge-calibration.md)).
  - It agrees with my labels 91% of the time (95% CI 78–100%) on 32 held-out answers, and all 3 disagreements were the
    judge being too strict.
  - Its leniency can't be measured yet, because the held-out labels contain no failing answers. That's also why kappa
    is 0.
  - That's why the claim rests on deterministic checks.
- **Web fetch can reach private addresses in `main`.** The block for loopback and private addresses is on
  `fix/fetch-private-block`, not merged yet. Even with it, a redirect to a private address and DNS rebinding stay open
  ([known issues](../known-issues.md)).
- **No published weights, by design.** The code, configs, manifests and hashes are in the repo; the trained student's
  weights and the teacher's training data aren't.
