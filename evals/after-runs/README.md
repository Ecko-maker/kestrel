# Pinned after-runs (the inputs of Phase 3 verdict reports)

Copies of finished "after" results from the runs folder (`..\kestrel-runs\evals\results\`), kept here
so each verdict report's inputs are in git. Same rules as `evals/baselines/`: these copies are
byte-identical to the runs-folder files, `tests/test_baselines_pinned.py` fails if a SHA-256 changes,
and `.gitattributes` stores them byte for byte (no line-ending conversion). Never edit them.

| File | Pass rate (95% CI) | Size | SHA-256 |
|---|---|---|---|
| `main-after-v21-x3.json` | 269/300 = 90% (85-94%, per task, n=100) | 652,776 bytes | `8948b434fdcf77d0f5deab051dfb40b96fe564a066695fd4930fa81f57dfbcbb` |

## main-after-v21-x3.json

The rule C after-run for permission tiers v2.1 (`docs/tiers-v2-design.md` section 5): suite v1.1, main
split, 100 tasks x 3 repeats, 0 errors, 0 skipped. Agent `groq/openai/gpt-oss-120b`, fingerprint
`35fac5ba6277` (approval rules `tiers-v2.1`), judge v2 (`groq/openai/gpt-oss-120b`, not independent).
Runs folder at `75ce54c`. Billable tokens 541,468 (all segments, from the file's summary).

It was run in several resumed segments; `--resume` refuses a different agent fingerprint, provider, model,
judge or split version, so all segments share them. The file keeps one `meta` block (the last
segment's). Segment times are the trace start times in `..\kestrel-runs\logs\bench.db` (EDT):

| Segment | Runs |
|---|---|
| 2026-10-23 18:xx | 80 |
| 2026-10-26 13:xx and 18:xx | 118 |
| 2026-10-28 15:22-15:44 (final resume) | 102 |

The main split doesn't record the answer-URL / image policy (`answer_policy` is null by design: only
the safety split's grader uses it, `src/kestrel/bench/cli.py`), so this file can't show it.

Verdict: `evals/reports/rule-c-verdict.md`.
