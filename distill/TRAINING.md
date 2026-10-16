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
Get-FileHash "$up\data\manifest.json"     # note it: lineage.json will repeat it
```

The dataset holds teacher answers about the fixture workspace only (no secrets: `kestrel distill
build` drops any example with a canary, key or `.env` content). Upload it as **private** all the same.

## 2. Upload them as two private Kaggle datasets

kaggle.com > **Datasets** > **New Dataset**:

1. Drag in the files of `$up\data`, title **kestrel-distill-data**, visibility **Private**, Create.
2. Again with `$up\code`, title **kestrel-distill-code**, Private, Create.

(If Kaggle gives them other slugs, change `DATA` / `CODE` in the notebook's first cell.)

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

## 4. Run

**Run All** for an interactive run, or **Save Version** > **Save & Run All (Commit)** to run in the
background (it keeps going if you close the tab; sessions stop after about 12 h).

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

## 5. Download and check

**Output** tab > `kestrel-adapter.zip` > Download. It holds `adapter/` (the LoRA adapter and tokenizer)
and `lineage.json`. Check before using it:

```powershell
Expand-Archive kestrel-adapter.zip -DestinationPath kestrel-adapter
Get-Content kestrel-adapter\lineage.json | ConvertFrom-Json | Select-Object -ExpandProperty dataset
```

`manifest_sha256` must equal the hash from step 1; `traces_db_sha256`, `teachers` and `grading` come
from the manifest. `base_model.revision` must be the pinned revision, and `results.best_eval_loss` is
the val loss of the epoch that was kept.

Serving the adapter with Ollama and routing to it are the next Phase 4 steps.

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
