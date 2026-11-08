"""Durable Night Shutdown state used by the existing restriction watcher.

No points, proofs, rewards or other schedules are written here. This component
has no timer/thread of its own: the existing Windows watcher calls tick().
"""
from __future__ import annotations
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit, parse_qs

VIDEO=re.compile(r'^[A-Za-z0-9_-]{11}$')
CAPTURE_FIELDS=('session_id','capture_date','capture_timestamp','timezone','video_title','channel',
    'video_url','canonical_url','video_id','playback_position','playback_position_seconds',
    'total_duration','total_duration_seconds','playback_state','playback_speed','capture_status',
    'browser_name','profile_id','tab_id','document_id','observed_timestamp','scheduled_capture_timestamp',
    'capture_delay_ms','controlled_live_run','primary','capture_source','extension_storage_status')
DEFAULT_SCHEDULE=dict(enabled=True,monitor_time='23:15',capture_time='23:19',shutdown_time='23:22',
                      force_unrelated_apps=False,configured=True)


def timestamp():return datetime.now().astimezone().isoformat(timespec='milliseconds')
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def seconds(value):
    if value is None:return None
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        raise ValueError('Invalid actual playback time')
    return value
def hms(value):
    if value is None:return None
    whole=int(seconds(value));return f'{whole//3600:02d}:{whole//60%60:02d}:{whole%60:02d}'
def video_id(value):
    u=urlsplit(str(value));host=(u.hostname or '').lower().rstrip('.')
    if u.scheme not in {'http','https'} or u.username or u.password:return None
    if not (host=='youtu.be' or host=='youtube.com' or host.endswith('.youtube.com')):return None
    candidate = None
    if host=='youtu.be':
        segs = [s for s in u.path.strip('/').split('/') if s]
        if segs: candidate = segs[0]
    elif host in {'youtube.com','www.youtube.com','m.youtube.com'} or host.endswith('.youtube.com'):
        candidate = parse_qs(u.query).get('v',[''])[0]
        if not candidate:
            segs = [s for s in u.path.strip('/').split('/') if s]
            if len(segs) >= 2 and segs[0].lower() in ('shorts', 'embed', 'live', 'v'):
                candidate = segs[1]
    return candidate if (candidate and VIDEO.fullmatch(candidate)) else None


