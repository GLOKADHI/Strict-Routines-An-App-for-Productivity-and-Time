def update_android_taufa():
    path = 'android/app/src/main/java/com/strictrecords/app/TaufaAiValidator.kt'
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    # 1. Update intra-submission in checkDuplicateEvidence
    old_intra = """                    if (hashes[i] == hashes[j]) {
                        return TaufaDuplicateResult(
                            submissionId = cleanSubId,
                            duplicateDetected = true,
                            matchedSubmissionId = cleanSubId,
                            similarityOrConfidence = 1.0,
                            reason = "Duplicate attachment within the same submission: photo #${i + 1} and photo #${j + 1} are identical.",
                            reviewRequired = true,
                            validatorStatus = "DUPLICATE_CONFIRMED",
                            matchType = "INTRA_SUBMISSION"
                        )
                    }"""

    new_intra = """                    if (hashes[i] == hashes[j]) {
                        return TaufaDuplicateResult(
                            submissionId = cleanSubId,
                            duplicateDetected = false,
                            matchedSubmissionId = cleanSubId,
                            similarityOrConfidence = 1.0,
                            reason = "Duplicate attachment within the same submission: photo #${i + 1} and photo #${j + 1} are identical.",
                            reviewRequired = false,
                            validatorStatus = "INTRA_SUBMISSION_NOTICE",
                            matchType = "INTRA_SUBMISSION"
                        )
                    }"""
    assert old_intra in text, "Could not find old_intra in TaufaAiValidator.kt"
    text = text.replace(old_intra, new_intra, 1)

    # 2. Update previous records check in checkDuplicateEvidence
    old_prev = """        for (img in images) {
            val currSha = computeSha256(img)
            for ((prevSubId, prevSha) in historicalSha256s) {
                if (prevSubId != cleanSubId && prevSha.isNotBlank() && prevSha.equals(currSha, ignoreCase = true)) {
                    return TaufaDuplicateResult(
                        submissionId = cleanSubId,
                        duplicateDetected = true,
                        matchedSubmissionId = prevSubId,
                        similarityOrConfidence = 1.0,
                        reason = "Confirmed exact duplicate of previously submitted hourly evidence (Submission: $prevSubId).",
                        reviewRequired = true,
                        validatorStatus = "DUPLICATE_CONFIRMED",
                        matchType = "EXACT"
                    )
                }
            }
        }"""

    new_prev = """        for (img in images) {
            val currSha = computeSha256(img)
            for ((prevSubId, prevSha) in historicalSha256s) {
                if (prevSha.isNotBlank() && prevSha.equals(currSha, ignoreCase = true)) {
                    if (prevSubId == cleanSubId) {
                        return TaufaDuplicateResult(
                            submissionId = cleanSubId,
                            duplicateDetected = false,
                            matchedSubmissionId = cleanSubId,
                            similarityOrConfidence = 1.0,
                            reason = "Identical submission retry recognized; processing as idempotent retry.",
                            reviewRequired = false,
                            validatorStatus = "IDEMPOTENT_RETRY",
                            matchType = "RETRY"
                        )
                    } else {
                        return TaufaDuplicateResult(
                            submissionId = cleanSubId,
                            duplicateDetected = false,
                            matchedSubmissionId = prevSubId,
                            similarityOrConfidence = 1.0,
                            reason = "Photographic file previously submitted for another occurrence (Submission: $prevSubId); queued for contextual validation.",
                            reviewRequired = true,
                            validatorStatus = "EXACT_REUSE_FLAGGED",
                            matchType = "EXACT"
                        )
                    }
                }
            }
        }"""
    assert old_prev in text, "Could not find old_prev in TaufaAiValidator.kt"
    text = text.replace(old_prev, new_prev, 1)

    # 3. Update lines 1024-1039 (remove auto-reject before Taufa)
    old_rej_1 = """            if (dupResult.duplicateDetected) {
                return TaufaValidationResult(
                    submissionId = cleanSubId,
                    score = 0,
                    maxScore = 8,
                    categoryMatch = false,
                    descriptionMatch = false,
                    evidenceMatch = false,
                    evidenceQuality = "INSUFFICIENT",
                    validatorStatus = "REJECTED",
                    reviewStatus = "REJECTED",
                    reason = "Duplicate evidence detected: ${dupResult.reason}",
                    warnings = listOf("DUPLICATE_EVIDENCE_DETECTED"),
                    duplicateResult = dupResult
                )
            }"""
    new_rej_1 = """            // Duplicate heuristics provide contextual metadata only; do not auto-reject before Taufa AI evaluation."""
    assert old_rej_1 in text, "Could not find old_rej_1 in TaufaAiValidator.kt"
    text = text.replace(old_rej_1, new_rej_1, 1)

    # 4. Update lines 1242-1257
    old_rej_2 = """            if (dupResult.duplicateDetected) {
                return TaufaValidationResult(
                    submissionId = cleanSubId,
                    score = 0,
                    maxScore = 8,
                    conversationId = convId,
                    evaluationId = cleanEvalId,
                    evaluationState = STATE_EVALUATION_FAILED,
                    validatorStatus = "REJECTED",
                    reviewStatus = "REJECTED",
                    reason = "Duplicate evidence detected: ${dupResult.reason}",
                    warnings = listOf("DUPLICATE_EVIDENCE_DETECTED"),
                    duplicateResult = dupResult,
                    attachmentsMeta = attachmentsMeta
                )
            }"""
    new_rej_2 = """            // Duplicate heuristics provide contextual metadata only; do not auto-reject before Taufa AI evaluation."""
    assert old_rej_2 in text, "Could not find old_rej_2 in TaufaAiValidator.kt"
    text = text.replace(old_rej_2, new_rej_2, 1)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print('Updated android TaufaAiValidator.kt')

if __name__ == '__main__':
    update_android_taufa()
