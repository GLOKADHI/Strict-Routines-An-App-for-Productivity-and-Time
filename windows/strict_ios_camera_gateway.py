import inspect

"""Opt-in, origin-restricted HTTPS/WSS ingress for video-only iOS WebRTC.



This listener exposes no dashboard, ledger, admin or general backend routes.

Media is terminated by aiortc on Windows; WSS carries signaling only.

"""

import asyncio

import base64

import hashlib

import hmac

import importlib.util

import json

import os

import secrets

import ssl

import threading

import time

from urllib.parse import urlsplit
import logging
logger = logging.getLogger("IOSCameraGateway")
import logging

logger = logging.getLogger("IOSCameraGateway")





def camera_configuration(env=None, db=None):

    env = os.environ if env is None else env

    origin = env.get('STRICTRECORDS_IOS_CAMERA_ORIGIN', '').rstrip('/')

    is_cloudflare = False

    mgr = None

    try:

        from windows.cloudflare_tunnel_manager import CloudflareTunnelManager

        mgr = CloudflareTunnelManager.get_instance(db=db)

        if mgr.public_origin:

            origin = mgr.public_origin.rstrip('/')

            is_cloudflare = True

    except Exception:

        pass



    if not origin and db and mgr and mgr.mode == "NAMED":

        try:

            with db._connect() as conn:

                cols = [r[1] for r in conn.execute("PRAGMA table_info(system_config)").fetchall()]

                key_col = "config_key" if "config_key" in cols else "key"

                val_col = "config_value" if "config_value" in cols else "value"

                row = conn.execute(f"SELECT {val_col} FROM system_config WHERE {key_col} = 'CLOUDFLARE_PUBLIC_HOSTNAME'").fetchone()

                if row and row[0]:

                    saved = str(row[0]).strip().rstrip('/')

                    if not saved.startswith("http"):

                        saved = "https://" + saved

                    origin = saved

                    is_cloudflare = True

        except Exception:

            pass



    if not origin:

        reason = "Cloudflare Tunnel is starting; waiting for public HTTPS address..." if (mgr and mgr.is_running()) else "Trusted iOS camera HTTPS domain is not configured."

        return {'configured': False, 'reason': reason}

    from core.strict_ios_camera import trusted_origin

    origin = trusted_origin(origin)

    proxy = env.get('STRICTRECORDS_IOS_CAMERA_PROXY') == '1' or is_cloudflare or ('trycloudflare.com' in origin)

    port = int(env.get('STRICTRECORDS_IOS_CAMERA_PORT', '9443'))

    if not 1024 <= port <= 65535:

        raise ValueError('Camera listener port must be between 1024 and 65535.')

    result = dict(configured=True, origin=origin, proxy=proxy, port=port,

                  is_cloudflare=is_cloudflare or ('trycloudflare.com' in origin),

                  cert=env.get('STRICTRECORDS_IOS_CAMERA_CERT', ''),

                  key=env.get('STRICTRECORDS_IOS_CAMERA_KEY', ''),

                  proxy_secret=env.get('STRICTRECORDS_IOS_CAMERA_PROXY_SECRET', ''),

                  turn_url=env.get('STRICTRECORDS_IOS_CAMERA_TURN_URL', ''),

                  turn_secret=env.get('STRICTRECORDS_IOS_CAMERA_TURN_SECRET', ''))

    if proxy and not result['is_cloudflare'] and len(result['proxy_secret']) < 32:

        raise ValueError('The loopback TLS proxy requires a random secret of at least 32 characters.')

    if not proxy and (not result['cert'] or not result['key']):

        raise ValueError('A trusted certificate full chain and private key are required.')

    if result.get('turn_url') and not result['turn_url'].startswith('turns:'):

        raise ValueError('TURN URL must use turns: scheme.')

    if result.get('turn_secret') and len(result['turn_secret']) < 32:

        raise ValueError('TURN secret must be at least 32 characters.')

    return result





def temporary_ice_servers(config, identity, now=None):

    """Generates short-lived ICE servers: uses configured TURN, Cloudflare Realtime TURN, or STUN fallbacks."""

    if config.get('turn_secret') and config.get('turn_url'):

        now = time.time() if now is None else now

        username = str(int(now) + 600) + ':' + identity['camera_id']

        password = base64.b64encode(hmac.new(config['turn_secret'].encode(), username.encode(), hashlib.sha1).digest()).decode()

        return [dict(urls=[config['turn_url']], username=username, credential=password)]



    try:

        from core.cloudflare_turn_service import CloudflareTURNService

        turn_svc = CloudflareTURNService.get_instance()

        servers = turn_svc.generate_ice_servers(

            session_id=identity.get('session_id', ''),

            camera_id=identity.get('camera_id', '')

        )

        if servers:

            return servers

    except Exception:

        pass



    return [

        {'urls': ['stun:stun.cloudflare.com:3478']},

        {'urls': ['stun:stun.l.google.com:19302']},

    ]





