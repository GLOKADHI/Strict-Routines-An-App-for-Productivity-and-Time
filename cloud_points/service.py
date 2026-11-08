"""A small cloud-only API, deliberately separate from the desktop control server."""
import hmac
import json
import logging
import os
import threading
from datetime import datetime,timezone
from urllib.parse import parse_qs
from .protocol import IST,canonical
from .store import Store
from .demo_access import state as demo_access_state, DemoUnavailable
from . import youtube_rewards

class UnavailableTaufa:
    status='UNAVAILABLE_NO_OFFICIAL_API_CONFIGURED'
    def reconcile(self,snapshot):
        # No request is sent when no authorized private cache is configured.
        return None

class Application:
    def __init__(self,store,*,scheduler_secret='',taufa=None):
        self.store=store;self.scheduler_secret=scheduler_secret;self.taufa=taufa or UnavailableTaufa()
    def cycle(self,now=None):
        now=now or datetime.now(timezone.utc)
        created=self.store.tick(now)
        processed=[]
        for _ in range(100):
            request=self.store.claim(now)
            if not request:break
            try:
                result=self.taufa.reconcile(json.loads(request['snapshot']))
                status=self.store.finish(request,result,now=datetime.now(timezone.utc))
                processed.append({'request_id':request['request_id'],'status':status})
            except Exception as error:
                # Retries keep the same request ID. Failed AI cannot block delivery.
                with self.store.transaction() as tx:
                    tx.execute("UPDATE points_requests SET last_error=?,status=?,lease_until=0 WHERE request_id=? AND attempt=? AND status='RUNNING'",
                        (getattr(error,'code',type(error).__name__),'FAILED' if request['attempt']>=3 else 'QUEUED',request['request_id'],request['attempt']))
                break
        return {'created':created,'processed':processed}
    def __call__(self,env,start):
        status=200
        try:
            path=env.get('PATH_INFO','');method=env.get('REQUEST_METHOD','GET')
            if path!='/health' and env.get('wsgi.url_scheme')!='https' and os.environ.get('STRICTROUTINES_POINTS_TEST_HTTP')!='1':raise PermissionError('HTTPS is required')
            if path=='/health' and method=='GET':result={'status':'OK','taufa':self.taufa.status}
            elif path=='/internal/reconcile/tick' and method=='POST':
                supplied=env.get('HTTP_AUTHORIZATION','').removeprefix('Bearer ')
                if not self.scheduler_secret or not hmac.compare_digest(supplied,self.scheduler_secret):raise PermissionError('Scheduler authentication required')
                result=self.cycle()
            else:
                if env.get('wsgi.url_scheme')!='https' and os.environ.get('STRICTROUTINES_POINTS_TEST_HTTP')!='1':raise PermissionError('HTTPS is required')
                auth=env.get('HTTP_AUTHORIZATION','')
                if not auth.startswith('Bearer '):raise PermissionError('Device credential required')
                principal=self.store.authenticate(env.get('HTTP_X_DEVICE_ID',''),auth[7:])
                if path in {'/v1/demo-access/state','/v1/demo-access/activate'} and method in {'GET','POST'}:
                    if (path.endswith('/state') and method != 'GET') or (path.endswith('/activate') and method != 'POST'):
                        raise ValueError('Invalid demo method')
                    # No submitted score, account ID or client timestamp is used.
                    if method == 'POST':
                        length=int(env.get('CONTENT_LENGTH') or 0)
                        if not 0 <= length <= 1024: raise ValueError('Invalid demo request size')
                        if length: env['wsgi.input'].read(length)
                    result=demo_access_state(self.store,principal,activate=method=='POST')
                elif path=='/v1/rewards/youtube/state' and method=='GET':
                    result=youtube_rewards.state(self.store,principal)
                elif path.startswith('/v1/rewards/youtube/strict-study/') and method=='POST':
                    length=int(env.get('CONTENT_LENGTH') or 0)
                    if not 0<length<=4096: raise ValueError('Invalid study receipt size')
                    body=json.loads(env['wsgi.input'].read(length))
                    result=youtube_rewards.strict_study(self.store,principal,path.rsplit('/',1)[-1],body)
                elif path in {'/v1/rewards/youtube/reconcile','/v1/rewards/youtube/activate','/v1/rewards/youtube/stop'} and method=='POST':
                    length=int(env.get('CONTENT_LENGTH') or 0)
                    if not 0<length<=200_000: raise ValueError('Invalid reward request size')
                    body=json.loads(env['wsgi.input'].read(length))
                    if path.endswith('/reconcile'):
                        result=youtube_rewards.reconcile(self.store,principal,body['submissions'])
                    else:
                        result=youtube_rewards.activate(self.store,principal,body['reward_id'],stop=path.endswith('/stop'))
                elif path=='/v1/points/state' and method=='GET':
                    date=parse_qs(env.get('QUERY_STRING','')).get('date',[datetime.now(IST).date().isoformat()])[0]
                    datetime.strptime(date,'%Y-%m-%d')
                    result=self.store.state(principal,date)
                elif path in {'/v1/points/checkpoint','/v1/points/proposals'} and method=='POST':
                    length=int(env.get('CONTENT_LENGTH') or 0)
                    if not 0<length<=2_000_000:raise ValueError('Invalid request size')
                    body=json.loads(env['wsgi.input'].read(length))
                    if not isinstance(body,dict):raise ValueError('Request must be a JSON object')
                    result=self.store.publish(principal,body) if path.endswith('/checkpoint') else self.store.propose(principal,body)
                    if path.endswith('/checkpoint'):result['accepted_source_sha256']=body.get('sha256')
                else:status=404;result={'error':'Unknown endpoint'}
        except DemoUnavailable as e:status=409;result={'success':False,'code':'DEMO_COOLDOWN','error':'Demo access is limited to once every seven full days.','demo_access':e.state}
        except PermissionError as e:status=401;result={'code':'AUTHENTICATION_REQUIRED','error':str(e)}
        except (ValueError,KeyError,TypeError,OverflowError):status=409;result={'code':'RECONCILIATION_REQUIRED','error':'Invalid, stale or conflicting request; retained ledger unchanged'}
        except Exception:
            logging.exception('Cloud points request failed')
            status=503;result={'code':'BACKEND_UNAVAILABLE','error':'Points service unavailable; retain pending changes and retry'}
        raw=canonical(result).encode()
        start(f'{status} '+{200:'OK',401:'Unauthorized',404:'Not Found',409:'Conflict',503:'Service Unavailable'}[status],
            [('Content-Type','application/json'),('Content-Length',str(len(raw))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff')])
        return [raw]

def create_app():
    store=Store(os.environ['STRICTROUTINES_POINTS_DATABASE_URL']);store.initialize()
    # API authorization is not available yet. Keep scheduled requests retryable
    # and ordinary synchronization running; never reuse browser-cookie captures.
    return Application(store,scheduler_secret=os.environ.get('STRICTROUTINES_POINTS_SCHEDULER_SECRET',''))

def run_worker():
    app=create_app();stopped=threading.Event()
    import signal
    for name in (signal.SIGINT,signal.SIGTERM):signal.signal(name,lambda *_:stopped.set())
    while not stopped.is_set():
        try:app.cycle()
        except Exception:logging.exception('Coordinator unavailable; next cycle will catch up once')
        stopped.wait(30)
