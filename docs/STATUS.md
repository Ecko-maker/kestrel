# Kestrel status

Changed only through docs PRs to `main`; feature branches never edit this file. Full history: [progress.md](progress.md).

## Updated

2026-11-02, from the 11-02 export-readiness session (log below).

## Now

**Tiers v2.1, the console, voice and the Phase 4 tooling are all in `main`:** tiers v2.1 (PR #27, `02c1d2e`), `console/hud-theme` (PR #29, `7664103`, no evals), voice (PR #31, `d746e9c`, 2026-10-29 21:22 EDT) `phase4/trace-dataset` (PR #37, `4290fc6`, 2026-10-30 17:45 EDT) and `phase4/kaggle-teacher` + `phase4/student-eval` (PR #50, `43d5522`, 2026-11-02 19:55 UTC). The same-day drift control for known issue #58 ran (gross drift not shown; #58 stays open). Memory stays parked until after Phase 4. Nothing is running.

**The single next step:** the owner's call on the router gap (KI-pending-router-text-tool-call: a reply whose tool call stays in the text isn't escalated), then the `phase4/export-readiness` PR (it now changes `src/kestrel/distill/train_format.py`, so it runs the CI subset on Groq: ~56k with the merge-to-main run, an approval line), then `distill/EVAL.md`'s free runs F1-F3 in `..\kestrel-runs`.

## 2026-11-02 (export-readiness session; KestrelBench not run, no Groq)

1. **11-02: QLoRA training started on Kaggle (code a4b3372, data e39dd661), private datasets.** The first attempt stopped in `trainer.train()`: "GPU T4 x2" left two GPUs visible and TRL's SFTTrainer refused `nn.DataParallel`. The owner restarted it with one extra first notebook cell, `CUDA_VISIBLE_DEVICES=0`; code dataset `a4b3372` unchanged, so `lineage.json`'s `code_commit` stays `a4b3372`. Same fix in git on `phase4/export-readiness` (`277fd67`): the notebook cell, `train.py` refuses more than one visible GPU, GPU count recorded. The 10-20 GPU smoke test missed it because it uses its own loop, not the Trainer; all pins matched (KI-pending-gpu-smoke-own-loop).
   The restart then **stopped at step 0/45**: `NotImplementedError: "_amp_foreach_non_finite_check_and_unscale_cuda" not implemented for 'BFloat16'` (`clip_grad_norm_` -> `GradScaler.unscale_`). Cause: trl 1.16.0's `SFTTrainer.__init__` casts a 4-bit model's LoRA weights to bfloat16 whatever `fp16` says; a T4 has no bfloat16. Fixed in `5b175f1` (`phase4/export-readiness`, pushed, no PR): `make_trainer()` casts them back to float32 and refuses to train otherwise, and `--gpu-smoke` now takes one real SFTTrainer step (fp16, clipping), so it would have caught both failures. `train_config.toml` and the data unchanged. New code zip: `C:\Users\noahi\Documents\kestrel-kaggle-upload\kestrel-train-code-5b175f17addf.zip` (sha256 `c012d2f2…`).
