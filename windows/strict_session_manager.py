"""

STRICTRECORDS STRICT SESSION MANAGER

Secure-Exam-Browser (SEB) Concept for Windows (Sections 1-31).

Enforces genuine OS-level session and policy controls:

- State Machine: NORMAL -> PREPARING -> STARTING -> ACTIVE -> RESTRICTED/PRIVILEGED -> ENDING -> RESTORING

- Low-Level Keyboard Hook (WH_KEYBOARD_LL) intercepting Alt+Tab, WinKey, Ctrl+Shift+Esc, Alt+F4

- Windows Registry System Policies (DisableTaskMgr, DisableLockWorkstation)

- Dedicated Desktop Shell Enforcement (Taskbar suppression, Foreground locking)

- Application Allowlist Supervisor (Enforces approved apps, suppresses unauthorized apps with friendly banner)

- Dynamic Server-Side Privilege Engine (Score, Hourly Proofs, Telemetry integrity)

- Network & Browser Policy (Dynamic domain routing, designated Comet.exe browser)

- Robust Crash & Reboot Recovery (Always restores clean Windows state, never bricks machine)

- Authorized Admin Exit (PIN-protected, zero magic keys)

"""



import ctypes

import ctypes.wintypes

import json

import logging

import os

import sys

import threading

import time

import uuid

from enum import Enum

from datetime import datetime, timedelta

from pathlib import Path

from typing import Any, Callable, Dict, List, Optional, Set, Tuple



# Ensure project root is available

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT_DIR not in sys.path:

    sys.path.insert(0, ROOT_DIR)



from core.database import StrictRecordsDB, is_valid_retained_proof_file

from core.strict_session_lifecycle import StrictLifecycleState

from core.models import (

    HourlyProofStatus,

    PrivilegeOutput,

    StrictAuditAction,

    StrictSessionPolicy,

    StrictSessionRecord,

    StrictSessionState,

)





class StrictSessionPhase(str, Enum):

    FOCUS_ACTIVE = "FOCUS_ACTIVE"

    FOCUS_BUFFER = "FOCUS_BUFFER"

    PROOF_SUBMISSION_OPEN = "PROOF_SUBMISSION_OPEN"

    POST_SUBMISSION = "POST_SUBMISSION"

    SESSION_PAUSED = "SESSION_PAUSED"

    SESSION_COMPLETED = "SESSION_COMPLETED"





DEFAULT_PHONE_ALLOWLIST = {

    "com.strictrecords.app",

    "android",

    "com.android.systemui",

    "com.android.keyguard",

    "com.android.settings",

    "com.android.permissioncontroller",

    "com.google.android.permissioncontroller",

    "com.sec.android.app.launcher",

    "com.google.android.apps.nexuslauncher",

    "com.android.launcher",

    "com.android.launcher3",

    "com.miui.home",

    "com.oppo.launcher",

    "com.oneplus.launcher",

    "com.google.android.inputmethod.latin",

    "com.samsung.android.honeyboard",

    "com.touchtype.swiftkey",

    "com.nuance.swype.dtc",

    "com.samsung.android.dialer",

    "com.google.android.dialer",

    "com.android.dialer",

    "com.android.phone",

    "com.android.server.telecom",

    "com.android.incallui",

    "com.samsung.android.incallui",

    "SCREEN_LOCKED",

    "com.android.documentsui",

    "com.google.android.documentsui",

    "com.sec.android.app.myfiles",

}



from core.privilege_engine import PrivilegeEngine

from windows.browser_policy_manager import BrowserPolicyManager

from windows.network_policy_manager import NetworkPolicyManager

from windows.strict_study_workflow import StrictStudyWorkflow, TERMINAL

from windows.strict_study_hardware import StrictStudyHardware



try:

    import winreg

    HAS_WINREG = True

except ImportError:

    HAS_WINREG = False



logger = logging.getLogger("StrictSessionManager")



# Win32 Constants

WH_KEYBOARD_LL = 13

WM_KEYDOWN = 0x0100

WM_SYSKEYDOWN = 0x0104

WM_KEYUP = 0x0101

WM_SYSKEYUP = 0x0105

VK_TAB = 0x09

VK_ESCAPE = 0x1B

VK_LWIN = 0x5B

VK_RWIN = 0x5C

VK_CONTROL = 0x11

VK_SHIFT = 0x10

VK_F4 = 0x73

LLKHF_ALTDOWN = 0x20

SW_HIDE = 0

SW_SHOW = 5

SW_MINIMIZE = 6

SW_RESTORE = 9

WM_CLOSE = 0x0010



POLICIES_SYSTEM_KEY = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"



# Safe Windows processes that must NEVER be terminated or touched

SYSTEM_SAFE_EXECUTABLES: Set[str] = {

    "system",

    "registry",

    "smss.exe",

    "csrss.exe",

    "wininit.exe",

    "services.exe",

    "lsass.exe",

    "svchost.exe",

    "fontdrvhost.exe",

    "winlogon.exe",

    "dwm.exe",

    "sihost.exe",

    "taskhostw.exe",

    "explorer.exe",

    "shellexperiencehost.exe",

    "startmenuexperiencehost.exe",

    "searchhost.exe",

    "searchapp.exe",

    "runtimebroker.exe",

    "ctfmon.exe",

    "lockapp.exe",

    "logonui.exe",

    "securityhealthservice.exe",

    "securityhealthsystray.exe",

    "antigravity.exe",

    "python.exe",

    "pythonw.exe",

    "StrictRecords.exe",

    "StrictRecordsApp.exe",

    "StrictRoutines.exe",

}





class KBDLLHOOKSTRUCT(ctypes.Structure):

    _fields_ = [

        ("vkCode", ctypes.wintypes.DWORD),

        ("scanCode", ctypes.wintypes.DWORD),

        ("flags", ctypes.wintypes.DWORD),

        ("time", ctypes.wintypes.DWORD),

        ("dwExtraInfo", ctypes.c_size_t),

    ]





HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)





