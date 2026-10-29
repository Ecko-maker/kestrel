# Main suite: power of the 5-point non-inferiority check

Analysis only, no model calls (`scripts/main_suite_power.py`, 2026-10-21). Question: if the agent under test is identical to the reference agent, how often does `kestrel bench compare` give a paired 95% lower bound above -5 points?

**Data** (reference agent `cc5c16377662`, suite 1.1, judge v2, all measured before 2026-10-21):
- `baseline-v1.1-judge-v2-final.json` (sha256 `bfd6bdeb6c3c`) and `run2-v1.1-judge-v2-final.json` (`29a607859aee`): 100 tasks each;
- `ci-repeat3-2026-10-07.json` (`4750d773e346`): the 16-task CI subset x 3.

**Model.** Each task's pass probability ~ Beta(1.44, 0.13), fitted by maximum likelihood on every task's passes out of its runs (84 tasks with 2 runs, 16 with 5). Mean pass probability 91.9%; it predicts about 11 tasks flipping between two runs of the same agent, against 9 observed (baseline vs run 2). For an identical agent the after-run is expected -0.1 points from the baseline and -0.6 from baseline + run 2: a task that passed every measured run still has a true pass probability a little below 1, so a fixed reference that happened to pass is slightly optimistic (regression to the mean). Each simulated after-run draws every task's probability from its posterior and then R pass/fail runs; the reference is the real file (or both files, as repeats), fixed. The statistic is compare's own (per-task pass fraction, paired percentile bootstrap) with 2,000 inner resamples; 2,000 simulated after-sides per row, seed 2026.

| Reference | After-runs | P(lower bound > -5) for an unchanged agent | Mean lower bound | Margin an unchanged agent clears 80% of the time | Billable (after side) | Groq days |
|---|---|---|---|---|---|---|
| 1 reference (baseline) | 1 | **31%** | -5.8 pts | 8.0 pts | ~174k | 1 |
| 1 reference (baseline) | 2 | **48%** | -4.9 pts | 6.5 pts | ~348k | 2 |
| 1 reference (baseline) | 3 | **56%** | -4.7 pts | 6.3 pts | ~522k | 3 |
| 1 reference (baseline) | 4 | **61%** | -4.5 pts | 6.0 pts | ~697k | 4 |
| 1 reference (baseline) | 6 | **67%** | -4.3 pts | 5.7 pts | ~1045k | 6 |
| 1 reference (baseline) | 8 | **71%** | -4.2 pts | 5.4 pts | ~1393k | 8 |
| 2 references (baseline + run 2, per-task mean) | 1 | **40%** | -5.6 pts | 8.0 pts | ~174k | 1 |
| 2 references (baseline + run 2, per-task mean) | 2 | **53%** | -4.7 pts | 6.5 pts | ~348k | 2 |
| 2 references (baseline + run 2, per-task mean) | 3 | **66%** | -4.2 pts | 5.8 pts | ~522k | 3 |
| 2 references (baseline + run 2, per-task mean) | 4 | **68%** | -4.1 pts | 5.5 pts | ~697k | 4 |
| 2 references (baseline + run 2, per-task mean) | 6 | **76%** | -3.9 pts | 5.2 pts | ~1045k | 6 |
| 2 references (baseline + run 2, per-task mean) | 8 | **79%** | -3.7 pts | 5.0 pts | ~1393k | 8 |

Unconditional check (the reference drawn fresh too, 1 vs 1): passes 31%.

Cost: 174,130 billable per main run (measured 2026-10-21, one Groq day at `--token-budget 180000`). A second reference costs nothing: run 2 is already measured. Runs of the main suite and the safety split go on separate days.

Limits: 84 tasks have only two measured runs, so each task's probability leans on the fitted prior; the judge's own run-to-run noise is inside the measured passes, not modelled apart; tasks are treated as independent.
