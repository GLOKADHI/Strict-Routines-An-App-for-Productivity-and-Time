"""Desktop/extension projection of the shared reward authority; no ledger writes."""
import hashlib
import json
import threading
import time
from datetime import datetime, timezone
import requests
from core.points_cloud_client import read_configuration


class YouTubeRewardCloud:
    def __init__(self, db, *, configuration=read_configuration, transport=None):
        self.db=db; self.configuration=configuration; self.transport=transport or requests.Session()
        self._lock=threading.RLock(); self._refreshing=False; self._data=None; self._scope=None
        self._checked=0; self._attempted=0; self._deadline=0; self._error='Shared reward service is not configured.'
        self._request_sequence=0; self._applied_sequence=0

    def binding(self):
        config=self.configuration()
        if not config: raise ValueError('Configure the shared HTTPS reward service in Cloud points before claiming rewards.')
        if config['account_id'] != str(self.db.get_active_user_profile().get('user_id')): raise PermissionError('Reward binding differs from active account')
        scope=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
        return config,scope

    def request(self, action='state', reward_id=None):
        config,scope=self.binding(); started=time.monotonic()
        with self._lock:
            self._request_sequence+=1; sequence=self._request_sequence
        headers={'Authorization':'Bearer '+config['device_token'],'X-Device-Id':config['device_id']}
        url=config['endpoint']+'/v1/rewards/youtube/'+action
        response=(self.transport.get(url,headers=headers,timeout=10,allow_redirects=False) if action=='state' else
                  self.transport.post(url,headers=headers,json={'reward_id':reward_id},timeout=10,allow_redirects=False))
        try:
            if len(response.content)>500_000: raise ValueError('Reward reply too large')
            data=response.json()
            if response.status_code!=200 or not data.get('success'): raise ValueError(data.get('error','Reward authority unavailable'))
        finally: response.close()
        if data.get('account_id')!=config['account_id'] or self.binding()[1]!=scope: raise PermissionError('Reward account changed')
        now=data['server_now_ms']; active=data.get('active_session'); deadline=0
        if type(now) is not int: raise ValueError('Trusted server time required')
        if active:
            if (active['account_id']!=config['account_id'] or active['state']!='ACTIVE'
                or active['expiry_ms']-active['activation_ms']!=1_200_000
                or not active['activation_ms']<=now<active['expiry_ms']): raise ValueError('Invalid reward receipt')
            deadline=started+(active['expiry_ms']-now)/1000
        with self._lock:
            if sequence < self._applied_sequence:
                return self.view()
            prior=(self._data or {}).get('active_session')
            if active and prior and self._scope==scope and prior.get('reward_id')==active.get('reward_id') and self._deadline>0:
                deadline=min(deadline,self._deadline)
            self._data=data;self._scope=scope;self._checked=time.monotonic();self._deadline=deadline;self._error=None
            self._applied_sequence=sequence
        return self.view()

    def study(self, action, session):
        config, scope = self.binding()
        response = self.transport.post(config['endpoint']+'/v1/rewards/youtube/strict-study/'+action,
            headers={'Authorization':'Bearer '+config['device_token'],'X-Device-Id':config['device_id']},
            json={'session_id':session['session_id'],'activity_id':session['window']['activity_id'],
                  'submission_id':session.get('submission_id')}, timeout=10, allow_redirects=False)
        try:
            if len(response.content)>16384: raise ValueError('Study receipt too large')
            result=response.json()
            if response.status_code!=200 or not result.get('success'): raise ValueError('Update the shared reward coordinator before starting Strict Session.')
            if result.get('account_id')!=config['account_id'] or self.binding()[1]!=scope: raise PermissionError('Study account changed')
            return result
        finally:
            response.close()

    def view(self):
        try: _,scope=self.binding()
        except (ValueError,PermissionError): scope=None
        with self._lock:
            if not scope or self._scope!=scope or not self._data:
                return dict(success=False,configured=scope is not None,active_session=None,available_rewards=[],error=self._error or 'Reward state unconfirmed')
            data=json.loads(json.dumps(self._data));active=data.get('active_session');now=time.monotonic()
            # Account/credential revocation or an ended session must propagate;
            # stale state never invents a new allowance while offline.
            if active and (now>=self._deadline or now-self._checked>30): data['active_session']=None
            elif active:
                active['remaining_seconds']=max(0,int(self._deadline-now))
                active['remaining_ms']=max(0,int((self._deadline-now)*1000))
                active['expiry_time']=datetime.fromtimestamp(active['expiry_ms']/1000,timezone.utc).isoformat()
            data['configured']=True;return data

    def refresh_async(self):
        with self._lock:
            if self._refreshing or time.monotonic()-self._attempted<10:return
            self._refreshing=True
            self._attempted=time.monotonic()
        def run():
            try:self.request()
            except Exception as error:
                with self._lock:self._error=str(error);self._checked=0;self._data=None
            finally:
                with self._lock:self._refreshing=False
        threading.Thread(target=run,name='YouTubeRewardSync',daemon=True).start()

    def youtube_active(self):
        self.refresh_async()
        active=self.view().get('active_session')
        try: config,_=self.binding()
        except Exception:return None
        # The EXE and its local extension are one PC playback integration. Other
        # enrolled devices display the shared state but cannot consume its grant.
        return active if active and active.get('active_client_id')==config['device_id'] else None


_instances={}
_instances_lock=threading.Lock()
def client(db):
    with _instances_lock:
        if db not in _instances:_instances[db]=YouTubeRewardCloud(db)
        return _instances[db]
