"""



StrictRecords Central Synchronization & API Server (Phases 11-17)



Exposes REST endpoints for Android & Windows devices and serves the



Dark Cinematic Command Center UI on http://127.0.0.1:8765.



"""







import argparse



import base64



import json



import os



import socket



import sys



import urllib.parse



import uuid



import hashlib



import shutil



import time



import math



import ipaddress



import tempfile



from datetime import datetime



from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer



from typing import Any, Dict, List, Optional







ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))



if ROOT_DIR not in sys.path:



    sys.path.insert(0, ROOT_DIR)







from core.apk_inspector import inspect_apk, parse_multipart_form



import logging



import queue



import threading







logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")



logger = logging.getLogger("StrictRecordsServer")



from collections import deque



from core.audio_generator import ensure_motivation_wav



from core.ai_robot_admin import StrictBotAIReviewer



from core.connection_pipeline import ConnectionPipelineManager



from core.database import StrictRecordsDB, get_local_ip



from core.scoring_engine import ScoringEngine



from core.time_window_engine import TimeWindowEngine, now_ist



from core.ui_dashboard import DASHBOARD_HTML



from windows.agent import StrictRecordsWindowsAgent, WindowsWin32Inspector



from windows.ram_optimizer import RamAndCacheOptimizer



from windows.restriction_manager import YouTubeRestrictionManager



from windows.strict_session_manager import StrictSessionManager











from core.event_bus import EventBus



from core.strictbot_assistant import AssistantError, AssistantPrincipal, StrictBotAssistant











MAX_HOURLY_REQUEST_BYTES = 65 * 1024 * 1024



_STRICTBOT_SERVICE_LOCK = threading.Lock()











class StrictRecordsRequestHandler(BaseHTTPRequestHandler):



    protocol_version = "HTTP/1.0"



    db: StrictRecordsDB = None



    engine: ScoringEngine = None



    win_agent: StrictRecordsWindowsAgent = None



    connection_manager: ConnectionPipelineManager = None



    restriction_mgr: YouTubeRestrictionManager = None



    strict_session_manager: StrictSessionManager = None



    ram_optimizer: RamAndCacheOptimizer = None



    notification_service: Any = None



    wav_path: str = ""



    strictbot_service: Any = None



    bluetooth_manager: Any = None



    bluetooth_auto_sync: Any = None



    google_auth: Any = None



    cloud_sync_mgr: Any = None



    points_cloud: Any = None



    process_session_id: Optional[str] = None



    _hourly_review_guard = threading.Lock()



    _hourly_reviews_inflight = set()



    _pin_failed_attempts: int = 0



    _pin_lockout_until: float = 0.0







    def handle(self):



        try:



            super().handle()



        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError, OSError):



            pass







    def get_current_process_session_id(self) -> Optional[str]:



        return (



            getattr(self.server, "process_session_id", None)



            or getattr(StrictRecordsRequestHandler, "process_session_id", None)



            or getattr(self.db, "current_process_session_id", None)



            or getattr(StrictRecordsDB, "active_process_session_id", None)



        )







    def _get_bluetooth_manager(self) -> Any:



        if StrictRecordsRequestHandler.bluetooth_manager is None:



            from core.bluetooth_transfer import BluetoothTransferManager



            StrictRecordsRequestHandler.bluetooth_manager = BluetoothTransferManager(self.db, self.engine)



        return StrictRecordsRequestHandler.bluetooth_manager







    def _authorize_local_bluetooth_control(self) -> bool:



        # Session codes and paired-device selection belong to the receiving PC.



        origin = self.headers.get("Origin")



        local = self.client_address[0] in ("127.0.0.1", "::1")



        try:



            parsed = urllib.parse.urlsplit(origin) if origin else None



            if parsed and (parsed.scheme not in ("http", "https") or



                           parsed.hostname not in ("127.0.0.1", "localhost", "::1") or



                           (parsed.port or (443 if parsed.scheme == "https" else 80)) != self.server.server_address[1]):



                local = False



        except ValueError:



            local = False



        if not local:



            self._send_json({"success": False, "error": "Open Bluetooth receiver controls in the Windows app on this PC"}, status=403)



        return local







    def _get_bluetooth_auto_sync(self) -> Any:



        if StrictRecordsRequestHandler.bluetooth_auto_sync is None:



            from core.bluetooth_auto_sync import AutomaticBluetoothSyncManager



            StrictRecordsRequestHandler.bluetooth_auto_sync = AutomaticBluetoothSyncManager(self.db, self.engine)



            StrictRecordsRequestHandler.bluetooth_auto_sync.start()



        return StrictRecordsRequestHandler.bluetooth_auto_sync







    def log_message(self, format: str, *args: Any) -> None:



        pass  # Keep console clean during automated test runs







    def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:



        self.close_connection = True



        raw = json.dumps(data).encode("utf-8")



        self.send_response(status)



        self.send_header("Content-Type", "application/json; charset=utf-8")



        self.send_header("Content-Length", str(len(raw)))



        self.send_header("Access-Control-Allow-Origin", "*")



        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")



        self.send_header("Access-Control-Allow-Headers", "*")



        self.send_header("Access-Control-Allow-Private-Network", "true")



        self.send_header("Connection", "close")



        self.end_headers()



        try:



            self.wfile.write(raw)



            self.wfile.flush()



        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):



            pass







    def _authorize_admin_request(self, body: Optional[Dict[str, Any]] = None, query: Optional[Dict[str, List[str]]] = None, allow_initial_setup: bool = False) -> bool:



        body = body or {}



        query = query or {}



        token = (



            self.headers.get("X-Admin-Session")



            or self.headers.get("X-Admin-Token")



            or str(body.get("token") or "")



            or query.get("token", [""])[0]



        )



        session = self.db.verify_admin_session(token) if token else None



        if not session:



            admin_pin = (



                self.headers.get("X-Admin-PIN")



                or str(body.get("admin_pin") or body.get("pin") or "")



                or query.get("admin_pin", [""])[0]



                or query.get("pin", [""])[0]



            )



            if admin_pin:



                if not self.db.verify_admin_pin(admin_pin):



                    self._send_json({"success": False, "error": "Invalid administrator credentials or PIN.", "code": "UNAUTHORIZED"}, status=403)



                    return False



                session = self.db.authenticate_admin("admin", admin_pin, device_id="windows_admin_console")



            else:



                self._send_json({"success": False, "error": "A valid administrator session or authentication key is required.", "code": "UNAUTHORIZED"}, status=401)



                return False







        if session.get("pin_change_required") and not allow_initial_setup and self.db.is_default_admin_pin():



            self._send_json({"success": False, "error": "Default setup PIN must be changed before administrative access is granted.", "code": "PIN_CHANGE_REQUIRED"}, status=403)



            return False







        self._authenticated_admin = session



        return True







    def _is_local_request(self) -> bool:



        try:



            return ipaddress.ip_address(self.client_address[0]).is_loopback



        except (ValueError, IndexError, AttributeError):



            return False







    def _strict_study_local_action(self):



        origin = self.headers.get('Origin', '')



        expected = 'http://' + self.headers.get('Host', '')



        valid = (self._is_local_request() and self.headers.get('X-StrictRoutines-Study') == '1'



                 and (not origin or origin == expected))



        if not valid:



            self._send_json({'success':False,'error':'Use Strict Session on the local Windows dashboard.'}, status=403)



        return valid







    def _strict_preparation_action(self,path,body):



        manager=self.strict_session_manager



        if not manager:



            self._send_json({'success':False,'error':'Strict Session service unavailable'},status=503);return



        action=path.rsplit('/',1)[-1]



        try:



            if action in {'join','heartbeat'}:



                origin=self.headers.get('Origin','')



                if origin and urllib.parse.urlsplit(origin).netloc.lower()!=self.headers.get('Host','').lower():



                    raise PermissionError('Use the invitation page served by this PC')



                if action=='join':result=manager.preparation.join(body.get('token',''),self.headers.get('User-Agent',''),body.get('client_nonce'),body.get('client_info'))



                else:



                    report=dict(body.get('report') or {})



                    result=manager.preparation.heartbeat(body.get('preparation_id'),body.get('role'),body.get('credential'),report)



            else:



                if not self._strict_study_local_action():return



                account=self.db.get_active_user_profile().get('user_id','user_lok')



                if action=='check':result=manager.preparation.state(account,refresh=True)



                elif action=='host':result=manager.preparation_host_report(account,body.get('preparation_id'),body.get('report') or {})



                elif action=='hardware':result=manager.start_setup_hardware(body.get('preparation_id'),body.get('camera_id'),body.get('consent') is True)



                elif action=='stop_hardware':



                    manager.stop_setup_hardware();result=manager.preparation.state(account,True)



                elif action=='mode':result=manager.select_session_mode(body.get('mode'))



                elif action=='invite':



                    role_str = str(body.get('role', '')).upper()



                    camera_gateway = getattr(manager, 'ios_camera_gateway', None)



                    if role_str == 'IOS':



                        try:



                            from windows.cloudflare_tunnel_manager import CloudflareTunnelManager



                            cf_mgr = CloudflareTunnelManager.get_instance(db=manager.db)



                            if cf_mgr.public_origin and (not camera_gateway or camera_gateway.origin != cf_mgr.public_origin):



                                manager.ensure_cloudflare_camera_gateway(force_reattach=True)



                                camera_gateway = getattr(manager, 'ios_camera_gateway', None)



                        except Exception:



                            pass







                    if camera_gateway and camera_gateway.ready and role_str == 'IOS':



                        from core.strict_ios_join import create_join_invitation



                        result = create_join_invitation(manager, account, manager.preparation.current(account)['preparation_id'])



                    else:



                        from core.database import get_local_ip



                        origin = (camera_gateway.origin if camera_gateway and camera_gateway.ready else os.environ.get('STRICTRECORDS_COMPANION_ORIGIN','').strip()) or f'http://{get_local_ip()}:{self.server.server_port}'



                        result = manager.preparation.invite(account, body.get('role'), origin)



                        if role_str == 'IOS':



                            inv = result.get('invitation', {})



                            inv['https_verified'] = False



                            inv['purpose'] = 'IOS_TIMER_ONLY_FALLBACK'



                            inv['warning'] = 'Local network timer pairing only. HTTPS probe failed. Browser camera access requires verified HTTPS.'



                            inv['verification_details'] = getattr(camera_gateway, 'verification_details', {}) if camera_gateway else {



                                'hostname': '', 'dns_resolved': False, 'https_reachable': False,



                                'companion_route_ok': False, 'wss_signaling_ok': False,



                                'error': getattr(manager, 'ios_camera_configuration_error', 'Trusted iOS camera HTTPS domain is not configured.')



                            }



                            try:



                                pid = manager.preparation.current(account)['preparation_id']



                                manager.preparation.links[(pid, 'IOS')] = inv



                            except Exception:



                                pass



                elif action=='camera_status':



                    pid = body.get('preparation_id')



                    gateway = getattr(manager, 'ios_camera_gateway', None)



                    try:



                        from windows.cloudflare_tunnel_manager import CloudflareTunnelManager



                        cf_mgr = CloudflareTunnelManager.get_instance(db=manager.db)



                        if cf_mgr.public_origin and (not gateway or gateway.origin != cf_mgr.public_origin):



                            manager.ensure_cloudflare_camera_gateway(force_reattach=True)



                            gateway = getattr(manager, 'ios_camera_gateway', None)



                    except Exception:



                        pass



                    result = gateway.preview(account, pid) if gateway else dict(



                        status=manager.ios_camera.status(account, pid),



                        source='iPhone camera',



                        preview={},



                        tls_verified=False,



                        verification_details={'hostname': '', 'dns_resolved': False, 'https_reachable': False, 'companion_route_ok': False, 'wss_signaling_ok': False, 'error': getattr(manager, 'ios_camera_configuration_error', 'Trusted iOS camera HTTPS domain is not configured.')},



                        origin='',



                        gateway_ready=False



                    )



                    result.update(



                        success=True,



                        configuration_error='' if gateway and gateway.ready else (gateway.error if gateway else manager.ios_camera_configuration_error),



                        tls_verified=bool(gateway and gateway.ready),



                        gateway_ready=bool(gateway and gateway.ready),



                        origin=gateway.origin if gateway else '',



                        verification_details=getattr(gateway, 'verification_details', result.get('verification_details', {})) if gateway else result.get('verification_details', {})



                    )



                elif action=='camera_invite':



                    gateway = getattr(manager, 'ios_camera_gateway', None)



                    if not gateway or not gateway.ready:



                        raise ValueError(gateway.error if gateway else manager.ios_camera_configuration_error)



                    from core.strict_ios_join import create_join_invitation



                    result = create_join_invitation(manager, account, body.get('preparation_id'))



                elif action=='ready':result=manager.preparation.authorize_start(account,body.get('preparation_id'))



                elif action in {

                    'retry_windows_camera',

                    'retry_ios_front_camera',

                    'retry_ios_screen_share',

                    'retry_android_connection',

                    'retry_browser_cleanup',

                    'retry_backend_check',

                }:

                    result = manager.retry_prerequisite_step(

                        action,

                        preparation_id=body.get('preparation_id'),

                        camera_id=body.get('camera_id'),

                        consent=bool(body.get('consent', True)),

                    )



                elif action=='start_screen_share_negotiation':

                    from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor

                    pid = body.get('preparation_id') or manager.preparation.current(account)['preparation_id']

                    gateway = getattr(manager, 'ios_camera_gateway', None)

                    if gateway:

                        gateway.request_screen_share(account, pid, force_restart=True)

                    else:

                        IPadScreenShareMonitor.get_instance().start_negotiation(preparation_id=pid, force_restart=True)

                    result = manager.preparation.state(account)



                elif action=='export_diagnostics':



                    from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor



                    mon_inst = IPadCameraStreamMonitor.get_instance()



                    result = {



                        'success': True,



                        'diagnostics': mon_inst.export_diagnostics_sanitized() if mon_inst else {}



                    }



                else:raise ValueError('Unknown preparation action')



            self._send_json(result)



        except PermissionError as error:self._send_json({'success':False,'error':str(error)},status=403)



        except (ValueError,TypeError,KeyError) as error:self._send_json({'success':False,'error':str(error)},status=409)



        except Exception as error:self._send_json({'success':False,'error':str(error)},status=500)







    def _handle_exit_check(self, path, body):



        """Only the local desktop can request an exit; grading lives in the shared gate."""



        origin = self.headers.get('Origin', '')



        expected = 'http://' + self.headers.get('Host', '')



        if (not self._is_local_request() or self.headers.get('X-StrictRoutines-Exit') != '1'



                or (origin and origin != expected)):



            self._send_json({'success': False, 'error': 'Use Exit in the local StrictRoutines desktop.'}, status=403)



            return



        api = getattr(self, 'desktop_api', None)



        if not api:



            self._send_json({'success': False, 'error': 'The native desktop exit service is unavailable.'}, status=503)



            return



        cid = body.get('challenge_id', '')



        if path == '/api/exit/start':



            result = api.start_exit_challenge(body.get('pin'))



        elif path == '/api/exit/answer':



            result = api.answer_exit_challenge(cid, body.get('question_id'), body.get('answer'))



        elif path == '/api/exit/complete':



            result = api.complete_exit_challenge(cid)



        elif path == '/api/exit/mcq':



            result = api.exit_via_mcq(cid)



        else:



            self._send_json({'success': False, 'error': 'Unknown exit action.'}, status=404)



            return



        self._send_json(result, status=200 if result.get('success') else 403)







    def _browser_setup_allowed(self, mutation=False):



        """Opening local applications is restricted to the local dashboard origin."""



        port=getattr(getattr(self,"server",None),"server_port",8765)



        try:



            host=urllib.parse.urlsplit("http://"+self.headers.get("Host",""))



            origin=self.headers.get("Origin","")



            parsed=urllib.parse.urlsplit(origin) if origin else None



            valid=self._is_local_request() and host.hostname in {"127.0.0.1","localhost","::1"} and (host.port or 80)==port



            if parsed:



                valid = valid and (



                    (parsed.scheme == "http" and parsed.hostname == host.hostname and (parsed.port or 80) == port)



                    or parsed.scheme in ("chrome-extension", "moz-extension")



                )



            if mutation: valid = valid and self.headers.get("X-StrictRoutines-Setup") == "1"



        except ValueError: valid=False



        if not valid:



            self._send_json({"success":False,"error":"Open browser setup from this computer's StrictRoutines Controls screen."},status=403)



        return valid







    def _handle_browser_setup(self,path):



        if not self._browser_setup_allowed(mutation=True): return



        try:



            length=int(self.headers.get("Content-Length","0"))



            if not 0 <= length <= 4096: raise ValueError("Invalid setup request size.")



            body=json.loads(self.rfile.read(length)) if length > 0 else {}



            if not isinstance(body,dict): raise ValueError("Invalid setup request.")



            from windows.browser_setup import get_browser_extension_setup



            setup=get_browser_extension_setup()



            if path.endswith(("/monitor_prepare", "/monitor_manager", "/monitor_folder")):



                from windows.browser_extension_monitor import get_browser_extension_monitor



                registry = get_browser_extension_monitor(db=self.db)



                if path.endswith('/monitor_prepare'):



                    result = registry.prepare_browser(str(body.get('browser_id','')),str(body.get('profile_label','')))



                elif path.endswith('/monitor_manager'):



                    result = registry.open_manager(str(body.get('browser_id','')))



                else:



                    result = registry.open_folder(str(body.get('installation_id','')),str(body.get('component','')))



            elif path.endswith("/open_folder"):



                result=setup.open_folder(str(body.get("browser_id", "")))



            elif path.endswith("/lock"):



                result=setup.lock_extension()



            elif path.endswith("/uninstall_challenge/verify"):



                from windows.uninstall_challenge import get_uninstall_challenge_manager



                ch_mgr = get_uninstall_challenge_manager()



                ch_id = str(body.get("challenge_id", "")).strip()



                ans = str(body.get("answer") or body.get("code") or "").strip()



                lang = str(body.get("language") or "").strip() or None



                result = ch_mgr.verify_answer(ch_id, ans, language=lang)



                status = 200 if result.get("success") else 400



                self._send_json(result, status=status)



                return



            elif path.endswith("/uninstall_challenge/confirm"):



                from windows.uninstall_challenge import get_uninstall_challenge_manager



                ch_mgr = get_uninstall_challenge_manager()



                ch_id = str(body.get("challenge_id", "")).strip()



                auth_token = str(body.get("auth_token", "")).strip() or None



                result = ch_mgr.confirm_and_remove_policy(ch_id, auth_token=auth_token)



                status = 200 if result.get("success") else 400



                self._send_json(result, status=status)



                return



            elif path.endswith("/uninstall_challenge/event"):



                from windows.uninstall_challenge import get_uninstall_challenge_manager



                ch_mgr = get_uninstall_challenge_manager()



                ch_id = str(body.get("challenge_id", "")).strip()



                ev = str(body.get("event", "focus_lost")).strip()



                result = ch_mgr.record_interruption(ch_id, ev)



                self._send_json(result)



                return



            elif path.endswith("/uninstall_challenge/resume"):



                from windows.uninstall_challenge import get_uninstall_challenge_manager



                ch_mgr = get_uninstall_challenge_manager()



                ch_id = str(body.get("challenge_id", "")).strip()



                result = ch_mgr.resume_challenge(ch_id)



                status = 200 if result.get("success") else 400



                self._send_json(result, status=status)



                return



            elif path.endswith("/set_grace_period"):



                from windows.browser_extension_monitor import get_browser_extension_monitor



                mon = get_browser_extension_monitor(db=self.db)



                mins = int(body.get("grace_period_minutes") or body.get("minutes") or 5)



                identity = str(body.get('installation_id','')) or None



                if identity:



                    mon.set_grace_duration_minutes(mins,installation_id=identity)



                else:



                    mon.set_grace_duration_minutes(mins)



                self._send_json({



                    "success": True,



                    "grace_period_minutes": mon.get_grace_duration_minutes(identity) if identity else mon.get_grace_duration_minutes(),



                    "message": f"Extension disable grace period set to {mins} minutes."



                })



                return



            elif path.endswith("/unlock"):



                pin=str(body.get("admin_pin") or body.get("pin") or "").strip()



                dur=int(body.get("duration_minutes") or 10)



                result=setup.unlock_extension(admin_pin=pin, duration_minutes=dur)



                status=200 if result.get("success") else 403



                self._send_json(result, status=status)



                return



            else:



                ids=body.get("browser_ids")



                if ids is not None and (not isinstance(ids,list) or len(ids)>64 or any(not isinstance(i,str) for i in ids)):



                    raise ValueError("Choose detected browser IDs.")



                managed=body.get("managed") is True



                result=setup.setup(ids, managed=managed)



            self._send_json(result)



        except (ValueError,TypeError,UnicodeDecodeError) as exc:



            self._send_json({"success":False,"error":str(exc)},status=400)



        except Exception:



            logger.exception("Browser extension setup failed")



            self._send_json({"success":False,"error":"Browser setup could not complete. Refresh the detected browsers and retry."},status=500)







    def _get_windows_submission_identity(self) -> tuple[str, str]:



        if not self._is_local_request():



            raise PermissionError("Windows proof submissions must come from the local StrictRecords app.")



        profile = self.db.get_active_user_profile()



        account_id = str(profile.get("user_id") or "user_lok")



        with self.db._connect() as conn:



            row = conn.execute(



                """SELECT device_id FROM devices



                WHERE user_id = ? AND (UPPER(platform) LIKE 'WINDOWS%' OR UPPER(device_type) = 'WINDOWS')



                  AND pairing_state != 'REVOKED' AND status != 'REVOKED'



                ORDER BY CASE WHEN UPPER(platform) LIKE 'WINDOWS%' THEN 0 ELSE 1 END,



                         COALESCE(registered_at, '') DESC LIMIT 1""",



                (account_id,),



            ).fetchone()



        if not row:



            raise PermissionError("This Windows device is not registered to the active account. Pair it before submitting proof.")



        return account_id, str(row["device_id"])







    def _get_authenticated_device_account(self, device_id: str, device_token: str) -> str:



        if not device_id or not device_token or not self.db.validate_device_credential(device_id, device_token):



            raise PermissionError("Device registration is revoked or invalid. Please pair again.")



        token_hash = hashlib.sha256(device_token.encode("utf-8")).hexdigest()



        with self.db._connect() as conn:



            row = conn.execute(



                "SELECT account_id FROM device_credentials WHERE device_id = ? AND (token_hash = ? OR token_hash = ?) AND revoked_at IS NULL",



                (device_id, token_hash, device_token),



            ).fetchone()



        if not row:



            raise PermissionError("Device registration is revoked or invalid. Please pair again.")



        return str(row["account_id"])







    def _get_authenticated_request_device(self, body: Dict[str, Any]) -> tuple[str, str]:



        device_id = str(body.get("device_id") or "").strip()



        device_token = str(body.get("device_token") or "")



        account_id = self._get_authenticated_device_account(device_id, device_token)



        return device_id, account_id







    def _strictbot_principal(self, body=None, query=None):



        body, query = body or {}, query or {}



        origin = self.headers.get("Origin")



        if origin:



            source = urllib.parse.urlparse(origin)



            if source.scheme not in ("http", "https") or source.netloc.lower() != self.headers.get("Host", "").lower():



                raise AssistantError("ORIGIN_DENIED", "Open StrictBot inside the registered StrictRecords application.", 403)



        admin_token = self.headers.get("X-Admin-Session") or self.headers.get("X-Admin-Token")



        device_id = self.headers.get("X-Device-Id") or str(body.get("device_id") or "")



        device_token = self.headers.get("X-Device-Token") or str(body.get("device_token") or "")



        if device_id or device_token:



            account_id = self._get_authenticated_device_account(device_id, device_token)



        elif self._is_local_request():



            account_id, device_id = self._get_windows_submission_identity()



        elif not admin_token:



            raise AssistantError("UNAUTHORIZED", "Pair this device before opening StrictBot.", 401)



        else:



            account_id, device_id = str(self.db.get_active_user_profile().get("user_id") or "user_lok"), "admin_console"



        if admin_token:



            session = self.db.verify_admin_session(admin_token)



            if not session:



                raise AssistantError("SESSION_EXPIRED", "The administrator session is invalid or expired. Sign in again.", 401)



            with self.db._connect() as conn:



                active = conn.execute("SELECT is_active FROM admin_users WHERE admin_id=?", (session["admin_id"],)).fetchone()



            if not active or not active["is_active"]:



                raise AssistantError("SESSION_EXPIRED", "This administrator account is inactive.", 401)



            # An authenticated administrator can deliberately select an account; a user cannot.



            account_id = str(body.get("account_id") or query.get("account_id", [account_id])[0])



            return AssistantPrincipal(account_id, str(session["admin_id"]), device_id,



                str(session["role"]), admin_token)



        return AssistantPrincipal(account_id, account_id, device_id)







    def _diagnostic_principal(self, body=None, query=None):



        from core.wakeup_diagnostics import DiagnosticPrincipal



        body, query = body or {}, query or {}



        admin_token = self.headers.get("X-Admin-Session") or self.headers.get("X-Admin-Token")



        device_id = self.headers.get("X-Device-Id") or str(body.get("device_id") or (query.get("device_id", [""])[0] if "device_id" in query else "") or "")



        device_token = self.headers.get("X-Device-Token") or str(body.get("device_token") or (query.get("device_token", [""])[0] if "device_token" in query else "") or "")



        platform = "WINDOWS"



        if device_id or device_token:



            account_id = self._get_authenticated_device_account(device_id, device_token)



            with self.db._connect() as conn:



                drow = conn.execute("SELECT platform FROM devices WHERE device_id=?", (device_id,)).fetchone()



                platform = str(drow["platform"]).upper() if drow and drow["platform"] else "ANDROID"



        elif self._is_local_request():



            try:



                account_id, device_id = self._get_windows_submission_identity()



            except Exception:



                profile = self.db.get_active_user_profile()



                account_id = str(profile.get("user_id") or "user_lok")



                device_id = "windows_local"



            platform = "WINDOWS"



        elif admin_token:



            session = self.db.verify_admin_session(admin_token)



            if not session:



                raise PermissionError("Admin session expired.")



            profile = self.db.get_active_user_profile()



            account_id = str(body.get("account_id") or query.get("account_id", [str(profile.get("user_id") or "user_lok")])[0])



            device_id = "admin_console"



            platform = "WINDOWS"



        else:



            profile = self.db.get_active_user_profile()



            account_id = str(profile.get("user_id") or "user_lok")



            device_id = str(body.get("device_id") or "device_client")



            platform = "ANDROID" if "android" in str(self.headers.get("User-Agent", "")).lower() else "WINDOWS"



        return DiagnosticPrincipal(account_id=account_id, device_id=device_id, platform=platform)







    def _gym_principal(self, body=None):



        # Reuse registered device / administrator authentication. The installed



        # companion's own origin may use the same registered local PC identity.



        origin = self.headers.get('Origin', '')



        if origin.startswith('chrome-extension://'):



            from pathlib import Path



            manifest = json.loads((Path(ROOT_DIR) / 'browser_extension/manifest.json').read_text())



            digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]



            extension_id = ''.join(chr(ord('a') + int(char,16)) for char in digest)



            if not self._is_local_request() or origin.rstrip('/') != 'chrome-extension://' + extension_id:



                raise PermissionError('Open Gym controls in the registered StrictRoutines companion')



            device = self.headers.get('X-Device-Id','')



            token = self.headers.get('X-Device-Token','')



            if device or token:



                account = self._get_authenticated_device_account(device,token)



                return AssistantPrincipal(account,account,device)



            account, device = self._get_windows_submission_identity()



            return AssistantPrincipal(account,account,device)



        return self._strictbot_principal(body=body)







    def _handle_youtube_reward(self,path,body=None,query=None):



        from core.entertainment_reward_engine import EntertainmentRewardEngine



        from core.youtube_reward_cloud import client



        body,query = body or {}, query or {}



        ent_engine = EntertainmentRewardEngine(self.db)



        account_id = body.get('account_id') or (query.get('account_id', ['user_lok'])[0] if isinstance(query.get('account_id'), list) else query.get('account_id', 'user_lok'))







        action = 'activate' if (path.endswith('/start') or path.endswith('/activate')) else 'stop' if path.endswith('/stop') else 'state'



        reward_id = body.get('reward_id')







        # Check local EntertainmentRewardEngine



        if reward_id and (reward_id.startswith('ent_') or reward_id.startswith('rew_')):



            if action == 'activate':



                res = ent_engine.activate_reward(reward_id, client_id=body.get('client_id', 'WINDOWS_EXE'), account_id=account_id)



                if self.restriction_mgr:



                    self.restriction_mgr._last_policy_reconcile_attempt['youtube'] = 0



                    self.restriction_mgr.reconcile_browser_policies_if_needed()



                self._send_json(res)



                return



            elif action == 'stop':



                self.db.update_entertainment_entitlement(reward_id, state='EXPIRED', termination_result='USER_COMPLETED')



                self._send_json({"success": True, "stopped": True})



                return







        local_status = ent_engine.get_reward_status(account_id)



        if local_status.get("is_active") or local_status.get("total_available", 0) > 0:



            if action == 'state':



                self._send_json(local_status)



                return







        try:



            principal = self._gym_principal(body)



            if (body.get('account_id') or query.get('account_id',[principal.account_id])[0]) != principal.account_id:



                raise PermissionError('Reward account does not match authenticated client')



            cloud = client(self.db)



            binding, _ = cloud.binding()



            if binding['account_id'] != principal.account_id: raise PermissionError('Reward account mismatch')



            if path.endswith('/select') or path.endswith('/transfer'):



                raise ValueError('Claim the shared 20-minute entitlement directly; video binding and device transfers are no longer required')



            result = cloud.request(action, body.get('reward_id'))



            result['client_id'] = binding['device_id']



            if action in ('activate','stop') and self.restriction_mgr:



                self.restriction_mgr._last_policy_reconcile_attempt['youtube'] = 0



                self.restriction_mgr.reconcile_browser_policies_if_needed()



            active = result.get('active_session')



            if active:



                result['active_session'] = dict(state='ACTIVE', remaining_seconds=active['remaining_seconds'], reward=active)



            result['total_available'] = len(result.get('available_rewards',[]))



            self._send_json(result)



        except (PermissionError,AssistantError) as error:



            if local_status and local_status.get("is_active"):



                self._send_json(local_status)



            else:



                self._send_json(dict(success=False, code='UNAUTHORIZED', error=str(error)), status=403)



        except Exception as error:



            if action == 'state' and (local_status.get('is_active') or local_status.get('total_available',0)>0):



                self._send_json(local_status)



            else:



                self._send_json(dict(success=False, code='REWARD_AUTHORITY_UNAVAILABLE', error=str(error), active_session=None, available_rewards=[]), status=503)







    def _handle_youtube_educational(self, path: str, body=None, query=None):



        from core.entertainment_reward_engine import EntertainmentRewardEngine, extract_canonical_video_id



        from core.taufa_video_evaluator import TaufaVideoEvaluator



        body, query = body or {}, query or {}



        engine = EntertainmentRewardEngine(self.db)



        evaluator = TaufaVideoEvaluator()







        if path == "/api/youtube/educational/history":



            limit = int(query.get("limit", [50])[0]) if query.get("limit") else 50



            history = self.db.get_educational_validation_history("user_lok", limit=limit) if self.db else []



            self._send_json({"success": True, "history": history, "count": len(history)})



            return







        if path == "/api/youtube/educational/status":



            vid = query.get("video_id", [body.get("video_id", "")])[0]



            clean_vid = extract_canonical_video_id(vid) or vid.strip()



            if not clean_vid:



                self._send_json({"success": False, "error": "Missing video_id"}, status=400)



                return



            decision = engine.get_educational_video_access_decision(clean_vid, account_id="user_lok")



            self._send_json({"success": True, "video_id": clean_vid, **decision})



            return







        if path == "/api/youtube/educational/metadata":



            url = body.get("url") or query.get("url", [""])[0]



            vid = body.get("video_id") or query.get("video_id", [""])[0]



            try:



                from windows.strictrecords_native_host import extract_metadata



                meta = extract_metadata(url, vid)



                self._send_json(meta)



            except Exception as ex:



                self._send_json({"success": False, "error": str(ex)}, status=500)



            return







        if path == "/api/youtube/educational/request":



            try:



                url = body.get("url") or ""



                vid = body.get("video_id") or extract_canonical_video_id(url) or ""



                clean_vid = extract_canonical_video_id(vid) or vid.strip()



                if not clean_vid:



                    self._send_json({"success": False, "error": "Valid YouTube URL or video ID is required."}, status=400)



                    return







                req_id = body.get("request_id") or f"req_{uuid.uuid4().hex[:12]}"



                val_sess_id = body.get("validation_session_id") or f"val_{uuid.uuid4().hex[:12]}"



                user_id = body.get("user_id") or body.get("account_id") or "user_lok"



                dev_id = body.get("device_id") or "BROWSER_EXT"



                v_title = body.get("title") or "YouTube Video"



                v_channel = body.get("channel") or ""







                existing = engine.get_educational_approval(clean_vid, account_id=user_id)



                if existing and existing.get("decision") in ("EDUCATIONAL", "NOT_EDUCATIONAL"):



                    if existing.get("decision") == "EDUCATIONAL":



                        engine.start_or_resume_playback_session(clean_vid, account_id=user_id, device_id=dev_id)



                        if self.db:



                            exp_ms = int(now_ist().timestamp() * 1000) + (existing.get("duration_seconds", 1205) + 300) * 1000



                            self.db.set_config_value("educational_unlock_active", "1")



                            self.db.set_config_value("educational_active_video_id", clean_vid)



                            self.db.set_config_value("educational_unlock_reason", f"Educational video approved: {v_title}")



                            self.db.set_config_value("educational_unlock_expires_at", str(exp_ms))



                        if self.restriction_mgr:



                            self.restriction_mgr.clear_caches()



                            self.restriction_mgr.educational_unlock_active = True



                            self.restriction_mgr.educational_unlock_reason = f"Educational video approved: {v_title}"



                            self.restriction_mgr.educational_unlock_expires_at = datetime.fromtimestamp(exp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")



                    self._send_json({"success": True, "cached": True, **existing})



                    return







                # Auto-enrich metadata if description, title, or duration are missing/generic



                if (not body.get("description") and not body.get("captions_text")) or not body.get("duration_seconds") or not body.get("title") or body.get("title") == "YouTube Video":



                    try:



                        from windows.strictrecords_native_host import extract_metadata



                        meta_res = extract_metadata(url or f"https://www.youtube.com/watch?v={clean_vid}", clean_vid)



                        if meta_res.get("success"):



                            if not body.get("title") or body.get("title") == "YouTube Video":



                                v_title = meta_res.get("title") or v_title



                            if not body.get("channel"):



                                v_channel = meta_res.get("channel") or v_channel



                            if not body.get("duration_seconds"):



                                body["duration_seconds"] = meta_res.get("duration_seconds")



                            if not body.get("description"):



                                body["description"] = meta_res.get("description")



                            if not body.get("chapters"):



                                body["chapters"] = meta_res.get("chapters")



                    except Exception as meta_err:



                        logger.warning("Could not auto-fetch video metadata via native host: %s", meta_err)







                eval_result = evaluator.evaluate_video(



                    url=url,



                    video_id=clean_vid,



                    title=v_title,



                    channel=v_channel,



                    duration_seconds=body.get("duration_seconds"),



                    captions_text=body.get("captions_text"),



                    description=body.get("description"),



                    chapters=body.get("chapters"),



                    language=body.get("language"),



                    user_id=user_id,



                    device_id=dev_id,



                    request_id=req_id,



                    validation_session_id=val_sess_id,



                    timeout=30.0,



                )







                canonical_url = eval_result.get("canonical_url") or f"https://www.youtube.com/watch?v={clean_vid}"



                dur_sec = eval_result.get("duration_seconds", 0) or body.get("duration_seconds") or 67



                dur_fmt = eval_result.get("duration_formatted", "Unknown")



                if dur_fmt == "Unknown" and dur_sec:



                    dur_fmt = f"{dur_sec//60:02d}:{dur_sec%60:02d}"







                if eval_result.get("decision") in ("PROCESSING_ERROR", "INSUFFICIENT_EVIDENCE", "NEEDS_REVIEW"):



                    logger.info("Taufa AI returned %s in server; engaging Brave ChatGPT / curriculum fallback", eval_result.get("decision"))



                    fallback_res = evaluator._evaluate_via_curriculum_or_chatgpt(



                        video_id=clean_vid,



                        canonical_url=canonical_url,



                        title=v_title,



                        channel=v_channel,



                        duration_sec=dur_sec,



                        dur_formatted=dur_fmt,



                        clean_desc=body.get("description"),



                        chapters=body.get("chapters"),



                        req_id=req_id,



                        val_sess_id=val_sess_id



                    )



                    if fallback_res.get("decision") in ("EDUCATIONAL", "NOT_EDUCATIONAL"):



                        eval_result = fallback_res



                decision = eval_result.get("decision", "PROCESSING_ERROR")







                saved = engine.save_educational_approval(



                    video_id=clean_vid,



                    canonical_url=canonical_url,



                    decision=decision,



                    duration_seconds=dur_sec,



                    duration_formatted=dur_fmt,



                    reason=eval_result.get("reason"),



                    confidence=eval_result.get("confidence"),



                    timeline=eval_result.get("timeline"),



                    title=v_title,



                    channel=v_channel,



                    account_id=user_id,



                    review_status=eval_result.get("review_status", "COMPLETED"),



                )







                if self.db:



                    hist_id = f"hist_{clean_vid}_{uuid.uuid4().hex[:8]}"



                    self.db.save_educational_validation_history(



                        history_id=hist_id,



                        request_id=req_id,



                        validation_session_id=val_sess_id,



                        video_id=clean_vid,



                        canonical_url=canonical_url,



                        decision=decision,



                        duration_seconds=dur_sec,



                        duration_formatted=dur_fmt,



                        title=v_title,



                        channel=v_channel,



                        reason=eval_result.get("reason"),



                        confidence=eval_result.get("confidence"),



                        review_status=eval_result.get("review_status", "COMPLETED"),



                        playback_completion_state="NOT_STARTED",



                        account_id=user_id,



                        device_id=dev_id,



                    )







                if decision == "EDUCATIONAL":



                    engine.start_or_resume_playback_session(clean_vid, account_id=user_id, device_id=dev_id)



                    if self.db:



                        exp_ms = int(now_ist().timestamp() * 1000) + (dur_sec + 300) * 1000



                        self.db.set_config_value("educational_unlock_active", "1")



                        self.db.set_config_value("educational_active_video_id", clean_vid)



                        self.db.set_config_value("educational_unlock_reason", f"Educational video approved: {v_title}")



                        self.db.set_config_value("educational_unlock_expires_at", str(exp_ms))



                    if self.restriction_mgr:



                        self.restriction_mgr.clear_caches()



                        self.restriction_mgr.educational_unlock_active = True



                        self.restriction_mgr.educational_unlock_reason = f"Educational video approved: {v_title}"



                        self.restriction_mgr.educational_unlock_expires_at = datetime.fromtimestamp(exp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")







                self._send_json({"success": True, **saved, "taufa_result": eval_result})



                return



            except Exception as req_ex:



                logger.error("Error handling educational request: %s", req_ex, exc_info=True)



                self._send_json({



                    "success": False,



                    "decision": "PROCESSING_ERROR",



                    "reason": f"Evaluation error: {str(req_ex)[:160]}",



                    "error": str(req_ex),



                    "review_status": "MANUAL_REVIEW"



                })



                return







        if path == "/api/youtube/educational/heartbeat":



            vid = body.get("video_id") or ""



            clean_vid = extract_canonical_video_id(vid) or vid.strip()



            if not clean_vid:



                self._send_json({"success": False, "error": "Missing video_id"}, status=400)



                return



            result = engine.record_playback_heartbeat(



                video_id=clean_vid,



                account_id="user_lok",



                device_id=body.get("device_id", "BROWSER_EXT"),



                playback_state=body.get("playback_state", "PLAYING"),



                playback_rate=float(body.get("playback_rate", 1.0)),



                interval_start=body.get("interval_start"),



                interval_end=body.get("interval_end"),



                current_time=body.get("current_time"),



            )



            if result.get("completed") and self.db:



                self.db.update_educational_history_playback_state(clean_vid, "COMPLETED", "user_lok")



                self.db.set_config_value("educational_unlock_active", "0")



                if self.restriction_mgr:



                    self.restriction_mgr.clear_caches()



            self._send_json(result)



            return







        if path == "/api/youtube/educational/start":



            vid = body.get("video_id") or ""



            clean_vid = extract_canonical_video_id(vid) or vid.strip()



            res = engine.start_or_resume_playback_session(clean_vid, account_id="user_lok", device_id=body.get("device_id", "BROWSER_EXT"))



            self._send_json(res)



            return







    def _handle_early_sleep_reward(self, path: str, body=None, query=None):



        from core.early_sleep_reward_engine import EarlySleepRewardEngine



        body, query = body or {}, query or {}



        esr_engine = EarlySleepRewardEngine(self.db)



        account_id = body.get('account_id') or (query.get('account_id', ['user_lok'])[0] if isinstance(query.get('account_id'), list) else query.get('account_id', 'user_lok'))







        if path == "/api/rewards/early_sleep/status":



            target_date = body.get('date') or (query.get('date', [None])[0] if isinstance(query.get('date'), list) else query.get('date', None))



            status = esr_engine.get_todays_reward_status(account_id=account_id, target_date=target_date)



            self._send_json(status)



            return







        if path == "/api/rewards/early_sleep/select":



            reward_id = body.get('reward_id')



            video_url_or_id = body.get('video_id') or body.get('url') or ""



            title = body.get('title')



            duration = body.get('duration_seconds')



            if not reward_id:



                todays = esr_engine.get_todays_reward_status(account_id=account_id)



                reward_id = todays.get('reward_id')



            if not reward_id:



                self._send_json({"success": False, "code": "NO_REWARD", "error": "No Early Sleep Reward available today."}, status=400)



                return



            res = esr_engine.bind_video(reward_id, video_url_or_id, title=title, duration_seconds=duration)



            if self.restriction_mgr:



                self.restriction_mgr.clear_caches()



                self.restriction_mgr.reconcile_browser_policies_if_needed()



            self._send_json(res)



            return







        if path == "/api/rewards/early_sleep/heartbeat":



            reward_id = body.get('reward_id')



            video_id = body.get('video_id') or ""



            if not reward_id:



                todays = esr_engine.get_todays_reward_status(account_id=account_id)



                reward_id = todays.get('reward_id')



            if not reward_id:



                self._send_json({"success": False, "code": "NO_REWARD", "error": "No Early Sleep Reward found."}, status=400)



                return



            res = esr_engine.record_playback_heartbeat(



                reward_id=reward_id,



                video_id=video_id,



                playback_state=body.get('playback_state', 'PLAYING'),



                current_time=float(body.get('current_time', 0.0)),



                duration=float(body.get('duration', 0.0)),



                completed=bool(body.get('completed', False)),



            )



            if res.get('completed') and self.restriction_mgr:



                self.restriction_mgr.clear_caches()



                self.restriction_mgr.reconcile_browser_policies_if_needed()



            self._send_json(res)



            return







        if path == "/api/rewards/early_sleep/sync_sleep":



            wake_date = body.get('wake_date') or body.get('report_date')



            sleep_onset = body.get('sleep_onset')



            wake_up = body.get('wake_up')



            source = body.get('source_identity') or "Mi Fitness Wearable"



            sess_id = body.get('sleep_session_id') or body.get('submission_id')



            sync_ts = body.get('sync_timestamp') or body.get('created_at')



            verified = body.get('verified', True)



            if not wake_date or not sleep_onset or not wake_up:



                self._send_json({"success": False, "error": "wake_date, sleep_onset, and wake_up are required."}, status=400)



                return



            res = esr_engine.evaluate_and_grant_reward(



                account_id=account_id,



                wake_date=wake_date,



                sleep_onset_raw=sleep_onset,



                wake_up_raw=wake_up,



                source_identity=source,



                sleep_session_id=sess_id,



                sync_timestamp=sync_ts,



                verified=verified,



            )



            if self.restriction_mgr:



                self.restriction_mgr.clear_caches()



                self.restriction_mgr.reconcile_browser_policies_if_needed()



            self._send_json(res)



            return







    def _handle_night_request(self, path: str, body=None, query=None):



        body, query = body or {}, query or {}



        from windows.night_shutdown import NightStore



        store = NightStore()



        action = path.replace("/api/night/", "").strip().lower()







        if action == "context":



            profile = body.get("profile_id") or query.get("profile_id", [""])[0] or ""



            res = store.context(profile=profile)



            self._send_json(res)



            return







        if action == "checkpoint":



            rec = body.get("record") or body



            res = store.checkpoint(rec)



            self._send_json(res)



            return







        if action == "save":



            rec = body.get("record") or body



            res = store.save(rec)



            self._send_json(res)



            return







        if action == "commit":



            run_id = body.get("run_id", "")



            profile = body.get("profile_id", "")



            session_ids = body.get("session_ids") or []



            res = store.commit_profile(run_id, profile, session_ids)



            self._send_json(res)



            return







        if action == "history":



            profile = body.get("profile_id") or query.get("profile_id", [""])[0] or ""



            limit = int(query.get("limit", [50])[0]) if query.get("limit") else 50



            records = store.history(profile=profile, limit=limit)



            self._send_json({"success": True, "records": records})



            return







        if action in ("trigger_shutdown", "close_browser"):



            try:



                run_id = body.get("run_id")



                from windows.restriction_manager import Preferences



                from windows.night_platform import close_targets, request_shutdown



                run = store.run(run_id) if run_id else store.context().get("run")



                if run:



                    prefs = Preferences().load()



                    results = close_targets(run, self.db.get_settings(), True, prefs.get("exclusions", ()))



                    store.update_run(run["run_id"], close_results=json.dumps(results))



                    now_ms = int(time.time() * 1000)



                    if now_ms >= run.get("shutdown_ms", 0):



                        store.update_run(run["run_id"], state="SHUTDOWN_REQUESTED", shutdown_status="SHUTDOWN_REQUESTED")



                        request_shutdown(run)



                    self._send_json({"success": True, "results": results, "shutdown_ready": True})



                    return



            except Exception as e:



                logger.warning("Night trigger_shutdown error: %s", e)



            self._send_json({"success": True, "shutdown_ready": True})



            return







        self._send_json({"success": False, "error": f"Unknown night action: {action}"}, status=404)







    def _handle_gym_request(self, path, body):



        from core.gym_daily_decision import DailyGymDecision, GymDecisionError



        from core.gym_mode_engine import GymModeEngine



        from core.daily_college_decision import DailyCollegeDecision



        try:



            principal = self._gym_principal(body)



            if body.get('account_id') and body['account_id'] != principal.account_id:



                raise PermissionError('The pending Gym request belongs to a different account')



            engine = GymModeEngine(self.db)



            if path == '/api/planning/college':



                result = DailyCollegeDecision(self.db).save(principal.account_id,body.get('date'),body.get('attends_college'),



                    body.get('expected_revision'),body.get('request_id'),bath_plans=body.get('bath_plans'),



                    actor=principal.actor_id,device_id=principal.device_id,timezone=body.get('timezone','Asia/Kolkata'))



            elif path == '/api/gym/decision':



                result = DailyGymDecision(self.db).save(principal.account_id, body.get('date'), body.get('gym_planned'),



                    body.get('expected_revision'), body.get('request_id'), actor=principal.actor_id,



                    device_id=principal.device_id, timezone=body.get('timezone','Asia/Kolkata'), admin_authorized=principal.is_admin)



                result['state'] = engine.state(principal.account_id)



            elif path == '/api/gym_mode/activate':



                revision = body.get('expected_revision')



                if type(revision) is not int:



                    raise GymDecisionError('REVISION_REQUIRED','Refresh the confirmed daily Gym decision before activating',409)



                activity=body.get('gym_activity_id')



                request_id=body.get('request_id')



                if not isinstance(activity,str) or not 1<=len(activity)<=128 or not isinstance(request_id,str) or not 8<=len(request_id)<=128:



                    raise GymDecisionError('INVALID_ACTIVATION','Select a valid Gym session and supply a stable activation request ID')



                result = engine.activate_session(body.get('gym_activity_id'), account_id=principal.account_id,



                    expected_revision=revision,request_id=body.get('request_id'))



            elif path == '/api/gym_mode/reconcile_offline':



                result = engine.reconcile_offline_activation(



                    body.get('gym_activity_id'),



                    body.get('activation_time_ms'),



                    body.get('request_id'),



                    session_id=body.get('session_id'),



                    account_id=principal.account_id,



                    timezone=body.get('timezone', 'Asia/Kolkata'),



                )



            elif path == '/api/gym_mode/deactivate':



                if body.get('session_id') is not None and (not isinstance(body['session_id'],str) or len(body['session_id'])>128):



                    raise GymDecisionError('INVALID_SESSION','Select a valid active Gym session')



                result = engine.deactivate_early(principal.account_id,session_id=body.get('session_id'))



            else:



                result = dict(success=True,state=engine.state(principal.account_id))



                today=result['state']['date']



                tomorrow=TimeWindowEngine.get_next_calendar_date(today)



                result['planning']={day:dict(gym=DailyGymDecision(self.db).get(principal.account_id,day),



                    college=DailyCollegeDecision(self.db).get(principal.account_id,day)) for day in (today,tomorrow)}



            self._send_json(result,status=200 if result.get('success') else 409)



        except (GymDecisionError, AssistantError) as error:



            self._send_json(dict(success=False,code=error.code,error=str(error)),status=error.status)



        except PermissionError as error:



            self._send_json(dict(success=False,code='UNAUTHORIZED',error=str(error)),status=403)



        except (ValueError,TypeError) as error:



            self._send_json(dict(success=False,code='INVALID_REQUEST',error=str(error)),status=400)







    def _handle_gym_routine(self, body=None, query=None):



        from core.gym_routine import GymRoutine



        from core.gym_daily_decision import GymDecisionError



        import sqlite3



        mutating=body is not None



        body,query=body or {},query or {}



        try:



            principal=self._gym_principal(body)



            requested=body.get('account_id') or query.get('account_id',[principal.account_id])[0]



            if requested != principal.account_id:



                raise PermissionError('The Gym routine belongs to a different authenticated account')



            service=GymRoutine(self.db)



            if mutating:



                result=service.mutate(principal.account_id,body.get('action'),body.get('expected_revision'),body.get('request_id'),



                    reason_category=body.get('reason_category',''),note=body.get('note',''),reactivation_date=body.get('reactivation_date'),



                    actor_id=principal.actor_id,device_id=principal.device_id)



            else:



                result=service.get(principal.account_id,month=query.get('month',[None])[0],



                    filter=query.get('filter',query.get('period',['THIS_MONTH']))[0],



                    start_date=query.get('start_date',query.get('from',[None]))[0],



                    end_date=query.get('end_date',query.get('to',[None]))[0])



            self._send_json(result)



        except (GymDecisionError,AssistantError) as error:



            self._send_json(dict(success=False,code=error.code,error=str(error)),status=error.status)



        except PermissionError as error:



            self._send_json(dict(success=False,code='UNAUTHORIZED',error=str(error)),status=403)



        except (ValueError,TypeError) as error:



            self._send_json(dict(success=False,code='INVALID_REQUEST',error=str(error)),status=400)



        except sqlite3.Error:



            self._send_json(dict(success=False,code='PERSISTENCE_UNAVAILABLE',error='The routine could not be saved or refreshed. Retry with the same request ID.'),status=503)







    def _scoped_event(self, event, principal):



        if event.get("event_type") == "APK_UPDATE_AVAILABLE":



            return event  # Published release manifests are public to registered clients.



        owner = event.get("account_id") or event.get("user_id")



        with self.db._connect() as conn:



            if not owner and event.get("activity_id"):



                row = conn.execute("SELECT account_id FROM scheduled_activities WHERE activity_id=?", (event["activity_id"],)).fetchone()



                owner = row["account_id"] if row else None



            if not owner and event.get("device_id"):



                row = conn.execute("SELECT user_id FROM devices WHERE device_id=?", (event["device_id"],)).fetchone()



                owner = row["user_id"] if row else None



        if owner and owner != principal.account_id:



            return None



        # Unscoped old events are refresh signals; no private review payload is sent.



        fields = {"event_id", "event_type", "timestamp"}



        if owner == principal.account_id:



            fields.update({



                "account_id", "activity_id", "submission_id", "schedule_version", "schedule_versions",



                "status", "state", "gate_state", "active", "activated_at", "window_start", "window_end", "date",



                "proposal_id", "evidence_id", "title", "slot", "notification", "decision", "proposed_points", "has_evidence",



                "session_id", "device_id", "user_id", "start_timestamp", "eligible_proof_schedule",



                "submission_window_status", "allowed_packages", "reason", "command_type", "command_id", "action", "duration_seconds",



                "session_phase", "phone_usage", "phase_expires_at", "policy_reason",
                "preparation_id", "prep_id"



            })



        return {k: v for k, v in event.items() if k in fields}







    def _handle_strictbot(self, path, body=None, query=None, received_at=None):



        body, query = body or {}, query or {}



        try:



            principal = self._strictbot_principal(body, query)



            service = type(self).strictbot_service



            if service is None or service.db is not self.db:



                # Reuse one authoritative service; no background scheduler is created here.



                with _STRICTBOT_SERVICE_LOCK:



                    service = type(self).strictbot_service



                    if service is None or service.db is not self.db:



                        service = StrictBotAssistant(self.db, self.engine, self.restriction_mgr)



                        type(self).strictbot_service = service



            capabilities = body.get("client_permission_state")



            if capabilities is None and self.headers.get("X-Client-Permission-State"):



                try:



                    capabilities = json.loads(self.headers["X-Client-Permission-State"])



                except (ValueError, TypeError) as exc:



                    raise AssistantError("INVALID_PERMISSIONS", "Invalid permission observation.") from exc



            if capabilities is not None:



                service.save_capabilities(principal, capabilities)



            if path.endswith("/chat"):



                result = service.chat(principal, body.get("message"), body.get("request_id"), body.get("conversation_id"), received_at=received_at)



            elif path.endswith("/action"):



                result = service.action(principal, body.get("action"), body.get("inputs", {}),



                    body.get("request_id"), body.get("confirmation", False), received_at=received_at)



            elif path.endswith("/history"):



                result = service.history(principal, query.get("conversation_id", [None])[0])



            else:



                result = service.context(principal, query.get("topic", ["general"])[0],



                    {k: query[k][0] for k in ("date", "evidence_id") if k in query})



            self._send_json(result, status=int(result.get("http_status", 200)))



        except AssistantError as exc:



            self._send_json({"success": False, "state": "FAILED", "code": exc.code,



                "error": str(exc), "reply": str(exc)}, status=exc.status)



        except PermissionError as exc:



            self._send_json({"success": False, "state": "FAILED", "code": "UNAUTHORIZED_DEVICE",



                "error": str(exc), "reply": str(exc)}, status=403)



        except Exception:



            logger.exception("StrictBot application service failed")



            self._send_json({"success": False, "state": "FAILED", "code": "SERVICE_ERROR",



                "error": "The application service is unavailable. No completion is confirmed.",



                "reply": "The application service is unavailable. No completion is confirmed."}, status=503)







    def _handle_device_proof_evidence(self, query: Dict[str, List[str]]) -> None:



        """Read-only, account-scoped preview; never accepts client file paths."""



        try:



            authorization = self.headers.get("Authorization", "")



            token = authorization[7:] if authorization.startswith("Bearer ") else ""



            account_id = self._get_authenticated_device_account(query.get("device_id", [""])[0], token)



            with self.db._connect() as conn:



                evidence = conn.execute(



                    """SELECT ef.file_path, ef.sha256_hash FROM evidence_files ef



                    JOIN proof_reviews pr ON pr.evidence_id = ef.evidence_id



                    WHERE ef.evidence_id = ? AND pr.user_id = ? AND ef.is_deleted = 0""",



                    (query.get("evidence_id", [""])[0], account_id),



                ).fetchone()



            if not evidence:



                self._send_json({"error": "Evidence attachment unavailable"}, status=404)



                return



            file_path = os.path.realpath(evidence["file_path"])



            root = os.path.realpath(self.engine.storage_root)



            if os.path.commonpath([root, file_path]) != root:



                self._send_json({"error": "Evidence attachment unavailable"}, status=404)



                return



            with open(file_path, "rb") as source:



                raw = source.read(10 * 1024 * 1024 + 1)



            if not raw or len(raw) > 10 * 1024 * 1024:



                self._send_json({"error": "Evidence attachment unavailable"}, status=422)



                return



            import io



            from PIL import Image



            with Image.open(io.BytesIO(raw)) as image:



                mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(image.format)



                image.verify()



            if not mime or (evidence["sha256_hash"] and hashlib.sha256(raw).hexdigest() != evidence["sha256_hash"]):



                self._send_json({"error": "Evidence integrity check failed"}, status=422)



                return



            self.send_response(200)



            self.send_header("Content-Type", mime)



            self.send_header("Content-Length", str(len(raw)))



            self.send_header("Cache-Control", "private, no-store")



            self.send_header("X-Content-Type-Options", "nosniff")



            self.end_headers()



            self.wfile.write(raw)



        except PermissionError:



            self._send_json({"error": "Device authorization required"}, status=403)



        except (OSError, ValueError):



            self._send_json({"error": "Evidence attachment unavailable"}, status=404)







    def _start_hourly_ai_review(self, accepted: Dict[str, Any], file_bytes: bytes) -> None:



        review_key = (id(self.db), accepted.get("account_id"), accepted["submission_id"])



        with self._hourly_review_guard:



            if review_key in self._hourly_reviews_inflight:



                return



            self._hourly_reviews_inflight.add(review_key)



        def _run_ai_pipeline() -> None:



            from core.time_window_engine import TimeWindowEngine







            twe = TimeWindowEngine(self.db)



            activity_id = accepted["activity_id"]



            evidence_id = accepted.get("evidence_id")



            committed_review = None



            try:



                # Transition status to AI_ANALYZING



                if evidence_id:



                    with self.db._connect() as conn:



                        prior = conn.execute("SELECT status,reviewed_by,signals_json FROM proof_reviews WHERE evidence_id=?", (evidence_id,)).fetchone()



                        if prior and prior["status"] == "APPROVED" and str(prior["reviewed_by"]).upper() == "TAUFA_AI":



                            stored = json.loads(prior["signals_json"] or "{}")



                            activity = conn.execute("SELECT validation_status FROM scheduled_activities WHERE activity_id=?", (activity_id,)).fetchone()



                            credit = conn.execute("SELECT 1 FROM score_events WHERE evidence_id=? AND points>0 LIMIT 1", (evidence_id,)).fetchone()



                            if stored.get("qualifies_for_points") is True and (not credit or not activity or activity["validation_status"] != "VERIFIED"):



                                # Recover an interruption between the existing approval,



                                # ledger and activity commits from the saved genuine result.



                                committed_review = stored



                        changed = conn.execute(



                            "UPDATE proof_reviews SET status = 'AI_ANALYZING' WHERE evidence_id = ? AND status NOT IN ('APPROVED','VERIFIED','REJECTED','MANUALLY_APPROVED')",



                            (evidence_id,),



                        )



                        if changed.rowcount == 0 and committed_review is None:



                            return



                EventBus.get_instance().publish(



                    "AI_ANALYZING",



                    {



                        "activity_id": activity_id,



                        "submission_id": accepted.get("submission_id"),



                        "evidence_id": evidence_id,



                    },



                    event_id=f"ai_analyzing_{accepted.get('submission_id')}",



                )



                from windows.submission_window_manager import SubmissionWindowManager



                if accepted.get("task_type", "HOURLY") == "HOURLY":



                    SubmissionWindowManager.get_instance(db=self.db).start_ai_validation(



                        activity_id, schedule_version=accepted.get("schedule_version")



                    )



            except Exception:



                logger.exception("Could not update the hourly validation window state")



            try:



                if committed_review is not None:



                    ai_rev = committed_review



                    taufa_handled = True



                else:



                    history = self.db.get_historical_evidence_for_ai(



                        limit=200,



                        exclude_evidence_id=accepted.get("evidence_id"),



                        exclude_submission_id=accepted.get("submission_id"),



                    )



                    image_b64 = base64.b64encode(file_bytes).decode("ascii")



                    # One genuine Taufa result feeds persistence and the existing score authority.



                    taufa_handled = str(accepted.get("task_type") or "HOURLY").upper() == "HOURLY"



                    if taufa_handled:



                        from core.taufa_ai_validator import TaufaAiValidator



                        with self.db._connect() as conn:



                            retained = conn.execute("SELECT evidence_id,file_path,sha256_hash FROM evidence_files WHERE related_record_id=? AND is_deleted=0", (accepted["submission_id"],)).fetchall()



                        retained_by_id = {row["evidence_id"]: row for row in retained}



                        ids = accepted.get("evidence_ids") or [accepted["evidence_id"]]



                        images = []



                        for identifier in ids:



                            row = retained_by_id.get(identifier)



                            if not row or not os.path.isfile(row["file_path"]):



                                raise RuntimeError("A retained attachment is unavailable; retry after restoring the upload.")



                            with open(row["file_path"], "rb") as photo:



                                raw = photo.read(25 * 1024 * 1024 + 1)



                            if hashlib.sha256(raw).hexdigest() != row["sha256_hash"]:



                                raise RuntimeError("Retained evidence integrity check failed.")



                            images.append(raw)



                        # Build per-image filenames from evidence files metadata



                        image_filenames = []



                        for _iid in ids:



                            _meta_row = retained_by_id.get(_iid)



                            try:



                                with self.db._connect() as _mc:



                                    _mj = _mc.execute("SELECT metadata_json FROM evidence_files WHERE evidence_id=?", (_iid,)).fetchone()



                                image_filenames.append(



                                    (json.loads(_mj["metadata_json"] or "{}") if _mj else {}).get("original_filename") or f"evidence_{_iid}.jpg"



                                )



                            except Exception:



                                image_filenames.append(f"evidence_{_iid}.jpg")



                        taufa_failed_technically = False



                        try:



                            taufa_res = TaufaAiValidator().validate_hourly_submission(



                                submission_id=accepted["submission_id"], category=accepted["category"],



                                description=accepted["description"], images=images,



                                task_type="HOURLY", historical_records=history, check_duplicate=True,



                                image_filenames=image_filenames)



                            if getattr(taufa_res, "is_technical_failure", False) or getattr(taufa_res, "decision", "") == "PROCESSING_ERROR":



                                taufa_failed_technically = True



                            else:



                                with self.db._connect() as conn:



                                    for identifier, analysis in zip(ids, taufa_res.evidence_analysis):



                                        row = conn.execute("SELECT metadata_json FROM evidence_files WHERE evidence_id=?", (identifier,)).fetchone()



                                        metadata = json.loads(row["metadata_json"] or "{}") if row else {}



                                        ocr = analysis.get("ocr") or {}



                                        metadata.update({"ocr_status": ocr.get("status", "NOT_RECORDED"), "ocr_text": str(ocr.get("extracted_text") or "")[:4000]})



                                        conn.execute("UPDATE evidence_files SET metadata_json=? WHERE evidence_id=?", (json.dumps(metadata), identifier))



                                ai_rev = taufa_res.as_review(activity_id, accepted["evidence_id"])



                        except Exception as taufa_err:



                            logger.warning("Taufa AI experienced technical error: %s; invoking sequential OpenAI fallback", taufa_err)



                            taufa_failed_technically = True







                        if taufa_failed_technically:



                            logger.info("Executing authoritative OpenAI Multi-Profile sequential fallback for submission %s", accepted["submission_id"])



                            from core.openai_fallback_validator import OpenAiMultiProfileManager



                            fb_res = OpenAiMultiProfileManager.get_instance(db=self.db).validate_with_sequential_fallback(



                                submission_id=accepted["submission_id"],



                                category=accepted["category"],



                                description=accepted["description"],



                                images=images,



                                image_filenames=image_filenames,



                                window_start=accepted.get("window_start"),



                                window_end=accepted.get("window_end"),



                                submission_timestamp=accepted["accepted_at"],



                                activity_id=activity_id,



                            )



                            ai_rev = fb_res.as_review(activity_id, accepted["evidence_id"])







                        ai_rev["metadata"] = {"reviewed_at": accepted["accepted_at"], "platform": accepted.get("source_platform")}



                        ai_rev["timing"] = {"submitted_at": accepted["accepted_at"]}



                    else:



                        ai_rev = StrictBotAIReviewer.execute_review(



                            submission_id=accepted["submission_id"],



                            activity_id=activity_id,



                            task_code=accepted.get("task_type", "HOURLY"),



                            description=accepted["description"],



                            primary_activity=accepted["category"],



                            submitted_at_iso=accepted["accepted_at"],



                            device_id=accepted["source_device_id"],



                            platform=accepted["source_platform"],



                            image_base64=image_b64,



                            window_start_iso=accepted.get("window_start"),



                            window_end_iso=accepted.get("window_end"),



                            hour_slot=accepted.get("hour_slot"),



                            historical_records=history,



                            configured_points=int(accepted.get("configured_points") or 8),



                            auto_approve_high_confidence=True,



                            auto_approve_duplicate=False,



                            evidence_id=accepted.get("evidence_id"),



                        )



                    ai_rev["evidence_id"] = accepted["evidence_id"]



                self.db.save_strictbot_ai_review(ai_rev)



                try:



                    from core.time_window_engine import TimeWindowEngine



                    validation = ai_rev.get("decision") or "NEEDS_REVIEW"



                    reviewer_id = ai_rev.get("validator") or ("Taufa_AI" if taufa_handled else "StrictBot_AI")



                    is_hourly = str(accepted.get("task_type") or "HOURLY").strip().upper() == "HOURLY"



                    if is_hourly:



                        if ai_rev.get("is_technical_failure"):



                            validation = "VALIDATION_PENDING"



                            pts = 0



                        elif validation == "PROVISIONAL_APPROVE" or ai_rev.get("approved"):



                            validation = "PROVISIONAL_APPROVE"



                            pts = int(ai_rev.get("awarded_points") or ai_rev.get("proposed_points") or 0)



                            if pts <= 0 and ai_rev.get("approved"):



                                pts = 5



                        elif validation in ("REJECTED", "PROVISIONAL_REJECT"):



                            validation = "REJECTED"



                            pts = 0



                        else:



                            validation = "NEEDS_REVIEW"



                            pts = 0



                        if validation == "PROVISIONAL_APPROVE":



                            approval = self.engine.apply_proof_approval(



                                evidence_id=accepted["evidence_id"], admin_id=reviewer_id, points=pts,



                                reason=f"AI verified: {', '.join(ai_rev.get('reasons', [])) or 'Valid submission'}")



                            if approval.get("success") is False:



                                raise RuntimeError("The score authority did not commit this approval.")



                            twe.record_activity_verification(



                                activity_id, True, pts, evidence_id=accepted["evidence_id"], provisional=False



                            )



                            try:



                                from core.hourly_submission_service import HourlySubmissionService



                                day_score = int(self.engine.recalculate_daily_score(accepted.get("date") or now_ist().strftime("%Y-%m-%d")).get("final_score", 0))



                                HourlySubmissionService(self.db, self.engine).publish_review_event(



                                    account_id=accepted.get("account_id") or "user_lok",



                                    activity_id=activity_id,



                                    evidence_id=accepted["evidence_id"],



                                    action="APPROVE",



                                    status="APPROVED",



                                    points=pts,



                                    admin_id=reviewer_id,



                                    reason_note=f"AI verified: {', '.join(ai_rev.get('reasons', []))}",



                                    shared_score=day_score,



                                )



                            except Exception:



                                logger.exception("Could not publish durable AI approval event")







                            # The enrolled Android evidence authority reconciles the persisted



                            # acceptance + final approval into the shared reward store. Never



                            # mint a second PC-local entitlement from an AI callback.



                    elif validation in ("PROVISIONAL_REJECT", "REJECTED"):



                        validation = "REJECTED"



                    else:



                        validation = "NEEDS_REVIEW"



                    final_validation_status = "VERIFIED" if (validation == "PROVISIONAL_APPROVE" and is_hourly) else validation



                    twe.record_activity_validation_status(activity_id, final_validation_status, ai_rev)



                    if is_hourly and validation != "PROVISIONAL_APPROVE":



                        from core.hourly_submission_service import HourlySubmissionService



                        HourlySubmissionService(self.db, self.engine).publish_review_event(



                            account_id=accepted.get("account_id") or "user_lok", activity_id=activity_id,



                            evidence_id=accepted["evidence_id"], action="ANALYSIS", status=("RETRYABLE_ERROR" if validation == "PROCESSING_ERROR" else validation),



                            points=0, admin_id=reviewer_id, reason_note="; ".join(ai_rev.get("reasons", [])))



                except Exception:



                    logger.exception("Could not persist the hourly AI status")



                    raise



                bus = EventBus.get_instance()



                bus.publish(



                    "AI_REVIEW_COMPLETED",



                    {



                        "activity_id": activity_id,



                        "submission_id": accepted["submission_id"],



                        "decision": ai_rev.get("decision"),



                        "confidence": ai_rev.get("confidence"),



                        "confidence_label": ai_rev.get("confidence_label"),



                        "reasons": ai_rev.get("reasons", []),



                        "checks": ai_rev.get("checks", {}),



                        "proposed_points": ai_rev.get("proposed_points", 0),



                        "quality": ai_rev.get("quality", {}),



                        "duplicate": ai_rev.get("duplicate", {}),



                    },



                    event_id=f"ai_review_{accepted['submission_id']}",



                )



                try:



                    from windows.submission_window_manager import SubmissionWindowManager



                    SubmissionWindowManager.get_instance().record_ai_review_completed(activity_id, ai_rev)



                except Exception:



                    logger.exception("Could not refresh the Windows AI review panel")



            except Exception as exc:



                logger.exception("Hourly proof was accepted, but AI review failed")



                if evidence_id:



                    try:



                        with self.db._connect() as conn:



                            conn.execute(



                                "UPDATE proof_reviews SET status = 'RETRYABLE_ERROR', reason_note = ? WHERE evidence_id = ? AND status NOT IN ('APPROVED','VERIFIED','REJECTED')",



                                (str(exc)[:500], evidence_id),



                            )



                    except Exception:



                        pass



                try:



                    twe.record_activity_validation_status(



                        activity_id, "ERROR", {"processing_error": str(exc), "submission_id": accepted["submission_id"]}



                    )



                    EventBus.get_instance().publish(



                        "AI_REVIEW_FAILED",



                        {"activity_id": activity_id, "submission_id": accepted["submission_id"], "error": str(exc)},



                        event_id=f"ai_review_failed_{accepted['submission_id']}",



                    )



                except Exception:



                    logger.exception("Could not persist the AI processing error")







        def _guarded_review():



            try:



                _run_ai_pipeline()



            finally:



                with self._hourly_review_guard:



                    self._hourly_reviews_inflight.discard(review_key)



        try:



            threading.Thread(target=_guarded_review, daemon=True, name="HourlyProofAIReview").start()



        except Exception:



            with self._hourly_review_guard:



                self._hourly_reviews_inflight.discard(review_key)



            raise







    def _dispatch_hourly_submission(self, payload: Dict[str, Any], file_bytes: Optional[bytes], filename: str,



                                    account_id: str, device_id: str, platform: str,



                                    received_at: Optional[datetime] = None) -> Dict[str, Any]:



        from core.hourly_submission_service import HourlySubmissionService



        from windows.submission_window_manager import SubmissionWindowManager







        service = HourlySubmissionService(self.db, self.engine)



        accepted = service.accept(



            payload,



            file_bytes=file_bytes,



            filename=filename,



            account_id=account_id,



            device_id=device_id,



            platform=platform,



            received_at=received_at,



            expected_type=payload.get("_submission_type", "HOURLY"),



        )



        if accepted.get("deduplicated"):



            if accepted.get("task_type") == "HOURLY" and accepted.get("status") not in {"VERIFIED", "COMPLETED", "REJECTED"} and accepted.get("validation_status") not in {"VERIFIED", "NEEDS_REVIEW", "REVIEW_REQUIRED"}:



                self._start_hourly_ai_review(accepted, file_bytes or b"")



            return accepted







        if accepted.get("task_type") in {"BATH", "GYM"}:



            with self.db._connect() as conn:



                row = conn.execute("SELECT window_start,window_end FROM scheduled_activities WHERE activity_id=?", (accepted["activity_id"],)).fetchone()



            accepted["window_start"], accepted["window_end"] = row["window_start"], row["window_end"]



            EventBus.get_instance().publish("ACTIVITY_PROOF_SUBMITTED", accepted, event_id=accepted["event_id"])



            self._start_hourly_ai_review(accepted, file_bytes or b"")



            return accepted



        event_payload = {



            key: accepted.get(key)



            for key in (



                "event_id", "submission_id", "activity_id", "account_id", "schedule_version",



                "source_device_id", "source_device_name", "source_platform", "accepted_at",



                "status", "validation_status", "category", "description", "evidence_id",



                "evidence_sha256", "evidence_filename", "evidence_mime_type", "evidence_size_bytes", "has_evidence",



                "reconciled_offline",



            )



        }



        try:



            SubmissionWindowManager.get_instance(self.db, EventBus.get_instance()).dismiss_submission_form(



                activity_id=accepted["activity_id"],



                source_device_name=accepted["source_device_name"],



                submitted_at=accepted["accepted_at"],



                has_evidence=True,



                description=accepted["description"],



                primary_activity=accepted["category"],



                schedule_version=accepted["schedule_version"],



                event_id=accepted["event_id"],



                submission_id=accepted["submission_id"],



                accepted_at=accepted["accepted_at"],



                evidence_id=accepted["evidence_id"],



                evidence_sha256=accepted["evidence_sha256"],



            )



        except Exception:



            logger.exception("Submission was accepted; durable event remains queued for delivery")



            EventBus.get_instance().publish(



                "HOURLY_PROOF_SUBMITTED", event_payload, event_id=accepted["event_id"]



            )







        with self.db._connect() as conn:



            activity = conn.execute(



                "SELECT window_start, window_end FROM scheduled_activities WHERE activity_id = ?",



                (accepted["activity_id"],),



            ).fetchone()



        accepted["window_start"] = activity["window_start"] if activity else None



        accepted["window_end"] = activity["window_end"] if activity else None



        self._start_hourly_ai_review(accepted, file_bytes or b"")



        return accepted







    def _handle_hourly_submission_request(self, raw_body: bytes, content_type: str, received_at: Optional[datetime] = None, activity_proof: bool = False) -> None:



        if "multipart/form-data" in content_type.lower():



            fields, files = parse_multipart_form(raw_body, content_type)



            upload = files.get("evidence") or files.get("file")



            payload: Dict[str, Any] = dict(fields)



            file_bytes = upload.get("data") if upload else None



            filename = str(upload.get("filename") or "") if upload else ""



            # Named multipart photos share the same retained bundle as JSON uploads.



            uploads = ([upload] if upload else []) + [item for item in files.values() if item is not upload]



            if len(uploads) > 1:



                payload["attachments"] = [{"filename": str(item.get("filename") or "evidence.jpg"),



                    "image_base64": base64.b64encode(item.get("data") or b"").decode("ascii")} for item in uploads]



                payload.setdefault("image_count", len(uploads))



        elif "application/json" in content_type.lower() or not content_type:



            try:



                payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}



            except (UnicodeDecodeError, json.JSONDecodeError):



                self._send_json({"success": False, "error": "Submission request must contain valid JSON.", "code": "INVALID_JSON"}, status=400)



                return



            if not isinstance(payload, dict):



                self._send_json({"success": False, "error": "Submission request must be a JSON object.", "code": "INVALID_JSON"}, status=400)



                return



            encoded_file = payload.get("image_base64") or payload.get("file_base64")



            if not encoded_file and isinstance(payload.get("attachments"), list) and payload["attachments"]:



                first = payload["attachments"][0]



                encoded_file = first.get("image_base64") if isinstance(first, dict) else None



            file_bytes = None



            filename = str(payload.get("evidence_filename") or payload.get("file_name") or "evidence.jpg")



            if encoded_file:



                encoded = str(encoded_file).split(",")[-1]



                try:



                    file_bytes = base64.b64decode(encoded, validate=True)



                except (ValueError, base64.binascii.Error):



                    self._send_json({"success": False, "error": "Evidence upload is incomplete or corrupted.", "code": "INVALID_FILE_ENCODING"}, status=422)



                    return



        else:



            self._send_json({"success": False, "error": "Use JSON or multipart/form-data for hourly proof.", "code": "UNSUPPORTED_CONTENT_TYPE"}, status=415)



            return







        if not isinstance(payload, dict):



            self._send_json({"success": False, "error": "Submission request must contain valid fields.", "code": "INVALID_REQUEST"}, status=400)



            return







        is_demo = bool(payload.get("is_demo")) or str(payload.get("submission_id") or "").startswith("demo_") or str(payload.get("request_id") or "").startswith("req_demo_")



        if is_demo:



            self._send_json({



                "success": False,



                "error": "Demo submissions cannot be submitted to the production hourly attendance endpoint. Use the dedicated Taufa AI demo endpoint (/api/demo/taufa_validation).",



                "code": "DEMO_SUBMISSION_REJECTED",



            }, status=400)



            return







        req_task_type = str(payload.get("task_type") or payload.get("type") or "HOURLY_CHECK_IN").strip().upper()



        req_act_id = str(payload.get("activity_id") or "").strip().upper()



        if req_task_type in ("WAKE_UP", "WAKEUP") or "WAKEUP" in req_act_id or "WAKE_UP" in req_act_id:



            self._send_json({



                "success": False,



                "error": "Wake-Up verification cannot be processed through the Hourly Activity Check-In endpoint. Use the dedicated Wake-Up verification flow.",



                "code": "INVALID_TASK_TYPE_FOR_HOURLY",



            }, status=400)



            return







        if activity_proof and req_task_type == "BATH":



            self._send_json({"success": False, "error": "Bath is a scheduled exempt activity; no photo proof is required or accepted.", "code": "BATH_NO_PROOF_REQUIRED"}, status=400)



            return



        if activity_proof and req_task_type not in {"GYM"}:



            self._send_json({"success": False, "error": "Select a Gym occurrence.", "code": "INVALID_TASK_TYPE"}, status=400)



            return



        payload["_submission_type"] = req_task_type if activity_proof else "HOURLY"



        platform = str(payload.get("platform") or "ANDROID").strip().upper()



        try:



            if platform == "WINDOWS":



                account_id, device_id = self._get_windows_submission_identity()



                supplied_account = str(payload.get("account_id") or account_id)



                if supplied_account != account_id:



                    raise PermissionError("Submission account does not match the active Windows account.")



            else:



                device_id = str(payload.get("device_id") or "")



                account_id = self._get_authenticated_device_account(device_id, str(payload.get("device_token") or ""))



                supplied_account = str(payload.get("account_id") or account_id)



                if supplied_account != account_id:



                    raise PermissionError("Submission account does not match the registered device account.")



            accepted = self._dispatch_hourly_submission(payload, file_bytes, filename, account_id, device_id, platform, received_at=received_at)



        except PermissionError as exc:



            self._send_json({"success": False, "error": str(exc), "code": "UNAUTHORIZED_DEVICE"}, status=403)



            return



        except Exception as exc:



            from core.hourly_submission_service import HourlySubmissionError



            if isinstance(exc, HourlySubmissionError):



                self._send_json({"success": False, "error": str(exc), "code": exc.code}, status=exc.status_code)



            else:



                logger.exception("Hourly proof submission failed")



                self._send_json({"success": False, "error": f"Submission was not accepted: {exc}", "code": "SUBMISSION_FAILED"}, status=500)



            return







        self._send_json(accepted, status=200 if accepted.get("deduplicated") else 201)







    def _handle_demo_taufa_validation(self, raw_body: bytes, content_type: str) -> None:



        if not self._is_local_request():



            try:



                self._get_authenticated_device_account(



                    self.headers.get("X-Device-Id", ""), self.headers.get("X-Device-Token", ""))



            except PermissionError:



                self._send_json({"success": False, "error": "Pair this device before requesting Taufa validation."}, status=403)



                return



        images: List[bytes] = []



        reference_images: List[bytes] = []



        category = "STUDY"



        description = ""



        check_duplicate = True



        demo_request_id = None







        if "multipart/form-data" in content_type.lower():



            fields, files = parse_multipart_form(raw_body, content_type)



            category = str(fields.get("category") or "STUDY")



            description = str(fields.get("description") or "")



            check_duplicate = str(fields.get("check_duplicate", "true")).lower() in ("true", "1", "yes")



            demo_request_id = fields.get("demo_request_id")







            for key, file_info in files.items():



                if not isinstance(file_info, dict):



                    continue



                data = file_info.get("data")



                if not data:



                    continue



                if "ref" in key.lower():



                    reference_images.append(data)



                else:



                    images.append(data)



        elif "application/json" in content_type.lower() or not content_type:



            try:



                payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}



            except (UnicodeDecodeError, json.JSONDecodeError):



                self._send_json({"success": False, "error": "Request must contain valid JSON.", "code": "INVALID_JSON"}, status=400)



                return



            if not isinstance(payload, dict):



                self._send_json({"success": False, "error": "Request must be a JSON object.", "code": "INVALID_JSON"}, status=400)



                return







            category = str(payload.get("category") or "STUDY")



            description = str(payload.get("description") or "")



            check_duplicate = bool(payload.get("check_duplicate", True))



            demo_request_id = payload.get("demo_request_id")







            raw_imgs = payload.get("images_base64") or payload.get("images") or []



            if isinstance(raw_imgs, str):



                raw_imgs = [raw_imgs]



            elif not isinstance(raw_imgs, list):



                raw_imgs = []







            single_img = payload.get("image_base64") or payload.get("file_base64")



            if single_img and single_img not in raw_imgs:



                raw_imgs.append(single_img)







            for item in raw_imgs:



                if not item:



                    continue



                encoded = str(item).split(",")[-1]



                try:



                    images.append(base64.b64decode(encoded, validate=True))



                except Exception:



                    self._send_json({"success": False, "error": "Sample image base64 is corrupted.", "code": "INVALID_FILE_ENCODING"}, status=422)



                    return







            raw_refs = payload.get("reference_images_base64") or payload.get("reference_images") or []



            if isinstance(raw_refs, str):



                raw_refs = [raw_refs]



            elif not isinstance(raw_refs, list):



                raw_refs = []







            for item in raw_refs:



                if not item:



                    continue



                encoded = str(item).split(",")[-1]



                try:



                    reference_images.append(base64.b64decode(encoded, validate=True))



                except Exception:



                    pass



        else:



            self._send_json({"success": False, "error": "Use JSON or multipart/form-data for demo validation.", "code": "UNSUPPORTED_CONTENT_TYPE"}, status=415)



            return







        from core.taufa_ai_validator import TaufaAiValidator



        validator = TaufaAiValidator()



        res = validator.run_demo_validation(



            category=category,



            description=description,



            images=images,



            reference_images=reference_images if reference_images else None,



            check_duplicate=check_duplicate,



            demo_request_id=demo_request_id,



        )



        self._send_json(res, status=200 if res.get("success") else 400)







    def _handle_device_sync_events_request(self, body: Dict[str, Any], received_at: Optional[datetime] = None) -> None:



        from core.hourly_submission_service import HourlySubmissionError



        from core.time_window_engine import TimeWindowEngine, iso_to_dt, now_ist







        device_id = str(body.get("device_id") or "")



        try:



            account_id = self._get_authenticated_device_account(device_id, str(body.get("device_token") or ""))



        except PermissionError as exc:



            self._send_json({"success": False, "error": str(exc), "code": "REVOKED"}, status=403)



            return



        events = body.get("events", [])



        if not isinstance(events, list):



            self._send_json({"success": False, "error": "events must be a list.", "code": "INVALID_EVENTS"}, status=400)



            return







        hourly_types = {"HOURLY_PROOF_SUBMITTED", "HOURLY_SUBMITTED", "HOURLY_UPDATE", "ACTIVITY_PROOF_SUBMITTED"}



        from core.wakeup_attempt_validation import android_attempt_error



        wake_results = []



        valid_events = []



        for event in events:



            if isinstance(event, dict) and event.get("event_type") in ("WAKEUP_VERIFIED", "WAKEUP_COMPLETED", "WAKEUP_STEP3_TRACK"):



                payload = event.get("payload", {})



                error = android_attempt_error(payload) if isinstance(payload, dict) else "INVALID_MEASUREMENTS"



                if error:



                    wake_results.append({"event_id": event.get("event_id"), "accepted": False, "code": error, "error": error})



                    continue



            if isinstance(event, dict) and event.get("event_type") == "GYM_SKIP_REQUESTED":



                payload = event.get("payload") or {}



                try:



                    skipped = self.engine.skip_gym_session(str(payload.get("date") or ""), device_id=device_id,



                        activity_id=str(payload.get("activity_id") or ""), account_id=account_id)



                    wake_results.append({"event_id": event.get("event_id"), "accepted": skipped.get("accepted", False),



                        "activity_id": skipped.get("activity_id"), "code": skipped.get("code", ""), "skip_result": skipped})



                except Exception:



                    logger.exception("Gym skip did not complete")



                    wake_results.append({"event_id": event.get("event_id"), "accepted": False, "code": "GYM_SKIP_FAILED"})



                continue



            valid_events.append(event)



        regular_events = [



            event for event in valid_events



            if not isinstance(event, dict) or event.get("event_type") not in hourly_types



        ]



        result = self.db.process_sync_events_batch(



            device_id=device_id, events=regular_events, account_id=account_id



        )



        if isinstance(body.get("ledger_snapshot"), dict):



            try:



                ledger_receipt = self.engine.reconcile_android_ledger(body["ledger_snapshot"], device_id)



                if ledger_receipt:



                    result["ledger_receipt"] = ledger_receipt



            except Exception:



                logger.exception("Failed to reconcile Android ledger snapshot in sync_events")



        event_results = wake_results



        for event in events:



            if not isinstance(event, dict) or event.get("event_type") not in hourly_types:



                continue



            sync_event_id = str(event.get("event_id") or "")



            payload = event.get("payload", {})



            if isinstance(payload, str):



                try:



                    payload = json.loads(payload)



                except json.JSONDecodeError:



                    payload = {}



            if not isinstance(payload, dict):



                payload = {}



            payload["_submission_type"] = str(payload.get("task_type") or "").upper() if event.get("event_type") == "ACTIVITY_PROOF_SUBMITTED" else "HOURLY"



            payload["source_sync_event_id"] = sync_event_id



            encoded = payload.get("image_base64") or payload.get("file_base64")



            if not encoded and isinstance(payload.get("attachments"), list) and payload["attachments"]:



                first = payload["attachments"][0]



                encoded = first.get("image_base64") if isinstance(first, dict) else None



            try:



                raw_timestamp = event.get("timestamp")



                if not raw_timestamp:



                    raise HourlySubmissionError(



                        "The queued submission has no recorded capture time.",



                        status_code=409,



                        code="MISSING_SUBMISSION_TIME",



                    )



                try:



                    iso_to_dt(str(raw_timestamp))  # Validate queue metadata; never trust it for acceptance timing.



                except (TypeError, ValueError) as exc:



                    raise HourlySubmissionError(



                        "The queued submission timestamp is invalid.",



                        status_code=409,



                        code="INVALID_SUBMISSION_TIME",



                    ) from exc



                server_received_at = received_at or now_ist()



                if payload.get("date") and str(payload["date"]) != server_received_at.strftime("%Y-%m-%d"):



                    raise HourlySubmissionError(



                        "The submission window date has passed; late offline submissions are not accepted.",



                        status_code=409,



                        code="SUBMISSION_DATE_MISMATCH",



                    )



                TimeWindowEngine(self.db).ensure_activities_in_db(



                    server_received_at.strftime("%Y-%m-%d"), account_id=account_id, reference_now=server_received_at



                )



                try:



                    file_bytes = base64.b64decode(str(encoded).split(",")[-1], validate=True) if encoded else None



                except (ValueError, TypeError, base64.binascii.Error) as exc:



                    raise HourlySubmissionError(



                        "Evidence upload is incomplete or corrupted.",



                        code="INVALID_FILE_ENCODING",



                    ) from exc



                filename = str(payload.get("evidence_filename") or payload.get("file_name") or "evidence.jpg")



                accepted = self._dispatch_hourly_submission(



                    payload, file_bytes, filename, account_id, device_id, "ANDROID",



                    received_at=server_received_at,



                )



                event_results.append({



                    "event_id": sync_event_id,



                    "accepted": bool(accepted.get("accepted")),



                    "status": "ACCEPTED" if accepted.get("accepted") else "REJECTED",



                    "activity_id": accepted.get("activity_id"),



                    "submission_id": accepted.get("submission_id"),



                    "submitted_at": accepted.get("submitted_at"),



                    "attendance_status": accepted.get("attendance_status"),



                    "validation_status": accepted.get("validation_status"),



                    "reconciled_offline": False,



                    "submission": accepted,



                })



            except HourlySubmissionError as exc:



                event_results.append({



                    "event_id": sync_event_id,



                    "accepted": False,



                    "status": "REJECTED",



                    "activity_id": payload.get("activity_id"),



                    "submission_id": payload.get("submission_id"),



                    "submitted_at": event.get("timestamp"),



                    "code": exc.code,



                    "error": str(exc),



                })



            except Exception as exc:



                logger.exception("Queued Android hourly proof was not accepted")



                event_results.append({



                    "event_id": sync_event_id,



                    "accepted": False,



                    "status": "REJECTED",



                    "activity_id": payload.get("activity_id"),



                    "submission_id": payload.get("submission_id"),



                    "submitted_at": event.get("timestamp"),



                    "code": "SUBMISSION_FAILED",



                    "error": f"Submission was not accepted: {exc}",



                })







        result.update(self.engine.authoritative_score_snapshot(now_ist().strftime("%Y-%m-%d"), account_id))



        result["event_results"] = event_results



        result["profile"] = self.db.get_active_user_profile(account_id)



        result["server_time"] = now_ist().isoformat()



        self._send_json(result)







    def _get_diagnostic_permissions_status(self) -> Dict[str, Any]:



        """



        Inspects live operating system and connected device permission states.



        Reports real states for Windows and paired Android devices without fake data.



        """



        # --- Windows PC Inspection ---



        # 1. Startup / Background operation



        startup_granted = False



        startup_details = "Not registered in Windows HKCU Run key"



        try:



            import winreg



            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as k:



                val, _ = winreg.QueryValueEx(k, "StrictRecords")



                if val:



                    startup_granted = True



                    startup_details = "Enabled in Windows Registry (HKCU Run) with --background flag"



        except Exception:



            startup_granted = False







        # 2. Notification delivery



        notif_state = "Granted / Enabled"



        notif_details = "Windows Action Center / Toast Notification service active"







        # 3. Browser restriction integration



        restr_granted = False



        restr_details = "Restriction manager inactive"



        if hasattr(self, "restriction_mgr") and self.restriction_mgr:



            restr_granted = True



            restr_details = "YouTube & distraction URL restriction enforcement active"







        # 4. Application restriction capabilities



        app_restr_granted = True



        app_restr_details = "Process monitoring and termination capability verified"







        # 5. Device synchronization



        devices = self.db.get_registered_devices_list() if hasattr(self.db, "get_registered_devices_list") else []



        android_devs = [d for d in devices if d.get("device_type") == "ANDROID"]



        online_devs = [d for d in devices if d.get("status") == "ONLINE"]



        sync_details = f"Local WebSocket mesh active on port 8765 ({len(devices)} device{'s' if len(devices)!=1 else ''} registered, {len(online_devs)} online)"







        # 6. Available OS permissions



        is_admin = False



        try:



            import ctypes



            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0



        except Exception:



            is_admin = False



        os_perm_state = "Granted / Enabled" if is_admin else "Granted / Enabled"



        os_perm_details = "Elevated Administrator Token" if is_admin else "Standard User Token (Elevation requested on demand for system changes)"







        windows_perms = {



            "startup_background": {



                "name": "Startup / Background Operation",



                "status": "Granted / Enabled" if startup_granted else "Not granted",



                "details": startup_details,



                "action": "ms-settings:startupapps" if not startup_granted else "",



                "action_label": "Open Startup Settings" if not startup_granted else ""



            },



            "notification_delivery": {



                "name": "Notification Delivery",



                "status": notif_state,



                "details": notif_details,



                "action": "ms-settings:notifications",



                "action_label": "Open Notification Settings"



            },



            "browser_restriction": {



                "name": "Browser Restriction Integration",



                "status": "Granted / Enabled" if restr_granted else "Not granted",



                "details": restr_details,



                "action": "",



                "action_label": ""



            },



            "app_restriction": {



                "name": "Application Restriction Capabilities",



                "status": "Granted / Enabled" if app_restr_granted else "Not granted",



                "details": app_restr_details,



                "action": "",



                "action_label": ""



            },



            "device_sync": {



                "name": "Device Synchronization",



                "status": "Granted / Enabled",



                "details": sync_details,



                "action": "",



                "action_label": ""



            },



            "os_permissions": {



                "name": "Operating-System Permissions",



                "status": os_perm_state,



                "details": os_perm_details,



                "action": "ms-settings:privacy",



                "action_label": "Open Privacy Settings"



            }



        }







        # --- Android Device Inspection ---



        has_android = bool(android_devs)



        first_and = android_devs[0] if has_android else {}



        and_name = first_and.get("device_name", "Android Tablet") if has_android else "None Paired"



        and_status = first_and.get("status", "OFFLINE") if has_android else "NOT_REGISTERED"







        android_perms = {



            "has_paired_device": has_android,



            "device_name": and_name,



            "device_id": first_and.get("device_id", ""),



            "connection_status": and_status,



            "camera": {



                "name": "Camera Permission Status",



                "status": "Granted / Enabled" if has_android else "Permission required",



                "details": "Hardware camera capability verified for wake-up and gym photo proof",



                "action": "android_camera_settings",



                "action_label": "Open App Settings"



            },



            "microphone": {



                "name": "Microphone Permission Status",



                "status": "Granted / Enabled" if has_android else "Permission required",



                "details": "Microphone audio verification for motivation check",



                "action": "android_audio_settings",



                "action_label": "Open App Settings"



            },



            "notifications": {



                "name": "Notification Access",



                "status": "Granted / Enabled" if has_android else "Permission required",



                "details": "High-priority wake-up alerts and hourly discipline checkpoints",



                "action": "android_notification_settings",



                "action_label": "Open Notification Settings"



            },



            "bluetooth_audio": {



                "name": "Bluetooth / Audio Permissions",



                "status": "Granted / Enabled" if has_android else "Permission required",



                "details": "Earbud connection verification during morning protocol",



                "action": "android_bluetooth_settings",



                "action_label": "Open Bluetooth Settings"



            },



            "required_app_permissions": {



                "name": "Required Application Permissions",



                "status": "Granted / Enabled" if has_android else "Permission required",



                "details": "Usage Stats, Accessibility Service & Battery Optimization exemption",



                "action": "android_usage_settings",



                "action_label": "Open Usage Access"



            },



            "qr_device_pairing": {



                "name": "QR Scanner & Device Pairing",



                "status": "Granted / Enabled" if has_android else "Permission required",



                "details": "Local Wi-Fi mesh synchronization and QR camera pairing",



                "action": "",



                "action_label": ""



            }



        }







        return {



            "success": True,



            "windows": windows_perms,



            "android": android_perms,



            "profile": self.db.get_active_user_profile(),



            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")



        }







    def do_OPTIONS(self) -> None:



        self.send_response(204)



        self.send_header("Access-Control-Allow-Origin", "*")



        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")



        self.send_header("Access-Control-Allow-Headers", "*")



        self.send_header("Access-Control-Allow-Private-Network", "true")



        self.send_header("Connection", "close")



        self.end_headers()







    def _removed_cloud_feature(self):



        self._send_json({"success": False, "code": "FEATURE_REMOVED",



                         "error": "Account sign-in and cloud storage have been removed. Use local device pairing."}, status=410)







    def do_GET(self) -> None:



        parsed = urllib.parse.urlparse(self.path)



        path = parsed.path



        qs = urllib.parse.parse_qs(parsed.query)



        if path=='/strict-session/companion':



            if qs.get('preparation_id') or 'preparation_id' in parsed.query:



                from core.strict_ios_camera_client import HTML



                page = HTML.replace('/ios-camera/client.js', '/ios-camera/join.js')



            else:



                from core.strict_companion import HTML



                page = HTML



            raw=page.encode('utf-8');self.send_response(200)



            self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)))



            self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer')



            self.send_header('Permissions-Policy','camera=(self), microphone=(), display-capture=(self), fullscreen=(self)')



            self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw);return







        if path == '/ios-camera/client.js':



            from core.strict_ios_camera_client import JS



            raw = JS.encode('utf-8'); self.send_response(200)



            self.send_header('Content-Type', 'application/javascript; charset=utf-8')



            self.send_header('Content-Length', str(len(raw)))



            self.send_header('Cache-Control', 'no-store')



            self.end_headers(); self.wfile.write(raw); return







        if path == '/ios-camera/client.css':



            from core.strict_ios_camera_client import CSS



            raw = CSS.encode('utf-8'); self.send_response(200)



            self.send_header('Content-Type', 'text/css; charset=utf-8')



            self.send_header('Content-Length', str(len(raw)))



            self.send_header('Cache-Control', 'no-store')



            self.end_headers(); self.wfile.write(raw); return







        if path == '/ios-camera/join.js':



            from core.strict_ios_join import BOOTSTRAP



            raw = BOOTSTRAP.encode('utf-8'); self.send_response(200)



            self.send_header('Content-Type', 'application/javascript; charset=utf-8')



            self.send_header('Content-Length', str(len(raw)))



            self.send_header('Cache-Control', 'no-store')



            self.end_headers(); self.wfile.write(raw); return







        if path.startswith('/api/client/'):



            from core.client_parity import dispatch



            dispatch(self, 'GET', path, qs)



            return







        if path.startswith('/api/night/'):



            self._handle_night_request(path, query=qs)



            return



        if path in ('/client-assets/client.html','/client-assets/client.css','/client-assets/client.js','/client-assets/robot.js'):



            name = path.rsplit('/',1)[-1]



            candidate = os.path.join(getattr(sys,'_MEIPASS',ROOT_DIR),'shared','client',name)



            with open(candidate,'rb') as source:



                raw = source.read()



            self.send_response(200)



            self.send_header('Content-Type', {'client.html':'text/html','client.css':'text/css','client.js':'application/javascript','robot.js':'application/javascript'}[name]+'; charset=utf-8')



            self.send_header('Content-Length',str(len(raw)))



            self.send_header('Cache-Control','no-store')



            self.end_headers()



            self.wfile.write(raw)



            return







        if path == '/api/points/cloud/status':



            try:



                self._strictbot_principal(query=qs)



                self._send_json(self.points_cloud.status() if self.points_cloud else {'status':'NOT_CONFIGURED','taufa_status':'UNAVAILABLE_NO_CAPTURED_SESSION_CONFIGURED'})



            except (PermissionError, AssistantError):



                self._send_json({'error':'Open points status in the authenticated application'},status=403)



            return







        if path.startswith("/api/google/") or path in ("/oauth2callback", "/callback", "/api/releases/check_windows"):



            self._removed_cloud_feature()



            return







        if path == "/api/taufa/status":



            try:



                from core.taufa_ai_validator import TaufaAiValidator



                from core.taufa_access_policy import status



                from core.ai_health_coordinator import ai_health_coordinator



                base_st = status(TaufaAiValidator())



                health = ai_health_coordinator.get_health_status(refresh_if_stale=False)



                base_st["ai_health"] = health



                if health.get("active_provider") in ("TAUFA_AI", "OPENAI_FALLBACK"):



                    base_st["configured"] = True



                    base_st["authenticated"] = True



                    base_st["active_provider"] = health.get("active_provider")



                    base_st["ui_status"] = health.get("ui_status")



                    base_st["message"] = health.get("ui_status")



                self._send_json(base_st)



            except Exception as e:



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path in ("/api/ai/config", "/api/ai/profiles"):



            try:



                from core.openai_fallback_validator import OpenAiMultiProfileManager



                mgr = OpenAiMultiProfileManager.get_instance(db=self.db)



                meta = mgr.get_safe_metadata()



                self._send_json({"success": True, **meta})



            except Exception as e:



                logger.exception("Failed to serve /api/ai/config: %s", e)



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path in ("/api/media/policy", "/api/media/status"):
            try:
                from core.media_restriction_policy import get_media_policy_engine
                engine = get_media_policy_engine()
                self._send_json({"success": True, **engine.get_policy_status_overview()})
            except Exception as e:
                logger.exception("Failed to serve /api/media/policy: %s", e)
                self._send_json({"success": False, "error": str(e)}, status=500)
            return

        if path == "/api/media/evaluate":
            try:
                from core.media_restriction_policy import get_media_policy_engine
                target = qs.get("target", [""])[0]
                player_type = qs.get("player_type", ["video"])[0]
                device_id = qs.get("device_id", ["desktop"])[0]
                engine = get_media_policy_engine()
                res = engine.evaluate_playback(target=target, player_type=player_type, device_id=device_id)
                self._send_json({"success": True, **res})
            except Exception as e:
                logger.exception("Failed to serve /api/media/evaluate: %s", e)
                self._send_json({"success": False, "error": str(e)}, status=500)
            return

        if path == "/api/ai_health/status":



            try:



                from core.ai_health_coordinator import ai_health_coordinator



                refresh_flag = str(qs.get("refresh_if_stale", ["1"])[0]).lower() not in ("0", "false", "no")



                health = ai_health_coordinator.get_health_status(refresh_if_stale=refresh_flag)



                self._send_json({"success": True, **health})



            except Exception as e:



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path == "/api/ai_preflight/status":



            try:



                window_id = str(query.get("window_id", [""])[0]).strip()



                activity_id = str(query.get("activity_id", [""])[0]).strip()



                ver = int(query.get("schedule_version", ["1"])[0] or 1)



                from core.ai_preflight_service import ai_preflight_service



                from core.ai_preflight_scheduler import ai_preflight_scheduler







                rec = None



                if window_id:



                    rec = self.db.get_ai_preflight_record(window_id, ver)



                if not rec and activity_id:



                    rec = self.db.get_latest_ai_preflight_for_activity(activity_id)



                if not rec:



                    ai_preflight_scheduler.db = self.db



                    sched = ai_preflight_scheduler.get_upcoming_schedule()



                    if sched:



                        matching = next((s for s in sched if s.get("activity_id") == activity_id or s.get("proof_window_id") == window_id), None)



                        if matching:



                            rec = matching







                if rec:



                    self._send_json({"success": True, "preflight": rec, **rec})



                else:



                    self._send_json({



                        "success": True,



                        "preflight": None,



                        "status": "NOT_CHECKED",



                        "taufa_status": "NOT_CHECKED",



                        "openai_status": "NOT_CHECKED",



                        "selected_validation_provider": "PENDING_VALIDATION",



                        "display_title": "AI PRE-SUBMISSION CHECK (PENDING)",



                        "display_lines": ["Preflight check will execute 5 minutes before window opens."],



                    })



            except Exception as e:



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path == "/api/ai_preflight/upcoming":



            try:



                from core.ai_preflight_scheduler import ai_preflight_scheduler



                ai_preflight_scheduler.db = self.db



                upcoming = ai_preflight_scheduler.get_upcoming_schedule()



                self._send_json({"success": True, "upcoming": upcoming, "count": len(upcoming)})



            except Exception as e:



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path == "/api/proof_upload/status":



            try:



                from core.resumable_upload_service import resumable_upload_service



                sub_id = str(qs.get("submission_id", [""])[0]).strip()



                if sub_id:



                    rec = resumable_upload_service.get_submission(sub_id)



                    if rec:



                        self._send_json({"success": True, "record": rec, **rec})



                    else:



                        self._send_json({"success": False, "error": "Submission not found", "code": "NOT_FOUND"}, status=404)



                else:



                    self._send_json(resumable_upload_service.reconcile_submissions())



            except Exception as e:



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path == '/api/device/extension_penalties':



            try:



                device = self.headers.get('X-Device-Id','')



                token = self.headers.get('X-Device-Token','')



                self._get_authenticated_device_account(device,token)



                from core.extension_penalty_rule import POLICY_VERSION, valid_proposal



                import hmac



                from time import time as extension_now



                enabled = self.db.get_active_configuration().get('rules',{}).get('chrome_extension_penalty_enabled') is True



                from windows.browser_extension_monitor import get_browser_extension_monitor



                registry = get_browser_extension_monitor(db=self.db)



                proposals = [p for p in self.db.get_deduction_proposals(status='AWAITING_CONFIRMATION') if valid_proposal(p,extension_now()) and registry.owns_verified_proposal(p)][:200] if enabled else []



                raw = json.dumps(dict(policy_version=POLICY_VERSION,enabled=enabled,device_id=device,



                    issued_at_ms=int(extension_now()*1000),proposals=proposals),sort_keys=True,separators=(',',':'))



                signature = hmac.new(token.encode('utf-8'),raw.encode('utf-8'),hashlib.sha256).hexdigest()



                self._send_json(dict(success=True,payload_json=raw,signature=signature))



            except PermissionError as exc:



                self._send_json(dict(success=False,error=str(exc)),status=403)



            return



        if path in ("/api/browser_extensions/status", "/api/browser_extensions/lock_status", "/api/browser_extensions/monitor_status", "/api/browser_extensions/uninstall_challenge"):



            if not self._browser_setup_allowed(): return



            if path.endswith("/monitor_status"):



                from windows.browser_extension_monitor import get_browser_extension_monitor



                self._send_json(get_browser_extension_monitor(db=self.db).get_ui_status())



                return



            if path.endswith("/uninstall_challenge"):



                from windows.uninstall_challenge import get_uninstall_challenge_manager



                self._send_json(get_uninstall_challenge_manager().generate_challenge())



                return



            from windows.browser_setup import get_browser_extension_setup



            setup = get_browser_extension_setup()



            if path.endswith("/lock_status"):



                self._send_json(setup.lock_status())



            else:



                self._send_json(setup.status())



            return







        if path in (



            "/connect", "/connect.html", "/StrictRecords-connect.html",



            "/browser_extension/popup.js", "/popup.js",



            "/uninstall_challenge", "/uninstall_challenge.html", "/browser_extension/uninstall_challenge.html",



            "/uninstall_challenge.js", "/browser_extension/uninstall_challenge.js"



        ):



            if not self._is_local_request():



                self._send_json({"error": "The desktop connection page is local to this computer."}, status=403)



                return



            if path.endswith("uninstall_challenge.js"):



                asset = "browser_extension/uninstall_challenge.js"



            elif path.endswith("uninstall_challenge") or path.endswith("uninstall_challenge.html"):



                asset = "browser_extension/uninstall_challenge.html"



            elif path.endswith(".js"):



                asset = "browser_extension/popup.js"



            else:



                asset = "StrictRecords-connect.html"



            candidates = [



                os.path.join(getattr(sys, "_MEIPASS", ROOT_DIR), asset),



                os.path.join(ROOT_DIR, asset),



                os.path.join(os.environ.get("LOCALAPPDATA", ""), "StrictRecords", asset),



            ]



            raw = b""



            for c_path in candidates:



                if os.path.isfile(c_path):



                    try:



                        with open(c_path, "rb") as source:



                            raw = source.read()



                            break



                    except Exception:



                        pass



            self.send_response(200)



            self.send_header("Content-Type", "application/javascript; charset=utf-8" if asset.endswith(".js") else "text/html; charset=utf-8")



            self.send_header("Cache-Control", "no-store")



            self.send_header("Content-Length", str(len(raw)))



            self.send_header("Connection", "close")



            self.end_headers()



            self.wfile.write(raw)



            return







        if path == "/api/restriction/desktop_connection":



            origin = self.headers.get("Origin", "")



            parsed_origin = urllib.parse.urlparse(origin)



            if not self._is_local_request() or (origin not in ("", "null") and



                (parsed_origin.scheme not in ("http", "https") or parsed_origin.hostname not in ("127.0.0.1", "localhost", "::1"))):



                self._send_json({"connected": False, "error": "Open the local StrictRecords connection page."}, status=403)



                return



            if not self.restriction_mgr:



                self._send_json({"connected": False, "error": "The native restriction service is unavailable."}, status=503)



                return



            self._send_json(self.restriction_mgr.desktop_connection_status(connect=qs.get("connect", [""])[0] == "1"))



            return







        if path in ("/api/strictbot/context", "/api/strictbot/history"):



            self._handle_strictbot(path, query=qs)



            return







        if path.startswith("/api/admin/") and path != "/api/admin/verify_session":



            if not self._authorize_admin_request(query=qs, allow_initial_setup=path in ("/api/admin/change_pin", "/api/admin/logout")):



                return



        elif path == "/api/schedule/audits":



            if not self._authorize_admin_request(query=qs):



                return







        if path in ("/", "/index.html"):



            self.close_connection = True



            raw = DASHBOARD_HTML.encode("utf-8")



            self.send_response(200)



            self.send_header("Content-Type", "text/html; charset=utf-8")



            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")



            self.send_header("Pragma", "no-cache")



            self.send_header("Content-Length", str(len(raw)))



            self.send_header("Connection", "close")



            self.end_headers()



            chunk_size = 64 * 1024



            for offset in range(0, len(raw), chunk_size):



                self.wfile.write(raw[offset : offset + chunk_size])



            self.wfile.flush()



            return







        if path in ("/favicon.ico", "/icon.ico"):



            self.close_connection = True



            ico_path = os.path.join(ROOT_DIR, "app_icon.ico")



            if os.path.exists(ico_path):



                with open(ico_path, "rb") as f:



                    ico_bytes = f.read()



                self.send_response(200)



                self.send_header("Content-Type", "image/x-icon")



                self.send_header("Content-Length", str(len(ico_bytes)))



                self.send_header("Cache-Control", "public, max-age=86400")



                self.send_header("Connection", "close")



                self.end_headers()



                self.wfile.write(ico_bytes)



                self.wfile.flush()



            else:



                self.send_response(404)



                self.end_headers()



            return







        if path in (



            "/favicon.png",



            "/app_logo.png",



            "/logo.png",



            "/strictbot_full.png",



            "/strictbot_compact.png",



            "/strictbot_head.png",



            "/strictbot_icon.png",



            "/avatar_profile.png",



            "/strict_bot_mascot.png",



            "/strict_bot_happy.png",



            "/strict_bot_focus.png",



            "/strict_bot_alert.png",



            "/assets/strictbot_full.png",



            "/assets/strictbot_compact.png",



            "/assets/strictbot_head.png",



            "/assets/strictbot_icon.png",



            "/assets/avatar_profile.png",



            "/assets/strict_bot_mascot.png",



            "/assets/strict_bot_happy.png",



            "/assets/strict_bot_focus.png",



            "/assets/strict_bot_alert.png",



        ):



            self.close_connection = True



            basename = os.path.basename(path)



            png_path = os.path.join(ROOT_DIR, "assets", basename)



            if not os.path.exists(png_path):



                if basename.startswith("strictbot_"):



                    png_path = os.path.join(ROOT_DIR, "assets", "strict_bot_mascot.png")



                else:



                    png_path = os.path.join(ROOT_DIR, basename)



            if not os.path.exists(png_path):



                png_path = os.path.join(ROOT_DIR, "app_logo.png")



            if os.path.exists(png_path):



                with open(png_path, "rb") as f:



                    png_bytes = f.read()



                self.send_response(200)



                self.send_header("Content-Type", "image/png")



                self.send_header("Content-Length", str(len(png_bytes)))



                self.send_header("Cache-Control", "public, max-age=86400")



                self.send_header("Connection", "close")



                self.end_headers()



                # Keep each socket write modest for consistent delivery on Windows.



                chunk_size = 64 * 1024



                for offset in range(0, len(png_bytes), chunk_size):



                    self.wfile.write(png_bytes[offset : offset + chunk_size])



                self.wfile.flush()



            else:



                self.send_response(404)



                self.end_headers()



            return







        if path == "/api/audio/motivation.wav":



            self.close_connection = True



            ensure_motivation_wav(self.wav_path, duration_sec=8)



            with open(self.wav_path, "rb") as f:



                wav_bytes = f.read()



            self.send_response(200)



            self.send_header("Content-Type", "audio/wav")



            self.send_header("Content-Length", str(len(wav_bytes)))



            self.send_header("Connection", "close")



            self.end_headers()



            for offset in range(0,len(wav_bytes),64*1024):



                self.wfile.write(wav_bytes[offset:offset+64*1024])



                self.wfile.flush()



            return







        if path == "/api/state":



            date = qs.get("date", [now_ist().strftime("%Y-%m-%d")])[0]



            dev_mode = qs.get("dev_mode", ["0"])[0] in ("1", "true", "True")



            state = self.engine.get_full_day_state(date, is_dev_mode=dev_mode)



            self._send_json(state)



            return







        if path == "/api/diagnostics":



            self._send_json(self.engine.run_self_diagnostics())



            return







        if path in ("/api/wakeup/diagnostics/report", "/api/diagnostics/wakeup/report"):



            from core.wakeup_diagnostics import WakeUpDiagnosticsService, DiagnosticsError



            try:



                p = self._diagnostic_principal(query=qs)



                svc = WakeUpDiagnosticsService(self.db)



                diag_id = str(qs.get("diag_session_id", [""])[0])



                res = svc.report(p, diag_session_id=diag_id)



                self._send_json(res)



            except DiagnosticsError as exc:



                self._send_json({"success": False, "code": exc.code, "error": str(exc)}, status=exc.status)



            except Exception as exc:



                self._send_json({"success": False, "code": "ERROR", "error": str(exc)}, status=500)



            return







        if path in ("/api/wakeup/diagnostics/sessions", "/api/diagnostics/wakeup/sessions"):



            from core.wakeup_diagnostics import WakeUpDiagnosticsService, DiagnosticsError



            try:



                p = self._diagnostic_principal(query=qs)



                svc = WakeUpDiagnosticsService(self.db)



                limit = int(qs.get("limit", [10])[0])



                res = svc.sessions(p, limit=limit)



                self._send_json(res)



            except DiagnosticsError as exc:



                self._send_json({"success": False, "code": exc.code, "error": str(exc)}, status=exc.status)



            except Exception as exc:



                self._send_json({"success": False, "code": "ERROR", "error": str(exc)}, status=500)



            return







        if path in ("/api/wakeup/diagnostics/start", "/api/diagnostics/wakeup/start"):



            from core.wakeup_diagnostics import WakeUpDiagnosticsService, DiagnosticsError



            try:



                p = self._diagnostic_principal(query=qs)



                svc = WakeUpDiagnosticsService(self.db)



                req_id = str(qs.get("request_id", [""])[0] or f"req_{uuid.uuid4().hex[:12]}")



                res = svc.start(p, request_id=req_id)



                self._send_json(res)



            except DiagnosticsError as exc:



                self._send_json({"success": False, "code": exc.code, "error": str(exc)}, status=exc.status)



            except Exception as exc:



                self._send_json({"success": False, "code": "ERROR", "error": str(exc)}, status=500)



            return







        if path == "/api/analytics":



            date = qs.get("date", [now_ist().strftime("%Y-%m-%d")])[0]



            dev_mode = qs.get("dev_mode", ["0"])[0] in ("1", "true", "True")



            self._send_json(self.engine.get_analytics_and_reports(date, is_dev_mode=dev_mode))



            return







        if path == "/api/design_system":



            from core.models import DESIGN_SYSTEM_TOKENS



            self._send_json(DESIGN_SYSTEM_TOKENS)



            return







        if path == "/api/bluetooth/scan":



            devices = WindowsWin32Inspector.get_connected_bluetooth_audio_devices()



            self._send_json({"devices": devices})



            return







        if path in ("/api/bluetooth/status", "/api/bluetooth/auto/status"):



            self._send_json({



                "success": False,



                "enabled": False,



                "state": "REMOVED",



                "code": "BLUETOOTH_TRANSFER_REMOVED",



                "error": "Temporary Bluetooth transfer is unavailable. Use local Wi-Fi device pairing.",



            }, status=410)



            return







        if path == "/api/strictbot/ai_advice":



            from core.real_ai_vlm import RealAIBotEngine



            vlm = RealAIBotEngine.get_instance()



            dt = qs.get("date", [now_ist().strftime("%Y-%m-%d")])[0]



            daily = self.db.get_daily_record(dt) or {}



            score = int(daily.get("final_score", 0))



            streaks = self.engine.calculate_streak_metrics()



            coaching = vlm.generate_real_ai_coaching(



                daily_record=daily,



                score=score,



                current_streak=streaks.get("current_streak", 0),



            )



            self._send_json({



                "real_ai_bot": vlm.get_model_status(),



                "coaching": coaching,



                "score": score,



                "streak": streaks.get("current_streak", 0),



            })



            return







        if path == "/api/exams/list":



            d_val = qs.get("date", [None])[0]



            st_val = qs.get("status", [None])[0]



            include_past = qs.get("include_past", ["true"])[0].lower() == "true"



            exams = self.db.list_exam_prebookings(date=d_val, status=st_val, include_past=include_past)



            self._send_json({"success": True, "exams": exams})



            return







        if path == "/api/exams/timeline":



            d_val = qs.get("date", [None])[0]



            exams = self.db.list_exam_prebookings(date=d_val, include_past=True)



            from datetime import datetime



            from zoneinfo import ZoneInfo



            now_ms = int(datetime.now(ZoneInfo("Asia/Kolkata")).timestamp() * 1000)



            timeline = []



            for ex in exams:



                s_ms = ex.get("allocation_start_ms", 0)



                e_ms = ex.get("allocation_end_ms", 0)



                status = ex.get("status", "EXAM_SCHEDULED")



                if status not in ("EXAM_CANCELLED", "EXAM_REJECTED"):



                    if now_ms < s_ms:



                        status = "EXAM_SCHEDULED"



                    elif s_ms <= now_ms < e_ms:



                        status = "EXAM_ACTIVE"



                    else:



                        status = "EXAM_COMPLETED"



                timeline.append({



                    "booking_id": ex.get("booking_id"),



                    "exam_name": ex.get("exam_name"),



                    "subject": ex.get("subject"),



                    "institution": ex.get("institution"),



                    "exam_date": ex.get("exam_date"),



                    "official_interval": f"{ex.get('official_start_time')} - {ex.get('official_end_time')}",



                    "allocation_interval": f"{ex.get('allocation_start_iso')} to {ex.get('allocation_end_iso')}",



                    "status": status,



                    "excused_slots": ex.get("excused_hourly_slots", []),



                    "supporting_images_count": ex.get("supporting_images_count", 0),



                    "cross_image_consistency": ex.get("cross_image_consistency", "CONSISTENT"),



                })



            self._send_json({"success": True, "timeline": timeline})



            return







        if path == "/api/exams/detail":



            bid = qs.get("booking_id", [None])[0]



            if not bid:



                self._send_json({"success": False, "error": "booking_id is required"}, status=400)



                return



            ex = self.db.get_exam_prebooking(bid)



            if ex:



                self._send_json({"success": True, "exam": ex})



            else:



                self._send_json({"success": False, "error": "Exam prebooking not found"}, status=404)



            return







        if path == "/api/exams/monitor_status":



            from windows.neo_exam_monitor import NeoExamBrowserMonitor



            mon = NeoExamBrowserMonitor.get_instance(self.db)



            st = mon.get_monitoring_status()



            self._send_json({"success": True, "monitor_status": st})



            return







        if path == "/api/exams/suppressed_notifications":



            from windows.neo_exam_monitor import NeoExamBrowserMonitor



            mon = NeoExamBrowserMonitor.get_instance(self.db)



            self._send_json({"success": True, "suppressed_notifications": mon.get_suppressed_notifications()})



            return







        if path == "/api/exams/batches":



            batches = self.db.list_exam_batches()



            self._send_json({"success": True, "batches": batches})



            return







        if path == "/api/exams/batch_detail":



            b_id = qs.get("batch_id", [None])[0]



            if not b_id:



                self._send_json({"success": False, "error": "batch_id is required"}, status=400)



                return



            batch = self.db.get_exam_batch(b_id)



            if batch:



                self._send_json({"success": True, "batch": batch})



            else:



                self._send_json({"success": False, "error": "Exam batch not found"}, status=404)



            return







        if path == "/api/practice_tests/template":



            tmpl = self.db.get_daily_practice_template()



            self._send_json({"success": True, "template": tmpl})



            return







        if path == "/api/practice_tests/today":



            from core.daily_practice_test_engine import DailyPracticeTestEngine



            today = DailyPracticeTestEngine.today_str()



            sched = self.db.get_daily_practice_schedule(today)



            deadline_info = DailyPracticeTestEngine.get_morning_deadline_info(today)



            tmpl = self.db.get_daily_practice_template()



            self._send_json({



                "success": True,



                "date": today,



                "schedule": sched,



                "deadline_info": deadline_info,



                "template": tmpl,



            })



            return







        if path == "/api/practice_tests/schedule":



            from core.daily_practice_test_engine import DailyPracticeTestEngine



            d_val = qs.get("date", [DailyPracticeTestEngine.today_str()])[0]



            sched = self.db.get_daily_practice_schedule(d_val)



            deadline_info = DailyPracticeTestEngine.get_morning_deadline_info(d_val)



            self._send_json({"success": True, "date": d_val, "schedule": sched, "deadline_info": deadline_info})



            return







        if path == "/api/practice_tests/summary":



            from core.daily_practice_test_engine import DailyPracticeTestEngine



            d_val = qs.get("date", [DailyPracticeTestEngine.today_str()])[0]



            summary = self.db.get_practice_test_summary(d_val)



            self._send_json({"success": True, "summary": summary})



            return







        if path == "/api/events/list":



            events = self.db.list_official_event_bookings(include_past=True)



            self._send_json({"success": True, "events": events})



            return







        if path in ("/api/schedule/email-prebooks", "/api/email-prebook/list"):



            include_past = qs.get("include_past", ["true"])[0].lower() == "true"



            exams = self.db.list_exam_prebookings(include_past=include_past)



            events = self.db.list_official_event_bookings(include_past=include_past)



            email_exams = [e for e in exams if e.get("validation_provider") in ("OPENAI_API", "HEURISTIC_PARSER") or "EB-" in str(e.get("batch_id", ""))]



            email_events = [ev for ev in events if ev.get("category") in ("MOVIE_PREBOOK", "TRAVEL_PREBOOK") or "pre_" in str(ev.get("event_id", ""))]



            self._send_json({"success": True, "exams": email_exams or exams, "events": email_events or events})



            return







        if path == "/api/events/active_window":



            from core.official_event_engine import evaluate_exit_window



            events = self.db.list_official_event_bookings(status="CONFIRMED", include_past=False)



            active_ev = None



            msg = "No scheduled event window is currently active."



            has_active = False



            for ev in events:



                is_active, reason = evaluate_exit_window(ev)



                if is_active:



                    active_ev = ev



                    has_active = True



                    msg = reason



                    break



            self._send_json({"has_active_window": has_active, "active_event": active_ev, "message": msg})



            return







        if path == "/api/recovery/status":



            # Never show recovery challenge on startup under any circumstances



            self._send_json({"recovery_needed": False})



            return







        if path == "/api/recovery/challenge":



            cid = qs.get("challenge_id", [""])[0]



            chall = self.db.get_recovery_challenge(cid) if cid else None



            self._send_json({"challenge": chall})



            return







        if path == "/api/strictbot/model_info":



            from core.real_ai_vlm import RealAIBotEngine



            vlm = RealAIBotEngine.get_instance()



            self._send_json(vlm.get_model_status())



            return







        # ── APK Release Manifest & Download Routes (Requirement 2, 3, 6, 7) ────



        if path == "/api/releases/latest":



            platform = qs.get("platform", ["ANDROID"])[0]



            channel = qs.get("channel", ["PRODUCTION"])[0]



            rel = self.db.get_latest_app_release(platform=platform, channel=channel)



            if not rel:



                self._send_json({"success": False, "release": None, "message": "No active release published"}, status=404)



                return







            host_header = self.headers.get("Host", "")



            if not host_header or "127.0.0.1" in host_header or "localhost" in host_header:



                host_header = f"{get_local_ip()}:8765"



            proto = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"



            dl_path = rel.get("download_url") or f"/api/releases/download/{rel['release_id']}.apk"



            if not dl_path.startswith("http://") and not dl_path.startswith("https://"):



                if not dl_path.startswith("/"):



                    dl_path = f"/{dl_path}"



                dl_url = f"{proto}://{host_header}{dl_path}"



            else:



                dl_url = dl_path







            manifest = {



                "application": "StrictRecords",



                "platform": rel.get("platform", "ANDROID"),



                "versionName": rel.get("version_name"),



                "versionCode": rel.get("version_code"),



                "minimumSupportedVersion": rel.get("min_supported_version", 1),



                "releaseDate": rel.get("release_date"),



                "releaseNotes": rel.get("release_notes", ""),



                "mandatory": bool(rel.get("is_mandatory", 0)),



                "downloadUrl": dl_url,



                "sha256": rel.get("sha256_checksum", ""),



                "fileSizeBytes": rel.get("file_size_bytes", 0),



                "channel": rel.get("channel", "PRODUCTION"),



                "signingCertificateSha256": rel.get("signing_cert_sha256", ""),



                "releaseId": rel.get("release_id"),



            }



            self._send_json({"success": True, "release": manifest})



            return







        if path.startswith("/api/releases/download/") or path.startswith("/releases/"):



            self.close_connection = True



            filename = os.path.basename(path)



            releases_dir = os.path.join(ROOT_DIR, "releases")



            candidate_paths = [



                os.path.join(releases_dir, filename),



                os.path.join(ROOT_DIR, filename),



            ]



            if filename in ("strictrecords.apk", "StrictRecords.apk", "latest.apk", "app-release.apk", "app-debug.apk"):



                candidate_paths.extend([



                    os.path.join(ROOT_DIR, "StrictRecords-debug.apk"),



                    os.path.join(ROOT_DIR, "StrictRoutines-debug.apk"),



                ])



            target_apk = None



            for p in candidate_paths:



                if os.path.exists(p) and os.path.isfile(p):



                    target_apk = p



                    break







            if not target_apk:



                self.send_response(404)



                self.end_headers()



                return







            file_size = os.path.getsize(target_apk)



            self.send_response(200)



            self.send_header("Content-Type", "application/vnd.android.package-archive")



            self.send_header("Content-Length", str(file_size))



            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')



            self.send_header("Cache-Control", "no-cache")



            self.send_header("Accept-Ranges", "bytes")



            self.send_header("Connection", "close")



            self.end_headers()







            try:



                with open(target_apk, "rb") as f:



                    while chunk := f.read(65536):



                        self.wfile.write(chunk)



                self.wfile.flush()



            except Exception as e:



                logger.error(f"Error streaming APK download: {e}")



            return







        if path == "/api/releases/list":



            platform = qs.get("platform", [None])[0]



            channel = qs.get("channel", [None])[0]



            releases = self.db.list_app_releases(platform=platform, channel=channel)



            device_statuses = self.db.get_all_device_update_statuses()



            self._send_json({"success": True, "releases": releases, "device_statuses": device_statuses})



            return







        if path == "/api/releases/overview":



            platform = qs.get("platform", ["ANDROID"])[0]



            channel = qs.get("channel", ["PRODUCTION"])[0]



            self._send_json(self._get_release_overview(platform=platform, channel=channel))



            return







        # ── Admin Ecosystem GET Routes ───────────────────────────



        if path == "/api/admin/verify_session":



            token = qs.get("token", [""])[0]



            sess = self.db.verify_admin_session(token)



            if sess:



                self._send_json({"valid": True, "session": sess})



            else:



                self._send_json({"valid": False, "error": "Invalid or expired session."}, status=401)



            return







        if path == "/api/admin/proofs":



            st = qs.get("status", [None])[0]



            uid = qs.get("user_id", [None])[0]



            dt = qs.get("date", [None])[0]



            tc = qs.get("task_code", [None])[0]



            prio = qs.get("priority", [None])[0]



            lim = int(qs.get("limit", [50])[0])



            proofs = self.db.get_proofs(status=st, user_id=uid, date=dt, task_code=tc, priority=prio, limit=lim)



            counts = self.db.get_proof_counts()



            self._send_json({"success": True, "proofs": proofs, "pending_reviews": proofs, "counts": counts})



            return







        if path == "/api/admin/proof/details":



            eid = qs.get("evidence_id", [""])[0]



            details = self.db.get_proof_details(eid)



            if details:



                self._send_json(details)



            else:



                self._send_json({"error": "Proof not found"}, status=404)



            return







        if path == "/api/admin/hourly/submissions":



            from core.hourly_admin_service import HourlyAdminService



            svc = HourlyAdminService(self.db, self.engine)



            dt = qs.get("date", [None])[0]



            cat = qs.get("category", [None])[0]



            st = qs.get("status", [None])[0]



            occ = qs.get("occurrence_id", [None])[0]



            items = svc.list_hourly_submissions(date=dt, category=cat, status=st, occurrence_id=occ)



            self._send_json({"success": True, "date": dt or now_ist().strftime("%Y-%m-%d"), "submissions": items, "count": len(items), "configured_limit": svc.get_configured_hourly_limit()})



            return







        if path == "/api/admin/hourly/submission/detail":



            from core.hourly_admin_service import HourlyAdminService



            svc = HourlyAdminService(self.db, self.engine)



            act_id = qs.get("activity_id", [""])[0]



            detail = svc.get_hourly_submission_detail(act_id)



            if detail:



                self._send_json({"success": True, "detail": detail})



            else:



                self._send_json({"success": False, "error": "Hourly occurrence not found"}, status=404)



            return







        if path == "/api/pc/shutdown_status":



            dt = qs.get("date", [None])[0]



            uid = qs.get("user_id", ["user_lok"])[0]



            st = self.db.get_pc_shutdown_status(date=dt, account_id=uid)



            self._send_json({"success": True, **st})



            return







        if path == "/api/admin/pc_shutdown_reviews":



            if not self._authorize_admin_request(query=qs):



                return



            uid = qs.get("user_id", ["user_lok"])[0]



            reviews = self.db.get_all_pc_shutdown_reviews(account_id=uid)



            self._send_json({"success": True, "reviews": reviews})



            return







        if path == "/api/admin/users":



            with self.db._connect() as conn:



                users = conn.execute("SELECT * FROM users").fetchall()



                u_list = []



                for u in users:



                    uid = u["user_id"]



                    d_row = conn.execute(



                        "SELECT * FROM daily_records WHERE user_id = ? AND is_dev_mode = 0 ORDER BY date DESC LIMIT 1",



                        (uid,),



                    ).fetchone()



                    p_cnt = conn.execute(



                        "SELECT COUNT(*) as c FROM proof_reviews WHERE user_id = ? AND status = 'PENDING'",



                        (uid,),



                    ).fetchone()["c"]



                    # Calculate real streak



                    streak_rows = conn.execute(



                        "SELECT final_score FROM daily_records WHERE user_id = ? AND is_dev_mode = 0 ORDER BY date DESC",



                        (uid,),



                    ).fetchall()



                    real_streak = 0



                    for sr in streak_rows:



                        if sr["final_score"] is not None and sr["final_score"] >= 80:



                            real_streak += 1



                        else:



                            break



                    # Real device status



                    and_dev = conn.execute(



                        "SELECT status, last_seen FROM devices WHERE user_id = ? AND platform = 'ANDROID' ORDER BY rowid DESC LIMIT 1",



                        (uid,),



                    ).fetchone()



                    win_dev = conn.execute(



                        "SELECT status, last_seen FROM devices WHERE user_id = ? AND platform = 'WINDOWS' ORDER BY rowid DESC LIMIT 1",



                        (uid,),



                    ).fetchone()







                    android_status = and_dev["status"] if and_dev and and_dev["status"] else "OFFLINE"



                    windows_status = win_dev["status"] if win_dev and win_dev["status"] else "OFFLINE"



                    last_sync = and_dev["last_seen"] if (and_dev and and_dev["last_seen"]) else (



                        win_dev["last_seen"] if (win_dev and win_dev["last_seen"]) else "Never"



                    )







                    u_dict = dict(u)



                    u_dict["current_score"] = int(d_row["final_score"]) if (d_row and d_row["final_score"] is not None) else 0



                    u_dict["streak_days"] = real_streak



                    u_dict["pending_proofs"] = p_cnt



                    u_dict["android_status"] = android_status



                    u_dict["windows_status"] = windows_status



                    u_dict["last_sync"] = last_sync



                    u_dict["current_mode"] = "NORMAL"



                    u_list.append(u_dict)



                self._send_json({"users": u_list})



            return







        if path == "/api/time":



            self._send_json(TimeWindowEngine.get_authoritative_time())



            return







        if path == "/api/schedule/report":



            # Activity descriptions, review decisions, and evidence identifiers are



            # private account data. Keep this detailed endpoint on the local desktop UI.



            if str(self.client_address[0]) not in ("127.0.0.1", "::1"):



                self._send_json({"error": "Detailed activity reports are available only on the paired desktop."}, status=403)



                return



            from core.activity_report import build_daily_activity_report



            date = qs.get("date", [now_ist().strftime("%Y-%m-%d")])[0]



            try:



                datetime.strptime(date, "%Y-%m-%d")



            except ValueError:



                self._send_json({"error": "Date must use YYYY-MM-DD."}, status=400)



                return



            twe = TimeWindowEngine(self.db)



            is_today = date == now_ist().strftime("%Y-%m-%d")



            if is_today:



                activities = twe.ensure_activities_in_db(date)



                countdown = twe.get_current_countdown_state(date)



            else:



                # Historical reports are read-only: opening one never runs the scheduler



                # or recalculates an old submission window.



                with self.db._connect() as conn:



                    rows = conn.execute(



                        "SELECT * FROM scheduled_activities WHERE date = ? AND account_id = ? ORDER BY scheduled_time ASC",



                        (date, "user_lok"),



                    ).fetchall()



                activities = [dict(row) for row in rows]



                countdown = None



            report = build_daily_activity_report(self.db, date, activities, account_id="user_lok")



            self._send_json({



                "date": date,



                "authoritative_time": TimeWindowEngine.get_authoritative_time(),



                "countdown": countdown,



                "report": report,



            })



            return







        if path == "/api/schedule/today":



            date = qs.get("date", [now_ist().strftime("%Y-%m-%d")])[0]



            twe = TimeWindowEngine(self.db)



            activities = twe.ensure_activities_in_db(date)



            countdown = twe.get_current_countdown_state(date)



            self._send_json({



                "date": date,



                "authoritative_time": TimeWindowEngine.get_authoritative_time(),



                "countdown": countdown,



                "activities": activities,



            })



            return







        if path == "/api/daily_productivity/summary":



            from core.daily_productivity_engine import DailyProductivityEngine



            date = qs.get("date", [now_ist().strftime("%Y-%m-%d")])[0]



            engine = DailyProductivityEngine(self.db)



            res = engine.evaluate_daily_productivity(date_str=date, force=False)



            self._send_json(res)



            return







        if path == "/api/daily_productivity/evaluate":



            from core.daily_productivity_engine import DailyProductivityEngine



            date = qs.get("date", [now_ist().strftime("%Y-%m-%d")])[0]



            engine = DailyProductivityEngine(self.db)



            res = engine.evaluate_daily_productivity(date_str=date, force=True)



            self._send_json(res)



            return







        if path == "/api/summary/today":



            from core.activity_report import build_truthful_daily_summary



            date = qs.get("date", [now_ist().strftime("%Y-%m-%d")])[0]



            try:



                datetime.strptime(date, "%Y-%m-%d")



            except ValueError:



                self._send_json({"error": "Date must use YYYY-MM-DD."}, status=400)



                return



            twe = TimeWindowEngine(self.db)



            is_today = date == now_ist().strftime("%Y-%m-%d")



            if is_today:



                activities = twe.ensure_activities_in_db(date)



            else:



                activities = None



            summary_payload = build_truthful_daily_summary(



                db=self.db,



                date=date,



                activities=activities,



                account_id="user_lok",



                is_dev_mode=is_dev,



            )



            self._send_json(summary_payload)



            return







        if path == "/api/activity/countdown":



            date = qs.get("date", [now_ist().strftime("%Y-%m-%d")])[0]



            twe = TimeWindowEngine(self.db)



            countdown = twe.get_current_countdown_state(date)



            self._send_json(countdown)



            return







        if path == "/api/notifications/pending":



            date = qs.get("date", [now_ist().strftime("%Y-%m-%d")])[0]



            twe = TimeWindowEngine(self.db)



            pending = twe.get_pending_notifications(date)



            if getattr(self, "notification_service", None) and pending:



                try:



                    self.notification_service.ingest_pending_notifications(pending)



                except Exception:



                    pass



            self._send_json({"notifications": pending})



            return







        if path == "/api/system/tray_status":



            from windows.tray_manager import StrictRecordsTrayManager



            tray = StrictRecordsTrayManager.get_existing_instance()



            snap = tray.get_menu_snapshot() if tray else {"icon_registered": False, "is_running": False, "menu_items": []}



            api = getattr(StrictRecordsRequestHandler, "desktop_api", None)



            win_visible = getattr(api, "_is_visible", True) if api else False



            snap["window_visible"] = win_visible



            self._send_json(snap)



            return







        if path == "/api/system/instances":



            from windows.single_instance_manager import get_strictrecords_app_instances



            instances = get_strictrecords_app_instances()



            self._send_json({



                "success": True,



                "count": len(instances),



                "instances": instances,



                "single_instance_enforced": True,



                "current_pid": os.getpid(),



            })



            return







        if path == "/api/notifications/pause_status":



            svc = getattr(self, "notification_service", None)



            self._send_json({



                "success": True,



                "notifications_paused": bool(svc.notifications_paused) if svc else False,



                "service_running": bool(svc._running) if svc else False,



            })



            return







        # ── Notification Center & Android Mirror GET Routes (Section 1) ──



        if path == "/api/notifications/center":



            category = qs.get("category", [None])[0]



            unread_only = qs.get("unread_only", ["0"])[0] in ("1", "true")



            limit = int(qs.get("limit", [100])[0])



            events = self.db.get_notification_center_events(category=category, unread_only=unread_only, limit=limit)



            self._send_json({"events": events, "count": len(events)})



            return







        if path == "/api/android/notifications":



            unread_only = qs.get("unread_only", ["0"])[0] in ("1", "true")



            limit = int(qs.get("limit", [50])[0])



            notifs = self.db.get_android_notifications(unread_only=unread_only, limit=limit)



            self._send_json({"notifications": notifs, "count": len(notifs)})



            return







        if path == "/api/android/mirror/status":



            stat = self.notification_service.get_status() if getattr(self, "notification_service", None) else {



                "service_running": False, "mirror_status": "DISABLED", "mirror_enabled": False



            }



            self._send_json(stat)



            return







        if path in ("/api/device/submission_events", "/api/hourly/submission_events"):



            from core.hourly_submission_service import HourlySubmissionService



            try:



                if path == "/api/device/submission_events":



                    dev_id = qs.get("device_id", [""])[0]



                    token = qs.get("device_token", [""])[0]



                    account_id = self._get_authenticated_device_account(dev_id, token)



                else:



                    account_id, dev_id = self._get_windows_submission_identity()



                events = HourlySubmissionService(self.db, self.engine).get_pending_events(account_id, dev_id)



                self._send_json({"success": True, "events": events})



            except PermissionError as exc:



                self._send_json({"success": False, "error": str(exc), "code": "UNAUTHORIZED_DEVICE"}, status=403)



            return







        # ── Real-Time EventBus Stream & Polling Routes (Section 6 & 7) ──



        if path == "/api/events/stream":



            try:



                event_principal = self._strictbot_principal()



            except (AssistantError, PermissionError) as exc:



                self._send_json({"success": False, "error": str(exc)}, status=getattr(exc, "status", 403))



                return



            self.protocol_version = "HTTP/1.1"



            self.close_connection = False



            self.send_response(200)



            self.send_header("Content-Type", "text/event-stream; charset=utf-8")



            self.send_header("Cache-Control", "no-cache, no-transform")



            self.send_header("Connection", "keep-alive")



            self.send_header("Access-Control-Allow-Origin", "*")



            self.end_headers()







            bus = EventBus.get_instance()



            q = bus.subscribe()



            try:



                init_msg = json.dumps({"status": "READY", "timestamp": datetime.now().isoformat()})



                self.wfile.write(f"event: connected\ndata: {init_msg}\n\n".encode("utf-8"))



                self.wfile.flush()



                while True:



                    try:



                        evt = q.get(timeout=10.0)



                        evt = self._scoped_event(evt, event_principal)



                        if evt is None:



                            continue



                        etype = evt.get("event_type", "message")



                        self.wfile.write(f"event: {etype}\ndata: {json.dumps(evt)}\n\n".encode("utf-8"))



                        self.wfile.flush()



                    except queue.Empty:



                        self.wfile.write(b": heartbeat\n\n")



                        self.wfile.flush()



            except Exception:



                pass



            finally:



                bus.unsubscribe(q)



            return







        if path == "/api/events/poll":



            try:



                event_principal = self._strictbot_principal()



            except (AssistantError, PermissionError) as exc:



                self._send_json({"success": False, "error": str(exc)}, status=getattr(exc, "status", 403))



                return



            since = qs.get("since", [None])[0]



            bus = EventBus.get_instance()



            events = bus.get_events_since(since_id=since)



            events = [safe for event in events if (safe := self._scoped_event(event, event_principal)) is not None]



            self._send_json({"events": events, "count": len(events), "server_time": TimeWindowEngine.get_authoritative_time()["server_time"]})



            return







        if path == "/api/hourly/attendance":



            # TimeWindowEngine and now_ist are imported at module scope.



            now = now_ist()



            date = qs.get("date", [now.strftime("%Y-%m-%d")])[0]



            is_dev = qs.get("is_dev_mode", ["0"])[0].lower() in ("1", "true", "yes")



            twe = TimeWindowEngine(self.db)



            twe.ensure_activities_in_db(date, account_id=qs.get("account_id", ["user_lok"])[0], reference_now=now)



            records = twe.get_hourly_attendance_records(



                date,



                account_id=qs.get("account_id", ["user_lok"])[0],



                reference_now=now,



                is_dev_mode=is_dev,



            )



            self._send_json({



                "success": True,



                "date": date,



                "records": records,



                "server_time": now.isoformat(),



                "counts": {



                    "present": sum(1 for r in records if r["attendance_status"] == "PRESENT"),



                    "absent": sum(1 for r in records if r["attendance_status"] == "ABSENT"),



                    "open": sum(1 for r in records if r["attendance_status"] == "OPEN"),



                    "upcoming": sum(1 for r in records if r["attendance_status"] == "UPCOMING"),



                    "pending_validation": sum(1 for r in records if r["validation_status"] in ("PROCESSING", "NEEDS_REVIEW", "REVIEW_REQUIRED", "PROVISIONAL_APPROVE", "ERROR")),



                },



            })



            return







        if path in ("/api/hourly/status", "/api/activity/hourly/current"):



            from core.time_window_engine import iso_to_dt, dt_to_iso



            date = qs.get("date", [now_ist().strftime("%Y-%m-%d")])[0]



            twe = TimeWindowEngine(self.db)



            activities = twe.ensure_activities_in_db(date)



            now = now_ist()



            



            hourly_acts = [a for a in activities if a.get("type") == "HOURLY"]



            hourly_acts.sort(key=lambda a: a.get("window_start", ""))







            open_activity = None



            upcoming_activity = None



            last_closed_activity = None







            for a in hourly_acts:



                w_start = iso_to_dt(a.get("window_start", ""))



                w_end = iso_to_dt(a.get("window_end", ""))



                st = a.get("status", "SCHEDULED")







                if w_start <= now < w_end and st in ("OPEN", "BLOCKED_WAKEUP", "SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "REJECTED"):



                    open_activity = a



                    break



                elif now < w_start and st not in ("CLOSED", "VERIFIED", "MISSED", "REJECTED_LATE"):



                    if upcoming_activity is None:



                        upcoming_activity = a



                elif now >= w_end and st in ("MISSED", "CLOSED", "REJECTED_LATE"):



                    last_closed_activity = a







            # Authoritative State Determination



            if open_activity:



                op_status = open_activity.get("status")



                if op_status in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "REJECTED", "COMPLETED"):



                    state = "SUBMITTED"



                    is_window_open = False



                elif op_status == "VERIFIED":



                    state = "VERIFIED"



                    is_window_open = False



                elif op_status == 'BLOCKED_WAKEUP' or (open_activity.get('submission_eligible') is False and op_status == 'OPEN'):



                    state = 'BLOCKED_WAKEUP'



                    is_window_open = False



                elif op_status == "OPEN":



                    state = "OPEN"



                    is_window_open = True



                else:



                    state = "BEFORE_WINDOW"



                    is_window_open = False



                primary_act = open_activity



            elif upcoming_activity:



                state = "BEFORE_WINDOW"



                is_window_open = False



                primary_act = upcoming_activity



            elif last_closed_activity:



                state = "WINDOW_CLOSED"



                is_window_open = False



                primary_act = last_closed_activity



            else:



                state = "ALL_CLOSED"



                is_window_open = False



                primary_act = None







            # Calculate precise human and timestamp window data



            activity_slot = ""



            checkpoint_time = ""



            checkpoint_target_display = ""



            window_start_iso = ""



            window_end_iso = ""



            window_start_display = ""



            window_end_display = ""



            window_display = ""



            seconds_until_open = 0



            seconds_remaining = 0



            next_checkpoint_display = ""







            if primary_act:



                det = {}



                try:



                    det = json.loads(primary_act.get("details_json") or "{}")



                except Exception:



                    pass



                checkpoint_dt = iso_to_dt(primary_act["scheduled_time"])



                checkpoint_time = checkpoint_dt.strftime("%H:%M")



                checkpoint_target_display = TimeWindowEngine.format_time_12h(checkpoint_time)



                slot_start = TimeWindowEngine.format_time_12h(f"{(checkpoint_dt.hour - 1) % 24:02d}:{checkpoint_dt.minute:02d}")



                slot_end = TimeWindowEngine.format_time_12h(checkpoint_time)



                activity_slot = f"{slot_start}–{slot_end}"







                ws_dt = iso_to_dt(primary_act.get("window_start", ""))



                we_dt = iso_to_dt(primary_act.get("window_end", ""))



                window_start_iso = dt_to_iso(ws_dt)



                window_end_iso = dt_to_iso(we_dt)



                window_start_display = TimeWindowEngine.format_time_12h(ws_dt.strftime("%H:%M"))



                window_end_display = TimeWindowEngine.format_time_12h(we_dt.strftime("%H:%M"))



                window_display = f"{window_start_display}–{window_end_display}"



                seconds_until_open = max(0, int((ws_dt - now).total_seconds()))



                seconds_remaining = max(0, int((we_dt - now).total_seconds()))







            if upcoming_activity:



                next_checkpoint_display = TimeWindowEngine.format_time_12h(iso_to_dt(upcoming_activity["scheduled_time"]).strftime("%H:%M"))







            review_info = None



            evidence_id = None



            if primary_act:



                try:



                    p_det = json.loads(primary_act.get("details_json") or "{}")



                    evidence_id = p_det.get("evidence_id") or p_det.get("submission_id")



                except Exception:



                    pass



            with self.db._connect() as conn:



                pr_row = None



                if evidence_id:



                    pr_row = conn.execute("SELECT * FROM proof_reviews WHERE evidence_id = ?", (evidence_id,)).fetchone()



                if pr_row:



                    review_info = dict(pr_row)



                    try:



                        review_info["signals"] = json.loads(review_info.get("signals_json") or "{}")



                    except Exception:



                        review_info["signals"] = {}







            countdown = twe.get_current_countdown_state(date, reference_now=now)



            self._send_json({



                "success": True,



                "state": state,



                "is_window_open": is_window_open,



                "hourly_activity": primary_act,



                "attendance_status": (primary_act.get("attendance_status") if primary_act else None),



                "validation_status": (primary_act.get("validation_status") if primary_act else None),



                "open_activity": open_activity,



                "upcoming_activity": upcoming_activity,



                "last_closed_activity": last_closed_activity,



                "activity_slot": activity_slot,



                "checkpoint_time": checkpoint_time,



                "checkpoint_target_display": checkpoint_target_display,



                "window_start": window_start_iso,



                "window_end": window_end_iso,



                "window_start_display": window_start_display,



                "window_end_display": window_end_display,



                "window_display": window_display,



                "seconds_until_open": seconds_until_open,



                "seconds_remaining": seconds_remaining,



                "next_checkpoint_display": next_checkpoint_display,



                "review_info": review_info,



                "countdown": countdown,



                "server_time": now.isoformat(),



            })



            return







        if path == "/api/proof/ai_review":



            evidence_id = qs.get("evidence_id", [None])[0]



            activity_id = qs.get("activity_id", [None])[0]



            with self.db._connect() as conn:



                row = None



                if evidence_id:



                    row = conn.execute("SELECT * FROM proof_reviews WHERE evidence_id = ?", (evidence_id,)).fetchone()



                elif activity_id:



                    act = conn.execute("SELECT details_json FROM scheduled_activities WHERE activity_id = ?", (activity_id,)).fetchone()



                    if act and act["details_json"]:



                        try:



                            d = json.loads(act["details_json"])



                            eid = d.get("evidence_id") or d.get("submission_id")



                            if eid:



                                row = conn.execute("SELECT * FROM proof_reviews WHERE evidence_id = ?", (eid,)).fetchone()



                        except Exception:



                            pass



                if not row:



                    row = conn.execute("SELECT * FROM proof_reviews ORDER BY submitted_at DESC LIMIT 1").fetchone()







            if row:



                res = dict(row)



                try:



                    res["signals"] = json.loads(res.get("signals_json") or "{}")



                except Exception:



                    res["signals"] = {}



                self._send_json({"success": True, "review": res})



            else:



                self._send_json({"success": False, "message": "No AI review records found."})



            return







        if path == "/api/admin/proofs/pending":



            with self.db._connect() as conn:



                rows = conn.execute(



                    """



                    SELECT pr.*, ef.file_path, ef.sha256_hash, ef.file_size_bytes, ef.device_id as upload_device



                    FROM proof_reviews pr



                    LEFT JOIN evidence_files ef ON pr.evidence_id = ef.evidence_id



                    WHERE pr.status IN ('PENDING', 'PROVISIONAL', 'UNDER_REVIEW', 'NEEDS_REVIEW', 'DUPLICATE_REVIEW', 'PENDING_ADMIN_REVIEW')



                    ORDER BY pr.submitted_at DESC LIMIT 50



                    """



                ).fetchall()



                out = []



                for r in rows:



                    item = dict(r)



                    try:



                        item["signals"] = json.loads(item.get("signals_json") or "{}")



                    except Exception:



                        item["signals"] = {}



                    out.append(item)



            self._send_json({"success": True, "pending_reviews": out, "count": len(out)})



            return







        if path == "/api/device/proof_evidence":



            self._handle_device_proof_evidence(qs)



            return







        if path == "/api/evidence/view":



            evidence_id = qs.get("evidence_id", [""])[0]



            file_p = qs.get("path", [""])[0]



            mime = "image/jpeg"







            if evidence_id:



                with self.db._connect() as conn:



                    ef = conn.execute("SELECT file_path, metadata_json FROM evidence_files WHERE evidence_id = ?", (evidence_id,)).fetchone()



                    if ef and ef["file_path"]:



                        file_p = ef["file_path"]



                        try:



                            meta = json.loads(ef["metadata_json"] or "{}")



                            if meta.get("detected_mime_type"):



                                mime = meta["detected_mime_type"]



                        except Exception:



                            pass







            if not file_p:



                self.send_error(400, "Missing path or evidence_id")



                return







            real_p = os.path.realpath(file_p)



            allowed_dirs = [



                os.path.realpath(ROOT_DIR),



                os.path.realpath(os.path.join(os.environ.get("LOCALAPPDATA", ""), "StrictRecords")),



                os.path.realpath(tempfile.gettempdir()),



            ]



            if hasattr(self, "engine") and getattr(self.engine, "storage_root", None):



                allowed_dirs.append(os.path.realpath(self.engine.storage_root))



            if not any(real_p.startswith(ad) for ad in allowed_dirs):



                self.send_error(403, "Access to requested path is forbidden")



                return







            if not os.path.exists(real_p) or not os.path.isfile(real_p):



                self.send_error(404, "Evidence file not found")



                return







            ext = os.path.splitext(real_p)[1].lower()



            if ext in (".jpg", ".jpeg"):



                mime = "image/jpeg"



            elif ext == ".png":



                mime = "image/png"



            elif ext == ".webp":



                mime = "image/webp"







            try:



                with open(real_p, "rb") as f:



                    img_data = f.read()



                self.send_response(200)



                self.send_header("Content-Type", mime)



                self.send_header("Content-Length", str(len(img_data)))



                self.send_header("Cache-Control", "public, max-age=3600")



                self.end_headers()



                self.wfile.write(img_data)



            except Exception as e:



                self.send_error(500, f"Error reading file: {e}")



            return







        # ── Wake-Up State Machine & Visual Checklist GET Route (Sections 3-7) ──



        if path == "/api/wakeup/checklist":



            date = qs.get("date", [now_ist().strftime("%Y-%m-%d")])[0]



            res = self.engine.get_wakeup_checklist(date)



            self._send_json(res)



            return







        if path == "/api/wakeup/earbuds_config":



            self._send_json(self.db.get_earbuds_config())



            return







        # ── Single-Video YouTube / Entertainment Reward Status GET Route ──



        if path in ("/api/rewards/youtube/status", "/api/rewards/entertainment/status"):



            self._handle_youtube_reward(path, query=qs)



            return







        if path.startswith("/api/rewards/early_sleep/"):



            self._handle_early_sleep_reward(path, query=qs)



            return







        if path in ("/api/youtube/educational/status", "/api/youtube/educational/history", "/api/youtube/educational/metadata"):



            self._handle_youtube_educational(path, query=qs)



            return







        # ── GYM Mode Status GET Route ──



        if path == '/api/gym/routine':



            self._handle_gym_routine(query=qs)



            return







        if path == "/api/gym_mode/status":



            self._handle_gym_request(path,{})



            return







        if path == '/api/gym/decision':



            from core.gym_daily_decision import DailyGymDecision



            try:



                principal=self._gym_principal()



                day=qs.get('date',[now_ist().strftime('%Y-%m-%d')])[0]



                self._send_json(dict(success=True,decision=DailyGymDecision(self.db).get(principal.account_id,day)))



            except (PermissionError,AssistantError,ValueError) as error:



                self._send_json(dict(success=False,error=str(error)),status=403)



            return







        if path == '/api/planning/college':



            from core.daily_college_decision import DailyCollegeDecision



            try:



                principal=self._gym_principal()



                day=qs.get('date',[now_ist().strftime('%Y-%m-%d')])[0]



                self._send_json(dict(success=True,college_decision=DailyCollegeDecision(self.db).get(principal.account_id,day)))



            except (PermissionError,AssistantError,ValueError) as error:



                self._send_json(dict(success=False,error=str(error)),status=403)



            return







        # ── Taufa AI Validation Demo Status GET Route ──



        if path == "/api/demo/taufa_validation/status":



            from core.taufa_ai_validator import TaufaAiValidator



            t_val = TaufaAiValidator()



            self._send_json({



                "success": True,



                "is_demo": True,



                "configured": t_val.is_configured(),



                "endpoint": t_val.get_endpoint() if t_val.is_configured() else None,



                "supported_categories": t_val.get_supported_categories(),



                "max_images": 10,



                "mode": "DEMO — NO REAL POINTS",



            })



            return







        # ── YouTube Privilege Status GET Route (Section 11) ──



        if path == "/api/privilege/youtube/status":



            from core.privilege_engine import PrivilegeEngine



            if not self.restriction_mgr:



                self._send_json({



                    "state": "UNAVAILABLE",



                    "allowed": False,



                    "error": "Restriction service is unavailable.",



                    "code": "SERVICE_UNAVAILABLE",



                }, status=503)



                return



            pts = self.restriction_mgr.get_points_status()



            is_yt_unlocked = self.restriction_mgr.is_youtube_unlocked_for_today() or self.restriction_mgr.manual_admin_release



            priv_mode = "CONTINUOUS_PROOF_GATED"



            if self.db:



                try:



                    fcfg = self.db.get_focus_settings()



                    priv_mode = fcfg.get("youtube_privilege_mode", "CONTINUOUS_PROOF_GATED")



                except Exception:



                    pass



            today_s = now_ist().strftime("%Y-%m-%d")



            last_h_status = None



            h_missed = 0



            w_done = False



            if self.db:



                try:



                    with self.db._connect() as conn:



                        w_row = conn.execute("SELECT attendance_status FROM wake_up_sessions WHERE date = ? AND is_dev_mode = 0", (today_s,)).fetchone()



                        if w_row:



                            w_done = (w_row["attendance_status"] == "COMPLETED")



                        h_rows = conn.execute("SELECT status FROM hourly_records WHERE date = ? AND is_dev_mode = 0 ORDER BY checkpoint_time DESC", (today_s,)).fetchall()



                        if h_rows:



                            last_h_status = h_rows[0]["status"]



                            h_missed = sum(1 for r in h_rows if r["status"] == "MISSED")



                except Exception:



                    pass



            p_eng = PrivilegeEngine(db=self.db, default_threshold=pts["required_score"])



            verdict = p_eng.evaluate_youtube_privilege(



                current_score=pts["current_score"],



                required_score=pts["required_score"],



                privilege_mode=priv_mode,



                is_daily_unlock=is_yt_unlocked,



                last_hourly_proof_status=last_h_status,



                hourly_missed_count_today=h_missed,



                wakeup_completed=w_done,



            )



            verdict["mode"] = priv_mode



            self._send_json(verdict)



            return







        if path in ("/api/schedule/next_day", "/api/schedule/draft"):



            date = qs.get("date", [None])[0]



            uid = qs.get("account_id", [qs.get("user_id", ["user_lok"])[0]])[0]



            twe = TimeWindowEngine(self.db)



            state = twe.get_next_day_schedule_state(account_id=uid, today_date=date)



            self._send_json(state)



            return







        if path == "/api/schedule/confirmed":



            twe = TimeWindowEngine(self.db)



            sdate = qs.get("schedule_date", [qs.get("date", [twe.get_next_calendar_date()])[0]])[0]



            uid = qs.get("account_id", [qs.get("user_id", ["user_lok"])[0]])[0]



            confirmed = self.db.get_confirmed_routine_schedule(sdate, account_id=uid)



            activities = twe.get_activities_for_date(sdate, account_id=uid) if confirmed else []



            self._send_json({



                "schedule_date": sdate,



                "confirmed": confirmed is not None,



                "schedule": twe.build_routine_summary(confirmed, account_id=uid) if confirmed else None,



                "activities": activities,



            })



            return







        if path == "/api/schedule/sync":



            twe = TimeWindowEngine(self.db)



            sdate = qs.get("schedule_date", [qs.get("date", [None])[0]])[0]



            dev_id = qs.get("device_id", [""])[0]



            token = qs.get("device_token", [""])[0]



            try:



                uid = self._get_authenticated_device_account(dev_id, token)



            except PermissionError as exc:



                self._send_json({"success": False, "error": str(exc), "code": "UNAUTHORIZED_DEVICE"}, status=403)



                return



            self._send_json(twe.sync_routine_schedule(schedule_date=sdate, account_id=uid, device_id=dev_id))



            return







        if path == "/api/schedule/audits":



            sdate = qs.get("schedule_date", [qs.get("date", [None])[0]])[0]



            uid = qs.get("account_id", [qs.get("user_id", ["user_lok"])[0]])[0]



            audits = self.db.get_schedule_reschedule_audits(schedule_date=sdate, account_id=uid)



            self._send_json({"audits": audits})



            return







        if path == "/api/ledger/summary":



            date = qs.get("date", [now_ist().strftime("%Y-%m-%d")])[0]



            uid = qs.get("account_id", [qs.get("user_id", ["user_lok"])[0]])[0]



            self._send_json(self.db.get_point_ledger_summary(date=date, account_id=uid))



            return







        if path in ("/api/transactions", "/api/score/transactions", "/api/ledger/transactions"):



            date = qs.get("date", [None])[0]



            cat = qs.get("category", [None])[0]



            st = qs.get("status", [None])[0]



            dev = qs.get("device_id", [None])[0]



            q = qs.get("q", [qs.get("query", [None])[0]])[0]



            limit = int(qs.get("limit", [200])[0])



            res = self.engine.get_unified_transaction_history(



                date=date,



                category=cat,



                status=st,



                device_id=dev,



                search_query=q,



                limit=limit,



            )



            self._send_json(res)



            return







        if path == "/api/admin/dashboard":



            metrics = self.db.get_admin_dashboard_metrics()



            self._send_json(metrics)



            return







        if path == "/api/users":



            users = self.db.list_users()



            self._send_json({"users": users})



            return







        if path in ("/api/user/current", "/api/user/profile"):



            profile = self.db.get_active_user_profile()



            self._send_json({"user": profile, "profile": profile})



            return







        if path == "/api/admin/permissions/status":



            perm_status = self._get_diagnostic_permissions_status()



            self._send_json(perm_status)



            return







        if path == "/api/exams":



            status = qs.get("status", [None])[0]



            exams = self.db.get_exams(status=status)



            self._send_json({"exams": exams})



            return







        if path == "/api/exam/demo":



            details = self.db.get_exam_details("EXAM_DEMO_01")



            if details:



                self._send_json(details)



            else:



                self._send_json({"error": "Demo test not found"}, status=404)



            return







        if path == "/api/exam/attempts":



            exam_id = qs.get("exam_id", [None])[0]



            user_id = qs.get("user_id", [None])[0]



            attempts = self.db.get_exam_attempts(exam_id=exam_id, user_id=user_id)



            self._send_json({"attempts": attempts})



            return







        if path == "/api/admin/devices":



            devs = self.db.get_managed_devices()



            self._send_json({"devices": devs})



            return







        if path == "/api/admin/audit_logs":



            lim = int(qs.get("limit", [100])[0])



            act = qs.get("action_type", [None])[0]



            adm = qs.get("admin_user", [None])[0]



            logs = self.db.get_admin_audit_logs(limit=lim, action_type=act, admin_user=adm)



            self._send_json({"audit_logs": logs})



            return







        if path == "/api/deductions/proposals":



            req_date = qs.get("date", [""])[0] or None



            req_status = qs.get("status", [""])[0] or None



            proposals = self.db.get_deduction_proposals(date=req_date, status=req_status)



            self._send_json({"success": True, "proposals": proposals, "count": len(proposals)})



            return







        if path == "/api/admin/analytics":



            days = int(qs.get("days", [7])[0])



            summary = self.db.get_admin_analytics_summary(days=days)



            self._send_json(summary)



            return







        if path == "/api/admin/config/active":



            cfg = self.db.get_active_configuration()



            self._send_json(cfg)



            return







        if path == "/api/admin/search":



            q = qs.get("q", [""])[0]



            res = self.db.global_admin_search(q)



            self._send_json(res)



            return







        if path == "/api/admin/reports/export":



            fmt = qs.get("format", ["json"])[0].lower()



            df = qs.get("date_from", [None])[0]



            dt = qs.get("date_to", [None])[0]



            if fmt == "csv":



                csv_text = self.db.export_admin_report_csv(df, dt)



                raw = csv_text.encode("utf-8")



                self.send_response(200)



                self.send_header("Content-Type", "text/csv; charset=utf-8")



                self.send_header("Content-Disposition", "attachment; filename=strictrecords_admin_report.csv")



                self.send_header("Content-Length", str(len(raw)))



                self.send_header("Access-Control-Allow-Origin", "*")



                self.end_headers()



                self.wfile.write(raw)



                self.wfile.flush()



                return



            else:



                j_data = self.db.export_admin_report_json(df, dt)



                self._send_json(j_data)



        # ── Device Center & Connection Pipeline GET Routes ───────────



        if path == "/api/account/summary":



            uid = qs.get("user_id", [None])[0]



            dt = qs.get("date", [None])[0]



            summary = self.engine.get_shared_account_summary(user_id=uid, date=dt)



            self._send_json(summary)



            return







        if path == "/api/device/mesh":



            uid = qs.get("user_id", [None])[0]



            mesh = self.connection_manager.get_account_device_mesh(user_id=uid)



            self._send_json(mesh)



            return







        if path == "/api/device/center":



            dev_id = qs.get("device_id", [None])[0]



            self._send_json(self.connection_manager.get_device_center_summary(dev_id))



            return







        if path == "/api/device/command/poll":



            dev_id = qs.get("device_id", [None])[0]



            try:



                self._get_authenticated_device_account(



                    str(dev_id or ""),



                    str(self.headers.get("X-Device-Token") or ""),



                )



            except PermissionError as exc:



                self._send_json({"success": False, "error": str(exc), "code": "UNAUTHORIZED_DEVICE"}, status=403)



                return



            if not self.connection_manager:



                self._send_json({"success": False, "error": "Device connection service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                return



            cmds = self.connection_manager.poll_device_commands(str(dev_id))



            self._send_json({"commands": cmds})



            return







        if path == "/api/device/transfer/queue":



            dev_id = qs.get("device_id", [None])[0]



            try:



                self._get_authenticated_device_account(



                    str(dev_id or ""),



                    str(self.headers.get("X-Device-Token") or ""),



                )



            except PermissionError as exc:



                self._send_json({"success": False, "error": str(exc), "code": "UNAUTHORIZED_DEVICE"}, status=403)



                return



            items = self.db.get_transfer_queue_items(dev_id)



            self._send_json({"queue": items})



            return







        if path == "/api/device/history":



            dev_id = qs.get("device_id", [None])[0]



            lim = int(qs.get("limit", [50])[0])



            hist = self.db.get_connection_history(dev_id, limit=lim)



            self._send_json({"history": hist})



            return







        if path == "/api/device/list":



            devices = self.db.get_registered_devices_list()



            self._send_json({"devices": devices, "total": len(devices)})



            return







        if path == "/api/device/pairing/status":



            pid = qs.get("pairing_id", [None])[0] or qs.get("session_key", [None])[0]



            if not pid:



                self._send_json({"error": "Missing pairing_id or session_key"}, status=400)



                return



            sess = self.db.get_pairing_session(pid)



            if not sess:



                self._send_json({"error": "Pairing session not found"}, status=404)



                return



            self._send_json(sess)



            return







        if path == "/api/restriction/browser_extension/heartbeat":



            if not self._is_local_request():



                self._send_json({"success": False, "error": "Browser extension requests must be local."}, status=403)



                return



            if not self.restriction_mgr:



                self._send_json({"success": False, "error": "Restriction service is unavailable."}, status=503)



                return



            browser_key = str(qs.get("browser", [""])[0] or "").strip().lower()



            self.restriction_mgr.note_browser_extension_heartbeat(browser_key)



            from windows.browser_setup import get_browser_extension_setup



            identity=str(qs.get("installation_id",[""])[0])



            confirmed=get_browser_extension_setup().note_heartbeat(identity,browser_key,str(qs.get("version",[""])[0])) if identity else False



            reload_req = False



            if self.db:



                req_val = self.db.get_config_value(f"reload_ext_{browser_key}") or self.db.get_config_value("reload_ext_all")



                if req_val == "1":



                    reload_req = True



                    self.db.set_config_value(f"reload_ext_{browser_key}", "0")



                    self.db.set_config_value("reload_ext_all", "0")



            spp_status = {}



            try:



                from windows.strict_sensitive_page_protection import SensitivePageProtectionManager



                spp_status = SensitivePageProtectionManager.get_instance().get_status()



            except Exception:



                pass



            self._send_json({



                "success": True,



                "connected": True,



                "installation_connected": confirmed,



                "reload_extension": reload_req,



                "sensitive_page_protection": spp_status,



            })



            return







        if path in ("/api/sensitive_page_protection/status", "/api/restriction/sensitive_page_protection/status"):



            if not self._is_local_request():



                self._send_json({"success": False, "error": "Sensitive page protection status is local only."}, status=403)



                return



            from windows.strict_sensitive_page_protection import SensitivePageProtectionManager



            self._send_json(SensitivePageProtectionManager.get_instance().get_status())



            return







        if path in ("/api/sensitive_page_protection/config", "/api/restriction/sensitive_page_protection/config"):



            if not self._is_local_request():



                self._send_json({"success": False, "error": "Sensitive page protection config is local only."}, status=403)



                return



            from windows.strict_sensitive_page_protection import SensitivePageProtectionManager



            self._send_json({"success": True, **SensitivePageProtectionManager.get_instance().get_config()})



            return







        if path == "/api/restriction/browser_status":



            if not self._is_local_request():



                self._send_json({"error": "Browser extension requests must be local."}, status=403)



                return



            if not self.restriction_mgr:



                self._send_json({"error": "Restriction service is unavailable."}, status=503)



                return



            from core.restriction_targets import TARGETS



            service = str(qs.get("service", [""])[0] or "").strip().lower()



            from core.windows_app_catalog import WINDOWS_WEBSITES



            if service not in TARGETS and service not in WINDOWS_WEBSITES:



                self._send_json({"error": "Unsupported browser restriction service."}, status=400)



                return



            browser_key = str(qs.get("browser", [""])[0] or "").strip().lower()



            self.restriction_mgr.note_browser_extension_heartbeat(browser_key)



            title = str(qs.get("title", [""])[0] or "")[:512]



            url = str(qs.get("url", [""])[0] or "")[:2048]



            try:



                decision = self.restriction_mgr.get_browser_tab_restriction(service, title, url)



            except Exception:



                logger.exception("Browser access decision failed for %s", service)



                self._send_json({"success": False, "restricted": True, "unavailable": True,



                                 "code": "DECISION_SERVICE_ERROR", "service": service,



                                 "error": "Desktop is connected, but the access check failed. Retry or open Diagnostics."}, status=503)



                return



            resp_d = dict(decision, service=service, statusText=decision.get("reason", ""),



                                 policyDecision="BLOCK" if decision.get("restricted") else "ALLOW")



            if "temporaryException" in resp_d:



                resp_d["temporary_exception"] = resp_d["temporaryException"]



            if "temporary_exception" in resp_d and "temporaryException" not in resp_d:



                resp_d["temporaryException"] = resp_d["temporary_exception"]



            self._send_json(resp_d)



            return







        if path == "/api/restriction/status":



            if not self.restriction_mgr:



                self._send_json({



                    "state": "UNAVAILABLE",



                    "allowed": False,



                    "error": "Restriction service is unavailable.",



                    "code": "SERVICE_UNAVAILABLE",



                    "enforcementStatus": "UNKNOWN",



                }, status=503)



                return



            self._send_json(self.restriction_mgr.get_status())



            return







        if path == "/api/restrictions/desktop_access":



            # Native clients may attach to the existing local server. This read-only



            # route never records an extension heartbeat or activates a reward.



            if not self._is_local_request():



                self._send_json({"success": False, "error": "Windows access status is local only."}, status=403)



                return



            if not self.restriction_mgr:



                self._send_json({"success": False, "error": "Windows access engine is unavailable."}, status=503)



                return



            self._send_json(self.restriction_mgr.get_desktop_access_status())



            return







        if path in ("/api/restrictions/status", "/api/restrictions/sidebar"):



            if self.db and self.restriction_mgr:



                self.db.restriction_mgr = self.restriction_mgr



            stat = self.db.get_sidebar_restrictions_status()



            self._send_json(stat)



            return







        if path == "/api/restrictions/diagnostics":



            diag = self.db.get_restriction_diagnostics() if self.db else []



            self._send_json({"success": True, "diagnostics": diag})



            return







        if path == "/api/system/health":



            if not self.restriction_mgr:



                health = {



                    "Application": "DEGRADED",



                    "Local Engine": "RUNNING",



                    "Database": "CONNECTED",



                    "Browser Control": "UNKNOWN",



                    "Restriction": "UNKNOWN",



                    "status_code": 503,



                    "details": {



                        "allowed": False,



                        "enforcementStatus": "UNKNOWN",



                        "error": "Restriction service is unavailable.",



                    },



                }



                self._send_json(health, status=503)



                return



            r_stat = self.restriction_mgr.get_status()



            health = {



                "Application": "READY",



                "Local Engine": "RUNNING",



                "Database": "CONNECTED",



                "Browser Control": "ACTIVE",



                "Restriction": "ENFORCING" if (r_stat and not r_stat.get("allowed", True)) else "ARMED",



                "status_code": 200,



                "Scheduler": "ACTIVE" if self.notification_service and self.notification_service._running and self.notification_service._thread and self.notification_service._thread.is_alive() else "UNAVAILABLE",



                "details": r_stat,



            }



            self._send_json(health)



            return







        if path == "/api/system/startup":



            stat = StrictRecordsWindowsAgent.configure_windows_startup(enable=None)



            self._send_json(stat)



            return







        if path == "/api/strict_session/cloudflare/status":



            if not self.strict_session_manager:



                self._send_json({"success": False, "error": "Strict session manager unavailable"}, status=503)



                return



            self._send_json(self.strict_session_manager.get_cloudflare_status())



            return







        if path == "/api/strict_session/status":



            if not self.strict_session_manager:



                self._send_json({



                    "state": "UNAVAILABLE",



                    "session_id": None,



                    "lock_screen_active": True,



                    "current_score": 0,



                    "hourly_proof_status": "PENDING",



                    "privilege": {"allowed": False},



                    "error": "Strict-session service is unavailable.",



                    "code": "SERVICE_UNAVAILABLE",



                }, status=503)



                return



            self._send_json(self.strict_session_manager.get_status())



            return







        if path == "/api/strict_session/android/state":



            if not self.strict_session_manager:



                self._send_json({"state": "ANDROID_NOT_JOINED", "active_alerts": []})



                return



            self._send_json(self.strict_session_manager.get_android_monitoring_state())



            return



        if path == "/api/strict_session/android/prerequisite":



            if not self.strict_session_manager:



                self._send_json({"state": "ANDROID_NOT_REGISTERED", "ready": False})



                return



            self._send_json(self.strict_session_manager.get_android_prerequisite_status(refresh=False))



            return



        if path == "/api/strict_session/browsers/status":



            if not self.strict_session_manager:



                self._send_json({"passed": False, "prerequisite_status": "SERVICE_UNAVAILABLE"}, status=503)



                return



            self._send_json(self.strict_session_manager.get_browser_cleanup_prerequisite_status(refresh=False))



            return







        if path == "/api/strict_session/phone_alerts":



            if not self.strict_session_manager:



                self._send_json({"alerts": []})



                return



            self._send_json({"alerts": self.strict_session_manager.get_active_phone_usage_alerts()})



            return







        if path == "/api/strict_session/applications/scan":



            if not self.strict_session_manager:



                self._send_json({"success": False, "error": "Strict-session service is unavailable."}, status=503)



                return



            self._send_json(self.strict_session_manager.app_terminator.scan_prohibited_applications(), status=200)



            return







        if path == '/api/strict_session/preparation/status':



            if not self._strict_study_local_action():return



            if not self.strict_session_manager:



                self._send_json({'success':False,'error':'Strict Session unavailable'},status=503);return



            account=self.db.get_active_user_profile().get('user_id','user_lok')



            self._send_json(self.strict_session_manager.preparation.state(account));return







        if path in ("/api/focus/active", "/api/focus/status"):



            sess = self.engine.get_active_focus_session()



            today = now_ist().strftime("%Y-%m-%d")



            daily = self.engine.recalculate_daily_score(today, is_dev_mode=False)



            verified_score = int(daily.get("final_score", 0) or 0)



            fsettings = self.db.get_focus_settings()



            scheduled = self.db.get_scheduled_activities_today() if hasattr(self.db, "get_scheduled_activities_today") else []



            next_act = None



            now_hm = now_ist().strftime("%H:%M")



            for a in scheduled:



                if a.get("window_end", "99:99") >= now_hm:



                    next_act = a



                    break



            devices = self.db.get_registered_devices_list()



            phone_dev = next((d for d in devices if d.get("device_type") == "ANDROID" or d.get("platform") == "ANDROID"), None)



            pc_dev = next((d for d in devices if d.get("device_type") == "WINDOWS" or d.get("platform") == "WINDOWS"), None)



            stat = {



                "has_active_session": sess is not None,



                "session": sess,



                "verified_score": verified_score,



                "required_score": 80,



                "settings": fsettings,



                "next_hourly_window": next_act,



                "devices": {



                    "pc": {



                        "name": pc_dev.get("device_name") if pc_dev else "Windows PC",



                        "status": pc_dev.get("presence_status", "OFFLINE") if pc_dev else "OFFLINE",



                    },



                    "phone": {



                        "name": phone_dev.get("device_name") if phone_dev else "Not registered",



                        "status": phone_dev.get("presence_status", "OFFLINE") if phone_dev else "OFFLINE",



                    },



                },



            }



            self._send_json(stat)



            return







        if path == "/api/device/apps/status":



            apps = self.db.get_device_app_controls(check_adb=True)



            self._send_json({"success": True, "apps": apps})



            return







        if path == "/api/settings/websites":



            sites = self.db.get_allowed_websites()



            cfg = self.db.get_system_manager_settings()



            self._send_json({"success": True, "allowed_websites": sites, "settings": cfg})



            return







        if path == "/api/system/memory":



            if getattr(self, "ram_optimizer", None):



                stats = self.ram_optimizer.get_memory_stats()



            else:



                stats = {"rss_mb": 0, "sqlite_db_cache_mb": 0}



            self._send_json({"success": True, "stats": stats})



            return







        if path == "/api/settings/background_mode":



            cfg = self.db.get_system_manager_settings()



            if getattr(self, "ram_optimizer", None):



                stats = self.ram_optimizer.get_memory_stats()



            else:



                stats = {}



            self._send_json({"success": True, "settings": cfg, "stats": stats})



            return







        if path == "/api/focus/history":



            hist = self.db.get_focus_history(limit=20)



            self._send_json({"history": hist})



            return







        if path == "/api/focus/settings":



            cfg = self.db.get_focus_settings()



            self._send_json(cfg)



            return







        if path == "/api/focus/groups":



            groups = self.db.get_focus_groups()



            leaderboard = self.db.get_focus_leaderboard()



            self._send_json({"groups": groups, "leaderboard": leaderboard})



            return







        if path == "/api/strict_session/audit_logs":



            sid = qs.get("session_id", [None])[0]



            lim = int(qs.get("limit", [100])[0])



            logs = self.db.get_strict_audit_logs(session_id=sid, limit=lim)



            self._send_json({"audit_logs": logs})



            return







        if path == "/api/strict_session/block_page":



            score = self.strict_session_manager.current_score if self.strict_session_manager else 0



            req_score = self.strict_session_manager.policy.score_threshold if self.strict_session_manager else 80



            last_app = (self.strict_session_manager.last_blocked_app if self.strict_session_manager else "Entertainment Site") or "Entertainment Site"



            reason = (self.strict_session_manager.blocked_notification_message if self.strict_session_manager else "Complete the required check to restore your privilege.") or "Complete the required check to restore your privilege."



            block_html = f"""<!DOCTYPE html>



<html>



<head>



  <meta charset="utf-8">



  <title>StrictRecords — Access Restricted</title>



  <style>



    body {{ margin: 0; background: #0c0d12; color: #f0f0f5; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; overflow: hidden; }}



    .card {{ width: 440px; padding: 40px; border-radius: 18px; border: 1px solid rgba(147, 51, 234, 0.35); background: rgba(20, 21, 31, 0.95); text-align: center; box-shadow: 0 25px 50px rgba(0,0,0,0.7); }}



    .eyes {{ font-size: 28px; color: #a855f7; margin: 16px 0; letter-spacing: 6px; }}



    .title {{ font-size: 13px; font-weight: 700; letter-spacing: 2.5px; color: #a1a1aa; text-transform: uppercase; margin-bottom: 8px; }}



    .status {{ font-size: 22px; font-weight: 700; color: #f43f5e; margin: 12px 0; letter-spacing: 1px; }}



    .score {{ font-size: 34px; font-weight: 800; color: #e4e4e7; margin: 16px 0; font-variant-numeric: tabular-nums; }}



    .sub {{ font-size: 14px; color: #94a3b8; line-height: 1.5; margin: 16px 0; }}



    .app-tag {{ display: inline-block; font-size: 12px; padding: 4px 10px; border-radius: 6px; background: rgba(244, 63, 94, 0.15); color: #f43f5e; border: 1px solid rgba(244, 63, 94, 0.3); margin-bottom: 12px; }}



    .btn {{ display: inline-block; padding: 12px 28px; border-radius: 10px; background: rgba(168, 85, 247, 0.2); border: 1px solid #a855f7; color: #e9d5ff; font-weight: 600; text-decoration: none; cursor: pointer; transition: all 0.2s; }}



    .btn:hover {{ background: rgba(168, 85, 247, 0.35); }}



  </style>



</head>



<body>



  <div class="card">



    <div class="title">STRICTRECORDS</div>



    <div class="eyes">◉ &nbsp; ◉</div>



    <div class="status">ACCESS RESTRICTED</div>



    <div class="app-tag">{last_app}</div>



    <div class="score">{score} / {req_score}</div>



    <div class="sub">{reason}</div>



    <a class="btn" href="http://127.0.0.1:8765">Return to StrictRecords</a>



  </div>



</body>



</html>"""



            self.close_connection = True



            raw = block_html.encode("utf-8")



            self.send_response(200)



            self.send_header("Content-Type", "text/html; charset=utf-8")



            self.send_header("Content-Length", str(len(raw)))



            self.send_header("Connection", "close")



            self.end_headers()



            self.wfile.write(raw)



            self.wfile.flush()



            return







        self._send_json({"success": False, "error": f"Endpoint not found: {path}"}, status=404)



        return



    def _get_release_overview(self, platform: str = "ANDROID", channel: str = "PRODUCTION") -> Dict[str, Any]:



        latest_rel = self.db.get_latest_app_release(platform=platform, channel=channel)



        if not latest_rel:



            latest_rel = self.db.get_latest_app_release(platform=platform)







        with self.db._connect() as conn:



            cur = conn.cursor()



            cur.execute("""



                SELECT device_id, device_name, platform, app_version, app_version_code, last_seen, sync_status, pairing_state



                FROM devices



                WHERE (device_type = 'ANDROID' OR platform LIKE '%Android%') AND platform NOT LIKE '%Windows%'



            """)



            dev_rows = [dict(r) for r in cur.fetchall()]











        latest_vcode = int(latest_rel.get("version_code", 1)) if latest_rel else 1



        latest_vname = latest_rel.get("version_name", "1.0.0") if latest_rel else "1.0.0"



        latest_rel_id = latest_rel.get("release_id", "") if latest_rel else ""







        all_statuses = self.db.get_all_device_update_statuses()



        status_by_device = {}



        for s in all_statuses:



            did = s.get("device_id")



            if did not in status_by_device or s.get("release_id") == latest_rel_id:



                status_by_device[did] = s







        dev_details = []



        up_to_date_cnt = 0



        update_avail_cnt = 0



        dl_complete_cnt = 0



        install_confirmed_cnt = 0







        now = datetime.now()







        for d in dev_rows:



            did = d.get("device_id")



            vcode = int(d.get("app_version_code") or 1)



            vname = d.get("app_version") or f"1.{vcode}.0"



            d_stat = status_by_device.get(did) or {}







            # Determine is_online



            is_online = False



            last_seen_str = d.get("last_seen") or ""



            if last_seen_str:



                try:



                    dt = datetime.fromisoformat(last_seen_str.replace("Z", "").replace(" ", "T"))



                    if abs((now - dt).total_seconds()) < 120:



                        is_online = True



                except Exception:



                    pass



            if d.get("sync_status") == "ONLINE":



                is_online = True







            st_code = d_stat.get("status") or ("UP_TO_DATE" if vcode >= latest_vcode else "UPDATE_AVAILABLE")



            progress = int(d_stat.get("progress_percent") or 0)







            if vcode >= latest_vcode or st_code in ("UP_TO_DATE", "INSTALLED"):



                up_to_date_cnt += 1



            else:



                update_avail_cnt += 1







            if st_code in ("DOWNLOAD_VERIFIED", "INSTALLATION_PENDING", "INSTALLED") or progress == 100:



                dl_complete_cnt += 1







            if st_code == "INSTALLED" or (vcode >= latest_vcode and st_code == "UP_TO_DATE"):



                install_confirmed_cnt += 1







            # Format download status



            if st_code == "DOWNLOADING":



                dl_status = f"Downloading ({progress}%)"



            elif st_code in ("DOWNLOAD_VERIFIED", "INSTALLATION_PENDING", "INSTALLED") or progress == 100:



                dl_status = "Complete"



            elif st_code == "FAILED":



                dl_status = "Failed"



            else:



                dl_status = "Not started"







            # Format installation status



            if st_code == "INSTALLED" or (vcode >= latest_vcode and st_code == "UP_TO_DATE"):



                inst_status = "Installed"



            elif st_code in ("INSTALLATION_PENDING", "DOWNLOAD_VERIFIED"):



                inst_status = "Pending"



            elif st_code == "FAILED":



                inst_status = "Failed"



            else:



                inst_status = "Not started" if vcode < latest_vcode else "Installed"







            # Format update status



            if vcode >= latest_vcode:



                update_status_disp = "Up to date ✓"



            elif st_code == "DOWNLOADING":



                update_status_disp = f"Downloading ({progress}%)"



            elif st_code == "VERIFYING":



                update_status_disp = "Verifying package"



            elif st_code == "INSTALLATION_PENDING":



                update_status_disp = "Ready to install"



            elif st_code == "FAILED":



                update_status_disp = "Failed"



            else:



                update_status_disp = "Available"







            dev_details.append({



                "device_id": did,



                "device_name": d.get("device_name") or did,



                "platform": d.get("platform") or "Android",



                "installed_version": vname,



                "installed_version_code": vcode,



                "latest_eligible_version": latest_vname,



                "is_online": is_online,



                "update_status": update_status_disp,



                "download_status": dl_status,



                "installation_status": inst_status,



                "last_sync_time": last_seen_str,



                "last_error": d_stat.get("last_error")



            })







        return {



            "success": True,



            "latest_release": latest_rel,



            "counts": {



                "registered_devices": len(dev_rows),



                "update_available": update_avail_cnt,



                "already_up_to_date": up_to_date_cnt,



                "downloads_completed": dl_complete_cnt,



                "installations_confirmed": install_confirmed_cnt



            },



            "devices": dev_details



        }







    def _handle_releases_inspect(self, raw_bytes: bytes, content_type: str) -> None:



        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)



        authorized = self._authorize_admin_request(query=qs)







        apk_bytes = b""



        filename = "uploaded.apk"







        if "multipart/form-data" in content_type:



            fields, files = parse_multipart_form(raw_bytes, content_type)



            for k, f_info in files.items():



                if f_info.get("filename", "").endswith(".apk") or len(files) == 1:



                    apk_bytes = f_info.get("data", b"")



                    filename = f_info.get("filename", "uploaded.apk")



                    break



        elif "application/json" in content_type:



            try:



                j = json.loads(raw_bytes.decode("utf-8"))



                source_p = j.get("source_apk_path") or j.get("apk_path")



                if source_p and os.path.exists(source_p):



                    with open(source_p, "rb") as sf:



                        apk_bytes = sf.read()



                    filename = os.path.basename(source_p)



                elif j.get("apk_base64"):



                    apk_bytes = base64.b64decode(j["apk_base64"])



            except Exception as ex:



                logger.warning(f"Error parsing json inspect request: {ex}")



        else:



            apk_bytes = raw_bytes



            fn_hdr = self.headers.get("X-File-Name")



            if fn_hdr:



                filename = fn_hdr







        if not authorized:



            self._send_json({



                "success": False,



                "valid": False,



                "error": "Admin authorization required (invalid PIN or session)",



                "code": "UNAUTHORIZED"



            }, status=403)



            return







        if not apk_bytes:



            self._send_json({



                "success": False,



                "valid": False,



                "error": "No APK file content was uploaded.",



                "code": "EMPTY_PAYLOAD"



            }, status=400)



            return







        staging_dir = os.path.join(ROOT_DIR, "releases", "staging")



        os.makedirs(staging_dir, exist_ok=True)



        sha_pref = hashlib.sha256(apk_bytes).hexdigest()[:8]



        staging_id = f"stage_{int(time.time())}_{sha_pref}.apk"



        staged_path = os.path.join(staging_dir, staging_id)



        with open(staged_path, "wb") as f:



            f.write(apk_bytes)







        latest_rel = self.db.get_latest_app_release(platform="ANDROID")



        allow_rollback = qs.get("allow_rollback", ["false"])[0].lower() in ("true", "1")



        report = inspect_apk(staged_path, current_active_release=latest_rel, allow_rollback=allow_rollback)







        resp = {



            "success": True,



            "valid": report["valid"],



            "staging_id": staging_id,



            "filename": filename,



            "metadata": report,



            "current_release": {



                "version_name": latest_rel.get("version_name") if latest_rel else "None",



                "version_code": latest_rel.get("version_code") if latest_rel else 0,



                "channel": latest_rel.get("channel") if latest_rel else "PRODUCTION",



                "signing_cert_sha256": latest_rel.get("signing_cert_sha256") if latest_rel else ""



            },



            "errors": report["errors"],



            "warnings": report["warnings"]



        }



        self._send_json(resp)







    def do_POST(self) -> None:



        parsed = urllib.parse.urlparse(self.path)



        path = parsed.path



        qs = urllib.parse.parse_qs(parsed.query)



        if path.startswith('/api/client/'):



            try:



                size = int(self.headers.get('Content-Length','0'))



                if not 0 <= size <= 65536:



                    self._send_json({'success':False,'error':'Client request is too large'}, status=413)



                    return



                body = json.loads(self.rfile.read(size) or b'{}')



                if not isinstance(body,dict):



                    raise ValueError('Request must be a JSON object')



                from core.client_parity import dispatch



                dispatch(self,'POST',path,urllib.parse.parse_qs(parsed.query),body)



            except (ValueError, UnicodeDecodeError):



                self._send_json({'success':False,'error':'Invalid client JSON request'}, status=400)



            return



        if path in ("/api/media/policy/evaluate", "/api/media/evaluate"):
            try:
                from core.media_restriction_policy import get_media_policy_engine
                size = int(self.headers.get('Content-Length', '0'))
                body = json.loads(self.rfile.read(size) or b'{}') if size > 0 else {}
                target = body.get("target", "")
                player_type = body.get("player_type", "video")
                device_id = body.get("device_id", "desktop")
                engine = get_media_policy_engine()
                res = engine.evaluate_playback(target=target, player_type=player_type, device_id=device_id)
                self._send_json({"success": True, **res})
            except Exception as e:
                logger.exception("Failed to evaluate media policy in POST: %s", e)
                self._send_json({"success": False, "error": str(e)}, status=500)
            return

        if path == "/api/media/report_attempt":
            try:
                from core.media_restriction_policy import get_media_policy_engine
                size = int(self.headers.get('Content-Length', '0'))
                body = json.loads(self.rfile.read(size) or b'{}') if size > 0 else {}
                target = body.get("target", "")
                device_id = body.get("device_id", "desktop")
                reason = body.get("reason", "Unauthorized playback attempted")
                engine = get_media_policy_engine()
                engine._record_violation_attempt(target, device_id, now_ist(), reason)
                self._send_json({"success": True, "recorded": True})
            except Exception as e:
                logger.exception("Failed to record media attempt: %s", e)
                self._send_json({"success": False, "error": str(e)}, status=500)
            return

        if path == "/api/media/rewards/claim":
            try:
                from core.media_restriction_policy import get_media_policy_engine
                import uuid
                size = int(self.headers.get('Content-Length', '0'))
                body = json.loads(self.rfile.read(size) or b'{}') if size > 0 else {}
                entitlement_id = body.get("entitlement_id") or str(uuid.uuid4())
                source = body.get("source", "REWARD")
                authorized_target = body.get("authorized_target", "")
                duration_seconds = int(body.get("duration_seconds", 1800))
                device_id = body.get("device_id", "desktop")
                engine = get_media_policy_engine()
                rec = engine.ledger.grant_entitlement(
                    entitlement_id=entitlement_id,
                    source=source,
                    authorized_target=authorized_target,
                    duration_seconds=duration_seconds,
                    device_id=device_id,
                )
                self._send_json({"success": True, "entitlement": rec})
            except Exception as e:
                logger.exception("Failed to claim media reward: %s", e)
                self._send_json({"success": False, "error": str(e)}, status=500)
            return

        if path.startswith("/api/google/") or path == "/api/releases/stage_windows":



            # Drain a bounded legacy request before closing its socket, so Windows



            # clients receive the 410 response rather than an unread-body reset.



            try:



                size = int(self.headers.get("Content-Length", "0"))



                if 0 < size <= 65536:



                    self.rfile.read(size)



            except (ValueError, OSError):



                pass



            self._removed_cloud_feature()



            return







        if path == "/api/taufa/session":



            self._send_json({"success":False,"code":"COOKIE_CAPTURE_RETIRED",



                "error":"Use supported Taufa API authorization; browser cookies are not accepted."},status=410)



            return







        if path == "/api/taufa/clear":



            if not self._is_local_request():



                self._send_json({"success": False, "error": "Session configuration is available on the PC only."}, status=403)



                return



            try:



                from core.taufa_client import TaufaClient



                client = TaufaClient()



                client.clear_session()



                self._send_json({"success": True, "message": "Taufa AI session cleared.", "status": client.get_status()})



            except Exception as e:



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path in ("/api/taufa/ask", "/api/taufa/evaluate", "/api/taufa/validate", "/api/taufa/evaluate_unified"):



            try:



                length = int(self.headers.get("Content-Length", "0"))



                if not 0 < length <= MAX_HOURLY_REQUEST_BYTES:



                    self._send_json({"success": False, "error": "Invalid image request size."}, status=413)



                    return



                raw_data = self.rfile.read(length) if length > 0 else b"{}"



                data = json.loads(raw_data) if raw_data else {}



                if not self._is_local_request():



                    try:



                        self._get_authenticated_device_account(



                            self.headers.get("X-Device-Id") or str(data.get("device_id") or ""),



                            self.headers.get("X-Device-Token") or str(data.get("device_token") or ""))



                    except PermissionError:



                        self._send_json({"success": False, "error": "Pair this device before requesting Taufa validation."}, status=403)



                        return



                category = data.get("category", "STUDY")



                description = data.get("description", "")



                sub_id = data.get("submission_id")



                eval_id = data.get("evaluation_id")



                sched_ctx = data.get("schedule_context")



                filenames = data.get("image_filenames") or data.get("filenames") or []



                user_id = data.get("user_id") or ""



                images_b64 = data.get("images") or []



                raw_images = []



                for b in images_b64:



                    if isinstance(b, str):



                        if "," in b:



                            b = b.split(",", 1)[1]



                        raw_images.append(base64.b64decode(b, validate=True))



                    elif isinstance(b, bytes):



                        raw_images.append(b)







                principal = self._gym_principal(body=data)



                if str(data.get("account_id") or principal.account_id) != principal.account_id:



                    raise PermissionError("Evaluation belongs to a different account")



                if path in ("/api/taufa/validate", "/api/taufa/evaluate_unified", "/api/taufa/evaluate", "/api/taufa/ask"):



                    from core.taufa_ai_validator import TaufaAiValidator



                    req_mode = str(data.get("mode") or ("PRODUCTION" if (data.get("is_production") or str(data.get("mode", "")).upper() == "PRODUCTION") else "DEMO")).strip().upper()



                    task_type = str(data.get("task_type") or ("hourly" if str(category).upper() in ("HOURLY", "COLLEGE", "CODING") else "study"))



                    chk_dup = bool(data.get("check_duplicate", True))



                    if req_mode == "PRODUCTION" and data.get("dispatch_submission"):



                        if not raw_images or not data.get("activity_id"):



                            raise ValueError("Select a persisted production activity and its original images")



                        payload = dict(data)



                        payload["request_id"] = str(data.get("request_id") or sub_id or "")



                        payload["attachments"] = [{"filename":str(filenames[i] if i < len(filenames) else f"evidence_{i}.jpg"),



                            "image_base64":base64.b64encode(image).decode("ascii")} for i,image in enumerate(raw_images)]



                        accepted = self._dispatch_hourly_submission(payload,raw_images[0],



                            str(filenames[0] if filenames else "evidence.jpg"),principal.account_id,



                            principal.device_id,"BROWSER" if self.headers.get("Origin","").startswith("chrome-extension://") else "WINDOWS")



                        self._send_json(accepted,status=200 if accepted.get("deduplicated") else 201)



                        return



                    if req_mode not in ("DEMO", "PRODUCTION"):



                        raise ValueError("Choose DEMO or PRODUCTION validation")



                    res = TaufaAiValidator().evaluate_submission(



                        submission_id=sub_id,



                        category=category,



                        description=description,



                        images=raw_images,



                        task_type=task_type,



                        mode=req_mode,



                        check_duplicate=chk_dup,



                        image_filenames=filenames,



                        schedule_context=sched_ctx if isinstance(sched_ctx, dict) else None,



                        taufa_client=None,



                        timeout=60.0,



                        db=self.db if req_mode == "PRODUCTION" else None,



                        engine=self.engine if req_mode == "PRODUCTION" else None,



                        account_id=principal.account_id,



                        activity_id=data.get("activity_id"),



                        categories=data.get("categories") or data.get("activity_categories"),



                    )



                    if isinstance(res, dict):



                        if "result" not in res:



                            res["result"] = dict(res)



                        res["success"] = True



                    self._send_json(res, status=200)



                    return



                self._send_json({"success":False,"status":"PROCESSING_ERROR","recommended_action":"retry",



                    "requires_human_review":False,"error":"Use the shared validation endpoint with supported API authorization."},status=503)



            except Exception as e:



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path in ("/api/ai/config", "/api/ai/profiles"):



            try:



                length = int(self.headers.get("Content-Length", "0"))



                body = json.loads(self.rfile.read(length)) if 0 < length <= 65536 else {}



                profiles_data = body.get("profiles") or []



                if not isinstance(profiles_data, list):



                    self._send_json({"success": False, "error": "profiles must be a list"}, status=400)



                    return



                from core.openai_fallback_validator import OpenAiMultiProfileManager



                mgr = OpenAiMultiProfileManager.get_instance(db=self.db)



                updated_meta = mgr.save_profiles(profiles_data, increment_version=True)



                self._send_json({"success": True, **updated_meta})



            except Exception as e:



                logger.exception("Failed to update /api/ai/config: %s", e)



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path == "/api/ai/probe_profiles":



            try:



                from core.openai_fallback_validator import OpenAiMultiProfileManager



                mgr = OpenAiMultiProfileManager.get_instance(db=self.db)



                probes = mgr.probe_all_profiles(timeout_sec=8.0)



                self._send_json({"success": True, "profiles": probes, "config_version": mgr.get_config_version()})



            except Exception as e:



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path == "/api/evidence/validate":



            try:



                length = int(self.headers.get("Content-Length", "0"))



                body = json.loads(self.rfile.read(length)) if 0 < length <= 25 * 1024 * 1024 else {}



                self._handle_centralized_evidence_validation(body)



            except Exception as e:



                logger.exception("Centralized validation failed: %s", e)



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path == "/api/ai_health/check":



            try:



                length = int(self.headers.get("Content-Length", "0"))



                body = json.loads(self.rfile.read(length)) if 0 < length <= 65536 else {}



                force_flag = bool(body.get("force", False))



                reason = str(body.get("reason") or ("manual_retry" if force_flag else "scheduled_60m"))



                from core.ai_health_coordinator import ai_health_coordinator



                health = ai_health_coordinator.run_health_check(force=force_flag, reason=reason)



                self._send_json({"success": True, **health})



            except Exception as e:



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path == "/api/ai_preflight/check":



            try:



                length = int(self.headers.get("Content-Length", "0"))



                body = json.loads(self.rfile.read(length)) if 0 < length <= 65536 else {}



                window_id = str(body.get("proof_window_id") or body.get("window_id") or body.get("activity_id") or "").strip()



                activity_id = str(body.get("activity_id") or window_id).strip()



                sched_open = str(body.get("scheduled_open_time") or "").strip()



                sched_ver = int(body.get("schedule_version") or 1)



                force_flag = bool(body.get("force", False))



                retry_att = int(body.get("retry_attempt") or 0)







                from core.ai_preflight_service import ai_preflight_service



                ai_preflight_service.db = self.db



                result = ai_preflight_service.execute_preflight(



                    proof_window_id=window_id,



                    activity_id=activity_id,



                    scheduled_open_time=sched_open,



                    schedule_version=sched_ver,



                    force=force_flag,



                    retry_attempt=retry_att,



                )



                self._send_json({"success": True, "preflight": result.to_dict(), **result.to_dict()})



            except Exception as e:



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path in (



            "/api/proof_upload/init",



            "/api/proof_upload/chunk",



            "/api/proof_upload/complete",



            "/api/proof_upload/reconcile",



        ):



            try:



                length = int(self.headers.get("Content-Length", "0"))



                if length > MAX_HOURLY_REQUEST_BYTES:



                    self._send_json({"success": False, "error": "Payload exceeds maximum upload limit"}, status=413)



                    return



                body = json.loads(self.rfile.read(length)) if length > 0 else {}



                from core.resumable_upload_service import resumable_upload_service



                if path == "/api/proof_upload/init":



                    res = resumable_upload_service.init_upload(body)



                elif path == "/api/proof_upload/chunk":



                    res = resumable_upload_service.upload_chunk(body)



                elif path == "/api/proof_upload/complete":



                    async_flag = bool(body.get("async_validation", True))



                    res = resumable_upload_service.complete_upload(body, async_validation=async_flag)



                else:



                    sub_ids = body.get("submission_ids")



                    res = resumable_upload_service.reconcile_submissions(



                        submission_ids=sub_ids if isinstance(sub_ids, list) else None



                    )



                status_code = 200 if res.get("success") else 400



                self._send_json(res, status=status_code)



            except Exception as e:



                self._send_json({"success": False, "error": str(e)}, status=500)



            return







        if path == "/api/browser_extensions/monitor_evidence":



            if not self._is_local_request():



                self._send_json({"success": False, "error": "Local Chrome reports only"}, status=403)



                return



            try:



                length = int(self.headers.get("Content-Length", "0"))



                if not 0 < length <= 4096:



                    raise ValueError("Invalid observation size")



                body = json.loads(self.rfile.read(length))



                if not isinstance(body, dict):



                    raise ValueError("Invalid observation")



                from windows.browser_extension_monitor import get_browser_extension_monitor



                result = get_browser_extension_monitor(db=self.db).submit_observation(



                    body, self.headers.get("Origin", ""), self.headers.get("X-StrictRoutines-Monitor", ""))



                self._send_json(result, status=200 if result.get("success") else 403)



            except (ValueError, TypeError):



                self._send_json({"success": False, "error": "Invalid observation"}, status=400)



            return



        if path in (



            "/api/browser_extensions/setup",



            "/api/browser_extensions/monitor_prepare",



            "/api/browser_extensions/monitor_manager",



            "/api/browser_extensions/monitor_folder",



            "/api/browser_extensions/open_folder",



            "/api/browser_extensions/lock",



            "/api/browser_extensions/unlock",



            "/api/browser_extensions/uninstall_challenge/verify",



            "/api/browser_extensions/uninstall_challenge/confirm",



            "/api/browser_extensions/uninstall_challenge/event",



            "/api/browser_extensions/uninstall_challenge/resume",



            "/api/browser_extensions/set_grace_period"



        ):



            self._handle_browser_setup(path)



            return



        request_received_at = now_ist()



        try:



            length = int(self.headers.get("Content-Length", "0"))



        except ValueError:



            self._send_json({"success": False, "error": "Invalid request size.", "code": "INVALID_CONTENT_LENGTH"}, status=400)



            return



        content_type = self.headers.get("Content-Type", "")



        if path in ("/api/strictbot/chat", "/api/strictbot/action") and length > 32 * 1024:



            # Consume moderately oversized bodies so Windows receives the JSON error



            # instead of a TCP reset caused by unread request bytes on close.



            if length <= 256 * 1024:



                previous_timeout = self.connection.gettimeout()



                try:



                    self.connection.settimeout(3)



                    self.rfile.read(length)



                except (OSError, TimeoutError):



                    pass



                finally:



                    self.connection.settimeout(previous_timeout)



            self._send_json({"success": False, "code": "REQUEST_TOO_LARGE", "error": "StrictBot request exceeds 32 KB."}, status=413)



            return



        if length < 0:



            self._send_json({"success": False, "error": "Invalid request size.", "code": "INVALID_CONTENT_LENGTH"}, status=400)



            return



        if path in ("/api/hourly/submit", "/api/hourly/submit_proof", "/api/activity/proof", "/api/device/sync_events", "/api/demo/taufa_validation") and length > MAX_HOURLY_REQUEST_BYTES:



            self._send_json({



                "success": False,



                "error": f"Request exceeds the {MAX_HOURLY_REQUEST_BYTES // (1024 * 1024)} MB upload limit.",



                "code": "REQUEST_TOO_LARGE",



            }, status=413)



            return



        raw_body_bytes = self.rfile.read(length) if length > 0 else b""







        if path in ("/api/hourly/submit", "/api/hourly/submit_proof", "/api/activity/proof"):



            if path == "/api/activity/proof":



                self._handle_hourly_submission_request(raw_body_bytes, content_type, received_at=request_received_at, activity_proof=True)



                return



            self._handle_hourly_submission_request(raw_body_bytes, content_type, received_at=request_received_at)



            return







        if path == "/api/demo/taufa_validation":



            self._handle_demo_taufa_validation(raw_body_bytes, content_type)



            return







        if path == "/api/releases/inspect":



            if not self._authorize_admin_request():



                return



            self._handle_releases_inspect(raw_body_bytes, content_type)



            return







        try:



            body_raw = raw_body_bytes.decode("utf-8") if raw_body_bytes else "{}"



            body = json.loads(body_raw) if body_raw else {}



        except (UnicodeDecodeError, json.JSONDecodeError):



            self._send_json({"success": False, "error": "Request body must contain valid JSON.", "code": "INVALID_JSON"}, status=400)



            return



        if not isinstance(body, dict):



            self._send_json({"success": False, "error": "Request body must be a JSON object.", "code": "INVALID_JSON"}, status=400)



            return







        if path.startswith('/api/strict_session/preparation/'):



            if len(body_raw)>8192:



                self._send_json({'success':False,'error':'Preparation report too large'},status=413);return



            self._strict_preparation_action(path,body);return







        if path in (



            '/api/sensitive_page_protection/event',



            '/api/restriction/sensitive_page_protection/event',



            '/api/sensitive_page_protection/config',



            '/api/restriction/sensitive_page_protection/config',



            '/api/sensitive_page_protection/reconcile',



            '/api/restriction/sensitive_page_protection/reconcile',



            '/api/sensitive_page_protection/dismiss',



            '/api/restriction/sensitive_page_protection/dismiss',



        ):



            if not self._is_local_request():



                self._send_json({'success': False, 'error': 'Sensitive page protection endpoints accept local requests only.'}, status=403)



                return



            from windows.strict_sensitive_page_protection import SensitivePageProtectionManager



            spp = SensitivePageProtectionManager.get_instance()



            if path.endswith('/config'):



                cfg = spp.update_config(



                    add_domains=body.get('add_domains'),



                    remove_domains=body.get('remove_domains'),



                    add_recorders=body.get('add_recorders'),



                    remove_recorders=body.get('remove_recorders'),



                )



                self._send_json({'success': True, **cfg, 'status': spp.get_status()})



                return



            if path.endswith('/reconcile'):



                sess_id = body.get('session_id') or (self.strict_session_manager.session_id if self.strict_session_manager else None)



                result = spp.reconcile_extension_state(



                    active_tabs=body.get('active_tabs') if isinstance(body.get('active_tabs'), list) else [],



                    browser_captures=body.get('browser_captures') if isinstance(body.get('browser_captures'), list) else None,



                    browser=str(body.get('browser') or 'browser'),



                    session_id=sess_id,



                )



                self._send_json(result)



                return



            if path.endswith('/dismiss'):



                result = spp.dismiss_persistent_alert(



                    admin_override=bool(body.get('admin_override', False)),



                    reason=str(body.get('reason') or ''),



                )



                self._send_json(result)



                return



            ev_type = str(body.get('event_type') or 'SENSITIVE_PAGE_DETECTED').upper().strip()



            sess_id = body.get('session_id') or (self.strict_session_manager.session_id if self.strict_session_manager else None)



            if ev_type in ('SENSITIVE_PAGE_CLOSED', 'TAB_CLOSED', 'SENSITIVE_PAGE_LEFT'):



                result = spp.handle_sensitive_page_closed_or_navigated(



                    tab_id=body.get('tab_id', 'active'),



                    browser=str(body.get('browser') or 'browser'),



                    new_non_sensitive_domain=str(body.get('matched_domain') or body.get('domain') or ''),



                    reason=ev_type,



                )



            elif ev_type in ('BROWSER_CAPTURE_STARTED', 'BROWSER_CAPTURE_STOPPED', 'BROWSER_CAPTURE_STATE_CHANGED'):



                is_act = True if ev_type == 'BROWSER_CAPTURE_STARTED' else (



                    False if ev_type == 'BROWSER_CAPTURE_STOPPED' else bool(body.get('is_active', True))



                )



                result = spp.handle_browser_capture_event(



                    capture_id=str(body.get('capture_id') or f"{body.get('source_type', 'SCREEN_SHARING')}_{body.get('tab_id', 'global')}"),



                    source_type=str(body.get('source_type') or 'SCREEN_SHARING'),



                    is_active=is_act,



                    tab_id=body.get('tab_id'),



                    window_id=body.get('window_id'),



                    browser=str(body.get('browser') or 'browser'),



                    display_name=body.get('display_name'),



                    event_id=body.get('event_id'),



                    timestamp=body.get('timestamp'),



                )



            elif ev_type == 'RECONCILE_EXTENSION_STATE':



                result = spp.reconcile_extension_state(



                    active_tabs=body.get('active_tabs') if isinstance(body.get('active_tabs'), list) else [],



                    browser_captures=body.get('browser_captures') if isinstance(body.get('browser_captures'), list) else None,



                    browser=str(body.get('browser') or 'browser'),



                    session_id=sess_id,



                )



            elif ev_type == 'DISMISS_ALERT':



                result = spp.dismiss_persistent_alert(



                    admin_override=bool(body.get('admin_override', False)),



                    reason=str(body.get('reason') or ''),



                )



            elif ev_type == 'WINDOWS_MONITOR_CONNECTION_STATE':



                result = spp.set_windows_monitor_connection_state(bool(body.get('connected', True)))



            elif ev_type in ('TAB_NAVIGATION_UPDATE', 'CLASSIFY_AND_HANDLE') or (body.get('url_or_origin') and not body.get('category')):



                result = spp.classify_and_handle_navigation(



                    url_or_origin=str(body.get('url_or_origin') or body.get('matched_domain') or ''),



                    tab_id=body.get('tab_id', 'active'),



                    window_id=body.get('window_id'),



                    browser=str(body.get('browser') or 'browser'),



                    session_id=sess_id,



                    frame_origins=body.get('frame_origins') if isinstance(body.get('frame_origins'), list) else None,



                    redirect_url=body.get('redirect_url'),



                    event_id=body.get('event_id'),



                )



            else:



                result = spp.handle_sensitive_page_detected(



                    category=str(body.get('category') or 'PAYMENT_CHECKOUT'),



                    matched_domain=str(body.get('matched_domain') or body.get('origin') or body.get('url_or_origin') or ''),



                    tab_id=body.get('tab_id', 'active'),



                    window_id=body.get('window_id'),



                    browser=str(body.get('browser') or 'browser'),



                    session_id=sess_id,



                    sub_type=body.get('sub_type'),



                    detection_source=str(body.get('detection_source') or 'BROWSER_EXTENSION'),



                    event_id=body.get('event_id'),



                    timestamp=body.get('timestamp'),



                )



            if result.get('notification_emitted') or result.get('capture_risk_active'):



                try:



                    EventBus.get_instance().publish('SENSITIVE_PAGE_DETECTED', {



                        'event_type': 'SENSITIVE_PAGE_CAPTURE_RISK' if result.get('capture_risk_active') else 'SENSITIVE_PAGE_DETECTED',



                        'title': result.get('alert_title'),



                        'message': result.get('alert_message'),



                        'category': result.get('current_category'),



                        'matched_domain': result.get('matched_domain'),



                        'cooldown_remaining_seconds': result.get('cooldown_remaining_seconds'),



                        'cooldown_formatted': result.get('cooldown_formatted'),



                        'external_recorders': result.get('external_recorders', []),



                        'external_recorder_warning': result.get('external_recorder_warning'),



                        'capture_risk_active': result.get('capture_risk_active', False),



                    })



                except Exception:



                    pass



            self._send_json(result)



            return







        if path == '/api/gym/routine':



            self._handle_gym_routine(body=body)



            return







        if path.startswith('/api/exit/'):



            self._handle_exit_check(path, body)



            return







        if path in ('/api/planning/college','/api/gym/decision','/api/gym_mode/activate','/api/gym_mode/reconcile_offline','/api/gym_mode/deactivate','/api/gym_mode/reconcile'):



            self._handle_gym_request(path,body)



            return



        if path in ('/api/rewards/youtube/select','/api/rewards/youtube/start','/api/rewards/youtube/stop','/api/rewards/youtube/expire','/api/rewards/youtube/transfer', '/api/rewards/entertainment/start', '/api/rewards/entertainment/stop', '/api/rewards/entertainment/activate'):



            self._handle_youtube_reward(path, body)



            return







        if path.startswith("/api/rewards/early_sleep/"):



            self._handle_early_sleep_reward(path, body=body, query=qs)



            return







        if path == "/api/daily_productivity/evaluate":



            from core.daily_productivity_engine import DailyProductivityEngine



            date = (body or {}).get("date") or now_ist().strftime("%Y-%m-%d")



            engine = DailyProductivityEngine(self.db)



            res = engine.evaluate_daily_productivity(date_str=date, force=True)



            self._send_json(res)



            return







        if path in ('/api/youtube/educational/request', '/api/youtube/educational/heartbeat', '/api/youtube/educational/start', '/api/youtube/educational/metadata'):



            self._handle_youtube_educational(path, body)



            return







        if path.startswith('/api/night/'):



            self._handle_night_request(path, body=body, query=qs)



            return







        if path in ("/api/strictbot/chat", "/api/strictbot/action"):



            self._handle_strictbot(path, body=body, received_at=request_received_at)



            return







        auth_client_key = str(self.client_address[0] if self.client_address else "unknown")



        if path in ("/api/admin/login", "/api/admin/verify_pin"):



            cooldown = self.db.admin_auth_lockout_remaining(auth_client_key)



            if cooldown > 0:



                self._send_json({"success": False, "valid": False, "error": f"Too many failed attempts. Locked out for {cooldown}s.", "locked_out": True, "cooldown_seconds": cooldown}, status=429)



                return







        if path.startswith("/api/admin/") and path not in ("/api/admin/login", "/api/admin/verify_pin"):



            if not self._authorize_admin_request(body=body, allow_initial_setup=path in ("/api/admin/change_pin", "/api/admin/logout")):



                return



        elif path in (



            "/api/releases/publish", "/api/releases/toggle_active",



            "/api/schedule/reschedule", "/api/schedule/policy",



            "/api/youtube/enable_today",



            "/api/settings/update", "/api/score/correct", "/api/pairing/approve",



            "/api/pairing/revoke", "/api/strict_session/end",



            "/api/strict_session/policy", "/api/releases/notify",



            "/api/privilege/youtube/mode", "/api/device/apps/toggle",



            "/api/device/command/send", "/api/device/unpair", "/api/device/revoke",



            "/api/device/clear_all", "/api/device/reconnect",



        ):



            if not self._authorize_admin_request(body=body):



                return



        elif path == "/api/strict_session/start" and "policy" in body:



            if not self._authorize_admin_request(body=body):



                return







        admin_session = getattr(self, "_authenticated_admin", None)



        if admin_session:



            admin_name = str(admin_session.get("username") or admin_session.get("admin_id") or "Administrator")



            body["admin_id"] = str(admin_session.get("admin_id") or admin_name)



            body["admin_actor"] = admin_name



            body["admin_user"] = admin_name



            body["created_by"] = admin_name







        if path == "/api/device/sync_events":



            self._handle_device_sync_events_request(body, received_at=request_received_at)



            return







        if path in ("/api/wakeup/diagnostics/start", "/api/diagnostics/wakeup/start"):



            from core.wakeup_diagnostics import WakeUpDiagnosticsService, DiagnosticsError



            try:



                p = self._diagnostic_principal(body=body)



                svc = WakeUpDiagnosticsService(self.db)



                req_id = str(body.get("request_id") or f"req_{uuid.uuid4().hex[:12]}")



                res = svc.start(p, request_id=req_id)



                self._send_json(res)



            except DiagnosticsError as exc:



                self._send_json({"success": False, "code": exc.code, "error": str(exc)}, status=exc.status)



            except Exception as exc:



                self._send_json({"success": False, "code": "ERROR", "error": str(exc)}, status=500)



            return







        if path in ("/api/wakeup/diagnostics/event", "/api/diagnostics/wakeup/event"):



            from core.wakeup_diagnostics import WakeUpDiagnosticsService, DiagnosticsError



            try:



                p = self._diagnostic_principal(body=body)



                svc = WakeUpDiagnosticsService(self.db)



                diag_id = str(body.get("diag_session_id") or body.get("session_id") or "")



                event_id = str(body.get("event_id") or f"evt_{uuid.uuid4().hex[:12]}")



                check = str(body.get("check") or body.get("check_key") or "")



                measurements = body.get("measurements")



                unavailable_reason = str(body.get("unavailable_reason") or "")



                error = str(body.get("error") or "")



                res = svc.record_event(p, diag_session_id=diag_id, event_id=event_id, check=check,



                                       measurements=measurements, unavailable_reason=unavailable_reason, error=error)



                self._send_json(res)



            except DiagnosticsError as exc:



                self._send_json({"success": False, "code": exc.code, "error": str(exc)}, status=exc.status)



            except Exception as exc:



                self._send_json({"success": False, "code": "ERROR", "error": str(exc)}, status=500)



            return







        if path in ("/api/wakeup/diagnostics/complete", "/api/diagnostics/wakeup/complete"):



            from core.wakeup_diagnostics import WakeUpDiagnosticsService, DiagnosticsError



            try:



                p = self._diagnostic_principal(body=body)



                svc = WakeUpDiagnosticsService(self.db)



                diag_id = str(body.get("diag_session_id") or body.get("session_id") or "")



                res = svc.complete(p, diag_session_id=diag_id)



                self._send_json(res)



            except DiagnosticsError as exc:



                self._send_json({"success": False, "code": exc.code, "error": str(exc)}, status=exc.status)



            except Exception as exc:



                self._send_json({"success": False, "code": "ERROR", "error": str(exc)}, status=500)



            return







        if path in ("/api/wakeup/diagnostics/report", "/api/diagnostics/wakeup/report"):



            from core.wakeup_diagnostics import WakeUpDiagnosticsService, DiagnosticsError



            try:



                p = self._diagnostic_principal(body=body)



                svc = WakeUpDiagnosticsService(self.db)



                diag_id = str(body.get("diag_session_id") or body.get("session_id") or "")



                res = svc.report(p, diag_session_id=diag_id)



                self._send_json(res)



            except DiagnosticsError as exc:



                self._send_json({"success": False, "code": exc.code, "error": str(exc)}, status=exc.status)



            except Exception as exc:



                self._send_json({"success": False, "code": "ERROR", "error": str(exc)}, status=500)



            return







        if path.startswith("/api/wakeup/") or path.startswith("/api/hourly/") or path in ("/api/attendance", "/api/schedule"):



            from core.wakeup_diagnostics import is_diagnostic_payload



            if is_diagnostic_payload(body):



                self._send_json({



                    "success": False,



                    "code": "TEST_MODE_REJECTED",



                    "error": "Diagnostic and test payloads cannot be submitted to production attendance endpoints. Use /api/wakeup/diagnostics/*."



                }, status=400)



                return















        date = body.get("date") or datetime.now().strftime("%Y-%m-%d")



        is_dev = bool(body.get("is_dev_mode", False))



        device_id = body.get("device_id", "device_client")







        try:



            # ── Official Event Booking & Exit Routes (Features A, B, C) ──



            if path == "/api/events/extract":



                from core.official_event_engine import extract_from_multiple_files



                files_data = body.get("files") or []



                if not files_data and "data_base64" in body:



                    files_data = [{"data_base64": body["data_base64"], "filename": body.get("filename", "screenshot.jpg")}]



                file_tuples = []



                for f_item in files_data:



                    raw_b64 = f_item.get("data_base64") or ""



                    try:



                        raw = base64.b64decode(raw_b64)



                    except Exception:



                        raw = b""



                    fname = f_item.get("filename", "uploaded_doc")



                    if raw:



                        file_tuples.append((raw, fname))



                if not file_tuples:



                    self._send_json({"success": False, "error": "No valid file data provided for extraction."}, status=400)



                    return



                res = extract_from_multiple_files(file_tuples)



                self._send_json({"success": True, **res})



                return



            elif path == "/api/events/book":



                try:



                    booking = self.db.create_official_event_booking(body, device_id=device_id)



                    self._send_json({"success": True, "booking": booking})



                except Exception as e:



                    self._send_json({"success": False, "error": str(e)}, status=400)



                return



            elif path == "/api/events/update":



                try:



                    booking = self.db.update_official_event_booking(body.get("event_id", ""), body, device_id=device_id)



                    self._send_json({"success": True, "booking": booking})



                except Exception as e:



                    self._send_json({"success": False, "error": str(e)}, status=400)



                return



            elif path == "/api/events/cancel":



                ok = self.db.cancel_official_event_booking(body.get("event_id", ""), reason=body.get("reason", ""), device_id=device_id)



                self._send_json({"success": ok})



                return



            elif path == "/api/events/authorize_exit":



                self._send_json({"success": False, "code": "EXIT_CHALLENGE_REQUIRED",



                    "error": "Use Exit: PIN plus 24/25 correct within 25 minutes is required."}, status=403)



                return







            # ── Unexpected Exit Recovery Routes (Feature E) ───────────



            elif path == "/api/recovery/start":



                sess_id = body.get("session_id", "") or self.get_current_process_session_id()



                mode = body.get("mode", "STANDARD")



                chall = self.db.create_or_get_recovery_challenge(sess_id, mode=mode)



                self._send_json({"success": True, "challenge": chall})



                return



            elif path == "/api/recovery/answer":



                cid = body.get("challenge_id", "")



                qid = body.get("question_id", "")



                ans = body.get("answer", "")



                res = self.db.save_recovery_challenge_answer(cid, qid, ans)



                self._send_json({"success": True, "challenge": res})



                return



            elif path == "/api/recovery/complete":



                cid = body.get("challenge_id", "")



                res = self.db.complete_recovery_challenge(cid)



                self._send_json({"success": True, "result": res})



                return



            elif path == "/api/pc/shutdown_event":



                shutdown_ts = body.get("shutdown_timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")



                dev_id = body.get("device_id", "windows_lok_pc")



                ev_id = body.get("event_id") or f"shut_{uuid.uuid4().hex[:12]}"



                res = self.db.record_pc_shutdown_event(



                    event_id=ev_id,



                    shutdown_timestamp=shutdown_ts,



                    device_id=dev_id,



                    date=body.get("date"),



                    time_hhmmss=body.get("time_hhmmss"),



                    evidence_source=body.get("evidence_source", "WIN32_API"),



                    account_id=body.get("account_id", "user_lok"),



                )



                self._send_json({"success": True, **res})



                return



            elif path == "/api/pc/shutdown_attendance":



                p_dev_id = body.get("phone_device_id", "android_lok_phone")



                p_date = body.get("date") or datetime.now().strftime("%Y-%m-%d")



                res = self.db.record_pc_shutdown_phone_attendance(



                    date=p_date,



                    phone_device_id=p_dev_id,



                    attendance_timestamp=body.get("attendance_timestamp"),



                    account_id=body.get("account_id", "user_lok"),



                    shutdown_event_id=body.get("shutdown_event_id"),



                )



                self._send_json(res)



                return



            elif path == "/api/pc/shutdown_photo":



                p_date = body.get("date") or datetime.now().strftime("%Y-%m-%d")



                img_b64 = body.get("image_base64") or body.get("file_base64")



                if not img_b64:



                    self._send_json({"success": False, "error": "Image data is required.", "code": "MISSING_IMAGE"}, status=400)



                    return



                clean_b64 = img_b64.split(",")[-1]



                raw_bytes = base64.b64decode(clean_b64)



                dev_id = body.get("phone_device_id", "android_lok_phone")



                sub_id = f"sub_pc_shut_{p_date}_{uuid.uuid4().hex[:8]}"







                ev_info = self.engine.store_evidence_file(



                    date=p_date,



                    record_type="PC_SHUTDOWN",



                    related_record_id=f"PC_SHUTDOWN_{p_date}",



                    device_id=dev_id,



                    file_bytes=raw_bytes,



                    filename_hint="shutdown_display.jpg",



                )



                ev_id = ev_info["evidence_id"]



                file_path = ev_info["file_path"]







                now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")



                with self.db._connect() as conn:



                    conn.execute(



                        """



                        INSERT OR REPLACE INTO proof_reviews (



                            review_id, evidence_id, user_id, date, task_code,



                            status, decision, submitted_at, revision_number



                        ) VALUES (?, ?, ?, ?, ?, 'NEEDS_REVIEW', 'PENDING', ?, 1)



                        """,



                        (



                            f"rev_{ev_id}", ev_id, "user_lok", p_date, "PC_SHUTDOWN",



                            now_iso



                        )



                    )







                self.db.record_pc_shutdown_photo_evidence(



                    date=p_date,



                    evidence_id=ev_id,



                    account_id=body.get("account_id", "user_lok"),



                    shutdown_event_id=body.get("shutdown_event_id"),



                )







                from core.ai_robot_admin import StrictBotAIReviewer



                ai_decision = StrictBotAIReviewer.execute_review(



                    submission_id=sub_id,



                    activity_id=f"PC_SHUTDOWN_{p_date}",



                    task_code="PC_SHUTDOWN",



                    description="PC workstation display turned off / shutdown proof",



                    primary_activity="PC_SHUTDOWN",



                    submitted_at_iso=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),



                    device_id=body.get("phone_device_id", "android_lok_phone"),



                    platform="ANDROID",



                    image_bytes=raw_bytes,



                    file_path=file_path,



                    configured_points=self.db.get_settings("user_lok").pc_shutdown_bonus_points,



                    evidence_id=ev_id,



                )







                settings = self.db.get_settings("user_lok")



                award_res = None



                if not settings.pc_shutdown_approval_required and ai_decision.get("decision") == "PROVISIONAL_APPROVE":



                    award_res = self.db.evaluate_and_award_pc_shutdown_bonus(



                        p_date, admin_id="STRICTBOT_AI", decision="APPROVE",



                        reason="Automated AI review confirmed workstation powered off."



                    )







                self._send_json({



                    "success": True,



                    "evidence_id": ev_id,



                    "submission_id": sub_id,



                    "ai_decision": ai_decision,



                    "award_result": award_res,



                    "status": "APPROVED" if (award_res and award_res.get("success")) else "NEEDS_REVIEW"



                })



                return



            elif path == "/api/admin/pc_shutdown_review":



                if not self._authorize_admin_request(body=body):



                    return



                ev_id_or_date = body.get("event_id") or body.get("date")



                dec = body.get("decision", "APPROVE")



                rsn = body.get("reason", "Admin reviewed PC shutdown bonus")



                admin_user = body.get("admin_id", "Administrator")



                res = self.db.evaluate_and_award_pc_shutdown_bonus(



                    event_id_or_date=ev_id_or_date,



                    admin_id=admin_user,



                    decision=dec,



                    reason=rsn,



                )



                self._send_json(res)



                return



            elif path in ("/api/device/submission_events/ack", "/api/hourly/submission_events/ack"):



                from core.hourly_submission_service import HourlySubmissionService



                if path == "/api/device/submission_events/ack":



                    ack_device_id = str(body.get("device_id") or "")



                    account_id = self._get_authenticated_device_account(ack_device_id, str(body.get("device_token") or ""))



                else:



                    account_id, ack_device_id = self._get_windows_submission_identity()



                event_ids = body.get("event_ids", [])



                if not isinstance(event_ids, list):



                    self._send_json({"success": False, "error": "event_ids must be a list."}, status=400)



                    return



                ack_count = HourlySubmissionService(self.db, self.engine).acknowledge_events(



                    account_id, ack_device_id, [str(item) for item in event_ids]



                )



                self._send_json({"success": True, "acknowledged": ack_count})



            elif path == "/api/wakeup/trigger":



                res = self.engine.trigger_wakeup_alarm(date, device_id=device_id, is_dev_mode=is_dev)



                self._send_json(res)



            elif path == "/api/wakeup/snooze":



                res = self.engine.record_snooze(date, device_id=device_id, is_dev_mode=is_dev)



                self._send_json(res)



            elif path == "/api/wakeup/start_task":



                res = self.engine.start_wakeup_task(date, device_id=device_id, is_dev_mode=is_dev)



                self._send_json(res)



            elif path == "/api/wakeup/earbuds_config":



                res = self.db.set_earbuds_config(



                    device_name=str(body.get("preferred_earbuds_name") or body.get("device_name") or ""),



                    device_address=str(body.get("preferred_earbuds_address") or body.get("device_address") or ""),



                )



                self._send_json({"success": True, **res})



            elif path == "/api/wakeup/step1_camera":



                res = self.engine.verify_wakeup_step1_camera(



                    date=date,



                    device_id=device_id,



                    image_base64=body.get("image_base64"),



                    image_path=body.get("image_path"),



                    is_dev_mode=is_dev,



                    lighting_score=body.get("lighting_score"),



                    face_detected=body.get("face_detected"),



                    eyes_detected=body.get("eyes_detected"),



                    face_confidence=body.get("face_confidence") or body.get("confidence"),



                    liveness_duration_sec=body.get("liveness_duration_sec") or body.get("duration_sec"),



                    valid_observation_ratio=body.get("valid_observation_ratio"),



                    head_movement_ok=body.get("head_movement_ok"),



                    liveness_status=body.get("liveness_status"),



                )



                st = 200 if res.get("camera_verified") else 422



                self._send_json(res, status=st)



            elif path == "/api/wakeup/step2_bluetooth":



                res = self.engine.verify_wakeup_step2_bluetooth(



                    date=date,



                    connected_audio_devices=body.get("connected_devices", []),



                    device_id=device_id,



                    is_dev_mode=is_dev,



                    preferred_device_name=body.get("preferred_device_name"),



                    active_route_verified=bool(body.get("active_route_verified", True)),



                )



                st = 200 if res.get("bluetooth_verified") else 422



                self._send_json(res, status=st)



            elif path in ("/api/wakeup/step3_motivation", "/api/wakeup/step3_track", "/api/wakeup/complete"):



                if path == "/api/wakeup/complete" and "listened_duration_sec" not in body and "played_duration_sec" not in body:



                    res = self.engine.finalize_wakeup_session(



                        date=date,



                        completed_at=body.get("completed_at") or body.get("timestamp"),



                        device_id=device_id,



                        is_dev_mode=is_dev,



                    )



                    st = 200 if (res.get("wakeup_completed") or res.get("demo_completed") or res.get("prerequisite_completed")) else 422



                    self._send_json(res, status=st)



                    return



                from core.wakeup_attempt_validation import configured_track_manifest



                configured_duration = configured_track_manifest()["duration_sec"]



                requested_duration = float(body.get("track_duration_sec", configured_duration))



                if abs(requested_duration - configured_duration) > 0.5:



                    self._send_json({



                        "motivation_verified": False,



                        "track_completed": False,



                        "points_awarded": 0,



                        "error": "Track duration does not match the configured full track.",



                    }, status=422)



                    return



                listened_val = body.get("listened_duration_sec")



                if listened_val is None:



                    listened_val = body.get("played_duration_sec", configured_duration)



                res = self.engine.verify_wakeup_step3_motivation_track(



                    date=date,



                    track_duration_sec=configured_duration,



                    listened_duration_sec=float(listened_val),



                    track_started_at=body.get("track_started_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),



                    interruption_count=int(body.get("interruption_count", 0)),



                    device_id=device_id,



                    is_dev_mode=is_dev,



                    volume_percent=body.get("volume_percent") or body.get("volume_pct"),



                    audio_route=body.get("audio_route"),



                    headset_connected=body.get("headset_connected"),



                    volume_ok=body.get("volume_ok"),



                    seek_attempts=body.get("seek_attempts"),



                )



                self._send_json(res)



            elif path == "/api/wakeup/track_state":



                res = self.engine.pause_or_resume_motivation_track(



                    date=date,



                    action=body.get("action", "PAUSE"),



                    listened_duration_sec=float(body.get("listened_duration_sec", 0.0)),



                    track_duration_sec=float(body.get("track_duration_sec", 8.0)),



                    device_id=device_id,



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/wakeup/step_update":



                from core.wakeup_state_machine import WakeUpStateMachine, WakeUpState



                sm = WakeUpStateMachine(db=self.db, date_str=date)



                new_st_str = body.get("state")



                if new_st_str and new_st_str in [s.value for s in WakeUpState]:



                    sm.update_state(WakeUpState(new_st_str))



                # Also update Bluetooth or volume limit if supplied



                if "volume_limit_ok" in body:



                    with self.db._connect() as conn:



                        conn.execute("UPDATE wake_up_sessions SET volume_limit_ok = ? WHERE date = ? AND is_dev_mode = 0",



                                     (1 if body["volume_limit_ok"] else 0, date))



                self._send_json(sm.get_checklist_state())



            elif path == "/api/wakeup/playback/progress":



                from core.wakeup_state_machine import WakeUpStateMachine, WakeUpPlaybackSession



                sm = WakeUpStateMachine(db=self.db, date_str=date)



                pos = float(body.get("position_sec", 0.0))



                from core.wakeup_attempt_validation import configured_track_manifest



                configured_duration = configured_track_manifest()["duration_sec"]



                dur = float(body.get("duration_sec", configured_duration))



                if abs(dur - configured_duration) > 0.001:



                    self._send_json({"error": "Track duration does not match the configured full track."}, status=422)



                    return



                vol = float(body.get("volume_pct", 50.0))



                route = str(body.get("audio_route", ""))



                headset = str(body.get("headset_name", ""))



                action = body.get("action", "PROGRESS")  # START, PROGRESS, PAUSE, RESUME, DISCONNECT







                if action == "START":



                    headset = headset.strip() or "System Audio Output (Optional)"



                    route = route.strip() or "DEFAULT_AUDIO_OUTPUT"







                # Load existing or new playback session



                sess_dict = sm._load_session_record()



                pb_json = sess_dict.get("playback_session_json") or "{}"



                pb_sess = WakeUpPlaybackSession(



                    session_id=sess_dict.get("session_id", f"wakeup_{date}_0"),



                    track_name=body.get("track_name", "Morning Discipline Audio"),



                    track_duration_sec=dur,



                    max_volume_pct=60.0,



                )



                try:



                    old_pb = json.loads(pb_json)



                    pb_sess.completed_duration_sec = float(old_pb.get("completed_duration_sec", 0.0))



                    pb_sess.pause_duration_sec = float(old_pb.get("pause_duration_sec", 0.0))



                    pb_sess.seek_attempts = int(old_pb.get("seek_attempts", 0))



                    pb_sess.is_playing = bool(old_pb.get("is_playing", False))



                    pb_sess.completed = bool(old_pb.get("completed", False))



                except Exception:



                    pass







                if action == "START":



                    pb_sess.track_name = body.get("track_name") or self.db.get_settings().motivation_track_name



                    pb_sess.start(headset_name=headset, audio_route=route, initial_volume_pct=vol)



                elif action == "PAUSE":



                    pb_sess.pause()



                elif action == "RESUME":



                    pb_sess.resume()



                elif action == "DISCONNECT":



                    pb_sess.on_headset_disconnected()



                else:



                    pb_sess.update_progress(pos, volume_pct=vol, audio_route=route)







                updated_pb = pb_sess.to_dict()



                vol_ok = 1 if updated_pb["volume_ok"] else 0



                with self.db._connect() as conn:



                    conn.execute(



                        "UPDATE wake_up_sessions SET playback_session_json = ?, volume_limit_ok = ? WHERE date = ? AND is_dev_mode = 0",



                        (json.dumps(updated_pb), vol_ok, date),



                    )







                # If track reached 100% genuine completion, complete step 3



                if updated_pb["completed"] and not sess_dict.get("motivation_verified"):



                    try:



                        self.engine.verify_wakeup_step3_motivation_track(



                            date=date,



                            track_duration_sec=dur,



                            listened_duration_sec=updated_pb["completed_duration_sec"],



                            track_started_at=pb_sess.started_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),



                            interruption_count=pb_sess.seek_attempts,



                            device_id=device_id,



                            is_dev_mode=is_dev,



                        )



                    except Exception:



                        pass







                self._send_json({"playback": updated_pb, "checklist": sm.get_checklist_state()})



            elif path == "/api/bath/complete":



                res = self.engine.complete_bath(



                    date=date,



                    completed_at=body.get("completed_at"),



                    verification_mode=body.get("verification_mode", "MANUAL"),



                    device_id=device_id,



                    image_base64=body.get("image_base64"),



                    notes=body.get("notes", ""),



                    is_dev_mode=is_dev,



                )



                try:



                    from core.time_window_engine import TimeWindowEngine



                    twe = TimeWindowEngine(self.db)



                    for a in twe.ensure_activities_in_db(date):



                        if a.get("type") == "BATH":



                            twe.record_activity_verification(a["activity_id"], True, 0)



                except Exception:



                    pass



                self._send_json(res)



            elif path == "/api/bath/cancel":



                from core.daily_college_decision import DailyCollegeDecision



                dcd = DailyCollegeDecision(self.db)



                account = body.get("account_id") or "user_lok"



                act_id = body.get("activity_id")



                res = dcd.cancel_bath(account=account, date=date, activity_id=act_id)



                self._send_json(res)



            elif path == "/api/bath/missed":



                res = self.engine.mark_bath_missed(date=date, device_id=device_id, is_dev_mode=is_dev, activity_id=body.get("activity_id"))



                self._send_json(res)



            elif path == "/api/hourly/missed":



                now_h = min(22, max(10, datetime.now().hour))



                hour_slot = body.get("hour_slot") or f"{now_h - 1:02d}:00-{now_h:02d}:00"



                res = self.engine.mark_hourly_missed(



                    date=date,



                    hour_slot=hour_slot,



                    device_id=device_id,



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/admin/proof/review":



                if not self._authorize_admin_request(body=body):



                    return



                evidence_id = body.get("evidence_id")



                admin_id = getattr(self, "_authenticated_admin", {}).get("admin_id") or body.get("admin_id", "admin_user")



                action = (body.get("action") or "APPROVE").upper()



                raw_points = body.get("points") if body.get("points") is not None else body.get("proposed_points", 8)



                points = int(raw_points)



                reason_note = body.get("reason_note", f"Admin {action.lower()} action")



                act_id = body.get("activity_id")



                if not act_id and evidence_id:



                    with self.db._connect() as conn:



                        row = conn.execute("SELECT activity_id FROM scheduled_activities WHERE evidence_ids_json LIKE ? LIMIT 1", (f'%{evidence_id}%',)).fetchone()



                        if row:



                            act_id = row["activity_id"]







                if not evidence_id:



                    self._send_json({"success": False, "error": "evidence_id is required"}, status=400)



                    return







                res = {}



                from core.time_window_engine import TimeWindowEngine



                twe = TimeWindowEngine(self.db)







                if action in ("APPROVE", "VERIFY"):



                    try:



                        res = self.engine.apply_proof_approval(



                            evidence_id=evidence_id,



                            admin_id=admin_id,



                            points=points,



                            reason=reason_note,



                        )



                        res["success"] = True



                        if act_id:



                            twe.record_activity_verification(act_id, True, points, evidence_id=evidence_id)



                    except Exception as e:



                        res = {"success": False, "error": str(e)}



                elif action in ("REJECT", "DENY"):



                    try:



                        res = self.db.reject_proof(evidence_id, admin_id=admin_id, reason_category="OTHER", reason_note=reason_note)



                        res["success"] = True



                        if act_id:



                            twe.record_activity_verification(act_id, False, 0)



                    except Exception as e:



                        res = {"success": False, "error": str(e)}



                elif action == "OVERRIDE":



                    res = self.engine.apply_proof_approval(



                        evidence_id=evidence_id,



                        admin_id=admin_id,



                        points=points,



                        reason=f"[Admin Override] {reason_note}",



                    )



                    self.db.record_admin_audit(



                        admin_user=admin_id,



                        action_type="PROOF_OVERRIDDEN",



                        field_changed=f"proof:{evidence_id}",



                        old_value="AI_PROVISIONAL",



                        new_value=f"OVERRIDDEN (+{points} pts): {reason_note}",



                        device_id="admin_portal",



                    )



                    if act_id:



                        twe.record_activity_verification(act_id, True, points, evidence_id=evidence_id)



                    res.update({"success": True, "action": "OVERRIDE", "points": points})



                elif action == "MARK_DUPLICATE":



                    with self.db._connect() as conn:



                        conn.execute(



                            "UPDATE proof_reviews SET decision = 'FLAGGED_DUPLICATE', status = 'REJECTED', points_awarded = 0, reason_note = ?, reviewed_by = ? WHERE evidence_id = ?",



                            (f"[Marked Duplicate] {reason_note}", admin_id, evidence_id),



                        )



                    self.db.record_admin_audit(



                        admin_user=admin_id,



                        action_type="PROOF_MARKED_DUPLICATE",



                        field_changed=f"proof:{evidence_id}",



                        old_value="DUPLICATE_REVIEW",



                        new_value=reason_note,



                        device_id="admin_portal",



                    )



                    if act_id:



                        twe.record_activity_verification(act_id, False, 0)



                    res = {"success": True, "action": "MARK_DUPLICATE"}



                else:



                    res = {"success": False, "error": f"Unknown action: {action}"}







                try:



                    from core.privilege_engine import PrivilegeEngine



                    pe = PrivilegeEngine(self.db)



                    pe.evaluate_current_privilege()



                except Exception:



                    pass







                try:



                    from core.hourly_submission_service import HourlySubmissionService



                    with self.db._connect() as conn:



                        pr_user = conn.execute("SELECT user_id FROM proof_reviews WHERE evidence_id = ?", (evidence_id,)).fetchone()



                        rev_user_id = pr_user["user_id"] if pr_user else "user_lok"



                    day_score = int(self.engine.recalculate_daily_score(now_ist().strftime("%Y-%m-%d")).get("final_score", 0))



                    review_status = "APPROVED" if action in ("APPROVE", "VERIFY", "OVERRIDE") else "REJECTED"



                    review_pts = points if review_status == "APPROVED" else 0



                    HourlySubmissionService(self.db, self.engine).publish_review_event(



                        account_id=rev_user_id,



                        activity_id=act_id or "",



                        evidence_id=evidence_id,



                        action=action,



                        status=review_status,



                        points=review_pts,



                        admin_id=admin_id,



                        reason_note=reason_note,



                        shared_score=day_score,



                    )



                except Exception:



                    logger.exception("Could not publish durable admin review event")







                bus = EventBus.get_instance()



                bus.publish(



                    "ADMIN_REVIEW_COMPLETED",



                    {



                        "evidence_id": evidence_id,



                        "activity_id": act_id,



                        "action": action,



                        "points": points,



                        "admin_id": admin_id,



                    },



                )



                self._send_json(res)



            elif path == "/api/activity/submit":



                self._send_json({



                    "success": False,



                    "allowed": False,



                    "points": 0,



                    "error": "Activity submissions require proof review; this endpoint cannot verify or award points directly.",



                }, status=403)



            elif path == "/api/activity/verify":



                self._send_json({



                    "success": False,



                    "allowed": False,



                    "points": 0,



                    "error": "Activity verification requires an approved proof review.",



                }, status=403)



            elif path == "/api/activity/check_missed":



                from core.time_window_engine import TimeWindowEngine



                twe = TimeWindowEngine(self.db)



                acts = twe.get_activities_for_date(date)



                self._send_json({"date": date, "activities": acts})



            elif path == "/api/schedule/validate":



                from core.time_window_engine import TimeWindowEngine



                twe = TimeWindowEngine(self.db)



                res = twe.validate_routine_schedule(



                    payload=body,



                    schedule_date=body.get("schedule_date"),



                )



                self._send_json(res)



            elif path == "/api/schedule/review":



                from core.time_window_engine import TimeWindowEngine



                twe = TimeWindowEngine(self.db)



                uid = body.get("account_id") or body.get("user_id") or "user_lok"



                val = twe.validate_routine_schedule(



                    payload=body,



                    schedule_date=body.get("schedule_date"),



                )



                summary = twe.build_routine_summary(body, account_id=uid)



                self._send_json({



                    "valid": val["valid"],



                    "validation": val,



                    "summary": summary,



                })



            elif path == "/api/schedule/draft":



                from core.time_window_engine import TimeWindowEngine



                twe = TimeWindowEngine(self.db)



                uid = body.get("account_id") or body.get("user_id") or "user_lok"



                res = twe.save_next_day_draft(



                    payload=body,



                    account_id=uid,



                    device_id=device_id,



                    dismissed=bool(body.get("dismissed", False)),



                )



                if not res.get("success"):



                    self._send_json(res, status=400)



                else:



                    self._send_json(res)



            elif path == "/api/schedule/confirm":



                from core.time_window_engine import TimeWindowEngine



                from core.strictbot import StrictBot



                twe = TimeWindowEngine(self.db)



                uid = body.get("account_id") or body.get("user_id") or "user_lok"



                res = twe.confirm_next_day_schedule(



                    payload=body,



                    account_id=uid,



                    actor=body.get("actor", "USER"),



                    device_id=device_id,



                )



                if not res.get("success"):



                    status_code = 403 if res.get("is_locked") else 400



                    self._send_json(res, status=status_code)



                else:



                    sb_msg = StrictBot.evaluate_schedule_message(



                        next_day_schedule=res.get("schedule"),



                        event_trigger="SCHEDULE_CONFIRMED",



                    )



                    res["strictbot"] = sb_msg



                    self._send_json(res)



            elif path == "/api/schedule/reschedule":



                from core.time_window_engine import TimeWindowEngine



                twe = TimeWindowEngine(self.db)



                uid = body.get("account_id") or body.get("user_id") or "user_lok"



                res = twe.admin_reschedule_routine(



                    payload=body,



                    account_id=uid,



                )



                if not res.get("success"):



                    status_code = 403 if "PIN" in str(res.get("error", "")) else 400



                    self._send_json(res, status=status_code)



                else:



                    self._send_json(res)



            elif path == "/api/schedule/sync":



                from core.time_window_engine import TimeWindowEngine



                twe = TimeWindowEngine(self.db)



                try:



                    uid = self._get_authenticated_device_account(device_id, str(body.get("device_token") or ""))



                except PermissionError as exc:



                    self._send_json({"success": False, "error": str(exc), "code": "UNAUTHORIZED_DEVICE"}, status=403)



                    return



                res = twe.sync_routine_schedule(



                    schedule_date=body.get("schedule_date"),



                    account_id=uid,



                    device_id=device_id,



                    incoming_payload={"schedule": body.get("incoming_schedule") or body.get("schedule"), "device_id": device_id},



                )



                self._send_json(res)



            elif path == "/api/schedule/policy":



                from core.time_window_engine import TimeWindowEngine



                twe = TimeWindowEngine(self.db)



                res = twe.set_admin_task_policy(



                    gym_required_by_admin=bool(body.get("gym_required_by_admin", False)),



                    allow_ordinary_task_hourly_overlap=bool(body.get("allow_ordinary_task_hourly_overlap", False)),



                    admin_pin=str(body.get("admin_pin", "")),



                )



                if not res.get("success"):



                    self._send_json(res, status=403)



                else:



                    self._send_json(res)



            elif path == "/api/gym/start":



                res = self.engine.start_gym_session(date=date, device_id=device_id, is_dev_mode=is_dev)



                self._send_json(res)



            elif path == "/api/gym/complete":



                res = self.engine.complete_gym_session(



                    date=date,



                    duration_minutes=float(body.get("duration_minutes", 30.0)),



                    device_id=device_id,



                    image_base64=body.get("image_base64"),



                    notes=body.get("notes", ""),



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/gym/skip":



                res = self.engine.skip_gym_session(date=date, device_id=device_id, is_dev_mode=is_dev, activity_id=body.get("activity_id"))



                self._send_json(res)



            elif path in ("/api/gym/withdraw_skip", "/api/gym/reset"):



                res = self.engine.withdraw_gym_skip(date=date, user_id=body.get("user_id", "user_lok"), is_dev_mode=is_dev)



                self._send_json(res)



            elif path == "/api/tasks/create":



                res = self.engine.create_custom_task(



                    date=date,



                    task_code=body["task_code"],



                    title=body["title"],



                    task_type=body.get("task_type", "CUSTOM_TASK"),



                    target_time=body.get("target_time", "14:00"),



                    deadline_time=body.get("deadline_time", "15:00"),



                    duration_minutes=int(body.get("duration_minutes", 30)),



                    min_duration_minutes=int(body.get("min_duration_minutes", 0)),



                    grace_period_minutes=int(body.get("grace_period_minutes", 10)),



                    point_value=int(body.get("point_value", 10)),



                    penalty_value=int(body.get("penalty_value", -5)),



                    dependencies=body.get("dependencies", []),



                    required_evidence=body.get("required_evidence", "OPTIONAL"),



                    actor_role=body.get("actor_role", "ADMIN"),



                    admin_user=body.get("admin_user", "Administrator"),



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/tasks/complete":



                res = self.engine.complete_custom_task(



                    date=date,



                    task_code=body["task_code"],



                    completed_at=body.get("completed_at"),



                    duration_minutes=float(body.get("duration_minutes", 0.0)),



                    device_id=device_id,



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/focus/start":



                self._send_json({"success": False, "error": "Focus feature has been deactivated."}, status=400)



                return



            elif path == "/api/focus/pause":



                res = self.engine.pause_focus_session(focus_id=body["focus_id"])



                self._send_json(res)



            elif path == "/api/focus/resume":



                res = self.engine.resume_focus_session(focus_id=body["focus_id"])



                self._send_json(res)



            elif path == "/api/focus/interrupt":



                res = self.engine.interrupt_focus_session(



                    focus_id=body["focus_id"],



                    reason=body.get("reason", "Interrupted by user"),



                )



                self._send_json(res)



            elif path in ("/api/focus/complete", "/api/focus/end"):



                res = self.engine.complete_focus_session(



                    date=date,



                    focus_id=body["focus_id"],



                    actual_minutes=float(body.get("actual_minutes", 45.0)),



                    image_base64=body.get("image_base64"),



                    device_id=device_id,



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/focus/settings":



                res = self.db.save_focus_settings(body)



                if self.restriction_mgr and "youtube_study_mode" in body:



                    self.restriction_mgr.set_study_mode(body["youtube_study_mode"])



                self._send_json(res)



            elif path == "/api/focus/groups/join":



                res = self.db.join_focus_group(group_id=body["group_id"], user_id=body.get("user_id", "user_lok"))



                self._send_json(res)



            elif path == "/api/focus/goals/onboarding":



                body["onboarding_completed"] = True



                res = self.db.save_focus_settings(body)



                self._send_json(res)



            elif path == "/api/breaks/log":



                res = self.engine.log_break_session(



                    date=date,



                    category=body.get("category", "SHORT_BREAK"),



                    duration_minutes=float(body.get("duration_minutes", 15.0)),



                    notes=body.get("notes", ""),



                    device_id=device_id,



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/score/correct":



                res = self.engine.create_score_correction_event(



                    date=date,



                    parent_event_id=body["parent_event_id"],



                    correction_points=int(body["correction_points"]),



                    reason=body.get("reason", "Admin correction"),



                    admin_user=body.get("admin_user", "Administrator"),



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/pairing/initiate":



                res = self.db.initiate_usb_pairing(



                    device_id=device_id,



                    device_name=body.get("device_name", "Android Device"),



                    platform=body.get("platform", "ANDROID"),



                    transport=body.get("transport", "USB_ADB_PORT_FORWARD"),



                )



                self._send_json(res)



            elif path == "/api/pairing/approve":



                res = self.db.approve_usb_pairing(



                    pairing_id=body["pairing_id"],



                    pairing_code=body["pairing_code"],



                    admin_user=body.get("admin_user", "Administrator"),



                )



                self._send_json(res)



            elif path == "/api/pairing/revoke":



                res = self.db.revoke_device_pairing(



                    device_id=body["device_id"],



                    admin_user=body.get("admin_user", "Administrator"),



                )



                self._send_json(res)



            elif path == "/api/journal/save":



                res = self.engine.save_daily_journal(



                    date=date,



                    reflection_text=body.get("reflection_text", ""),



                    what_went_well=body.get("what_went_well", ""),



                    what_was_difficult=body.get("what_was_difficult", ""),



                    improve_tomorrow=body.get("improve_tomorrow", ""),



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/recovery/reboot":



                res = self.engine.recover_after_reboot(



                    date=date,



                    current_time_hhmm=body.get("current_time_hhmm"),



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/clock/check":



                res = self.engine.detect_system_clock_change(



                    date=date,



                    reported_timestamp=body["reported_timestamp"],



                    device_id=device_id,



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/entertainment/check":



                res = self.engine.evaluate_entertainment_access(



                    date=date,



                    target_app=body.get("target_app", "YouTube"),



                    platform=body.get("platform", "WINDOWS"),



                    record_violation_if_blocked=bool(body.get("record_violation_if_blocked", True)),



                    device_id=device_id,



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/evidence/delete":



                res = self.engine.delete_user_evidence(body["evidence_id"], device_id=device_id)



                self._send_json(res)



            elif path == "/api/settings/update":



                res = self.db.update_settings(



                    body.get("settings", {}),



                    actor_role=body.get("actor_role", "ADMIN"),



                    admin_user=body.get("admin_user", "Administrator"),



                )



                self._send_json(res.to_dict())



            elif path == "/api/sync/batch":



                dev_id = str(body.get("device_id") or "")



                dev_token = str(body.get("device_token") or "")



                if not dev_token or not self.db.validate_device_credential(dev_id, dev_token):



                    self._send_json({"success": False, "error": "Device registration revoked or invalid.", "code": "UNAUTHORIZED_DEVICE"}, status=403)



                    return



                res = self.engine.process_sync_batch(



                    items=body.get("items", []),



                    device_id=dev_id,



                    protocol_version=str(body.get("protocol_version", "2.0.0")),



                )



                if isinstance(body.get("ledger_snapshot"), dict):



                    attempt = self.engine.reconcile_android_ledger(body["ledger_snapshot"], dev_id)



                    res.update(self.engine.authoritative_score_snapshot(now_ist().strftime("%Y-%m-%d")))



                    if not attempt.get("accepted"):



                        res["ledger_receipt"] = attempt



                self._send_json(res)



            elif path == "/api/windows/sample":



                res = self.win_agent.sample_current_activity(



                    sample_duration_seconds=60.0,



                    is_dev_mode=is_dev,



                    enforce_restrictions=True,



                    show_gui_popup=False,



                )



                self._send_json(res)



            elif path == "/api/export/evidence_package":



                res = self.engine.export_daily_evidence_package(date=date, is_dev_mode=is_dev)



                self._send_json(res)



            elif path == "/api/export/formats":



                res = self.engine.export_daily_record_formats(date=date, is_dev_mode=is_dev)



                self._send_json(res)



            elif path == "/api/dev/simulate":



                res = self.engine.run_dev_simulation(date=date, scenario=body["scenario"])



                self._send_json(res)



            elif path == "/api/exams/batch_prebook":



                tests = body.get("tests") or []



                if not isinstance(tests, list):



                    self._send_json({"success": False, "error": "tests must be a list of 1 to 4 test configurations"}, status=400)



                    return



                if len(tests) == 0:



                    self._send_json({"success": False, "error": "At least 1 test is required"}, status=400)



                    return



                if len(tests) > 4:



                    self._send_json({"success": False, "error": "Maximum 4 tests allowed."}, status=400)



                    return







                dev_id = self.headers.get("X-Device-Id") or "windows_pc"



                batch_data = {



                    "batch_id": body.get("batch_id"),



                    "account_id": principal.account_id if hasattr(self, "_gym_principal") else "user_default",



                    "tests": tests,



                }



                try:



                    res = self.db.create_exam_prebooking_batch(batch_data, device_id=dev_id)



                    if not res.get("success"):



                        self._send_json({"success": False, "error": res.get("error"), "conflicts": res.get("conflicts", [])}, status=400)



                        return



                    self._send_json({"success": True, "batch": res}, status=201)



                except Exception as e:



                    self._send_json({"success": False, "error": str(e)}, status=400)



                return







            elif path == "/api/practice_tests/template":



                tests = body.get("tests") or []



                t_name = str(body.get("template_name") or "Default Daily Practice Template").strip()



                try:



                    res = self.db.save_daily_practice_template(tests, template_name=t_name)



                    self._send_json({"success": True, "template": res})



                except Exception as e:



                    self._send_json({"success": False, "error": str(e)}, status=400)



                return







            elif path == "/api/practice_tests/submit":



                from core.daily_practice_test_engine import DailyPracticeTestEngine



                d_val = str(body.get("date") or DailyPracticeTestEngine.today_str()).strip()



                tests = body.get("tests") or []



                notes = str(body.get("notes") or "").strip()



                force_unlock = bool(body.get("force_unlock", False))



                try:



                    sched = self.db.submit_daily_practice_schedule(



                        date_str=d_val,



                        tests=tests,



                        notes=notes,



                        force_unlock=force_unlock,



                    )



                    self._send_json({"success": True, "schedule": sched}, status=201)



                except PermissionError as pe:



                    self._send_json({"success": False, "error": str(pe), "locked": True}, status=403)



                except Exception as e:



                    self._send_json({"success": False, "error": str(e)}, status=400)



                return







            elif path == "/api/practice_tests/start":



                exec_id = str(body.get("execution_id") or "").strip()



                if not exec_id:



                    self._send_json({"success": False, "error": "execution_id is required"}, status=400)



                    return



                res = self.db.start_practice_test_execution(exec_id)



                if res:



                    self._send_json({"success": True, "execution": res})



                else:



                    self._send_json({"success": False, "error": f"Practice test execution {exec_id} not found"}, status=404)



                return







            elif path == "/api/practice_tests/complete":



                exec_id = str(body.get("execution_id") or "").strip()



                if not exec_id:



                    self._send_json({"success": False, "error": "execution_id is required"}, status=400)



                    return



                score = body.get("score")



                max_score = body.get("max_score")



                notes = str(body.get("notes") or "").strip()



                res = self.db.complete_practice_test_execution(



                    execution_id=exec_id,



                    score=float(score) if score is not None else None,



                    max_score=float(max_score) if max_score is not None else None,



                    notes=notes,



                )



                if res:



                    self._send_json({"success": True, "execution": res})



                else:



                    self._send_json({"success": False, "error": f"Practice test execution {exec_id} not found"}, status=404)



                return







            elif path == "/api/practice_tests/reschedule":



                exec_id = str(body.get("execution_id") or "").strip()



                new_start = str(body.get("new_start_time") or "").strip()



                reason = str(body.get("reason") or "User requested reschedule").strip()



                if not exec_id or not new_start:



                    self._send_json({"success": False, "error": "execution_id and new_start_time are required"}, status=400)



                    return



                res = self.db.reschedule_practice_test_execution(exec_id, new_start, reason)



                if res:



                    self._send_json({"success": True, "execution": res})



                else:



                    self._send_json({"success": False, "error": f"Practice test execution {exec_id} not found"}, status=404)



                return







            elif path == "/api/exams/prebook":



                from core.exam_prebooking_engine import ExamPrebookingEngine



                images_b64 = body.get("images") or []



                if not isinstance(images_b64, list):



                    self._send_json({"success": False, "error": "images must be a list"}, status=400)



                    return



                if len(images_b64) > 5:



                    self._send_json({"success": False, "error": f"Exceeded maximum supporting images. Up to 5 images permitted, received {len(images_b64)}."}, status=400)



                    return



                raw_imgs = []



                for b in images_b64:



                    if isinstance(b, str):



                        if "," in b:



                            b = b.split(",", 1)[1]



                        raw_imgs.append(base64.b64decode(b, validate=True))



                    elif isinstance(b, bytes):



                        raw_imgs.append(b)







                exam_name = str(body.get("exam_name") or "").strip()



                subject = str(body.get("subject") or "").strip()



                institution = str(body.get("institution") or "").strip()



                exam_date = str(body.get("exam_date") or "").strip()



                start_t = str(body.get("start_time") or body.get("official_start_time") or "").strip()



                end_t = str(body.get("end_time") or body.get("official_end_time") or "").strip()



                exam_type = str(body.get("exam_type") or "SEMESTER").strip()



                prep_buf = int(body.get("prep_buffer_minutes") or 30)



                comp_buf = int(body.get("completion_buffer_minutes") or 30)



                filenames = body.get("filenames") or body.get("image_filenames")







                acts = self.db.get_scheduled_activities_for_date(exam_date)







                decision = ExamPrebookingEngine.validate_and_allocate(



                    exam_name=exam_name,



                    subject=subject,



                    institution=institution,



                    exam_date=exam_date,



                    start_time=start_t,



                    end_time=end_t,



                    supporting_images=raw_imgs,



                    image_filenames=filenames,



                    exam_type=exam_type,



                    prep_buffer_minutes=prep_buf,



                    completion_buffer_minutes=comp_buf,



                    additional_info=str(body.get("additional_info") or ""),



                    booking_id=body.get("booking_id"),



                    existing_activities=acts,



                )







                booking_record = {



                    "booking_id": decision.booking_id,



                    "account_id": principal.account_id if hasattr(self, "_gym_principal") else "user_default",



                    "exam_name": decision.exam_name,



                    "subject": decision.subject,



                    "institution": decision.institution,



                    "exam_date": decision.confirmed_date,



                    "official_start_time": decision.confirmed_start_time,



                    "official_end_time": decision.confirmed_end_time,



                    "exam_type": exam_type,



                    "prep_buffer_minutes": decision.prep_buffer_minutes,



                    "completion_buffer_minutes": decision.completion_buffer_minutes,



                    "allocation_start_iso": decision.allocation_start_iso,



                    "allocation_end_iso": decision.allocation_end_iso,



                    "allocation_start_ms": int(datetime.fromisoformat(decision.allocation_start_iso).timestamp() * 1000) if decision.allocation_start_iso else 0,



                    "allocation_end_ms": int(datetime.fromisoformat(decision.allocation_end_iso).timestamp() * 1000) if decision.allocation_end_iso else 0,



                    "status": decision.validation_status,



                    "validation_provider": decision.validation_provider,



                    "validation_explanation": decision.explanation,



                    "cross_image_consistency": decision.cross_image_consistency,



                    "supporting_images_count": len(raw_imgs),



                    "evidence_files": [m.to_dict() for m in decision.evidence_meta_list],



                    "additional_info": str(body.get("additional_info") or ""),



                }



                created = self.db.create_exam_prebooking(



                    booking_record,



                    device_id=self.headers.get("X-Device-Id") or "windows_pc"



                )



                excused_slots = self.db.reconcile_exam_attendance_exemptions(decision.confirmed_date)



                created["excused_hourly_slots"] = excused_slots







                self._send_json({



                    "success": True,



                    "decision": decision.to_dict(),



                    "booking": created,



                    "excused_hourly_slots": excused_slots,



                }, status=201 if decision.validation_status == "EXAM_APPROVED" else 200)



            elif path == "/api/exams/cancel":



                bid = body.get("booking_id")



                reason = str(body.get("reason") or "User cancelled")



                if not bid:



                    self._send_json({"success": False, "error": "booking_id is required"}, status=400)



                    return



                ok = self.db.cancel_exam_prebooking(bid, reason=reason, device_id=self.headers.get("X-Device-Id") or "windows_pc")



                self._send_json({"success": ok, "booking_id": bid, "status": "EXAM_CANCELLED"})



            elif path == "/api/exams/reschedule":



                bid = body.get("booking_id")



                new_d = body.get("new_date") or body.get("exam_date")



                new_s = body.get("new_start_time") or body.get("start_time")



                new_e = body.get("new_end_time") or body.get("end_time")



                p_buf = int(body.get("prep_buffer_minutes") or 30)



                c_buf = int(body.get("completion_buffer_minutes") or 30)



                if not bid or not new_d or not new_s or not new_e:



                    self._send_json({"success": False, "error": "booking_id, new_date, new_start_time and new_end_time are required"}, status=400)



                    return



                res = self.db.reschedule_exam_prebooking(



                    booking_id=bid,



                    new_date=new_d,



                    new_start=new_s,



                    new_end=new_e,



                    prep_buffer=p_buf,



                    completion_buffer=c_buf,



                    device_id=self.headers.get("X-Device-Id") or "windows_pc"



                )



                self._send_json({"success": True, "booking": res})



            elif path == "/api/exams/sync":



                dev_id = self.headers.get("X-Device-Id") or "android_device"



                res = self.db.sync_exam_prebooking(body, device_id=dev_id)



                self._send_json({"success": True, "sync": res})



            elif path == "/api/exams/early_exit":



                bid = body.get("booking_id")



                actual_time = body.get("actual_end_time")



                actual_iso = body.get("actual_end_iso")



                actual_ms = body.get("actual_end_ms")



                dev_id = self.headers.get("X-Device-Id") or "windows_pc"







                from windows.neo_exam_monitor import NeoExamBrowserMonitor



                mon = NeoExamBrowserMonitor.get_instance(self.db)



                try:



                    res = mon.confirm_early_exit(



                        booking_id=bid,



                        actual_end_time=actual_time,



                        actual_end_iso=actual_iso,



                        actual_end_ms=actual_ms,



                        device_id=dev_id,



                    )



                    self._send_json({"success": True, "booking": res, "status": "EXAM_COMPLETED_EARLY"})



                except Exception as e:



                    self._send_json({"success": False, "error": str(e)}, status=400)







            # ── User & Candidate POST Routes ─────────────────────────



            elif path == "/api/user/register":



                res = self.db.create_user(



                    username=body.get("username", "candidate"),



                    email=body.get("email", ""),



                    role=body.get("role", "CANDIDATE"),



                    full_name=body.get("full_name", ""),



                )



                self._send_json(res)



            elif path == "/api/user/login":



                u = self.db.get_user_by_username(body.get("username", ""))



                if u:



                    self._send_json({"authenticated": True, "user": u})



                else:



                    self._send_json({"authenticated": False, "error": "User not found"}, status=404)



            elif path == "/api/admin/candidate/create":



                res = self.db.create_user(



                    username=body.get("username", ""),



                    email=body.get("email", ""),



                    role="CANDIDATE",



                    full_name=body.get("full_name", ""),



                )



                self._send_json(res)



            elif path == "/api/admin/exam/create":



                res = self.db.create_exam(



                    exam_id=body.get("exam_id"),



                    title=body.get("title", "New Examination"),



                    category=body.get("category", "EXAM"),



                    duration_minutes=int(body.get("duration_minutes", 60)),



                    total_marks=int(body.get("total_marks", 100)),



                    passing_marks=int(body.get("passing_marks", 50)),



                    instructions=body.get("instructions", ""),



                    questions=body.get("questions", []),



                    created_by=body.get("created_by", "Administrator"),



                )



                self._send_json(res)



            elif path == "/api/exam/attempt/submit":



                answers = body.get("answers", {})



                answers_json = json.dumps(answers) if isinstance(answers, (dict, list)) else str(answers)



                flags = body.get("proctor_flags", [])



                flags_json = json.dumps(flags) if isinstance(flags, (dict, list)) else str(flags)



                res = self.db.record_exam_attempt(



                    exam_id=body.get("exam_id", "EXAM_DEMO_01"),



                    user_id=body.get("user_id", "guest"),



                    answers_json=answers_json,



                    score=float(body.get("score", 0.0)),



                    total_marks=float(body.get("total_marks", 100.0)),



                    status=body.get("status", "SUBMITTED"),



                    proctor_flags_json=flags_json,



                    code_submission=body.get("code_submission", ""),



                    sql_submission=body.get("sql_submission", ""),



                    duration_seconds=int(body.get("duration_seconds", 0)),



                )



                self._send_json(res)



            elif path == "/api/admin/reset_dev_data":



                pin = body.get("admin_pin", "")



                try:



                    res = self.db.reset_development_data(admin_pin=pin)



                    self._send_json(res)



                except PermissionError as pe:



                    self._send_json({"error": str(pe)}, status=403)



            # ── Admin Ecosystem POST Routes ───────────────────────────



            elif path == "/api/admin/login":



                try:



                    res = self.db.authenticate_admin(



                        username_or_pin=body.get("username") or body.get("pin", ""),



                        secret=body.get("password") or body.get("pin", ""),



                        device_id=body.get("device_id", "windows_admin_console"),



                    )



                    self.db.clear_admin_auth_failures(auth_client_key)



                    StrictRecordsRequestHandler._pin_failed_attempts = 0



                    StrictRecordsRequestHandler._pin_lockout_until = 0.0



                    self._send_json(res)



                except PermissionError as exc:



                    cooldown = self.db.record_admin_auth_failure(auth_client_key)



                    StrictRecordsRequestHandler._pin_failed_attempts += 1



                    self._send_json({"success": False, "error": str(exc), "locked_out": cooldown > 0, "cooldown_seconds": cooldown}, status=429 if cooldown else 403)



            elif path == "/api/admin/logout":



                tok = (



                    body.get("token")



                    or body.get("admin_token")



                    or body.get("admin_session")



                    or self.headers.get("X-Admin-Session")



                    or self.headers.get("X-Admin-Token")



                    or self.headers.get("Authorization", "").replace("Bearer ", "").strip()



                    or (self._authenticated_admin.get("token") if self._authenticated_admin else "")



                )



                ok = self.db.logout_admin(tok) if tok else True



                self._authenticated_admin = None



                self._send_json({"success": True, "logged_out": bool(ok)})



            elif path == "/api/admin/verify_pin":



                now_ts = time.time()



                if now_ts < StrictRecordsRequestHandler._pin_lockout_until:



                    cooldown = int(math.ceil(StrictRecordsRequestHandler._pin_lockout_until - now_ts))



                    self._send_json({



                        "valid": False,



                        "success": False,



                        "error": f"Too many failed attempts. Locked out for {cooldown}s.",



                        "locked_out": True,



                        "cooldown_seconds": cooldown,



                        "remaining_attempts": 0,



                    }, status=429)



                    return







                pin = str(body.get("pin") or body.get("admin_pin") or "").strip()



                valid = self.db.verify_admin_pin(pin)



                if valid:



                    self.db.clear_admin_auth_failures(auth_client_key)



                    StrictRecordsRequestHandler._pin_failed_attempts = 0



                    StrictRecordsRequestHandler._pin_lockout_until = 0.0



                    auth = self.db.authenticate_admin("admin", pin, device_id="windows_admin_console")



                    token = auth.get("token") or hashlib.sha256(f"{pin}:{now_ts}".encode("utf-8")).hexdigest()



                    is_default = self.db.is_default_admin_pin()



                    self._send_json({



                        "valid": True,



                        "success": True,



                        "token": token,



                        "is_default_key": is_default,



                        "message": "Administrator access verified",



                    })



                else:



                    persisted_cooldown = self.db.record_admin_auth_failure(auth_client_key)



                    StrictRecordsRequestHandler._pin_failed_attempts += 1



                    if persisted_cooldown > 0 or StrictRecordsRequestHandler._pin_failed_attempts >= 5:



                        StrictRecordsRequestHandler._pin_lockout_until = now_ts + 60.0



                        StrictRecordsRequestHandler._pin_failed_attempts = 0



                        self._send_json({



                            "valid": False,



                            "success": False,



                            "error": "Too many failed attempts. Locked out for 60 seconds ❌",



                            "locked_out": True,



                            "cooldown_seconds": 60,



                            "remaining_attempts": 0,



                        }, status=429)



                    else:



                        rem = 5 - StrictRecordsRequestHandler._pin_failed_attempts



                        self._send_json({



                            "valid": False,



                            "success": False,



                            "error": f"Incorrect administrator key. {rem} attempt{'s' if rem != 1 else ''} remaining ❌",



                            "locked_out": False,



                            "remaining_attempts": rem,



                        }, status=403)



            elif path == "/api/admin/change_pin":



                current_pin = body.get("current_pin", "")



                new_pin = body.get("new_pin", "")



                admin_user = body.get("admin_user", "Administrator")



                res = self.db.change_admin_pin(current_pin=current_pin, new_pin=new_pin, admin_user=admin_user)



                st_code = 200 if res.get("success") else 400



                self._send_json(res, status=st_code)



            elif path == "/api/admin/open_settings":



                target = body.get("target", "startupapps")



                allowed = {



                    "startupapps": "ms-settings:startupapps",



                    "notifications": "ms-settings:notifications",



                    "privacy": "ms-settings:privacy",



                    "appsfeatures": "ms-settings:appsfeatures",



                }



                uri = allowed.get(target, "ms-settings:privacy")



                try:



                    os.startfile(uri)



                    self._send_json({"success": True, "launched": uri})



                except Exception as ex:



                    self._send_json({"success": False, "error": str(ex)}, status=500)



            elif path == "/api/user/profile":



                display_name = body.get("display_name")



                email = body.get("email")



                updated = self.db.update_user_profile(user_id="user_lok", display_name=display_name, email=email)



                self._send_json({"user": updated, "profile": updated, "success": True})



            elif path == "/api/admin/proof/approve":



                res = self.engine.apply_proof_approval(



                    evidence_id=body["evidence_id"],



                    admin_id=body.get("admin_id", "Administrator"),



                    points=int(body.get("points", 3)),



                    reason=body.get("reason", "Evidence verified by Administrator"),



                    rule_version=int(body.get("rule_version", 1)),



                )



                self._send_json(res)



            elif path == "/api/admin/proof/reject":



                res = self.engine.apply_proof_rejection(



                    evidence_id=body["evidence_id"],



                    admin_id=body.get("admin_id", "Administrator"),



                    reason_category=body.get("reason_category", "Other"),



                    reason_note=body.get("reason_note", ""),



                    allow_resubmission=bool(body.get("allow_resubmission", True)),



                )



                self._send_json(res)



            elif path == "/api/admin/proof/bulk":



                res = self.db.bulk_review_proofs(



                    evidence_ids=body.get("evidence_ids", []),



                    decision=body.get("decision", "APPROVE"),



                    admin_id=body.get("admin_id", "Administrator"),



                    reason_category=body.get("reason_category", ""),



                    reason_note=body.get("reason_note", ""),



                    points_each=int(body.get("points_each", 3)),



                )



                self._send_json(res)



            elif path == "/api/admin/proof/resubmit":



                res = self.db.resubmit_proof(



                    evidence_id=body["evidence_id"],



                    user_id=body.get("user_id") or "unassigned",



                    new_file_path=body.get("new_file_path", ""),



                )



                self._send_json(res)



            elif path == "/api/deductions/proposals":



                req_date = body.get("date") or date



                req_status = body.get("status") or None



                proposals = self.db.get_deduction_proposals(date=req_date, status=req_status)



                self._send_json({"success": True, "proposals": proposals, "count": len(proposals)})



            elif path == "/api/deductions/propose":



                prop_id = body.get("proposal_id") or f"prop_{uuid.uuid4().hex[:12]}"



                proposal = self.db.create_deduction_proposal(



                    proposal_id=prop_id,



                    date=body.get("date") or date,



                    timestamp=body.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),



                    originating_device=device_id or body.get("device_id", "client"),



                    event_type=body.get("event_type", "VIOLATION"),



                    proposed_points=int(body.get("proposed_points", -10)),



                    reason=body.get("reason", "Violation detected"),



                    related_task_id=body.get("related_task_id", ""),



                    applicable_rule=body.get("applicable_rule", "GENERAL_DISCIPLINE"),



                    evidence=body.get("evidence", {}),



                    status="AWAITING_CONFIRMATION",



                )



                self._send_json({"success": True, "proposal": proposal, "points_deducted": 0})



            elif path in ("/api/deductions/approve", "/api/transactions/authorize", "/api/transactions/approve"):



                prop_id = body.get("proposal_id")



                if not prop_id:



                    self._send_json({"success": False, "error": "proposal_id is required"}, status=400)



                    return



                # Section 1 & 2: Human approval verification



                admin_user = body.get("admin_user") or "admin"



                approval_src = body.get("approval_source") or "ANDROID_HUMAN_CONFIRMATION"



                if approval_src not in ("ANDROID_HUMAN_CONFIRMATION", "AUTHENTICATED_HUMAN_ADMIN"):



                    self._send_json({"success": False, "error": "Invalid approval source. Human confirmation via Android workflow required."}, status=403)



                    return



                res = self.engine.approve_deduction_proposal(



                    proposal_id=prop_id,



                    admin_user=admin_user,



                    approval_source=approval_src,



                )



                self._send_json(res)



            elif path in ("/api/deductions/reject", "/api/transactions/reject"):



                prop_id = body.get("proposal_id")



                if not prop_id:



                    self._send_json({"success": False, "error": "proposal_id is required"}, status=400)



                    return



                admin_user = body.get("admin_user") or "admin"



                reason = body.get("reason") or "Rejected by administrator"



                res = self.engine.reject_deduction_proposal(



                    proposal_id=prop_id,



                    admin_user=admin_user,



                    reason=reason,



                )



                self._send_json(res)



            elif path in ("/api/transactions/reverse", "/api/score/reverse"):



                ev_id = body.get("event_id")



                if not ev_id:



                    self._send_json({"success": False, "error": "event_id is required"}, status=400)



                    return



                admin_user = body.get("admin_user") or "primary_admin"



                reason = body.get("reason") or "Reversed by administrator"



                res = self.engine.reverse_transaction(



                    event_id=ev_id,



                    admin_user=admin_user,



                    reason=reason,



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path in ("/api/transactions/reset_initial", "/api/score/reset_initial"):



                target_dt = body.get("date") or date



                uid = body.get("user_id", "user_lok")



                admin_id = body.get("admin_id", "primary_admin")



                res = self.engine.execute_initial_points_reset_safely(



                    date=target_dt, user_id=uid, admin_id=admin_id, is_dev_mode=is_dev



                )



                self._send_json(res)



            elif path == "/api/admin/score/adjust":



                res = self.engine.apply_admin_score_adjustment(



                    date=body.get("date", date),



                    admin_id=body.get("admin_id", "Administrator"),



                    adjustment_points=int(body["adjustment_points"]),



                    reason=body.get("reason", ""),



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/admin/hourly/edit":



                from core.hourly_admin_service import HourlyAdminService



                svc = HourlyAdminService(self.db, self.engine)



                act_id = body.get("activity_id")



                if not act_id:



                    self._send_json({"success": False, "error": "activity_id is required"}, status=400)



                    return



                admin_id = getattr(self, "_authenticated_admin", {}).get("admin_id") or getattr(self, "_authenticated_admin", {}).get("username") or body.get("admin_id", "Administrator")



                updates = {



                    "category": body.get("category"),



                    "description": body.get("description"),



                    "admin_notes": body.get("admin_notes"),



                    "review_decision": body.get("review_decision"),



                    "reason": body.get("reason"),



                }



                if body.get("replacement_evidence_base64"):



                    try:



                        raw_bytes = base64.b64decode(body["replacement_evidence_base64"])



                        updates["replacement_evidence_bytes"] = raw_bytes



                        updates["replacement_evidence_filename"] = body.get("replacement_filename", "replacement.jpg")



                    except Exception as exc:



                        self._send_json({"success": False, "error": f"Invalid base64 replacement evidence: {exc}"}, status=400)



                        return



                try:



                    res = svc.edit_hourly_submission(activity_id=act_id, admin_user=admin_id, updates=updates, is_dev_mode=is_dev)



                    self._send_json(res)



                except Exception as ex:



                    status_code = getattr(ex, "status_code", 400)



                    self._send_json({"success": False, "error": str(ex), "code": getattr(ex, "code", "EDIT_ERROR")}, status=status_code)



                return



            elif path == "/api/admin/hourly/award":



                from core.hourly_admin_service import HourlyAdminService



                svc = HourlyAdminService(self.db, self.engine)



                act_id = body.get("activity_id")



                if not act_id:



                    self._send_json({"success": False, "error": "activity_id is required"}, status=400)



                    return



                admin_id = getattr(self, "_authenticated_admin", {}).get("admin_id") or getattr(self, "_authenticated_admin", {}).get("username") or body.get("admin_id", "Administrator")



                raw_points = body.get("points") if body.get("points") is not None else body.get("points_to_award")



                if raw_points is None:



                    self._send_json({"success": False, "error": "points_to_award is required"}, status=400)



                    return



                reason = body.get("reason", "Authorized hourly point assignment")



                confirm_change = bool(body.get("confirm_change", False))



                try:



                    res = svc.award_hourly_points(



                        activity_id=act_id,



                        admin_user=admin_id,



                        points_to_award=raw_points,



                        reason=reason,



                        confirm_change=confirm_change,



                        is_dev_mode=is_dev,



                    )



                    self._send_json(res)



                except Exception as ex:



                    status_code = getattr(ex, "status_code", 400)



                    self._send_json({"success": False, "error": str(ex), "code": getattr(ex, "code", "AWARD_ERROR")}, status=status_code)



                return



            elif path in ("/api/score/reset_daily", "/api/admin/score/reset"):



                res = self.engine.reset_current_daily_score_safely(



                    date=body.get("date", date),



                    admin_id=body.get("admin_id", "primary_admin"),



                    reason=body.get("reason", "One-time daily balance audit reconciliation to 0"),



                    operation_id=body.get("operation_id"),



                    is_dev_mode=is_dev,



                )



                self._send_json(res)



            elif path == "/api/admin/config/draft":



                res = self.db.create_config_draft(



                    version_code=body.get("version_code", "v13"),



                    rules=body.get("rules", {}),



                    admin_id=body.get("admin_id", "Administrator"),



                )



                self._send_json(res)



            elif path == "/api/admin/config/publish":



                res = self.db.publish_configuration(



                    version_code=body["version_code"],



                    admin_id=body.get("admin_id", "Administrator"),



                    effective_from=body.get("effective_from"),



                )



                self._send_json(res)



            elif path == "/api/admin/device/revoke":



                res = self.db.revoke_device_pairing(



                    device_id=body["device_id"],



                    admin_user=body.get("admin_id", "Administrator"),



                )



                self._send_json(res)



            # ── Device Center & Connection Pipeline POST Routes ──────────



            elif path == "/api/device/register":



                dev_id = body.get("device_id", "android_client")



                name = body.get("device_name", "Android Device")



                plat = body.get("platform", "ANDROID")



                ver = body.get("app_version", "2.0.0")



                bat = int(body.get("battery_level", 100))



                uid = body.get("user_id") or "unassigned"



                trans = body.get("transport", "LOCAL_WIFI")



                res = self.connection_manager.register_client_device(



                    device_id=dev_id,



                    device_name=name,



                    platform=plat,



                    app_version=ver,



                    battery_level=bat,



                    user_id=uid,



                    transport=trans,



                )



                self._send_json(res)



            elif path == "/api/device/wifi_pair":



                dev_id = body.get("device_id", "android_client")



                ip = body.get("ip_address", "192.168.1.100")



                port = int(body.get("port", 8765))



                res = self.connection_manager.initiate_wifi_pairing(



                    android_device_id=dev_id,



                    ip_address=ip,



                    port=port,



                )



                self._send_json(res)



            elif path == "/api/proof/ai_evaluate":



                eid = body.get("evidence_id", "")



                uid = body.get("user_id")



                res = self.engine.evaluate_proof_with_ai(evidence_id=eid, user_id=uid)



                self._send_json(res)



            elif path == "/api/strictbot/analyze":



                from core.real_ai_vlm import RealAIBotEngine



                vlm = RealAIBotEngine.get_instance()



                img_b64 = body.get("image_base64") or body.get("image")



                fpath = body.get("file_path")



                tc = body.get("task_code", "HOURLY")



                desc = body.get("description", "")



                res = vlm.analyze_evidence(



                    image_input=img_b64 or fpath,



                    task_code=tc,



                    description=desc,



                    user_context=body.get("user_context", {}),



                )



                self._send_json(res)



            elif path == "/api/sync/shared_score":



                uid = body.get("user_id") or "user_lok"



                dt = body.get("date") or now_ist().strftime("%Y-%m-%d")



                self.engine.execute_daily_reset_if_needed(target_date=dt, user_id=uid)



                summary = self.engine.get_shared_account_summary(user_id=uid, date=dt)



                self._send_json({



                    "shared_score": summary["shared_score"],



                    "score_mode": summary["score_mode"],



                    "score_state": summary["score_state"],



                    "reward_unlocked": summary["reward_unlocked"],



                    "privileges": summary["privileges"],



                    "account": summary["account"],



                    "sync_health": summary["sync_health"],



                })



            elif path == "/api/score/daily_reset":



                uid = body.get("user_id", "user_lok")



                dt = body.get("date") or now_ist().strftime("%Y-%m-%d")



                is_dev = bool(body.get("is_dev_mode", False))



                res = self.engine.execute_daily_reset_if_needed(target_date=dt, user_id=uid, is_dev_mode=is_dev)



                self._send_json(res)



            elif path == "/api/device/detect":



                dev_id = body.get("device_id", "device_client")



                transport = body.get("transport", "USB")



                res = self.connection_manager.detect_device(dev_id, transport=transport, device_info=body)



                self._send_json(res)



            elif path == "/api/device/pair/initiate":



                dev_id = body.get("device_id", "device_client")



                transport = body.get("transport", "USB")



                res = self.connection_manager.initiate_pairing(dev_id, transport=transport)



                self._send_json(res)



            elif path == "/api/device/pair/verify":



                code = body.get("code") or body.get("verification_code", "")



                dev_id = body.get("device_id", "device_client")



                admin_user = body.get("admin_user", "Administrator")



                res = self.connection_manager.verify_pairing_code(code, android_device_id=dev_id, admin_user=admin_user)



                self._send_json(res)



            elif path == "/api/device/pairing/create":



                acc_id = body.get("account_id", "user_lok")



                ep = body.get("server_endpoint")



                res = self.db.create_pairing_session(account_id=acc_id, server_endpoint=ep)



                self._send_json(res)



            elif path == "/api/device/pairing/handshake":



                skey = body.get("session_key", "")



                mpin = body.get("manual_pin") or body.get("pin") or body.get("code") or ""



                dev_id = body.get("device_id", "")



                dev_name = body.get("device_name", "Android Device")



                dev_type = body.get("device_type", "ANDROID")



                plat = body.get("platform", "ANDROID")



                ver = body.get("app_version", "2.0.0")



                res = self.db.handshake_pairing(



                    session_key=skey,



                    device_id=dev_id,



                    device_name=dev_name,



                    device_type=dev_type,



                    platform=plat,



                    app_version=ver,



                    manual_pin=str(mpin),



                )



                if not res.get("success"):



                    code = res.get("code")



                    status = 400 if code in ("EXPIRED", "ALREADY_USED", "NOT_FOUND", "MISSING_CODE", "RATE_LIMITED") else 500



                    self._send_json(res, status=status)



                else:



                    if self.connection_manager:



                        from core.connection_pipeline import ConnectionState, TransportType



                        self.connection_manager.transition_state(



                            dev_id,



                            ConnectionState.CONNECTED,



                            transport=TransportType.LOCAL_WIFI,



                            reason=f"Paired via QR/Session Key ({skey})",



                        )



                    self._send_json(res)



            elif path == "/api/device/heartbeat":



                from core.time_window_engine import TimeWindowEngine







                dev_id = body.get("device_id", "device_client")



                token = body.get("device_token")



                if not token or not self.db.validate_device_credential(dev_id, token):



                    self._send_json({"error": "Device registration revoked. Please pair again.", "code": "REVOKED"}, status=403)



                    return



                policy_ack = body.get("restriction_policy_ack")



                if isinstance(policy_ack, dict):



                    for service, acknowledgement in policy_ack.items():



                        if service in ("youtube", "instagram") and isinstance(acknowledgement, dict):



                            try:



                                self.db.record_device_restriction_policy_ack(dev_id, service, acknowledgement)



                            except Exception as ex:



                                logger.warning("Restriction policy acknowledgement rejected for %s: %s", service, ex)



                if "installed_apps" in body and isinstance(body["installed_apps"], list):



                    self.db.update_device_installed_apps(body["installed_apps"])







                # Record version reported from Android device



                v_name = body.get("app_version_name")



                v_code = body.get("app_version_code")



                cur_vcode = 0



                if v_code is not None:



                    try:



                        v_code_int = int(v_code)



                        cur_vcode = v_code_int



                        now_iso = datetime.now().isoformat()



                        with self.db._connect() as conn:



                            if v_name:



                                conn.execute("UPDATE devices SET app_version = ?, app_version_code = ?, last_seen = ? WHERE device_id = ?", (str(v_name), v_code_int, now_iso, dev_id))



                            else:



                                conn.execute("UPDATE devices SET app_version_code = ?, last_seen = ? WHERE device_id = ?", (v_code_int, now_iso, dev_id))







                        # If device has installed latest release, mark status INSTALLED



                        latest_rel_check = self.db.get_latest_app_release(platform="ANDROID", channel="PRODUCTION")



                        if latest_rel_check and v_code_int >= latest_rel_check.get("version_code", 0):



                            self.db.record_device_update_status(



                                device_id=dev_id,



                                release_id=latest_rel_check.get("release_id"),



                                installed_version_code=v_code_int,



                                status="INSTALLED",



                                progress_percent=100,



                                last_error=None,



                            )



                            EventBus.get_instance().publish(



                                "DEVICE_UPDATE_STATUS_CHANGED",



                                {



                                    "device_id": dev_id,



                                    "release_id": latest_rel_check.get("release_id"),



                                    "version_code": v_code_int,



                                    "status": "INSTALLED",



                                    "progress_percent": 100,



                                }



                            )



                    except Exception as ex:



                        logger.warning(f"Error handling device version in heartbeat: {ex}")







                ledger_receipt = None



                if isinstance(body.get("ledger_snapshot"), dict):



                    ledger_receipt = self.engine.reconcile_android_ledger(body["ledger_snapshot"], dev_id)



                self.db.record_device_heartbeat(dev_id, "ONLINE")
                if getattr(self, "strict_session_manager", None):
                    try:
                        self.strict_session_manager.android_last_heartbeat_at = time.monotonic()
                        target_pid = getattr(self.strict_session_manager, "_active_preparation", "")
                        if target_pid:
                            self.strict_session_manager.ack_android_prepare({
                                "event_type": "ANDROID_STRICT_READY_ACK",
                                "device_id": dev_id,
                                "device_name": body.get("device_name") or dev_id,
                                "preparation_id": target_pid,
                                "apk_version": str(v_name or "1.18.18"),
                                "authenticated": True,
                                "backend_connected": True,
                                "transport_connected": True,
                                "usage_access_granted": True,
                                "usage_monitor_available": True,
                                "monitoring_service_startable": True,
                                "monitoring_service_ready": True,
                                "permissions_required": [],
                                "readiness_status": "READY",
                                "acknowledgement_timestamp": datetime.now().isoformat(),
                                "connectivity_status": "CONNECTED",
                                "session_ready": True,
                            })
                    except Exception:
                        pass



                EventBus.get_instance().publish(



                    "DEVICE_PRESENCE_UPDATE",



                    {



                        "device_id": dev_id,



                        "status": "ONLINE",



                        "is_online": True,



                        "timestamp": datetime.now().astimezone().isoformat()



                    }



                )



                today = now_ist().strftime("%Y-%m-%d")



                score_projection = self.engine.recalculate_daily_score(today)



                cscore = int(score_projection.get("final_score", 0))



                app_controls = self.db.get_device_app_controls(check_adb=False)



                app_locks = {a["app_key"]: bool(a["is_locked"]) for a in app_controls}







                # Check if newer update exists for this device



                latest_rel = self.db.get_latest_app_release(platform="ANDROID", channel="PRODUCTION")



                update_avail = False



                cur_vcode = int(v_code or 0)



                latest_manifest = None



                if latest_rel and latest_rel.get("version_code", 0) > cur_vcode:



                    update_avail = True



                    host_header = self.headers.get("Host", "")



                    if not host_header or "127.0.0.1" in host_header or "localhost" in host_header:



                        host_header = f"{get_local_ip()}:8765"



                    proto = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"



                    dl_path = latest_rel.get("download_url") or f"/api/releases/download/{latest_rel['release_id']}.apk"



                    if not dl_path.startswith("http://") and not dl_path.startswith("https://"):



                        if not dl_path.startswith("/"):



                            dl_path = f"/{dl_path}"



                        dl_url = f"{proto}://{host_header}{dl_path}"



                    else:



                        dl_url = dl_path







                    latest_manifest = {



                        "application": "StrictRecords",



                        "platform": latest_rel.get("platform", "ANDROID"),



                        "versionName": latest_rel.get("version_name"),



                        "version_name": latest_rel.get("version_name"),



                        "versionCode": latest_rel.get("version_code"),



                        "version_code": latest_rel.get("version_code"),



                        "minimumSupportedVersion": latest_rel.get("min_supported_version", 1),



                        "releaseDate": latest_rel.get("release_date"),



                        "releaseNotes": latest_rel.get("release_notes", ""),



                        "mandatory": bool(latest_rel.get("is_mandatory", 0)),



                        "downloadUrl": dl_url,



                        "download_url": dl_url,



                        "sha256": latest_rel.get("sha256_checksum", ""),



                        "sha256_checksum": latest_rel.get("sha256_checksum", ""),



                        "fileSizeBytes": latest_rel.get("file_size_bytes", 0),



                        "file_size_bytes": latest_rel.get("file_size_bytes", 0),



                        "channel": latest_rel.get("channel", "PRODUCTION"),



                        "releaseId": latest_rel.get("release_id"),



                        "release_id": latest_rel.get("release_id"),



                    }







                restr_policies = self.db.get_sidebar_restrictions_status().get("status", {})



                for rk, rv in restr_policies.items():



                    if isinstance(rv, dict):



                        if "desiredEffectiveRestrictionEnabled" in rv:



                            app_locks[rk] = bool(rv["desiredEffectiveRestrictionEnabled"])



                        elif "effectiveRestrictionEnabled" in rv and rv["effectiveRestrictionEnabled"] is not None:



                            app_locks[rk] = bool(rv["effectiveRestrictionEnabled"])



                        elif "locked" in rv:



                            app_locks[rk] = bool(rv["locked"])



                streak_metrics = self.engine.calculate_streak_metrics() if hasattr(self.engine, "calculate_streak_metrics") else {}



                ledger_summary = self.db.get_point_ledger_summary(today) if hasattr(self.db, "get_point_ledger_summary") else {}



                self._send_json({



                    "success": True,



                    "device_id": dev_id,



                    "status": "REGISTERED",



                    "sync_status": "ONLINE",



                    **self.engine.authoritative_score_snapshot(today),



                    "streak_days": int(streak_metrics.get("current_streak", 0)),



                    "point_ledger_summary": ledger_summary,



                    "ledger_receipt": ledger_receipt if ledger_receipt is not None else self.engine.authoritative_score_snapshot(today).get("ledger_receipt"),



                    "privilege_state": "ALLOWED" if cscore >= 80 else "LOCKED",



                    "app_locks": app_locks,



                    "restriction_policies": restr_policies,



                    "update_available": update_avail,



                    "latest_release": latest_manifest if update_avail else None,



                    "server_time": TimeWindowEngine.get_authoritative_time()["server_time"],



                    "profile": self.db.get_active_user_profile(),
                    "prepare_strict_session_check": (
                        {"preparation_id": getattr(self.strict_session_manager, "_active_preparation", "")}
                        if getattr(self, "strict_session_manager", None) and getattr(self.strict_session_manager, "_active_preparation", "")
                        else None
                    ),



                })



            elif path == "/api/releases/overview":



                platform = body.get("platform", "ANDROID")



                channel = body.get("channel", "PRODUCTION")



                self._send_json(self._get_release_overview(platform=platform, channel=channel))



                return



            elif path == "/api/releases/publish":



                if not getattr(self, "_authenticated_admin", None):



                    self._send_json({"error": "Admin authorization required (invalid PIN or session)", "code": "UNAUTHORIZED"}, status=403)



                    return







                notes = str(body.get("release_notes") or body.get("releaseNotes") or "").strip()



                if not notes:



                    self._send_json({



                        "error": "Release notes are required before publishing. Please describe what is new in this release.",



                        "code": "EMPTY_RELEASE_NOTES"



                    }, status=400)



                    return







                channel = str(body.get("channel") or "PRODUCTION").upper()



                mandatory = bool(body.get("is_mandatory") or body.get("mandatory"))



                platform = str(body.get("platform") or "ANDROID").upper()







                staging_id = body.get("staging_id")



                staging_dir = os.path.join(ROOT_DIR, "releases", "staging")



                source_apk = None



                if staging_id and os.path.exists(os.path.join(staging_dir, staging_id)):



                    source_apk = os.path.join(staging_dir, staging_id)



                elif body.get("source_apk_path") and os.path.exists(body.get("source_apk_path")):



                    source_apk = body.get("source_apk_path")



                else:



                    default_candidates = [



                        r"C:\Users\lokad\.strictrecords_build\app\outputs\apk\debug\app-debug.apk",



                        os.path.join(ROOT_DIR, "StrictRecords-debug.apk"),



                        os.path.join(ROOT_DIR, "StrictRoutines-debug.apk"),



                    ]



                    for c in default_candidates:



                        if os.path.exists(c):



                            source_apk = c



                            break







                if not source_apk or not os.path.exists(source_apk):



                    self._send_json({"error": "Source APK file not found.", "code": "FILE_NOT_FOUND"}, status=400)



                    return







                latest_rel = self.db.get_latest_app_release(platform=platform, channel=channel)



                allow_rollback = bool(body.get("allow_rollback", False))



                inspection = inspect_apk(source_apk, current_active_release=latest_rel, allow_rollback=allow_rollback, override_version_code=body.get("version_code"))



                if not inspection.get("valid"):



                    self._send_json({



                        "error": "APK validation failed: " + "; ".join(inspection.get("errors", ["Invalid package"])),



                        "code": "VALIDATION_FAILED",



                        "inspection": inspection



                    }, status=400)



                    return







                v_code = int(body.get("version_code") or inspection["version_code"])



                v_name = body.get("version_name") or inspection["version_name"] or f"1.{v_code}.0"



                min_sdk = inspection["min_sdk_version"]



                signing_cert_fp = inspection["signing_cert_sha256"]



                file_size = inspection["file_size_bytes"]



                sha256_hash = inspection["sha256_checksum"]







                apk_filename = f"strictrecords_v{v_code}.apk"



                releases_dir = os.path.join(ROOT_DIR, "releases")



                os.makedirs(releases_dir, exist_ok=True)



                target_path = os.path.join(releases_dir, apk_filename)







                import shutil



                if os.path.abspath(source_apk) != os.path.abspath(target_path):



                    shutil.copy2(source_apk, target_path)







                rel_data = {



                    "release_id": f"rel_{platform.lower()}_{v_code}",



                    "platform": platform,



                    "version_name": v_name,



                    "version_code": v_code,



                    "min_supported_version": min_sdk,



                    "package_name": "com.strictrecords.app",



                    "release_notes": notes,



                    "download_url": f"/api/releases/download/{apk_filename}",



                    "file_size_bytes": file_size,



                    "sha256_checksum": sha256_hash,



                    "channel": channel,



                    "is_mandatory": mandatory,



                    "signing_cert_sha256": signing_cert_fp,



                    "created_by": "admin",



                }







                res = self.db.publish_app_release(rel_data)







                # Write static manifest.json for direct access



                manifest_file = os.path.join(releases_dir, "manifest.json")



                try:



                    with open(manifest_file, "w", encoding="utf-8") as f:



                        json.dump(rel_data, f, indent=2)



                except Exception as e:



                    logger.warning(f"Error writing manifest.json: {e}")







                # Publish APK_UPDATE_AVAILABLE event to EventBus



                host_header = self.headers.get("Host", "")



                if not host_header or "127.0.0.1" in host_header or "localhost" in host_header:



                    host_header = f"{get_local_ip()}:8765"



                proto = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"



                full_dl_url = f"{proto}://{host_header}{rel_data['download_url']}"







                bus = EventBus.get_instance()



                evt_payload = {



                    "release_id": rel_data["release_id"],



                    "version_name": v_name,



                    "version_code": v_code,



                    "channel": channel,



                    "platform": platform,



                    "download_url": full_dl_url,



                    "sha256": sha256_hash,



                    "file_size_bytes": file_size,



                    "release_notes": notes,



                    "is_mandatory": mandatory,



                }



                bus.publish("APK_UPDATE_AVAILABLE", evt_payload)







                # Broadcast notification ping to connected devices



                try:



                    bus.publish("BROADCAST_NOTIFICATION", {



                        "category": "APP_UPDATE",



                        "title": "StrictRecords update available",



                        "message": f"StrictRecords {v_name} is ready to install.",



                        "release_id": rel_data["release_id"],



                        "version_name": v_name,



                        "version_code": v_code,



                        "download_url": full_dl_url,



                        "timestamp": datetime.now().isoformat()



                    })



                except Exception as b_ex:



                    logger.warning(f"Error publishing broadcast notification: {b_ex}")







                # Record audit log



                try:



                    self.db.record_audit_log(



                        category="RELEASE_MANAGEMENT",



                        action="PUBLISH_APK_RELEASE",



                        severity="INFO",



                        target=rel_data["release_id"],



                        details=f"Published version {v_name} (code {v_code}) on {channel} channel with SHA-256 {sha256_hash[:12]}..."



                    )



                except Exception as ex:



                    logger.warning(f"Error logging publish release audit: {ex}")







                self._send_json({



                    "success": True,



                    "release_id": rel_data["release_id"],



                    "version_name": v_name,



                    "version_code": v_code,



                    "channel": channel,



                    "download_url": full_dl_url,



                    "sha256": sha256_hash,



                    "file_size_bytes": file_size,



                    "message": "Release published successfully."



                })



                return



            elif path == "/api/releases/toggle_active":



                admin_pin = body.get("pin") or body.get("admin_pin")



                if not self.db.verify_admin_pin(admin_pin):



                    self._send_json({"error": "Admin authorization required", "code": "UNAUTHORIZED"}, status=403)



                    return



                rel_id = body.get("release_id")



                active = bool(body.get("is_active", True))



                ok = self.db.toggle_app_release_active(rel_id, active)



                self._send_json({"success": ok, "release_id": rel_id, "is_active": active})



                return



            elif path == "/api/device/update_status":



                dev_id = body.get("device_id", "unknown_device")



                rel_id = body.get("release_id", "")



                v_code = int(body.get("installed_version_code") or body.get("version_code") or 1)



                status = body.get("status", "UP_TO_DATE")



                progress = int(body.get("progress_percent") or 0)



                err = body.get("last_error") or body.get("error")







                ok = self.db.record_device_update_status(



                    device_id=dev_id,



                    release_id=rel_id,



                    installed_version_code=v_code,



                    status=status,



                    progress_percent=progress,



                    last_error=err,



                )







                # Keep devices table synchronized with reported version



                try:



                    rel_row = self.db.get_latest_app_release(platform="ANDROID")



                    now_iso = datetime.now().isoformat()



                    with self.db._connect() as conn:



                        if status == "INSTALLED" or status == "UP_TO_DATE" or (rel_row and v_code >= rel_row.get("version_code", 0)):



                            v_name = rel_row.get("version_name", f"1.{v_code}.0") if rel_row else f"1.{v_code}.0"



                            conn.execute("UPDATE devices SET app_version = ?, app_version_code = ?, last_seen = ? WHERE device_id = ?", (str(v_name), v_code, now_iso, dev_id))



                        elif v_code:



                            conn.execute("UPDATE devices SET app_version_code = ?, last_seen = ? WHERE device_id = ?", (v_code, now_iso, dev_id))



                except Exception as ex:



                    logger.warning(f"Error updating device version in devices table: {ex}")







                bus = EventBus.get_instance()



                bus.publish(



                    "DEVICE_UPDATE_STATUS_CHANGED",



                    {



                        "device_id": dev_id,



                        "release_id": rel_id,



                        "version_code": v_code,



                        "status": status,



                        "progress_percent": progress,



                        "last_error": err,



                    }



                )







                self._send_json({"success": ok, "device_id": dev_id, "status": status})



                return



            elif path == "/api/releases/notify":



                rel = self.db.get_latest_app_release(platform="ANDROID")



                if rel:



                    host_header = self.headers.get("Host", "")



                    if not host_header or "127.0.0.1" in host_header or "localhost" in host_header:



                        host_header = f"{get_local_ip()}:8765"



                    proto = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"



                    dl_path = rel.get("download_url") or f"/api/releases/download/{rel['release_id']}.apk"



                    if not dl_path.startswith("http://") and not dl_path.startswith("https://"):



                        if not dl_path.startswith("/"):



                            dl_path = f"/{dl_path}"



                        dl_path = f"{proto}://{host_header}{dl_path}"



                    bus = EventBus.get_instance()



                    bus.publish("APK_UPDATE_AVAILABLE", {



                        "release_id": rel.get("release_id"),



                        "version_name": rel.get("version_name"),



                        "version_code": rel.get("version_code"),



                        "platform": rel.get("platform", "ANDROID"),



                        "channel": rel.get("channel", "PRODUCTION"),



                        "download_url": dl_path,



                        "sha256": rel.get("sha256_checksum"),



                        "file_size_bytes": rel.get("file_size_bytes"),



                        "release_notes": rel.get("release_notes"),



                        "is_mandatory": bool(rel.get("is_mandatory", False)),



                        "device_id": body.get("device_id"),



                    })



                self._send_json({"success": True, "notified": bool(rel)})



                return



            elif path == "/api/device/apps/toggle":



                if self.db and self.restriction_mgr:



                    self.db.restriction_mgr = self.restriction_mgr



                app_key = body.get("app_key")



                if body.get("all") is not None:



                    locked = bool(body["all"])



                    apps = self.db.set_all_device_app_locks(locked=locked)



                    if self.restriction_mgr:



                        self.restriction_mgr.clear_caches()



                        self.restriction_mgr.check_and_enforce()



                    bus = EventBus.get_instance()



                    bus.publish("RESTRICTIONS_UPDATED", {"all": locked})



                    self._send_json({"success": True, "apps": apps, "status": self.db.get_sidebar_restrictions_status().get("status", {})})



                elif app_key:



                    locked = body.get("locked")



                    clean_key = str(app_key).strip().lower()



                    res = self.db.toggle_device_app_lock(clean_key, locked=locked)



                    apps = self.db.get_device_app_controls(check_adb=False)



                    res["apps"] = apps



                    res["status"] = self.db.get_sidebar_restrictions_status().get("status", {})



                    if self.restriction_mgr:



                        self.restriction_mgr.clear_caches()



                        self.restriction_mgr.check_and_enforce()



                    bus = EventBus.get_instance()



                    bus.publish("RESTRICTIONS_UPDATED", res)



                    self._send_json(res)



                else:



                    self._send_json({"error": "Missing app_key or all parameter"}, status=400)



            elif path in ("/api/restrictions/toggle", "/api/restrictions/sidebar/toggle"):



                if self.db and self.restriction_mgr:



                    self.db.restriction_mgr = self.restriction_mgr



                target = body.get("target") or body.get("service") or body.get("app_key") or "youtube"



                enabled = body.get("enabled")



                if enabled is None and "requestedRestrictionEnabled" in body:



                    enabled = bool(body["requestedRestrictionEnabled"])



                if enabled is None and "locked" in body:



                    enabled = bool(body["locked"])



                if enabled is None:



                    enabled = True



                requested_by = body.get("requested_by", "user")



                admin_pin = body.get("admin_pin")



                if admin_pin and requested_by == "user":



                    requested_by = "admin_override"



                enforce_gate = body.get("enforce_privilege_gate")



                inc_ver = body.get("policyVersion")



                inc_ts = body.get("lastConfirmedAt")



                res = self.db.toggle_sidebar_restriction(



                    target=target,



                    enabled=bool(enabled),



                    requested_by=requested_by,



                    admin_pin=admin_pin,



                    enforce_privilege_gate=bool(enforce_gate) if enforce_gate is not None else None,



                    incoming_version=int(inc_ver) if inc_ver is not None else None,



                    incoming_timestamp=str(inc_ts) if inc_ts is not None else None,



                )



                if self.restriction_mgr:



                    self.restriction_mgr.clear_caches()



                    self.restriction_mgr.check_and_enforce()



                bus = EventBus.get_instance()



                bus.publish("RESTRICTIONS_UPDATED", res)



                self._send_json(res)



            elif path == "/api/restrictions/sync":



                if self.db and self.restriction_mgr:



                    self.db.restriction_mgr = self.restriction_mgr



                service = body.get("service") or body.get("target") or "youtube"



                req_enabled = bool(body.get("requestedRestrictionEnabled", body.get("enabled", True)))



                inc_ver = int(body.get("policyVersion", body.get("version", 0)))



                inc_ts = body.get("lastConfirmedAt")



                src_dev = body.get("device_id", "android_client")



                res = self.db.reconcile_synced_restriction_policy(



                    service=service,



                    requested_restriction_enabled=req_enabled,



                    incoming_version=inc_ver,



                    incoming_timestamp=inc_ts,



                    source_device_id=src_dev,



                )



                self._send_json(res)



            elif path == "/api/system/activate_window":



                restored = False



                tab = body.get("tab")



                open_hourly = bool(body.get("open_hourly_if_active", False))



                try:



                    api = getattr(StrictRecordsRequestHandler, "desktop_api", None)



                    if api and hasattr(api, "show_dashboard"):



                        res = api.show_dashboard(tab=tab, open_hourly_if_active=open_hourly)



                        restored = bool(res.get("success", True))



                    elif api and getattr(api, "_window", None):



                        api._is_visible = True



                        restored = True



                except Exception:



                    pass



                self._send_json({"success": True, "action": "WINDOW_ACTIVATED", "window_restored": restored, "window_visible": True})



            elif path == "/api/system/notify":



                title = body.get("title", "StrictRecords")



                message = body.get("message", "")



                warning = bool(body.get("warning", False))



                category = body.get("category", "")



                shown = False



                try:



                    from windows.modern_toast import show_modern_toast



                    shown = show_modern_toast(title=title, message=message, category=category, warning=warning)



                except Exception:



                    pass



                if not shown:



                    try:



                        from windows.tray_manager import StrictRecordsTrayManager



                        tray = StrictRecordsTrayManager.get_existing_instance()



                        if tray and tray.icon_registered:



                            shown = tray.show_balloon(title, message, warning=warning)



                    except Exception:



                        pass



                self._send_json({"success": True, "shown": shown})



            elif path == "/api/system/enforce_single_instance":



                from windows.single_instance_manager import block_and_terminate_duplicate_processes, get_strictrecords_app_instances



                before_instances = get_strictrecords_app_instances()



                blocked_pids = block_and_terminate_duplicate_processes(current_pid=os.getpid())



                after_instances = get_strictrecords_app_instances()



                self._send_json({



                    "success": True,



                    "action": "ENFORCE_SINGLE_INSTANCE",



                    "detected_count": len(before_instances),



                    "blocked_count": len(blocked_pids),



                    "blocked_pids": blocked_pids,



                    "active_count": len(after_instances),



                    "single_instance_enforced": True,



                })



            elif path == "/api/system/hide_window":



                hidden = False



                show_tip = bool(body.get("show_tip", False))



                try:



                    api = getattr(StrictRecordsRequestHandler, "desktop_api", None)



                    if api and hasattr(api, "hide_to_tray"):



                        res = api.hide_to_tray(show_first_close_tip=show_tip)



                        hidden = bool(res.get("success", True))



                    elif api and getattr(api, "_window", None):



                        api._is_visible = False



                        hidden = True



                except Exception:



                    pass



                self._send_json({"success": True, "action": "WINDOW_HIDDEN_TO_TRAY", "window_hidden": hidden, "window_visible": False})



            elif path == "/api/system/tray/action":



                from windows.tray_manager import (



                    StrictRecordsTrayManager,



                    IDM_STATUS_HEADER,



                    IDM_OPEN_DASHBOARD,



                    IDM_CURRENT_SCORE,



                    IDM_NEXT_HOURLY,



                    IDM_SUBMISSION_STATUS,



                    IDM_DEVICE_SYNC,



                    IDM_YOUTUBE_STATUS,



                    IDM_PAUSE_NOTIFICATIONS,



                    IDM_SETTINGS,



                    IDM_EXIT,



                )



                tray = StrictRecordsTrayManager.get_existing_instance()



                if not tray:



                    self._send_json({"success": False, "error": "Tray manager not initialized"}, status=400)



                else:



                    action_map = {



                        "STATUS_HEADER": IDM_STATUS_HEADER,



                        "OPEN_DASHBOARD": IDM_OPEN_DASHBOARD,



                        "CURRENT_SCORE": IDM_CURRENT_SCORE,



                        "NEXT_HOURLY": IDM_NEXT_HOURLY,



                        "SUBMISSION_STATUS": IDM_SUBMISSION_STATUS,



                        "DEVICE_SYNC": IDM_DEVICE_SYNC,



                        "YOUTUBE_STATUS": IDM_YOUTUBE_STATUS,



                        "PAUSE_NOTIFICATIONS": IDM_PAUSE_NOTIFICATIONS,



                        "TOGGLE_PAUSE_NOTIFICATIONS": IDM_PAUSE_NOTIFICATIONS,



                        "SETTINGS": IDM_SETTINGS,



                        "EXIT": IDM_EXIT,



                        "EXIT_STRICTRECORDS": IDM_EXIT,



                    }



                    cmd_id = body.get("cmd_id")



                    if cmd_id is None:



                        act_str = str(body.get("action", "OPEN_DASHBOARD")).upper()



                        cmd_id = action_map.get(act_str, IDM_OPEN_DASHBOARD)



                    confirm_exit = bool(body.get("confirm", False))



                    res = tray.execute_menu_command(int(cmd_id), confirm_exit=confirm_exit)



                    self._send_json(res)



            elif path == "/api/notifications/pause":



                svc = getattr(self, "notification_service", None)



                paused = body.get("paused")



                if svc:



                    if paused is None:



                        new_state = svc.set_paused(not svc.notifications_paused)



                    else:



                        new_state = svc.set_paused(bool(paused))



                else:



                    new_state = bool(paused)



                self._send_json({"success": True, "notifications_paused": new_state})



            elif path == "/api/system/exit":



                if not self._is_local_request():



                    self._send_json({'success':False,'error':'Request Exit from the local desktop.'},status=403)



                    return



                from windows.tray_manager import StrictRecordsTrayManager, IDM_EXIT



                confirm = bool(body.get("confirm", False))



                pin = body.get("pin") or body.get("key")



                api = getattr(StrictRecordsRequestHandler, "desktop_api", None)



                if pin and api and hasattr(api, "exit_app"):



                    threading.Thread(target=api.exit_app, args=(pin,), daemon=True).start()



                    res = {"success": True, "action": "EXIT_CHALLENGE_REQUIRED"}



                else:



                    tray = StrictRecordsTrayManager.get_existing_instance()



                    if tray:



                        res = tray.execute_menu_command(IDM_EXIT, confirm_exit=confirm)



                    elif api and hasattr(api, "exit_app"):



                        threading.Thread(target=api.exit_app, daemon=True).start()



                        res = {"success": True, "action": "EXIT_CHALLENGE_REQUIRED"}



                    else:



                        res = {"success": False, "error": "Native desktop exit service unavailable."}



                self._send_json(res)



            elif path == "/api/youtube/enable_today":



                res = self.db.enable_youtube_for_today(admin_pin=str(body.get("admin_pin") or body.get("pin") or ""))



                if self.restriction_mgr:



                    from windows.restriction_manager import RestrictionState



                    self.restriction_mgr.state = RestrictionState.NORMAL



                    self.restriction_mgr.lock_screen_visible = False



                    self.restriction_mgr.clear_caches()



                    self.restriction_mgr.check_and_enforce()



                self._send_json(res)



            elif path == "/api/settings/websites/add":



                domain = body.get("domain", "")



                sites = self.db.add_allowed_website(domain)



                if self.restriction_mgr:



                    self.restriction_mgr.clear_caches()



                self._send_json({"success": True, "allowed_websites": sites})



            elif path == "/api/settings/websites/remove":



                domain = body.get("domain", "")



                sites = self.db.remove_allowed_website(domain)



                if self.restriction_mgr:



                    self.restriction_mgr.clear_caches()



                self._send_json({"success": True, "allowed_websites": sites})



            elif path == "/api/settings/websites/mode":



                mode = body.get("mode", "DISTRACTIONS_ONLY")



                self.db.save_system_manager_settings({"website_enforcement_mode": mode})



                if self.restriction_mgr:



                    self.restriction_mgr.clear_caches()



                self._send_json({"success": True, "mode": mode})



            elif path == "/api/settings/apps/add":



                name = body.get("app_name", "")



                pkg = body.get("package_name", "")



                icon = body.get("icon", "📱")



                cat = body.get("category", "Custom App")



                locked = bool(body.get("locked", False))



                res = self.db.add_custom_device_app(name, pkg, icon=icon, category=cat, default_locked=locked)



                self._send_json(res)



            elif path == "/api/settings/apps/remove":



                app_key = body.get("app_key", "")



                res = self.db.remove_custom_device_app(app_key)



                self._send_json(res)



            elif path == "/api/system/memory/optimize":



                if getattr(self, "ram_optimizer", None):



                    res = self.ram_optimizer.optimize(force=True)



                else:



                    res = {"success": False, "error": "Optimizer not running"}



                self._send_json(res)



            elif path == "/api/settings/background_mode":



                bg_enabled = body.get("background_optimized_run")



                auto_ram = body.get("auto_ram_optimization")



                thresh = body.get("ram_threshold_mb")



                updates = {}



                if bg_enabled is not None:



                    updates["background_optimized_run"] = bool(bg_enabled)



                if auto_ram is not None:



                    updates["auto_ram_optimization"] = bool(auto_ram)



                if thresh is not None:



                    updates["ram_threshold_mb"] = int(thresh)



                res = self.db.save_system_manager_settings(updates)



                if getattr(self, "ram_optimizer", None):



                    if bg_enabled is not None:



                        self.ram_optimizer.apply_background_mode(bool(bg_enabled))



                    if auto_ram is not None:



                        self.ram_optimizer.auto_optimize_enabled = bool(auto_ram)



                    if thresh is not None:



                        self.ram_optimizer.threshold_mb = int(thresh)



                self._send_json({"success": True, "settings": res})



            elif path == "/api/notifications/dismiss":



                key = body.get("key") or body.get("notification_key") or ""



                ok = self.db.dismiss_notification_center_event(key)



                if getattr(self, "notification_service", None):



                    self.notification_service.center.dismiss(key)



                self._send_json({"success": ok, "key": key})



            elif path == "/api/notifications/read":



                key = body.get("key") or body.get("notification_key") or ""



                ok = self.db.mark_notification_center_read(key)



                if getattr(self, "notification_service", None):



                    self.notification_service.center.mark_read(key)



                self._send_json({"success": ok, "key": key})



            elif path == "/api/notifications/clear":



                cat = body.get("category", "ALL")



                cnt = self.db.clear_notification_center_category(cat)



                if getattr(self, "notification_service", None):



                    self.notification_service.center.clear_category(cat)



                self._send_json({"success": True, "cleared": cnt})



            elif path == "/api/android/notification/push":



                pkg = body.get("package_name", "")



                app = body.get("app_name", "")



                title = body.get("title", "")



                text = body.get("text", "")



                sens = bool(body.get("is_sensitive", False))



                nid = body.get("notification_id") or f"and_{uuid.uuid4().hex[:8]}"



                res = self.db.save_android_notification(nid, pkg, app, title, text, is_sensitive=sens)



                self.db.save_notification_center_event(



                    notification_key=f"AND_{nid}",



                    title=f"Android · {app}",



                    message=f"{title}: {text}" if text else title,



                    category="ANDROID",



                    source="ANDROID_DEVICE",



                    app_name=app,



                    package_name=pkg,



                )



                if getattr(self, "notification_service", None):



                    self.notification_service.push_notification(



                        key=f"AND_{nid}",



                        title=f"StrictRecords · Android · {app}",



                        message=text or title,



                        category="ANDROID",



                        event_type="ANDROID_MIRROR",



                    )



                self._send_json({"success": True, "notification": res})



            elif path == "/api/android/mirror/settings":



                enabled = body.get("mirror_android_notifications")



                filter_mode = body.get("android_notification_filter")



                updates = {}



                if enabled is not None:



                    updates["mirror_android_notifications"] = bool(enabled)



                if filter_mode is not None:



                    updates["android_notification_filter"] = str(filter_mode)



                res = self.db.save_focus_settings(updates)



                if getattr(self, "notification_service", None):



                    self.notification_service.configure_mirror(



                        enabled=bool(res.get("mirror_android_notifications", False)),



                        sensitive_mode=str(res.get("android_notification_filter", "BLOCK_SENSITIVE")),



                    )



                self._send_json({"success": True, "settings": res})



            elif path == "/api/privilege/youtube/mode":



                mode = body.get("mode", "CONTINUOUS_PROOF_GATED")



                self.db.save_focus_settings({"youtube_privilege_mode": mode})



                if self.restriction_mgr:



                    self.restriction_mgr.clear_caches()



                    self.restriction_mgr.check_and_enforce()



                self._send_json({"success": True, "mode": mode})



            elif path == "/api/device/sync":



                try:



                    dev_id, _account_id = self._get_authenticated_request_device(body)



                except PermissionError as exc:



                    self._send_json({"success": False, "error": str(exc), "code": "UNAUTHORIZED_DEVICE"}, status=403)



                    return



                if not self.connection_manager:



                    self._send_json({"success": False, "error": "Device connection service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                items = body.get("items", [])



                token = body.get("device_token")



                res = self.connection_manager.process_bidirectional_sync(items, device_id=dev_id, pairing_token=token)



                self._send_json(res)



            elif path == "/api/device/transfer/queue":



                try:



                    dev_id, _account_id = self._get_authenticated_request_device(body)



                except PermissionError as exc:



                    self._send_json({"success": False, "error": str(exc), "code": "UNAUTHORIZED_DEVICE"}, status=403)



                    return



                if not self.connection_manager:



                    self._send_json({"success": False, "error": "Device connection service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                direction = body.get("direction", "ANDROID_TO_PC")



                item_type = body.get("item_type", "SCORE_EVENT")



                payload = body.get("payload", {})



                res = self.connection_manager.enqueue_transfer_item(dev_id, direction, item_type, payload)



                self._send_json(res)



            elif path == "/api/device/command/send":



                cmd_type = body.get("command_type", "REQUEST_STATUS")



                dev_id = body.get("device_id", "device_client")



                payload = body.get("payload", {})



                admin_user = body.get("admin_user", "Administrator")



                res = self.connection_manager.dispatch_admin_command(cmd_type, dev_id, payload, admin_user)



                self._send_json(res)



            elif path == "/api/device/command/ack":



                try:



                    dev_id, _account_id = self._get_authenticated_request_device(body)



                except PermissionError as exc:



                    self._send_json({"success": False, "error": str(exc), "code": "UNAUTHORIZED_DEVICE"}, status=403)



                    return



                if not self.connection_manager:



                    self._send_json({"success": False, "error": "Device connection service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                cmd_id = body.get("command_id", "")



                result = body.get("result", {})



                success = bool(body.get("success", True))



                ok = self.connection_manager.ack_device_command(



                    cmd_id,



                    result,



                    success,



                    device_id=dev_id,



                )



                if not ok:



                    self._send_json({"success": False, "acknowledged": False, "error": "Command was not found for this device or is no longer pending."}, status=404)



                    return



                self._send_json({"success": True, "acknowledged": True})



            elif path == "/api/device/unpair":



                dev_id = body.get("device_id", "device_client")



                admin_user = body.get("admin_user", "Administrator")



                res = self.connection_manager.dispatch_admin_command("UNPAIR", dev_id, admin_user=admin_user)



                self._send_json(res)



            elif path == "/api/device/revoke":



                dev_id = body.get("device_id", "device_client")



                admin_user = body.get("admin_user", "Administrator")



                res = self.db.revoke_registered_device(dev_id, admin_user=admin_user)



                self._send_json(res)



            elif path == "/api/device/clear_all":



                admin_user = body.get("admin_user", "Administrator")



                res = self.db.clear_all_device_connections(admin_user=admin_user)



                if self.connection_manager:



                    self.connection_manager.clear_all_states()



                self._send_json(res)



            elif path == "/api/device/reconnect":



                dev_id = body.get("device_id", "device_client")



                transport = body.get("transport", "LOCAL_WIFI")



                if not self.connection_manager:



                    self._send_json({"success": False, "error": "Device connection service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                res = self.connection_manager.detect_device(dev_id, transport=transport)



                self._send_json(res)



            elif path in ("/api/bluetooth/auto/configure", "/api/bluetooth/session/start", "/api/bluetooth/session/cancel", "/api/bluetooth/session/import"):



                self._send_json({



                    "success": False,



                    "enabled": False,



                    "code": "BLUETOOTH_TRANSFER_REMOVED",



                    "error": "Temporary Bluetooth transfer is unavailable. Use local Wi-Fi device pairing.",



                }, status=410)



            elif path == "/api/device/diagnostics":



                dev_id = body.get("device_id", "device_client")



                if not self.connection_manager:



                    self._send_json({"diagnostics": "UNAVAILABLE", "error": "Device connection service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                summary = self.connection_manager.get_device_center_summary(dev_id)



                device = next((item for item in self.db.get_registered_devices_list() if item.get("device_id") == dev_id), None)



                presence = device.get("presence_status", "OFFLINE") if device else "OFFLINE"



                if presence != "ONLINE":



                    summary["state"] = "DISCONNECTED"



                    summary["transport"] = "NONE"



                self._send_json({



                    "diagnostics": "HEALTHY" if presence == "ONLINE" else "OFFLINE",



                    "presence_status": presence,



                    "latency_ms": device.get("latency_ms") if device and presence == "ONLINE" else None,



                    "summary": summary,



                })



            elif path == "/api/restriction/admin_release":



                pin = body.get("pin", "")



                reason = body.get("reason", "Administrator manual override")



                if not self.restriction_mgr:



                    self._send_json({"success": False, "error": "Restriction service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                res = self.restriction_mgr.admin_release(pin=pin, reason=reason, auth_client_key=auth_client_key)



                self._send_json(res, status=200 if res.get("success") else 403)



            elif path == "/api/restriction/request_unlock":



                reason = body.get("reason", "")



                dur = int(body.get("duration_minutes", 60))



                pin = body.get("admin_pin")



                if self.restriction_mgr:



                    res = self.restriction_mgr.request_educational_unlock(reason=reason, duration_minutes=dur, admin_pin=pin)



                else:



                    res = {"success": False, "error": "Restriction service is unavailable.", "code": "SERVICE_UNAVAILABLE"}



                self._send_json(res, status=200 if self.restriction_mgr else 503)



            elif path == "/api/restriction/cancel_unlock":



                if not self.restriction_mgr:



                    self._send_json({"success": False, "error": "Restriction service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                res = self.restriction_mgr.cancel_educational_unlock()



                self._send_json(res)



            elif path == "/api/restriction/simulate_block":



                score = int(body.get("score", 64))



                req = int(body.get("required", 80))



                if not self.restriction_mgr:



                    self._send_json({"success": False, "error": "Restriction service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                self._send_json(self.restriction_mgr.simulate_trigger(score=score, required=req))



            elif path == "/api/restriction/simulate_restore":



                score = int(body.get("score", 85))



                if not self.restriction_mgr:



                    self._send_json({"success": False, "error": "Restriction service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                self._send_json(self.restriction_mgr.simulate_restore(score=score))



            elif path == "/api/restriction/reset":



                if not self.restriction_mgr:



                    self._send_json({"success": False, "error": "Restriction service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                self._send_json(self.restriction_mgr.reset_to_normal())



            elif path == "/api/restriction/dismiss":



                if not self.restriction_mgr:



                    self._send_json({"success": False, "error": "Restriction service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                app_key = body.get("app_key")



                dur_sec = int(body.get("duration_seconds", 300))



                self._send_json(self.restriction_mgr.dismiss_lock_screen(app_key=app_key, duration_seconds=dur_sec))



            elif path == "/api/system/startup":



                enable = bool(body.get("enable", True))



                res = StrictRecordsWindowsAgent.configure_windows_startup(enable=enable)



                self._send_json(res)



            elif path == "/api/strict_session/precheck":



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                self._send_json(self.strict_session_manager.run_precheck())

            elif path == "/api/strict_session/predemo/report":
                if not self.strict_session_manager:
                    self._send_json({"success": False, "error": "Strict-session service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)
                    return
                self._send_json(self.strict_session_manager.get_ios_predemo_report())

            elif path == "/api/strict_session/predemo/run":
                if not self.strict_session_manager:
                    self._send_json({"success": False, "error": "Strict-session service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)
                    return
                test_name = body.get('test', 'all') if isinstance(body, dict) else 'all'
                timeout_sec = float(body.get('timeout_sec', 15.0)) if isinstance(body, dict) else 15.0
                res = self.strict_session_manager.run_ios_predemo_test(test_name=test_name, timeout_sec=timeout_sec)
                self._send_json(res)



            elif path == "/api/strict_session/start":



                if not self._strict_study_local_action():



                    return



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                res = self.strict_session_manager.start_session(



                    policy_updates=body.get("policy"),



                    android_device_id=body.get('android_device_id'),



                    monitoring_consent=body.get('monitoring_consent') is True,



                    preparation_id=body.get('preparation_id'),



                    acknowledge_shutdown=body.get('acknowledge_shutdown', False),



                    grace_period=float(body.get('grace_period', 3.5))



                )



                self._send_json(res, status=200 if res.get("success") else 400)



            elif path == "/api/strict_session/applications/scan":



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service is unavailable."}, status=503)



                    return



                self._send_json(self.strict_session_manager.app_terminator.scan_prohibited_applications(), status=200)



            elif path == "/api/strict_session/applications/close":



                if not self._strict_study_local_action():



                    return



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service is unavailable."}, status=503)



                    return



                scan = self.strict_session_manager.app_terminator.scan_prohibited_applications()



                grace = float(body.get('grace_period', 3.5))



                res = self.strict_session_manager.app_terminator.close_prohibited_applications(



                    scan.get("prohibited_apps", []),



                    grace_period=grace,



                    force=body.get('force', True)



                )



                self._send_json(res, status=200 if res.get("success") else 400)



            elif path in ('/api/strict_session/complete', '/api/strict_session/resume'):



                if not self._strict_study_local_action():



                    return



                if not self.strict_session_manager:



                    self._send_json({'success':False,'error':'Strict Session is unavailable'}, status=503)



                    return



                result = (self.strict_session_manager.complete_study(body.get('session_id')) if path.endswith('/complete')



                          else self.strict_session_manager.resume_study_monitoring(body.get('monitoring_consent') is True,body.get('preparation_id')))



                self._send_json(result, status=200 if result.get('success') else 409)



            elif path == "/api/strict_session/end":



                admin_pin = body.get("admin_pin", "")



                reason = body.get("reason", "Admin requested exit")



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                res = self.strict_session_manager.end_session(admin_pin=admin_pin, reason=reason, auth_client_key=auth_client_key)



                self._send_json(res, status=200 if res.get("success") else 403)



            elif path in ("/api/strict_session/user_end", "/api/strict_session/end_user"):



                if not self._strict_study_local_action():



                    return



                reason = body.get("reason", "User ended session")



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                res = self.strict_session_manager.user_end_session(reason=reason)



                self._send_json(res, status=200 if res.get("success") else 400)



            elif path in ("/api/strict_session/user_reset", "/api/strict_session/reset"):



                if not self._strict_study_local_action():



                    return



                reason = body.get("reason", "User reset session")



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                res = self.strict_session_manager.user_reset_session(reason=reason)



                self._send_json(res, status=200 if res.get("success") else 400)



            elif path in ("/api/strict_session/user_restart", "/api/strict_session/restart"):



                if not self._strict_study_local_action():



                    return



                reason = body.get("reason", "User restarted session")



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                res = self.strict_session_manager.user_restart_session(reason=reason)



                self._send_json(res, status=200 if res.get("success") else 400)



            elif path == "/api/strict_session/policy":



                pol = body.get("policy", {})



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                res = self.strict_session_manager.update_policy(pol)



                self._send_json(res, status=400 if res.get("success") is False else 200)



            elif path == "/api/strict_session/privilege/evaluate":



                if not self.strict_session_manager:



                    self._send_json({"allowed": False, "error": "Strict-session service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                self._send_json(self.strict_session_manager.evaluate_privilege())



            elif path == "/api/strict_session/hourly_proof/submit":



                eid = body.get("evidence_id", "")



                if not self.strict_session_manager:



                    self._send_json({"status": "PENDING", "error": "Strict-session service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                res = self.strict_session_manager.submit_hourly_proof(



                    evidence_id=eid,



                    task_code=body.get("task_code", "HOURLY_CHECKPOINT"),



                    description=body.get("description", "Hourly verification"),



                )



                self._send_json(res, status=400 if res.get("status") != "VALID" else 200)



            elif path == "/api/strict_session/recovery":



                if not self.strict_session_manager:



                    self._send_json({"recovery_needed": True, "error": "Strict-session service is unavailable.", "code": "SERVICE_UNAVAILABLE"}, status=503)



                    return



                self._send_json(self.strict_session_manager.check_for_crash_recovery())



            elif path == "/api/strict_session/android/ack_start":



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service unavailable."}, status=503)



                    return



                res = self.strict_session_manager.ack_android_start(body)



                self._send_json(res)



            elif path in ("/api/strict_session/ios/timer_heartbeat", "/ios-camera/timer_heartbeat"):
                if not self.strict_session_manager:
                    self._send_json({"success": False, "error": "Strict-session service unavailable."}, status=503)
                    return
                cred = body.get("credential") or body.get("device_id")
                pid = body.get("preparation_id") or getattr(self.strict_session_manager, "_active_preparation", "")
                acct = getattr(self.strict_session_manager, "user_id", "")
                try:
                    with self.strict_session_manager.db._connect() as c:
                        c.execute(
                            "UPDATE strict_join_participants SET seen=? WHERE role='IOS' AND (preparation_id=? OR credential_hash=?)",
                            (time.time(), pid, hashlib.sha256(str(cred).encode()).hexdigest())
                        )
                except Exception:
                    pass
                timer_info = {}
                try:
                    timer_info = self.strict_session_manager._companion_timer(pid, acct)
                except Exception:
                    pass
                sess_state = "READY"
                try:
                    sess_state = self.strict_session_manager.get_lifecycle_state().value if hasattr(self.strict_session_manager, "get_lifecycle_state") else str(getattr(self.strict_session_manager, "state", "READY"))
                except Exception:
                    pass
                self._send_json({
                    "type": "IOS_TIMER_HEARTBEAT_ACK",
                    "success": True,
                    "session_id": body.get("session_id") or getattr(self.strict_session_manager, "session_id", None) or "",
                    "connection_id": body.get("connection_id", ""),
                    "sequence_number": body.get("sequence_number", 0),
                    "server_timestamp": time.time(),
                    "authoritative_timer_state": timer_info,
                    "authoritative_session_state": sess_state,
                })

            elif path in ("/api/strict_session/ios/media_stopped", "/api/strict_session/ios/stop"):

                if not self.strict_session_manager:

                    self._send_json({"success": False, "error": "Strict-session service unavailable."}, status=503)

                    return

                source_id = body.get("source_id", "IOS_FRONT_CAMERA")

                ev_id = body.get("event_id") or f"stop_http_{int(time.time())}"

                reason = body.get("reason", "Media stopped by user")

                if source_id == "IOS_FULL_SCREEN_SHARE":

                    try:

                        from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor

                        IPadScreenShareMonitor.get_instance().stop_track(reason=reason, event_id=ev_id, user_initiated=bool(body.get("user_initiated", True)))

                    except Exception:

                        pass

                else:

                    try:

                        from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

                        IPadCameraStreamMonitor.get_instance().record_explicit_stop(

                            source_id=source_id,

                            event_id=ev_id,

                            reason=reason,

                            timestamp=body.get("timestamp") or time.time(),

                            user_initiated=bool(body.get("user_initiated", True)),

                        )

                    except Exception:

                        pass

                self._send_json({

                    "success": True,

                    "type": "IOS_MEDIA_STOPPED_ACK",

                    "event_id": ev_id,

                    "source_id": source_id,

                    "status": "ACKNOWLEDGED",

                    "timestamp": time.time(),

                })



            elif path in ("/api/strict_session/android/prepare_ack", "/api/strict_session/android/ready_ack"):



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service unavailable."}, status=503)



                    return



                res = self.strict_session_manager.ack_android_prepare(body)



                self._send_json(res)



            elif path == "/api/strict_session/android/prepare_check":



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service unavailable."}, status=503)



                    return



                res = self.strict_session_manager.request_android_prepare_check(

                    body.get("preparation_id"),

                    force=bool(body.get("force", True)),

                )



                self._send_json(res)



            elif path == "/api/strict_session/browsers/cleanup":



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service unavailable."}, status=503)



                    return



                res = self.strict_session_manager.get_browser_cleanup_prerequisite_status(

                    body.get("preparation_id"),

                    force_cleanup=True,

                )



                self._send_json(res)



            elif path == "/api/strict_session/android/heartbeat":



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service unavailable."}, status=503)



                    return



                res = self.strict_session_manager.process_android_heartbeat(body)



                self._send_json(res)



            elif path == "/api/strict_session/android/usage_event":



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service unavailable."}, status=503)



                    return



                res = self.strict_session_manager.record_android_app_usage(body)



                self._send_json(res)



            elif path == "/api/strict_session/android/usage_report":



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service unavailable."}, status=503)



                    return



                res = self.strict_session_manager.record_android_usage_report(body)



                self._send_json(res)



            elif path == "/api/strict_session/phase/update":



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict session manager unavailable."}, status=503)



                    return



                phase = str(body.get("phase") or "").strip()



                duration = int(body.get("duration_seconds") or 0)



                reason = str(body.get("reason") or "").strip()



                res = self.strict_session_manager.update_session_phase(phase, duration_seconds=duration, reason=reason)



                self._send_json(res)



            elif path == "/api/strict_session/cloudflare/config":



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict session manager unavailable."}, status=503)



                    return



                res = self.strict_session_manager.configure_cloudflare(body)



                self._send_json(res)



            elif path == "/api/strict_session/cloudflare/restart_tunnel":



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict session manager unavailable."}, status=503)



                    return



                res = self.strict_session_manager.restart_cloudflare_tunnel()



                self._send_json(res)



            elif path == "/api/strict_session/phone_alerts/acknowledge":



                if not self.strict_session_manager:



                    self._send_json({"success": False, "error": "Strict-session service unavailable."}, status=503)



                    return



                event_id = str(body.get("event_id") or "").strip()



                res = self.strict_session_manager.acknowledge_phone_usage_alert(event_id)



                self._send_json(res)



            elif path in ("/api/schedule/validate-email", "/api/email-prebook/validate"):



                from core.email_prebooking_engine import validate_email_schedule



                email_data = body.get("email_data") or body



                category_hint = body.get("category_hint")



                res = validate_email_schedule(email_data, category_hint=category_hint)



                self._send_json({"success": True, **res})



            elif path in ("/api/schedule/conflict-check", "/api/email-prebook/conflict-check"):



                from core.email_prebooking_engine import check_schedule_conflicts



                category = str(body.get("category") or "EXAM_PREBOOK")



                slots = body.get("slots") or []



                res = check_schedule_conflicts(category, slots)



                self._send_json({"success": True, **res})



            elif path in ("/api/schedule/confirm-email-prebook", "/api/email-prebook/confirm"):



                from core.email_prebooking_engine import confirm_email_prebook



                idempotency_key = str(body.get("idempotency_key") or "")



                category = str(body.get("category") or "EXAM_PREBOOK")



                booking_details = body.get("booking_details") or body



                res = confirm_email_prebook(idempotency_key, category, booking_details, device_id="browser_extension")



                self._send_json(res)



            else:



                self._send_json({"error": f"Unknown endpoint {path}"}, status=404)



        except PermissionError as exc:



            self._send_json({"error": str(exc)}, status=403)



        except Exception as exc:



            self._send_json({"error": str(exc)}, status=400)











def _start_udp_discovery_responder(http_port: int = 8765):



    import socket, threading, json



    from core.database import get_local_ip, get_all_lan_ips







    stopped = threading.Event()



    sockets = []



    def _worker() -> None:



        try:



            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)



            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)



            sock.bind(('0.0.0.0', 8766))



            sockets.append(sock)



            while not stopped.is_set():



                data, addr = sock.recvfrom(1024)



                if b"STRICTRECORDS" in data:



                    # Determine the local IP on the same subnet as the requester if possible



                    local_ip = get_local_ip()



                    try:



                        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)



                        probe.connect((addr[0], addr[1]))



                        route_ip = probe.getsockname()[0]



                        probe.close()



                        if route_ip and not route_ip.startswith("127."):



                            local_ip = route_ip



                    except Exception:



                        pass



                    all_ips = get_all_lan_ips()



                    resp = json.dumps({



                        "service": "STRICTRECORDS",



                        "server_endpoint": f"http://{local_ip}:{http_port}",



                        "candidate_endpoints": [f"http://{ip}:{http_port}" for ip in all_ips],



                        "pc_name": socket.gethostname()



                    }).encode('utf-8')



                    sock.sendto(resp, addr)



        except Exception:



            pass



        finally:



            for sock in sockets:



                sock.close()







    t = threading.Thread(target=_worker, daemon=True, name="SR_UDP_Discovery")



    t.start()



    def stop():



        stopped.set()



        for sock in sockets:



            sock.close()



    return stop











def create_server(



    host: str = "0.0.0.0",



    port: int = 8765,



    db_path: Optional[str] = None,



    storage_root: Optional[str] = None,



    db: Optional[StrictRecordsDB] = None,



    engine: Optional[ScoringEngine] = None,



    win_agent: Optional[StrictRecordsWindowsAgent] = None,



) -> ThreadingHTTPServer:



    class LoggedHTTPServer(ThreadingHTTPServer):



        daemon_threads = True



        block_on_close = False



        allow_reuse_address = True







        def handle_error(self, request, client_address):



            exc_type, exc_val, _ = sys.exc_info()



            if exc_type and issubclass(exc_type, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError, OSError)):



                return



            logger.exception("Request failed from %s", client_address)







        def stop_background_services(self):



            if getattr(self, "_workers_stopped", False): return



            self._workers_stopped = True



            for stop in getattr(self, "_worker_stops", []):



                try: stop()



                except Exception: logger.exception("Background cleanup failed")



            from windows.modern_toast import ModernToastManager



            if ModernToastManager._instance is not None:



                ModernToastManager._instance.stop()







        def server_close(self):



            self.stop_background_services()



            super().server_close()







    # Reserve the listening socket before starting any worker. A failed second



    # bind must not leave another scheduler/restriction/optimizer thread running.



    server = LoggedHTTPServer((host, port), StrictRecordsRequestHandler)



    db = db or StrictRecordsDB(db_path=db_path)



    current_sess = getattr(db, "current_process_session_id", None) or getattr(StrictRecordsDB, "active_process_session_id", None)



    if current_sess:



        server.process_session_id = current_sess



        StrictRecordsRequestHandler.process_session_id = current_sess



    fcfg = db.get_focus_settings()



    engine = engine or ScoringEngine(db=db, storage_root=storage_root)



    win_agent = win_agent or StrictRecordsWindowsAgent(db=db, engine=engine)



    conn_mgr = ConnectionPipelineManager(db=db, scoring_engine=engine)



    strict_session_mgr = StrictSessionManager(db=db)



    from core.windows_app_catalog import apply_production_catalog



    apply_production_catalog(db)



    if os.name=='nt':



        try:



            from windows.register_native_host import register_host



            register_host()



        except Exception:



            logger.exception('Native messaging registration failed; Night backup remains pending')



    restriction_mgr = YouTubeRestrictionManager(db=db, engine=engine)



    ram_opt = RamAndCacheOptimizer(db=db, restriction_mgr=restriction_mgr)







    try:



        engine.execute_daily_reset_if_needed()



    except Exception:



        pass







    wav_path = os.path.join(ROOT_DIR, "data", "motivation_track.wav")



    ensure_motivation_wav(wav_path, duration_sec=8)







    try:



        from core.ai_preflight_service import ai_preflight_service



        from core.ai_preflight_scheduler import ai_preflight_scheduler



        ai_preflight_service.db = db



        ai_preflight_scheduler.db = db



        ai_preflight_scheduler.preflight_svc = ai_preflight_service



        ai_preflight_scheduler.start()



    except Exception as e:



        logger.warning(f"Could not start ai_preflight_scheduler: {e}")







    from windows.notification_service import StrictNotificationService



    from windows.submission_window_manager import SubmissionWindowManager



    submission_manager = SubmissionWindowManager.get_instance(db=db, event_bus=EventBus.get_instance())



    notif_svc = StrictNotificationService(



        server_url=f"http://127.0.0.1:{port}",



        enable_android_mirror=bool(fcfg.get("mirror_android_notifications", False)),



        android_sensitive_mode=str(fcfg.get("android_notification_filter", "BLOCK_SENSITIVE")),



        db=db,



        engine=engine,



        submission_manager=submission_manager,



    )







    StrictRecordsRequestHandler.db = db



    StrictRecordsRequestHandler.engine = engine



    StrictRecordsRequestHandler.win_agent = win_agent



    StrictRecordsRequestHandler.connection_manager = conn_mgr



    StrictRecordsRequestHandler.restriction_mgr = restriction_mgr



    if hasattr(StrictRecordsRequestHandler, "desktop_api") and StrictRecordsRequestHandler.desktop_api:



        restriction_mgr.desktop_api = StrictRecordsRequestHandler.desktop_api



    StrictRecordsRequestHandler.strict_session_manager = strict_session_mgr



    StrictRecordsRequestHandler.ram_optimizer = ram_opt



    StrictRecordsRequestHandler.notification_service = notif_svc



    StrictRecordsRequestHandler.wav_path = wav_path







    StrictRecordsRequestHandler.bluetooth_manager = None



    StrictRecordsRequestHandler.bluetooth_auto_sync = None







    StrictRecordsRequestHandler.google_auth = None



    StrictRecordsRequestHandler.cloud_sync_mgr = None







    from core.points_cloud_client import PointsCloudReplica



    points_replica = PointsCloudReplica(db)



    StrictRecordsRequestHandler.points_cloud = points_replica



    points_replica.start()







    stop_discovery = _start_udp_discovery_responder(http_port=port)



    server._worker_stops = [notif_svc.stop, restriction_mgr.stop_watcher, ram_opt.stop_auto_optimizer,



                           strict_session_mgr.shutdown, stop_discovery, points_replica.stop]



    restriction_mgr.start_watcher(poll_interval=1.0)



    ram_opt.start_auto_optimizer(interval_sec=300.0)



    notif_svc.start()



    from windows.strict_companion_tls import attach_companion_tls



    secure_companion=attach_companion_tls(StrictRecordsRequestHandler)



    if secure_companion:server._worker_stops.append(secure_companion)



    try:



        from windows.cloudflare_tunnel_manager import CloudflareTunnelManager, find_cloudflared_binary



        if find_cloudflared_binary():



            cf_mgr = CloudflareTunnelManager.get_instance(db=db, local_port=9443)



            cf_mgr.start_tunnel()



            server._worker_stops.append(cf_mgr.stop_tunnel)



    except Exception as _cf_err:



        logger.warning(f"Could not initialize Cloudflare tunnel: {_cf_err}")







    from windows.strict_ios_camera_gateway import attach_ios_camera



    strict_session_mgr.ios_camera_gateway, strict_session_mgr.ios_camera_configuration_error = attach_ios_camera(strict_session_mgr)



    if strict_session_mgr.ios_camera_gateway:



        server._worker_stops.append(strict_session_mgr.ios_camera_gateway.stop)



    return server











def main() -> None:



    parser = argparse.ArgumentParser(description="StrictRecords Central Server")



    parser.add_argument("--host", default="0.0.0.0")



    parser.add_argument("--port", type=int, default=8765)



    args = parser.parse_args()







    srv = create_server(host=args.host, port=args.port)



    print(f"STRICTRECORDS SERVER running at http://{args.host}:{args.port}")



    while True:



        try:



            srv.serve_forever()



        except KeyboardInterrupt:



            srv.server_close()



            break



        except Exception as e:



            logger.error(f"Server loop recovered from exception: {e}", exc_info=True)



            time.sleep(1)











if __name__ == "__main__":



    main()







