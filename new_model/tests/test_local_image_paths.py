from pathlib import Path

import pytest
from PIL import Image

from local_image_paths import LocalImageInputError, resolve_local_image


def _make_image(path: Path):
    Image.new("RGB", (12, 9), "teal").save(path, format="PNG")
    return path


def test_accepts_only_readable_images_inside_explicit_root(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    image = _make_image(root / "proof.png")
    assert resolve_local_image("proof.png", allowed_roots=(root,), relative_to=root) == image.resolve()
    with pytest.raises(LocalImageInputError):
        resolve_local_image("https://example.invalid/image.png", allowed_roots=(root,), relative_to=root)


@pytest.mark.parametrize("candidate", ["../outside.png", "sub/../../outside.png"])
def test_rejects_parent_traversal_even_if_normalization_would_be_inside(tmp_path, candidate):
    root = tmp_path / "allowed"
    root.mkdir()
    with pytest.raises(LocalImageInputError):
        resolve_local_image(candidate, allowed_roots=(root,), relative_to=root)


def test_rejects_absolute_outside_and_missing_or_non_image_paths(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = _make_image(tmp_path / "outside.png")
    with pytest.raises(LocalImageInputError):
        resolve_local_image(outside, allowed_roots=(root,))
    with pytest.raises(LocalImageInputError):
        resolve_local_image(root / "missing.png", allowed_roots=(root,))
    malformed = root / "not-image.png"
    malformed.write_text("not an image", encoding="utf-8")
    with pytest.raises(LocalImageInputError):
        resolve_local_image(malformed, allowed_roots=(root,))


def test_rejects_symlink_escape_when_supported(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = _make_image(tmp_path / "outside.png")
    link = root / "escape.png"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks unavailable on this Windows configuration: {type(exc).__name__}")
    with pytest.raises(LocalImageInputError):
        resolve_local_image(link, allowed_roots=(root,))
