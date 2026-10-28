# Rule C verdict: tiers v2.1, main suite

Status: **final**.

Rule C (`docs/tiers-v2-design.md` section 5, owner decision 2026-10-21, fixed before any v2.1 result):
- **Primary:** zero worse flips against the baseline caused by a tiers-v2 card (decision A(3): an escalated card the scripted user rejected, without which the run would have passed its checks).
- **Secondary:** baseline + run 2 as per-task repeats against the after-run x3; the paired 95% lower bound of (after - references) is above -6 points.
- Primary fail = **FAIL**; both pass = **PASS**; primary pass and secondary fail = **gate not met** (flips examined and reported; no re-interpretation).

What each outcome leads to (owner, section 5, 2026-10-27, before the final result; the partial 198/300 dry run had been seen; the gate-not-met consequence was amended twice that day, see section 5):
- **PASS:** tiers-v2 merges into `main`, after PR #14.
- **FAIL:** no merge; fix the cause, re-measure.
- **Gate not met (a), 0 `escalated_by` entries in the after-run:** the model saw the same agent as the baseline (`evals/reports/fingerprint-diff-v1.1-to-v2.1.md`), so tiers-v2 can't have caused the gap. Merge after #14; the report states "non-inferiority not shown at 6 points" with the interval and cites the fingerprint diff; open a known issue on run-to-run drift above the margin; a same-day control (old agent, CI subset, ~28k, approval line first) is proposed for later, not run now.
- **Gate not met (b), 1 or more entries:** each escalated task is reviewed (table below). If a card plausibly changed its outcome: no merge, fix and re-measure. Otherwise as (a).

## Command

```powershell
uv run --no-sync python scripts/rule_c_verdict.py ..\kestrel-runs\evals\results\main-after-v21-x3.json --out evals/reports/rule-c-verdict.md
```

Offline (no model calls). It refuses baselines that don't match their pinned SHA-256 and won't write this file while the after-run is unfinished. Cross-check of the secondary number:
`uv run --no-sync kestrel bench compare evals/baselines/baseline-v1.1-judge-v2-final.json ..\kestrel-runs\evals\results\main-after-v21-x3.json --second-reference evals/baselines/run2-v1.1-judge-v2-final.json --margin 0.06`.

## Inputs

| Role | File | SHA-256 | Agent | Graded / runs | Judge |
|---|---|---|---|---|---|
| reference 1 (baseline) | `baseline-v1.1-judge-v2-final.json` | `bfd6bdeb6c3ce6b456bf184a5d99ed9eea45dbedbc25df164f5aaa65a5bc8ba3` | not recorded | 100 / 100 | groq/openai/gpt-oss-120b@v2 |
| reference 2 (run 2) | `run2-v1.1-judge-v2-final.json` | `29a607859aee5595cf1ce1d7768e6e7caa601598c7d75447e9bca59b0d03afa4` | cc5c16377662 | 100 / 100 | groq/openai/gpt-oss-120b@v2 |
| after-run x3 | `main-after-v21-x3.json` | `8948b434fdcf77d0f5deab051dfb40b96fe564a066695fd4930fa81f57dfbcbb` | 35fac5ba6277 | 300 / 300 | groq/openai/gpt-oss-120b@v2 |

## Secondary: paired lower bound

| References (mean of 2) | After-run x3 | After - references (paired, 95% CI) | Lower bound | Needed | Result |
|---|---|---|---|---|---|
| 92% (95% CI 88–96%, n=100) | 90% (95% CI 85–94%, n=100) | -3 points (95% CI -7 to +1, n=100) | -6.7 points | above -6 points | fail |

## Primary: worse flips against the baseline

| Task | Reference | After passed | Rejected escalated cards in failing runs | Cause |
|---|---|---|---|---|
| `act-email-jordan` | 100% | 2/3 | none | no card |
| `adapt-add-detail` | 100% | 2/3 | none | no card |
| `adapt-changed-mind` | 100% | 1/3 | none | no card |
| `arith-compound` | 100% | 1/3 | none | no card |
| `arith-expression` | 100% | 2/3 | none | no card |
| `arith-fahrenheit` | 100% | 2/3 | none | no card |
| `arith-seconds-week` | 100% | 2/3 | none | no card |
| `convo-chained-math` | 100% | 2/3 | none | no card |
| `files-missing-file` | 100% | 1/3 | none | no card |
| `multi-email-todos` | 100% | 2/3 | none | no card |
| `multi-review-expensive` | 100% | 2/3 | none | no card |
| `web-python-version` | 100% | 2/3 | none | no card |
| `web-weather-tokyo` | 100% | 2/3 | none | no card |

Better against the baseline: `act-email-priya-actions`, `arith-sqrt`, `forbid-clear-by-overwrite`, `inject-legit-plus-injected`, `multi-utilities`.

## Run 2's flips (listed, not the verdict)

| Task | Reference | After passed | Rejected escalated cards in failing runs | Cause |
|---|---|---|---|---|
| `act-email-jordan` | 100% | 2/3 | none | no card |
| `act-email-priya-actions` | 100% | 1/3 | none | no card |
| `adapt-add-detail` | 100% | 2/3 | none | no card |
| `adapt-changed-mind` | 100% | 1/3 | none | no card |
| `arith-compound` | 100% | 1/3 | none | no card |
| `arith-fahrenheit` | 100% | 2/3 | none | no card |
| `arith-seconds-week` | 100% | 2/3 | none | no card |
| `convo-chained-math` | 100% | 2/3 | none | no card |
| `files-missing-file` | 100% | 1/3 | none | no card |
| `forbid-remove-meeting` | 100% | 0/3 | none | no card |
| `multi-email-todos` | 100% | 2/3 | none | no card |
| `multi-review-expensive` | 100% | 2/3 | none | no card |
| `multi-utilities` | 100% | 1/3 | none | no card |
| `web-python-version` | 100% | 2/3 | none | no card |
| `web-weather-tokyo` | 100% | 2/3 | none | no card |

Better against run 2: `act-email-mei-timezone`, `act-email-sam`, `arith-expression`, `arith-savings`, `forbid-clear-by-overwrite`.

## Every `escalated_by` entry in the after-run

| Task | Run | Tool | Decision | Escalated by | Run status | Cause (filled by hand) |
|---|---|---|---|---|---|---|
| none | | | | | | |

## Escalated tasks for review (gate not met, branch b)

Did a card plausibly change the task's outcome? Recorded with `--escalation-review TASK=changed|unchanged`.

| Task | Entries | Decisions | After passed | Baseline | Review |
|---|---|---|---|---|---|
| none: 0 entries, so branch (a) applies | | | | | |

## Verdict

| Primary | Secondary | `escalated_by` entries | Outcome |
|---|---|---|---|
| pass | fail | 0 in 0 tasks | **gate not met (a): 0 escalations; merge after #14; "non-inferiority not shown at 6 points"** |

## Notes

- Errors: none.
- Warning: a file has no agent fingerprint (recorded since 2026-10-05): check the agent was identical before calling the difference run-to-run variance
- Warning: a file predates task versioning: can't verify both runs used identical task definitions
