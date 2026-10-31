# Option c teacher selection (known issue #72, pre-registered 2026-10-30)

Slice: the 20 check-graded distill tasks listed in #72, once each, `--judge none`.

| Candidate | Check-pass | Errored | Valid tool calls | Harmony leaks (runs) | Mean s/task | Fits 2 x T4 | Out |
|---|---|---|---|---|---|---|---|
| gpt-oss:20b | 15/20 (75%) | 0 | 24/27 | 0 | 7 | yes | - |
| Qwen3-30B-A3B-Instruct-2507 | 15/20 (75%) | 0 | 28/28 | 0 | 2 | yes | - |

**Winner: Qwen3-30B-A3B-Instruct-2507** (check-pass within 2 tasks (15 vs 15); more valid tool calls).
