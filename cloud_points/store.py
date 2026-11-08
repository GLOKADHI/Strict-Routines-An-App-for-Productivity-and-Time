"""PostgreSQL in production; SQLite is solely a local contract-test backend."""
import contextlib
import hashlib
import hmac
import json
import sqlite3
import time
import uuid
from datetime import datetime,timezone
from .protocol import THREAD_ID,canonical,digest,slot_at,next_slot,validate,validate_taufa

SCHEMA='''
CREATE TABLE IF NOT EXISTS points_accounts(account_id TEXT PRIMARY KEY,authority_device TEXT NOT NULL,revision BIGINT NOT NULL DEFAULT 0,last_slot TEXT,last_success TEXT,last_ai_revision BIGINT NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS points_credentials(device_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,token_hash TEXT NOT NULL,role TEXT NOT NULL,expires_at BIGINT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS points_periods(account_id TEXT NOT NULL,date TEXT NOT NULL,revision BIGINT NOT NULL,source_revision BIGINT NOT NULL,sha256 TEXT NOT NULL,envelope TEXT NOT NULL,balance INTEGER NOT NULL,raw_points INTEGER NOT NULL,received_at TEXT NOT NULL,PRIMARY KEY(account_id,date));
CREATE TABLE IF NOT EXISTS points_events(account_id TEXT NOT NULL,event_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,date TEXT NOT NULL,revision BIGINT NOT NULL,content_hash TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(account_id,event_id),UNIQUE(account_id,idempotency_key));
CREATE TABLE IF NOT EXISTS points_proposals(account_id TEXT NOT NULL,event_id TEXT NOT NULL,device_id TEXT NOT NULL,payload TEXT NOT NULL,content_hash TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING_AUTHORITY',PRIMARY KEY(account_id,event_id));
CREATE TABLE IF NOT EXISTS points_requests(request_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,scheduled_at TEXT NOT NULL,executed_at TEXT,interval_type TEXT NOT NULL,ledger_revision BIGINT NOT NULL,status TEXT NOT NULL,attempt INTEGER NOT NULL DEFAULT 0,lease_until BIGINT NOT NULL DEFAULT 0,snapshot TEXT NOT NULL,result TEXT,last_error TEXT,UNIQUE(account_id,scheduled_at));
'''

