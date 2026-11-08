"""Account-scoped alternate-day plans. Calendar operations never write scores.

Segments contain local dates in [start_date, end_date). An explicit daily
decision or a retained booked obligation takes precedence over this plan.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import uuid
from zoneinfo import ZoneInfo

from core.gym_daily_decision import GymDecisionError
from core.time_window_engine import now_ist

START_DATE = '2026-10-25'
TIMEZONE = 'Asia/Kolkata'
ZONE = ZoneInfo(TIMEZONE)
REASONS = ('ILLNESS', 'INJURY', 'TRAVEL', 'WORK_STUDY', 'PERSONAL', 'OTHER')


def local_now(current=None):
    value = current or now_ist()
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZONE)
    return value.astimezone(ZONE)


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise GymDecisionError('INVALID_DATE', 'Use a local calendar date in YYYY-MM-DD format')
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise GymDecisionError('INVALID_DATE', 'Select a valid calendar date') from error


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _json(value):
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def _retained(evidence, review, activities, proof_cache=None):
    if not evidence or evidence['record_type'].upper() != 'GYM' or evidence['date'] != review['date']:
        return False
    activity_id = review.get('activity_id')
    if not activity_id or activity_id not in activities or activities[activity_id]['date'] != review['date']:
        return False
    if evidence['related_record_id'] not in (activity_id, review['review_id']):
        return False
    try:
        path = Path(evidence['file_path'])
        if not path.is_file():
            return False
        expected = evidence.get('sha256_hash')
        stat = path.stat()
        cache_key = (evidence['evidence_id'], str(path), expected, stat.st_size, stat.st_mtime_ns)
        if proof_cache is not None and cache_key in proof_cache:
            return proof_cache[cache_key]
        if expected:
            if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected):
                return False
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            if digest.hexdigest().lower() != expected.lower():
                return False
        if proof_cache is not None:
            proof_cache[cache_key] = True
        return True
    except (OSError, TypeError, ValueError):
        return False


def _event_day(event):
    try:
        stamp = datetime.fromisoformat(str(event['timestamp']).replace('Z', '+00:00'))
        if stamp.tzinfo:
            return stamp.astimezone(ZONE).date().isoformat()
    except (ValueError, TypeError):
        pass
    return event['date']


class GymRoutine:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def ensure_schema(conn):
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS gym_routines(
                account_id TEXT PRIMARY KEY, routine_id TEXT NOT NULL UNIQUE,
                start_date TEXT NOT NULL, timezone TEXT NOT NULL,
                status TEXT NOT NULL, revision INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS gym_routine_segments(
                segment_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                routine_id TEXT NOT NULL, anchor_date TEXT NOT NULL,
                start_date TEXT NOT NULL, end_date TEXT,
                created_revision INTEGER NOT NULL,
                CHECK(end_date IS NULL OR end_date >= start_date),
                UNIQUE(account_id,created_revision));
            CREATE UNIQUE INDEX IF NOT EXISTS gym_routine_single_open_segment
                ON gym_routine_segments(account_id) WHERE end_date IS NULL;
            CREATE INDEX IF NOT EXISTS gym_routine_segment_dates
                ON gym_routine_segments(account_id,start_date,end_date);
            CREATE TABLE IF NOT EXISTS gym_routine_events(
                event_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                routine_id TEXT NOT NULL, event_type TEXT NOT NULL,
                effective_date TEXT NOT NULL, created_at TEXT NOT NULL,
                reason_category TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
                previous_status TEXT NOT NULL, previous_anchor_date TEXT,
                anchor_date TEXT, revision INTEGER NOT NULL,
                actor_id TEXT NOT NULL, device_id TEXT NOT NULL,
                UNIQUE(account_id,revision));
            CREATE TABLE IF NOT EXISTS gym_routine_requests(
                account_id TEXT NOT NULL, request_id TEXT NOT NULL,
                request_sha256 TEXT NOT NULL, result_json TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(account_id,request_id));
        ''')

    @staticmethod
    def read_routine(conn, account, current=None):
        current = local_now(current)
        row = conn.execute('SELECT * FROM gym_routines WHERE account_id=?', (account,)).fetchone() if _table(conn, 'gym_routines') else None
        value = dict(row) if row else dict(account_id=account, routine_id=None, start_date=START_DATE,
            timezone=TIMEZONE, status='SETUP_REQUIRED', revision=0, created_at=None, updated_at=None)
        value['segments'] = [dict(r) for r in conn.execute('SELECT * FROM gym_routine_segments WHERE account_id=? ORDER BY start_date,created_revision', (account,))] if row else []
        value['events'] = [dict(r) for r in conn.execute('SELECT * FROM gym_routine_events WHERE account_id=? ORDER BY revision', (account,))] if row else []
        value['anchor_date'] = value['segments'][-1]['anchor_date'] if value['segments'] else START_DATE
        value['today'] = GymRoutine.day(value, current.date().isoformat(), current)
        pending = next((s['start_date'] for s in value['segments'] if s['start_date'] > current.date().isoformat() and s['created_revision'] > 1), None)
        value['stored_status'] = row['status'] if row else 'SETUP_REQUIRED'
        if row:
            state = value['today']['planned_state']
            value['status'] = 'PAUSED' if row['status'] == 'PAUSED' or pending else 'NOT_STARTED' if state == 'NOT_TRACKED' else 'PAUSED' if state == 'PAUSED' else 'ACTIVE'
        value['pending_resume_date'] = pending
        value['can_pause'] = bool(row and any(not s['end_date'] for s in value['segments']) and not pending)
        value['can_resume'] = bool(row and row['status'] == 'PAUSED')
        upcoming = []
        today = max(current.date(), parse_date(START_DATE))
        for segment in value['segments']:
            begin = max(today, parse_date(segment['start_date']))
            end = parse_date(segment['end_date']) if segment['end_date'] else date.max
            if begin >= end:
                continue
            offset = (begin - parse_date(segment['anchor_date'])).days
            gym = begin if offset % 2 == 0 else begin + timedelta(days=1)
            rest = begin if offset % 2 else begin + timedelta(days=1)
            upcoming.extend((kind, candidate.isoformat()) for kind, candidate in [('GYM', gym), ('REST', rest)] if candidate < end)
        value['next_gym_date'] = min((d for kind, d in upcoming if kind == 'GYM'), default=None)
        value['next_rest_date'] = min((d for kind, d in upcoming if kind == 'REST'), default=None)
        value['server_time'] = current.isoformat()
        value['planning_only'] = True
        return value

    @staticmethod
    def day(routine, day, current=None):
        target = parse_date(day)
        result = dict(date=day, weekday=target.strftime('%A'), is_today=target == local_now(current).date(),
            planned_state='SETUP_REQUIRED', gym_planned=None, segment_id=None, anchor_date=None, pause_reason=None)
        if day < routine['start_date']:
            result['planned_state'] = 'NOT_TRACKED'
            return result
        if not routine.get('routine_id'):
            return result
        if routine.get('segments') and day < routine['segments'][0]['start_date']:
            result['planned_state'] = 'NOT_TRACKED'
            return result
        result.update(planned_state='PAUSED', gym_planned=False)
        for segment in routine.get('segments', []):
            if segment['start_date'] <= day and (not segment['end_date'] or day < segment['end_date']):
                gym = (target - parse_date(segment['anchor_date'])).days % 2 == 0
                result.update(planned_state='GYM_DAY' if gym else 'REST_DAY', gym_planned=gym,
                    segment_id=segment['segment_id'], anchor_date=segment['anchor_date'])
                return result
        pauses = [e for e in routine.get('events', []) if e['event_type'] == 'PAUSE' and e['effective_date'] <= day]
        if pauses:
            result['pause_reason'] = {k: pauses[-1][k] for k in ('reason_category', 'note', 'effective_date')}
        return result

    def get(self, account, *, month=None, filter='THIS_MONTH', start_date=None, end_date=None, current=None):
        if not isinstance(account, str) or not account.strip():
            raise GymDecisionError('ACCOUNT_REQUIRED', 'An authenticated account is required', 403)
        current = local_now(current)
        if month is None:
            month = current.strftime('%Y-%m')
        if not isinstance(month, str) or not re.fullmatch(r'\d{4}-\d{2}', month):
            raise GymDecisionError('INVALID_MONTH', 'Select a calendar month in YYYY-MM format')
        first = parse_date(month + '-01')
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        with self.db._connect() as conn:
            self.ensure_schema(conn)
            conn.execute('BEGIN')
            return self._snapshot(conn, account, current, first, last, filter, start_date, end_date)

    def _snapshot(self, conn, account, current, first, last, period, start, end):
        routine = self.read_routine(conn, account, current)
        self._intent(conn, account, routine['today'], current)
        # One request may render both calendar and statistics for overlapping
        # dates. Verify retained bytes once per unchanged file in that request.
        proof_cache = {}
        stats = self.statistics(conn, account, routine, period, start, end, current, proof_cache)
        facts = self._facts(conn, account, max(first, parse_date(START_DATE)).isoformat(), min(last, current.date()).isoformat(), proof_cache) if first <= current.date() else dict(sessions={}, ledger=[], unattributed_count=0)
        days = []
        for offset in range((last - first).days + 1):
            day = (first + timedelta(days=offset)).isoformat()
            item = self.day(routine, day, current)
            self._intent(conn, account, item, current)
            sessions = facts['sessions'].get(day, [])
            ledger = [e for e in facts['ledger'] if e['date'] == day]
            states = sorted({s['attendance_status'] for s in sessions})
            item.update(attendance_status=(', '.join(states) if states else None), session_ids=[s['activity_id'] for s in sessions],
                sessions=sessions, verified_points=sum(e['points'] for e in ledger if e['points'] > 0 and e['verification_status'] == 'VERIFIED'),
                deductions=sum(-e['points'] for e in ledger if e['points'] < 0), net_points=sum(e['points'] for e in ledger))
            days.append(item)
        return dict(success=True, routine=routine, calendar=days, statistics=stats)

    @staticmethod
    def _intent(conn, account, item, current):
        if _table(conn, 'daily_gym_decisions') and _table(conn, 'routine_schedules') and _table(conn, 'scheduled_activities'):
            from core.gym_daily_decision import DailyGymDecision
            decision = DailyGymDecision.read(conn, account, item['date'], current)
        else:
            decision = dict(gym_planned=None, source='DECISION_REQUIRED')
        item['effective_daily_intent'] = decision['gym_planned']
        item['daily_intent_source'] = decision.get('source') or ('EXPLICIT_DAILY_CHOICE' if decision.get('revision', 0) > 0 else 'DECISION_REQUIRED')
        item['planning_exception'] = bool(type(decision['gym_planned']) is bool and type(item['gym_planned']) is bool and decision['gym_planned'] != item['gym_planned'])

    def mutate(self, account, action, expected_revision, request_id, *, reason_category='', note='',
               reactivation_date=None, actor_id='', device_id='', current=None):
        if not isinstance(account, str) or not account.strip():
            raise GymDecisionError('ACCOUNT_REQUIRED', 'An authenticated account is required', 403)
        if type(expected_revision) is not int or expected_revision < 0:
            raise GymDecisionError('REVISION_REQUIRED', 'Refresh the saved routine revision before confirming', 409)
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 128:
            raise GymDecisionError('REQUEST_ID_REQUIRED', 'A stable request identifier is required')
        if action not in ('SETUP', 'PAUSE', 'RESUME'):
            raise GymDecisionError('INVALID_ACTION', 'Choose SETUP, PAUSE or RESUME')
        if not isinstance(note, str) or len(note) > 500 or not isinstance(reason_category, str):
            raise GymDecisionError('INVALID_REASON', 'Keep the optional note within 500 characters')
        note, reason_category = note.strip(), reason_category.strip().upper()
        if action == 'PAUSE' and (reason_category not in REASONS or (reason_category == 'OTHER' and not note)):
            raise GymDecisionError('REASON_REQUIRED', 'Select a pause reason; add a short explanation for Other')
        current = local_now(current)
        requested = [account, action, expected_revision, reason_category, note, reactivation_date]
        digest = hashlib.sha256(json.dumps(requested, separators=(',', ':')).encode()).hexdigest()
        with self.db._connect() as conn:
            self.ensure_schema(conn)
            conn.execute('BEGIN IMMEDIATE')
            prior = conn.execute('SELECT * FROM gym_routine_requests WHERE account_id=? AND request_id=?', (account, request_id)).fetchone()
            if prior:
                if prior['request_sha256'] != digest:
                    raise GymDecisionError('REQUEST_CONFLICT', 'This request ID was already used for different choices', 409)
                original = json.loads(prior['result_json'])
                first = current.date().replace(day=1)
                last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
                result = self._snapshot(conn, account, current, first, last, 'THIS_MONTH', None, None)
                result['mutation'] = original['mutation']
                result['idempotent_replay'] = True
                return result
            old = self.read_routine(conn, account, current)
            if expected_revision != old['revision']:
                raise GymDecisionError('REVISION_CONFLICT', 'A newer routine exists. Refresh before confirming.', 409)
            stamp = current.isoformat()
            effective = max(current.date(), parse_date(START_DATE)).isoformat()
            revision = old['revision'] + 1
            identity = old['routine_id'] or 'gym_routine_' + uuid.uuid4().hex
            if action == 'SETUP':
                if old['routine_id']:
                    raise GymDecisionError('ALREADY_CONFIGURED', 'The saved routine cannot be reset by setup', 409)
                conn.execute('INSERT INTO gym_routines VALUES(?,?,?,?,?,?,?,?)', (account, identity, START_DATE, TIMEZONE, 'ACTIVE', revision, stamp, stamp))
            elif not old['routine_id']:
                raise GymDecisionError('SETUP_REQUIRED', 'Confirm initial routine setup first', 409)
            elif action == 'PAUSE':
                if old['pending_resume_date']:
                    raise GymDecisionError('RESUME_PENDING', 'A future reactivation is already confirmed; refresh its date before another change', 409)
                open_segment = next((s for s in old['segments'] if not s['end_date']), None)
                if not open_segment:
                    raise GymDecisionError('ALREADY_PAUSED', 'This routine is already paused', 409)
                conn.execute('UPDATE gym_routine_segments SET end_date=? WHERE segment_id=? AND account_id=?', (effective, open_segment['segment_id'], account))
            elif action == 'RESUME':
                target = parse_date(reactivation_date or effective)
                if target < max(current.date(), parse_date(START_DATE)):
                    raise GymDecisionError('HISTORICAL_REACTIVATION', 'Resume today or on a future date; historical cycles cannot be rewritten')
                if any(not s['end_date'] for s in old['segments']):
                    raise GymDecisionError('ALREADY_ACTIVE', 'An active or pending cycle already exists', 409)
                effective = target.isoformat()
                if any(s['end_date'] and effective < s['end_date'] for s in old['segments']):
                    raise GymDecisionError('CYCLE_OVERLAP', 'Reactivation cannot overlap a retained cycle', 409)
            if action in ('SETUP', 'RESUME'):
                anchor = START_DATE if action == 'SETUP' else effective
                conn.execute('INSERT INTO gym_routine_segments VALUES(?,?,?,?,?,?,?)', ('gym_segment_' + uuid.uuid4().hex, account, identity, anchor, effective, None, revision))
            if action != 'SETUP':
                conn.execute('UPDATE gym_routines SET status=?,revision=?,updated_at=? WHERE account_id=? AND revision=?',
                    ('PAUSED' if action == 'PAUSE' else 'ACTIVE', revision, stamp, account, expected_revision))
            conn.execute('INSERT INTO gym_routine_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('gym_event_' + uuid.uuid4().hex, account, identity, action, effective, stamp,
                 reason_category if action == 'PAUSE' else '', note if action == 'PAUSE' else '', old['status'],
                 old['anchor_date'] if old['routine_id'] else None, START_DATE if action == 'SETUP' else effective if action == 'RESUME' else old['anchor_date'], revision, actor_id, device_id))
            first = current.date().replace(day=1)
            last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
            result = self._snapshot(conn, account, current, first, last, 'THIS_MONTH', None, None)
            result['mutation'] = dict(action=action, request_id=request_id, revision=revision, committed=True)
            conn.execute('INSERT INTO gym_routine_requests VALUES(?,?,?,?,?)', (account, request_id, digest, json.dumps(result), stamp))
            return result

    @staticmethod
    def _facts(conn, account, start, end, proof_cache=None):
        """Read only real production-mode rows, with explicit owner/device links.

        Unlinked old ledger rows are not assigned to the active account by default.
        Approved sessions require a retained evidence file and a finalized review.
        """
        result = dict(sessions={}, ledger=[], unattributed_count=0)
        if start > end:
            return result
        activities = {}
        if _table(conn, 'scheduled_activities'):
            activities = {r['activity_id']: dict(r) for r in conn.execute("SELECT * FROM scheduled_activities WHERE account_id=? AND type='GYM' AND date BETWEEN ? AND ?", (account, start, end))}
        reviews = {}
        if _table(conn, 'proof_reviews'):
            for row in conn.execute("SELECT * FROM proof_reviews WHERE user_id=? AND UPPER(task_code)='GYM' AND date BETWEEN ? AND ?", (account, start, end)):
                review = dict(row)
                evidence = conn.execute('SELECT * FROM evidence_files WHERE evidence_id=? AND is_dev_mode=0 AND is_deleted=0', (review['evidence_id'],)).fetchone() if _table(conn, 'evidence_files') else None
                retained = _retained(dict(evidence) if evidence else None, review, activities, proof_cache)
                decision = str(review.get('decision') or '').upper()
                reviewer = str(review.get('reviewed_by') or '')
                review['_verified'] = bool(retained and review['status'] in ('APPROVED', 'VERIFIED') and reviewer and 'TAUFA' not in reviewer.upper() and 'PROVISIONAL' not in decision)
                review['_retained'] = retained
                reviews[review['evidence_id']] = review
        for activity in activities.values():
            linked = [r for r in reviews.values() if r.get('activity_id') == activity['activity_id']]
            approved = any(r['_verified'] for r in linked)
            pending = activity['status'] in ('SUBMITTED', 'VALIDATING', 'PENDING_REVIEW', 'AI_PROVISIONAL', 'PENDING_SYNC') or any(r['status'] in ('PENDING', 'PENDING_REVIEW', 'VALIDATING', 'PROCESSING', 'AI_PROVISIONAL', 'NEEDS_REVIEW') and r['_retained'] for r in linked)
            actual = 'VERIFIED' if approved and activity['status'] in ('VERIFIED', 'APPROVED', 'COMPLETED') else 'PENDING' if pending else 'MISSED' if activity['status'] == 'MISSED' else 'RECORDED_UNVERIFIED' if activity['status'] in ('VERIFIED', 'APPROVED', 'COMPLETED') else activity['status']
            item = dict(activity_id=activity['activity_id'], title=activity.get('title') or 'Gym workout', attendance_status=actual,
                stored_status=activity['status'], verified=actual == 'VERIFIED', pending=actual == 'PENDING', missed=actual == 'MISSED')
            result['sessions'].setdefault(activity['date'], []).append(item)
        if not _table(conn, 'score_events'):
            return result
        device_owners = {r[0]: r[1] for r in conn.execute('SELECT device_id,user_id FROM devices')} if _table(conn, 'devices') else {}
        devices = {identity for identity, owner in device_owners.items() if owner == account}
        reliable_types = {'GYM_COMPLETED', 'GYM_EVIDENCE', 'GYM_SKIPPED', 'GYM_MISSED', 'GYM_PENALTY'}
        low, high = (parse_date(start) - timedelta(days=1)).isoformat(), (parse_date(end) + timedelta(days=1)).isoformat()
        events = []
        for row in conn.execute('SELECT * FROM score_events WHERE is_dev_mode=0 AND date BETWEEN ? AND ? ORDER BY timestamp,event_id', (low, high)):
            event = dict(row)
            event['date'] = _event_day(event)
            if start <= event['date'] <= end:
                events.append(event)
        accepted = {}
        for event in events:
            review = reviews.get(event.get('evidence_id'))
            activity = activities.get(event.get('related_task_id'))
            if not activity and review:
                activity = activities.get(review.get('activity_id'))
            audit = _json(event.get('audit_json'))
            owner = audit.get('account_id') or audit.get('user_id')
            explicitly_foreign = bool(owner and owner != account)
            if event.get('related_task_id') and _table(conn, 'scheduled_activities'):
                linked_owner = conn.execute('SELECT account_id FROM scheduled_activities WHERE activity_id=?', (event['related_task_id'],)).fetchone()
                explicitly_foreign = explicitly_foreign or bool(linked_owner and linked_owner[0] != account)
            if event.get('evidence_id') and _table(conn, 'proof_reviews'):
                linked_owner = conn.execute('SELECT user_id FROM proof_reviews WHERE evidence_id=?', (event['evidence_id'],)).fetchone()
                explicitly_foreign = explicitly_foreign or bool(linked_owner and linked_owner[0] != account)
            scoped = not explicitly_foreign and bool(activity or review or owner == account or event['device_id'] in devices)
            gym = bool(activity or review or event['event_type'] in reliable_types)
            if not gym:
                continue
            if not scoped:
                # A foreign account's history must never be exposed as unattributed.
                if not explicitly_foreign and event['device_id'] not in device_owners:
                    result['unattributed_count'] += 1
                continue
            accepted[event['event_id']] = (event, review)
        # Exact compensating ledger entries retain their real signed value.
        for event in events:
            parent = accepted.get(event.get('parent_event_id'))
            if not parent and event['event_type'] == 'SCORE_CORRECTION' and event.get('parent_event_id'):
                # A correction in this period may refer to a Gym transaction
                # outside it. Read that exact parent rather than losing the
                # recorded correction at the filter boundary.
                record = conn.execute('SELECT * FROM score_events WHERE event_id=? AND is_dev_mode=0', (event['parent_event_id'],)).fetchone()
                if record:
                    record = dict(record)
                    audit = _json(record.get('audit_json'))
                    owner = audit.get('account_id') or audit.get('user_id')
                    activity = conn.execute('SELECT account_id,type FROM scheduled_activities WHERE activity_id=?', (record.get('related_task_id'),)).fetchone() if _table(conn, 'scheduled_activities') else None
                    review = conn.execute('SELECT user_id,task_code FROM proof_reviews WHERE evidence_id=?', (record.get('evidence_id'),)).fetchone() if _table(conn, 'proof_reviews') else None
                    foreign = bool(owner and owner != account) or bool(activity and activity['account_id'] != account) or bool(review and review['user_id'] != account)
                    scoped = owner == account or record['device_id'] in devices or bool(activity and activity['account_id'] == account) or bool(review and review['user_id'] == account)
                    gym = record['event_type'] in reliable_types or bool(activity and activity['type'] == 'GYM') or bool(review and str(review['task_code']).upper() == 'GYM')
                    if not foreign and scoped and gym:
                        parent = (record, None)
            if parent and event['event_type'] == 'SCORE_CORRECTION':
                audit = _json(event.get('audit_json'))
                if audit.get('account_id', audit.get('user_id', account)) == account:
                    accepted[event['event_id']] = (event, parent[1])
        for event, review in accepted.values():
            positive = event['points'] > 0
            provisional = bool(review and (review['status'] in ('AI_PROVISIONAL', 'PROVISIONAL') or 'PROVISIONAL' in str(review.get('decision') or '').upper()))
            status = ('RECORDED_ADJUSTMENT' if event['event_type'] == 'SCORE_CORRECTION' else
                'VERIFIED' if positive and review and review['_verified'] else 'PROVISIONAL' if positive and provisional else
                'RECORDED_CREDIT_UNVERIFIED' if positive else 'RECORDED_DEDUCTION' if event['points'] < 0 else 'RECORDED')
            item = {k: event.get(k) for k in ('event_id', 'date', 'timestamp', 'event_type', 'description', 'points', 'evidence_id', 'related_task_id', 'parent_event_id')}
            item['verification_status'] = status
            result['ledger'].append(item)
        result['ledger'].sort(key=lambda e: (e['timestamp'], e['event_id']), reverse=True)
        return result

    @classmethod
    def statistics(cls, conn, account, routine, period, start, end, current, proof_cache=None):
        aliases = {'week': 'THIS_WEEK', 'month': 'THIS_MONTH', 'last_month': 'LAST_MONTH', 'custom': 'CUSTOM'}
        period = aliases.get(period, period)
        today = current.date()
        if period == 'THIS_WEEK':
            first, last = today - timedelta(days=today.weekday()), today
        elif period == 'THIS_MONTH':
            first, last = today.replace(day=1), today
        elif period == 'LAST_MONTH':
            last = today.replace(day=1) - timedelta(days=1)
            first = last.replace(day=1)
        elif period == 'CUSTOM':
            first, last = parse_date(start), parse_date(end)
            if last < first:
                raise GymDecisionError('INVALID_RANGE', 'The end date must be on or after the start date')
        else:
            raise GymDecisionError('INVALID_FILTER', 'Choose this week, this month, last month or a custom range')
        first, last = max(first, parse_date(START_DATE)), min(last, today)
        if last >= first and (last - first).days > 3660:
            raise GymDecisionError('RANGE_TOO_LARGE', 'Choose a history range of ten years or less')
        facts = cls._facts(conn, account, first.isoformat(), last.isoformat(), proof_cache)
        planned = [cls.day(routine, (first + timedelta(days=i)).isoformat(), current) for i in range(max(0, (last - first).days + 1))]
        sessions = [s for values in facts['sessions'].values() for s in values]
        verified = sum(s['verified'] for s in sessions)
        planned_days = sum(d['planned_state'] == 'GYM_DAY' for d in planned)
        verified_planned_dates = {d['date'] for d in planned if d['planned_state'] == 'GYM_DAY' and any(s['verified'] for s in facts['sessions'].get(d['date'], []))}
        ledger = facts['ledger']
        daily = []
        # Sparse recorded dates; missing observations are not invented zeroes.
        for day in sorted(set(facts['sessions']) | {e['date'] for e in ledger}):
            points = [e for e in ledger if e['date'] == day]
            daily.append(dict(date=day, verified_sessions=sum(s['verified'] for s in facts['sessions'].get(day, [])),
                verified_points=sum(e['points'] for e in points if e['points'] > 0 and e['verification_status'] == 'VERIFIED'),
                provisional_points=sum(e['points'] for e in points if e['points'] > 0 and e['verification_status'] == 'PROVISIONAL'),
                recorded_unverified_points=sum(e['points'] for e in points if e['points'] > 0 and e['verification_status'] == 'RECORDED_CREDIT_UNVERIFIED'),
                recorded_adjustment_credits=sum(e['points'] for e in points if e['points'] > 0 and e['verification_status'] == 'RECORDED_ADJUSTMENT'),
                deductions=sum(-e['points'] for e in points if e['points'] < 0), net_points=sum(e['points'] for e in points)))
        return dict(filter=period, start_date=first.isoformat(), end_date=last.isoformat(), has_tracked_period=last >= first,
            timezone=TIMEZONE, planned_gym_days=planned_days, verified_sessions=verified, pending_sessions=sum(s['pending'] for s in sessions),
            missed_sessions=sum(s['missed'] for s in sessions), verified_points=sum(e['points'] for e in ledger if e['points'] > 0 and e['verification_status'] == 'VERIFIED'),
            provisional_points=sum(e['points'] for e in ledger if e['points'] > 0 and e['verification_status'] == 'PROVISIONAL'),
            recorded_unverified_points=sum(e['points'] for e in ledger if e['points'] > 0 and e['verification_status'] == 'RECORDED_CREDIT_UNVERIFIED'),
            recorded_adjustment_credits=sum(e['points'] for e in ledger if e['points'] > 0 and e['verification_status'] == 'RECORDED_ADJUSTMENT'),
            deductions=sum(-e['points'] for e in ledger if e['points'] < 0), net_points=sum(e['points'] for e in ledger), streak=None,
            completion_rate=round(len(verified_planned_dates) / planned_days * 100, 1) if planned_days else None,
            history=ledger, daily=daily, unattributed_count=facts['unattributed_count'],
            empty_state=not sessions and not ledger, attribution_note='Only account-scoped retained Gym records and committed ledger entries are included. Missing attendance is not inferred.',
            routine_status=routine['status'], next_gym_date=routine['next_gym_date'])
