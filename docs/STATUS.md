# Kestrel status

Changed only through docs PRs to `main`; feature branches never edit this file. Full history: [progress.md](progress.md).

## Updated

2026-11-12, from the Telegram post-merge docs session (log below); the same day, the v1.0 release PR (`release/v1.0`): owner decision 9 (Phase 4 done) and #90's measured CI runs. `main` = `440db5c` before that PR.

## Now

**Phase 4 gate passed:** section 9, PASS by its frozen rule 9.6 (2026-11-09, `evals/reports/phase4-section9-verdict.md`,
PR #87), after section 8's FAIL (11-05). The router ships opt-in (`KESTREL_PROVIDERS=routed`, off by default; PR #79).
`main` runs rules `tiers-v2.2`, default agent fingerprint `8a8174d8f17d` (PR #90).

**Telegram channel in `main`** (PR #96, `c51c4f6`, 2026-11-12): `uv run kestrel telegram`, `docs/telegram.md`,
decision 32, known issues 84-88.

**Open:**
- **v1.0:** README, release notes and blog post 2 are merged (#91, #92) and the repo is public. The release notes'
  final version (no draft banner, numbers re-checked against their sources) is in the `release/v1.0` PR; the tag
  `v1.0.0` and the GitHub release follow its merge, on the owner's OK (`docs/release-checklist.md` section 5).
- **Phase 4 is done** (owner decision 9, 2026-11-12): the gate passed on 11-09, and there's no retrain. Next in the
  owner's order (2026-11-11): the Google tools, after the v1.0 tag; their measurement budget needs its own approval.
- **PR #94,** the kestrel-data RCA design (docs only): open, built after Phase 4.
- **Merged branches not yet deleted:** 14 on `origin` (Branches, below).

## 2026-11-12 (Telegram session; Groq only for #96's CI)

1. **Telegram channel merged** (PR #96, `c51c4f6`): built offline, then a live phone test with the demo provider.
   Behaviour-neutral: fingerprint `8a8174d8f17d`, tools `ede3efa38c54`, system prompt `36d57c7e8a82`, equal to
   `main`'s before the merge.
   - **CI:** the PR run 94% (15/16), 0 errors, 31,480 billable; the merge-to-`main` run 87.5% (14/16), 0 errors,
     20,048 billable. Failures: `multi-scale-recipe` (both runs, systematic in `evals/reports/run-to-run.md`) and
     `convo-chained-math` (main only, the known calculator flip in `evals/reports/ci-threshold.md`).
   - Two earlier PR runs failed on ubuntu in the new `logs/` test guard (Linux `rmtree` paths); KestrelBench was
     skipped in both, so they cost 0 tokens.
2. **The live test found 3 bugs, all fixed before the merge:**
   - a failed start message (the documented `TELEGRAM_ALLOWED_USER_ID=1` id lookup) looked like a crash; failed
     sends are now logged with Telegram's reason and never stop the bot;
   - an expired card read like the next one; it now ends "Expired: no answer in 5 min, not done";
   - a test wrote to the real `logs/approvals.jsonl` (an `ApprovalGate` with no `log_path`, one line per full test
     run since 11-05); tests now use a scratch audit log and fail if they touch `logs/`.
3. **Audit log cleaned** (owner's OK): the 19 test-written `search` lines were moved, in order, from
   `logs/approvals.jsonl` (45 lines -> 26, every other line byte-identical) to
   `logs/approvals.test-pollution-2026-11-12.jsonl`; the original is in `C:\kestrel-backup\2026-11-12\`.
4. **Token leak check** (`scripts/live/telegram_leak_check.py`, after the phone test): 9 files in `logs/`, the token
   and its secret part in none.
5. **The 10-28 merge queue is done:** `console/hud-theme` (PR #29, 10-29), `voice/talk` (PR #31, 10-30) and
   `phase4/trace-dataset` (PR #37, 10-30) are all in `main`, and their branches are gone. No next action for any.

## 2026-11-08 afternoon (branch-cleanup docs session; no Groq, no model calls)

1. **Merged into `main`:**
   - PR #82, the judge calibration report: held-out 32 answers, 91% (95% CI 78–100%), kappa 0.0, below #21's 0.6;
   - PR #83: the 32 run 2 labels now point at the pinned copy, so `calibrate` works on a clean clone, with identical output;
   - PR #84: the derived numbers (94.8%, 0.970) recorded in `phase4-result.md` with their commands.
   - **CI flake on #84:** the push-event Windows job failed once on `test_wake.py::test_wake_flow_end_to_end`; the re-run of that job passed.
2. **Pushed, no PR:**
   - `docs/readme-v1`: the README rewrite, fixed after a fresh-clone test in demo mode, and the v1.0 release notes draft;
   - `docs/blog-phase4`: blog post 2 with section 8's result and a section 9 placeholder;
   - `fix/voice-console-week` and `fix/fetch-private-block`, with `main` merged in and all checks green.
   - **Fingerprints:** `35fac5ba6277` on both. The fetch branch changes the gate without bumping `RULES_VERSION` (owner's call before its PR).
3. **Branch cleanup (owner's OK):**
   - 9 merged branches deleted, and 7 parked branches archived as tags (`archive/<branch>`, list under Branches) and then deleted.
   - The `..\kestrel-f5` worktree was removed; it held only an empty log. `..\kestrel-runs` is again the only other worktree.
   - `fix/bench-audit-line-separator` stays until #79 merges.

## 2026-11-08 (demo-video docs session; no Groq from the session)

1. **Demo video published:** [Demo (1:50)](https://youtu.be/kglrGnkzt_I), linked near the top of README.md. Scene 3 uses a scripted model to show the approval gate stopping a prompt injection; the closing slide's numbers are the measured ones, each linked in README's Results section. Phase 5's demo video is done; the posts are not.

## 2026-11-05 night (after-section-8 session; no Groq from the session)

1. **Owner decisions (8.7, D-pending-after-section8):** the FAIL stands; next decision 9's fix, then a new pre-registered measurement (section 9); retraining only if that fails; `rejudge` deferred. **Correction found after them:** Ollama 0.40.14 changed the student's sampling, not just the version: 1.0 / 1.0 for the first 320 routed runs and the student-alone runs, 0.7 / 0.8 for the last 180 and group S, from Ollama's source, not measured (8.7, KI-pending-ollama-auto-update).
2. **Parity on 0.40.14:** 20/20 live on the running server; renderer and golden 20/20, both at v0.40.6; a v0.40.14 renderer not built (~460 MB, owner's call). `distill/EVAL.md` step 0 now stops a run on any Ollama version but the recorded one. The owner turns off "Auto-download updates".
3. **`phase4/integration` fixed at the root:** the drift reference's original CRLF bytes stored with `-text` (`9ad3742`), pin and section 8 text unchanged. **`phase4/tainted-to-teacher`** (`97f14b1`, pushed, no PR): design and section 9 draft, no code.
4. **Owner decisions on the design and section 9 (8.7):** the sampling finding is disclosed, not recomputed; rule A, plus explicit student sampling in the routed fingerprint; only FAIL retrains; if group S fails, no main suite; no 460 MB renderer; rubber-stamp ×1; section 9 approved up to 600k over at most 4 Groq days, the fix PR's ~56k on its own line.

## 2026-11-05 evening (section 8 verdict session; no Groq from the session)

1. **Outcome (section 8.5): FAIL**, by the safety gate: group S rubber-stamp system-level 1/17 (`atk-exfil-search-file`), where 8.7's rule requires 0 (`--safety failed`). Strict 0/17, gate line PASS; group F the teacher's 0 by the pre-route rule (`tests/test_routed_safety.py`). The rubber-stamp run made no Groq calls.
2. **The other conditions:** quality NOT SHOWN, mean(routed − 0.95 × teacher) +0.0188 (95% CI −0.0342 to +0.0715), at 0.90 lower bound +0.0120, (b) NOT SHOWN; check-pass teacher 92%, routed 90%. Cost SHOWN, −$0.544 per 1,000 tasks (95% CI −$0.597 to −$0.494), $0.030 against $0.574; teacher's share of turns 3.4% (95% CI 1.3–6.0%); drift none; no student-health stop. Secondary metric not run.
3. **Pinned:** the three routed files in `evals/after-runs/`, with tests. **Groq:** main 68,348 + aborted 59,554 = 127,902 of 500k; group S 12,991 of 180k. **Disclosed in 8.7:** Ollama updated itself from 0.40.13 to 0.40.14 at 15:08 EST on 11-05, between the main suite's two segments (KI-pending-ollama-auto-update; parity not repeated on 0.40.14). Backup `C:\kestrel-backup\2026-11-05\`. `bench/student-alone-label` and the two 11-05 bugs no longer wait for the section 8 runs; `phase4/integration`'s red Ubuntu CI (below) still needs the owner's call.

## 2026-11-05 (routed-main-done session; no Groq from the session)

1. **Routed main suite complete:** 500/500 graded, 0 skipped, 0 errored, at `bf1b68b`, agent `502b95cc0fb8`: 11-04 18:39–19:46 EST (320 runs, stopped by its budget) and 11-05 15:39–16:27 EST (180 runs, `--resume`). **68,348 Groq billable;** with both aborted attempts **127,902 of the 500k approval**. No pass rate read. `docs/phase4-plan.md` 8.7 has the detail, including the owner's disclosure: 6 per-task results from repeat 4 were printed after the run ended, by following an instruction of Claude's meant for a refused run; the frozen design is unchanged.
2. **Two bugs found, both to fix after the section 8 runs:** `--token-budget` counts the student's free local tokens (KI-pending-budget-counts-local-tokens; the file's "billable" 202,084 includes 133,736 of them), and a routed run started with `--model` can't resume (KI-pending-routed-resume-fingerprint, 3 refused starts in all, one of them a mistyped budget, 0 Groq). **Workaround:** no `--model`, on the owner's OK; `KESTREL_ROUTER_STUDENT_MODEL` names the student. `distill/EVAL.md` is corrected.
3. **Both fingerprints are one agent:** `502b95cc0fb8` in results files, `4da8f4b3c792` in the docs; the hash covers the model label, `kestrel-student -> openai/gpt-oss-120b` or `kestrel-student`. **Backup:** `C:\kestrel-backup\2026-11-04\` (results, all 5 logs, `bench.db` by the backup API, integrity check ok, 31.9 MB with `MANIFEST.txt`; the runs' logs still unread by Claude). 
4. **`fix/bench-audit-line-separator` merged into `phase4/integration`** (`426f5a5`, pushed; local checks 1181 passed, default fingerprint `35fac5ba6277`). **That branch's Ubuntu CI is red, and was before this merge** (`4642888`, `552a5ff`): `test_the_pinned_references_are_the_files_in_git`, `drift_reference`. `scripts/phase4_result.py` pins `evals/reports/ci-repeat3-2026-10-07.json` at `4750d773…`, its CRLF form from a Windows checkout; the bytes in git hash to `c0e2d44a…`, and `.gitattributes` has no `-text` for that folder. Must be fixed before that branch can merge; pin not changed, owner's call.

## 2026-11-04 (routed attempts session; no Groq from the session)

1. **Two aborted attempts, both before any routed result was seen** (`docs/phase4-plan.md` 8.7): `a59439b`, a crash in the bench's audit reader (22 Groq calls, ~30.5k); `46c54d8`, the process killed from outside when its window disappeared by itself, cause unknown (20 calls, ~29k). Fixes on `fix/bench-audit-line-separator` (`bf1b68b`, pushed, no PR until the section 8 runs are done). **Tonight: restart at `bf1b68b`**, all 500 runs. If a run dies at `act-packing-list` repeat 2 again, it isn't chance.
2. **Pre-commit's Go tools, 11-04 18:08-18:09 EST (no change needed).** Go is now on `PATH` (`C:\Program Files\Go`, 1.27.3), so the next commit made pre-commit build gitleaks again with that Go, as a new environment. New: `golangenv-system` 44 MB (`gitleaks.exe` 25 MB, `config.exe` 21 MB) in `~\.cache\pre-commit\repo3humfkh1`, and 176 MB of Go build cache in `~\AppData\Local\go-build` (298 MB in all). Go modules were downloaded for the build and removed afterwards, so their size can't be read from the cache, and it isn't known whether the download passed the 100 MB rule. No Go toolchain was downloaded. The pre-commit cache's 398 MB total is mostly from 2026-10-03 (`golangenv-default`: Go 1.27.1 268 MB, binaries 44 MB; Python hooks 40 MB).

## 2026-11-03 evening (quota-fix and blog session; KestrelBench not run, no Groq)

1. **11-03: `groq_quota.py` reads each job's log (#61), EVAL.md router-variables note (#62), demo kit merged (#59).** A KestrelBench job counts as having used Groq only if its log lacks "KestrelBench not run"; the two 32–33 s not-run jobs now count as not run. Without `KESTREL_ROUTER_STUDENT_MODEL` a routed run is labelled `qwen3:4b` (found in the pre-flight; the student is still the one called).
2. **Blog post 2 skeleton** on `docs/blog-phase4` (`d1f92ab`, no PR): section 5 is placeholders mapped to `evals/reports/phase4-result.md`.
3. **Next:** routed main suite day 1 starts 11-04 after 09:40 EST (cancelled CI runs leave the window at 09:38), once the quota check prints OK. Pre-flight passed offline: `a59439b`, default `35fac5ba6277`, routed `4da8f4b3c792` (router block only), dry run 500 runs.

## 2026-11-03 (drift-control session; KestrelBench not run, no Groq from the session)

1. **The same-week drift control ran** (owner, 09:03–09:09 EST, runs folder at `c7c0d4f`, old agent `cc5c16377662`, CI subset ×2): 88%, 0 errored, **49,953 billable**, within the 55,000 approval. Pinned `evals/controls/control-old-ci-2026-11-03.json` (`5a587c8c…`) with a test.
2. **#58 reading (pre-registered 11-01): drift not shown.** 10-07 ×3 88% vs 11-03 ×2 88%: +0 points (95% CI −10 to +11), not entirely below 0, so section 8 uses `--drift none` (recorded in #58 and `docs/phase4-plan.md` 8.7).
3. **Process slip, on the record:** the run was started while `groq_quota.py` printed NOT ENOUGH (51,862 free); it used 49,953, so the margin held, but the stop rule was skipped. Rule kept: a run starts only after the check prints OK. Also fixed: `groq_quota.py` missed judge tokens (not traced); it now adds them from the results files (45,034 → 49,953 for this run).

## 2026-11-03 (student-safety session; KestrelBench not run, no Groq)

1. **11-02 evening: the free runs F1-F3 ran** (`kestrel-student` alone on local Ollama, runs folder at `7ec2e84`, no judge, Groq and Gemini blocked in the window). F1 timing smoke 10/10, p50 14.5 s per task. F2 strict ×3 and F3 rubber-stamp ×1, 0 errored: system ASR strict **0/29**, rubber-stamp **3/29**; model ASR strict 5/29, rubber-stamp 4/29; benign every repeat 10/18. Teacher v2.1: 0/29, 0/29, 1/29, 0/29, 11/18. No paired difference significant (exact McNemar p = 0.22, 0.125, 0.25; benign −7 points, 95% CI −28 to +13). Report `evals/reports/student-safety-alone.md`; the 7 undeclared actions and 2 one-click links are listed there for the owner's review, nothing declared. Reported alongside section 8 (8.7), not the claim.
2. **Pinned:** `evals/after-runs/student-safety-strict-x3.json` (`61f35e66…`) and `student-safety-rubber-attacks.json` (`de1a273f…`), with a pinning test. Backup `C:\kestrel-backup\2026-11-03\` (results + `bench.db` by the sqlite3 backup API, `MANIFEST.txt`).
3. **The "SMOKE TEST" label** on those reports came from `safety_report.run_label()`, which labelled every Ollama run so. Fixed on `bench/student-alone-label` (`8d998d9`, pushed, no PR): the served student's runs are labelled the student-alone measurement; grading unchanged (test: the report is identical apart from the label and the gate sentence). It changes `src/kestrel/`, so its PR runs the CI subset on Groq (~56k): it needs its own approval line and quota, best after the section 8 runs.

## 2026-11-03 (export-merge session; no Groq from the session)

1. **11-02/03: PR #53 (`phase4/export-readiness` → `main`) opened after the quota check:** 200,000 − 81,963 used in the last 24 h (the collection's last 18 calls 22,655, an upper bound; #50's PR run 28,598; #50's merge run 30,710) − 15,000 = 103,037 ≥ 70,000; no other CI run in progress.
2. **#53's two CI runs:** PR run 41555193647 88% (14/16, gate 75% passed), 0 errored, **31,892 billable**; merge-to-main run 41556301668 100% (16/16), 0 errored, **21,587 billable**; **53,479 in all, within the 70k approval**. Merged by the owner, `2cb2a4b`, 2026-11-03 00:55 UTC.
3. **#53 holds** the trained student's export path (`export.py --low-memory`), the Ollama tool-call tag fix (#81) and the router's `text_tool_call` escalation (#82, `docs/phase4-plan.md` 8.7, before any routed run); default fingerprint `35fac5ba6277`. Pending names numbered in this docs PR: known issues **#80** (GPU smoke test's own loop, closed), **#81** (Ollama tool-call tag), **#82** (router text tool call, closed), **#83** (Windows CI flake in `test_wake.py`, owner decision open). Next free on `main`: **decision 32, issue 84**.

## 2026-11-02 (export-readiness session; KestrelBench not run, no Groq)

1. **11-02: QLoRA training started on Kaggle (code a4b3372, data e39dd661), private datasets.** The first attempt stopped in `trainer.train()`: "GPU T4 x2" left two GPUs visible and TRL's SFTTrainer refused `nn.DataParallel`. The owner restarted it with one extra first notebook cell, `CUDA_VISIBLE_DEVICES=0`; code dataset `a4b3372` unchanged, so `lineage.json`'s `code_commit` stays `a4b3372`. Same fix in git on `phase4/export-readiness` (`277fd67`): the notebook cell, `train.py` refuses more than one visible GPU, GPU count recorded. The 10-20 GPU smoke test missed it because it uses its own loop, not the Trainer; all pins matched (known issue #80).
   The restart then **stopped at step 0/45**: `NotImplementedError: "_amp_foreach_non_finite_check_and_unscale_cuda" not implemented for 'BFloat16'` (`clip_grad_norm_` -> `GradScaler.unscale_`). Cause: trl 1.16.0's `SFTTrainer.__init__` casts a 4-bit model's LoRA weights to bfloat16 whatever `fp16` says; a T4 has no bfloat16. Fixed in `5b175f1` (`phase4/export-readiness`, pushed, no PR): `make_trainer()` casts them back to float32 and refuses to train otherwise, and `--gpu-smoke` now takes one real SFTTrainer step (fp16, clipping), so it would have caught both failures. `train_config.toml` and the data unchanged. New code zip: `<home>\Documents\kestrel-kaggle-upload\kestrel-train-code-5b175f17addf.zip` (sha256 `c012d2f2…`).
2. **Export readiness** (`phase4/export-readiness` `e1306dc`, off `phase4/student-eval`, pushed, no PR, CI green): nothing had to be installed (llama.cpp `b11712`, the parity renderer, the `train` + `export` extras, Ollama 0.40.13). 15.3 GB RAM (the merge needs ~9 GB free), 683 GB free disk; the ~8 GB base model is not downloaded yet. The smoke adapter ran the whole chain on Ollama 0.40.13: tool schemas identical, 20/20 prompt token counts for the student, `--gguf` and the base, template parity 20/20 (golden and renderer); test models deleted. New `scripts/check_lineage.py` (KAGGLE.md step 10 as a check, 9 tests); the commands for the adapter's arrival are in `distill/EXPORT.md`.
3. **Free student runs written** in `distill/EVAL.md` ("Free runs first", local Ollama, no judge, no Groq): F1 main smoke `--judge none --sample 10` (10 runs), F2 safety strict x3 (144), F3 safety rubber-stamp attacks (30); dry runs match. Waiting for the owner: README #46 (green, KestrelBench not run), Dependabot #38, #41 (green), #40 (green; no `run-evals` label run, the owner's call).
4. **PR #50 merged** (`phase4/kaggle-teacher` + `phase4/student-eval` → `main`, `43d5522`, 2026-11-02 19:55 UTC; the owner's merge). CI subset on Groq, both runs 81% (13/16, gate 75% passed), 0 errored: PR run 41471771520 **28,598 billable**, merge-to-main run 41514497831 **30,710 billable**; **59,308 in all, within the 70k approval**. Pending names numbered in docs PR #52: decision 30 (Phase 4 cost report), decision 31 (routed safety groups), known issue #79 (routed safety pre-route); the next free on `main` are then **decision 32 and issue 80** (`phase4/export-readiness` brings known issue #80).
5. **11-02: the student is trained and served (KestrelBench not run, no Groq).** Kaggle run on code `5b175f1` + data `e39dd661`, one T4, 44 min: 5/5 epochs, 45 steps, no early stop, val loss 0.660 → 0.409 (best = epoch 5); `check_lineage.py` all OK. Base model download 7.51 GB (`~\.cache\huggingface\hub`). With 2-3.6 GB of RAM free, exported with the new `export.py --low-memory` (tensor-by-tensor merge, bit-identical to peft's on the smoke model): GGUF Q4_K_M sha256 `53052ecf…` → `kestrel-student`; tool schemas identical, 20/20 token counts and template parity. A 3-task local sanity run first failed both tool tasks: Ollama took its tool-call tag from Kestrel's template as `{` and returned the student's well-formed `<tool_call>` blocks as text (known issue #81). Template fixed with identical prompt bytes, tag now checked in `golden.json`; re-created from the same GGUF: 3/3, no tool-call text or template tokens in answers. `phase4/export-readiness` `4d59e5f`, pushed, no PR. Backup `C:\kestrel-backup\2026-11-02\student\` (2.59 GB, MANIFEST-student.txt with SHA-256).

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
| 2 Evals | done, except judge calibration (#21): measured 2026-11-08 on 32 held-out answers, agreement 91% (78–100%), kappa 0.0, so not calibrated (`evals/reports/judge-calibration.md`) | README baseline 92%; `evals/baselines/` on `main`; labels in `evals/labels/human.jsonl` on `main` |
| 3 Memory and safety | permission tiers **done** (v2.1 shipped 2026-10-29 with the tiers-v2 merge, PR #27, decision 17; `main` runs `tiers-v2.2` since PR #90, default agent fingerprint `8a8174d8f17d`); memory parked until after Phase 4 | `main` `02c1d2e` (`evals/reports/safety-after-v2.1.md`, `evals/reports/rule-c-verdict.md`); memory on the tags `archive/phase3/memory-rag` (`docs/memory-rag-design.md`, owner decision 2026-10-26) and `archive/phase3/memory` |
| 4 Distillation | **done** (owner decision 9, 2026-11-12; no retrain): tooling **in `main`** (PR #37, `4290fc6`, 2026-10-30; student-eval tooling and the option a pins PR #50, `43d5522`, 2026-11-02), claim pre-registered (`docs/phase4-plan.md` section 8); teacher data collected 11-01/02 and the training set built (89: 71 / 18); student trained on Kaggle 11-02 (5/5 epochs, val loss 0.660 → 0.409) and served as `kestrel-student` (PR #53, `2cb2a4b`); free local runs F1-F3 done; section 8 runs done 11-05: **outcome FAIL** (safety gate) (`evals/reports/phase4-result.md`); section 9 after decision 9's fix: **PASS** (11-09), **gate passed**, router opt-in (PR #79) | `main` (`docs/phase4-plan.md`, `distill/*.md`, `evals/reports/phase4-section9-verdict.md`) |
| 5 Voice and launch | in progress, started early: wake word and speech in/out; demo video ✓ done (2026-11-08); blog posts 1 and 2 in `main` (#92); the 2 MCP servers in `main` (#90); Telegram channel in `main` (#96); v1.0 not tagged yet | `main` (`docs/voice-wake.md`, `docs/telegram.md`, `servers/`); [demo video](https://youtu.be/kglrGnkzt_I) |

## Branches

**Live branches** (2026-11-12, on `origin`; the same set locally):

| Branch | Holds | Next |
|---|---|---|
| `main` | everything merged, up to PR #96 (`c51c4f6`); rules `tiers-v2.2`, default agent fingerprint `8a8174d8f17d` | – |
| `design/data-rca` | the kestrel-data RCA design, PR #94 (docs only; adds `D-pending-data-rca`, decision 33 if it merges next) | owner's decisions; built after Phase 4 |
| merged, not deleted (14): `feature/telegram` (#96, squash), `design/telegram-google` (#93, squash), `phase4/tainted-to-teacher`, `post-s9/combined`, `phase4/integration`, `fix/fetch-private-block`, `fix/voice-console-week`, `docs/readme-v1`, `docs/blog-phase4`, `docs/privacy-cleanup`, `docs/s9-verdict`, `docs/approval-79-combined`, `docs/status-mcp-combined`, `docs/status-2026-11-08` | all in `main` (checked 2026-11-12 with `git merge-base --is-ancestor`, or the PR for the two squashes) | delete with the owner's OK (`docs/release-checklist.md` 1.5) |

**Archived as tags, branches deleted (2026-11-08, owner's OK).** Each tag sits at the branch's last commit:

| Tag | Commit |
|---|---|
| `archive/phase3/memory` | `e960038` |
| `archive/phase3/memory-rag` | `afc3435` (holds `docs/memory-rag-design.md`) |
| `archive/phase3/memory-evals` | `b3d4851` |
| `archive/phase3/search-spike` | `514a00b` |
| `archive/phase4/results-kit` | `cfb9634` |
| `archive/bench/student-alone-label` | `8d998d9` |
| `archive/phase5/mcp-servers` | `dedd05d` (the 2 read-only MCP servers; the tag stays, the code comes back via the combined PR) |
| `archive/voice/talk` | `5f8f681` (folded into `fix/voice-console-week`, 2026-11-08) |

- **Restore one:** `git switch -c <branch> archive/<branch>`, for example `git switch -c phase3/memory archive/phase3/memory`.
- **Older archive tags** (different naming): `archive/console-hud-theme-9c4bb52`, `archive/docs-merge-plan-56d1edf`, `archive/fix-quota-retry-spend-ee496a0`, `archive/phase4-trace-dataset-1f0084f`, `archive/phase4-trace-dataset-v0-289274e`, `archive/voice-talk-4155004`, `archive/voice-talk-8a4e22b`, `archive/voice-wake-word-88bb9d1`.

**Deleted 2026-11-08** (owner's OK; local and `origin`, all merged into `main`): `deps/pin-training-libs`, `docs/demo-video`, `docs/judge-calibration`, `docs/phase4-derived-numbers`, `docs/s9-approval`, `docs/s9-approval-303k`, `fix/audit-2026-11-03`, `fix/label-paths`, and `dependabot/npm_and_yarn/console/console-5cae597071` (origin only).

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
| 11-02 | 11-02: phase4/export-readiness → main, one CI run on the PR + one on the merge | ~56k (estimate) | approved by the owner, up to **70k billable**. `main` merged into `phase4/export-readiness` and the checks run before the PR opens; default agent fingerprint `35fac5ba6277`. Opens only after this line is on `main`, while no other CI run is in progress, and when 200,000 − last-24 h use − 15,000 ≥ 70,000. Extra pushes to the open PR need a new approval line. **Ran:** PR #53, PR run 31,892 + merge-to-main run 21,587 = **53,479 billable**, within the 70k approval (88% 14/16 and 100% 16/16, 0 errored). |
| 11-03 | 11-03: same-week drift control (#58), old agent ×2 on the CI subset, approved up to 55,000 billable. | ~40k (39,706 measured 10-29) | approved by the owner (11-03). One command in `..\kestrel-runs` at `c7c0d4f` (agent `cc5c16377662`): `--subset ci --repeat 2 --token-budget 55000`, judge groq, no `--wait-for-quota`, results `control-old-ci-2026-11-03.json`; then the runs folder back to `a59439b`. Starts after 10:30 EST, while no other Groq consumer runs, when `scripts/groq_quota.py --need 55000` prints OK. **Ran:** 2026-11-03 09:03–09:09 EST (agent runs 14:03–14:09 UTC), runs folder at `c7c0d4f`, agent `cc5c16377662`, 28/32 runs passed, 0 errored; **49,953 billable** by the run's summary (agent 45,034 + judge 4,919), within the 55,000 approval. The Groq-side count from the trace database was 45,034: both subtract cached input (29,184 here), but the judge's calls aren't traced, so the traces alone missed the judge's 4,919; `groq_quota.py` now adds a run's judge tokens from its results file. **Process slip:** the run was started although `groq_quota.py --need 55000` had printed NOT ENOUGH (51,862 free). It used less than was free (49,953 ≤ 51,862; window total 183,091 ≤ 185,000), so no limit was hit and the 15,000 margin held, but the stop rule was skipped. **Rule kept:** a run starts only after the check prints OK. Reading: drift not shown (+0 points, 95% CI −10 to +11; #58). |
| 11-04 onward | 11-04 onward: routed main suite R=5, one command per Groq day with `--token-budget` = that day's 200,000 − last 24 h − 15,000, approved up to **500,000 billable** over at most 3 days (11-04 to 11-06), counting both aborted attempts (~30.5k + ~29k = **~59.5k so far**). | 5 × e × 153k: 76.5k / 229.5k / 382.5k at e = 10 / 30 / 50% | approved by the owner (11-03, up to 400,000); **raised to 500,000 by the owner on 11-04, before any routed data exists:** at e ≈ 50% the estimate (382.5k) plus the aborted 30.5k exceeds 400k, and an incomplete R=5 would break the pre-registered design. `--provider routed --model kestrel-student --repeat 5 --judge none --wait-for-quota`, results `routed-main-r5.json` (`--resume` on later days); `KESTREL_REQUEST_TIMEOUT` unset (60 s); `KESTREL_ROUTER_LINEAGE` set per session (`docs/phase4-plan.md` 8.7, 11-03). First restart at `46c54d8` aborted (8.7, 11-04, second note); **the restart at `bf1b68b` runs tonight (11-04)**, all 500 runs from the start. After the drift control. **Ran:** 11-04 18:39–19:46 EST (320 runs, stopped by its budget) and 11-05 15:39–16:27 EST (180 runs, `--resume` without `--model`, owner's OK 11-05), at `bf1b68b`, agent `502b95cc0fb8`; 500/500 graded, 0 errored; **68,348 Groq billable** from the traces (the file's 202,084 includes 133,736 local Ollama tokens); with both aborted attempts (59,554) **127,902, within the 500,000 approval**. |
| after the main suite | Routed safety group S (8.7), strict ×3 + rubber-stamp ×1, approved up to 180,000 billable. | 18k / 54k / 90k at e = 10 / 30 / 50%; ceiling ~180k | approved by the owner (11-03). `--split safety --safety-group S --provider routed` (no `--model`, 8.7, 11-05), results `routed-safety-S-strict-x3.json` and `routed-safety-S-rubber-attacks.json`, `KESTREL_IMAGE_POLICY=allowlist`, `KESTREL_MCP=off`; each command's `--token-budget` = that day's free quota. **Ran:** 11-05 17:07–17:26 and 17:31–17:35 EST, at `bf1b68b`, agent `502b95cc0fb8`; 87/87 and 18/18 graded; **12,991 Groq billable** from the traces (strict 12,991, rubber-stamp 0; the files' 35,135 and 4,640 include local tokens), within the 180,000 approval. |
| when opened | Fix PR (`phase4/tainted-to-teacher` → `main`), one CI run on the PR + one on the merge | ~56k (estimate) | approved by the owner (11-05) as its own line. Not opened yet: the build is pushed without a PR, on the owner's word. Starts when `scripts/groq_quota.py` prints OK and no other Groq consumer runs; extra pushes to the open PR need a new line. |
| after section 9 is frozen | Section 9 runs (`docs/phase4-plan.md`, section 9 once frozen): routed group S strict ×3 + rubber-stamp ×1, the same-week drift control, routed main suite R=5 | ~463k (estimate, rule A) | approved by the owner (11-05), up to **600,000 billable over at most 4 Groq days**. Nothing starts until section 9 is frozen. If group S fails, the main suite is not run. Each command's `--token-budget` = that day's free quota. |
| 11-08 onward | Section 9 as frozen (`0071e54`, `docs/phase4-section9-proposal.md`): the CI run on the `phase4/tainted-to-teacher` PR (#79: 31,604 measured); a second CI run on #79 after section 8's bench fixes are merged into the branch (~31k); run 1, group S strict ×3 (~73k); run 2, group S rubber-stamp ×3 (~57k); run 3, the same-week drift control (~50k); run 4, routed main suite R=1 (~64k) | **~303k** (trace-based estimates; was ~272k before the second CI run) | **approved by the owner (2026-11-07; raised from ~272k to ~303k the same night), within the 600,000 approval above.** Why the second CI run: the tagged commit lacked section 8's bench fixes (`fix/bench-audit-line-separator`: the U+2028 audit reader, lone-surrogate-safe results, a save after every run, no overwrite, the RUN STOPPED line), which never reached `main`; they are merged into the branch before any section 9 run, and pushing to the open PR re-runs its CI. Runs 1, 2 and 4 at the tag `phase4-s9-runs` on the PR head after `main` is merged into it (agent code and dependencies then equal `main` after the merge; routed fingerprint `2ae99cffdbbc` from a dry run there); the PR isn't merged before the runs. Results `s9-safety-S-strict-x3.json`, `s9-safety-S-rubber-attacks-x3.json`, `s9-main-r1.json`. Stop rule 9.3: runs 3-4 only if group S holds. `--wait-for-quota`; each `--token-budget` from `scripts/groq_quota.py`. **Ran:** 11-07 to 11-09, all four runs graded; **293,042 billable** of the 303,000. Verdict PASS by 9.6 (`evals/reports/phase4-section9-verdict.md`). |
| 11-09 onward | After section 9's PASS: (1) #79's CI after pushing its banner commit (`a825b81`, "frozen in 0071e54"), ~31k; (2) #79's merge-to-`main` run, ~31k; (3) the combined PR (`post-s9/combined`: `fix/fetch-private-block` + `fix/voice-console-week` + `phase4/integration`, rules `tiers-v2.2`), its PR run and its merge-to-`main` run, ~56k | **~118k** (estimate from #79's measured 31,604 and ~28k per CI subset run) | **approved by the owner (2026-11-09), up to ~120,000 billable** for these four runs only. Each starts only when `scripts/groq_quota.py` prints OK and no other Groq consumer runs; any other push to an open PR needs a new line. **Ran:** #79's two runs (51,696 together, release checklist); the combined PR #90, **49,694 measured** (PR run 29,410, run 42755872462, 14/15 graded, 1 errored; merge-to-`main` run 20,284, run 42759045086, 14/16, 0 errored). |
| 2026-11-12 | feature/telegram PR: CI subset on the PR + on the merge to main | ~56k (est.); **51,528 measured** (PR run 31,480 + main run 20,048) | approved by the owner 2026-11-12. **Ran:** PR #96, 94% and 87.5%, 0 errors; two earlier PR runs skipped KestrelBench (0 tokens). |

## Owner decisions open

1. ~~**Phase 3 tiers gate**~~: decided 2026-10-28, ship tiers v2.1 (owner override on rule A part 1); in `main` as design decision 17 (PR #27).
2. **Memory before or after teacher data:** decided 2026-10-26 (option B: Phase 4 first). In `main` with phase4 (CLAUDE.md, `docs/phase4-plan.md`); `docs/memory-rag-design.md` is still on `phase3/memory-rag`.
3. **Park the v1.2 suite items until after Phase 4** (D1 says "after the Phase 3 gate"): `sandbox-parent-dir` leak check, `act-email-priya-actions` ambiguity, hard split.
4. **CI threshold:** the proposal is to keep 75% (`evals/reports/ci-threshold.md`, on the tiers-v2 line).
5. ~~**Teacher decision (#72, Groq §6.3(e))**~~: decided 2026-11-01, **option a** (Groq's written confirmation, 10-31), design decision 29 on `phase4/kaggle-teacher`. The student weights are not published.
6. Also waiting: labels for #21; tiers-v2 §6 questions 4–8. (`docs/phase4-plan.md` open question 13 is answered by section 8, 2026-10-30.)
7. ~~**Telegram and Google tools order**~~: decided 2026-11-11 (`docs/telegram-google-design.md`, "Owner decisions"): the Telegram channel → Phase 4 teacher data and measurements → Google tools. **Telegram done** (PR #96, 2026-11-12); the Phase 4 step is settled by decision 9. Google's measurement budget is **not approved**: it gets a fresh `--dry-run` estimate and its own approval line.
8. **kestrel-data root-cause-analysis server:** design in PR #94 (docs only, open; `D-pending-data-rca`). Built after Phase 4, like Google.
9. ~~**Phase 4 done or retrain**~~: decided 2026-11-12: **Phase 4 declared done** (the gate passed 11-09 with section 9; no retrain). "Retrain with memory" stays the planned follow-up once memory is built and measured.

## Docs on unmerged branches

A new decision or issue keeps a slug until its branch merges; `main` assigns the number then. The numbers the branches use today (since 2026-11-08 the memory branches, `phase4/results-kit` and `bench/student-alone-label` are archive tags, listed under Branches):

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
| KI-pending-gpu-smoke-own-loop, -ollama-tool-tag, -router-text-tool-call, -wake-greeting-timeout | in `main` (PR #53) | **final 80, 81, 82, 83** (80 and 82 closed; numbered 2026-11-03) |
| KI-pending-budget-counts-local-tokens, -routed-resume-fingerprint, -ollama-auto-update | in `main` (docs PRs of 11-05) | not numbered yet |
| KI-pending-training-on-bench, -secret-scrub-v0 | trace-dataset-v0 | **25, 26** |

Clashes (bold): decisions **16, 17, 18, 19, 20**; issues **25, 26**. Phase4 skips decisions 13–18 (it predates #9/#11). The merge rehearsal's "next free issue is 51" is wrong: phase4 uses 51. `main` now holds decisions 15–17 and issues 52–59 (with tiers-v2), so every other branch's 16 and 17 must move, and **the next free numbers on `main` are decision 18 and issue 60**. Update 2026-10-29 late: console took decision 18 and issue 60, voice decisions 19–21 and issues 61–65, so **the next free numbers on `main` are decision 22 and issue 66**. Update 2026-10-30: phase4 takes decisions 22–28 and issues 66–78 when it merges; the next free are then **decision 29 and issue 79**. Update 2026-10-30 evening: phase4 merged (PR #37), so **the next free numbers on `main` are decision 29 and issue 79**. Update 2026-11-01: `phase4/kaggle-teacher` takes **decision 29** (Phase 4 teacher, option a) when it merges; the next free are then **decision 30 and issue 79**. Update 2026-11-02: PR #50 merged (decision 29 with it); its pending names are numbered in PR #52 as decisions 30, 31 and issue 79, so **the next free numbers on `main` are decision 32 and issue 80**. Update 2026-11-12: "issue 80" was stale, since `main` already held issues 80-83 (PR #53, numbered 11-03). Telegram took decision 32 and issues 84-88 (PR #96), so **the next free numbers on `main` are decision 33 and issue 89**. The `KI-pending-*` and `D-pending-*` entries already in `main` (from the docs PRs of 11-05 onward) still have no numbers.

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
| Student-alone runs F1-F3 (results, reports) and their traces | `..\kestrel-runs\evals\results\student-*`, `..\kestrel-runs\logs\bench.db` | yes, 2026-11-03 (the two safety JSON also pinned in git) |
| Routed main suite: results, report, 5 logs and traces | `..\kestrel-runs\evals\results\routed-main-r5.*`, `..\kestrel-runs\routed-2026*.log`, `..\kestrel-runs\logs\bench.db` | yes, `C:\kestrel-backup\2026-11-04\` (written 11-05); results also pinned in git |
| Routed safety group S: results, reports, 2 logs, the cost report and traces | `..\kestrel-runs\evals\results\routed-safety-S-*`, `..\kestrel-runs\routed-safety-S-*.log`, `..\kestrel-runs\logs\bench.db` | yes, `C:\kestrel-backup\2026-11-05\` (10 files, 32 MB, `MANIFEST.txt`); results also pinned in git |
| The 19 test-written audit-log lines (moved out of `logs/approvals.jsonl`, 2026-11-12) | `logs/approvals.test-pollution-2026-11-12.jsonl` | the original file, `C:\kestrel-backup\2026-11-12\approvals.jsonl.orig` (SHA-256 `58f1f0ab…82b0`) |

Backup: `C:\kestrel-backup\2026-10-26\` (116 files, 172.5 MB, `MANIFEST.txt` with SHA-256 per file). `C:\kestrel-backup\2026-10-31\` (32 files, 3.2 MB, option c evidence, `MANIFEST.txt`). `C:\kestrel-backup\2026-11-02\` (6 files, 2.2 MB, option a collection and training set, `MANIFEST.txt`). **Off-laptop copy: not yet.** The backup is on the same disk, so copy it to a USB drive or private cloud storage. `.env` (both folders) is never backed up here. `C:\kestrel-backup\2026-11-03\` (7 files, 24.8 MB: the student-alone results and `bench.db`, `MANIFEST.txt`).
