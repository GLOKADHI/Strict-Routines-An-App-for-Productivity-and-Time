"""Short-lived, registered-companion camera authority; no media or score storage.

The HTTPS gateway must validate its public certificate before enabling this store.
Only the trusted native receiver may call ``receiver_frame*``. Browser diagnostics
and signaling alone cannot assert that decoded video reached Windows.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import threading
import time
from urllib.parse import quote, urlsplit


STATES = frozenset({
    'IOS_INVITE_CREATED', 'IOS_INVITE_OPENED', 'IOS_INVITE_EXPIRED',
    'IOS_INVITE_REDEEMED', 'IOS_DEVICE_AUTHENTICATED',
    'IOS_CAMERA_PERMISSION_PENDING', 'IOS_CAMERA_PERMISSION_GRANTED',
    'IOS_CAMERA_INITIALIZING', 'IOS_WEBRTC_NEGOTIATING', 'IOS_VIDEO_CONNECTING',
    'IOS_CAMERA_STREAMING', 'IOS_CAMERA_INTERRUPTED', 'IOS_CAMERA_STOPPED',
    'IOS_CAMERA_REVOKED', 'IOS_RECONNECTING',
})
CLIENT_STATES = STATES - {'IOS_CAMERA_STREAMING', 'IOS_INVITE_CREATED',
    'IOS_INVITE_OPENED', 'IOS_INVITE_EXPIRED', 'IOS_INVITE_REDEEMED',
    'IOS_DEVICE_AUTHENTICATED'}
ERROR_NAMES = frozenset({'NotAllowedError', 'NotFoundError', 'NotReadableError',
    'OverconstrainedError', 'SecurityError', 'TypeError', 'NetworkError',
    'AbortError', 'InvalidStateError', 'NotSupportedError'})
SCHEMA = '''
CREATE TABLE IF NOT EXISTS strict_ios_camera_invites(
 invitation_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
 account_id TEXT NOT NULL, preparation_id TEXT NOT NULL, session_id TEXT NOT NULL,
 participant_hash TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
 redeemed_at REAL, revoked INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'IOS_INVITE_CREATED');
CREATE TABLE IF NOT EXISTS strict_ios_camera_bindings(
 camera_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, preparation_id TEXT NOT NULL,
 session_id TEXT NOT NULL, participant_hash TEXT NOT NULL,
 credential_hash TEXT NOT NULL UNIQUE, created_at REAL NOT NULL,
 expires_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL, diagnostics_json TEXT NOT NULL DEFAULT '{}',
 last_frame_at REAL, frame_sequence INTEGER NOT NULL DEFAULT 0,
 updated_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS strict_ios_camera_scope
 ON strict_ios_camera_bindings(account_id, preparation_id, session_id);
CREATE TABLE IF NOT EXISTS strict_ios_camera_limits(
 bucket TEXT PRIMARY KEY, window_start REAL NOT NULL, count INTEGER NOT NULL);
'''


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _secret(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', value):
        raise PermissionError('Invalid camera authorization.')
    return value


def trusted_origin(origin: str) -> str:
    """Validate origin shape, not certificate trust or domain ownership."""
    if not isinstance(origin, str):
        raise ValueError('Configure a trusted HTTPS camera origin.')
    try:
        if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in origin):
            raise ValueError
        p = urlsplit(origin)
        port = p.port
        host = p.hostname or ''
        if (p.scheme != 'https' or not host or p.username or p.password
                or p.path not in ('', '/') or p.query or p.fragment
                or host.lower() in {'localhost', 'localhost.localdomain'}
                or host.lower().endswith(('.local', '.localhost'))):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        host = host.encode('idna').decode('ascii').lower()
        if '.' not in host or not all(re.fullmatch(r'(?!-)[a-z0-9-]{1,63}(?<!-)', s) for s in host.split('.')):
            raise ValueError
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        return 'https://' + host + (':' + str(port) if port not in (None, 443) else '')
    except (ValueError, UnicodeError):
        raise ValueError('Camera invitations require a trusted HTTPS DNS origin; LAN IPs and self-signed setups are unsupported.') from None


class StrictIOSCamera:
    def __init__(self, db, *, clock=time.time, session_validator=None,
                 invitation_ttl=180, credential_ttl=600):
        self.db, self.clock = db, clock
        self.session_validator = session_validator
        self.invitation_ttl = max(30, min(300, int(invitation_ttl)))
        self.credential_ttl = max(60, min(900, int(credential_ttl)))
        self.lock = threading.RLock()
        self.enabled = False
        self.origin = ''
        with db._connect() as c:
            c.executescript(SCHEMA)
            # A process restart cannot establish that a previous decoder is live.
            c.execute("UPDATE strict_ios_camera_bindings SET state='IOS_CAMERA_INTERRUPTED' WHERE revoked=0 AND state='IOS_CAMERA_STREAMING'")

    def configure(self, origin, *, enabled=False, tls_verified=False):
        """Enable only after gateway verifies default-CA trust, SAN and expiry.

        ``tls_verified`` is a trusted server-side result, never a client input.
        """
        normalized = trusted_origin(origin)
        with self.lock:
            self.origin = normalized
            self.enabled = bool(enabled and tls_verified)
        return dict(enabled=self.enabled, origin=self.origin,
                    reason='' if self.enabled else 'Trusted TLS configuration has not been verified.')

    def _configured(self):
        if not self.enabled or not self.origin:
            raise PermissionError('iPhone camera is disabled until trusted HTTPS and secure signaling are configured.')

    def _rate(self, bucket, *, limit=10, seconds=60):
        # Independent commit intentionally counts failed redemption attempts too.
        now = self.clock()
        with self.lock, self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM strict_ios_camera_limits WHERE window_start<?', (now - 3600,))
            key = _hash(bucket)
            row = c.execute('SELECT * FROM strict_ios_camera_limits WHERE bucket=?', (key,)).fetchone()
            start, count = (row['window_start'], row['count']) if row else (now, 0)
            if now < start or now - start >= seconds:
                start, count = now, 0
            c.execute('INSERT INTO strict_ios_camera_limits VALUES(?,?,?) ON CONFLICT(bucket) DO UPDATE SET window_start=excluded.window_start,count=excluded.count',
                      (key, start, count + 1))
        if count >= limit:
            raise PermissionError('Too many camera requests. Wait before retrying.')

    def _scope(self, c, account, pid, sid, expected_participant=None):
        p = c.execute("SELECT * FROM strict_preparations WHERE preparation_id=? AND account_id=? AND status IN ('PENDING','ACTIVE')", (pid, account)).fetchone()
        if not p or (p['status'] == 'PENDING' and p['expires_at'] <= self.clock()):
            raise PermissionError('Camera session is no longer active.')
        if not isinstance(sid, str) or not sid or len(sid) > 128:
            raise PermissionError('An active Windows Strict Session is required.')
        if self.session_validator is None or not self.session_validator(account, pid, sid):
            raise PermissionError('Camera session does not match the active Windows Strict Session.')
        participant = c.execute("SELECT credential_hash FROM strict_join_participants WHERE preparation_id=? AND role='IOS'", (pid,)).fetchone()
        if not participant or not re.fullmatch('[0-9a-f]{64}', participant['credential_hash']):
            raise PermissionError('Join the registered iPhone/iPad companion before enabling its camera.')
        ph = participant['credential_hash']
        if expected_participant is not None and not hmac.compare_digest(ph, expected_participant):
            raise PermissionError('The registered iOS companion changed. Create a new camera invitation.')
        return ph

    def invite(self, account, pid, session_id, origin=None):
        self._configured()
        if origin is not None and trusted_origin(origin) != self.origin:
            raise PermissionError('Camera origin does not match the verified HTTPS configuration.')
        self._rate('invite:' + str(account), limit=6)
        now = self.clock()
        token, iid = secrets.token_urlsafe(32), 'iosinv_' + secrets.token_hex(16)
        with self.lock, self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            participant = self._scope(c, account, pid, session_id)
            c.execute('UPDATE strict_ios_camera_invites SET revoked=1 WHERE preparation_id=? AND redeemed_at IS NULL', (pid,))
            c.execute('INSERT INTO strict_ios_camera_invites(invitation_id,token_hash,account_id,preparation_id,session_id,participant_hash,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)',
                      (iid, _hash(token), account, pid, session_id, participant, now, now + self.invitation_ttl))
        return dict(success=True, preparation_id=pid, session_id=session_id,
                    invitation=dict(invitation_id=iid,
                        url=self.origin + '/ios-camera?preparation_id=' + quote(pid, safe='') + '#' + token,
                        expires_at=now + self.invitation_ttl, secure=True,
                        state='IOS_INVITE_CREATED'))

    def opened(self, token):
        """Optional metadata observation; never redeems or authenticates a device."""
        self._configured()
        token = _secret(token)
        with self.lock, self.db._connect() as c:
            row = c.execute('SELECT * FROM strict_ios_camera_invites WHERE token_hash=?', (_hash(token),)).fetchone()
            if not row or row['revoked'] or row['redeemed_at'] is not None or row['expires_at'] <= self.clock():
                raise PermissionError('Camera invitation expired, revoked or already redeemed.')
            c.execute("UPDATE strict_ios_camera_invites SET state='IOS_INVITE_OPENED' WHERE invitation_id=?", (row['invitation_id'],))
        return dict(success=True, state='IOS_INVITE_OPENED', expires_at=row['expires_at'])

    def redeem(self, token, pid, companion_credential):
        self._configured()
        self._rate('redeem:' + str(pid))
        token, companion_credential = _secret(token), _secret(companion_credential)
        now = self.clock()
        credential, camera_id = secrets.token_urlsafe(32), 'ioscam_' + secrets.token_hex(16)
        with self.lock, self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM strict_ios_camera_invites WHERE token_hash=?', (_hash(token),)).fetchone()
            if not row or row['preparation_id'] != pid or row['revoked'] or row['redeemed_at'] is not None or row['expires_at'] <= now:
                raise PermissionError('Camera invitation expired, revoked or already redeemed.')
            self._scope(c, row['account_id'], pid, row['session_id'], row['participant_hash'])
            if not hmac.compare_digest(row['participant_hash'], _hash(companion_credential)):
                raise PermissionError('Authenticate with the registered iOS companion before camera pairing.')
            changed = c.execute("UPDATE strict_ios_camera_invites SET redeemed_at=?,state='IOS_INVITE_REDEEMED' WHERE invitation_id=? AND redeemed_at IS NULL AND revoked=0 AND expires_at>?", (now, row['invitation_id'], now)).rowcount
            if changed != 1:
                raise PermissionError('Camera invitation already redeemed.')
            c.execute("UPDATE strict_ios_camera_bindings SET revoked=1,state='IOS_CAMERA_REVOKED',updated_at=? WHERE preparation_id=? AND revoked=0", (now, pid))
            c.execute('INSERT INTO strict_ios_camera_bindings(camera_id,account_id,preparation_id,session_id,participant_hash,credential_hash,created_at,expires_at,state,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (camera_id, row['account_id'], pid, row['session_id'], row['participant_hash'], _hash(credential), now, now + self.credential_ttl, 'IOS_DEVICE_AUTHENTICATED', now))
        return dict(success=True, camera_id=camera_id, preparation_id=pid,
                    session_id=row['session_id'], credential=credential,
                    expires_at=now + self.credential_ttl, state='IOS_DEVICE_AUTHENTICATED')

    def _binding(self, c, credential=None, camera_id=None):
        if credential is not None:
            credential = _secret(credential)
            row = c.execute('SELECT * FROM strict_ios_camera_bindings WHERE credential_hash=?', (_hash(credential),)).fetchone()
        else:
            row = c.execute('SELECT * FROM strict_ios_camera_bindings WHERE camera_id=?', (camera_id,)).fetchone()
        if not row or row['revoked'] or row['expires_at'] <= self.clock():
            raise PermissionError('Camera authorization expired or was revoked.')
        self._scope(c, row['account_id'], row['preparation_id'], row['session_id'], row['participant_hash'])
        return row

    @staticmethod
    def _identity(row):
        return {key: row[key] for key in ('camera_id', 'account_id', 'preparation_id', 'session_id', 'expires_at', 'state', 'frame_sequence')}

    def authenticate(self, camera_credential):
        self._configured()
        with self.lock, self.db._connect() as c:
            return self._identity(self._binding(c, credential=camera_credential))

    def receiver_identity(self, camera_id):
        """Trusted server watchdog only; never authorize a remote client by ID."""
        self._configured()
        with self.lock, self.db._connect() as c:
            return self._identity(self._binding(c, camera_id=camera_id))

    def rotate(self, camera_credential):
        self._configured()
        credential = secrets.token_urlsafe(32)
        with self.lock, self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = self._binding(c, credential=camera_credential)
            expiry = self.clock() + self.credential_ttl
            c.execute('UPDATE strict_ios_camera_bindings SET credential_hash=?,expires_at=?,updated_at=? WHERE camera_id=?', (_hash(credential), expiry, self.clock(), row['camera_id']))
        return dict(success=True, **{**self._identity(row), 'expires_at': expiry}, credential=credential)

    @staticmethod
    def _diagnostics(data):
        if not isinstance(data, dict):
            raise ValueError('Invalid camera diagnostics.')
        clean = {key: data[key] for key in ('secure_context', 'media_devices', 'get_user_media', 'visible', 'audio_tracks_present') if isinstance(data.get(key), bool)}
        enums = {
            'protocol': {'https:', 'http:'},
            'permission': {'granted', 'denied', 'prompt', 'unsupported', 'unknown'},
            'api': {'connected', 'failed', 'pending'},
            'signaling': {'connected', 'failed', 'connecting', 'closed'},
            'peer': {'new', 'connecting', 'connected', 'disconnected', 'failed', 'closed'},
            'track': {'live', 'ended', 'muted', 'unavailable'},
            'error_name': ERROR_NAMES,
        }
        for key, allowed in enums.items():
            if data.get(key) in allowed:
                clean[key] = data[key]
        return clean

    def client_state(self, camera_credential, state, diagnostics=None):
        self._configured()
        if state not in CLIENT_STATES:
            raise ValueError('Camera clients cannot assert received video or invitation authority.')
        clean = self._diagnostics(diagnostics or {})
        if clean.get('audio_tracks_present'):
            state = 'IOS_CAMERA_REVOKED'
        with self.lock, self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = self._binding(c, credential=camera_credential)
            revoked = state == 'IOS_CAMERA_REVOKED'
            c.execute('UPDATE strict_ios_camera_bindings SET state=?,diagnostics_json=?,updated_at=?,revoked=? WHERE camera_id=?', (state, json.dumps(clean, sort_keys=True), self.clock(), int(revoked), row['camera_id']))
        return dict(success=True, camera_id=row['camera_id'], state=state)

    def receiver_frame(self, camera_credential, frame_sequence):
        """Called only after native Windows receiver decodes a video frame."""
        self._configured()
        with self.lock, self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = self._binding(c, credential=camera_credential)
            return self._record_frame(c, row, frame_sequence)

    def receiver_frame_by_id(self, camera_id, frame_sequence):
        """Internal receiver capability; never expose this method over an API."""
        self._configured()
        with self.lock, self.db._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = self._binding(c, camera_id=camera_id)
            return self._record_frame(c, row, frame_sequence)

    def _record_frame(self, c, row, sequence):
        if row['state'] not in {'IOS_DEVICE_AUTHENTICATED', 'IOS_CAMERA_PERMISSION_GRANTED',
                'IOS_CAMERA_INITIALIZING', 'IOS_WEBRTC_NEGOTIATING', 'IOS_VIDEO_CONNECTING',
                'IOS_CAMERA_STREAMING', 'IOS_CAMERA_INTERRUPTED', 'IOS_RECONNECTING'}:
            raise ValueError('Camera capture is not accepting media in its current state.')
        if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 < sequence <= 2**53:
            raise ValueError('Invalid receiver frame sequence.')
        if sequence <= row['frame_sequence']:
            raise ValueError('Receiver frame sequence must advance.')
        c.execute("UPDATE strict_ios_camera_bindings SET state='IOS_CAMERA_STREAMING',last_frame_at=?,frame_sequence=?,updated_at=? WHERE camera_id=?", (self.clock(), sequence, self.clock(), row['camera_id']))
        return dict(success=True, camera_id=row['camera_id'], state='IOS_CAMERA_STREAMING', last_frame_at=self.clock(), frame_sequence=sequence)

    def status(self, account, pid):
        now = self.clock()
        result = dict(success=True, enabled=self.enabled, state='IOS_CAMERA_STOPPED',
                      streaming=False, last_frame_at=None, frame_sequence=0)
        with self.lock, self.db._connect() as c:
            p = c.execute('SELECT account_id FROM strict_preparations WHERE preparation_id=?', (pid,)).fetchone()
            if not p or p['account_id'] != account:
                raise PermissionError('Camera preparation belongs to another account.')
            row = c.execute('SELECT * FROM strict_ios_camera_bindings WHERE account_id=? AND preparation_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (account, pid)).fetchone()
            invitation = c.execute('SELECT * FROM strict_ios_camera_invites WHERE account_id=? AND preparation_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (account, pid)).fetchone()
            if row:
                result.update(self._identity(row), last_frame_at=row['last_frame_at'], frame_sequence=row['frame_sequence'], diagnostics=json.loads(row['diagnostics_json']))
                try:
                    self._binding(c, camera_id=row['camera_id'])
                except PermissionError:
                    result['state'] = 'IOS_CAMERA_REVOKED'
                else:
                    if row['state'] == 'IOS_CAMERA_STREAMING':
                        # Use 15.0s active watchdog threshold; do not falsely classify as interrupted if frames arrived recently
                        fresh_frame = bool(row['last_frame_at'] is not None and 0 <= (now - row['last_frame_at']) < 15.0)
                        result['streaming'] = fresh_frame
                        if not fresh_frame:
                            result['state'] = 'IOS_CAMERA_INTERRUPTED'
            elif invitation:
                result['state'] = ('IOS_CAMERA_REVOKED' if invitation['revoked'] else 'IOS_INVITE_EXPIRED' if invitation['expires_at'] <= now else invitation['state'])
            if invitation:
                result['invitation'] = {key: invitation[key] for key in ('invitation_id', 'expires_at', 'redeemed_at', 'revoked')}
                result['invitation']['state'] = ('IOS_CAMERA_REVOKED' if invitation['revoked'] else
                    'IOS_INVITE_EXPIRED' if invitation['redeemed_at'] is None and invitation['expires_at'] <= now else invitation['state'])
        return result

    def stop_session(self, session_id=None, *, preparation_id=None):
        if not session_id and not preparation_id:
            raise ValueError('Session identity is required for camera revocation.')
        field, value = ('session_id', session_id) if session_id else ('preparation_id', preparation_id)
        with self.lock, self.db._connect() as c:
            c.execute(f'UPDATE strict_ios_camera_invites SET revoked=1 WHERE {field}=?', (value,))
            c.execute(f"UPDATE strict_ios_camera_bindings SET revoked=1,state='IOS_CAMERA_REVOKED',updated_at=? WHERE {field}=?", (self.clock(), value))
        return dict(success=True, state='IOS_CAMERA_REVOKED')
