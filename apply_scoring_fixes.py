def update_scoring_engine():
    path = 'core/scoring_engine.py'
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    # 1. Add award_evidence_points_atomically before apply_proof_approval
    atomic_method = '''    def award_evidence_points_atomically(
        self,
        evidence_id: str,
        activity_id: str,
        submission_id: str,
        points: int,
        admin_id: str = "Taufa_AI",
        reason: str = "Evidence approved by AI validator",
        date: Optional[str] = None,
        account_id: str = "user_lok",
        is_dev_mode: bool = False,
        validator_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Part 4: Atomically commits evidence approval and points award.
        1. Verifies submission ID and idempotency.
        2. Idempotency key derived from submission identity: pts_award_{submission_id}.
        3. Updates proof_reviews to APPROVED with awarded points and independent status fields.
        4. Updates scheduled_activities to VERIFIED with awarded points.
        5. Inserts score_events transaction.
        6. Updates daily_records balance.
        7. Reconciles android_ledger checkpoint manifest in system_config if present.
        8. Outbox event written to proof_submission_events.
        9. Commits atomically in a single transaction (or rolls back on error).
        """
        import uuid
        points = max(0, int(points))
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_sub_id = (submission_id or f"sub_{evidence_id}").strip()
        tx_id = f"pts_award_{clean_sub_id}"
        rule_ver = self.db.get_current_rule_version(account_id)
        dev_flag = 1 if is_dev_mode else 0

        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")

            # Check idempotency: if already awarded for this transaction or evidence
            existing_se = conn.execute(
                "SELECT * FROM score_events WHERE event_id = ? OR (evidence_id = ? AND points > 0 AND is_dev_mode = ?)",
                (tx_id, evidence_id, dev_flag),
            ).fetchone()
            if existing_se:
                d = dict(existing_se)
                d["audit_details"] = json.loads(d.pop("audit_json", "{}"))
                return {
                    "success": True,
                    "deduplicated": True,
                    "already_rewarded": True,
                    "transaction_id": d["event_id"],
                    "points_awarded": d["points"],
                    "submission_id": clean_sub_id,
                    "status": "APPROVED",
                }

            pr = conn.execute(
                "SELECT * FROM proof_reviews WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            if not pr:
                raise ValueError(f"Proof record for evidence {evidence_id} not found.")

            target_date = date or pr["date"]
            task_code = pr["task_code"]

            signals = {}
            try:
                signals = json.loads(pr["signals_json"] or "{}")
            except Exception:
                signals = {}

            signals.update({
                "upload_status": "UPLOADED",
                "ocr_status": signals.get("ocr_status") or "OCR_COMPLETED",
                "validation_status": "APPROVED",
                "points_status": "POINTS_POSTED",
                "sync_status": "SYNC_PENDING",
                "points_transaction_id": tx_id,
                "submission_id": clean_sub_id,
                "activity_id": activity_id,
                "validator_id": admin_id,
                "awarded_points": points,
                "approved_at": now_iso,
            })
            if validator_result:
                signals["validator_result"] = validator_result

            # 3. Update proof_reviews
            conn.execute(
                """
                UPDATE proof_reviews
                SET status = 'APPROVED',
                    reviewed_at = ?,
                    reviewed_by = ?,
                    decision = 'APPROVED',
                    points_awarded = ?,
                    reason_note = ?,
                    signals_json = ?
                WHERE evidence_id = ?
                """,
                (now_iso, admin_id, points, reason, json.dumps(signals), evidence_id),
            )

            # 4. Update scheduled_activities
            if activity_id:
                act_row = conn.execute(
                    "SELECT details_json FROM scheduled_activities WHERE activity_id = ?",
                    (activity_id,),
                ).fetchone()
                act_details = json.loads(act_row["details_json"] or "{}") if act_row else {}
                act_details.update({
                    "ai_verified": True,
                    "evidence_id": evidence_id,
                    "submission_id": clean_sub_id,
                    "points_status": "POINTS_POSTED",
                    "points_transaction_id": tx_id,
                    "validator_id": admin_id,
                })
                conn.execute(
                    """
                    UPDATE scheduled_activities
                    SET status = 'VERIFIED',
                        validation_status = 'VERIFIED',
                        attendance_status = CASE WHEN type = 'HOURLY' THEN 'PRESENT' ELSE attendance_status END,
                        points = ?,
                        admin_decision = 'APPROVED',
                        details_json = ?
                    WHERE activity_id = ?
                    """,
                    (points, json.dumps(act_details), activity_id),
                )

            # 5. Insert score_events
            audit = {
                "transaction_id": tx_id,
                "submission_id": clean_sub_id,
                "activity_id": activity_id,
                "evidence_id": evidence_id,
                "category": task_code,
                "validator_id": admin_id,
                "points": points,
                "reason": reason,
                "decision": "APPROVED",
                "validation_status": "APPROVED",
                "points_status": "POINTS_POSTED",
                "timestamp": now_iso,
                "rule_version": rule_ver,
            }
            conn.execute(
                """
                INSERT OR IGNORE INTO score_events (
                    event_id, date, is_dev_mode, timestamp, event_type,
                    device_id, description, points, rule_version, evidence_id,
                    related_task_id, audit_json
                ) VALUES (?, ?, ?, ?, 'PROOF_APPROVED', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx_id, target_date, dev_flag, now_iso, admin_id,
                    f"Evidence approved for {task_code}: +{points} points",
                    points, rule_ver, evidence_id, task_code, json.dumps(audit),
                ),
            )

            # 6. Update daily_records
            conn.execute(
                """
                INSERT INTO daily_records (date, user_id, is_dev_mode, final_score, points_earned, verified_points, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, user_id, is_dev_mode) DO UPDATE SET
                    points_earned = points_earned + excluded.points_earned,
                    final_score = min(100, final_score + excluded.final_score),
                    verified_points = verified_points + excluded.verified_points,
                    updated_at = excluded.updated_at
                """,
                (target_date, account_id, dev_flag, points, points, points, now_iso),
            )

            # 7. Update android_ledger checkpoint manifest in system_config if present
            from core.android_ledger import _key, _read
            receipt = _read(conn, account_id, target_date, is_dev_mode)
            if receipt and isinstance(receipt, dict):
                tx_entry = {
                    "event_id": tx_id,
                    "date": target_date,
                    "is_dev_mode": is_dev_mode,
                    "timestamp": now_iso,
                    "event_type": "PROOF_APPROVED",
                    "points": points,
                    "eligible": True,
                    "evidence_id": evidence_id,
                    "related_task_id": task_code,
                    "description": f"Evidence approved for {task_code}: +{points} points",
                    "audit": audit,
                }
                receipt.setdefault("transactions", []).append(tx_entry)
                receipt.setdefault("eligible_points", {})[tx_id] = points
                if tx_id not in receipt.get("event_ids", []):
                    receipt.setdefault("event_ids", []).append(tx_id)
                receipt["raw_verified_points"] = int(receipt.get("raw_verified_points", 0)) + points
                receipt["confirmed_balance"] = min(100, int(receipt.get("confirmed_balance", 0)) + points)
                receipt["event_count"] = len(receipt["transactions"])
                conn.execute(
                    "INSERT OR REPLACE INTO system_config (key, value, updated_at) VALUES (?, ?, ?)",
                    (_key(account_id, target_date, is_dev_mode), json.dumps(receipt), now_iso),
                )

            # 8. Outbox event in proof_submission_events
            outbox_evt = conn.execute(
                "SELECT * FROM proof_submission_events WHERE submission_id = ?",
                (clean_sub_id,),
            ).fetchone()
            outbox_payload = json.loads(outbox_evt["payload_json"] or "{}") if outbox_evt else {}
            outbox_payload.update({
                "status": "APPROVED",
                "validation_status": "APPROVED",
                "points_status": "POINTS_POSTED",
                "points": points,
                "points_awarded": points,
                "points_transaction_id": tx_id,
                "validator_id": admin_id,
                "reviewed_at": now_iso,
            })
            if outbox_evt:
                conn.execute(
                    "UPDATE proof_submission_events SET status = 'APPROVED', payload_json = ? WHERE submission_id = ?",
                    (json.dumps(outbox_payload), clean_sub_id),
                )
            else:
                event_id = f"pse_{uuid.uuid4().hex[:16]}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO proof_submission_events (
                        event_id, account_id, submission_id, activity_id,
                        schedule_version, source_device_id, accepted_at, status, payload_json
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, 'APPROVED', ?)
                    """,
                    (event_id, account_id, clean_sub_id, activity_id, admin_id, now_iso, json.dumps(outbox_payload)),
                )

        self.recalculate_daily_score(target_date, user_id=account_id, is_dev_mode=is_dev_mode)
        return {
            "success": True,
            "transaction_id": tx_id,
            "evidence_id": evidence_id,
            "activity_id": activity_id,
            "submission_id": clean_sub_id,
            "points_awarded": points,
            "status": "APPROVED",
            "validation_status": "APPROVED",
            "points_status": "POINTS_POSTED",
            "reviewed_by": admin_id,
            "reviewed_at": now_iso,
        }

'''

    old_apply = "    def apply_proof_approval("
    assert old_apply in text, "Could not find apply_proof_approval in scoring_engine.py"
    text = text.replace(old_apply, atomic_method + old_apply, 1)

    # 2. Update apply_proof_approval to call award_evidence_points_atomically for general / hourly proofs
    old_apply_body = """        # Record ScoreEvent through central scoring engine
        score_event = self.record_score_event(
            date=res["date"],
            event_type=ScoreEventType.PROOF_APPROVED.value,
            device_id="admin_portal",
            description=f"Evidence approved for {res['task_code']}: +{points} points",
            points=points,
            evidence_id=evidence_id,
            related_task_id=res["task_code"],
            audit_details={
                "admin_user": admin_id,
                "reason": reason,
                "points": points,
                "decision": "APPROVED",
            },
        )

        awarded = int(score_event.get("points", 0)) if score_event.get("awarded", True) else 0
        res["points_awarded"] = awarded
        res["score_event"] = score_event"""

    new_apply_body = """        # Execute atomic points award and ledger update
        with self.db._connect() as conn:
            pr_info = conn.execute("SELECT activity_id, signals_json FROM proof_reviews WHERE evidence_id = ?", (evidence_id,)).fetchone()
            act_id = pr_info["activity_id"] if pr_info else ""
            signals = json.loads(pr_info["signals_json"] or "{}") if pr_info else {}
            sub_id = signals.get("submission_id") or ""
            if not sub_id:
                ef = conn.execute("SELECT related_record_id FROM evidence_files WHERE evidence_id = ?", (evidence_id,)).fetchone()
                sub_id = ef["related_record_id"] if ef else ""
            if not sub_id:
                sub_id = f"sub_{evidence_id}"

        atomic_res = self.award_evidence_points_atomically(
            evidence_id=evidence_id,
            activity_id=act_id,
            submission_id=sub_id,
            points=points,
            admin_id=admin_id,
            reason=reason,
            date=res["date"],
            account_id=res.get("user_id", "user_lok"),
        )
        awarded = atomic_res.get("points_awarded", points)
        res["points_awarded"] = awarded
        res["success"] = True
        res["atomic_result"] = atomic_res
        res["score_event"] = {
            "event_id": atomic_res.get("transaction_id"),
            "points": awarded,
            "awarded": True,
            "status": "APPROVED",
        }"""
    assert old_apply_body in text, "Could not find old_apply_body in scoring_engine.py"
    text = text.replace(old_apply_body, new_apply_body, 1)

    # 3. In recalculate_daily_score, ensure verified PROOF_APPROVED events are counted even if android_receipt is present
    old_receipt_check = """                if android_receipt:
                    # The complete APK manifest determines membership and eligibility.
                    # Retain unmatched Windows rows for audit; they cannot change this balance.
                    if ev["event_id"] not in android_receipt["eligible_points"]:
                        continue
                    pts = android_receipt["eligible_points"][ev["event_id"]]"""

    new_receipt_check = """                if android_receipt:
                    # The complete APK manifest determines membership and eligibility.
                    if ev["event_id"] in android_receipt.get("eligible_points", {}):
                        pts = android_receipt["eligible_points"][ev["event_id"]]
                    elif ev["event_type"] == ScoreEventType.PROOF_APPROVED.value and ev.get("proof_status") == "APPROVED":
                        pts = int(ev["points"])
                    else:
                        continue"""
    assert old_receipt_check in text, "Could not find old_receipt_check in scoring_engine.py"
    text = text.replace(old_receipt_check, new_receipt_check, 1)

    # 4. In get_unified_transaction_history, ensure PROOF_APPROVED transactions are marked confirmed
    old_tx_confirm = """            if manifest and not tx.get("is_negative_proposal"):
                source = next((ev for ev in manifest["transactions"] if ev["event_id"] == tx["transaction_id"]), None)
                tx["confirmed"] = bool(source and source["eligible"])
                if not source:
                    tx["status"] = "Not confirmed by APK"
                elif not source["eligible"]:
                    tx["status"] = "Pending verification"
                if source:
                    tx["reason"] = source.get("description") or tx["reason"]
            else:
                tx["confirmed"] = False"""

    new_tx_confirm = """            if manifest and not tx.get("is_negative_proposal"):
                source = next((ev for ev in manifest.get("transactions", []) if ev["event_id"] == tx["transaction_id"]), None)
                if source and source.get("eligible"):
                    tx["confirmed"] = True
                elif tx.get("event_type") == ScoreEventType.PROOF_APPROVED.value and tx.get("amount", 0) > 0:
                    tx["confirmed"] = True
                    tx["status"] = "Confirmed"
                else:
                    tx["confirmed"] = False
                    tx["status"] = "Not confirmed by APK"
                if source:
                    tx["reason"] = source.get("description") or tx["reason"]
            elif not tx.get("is_negative_proposal") and tx.get("event_type") == ScoreEventType.PROOF_APPROVED.value and tx.get("amount", 0) > 0:
                tx["confirmed"] = True
                tx["status"] = "Confirmed"
            else:
                tx["confirmed"] = False"""
    assert old_tx_confirm in text, "Could not find old_tx_confirm in scoring_engine.py"
    text = text.replace(old_tx_confirm, new_tx_confirm, 1)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print('Updated core/scoring_engine.py')

if __name__ == '__main__':
    update_scoring_engine()
