# Vision-Language V1

This is the first slice of the multimodal system: image upload, image preprocessing, a repository-owned vision-language model, structured JSON output, and a minimal browser interface.

The model in this repository does not use Qwen, Llama, CLIP, or any other pretrained model. It is a small CNN + character-level GRU fusion network initialized and trained from scratch. The included training task teaches it to answer questions about synthetic colored shapes. This is a deliberately narrow baseline for validating the architecture before collecting a real dataset.

## Run immediately with the mock backend

From PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
$env:MODEL_BACKEND = "mock"
python run_local.py legacy --reload
```

Open <http://127.0.0.1:8000>.

The mock backend verifies the upload path and UI without requiring model weights.

## Train and use the native model

Install PyTorch, train the model from scratch, then select the native backend:

```powershell
pip install -e ".[native]"
$env:MODEL_BACKEND = "native"
python -m training.train_tiny_vlm
python run_local.py legacy --reload
```

The default training command uses generated images and CPU-compatible PyTorch operations. It produces `checkpoints/tiny_vlm.pt`, which is ignored by Git because model weights are generated artifacts.

Evaluate on held-out synthetic examples:

```powershell
python -m training.evaluate_tiny_vlm --samples 512
```

## Train on realistic downloaded images

The realistic experiment downloads the official CIFAR-10 photographic dataset, writes a balanced local split, and trains the repository-owned residual CNN with a 10-class object head. The current phase-4 run uses the full 50,000 balanced training images and a 1,000-image balanced held-out test set:

```powershell
python -m training.download_realistic_images --output data/cifar10_phase3 --train-count 50000 --test-count 1000
python -m training.train_realistic_vlm --data data/cifar10_phase3 --output checkpoints/realistic_vlm_phase3.pt --steps 1600 --batch-size 128 --label-smoothing 0.0 --learning-rate 0.001 --init-checkpoint checkpoints/realistic_vlm_phase2.pt --cache-images
python -m training.evaluate_realistic_vlm --data data/cifar10_phase3 --checkpoint checkpoints/realistic_vlm_phase3.pt --confidence-threshold 0.90
python -m training.train_realistic_vlm --data data/cifar10_phase3 --output checkpoints/realistic_vlm_phase4.pt --steps 800 --batch-size 128 --label-smoothing 0.0 --learning-rate 0.0003 --init-checkpoint checkpoints/realistic_vlm_phase3.pt --cache-images
python -m training.evaluate_realistic_vlm --data data/cifar10_phase3 --checkpoint checkpoints/realistic_vlm_phase4.pt --confidence-threshold 0.90
$env:MODEL_BACKEND = "realistic"
$env:CHECKPOINT_PATH = "checkpoints/realistic_vlm_phase4.pt"
python run_local.py legacy --reload
```

The phase-4 checkpoint reached 84.0% top-1 accuracy, 97.3% top-3 accuracy, and 86.8% mean confidence on the 1,000-image test set. At the 0.90 confidence threshold, it covers 63.4% of inputs with 96.21% selective accuracy. Phase 3 and the earlier checkpoints remain available for rollback.

The requested counts are 70 train and 40 test, which is a 63.6%/36.4% split. An exact 70%/30% split with 40 test images would require 93 training images.

Test confidence and automatically retrain when confidence is below 0.70 or test accuracy is below 50%:

```powershell
python -m training.test_and_retrain_realistic
```

The automatic recovery expands the balanced training set to 2,000 images while retaining 40 held-out test images.

## Desktop GUI model tester

Launch the local desktop GUI connected directly to the active phase-4 checkpoint. It uses a dark premium layout with image preview, confidence meter, review state, and structured JSON output:

```powershell
python -m winassist_ai.gui
```

Choose an image, enter a question, and press **Analyze**. The GUI shows the model answer, confidence, review flag, top predictions, and metadata. To test without opening a window:

```powershell
python -m winassist_ai.gui --self-test data/cifar10_phase3/test/truck/test_004.png
```

Build the Windows application as a folder-based executable:

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --windowed --onedir --name WinAssistVision --paths . --add-data "checkpoints\\realistic_vlm_phase4.pt;checkpoints" winassist_ai\\gui.py
```

