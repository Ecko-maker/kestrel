"""QLoRA fine-tuning of the Kestrel student on a `kestrel distill build` dataset (Phase 4).

    python train.py --data DIR --out DIR [--config train_config.toml]   # Kaggle GPU: QLoRA
    python train.py --gpu-smoke --data smoke --out OUT                   # Kaggle GPU: run this first
    python train.py --smoke --data distill/smoke --out OUT               # CPU: tiny model, a few steps

--gpu-smoke checks the real setup before a real run: the pinned base model in 4-bit learns a fixed batch
of synthetic examples (an overfitting test: the loss must fall by half), every example has trainable
tokens, and afterwards a greedy generation makes a tool call train_format can parse. It also prints the
library versions next to the pins in the config.

DIR holds train.jsonl, val.jsonl and manifest.json. Settings come from the config file. Output:
OUT/adapter (the LoRA adapter and tokenizer), OUT/lineage.json (what was trained on what, with which
code), OUT/checkpoints (intermediate, deletable). Distill/TRAINING.md has the Kaggle steps.

Runs on Kaggle, where Kestrel can't be installed: Python >= 3.11 (tomllib, trl), no kestrel imports;
train_format.py is loaded from next to this file or from the repo.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import platform
import sys
import tomllib
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
LIBRARIES = ("torch", "transformers", "peft", "trl", "datasets", "accelerate", "bitsandbytes")


def load_train_format() -> ModuleType:
    """train_format.py from next to this file (Kaggle code dataset) or from the repo."""
    for candidate in (HERE / "train_format.py", HERE.parent / "src" / "kestrel" / "distill" / "train_format.py"):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("train_format", candidate)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules["train_format"] = module
            spec.loader.exec_module(module)
            return module
    raise FileNotFoundError("train_format.py not found next to train.py or in src/kestrel/distill/")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def versions() -> dict[str, Any]:
    found: dict[str, Any] = {"python": platform.python_version()}
    for name in LIBRARIES:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = None
    try:
        import torch

        found["cuda"] = torch.version.cuda
        found["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except ImportError:
        pass
    return found


def version_report(found: dict[str, Any], pins: dict[str, str]) -> tuple[list[str], list[str]]:
    """(lines to print, names whose installed version differs from the pin). torch isn't pinned: Kaggle's
    preinstalled CUDA build is used, and reported as such."""
    lines, mismatched = [], []
    for name in LIBRARIES:
        have, want = found.get(name), pins.get(name)
        if want is None:
            lines.append(f"  {name:<13}{have or 'missing':<14} (preinstalled, not pinned)")
            continue
        ok = have == want
        mismatched += [] if ok else [name]
        lines.append(f"  {name:<13}{have or 'missing':<14} pin {want:<10} {'ok' if ok else 'MISMATCH'}")
    lines.append(f"  python {found.get('python')}, CUDA {found.get('cuda')}, GPU {found.get('gpu')}")
    return lines, mismatched


def check_overfit(losses: list[float], min_drop: float) -> tuple[bool, str]:
    """An overfitting test passes if every loss is finite and the last is at most (1 - min_drop) times
    the first: gradients flow, and the labels leave something to learn."""
    if not losses or not all(math.isfinite(x) for x in losses):
        return False, f"non-finite loss: {losses}"
    first, last = losses[0], losses[-1]
    ok = last <= first * (1 - min_drop)
    return ok, f"loss {first:.4f} -> {last:.4f} ({1 - last / first:.0%} drop, need {min_drop:.0%})"


def check_trainable(counts: list[int]) -> tuple[bool, str]:
    return all(n > 0 for n in counts), f"trainable tokens per example: {counts}"


def first_tool_prompt(record: dict[str, Any]) -> tuple[list[dict], set[str]]:
    """The messages before the record's first assistant turn that calls a tool, and the tool names."""
    for i, m in enumerate(record["messages"]):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            names = {t["function"]["name"] for t in record.get("tools") or []}
            return record["messages"][:i], names
    raise SystemExit("the smoke dataset's first record has no tool call to test generation with")


