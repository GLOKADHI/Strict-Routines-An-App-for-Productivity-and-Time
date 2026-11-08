"""Small, resumable CPU LoRA fine-tuning run on image + text examples."""
import argparse
import hashlib
import json
import math
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from common import ROOT, load_base, model_inputs, read_config, read_rows
import psutil
import torch
from peft import LoraConfig, PeftModel, TaskType, get_peft_model


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def parameter_digest(model):
    digest = hashlib.sha256()
    for name, parameter in model.named_parameters():
        if parameter.requires_grad:
            digest.update(name.encode())
            digest.update(parameter.detach().float().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def evaluate(model, processor, rows, dtype, max_length):
    model.eval()
    records = []
    with torch.no_grad():
        for row in rows:
            inputs = model_inputs(processor, row, dtype, include_answer=True, max_length=max_length)
            loss = model(**inputs, use_cache=False).loss.item()
            if not math.isfinite(loss):
                raise RuntimeError("Non-finite validation loss")
            records.append({"id": row["id"], "modality": row["modality"], "loss": loss,
                            "answer_tokens": int((inputs["labels"][:, 1:] != -100).sum())})
            emit("validation_example", **records[-1])
    metrics = {}
    for group in ["all", "image", "text"]:
        selected = [r for r in records if group == "all" or r["modality"] == group]
        tokens = sum(r["answer_tokens"] for r in selected)
        metrics[group] = {"examples": len(selected), "answer_tokens": tokens,
                          "token_weighted_loss": sum(r["loss"] * r["answer_tokens"] for r in selected) / tokens}
    return {"summary": metrics, "examples": records}


def save_checkpoint(model, optimizer, output, state):
    model.save_pretrained(output, safe_serialization=True)
    torch.save({"optimizer": optimizer.state_dict(), "rng": torch.get_rng_state()}, output / "optimizer.pt")
    temporary = output / "run_state.tmp.json"
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(output / "run_state.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--output", help="Output relative to this folder")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    config = read_config(args.config)
    if args.max_steps is not None:
        config["max_steps"] = args.max_steps
    if args.output:
        config["output"] = args.output
    if config["max_steps"] < 1:
        raise ValueError("max_steps must be positive")
    output = (ROOT / config["output"]).resolve()
    if not output.is_relative_to(ROOT / "checkpoints"):
        raise ValueError("Keep training checkpoints under qwen_image_text/checkpoints")
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "run_state.json"
    if state_path.exists() and not args.resume:
        raise RuntimeError("Checkpoint already exists. Use --resume or --output checkpoints/a-new-run")
    if args.resume and not state_path.exists():
        raise RuntimeError("No saved run state to resume")
    memory = psutil.virtual_memory()
    emit("starting", pid=os.getpid(), device="cpu", dtype=config["dtype"],
         available_ram_gib=round(memory.available / 2**30, 2), config=config)
    if memory.available < 5 * 2**30:
        raise RuntimeError("Less than 5 GiB RAM available; close other applications before loading this 2B model")
    train_rows = read_rows(ROOT / config["train_file"])
    validation_rows = read_rows(ROOT / config["validation_file"])
    # A fixed four-example validation subset keeps this CPU prototype practical.
    validation_rows = validation_rows[:4]
    if set(r["modality"] for r in train_rows) != {"image", "text"}:
        raise ValueError("Training data must contain both image and text examples")
    data_hashes = {name: hashlib.sha256((ROOT / config[key]).read_bytes()).hexdigest()
                   for name, key in [("train", "train_file"), ("validation", "validation_file")]}
    model, processor, dtype = load_base(config)
    if args.resume:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["dataset_sha256"] != data_hashes:
            raise RuntimeError("Dataset changed since the saved checkpoint")
        previous_config = state["config"]
        if any(previous_config[k] != config[k] for k in config if k != "max_steps"):
            raise RuntimeError("Only max_steps may change when resuming a run")
        if state["step"] >= config["max_steps"] and state["status"] == "complete":
            emit("already_complete", step=state["step"], output=str(output))
            return
        state["config"] = config
        state["status"] = "running"
        state.pop("error", None)
        model = PeftModel.from_pretrained(model, output, is_trainable=True, local_files_only=True)
    else:
        target = r".*language_model\.layers\.\d+\.self_attn\.(q_proj|v_proj)$"
        modules = [name for name, _ in model.named_modules() if re.fullmatch(target, name)]
        if not modules:
            raise RuntimeError("No language-attention modules matched; refusing to train the wrong modules")
        model = get_peft_model(model, LoraConfig(
            r=config["lora_rank"], lora_alpha=config["lora_alpha"], lora_dropout=0.0,
            target_modules=target, bias="none", task_type=TaskType.CAUSAL_LM,
        ))
        state = {"status": "running", "started_at_utc": datetime.now(timezone.utc).isoformat(),
                 "config": config, "step": 0, "history": [], "dataset_sha256": data_hashes,
                 "model_revision": json.loads((ROOT / "models" / "download_manifest.json").read_text())["revision"],
                 "initial_adapter_sha256": parameter_digest(model),
                 "lora_target_count": len(modules), "validation_subset_ids": [r["id"] for r in validation_rows]}
    trainable = [p for p in model.parameters() if p.requires_grad]
    trainable_count = sum(p.numel() for p in trainable)
    state["trainable_parameters"] = trainable_count
    emit("model_loaded", total_parameters=sum(p.numel() for p in model.parameters()),
         trainable_parameters=trainable_count, rss_gib=round(psutil.Process().memory_info().rss / 2**30, 2))
    optimizer = torch.optim.AdamW(trainable, lr=config["learning_rate"], weight_decay=0.0)
    if args.resume:
        saved = torch.load(output / "optimizer.pt", map_location="cpu", weights_only=True)
        optimizer.load_state_dict(saved["optimizer"])
        torch.set_rng_state(saved["rng"])
    else:
        emit("validation_before")
        state["validation_before"] = evaluate(model, processor, validation_rows, dtype, config["max_sequence_length"])
        save_checkpoint(model, optimizer, output, state)
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    start = time.monotonic()
    try:
        for index in range(state["step"], config["max_steps"]):
            model.train()
            row = train_rows[index % len(train_rows)]
            inputs = model_inputs(processor, row, dtype, include_answer=True, max_length=config["max_sequence_length"])
            optimizer.zero_grad(set_to_none=True)
            step_start = time.monotonic()
            loss = model(**inputs, use_cache=False).loss
            if not torch.isfinite(loss):
                raise RuntimeError("Non-finite training loss")
            loss.backward()
            grad_norm = torch.nn.utils.clip_grad_norm_(trainable, 1.0, error_if_nonfinite=True).item()
            optimizer.step()
            state["step"] = index + 1
            record = {"step": index + 1, "id": row["id"], "modality": row["modality"],
                      "loss": loss.item(), "grad_norm": grad_norm,
                      "tokens": inputs["input_ids"].shape[-1], "seconds": round(time.monotonic() - step_start, 2),
                      "rss_gib": round(psutil.Process().memory_info().rss / 2**30, 2)}
            state["history"].append(record)
            emit("training_step", **record)
            if state["step"] % 5 == 0:
                save_checkpoint(model, optimizer, output, state)
        emit("validation_after")
        state["validation_after"] = evaluate(model, processor, validation_rows, dtype, config["max_sequence_length"])
        state["final_adapter_sha256"] = parameter_digest(model)
        if state["final_adapter_sha256"] == state["initial_adapter_sha256"]:
            raise RuntimeError("Adapter parameters did not change during training")
        state["status"] = "complete"
        state["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        state["last_session_training_seconds"] = round(time.monotonic() - start, 2)
        state["modality_steps"] = dict(Counter(r["modality"] for r in state["history"]))
        save_checkpoint(model, optimizer, output, state)
        (output / "metrics.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        (output / "training_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        emit("complete", step=state["step"], modality_steps=state["modality_steps"],
             output=str(output), validation=state["validation_after"]["summary"])
    except (Exception, KeyboardInterrupt) as exc:
        state["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        state["error"] = str(exc)
        save_checkpoint(model, optimizer, output, state)
        raise


if __name__ == "__main__":
    main()
