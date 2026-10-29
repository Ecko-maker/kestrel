# Kestrel status

Changed only through docs PRs to `main`; feature branches never edit this file. Full history: [progress.md](progress.md).

## Updated

2026-10-29 evening EDT, from the 10-29 ship session (tiers v2.1 merged to `main`, log below).

## Now

**Tiers v2.1 is shipped:** PR #27 merged `phase3/tiers-v2` into `main` with a merge commit, `02c1d2e` (2026-10-29 17:02 EDT). Phase 3's permission tiers are done (design decision 17: safety 0/29, rule C gate not met (a), rule A part 1 an owner override, not a pass). Memory stays parked until after Phase 4. Nothing is running.

**The single next step:** rebase `console/hud-theme` onto `main` and merge it (console only, no evals). Then voice (one PR, ~56k, renumber its 37/43–45 to 54–57), then `phase4/trace-dataset` rebased, which carries the fix for known issue #53 (`fix/quota-retry-spend`, owner decision 2026-10-29).

## 2026-10-29 (ship session)

1. **Preconditions** for the tiers-v2 PR held: approval line on `main`, after 15:45 EDT (16:13), Groq idle (no bench process, no run in `..\kestrel-runs`, the open Dependabot PRs' KestrelBench jobs skipped). No model calls from the session; `..\kestrel-runs` untouched.
2. **`main` merged into `phase3/tiers-v2`** again (`60e7f4f`; `main` had moved by the 10-28 branch-cleanup STATUS, no conflicts). The branch's own STATUS edits from the merge prep (`d5b46d5`) were reverted (`1afd58d`), since feature branches never edit this file; their content is in this update. Local checks: 544 passed, 4 xfailed. Agent fingerprint at the PR head `35fac5ba6277` = the measured agent; `src/` differs from the measured `75ce54c` only by two comments renumbering issues.
3. **Numbers assigned for `main`** (in the merge prep, `d5b46d5`): decisions 15 (safety repeats), 16 (tiers v2), 17 (tiers gate, was `D-pending-tiers-v2-gate`); issues 54 (tool not found, was 37 on the branch), 55 (errored-row fingerprint, was 43), 56 (content check silent, was 44), 57 (taint on run, was 45, closed), 58 (main-suite drift), 59 (draft-and-ask). Console and voice carry the old 37/43–45 and renumber when they rebase.
4. **PR #27 CI** (run `40947683871`): all jobs green; KestrelBench CI subset **93%** (14/15, 95% CI 80–100%) against the 75% gate, 1 errored (`multi-budget-total`, known issue #54: the model called `calculator<|channel|>commentary`), 1 failed (`multi-scale-recipe`), **32,783 billable**.
5. **Merged** by the owner (`02c1d2e`, merge commit). **Push-to-main CI** (run `40954250778`): all jobs green; KestrelBench **87%** (13/15, 95% CI 67–100%), 1 errored (`multi-budget-total`, #54 again), 2 failed (`multi-scale-recipe`, `convo-chained-math`), **26,958 billable**. Both runs: agent `35fac5ba6277`. Total **59,741 billable** against the ~56k estimate.
6. `multi-budget-total` errored the same way (#54) in both runs: worth a look when #54 is fixed.

## 2026-10-28 (gate session)

1. **Phase 3 tiers gate: ship tiers v2.1** (owner decision, `D-pending-tiers-v2-gate`, `ffe502e` on `phase3/tiers-v2`). Rule A part 1 not met and overridden (not a pass); the next safety measurement pre-registers the s1.1 rule from `evals/reports/rule-a-power.md` before its before-side runs. Caveats carried: `KI-pending-main-suite-drift`, 18 benign tasks (±14 points), #44, #23, #21, #53, image policy unrecorded on the main split. New issues: `KI-pending-main-suite-drift`, `KI-pending-draft-and-ask`.
2. **PR #14 merged** into `phase3/tiers-v2` (`caff0f8`, no evals). Checks re-run locally on the merged branch: 544 passed, 4 xfailed.
3. **tiers-v2 → `main` PR opens 10-29 after ~15:45 EDT**, with its approval line (~56k). Dry-run merge (`git merge-tree`): 2 doc conflicts (`CLAUDE.md` ×3, `docs/known-issues.md` ×1); 24 eval-trigger files, so CI evals run.
4. **Known issue #45 (taint on run) closed** on `phase3/tiers-v2` (`635da8e`): the fix is measured, 0 escalations in the rule C after-run.

## 2026-10-28 (verdict session)

1. **After-run finished:** the single resume ran **10-28 15:22:54–15:44:11 EDT** (trace start times in `..\kestrel-runs\logs\bench.db`) and ran all 102 remaining runs. That was earlier than the planned ~18:30, and about 21 h after the 10-27 18:28 stop, not a full 24 h. The approval line (PR #23) merged at 15:12:41 EDT, **10 minutes before the run started**. No 10-29 resume is needed.
2. **Integrity checks** (read-only): 300 rows, 269 pass + 31 fail, 100 tasks × repeats 1–3, every row on provider `groq`; meta: agent `35fac5ba6277`, `groq/openai/gpt-oss-120b`, judge v2, git `75ce54c`; all 129 judge entries are `groq/openai/gpt-oss-120b@v2`. The file keeps one meta block (the last segment's); `--resume` refuses a different agent, provider, model, judge or split version, so the earlier segments match. **Image policy isn't recorded** for the main split (`answer_policy` is null by design: only the safety grader uses it), so the file can't show it. `..\kestrel-runs` still at `75ce54c`, clean.
3. **Verdict** (`b99113c` on `phase3/tiers-v2`): gate not met, branch (a), see Now. The results file is copied to `evals/after-runs/` with its SHA-256 and a pinning test (same as `evals/baselines/`).
4. **`KI-pending-quota-retry-spend` → known issue #53** (`docs/known-issues.md`; #52 was already taken).

## 2026-10-28

1. **PR #22 merged** (`2b643c6`): the 10-27 voice STATUS update.
2. **After-run stopped** by the owner 10-27 18:28 EDT at 198/300, nothing lost (177 pass, 21 fail, 0 errored, 102 not yet run). Resume plan in Now; approval in the Groq log.
3. **`KI-pending-quota-retry-spend`** (not yet in `docs/known-issues.md`): the `--wait-for-quota` retry loop spends tokens on attempts Groq then drops, and it ignores `--token-budget`; the spent tokens keep the rolling window full, so the run livelocks. The 10-26–27 livelock spent ~225k in dropped attempts. Fix after the verdict on a feature branch: a budget check inside the retry loop, a longer back-off after an attempt that spent tokens, and logging Groq's TPD/RPD error text. **Operating rule until fixed:** one resume per quiet 24 h window, never a loop.

## 2026-10-27

In order; detail is in the linked files (all on `phase3/tiers-v2` unless marked).
1. **Approvals log** merged to `main` (PR #20, `0960cf8`): the Groq table below.
2. **Stale `main` hash** fixed here: `main` = `0960cf8`, not `880d601`.
3. **#36 (teacher terms, Groq §6.3(e)):** a follow-up to the 10-18 question is drafted, **not sent yet** (the owner sends it). Decision date: 10-31 or after the phase4 rebase, whichever is later. Already decided: no public hosting of the student. Publishing the weights (LoRA adapter): decided later, at that date.
4. **Rule C verdict script** (`c937cad`): `scripts/rule_c_verdict.py` + tests; report template `evals/reports/rule-c-verdict.md`. A partial dry run was a plumbing check only, not a verdict.
5. **Rule C outcomes recorded** (`0d3c3c0`), then **amended** before the result (`2df47f8`, `130134d`): PASS → merge after #14; FAIL → no merge, fix, re-measure; gate not met → (a) 0 `escalated_by` entries: merge after #14, report "non-inferiority not shown at 6 points", open `KI-pending-main-suite-drift`; (b) 1 or more: review each escalated task, no merge if a card plausibly changed an outcome. Rule and margin unchanged. Superseded options, the reasons, and the disclosure of the partial data seen are in §5.
6. **Fingerprint diff** (`558035a`): `cc5c16377662` → `35fac5ba6277` changes only the tiers-v2 cards (capabilities + approval rules); `evals/reports/fingerprint-diff-v1.1-to-v2.1.md`.
7. **Freeze** (`a29c5f3`): the 2df47f8 reason corrected (A was the owner's choice; it was left because of the fingerprint diff); no further changes to the rule C consequences before the final result.
8. **Voice CI green** (voice branches, no PR, no model calls): `voice/wake-word` `88bb9d1` (run `40605087925`); `voice/talk` `5e36551` (run `40605628065`, after one Windows rerun). The `ci.yml` Python job now installs `--extra wake`, so mypy sees `pywhispercpp` and `numpy` (both ship types; no override). wake-word's own failure was a test catching a real bug: `c_long`/`c_ulong` are 8 bytes on 64-bit Linux, so the Windows `INPUT` struct was 56 bytes, not 40; fixed with fixed-width types.
9. **`voice/talk` rebased** onto wake-word; the old tip is kept as tag `archive/voice-talk-4155004`. The merge rehearsal's voice commit hashes are stale.
10. **`KI-pending-wake-window-start`** (voice, not yet in `docs/known-issues.md`): `on_wake` counts the Listening window from before the trace write and two Windows calls, so listening gets less than the configured time (the cause of the flaky `test_level_events_carry_one_number_and_only_while_listening` on Windows CI). Fix on `voice/talk` later, measured with the offline voice evals before and after.
11. **Cleanup later:** the `ddgs` entry in the mypy `ignore_missing_imports` override is no longer needed (`ddgs` ships `py.typed`).

## Phases

| Phase | State | Proof |
|---|---|---|
| 1 Foundation | done | tag `v0.1.0`; `main` |
| 2 Evals | done, except judge calibration (#21: 3 labels so far) | README baseline 92%; `evals/baselines/` on `main`; labels on `labels/judge-calibration` |
| 3 Memory and safety | permission tiers **done** (v2.1 shipped 2026-10-29, decision 17); memory parked until after Phase 4 | `main` `02c1d2e` (`evals/reports/safety-after-v2.1.md`, `evals/reports/rule-c-verdict.md`); `phase3/memory-rag` (`docs/memory-rag-design.md`, owner decision 2026-10-26) |
| 4 Distillation | in progress: tooling done, GPU smoke passed 2026-10-20, no teacher data collected | `phase4/trace-dataset` (`docs/phase4-plan.md`, `distill/*.md`) |
| 5 Voice and launch | in progress, started early: wake word and speech in/out; demo video and posts not started | `voice/talk` (`docs/voice-wake.md`); CI green |

## Branches

Merge order: ~~PR #14 → `phase3/tiers-v2`~~ (done, `02c1d2e`) → `console/hud-theme` → voice → `phase4/trace-dataset` (rebased, with `fix/quota-retry-spend`). This follows the merge rehearsal (`evals/reports/merge-rehearsal.md`), which already resolved the conflicts in that order; it is refreshed before each merge. tiers-v2 is now in `main`, so `console/hud-theme` costs no evals once rebased. `docs/merge-plan` merges whenever (no evals). Nothing is deleted yet: the last column only proposes.

| Branch | Phase | State | Next action |
|---|---|---|---|
| `phase3/results-analysis` | 3 | in `main` via tiers-v2 (PR #14 → `caff0f8`) | proposed: delete (no tag needed) |
| `phase3/tiers-v2` | 3 | **merged into `main`** (PR #27, `02c1d2e`, 2026-10-29) | proposed: delete |
| `phase4/trace-dataset` | 4 | built on v0.2.0, before #9/#11; 8 conflicting files (`evals/reports/merge-rehearsal.md`) | last: rebase onto `main` after voice; fix #42 and the main-meta test; then merge (~56k) |
| `voice/wake-word` + `voice/talk` | 5 | stacked on tiers-v2 `75ce54c`; CI green (wake-word `88bb9d1`, talk `8a4e22b`; old talk tip = tag `archive/voice-talk-4155004`); `KI-pending-wake-window-start` fixed and closed on talk (`8a4e22b`, `evals/reports/wake-window-start.md`) | rebase onto `main` after console (renumber its 37/43–45 to 54–57 then); one PR (~56k); keeps decisions 19–20 only if it merges first (numbers are assigned at merge) |
| `console/hud-theme` | console | stacked on tiers-v2 `75ce54c`; console only | rebase onto `main` after tiers-v2; merge (no evals) |
| `docs/merge-plan` | – | merge-rehearsal report (stale on memory-rag, the phase4 tip and the voice hashes after the 10-27 rebase) | merge whenever (no evals) |
| `phase3/memory-rag` | 3 | memory + hybrid RAG design, parked (option B) | park until after the Phase 4 measurements |
| `phase3/memory` | 3 | memory backend + guidance; smoke 2 on Ollama | park; rebase after Phase 4 |
| `phase3/search-spike` | 3 | search design (in `phase3/memory`) | park; then archive as tag, delete |
| `phase3/memory-evals` | 3 | memory split m1.0 (in `phase3/memory`) | park; then archive as tag, delete |
| `fix/quota-retry-spend` | 3 | from tiers-v2 `d5b46d5`; known issue #53 fixed in code (`ee496a0`): budget checked inside the retry loop, back-off covers spent tokens, Groq's limit text in the `[quota]` line; agent fingerprint unchanged (`35fac5ba6277`); CI green | rides with the phase4 PR (owner decision 2026-10-29; not in PR #27) |
| `docs/status-2026-10-29-shipped` | – | this update | merge to `main` (no evals) |
| `main` | – | = `origin/main` `02c1d2e` (before this PR) | – |

**Deleted 2026-10-28** (owner OK; local and `origin`): `master`, `dev`, `phase2/kestrelbench` (merged into `main`); `docs/status-2026-10-27`, `-27-voice`, `-28`, `-28-verdict`, `-28-gate` (merged); `docs/status-2026-10-26`, `docs/approvals-2026-10-26`, `labels/judge-calibration` (squash-merged as PRs #18, #20, #19; GitHub keeps their tips under the PRs); `phase3/memory-safety` (tip kept by tag `safety-before`, `6f58ee5`); `phase4/trace-dataset-v0` (tip kept by the new tag `archive/phase4-trace-dataset-v0-289274e`). Kept for now: `phase3/results-analysis` (in tiers-v2 via #14; delete without a tag once tiers-v2 is in `main`), `docs/merge-plan`, the Dependabot branches (open PRs).

Dependabot PRs #15–17 are open (checks green apart from cancelled duplicates); they cost no evals.

## Groq

- **Quota:** 200k tokens per rolling 24 h per organisation (usage frees ~24 h after it was spent); 1,000 requests/day never bind. Cached input doesn't count.
- **One consumer at a time.** No CI-triggering PR while a measurement runs.
- **From 2026-10-26, a run starts only after its approval line is added to the log below** (through a docs PR to `main`).
- **Each agent-affecting PR costs ~56k:** the PR run (~28k) plus the merge-to-main run (~28k). Every new push to an open PR runs it again.
- **Calendar:**
  1. ~~After-run resumes~~: done in one resume 10-28 15:22–15:44 EDT; verdict 10-28.
  2. Merges, one per quota day: #14 (done 10-28, no evals) and tiers-v2 (done 10-29, 59.7k), console (no evals), voice (day 2), phase4 rebased (day 3). The no-eval branches go in between.
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
| 2026-10-26–27 | main suite after, tiers v2.1, ×3 (`main-after-v21-x3.json`) | 365.2k at 198/300 | approved by the owner at the time, recorded 10-26 |
| 2026-10-28 and 10-29 | resume of the main-suite after-run (same measurement, single command per quiet 24 h window, `--token-budget 180000`) | ~210k (estimate) | approved by the owner. The 10-26–27 livelock spent ~225k in dropped attempts; stopped by the owner 10-27 18:28 at 198/300, nothing lost. **Ran 10-28 15:22:54–15:44:11 EDT**, one resume, 102 runs, finished 300/300. Approval line merged (PR #23) at 15:12:41 EDT, 10 min before the start; the start was ~3 h earlier than the planned ~18:30 and ~21 h after the stop. ~176.3k billable (file total 541,468 minus 365.2k at 198/300), under the 180k budget. The 10-29 resume isn't needed. |
| 2026-10-29 | tiers-v2 → main PR (one CI run on the PR + one on the merge to main) | ~56k (estimate); **59,741 measured** (PR run 32,783 + main run 26,958) | approved by the owner. Conflicts are resolved on the branch before the PR opens; extra pushes to the open PR need a new approval line. **Ran:** PR #27 (run `40947683871`, 10-29 ~16:20 EDT) and the merge to `main` (run `40954250778`, ~17:02 EDT); no extra pushes. |

## Owner decisions open

1. ~~**Phase 3 tiers gate**~~: decided 2026-10-28, ship tiers v2.1 (owner override on rule A part 1); in `main` as design decision 17 (PR #27).
2. **Memory before or after teacher data:** decided 2026-10-26 (option B: Phase 4 first). It is recorded only on unmerged branches (`phase4/trace-dataset` CLAUDE.md, `docs/memory-rag-design.md`); it lands with them.
3. **Park the v1.2 suite items until after Phase 4** (D1 says "after the Phase 3 gate"): `sandbox-parent-dir` leak check, `act-email-priya-actions` ambiguity, hard split.
4. **CI threshold:** the proposal is to keep 75% (`evals/reports/ci-threshold.md`, on the tiers-v2 line).
5. **Teacher decision (#36, Groq §6.3(e))**: on 10-31 or after the phase4 rebase, whichever is later; send the follow-up first (log item 3). Publishing the weights is decided then.
6. Also waiting: labels for #21; `docs/phase4-plan.md` open question 13 (power of the quality claim); tiers-v2 §6 questions 4–8.

## Docs on unmerged branches

A new decision or issue keeps a slug until its branch merges; `main` assigns the number then. The numbers the branches use today:

| Pending name | Branch(es) | Number used there |
|---|---|---|
| D-pending-safety-repeats | in `main` (PR #27); also memory-* | **final 15** (same text everywhere) |
| D-pending-tiers-v2 | in `main` (PR #27); also memory-rag, console, voice, v0 | **final 16** |
| D-pending-tiers-v2-gate | in `main` (PR #27) | **final 17** |
| D-pending-memory-split | memory-evals, search-spike, memory | **16** |
| D-pending-memory-search | memory | **17** |
| D-pending-hud-console | console, voice | **17** |
| D-pending-distill-dataset-v0 | trace-dataset-v0 | **17** |
| D-pending-memory-guidance | memory | **18** |
| D-pending-voice-wake | wake-word, talk | **18** |
| D-pending-talk-whisper, D-pending-send-countdown | talk | **19, 20** |
| D-pending-distill-data, -distill-taskset, -distill-overlap, -distill-rubric-checks, -student-qlora, -prompt-format, -router-v1 | phase4 | **19, 20**, 21–25 |
| KI-pending-tool-not-found, -errored-row-fingerprint, -content-check-silent, -taint-on-run | in `main` (PR #27); console and voice still use 37, 43–45 | **final 54, 55, 56, 57** (57 closed) |
| KI-pending-console-taint | console, voice | 46 |
| KI-pending-wake-owner-machine, -self-interruption, -vocab-hint (closed), -cancel-unmeasured | voice | 47–50 |
| KI-pending-wake-window-start | voice (not yet written on the branch) | – |
| ~~KI-pending-quota-retry-spend~~ | numbered on `main` as #53; fixed on `fix/quota-retry-spend` (rides with phase4) | 53 |
| KI-pending-main-suite-drift, -draft-and-ask | in `main` (PR #27) | **final 58, 59** |
| KI-pending-memory-isolation, -memory-attack-surface, -memory-split-limits | memory-evals, search-spike, memory | **25, 26**, 27 |
| KI-pending-memory-search-limits, -estimate-from-cache | memory | 28, 29 |
| KI-pending-safety-prompts-pinned … -router-tiers-v2 (12 issues) | phase4 | 30–36, 38–42 |
| KI-pending-student-no-memory | phase4 | 51 |
| KI-pending-training-on-bench, -secret-scrub-v0 | trace-dataset-v0 | **25, 26** |

Clashes (bold): decisions **16, 17, 18, 19, 20**; issues **25, 26**. Phase4 skips decisions 13–18 (it predates #9/#11). The merge rehearsal's "next free issue is 51" is wrong: phase4 uses 51. `main` now holds decisions 15–17 and issues 52–59 (with tiers-v2), so every other branch's 16 and 17 must move, and **the next free numbers on `main` are decision 18 and issue 60**.

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
