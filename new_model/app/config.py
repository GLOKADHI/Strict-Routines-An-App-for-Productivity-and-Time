from functools import lru_cache
import sys
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_checkpoint_path() -> str:
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        bundled = bundle_root / "checkpoints" / "realistic_vlm_phase4.pt"
        if bundled.exists():
            return str(bundled)
        return str(Path(sys.executable).resolve().parent / "checkpoints" / "realistic_vlm_phase4.pt")
    return str(Path(__file__).resolve().parent.parent / "checkpoints" / "realistic_vlm_phase4.pt")


class Settings(BaseSettings):
    app_name: str = "Vision-Language V1"
    model_backend: Literal["mock", "native", "realistic", "screenshot_ocr"] = "realistic"
    checkpoint_path: str = _default_checkpoint_path()
    min_confidence: float = 0.90
    max_image_bytes: int = 10 * 1024 * 1024
    max_image_side: int = 1536

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
