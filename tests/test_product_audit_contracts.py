"""Product audit regressions. All databases, profiles and policy adapters are isolated."""
import hashlib
import json
from unittest.mock import Mock

import pytest

from core.bluetooth_transfer import BluetoothTransferManager
from tests.bluetooth_fakes import FakeBackend, PEER
from tests.test_bluetooth_transfer_v2 import package, DEVICE
from core.database import StrictRecordsDB
from windows.browser_extension_monitor import BrowserExtensionMonitor
from windows.uninstall_challenge import UninstallChallengeManager


@pytest.fixture
def transfer(tmp_path, monkeypatch):
    db = StrictRecordsDB(str(tmp_path / "audit.sqlite"))
    manager = BluetoothTransferManager(db, backend_factory=FakeBackend)
    session = manager.start_session(peer_address=PEER)
    yield db, manager, session["session_code"]
    manager.cancel_session()


def test_wrong_transfer_code_does_not_disclose_the_expected_code(transfer):
    _, manager, code = transfer
    result = manager.import_payload("wrong", {}, "0" * 64)
    assert not result["success"]
    assert code not in json.dumps(result)


def test_transfer_checksum_is_required_before_any_import(transfer):
    db, manager, code = transfer
    before = db.get_score_events("2026-10-17")
    result = manager.import_payload(code, package(), peer_address=PEER)
    assert not result["success"]
    assert result["code"] == "INTEGRITY_CHECK_FAILED"
    assert db.get_score_events("2026-10-17") == before


def test_transfer_raw_checksum_must_bind_the_actual_parsed_payload(transfer):
    db, manager, code = transfer
    raw = json.dumps(package())
    changed = package()
    changed["injected_content"] = {"event_id":"audit-forged","points":99}
    result = manager.import_payload(code, changed, hashlib.sha256(raw.encode()).hexdigest(),
                                    source_device=DEVICE, raw_payload_str=raw, peer_address=PEER)
    assert not result["success"]
    with db._connect() as connection:
        assert connection.execute("SELECT count(*) FROM score_events WHERE event_id='audit-forged'").fetchone()[0] == 0


def test_transfer_invalid_schema_returns_failure_without_mutating_records(transfer):
    _, manager, code = transfer
    raw = "[]"
    result = manager.import_payload(code, [], hashlib.sha256(raw.encode()).hexdigest(), raw_payload_str=raw, peer_address=PEER)
    assert not result["success"]
    assert result["code"] == "INVALID_SCHEMA"


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    db = StrictRecordsDB(str(tmp_path / "monitor.sqlite"))
    tracker = BrowserExtensionMonitor(storage_dir=str(tmp_path / "state"), db=db,
                                      process_checker=lambda name: True, clock=lambda: 1000.0,
                                      grace_duration_minutes=2)
    monkeypatch.setattr(tracker, "show_or_update_notification", Mock())
    monkeypatch.setattr(tracker, "dismiss_notification", Mock())
    return db, tracker


@pytest.mark.parametrize("preference,expected", [(None, "UNKNOWN"), (False, "COMPANION_UNAVAILABLE")])
def test_missing_heartbeat_does_not_establish_disablement(monitor, monkeypatch, preference, expected):
    db, tracker = monitor
    monkeypatch.setattr(tracker, "is_extension_heartbeat_healthy", lambda: False)
    monkeypatch.setattr(tracker, "is_extension_disabled_in_preferences", lambda: preference)
    assert tracker.check_state(now=1000.0)["state"] == expected
    assert not tracker.is_interrupted
    assert db.get_deduction_proposals() == []


def test_browser_closure_and_restart_gaps_do_not_advance_disabled_duration(monitor, monkeypatch):
    _, tracker = monitor
    monkeypatch.setattr(tracker, "is_extension_heartbeat_healthy", lambda: False)
    monkeypatch.setattr(tracker, "is_extension_disabled_in_preferences", lambda: True)
    tracker.check_state(now=1000.0)
    tracker.check_state(now=1060.0)
    assert tracker.confirmed_disabled_seconds == 60.0
    monkeypatch.setattr(tracker, "is_chrome_running", lambda: False)
    tracker.check_state(now=1070.0)
    monkeypatch.setattr(tracker, "is_chrome_running", lambda: True)
    tracker.check_state(now=2000.0)
    assert tracker.confirmed_disabled_seconds == 60.0
    tracker.check_state(now=2060.0)
    assert tracker.confirmed_disabled_seconds == 120.0
    tracker.check_state(now=5000.0)
    assert tracker.confirmed_disabled_seconds == 120.0


