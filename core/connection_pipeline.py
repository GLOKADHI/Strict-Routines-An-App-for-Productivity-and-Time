"""
StrictRecords PC ↔ Android Connection Pipeline, Device Control, Data Transfer & Sync Center
Implements the 15-state Connection State Machine, USB First-Pairing with 6-digit Code,
Local Wi-Fi Automatic Discovery, Bidirectional Transfer Queue with 4-stage ACKs & SHA-256 Hashing,
and Admin Remote Discipline Control Center.
"""

import base64
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional


class ConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    DETECTING = "DETECTING"
    DEVICE_FOUND = "DEVICE_FOUND"
    PAIR_REQUESTED = "PAIR_REQUESTED"
    PAIRING = "PAIRING"
    AUTHORIZING = "AUTHORIZING"
    HANDSHAKING = "HANDSHAKING"
    CONNECTED = "CONNECTED"
    SYNCING = "SYNCING"
    SYNC_COMPLETE = "SYNC_COMPLETE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    RECONNECTING = "RECONNECTING"
    REVOKED = "REVOKED"
    FAILED = "FAILED"


class TransportType(str, Enum):
    USB = "USB"
    LOCAL_WIFI = "LOCAL_WIFI"


class AckStage(str, Enum):
    SENT = "SENT"
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    STORED = "STORED"


class TransferStatus(str, Enum):
    PENDING = "PENDING"
    IN_TRANSIT = "IN_TRANSIT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TransferDirection(str, Enum):
    ANDROID_TO_PC = "ANDROID_TO_PC"
    PC_TO_ANDROID = "PC_TO_ANDROID"


class CommandType(str, Enum):
    SYNC_NOW = "SYNC_NOW"
    REQUEST_STATUS = "REQUEST_STATUS"
    REFRESH_DEVICE = "REFRESH_DEVICE"
    PUSH_CONFIG = "PUSH_CONFIG"
    SEND_STRICTBOT_MESSAGE = "SEND_STRICTBOT_MESSAGE"
    VIEW_HEALTH = "VIEW_HEALTH"
    REVOKE_DEVICE = "REVOKE_DEVICE"
    UNPAIR = "UNPAIR"
    START_STRICT_SESSION = "START_STRICT_SESSION"
    STOP_STRICT_SESSION = "STOP_STRICT_SESSION"
    PAUSE_STRICT_SESSION = "PAUSE_STRICT_SESSION"
    RESUME_STRICT_SESSION = "RESUME_STRICT_SESSION"
    UPDATE_ALLOWED_APPS_POLICY = "UPDATE_ALLOWED_APPS_POLICY"
    REQUEST_USAGE_SYNC = "REQUEST_USAGE_SYNC"
    SESSION_STATUS_QUERY = "SESSION_STATUS_QUERY"
    UPDATE_SESSION_PHASE = "UPDATE_SESSION_PHASE"


PIPELINE_STAGES = [
    {"name": "DETECTING", "label": "Detecting", "icon": "🔍", "order": 1},
    {"name": "PAIRING", "label": "Pairing", "icon": "🔗", "order": 2},
    {"name": "AUTHORIZING", "label": "Authorizing", "icon": "🛡️", "order": 3},
    {"name": "HANDSHAKE", "label": "Handshake", "icon": "🤝", "order": 4},
    {"name": "CONNECTED", "label": "Connected", "icon": "⚡", "order": 5},
    {"name": "SYNCING", "label": "Syncing", "icon": "🔄", "order": 6},
    {"name": "VERIFIED", "label": "Verified", "icon": "✓", "order": 7},
    {"name": "READY", "label": "Ready", "icon": "✨", "order": 8},
]


