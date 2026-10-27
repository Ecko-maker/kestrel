# Rule C verdict: tiers v2.1, main suite

Status: **template**, filled by the command below when the run ends.

Rule C (`docs/tiers-v2-design.md` section 5, owner decision 2026-10-21, fixed before any v2.1 result):
- **Primary:** zero worse flips against the baseline caused by a tiers-v2 card (decision A(3): an escalated card the scripted user rejected, without which the run would have passed its checks).
- **Secondary:** baseline + run 2 as per-task repeats against the after-run x3; the paired 95% lower bound of (after - references) is above -6 points.
- Primary fail = **FAIL**; both pass = **PASS**; primary pass and secondary fail = **gate not met** (flips examined and reported; no re-run, no re-interpretation).

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
| after-run x3 | `main-after-v21-x3.json` | _(to fill)_ | 35fac5ba6277 (expected) | _(to fill)_ / 300 | _(to fill)_ |

## Secondary: paired lower bound

| References (mean of 2) | After-run x3 | After - references (paired, 95% CI) | Lower bound | Needed | Result |
|---|---|---|---|---|---|
| _(to fill)_ | _(to fill)_ | _(to fill)_ | _(to fill)_ | above -6 points | _(to fill)_ |

## Primary: worse flips against the baseline

| Task | Reference | After passed | Rejected escalated cards in failing runs | Cause |
|---|---|---|---|---|
| _(to fill)_ | | | | |

## Run 2's flips (listed, not the verdict)

| Task | Reference | After passed | Rejected escalated cards in failing runs | Cause |
|---|---|---|---|---|
| _(to fill)_ | | | | |

## Every `escalated_by` entry in the after-run

| Task | Run | Tool | Decision | Escalated by | Run status | Cause (filled by hand) |
|---|---|---|---|---|---|---|
| _(to fill)_ | | | | | | |

## Verdict

| Primary | Secondary | Outcome |
|---|---|---|
| _(to fill)_ | _(to fill)_ | _(to fill)_ |

## Notes

Errors, warnings and each flip's cause: _(to fill)_
