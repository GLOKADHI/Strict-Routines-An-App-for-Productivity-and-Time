"""Windows Google identity and Drive backup; the transactional ledger stays separate."""
import json
import threading
from pathlib import Path


def ensure_configuration(db):
    with db._connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT)')


class ClientAccountConnection:
    def __init__(self, db, auth=None, sync=None):
        self.db = db
        ensure_configuration(db)
        if auth is None:
            from core.google_auth import GoogleAuthConfig, GoogleAuthManager, runtime_google_auth_dir
            folder = Path(runtime_google_auth_dir(db.db_path))
            auth = GoogleAuthManager(GoogleAuthConfig(str(folder / 'google_client_config.json')),
                session_file=str(folder / 'google_session.enc'), folder_registry_file=str(folder / 'google_drive_folders.json'))
        self.auth = auth
        if sync is None:
            from core.drive_backup import DriveBackupCoordinator
            sync = DriveBackupCoordinator(db, auth_manager=auth)
        self.sync = sync
        self._lock = threading.Lock()
        self._busy = False
        self._last_error = None

    def _link_key(self, account):
        return 'google_identity_binding:' + account

    def _linked_subject(self, account):
        with self.db._connect() as conn:
            row = conn.execute('SELECT value FROM system_config WHERE key=?', (self._link_key(account),)).fetchone()
        return row[0] if row else ''

    def status(self, account):
        auth = self.auth.get_detailed_connection_status()
        user = auth.get('user') or {}
        subject=str(user.get('sub') or '')
        with self.db._connect() as conn:
            existing=conn.execute("SELECT key FROM system_config WHERE key LIKE 'google_identity_binding:%' AND value=?",(subject,)).fetchall() if subject else []
        if any(row[0]!=self._link_key(account) for row in existing):
            return dict(success=True,account_id=account,configured=bool(auth.get('configured')),
                authenticated=False,user={},account_linked=False,google_state='Account isolated',
                drive_state='Not connected for this account',sync_state='Account link required',
                busy=self._busy,message='Sign in with the Google identity linked to this StrictRecords account.')
        public = {name: user.get(name) for name in ('sub', 'email', 'name', 'picture') if user.get(name)}
        return dict(success=True, account_id=account,
            configured=bool(auth.get('configured')), authenticated=bool(auth.get('is_authenticated')),
            google_state=auth.get('account_status'), drive_state=auth.get('drive_status'),
            sync_state=auth.get('sync_status'), user=public,
            root_folder_id=auth.get('root_folder_id'), busy=self._busy or bool(auth.get('in_progress')),
            account_linked=bool(public.get('sub') and public['sub'] == self._linked_subject(account)),
            error=self._last_error or (auth.get('error') or {}).get('message'),
            queue=self.sync.get_status(),
            ledger_transport='TRANSACTIONAL_BACKEND', drive_transport='FILES_AND_BACKUPS',
            message='Drive files cannot approve proofs, award points, or replace the authoritative ledger.')

    def _require_link(self, account):
        subject = self.auth.get_account_sub()
        if not subject or self._linked_subject(account) != subject:
            raise PermissionError('Confirm this Google account for the current StrictRecords account before syncing.')

    def action(self, account, action, body):
        if action == 'import-config':
            self.auth.config.import_document(body.get('document'))
            return self.status(account)
        if action == 'link-account':
            if body.get('confirmed') is not True or not self.auth.is_authenticated():
                raise PermissionError('Sign in, then explicitly confirm the existing account link.')
            with self.db._connect() as conn:
                previous=conn.execute("SELECT key FROM system_config WHERE key LIKE 'google_identity_binding:%' AND value=?",(self.auth.get_account_sub(),)).fetchall()
                if any(row[0]!=self._link_key(account) for row in previous):
                    raise PermissionError('This Google identity is already linked to another local account. Reconcile it before switching.')
                conn.execute('INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                             (self._link_key(account), self.auth.get_account_sub()))
            return self.status(account)
        if action == 'sign-out':
            if self._busy:
                raise ValueError('Wait for the current connection operation to finish.')
            session=self.auth.get_session() or {}
            revocation_token=session.get('refresh_token') or session.get('access_token')
            self._last_error=None
            try:
                if revocation_token:
                    import requests
                    response=requests.post('https://oauth2.googleapis.com/revoke',
                        data={'token':revocation_token},timeout=10,allow_redirects=False)
                    if response.status_code not in (200,400):
                        self._last_error='Signed out locally. Google token revocation was not confirmed; review app access in your Google account.'
            except Exception:
                self._last_error='Signed out locally. Google token revocation could not be reached; review app access in your Google account.'
            finally:
                self.auth.sign_out()
            return self.status(account)
        if action not in {'sign-in', 'grant-drive', 'verify-drive', 'sync', 'select-folder'}:
            raise ValueError('Unknown account action')
        if action in {'sync', 'select-folder'}:
            self._require_link(account)
        with self._lock:
            if self._busy:
                raise ValueError('A connection operation is already running.')
            self._busy = True
            self._last_error = None

        def work():
            try:
                if action == 'sign-in':
                    self.auth.start_pkce_flow(include_drive_scope=False, force_account_select=True)
                elif action == 'grant-drive':
                    self.auth.grant_drive_access(drive_verifier=self.sync.verify_drive_access)
                elif action == 'verify-drive':
                    result = self.sync.verify_drive_access()
                    if result.get('success') is not True:
                        raise ValueError(result.get('error_message') or 'Drive read/write verification failed.')
                elif action == 'select-folder':
                    result=self.sync.select_shared_root_folder(str(body.get('folder_id') or '').strip())
                    if result.get('success') is not True:
                        raise ValueError('Selected folder did not pass verification')
                else:
                    if not self.auth.is_drive_connected():
                        raise PermissionError('Grant and verify Drive access before starting backup.')
                    result = self.sync.sync_now()
                    if result.get('success') is not True:
                        raise ValueError(result.get('error') or 'Backup did not complete.')
                    session=self.auth.get_session() or {}
                    session['sync_enabled']=True
                    self.auth.save_session(session)
                    self.sync.start()
            except Exception as error:
                self._last_error = f'{action} did not complete ({type(error).__name__}). Check the account and Drive permission state, then retry.'
            finally:
                with self._lock:
                    self._busy = False

        threading.Thread(target=work, name='AccountConnection', daemon=True).start()
        return dict(success=True, pending=True)
