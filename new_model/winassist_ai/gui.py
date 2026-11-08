"""Premium local desktop client for the repository-owned vision model."""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path
from typing import Any

from PIL import Image, ImageTk

from app.config import Settings
from app.schemas import ImageMetadata
from app.services.image_service import InvalidImageError, prepare_image
from app.services.model_service import VisionLanguageService
from local_image_paths import DEFAULT_IMAGE_ROOTS, resolve_local_image


def analyze_image_file(
    image_path: str | Path,
    question: str,
    service: VisionLanguageService | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Analyze one local image through the same service used by the API."""
    settings = settings or Settings()
    service = service or VisionLanguageService(settings)
    path = resolve_local_image(image_path, allowed_roots=DEFAULT_IMAGE_ROOTS)
    raw = path.read_bytes()
    try:
        prepared = prepare_image(raw, settings.max_image_side)
    except InvalidImageError as exc:
        raise ValueError(str(exc)) from exc

    metadata = ImageMetadata(
        filename=path.name,
        content_type=None,
        original_width=prepared.original_width,
        original_height=prepared.original_height,
        width=prepared.width,
        height=prepared.height,
        format=prepared.image_format,
    )
    response = service.analyze(prepared.image, question, metadata)
    return response.model_dump()


class VisionModelGUI:
    BG = "#0a0f1c"
    PANEL = "#111827"
    CARD = "#172235"
    CARD_ALT = "#1c2b43"
    FIELD = "#0e1728"
    BORDER = "#293b58"
    TEXT = "#f4f7fb"
    MUTED = "#93a4bd"
    ACCENT = "#62b0ff"
    ACCENT_DARK = "#0b1728"
    GOOD = "#35d39a"
    WARN = "#f4c95d"

    def __init__(self, settings: Settings | None = None):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk = tk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.ttk = ttk
        self.settings = settings or Settings()
        self.service = VisionLanguageService(self.settings)
        self.image_path: Path | None = None
        self.preview_image = None

        self.root = tk.Tk()
        self.root.title("WinAssist Vision")
        self.root.geometry("1180x780")
        self.root.minsize(940, 650)
        self.root.configure(bg=self.BG)

        self.status = tk.StringVar(value="Select an image to begin.")
        self.question = tk.StringVar(value="What object is in this image?")
        self.file_label = tk.StringVar(value="No image selected")
        self.prediction = tk.StringVar(value="Waiting for an image")
        self.answer = tk.StringVar(value="Your model response will appear here.")
        self.confidence = tk.StringVar(value="—")
        self.review = tk.StringVar(value="READY")
        self.image_meta = tk.StringVar(value="PNG  •  — × —")
        self.model_label = tk.StringVar(value="LOCAL MODEL  /  PHASE 4")

        self._configure_styles()
        self._build()

    def _configure_styles(self) -> None:
        style = self.ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("App.TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.CARD)
        style.configure("Title.TLabel", background=self.BG, foreground=self.TEXT, font=("Segoe UI", 24, "bold"))
        style.configure("Subtitle.TLabel", background=self.BG, foreground=self.MUTED, font=("Segoe UI", 10))
        style.configure("CardText.TLabel", background=self.CARD, foreground=self.MUTED, font=("Segoe UI", 9))
        style.configure("Value.TLabel", background=self.CARD, foreground=self.TEXT, font=("Segoe UI", 15, "bold"))
        style.configure("Meta.TLabel", background=self.CARD, foreground=self.MUTED, font=("Segoe UI", 9))
        style.configure("Accent.TButton", background=self.ACCENT, foreground=self.ACCENT_DARK, borderwidth=0, padding=(16, 10), font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#8bc9ff"), ("disabled", "#40536d")])
        style.configure("Secondary.TButton", background=self.CARD_ALT, foreground=self.TEXT, borderwidth=0, padding=(12, 9), font=("Segoe UI", 9, "bold"))
        style.map("Secondary.TButton", background=[("active", "#2b4162")])
        style.configure("TEntry", fieldbackground=self.FIELD, foreground=self.TEXT, insertcolor=self.TEXT, bordercolor=self.BORDER, padding=10)

    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root, style="App.TFrame", padding=(28, 24, 28, 16))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="WinAssist Vision", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.model_label, style="Subtitle.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(header, text="PRIVATE  •  OFFLINE  •  FROM-SCRATCH MODEL", style="Subtitle.TLabel").grid(row=0, column=1, rowspan=2, sticky="e")

        content = ttk.Frame(self.root, style="App.TFrame", padding=(28, 0, 28, 20))
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=5)
        content.columnconfigure(1, weight=6)
        content.rowconfigure(1, weight=1)

        input_card = ttk.Frame(content, style="Card.TFrame", padding=18)
        input_card.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 16))
        input_card.columnconfigure(1, weight=1)
        ttk.Label(input_card, text="ANALYSIS INPUT", style="CardText.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Button(input_card, text="Choose image", style="Secondary.TButton", command=self.choose_image).grid(row=1, column=0, sticky="w", pady=(10, 0), padx=(0, 12))
        ttk.Label(input_card, textvariable=self.file_label, style="CardText.TLabel").grid(row=1, column=1, sticky="w", pady=(10, 0))
        ttk.Button(input_card, text="Clear", style="Secondary.TButton", command=self.clear).grid(row=1, column=2, sticky="e", pady=(10, 0))
        ttk.Label(input_card, text="Question", style="CardText.TLabel").grid(row=2, column=0, sticky="w", pady=(14, 0))
        self.question_entry = ttk.Entry(input_card, textvariable=self.question)
        self.question_entry.grid(row=2, column=1, sticky="ew", pady=(14, 0))
        self.question_entry.bind("<Return>", lambda _event: self.analyze())
        self.analyze_button = ttk.Button(input_card, text="Analyze image  →", style="Accent.TButton", command=self.analyze)
        self.analyze_button.grid(row=2, column=2, sticky="e", pady=(14, 0), padx=(12, 0))

        preview_card = ttk.Frame(content, style="Card.TFrame", padding=18)
        preview_card.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        preview_card.rowconfigure(2, weight=1)
        preview_card.columnconfigure(0, weight=1)
        ttk.Label(preview_card, text="IMAGE PREVIEW", style="CardText.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(preview_card, textvariable=self.image_meta, style="Meta.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 12))
        self.preview_surface = tk.Frame(preview_card, bg=self.FIELD, highlightbackground=self.BORDER, highlightthickness=1)
        self.preview_surface.grid(row=2, column=0, sticky="nsew")
        self.preview_surface.rowconfigure(0, weight=1)
        self.preview_surface.columnconfigure(0, weight=1)
        self.preview = tk.Label(self.preview_surface, text="Drop an image here\nor choose one above", bg=self.FIELD, fg=self.MUTED, font=("Segoe UI", 12), justify="center")
        self.preview.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        result_card = ttk.Frame(content, style="Card.TFrame", padding=18)
        result_card.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        result_card.rowconfigure(5, weight=1)
        result_card.columnconfigure(0, weight=1)
        ttk.Label(result_card, text="MODEL RESPONSE", style="CardText.TLabel").grid(row=0, column=0, sticky="w")
        response_header = ttk.Frame(result_card, style="Card.TFrame")
        response_header.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        response_header.columnconfigure(0, weight=1)
        ttk.Label(response_header, textvariable=self.prediction, style="Value.TLabel").grid(row=0, column=0, sticky="w")
        self.review_label = tk.Label(response_header, textvariable=self.review, bg=self.CARD_ALT, fg=self.MUTED, font=("Segoe UI", 8, "bold"), padx=10, pady=5)
        self.review_label.grid(row=0, column=1, sticky="e")
        ttk.Label(result_card, textvariable=self.answer, style="CardText.TLabel", wraplength=500, justify="left").grid(row=2, column=0, sticky="w", pady=(8, 12))
        self.confidence_canvas = tk.Canvas(result_card, height=14, bg=self.FIELD, bd=0, highlightthickness=0)
        self.confidence_canvas.grid(row=3, column=0, sticky="ew")
        confidence_row = ttk.Frame(result_card, style="Card.TFrame")
        confidence_row.grid(row=4, column=0, sticky="ew", pady=(7, 12))
        confidence_row.columnconfigure(0, weight=1)
        ttk.Label(confidence_row, text="CONFIDENCE", style="CardText.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(confidence_row, textvariable=self.confidence, style="Value.TLabel").grid(row=0, column=1, sticky="e")

        json_frame = tk.Frame(result_card, bg=self.FIELD, highlightbackground=self.BORDER, highlightthickness=1)
        json_frame.grid(row=5, column=0, sticky="nsew")
        json_frame.rowconfigure(0, weight=1)
        json_frame.columnconfigure(0, weight=1)
        self.result = tk.Text(json_frame, wrap="word", state="disabled", bg=self.FIELD, fg="#d8e3f2", insertbackground=self.TEXT, relief="flat", bd=0, padx=12, pady=10, font=("Cascadia Mono", 9))
        self.result.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(json_frame, orient="vertical", command=self.result.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.result.configure(yscrollcommand=scrollbar.set)

        footer = ttk.Frame(self.root, style="App.TFrame", padding=(28, 0, 28, 16))
        footer.grid(row=2, column=0, sticky="ew")
        ttk.Label(footer, textvariable=self.status, style="Subtitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(footer, text="Phase 4  •  84.0% accuracy  •  63.4% ≥90% coverage", style="Subtitle.TLabel").grid(row=0, column=1, sticky="e")

    def choose_image(self) -> None:
        filename = self.filedialog.askopenfilename(title="Choose an image", initialdir=str(DEFAULT_IMAGE_ROOTS[0]),
                                                   filetypes=[("Images", "*.png *.jpg *.jpeg *.webp")])
        if not filename:
            return
        try:
            self.image_path = resolve_local_image(filename, allowed_roots=DEFAULT_IMAGE_ROOTS)
        except Exception:
            self.image_path = None
            self.status.set("Choose a readable image from the authorized project image folders.")
            self.preview.configure(image="", text="Image path is not authorized")
            return
        self.file_label.set(str(self.image_path))
        try:
            image = Image.open(self.image_path).convert("RGB")
            original_size = image.size
            image.thumbnail((480, 420))
            self.preview_image = ImageTk.PhotoImage(image)
            self.preview.configure(image=self.preview_image, text="")
            self.image_meta.set(f"{self.image_path.suffix.upper().replace('.', '') or 'IMAGE'}  •  {original_size[0]} × {original_size[1]}")
            self.status.set("Image ready. Ask a question and analyze.")
        except Exception:
            self.preview.configure(image="", text="Could not preview image")
            self.status.set("The authorized image could not be previewed.")

    def clear(self) -> None:
        self.image_path = None
        self.preview_image = None
        self.file_label.set("No image selected")
        self.image_meta.set("PNG  •  — × —")
        self.preview.configure(image="", text="Drop an image here\nor choose one above")
        self.prediction.set("Waiting for an image")
        self.answer.set("Your model response will appear here.")
        self.confidence.set("—")
        self.review.set("READY")
        self.review_label.configure(bg=self.CARD_ALT, fg=self.MUTED)
        self.confidence_canvas.delete("all")
        self._set_result({})
        self.status.set("Select an image to begin.")

    def _set_result(self, payload: dict[str, Any]) -> None:
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", json.dumps(payload, indent=2) if payload else "No response yet.")
        self.result.configure(state="disabled")

    def _set_confidence(self, confidence: float | None, needs_review: bool) -> None:
        self.confidence_canvas.delete("all")
        width = max(self.confidence_canvas.winfo_width(), 100)
        self.confidence_canvas.create_rectangle(0, 0, width, 14, fill=self.FIELD, outline="")
        if confidence is not None:
            fill = self.WARN if needs_review else self.GOOD
            self.confidence_canvas.create_rectangle(0, 0, width * max(0.0, min(1.0, confidence)), 14, fill=fill, outline="")

    def analyze(self) -> None:
        if self.image_path is None:
            self.messagebox.showinfo("Choose an image", "Choose an image before analyzing.")
            return
        self.analyze_button.configure(state="disabled")
        self.status.set("Analyzing with the local model…")
        image_path = self.image_path
        question = self.question.get().strip() or "What object is in this image?"

        def worker() -> None:
            try:
                payload = analyze_image_file(image_path, question, self.service, self.settings)
                confidence = payload.get("confidence")
                needs_review = payload.get("needs_review", True)
                message = "Analysis complete." if confidence is None else f"Analysis complete: confidence {confidence:.1%}." + (" Review recommended." if needs_review else "")
                self.root.after(0, lambda: self._complete(payload, message))
            except Exception as exc:
                self.root.after(0, lambda: self._failed(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _complete(self, payload: dict[str, Any], message: str) -> None:
        label = (payload.get("objects") or ["Unknown"])[0]
        confidence = payload.get("confidence")
        needs_review = bool(payload.get("needs_review", True))
        self.prediction.set(f"Top prediction  /  {label}")
        self.answer.set(payload.get("answer") or payload.get("description") or "No answer returned.")
        self.confidence.set(f"{confidence:.1%}" if confidence is not None else "—")
        self.review.set("REVIEW" if needs_review else "CONFIDENT")
        self.review_label.configure(bg="#4b3920" if needs_review else "#123d35", fg=self.WARN if needs_review else self.GOOD)
        self._set_confidence(confidence, needs_review)
        self._set_result(payload)
        self.status.set(message)
        self.analyze_button.configure(state="normal")

    def _failed(self, exc: Exception) -> None:
        self.status.set(f"Analysis failed: {exc}")
        self.analyze_button.configure(state="normal")
        self.messagebox.showerror("Model error", str(exc))

    def run(self) -> None:
        self.root.mainloop()


def self_test(image_path: str | Path) -> dict[str, Any]:
    payload = analyze_image_file(image_path, "What object is in this image?")
    print(json.dumps(payload, indent=2))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local vision model GUI.")
    parser.add_argument("--self-test", metavar="IMAGE", help="Analyze one image and print JSON without opening the GUI.")
    args = parser.parse_args()
    if args.self_test:
        self_test(args.self_test)
        return
    VisionModelGUI().run()


if __name__ == "__main__":
    main()
