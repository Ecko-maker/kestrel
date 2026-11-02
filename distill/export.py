"""Export the trained student to Ollama: merge, convert to GGUF, quantize, create, check.

    python distill/export.py --adapter RUN/adapter --out EXPORT --name kestrel-student --create --verify
    python distill/export.py --smoke --adapter RUN/adapter --out EXPORT --name kestrel-smoke --create --verify
    python distill/export.py --base --out EXPORT-BASE --name kestrel-base --create --verify   # the untrained base
    python distill/export.py --gguf EXPORT/model-q4_K_M.gguf --name kestrel-student --create --verify   # serve again

1. Merge: the base model (the pinned revision from train_config.toml, or RUN/base for a --smoke run) plus
   the adapter, merged in float16 on CPU (peft merge_and_unload), saved as safetensors (EXPORT/merged).
2. GGUF: llama.cpp's convert_hf_to_gguf.py (float16), then llama-quantize (default Q4_K_M), both from the
   pinned llama.cpp build LLAMA_CPP_BUILD. Ollama 0.40's own safetensors import needs its MLX engine,
   which isn't available on Windows, so the student goes in as GGUF (run by Ollama's llama.cpp backend).
3. Modelfile (EXPORT/Modelfile): FROM the GGUF, train_format.OLLAMA_TEMPLATE (byte-for-byte the format the
   student was trained on, checked by scripts/template_parity.py), Qwen's stop tokens and sampling
   defaults, and num_ctx (Ollama drops the oldest messages of longer prompts).
4. --create: `ollama create NAME -f Modelfile`.
5. --verify, first the tool schemas: the student's lineage.json records a hash of the exact tool schemas in
   its training text (from the code that built the dataset); they must equal what Kestrel's agent sends now
   (its tool registry, after Ollama's dropping of unknown keys, known issue #74). A mismatch fails and lists
   the tools that differ. MCP tools (kestrel.mcp.json) aren't in the registry until Kestrel starts them and
   weren't in training: serve the student with KESTREL_MCP=off.
   Then every fixed conversation of distill/parity/cases.json goes to the created model through
   Ollama's OpenAI-compatible endpoint (the way Kestrel's Ollama provider calls it, 1 token), and
   Ollama's prompt token count must equal the training tokenizer's count for train_format.render(). That
   checks the running server: a wrong TEMPLATE, a missing num_ctx or extra tokens all change the count.

--gguf skips steps 1-2 for a GGUF that was already built (another machine, or after `ollama rm`): it
writes the same Modelfile next to it, then --create and --verify as above. The tokenizer for --verify is
EXPORT/merged (--tokenizer to point elsewhere); the lineage is --lineage (required with --verify, since a
GGUF alone doesn't say which tool schemas it was trained on).

--base exports the untrained base model through the same GGUF path and the same Modelfile (only the
GGUF differs), so a base-vs-student comparison is like for like: never compare against Ollama's packaged
template (known issue #73).

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


def base_source(cfg: dict[str, Any], adapter: Path | None, smoke: bool) -> Path:
    """A local folder with the base model's safetensors and tokenizer, for --base: RUN/base of a --smoke
    run, or the pinned revision from the Hugging Face cache (downloaded the first time: ~8 GB)."""
    if smoke:
        if adapter is None:
            raise SystemExit("--smoke --base needs --adapter RUN/adapter (the base is RUN/base)")
        return adapter.parent / "base"
    from huggingface_hub import snapshot_download

    base = cfg["base_model"]
    return Path(snapshot_download(base["id"], revision=base["revision"]))


def served_tool_schemas() -> list[dict[str, Any]]:
    """The tool schemas Kestrel's agent sends (Agent: registry.schemas()), as built without MCP."""
    from kestrel.tools import registry  # this script runs where Kestrel is installed (not on Kaggle)

    return registry.schemas()


