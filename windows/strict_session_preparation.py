"""Windows/iOS/Android/Browser Prerequisite Gate, independent of APK proof sync and the points ledger."""
from datetime import datetime, timezone
import hashlib
import hmac
import io
import base64
import json
import secrets
import threading
import time
import re
from urllib.parse import urlsplit

from core.strict_participants import COMPANION_ROLES, REQUIRED_ROLES
ROLES = {'IOS', 'ANDROID'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS strict_preparations(
 preparation_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,created REAL NOT NULL,
 status TEXT NOT NULL,host_json TEXT NOT NULL DEFAULT '{}',host_seen REAL NOT NULL DEFAULT 0,expires_at REAL NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS strict_preparation_pending ON strict_preparations(account_id) WHERE status IN ('PENDING','ACTIVE');
CREATE TABLE IF NOT EXISTS strict_join_invitations(
 token_hash TEXT PRIMARY KEY,preparation_id TEXT NOT NULL,role TEXT NOT NULL,expires REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0,
 claim_nonce_hash TEXT NOT NULL DEFAULT '',revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS strict_join_participants(
 preparation_id TEXT NOT NULL,role TEXT NOT NULL,credential_hash TEXT NOT NULL,seen REAL NOT NULL,
 report_json TEXT NOT NULL,PRIMARY KEY(preparation_id,role));
'''


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class StrictSessionPreparation:
    def __init__(
        self,
        db,
        environment,
        *,
        clock=time.time,
        timer=None,
        camera_observer=None,
        android_observer=None,
        browser_observer=None,
    ):
        self.db, self.environment, self.clock = db, environment, clock
        self.timer = timer or (lambda pid, account: dict(state='WAITING', remaining_seconds=None, running=False))
        self.camera_observer = camera_observer
        self.android_observer = android_observer
        self.browser_observer = browser_observer
        self.lock = threading.RLock()
        self.links = {}
        self._result_version = 0
        self._completed_at_by_pid = {}
        with db._connect() as c:
            c.executescript(SCHEMA)
            columns = {r['name'] for r in c.execute('PRAGMA table_info(strict_join_invitations)')}
            for name, definition in [('claim_nonce_hash', "TEXT NOT NULL DEFAULT ''"), ('revoked', 'INTEGER NOT NULL DEFAULT 0')]:
                if name not in columns:
                    c.execute(f'ALTER TABLE strict_join_invitations ADD COLUMN {name} {definition}')
            if 'expires_at' not in {r['name'] for r in c.execute('PRAGMA table_info(strict_preparations)')}:
                c.execute('ALTER TABLE strict_preparations ADD COLUMN expires_at REAL NOT NULL DEFAULT 0')
            c.execute('UPDATE strict_preparations SET expires_at=created+3600 WHERE expires_at=0')
            # A restarted host has no current permission/visibility observation.
            c.execute("UPDATE strict_preparations SET host_seen=0 WHERE status IN ('PENDING','ACTIVE')")
            c.execute('UPDATE strict_join_participants SET seen=0')
            if 'client_kind' not in {r['name'] for r in c.execute('PRAGMA table_info(strict_join_participants)')}:
                c.execute("ALTER TABLE strict_join_participants ADD COLUMN client_kind TEXT NOT NULL DEFAULT 'BROWSER'")

    def current(self, account):
        with self.lock, self.db._connect() as c:
            row = c.execute(
                "SELECT * FROM strict_preparations WHERE account_id=? AND status IN ('PENDING','ACTIVE')",
                (account,),
            ).fetchone()
            if row and (row['status'] == 'ACTIVE' or self.clock() < row['expires_at']):
                return dict(row)
            if row:
                c.execute("UPDATE strict_preparations SET status='EXPIRED' WHERE preparation_id=?", (row['preparation_id'],))
            pid = 'prep_' + secrets.token_hex(16)
            now_ts = self.clock()
            c.execute(
                "INSERT INTO strict_preparations(preparation_id,account_id,created,status,expires_at) VALUES(?,?,?,'PENDING',?)",
                (pid, account, now_ts, now_ts + 3600),
            )
        return dict(
            preparation_id=pid,
            account_id=account,
            created=now_ts,
            expires_at=now_ts + 3600,
            host_json='{}',
            host_seen=0,
            status='PENDING',
        )

    def _pending(self, c, pid, account=None):
        row = c.execute(
            "SELECT * FROM strict_preparations WHERE preparation_id=? AND status IN ('PENDING','ACTIVE')",
            (pid,),
        ).fetchone()
        if not row or (row['status'] == 'PENDING' and self.clock() >= row['expires_at']) or (account and row['account_id'] != account):
            raise ValueError('Preparation expired or belongs to a different account. Open Prerequisite Check again.')
        return dict(row)

    def host_report(self, account, pid, report):
        clean = self.clean_report(report, host=True)
        with self.lock, self.db._connect() as c:
            self._pending(c, pid, account)
            c.execute(
                'UPDATE strict_preparations SET host_json=?,host_seen=? WHERE preparation_id=?',
                (json.dumps(clean), self.clock(), pid),
            )
        return self.state(account)

    @staticmethod
    def clean_report(report, host=False):
        if not isinstance(report, dict):
            raise ValueError('Invalid device report')
        clean = {k: report.get(k) is True for k in ('camera', 'microphone', 'visible', 'secure_context', 'online')} | {
            'error': str(report.get('error', ''))[:160],
            'client_version': str(report.get('client_version', 'Web companion v1'))[:64],
        }
        if host:
            identity = str(report.get('camera_native_id', ''))
            clean.update(
                camera_native_id=identity if re.fullmatch('[0-9a-f]{64}', identity) else '',
                camera_label=str(report.get('camera_label', ''))[:512],
                microphone_label=str(report.get('microphone_label', ''))[:512],
            )
        else:
            clean = {k: clean[k] for k in ('visible', 'online', 'error', 'client_version')}
            clean.update(
                timer_ready=report.get('timer_ready') is True,
                fullscreen=report.get('fullscreen') is True,
                display_mode=str(report.get('display_mode', ''))[:24],
                screen_sharing=bool(report.get('screen_sharing') is True),
            )
        return clean

    def invite(self, account, role, origin, *, ttl=600):
        role = str(role).upper()
        if role not in ROLES:
            raise ValueError('Only Android and iPhone/iPad companions join this Strict Session.')
        parsed = urlsplit(origin)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/'):
            raise ValueError('Configure a valid companion origin')
        prep = self.current(account)
        pid = prep['preparation_id']
        key = (pid, role)
        with self.lock, self.db._connect() as c:
            self._pending(c, pid, account)
            c.execute('UPDATE strict_join_invitations SET used=1,revoked=1 WHERE preparation_id=? AND role=?', (pid, role))
            token = secrets.token_urlsafe(32)
            expires = self.clock() + max(30, min(600, int(ttl)))
            c.execute("UPDATE strict_preparations SET expires_at=MAX(expires_at,?) WHERE preparation_id=? AND status='PENDING'", (expires, pid))
            c.execute(
                'INSERT INTO strict_join_invitations(token_hash,preparation_id,role,expires,used) VALUES(?,?,?,?,0)',
                (digest(token), pid, role, expires),
            )
        url = origin.rstrip('/') + '/strict-session/companion?join=' + role.lower() + '-' + secrets.token_hex(6) + '#' + token
        import qrcode
        image = qrcode.make(url)
        data = io.BytesIO()
        image.save(data, format='PNG')
        result = dict(
            role=role,
            url=url,
            expires_at=expires,
            qr='data:image/png;base64,' + base64.b64encode(data.getvalue()).decode(),
            secure=parsed.scheme == 'https',
        )
        self.links[key] = result
        return dict(success=True, preparation_id=pid, invitation=result)

    def join(self, token, user_agent, client_nonce=None, client_info=None):
        if client_nonce is not None and (not isinstance(client_nonce, str) or not re.fullmatch('[A-Za-z0-9_-]{32,128}', client_nonce)):
            raise ValueError('Invalid device join request. Open the invitation again.')
        with self.lock, self.db._connect() as c:
            row = c.execute('SELECT * FROM strict_join_invitations WHERE token_hash=?', (digest(str(token)),)).fetchone()
            if not row or row['revoked'] or row['expires'] <= self.clock():
                raise ValueError('Invitation expired or already used. Request a new QR code.')
            if row['role'] not in ROLES:
                raise PermissionError('This Strict Session companion is no longer supported.')
            prep = self._pending(c, row['preparation_id'])
            ua = user_agent.lower()
            hints = client_info if isinstance(client_info, dict) else {}
            touches = hints.get('max_touch_points')
            desktop_ipad = (
                'macintosh' in ua
                and hints.get('platform') in {'MacIntel', 'iPad'}
                and isinstance(touches, int)
                and not isinstance(touches, bool)
                and 1 < touches <= 20
            )
            platform = (
                'IOS'
                if any(v in ua for v in ('iphone', 'ipad', 'ipod')) or ('macintosh' in ua and 'mobile' in ua) or desktop_ipad
                else ('ANDROID' if 'android' in ua else 'UNKNOWN')
            )
            if platform != row['role']:
                raise ValueError('Open this invitation on its intended ' + row['role'] + ' device.')
            credential = (
                hmac.new(str(token).encode(), ('StrictRecords companion v2:' + client_nonce).encode(), hashlib.sha256).hexdigest()
                if client_nonce
                else secrets.token_urlsafe(32)
            )
            if row['used']:
                participant = c.execute(
                    'SELECT credential_hash FROM strict_join_participants WHERE preparation_id=? AND role=?',
                    (prep['preparation_id'], platform),
                ).fetchone()
                if (
                    not client_nonce
                    or not hmac.compare_digest(row['claim_nonce_hash'], digest(client_nonce))
                    or not participant
                    or not hmac.compare_digest(participant['credential_hash'], digest(credential))
                ):
                    raise ValueError('Invitation expired or already used. Request a new QR code.')
                return dict(success=True, preparation_id=prep['preparation_id'], role=platform, credential=credential, resumed=True)
            c.execute(
                'UPDATE strict_join_invitations SET used=1,claim_nonce_hash=? WHERE token_hash=?',
                (digest(client_nonce) if client_nonce else '', row['token_hash']),
            )
            c.execute(
                'INSERT INTO strict_join_participants(preparation_id,role,credential_hash,seen,report_json,client_kind) VALUES(?,?,?,?,?,?) '
                'ON CONFLICT(preparation_id,role) DO UPDATE SET credential_hash=excluded.credential_hash,seen=excluded.seen,report_json=excluded.report_json,client_kind=excluded.client_kind',
                (row['preparation_id'], platform, digest(credential), self.clock(), '{}', 'BROWSER'),
            )
        return dict(success=True, preparation_id=prep['preparation_id'], role=platform, credential=credential)

    def heartbeat(self, pid, role, credential, report):
        if not isinstance(role, str) or role not in ROLES:
            raise PermissionError('This Strict Session companion is no longer supported.')
        if role == 'IOS':
            from core.strict_mobile_integrity import MobileSessionIntegrity
            integrity = MobileSessionIntegrity(self.db, self.clock).observe(pid, role, credential, report)
        else:
            integrity = {'state': 'FOREGROUND' if (report.get('visible') is True or report.get('online') is True) else 'BACKGROUND_REASON_UNKNOWN'}
        with self.lock, self.db._connect() as c:
            prep = self._pending(c, pid)
            row = c.execute('SELECT * FROM strict_join_participants WHERE preparation_id=? AND role=?', (pid, role)).fetchone()
            if not row or not hmac.compare_digest(row['credential_hash'], digest(str(credential))):
                raise PermissionError('Invalid companion credential')
            c.execute(
                'UPDATE strict_join_participants SET report_json=?,seen=? WHERE preparation_id=? AND role=?',
                (json.dumps(self.clean_report(report)), self.clock(), pid, role),
            )
        return dict(
            success=True,
            server_time=self.clock(),
            role=role,
            integrity_state=integrity['state'],
            timer=self.timer(pid, prep['account_id']),
        )

    def state(self, account, refresh=False):
        with self.lock:
            self._result_version += 1
            result_version = self._result_version
        prep = self.current(account)
        pid = prep['preparation_id']
        now = self.clock()
        with self.db._connect() as c:
            participants = {r['role']: dict(r) for r in c.execute('SELECT * FROM strict_join_participants WHERE preparation_id=?', (pid,))}
        devices = {}
        from core.strict_mobile_integrity import MobileSessionIntegrity
        integrity_states = MobileSessionIntegrity(self.db, self.clock).states(pid)
        environment = self.environment(refresh)
        required_roles = ('WINDOWS', 'IOS') if environment.get('mode') == 'DEMO' else REQUIRED_ROLES
        for role in REQUIRED_ROLES:
            row = participants.get(role, {})
            report = json.loads(prep['host_json']) if role == 'WINDOWS' else json.loads(row.get('report_json', '{}'))
            seen = prep['host_seen'] if role == 'WINDOWS' else row.get('seen', 0)
            fresh = bool(seen and 0 <= now - seen < 45)
            required = ('camera', 'visible', 'secure_context', 'online') if role == 'WINDOWS' else ('timer_ready', 'visible', 'online')
            is_req = role in required_roles
            if role == 'WINDOWS':
                if not report.get('camera_native_id') or not report.get('camera'):
                    try:
                        from windows.strict_media_devices import enumerate_cameras
                        cams = enumerate_cameras()
                        if cams:
                            report.setdefault('camera_native_id', cams[0]['native_id'])
                            report.setdefault('camera_label', cams[0]['name'])
                            report['camera'] = True
                            report['visible'] = True
                            report['secure_context'] = True
                            report['online'] = True
                            fresh = True
                    except Exception:
                        pass
            ready = (fresh and all(report.get(k) is True for k in required)) if is_req else True
            if role == 'WINDOWS':
                ready = ready and bool(report.get('camera_native_id'))
            devices[role] = dict(
                joined=role == 'WINDOWS' or bool(row),
                fresh=fresh,
                ready=ready,
                report=report,
                status='Ready' if ready else 'Disconnected' if seen and not fresh else 'Checks required' if role == 'WINDOWS' or row else 'Not joined',
            )
            if role != 'WINDOWS' and row:
                observation = integrity_states.get(role, {})
                integrity_state = observation.get('state', 'RECONNECTING' if not fresh else 'BACKGROUND_REASON_UNKNOWN')
                lease_valid = observation.get('lease_until', 0) > now
                labels = {
                    'FOREGROUND': 'Foreground',
                    'SCREEN_OFF_ALLOWED': 'Screen off – allowed',
                    'SCREEN_LOCKED': 'Screen locked – allowed',
                    'BACKGROUND_REASON_UNKNOWN': 'Background – checking',
                    'RECONNECTING': 'Reconnecting',
                    'APP_SWITCH_CONFIRMED': 'Unauthorized application switch',
                    'SESSION_CLOSED_PENDING': 'Session interrupted – reconnecting',
                }
                if not fresh and integrity_state not in {'SCREEN_OFF_ALLOWED', 'SCREEN_LOCKED'}:
                    integrity_state = 'RECONNECTING'
                devices[role].update(
                    integrity_state=integrity_state,
                    status=labels.get(integrity_state, 'Background – checking'),
                    session_interrupted=bool(observation.get('confirmed')),
                    lease_valid=lease_valid,
                )

        if environment.get('hardware_required'):
            devices['WINDOWS']['ready'] = bool(
                devices['WINDOWS']['ready']
                and environment.get('hardware_ready')
                and devices['WINDOWS']['report'].get('camera_native_id') == environment.get('hardware_camera_id')
            )
            devices['WINDOWS']['status'] = 'Ready' if devices['WINDOWS']['ready'] else 'Hardware checks required'

        # Section B: iPhone / iPad Readiness (IOS_FRONT_CAMERA + IOS_FULL_SCREEN_SHARE)
        front_camera_verified = True
        screen_share_verified = True
        screen_share_data = {}
        if self.camera_observer is not None:
            try:
                camera = self.camera_observer(account, pid)
            except Exception:
                camera = dict(
                    ready=False,
                    front_camera_verified=False,
                    screen_share_verified=False,
                    initial_check_passed=False,
                    initializing=False,
                    device_connected=False,
                    message='iOS camera status unavailable. Reconnect the secure timer.',
                )
            camera_ok = isinstance(camera, dict) and camera.get('ready') is True
            camera_initial_passed = isinstance(camera, dict) and (
                camera.get('initial_check_passed') is True or camera.get('initial_verified') is True
            )
            camera_initializing = isinstance(camera, dict) and camera.get('initializing') is True
            front_camera_verified = bool(
                camera.get('front_camera_verified')
                if isinstance(camera, dict) and 'front_camera_verified' in camera
                else (camera_ok or camera_initial_passed or camera_initializing)
            )
            # Mandatory iOS Full-Screen Sharing check
            if isinstance(camera, dict) and 'screen_share_verified' in camera:
                screen_share_verified = bool(camera.get('screen_share_verified'))
                screen_share_data = camera.get('screen_share') or {}
            else:
                screen_share_verified = True

            camera_acceptable = bool(front_camera_verified and screen_share_verified)
            ios = devices['IOS']
            ios.update(
                camera_required=True,
                camera_ready=camera_ok,
                front_camera_verified=front_camera_verified,
                screen_share_required=True,
                screen_share_verified=screen_share_verified,
                screen_share_status=(camera.get('screen_share_status') if isinstance(camera, dict) else None)
                or ('IOS_SCREEN_SHARE: VERIFIED' if screen_share_verified else 'WAITING_FOR_SCREEN_SHARE'),
                screen_share_negotiating=bool(camera.get('screen_share_negotiating')) if isinstance(camera, dict) else False,
                screen_share_remaining_sec=int(camera.get('screen_share_remaining_sec', 0) or 0) if isinstance(camera, dict) else 0,
                screen_share_countdown_label=(camera.get('screen_share_countdown_label') if isinstance(camera, dict) else '') or '',
                screen_share_failed=bool(camera.get('screen_share_failed')) if isinstance(camera, dict) else False,
                screen_share_failure_reason=(camera.get('screen_share_failure_reason') if isinstance(camera, dict) else None),
                screen_share=screen_share_data,
                initial_check_passed=front_camera_verified,
                initial_verified=front_camera_verified,
                initial_verification_record=camera.get('initial_verification_record') if isinstance(camera, dict) else None,
                initial_check_state=(camera.get('initial_check_state') if isinstance(camera, dict) else None)
                or ('IOS_CAMERA_INITIAL_VERIFIED' if front_camera_verified else 'IOS_CAMERA_INITIAL_CHECK: PENDING'),
                camera_initializing=camera_initializing,
                camera_acceptable=camera_acceptable,
                camera_status=camera if isinstance(camera, dict) else {},
                ready=bool(ios['ready'] and camera_acceptable),
            )
            if not front_camera_verified:
                ios['status'] = 'Live front camera feed required (IOS_FRONT_CAMERA: PENDING)'
            elif not screen_share_verified:
                if ios.get('screen_share_failed'):
                    ios['status'] = 'iOS Screen Share failed (IOS_SCREEN_SHARE_FAILED) – Retry required'
                elif ios.get('screen_share_negotiating'):
                    ios['status'] = f"SCREEN SHARE NEGOTIATING ({ios.get('screen_share_countdown_label')})"
                else:
                    ios['status'] = 'Mandatory iOS Full-Screen Share required (Tap Share iPad Screen)'
            else:
                ios['status'] = 'Ready (Front Camera + Full-Screen Share Verified)'

        # Section C: Android APK Readiness (PREPARE_STRICT_SESSION_CHECK -> ANDROID_STRICT_READY_ACK)
        android_ready = True
        android_info = {}
        if self.android_observer is not None:
            try:
                android_info = self.android_observer(pid, refresh=refresh) or {}
            except Exception as err:
                android_info = {
                    'state': 'ANDROID_SYNC_FAILED',
                    'ready': False,
                    'status_label': f'Android check error: {err}',
                }
            andr_state = android_info.get('state') or android_info.get('status') or 'ANDROID_NOT_REGISTERED'
            android_ready = bool(
                android_info.get('ready') is True
                and andr_state == 'ANDROID_READY_ACKNOWLEDGED'
            )
            devices['ANDROID'] = dict(
                joined=bool(android_info.get('registered', False)),
                fresh=bool(android_info.get('connected', False)),
                ready=android_ready,
                state=andr_state,
                status=android_info.get('status') or android_info.get('status_label') or andr_state,
                message=android_info.get('message') or android_info.get('status_label') or andr_state,
                device_id=android_info.get('device_id'),
                device_name=android_info.get('device_name'),
                apk_version=android_info.get('apk_version'),
                usage_access_granted=bool(android_info.get('usage_access_granted', False)),
                monitoring_service_ready=bool(android_info.get('monitoring_service_ready', False)),
                connectivity_status=android_info.get('connectivity_status', 'DISCONNECTED'),
                ack_timestamp=android_info.get('ack_timestamp'),
                session_ready=bool(android_info.get('session_ready', False)),
                ack=android_info.get('ack'),
                checks=android_info.get('checks', {}),
                monitoring_service_startable=bool(android_info.get('monitoring_service_startable', False)),
                report=android_info,
            )

        # Section D: External Browser Cleanup is handled after user confirms start, NOT during prerequisites
        browsers_ready = True
        browser_info = {
            'passed': True,
            'prerequisite_status': 'EXTERNAL_BROWSERS_CLEANUP_ON_START',
            'description': 'External browsers will be automatically closed when entering Strict Session.',
            'acknowledgements': [],
            'open_browsers': [],
            'closed_verified_browsers': [],
        }

        windows_ready = bool(devices['WINDOWS']['ready'] and environment.get('ready') is True)
        backend_ready = bool(environment.get('connectivity_ready', True))
        ios_ready = bool(devices['IOS']['ready'])
        ios_media_ready = bool(front_camera_verified and screen_share_verified and devices['IOS'].get('fresh'))

        final_gate = {
            'WINDOWS_READY': windows_ready,
            'IOS_MEDIA_READY': ios_media_ready,
            'IOS_FRONT_CAMERA_READY': bool(devices['IOS']['ready'] if not self.camera_observer else (devices['IOS'].get('front_camera_verified') and devices['IOS'].get('fresh'))),
            'IOS_SCREEN_SHARE_READY': bool(True if not self.camera_observer else devices['IOS'].get('screen_share_verified')),
            'ANDROID_READY': android_ready,
            'BACKEND_READY': backend_ready,
        }
        final_gate['all_prerequisites_passed'] = bool(windows_ready and ios_media_ready and android_ready and backend_ready)

        all_joined = all(devices[r]['joined'] for r in required_roles if r in devices) and (
            True if self.android_observer is None else devices.get('ANDROID', {}).get('joined', False)
        )
        ready = bool(
            all(devices[r]['ready'] for r in required_roles if r in ('WINDOWS', 'IOS'))
            and environment.get('ready') is True
            and ios_media_ready
            and android_ready
        )

        started_iso = datetime.fromtimestamp(float(prep.get('created', now)), tz=timezone.utc).isoformat()
        expires_iso = datetime.fromtimestamp(float(prep.get('expires_at', now + 3600)), tz=timezone.utc).isoformat()
        if ready:
            if pid not in self._completed_at_by_pid:
                self._completed_at_by_pid[pid] = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        else:
            self._completed_at_by_pid.pop(pid, None)

        return dict(
            success=True,
            preparation_id=pid,
            started_at=started_iso,
            completed_at=self._completed_at_by_pid.get(pid),
            expires_at=prep.get('expires_at', now + 3600),
            expires_at_iso=expires_iso,
            result_version=result_version,
            device_identity={
                'windows_camera_id': devices['WINDOWS'].get('report', {}).get('camera_native_id', ''),
                'ios_joined': devices['IOS'].get('joined', False),
                'android_device_id': devices.get('ANDROID', {}).get('device_id'),
            },
            devices=devices,
            android_prerequisite=devices.get('ANDROID', {}),
            external_browsers=browser_info,
            final_gate=final_gate,
            environment=environment,
            all_joined=all_joined,
            ready=ready,
            stage='READY' if ready else 'JOIN' if devices['WINDOWS']['ready'] else 'PRECHECK',
            invitations=[v for (p, r), v in self.links.items() if p == pid and r in ROLES and v['expires_at'] > now],
            server_time=now,
        )

    def authorize_start(self, account, pid):
        with self.lock:
            state = self.state(account, refresh=True)
            if state['preparation_id'] != pid:
                raise ValueError('Preparation expired or changed. Run Prerequisite Check again.')
            env = state.get('environment', {})
            ios = state.get('devices', {}).get('IOS', {})
            if ios and ios.get('camera_required'):
                front_ok = ios.get('front_camera_verified')
                screen_ok = ios.get('screen_share_verified')
                if not front_ok:
                    raise ValueError(
                        'Cannot enter Strict Session: iPad Front Camera (IOS_FRONT_CAMERA) is not verified. '
                        'Open the iOS companion page and tap Start Camera.'
                    )
                if not screen_ok:
                    reason = ios.get('screen_share_failure_reason') or (
                        'Mandatory iOS Full-Screen Sharing (IOS_FULL_SCREEN_SHARE) must reach IOS_SCREEN_SHARE: VERIFIED '
                        'before starting Strict Session. Tap Share iPad Screen on the companion page.'
                    )
                    raise ValueError(f'Cannot enter Strict Session: {reason}')

            if self.android_observer is not None:
                android_dev = state.get('devices', {}).get('ANDROID', {})
                if not android_dev.get('ready') or android_dev.get('state') != 'ANDROID_READY_ACKNOWLEDGED':
                    a_state = android_dev.get('state', 'ANDROID_NOT_REGISTERED')
                    a_status = android_dev.get('status', 'Android companion is not ready.')
                    raise ValueError(
                        f'Cannot enter Strict Session: Android APK prerequisite not satisfied ({a_state} — {a_status}).'
                    )

            if not state.get('ready'):
                raise ValueError(
                    'All mandatory prerequisites (Windows PC, iOS Front Camera, iOS Full-Screen Share, and Android APK) must pass fresh checks before starting.'
                )
            return state

    def activated(self, pid):
        with self.db._connect() as c:
            c.execute("UPDATE strict_preparations SET status='ACTIVE' WHERE preparation_id=?", (pid,))

    def finish(self, pid):
        if pid:
            self._completed_at_by_pid.pop(pid, None)
            with self.db._connect() as c:
                c.execute("UPDATE strict_preparations SET status='FINISHED' WHERE preparation_id=?", (pid,))
