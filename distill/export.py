"""Export the trained student to Ollama: merge, convert to GGUF, quantize, create, check.

    python distill/export.py --adapter RUN/adapter --out EXPORT --name kestrel-student --create --verify
    python distill/export.py --smoke --adapter RUN/adapter --out EXPORT --name kestrel-smoke --create --verify

1. Merge: the base model (the pinned revision from train_config.toml, or RUN/base for a --smoke run) plus
   the adapter, merged in float16 on CPU (peft merge_and_unload), saved as safetensors (EXPORT/merged).
2. GGUF: llama.cpp's convert_hf_to_gguf.py (float16), then llama-quantize (default Q4_K_M), both from the
   pinned llama.cpp build LLAMA_CPP_BUILD. Ollama 0.40's own safetensors import needs its MLX engine,
   which isn't available on Windows, so the student goes in as GGUF (run by Ollama's llama.cpp backend).
3. Modelfile (EXPORT/Modelfile): FROM the GGUF, train_format.OLLAMA_TEMPLATE (byte-for-byte the format the
   student was trained on, checked by scripts/template_parity.py), Qwen's stop tokens and sampling
   defaults, and num_ctx (Ollama drops the oldest messages of longer prompts).
4. --create: `ollama create NAME -f Modelfile`.
5. --verify: every fixed conversation of distill/parity/cases.json goes to the created model through
   Ollama's OpenAI-compatible endpoint (the way Kestrel's Ollama provider calls it, 1 token), and
   Ollama's prompt token count must equal the training tokenizer's count for train_format.render(). That
   checks the running server: a wrong TEMPLATE, a missing num_ctx or extra tokens all change the count.

Needs the optional extras `train` and `export`, the pinned llama.cpp build (distill/EXPORT.md says where
to get it) and Ollama. No model calls beyond the local Ollama server.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
LLAMA_CPP_BUILD = "b11712"  # ggml-org/llama.cpp release tag (2026-10-17): converter, gguf-py 0.19.0, llama-quantize
NUM_CTX = 16384  # Kestrel's history budget (KESTREL_MAX_CONTEXT_TOKENS, default 16000) fits
# Qwen3-4B-Instruct-2507's defaults, as Ollama's own package for it sets them.
SAMPLING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "repeat_penalty": 1}
STOPS = ("<|im_start|>", "<|im_end|>")


def load_train_format() -> ModuleType:
    for candidate in (HERE / "train_format.py", HERE.parent / "src" / "kestrel" / "distill" / "train_format.py"):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("train_format", candidate)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules["train_format"] = module
            spec.loader.exec_module(module)
            return module
    raise FileNotFoundError("train_format.py not found")


def default_llama_cpp() -> Path:
    if env := os.environ.get("KESTREL_LLAMA_CPP"):
        return Path(env)
    return Path(os.environ.get("LOCALAPPDATA", "")) / "kestrel-tools" / f"llama.cpp-{LLAMA_CPP_BUILD}"


def modelfile(template: str, gguf_name: str, num_ctx: int = NUM_CTX) -> str:
    if '"""' in template:
        raise ValueError("the template contains triple quotes, which a Modelfile can't hold")
    lines = [f"FROM ./{gguf_name}", f'TEMPLATE """{template}"""']
    lines += [f"PARAMETER stop {s}" for s in STOPS]
    lines += [f"PARAMETER num_ctx {num_ctx}"] + [f"PARAMETER {k} {v}" for k, v in SAMPLING.items()]
    lines += ['LICENSE """Apache-2.0 (base model Qwen/Qwen3-4B-Instruct-2507; teacher data from gpt-oss-120b)"""']
    return "\n".join(lines) + "\n"


def merge(adapter: Path, out: Path, cfg: dict[str, Any], smoke: bool) -> Path:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    base = cfg["base_model"]
    if smoke:
        source: str | Path = adapter.parent / "base"
        kwargs: dict[str, Any] = {}
    else:
        source, kwargs = base["id"], {"revision": base["revision"]}
    model = AutoModelForCausalLM.from_pretrained(source, dtype=torch.float16, **kwargs)
    merged = PeftModel.from_pretrained(model, adapter).merge_and_unload()
    target = out / "merged"
    merged.save_pretrained(target, safe_serialization=True)
    AutoTokenizer.from_pretrained(adapter).save_pretrained(target)
    return target


