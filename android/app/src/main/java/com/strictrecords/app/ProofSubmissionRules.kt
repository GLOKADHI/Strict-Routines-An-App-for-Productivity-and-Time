package com.strictrecords.app

import java.util.Locale

/**
 * Pure submission acceptance and evaluation state machine rules.
 * Governs Stage A (Immediate submission validation & acceptance) and
 * Stage B (Asynchronous evaluation state transitions & idempotency).
 */
object ProofSubmissionRules {

    const val MAX_PROOF_FILE_BYTES: Long = 25L * 1024 * 1024 // 25 MiB
    val SUPPORTED_EXTENSIONS = setOf(".jpg", ".jpeg", ".png", ".webp")
    val SUPPORTED_MIME_TYPES = setOf("image/jpeg", "image/png", "image/webp")

    val HOURLY_ACTIVE_CATEGORIES = listOf("COLLEGE", "STUDY", "EDITING", "VIDEO SHOOT")
    val HISTORICAL_HOURLY_CATEGORIES = setOf("CODING", "PROJECT", "RESEARCH", "EXERCISE", "GYM", "READING", "WORK", "BREAK", "OTHER", "WASTED TIME")
    val ALL_KNOWN_HOURLY_CATEGORIES = (HOURLY_ACTIVE_CATEGORIES + HISTORICAL_HOURLY_CATEGORIES).toSet()

    fun isValidCategory(taskType: String, category: String): Boolean {
        val clean = category.trim().uppercase(Locale.US)
        if (taskType.equals("HOURLY", ignoreCase = true)) {
            return clean in ALL_KNOWN_HOURLY_CATEGORIES
        }
        return false
    }

    object States {
        const val PREPARING_SUBMISSION = "PREPARING_SUBMISSION"
        const val SAVED_LOCALLY_PENDING_UPLOAD = "SAVED_LOCALLY_PENDING_UPLOAD"
        const val UPLOAD_IN_PROGRESS = "UPLOAD_IN_PROGRESS"
        const val SUBMITTED_PENDING_EVALUATION = "SUBMITTED_PENDING_EVALUATION"
        const val EVALUATING = "EVALUATING"
        const val APPROVED = "APPROVED"
        const val REJECTED = "REJECTED"
        const val RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
        const val FLAGGED_FOR_REVIEW = "FLAGGED_FOR_REVIEW"
        const val FINALIZED = "FINALIZED"
    }

    object PipelineStages {
        const val STAGE_1_RECEIVED = 1
        const val STAGE_2_IMAGE_UPLOADED = 2
        const val STAGE_2_QUEUED = 2
        const val STAGE_3_EVIDENCE_PRESERVED = 3
        const val STAGE_3_INPUT_VALIDATION = 3
        const val STAGE_4_LOCAL_CHECKS = 4
        const val STAGE_4_EVIDENCE_EXTRACTION = 4
        const val STAGE_5_DISPATCHED = 5
        const val STAGE_5_AI_ANALYSIS = 5
        const val STAGE_6_RESPONSE_RECEIVED = 6
        const val STAGE_6_MATCHING_CHECKS = 6
        const val STAGE_7_CRITERIA_EVALUATED = 7
        const val STAGE_7_PERSISTENCE = 7
        const val STAGE_8_COMPLETE = 8

        fun getPercent(stage: Int): Int = when (stage) {
            1 -> 12
            2 -> 25
            3 -> 38
            4 -> 50
            5 -> 63
            6 -> 75
            7 -> 88
            8 -> 100
            else -> 0
        }

        fun getStageName(stage: Int): String = when (stage) {
            1 -> "Submission received"
            2 -> "Image uploaded"
            3 -> "Original evidence preserved"
            4 -> "Local evidence checks"
            5 -> "Taufa request dispatched"
            6 -> "Taufa response received"
            7 -> "Criteria evaluated"
            8 -> "Final decision recorded"
            else -> "Pending"
        }
    }

    object StatusLabels {
        const val SUBMITTED = "Submitted"
        const val QUEUED = "Queued"
        const val PROCESSING = "Processing"
        const val AI_ANALYSIS_COMPLETE = "AI analysis complete"
        const val AWAITING_ADMIN_REVIEW = "Awaiting admin/human review"
        const val APPROVED_FULL_POINTS = "Approved — full points"
        const val APPROVED_PARTIAL_POINTS = "Approved — partial points"
        const val REJECTED = "Rejected"
        const val FAILED_RETRY_AVAILABLE = "Failed — retry available"
    }

    data class ValidationResult(
        val valid: Boolean,
        val message: String,
        val errorCode: String = ""
    )

    data class SubmissionReceipt(
        val success: Boolean,
        val message: String,
        val submissionId: String = "",
        val evidenceId: String = "",
        val evaluationState: String = States.SUBMITTED_PENDING_EVALUATION,
        val errorCode: String = ""
    )

    data class EvaluationEvent(
        val taskCategory: String, // HOURLY, GYM, SLEEP, BATH, TRAVEL
        val activityId: String,
        val submissionId: String,
        val status: String,
        val pointsAwarded: Int = 0,
        val message: String = "",
        val stage: Int = 0,
        val stageName: String = "",
        val progressPercent: Int = 0,
        val statusMessage: String = "",
        val statusLabel: String = ""
    )

