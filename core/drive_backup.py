"""Account-scoped Drive file backup over the existing durable upload queue.

Drive is never an authority for points, approvals, or schedule replacement.
"""
import hashlib
import json
from pathlib import Path
import threading
from datetime import datetime, timezone

from core.account_scope import filter_account_score_events
from core.google_drive import GoogleDriveClient


class DriveBackupCoordinator:
    def __init__(self, db, auth_manager):
        self.db, self.auth_manager = db, auth_manager
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._status = {'status':'NOT_INITIALIZED','last_successful_sync':None,'pending_uploads':0,'last_error':None}
        self.drive_client = GoogleDriveClient(auth_manager.get_valid_access_token,
            root_folder_id_getter=auth_manager.get_registered_root_folder_id,
            root_folder_id_setter=auth_manager.set_registered_root_folder_id,
            expected_email_getter=lambda:str(auth_manager.get_user_profile().get('email') or ''))
        auth_manager.on_account_switched = self._account_switched
        with db._connect() as conn:
            columns={row[1] for row in conn.execute('PRAGMA table_info(cloud_sync_files)')}
            for name in ('google_sub','local_account_id'):
                if name not in columns:
                    conn.execute(f'ALTER TABLE cloud_sync_files ADD COLUMN {name} TEXT')

    def _account_switched(self, *_):
        self.drive_client.clear_cache()
        self._status.update(status='ACCOUNT_LINK_REQUIRED',last_successful_sync=None)

    def _binding(self):
        sub=self.auth_manager.get_account_sub()
        if not self.auth_manager.is_authenticated() or not sub:
            raise PermissionError('Google account is not connected')
        with self.db._connect() as conn:
            links=conn.execute("SELECT key FROM system_config WHERE key LIKE 'google_identity_binding:%' AND value=?",(sub,)).fetchall()
        if len(links)!=1:
            raise PermissionError('Confirm exactly one existing account for this Google identity')
        return links[0][0].split(':',1)[1],sub

    def get_status(self):
        result=dict(self._status)
        try:
            account,sub=self._binding()
            with self.db._connect() as conn:
                rows=conn.execute('''SELECT q.status,COUNT(*) FROM cloud_sync_queue q
                    JOIN cloud_sync_files f ON f.file_id=q.file_id
                    WHERE f.google_sub=? AND f.local_account_id=? GROUP BY q.status''',(sub,account)).fetchall()
            result['queue']={row[0]:row[1] for row in rows}
            result['pending_uploads']=sum(count for key,count in rows if key!='COMPLETED')
        except PermissionError:
            result['status']='ACCOUNT_LINK_REQUIRED'
        return result

    def verify_drive_access(self):
        result=self.drive_client.verify_drive_connection()
        if result.get('success') is True:
            session=self.auth_manager.get_session() or {}
            session.update(drive_verified=True,root_folder_id=self.auth_manager.get_registered_root_folder_id())
            self.auth_manager.save_session(session)
        return result

    def select_shared_root_folder(self, folder):
        self._binding()
        self.drive_client.set_registered_root_folder_id(folder,expected_owner_sub=self.auth_manager.get_account_sub())
        return self.verify_drive_access()

    def _queue(self, account,sub,path,category):
        from core.google_drive import compute_file_hashes,guess_mime_type
        sha,md5,size=compute_file_hashes(str(path))
        identity=hashlib.sha256((account+'\n'+sub+'\n'+category+'\n'+sha).encode()).hexdigest()
        file_id='drive_'+identity
        with self.db._connect() as conn:
            saved=conn.execute('SELECT sync_status FROM cloud_sync_files WHERE file_id=?',(file_id,)).fetchone()
        if saved and saved[0]=='SYNCED':
            return
        self.db.upsert_cloud_sync_file(file_id=file_id,local_path=str(path),category=category,
            file_size_bytes=size,sha256_hash=sha,md5_hash=md5,mime_type=guess_mime_type(str(path)))
        with self.db._connect() as conn:
            conn.execute('UPDATE cloud_sync_files SET google_sub=?,local_account_id=? WHERE file_id=?',(sub,account,file_id))
            completed=conn.execute("SELECT 1 FROM cloud_sync_queue WHERE file_id=? AND status='COMPLETED'",(file_id,)).fetchone()
            pending=conn.execute("SELECT 1 FROM cloud_sync_queue WHERE file_id=? AND status IN ('QUEUED','IN_PROGRESS','FAILED')",(file_id,)).fetchone()
        if not completed and not pending:
            self.db.enqueue_cloud_sync_task(file_id,'UPLOAD')

    def _export(self, account,sub):
        from core.android_ledger import reporting_date,projection
        day=reporting_date(None)
        with self.db._connect() as conn:
            events=filter_account_score_events(conn,conn.execute('SELECT * FROM score_events WHERE date=? AND is_dev_mode=0',(day,)).fetchall(),account)
            safe_events=[{k:dict(row).get(k) for k in ('event_id','date','timestamp','event_type','points','rule_version','evidence_id','related_task_id','parent_event_id')} for row in events]
            activities=[dict(row) for row in conn.execute('''SELECT activity_id,date,type,title,scheduled_time,window_start,
                window_end,status,validation_status,submission_id,schedule_version FROM scheduled_activities WHERE account_id=? AND date=?''',(account,day))]
            college=conn.execute('SELECT * FROM daily_college_decisions WHERE account_id=? AND date=?',(account,day)).fetchone()
            gym=conn.execute('SELECT * FROM daily_gym_decisions WHERE account_id=? AND date=?',(account,day)).fetchone()
            evidence=conn.execute('''SELECT DISTINCT e.evidence_id,e.file_path,e.sha256_hash FROM evidence_files e
                JOIN proof_reviews p ON p.evidence_id=e.evidence_id WHERE p.user_id=? AND e.is_dev_mode=0 AND e.is_deleted=0''',(account,)).fetchall()
        snapshot=dict(schema_version=1,account_id=account,date=day,ledger_projection=projection(self.db,day,account),
            score_events=safe_events,activities=activities,college=dict(college) if college else None,gym=dict(gym) if gym else None,
            source='EXISTING_PERSISTED_RECORDS',authoritative_import_allowed=False)
        raw=json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode()
        export=Path(self.db.db_path).resolve().parent/'drive_exports'/hashlib.sha256((account+sub).encode()).hexdigest()[:24]
        export.mkdir(parents=True,exist_ok=True)
        path=export/(day+'_'+hashlib.sha256(raw).hexdigest()+'.json')
        if not path.exists():
            path.write_bytes(raw)
        self._queue(account,sub,path,'Manifests')
        for row in evidence:
            original=Path(row['file_path'] or '')
            if not original.is_file() or not row['sha256_hash']:
                continue
            actual=hashlib.sha256(original.read_bytes()).hexdigest()
            if actual!=row['sha256_hash']:
                raise ValueError('Retained evidence digest mismatch; no replacement uploaded')
            category='Evidence/Images' if original.suffix.lower() in {'.jpg','.jpeg','.png','.webp'} else 'Evidence/Videos' if original.suffix.lower() in {'.mp4','.mov'} else 'Evidence/Documents'
            self._queue(account,sub,original,category)

    def sync_now(self):
        if not self._lock.acquire(blocking=False):
            return {'success':False,'error':'Backup is already in progress'}
        try:
            account,sub=self._binding()
            if not self.auth_manager.is_drive_connected():
                raise PermissionError('Drive read/write verification is required')
            self._status['status']='UPLOADING_FILES'
            self._export(account,sub)
            with self.db._connect() as conn:
                rows=conn.execute('''SELECT q.queue_id,f.* FROM cloud_sync_queue q
                    JOIN cloud_sync_files f ON f.file_id=q.file_id WHERE f.google_sub=? AND f.local_account_id=?
                    AND q.status IN ('QUEUED','FAILED','IN_PROGRESS') ORDER BY q.created_at LIMIT 20''',(sub,account)).fetchall()
            for item in rows:
                if self._binding()!=(account,sub):
                    raise PermissionError('Google account changed during backup')
                path=Path(item['local_path'])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256_hash']:
                    raise ValueError('Queued original evidence is missing or changed')
                self.db.update_cloud_sync_queue_status(item['queue_id'],'IN_PROGRESS')
                try:
                    result=self.drive_client.upload_file(str(path),category=item['category'])
                    if not result.get('id'):
                        raise ValueError('Drive did not acknowledge the upload')
                    self.db.update_cloud_sync_file_status(item['file_id'],'SYNCED',drive_file_id=result['id'])
                    self.db.update_cloud_sync_queue_status(item['queue_id'],'COMPLETED')
                except Exception:
                    self.db.update_cloud_sync_queue_status(item['queue_id'],'FAILED')
                    raise
            self._status.update(status='FILES_BACKED_UP',last_successful_sync=datetime.now(timezone.utc).isoformat(),last_error=None)
            return {'success':True,**self.get_status()}
        except Exception as error:
            self._status.update(status='BACKUP_PENDING',last_error=type(error).__name__)
            return {'success':False,'error':'Backup pending; original files and queue are retained.',**self.get_status()}
        finally:
            self._lock.release()

    def start(self):
        if getattr(self,'_worker',None) and self._worker.is_alive():
            return
        def loop():
            while not self._stop.wait(60):
                if (self.auth_manager.get_session() or {}).get('sync_enabled'):
                    self.sync_now()
        self._worker=threading.Thread(target=loop,name='DriveFileBackup',daemon=True)
        self._worker.start()

    def stop(self):
        self._stop.set()
