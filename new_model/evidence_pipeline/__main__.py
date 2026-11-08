import argparse
import json
from pathlib import Path

from local_image_paths import PROJECT_ROOT
from .pipeline import Attachment, EvidencePipeline, stable_image_id


def main():
    parser = argparse.ArgumentParser(description="Local image analysis; no proof approval or scoring.")
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--task", required=True, help="Real occurrence ID, or diagnostic-only identifier.")
    parser.add_argument("--batch", required=True, help="Stable client batch ID for retries.")
    parser.add_argument("--kind", choices=["photo", "screenshot", "document"], default="photo")
    parser.add_argument("--store", type=Path, default=PROJECT_ROOT/"output"/"evidence_analysis")
    args = parser.parse_args()
    store = args.store.resolve()
    authorized_store = (PROJECT_ROOT/"output"/"evidence_analysis").resolve()
    if not store.is_relative_to(authorized_store):
        parser.error("--store must be inside new_model/output/evidence_analysis")
    attachments = [Attachment(p, stable_image_id(args.task, f"{i}:{p.name}"), args.kind) for i, p in enumerate(args.images)]
    result = EvidencePipeline(store).analyze_batch(attachments, args.task, args.batch)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
