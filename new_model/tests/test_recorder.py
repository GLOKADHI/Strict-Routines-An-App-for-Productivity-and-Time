import json
from pathlib import Path

from winassist_ai.recorder.recorder import Recorder, RecorderConfig, Trajectory


def test_trajectory_serializes_training_fields() -> None:
    trajectory = Trajectory(
        schema_version="winassist-trajectory-v1",
        session_id="example",
        goal="Open Calculator",
        started_at="2026-09-21T00:00:00+00:00",
        outcome="success",
    )
    payload = trajectory.to_dict()
    assert payload["schema_version"] == "winassist-trajectory-v1"
    assert payload["goal"] == "Open Calculator"
    assert payload["actions"] == []
    json.dumps(payload)


def test_recorder_can_capture_without_desktop_listeners(tmp_path: Path) -> None:
    recorder = Recorder(
        RecorderConfig(
            command="test command",
            output_dir=tmp_path,
            capture_screenshots=False,
            capture_ocr=False,
        ),
        logger=lambda _: None,
    )
    recorder.session_dir.mkdir(parents=True, exist_ok=True)
    recorder.frames_dir.mkdir(parents=True, exist_ok=True)
    frame_id = recorder.capture_frame("test")
    recorder.record_action("key_press", {"key": "ENTER"}, capture_after=False)
    recorder.trajectory.ended_at = recorder.trajectory.started_at
    output_path = recorder.session_dir / "trajectory.json"
    output_path.write_text(json.dumps(recorder.trajectory.to_dict()), encoding="utf-8")

    assert frame_id == "frame_000000"
    assert recorder.trajectory.actions[0].action_type == "key_press"
    assert output_path.exists()