def run(cmd: list[str], **kwargs: Any) -> None:
    print("$ " + " ".join(cmd))
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)
    tail = (done.stdout + done.stderr).strip().splitlines()[-6:]
    print("\n".join(f"  {line}" for line in tail))
    if done.returncode:
        raise SystemExit(f"failed ({done.returncode}): {cmd[0]}")


def to_gguf(merged: Path, out: Path, llama_cpp: Path, quantize: str) -> Path:
    src = llama_cpp / f"llama.cpp-{LLAMA_CPP_BUILD}"
    converter, quantizer = src / "convert_hf_to_gguf.py", llama_cpp / "bin" / "llama-quantize.exe"
    if not converter.is_file() or not quantizer.is_file():
        raise SystemExit(f"llama.cpp {LLAMA_CPP_BUILD} not found in {llama_cpp} (distill/EXPORT.md, step 2)")
    f16 = out / "model-f16.gguf"
    env = os.environ | {"PYTHONPATH": str(src / "gguf-py")}  # the converter's own gguf-py, same commit
    run([sys.executable, str(converter), str(merged), "--outfile", str(f16), "--outtype", "f16"], env=env)
    quantized = out / f"model-{quantize}.gguf"
    run([str(quantizer), str(f16), str(quantized), quantize.upper()])
    return quantized


def prompt_tokens(name: str, messages: list[dict], tools: list[dict], host: str) -> int:
    """Ollama's prompt token count for a request sent the way Kestrel sends it (OpenAI endpoint)."""
    body: dict[str, Any] = {"model": name, "messages": messages, "max_tokens": 1, "temperature": 0}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(
        f"{host}/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return int(json.load(resp)["usage"]["prompt_tokens"])


def verify(name: str, tokenizer_dir: Path, host: str) -> int:
    from transformers import AutoTokenizer

    fmt = load_train_format()
    tok = AutoTokenizer.from_pretrained(tokenizer_dir)
    cases = json.loads((HERE / "parity" / "cases.json").read_text(encoding="utf-8"))
    bad = total = 0
    for case in cases:
        msgs, tools = case["messages"], case.get("tools") or []
        cuts = [i for i, m in enumerate(msgs) if m["role"] == "assistant"]
        cuts += [len(msgs)] if msgs[-1]["role"] != "assistant" else []
        for k in cuts:
            total += 1
            text, _ = fmt.render(msgs[:k], tools, generation_prompt=True)
            want = len(tok(text, add_special_tokens=False)["input_ids"])
            got = prompt_tokens(name, msgs[:k], tools, host)
            if got != want:
                bad += 1
                print(f"MISMATCH {case['name']}@{k}: Ollama {got} prompt tokens, training {want}")
    print(f"{total - bad}/{total} prompts: Ollama's prompt token count equals training's.")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--adapter", type=Path, required=True, help="the trained adapter folder (RUN/adapter)")
    parser.add_argument("--out", type=Path, required=True, help="export folder (merged/, GGUF, Modelfile)")
    parser.add_argument("--name", default="kestrel-student", help="Ollama model name (default kestrel-student)")
    parser.add_argument("--config", type=Path, default=HERE / "train_config.toml")
    parser.add_argument("--smoke", action="store_true", help="the adapter of a train.py --smoke run")
    parser.add_argument("--quantize", default="q4_K_M", help="llama-quantize type (default q4_K_M)")
    parser.add_argument("--llama-cpp", type=Path, default=default_llama_cpp(), help="pinned llama.cpp folder")
    parser.add_argument("--create", action="store_true", help="run `ollama create`")
    parser.add_argument("--verify", action="store_true", help="compare Ollama's prompt token counts with training's")
    parser.add_argument("--host", default="http://127.0.0.1:11434", help="Ollama server (default local)")
    args = parser.parse_args(argv)
    with args.config.open("rb") as f:
        cfg = tomllib.load(f)
    args.out.mkdir(parents=True, exist_ok=True)
    merged = merge(args.adapter, args.out, cfg, args.smoke)
    gguf = to_gguf(merged, args.out, args.llama_cpp, args.quantize)
    template = load_train_format().OLLAMA_TEMPLATE
    (args.out / "Modelfile").write_text(modelfile(template, gguf.name), encoding="utf-8", newline="\n")
    print(f"GGUF: {gguf}\nModelfile: {args.out / 'Modelfile'}")
    if args.create:
        run(["ollama", "create", args.name, "-f", str(args.out / "Modelfile")], cwd=args.out)
    if args.verify:
        return verify(args.name, merged, args.host)
    return 0


if __name__ == "__main__":
    sys.exit(main())