class NightStore:
    def __init__(self,path=None):
        self.path=Path(path or Path(os.environ.get('LOCALAPPDATA',Path.home()))/'StrictRecords/night_shutdown.sqlite').resolve()
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS night_config(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS night_checkpoints(profile_id TEXT NOT NULL,tab_id INTEGER NOT NULL,document_id TEXT NOT NULL,payload TEXT NOT NULL,received_ms INTEGER NOT NULL,PRIMARY KEY(profile_id,tab_id,document_id));
                CREATE TABLE IF NOT EXISTS night_records(session_id TEXT PRIMARY KEY,capture_date TEXT NOT NULL,profile_id TEXT NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS night_profiles(run_id TEXT NOT NULL,profile_id TEXT NOT NULL,committed INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(run_id,profile_id));
                CREATE TABLE IF NOT EXISTS night_runs(run_id TEXT PRIMARY KEY,date TEXT NOT NULL,monitor_ms INTEGER NOT NULL,capture_ms INTEGER NOT NULL,shutdown_ms INTEGER NOT NULL,controlled INTEGER NOT NULL DEFAULT 0,profile_id TEXT,process_scope TEXT,state TEXT NOT NULL,requested_boot REAL,request_timestamp TEXT,shutdown_status TEXT NOT NULL,close_results TEXT NOT NULL DEFAULT '[]');''')
            if 'requested_session' not in {r[1] for r in c.execute('PRAGMA table_info(night_runs)')}:
                c.execute('ALTER TABLE night_runs ADD COLUMN requested_session TEXT')
    @contextlib.contextmanager
    def connect(self):
        c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
        try:yield c;c.commit()
        except BaseException:c.rollback();raise
        finally:c.close()
    def config(self):
        with self.connect() as c:row=c.execute("SELECT payload FROM night_config WHERE key='schedule'").fetchone()
        return dict(DEFAULT_SCHEDULE,**(json.loads(row[0]) if row else {}))
    def configure(self,values):
        current=self.config();result=dict(current)
        for key in ('monitor_time','capture_time','shutdown_time'):
            value=values.get(key,current[key])
            if not isinstance(value,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value):raise ValueError('Use HH:MM local PC time')
            result[key]=value
        if not result['monitor_time']<result['capture_time']<result['shutdown_time']:raise ValueError('Monitoring must precede capture and shutdown')
        for key in ('enabled','force_unrelated_apps'):
            if key in values:
                if type(values[key]) is not bool:raise ValueError('Invalid safety setting')
                result[key]=values[key]
        result['configured']=True
        with self.connect() as c:c.execute("INSERT OR REPLACE INTO night_config VALUES('schedule',?)",(canonical(result),))
        return result
    def ensure_run(self,now=None):
        now=now or datetime.now().astimezone();cfg=self.config()
        if not cfg['enabled'] or not cfg['configured']:return None
        day=now.date().isoformat();run_id='night_'+day
        times=[int(datetime.combine(now.date(),datetime.strptime(cfg[k],'%H:%M').time()).astimezone().timestamp()*1000) for k in ('monitor_time','capture_time','shutdown_time')]
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO night_runs(run_id,date,monitor_ms,capture_ms,shutdown_ms,state,shutdown_status) VALUES(?,?,?,?,?,'SCHEDULED','PENDING')",(run_id,day,*times))
            row=dict(c.execute('SELECT * FROM night_runs WHERE run_id=?',(run_id,)).fetchone())
            if row.get('state') in ('SCHEDULED','MONITORING') and (row.get('monitor_ms')!=times[0] or row.get('capture_ms')!=times[1] or row.get('shutdown_ms')!=times[2]):
                c.execute("UPDATE night_runs SET monitor_ms=?, capture_ms=?, shutdown_ms=? WHERE run_id=?", (*times, run_id))
                row['monitor_ms'], row['capture_ms'], row['shutdown_ms'] = times
            return row
    def controlled_run(self,profile,process_scope,monitor_ms,capture_ms,shutdown_ms):
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',profile):raise ValueError('Use the dedicated validation profile identity')
        if not isinstance(process_scope,list) or not process_scope:raise ValueError('A controlled live run requires real, owned process identities')
        if not monitor_ms<capture_ms<shutdown_ms:raise ValueError('Invalid controlled schedule')
        run_id='live_'+hashlib.sha256(f'{profile}:{capture_ms}'.encode()).hexdigest()[:24]
        day=datetime.fromtimestamp(capture_ms/1000).astimezone().date().isoformat()
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO night_runs(run_id,date,monitor_ms,capture_ms,shutdown_ms,controlled,profile_id,process_scope,state,shutdown_status) VALUES(?,?,?,?,?,1,?,?,'SCHEDULED','PENDING')",(run_id,day,monitor_ms,capture_ms,shutdown_ms,profile,canonical(process_scope)))
        return self.run(run_id)
    def run(self,run_id):
        with self.connect() as c:row=c.execute('SELECT * FROM night_runs WHERE run_id=?',(run_id,)).fetchone()
        return dict(row) if row else None
    def context(self,profile='',now_ms=None):
        now_ms=int(time.time()*1000) if now_ms is None else now_ms
        self.ensure_run()
        with self.connect() as c:
            row=c.execute("SELECT * FROM night_runs WHERE (controlled=0 OR profile_id=?) AND shutdown_ms>=? AND state NOT IN ('COMPLETE','SHUTDOWN_BLOCKED') ORDER BY controlled DESC,capture_ms LIMIT 1",(profile,now_ms-300000)).fetchone()
        if row and profile and row['monitor_ms']<=now_ms<=row['capture_ms']+10000:
            with self.connect() as c:c.execute('INSERT OR IGNORE INTO night_profiles VALUES(?,?,0)',(row['run_id'],profile))
        cfg=self.config()
        with self.connect() as c:locked=c.execute("SELECT 1 FROM night_runs WHERE controlled=0 AND state IN ('NIGHT_LOCK_ACTIVE','SHUTDOWN_REQUESTED','SHUTDOWN_BLOCKED') LIMIT 1").fetchone()
        return dict(success=True,server_now_ms=now_ms,night_lock_active=bool(locked),timezone=str(datetime.now().astimezone().tzinfo),schedule=cfg,
                    run={k:row[k] for k in ('run_id','date','monitor_ms','capture_ms','shutdown_ms','controlled','state','shutdown_status')} if row else None)
    def checkpoint(self,item):
        clean=self.clean_record(item,checkpoint=True)
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO night_checkpoints VALUES(?,?,?,?,?)',(clean['profile_id'],clean['tab_id'],clean['document_id'],canonical(clean),int(time.time()*1000)))
        return dict(success=True,storage_status='PERSISTED')
    def commit_profile(self,run_id,profile,session_ids):
        """Authorize closure only after every selected tab has a native backup."""
        if not isinstance(session_ids,list) or not session_ids:raise ValueError('No committed captures')
        prefix=run_id+':'+profile+':'
        with self.connect() as c:
            for sid in session_ids:
                if not isinstance(sid,str) or not sid.startswith(prefix):raise ValueError('Wrong capture identity')
                if not c.execute('SELECT 1 FROM night_records WHERE session_id=? AND profile_id=?',(sid,profile)).fetchone():raise ValueError('Native backup incomplete')
            c.execute('INSERT OR REPLACE INTO night_profiles VALUES(?,?,1)',(run_id,profile))
        return dict(success=True,status='NATIVE_BACKUP_SAVED')
    def ready(self,run):
        with self.connect() as c:
            rows=c.execute('SELECT committed FROM night_profiles WHERE run_id=?',(run['run_id'],)).fetchall()
        return bool(rows) and all(r[0] for r in rows)
    @staticmethod
    def clean_record(item,checkpoint=False):
        if not isinstance(item,dict):raise ValueError('A playback record is required')
        result={k:item[k] for k in CAPTURE_FIELDS if k in item}
        for key in ('session_id','profile_id','document_id'):
            if not isinstance(result.get(key),str) or not 1<=len(result[key])<=240:raise ValueError('Missing session identity')
        if type(result.get('tab_id')) is not int:raise ValueError('Missing browser tab identity')
        if result.get('capture_status') not in {'CAPTURED','LAST_OBSERVED','NO_ACTIVE_YOUTUBE_VIDEO','CAPTURE_FAILED'}:raise ValueError('Invalid capture status')
        if result['capture_status'] in {'CAPTURED','LAST_OBSERVED'}:
            vid=video_id(result.get('video_url',''))
            if not vid or result.get('video_id')!=vid:raise ValueError('The record does not identify an actual YouTube watch video')
            result['canonical_url']='https://www.youtube.com/watch?v='+vid
            result['playback_position_seconds']=seconds(result.get('playback_position_seconds'))
            if result['playback_position_seconds'] is None:raise ValueError('Actual player position required')
            result['total_duration_seconds']=seconds(result.get('total_duration_seconds'))
            result['playback_position']=hms(result['playback_position_seconds'])
            result['total_duration']=hms(result['total_duration_seconds'])
            speed=seconds(result.get('playback_speed'))
            if speed is None or not 0<speed<=16:raise ValueError('Invalid playback speed')
            if result.get('playback_state') not in {'PLAYING','PAUSED','ENDED'}:raise ValueError('Invalid player state')
        for key in ('video_title','channel','video_url','browser_name','timezone'):
            if key in result and (not isinstance(result[key],str) or len(result[key])>4096):raise ValueError('Invalid playback text')
        stamp=datetime.fromisoformat(result['capture_timestamp'].replace('Z','+00:00'))
        if stamp.tzinfo is None or stamp.date().isoformat()!=result.get('capture_date'):raise ValueError('Capture timestamp/date must identify the actual local capture')
        result['storage_status']='PERSISTED'
        return result
    def save(self,item):
        result=self.clean_record(item);digest=hashlib.sha256(canonical({k:result.get(k) for k in CAPTURE_FIELDS}).encode()).hexdigest()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT fingerprint FROM night_records WHERE session_id=?',(result['session_id'],)).fetchone()
            if old and old[0]!=digest:raise ValueError('A stored capture cannot be overwritten by a different video or timeline')
            result['backup_status']='NATIVE_BACKUP_SAVED';result['shutdown_status']='PENDING'
            c.execute('INSERT OR IGNORE INTO night_records VALUES(?,?,?,?,?)',(result['session_id'],result['capture_date'],result['profile_id'],digest,canonical(result)))
            persisted=c.execute('SELECT payload FROM night_records WHERE session_id=?',(result['session_id'],)).fetchone()
            if not persisted:raise OSError('Native capture readback failed')
        return dict(success=True,backup_status='NATIVE_BACKUP_SAVED',session_id=result['session_id'],fingerprint=digest,record=json.loads(persisted[0]))
    def history(self,profile='',limit=100):
        with self.connect() as c:
            rows=c.execute('SELECT payload FROM night_records'+(" WHERE profile_id=? OR profile_id='windows-no-browser'" if profile else '')+" ORDER BY json_extract(payload,'$.capture_timestamp') DESC LIMIT ?",((profile,limit) if profile else (limit,))).fetchall()
        return [json.loads(r[0]) for r in rows]
    def update_run(self,run_id,**changes):
        allowed={'state','shutdown_status','requested_boot','requested_session','request_timestamp','close_results'}
        if not changes or set(changes)-allowed:raise ValueError('Invalid night state update')
        with self.connect() as c:
            c.execute('UPDATE night_runs SET '+','.join(k+'=?' for k in changes)+' WHERE run_id=?',(*changes.values(),run_id))
            if 'shutdown_status' in changes or 'close_results' in changes:
                for row in c.execute('SELECT session_id,payload FROM night_records').fetchall():
                    item=json.loads(row['payload'])
                    if item['session_id'].startswith(run_id+':'):
                        if 'shutdown_status' in changes:item['shutdown_status']=changes['shutdown_status']
                        if 'close_results' in changes:
                            results=json.loads(changes['close_results'])
                            item['browser_close_status']='EXIT_VERIFIED' if all(r.get('exited') for r in results) else 'BROWSER_CLOSE_FAILED'
                        c.execute('UPDATE night_records SET payload=? WHERE session_id=?',(canonical(item),row['session_id']))
    def confirmed_startup(self,boot_time,evidence,session_id=''):
        """A request/exit code alone is never evidence of a completed shutdown."""
        with self.connect() as c:
            rows=[dict(r) for r in c.execute("SELECT * FROM night_runs WHERE shutdown_status='SHUTDOWN_REQUESTED'")]
        for run in rows:
            new_session=bool(session_id and run.get('requested_session') and session_id!=run['requested_session'])
            if not new_session and (run['requested_boot'] is None or boot_time<=run['requested_boot']):continue
            status='SHUTDOWN_CONFIRMED_ON_NEXT_STARTUP' if evidence(run,boot_time) else 'UNCONFIRMED_AFTER_RESTART'
            self.update_run(run['run_id'],state='COMPLETE',shutdown_status=status)
        with self.connect() as c:
            interrupted=[dict(r) for r in c.execute("SELECT * FROM night_runs WHERE controlled=0 AND state IN ('NIGHT_LOCK_ACTIVE','SHUTDOWN_BLOCKED') AND ((requested_boot IS NOT NULL AND requested_boot<?) OR (requested_session IS NOT NULL AND requested_session!='' AND requested_session!=? AND ?!=''))",(boot_time,session_id,session_id))]
        for run in interrupted:self.update_run(run['run_id'],state='COMPLETE',shutdown_status='UNCONFIRMED_AFTER_RESTART')


class NightCoordinator:
    """A stage of the existing enforcement loop, never a second scheduler."""
    def __init__(self,store,close_targets,shutdown,show_countdown=lambda _:None,boot_time=lambda:0,fallback_capture=lambda _:None,session_identity=lambda:''):
        self.store=store;self.close_targets=close_targets;self.shutdown=shutdown
        self.show_countdown=show_countdown;self.boot_time=boot_time
        self.fallback_capture=fallback_capture
        self.session_identity=session_identity
    def tick(self,now_ms=None):
        now_ms=int(time.time()*1000) if now_ms is None else now_ms
        self.store.ensure_run()
        with self.store.connect() as c:runs=[dict(r) for r in c.execute("SELECT * FROM night_runs WHERE monitor_ms<=? AND state!='COMPLETE'",(now_ms,))]
        for run in runs:
            if now_ms>run['shutdown_ms']+300000 and run['state'] not in ('NIGHT_LOCK_ACTIVE','SHUTDOWN_REQUESTED','SHUTDOWN_BLOCKED'):
                self.store.update_run(run['run_id'],state='COMPLETE',shutdown_status='MISSED_SCHEDULE')
                continue
            if now_ms<run['capture_ms']:
                if run['state']=='SCHEDULED':self.store.update_run(run['run_id'],state='MONITORING')
                continue
            # Wait for the verified extension-side commit and native backup.
            if not self.store.ready(run):self.fallback_capture(run)
            records=[r for r in self.store.history(run['profile_id'] or '') if r['session_id'].startswith(run['run_id']+':')]
            saved=bool(records) and self.store.ready(run)
            if saved:
                results=self.close_targets(run)
                state=run['state'] if run['state'] in ('SHUTDOWN_REQUESTED','SHUTDOWN_BLOCKED') else 'NIGHT_LOCK_ACTIVE'
                self.store.update_run(run['run_id'],state=state,close_results=canonical(results),requested_boot=run['requested_boot'] or self.boot_time(),requested_session=run.get('requested_session') or self.session_identity())
            else:results=[]
            if saved and any(not r.get('exited') for r in results):
                self.store.update_run(run['run_id'],shutdown_status='BROWSER_CLOSE_FAILED')
            self.show_countdown(dict(run,controlled=bool(run['controlled']),records_saved=saved,video_saved=any(r['capture_status'] in ('CAPTURED','LAST_OBSERVED') for r in records),capture_failed=any(r['capture_status']=='CAPTURE_FAILED' for r in records),remaining_seconds=max(0,math.ceil((run['shutdown_ms']-now_ms)/1000)),close_failed=any(not r.get('exited') for r in results),close_results=results))
            if now_ms>=run['shutdown_ms']:
                if run['state'] in ('SHUTDOWN_REQUESTED','SHUTDOWN_BLOCKED'):continue
                if not saved:
                    self.store.update_run(run['run_id'],state='CAPTURE_BLOCKED',shutdown_status='SHUTDOWN_BLOCKED')
                    continue
                if run['controlled']:
                    self.store.update_run(run['run_id'],state='COMPLETE',shutdown_status='SHUTDOWN_SUPPRESSED_CONTROLLED_LIVE_RUN')
                    self.show_countdown(dict(dismiss=True))
                else:
                    self.store.update_run(run['run_id'],state='SHUTDOWN_REQUESTED',shutdown_status='SHUTDOWN_REQUESTED',requested_boot=self.boot_time(),requested_session=self.session_identity(),request_timestamp=timestamp())
                    result=self.shutdown(run,self.store.config()['force_unrelated_apps'])
                    if not result.get('requested'):self.store.update_run(run['run_id'],state='SHUTDOWN_BLOCKED',shutdown_status='SHUTDOWN_BLOCKED')
    def lock_active(self):
        now=int(time.time()*1000)
        with self.store.connect() as c:
            row=c.execute("SELECT 1 FROM night_runs WHERE controlled=0 AND capture_ms<=? AND state IN ('NIGHT_LOCK_ACTIVE','SHUTDOWN_REQUESTED','SHUTDOWN_BLOCKED')",(now,)).fetchone()
        return bool(row)
