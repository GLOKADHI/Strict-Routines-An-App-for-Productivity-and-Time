import re

def update_ai_robot_admin():
    with open('core/ai_robot_admin.py', 'r', encoding='utf-8') as f:
        text = f.read()

    # 1. Update best_dist <= 4 and best_dist <= 8 in check_duplicate_evidence
    old_block = """                elif best_dist <= 4:
                    res.update({
                        "duplicate_risk": "HIGH",
                        "status": "HIGH_SIMILARITY",
                        "duplicate_status": "HIGH_SIMILARITY",
                        "match_status": "HIGH_SIMILARITY",
                        "matched_evidence_id": eid,
                        "comparison_method": method_used,
                        "threshold": 0.9375,
                        "similarity_score": sim,
                        "explanation": f"High visual similarity to prior evidence ({eid}) (distance={best_dist}, similarity={sim*100:.1f}%).",
                    })
                    return res
                elif best_dist <= 8:
                    if res["duplicate_risk"] == "LOW" or sim > res["similarity_score"]:
                        res.update({
                            "duplicate_risk": "MEDIUM",
                            "status": "POSSIBLE_DUPLICATE",
                            "duplicate_status": "POSSIBLE_DUPLICATE",
                            "match_status": "POSSIBLE_DUPLICATE",
                            "matched_evidence_id": eid,
                            "comparison_method": method_used,
                            "threshold": 0.875,
                            "similarity_score": sim,
                            "explanation": f"Possible visual resemblance to prior evidence ({eid}) (similarity={sim*100:.1f}%).",
                        })"""

    new_block = """                elif best_dist <= 4:
                    # Recurring study desk / laptop / notebook environment; advisory contextual note only.
                    res.update({
                        "duplicate_risk": "LOW",
                        "status": "ENVIRONMENT_SIMILARITY",
                        "duplicate_status": "ENVIRONMENT_SIMILARITY",
                        "match_status": "ENVIRONMENT_SIMILARITY",
                        "matched_evidence_id": eid,
                        "comparison_method": method_used,
                        "threshold": 0.9375,
                        "similarity_score": sim,
                        "explanation": f"Visual resemblance observed with recurring study workstation ({eid}) (distance={best_dist}, similarity={sim*100:.1f}%).",
                    })
                elif best_dist <= 8:
                    if res["duplicate_risk"] == "LOW":
                        res.update({
                            "duplicate_risk": "LOW",
                            "status": "WORKSPACE_SIMILARITY",
                            "duplicate_status": "WORKSPACE_SIMILARITY",
                            "match_status": "WORKSPACE_SIMILARITY",
                            "matched_evidence_id": eid,
                            "comparison_method": method_used,
                            "threshold": 0.875,
                            "similarity_score": sim,
                            "explanation": f"Possible workspace visual resemblance to prior evidence ({eid}) (similarity={sim*100:.1f}%).",
                        })"""

    assert old_block in text, "Could not find dist block in ai_robot_admin.py"
    text = text.replace(old_block, new_block, 1)

    # 2. Update OCR text similarity in check_duplicate_evidence
    old_ocr = """            # 3. Normalized OCR text similarity
            if current_ocr_tokens and past_tokens:
                text_sim = cls.compute_text_similarity(current_ocr_tokens, past_tokens)
                if text_sim >= 0.92:
                    res.update({
                        "duplicate_risk": "HIGH",
                        "status": "HIGH_SIMILARITY",
                        "duplicate_status": "HIGH_SIMILARITY",
                        "match_status": "HIGH_SIMILARITY",
                        "matched_evidence_id": eid,
                        "comparison_method": "OCR_TEXT_JACCARD",
                        "threshold": 0.92,
                        "similarity_score": text_sim,
                        "explanation": f"Extracted OCR text strongly matches prior evidence ({eid}) (similarity={text_sim*100:.1f}%).",
                    })
                    return res
                elif text_sim >= 0.75 and res["duplicate_risk"] == "LOW":
                    res.update({
                        "duplicate_risk": "MEDIUM",
                        "status": "POSSIBLE_DUPLICATE",
                        "duplicate_status": "POSSIBLE_DUPLICATE",
                        "match_status": "POSSIBLE_DUPLICATE",
                        "matched_evidence_id": eid,
                        "comparison_method": "OCR_TEXT_JACCARD",
                        "threshold": 0.75,
                        "similarity_score": text_sim,
                        "explanation": f"Extracted OCR text resembles prior evidence ({eid}) (similarity={text_sim*100:.1f}%).",
                    })"""

    new_ocr = """            # 3. Normalized OCR text similarity: OCR text supports activity relevance;
            # OCR text overlap on study notes/code must NOT independently reject or assign zero points.
            if current_ocr_tokens and past_tokens:
                text_sim = cls.compute_text_similarity(current_ocr_tokens, past_tokens)
                if text_sim >= 0.75:
                    res.update({
                        "duplicate_risk": "LOW",
                        "status": "TOPICAL_OVERLAP",
                        "duplicate_status": "TOPICAL_OVERLAP",
                        "match_status": "TOPICAL_OVERLAP",
                        "matched_evidence_id": eid,
                        "comparison_method": "OCR_TEXT_JACCARD",
                        "threshold": 0.75,
                        "similarity_score": text_sim,
                        "explanation": f"Extracted OCR text reflects continuous topic/coursework matching prior study notes ({eid}) (similarity={text_sim*100:.1f}%).",
                    })"""

    assert old_ocr in text, "Could not find old_ocr in ai_robot_admin.py"
    text = text.replace(old_ocr, new_ocr, 1)

    with open('core/ai_robot_admin.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print('Updated core/ai_robot_admin.py')

def update_ai_analyzer():
    with open('core/ai_analyzer.py', 'r', encoding='utf-8') as f:
        text = f.read()

    old_hash = """        # 1. Cryptographic Hash Duplication Check (Anti-Cheating)
        if sha256_hash and sha256_hash in existing_hashes:
            is_tampered = True
            anti_cheat_signals.append({
                "code": "DUPLICATE_PHOTO_HASH",
                "severity": "SUSPICIOUS",
                "message": "SHA-256 hash matches an already submitted evidence item across the account.",
            })"""

    new_hash = """        # 1. Cryptographic Hash Duplication Check (Anti-Cheating)
        if sha256_hash and sha256_hash in existing_hashes:
            # Advisory warning for validator context; do not independently declare fraudulent
            anti_cheat_signals.append({
                "code": "DUPLICATE_PHOTO_HASH",
                "severity": "WARNING",
                "message": "SHA-256 hash matches an already submitted evidence item across the account; flagged for AI context.",
            })"""

    assert old_hash in text, "Could not find old_hash in ai_analyzer.py"
    text = text.replace(old_hash, new_hash, 1)

    with open('core/ai_analyzer.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print('Updated core/ai_analyzer.py')

if __name__ == '__main__':
    update_ai_robot_admin()
    update_ai_analyzer()
