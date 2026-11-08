"""Verify saved training artifacts without loading or changing any app state."""
import collections
import hashlib
import json
import math
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import yaml


def main():
    root = Path(__file__).resolve().parents[1] / "datasets/strict_session_devices_600"
    manifest = [json.loads(line) for line in (root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    counts, boxes = collections.Counter(), collections.Counter()
    hashes, pixels = set(), set()
    groups = collections.defaultdict(set)
    for row in manifest:
        image_path, label_path = root / row["image"], root / row["label"]
        assert hashlib.sha256(image_path.read_bytes()).hexdigest() == row["sha256"]
        assert row["sha256"] not in hashes
        hashes.add(row["sha256"])
        with Image.open(image_path) as im:
            im.load()
            assert im.size == (row["width"], row["height"])
            rgb = im.convert("RGB")
            pixel = hashlib.sha256(str(rgb.size).encode() + rgb.tobytes()).hexdigest()
            assert pixel == row["pixel_sha256"] and pixel not in pixels
            pixels.add(pixel)
        lines = label_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == len(row["boxes"])
        for line in lines:
            parts = line.split()
            assert len(parts) == 5 and parts[0] == "0"
            x, y, w, h = map(float, parts[1:])
            assert all(math.isfinite(v) for v in (x, y, w, h))
            assert w > 0 and h > 0
            assert x-w/2 >= -1e-7 and y-h/2 >= -1e-7
            assert x+w/2 <= 1+1e-7 and y+h/2 <= 1+1e-7
        counts[row["category"]] += 1
        boxes[row["category"]] += len(lines)
        groups[row["group"]].add(row["split"])
    assert counts == {"mobile_phone": 300, "earbud_worn": 300}
    assert all(len(splits) == 1 for splits in groups.values())
    for category in counts:
        cfg = yaml.safe_load((root / category / "data.yaml").read_text(encoding="utf-8"))
        assert Path(cfg["path"]).resolve() == root / category
        assert cfg["nc"] == 1 and cfg["names"] == {0: category}
        assert len(list((root / category / "images").rglob("*.jpg"))) == 300
        assert len(list((root / category / "labels").rglob("*.txt"))) == 300
        for split in ("train", "val", "test"):
            assert (root / category / cfg[split]).is_dir()
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
    preview = Image.new("RGB", (1440, 1080), "#0e1620")
    draw = ImageDraw.Draw(preview)
    draw.text((20, 10), "300 MOBILE PHONES + 300 WORN EARBUDS | SOURCE BOUNDING BOXES", font=font, fill="white")
    for k, category in enumerate(counts):
        rows = [r for r in manifest if r["category"] == category][:18]
        for i, row in enumerate(rows):
            with Image.open(root / row["image"]) as original:
                im = original.convert("RGB")
            im.thumbnail((230, 145))
            left, top = (i % 6)*240 + (240-im.width)//2, 45 + k*510 + (i//6)*165
            preview.paste(im, (left, top))
            for x, y, w, h in row["boxes"]:
                draw.rectangle((left+(x-w/2)*im.width, top+(y-h/2)*im.height,
                                left+(x+w/2)*im.width, top+(y+h/2)*im.height), outline="#37df99", width=2)
            draw.text(((i % 6)*240+8, top+146), Path(row["image"]).stem, font=font, fill="#b5c8da")
    preview.save(root / "preview.jpg", quality=92)
    audit = dict(result="PASS", images_checked=len(manifest), class_counts=dict(counts),
                 bounding_boxes=dict(boxes), file_and_pixel_duplicates=0,
                 matching_labels=True, all_images_decode=True, boxes_valid=True,
                 known_group_split_leakage=0, yolo_configs_resolve=True,
                 notes="Independent on-disk artifact audit; does not certify source annotation accuracy or model performance")
    (root / "artifact_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    source_root = root.parent / "strict_session_devices_sources"
    source_hashes = {}
    for filename in ("earphones_repository.zip", "driver_earphones_repository.zip",
                     "openimages_validation_boxes.csv", "openimages_test_boxes.csv",
                     "openimages_validation_metadata.csv", "openimages_test_metadata.csv",
                     "phone_candidates.json", "driver_candidates.json", "approved_selection.json"):
        source = source_root / filename
        source_hashes[filename] = hashlib.sha256(source.read_bytes()).hexdigest()
    (root / "source_checksums.json").write_text(json.dumps(source_hashes, indent=2), encoding="utf-8")
    archive = root.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as z:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(root.parent))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert sum("/images/" in n and n.endswith(".jpg") for n in z.namelist()) == 600
        assert sum("/labels/" in n and n.endswith(".txt") for n in z.namelist()) == 600
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{archive_sha}  {archive.name}\n", encoding="ascii")
    print(json.dumps(dict(audit=audit, zip_bytes=archive.stat().st_size, zip_sha256=archive_sha)))


if __name__ == "__main__":
    main()
