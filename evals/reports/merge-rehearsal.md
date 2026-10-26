# Merge rehearsal (2026-10-26)

A local rehearsal of merging every open line of work into `main`, to find conflicts, numbering clashes and
failures before the real PRs. Nothing was pushed or opened, no existing branch was changed, `..\kestrel-runs`
was not touched, and no model was called. The rehearsal ran in a throwaway local clone (the working folder was in
use on `voice/talk` by another session), on a branch `rehearsal/all`, deleted afterwards. Dependencies came from
the local caches only (`uv sync --locked --offline`, `npm ci --offline`).

## Merge order and base

Base: `origin/main` = `880d601` (local `main` was 2 commits behind: #9 and #11). Order, each with `git merge --no-ff`:

| # | Branch | Tip | Result |
|---|---|---|---|
| 1 | `phase3/tiers-v2` | `6a551a5` | clean |
| 2 | `phase3/memory-rag` | - | **does not exist** (skipped) |
| 3 | `console/hud-theme` | `9c4bb52` | clean |
| 4 | `voice/wake-word` | `6468622` | clean |
| 5 | `voice/talk` | `4155004` | clean |
| 6 | `phase4/trace-dataset` | `548446d` | **8 files in conflict, 31 hunks** |

Notes on the branch graph:

- `console/hud-theme`, `voice/wake-word` and `voice/talk` are stacked, and all three are built on `phase3/tiers-v2`
  at `75ce54c` (the v2.1 fix). Only one tiers-v2 commit is not in them: `6a551a5` (the v2.1 safety record, docs only).
- `phase4/trace-dataset` branched from `main` before #9 and #11, so it is the only branch that meets the Phase 3
  code for the first time at the merge.
- Not in this rehearsal (not on the list): `phase3/memory`, `phase3/memory-evals`, `phase3/memory-safety` (its tip
  `6f58ee5`; most of it is in `main` through #9), `phase3/results-analysis`, `phase3/search-spike`,
  `phase4/trace-dataset-v0`, `dev`. None of them is contained in `tiers-v2` or `voice/talk`.

## Conflicts (merge 6: `phase4/trace-dataset` into tiers-v2 + console + voice)

"Ours" = tiers-v2 + console + voice; "theirs" = phase4.

| File | Hunks | What clashes | Resolution |
|---|---|---|---|
| `src/kestrel/bench/cli.py` | 13 | Both branches restructured `bench run` for a third split: tiers-v2 `safety` (per-user passes, checkpoints, quota waits, `--user`, `--kinds`, `--scripted`), phase4 `distill` (its own task folder, workspace, trace DB and meta) and the `routed` provider. | Keep tiers-v2's structure; port phase4 in: `_split_dirs()` for main/distill next to `_safety()`; task pool by split; output label `safety-*` / `distill-*`; `routed` provider check and `RouterConfig`; fingerprint uses the router label for routed runs; `--reuse` refuses another split and passes the workspace; distill meta added after the shared meta; in the per-user loop the main/distill runs get `base_workspace`, the distill trace DB and `student_health_limit`. Imports: union. `_compare_safety`/`validate`/`show` and `quality_verdict`: both. |
| `src/kestrel/bench/runner.py` | 4 | `TaskResult` fields (`safety`, `user`, `approvals` vs `routing`), `run_task` keyword, `run_suite` parameters (`setup`, `grader`, `quota` vs `student_health_limit`), its docstring. | Both sides; one docstring with both paragraphs. |
| `scripts/check_results.py` | 3 | Imports, `check()` signature, the safety branch vs the distill branch. | Both: `check(path, suite: str \| None, judge, safety=..., partial=False)`; safety first, then distill's default suite. |
| `pyproject.toml` | 1 | `[project.optional-dependencies]`: `wake` vs `train` + `export`. | One table with all three extras. |
| `uv.lock` | 7 | Follows `pyproject.toml`. | Regenerated: `uv lock` (offline, from the cache). |
| `.env.example` | 1 | Wake/talk settings vs router settings, same place. | Both blocks. |
| `evals/CHANGELOG.md` | 1 | "Agent changes" + "Safety split" sections vs "Check types added without a suite version change". | Both sections. |
| `docs/design-decisions.md` | 1 | Decisions 13-20 (tiers-v2, console, voice) vs 19-25 (phase4) at the end of the file. | Both, then renumber (below). |

**Silent merge defects** (merged without a conflict marker, found by the checks):

1. **`bench run` gets `--split` and `--split-dir` twice.** tiers-v2 added them (choices `main`, `safety`) and
   phase4 added them (choices `main`, `distill`) at different lines. argparse refuses the parser
   ("conflicting option string: --split"), so every CLI command and **44 tests** fail. Fix: one `--split` with
   `choices=("main", "safety", "distill")` (`RUN_SPLITS`; `validate` keeps `SPLITS = ("main", "safety")`) and one
   `--split-dir` whose help covers both.
2. **The results meta dict gets the `"split"` key twice** (one per branch). Same value, so no wrong output, but
   ruff F601 territory; keep one, with phase4's comment.
3. **Design decisions 19 and 20 exist twice** (below).

## Numbering

| Document | Clash | Proposal |
|---|---|---|
| `docs/design-decisions.md` | **19** = "Talking to Kestrel: whisper.cpp base.en" (voice/talk) and "Distillation data: open-weight teacher only" (phase4); **20** = "A 2-second Sending countdown" (voice/talk) and "Distill task set: disjoint from KestrelBench" (phase4). | Keep phase4's 19-25 (cited in code: `router.py`, `cli.py`, `stats.py` say decision 25; in `CLAUDE.md`; in `distill/COLLECTION.md`, `EXPORT.md`, `README.md`, `TRAINING.md`; written 2026-10-18). Renumber voice's **19 -> 26** and **20 -> 27**, and the two places that cite "design decision 20": `docs/known-issues.md` (the spoken-cancel issue) and `docs/voice-wake.md` (the send countdown). |
| `docs/known-issues.md` | None. The branches already split the range: tiers-v2 #23, 24, 37, 43-45; phase4 #30-36, 38-42; console #46, voice #47-50. | Keep. **25-29 are used only on `phase3/memory`** (not merged): leave them reserved. The next free number after the merge is 51. |
| `evals/CHANGELOG.md` | None (different sections, no version reused: suite 1.1, safety s1.0, distill set d1.x). | Keep; order: suite versions, then safety split, then agent changes, then check types, then proposals. |
| `CLAUDE.md` | No duplicate heading, but uneven structure: Phases 2 and 3 are `###` under Progress, Phase 4 is a top-level `## Phase 4`, and the voice work (Phase 5) has no section at all (2 lines mention voice). | Move Phase 4 under Progress as `### Phase 4`; add `### Phase 5: Voice` from the voice branches' docs. |

## Checks on the merged result

Run in the clone after the conflict resolution, with CI's own commands (`uv sync --locked`, no extras).

| Check | Result | Cause |
|---|---|---|
| `ruff check .` | 1 error | E501 in my resolution of `cli.py` (one long line). A merge artifact: wrap it. |
| `ruff format --check .` | 2 files | `cli.py`, `scripts/check_results.py`: formatting of the resolved hunks. Run `ruff format`. |
| `mypy` | 2 errors | `src/kestrel/wake/transcribe.py`: `pywhispercpp.model` and `numpy` not found. **Pre-existing on `voice/talk`** (same 2 errors on its tip alone): neither is in the default dependencies nor in the mypy `ignore_missing_imports` list, so CI (no `wake` extra) fails. |
| `pytest` (first run) | 49 failed, 824 passed | 44 from silent defect 1 (duplicate `--split`), plus the 5 below. |
| `pytest` (after fixing defect 1) | **5 failed**, 868 passed, 7 skipped, 4 xfailed | See below. |
| console `typecheck`, `lint` | pass | |
| console `test` | 65/65 pass | |
| console `build` | pass | |

The 5 remaining test failures:

1. **3 x `tests/test_router.py`** (a rejected approval never escalates; an approved action is history for the
   teacher; the gate applies to the teacher's calls): phase4's fake approver has `review(tool, args, preview, *,
   allow_session=False)`, and tiers-v2's gate now calls it with `notice=` (the card's reasons), so the agent
   reports a TypeError. This is **phase4 known issue #42** coming due (the router was tested only with the old
   gate). Fix: the fake approver accepts `notice=None`; then add the tiers-v2 cases #42 asks for (taint across an
   escalation).
