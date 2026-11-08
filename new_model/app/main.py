from functools import lru_cache
import logging
import re
from uuid import uuid4
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.config import Settings, get_settings
from app.schemas import AnalyzeResponse, ImageMetadata
from app.services.image_service import InvalidImageError, prepare_image
from app.services.model_service import VisionLanguageService
from bounded_multipart import LegacyUploadRoute
from local_api_security import (LoopbackRequestGuardMiddleware,
                                MAX_LEGACY_REQUEST_BYTES,
                                RequestBodyLimitMiddleware)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Vision-Language V1", version="0.1.0")
app.router.route_class = LegacyUploadRoute
app.add_middleware(LoopbackRequestGuardMiddleware)
app.add_middleware(RequestBodyLimitMiddleware,
                   path_limits={"/analyze": MAX_LEGACY_REQUEST_BYTES})
logger = logging.getLogger(__name__)
MAX_LEGACY_IMAGE_BYTES = 10 * 1024 * 1024


@lru_cache
def get_model_service() -> VisionLanguageService:
    return VisionLanguageService(get_settings())


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    return {"status": "ok", "backend": settings.model_backend}


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    request: Request,
    image: UploadFile = File(...),
    question: str = Form("Describe this image in detail."),
    settings: Settings = Depends(get_settings),
    service: VisionLanguageService = Depends(get_model_service),
) -> AnalyzeResponse:
    request_id = _request_id(request.headers.get("x-request-id"))
    if len(question) > 8000:
        raise HTTPException(status_code=400, detail="The analysis question exceeds the allowed length.")
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Please upload a supported image file.",
        )

    image_limit = min(settings.max_image_bytes, MAX_LEGACY_IMAGE_BYTES)
    raw = await image.read(image_limit + 1)
    if len(raw) > image_limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image exceeds the {image_limit // (1024 * 1024)} MB limit.",
        )

    try:
        prepared = prepare_image(raw, settings.max_image_side)
    except InvalidImageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    metadata = ImageMetadata(
        filename="".join(c for c in Path((image.filename or "image").replace("\\", "/")).name
                          if c.isalnum() or c in "._- ")[:128] or "image",
        content_type=image.content_type,
        original_width=prepared.original_width,
        original_height=prepared.original_height,
        width=prepared.width,
        height=prepared.height,
        format=prepared.image_format,
    )
    try:
        result = service.analyze(prepared.image, question, metadata)
        logger.info("image_analysis request_id=%s stage=inference status=%s analysis_only=true",
                    request_id, result.analysis_status)
        return result
    except RuntimeError as exc:
        logger.warning("image_analysis request_id=%s stage=inference status=failed error=%s",
                       request_id, type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Image analysis could not be completed.") from exc


def _request_id(value: str | None) -> str:
    if value and len(value) <= 64 and re.fullmatch(r"[A-Za-z0-9._-]+", value):
        return value
    return uuid4().hex