class IOSCameraGateway:

    def __init__(self, manager, config):

        self.manager, self.store, self.config = manager, manager.ios_camera, config

        self.origin = config['origin']

        self.ready = False

        self.error = 'Trusted TLS endpoint has not been verified.'

        from urllib.parse import urlsplit


        self.verification_details = {

            "hostname": urlsplit(self.origin).hostname or "",

            "dns_resolved": False,

            "https_reachable": False,

            "companion_route_ok": False,

            "wss_signaling_ok": False,

            "verified_at": 0,

            "error": "Verification pending..."

        }

        self.loop = self.thread = self.runner = self.receiver = None

        self.sockets = {}

        self.probe = secrets.token_urlsafe(32)

        self.started = threading.Event()

        self._last_preview = {}

        self._preview_lock = threading.Lock()

        self._monitors = {}



    def start(self):

        if not importlib.util.find_spec('aiortc'):

            self.error = 'WebRTC receiver is unavailable: install requirements-ios-camera.txt.'

            return self

        def run():

            self.loop = asyncio.new_event_loop()

            asyncio.set_event_loop(self.loop)

            try:

                self.loop.run_until_complete(self._start())

                self.loop.run_forever()

            except Exception as error:

                self.error = 'Camera listener could not start: ' + type(error).__name__

            finally:

                self.ready = False

                self.started.set()

                self.loop.run_until_complete(self._cleanup())

                self.loop.close()

        self.thread = threading.Thread(target=run, name='StrictIOSCameraTLS', daemon=True)

        self.thread.start()

        return self



    async def _start(self):

        from aiohttp import web

        from windows.strict_ios_receiver import IOSVideoReceiver

        @web.middleware

        async def security(request, handler):

            if request.host.lower() != urlsplit(self.origin).netloc.lower():

                return web.Response(status=403)

            if self.config['proxy']:

                if self.config.get('is_cloudflare'):

                    if request.remote not in ('127.0.0.1', '::1'):

                        return web.Response(status=403)

                else:

                    provided = request.headers.get('X-StrictRecords-Proxy', '')

                    if request.remote not in ('127.0.0.1', '::1') or not hmac.compare_digest(provided, self.config['proxy_secret']):

                        return web.Response(status=403)

            if request.method != 'GET' or request.path.endswith('/signaling'):

                if request.headers.get('Origin') != self.origin:

                    return web.Response(status=403)

            if request.headers.get('Sec-Fetch-Site') == 'cross-site' and request.method != 'GET':

                return web.Response(status=403)

            try:

                response = await handler(request)

            except (ValueError, TypeError, KeyError):

                response = web.json_response({'success': False, 'error': 'Invalid, expired or reused camera request.'}, status=409)

            except PermissionError:

                response = web.json_response({'success': False, 'error': 'Camera authorization failed.'}, status=403)

            self._headers(response)

            if response.get('timer_csp'):

                response.headers['Content-Security-Policy'] = response['timer_csp']

            return response

        app = web.Application(client_max_size=32768, middlewares=[security])

        app.router.add_get('/ios-camera/health', self._health)

        app.router.add_get('/ios-camera', self._page)

        app.router.add_get('/ios-camera/join', self._join_page)

        app.router.add_get('/ios-camera/join.js', self._join_script)

        app.router.add_get('/ios-camera/client.js', self._script)

        app.router.add_get('/ios-camera/client.css', self._style)

        app.router.add_post('/ios-camera/redeem', self._redeem)

        app.router.add_post('/ios-camera/refresh', self._refresh)

        app.router.add_post('/ios-camera/status', self._status)

        app.router.add_post('/ios-camera/opened', self._opened)

        app.router.add_post('/ios-camera/grant', self._grant)

        app.router.add_get('/ios-camera/signaling', self._signal)

        # Canonical companion and timer identity on trusted HTTPS origin

        app.router.add_get('/strict-session/companion', self._timer_page)

        app.router.add_post('/api/strict_session/preparation/join', self._timer_join)

        app.router.add_post('/api/strict_session/preparation/heartbeat', self._timer_heartbeat)
        app.router.add_post('/ios-camera/heartbeat', self._media_heartbeat)
        app.router.add_post('/api/strict_session/ios/media_heartbeat', self._media_heartbeat)
        app.router.add_post('/api/strict_session/ios/timer_heartbeat', self._ios_timer_heartbeat)
        app.router.add_post('/ios-camera/timer_heartbeat', self._ios_timer_heartbeat)
        app.router.add_post('/ios-camera/media_stopped', self._media_stopped)
        app.router.add_post('/api/strict_session/ios/media_stopped', self._media_stopped)

        recv_sig = inspect.signature(IOSVideoReceiver.__init__)

        recv_kw = {}

        if 'on_stage_ack' in recv_sig.parameters:

            recv_kw['on_stage_ack'] = self._send_stage_ack

        if 'on_keyframe_needed' in recv_sig.parameters:

            recv_kw['on_keyframe_needed'] = self._send_keyframe_request

        self.receiver = IOSVideoReceiver(self.store, on_frame=self._frame, **recv_kw)

        self.runner = web.AppRunner(app, access_log=None)

        await self.runner.setup()

        context = None

        if not self.config['proxy']:

            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

            context.minimum_version = ssl.TLSVersion.TLSv1_2

            context.load_cert_chain(self.config['cert'], self.config['key'])

        site = web.TCPSite(self.runner, '127.0.0.1' if self.config['proxy'] else '0.0.0.0', self.config['port'], ssl_context=context)

        await site.start()

        asyncio.create_task(self._verify_tls())

        asyncio.create_task(self._watchdog())

        self.started.set()



    async def _send_stage_ack(self, camera_id: str, payload: dict):

        ws = self.sockets.get(camera_id)

        if ws and not ws.closed:

            try:

                await ws.send_json(payload)

            except Exception:

                pass



    async def _send_keyframe_request(self, camera_id: str):

        ws = self.sockets.get(camera_id)

        if ws and not ws.closed:

            try:

                await ws.send_json({"type": "request_keyframe", "timestamp": time.time()})

            except Exception:

                pass



    def _headers(self, response, script_hash=None):

        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',

            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',

            'Permissions-Policy': 'camera=(self), microphone=(), display-capture=(), fullscreen=(self)',

            'Strict-Transport-Security': 'max-age=300', 'Cross-Origin-Resource-Policy': 'same-origin'})

        script = "'self'" if not script_hash else "'sha256-" + script_hash + "'"

        response.headers['Content-Security-Policy'] = (

            "default-src 'none'; script-src " + script + "; style-src 'self' 'unsafe-inline'; "

            "connect-src 'self' " + self.origin.replace('https:', 'wss:', 1) + "; "

            "media-src 'self' blob:; img-src 'self' data:; object-src 'none'; base-uri 'none'; "

            "frame-ancestors 'none'; form-action 'self'; require-trusted-types-for 'script'")



    async def _verify_tls(self):

        import socket

        from urllib.parse import urlsplit


        from aiohttp import ClientSession, ClientTimeout



        parsed = urlsplit(self.origin)

        host = parsed.hostname or ''

        port = parsed.port or (443 if parsed.scheme == 'https' else 80)



        # Temporary hostnames may appear before public DNS has propagated.

        # Keep retrying with diagnostics until verified or listener shutdown.

        while True:

            try:

                # 1. DNS Resolution check

                try:

                    await asyncio.to_thread(socket.getaddrinfo, host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)

                    self.verification_details["dns_resolved"] = True

                except Exception as dns_err:

                    self.verification_details["dns_resolved"] = False

                    err_msg = f"DNS resolution failed for '{host}'. Ensure your Cloudflare Tunnel is running and public DNS has propagated."

                    self.verification_details["error"] = err_msg

                    self.error = err_msg

                    await asyncio.sleep(4)

                    continue



                # 2. HTTPS Health Probe check

                async with ClientSession(timeout=ClientTimeout(total=8)) as client:

                    async with client.get(self.origin + '/ios-camera/health', allow_redirects=False) as response:

                        if response.status != 200:

                            raise ValueError(f"HTTPS health probe returned status {response.status}")

                        result = await response.json()

                        if result.get('probe') != self.probe:

                            raise ValueError('Probe token mismatch: Domain reaches a different service, not this instance.')

                        self.verification_details["https_reachable"] = True



                # 3. Companion Route check

                async with ClientSession(timeout=ClientTimeout(total=8)) as client:

                    async with client.get(self.origin + '/strict-session/companion', allow_redirects=False) as response:

                        if response.status != 200:

                            raise ValueError(f"Companion route returned status {response.status}")

                        self.verification_details["companion_route_ok"] = True



                # 4. Secure WSS Signaling check

                async with ClientSession(timeout=ClientTimeout(total=8)) as client:

                    wss_url = self.origin.replace('https:', 'wss:', 1) + '/ios-camera/signaling'

                    async with client.ws_connect(wss_url, origin=self.origin,

                            headers={'X-StrictRecords-Probe': self.probe}, timeout=5) as ws:

                        result = await ws.receive_json(timeout=5)

                        if result.get('probe') != self.probe:

                            raise ValueError('WSS probe reaches a different camera instance.')

                        await ws.close()

                        self.verification_details["wss_signaling_ok"] = True



                # All checks succeeded!

                self.verification_details["verified_at"] = time.time()

                self.verification_details["error"] = ""

                self.store.configure(self.origin, enabled=True, tls_verified=True)

                self.ready, self.error = True, ""

                return



            except Exception as error:

                err_type = type(error).__name__

                if "DNS" in err_type or (isinstance(error, (socket.gaierror, OSError)) and "getaddrinfo" in str(error)):

                    self.error = f"DNS resolution failed for '{host}' ({err_type})."

                elif isinstance(error, ValueError):

                    self.error = f"HTTPS verification error: {error}"

                else:

                    self.error = f"Trusted HTTPS probe failed: {err_type} - {error}"



                self.verification_details["error"] = self.error

                await asyncio.sleep(4)



    async def _health(self, request):

        from aiohttp import web

        return web.json_response({'success': True, 'probe': self.probe, 'tls_verified': self.ready})



    async def _page(self, request):

        from aiohttp import web

        from core.strict_ios_camera_client import HTML

        return web.Response(text=HTML, content_type='text/html')



    async def _script(self, request):

        from aiohttp import web

        from core.strict_ios_camera_client import JS

        return web.Response(text=JS, content_type='application/javascript')



    async def _join_page(self, request):

        from aiohttp import web

        from core.strict_ios_camera_client import HTML

        return web.Response(text=HTML.replace('/ios-camera/client.js','/ios-camera/join.js'), content_type='text/html')



    async def _join_script(self, request):

        from aiohttp import web

        from core.strict_ios_join import BOOTSTRAP

        return web.Response(text=BOOTSTRAP, content_type='application/javascript')



    async def _grant(self, request):

        """Registered timer requests camera setup before or during its host session."""

        from aiohttp import web

        body = await self._body(request)

        pid, credential = body.get('preparation_id'), body.get('companion_credential')

        if not isinstance(credential,str) or not 32 <= len(credential) <= 128:

            raise PermissionError('Registered iOS timer required.')

        with self.manager.db._connect() as c:

            prep = c.execute('SELECT account_id FROM strict_preparations WHERE preparation_id=?',(pid,)).fetchone()

            participant = c.execute("SELECT credential_hash FROM strict_join_participants WHERE preparation_id=? AND role='IOS'",(pid,)).fetchone()

        if not prep or not participant or not hmac.compare_digest(participant['credential_hash'],hashlib.sha256(credential.encode()).hexdigest()):

            raise PermissionError('Registered iOS timer required.')

        session_id = f"prep_{pid}"

        if not self.store.session_validator or not self.store.session_validator(prep['account_id'],pid,session_id):

            return web.json_response({'success':False,'error':'Open the current Strict Session prerequisite page and reconnect this iOS timer.'},status=409)

        # The authority validates active session identity again at issue/redemption.

        result = self.store.invite(prep['account_id'], pid, session_id, self.origin)

        return web.json_response(dict(success=True, token=result['invitation']['url'].split('#',1)[1]))



    async def _style(self, request):

        from aiohttp import web

        from core.strict_ios_camera_client import CSS

        return web.Response(text=CSS, content_type='text/css')



    def _credential_response(self, result):

        result = dict(result)

        result['camera_credential'] = result.pop('credential')

        result['credential_expires_at'] = result.pop('expires_at')

        result['ice_servers'] = temporary_ice_servers(self.config, result)

        return result



    async def _timer_page(self, request):

        from aiohttp import web

        if request.query.get('preparation_id'):

            from core.strict_ios_camera_client import HTML

            page = HTML

            if '/ios-camera/join.js' not in page:

                page = page.replace('/ios-camera/client.js', '/ios-camera/join.js')

            return web.Response(text=page, content_type='text/html')

        from core.strict_companion import HTML

        from core.strict_companion_client import JS

        response = web.Response(text=HTML, content_type='text/html')

        self._headers(response, base64.b64encode(hashlib.sha256(JS.encode()).digest()).decode())

        response['timer_csp'] = response.headers['Content-Security-Policy']

        return response



    async def _timer_join(self, request):

        from aiohttp import web

        body = await self._body(request)

        return web.json_response(self.manager.preparation.join(body.get('token', ''), request.headers.get('User-Agent', ''), body.get('client_nonce'), body.get('client_info')))



    async def _timer_heartbeat(self, request):

        from aiohttp import web

        body = await self._body(request)

        return web.json_response(self.manager.preparation.heartbeat(body.get('preparation_id'), body.get('role'), body.get('credential'), body.get('report') or {}))

    async def _ios_timer_heartbeat(self, request):
        from aiohttp import web
        body = await self._body(request)
        cred = body.get('credential') or body.get('camera_credential') or body.get('device_id')
        identity = None
        if cred:
            try:
                identity = self.store.authenticate(cred)
            except Exception:
                pass
        pid = (identity.get('preparation_id') if identity else None) or body.get('preparation_id') or getattr(self.manager, '_active_preparation', '')
        acct = (identity.get('account_id') if identity else None) or getattr(self.manager, 'user_id', '')

        # 1. Update strict_join_participants seen to keep timer connection alive in DB
        try:
            with self.manager.db._connect() as c:
                c.execute(
                    "UPDATE strict_join_participants SET seen=? WHERE role='IOS' AND (preparation_id=? OR credential_hash=?)",
                    (time.time(), pid, digest(str(cred)))
                )
        except Exception:
            pass

        # 2. Query authoritative timer state
        timer_info = {}
        try:
            timer_info = self.manager._companion_timer(pid, acct)
        except Exception:
            pass

        sess_state = "READY"
        try:
            sess_state = self.manager.get_lifecycle_state().value if hasattr(self.manager, "get_lifecycle_state") else str(getattr(self.manager, "state", "READY"))
        except Exception:
            pass

        return web.json_response({
            'type': 'IOS_TIMER_HEARTBEAT_ACK',
            'success': True,
            'session_id': body.get('session_id') or getattr(self.manager, 'session_id', None) or '',
            'connection_id': body.get('connection_id', ''),
            'sequence_number': body.get('sequence_number', 0),
            'server_timestamp': time.time(),
            'authoritative_timer_state': timer_info,
            'authoritative_session_state': sess_state,
        })

    async def _media_heartbeat(self, request):
        from aiohttp import web
        body = await self._body(request)
        cred = body.get('credential') or body.get('camera_credential') or body.get('token')
        identity = None
        if cred:
            try:
                identity = self.store.authenticate(cred)
            except Exception:
                pass
        if not identity and not self.ready:
            return web.json_response({'success': False, 'error': 'Unauthorized'}, status=401)
        try:
            from windows.strict_ios_media_health import IOSMediaHealthManager
            mgr = IOSMediaHealthManager.get_instance()
            mgr.record_multi_source_heartbeat(body, channel="HTTPS")
        except Exception as ex:
            logger.debug(f"HTTPS heartbeat error: {ex}")
        return web.json_response({'success': True, 'timestamp': time.time(), 'status': 'ACKNOWLEDGED'})

    async def _media_stopped(self, request):
        from aiohttp import web
        body = await self._body(request)
        cred = body.get('credential') or body.get('camera_credential') or body.get('token')
        identity = None
        if cred:
            try:
                identity = self.store.authenticate(cred)
            except Exception:
                pass
        if not identity and not self.ready:
            return web.json_response({'success': False, 'error': 'Unauthorized'}, status=401)
        source_id = body.get('source_id', 'IOS_FRONT_CAMERA')
        try:
            from windows.strict_ios_media_health import IOSMediaHealthManager
            mgr = IOSMediaHealthManager.get_instance()
            mgr.record_explicit_stop(source_id, body)
        except Exception as ex:
            logger.debug(f"HTTPS media stopped error: {ex}")
        return web.json_response({'success': True, 'timestamp': time.time(), 'status': 'ACKNOWLEDGED'})



    async def _redeem(self, request):

        from aiohttp import web

        if not self.ready:

            return web.json_response({'success': False, 'error': self.error}, status=503)

        body = await self._body(request)

        result = self.store.redeem(body.get('token'), body.get('preparation_id'), body.get('companion_credential'))

        return web.json_response(self._credential_response(result))



    async def _refresh(self, request):

        from aiohttp import web

        body = await self._body(request)

        result = self.store.rotate(body.get('camera_credential'))

        return web.json_response(self._credential_response(result))



    async def _status(self, request):

        from aiohttp import web

        body = await self._body(request)

        return web.json_response(self.store.client_state(body.get('camera_credential'), body.get('state'), body.get('diagnostics') or {}))



    async def _opened(self, request):

        from aiohttp import web

        body = await self._body(request)

        return web.json_response(self.store.opened(body.get('token')))



    @staticmethod

    async def _body(request):

        if request.content_type != 'application/json':

            raise ValueError('JSON required.')

        body = await request.json()

        if not isinstance(body, dict):

            raise ValueError('JSON object required.')

        return body



    async def _signal(self, request):

        from aiohttp import web, WSMsgType

        # Exact Host/Origin middleware still applies. This health-only handshake

        # breaks the readiness/probe cycle without authorizing camera signaling.

        if hmac.compare_digest(request.headers.get('X-StrictRecords-Probe', ''), self.probe):

            probe_socket = web.WebSocketResponse(max_msg_size=1024)

            self._headers(probe_socket)

            await probe_socket.prepare(request)

            await probe_socket.send_json({'success': True, 'probe': self.probe})

            await probe_socket.close()

            return probe_socket

        if not self.ready:

            return web.Response(status=503)

        ws = web.WebSocketResponse(max_msg_size=65536, heartbeat=10)

        self._headers(ws)

        await ws.prepare(request)

        credential = None

        camera_id = None

        try:

            message = await asyncio.wait_for(ws.receive(), 5)

            if message.type != WSMsgType.TEXT:

                raise PermissionError('Authentication required.')

            body = json.loads(message.data)

            if not isinstance(body, dict) or body.get('type') != 'authenticate':

                raise PermissionError('Authentication required.')

            credential = body.get('credential')

            identity = self.store.authenticate(credential)

            camera_id = identity['camera_id']

            old = self.sockets.get(camera_id)

            self.sockets[camera_id] = ws

            if old:

                await old.close(code=4000)



            try:

                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

                mon = IPadCameraStreamMonitor.get_instance()

                mon.record_detailed_stage(

                    "1_ios_companion_invitation_auth",

                    session_id=identity.get("session_id", ""),

                    success=True

                )

                mon.record_detailed_stage(

                    "3_signaling_websocket_connect",

                    session_id=identity.get("session_id", ""),

                    signaling_state="AUTHENTICATED",

                    success=True

                )

            except Exception:

                pass



            ice = temporary_ice_servers(self.config, identity)

            await ws.send_json({'type': 'authenticated', 'ice_servers': ice})

            count, since = 0, time.monotonic()

            async for message in ws:

                if message.type != WSMsgType.TEXT:

                    break

                count += 1

                if time.monotonic() - since > 10:

                    count, since = 1, time.monotonic()

                if count > 100:

                    raise PermissionError('Signaling rate exceeded.')

                body = json.loads(message.data)

                if not isinstance(body, dict):

                    raise ValueError('Signaling object required.')

                kind = body.get('type')

                if kind == 'authenticate':

                    updated = self.store.authenticate(body.get('credential'))

                    if updated['camera_id'] != camera_id:

                        raise PermissionError('Participant changed.')

                    credential = body['credential']

                    ice = temporary_ice_servers(self.config, updated)

                    await self.receiver.reauthorize(credential)

                    continue

                self.store.authenticate(credential)

                if kind == 'offer':

                    self.receiver.ice_servers = ice

                    tx_id = body.get('tx_id')

                    conn_id = body.get('connection_id')

                    source_id = body.get('source_id')

                    import inspect

                    sig = inspect.signature(self.receiver.offer)

                    kw = {}

                    if 'tx_id' in sig.parameters and tx_id is not None: kw['tx_id'] = tx_id

                    if 'connection_id' in sig.parameters and conn_id is not None: kw['connection_id'] = conn_id

                    if 'source_id' in sig.parameters and source_id is not None: kw['source_id'] = source_id

                    ans = await self.receiver.offer(credential, body.get('sdp'), **kw)

                    await ws.send_json(ans)

                    try:

                        from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

                        IPadCameraStreamMonitor.get_instance().record_detailed_stage(

                            "11_sdp_answer_delivery_ios",

                            session_id=identity.get("session_id", ""),

                            connection_id=conn_id or "",

                            source_id=source_id or "IOS_FRONT_CAMERA",

                            success=True

                        )

                    except Exception:

                        pass

                elif kind == 'candidate':

                    sig_cand = inspect.signature(self.receiver.add_ice_candidate)

                    kw_cand = {}

                    if 'tx_id' in sig_cand.parameters and body.get('tx_id') is not None: kw_cand['tx_id'] = body.get('tx_id')

                    if 'connection_id' in sig_cand.parameters and body.get('connection_id') is not None: kw_cand['connection_id'] = body.get('connection_id')

                    if 'source_id' in sig_cand.parameters and body.get('source_id') is not None: kw_cand['source_id'] = body.get('source_id')

                    await self.receiver.add_ice_candidate(credential, body.get('candidate'), **kw_cand)

                elif kind == 'track_sources':

                    sources = body.get('sources') or {}

                    self.receiver.set_track_sources(camera_id, sources)

                elif kind == 'screen_negotiating':

                    try:

                        from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor

                        IPadScreenShareMonitor.get_instance().start_negotiation(

                            preparation_id=identity.get('preparation_id'),

                            force_restart=bool(body.get('force_restart', False)),

                        )

                    except Exception:

                        pass

                elif kind == 'IOS_MEDIA_HEARTBEAT':
                    try:
                        from windows.strict_ios_media_health import IOSMediaHealthManager
                        mgr = IOSMediaHealthManager.get_instance()
                        mgr.record_multi_source_heartbeat(body, channel="WSS")
                    except Exception as ex:
                        logger.debug(f"WSS media heartbeat processing error: {ex}")
                    ack_payload = {
                        'type': 'IOS_MEDIA_HEARTBEAT_ACK',
                        'heartbeat_seq': body.get('heartbeat_seq', 0),
                        'timestamp': time.time(),
                        'status': 'OK',
                    }
                    try:
                        await ws.send_json(ack_payload)
                    except Exception:
                        pass

                elif kind in ('IOS_CAMERA_STOPPED', 'camera_stopped'):
                    try:
                        from windows.strict_ios_media_health import IOSMediaHealthManager
                        IOSMediaHealthManager.get_instance().record_explicit_stop('IOS_FRONT_CAMERA', body)
                    except Exception:
                        pass

                    ev_id = body.get('event_id') or f"stop_cam_{int(time.time())}"

                    reason = body.get('reason') or 'iPad front camera stopped by user'

                    logger.warning(f"iOS Front Camera explicit stop received: {reason} (event_id={ev_id})")

                    try:

                        self.store.client_state(credential, 'IOS_CAMERA_STOPPED', body.get('diagnostics') or {})

                    except Exception:

                        pass

                    try:

                        from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

                        IPadCameraStreamMonitor.get_instance().record_explicit_stop(

                            source_id='IOS_FRONT_CAMERA',

                            event_id=ev_id,

                            reason=reason,

                            timestamp=body.get('timestamp') or time.time(),

                            user_initiated=bool(body.get('user_initiated', True)),

                        )

                    except Exception:

                        pass

                    # Acknowledge stop event back to iOS companion

                    ack_payload = {

                        'type': 'IOS_MEDIA_STOPPED_ACK',

                        'event_id': ev_id,

                        'source_id': 'IOS_FRONT_CAMERA',

                        'status': 'ACKNOWLEDGED',

                        'timestamp': time.time(),

                    }

                    try:

                        await ws.send_json(ack_payload)

                    except Exception:

                        pass



                elif kind in ('IOS_SCREEN_SHARE_STOPPED', 'screen_stopped'):
                    try:
                        from windows.strict_ios_media_health import IOSMediaHealthManager
                        IOSMediaHealthManager.get_instance().record_explicit_stop('IOS_FULL_SCREEN_SHARE', body)
                    except Exception:
                        pass

                    ev_id = body.get('event_id') or f"stop_scr_{int(time.time())}"

                    reason = body.get('reason') or 'iPad full-screen sharing stopped by user'

                    logger.warning(f"iOS Screen Share explicit stop received: {reason} (event_id={ev_id})")

                    try:

                        from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor

                        IPadScreenShareMonitor.get_instance().stop_track(

                            reason=reason,

                            event_id=ev_id,

                            user_initiated=bool(body.get('user_initiated', True)),

                        )

                    except Exception:

                        pass

                    ack_payload = {

                        'type': 'IOS_MEDIA_STOPPED_ACK',

                        'event_id': ev_id,

                        'source_id': 'IOS_FULL_SCREEN_SHARE',

                        'status': 'ACKNOWLEDGED',

                        'timestamp': time.time(),

                    }

                    try:

                        await ws.send_json(ack_payload)

                    except Exception:

                        pass

                elif kind == 'stage_event':

                    try:

                        from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

                        IPadCameraStreamMonitor.get_instance().record_detailed_stage(

                            stage_name=body.get('stage', ''),

                            session_id=body.get('session_id') or identity.get('session_id', ''),

                            connection_id=body.get('connection_id', ''),

                            source_id=body.get('source_id', 'IOS_FRONT_CAMERA'),

                            signaling_state=body.get('signaling_state', ''),

                            ice_state=body.get('ice_state', ''),

                            peer_connection_state=body.get('peer_connection_state', ''),

                            success=bool(body.get('success', True)),

                            error_message=str(body.get('error', ''))

                        )

                    except Exception:

                        pass

                elif kind == 'status':

                    self.store.client_state(credential, body.get('state'), body.get('diagnostics') or {})

                    if body.get('state') in ('IOS_CAMERA_INTERRUPTED', 'IOS_CAMERA_STOPPED', 'IOS_CAMERA_REVOKED'):

                        await self.receiver.stop(credential, report=False)

                        self._forget_frame(camera_id)

                elif kind == 'webrtc_stats':

                    try:

                        from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

                        IPadCameraStreamMonitor.get_instance().record_ios_sender_stats(body.get('stats') or {})

                    except Exception:

                        pass

                else:

                    raise ValueError('Unsupported signal type.')

        except (PermissionError, ValueError, TypeError, KeyError, asyncio.TimeoutError, json.JSONDecodeError):

            await ws.close(code=4003, message=b'Camera authorization or signaling failed')

        finally:

            if camera_id and self.sockets.get(camera_id) is ws:

                self.sockets.pop(camera_id, None)

                try:

                    identity = self.store.authenticate(credential)

                    if identity['state'] not in {'IOS_CAMERA_INTERRUPTED','IOS_CAMERA_STOPPED','IOS_CAMERA_REVOKED'}:

                        self.store.client_state(credential, 'IOS_RECONNECTING', {})

                except (PermissionError, ValueError):

                    pass

                await self.receiver.stop_camera(camera_id, report=False)

                self._forget_frame(camera_id)

        return ws



    def _forget_frame(self, camera_id):

        with self._preview_lock:

            self._last_preview.pop(camera_id, None)

        self._monitors.pop(camera_id, None)



    async def _frame(self, identity, frame):

        camera_id = identity['camera_id']

        monitor = self._monitors.get(camera_id)

        if monitor is None:

            from windows.strict_ios_frame_monitor import IOSFrameMonitor

            monitor = self._monitors[camera_id] = IOSFrameMonitor()

        observed = self.store.receiver_identity(camera_id)

        frame_seq = observed.get('frame_sequence', 0) if isinstance(observed, dict) else 0

        await asyncio.to_thread(monitor.process, frame, frame_seq, identity.get('session_id', ''))

        with self._preview_lock:

            self._last_preview[camera_id] = monitor.preview()

        ws = self.sockets.get(camera_id)

        if ws and not ws.closed:

            try:

                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

                mon = IPadCameraStreamMonitor.get_instance()

                ack = mon.get_frame_confirmation_payload()

                await ws.send_json(ack)

            except Exception:

                pass

            try:

                await ws.send_json({'type': 'receiver_frames', 'state': 'IOS_CAMERA_STREAMING',

                    'frames_received': frame_seq, 'last_frame_at': time.time(),

                    'session_id': identity['session_id']})

            except Exception:

                pass



    async def _watchdog(self):

        while True:

            await asyncio.sleep(2)

            for camera_id, ws in list(self.sockets.items()):

                try:

                    # Validates current registration/session even without client messages.

                    observed = self.store.receiver_identity(camera_id)

                    last_frame = observed.get('last_frame_at')

                    if ws and not ws.closed and observed.get('state') in ('IOS_CAMERA_INTERRUPTED', 'CAMERA_DEGRADED', 'CAMERA_INTERRUPTED') and isinstance(last_frame, (int, float)) and (time.time() - last_frame) >= 15.0:

                        try:

                            await ws.send_json({'type': 'reconnect', 'reason': 'Camera frame gap exceeded 15s; attempting automatic WebRTC recovery.'})

                        except Exception:

                            pass

                except (PermissionError, ValueError):

                    try:

                        await ws.send_json({'type': 'stop', 'reason': 'Session ended or camera authorization expired.'})

                        await ws.close()

                    except ConnectionError:

                        pass



    def request_screen_share(self, account, pid, force_restart=True):

        """Starts the 60-second screen-share negotiation window and notifies the connected iOS companion."""

        from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor

        telemetry = IPadScreenShareMonitor.get_instance().start_negotiation(

            preparation_id=pid,

            force_restart=force_restart,

        )

        try:

            status = self.store.status(account, pid)

            camera_id = status.get('camera_id')

            ws = self.sockets.get(camera_id) if camera_id else None

            if ws and not ws.closed and self.loop and self.loop.is_running():

                asyncio.run_coroutine_threadsafe(

                    ws.send_json({'type': 'request_screen_share', 'preparation_id': pid, 'grace_seconds': 60}),

                    self.loop,

                )

        except Exception:

            pass

        return telemetry



    def request_front_camera_retry(self, account, pid):

        """Requests the connected iOS companion to retry/reconnect the front camera stream."""

        try:

            status = self.store.status(account, pid)

            camera_id = status.get('camera_id')

            ws = self.sockets.get(camera_id) if camera_id else None

            if ws and not ws.closed and self.loop and self.loop.is_running():

                asyncio.run_coroutine_threadsafe(

                    ws.send_json({'type': 'reconnect', 'reason': 'User requested front camera retry from Windows.'}),

                    self.loop,

                )

        except Exception:

            pass

        return self.preview(account, pid)



    def preview(self, account, pid):

        status = self.store.status(account, pid)

        camera_id = status.get('camera_id')

        monitor = self._monitors.get(camera_id) if camera_id else None

        with self._preview_lock:

            preview = dict(self._last_preview.get(camera_id, {}))

        if monitor is not None:

            try:

                live_preview = monitor.preview()

                preview.update(live_preview)

            except Exception:

                pass

        last_frame_at = status.get('last_frame_at') or preview.get('last_frame_at')

        # Check actual live stream monitor to avoid false CAMERA_INTERRUPTED when incoming frames are active

        live_rx_fps = 0.0

        live_proc_fps = 0.0

        try:

            from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

            mon_inst = IPadCameraStreamMonitor.get_instance()

            live_rx_fps = float(getattr(mon_inst, "received_fps", 0.0) or 0.0)

            live_proc_fps = float(getattr(mon_inst, "processing_fps", 0.0) or 0.0)

            if live_rx_fps <= 0.0:
                try:
                    from windows.strict_dual_camera_proctor import StrictDualCameraProctor
                    dc = StrictDualCameraProctor.get_instance()
                    live_rx_fps = float(getattr(dc, "_ios_received_fps", 0.0) or 0.0)
                except Exception:
                    pass

            if live_proc_fps <= 0.0:
                try:
                    from windows.strict_dual_camera_proctor import StrictDualCameraProctor
                    dc = StrictDualCameraProctor.get_instance()
                    live_proc_fps = float(getattr(dc, "_ios_processing_fps", 0.0) or 0.0)
                except Exception:
                    pass

            if live_rx_fps > 0.0:

                # Actual incoming frames are arriving; camera must NOT be classified as interrupted

                status['state'] = 'IOS_CAMERA_STREAMING'

                status['streaming'] = True

                if 'received_fps' not in preview or preview['received_fps'] == 0.0:

                    preview['received_fps'] = live_rx_fps

                if 'processing_fps' not in preview or preview['processing_fps'] == 0.0:

                    preview['processing_fps'] = live_proc_fps

        except Exception:

            pass



        if status.get('state') != 'IOS_CAMERA_STREAMING' and live_rx_fps <= 0.0:

            preview.pop('jpeg', None)

            if not isinstance(last_frame_at, (int, float)) or (time.time() - float(last_frame_at)) > 2.0:

                if 'received_fps' in preview:

                    preview['received_fps'] = 0.0

                if 'processing_fps' in preview:

                    preview['processing_fps'] = 0.0

        elif isinstance(last_frame_at, (int, float)) and (time.time() - float(last_frame_at)) > 15.0 and live_rx_fps <= 0.0:

            preview['received_fps'] = 0.0

            preview['processing_fps'] = 0.0

        ipad_diag = {}

        init_prereq = {}

        ipad_screen = {}

        try:

            from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

            mon_inst = IPadCameraStreamMonitor.get_instance()

            ipad_diag = mon_inst.get_diagnostics()

            init_prereq = mon_inst.get_initial_prerequisite_status()

        except Exception:

            pass

        try:

            from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor

            ipad_screen = IPadScreenShareMonitor.get_instance().get_telemetry()

        except Exception:

            pass

        return dict(

            status=status,

            preview=preview,

            ipad_camera_diagnostics=ipad_diag,

            initial_prerequisite=init_prereq,

            ipad_screen_share=ipad_screen,

            source='iPhone camera',

            tls_verified=self.ready,

            verification_details=dict(self.verification_details),

            origin=self.origin,

            gateway_ready=self.ready

        )



    async def _cleanup(self):

        if self.receiver:

            await self.receiver.close()

        for ws in list(self.sockets.values()):

            await ws.close()

        self.sockets.clear()

        self._last_preview.clear()

        self._monitors.clear()

        if self.runner:

            await self.runner.cleanup()

        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

        for task in pending:

            task.cancel()

        if pending:

            await asyncio.gather(*pending, return_exceptions=True)



    def stop(self):

        if self.loop and self.loop.is_running():

            self.loop.call_soon_threadsafe(self.loop.stop)

        if self.thread and self.thread is not threading.current_thread():

            self.thread.join(timeout=4)





def attach_ios_camera(manager):

    """No network listener or permissions at all until explicitly configured."""

    try:

        config = camera_configuration(db=getattr(manager, 'db', None))

        if not config['configured']:

            return None, config['reason']

        gateway = IOSCameraGateway(manager, config).start()

        return gateway, gateway.error

    except (ValueError, OSError) as error:

        return None, str(error)

