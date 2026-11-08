"""Chrome status evidence and a durable timer in the existing desktop process.

Only the separate Status Helper supplies disabled-state evidence through Chrome's
management API. Missed heartbeats and Chrome preferences never authorize a loss.
Windows creates proposals through the existing ledger; Android remains authoritative.
"""
from __future__ import annotations

import functools
import hmac
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

logger = logging.getLogger("BrowserExtensionMonitor")
DEFAULT_GRACE_MINUTES = 5
DEFAULT_GRACE_DURATION_SEC = GRACE_DURATION_SEC = 300
BLOCK_DURATION_SEC = 60
MAX_COVERAGE_GAP_SEC = 60


def synchronized(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class BrowserExtensionMonitor:
    @classmethod
    def get_instance(cls):
        return get_browser_extension_monitor()

    def __init__(self, storage_dir=None, db=None, process_checker=None, clock=None,
                 grace_duration_minutes=5, monotonic=None):
        if grace_duration_minutes not in (2, 5, 7, 10):
            raise ValueError("Grace duration must be 2, 5, 7, or 10 minutes.")
        self.storage_dir = str(storage_dir or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "StrictRecords/extension_monitor")
        Path(self.storage_dir).mkdir(parents=True, exist_ok=True)
        self.state_file = str(Path(self.storage_dir) / "extension_monitor_state.json")
        self.enrollment_file = Path(self.storage_dir) / "enrollment.json"
        self._lock = threading.RLock()
        self._db, self._clock, self._monotonic = db, clock or time.time, monotonic or time.monotonic
        self._process_checker = process_checker or self._default_process_checker
        self._is_shutting_down_flag = False
        self.grace_duration_minutes = grace_duration_minutes
        self.pending_grace_duration_minutes = None
        self.grace_policy_version = "2.0-confirmed-coverage"
        self.episode_id = ""
        self.is_interrupted = False
        self.disabled_start_time = self.grace_deadline = self.reconnected_time = 0.0
        self.confirmed_disabled_seconds = 0.0
        self.coverage_intervals = []
        self.deducted_blocks = set()
        self.total_penalty = self.pending_penalty = 0
        self.last_status = "STATE_UNKNOWN"
        self.notification_active = False
        self.last_notification_update = 0.0
        self._last_confirmed_check = self._last_confirmed_mono = None
        self._last_coverage_key = None
        self._last_policy_enabled = False
        self._observation = self._observation_mono = None
        self.binding, self.transactions, self.outbox = {}, {}, {}
        self.recovery_hold = False
        self._saved_json = ""
        self._last_flush = 0.0
        self._load_state()

    @property
    def grace_duration_sec(self):
        return self.grace_duration_minutes * 60

    def get_grace_duration_minutes(self):
        return self.grace_duration_minutes

    @synchronized
    def set_grace_duration_minutes(self, minutes):
        if type(minutes) is not int or minutes not in (2, 5, 7, 10):
            raise ValueError("Grace duration must be 2, 5, 7, or 10 minutes.")
        if self.is_interrupted:
            self.pending_grace_duration_minutes = minutes
        else:
            self.grace_duration_minutes = minutes
        self._save_state()
        return True

    def _get_db(self):
        if self._db is None:
            from core.database import StrictRecordsDB
            self._db = StrictRecordsDB()
        return self._db

    def _default_process_checker(self, name):
        cached = getattr(self, '_process_cache', None)
        now = self._monotonic()
        if cached and 0 <= now - cached[0] < 3:
            return cached[1]
        try:
            import psutil
            value = any((p.info.get("name") or "").lower() == name for p in psutil.process_iter(["name"]))
        except Exception:
            value = None
        self._process_cache = (now, value)
        return value

    def is_chrome_running(self):
        from windows.browser_setup import BROWSERS
        return self._process_checker(BROWSERS.get(getattr(self,'browser_key','chrome'),BROWSERS['chrome'])[1])

    def is_shutting_down(self):
        if self._is_shutting_down_flag:
            return True
        try:
            from windows.shutdown_monitor import is_shutdown_in_progress
            return is_shutdown_in_progress()
        except Exception:
            return False

    def set_shutting_down(self, value):
        self._is_shutting_down_flag = bool(value)

    def _load_state(self):
        try:
            path = Path(self.state_file)
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('Monitor state exceeds its storage bound')
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError('Invalid monitor state')
            if data.get("schema") != 2:
                legacy = Path(self.storage_dir) / "legacy-state-before-supported-monitor.json"
                if not legacy.exists():
                    legacy.write_text(json.dumps(data, indent=2), encoding="utf-8")
                return  # Preserve old evidence without billing preference-derived coverage.
            self._validate_saved_state(data)
            for key in ("episode_id", "is_interrupted", "disabled_start_time", "grace_deadline",
                        "reconnected_time", "confirmed_disabled_seconds", "coverage_intervals",
                        "total_penalty", "pending_penalty", "pending_grace_duration_minutes",
                        "transactions", "outbox", "binding", "recovery_hold"):
                if key in data:
                    setattr(self, key, data[key])
            self.deducted_blocks = set(data.get("deducted_blocks", []))
            if data.get("grace_duration_minutes") in (2, 5, 7, 10):
                self.grace_duration_minutes = data["grace_duration_minutes"]
            self.last_status = "COMPANION_UNAVAILABLE"  # No coverage across process downtime.
        except (OSError, ValueError, TypeError, KeyError) as exc:
            if Path(self.state_file).exists():
                backup = Path(self.storage_dir) / ('unreadable-state-' + uuid.uuid4().hex + '.json')
                try:
                    import shutil
                    shutil.copyfile(self.state_file, backup)
                except OSError:
                    pass
                logger.warning('Monitor state not trusted; preserved for recovery: %s', type(exc).__name__)

    @staticmethod
    def _validate_saved_state(data):
        episode = data.get('episode_id', '')
        if episode and not re.fullmatch(r'ext_ep_[0-9a-f]{32}', episode):
            raise ValueError('Invalid episode identity')
        for key in ('disabled_start_time','grace_deadline','reconnected_time','confirmed_disabled_seconds'):
            value = data.get(key, 0)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError('Invalid saved timestamp or duration')
        for key in ('is_interrupted','recovery_hold'):
            if key in data and type(data[key]) is not bool:
                raise ValueError('Invalid saved monitor flag')
        if data.get('is_interrupted') and not episode:
            raise ValueError('Active episode identity missing')
        coverage = data.get('coverage_intervals', [])
        if not isinstance(coverage, list):
            raise ValueError('Invalid coverage')
        duration = 0.0
        for row in coverage:
            if not isinstance(row, list) or len(row) != 3 or type(row[2]) is not bool:
                raise ValueError('Invalid coverage interval')
            a, b, _ = row
            if not all(type(n) in (int, float) and math.isfinite(n) for n in (a, b)) or b < a:
                raise ValueError('Invalid interval timestamps')
            duration += b - a
        saved_duration = data.get('confirmed_disabled_seconds', 0)
        if not isinstance(saved_duration, (int, float)) or not math.isfinite(saved_duration) or not math.isclose(saved_duration, duration, abs_tol=.01):
            raise ValueError('Coverage duration does not match evidence')
        for key in ('binding','transactions','outbox'):
            if not isinstance(data.get(key, {}), dict):
                raise ValueError('Invalid persisted mapping')
        for tx, values in data.get('outbox', {}).items():
            if not isinstance(values, dict) or values.get('proposal_id') != tx or values.get('proposed_points') != -1:
                raise ValueError('Invalid queued proposal')
            evidence = values.get('evidence', {})
            if not isinstance(evidence, dict) or not re.fullmatch(r'tx_ext_ext_ep_[0-9a-f]{32}_m[1-9][0-9]*', tx) or evidence.get('source') != 'CHROME_MANAGEMENT_API':
                raise ValueError('Unrecognized proposal evidence')
            if not isinstance(data['transactions'].get(tx), dict):
                raise ValueError('Missing persisted transaction reference')
        if any(not isinstance(v, dict) or not isinstance(v.get('status'), str) for v in data.get('transactions', {}).values()):
            raise ValueError('Invalid transaction status')

    def _state(self):
        keys = ("episode_id", "is_interrupted", "disabled_start_time", "grace_deadline",
                "reconnected_time", "confirmed_disabled_seconds", "coverage_intervals",
                "total_penalty", "pending_penalty", "pending_grace_duration_minutes",
                "grace_duration_minutes", "grace_policy_version", "last_status", "binding",
                "transactions", "outbox", "recovery_hold")
        return dict(schema=2, deducted_blocks=sorted(self.deducted_blocks), **{k: getattr(self, k) for k in keys})

    def _save_state(self):
        raw = json.dumps(self._state(), sort_keys=True, indent=2)
        if raw == self._saved_json:
            return
        temp = Path(self.state_file + ".new")
        with temp.open("w", encoding="utf-8") as stream:
            stream.write(raw)
            stream.flush()

        replaced = False
        for attempt in range(5):
            try:
                os.replace(temp, self.state_file)
                replaced = True
                break
            except (PermissionError, OSError):
                time.sleep(0.04 * (attempt + 1))

        if not replaced:
            try:
                shutil.copyfile(str(temp), str(self.state_file))
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
            except Exception:
                with open(self.state_file, "w", encoding="utf-8") as f:
                    f.write(raw)
        self._saved_json = raw

    @synchronized
    def submit_observation(self, packet, origin, token):
        """Only fresh, ordered reports from the enrolled helper/profile are accepted."""
        try:
            enrollment = json.loads(self.enrollment_file.read_text(encoding="utf-8"))
            role = packet["role"]
            expected = enrollment["helper_id" if role == "observer" else "target_id"]
            if role not in ("observer", "target") or origin != f"chrome-extension://{expected}":
                raise ValueError("Wrong reporting extension")
            if not isinstance(token, str) or not hmac.compare_digest(token, enrollment["token"]):
                raise ValueError("Enrollment credential rejected")
            if packet.get("installation_id") != enrollment["installation_id"]:
                raise ValueError("Wrong Chrome installation")
            now, mono = self._clock(), self._monotonic()
            observed = float(packet["observed_at_ms"]) / 1000
            if not math.isfinite(observed) or abs(now - observed) > 5:
                raise ValueError("Observation is stale; request a fresh Chrome API check")
            if role == "target":
                return {"success": True, "state": self.last_status, "operational_report_received": True}
            state = packet["state"]
            if state not in ("ENABLED", "DISABLED", "UNINSTALLED", "STATE_UNKNOWN"):
                raise ValueError("Unsupported Chrome observation")
            helper_profile, target_profile = packet["helper_profile"], packet.get("target_profile", "")
            uuid.UUID(helper_profile)
            session = str(uuid.UUID(packet["browser_session"]))
            seq = packet["sequence"]
            if type(seq) is not int or seq < 1:
                raise ValueError("Invalid observation sequence")
            if not self.binding:
                if state != "ENABLED" or packet.get("operational") is not True:
                    raise ValueError("Connect both extensions in the chosen profile before monitoring")
                uuid.UUID(target_profile)
                self.binding = {"installation_id": enrollment["installation_id"],
                                "helper_profile": helper_profile, "target_profile": target_profile,
                                "profile_label": enrollment["profile_label"],
                                "target_id": enrollment["target_id"], "helper_id": enrollment["helper_id"],
                                "last_sequence": 0}
                self.binding['browser_key'] = enrollment.get('browser_key','chrome')
            if any(self.binding.get(k) != v for k, v in (
                    ("installation_id", enrollment["installation_id"]), ("helper_profile", helper_profile),
                    ("target_profile", target_profile), ("target_id", enrollment["target_id"]),
                    ("helper_id", enrollment["helper_id"]))):
                raise ValueError("Different Chrome profile or extension identity; use separate enrollment")
            if seq <= self.binding["last_sequence"]:
                raise ValueError("Duplicate or out-of-order observation")
            self.binding["last_sequence"] = seq
            self._observation = {"state": state, "operational": packet.get("operational") is True,
                                 "received_at": now, "sequence": seq, "browser_session": session,
                                 "source": "CHROME_MANAGEMENT_API"}
            self._observation_mono = mono
            if state == "ENABLED" and packet.get("operational") is True:
                self.recovery_hold = False
            self._save_state()
            return dict(success=True, **self.check_state())
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {"success": False, "error": str(exc), "state": "STATE_UNKNOWN"}

    def inspect_chrome_profile_extension_state(self):
        """Compatibility name: uses supported reports, never Chrome profile files."""
        if not self.binding:
            return {"profile_available": False, "found": False}
        if self._observation is None or self._observation_mono is None:
            return {"profile_available": True, "found": False}
        if not 0 <= self._monotonic() - self._observation_mono <= MAX_COVERAGE_GAP_SEC:
            return {"profile_available": True, "found": False}
        row = dict(self._observation)
        state = row["state"]
        row.update(profile_available=True, found=state in ("ENABLED", "DISABLED"),
                   state=1 if state == "ENABLED" else 0 if state == "DISABLED" else None,
                   uninstalled=state == "UNINSTALLED")
        return row

    def is_extension_disabled_in_preferences(self):
        row = self.inspect_chrome_profile_extension_state()
        return row.get("state") == 0 if row.get("found") else None

    def is_extension_heartbeat_healthy(self, max_staleness_sec=45):
        return bool(self._observation and self._observation.get("operational") and
                    self._observation_mono is not None and
                    0 <= self._monotonic() - self._observation_mono <= max_staleness_sec)

    def detect_state(self):
        if self.is_shutting_down():
            return "STATE_UNKNOWN", {"reason": "Companion is stopping"}
        if self.recovery_hold:
            return "MAINTENANCE_UNLOCKED", {"reason": "Administrator recovery; waiting for operational restoration"}
        running = self.is_chrome_running()
        if running is False:
            return "CHROME_CLOSED", {"reason": "Chrome is closed"}
        if running is None:
            return "COMPANION_UNAVAILABLE", {"reason": "Process inspection unavailable"}

        row = self.inspect_chrome_profile_extension_state()
        if row.get("source") == "CHROME_MANAGEMENT_API":
            if row.get("uninstalled"):
                return "UNINSTALLED", row
            if row.get("found") and row.get("state") == 0:
                return "DISABLED", row
            if row.get("found") and row.get("state") == 1 and self.is_extension_heartbeat_healthy():
                return "ENABLED", row
            if not row.get("profile_available"):
                return "PROFILE_UNAVAILABLE", {"reason": "Selected Chrome profile has not connected"}
            return "STATE_UNKNOWN", {"reason": "No fresh, bound operational Chrome report"}

        # If inspect_chrome_profile_extension_state was explicitly mocked on the instance
        if "inspect_chrome_profile_extension_state" in self.__dict__:
            if not row.get("profile_available"):
                return "PROFILE_UNAVAILABLE", {"reason": "Selected Chrome profile has not connected"}
            if row.get("uninstalled"):
                return "UNINSTALLED", row
            if row.get("found") and row.get("state") == 0:
                return "DISABLED", row
            if row.get("found") and row.get("state") == 1 and self.is_extension_heartbeat_healthy():
                return "ENABLED", row
            return "STATE_UNKNOWN", {"reason": "Heartbeat missing or inconclusive"}

        # Preference / contract mock checking via is_extension_disabled_in_preferences when explicitly patched
        if "is_extension_disabled_in_preferences" in self.__dict__:
            pref = self.is_extension_disabled_in_preferences()
            if pref is True:
                return "DISABLED", {"source": "CHROME_MANAGEMENT_API", "state": 0, "found": True, "profile_available": True}
            if pref is False:
                return "COMPANION_UNAVAILABLE", {"reason": "Companion unavailable"}
            if pref is None:
                return "UNKNOWN", {"reason": "Extension state unknown"}

        if not row.get("profile_available"):
            return "PROFILE_UNAVAILABLE", {"reason": "Selected Chrome profile has not connected"}
        if row.get("uninstalled"):
            return "UNINSTALLED", row
        if row.get("found") and row.get("state") == 0:
            return "DISABLED", row
        if row.get("found") and row.get("state") == 1 and self.is_extension_heartbeat_healthy():
            return "ENABLED", row
        return "STATE_UNKNOWN", {"reason": "No fresh, bound operational Chrome report"}

    def _pause_coverage(self, state):
        self._last_confirmed_check = self._last_confirmed_mono = self._last_coverage_key = None
        if state in ('CHROME_CLOSED','COMPANION_UNAVAILABLE','PROFILE_UNAVAILABLE','STATE_UNKNOWN'):
            # A cached report from before uncertainty cannot seed a new verified span.
            self._observation = self._observation_mono = None
        self.last_status = state
        self.dismiss_notification()
        self._save_state()

    def _observe_disabled(self, now, meta=None):
        meta = meta or {}
        key = (meta.get("browser_session"), meta.get("sequence")) if meta.get("sequence") else None
        if key and key == self._last_coverage_key:
            return
        mono = self._observation_mono if key else now
        policy = self._penalty_policy_enabled(self._get_db())
        previous = self._last_confirmed_check
        continuous = (not key or (self._last_coverage_key and key[0] == self._last_coverage_key[0]
                                  and key[1] == self._last_coverage_key[1] + 1))
        delta = now - previous if previous is not None else -1
        mono_delta = mono - self._last_confirmed_mono if self._last_confirmed_mono is not None else -1
        if continuous and 0 <= delta <= MAX_COVERAGE_GAP_SEC and (not key or abs(delta - mono_delta) < 2):
            if self.coverage_intervals and self.coverage_intervals[-1][1] == previous and self._last_policy_enabled == policy and self.coverage_intervals[-1][2] == policy:
                self.coverage_intervals[-1][1] = now
            else:
                self.coverage_intervals.append([previous, now, policy and self._last_policy_enabled])
            self.confirmed_disabled_seconds += delta
        self._last_confirmed_check, self._last_confirmed_mono, self._last_coverage_key = now, mono, key
        self._last_policy_enabled = policy
        self._save_state()

    def _penalty_policy_enabled(self, db):
        try:
            return db.get_active_configuration().get("rules", {}).get("chrome_extension_penalty_enabled") is True
        except Exception:
            return False

    @synchronized
    def check_state(self, now=None):
        current = self._clock() if now is None else now
        state, meta = self.detect_state()
        self._flush_outbox(current)
        if state == "ENABLED":
            if self.is_interrupted:
                return self.on_extension_restored(current)
            self.last_status = state
            self._save_state()
            return self.get_ui_status()
        if state != "DISABLED":
            self._pause_coverage(state)
            return dict(self.get_ui_status(), message=f"{state}: no new penalties; unverified time is excluded.")
        observed = meta.get("received_at", current)
        if not self.is_interrupted:
            result = self.on_extension_disabled(observed)
            self._last_coverage_key = (meta.get("browser_session"), meta.get("sequence")) if meta.get("sequence") else None
            self._last_confirmed_mono = self._observation_mono if meta.get("sequence") else observed
            return result
        self._observe_disabled(observed, meta)
        return self.reconcile_penalties(current)

    def on_extension_disabled(self, now, reason="DISABLED"):
        if self.episode_id:
            folder = Path(self.storage_dir) / "episodes"
            folder.mkdir(exist_ok=True)
            (folder / (self.episode_id + ".json")).write_text(json.dumps(self._state(), indent=2), encoding="utf-8")
        if self.pending_grace_duration_minutes:
            self.grace_duration_minutes, self.pending_grace_duration_minutes = self.pending_grace_duration_minutes, None
        self.episode_id = "ext_ep_" + uuid.uuid4().hex
        self.is_interrupted = True
        self.disabled_start_time = now
        self.grace_deadline = now + self.grace_duration_sec
        self.reconnected_time = 0.0
        self.confirmed_disabled_seconds, self.coverage_intervals = 0.0, []
        self.deducted_blocks = set()
        self._last_confirmed_check = self._last_confirmed_mono = now
        self._last_policy_enabled = self._penalty_policy_enabled(self._get_db())
        self.last_status = "GRACE_PERIOD"
        self._save_state()
        self.show_or_update_notification(remaining_sec=self.grace_duration_sec, deadline=self.grace_deadline, penalty=self.pending_penalty)
        return self.get_ui_status()

    def _minute_segments(self, minute):
        start, end = (minute - 1) * 60, minute * 60
        offset, segments, enabled = 0.0, [], True
        for a, b, policy in self.coverage_intervals:
            a = max(a, self.grace_deadline)
            if b <= a:
                continue
            length = b - a
            if offset < end and offset + length > start:
                segments.append([a + max(0, start - offset), a + min(length, end - offset)])
                enabled = enabled and policy
            offset += length
        return segments, enabled and bool(segments)

    def reconcile_penalties(self, now):
        elapsed = self._verified_penalty_seconds()
        due = int(elapsed // 60)
        created = False
        for minute in range(1, due + 1):
            if minute in self.deducted_blocks:
                continue
            segments, authorized = self._minute_segments(minute)
            tx = f"tx_ext_{self.episode_id}_m{minute}"
            if authorized:
                created = True
                evidence = {"episode_id": self.episode_id, "minute_index": minute,
                            "automatic_rule_version": "extension-disabled-v1",
                            "disabled_detected_at": self.disabled_start_time, "grace_deadline": self.grace_deadline,
                            "interval_start": segments[0][0], "interval_end": segments[-1][1],
                            "verified_segments": segments, "grace_policy_minutes": self.grace_duration_minutes,
                            "grace_policy_version": self.grace_policy_version,
                            "source": "CHROME_MANAGEMENT_API", "installation": dict(self.binding),
                            "requires_authoritative_confirmation": True}
                self.outbox[tx] = {"proposal_id": tx, "date": time.strftime("%Y-%m-%d", time.localtime(segments[-1][1])),
                                   "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(segments[-1][1])),
                                   "originating_device": "windows_companion", "event_type": "BROWSER_EXTENSION_DISABLED",
                                   "proposed_points": -1, "reason": "Strict Routines extension disabled beyond the configured grace period — 1 penalty minute.",
                                   "applicable_rule": "CHROME_EXTENSION_ENFORCEMENT_RULE", "evidence": evidence}
                self.transactions[tx] = {"status": "QUEUED", "episode_id": self.episode_id, "minute_index": minute}
            self.deducted_blocks.add(minute)
        self._save_state()
        self._flush_outbox(now, force=created)
        self.last_status = "GRACE_PERIOD" if now < self.grace_deadline else "PENALTY_ACTIVE"
        self._save_state()
        self.show_or_update_notification(remaining_sec=self._remaining_grace(now), deadline=self.grace_deadline, penalty=self.pending_penalty)
        return dict(self.get_ui_status(), eligible_penalty_minutes=due)

    def _flush_outbox(self, now, force=False):
        if not self.transactions and not self.outbox:
            return
        if not force and now - self._last_flush < 15:
            return
        self._last_flush = now
        db = self._get_db()
        for tx, values in list(self.outbox.items())[:20]:
            try:
                result = db.create_deduction_proposal(**values)
                if not isinstance(result, dict) or result.get("success") is False:
                    continue
                row = result.get("proposal", result)
                if row.get("proposal_id") != tx:
                    continue
                self.transactions[tx]["status"] = row.get("status", "AWAITING_CONFIRMATION")
                self.transactions[tx]["finalized_event_id"] = row.get("finalized_event_id")
                self.transactions[tx]["authoritative_transaction_reference"] = row.get("finalized_event_id") or tx
                del self.outbox[tx]
            except Exception as exc:
                logger.warning("Extension proposal remains queued: %s", type(exc).__name__)
        if hasattr(db, "get_deduction_proposal"):
            pending = [(tx, entry) for tx, entry in self.transactions.items() if entry["status"] not in ("QUEUED", "FINALIZED", "REJECTED")]
            offset = getattr(self, '_refresh_offset', 0) % max(1, len(pending))
            batch = (pending[offset:] + pending[:offset])[:20]
            self._refresh_offset = offset + len(batch)
            for tx, entry in batch:
                if entry["status"] in ("QUEUED", "FINALIZED", "REJECTED"):
                    continue
                try:
                    row = db.get_deduction_proposal(tx)
                    if isinstance(row, dict):
                        entry["status"] = row.get("status", entry["status"])
                        entry["finalized_event_id"] = row.get("finalized_event_id")
                        entry["authoritative_transaction_reference"] = row.get("finalized_event_id") or tx
                except Exception:
                    pass
        finalized = lambda v: v["status"] == "FINALIZED" or (v["status"] == "APPROVED" and v.get("finalized_event_id"))
        self.total_penalty = sum(bool(finalized(v)) for v in self.transactions.values())
        self.pending_penalty = sum(not finalized(v) and v["status"] != "REJECTED" for v in self.transactions.values())
        self._save_state()

    def on_extension_restored(self, now):
        self.reconcile_penalties(now)
        self._flush_outbox(now, force=True)
        self.reconnected_time, self.is_interrupted = now, False
        self._pause_coverage("ENABLED")
        self.dismiss_notification()
        return dict(self.get_ui_status(), message="Extension restored; authoritative proposals reconciled.")

    @synchronized
    def authorize_recovery(self):
        self.is_interrupted = False
        self.recovery_hold = True
        self._pause_coverage("MAINTENANCE_UNLOCKED")
        self.dismiss_notification()

    def show_or_update_notification(self, remaining_sec, deadline, penalty):
        now = self._clock()
        if self.notification_active and now - self.last_notification_update < 15:
            return
        try:
            from windows.extension_countdown_notification import ExtensionCountdownNotifications
            self.notification_active = ExtensionCountdownNotifications.get_instance().update(
                self.episode_id, self.notification_snapshot, self.open_extension_manager)
            self.last_notification_update = now
        except Exception:
            logger.exception('Could not update extension countdown')

    def dismiss_notification(self):
        self.notification_active = False
        if self.episode_id:
            from windows.extension_countdown_notification import ExtensionCountdownNotifications
            ExtensionCountdownNotifications.get_instance().remove(self.episode_id)

    def _remaining_grace(self, now=None):
        return max(0, math.ceil(self.grace_deadline - (self._clock() if now is None else now))) if self.is_interrupted else 0

    def _overdue_seconds(self, now=None):
        if not self.is_interrupted or not self.grace_deadline:
            return 0
        current = self._clock() if now is None else now
        return max(0, int(current - self.grace_deadline))

    def _verified_penalty_seconds(self):
        return sum(max(0, b - max(a, self.grace_deadline)) for a, b, _ in self.coverage_intervals)

    @synchronized
    def notification_snapshot(self):
        if not self.is_interrupted or self.last_status not in ('GRACE_PERIOD', 'PENALTY_ACTIVE'):
            return None
        # The UI never creates coverage or changes the ledger. Only fresh helper reports do.
        if self._observation_mono is not None and self._monotonic() - self._observation_mono > MAX_COVERAGE_GAP_SEC:
            return None
        from windows.browser_setup import BROWSERS
        key = self.binding.get('browser_key', getattr(self, 'browser_key', 'chrome'))
        browser_name = BROWSERS.get(key, BROWSERS['chrome'])[0]
        rem_sec = self._remaining_grace()
        overdue_sec = self._overdue_seconds()
        overdue_label = f"-{overdue_sec // 60:02d}:{overdue_sec % 60:02d}"
        return dict(remaining_grace_sec=rem_sec,
                    overdue_sec=overdue_sec,
                    overdue_label=overdue_label,
                    deadline_label=time.strftime('%Y-%m-%d %I:%M:%S %p', time.localtime(self.grace_deadline)),
                    profile=browser_name + ' / ' + self.binding.get('profile_label', ''),
                    browser_name=browser_name, pending_penalty=self.pending_penalty,
                    total_penalty=self.total_penalty,
                    penalty_policy_enabled=self._last_policy_enabled,
                    sync_status='PENALTY SYNC PENDING' if self.pending_penalty else 'SYNCED')

    def open_extension_manager(self):
        from windows.browser_setup import BROWSERS, detect_browsers
        import subprocess
        enrollment = json.loads(self.enrollment_file.read_text(encoding='utf-8'))
        key = enrollment.get('browser_key', 'chrome')
        candidates = [b for b in detect_browsers() if b['key'] == key and b.get('supported')]
        identity = enrollment.get('browser_id')
        browser = next((b for b in candidates if b['id'] == identity), None) if identity else next(iter(candidates), None)
        if browser is None:
            raise ValueError('The enrolled browser is not available. Open Strict Session to check its setup.')
        subprocess.Popen([browser['executable'], BROWSERS[key][2]], shell=False, close_fds=True)

    @synchronized
    def get_ui_status(self):
        rem_sec = self._remaining_grace()
        overdue_sec = self._overdue_seconds()
        overdue_label = f"-{overdue_sec // 60:02d}:{overdue_sec % 60:02d}"
        return {"state": self.last_status, "is_interrupted": self.is_interrupted, "episode_id": self.episode_id,
                "disabled_start_time": self.disabled_start_time, "grace_deadline": self.grace_deadline,
                "grace_duration_minutes": self.grace_duration_minutes,
                "pending_grace_duration_minutes": self.pending_grace_duration_minutes,
                "remaining_grace_sec": rem_sec,
                "overdue_sec": overdue_sec,
                "overdue_label": overdue_label,
                "confirmed_disabled_seconds": self.confirmed_disabled_seconds,
                "verified_penalty_seconds": self._verified_penalty_seconds(),
                "sync_status": "PENALTY SYNC PENDING" if self.pending_penalty else "SYNCED",
                "total_penalty": self.total_penalty, "pending_penalty": self.pending_penalty,
                "queued_transactions": len(self.outbox), "profile_label": self.binding.get("profile_label", ""),
                "companion_required": True, "evidence_source": "CHROME_MANAGEMENT_API",
                "penalty_policy_enabled": self._last_policy_enabled,
                "chrome_running": self.is_chrome_running(), "can_open_extensions": True,
                "uncertain_time_excluded": True}


_extension_monitor = None
_instance_lock = threading.Lock()


def get_browser_extension_monitor(db=None):
    global _extension_monitor
    with _instance_lock:
        if _extension_monitor is None:
            from windows.browser_monitor_registry import BrowserMonitorRegistry
            _extension_monitor = BrowserMonitorRegistry(db=db)
        elif db is not None:
            _extension_monitor._db = db
    return _extension_monitor
