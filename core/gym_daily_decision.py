"""Dated Gym planning in the existing account database; never awards points or lives."""
from datetime import date as calendar_date, timedelta
from contextlib import contextmanager
import hashlib
import json
from zoneinfo import ZoneInfo

from core.time_window_engine import now_ist


@contextmanager
def planning_connection(db, connection=None):
    if connection is not None:
        yield connection
    else:
        with db._connect() as conn:
            yield conn


class GymDecisionError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


class DailyGymDecision:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def ensure_schema(conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS daily_gym_decisions(
            account_id TEXT NOT NULL, date TEXT NOT NULL, gym_planned INTEGER NOT NULL,
            revision INTEGER NOT NULL, decision_at TEXT NOT NULL, timezone TEXT NOT NULL,
            actor TEXT NOT NULL, device_id TEXT NOT NULL, PRIMARY KEY(account_id,date))''')
        columns={row[1] for row in conn.execute('PRAGMA table_info(daily_gym_decisions)')}
        if 'effective_at_ms' not in columns:
            conn.execute('ALTER TABLE daily_gym_decisions ADD COLUMN effective_at_ms INTEGER NOT NULL DEFAULT 0')
        conn.execute('''CREATE TABLE IF NOT EXISTS gym_decision_requests(
            account_id TEXT NOT NULL, request_id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
            result_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(account_id,request_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS gym_mode_violations(
            account_id TEXT NOT NULL, gym_activity_id TEXT NOT NULL, session_id TEXT NOT NULL,
            reason TEXT NOT NULL, confirmed_at TEXT NOT NULL, week_key TEXT NOT NULL,
            PRIMARY KEY(account_id,gym_activity_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS gym_activation_requests(
            account_id TEXT NOT NULL,request_id TEXT NOT NULL,gym_activity_id TEXT NOT NULL,
            decision_revision INTEGER NOT NULL,session_id TEXT NOT NULL,PRIMARY KEY(account_id,request_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS bluetooth_planning_receipts(
            account_id TEXT NOT NULL,request_id TEXT NOT NULL,request_sha256 TEXT NOT NULL,request_json TEXT NOT NULL,
            result_json TEXT NOT NULL,received_at TEXT NOT NULL,PRIMARY KEY(account_id,request_id))''')

    @staticmethod
    def read(conn, account, date, reference_now=None):
        row = conn.execute('SELECT * FROM daily_gym_decisions WHERE account_id=? AND date=?', (account,date)).fetchone()
        if not row:
            schedule=conn.execute('SELECT timezone,status,gym_enabled FROM routine_schedules WHERE account_id=? AND schedule_date=? ORDER BY schedule_version DESC LIMIT 1',(account,date)).fetchone()
            routine=None
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gym_routines'").fetchone():
                routine=conn.execute('SELECT start_date,revision FROM gym_routines WHERE account_id=?',(account,)).fetchone()
            # A new long-term plan cannot revoke already booked obligations or
            # reset an active/pending Gym Mode session. Explicit daily rows above
            # always win, including an explicit No.
            retained=conn.execute("SELECT 1 FROM scheduled_activities WHERE account_id=? AND date=? AND type='GYM' AND is_enabled=1 AND status NOT IN ('CANCELLED','DISABLED') LIMIT 1",(account,date)).fetchone() if routine else None
            preserved = bool(routine and date>=routine['start_date']) and (bool(retained) or bool(schedule and schedule['status'] in ('SCHEDULED','LOCKED')))
            if preserved:
                planned=bool(retained) or bool(schedule['gym_enabled'])
                return dict(account_id=account,date=date,timezone=(schedule['timezone'] if schedule else None) or 'Asia/Kolkata',
                    gym_planned=planned,revision=0,status='GYM_PLANNED' if planned else 'GYM_NOT_PLANNED',
                    sync_status='CONFIRMED',source='EXISTING_BOOKED_SCHEDULE')
            # This path runs on the frequent status poll. Read one indexed cycle
            # only; no schema creation, full history, statistics or score writes.
            if routine and date >= routine['start_date']:
                segment=conn.execute('''SELECT anchor_date FROM gym_routine_segments WHERE account_id=? AND start_date<=?
                    AND (end_date IS NULL OR end_date>?) ORDER BY created_revision DESC LIMIT 1''',(account,date,date)).fetchone()
                first=conn.execute('SELECT MIN(start_date) FROM gym_routine_segments WHERE account_id=?',(account,)).fetchone()[0]
                if first and date>=first:
                    planned=bool(segment and (calendar_date.fromisoformat(date)-calendar_date.fromisoformat(segment['anchor_date'])).days%2==0)
                    return dict(account_id=account,date=date,timezone='Asia/Kolkata',gym_planned=planned,revision=0,
                        routine_revision=routine['revision'],status='GYM_PLANNED' if planned else 'GYM_NOT_PLANNED',
                        sync_status='CONFIRMED',source='ALTERNATE_DAY_ROUTINE')
            return dict(account_id=account,date=date,timezone=(schedule['timezone'] if schedule else None) or 'Asia/Kolkata',gym_planned=None,revision=0,status='GYM_DECISION_REQUIRED',sync_status='DECISION_REQUIRED',source='DECISION_REQUIRED')
        value=dict(row)
        value['gym_planned']=bool(value['gym_planned'])
        value.update(status='GYM_PLANNED' if value['gym_planned'] else 'GYM_NOT_PLANNED',sync_status='CONFIRMED',source='EXPLICIT_DAILY_CHOICE')
        if not value['gym_planned'] and value.get('effective_at_ms',0)>int((reference_now or now_ist()).timestamp()*1000):
            value['sync_status']='REVOCATION_PENDING'
        return value

    def get(self, account, date):
        calendar_date.fromisoformat(date)
        with self.db._connect() as conn:
            self.ensure_schema(conn)
            return self.read(conn,account,date)

    def save(self, account, date, planned, expected_revision, request_id, *, actor, device_id,
             timezone='Asia/Kolkata', admin_authorized=False, current=None, connection=None):
        if type(planned) is not bool or type(expected_revision) is not int or expected_revision < 0:
            raise GymDecisionError('INVALID_DECISION','Choose Yes or No and provide the saved revision')
        try:
            target=calendar_date.fromisoformat(date)
            zone=ZoneInfo(timezone)
        except (ValueError,KeyError,TypeError):
            raise GymDecisionError('INVALID_DATE','Select a valid local date and timezone')
        if not isinstance(request_id,str) or not 8 <= len(request_id) <= 128:
            raise GymDecisionError('REQUEST_ID_REQUIRED','A stable request identifier is required')
        current=(current or now_ist()).astimezone(zone)
        if target < current.date():
            raise GymDecisionError('HISTORICAL_DATE','Past Gym decisions and their audit history cannot be rewritten')
        stamp=current.isoformat()
        request_hash=hashlib.sha256(json.dumps([account,date,planned,expected_revision,timezone],separators=(',',':')).encode()).hexdigest()
        with planning_connection(self.db,connection) as conn:
            self.ensure_schema(conn)
            if connection is None:conn.execute('BEGIN IMMEDIATE')
            prior=conn.execute('SELECT * FROM gym_decision_requests WHERE account_id=? AND request_id=?',(account,request_id)).fetchone()
            if prior:
                if prior['request_sha256'] != request_hash:
                    raise GymDecisionError('REQUEST_CONFLICT','This request ID was already used for different choices',409)
                return json.loads(prior['result_json'])
            old=self.read(conn,account,date)
            if old['revision'] != expected_revision:
                raise GymDecisionError('REVISION_CONFLICT','A newer Gym decision exists. Refresh before saving.',409)
            locked=conn.execute("SELECT 1 FROM routine_schedules WHERE account_id=? AND schedule_date=? AND status IN ('SCHEDULED','LOCKED')",(account,date)).fetchone()
            if old['revision'] and planned != old['gym_planned'] and locked and not admin_authorized:
                raise GymDecisionError('ADMIN_REQUIRED','This schedule is confirmed. Use the existing administrator-authorized schedule change.',403)
            if old['revision'] and planned != old['gym_planned'] and not admin_authorized:
                from core.time_window_engine import iso_to_dt
                minimum=6
                inclusive=True
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='strictbot_policy'").fetchone():
                    policy=conn.execute('SELECT minimum_notice_hours,accept_at_deadline FROM strictbot_policy WHERE account_id=?',(account,)).fetchone()
                    if policy:minimum=max(minimum,policy[0]);inclusive=bool(policy[1])
                booking=conn.execute("SELECT scheduled_time FROM scheduled_activities WHERE account_id=? AND date=? AND type='GYM' ORDER BY scheduled_time LIMIT 1",(account,date)).fetchone()
                if booking:
                    deadline=iso_to_dt(booking[0])-timedelta(hours=minimum)
                    if current>deadline or (not inclusive and current==deadline):
                        raise GymDecisionError('ADMIN_REQUIRED','This Gym change is inside the scheduling notice period. Use the existing approved schedule change.',403)
            # A calendar date always uses its existing schedule timezone; clients
            # cannot shift a choice across midnight by supplying another zone.
            if old.get('timezone') and old['timezone'] != timezone:
                raise GymDecisionError('TIMEZONE_CONFLICT','Refresh the saved schedule timezone before changing this date',409)
            conn.execute('''INSERT INTO daily_gym_decisions(account_id,date,gym_planned,revision,decision_at,timezone,actor,device_id)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(account_id,date) DO UPDATE SET gym_planned=excluded.gym_planned,
                revision=excluded.revision,decision_at=excluded.decision_at,actor=excluded.actor,device_id=excluded.device_id''',
                (account,date,int(planned),expected_revision+1,stamp,timezone,actor,device_id))
            if not planned:
                active=conn.execute('''SELECT 1 FROM gym_mode_sessions WHERE account_id=? AND status='ACTIVE' AND gym_activity_id IN
                    (SELECT activity_id FROM scheduled_activities WHERE account_id=? AND date=? AND type='GYM')''',(account,account,date)).fetchone()
                from core.product_policy import GYM
                effective=int(current.timestamp()*1000)+(GYM['status_lease_seconds']*1000 if active else 0)
                conn.execute('UPDATE daily_gym_decisions SET effective_at_ms=? WHERE account_id=? AND date=?',(effective,account,date))
                # Revoke music immediately while retaining consumed activations,
                # pending proof obligations and all earlier ledger transactions.
                conn.execute('''UPDATE gym_mode_sessions SET status='EXPIRED',toggled_off_early=1,toggled_off_at_ms=?,updated_at=?
                    WHERE account_id=? AND status='ACTIVE' AND gym_activity_id IN
                    (SELECT activity_id FROM scheduled_activities WHERE account_id=? AND date=? AND type='GYM')''',
                    (int(current.timestamp()*1000),stamp,account,account,date))
                conn.execute('''UPDATE scheduled_activities SET is_enabled=0,status='CANCELLED'
                    WHERE account_id=? AND date=? AND type='GYM' AND status IN ('SCHEDULED','UPCOMING','NOTIFIED','OPEN')
                    AND NOT EXISTS(SELECT 1 FROM gym_mode_sessions m WHERE m.account_id=? AND m.gym_activity_id=scheduled_activities.activity_id)''',
                    (account,date,account))
            result=dict(success=True,decision=self.read(conn,account,date,current))
            conn.execute('INSERT INTO gym_decision_requests(account_id,request_id,request_sha256,result_json,created_at) VALUES(?,?,?,?,?)',
                (account,request_id,request_hash,json.dumps(result),stamp))
        return result
