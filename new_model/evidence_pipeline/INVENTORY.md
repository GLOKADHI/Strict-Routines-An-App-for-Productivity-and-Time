# Evidence feature inventory — before implementation (2026-10-14)

The existing directory is `new_model`. This inventory was created after a read-only inspection and before implementation.

## Existing capabilities

| Component | Actual capability | Limitation / preservation decision |
|---|---|---|
| `tiny_vlm.pt` (1,929,693 bytes) | Synthetic CNN + question GRU; shape, color and count heads | Synthetic shapes only; preserve checkpoint and modules. |
| `realistic_vlm*.pt` (four ~11.87 MB files) | Residual CNN + question GRU, ten CIFAR object classes, 32 px input | No OCR, localization, general captioning or garment analysis. Preserve assets and legacy imports. |
| `app/services/realistic_model.py` | Torch lazy load, softmax top three predictions and templated answer | Classifier output is a domain-limited hypothesis, not proof verification. |
| Advertised `screenshot_ocr` backend | Config/README references only | Required module and checkpoint are absent. Do not claim a working OCR model. |
| `app/services/image_service.py` | Pillow validation, EXIF transpose, RGB conversion, 1536 px resize | Single image; no explicit pixel budget, hashes, compact storage or batch protocol. Preserve legacy API. |
| Existing FastAPI `/analyze` | Single multipart image, 10 MiB cap | No task IDs, batch, independent OCR/vision stages or proof metadata persistence. Preserve. |
| `winassist_ai/recorder` | Optional screenshot OCR via pytesseract | Desktop capture path; unsuitable for evidence uploads. Preserve. |
| Existing runtime | Pillow, NumPy, Torch, FastAPI, pytest; venv also pytesseract | System Tesseract 5.5.3 has `eng` and `osd`. System Python has ONNX Runtime; existing venv does not. Do not alter Windows dependencies. |
| Existing tests/data | Five API/GUI/recorder tests; 32 px CIFAR and synthetic data | No existing large-photo, structured OCR, integrity or batch acceptance tests. |

## Android audit

Android source starts at version 1.15.15 / code 43, min SDK 24. Gallery accepts up to five JPEG/PNG/WebP attachments, 25 MiB each. Original-byte SHA-256 and perceptual fingerprints are already used for evidence integrity. Existing local evidence files and cleanup remain authoritative.

The current `analyzeAndRecordEvidence` incorrectly reports filename/description tokens as OCR, without reading image pixels. Its cache insert also uses a column absent from the schema. Diagnostic analysis writes production fingerprints/reviews/journal entries and can print success after failure. These are necessary Android integration fixes.

Hourly sync currently sends a preview of the first attachment and an image count, rather than all original files. Android has no caller of the existing new_model endpoint. Shared backend storage/limits cannot be changed in this task; a standalone adapter and explicit local analysis will document that boundary.

## Scope and proposed extension

Add a standalone `evidence_pipeline` package and its tests/docs inside `new_model`; leave legacy app, GUI, config, Python dependencies, all checkpoints and Windows outputs unchanged. Reuse compatible legacy classifiers through a read-only optional adapter. Use actual local Tesseract OCR and an available compact vision adapter, with explicit unavailable/unsupported results. Add bundled Android ML Kit Latin OCR and image labeling for on-device evidence; object detection has an unbundled model and must report availability honestly. Color and image-quality statistics come from pixels, with uncertainty for texture/material inference.

Use independent stage results, stable attachment/task IDs, bounded sequential processing, immutable content-addressed derivatives, and hashes of exact final bytes. Originals required by the proof contract remain intact. Diagnostics must not create attendance, proof approvals or scores. Liveness and score rules remain separate.

## SDK suitability and terms checked before adding dependencies

- ML Kit bundled Latin OCR `16.0.1`: approximately 4 MB per architecture; Android API >=23.
- ML Kit bundled image labeling `17.0.9`: approximately 5.7 MB; 400+ global labels.
- ML Kit object detection `17.0.2`: unbundled model, up to five boxes and five coarse categories; cannot promise offline availability on first use.
- ML Kit operates on-device; Google API/ML Kit terms apply. SDK utilization/performance telemetry may contact Google; image inputs and analysis outputs are processed on-device. Do not extract proprietary SDK models.
- No credential-dependent or paid image provider is configured. No evidence will be sent to a cloud analysis provider.

Official references: https://developers.google.com/ml-kit/vision/text-recognition/v2/android ; https://developers.google.com/ml-kit/vision/image-labeling/android ; https://developers.google.com/ml-kit/vision/object-detection/android ; https://developers.google.com/ml-kit/terms .

## Verification planned

Verify old model loading, real text/no-text OCR, compact image/hash associations, colors, uncertainty, per-stage failures/retries, five real 3–5 MB image inputs and memory bounds, existing tests, Android unit/build checks, and device inference only when ADB is connected. Take and compare a protected Windows file baseline; do not run Windows build or production servers/database mutations.
