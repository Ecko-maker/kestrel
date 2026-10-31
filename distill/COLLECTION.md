# Collecting distill data: one pass (checklist)

> **Not started. The teacher decision (known issue #72, Groq Services Agreement §6.3(e)) is deferred to
> 2026-11-04:** option a only with Groq's written confirmation by then, otherwise option c with the
> teacher chosen by #72's pre-registered selection. This checklist works for either answer; the decision
> changes one setting (step 0).
> Nothing here has been run on real teacher data.

| | Option a | Option c |
|---|---|---|
| Teacher | `groq` + `openai/gpt-oss-120b` | `ollama` + `gpt-oss:20b`, served on a Kaggle GPU |
| When | #72 confirms reading (a) | #72's answer is no (design decision 22's fallback) |
| Cost | ~253k billable Groq tokens (range ~242k-284k), 2 quota days; the dry run says ~313k (see step 3) | no Groq; Kaggle GPU time (estimate 1-3 h, not measured) and a ~14 GB model pull on Kaggle's disk |
| Owner's OK | an approval line in docs/STATUS.md's Groq log, merged to `main` first | the owner's OK for the Kaggle GPU hours and the model download |
| Where it runs | `..\kestrel-runs` (a standalone PowerShell window) | a Kaggle notebook (what it needs: "Option c notebook" below; not built yet) |

The order after collection is fixed and the same for both: **manifest provenance check, leak filter,
overlap check against KestrelBench, then the train/val split** (step 7; `kestrel distill build` runs its
filters in that order, and the manifest counts each drop).

All commands are PowerShell, from the development folder unless the step says otherwise.

## 0. Choose the teacher: the one setting

- [ ] In `src/kestrel/distill/teachers.toml`, keep exactly one `[[teacher]]` entry: option a's (active
  now) or option c's (written there, commented out). Commit the change.
- [ ] Read it back; every command below uses `$provider` and `$model`:

```powershell
$t = uv run --no-sync python -c "from kestrel.distill.dataset import load_teachers as l; (p, m), = l(); print(p, m)"
$provider, $model = $t.Split(" ")
```

`kestrel distill build` keeps only traces whose every model step is that (provider, model), so a run
made with any other teacher is dropped as `teacher`, never trained on.

## 1. Check the commit

- [ ] `git switch phase4/trace-dataset` (or `main` after the merge); `git status` clean.
- [ ] `uv run --no-sync python scripts/distill_overlap.py` says "Overlap check passed." (0 fail).
- [ ] `uv run --no-sync pytest` green.
- [ ] Agent fingerprint unchanged since the last measured run (`35fac5ba6277` for groq + gpt-oss-120b):

```powershell
uv run --no-sync python -c "from kestrel.bench.runner import agent_fingerprint as f; print(f('groq', 'openai/gpt-oss-120b')['sha'])"
$commit = git rev-parse HEAD
```

## 2. Nothing else is running

- [ ] Option a: no Groq consumer (no bench process, no KestrelBench CI job running, no open PR about to
  run one), and 200,000 - billable use in the last 24 h - 15,000 >= the day's `--token-budget`.

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "kestrel(\.exe)?\s+bench\s+(run|rejudge)" } |
    Select-Object ProcessId, CommandLine
gh run list --status in_progress
```

- [ ] Option c: no other notebook using the Kaggle GPU quota you need this week.

## 3. Dry run (no calls)

```powershell
uv run --no-sync kestrel bench run --split distill --judge none --dry-run --provider $provider --model $model
```

- [ ] It lists 122 tasks from `distill/tasks` (version d1.2).
- [ ] Record its estimate. On 2026-10-30 (groq): **~417,100 tokens, ~312,825 billable, ~395 requests**,
  "needs more than a day". That is 122 x 3,300 raw tokens + 29 x 500 judge tokens, times the
  estimator's billable share 0.75; it still counts the judge tokens under `--judge none` (~11k billable)
  and has no measured distill tasks, so it is the high side. The measured-based figure is ~253k
  (`README.md`, "Cost"). Plan the budget for up to ~313k: 2 quota days either way.

## 4. Option a: collect on Groq

- [ ] Its approval line is in docs/STATUS.md's Groq log on `main`, for up to ~320k over 2 days.
- [ ] Move the runs folder to this commit and check `.env` is there (never copy it by script):

```powershell
.\scripts\runs_folder.ps1 $commit
Test-Path ..\kestrel-runs\.env                  # must be True; copy .env in by hand if not
```

- [ ] In a standalone PowerShell window in `..\kestrel-runs` (Start menu, not VS Code; laptop on mains
  power and awake), day 1:

```powershell
git rev-parse HEAD                              # the same $commit
uv run --no-sync kestrel bench run --split distill --judge none --provider $provider --model $model `
    --token-budget 180000 --wait-for-quota --out evals\results\distill-d1.2-pass1.json
```

- [ ] Day 2 (and later, if needed): wait until the budget has freed, check step 2 again, then resume.
  Repeat until 0 skipped and 0 errored:

```powershell
uv run --no-sync kestrel bench run --split distill --judge none --provider $provider --model $model `
    --token-budget 180000 --wait-for-quota --resume evals\results\distill-d1.2-pass1.json
```

`--judge none`: the teacher would grade itself (#69); check-only tasks are graded in full. Traces go to
`logs\distill.db` in the runs folder.

## 5. Option c: collect on Kaggle

- [ ] Build and test the notebook first (below), with the owner's OK for the GPU hours.
- [ ] Run it at `$commit`; download its outputs (`distill-d1.2-pass1.json`, `distill.db`,
  `provenance.json`) into `..\kestrel-runs\evals\results\` and `..\kestrel-runs\logs\` (the runs folder
  moved to `$commit` as in step 4, `.env` not needed).

### Option c notebook: what it needs (not built yet)

1. **Accelerator:** GPU T4 x2 (or P100). gpt-oss-20b in Ollama's MXFP4 package is ~14 GB; one 16 GB T4
   is tight with a 16k context, so let Ollama split it over both T4s. Internet on (phone-verified
   account): for the Ollama install, the model pull and the 7 web tasks (`09_web.yaml`).
2. **Inputs:** a Kaggle dataset holding `git archive $commit` of this repo (code, `distill/tasks`,
   `distill/workspace`, `evals/kestrelbench/` for the build's checks), nothing else: no `.env`, no keys.
3. **Install:** Python 3.14 + `uv` at CI's pins; `uv sync --locked` (no `train` extra); Ollama at a
   pinned version (the laptop has 0.40.13), started with `ollama serve` in the background, then
   `ollama pull gpt-oss:20b` (~14 GB, on Kaggle's disk); record the model digest (`ollama show`).
4. **Environment:** `KESTREL_MCP=off`, `OLLAMA_BASE_URL=http://localhost:11434/v1`,
   `KESTREL_REQUEST_TIMEOUT=300`, no provider keys set at all (so no hosted model can be called).
5. **Run:** `kestrel bench run --split distill --judge none --provider ollama --model gpt-oss:20b
   --stop-after-errors 0 --out distill-d1.2-pass1.json` from the repo root, so traces land in
   `logs/distill.db`. No `--token-budget` or `--wait-for-quota` (no quota); `--resume` if the session
   ends early (Kaggle's 12 h limit).
6. **Stops, like train.ipynb's:** no GPU, no internet, or a version off its pin (Ollama, Python, `uv`,
   the commit) stops the notebook before the run.
7. **Outputs:** the results file, a copy of `logs/distill.db` (closed after the run), and
   `provenance.json`: commit, Ollama version, model name and digest, GPU, start and end times, the
   agent fingerprint the run recorded.
8. **Not on Kaggle:** `kestrel distill build` runs on the laptop (step 7), so the dataset is built with
   the same code and checks as option a.

## 6. Check the results

Still in `..\kestrel-runs`, after the run has finished (no process from step 2):

```powershell
uv run --no-sync python scripts\check_results.py evals\results\distill-d1.2-pass1.json
$copy = "$env:TEMP\distill-d1.2-pass1.db"
Copy-Item logs\distill.db $copy                 # work on a copy, never the live database
uv run --no-sync python scripts\audit_rate_limits.py evals\results\distill-d1.2-pass1.json --db $copy
```

- [ ] `check_results.py` says OK (all 122 distill tasks, suite d1.2, no judge).
- [ ] Option a: the audit shows no graded task touched by a rate limit (resolve any it lists).
- [ ] Option c: `provenance.json` names `$commit`, the pinned Ollama version and the model digest.

## 7. Build the dataset (provenance, leak filter, overlap, split)

Build it **in the runs folder, at the collection commit**:

```powershell
uv run --no-sync kestrel distill build --db $copy --results evals\results\distill-d1.2-pass1.json --out data\distill\d1.2-pass1
```

In this order, from the printed counts and `data\distill\d1.2-pass1\manifest.json`:

- [ ] **Manifest provenance:** `results` lists the file as `"source": "distill"`; the build didn't stop
  on an agent-fingerprint mismatch (it refuses a results file recorded with other agent code);
  `teachers` is exactly step 0's entry; `dropped.teacher` is 0 (else some step came from another model);
  `settings.bench_sources` shows the safety split at commit `6f58ee5...` as available; `git` is `$commit`.
- [ ] **Leak filter:** `dropped.harmony_leak_tool_name`, `_arguments` and `_text` (known issue #54).
  Expected small; if more than a few per cent, look at the traces before training.
- [ ] **Overlap against KestrelBench:** `dropped.contamination_run`, `contamination_prompt` and
  `near_duplicate` are 0 for distill traces (step 1's overlap check already passed on the task set).
- [ ] **Train/val split:** `stats.train`, `stats.val` and `stats.sessions` (split by session hash, salt
  2026, so no conversation is on both sides). Expected roughly 85-95 examples from the 93 check-only
  tasks; `rubric_task` drops the 29 rubric tasks.

`data\` is git-ignored: keep the manifest's numbers in the Phase 4 notes, not the data.
