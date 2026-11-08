from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps


SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_IMAGE_PIXELS = 40_000_000


class InvalidImageError(ValueError):
    """Raised when an upload is not a readable image."""


@dataclass(frozen=True)
class PreparedImage:
    image: Image.Image
    original_width: int
    original_height: int
    width: int
    height: int
    image_format: str | None


def prepare_image(raw: bytes, max_side: int) -> PreparedImage:
    if not raw:
        raise InvalidImageError("The uploaded file is empty.")

    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in SUPPORTED_FORMATS:
                raise InvalidImageError("Only JPEG, PNG, and WebP images are supported.")
            if getattr(source, "n_frames", 1) != 1:
                raise InvalidImageError("Animated and multi-frame images are not supported.")
            if source.width <= 0 or source.height <= 0 or source.width * source.height > MAX_IMAGE_PIXELS:
                raise InvalidImageError("The image dimensions exceed the allowed limit.")
            source.verify()
        with Image.open(BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image_format = source.format

        original_width, original_height = image.size
    except Exception as exc:  # Pillow exposes several format-specific exceptions.
        raise InvalidImageError("The uploaded file is not a readable image.") from exc

    if max(image.size) > max_side:
        scale = max_side / max(image.size)
        new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(new_size, Image.Resampling.LANCZOS)

    return PreparedImage(
        image=image,
        original_width=original_width,
        original_height=original_height,
        width=image.width,
        height=image.height,
        image_format=image_format,
    )
