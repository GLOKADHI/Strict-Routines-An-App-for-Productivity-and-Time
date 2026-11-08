"""Create a small public-image and authored-text demonstration dataset."""
import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASELINE = ROOT.parent

TEXT_TRAIN = [
    ("Read the text: 'A red bicycle is beside a blue car.' What color is the bicycle?", "The bicycle is red."),
    ("Read the text: 'Mira has two cats and one dog.' How many animals does Mira have?", "Mira has three animals."),
    ("Summarize in one sentence: 'Ravi watered the plants. He swept the patio. Then he went inside.'", "Ravi watered the plants and swept the patio before going inside."),
    ("Classify this sentence as positive or negative: 'The clear instructions made this task easy.'", "Positive."),
    ("Extract the two objects: 'A cup and a notebook are on the table.'", "Cup; notebook."),
    ("Read the text: 'The train leaves at 8:15 AM and arrives at 9:00 AM.' How long is the trip?", "The trip lasts 45 minutes."),
    ("Rewrite politely: 'Send me the file now.'", "Please send me the file when you can."),
    ("Read the text: 'The lamp is to the left of the chair.' Where is the lamp?", "The lamp is to the left of the chair."),
    ("Translate this simple instruction into Spanish: 'Open the door.'", "Abre la puerta."),
    ("Read the text: 'The museum is closed on Monday.' Is the museum open on Monday?", "No, the museum is closed on Monday."),
    ("Return only the number of items in this list: apple, pear, mango, banana.", "4"),
    ("Read the text: 'Sara borrowed a book from Leo.' Who lent the book?", "Leo lent the book."),
    ("Explain in one short sentence why a helmet is useful when cycling.", "A helmet helps protect your head if you fall."),
    ("Read the text: 'The parcel contains a shirt, socks and a hat.' List the contents as JSON.", '["shirt", "socks", "hat"]'),
    ("Read the text: 'We walked to the park because it was sunny.' Why did they walk to the park?", "They walked to the park because it was sunny."),
    ("Fix the spelling: 'The cat is sleping on the chare.'", "The cat is sleeping on the chair."),
    ("Classify this sentence as positive or negative: 'The broken handle made the door difficult to open.'", "Negative."),
    ("Read the text: 'Three birds landed on a branch. One flew away.' How many birds remain?", "Two birds remain."),
    ("Read the text: 'The box is empty.' What is inside the box?", "Nothing is inside the box."),
    ("Read the text: 'Arun is older than Nina.' Who is younger?", "Nina is younger."),
]
TEXT_VALIDATION = [
    ("Read the text: 'A green bus stops beside a yellow building.' What color is the bus?", "The bus is green."),
    ("Read the text: 'There are five oranges. Two are eaten.' How many oranges remain?", "Three oranges remain."),
    ("Extract the objects: 'A fork and a plate are on the counter.'", "Fork; plate."),
    ("Summarize: 'Ana packed her bag. Then she took the bus to school.'", "Ana packed her bag and took the bus to school."),
    ("Read the text: 'The shop opens at 10:00 AM.' Is it open at 9:00 AM?", "No, the shop has not opened at 9:00 AM."),
    ("Classify as positive or negative: 'The new shoes are comfortable.'", "Positive."),
]


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def main():
    manifest_path = BASELINE / "data" / "cifar10_phase3" / "manifest.json"
    source = json.loads(manifest_path.read_text(encoding="utf-8"))
    grouped = defaultdict(list)
    for row in source:
        grouped[(row["split"], row["class_name"])].append(row)
    classes = sorted({row["class_name"] for row in source})
    image_rows = {}
    provenance = []
    for destination, split, per_class in [("train", "train", 3), ("validation", "test", 2)]:
        selected = []
        for index in range(per_class):
            for label in classes:
                rows = sorted(grouped[(split, label)], key=lambda r: r["source_index"])
                row = rows[index]
                original = BASELINE / row["path"]
                relative = Path("data") / "images" / destination / f"{label}_{index:02d}.png"
                target = ROOT / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, target)
                selected.append({"id": f"{destination}-image-{label}-{index}", "modality": "image",
                                 "image": relative.as_posix(), "question": "What is the main object in this image? Answer with its category.",
                                 "answer": label, "source": "CIFAR-10", "source_split": split,
                                 "source_index": row["source_index"]})
                provenance.append({"image": relative.as_posix(), "original": str(original),
                                   "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        image_rows[destination] = selected
    for destination, texts in [("train", TEXT_TRAIN), ("validation", TEXT_VALIDATION)]:
        text_rows = [{"id": f"{destination}-text-{i:02d}", "modality": "text", "question": q,
                      "answer": a, "source": "Authored demonstration example"}
                     for i, (q, a) in enumerate(texts)]
        # Interleave so even the first twenty updates see all ten image classes and text.
        mixed = []
        for i in range(max(len(image_rows[destination]), len(text_rows))):
            if i < len(image_rows[destination]):
                mixed.append(image_rows[destination][i])
            if i < len(text_rows):
                mixed.append(text_rows[i])
        write_jsonl(ROOT / "data" / f"{destination}.jsonl", mixed)
    card = {"purpose": "Small supervised fine-tuning prototype, not a general image-understanding benchmark",
            "image_source": "https://www.cs.toronto.edu/~kriz/cifar.html",
            "image_resolution": "Original 32x32 CIFAR-10 images, resized by the Qwen processor",
            "train": {"image": 30, "text": len(TEXT_TRAIN)},
            "validation": {"image": 20, "text": len(TEXT_VALIDATION)},
            "split_policy": "CIFAR training images for training; CIFAR test images for validation. Text passages are distinct.",
            "private_data": "No application database, personal desktop recordings or user screenshots used.",
            "images": provenance}
    (ROOT / "data" / "dataset_manifest.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    print(json.dumps({"train": card["train"], "validation": card["validation"]}), flush=True)


if __name__ == "__main__":
    main()
