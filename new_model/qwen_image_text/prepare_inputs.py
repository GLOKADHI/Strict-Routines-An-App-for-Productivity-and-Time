"""Tokenize the image/text dataset and save answer-masked tensors without training."""
import hashlib
import json

from common import ROOT, model_inputs, read_config, read_rows
import torch
from transformers import AutoProcessor


def main():
    config = read_config()
    torch.set_num_threads(config["threads"])
    processor = AutoProcessor.from_pretrained(str(ROOT / config["model_path"]),
                                             local_files_only=True, trust_remote_code=False)
    processor.image_processor.size = {"shortest_edge": config["image_pixels"],
                                      "longest_edge": config["image_pixels"]}
    dtype = {"bfloat16": torch.bfloat16, "float32": torch.float32}[config["dtype"]]
    destination = ROOT / "data" / "encoded"
    destination.mkdir(exist_ok=True)
    metadata = {"image_pixels": config["image_pixels"], "max_sequence_length": config["max_sequence_length"],
                "dtype": config["dtype"], "examples": [], "dataset_sha256": {}}
    for split, key in [("train", "train_file"), ("validation", "validation_file")]:
        path = ROOT / config[key]
        metadata["dataset_sha256"][split] = hashlib.sha256(path.read_bytes()).hexdigest()
        for row in read_rows(path):
            inputs = model_inputs(processor, row, dtype, include_answer=True,
                                  max_length=config["max_sequence_length"])
            torch.save(dict(inputs), destination / f"{row['id']}.pt")
            record = {"id": row["id"], "split": split, "modality": row["modality"],
                      "tokens": inputs["input_ids"].shape[-1],
                      "answer_tokens": int((inputs["labels"] != -100).sum())}
            if "image_grid_thw" in inputs:
                record["image_grid_thw"] = inputs["image_grid_thw"].tolist()
            metadata["examples"].append(record)
    (destination / "manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps({"encoded_examples": len(metadata["examples"]),
                      "maximum_tokens": max(r["tokens"] for r in metadata["examples"]),
                      "training_performed": False}), flush=True)


if __name__ == "__main__":
    main()
