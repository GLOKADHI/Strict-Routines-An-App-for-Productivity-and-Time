"""Download official Qwen weights at a pinned revision, with local-only caches."""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("HF_HOME", str(ROOT / ".cache" / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from huggingface_hub import HfApi, snapshot_download


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", help="Pinned Hugging Face commit; defaults to current official revision")
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    model_id = config["model_id"]
    target = ROOT / config["model_path"]
    info = HfApi().model_info(model_id, revision=args.revision, files_metadata=True)
    expected = {s.rfilename: s.lfs for s in info.siblings if s.rfilename.endswith(".safetensors")}
    total = sum(s.size or 0 for s in info.siblings)
    print(json.dumps({"model": model_id, "revision": info.sha, "repository_bytes": total}), flush=True)
    snapshot_download(
        repo_id=model_id,
        revision=info.sha,
        local_dir=target,
        max_workers=2,
        allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "*.jinja", "*.md", "LICENSE*"],
    )
    files = []
    for path in sorted(target.iterdir()):
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        lfs = expected.get(path.name)
        expected_sha = getattr(lfs, "sha256", None) if lfs else None
        if isinstance(lfs, dict):
            expected_sha = lfs.get("sha256")
        if expected_sha and digest != expected_sha:
            raise RuntimeError(f"Checksum mismatch for {path.name}")
        files.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest,
                      "official_lfs_sha256_verified": bool(expected_sha)})
    if not any(f["name"].endswith(".safetensors") for f in files):
        raise RuntimeError("No model weights were downloaded")
    manifest = {"model_id": model_id, "revision": info.sha,
                "source": f"https://huggingface.co/{model_id}", "license": "Apache-2.0",
                "downloaded_at_utc": datetime.now(timezone.utc).isoformat(), "files": files}
    (ROOT / "models" / "download_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("DOWNLOAD COMPLETE; local weights and checksums saved.", flush=True)


if __name__ == "__main__":
    main()
