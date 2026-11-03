# Can a 4B model do most of an agent's work?

*Skeleton draft, 2026-11-03. Sections 1–4 and 6 use only numbers that exist now, each linked to its source. Section 5 is
placeholders, filled from `evals/reports/phase4-result.md` once the section 8 runs are done (the table at the end says
where each one comes from). Nothing here says how the routed system did: that isn't measured yet.*

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

## 4. Safety

- **The student alone** (no router, no teacher), on the 48-task prompt-injection split ([report](../../evals/reports/student-safety-alone.md)):
  - **0 of 29** attacks got through under the strict user (95% CI 0–12%), the same as the teacher;
  - but it **attempted** more: 5 of 29, against the teacher's 1;
  - under a user who approves everything, 3 of 29 got through, against the teacher's 0.
  
  With 29 attacks none of these differences is significant. Still, they lean toward the student, and the gate is what
  held.
- **The group F/S split.** Every safety task offers a fetch tool, and the student never saw that tool in training, so
  the router would send all 48 tasks to the teacher and measure nothing new. So I split the tasks by their own data
  ([design decision 31](../design-decisions.md), [8.7](../phase4-plan.md)):
  - **Group F (19):** tasks that need a tool the student never saw. They stay with the teacher by rule, keeping its
    measured result.
  - **Group S (29):** run routed.

## 5. Results

{{SECTION8_VERDICT}}

| | Teacher alone | Routed | Student alone |
|---|---|---|---|
| Check-pass rate (95% CI) | {{TEACHER_CHECK_RATE}} | {{ROUTED_CHECK_RATE}} | {{STUDENT_CHECK_RATE}} |
| List-price cost per 1,000 tasks | {{COST_PER_1000_TASKS_TEACHER}} | {{COST_PER_1000_TASKS_ROUTED}} | {{COST_PER_1000_TASKS_STUDENT}} |
| Groq billable tokens per 100 tasks | {{GROQ_BILLABLE_PER_100_TEACHER}} | {{GROQ_BILLABLE_PER_100_ROUTED}} | |
| Latency p50 / p95 per task run | {{LATENCY_TEACHER}} | {{LATENCY_ROUTED}} | |

- Quality (a), primary: {{QUALITY_A}}
- Quality (b), against all three references: {{QUALITY_B}}
- Cost, routed − teacher: {{COST_DIFF}}
- The teacher's share of turns: {{TEACHER_SHARE_TURNS}}; of task runs: {{TEACHER_SHARE_RUNS}}
- Why it escalated: {{ESCALATION_REASONS}}
- Safety, group S: {{SAFETY_GATE}}
- Drift guard: {{DRIFT}}. Student-health stop: {{STUDENT_HEALTH}}
- At a 0.90 ratio, for context only: {{CONTEXT_090}}
- Secondary metric (full rule, judge): {{SECONDARY}}

{{CHART}}

## 6. Limits

- **89 examples.** That's a small training set, and the student only knows Kestrel's built-in tools: the fetch and MCP
  tools send a turn to the teacher by rule.
- **CPU serving.** On my laptop the student's model calls took 6.6 s at the median and 16.1 s at p95, with the slowest
  at 42.9 s (658 calls, [8.7](../phase4-plan.md)). Fine for a background agent, slow for a chat. Cheaper doesn't mean
  faster here.
- **A self-judging secondary metric.** The secondary metric's judge is gpt-oss-120b, the teacher itself, and it isn't
  calibrated: on my held-out labels it agrees 91% of the time, but Cohen's kappa can't be read yet
  ([known issue #21](../known-issues.md)). That's why the claim rests on deterministic checks.
- **No published weights, by design.** The code, configs, manifests and hashes are in the repo; the trained student's
  weights and the teacher's training data aren't.

---

## Filling section 5 (delete this part before publishing)

Every placeholder comes from `evals/reports/phase4-result.md` (written by `scripts/phase4_result.py`, branch
`phase4/results-kit`) or from the chart file it writes beside it. The script writes labelled rows, not named values, so
each placeholder is named after the row it copies; copy the cell as written.

| Placeholder | Where in `phase4-result.md` |
|---|---|
| `{{SECTION8_VERDICT}}` | the title line `# Phase 4 result: …`, and the **Outcome** row of "## Verdict (section 8.5)" (Holds column) |
| `{{QUALITY_A}}` | "## Verdict", row "Quality (a), primary": the Result and Holds cells |
| `{{QUALITY_B}}` | "## Verdict", row "Quality (b)": the Result and Holds cells |
| `{{COST_DIFF}}` | "## Verdict", row "Cost": the Result cell (USD per 1,000 tasks, 95% CI) and Holds (`yes` / `no`) |
| `{{SAFETY_GATE}}` | "## Verdict", row "Safety (8.4 as amended in 8.7)": the Result and Holds cells |
| `{{DRIFT}}` | "## Verdict", row "Drift guard (#58, read as pre-registered 2026-11-01)": the Result and Holds cells |
| `{{STUDENT_HEALTH}}` | "## Verdict", row "Student-health stop": the Holds cell |
| `{{CONTEXT_090}}` | "Not part of the verdict", line "At 0.90 for context: lower bound …" |
| `{{SECONDARY}}` | "Not part of the verdict", line "Secondary metric (full rule, judge `gpt-oss-120b` v2): …" (`not run` unless re-judged) |
| `{{TEACHER_CHECK_RATE}}`, `{{ROUTED_CHECK_RATE}}`, `{{STUDENT_CHECK_RATE}}` | "## Cost vs quality" table, column "Check-pass rate (95% CI, over tasks)", rows "Teacher alone", "Routed", "Student alone" (without a student run, the line "Student alone: not measured…") |
| `{{COST_PER_1000_TASKS_TEACHER}}`, `{{COST_PER_1000_TASKS_ROUTED}}`, `{{COST_PER_1000_TASKS_STUDENT}}` | "## Cost vs quality" table, column "List-price cost per 1,000 tasks", rows "Teacher alone", "Routed", "Student alone" |
| `{{GROQ_BILLABLE_PER_100_TEACHER}}`, `{{GROQ_BILLABLE_PER_100_ROUTED}}` | "## Cost" table, row "Groq billable tokens per 100 tasks" |
| `{{LATENCY_TEACHER}}`, `{{LATENCY_ROUTED}}` | "## Cost" table, row "Latency p50 / p95 per task run" |
| `{{TEACHER_SHARE_TURNS}}`, `{{TEACHER_SHARE_RUNS}}` | "## Cost" table, rows "Teacher's share of turns" and "Teacher's share of task runs", Routed column |
| `{{ESCALATION_REASONS}}` | "## Cost", the "Reason \| Turns" table and the "Turns: … escalated" line under it |
| `{{CHART}}` | `![Cost vs quality](../../evals/reports/phase4-cost-quality.svg)`: the file the script writes next to the report |
