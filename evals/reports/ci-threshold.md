# CI subset: run-to-run variance and the gate threshold (#22)

**Run:** `kestrel bench run --subset ci --repeat 3 --wait-for-quota`, from the runs folder (`..\kestrel-runs`) at 642e8c7, 2026-10-07 01:24 UTC. Groq `openai/gpt-oss-120b`, judge the same model (v2), suite v1.1, agent fingerprint **`cc5c16377662`** (same agent as both pinned baselines). Results: `evals/reports/ci-repeat3-2026-10-07.json` (SHA-256 starts `4750d773e346a25e`).

**Cost:** 119,309 tokens, **66,573 billable** (dry-run estimate 79,437: 16% over-estimated), 0 errors, 0 skips, no daily-limit waits.

## Scores

| Repeat | Passed | Pass rate |
|---|---|---|
| 1 | 13/16 | 81% |
| 2 | 14/16 | 88% |
| 3 | 15/16 | 94% |
| **Mean** | 42/48 | **88% (95% bootstrap CI over tasks 73-100%, n=16)** |

**Flips** (outcome differs between repeats): 2 of 16 tasks.
- `convo-chained-math` F P P: calculator skipped once (the known flip habit).
- `files-contact-email` F F P: asked which Sam instead of reading `contacts.csv`.

**Failed every repeat:** `multi-scale-recipe` (asks for the amounts instead of reading the recipe file). It failed in both pinned baselines and on PR #11 too: a systematic failure, not noise.

## Deriving the threshold

The gate is one run of 16 tasks, so its score is a sum of 16 coin flips with different odds. Each task's pass probability is estimated from 6 observations of the same agent: the 3 repeats, the 2 pinned full runs (`evals/baselines/`), and the CI run on PR #11 (13/16). Non-reliable tasks: `files-contact-email` 4/6, `convo-chained-math` 4/6, `act-email-sam` 5/6, `adapt-casual-email` 5/6, `multi-scale-recipe` 0/6. All others passed 6/6.

The score distribution of one CI run is simulated from those probabilities (200,000 draws, seed 2026). Two estimates bracket the truth:
- **observed:** p = passes / observations (a task never seen to fail can't fail);
- **Jeffreys:** p = (passes + 0.5) / (observations + 1). Pessimistic: every 6/6 task gets p = 0.93.

**False alarm** = a run of the unchanged agent scores below the gate:

| Gate | observed p | Jeffreys p |
|---|---|---|
| 62% (10/16) | 0.0% | 0.5% |
| 69% (11/16) | 0.0% | 2.6% |
| **75% (12/16), today** | **0.3%** | **10.1%** |
| 81% (13/16) | 4.6% | 28.5% |

**Power** = a real regression is caught. Here a regression means r reliable tasks start failing every time:

| Regression | caught at 75% (observed / Jeffreys) | caught at 69% |
|---|---|---|
| 1 task | 5% / 26% | 0% / 9% |
| 2 tasks | 26% / 54% | 5% / 24% |
| 3 tasks | 69% / 84% | 26% / 52% |
| 4 tasks | 100% / 99% | 69% / 82% |

## Proposal (ci.yml not changed)

**Keep the gate at 75% (12/16).** Under the measured odds, an unchanged agent fails it about 1 time in 300. Even the pessimistic prior keeps that at 1 in 10. It reliably catches a regression of 4 tasks (a quarter of the subset), and usually catches 3. Lowering it to 69% would almost never false-alarm, but would miss a 3-task regression about half the time. 81% would false-alarm on 1 PR in 20 under the measured odds.

What the subset is: a smoke test for large regressions. A 1- or 2-task change is below what 16 tasks can see. That needs the full suite with `kestrel bench compare` (paired interval, non-inferiority margin 5 points).

Possible later refinement, not proposed now: on a gate failure, rerun the subset once and fail only if both runs fail. That would cut false alarms to about their square, for ~27k more tokens on a failing PR only.

Scripts (offline, no model calls): the per-repeat and distribution numbers were computed from the results files above. Re-derive with any `--repeat` results file and the two pinned baselines.