def check_tools(lineage: dict[str, Any], served: list[dict[str, Any]]) -> list[str]:
    """Differences between the tool schemas in the training text and the ones served now."""
    fmt = load_train_format()
    trained = lineage.get("tool_schemas")
    if not trained:
        return ["lineage.json has no tool_schemas: retrain with the current train.py"]
    return fmt.compare_tools(trained, fmt.tools_fingerprint(served))


def mcp_servers(path: Path = HERE.parent / "kestrel.mcp.json") -> list[str]:
    if not path.is_file():
        return []
    return sorted((json.loads(path.read_text(encoding="utf-8")).get("mcpServers") or {}).keys())


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


def verify(name: str, tokenizer_dir: Path, host: str, lineage: Path | None) -> int:
    from transformers import AutoTokenizer

    fmt = load_train_format()
    if lineage is not None:
        if not lineage.is_file():
            print(f"TOOL SCHEMAS: no lineage.json at {lineage}: can't check what the student was trained with")
            return 1
        problems = check_tools(json.loads(lineage.read_text(encoding="utf-8")), served_tool_schemas())
        for line in problems:
            print(f"TOOL SCHEMA MISMATCH {line}")
        if problems:
            print("The student would be served tool schemas it wasn't trained on. Retrain, or serve the old schemas.")
            return 1
        print("Tool schemas: identical to the training text.")
        if servers := mcp_servers():
            print(
                f"Note: with MCP on, Kestrel also sends the tools of {servers}, never seen in training: KESTREL_MCP=off"
            )
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
    parser.add_argument("--adapter", type=Path, help="the trained adapter folder (RUN/adapter)")
    parser.add_argument("--base", action="store_true", help="export the untrained base, same template (comparisons)")
    parser.add_argument("--lineage", type=Path, help="the run's lineage.json (default: next to the adapter folder)")
    parser.add_argument("--out", type=Path, help="export folder (merged/, GGUF, Modelfile); with --gguf, its folder")
    parser.add_argument("--gguf", type=Path, help="an already-built GGUF: write its Modelfile, then --create/--verify")
    parser.add_argument("--tokenizer", type=Path, help="with --gguf: the tokenizer folder (default <gguf dir>/merged)")
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
    if args.gguf is not None:
        if args.adapter is not None or args.base:
            parser.error("--gguf serves an existing GGUF: leave out --adapter and --base")
        if not args.gguf.is_file():
            parser.error(f"no GGUF at {args.gguf}")
        if args.out is not None and args.out.resolve() != args.gguf.parent.resolve():
            parser.error("--gguf writes the Modelfile next to the GGUF: leave out --out")
        if args.verify and args.lineage is None:
            parser.error("--gguf --verify needs --lineage (the training run's lineage.json)")
        args.out, gguf = args.gguf.parent, args.gguf
        merged = args.tokenizer or args.out / "merged"
        if args.verify and not merged.is_dir():
            parser.error(f"no tokenizer folder at {merged}: give --tokenizer")
    else:
        if args.out is None:
            parser.error("--out is required")
        if not args.base and args.adapter is None:
            parser.error("--adapter is required (or --base for the untrained base model)")
        args.out.mkdir(parents=True, exist_ok=True)
        merged = (
            base_source(cfg, args.adapter, args.smoke) if args.base else merge(args.adapter, args.out, cfg, args.smoke)
        )
        gguf = to_gguf(merged, args.out, args.llama_cpp, args.quantize)
    template = load_train_format().OLLAMA_TEMPLATE
    (args.out / "Modelfile").write_text(modelfile(template, gguf.name), encoding="utf-8", newline="\n")
    print(f"GGUF: {gguf}\nModelfile: {args.out / 'Modelfile'}")
    if args.create:
        run(["ollama", "create", args.name, "-f", str(args.out / "Modelfile")], cwd=args.out)
    if args.verify:
        lineage = None if args.base else (args.lineage or args.adapter.parent / "lineage.json")
        return verify(args.name, merged, args.host, lineage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
