# Kestrel status

Changed only through docs PRs to `main`; feature branches never edit this file. Full history: [progress.md](progress.md).

## Updated

2026-10-27 16:00 EDT (20:00 UTC), from the day's sessions on `main` and `phase3/tiers-v2` (log below).

## Now

Phase 3 is closing on permission tiers v2.1. Its safety gate passed (strict system-level ASR 0/29, `evals/reports/safety-after-v2.1.md` on `phase3/tiers-v2`). The last measurement is running: the main-suite non-inferiority check under rule C (main suite ×3 against the two pinned baselines, `docs/tiers-v2-design.md` §5 on `phase3/tiers-v2`). It runs on Groq in a standalone PowerShell window in `..\kestrel-runs` (PID 13464, `evals\results\main-after-v21-x3.json`, `--wait-for-quota`). Last read 10-27 08:02: 198/300, waiting for the rolling quota; expected finish 10-27 evening to 10-28 midday. Runs pause in Modern Standby, so keep the laptop on mains power and awake. What each rule C outcome leads to is recorded and **frozen** (`a29c5f3`). **The single next step:** when the run ends, run `scripts/rule_c_verdict.py` (offline, no model calls; plus `--escalation-review` if any task was escalated) with `--out evals/reports/rule-c-verdict.md`, and commit the report on `phase3/tiers-v2`.

## 2026-10-27

