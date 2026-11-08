"""Run the local base model plus trained LoRA adapter on text or an image."""
import argparse
import json
from pathlib import Path

from common import ROOT, load_base, model_inputs, read_config
import torch
from peft import PeftModel


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--adapter", default="checkpoints/image-text-lora")
    parser.add_argument("--base-only", action="store_true")
    parser.add_argument("--max-new-tokens", type=int, default=96)
    args = parser.parse_args()
    config = read_config()
    model, processor, dtype = load_base(config)
    if not args.base_only:
        path = ROOT / args.adapter
        state = json.loads((path / "run_state.json").read_text(encoding="utf-8"))
        if state["status"] != "complete":
            raise RuntimeError("Adapter training is not complete; inspect run_state.json")
        model = PeftModel.from_pretrained(model, path, local_files_only=True)
    row = {"question": args.prompt}
    if args.image:
        row["image"] = str(args.image)
    inputs = model_inputs(processor, row, dtype, max_length=config["max_sequence_length"])
    model.eval()
    with torch.no_grad():
        generated = model.generate(**inputs, max_new_tokens=args.max_new_tokens, do_sample=False, use_cache=True)
    response = processor.decode(generated[0, inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
    print(response, flush=True)


if __name__ == "__main__":
    main()