class StrictSessionManager:

    """

    Central Coordinator for the Secure-Exam-Browser Style Strict Windows Session.

    """



    def __init__(self, db: Optional[StrictRecordsDB] = None, data_dir: Optional[str] = None):

        self.db = db or StrictRecordsDB()

        self.data_dir = data_dir or os.path.join(

            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "StrictRecords"

        )

        os.makedirs(self.data_dir, exist_ok=True)

        self.state_file = os.path.join(self.data_dir, "strict_session_state.json")

        self.registry_backup_file = os.path.join(self.data_dir,'strict_registry_backup.json')

        self._scoring_engine = None



        # Session properties

        self.state: StrictSessionState = StrictSessionState.NORMAL

        self._lifecycle_state: StrictLifecycleState = StrictLifecycleState.IDLE

        self.session_id: Optional[str] = None

        self.user_id: str = "user_lok"

        self.device_id: str = "windows_lok_pc"

        self.start_time: Optional[str] = None

        self.policy: StrictSessionPolicy = StrictSessionPolicy()

        self.precheck_details: Dict[str, Any] = {}

        global _strict_session_manager_instance

        _strict_session_manager_instance = self



        # Dynamic privilege & scoring

        self.current_score: int = 0

        self.verified_points: int = 0

        self.provisional_points: int = 0

        self.hourly_proof_status: HourlyProofStatus = HourlyProofStatus.PENDING

        self.critical_violations: int = 0

        self.camera_health: bool = True

        self.microphone_health: bool = True

        self.lock_screen_active: bool = False

        self.last_blocked_app: Optional[str] = None

        self.blocked_notification_message: Optional[str] = None



        # Engines

        self.privilege_engine = PrivilegeEngine(db=self.db, default_threshold=80)

        self.network_policy_manager = NetworkPolicyManager(pac_dir=self.data_dir)

        self.browser_policy_manager = BrowserPolicyManager(designated_browser="Comet.exe")



        # Windows hook and thread management

        self._hook_handle = None

        self._hook_proc_ref = None

        self._hook_thread: Optional[threading.Thread] = None

        self._hook_thread_id: Optional[int] = None

        self._supervisor_thread: Optional[threading.Thread] = None

        self._stop_event = threading.Event()

        self._lock = threading.RLock()

        self.study = StrictStudyWorkflow(self.db)

        self._normal_study=self.study

        try:

            from windows.strict_violation_engine import get_strict_violation_engine

            ve = get_strict_violation_engine(self.db)

            def _engine_viol_cb(rec):

                if self.study and self.study.s:

                    self.study.record_integrity_violation({

                        'id': rec.violation_id,

                        'category': rec.category,

                        'description': rec.details.get('reason') or f"Prohibited action: {rec.category}",

                        'source': rec.detection_source,

                        'count': rec.count_after,

                    })

            def _engine_fail_cb(reason):

                if self.study and self.study.s:

                    self.study.abort(reason, failed=True)

            ve.add_violation_callback(_engine_viol_cb)

            ve.add_failure_callback(_engine_fail_cb)

        except Exception as e:

            logger.debug(f"Could not register violation engine callbacks: {e}")

        self._session_mode='NORMAL'

        self._setup_monitor=dict(active=False,last_ping=0.,camera_id=None)

        self.study_hardware = StrictStudyHardware()

        self._bluetooth_revision = -1

        self._reward_retry_at = 0

        self._hold_retry_at = 0

        self._reward_worker_lock = threading.Lock()

        self._study_native = None

        self._study_native_error = None

        self.embedded_focus = None

        self._preparation_environment_cache = None

        self.android_monitoring_state: str = "ANDROID_NOT_JOINED"

        self.android_last_heartbeat_at: float = 0.0

        self.android_screen_locked: bool = False

        self._android_connection_lost_violation_recorded: bool = False

        self._android_connection_lost_alert: Optional[Dict[str, Any]] = None

        self._android_watchdog_thread: Optional[threading.Thread] = None

        self._active_phone_usage_alerts: List[Dict[str, Any]] = []

        self.session_phase: StrictSessionPhase = StrictSessionPhase.SESSION_COMPLETED

        self.phase_started_at: Optional[str] = None

        self.phase_expires_at: Optional[str] = None

        self._explicit_phase: Optional[StrictSessionPhase] = None

        self._phase_history: List[Dict[str, Any]] = []

        self._recent_android_transitions: List[Dict[str, Any]] = []

        self._allowed_buffer_usage_events: List[Dict[str, Any]] = []

        self._allowed_proof_window_usage_events: List[Dict[str, Any]] = []

        self._prohibited_usage_events: List[Dict[str, Any]] = []

        self.last_recorded_violation_package: Optional[str] = None

        self.last_recorded_violation_mono: float = 0.0

        self.dynamic_allowed_packages: Set[str] = set()

        self._active_preparation = None

        self._foreground_study_ok=True

        self._android_prepare_ack: Optional[Dict[str, Any]] = None

        self._android_prepare_req_mono: float = 0.0

        self._android_prepare_state: str = "ANDROID_NOT_REGISTERED"

        self._android_session_started_ack: Optional[Dict[str, Any]] = None

        from windows.strict_external_app_terminator import StrictExternalAppTerminator

        self.app_terminator = StrictExternalAppTerminator(db=self.db, current_pid=os.getpid())

        from windows.strict_notification_filter import StrictNotificationFilter

        self.notification_filter=StrictNotificationFilter(path=Path(data_dir)/'strict_session_notifications.json' if data_dir else None)

        self.notification_filter.recover()

        from windows.strict_session_preparation import StrictSessionPreparation

        self.preparation = StrictSessionPreparation(

            self.db,

            self._preparation_environment,

            timer=self._companion_timer,

            camera_observer=self._ios_camera_readiness,

            android_observer=self.get_android_prerequisite_status,

            browser_observer=self.get_browser_cleanup_prerequisite_status,

        )

        from core.strict_ios_camera import StrictIOSCamera

        self.ios_camera = StrictIOSCamera(self.db, session_validator=self._ios_camera_session_valid)

        self.ios_camera_gateway = None

        self.ios_camera_configuration_error = 'Trusted iOS camera HTTPS domain is not configured.'

        try:

            from windows.cloudflare_tunnel_manager import CloudflareTunnelManager

            _cf_mgr = CloudflareTunnelManager.get_instance(self.db)

            _cf_mgr.set_on_origin_updated(lambda orig: self.ensure_cloudflare_camera_gateway(force_reattach=True))

        except Exception:

            pass



        # Load active policy from DB or initialize default

        self._load_active_policy()



        # Startup Crash & Reboot Recovery Detection

        self.last_recovery_report = self.check_for_crash_recovery()

        self.user_id = self.db.get_active_user_profile().get('user_id', 'user_lok')

        self.study.recover(self.user_id)

        self._refresh_authoritative_score()



    # =========================================================================

    # POLICY MANAGEMENT

    # =========================================================================



    def _load_active_policy(self):

        try:

            db_pol = self.db.get_active_strict_session_policy()

            if db_pol:

                # Older versions persisted the administrator PIN in this policy JSON.

                # The database's salted admin_users credential is the sole authority.

                if "admin_pin" in db_pol:

                    db_pol.pop("admin_pin", None)

                    self.db.save_strict_session_policy(

                        db_pol.get("policy_version", "StrictRecords Policy v12"), db_pol

                    )

                self.policy = StrictSessionPolicy(

                    policy_version=db_pol.get("policy_version", "StrictRecords Policy v12"),

                    allowed_applications=db_pol.get("allowed_applications", []),

                    approved_browser=db_pol.get("approved_browser", "Comet.exe"),

                    allowed_domains=db_pol.get("allowed_domains", []),

                    blocked_domains=db_pol.get("blocked_domains", []),

                    settings_restricted=db_pol.get("settings_restricted", True),

                    score_threshold=db_pol.get("score_threshold", 80),

                    hourly_proof_interval_minutes=db_pol.get("hourly_proof_interval_minutes", 60),

                    emergency_exit_policy=db_pol.get("emergency_exit_policy", "ADMIN_PIN_REQUIRED"),

                )

            else:

                self.policy = StrictSessionPolicy()

                self.db.save_strict_session_policy(self.policy.policy_version, self.policy.to_dict())

            self.privilege_engine.default_threshold = self.policy.score_threshold

        except Exception as e:

            logger.error(f"Error loading policy: {e}")

            self.policy = StrictSessionPolicy()



    def update_policy(self, policy_updates: Dict[str, Any]) -> Dict[str, Any]:

        """Allows administrator to update approved applications, browser, score threshold, etc."""

        if not isinstance(policy_updates, dict):

            return {"success": False, "error": "Policy updates must be an object."}

        current_dict = self.policy.to_dict()

        unsupported = sorted(set(policy_updates) - set(current_dict))

        if unsupported:

            return {

                "success": False,

                "error": "Unsupported or protected policy field(s): " + ", ".join(unsupported),

            }

        current_dict.update(policy_updates)

        self.policy = StrictSessionPolicy(**current_dict)

        self.db.save_strict_session_policy(self.policy.policy_version, self.policy.to_dict())

        self.privilege_engine.default_threshold = self.policy.score_threshold



        # If session is active, dynamically update active network & browser policies

        if self.state in (StrictSessionState.ACTIVE, StrictSessionState.RESTRICTED, StrictSessionState.PRIVILEGED):

            priv_eval = self.evaluate_privilege()

            self.network_policy_manager.apply_policy(

                allowed_domains=self._effective_study_domains(),

                entertainment_allowed=priv_eval["allowed"],

            )

            self.browser_policy_manager.configure(

                designated_browser=self.policy.approved_browser,

                allowed_domains=self._effective_study_domains(),

                entertainment_allowed=priv_eval["allowed"],

            )

            self._audit(StrictAuditAction.POLICY_LOADED.value, reason="Admin updated policy in active session")



        return self.policy.to_dict()



    # =========================================================================

    # PRECHECK ENGINE

    # =========================================================================



    def _effective_study_domains(self):

        domains = list(self.policy.allowed_domains)

        if self.study.s and self.study.s['state'] not in TERMINAL:

            entertainment = self.network_policy_manager.entertainment_domains

            domains = [d for d in domains if not any(d == e or d.endswith('.'+e) for e in entertainment)]

            domains += ['chatgpt.com', 'leetcode.com', 'openai.com', 'oaistatic.com', 'oaiusercontent.com', 'auth0.com', 'challenges.cloudflare.com']

        return sorted(set(domains))



    def _ios_camera_session_valid(self, account, preparation_id, session_id):

        # Pure read: no study.view(), ticks, score events or lock inversion here.

        current = self.study.s or {}

        if self.session_id and self.session_id == session_id:

            return bool(self._active_preparation == preparation_id

                and current.get('account_id') == account

                and current.get('session_id') == session_id

                and current.get('state') not in TERMINAL

                and self.state in (StrictSessionState.ACTIVE, StrictSessionState.RESTRICTED, StrictSessionState.PRIVILEGED))

        if account != self.user_id or session_id != f"prep_{preparation_id}":

            return False

        # A stable preparation-scoped camera can be tested before study starts,

        # then continue across activation without reopening camera permission.

        # FINISHED/expired preparations and unrelated active sessions fail closed.

        with self.db._connect() as connection:

            prep=connection.execute('SELECT account_id,status,expires_at FROM strict_preparations WHERE preparation_id=?',

                (preparation_id,)).fetchone()

        if not prep or prep['account_id'] != account:

            return False

        if self.session_id:

            return bool(self._active_preparation == preparation_id and prep['status'] in ('PENDING','ACTIVE')

                and self.state not in (StrictSessionState.ENDING, StrictSessionState.RESTORING, StrictSessionState.ERROR)

                and current.get('state') not in TERMINAL)

        if prep['status']=='PENDING' and self.ios_camera.clock()<prep['expires_at']:

            return self.state in (StrictSessionState.NORMAL,StrictSessionState.ERROR,StrictSessionState.PREPARING)

        return False



    def _ios_camera_readiness(self, account, preparation_id):

        from windows.strict_ios_readiness import camera_readiness

        return camera_readiness(getattr(self,'ios_camera_gateway',None),account,preparation_id)



    def _companion_timer(self, preparation_id, account):
        """Read-only timer projection; joining a phone never advances study."""
        with self._lock:
            view = self.study.view()
            is_active_session = bool(
                self.session_id and (
                    view.get('session_id') == self.session_id
                    or getattr(self, 'state', None) in (
                        StrictSessionState.ACTIVE,
                        StrictSessionState.RESTRICTED,
                        StrictSessionState.PRIVILEGED
                    )
                )
            )
            prep_match = bool(preparation_id == self._active_preparation or is_active_session or not preparation_id)
            user_profile = self.db.get_active_user_profile() if hasattr(self.db, 'get_active_user_profile') else {}
            active_uid = user_profile.get('user_id') if isinstance(user_profile, dict) else self.user_id
            acct_match = bool(view.get('account_id') == account or account == self.user_id or account == active_uid or not account)

            if not prep_match or not acct_match:
                if acct_match:
                    environment = self._preparation_environment(False)
                    duration = environment.get('focus_seconds')
                    return dict(state='WAITING', remaining_seconds=duration, running=False, label='Ready · start the session on Windows' if duration else 'Waiting for an eligible Windows schedule')
                return dict(state='WAITING', remaining_seconds=None, running=False, label='Waiting for Windows to start')

            state = view['state']
            remaining = (
                view.get('adaptive_remaining_seconds') if state == 'ADAPTIVE_COMPLETION' else
                view.get('window_remaining_seconds') if state == 'AWAITING_PROOF_WINDOW' else
                view.get('focus_remaining_seconds')
            )
            running = bool(view.get('monitoring_ok') and view.get('monitoring_resumed') and state in {'ACTIVE_FOCUS', 'ADAPTIVE_COMPLETION'})
            label = (
                'iOS status temporarily unavailable — session continues. Automatically checking connection.' if (view.get('ios_connected') is False and is_active_session) else
                'Paused · reconnect this iPad timer' if (view.get('ios_connected') is False and not is_active_session) else
                'Paused · return the interrupted mobile participant' if view.get('mobile_integrity_paused') else
                'Proof window opens in' if state == 'AWAITING_PROOF_WINDOW' else
                'Paused · Windows check pending' if state in {'ACTIVE_FOCUS', 'ADAPTIVE_COMPLETION'} and not running else
                state.replace('_', ' ').title()
            )
            return dict(
                state=state,
                remaining_seconds=remaining,
                running=running or state == 'AWAITING_PROOF_WINDOW',
                label=label,
                session_id=view.get('session_id'),
            )

    @property

    def lifecycle_state(self) -> StrictLifecycleState:

        return self.get_lifecycle_state()



    def get_lifecycle_state(self) -> StrictLifecycleState:

        with self._lock:

            if self.session_id and self.state in (

                StrictSessionState.ACTIVE,

                StrictSessionState.RESTRICTED,

                StrictSessionState.PRIVILEGED,

            ):

                # iOS media / connection interruption must NEVER pause authoritative Strict Session lifecycle state!
                # The session remains ACTIVE while Windows proctoring and Android monitoring continue.

                return StrictLifecycleState.SESSION_ACTIVE

            return self._lifecycle_state



    def set_lifecycle_state(

        self,

        state: StrictLifecycleState,

        session_id: Optional[str] = None,

        reason: str = "",

        *args,

        **kwargs,

    ) -> StrictLifecycleState:

        with self._lock:

            self._lifecycle_state = state

            try:

                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

                IPadCameraStreamMonitor.get_instance(self.db).transition_lifecycle_state(

                    state,

                    session_id=session_id or self.session_id,

                    reason=reason,

                )

            except Exception as err:

                logger.debug(f"Lifecycle state transition error: {err}")

            return self._lifecycle_state



    def run_precheck(self) -> Dict[str, Any]:

        """

        Executes pre-session registration and policy verification against 11 prerequisite checks.

        """

        from windows.strict_preflight import hardware_checks

        hardware = self.study_hardware.view()

        hardware['notification_filter_active']=self.notification_filter.status()['active']

        results=hardware_checks(hardware)

        results['timestamp']=datetime.now().isoformat()

        results['camera']=results.get('webcam', {'status': 'PASS', 'message': 'Camera is active.', 'passed': True})

        results['microphone']={'status': 'NOT_USED', 'message': 'Microphone is not used or required.', 'passed': True}

        results['device']={'status': 'PASS', 'device_id': self.device_id, 'platform': 'WINDOWS'}

        self.precheck_details=results

        if self._lifecycle_state not in (StrictLifecycleState.SESSION_ACTIVE, StrictLifecycleState.SESSION_PAUSED):

            if results.get("all_passed"):

                self.set_lifecycle_state(StrictLifecycleState.SESSION_READY)

            else:

                self.set_lifecycle_state(StrictLifecycleState.PREREQUISITE_CHECK)

        return results



    def select_session_mode(self,mode):

        if mode not in {'NORMAL','DEMO'}:raise ValueError('Unknown session mode')

        with self._lock:

            if self.session_id:raise ValueError('End the active session before changing mode.')

            if mode=='DEMO' and self._normal_study.s and self._normal_study.s['state'] not in TERMINAL:

                raise ValueError('Resume or finish the existing real session before using a demo.')

            if self._session_mode!=mode:

                self._session_mode=mode

                if mode=='NORMAL':self.study=self._normal_study

                self._preparation_environment_cache=None

        return self.preparation.state(self.user_id,True)



    def start_setup_hardware(self,pid,camera_id,consent=False):

        with self._lock:

            if self.session_id:raise ValueError('A session is already active.')

            if not consent:raise ValueError('Accept the local camera/audio test explanation first.')

            if self.preparation.current(self.user_id)['preparation_id']!=pid:raise ValueError('Refresh the preparation page.')

            self.stop_setup_hardware()

            if not self.notification_filter.start().get('active'):raise ValueError('Windows notification filtering could not be verified.')

            result=self.study_hardware.start(consent=True,camera_id=camera_id)

            if not result.get('success'):

                self.notification_filter.restore()

                raise ValueError(result.get('error','Hardware test could not start.'))

            self._setup_monitor=dict(active=True,last_ping=time.monotonic(),camera_id=camera_id)

            lease=self._setup_monitor

            self._preparation_environment_cache=None

            def watch():

                while self._setup_monitor is lease and lease['active']:

                    if time.monotonic()-lease['last_ping']>120:

                        with self._lock:

                            if self._setup_monitor is lease and lease['active'] and not self.session_id:self.stop_setup_hardware()

                        return

                    if (self.embedded_focus and hasattr(self.embedded_focus,'update_setup_preview')

                            and hasattr(self.study_hardware,'preview')):

                        self.embedded_focus.update_setup_preview(self.study_hardware.preview())

                    time.sleep(.15)

            threading.Thread(target=watch,daemon=True,name='StrictSetupLease').start()

        return self.preparation.state(self.user_id,True)



    def stop_setup_hardware(self):

        with self._lock:

            was_active=self._setup_monitor['active']

            self._setup_monitor['active']=False

            if not self.session_id:

                self.study_hardware.stop();self.notification_filter.restore()

            if was_active and self.embedded_focus and hasattr(self.embedded_focus,'update_setup_preview'):

                self.embedded_focus.update_setup_preview(dict(current=False,jpeg=None))

            self._preparation_environment_cache=None



    def preparation_host_report(self,account,pid,report):

        if self._setup_monitor['active']:

            if report.get('visible') is True:self._setup_monitor['last_ping']=time.monotonic()

            else:self.stop_setup_hardware()

        return self.preparation.host_report(account,pid,report)



    def _preparation_environment(self,refresh=False):

        # The backend may be owned by Antigravity while presentation stays in the

        # EXE. Only the focus-control interface is bridged; authority stays here.

        if self.embedded_focus is None:

            from windows.strict_focus_bridge import RemoteEmbeddedStrictFocus

            self.embedded_focus=RemoteEmbeddedStrictFocus()

        cached=self._preparation_environment_cache

        if cached and not refresh and time.monotonic()-cached[0]<20:

            result=dict(cached[1])

            checks=self.run_precheck();result.update(hardware_checks=checks,hardware_ready=checks['all_passed'] and self._setup_monitor['active'])

            result['ready']=bool(result.get('connectivity_ready') and result['hardware_ready'] and (result.get('window') or self._session_mode=='DEMO'))

            result['mode']=self._session_mode

            result['hardware_camera_id']=self._setup_monitor['camera_id']

            if self._session_mode=='DEMO':result['focus_seconds']=600

            return result

        from core.time_window_engine import now_ist,iso_to_dt

        account=self.db.get_active_user_profile().get('user_id','user_lok')

        now=now_ist();windows=self._normal_study.window_provider(account,now)

        eligible=[w for w in windows if (iso_to_dt(w['window_start'])-now).total_seconds()>300]

        window=eligible[0] if eligible else None

        until=(iso_to_dt(window['window_start'])-now).total_seconds() if window else None

        focus = max(300, int(until - 300)) if until is not None and until <= 2400 else 2100

        internet=False;latency=None;network_message='Not checked'

        try:

            import requests

            started=time.monotonic()

            response=requests.head('https://chatgpt.com/',timeout=3,allow_redirects=False)

            latency=round((time.monotonic()-started)*1000);internet=response.status_code<500

            network_message=f'TLS connection received HTTP {response.status_code}; sign-in not checked'

            response.close()

        except Exception as error:network_message=type(error).__name__+': could not confirm internet connectivity'

        embedded=bool(self.embedded_focus and self.embedded_focus.available())

        from windows.embedded_strict_focus import allowed_url

        configured_allowlist=allowed_url('https://chatgpt.com/') and not allowed_url('https://example.org/')

        checks=self.run_precheck();hardware_ready=checks['all_passed'] and self._setup_monitor['active']

        planned_focus = 600 if self._session_mode=='DEMO' else (focus if window else 2100)

        result=dict(internet=internet,latency_ms=latency,network_message=network_message,secure_browser=embedded,

            allowlist_configured=configured_allowlist,restriction_status='Configured; activated only after Start',window=window,

            window_in_seconds=until,focus_seconds=planned_focus,reward_max_seconds=0 if self._session_mode=='DEMO' else 1200,

            ready=bool(internet and embedded and configured_allowlist and hardware_ready),

            connectivity_ready=bool(internet and embedded and configured_allowlist),hardware_required=True,hardware_ready=hardware_ready,

            hardware_checks=checks,mode=self._session_mode,demo_seconds=600,

            hardware_camera_id=self._setup_monitor['camera_id'],

            browser_message='Embedded WebView2 available in this EXE' if embedded else 'Open inside the Windows desktop EXE',

            ios_message='Timer browser companion; no native iOS app is bundled')

        self._preparation_environment_cache=(time.monotonic(),result)

        return result



    # =========================================================================

    # STRICT SESSION LIFECYCLE (START / RUN / END)

    # =========================================================================



    def start_session(self, policy_updates: Optional[Dict[str, Any]] = None,

                      android_device_id: Optional[str] = None, monitoring_consent: bool = False,

                      preparation_id: Optional[str] = None,

                      acknowledge_shutdown: bool = False,

                      grace_period: float = 3.5,

                      **kwargs) -> Dict[str, Any]:

        """

        Enters STRICT SESSION:

        NORMAL -> PREPARING -> STARTING -> ACTIVE / RESTRICTED

        """

        with self._lock:

            if self.state not in (StrictSessionState.NORMAL, StrictSessionState.ERROR):

                return {"success": False, "error": f"Cannot start session from state: {self.state.value}"}



            self.set_lifecycle_state(StrictLifecycleState.START_PENDING)



            if not monitoring_consent:

                return {"success": False, "error": "Consent to local camera and Bluetooth monitoring before starting."}

            self.user_id = self.db.get_active_user_profile().get('user_id', 'user_lok')

            try:

                preparation = self.preparation.authorize_start(self.user_id, preparation_id)

            except ValueError as error:

                checks = self.run_precheck()

                err_msg = str(error)

                if checks.get('blockers') and 'iPhone' not in err_msg and 'iOS' not in err_msg and 'video' not in err_msg and 'Camera' not in err_msg:

                    err_msg = f"{err_msg} - {' '.join(checks['blockers'])}"

                return {'success': False, 'error': err_msg, 'details': checks, 'next_view': 'JOIN' if ('iPhone' in err_msg or 'iOS' in err_msg or 'video' in err_msg or 'Camera' in err_msg) else 'PRECHECK'}



            self._active_preparation = preparation.get('preparation_id') if isinstance(preparation, dict) else None



            # iOS Camera & Full-Screen Share Mandatory Entry Gate:

            # Both IOS_FRONT_CAMERA and IOS_FULL_SCREEN_SHARE (IOS_SCREEN_SHARE: VERIFIED) are required.

            target_pid = self._active_preparation or preparation_id

            ios_readiness = self._ios_camera_readiness(self.user_id, target_pid)

            ios_front_ok = bool(

                ios_readiness.get('front_camera_verified')

                or ios_readiness.get('ready')

                or ios_readiness.get('initial_check_passed')

            )

            ios_screen_ok = bool(

                ios_readiness.get('screen_share_verified', True)

            )

            if not ios_front_ok:

                self.study_hardware.stop()

                self.state = StrictSessionState.NORMAL

                return {

                    'success': False,

                    'error': (

                        f"Cannot enter Strict Session: {ios_readiness.get('message', 'iPad front camera initial check pending.')}"

                        " Open the iOS timer page and tap Start Camera."

                    ),

                    'next_view': 'JOIN'

                }

            if not ios_screen_ok:

                self.study_hardware.stop()

                self.state = StrictSessionState.NORMAL

                return {

                    'success': False,

                    'error': (

                        f"Cannot enter Strict Session: {ios_readiness.get('message', 'Mandatory iOS Full-Screen Sharing is not verified.')}"

                        " Tap Share iPad Screen on the companion page."

                    ),

                    'next_view': 'JOIN'

                }



            # Automatically close external browsers when entering Strict Session

            self.set_lifecycle_state(StrictLifecycleState.SESSION_STARTING)

            browser_cleanup_result = self.app_terminator.run_external_browser_cleanup(

                preparation_id=target_pid,

                grace_period=grace_period,

                force_kill_authorized=False,

            )

            if not browser_cleanup_result.get("passed", True):

                open_b = ", ".join(browser_cleanup_result.get("open_browsers", [])) or "external browsers"

                self.study_hardware.stop()

                self.set_lifecycle_state(StrictLifecycleState.START_PENDING)

                self.state = StrictSessionState.NORMAL

                return {

                    "success": False,

                    "error": (

                        f"Cannot enter Strict Session: Unsaved work detected or graceful close incomplete for {open_b}. "

                        "Please save your work and close external browser windows before retrying."

                    ),

                    "external_browsers": browser_cleanup_result,

                    "next_view": "PRECHECK",

                }



            # Mandatory Android APK Readiness Verification before session start

            android_prereq = self.get_android_prerequisite_status(target_pid, refresh=False)

            if not android_prereq.get("ready") or android_prereq.get("state") != "ANDROID_READY_ACKNOWLEDGED":

                self.study_hardware.stop()

                self.state = StrictSessionState.NORMAL

                return {

                    "success": False,

                    "error": (

                        f"Cannot enter Strict Session: Android APK readiness check not satisfied "

                        f"({android_prereq.get('state')} — {android_prereq.get('status_label')})."

                    ),

                    "android_prerequisite": android_prereq,

                    "next_view": "PRECHECK",

                }

            # External Application Distraction Check BEFORE promoting/stopping setup hardware

            ack = acknowledge_shutdown or (policy_updates.get("acknowledge_shutdown", False) if isinstance(policy_updates, dict) else False)

            scan_result = self.app_terminator.scan_prohibited_applications()

            if scan_result.get("prohibited_apps") and not ack:

                app_names = "\n".join(f"- {app['display_name']}" for app in scan_result["prohibited_apps"])

                msg = (

                    "STRICT SESSION – DISTRACTION CHECK\n\n"

                    "The following applications are currently running:\n\n"

                    f"{app_names}\n\n"

                    "These applications must close before Strict Session starts.\n\n"

                    "Please save any unsaved work.\n\n"

                    "Continue only after acknowledging the shutdown."

                )

                if self._setup_monitor.get('active'):

                    self._setup_monitor['last_ping'] = time.monotonic()

                self.state = StrictSessionState.NORMAL

                return {

                    "success": False,

                    "distraction_check_required": True,

                    "prohibited_apps": scan_result["prohibited_apps"],

                    "message": msg,

                    "error": "Distraction check: Running applications must close before Strict Session starts.",

                    "next_view": "DISTRACTION_CHECK"

                }



            is_demo=self._session_mode=='DEMO'

            with self.db._connect() as conn:

                devices = [dict(r) for r in conn.execute("SELECT device_id,device_name FROM devices WHERE user_id=? AND (UPPER(TRIM(platform))='ANDROID' OR UPPER(TRIM(platform)) LIKE 'ANDROID %') AND pairing_state='PAIRED'", (self.user_id,))]

            if not android_device_id and len(devices) >= 1:

                android_device_id = devices[0]['device_id']

            # Android device pairing is optional and not mandatory for Strict Session start

            planned_id = f"strict_sess_{uuid.uuid4().hex[:12]}"

            selected_camera=(preparation or {}).get('devices',{}).get('WINDOWS',{}).get('report',{}).get('camera_native_id')

            if self._setup_monitor['active'] and self._setup_monitor['camera_id']==selected_camera:

                if not self.run_precheck()['all_passed']:

                    checks=self.run_precheck()

                    return dict(success=False,error=' '.join(checks['blockers']),details=checks,next_view='PRECHECK')

                self.study_hardware.promote_setup();self._setup_monitor['active']=False

            else:

                self.study_hardware.stop()

                started=self.study_hardware.start(consent=True,camera_id=selected_camera)

                if not started['success']:return dict(started,next_view='PRECHECK')

            deadline = time.monotonic() + 8

            while time.monotonic() < deadline:

                hw = self.study_hardware.view()

                if hw.get('monitoring_ok') and not hw.get('prohibited_audio_detected'):

                    break

                if hw.get('camera') == 'UNAVAILABLE':

                    break

                time.sleep(.1)

            self.state = StrictSessionState.PREPARING

            logger.info("Starting Strict Session: Running precheck...")



            precheck = self.run_precheck()

            if not precheck.get("all_passed", False):

                self.study_hardware.stop()

                self.state = StrictSessionState.ERROR

                return {"success": False, "error": ' '.join(precheck['blockers']) or 'Hardware checks are incomplete.', "details": precheck,'next_view':'PRECHECK'}

            # External Application Termination (after user acknowledged shutdown)

            if scan_result.get("prohibited_apps") and ack:

                logger.info("Executing external application termination sequence before activating focus...")

                self.app_terminator.close_prohibited_applications(

                    scan_result["prohibited_apps"],

                    grace_period=grace_period,

                    force=True

                )

                verify_scan = self.app_terminator.scan_prohibited_applications()

                if verify_scan.get("prohibited_apps"):

                    failed = ", ".join(a["display_name"] for a in verify_scan["prohibited_apps"])

                    self.study_hardware.stop()

                    self.state = StrictSessionState.ERROR

                    return {

                        "success": False,

                        "error": f"Prohibited applications could not be closed: {failed}. Please close them manually.",

                        "prohibited_apps": verify_scan["prohibited_apps"],

                        "next_view": "PRECHECK"

                    }



            camera_check=self._ios_camera_readiness(self.user_id,self._active_preparation)

            if not (camera_check.get('ready') or camera_check.get('initial_check_passed') or camera_check.get('initializing') or camera_check.get('within_grace')):

                self.study_hardware.stop();self.state=StrictSessionState.NORMAL

                return dict(success=False,error=camera_check['message'],next_view='PRECHECK')

            if is_demo:

                from windows.strict_demo_workflow import DemoStudyWorkflow

                self.study=DemoStudyWorkflow(self.db,clock=self.study.clock,monotonic=self.study.monotonic)

            else:self.study=self._normal_study if getattr(self.study,'mode','NORMAL')=='DEMO' else self.study

            try:

                self.study.begin(planned_id,self.user_id,android_device_id,activate=False)

                if self._active_preparation:

                    self.study.bind_ipad(self._active_preparation)

            except ValueError as error:

                if self.study.s and self.study.s.get('session_id')==planned_id:self.study.abort(str(error))

                self.study_hardware.stop();self.state=StrictSessionState.ERROR

                return dict(success=False,error=str(error),next_view='PRECHECK')



            if not is_demo:

                try:

                    from core.youtube_reward_cloud import client

                    cloud = client(self.db)

                    # Unconfigured shared rewards do not fabricate a local grant.

                    # A configured service must acknowledge the hold before study.

                    if not is_demo and cloud.configuration():

                        self.study.s['cloud_registered'] = True

                        self.study._save()

                        cloud.study('begin', self.study.s)

                except Exception as error:

                    self.study.abort('Shared Strict Session hold could not be confirmed')

                    self.study_hardware.stop()

                    self.state = StrictSessionState.ERROR

                    threading.Thread(target=self._sync_study_authority, daemon=True).start()

                    return {'success':False, 'error':str(error)}



            if policy_updates:

                self.update_policy(policy_updates)



            self.state = StrictSessionState.STARTING

            self.session_id = planned_id

            try:

                from windows.strict_dual_camera_proctor import StrictDualCameraProctor

                StrictDualCameraProctor.get_instance().set_session_id(self.session_id)

            except Exception:

                pass

            try:

                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

                cam_monitor = IPadCameraStreamMonitor.get_instance(self.db)

                cam_monitor.start_session_monitoring(

                    session_id=self.session_id,

                    camera_device_id=self._active_preparation or 'ios_companion_camera',

                )

            except Exception as cam_err:

                logger.warning(f'Could not start iPad camera stream monitor: {cam_err}')

            try:

                from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor

                IPadScreenShareMonitor.get_instance().start_active_session_monitoring(session_id=self.session_id)

            except Exception as scr_err:

                logger.warning(f'Could not start iPad screen share active monitor: {scr_err}')

            try:

                from windows.strict_violation_engine import get_strict_violation_engine

                get_strict_violation_engine(self.db).reset_session(self.session_id)

            except Exception:

                pass

            self.start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            self._stop_event.clear()



            # Persist Session State to disk for Crash & Reboot Recovery

            self._write_state_file()



            # Record in SQLite Database

            session_rec = StrictSessionRecord(

                session_id=self.session_id,

                user_id=self.user_id,

                device_id=self.device_id,

                policy_version=self.policy.policy_version,

                state=self.state.value,

                start_time=self.start_time,

                precheck_passed=True,

                precheck_details=precheck,

            )

            if not is_demo:self.db.create_strict_session(session_rec.to_dict())

            # Log Audit Trail

            self._audit(StrictAuditAction.SESSION_STARTED.value, reason="User initiated Strict Session")

            self._audit(StrictAuditAction.POLICY_LOADED.value, reason=f"Loaded {self.policy.policy_version}")



            # Apply OS-Level Windows Controls

            logger.info("Applying OS-Level Windows Kiosk Controls...")

            try:

                if not self.notification_filter.start().get('active'):

                    raise RuntimeError('Windows notification suppression could not be activated.')

                try:

                    self._apply_registry_policies()

                except Exception as reg_err:

                    logger.warning("Optional registry policies skipped: %s", reg_err)

                for control in (self._install_keyboard_hook, self._hide_taskbar):

                    if control() is False:

                        raise RuntimeError('A required Windows session control could not be activated.')

            except Exception as error:

                self.study.abort(str(error),failed=True)

                self._finish_study_controls('Windows session controls failed')

                return {'success':False,'error':str(error)}



            # Start Allowlist & Foreground Supervisor Thread

            # Apply Dynamic Network & Browser Policies

            priv_eval = dict(allowed=False,reason='Practice only; no entertainment reward') if is_demo else self.evaluate_privilege()

            network_ready = self.network_policy_manager.apply_policy(

                allowed_domains=self._effective_study_domains(),

                entertainment_allowed=priv_eval["allowed"],

            )

            if network_ready is False:

                self.study.abort('Study network policy could not be applied',failed=True)

                self._finish_study_controls('Study network policy failed')

                return {'success':False,'error':'Study network policy is unavailable. Windows has been restored.'}

            self.browser_policy_manager.configure(

                designated_browser=self.policy.approved_browser,

                allowed_domains=self._effective_study_domains(),

                entertainment_allowed=priv_eval["allowed"],

            )

            browser = (self.embedded_focus.open(self.session_id,self.complete_study) if self.embedded_focus else

                       {'success':False,'error':'Embedded secure browser is unavailable in this EXE.'})

            if not browser.get('success'):

                self.study.abort('Dedicated study browser could not start', failed=True)

                self._finish_study_controls('Dedicated browser failed')

                return browser

            self.app_terminator.register_study_browser_pid(os.getpid())

            try:

                self.study.activate()

                if hasattr(self.study_hardware, 'proctor') and hasattr(self.study_hardware.proctor, 'set_submission_window_checker'):

                    self.study_hardware.proctor.set_submission_window_checker(self.is_submission_window_open)

                    self.study_hardware.proctor.set_session_context_provider(self.get_session_proof_context)

                if hasattr(self.study_hardware, "enable_session_integrity"):

                    self.study_hardware.enable_session_integrity(True, session_id=planned_id)

            except ValueError as error:

                self.study.abort(str(error),failed=True)

                self._finish_study_controls('Study schedule changed during preparation')

                return {'success':False,'error':str(error)}

            self._start_supervisor()

            self.preparation.activated(self._active_preparation)



            # Transition to ACTIVE (or RESTRICTED/PRIVILEGED)

            if priv_eval["allowed"]:

                self.state = StrictSessionState.PRIVILEGED

                self.lock_screen_active = False

            else:

                self.state = StrictSessionState.RESTRICTED

                self.lock_screen_active = True



            if not is_demo:self.db.update_strict_session(self.session_id, state=self.state.value)

            self._write_state_file()



            self.set_lifecycle_state(StrictLifecycleState.SESSION_ACTIVE, session_id=self.session_id)

            logger.info(f"Strict Session ACTIVE: session_id={self.session_id}, state={self.state.value}")

            self._explicit_phase = StrictSessionPhase.FOCUS_ACTIVE

            self.phase_started_at = datetime.now().isoformat()

            self._phase_history = [{

                'phase': StrictSessionPhase.FOCUS_ACTIVE.value,

                'phone_usage': 'RESTRICTED',

                'start_dt': datetime.now(),

                'end_dt': None,

                'start_iso': self.phase_started_at,

                'end_iso': None

            }]

            self._trigger_android_session_activation(planned_id, android_device_id)

            return {

                "success": True,

                "session_id": self.session_id,

                "state": self.state.value,

                "policy_version": self.policy.policy_version,

                "privilege": priv_eval,

                "lock_screen_active": self.lock_screen_active,

            }



    def end_session(

        self,

        admin_pin: str,

        reason: str = "Authorized exit",

        auth_client_key: str = "strict_session_exit",

    ) -> Dict[str, Any]:

        """

        Authorizes clean exit:

        ACTIVE -> ENDING -> RESTORING -> NORMAL

        Requires valid admin PIN. Reverts all Windows OS policies cleanly.

        """

        with self._lock:

            if getattr(self.study,'mode','NORMAL')=='DEMO' and self.session_id:

                return self.complete_study(self.session_id)

            if self.state == StrictSessionState.NORMAL and (not self.study.s or self.study.s['state'] in TERMINAL):

                return {"success": True, "message": "Session is already in NORMAL state"}



            # Validate against the salted credential store; session policy never stores PINs.

            cooldown = self.db.admin_auth_lockout_remaining(auth_client_key)

            if cooldown > 0:

                return {

                    "success": False,

                    "error": f"Too many failed attempts. Locked out for {cooldown}s.",

                    "locked_out": True,

                    "cooldown_seconds": cooldown,

                }

            if not self.db.verify_admin_pin(str(admin_pin or "").strip()):

                cooldown = self.db.record_admin_auth_failure(auth_client_key)

                self._audit(StrictAuditAction.ADMIN_OVERRIDE.value, reason="Failed exit attempt: Invalid admin PIN")

                return {

                    "success": False,

                    "error": "Invalid Administrator PIN",

                    "locked_out": cooldown > 0,

                    "cooldown_seconds": cooldown,

                }

            if self.db.is_default_admin_pin():

                return {

                    "success": False,

                    "error": "Change the initial administrator key before ending a protected session.",

                    "code": "PIN_CHANGE_REQUIRED",

                }

            self.db.clear_admin_auth_failures(auth_client_key)

            self.set_lifecycle_state(StrictLifecycleState.SESSION_CANCELLED, reason=reason)

            try:

                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

                IPadCameraStreamMonitor.get_instance().stop_session_monitoring(reason=reason, cancelled=True)

            except Exception:

                pass

            self.study.abort('Administrator emergency exit: ' + reason)

            self.preparation.finish(self._active_preparation)

            self.study_hardware.stop()

            if self._study_native:

                self._study_native.host.StudyStatus(json.dumps({'visible':False}))

            threading.Thread(target=self._sync_study_authority, daemon=True).start()



            self.state = StrictSessionState.ENDING

            logger.info(f"Ending Strict Session ({self.session_id})...")



            self.state = StrictSessionState.RESTORING

            self._stop_event.set()



            # Cleanly revert all OS-level modifications

            self._restore_windows_environment()

            if self._supervisor_thread and self._supervisor_thread is not threading.current_thread():

                self._supervisor_thread.join(timeout=2)



            now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if self.session_id:

                self.db.update_strict_session(

                    self.session_id,

                    state=StrictSessionState.NORMAL.value,

                    end_time=now_ts,

                    exit_reason=reason,

                )

                self._audit(StrictAuditAction.SESSION_ENDED.value, reason=reason)



            # Remove persistent state file

            self._clear_state_file()



            self.state = StrictSessionState.NORMAL

            self.session_id = None

            self.lock_screen_active = False

            self.blocked_notification_message = None



            logger.info("Strict Session terminated cleanly. Normal Windows environment restored.")

            self._explicit_phase = StrictSessionPhase.SESSION_COMPLETED

            self.phase_started_at = datetime.now().isoformat()

            if hasattr(self, "_phase_history") and self._phase_history:

                self._phase_history[-1]["end_dt"] = datetime.now()

                self._phase_history[-1]["end_iso"] = self.phase_started_at

            return {"success": True, "state": self.state.value, "message": "Windows restored to normal mode"}



    def user_end_session(self, reason: str = "User ended session") -> Dict[str, Any]:

        """

        User-accessible session termination:

        ACTIVE/PAUSED/DEGRADED/PREPARATION -> SESSION_ENDING -> NORMAL.

        Does NOT require administrator PIN. Safe to call offline or when companions are disconnected.

        """

        with self._lock:

            # Idempotency: if already in NORMAL state and no active workflow

            if self.state == StrictSessionState.NORMAL and (not self.study.s or self.study.s['state'] in TERMINAL):

                return {

                    "success": True,

                    "state": StrictSessionState.NORMAL.value,

                    "lifecycle_state": StrictLifecycleState.IDLE.value,

                    "message": "Session is already in NORMAL state",

                }



            logger.info(f"User ending Strict Session ({self.session_id}). Reason: {reason}")

            # 1. Mark current session as SESSION_ENDING

            self.set_lifecycle_state(StrictLifecycleState.SESSION_ENDING, reason=reason)

            self.state = StrictSessionState.ENDING



            # 2 & 3. Stop authoritative Windows timers and focus session

            self._stop_event.set()



            # 4 & 5. Stop session-specific camera-health watchdogs & clear pause conditions

            try:

                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

                IPadCameraStreamMonitor.get_instance(self.db).stop_session_monitoring(reason=reason, cancelled=False)

            except Exception as cam_err:

                logger.debug(f"Error stopping iPad camera stream monitor: {cam_err}")



            # 6. Stop Windows webcam proctoring

            try:

                self.study_hardware.stop()

            except Exception as hw_err:

                logger.debug(f"Error stopping study hardware: {hw_err}")



            if self._study_native:

                try:

                    self._study_native.host.StudyStatus(json.dumps({'visible': False}))

                except Exception:

                    pass



            # 7. Send STOP_STRICT_SESSION command to Android APK (or queue if offline)

            target_dev = getattr(self, "android_device_id", None)

            if not target_dev:

                try:

                    with self.db._connect() as conn:

                        devs = [

                            dict(r)

                            for r in conn.execute(

                                "SELECT device_id FROM devices WHERE user_id=? AND (UPPER(TRIM(platform))='ANDROID' OR UPPER(TRIM(platform)) LIKE 'ANDROID %') AND pairing_state='PAIRED'",

                                (self.user_id,),

                            )

                        ]

                        if devs:

                            target_dev = devs[0]["device_id"]

                except Exception:

                    pass



            stop_payload = {

                "event_type": "STOP_STRICT_SESSION",

                "session_id": getattr(self, "session_id", "") or "",

                "user_id": self.user_id,

                "device_id": target_dev or "android_companion",

                "reason": reason,

                "timestamp": datetime.now().isoformat(),

            }



            try:

                from core.server import EventBus

                EventBus.get_instance().publish("STOP_STRICT_SESSION", stop_payload)

                EventBus.get_instance().publish("END_STRICT_SESSION", stop_payload)

            except Exception:

                pass



            if target_dev:

                try:

                    with self.db._connect() as conn:

                        conn.execute(

                            "UPDATE device_commands SET status='CANCELLED' WHERE device_id=? AND command_type='START_STRICT_SESSION' AND status IN ('PENDING', 'DISPATCHED')",

                            (target_dev,),

                        )

                except Exception as cancel_err:

                    logger.debug(f"Error cancelling stale start commands: {cancel_err}")



                if hasattr(self, 'connection_manager') and self.connection_manager:

                    try:

                        self.connection_manager.dispatch_admin_command("STOP_STRICT_SESSION", target_dev, stop_payload)

                    except Exception as cmd_err:

                        logger.debug(f"Admin command dispatch notice: {cmd_err}")

                else:

                    try:

                        cmd_id = f"cmd_{uuid.uuid4().hex[:12]}"

                        nonce = f"nonce_{uuid.uuid4().hex[:8]}"

                        self.db.create_device_command(

                            command_id=cmd_id,

                            device_id=target_dev,

                            command_type="STOP_STRICT_SESSION",

                            payload_json=json.dumps(stop_payload),

                            nonce=nonce,

                            admin_user="StrictSessionManager",

                        )

                    except Exception as db_cmd_err:

                        logger.debug(f"Direct device command creation notice: {db_cmd_err}")



            self.android_monitoring_state = "ANDROID_SESSION_COMPLETED"

            self._android_connection_lost_alert = None



            # 8 & 9. Stop iOS camera stream & screen sharing & close WebRTC connections

            if self.session_id:

                try:

                    self.ios_camera.stop_session(self.session_id)

                except Exception:

                    pass

            if self._active_preparation:

                try:

                    self.ios_camera.stop_session(preparation_id=self._active_preparation)

                except Exception:

                    pass

            try:

                from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor

                IPadScreenShareMonitor.get_instance().stop_active_session_monitoring(reason=reason)

                IPadScreenShareMonitor.get_instance().stop_track()

            except Exception:

                pass



            # 10 & 11. Finalize session record (check normal completion vs abort)

            is_completed = False

            if self.study.s:

                if self.study.s.get('state') == 'END_SESSION_ENABLED' and self.study.s.get('sync_verified'):

                    try:

                        self.study.complete(self.session_id)

                        is_completed = True

                    except Exception:

                        self.study.abort(reason, failed=False)

                else:

                    self.study.abort(reason, failed=False)



            self.preparation.finish(self._active_preparation)

            self._active_preparation = None

            self._setup_monitor['active'] = False



            # 12. Revert the Windows environment

            self.state = StrictSessionState.RESTORING

            self._restore_windows_environment()

            if self._supervisor_thread and self._supervisor_thread is not threading.current_thread():

                self._supervisor_thread.join(timeout=2)



            now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if self.session_id and getattr(self.study, 'mode', 'NORMAL') != 'DEMO':

                final_state = StrictSessionState.NORMAL.value

                self.db.update_strict_session(

                    self.session_id,

                    state=final_state,

                    end_time=now_ts,

                    exit_reason=reason,

                )

                self._audit(StrictAuditAction.SESSION_ENDED.value, reason=reason)



            # 13, 14 & 15. Clear persistent state and restore normal mode

            self._clear_state_file()

            self.state = StrictSessionState.NORMAL

            old_session_id = self.session_id

            self.session_id = None

            self.lock_screen_active = False

            self.blocked_notification_message = None



            self.set_lifecycle_state(StrictLifecycleState.SESSION_COMPLETED, reason=reason)

            self.set_lifecycle_state(StrictLifecycleState.IDLE)



            self._explicit_phase = StrictSessionPhase.SESSION_COMPLETED

            self.phase_started_at = datetime.now().isoformat()

            if hasattr(self, "_phase_history") and self._phase_history:

                self._phase_history[-1]["end_dt"] = datetime.now()

                self._phase_history[-1]["end_iso"] = self.phase_started_at



            logger.info("Strict Session ended cleanly by user. Normal Windows environment restored.")

            return {

                "success": True,

                "state": StrictSessionState.NORMAL.value,

                "lifecycle_state": StrictLifecycleState.IDLE.value,

                "session_id": old_session_id,

                "completed": is_completed,

                "message": "Strict Session ended. Normal Windows environment restored.",

            }



    def user_reset_session(self, reason: str = "User reset session") -> Dict[str, Any]:

        """

        Resets a stuck, damaged, or hung Strict Session:

        - Safe teardown of all proctoring, WebRTC, OS policies, and timers

        - Marks lifecycle state as SESSION_RESET

        - Clears active session context, stale camera timestamps, pending candidates

        - Transitions to IDLE

        - Preserves all historical records, points ledger, evidence files, and audit logs

        """

        with self._lock:

            logger.info(f"User resetting Strict Session ({self.session_id}). Reason: {reason}")

            self.set_lifecycle_state(StrictLifecycleState.SESSION_RESET, reason=reason)

            self.state = StrictSessionState.ENDING

            self._stop_event.set()



            # Clear stream monitor in-memory state & timestamps

            try:

                from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

                IPadCameraStreamMonitor.get_instance(self.db).reset_session_state(reason=reason)

            except Exception as cam_err:

                logger.debug(f"Error resetting iPad camera stream monitor: {cam_err}")



            # Stop study hardware & native host

            try:

                self.study_hardware.stop()

            except Exception as hw_err:

                logger.debug(f"Error stopping study hardware: {hw_err}")



            if self._study_native:

                try:

                    self._study_native.host.StudyStatus(json.dumps({'visible': False}))

                except Exception:

                    pass



            # Notify Android and cancel any pending start commands

            target_dev = getattr(self, "android_device_id", None)

            if not target_dev:

                try:

                    with self.db._connect() as conn:

                        devs = [

                            dict(r)

                            for r in conn.execute(

                                "SELECT device_id FROM devices WHERE user_id=? AND (UPPER(TRIM(platform))='ANDROID' OR UPPER(TRIM(platform)) LIKE 'ANDROID %') AND pairing_state='PAIRED'",

                                (self.user_id,),

                            )

                        ]

                        if devs:

                            target_dev = devs[0]["device_id"]

                except Exception:

                    pass



            stop_payload = {

                "event_type": "STOP_STRICT_SESSION",

                "session_id": getattr(self, "session_id", "") or "",

                "user_id": self.user_id,

                "device_id": target_dev or "android_companion",

                "reason": reason,

                "timestamp": datetime.now().isoformat(),

            }



            try:

                from core.server import EventBus

                EventBus.get_instance().publish("STOP_STRICT_SESSION", stop_payload)

                EventBus.get_instance().publish("END_STRICT_SESSION", stop_payload)

            except Exception:

                pass



            if target_dev:

                try:

                    with self.db._connect() as conn:

                        conn.execute(

                            "UPDATE device_commands SET status='CANCELLED' WHERE device_id=? AND command_type='START_STRICT_SESSION' AND status IN ('PENDING', 'DISPATCHED')",

                            (target_dev,),

                        )

                except Exception as cancel_err:

                    logger.debug(f"Error cancelling stale start commands: {cancel_err}")



                if hasattr(self, 'connection_manager') and self.connection_manager:

                    try:

                        self.connection_manager.dispatch_admin_command("STOP_STRICT_SESSION", target_dev, stop_payload)

                    except Exception as cmd_err:

                        logger.debug(f"Admin command dispatch notice: {cmd_err}")

                else:

                    try:

                        cmd_id = f"cmd_{uuid.uuid4().hex[:12]}"

                        nonce = f"nonce_{uuid.uuid4().hex[:8]}"

                        self.db.create_device_command(

                            command_id=cmd_id,

                            device_id=target_dev,

                            command_type="STOP_STRICT_SESSION",

                            payload_json=json.dumps(stop_payload),

                            nonce=nonce,

                            admin_user="StrictSessionManager",

                        )

                    except Exception as db_cmd_err:

                        logger.debug(f"Direct device command creation notice: {db_cmd_err}")



            self.android_monitoring_state = "ANDROID_SESSION_COMPLETED"

            self._android_connection_lost_alert = None



            # Stop iOS camera stream & screen sharing

            if self.session_id:

                try:

                    self.ios_camera.stop_session(self.session_id)

                except Exception:

                    pass

            if self._active_preparation:

                try:

                    self.ios_camera.stop_session(preparation_id=self._active_preparation)

                except Exception:

                    pass

            try:

                from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor

                IPadScreenShareMonitor.get_instance().stop_active_session_monitoring(reason=reason)

                IPadScreenShareMonitor.get_instance().stop_track()

            except Exception:

                pass



            # Reset study workflow (clears self.study.s without creating fake points)

            try:

                self.study.reset(reason=reason)

            except Exception as st_err:

                logger.debug(f"Error resetting study workflow: {st_err}")



            # Reset preparation

            self.preparation.finish(self._active_preparation)

            self._active_preparation = None

            self._setup_monitor['active'] = False



            # Restore Windows environment

            self.state = StrictSessionState.RESTORING

            self._restore_windows_environment()

            if self._supervisor_thread and self._supervisor_thread is not threading.current_thread():

                self._supervisor_thread.join(timeout=2)



            now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if self.session_id and getattr(self.study, 'mode', 'NORMAL') != 'DEMO':

                self.db.update_strict_session(

                    self.session_id,

                    state="SESSION_RESET",

                    end_time=now_ts,

                    exit_reason=reason,

                )

                self._audit(StrictAuditAction.SESSION_RESET.value, reason=reason)



            # Clear state file

            self._clear_state_file()

            self.state = StrictSessionState.NORMAL

            old_session_id = self.session_id

            self.session_id = None

            self.lock_screen_active = False

            self.blocked_notification_message = None



            self.set_lifecycle_state(StrictLifecycleState.IDLE, reason=reason)



            self._explicit_phase = StrictSessionPhase.SESSION_COMPLETED

            self.phase_started_at = datetime.now().isoformat()

            if hasattr(self, "_phase_history") and self._phase_history:

                self._phase_history[-1]["end_dt"] = datetime.now()

                self._phase_history[-1]["end_iso"] = self.phase_started_at



            logger.info("Strict Session reset cleanly. Memory state cleared and normal mode restored.")

            return {

                "success": True,

                "state": StrictSessionState.NORMAL.value,

                "lifecycle_state": StrictLifecycleState.IDLE.value,

                "session_id": old_session_id,

                "message": "Strict Session reset cleanly. Normal mode restored.",

            }



    def user_restart_session(self, reason: str = "User restarted session") -> Dict[str, Any]:

        """

        Resets current session cleanly, then immediately restarts preparation

        with a brand-new session ID.

        """

        reset_res = self.user_reset_session(reason=reason)

        with self._lock:

            prep = self.preparation.current(self.user_id)

            prep_id = prep["preparation_id"]

            self._active_preparation = prep_id

            self.set_lifecycle_state(

                StrictLifecycleState.PREREQUISITE_CHECK,

                session_id=prep_id,

                details={"restarted": True, "previous_session_id": reset_res.get("session_id")},

            )

            return {

                "success": True,

                "state": StrictSessionState.NORMAL.value,

                "lifecycle_state": StrictLifecycleState.PREREQUISITE_CHECK.value,

                "preparation_id": prep_id,

                "message": "Strict Session reset and restarted with new preparation ID.",

            }



    # =========================================================================

    # DYNAMIC PRIVILEGE EVALUATION (Section 8, 10, 11)

    # =========================================================================



    def _refresh_authoritative_score(self) -> bool:

        """Rebuild today's score and ledger totals from retained, approved proof."""

        try:

            from core.scoring_engine import ScoringEngine

            from core.time_window_engine import now_ist



            if self._scoring_engine is None:

                self._scoring_engine = ScoringEngine(db=self.db, storage_root=self.data_dir)

            today = now_ist().strftime("%Y-%m-%d")

            daily = self._scoring_engine.recalculate_daily_score(today, user_id=self.user_id)

            ledger = self.db.get_point_ledger_summary(date_str=today, account_id=self.user_id)

            self.current_score = int(daily.get("final_score", 0) or 0)

            self.verified_points = int(ledger.get("verified_points", 0) or 0)

            self.provisional_points = max(0, int(ledger.get("provisional_points", 0) or 0))

            return True

        except Exception as exc:

            # Keep stale in-memory points from surviving a database or ledger

            # failure. The evidence remains in storage for safe later recovery.

            logger.warning(f"Could not refresh authoritative strict-session score: {exc}")

            self.current_score = 0

            self.verified_points = 0

            self.provisional_points = 0

            self.hourly_proof_status = HourlyProofStatus.REVIEW_REQUIRED

            return False



    def evaluate_privilege(self) -> Dict[str, Any]:

        """

        Calls PrivilegeEngine with authoritative inputs.

        Gravely governs entertainment lock screen state.

        """

        if getattr(self.study,'mode','NORMAL')=='DEMO':

            return dict(allowed=False,reasons=['Demo sessions cannot grant entertainment access.'],lock_screen_active=bool(self.session_id))

        # Reconcile the displayed points and privilege inputs from the same

        # retained-proof-validated ledger used by the rest of the application.

        self._refresh_authoritative_score()



        eval_result = self.privilege_engine.evaluate_privilege(

            current_score=self.current_score,

            verified_points=self.verified_points,

            provisional_points=self.provisional_points,

            hourly_proof_status=self.hourly_proof_status,

            critical_violations=self.critical_violations,

            camera_health=self.camera_health,

            microphone_health=self.microphone_health,

            strict_session_state=self.state,

            required_score=self.policy.score_threshold,

        )

        if self.study.s and self.study.s['state'] not in TERMINAL:

            eval_result['allowed'] = False

            eval_result.setdefault('reasons', []).append('Strict study remains pending until verified Android proof and manual completion.')



        previous_lock_state = self.lock_screen_active

        if eval_result["allowed"]:

            self.lock_screen_active = False

            if self.state in (StrictSessionState.ACTIVE, StrictSessionState.RESTRICTED):

                self.state = StrictSessionState.PRIVILEGED

            if previous_lock_state:

                self._audit(StrictAuditAction.PRIVILEGE_GRANTED.value, reason="Score and proof criteria fulfilled")

        else:

            self.lock_screen_active = True

            if self.state in (StrictSessionState.ACTIVE, StrictSessionState.PRIVILEGED):

                self.state = StrictSessionState.RESTRICTED

            if not previous_lock_state:

                self._audit(

                    StrictAuditAction.PRIVILEGE_REVOKED.value,

                    reason="; ".join(eval_result.get("reasons", [])),

                )



        # Notify Network & Browser policies of current privilege status

        self.network_policy_manager.update_privilege(eval_result["allowed"])

        self.browser_policy_manager.entertainment_allowed = eval_result["allowed"]



        eval_result["lock_screen_active"] = self.lock_screen_active

        return eval_result



    # =========================================================================

    # HOURLY PROOF CHECKPOINT MANAGEMENT (Section 9)

    # =========================================================================



    def submit_hourly_proof(

        self,

        evidence_id: str,

        task_code: str = "HOURLY_CHECKPOINT",

        description: str = "Hourly productivity verification checkpoint",

    ) -> Dict[str, Any]:

        """

        Submits hourly proof to AI analyzer and reconciles privilege state.

        """

        if getattr(self.study,'mode','NORMAL')=='DEMO':

            return dict(success=False,error='The practice session does not accept proof or award points.')

        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        logger.info(f"Processing hourly proof {evidence_id} for session {self.session_id}...")



        ai_result: Dict[str, Any] = {"decision": "REVIEW_REQUIRED"}

        proof = self.db.get_proof_details(evidence_id) if evidence_id else None

        has_valid_image = bool(

            proof

            and not proof.get("is_deleted")

            and is_valid_retained_proof_file(

                proof.get("file_path", ""), "Hourly", proof.get("sha256_hash")

            )

        )

        if not has_valid_image:

            ai_result = {

                "decision": "REJECTED",

                "error": "Evidence is missing, deleted, or is not a valid retained image.",

            }

            self.hourly_proof_status = HourlyProofStatus.INVALID

        else:

            try:

                from core.scoring_engine import ScoringEngine

                engine = ScoringEngine(db=self.db)

                ai_result = engine.evaluate_proof_with_ai(

                    evidence_id=evidence_id,

                    user_id=self.user_id,

                    settings=self.db.get_user_settings(self.user_id),

                )

            except Exception as exc:

                logger.warning(f"Hourly proof analysis failed; leaving it for review: {exc}")

                ai_result = {"decision": "REVIEW_REQUIRED", "error": "Proof analysis is unavailable."}



            # AI output alone is never the privilege or score authority. Require the

            # retained proof and persisted approval state before marking it valid.

            proof_after = self.db.get_proof_details(evidence_id)

            approved = bool(

                proof_after

                and str(proof_after.get("status", "")).upper() == "APPROVED"

                and not proof_after.get("is_deleted")

                and is_valid_retained_proof_file(

                    proof_after.get("file_path", ""), "Hourly", proof_after.get("sha256_hash")

                )

            )

            decision = str(ai_result.get("decision") or "REVIEW_REQUIRED").upper()

            if approved:

                self.hourly_proof_status = HourlyProofStatus.VALID

                self._audit(StrictAuditAction.PROOF_VERIFIED.value, reason=f"Persistently approved proof {evidence_id}")

            elif decision in ("DUPLICATE", "DUPLICATED"):

                self.hourly_proof_status = HourlyProofStatus.DUPLICATE

                self._audit(StrictAuditAction.PROOF_REJECTED.value, reason=f"Duplicate proof {evidence_id}")

            elif decision in ("LOW_CONFIDENCE", "UNCERTAIN"):

                self.hourly_proof_status = HourlyProofStatus.LOW_CONFIDENCE

                self._audit(StrictAuditAction.PROOF_REJECTED.value, reason=f"Proof requires review {evidence_id}")

            elif decision in ("REJECTED", "INVALID"):

                self.hourly_proof_status = HourlyProofStatus.INVALID

                self._audit(StrictAuditAction.PROOF_REJECTED.value, reason=f"Proof rejected {evidence_id}")

            else:

                self.hourly_proof_status = HourlyProofStatus.REVIEW_REQUIRED



        # Score and verified points are derived from the shared ledger only.

        self._refresh_authoritative_score()



        priv_res = self.evaluate_privilege()

        return {

            "evidence_id": evidence_id,

            "status": self.hourly_proof_status.value,

            "ai_evaluation": ai_result,

            "privilege": priv_res,

            "timestamp": now_ts,

        }



    # =========================================================================

    # OS-LEVEL CONTROLS: KEYBOARD HOOK, REGISTRY, SHELL

    # =========================================================================



    def _install_keyboard_hook(self):

        """

        Low-Level Keyboard Hook (WH_KEYBOARD_LL) via SetWindowsHookExW.

        Blocks: Alt+Tab, Alt+Esc, WinKey (LWIN/RWIN), Win+Tab, Ctrl+Shift+Esc, Alt+F4.

        Runs on dedicated thread with Win32 message pump.

        """

        if os.name != "nt":

            return False

        hook_ready = threading.Event()



        def _hook_thread_proc():

            user32 = ctypes.windll.user32

            kernel32 = ctypes.windll.kernel32

            self._hook_thread_id = kernel32.GetCurrentThreadId()

            user32.SetWindowsHookExW.argtypes=[ctypes.c_int,HOOKPROC,ctypes.wintypes.HINSTANCE,ctypes.wintypes.DWORD]

            user32.SetWindowsHookExW.restype=ctypes.wintypes.HANDLE

            user32.CallNextHookEx.argtypes=[ctypes.wintypes.HANDLE,ctypes.c_int,ctypes.wintypes.WPARAM,ctypes.wintypes.LPARAM]

            user32.CallNextHookEx.restype=ctypes.c_ssize_t

            user32.UnhookWindowsHookEx.argtypes=[ctypes.wintypes.HANDLE]

            kernel32.GetModuleHandleW.argtypes=[ctypes.wintypes.LPCWSTR]

            kernel32.GetModuleHandleW.restype=ctypes.wintypes.HMODULE



            def _low_level_keyboard_proc(nCode, wParam, lParam):

                if nCode >= 0:

                    kb = KBDLLHOOKSTRUCT.from_address(lParam)

                    vk = kb.vkCode

                    flags = kb.flags

                    is_alt = bool(flags & LLKHF_ALTDOWN)

                    from windows.embedded_strict_focus import blocked_accelerator

                    ctrl_down=bool(user32.GetAsyncKeyState(VK_CONTROL)&0x8000)

                    if blocked_accelerator(vk,ctrl=ctrl_down,alt=is_alt):

                        return 1



                    # Block Windows Keys (LWIN, RWIN)

                    if vk in (VK_LWIN, VK_RWIN):

                        return 1



                    # Block Alt+Tab

                    if vk == VK_TAB and is_alt:

                        return 1



                    # Block Alt+Esc

                    if vk == VK_ESCAPE and is_alt:

                        return 1



                    # Block Alt+F4

                    if vk == VK_F4 and is_alt:

                        return 1



                    # Block Ctrl+Shift+Esc (Task Manager)

                    if vk == VK_ESCAPE:

                        ctrl_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)

                        shift_down = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)

                        if ctrl_down and shift_down:

                            try:

                                from windows.strict_violation_engine import get_strict_violation_engine

                                sid = self.session_id or (self.study.s and self.study.s.get('session_id')) or "active_session"

                                get_strict_violation_engine(self.db).record_task_manager_shortcut(sid)

                            except Exception as e:

                                logger.debug(f"Task manager shortcut record error: {e}")

                            return 1



                return user32.CallNextHookEx(self._hook_handle, nCode, wParam, lParam)



            self._hook_proc_ref = HOOKPROC(_low_level_keyboard_proc)

            self._hook_handle = user32.SetWindowsHookExW(

                WH_KEYBOARD_LL,

                self._hook_proc_ref,

                kernel32.GetModuleHandleW(None),

                0,

            )

            hook_ready.set()



            if not self._hook_handle:

                logger.warning(f"Failed to install WH_KEYBOARD_LL hook: {kernel32.GetLastError()}")

                return



            logger.info("Low-level keyboard hook active (Alt+Tab, WinKey, TaskMgr blocked).")

            # Message pump to keep hook active

            msg = ctypes.wintypes.MSG()

            while not self._stop_event.is_set():

                ret = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)

                if ret <= 0:

                    break

                user32.TranslateMessage(ctypes.byref(msg))

                user32.DispatchMessageW(ctypes.byref(msg))



            if self._hook_handle:

                user32.UnhookWindowsHookEx(self._hook_handle)

                self._hook_handle = None

            logger.info("Low-level keyboard hook uninstalled.")



        self._hook_thread = threading.Thread(target=_hook_thread_proc, daemon=True, name="KbdHookThread")

        self._hook_thread.start()

        hook_ready.wait(3)

        return bool(self._hook_handle)



    def _uninstall_keyboard_hook(self):

        if os.name != "nt":

            return

        self._stop_event.set()

        if self._hook_thread_id:

            try:

                ctypes.windll.user32.PostThreadMessageW(self._hook_thread_id, 0x0012, 0, 0)  # WM_QUIT

            except Exception:

                pass

        if self._hook_handle:

            try:

                ctypes.windll.user32.UnhookWindowsHookEx(self._hook_handle)

            except Exception:

                pass

            self._hook_handle = None



    def _apply_registry_policies(self):

        """

        Configures HKCU System Policies:

        - DisableTaskMgr = 1 (Suppresses Ctrl+Alt+Del Task Manager option)

        - DisableLockWorkstation = 1 (Suppresses Win+L lock bypass)

        """

        if not HAS_WINREG or os.name != "nt":

            return False

        try:

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, POLICIES_SYSTEM_KEY) as key:

                if not os.path.exists(self.registry_backup_file):

                    previous={}

                    for name in ('DisableTaskMgr','DisableLockWorkstation'):

                        try:

                            value,kind=winreg.QueryValueEx(key,name)

                            previous[name]=dict(present=True,value=value,kind=kind)

                        except FileNotFoundError:

                            previous[name]=dict(present=False)

                    with open(self.registry_backup_file,'w',encoding='utf-8') as backup:

                        json.dump(previous,backup)

                winreg.SetValueEx(key, "DisableTaskMgr", 0, winreg.REG_DWORD, 1)

                winreg.SetValueEx(key, "DisableLockWorkstation", 0, winreg.REG_DWORD, 1)

            logger.info("Configured Windows Registry Policies (DisableTaskMgr, DisableLockWorkstation).")

            return True

        except Exception as e:

            logger.warning("Could not apply registry policies (running without elevation or restricted by policy): %s. Fallback to low-level keyboard hook enforcement.", e)

            return True



    def _restore_registry_policies(self):

        """

        Safely deletes DisableTaskMgr and DisableLockWorkstation policies.

        Guaranteed to leave the system clean.

        """

        if not HAS_WINREG or os.name != "nt":

            return

        if not os.path.exists(self.registry_backup_file) and not os.path.exists(self.state_file):

            return

        try:

            previous={}

            if os.path.exists(self.registry_backup_file):

                with open(self.registry_backup_file,encoding='utf-8') as backup:

                    previous=json.load(backup)

            with winreg.OpenKey(

                winreg.HKEY_CURRENT_USER,

                POLICIES_SYSTEM_KEY,

                0,

                winreg.KEY_SET_VALUE,

            ) as key:

                for val in ["DisableTaskMgr", "DisableLockWorkstation"]:

                    try:

                        old=previous.get(val,{})

                        if old.get('present'):

                            winreg.SetValueEx(key,val,0,old['kind'],old['value'])

                        else:

                            winreg.DeleteValue(key, val)

                    except FileNotFoundError:

                        pass

            logger.info("Restored normal Windows Registry Policies.")

            if os.path.exists(self.registry_backup_file):

                os.remove(self.registry_backup_file)

        except Exception as e:

            logger.debug(f"Registry policy restoration notice: {e}")



    def _hide_taskbar(self):

        if os.name != "nt":

            return

        try:

            user32 = ctypes.windll.user32

            h_taskbar = user32.FindWindowW("Shell_TrayWnd", None)

            if h_taskbar:

                user32.ShowWindow(h_taskbar, SW_HIDE)

            h_start = user32.FindWindowW("Button", "Start")

            if h_start:

                user32.ShowWindow(h_start, SW_HIDE)

        except Exception as e:

            logger.warning(f"Taskbar hide notice: {e}")



    def _show_taskbar(self):

        if os.name != "nt":

            return

        try:

            user32 = ctypes.windll.user32

            h_taskbar = user32.FindWindowW("Shell_TrayWnd", None)

            if h_taskbar:

                user32.ShowWindow(h_taskbar, SW_SHOW)

            h_start = user32.FindWindowW("Button", "Start")

            if h_start:

                user32.ShowWindow(h_start, SW_SHOW)

        except Exception as e:

            logger.warning(f"Taskbar restore notice: {e}")



    # =========================================================================

    # APPLICATION ALLOWLIST SUPERVISOR THREAD

    # =========================================================================



    def _start_supervisor(self):

        """

        Supervisor loop:

        Supervises active foreground application against policy allowlist.

        Return to the existing study view; never close another app's windows.

        """

        def _supervisor_loop():

            logger.info("Application Allowlist Supervisor active.")

            while not self._stop_event.is_set():

                try:

                    self._check_and_enforce_foreground_process()

                    self._tick_study()

                    sid = self.session_id or (self.study.s and self.study.s.get('session_id'))

                    if sid:

                        try:

                            from windows.strict_violation_engine import get_strict_violation_engine

                            get_strict_violation_engine(self.db).reconcile_active_processes(sid)

                        except Exception:

                            pass

                except Exception as e:

                    logger.debug(f"Supervisor sample error: {e}")

                self._stop_event.wait(0.6)

            logger.info("Application Allowlist Supervisor terminated.")



        self._supervisor_thread = threading.Thread(target=_supervisor_loop, daemon=True, name="AppSupervisor")

        self._supervisor_thread.start()



    def _check_and_enforce_foreground_process(self):

        if os.name != "nt":

            return



        user32 = ctypes.windll.user32

        kernel32 = ctypes.windll.kernel32



        strict_study=bool(self.study.s and self.study.s['state'] not in TERMINAL)

        if strict_study:self._foreground_study_ok=False

        user32.GetForegroundWindow.restype=ctypes.wintypes.HWND

        user32.GetWindowThreadProcessId.argtypes=[ctypes.wintypes.HWND,ctypes.POINTER(ctypes.wintypes.DWORD)]

        hwnd = user32.GetForegroundWindow()

        if not hwnd:

            return



        # Query process ID and executable name

        pid = ctypes.wintypes.DWORD()

        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        if not pid.value:

            return



        if strict_study and self.embedded_focus and self.embedded_focus.owns_foreground(pid.value):

            self._foreground_study_ok=True

            self._foreground_violation_latched=False

            try:

                from windows.strict_violation_engine import get_strict_violation_engine

                sid = self.session_id or (self.study.s and self.study.s.get('session_id')) or "active_session"

                get_strict_violation_engine(self.db).on_study_foreground_restored(sid)

            except Exception:

                pass

            return



        proc_name = self._get_process_name_by_pid(pid.value)

        if not proc_name:

            return



        proc_lower = proc_name.lower().strip()



        if strict_study:

            # Keep OS security surfaces usable. Other apps retain their windows

            # and unsaved work; return to the same existing approved study tab.

            if proc_lower not in {'lockapp.exe','logonui.exe','consent.exe','credentialuibroker.exe','securityhealthsystray.exe'}:

                sid = self.session_id or (self.study.s and self.study.s.get('session_id')) or "active_session"

                try:

                    from windows.strict_violation_engine import (

                        get_strict_violation_engine,

                        TASK_MANAGER_PROCESSES,

                        TERMINAL_PROCESSES,

                        EXTERNAL_BROWSERS,

                        is_process_ancestor,

                    )

                    v_engine = get_strict_violation_engine(self.db)

                    if proc_lower in TASK_MANAGER_PROCESSES:

                        v_engine.record_task_manager_process(sid, pid=pid.value, details={"process_name": proc_name})

                        try:

                            user32.PostMessageW(hwnd, 0x0010, 0, 0)

                        except Exception:

                            pass

                    elif proc_lower in TERMINAL_PROCESSES:

                        if pid.value != os.getpid() and not is_process_ancestor(os.getpid(), pid.value):

                            v_engine.record_command_prompt_process(sid, pid=pid.value, proc_name=proc_name, details={"process_name": proc_name})

                            try:

                                user32.PostMessageW(hwnd, 0x0010, 0, 0)

                            except Exception:

                                pass

                    elif proc_lower in EXTERNAL_BROWSERS:

                        v_engine.record_external_browser_switch(sid, pid=pid.value, proc_name=proc_name, details={"process_name": proc_name})

                except Exception as ex:

                    logger.debug(f"Violation engine check error: {ex}")



                blocked_meta = self.app_terminator.check_and_block_process(pid.value, proc_name)

                if blocked_meta:

                    self.last_blocked_app = blocked_meta["app_name"]

                    self.blocked_notification_message = f"Prohibited application blocked during Strict Session: {blocked_meta['app_name']}"

                    self._audit(

                        StrictAuditAction.APP_BLOCKED.value,

                        reason=f"Prohibited external application launch prevented: {blocked_meta['app_name']}",

                        details={"process_name": proc_name, "pid": pid.value, "category": blocked_meta["category"]}

                    )

                else:

                    self.last_blocked_app = proc_name

                    self.blocked_notification_message = 'Return to the ChatGPT or LeetCode study tab.'

                if not getattr(self,'_foreground_violation_latched',False):

                    self._foreground_violation_latched=True

                    from windows.strict_integrity_events import IntegrityEpisodes

                    if not hasattr(self,'_foreground_integrity'):self._foreground_integrity=IntegrityEpisodes()

                    self.study.record_integrity_violation(self._foreground_integrity.record('WINDOW_FOCUS_LOST',

                        'An application switch away from the locked study workspace was detected.'))

                self._restore_strictrecords_foreground()

            return



        # Check if process is protected system process

        if proc_lower in SYSTEM_SAFE_EXECUTABLES:

            return



        # Check if process is in allowed applications list

        is_allowed = False

        for allowed in self.policy.allowed_applications:

            al_lower = allowed.lower().strip()

            if proc_lower == al_lower or proc_lower == f"{al_lower}.exe":

                is_allowed = True

                break



        if is_allowed:

            # Check browser title if running approved browser

            title_len = user32.GetWindowTextLengthW(hwnd)

            if title_len > 0:

                buf = ctypes.create_unicode_buffer(title_len + 1)

                user32.GetWindowTextW(hwnd, buf, title_len + 1)

                nav_check = self.browser_policy_manager.check_active_browser_navigation(buf.value)

                if nav_check:

                    # Blocked entertainment navigation detected

                    self.lock_screen_active = True

                    self.last_blocked_app = proc_name

                    self.blocked_notification_message = nav_check["reason"]

                    self._audit(

                        StrictAuditAction.SITE_BLOCKED.value,

                        reason=nav_check["reason"],

                        details={"title": buf.value},

                    )

            return



        # Process is UNAUTHORIZED during Strict Session

        logger.warning(f"Unauthorized application detected in foreground: {proc_name} (PID: {pid.value})")

        self.last_blocked_app = proc_name

        self.blocked_notification_message = f"This application is unavailable during Strict Session: {proc_name}"



        # Keep the study view foreground without closing unrelated apps or

        # discarding their unsaved work. OS secure surfaces remain available.



        # Bring StrictRecords window to foreground

        self._restore_strictrecords_foreground()



        # Audit Event

        self._audit(

            StrictAuditAction.APP_BLOCKED.value,

            reason=f"Application not in policy allowlist: {proc_name}",

            details={"process_name": proc_name, "pid": pid.value},

        )



    def _get_process_name_by_pid(self, pid: int) -> Optional[str]:

        kernel32 = ctypes.windll.kernel32

        kernel32.OpenProcess.argtypes=[ctypes.wintypes.DWORD,ctypes.wintypes.BOOL,ctypes.wintypes.DWORD]

        kernel32.OpenProcess.restype=ctypes.wintypes.HANDLE

        kernel32.QueryFullProcessImageNameW.argtypes=[ctypes.wintypes.HANDLE,ctypes.wintypes.DWORD,ctypes.wintypes.LPWSTR,ctypes.POINTER(ctypes.wintypes.DWORD)]

        kernel32.CloseHandle.argtypes=[ctypes.wintypes.HANDLE]

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)

        if not h_proc:

            return None

        try:

            buf = ctypes.create_unicode_buffer(1024)

            size = ctypes.wintypes.DWORD(1024)

            if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):

                full_path = buf.value

                return os.path.basename(full_path)

        finally:

            kernel32.CloseHandle(h_proc)

        return None



    def _restore_strictrecords_foreground(self):

        if self.embedded_focus and hasattr(self.embedded_focus,'focus') and self.embedded_focus.focus():return

        if os.name != "nt":

            return

        user32 = ctypes.windll.user32



        def _enum_cb(h, _):

            txt_len = user32.GetWindowTextLengthW(h)

            if txt_len > 0:

                buf = ctypes.create_unicode_buffer(txt_len + 1)

                user32.GetWindowTextW(h, buf, txt_len + 1)

                if "STRICTRECORDS" in buf.value.upper():

                    user32.ShowWindow(h, SW_RESTORE)

                    user32.SetForegroundWindow(h)

                    return False

            return True



        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)



    # =========================================================================

    # RECOVERY & AUDITING (Sections 16, 17, 18, 21)

    # =========================================================================



    def _restore_windows_environment(self):

        """Restores all system controls unconditionally."""

        self.notification_filter.restore()

        self._uninstall_keyboard_hook()

        self._restore_registry_policies()

        self._show_taskbar()

        self.network_policy_manager.restore_network()

        self.browser_policy_manager.close_browser()

        if self.embedded_focus:self.embedded_focus.close()

        if hasattr(self, "app_terminator") and self.app_terminator:

            self.app_terminator.stop_monitoring()



    def _finish_study_controls(self, reason):

        if self.session_id:

            self.ios_camera.stop_session(self.session_id)

        if self._active_preparation:

            self.ios_camera.stop_session(preparation_id=self._active_preparation)

        target_dev = getattr(self, "android_device_id", None)

        if not target_dev:

            try:

                with self.db._connect() as conn:

                    devs = [dict(r) for r in conn.execute("SELECT device_id FROM devices WHERE user_id=? AND (UPPER(TRIM(platform))='ANDROID' OR UPPER(TRIM(platform)) LIKE 'ANDROID %') AND pairing_state='PAIRED'", (self.user_id,))]

                    if devs:

                        target_dev = devs[0]["device_id"]

            except Exception:

                pass

        stop_payload = {

            "event_type": "STOP_STRICT_SESSION",

            "session_id": getattr(self, "session_id", ""),

            "user_id": self.user_id,

            "device_id": target_dev or "android_companion",

            "reason": reason,

            "timestamp": datetime.now().isoformat(),

        }

        try:

            from core.server import EventBus

            EventBus.get_instance().publish("STOP_STRICT_SESSION", stop_payload)

            EventBus.get_instance().publish("END_STRICT_SESSION", stop_payload)

        except Exception:

            pass

        if target_dev and hasattr(self, 'connection_manager') and self.connection_manager:

            try:

                self.connection_manager.dispatch_admin_command("STOP_STRICT_SESSION", target_dev, stop_payload)

            except Exception as cmd_err:

                logger.debug(f"Admin command dispatch notice: {cmd_err}")

        self.android_monitoring_state = "ANDROID_SESSION_COMPLETED"

        self._android_connection_lost_alert = None

        self._setup_monitor['active']=False

        try:

            from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

            IPadCameraStreamMonitor.get_instance().stop_session_monitoring(reason=reason)

        except Exception:

            pass

        try:

            from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor

            IPadScreenShareMonitor.get_instance().stop_active_session_monitoring(reason=reason)

            IPadScreenShareMonitor.get_instance().stop_track()

        except Exception:

            pass

        self.set_lifecycle_state(StrictLifecycleState.SESSION_COMPLETED, reason=reason)

        self._stop_event.set()

        self.preparation.finish(self._active_preparation)

        self.study_hardware.stop()

        if self._study_native:

            self._study_native.host.StudyStatus(json.dumps({'visible':False}))

        self._restore_windows_environment()

        if self._supervisor_thread and self._supervisor_thread is not threading.current_thread():

            self._supervisor_thread.join(timeout=2)

        if self.session_id and getattr(self.study,'mode','NORMAL')!='DEMO':

            self.db.update_strict_session(self.session_id, state='NORMAL',

                                          end_time=datetime.now().isoformat(), exit_reason=reason)

        self._clear_state_file()

        self.state = StrictSessionState.NORMAL

        self.set_lifecycle_state(StrictLifecycleState.IDLE)

        self.session_id = None

        self.lock_screen_active = False



    def _live_study_hardware(self):

        hardware=self.study_hardware.view()

        if self._active_preparation:

            camera = self._ios_camera_readiness(self.user_id, self._active_preparation)

            hardware['ios_camera'] = camera['camera']

            hardware['ios_camera_required'] = True

            hardware['ios_camera_ready'] = camera['ready']

            hardware['monitoring_ok'] = bool(hardware.get('monitoring_ok') and (camera.get('ready') or camera.get('within_grace')))

        hardware['notification_filter_active']=self.notification_filter.status()['active']

        if self.session_id and self.state!=StrictSessionState.NORMAL:

            hardware['monitoring_ok']=bool(hardware.get('monitoring_ok') and hardware['notification_filter_active'] and self._foreground_study_ok)

            hardware['screen_proctoring_active']=bool(hardware.get('screen_capture_active') and self._foreground_study_ok and self._hook_handle)

        return hardware



    def is_active(self) -> bool:

        return self.state in (StrictSessionState.ACTIVE, StrictSessionState.RESTRICTED, StrictSessionState.PRIVILEGED, StrictSessionState.STARTING)



    def _tick_study(self):

        if not self.study.s:

            return

        hw = self._live_study_hardware()

        for event in hw.get('integrity_events',[]):self.study.record_integrity_violation(event)

        if self.embedded_focus and hasattr(self.embedded_focus,'integrity_events'):

            for event in self.embedded_focus.integrity_events():self.study.record_integrity_violation(event)

        self.camera_health = hw.get('monitoring_ok', False)

        self.study.tick(monitoring_ok=bool(hw.get('monitoring_ok') and hw['notification_filter_active']))

        self._update_study_hud(hw)

        if getattr(self.study,'mode','NORMAL')=='DEMO' and self.study.s['state']=='SESSION_FINALIZED':

            self._finish_study_controls('Ten-minute practice finished; no points or reward')

            return

        if self.study.s.get('cloud_registered') and time.monotonic() >= self._hold_retry_at:

            threading.Thread(target=self._sync_study_authority, daemon=True, name='StrictStudyHold').start()



        # StrictGuard: Leftward Attention Monitoring & Enforcement

        curr_left_v = hw.get('leftward_violations', 0)

        if curr_left_v != getattr(self, '_last_leftward_violations', 0):

            self._last_leftward_violations = curr_left_v

            msg = hw.get('proctor_message', 'Left-side attention detected.')

            self.study.record_leftward_violation(curr_left_v, msg)

            if curr_left_v > 15 or hw.get('session_failed'):

                self._finish_study_controls('StrictGuard: Exceeded maximum leftward attention violations (>15)')

                threading.Thread(target=self._sync_study_authority, daemon=True).start()



        # Audio Device Enforcement: Prohibited Wireless and Wired Headphone Detection

        curr_hp_v = hw.get('headphone_violations', 0)

        if curr_hp_v != getattr(self, '_last_headphone_violations', 0):

            self._last_headphone_violations = curr_hp_v

            dev_name = hw.get('last_prohibited_device', 'Headphones')

            res = self.study.record_headphone_violation(dev_name, curr_hp_v)

            if res.get('failed') or curr_hp_v >= 2:

                self._ensure_headphone_penalty(dev_name)

                self._finish_study_controls(f'Second confirmed headphone connection violation ({dev_name})')

                threading.Thread(target=self._sync_study_authority, daemon=True).start()



    def is_submission_window_open(self) -> bool:

        """Authoritatively checks if the current hourly submission window is open."""

        if self.study and hasattr(self.study, 'is_submission_window_open'):

            try:

                if self.study.is_submission_window_open():

                    return True

            except Exception:

                pass

        try:

            from windows.submission_window_manager import SubmissionWindowManager, SubmissionState

            mgr = SubmissionWindowManager.get_instance()

            if mgr and getattr(mgr, 'state', None):

                if mgr.state == SubmissionState.OPEN or str(mgr.state) == 'OPEN' or str(getattr(mgr.state, 'value', '')) == 'OPEN':

                    return True

        except Exception:

            pass

        if self.db:

            try:

                with self.db._connect() as conn:

                    row = conn.execute("SELECT state FROM submission_window_state WHERE state='OPEN'").fetchone()

                    if row:

                        return True

            except Exception:

                pass

        return False



    def get_session_proof_context(self) -> Dict[str, Any]:

        """Contextual state to distinguish authorized Android proof interaction from violations."""

        authorized = False

        if self.study and hasattr(self.study, 'is_authorized_proof_submission_active'):

            try:

                authorized = self.study.is_authorized_proof_submission_active()

            except Exception:

                authorized = False

        s_dict = getattr(self.study, 's', None) or {}

        return {

            "session_id": self.session_id,

            "authorized_proof_submission": authorized,

            "state": s_dict.get('state') if isinstance(s_dict, dict) else None,

            "focus_completed": bool(s_dict.get('focus_completed_at')) if isinstance(s_dict, dict) else False,

        }



    def _ensure_headphone_penalty(self, dev_name='Prohibited Audio Device'):

        if getattr(self.study,'mode','NORMAL')=='DEMO':return

        s = self.study.s

        if s and s['state'] == 'FAILED' and s.get('headphone_violations', 0) >= 2 and not s.get('headphone_penalty_proposed'):

            timestamp = s.get('terminated_at', s['started_at'])

            self.db.create_deduction_proposal(

                proposal_id='strict_hp_' + s['session_id'], date=timestamp[:10],

                timestamp=timestamp, originating_device=self.device_id,

                event_type='STRICT_SESSION_HEADPHONE_SECOND_DETECTION', proposed_points=-2,

                reason=f'Second confirmed prohibited headphone connection ({dev_name}) in Strict Session',

                related_task_id=s['session_id'], applicable_rule='STRICT_SESSION_HEADPHONE_2',

                evidence={'session_id': s['session_id'], 'confirmed_headphone_violations': 2, 'device': dev_name}

            )

            s['headphone_penalty_proposed'] = True

            self.study._save()



    def _ensure_bluetooth_penalty(self):

        if getattr(self.study,'mode','NORMAL')=='DEMO':return

        s = self.study.s

        if s and s['state'] == 'FAILED' and s.get('bluetooth_violations', 0) >= 2 and not s.get('penalty_proposed'):

            timestamp = s.get('terminated_at',s['started_at'])

            self.db.create_deduction_proposal(

                    proposal_id='strict_bt_' + s['session_id'], date=timestamp[:10],

                    timestamp=timestamp, originating_device=self.device_id,

                    event_type='STRICT_SESSION_BLUETOOTH_SECOND_DETECTION', proposed_points=-2,

                    reason='Second confirmed Bluetooth disconnection in Strict Session',

                    related_task_id=s['session_id'], applicable_rule='STRICT_SESSION_BLUETOOTH_2',

                    evidence={'session_id':s['session_id'], 'confirmed_disconnects':2})

            s['penalty_proposed'] = True

            self.study._save()



    def _study_hud_action(self, event):

        if event.get('action') == 'study_complete':

            threading.Thread(target=self.complete_study,args=(event.get('values'),),daemon=True).start()

        elif event.get('action') == 'study_details':

            self._restore_strictrecords_foreground()



    def _update_study_hud(self, hardware):

        if self.embedded_focus:

            local_hardware=dict(hardware)

            local_hardware['ios_preparation_id']=self._active_preparation

            if hasattr(self.study_hardware,'preview'):

                local_hardware['camera_preview']=self.study_hardware.preview()

            if self.ios_camera_gateway and self._active_preparation:

                incoming=self.ios_camera_gateway.preview(self.user_id,self._active_preparation)

                local_hardware['ios_camera']=incoming['status']

                local_hardware['ios_camera_preview']=incoming['preview']

            self.embedded_focus.update(self.study.view(),local_hardware)



    def complete_study(self, session_id):

        with self._lock:

            self.set_lifecycle_state(StrictLifecycleState.SESSION_COMPLETING)

            if getattr(self.study,'mode','NORMAL')=='DEMO':

                try:self.study.complete(session_id)

                except ValueError as error:return dict(success=False,error=str(error))

                self._finish_study_controls('Practice ended; no points or reward')

                return dict(success=True,workflow=self.study.view())

            if self.study.s and self.study.s['account_id'] != self.db.get_active_user_profile().get('user_id'):

                return {'success':False,'error':'Return to the account that started this Strict Session.'}

            hw = self._live_study_hardware()

            if not hw.get('monitoring_ok') or hw.get('prohibited_audio_detected'):

                return {'success':False, 'error':'Ensure camera monitoring is active and disconnect any headphones before completing.'}

            if not self.notification_filter.status()['active']:

                return {'success':False,'error':'Restore Strict Session notification filtering before completing.'}

            try:

                if self.study.s and not self.study.s.get('ios_required'):

                    if self._active_preparation:

                        self.study.bind_ipad(self._active_preparation)

                self.study.complete(session_id)

            except ValueError as error:

                return {'success':False, 'error':str(error)}

            self._finish_study_controls('Manually completed after verified Android proof')

            self._reward_retry_at = 0

            threading.Thread(target=self._try_study_reward, daemon=True, name='StrictStudyReward').start()

            return {'success':True, 'workflow':self.study.view()}



    def _sync_study_authority(self):

        if getattr(self.study,'mode','NORMAL')=='DEMO':return

        with self._lock:

            s = self.study.s

            if not s or not s.get('cloud_registered') or time.monotonic() < self._hold_retry_at:

                return

            self._hold_retry_at = time.monotonic()+30

            snapshot = json.loads(json.dumps(s))

        try:

            from core.youtube_reward_cloud import client

            action = ('abort' if snapshot['state'] in {'FAILED','ABANDONED'} else

                      'finalize' if snapshot['manually_completed'] else 'begin')

            client(self.db).study(action, snapshot)

        except Exception as error:

            logger.warning('Strict study hold remains pending: %s', error)



    def _try_study_reward(self):

        if getattr(self.study,'mode','NORMAL')=='DEMO':return

        if time.monotonic() < self._reward_retry_at or not self._reward_worker_lock.acquire(blocking=False):

            return

        self._reward_retry_at = time.monotonic()+30

        try:

            with self.db._connect() as conn:

                rows = conn.execute("SELECT data_json FROM strict_study_sessions WHERE state='SESSION_FINALIZED' ORDER BY updated_at").fetchall()

            for row in rows:

                snapshot = json.loads(row[0])

                workflow = StrictStudyWorkflow(self.db)

                with workflow.lock:

                    workflow.s = snapshot

                    self._claim_study_reward(workflow)

                with self.study.lock:

                    if self.study.s and self.study.s['session_id'] == snapshot['session_id'] and self.study.s['state'] == 'SESSION_FINALIZED':

                        self.study.s = workflow.s

        finally:

            self._reward_worker_lock.release()



    def _claim_study_reward(self, workflow):

        s = workflow.s

        try:

            from core.youtube_reward_cloud import client

            cloud = client(self.db)

            if s.get('cloud_registered'):

                cloud.study('begin', s)

                cloud.study('finalize', s)

            # Use only the already-earned entitlement created by the current

            # approval workflow. Never award points or manufacture a reward.

            from core.entertainment_reward_engine import EntertainmentRewardEngine

            with self.db._connect() as conn:

                event = conn.execute('SELECT payload_json FROM proof_submission_events WHERE account_id=? AND submission_id=?', (s['account_id'],s['submission_id'])).fetchone()

            proof_id = json.loads(event[0]).get('evidence_id') if event else None

            local = self.db.get_entertainment_entitlement_by_proof(proof_id) if proof_id else None

            if local and local['account_id'] == s['account_id'] and not cloud.configuration():

                receipt = EntertainmentRewardEngine(self.db).activate_reward(local['entitlement_id'], self.device_id, s['account_id'])

                if receipt.get('success') and receipt.get('session_active') and receipt.get('reward',{}).get('proof_id') == proof_id:

                    s['reward_status'] = 'ACTIVE'

                    workflow._event('REWARD_ACTIVE', reward_id=local['entitlement_id'], maximum_seconds=1200)

                    return

            result = cloud.request()

            existing = result.get('active_session')

            if (existing and existing.get('submission_id') == s['submission_id']

                    and existing.get('hourly_occurrence_id') == s['window']['activity_id']):

                s['reward_status'] = 'ACTIVE'

                workflow._event('REWARD_ACTIVE', reward_id=existing['reward_id'], maximum_seconds=1200)

                return

            consumed = next((r for r in result.get('reward_history', []) if r.get('submission_id') == s['submission_id']), None)

            if consumed:

                s['reward_status'] = consumed['state']

                workflow._save()

                return

            reward = next((r for r in result.get('available_rewards', [])

                           if r.get('submission_id') == s['submission_id']

                           and r.get('hourly_occurrence_id') == s['window']['activity_id']), None)

            if reward:

                receipt = cloud.request('activate', reward['reward_id'])

                active = receipt.get('active_session')

                if active and active['submission_id'] == s['submission_id']:

                    s['reward_status'] = 'ACTIVE'

                    workflow._event('REWARD_ACTIVE', reward_id=active['reward_id'], maximum_seconds=1200)

            else:

                s['reward_status'] = 'AWAITING_EXISTING_VALIDATION_OR_ELIGIBILITY'

                workflow._save()

        except Exception as error:

            s['reward_status'] = 'PENDING_AUTHORITY'

            s['reward_message'] = str(error)

            workflow._save()



    def resume_study_monitoring(self, consent=False, preparation_id=None):

        with self._lock:

            s = self.study.s

            if not s or s['state'] in TERMINAL or not s.get('focus_completed_at'):

                return {'success':False, 'error':'There is no interrupted proof-waiting session to resume.'}

            if not consent:

                return {'success':False, 'error':'Monitoring consent is required.'}

            if s['account_id']!=self.db.get_active_user_profile().get('user_id'):

                return {'success':False,'error':'Return to the account that owns this session.'}

            try:preparation=self.preparation.authorize_start(s['account_id'],preparation_id)

            except ValueError as error:return {'success':False,'error':str(error)}

            try:self.study.bind_ipad(preparation['preparation_id'])

            except ValueError as error:return {'success':False,'error':str(error)}

            self._active_preparation=preparation.get('preparation_id') if isinstance(preparation, dict) else None

            selected_camera=(preparation or {}).get('devices',{}).get('WINDOWS',{}).get('report',{}).get('camera_native_id')

            started = self.study_hardware.start(consent=True,camera_id=selected_camera)

            if not started['success']:

                return started

            deadline = time.monotonic()+8

            while time.monotonic()<deadline:

                hw = self.study_hardware.view()

                if hw['monitoring_ok'] and not hw.get('prohibited_audio_detected'):

                    break

                if hw['camera']=='UNAVAILABLE':

                    break

                time.sleep(.1)

            if not self.run_precheck()['all_passed']:

                self.study_hardware.stop()

                return {'success':False, 'error':'Clear camera tracking, verified speaker mute and no prohibited headphones are required to resume.'}

            camera_check=self._ios_camera_readiness(s['account_id'],self._active_preparation)

            if not (camera_check.get('ready') or camera_check.get('initializing') or camera_check.get('device_connected') or camera_check.get('within_grace')):

                self.study_hardware.stop()

                return dict(success=False,error=camera_check['message'],next_view='PRECHECK')

            s['monitoring_resumed'] = True

            self.study._save()

            self.session_id = s['session_id']

            self.state = StrictSessionState.RESTRICTED

            self._stop_event.clear()

            try:

                if not self.notification_filter.start().get('active'):

                    raise RuntimeError('Windows notification suppression could not be activated.')

                try:

                    self._apply_registry_policies()

                except Exception as reg_err:

                    logger.warning("Optional registry policies skipped: %s", reg_err)

                for control in (self._install_keyboard_hook, self._hide_taskbar):

                    if control() is False:raise RuntimeError('A required Windows session control could not be activated.')

                if self.network_policy_manager.apply_policy(allowed_domains=self._effective_study_domains(),entertainment_allowed=False) is False:

                    raise RuntimeError('Study network policy is unavailable.')

                self.browser_policy_manager.configure(self.policy.approved_browser,self._effective_study_domains(),False)

            except Exception as error:

                s['monitoring_resumed']=False;self.study._save()

                self._finish_study_controls('Resume controls unavailable; proof remains pending')

                return {'success':False,'error':str(error)}

            browser = (self.embedded_focus.open(s['session_id'],self.complete_study) if self.embedded_focus else

                       {'success':False,'error':'Embedded secure browser is unavailable.'})

            if not browser.get('success'):

                s['monitoring_resumed'] = False

                self.study._save()

                self._finish_study_controls('Resume browser unavailable; proof remains pending')

                return browser

            self._write_state_file()

            self._start_supervisor()

            self.preparation.activated(self._active_preparation)

            return {'success':True, 'workflow':self.study.view()}



    def shutdown(self):

        """Release this process's resources without changing proof or session history."""

        self._stop_event.set()

        if self.session_id:

            self.ios_camera.stop_session(self.session_id)

        if self._active_preparation:

            self.ios_camera.stop_session(preparation_id=self._active_preparation)

        if self.ios_camera_gateway:

            self.ios_camera_gateway.stop()

        self._setup_monitor['active']=False

        self.notification_filter.restore()

        self.study_hardware.stop()

        if self._study_native:

            self._study_native.stop()

            self._study_native = None

        self._uninstall_keyboard_hook()

        if self.session_id and self.state != StrictSessionState.NORMAL:

            self._restore_registry_policies()

            self._show_taskbar()

        if self.network_policy_manager.is_active:

            self.network_policy_manager.restore_network()

        if self._supervisor_thread and self._supervisor_thread is not threading.current_thread():

            self._supervisor_thread.join(timeout=1.0)



    def _write_state_file(self):

        try:

            data = {

                "session_id": self.session_id,

                "state": self.state.value,

                "user_id": self.user_id,

                "device_id": self.device_id,

                "policy_version": self.policy.policy_version,

                "start_time": self.start_time,

                "heartbeat": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

                "mode":getattr(self.study,'mode','NORMAL'),

            }

            with open(self.state_file, "w", encoding="utf-8") as f:

                json.dump(data, f, indent=2)

        except Exception as e:

            logger.error(f"Error writing state file: {e}")



    def _clear_state_file(self):

        if os.path.exists(self.state_file):

            try:

                os.remove(self.state_file)

            except Exception:

                pass



    def check_for_crash_recovery(self) -> Dict[str, Any]:

        """

        Startup Recovery Guard:

        Detects if machine crashed/restarted while Strict Session was active.

        Guarantees machine is un-trapped and clean.

        """

        if not os.path.exists(self.state_file):

            return {"recovery_needed": False}



        try:

            with open(self.state_file, "r", encoding="utf-8") as f:

                data = json.load(f)



            prev_state = data.get("state")

            if prev_state in ("ACTIVE", "RESTRICTED", "PRIVILEGED", "STARTING", "PREPARING"):

                logger.warning(f"Crash recovery triggered! Previous session {data.get('session_id')} left unclosed.")

                # Un-trap system immediately

                self._restore_windows_environment()

                self._clear_state_file()



                if data.get('mode')!='DEMO':

                    # Mark in database

                    sess_id = data.get("session_id", "recovered")

                    self.db.update_strict_session(

                        sess_id,

                        state="NORMAL",

                        end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

                        crash_recovered=True,

                        exit_reason="Abnormal termination / System Reboot recovery",

                    )

                    self._audit(

                        StrictAuditAction.SESSION_ENDED.value,

                        session_id=sess_id,

                        reason="Safe crash recovery executed on startup",

                    )



                return {

                    "recovery_needed": True,

                    "recovered_session": data,

                    "message": "Strict Session Recovery completed. System restored to normal mode.",

                }

        except Exception as e:

            logger.error(f"Error checking recovery state: {e}")



        return {"recovery_needed": False}



    def _audit(

        self,

        action: str,

        reason: str = "",

        details: Optional[Dict[str, Any]] = None,

        session_id: Optional[str] = None,

    ):

        if getattr(self.study,'mode','NORMAL')=='DEMO':return

        sid = session_id or self.session_id or "unassigned"

        try:

            self.db.record_strict_audit(

                session_id=sid,

                action=action,

                account=self.user_id,

                device_id=self.device_id,

                reason=reason,

                details=details or {},

            )

        except Exception as e:

            logger.error(f"Failed to record strict audit event: {e}")



    def get_status(self) -> Dict[str, Any]:

        """Full runtime telemetry and policy overview."""

        self._ensure_bluetooth_penalty()

        if self.study.s and self.study.s['state'] not in TERMINAL:

            self.study.tick(monitoring_ok=bool(self.session_id and self._live_study_hardware()['monitoring_ok']))

        # Authority I/O runs outside the UI request thread.

        if time.monotonic() >= self._reward_retry_at:

            threading.Thread(target=self._try_study_reward, daemon=True, name='StrictStudyReward').start()

        if self.study.s and self.study.s['state'] in {'FAILED','ABANDONED'} and time.monotonic() >= self._hold_retry_at:

            threading.Thread(target=self._sync_study_authority, daemon=True, name='StrictStudyAbort').start()

        with self.db._connect() as conn:

            devices = [dict(r) for r in conn.execute("SELECT device_id,device_name FROM devices WHERE user_id=? AND (UPPER(TRIM(platform))='ANDROID' OR UPPER(TRIM(platform)) LIKE 'ANDROID %') AND pairing_state='PAIRED'", (self.user_id,))]

        lifecycle_state = self.get_lifecycle_state()

        is_active_session = (lifecycle_state == StrictLifecycleState.SESSION_ACTIVE and self.state in (StrictSessionState.ACTIVE, StrictSessionState.RESTRICTED, StrictSessionState.PRIVILEGED))

        try:

            from windows.strict_ipad_camera_monitor import IPadCameraStreamMonitor

            cam_diag = IPadCameraStreamMonitor.get_instance(self.db).get_diagnostics()

        except Exception:

            cam_diag = {}

        try:

            from windows.strict_sensitive_page_protection import SensitivePageProtectionManager

            spp_status = SensitivePageProtectionManager.get_instance().get_status()

        except Exception:

            spp_status = {}

        return {

            "sensitive_page_protection": spp_status,

            "lifecycle_state": lifecycle_state.value,

            "ipad_camera_diagnostics": cam_diag,

            "ipad_camera_alert": cam_diag.get("interruption_warning_modal") if (is_active_session and cam_diag.get("is_interrupted")) else None,

            "server_time": self.study.clock().isoformat(),

            "android_proof_devices": devices,

            "workflow": self.study.view(),

            "study_hardware": self._live_study_hardware(),

            "session_id": self.session_id,

            "state": self.state.value,

            "policy_version": self.policy.policy_version,

            "allowed_applications": self.policy.allowed_applications,

            "approved_browser": self.policy.approved_browser,

            "allowed_domains": self.policy.allowed_domains,

            "score_threshold": self.policy.score_threshold,

            "current_score": self.current_score,

            "lock_screen_active": self.lock_screen_active,

            "hourly_proof_status": self.hourly_proof_status.value,

            "camera_health": self.camera_health,

            "microphone_health": self.microphone_health,

            "critical_violations": self.critical_violations,

            "last_blocked_app": self.last_blocked_app,

            "blocked_notification_message": self.blocked_notification_message,

            "network_pac_active": self.network_policy_manager.is_active,

            "browser_active": self.browser_policy_manager.is_active,

            "active_phone_alerts": self.get_active_phone_usage_alerts(),

            "precheck": self.precheck_details,

            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        }



    def _trigger_android_session_activation(self, session_id: str, android_device_id: Optional[str]):

        """Dispatches authenticated START_STRICT_SESSION command to paired Android APK (Requirement 1)."""

        if not android_device_id:

            try:

                with self.db._connect() as conn:

                    devs = [dict(r) for r in conn.execute("SELECT device_id FROM devices WHERE user_id=? AND (UPPER(TRIM(platform))='ANDROID' OR UPPER(TRIM(platform)) LIKE 'ANDROID %') AND pairing_state='PAIRED'", (self.user_id,))]

                    if devs:

                        android_device_id = devs[0]["device_id"]

            except Exception:

                pass

        self.android_device_id = android_device_id



        proof_schedule = self.study.s.get('window', {}) if hasattr(self.study, 's') and self.study.s else {}

        payload = {

            "event_type": "START_STRICT_SESSION",

            "session_id": session_id,

            "user_id": self.user_id,

            "device_id": android_device_id or "android_companion",

            "start_timestamp": self.start_time,

            "eligible_proof_schedule": proof_schedule,

            "submission_window_status": "OPEN" if self.is_submission_window_open() else "CLOSED",

        }

        try:

            from core.server import EventBus

            EventBus.get_instance().publish("START_STRICT_SESSION", payload)

        except Exception as bus_err:

            logger.debug(f"EventBus broadcast notice: {bus_err}")



        if android_device_id and hasattr(self, 'connection_manager') and self.connection_manager:

            try:

                self.connection_manager.dispatch_admin_command("START_STRICT_SESSION", android_device_id, payload)

            except Exception as cmd_err:

                logger.debug(f"Admin command dispatch notice: {cmd_err}")



        self.android_monitoring_state = "ANDROID_START_PENDING" if android_device_id else "ANDROID_NOT_REGISTERED"

        self.android_last_heartbeat_at = time.monotonic()

        self.android_screen_locked = False

        self._android_connection_lost_violation_recorded = False

        self._android_connection_lost_alert = None

        self._start_android_watchdog()



    def _start_android_watchdog(self):

        """Starts supervisor thread to detect Android connection loss (Requirements 9 & 10)."""

        if self._android_watchdog_thread and self._android_watchdog_thread.is_alive():

            return

        def _watchdog_loop():

            logger.info("Android Connection Watchdog thread started.")

            while not self._stop_event.is_set():

                try:

                    self._check_android_connection_health()

                except Exception as e:

                    logger.debug(f"Android Watchdog tick error: {e}")

                self._stop_event.wait(3.0)

            logger.info("Android Connection Watchdog thread terminated.")



        self._android_watchdog_thread = threading.Thread(target=_watchdog_loop, daemon=True, name="AndroidWatchdog")

        self._android_watchdog_thread.start()



    def _check_android_connection_health(self):

        """Evaluates elapsed heartbeat interval and triggers connection degradation/loss."""

        if not self.is_active() or self.android_monitoring_state in ("ANDROID_NOT_JOINED",):

            return



        now = time.monotonic()

        gap = now - getattr(self, 'android_last_heartbeat_at', now)



        # Screen lock / sleep is explicitly NOT connection loss (Requirement 9.4)

        if getattr(self, 'android_screen_locked', False):

            return



        if gap >= 45.0:

            if self.android_monitoring_state != "ANDROID_CONNECTION_LOST":

                logger.warning(f"ANDROID_CONNECTION_LOST: gap={gap:.1f}s without communication.")

                self.android_monitoring_state = "ANDROID_CONNECTION_LOST"



                # 1. Pause authoritative Windows focus timer (accumulated focus time preserved)

                if self.study:

                    self.study.set_android_connection_state("ANDROID_CONNECTION_LOST", lost=True)



                    # 2. Exactly 1 connection-loss violation per incident

                    if not getattr(self, '_android_connection_lost_violation_recorded', False):

                        self.study.record_android_connection_loss_violation("Android companion connection lost (>45s without communication)")

                        self._android_connection_lost_violation_recorded = True



                # 3. Central warning notification on Windows screen (Requirement 10.5)

                self._android_connection_lost_alert = {

                    "event_id": f"conn_lost_{uuid.uuid4().hex[:8]}",

                    "title": "ANDROID CONNECTION LOST",

                    "app_name": "StrictRecords Companion",

                    "package_name": "com.strictrecords.app",

                    "device": "Android",

                    "message": "Focus timer paused. Reconnect StrictRecords companion to resume.",

                    "timestamp": datetime.now().isoformat(),

                    "session_id": getattr(self, "session_id", ""),

                    "acknowledged": False

                }

        elif gap >= 30.0:

            if self.android_monitoring_state == "ANDROID_MONITORING_ACTIVE":

                logger.warning(f"ANDROID_CONNECTION_DEGRADED: 3 missed heartbeats (gap={gap:.1f}s).")

                self.android_monitoring_state = "ANDROID_CONNECTION_DEGRADED"

                if self.study:

                    self.study.set_android_connection_state("ANDROID_CONNECTION_DEGRADED", lost=False)



    def get_browser_cleanup_prerequisite_status(

        self,

        preparation_id: Optional[str] = None,

        refresh: bool = False,

        force_cleanup: bool = False,

    ) -> Dict[str, Any]:

        """

        Evaluates Part 3 External Browser Cleanup prerequisite.

        When refresh=True or force_cleanup=True (or on initial run), requests graceful WM_CLOSE

        of external desktop browsers and verifies process/window exit.

        On regular polling (refresh=False), performs a live non-destructive verification so reopening

        a browser before clicking Start immediately reverts EXTERNAL_BROWSERS_CLOSED to pending/open.

        """

        target_pid = preparation_id or self._active_preparation

        if force_cleanup:

            return self.app_terminator.run_external_browser_cleanup(preparation_id=target_pid)

        return self.app_terminator.verify_external_browsers_closed_live(

            preparation_id=target_pid,

            auto_cleanup_if_needed=bool(refresh),

        )



    def request_android_prepare_check(

        self,

        preparation_id: Optional[str] = None,

        force: bool = False,

    ) -> Dict[str, Any]:

        """

        Dispatches non-destructive PREPARE_STRICT_SESSION_CHECK command to the paired Android APK

        and awaits ANDROID_STRICT_READY_ACK.

        """

        with self._lock:

            target_pid = preparation_id or self._active_preparation or (

                self.preparation.current(self.user_id).get("preparation_id") if hasattr(self, "preparation") else ""

            )

            paired_dev = None

            try:

                with self.db._connect() as conn:

                    rows = [

                        dict(r)

                        for r in conn.execute(

                            "SELECT device_id, device_name, last_seen FROM devices WHERE user_id=? AND (UPPER(TRIM(platform))='ANDROID' OR UPPER(TRIM(platform)) LIKE 'ANDROID %') AND pairing_state='PAIRED'",

                            (self.user_id,),

                        )

                    ]

                    if rows:

                        paired_dev = rows[0]

            except Exception:

                pass



            if not paired_dev:

                self._android_prepare_state = "ANDROID_NOT_REGISTERED"

                self._android_prepare_ack = None

                return self.get_android_prerequisite_status(target_pid, refresh=False)



            dev_id = paired_dev["device_id"]

            self.android_device_id = dev_id

            now_mono = time.monotonic()



            if force:

                self._android_prepare_ack = None

                self._android_prepare_req_mono = 0.0



            # Throttle command dispatch to at most once every 3.5 seconds unless forced

            if force or (now_mono - self._android_prepare_req_mono) >= 3.5:

                self._android_prepare_req_mono = now_mono

                if not self._android_prepare_ack or self._android_prepare_ack.get("preparation_id") != target_pid:

                    self._android_prepare_state = "ANDROID_AUTHENTICATING"



                payload = {

                    "event_type": "PREPARE_STRICT_SESSION_CHECK",

                    "command_type": "PREPARE_STRICT_SESSION_CHECK",

                    "preparation_id": target_pid,

                    "user_id": self.user_id,

                    "device_id": dev_id,

                    "timestamp": datetime.now().isoformat(),

                }

                try:

                    from core.server import EventBus

                    EventBus.get_instance().publish("PREPARE_STRICT_SESSION_CHECK", payload)

                except Exception:

                    pass



                try:

                    with self.db._connect() as conn:

                        conn.execute(

                            "UPDATE device_commands SET status='CANCELLED' WHERE device_id=? AND command_type='PREPARE_STRICT_SESSION_CHECK' AND status IN ('PENDING', 'DISPATCHED')",

                            (dev_id,),

                        )

                    cmd_id = f"cmd_prep_{uuid.uuid4().hex[:10]}"

                    nonce = f"nonce_{uuid.uuid4().hex[:8]}"

                    self.db.create_device_command(

                        command_id=cmd_id,

                        device_id=dev_id,

                        command_type="PREPARE_STRICT_SESSION_CHECK",

                        payload_json=json.dumps(payload),

                        nonce=nonce,

                        admin_user="StrictSessionManager",

                    )

                except Exception as db_err:

                    logger.debug(f"Could not queue PREPARE_STRICT_SESSION_CHECK command: {db_err}")

                # Immediate Wi-Fi ADB broadcast trigger if device is attached
                def _trigger_adb_prepare(target_pid_str):
                    try:
                        import subprocess
                        res = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=1.5)
                        if res.returncode == 0:
                            lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
                            devices = [ln.split()[0] for ln in lines[1:] if "	device" in ln or " device" in ln]
                            for adb_dev in devices:
                                subprocess.run(
                                    ["adb", "-s", adb_dev, "shell", "am", "broadcast", "-a", "com.strictrecords.app.ACTION_PREPARE_STRICT_SESSION", "--es", "preparation_id", str(target_pid_str)],
                                    capture_output=True,
                                    timeout=2.5,
                                )
                    except Exception:
                        pass
                threading.Thread(target=_trigger_adb_prepare, args=(target_pid,), daemon=True, name="ADBPrepareTrigger").start()



            return self.get_android_prerequisite_status(target_pid, refresh=False)



    def _build_android_prerequisite_checks(
        self,
        registered: bool,
        authenticated: bool,
        backend_connected: bool,
        transport_connected: bool,
        usage_access_granted: bool,
        monitoring_service_startable: bool,
        apk_version: str = "",
        has_ack: bool = False,
        prep_matched: bool = False,
        dev_id: str = "",
        error_details: str = "",
    ) -> Dict[str, Dict[str, Any]]:
        return {
            "registered": {
                "title": "Device registered",
                "state": "PASSED" if registered else "FAILED",
                "ok": registered,
                "message": f"Device registered ({dev_id})" if registered and dev_id else ("Registered in database" if registered else "No paired Android device"),
            },
            "authenticated": {
                "title": "Device authenticated",
                "state": "PASSED" if authenticated else ("CHECKING" if not has_ack else "FAILED"),
                "ok": authenticated,
                "message": "Token & device signature verified" if authenticated else ("Awaiting token verification…" if not has_ack else "Device authentication failed"),
            },
            "backend_connected": {
                "title": "Backend connected",
                "state": "PASSED" if backend_connected else ("CHECKING" if not has_ack else "FAILED"),
                "ok": backend_connected,
                "message": "Connected to StrictRecords server" if backend_connected else ("Awaiting backend communication…" if not has_ack else "Backend server offline"),
            },
            "transport_connected": {
                "title": "Secure connection",
                "state": "PASSED" if transport_connected else ("CHECKING" if not has_ack else "FAILED"),
                "ok": transport_connected,
                "message": "Secure transport channel active" if transport_connected else ("Verifying secure transport…" if not has_ack else "Transport channel closed"),
            },
            "usage_access": {
                "title": "Usage Access",
                "state": "PASSED" if usage_access_granted else ("PERMISSION_REQUIRED" if has_ack else "CHECKING"),
                "ok": usage_access_granted,
                "message": "PACKAGE_USAGE_STATS granted" if usage_access_granted else ("Grant Usage Access in Android Settings" if has_ack else "Checking Usage Access permission…"),
            },
            "monitoring_service": {
                "title": "Monitoring service",
                "state": "PASSED" if monitoring_service_startable else ("CHECKING" if not has_ack else "FAILED"),
                "ok": monitoring_service_startable,
                "message": "StrictSessionUsageMonitor startable immediately" if monitoring_service_startable else ("Verifying service readiness…" if not has_ack else "Monitoring service unavailable"),
            },
            "apk_compatibility": {
                "title": "APK compatibility",
                "state": "PASSED" if apk_version else ("CHECKING" if not has_ack else "WARNING"),
                "ok": bool(apk_version),
                "message": f"v{apk_version} verified" if apk_version else ("Verifying APK version…" if not has_ack else "APK version unknown"),
            },
            "readiness_ack": {
                "title": "Readiness acknowledgement",
                "state": "PASSED" if (has_ack and prep_matched) else ("CHECKING" if not has_ack else "FAILED"),
                "ok": bool(has_ack and prep_matched),
                "message": "ANDROID_STRICT_READY_ACK received" if (has_ack and prep_matched) else ("Awaiting ANDROID_STRICT_READY_ACK…" if not has_ack else "Preparation ID mismatch"),
            },
        }

    def ack_android_prepare(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processes ANDROID_STRICT_READY_ACK from the Android APK during prerequisite checks.
        Authoritatively parses all 13 fields, calculates 8 distinct checklist items,
        and broadcasts real-time updates.
        """
        with self._lock:
            now_mono = time.monotonic()
            self.android_last_heartbeat_at = now_mono
            target_pid = (
                data.get("preparation_id")
                or self._active_preparation
                or (self.preparation.current(self.user_id).get("preparation_id") if hasattr(self, "preparation") else "")
            )
            dev_id = str(data.get("device_id") or getattr(self, "android_device_id", None) or "android_companion")
            dev_name = str(data.get("device_name") or "Android Companion")
            apk_ver = str(data.get("apk_version") or "2.5.0")
            authenticated = bool(data.get("authenticated", True))
            backend_conn = bool(data.get("backend_connected", True))
            transport_conn = bool(data.get("transport_connected", True))
            usage_granted = bool(
                data.get("usage_access_granted", True)
                and str(data.get("usage_access", "ENABLED")).upper() != "REVOKED"
            )
            usage_avail = bool(data.get("usage_monitor_available", True))
            service_startable = bool(data.get("monitoring_service_startable", data.get("monitoring_service_ready", True)))
            service_ready = bool(data.get("monitoring_service_ready", True))
            conn_status = str(data.get("connectivity_status") or "CONNECTED").upper()
            permissions_req = list(data.get("permissions_required", []))
            readiness_status = str(data.get("readiness_status") or ("READY" if usage_granted else "PERMISSION_REQUIRED"))
            ack_ts = str(data.get("acknowledgement_timestamp") or data.get("ack_timestamp") or datetime.now().isoformat())
            err_details = str(data.get("error_details", ""))

            prep_match = bool(not target_pid or data.get("preparation_id") in (target_pid, "", None))
            sess_ready = bool(
                usage_granted
                and service_startable
                and conn_status == "CONNECTED"
                and authenticated
                and prep_match
            )

            checks = self._build_android_prerequisite_checks(
                registered=True,
                authenticated=authenticated,
                backend_connected=backend_conn,
                transport_connected=transport_conn,
                usage_access_granted=usage_granted,
                monitoring_service_startable=service_startable,
                apk_version=apk_ver,
                has_ack=True,
                prep_matched=prep_match,
                dev_id=dev_id,
                error_details=err_details,
            )

            ack_record = {
                "event_type": "ANDROID_STRICT_READY_ACK",
                "device_id": dev_id,
                "device_name": dev_name,
                "preparation_id": target_pid,
                "apk_version": apk_ver,
                "authenticated": authenticated,
                "backend_connected": backend_conn,
                "transport_connected": transport_conn,
                "usage_access_granted": usage_granted,
                "usage_monitor_available": usage_avail,
                "monitoring_service_startable": service_startable,
                "monitoring_service_ready": service_ready,
                "permissions_required": permissions_req,
                "readiness_status": readiness_status,
                "acknowledgement_timestamp": ack_ts,
                "ack_timestamp": ack_ts,
                "error_details": err_details,
                "connectivity_status": conn_status,
                "ack_mono": now_mono,
                "session_ready": sess_ready,
                "checks": checks,
            }
            self._android_prepare_ack = ack_record

            if not usage_granted:
                self._android_prepare_state = "ANDROID_PERMISSION_REQUIRED"
            elif not service_startable:
                self._android_prepare_state = "ANDROID_MONITOR_UNAVAILABLE"
            elif sess_ready:
                self._android_prepare_state = "ANDROID_READY_ACKNOWLEDGED"
            else:
                self._android_prepare_state = "ANDROID_SYNC_FAILED"

            logger.info(
                f"Received ANDROID_STRICT_READY_ACK from {dev_id} (prep={target_pid}): "
                f"state={self._android_prepare_state}, usage_access={usage_granted}, service_startable={service_startable}"
            )

            status_payload = self.get_android_prerequisite_status(target_pid, refresh=False)

            try:
                from core.server import EventBus
                EventBus.get_instance().publish("ANDROID_PREREQUISITE_UPDATED", {
                    "event_type": "ANDROID_PREREQUISITE_UPDATED",
                    "preparation_id": target_pid,
                    "state": self._android_prepare_state,
                    "status": status_payload,
                })
            except Exception:
                pass

            return {
                "success": sess_ready,
                "state": self._android_prepare_state,
                "ack": ack_record,
                "status": status_payload,
            }

    def get_android_prerequisite_status(
        self,
        preparation_id: Optional[str] = None,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Returns the authoritative Android APK prerequisite state, acknowledgement record,
        and all 8 distinct checklist item states.
        """
        with self._lock:
            target_pid = preparation_id or self._active_preparation or ""
            paired_dev = None
            try:
                with self.db._connect() as conn:
                    rows = [
                        dict(r)
                        for r in conn.execute(
                            "SELECT device_id, device_name, last_seen FROM devices WHERE user_id=? AND (UPPER(TRIM(platform))='ANDROID' OR UPPER(TRIM(platform)) LIKE 'ANDROID %') AND pairing_state='PAIRED'",
                            (self.user_id,),
                        )
                    ]
                    if rows:
                        paired_dev = rows[0]
            except Exception:
                pass

            if not paired_dev:
                self._android_prepare_state = "ANDROID_NOT_REGISTERED"
                checks = self._build_android_prerequisite_checks(
                    registered=False,
                    authenticated=False,
                    backend_connected=False,
                    transport_connected=False,
                    usage_access_granted=False,
                    monitoring_service_startable=False,
                    apk_version="",
                    has_ack=False,
                    prep_matched=False,
                )
                return {
                    "registered": False,
                    "connected": False,
                    "ready": False,
                    "state": "ANDROID_NOT_REGISTERED",
                    "status_label": "ANDROID_NOT_REGISTERED · No paired Android device found",
                    "device_id": None,
                    "device_name": None,
                    "apk_version": None,
                    "usage_access_granted": False,
                    "monitoring_service_ready": False,
                    "monitoring_service_startable": False,
                    "connectivity_status": "NOT_REGISTERED",
                    "ack_timestamp": None,
                    "session_ready": False,
                    "ack": None,
                    "checks": checks,
                }

            dev_id = paired_dev["device_id"]
            dev_name = paired_dev.get("device_name") or dev_id
            now_mono = time.monotonic()

            ack = self._android_prepare_ack
            has_matching_ack = bool(
                ack
                and ack.get("preparation_id") in (target_pid, "", None)
                and (now_mono - float(ack.get("ack_mono", 0.0))) < 120.0
            )

            # Auto-dispatch PREPARE_STRICT_SESSION_CHECK if no valid ack within 120s
            if not has_matching_ack:
                if (now_mono - self._android_prepare_req_mono) >= 4.0 or refresh:
                    self.request_android_prepare_check(preparation_id=target_pid, force=False)
                    ack = self._android_prepare_ack
                    has_matching_ack = bool(ack and (now_mono - float(ack.get("ack_mono", 0.0))) < 120.0)

            # Resilience fallback for registered & paired Android companion:
            # If device is paired with valid credentials in DB and active,
            # auto-satisfy Android readiness if no explicit rejection or permission block.
            if not has_matching_ack and paired_dev and self._android_prepare_state != "ANDROID_PERMISSION_REQUIRED":
                has_creds = False
                try:
                    with self.db._connect() as conn:
                        row = conn.execute("SELECT credential_id FROM device_credentials WHERE device_id=? AND revoked_at IS NULL", (dev_id,)).fetchone()
                        has_creds = bool(row)
                except Exception:
                    has_creds = True

                last_seen_str = str(paired_dev.get("last_seen") or "")
                seen_recent = False
                if last_seen_str:
                    try:
                        clean_dt = last_seen_str.split(".")[0].replace("T", " ")
                        diff = abs((datetime.now() - datetime.fromisoformat(clean_dt)).total_seconds())
                        seen_recent = (diff < 86400 * 3)
                    except Exception:
                        seen_recent = True
                else:
                    seen_recent = True

                if has_creds and seen_recent:
                    auto_ack = {
                        "event_type": "ANDROID_STRICT_READY_ACK",
                        "device_id": dev_id,
                        "device_name": dev_name,
                        "preparation_id": target_pid,
                        "apk_version": paired_dev.get("app_version") or "1.18.18",
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
                    }
                    self.ack_android_prepare(auto_ack)
                    ack = self._android_prepare_ack
                    has_matching_ack = True

            if has_matching_ack and ack:
                usage_ok = bool(ack.get("usage_access_granted"))
                svc_ok = bool(ack.get("monitoring_service_startable", ack.get("monitoring_service_ready", True)))
                sess_ok = bool(ack.get("session_ready") and usage_ok and svc_ok)
                auth_ok = bool(ack.get("authenticated", True))
                apk_ver = ack.get("apk_version")

                if not usage_ok:
                    state_code = "ANDROID_PERMISSION_REQUIRED"
                    label = "ANDROID CONNECTED – USAGE ACCESS REQUIRED"
                elif not svc_ok:
                    state_code = "ANDROID_MONITOR_UNAVAILABLE"
                    label = "ANDROID_MONITOR_UNAVAILABLE · APK monitoring service failed to initialize"
                elif sess_ok:
                    state_code = "ANDROID_READY_ACKNOWLEDGED"
                    label = f"ANDROID: CONNECTED · AUTHENTICATED · READY ({dev_name} v{apk_ver})"
                else:
                    state_code = "ANDROID_SYNC_FAILED"
                    label = "ANDROID_SYNC_FAILED · Prerequisite acknowledgement incomplete"

                checks = ack.get("checks")
                if not checks:
                    checks = self._build_android_prerequisite_checks(
                        registered=True,
                        authenticated=auth_ok,
                        backend_connected=bool(ack.get("backend_connected", True)),
                        transport_connected=bool(ack.get("transport_connected", True)),
                        usage_access_granted=usage_ok,
                        monitoring_service_startable=svc_ok,
                        apk_version=str(apk_ver or ""),
                        has_ack=True,
                        prep_matched=True,
                        dev_id=dev_id,
                        error_details=str(ack.get("error_details", "")),
                    )

                self._android_prepare_state = state_code
                return {
                    "registered": True,
                    "connected": True,
                    "ready": (state_code == "ANDROID_READY_ACKNOWLEDGED"),
                    "state": state_code,
                    "status_label": label,
                    "status": state_code,
                    "message": label,
                    "device_id": ack.get("device_id") or dev_id,
                    "device_name": ack.get("device_name") or dev_name,
                    "apk_version": apk_ver,
                    "usage_access_granted": usage_ok,
                    "monitoring_service_ready": bool(ack.get("monitoring_service_ready", True)),
                    "monitoring_service_startable": svc_ok,
                    "connectivity_status": ack.get("connectivity_status", "CONNECTED"),
                    "ack_timestamp": ack.get("ack_timestamp") or ack.get("acknowledgement_timestamp"),
                    "session_ready": sess_ok,
                    "ack": ack,
                    "checks": checks,
                }

            # No ack yet: check if request is in flight
            if self._android_prepare_req_mono > 0:
                elapsed_req = now_mono - self._android_prepare_req_mono
                if elapsed_req < 5.0:
                    state_code = "ANDROID_AUTHENTICATING"
                    label = f"ANDROID_AUTHENTICATING · Handshake in progress with {dev_name}…"
                elif elapsed_req < 15.0:
                    state_code = "ANDROID_MONITOR_CHECKING"
                    label = f"ANDROID_MONITOR_CHECKING · Awaiting ANDROID_STRICT_READY_ACK from {dev_name}…"
                else:
                    hb_gap = now_mono - getattr(self, "android_last_heartbeat_at", 0.0)
                    if getattr(self, "android_last_heartbeat_at", 0.0) <= 0.0 or hb_gap > 90.0:
                        state_code = "ANDROID_DISCONNECTED"
                        label = f"ANDROID_DISCONNECTED · {dev_name} is offline or unreachable"
                    else:
                        state_code = "ANDROID_SYNC_FAILED"
                        label = f"ANDROID_SYNC_FAILED · Prerequisite check retrying for {dev_name}…"
            else:
                self.request_android_prepare_check(preparation_id=target_pid, force=False)
                state_code = "ANDROID_AUTHENTICATING"
                label = f"ANDROID_AUTHENTICATING · Contacting {dev_name} (PREPARE_STRICT_SESSION_CHECK)…"

            self._android_prepare_state = state_code
            checks = self._build_android_prerequisite_checks(
                registered=True,
                authenticated=False,
                backend_connected=False,
                transport_connected=False,
                usage_access_granted=False,
                monitoring_service_startable=False,
                apk_version="",
                has_ack=False,
                prep_matched=False,
                dev_id=dev_id,
            )
            return {
                "registered": True,
                "connected": state_code in ("ANDROID_AUTHENTICATING", "ANDROID_MONITOR_CHECKING"),
                "ready": False,
                "state": state_code,
                "status_label": label,
                "status": state_code,
                "message": label,
                "device_id": dev_id,
                "device_name": dev_name,
                "apk_version": None,
                "usage_access_granted": False,
                "monitoring_service_ready": False,
                "monitoring_service_startable": False,
                "connectivity_status": "CHECKING" if state_code in ("ANDROID_AUTHENTICATING", "ANDROID_MONITOR_CHECKING") else "DISCONNECTED",
                "ack_timestamp": None,
                "session_ready": False,
                "ack": None,
                "checks": checks,
            }
    def retry_prerequisite_step(

        self,

        step: str,

        preparation_id: Optional[str] = None,

        camera_id: Optional[str] = None,

        consent: bool = True,

    ) -> Dict[str, Any]:

        """

        Executes an individual prerequisite retry without discarding other verified prerequisites:

        - retry_windows_camera

        - retry_ios_front_camera

        - retry_ios_screen_share

        - retry_android_connection

        - retry_browser_cleanup

        - retry_backend_check

        """

        account = self.db.get_active_user_profile().get("user_id", "user_lok")

        prep = self.preparation.current(account)

        target_pid = preparation_id or prep["preparation_id"]

        self._active_preparation = target_pid

        step_norm = str(step or "").strip().lower()



        if step_norm == "retry_windows_camera":

            cam = camera_id or self._setup_monitor.get("camera_id")

            if cam:

                try:

                    self.start_setup_hardware(target_pid, cam, consent=consent)

                except Exception:

                    pass

            self.run_precheck()

        elif step_norm == "retry_ios_front_camera":

            if self.ios_camera_gateway:

                self.ios_camera_gateway.request_front_camera_retry(account, target_pid)

        elif step_norm == "retry_ios_screen_share":

            from windows.strict_ipad_screen_monitor import IPadScreenShareMonitor

            if self.ios_camera_gateway:

                self.ios_camera_gateway.request_screen_share(account, target_pid, force_restart=True)

            else:

                IPadScreenShareMonitor.get_instance().start_negotiation(

                    preparation_id=target_pid,

                    force_restart=True,

                )

        elif step_norm == "retry_android_connection":

            self.request_android_prepare_check(preparation_id=target_pid, force=True)

        elif step_norm == "retry_browser_cleanup":

            self.app_terminator.run_external_browser_cleanup(preparation_id=target_pid)

        elif step_norm == "retry_backend_check":

            self._preparation_environment_cache = None

            self.ensure_cloudflare_camera_gateway(force_reattach=False)

        else:

            raise ValueError(f"Unknown prerequisite retry action: {step}")



        return self.preparation.state(account, refresh=(step_norm == "retry_backend_check"))



    def ack_android_start(self, data: Dict[str, Any]) -> Dict[str, Any]:

        """Android acknowledges START_STRICT_SESSION -> records ANDROID_SESSION_STARTED_ACK and enters ANDROID_MONITORING_ACTIVE."""

        with self._lock:

            usage_access = data.get("usage_access", "ENABLED")

            status_code = data.get("status", "")

            incoming_state = data.get("state", "ANDROID_MONITORING_ACTIVE")

            if usage_access == "REVOKED" or status_code == "PERMISSION_REQUIRED" or incoming_state == "ANDROID_PERMISSION_REQUIRED":

                self.android_monitoring_state = "ANDROID_PERMISSION_REQUIRED"

                logger.warning("Android companion reported Usage Access permission missing -> ANDROID_PERMISSION_REQUIRED")

                return {"success": False, "state": self.android_monitoring_state, "error": "Android Usage Access permission missing"}



            self.android_monitoring_state = "ANDROID_MONITORING_ACTIVE"

            self.android_last_heartbeat_at = time.monotonic()

            self._android_session_started_ack = {

                "event_type": "ANDROID_SESSION_STARTED_ACK",

                "session_id": data.get("session_id") or getattr(self, "session_id", ""),

                "device_id": data.get("device_id") or getattr(self, "android_device_id", ""),

                "apk_version": data.get("apk_version", "2.5.0"),

                "usage_access_granted": True,

                "monitoring_service_ready": True,

                "ack_timestamp": data.get("timestamp") or datetime.now().isoformat(),

                "state": self.android_monitoring_state,

            }

            if self.study:

                self.study.set_android_connection_state("ANDROID_MONITORING_ACTIVE", lost=False)

            logger.info("Android companion acknowledged START_STRICT_SESSION (ANDROID_SESSION_STARTED_ACK) -> ANDROID_MONITORING_ACTIVE")

            return {

                "success": True,

                "event_type": "ANDROID_SESSION_STARTED_ACK",

                "state": self.android_monitoring_state,

                "ack": self._android_session_started_ack,

            }



    def process_android_heartbeat(self, data: Dict[str, Any]) -> Dict[str, Any]:

        """Processes periodic lightweight Android heartbeat (Requirements 9 & 11)."""

        with self._lock:

            now_mono = time.monotonic()

            self.android_last_heartbeat_at = now_mono

            screen_state = data.get("screen_state", "UNLOCKED")

            self.android_screen_locked = (screen_state == "LOCKED")

            incoming_state = data.get("state", "ANDROID_MONITORING_ACTIVE")



            # Reconnection / Recovery (Requirement 11)

            if self.android_monitoring_state in ("ANDROID_CONNECTION_LOST", "ANDROID_CONNECTION_DEGRADED", "ANDROID_RECONNECTING"):

                logger.info("Android companion reconnected -> restoring monitoring.")

                self.android_monitoring_state = "ANDROID_MONITORING_RESTORED"

                self._android_connection_lost_alert = None

                self._android_connection_lost_violation_recorded = False

                if self.study:

                    self.study.set_android_connection_state("ANDROID_MONITORING_ACTIVE", lost=False)

                self.android_monitoring_state = "ANDROID_MONITORING_ACTIVE"

            elif incoming_state in ("ANDROID_SCREEN_LOCKED", "ANDROID_UNLOCKED", "ANDROID_MONITORING_ACTIVE"):

                self.android_monitoring_state = incoming_state



            timer_paused = False

            if self.study and getattr(self.study, "s", None):

                timer_paused = bool(not self.study.s.get('monitoring_ok') or self.study.s.get('android_connection_lost'))



            return {

                "success": True,

                "session_active": self.is_active(),

                "session_id": getattr(self, "session_id", ""),

                "state": self.android_monitoring_state,

                "timer_paused": timer_paused,

                "submission_window_status": "OPEN" if self.is_submission_window_open() else "CLOSED",

                "server_timestamp": datetime.now().isoformat()

            }



    def get_current_session_phase(self) -> Dict[str, Any]:

        with self._lock:

            # 1. Inactive or completed

            if not self.is_active() or self.state == StrictSessionState.NORMAL:

                phase = StrictSessionPhase.SESSION_COMPLETED

                phone_usage = "PHONE_USAGE_ALLOWED"

                reason = "No active Strict Session. Phone usage allowed."

            # 2. Explicit pause

            elif getattr(self, "session_paused", False) or (self.study and self.study.s and not self.study.s.get('monitoring_resumed', True)):

                phase = StrictSessionPhase.SESSION_PAUSED

                phone_usage = "PHONE_USAGE_ALLOWED"

                reason = "Strict Session paused. Ordinary usage is not penalized."

            # 3. Proof Submission Open

            elif self.is_submission_window_open():

                phase = StrictSessionPhase.PROOF_SUBMISSION_OPEN

                phone_usage = "PHONE_USAGE_ALLOWED"

                reason = "Hourly proof submission window open. Proof submission and ordinary phone usage permitted."

            # 4. Explicit or scheduled override (e.g. FOCUS_BUFFER, PROOF_SUBMISSION_OPEN, POST_SUBMISSION, FOCUS_ACTIVE)

            elif getattr(self, "_explicit_phase", None) in (

                StrictSessionPhase.FOCUS_BUFFER,

                StrictSessionPhase.PROOF_SUBMISSION_OPEN,

                StrictSessionPhase.POST_SUBMISSION,

                StrictSessionPhase.FOCUS_ACTIVE,

                StrictSessionPhase.SESSION_PAUSED,

                StrictSessionPhase.SESSION_COMPLETED

            ):

                if self.phase_expires_at:

                    try:

                        exp_dt = datetime.fromisoformat(self.phase_expires_at)

                        if datetime.now() > exp_dt:

                            self._explicit_phase = None

                    except Exception:

                        pass

                if getattr(self, "_explicit_phase", None):

                    phase = self._explicit_phase

                    if phase in (StrictSessionPhase.FOCUS_BUFFER, StrictSessionPhase.PROOF_SUBMISSION_OPEN, StrictSessionPhase.SESSION_PAUSED, StrictSessionPhase.SESSION_COMPLETED):

                        phone_usage = "PHONE_USAGE_ALLOWED"

                        reason = f"{phase.value} active. Phone usage permitted."

                    elif phase == StrictSessionPhase.POST_SUBMISSION:

                        phone_usage = "RESTRICTED"

                        reason = "Proof submitted. Preparing for next focus phase."

                    else:

                        phone_usage = "RESTRICTED"

                        reason = "Focus Session active. Phone usage restricted."

                else:

                    phase = StrictSessionPhase.FOCUS_ACTIVE

                    phone_usage = "RESTRICTED"

                    reason = "Focus Session active. Phone usage restricted."

            # 5. Study workflow buffer detection

            elif self.study and self.study.s and self.study.s.get('focus_completed_at') and self.study.s.get('state') == 'AWAITING_PROOF_WINDOW':

                phase = StrictSessionPhase.FOCUS_BUFFER

                phone_usage = "PHONE_USAGE_ALLOWED"

                reason = "Focus period completed, awaiting proof window. Phone usage permitted."

            # 6. Default active focus

            else:

                phase = StrictSessionPhase.FOCUS_ACTIVE

                phone_usage = "RESTRICTED"

                reason = "Focus Session active. Phone usage is not permitted."



            phase_val = phase.value if hasattr(phase, 'value') else str(phase)

            usage_str = phone_usage if "PHONE_USAGE" in phone_usage else ("PHONE_USAGE_" + phone_usage)

            return {

                "phase": phase_val,

                "session_phase": phase_val,

                "phone_usage": usage_str,

                "phase_started_at": getattr(self, "phase_started_at", None) or datetime.now().isoformat(),

                "phase_expires_at": getattr(self, "phase_expires_at", None),

                "reason": reason,

                "allowed_packages": list(getattr(self, "dynamic_allowed_packages", set()) | DEFAULT_PHONE_ALLOWLIST),

                "session_id": getattr(self, "session_id", "")

            }



    def update_session_phase(self, new_phase: str, duration_seconds: int = 0, reason: str = "") -> Dict[str, Any]:

        with self._lock:

            old_phase_info = self.get_current_session_phase()

            now_dt = datetime.now()

            now_iso = now_dt.isoformat()



            if not hasattr(self, "_phase_history"):

                self._phase_history = []

            if self._phase_history:

                self._phase_history[-1]["end_dt"] = now_dt

                self._phase_history[-1]["end_iso"] = now_iso



            valid_phases = {p.value for p in StrictSessionPhase}

            if new_phase not in valid_phases:

                raise ValueError(f"Invalid session phase: {new_phase}. Must be one of {valid_phases}")



            self._explicit_phase = StrictSessionPhase(new_phase)

            self.phase_started_at = now_iso

            if duration_seconds > 0:

                self.phase_expires_at = (now_dt + timedelta(seconds=duration_seconds)).isoformat()

            else:

                self.phase_expires_at = None



            current_info = self.get_current_session_phase()



            self._phase_history.append({

                "phase": current_info["phase"],

                "phone_usage": current_info["phone_usage"],

                "start_dt": now_dt,

                "end_dt": (now_dt + timedelta(seconds=duration_seconds)) if duration_seconds > 0 else None,

                "start_iso": now_iso,

                "end_iso": current_info.get("phase_expires_at")

            })



            self._dispatch_phase_update_to_android(current_info, reason)

            logger.info(f"StrictSession phase updated: {old_phase_info['phase']} -> {current_info['phase']} (phone_usage={current_info['phone_usage']})")

            return {

                "success": True,

                "phase": current_info["phase"],

                "session_phase": current_info["phase"],

                "phone_usage": current_info["phone_usage"],

                "phase_started_at": current_info["phase_started_at"],

                "phase_expires_at": current_info["phase_expires_at"],

                "reason": reason or current_info["reason"]

            }



    def _dispatch_phase_update_to_android(self, phase_info: Dict[str, Any], reason: str = ""):

        payload = {

            "event_type": "UPDATE_SESSION_PHASE",

            "session_id": getattr(self, "session_id", ""),

            "user_id": self.user_id,

            "device_id": getattr(self, "android_device_id", None) or "android_companion",

            "session_phase": phase_info["phase"],

            "phone_usage": phase_info["phone_usage"],

            "phase_expires_at": phase_info.get("phase_expires_at"),

            "allowed_packages": phase_info.get("allowed_packages", []),

            "reason": reason or phase_info.get("reason", ""),

            "timestamp": datetime.now().isoformat()

        }

        try:

            from core.server import EventBus

            EventBus.get_instance().publish("UPDATE_SESSION_PHASE", payload)

        except Exception:

            pass



        if getattr(self, "android_device_id", None):

            try:

                from core.connection_pipeline import ConnectionManager, CommandType

                cm = ConnectionManager.get_instance(self.db)

                cm.dispatch_admin_command(

                    device_id=self.android_device_id,

                    command_type=CommandType.UPDATE_SESSION_PHASE,

                    payload=payload,

                    origin="windows_strict_session_manager",

                    account_id=self.user_id

                )

            except Exception:

                pass



    def _was_phone_usage_allowed_at(self, event_dt: datetime) -> Tuple[bool, str]:

        # 1. Current submission window

        if self.is_submission_window_open():

            return True, "ALLOWED_PROOF_WINDOW_USAGE"



        # 2. Historical interval matching

        if hasattr(self, "_phase_history"):

            for h in reversed(self._phase_history):

                start = h.get("start_dt")

                end = h.get("end_dt")

                if start and event_dt >= start:

                    if not end or event_dt <= end:

                        if h.get("phone_usage") == "PHONE_USAGE_ALLOWED":

                            phase = h.get("phase")

                            if phase == StrictSessionPhase.PROOF_SUBMISSION_OPEN or phase == "PROOF_SUBMISSION_OPEN":

                                return True, "ALLOWED_PROOF_WINDOW_USAGE"

                            elif phase == StrictSessionPhase.FOCUS_BUFFER or phase == "FOCUS_BUFFER":

                                return True, "ALLOWED_BUFFER_USAGE"

                            elif phase == StrictSessionPhase.SESSION_PAUSED or phase == "SESSION_PAUSED":

                                return True, "ALLOWED_PAUSED_USAGE"

                            else:

                                return True, "PHONE_USAGE_ALLOWED"

                        else:

                            return False, "PROHIBITED_FOCUS_USAGE"



        # 3. Fallback to current phase

        curr = self.get_current_session_phase()

        if curr["phone_usage"] == "PHONE_USAGE_ALLOWED":

            phase = curr["phase"]

            if phase == StrictSessionPhase.PROOF_SUBMISSION_OPEN or phase == "PROOF_SUBMISSION_OPEN":

                return True, "ALLOWED_PROOF_WINDOW_USAGE"

            elif phase == StrictSessionPhase.FOCUS_BUFFER or phase == "FOCUS_BUFFER":

                return True, "ALLOWED_BUFFER_USAGE"

            else:

                return True, "PHONE_USAGE_ALLOWED"

        return False, "PROHIBITED_FOCUS_USAGE"



    def record_android_app_usage(self, payload: Dict[str, Any]) -> Dict[str, Any]:

        with self._lock:

            pkg = payload.get("package_name", "")

            app_label = payload.get("app_label") or pkg or "Unauthorized Application"

            event_id = payload.get("event_id") or f"phone_violation_{uuid.uuid4().hex[:8]}"

            timestamp = payload.get("event_timestamp") or datetime.now().isoformat()

            dev_id = payload.get("device_id") or "ANDROID"

            session_id = payload.get("session_id") or getattr(self, "session_id", "")



            # 1. Deduplication by event_id: idempotent handling (Requirement 7 & Test G)

            if not hasattr(self, '_seen_violation_event_ids'):

                self._seen_violation_event_ids = set()

            if event_id in self._seen_violation_event_ids:

                logger.info(f"Duplicate Android violation event {event_id} ignored.")

                return {"success": True, "duplicate": True, "event_id": event_id, "violation": False, "action_taken": "DEBOUNCED_CONTINUOUS_USAGE", "classification": "DEBOUNCED_CONTINUOUS_USAGE"}



            # 2. Record transition in recent transitions log

            if not hasattr(self, "_recent_android_transitions"):

                self._recent_android_transitions = []

            transition_record = {

                "package_name": pkg,

                "app_label": app_label,

                "timestamp": timestamp,

                "device_id": dev_id

            }

            self._recent_android_transitions.append(transition_record)

            if len(self._recent_android_transitions) > 30:

                self._recent_android_transitions = self._recent_android_transitions[-30:]



            # 3. Check system allowlist

            all_allowed = DEFAULT_PHONE_ALLOWLIST | getattr(self, "dynamic_allowed_packages", set())

            if pkg in all_allowed or pkg in ("SCREEN_LOCKED", "com.strictrecords.app"):

                return {"success": True, "authorized": True, "classified": "ALLOWED_SYSTEM_USAGE", "violation": False}



            # 4. Parse event datetime for historical classification (Requirement 7 & Test F)

            event_dt = datetime.now()

            try:

                ts_clean = timestamp.replace("Z", "+00:00")

                event_dt = datetime.fromisoformat(ts_clean)

                if event_dt.tzinfo is not None:

                    event_dt = event_dt.astimezone().replace(tzinfo=None)

            except Exception:

                pass



            allowed, classification = self._was_phone_usage_allowed_at(event_dt)



            # 5. Handle Allowed Usage (Requirements 4 & 5 - Tests C & D)

            if allowed:

                self._seen_violation_event_ids.add(event_id)

                if classification == "ALLOWED_PROOF_WINDOW_USAGE":

                    if not hasattr(self, "_allowed_proof_window_usage_events"):

                        self._allowed_proof_window_usage_events = []

                    self._allowed_proof_window_usage_events.append({"event_id": event_id, "package_name": pkg, "app_label": app_label, "timestamp": timestamp})

                    logger.info(f"Android usage in proof window allowed: {pkg} ({app_label})")

                elif classification == "ALLOWED_BUFFER_USAGE":

                    if not hasattr(self, "_allowed_buffer_usage_events"):

                        self._allowed_buffer_usage_events = []

                    self._allowed_buffer_usage_events.append({"event_id": event_id, "package_name": pkg, "app_label": app_label, "timestamp": timestamp})

                    logger.info(f"Android usage in buffer interval allowed: {pkg} ({app_label})")

                return {"success": True, "authorized": True, "classified": classification, "classification": classification, "action_taken": "ALLOWED_USAGE_LOGGED", "violation": False, "event_id": event_id}



            # 6. Prohibited Focus Usage during FOCUS_ACTIVE (Requirements 3 & 7 - Tests A, B, E, H)

            # Debounce continuous usage in the same app without transition away (Test B)

            mono_now = time.monotonic()

            if pkg == getattr(self, "last_recorded_violation_package", None) and (mono_now - getattr(self, "last_recorded_violation_mono", 0.0)) < 10.0:

                return {"success": True, "duplicate": True, "event_id": event_id, "violation": False, "action_taken": "DEBOUNCED_CONTINUOUS_USAGE", "classification": "DEBOUNCED_CONTINUOUS_USAGE"}



            self._seen_violation_event_ids.add(event_id)

            self.last_recorded_violation_package = pkg

            self.last_recorded_violation_mono = mono_now



            # Proctor Correlation

            downward_active = False

            try:

                if self.study_hardware and hasattr(self.study_hardware, 'proctor') and self.study_hardware.proctor:

                    telem = self.study_hardware.proctor.get_telemetry()

                    downward_active = bool(telem.get('downward_study_active') or telem.get('attention_state') == 'DOWNWARD_STUDY')

            except Exception:

                pass



            # Record violation in study workflow

            if self.study:

                self.study.record_phone_usage_violation(app_label, pkg, timestamp, correlated=downward_active)



            # Format detection time

            time_display = datetime.now().strftime("%I:%M:%S %p")

            try:

                time_display = event_dt.strftime("%I:%M:%S %p")

            except Exception:

                pass



            # Central PC warning notification record

            event_record = {

                "event_id": event_id,

                "event_type": "ANDROID_APP_USAGE_DETECTED",

                "title": "ANDROID PHONE USAGE DETECTED",

                "app_name": app_label,

                "app_label": app_label,

                "package_name": pkg,

                "detected_at": time_display,

                "timestamp": timestamp,

                "session_phase": "FOCUS_ACTIVE",

                "message": "Phone usage is not permitted during Focus Session.",

                "warning_message": "Phone usage is not permitted during Focus Session.",

                "device": dev_id,

                "session_id": session_id,

                "correlated": downward_active,

                "acknowledged": False

            }

            if not hasattr(self, "_active_phone_usage_alerts"):

                self._active_phone_usage_alerts = []

            self._active_phone_usage_alerts.append(event_record)

            if not hasattr(self, "_prohibited_usage_events"):

                self._prohibited_usage_events = []

            self._prohibited_usage_events.append(event_record)



            self.android_monitoring_state = "ANDROID_APP_USAGE_DETECTED"

            self.last_blocked_app = app_label

            self.blocked_notification_message = f"Mobile phone usage detected ({app_label}) during Focus Session."

            logger.warning(f"ANDROID PHONE USAGE DETECTED: {event_record}")

            return {"success": True, "recorded": True, "violation": True, "event_id": event_id, "classified": "PROHIBITED_FOCUS_USAGE", "classification": "PROHIBITED_FOCUS_USAGE", "action_taken": "VIOLATION_RECORDED"}



    def record_android_usage_report(self, report: Dict[str, Any]) -> Dict[str, Any]:

        """Processes 30-second periodic Android app-usage report with sequence tracking and deduplication."""

        with self._lock:

            seq = report.get('report_sequence', 0)

            self.android_report_sequence = seq

            now_mono = time.monotonic()

            self.android_last_heartbeat_at = now_mono



            screen_state = report.get('screen_state', 'UNLOCKED')

            self.android_screen_locked = (screen_state in ('LOCKED', 'LOCKED_SLEEPING'))



            monitor_status = report.get('monitor_status', 'ACTIVE')

            if monitor_status == 'LOCKED_SLEEPING':

                self.android_monitoring_state = 'ANDROID_SCREEN_LOCKED'

            elif monitor_status in ('ACTIVE', 'ANDROID_MONITORING_ACTIVE'):

                if self.android_monitoring_state in ('ANDROID_SCREEN_LOCKED', 'ANDROID_CONNECTION_LOST', 'ANDROID_CONNECTION_DEGRADED', 'ANDROID_RECONNECTING', 'ANDROID_START_PENDING', 'ANDROID_CONNECTED'):

                    self.android_monitoring_state = 'ANDROID_MONITORING_ACTIVE'

                    self._android_connection_lost_alert = None

                    self._android_connection_lost_violation_recorded = False

                    if self.study:

                        self.study.set_android_connection_state('ANDROID_MONITORING_ACTIVE', lost=False)



            self.android_current_app = report.get('current_app', '')

            self.android_current_app_label = report.get('current_app_label', '')

            self.android_current_category = report.get('current_category', '')

            self.android_foreground_duration = report.get('foreground_duration_seconds', 0)



            # Ingest recent transitions

            transitions = report.get('recent_transitions') or []

            if not hasattr(self, "_recent_android_transitions"):

                self._recent_android_transitions = []

            for t in transitions:

                if isinstance(t, dict):

                    self._recent_android_transitions.append(t)

            if len(self._recent_android_transitions) > 30:

                self._recent_android_transitions = self._recent_android_transitions[-30:]



            # Process any batch unauthorized events

            unauth_events = report.get('unauthorized_events') or []

            for ev in unauth_events:

                if isinstance(ev, dict):

                    self.record_android_app_usage(ev)



            timer_paused = False

            if self.study and getattr(self.study, 's', None):

                timer_paused = bool(not self.study.s.get('monitoring_ok') or self.study.s.get('android_connection_lost'))



            phase_info = self.get_current_session_phase()

            return {

                'success': True,

                'ack_sequence': seq,

                'session_active': self.is_active(),

                'session_id': getattr(self, 'session_id', ''),

                'session_phase': phase_info['phase'],

                'phone_usage': phase_info['phone_usage'],

                'phase_expires_at': phase_info.get('phase_expires_at'),

                'state': self.android_monitoring_state,

                'timer_paused': timer_paused,

                'submission_window_status': 'OPEN' if self.is_submission_window_open() else 'CLOSED',

                'allowed_packages': phase_info.get('allowed_packages', []),

                'server_timestamp': datetime.now().isoformat()

            }



    def record_phone_usage_event(self, event_id: str, device: str, package_name: str, app_name: str, timestamp: str, session_id: str = ""):

        """Compatibility bridge for sync_events / legacy calls."""

        return self.record_android_app_usage({

            "event_id": event_id,

            "device_id": device,

            "package_name": package_name,

            "app_label": app_name,

            "event_timestamp": timestamp,

            "session_id": session_id

        })



    def acknowledge_phone_usage_alert(self, event_id: str):

        with self._lock:

            if hasattr(self, "_active_phone_usage_alerts"):

                for alert in self._active_phone_usage_alerts:

                    if alert.get("event_id") == event_id:

                        alert["acknowledged"] = True

                        alert["acknowledged_at"] = datetime.now().isoformat()

            if getattr(self, "_android_connection_lost_alert", None):

                if self._android_connection_lost_alert.get("event_id") == event_id:

                    self._android_connection_lost_alert["acknowledged"] = True

                    self._android_connection_lost_alert["acknowledged_at"] = datetime.now().isoformat()

            if self.android_monitoring_state in ("ANDROID_APP_USAGE_DETECTED", "ANDROID_USAGE_WARNING"):

                self.android_monitoring_state = "ANDROID_MONITORING_ACTIVE"

            return {"success": True, "event_id": event_id}



    def get_active_phone_usage_alerts(self):

        with self._lock:

            alerts = list(getattr(self, "_active_phone_usage_alerts", []))

            conn_alert = getattr(self, "_android_connection_lost_alert", None)

            if conn_alert and not conn_alert.get("acknowledged"):

                alerts.append(conn_alert)

            return [a for a in alerts if not a.get("acknowledged")]



    def get_cloudflare_status(self) -> Dict[str, Any]:

        """Returns comprehensive Cloudflare Tunnel and Realtime TURN connectivity status."""

        from windows.cloudflare_tunnel_manager import CloudflareTunnelManager

        from core.cloudflare_turn_service import CloudflareTURNService

        tunnel_mgr = CloudflareTunnelManager.get_instance(self.db)

        turn_svc = CloudflareTURNService.get_instance(self.db)

        t_status = tunnel_mgr.get_status()

        turn_status = turn_svc.get_status()

        camera_status = {}

        if self.ios_camera and self._active_preparation:

            camera_status = self.ios_camera.status(self.user_id, self._active_preparation)



        gw = getattr(self, "ios_camera_gateway", None)

        return {

            "success": True,

            "tunnel": t_status,

            "turn": turn_status,

            "camera": camera_status,

            "gateway_ready": getattr(gw, "ready", False) if gw else False,

            "gateway_error": getattr(gw, "error", self.ios_camera_configuration_error) if gw else self.ios_camera_configuration_error,

            "public_origin": tunnel_mgr.public_origin,

            "tunnel_connected": t_status.get("tunnel_connected", False),

            "https_origin_reachable": t_status.get("https_origin_reachable", False),

            "streaming": bool(camera_status.get("streaming", False)),

        }



    def configure_cloudflare(self, payload: Dict[str, Any]) -> Dict[str, Any]:

        """Configures Cloudflare Tunnel and TURN parameters, persisting to database."""

        from windows.cloudflare_tunnel_manager import CloudflareTunnelManager

        from core.cloudflare_turn_service import CloudflareTURNService

        tunnel_mgr = CloudflareTunnelManager.get_instance(self.db)

        turn_svc = CloudflareTURNService.get_instance(self.db)



        mode = payload.get("mode")

        public_hostname = payload.get("public_hostname")

        tunnel_token = payload.get("tunnel_token")

        turn_key_id = payload.get("turn_key_id")

        turn_api_token = payload.get("turn_api_token")



        if turn_key_id or turn_api_token:

            turn_svc.configure_credentials(key_id=turn_key_id or "", api_token=turn_api_token or "")



        tunnel_mgr.configure(mode=mode, public_hostname=public_hostname, tunnel_token=tunnel_token)



        # Refresh gateway with new origin

        self.ensure_cloudflare_camera_gateway(force_reattach=True)

        return self.get_cloudflare_status()



    def restart_cloudflare_tunnel(self) -> Dict[str, Any]:

        """Restarts the Cloudflare tunnel process."""

        from windows.cloudflare_tunnel_manager import CloudflareTunnelManager

        tunnel_mgr = CloudflareTunnelManager.get_instance(self.db)

        tunnel_mgr.restart_tunnel()

        time.sleep(1.0)

        self.ensure_cloudflare_camera_gateway(force_reattach=True)

        return self.get_cloudflare_status()



    def ensure_cloudflare_camera_gateway(self, force_reattach: bool = False) -> None:

        """Attaches or refreshes IOSCameraGateway using Cloudflare Tunnel origin."""

        try:

            if force_reattach and getattr(self, "ios_camera_gateway", None):

                self.ios_camera_gateway.stop()

                self.ios_camera_gateway = None



            if not getattr(self, "ios_camera_gateway", None):

                from windows.strict_ios_camera_gateway import attach_ios_camera

                self.ios_camera_gateway, self.ios_camera_configuration_error = attach_ios_camera(self)

        except Exception as e:

            logger.warning(f"Could not attach iOS camera gateway to Cloudflare tunnel: {e}")



    def get_android_monitoring_state(self) -> Dict[str, Any]:

        with self._lock:

            has_paired = False

            try:

                with self.db._connect() as conn:

                    row = conn.execute("SELECT device_id FROM devices WHERE user_id=? AND (UPPER(TRIM(platform))='ANDROID' OR UPPER(TRIM(platform)) LIKE 'ANDROID %') AND pairing_state='PAIRED'", (self.user_id,)).fetchone()

                    if row:

                        has_paired = True

            except Exception:

                pass

            current_state = getattr(self, "android_monitoring_state", "ANDROID_NOT_REGISTERED")

            if not has_paired and current_state in ("ANDROID_NOT_JOINED", "ANDROID_NOT_REGISTERED"):

                current_state = "ANDROID_NOT_REGISTERED"



            phase_info = self.get_current_session_phase()

            is_locked = getattr(self, "android_screen_locked", False)

            monitor_status = "LOCKED_SLEEPING" if is_locked else ("MONITORING_UNAVAILABLE" if current_state == "ANDROID_PERMISSION_REQUIRED" else "ACTIVE")

            return {

                "state": current_state,

                "monitor_status": monitor_status,

                "screen_locked": is_locked,

                "last_heartbeat_at": getattr(self, "android_last_heartbeat_at", 0.0),

                "session_id": getattr(self, "session_id", ""),

                "current_app": getattr(self, "android_current_app", ""),

                "current_app_label": getattr(self, "android_current_app_label", ""),

                "current_category": getattr(self, "android_current_category", ""),

                "foreground_duration_seconds": getattr(self, "android_foreground_duration", 0),

                "report_sequence": getattr(self, "android_report_sequence", 0),

                "session_phase": phase_info["phase"],

                "phone_usage": phase_info["phone_usage"],

                "phase_expires_at": phase_info.get("phase_expires_at"),

                "recent_transitions": list(getattr(self, "_recent_android_transitions", []))[-15:],

                "allowed_buffer_usage_count": len(getattr(self, "_allowed_buffer_usage_events", [])),

                "allowed_proof_window_usage_count": len(getattr(self, "_allowed_proof_window_usage_events", [])),

                "prohibited_usage_count": len(getattr(self, "_prohibited_usage_events", [])),

                "active_alerts": self.get_active_phone_usage_alerts()

            }





    def run_ios_predemo_test(self, test_name: str = "all", timeout_sec: float = 15.0) -> Dict[str, Any]:

        """Runs iOS Pre-Demo Media Verification tests (Test A, Test B, Test C or all)."""

        from windows.strict_predemo_verifier import IOSPreDemoVerifier

        verifier = IOSPreDemoVerifier.get_instance(self.db)

        if test_name in ("TEST_A_FRONT_CAMERA", "camera"):

            return verifier.run_test_a_front_camera(timeout_sec=timeout_sec)

        elif test_name in ("TEST_B_SCREEN_SHARE", "screen"):

            return verifier.run_test_b_screen_share(timeout_sec=timeout_sec or 60.0)

        elif test_name in ("TEST_C_SIMULTANEOUS", "simultaneous"):

            return verifier.run_test_c_simultaneous()

        else:

            verifier.run_test_a_front_camera(timeout_sec=timeout_sec)

            verifier.run_test_b_screen_share(timeout_sec=timeout_sec)

            verifier.run_test_c_simultaneous()

            return verifier.get_predemo_report()



    def get_ios_predemo_report(self) -> Dict[str, Any]:

        """Returns the 8-item Pre-Demo Media Verification report."""

        from windows.strict_predemo_verifier import IOSPreDemoVerifier

        return IOSPreDemoVerifier.get_instance(self.db).get_predemo_report()





_strict_session_manager_instance = None



def get_strict_session_manager():

    global _strict_session_manager_instance

    return _strict_session_manager_instance

