# Training the student on Kaggle (QLoRA)

The student is **Qwen/Qwen3-4B-Instruct-2507** (Apache-2.0), pinned to revision `cdbee75f…` in
`train_config.toml`. Training adds a small LoRA adapter on top of the 4-bit base model; the base
weights never change. Libraries: transformers + peft + trl + bitsandbytes (the versions in `uv.lock`;
the notebook installs exactly those). Design decision 23.

**Before you start:** you need a dataset from one collection pass (`COLLECTION.md`), i.e. a folder
with `train.jsonl`, `val.jsonl` and `manifest.json` from `kestrel distill build`. And a Kaggle account
with a verified phone number (needed for GPUs and internet).

## 1. Prepare the two upload folders (on your laptop, PowerShell)

```powershell
$up = "$env:TEMP\kaggle-upload"
New-Item -ItemType Directory -Force "$up\data", "$up\code" | Out-Null
Copy-Item ..\kestrel-runs\data\distill\d1.2-pass1\train.jsonl, ..\kestrel-runs\data\distill\d1.2-pass1\val.jsonl, `
          ..\kestrel-runs\data\distill\d1.2-pass1\manifest.json "$up\data\"
Copy-Item distill\train.py, distill\train_config.toml, src\kestrel\distill\train_format.py "$up\code\"
Copy-Item distill\smoke\train.jsonl "$up\code\"     # the GPU smoke test's synthetic examples, flat
Get-FileHash "$up\data\manifest.json"     # note it: lineage.json will repeat it
```

The dataset holds teacher answers about the fixture workspace only (no secrets: `kestrel distill
build` drops any example with a canary, key or `.env` content). Upload it as **private** all the same.

## 2. Upload them as two private Kaggle datasets

kaggle.com > **Datasets** > **New Dataset**:

1. Drag in the files of `$up\data`, title **kestrel-distill-data**, visibility **Private**, Create.
2. Again with `$up\code`, title **kestrel-distill-code**, Private, Create.

Each folder's files go in flat, with no subfolders. Keep the two datasets apart: the code dataset's
`train.jsonl` is the synthetic smoke set, and the notebook never uses it as training data. Whatever
the slugs and the mount layout, the notebook's first cell finds both datasets under `/kaggle/input`
and prints the paths it chose.

## 3. Open the notebook

kaggle.com > **Code** > **New Notebook** > **File** > **Import Notebook** > `distill/train.ipynb`. Then in
the right-hand panel:

- **Input** > Add Input: both datasets.
- **Settings** > **Accelerator: GPU T4 x2** (the script uses one GPU). Prefer T4 over P100: bitsandbytes'
  4-bit kernels target newer GPUs, and the P100 may not be supported.
- **Settings** > **Internet: On** (pip and the model download).

**Tokens:** none are needed: the base model isn't gated. If Hugging Face rate-limits the download, create
a read token at huggingface.co > Settings > Access Tokens, then in Kaggle **Add-ons** > **Secrets** >
Add a secret named `HF_TOKEN` and tick it for this notebook. The notebook reads it from there; never
paste a token into a cell or a file.

## 4. Run the GPU smoke test first

Run the cells from the top: paths, GPU, install (it stops if the internet is off or a library isn't
at its pin), HF_TOKEN, then `train.py --gpu-smoke`. **Run All** is safe too: the full training cell
is skipped unless you set `RUN_TRAINING = True`. The smoke test takes a few minutes and spends almost
nothing, and it tells you whether a real run can work:

- prints Kaggle's preinstalled torch and CUDA, the GPU, and each library next to its pin in
  `train_config.toml` (`[pins]`, equal to `uv.lock`): `ok` or `MISMATCH`;
- loads the real base model at the pinned revision in 4-bit and trains it for 30 steps on a fixed batch of
  4 synthetic examples (the code dataset's `train.jsonl`): an overfitting test. **Passes only if** the loss is finite and falls by
  at least 50% (gradients flow, the masking leaves trainable tokens), every example has trainable tokens
  (printed per example), and afterwards a greedy generation on a synthetic tool prompt makes a tool call
  that `train_format` parses;
- writes `/kaggle/working/gpu-smoke/gpu_smoke.json` and ends with `GPU smoke test PASSED` or `FAILED`.

If it fails, the notebook stops there: look at the printed reason before spending GPU time on the real
run. A `MISMATCH` can't reach this step: the install cell stops first (torch is Kaggle's own and isn't
pinned).

**First pass, 2026-10-20: PASSED.** Loss 3.8575 -> 0.0001 in 30 steps, trainable tokens in every
example, and the greedy generation made a tool call that `train_format` parsed
(`{"name": "read_file", "arguments": {"path": "a.txt"}}`). Environment: Kaggle, Tesla T4, Python
3.13.15, torch 2.11.0+cu128, CUDA 12.8; base model download 8.04 GB. **All six pinned libraries ran at
their pins**; torch was Kaggle's preinstalled build (not pinned). From the run's own output:

| Library | Ran | Pin |
|---|---|---|
| torch | 2.11.0+cu128 | Kaggle's build, not pinned |
| transformers | 5.20.1 | 5.20.1 ok |
| peft | 0.21.2 | 0.21.2 ok |
| trl | 1.16.0 | 1.16.0 ok |
| datasets | 5.1.0 | 5.1.0 ok |
| accelerate | 1.15.0 | 1.15.0 ok |
| bitsandbytes | 0.50.2 | 0.50.2 ok |

From now on the install cell prints this table before any GPU work and stops on any difference.

## 5. Run the training

Attach **kestrel-distill-data**, set `RUN_TRAINING = True` in the full training cell, then **Run All**
for an interactive run, or **Save Version** > **Save & Run All (Commit)** to run in the background (it
keeps going if you close the tab; sessions stop after about 12 h). The cell checks that `train.jsonl`,
`val.jsonl` and `manifest.json` are all there before the model is loaded (`train.py` checks again).

What happens: pip installs the pinned versions, the script downloads the base model (~8 GB) once,
loads it in 4-bit, encodes the dataset (stopping with a clear error if an example is longer than
`max_length`: rebuild with a lower `--max-tokens`, never truncate), trains, evaluates val loss after
every epoch and stops early when it no longer improves, keeping the best epoch.

**Expected time** (an estimate; the first real run will tell): about 90 examples of ~2.5k tokens, 3-5
epochs at roughly 1-1.5k tokens/s on one T4 = **15-25 min of training, 30-45 min for the whole session**
with installs and the download. That is **under 1 GPU-hour** of Kaggle's ~30 free hours a week (check
the quota shown in your account; it varies).

If the log shows `loss is not finite` (float16 overflow on T4): lower `learning_rate` in
`train_config.toml`, re-upload the code dataset, and run again.

## 6. Download and check

**Output** tab > `kestrel-adapter.zip` > Download. It holds `adapter/` (the LoRA adapter and tokenizer)
and `lineage.json`. Check before using it:

```powershell
Expand-Archive kestrel-adapter.zip -DestinationPath kestrel-adapter
Get-Content kestrel-adapter\lineage.json | ConvertFrom-Json | Select-Object -ExpandProperty dataset
```

`manifest_sha256` must equal the hash from step 1; `traces_db_sha256`, `teachers` and `grading` come
from the manifest. `base_model.revision` must be the pinned revision, and `results.best_eval_loss` is
the val loss of the epoch that was kept.

Next: `EXPORT.md` (merge, GGUF, Ollama, and the check that Ollama serves the trained prompt format).

## Kaggle gotchas

- **Phone verification.** GPUs and the Internet switch need a verified phone number: kaggle.com > your
  picture > Settings > Phone verification. Without it the switch is greyed out.
- **Internet switch.** Settings > Internet: On. When it's off, pip fails with `Temporary failure in name
  resolution` and `from versions: none`; the install cell now stops before pip with a message saying so.
  Changing it restarts the session: check the accelerator and the inputs, then run from the top.
- **Mount path.** Kaggle now mounts datasets at `/kaggle/input/datasets/<user>/<slug>/` (older:
  `/kaggle/input/<slug>/`). The first cell searches `/kaggle/input` and prints what it found; no path
  needs editing.
- **Flat uploads.** Dragging in a folder can drop its subfolders, so every file sits at the top of its
  dataset (the smoke `train.jsonl` next to `train.py`). After adding files, create a **New Version** of
  the dataset and re-attach it.
- **Outputs.** `/kaggle/input` is read-only: everything is written under `/kaggle/working`, and
  `train.py` refuses an `--out` under `/kaggle/input` before loading anything.
- **Harmless warnings.** pip's dependency-conflict lines about Kaggle's own packages (e.g. `bigframes`):
  `train.py` doesn't use them. pip's "you may need to restart the kernel": `train.py` runs in a new
  process, which loads the newly installed versions. "No HF_TOKEN secret": the base model isn't gated.

## Local smoke test (no GPU)

Checks the whole script on your laptop in about a minute: a tiny randomly initialized Qwen3 model with
the real tokenizer (same chat template), CPU, no 4-bit, 4 steps, on the synthetic `distill/smoke/`
dataset (built by `kestrel distill build` from synthetic traces). It proves the pipeline runs and
writes the adapter and `lineage.json`, not that the model learns anything (its loss is ~ln(151,669)).

```powershell
uv sync --inexact --no-install-project --extra train      # torch etc. (CPU), only for training
uv run --no-sync python distill/train.py --smoke --data distill/smoke --out $env:TEMP\smoke-run
uv run --no-sync pytest tests/test_train_format.py        # the same run, plus the formatter tests
```

The first run downloads only the tokenizer files (a few MB) of the pinned revision.
