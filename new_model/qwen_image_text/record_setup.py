"""Record actual completed setup stages; never mark deferred training complete."""
import importlib.metadata as metadata
import json
import platform
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    packages = ["torch", "torchvision", "transformers", "peft", "accelerate", "huggingface-hub",
                "pillow", "psutil", "safetensors", "tokenizers", "numpy"]
    versions = {}
    for name in packages:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "MISSING"
    source = ROOT / "vendor" / "Qwen3-VL"
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    download_path = ROOT / "models" / "download_manifest.json"
    encoded_path = ROOT / "data" / "encoded" / "manifest.json"
    download = json.loads(download_path.read_text(encoding="utf-8")) if download_path.exists() else None
    encoded = json.loads(encoded_path.read_text(encoding="utf-8")) if encoded_path.exists() else None
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    weight_files = [f for f in download["files"] if f["name"].endswith(".safetensors")] if download else []
    files_present = download and all((ROOT / config["model_path"] / f["name"]).is_file() and
                                    (ROOT / config["model_path"] / f["name"]).stat().st_size == f["bytes"]
                                    for f in download["files"])
    status = {"recorded_at_utc": datetime.now(timezone.utc).isoformat(),
              "python": platform.python_version(), "dependencies": versions,
              "source_clone": {"status": "COMPLETE", "url": "https://github.com/QwenLM/Qwen3-VL", "commit": commit},
              "model_download": {"status": "COMPLETE" if files_present else "INCOMPLETE",
                                 "model_id": config["model_id"], "revision": download["revision"] if download else None,
                                 "weight_bytes": sum(f["bytes"] for f in weight_files),
                                 "published_weight_hashes_verified": bool(weight_files) and
                                     all(f["official_lfs_sha256_verified"] for f in weight_files)},
              "dataset": json.loads((ROOT / "data" / "dataset_manifest.json").read_text(encoding="utf-8"))["train"],
              "preprocessing": {"status": "COMPLETE" if encoded else "INCOMPLETE",
                                "encoded_examples": len(encoded["examples"]) if encoded else 0,
                                "modality_counts": dict(Counter(r["modality"] for r in encoded["examples"])) if encoded else {}},
              "full_model_loading": "NOT VERIFIED — deferred due to available RAM and user choice",
              "training": "DEFERRED — user requested finishing setup and training later",
              "inference": "NOT VERIFIED — full model not loaded",
              "application_changes": "No EXE/APK or app environment changes performed for this task"}
    (ROOT / "SETUP_STATUS.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    (ROOT / "installed_versions.json").write_text(json.dumps(versions, indent=2), encoding="utf-8")
    # Pin the model runtime only; avoid copying unrelated shared application packages.
    pins = [f"{name}=={version}" for name, version in versions.items() if version != "MISSING"]
    (ROOT / "requirements.lock.txt").write_text("--extra-index-url https://download.pytorch.org/whl/cpu\n" +
                                               "\n".join(pins) + "\n", encoding="utf-8")
    print(json.dumps(status, indent=2), flush=True)


if __name__ == "__main__":
    main()
