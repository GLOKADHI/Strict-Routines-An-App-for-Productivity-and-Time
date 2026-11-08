"""Deterministic software tests, not real browser disablement tests."""
import json
from pathlib import Path
import uuid

import pytest

from prepare_chrome_monitor import prepare
from windows.browser_extension_monitor import BrowserExtensionMonitor

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    wall = 1000.0
    mono = 100.0

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


class Ledger:
    enabled = True
    offline = False

    def __init__(self):
        self.rows = {}

    def get_active_configuration(self):
        return {'rules': {'chrome_extension_penalty_enabled': self.enabled}}

    def create_deduction_proposal(self, **values):
        if self.offline:
            raise OSError('test offline')
        self.rows.setdefault(values['proposal_id'], dict(values, status='AWAITING_CONFIRMATION'))
        return self.rows[values['proposal_id']]

    def get_deduction_proposal(self, tx):
        return self.rows.get(tx)


@pytest.fixture
def rig(tmp_path):
    storage = tmp_path / 'state'
    prepare(tmp_path / 'package', storage, 'Software fixture: Default', ROOT)
    config = json.loads((storage / 'enrollment.json').read_text())
    clock, ledger = Clock(), Ledger()
    monitor = BrowserExtensionMonitor(storage_dir=storage, db=ledger, process_checker=lambda _: True,
                                      clock=lambda: clock.wall, monotonic=lambda: clock.mono)
    monitor.show_or_update_notification = lambda **_: None
    packet = dict(role='observer', installation_id=config['installation_id'],
                  helper_profile=str(uuid.uuid4()), target_profile=str(uuid.uuid4()),
                  browser_session=str(uuid.uuid4()), sequence=0, state='ENABLED', operational=True)

    def send(state='DISABLED', advance=30, **changes):
        clock.advance(advance)
        packet.update(state=state, operational=state == 'ENABLED', sequence=packet['sequence'] + 1,
                      observed_at_ms=clock.wall * 1000)
        packet.update(changes)
        return monitor.submit_observation(dict(packet), 'chrome-extension://' + config['helper_id'], config['token'])

    assert send('ENABLED', 0)['success']
    return monitor, clock, ledger, send, packet, config, storage


@pytest.mark.parametrize('grace', [5, 7, 10])
def test_exact_grace_and_complete_minute_boundaries(rig, grace):
    monitor, clock, ledger, send, *_ = rig
    monitor.set_grace_duration_minutes(grace)
    send(advance=0)
    start, deadline = clock.wall, monitor.grace_deadline
    for _ in range(grace * 2 + 1):
        send()
    send(advance=29)
    assert not ledger.rows
    send(advance=1)
    assert len(ledger.rows) == 1
    send(advance=60)
    assert len(ledger.rows) == 2
    assert deadline == start + grace * 60 == monitor.grace_deadline
    for row in ledger.rows.values():
        assert row['proposed_points'] == -1
        assert sum(b-a for a,b in row['evidence']['verified_segments']) == 60
    assert monitor.total_penalty == 0  # Proposed is never advertised as finalized.


def test_missed_heartbeats_and_missing_helper_never_bill(rig):
    monitor, clock, ledger, send, *_ = rig
    send(advance=0)
    send()
    before = monitor.confirmed_disabled_seconds
    clock.advance(800)
    assert monitor.check_state()['state'] == 'STATE_UNKNOWN'
    assert monitor.confirmed_disabled_seconds == before
    assert not ledger.rows
    send()
    assert monitor.confirmed_disabled_seconds == before


def test_profile_origin_token_replay_and_stale_reports_rejected(rig):
    monitor, clock, ledger, send, packet, config, _ = rig
    packet = dict(packet, observed_at_ms=clock.wall * 1000, sequence=packet['sequence']+1)
    origin = 'chrome-extension://' + config['helper_id']
    for data, org, token in [(packet, 'https://example.org', config['token']),
                             (packet, origin, 'wrong'),
                             (dict(packet,helper_profile=str(uuid.uuid4())),origin,config['token']),
                             (dict(packet,observed_at_ms=0),origin,config['token']),
                             (dict(packet,sequence=1),origin,config['token'])]:
        assert monitor.submit_observation(data, org, token)['success'] is False
    assert monitor.last_status == 'ENABLED'
    assert not ledger.rows


def test_process_restart_preserves_deadline_but_excludes_downtime(rig):
    monitor, clock, ledger, send, packet, config, storage = rig
    send(advance=0)
    send()
    episode, deadline = monitor.episode_id, monitor.grace_deadline
    replacement = BrowserExtensionMonitor(storage_dir=storage, db=ledger, process_checker=lambda _: True,
                                          clock=lambda: clock.wall, monotonic=lambda: clock.mono)
    replacement.show_or_update_notification = lambda **_: None
    clock.advance(500)
    packet.update(sequence=packet['sequence']+1, observed_at_ms=clock.wall*1000)
    assert replacement.submit_observation(packet,'chrome-extension://'+config['helper_id'],config['token'])['success']
    assert replacement.episode_id == episode and replacement.grace_deadline == deadline
    assert replacement.confirmed_disabled_seconds == 30
    assert not ledger.rows


