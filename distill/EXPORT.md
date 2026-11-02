# Exporting the student to Ollama

After training (`TRAINING.md`) you have `kestrel-adapter.zip`: a LoRA adapter for
`Qwen/Qwen3-4B-Instruct-2507` at revision `cdbee75f…`. This turns it into an Ollama model that Kestrel
runs with its existing Ollama provider, and proves the served prompts are exactly the trained ones.

```
adapter + base (fp16) --merge--> merged/ (safetensors) --convert--> model-f16.gguf --quantize--> model-q4_K_M.gguf
                                                                       Modelfile (TEMPLATE, num_ctx) --ollama create--> kestrel-student
```

Why GGUF and not Ollama's own safetensors import: in Ollama 0.40 that import runs through its MLX engine,
which isn't available on Windows ("MLX runtime is not available"), and its `--quantize` no longer offers
`q4_K_M`. GGUF is what Ollama's llama.cpp backend runs on every platform. Design decision 27.

## 1. Tools (once)

| Tool | Version | Licence | What for | Size |
|---|---|---|---|---|
| Ollama | 0.40.13 (installed; the parity renderer is built against 0.40.6, and `--verify` checks the running server) | MIT | serving | - |
| llama.cpp | build `b11712` (2026-10-17), pinned in `export.py` | MIT | `convert_hf_to_gguf.py` + its `gguf-py` 0.19.0, `llama-quantize` | 19.6 MB binaries + 40 MB source |
| Python extras | `uv sync --inexact --no-install-project --extra train --extra export` | Apache-2.0 / MIT | torch, transformers, peft, sentencepiece | ~1 GB (CPU torch) |
| Go (parity check only) | 1.27.2 | BSD | builds Ollama's own prompt renderer | 79 MB + ~460 MB module cache |

```powershell
$t = "$env:LOCALAPPDATA\kestrel-tools\llama.cpp-b11712"
New-Item -ItemType Directory -Force "$t\bin" | Out-Null
Invoke-WebRequest https://github.com/ggml-org/llama.cpp/releases/download/b11712/llama-b11712-bin-win-cpu-x64.zip -OutFile "$t\bin.zip"
Invoke-WebRequest https://codeload.github.com/ggml-org/llama.cpp/zip/refs/tags/b11712 -OutFile "$t\src.zip"
Expand-Archive "$t\bin.zip" "$t\bin"; Expand-Archive "$t\src.zip" $t; Remove-Item "$t\*.zip"
```

(Elsewhere, set `KESTREL_LLAMA_CPP` to the folder.) The Go setup is under "Template parity" below.

