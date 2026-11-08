"""Optional separate localhost adapter; the existing /analyze and Windows server are untouched."""
import json
import logging
import re
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from bounded_multipart import EvidenceUploadRoute
from local_api_security import (LoopbackRequestGuardMiddleware,
                                MAX_EVIDENCE_REQUEST_BYTES,
                                RequestBodyLimitMiddleware)
from local_image_paths import LocalImageInputError, resolve_local_image
from .pipeline import Attachment, EvidencePipeline

app = FastAPI(title="Local evidence analysis adapter", version="1.0")
app.router.route_class = EvidenceUploadRoute
app.add_middleware(LoopbackRequestGuardMiddleware)
app.add_middleware(RequestBodyLimitMiddleware,
                   path_limits={"/evidence/analyze": MAX_EVIDENCE_REQUEST_BYTES})
pipeline = EvidencePipeline(Path(__file__).resolve().parents[1]/"output"/"evidence_analysis")
logger = logging.getLogger(__name__)


@app.get("/health")
def health():
    return {"status": "ok", "analysis_only": True, "scoring": False,
            "ocr_engine": pipeline.ocr.model_version, "vision_engine": pipeline.vision.model_version}


@app.post("/evidence/analyze")
async def analyze(request: Request, images: list[UploadFile] = File(...),
                  task_occurrence_id: str = Form(...), batch_id: str = Form(...),
                  image_ids: str = Form(...), kinds: str = Form("[]")):
    request_id = request.headers.get("x-request-id", "")
    if not request_id or len(request_id) > 64 or not re.fullmatch(r"[A-Za-z0-9._-]+", request_id):
        request_id = uuid4().hex
    try:
        ids = json.loads(image_ids)
        image_kinds = json.loads(kinds)
        if not isinstance(ids, list) or len(ids) != len(images) or any(not isinstance(i, str) or not i for i in ids):
            raise ValueError("image_ids must list one nonempty stable ID per image.")
        if (len(set(ids)) != len(ids) or not task_occurrence_id or not batch_id
                or len(task_occurrence_id) > 256 or len(batch_id) > 256
                or any(len(i) > 256 for i in ids)):
            raise ValueError("Distinct image IDs, task occurrence ID and batch ID are required.")
        if not image_kinds:
            image_kinds = ["photo"]*len(images)
        if len(image_kinds) != len(images) or any(k not in ("photo", "screenshot", "document") for k in image_kinds):
            raise ValueError("kinds must match images and use photo, screenshot or document.")
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if len(images) > pipeline.policy.max_files or not images:
        raise HTTPException(413, "Select 1–5 images; maximum 25 MiB each and 125 MiB aggregate.")
    scratch = pipeline.store_dir / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=scratch, prefix="batch-") as temp:
            attachments = []
            total = 0
            for index, upload in enumerate(images):
                name = Path((upload.filename or "image").replace("\\", "/")).name
                safe_name = "".join(c for c in name if c.isalnum() or c in "._- ")[:128] or "image"
                path = Path(temp)/str(index)/safe_name
                path.parent.mkdir()
                size = 0
                with path.open("wb") as out:
                    while chunk := await upload.read(65536):
                        size += len(chunk)
                        total += len(chunk)
                        if size > pipeline.policy.max_image_bytes or total > pipeline.policy.max_aggregate_bytes:
                            raise HTTPException(413, "Actual image bytes exceed per-file or aggregate budget.")
                        out.write(chunk)
                try:
                    resolve_local_image(path, allowed_roots=(scratch,), relative_to=scratch,
                                        max_bytes=pipeline.policy.max_image_bytes,
                                        max_pixels=pipeline.policy.max_pixels)
                except LocalImageInputError as exc:
                    raise HTTPException(400, "An uploaded file is not a supported readable image.") from exc
                attachments.append(Attachment(path, ids[index], image_kinds[index], private_snapshot=True))
            result = await run_in_threadpool(pipeline.analyze_batch, attachments, task_occurrence_id, batch_id)
            logger.info("evidence_analysis request_id=%s stage=analysis status=%s count=%s analysis_only=true",
                        request_id, result["status"], len(attachments))
            return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("evidence_analysis request_id=%s stage=analysis status=failed error=%s",
                       request_id, type(exc).__name__)
        raise HTTPException(500, "Evidence analysis could not be completed.") from exc
    finally:
        for upload in images:
            await upload.close()
