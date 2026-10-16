"""QLoRA fine-tuning of the Kestrel student on a `kestrel distill build` dataset (Phase 4).

    python train.py --data DIR --out DIR [--config train_config.toml]   # Kaggle GPU: QLoRA
    python train.py --smoke --data distill/smoke --out OUT               # CPU: tiny model, a few steps

DIR holds train.jsonl, val.jsonl and manifest.json. Settings come from the config file. Output:
OUT/adapter (the LoRA adapter and tokenizer), OUT/lineage.json (what was trained on what, with which
code), OUT/checkpoints (intermediate, deletable). Distill/TRAINING.md has the Kaggle steps.

Runs on Kaggle, where Kestrel can't be installed: Python >= 3.11 (tomllib, trl), no kestrel imports;
train_format.py is loaded from next to this file or from the repo.
"""

from __future__ import annotations

import argparse
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
    parser.add_argument("--smoke", action="store_true", help="CPU, tiny random model, a few steps")
    args = parser.parse_args(argv)
    lineage = run(args.data, args.out, args.config, args.smoke)
    r = lineage["results"]
    print(f"train loss {r['train_loss']:.4f}, best val loss {r['best_eval_loss']}, steps {r['steps']}")
    print(f"Adapter: {args.out / 'adapter'}\nLineage: {args.out / 'lineage.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
