from pathlib import Path

from PIL import Image

from app.schemas import AnalyzeResponse, ImageMetadata
from app.services.cifar_model import CifarVisionLanguageModel
from app.services.native_model import QuestionTokenizer, image_to_tensor


REALISTIC_CLASSES = (
    "airplane",
    "automobile",
    "bird",
    "cat",
    "deer",
    "dog",
    "frog",
    "horse",
    "ship",
    "truck",
)


class RealisticVisionLanguageService:
    """Inference adapter for the from-scratch CIFAR-10 object classifier."""

    def __init__(self, checkpoint_path: str, min_confidence: float = 0.90):
        self.checkpoint_path = Path(checkpoint_path)
        self.min_confidence = min_confidence
        self.tokenizer = QuestionTokenizer()
        self.model = None
        self.device = None

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
                f"Realistic checkpoint not found at {self.checkpoint_path}. "
                "Run: python -m training.train_realistic_vlm"
            )

        checkpoint = torch.load(self.checkpoint_path, map_location="cpu", weights_only=True)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = CifarVisionLanguageModel(self.tokenizer.vocab_size, num_classes=len(REALISTIC_CLASSES))
        self.model.load_state_dict(checkpoint["model"])
        self.model.to(self.device).eval()

    def analyze(self, image: Image.Image, question: str, metadata: ImageMetadata) -> AnalyzeResponse:
        self._load()
        import torch

        image_tensor = image_to_tensor(image, size=32).unsqueeze(0).to(self.device)
        question_tensor = torch.tensor([self.tokenizer.encode(question)], dtype=torch.long, device=self.device)
        with torch.inference_mode():
            outputs = self.model(image_tensor, question_tensor)

        probabilities = torch.softmax(outputs["object"], dim=-1)[0]
        top_confidences, top_indices = probabilities.topk(min(3, len(REALISTIC_CLASSES)))
        label = REALISTIC_CLASSES[int(top_indices[0].item())]
        confidence = float(top_confidences[0].item())
        top_predictions = [
            {"label": REALISTIC_CLASSES[int(index.item())], "confidence": float(score.item())}
            for score, index in zip(top_confidences, top_indices)
        ]
        confident = confidence >= self.min_confidence
        if confident:
            answer = f"This image most likely shows a {label}."
        else:
            answer = (
                f"Low-confidence result: {label} ({confidence:.1%}). "
                "Review the top predictions before relying on this answer."
            )
        return AnalyzeResponse(
            analysis_status="success" if confident else "uncertain",
            description=f"Top visual prediction: {label}. The model also considered "
            + ", ".join(f"{item['label']} ({item['confidence']:.1%})" for item in top_predictions[1:])
            + ".",
            objects=[label],
            actions=[],
            text=[],
            events=[],
            answer=answer,
            confidence=confidence,
            confidence_threshold=self.min_confidence,
            needs_review=not confident,
            top_predictions=top_predictions,
            metadata=metadata,
        )
