"""Temporary Android -> Windows RFCOMM transfer; existing ledger, atomic receipt."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import secrets
import socket
import threading
from core import bluetooth_protocol as wire

logger = logging.getLogger("StrictRecords.BluetoothTransfer")
BT_SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"
DEFAULT_SESSION_TIMEOUT_SECONDS = 180
MAX_TRANSFER_BYTES = wire.MAX_TRANSFER_BYTES


class TransferConflict(ValueError):
    pass


class TransferIntegrity(ValueError):
    pass


class _TransactionDb:
    def __init__(self, db, connection):
        self.db, self.connection = db, connection

    def __getattr__(self, name):
        return getattr(self.db, name)

    @contextmanager
    def _connect(self):
        yield self.connection


class BluetoothTransferManager:
    def __init__(self, db, engine=None, backend_factory=None):
        from windows.bluetooth_rfcomm import WindowsRfcomm
        self.db, self.engine = db, engine
        self.backend_factory = backend_factory or WindowsRfcomm
        self._lock = threading.RLock()
        self._active_session = None
        self._rfcomm_socket = self._client_socket = self._backend = None
        self._listener_thread = self._expiry_timer = None
        self._stop_listener = threading.Event()
        self._last_receipt = None
        try:
            with db._connect() as conn:
                row = conn.execute("SELECT value FROM system_config WHERE key='bluetooth_last_receipt_v2'").fetchone()
                self._last_receipt = json.loads(row[0]) if row else None
        except Exception:
            pass

    @staticmethod
    def is_bluetooth_supported():
        from windows.bluetooth_rfcomm import WindowsRfcomm
        return WindowsRfcomm.capability()

    def get_status(self):
        supported, message = self.backend_factory.capability()
        try:
            peers = self.backend_factory.paired_devices()
        except OSError:
            peers = []
        with self._lock:
            current = self._active_session
            if current and datetime.now(timezone.utc) >= current["expires_at"]:
                self._cleanup_session_locked()
                current = None
            public = {key: current[key] for key in ("session_id", "session_code", "peer_address", "peer_name", "state", "received_bytes", "total_bytes", "metadata_policy")} if current else {}
            return dict(public, available=supported, active_session=bool(current),
                        expires_in=max(0, int((current["expires_at"] - datetime.now(timezone.utc)).total_seconds())) if current else 0,
                        paired_devices=peers, receiver_name=socket.gethostname(), transport="BLUETOOTH_RFCOMM",
                        message=message, last_receipt=self._last_receipt, protocol_version=wire.VERSION)

    def start_session(self, timeout_seconds=180, peer_address="", peer_name="", metadata_policy="STRICT"):
        from windows.bluetooth_rfcomm import normalize_address
        if type(timeout_seconds) is not int or not 30 <= timeout_seconds <= 180:
            return {"success": False, "code": "INVALID_TIMEOUT", "error": "Session duration must be 30–180 seconds"}
        if metadata_policy not in ("STRICT", "QUEUE_RECONCILIATION"):
            return {"success": False, "code": "INVALID_METADATA_POLICY", "error": "Select a supported metadata reconciliation policy"}
        try:
            address = normalize_address(peer_address)
            peers = self.backend_factory.paired_devices()
            peer = next((item for item in peers if item["address"].upper() == address and item.get("paired")), None)
            if not peer:
                raise ValueError("Pair the intended Android sender in Windows Settings, then select it")
            supported, message = self.backend_factory.capability()
            if not supported:
                return {"success": False, "code": "BLUETOOTH_UNAVAILABLE", "error": message}
        except (ValueError, OSError) as error:
            return {"success": False, "code": "PEER_SELECTION_REQUIRED", "error": str(error)}
        self.cancel_session()
        with self._lock:
            try:
                backend = self.backend_factory()
                listener = backend.listen()
            except OSError as error:
                return {"success": False, "code": "RECEIVER_UNAVAILABLE",
                        "error": f"Cannot advertise the Bluetooth receiver. Check the PC adapter and permissions ({error.errno})"}
            stop = threading.Event()
            self._stop_listener = stop
            self._backend, self._rfcomm_socket = backend, listener
            session = {"session_id": secrets.token_hex(16), "session_code": f"{secrets.randbelow(900000)+100000}",
                       "expires_at": datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds),
                       "peer_address": address, "peer_name": peer["name"], "state": "WAITING_FOR_CONNECTION",
                       "received_bytes": 0, "total_bytes": 0, "failures": 0, "metadata_policy": metadata_policy,
                       "account_id": self.db.get_active_user_profile().get("user_id", "user_lok")}
            self._active_session = session
            self._expiry_timer = threading.Timer(timeout_seconds, lambda: self._expire(session["session_id"]))
            self._expiry_timer.daemon = True
            self._expiry_timer.start()
            self._listener_thread = threading.Thread(target=self._listen, args=(listener, session, stop), daemon=True, name="SR-BluetoothTransfer")
            self._listener_thread.start()
            return {"success": True, "session_id": session["session_id"], "session_code": session["session_code"],
                    "expires_in": timeout_seconds, "peer_name": peer["name"], "state": session["state"],
                    "transport": "BLUETOOTH_RFCOMM", "metadata_policy": metadata_policy,
                    "message": "Receiver advertised. Enter this code on the selected Android sender."}

    def _expire(self, session_id):
        with self._lock:
            if self._active_session and self._active_session["session_id"] == session_id:
                self._cleanup_session_locked()

    def cancel_session(self):
        self._stop_listener.set()
        with self._lock:
            self._cleanup_session_locked()
        return {"success": True, "message": "Temporary Bluetooth connection closed"}

    def stop_session(self):
        self.cancel_session()

    def _cleanup_session_locked(self):
        self._stop_listener.set()
        if self._expiry_timer:
            self._expiry_timer.cancel()
            self._expiry_timer = None
        for name in ("_client_socket", "_rfcomm_socket"):
            stream = getattr(self, name)
            setattr(self, name, None)
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass
        if self._backend:
            self._backend.close()
            self._backend = None
        self._active_session = None

    def _listen(self, listener, session, stop):
        while not stop.is_set():
            try:
                stream, peer = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                address = str(peer[0]).upper().replace("-", ":")
                if address != session["peer_address"]:
                    raise ValueError("Connection is not from the selected sender")
                with self._lock:
                    if self._active_session is not session:
                        break
                    self._client_socket = stream
                    session["state"] = "CONNECTED"
                self._handle_client(stream, session, address)
                with self._lock:
                    if self._active_session is session and session["state"] != "COMMITTED":
                        session["state"] = "WAITING_FOR_RETRY"
            except (OSError, ValueError, KeyError, TypeError) as error:
                # Never log codes, authenticators, payloads, or transferred evidence.
                logger.info("Bluetooth transfer did not complete (%s)", type(error).__name__)
                with self._lock:
                    if self._active_session is session:
                        session["failures"] += 1
                        session["state"] = "WAITING_FOR_RETRY"
                        if session["failures"] >= 5:
                            self._cleanup_session_locked()
            finally:
                try:
                    stream.close()
                except OSError:
                    pass
                with self._lock:
                    if self._client_socket is stream:
                        self._client_socket = None

    def _handle_client(self, stream, session, address):
        stream.settimeout(20)
        challenge = secrets.token_hex(24)
        wire.send_frame(stream, {"protocol": wire.PROTOCOL, "version": wire.VERSION, "session_id": session["session_id"],
                                 "challenge": challenge, "receiver_name": socket.gethostname(), **self._hello_extra(session)})
        auth = wire.read_frame(stream)
        if auth.get("protocol") != wire.PROTOCOL or auth.get("version") != wire.VERSION:
            raise ValueError("Unsupported Bluetooth protocol version")
        size, count = auth["payload_length"], auth["record_count"]
        if type(size) is not int or not 0 < size <= MAX_TRANSFER_BYTES or type(count) is not int or not 0 <= count <= wire.MAX_RECORDS:
            raise ValueError("Invalid declared transfer size or record count")
        authentication_key = self._authentication_key(auth, session)
        authentication_text = wire.auth_text(session["session_id"], challenge, auth["package_id"],
                                              auth["device_id"], auth["account_id"], size, count)
        expected = wire.mac(authentication_key, authentication_text)
        if auth.get("session_id") != session["session_id"] or not hmac.compare_digest(str(auth.get("authenticator", "")), expected):
            raise ValueError("Bluetooth session authentication failed")
        with self._lock:
            if self._active_session is not session or auth["account_id"] != session["account_id"]:
                raise ValueError("Transfer session or account changed")
            session["state"] = "AUTHENTICATED"
            session["total_bytes"] = size
        wire.send_frame(stream, {"state": "AUTHENTICATED", "session_id": session["session_id"], "package_id": auth["package_id"],
                                 "receiver_mac": wire.mac(authentication_key, "RECEIVER\n" + authentication_text)})
        def progress(received, total):
            if self._stop_listener.is_set():
                raise ConnectionError("Transfer cancelled")
            with self._lock:
                session["state"] = "TRANSFERRING"
                session["received_bytes"] = received
        raw = wire.read_exact(stream, size, progress).decode("utf-8")
        payload = wire.strict_json(raw)
        receipt = self.import_payload(session["session_code"], payload, auth["package_id"], auth["device_id"], raw,
                                      peer_address=address, session_id=session["session_id"], declared_count=count)
        receipt.update(protocol=wire.PROTOCOL, version=wire.VERSION, session_id=session["session_id"], package_id=auth["package_id"])
        receipt["receipt_mac"] = wire.mac(authentication_key, wire.receipt_text(receipt))
        self._sign_receipt(receipt, authentication_key)
        wire.send_frame(stream, receipt)
        if receipt.get("success"):
            self._expire(session["session_id"])

    def _hello_extra(self, session):
        return {}

    def _authentication_key(self, auth, session):
        return session["session_code"]

    def _sign_receipt(self, receipt, authentication_key):
        pass

    def _authorize_commit(self, conn, session, source_device):
        pass

    def _merge_extra_records(self, conn, payload, session):
        if payload.get('planning_requests'):
            raise ValueError('Planning requests require the registered automatic Bluetooth connection')
        return 0,0,0,{}

    def import_payload(self, session_code, payload_data, payload_hash="", source_device="android_client", raw_payload_str="",
                       *, peer_address="", session_id=None, declared_count=None):
        from core.android_ledger import reconcile
        with self._lock:
            session = self._active_session
            if not session:
                return {"success": False, "code": "NO_ACTIVE_SESSION", "error": "Open a transfer session on the receiving PC"}
            if datetime.now(timezone.utc) >= session["expires_at"]:
                self._cleanup_session_locked()
                return {"success": False, "code": "SESSION_EXPIRED", "error": "Start a new temporary session"}
            if not hmac.compare_digest(str(session_code), session["session_code"]):
                return {"success": False, "code": "AUTHENTICATION_FAILED", "error": "Session code is not valid"}
            if peer_address.upper() != session["peer_address"] or (session_id and session_id != session["session_id"]):
                return {"success": False, "code": "WRONG_PEER", "error": "Select and confirm the intended paired sender"}
            count = 0
            try:
                if not isinstance(payload_data, dict):
                    return {"success": False, "code": "INVALID_SCHEMA", "error": "Transfer package must be a JSON object"}
                if payload_data.get("transfer_version") != wire.VERSION:
                    raise ValueError("Unsupported transfer schema; update both applications")
                raw = raw_payload_str or json.dumps(payload_data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                if not 0 < len(raw.encode("utf-8")) <= MAX_TRANSFER_BYTES:
                    raise ValueError("Payload exceeds the configured transfer limit")
                if not isinstance(payload_hash, str) or not hmac.compare_digest(hashlib.sha256(raw.encode("utf-8")).hexdigest(), payload_hash):
                    raise TransferIntegrity("Payload SHA-256 mismatch")
                if wire.strict_json(raw) != payload_data:
                    raise TransferIntegrity("Raw and parsed payloads disagree")
                if payload_data["device_id"] != source_device or payload_data["account_id"] != session["account_id"]:
                    raise ValueError("Sender identity or account does not match this session")
                for key in ("proof_reviews", "scheduled_activities", "planning_requests"):
                    rows = payload_data.get(key, [])
                    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                        raise ValueError("Invalid metadata collection")
                count = wire.package_count(payload_data)
                if count > wire.MAX_RECORDS or (declared_count is not None and declared_count != count):
                    raise ValueError("Declared record count does not match the package")
                manifest = wire.strict_json(payload_data["ledger_snapshot"]["payload_json"])
                if payload_data["date"] != manifest["date"] or payload_data.get("score_events"):
                    raise ValueError("Transfer must use the single existing ledger manifest")
                with self.db._connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    if self._stop_listener.is_set() or self._active_session is not session:
                        raise ValueError("Session cancelled before commit")
                    self._authorize_commit(conn, session, source_device)
                    self._validate_immutable_events(conn, manifest)
                    self._validate_losses(conn, manifest, session["account_id"])
                    ledger = reconcile(_TransactionDb(self.db, conn), payload_data["ledger_snapshot"], source_device, session["account_id"])
                    if not ledger.get("accepted"):
                        raise TransferConflict(ledger.get("error", "Ledger reconciliation failed"))
                    last = conn.execute("SELECT value FROM system_config WHERE key='bluetooth_last_receipt_v2'").fetchone()
                    prior_package = json.loads(last[0]) if last else {}
                    policy = session.get("metadata_policy", "STRICT")
                    if prior_package.get("package_id") == payload_hash and prior_package.get("metadata_policy", "STRICT") == policy:
                        created, present = 0, count - ledger["event_count"]
                        pending = int(prior_package.get("pending_metadata_count") or 0)
                    else:
                        created, present, pending = self._merge_transfer_metadata(conn, payload_data, manifest, payload_hash, policy)
                    if prior_package.get('package_id')==payload_hash and prior_package.get('metadata_policy','STRICT')==policy:
                        extra_fields={key:prior_package[key] for key in ('planning_results',) if key in prior_package}
                    else:
                        extra_new,extra_known,extra_pending,extra_fields=self._merge_extra_records(conn,payload_data,session)
                        created+=extra_new;present+=extra_known;pending+=extra_pending
                    imported = ledger["new_event_count"] + created
                    known = ledger["event_count"] - ledger["new_event_count"] + present
                    stamp = datetime.now(timezone.utc).isoformat()
                    receipt = {"success": True, "status": "SUCCESS", "package_id": payload_hash,
                               "protocol": wire.PROTOCOL, "version": wire.VERSION, "session_id": session["session_id"],
                               "total_count": count, "transferred_count": imported, "already_present_count": known,
                               "rejected_count": 0, "conflict_count": 0, "confirmed_score": ledger["confirmed_balance"],
                               "metadata_policy": policy, "pending_metadata_count": pending,
                               "applied_record_count": count - pending,
                               "final_score": ledger["confirmed_balance"], "scoring_date": ledger["date"], "synced_at": stamp,
                               "source_device": source_device, "transport": "BLUETOOTH_RFCOMM", "ledger_sha256": ledger["sha256"],
                               "ledger_receipt": {k: v for k, v in ledger.items() if k not in {"transactions", "event_ids", "eligible_points"}},
                               "message": (f"Package received; {pending} metadata records retained pending reconciliation; their claims were not applied"
                                           if pending else "Complete package verified and committed to the existing ledger")}
                    if "points_breakdown" in payload_data and isinstance(payload_data["points_breakdown"], dict):
                        receipt["points_breakdown"] = payload_data["points_breakdown"]
                    receipt.update(extra_fields)
                    conn.execute("INSERT OR REPLACE INTO system_config(key,value,updated_at) VALUES(?,?,?)",
                                 ("bluetooth_last_receipt_v2", json.dumps(receipt), stamp))
                    if self._stop_listener.is_set() or datetime.now(timezone.utc) >= session["expires_at"]:
                        raise ValueError("Transfer cancelled before commit")
                self._last_receipt = receipt
                session["state"] = "COMMITTED"
                if self.engine:
                    try:
                        recalc = self.engine.recalculate_daily_score(ledger["date"], user_id=session["account_id"])
                        if recalc and "breakdown" in recalc:
                            receipt["points_breakdown"] = recalc["breakdown"]
                            self._last_receipt = receipt
                            with self.db._connect() as conn2:
                                conn2.execute("INSERT OR REPLACE INTO system_config(key,value,updated_at) VALUES(?,?,?)",
                                             ("bluetooth_last_receipt_v2", json.dumps(receipt), stamp))
                    except Exception:
                        logger.exception("Score recalculation failed after Bluetooth transfer commit")
                # Reconciliation already provides the authoritative projection. Never replace it with a client total.
                return receipt
            except (ValueError, KeyError, TypeError, OverflowError) as error:
                conflict = isinstance(error, TransferConflict)
                return {"success": False, "status": "REJECTED", "code": "RECONCILIATION_REQUIRED" if conflict else "INTEGRITY_CHECK_FAILED" if isinstance(error, TransferIntegrity) else "INVALID_PACKAGE",
                        "error": str(error), "total_count": count, "transferred_count": 0, "already_present_count": 0,
                        "rejected_count": count, "conflict_count": 1 if conflict else 0}
            except Exception:
                logger.exception("Bluetooth import rolled back")
                return {"success": False, "code": "IMPORT_FAILED", "error": "Import did not commit; retry the original package",
                        "total_count": count, "transferred_count": 0, "rejected_count": count, "conflict_count": 0}

    def _validate_immutable_events(self, conn, manifest):
        known = {row.get("event_id") for row in manifest["score_events"]}
        for event in manifest["score_events"]:
            old = conn.execute("SELECT * FROM score_events WHERE event_id=?", (event.get("event_id"),)).fetchone()
            if old and any(old[key] != event.get(key) for key in ("date", "event_type", "points", "timestamp", "description")):
                raise TransferConflict("Transaction identity has conflicting immutable content")
            audit = event.get("audit") or {}
            parent = event.get("parent_event_id") or (audit.get("parent_event_id") or audit.get("Original_Event_ID") if isinstance(audit, dict) else None)
            if parent:
                if parent not in known and not conn.execute("SELECT 1 FROM score_events WHERE event_id=?", (parent,)).fetchone():
                    raise TransferConflict("Correction references an unavailable original transaction")
                if old and old["parent_event_id"] not in (None, "", parent):
                    raise TransferConflict("Correction parent conflicts with the retained original")

    def _validate_losses(self, conn, manifest, account):
        from core.android_ledger import _read
        prior = _read(conn, account, manifest["date"], bool(manifest.get("is_dev_mode"))) or {}
        for event in manifest["score_events"]:
            if not isinstance(event, dict) or type(event.get("points")) is not int:
                raise ValueError("Invalid ledger transaction")
            if event["points"] >= 0 or event.get("eligible") is not True:
                continue
            if prior.get("eligible_points", {}).get(event["event_id"]) == event["points"]:
                continue
            audit = event.get("audit") or {}
            if isinstance(audit, str):
                try: audit = json.loads(audit)
                except Exception: audit = {}
            if event["event_type"] == "INITIAL_RESET" and audit.get("action") == "INITIAL_POINTS_RESET" and audit.get("reason"):
                continue
            if event["event_type"] in {"SNOOZE", "HOURLY_WASTED", "DAILY_RESET"}:
                continue
            reference = audit.get("proposal_id") if isinstance(audit, dict) else None
            approval = conn.execute("SELECT * FROM deduction_proposals WHERE proposal_id=?", (reference,)).fetchone() if reference else None
            if (not approval or approval["status"] not in ("CONFIRMED", "APPROVED") or
                    not approval["approved_by"] or not approval["approved_at"] or
                    approval["proposed_points"] != event["points"] or approval["date"] != event["date"] or
                    approval["event_type"] != event["event_type"] or
                    (approval["related_task_id"] or "") != (event.get("related_task_id") or "") or
                    approval["finalized_event_id"] not in (None, "", event["event_id"])):
                raise TransferConflict("A negative transaction requires a retained authorized approval reference; a transferred claim is not approval")
            conn.execute("UPDATE deduction_proposals SET finalized_event_id=? WHERE proposal_id=?", (event["event_id"], reference))

    def _merge_transfer_metadata(self, conn, payload, manifest, package_id, policy):
        if policy == "STRICT":
            created, present = self._merge_metadata(conn, payload, manifest)
            return created, present, 0
        created = present = pending = 0
        context = self._metadata_context(conn, manifest)
        for collection, kind, key, table in (
            ("proof_reviews", "BLUETOOTH_PROOF_REVIEW", "evidence_id", "proof_reviews"),
            ("scheduled_activities", "BLUETOOTH_ACTIVITY", "activity_id", "scheduled_activities"),
        ):
            for record in payload.get(collection, []):
                single = dict(payload, proof_reviews=[], scheduled_activities=[])
                single[collection] = [record]
                conn.execute("SAVEPOINT bluetooth_metadata")
                try:
                    new, known = self._merge_metadata(conn, single, manifest, context=context)
                    conn.execute("RELEASE bluetooth_metadata")
                    created += new
                    present += known
                except TransferConflict as error:
                    conn.execute("ROLLBACK TO bluetooth_metadata")
                    conn.execute("RELEASE bluetooth_metadata")
                    # An explicitly selected receiver policy preserves a conflicting
                    # claim in the existing inbox, without approving it or changing
                    # the canonical Windows record. Invalid schema/scope still aborts.
                    identity = wire.identifier(record[key])
                    original = json.dumps(record, sort_keys=True, ensure_ascii=False)
                    stable = "\n".join((payload["account_id"], payload["device_id"], kind, identity, original))
                    conflict_id = "btmeta_" + hashlib.sha256(stable.encode("utf-8")).hexdigest()
                    local = conn.execute(f"SELECT * FROM {table} WHERE {key}=?", (identity,)).fetchone()
                    cursor = conn.execute("""INSERT OR IGNORE INTO sync_conflicts(
                        conflict_id,date,entity_type,entity_id,android_payload_json,windows_payload_json,
                        resolution_policy,resolved_payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (conflict_id, manifest["date"], kind, identity, original,
                         json.dumps(dict(local) if local else {}), "KEEP_WINDOWS_PENDING_RECONCILIATION",
                         json.dumps({"applied": False, "status": "PENDING", "reason": str(error),
                                     "package_id": package_id, "account_id": payload["account_id"],
                                     "device_id": payload["device_id"]}), datetime.now(timezone.utc).isoformat()))
                    created += int(cursor.rowcount == 1)
                    present += int(cursor.rowcount != 1)
                    pending += 1
        return created, present, pending

    def _metadata_context(self, conn, manifest):
        evidence_points = {}
        activity_points = {}
        for event in manifest["score_events"]:
            if event.get("eligible") and event["points"] > 0:
                audit = event.get("audit") or {}
                evidence = event.get("evidence_id") or audit.get("evidence_id")
                activity = event.get("related_task_id") or audit.get("activity_id")
                if evidence:
                    evidence_points[evidence] = evidence_points.get(evidence, 0) + event["points"]
                if activity:
                    activity_points[activity] = activity_points.get(activity, 0) + event["points"]
        confirmed_activities = {e.get("related_task_id") or (e.get("audit") or {}).get("activity_id")
                                for e in manifest["score_events"] if e.get("eligible") and e["points"] > 0}
        for event in manifest["score_events"]:
            audit = event.get("audit") or {}
            parent = event.get("parent_event_id") or audit.get("parent_event_id") or audit.get("Original_Event_ID")
            if parent:
                conn.execute("UPDATE score_events SET parent_event_id=? WHERE event_id=? AND (parent_event_id IS NULL OR parent_event_id='')", (parent, event["event_id"]))
        return evidence_points, activity_points, confirmed_activities

    def _merge_metadata(self, conn, payload, manifest, context=None):
        created = present = 0
        evidence_points, activity_points, confirmed_activities = context if context is not None else self._metadata_context(conn, manifest)
        confirmed_evidence = evidence_points.keys()
        for review in payload.get("proof_reviews", []):
            evidence = wire.identifier(review["evidence_id"])
            status = review.get("status") or "PENDING"
            review_date = review.get("date")
            if review_date in (None, ""):
                # Older Android exports selected this day in SQL but omitted
                # the date from each review. Recover only from its own timestamp;
                # never overwrite an explicitly different reporting date.
                submitted_at = review.get("submitted_at")
                if not isinstance(submitted_at, str) or len(submitted_at) < 19:
                    raise ValueError("Review date missing; update Android and choose Use latest data before retrying")
                try:
                    submitted = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
                except ValueError as error:
                    raise ValueError("Review submission timestamp is invalid") from error
                reporting_zone = timezone(timedelta(hours=5, minutes=30))
                if submitted.tzinfo is None:
                    submitted = submitted.replace(tzinfo=reporting_zone)
                review_date = submitted.astimezone(reporting_zone).date().isoformat()
            if review_date != manifest["date"] or review.get("user_id", payload["account_id"]) != payload["account_id"]:
                raise ValueError("Review account/date scope mismatch")
            old = conn.execute("SELECT * FROM proof_reviews WHERE evidence_id=?", (evidence,)).fetchone()
            if old and (old["user_id"] != payload["account_id"] or old["date"] != review_date):
                raise ValueError("Review account/date scope mismatch")
            if type(review.get("points_awarded", 0)) is not int:
                raise ValueError("Review award must be an integer")
            if status in ("APPROVED", "VERIFIED") and (evidence not in confirmed_evidence or not review.get("reviewed_by")):
                raise TransferConflict("Review metadata cannot approve a proof without its committed approval transaction")
            if status in ("APPROVED", "VERIFIED") and review.get("points_awarded", 0) != evidence_points[evidence]:
                raise TransferConflict("Review award differs from its committed ledger transactions")
            if old:
                if old["status"] != status or int(old["points_awarded"] or 0) != int(review.get("points_awarded") or 0):
                    raise TransferConflict("Existing proof review differs; authorized reconciliation is required")
                present += 1
                continue
            fields = {"review_id": wire.identifier(review.get("review_id") or "bt_" + evidence), "evidence_id": evidence, "user_id": payload["account_id"],
                      "date": review_date, "task_code": review.get("task_code") or "HOURLY",
                      "status": status, "decision": review.get("decision") or status,
                      "points_awarded": int(review.get("points_awarded") or 0), "submitted_at": review.get("submitted_at"),
                      "reviewed_at": review.get("reviewed_at"), "reviewed_by": review.get("reviewed_by"),
                      "signals_json": json.dumps({"activity_id": review.get("activity_id"), "submission_id": review.get("submission_id"),
                                                   "source_device": payload["device_id"], "transport": "BLUETOOTH_RFCOMM"}),
                      "reason_note": "Original Android review metadata received via verified RFCOMM"}
            # Some installations have optional link columns; the base schema stores them in signals_json.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(proof_reviews)")}
            for key in ("activity_id", "submission_id"):
                if key in columns:
                    fields[key] = review.get(key) or ''
            if status not in ("APPROVED", "VERIFIED") and fields["points_awarded"]:
                raise ValueError("Pending/rejected review cannot carry a finalized award")
            conn.execute("INSERT INTO proof_reviews(" + ",".join(fields) + ") VALUES(" + ",".join("?" for _ in fields) + ")", tuple(fields.values()))
            created += 1
        for activity in payload.get("scheduled_activities", []):
            identity = wire.identifier(activity["activity_id"])
            old = conn.execute("SELECT * FROM scheduled_activities WHERE activity_id=?", (identity,)).fetchone()
            if activity.get("date") != manifest["date"]:
                raise ValueError("Activity reporting date mismatch")
            if not old:
                required = ("type", "title", "scheduled_time", "window_start", "window_end", "notification_time", "schedule_id", "schedule_version", "status")
                if any(key not in activity for key in required):
                    raise ValueError("New occurrence is missing authoritative schedule metadata")
                fields = {key: activity[key] for key in required}
                fields.update(activity_id=identity, account_id=payload["account_id"], date=activity["date"], points=0)
                # Importing task metadata does not independently award attendance or points.
                if fields["status"] in ("APPROVED", "VERIFIED", "COMPLETED") and identity not in confirmed_activities:
                    raise TransferConflict("Finalized occurrence needs the existing review/attendance reconciliation flow")
                if fields["status"] in ("APPROVED", "VERIFIED", "COMPLETED"):
                    if activity.get("points", 0) != activity_points[identity]:
                        raise TransferConflict("Occurrence award differs from its committed ledger transactions")
                    fields["points"] = activity_points[identity]
                conn.execute("INSERT INTO scheduled_activities(" + ",".join(fields) + ") VALUES(" + ",".join("?" for _ in fields) + ")", tuple(fields.values()))
                created += 1
            else:
                if old["account_id"] != payload["account_id"]:
                    raise ValueError("Activity account scope mismatch")
                if int(old["schedule_version"] or 0) != int(activity.get("schedule_version") or 0) or any(old[key] != activity.get(key) for key in ("date", "window_start", "window_end", "schedule_id")):
                    raise TransferConflict("Occurrence state/version conflict; newer local task state was preserved")
                if old["status"] != activity.get("status"):
                    if old["status"] in ("APPROVED", "VERIFIED", "COMPLETED", "REJECTED", "CANCELLED") or identity not in confirmed_activities:
                        raise TransferConflict("Existing finalized or pending task state needs authorized reconciliation")
                    if activity.get("points", 0) != activity_points[identity]:
                        raise TransferConflict("Occurrence award differs from its committed ledger transactions")
                    conn.execute("UPDATE scheduled_activities SET status=?,points=? WHERE activity_id=?", (activity["status"], activity_points[identity], identity))
                present += 1
        return created, present
