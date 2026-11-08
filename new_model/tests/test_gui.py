from pathlib import Path

from winassist_ai.gui import analyze_image_file


def test_gui_inference_helper_returns_structured_response() -> None:
    image_path = next(Path("data/cifar10_phase3/test").glob("*/*.png"))
    payload = analyze_image_file(image_path, "What object is in this image?")
    assert payload["answer"]
    assert payload["objects"]
    assert 0.0 <= payload["confidence"] <= 1.0
    assert payload["top_predictions"]
