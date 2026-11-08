"""Record user-performed Windows actions as multimodal trajectories.

The recorder intentionally observes input rather than injecting input. Each
trajectory contains the user's goal, screenshots, foreground-window metadata,
optional OCR text, cursor state, and the actions that occurred between frames.

Start it with:

    python -m winassist_ai.recorder --command "Open Calculator" --duration 30

Press Ctrl+Shift+F12 to stop early. The default output is a JSON trajectory
plus a ``frames`` directory containing the captured screenshots.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import ImageGrab


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


@dataclass(frozen=True)
class RecorderConfig:
    command: str
    output_dir: Path = Path("data/winassist_trajectories")
    session_id: str = ""
    duration_seconds: float | None = None
    post_action_delay: float = 0.20
    capture_ocr: bool = True
    capture_screenshots: bool = True
    move_capture_interval: float = 0.15

    def resolved_session_id(self) -> str:
        return self.session_id or datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]


@dataclass
class FrameRecord:
    frame_id: str
    captured_at: str
    reason: str
    image_path: str | None
    screen_state: dict[str, Any]


@dataclass
class ActionRecord:
    action_id: int
    timestamp: str
    action_type: str
    details: dict[str, Any]
    frame_after: str | None = None


@dataclass
class Trajectory:
    schema_version: str
    session_id: str
    goal: str
    started_at: str
    ended_at: str | None = None
    outcome: str = "unknown"
    frames: list[FrameRecord] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


def _foreground_window() -> dict[str, Any]:
    """Return foreground title, PID, and executable path when available."""
    if os.name != "nt":
        return {"available": False, "title": "", "pid": None, "executable": None}

    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"available": True, "title": "", "pid": None, "executable": None}

        length = user32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, length + 1)

        process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        pid = int(process_id.value) or None
        executable = None
        if pid:
            process_handle = kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
            if process_handle:
                try:
                    path_buffer = ctypes.create_unicode_buffer(1024)
                    path_length = ctypes.c_uint32(len(path_buffer))
                    if kernel32.QueryFullProcessImageNameW(
                        process_handle, 0, path_buffer, ctypes.byref(path_length)
                    ):
                        executable = path_buffer.value
                finally:
                    kernel32.CloseHandle(process_handle)

        return {
            "available": True,
            "hwnd": int(hwnd),
            "title": title_buffer.value,
            "pid": pid,
            "executable": executable,
        }
    except Exception as exc:  # pragma: no cover - depends on Windows permissions
        return {
            "available": False,
            "title": "",
            "pid": None,
            "executable": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _cursor_position() -> dict[str, int] | None:
    if os.name != "nt":
        return None
    try:
        point = ctypes.wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return {"x": int(point.x), "y": int(point.y)}
    except Exception:  # pragma: no cover - depends on Windows APIs
        pass
    return None


def _key_name(key: Any) -> str:
    char = getattr(key, "char", None)
    if char:
        return char
    return str(key).replace("Key.", "")


class Recorder:
    """Capture one user-performed interaction trajectory."""

    def __init__(self, config: RecorderConfig, logger: Callable[[str], None] = print):
        self.config = config
        self.logger = logger
        self.session_id = config.resolved_session_id()
        self.session_dir = config.output_dir / self.session_id
        self.frames_dir = self.session_dir / "frames"
        self.trajectory = Trajectory(
            schema_version="winassist-trajectory-v1",
            session_id=self.session_id,
            goal=config.command,
            started_at=utc_now(),
        )
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._last_move_at = 0.0

    def _screen_state(self, image: Any | None) -> dict[str, Any]:
        state: dict[str, Any] = {
            "foreground_window": _foreground_window(),
            "cursor": _cursor_position(),
        }
        if self.config.capture_ocr:
            state["ocr"] = self._ocr(image)
        return state

    @staticmethod
    def _ocr(image: Any | None) -> dict[str, Any]:
        if image is None:
            return {"available": False, "text": "", "reason": "no_screenshot"}
        try:
            import pytesseract

            return {"available": True, "text": pytesseract.image_to_string(image)}
        except ImportError:
            return {"available": False, "text": "", "reason": "pytesseract_not_installed"}
        except Exception as exc:  # pragma: no cover - depends on local Tesseract install
            return {"available": False, "text": "", "reason": f"{type(exc).__name__}: {exc}"}

    def capture_frame(self, reason: str) -> str:
        frame_id = f"frame_{len(self.trajectory.frames):06d}"
        image = None
        image_path: str | None = None
        if self.config.capture_screenshots:
            try:
                image = ImageGrab.grab(all_screens=True)
                path = self.frames_dir / f"{frame_id}.png"
                image.save(path)
                image_path = str(path.relative_to(self.session_dir))
            except Exception as exc:  # pragma: no cover - depends on desktop session
                image = None
                image_path = None
                self.logger(f"screenshot failed: {type(exc).__name__}: {exc}")

        frame = FrameRecord(
            frame_id=frame_id,
            captured_at=utc_now(),
            reason=reason,
            image_path=image_path,
            screen_state=self._screen_state(image),
        )
        with self._lock:
            self.trajectory.frames.append(frame)
        return frame_id

    def record_action(self, action_type: str, details: dict[str, Any], capture_after: bool = True) -> None:
        action = ActionRecord(
            action_id=len(self.trajectory.actions),
            timestamp=utc_now(),
            action_type=action_type,
            details=_json_safe(details),
        )
        if capture_after:
            time.sleep(self.config.post_action_delay)
            action.frame_after = self.capture_frame(f"after_{action_type}_{action.action_id}")
        with self._lock:
            self.trajectory.actions.append(action)

    def stop(self) -> None:
        self._stop_event.set()

    def _install_listeners(self) -> tuple[Any, Any]:
        try:
            from pynput import keyboard, mouse
        except ImportError as exc:
            raise RuntimeError(
                "Recorder input listeners require the automation extra. "
                "Install with: pip install -e \".[automation]\""
            ) from exc

        hotkey = keyboard.HotKey(
            keyboard.HotKey.parse("<ctrl>+<shift>+<f12>"),
            self.stop,
        )

        def on_press(key: Any) -> None:
            try:
                hotkey.press(keyboard.Listener.canonical(key))
            except Exception:
                pass
            self.record_action("key_press", {"key": _key_name(key)})

        def on_release(key: Any) -> None:
            try:
                hotkey.release(keyboard.Listener.canonical(key))
            except Exception:
                pass

        def on_move(x: int, y: int) -> None:
            now = time.monotonic()
            if now - self._last_move_at >= self.config.move_capture_interval:
                self._last_move_at = now
                self.record_action("mouse_move", {"x": int(x), "y": int(y)}, capture_after=False)

        def on_click(x: int, y: int, button: Any, pressed: bool) -> None:
            if pressed:
                self.record_action(
                    "mouse_click",
                    {"x": int(x), "y": int(y), "button": str(button).replace("Button.", "")},
                )

        def on_scroll(x: int, y: int, dx: int, dy: int) -> None:
            self.record_action(
                "mouse_scroll",
                {"x": int(x), "y": int(y), "dx": int(dx), "dy": int(dy)},
            )

        keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
        return keyboard_listener, mouse_listener

    def run(self, outcome: str = "unknown") -> Path:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.capture_frame("initial")
        keyboard_listener, mouse_listener = self._install_listeners()
        keyboard_listener.start()
        mouse_listener.start()
        timer = None
        if self.config.duration_seconds is not None:
            timer = threading.Timer(self.config.duration_seconds, self.stop)
            timer.daemon = True
            timer.start()

        self.logger("Recording started. Press Ctrl+Shift+F12 to stop.")
        self._stop_event.wait()
        if timer:
            timer.cancel()
        keyboard_listener.stop()
        mouse_listener.stop()
        keyboard_listener.join(timeout=2)
        mouse_listener.join(timeout=2)
        self.capture_frame("final")
        self.trajectory.ended_at = utc_now()
        self.trajectory.outcome = outcome
        output_path = self.session_dir / "trajectory.json"
        output_path.write_text(json.dumps(self.trajectory.to_dict(), indent=2), encoding="utf-8")
        self.logger(f"Saved trajectory: {output_path}")
        return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a WinAssist Windows interaction trajectory.")
    parser.add_argument("--command", required=True, help="The user's goal or command being demonstrated.")
    parser.add_argument("--output", default="data/winassist_trajectories")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--duration", type=float, default=None, help="Optional automatic stop duration in seconds.")
    parser.add_argument("--outcome", choices=("success", "failure", "unknown"), default="unknown")
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--no-screenshots", action="store_true")
    args = parser.parse_args()

    config = RecorderConfig(
        command=args.command,
        output_dir=Path(args.output),
        session_id=args.session_id,
        duration_seconds=args.duration,
        capture_ocr=not args.no_ocr,
        capture_screenshots=not args.no_screenshots,
    )
    Recorder(config).run(outcome=args.outcome)
