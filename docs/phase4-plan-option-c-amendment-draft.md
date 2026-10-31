# DRAFT, not applied: section 8 amendment for option c

**Status: draft only (2026-10-30), not part of `docs/phase4-plan.md` and not in force.** Section 8.5 allows one amendment "if #72 picks option c ... before any routed run, with a dated line saying what changed". This is the text for it, written before #72 is decided (deadline 2026-11-04) and before any candidate, student or routed system has run. If #72 ends in option a, this file is deleted unused. If it ends in option c, the owner picks **A** or **B** below, and the chosen text goes into section 8 with the dated line.

## What option c changes

Only who **taught** the student: a teacher self-hosted on Kaggle (gpt-oss:20b or Qwen3-30B-A3B-Instruct-2507, picked by #72's pre-registered selection; call it T_c) instead of Groq's gpt-oss-120b. The suite (v1.1), the metrics (8.2), R=5, the margin 0.95, the safety gate (8.4) and the outcome rules (8.5) stay as frozen. The open question is what "the teacher" in the claim means, because two different models now play the two roles section 8 gave to one.

## Variant A (recommended): the claim stays against gpt-oss-120b

- **8.1, the claim:** routed (student taught by T_c, fallback to **gpt-oss-120b on Groq**) against **gpt-oss-120b alone**, the same reference as now: the 10-28 after-run ×3 (`8948b434fdcf`, agent `35fac5ba6277`). Add one sentence: "The student is distilled from T_c (option c, #72); the fallback and the reference are gpt-oss-120b, which no training data comes from."
- **Why A:** it keeps the headline claim ("near-frontier quality") and every frozen number, and the reference already exists, so it costs nothing to set up. Using Groq at serve time for escalated turns is inference, not training on Groq outputs. The teacher filter keeps those turns out of any later dataset, since `teachers.toml` would list only T_c. The owner should still confirm that this use is acceptable under the same terms question (#72).
- **8.3, the power table:** **no number changes.** The table is computed from the reference's own run-to-run data, and that reference is unchanged, so `scripts/phase4_power.py` and its test stay as they are. What changes is which row is likely: a student taught by a weaker teacher probably fails more tasks on its own, so it escalates more. That moves it towards the "loses k tasks" rows (14% or less) unless escalation catches those failures. Say so in 8.3: "With option c, the 'identical' row (87%) is less likely to describe the routed system; the table itself is unchanged."
- **Cost (8.2):** unchanged, still against gpt-oss-120b's Groq list price. More escalations raise the routed cost per task and the Groq tokens. **Recompute 8.3's Groq estimate** after the first local day, with the measured escalation rate (the 10/30/50% lines stay as the range).
- **8.6, disclosure:** add that the selection runs (both candidates on #72's 20 distill tasks) were seen before the routed runs. They are teacher candidates on distill tasks, never KestrelBench, so they reveal nothing about the routed system's main-suite score.

## Variant B: the claim is against T_c

- **8.1, the claim:** routed (student taught by T_c, fallback to T_c served the same way) against **T_c alone**.
- **New reference, needed before any routed run:** T_c on main suite v1.1 ×3, `--judge none` (primary needs no judge). It runs on Kaggle, at T_c's measured time per task: about 300 runs at up to 3 min each, so up to 15 GPU-hours, more than half of a week's free quota. The secondary metric needs the judge by `rejudge`: about 3 × 27.5k Groq billable.
- **8.3, the power table:** **recomputed** with `scripts/phase4_power.py` on T_c's ×3 file. The `after` input and its sha256 change; seeds, scenarios, R and margin stay the same. The new table replaces 8.3's before any routed run, with its own test.
  - **What to expect:** at a teacher pass rate p̄ the headroom is 0.05 × p̄. At 85% that's +0.0425, against +0.046 now.
  - Per-task run variance p(1−p) is larger for a weaker teacher. So power falls on both counts, and the claim gets harder, not easier.
- **Cost (8.2):** T_c alone is self-hosted, so its marginal list-price cost is $0. "Beats the teacher on cost" then has no meaning. The cost claim needs a sourced, dated `prices.toml` entry for a hosted T_c (section 7, question 8), or it is dropped and the claim says so.
- **What B costs the headline:** "≥ 95% of a 20-30B model" is a weaker result than "≥ 95% of gpt-oss-120b", and the README would say which.

## The dated line, for whichever variant is applied

> **Amended YYYY-MM-DD (option c, #72):** [variant A / B]. Changed: [8.1 sentence / reference / 8.3 table / cost / 8.6]. Unchanged: suite, metrics, R=5, margin 0.95, safety gate, outcome rules. No routed or student result existed when this was written.
