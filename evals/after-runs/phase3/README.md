# Phase 3 evidence: the safety runs and the tiers v2 main-suite after-run

Copies of Phase 3's results from the runs folder (`..\kestrel-runs\evals\results\`), so the before and after
measurements of the safety gate are in git and not only in the runs folder and the private backups. Every
file except one is byte-identical to its runs-folder original (that one is redacted, below).
`tests/test_baselines_pinned.py` fails if a SHA-256 changes, and `.gitattributes` stores them byte for byte.
Never edit them. Each `.md` is the report `kestrel bench report` printed for the run, kept next to it.

All runs: agent `groq/openai/gpt-oss-120b`. Safety split s1.0 (29 attacks + 1 policy probe, 18 benign) with no
judge (checks only); the strict user runs all 48 tasks x3, the rubber-stamp user (approves everything) the
30 attacks x1. Results are the runs' own reports; ASR = attack success rate; the gate is 0 system-level
successes under the strict user. "Ran at" is the runs folder's commit (`meta.git`); finish times are UTC.

| File | Ran at | Finished | What it is | Result | Billable |
|---|---|---|---|---|---|
| `safety-before-smoke.json` | `6f58ee5` (tag `safety-before`) | 2026-10-15 22:24 | 5-task smoke run before the before-run; no claim | 4/5 pass | 18,738 |
| `safety-before-strict-x3.json` | `6f58ee5` (tag `safety-before`) | 2026-10-17 20:49 | before tiers v2, strict user x3 (**redacted copy**, below) | system ASR 1/29 (gate FAIL, `atk-fake-approval-mcp`), model ASR 1/29; benign every repeat 12/18 | 210,057 |
| `safety-before-rubber-attacks.json` | `6f58ee5` (tag `safety-before`) | 2026-10-17 20:50 | before tiers v2, rubber-stamp user, attacks | system ASR 0/28, model ASR 0/28 (1 run errored) | 58,447 |
| `safety-after-strict-x3.json` | `165f725` (tag `safety-after`) | 2026-10-19 18:32 | after tiers v2, strict user x3 | system ASR 0/29 (gate PASS), model ASR 1/29; benign every repeat 12/18 | 223,514 |
| `safety-after-rubber-attacks.json` | `165f725` (tag `safety-after`) | 2026-10-19 18:37 | after tiers v2, rubber-stamp user, attacks | system ASR 1/29, model ASR 1/29 | 36,287 |
| `safety-after-v21-strict-x3.json` | `75ce54c` (tag `safety-after-v2.1`) | 2026-10-22 09:12 | after tiers v2.1 (what shipped, PR #27), strict user x3 | system ASR 0/29 (gate PASS), model ASR 1/29; benign every repeat 11/18 | 230,215 |
| `safety-after-v21-rubber-attacks.json` | `75ce54c` (tag `safety-after-v2.1`) | 2026-10-23 10:11 | after tiers v2.1, rubber-stamp user, attacks | system ASR 0/29, model ASR 0/29 | 68,392 |
| `main-after-v1.1.json` | `165f725` (tag `safety-after`) | 2026-10-21 01:33 | main suite v1.1 x1 after tiers v2 (agent `2cac3804ef92`, rules `tiers-v2`, judge v2): the rule B run, superseded for v2.1 by `../main-after-v21-x3.json` | 88 pass, 11 fail, 1 error: 89% (95% CI 82-95%, n=99); rule B FAIL (`docs/tiers-v2-design.md`) | 174,130 |

The after runs used `KESTREL_IMAGE_POLICY=allowlist` (`meta.answer_policy`); the before runs predate the answer-URL
policy. Reports built on these: `evals/reports/safety-after-v2.1.md`, `evals/reports/tiers-v2.1-offline.md`.

## Redaction in safety-before-strict-x3.json

One model-written email address, in the `answer` of task `owner-4`, repeat 2, is replaced with
`[redacted-email]`. It was not in any task fixture or workspace file, and not the owner's; it was redacted
because a real-looking address doesn't belong in a public repo. Nothing else changed: `kestrel bench report`
prints byte-identical reports for the original and this copy, and `meta`, `summary`, `safety` and every
row's status are identical (only that one `answer` differs; checked 2026-11-03).

**The unredacted copy is in git history.** The original file (SHA-256 `efc1ca17…`) was committed as
`evals/safety-runs/safety-before-strict-x3.json` in `db7c1ab` and deleted in `fe79953` (both 2026-11-03), before
this redacted copy was pinned. It remains in git history, which wasn't rewritten. Found by the pre-release privacy
audit (2026-11-09). The address is a generic support-style address written by the model, not a person's, so the
owner judged it non-personal and kept the history.

- Original: `safety-before-strict-x3.json`, 356,281 bytes, SHA-256
  `efc1ca17ea7a0ce2bbb98c26b2566c00cc478297bb975831cdafb1eb117c2a42` (the hash pinned on
  `fix/audit-2026-11-03`), kept in `..\kestrel-runs\evals\results\` and the private backups
  `C:\kestrel-backup\2026-10-26\` and `C:\kestrel-backup\2026-10-28\`, not in git.
- This copy: 356,274 bytes, SHA-256 `f0d5c2a3efe695988ee2724a3d42a9090d98bdd3962e2b907d5deff5c4f1d2db`.

## SHA-256

| File | Size | SHA-256 |
|---|---|---|
| `main-after-v1.1.json` | 217,962 | `8cb09a90aae510b9295c49ef3704c7edecdc6b812dac74c866e10979e2eb818a` |
| `main-after-v1.1.md` | 3,811 | `3b2435a6bde94960a307f8d2b749a081f296e3baa7e1948ebcd9e6109cb7412a` |
| `safety-after-rubber-attacks.json` | 75,327 | `39d0db1839b1233c32965533273ceb595ddd43d168e0cc37bf76dcd73fac1e8f` |
| `safety-after-rubber-attacks.md` | 6,011 | `9e61fe1371f8400f16af471b580de783942b4cd09f61c9be822e0d63934f8214` |
| `safety-after-strict-x3.json` | 358,378 | `2ec8d7f619d3111d8f165aecf5c9600e8444dc1261b4632862a60c62b334826d` |
| `safety-after-strict-x3.md` | 9,494 | `4ec8ef80385347e6d64f25f862af304a89eda6c2c27d6aa33042f67313c24c25` |
| `safety-after-v21-rubber-attacks.json` | 73,259 | `d663322c94e01a260e0f8a51d560082287b51f85c476c9e5a67bb48b7423d81f` |
| `safety-after-v21-rubber-attacks.md` | 5,358 | `a2c2da1c89a9c4ea4524c83e4df37b2d275053c69ee7d70b444b50ea4c3d83be` |
| `safety-after-v21-strict-x3.json` | 355,767 | `bca96d70b9f2aa1c62cf68afd7807b5232c09fccf5d1b82c15a732717aa3031d` |
| `safety-after-v21-strict-x3.md` | 9,190 | `7310a3bb9e644bc447342d4efbc56dc3d6af867d7717009e56b817c940e83356` |
| `safety-before-rubber-attacks.json` | 69,813 | `f96cd786d24862200f3891c746e05a73934171ff463df1dca949bec5c9e12fed` |
| `safety-before-rubber-attacks.md` | 2,945 | `eb9f1742aa74967dc1892f97e9a4dd4183be7c63872cd10cc7a02dbe56225e6c` |
| `safety-before-smoke.json` | 16,729 | `beba90a97ed0a4ae77cfc8f069667f0917b742bd5cdd9814aff2021ed09f7b0a` |
| `safety-before-smoke.md` | 3,021 | `c7908a41f55aee01fe10e62072124b6c12783baa8746eeb861200e8bd5995e94` |
| `safety-before-strict-x3.json` (redacted) | 356,274 | `f0d5c2a3efe695988ee2724a3d42a9090d98bdd3962e2b907d5deff5c4f1d2db` |
| `safety-before-strict-x3.md` | 6,636 | `248b5ecf79179d9fac42610aad7964a6d6b88440a0402bb8c7b1addd072637c5` |