In order; detail is in the linked files (all on `phase3/tiers-v2` unless marked).
1. **Approvals log** merged to `main` (PR #20, `0960cf8`): the Groq table below.
2. **Stale `main` hash** fixed here: `main` = `0960cf8`, not `880d601`.
3. **#36 (teacher terms, Groq §6.3(e)):** a follow-up to the 10-18 question is drafted, **not sent yet** (the owner sends it). Decision date: 10-31 or after the phase4 rebase, whichever is later. Already decided: no public hosting of the student. Publishing the weights (LoRA adapter): decided later, at that date.
4. **Rule C verdict script** (`c937cad`): `scripts/rule_c_verdict.py` + tests; report template `evals/reports/rule-c-verdict.md`. A partial dry run was a plumbing check only, not a verdict.
5. **Rule C outcomes recorded** (`0d3c3c0`), then **amended** before the result (`2df47f8`, `130134d`): PASS → merge after #14; FAIL → no merge, fix, re-measure; gate not met → (a) 0 `escalated_by` entries: merge after #14, report "non-inferiority not shown at 6 points", open `KI-pending-main-suite-drift`; (b) 1 or more: review each escalated task, no merge if a card plausibly changed an outcome. Rule and margin unchanged. Superseded options, the reasons, and the disclosure of the partial data seen are in §5.
6. **Fingerprint diff** (`558035a`): `cc5c16377662` → `35fac5ba6277` changes only the tiers-v2 cards (capabilities + approval rules); `evals/reports/fingerprint-diff-v1.1-to-v2.1.md`.
7. **Freeze** (`a29c5f3`): the 2df47f8 reason corrected (A was the owner's choice; it was left because of the fingerprint diff); no further changes to the rule C consequences before the final result.
8. **Voice CI:** still red. Last run 10-26 (`40406346377`, `voice/talk`): mypy can't find `pywhispercpp.model` and `numpy` in `src/kestrel/wake/transcribe.py`, on Ubuntu and Windows. No voice push since.

## Phases

| Phase | State | Proof |
|---|---|---|
| 1 Foundation | done | tag `v0.1.0`; `main` |
| 2 Evals | done, except judge calibration (#21: 3 labels so far) | README baseline 92%; `evals/baselines/` on `main`; labels on `labels/judge-calibration` |
| 3 Memory and safety | in progress: tiers v2.1 gate pending; memory parked until after Phase 4 | `phase3/tiers-v2` (`evals/reports/safety-after-v2.1.md`); `phase3/memory-rag` (`docs/memory-rag-design.md`, owner decision 2026-10-26) |
| 4 Distillation | in progress: tooling done, GPU smoke passed 2026-10-20, no teacher data collected | `phase4/trace-dataset` (`docs/phase4-plan.md`, `distill/*.md`) |
| 5 Voice and launch | in progress, started early: wake word and speech in/out; demo video and posts not started | `voice/talk` (`docs/voice-wake.md`); CI red |

## Branches

Merge order: PR #14 → `phase3/tiers-v2` → `console/hud-theme` → voice → `phase4/trace-dataset` (rebased). This follows the merge rehearsal (`evals/reports/merge-rehearsal.md`), which already resolved the conflicts in that order; it is refreshed before each merge. `console/hud-theme` costs no evals only after tiers-v2 is in `main`; until then it carries tiers-v2's code. `docs/merge-plan` merges whenever (no evals). Nothing is deleted yet: the last column only proposes.

| Branch | Phase | State | Next action |
|---|---|---|---|
| `phase3/results-analysis` | 3 | PR #14 into `phase3/tiers-v2`, 1 commit, CI green, evals not run | merge into tiers-v2 (no evals) |
| `phase3/tiers-v2` | 3 | safety gate PASS; rule A part 1 not met as written; rule C running, consequences frozen (`a29c5f3`, CI green) | verdict report; then merge to `main` per §5 and the owner's gate call (~56k) |
| `phase4/trace-dataset` | 4 | built on v0.2.0, before #9/#11; 8 conflicting files (`evals/reports/merge-rehearsal.md`) | last: rebase onto `main` after voice; fix #42 and the main-meta test; then merge (~56k) |
| `voice/wake-word` + `voice/talk` | 5 | stacked on tiers-v2 `75ce54c`; CI red (mypy imports in `wake/transcribe.py`, run `40406346377`) | fix CI; rebase onto `main` after console; one PR (~56k); keeps decisions 19–20 only if it merges first (numbers are assigned at merge) |
| `console/hud-theme` | console | stacked on tiers-v2 `75ce54c`; console only | rebase onto `main` after tiers-v2; merge (no evals) |
| `labels/judge-calibration` | 2 | 3 owner labels | merged to `main` via PR (2026-10-26) |
| `docs/merge-plan` | – | merge-rehearsal report (stale on memory-rag and the phase4 tip) | merge whenever (no evals) |
| `docs/status-2026-10-26` | – | this file, `progress.md`, CLAUDE.md rewrite | merged to `main` as PR #18 (2026-10-26) |
| `docs/approvals-2026-10-26` | – | Groq approvals log | merged to `main` as PR #20 (2026-10-27) |
| `docs/status-2026-10-27` | – | this update | merge to `main` (no evals) |
| `phase3/memory-rag` | 3 | memory + hybrid RAG design, parked (option B) | park until after the Phase 4 measurements |
| `phase3/memory` | 3 | memory backend + guidance; smoke 2 on Ollama | park; rebase after Phase 4 |
| `phase3/search-spike` | 3 | search design (in `phase3/memory`) | park; then archive as tag, delete |
| `phase3/memory-evals` | 3 | memory split m1.0 (in `phase3/memory`) | park; then archive as tag, delete |
| `phase3/memory-safety` | 3 | tip = tag `safety-before`; its content is in tiers-v2 | proposed: delete (tag exists) |
| `phase4/trace-dataset-v0` | 4 | first distill attempt, replaced | proposed: archive as tag, delete |
| `dev`, `phase2/kestrelbench`, `master` | 1–2 | fully merged into `main` | proposed: delete |
| `main` | – | = `origin/main` `0960cf8` (before this PR) | – |

Dependabot PRs #15–17 are open (checks green apart from cancelled duplicates); they cost no evals.

## Groq

- **Quota:** 200k tokens per rolling 24 h per organisation (usage frees ~24 h after it was spent); 1,000 requests/day never bind. Cached input doesn't count.
- **One consumer at a time.** No CI-triggering PR while a measurement runs.
- **From 2026-10-26, a run starts only after its approval line is added to the log below** (through a docs PR to `main`).
- **Each agent-affecting PR costs ~56k:** the PR run (~28k) plus the merge-to-main run (~28k). Every new push to an open PR runs it again.
- **Calendar:**
  1. Live run (until ~10-28).
  2. Merges, one per quota day: #14 and tiers-v2 (day 1), console (no evals), voice (day 2), phase4 rebased (day 3). The no-eval branches go in between.
  3. Teacher data: dry-run estimate first (`distill/README.md` says ~253k, 2 days); needs the owner's OK and the teacher decision (#36).
  4. Kaggle QLoRA training (no Groq; under 1 GPU hour).
  5. Student evals: the student on Ollama locally, the judge only on Groq.

### Owner approvals (Groq quota)

Billable tokens from each results file's summary (measured). Files are in `..\kestrel-runs\evals\results\`.

| Date | Run | Billable | Approval |
|---|---|---|---|
| 2026-10-06 | PR #11 labelled CI subset (run 37504156469) | 27.7k | owner decision D4 |
| 2026-10-07 | CI subset ×3 (`ci-repeat3-2026-10-07.json`) | 66.6k | approved by the owner at the time, recorded 10-26 |
| 2026-10-11 | memory split before, ×3 (`20261011-092134-memory-groq.json`) | 87.3k | approved by the owner, recorded 10-26 |
| 2026-10-15–17 | safety before side: smoke, strict ×3, rubber-stamp (`safety-before-*.json`) | 18.7k + 210.1k + 58.4k | approved by the owner 2026-10-15 (tiers-v2 CLAUDE.md) |
| 2026-10-17–19 | safety after side, tiers v2: strict ×3, rubber-stamp (`safety-after-*.json`) | 223.5k + 36.3k | approved by the owner at the time, recorded 10-26 |
| 2026-10-20–21 | main suite after, tiers v2 (`main-after-v1.1.json`) | 174.1k | approved by the owner at the time, recorded 10-26 |
| 2026-10-21–23 | safety after side, tiers v2.1: strict ×3, rubber-stamp (`safety-after-v21-*.json`) | 230.2k + 68.4k | approved by the owner at the time, recorded 10-26 |
| from 2026-10-26 | main suite after, tiers v2.1, ×3, live (`main-after-v21-x3.json`) | 365.2k so far (198/300) | approved by the owner at the time, recorded 10-26 |

## Owner decisions open

1. **Phase 3 tiers gate**, after the rule C verdict (what each outcome leads to: §5, frozen). The gate requires no drop in the benign pass rate (decision A, `docs/tiers-v2-design.md` §5), and rule A part 1 is not met as written (`evals/reports/rule-a-power.md` on tiers-v2 reads it as noise). It includes **`ben-note-from-mcp`**: it still fails 1 of 3 strict runs on v2.1 (`docs__lookup` ran, `create_note` didn't), and the reports give rule A with and without it. Ship or not.
2. **Memory before or after teacher data:** decided 2026-10-26 (option B: Phase 4 first). It is recorded only on unmerged branches (`phase4/trace-dataset` CLAUDE.md, `docs/memory-rag-design.md`); it lands with them.
3. **Park the v1.2 suite items until after Phase 4** (D1 says "after the Phase 3 gate"): `sandbox-parent-dir` leak check, `act-email-priya-actions` ambiguity, hard split.
4. **CI threshold:** the proposal is to keep 75% (`evals/reports/ci-threshold.md`, on the tiers-v2 line).
5. **Teacher decision (#36, Groq §6.3(e))**: on 10-31 or after the phase4 rebase, whichever is later; send the follow-up first (log item 3). Publishing the weights is decided then.
6. Also waiting: labels for #21; `docs/phase4-plan.md` open question 13 (power of the quality claim); tiers-v2 §6 questions 4–8.

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

Backup: `C:\kestrel-backup\2026-10-26\` (116 files, 172.5 MB, `MANIFEST.txt` with SHA-256 per file). **Off-laptop copy: not yet.** The backup is on the same disk, so copy it to a USB drive or private cloud storage. `.env` (both folders) is never backed up here.