2. **1 x `tests/test_distill_taskset.py::test_a_main_run_is_unchanged_but_for_the_split_key`**: phase4 asserts a
   main-suite run's meta gains only `split`; tiers-v2 adds `split_version` (= the suite version for main) and the
   safety fields (`user`, `kinds`, `answer_policy`, `users`, all null for main). The two branches assert different
   things about the same meta. Fix: re-base phase4's "main run unchanged" check on tiers-v2's main meta (allow those
   keys, still refuse `task_dir`/`workspace_dir` on main).
3. **1 x `tests/test_talk.py::test_the_hint_is_passed_to_whisper_cpp_and_can_be_turned_off`**: `numpy` missing.
   **Pre-existing on `voice/talk`** (fails on its tip alone), same root as the mypy errors. Fix on `voice/talk`:
   make the test skip without the `wake` extra (or stub numpy), and add `pywhispercpp.*` and `numpy` to the mypy
   override, or install the `wake` extra in CI.

## Agent fingerprint

Computed offline with `agent_fingerprint` at each branch tip and on the merged tree (groq / openai/gpt-oss-120b;
routed with a placeholder lineage file).

| Tree | Main-suite agent | Safety-split agent | Routed |
|---|---|---|---|
| `main` `880d601` | `cc5c16377662` | `25719febaed9` | - |
| `phase3/tiers-v2` | `35fac5ba6277` | `bb65063bad46` | - |
| `console/hud-theme`, `voice/wake-word`, `voice/talk` | `35fac5ba6277` | `bb65063bad46` | - |
| `phase4/trace-dataset` | `cc5c16377662` | - | `8cb421d99d52` (with a `router` entry) |
| **merged** | **`35fac5ba6277`** | **`bb65063bad46`** | `e68a0d0cb7bc` (with a `router` entry) |

