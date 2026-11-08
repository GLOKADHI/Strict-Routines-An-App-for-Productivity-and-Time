def update_server():
    path = 'core/server.py'
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    old_block = """                    validation = ai_rev.get("decision") or "NEEDS_REVIEW"
                    reviewer_id = "Taufa_AI" if taufa_handled else "StrictBot_AI"
                    is_hourly = str(accepted.get("task_type") or "HOURLY").strip().upper() == "HOURLY"
                    if is_hourly:
                        if not taufa_handled or validation != "PROVISIONAL_APPROVE":
                            validation = ai_rev.get("decision") or "NEEDS_REVIEW"
                            pts = 0
                        else:
                            cat_ok = bool(ai_rev.get("category_match", False))
                            desc_ok = bool(ai_rev.get("description_match", False))
                            ev_ok = bool(ai_rev.get("evidence_match", False))
                            qualifies = bool(ai_rev.get("qualifies_for_points", False))
                            score_val = int(ai_rev.get("proposed_points") or 0)
                            if not qualifies or not cat_ok or not desc_ok or not ev_ok or score_val <= 0:
                                validation = "REJECTED"
                                pts = 0
                            else:
                                pts = score_val"""

    new_block = """                    validation = ai_rev.get("decision") or "NEEDS_REVIEW"
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
                            pts = 0"""

    assert old_block in text, "Could not find old_block in server.py"
    text = text.replace(old_block, new_block, 1)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print('Updated core/server.py')

if __name__ == '__main__':
    update_server()
