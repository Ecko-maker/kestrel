# Option c on Kaggle: teacher selection and collection

Step by step for the owner. `distill/collect.ipynb` runs a teacher on Kaggle's free GPUs, so **no Groq quota
is used**. It has two modes:
- **compare**: known issue #72's pre-registered selection between gpt-oss:20b and Qwen3-30B-A3B-Instruct-2507.
- **collect**: the full distill pass with the winner.

The checklist around it is `distill/COLLECTION.md`. Laptop commands are PowerShell, from the development
folder.

**What it needs:**
- a Kaggle account with a verified phone number (for Internet), and the GPU quota: about 2 h for compare and
  up to ~6 h for collect;
- nothing else: no keys and no tokens. The notebook sets none, so no hosted model can be called.

**Downloads on Kaggle's side (not the laptop):**
- the models: gpt-oss:20b ~14 GB, Qwen3-30B-A3B Q4 ~19 GB;
- uv's Python 3.14 and Kestrel's dependencies, ~1 GB.

Only the outputs come to the laptop (a few MB).

## 1. Make the code snapshot (laptop)

The commit you snapshot is the commit the collection is recorded against (`manifest.json` > `commit`). It
must be clean and committed.

```powershell
git status                                   # clean
$c = git rev-parse HEAD
$up = "$env:TEMP\kaggle-upload"; New-Item -ItemType Directory -Force $up | Out-Null
git archive --format=zip --add-virtual-file="COMMIT:$c" -o "$up\kestrel-snapshot.zip" HEAD
Get-Item "$up\kestrel-snapshot.zip" | Select-Object Name, Length
```

`git archive` takes only tracked files, so `.env`, `logs/` and `data/` are never in it. The `COMMIT` file is
how the notebook knows the commit; it stops without it.

## 2. Upload it as a private dataset

1. kaggle.com > **Datasets** > **New Dataset** > upload `kestrel-snapshot.zip`.
2. Title `kestrel-snapshot`, visibility **Private** > Create.
3. Kaggle may unzip it; the notebook works with the zip or the unzipped tree.
4. For a later commit, open the dataset > **New Version** and upload the new zip.

## 3. Create the notebook and set it up

