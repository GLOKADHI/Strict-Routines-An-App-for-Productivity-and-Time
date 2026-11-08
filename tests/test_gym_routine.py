"""Alternate-day planning tests use disposable SQLite only; no live app/build."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

import pytest

from core.gym_daily_decision import DailyGymDecision, GymDecisionError
from core.gym_routine import GymRoutine, ZONE


class FixtureDB:
    def __init__(self, path):
        self.path = str(path)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def validate_device_credential(self, device_id, token):
        # Exercise the application's real credential validator against this
        # temporary database, rather than replacing the handler's principal.
        from core.database import StrictRecordsDB
        return StrictRecordsDB.validate_device_credential(self, device_id, token)


def now(day='2026-10-24', hour=12):
    return datetime.fromisoformat(day + f'T{hour:02}:00:00').replace(tzinfo=ZONE)


@pytest.fixture
def store(tmp_path):
    db = FixtureDB(tmp_path / 'routine.sqlite')
    with db._connect() as conn:
        conn.executescript('''
            CREATE TABLE routine_schedules(account_id TEXT,schedule_date TEXT,schedule_version INTEGER,
                timezone TEXT,status TEXT,gym_enabled INTEGER);
            CREATE TABLE scheduled_activities(activity_id TEXT PRIMARY KEY,account_id TEXT,type TEXT,date TEXT,
                title TEXT,status TEXT,is_enabled INTEGER DEFAULT 1);
            CREATE TABLE score_events(event_id TEXT PRIMARY KEY,date TEXT,is_dev_mode INTEGER DEFAULT 0,
                timestamp TEXT,event_type TEXT,device_id TEXT,description TEXT,points INTEGER,
                evidence_id TEXT,related_task_id TEXT,parent_event_id TEXT,audit_json TEXT DEFAULT '{}');
            CREATE TABLE evidence_files(evidence_id TEXT PRIMARY KEY,date TEXT,is_dev_mode INTEGER DEFAULT 0,
                record_type TEXT,related_record_id TEXT,file_path TEXT,is_deleted INTEGER DEFAULT 0,sha256_hash TEXT);
            CREATE TABLE proof_reviews(review_id TEXT PRIMARY KEY,evidence_id TEXT,user_id TEXT,task_code TEXT,
                date TEXT,activity_id TEXT,status TEXT,decision TEXT,reviewed_by TEXT);
            CREATE TABLE devices(device_id TEXT,user_id TEXT,pairing_state TEXT DEFAULT 'PAIRED',status TEXT DEFAULT 'REGISTERED');
            CREATE TABLE device_credentials(device_id TEXT,account_id TEXT,token_hash TEXT,revoked_at TEXT);
            INSERT INTO devices(device_id,user_id) VALUES('phone-a','alice'),('phone-b','bob');
        ''')
        DailyGymDecision.ensure_schema(conn)
        for device, account in [('phone-a', 'alice'), ('phone-b', 'bob')]:
            token = hashlib.sha256(('token-' + account).encode()).hexdigest()
            conn.execute('INSERT INTO device_credentials VALUES(?,?,?,NULL)', (device, account, token))
    return db, GymRoutine(db)


def setup(service, account='alice', current=None, request_id='setup-request-01'):
    return service.mutate(account, 'SETUP', 0, request_id, current=current or now(), actor_id=account, device_id='phone-a')


def mutate(service, action, revision, day, **kwargs):
    return service.mutate('alice', action, revision, f'{action.lower()}-request-{revision}', current=now(day), **kwargs)


def calendar_map(value):
    return {row['date']: row for row in value['calendar']}


def test_missing_configuration_is_not_an_active_plan(store):
    db, service = store
    value = service.get('alice', current=now())
    assert value['routine']['status'] == 'SETUP_REQUIRED'
    assert value['routine']['routine_id'] is None
    assert value['statistics']['empty_state']
    assert not value['statistics']['has_tracked_period']
    assert {d['planned_state'] for d in value['calendar']} == {'SETUP_REQUIRED', 'NOT_TRACKED'}
    with db._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM gym_routines').fetchone()[0] == 0


def test_october_november_calendar_deterministic_and_persistent(store):
    db, service = store
    result = setup(service)
    routine_id = result['routine']['routine_id']
    assert result['routine']['status'] == 'NOT_STARTED'
    assert result['routine']['next_gym_date'] == '2026-10-25'
    october = calendar_map(service.get('alice', month='2026-10', current=now('2026-10-30')))
    for day in (25, 27, 29, 31):
        assert october[f'2026-10-{day}']['planned_state'] == 'GYM_DAY'
    for day in (26, 28, 30):
        assert october[f'2026-10-{day}']['planned_state'] == 'REST_DAY'
    assert october['2026-10-24']['planned_state'] == 'NOT_TRACKED'
    november = calendar_map(GymRoutine(FixtureDB(db.path)).get('alice', month='2026-11', current=now('2026-11-04')))
    assert november['2026-11-01']['planned_state'] == 'REST_DAY'
    assert november['2026-11-02']['planned_state'] == 'GYM_DAY'
    assert november['2026-11-03']['planned_state'] == 'REST_DAY'
    assert november['2026-11-04']['planned_state'] == 'GYM_DAY'
    assert GymRoutine(FixtureDB(db.path)).get('alice', current=now())['routine']['routine_id'] == routine_id


def test_setup_late_does_not_backfill_historical_obligations(store):
    _, service = store
    value = setup(service, current=now('2026-11-02'))
    assert value['routine']['start_date'] == value['routine']['anchor_date'] == '2026-10-25'
    assert value['routine']['segments'][0]['start_date'] == '2026-11-02'
    october = service.get('alice', month='2026-10', current=now('2026-11-02'))
    assert all(d['planned_state'] == 'NOT_TRACKED' for d in october['calendar'])
    assert calendar_map(value)['2026-11-02']['planned_state'] == 'GYM_DAY'


@pytest.mark.parametrize('category,note', [('', ''), (' ', ''), ('UNKNOWN', ''), ('OTHER', ' ')])
def test_blank_and_invalid_pause_reason_is_rejected(store, category, note):
    _, service = store
    setup(service)
    with pytest.raises(GymDecisionError) as error:
        mutate(service, 'PAUSE', 1, '2026-10-26', reason_category=category, note=note)
    assert error.value.code == 'REASON_REQUIRED'
    assert service.get('alice', current=now())['routine']['revision'] == 1


def test_pause_and_resume_preserve_cycle_segments_and_reanchor(store):
    db, service = store
    setup(service)
    paused = mutate(service, 'PAUSE', 1, '2026-10-26', reason_category='TRAVEL')
    assert paused['routine']['status'] == 'PAUSED'
    assert paused['routine']['next_gym_date'] is None
    assert paused['routine']['segments'][0]['end_date'] == '2026-10-26'
    assert calendar_map(paused)['2026-10-25']['planned_state'] == 'GYM_DAY'
    assert calendar_map(paused)['2026-10-26']['planned_state'] == 'PAUSED'
    resumed = mutate(service, 'RESUME', 2, '2026-10-28', reactivation_date='2026-10-28')
    dates = calendar_map(resumed)
    assert dates['2026-10-27']['planned_state'] == 'PAUSED'
    assert dates['2026-10-28']['planned_state'] == 'GYM_DAY'
    assert dates['2026-10-29']['planned_state'] == 'REST_DAY'
    assert dates['2026-10-30']['planned_state'] == 'GYM_DAY'
    november = calendar_map(service.get('alice', month='2026-11', current=now('2026-11-02')))
    assert november['2026-11-01']['planned_state'] == 'GYM_DAY'
    persisted = GymRoutine(FixtureDB(db.path)).get('alice', current=now('2026-10-28'))['routine']
    assert len(persisted['segments']) == 2
    assert [e['event_type'] for e in persisted['events']] == ['SETUP', 'PAUSE', 'RESUME']
    assert persisted['events'][1]['reason_category'] == 'TRAVEL'


def test_prelaunch_pause_empty_segment_future_resume_and_pending_conflicts(store):
    _, service = store
    setup(service)
    paused = mutate(service, 'PAUSE', 1, '2026-10-24', reason_category='WORK_STUDY')
    segment = paused['routine']['segments'][0]
    assert paused['routine']['status'] == 'PAUSED' and paused['routine']['can_resume']
    assert segment['start_date'] == segment['end_date'] == '2026-10-25'
    assert calendar_map(paused)['2026-10-24']['planned_state'] == 'NOT_TRACKED'
    future = mutate(service, 'RESUME', 2, '2026-10-25', reactivation_date='2026-10-28')
    assert future['routine']['status'] == 'PAUSED'
    assert future['routine']['pending_resume_date'] == '2026-10-28'
    assert not future['routine']['can_pause'] and not future['routine']['can_resume']
    assert future['routine']['next_gym_date'] == '2026-10-28'
    assert calendar_map(future)['2026-10-27']['planned_state'] == 'PAUSED'
    with pytest.raises(GymDecisionError):
        mutate(service, 'RESUME', 3, '2026-10-26', reactivation_date='2026-10-29')
    with pytest.raises(GymDecisionError) as error:
        mutate(service, 'PAUSE', 3, '2026-10-26', reason_category='PERSONAL')
    assert error.value.code == 'RESUME_PENDING'
    assert service.get('alice', current=now('2026-10-28'))['routine']['status'] == 'ACTIVE'


def test_no_retroactive_resume_and_many_segments_do_not_overlap(store):
    _, service = store
    setup(service)
    mutate(service, 'PAUSE', 1, '2026-10-26', reason_category='PERSONAL')
    with pytest.raises(GymDecisionError) as error:
        mutate(service, 'RESUME', 2, '2026-10-28', reactivation_date='2026-10-27')
    assert error.value.code == 'HISTORICAL_REACTIVATION'
    mutate(service, 'RESUME', 2, '2026-10-28', reactivation_date='2026-10-28')
    mutate(service, 'PAUSE', 3, '2026-11-02', reason_category='INJURY')
    value = mutate(service, 'RESUME', 4, '2026-11-04', reactivation_date='2026-11-04')
    segments = value['routine']['segments']
    assert len(segments) == 3
    assert all(left['end_date'] <= right['start_date'] for left, right in zip(segments, segments[1:]))


def test_request_retry_cas_payload_conflict_and_no_reset(store):
    db, service = store
    first = setup(service)
    assert setup(service)['idempotent_replay']
    with pytest.raises(GymDecisionError) as error:
        service.mutate('alice', 'SETUP', 1, 'setup-request-01', current=now())
    assert error.value.code == 'REQUEST_CONFLICT'
    with pytest.raises(GymDecisionError) as error:
        service.mutate('alice', 'SETUP', 1, 'different-setup-id', current=now())
    assert error.value.code == 'ALREADY_CONFIGURED'
    mutate(service, 'PAUSE', 1, '2026-10-26', reason_category='OTHER', note='Family commitment')
    # Retry of a formerly successful request must return the current revision,
    # rather than overwriting a newer client's cache with its old snapshot.
    retry = setup(service, current=now('2026-10-26'))
    assert retry['routine']['revision'] == 2
    assert retry['routine']['status'] == 'PAUSED'
    assert retry['routine']['routine_id'] == first['routine']['routine_id']
    with pytest.raises(GymDecisionError) as error:
        service.mutate('alice', 'PAUSE', 1, 'stale-pause-id', reason_category='TRAVEL', current=now('2026-10-27'))
    assert error.value.code == 'REVISION_CONFLICT'
    with db._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM gym_routine_events').fetchone()[0] == 2


def test_independent_accounts_never_share_configuration_or_idempotency(store):
    db, service = store
    a = setup(service)
    b = setup(service, account='bob')
    assert a['routine']['routine_id'] != b['routine']['routine_id']
    mutate(service, 'PAUSE', 1, '2026-10-26', reason_category='TRAVEL')
    assert service.get('bob', current=now('2026-10-26'))['routine']['status'] == 'ACTIVE'
    with db._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM gym_routine_requests').fetchone()[0] == 3


def test_timezone_uses_kolkata_calendar_not_host_or_elapsed_day(store):
    _, service = store
    setup(service)
    # UTC Oct 24 is already Oct 25 in Kolkata.
    value = service.get('alice', current=datetime(2026, 10, 24, 18, 31, tzinfo=timezone.utc))
    assert value['routine']['today']['date'] == '2026-10-25'
    assert value['routine']['today']['planned_state'] == 'GYM_DAY'
    assert sum(d['is_today'] for d in value['calendar']) == 1
    before = service.get('alice', current=datetime(2026, 10, 24, 18, 29, tzinfo=timezone.utc))
    assert before['routine']['today']['date'] == '2026-10-24'


def test_leap_day_month_and_year_boundaries_keep_calendar_parity(store):
    from datetime import date
    _, service = store
    setup(service)
    for month in ('2026-12', '2027-01', '2028-02', '2028-03'):
        values = service.get('alice', month=month, current=now('2026-10-26'))['calendar']
        for item in values:
            offset = (date.fromisoformat(item['date']) - date(2026, 10, 25)).days
            assert item['gym_planned'] == (offset % 2 == 0)
    leap = calendar_map(service.get('alice', month='2028-02', current=now('2026-10-26')))
    assert len(leap) == 29 and '2028-02-29' in leap


def test_daily_intent_fallback_and_explicit_or_booked_obligation_precedence(store):
    db, service = store
    with db._connect() as conn:
        # Frequent reads must not create the new schema or infer intent.
        assert DailyGymDecision.read(conn, 'alice', '2026-10-25')['gym_planned'] is None
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='gym_routines'").fetchone()
    setup(service)
    with db._connect() as conn:
        gym = DailyGymDecision.read(conn, 'alice', '2026-10-25')
        rest = DailyGymDecision.read(conn, 'alice', '2026-10-26')
        assert gym['gym_planned'] and gym['source'] == 'ALTERNATE_DAY_ROUTINE' and gym['revision'] == 0
        assert not rest['gym_planned'] and rest['routine_revision'] == 1
        conn.execute("INSERT INTO daily_gym_decisions(account_id,date,gym_planned,revision,decision_at,timezone,actor,device_id) VALUES('alice','2026-10-25',0,4,'stamp','Asia/Kolkata','alice','phone-a')")
        assert DailyGymDecision.read(conn, 'alice', '2026-10-25')['gym_planned'] is False
        assert DailyGymDecision.read(conn, 'alice', '2026-10-25')['revision'] == 4
        conn.execute("INSERT INTO scheduled_activities(activity_id,account_id,type,date,title,status) VALUES('booked','alice','GYM','2026-10-26','Existing','SUBMITTED')")
        booked = DailyGymDecision.read(conn, 'alice', '2026-10-26')
        assert booked['gym_planned'] and booked['source'] == 'EXISTING_BOOKED_SCHEDULE'
        conn.execute("INSERT INTO routine_schedules VALUES('alice','2026-10-27',1,'Asia/Kolkata','LOCKED',0)")
        assert DailyGymDecision.read(conn, 'alice', '2026-10-27')['gym_planned'] is False
    mutate(service, 'PAUSE', 1, '2026-10-26', reason_category='TRAVEL')
    with db._connect() as conn:
        assert DailyGymDecision.read(conn, 'alice', '2026-10-26')['gym_planned'] is True
        assert DailyGymDecision.read(conn, 'alice', '2026-10-28')['gym_planned'] is False
    value = service.get('alice', current=now('2026-10-26'))
    booked = calendar_map(value)['2026-10-26']
    assert booked['planned_state'] == 'PAUSED'
    assert booked['effective_daily_intent'] is True and booked['planning_exception']
    assert booked['daily_intent_source'] == 'EXISTING_BOOKED_SCHEDULE'
    explicit = calendar_map(value)['2026-10-25']
    assert explicit['effective_daily_intent'] is False and explicit['planning_exception']
    assert explicit['daily_intent_source'] == 'EXPLICIT_DAILY_CHOICE'
    assert value['routine']['today']['planning_exception']


def add_evidence(db, tmp_path, identity='one', day='2026-10-25', account='alice', status='VERIFIED', review_status='APPROVED'):
    path = tmp_path / (identity + '.jpg')
    path.write_bytes(b'fixture-proof-' + identity.encode())
    with db._connect() as conn:
        conn.execute('INSERT INTO scheduled_activities(activity_id,account_id,type,date,title,status) VALUES(?,?,?,?,?,?)',
            (identity, account, 'GYM', day, 'Workout ' + identity, status))
        conn.execute('INSERT INTO evidence_files(evidence_id,date,record_type,related_record_id,file_path,sha256_hash) VALUES(?,?,?,?,?,?)',
            ('ev-' + identity, day, 'GYM', identity, str(path), hashlib.sha256(path.read_bytes()).hexdigest()))
        conn.execute('INSERT INTO proof_reviews VALUES(?,?,?,?,?,?,?,?,?)',
            ('pr-' + identity, 'ev-' + identity, account, 'GYM', day, identity, review_status, 'APPROVE', 'trusted-admin'))
    return path


def add_points(db, identity, points, day='2026-10-25', *, evidence=None, activity=None, device='phone-a', kind='GYM_COMPLETED', dev=0, timestamp=None):
    with db._connect() as conn:
        conn.execute('INSERT INTO score_events(event_id,date,is_dev_mode,timestamp,event_type,device_id,description,points,evidence_id,related_task_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (identity, day, dev, timestamp or day + 'T19:00:00+05:30', kind, device, 'Actual fixture transaction', points, evidence, activity))


def test_statistics_exact_real_ledger_sessions_scope_and_no_writes(store, tmp_path):
    db, service = store
    setup(service)
    add_evidence(db, tmp_path)
    add_evidence(db, tmp_path, identity='pending', day='2026-10-27', status='SUBMITTED', review_status='PENDING')
    add_evidence(db, tmp_path, identity='foreign', account='bob')
    add_points(db, 'credit', 15, evidence='ev-one', activity='one')
    add_points(db, 'gym-deduct', -10, '2026-10-27', kind='GYM_SKIPPED')
    add_points(db, 'unverified', 3, '2026-10-27')
    add_points(db, 'foreign', 99, evidence='ev-foreign', activity='foreign', device='phone-b')
    add_points(db, 'cross-linked-foreign', 88, evidence='ev-foreign', device='phone-a')
    add_points(db, 'dev', 777, evidence='ev-one', activity='one', dev=1)
    add_points(db, 'nongym', 100, kind='HOURLY_EVIDENCE')
    add_points(db, 'before-start', 123, '2026-10-24')
    add_points(db, 'future', 321, '2026-11-01')
    add_points(db, 'unattributed', 90, device='legacy-unowned')
    with db._connect() as conn:
        before = [tuple(r) for r in conn.execute('SELECT * FROM score_events ORDER BY event_id')]
        activities_before = [tuple(r) for r in conn.execute('SELECT * FROM scheduled_activities ORDER BY activity_id')]
    stats = service.get('alice', current=now('2026-10-28'))['statistics']
    assert stats['verified_sessions'] == 1 and stats['pending_sessions'] == 1
    assert stats['missed_sessions'] == 0
    assert stats['planned_gym_days'] == 2
    assert stats['completion_rate'] == 50.0
    assert stats['verified_points'] == 15
    assert stats['recorded_unverified_points'] == 3
    assert stats['provisional_points'] == 0
    assert stats['deductions'] == 10 and stats['net_points'] == 8
    assert stats['unattributed_count'] == 1 and stats['streak'] is None
    assert {e['event_id'] for e in stats['history']} == {'credit', 'gym-deduct', 'unverified'}
    assert {d['date'] for d in stats['daily']} == {'2026-10-25', '2026-10-27'}
    mutate(service, 'PAUSE', 1, '2026-10-28', reason_category='TRAVEL')
    mutate(service, 'RESUME', 2, '2026-10-29')
    for _ in range(2):
        service.get('alice', current=now('2026-10-30'))
    with db._connect() as conn:
        assert [tuple(r) for r in conn.execute('SELECT * FROM score_events ORDER BY event_id')] == before
        assert [tuple(r) for r in conn.execute('SELECT * FROM scheduled_activities ORDER BY activity_id')] == activities_before


@pytest.mark.parametrize('damage', ['missing', 'changed', 'deleted', 'wrong_date', 'wrong_link', 'provisional', 'taufa'])
def test_untrusted_or_missing_proof_never_shows_verified(store, tmp_path, damage):
    db, service = store
    setup(service)
    path = add_evidence(db, tmp_path)
    add_points(db, 'credit', 15, evidence='ev-one', activity='one')
    with db._connect() as conn:
        if damage == 'missing':
            path.unlink()
        elif damage == 'changed':
            path.write_bytes(b'changed-proof')
        elif damage == 'deleted':
            conn.execute('UPDATE evidence_files SET is_deleted=1')
        elif damage == 'wrong_date':
            conn.execute("UPDATE evidence_files SET date='2026-10-26'")
        elif damage == 'wrong_link':
            conn.execute("UPDATE evidence_files SET related_record_id='other-session'")
        elif damage == 'provisional':
            conn.execute("UPDATE proof_reviews SET decision='PROVISIONAL_APPROVE'")
        elif damage == 'taufa':
            conn.execute("UPDATE proof_reviews SET reviewed_by='Taufa AI'")
    result = service.get('alice', current=now('2026-10-26'))
    assert result['statistics']['verified_sessions'] == 0
    assert result['statistics']['verified_points'] == 0
    assert calendar_map(result)['2026-10-25']['attendance_status'] == 'RECORDED_UNVERIFIED'
    assert result['statistics']['net_points'] == 15
    if damage == 'provisional':
        assert result['statistics']['provisional_points'] == 15
    else:
        assert result['statistics']['recorded_unverified_points'] == 15


def test_actual_provisional_points_are_separate_and_timestamp_uses_kolkata(store, tmp_path):
    db, service = store
    setup(service)
    add_evidence(db, tmp_path, identity='p', review_status='AI_PROVISIONAL', status='SUBMITTED')
    # UTC date Oct 24 is local Oct 25. Real timestamp assigns the ledger day.
    add_points(db, 'p-credit', 4, '2026-10-24', evidence='ev-p', activity='p', timestamp='2026-10-24T19:00:00Z')
    stats = service.get('alice', current=now('2026-10-26'))['statistics']
    assert stats['provisional_points'] == 4 and stats['verified_points'] == 0
    assert stats['history'][0]['date'] == '2026-10-25'


def test_filters_clamp_launch_today_and_future_denominator(store):
    _, service = store
    setup(service)
    value = service.get('alice', current=now('2026-10-26'), filter='CUSTOM', start_date='2026-01-01', end_date='2026-12-31')
    stats = value['statistics']
    assert (stats['start_date'], stats['end_date']) == ('2026-10-25', '2026-10-26')
    assert stats['planned_gym_days'] == 1 and stats['verified_sessions'] == 0
    assert calendar_map(value)['2026-10-31']['planned_state'] == 'GYM_DAY'
    assert calendar_map(value)['2026-10-31']['attendance_status'] is None
    assert service.get('alice', current=now('2026-10-26'), filter='LAST_MONTH')['statistics']['has_tracked_period'] is False
    assert service.get('alice', current=now('2026-10-26'), filter='THIS_WEEK')['statistics']['start_date'] == '2026-10-26'
    with pytest.raises(GymDecisionError):
        service.get('alice', current=now(), filter='CUSTOM', start_date='2026-11-02', end_date='2026-11-01')


@pytest.mark.parametrize('month', ['2026-13', '2026-00', '2026-2', 'not-month'])
def test_calendar_invalid_month_rejected(store, month):
    _, service = store
    with pytest.raises(GymDecisionError):
        service.get('alice', month=month, current=now())


def test_failure_rolls_back_transition_and_no_false_success(store, monkeypatch):
    db, service = store
    setup(service)
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError('fixture persistence error')
    monkeypatch.setattr(service, '_snapshot', unavailable)
    with pytest.raises(sqlite3.OperationalError):
        mutate(service, 'PAUSE', 1, '2026-10-26', reason_category='TRAVEL')
    with db._connect() as conn:
        assert conn.execute("SELECT revision FROM gym_routines WHERE account_id='alice'").fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM gym_routine_events').fetchone()[0] == 1
        assert conn.execute('SELECT end_date FROM gym_routine_segments').fetchone()[0] is None


def test_real_correction_credit_reconciles_totals_even_when_parent_outside_filter(store):
    db, service = store
    setup(service)
    add_points(db, 'old-penalty', -10, kind='GYM_SKIPPED')
    add_points(db, 'refund', 10, day='2026-10-27', kind='SCORE_CORRECTION')
    with db._connect() as conn:
        conn.execute("UPDATE score_events SET parent_event_id='old-penalty' WHERE event_id='refund'")
    result = service.get('alice', current=now('2026-10-28'), filter='CUSTOM', start_date='2026-10-27', end_date='2026-10-28')
    stats = result['statistics']
    assert stats['recorded_adjustment_credits'] == 10
    assert stats['verified_points'] == stats['provisional_points'] == stats['recorded_unverified_points'] == 0
    assert stats['net_points'] == (stats['verified_points'] + stats['provisional_points'] + stats['recorded_unverified_points'] + stats['recorded_adjustment_credits'] - stats['deductions']) == 10
    assert [e['event_id'] for e in stats['history']] == ['refund']
    assert stats['history'][0]['verification_status'] == 'RECORDED_ADJUSTMENT'
    assert stats['daily'][0]['recorded_adjustment_credits'] == 10


def test_concurrent_duplicate_and_stale_transitions_are_serialized(store):
    from concurrent.futures import ThreadPoolExecutor
    db, service = store
    setup(service)
    def submit(_):
        return service.mutate('alice', 'PAUSE', 1, 'concurrent-same-request', reason_category='TRAVEL', current=now('2026-10-26'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert all(r['routine']['revision'] == 2 for r in results)
    assert sum(bool(r.get('idempotent_replay')) for r in results) == 1
    with db._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM gym_routine_events').fetchone()[0] == 2
    def resume(index):
        try:
            return service.mutate('alice', 'RESUME', 2, 'different-request-' + str(index), current=now('2026-10-28'))
        except GymDecisionError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resume, range(2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert 'REVISION_CONFLICT' in results
    with db._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM gym_routine_segments').fetchone()[0] == 2


def authenticated_handler(db, *, token='token-alice', device='phone-a', origin=None):
    from core.server import StrictRecordsRequestHandler
    handler = object.__new__(StrictRecordsRequestHandler)
    handler.db = db
    handler.client_address = ('192.0.2.20', 23456)
    handler.headers = {'X-Device-Id': device, 'X-Device-Token': token, 'Host': '192.0.2.10:8765'}
    if origin:
        handler.headers['Origin'] = origin
    replies = []
    handler._send_json = lambda data, status=200: replies.append((status, data))
    return handler, replies


def test_authenticated_get_post_and_cross_account_scope_use_real_principal(store, monkeypatch):
    import core.gym_routine as module
    db, _ = store
    monkeypatch.setattr(module, 'now_ist', lambda: now())
    handler, replies = authenticated_handler(db)
    handler._handle_gym_routine(query={'month': ['2026-10']})
    assert replies[-1][0] == 200 and replies[-1][1]['routine']['account_id'] == 'alice'
    handler._handle_gym_routine(body=dict(action='SETUP', expected_revision=0, request_id='http-setup-request', account_id='alice'))
    assert replies[-1][0] == 200 and replies[-1][1]['routine']['revision'] == 1
    handler._handle_gym_routine(query={'account_id': ['bob']})
    assert replies[-1][0] == 403 and replies[-1][1]['code'] == 'UNAUTHORIZED'
    handler._handle_gym_routine(body=dict(action='PAUSE', expected_revision=1, request_id='http-foreign-request', account_id='bob', reason_category='TRAVEL'))
    assert replies[-1][0] == 403
    with db._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM gym_routines').fetchone()[0] == 1
        assert conn.execute("SELECT revision FROM gym_routines WHERE account_id='alice'").fetchone()[0] == 1


@pytest.mark.parametrize('failure', ['wrong-token', 'revoked-device', 'revoked-credential', 'cross-origin'])
def test_routine_rejects_invalid_authentication_and_origin(store, failure):
    db, _ = store
    if failure == 'revoked-device':
        with db._connect() as conn:
            conn.execute("UPDATE devices SET pairing_state='REVOKED' WHERE device_id='phone-a'")
    if failure == 'revoked-credential':
        with db._connect() as conn:
            conn.execute("UPDATE device_credentials SET revoked_at='fixture-revoked' WHERE device_id='phone-a'")
    handler, replies = authenticated_handler(db, token='wrong' if failure == 'wrong-token' else 'token-alice',
        origin='https://foreign.example' if failure == 'cross-origin' else None)
    handler._handle_gym_routine(body=dict(action='SETUP', expected_revision=0, request_id='rejected-http-request'))
    assert replies[-1][0] == 403
    with db._connect() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='gym_routines'").fetchone()


def test_empty_post_is_not_accidentally_treated_as_read_and_persistence_error_reported(store, monkeypatch):
    db, _ = store
    handler, replies = authenticated_handler(db)
    handler._handle_gym_routine(body={})
    assert replies[-1][0] == 409 and replies[-1][1]['code'] == 'REVISION_REQUIRED'
    def failed(*args, **kwargs):
        raise sqlite3.OperationalError('fixture unavailable')
    monkeypatch.setattr(GymRoutine, 'get', failed)
    handler._handle_gym_routine(query={})
    assert replies[-1] == (503, dict(success=False, code='PERSISTENCE_UNAVAILABLE', error='The routine could not be saved or refreshed. Retry with the same request ID.'))
