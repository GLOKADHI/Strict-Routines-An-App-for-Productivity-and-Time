import re

path = 'windows/strict_session_manager.py'
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Update run_precheck
old_precheck = """    def run_precheck(self) -> Dict[str, Any]:
        \"\"\"
        Executes pre-session registration and policy verification.
        \"\"\"
        hardware = self.study_hardware.view()
        results = {
            "camera": {"status": "PASS" if hardware['monitoring_ok'] else "CONSENT_OR_DEVICE_REQUIRED", "message": "Local camera frames only; no image retention or upload"},
            "microphone": {"status": "NOT_USED", "message": "Microphone recording is not used by the study monitor"},
            "bluetooth": {"status": "PASS" if hardware['bluetooth_connected'] else "CONNECTED_AUDIO_REQUIRED"},
            "device": {"status": "PASS", "device_id": self.device_id, "message": "Windows Client ready"},
            "policy": {"status": "PASS", "version": self.policy.policy_version, "message": "Strict Policy validated"},
            "all_passed": bool(hardware['monitoring_ok'] and hardware['bluetooth_connected']),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.precheck_details = results
        return results"""

new_precheck = """    def run_precheck(self) -> Dict[str, Any]:
        \"\"\"
        Executes pre-session registration and policy verification against 11 prerequisite checks.
        \"\"\"
        hardware = self.study_hardware.view()
        results = {
            "webcam": {
                "status": "PASS" if hardware.get('camera') == 'ACTIVE' else "DEVICE_REQUIRED",
                "message": "Local camera active; continuous focus detection ready"
            },
            "strictguard": {
                "status": "PASS" if hardware.get('strictguard_proctor') == 'ACTIVE' else "INITIALIZING",
                "message": "StrictGuard AI proctor loaded; local processing only"
            },
            "calibration": {
                "status": "PASS" if hardware.get('calibration_ready', True) else "CALIBRATION_PENDING",
                "message": "Neutral position calibrated / ready for calibration upon start"
            },
            "bluetooth_monitoring": {
                "status": "PASS" if hardware.get('bluetooth_monitoring') == 'ACTIVE' else "MONITOR_INACTIVE",
                "message": "Continuous detection of prohibited wireless audio active"
            },
            "prohibited_bluetooth": {
                "status": "PASS" if not hardware.get('prohibited_bluetooth_connected') else "PROHIBITED_DEVICE_CONNECTED",
                "message": "No prohibited Bluetooth audio devices connected" if not hardware.get('prohibited_bluetooth_connected') else f"Disconnect {hardware.get('prohibited_bluetooth_name', 'Bluetooth audio')}"
            },
            "wired_headphones": {
                "status": "PASS" if not hardware.get('wired_headphones_connected') else "PROHIBITED_DEVICE_CONNECTED",
                "message": "No 3.5mm wired headphones/headsets connected" if not hardware.get('wired_headphones_connected') else f"Unplug wired headphones ({hardware.get('wired_headphone_name', '3.5mm Jack')})"
            },
            "physical_jack": {
                "status": hardware.get('physical_jack_status', 'UNSUPPORTED'),
                "message": f"Hardware audio jack presence reporting: {hardware.get('physical_jack_status', 'UNSUPPORTED')}"
            },
            "speaker_output": {
                "status": "MUTED" if hardware.get('speaker_status') == 'MUTED' else "PASS",
                "message": "PC speakers will be muted during active session"
            },
            "microphone": {
                "status": "NOT_REQUIRED",
                "message": "Microphone recording is not required or accessed across any device"
            },
            "device": {"status": "PASS", "device_id": self.device_id, "message": "Windows Client ready"},
            "policy": {"status": "PASS", "version": self.policy.policy_version, "message": "Strict Policy validated"},
            "all_passed": bool(
                hardware.get('camera') == 'ACTIVE'
                and not hardware.get('prohibited_audio_detected')
            ),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.precheck_details = results
        return results"""

# Normalize
norm_t = text.replace('\r\n', '\n')
norm_old = old_precheck.replace('\r\n', '\n')
norm_new = new_precheck.replace('\r\n', '\n')

if norm_old in norm_t:
    norm_t = norm_t.replace(norm_old, norm_new, 1)
    print("Replaced run_precheck successfully")
else:
    print("Could not find old_precheck")

