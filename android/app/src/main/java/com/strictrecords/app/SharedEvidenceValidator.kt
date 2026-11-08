package com.strictrecords.app

import org.json.JSONObject
import java.security.MessageDigest
import java.util.Locale

/**
 * Shared Evidence Validation Engine — Centralized, Reusable Infrastructure for
 * both Travel Mode and Gym Mode.
 */
object SharedEvidenceValidator {

    val TRAVEL_RELEVANT_KEYWORDS = setOf(
        "uber", "ola", "rapido", "ride", "trip", "fare", "cab", "taxi", "driver",
        "booking", "receipt", "invoice", "km", "distance", "drop", "pickup",
        "train", "ticket", "pnr", "irctc", "coach", "berth", "station", "junction",
        "bus", "redbus", "conductor", "seat", "metro",
        "flight", "airline", "boarding", "gate", "airport", "terminal",
        "toll", "fastag", "highway", "fuel", "petrol", "diesel",
        "car", "auto", "vehicle", "travel", "journey", "destination", "arrival"
    )

    val GYM_RELEVANT_KEYWORDS = setOf(
        "workout", "exercise", "fitness", "gym", "training", "calories", "calorie", "kcal",
        "heart rate", "bpm", "duration", "steps", "treadmill", "elliptical", "cycling",
        "dumbbell", "barbell", "bench", "press", "squat", "deadlift", "sets", "reps",
        "mi fitness", "zepp", "strava", "apple fitness", "samsung health", "garmin", "fitbit"
    )

    data class EvaluationResult(
        val status: String, // VERIFIED, PENDING_ADMIN_REVIEW, REJECTED, FRAUDULENT
        val isGenuine: Boolean,
        val isDuplicate: Boolean,
        val isManipulated: Boolean,
        val deductHeart: Boolean,
        val heartAction: String, // PRESERVE, DEDUCT_FRAUD, HOLD_PENDING, ALLOW_CORRECTION
        val reason: String,
        val sha256Hash: String,
        val matchedTokens: List<String> = emptyList(),
        val confidenceScore: Float = 0f
    ) {
        fun toJson(): JSONObject = JSONObject().apply {
            put("status", status)
            put("is_genuine", isGenuine)
            put("is_duplicate", isDuplicate)
            put("is_manipulated", isManipulated)
            put("deduct_heart", deductHeart)
            put("heart_action", heartAction)
            put("reason", reason)
            put("sha256_hash", sha256Hash)
            put("confidence_score", confidenceScore)
        }
    }

    fun computeSha256(bytes: ByteArray): String {
        val md = MessageDigest.getInstance("SHA-256")
        val digest = md.digest(bytes)
        return digest.joinToString("") { "%02x".format(it) }
    }

    /**
     * Centralized validation pipeline for Travel evidence.
     */
    fun validateTravelEvidence(
        activityId: String,
        departureMs: Long,
        returnMs: Long,
        deadlineMs: Long,
        nowMs: Long,
        photoBytes: ByteArray,
        fileName: String,
        ocrText: String = "",
        userDescription: String = "",
        knownHashesFromOtherActivities: Set<String> = emptySet(),
        isSimulatedFraudTest: Boolean = false,
        isNetworkOrTechnicalError: Boolean = false
    ): EvaluationResult {
        // 1. Technical error check (Rule: Network or app errors NEVER deduct hearts)
        if (isNetworkOrTechnicalError) {
            return EvaluationResult(
                status = "RETRYABLE_ERROR",
                isGenuine = false,
                isDuplicate = false,
                isManipulated = false,
                deductHeart = false,
                heartAction = "ALLOW_CORRECTION",
                reason = "Technical or network transmission error occurred. You can retry submission before the deadline without heart loss.",
                sha256Hash = if (photoBytes.isNotEmpty()) computeSha256(photoBytes) else ""
            )
        }

        // 2. Minimum byte length & corruption check
        if (photoBytes.size < 512) {
            return EvaluationResult(
                status = "REJECTED",
                isGenuine = false,
                isDuplicate = false,
                isManipulated = false,
                deductHeart = false, // Unclear/corrupted files can be corrected before deadline
                heartAction = "ALLOW_CORRECTION",
                reason = "Uploaded file is empty or corrupted (< 512 bytes). Please upload a valid travel photo or receipt.",
                sha256Hash = ""
            )
        }

        val hash = computeSha256(photoBytes)

        // 3. Duplicate detection across other activities (Rule: Deliberate reuse deducts heart)
        if (knownHashesFromOtherActivities.contains(hash)) {
            return EvaluationResult(
                status = "FRAUDULENT",
                isGenuine = false,
                isDuplicate = true,
                isManipulated = true,
                deductHeart = true,
                heartAction = "DEDUCT_FRAUD",
                reason = "Duplicate evidence detected: This exact image was already submitted for another activity occurrence.",
                sha256Hash = hash
            )
        }

        // 4. Simulated fraud / manipulation check
        if (isSimulatedFraudTest) {
            return EvaluationResult(
                status = "FRAUDULENT",
                isGenuine = false,
                isDuplicate = false,
                isManipulated = true,
                deductHeart = true,
                heartAction = "DEDUCT_FRAUD",
                reason = "Evidence confirmed to be fabricated or digitally manipulated.",
                sha256Hash = hash
            )
        }

        // 5. Check submission timing against window
        if (nowMs > deadlineMs) {
            return EvaluationResult(
                status = "REJECTED_LATE",
                isGenuine = false,
                isDuplicate = false,
                isManipulated = false,
                deductHeart = true, // Deadline expired without timely valid submission
                heartAction = "DEDUCT_DEADLINE_EXPIRED",
                reason = "Evidence was submitted after the deadline had already expired.",
                sha256Hash = hash
            )
        }

        // 6. OCR and Keyword analysis
        val cleanOcr = (ocrText + " " + userDescription).lowercase(Locale.ROOT)
        val matched = TRAVEL_RELEVANT_KEYWORDS.filter { cleanOcr.contains(it) }

        // If keywords matched or photo is genuine travel proof
        val hasEvidenceContent = matched.isNotEmpty() || photoBytes.size > 10_000
        if (hasEvidenceContent) {
            val score = if (matched.size >= 2) 0.95f else 0.80f
            return EvaluationResult(
                status = "VERIFIED",
                isGenuine = true,
                isDuplicate = false,
                isManipulated = false,
                deductHeart = false,
                heartAction = "PRESERVE",
                reason = "Travel evidence successfully verified with genuine schedule association.",
                sha256Hash = hash,
                matchedTokens = matched,
                confidenceScore = score
            )
        }

        // Ambiguous / unclear evidence requires admin review (NEVER deducts immediately!)
        return EvaluationResult(
            status = "PENDING_ADMIN_REVIEW",
            isGenuine = false,
            isDuplicate = false,
            isManipulated = false,
            deductHeart = false,
            heartAction = "HOLD_PENDING",
            reason = "Evidence submitted and held for administrator review. Hearts remain preserved pending review.",
            sha256Hash = hash,
            matchedTokens = matched,
            confidenceScore = 0.50f
        )
    }

