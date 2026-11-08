"""Real local Tesseract TSV OCR. No desktop capture, cloud calls or DB writes."""
import csv
import io
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageOps

from .schemas import ModelUnavailable


class TesseractOCR:
    model_version = "tesseract-eng-tsv-v1"

    def __init__(self, executable: str | None = None, timeout_seconds: float = 30):
        self.executable = executable or shutil.which("tesseract")
        if not self.executable:
            candidate = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
            self.executable = str(candidate) if candidate.is_file() else None
        self.timeout_seconds = timeout_seconds

    def _run(self, image: Image.Image, args: list[str]) -> subprocess.CompletedProcess:
        if not self.executable:
            raise ModelUnavailable("Tesseract is unavailable; configure its local executable and eng/osd language data.")
        buffer = io.BytesIO()
        # Flatten transparency for OCR only; canonical bytes remain untouched.
        rgba = image.convert("RGBA")
        flat = Image.new("RGBA", rgba.size, "white")
        flat.alpha_composite(rgba)
        flat.convert("RGB").save(buffer, "PNG")
        rgba.close()
        flat.close()
        try:
            return subprocess.run([self.executable, "stdin", "stdout", *args], input=buffer.getvalue(),
                                  capture_output=True, timeout=self.timeout_seconds, check=False)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"OCR exceeded {self.timeout_seconds:g} seconds; retry this stage.") from exc
        except OSError as exc:
            raise ModelUnavailable(f"Cannot start local Tesseract: {exc}") from exc

    def analyze(self, image: Image.Image) -> dict:
        warnings: list[str] = []
        rotated = image
        rotation = 0
        # OSD is best effort: sparse/no-text photographs commonly cannot orient text.
        osd = self._run(image, ["-l", "osd", "--psm", "0"])
        orientation = (osd.stdout + osd.stderr).decode("utf-8", "replace")
        angle = re.search(r"Rotate:\s*(\d+)", orientation)
        certainty = re.search(r"Orientation confidence:\s*([\d.]+)", orientation)
        if osd.returncode == 0 and angle and certainty and float(certainty.group(1)) >= 5:
            rotation = int(angle.group(1)) % 360
            if rotation:
                rotated = image.rotate(-rotation, expand=True)
        elif osd.returncode:
            warnings.append("Text orientation was not determined; EXIF-normalized image used.")
        try:
            normalized = ImageOps.autocontrast(rotated.convert("RGB"), cutoff=0.5)
            try:
                result = self._run(normalized, ["-l", "eng", "--psm", "3", "tsv"])
            finally:
                normalized.close()
            if result.returncode:
                raise RuntimeError("Tesseract OCR failed: " + result.stderr.decode("utf-8", "replace")[-1200:])
            rows = csv.DictReader(io.StringIO(result.stdout.decode("utf-8", "replace")), delimiter="\t")
            lines: dict[tuple[int, int, int, int], list[dict]] = {}
            for row in rows:
                word = (row.get("text") or "").strip()
                if row.get("level") != "5" or not word:
                    continue
                confidence = float(row["conf"])
                if confidence < 0:
                    continue
                key = tuple(int(row[k]) for k in ("page_num", "block_num", "par_num", "line_num"))
                box = [int(row[k]) for k in ("left", "top", "width", "height")]
                lines.setdefault(key, []).append({"text": word, "bbox": box, "confidence": confidence / 100})
            blocks: dict[tuple[int, int], list[dict]] = {}
            confidences = []
            for order, (key, words) in enumerate(lines.items()):
                confidences.extend(w["confidence"] for w in words)
                left = min(w["bbox"][0] for w in words)
                top = min(w["bbox"][1] for w in words)
                right = max(w["bbox"][0] + w["bbox"][2] for w in words)
                bottom = max(w["bbox"][1] + w["bbox"][3] for w in words)
                line = {"text": " ".join(w["text"] for w in words), "bbox": [left, top, right-left, bottom-top],
                        "reading_order": order, "words": words}
                blocks.setdefault(key[:2], []).append(line)
            block_values = [{"reading_order": order, "lines": values, "text": "\n".join(v["text"] for v in values)}
                            for order, values in enumerate(blocks.values())]
            text = "\n\n".join(v["text"] for v in block_values)
            if any(c < .5 for c in confidences):
                warnings.append("Some recognized words have low engine confidence; review them against the image.")
            return {"text_detected": bool(text), "extracted_text": text, "blocks": block_values,
                    "language": None, "configured_languages": ["eng"],
                    "confidence": sum(confidences)/len(confidences) if confidences else None,
                    "confidence_source": "mean_engine_word_confidence" if confidences else None,
                    "coordinates": {"image_width": rotated.width, "image_height": rotated.height,
                                    "text_rotation_clockwise": rotation, "space": "OCR input pixels"},
                    "unreadable_regions": [], "warnings": warnings, "source": "detected_text"}
        finally:
            if rotated is not image:
                rotated.close()
