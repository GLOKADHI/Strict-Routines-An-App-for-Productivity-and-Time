from pathlib import Path

from PIL import Image

from app.schemas import AnalyzeResponse, ImageMetadata


QUESTION_TYPES = ("describe", "color", "shape", "count")
COLORS = ("red", "green", "blue", "yellow", "purple", "orange")
SHAPES = ("circle", "square", "triangle", "rectangle")
COUNT_LABELS = (1, 2, 3)


class QuestionTokenizer:
    """Small deterministic character tokenizer owned by this project."""

    PAD = 0
    UNK = 1
    ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789 ?,."

    def __init__(self, max_length: int = 96):
        self.max_length = max_length
        self._ids = {character: index + 2 for index, character in enumerate(self.ALPHABET)}

    @property
    def vocab_size(self) -> int:
        return len(self._ids) + 2

    def encode(self, text: str) -> list[int]:
        encoded = [self._ids.get(character, self.UNK) for character in text.lower()[: self.max_length]]
        return encoded + [self.PAD] * (self.max_length - len(encoded))


def image_to_tensor(image: Image.Image, size: int = 128):
    """Convert a PIL image to normalized CHW tensor without torchvision."""
    import torch

    resized = image.convert("RGB").resize((size, size), Image.Resampling.BILINEAR)
    values = torch.tensor(list(resized.tobytes()), dtype=torch.float32)
    tensor = values.view(size, size, 3).permute(2, 0, 1) / 255.0
    return (tensor - 0.5) / 0.5


class TinyVisionLanguageModel:
    """A compact CNN + GRU fusion model initialized and trained from scratch."""

    def __new__(
        cls,
        vocab_size: int,
        object_classes: int | None = None,
        question_type_classes: int = len(QUESTION_TYPES),
    ):
        import torch
        import torch.nn as nn

        class _Model(nn.Module):
            def __init__(self, vocab_size: int, object_classes: int | None, question_type_classes: int):
                super().__init__()
                self.vision = nn.Sequential(
                    nn.Conv2d(3, 32, 3, padding=1),
                    nn.BatchNorm2d(32),
                    nn.GELU(),
                    nn.MaxPool2d(2),
                    nn.Conv2d(32, 64, 3, padding=1),
                    nn.BatchNorm2d(64),
                    nn.GELU(),
                    nn.MaxPool2d(2),
                    nn.Conv2d(64, 128, 3, padding=1),
                    nn.BatchNorm2d(128),
                    nn.GELU(),
                    nn.MaxPool2d(2),
                    nn.Conv2d(128, 192, 3, padding=1),
                    nn.GELU(),
                    nn.AdaptiveAvgPool2d(1),
                )
                self.question_embedding = nn.Embedding(vocab_size, 64, padding_idx=QuestionTokenizer.PAD)
                self.question_encoder = nn.GRU(64, 128, batch_first=True)
                self.fusion = nn.Sequential(
                    nn.Linear(192 + 128, 256),
                    nn.GELU(),
                    nn.Dropout(0.1),
                )
                self.question_type_head = nn.Linear(256, question_type_classes)
                self.color_head = nn.Linear(256, len(COLORS))
                self.shape_head = nn.Linear(256, len(SHAPES))
                self.count_head = nn.Linear(256, len(COUNT_LABELS))
                self.object_head = nn.Linear(256, object_classes) if object_classes else None

            def forward(self, images, questions):
                visual = self.vision(images).flatten(1)
                encoded, _ = self.question_encoder(self.question_embedding(questions))
                mask = questions.ne(QuestionTokenizer.PAD).unsqueeze(-1)
                question_features = (encoded * mask).sum(dim=1) / mask.sum(dim=1).clamp_min(1)
                fused = self.fusion(torch.cat([visual, question_features], dim=1))
                outputs = {
                    "question_type": self.question_type_head(fused),
                    "color": self.color_head(fused),
                    "shape": self.shape_head(fused),
                    "count": self.count_head(fused),
                }
                if self.object_head is not None:
                    outputs["object"] = self.object_head(fused)
                return outputs

        return _Model(vocab_size, object_classes, question_type_classes)


class NativeVisionLanguageService:
    def __init__(self, checkpoint_path: str, min_confidence: float = 0.90):
        self.checkpoint_path = Path(checkpoint_path)
        self.min_confidence = min_confidence
        self.tokenizer = QuestionTokenizer()
        self.device = None
        self.model = None

    def _load(self) -> None:
        if self.model is not None:
            return

        try:
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "PyTorch is not installed. Run: pip install -e \".[native]\""
            ) from exc

        if not self.checkpoint_path.exists():
            raise RuntimeError(
                f"Native checkpoint not found at {self.checkpoint_path}. "
                "Run: python -m training.train_tiny_vlm"
            )

        checkpoint = torch.load(self.checkpoint_path, map_location="cpu", weights_only=True)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = TinyVisionLanguageModel(self.tokenizer.vocab_size)
        self.model.load_state_dict(checkpoint["model"])
        self.model.to(self.device).eval()

    def analyze(self, image: Image.Image, question: str, metadata: ImageMetadata) -> AnalyzeResponse:
        self._load()
        import torch

        image_tensor = image_to_tensor(image).unsqueeze(0).to(self.device)
        question_tensor = torch.tensor([self.tokenizer.encode(question)], dtype=torch.long, device=self.device)
        with torch.inference_mode():
            outputs = self.model(image_tensor, question_tensor)

        question_type = int(outputs["question_type"].argmax(dim=-1).item())
        confidence = float(torch.softmax(outputs["question_type"], dim=-1).max().item())
        color = COLORS[int(outputs["color"].argmax(dim=-1).item())]
        shape = SHAPES[int(outputs["shape"].argmax(dim=-1).item())]
        count = COUNT_LABELS[int(outputs["count"].argmax(dim=-1).item())]
        plural = "s" if count != 1 else ""
        subject = f"{count} {color} {shape}{plural}"

        if QUESTION_TYPES[question_type] == "color":
            answer = f"The shape is {color}."
        elif QUESTION_TYPES[question_type] == "shape":
            answer = f"The shape is a {shape}."
        elif QUESTION_TYPES[question_type] == "count":
            answer = f"There are {count} shapes."
        else:
            answer = f"The image shows {subject}."

        return AnalyzeResponse(
            analysis_status="uncertain" if confidence < self.min_confidence else "success",
            description=f"The image shows {subject}.",
            objects=[f"{color} {shape}"],
            actions=[],
            text=[],
            events=[],
            answer=answer,
            confidence_threshold=self.min_confidence,
            needs_review=confidence < self.min_confidence,
            confidence=confidence,
            metadata=metadata,
        )
