"""Local model loading and multimodal input preparation."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from local_image_paths import DEFAULT_IMAGE_ROOTS, resolve_local_image

os.environ.setdefault("HF_HOME", str(ROOT / ".cache" / "huggingface"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import torch
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration


def read_config(path=None):
    return json.loads(Path(path or ROOT / "config.json").read_text(encoding="utf-8"))


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_base(config):
    torch.set_num_threads(config["threads"])
    torch.set_num_interop_threads(1)
    torch.manual_seed(config["seed"])
    dtype = {"bfloat16": torch.bfloat16, "float32": torch.float32}[config["dtype"]]
    path = ROOT / config["model_path"]
    if not (ROOT / "models" / "download_manifest.json").exists():
        raise RuntimeError("Complete download_model.py before loading weights")
    processor = AutoProcessor.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
    # Qwen processors express size limits as pixel counts, despite the edge names.
    processor.image_processor.size = {"shortest_edge": config["image_pixels"],
                                      "longest_edge": config["image_pixels"]}
    model = Qwen3VLForConditionalGeneration.from_pretrained(
        str(path), local_files_only=True, trust_remote_code=False,
        dtype=dtype, device_map={"": "cpu"}, attn_implementation="sdpa",
    )
    model.config.use_cache = False
    return model, processor, dtype


def messages_for(row, include_answer=False):
    if not isinstance(row, dict) or not isinstance(row.get("question"), str) or len(row["question"]) > 8000:
        raise ValueError("The analysis prompt is invalid or too long.")
    content = []
    if row.get("image"):
        path = resolve_local_image(row["image"], allowed_roots=DEFAULT_IMAGE_ROOTS,
                                   relative_to=ROOT)
        content.append({"type": "image", "image": str(path)})
    content.append({"type": "text", "text": row["question"]})
    messages = [{"role": "user", "content": content}]
    if include_answer:
        messages.append({"role": "assistant", "content": [{"type": "text", "text": row["answer"]}]})
    return messages


def model_inputs(processor, row, dtype, include_answer=False, max_length=512):
    inputs = processor.apply_chat_template(
        messages_for(row, include_answer=include_answer), tokenize=True,
        add_generation_prompt=not include_answer, return_dict=True, return_tensors="pt",
    )
    if inputs["input_ids"].shape[-1] > max_length:
        raise ValueError(f"{row.get('id', 'input')} exceeds {max_length} tokens; shorten the text or image budget")
    for key, tensor in inputs.items():
        if isinstance(tensor, torch.Tensor) and tensor.is_floating_point():
            inputs[key] = tensor.to(dtype=dtype)
    if include_answer:
        prefix = model_inputs(processor, row, dtype, include_answer=False, max_length=max_length)
        prefix_ids = prefix["input_ids"]
        prefix_length = prefix_ids.shape[-1]
        if not torch.equal(inputs["input_ids"][:, :prefix_length], prefix_ids):
            raise RuntimeError("Chat template prefix mismatch; refusing incorrect supervision masking")
        labels = inputs["input_ids"].clone()
        labels[:, :prefix_length] = -100
        if inputs.get("attention_mask") is not None:
            labels[inputs["attention_mask"] == 0] = -100
        if (labels != -100).sum().item() == 0:
            raise ValueError("Example has no assistant tokens to supervise")
        inputs["labels"] = labels
    return inputs
