def update_hourly_admin():
    path = 'core/hourly_admin_service.py'
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    old_block = """            items.append({
                "activity_id": act["activity_id"],
                "occurrence_id": act["activity_id"],
                "date": target_date,
                "hour_slot": hour_slot,
                "session_label": session_label,
                "window_start": act.get("window_start"),
                "window_end": act.get("window_end"),
                "submission_window_label": window_label,
                "submission_id": submission_id,
                "category": cat,
                "description": desc,
                "submitted_at": submitted_at,
                "review_status": review_status,
                "raw_status": raw_status,
                "points_awarded": confirmed_points,
                "configured_limit": self.get_configured_hourly_limit(),
                "has_actual_submission": has_submission,
                "evidence": evidence_info,
                "review": review_info,
                "source_platform": details.get("source_platform") or "ANDROID",
                "source_device_id": details.get("source_device_id") or legacy_hr.get("device_id") or "",
                "pending_sync": is_pending_sync,
            })"""

    new_block = """            sig = (review_info.get("signals") or {}) if review_info else {}
            u_status = sig.get("upload_status") or ("UPLOADED" if has_submission else "UPLOAD_PENDING")
            ocr_meta = (evidence_info.get("metadata") or {}) if evidence_info else {}
            o_status = sig.get("ocr_status") or ocr_meta.get("ocr_status") or ("OCR_COMPLETED" if (ocr_meta.get("ocr_text") or sig.get("ocr_text")) else ("OCR_PENDING" if has_submission else "OCR_PENDING"))
            v_status = sig.get("validation_status") or (
                "APPROVED" if review_status == "APPROVED" else (
                    "REJECTED" if review_status == "REJECTED" else (
                        "REVIEW_REQUIRED" if review_status in ("NEEDS_REVIEW", "REVIEW_REQUIRED") else (
                            "PROVIDER_UNAVAILABLE" if sig.get("is_technical_failure") else "VALIDATION_PENDING"
                        )
                    )
                )
            )
            p_status = sig.get("points_status") or (
                "POINTS_POSTED" if (confirmed_points > 0 or review_status in ("APPROVED", "REJECTED")) else "POINTS_PENDING"
            )
            s_status = sig.get("sync_status") or (
                "SYNC_PENDING" if is_pending_sync else ("SYNCED_ANDROID" if (details.get("source_platform") == "ANDROID") else "SYNCED_WINDOWS")
            )

            items.append({
                "activity_id": act["activity_id"],
                "occurrence_id": act["activity_id"],
                "date": target_date,
                "hour_slot": hour_slot,
                "session_label": session_label,
                "window_start": act.get("window_start"),
                "window_end": act.get("window_end"),
                "submission_window_label": window_label,
                "submission_id": submission_id,
                "category": cat,
                "description": desc,
                "submitted_at": submitted_at,
                "review_status": review_status,
                "raw_status": raw_status,
                "points_awarded": confirmed_points,
                "configured_limit": self.get_configured_hourly_limit(),
                "has_actual_submission": has_submission,
                "evidence": evidence_info,
                "review": review_info,
                "source_platform": details.get("source_platform") or "ANDROID",
                "source_device_id": details.get("source_device_id") or legacy_hr.get("device_id") or "",
                "pending_sync": is_pending_sync,
                "upload_status": u_status,
                "ocr_status": o_status,
                "validation_status": v_status,
                "points_status": p_status,
                "sync_status": s_status,
            })"""

    assert old_block in text, "Could not find old_block in hourly_admin_service.py"
    text = text.replace(old_block, new_block, 1)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print('Updated core/hourly_admin_service.py')

if __name__ == '__main__':
    update_hourly_admin()
