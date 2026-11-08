"""
StrictRecords — GYM Mode Engine (Windows & Companion Service)
============================================================
Enforces GYM Mode temporary music access across Windows EXE and Chrome Extension:
  1. Activation Window: 17:00 <= currentTime < 20:30 (strictly before 8:30 PM).
  2. Duration: until today's 20:30 cutoff (or an earlier manual switch-off).
  3. Scope: Unlocks ONLY Spotify on Windows and Chrome Extension.
     YouTube, Instagram, etc. remain strictly blocked.
  4. Three-Life System: ♥ ♥ ♥ — 3/3 lives per week.
     Weekly reset every Monday at 12:00 AM (00:00) IST.
  5. Single Deduction Guarantee: Each failed gym session can deduct AT MOST one life.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any, Dict, Optional, Tuple

from core.time_window_engine import IST, dt_to_iso, iso_to_dt, now_ist
from core.product_policy import GYM, POLICY

logger = logging.getLogger("GymModeEngine")

ACTIVATION_START_HOUR = GYM['activation_start_hour']
CUTOFF_HOUR = GYM['cutoff_hour']
CUTOFF_MINUTE = GYM['cutoff_minute']
MAX_MUSIC_DURATION_SECONDS = GYM['music_duration_seconds']
MAX_LIVES = GYM['max_lives']


def is_within_activation_window(dt: Optional[datetime] = None) -> bool:
    """
    Checks if given datetime is strictly within [17:00, 20:30).
    """
    current = dt or now_ist()
    h = current.hour
    m = current.minute
    if h < ACTIVATION_START_HOUR:
        return False
    if h > CUTOFF_HOUR:
        return False
    if h == CUTOFF_HOUR and m >= CUTOFF_MINUTE:
        return False
    return True


def calculate_access_expiry(activation_dt: Optional[datetime] = None) -> datetime:
    """
    accessExpiry = today's 8:30 PM cutoff; activation remains restricted to its daily window.
    """
    act = activation_dt or now_ist()
    cutoff = act.replace(hour=CUTOFF_HOUR, minute=CUTOFF_MINUTE, second=0, microsecond=0)
    return cutoff


def format_lives_badge(lives: int) -> str:
    clamped = max(0, min(MAX_LIVES, int(lives)))
    hearts = {
        3: "♥ ♥ ♥",
        2: "♥ ♥ ♡",
        1: "♥ ♡ ♡",
        0: "♡ ♡ ♡",
    }.get(clamped, "♡ ♡ ♡")
    return f"{hearts} — {clamped}/{MAX_LIVES} lives"


def get_iso_week_key(dt: Optional[datetime] = None) -> str:
    current = dt or now_ist()
    year, week, _ = current.isocalendar()
    return f"{year:04d}-W{week:02d}"


def calculate_next_weekly_reset(dt: Optional[datetime] = None) -> datetime:
    current = dt or now_ist()
    # Find next Monday 00:00:00
    days_ahead = (0 - current.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    next_monday = (current + timedelta(days=days_ahead)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return next_monday


class GymModeEngine:
    """One persisted life balance and activation path for Android, Windows and Chrome."""
    def __init__(self, db=None):
        self.db = db

    @staticmethod
    def _local_time(conn,account,current):
        from zoneinfo import ZoneInfo
        base=current.astimezone(IST) if current.tzinfo else current.replace(tzinfo=IST)
        low,high=(base-timedelta(days=1)).date().isoformat(),(base+timedelta(days=1)).date().isoformat()
        records={}
        for row in conn.execute('SELECT schedule_date AS date,timezone FROM routine_schedules WHERE account_id=? AND schedule_date BETWEEN ? AND ? ORDER BY schedule_version DESC',(account,low,high)):
            records.setdefault(row['date'],row['timezone'])
        for row in conn.execute('SELECT date,timezone FROM daily_gym_decisions WHERE account_id=? AND date BETWEEN ? AND ?',(account,low,high)):
            records.setdefault(row['date'],row['timezone'])
        for date,zone in sorted(records.items(),reverse=True):
            local=base.astimezone(ZoneInfo(zone or 'Asia/Kolkata'))
            if local.date().isoformat()==date:return local
        return base.astimezone(ZoneInfo('Asia/Kolkata'))

    def _reset_week(self, conn, account, current):
        week=get_iso_week_key(current)
        row=conn.execute('SELECT * FROM gym_mode_state WHERE account_id=?',(account,)).fetchone()
        ms=int(current.timestamp()*1000)
        if not row:
            conn.execute('INSERT INTO gym_mode_state(account_id,lives_balance,last_reset_week,last_reset_time_ms,updated_at) VALUES(?,?,?,?,?)',
                         (account,MAX_LIVES,week,ms,current.isoformat()))
        elif not row['last_reset_week']:
            # A legacy balance is preserved when first attaching a week marker.
            conn.execute('UPDATE gym_mode_state SET last_reset_week=?,last_reset_time_ms=?,updated_at=? WHERE account_id=?',
                         (week,ms,current.isoformat(),account))
        elif week > row['last_reset_week'] and ms > row['last_reset_time_ms']:
            conn.execute('UPDATE gym_mode_state SET lives_balance=?,last_reset_week=?,last_reset_time_ms=?,updated_at=? WHERE account_id=?',
                         (MAX_LIVES,week,ms,current.isoformat(),account))

    def _reconcile_proof(self, conn, account, current):
        ms=int(current.timestamp()*1000)
        from core.scoring_engine import ScoringEngine
        # Applicable even if the workout was completed without activating music.
        approved=conn.execute("""SELECT a.*,pr.evidence_id AS gym_evidence_id FROM scheduled_activities a
            JOIN proof_reviews pr ON pr.activity_id=a.activity_id AND pr.user_id=a.account_id AND pr.date=a.date
            JOIN evidence_files ef ON ef.evidence_id=pr.evidence_id AND ef.is_deleted=0
            WHERE a.account_id=? AND a.type='GYM' AND a.status IN ('VERIFIED','COMPLETED','APPROVED')
            AND UPPER(pr.task_code)='GYM' AND pr.status IN ('APPROVED','VERIFIED') AND pr.reviewed_by!=''
            AND UPPER(pr.reviewed_by) NOT LIKE '%TAUFA%'""",(account,)).fetchall()
        for activity in approved:
            review=conn.execute('SELECT * FROM proof_reviews WHERE evidence_id=?',(activity['gym_evidence_id'],)).fetchone()
            ScoringEngine.reconcile_verified_gym_attendance(conn,activity,review,current)
        sessions=conn.execute("SELECT * FROM gym_mode_sessions WHERE account_id=? AND status IN ('ACTIVE','EXPIRED')",(account,)).fetchall()
        for session in sessions:
            activity=conn.execute("SELECT * FROM scheduled_activities WHERE account_id=? AND activity_id=? AND type='GYM'",(account,session['gym_activity_id'])).fetchone()
            if not activity:continue
            reviews=conn.execute("""SELECT pr.*,EXISTS(SELECT 1 FROM evidence_files ef
                WHERE ef.evidence_id=pr.evidence_id AND ef.date=pr.date AND ef.is_deleted=0
                AND UPPER(ef.record_type)='GYM') AS retained_gym_evidence
                FROM proof_reviews pr WHERE activity_id=? AND date=? AND user_id=? AND UPPER(task_code)='GYM'""",
                                 (activity['activity_id'],activity['date'],account)).fetchall()
            # Review outcomes remain in the existing Gym validation pipeline. A
            # Taufa hourly score or an unconfirmed sync claim is never Gym proof.
            accepted=any(r['status'] in ('APPROVED','VERIFIED') and r['reviewed_by'] and r['retained_gym_evidence'] and
                         'TAUFA' not in str(r['reviewed_by']).upper() for r in reviews)
            if accepted and activity['status'] in ('VERIFIED','COMPLETED','APPROVED'):
                conn.execute("UPDATE gym_mode_sessions SET status='FULFILLED',updated_at=? WHERE session_id=?",(current.isoformat(),session['session_id']))
                continue
            pending=any(r['status'] in ('PENDING','PENDING_REVIEW','PROCESSING','VALIDATING','AI_PROVISIONAL','NEEDS_REVIEW') for r in reviews)
            submitted=activity['status'] in ('SUBMITTED','VALIDATING','PENDING_REVIEW','AI_PROVISIONAL','PENDING_SYNC') or bool(activity['submitted_at'])
            fraud=any(r['status']=='REJECTED' and r['reviewed_by'] and
                      str(r['reason_category'] or '').upper() in ('FAKE_EVIDENCE','MANIPULATED_EVIDENCE','CONFIRMED_FAKE','FRAUD') for r in reviews)
            # An expired timer or an unavailable phone is not confirmation that
            # proof was deliberately missed. Use the existing finalized review.
            missed=ms >= session['proof_deadline_ms'] and any(r['status']=='REJECTED' and r['reviewed_by'] and
                str(r['reason_category'] or '').upper() in ('CONFIRMED_MISSING_PROOF','MISSED_PROOF_CONFIRMED') for r in reviews)
            if pending or not (fraud or missed):continue
            activation=datetime.fromtimestamp(session['activation_time_ms']/1000,current.tzinfo)
            week=get_iso_week_key(activation)
            cursor=conn.execute('INSERT OR IGNORE INTO gym_mode_violations(account_id,gym_activity_id,session_id,reason,confirmed_at,week_key) VALUES(?,?,?,?,?,?)',
                (account,activity['activity_id'],session['session_id'],'CONFIRMED_FAKE' if fraud else 'CONFIRMED_MISSING_PROOF',current.isoformat(),week))
            if cursor.rowcount and week==get_iso_week_key(current):
                conn.execute('UPDATE gym_mode_state SET lives_balance=MAX(0,lives_balance-1),updated_at=? WHERE account_id=?',(current.isoformat(),account))
            conn.execute("UPDATE gym_mode_sessions SET status='FAILED',updated_at=? WHERE session_id=?",(current.isoformat(),session['session_id']))

    def _state(self, conn, account, current):
        from core.gym_daily_decision import DailyGymDecision
        self._reset_week(conn,account,current)
        self._reconcile_proof(conn,account,current)
        date=current.strftime('%Y-%m-%d')
        decision=DailyGymDecision.read(conn,account,date,current)
        ms=int(current.timestamp()*1000)
        active=conn.execute("SELECT * FROM gym_mode_sessions WHERE account_id=? AND status='ACTIVE' ORDER BY activation_time_ms DESC LIMIT 1",(account,)).fetchone()
        if active:
            activation=datetime.fromtimestamp(active['activation_time_ms']/1000,current.tzinfo)
            cutoff=int(calculate_access_expiry(activation).timestamp()*1000)
            valid=decision['gym_planned'] is True and activation.date()==current.date() and ms>=active['activation_time_ms'] and ms<min(active['access_expiry_ms'],cutoff) and not active['toggled_off_early']
            if not valid:
                conn.execute("UPDATE gym_mode_sessions SET status='EXPIRED',updated_at=? WHERE session_id=?",(current.isoformat(),active['session_id']))
                active=None
        latest=conn.execute('''SELECT m.* FROM gym_mode_sessions m JOIN scheduled_activities a ON a.activity_id=m.gym_activity_id
            WHERE m.account_id=? AND a.account_id=? AND a.date=? ORDER BY m.activation_time_ms DESC LIMIT 1''',(account,account,date)).fetchone()
        lives=conn.execute('SELECT lives_balance FROM gym_mode_state WHERE account_id=?',(account,)).fetchone()[0]
        state=decision['status']
        if active:state='GYM_MODE_ACTIVE'
        elif latest and decision['gym_planned'] is True:state='GYM_MODE_EXPIRED'
        proof='GYM_PROOF_ACCEPTED' if latest and latest['status']=='FULFILLED' else 'GYM_PROOF_REJECTED' if latest and latest['status']=='FAILED' else 'GYM_PROOF_PENDING' if latest else None
        available=conn.execute('''SELECT activity_id,title,scheduled_time,window_end FROM scheduled_activities a
            WHERE account_id=? AND date=? AND type='GYM' AND is_enabled=1 AND status IN ('SCHEDULED','UPCOMING','NOTIFIED','OPEN','BOOKED','RESERVED')
            AND NOT EXISTS(SELECT 1 FROM gym_mode_sessions m WHERE m.account_id=a.account_id AND m.gym_activity_id=a.activity_id)''',(account,date)).fetchall()
        return dict(success=True,account_id=account,date=date,timezone=getattr(current.tzinfo,'key','Asia/Kolkata'),decision=decision,
            lifecycle=state,proof_status=proof,is_active=bool(active),active_session=dict(active) if active else None,
            last_session=dict(latest) if latest else None,lives_balance=max(0,min(MAX_LIVES,lives)),
            remaining_seconds=max(0,int((active['access_expiry_ms']-ms)/1000)) if active else 0,
            next_reset=calculate_next_weekly_reset(current).isoformat(),server_time=current.isoformat(),
            activation_window_open=is_within_activation_window(current),eligible_sessions=[dict(row) for row in available if iso_to_dt(row['window_end'])>current],
            status_valid_until_ms=ms+GYM['status_lease_seconds']*1000,policy_version=POLICY['version'],policy=GYM,
            message='Gym marked No · restoring normal restrictions across active clients.' if decision.get('sync_status')=='REVOCATION_PENDING' else
                    'GYM Mode unavailable — Gym was marked as not planned for today.' if decision['gym_planned'] is False else
                    'Choose whether Gym is planned for today before activating GYM Mode.' if decision['gym_planned'] is None else 'Gym planned; session, lives and activation window still apply.')

    def state(self, account_id='user_lok', dt=None):
        from core.gym_daily_decision import DailyGymDecision
        current=(dt or now_ist()).astimezone(IST)
        with self.db._connect() as conn:
            DailyGymDecision.ensure_schema(conn)
            conn.execute('BEGIN IMMEDIATE')
            current=self._local_time(conn,account_id,current)
            return self._state(conn,account_id,current)

    def _can_activate(self, conn, activity_id, account, current, expected_revision=None):
        state=self._state(conn,account,current)
        decision=state['decision']
        if decision['gym_planned'] is not True:return False,state['message']
        if expected_revision is not None and expected_revision != decision['revision']:return False,'A newer Gym decision exists. Refresh before activation.'
        if state['lives_balance']<=0:return False,'No lives remaining. GYM Mode is disabled until the weekly reset.'
        if state['is_active']:return False,'A GYM Mode session is already active.'
        if not is_within_activation_window(current):return False,'GYM Mode activation is permitted from 5:00 PM until strictly before 8:30 PM.'
        activity=conn.execute("SELECT * FROM scheduled_activities WHERE account_id=? AND date=? AND activity_id=? AND type='GYM' AND is_enabled=1",
            (account,current.strftime('%Y-%m-%d'),activity_id)).fetchone()
        if not activity or activity['status'] not in ('SCHEDULED','UPCOMING','NOTIFIED','OPEN','BOOKED','RESERVED'):
            return False,'Select a valid planned Gym session for today through the scheduler.'
        if iso_to_dt(activity['window_end'])<=current:return False,'This Gym session proof deadline has closed.'
        if conn.execute('SELECT 1 FROM gym_mode_sessions WHERE account_id=? AND gym_activity_id=?',(account,activity_id)).fetchone():
            return False,'This Gym session has already been consumed by a GYM Mode activation.'
        return True,'OK'

    def can_activate(self,gym_activity_id,account_id='user_lok',dt=None):
        current=(dt or now_ist()).astimezone(IST)
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current=self._local_time(conn,account_id,current)
            return self._can_activate(conn,gym_activity_id,account_id,current)

    def activate_session(self,gym_activity_id,proof_deadline_iso=None,account_id='user_lok',dt=None,*,expected_revision=None,request_id=None):
        import uuid
        current=(dt or now_ist()).astimezone(IST)
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current=self._local_time(conn,account_id,current)
            if request_id:
                prior=conn.execute('SELECT * FROM gym_activation_requests WHERE account_id=? AND request_id=?',(account_id,request_id)).fetchone()
                if prior:
                    if prior['gym_activity_id']!=gym_activity_id or prior['decision_revision']!=expected_revision:
                        return dict(success=False,error='This activation request ID was used for different inputs')
                    state=self._state(conn,account_id,current)
                    return dict(success=True,replayed=True,session_id=prior['session_id'],state=state,
                                remaining_seconds=state['remaining_seconds'],lives_balance=state['lives_balance'])
            can,message=self._can_activate(conn,gym_activity_id,account_id,current,expected_revision)
            if not can:return dict(success=False,error=message,state=self._state(conn,account_id,current))
            activity=conn.execute('SELECT window_end FROM scheduled_activities WHERE account_id=? AND activity_id=?',(account_id,gym_activity_id)).fetchone()
            expiry=calculate_access_expiry(current)
            deadline=iso_to_dt(activity['window_end'])
            sid='gym_mode_'+uuid.uuid4().hex
            conn.execute('''INSERT INTO gym_mode_sessions(session_id,account_id,gym_activity_id,activation_time_ms,access_expiry_ms,
                proof_deadline_ms,status,toggled_off_early,created_at,updated_at) VALUES(?,?,?,?,?,?,'ACTIVE',0,?,?)''',
                (sid,account_id,gym_activity_id,int(current.timestamp()*1000),int(expiry.timestamp()*1000),int(deadline.timestamp()*1000),current.isoformat(),current.isoformat()))
            if request_id:
                revision=expected_revision if expected_revision is not None else self._state(conn,account_id,current)['decision']['revision']
                conn.execute('INSERT INTO gym_activation_requests(account_id,request_id,gym_activity_id,decision_revision,session_id) VALUES(?,?,?,?,?)',
                             (account_id,request_id,gym_activity_id,revision,sid))
            state=self._state(conn,account_id,current)
            return dict(success=True,session_id=sid,access_expiry=expiry.isoformat(),remaining_seconds=state['remaining_seconds'],
                        lives_balance=state['lives_balance'],state=state)

    def deactivate_early(self,account_id='user_lok',dt=None,session_id=None):
        current=(dt or now_ist()).astimezone(IST)
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current=self._local_time(conn,account_id,current)
            conn.execute("UPDATE gym_mode_sessions SET status='EXPIRED',toggled_off_early=1,toggled_off_at_ms=?,updated_at=? WHERE account_id=? AND status='ACTIVE' AND (? IS NULL OR session_id=?)",
                (int(current.timestamp()*1000),current.isoformat(),account_id,session_id,session_id))
            return dict(success=True,message='Music access revoked. Existing proof obligation remains.',state=self._state(conn,account_id,current))

    def reconcile_offline_activation(self, gym_activity_id, activation_time_ms, request_id, *, session_id=None, account_id='user_lok', device_id='android_offline', timezone='Asia/Kolkata', dt=None, connection=None):
        """
        Idempotently reconciles an offline phone GYM Mode activation without fabricating timestamps
        or double-deducting hearts/lives. Preserves existing ledger history on conflict.
        """
        import uuid
        from zoneinfo import ZoneInfo
        from core.gym_daily_decision import DailyGymDecision, planning_connection
        current = (dt or now_ist()).astimezone(IST)
        if not isinstance(gym_activity_id, str) or not 1 <= len(gym_activity_id) <= 128:
            return dict(success=False, code='INVALID_ACTIVATION', error='Invalid gym_activity_id')
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 128:
            return dict(success=False, code='INVALID_ACTIVATION', error='Invalid request_id')
        try:
            act_ms = int(activation_time_ms)
        except (TypeError, ValueError):
            return dict(success=False, code='INVALID_ACTIVATION', error='Invalid activation_time_ms')

        with planning_connection(self.db, connection) as conn:
            DailyGymDecision.ensure_schema(conn)
            if connection is None:
                conn.execute('BEGIN IMMEDIATE')
            current = self._local_time(conn, account_id, current)
            zone = ZoneInfo(timezone or 'Asia/Kolkata')
            act_dt = datetime.fromtimestamp(act_ms / 1000.0, tz=zone)
            act_date = act_dt.strftime('%Y-%m-%d')

            # 1. Idempotency check by request_id
            prior = conn.execute('SELECT * FROM gym_activation_requests WHERE account_id=? AND request_id=?', (account_id, request_id)).fetchone()
            if prior:
                if prior['gym_activity_id'] != gym_activity_id:
                    return dict(success=False, conflict=True, code='CONFLICT', error='Activation request ID was already used for a different Gym activity')
                state = self._state(conn, account_id, current)
                return dict(success=True, replayed=True, session_id=prior['session_id'], state=state, lives_balance=state['lives_balance'])

            # 2. Validate historical activation window (after 5:00 PM until 8:30 PM in local timezone)
            if not is_within_activation_window(act_dt):
                return dict(success=False, code='WINDOW_CLOSED', error='Offline activation timestamp falls outside the 5:00 PM - 8:30 PM activation window')

            self._reset_week(conn, account_id, current)
            lives_row = conn.execute('SELECT lives_balance FROM gym_mode_state WHERE account_id=?', (account_id,)).fetchone()
            lives = lives_row[0] if lives_row else MAX_LIVES
            if lives <= 0:
                return dict(success=False, code='NO_LIVES', error='No lives remaining in authoritative ledger at reconciliation time')

            # 3. Conflict check against authoritative daily Gym decision & existing sessions
            decision = DailyGymDecision.read(conn, account_id, act_date, current)
            if decision['gym_planned'] is False:
                return dict(success=False, conflict=True, code='CONFLICT', error='Gym was marked as Not Planned on PC for this date; preserving existing ledger history')

            existing_for_act = conn.execute('SELECT session_id FROM gym_mode_sessions WHERE account_id=? AND gym_activity_id=?', (account_id, gym_activity_id)).fetchone()
            if existing_for_act:
                if session_id and existing_for_act['session_id'] == session_id:
                    state = self._state(conn, account_id, current)
                    return dict(success=True, replayed=True, session_id=session_id, state=state, lives_balance=state['lives_balance'])
                return dict(success=False, conflict=True, code='CONFLICT', error='This Gym session already has a recorded activation in the ledger')

            # 4. Ensure daily decision is marked planned if still unconfirmed, and ensure scheduled_activities row exists
            if decision['gym_planned'] is None:
                DailyGymDecision(self.db).save(
                    account_id, act_date, True, decision['revision'], f'auto_dec_{request_id[:48]}',
                    timezone=timezone or 'Asia/Kolkata', actor=account_id, device_id=device_id or 'android_offline',
                    current=act_dt, connection=conn
                )
                decision = DailyGymDecision.read(conn, account_id, act_date, current)

            activity = conn.execute("SELECT * FROM scheduled_activities WHERE account_id=? AND activity_id=? AND type='GYM'", (account_id, gym_activity_id)).fetchone()
            expiry_dt = calculate_access_expiry(act_dt)
            expiry_ms = int(expiry_dt.timestamp() * 1000)
            if not activity:
                deadline_dt = expiry_dt + timedelta(minutes=30)
                conn.execute(
                    '''INSERT OR IGNORE INTO scheduled_activities(activity_id,account_id,type,title,date,scheduled_time,window_start,window_end,notification_time,status,is_enabled)
                       VALUES(?,?,'GYM','Gym Workout Session',?,?,?,?,?,'SCHEDULED',1)''',
                    (gym_activity_id, account_id, act_date, act_dt.isoformat(), expiry_dt.isoformat(), deadline_dt.isoformat(), expiry_dt.isoformat())
                )
                deadline_ms = int(deadline_dt.timestamp() * 1000)
            else:
                deadline_ms = int(iso_to_dt(activity['window_end']).timestamp() * 1000)

            now_ms = int(current.timestamp() * 1000)
            sid = session_id if (isinstance(session_id, str) and 4 <= len(session_id) <= 128) else ('gym_off_' + uuid.uuid4().hex[:16])
            sess_status = 'ACTIVE' if (act_date == current.strftime('%Y-%m-%d') and act_ms <= now_ms < expiry_ms) else 'EXPIRED'

            conn.execute(
                '''INSERT INTO gym_mode_sessions(session_id,account_id,gym_activity_id,activation_time_ms,access_expiry_ms,
                   proof_deadline_ms,status,toggled_off_early,created_at,updated_at) VALUES(?,?,?,?,?,?,?,0,?,?)''',
                (sid, account_id, gym_activity_id, act_ms, expiry_ms, deadline_ms, sess_status, act_dt.isoformat(), current.isoformat())
            )
            conn.execute(
                'INSERT INTO gym_activation_requests(account_id,request_id,gym_activity_id,decision_revision,session_id) VALUES(?,?,?,?,?)',
                (account_id, request_id, gym_activity_id, decision['revision'], sid)
            )
            state = self._state(conn, account_id, current)
            return dict(success=True, replayed=False, session_id=sid, access_expiry=expiry_dt.isoformat(), lives_balance=state['lives_balance'], state=state)