class ConnectionPipelineManager:
    """
    Central Coordinator for the StrictRecords PC ↔ Android Connection Pipeline.
    Manages physical transport awareness (USB vs Wi-Fi), cryptographic device trust,
    real packet movement tracking, 4-stage transfer acknowledgements, and disciplined
    admin command dispatch.
    """

    def __init__(self, db, scoring_engine=None):
        self.db = db
        self.engine = scoring_engine
        self._states: Dict[str, Dict[str, Any]] = {}
        self._in_flight_packets: List[Dict[str, Any]] = []

    def _ensure_device_state(self, device_id: str) -> Dict[str, Any]:
        if device_id not in self._states:
            # Query DB for pairing and status
            with self.db._connect() as conn:
                dev = conn.execute(
                    "SELECT * FROM devices WHERE device_id = ?", (device_id,)
                ).fetchone()
                pairing = conn.execute(
                    """
                    SELECT * FROM device_pairings 
                    WHERE (android_device_id = ? OR windows_device_id = ?) 
                    ORDER BY paired_at DESC, rowid DESC LIMIT 1
                    """,
                    (device_id, device_id),
                ).fetchone()

            initial_state = ConnectionState.DISCONNECTED.value
            transport = TransportType.USB.value
            trust = "UNPAIRED"

            if pairing:
                if pairing["status"] == "PAIRED" and not pairing["revoked_at"]:
                    initial_state = ConnectionState.CONNECTED.value
                    trust = "VERIFIED"
                    transport = TransportType.LOCAL_WIFI.value
                elif pairing["status"] == "REVOKED":
                    initial_state = ConnectionState.REVOKED.value
                    trust = "REVOKED"
                elif pairing["status"] == "PENDING_APPROVAL":
                    initial_state = ConnectionState.PAIR_REQUESTED.value
                    trust = "PENDING_APPROVAL"
            elif dev and dev["pairing_state"] == "REVOKED":
                initial_state = ConnectionState.REVOKED.value
                trust = "REVOKED"

            now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._states[device_id] = {
                "state": initial_state,
                "transport": transport,
                "trust": trust,
                "latency_ms": 12.0,
                "last_state_change": now_iso,
                "last_heartbeat": now_iso,
                "active_stage_idx": self._determine_stage_index(initial_state),
                "history": [],
            }
        return self._states[device_id]

    def _determine_stage_index(self, state_str: str) -> int:
        mapping = {
            ConnectionState.DETECTING.value: 1,
            ConnectionState.DEVICE_FOUND.value: 1,
            ConnectionState.PAIR_REQUESTED.value: 2,
            ConnectionState.PAIRING.value: 2,
            ConnectionState.AUTHORIZING.value: 3,
            ConnectionState.HANDSHAKING.value: 4,
            ConnectionState.CONNECTED.value: 5,
            ConnectionState.SYNCING.value: 6,
            ConnectionState.SYNC_COMPLETE.value: 7,
            "READY": 8,
        }
        return mapping.get(state_str, 0)

    def get_current_state(self, device_id: str) -> str:
        st = self._ensure_device_state(device_id)
        return st["state"]

    def transition_state(
        self,
        device_id: str,
        to_state: ConnectionState,
        transport: Optional[TransportType] = None,
        reason: str = "",
        latency_ms: Optional[float] = None,
    ) -> str:
        """
        Safely transitions connection state, verifies policy barriers (e.g. revoked devices),
        updates persistent connection history, and updates UI pipeline indicators.
        """
        st = self._ensure_device_state(device_id)
        from_state = st["state"]
        to_state_val = to_state.value if isinstance(to_state, ConnectionState) else str(to_state)

        # Barrier 1: If device is REVOKED in DB, disallow transitioning to CONNECTED or SYNCING
        with self.db._connect() as conn:
            dev = conn.execute("SELECT pairing_state FROM devices WHERE device_id = ?", (device_id,)).fetchone()
            if dev and dev["pairing_state"] == "REVOKED" and to_state_val not in (
                ConnectionState.REVOKED.value,
                ConnectionState.DISCONNECTED.value,
                ConnectionState.PAIR_REQUESTED.value,
                ConnectionState.PAIRING.value,
            ):
                to_state_val = ConnectionState.REVOKED.value
                reason = "Blocked: Device trust is revoked by Administrator"

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if transport:
            st["transport"] = transport.value if isinstance(transport, TransportType) else str(transport)
        if latency_ms is not None:
            st["latency_ms"] = float(latency_ms)

        st["state"] = to_state_val
        st["last_state_change"] = now_iso
        st["active_stage_idx"] = self._determine_stage_index(to_state_val)

        # Log transition to database
        self.db.log_connection_event(
            device_id=device_id,
            from_state=from_state,
            to_state=to_state_val,
            transport_mode=st["transport"],
            latency_ms=st["latency_ms"],
            reason=reason,
        )

        return to_state_val

    def detect_device(
        self,
        device_id: str = "android_lok_phone",
        transport: str = "USB",
        device_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Triggered when USB cable is attached or Wi-Fi discovery packet is intercepted."""
        t_enum = TransportType.USB if transport.upper() == "USB" else TransportType.LOCAL_WIFI
        self.transition_state(device_id, ConnectionState.DETECTING, transport=t_enum, reason="Physical connection detected")
        self.transition_state(device_id, ConnectionState.DEVICE_FOUND, transport=t_enum, reason="Device identity broadcast received")

        # Check existing trust
        with self.db._connect() as conn:
            p = conn.execute(
                """
                SELECT * FROM device_pairings 
                WHERE (android_device_id = ? OR windows_device_id = ?) 
                  AND status = 'PAIRED' AND revoked_at IS NULL
                """,
                (device_id, device_id),
            ).fetchone()

        st = self._ensure_device_state(device_id)
        if p:
            st["trust"] = "VERIFIED"
            self.transition_state(device_id, ConnectionState.HANDSHAKING, reason="Known trusted device reconnecting")
            self.transition_state(device_id, ConnectionState.CONNECTED, reason="Trusted session active")
        else:
            st["trust"] = "UNPAIRED"

        return {
            "device_id": device_id,
            "state": st["state"],
            "trust": st["trust"],
            "transport": st["transport"],
        }

    def initiate_pairing(
        self,
        android_device_id: str = "android_lok_phone",
        transport: str = "USB",
    ) -> Dict[str, Any]:
        """
        Initiates 6-digit cryptographic pairing session over USB.
        Generates random 6-digit verification code (e.g. '839 214').
        """
        t_enum = TransportType.USB if transport.upper() == "USB" else TransportType.LOCAL_WIFI
        res = self.db.initiate_usb_pairing(
            android_device_id=android_device_id,
            windows_device_id="windows_lok_pc",
        )
        self.transition_state(
            android_device_id,
            ConnectionState.PAIR_REQUESTED,
            transport=t_enum,
            reason=f"Pairing initiated with verification code {res['verification_code']}",
        )
        self.transition_state(
            android_device_id,
            ConnectionState.PAIRING,
            transport=t_enum,
            reason="Awaiting mutual user approval on Windows & Android",
        )
        st = self._ensure_device_state(android_device_id)
        st["trust"] = "PAIRING_IN_PROGRESS"
        return res

    def verify_pairing_code(
        self,
        code: str,
        android_device_id: str = "android_lok_phone",
        admin_user: str = "Administrator",
    ) -> Dict[str, Any]:
        """
        Validates the 6-digit code entered by user, establishes mutual trust token,
        and transitions state machine: AUTHORIZING -> HANDSHAKING -> CONNECTED.
        """
        self.transition_state(android_device_id, ConnectionState.AUTHORIZING, reason="Verifying 6-digit pairing code")
        try:
            res = self.db.verify_pairing_code(code, device_id=android_device_id, admin_user=admin_user)
        except Exception as e:
            self.transition_state(android_device_id, ConnectionState.FAILED, reason=str(e))
            raise

        self.transition_state(android_device_id, ConnectionState.HANDSHAKING, reason="Exchanging trust tokens")
        self.transition_state(android_device_id, ConnectionState.CONNECTED, reason="Mutual trust established")
        st = self._ensure_device_state(android_device_id)
        st["trust"] = "VERIFIED"
        st["state"] = ConnectionState.CONNECTED.value
        st["active_stage_idx"] = 5
        return res

    def register_client_device(
        self,
        device_id: str,
        device_name: str,
        platform: str,
        app_version: str = "2.0.0",
        battery_level: int = 100,
        user_id: str = "user_lok",
        transport: str = "LOCAL_WIFI",
    ) -> Dict[str, Any]:
        """Registers a client under the StrictRecords Account."""
        dev = self.db.register_or_update_device(
            device_id=device_id,
            user_id=user_id,
            device_name=device_name,
            platform=platform,
            app_version=app_version,
            battery_level=battery_level,
            sync_status="ONLINE",
            pairing_state="PAIRED",
        )
        t_enum = TransportType.USB if transport.upper() == "USB" else TransportType.LOCAL_WIFI
        self.transition_state(device_id, ConnectionState.CONNECTED, transport=t_enum, reason="Client device registered under account")
        st = self._ensure_device_state(device_id)
        st["trust"] = "VERIFIED"
        return {
            "registered": True,
            "device": dev,
            "account_id": user_id,
            "connection_state": st["state"],
            "trust": st["trust"],
        }

    def initiate_wifi_pairing(
        self,
        android_device_id: str = "android_lok_phone",
        ip_address: str = "192.168.1.100",
        port: int = 8765,
    ) -> Dict[str, Any]:
        """Initiates local Wi-Fi pairing session and returns 6-digit challenge code."""
        res = self.initiate_pairing(android_device_id=android_device_id, transport="LOCAL_WIFI")
        res["ip_address"] = ip_address
        res["port"] = port
        res["discovery_url"] = f"http://{ip_address}:{port}"
        return res

    def get_account_device_mesh(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Returns the synchronized two-client mesh for the StrictRecords account."""
        managed = self.db.get_managed_devices()
        windows_client = next((d for d in managed if d.get("platform") == "WINDOWS"), None)
        android_client = next((d for d in managed if d.get("platform") == "ANDROID"), None)
        mesh_status = "ONLINE" if (windows_client and android_client) else ("PARTIAL" if (windows_client or android_client) else "NO_DEVICES")
        return {
            "account_id": user_id or (managed[0]["user_id"] if managed else "unassigned"),
            "windows_client": windows_client,
            "android_client": android_client,
            "all_devices": managed,
            "mesh_status": mesh_status,
            "active_transport": "DISCONNECTED" if not managed else ("LOCAL_WIFI" if android_client else "LOCAL"),
        }

    def record_heartbeat(
        self,
        device_id: str,
        transport: str = "LOCAL_WIFI",
        telemetry: Optional[Dict[str, Any]] = None,
        latency_ms: float = 12.0,
    ) -> Dict[str, Any]:
        """
        Processes real-time heartbeat and device health metrics (battery, storage, network).
        Transitions RECONNECTING / OFFLINE -> CONNECTED or DEGRADED if latency exceeds 800ms.
        """
        with self.db._connect() as conn:
            dev = conn.execute("SELECT pairing_state FROM devices WHERE device_id = ?", (device_id,)).fetchone()
            pairing = conn.execute(
                """
                SELECT * FROM device_pairings 
                WHERE (android_device_id = ? OR windows_device_id = ?) 
                  AND status = 'PAIRED' AND revoked_at IS NULL
                """,
                (device_id, device_id),
            ).fetchone()

        if dev and dev["pairing_state"] == "REVOKED":
            self.transition_state(device_id, ConnectionState.REVOKED, reason="Heartbeat rejected: Device trust revoked")
            raise PermissionError(f"Device {device_id} trust has been revoked by Administrator.")

        if not pairing:
            self.transition_state(device_id, ConnectionState.DISCONNECTED, reason="Device not paired")
            return {"device_id": device_id, "state": ConnectionState.DISCONNECTED.value, "trust": "UNPAIRED"}

        t_enum = TransportType.USB if transport.upper() == "USB" else TransportType.LOCAL_WIFI
        new_state = ConnectionState.DEGRADED if latency_ms > 800.0 else ConnectionState.CONNECTED

        if telemetry:
            telemetry["latency_ms"] = latency_ms
            self.db.update_device_telemetry(device_id, telemetry)

        self.transition_state(
            device_id,
            new_state,
            transport=t_enum,
            latency_ms=latency_ms,
            reason=f"Heartbeat received ({latency_ms:.1f}ms latency)",
        )

        st = self._ensure_device_state(device_id)
        st["last_heartbeat"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st["trust"] = "VERIFIED"

        return {
            "device_id": device_id,
            "state": st["state"],
            "trust": st["trust"],
            "transport": st["transport"],
            "latency_ms": latency_ms,
        }

    def enqueue_transfer_item(
        self,
        device_id: str,
        direction: TransferDirection,
        item_type: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Puts an item into the bidirectional transfer queue with SHA-256 integrity hash.
        Tracks the moving packet for genuine particle flow animation.
        """
        payload_str = json.dumps(payload, sort_keys=True)
        payload_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
        transfer_id = f"tx_{uuid.uuid4().hex[:12]}"
        size_bytes = len(payload_str.encode("utf-8"))

        dir_str = direction.value if isinstance(direction, TransferDirection) else str(direction)
        item = self.db.save_transfer_item(
            transfer_id=transfer_id,
            device_id=device_id,
            direction=dir_str,
            item_type=item_type,
            payload_json=payload_str,
            payload_hash=payload_hash,
            size_bytes=size_bytes,
            ack_stage=AckStage.SENT.value,
            status=TransferStatus.PENDING.value,
        )

        # Track packet movement for real-time visualization
        packet_info = {
            "transfer_id": transfer_id,
            "direction": dir_str,
            "item_type": item_type,
            "size_bytes": size_bytes,
            "label": f"{item_type} ({max(1, size_bytes // 1024)} KB)",
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }
        self._in_flight_packets.append(packet_info)
        if len(self._in_flight_packets) > 10:
            self._in_flight_packets = self._in_flight_packets[-10:]

        return item

    def process_bidirectional_sync(
        self,
        items: List[Dict[str, Any]],
        device_id: str = "android_lok_phone",
        pairing_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes real data transfer with 4-stage ACKs (SENT -> RECEIVED -> VALIDATED -> STORED),
        SHA-256 payload integrity check, and strict idempotency (zero point duplication).
        """
        # Validate device authorization
        with self.db._connect() as conn:
            dev = conn.execute("SELECT pairing_state FROM devices WHERE device_id = ?", (device_id,)).fetchone()
            pairing = conn.execute(
                """
                SELECT * FROM device_pairings 
                WHERE (android_device_id = ? OR windows_device_id = ?) 
                  AND status = 'PAIRED' AND revoked_at IS NULL
                """,
                (device_id, device_id),
            ).fetchone()

        if dev and dev["pairing_state"] == "REVOKED":
            self.transition_state(device_id, ConnectionState.REVOKED, reason="Sync blocked: Device trust is revoked")
            raise PermissionError(f"Device {device_id} trust has been revoked by Administrator. Synchronization blocked.")

        if not pairing:
            self.transition_state(device_id, ConnectionState.DISCONNECTED, reason="Sync refused: Device not paired")
            raise PermissionError(f"Device {device_id} is not paired.")

        self.transition_state(device_id, ConnectionState.SYNCING, reason=f"Syncing batch of {len(items)} items")

        acks = []
        applied_count = 0
        skipped_duplicates = 0
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for raw_item in items:
            transfer_id = raw_item.get("transfer_id") or raw_item.get("sync_id") or f"tx_{uuid.uuid4().hex[:12]}"
            ptype = raw_item.get("payload_type") or raw_item.get("item_type", "SCORE_EVENT")
            payload = raw_item.get("payload", {})
            date_val = payload.get("date") or datetime.now().strftime("%Y-%m-%d")
            is_dev = bool(payload.get("is_dev_mode", False))

            payload_str = json.dumps(payload, sort_keys=True)
            expected_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
            provided_hash = raw_item.get("payload_hash") or expected_hash

            # Stage 1: RECEIVED
            self.db.update_transfer_item(transfer_id, AckStage.RECEIVED.value, TransferStatus.IN_TRANSIT.value)

            # Stage 2: VALIDATED
            hash_valid = (expected_hash == provided_hash)
            if not hash_valid:
                self.db.update_transfer_item(
                    transfer_id,
                    AckStage.VALIDATED.value,
                    TransferStatus.FAILED.value,
                    error_message="SHA-256 payload checksum mismatch",
                )
                acks.append({
                    "transfer_id": transfer_id,
                    "ack_stage": AckStage.VALIDATED.value,
                    "status": "CHECKSUM_ERROR",
                    "hash_valid": False,
                })
                continue

            self.db.update_transfer_item(transfer_id, AckStage.VALIDATED.value, TransferStatus.IN_TRANSIT.value)

            # Stage 3: STORED (with strict Idempotency check)
            is_duplicate = False

            if ptype == "SCORE_EVENT":
                eid = payload.get("event_id") or transfer_id
                edate = payload.get("date") or date_val
                etype = payload.get("event_type", "SCORE_SYNC")
                pts = int(payload.get("points", 0))
                desc = payload.get("description", "Synced Score Event")
                audit = payload.get("audit") or {}
                with self.db._connect() as conn:
                    existing_ev = conn.execute("SELECT 1 FROM score_events WHERE event_id = ?", (eid,)).fetchone()
                if existing_ev:
                    is_duplicate = True
                    skipped_duplicates += 1
                else:
                    with self.db._connect() as conn:
                        conn.execute(
                            """INSERT OR IGNORE INTO score_events (
                                event_id, date, is_dev_mode, timestamp, event_type,
                                device_id, description, points, rule_version, audit_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                            (eid, edate, 1 if is_dev else 0, now_iso, etype, device_id, desc, pts, json.dumps(audit)),
                        )
                    if self.engine:
                        self.engine.recalculate_daily_score(edate, is_dev_mode=is_dev)
                    applied_count += 1

            elif ptype == "TASK_COMPLETION":
                task_code = payload.get("task_code")
                if task_code and self.engine:
                    try:
                        self.engine.complete_custom_task(
                            date=date_val,
                            task_code=task_code,
                            completed_at=payload.get("completed_at") or now_iso,
                            duration_minutes=float(payload.get("duration_minutes", 15.0)),
                            device_id=device_id,
                            is_dev_mode=is_dev,
                        )
                        applied_count += 1
                    except Exception:
                        is_duplicate = True
                        skipped_duplicates += 1

            elif ptype == "EVIDENCE_PHOTO":
                image_b64 = payload.get("image_base64")
                ev_id = payload.get("evidence_id") or f"ev_{uuid.uuid4().hex[:12]}"
                t_code = payload.get("task_code", "CUSTOM_TASK")
                t_title = payload.get("task_title", "User Submitted Proof")
                u_id = payload.get("user_id") or "user_lok"
                if not image_b64:
                    self.db.update_transfer_item(
                        transfer_id,
                        AckStage.STORED.value,
                        TransferStatus.FAILED.value,
                        error_message="Evidence upload requires image bytes and the server evidence store.",
                    )
                    acks.append({
                        "transfer_id": transfer_id,
                        "ack_stage": AckStage.STORED.value,
                        "status": "EVIDENCE_BYTES_REQUIRED",
                        "hash_valid": True,
                        "duplicate": False,
                    })
                    continue
                try:
                    try:
                        image_bytes = base64.b64decode(str(image_b64).split(",")[-1], validate=True)
                    except Exception:
                        image_bytes = str(image_b64).encode("utf-8")
                    
                    evidence = None
                    if self.engine:
                        try:
                            evidence = self.engine.store_evidence_file(
                                date=date_val,
                                record_type=t_code,
                                related_record_id=payload.get("related_record_id") or transfer_id,
                                device_id=device_id,
                                file_bytes=image_bytes,
                                filename_hint=payload.get("filename") or f"{ev_id}.jpg",
                                metadata={"task_title": t_title},
                                is_dev_mode=is_dev,
                                timestamp=now_iso,
                            )
                        except Exception:
                            pass
                    file_p = evidence["file_path"] if evidence else f"evidence/{ev_id}.jpg"
                    file_h = evidence["sha256_hash"] if evidence else hashlib.sha256(image_bytes).hexdigest()
                    with self.db._connect() as conn:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO evidence_files (
                                evidence_id, date, is_dev_mode, record_type, related_record_id,
                                file_path, sha256_hash, file_size_bytes, timestamp, device_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (ev_id, date_val, 1 if is_dev else 0, t_code, transfer_id, file_p, file_h, len(image_bytes), now_iso, device_id),
                        )
                    self.db.register_proof_review(
                        evidence_id=ev_id,
                        date=date_val,
                        task_code=t_code,
                        task_title=t_title,
                        file_path=file_p,
                        file_hash=file_h,
                        user_id=u_id,
                    )
                    applied_count += 1
                except Exception as exc:
                    self.db.update_transfer_item(
                        transfer_id,
                        AckStage.STORED.value,
                        TransferStatus.FAILED.value,
                        error_message=f"Evidence bytes could not be stored: {str(exc)[:160]}",
                    )
                    acks.append({
                        "transfer_id": transfer_id,
                        "ack_stage": AckStage.STORED.value,
                        "status": "INVALID_EVIDENCE",
                        "hash_valid": True,
                        "duplicate": False,
                    })
                    continue

            elif ptype in ("HOURLY_UPDATE", "ACTIVITY_LOG", "SNOOZE"):
                if self.engine:
                    self.engine.process_sync_batch([raw_item], device_id=device_id)
                    applied_count += 1

            # Advance to STORED
            self.db.update_transfer_item(transfer_id, AckStage.STORED.value, TransferStatus.COMPLETED.value)
            acks.append({
                "transfer_id": transfer_id,
                "ack_stage": AckStage.STORED.value,
                "status": "DUPLICATE_IGNORED" if is_duplicate else "STORED_OK",
                "hash_valid": True,
                "duplicate": is_duplicate,
            })

        self.transition_state(device_id, ConnectionState.SYNC_COMPLETE, reason="All transfer items acknowledged")
        self.transition_state(device_id, ConnectionState.CONNECTED, reason="Sync completed successfully")

        # Clear in-flight packet list
        self._in_flight_packets.clear()

        return {
            "device_id": device_id,
            "sync_status": "SYNC_COMPLETE",
            "applied_count": applied_count,
            "skipped_duplicates": skipped_duplicates,
            "acks": acks,
            "synced_at": now_iso,
        }

    def dispatch_admin_command(
        self,
        command_type: str,
        device_id: str = "android_lok_phone",
        payload: Optional[Dict[str, Any]] = None,
        admin_user: str = "Administrator",
    ) -> Dict[str, Any]:
        """
        Dispatches legitimate discipline administrative commands to Android.
        Enforces discipline boundary: NO remote shell, NO silent Android permission grants.
        """
        cmd_enum_val = command_type.upper().strip()
        allowed_commands = {c.value for c in CommandType}
        if cmd_enum_val not in allowed_commands:
            raise ValueError(f"Unknown or unauthorized admin command type: {command_type}")

        payload_dict = payload or {}
        command_id = f"cmd_{uuid.uuid4().hex[:12]}"
        nonce = f"nonce_{uuid.uuid4().hex[:8]}"

        # Special local actions on PC side
        if cmd_enum_val == CommandType.REVOKE_DEVICE.value:
            self.db.revoke_device_pairing(device_id=device_id, admin_user=admin_user)
            self.transition_state(device_id, ConnectionState.REVOKED, reason="Device revoked by Administrator")
        elif cmd_enum_val == CommandType.UNPAIR.value:
            self.db.unpair_device(device_id=device_id, admin_user=admin_user)
            self.transition_state(device_id, ConnectionState.DISCONNECTED, reason="Device unpaired by Administrator")
        elif cmd_enum_val == CommandType.PUSH_CONFIG.value:
            settings = self.db.get_settings()
            payload_dict["config"] = settings.to_dict()
            payload_dict["rule_version"] = getattr(settings, "rule_version", 1)
            # Queue config update in transfer queue
            self.enqueue_transfer_item(
                device_id=device_id,
                direction=TransferDirection.PC_TO_ANDROID,
                item_type="CONFIG_UPDATE",
                payload=payload_dict,
            )

        cmd = self.db.create_device_command(
            command_id=command_id,
            device_id=device_id,
            command_type=cmd_enum_val,
            payload_json=json.dumps(payload_dict),
            nonce=nonce,
            admin_user=admin_user,
        )
        return cmd

    def poll_device_commands(self, device_id: str = "android_lok_phone") -> List[Dict[str, Any]]:
        """Android fetches pending admin commands."""
        return self.db.poll_device_commands(device_id)

    def ack_device_command(
        self,
        command_id: str,
        result_payload: Optional[Dict[str, Any]] = None,
        success: bool = True,
        device_id: str = "",
    ) -> bool:
        """Android acknowledges execution of an admin command with result telemetry."""
        if not device_id:
            return False
        res_json = json.dumps(result_payload or {})
        status = "ACKNOWLEDGED" if success else "FAILED"
        updated = self.db.ack_device_command(
            command_id,
            res_json,
            status=status,
            device_id=device_id,
        )
        if not updated:
            return False

        # If result contains device telemetry, update in DB
        if result_payload and ("battery_level" in result_payload or "storage_free_mb" in result_payload):
            self.db.update_device_telemetry(device_id, result_payload)
        return True

    def clear_all_states(self) -> None:
        self._states.clear()
        self._in_flight_packets.clear()

    def get_device_center_summary(self, device_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Gathers complete state, real 8-stage pipeline status, active data flow,
        device diagnostics, transfer queues, and audit logs for the PC UI.
        """
        if not device_id:
            with self.db._connect() as conn:
                first_p = conn.execute("SELECT android_device_id FROM device_pairings WHERE status = 'PAIRED' AND revoked_at IS NULL ORDER BY paired_at DESC LIMIT 1").fetchone()
                if first_p:
                    device_id = first_p["android_device_id"]
                else:
                    first_d = conn.execute("SELECT device_id FROM devices WHERE device_id != 'win_host_pc' AND pairing_state != 'REVOKED' AND status != 'REVOKED' ORDER BY rowid ASC LIMIT 1").fetchone()
                    if first_d:
                        device_id = first_d["device_id"]

        st = self._ensure_device_state(device_id) if device_id else {
            "state": ConnectionState.DISCONNECTED.value,
            "transport": "NONE",
            "trust": "UNPAIRED",
            "latency_ms": 0.0,
            "last_state_change": "",
            "last_heartbeat": "",
            "active_stage_idx": 0,
            "history": [],
        }

        # Get device info from DB
        with self.db._connect() as conn:
            dev = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone() if device_id else None
            pairing = conn.execute(
                """
                SELECT * FROM device_pairings 
                WHERE (android_device_id = ? OR windows_device_id = ?) 
                ORDER BY paired_at DESC, rowid DESC LIMIT 1
                """,
                (device_id, device_id),
            ).fetchone() if device_id else None

            # Transfer counts today
            today = datetime.now().strftime("%Y-%m-%d")
            score_count = conn.execute(
                "SELECT COUNT(*) as c FROM score_events WHERE date = ?", (today,)
            ).fetchone()["c"]
            task_count = conn.execute(
                "SELECT COUNT(*) as c FROM scheduled_tasks WHERE date = ? AND state = 'COMPLETED'", (today,)
            ).fetchone()["c"]
            proof_count = conn.execute(
                "SELECT COUNT(*) as c FROM proof_reviews WHERE date = ?", (today,)
            ).fetchone()["c"]
            rules_count = conn.execute(
                "SELECT COUNT(*) as c FROM scoring_rule_versions"
            ).fetchone()["c"]

        # Synchronize trust and state from persistent pairing record
        if pairing and pairing["status"] == "PAIRED" and not pairing["revoked_at"]:
            st["trust"] = "VERIFIED"
            if st["state"] == ConnectionState.REVOKED.value:
                st["state"] = ConnectionState.CONNECTED.value
                st["active_stage_idx"] = 5
        elif pairing and pairing["status"] == "REVOKED":
            st["trust"] = "REVOKED"
            st["state"] = ConnectionState.REVOKED.value
            st["active_stage_idx"] = 0
        elif pairing and pairing["status"] == "PENDING_APPROVAL":
            st["trust"] = "PENDING_APPROVAL"
            st["state"] = ConnectionState.PAIR_REQUESTED.value
            st["active_stage_idx"] = 2
        elif not dev and not pairing:
            st["trust"] = "UNPAIRED"
            st["state"] = ConnectionState.DISCONNECTED.value
            st["active_stage_idx"] = 0

        # Build pipeline stages with active illuminated state
        active_idx = st["active_stage_idx"]
        stages = []
        for s in PIPELINE_STAGES:
            order = s["order"]
            is_active = (order == active_idx)
            is_completed = (order < active_idx) or (active_idx == 8)
            stages.append({
                "name": s["name"],
                "label": s["label"],
                "icon": s["icon"],
                "order": order,
                "is_active": is_active,
                "is_completed": is_completed,
                "timestamp": st["last_state_change"] if (is_active or is_completed) else "",
            })

        # Calculate relative last sync text
        last_sync_text = "Never"
        if dev and dev["last_seen"]:
            try:
                dt = datetime.strptime(dev["last_seen"], "%Y-%m-%d %H:%M:%S")
                diff_sec = int((datetime.now() - dt).total_seconds())
                if diff_sec < 5:
                    last_sync_text = "Just now"
                elif diff_sec < 60:
                    last_sync_text = f"{diff_sec}s ago"
                elif diff_sec < 3600:
                    last_sync_text = f"{diff_sec // 60}m ago"
                else:
                    last_sync_text = f"{diff_sec // 3600}h ago"
            except Exception:
                last_sync_text = dev["last_seen"]

        verification_code = pairing["verification_code"] if (pairing and pairing["status"] == "PENDING_APPROVAL") else ""

        def device_value(column: str, default: Any = None) -> Any:
            if not dev or column not in dev.keys():
                return default
            value = dev[column]
            return default if value is None else value

        telemetry = {
            "battery_level": int(device_value("battery_level", 0)),
            "is_charging": False,
            "storage_free_mb": int(device_value("storage_free_mb", 0)),
            "wifi_ssid": device_value("wifi_ssid", "") or "",
            "latency_ms": float(device_value("latency_ms", st.get("latency_ms", 0.0))),
            "ip_address": device_value("ip_address", "") or "",
            "protocol_version": device_value("protocol_version", "") or "",
            "config_version": int(device_value("config_version", 0)),
        }

        return {
            "device_id": dev["device_id"] if dev else (device_id or ""),
            "device_name": dev["device_name"] if dev else "No Device Connected",
            "platform": dev["platform"] if dev else "NONE",
            "state": st["state"] if dev else ConnectionState.DISCONNECTED.value,
            "transport": st["transport"] if dev else "NONE",
            "trust": st["trust"] if dev else "UNPAIRED",
            "last_sync": last_sync_text,
            "verification_code": verification_code,
            "stages": stages,
            "telemetry": telemetry,
            "metrics": {
                "tasks_synced": task_count,
                "score_events_synced": score_count,
                "proofs_transferred": proof_count,
                "rules_published": rules_count,
            },
            "in_flight_packets": list(self._in_flight_packets),
            "transfer_queue": self.db.get_transfer_queue_items(device_id=device_id, limit=20) if device_id else [],
            "recent_syncs": self.db.get_connection_history(device_id=device_id, limit=15) if device_id else [],
            "recent_commands": self.db.get_device_commands(device_id=device_id, limit=10) if device_id else [],
        }
