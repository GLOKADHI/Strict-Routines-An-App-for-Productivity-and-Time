"""Prepare public, source-attributed phone / worn-earbud object-detection samples.

Only reads downloaded dataset archives and image files. Never executes upstream
code, loads upstream model weights, or changes the StrictRecords application.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import io
import json
import math
import random
import re
import zipfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "datasets/strict_session_devices_sources"
OUTPUT = ROOT / "datasets/strict_session_devices_600"
SEED = 20261104

# Contact-sheet indices in driver_candidates.json, visually reviewed for an
# in-ear device actually on the ear. Excludes loose products/cases, over-ear
# headphones, and source boxes that encompass a person's torso or a long cable.
DRIVER_APPROVED = [
    8, 9, 13, 14, 16, 27, 28, 29, 30, 37, 40, 41, 43,
    57, 61, 70, 73, 77, 80, 82,
    103, 105, 124, 127, 128, 130, 131, 140,
    145, 149, 165, 167, 168, 191,
    192, 194, 196, 211, 212, 213, 214, 219, 221, 224,
    225, 227, 228, 229, 232, 235, 237,
    245, 246, 268, 270, 274,
    291, 292, 294, 296, 297, 298, 299, 305, 306, 307, 328,
    339, 340, 343, 345, 352, 357, 359, 360, 363, 364, 365, 366,
]

KNOWN_GROUPS = [
    [8, 9, 127, 128, 130],
    [13, 14, 16, 165, 167, 168],
    [27, 224, 225, 227, 228, 229],
    [28, 232],
    [29, 191, 192, 194, 196, 339, 340],
    [30, 268, 270, 352],
    [43, 305],
    [70, 73],
    [80, 82],
    [211, 212, 213, 214, 219, 221, 343, 345],
    [235, 328],
    [245, 246],
    [274, 357],
    [294, 359, 360],
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def phash(im: Image.Image) -> int:
    gray = np.asarray(im.convert("L").resize((32, 32)), dtype=np.float32)
    low = cv2.dct(gray)[:8, :8].flatten()[1:]
    median = float(np.median(low))
    return sum(int(value > median) << bit for bit, value in enumerate(low))


def decode(data: bytes):
    im = Image.open(io.BytesIO(data))
    im.load()
    if min(im.size) < 64:
        raise ValueError("Image too small")
    if im.getexif().get(274, 1) not in (None, 1):
        raise ValueError("EXIF rotation needs annotation review")
    im = im.convert("RGB")
    pixel = sha(str(im.size).encode() + im.tobytes())
    return im, pixel, phash(im), phash(im.transpose(Image.Transpose.FLIP_LEFT_RIGHT))


def boxes_from_yolo(text: str, classes: set[int]):
    boxes = []
    for line in text.splitlines():
        row = line.split()
        if not row or int(row[0]) not in classes:
            continue
        # Do not silently accept polygons or a different annotation format.
        if len(row) != 5:
            raise ValueError("Non-box annotation needs manual review")
        x, y, w, h = map(float, row[1:])
        x0, y0, x1, y1 = x-w/2, y-h/2, x+w/2, y+h/2
        if min(x0, y0) < -0.011 or max(x1, y1) > 1.011:
            raise ValueError("Box extends materially beyond image")
        # Source coordinate rounding can stray slightly over an edge.
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(1, x1), min(1, y1)
        if x1 <= x0 or y1 <= y0:
            raise ValueError("Empty box")
        boxes.append([(x0+x1)/2, (y0+y1)/2, x1-x0, y1-y0])
    if not boxes:
        raise ValueError("No target boxes")
    return boxes


def candidate(data, boxes, category, source, source_id, original_path,
              group, attribution, review, **extra):
    im, pixel, p_hash, mirror = decode(data)
    for x, y, w, h in boxes:
        assert all(math.isfinite(v) for v in (x, y, w, h))
        assert 0 <= x-w/2 <= x+w/2 <= 1.000001
        assert 0 <= y-h/2 <= y+h/2 <= 1.000001
        assert w > 0 and h > 0
    return dict(data=data, boxes=boxes, category=category, source=source,
                source_id=source_id, original_path=original_path,
                group=group, attribution=attribution, review=review,
                width=im.width, height=im.height, sha256=sha(data),
                pixel_sha256=pixel, phash=p_hash, mirror_phash=mirror, **extra)


def is_duplicate(item, accepted, max_distance=5):
    for prior in accepted:
        if item["sha256"] == prior["sha256"] or item["pixel_sha256"] == prior["pixel_sha256"]:
            return True
        if min((item["phash"] ^ prior["phash"]).bit_count(),
               (item["mirror_phash"] ^ prior["phash"]).bit_count()) <= max_distance:
            return True
    return False


def load_phones():
    items, rejected = [], collections.Counter()
    for row in json.loads((SOURCES / "phone_candidates.json").read_text(encoding="utf-8")):
        path = SOURCES / "phone_images" / f'{row["split"]}_{row["id"]}.jpg'
        if not path.is_file():
            continue
        meta = row["metadata"]
        if meta["License"].rstrip("/") != "https://creativecommons.org/licenses/by/2.0":
            rejected["unrecognized_license"] += 1
            continue
        boxes = []
        for b in row["annotations"]:
            x0, x1, y0, y1 = [float(b[k]) for k in ("XMin", "XMax", "YMin", "YMax")]
            boxes.append([(x0+x1)/2, (y0+y1)/2, x1-x0, y1-y0])
        try:
            item = candidate(path.read_bytes(), boxes, "mobile_phone", "open_images",
                             row["id"], str(path.relative_to(ROOT)),
                             "oi-author:" + (meta["AuthorProfileURL"] or row["id"]),
                             dict(meta, image_download_url=row["url"],
                                  annotation_license="CC BY 4.0",
                                  annotation_source="https://storage.googleapis.com/openimages/web/download_v6.html"),
                             "source_annotated; contact_sheet_review", source_split=row["split"])
            if is_duplicate(item, items):
                rejected["exact_or_perceptual_duplicate"] += 1
            else:
                items.append(item)
        except (ValueError, AssertionError, OSError):
            rejected["invalid_image_or_annotation"] += 1
    return items, rejected


def load_earbuds():
    items, rejected = [], collections.Counter()
    groups = {i: "driver-reviewed-" + str(min(g)) for g in KNOWN_GROUPS for i in g}
    with zipfile.ZipFile(SOURCES / "driver_earphones_repository.zip") as z:
        rows = json.loads((SOURCES / "driver_candidates.json").read_text(encoding="utf-8"))
        for i in DRIVER_APPROVED:
            row = rows[i]
            try:
                boxes = boxes_from_yolo("\n".join(row["lines"]), {0, 3})
                item = candidate(z.read(row["path"]), boxes, "earbud_worn", "driver_earphone",
                                 row["stem"], row["path"], groups.get(i, f"driver-reviewed-{i}"),
                                 dict(author="ginger-ojdx6 / Roboflow contributor (as supplied)",
                                      source_url="https://universe.roboflow.com/ginger-ojdx6/earphone-detection-75qzd/dataset/1",
                                      mirror_url="https://github.com/bsaisoumith/Driver-Earphone-Detection-using-YOLOv5",
                                      license="CC BY 4.0 (upstream declaration)", original_class_ids=[0, 3]),
                                 "visually_selected_worn_in_ear_device; source_boxes_retained", review_index=i)
                if is_duplicate(item, items):
                    rejected["driver_exact_or_perceptual_duplicate"] += 1
                else:
                    items.append(item)
            except (ValueError, AssertionError, OSError):
                rejected["driver_invalid_image_or_annotation"] += 1
    with zipfile.ZipFile(SOURCES / "earphones_repository.zip") as z:
        paths = sorted(n for n in z.namelist() if "/earphone.v1i.yolov8/" in n
                       and "/images/" in n and n.lower().endswith((".jpg", ".jpeg", ".png")))
        random.Random(SEED).shuffle(paths)
        for path in paths:
            label = path.replace("/images/", "/labels/").rsplit(".", 1)[0] + ".txt"
            try:
                boxes = boxes_from_yolo(z.read(label).decode(), {0})
                stem = Path(path).name.split(".rf.")[0]
                item = candidate(z.read(path), boxes, "earbud_worn", "earphone_video",
                                 stem, path, "earphone-video-20250920-103420",
                                 dict(author="test-earphones / Roboflow contributor (as supplied)",
                                      source_url="https://universe.roboflow.com/test-earphones/earphone-nwwsh-ja3nu/dataset/1",
                                      mirror_url="https://github.com/LynnLox/earphone-detection/tree/dataset_merge",
                                      license="CC BY 4.0 (upstream declaration)", original_class_ids=[0]),
                                 "source_worn_video; contact_sheet_review_required", forced_split="train")
                if is_duplicate(item, items):
                    rejected["video_exact_or_perceptual_duplicate"] += 1
                else:
                    items.append(item)
            except (ValueError, AssertionError, OSError, KeyError):
                rejected["video_invalid_image_or_annotation"] += 1
            if len(items) >= 335:
                break
    return items, rejected


def grouped_splits(items):
    groups = collections.defaultdict(list)
    for item in items:
        groups[item["group"]].append(item)
    keys = sorted(groups)
    random.Random(SEED).shuffle(keys)
    counts = collections.Counter()
    for key in keys:
        rows = groups[key]
        if rows[0].get("forced_split"):
            split = rows[0]["forced_split"]
        else:
            choices = [s for s in ("val", "test") if counts[s] < 30]
            split = min(choices, key=lambda s: counts[s]) if choices else "train"
        for row in rows:
            row["split"] = split
        counts[split] += len(rows)
    return dict(counts)


def review_sheets(items, folder, prefix):
    folder.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 13)
    for start in range(0, len(items), 48):
        sheet = Image.new("RGB", (1600, 1200), "#111820")
        draw = ImageDraw.Draw(sheet)
        for offset, item in enumerate(items[start:start+48]):
            im = Image.open(io.BytesIO(item["data"])).convert("RGB")
            im.thumbnail((194, 169))
            left, top = (offset % 8)*200 + (200-im.width)//2, (offset//8)*200 + 3
            sheet.paste(im, (left, top))
            for cx, cy, w, h in item["boxes"]:
                draw.rectangle((left+(cx-w/2)*im.width, top+(cy-h/2)*im.height,
                                left+(cx+w/2)*im.width, top+(cy+h/2)*im.height),
                               outline="#ff5252", width=2)
            draw.text(((offset % 8)*200+4, (offset//8)*200+175),
                      f'{start+offset:03d} {item["source"]}', fill="white", font=font)
        sheet.save(folder / f"{prefix}_{start//48:02d}.jpg", quality=91)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-only", action="store_true")
    args = parser.parse_args()
    phones, phone_rejected = load_phones()
    earbuds, earbud_rejected = load_earbuds()
    review = SOURCES / "candidate_review"
    review_sheets(phones, review, "phone")
    review_sheets(earbuds, review, "earbud")
    public = lambda r: {k: v for k, v in r.items() if k != "data"}
    (review / "candidates.json").write_text(json.dumps(
        dict(phones=[public(r) for r in phones], earbuds=[public(r) for r in earbuds],
             rejected=dict(phone=phone_rejected, earbud=earbud_rejected)), indent=2), encoding="utf-8")
    print(json.dumps(dict(phone_candidates=len(phones), earbud_candidates=len(earbuds),
                          rejected=dict(phone=phone_rejected, earbud=earbud_rejected))))
    if args.review_only:
        return
    selection_path = SOURCES / "approved_selection.json"
    if not selection_path.is_file():
        raise SystemExit("Review contact sheets and write approved_selection.json first")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selected = {}
    for category, rows, key in [("mobile_phone", phones, "phones"), ("earbud_worn", earbuds, "earbuds")]:
        indices = selection[key]
        assert len(indices) == 300 and len(set(indices)) == 300
        selected[category] = [rows[i] for i in indices]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    old_manifest = OUTPUT / "manifest.jsonl"
    if old_manifest.is_file():
        # Remove only the known generated image/label files from a previous
        # run, so moving an example between splits cannot leave stale samples.
        for line in old_manifest.read_text(encoding="utf-8").splitlines():
            previous = json.loads(line)
            for key in ("image", "label"):
                previous_path = (OUTPUT / previous[key]).resolve()
                assert previous_path.is_relative_to(OUTPUT.resolve())
                assert previous_path.suffix in {".jpg", ".txt"}
                assert previous_path.stem.startswith(("mobile_phone_", "earbud_worn_"))
                previous_path.unlink(missing_ok=True)
    manifest, report = [], dict(total_images=0, categories={}, duplicate_checks={})
    for category, rows in selected.items():
        splits = grouped_splits(rows)
        for idx, item in enumerate(rows, 1):
            split = item["split"]
            name = f"{category}_{idx:04d}"
            image_dir = OUTPUT / category / "images" / split
            label_dir = OUTPUT / category / "labels" / split
            image_dir.mkdir(parents=True, exist_ok=True)
            label_dir.mkdir(parents=True, exist_ok=True)
            (image_dir / f"{name}.jpg").write_bytes(item["data"])
            (label_dir / f"{name}.txt").write_text("".join(
                "0 " + " ".join(f"{v:.8f}" for v in box) + "\n" for box in item["boxes"]), encoding="utf-8")
            record = public(item)
            record.update(image=f"{category}/images/{split}/{name}.jpg",
                          label=f"{category}/labels/{split}/{name}.txt",
                          class_id=0, review="contact_sheet_selected; source_boxes_not_exhaustively_reannotated")
            record["phash"] = f'{record["phash"]:016x}'
            record["mirror_phash"] = f'{record["mirror_phash"]:016x}'
            manifest.append(record)
        (OUTPUT / category / "data.yaml").write_text(
            f"# Update path if you move this dataset to a different directory.\npath: {json.dumps((OUTPUT / category).as_posix())}\ntrain: images/train\nval: images/val\ntest: images/test\nnc: 1\nnames:\n  0: {category}\n", encoding="utf-8")
        review_sheets(rows, OUTPUT / "review", category)
        report["categories"][category] = dict(images=len(rows), splits=splits,
                                               boxes=sum(len(r["boxes"]) for r in rows),
                                               sources=dict(collections.Counter(r["source"] for r in rows)))
    (OUTPUT / "manifest.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in manifest), encoding="utf-8")
    with (OUTPUT / "attribution.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["image", "category", "source", "original_image", "author", "license", "source_url", "modifications"])
        writer.writeheader()
        for row in manifest:
            attr = row["attribution"]
            writer.writerow(dict(image=row["image"], category=row["category"], source=row["source"],
                                 original_image=row["original_path"], author=attr.get("Author", attr.get("author")),
                                 license=attr.get("License", attr.get("license")),
                                 source_url=attr.get("OriginalLandingURL", attr.get("source_url")),
                                 modifications="Image bytes unchanged; target-only class remap; bbox edge rounding clipped if needed"))
    hashes = [r["sha256"] for r in manifest]
    pixels = [r["pixel_sha256"] for r in manifest]
    assert len(hashes) == len(set(hashes)) == 600
    assert len(pixels) == len(set(pixels)) == 600
    group_splits = collections.defaultdict(set)
    for row in manifest:
        group_splits[row["group"]].add(row["split"])
    assert max(map(len, group_splits.values())) == 1
    report.update(total_images=600, seed=SEED, labels="YOLO normalized bounding boxes; class 0 in each separate dataset",
                  annotation_status="Source labels retained; curated target semantics; not exhaustive reannotation",
                  duplicate_checks=dict(exact_file_duplicates=0, exact_decoded_pixel_duplicates=0,
                                        within_category_phash_or_mirror_distance_lte_5=0,
                                        known_source_groups_crossing_splits=0),
                  rejected_candidates=dict(phone=phone_rejected, earbud=earbud_rejected),
                  limitations=["Generic worn earbuds, not verified Apple branding", "Earphone video is one repeated-subject sequence and is train-only", "Known groups are isolated; subject-independent splits not certified", "Two separate one-class datasets avoid treating unlabelled cross-category objects as background", "No negative/background set or production accuracy validation", "Upstream earbud licenses are dataset-publisher declarations, not independently verified rights clearance"])
    (OUTPUT / "validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    license_dir = OUTPUT / "sources"
    license_dir.mkdir(exist_ok=True)
    for archive, token, short in [("earphones_repository.zip", "/earphone.v1i.yolov8/", "earphone_video"),
                                  ("driver_earphones_repository.zip", "/datasets/", "driver_earphone")]:
        with zipfile.ZipFile(SOURCES / archive) as z:
            for filename in ("README.dataset.txt", "README.roboflow.txt", "data.yaml"):
                path = next(n for n in z.namelist() if token in n and n.endswith("/"+filename))
                (license_dir / (short+"_"+filename)).write_bytes(z.read(path))
    (OUTPUT / "README.md").write_text(README, encoding="utf-8")
    (OUTPUT / "selection_review.json").write_text(json.dumps(selection, indent=2), encoding="utf-8")
    archive_path = OUTPUT.with_suffix(".zip")
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as z:
        for path in sorted(OUTPUT.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(OUTPUT.parent))
    print(json.dumps(dict(output=str(OUTPUT), archive=str(archive_path), report=report)))


README = """# Strict Session device-detection starter samples