2. **Export readiness** (`phase4/export-readiness` `e1306dc`, off `phase4/student-eval`, pushed, no PR, CI green): nothing had to be installed (llama.cpp `b11712`, the parity renderer, the `train` + `export` extras, Ollama 0.40.13). 15.3 GB RAM (the merge needs ~9 GB free), 683 GB free disk; the ~8 GB base model is not downloaded yet. The smoke adapter ran the whole chain on Ollama 0.40.13: tool schemas identical, 20/20 prompt token counts for the student, `--gguf` and the base, template parity 20/20 (golden and renderer); test models deleted. New `scripts/check_lineage.py` (KAGGLE.md step 10 as a check, 9 tests); the commands for the adapter's arrival are in `distill/EXPORT.md`.
3. **Free student runs written** in `distill/EVAL.md` ("Free runs first", local Ollama, no judge, no Groq): F1 main smoke `--judge none --sample 10` (10 runs), F2 safety strict x3 (144), F3 safety rubber-stamp attacks (30); dry runs match. Waiting for the owner: README #46 (green, KestrelBench not run), Dependabot #38, #41 (green), #40 (green; no `run-evals` label run, the owner's call).
4. **PR #50 merged** (`phase4/kaggle-teacher` + `phase4/student-eval` → `main`, `43d5522`, 2026-11-02 19:55 UTC; the owner's merge). CI subset on Groq, both runs 81% (13/16, gate 75% passed), 0 errored: PR run 41471771520 **28,598 billable**, merge-to-main run 41514497831 **30,710 billable**; **59,308 in all, within the 70k approval**. Pending names numbered in docs PR #52: decision 30 (Phase 4 cost report), decision 31 (routed safety groups), known issue #79 (routed safety pre-route); the next free on `main` are then **decision 32 and issue 80** (`phase4/export-readiness` brings `KI-pending-gpu-smoke-own-loop`).
5. **11-02: the student is trained and served (KestrelBench not run, no Groq).** Kaggle run on code `5b175f1` + data `e39dd661`, one T4, 44 min: 5/5 epochs, 45 steps, no early stop, val loss 0.660 → 0.409 (best = epoch 5); `check_lineage.py` all OK. Base model download 7.51 GB (`~\.cache\huggingface\hub`). With 2-3.6 GB of RAM free, exported with the new `export.py --low-memory` (tensor-by-tensor merge, bit-identical to peft's on the smoke model): GGUF Q4_K_M sha256 `53052ecf…` → `kestrel-student`; tool schemas identical, 20/20 token counts and template parity. A 3-task local sanity run first failed both tool tasks: Ollama took its tool-call tag from Kestrel's template as `{` and returned the student's well-formed `<tool_call>` blocks as text (KI-pending-ollama-tool-tag). Template fixed with identical prompt bytes, tag now checked in `golden.json`; re-created from the same GGUF: 3/3, no tool-call text or template tokens in answers. `phase4/export-readiness` `4d59e5f`, pushed, no PR. Backup `C:\kestrel-backup\2026-11-02\student\` (2.59 GB, MANIFEST-student.txt with SHA-256).

## 2026-11-02 (training-set session; KestrelBench not run, no Groq)

1. **Option a collection done** (11-01 and 11-02, runs folder at `5b2a242`, agent `35fac5ba6277`, finished 11-02 14:33 UTC): 122/122 distill tasks graded once each, **104 passed**, 18 failed, 0 errored, 0 skipped; groq + openai/gpt-oss-120b only; **206,378 billable**, within the 320k approval. The rate-limit audit flagged 104 graded tasks, all for retried calls that then answered (and `max_steps` on 3 failed tasks); no call gave up, no tool timed out, no trace was missing.
2. **Training set built** at `5b2a242` (clean), on a copy of `distill.db`, in `data\distill\d1.2-groq-gpt-oss-120b`: 131 traces → **89 kept, 71 train / 18 val** (82 tasks: 65 / 17), split by task (one session per task). Dropped: `quality` 20 (18 failed tasks + 2 traces no result names), `rubric_task` 22; teacher, Harmony leak, KestrelBench overlap (`contamination_*`, `near_duplicate`) and secrets all 0. Plan: 85-95 examples; option c's evidence-only pass: 82. `--val-frac` 0.2 gave 8 val examples; 0.3 was chosen from the session hashes alone, before any training.
3. **Pinned on `phase4/student-eval`** (`0b97f30`, pushed, no PR): `distill/collection-a/` (collection manifest without answers or traces, build manifest byte for byte, SHA-256 tested; the test rebuilds 71 / 18 from the pinned sessions). Backup `C:\kestrel-backup\2026-11-02\` (6 files, 2.2 MB: results, `distill.db`, the dataset; `MANIFEST.txt`).
4. **Judge labels:** 32 owner labels merged (PR #47, `fa06b03`): 35 in total, dev 20 / held-out 15. Waiting for the owner: README PR #46, Dependabot #38 (fixed on its branch: transformers kept at 5.20.1, the Kaggle-verified pin), #41, #40 (no `run-evals` label run yet).

## 2026-11-01 (teacher-decision session; KestrelBench not run, no Groq)

1. **#72 decided and closed: option a, `groq` + `openai/gpt-oss-120b` (design decision 29, on `phase4/kaggle-teacher` `5b2a242`).** Groq Support confirmed the use in writing in a support chat on 2026-10-31, before the 11-04 deadline (summarized in #72; the transcript stays with the owner). Groq's AI Usage Policy has nothing that conflicts. Scope: personal project, no hosted service, student weights not published. Correction: the 10-30 contact was a support chat, not an email follow-up. `teachers.toml` lists only Groq's entry; agent fingerprint `35fac5ba6277` unchanged; checks green (1004 passed).
2. **Option c pass 1 is evidence only, never training data** (one teacher per dataset, design decision 22). Kaggle collect run 11-01 00:55-01:07 UTC at `5cce69c` (Qwen3-30B-A3B): 122/122 tasks, 93 passed, 0 errored; built 82 examples (74 train / 8 val). Manifests pinned in `distill/collection/` (`826e690`), findings in `distill/COLLECTION.md`. Section 8 unchanged; the option c amendment is not applied.
3. **#59 (draft-and-ask): collect now, fix later.** Traces that draft and ask fail their checks and are dropped; the later prompt fix must re-measure the routed system too, or retrain. Backup of the Kaggle outputs: `C:\kestrel-backup\2026-10-31\` (32 files, SHA-256 manifest).

## 2026-10-31 (option c selection session; KestrelBench not run, no Groq)

1. **#72's option c selection ran on Kaggle** (2×T4, `collect.ipynb` compare mode, commit `5ec57a7`, 20:17-20:29 UTC; version 1 had stopped at the Ollama install, missing `zstd`, fixed in `5ec57a7`). Both candidates ran exactly the 20 pre-registered tasks: check-pass 15/20 each, leaks 0/0, valid tool calls 28/28 (Qwen) vs 24/27 (gpt-oss:20b), 2.5 vs 6.6 s/task, both fit. **Winner by the rule: Qwen3-30B-A3B-Instruct-2507 Q4**; an independent recomputation matched.
2. **On `phase4/kaggle-teacher` (`5cce69c`, pushed, no PR):** evidence pinned in `distill/selection/` (SHA-256, tested); result in #72; `teachers.toml` has Qwen active as option c (used only if Groq hasn't confirmed in writing by 2026-11-04; option a kept, commented out); the section 8 amendment draft is finished for Qwen, not applied.
3. **Data outside git:** the compare output (results files, `distill.db` per candidate) in `..\kestrel-runs\evals\results\teacher-selection-5ec57a709a43\`; not backed up yet.

## 2026-10-30 (teacher-deferral docs session)

1. **Teacher decision (#72) deferred by the owner** (no decision number yet). No reply from Groq to the 10-18 email or the 10-30 follow-up. The owner will not train on Groq outputs without Groq's written confirmation.
2. **Deadline 2026-11-04:** Groq confirms in writing by then → option a (its own approval line); otherwise option c, a Kaggle-hosted teacher chosen by the selection pre-registered in #72 (gpt-oss:20b vs Qwen3-30B-A3B-Instruct-2507 at Q4, 20 fixed check-graded distill tasks, seed 2026, `--judge none`, check-pass rate first, 3 min/task limit). Option c is built and tested on Kaggle meanwhile (no Groq).
3. **If option c is used,** the Groq days freed by not collecting on Groq may go to the draft-and-ask fix (#59) instead, with its own approval line.
4. **Groq replied later the same day** (summarized in #72): they understand the use case (personal research and portfolio, a locally run fine-tuned model, not a hosted service) and are reviewing §6.3(e) with an internal team before confirming. **No permission yet**; the 2026-11-04 rule stands. The email stays in the owner's inbox as evidence.

## 2026-10-30 (phase4 merge session)

1. **Before the PR, on `phase4/trace-dataset`** (no Groq): the Harmony-leak filter in `distill build` (#54); `distill/COLLECTION.md` as a checklist for either teacher; the routed path checked end to end on local Ollama only; the **Phase 4 pre-registration** (`docs/phase4-plan.md` section 8, frozen 2026-10-30, before any teacher data), its power script `scripts/phase4_power.py` (reproduced exactly by a test) and its statistic in `kestrel bench compare --metric checks`. Agent fingerprint unchanged (`35fac5ba6277`).
2. **Quota check before opening** (17:25 EDT): 200,000 − 103,956 used in the last 24 h (PR #31 run, voice merge run, drift control) − 15,000 = 81,044 ≥ 70,000; no other Groq consumer.
3. **PR #37** (run `41131505114`, 17:30 EDT): all jobs green; KestrelBench CI subset **88%** (14/16, 95% CI 69–100%), 0 errored, failed `act-email-sam`, `multi-scale-recipe`; **30,911 billable**.
4. **Merged** by the owner as `4290fc6` (merge commit, 17:45 EDT). Its push-to-main run `41133429864` was **cancelled** at 17:48, before KestrelBench started (0 tokens), because Dependabot #17 and #15 were merged at 17:47 and CI cancels older `main` runs; the `d3fcfb7` run that replaced it was green but "KestrelBench not run" (its diff held only #15). With the owner's OK the cancelled run was **re-run** (18:20–18:27 EDT, at `4290fc6`): all jobs green; subset **94%** (15/16, 95% CI 81–100%), 0 errored, failed `multi-scale-recipe`; **29,685 billable**.
5. **Total for the phase4 merge: 60,596 billable** (30,911 + 29,685), within the 70k approval; no extra pushes to the open PR.
6. **Dependabot #15 and #17 merged** (owner, 17:47 EDT). #15 changes `ci.yml` (setup-uv 10.2.0 → 10.3.0) without a `run-evals`-labelled run (CLAUDE.md: a change to the gate is proved with the label); the re-run above still used 10.2.0. #33 is still open.

## 2026-10-30 (rebase session)

1. **`phase4/trace-dataset` rebased onto `main` `d47bf09`**, with `fix/quota-retry-spend` brought in; tip **`f0a877b`**, pushed (CI run `41099764579` green, KestrelBench not run). The old tip is kept as tag `archive/phase4-trace-dataset-1f0084f`.
2. **Final numbers:** phase4's decisions are **22–28** and its issues **66–78**. Closed: **#53** (quota retry livelock, fixed by `4554331`, tests pass) and **#77** (router tested under tiers v2, was `#42` on the branch). The teacher-terms issue (Groq §6.3(e)) is now **#72** (was #36 on the branch).
3. **Agent fingerprint unchanged:** `35fac5ba6277` = `main`'s; the router is off unless `KESTREL_PROVIDERS=routed`. The merge rehearsal (`evals/reports/merge-rehearsal.md` on `docs/merge-plan`) is **superseded** by the merges as done.

## 2026-10-29 (voice + control docs session)

1. **Voice merged** (PR #31, `d746e9c`). Its two CI runs: PR run `40970175103` 94% (15/16), 31,289 billable; merge-to-main run `40985473324` 94% (15/16, 95% CI 81–100%), 0 errored, 32,961 billable. Total **64,250**, within the 70k approval.
2. **Drift control ran** (known issue #58): old agent `cc5c16377662` ×2 on the CI subset, runs folder at `c7c0d4f`, ran 21:54–21:58 EDT (trace start times in `..\kestrel-runs\logs\bench.db`; after the voice merge run ended ~21:28, so no overlap); 84% (run 1 88%, run 2 81%), 0 errored, **39,706 billable**, within the 55k approval. Pinned in `evals/controls/` (SHA-256 `0c48e60d…`, `tests/test_baselines_pinned.py`). Result in the pre-registered terms: gross drift not shown; the main suite's −3 points stay unresolved at this size; re-test at Phase 4's main-suite run (`docs/known-issues.md` #58). Afterwards `..\kestrel-runs` was moved to `d746e9c` (clean).
3. **Groq today:** 59,741 (tiers-v2) + 64,250 (voice) + 39,706 (control) = **163,697 billable (≈164k of 200k)**.
4. **Numbering on `main` checked:** no gap. The console PR (#29, commit `8970592`) merged before voice and took decision **18** (HUD look) and issue **60** (console taint, was `KI-pending-console-taint`). Voice then took decisions **19–21** and issues **61–65** (`8a3ee76`). Next free on `main`: **decision 22, issue 66**.

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
3. **#72 (was #36 on the branch; teacher terms, Groq §6.3(e)):** a follow-up to the 10-18 question is drafted, **not sent yet** (the owner sends it). Decision date: 10-31 or after the phase4 rebase, whichever is later. Already decided: no public hosting of the student. Publishing the weights (LoRA adapter): decided later, at that date.
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
| 2 Evals | done, except judge calibration (#21: 35 labels, dev 20 / held-out 15, PR #47) | README baseline 92%; `evals/baselines/` on `main`; labels in `evals/labels/human.jsonl` on `main` |
| 3 Memory and safety | permission tiers **done** (v2.1 shipped 2026-10-29, decision 17); memory parked until after Phase 4 | `main` `02c1d2e` (`evals/reports/safety-after-v2.1.md`, `evals/reports/rule-c-verdict.md`); `phase3/memory-rag` (`docs/memory-rag-design.md`, owner decision 2026-10-26) |
| 4 Distillation | in progress: tooling **in `main`** (PR #37, `4290fc6`, 2026-10-30; student-eval tooling and the option a pins PR #50, `43d5522`, 2026-11-02), claim pre-registered (`docs/phase4-plan.md` section 8); teacher data collected 11-01/02 and the training set built (89: 71 / 18); QLoRA training started on Kaggle 11-02 | `main` (`docs/phase4-plan.md`, `distill/*.md`) |
| 5 Voice and launch | in progress, started early: wake word and speech in/out; demo video and posts not started | `voice/talk` (`docs/voice-wake.md`); CI green |

## Branches

Merge order: ~~PR #14 → `phase3/tiers-v2`~~ (done, `02c1d2e`) → ~~`console/hud-theme`~~ (done, `7664103`) → ~~voice~~ (done, `d746e9c`) → ~~`phase4/trace-dataset`~~ (done, PR #37, `4290fc6`, with `fix/quota-retry-spend`). The merge rehearsal (`evals/reports/merge-rehearsal.md` on `docs/merge-plan`) planned this order; it is **superseded** (2026-10-30): every branch before phase4 is merged and phase4 is rebased onto `main`, so its hashes and conflict lists no longer apply. Nothing is deleted yet: the last column only proposes.

| Branch | Phase | State | Next action |
|---|---|---|---|
| `phase3/results-analysis` | 3 | in `main` via tiers-v2 (PR #14 → `caff0f8`) | proposed: delete (no tag needed) |
| `phase3/tiers-v2` | 3 | **merged into `main`** (PR #27, `02c1d2e`, 2026-10-29) | proposed: delete |
| `phase4/trace-dataset` | 4 | **merged into `main`** (PR #37, `4290fc6`, 2026-10-30; decisions 22–28, issues 66–78, #53 and #77 closed) | keep for now (Phase 4 continues there or on a new branch off `main`) |
| `voice/wake-word` + `voice/talk` | 5 | **merged into `main`** (PR #31, `d746e9c`, 2026-10-29; decisions 19–21, issues 61–65) | proposed: delete (old talk tip kept by tag `archive/voice-talk-4155004`) |
| `console/hud-theme` | console | **merged into `main`** (PR #29, `7664103`, 2026-10-29; decision 18, issue 60) | proposed: delete |
| `docs/merge-plan` | – | merge-rehearsal report, **superseded** 2026-10-30 (all merges before phase4 done, phase4 rebased) | proposed: don't merge; archive as tag, delete |
| `phase3/memory-rag` | 3 | memory + hybrid RAG design, parked (option B) | park until after the Phase 4 measurements |
| `phase3/memory` | 3 | memory backend + guidance; smoke 2 on Ollama | park; rebase after Phase 4 |
| `phase3/search-spike` | 3 | search design (in `phase3/memory`) | park; then archive as tag, delete |
| `phase3/memory-evals` | 3 | memory split m1.0 (in `phase3/memory`) | park; then archive as tag, delete |
| `fix/quota-retry-spend` | 3 | from tiers-v2 `d5b46d5`; known issue #53 fixed in code (`ee496a0`): budget checked inside the retry loop, back-off covers spent tokens, Groq's limit text in the `[quota]` line; agent fingerprint unchanged (`35fac5ba6277`); CI green; **in `main` via phase4** (PR #37, `4290fc6`) | proposed: delete |
| `docs/approval-2026-10-30-phase4` | – | merged (PR #36, `6292bf7`) | proposed: delete |
| `docs/status-2026-10-30-phase4-merged` | – | this update | merge to `main` (KestrelBench not run) |
| `phase4/kaggle-teacher` | 4 | **merged into `main`** with `phase4/student-eval` (PR #50, `43d5522`, 2026-11-02); `5b2a242` = the collection commit | keep (the collection commit) |
| `phase4/student-eval` | 4 | **merged into `main`** (PR #50, `43d5522`, 2026-11-02; decisions 30-31 and issue 79 numbered in PR #52); `a4b3372` = the first training code commit | keep while `phase4/export-readiness` is open |
| `phase4/export-readiness` | 4 | off `phase4/student-eval`: lineage check, export commands, free runs F1-F3, the one-GPU and float32-LoRA fixes, the trained student's export (`--low-memory`) and the Ollama tool-tag fix; `4d59e5f` (code zip from `5b175f1`), no PR | PR after the owner's call on the router gap; it runs the CI subset (changes `src/kestrel/distill/`) |
| `main` | – | = `origin/main` `43d5522` (PR #50) | – |

**Deleted 2026-10-28** (owner OK; local and `origin`): `master`, `dev`, `phase2/kestrelbench` (merged into `main`); `docs/status-2026-10-27`, `-27-voice`, `-28`, `-28-verdict`, `-28-gate` (merged); `docs/status-2026-10-26`, `docs/approvals-2026-10-26`, `labels/judge-calibration` (squash-merged as PRs #18, #20, #19; GitHub keeps their tips under the PRs); `phase3/memory-safety` (tip kept by tag `safety-before`, `6f58ee5`); `phase4/trace-dataset-v0` (tip kept by the new tag `archive/phase4-trace-dataset-v0-289274e`). Kept for now: `phase3/results-analysis` (in tiers-v2 via #14; delete without a tag once tiers-v2 is in `main`), `docs/merge-plan`, the Dependabot branches (open PRs).

Dependabot PRs #15 and #17 merged 2026-10-30 (the 10-30 log, item 6); #33 is open. They cost no evals.

## Groq

- **Quota:** 200k tokens per rolling 24 h per organisation (usage frees ~24 h after it was spent); 1,000 requests/day never bind. Cached input doesn't count.
- **One consumer at a time.** No CI-triggering PR while a measurement runs.
- **From 2026-10-26, a run starts only after its approval line is added to the log below** (through a docs PR to `main`).
- **Each agent-affecting PR costs ~56k:** the PR run (~28k) plus the merge-to-main run (~28k). Every new push to an open PR runs it again.
- **Calendar:**
  1. ~~After-run resumes~~: done in one resume 10-28 15:22–15:44 EDT; verdict 10-28.
  2. Merges, one per quota day: #14 (done 10-28, no evals) and tiers-v2 (done 10-29, 59.7k), console (done 10-29, no evals), voice (done 10-29, 64.3k), phase4 (done 10-30, 60.6k). The no-eval branches go in between.
  3. ~~Teacher data~~: **done** 11-01 and 11-02, option a, 206,378 billable (approval line below).
  4. Kaggle QLoRA training (no Groq; under 1 GPU hour): **started 11-02**.
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
| 2026-10-29 | voice → main PR (`voice/talk`, one CI run on the PR + one on the merge to main) | ~56k (estimate) | approved by the owner, up to 70k billable. Rebased onto `main` `7664103` (after PR #29), agent fingerprint `35fac5ba6277` = main's; extra pushes to the open PR need a new approval line. **PR run:** PR #31 (run `40970175103`, 10-29 19:03–19:06 EDT), **31,289 billable**, 94% (15/16), 0 errors. PR #31 was opened before this line merged (PR #30), so that run started without its approval line on `main`; it stayed within the 70k. The merge-to-main run has up to 38,711 left. **Merge run:** `40985473324` (10-29 ~21:22 EDT), **32,961 billable**, 94% (15/16), 0 errors. Total **64,250** over the 2 runs, within the 70k; no extra pushes. |
| 2026-10-29 | 10-29 evening: same-day drift control (known issue #58), old agent ×2 and new agent ×0 on the CI subset, approved up to **55,000** billable | old ×2: ~44–56k (22.2k per run on 10-07; 28k per run as the planning estimate) | approved by the owner. One command in `..\kestrel-runs` at `c7c0d4f` (agent `cc5c16377662`): `--subset ci --repeat 2 --token-budget 55000`, no `--wait-for-quota`, results `control-old-ci-2026-10-29.json`. Budget: 200,000 − 91,030 used in the last 24 h (CI runs on PR #27, the tiers-v2 merge and PR #31) − 15,000 margin = 93,970; minus 38,711 kept for the voice merge run = 55,259. A new-agent run (~28k) doesn't fit as well: today's three CI runs of agent `35fac5ba6277` are the new side. Starts only after this line is on `main` and while no other Groq consumer runs. **Ran:** finished 10-29 21:58 EDT (line merged as PR #32 at 20:50 EDT), runs folder at `c7c0d4f`, agent `cc5c16377662`; **39,706 billable**, within the 55k; 84%, 0 errored. Pinned in `evals/controls/`; result under known issue #58. |
| 2026-10-30 | phase4 → main PR (`phase4/trace-dataset`, one CI run on the PR + one on the merge to main) | ~56k (estimate) | approved by the owner, up to 70k billable. Rebased onto `main` `d47bf09`, tip `f0a877b`, agent fingerprint `35fac5ba6277` = main's. Opens only after this line is on `main`, while no other Groq consumer runs, and when 200,000 − last-24 h use − 15,000 ≥ 70,000. Extra pushes to the open PR need a new approval line. **Ran:** PR #37 run `41131505114` (10-30 17:30 EDT, quota check 81,044 ≥ 70,000), **30,911 billable**, 88% (14/16), 0 errors; merge-to-main run `41133429864` cancelled by the Dependabot merges before it started (0 tokens), re-run with the owner's OK 18:20 EDT, **29,685 billable**, 94% (15/16), 0 errors. Total **60,596**, within the 70k; no extra pushes. |
| 11-01 and 11-02 | Phase 4 teacher collection, 122 distill tasks on groq gpt-oss-120b (`--split distill --judge none`, runs folder at `phase4/kaggle-teacher` `5b2a242`, agent `35fac5ba6277`) | ~253k (estimate; dry run ~313k) | approved by the owner, up to **320k billable over the two days**. One command per day, with `--token-budget` set so Groq's window keeps a 15k margin; never a loop. Day 1 budget: 200,000 − 0 used in the last 24 h (8 CI runs, all "KestrelBench not run"; no local Groq traces) − 15,000 = 185,000. Starts only after this line is on `main` and while no other Groq consumer runs. **Ran:** started 11-01 12:14 UTC (07:14 EST), finished 11-02 14:33 UTC; 122/122 graded, 104 passed; **206,378 billable measured**, within the 320k approval. |
| 11-02 | Phase 4 branches (`phase4/kaggle-teacher` + `phase4/student-eval`) → `main`, one CI run on the PR + one on the merge to `main` | ~56k (estimate) | approved by the owner, up to **70k billable**. `main` merged into `phase4/student-eval` and pushed before the PR opens; agent fingerprint `35fac5ba6277` = `main`'s. Opens only after this line is on `main`, while no other Groq consumer runs, and when 200,000 − last-24 h use − 15,000 ≥ 70,000. Extra pushes to the open PR need a new approval line. **Ran:** PR #50, PR run 28,598 + merge-to-main run 30,710 = **59,308 billable**, within the 70k approval (both 81%, 13/16, 0 errored). |

## Owner decisions open

1. ~~**Phase 3 tiers gate**~~: decided 2026-10-28, ship tiers v2.1 (owner override on rule A part 1); in `main` as design decision 17 (PR #27).
2. **Memory before or after teacher data:** decided 2026-10-26 (option B: Phase 4 first). In `main` with phase4 (CLAUDE.md, `docs/phase4-plan.md`); `docs/memory-rag-design.md` is still on `phase3/memory-rag`.
3. **Park the v1.2 suite items until after Phase 4** (D1 says "after the Phase 3 gate"): `sandbox-parent-dir` leak check, `act-email-priya-actions` ambiguity, hard split.
4. **CI threshold:** the proposal is to keep 75% (`evals/reports/ci-threshold.md`, on the tiers-v2 line).
5. ~~**Teacher decision (#72, Groq §6.3(e))**~~: decided 2026-11-01, **option a** (Groq's written confirmation, 10-31), design decision 29 on `phase4/kaggle-teacher`. The student weights are not published.
6. Also waiting: labels for #21; tiers-v2 §6 questions 4–8. (`docs/phase4-plan.md` open question 13 is answered by section 8, 2026-10-30.)

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
| D-pending-distill-data, -distill-taskset, -distill-overlap, -distill-rubric-checks, -student-qlora, -prompt-format, -router-v1 | phase4 (rebased 10-30) | **final 22–28** |
| KI-pending-tool-not-found, -errored-row-fingerprint, -content-check-silent, -taint-on-run | in `main` (PR #27); console and voice still use 37, 43–45 | **final 54, 55, 56, 57** (57 closed) |
| KI-pending-console-taint | console, voice | 46 |
| KI-pending-wake-owner-machine, -self-interruption, -vocab-hint (closed), -cancel-unmeasured | voice | 47–50 |
| KI-pending-wake-window-start | voice (not yet written on the branch) | – |
| ~~KI-pending-quota-retry-spend~~ | numbered on `main` as #53; fix brought into phase4, closed there 10-30 | 53 (closed on phase4) |
| KI-pending-main-suite-drift, -draft-and-ask | in `main` (PR #27) | **final 58, 59** |
| KI-pending-memory-isolation, -memory-attack-surface, -memory-split-limits | memory-evals, search-spike, memory | **25, 26**, 27 |
| KI-pending-memory-search-limits, -estimate-from-cache | memory | 28, 29 |
| KI-pending-safety-prompts-pinned … -router-tiers-v2 (12 issues) | phase4 (rebased 10-30) | **final 66–77** (was 30–36, 38–42; #36 → #72, #42 → #77 closed) |
| KI-pending-student-no-memory | phase4 (rebased 10-30) | **final 78** (was 51) |
| KI-pending-training-on-bench, -secret-scrub-v0 | trace-dataset-v0 | **25, 26** |

Clashes (bold): decisions **16, 17, 18, 19, 20**; issues **25, 26**. Phase4 skips decisions 13–18 (it predates #9/#11). The merge rehearsal's "next free issue is 51" is wrong: phase4 uses 51. `main` now holds decisions 15–17 and issues 52–59 (with tiers-v2), so every other branch's 16 and 17 must move, and **the next free numbers on `main` are decision 18 and issue 60**. Update 2026-10-29 late: console took decision 18 and issue 60, voice decisions 19–21 and issues 61–65, so **the next free numbers on `main` are decision 22 and issue 66**. Update 2026-10-30: phase4 takes decisions 22–28 and issues 66–78 when it merges; the next free are then **decision 29 and issue 79**. Update 2026-10-30 evening: phase4 merged (PR #37), so **the next free numbers on `main` are decision 29 and issue 79**. Update 2026-11-01: `phase4/kaggle-teacher` takes **decision 29** (Phase 4 teacher, option a) when it merges; the next free are then **decision 30 and issue 79**. Update 2026-11-02: PR #50 merged (decision 29 with it); its pending names are numbered in PR #52 as decisions 30, 31 and issue 79, so **the next free numbers on `main` are decision 32 and issue 80**.

## Data outside git

| What | Where | Backed up |
|---|---|---|
| Phase 3 results (11 JSON, plus `.md`/`.log`) and their traces (`logs/bench.db`) | `..\kestrel-runs\evals\results\`, `..\kestrel-runs\logs\` | JSON + db, 2026-10-26 (live file as a PARTIAL snapshot) |
| Phase 2 bench traces, chat traces, approvals | `logs/bench.db`, `logs/traces.db`, `logs/approvals.jsonl` | yes |
| Voice recordings (your voice, room audio; private) | `data/wake/`, `data/talk/` | yes |
| Phase 2 intermediate results (superseded by `evals/baselines/`) | `evals/results/` | no |
| Kaggle smoke bundle (rebuildable) | `dist/kaggle-smoke/` | no |
| Kaggle notebook and dataset versions | Kaggle | – |
| Option a collection and training set: results file, `distill.db`, the dataset (89 examples) | `..\kestrel-runs\evals\results\distill-d1.2-groq-gpt-oss-120b.*`, `..\kestrel-runs\logs\distill.db`, `..\kestrel-runs\data\distill\d1.2-groq-gpt-oss-120b\` | yes, 2026-11-02 backup |
| Option c evidence: teacher selection, pass 1 collection, its dataset (evidence only), scratch DB | `..\kestrel-runs\evals\results\teacher-selection-5ec57a709a43\`, `...\collect-5cce69cdee15\`, `..\kestrel-runs\data\distill\d1.2-pass1\`, `%TEMP%\distill-d1.2-pass1.db` | yes, 2026-10-31 backup |

Backup: `C:\kestrel-backup\2026-10-26\` (116 files, 172.5 MB, `MANIFEST.txt` with SHA-256 per file). `C:\kestrel-backup\2026-10-31\` (32 files, 3.2 MB, option c evidence, `MANIFEST.txt`). `C:\kestrel-backup\2026-11-02\` (6 files, 2.2 MB, option a collection and training set, `MANIFEST.txt`). **Off-laptop copy: not yet.** The backup is on the same disk, so copy it to a USB drive or private cloud storage. `.env` (both folders) is never backed up here.
