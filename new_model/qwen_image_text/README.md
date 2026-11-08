# Qwen image + text fine-tuning workspace

This folder contains a local installation of **Qwen/Qwen3-VL-2B-Instruct**, the official Qwen3-VL source checkout, and a small image/text supervised dataset. It is separate from the StrictRoutines application. No APK or EXE integration is included.

## Status

See `SETUP_STATUS.json` for the actual installation/download/preprocessing results. **Training is deferred at the user's request. No trained adapter or measured model-quality result is claimed.**

## Contents

- `vendor/Qwen3-VL`: official upstream source, cloned from <https://github.com/QwenLM/Qwen3-VL>.
- `models/Qwen3-VL-2B-Instruct`: actual pretrained safetensors, tokenizer, and image processor.
- `models/download_manifest.json`: pinned Hugging Face revision, sizes, SHA-256 hashes, and checks against published weight hashes.
- `.venv`: separate Python 3.14 virtual environment. It reads the existing CPU PyTorch installation through `--system-site-packages`; model dependencies were installed into this new environment. The existing app environments were not modified.
- `data/train.jsonl`: 30 CIFAR-10 images and 20 authored text examples, interleaved.
- `data/validation.jsonl`: 20 distinct CIFAR-10 test images and six distinct authored text examples.
- `data/dataset_manifest.json`: source paths, image hashes, and split provenance.
- `data/encoded`: image/text tokens and assistant-only supervision masks prepared without training.
- `config.json`: CPU settings for the initial 20-update LoRA prototype.
- `logs`: actual dependency, clone, download, preprocessing, and future training output.

The public images are originally 32x32 pixels and are resized to 128x128 for this inexpensive prototype. They cover ten object categories. The text examples cover short reading, extraction, summaries, arithmetic, and simple instructions. This is a demonstration dataset; broader OCR, proof-image review, or domain-specific tasks need representative labelled data and a larger training/evaluation run. No application database, private screen recording, or user screenshot was used.

## Train later (PowerShell)

Close unused memory-heavy applications first. This PC has about 16 GB RAM, Intel Iris Xe graphics, and no CUDA GPU. The script requires at least 5 GiB RAM available before loading; 7–8 GiB available gives more headroom. CPU fine-tuning is slow. The script does not stop other applications or start itself automatically.

```powershell
Set-Location 'C:\Users\lokad\Music\Practical\StrictRecords\new_model\qwen_image_text'
& .\train.ps1
```

This performs twenty actual optimizer updates: ten image examples and ten text examples. The base model and vision encoder stay frozen. LoRA rank eight updates only the language model's attention query/value projections. Assistant answers contribute to loss; image tokens and user prompts are masked. Batch size is one, CPU weights use bfloat16, and gradient checkpointing limits activation memory.

Results are written to `checkpoints/image-text-lora`:

- `adapter_model.safetensors` and `adapter_config.json`: learned adapter; the base model is also required for inference.
- `optimizer.pt` and `run_state.json`: checkpoint every five updates and on a training error/interruption.
- `metrics.json`: written only after training and post-training validation finish successfully.
- `training_config.json`: settings used for the completed run.

The four-example validation subset contains two held-out image examples and two text examples. The script records before/after answer-token loss on those same examples and verifies that adapter parameters changed. This small loss comparison does not establish general accuracy.

Resume an interrupted run:

```powershell
& .\train.ps1 -Resume
```

Continue an existing completed adapter to a higher step count:

```powershell
& .\train.ps1 -Resume -MaxSteps 100
```

Start a separate run without overwriting the earlier adapter:

```powershell
& .\.venv\Scripts\python.exe -u train.py --output checkpoints/second-run --max-steps 100
```

## Use the adapter after training completes

```powershell
& .\.venv\Scripts\python.exe infer.py --prompt 'Summarize: Ana packed her bag and took the bus to school.'
& .\.venv\Scripts\python.exe infer.py --image '.\data\images\validation\cat_00.png' --prompt 'What is the main object in this image?'
```

`--base-only` uses the downloaded pretrained model without an adapter. Inference is local; scripts do not upload images or text. Model loading refuses incomplete downloads. Adapter inference refuses incomplete training.

## Add your own data

Use JSONL records with `id`, `modality`, `question`, and `answer`. Image records also need `image`, a path relative to this folder or an absolute image path. Keep training and validation images/passages distinct.

```json
{"id":"train-example-001","modality":"image","image":"data/my_images/photo.jpg","question":"Describe the objects on the table.","answer":"A cup and a notebook are on the table."}
{"id":"train-example-002","modality":"text","question":"Read: The lamp is beside the chair. Where is the lamp?","answer":"The lamp is beside the chair."}
```

Point `train_file` and `validation_file` in `config.json` to your datasets and choose a fresh output directory. The small 512-token limit deliberately rejects oversized examples; shorten them or increase the limit when hardware permits. Resuming refuses altered datasets or incompatible settings.

The preprocessed tensor files are optional preparation artifacts; training re-encodes the JSONL records with the configured processor. To regenerate the demonstration data and tensors:

```powershell
& .\.venv\Scripts\python.exe prepare_dataset.py
& .\.venv\Scripts\python.exe prepare_inputs.py
```

To resume an incomplete weight download at the recorded source revision:

```powershell
& .\.venv\Scripts\python.exe download_model.py --revision 89644892e4d85e24eaac8bacfd4f463576704203
```

## Sources

- Model card and Apache-2.0 model license: <https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct>.
- Official source repository: <https://github.com/QwenLM/Qwen3-VL> (checkout commit recorded in `SETUP_STATUS.json`).
- CIFAR-10 source and class definitions: <https://www.cs.toronto.edu/~kriz/cifar.html>.
- LoRA library: <https://huggingface.co/docs/peft/en/task_guides/lora_based_methods>.
