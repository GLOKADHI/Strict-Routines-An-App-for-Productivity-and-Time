"""Browser-specific setup and monitors owned by the one desktop process."""
import json
import os
from pathlib import Path
import re
import threading
import time

from windows.browser_setup import BROWSERS, SUPPORTED, detect_browsers
from windows.browser_monitor_package import prepare


class BrowserMonitorRegistry:
    def __init__(self, storage=None, db=None, detector=None, launcher=None):
        self.storage = Path(storage or Path(os.environ.get('LOCALAPPDATA', Path.home()))/'StrictRecords/extension_monitor')
        self.storage.mkdir(parents=True,exist_ok=True)
        self._db = db
        self.detector = detector or detect_browsers
        self.launcher = launcher
        self._lock = threading.RLock()
        self.monitors = {}
        self.enrollments = {}
        self._discovered = 0.0
        self._process_snapshot = (0.0, None)
        self._last_check = 0.0
        self._discover(force=True)

    def _running(self, name):
        now = time.monotonic()
        timestamp, names = self._process_snapshot
        if now - timestamp > 3:
            try:
                import psutil
                names = {(p.info.get('name') or '').lower() for p in psutil.process_iter(['name'])}
            except Exception:
                names = None
            self._process_snapshot = (now,names)
        return name in names if names is not None else None

    def _load(self, folder):
        from windows.browser_extension_monitor import BrowserExtensionMonitor
        try:
            enrollment = json.loads((folder/'enrollment.json').read_text(encoding='utf-8'))
            identity = enrollment['installation_id']
            key = enrollment.get('browser_key','chrome')
            if not re.fullmatch('[a-f0-9]{20}',identity) or key not in SUPPORTED:
                return
            if identity in self.monitors:
                return
            executable = BROWSERS[key][1]
            monitor = BrowserExtensionMonitor(storage_dir=folder,db=self._db,
                process_checker=lambda _, process=executable:self._running(process))
            monitor.browser_key = key
            self.monitors[identity],self.enrollments[identity] = monitor,enrollment
        except (OSError,ValueError,KeyError,TypeError):
            return

    def _discover(self, force=False):
        now = time.monotonic()
        if not force and now - self._discovered < 10:
            return
        self._discovered = now
        self._load(self.storage)  # Preserve the previous single-Chrome enrollment in place.
        root = self.storage/'installations'
        if root.is_dir():
            for folder in root.iterdir():
                if folder.is_dir() and re.fullmatch('[a-f0-9]{20}',folder.name):
                    self._load(folder)

    def _launch(self,args):
        if self.launcher:
            self.launcher(args)
        else:
            import subprocess
            subprocess.Popen(args,shell=False,close_fds=True)

    def _browser(self, identity):
        browser = next((b for b in self.detector() if b['id'] == identity),None)
        if not browser or not browser.get('supported'):
            raise ValueError('Choose a supported browser installed on this computer.')
        return browser

    def prepare_browser(self, browser_id, profile_label):
        with self._lock:
            browser = self._browser(browser_id)
            profile_label = str(profile_label or '').strip()
            if not profile_label or len(profile_label) > 120:
                raise ValueError('Enter the profile name or Profile Path you will use in this browser.')
            self._discover(force=True)
            existing = next(((i,e) for i,e in self.enrollments.items()
                             if e.get('browser_key','chrome') == browser['key'] and
                             e.get('browser_id',browser_id) == browser_id and e.get('profile_label') == profile_label),None)
            if existing:
                identity,enrollment = existing
                monitor = self.monitors[identity]
                folder = Path(monitor.storage_dir)
                package = folder/'package'
                if package.is_dir():
                    # Update code atomically while preserving generated enrollment config and profile storage.
                    from windows.browser_monitor_package import refresh_package
                    refresh_package(package,enrollment)
                else:
                    prepare(package,folder,profile_label,update_existing=True,browser_key=browser['key'],browser_id=browser_id)
            else:
                import uuid
                folder = self.storage/'installations'/uuid.uuid4().hex[:20]
                package = folder/'package'
                prepare(package,folder,profile_label,browser_key=browser['key'],browser_id=browser_id)
                self._load(folder)
                identity = json.loads((folder/'enrollment.json').read_text())['installation_id']
            return dict(success=True,installation_id=identity,browser_name=browser['name'],
                        target_folder=str(package/'StrictRoutines'),helper_folder=str(package/'StatusHelper'),
                        extensions_url=browser['extensions_url'],
                        message='Folders prepared. Load StrictRoutines and StatusHelper in this same browser profile, then verify the live status.')

    def open_manager(self,browser_id):
        browser = self._browser(browser_id)
        self._launch([browser['executable'],browser['extensions_url']])
        return {'success':True}

    def open_folder(self,installation_id,component):
        if component not in ('target','helper'):
            raise ValueError('Choose the extension or helper folder.')
        with self._lock:
            self._discover()
            if installation_id not in self.monitors:
                raise ValueError('Prepare this browser profile first.')
            package = Path(self.monitors[installation_id].storage_dir)/'package'
            folder = package/('StrictRoutines' if component == 'target' else 'StatusHelper')
            if not folder.is_dir():
                raise ValueError('Refresh this browser setup to prepare its folders.')
            assert folder.resolve().is_relative_to(self.storage.resolve())
            explorer = str(Path(os.environ.get('WINDIR',r'C:\Windows'))/'explorer.exe')
            self._launch([explorer,str(folder)])
            return {'success':True}

    def submit_observation(self,packet,origin,token):
        with self._lock:
            self._discover()
            monitor = self.monitors.get(packet.get('installation_id'))
            if not monitor:
                return {'success':False,'state':'STATE_UNKNOWN','error':'This browser profile has not been prepared in Strict Session.'}
            return monitor.submit_observation(packet,origin,token)

    def check_state(self,now=None):
        with self._lock:
            tick = time.monotonic()
            if tick - self._last_check < 3 and now is None:
                return
            self._last_check = tick
            self._discover()
            for monitor in self.monitors.values():
                monitor._db = self._db
                monitor.check_state(now=now)

    def get_ui_status(self):
        with self._lock:
            self._discover()
            installed = self.detector()
            rows = []
            for key, (name,_,manager) in BROWSERS.items():
                candidates = [b for b in installed if b['key'] == key]
                if not candidates:
                    candidates = [dict(id='',key=key,name=name,supported=key in SUPPORTED,extensions_url=manager)]
                for browser in candidates:
                    enrolled = []
                    for identity,enrollment in self.enrollments.items():
                        if enrollment.get('browser_key','chrome') != key:
                            continue
                        if enrollment.get('browser_id') and enrollment['browser_id'] != browser['id']:
                            continue
                        monitor = self.monitors[identity]
                        package = Path(monitor.storage_dir)/'package'
                        enrolled.append(dict(monitor.get_ui_status(),installation_id=identity,
                            browser_key=key,profile_label=enrollment.get('profile_label',''),profile_bound=bool(monitor.binding),
                            target_folder=str(package/'StrictRoutines') if package.is_dir() else '',
                            helper_folder=str(package/'StatusHelper') if package.is_dir() else ''))
                    rows.append(dict(browser,installed=bool(browser['id']),profiles=enrolled,
                        capability='Chromium extension package; browser confirmation required.' if key in SUPPORTED
                        else 'Firefox needs a separately signed Firefox package; this Chromium package cannot be installed.'))
            known = set(BROWSERS)
            rows.extend(dict(b,installed=True,profiles=[],capability=b.get('limitation','Unsupported browser'))
                        for b in installed if b['key'] not in known)
            return dict(success=True,browsers=rows,installations=[p for r in rows for p in r['profiles']],
                        state='PER_BROWSER',pending_penalty=sum(m.pending_penalty for m in self.monitors.values()),
                        total_penalty=sum(m.total_penalty for m in self.monitors.values()),
                        companion_required=True,message='Each browser profile connects separately. Unknown time produces no new penalty coverage.')

    def set_grace_duration_minutes(self,minutes,installation_id=None):
        with self._lock:
            self._discover()
            if installation_id:
                selected = self.monitors.get(installation_id)
            else:
                selected = next(iter(self.monitors.values())) if len(self.monitors) == 1 else None
            if not selected:
                raise ValueError('Choose the prepared browser profile to update.')
            return selected.set_grace_duration_minutes(minutes)

    def owns_verified_proposal(self, proposal):
        with self._lock:
            self._discover()
            evidence = proposal.get('evidence',{})
            monitor = self.monitors.get(evidence.get('installation',{}).get('installation_id'))
            if not monitor:
                return False
            with monitor._lock:
                identity_keys = ('installation_id','helper_profile','target_profile','target_id','helper_id','browser_key','profile_label')
                binding = evidence.get('installation',{})
                if (proposal.get('proposal_id') not in monitor.transactions or
                        any(binding.get(key) != monitor.binding.get(key) for key in identity_keys)):
                    return False
                if evidence.get('episode_id') == monitor.episode_id:
                    saved = monitor._state()
                else:
                    episode = evidence.get('episode_id','')
                    if not re.fullmatch('ext_ep_[a-f0-9]{32}',episode): return False
                    try:
                        saved = json.loads((Path(monitor.storage_dir)/'episodes'/(episode+'.json')).read_text())
                    except (OSError,ValueError): return False
                if (saved.get('disabled_start_time') != evidence.get('disabled_detected_at') or
                        saved.get('grace_deadline') != evidence.get('grace_deadline')):
                    return False
                offset, segments, enabled = 0., [], True
                start = (evidence['minute_index']-1)*60
                for a,b,policy in saved.get('coverage_intervals',[]):
                    a = max(a,saved['grace_deadline'])
                    if b <= a: continue
                    length = b-a
                    if offset < start+60 and offset+length > start:
                        segments.append([a+max(0,start-offset),a+min(length,start+60-offset)])
                        enabled = enabled and policy
                    offset += length
                return enabled and segments == evidence.get('verified_segments')

    def get_grace_duration_minutes(self,installation_id=None):
        selected = self.monitors.get(installation_id) if installation_id else next(iter(self.monitors.values()),None)
        return selected.get_grace_duration_minutes() if selected else 5

    def authorize_recovery(self):
        with self._lock:
            for monitor in self.monitors.values():
                monitor.authorize_recovery()
