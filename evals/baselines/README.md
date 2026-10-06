# Pinned baselines (the "before" numbers for Phase 3)

Two full KestrelBench runs of the Phase 1/2 agent, kept here so Phase 3 changes (memory, permission
tiers, safety) can be compared against fixed files. `evals/results/` is git-ignored scratch space;
these copies are tracked and must never change. `tests/test_baselines_pinned.py` fails if either
file's SHA-256 changes, and `.gitattributes` stores them byte for byte (no line-ending conversion).

| File | Pass rate (95% CI) | Size | SHA-256 |
|---|---|---|---|
| `baseline-v1.1-judge-v2-final.json` | 92/100 = 92% (86-97%) | 174,337 bytes | `bfd6bdeb6c3ce6b456bf184a5d99ed9eea45dbedbc25df164f5aaa65a5bc8ba3` |
| `run2-v1.1-judge-v2-final.json` | 93/100 = 93% (88-98%) | 213,534 bytes | `29a607859aee5595cf1ce1d7768e6e7caa601598c7d75447e9bca59b0d03afa4` |

Both: suite v1.1 (100 tasks, 0 errors), agent `groq/openai/gpt-oss-120b`, judge v2
(`groq/openai/gpt-oss-120b`, sees 2,000 characters of tool results; not independent of the agent,
not yet calibrated against human labels, see known issue #21).

## How they were produced

- **baseline**: the v1.0 run (`baseline-full.json`, git `f33f154`) re-scored with the v1.1 checks
  (`scripts/rescore.py`), then re-graded by judge v2 from the stored answers (`kestrel bench
  rejudge`, no new agent calls). Finished 2026-10-05. It has no agent fingerprint (recorded since
  2026-10-05).
- **run2**: a fresh v1.1 run (git `353ffb1`), judge v2, finished 2026-10-06 after Groq's daily
  limit paused it. Agent fingerprint `cc5c16377662`; recomputed offline on main (`c7c0d4f`) on
  2026-10-06 and still identical, so Phase 3 starts from the same agent.

Run 2 vs baseline is in `evals/reports/run-to-run.md`; the baseline analysis in
`evals/reports/baseline-v1.1.md`.