def test_policy_opt_in_never_retroactively_bills_disabled_minutes(rig):
    monitor, clock, ledger, send, *_ = rig
    ledger.enabled = False
    send(advance=0)
    for _ in range(14):
        send()
    ledger.enabled = True
    send()
    assert not ledger.rows
    send()
    send()
    send()
    assert len(ledger.rows) == 1


def test_restoration_retries_outbox_and_deduplicates_after_crash(rig):
    monitor, clock, ledger, send, packet, config, storage = rig
    ledger.offline = True
    send(advance=0)
    for _ in range(12):
        send()
    assert len(monitor.outbox) == 1 and not ledger.rows
    tx = next(iter(monitor.outbox))
    ledger.offline = False
    send('ENABLED', 1)
    assert list(ledger.rows) == [tx] and not monitor.outbox
    assert not monitor.is_interrupted
    replacement = BrowserExtensionMonitor(storage_dir=storage, db=ledger, process_checker=lambda _: True,
                                          clock=lambda: clock.wall, monotonic=lambda: clock.mono)
    replacement._flush_outbox(clock.wall, force=True)
    assert list(ledger.rows) == [tx]
    ledger.rows[tx].update(status='FINALIZED', finalized_event_id='android-confirmed-id')
    replacement._flush_outbox(clock.wall, force=True)
    assert replacement.total_penalty == 1 and replacement.pending_penalty == 0
    assert replacement.transactions[tx]['authoritative_transaction_reference'] == 'android-confirmed-id'
    ledger.rows[tx].update(status='APPROVED')
    replacement.transactions[tx]['status'] = 'AWAITING_CONFIRMATION'
    replacement._flush_outbox(clock.wall, force=True)
    assert replacement.total_penalty == 1 and replacement.pending_penalty == 0


def test_admin_recovery_waits_for_operational_restoration(rig):
    monitor, clock, ledger, send, *_ = rig
    send(advance=0)
    monitor.authorize_recovery()
    assert send()['state'] == 'MAINTENANCE_UNLOCKED'
    assert not monitor.is_interrupted
    assert send('ENABLED')['state'] == 'ENABLED'
    assert send()['state'] == 'GRACE_PERIOD'


def test_grace_change_persisted_for_next_episode_only(rig):
    monitor, clock, ledger, send, *_ = rig
    send(advance=0)
    deadline = monitor.grace_deadline
    monitor.set_grace_duration_minutes(10)
    assert monitor.grace_duration_minutes == 5 and monitor.grace_deadline == deadline
    send('ENABLED')
    send()
    assert monitor.grace_duration_minutes == 10


def test_removal_is_distinct_and_has_no_implicit_penalty_policy(rig):
    monitor, clock, ledger, send, *_ = rig
    send('UNINSTALLED')
    for _ in range(20):
        assert send('UNINSTALLED')['state'] == 'UNINSTALLED'
    assert not ledger.rows


def test_clock_jump_or_sequence_gap_does_not_create_coverage(rig):
    monitor, clock, ledger, send, packet, *_ = rig
    send(advance=0)
    send()
    before = monitor.confirmed_disabled_seconds
    send(sequence=packet['sequence']+2)
    assert monitor.confirmed_disabled_seconds == before
    clock.wall += 30  # Wall clock jumps while monotonic does not.
    send(advance=0)
    assert monitor.confirmed_disabled_seconds == before
    assert not ledger.rows


def test_new_browser_session_has_no_gap_coverage_or_grace_reset(rig):
    monitor, clock, ledger, send, *_ = rig
    send(advance=0)
    send()
    deadline = monitor.grace_deadline
    send(browser_session=str(uuid.uuid4()))
    assert monitor.confirmed_disabled_seconds == 30 and monitor.grace_deadline == deadline


def test_preparation_preserves_identity_and_does_not_ship_static_credentials(tmp_path):
    public = prepare(tmp_path/'package', tmp_path/'state', 'Chosen profile', ROOT)
    assert 'token' not in public
    assert json.loads((tmp_path/'package/StrictRoutines/manifest.json').read_text())['key'] == json.loads((ROOT/'browser_extension/manifest.json').read_text())['key']
    with pytest.raises(ValueError):
        prepare(tmp_path/'other', tmp_path/'state', 'Other profile', ROOT)