600 real source images: **300 mobile-phone images and 300 worn-earbud images**.
Prepared 2026-11-04. No synthetic images or generated augmentation were added.
This is a curated starter collection, not a production-validated detection model.

## Layout and training labels

`mobile_phone/` and `earbud_worn/` are **separate one-class YOLO detection datasets**.
Each contains images/train, images/val, images/test, matching labels folders, and
data.yaml. Every label line is `0 x_center y_center width height`, normalized to
the image width and height. Images retain their original downloaded bytes.
Each data.yaml points at its current local absolute folder. If you extract this
ZIP elsewhere, update `path` to that dataset's absolute folder before training.

Use these as two separate detector fine-tuning sets. Do not simply concatenate
them into a two-class training set: phones visible in earbud-source photos, and
earbuds possibly visible in phone-source photos, are not exhaustively annotated.
A joint detector needs a separate cross-class annotation pass first.

`manifest.jsonl` contains every source, original label mapping, bounding box,
checksum, image dimension and known grouping. `attribution.csv` provides per-image
credits and license links. `validation.json` gives exact counts and split results.
`review/` has numbered contact sheets with target boxes for all 600 images.

## Selection and validation

- Phone labels: Open Images Mobile phone, /m/050k8; excluded group-of/depiction
  annotations and malformed images/boxes, plus obvious non-phone source errors
  found in visual review. Includes legacy phones, product stills/renderings and
  hand-held examples, not exclusively people using modern phones at a webcam.
  Source validation/test images were
  repurposed into this small starter collection's own split; do not use its
  metrics as an official Open Images benchmark result.