The executable is created at `dist\\WinAssistVision\\WinAssistVision.exe`. The bundled model checkpoint is kept in the adjacent `checkpoints` folder so the app starts offline and can be updated without rebuilding the UI.

## WinAssist text-understanding model

The command model is a separate from-scratch character-GRU model. It predicts intent, OS domain, target, and risk, then extracts application names, folders, paths, keys, quoted text, and PowerShell cmdlets. It does not execute commands.

Generate and train it:

```powershell
python -m training.generate_command_dataset --output data/commands/commands_v3.jsonl
python -m training.train_command_model --data data/commands/commands_v3.jsonl --output checkpoints/command_understanding_v3.pt --epochs 20
python -m training.evaluate_command_model --data data/commands/commands_v3.jsonl --checkpoint checkpoints/command_understanding_v3.pt --split test
```

Test a command:

```powershell
python -m winassist_ai.text_model "Open Calculator"
python -m winassist_ai.text_model "PowerShell: Get-Process"
python -m winassist_ai.text_model "PowerShell: Remove-Item C:\\Temp\\old.txt"
```

The v3 test result is 99.77% joint accuracy on 429 held-out commands, with 99.70% mean confidence. This is a controlled command grammar benchmark, not proof of general Windows understanding. Destructive patterns are checked by a deterministic safety gate; commands such as `Remove-Item`, `Stop-Process`, `Restart-Computer`, registry changes, chained PowerShell, and encoded commands are not allowed to bypass policy.

## Autonomous decision brain

The decision layer is separate from the neural model. It combines the text model with a local knowledge base and hard rules:

```powershell
python -m winassist_ai.agent "Open Calculator"
python -m winassist_ai.agent "Open Display settings"
python -m winassist_ai.agent "Lock the screen"
python -m winassist_ai.agent "PowerShell: Remove-Item C:\\Temp\\old.txt"
```

The policy returns `allow_auto`, `ask_confirmation`, or `block`, plus bounded actions and verification conditions. The local knowledge base is in `winassist_ai/agent/knowledge_base.json`; the rule engine is in `winassist_ai/agent/policy.py` and `winassist_ai/text_model/safety.py`. Known built-in app launches, opening settings pages, read-only OS inspection, short safe keyboard navigation, bounded scrolling, and read-only PowerShell can be planned automatically. Settings changes, clicks, arbitrary text entry, closing apps, and screen locking require confirmation. Credential/secret entry, destructive operations, blocked shortcuts, unknown executables, and unbounded shell activity are blocked. The planner never executes the returned actions by itself.

The UI action guard can also be used directly before an executor is added:

```powershell
python -c "from winassist_ai.agent import assess_ui_action; print(assess_ui_action({'type': 'move_cursor', 'coordinate': '100,200'}, screen_size=(1920,1080)))"
```

Run the real-time dry-run simulator. It uses a virtual desktop, simulated action latency, state transitions, and post-action verification; it never controls the actual mouse, keyboard, applications, or lock screen:

```powershell
python -m winassist_ai.agent.simulator
python -m winassist_ai.agent.simulator --realtime --delay-scale 0.02
```

## Screenshot understanding and OCR

The screenshot model is a separate from-scratch model for UI screenshots. It predicts a screen type and recognizes text in four supervised UI regions (title, two content lines, and a button). It uses only repository-owned code, PyTorch, and Pillow; it does not use a pretrained vision or OCR model.

Generate and train it:

```powershell
python -m training.generate_screenshot_dataset --output data/screenshots_ocr --train-count 4000 --validation-count 500 --test-count 800
python -m training.train_screenshot_model --data data/screenshots_ocr --output checkpoints/screenshot_ocr_v4.pt --epochs 10 --batch-size 128 --learning-rate 0.0005 --cache-images --init-checkpoint checkpoints/screenshot_ocr_v3.pt
python -m training.evaluate_screenshot_model --data data/screenshots_ocr --checkpoint checkpoints/screenshot_ocr_v4.pt --split test
```