As expected: **only tiers-v2.1 changes the main-suite agent** (capabilities hashed, approval rules `tiers-v2.1`);
the system prompt (`36d57c7e8a82`) and the model-facing tools are the same everywhere. The console and voice
branches change nothing the fingerprint covers. Phase4 adds the `router` entry only when the provider is
`routed`; merged-routed differs from phase4-routed only because it inherits tiers-v2's capabilities and rules.
The merged main agent equals the one the running main-suite measurement records (`35fac5ba6277`).

## Recommended real merge plan

Order follows the branch graph: the console and voice branches are built on tiers-v2, phase4 comes last because it
is the only one that meets the Phase 3 code for the first time.

| PR | Branch | Before opening it | CI subset on Groq? |
|---|---|---|---|
| 1 | `phase3/tiers-v2` -> `main` | The Phase 3 gate is decided: rule C's verdict from the main-suite measurement now running, and the owner's call on rule A (part 1 not met as written). | **Yes** (24 files under `src/kestrel/`). |
| 2 | `console/hud-theme` -> `main` | Rebase on `main` after PR 1 (drops the tiers-v2 commits it carries). | No (console only). |
| 3+4 | `voice/wake-word` + `voice/talk` -> `main`, **as one PR** | Fix the mypy errors and the numpy test on `voice/talk` first (they fail CI today). Renumber its design decisions 19, 20 -> 26, 27. One PR instead of two saves one CI subset run. | **Yes** (`src/kestrel/__init__.py`, `tracing.py`, `wake/`). |
| 5 | `phase4/trace-dataset` -> `main` | Rebase on `main` after 1-4; resolve as above (the `cli.py` port, one `--split`, one `"split"` meta key); fix the 3 router tests (#42) and the main-meta test; renumber nothing (it keeps 19-25); `CLAUDE.md` restructure. | **Yes** (`agent.py`, `bench/`, `router.py`, ...). |

**Groq cost and timing.** Each triggered CI subset run costs about 28k billable tokens (PR #11, run
37504156469: 27.7k), so PRs 1, 3+4 and 5 are about 85k, and each needs the owner's OK (CLAUDE.md). They share the
Groq organisation's 200k tokens per day with any measurement: open them **after the main-suite measurement
finishes**, and not on a day it runs. A CI subset run on a day that is already near the limit can stall the same
way the measurement did on 2026-10-24/25.

**Not part of this plan:** the other Phase 3 branches listed above; decide them separately (most of
`phase3/memory-safety` is already in `main`).
