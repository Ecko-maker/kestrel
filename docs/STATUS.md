# Kestrel status

Changed only through docs PRs to `main`; feature branches never edit this file. Full history: [progress.md](progress.md).

## Updated

2026-10-26 22:00 EDT (2026-10-27 02:00 UTC), from a read-only check of every branch, process and results file.

## Now

Phase 3 is closing on permission tiers v2.1. Its safety gate passed (strict system-level ASR 0/29, `evals/reports/safety-after-v2.1.md` on `phase3/tiers-v2`). The last measurement is running: the main-suite non-inferiority check under rule C (main suite ×3 against the two pinned baselines, `docs/tiers-v2-design.md` §5 on `phase3/tiers-v2`). It runs on Groq in a standalone PowerShell window in `..\kestrel-runs` (PID 13464, `evals\results\main-after-v21-x3.json`, `--wait-for-quota`). On 10-26 it stood at 198/300 tasks and was waiting for the rolling quota; it should finish around 10-28. Runs pause in Modern Standby, so keep the laptop on mains power and awake. **The single next step:** when the run ends, compute the rule C verdict offline (no model calls) and record it on `phase3/tiers-v2`; then the owner decides the Phase 3 gate.

## Phases

| Phase | State | Proof |
|---|---|---|
| 1 Foundation | done | tag `v0.1.0`; `main` |
| 2 Evals | done, except judge calibration (#21: 3 labels so far) | README baseline 92%; `evals/baselines/` on `main`; labels on `labels/judge-calibration` |
| 3 Memory and safety | in progress: tiers v2.1 gate pending; memory parked until after Phase 4 | `phase3/tiers-v2` (`evals/reports/safety-after-v2.1.md`); `phase3/memory-rag` (`docs/memory-rag-design.md`, owner decision 2026-10-26) |
| 4 Distillation | in progress: tooling done, GPU smoke passed 2026-10-20, no teacher data collected | `phase4/trace-dataset` (`docs/phase4-plan.md`, `distill/*.md`) |
| 5 Voice and launch | in progress, started early: wake word and speech in/out; demo video and posts not started | `voice/talk` (`docs/voice-wake.md`); CI red |

## Branches

Merge order: PR #14 → `phase3/tiers-v2` → `phase4/trace-dataset` (rebased) → voice. `labels/judge-calibration`, `docs/merge-plan` and this docs branch merge whenever: they cost no evals. `console/hud-theme` costs no evals only after tiers-v2 is in `main`; until then it carries tiers-v2's code. Nothing is deleted yet: the last column only proposes.

| Branch | Phase | State | Next action |
|---|---|---|---|
| `phase3/results-analysis` | 3 | PR #14 into `phase3/tiers-v2`, 1 commit, CI green, evals not run | merge into tiers-v2 (no evals) |
| `phase3/tiers-v2` | 3 | safety gate PASS; rule A part 1 not met as written; rule C running | merge to `main` after the rule C verdict and the owner's gate call (~56k) |
| `phase4/trace-dataset` | 4 | built on v0.2.0, before #9/#11; 8 conflicting files (`evals/reports/merge-rehearsal.md`) | rebase onto `main` after tiers-v2; fix #42 and the main-meta test; then merge (~56k) |
| `voice/wake-word` + `voice/talk` | 5 | stacked on tiers-v2 `75ce54c`; mypy/numpy fail CI | fix CI; rename decisions 19–20; rebase onto `main`; one PR after phase4 (~56k) |
| `console/hud-theme` | console | stacked on tiers-v2 `75ce54c`; console only | rebase onto `main` after tiers-v2; merge (no evals) |
| `labels/judge-calibration` | 2 | 3 owner labels, pushed | merge whenever (no evals) |
| `docs/merge-plan` | – | merge-rehearsal report (stale on memory-rag and the phase4 tip) | merge whenever (no evals) |
| `docs/status-2026-10-26` | – | this file, `progress.md`, CLAUDE.md rewrite | merge whenever (no evals) |
| `phase3/memory-rag` | 3 | memory + hybrid RAG design, parked (option B) | park until after the Phase 4 measurements |
| `phase3/memory` | 3 | memory backend + guidance; smoke 2 on Ollama | park; rebase after Phase 4 |
| `phase3/search-spike` | 3 | search design (in `phase3/memory`) | park; then archive as tag, delete |
| `phase3/memory-evals` | 3 | memory split m1.0 (in `phase3/memory`) | park; then archive as tag, delete |
| `phase3/memory-safety` | 3 | tip = tag `safety-before`; its content is in tiers-v2 | proposed: delete (tag exists) |
| `phase4/trace-dataset-v0` | 4 | first distill attempt, replaced | proposed: archive as tag, delete |
| `dev`, `phase2/kestrelbench`, `master` | 1–2 | fully merged into `main` | proposed: delete |
| `main` | – | = `origin/main` `880d601` | – |

Dependabot PRs #15–17 are open (checks green apart from cancelled duplicates); they cost no evals.

## Groq

- **Quota:** 200k tokens per rolling 24 h per organisation (usage frees ~24 h after it was spent); 1,000 requests/day never bind. Cached input doesn't count.
- **One consumer at a time.** No CI-triggering PR while a measurement runs.
- **Each agent-affecting PR costs ~56k:** the PR run (~28k) plus the merge-to-main run (~28k). Every new push to an open PR runs it again.
- **Calendar:**
  1. Live run (until ~10-28).
  2. Merges, one per quota day: #14 and tiers-v2 (day 1), phase4 rebased (day 2), voice (day 3). The no-eval branches go in between.
  3. Teacher data: dry-run estimate first (`distill/README.md` says ~253k, 2 days); needs the owner's OK and the teacher decision (#36).
  4. Kaggle QLoRA training (no Groq; under 1 GPU hour).
  5. Student evals: the student on Ollama locally, the judge only on Groq.

## Owner decisions open

1. **Phase 3 tiers gate**, after the rule C verdict: rule A part 1 is not met as written (`evals/reports/rule-a-power.md` on tiers-v2 reads it as noise). Ship or not.
2. **Memory before or after teacher data:** decided 2026-10-26 (option B: Phase 4 first). It is recorded only on unmerged branches (`phase4/trace-dataset` CLAUDE.md, `docs/memory-rag-design.md`); it lands with them.
3. **Park the v1.2 suite items until after Phase 4** (D1 says "after the Phase 3 gate"): `sandbox-parent-dir` leak check, `act-email-priya-actions` ambiguity, hard split.
4. **CI threshold:** the proposal is to keep 75% (`evals/reports/ci-threshold.md`, on the tiers-v2 line).
5. **`ben-note-from-mcp`:** its `docs__lookup` approval rule is in (2026-10-15). It still failed 1 of 3 strict runs on v2.1; the reports give rule A with and without it. No separate question is recorded; decide it with item 1.
6. Also waiting: the teacher decision (#36, Groq §6.3(e)); labels for #21; `docs/phase4-plan.md` open question 13 (power of the quality claim); tiers-v2 §6 questions 4–8.

## Docs on unmerged branches

A new decision or issue keeps a slug until its branch merges; `main` assigns the number then. The numbers the branches use today:

| Pending name | Branch(es) | Number used there |
|---|---|---|
| D-pending-safety-repeats | memory-safety line (tiers-v2 and all descendants, memory-*) | 15 (same text everywhere) |
| D-pending-tiers-v2 | tiers-v2, results-analysis, memory-rag, console, voice, v0 | **16** |
| D-pending-memory-split | memory-evals, search-spike, memory | **16** |
| D-pending-memory-search | memory | **17** |
| D-pending-hud-console | console, voice | **17** |
| D-pending-distill-dataset-v0 | trace-dataset-v0 | **17** |
| D-pending-memory-guidance | memory | **18** |
| D-pending-voice-wake | wake-word, talk | **18** |
| D-pending-talk-whisper, D-pending-send-countdown | talk | **19, 20** |
| D-pending-distill-data, -distill-taskset, -distill-overlap, -distill-rubric-checks, -student-qlora, -prompt-format, -router-v1 | phase4 | **19, 20**, 21–25 |
| KI-pending-tool-not-found, -errored-row-fingerprint, -content-check-silent, -taint-on-run | tiers-v2 and descendants | 37, 43, 44, 45 |
| KI-pending-console-taint | console, voice | 46 |
| KI-pending-wake-owner-machine, -self-interruption, -vocab-hint (closed), -cancel-unmeasured | voice | 47–50 |
| KI-pending-memory-isolation, -memory-attack-surface, -memory-split-limits | memory-evals, search-spike, memory | **25, 26**, 27 |
| KI-pending-memory-search-limits, -estimate-from-cache | memory | 28, 29 |
| KI-pending-safety-prompts-pinned … -router-tiers-v2 (12 issues) | phase4 | 30–36, 38–42 |
| KI-pending-student-no-memory | phase4 | 51 |
| KI-pending-training-on-bench, -secret-scrub-v0 | trace-dataset-v0 | **25, 26** |

Clashes (bold): decisions **16, 17, 18, 19, 20**; issues **25, 26**. Phase4 skips decisions 13–18 (it predates #9/#11). The merge rehearsal's "next free issue is 51" is wrong: phase4 uses 51. On `main` the next free numbers are decision 15 and issue 25; this PR's new issue is #52 (above every number in use).

## Data outside git

| What | Where | Backed up |
|---|---|---|
| Phase 3 results (11 JSON, plus `.md`/`.log`) and their traces (`logs/bench.db`) | `..\kestrel-runs\evals\results\`, `..\kestrel-runs\logs\` | JSON + db, 2026-10-26 (live file as a PARTIAL snapshot) |
| Phase 2 bench traces, chat traces, approvals | `logs/bench.db`, `logs/traces.db`, `logs/approvals.jsonl` | yes |
| Voice recordings (your voice, room audio; private) | `data/wake/`, `data/talk/` | yes |
| Phase 2 intermediate results (superseded by `evals/baselines/`) | `evals/results/` | no |
| Kaggle smoke bundle (rebuildable) | `dist/kaggle-smoke/` | no |
| Kaggle notebook and dataset versions | Kaggle | – |

Backup: `C:\kestrel-backup\2026-10-26\` (116 files, 172.5 MB, `MANIFEST.txt` with SHA-256 per file). It is on the same disk: copy it to a USB drive or private cloud storage. `.env` (both folders) is never backed up here.
