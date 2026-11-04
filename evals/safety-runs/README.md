# Pinned Phase 3 safety runs (the inputs of the safety gate)

Copies of the Phase 3 safety-split results from the runs folder (`..\kestrel-runs\evals\results\`), so the
before and after measurements of the prompt-injection gate are in git and not only in the runs folder and the
hand-made backups (audit 2026-11-03). Same rules as `evals/after-runs/`: byte-identical to the runs-folder
files, `tests/test_baselines_pinned.py` fails if a SHA-256 changes, and `.gitattributes` stores them byte for
byte. Never edit them.

All six: agent `groq/openai/gpt-oss-120b`, safety split s1.0 (29 attacks + 1 policy probe, 18 benign), no
judge (checks only). Strict user = all 48 tasks x3; rubber-stamp user (approves everything) = the 30 attacks
x1. The numbers are the runs' own `.md` reports.

| File | Tag (runs folder at) | Finished (UTC) | Result | Size | SHA-256 |
|---|---|---|---|---|---|
| `safety-before-strict-x3.json` | `safety-before` (`6f58ee5`) | 2026-10-17 20:49 | system ASR 1/29 (gate FAIL), model ASR 1/29; benign every repeat 12/18 | 356,281 bytes | `efc1ca17ea7a0ce2bbb98c26b2566c00cc478297bb975831cdafb1eb117c2a42` |
| `safety-before-rubber-attacks.json` | `safety-before` (`6f58ee5`) | 2026-10-17 20:50 | system ASR 0/28, model ASR 0/28 (1 run errored) | 69,813 bytes | `f96cd786d24862200f3891c746e05a73934171ff463df1dca949bec5c9e12fed` |
| `safety-after-strict-x3.json` | `safety-after` (`165f725`, tiers v2) | 2026-10-19 18:32 | system ASR 0/29 (gate PASS), model ASR 1/29; benign every repeat 12/18 | 358,378 bytes | `2ec8d7f619d3111d8f165aecf5c9600e8444dc1261b4632862a60c62b334826d` |
| `safety-after-rubber-attacks.json` | `safety-after` (`165f725`, tiers v2) | 2026-10-19 18:37 | system ASR 1/29, model ASR 1/29 | 75,327 bytes | `39d0db1839b1233c32965533273ceb595ddd43d168e0cc37bf76dcd73fac1e8f` |
| `safety-after-v21-strict-x3.json` | `safety-after-v2.1` (`75ce54c`, tiers v2.1) | 2026-10-22 09:12 | system ASR 0/29 (gate PASS), model ASR 1/29; benign every repeat 11/18 | 355,767 bytes | `bca96d70b9f2aa1c62cf68afd7807b5232c09fccf5d1b82c15a732717aa3031d` |
| `safety-after-v21-rubber-attacks.json` | `safety-after-v2.1` (`75ce54c`, tiers v2.1) | 2026-10-23 10:11 | system ASR 0/29, model ASR 0/29 | 73,259 bytes | `d663322c94e01a260e0f8a51d560082287b51f85c476c9e5a67bb48b7423d81f` |

The after runs used `KESTREL_IMAGE_POLICY=allowlist` (`meta.answer_policy`); the before runs predate the
answer-URL policy (`answer_policy` null). Tiers v2.1 is what shipped (PR #27); its report is
`evals/reports/safety-after-v2.1.md`. Not pinned: `safety-before-smoke.json` (a smoke run, no claim).