Analyze a screenshot:

```powershell
python -m winassist_ai.screenshot_ocr --checkpoint checkpoints/screenshot_ocr_v4.pt --image path\to\screenshot.png
```

The current v4 test result is 100% screen-type accuracy, 88.43% overall OCR character accuracy, 23.22% exact-line accuracy, and approximately 72% non-padding character accuracy on 800 held-out generated screenshots. Real desktop screenshots should be collected through the recorder and added to the training set before treating the OCR output as authoritative.

Use it through the existing API or desktop GUI by selecting the screenshot backend:

```powershell
$env:MODEL_BACKEND = "screenshot_ocr"
$env:CHECKPOINT_PATH = "checkpoints/screenshot_ocr_v4.pt"
python run_local.py legacy --reload
```

## WinAssist trajectory recorder

The first Windows-automation component is an observational recorder. It captures the command, screenshots, foreground-window metadata, cursor position, keyboard and mouse actions, optional OCR text, and the final outcome as one training trajectory. It does not control applications itself.

Install the optional recorder dependencies:

```powershell
pip install -e ".[automation]"
```

Record a safe demonstration such as opening Calculator:

```powershell
python -m winassist_ai.recorder --command "Open Calculator" --duration 30 --outcome success
```

Press `Ctrl+Shift+F12` to stop early. Each session is written below `data/winassist_trajectories/<session-id>/` with `trajectory.json` and a `frames/` directory. Do not record passwords, private messages, or other sensitive content.

## API

`POST /analyze` accepts multipart form data:

- `image`: an image file
- `question`: an optional question

Start either local API from this folder with `python run_local.py legacy` or
`python run_local.py evidence --port 8001`. The launcher binds to `127.0.0.1`
and does not accept a host override; the APIs also reject non-loopback clients
and mismatched browser origins. The legacy endpoint preserves its 10 MiB image
limit and caps the whole multipart request at 11 MiB. Evidence requests allow at
most five images, 25 MiB per image, 125 MiB total image bytes, and a 1 MiB
multipart framing budget. These checks do not provide operating-system network
isolation.

The evidence API writes analysis-only artifacts under
`output/evidence_analysis`. Caller-supplied local image paths are accepted only
from `data/`, `TestImages/`, or `qwen_image_text/data/`; the HTTP upload route
uses its own short-lived scratch directory. It never approves proofs or awards
points.

Example response:

```json
{
  "analysis_only": true,
  "analysis_status": "success",
  "description": "The image shows 1 red circle.",
  "objects": ["red circle"],
  "actions": [],
  "text": [],
  "events": [],
  "answer": "The shape is red.",
  "confidence": 0.93,
  "top_predictions": [
    {"label": "ship", "confidence": 0.93},
    {"label": "airplane", "confidence": 0.04},
    {"label": "bird", "confidence": 0.02}
  ],
  "metadata": {
    "filename": "photo.png",
    "content_type": "image/png",
    "original_width": 1024,
    "original_height": 768,
    "width": 1024,
    "height": 768,
    "format": "PNG"
  }
}
```

## Current limitations

This native baseline is not yet a general image-language model. The realistic experiment is trained from scratch on low-resolution CIFAR-10 images, so its accuracy should not be confused with broad visual intelligence. It returns empty OCR, action, and event fields. General capability requires a larger licensed dataset and a decoder trained from scratch.

## Tests

```powershell
pytest
```

## Next increments

1. Replace synthetic shapes with a real, licensed dataset collected for your domain.
2. Add a from-scratch token decoder for free-form answers and OCR.
3. Add temporal frame encoding and audio only after the image model is reliable.
4. Add embedding caching, authentication, request limits, and GPU-serving optimizations.
