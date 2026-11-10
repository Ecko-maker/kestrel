# Pinned controls (the inputs of drift checks)

Copies of finished control runs from the runs folder (`..\kestrel-runs\evals\results\`), kept here so
each drift check's inputs are in git. Same rules as `evals/baselines/` and `evals/after-runs/`: these
copies are byte-identical to the runs-folder files, `tests/test_baselines_pinned.py` fails if a SHA-256
changes, and `.gitattributes` stores them byte for byte (no line-ending conversion). Never edit them.

| File | Pass rate (95% CI) | Size | SHA-256 |
|---|---|---|---|
| `control-old-ci-2026-10-29.json` | 27/32 runs; 84% (66-100%, per task, n=16) | 54,541 bytes | `0c48e60decdfb3a368ba2736fec5bf810f1656a72d2b694d57018b9dec3d2b8c` |
| `control-old-ci-2026-11-03.json` | 28/32 runs; 88% (72-100%, per task, n=16) | 54,479 bytes | `5a587c8c37b3d921b1843959dfd502eb1c625671d0b83367c60f9530fbf83d20` |
| `control-old-ci-2026-11-08.json` | 30/32 runs; 94% (81-100%, per task, n=16) | 56,416 bytes | `14e8f42098dd1b93d507e88d4005928ae7b4341d4bf7a322ce0c38f6afd1a46b` |

## control-old-ci-2026-10-29.json

The same-day drift control for known issue #58 (`docs/known-issues.md`; pre-registered there before the
run): suite v1.1, CI subset, 16 tasks x 2 repeats, 0 errored, 0 excluded, 0 skipped. Run 1 88%, run 2
81%. Agent `groq/openai/gpt-oss-120b`, fingerprint `cc5c16377662` (the old agent, before tiers v2),
judge v2 (`groq/openai/gpt-oss-120b`, not independent). Runs folder at `c7c0d4f`. Finished
2026-10-29 21:58 EDT. Billable tokens 39,706 (from the file's summary), within the 55,000 approval
(docs/STATUS.md, Groq approvals).

Compared against the old agent's 10-07 x3 (`ci-repeat3-2026-10-07.json`, runs folder) and the new agent's
CI runs of 10-29; the result is under known issue #58.

## control-old-ci-2026-11-03.json

The same-week drift control of `docs/phase4-plan.md` section 8.3, read by the rule pre-registered on
2026-11-01 (8.7). The setup:
- suite v1.1, CI subset, 16 tasks x 2 repeats, 0 errored, 0 excluded, 0 skipped; run 1 13/16, run 2 15/16;
- agent `groq/openai/gpt-oss-120b`, fingerprint `cc5c16377662` (the old agent);
- judge v2 (`groq/openai/gpt-oss-120b`, not independent);
- runs folder at `c7c0d4f`; agent runs 2026-11-03 14:03-14:09 UTC (09:03-09:09 EST), finished 14:08:54 UTC.

Billable: **49,953** from the file's summary (agent 45,034 + judge 4,919), within the 55,000 approval
(docs/STATUS.md, Groq approvals).

**Reading:** `kestrel bench compare evals/reports/ci-repeat3-2026-10-07.json` with this file gives 88% vs 88%,
B - A **+0 points (95% CI -10 to +11, n=16)**. That isn't entirely below 0, so gross drift is not shown, and
section 8 is compared with `--drift none`. Against the 10-29 control (context only): +3 points (95% CI +0
to +9).

## control-old-ci-2026-11-08.json

Section 9's same-week drift control (run 3, `docs/phase4-section9-proposal.md` 9.4), read by the rule
pre-registered on 2026-11-01 (8.7). The setup:
- suite v1.1, CI subset, 16 tasks x 2 repeats, 0 errored, 0 excluded, 0 skipped; run 1 15/16, run 2 15/16;
- agent `groq/openai/gpt-oss-120b`, fingerprint `cc5c16377662` (the old agent);
- judge v2 (`groq/openai/gpt-oss-120b`, not independent);
- runs folder at `c7c0d4f`; started 2026-11-08 23:03 EST (queue attempt 27, after waiting for quota), finished 23:09 EST.

Billable: **52,823** from the file's summary (agent 47,587 + judge 5,236), within section 9's 303,000 approval.

**Reading:** against `evals/reports/ci-repeat3-2026-10-07.json`, 88% vs 94%, B - A **+6 points (95% CI +0 to
+17, n=16)**: not entirely below 0, so gross drift is not shown (`--drift none`). Against the 10-29 control
(context only): +9 points (95% CI +0 to +25). The rule guards against downward drift only; this one is upward
(`evals/reports/phase4-section9-verdict.md`, "Drift").

Copied byte for byte on 2026-11-09 after the same scan as section 9's routed runs (`evals/after-runs/README.md`).