1. kaggle.com > **Code** > **New Notebook** > File > **Import Notebook** > upload `distill/collect.ipynb`.
2. Right panel > **Settings**:
   - **Accelerator: GPU T4 x2** (#72's fit rule is about these two 16 GB cards; the notebook stops on anything else);
   - **Internet: On**;
   - **Persistence**: no need.
3. **Input** > Add Input > **Your Datasets** > `kestrel-snapshot`.
4. No secrets. Don't add any API key: the notebook doesn't need one, and none must be on the machine.

## 4. Run compare mode (#72's teacher selection)

1. In the first code cell, keep `MODE = "compare"`.
2. Top right > **Save Version** > **Save & Run All (Commit)** > Save. Only a saved version keeps its outputs
   after the session ends; an interactive session loses them.
3. Wait, about 1-2 h. For each candidate, the notebook:
   - pulls it and serves it (Qwen with Kestrel's template, known issue #73; gpt-oss with its own);
   - checks it fits the two T4s, from Ollama's `/api/ps`: all of the model in GPU memory, nothing on the CPU;
   - runs #72's 20 distill tasks once, with `--judge none`.

   A candidate that doesn't fit isn't run: it is out by #72's rule, and the table says so.
4. The version's log ends with the selection table and `**Winner: ...**`, applying #72's rule:
   - higher check-pass rate;
   - within 2 tasks: fewer Harmony leaks, then more valid tool calls, then less time per task;
   - over 3 min per task is out.

## 5. Download the compare output and record it

1. Open the saved version > **Output** > download `kestrel-compare-<commit>.zip`.
2. On the laptop, unzip it into the runs folder's data area. Keep it out of git, like every results file, and
   check the target first so nothing is overwritten:

   ```powershell
   $dest = "..\kestrel-runs\evals\results\teacher-selection-<commit>"
   Test-Path $dest                            # must be False
   Expand-Archive "$HOME\Downloads\kestrel-compare-<commit>.zip" $dest
   Get-Content "$dest\compare\selection.md"
   ```

3. Record the table and the winner in known issue #72 (a docs PR; KestrelBench not run), as #72 says. Back
   the folder up with the other results (docs/STATUS.md, "Data outside git").

## 6. Switch the teacher to the winner (laptop, a branch + PR)

1. In `src/kestrel/distill/teachers.toml`, comment out option a and uncomment the winner's three lines:
   - `gpt-oss:20b`;
   - or `kestrel-teacher-qwen3-30b-a3b` for Qwen.
2. Commit. This is `distill/COLLECTION.md` step 0, and that file's read-back must print `ollama <model>`.
3. Make a new snapshot (step 1) from that commit and upload it as a new dataset version (step 2).

The collect mode stops if the snapshot's `teachers.toml` doesn't list exactly that teacher. Otherwise
`kestrel distill build` would drop every trace as `teacher`.

## 7. Run collect mode

1. In the notebook: `MODE = "collect"`, `TEACHER = "<the winner's name>"` (`"gpt-oss:20b"` or
   `"Qwen3-30B-A3B-Instruct-2507"`).
2. Make sure Input has the **new** snapshot version.
3. **Save & Run All (Commit)**.
4. It runs the 122 tasks in 8 shards (`--shard i/8`). Each shard writes its own results file, and all of them
   share one `logs/distill.db`. Up to ~6 h at #72's 3-min limit, which is within one session.

**If the version stops early** (Kaggle's session limit, or a crash):
1. Open the notebook > **Input** > Add Input > **Your Work** > this notebook > its last version's output.
2. **Save & Run All** again.
3. The first cell copies the earlier `out/` back: finished shards are skipped, and a partial one continues
   with `--resume`.

A shard cut off mid-run left no results file. It runs again from its start, and its earlier traces have no
verdict, so `distill build` drops them as `quality`.

At the end the notebook runs `kaggle_collect.py check-shards`. It must say "Shards OK" (every distill task
once, nothing skipped or errored).

## 8. Download the collect output and where the files go

1. Download `kestrel-collect-<commit>.zip` from the version's Output.
2. Move the runs folder to the collection commit and unzip the output into it. The folder must be at that
   commit: `distill build` checks the agent fingerprint against the code it runs with.

   ```powershell
   .\scripts\runs_folder.ps1 <commit>
   $tmp = "$env:TEMP\kestrel-collect"; Expand-Archive "$HOME\Downloads\kestrel-collect-<commit>.zip" $tmp
   Test-Path ..\kestrel-runs\logs\distill.db      # if True, stop: don't overwrite a live or earlier database
   Copy-Item "$tmp\collect\results\*.json" ..\kestrel-runs\evals\results\
   Copy-Item "$tmp\collect\manifest.json" ..\kestrel-runs\evals\results\distill-d1.2-collect-manifest.json
   Copy-Item "$tmp\collect\logs\distill.db" ..\kestrel-runs\logs\distill.db
   ```

3. Then `distill/COLLECTION.md` steps 6-7, with the shard files in place of the single results file:

   ```powershell
   Set-Location ..\kestrel-runs
   $shards = Get-ChildItem evals\results\distill-d1.2-*-shard-*-of-8.json | ForEach-Object FullName
   uv run --no-sync python distill\kaggle_collect.py check-shards $shards      # "Shards OK"
   $copy = "$env:TEMP\distill-d1.2-pass1.db"; Copy-Item logs\distill.db $copy
   $env:KESTREL_REQUEST_TIMEOUT = "600"   # the value Kaggle used (REQUEST_TIMEOUT_S); it is part of the fingerprint
   uv run --no-sync kestrel distill build --db $copy --results $shards --out data\distill\d1.2-pass1
   ```

   **Set `KESTREL_REQUEST_TIMEOUT` to 600 before building.** The notebook runs with a 600 s request timeout,
   and the agent fingerprint records it. Without the setting, the laptop's default (60 s) gives a different
   fingerprint and the build stops. The error names the input that differs ("request_timeout_s 600.0 in the
   file vs 60.0 now") and the setting to use.

There's no rate-limit audit for option c (no Groq). Read the manifest checks in `COLLECTION.md` step 7: the
provenance must match `collect/manifest.json` (teacher, digest, commit).

## What the manifest records

`out/<mode>/manifest.json`:
- `teacher`: the name, the Ollama tag, the served name, the template and the pulled **digest**;
- `ollama_version`;
- `gpu`: the **devices** with their memory, whether the model **fits**, and the loaded size against the GPU size;
- `commit`, the run's `agent_fingerprint`, the results files, and start and finish times.

In compare mode there is one manifest per candidate folder.

## Known limits

- **Not run yet.** The notebook is tested offline only (every cell parses; the selection, shard and manifest
  logic in `distill/kaggle_collect.py` has unit tests). The first real run is the first test of the Kaggle
  side: Ollama's install script, `/api/ps` on two T4s, and model tags that may have moved.
- **Sampling:**
  - Qwen is served with Kestrel's template, and so with the student's sampling settings (temperature 0.7,
    top_p 0.8, top_k 20, Qwen's own recommendation).
  - gpt-oss uses its package's defaults.

  Each candidate is served the way it is meant to be; the comparison doesn't equalise them.
- **Template parity:** Kestrel's template is checked byte for byte against Ollama's renderer for the 4B
  student (`scripts/template_parity.py`), not for the 30B. Both are Qwen3-2507 instruct models with the same
  chat format, but the 30B isn't separately verified.
