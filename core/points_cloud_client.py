"""Optional HTTPS replica of the existing Android-authoritative ledger.

No endpoint is guessed, no Google credentials are used, and failure leaves the
last confirmed local ledger untouched. Deployment/account provisioning is explicit.
"""
import json
import os
from pathlib import Path
import threading
from datetime import datetime,timezone
from urllib.parse import urlsplit
import requests
from core.android_ledger import reconcile,reporting_date

def read_configuration(path=None):
    path=Path(path) if path else Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'StrictRecords'/'PointsCloud'/'client.enc'
    if not path.exists():return None
    import win32crypt
    _,plain=win32crypt.CryptUnprotectData(path.read_bytes(),None,None,None,0)
    config=json.loads(plain)
    endpoint=config['endpoint'].rstrip('/');url=urlsplit(endpoint)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        raise ValueError('Configure a dedicated HTTPS cloud points origin')
    if not all(config.get(k) for k in ['account_id','device_id','device_token']):raise ValueError('Missing cloud device binding')
    return dict(config,endpoint=endpoint)

class PointsCloudReplica:
    def __init__(self,db,*,configuration=read_configuration,transport=None):
        self.db=db;self.configuration=configuration;self.transport=transport if transport is not None else requests.Session()
        self._stop=threading.Event();self._lock=threading.Lock();self._thread=None
        self._status={'status':'NOT_CONFIGURED','taufa_status':'UNAVAILABLE_NO_CAPTURED_SESSION_CONFIGURED','pending_changes':0,'ledger_revision':None,'last_successful_sync':None}
    def status(self):
        return dict(self._status)
    def _send_local_claims(self,config,headers,date):
        from core.account_scope import filter_account_score_events
        with self.db._connect() as conn:
            rows=list(conn.execute('SELECT * FROM score_events WHERE date=? AND is_dev_mode=0 ORDER BY timestamp DESC LIMIT 100',(date,)))
            rows=filter_account_score_events(conn,rows,config['account_id'])
        pending=0
        for original in rows:
            event=dict(original);audit=json.loads(event.get('audit_json') or '{}')
            if audit.get('synced_from_android') or event.get('device_id') not in {config['device_id'],'windows_pc'}:continue
            payload={k:event.get(k) for k in ['event_id','event_type','points','date','timestamp','rule_version','evidence_id','related_task_id']}
            payload['idempotency_key']=event['event_id']
            response=self.transport.post(config['endpoint']+'/v1/points/proposals',json=payload,headers=headers,timeout=15,allow_redirects=False)
            response.raise_for_status()
            if response.json().get('status')=='PENDING_AUTHORITY':pending+=1
        return pending
    def once(self):
        if not self._lock.acquire(blocking=False):return
        try:
            config=self.configuration()
            if not config:self._status['status']='NOT_CONFIGURED';return
            if str(self.db.get_active_user_profile().get('user_id'))!=config['account_id']:
                raise ValueError('Cloud binding differs from the active StrictRecords account')
            self._status['status']='SYNCING'
            now=reporting_date()
            headers={'Authorization':'Bearer '+config['device_token'],'X-Device-Id':config['device_id']}
            self._send_local_claims(config,headers,now)
            response=self.transport.get(config['endpoint']+'/v1/points/state',params={'date':now},
                headers=headers,timeout=15,allow_redirects=False,stream=True)
            if response.status_code in (401,403):
                response.close();self._status['status']='AUTHENTICATION_REQUIRED';return
            response.raise_for_status()
            try:
                raw=bytearray()
                for chunk in response.iter_content(8192):
                    raw.extend(chunk)
                    if len(raw)>4_000_000:raise ValueError('Cloud response exceeds bounded size')
                data=json.loads(raw)
            finally:response.close()
            if data.get('account_id')!=config['account_id']:raise ValueError('Cloud account does not match this installation')
            prior=self._status.get('ledger_revision') or 0
            if type(data.get('ledger_revision')) is not int or data['ledger_revision']<prior:raise ValueError('Stale cloud revision')
            if data.get('envelope'):
                payload=json.loads(data['envelope']['payload_json'])
                receipt=reconcile(self.db,data['envelope'],payload['device_id'],account_id=config['account_id'],minimized_transport=True)
                if not receipt.get('accepted'):raise ValueError('Cloud event manifest conflicts with retained records')
                if receipt['confirmed_balance']!=data.get('confirmed_balance'):raise ValueError('Cloud projection mismatch')
            self._status.update(status='SYNCED' if data.get('envelope') else 'WAITING_FOR_AUTHORITY_LEDGER',
                ledger_revision=data['ledger_revision'],confirmed_balance=data.get('confirmed_balance'),
                pending_changes=data.get('pending_changes',0),reconciliation=data.get('reconciliation'),
                last_successful_sync=datetime.now(timezone.utc).isoformat(),last_source_sync=data.get('last_synchronized_at'),
                taufa_status=data.get('taufa_status','UNAVAILABLE'),last_error=None)
        except Exception as error:
            self._status.update(status='OFFLINE_OR_RECONCILIATION_REQUIRED',last_error=type(error).__name__)
        finally:self._lock.release()
    def start(self):
        # No idle worker when deployment is unconfigured.
        try:
            if not self.configuration():return
        except Exception:
            self._status['status']='CONFIGURATION_ERROR';return
        if self._thread and self._thread.is_alive():return
        def loop():
            delay=30
            while not self._stop.is_set():
                self.once();delay=min(300,delay*2) if self.status()['status']=='OFFLINE_OR_RECONCILIATION_REQUIRED' else 30
                self._stop.wait(delay)
        self._thread=threading.Thread(target=loop,name='PointsCloudReplica',daemon=True);self._thread.start()
    def stop(self):
        self._stop.set()
        if self._thread:self._thread.join(timeout=2)