def test_update_keeps_enrollment_credentials_and_existing_timer(tmp_path):
    storage = tmp_path/'state'
    prepare(tmp_path/'first', storage, 'Chosen profile', ROOT)
    enrollment = (storage/'enrollment.json').read_bytes()
    (storage/'extension_monitor_state.json').write_text('{"episode_id":"keep"}')
    prepare(tmp_path/'updated', storage, 'Chosen profile', ROOT, update_existing=True)
    assert (storage/'enrollment.json').read_bytes() == enrollment
    assert (storage/'extension_monitor_state.json').read_text() == '{"episode_id":"keep"}'
    with pytest.raises(ValueError):
        prepare(tmp_path/'wrong-profile', storage, 'Another profile', ROOT, update_existing=True)


def test_actual_sqlite_proposals_are_idempotent_and_do_not_change_score(tmp_path):
    from core.database import StrictRecordsDB
    db = StrictRecordsDB(str(tmp_path/'isolated.sqlite'))
    db.get_active_configuration = lambda: {'rules': {'chrome_extension_penalty_enabled': True}}
    monitor = BrowserExtensionMonitor(storage_dir=tmp_path/'monitor', db=db, process_checker=lambda _: True)
    monitor.show_or_update_notification = lambda **_: None
    monitor.detect_state = lambda: ('DISABLED', {})
    with db._connect() as connection:
        before = connection.execute('SELECT COUNT(*) FROM score_events').fetchone()[0]
    for sec in range(0,361,30):
        monitor.check_state(now=1000+sec)
    monitor._flush_outbox(1360, force=True)
    assert len(db.get_deduction_proposals()) == 1
    from core.scoring_engine import ScoringEngine
    history = ScoringEngine(db, storage_root=str(tmp_path/'proofs')).get_unified_transaction_history(date='1970-01-01')
    tx = next(t for t in history['transactions'] if t['transaction_id'].startswith('tx_ext_'))
    assert tx['status'] == 'Pending confirmation' and tx['is_negative_proposal']
    with db._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM score_events').fetchone()[0] == before


def test_lost_commit_acknowledgement_retries_same_transaction(rig):
    monitor, clock, ledger, send, *_ = rig
    original = ledger.create_deduction_proposal
    failures = []
    def commit_then_disconnect(**values):
        result = original(**values)
        if not failures:
            failures.append(True)
            raise OSError('Committed, but acknowledgement lost')
        return result
    ledger.create_deduction_proposal = commit_then_disconnect
    send(advance=0)
    for _ in range(12):
        send()
    assert len(ledger.rows) == 1 and len(monitor.outbox) == 1
    tx = next(iter(ledger.rows))
    send('ENABLED', 1)
    assert list(ledger.rows) == [tx] and not monitor.outbox


@pytest.mark.parametrize('bad', ['not json', '{"schema":2,"coverage_intervals":[[0,1,true]],"confirmed_disabled_seconds":900}',
                               '{"schema":2,"episode_id":"../../outside"}',
                               '{"schema":2,"outbox":{"malformed":{"proposed_points":-99}}}'])
def test_corrupt_state_is_archived_and_cannot_bill(tmp_path, bad):
    (tmp_path/'extension_monitor_state.json').write_text(bad)
    ledger = Ledger()
    monitor = BrowserExtensionMonitor(storage_dir=tmp_path, db=ledger, process_checker=lambda _: True)
    monitor.check_state(now=2000)
    assert not monitor.is_interrupted and not monitor.outbox and not ledger.rows
    backups = list(tmp_path.glob('unreadable-state-*.json'))
    assert len(backups) == 1 and backups[0].read_text() == bad


def test_concurrent_startup_uses_one_monitor(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    import time
    import windows.browser_extension_monitor as module
    made = []
    def construct(db=None):
        made.append(True)
        time.sleep(.01)
        return SimpleNamespace(_db=db)
    monkeypatch.setattr(module, '_extension_monitor', None)
    import windows.browser_monitor_registry as registry
    monkeypatch.setattr(registry, 'BrowserMonitorRegistry', construct)
    with ThreadPoolExecutor(max_workers=8) as executor:
        instances = list(executor.map(lambda _: module.get_browser_extension_monitor(),range(24)))
    assert len(made) == 1 and len({id(x) for x in instances}) == 1


def test_uncertainty_requires_fresh_evidence_before_coverage_resumes(rig):
    monitor, clock, ledger, send, *_ = rig
    send(advance=0)
    send()
    before = monitor.confirmed_disabled_seconds
    monitor.set_shutting_down(True)
    assert monitor.check_state()['state'] == 'STATE_UNKNOWN'
    monitor.set_shutting_down(False)
    assert monitor.check_state()['state'] == 'STATE_UNKNOWN'
    send()
    assert monitor.confirmed_disabled_seconds == before
    assert not ledger.rows
