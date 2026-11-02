# DRAFT, not applied: section 8 amendment for option c (teacher Qwen3-30B-A3B-Instruct-2507)

> **Not applied, and it won't be (2026-11-01, design decision 29).** #72 ended in option a (Groq confirmed in
> writing on 10-31), so section 8 stays as frozen. This file is kept for the record until the branch merges;
> the rule below says it may then be deleted.

**Status: draft only, not part of `docs/phase4-plan.md` and not in force.** Started 2026-10-30, finished
2026-10-31 for the selected teacher.
- **What allows it:** section 8.5 allows one amendment "if #72 picks option c ... before any routed run, with
  a dated line saying what changed".
- **It takes effect only if option c is chosen on 2026-11-04** (#72: no written confirmation from Groq by then).
  If #72 ends in option a, this file is deleted unused.
- **If it ends in option c,** the owner picks **A** or **B** below, and the chosen text goes into section 8 with
  the dated line at the end of this file, before any routed run.

## The teacher (#72's selection, 2026-10-31)

**T_c = Qwen3-30B-A3B-Instruct-2507 at Q4.**
- **Package:** Ollama `qwen3:30b-a3b-instruct-2507-q4_K_M`, digest `19e422b02313…`.
- **How it's served:** as `kestrel-teacher-qwen3-30b-a3b` with Kestrel's template (#73), on Kaggle's 2×T4.
  It loaded at 20.7 GB, all on the GPUs.
- **How it was picked:** #72's pre-registered rule, on 20 distill tasks, `--judge none`:
  - check-pass tied with gpt-oss:20b at 15/20;
  - Harmony leaks tied at 0/0;
  - decided on valid tool calls, 28/28 against 24/27;
  - 2.5 s per task on average.
- **Evidence:** `distill/selection/` (pinned); the table is in `docs/known-issues.md` #72.

Section 8.5's own sentence names gpt-oss:20b ("gpt-oss:20b as the training teacher"). That was the expected
option c teacher before the selection was pre-registered. The amendment replaces it with T_c.

## What option c changes

Only who **taught** the student: T_c, self-hosted on Kaggle, instead of Groq's gpt-oss-120b. These stay as
frozen:
- the suite (v1.1) and the metrics (8.2);
- R=5 and the margin 0.95;
- the safety gate (8.4) and the outcome rules (8.5).

The open question is what "the teacher" in the claim means. Two different models now play the two roles
section 8 gave to one.

**Things specific to T_c that both variants must state:**
- **Same family as the student.** The student is Qwen3-4B-Instruct-2507, so teacher and student share a chat
  format and tokenizer family. That probably makes imitation easier than from gpt-oss-120b. It's stated so it
  isn't mistaken for a result of the method.
- **Template not verified for the 30B.** Kestrel's template is checked byte for byte against Ollama's renderer
  for the 4B student, not for the 30B (`distill/KAGGLE.md`, known limits).
- **Sampling:** T_c generates the data with the student's sampling settings (temperature 0.7, top_p 0.8,
  top_k 20). That's Qwen's own recommendation, set by Kestrel's Modelfile.

## Variant A (recommended): the claim stays against gpt-oss-120b

- **8.1, the claim:** routed (student taught by T_c, fallback to **gpt-oss-120b on Groq**) against
  **gpt-oss-120b alone**. That is the same reference as now: the 10-28 after-run ×3 (`8948b434fdcf`, agent
  `35fac5ba6277`). Add one sentence: "The student is distilled from Qwen3-30B-A3B-Instruct-2507 (option c, #72);
  the fallback and the reference are gpt-oss-120b, which no training data comes from."
- **Why A:**
  - It keeps the headline claim ("near-frontier quality") and every frozen number.
  - The reference already exists, so it costs nothing to set up.
  - Using Groq at serve time for escalated turns is inference, not training on Groq outputs. The teacher filter
    keeps those turns out of any later dataset, since `teachers.toml` lists only T_c (tested 2026-10-31: a Groq
    trace is dropped as `teacher`).
  - **The owner confirms** that serving escalations through Groq is acceptable while §6.3(e) is unconfirmed. This
    is a separate question from training on Groq outputs, which option c avoids.
- **8.3, the power table:** **no number changes.**
  - The table comes from the reference's own run-to-run data. That reference is unchanged, so
    `scripts/phase4_power.py` and its test stay as they are.
  - What changes is which row is likely. T_c tied gpt-oss:20b on the selection slice. Nothing yet compares it
    with gpt-oss-120b, which scored 92.3% check-pass on the main suite. If T_c is weaker, its student probably
    fails more tasks alone and escalates more. That moves the routed system towards the "loses k tasks" rows
    (14% or less), unless escalation catches those failures.
  - Say so in 8.3: "With option c, the 'identical' row (87%) is less likely to describe the routed system; the
    table itself is unchanged."
- **Cost (8.2):** unchanged, still against gpt-oss-120b's Groq list price. More escalations raise the routed
  cost per task and the Groq tokens. **Recompute 8.3's Groq estimate** after the first local day, with the
  measured escalation rate (the 10/30/50% lines stay as the range).
- **8.6, disclosure:** add these lines.
  - "Teacher selection data (2026-10-31): gpt-oss:20b and Qwen3-30B-A3B-Instruct-2507 on #72's 20 distill tasks,
    once each, `--judge none`, on Kaggle. 15/20 check-pass each; Qwen chosen on valid tool calls (28/28 vs 24/27).
    Distill tasks, never KestrelBench: nothing about the routed system's main-suite score was seen."
  - "A scratch `kestrel distill build` on copies of those traces (2026-10-31) checked the teacher and leak filters
    only; its output was not kept and nothing was trained on it."

## Variant B: the claim is against T_c

- **8.1, the claim:** routed (student taught by T_c, fallback to T_c served the same way) against **T_c alone**.
- **New reference, needed before any routed run:** T_c on main suite v1.1 ×3, `--judge none` (the primary
  metric needs no judge).
  - Where it runs: Kaggle 2×T4, served exactly as in the selection.
  - Time: at the measured 2.5 s per task (selection slice), ~300 runs take about 15-30 min plus the ~19 GB
    pull. That's well inside the weekly free quota, not the earlier 15-GPU-hour bound.
  - The secondary metric needs the judge by `rejudge`: about 3 × 27.5k Groq billable, with its own approval
    line.
- **Routed runs on Kaggle too:** the fallback is T_c, so student and teacher are served together. That's about
  20.7 GB for T_c plus ~3-4 GB for the 4B student at 16k context, on 2 × 15 GiB. A fit check (`/api/ps`, both
  loaded, nothing on the CPU) comes first.
- **8.3, the power table:** **recomputed** with `scripts/phase4_power.py` on T_c's ×3 file.
  - The `after` input and its sha256 change. Seeds, scenarios, R and margin stay the same.
  - The new table replaces 8.3's before any routed run, with its own test.
  - **What to expect:** at a teacher pass rate p̄ the headroom is 0.05 × p̄. At 85% that's +0.0425, against
    +0.046 now.
  - Per-task run variance p(1−p) is larger for a weaker teacher. So power falls on both counts, and the claim
    gets harder, not easier.
- **Cost (8.2):** T_c alone is self-hosted, so its marginal list-price cost is $0, and "beats the teacher on
  cost" has no meaning. The cost claim needs a sourced, dated `prices.toml` entry for a hosted
  Qwen3-30B-A3B-Instruct-2507 (section 7, question 8). If no source is found, the claim is dropped and the
  write-up says so.
- **What B costs the headline:** "≥ 95% of a 30B mixture-of-experts model (3B active)" is a weaker result than
  "≥ 95% of gpt-oss-120b", and the README would say which.

## Outside section 8 (no amendment needed; before the first export)

- `distill/export.py`'s Modelfile `LICENSE` line says "teacher data from gpt-oss-120b". With option c it must
  name T_c. Check T_c's license on its model card first (Qwen publishes the 2507 instruct models as
  Apache-2.0); a test should pin the line to `teachers.toml`.
- The README and the blog posts name the teacher in the same way.

## The dated line, for whichever variant is applied

> **Amended YYYY-MM-DD (option c, #72; teacher Qwen3-30B-A3B-Instruct-2507 Q4, selected 2026-10-31):** [variant A / B].
> Changed: [8.1 sentence / reference / 8.3 table / cost / 8.5's "gpt-oss:20b" → T_c / 8.6].
> Unchanged: suite, metrics, R=5, margin 0.95, safety gate, outcome rules.
> No routed or student result existed when this was written.