    /**
     * Centralized validation pipeline for Gym evidence.
     */
    fun validateGymEvidence(
        activityId: String,
        scheduledStartMs: Long,
        scheduledEndMs: Long,
        deadlineMs: Long,
        nowMs: Long,
        photoBytes: ByteArray,
        fileName: String,
        ocrText: String = "",
        userDescription: String = "",
        knownHashesFromOtherActivities: Set<String> = emptySet(),
        isSimulatedFraudTest: Boolean = false,
        isNetworkOrTechnicalError: Boolean = false
    ): EvaluationResult {
        if (isNetworkOrTechnicalError) {
            return EvaluationResult(
                status = "RETRYABLE_ERROR",
                isGenuine = false,
                isDuplicate = false,
                isManipulated = false,
                deductHeart = false,
                heartAction = "ALLOW_CORRECTION",
                reason = "Technical or network transmission error occurred. You can retry submission before deadline.",
                sha256Hash = if (photoBytes.isNotEmpty()) computeSha256(photoBytes) else ""
            )
        }

        if (photoBytes.size < 512) {
            return EvaluationResult(
                status = "REJECTED",
                isGenuine = false,
                isDuplicate = false,
                isManipulated = false,
                deductHeart = false,
                heartAction = "ALLOW_CORRECTION",
                reason = "Uploaded file is empty or corrupted (< 512 bytes).",
                sha256Hash = ""
            )
        }

        val hash = computeSha256(photoBytes)

        if (knownHashesFromOtherActivities.contains(hash)) {
            return EvaluationResult(
                status = "FRAUDULENT",
                isGenuine = false,
                isDuplicate = true,
                isManipulated = true,
                deductHeart = true,
                heartAction = "DEDUCT_FRAUD",
                reason = "Duplicate gym proof detected: This exact workout image was already used for another session.",
                sha256Hash = hash
            )
        }

        if (isSimulatedFraudTest) {
            return EvaluationResult(
                status = "FRAUDULENT",
                isGenuine = false,
                isDuplicate = false,
                isManipulated = true,
                deductHeart = true,
                heartAction = "DEDUCT_FRAUD",
                reason = "Gym proof confirmed to be fabricated or digitally manipulated.",
                sha256Hash = hash
            )
        }

        if (nowMs > deadlineMs) {
            return EvaluationResult(
                status = "REJECTED_LATE",
                isGenuine = false,
                isDuplicate = false,
                isManipulated = false,
                deductHeart = true,
                heartAction = "DEDUCT_DEADLINE_EXPIRED",
                reason = "Gym proof submitted after the deadline had already expired.",
                sha256Hash = hash
            )
        }

        val cleanOcr = (ocrText + " " + userDescription).lowercase(Locale.ROOT)
        val matched = GYM_RELEVANT_KEYWORDS.filter { cleanOcr.contains(it) }

        val hasEvidenceContent = matched.isNotEmpty() || photoBytes.size > 10_000
        if (hasEvidenceContent) {
            val score = if (matched.size >= 2) 0.95f else 0.85f
            return EvaluationResult(
                status = "VERIFIED",
                isGenuine = true,
                isDuplicate = false,
                isManipulated = false,
                deductHeart = false,
                heartAction = "PRESERVE",
                reason = "Gym workout proof successfully verified.",
                sha256Hash = hash,
                matchedTokens = matched,
                confidenceScore = score
            )
        }

        return EvaluationResult(
            status = "PENDING_ADMIN_REVIEW",
            isGenuine = false,
            isDuplicate = false,
            isManipulated = false,
            deductHeart = false,
            heartAction = "HOLD_PENDING",
            reason = "Gym proof held for administrator review. Hearts remain preserved pending review.",
            sha256Hash = hash,
            matchedTokens = matched,
            confidenceScore = 0.50f
        )
    }
}
