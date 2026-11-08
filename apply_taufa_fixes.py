import sys

def main():
    with open('core/taufa_ai_validator.py', 'r', encoding='utf-8') as f:
        text = f.read()

    # 1. Update TaufaValidationResult to include standard properties
    old_tvr = """    category: str = "STUDY"
    description: str = ""

    @property
    def is_valid(self) -> bool:"""

    new_tvr = """    category: str = "STUDY"
    description: str = ""
    validator_id: str = "Taufa_AI"
    is_technical_failure: bool = False

    @property
    def approved(self) -> bool:
        return bool(self.qualifies_for_points and (self.score or 0) > 0)

    @property
    def awarded_points(self) -> int:
        return int(self.score or 0) if self.qualifies_for_points else 0

    @property
    def activity_relevance(self) -> str:
        if self.category_match and self.description_match:
            return "HIGH"
        if self.category_match or self.description_match:
            return "MEDIUM"
        return "INSUFFICIENT"

    @property
    def explanation(self) -> str:
        return self.reason or ""

    @property
    def rejection_reason(self) -> str:
        return "" if self.approved else (self.reason or "")

    @property
    def validation_timestamp(self) -> str:
        return self.validated_at or datetime.now(timezone.utc).isoformat()

    @property
    def is_valid(self) -> bool:"""

    assert old_tvr in text, "Could not find old_tvr"
    text = text.replace(old_tvr, new_tvr, 1)

    # 2. Update to_dict() in TaufaValidationResult to output standard fields
    old_to_dict = """        return {
            "submission_id": self.submission_id,
            "score": self.score,"""
    new_to_dict = """        std_status = "TAUFA_APPROVED" if self.approved else (
            "TAUFA_REJECTED" if self.validator_status == "REJECTED" else (
                "TAUFA_TIMEOUT" if "TIMEOUT" in self.warnings else (
                    "TAUFA_API_ERROR" if ("API_ERROR" in self.warnings or any("HTTP_" in str(w) for w in self.warnings)) else (
                        "TAUFA_REVIEW_REQUIRED" if self.validator_status == "REVIEW_REQUIRED" else (
                            "TAUFA_INVALID_RESPONSE" if "MALFORMED_JSON_RESPONSE" in self.warnings else "TAUFA_PENDING"
                        )
                    )
                )
            )
        )
        return {
            "submission_id": self.submission_id,
            "approved": self.approved,
            "awarded_points": self.awarded_points,
            "activity_relevance": self.activity_relevance,
            "validator_id": self.validator_id,
            "explanation": self.explanation,
            "rejection_reason": self.rejection_reason,
            "validation_timestamp": self.validation_timestamp,
            "validation_status": std_status,
            "is_technical_failure": self.is_technical_failure,
            "score": self.score,"""
    assert old_to_dict in text, "Could not find old_to_dict"
    text = text.replace(old_to_dict, new_to_dict, 1)

    # 3. Update as_review in TaufaValidationResult to include standard fields
    old_as_review = """        review = {"submission_id": self.submission_id, "activity_id": activity_id,
            "evidence_id": evidence_id, "decision": decision,"""
    new_as_review = """        review = {"submission_id": self.submission_id, "activity_id": activity_id,
            "evidence_id": evidence_id, "decision": decision,
            "approved": self.approved,
            "awarded_points": self.awarded_points,
            "activity_relevance": self.activity_relevance,
            "validator_id": self.validator_id,
            "is_technical_failure": self.is_technical_failure,"""
    assert old_as_review in text, "Could not find old_as_review"
    text = text.replace(old_as_review, new_as_review, 1)

    # 4. Update check_duplicate_evidence to not falsely reject similar notes / retries
    old_intra = """                    # Exact intra-submission duplicate
                    if img_hashes[i] == img_hashes[j]:
                        return TaufaDuplicateResult(
                            submission_id=clean_sub_id,
                            duplicate_detected=True,
                            matched_submission_id=clean_sub_id,
                            similarity_or_confidence=1.0,
                            reason=f"Duplicate attachment within the same submission: photo #{i+1} and photo #{j+1} are identical.",
                            review_required=True,
                            validator_status="DUPLICATE_CONFIRMED",
                            match_type="INTRA_SUBMISSION",
                        )
                    # Near-duplicate intra-submission
                    if img_dhashes[i] and img_dhashes[j]:
                        dist = self.hamming_distance(img_dhashes[i], img_dhashes[j])
                        if dist <= 2:
                            sim = round(1.0 - (dist / 64.0), 3)
                            return TaufaDuplicateResult(
                                submission_id=clean_sub_id,
                                duplicate_detected=True,
                                matched_submission_id=clean_sub_id,
                                similarity_or_confidence=sim,
                                reason=f"Substantially duplicate attachment within the same submission: photo #{i+1} and photo #{j+1} (similarity {sim}).",
                                review_required=True,
                                validator_status="NEAR_DUPLICATE",
                                match_type="INTRA_SUBMISSION",
                            )"""

    new_intra = """                    # Exact intra-submission duplicate (advisory flag, do not auto-reject entire submission)
                    if img_hashes[i] == img_hashes[j]:
                        return TaufaDuplicateResult(
                            submission_id=clean_sub_id,
                            duplicate_detected=False,
                            matched_submission_id=clean_sub_id,
                            similarity_or_confidence=1.0,
                            reason=f"Duplicate attachment within the same submission: photo #{i+1} and photo #{j+1} are identical.",
                            review_required=False,
                            validator_status="INTRA_SUBMISSION_NOTICE",
                            match_type="INTRA_SUBMISSION",
                        )
                    # Near-duplicate intra-submission (photos of same desk/notebook from slightly different angles)
                    if img_dhashes[i] and img_dhashes[j]:
                        dist = self.hamming_distance(img_dhashes[i], img_dhashes[j])
                        if dist <= 2:
                            sim = round(1.0 - (dist / 64.0), 3)
                            return TaufaDuplicateResult(
                                submission_id=clean_sub_id,
                                duplicate_detected=False,
                                matched_submission_id=clean_sub_id,
                                similarity_or_confidence=sim,
                                reason=f"Similar attachments within the same submission: photo #{i+1} and photo #{j+1} (similarity {sim}). Contextual metadata provided.",
                                review_required=False,
                                validator_status="INTRA_SUBMISSION_SIMILARITY",
                                match_type="INTRA_SUBMISSION",
                            )"""
    assert old_intra in text, "Could not find old_intra"
    text = text.replace(old_intra, new_intra, 1)

    # 5. Update history filter in check_duplicate_evidence for Scenario A (Idempotent retry)
    old_hist_filter = """            rec_sub = str(rec.get("submission_id") or rec.get("related_record_id") or "")
            if rec_sub and rec_sub == clean_sub_id:
                continue
            relevant_history.append(rec)"""

    new_hist_filter = """            rec_sub = str(rec.get("submission_id") or rec.get("related_record_id") or "")
            if rec_sub and rec_sub == clean_sub_id:
                # Scenario A: Duplicate upload retry - recognize same submission identity as idempotent
                return TaufaDuplicateResult(
                    submission_id=clean_sub_id,
                    duplicate_detected=False,
                    matched_submission_id=clean_sub_id,
                    similarity_or_confidence=1.0,
                    reason="Identical submission retry recognized; processing as idempotent retry.",
                    review_required=False,
                    validator_status="IDEMPOTENT_RETRY",
                    match_type="RETRY",
                )
            relevant_history.append(rec)"""
    assert old_hist_filter in text, "Could not find old_hist_filter"
    text = text.replace(old_hist_filter, new_hist_filter, 1)

    # 6. Update exact_match and high_sim_match in check_duplicate_evidence
    old_matches = """        if exact_match:
            return TaufaDuplicateResult(
                submission_id=clean_sub_id,
                duplicate_detected=True,
                matched_submission_id=str(exact_match[0]),
                similarity_or_confidence=1.0,
                reason=f"Confirmed exact duplicate of previously submitted hourly evidence (Submission: {exact_match[0]}).",
                review_required=True,
                validator_status="DUPLICATE_CONFIRMED",
                match_type="EXACT",
            )

        if high_sim_match:
            # Perceptual similarity reflects recurring desk/laptop/notebook environments.
            # Flag for REVIEW_REQUIRED, never automatically reject as fraudulent duplicate.
            return TaufaDuplicateResult(
                submission_id=clean_sub_id,
                duplicate_detected=False,
                matched_submission_id=str(high_sim_match[0]),
                similarity_or_confidence=high_sim_match[1],
                reason=f"Visual resemblance observed with recurring study environment (prior submission {high_sim_match[0]}, similarity {high_sim_match[1]}); queued for review without deduction.",
                review_required=True,
                validator_status="REVIEW_REQUIRED",
                match_type="NEAR_DUPLICATE",
            )

        if ambiguous_match:
            # Ambiguous similarity must not automatically create a point deduction or fraud finding.
            return TaufaDuplicateResult(
                submission_id=clean_sub_id,
                duplicate_detected=False,
                matched_submission_id=str(ambiguous_match[0]),
                similarity_or_confidence=ambiguous_match[1],
                reason=f"Visual similarity detected against previous evidence (Submission: {ambiguous_match[0]}, similarity {ambiguous_match[1]}); flagged for manual review without deduction.",
                review_required=True,
                validator_status="REVIEW_REQUIRED",
                match_type="AMBIGUOUS",
            )"""

    new_matches = """        if exact_match:
            # Scenario D: Exact image reused for a different activity.
            # Flag for contextual evaluation by Taufa AI; do not independently assign zero.
            return TaufaDuplicateResult(
                submission_id=clean_sub_id,
                duplicate_detected=False,
                matched_submission_id=str(exact_match[0]),
                similarity_or_confidence=1.0,
                reason=f"Photographic file previously submitted for another occurrence (Submission: {exact_match[0]}); queued for contextual validation.",
                review_required=True,
                validator_status="EXACT_REUSE_FLAGGED",
                match_type="EXACT",
            )

        if high_sim_match:
            # Scenario B: Similar photographs showing recurring desk/laptop/notebook environment.
            # Do not auto-reject. Pass to Taufa AI with contextual similarity notes.
            return TaufaDuplicateResult(
                submission_id=clean_sub_id,
                duplicate_detected=False,
                matched_submission_id=str(high_sim_match[0]),
                similarity_or_confidence=high_sim_match[1],
                reason=f"Visual resemblance observed with recurring study environment (prior submission {high_sim_match[0]}, similarity {high_sim_match[1]}); passed as context without deduction.",
                review_required=False,
                validator_status="SIMILARITY_NOTED",
                match_type="NEAR_DUPLICATE",
            )

        if ambiguous_match:
            # Ambiguous similarity must not automatically create a point deduction or fraud finding.
            return TaufaDuplicateResult(
                submission_id=clean_sub_id,
                duplicate_detected=False,
                matched_submission_id=str(ambiguous_match[0]),
                similarity_or_confidence=ambiguous_match[1],
                reason=f"Visual similarity detected against previous evidence (Submission: {ambiguous_match[0]}, similarity {ambiguous_match[1]}); flagged for manual review without deduction.",
                review_required=False,
                validator_status="REVIEW_REQUIRED",
                match_type="AMBIGUOUS",
            )"""
    assert old_matches in text, "Could not find old_matches"
    text = text.replace(old_matches, new_matches, 1)

    # 7. In validate_hourly_submission, remove early rejection on dup_res.duplicate_detected
    old_val_dup = """        dup_res = None
        if check_duplicate:
            dup_res = self.check_duplicate_evidence(
                clean_sub_id, category, description, images, historical_records, task_type, timeout
            )
            if dup_res.duplicate_detected:
                return TaufaValidationResult(
                    submission_id=clean_sub_id,
                    score=0,
                    max_score=8,
                    validator_status="REJECTED",
                    reason=f"Duplicate evidence detected: {dup_res.reason}",
                    warnings=["DUPLICATE_EVIDENCE_DETECTED"],
                    duplicate_result=dup_res,
                )"""

    new_val_dup = """        dup_res = None
        if check_duplicate:
            dup_res = self.check_duplicate_evidence(
                clean_sub_id, category, description, images, historical_records, task_type, timeout
            )
            # Duplicate heuristics provide contextual metadata only; OCR and image similarity
            # must not independently reject evidence or assign zero points prior to AI review."""
    assert old_val_dup in text, "Could not find old_val_dup"
    text = text.replace(old_val_dup, new_val_dup, 1)

    # 8. In validate_hourly_submission, handle unconfigured with OpenAI Fallback
    old_unconf = """        if not (self.is_configured() or (taufa_client is not None and taufa_client.has_active_session())):
            logger.info("Taufa AI is unconfigured. Yielding UNCONFIGURED status.")
            return TaufaValidationResult(
                submission_id=clean_sub_id,
                score=None,
                max_score=8,
                validator_status="UNCONFIGURED",
                reason="Official Taufa API access is not configured. Retain this evidence and retry after configuration; no admin review is required for this connection failure.",
                warnings=["TAUFA_AI_UNCONFIGURED"],
                duplicate_result=dup_res,
            )"""

    new_unconf = """        if not (self.is_configured() or (taufa_client is not None and taufa_client.has_active_session())):
            logger.info("Taufa AI is unconfigured. Attempting official OpenAI API fallback.")
            try:
                from core.openai_fallback_validator import OpenAiFallbackValidator
                fb_val = OpenAiFallbackValidator()
                if fb_val.is_configured():
                    fb_res = fb_val.validate_evidence(
                        submission_id=clean_sub_id,
                        category=category,
                        description=description,
                        images=images,
                        image_filenames=image_filenames,
                        ocr_text=None,
                        integrity_warnings=[dup_res.reason] if (dup_res and dup_res.review_required) else [],
                        timeout=timeout,
                    )
                    if not fb_res.is_technical_failure:
                        return TaufaValidationResult(
                            submission_id=clean_sub_id,
                            score=fb_res.awarded_points,
                            max_score=8,
                            category_match=fb_res.activity_relevance in ("HIGH", "MEDIUM"),
                            description_match=fb_res.activity_relevance in ("HIGH", "MEDIUM"),
                            evidence_match=fb_res.evidence_quality in ("EXEMPLARY", "STRONG", "CREDIBLE", "PARTIAL"),
                            evidence_quality=fb_res.evidence_quality,
                            reason=fb_res.explanation,
                            warnings=fb_res.warnings,
                            validator_status="SUCCESS" if fb_res.approved else "REJECTED",
                            review_status="APPROVED" if fb_res.approved else "REJECTED",
                            duplicate_result=dup_res,
                            confidence=fb_res.confidence,
                            validated_at=fb_res.validation_timestamp,
                            category=category,
                            description=description,
                            validator_id="OPENAI_FALLBACK",
                        )
            except Exception as fb_exc:
                logger.warning("OpenAI fallback invocation error: %s", fb_exc)

            return TaufaValidationResult(
                submission_id=clean_sub_id,
                score=0,
                max_score=8,
                validator_status="UNCONFIGURED",
                review_status="UNREVIEWED",
                reason="Official Taufa API and OpenAI fallback access are unconfigured. Retaining as VALIDATION_PENDING.",
                warnings=["TAUFA_AI_UNCONFIGURED", "VALIDATION_PENDING"],
                duplicate_result=dup_res,
                is_technical_failure=True,
            )"""
    assert old_unconf in text, "Could not find old_unconf"
    text = text.replace(old_unconf, new_unconf, 1)

    # 9. Remove lines that force score = 0 when dup_res.duplicate_detected
    old_post_client = """                result.attachments_meta = chat_res.get("attachments", [])
                result.duplicate_result = dup_res
                if dup_res and dup_res.duplicate_detected:
                    result.score = 0
                    result.validator_status = "REJECTED"
                    result.review_status = "REJECTED"
                    result.category_match = False
                    result.evidence_match = False
                return result"""
    new_post_client = """                result.attachments_meta = chat_res.get("attachments", [])
                result.duplicate_result = dup_res
                return result"""
    assert old_post_client in text, "Could not find old_post_client"
    text = text.replace(old_post_client, new_post_client, 1)

    old_post_api = """            result.duplicate_result = dup_res
            if dup_res and dup_res.duplicate_detected:
                result.score = 0
                result.validator_status = "REJECTED"
                result.review_status = "REJECTED"
                result.category_match = False
                result.evidence_match = False
            return result"""
    new_post_api = """            result.duplicate_result = dup_res
            return result"""
    assert old_post_api in text, "Could not find old_post_api"
    text = text.replace(old_post_api, new_post_api, 1)

    # 10. Update exceptions to call OpenAI fallback
    old_exceptions = """        except urllib.error.HTTPError as http_err:
            logger.warning("Taufa AI HTTP error: %s", http_err)
            return TaufaValidationResult(
                submission_id=clean_sub_id,
                score=None,
                validator_status="FAILED_RETRYABLE",
                reason=f"Taufa AI request failed: HTTP {http_err.code} {http_err.reason}",
                warnings=[f"HTTP_{http_err.code}"],
            )
        except (TimeoutError, urllib.error.URLError) as url_err:
            logger.warning("Taufa AI network error: %s", url_err)
            is_timeout = isinstance(url_err, TimeoutError) or "timed out" in str(url_err).lower()
            return TaufaValidationResult(
                submission_id=clean_sub_id,
                score=None,
                validator_status="FAILED_RETRYABLE",
                reason="Taufa AI request timed out." if is_timeout else "Taufa AI endpoint unreachable. Network error.",
                warnings=["TIMEOUT"] if is_timeout else ["NETWORK_UNREACHABLE"],
            )"""

    new_exceptions = """        except (urllib.error.HTTPError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, Exception) as primary_err:
            logger.warning("Taufa AI primary validation failed: %s; initiating OpenAI fallback", primary_err)
            is_timeout = isinstance(primary_err, (TimeoutError, urllib.error.URLError)) and "timed out" in str(primary_err).lower()
            try:
                from core.openai_fallback_validator import OpenAiFallbackValidator
                fb_val = OpenAiFallbackValidator()
                if fb_val.is_configured():
                    fb_res = fb_val.validate_evidence(
                        submission_id=clean_sub_id,
                        category=category,
                        description=description,
                        images=images,
                        image_filenames=image_filenames,
                        ocr_text=None,
                        integrity_warnings=[dup_res.reason] if (dup_res and dup_res.review_required) else [],
                        timeout=timeout,
                    )
                    if not fb_res.is_technical_failure:
                        return TaufaValidationResult(
                            submission_id=clean_sub_id,
                            score=fb_res.awarded_points,
                            max_score=8,
                            category_match=fb_res.activity_relevance in ("HIGH", "MEDIUM"),
                            description_match=fb_res.activity_relevance in ("HIGH", "MEDIUM"),
                            evidence_match=fb_res.evidence_quality in ("EXEMPLARY", "STRONG", "CREDIBLE", "PARTIAL"),
                            evidence_quality=fb_res.evidence_quality,
                            reason=fb_res.explanation,
                            warnings=fb_res.warnings,
                            validator_status="SUCCESS" if fb_res.approved else "REJECTED",
                            review_status="APPROVED" if fb_res.approved else "REJECTED",
                            duplicate_result=dup_res,
                            confidence=fb_res.confidence,
                            validated_at=fb_res.validation_timestamp,
                            category=category,
                            description=description,
                            validator_id="OPENAI_FALLBACK",
                        )
            except Exception as fb_exc:
                logger.warning("OpenAI fallback failed after Taufa failure: %s", fb_exc)

            err_tag = "TIMEOUT" if is_timeout else "API_ERROR"
            return TaufaValidationResult(
                submission_id=clean_sub_id,
                score=0,
                max_score=8,
                validator_status="FAILED_RETRYABLE",
                review_status="UNREVIEWED",
                reason=f"Taufa AI and fallback validation unavailable: {primary_err}",
                warnings=[err_tag, "VALIDATION_PENDING"],
                duplicate_result=dup_res,
                is_technical_failure=True,
            )"""

    assert old_exceptions in text, "Could not find old_exceptions"
    # Find up through except Exception as exc: and replace the whole block
    exc_end = text.find("    @classmethod\n    def parse_and_validate_response(", text.find("except urllib.error.HTTPError as http_err:"))
    assert exc_end != -1, "Could not find end of exception handlers"
    text = text[:text.find("        except urllib.error.HTTPError as http_err:")] + new_exceptions + "\n\n" + text[exc_end:]

    with open('core/taufa_ai_validator.py', 'w', encoding='utf-8') as f:
        f.write(text)

    print('Successfully updated core/taufa_ai_validator.py')

if __name__ == '__main__':
    main()
