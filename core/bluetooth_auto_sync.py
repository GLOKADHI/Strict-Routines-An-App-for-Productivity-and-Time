"""Opt-in receiver for one registered, OS-paired phone. No discovery or new ledger."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import threading
import time

from core import bluetooth_protocol as wire
from core.bluetooth_transfer import BluetoothTransferManager,TransferConflict
from windows.bluetooth_rfcomm import AutomaticSyncRfcomm, normalize_address

CONFIG_KEY = 'bluetooth_auto_sync_v1'
MODE = 'REGISTERED_DEVICE_V1'


def automatic_key(token_hash, device, account):
    return wire.mac(token_hash, 'STRICTROUTINES_AUTO_V1\n' + wire.identifier(device) + '\n' + wire.identifier(account))


def automatic_receipt_text(receipt):
    return '\n'.join((wire.receipt_text(receipt), MODE, str(receipt.get('pending_metadata_count', 0)),
                      str(receipt.get('applied_record_count', 0)),receipt.get('planning_sha256','')))


class AutomaticBluetoothSyncManager(BluetoothTransferManager):
    def __init__(self, db, engine=None, backend_factory=None, clock=time.monotonic):
        super().__init__(db, engine, backend_factory or AutomaticSyncRfcomm)
        self._clock = clock
        self._shutdown = threading.Event()
        self._wake = threading.Event()
        self._worker = None
        self._next_attempt = 0
        self._retry_delay = 15
        self._state = 'DISABLED'
        self._message = 'Automatic Bluetooth sync is off'
        self._config = self._read_config()

    def _read_config(self, conn=None):
        if conn is None:
            with self.db._connect() as connection:
                return self._read_config(connection)
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_config'").fetchone():
            return {'enabled': False}
        row = conn.execute('SELECT value FROM system_config WHERE key=?', (CONFIG_KEY,)).fetchone()
        try:
            value=json.loads(row[0]) if row else {'enabled':False}
            return value if isinstance(value,dict) and type(value.get('enabled')) is bool else {'enabled':False}
        except (ValueError,TypeError):
            return {'enabled':False}

    def _credentials(self, conn, device, account):
        return conn.execute('''SELECT c.token_hash FROM device_credentials c JOIN devices d ON d.device_id=c.device_id
            WHERE c.device_id=? AND c.account_id=? AND d.user_id=? AND c.revoked_at IS NULL
            AND d.pairing_state!='REVOKED' AND d.status!='REVOKED' AND upper(d.platform) LIKE '%ANDROID%' ''',
            (device, account, account)).fetchall()

    def configure(self, enabled, peer_address='', device_id='', metadata_policy='STRICT'):
        if type(enabled) is not bool or metadata_policy not in ('STRICT', 'QUEUE_RECONCILIATION'):
            return {'success': False, 'error': 'Select an explicit automatic sync setting and metadata policy'}
        config = {'enabled': enabled}
        try:
            if enabled:
                address = normalize_address(peer_address)
                peer = next((p for p in self.backend_factory.paired_devices() if p['address'] == address and p.get('paired')), None)
                if not peer:
                    raise ValueError('Select the intended OS-paired Android phone')
                account = self.db.get_active_user_profile().get('user_id', 'user_lok')
                wire.identifier(device_id)
                with self.db._connect() as conn:
                    conn.execute('CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT)')
                    if not self._credentials(conn, device_id, account):
                        raise ValueError('Register this Android phone using QR / code first')
                config.update(peer_address=address, peer_name=peer['name'], device_id=device_id,
                              account_id=account, metadata_policy=metadata_policy)
            # Lock ordering matches import (manager, then DB); disabling waits for an
            # already committed transaction, and closes an uncommitted connection.
            with self._lock:
                self.cancel_session()
                with self.db._connect() as conn:
                    conn.execute('INSERT OR REPLACE INTO system_config(key,value,updated_at) VALUES(?,?,?)',
                                 (CONFIG_KEY, json.dumps(config), datetime.now(timezone.utc).isoformat()))
                self._config = config
                self._next_attempt, self._retry_delay = 0, 15
                self._state = 'STARTING' if enabled else 'DISABLED'
                self._message = 'Enable automatic sync to this PC in the Android app' if enabled else 'Automatic Bluetooth sync is off'
            self._wake.set()
            return {'success': True, 'enabled': enabled}
        except (ValueError, OSError) as error:
            return {'success': False, 'error': str(error)}

    def start(self):
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._shutdown.clear()
            self._worker = threading.Thread(target=self._run, daemon=True, name='SR-AutomaticBluetooth')
            self._worker.start()

    def stop(self):
        self._shutdown.set()
        self._wake.set()
        self.cancel_session()
        if self._worker and self._worker is not threading.current_thread():
            self._worker.join(timeout=2)

    def _run(self):
        while not self._shutdown.is_set():
            self._wake.clear()
            try:
                self.tick()
            except Exception:
                # No credentials, codes, evidence or payload content in logs/status.
                self._state, self._message = 'RETRYING', 'Receiver unavailable; retrying automatically'
                self._next_attempt = self._clock() + 60
            self._wake.wait(10)

    def tick(self):
        with self._lock:
            config = self._config
            if not config.get('enabled'):
                return
            account = self.db.get_active_user_profile().get('user_id', 'user_lok')
            with self.db._connect() as conn:
                valid = config['account_id'] == account and bool(self._credentials(conn, config['device_id'], account))
            if not valid:
                self.cancel_session()
                self._state, self._message = 'REGISTRATION_REQUIRED', 'Phone registration was revoked or account changed; pair again'
                return
            if self._active_session and datetime.now(timezone.utc) >= self._active_session['expires_at']:
                self._cleanup_session_locked()
            if self._active_session or self._clock() < self._next_attempt:
                return
            result = self.start_session(peer_address=config['peer_address'], metadata_policy=config['metadata_policy'])
            if result.get('success'):
                self._retry_delay = 15
                self._state, self._message = 'READY', 'Waiting for the remembered phone · reconnects automatically'
                self._active_session['automatic_config'] = dict(config)
            else:
                self._state, self._message = 'RETRYING', result.get('error', 'Waiting for Bluetooth')
                self._next_attempt = self._clock() + self._retry_delay
                self._retry_delay = min(300, self._retry_delay * 2)

    def _hello_extra(self, session):
        return {'auth_mode': MODE}

    def _authentication_key(self, auth, session):
        config = session.get('automatic_config')
        if not config or auth.get('auth_mode') != MODE or auth.get('device_id') != config['device_id'] or auth.get('account_id') != config['account_id']:
            raise ValueError('Automatic sender does not match the remembered registration')
        with self.db._connect() as conn:
            rows = self._credentials(conn, config['device_id'], config['account_id'])
        for row in rows:
            token_hash = row['token_hash']
            tag = hashlib.sha256(token_hash.encode('ascii')).hexdigest()
            if hmac.compare_digest(tag, str(auth.get('credential_tag', ''))):
                session['credential_hash'] = token_hash
                return automatic_key(token_hash, config['device_id'], config['account_id'])
        raise ValueError('Phone credential expired; register it again')

    def _authorize_commit(self, conn, session, source_device):
        config = session.get('automatic_config') or {}
        if self._read_config(conn) != config or not config.get('enabled') or source_device != config.get('device_id'):
            raise ValueError('Automatic sync was disabled or its selected phone changed')
        if self.db.get_active_user_profile().get('user_id', 'user_lok') != config['account_id']:
            raise ValueError('Active account changed')
        rows = self._credentials(conn, source_device, config['account_id'])
        if not any(hmac.compare_digest(row['token_hash'], session.get('credential_hash', '')) for row in rows):
            raise ValueError('Phone registration revoked before commit')

    def _sign_receipt(self, receipt, authentication_key):
        receipt['auth_mode'] = MODE
        planning=dict(results=receipt.get('planning_results',[]))
        if receipt.get('success'):
            from core.gym_mode_engine import GymModeEngine
            from core.gym_daily_decision import DailyGymDecision
            from core.daily_college_decision import DailyCollegeDecision
            from core.time_window_engine import TimeWindowEngine
            account=self._config['account_id']
            state=GymModeEngine(self.db).state(account)
            state.pop('eligible_sessions',None)  # Booking selection stays in the authoritative online API.
            planning['state']=state
            today=state['date'];tomorrow=TimeWindowEngine.get_next_calendar_date(today)
            planning['planning']={day:dict(gym=DailyGymDecision(self.db).get(account,day),
                college=DailyCollegeDecision(self.db).get(account,day)) for day in (today,tomorrow)}
        receipt['planning_payload']=json.dumps(planning,ensure_ascii=False,separators=(',',':'),sort_keys=True)
        receipt['planning_sha256']=hashlib.sha256(receipt['planning_payload'].encode('utf-8')).hexdigest()
        receipt['automatic_receipt_mac'] = wire.mac(authentication_key, automatic_receipt_text(receipt))
        if receipt.get('success'):
            # A post-commit presence refresh must never prevent receipt delivery.
            try:
                self.db.record_device_heartbeat(receipt['source_device'])
            except Exception:
                pass

    def _merge_extra_records(self,conn,payload,session):
        from core.gym_daily_decision import DailyGymDecision,GymDecisionError
        from core.daily_college_decision import DailyCollegeDecision
        from core.time_window_engine import now_ist
        requests=payload.get('planning_requests',[])
        if len(requests)>8:raise ValueError('Too many pending planning requests')
        created=known=pending=0
        results=[]
        account=session['account_id']
        for entry in requests:
            kind=entry.get('kind');body=entry.get('payload')
            if kind not in ('GYM_DECISION','COLLEGE_DECISION','GYM_ACTIVATE','GYM_OFF') or not isinstance(body,dict):
                raise ValueError('Invalid planning request schema')
            if body.get('account_id')!=account:raise ValueError('Planning account does not match the registered phone')
            request_id=wire.identifier(body.get('request_id'))
            if len(request_id)<8:raise ValueError('A stable planning request ID is required')
            original=json.dumps(entry,sort_keys=True,separators=(',',':'),ensure_ascii=False)
            digest=hashlib.sha256(original.encode('utf-8')).hexdigest()
            prior=conn.execute('SELECT * FROM bluetooth_planning_receipts WHERE account_id=? AND request_id=?',(account,request_id)).fetchone()
            if prior:
                if prior['request_sha256']!=digest:raise ValueError('Planning request ID has different immutable content')
                outcome=json.loads(prior['result_json']);known+=1
            else:
                existed=False
                conn.execute('SAVEPOINT bluetooth_planning')
                try:
                    if kind=='GYM_DECISION':
                        existed=bool(conn.execute('SELECT 1 FROM gym_decision_requests WHERE account_id=? AND request_id=?',(account,request_id)).fetchone())
                        result=DailyGymDecision(self.db).save(account,body.get('date'),body.get('gym_planned'),body.get('expected_revision'),request_id,
                            timezone=body.get('timezone','Asia/Kolkata'),actor=account,device_id=payload['device_id'],connection=conn)
                    elif kind=='COLLEGE_DECISION':
                        existed=bool(conn.execute('SELECT 1 FROM college_decision_requests WHERE account_id=? AND request_id=?',(account,request_id)).fetchone())
                        result=DailyCollegeDecision(self.db).save(account,body.get('date'),body.get('attends_college'),body.get('expected_revision'),request_id,
                            bath_plans=body.get('bath_plans'),timezone=body.get('timezone','Asia/Kolkata'),actor=account,device_id=payload['device_id'],connection=conn)
                    elif kind=='GYM_ACTIVATE':
                        from core.gym_mode_engine import GymModeEngine
                        existed=bool(conn.execute('SELECT 1 FROM gym_activation_requests WHERE account_id=? AND request_id=?',(account,request_id)).fetchone())
                        result=GymModeEngine(self.db).reconcile_offline_activation(
                            body.get('gym_activity_id'),body.get('activation_time_ms'),request_id,
                            session_id=body.get('session_id'),account_id=account,timezone=body.get('timezone','Asia/Kolkata'),connection=conn)
                        if not result.get('success'):
                            raise GymDecisionError(result.get('code','CONFLICT'),result.get('error','Offline Gym activation conflict'),409)
                    else:
                        sid=wire.identifier(body.get('session_id'))
                        stamp=now_ist()
                        conn.execute("UPDATE gym_mode_sessions SET status='EXPIRED',toggled_off_early=1,toggled_off_at_ms=?,updated_at=? WHERE account_id=? AND session_id=? AND status='ACTIVE'",
                            (int(stamp.timestamp()*1000),stamp.isoformat(),account,sid))
                        result={'success':True}
                    outcome=dict(kind=kind,request_id=request_id,date=body.get('date'),accepted=True,result=result)
                    conn.execute('RELEASE bluetooth_planning')
                except GymDecisionError as error:
                    conn.execute('ROLLBACK TO bluetooth_planning');conn.execute('RELEASE bluetooth_planning')
                    if error.code not in ('REVISION_CONFLICT','ADMIN_REQUIRED','HISTORICAL_DATE'):raise ValueError(str(error))
                    if session['metadata_policy']=='STRICT':raise TransferConflict(str(error))
                    outcome=dict(kind=kind,request_id=request_id,date=body.get('date'),accepted=False,code=error.code,error=str(error))
                conn.execute('INSERT INTO bluetooth_planning_receipts VALUES(?,?,?,?,?,?)',
                    (account,request_id,digest,original,json.dumps(outcome),datetime.now(timezone.utc).isoformat()))
                if existed:known+=1
                else:created+=1
            pending+=int(not outcome['accepted'])
            results.append(outcome)
        return created,known,pending,{'planning_results':results}

    def get_status(self):
        status = super().get_status()
        status.pop('session_code', None)
        account = self.db.get_active_user_profile().get('user_id', 'user_lok')
        with self.db._connect() as conn:
            devices = conn.execute('''SELECT DISTINCT d.device_id, d.device_name FROM devices d
                JOIN device_credentials c ON c.device_id=d.device_id WHERE c.account_id=? AND d.user_id=?
                AND c.revoked_at IS NULL AND d.pairing_state!='REVOKED' AND d.status!='REVOKED'
                AND upper(d.platform) LIKE '%ANDROID%' ''', (account, account)).fetchall()
        with self._lock:
            status.update(config=dict(self._config), receiver_state=self._state, message=self._message,
                          registered_devices=[dict(row) for row in devices], auth_mode=MODE,
                          next_retry_seconds=max(0, int(self._next_attempt - self._clock())))
        return status
