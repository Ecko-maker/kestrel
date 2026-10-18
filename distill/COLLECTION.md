# Collecting distill data: one pass

> **BLOCKED until the owner confirms (known issue #36).** The provisional reading of Groq's Services
> Agreement §6.3(e) is (a): a locally served student doesn't compete with GroqCloud. The owner has asked
> Groq for written confirmation (2026-10-18) and is waiting for the reply. Don't run anything below
> until the owner says so. Fallback if the answer is no: gpt-oss-20b served locally by Ollama as the
> teacher (design decision 19).

> **Prerequisite: these commands work only after Phase 3 has been merged into main and
> `phase4/trace-dataset` has been rebased onto it.** They use `scripts\runs_folder.ps1` and
> `kestrel bench run --wait-for-quota`, which come with Phase 3 and are not on this branch yet.
> Nothing here has been run.

Also required before starting:

- The owner's OK for the quota: about **253k billable Groq tokens, 2 days** (`README.md`, "Cost").
- No other eval running (the Phase 3 safety runs share the same Groq daily budget).
- The overlap check passes and the tests are green on the commit you collect from.

All commands are PowerShell, from the development folder unless the step says otherwise.

## 1. Check the commit

```powershell
git switch phase4/trace-dataset
git status                                      # clean
uv run --no-sync python scripts/distill_overlap.py   # "Overlap check passed."
uv run --no-sync pytest
$commit = git rev-parse HEAD
```

## 2. Make sure nothing else is running

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "kestrel(\.exe)?\s+bench\s+(run|rejudge)" } |
    Select-Object ProcessId, CommandLine
```

Expect no rows. If the Phase 3 safety run (or any other eval) is still going, wait until it has
finished and its last log line says so.

## 3. Move the runs folder to this commit

```powershell
.\scripts\runs_folder.ps1 $commit
Test-Path ..\kestrel-runs\.env                  # must be True; copy .env in by hand if not (never by script)
Set-Location ..\kestrel-runs
git rev-parse HEAD                              # the same $commit
```

## 4. Dry run (no calls)

```powershell
uv run --no-sync kestrel bench run --split distill --judge none --dry-run
```

It should list 122 tasks from `distill/tasks` (version d1.2). Its token estimate is too low (it uses the
old 0.32 billable share); expect ~253k billable, not ~133k.

## 5. Day 1

```powershell
uv run --no-sync kestrel bench run --split distill --judge none --token-budget 180000 --wait-for-quota `
    --out evals\results\distill-d1.2-pass1.json
```

- `--judge none`: no rubric grading now (the teacher would grade itself; see README "Grading the rubric
  tasks later"). Check-only tasks are graded in full.
- Traces go to `logs\distill.db` in the runs folder; the results file records `"split": "distill"`.
- The run stops starting new tasks at ~180k billable tokens; the rest are marked `skipped`.

## 6. Day 2 (and later days, if needed)

Wait until the budget has freed (a rolling ~24 h window), check step 2 again, then:

```powershell
uv run --no-sync kestrel bench run --split distill --judge none --token-budget 180000 --wait-for-quota `
    --resume evals\results\distill-d1.2-pass1.json
```

Repeat until the run reports 0 skipped and 0 errored.

## 7. Check the results

Still in `..\kestrel-runs`, after the run has finished (no process from step 2):

```powershell
uv run --no-sync python scripts\check_results.py evals\results\distill-d1.2-pass1.json
$copy = "$env:TEMP\distill-d1.2-pass1.db"
Copy-Item logs\distill.db $copy                 # work on a copy, never the live database
uv run --no-sync python scripts\audit_rate_limits.py evals\results\distill-d1.2-pass1.json --db $copy
```

`check_results.py` must say OK (all 122 distill tasks, suite d1.2, no judge). The audit must show no
graded task touched by a rate limit; resolve any it lists before building.

## 8. Build the dataset

Build it **in the runs folder, at the collection commit**. `kestrel distill build` checks every distill results
file's agent fingerprint against the current code and stops with an error naming the file and both
fingerprints if they differ (or if the file has none): traces collected with other agent code (system
prompt, tools, limits) would be trained under a prompt they never had.

```powershell
uv run --no-sync kestrel distill build --db $copy --results evals\results\distill-d1.2-pass1.json --out data\distill\d1.2-pass1
```

Read the printed counts and `data\distill\d1.2-pass1\manifest.json`:

- `results` must list the file as `"source": "distill"`.
- `settings.bench_sources` must show the safety split at commit `6f58ee5...` as available (after the
  merge it is also on disk).
- Expected: roughly 85-95 examples from the 93 check-only tasks (more for two-turn conversation tasks,
  fewer for failed ones); `rubric_task` drops the 29 rubric tasks; `contamination_*` and
  `near_duplicate` should be 0 for distill traces.

`data\` is git-ignored: keep the manifest's numbers in the Phase 4 notes, not the data.