# 2. Update start_session polling loop
old_poll = """            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if self.study_hardware.view()['monitoring_ok'] and self.study_hardware.view()['bluetooth_connected']:
                    break
                if self.study_hardware.view()['camera'] == 'UNAVAILABLE':
                    break
                time.sleep(.1)"""

new_poll = """            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                hw = self.study_hardware.view()
                if hw.get('monitoring_ok') and not hw.get('prohibited_audio_detected'):
                    break
                if hw.get('camera') == 'UNAVAILABLE':
                    break
                time.sleep(.1)"""

norm_poll_old = old_poll.replace('\r\n', '\n')
norm_poll_new = new_poll.replace('\r\n', '\n')
if norm_poll_old in norm_t:
    norm_t = norm_t.replace(norm_poll_old, norm_poll_new, 1)
    print("Replaced start_session poll loop successfully")
else:
    print("Could not find old_poll")

# 3. Update _tick_study and complete_study
old_tick = """    def _tick_study(self):
        if not self.study.s:
            return
        hw = self.study_hardware.view()
        self.camera_health = hw['monitoring_ok']
        self.study.tick(monitoring_ok=hw['monitoring_ok'])
        self._update_study_hud(hw)
        if self.study.s.get('cloud_registered') and time.monotonic() >= self._hold_retry_at:
            threading.Thread(target=self._sync_study_authority, daemon=True, name='StrictStudyHold').start()
        revision = hw['bluetooth_revision']
        if revision != self._bluetooth_revision:
            self._bluetooth_revision = revision
            if self.study.bluetooth_sample(hw['bluetooth_connected']):
                self._ensure_bluetooth_penalty()
                self._finish_study_controls('Second confirmed Bluetooth disconnection')
                threading.Thread(target=self._sync_study_authority, daemon=True).start()

    def _ensure_bluetooth_penalty(self):
        s = self.study.s
        if s and s['state'] == 'FAILED' and s['bluetooth_violations'] >= 2 and not s.get('penalty_proposed'):
            timestamp = s.get('terminated_at',s['started_at'])
            self.db.create_deduction_proposal(
                    proposal_id='strict_bt_' + s['session_id'], date=timestamp[:10],
                    timestamp=timestamp, originating_device=self.device_id,
                    event_type='STRICT_SESSION_BLUETOOTH_SECOND_DETECTION', proposed_points=-2,
                    reason='Second confirmed Bluetooth disconnection in Strict Session',
                    related_task_id=s['session_id'], applicable_rule='STRICT_SESSION_BLUETOOTH_2',
                    evidence={'session_id':s['session_id'], 'confirmed_disconnects':2})
            s['penalty_proposed'] = True
            self.study._save()"""

new_tick = """    def _tick_study(self):
        if not self.study.s:
            return
        hw = self.study_hardware.view()
        self.camera_health = hw.get('monitoring_ok', False)
        self.study.tick(monitoring_ok=hw.get('monitoring_ok', False))
        self._update_study_hud(hw)
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

    def _ensure_headphone_penalty(self, dev_name='Prohibited Audio Device'):
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
            self.study._save()"""

norm_tick_old = old_tick.replace('\r\n', '\n')
norm_tick_new = new_tick.replace('\r\n', '\n')
if norm_tick_old in norm_t:
    norm_t = norm_t.replace(norm_tick_old, norm_tick_new, 1)
    print("Replaced _tick_study successfully")
else:
    print("Could not find old_tick")

# 4. Update complete_study
old_complete = """            hw = self.study_hardware.view()
            if not hw['monitoring_ok'] or hw['bluetooth_connected'] is not True:
                return {'success':False, 'error':'Restore camera monitoring and connected Bluetooth audio before completing.'}"""

new_complete = """            hw = self.study_hardware.view()
            if not hw.get('monitoring_ok') or hw.get('prohibited_audio_detected'):
                return {'success':False, 'error':'Ensure camera monitoring is active and disconnect any headphones before completing.'}"""

norm_complete_old = old_complete.replace('\r\n', '\n')
norm_complete_new = new_complete.replace('\r\n', '\n')
if norm_complete_old in norm_t:
    norm_t = norm_t.replace(norm_complete_old, norm_complete_new, 1)
    print("Replaced complete_study successfully")
else:
    print("Could not find old_complete")

with open(path, 'w', encoding='utf-8') as f:
    f.write(norm_t)
print("Finished patching windows/strict_session_manager.py")
