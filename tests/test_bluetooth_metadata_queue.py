"""Explicit PC policy retains disputed metadata without treating claims as approval."""
import hashlib
import json

import pytest

from core.android_ledger import checkpoint
from tests.bluetooth_fakes import PEER
from tests.test_bluetooth_transfer_v2 import DATE, DEVICE, metadata, package, transfer


def receive(manager, payload, policy='QUEUE_RECONCILIATION'):
    session = manager.start_session(peer_address=PEER, metadata_policy=policy)
    assert session['success']
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    from core.bluetooth_protocol import package_count
    return manager.import_payload(session['session_code'], payload, hashlib.sha256(raw.encode()).hexdigest(),
                                  DEVICE, raw, peer_address=PEER, session_id=session['session_id'],
                                  declared_count=package_count(payload))


def unsupported_review(revision=1):
    payload = metadata(package(revision=revision))
    payload['proof_reviews'][0].update(status='APPROVED', reviewed_by='claimed-reviewer', points_awarded=15)
    return payload


def test_explicit_queue_retains_original_claim_without_approval_or_added_points(transfer):
    db, manager = transfer
    payload = unsupported_review()
    result = receive(manager, payload)
    assert result['success'] and result['confirmed_score'] == 4
    assert result['total_count'] == result['transferred_count'] == 3
    assert result['pending_metadata_count'] == 1 and result['applied_record_count'] == 2
    assert result['conflict_count'] == result['rejected_count'] == 0
    with db._connect() as conn:
        assert conn.execute("SELECT count(*) FROM proof_reviews WHERE evidence_id='pending-proof'").fetchone()[0] == 0
        row = conn.execute('SELECT * FROM sync_conflicts').fetchone()
    assert row['resolution_policy'] == 'KEEP_WINDOWS_PENDING_RECONCILIATION'
    assert json.loads(row['android_payload_json']) == payload['proof_reviews'][0]
    assert json.loads(row['resolved_payload_json'])['applied'] is False
    assert checkpoint(db, DATE)['confirmed_balance'] == 4


def test_default_strict_policy_and_payload_cannot_enable_queue(transfer):
    db, manager = transfer
    payload = unsupported_review()
    payload['metadata_policy'] = 'QUEUE_RECONCILIATION'
    result = receive(manager, payload, policy='STRICT')
    assert not result['success'] and result['code'] == 'RECONCILIATION_REQUIRED'
    with db._connect() as conn:
        assert conn.execute('SELECT count(*) FROM score_events').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM sync_conflicts').fetchone()[0] == 0


def test_pending_claim_deduplicates_across_retries_and_later_package_revisions(transfer):
    db, manager = transfer
    assert receive(manager, unsupported_review())['transferred_count'] == 3
    retry = receive(manager, unsupported_review())
    assert retry['transferred_count'] == 0 and retry['already_present_count'] == 3
    assert retry['pending_metadata_count'] == 1
    revised = receive(manager, unsupported_review(revision=2))
    assert revised['success'] and revised['transferred_count'] == 0
    assert revised['already_present_count'] == 3 and revised['pending_metadata_count'] == 1
    with db._connect() as conn:
        assert conn.execute('SELECT count(*) FROM sync_conflicts').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM score_events').fetchone()[0] == 1


@pytest.mark.parametrize('changes', [{'date': '2026-10-17'}, {'user_id': 'another-account'}])
def test_queue_does_not_accept_wrong_scope(transfer, changes):
    db, manager = transfer
    payload = unsupported_review()
    payload['proof_reviews'][0].update(changes)
    assert not receive(manager, payload)['success']
    with db._connect() as conn:
        assert conn.execute('SELECT count(*) FROM score_events').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM sync_conflicts').fetchone()[0] == 0


def test_later_invalid_record_rolls_back_queued_claim_and_ledger(transfer):
    db, manager = transfer
    payload = unsupported_review()
    payload['scheduled_activities'][0]['date'] = '2026-10-17'
    assert not receive(manager, payload)['success']
    with db._connect() as conn:
        assert conn.execute('SELECT count(*) FROM score_events').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM sync_conflicts').fetchone()[0] == 0


def test_pending_policy_preserves_existing_schedule_and_finalized_state(transfer):
    db, manager = transfer
    original = metadata(package())
    assert receive(manager, original)['success']
    with db._connect() as conn:
        conn.execute("UPDATE scheduled_activities SET status='VERIFIED' WHERE activity_id='hourly-10'")
    incoming = metadata(package(revision=2))
    incoming['scheduled_activities'][0].update(status='EXCUSED', window_end=DATE+'T10:20:00+05:30')
    result = receive(manager, incoming)
    assert result['success'] and result['pending_metadata_count'] == 1
    with db._connect() as conn:
        row = conn.execute("SELECT status,window_end FROM scheduled_activities WHERE activity_id='hourly-10'").fetchone()
    assert tuple(row) == ('VERIFIED', DATE+'T10:10:00+05:30')
    assert result['confirmed_score'] == 4


def test_invalid_receiver_policy_does_not_start_a_session(transfer):
    _, manager = transfer
    assert manager.start_session(peer_address=PEER, metadata_policy='IGNORE_ALL')['code'] == 'INVALID_METADATA_POLICY'
    assert not manager.get_status()['active_session']


def test_invalid_review_award_type_is_not_accepted_as_a_pending_claim(transfer):
    db, manager = transfer
    payload = unsupported_review()
    payload['proof_reviews'][0]['points_awarded'] = '15'
    assert not receive(manager, payload)['success']
    with db._connect() as conn:
        assert conn.execute('SELECT count(*) FROM sync_conflicts').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM score_events').fetchone()[0] == 0


def test_existing_other_account_record_remains_outside_transfer_scope(transfer):
    db, manager = transfer
    assert receive(manager, metadata(package()))['success']
    with db._connect() as conn:
        conn.execute("UPDATE scheduled_activities SET account_id='other-account' WHERE activity_id='hourly-10'")
    result = receive(manager, metadata(package(revision=2)))
    assert not result['success'] and result['code'] == 'INVALID_PACKAGE'
    with db._connect() as conn:
        assert conn.execute('SELECT count(*) FROM sync_conflicts').fetchone()[0] == 0


def test_unbacked_approved_review_cannot_queue_a_different_accounts_local_record(transfer):
    db, manager = transfer
    assert receive(manager, metadata(package()))['success']
    with db._connect() as conn:
        conn.execute("UPDATE proof_reviews SET user_id='other-account' WHERE evidence_id='pending-proof'")
    result = receive(manager, unsupported_review(revision=2))
    assert not result['success'] and result['code'] == 'INVALID_PACKAGE'
    with db._connect() as conn:
        assert conn.execute('SELECT count(*) FROM sync_conflicts').fetchone()[0] == 0
        assert conn.execute("SELECT user_id FROM proof_reviews WHERE evidence_id='pending-proof'").fetchone()[0] == 'other-account'