    /**
     * Minimum validation checks required before Stage A acceptance.
     */
    fun validateSubmissionInput(
        taskType: String,
        hasPhoto: Boolean,
        fileBytes: ByteArray?,
        fileName: String,
        category: String = "",
        timingAllowed: Boolean = true,
        timingMessage: String = "",
        description: String = "",
        imageCount: Int = 1
    ): ValidationResult {
        val cleanType = taskType.uppercase(Locale.US).trim()

        if (!timingAllowed) {
            return ValidationResult(
                false,
                timingMessage.ifBlank { "Submission window is closed or not eligible." },
                "WINDOW_CLOSED"
            )
        }

        // Category & description requirement for Hourly
        if (cleanType == "HOURLY") {
            if (category.isBlank()) {
                return ValidationResult(false, "Select an activity category.", "CATEGORY_REQUIRED")
            }
            if (description.isNotBlank()) {
                val (descOk, descMsg) = TaufaAiValidator.validateDescription(description)
                if (!descOk) {
                    return ValidationResult(false, descMsg, "INVALID_DESCRIPTION")
                }
            }
            if (imageCount > TaufaAiValidator.MAX_HOURLY_PHOTOS) {
                return ValidationResult(
                    false,
                    "Maximum ${TaufaAiValidator.MAX_HOURLY_PHOTOS} photos allowed for one hourly attendance submission.",
                    "EXCEEDED_MAX_ATTACHMENTS"
                )
            }
        }

        // Photo requirements
        val photoMandatory = cleanType in setOf("GYM", "BATH", "TRAVEL", "WAKE_UP", "WAKEUP", "SLEEP")
        if (photoMandatory && (!hasPhoto || fileBytes == null || fileBytes.isEmpty())) {
            return ValidationResult(
                false,
                "A valid photo proof is mandatory for ${cleanType.lowercase().replaceFirstChar { it.uppercase() }}.",
                "ATTACHMENT_REQUIRED"
            )
        }

        // File size check
        if (fileBytes != null && fileBytes.isNotEmpty()) {
            if (fileBytes.size > MAX_PROOF_FILE_BYTES) {
                return ValidationResult(
                    false,
                    "Evidence file exceeds the 25 MB limit.",
                    "FILE_TOO_LARGE"
                )
            }

            // Extension check if filename provided
            if (fileName.isNotBlank()) {
                val ext = "." + fileName.substringAfterLast(".", "").lowercase()
                if (ext.isNotBlank() && ext !in SUPPORTED_EXTENSIONS) {
                    return ValidationResult(
                        false,
                        "Supported evidence formats are JPEG, PNG, and WebP.",
                        "UNSUPPORTED_FILE"
                    )
                }
            }
        }

        return ValidationResult(true, "Validation passed.")
    }

    /**
     * Determines whether an incoming submission request is a duplicate of an in-progress evaluation.
     */
    fun isDuplicateInProgress(existingStatus: String?): Boolean {
        if (existingStatus.isNullOrBlank()) return false
        return existingStatus in setOf(
            States.SUBMITTED_PENDING_EVALUATION,
            States.EVALUATING,
            "VALIDATING",
            "PROCESSING"
        )
    }

    /**
     * Determines if a state transition is permitted.
     */
    fun isValidTransition(current: String, next: String): Boolean {
        if (current == next) return true
        return when (current) {
            States.PREPARING_SUBMISSION -> next in setOf(
                States.SAVED_LOCALLY_PENDING_UPLOAD,
                States.SUBMITTED_PENDING_EVALUATION,
                States.RETRYABLE_FAILURE
            )
            States.SAVED_LOCALLY_PENDING_UPLOAD -> next in setOf(
                States.UPLOAD_IN_PROGRESS,
                States.SUBMITTED_PENDING_EVALUATION,
                States.RETRYABLE_FAILURE
            )
            States.UPLOAD_IN_PROGRESS -> next in setOf(
                States.SUBMITTED_PENDING_EVALUATION,
                States.RETRYABLE_FAILURE
            )
            States.SUBMITTED_PENDING_EVALUATION -> next in setOf(
                States.EVALUATING,
                States.RETRYABLE_FAILURE
            )
            States.EVALUATING -> next in setOf(
                States.APPROVED,
                States.REJECTED,
                States.RETRYABLE_FAILURE,
                States.FINALIZED
            )
            States.RETRYABLE_FAILURE -> next in setOf(
                States.EVALUATING,
                States.SUBMITTED_PENDING_EVALUATION,
                States.REJECTED
            )
            States.APPROVED -> next in setOf(
                States.FINALIZED
            )
            States.REJECTED -> next in setOf(
                States.FINALIZED,
                States.SUBMITTED_PENDING_EVALUATION // On permitted resubmission
            )
            States.FINALIZED -> false // Terminal state for a given submission version
            else -> false
        }
    }

    /**
     * Evaluates whether points can be awarded in the given state.
     * Only APPROVED and FINALIZED with approved outcome may award points.
     * Pending or evaluating states MUST NEVER award points.
     */
    fun canAwardPoints(status: String): Boolean {
        return status == States.APPROVED || status == States.FINALIZED
    }

    /**
     * Generates user-facing acceptance message for Stage A.
     */
    fun acceptanceMessage(taskCategory: String): String {
        return when (taskCategory.uppercase(Locale.US).trim()) {
            "SLEEP" -> "Sleep report submitted — verification pending."
            "GYM" -> "Gym proof submitted — workout analysis pending."
            else -> "Proof submitted successfully — evaluation pending."
        }
    }
}
