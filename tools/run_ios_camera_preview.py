"""Disposable timer/video preview. Never opens the production DB or a camera.

Requires an explicitly approved HTTPS tunnel to 127.0.0.1:54411. It is a
single-device test, has no TURN relay, and exits after 15 minutes.
"""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '.tmp/ios-camera-deps'))

from aiohttp import web
from core.strict_ios_camera import StrictIOSCamera, trusted_origin
from core.strict_ios_camera_client import HTML, JS
from windows.strict_ios_camera_gateway import IOSCameraGateway
import windows.strict_ios_camera_gateway as gateway_module
from windows.strict_ios_receiver import IOSVideoReceiver


class DB:
    def __init__(self):
        self.anchor = sqlite3.connect('file:ios_preview?mode=memory&cache=shared', uri=True)
    def _connect(self):
        connection = sqlite3.connect('file:ios_preview?mode=memory&cache=shared', uri=True)
        connection.row_factory = sqlite3.Row
        return connection


class Preview(IOSCameraGateway):
    def __init__(self, origin):
        self.db = DB()
        self.created = time.time()
        self.joined_at = None
        self.companion = secrets.token_urlsafe(32)
        self.entry = secrets.token_urlsafe(32)
        self.entry_used = False
        self.pid, self.sid = 'preview_preparation', 'preview_session'
        with self.db._connect() as c:
            c.executescript('''CREATE TABLE strict_preparations(preparation_id TEXT PRIMARY KEY,
                account_id TEXT,status TEXT,expires_at REAL);
                CREATE TABLE strict_join_participants(preparation_id TEXT,role TEXT,credential_hash TEXT);''')
            c.execute('INSERT INTO strict_preparations VALUES(?,?,?,?)', (self.pid, 'preview', 'ACTIVE', self.created+900))
            c.execute('INSERT INTO strict_join_participants VALUES(?,?,?)', (self.pid, 'IOS', hashlib.sha256(self.companion.encode()).hexdigest()))
        store = StrictIOSCamera(self.db, session_validator=lambda account,pid,sid:
            (account,pid,sid)==('preview',self.pid,self.sid) and time.time()<self.created+900)
        manager = SimpleNamespace(ios_camera=store)
        super().__init__(manager, dict(origin=origin))

    async def _page(self, request):
        page = HTML.replace('/ios-camera/client.js', '/ios-camera/client.js?bootstrap=1')
        page = page.replace('iPhone Camera Companion', 'Timer + Camera Preview')
        page = page.replace('<section class="session-box">',
            '<p class="fine">PREVIEW ONLY — temporary Windows receiver, not an active EXE Strict Session. No points or rewards change.</p><section class="session-box">')
        return web.Response(text=page, content_type='text/html')

    async def _script(self, request):
        if 'bootstrap' not in request.query:
            return web.Response(text=JS, content_type='application/javascript')
        return web.Response(content_type='application/javascript', text=r'''
(async()=>{
 const token=location.hash.slice(1);history.replaceState(null,'',location.pathname+location.search);
 try{
  const response=await fetch('/preview/enroll',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
  const data=await response.json();if(!response.ok)throw Error(data.error||'Preview link expired');
  localStorage.setItem('strictrecords.timer.binding.v2',JSON.stringify(data.binding));
  history.replaceState(null,'','/ios-camera?preparation_id='+data.binding.preparation_id+'#'+data.camera_token);
  await import('/ios-camera/client.js');
 }catch(error){document.getElementById('cameraState').textContent=error.message;}
})();''')

    async def enroll(self, request):
        body = await self._body(request)
        if self.entry_used or time.time()>getattr(self, 'entry_expires', self.created) or not secrets.compare_digest(str(body.get('token','')), self.entry):
            raise PermissionError('Preview link expired or already used.')
        if not self.ready:
            return web.json_response({'error':'Trusted HTTPS verification is still pending. Retry shortly.'},status=503)
        self.entry_used = True
        self.joined_at = time.time()
        invitation = self.store.invite('preview', self.pid, self.sid)['invitation']
        return web.json_response(dict(binding=dict(preparation_id=self.pid,role='IOS',credential=self.companion,
            expires_at=(self.created+900)*1000),camera_token=invitation['url'].split('#')[1]))

    async def timer(self, request):
        body=await self._body(request)
        if body.get('preparation_id')!=self.pid or not secrets.compare_digest(str(body.get('credential','')),self.companion):
            raise PermissionError('Invalid preview timer credential.')
        remaining=max(0,600-(time.time()-(self.joined_at or time.time())))
        return web.json_response(dict(success=True,timer=dict(session_id=self.sid,remaining_seconds=remaining,
            running=True,label='10-minute preview · no points')))

    async def status(self, request):
        # Receiver diagnostics only, no private video or credentials on public route.
        if request.remote not in ('127.0.0.1','::1') or request.headers.get('Host')!='127.0.0.1:54411':
            return web.Response(status=403)
        return web.json_response(self.store.status('preview',self.pid))

    async def run(self):
        @web.middleware
        async def security(request, handler):
            if request.path=='/preview/local-status':return await handler(request)
            if request.host!=self.origin.removeprefix('https://'):
                return web.Response(status=403)
            if request.method!='GET' or request.path.endswith('/signaling'):
                if request.headers.get('Origin')!=self.origin:return web.Response(status=403)
            try:response=await handler(request)
            except (PermissionError,ValueError,TypeError,KeyError):
                response=web.json_response({'success':False,'error':'Preview authorization failed or link expired.'},status=403)
            self._headers(response)
            return response
        app=web.Application(client_max_size=32768,middlewares=[security])
        for path,handler in [('/ios-camera',self._page),('/strict-session/companion',self._page),('/ios-camera/client.js',self._script),
            ('/ios-camera/client.css',self._style),('/ios-camera/health',self._health),
            ('/ios-camera/signaling',self._signal),('/preview/local-status',self.status)]:
            app.router.add_get(path,handler)
        for path,handler in [('/preview/enroll',self.enroll),('/ios-camera/redeem',self._redeem),
            ('/ios-camera/opened',self._opened),('/ios-camera/refresh',self._refresh),
            ('/ios-camera/status',self._status),('/api/strict_session/preparation/heartbeat',self.timer)]:
            app.router.add_post(path,handler)
        # Same-Wi-Fi test only: no paid or unauthenticated third-party TURN server.
        gateway_module.temporary_ice_servers=lambda config,identity,now=None: []
        async def frame(identity,pixels):
            ws=self.sockets.get(identity['camera_id'])
            observed=self.store.receiver_identity(identity['camera_id'])
            if ws and not ws.closed:
                await ws.send_json(dict(type='receiver_frames',session_id=self.sid,
                    frames_received=observed['frame_sequence'],last_frame_at=time.time()))
        self.receiver=IOSVideoReceiver(self.store,on_frame=frame)
        self.runner=web.AppRunner(app,access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner,'127.0.0.1',54411).start()
        await self._verify_tls()
        if not self.ready:raise RuntimeError(self.error)
        import qrcode
        directory=ROOT/'reports/ios-camera-preview-20261105';directory.mkdir(parents=True,exist_ok=True)
        self.entry_expires=time.time()+180
        url=self.origin+'/strict-session/companion?preparation_id='+self.pid+'#'+self.entry
        qrcode.make(url).save(directory/'ios-preview-qr.png')
        (directory/'preview.json').write_text(json.dumps(dict(url=url,expires_at=self.entry_expires,
            local_status='http://127.0.0.1:54411/preview/local-status',demo=True)),encoding='utf-8')
        print('HTTPS preview verified; QR is ready.',flush=True)
        try:await asyncio.sleep(max(0,self.created+900-time.time()))
        finally:
            self.store.stop_session(self.sid)
            await self.receiver.close()
            await self.runner.cleanup()


if __name__=='__main__':
    asyncio.run(Preview(trusted_origin(sys.argv[1])).run())
