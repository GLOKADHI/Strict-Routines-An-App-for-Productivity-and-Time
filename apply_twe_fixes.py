def update_time_window_engine():
    path = 'core/time_window_engine.py'
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    old_block = """            preserved_sub = row["submitted_at"]
            default_pts = int(row["points"] or 0)
            valid_submission = False
            try:
                valid_submission = bool(
                    preserved_sub
                    and iso_to_dt(row["window_start"]) <= iso_to_dt(preserved_sub) < iso_to_dt(row["window_end"])
                    and row["status"] in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL")
                )
            except ValueError:
                valid_submission = False

            accepted_verification = bool(accepted_verification and valid_submission)
            if is_verified and not valid_submission:
                return {
                    "activity_id": activity_id,
                    "status": row["status"],
                    "points": 0,
                    "verified": False,
                    "message": "No valid on-time submission exists; activity state was not changed.",
                }
            if not is_verified and row["status"] not in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL"):
                return {
                    "activity_id": activity_id,
                    "status": row["status"],
                    "points": 0,
                    "verified": False,
                    "message": "Activity is not awaiting review; state was not changed.",
                }"""

    new_block = """            preserved_sub = row["submitted_at"]
            default_pts = int(row["points"] or 0)
            valid_submission = False
            try:
                valid_submission = bool(
                    proof_approved
                    or (
                        preserved_sub
                        and iso_to_dt(row["window_start"]) <= iso_to_dt(preserved_sub) <= (iso_to_dt(row["window_end"]) + timedelta(minutes=15))
                    )
                    or (act_type == "HOURLY" and (row["status"] in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "AI_ANALYZING", "SCHEDULED", "PENDING_REVIEW", "UNDER_REVIEW", "AI_QUEUED") or bool(preserved_sub)))
                )
            except (ValueError, TypeError):
                valid_submission = bool(proof_approved or (act_type == "HOURLY" and bool(preserved_sub)))

            accepted_verification = bool(accepted_verification and valid_submission)
            if is_verified and not valid_submission:
                return {
                    "activity_id": activity_id,
                    "status": row["status"],
                    "points": 0,
                    "verified": False,
                    "message": "No valid on-time submission exists; activity state was not changed.",
                }
            if not is_verified and row["status"] not in ("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "AI_ANALYZING", "SCHEDULED", "PENDING_REVIEW", "UNDER_REVIEW", "AI_QUEUED"):
                return {
                    "activity_id": activity_id,
                    "status": row["status"],
                    "points": 0,
                    "verified": False,
                    "message": "Activity is not awaiting review; state was not changed.",
                }"""

    assert old_block in text, "Could not find old_block in time_window_engine.py"
    text = text.replace(old_block, new_block, 1)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print('Updated core/time_window_engine.py')

if __name__ == '__main__':
    update_time_window_engine()
