# Pinned controls (the inputs of drift checks)

Copies of finished control runs from the runs folder (`..\kestrel-runs\evals\results\`), kept here so
each drift check's inputs are in git. Same rules as `evals/baselines/` and `evals/after-runs/`: these
copies are byte-identical to the runs-folder files, `tests/test_baselines_pinned.py` fails if a SHA-256
changes, and `.gitattributes` stores them byte for byte (no line-ending conversion). Never edit them.

| File | Pass rate (95% CI) | Size | SHA-256 |
|---|---|---|---|
| `control-old-ci-2026-10-29.json` | 27/32 runs; 84% (66-100%, per task, n=16) | 54,541 bytes | `0c48e60decdfb3a368ba2736fec5bf810f1656a72d2b694d57018b9dec3d2b8c` |

## control-old-ci-2026-10-29.json

The same-day drift control for known issue #58 (`docs/known-issues.md`; pre-registered there before the
run): suite v1.1, CI subset, 16 tasks x 2 repeats, 0 errored, 0 excluded, 0 skipped. Run 1 88%, run 2
81%. Agent `groq/openai/gpt-oss-120b`, fingerprint `cc5c16377662` (the old agent, before tiers v2),
judge v2 (`groq/openai/gpt-oss-120b`, not independent). Runs folder at `c7c0d4f`. Finished
2026-10-29 21:58 EDT. Billable tokens 39,706 (from the file's summary), within the 55,000 approval
(docs/STATUS.md, Groq approvals).

Compared against the old agent's 10-07 x3 (`ci-repeat3-2026-10-07.json`, runs folder) and the new agent's
CI runs of 10-29; the result is under known issue #58.