**Checked on this laptop 2026-11-02:** llama.cpp `b11712` (converter and `llama-quantize`), the parity renderer,
the `train` + `export` extras (torch 2.14.1, transformers 5.20.1, peft 0.21.2, sentencepiece 0.2.2) and Ollama
0.40.13 serving are all installed; nothing had to be added. 15.3 GB RAM (merging needs ~9 GB free: close other
apps first), 683 GB free on C:. The ~8 GB base model isn't downloaded yet (only its tokenizer): the first real
export fetches it (owner's OK for the download). The smoke chain below ran end to end on 0.40.13: tool schemas
identical, 20/20 prompt token counts for the student, `--gguf` and `--base`; template parity 20/20 against the
golden file and Ollama's renderer; the smoke models were deleted afterwards.

## 2. Export

The first real export downloads the base model at the pinned revision from Hugging Face: **about 8 GB**
(float16 safetensors), cached under `~\.cache\huggingface`. Merging needs about 9 GB of free RAM.

```powershell
Expand-Archive kestrel-adapter.zip -DestinationPath $env:TEMP\kestrel-adapter
uv run --no-sync python distill/export.py --adapter $env:TEMP\kestrel-adapter\adapter --out $env:TEMP\kestrel-export `
    --name kestrel-student --create --verify
```

It merges, converts, quantizes to Q4_K_M (~2.5 GB), writes the Modelfile, runs `ollama create`, then
checks **the tool schemas**: `lineage.json` (next to the adapter folder) records a fingerprint of the exact
tool schemas in the training text; they must equal what Kestrel's agent sends now, or the export fails and
names the tools that differ (`TOOL SCHEMA MISMATCH read_file: schema differs ...`). A tool changed since the
dataset was built means the student would be served schemas it never saw: retrain. Then it
**verifies the running server**: the 20 fixed conversations of `distill/parity/cases.json` go to
`kestrel-student` through Ollama's OpenAI-compatible endpoint (as Kestrel sends them, 1 token each), and
Ollama's prompt token count must equal the training tokenizer's count for the training text. Expected:
`20/20 prompts: Ollama's prompt token count equals training's.` A different count means the model is
not seeing what it was trained on: don't use it.

To register an already-built GGUF again (no merge or conversion): `export.py --gguf EXPORT\model-q4_K_M.gguf
--lineage RUN\lineage.json --name kestrel-student --create --verify` (`distill/EVAL.md`, step 1).

The Modelfile sets the trained template (`train_format.OLLAMA_TEMPLATE`), the stop tokens `<|im_start|>`
and `<|im_end|>`, Qwen's sampling defaults (temperature 0.7, top_p 0.8, top_k 20) and **`num_ctx
16384`**: without it Ollama silently drops the oldest messages of a longer prompt, and Kestrel's tool
schemas alone are ~970 tokens (measured with the student's tokenizer).

## 3. Use it in Kestrel (no code change)

The student runs as Kestrel's existing Ollama provider. In `.env` (names only; you set the values):

| Variable | Set to |
|---|---|
| `KESTREL_PROVIDERS` | `ollama` for the student alone, or a chain such as `ollama,groq` |
| `OLLAMA_MODEL` | the name you created, e.g. `kestrel-student` |
| `OLLAMA_BASE_URL` | only if Ollama isn't on the default local address |
| `KESTREL_MCP` | `off`: the student was trained without the MCP tools (`fetch`, `time`); with MCP on, Kestrel would send it tools it never saw |

Then `uv run --no-sync kestrel` (or `kestrel web`). Before trusting it, run KestrelBench on it like any
provider (`kestrel bench run --provider ollama`; no Groq quota for the agent, but the judge's would).

**Laptop RAM at Q4_K_M:** ~2.5 GB of weights + ~2.4 GB of KV cache at 16k context (36 layers x 8 KV heads
x 128 dims x 2 x 2 bytes = 147 KB per token) + Ollama's overhead: **about 5.5-6 GB**, so fine on 16 GB.
CPU-only generation speed: expect a few tokens per second; measure it.

## When `kestrel-adapter.zip` arrives (PowerShell, development folder)

```powershell
$zip = "$HOME\Downloads\kestrel-adapter.zip"
$a = "$env:TEMP\kestrel-adapter"; Remove-Item -Recurse -Force $a -ErrorAction SilentlyContinue
Expand-Archive $zip $a                                   # -> $a\adapter\ and $a\lineage.json
Get-FileHash $zip                                        # note it in the Phase 4 log

# 1. The lineage (KAGGLE.md step 10): every line must say OK.
#    The code commit is the COMMIT inside the uploaded code zip (kestrel-train-code-a4b33721d452.zip).
#    The 11-02 run used that code unchanged; only its notebook had an extra first cell, CUDA_VISIBLE_DEVICES=0
#    (TRAINING.md, run log), which lineage.json doesn't record: the notebook isn't part of the code zip.
uv run --no-sync python scripts/check_lineage.py "$a\lineage.json" --code-commit a4b33721d452c6aa83f0fc068bec1bb6db677296

# 2. Room to merge: ~9 GB free RAM, ~20 GB free disk under $env:TEMP.
"{0:N1} GB RAM free" -f ((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB)
"{0:N1} GB disk free" -f ((Get-PSDrive C).Free / 1GB)

# 3. The format, offline, then the export (first run: downloads the ~8 GB base, owner's OK).
uv run --no-sync python scripts/template_parity.py --golden          # 20/20
uv run --no-sync python distill/export.py --adapter "$a\adapter" --out "$env:TEMP\kestrel-export" `
    --name kestrel-student --create --verify
#    Must end with: "Tool schemas: identical to the training text." and
#    "20/20 prompts: Ollama's prompt token count equals training's."
ollama list | Select-String kestrel-student

# 4. Keep model-q4_K_M.gguf, Modelfile and merged\ (its tokenizer is --verify's for `--gguf`); the f16 GGUF can go.
Remove-Item "$env:TEMP\kestrel-export\model-f16.gguf"
```

Any `FAIL` in step 1, or a `MISMATCH` line in step 3: stop, don't use the model. Then `EVAL.md`, from step 1.4
(router settings) and the free runs.

## The base model, for comparisons

```powershell
uv run --no-sync python distill/export.py --base --out $env:TEMP\kestrel-base-export --name kestrel-base --create --verify
```

Same GGUF path and **byte-identical Modelfile** as the student (only the GGUF differs), so a base-vs-student
comparison is like for like. Uses the same ~8 GB base download as the student's merge. Never compare against
Ollama's packaged `qwen3:4b-instruct-2507` (known issue #73).

## Template parity: the student sees exactly what it was trained on

Ollama doesn't use the tokenizer's chat template: it renders prompts with its own Go templates. The
format is therefore defined once in `src/kestrel/distill/train_format.py` and written twice: `render()`
(Python, used for training) and `OLLAMA_TEMPLATE` (Go, the Modelfile). Tool schemas and arguments use
Go's JSON encoding on both sides, because that is all Ollama can write. Two checks keep them equal:

1. **Exact, against Ollama's own code:** `scripts/template_parity.py` renders every generation point of the
   fixed conversations with `render()` and with `distill/parity/main.go`, a short Go program that calls
   Ollama's `openai.FromChatRequest` and `template.Execute` (the server's code, at the version pinned in
   `distill/parity/go.mod`). They must be identical byte for byte; a difference prints the first differing
   line. The renderings are kept in `distill/parity/golden.json`, which a test compares with `render()`
   on every `pytest` run, CI included.
2. **At runtime, against the real server:** `export.py --verify` (above).

When to re-run: after changing `OLLAMA_TEMPLATE` or `render()`, or upgrading Ollama (bump `go.mod` to the
installed version first). Setup and run:

```powershell
$go = "$env:LOCALAPPDATA\kestrel-tools\go1.27.2"     # unzip go1.27.2.windows-amd64.zip (go.dev/dl) here
$env:GOPATH = "$env:LOCALAPPDATA\kestrel-tools\gopath"; $env:GOTOOLCHAIN = "local"; $env:CGO_ENABLED = "0"
Push-Location distill\parity
& "$go\go\bin\go.exe" build -o "$env:LOCALAPPDATA\kestrel-tools\ollama-render.exe" .
Pop-Location
uv run --no-sync python scripts/template_parity.py                 # 20/20 prompts identical (Ollama v0.40.6)
uv run --no-sync python scripts/template_parity.py --write-golden  # after an intended change
```

The module cache (~460 MB) can be removed with `go clean -modcache`; the built renderer keeps working.

What the check found in Ollama's own `qwen3:4b-instruct-2507` package (known issue #73): its template
prints each tool's schema as a Go struct dump (`{read_file Read a file. {object <nil> <nil> [path] ...}}`)
instead of JSON, adds a newline before `<tool_call>` and gives each tool result its own turn. The student
is served with Kestrel's template instead.

## Smoke test of the whole chain (no GPU, a few minutes)

```powershell
uv run --no-sync python distill/train.py --smoke --data distill/smoke --out $env:TEMP\smoke-run
uv run --no-sync python distill/export.py --smoke --adapter $env:TEMP\smoke-run\adapter --out $env:TEMP\smoke-export `
    --name kestrel-smoke --create --verify
uv run --no-sync python distill/export.py --smoke --base --adapter $env:TEMP\smoke-run\adapter --out $env:TEMP\smoke-base `
    --name kestrel-smoke-base --create --verify
ollama rm kestrel-smoke kestrel-smoke-base
```

A tiny random Qwen3 (2 layers, width 64) with the real tokenizer: the merge, GGUF conversion, Q4_K_M
quantization (15 of 25 tensors fall back to another type because the model is too narrow for 4-bit
k-quant blocks; a real 4B model isn't), `ollama create` and the token-count check all run. Verified
2026-10-18: 20/20; the same GGUF with Ollama's own qwen3 template gives 1/20.