def test_extension_penalty_is_an_idempotent_proposal_and_never_a_windows_final_deduction(monitor, monkeypatch):
    db, tracker = monitor
    monkeypatch.setattr(tracker, "_penalty_policy_enabled", lambda db: True)
    monkeypatch.setattr(tracker, "is_extension_heartbeat_healthy", lambda: False)
    monkeypatch.setattr(tracker, "is_extension_disabled_in_preferences", lambda: True)
    for second in range(0, 181, 15):
        result = tracker.check_state(now=1000.0 + second)
    assert result["pending_penalty"] == 1
    assert result["total_penalty"] == 0
    proposals = db.get_deduction_proposals()
    assert len(proposals) == 1
    assert proposals[0]["proposed_points"] == -1
    tracker.check_state(now=1180.0)
    assert len(db.get_deduction_proposals()) == 1
    with db._connect() as connection:
        assert connection.execute("SELECT count(*) FROM score_events WHERE points<0").fetchone()[0] == 0


def test_extension_penalties_require_an_explicit_published_policy(monitor, monkeypatch):
    db, tracker = monitor
    monkeypatch.setattr(tracker, "is_extension_heartbeat_healthy", lambda: False)
    monkeypatch.setattr(tracker, "is_extension_disabled_in_preferences", lambda: True)
    for second in range(0, 301, 15):
        tracker.check_state(now=1000.0 + second)
    assert db.get_deduction_proposals() == []
    assert tracker.total_penalty == 0


def test_failed_management_release_does_not_claim_the_policy_was_removed(tmp_path, monkeypatch):
    controller = Mock()
    controller.apply_service_policy.return_value = {"verified": False, "enforcementStatus": "FAILED", "error": "Access denied"}
    monkeypatch.setattr("windows.browser_policy_controller.BrowserPolicyController", lambda: controller)
    setup = Mock()
    setup.unlock_extension.return_value = {"success": False, "error": "Access denied"}
    monkeypatch.setattr("windows.browser_setup.get_browser_extension_setup", lambda: setup)
    manager = UninstallChallengeManager(storage_dir=str(tmp_path), db=Mock())
    challenge = manager.generate_challenge()
    manager.verify_answer(challenge["challenge_id"], manager.active_challenge["expected_answer"])
    result = manager.confirm_and_remove_policy(challenge["challenge_id"])
    assert not result["success"]
    assert not result.get("policy_removed", False)
    assert manager.active_challenge is not None


@pytest.mark.parametrize("task", ["WAKE_UP", "GYM", "BATH", "HOURLY"])
@pytest.mark.parametrize("description", ["A cat on a blanket", "morning gym workout bath study coding"])
def test_cifar_classification_is_not_semantic_proof_verification(task, description):
    from core.real_ai_vlm import RealAIBotEngine
    engine = RealAIBotEngine(checkpoint_path="unused-audit-model.pt")
    advice = engine._synthesize_ai_advice("cat", 0.99, task, description, {})
    assert not advice["verdict"].startswith("VERIFIED")
    assert "confirmed" not in advice["advice"].lower()
    assert "verified" not in advice["advice"].lower()


def test_missing_image_or_model_has_no_invented_confidence():
    from core.real_ai_vlm import RealAIBotEngine
    advice = RealAIBotEngine(checkpoint_path="unused-audit-model.pt")._heuristic_advice(
        "GYM", "gym workout", "missing image", {})
    assert advice["neural_confidence"] == 0
    assert advice["top_predictions"] == []
    assert not advice["discipline_verdict"].startswith("VERIFIED")


def test_local_classifier_fallback_cannot_approve_or_award_missing_proof():
    from core.ai_analyzer import AIProofAnalyzer
    result = AIProofAnalyzer._deterministic_proof_analysis(
        evidence_id="audit-no-image", task_code="GYM", task_title="Gym workout",
        timestamp="2026-10-17T18:30:00+05:30", device_id="fixture",
        platform="WINDOWS", sha256_hash=None, is_tampered=False, anti_cheat_signals=[])
    assert result["verdict"] == "NEEDS_REVIEW"
    assert not result["auto_approved"]
    assert result["suggested_points"] == 0