- Worn-earbud labels: manually selected contact-sheet candidates from an
  earphone/airpods dataset, plus real frames from an earphone video dataset.
  Removed loose products, cases, over-ear headphones and obvious broad
  cable/torso boxes from the mixed source. Retained source box geometry with
  only rounding-edge clipping. Not every source box was independently redrawn.
- Generic in-ear devices include AirPods-style buds, other wireless buds and
  some ear-hook devices. Apple branding and a wearer's gender are not certified.
- Checked JPEG decoding, dimensions, finite positive normalized boxes, matching
  labels and checksums. Removed exact byte/pixel duplicates and candidates with
  perceptual-hash or horizontal-mirror distance <= 5 within each category.
  This does not prove that all visual near-duplicates have been eliminated.
- Kept known related source groups in one split. All frames from the single
  earphone video stay in TRAIN. Other video/stock groups identified during
  review were grouped too; unknown related subjects may remain. Splits are not
  certified subject-disjoint or a reliable measure of deployment generalization.

## Sources and licenses

1. **Open Images**: https://storage.googleapis.com/openimages/web/download_v6.html
   Bounding-box annotations: CC BY 4.0. Selected images individually list CC BY
   2.0 in Open Images metadata. Each image's author, original landing page and
   license are retained in attribution.csv and manifest.jsonl.
