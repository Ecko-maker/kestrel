# Pinned after-runs (the inputs of Phase 3 verdict reports)

Copies of finished "after" results from the runs folder (`..\kestrel-runs\evals\results\`), kept here
so each verdict report's inputs are in git. Same rules as `evals/baselines/`: these copies are
byte-identical to the runs-folder files, `tests/test_baselines_pinned.py` fails if a SHA-256 changes,
and `.gitattributes` stores them byte for byte (no line-ending conversion). Never edit them.

| File | Pass rate (95% CI) | Size | SHA-256 |
|---|---|---|---|
| `main-after-v21-x3.json` | 269/300 = 90% (85-94%, per task, n=100) | 652,776 bytes | `8948b434fdcf77d0f5deab051dfb40b96fe564a066695fd4930fa81f57dfbcbb` |
| `student-safety-strict-x3.json` | system ASR 0/29, model ASR 5/29; benign every repeat 10/18 | 324,134 bytes | `61f35e664ae5087840125f5bbe09c16ef7eb12744456831be07e7ef208a54cc1` |
| `student-safety-rubber-attacks.json` | system ASR 3/29, model ASR 4/29 | 73,002 bytes | `de1a273faff2cf439e2835f8a7ee47e5a8a1becd553819697c439818b698d8d4` |
| `student-main-x1.json` | 90/100 = 90% (84-96%, per task, n=100), checks only | 207,509 bytes | `781efe2c5fba544417de3ea275465a37ff0fc0a150ced06ad156083af618957b` |
| `routed-main-r5.json` | 448/500 = 90% (84-94%, per task, n=100), checks only | 1,079,887 bytes | `02af56169f68e55fef608c199a20c942e1824ca4afd45dbd2c345d608f786376` |
| `routed-safety-S-strict-x3.json` | system ASR 0/17, model ASR 2/17; benign every repeat 9/11 | 211,889 bytes | `f131edaa95989ea8ea7c47681414a88a464817874d261341a6c1b733ce212310` |
| `routed-safety-S-rubber-attacks.json` | system ASR 1/17, model ASR 2/17 | 44,103 bytes | `f51274f6ff00c1166aaa77a9b1cc856a8f655794018ce9d03ba6f9550431c3c4` |

## routed-main-r5.json, routed-safety-S-strict-x3.json and routed-safety-S-rubber-attacks.json

Section 8's routed runs, the inputs of `evals/reports/phase4-result.md` (outcome: FAIL; `docs/phase4-plan.md` 8.7, 2026-11-05):
- the routed system (`kestrel-student` on local Ollama first, `groq` + `openai/gpt-oss-120b` as the teacher), runs folder at `bf1b68b`, agent `502b95cc0fb8`, `--judge none`, default 60 s timeout;
- **main suite v1.1, ×5:** two segments, 11-04 18:39-19:46 EST (320 runs, stopped by its budget) and 11-05 15:39-16:27 EST (180 runs, `--resume`);
- **safety split s1.0, group S (29 tasks, no fetch stub):** strict ×3, 11-05 17:07-17:26 EST, and rubber-stamp ×1 on its 18 attacks (17 + 1 policy probe), 17:31-17:35 EST; tiers v2.1 rules, `KESTREL_IMAGE_POLICY=allowlist`, `KESTREL_MCP=off`;
- **Ollama:** 0.40.13 for the first segment, 0.40.14 for the second segment and group S (it updated itself at 15:08 EST on 11-05).

The `billable` totals inside these files include the student's local tokens. Groq's own use, from the traces: 68,348, 12,991 and 0.

## student-main-x1.json

The student alone on the main suite (`distill/EVAL.md` section 5's reported extra):
- `kestrel-student` on local Ollama, no router, no teacher, no judge (`--judge none`), so a pass means every
  deterministic check passed;
- suite v1.1, all 100 tasks ×1, 90 pass, 10 fail, 0 errored;
- runs folder at `a59439b` (tag `phase4-routed-runs`), finished 2026-11-03 21:08 UTC;
- `KESTREL_REQUEST_TIMEOUT=300` in that session, so its agent fingerprint (`021f32230e42`) isn't the routed runs' one.

It was run and seen before the routed main suite started, and it is **not** part of the section 8 claim
(`docs/phase4-plan.md` 8.7, 11-03). It is the "student alone" point in the results kit's cost-vs-quality chart.

## student-safety-strict-x3.json and student-safety-rubber-attacks.json

The student-alone safety runs of `docs/phase4-plan.md` 8.7 (`distill/EVAL.md` F2 and F3): the trained student
`kestrel-student` (code `5b175f1`, GGUF `53052ecf…`) on local Ollama 0.40.13, no router, no teacher, no judge.
Safety split s1.0, all 48 tasks with the strict user ×3, and the 30 attacks (29 + 1 policy probe) with the
rubber-stamp user ×1; tiers v2.1 rules, `KESTREL_IMAGE_POLICY=allowlist`, `KESTREL_MCP=off`,
`KESTREL_REQUEST_TIMEOUT=300`, Groq and Gemini blocked in the window. Runs folder at `7ec2e84`, finished
2026-11-03 02:43 and 02:52 UTC, 0 errored. Agent fingerprint `f2d6be4e907e`: the teacher's v2.1 runs'
(`bb65063bad46`) with only provider, model and request timeout changed; all 48 task hashes identical.

Reported alongside section 8, not part of the Phase 4 claim or gate. The reports written with them call them
"SMOKE TEST" (the safety report labelled every Ollama run so); that label is wrong for these runs and is fixed
on `bench/student-alone-label`. Comparison with the teacher: `evals/reports/student-safety-alone.md`.

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
