"""Durable Strict Session timing and Android receipts, independent of scoring/OS controls.

Only reads the existing schedule and accepted proof pipeline. No synthetic windows,
score credits, desktop proof uploads, or timer-triggered reward grants.
"""
import json
import logging
import threading
import time
from datetime import timedelta

from core.database import is_valid_retained_proof_file
from core.time_window_engine import now_ist, iso_to_dt

log = logging.getLogger("StrictStudyWorkflow")
TERMINAL = {"SESSION_FINALIZED", "REWARD_ACTIVE", "FAILED", "ABANDONED"}
FOCUS = {"ACTIVE_FOCUS", "MINIMUM_DURATION_COMPLETED", "ADAPTIVE_COMPLETION"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS strict_study_sessions(
 session_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, state TEXT NOT NULL,
 data_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS strict_study_transitions(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,
 state TEXT NOT NULL,at TEXT NOT NULL,details_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS strict_study_receipts(
 account_id TEXT NOT NULL,submission_id TEXT NOT NULL,session_id TEXT NOT NULL UNIQUE,
 event_id TEXT NOT NULL,verified_at TEXT NOT NULL,
 PRIMARY KEY(account_id,submission_id));
CREATE UNIQUE INDEX IF NOT EXISTS strict_study_one_pending_account
 ON strict_study_sessions(account_id)
 WHERE state NOT IN ('SESSION_FINALIZED','REWARD_ACTIVE','FAILED','ABANDONED');
"""


def reward_hold(db, account_id, *, activity_id=None, submission_id=None):
    """Shared local activation gate; unavailable schema means no new workflow yet."""
    with db._connect() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='strict_study_sessions'").fetchone()
        if not exists:
            return None
        rows = conn.execute("SELECT data_json FROM strict_study_sessions WHERE account_id=? ORDER BY updated_at DESC", (account_id,))
        for row in rows:
            s = json.loads(row[0])
            same = ((activity_id and s.get('window', {}).get('activity_id') == activity_id)
                    or (submission_id and s.get('submission_id') == submission_id))
            if same and (s['state'] not in {'SESSION_FINALIZED', 'REWARD_ACTIVE'}):
                return s['session_id']
            if s['state'] not in TERMINAL:
                return s['session_id']
    return None


class StrictStudyWorkflow:
    def __init__(self, db, *, clock=now_ist, monotonic=time.monotonic, windows=None):
        self.db, self.clock, self.monotonic = db, clock, monotonic
        self.window_provider = windows or self._windows
        self.lock = threading.RLock()
        self.s = None
        self._last_mono = monotonic()
        self._saved_mono = 0
        self._scan_mono = -100
        with db._connect() as conn:
            conn.executescript(SCHEMA)

    def _windows(self, account, now):
        from core.daily_routine_gate import DailyRoutineGate
        with self.db._connect() as conn:
            rows = conn.execute("""SELECT * FROM scheduled_activities
                WHERE account_id=? AND type='HOURLY' AND is_enabled=1
                ORDER BY window_start""", (account,)).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            try:
                if (iso_to_dt(r['window_end']) > now and r['status'] in
                    {'PENDING', 'UPCOMING', 'OPEN', 'NOTIFIED', 'SCHEDULED', 'BLOCKED_WAKEUP', 'SKIPPED_PRE_GATE'}
                    and DailyRoutineGate.activity_is_eligible(r, {}, reference_now=now)):
                    result.append(r)
            except (ValueError, TypeError):
                continue
        return result

    def _save(self, transition=None):
        with self.db._connect() as conn:
            if transition:
                state, details = transition
                conn.execute("INSERT INTO strict_study_transitions(session_id,state,at,details_json) VALUES(?,?,?,?)",
                             (self.s['session_id'], state, self.clock().isoformat(), json.dumps(details)))
            conn.execute("""INSERT INTO strict_study_sessions VALUES(?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET state=excluded.state,
                data_json=excluded.data_json,updated_at=excluded.updated_at""",
                (self.s['session_id'], self.s.get('account_id', 'user_lok'), self.s['state'],
                 json.dumps(self.s), self.clock().isoformat()))
        self._saved_mono = self.monotonic()

    def _event(self, state, **details):
        self.s['state'] = state
        self._save(transition=(state, details))
        log.info("Strict study %s %s %s", self.s['session_id'], state, json.dumps(details))

    def begin(self, session_id, account_id, android_device_id, *, activate=True):
        with self.lock:
            if self.s and self.s['state'] not in TERMINAL:
                raise ValueError('The previous Strict Session is still pending.')
            with self.db._connect() as conn:
                pending=conn.execute("SELECT 1 FROM strict_study_sessions WHERE account_id=? AND state NOT IN ('SESSION_FINALIZED','REWARD_ACTIVE','FAILED','ABANDONED')", (account_id,)).fetchone()
            if pending:
                raise ValueError('This account already has a pending Strict Session. Resume or end it first.')
            now = self.clock()
            options = self.window_provider(account_id, now)
            # An already-open/imminent occurrence cannot provide positive study
            # time plus the requested buffer. Select the following real window.
            eligible = [r for r in options if (iso_to_dt(r['window_start']) - now).total_seconds() > 300]
            if not eligible:
                raise ValueError('No eligible hourly window more than five minutes away. Confirm the existing routine schedule, then retry.')
            window = min(eligible, key=lambda r: iso_to_dt(r['window_start']))
            skipped = any(iso_to_dt(r['window_start']) <= now + timedelta(minutes=5) for r in options)
            self.s = dict(session_id=session_id, account_id=account_id, android_device_id=android_device_id,
                          state='PREPARING', started_at=now.isoformat(), elapsed_seconds=0.0,
                          window=window, focus_completed_at=None, submission_id=None,
                          bluetooth_violations=0, bluetooth_connected=False, bluetooth_false_samples=0,
                          leftward_violations=0, max_leftward_violations=15, headphone_violations=0,
                          phone_violations=0, connection_loss_violations=0, android_connection_lost=False, android_monitoring_state='ANDROID_NOT_JOINED',
                          unauthorized_tab_switch_violations=0, task_manager_violations=0, command_prompt_violations=0, violations_count=0,
                          proctor_status='INITIALIZING', failed_reason='',
                          sync_verified=False, manually_completed=False, reward_status='NOT_REQUESTED',
                          monitoring_resumed=True, message=('The imminent window was skipped; using the following scheduled window.' if skipped else ''))
            self._last_mono = self.monotonic()
            self._event('PREPARING', skipped_imminent_window=skipped)
            self._calculate(now)
            self.s['initial_focus_target_seconds'] = self.s['focus_target_seconds']
            if activate:
                self.activate()
            else:
                self._save()
            return self.view()

    def bind_ipad(self,preparation_id):
        from core.strict_ipad_connection import ipad_binding,ipad_connection
        binding=ipad_binding(self.db,preparation_id,self.s['account_id'])
        state=ipad_connection(self.db,preparation_id,self.s['account_id'],binding,self.clock().timestamp())
        if not state['connected']:raise ValueError(state['reason'])
        self.s.update(ios_required=True,ios_preparation_id=preparation_id,ios_binding_hash=binding)
        self._save()

    def _ipad_status(self):
        if not self.s or not self.s.get('ios_required'):return dict(connected=True,reason='')
        if self.s.get('ios_connected_at_completion') and self.s['state'] in {'SESSION_FINALIZED','REWARD_ACTIVE'}:
            return dict(connected=True,reason='')
        from core.strict_ipad_connection import ipad_connection
        return ipad_connection(self.db,self.s.get('ios_preparation_id'),self.s['account_id'],self.s.get('ios_binding_hash',''),self.clock().timestamp())

    def activate(self):
        with self.lock:
            if self.s['state'] != 'PREPARING':
                raise ValueError('Study can start only after preparation.')
            ipad=self._ipad_status()
            if not ipad['connected']:raise ValueError(ipad['reason'])
            now=self.clock()
            if (iso_to_dt(self.s['window']['window_start'])-now).total_seconds() <= 300:
                options=[r for r in self.window_provider(self.s['account_id'],now)
                         if (iso_to_dt(r['window_start'])-now).total_seconds()>300]
                if not options:
                    raise ValueError('The proof window became imminent during preparation. Wait for the next confirmed schedule.')
                self.s['window']=options[0]
                self.s['message']='Preparation reached the imminent window; using the following eligible scheduled window.'
            self.s['started_at']=now.isoformat()
            self.s['elapsed_seconds']=0.0
            self._last_mono=self.monotonic()
            self._calculate(now)
            self._event('ACTIVE_FOCUS',window_start=self.s['window']['window_start'],
                        window_end=self.s['window']['window_end'],focus_seconds=self.s['focus_target_seconds'],
                        rule=self.s['timing_rule'])

    def _calculate(self, now):
        until = (iso_to_dt(self.s['window']['window_start']) - now).total_seconds()
        elapsed = self.s.get('elapsed_seconds', 0.0)
        total_until = elapsed + until

        # 1. Five-Minute Buffer Rule:
        # Approaching submission window: total time until next window <= 40 minutes (2400 seconds).
        # Focus Duration = Time Until Next Submission Window - 5 Minutes (300 seconds).
        # The five-minute buffer is reserved for the transition into the existing proof-submission workflow.
        approaching = total_until <= 2400.0

        if approaching:
            calc_target = max(0.0, total_until - 300.0)
            rule = 'WINDOW_MINUS_FIVE_MINUTES'
        else:
            # 3. Windows More Than 40 Minutes Away:
            # Standard focus duration: 35 minutes (2100 seconds).
            # Additional adaptive flexibility: maximum 5 minutes (up to 40 minutes total).
            calc_target = 2100.0
            rule = '35_MINUTES_PLUS_ADAPTIVE_MAX_40'

        # 5. Schedule Changes & Active Session Safety:
        # If the administrator changes the submission window during an active session:
        # - Never reset accumulated focus time: target >= elapsed.
        # - Never exceed the 40-minute normal focus limit (2400 seconds).
        # - If the window moves earlier: complete the focus phase in time for the five-minute buffer where possible.
        # - If the window moves later: do not unnecessarily extend the existing focus duration.
        existing_target = self.s.get('focus_target_seconds')
        has_initial = self.s.get('initial_focus_target_seconds') is not None
        if existing_target is not None and has_initial:
            if calc_target > existing_target:
                target = existing_target
            else:
                target = max(elapsed, calc_target)
        else:
            target = calc_target

        target = min(2400.0, target)
        self.s['focus_target_seconds'] = target
        if 'initial_focus_target_seconds' not in self.s:
            self.s['initial_focus_target_seconds'] = target
        self.s['timing_rule'] = rule

    def tick(self, *, monitoring_ok=True, refresh=False):
        with self.lock:
            if not self.s or self.s['state'] in TERMINAL:
                return self.view()
            now, mono = self.clock(), self.monotonic()
            delta = max(0.0, mono - self._last_mono)
            self._last_mono = mono
            # Sleep/stall is not proctored study. Do not credit an unobserved gap.
            if delta > 15:
                monitoring_ok = False
            from core.strict_mobile_integrity import session_interrupted
            self.s['mobile_integrity_paused']=session_interrupted(self.db,self.s['account_id'],self.s['session_id'])
            if self.s['mobile_integrity_paused']:
                monitoring_ok = False
            ipad_stat = self._ipad_status()
            ipad_is_conn = bool(ipad_stat.get('connected'))
            self.s['ipad_connected'] = ipad_is_conn
            self.s['ipad_connection_paused'] = False  # CRITICAL: Decoupled! Never pause session timer on iOS disconnect
            if not ipad_is_conn:
                self.s['ipad_control_state'] = 'IOS_STATUS_UNKNOWN'
                self.s['ipad_connection_message'] = "iOS status temporarily unavailable — session continues. Automatically checking connection."
            else:
                self.s['ipad_control_state'] = 'CONNECTED'
                self.s['ipad_connection_message'] = ipad_stat.get('reason', '')

            # Mandatory continuous iPad camera stream evaluation (15-second interruption policy)
            if self.s.get('state') in FOCUS and self.s.get('ios_required', True):
                try:
                    from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
                    cam_monitor = IPadCameraStreamMonitor.get_instance(self.db)
                    cam_diag = cam_monitor.evaluate_health(mono, now)
                    self.s['ipad_camera_interrupted'] = cam_diag.get('is_interrupted', False)
                    self.s['ipad_camera_degraded'] = cam_diag.get('is_degraded', False)
                    self.s['ipad_camera_live'] = cam_diag.get('is_live', False)
                    self.s['ipad_camera_diagnostics'] = cam_diag
                    if cam_diag.get('is_interrupted', False):
                        # CRITICAL: Do NOT pause authoritative Strict Session timer on iOS camera interruption!
                        self.s['ipad_camera_paused'] = False
                        self.s['ipad_camera_interrupted'] = True
                        for flg in ('MANDATORY_IPAD_CAMERA_INTERRUPTED', 'FLAGGED_CAMERA_INTERRUPTION'):
                            if flg not in self.s.setdefault('flags', []):
                                self.s['flags'].append(flg)
                    else:
                        self.s['ipad_camera_paused'] = False
                        self.s['ipad_camera_interrupted'] = False
                except Exception as cam_err:
                    pass
            if self.s.get('android_connection_lost'):
                monitoring_ok = False
            if monitoring_ok and self.s.get('monitoring_resumed') and self.s['state'] in FOCUS:
                self.s['elapsed_seconds'] = min(2400.0, self.s['elapsed_seconds'] + delta)
            self.s['monitoring_ok'] = bool(monitoring_ok and self.s.get('monitoring_resumed'))
            scan_due = refresh or mono - self._scan_mono >= 5
            previous_state = self.s['state']
            if scan_due:
                self._scan_mono = mono
                self._refresh_window(now)
            if not self.s.get('focus_completed_at') and self.s['state'] in FOCUS:
                elapsed = self.s['elapsed_seconds']
                window_open = iso_to_dt(self.s['window']['window_start']) <= now < iso_to_dt(self.s['window']['window_end'])
                if elapsed >= self.s['focus_target_seconds'] or window_open:
                    if self.s['state'] == 'ACTIVE_FOCUS':
                        self._event('MINIMUM_DURATION_COMPLETED', actual_focus_seconds=elapsed)
                    if (self.s['timing_rule'] == 'WINDOW_MINUS_FIVE_MINUTES' or window_open or elapsed >= 2400):
                        self.s['focus_completed_at'] = now.isoformat()
                        self._event('FOCUS_COMPLETED', actual_focus_seconds=elapsed)
                        if window_open:
                            self._event('PROOF_WINDOW_OPEN', window=self.s['window']['activity_id'])
                            self._event('AWAITING_ANDROID_SUBMISSION')
                        else:
                            self._event('AWAITING_PROOF_WINDOW', window_start=self.s['window']['window_start'])
                    elif self.s['state'] != 'ADAPTIVE_COMPLETION':
                        self._event('ADAPTIVE_COMPLETION', maximum_focus_seconds=2400)
            if self.s.get('focus_completed_at') and self.s['state'] == 'AWAITING_PROOF_WINDOW':
                if now >= iso_to_dt(self.s['window']['window_start']):
                    self._event('PROOF_WINDOW_OPEN', window=self.s['window']['activity_id'])
                    self._event('AWAITING_ANDROID_SUBMISSION')
            # Poll again after phase transitions so an accepted, delayed receipt
            # can be reconciled without manufacturing an acknowledgement.
            if self.s.get('focus_completed_at') and (scan_due or self.s['state'] != previous_state):
                self._scan_proof(now)
            if mono - self._saved_mono >= 5:
                self._save()
            return self.view()

    def _refresh_window(self, now):
        if self.s.get('submission_id'):
            return
        old = self.s['window']
        with self.db._connect() as conn:
            row = conn.execute('SELECT * FROM scheduled_activities WHERE activity_id=? AND account_id=?',
                               (old['activity_id'], self.s['account_id'])).fetchone()
        # Preserve a closed window while waiting for a delayed accepted receipt.
        if row and row['is_enabled'] and row['status'] not in {'CANCELLED', 'EXCUSED', 'SKIPPED'}:
            updated = dict(row)
        else:
            options = self.window_provider(self.s['account_id'], now)
            updated = options[0] if options else old
            if not options:
                self.s['message'] = 'The bound window was removed. Waiting for an eligible existing schedule; no receipt can be accepted for a cancelled occurrence.'
        keys = ('activity_id', 'schedule_version', 'window_start', 'window_end')
        if any(updated.get(k) != old.get(k) for k in keys):
            self.s['window'] = updated
            if not self.s.get('focus_completed_at'):
                self._calculate(now)
            self._event(self.s['state'], schedule_changed=True, window_start=updated['window_start'],
                        focus_seconds=self.s['focus_target_seconds'], elapsed_seconds=self.s['elapsed_seconds'])

    def _verified_receipt(self, event):
        s, w = self.s, self.s['window']
        p = json.loads(event['payload_json'])
        accepted = iso_to_dt(event['accepted_at'])
        if (not s.get('focus_completed_at') or event['account_id'] != s['account_id']
            or event['source_device_id'] != s['android_device_id']
            or event['activity_id'] != w['activity_id'] or event['schedule_version'] != w['schedule_version']
            or p.get('source_platform') != 'ANDROID' or p.get('task_type') != 'HOURLY'
            or p.get('status') not in {'SUBMITTED', 'VALIDATING', 'VERIFIED', 'COMPLETED'}
            or p.get('submission_id') != event['submission_id']
            or p.get('activity_id') != event['activity_id'] or p.get('schedule_version') != event['schedule_version']
            or p.get('accepted_at') != event['accepted_at']
            or p.get('account_id') != s['account_id'] or p.get('source_device_id') != s['android_device_id']
            or p.get('strict_session_id', s['session_id']) != s['session_id']
            or not (iso_to_dt(w['window_start']) <= accepted < iso_to_dt(w['window_end']))
            or accepted < iso_to_dt(s['focus_completed_at']) or accepted > self.clock()
            or not p.get('category') or not p.get('description') or not p.get('has_evidence')):
            return False
        with self.db._connect() as conn:
            device = conn.execute("SELECT 1 FROM devices WHERE device_id=? AND user_id=? AND (UPPER(TRIM(platform))='ANDROID' OR UPPER(TRIM(platform)) LIKE 'ANDROID %') AND pairing_state='PAIRED'", (s['android_device_id'], s['account_id'])).fetchone()
            act = conn.execute('SELECT * FROM scheduled_activities WHERE activity_id=? AND account_id=?', (w['activity_id'], s['account_id'])).fetchone()
            proof = conn.execute('SELECT * FROM evidence_files WHERE evidence_id=?', (p.get('evidence_id'),)).fetchone()
        return bool(device and act and act['is_enabled'] and act['schedule_version'] == event['schedule_version']
                    and act['submission_id'] == event['submission_id']
                    and act['status'] in {'SUBMITTED', 'VALIDATING', 'AI_PROVISIONAL', 'VERIFIED', 'COMPLETED'}
                    and proof and not proof['is_deleted'] and not proof['is_dev_mode']
                    and proof['device_id'] == s['android_device_id']
                    and proof['sha256_hash'] == p.get('evidence_sha256')
                    and is_valid_retained_proof_file(proof['file_path'], 'Hourly', proof['sha256_hash']))

    def _scan_proof(self, now):
        if not self.s.get('focus_completed_at') or self.s['state'] in TERMINAL:
            return
        with self.db._connect() as conn:
            rows = conn.execute('SELECT * FROM proof_submission_events WHERE account_id=? AND activity_id=? ORDER BY accepted_at',
                                (self.s['account_id'], self.s['window']['activity_id'])).fetchall()
        valid = None
        for event in rows:
            try:
                if self._verified_receipt(event):
                    valid = event
                    break
            except (ValueError, TypeError, KeyError):
                continue
        if not valid:
            if self.s.get('sync_verified'):
                self.s['sync_verified'] = False
                self._event('AWAITING_ANDROID_SUBMISSION', receipt_revalidation_failed=True)
            return
        if self.s.get('sync_verified'):
            return
        with self.db._connect() as conn:
            prior = conn.execute('SELECT session_id FROM strict_study_receipts WHERE account_id=? AND submission_id=?',
                                 (self.s['account_id'], valid['submission_id'])).fetchone()
            if prior and prior[0] != self.s['session_id']:
                return
            own = conn.execute('SELECT submission_id FROM strict_study_receipts WHERE session_id=?', (self.s['session_id'],)).fetchone()
            if own and own[0] != valid['submission_id']:
                return
            conn.execute('INSERT INTO strict_study_receipts VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING',
                         (self.s['account_id'], valid['submission_id'], self.s['session_id'], valid['event_id'], now.isoformat()))
        self.s['submission_id'] = valid['submission_id']
        self._event('ANDROID_PROOF_SUBMITTED', submission_id=valid['submission_id'])
        self._event('SYNCING_PROOF', event_id=valid['event_id'])
        self.s['sync_verified'] = True
        self._event('PROOF_SYNCHRONIZED', submission_id=valid['submission_id'])
        self._event('END_SESSION_ENABLED')

    def bluetooth_sample(self, connected):
        with self.lock:
            if not self.s or self.s['state'] in TERMINAL:
                return False
            if connected is None:
                self.s['bluetooth_false_samples'] = 0
                return False
            if connected:
                self.s.update(bluetooth_connected=True, bluetooth_false_samples=0)
            elif self.s['bluetooth_connected']:
                self.s['bluetooth_false_samples'] += 1
                if self.s['bluetooth_false_samples'] >= 2:
                    self.s['bluetooth_connected'] = False
                    self.s['bluetooth_violations'] += 1
                    self.s['message'] = ('Bluetooth audio disconnected. Reconnect it; a second confirmed disconnection ends this session and proposes the configured two-point deduction.')
                    self._event(self.s['state'], bluetooth_disconnect=self.s['bluetooth_violations'])
                    if self.s['bluetooth_violations'] >= 2:
                        self.abort('Second confirmed Bluetooth disconnection', failed=True)
                        return True
            return False

    def record_leftward_violation(self, count: int, message: str = "") -> Dict[str, Any]:
        with self.lock:
            if not self.s or self.s['state'] in TERMINAL:
                return {"failed": False, "violations": count}
            self.s['leftward_violations'] = count
            self.s['last_leftward_message'] = message
            self._event(self.s['state'], leftward_violation=count, message=message)
            failed = count > 15
            if failed:
                fail_msg = f"StrictGuard: Maximum leftward attention violations exceeded ({count} / 15)"
                self.s['failed_reason'] = fail_msg
                self.s['reward_status'] = 'CANCELLED'
                self.abort(fail_msg, failed=True)
            return {"failed": failed, "violations": count, "message": message}

    def record_integrity_violation(self,event):
        """Persist a confirmed session warning without writing to the points ledger."""
        with self.lock:
            if not self.s or self.s['state'] in TERMINAL:return False
            allowed_categories = {
                'FACE_MISSING','PHONE_VISIBLE','EARBUD_VISIBLE','UNAUTHORIZED_URL',
                'WINDOW_FOCUS_LOST','TAB_SWITCH_ATTEMPT','MOBILE_PHONE_DETECTED',
                'MOBILE_PHONE_DETECTION_CLEARED','AIRPODS_WORN_CAMERA_DETECTED',
                'BLUETOOTH_AUDIO_DEVICE_CONNECTED','BLUETOOTH_AUDIO_DEVICE_DISCONNECTED',
                'HEADPHONE_VIOLATION_CONFIRMED','HEADPHONE_VIOLATION_CLEARED',
                'UNAUTHORIZED_TAB_SWITCH','TASK_MANAGER_OPENED','COMMAND_PROMPT_OPENED'
            }
            if not isinstance(event,dict) or event.get('category') not in allowed_categories:return False
            identity=str(event.get('id',''))[:128]
            if not identity or identity in self.s.get('integrity_event_ids',[]):return False
            self.s.setdefault('integrity_event_ids',[]).append(identity)
            self.s.setdefault('integrity_events',[]).append(dict(event))
            self.s['integrity_events']=self.s['integrity_events'][-200:]
            counts=self.s.setdefault('integrity_counts',{})
            category=event['category'];counts[category]=counts.get(category,0)+1
            if category == 'MOBILE_PHONE_DETECTED':
                self.s['phone_violations'] = counts.get('MOBILE_PHONE_DETECTED', 0)
            elif category == 'UNAUTHORIZED_TAB_SWITCH':
                self.s['unauthorized_tab_switch_violations'] = counts.get('UNAUTHORIZED_TAB_SWITCH', 0)
            elif category == 'TASK_MANAGER_OPENED':
                self.s['task_manager_violations'] = counts.get('TASK_MANAGER_OPENED', 0)
            elif category == 'COMMAND_PROMPT_OPENED':
                self.s['command_prompt_violations'] = counts.get('COMMAND_PROMPT_OPENED', 0)
            self.s['violations_count'] = (
                self.s.get('leftward_violations', 0)
                + counts.get('UNAUTHORIZED_TAB_SWITCH', 0)
                + counts.get('TASK_MANAGER_OPENED', 0)
                + counts.get('COMMAND_PROMPT_OPENED', 0)
            )
            if self.s['violations_count'] > self.s.get('max_leftward_violations', 15):
                fail_msg = f"StrictGuard: Maximum permitted violations exceeded ({self.s['violations_count']} / 15)"
                self.s['failed_reason'] = fail_msg
                self.s['reward_status'] = 'CANCELLED'
                self.abort(fail_msg, failed=True)
            self.s['message']=str(event.get('description') or 'Strict Session integrity warning.')[:500]
            self._event(self.s['state'],integrity_violation=dict(event))
            return True

    def is_submission_window_open(self, now=None):
        with self.lock:
            if not self.s or not self.s.get('window'):
                return False
            w = self.s['window']
            try:
                start = iso_to_dt(w['window_start'])
                end = iso_to_dt(w['window_end'])
                check_now = now or self.clock()
                return start <= check_now < end
            except Exception:
                return False

    def is_authorized_proof_submission_active(self):
        with self.lock:
            if not self.s:
                return False
            in_submission_phase = bool(
                self.s.get('focus_completed_at') or
                self.s.get('state') in {'AWAITING_PROOF_WINDOW', 'AWAITING_ANDROID_SUBMISSION', 'PROOF_SYNCHRONIZING', 'END_SESSION_ENABLED'}
            )
            return bool(in_submission_phase and self.s.get('authorized_proof_in_progress'))

    def record_headphone_violation(self, device_name: str, violation_number: int) -> Dict[str, Any]:
        with self.lock:
            if not self.s or self.s['state'] in TERMINAL:
                return {"failed": False, "violations": violation_number}
            self.s['headphone_violations'] = violation_number
            failed = violation_number >= 2
            if violation_number == 1:
                self.s['headphone_paused'] = True
                self.s['message'] = f"Prohibited headphone detected: {device_name}. Disconnect it to continue."
                self._event(self.s['state'], headphone_warning=1, device=device_name)
            elif failed:
                fail_msg = f"Second confirmed prohibited headphone connection ({device_name})"
                self.s['failed_reason'] = fail_msg
                self.s['reward_status'] = 'CANCELLED'
                # The manager's idempotent two-point proposal is the sole
                # deduction path; never also write a second ledger debit here.
                self.abort(fail_msg, failed=True)
            return {"failed": failed, "violations": violation_number, "device": device_name}

    def record_phone_usage_violation(self, app_label: str, package_name: str, timestamp: str, correlated: bool = False):
        with self.lock:
            if not self.s: return {}
            self.s['phone_violations'] = self.s.get('phone_violations', 0) + 1
            curr_v = self.s['phone_violations']
            self._event('ANDROID_APP_USAGE_DETECTED', app_label=app_label, package_name=package_name, timestamp=timestamp, correlated=correlated, count=curr_v)
            self.s['last_blocked_app'] = app_label
            self._save()
            return {"success": True, "count": curr_v}

    def set_android_connection_state(self, state: str, lost: bool = False):
        with self.lock:
            if not self.s: return
            self.s['android_monitoring_state'] = state
            self.s['android_connection_lost'] = bool(lost)
            self._save()

    def record_android_connection_loss_violation(self, reason: str = "Android companion connection lost"):
        with self.lock:
            if not self.s: return {}
            self.s['connection_loss_violations'] = self.s.get('connection_loss_violations', 0) + 1
            curr_v = self.s['connection_loss_violations']
            self._event('ANDROID_CONNECTION_LOST', reason=reason, count=curr_v)
            self._save()
            return {"success": True, "count": curr_v}

    def complete(self, session_id):
        with self.lock:
            self.tick(refresh=True, monitoring_ok=False)
            if (not self.s or session_id != self.s['session_id'] or self.s['state'] != 'END_SESSION_ENABLED'
                    or not self.s['sync_verified'] or not self.s.get('monitoring_resumed')):
                raise ValueError('End Session requires the current verified Android receipt and resumed monitoring.')
            if self.s.get('leftward_violations', 0) > 15:
                raise ValueError("Session cannot be completed: maximum leftward attention violations exceeded.")
            if self.s.get('headphone_violations', 0) >= 2:
                raise ValueError("Session cannot be completed: prohibited headphone violation limit exceeded.")
            if self.s.get('mobile_integrity_paused'):
                raise ValueError('Return the interrupted mobile participant to Strict Session before completing.')
            if not self._ipad_status()['connected']:
                raise ValueError('Reconnect the session iPad before completing Strict Session.')
            self.s['manually_completed'] = True
            self.s['ios_connected_at_completion']=True
            self.s['reward_status'] = 'PENDING_EXISTING_ELIGIBILITY'
            self._event('SESSION_FINALIZED', manual=True, submission_id=self.s['submission_id'])
            return self.view()

    def abort(self, reason, *, failed=False):
        with self.lock:
            if self.s and self.s['state'] not in TERMINAL:
                self.s['reward_status'] = 'INELIGIBLE'
                self.s['terminated_at'] = self.clock().isoformat()
                self.s['message'] = reason
                self._event('FAILED' if failed else 'ABANDONED', reason=reason)

    def reset(self, reason="Session reset"):
        with self.lock:
            if self.s and self.s['state'] not in TERMINAL:
                self.s['reward_status'] = 'INELIGIBLE'
                self.s['terminated_at'] = self.clock().isoformat()
                self.s['message'] = reason
                self._event('ABANDONED', reason=reason)
            self.s = None

    def recover(self, account_id):
        with self.lock:
            with self.db._connect() as conn:
                row = conn.execute("""SELECT data_json FROM strict_study_sessions WHERE account_id=?
                    ORDER BY CASE WHEN state NOT IN ('SESSION_FINALIZED','REWARD_ACTIVE','FAILED','ABANDONED') THEN 0 ELSE 1 END,
                    json_extract(data_json,'$.started_at') DESC LIMIT 1""", (account_id,)).fetchone()
            if not row:
                return
            self.s = json.loads(row[0])
            try:
                from windows.strict_violation_engine import get_strict_violation_engine
                eng = get_strict_violation_engine(self.db)
                sid = self.s.get('session_id')
                if sid:
                    eng_counts = eng.get_category_counts(sid)
                    self.s['unauthorized_tab_switch_violations'] = eng_counts.get('UNAUTHORIZED_TAB_SWITCH', 0)
                    self.s['task_manager_violations'] = eng_counts.get('TASK_MANAGER_OPENED', 0)
                    self.s['command_prompt_violations'] = eng_counts.get('COMMAND_PROMPT_OPENED', 0)
                    total_v = eng.get_total_violations(sid)
                    if total_v > 0:
                        self.s['violations_count'] = total_v
            except Exception as e:
                log.warning("Could not restore violation engine on recover: %s", e)
            self._last_mono = self.monotonic()
            if self.s['state'] in FOCUS or self.s['state']=='PREPARING':
                self.abort('Study interrupted by restart; offline time was not credited')
            elif self.s['state'] not in TERMINAL:
                self.s.update(sync_verified=False, monitoring_resumed=False)
                self._event('AWAITING_ANDROID_SUBMISSION', recovered=True, requires_monitoring_resume=True)
                self.tick(monitoring_ok=False, refresh=True)

    def view(self):
        if not self.s:
            return dict(state='IDLE', end_enabled=False, windows_connected=False, pc_connected=False, android_connected=False)
        result = json.loads(json.dumps(self.s))
        result.pop('ios_binding_hash',None)
        result['windows_connected'] = True
        result['pc_connected'] = True
        result['host_connected'] = True
        result['host_status'] = 'ACTIVE'
        android_id = self.s.get('android_device_id')
        android_verified = False
        if android_id:
            try:
                with self.db._connect() as conn:
                    row = conn.execute("SELECT 1 FROM devices WHERE device_id=? AND (UPPER(TRIM(platform))='ANDROID' OR UPPER(TRIM(platform)) LIKE 'ANDROID %') AND pairing_state='PAIRED'", (android_id,)).fetchone()
                    android_verified = bool(row)
            except Exception:
                android_verified = True
            if not android_verified and self.s.get('account_id'):
                android_verified = True
        result['android_connected'] = bool(android_id and (android_verified or self.s.get('state') not in TERMINAL)) and not self.s.get('android_connection_lost', False)
        if self.s.get('android_connection_lost'):
            result['android_status'] = 'DISCONNECTED'
        result['android_monitoring_state'] = self.s.get('android_monitoring_state', 'ANDROID_NOT_JOINED')
        result['android_connection_lost'] = self.s.get('android_connection_lost', False)
        result['phone_violations'] = self.s.get('phone_violations', 0)
        result['connection_loss_violations'] = self.s.get('connection_loss_violations', 0)
        result['android_proof_app'] = 'VERIFIED' if result['android_connected'] else 'UNVERIFIED'
        ipad=self._ipad_status()
        result['ios_connected']=ipad['connected'] if self.s.get('ios_required') else None
        if self.s.get('ios_required') and not ipad['connected']:
            result['ipad_connection_message'] = "iOS status temporarily unavailable — session continues. Automatically checking connection."
        else:
            result['ipad_connection_message'] = ipad['reason']
        result['ios_screen_sharing']=ipad.get('screen_sharing', False) if self.s.get('ios_required') else False
        result['ios_screen_preview']=ipad.get('screen_preview') if self.s.get('ios_required') else None
        try:
            from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor
            cam_monitor = IPadCameraStreamMonitor.get_instance(self.db)
            result['ipad_camera_diagnostics'] = cam_monitor.get_diagnostics()
            is_active_focus = bool(self.s and self.s.get('state') in FOCUS)
            result['ipad_camera_paused'] = self.s.get('ipad_camera_paused', False) if is_active_focus else False
            result['ipad_camera_interrupted'] = self.s.get('ipad_camera_interrupted', False) if is_active_focus else False
            result['ipad_camera_degraded'] = self.s.get('ipad_camera_degraded', False) if is_active_focus else False
            result['ipad_camera_live'] = self.s.get('ipad_camera_live', False)
            result['flags'] = list(self.s.get('flags', []))
            if is_active_focus and result['ipad_camera_interrupted'] and result.get('ipad_camera_diagnostics', {}).get('interruption_warning_modal'):
                result['ipad_camera_alert'] = result['ipad_camera_diagnostics']['interruption_warning_modal']
            else:
                result['ipad_camera_alert'] = None
        except Exception:
            pass
        now = self.clock()
        result['focus_remaining_seconds'] = max(0, result.get('focus_target_seconds', 0) - result.get('elapsed_seconds', 0)) if not result.get('focus_completed_at') else 0
        result['adaptive_remaining_seconds'] = max(0, 2400-result['elapsed_seconds']) if result['state'] == 'ADAPTIVE_COMPLETION' else 0
        result['window_remaining_seconds'] = max(0, (iso_to_dt(result['window']['window_start']) - now).total_seconds())
        result['window_imminent'] = bool(result['window_remaining_seconds'] <= 300)
        result['buffer_seconds'] = 300 if result.get('timing_rule') == 'WINDOW_MINUS_FIVE_MINUTES' else max(0, result['window_remaining_seconds'] - result['focus_remaining_seconds'])
        result['initial_focus_target_seconds'] = self.s.get('initial_focus_target_seconds', result['focus_target_seconds'])
        result['leftward_violations'] = self.s.get('leftward_violations', 0)
        result['max_leftward_violations'] = self.s.get('max_leftward_violations', 15)
        result['headphone_violations'] = self.s.get('headphone_violations', 0)
        result['unauthorized_tab_switch_violations'] = self.s.get('unauthorized_tab_switch_violations', 0)
        result['task_manager_violations'] = self.s.get('task_manager_violations', 0)
        result['command_prompt_violations'] = self.s.get('command_prompt_violations', 0)
        result['violations_count'] = self.s.get('violations_count', self.s.get('leftward_violations', 0))
        result['failed_reason'] = self.s.get('failed_reason', '')
        result['reward_eligible'] = bool(
            self.s['state'] not in TERMINAL
            and self.s.get('leftward_violations', 0) <= 15
            and self.s.get('headphone_violations', 0) < 2
            and self.s.get('reward_status') != 'CANCELLED'
        )
        result['end_enabled'] = (
            result['state'] == 'END_SESSION_ENABLED'
            and result['sync_verified']
            and result.get('monitoring_resumed', False)
            and result['reward_eligible']
            and not result.get('mobile_integrity_paused', False)
            and not result.get('ipad_camera_paused', False)
            and not result.get('ipad_camera_interrupted', False)
            and ipad['connected']
        )
        return result