2. **Earphone Detection / ginger-ojdx6**, version 1:
   https://universe.roboflow.com/ginger-ojdx6/earphone-detection-75qzd/dataset/1
   Public mirror:
   https://github.com/bsaisoumith/Driver-Earphone-Detection-using-YOLOv5
   Dataset publisher declares CC BY 4.0. Only dataset images, labels and metadata
   were read; no upstream code or model weights were executed.
3. **earphone / test-earphones**, version 1:
   https://universe.roboflow.com/test-earphones/earphone-nwwsh-ja3nu/dataset/1
   Public mirror: https://github.com/LynnLox/earphone-detection/tree/dataset_merge
   Dataset publisher declares CC BY 4.0. Source README states no augmentation.

Licenses: https://creativecommons.org/licenses/by/2.0/ and
https://creativecommons.org/licenses/by/4.0/ . Original earbud-source metadata
is copied into sources/. Upstream dataset declarations do not establish the
rights to every underlying earbud photograph independently; review attribution
and provenance before publishing or commercial redistribution. No additional
license or ownership claim is made over the source images.

## Material limitations and next steps

The earbud class is dominated by a single video, repeated subjects and close-up
ears. It needs more independently consented, diverse webcam-distance examples,
lighting/pose variation, partially occluded ears and small black/white earbuds.
Neither set supplies a balanced negative/background collection. Add empty ears,
glasses, earrings, hair occlusion and phone-like objects as negatives and obtain
an independent, manually checked validation set before enabling real violations.
An image model cannot reliably prove an unseen earbud is absent or identify a
specific brand from every webcam frame. Do not treat sample count as accuracy.

No model was trained or deployed. EXE/APK code, running services, score rules,
user records and app data were not changed by this dataset preparation.

Reproduction: tools/prepare_proctoring_samples.py plus the downloaded source
cache and approved_selection.json. Seeds and input references are in the manifest.
"""


if __name__ == "__main__":
    main()