def gpu_smoke(data: Path, out: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoTokenizer, set_seed

    fmt = load_train_format()
    s, base, lora_cfg = cfg["gpu_smoke"], cfg["base_model"], cfg["lora"]
    set_seed(cfg["seed"])
    if not torch.cuda.is_available():
        raise SystemExit("--gpu-smoke needs a GPU (Kaggle: Settings > Accelerator > GPU T4)")
    found = versions()
    lines, mismatched = version_report(found, cfg.get("pins") or {})
    print("Library versions:\n" + "\n".join(lines))

    tokenizer = AutoTokenizer.from_pretrained(base["id"], revision=base["revision"])
    records = fmt.load_jsonl(data / "train.jsonl")[: s["batch_examples"]]
    rows = [fmt.encode(tokenizer, r, cfg["data"]["max_length"]) for r in records]
    counts = [sum(label != fmt.IGNORE for label in r["labels"]) for r in rows]
    trainable_ok, trainable_msg = check_trainable(counts)
    print(trainable_msg)

    model = build_model(cfg, tokenizer, smoke=False)
    model = get_peft_model(
        model,
        LoraConfig(
            r=lora_cfg["r"],
            lora_alpha=lora_cfg["alpha"],
            lora_dropout=0.0,  # deterministic overfitting
            target_modules=lora_cfg["target_modules"],
            bias="none",
            task_type="CAUSAL_LM",
        ),
    )
    width = max(len(r["input_ids"]) for r in rows)
    pad = tokenizer.pad_token_id
    batch = {
        "input_ids": torch.tensor([r["input_ids"] + [pad] * (width - len(r["input_ids"])) for r in rows]),
        "attention_mask": torch.tensor([[1] * len(r["input_ids"]) + [0] * (width - len(r["input_ids"])) for r in rows]),
        "labels": torch.tensor([r["labels"] + [fmt.IGNORE] * (width - len(r["labels"])) for r in rows]),
    }
    batch = {k: v.to(model.device) for k, v in batch.items()}
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=s["learning_rate"])
    model.train()
    losses = []
    for step in range(s["steps"]):
        optimizer.zero_grad()
        loss = model(**batch).loss
        loss.backward()
        optimizer.step()
        losses.append(float(loss.detach()))
        print(f"  step {step + 1:>2}/{s['steps']}: loss {losses[-1]:.4f}")
    overfit_ok, overfit_msg = check_overfit(losses, s["min_loss_drop"])
    print(overfit_msg)

    model.eval()
    prompt_messages, tool_names = first_tool_prompt(records[0])
    prompt, _ = fmt.render(prompt_messages, records[0].get("tools") or [], generation_prompt=True)
    ids = tokenizer(prompt, add_special_tokens=False, return_tensors="pt").to(model.device)
    with torch.no_grad():
        generated = model.generate(**ids, max_new_tokens=s["max_new_tokens"], do_sample=False)
    text = tokenizer.decode(generated[0, ids["input_ids"].shape[1] :], skip_special_tokens=False)
    calls: list[dict[str, Any]] = []
    with contextlib.suppress(json.JSONDecodeError):  # a malformed call simply fails the check
        calls = fmt.parse_tool_calls(text)
    tool_ok = bool(calls) and all(c.get("name") in tool_names for c in calls)
    print(f"generation: {text[:300]!r}\nparsed tool calls: {calls}")

    report = {
        "passed": trainable_ok and overfit_ok and tool_ok,
        "checks": {"trainable_tokens": trainable_ok, "loss_drop": overfit_ok, "tool_call": tool_ok},
        "trainable_tokens": counts,
        "losses": losses,
        "generation": text,
        "tool_calls": calls,
        "libraries": found,
        "version_mismatches": mismatched,
        "base_model": base,
        "config": cfg["gpu_smoke"],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "gpu_smoke.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    verdict = "PASSED" if report["passed"] else "FAILED"
    print(
        f"GPU smoke test {verdict}: {report['checks']}"
        + (f"; versions differ from pins: {mismatched}" if mismatched else "")
    )
    return report


def build_model(cfg: dict[str, Any], tokenizer: Any, smoke: bool) -> Any:
    import torch
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig

    base = cfg["base_model"]
    if smoke:
        from transformers import Qwen3Config

        s = cfg["smoke"]
        config = Qwen3Config(
            vocab_size=len(tokenizer),
            hidden_size=s["hidden_size"],
            intermediate_size=s["intermediate_size"],
            num_hidden_layers=s["num_hidden_layers"],
            num_attention_heads=s["num_attention_heads"],
            num_key_value_heads=s["num_key_value_heads"],
            head_dim=s["head_dim"],
            max_position_embeddings=cfg["data"]["max_length"],
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
        return AutoModelForCausalLM.from_config(config, dtype=torch.float32)
    q = cfg["quantization"]
    bnb = BitsAndBytesConfig(
        load_in_4bit=q["load_in_4bit"],
        bnb_4bit_quant_type=q["quant_type"],
        bnb_4bit_use_double_quant=q["double_quant"],
        bnb_4bit_compute_dtype=getattr(torch, q["compute_dtype"]),
    )
    model = AutoModelForCausalLM.from_pretrained(
        base["id"],
        revision=base["revision"],
        quantization_config=bnb,
        dtype=getattr(torch, q["compute_dtype"]),
        device_map={"": 0},
    )
    from peft import prepare_model_for_kbit_training

    return prepare_model_for_kbit_training(model, use_gradient_checkpointing=cfg["training"]["gradient_checkpointing"])


def run(data: Path, out: Path, config_path: Path, smoke: bool) -> dict[str, Any]:
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model
    from transformers import AutoTokenizer, EarlyStoppingCallback, set_seed
    from trl import SFTConfig, SFTTrainer

    with config_path.open("rb") as f:
        cfg = tomllib.load(f)
    fmt = load_train_format()
    set_seed(cfg["seed"])
    base, train_cfg, lora_cfg = cfg["base_model"], cfg["training"], cfg["lora"]
    started = datetime.now(UTC).isoformat(timespec="seconds")

    tokenizer = AutoTokenizer.from_pretrained(base["id"], revision=base["revision"])
    max_length = cfg["data"]["max_length"]
    train_rows = fmt.encode_file(tokenizer, data / "train.jsonl", max_length)
    val_rows = fmt.encode_file(tokenizer, data / "val.jsonl", max_length)
    if not train_rows or not val_rows:
        raise SystemExit(f"need examples in both train.jsonl and val.jsonl (got {len(train_rows)}, {len(val_rows)})")

    model = build_model(cfg, tokenizer, smoke)
    if smoke:  # a random base can't be re-created later: keep it, so export.py can merge the adapter into it
        model.save_pretrained(out / "base")
        tokenizer.save_pretrained(out / "base")
    lora = LoraConfig(
        r=lora_cfg["r"],
        lora_alpha=lora_cfg["alpha"],
        lora_dropout=lora_cfg["dropout"],
        target_modules=lora_cfg["target_modules"],
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora)

    per_epoch = {"eval_strategy": "epoch", "save_strategy": "epoch"}
    smoke_steps = {
        "max_steps": cfg["smoke"]["max_steps"],
        "eval_strategy": "steps",
        "save_strategy": "steps",
        "eval_steps": cfg["smoke"]["eval_steps"],
        "save_steps": cfg["smoke"]["eval_steps"],
        "use_cpu": True,
    }
    args = SFTConfig(
        output_dir=str(out / "checkpoints"),
        num_train_epochs=train_cfg["epochs"],
        per_device_train_batch_size=train_cfg["batch_size"],
        per_device_eval_batch_size=train_cfg["batch_size"],
        gradient_accumulation_steps=1 if smoke else train_cfg["gradient_accumulation"],
        learning_rate=train_cfg["learning_rate"],
        lr_scheduler_type=train_cfg["scheduler"],
        warmup_steps=0 if smoke else train_cfg["warmup_steps"],
        logging_steps=1 if smoke else train_cfg["logging_steps"],
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        save_total_limit=2,
        seed=cfg["seed"],
        data_seed=cfg["seed"],
        fp16=False if smoke else train_cfg["fp16"],
        bf16=False,
        gradient_checkpointing=False if smoke else train_cfg["gradient_checkpointing"],
        report_to="none",
        max_length=None,  # train_format already refused anything longer; never truncate here
        packing=False,
        dataset_kwargs={"skip_prepare_dataset": True},  # rows are already input_ids + labels
        **(smoke_steps if smoke else per_epoch),
    )
    trainer = SFTTrainer(
        model=model,
        args=args,
        train_dataset=Dataset.from_list(train_rows),
        eval_dataset=Dataset.from_list(val_rows),
        processing_class=tokenizer,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=train_cfg["early_stopping_patience"])],
    )
    result = trainer.train()
    final_eval = trainer.evaluate()

    adapter = out / "adapter"
    model.save_pretrained(adapter)
    tokenizer.save_pretrained(adapter)

    manifest_path = data / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    history = [
        {k: v for k, v in row.items() if k in ("epoch", "step", "loss", "eval_loss")}
        for row in trainer.state.log_history
        if "loss" in row or "eval_loss" in row
    ]
    lineage = {
        "smoke": smoke,
        "started": started,
        "finished": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": {
            "manifest_sha256": sha256(manifest_path),
            "train_sha256": sha256(data / "train.jsonl"),
            "val_sha256": sha256(data / "val.jsonl"),
            "traces_db_sha256": manifest.get("input_db_sha256"),
            "teachers": manifest.get("teachers"),
            "dataset_git": manifest.get("git"),
            "grading": (manifest.get("settings") or {}).get("grading"),
            "train_examples": len(train_rows),
            "val_examples": len(val_rows),
        },
        "base_model": {"id": base["id"], "revision": base["revision"], "random_tiny": smoke},
        "config": cfg,
        "libraries": versions(),
        "results": {
            "train_loss": result.training_loss,
            "best_eval_loss": trainer.state.best_metric,
            "final_eval_loss": final_eval.get("eval_loss"),
            "epochs": trainer.state.epoch,
            "steps": trainer.state.global_step,
            "best_checkpoint": trainer.state.best_model_checkpoint,
            "history": history,
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "lineage.json").write_text(json.dumps(lineage, indent=2, default=str) + "\n", encoding="utf-8")
    losses = [result.training_loss, final_eval.get("eval_loss")]
    if not all(isinstance(x, float) and math.isfinite(x) for x in losses):
        raise SystemExit(f"loss is not finite: train {losses[0]}, eval {losses[1]}")
    return lineage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, required=True, help="folder with train.jsonl, val.jsonl, manifest.json")
    parser.add_argument("--out", type=Path, required=True, help="output folder (adapter/, lineage.json)")
    parser.add_argument("--config", type=Path, default=HERE / "train_config.toml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true", help="CPU, tiny random model, a few steps")
    mode.add_argument("--gpu-smoke", action="store_true", help="GPU: real base model overfits a fixed batch")
    args = parser.parse_args(argv)
    if args.gpu_smoke:
        with args.config.open("rb") as f:
            return 0 if gpu_smoke(args.data, args.out, tomllib.load(f))["passed"] else 1
    lineage = run(args.data, args.out, args.config, args.smoke)
    r = lineage["results"]
    print(f"train loss {r['train_loss']:.4f}, best val loss {r['best_eval_loss']}, steps {r['steps']}")
    print(f"Adapter: {args.out / 'adapter'}\nLineage: {args.out / 'lineage.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