class Store:
    def __init__(self,url,*,test_sqlite=False):
        self.url=url;self.sqlite=test_sqlite
        if not test_sqlite and not url.startswith(('postgresql://','postgres://')):raise ValueError('Production requires a PostgreSQL database URL')
    @contextlib.contextmanager
    def transaction(self):
        if self.sqlite:
            c=sqlite3.connect(self.url,timeout=20);c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE')
        else:
            import psycopg
            from psycopg.rows import dict_row
            c=psycopg.connect(self.url,row_factory=dict_row)
        class Tx:
            def execute(_,sql,args=()):return c.execute(sql if self.sqlite else sql.replace('?','%s'),args)
        try:yield Tx();c.commit()
        except BaseException:c.rollback();raise
        finally:c.close()
    def initialize(self):
        from .demo_access import SCHEMA as DEMO_SCHEMA
        from .youtube_rewards import SCHEMA as REWARD_SCHEMA
        with self.transaction() as tx:
            if not self.sqlite:tx.execute('SELECT pg_advisory_xact_lock(734174)')
            for sql in SCHEMA.split(';'):
                if sql.strip():tx.execute(sql)
            tx.execute(DEMO_SCHEMA)
            for sql in REWARD_SCHEMA.split(';'):
                if sql.strip(): tx.execute(sql)
    def lock(self,tx,account):
        row=tx.execute('SELECT * FROM points_accounts WHERE account_id=?'+('' if self.sqlite else ' FOR UPDATE'),(account,)).fetchone()
        if not row:raise PermissionError('Unknown account')
        return dict(row)
    def provision(self,account,device,token_hash,role,expiry,*,authority_device):
        if role not in {'AUTHORITY','REPLICA','PROPOSER'} or len(token_hash)!=64 or expiry<=time.time():raise ValueError('Invalid provisioning metadata')
        with self.transaction() as tx:
            tx.execute('INSERT INTO points_accounts(account_id,authority_device) VALUES(?,?) ON CONFLICT(account_id) DO NOTHING',(account,authority_device))
            row=self.lock(tx,account)
            if row['authority_device']!=authority_device or role=='AUTHORITY' and device!=authority_device:raise ValueError('Authority must be explicitly migrated, never silently replaced')
            existing=tx.execute('SELECT account_id FROM points_credentials WHERE device_id=?',(device,)).fetchone()
            if existing and existing['account_id']!=account:raise ValueError('Device already belongs to another account')
            tx.execute('INSERT INTO points_credentials(device_id,account_id,token_hash,role,expires_at) VALUES(?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET token_hash=excluded.token_hash,role=excluded.role,expires_at=excluded.expires_at,revoked=0',(device,account,token_hash,role,expiry))
    def authenticate(self,device,token,now=None):
        if not token or len(token)>4096:raise PermissionError('Device authentication required')
        with self.transaction() as tx:row=tx.execute('SELECT * FROM points_credentials WHERE device_id=?',(device,)).fetchone()
        if not row or row['revoked'] or row['expires_at']<=(time.time() if now is None else now) or not hmac.compare_digest(row['token_hash'],hashlib.sha256(token.encode()).hexdigest()):raise PermissionError('Device credential expired, revoked or invalid')
        return dict(row)
    def publish(self,principal,envelope,now=None):
        account=principal['account_id'];device=principal['device_id']
        if principal['role']!='AUTHORITY':raise PermissionError('Only the registered existing Android ledger authority can publish confirmed events')
        p,clean=validate(envelope,account,device);received=(now or datetime.now(timezone.utc)).isoformat()
        with self.transaction() as tx:
            owner=self.lock(tx,account)
            if owner['authority_device']!=device:raise PermissionError('Different ledger authority')
            previous=tx.execute('SELECT * FROM points_periods WHERE account_id=? AND date=?',(account,p['date'])).fetchone()
            if previous:
                if p['revision']<previous['source_revision']:raise ValueError('Stale source revision')
                if p['revision']==previous['source_revision']:
                    if clean['sha256']!=previous['sha256']:raise ValueError('Conflicting source revision')
                    return self.state_in(tx,account,p['date'])
            retained=list(tx.execute('SELECT event_id,content_hash FROM points_events WHERE account_id=? AND date=?',(account,p['date'])))
            incoming={e['event_id']:e for e in p['score_events']}
            if any(r['event_id'] not in incoming for r in retained):raise ValueError('Missing retained events; human reconciliation required')
            for r in retained:
                if r['content_hash']!=digest(incoming[r['event_id']]):raise ValueError('Historical event changed; use an authorized append-only correction')
            revision=owner['revision']+1
            for e in p['score_events']:
                existing=tx.execute('SELECT content_hash FROM points_events WHERE account_id=? AND (event_id=? OR idempotency_key=?)',(account,e['event_id'],e['idempotency_key'])).fetchone()
                if existing and existing['content_hash']!=digest(e):raise ValueError('Replayed event identity conflicts with history')
                tx.execute('INSERT INTO points_events(account_id,event_id,idempotency_key,date,revision,content_hash,payload) VALUES(?,?,?,?,?,?,?) ON CONFLICT(account_id,event_id) DO NOTHING',(account,e['event_id'],e['idempotency_key'],p['date'],revision,digest(e),canonical(e)))
                tx.execute("UPDATE points_proposals SET status='ACKNOWLEDGED_BY_AUTHORITY' WHERE account_id=? AND event_id=?",(account,e['event_id']))
            tx.execute('INSERT INTO points_periods(account_id,date,revision,source_revision,sha256,envelope,balance,raw_points,received_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,date) DO UPDATE SET revision=excluded.revision,source_revision=excluded.source_revision,sha256=excluded.sha256,envelope=excluded.envelope,balance=excluded.balance,raw_points=excluded.raw_points,received_at=excluded.received_at',(account,p['date'],revision,p['revision'],clean['sha256'],canonical(clean),p['confirmed_score'],p['raw_verified_points'],received))
            tx.execute('UPDATE points_accounts SET revision=? WHERE account_id=?',(revision,account))
            return self.state_in(tx,account,p['date'])
    def propose(self,principal,event):
        # Pending source claims never alter score_events or calculated balances.
        if not isinstance(event,dict) or not isinstance(event.get('event_id'),str) or len(event['event_id'])>200:raise ValueError('Invalid source event')
        allowed={'event_id','event_type','points','date','timestamp','rule_version','evidence_id','related_task_id','idempotency_key'}
        clean={k:v for k,v in event.items() if k in allowed}
        with self.transaction() as tx:
            self.lock(tx,principal['account_id'])
            confirmed=tx.execute('SELECT payload FROM points_events WHERE account_id=? AND event_id=?',(principal['account_id'],clean['event_id'])).fetchone()
            if confirmed:
                retained=json.loads(confirmed['payload'])
                if any(clean.get(k)!=retained.get(k) for k in ['event_type','points']):raise ValueError('Claim conflicts with confirmed source event')
                return {'accepted':True,'status':'ACKNOWLEDGED_BY_AUTHORITY','event_id':clean['event_id']}
            old=tx.execute('SELECT content_hash FROM points_proposals WHERE account_id=? AND event_id=?',(principal['account_id'],clean['event_id'])).fetchone()
            if old and old['content_hash']!=digest(clean):raise ValueError('Conflicting pending event')
            tx.execute('INSERT INTO points_proposals(account_id,event_id,device_id,payload,content_hash) VALUES(?,?,?,?,?) ON CONFLICT(account_id,event_id) DO NOTHING',(principal['account_id'],clean['event_id'],principal['device_id'],canonical(clean),digest(clean)))
        return {'accepted':True,'status':'PENDING_AUTHORITY','event_id':clean['event_id']}
    def state_in(self,tx,account,date):
        owner=self.lock(tx,account);row=tx.execute('SELECT * FROM points_periods WHERE account_id=? AND date=?',(account,date)).fetchone()
        request=tx.execute('SELECT request_id,scheduled_at,executed_at,ledger_revision,status,last_error,result,snapshot FROM points_requests WHERE account_id=? ORDER BY scheduled_at DESC LIMIT 1',(account,)).fetchone()
        request=dict(request) if request else None
        ai_result=json.loads(request.pop('result') or 'null') if request else None
        request_snapshot=json.loads(request.pop('snapshot')) if request else None
        taufa_status='NOT_YET_RECONCILED'
        if request:
            taufa_status=request['last_error'] or {
                'QUEUED':'RECONCILIATION_PENDING','RUNNING':'RECONCILIATION_PENDING',
                'COMPLETE_TAUFA_UNAVAILABLE':'UNAVAILABLE_NO_CAPTURED_SESSION_CONFIGURED',
            }.get(request['status'],request['status'])
        if ai_result and ai_result.get('_transport')=='CAPTURED_EXTENSION_SESSION':
            current=(request['ledger_revision']==owner['revision'] and row is not None
                and request_snapshot.get('reporting_date')==date
                and request['status'] not in {'STALE_REQUIRES_RECHECK','WAITING_FOR_AUTHORITY_LEDGER'})
            taufa_status=('VERIFIED_CAPTURED_EXTENSION_SESSION' if request['status']=='CONSISTENT'
                and ai_result.get('reconciliation_status')=='CONSISTENT' else 'REVIEW_REQUIRED_CAPTURED_SESSION_RESULT') if current else 'STALE_CAPTURED_SESSION_RESULT'
            request['result']=ai_result
        pending=tx.execute("SELECT COUNT(*) AS n FROM points_proposals WHERE account_id=? AND status='PENDING_AUTHORITY'",(account,)).fetchone()['n']
        proposals=[json.loads(r['payload']) for r in tx.execute("SELECT payload FROM points_proposals WHERE account_id=? AND status='PENDING_AUTHORITY' ORDER BY event_id LIMIT 100",(account,))]
        return {'account_id':account,'date':date,'ledger_revision':owner['revision'],'period_revision':row['revision'] if row else None,'confirmed_balance':row['balance'] if row else None,'raw_verified_points':row['raw_points'] if row else None,'last_synchronized_at':row['received_at'] if row else None,'envelope':json.loads(row['envelope']) if row else None,'pending_changes':pending,'pending_proposals':proposals,'last_successful_reconciliation':owner['last_success'],'reconciliation':request,'taufa_status':taufa_status,'thread_id':THREAD_ID,'score_authority':'ANDROID','next_scheduled_at':next_slot(datetime.now(timezone.utc)).isoformat()}
    def state(self,principal,date):
        with self.transaction() as tx:return self.state_in(tx,principal['account_id'],date)
    def tick(self,now,*,limit=100):
        slot=slot_at(now).isoformat();created=[]
        with self.transaction() as tx:
            accounts=list(tx.execute('SELECT account_id FROM points_accounts WHERE last_slot IS NULL OR last_slot<? ORDER BY account_id LIMIT ?',(slot,limit)))
            for entry in accounts:
                account=entry['account_id'];owner=self.lock(tx,account)
                if owner['last_slot'] and owner['last_slot']>=slot:continue
                request_id='reconcile_'+digest({'account':account,'slot':slot})[:40]
                day=slot_at(now).date().isoformat()
                rows=list(tx.execute('SELECT payload,revision FROM points_events WHERE account_id=? AND date=? ORDER BY revision,event_id',(account,day)))
                events=[json.loads(r['payload']) for r in rows]
                new_ids=[e['event_id'] for e,r in zip(events,rows) if r['revision']>owner['last_ai_revision']]
                period=tx.execute('SELECT balance,raw_points FROM points_periods WHERE account_id=? AND date=?',(account,day)).fetchone()
                # Reconcile the complete reporting day; a delta alone cannot be
                # compared with the full daily aggregate or mixed across dates.
                snapshot={'request_id':request_id,'thread_id':THREAD_ID,'authenticated_user_reference':account,'ledger_revision':owner['revision'],'last_successfully_synchronized_revision':owner['last_ai_revision'],'current_calculated_balance':period['balance'] if period else None,'reporting_date':day,'events':events,'event_scope':'COMPLETE_REPORTING_DAY','new_event_ids':new_ids}
                tx.execute("INSERT INTO points_requests(request_id,account_id,scheduled_at,interval_type,ledger_revision,status,snapshot) VALUES(?,?,?,?,?,'QUEUED',?) ON CONFLICT(account_id,scheduled_at) DO NOTHING",(request_id,account,slot,'HOURLY' if slot_at(now).hour>=19 else 'TWO_HOURLY',owner['revision'],canonical(snapshot)))
                tx.execute('UPDATE points_accounts SET last_slot=? WHERE account_id=?',(slot,account));created.append(request_id)
        return created
    def claim(self,now,lease_seconds=90,max_attempts=3):
        with self.transaction() as tx:
            tx.execute("UPDATE points_requests SET status='FAILED',last_error='RETRY_LIMIT' WHERE status='RUNNING' AND lease_until<=? AND attempt>=?",(int(now.timestamp()),max_attempts))
            suffix='' if self.sqlite else ' FOR UPDATE SKIP LOCKED'
            row=tx.execute("SELECT * FROM points_requests WHERE (status='QUEUED' OR status='RUNNING' AND lease_until<=?) AND attempt<? ORDER BY scheduled_at LIMIT 1"+suffix,(int(now.timestamp()),max_attempts)).fetchone()
            if not row:return None
            attempt=row['attempt']+1
            tx.execute("UPDATE points_requests SET status='RUNNING',attempt=?,lease_until=?,executed_at=? WHERE request_id=?",(attempt,int(now.timestamp())+lease_seconds,now.isoformat(),row['request_id']))
            return dict(row,attempt=attempt)
    def finish(self,claim,result=None,*,now=None):
        # AI produces an audit result only. This method never writes points_events.
        snapshot=json.loads(claim['snapshot']);status='COMPLETE_TAUFA_UNAVAILABLE'
        if result is not None:
            validate_taufa(result,snapshot);status=result['reconciliation_status']
        moment=now or datetime.now(timezone.utc)
        with self.transaction() as tx:
            owner=self.lock(tx,claim['account_id'])
            current=tx.execute('SELECT status,attempt,lease_until FROM points_requests WHERE request_id=?',(claim['request_id'],)).fetchone()
            if current['status']!='RUNNING' or current['attempt']!=claim['attempt'] or current['lease_until']<int(moment.timestamp()):raise ValueError('Expired/replayed reconciliation worker lease')
            if owner['revision']!=claim['ledger_revision']:status='STALE_REQUIRES_RECHECK'
            elif snapshot['current_calculated_balance'] is None:status='WAITING_FOR_AUTHORITY_LEDGER'
            tx.execute('UPDATE points_requests SET status=?,result=?,lease_until=0 WHERE request_id=?',(status,canonical(result) if result else None,claim['request_id']))
            if status in {'CONSISTENT','COMPLETE_TAUFA_UNAVAILABLE'}:
                tx.execute('UPDATE points_accounts SET last_success=? WHERE account_id=?',(moment.isoformat(),claim['account_id']))
                if status=='CONSISTENT':tx.execute('UPDATE points_accounts SET last_ai_revision=? WHERE account_id=?',(claim['ledger_revision'],claim['account_id']))
        return status
