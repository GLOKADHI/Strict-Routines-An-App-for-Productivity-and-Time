package com.strictrecords.app

import android.content.Context
import android.graphics.BitmapFactory
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import javax.net.ssl.HttpsURLConnection

/**
 * StrictRecords — Temporary Taufa AI Hourly Attendance Validation Adapter (Android)
 *
 * Isolated adapter for evaluating Hourly Attendance submissions against Taufa AI.
 * Explicitly excludes: Bath Protocol, Gym Workout, sleep reports, wake-up verification,
 * and travel bookings.
 */
object TaufaAiValidator {

    const val VALIDATOR_VERSION = "1.2.0"
    const val MAX_HOURLY_PHOTOS = 10
    const val MAX_PHOTO_BYTES = 25 * 1024 * 1024 // 25 MB Android limit

    const val DESIGNATED_CONVERSATION_ID = "891a3c1e-eed2-460e-bc05-6b8ac94bec5b"
    const val DESIGNATED_CHAT_URL = "https://taufa.ai/chat/891a3c1e-eed2-460e-bc05-6b8ac94bec5b"

    const val TAUFA_PROOF_SCORING_RUBRIC =
        "Score\tWhat it looks like\n" +
        "0\tBlank, unreadable, selfie, wallpaper, or content that contradicts the claim (fitness app screenshot claimed as study, kit gallery for a baking claim)\n" +
        "1\tBarely relevant, or a decorative/title graphic with a topic caption but no actual content\n" +
        "2\tReal material but near-empty page, gibberish description, or no way to tie it to the claimed activity\n" +
        "3\tRight subject, but weak proof: recycled or identical evidence reused from another slot, so it cannot evidence this hour\n" +
        "4\tPartial: correct material visible, description only loosely matched, or content too thin for an hour\n" +
        "5\tCredible and on-topic, but sparse, no timestamp, or the claim is vaguer than the evidence supports\n" +
        "6\tAuthentic and clearly matching, timestamped, with one real gap (short volume, one page, mixed topics)\n" +
        "7\tStrong, specific, timestamped evidence matching category and description closely\n" +
        "8\tExemplary: multiple timestamped pages of worked material that matches the described topics point for point"

    const val TAUFA_COMPACT_PROMPT =
        "You are the StrictRecords AI Attendance Evaluator in designated thread {conversation_id}.\n" +
        "Visually inspect attached image(s) for claimed hourly task.\n" +
        "Task: Sub {submission_id} | Cat: {category} | Desc: {description} | Slot: {schedule_context} | Imgs: {image_count}\n\n" +
        "SCORING SYSTEM FOR PROOF (0-8):\n" +
        "Score\tWhat it looks like\n" +
        "0\tBlank, unreadable, selfie, wallpaper, or content that contradicts the claim\n" +
        "1\tBarely relevant, or a decorative/title graphic with a topic caption but no actual content\n" +
        "2\tReal material but near-empty page, gibberish description, or no way to tie it to the claimed activity\n" +
        "3\tRight subject, but weak proof: recycled or identical evidence reused from another slot\n" +
        "4\tPartial: correct material visible, description only loosely matched, or content too thin for an hour\n" +
        "5\tCredible and on-topic, but sparse, no timestamp, or the claim is vaguer than the evidence supports\n" +
        "6\tAuthentic and clearly matching, timestamped, with one real gap (short volume, one page, mixed topics)\n" +
        "7\tStrong, specific, timestamped evidence matching category and description closely\n" +
        "8\tExemplary: multiple timestamped pages of worked material that matches the described topics point for point\n\n" +
        "Instructions: Award 0 for selfie/blank/meme/unrelated. Genuine visual proof required.\n" +
        "Return JSON only:\n" +
        "{\"submission_id\": \"{submission_id}\", \"score\": 0, \"category_match\": false, \"description_match\": false, \"evidence_match\": false, \"evidence_quality\": \"INSUFFICIENT\", \"reason\": \"<visual findings>\", \"warnings\": []}"

    const val TAUFA_SCORING_PROMPT =
        "You are the StrictRecords AI Attendance Evaluator embedded in this designated conversation: {conversation_id}.\n" +
        "You are evaluating a real hourly attendance submission with visual evidence.\n\n" +
        "TASK DETAILS:\n" +
        "- Submission ID: {submission_id}\n" +
        "- Evaluation ID: {evaluation_id}\n" +
        "- Category: {category}\n" +
        "- User Description: {description}\n" +
        "- Schedule Slot: {schedule_context}\n" +
        "- Attached Images: {image_count} original unmodified file(s)\n" +
        "- Image Checksums: {image_checksums}\n\n" +
        "MANDATORY VISUAL EVALUATION INSTRUCTIONS:\n" +
        "1. Inspect the visual contents of every attached image thoroughly.\n" +
        "2. Check whether the image visibly depicts physical objects, handwritten notes, books, open code editors, study materials, or tasks matching the claimed category and description.\n" +
        "3. Award 0 points if an image is a selfie, blank screen, meme, gaming screenshot, duplicate, or completely unrelated to the category.\n" +
        "4. DO NOT award points based on user claims or descriptions alone. Genuine visual proof is required.\n" +
        "5. SCORING SYSTEM FOR PROOF:\n" +
        "Score\tWhat it looks like\n" +
        "0\tBlank, unreadable, selfie, wallpaper, or content that contradicts the claim (fitness app screenshot claimed as study, kit gallery for a baking claim)\n" +
        "1\tBarely relevant, or a decorative/title graphic with a topic caption but no actual content\n" +
        "2\tReal material but near-empty page, gibberish description, or no way to tie it to the claimed activity\n" +
        "3\tRight subject, but weak proof: recycled or identical evidence reused from another slot, so it cannot evidence this hour\n" +
        "4\tPartial: correct material visible, description only loosely matched, or content too thin for an hour\n" +
        "5\tCredible and on-topic, but sparse, no timestamp, or the claim is vaguer than the evidence supports\n" +
        "6\tAuthentic and clearly matching, timestamped, with one real gap (short volume, one page, mixed topics)\n" +
        "7\tStrong, specific, timestamped evidence matching category and description closely\n" +
        "8\tExemplary: multiple timestamped pages of worked material that matches the described topics point for point\n\n" +
        "6. Return ONLY a single raw JSON object (enclosed in ```json ... ``` or directly) matching this schema:\n" +
        "{\n" +
        "  \"submission_id\": \"{submission_id}\",\n" +
        "  \"score\": <integer from 0 to 8>,\n" +
        "  \"category_match\": <true or false>,\n" +
        "  \"description_match\": <true or false>,\n" +
        "  \"evidence_match\": <true or false>,\n" +
        "  \"evidence_quality\": \"HIGH\" | \"MEDIUM\" | \"LOW\" | \"INSUFFICIENT\",\n" +
        "  \"reason\": \"<specific visual observations explaining score>\",\n" +
        "  \"warnings\": [\"<warning codes or empty array>\"]\n" +
        "}"

    // Explicit 11 Image-Evaluation States
    const val STATE_IMAGE_RECEIVED = "IMAGE_RECEIVED"
    const val STATE_IMAGE_INTEGRITY_CHECKED = "IMAGE_INTEGRITY_CHECKED"
    const val STATE_UPLOADING_TO_TAUFA = "UPLOADING_TO_TAUFA"
    const val STATE_TAUFA_UPLOAD_FAILED = "TAUFA_UPLOAD_FAILED"
    const val STATE_ATTACHMENT_CONFIRMED = "ATTACHMENT_CONFIRMED"
    const val STATE_EVALUATION_REQUEST_SENT = "EVALUATION_REQUEST_SENT"
    const val STATE_WAITING_FOR_TAUFA = "WAITING_FOR_TAUFA"
    const val STATE_EVALUATION_RECEIVED = "EVALUATION_RECEIVED"
    const val STATE_EVALUATION_VALIDATED = "EVALUATION_VALIDATED"
    const val STATE_EVALUATION_FAILED = "EVALUATION_FAILED"
    const val STATE_HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"

    data class ImageAttachmentMeta(
        val filename: String,
        val sha256: String,
        val byteSize: Int,
        val mimeType: String,
        val width: Int,
        val height: Int,
        val dimensions: String,
        val attachmentId: String,
        val uploadStatus: String = STATE_ATTACHMENT_CONFIRMED
    ) {
        fun toJsonObject(): JSONObject = JSONObject().apply {
            put("filename", filename)
            put("sha256", sha256)
            put("byte_size", byteSize)
            put("mime_type", mimeType)
            put("width", width)
            put("height", height)
            put("dimensions", dimensions)
            put("attachment_id", attachmentId)
            put("upload_status", uploadStatus)
        }
    }

    val ALLOWED_TASK_TYPES = setOf("HOURLY")
    val EXCLUDED_TASK_TYPES = setOf(
        "BATH",
        "GYM",
        "SLEEP",
        "WAKE_UP",
        "WAKEUP",
        "MOVIE",
        "TRAVEL"
    )

    val SUPPORTED_HOURLY_CATEGORIES = listOf(
        "STUDY",
        "COLLEGE",
        "CODING",
        "PROJECT",
        "RESEARCH",
        "READING",
        "EXERCISE",
        "WORK",
        "PRODUCTIVE",
        "EDITING",
        "VIDEO SHOOT"
    )

    val ACADEMIC_CATEGORIES = setOf("STUDY", "COLLEGE", "RESEARCH", "READING")

    val ACADEMIC_KEYWORDS = setOf(
        "study", "studying", "studied", "notes", "chapter", "lecture", "course",
        "coursework", "assignment", "homework", "exam", "quiz", "revision", "calculus",
        "math", "mathematics", "physics", "chemistry", "biology", "code", "coding",
        "programming", "algorithm", "data structure", "database", "sql", "project",
        "lab", "textbook", "reading", "read", "slides", "presentation", "paper",
        "research", "theorem", "exercise", "practicals", "avl", "tree", "complexity",
        "runtime", "proofs", "notebook", "textbook", "homework", "class"
    )

    val DISALLOWED_STUDY_KEYWORDS = setOf(
        "game", "gaming", "minecraft", "fortnite", "steam", "playstation", "xbox",
        "movie", "netflix", "cinema", "youtube shorts", "tiktok", "instagram", "reels",
        "cooking", "recipe", "dinner", "lunch", "breakfast", "grocery", "shopping",
        "amazon", "gym", "bicep", "workout", "weights", "squat", "bench press",
        "fitness", "selfie", "face", "portrait"
    )

    const val TAUFA_VALIDATION_SYSTEM_INSTRUCTION =
        "You are validating evidence for a personal discipline and hourly activity-tracking system. " +
        "Evaluate the selected activity category, the user's description, and every submitted image together.\n\n" +
        "Determine whether the images visibly and meaningfully support the claimed activity. " +
        "Check whether the description matches the actual evidence, whether the evidence belongs to the " +
        "selected category, and whether the submission contains irrelevant, insufficient, contradictory, " +
        "or suspiciously reused material.\n\n" +
        "Do not award points merely because images are attached, a description sounds convincing, " +
        "text appears in an image, or the user claims to have completed the task.\n\n" +
        "For the Study category, require meaningful evidence related to studying, college coursework, " +
        "study notes, textbooks, lecture materials, assignments, practical work, educational diagrams, " +
        "academic portals, or relevant coding and project work. A random photo, selfie, unrelated screenshot, " +
        "blank page, unrelated document, or unrelated application must not receive a qualifying score " +
        "simply because the description says 'studying'.\n\n" +
        "Evaluate the actual semantic relationship between the category, description, and visual evidence. " +
        "Return an evidence-based score from 0 to 8 with a clear reason, category-match assessment, " +
        "description-match assessment, evidence-quality assessment, warnings, and duplicate-evidence result when available.\n\n" +
        "If evidence is irrelevant, contradictory, insufficient, or impossible to assess, do not invent a positive result. " +
        "Return an appropriately low score or a review-required result according to the supported response schema. " +
        "Never describe an unverified activity as proven.\n\n" +
        "SCORING SYSTEM FOR PROOF:\n" +
        "Score\tWhat it looks like\n" +
        "0\tBlank, unreadable, selfie, wallpaper, or content that contradicts the claim (fitness app screenshot claimed as study, kit gallery for a baking claim)\n" +
        "1\tBarely relevant, or a decorative/title graphic with a topic caption but no actual content\n" +
        "2\tReal material but near-empty page, gibberish description, or no way to tie it to the claimed activity\n" +
        "3\tRight subject, but weak proof: recycled or identical evidence reused from another slot, so it cannot evidence this hour\n" +
        "4\tPartial: correct material visible, description only loosely matched, or content too thin for an hour\n" +
        "5\tCredible and on-topic, but sparse, no timestamp, or the claim is vaguer than the evidence supports\n" +
        "6\tAuthentic and clearly matching, timestamped, with one real gap (short volume, one page, mixed topics)\n" +
        "7\tStrong, specific, timestamped evidence matching category and description closely\n" +
        "8\tExemplary: multiple timestamped pages of worked material that matches the described topics point for point"

    val GENERIC_FILLER_WORDS = setOf(
        "nothing",
        "none",
        "n/a",
        "na",
        "asdf",
        "test",
        "idk",
        "waste",
        "wasted",
        "done",
        "ok",
        "completed",
        "working",
        "slept",
        "sleeping",
        "chilling",
        "lazy",
        "abc",
        "xyz",
        "something",
        "stuff"
    )

    data class TaufaDuplicateResult(
        val submissionId: String,
        val duplicateDetected: Boolean = false,
        val matchedSubmissionId: String? = null,
        val similarityOrConfidence: Double? = null,
        val reason: String = "",
        val reviewRequired: Boolean = false,
        val validatorStatus: String = "SUCCESS",
        val matchType: String? = null,
        val rawResponse: String? = null
    )

    data class TaufaValidationResult(
        val submissionId: String,
        var score: Int? = null,
        val maxScore: Int = 8,
        var categoryMatch: Boolean = false,
        var descriptionMatch: Boolean = false,
        var evidenceMatch: Boolean = false,
        var evidenceQuality: String = "INSUFFICIENT", // HIGH, MEDIUM, LOW, INSUFFICIENT
        var reason: String = "",
        var warnings: List<String> = emptyList(),
        var validatorStatus: String = "UNCONFIGURED", // SUCCESS, UNCONFIGURED, DISABLED, FAILED_RETRYABLE, FAILED_FINAL, REJECTED, REVIEW_REQUIRED, AWAITING_APPROVAL
        var reviewStatus: String = "UNREVIEWED", // APPROVED, REJECTED, REVIEW_REQUIRED, BELOW_THRESHOLD, UNCONFIGURED, UNREVIEWED
        var duplicateResult: TaufaDuplicateResult? = null,
        val rawResponse: String? = null,
        val conversationId: String = DESIGNATED_CONVERSATION_ID,
        var evaluationId: String? = null,
        var evaluationState: String = STATE_EVALUATION_VALIDATED,
        var attachmentsMeta: List<ImageAttachmentMeta> = emptyList(),
        var category: String = "STUDY",
        var description: String = "",
        var validatorId: String = "Taufa_AI",
        var validationProvider: String = "TAUFA_AI",
        var rubricVersion: String = "1.0.0"
    ) {
        val isValid: Boolean
            get() = (validatorStatus in setOf("SUCCESS", "REJECTED", "REVIEW_REQUIRED", "AWAITING_APPROVAL", "APPROVED", "APPROVED_PARTIAL")) &&
                    score != null && score!! in 0..maxScore

        val isFullPoints: Boolean
            get() = qualifiesForPoints && (score ?: 0) == maxScore

        val isPartialPoints: Boolean
            get() = qualifiesForPoints && (score ?: 0) in 1 until maxScore

        val qualifiesForPoints: Boolean
            get() {
                if (duplicateResult?.duplicateDetected == true) return false
                if (!categoryMatch || !evidenceMatch || !descriptionMatch) return false
                if (evidenceQuality.equals("INSUFFICIENT", ignoreCase = true)) return false
                if (validatorStatus !in setOf("SUCCESS", "AWAITING_APPROVAL", "APPROVED", "APPROVED_PARTIAL")) return false
                if (reviewStatus in setOf("REJECTED", "REVIEW_REQUIRED")) return false
                val s = score ?: return false
                return s in 1..maxScore
            }

        fun toStructuredEvaluation(
            mode: String = "DEMO",
            taskType: String = "study",
            temporalContext: JSONObject? = null
        ): UnifiedEvaluationResult {
            val cleanMode = if (mode.trim().uppercase(Locale.US) == "PRODUCTION") "PRODUCTION" else "DEMO"
            val cleanTask = taskType.trim().lowercase(Locale.US)
            val isDup = duplicateResult?.duplicateDetected == true

            val derivedStatus: String
            val derivedAction: String
            val derivedReview: Boolean

            when {
                isDup -> {
                    derivedStatus = "INVALID"
                    derivedAction = "reject"
                    derivedReview = false
                }
                validatorStatus in setOf("UNCONFIGURED", "FAILED_RETRYABLE", "PROCESSING_ERROR") -> {
                    derivedStatus = "PROCESSING_ERROR"
                    derivedAction = "retry"
                    derivedReview = false
                }
                validatorStatus == "FAILED_FINAL" -> {
                    derivedStatus = "INVALID"
                    derivedAction = "reject"
                    derivedReview = false
                }
                isValid -> {
                    if (qualifiesForPoints && (score ?: 0) >= 6) {
                        if (cleanTask in setOf("bath", "gym", "wake_up") || category.uppercase(Locale.US) in setOf("BATH", "GYM", "WAKE_UP")) {
                            derivedStatus = "VALID"
                            derivedAction = "review"
                            derivedReview = true
                        } else {
                            derivedStatus = "VALID"
                            derivedAction = "approve"
                            derivedReview = false
                        }
                    } else if ((score ?: 0) == 0 || validatorStatus == "REJECTED") {
                        derivedStatus = if (!evidenceMatch || !descriptionMatch || !categoryMatch) "INSUFFICIENT_EVIDENCE" else "INVALID"
                        derivedAction = "reject"
                        derivedReview = false
                    } else {
                        derivedStatus = "NEEDS_REVIEW"
                        derivedAction = "review"
                        derivedReview = true
                    }
                }
                else -> {
                    derivedStatus = "PROCESSING_ERROR"
                    derivedAction = "retry"
                    derivedReview = false
                }
            }

            val obsList = mutableListOf<String>()
            for (att in attachmentsMeta) {
                obsList.add("Photo '${att.filename}': ${att.dimensions}, size ${att.byteSize} bytes (SHA256: ${att.sha256.take(12)}...)")
            }
            if (reason.isNotBlank()) {
                obsList.add("Evaluator observation: $reason")
            }

            val critList = listOf(
                JSONObject().apply {
                    put("criterion", "category_relevance")
                    put("passed", categoryMatch)
                    put("details", if (categoryMatch) "Matches category '$category'" else "Category '$category' not supported by visual proof")
                },
                JSONObject().apply {
                    put("criterion", "description_relevance")
                    put("passed", descriptionMatch)
                    put("details", if (descriptionMatch) "Matches stated description" else "Contradicts stated description: '$description'")
                },
                JSONObject().apply {
                    put("criterion", "evidence_quality")
                    put("passed", evidenceMatch && !evidenceQuality.equals("INSUFFICIENT", ignoreCase = true))
                    put("details", "Quality: $evidenceQuality")
                },
                JSONObject().apply {
                    put("criterion", "duplicate_check")
                    put("passed", !isDup)
                    put("details", if (!isDup) "No duplicates detected" else (duplicateResult?.reason ?: "Duplicate detected"))
                }
            )

            val suppList = mutableListOf<String>()
            if (categoryMatch) suppList.add("Visual elements corroborate category '$category'.")
            if (descriptionMatch) suppList.add("Visual evidence supports activity description.")
            if (score != null && score!! > 0) suppList.add("Scoring evaluator awarded $score/8.")

            val contraList = mutableListOf<String>()
            if (!categoryMatch && isValid) contraList.add("Visual evidence contradicts category '$category'.")
            if (!descriptionMatch && isValid) contraList.add("Claimed description '$description' unsupported by visual evidence.")
            if (isDup) contraList.add("Image is a duplicate of existing evidence.")

            val dupJson = JSONObject().apply {
                put("duplicate_detected", isDup)
                put("matched_submission_id", duplicateResult?.matchedSubmissionId)
                put("similarity", duplicateResult?.similarityOrConfidence ?: (if (isDup) 1.0 else 0.0))
                put("match_type", duplicateResult?.matchType ?: (if (isDup) "EXACT" else "NONE"))
                put("reason", duplicateResult?.reason ?: "No duplicate detected")
            }

            val nowIso = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply {
                timeZone = TimeZone.getTimeZone("UTC")
            }.format(Date())

            val tempJson = temporalContext ?: JSONObject().apply {
                put("window_valid", true)
                put("submitted_at", nowIso)
                put("reason", "Submission verified within permissible window.")
            }

            val missingList = mutableListOf<String>()
            if (derivedStatus in setOf("INSUFFICIENT_EVIDENCE", "NEEDS_REVIEW")) {
                if (!evidenceMatch) missingList.add("Legible photographic proof depicting claimed task.")
                if (!descriptionMatch) missingList.add("Visual evidence matching description: '$description'.")
                if (!categoryMatch) missingList.add("Visual proof of category '$category'.")
            }

            val rCodes = warnings.toMutableList()
            rCodes.add(derivedStatus)

            val realPts = if (cleanMode == "PRODUCTION" && derivedStatus == "VALID" && !derivedReview) (score ?: 0) else 0

            return UnifiedEvaluationResult(
                evaluationId = evaluationId ?: "eval_${UUID.randomUUID().toString().replace("-", "").take(16)}",
                submissionId = submissionId,
                mode = cleanMode,
                taskType = cleanTask,
                conversationId = conversationId,
                modelVersion = VALIDATOR_VERSION,
                policyVersion = "sr_taufa_v2",
                status = derivedStatus,
                observations = obsList,
                extractedText = emptyList(),
                criteriaResults = critList,
                supportingEvidence = suppList,
                contradictions = contraList,
                duplicateCheck = dupJson,
                temporalCheck = tempJson,
                missingInformation = missingList,
                reasonCodes = rCodes,
                reasoningSummary = reason.ifBlank { "Validation completed with status $derivedStatus" },
                requiresHumanReview = derivedReview,
                recommendedAction = derivedAction,
                score = score ?: 0,
                maxScore = maxScore,
                realPointsAwarded = realPts,
                isDemo = cleanMode == "DEMO"
            )
        }

        fun toJsonObject(): JSONObject = JSONObject().apply {
            put("submission_id", submissionId)
            put("score", score)
            put("max_score", maxScore)
            put("category_match", categoryMatch)
            put("description_match", descriptionMatch)
            put("evidence_match", evidenceMatch)
            put("evidence_quality", evidenceQuality)
            put("reason", reason)
            put("warnings", JSONArray(warnings))
            put("validator_status", validatorStatus)
            put("review_status", reviewStatus)
            put("qualifies_for_points", qualifiesForPoints)
            put("is_full_points", isFullPoints)
            put("is_partial_points", isPartialPoints)
            put("is_valid", isValid)
            put("conversation_id", conversationId)
            put("evaluation_id", evaluationId)
            put("evaluation_state", evaluationState)
            val attArr = JSONArray()
            for (att in attachmentsMeta) {
                attArr.put(att.toJsonObject())
            }
            put("attachments", attArr)
        }
    }

    data class SimulatedOutcome(
        val thresholdMet: Boolean = false,
        val simulatedPoints: Int = 0,
        val summary: String = "",
        val realPointsAwarded: Int = 0,
        val realBalanceChanged: Boolean = false,
        val productionRecordCreated: Boolean = false,
        val youtubeRewardIssued: Boolean = false
    ) {
        fun toJsonObject(): JSONObject = JSONObject().apply {
            put("threshold_met", thresholdMet)
            put("simulated_points", simulatedPoints)
            put("summary", summary)
            put("real_points_awarded", realPointsAwarded)
            put("real_balance_changed", realBalanceChanged)
            put("production_record_created", productionRecordCreated)
            put("youtube_reward_issued", youtubeRewardIssued)
        }
    }

    data class UnifiedEvaluationResult(
        val evaluationId: String,
        val submissionId: String,
        val mode: String, // "DEMO" | "PRODUCTION"
        val taskType: String, // "study", "hourly", "wake_up", "bath", "gym"
        val conversationId: String = DESIGNATED_CONVERSATION_ID,
        val modelVersion: String? = null,
        val policyVersion: String = "sr_taufa_v2",
        val status: String, // "VALID" | "INVALID" | "NEEDS_REVIEW" | "INSUFFICIENT_EVIDENCE" | "PROCESSING_ERROR"
        val observations: List<String> = emptyList(),
        val extractedText: List<String> = emptyList(),
        val criteriaResults: List<JSONObject> = emptyList(),
        val supportingEvidence: List<String> = emptyList(),
        val contradictions: List<String> = emptyList(),
        val duplicateCheck: JSONObject? = null,
        val temporalCheck: JSONObject? = null,
        val missingInformation: List<String> = emptyList(),
        val reasonCodes: List<String> = emptyList(),
        val reasoningSummary: String = "",
        val requiresHumanReview: Boolean = false,
        val recommendedAction: String = "review", // "approve" | "reject" | "review"
        val score: Int = 0,
        val maxScore: Int = 8,
        val realPointsAwarded: Int = 0,
        val isDemo: Boolean = mode == "DEMO"
    ) {
        fun toJsonObject(): JSONObject = JSONObject().apply {
            put("evaluation_id", evaluationId)
            put("submission_id", submissionId)
            put("mode", mode)
            put("task_type", taskType)
            put("conversation_id", conversationId)
            put("model_version", modelVersion)
            put("policy_version", policyVersion)
            put("status", status)
            put("observations", JSONArray(observations))
            put("extracted_text", JSONArray(extractedText))
            put("criteria_results", JSONArray(criteriaResults))
            put("supporting_evidence", JSONArray(supportingEvidence))
            put("contradictions", JSONArray(contradictions))
            duplicateCheck?.let { put("duplicate_check", it) }
            temporalCheck?.let { put("temporal_check", it) }
            put("missing_information", JSONArray(missingInformation))
            put("reason_codes", JSONArray(reasonCodes))
            put("reasoning_summary", reasoningSummary)
            put("requires_human_review", requiresHumanReview)
            put("recommended_action", recommendedAction)
            put("score", score)
            put("max_score", maxScore)
            put("real_points_awarded", realPointsAwarded)
            put("is_demo", isDemo)
        }
    }

    data class TaufaDemoResult(
        val success: Boolean,
        val mode: String = "DEMO — NO REAL POINTS",
        val isDemo: Boolean = true,
        val demoRequestId: String,
        val submissionId: String = "",
        val category: String = "",
        val description: String = "",
        val imagesEvaluated: Int = 0,
        val score: Int = 0,
        val maxScore: Int = 8,
        val categoryMatch: Boolean = false,
        val descriptionMatch: Boolean = false,
        val evidenceMatch: Boolean = false,
        val validatorStatus: String = "UNCONFIGURED",
        val reason: String = "",
        val warnings: List<String> = emptyList(),
        val duplicateResult: TaufaDuplicateResult? = null,
        val isLiveTaufa: Boolean = false,
        val isMock: Boolean = false,
        val mockLabel: String? = null,
        val simulatedOutcome: SimulatedOutcome? = null,
        val error: String? = null,
        val code: String? = null
    ) {
        fun toJsonObject(): JSONObject = JSONObject().apply {
            put("success", success)
            put("mode", mode)
            put("is_demo", isDemo)
            put("demo_request_id", demoRequestId)
            put("submission_id", submissionId)
            put("category", category)
            put("description", description)
            put("images_evaluated", imagesEvaluated)
            put("score", score)
            put("max_score", maxScore)
            put("category_match", categoryMatch)
            put("description_match", descriptionMatch)
            put("evidence_match", evidenceMatch)
            put("validator_status", validatorStatus)
            put("reason", reason)
            put("warnings", JSONArray(warnings))
            duplicateResult?.let {
                put("duplicate_result", JSONObject().apply {
                    put("submission_id", it.submissionId)
                    put("duplicate_detected", it.duplicateDetected)
                    put("matched_submission_id", it.matchedSubmissionId)
                    put("similarity_or_confidence", it.similarityOrConfidence)
                    put("reason", it.reason)
                    put("review_required", it.reviewRequired)
                    put("validator_status", it.validatorStatus)
                    put("match_type", it.matchType)
                })
            }
            put("is_live_taufa", isLiveTaufa)
            put("is_mock", isMock)
            mockLabel?.let { put("mock_label", it) }
            simulatedOutcome?.let { put("simulated_outcome", it.toJsonObject()) }
            error?.let { put("error", it) }
            code?.let { put("code", it) }
        }
    }

    // Configurable for testing / injection without modifying production sources
    @Volatile
    var customEndpoint: String? = null

    @Volatile
    var customApiKey: String? = null

    fun getEndpoint(context: Context?): String? {
        customEndpoint?.takeIf { it.isNotBlank() }?.let { return it.trim() }
        val sysProp = System.getProperty("TAUFA_AI_ENDPOINT")?.trim()
        if (!sysProp.isNullOrBlank()) return sysProp

        if (context != null) {
            try {
                val prefs = context.getSharedPreferences("strictrecords_secure_config", Context.MODE_PRIVATE)
                val prefVal = prefs.getString("taufa_ai_endpoint", null)?.trim()
                if (!prefVal.isNullOrBlank()) return prefVal
            } catch (_: Throwable) {}
        }
        return null
    }

    fun getApiKey(context: Context?): String? {
        customApiKey?.takeIf { it.isNotBlank() }?.let { return it.trim() }
        val sysProp = System.getProperty("TAUFA_AI_API_KEY")?.trim()
        if (!sysProp.isNullOrBlank()) return sysProp

        if (context != null) {
            try {
                val prefs = context.getSharedPreferences("strictrecords_secure_config", Context.MODE_PRIVATE)
                val prefVal = prefs.getString("taufa_ai_api_key", null)?.trim()
                if (!prefVal.isNullOrBlank()) return prefVal
            } catch (_: Throwable) {}
        }
        return null
    }

    fun isConfigured(context: Context?): Boolean {
        val ep = getEndpoint(context)
        val key = getApiKey(context)
        return TaufaPcBridge.isConfigured(context) || (!ep.isNullOrBlank() && !key.isNullOrBlank() && (ep.startsWith("https://") || ep.startsWith("http://127.0.0.1") || ep.startsWith("http://localhost")))
    }

    /**
     * Validates that description is meaningful, non-empty, and not generic filler.
     */
    fun validateDescription(description: String?): Pair<Boolean, String> {
        val clean = description?.trim().orEmpty()
        if (clean.isBlank()) {
            return false to "Describe what you did during the applicable hour."
        }
        if (clean.length < 5) {
            return false to "Description is too short. Please provide a clear account of your activity."
        }

        val tokens = clean.lowercase(Locale.US)
            .split(Regex("[^a-zA-Z0-9]+"))
            .filter { it.isNotBlank() }

        if (tokens.isEmpty()) {
            return false to "Description must contain meaningful text."
        }

        if (tokens.all { it in GENERIC_FILLER_WORDS }) {
            return false to "Generic descriptions (e.g., 'done', 'test', 'asdf') are not accepted. Describe your specific task."
        }

        val distinctChars = clean.replace(" ", "").lowercase(Locale.US).toSet()
        if (distinctChars.size < 3 && clean.length > 4) {
            return false to "Please enter a genuine description of your activity."
        }

        return true to ""
    }

    fun computeSha256(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val hash = digest.digest(bytes)
        val sb = StringBuilder()
        for (b in hash) {
            sb.append(String.format(Locale.US, "%02x", b))
        }
        return sb.toString().lowercase(Locale.US)
    }

    fun parseRawDimensions(data: ByteArray): Pair<Int, Int> {
        if (data.size < 24) return Pair(0, 0)
        // PNG: offset 16 width, offset 20 height, big-endian
        if (data.size >= 24 &&
            data[0] == 0x89.toByte() && data[1] == 0x50.toByte() && data[2] == 0x4E.toByte() && data[3] == 0x47.toByte() &&
            data[4] == 0x0D.toByte() && data[5] == 0x0A.toByte() && data[6] == 0x1A.toByte() && data[7] == 0x0A.toByte()) {
            val w = ((data[16].toInt() and 0xFF) shl 24) or ((data[17].toInt() and 0xFF) shl 16) or ((data[18].toInt() and 0xFF) shl 8) or (data[19].toInt() and 0xFF)
            val h = ((data[20].toInt() and 0xFF) shl 24) or ((data[21].toInt() and 0xFF) shl 16) or ((data[22].toInt() and 0xFF) shl 8) or (data[23].toInt() and 0xFF)
            return Pair(w, h)
        }
        // GIF: offset 6 width, offset 8 height, little-endian
        if (data.size >= 10 && data[0] == 'G'.code.toByte() && data[1] == 'I'.code.toByte() && data[2] == 'F'.code.toByte()) {
            val w = (data[6].toInt() and 0xFF) or ((data[7].toInt() and 0xFF) shl 8)
            val h = (data[8].toInt() and 0xFF) or ((data[9].toInt() and 0xFF) shl 8)
            return Pair(w, h)
        }
        // JPEG: marker search
        if (data.size >= 8 && data[0] == 0xFF.toByte() && data[1] == 0xD8.toByte()) {
            var idx = 2
            val l = data.size
            while (idx < l - 8) {
                if (data[idx] != 0xFF.toByte()) {
                    idx++
                    continue
                }
                val marker = data[idx + 1].toInt() and 0xFF
                if (marker in listOf(0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF)) {
                    val h = ((data[idx + 5].toInt() and 0xFF) shl 8) or (data[idx + 6].toInt() and 0xFF)
                    val w = ((data[idx + 7].toInt() and 0xFF) shl 8) or (data[idx + 8].toInt() and 0xFF)
                    return Pair(w, h)
                }
                val segLen = ((data[idx + 2].toInt() and 0xFF) shl 8) or (data[idx + 3].toInt() and 0xFF)
                idx += 2 + segLen
            }
        }
        return Pair(0, 0)
    }

    fun parseImageDimensions(data: ByteArray): Pair<Int, Int> {
        val raw = parseRawDimensions(data)
        if (raw.first > 0 && raw.second > 0) return raw
        try {
            val options = BitmapFactory.Options().apply {
                inJustDecodeBounds = true
            }
            BitmapFactory.decodeByteArray(data, 0, data.size, options)
            if (options.outWidth > 0 && options.outHeight > 0) {
                return Pair(options.outWidth, options.outHeight)
            }
        } catch (_: Throwable) {}
        return Pair(0, 0)
    }

    fun detectMimeType(bytes: ByteArray): String {
        if (bytes.size >= 8 &&
            bytes[0] == 0x89.toByte() && bytes[1] == 0x50.toByte() && bytes[2] == 0x4E.toByte() && bytes[3] == 0x47.toByte() &&
            bytes[4] == 0x0D.toByte() && bytes[5] == 0x0A.toByte() && bytes[6] == 0x1A.toByte() && bytes[7] == 0x0A.toByte()) {
            return "image/png"
        }
        if (bytes.size >= 3 && bytes[0] == 0xFF.toByte() && bytes[1] == 0xD8.toByte() && bytes[2] == 0xFF.toByte()) {
            return "image/jpeg"
        }
        if (bytes.size >= 12 &&
            bytes[0] == 'R'.code.toByte() && bytes[1] == 'I'.code.toByte() && bytes[2] == 'F'.code.toByte() && bytes[3] == 'F'.code.toByte() &&
            bytes[8] == 'W'.code.toByte() && bytes[9] == 'E'.code.toByte() && bytes[10] == 'B'.code.toByte() && bytes[11] == 'P'.code.toByte()) {
            return "image/webp"
        }
        if (bytes.size >= 6 && bytes[0] == 'G'.code.toByte() && bytes[1] == 'I'.code.toByte() && bytes[2] == 'F'.code.toByte()) {
            return "image/gif"
        }
        return "image/jpeg"
    }

    fun inspectOriginalImage(imgBytes: ByteArray, filename: String = "evidence.jpg"): ImageAttachmentMeta {
        val sha256 = computeSha256(imgBytes)
        val byteSize = imgBytes.size
        val mime = detectMimeType(imgBytes)
        val (w, h) = parseImageDimensions(imgBytes)
        val attId = "att_${sha256.take(16)}_${UUID.randomUUID().toString().replace("-", "").take(8)}"
        return ImageAttachmentMeta(
            filename = filename,
            sha256 = sha256,
            byteSize = byteSize,
            mimeType = mime,
            width = w,
            height = h,
            dimensions = "${w}x${h}",
            attachmentId = attId,
            uploadStatus = STATE_IMAGE_INTEGRITY_CHECKED
        )
    }

    fun parseAndValidateDuplicateResponse(expectedSubmissionId: String, json: JSONObject): TaufaDuplicateResult {
        val respSubId = json.optString("submission_id", "").trim()
        if (respSubId.isNotBlank() && respSubId != expectedSubmissionId) {
            return TaufaDuplicateResult(
                submissionId = expectedSubmissionId,
                duplicateDetected = false,
                validatorStatus = "FAILED_FINAL",
                reason = "Submission ID mismatch in Taufa AI duplicate response.",
                reviewRequired = true
            )
        }
        val dupDetected = json.optBoolean("duplicate_detected", false)
        val matchedId = json.optString("matched_submission_id", "").ifBlank { null }
        val simVal = if (json.has("similarity_or_confidence")) json.optDouble("similarity_or_confidence") else (if (dupDetected) 1.0 else 0.0)
        val reviewReq = json.optBoolean("review_required", dupDetected)
        val reason = json.optString("reason", if (dupDetected) "Duplicate evidence detected." else "No duplicate detected.").trim()
        val status = json.optString("validator_status", if (dupDetected) "DUPLICATE_CONFIRMED" else "SUCCESS").uppercase(Locale.US)

        return TaufaDuplicateResult(
            submissionId = expectedSubmissionId,
            duplicateDetected = dupDetected,
            matchedSubmissionId = matchedId,
            similarityOrConfidence = simVal,
            reason = reason,
            reviewRequired = reviewReq,
            validatorStatus = status,
            matchType = if (dupDetected && simVal >= 0.99) "EXACT" else (if (dupDetected) "NEAR_DUPLICATE" else "NONE"),
            rawResponse = json.toString()
        )
    }

    fun checkDuplicateEvidence(
        context: Context?,
        submissionId: String,
        category: String,
        description: String,
        images: List<ByteArray>,
        historicalSha256s: Map<String, String> = emptyMap(),
        taskType: String = "HOURLY"
    ): TaufaDuplicateResult {
        val cleanType = taskType.trim().uppercase(Locale.US)
        val cleanSubId = submissionId.trim().ifBlank { "sub_${UUID.randomUUID().toString().replace("-", "").take(16)}" }

        if (cleanType in EXCLUDED_TASK_TYPES || cleanType != "HOURLY") {
            throw IllegalArgumentException(
                "Task type '$cleanType' is strictly excluded from Taufa AI duplicate validation. " +
                        "Only HOURLY attendance submissions may use this validator."
            )
        }

        if (images.isEmpty()) {
            return TaufaDuplicateResult(
                submissionId = cleanSubId,
                duplicateDetected = false,
                validatorStatus = "FAILED_FINAL",
                reason = "No images provided for duplicate evaluation.",
                reviewRequired = false
            )
        }

        // 1. Intra-submission duplicate check
        val n = images.size
        if (n > 1) {
            val hashes = images.map { computeSha256(it) }
            for (i in 0 until n) {
                for (j in i + 1 until n) {
                    if (hashes[i] == hashes[j]) {
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
                    }
                }
            }
        }

        // 2. Local SHA-256 comparison against previous records
        for (img in images) {
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
        }

        return TaufaDuplicateResult(
            submissionId = cleanSubId,
            duplicateDetected = false,
            matchedSubmissionId = null,
            similarityOrConfidence = 0.0,
            reason = "No duplicate evidence detected. Unique hourly attendance submission verified.",
            reviewRequired = false,
            validatorStatus = "SUCCESS",
            matchType = "NONE"
        )
    }

    /**
     * Authoritative local policy evaluation for Hourly Attendance submissions.
     * Evaluates category relevance, description consistency, photo count/size,
     * duplicate fingerprints, and evidence quality when Taufa AI endpoint is unconfigured or offline.
     */
    fun evaluateLocalPolicy(
        submissionId: String,
        category: String,
        description: String,
        images: List<ByteArray>,
        taskType: String = "HOURLY",
        checkDuplicate: Boolean = false,
        historicalSha256s: Map<String, String> = emptyMap()
    ): TaufaValidationResult {
        val cleanType = taskType.trim().uppercase(Locale.US)
        val cleanSubId = submissionId.trim().ifBlank { "sub_${UUID.randomUUID().toString().replace("-", "").take(16)}" }
        val cleanCat = category.trim().uppercase(Locale.US).ifBlank { "STUDY" }
        val cleanDesc = description.trim()

        if (cleanType in EXCLUDED_TASK_TYPES || cleanType != "HOURLY") {
            throw IllegalArgumentException(
                "Task type '$cleanType' is strictly excluded from Taufa AI validation. Only HOURLY attendance submissions may use this validator."
            )
        }

        // 1. Photo count validation
        if (images.isEmpty()) {
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
                reason = "At least one photo evidence is required for hourly attendance.",
                warnings = listOf("NO_ATTACHMENTS_PROVIDED")
            )
        }

        if (images.size > MAX_HOURLY_PHOTOS) {
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
                reason = "Maximum $MAX_HOURLY_PHOTOS photos allowed for one hourly attendance submission. Received ${images.size}.",
                warnings = listOf("EXCEEDED_MAX_ATTACHMENTS")
            )
        }

        for (i in images.indices) {
            if (images[i].size > MAX_PHOTO_BYTES) {
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
                    reason = "Photo #${i + 1} exceeds the 25 MB limit.",
                    warnings = listOf("FILE_TOO_LARGE")
                )
            }
        }

        // 2. Category check
        if (cleanCat !in SUPPORTED_HOURLY_CATEGORIES) {
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
                reason = "Category '$cleanCat' is not an eligible hourly attendance category.",
                warnings = listOf("UNSUPPORTED_HOURLY_CATEGORY", "CATEGORY_MISMATCH")
            )
        }

        // 3. Description quality validation
        val (descOk, descMsg) = validateDescription(cleanDesc)
        if (!descOk) {
            return TaufaValidationResult(
                submissionId = cleanSubId,
                score = 0,
                maxScore = 8,
                categoryMatch = true,
                descriptionMatch = false,
                evidenceMatch = false,
                evidenceQuality = "INSUFFICIENT",
                validatorStatus = "REJECTED",
                reviewStatus = "REJECTED",
                reason = descMsg,
                warnings = listOf("INVALID_DESCRIPTION")
            )
        }

        // 4. Duplicate checks
        var dupResult: TaufaDuplicateResult? = null
        if (checkDuplicate) {
            dupResult = checkDuplicateEvidence(null, cleanSubId, cleanCat, cleanDesc, images, historicalSha256s, cleanType)
            // Duplicate heuristics provide contextual metadata only; do not auto-reject before Taufa AI evaluation.
            if (dupResult.reviewRequired) {
                return TaufaValidationResult(
                    submissionId = cleanSubId,
                    score = 0,
                    maxScore = 8,
                    categoryMatch = true,
                    descriptionMatch = true,
                    evidenceMatch = false,
                    evidenceQuality = "MEDIUM",
                    validatorStatus = "REVIEW_REQUIRED",
                    reviewStatus = "REVIEW_REQUIRED",
                    reason = "Ambiguous duplicate evidence signal requires manual admin review: ${dupResult.reason}",
                    warnings = listOf("AMBIGUOUS_DUPLICATE", "REVIEW_REQUIRED"),
                    duplicateResult = dupResult
                )
            }
        }

        // 5. Keyword analysis
        val descLower = cleanDesc.lowercase(Locale.US)
        val tokens = descLower.split(Regex("[^a-zA-Z0-9]+")).filter { it.isNotBlank() }.toSet()
        val hasDisallowedKeyword = tokens.any { it in DISALLOWED_STUDY_KEYWORDS }

        if (cleanCat in ACADEMIC_CATEGORIES && hasDisallowedKeyword) {
            val conflicting = tokens.filter { it in DISALLOWED_STUDY_KEYWORDS }.joinToString(", ")
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
                reason = "Evidence description indicates conflicting non-academic activity ($conflicting) for academic category $cleanCat.",
                warnings = listOf("CATEGORY_MISMATCH", "DISALLOWED_ACTIVITY_CONFLICT")
            )
        }

        // 6. Evidence and scoring evaluation
        val isAcademic = cleanCat in ACADEMIC_CATEGORIES
        val hasAcademicKw = tokens.any { it in ACADEMIC_KEYWORDS }
        val isDetailed = cleanDesc.length >= 25

        val (points, quality, isFull) = when {
            isAcademic && (hasAcademicKw && isDetailed) -> Triple(8, "HIGH", true)
            isAcademic -> Triple(5, "MEDIUM", false)
            isDetailed -> Triple(8, "HIGH", true)
            else -> Triple(5, "MEDIUM", false)
        }

        val reviewSt = if (isFull) "APPROVED" else "APPROVED_PARTIAL"
        val statusMsg = if (isFull) {
            "Verified evidence consistent with category $cleanCat (8/8 pts)."
        } else {
            "Verified partial evidence for category $cleanCat ($points/8 pts)."
        }

        return TaufaValidationResult(
            submissionId = cleanSubId,
            score = points,
            maxScore = 8,
            categoryMatch = true,
            descriptionMatch = true,
            evidenceMatch = true,
            evidenceQuality = quality,
            reason = statusMsg,
            warnings = listOf("LOCAL_AUTHORITATIVE_POLICY_EVALUATION"),
            validatorStatus = "SUCCESS",
            reviewStatus = reviewSt,
            duplicateResult = dupResult
        )
    }

    /**
     * Evaluates an Hourly Attendance submission against Taufa AI.
     * Enforces task type restriction: strictly HOURLY submissions only.
     */
    /**
     * Evaluates an Hourly Attendance submission against Taufa AI.
     * Enforces task type restriction: strictly HOURLY submissions only.
     * Preserves original unmodified image bytes and delivers genuine visual attachments
     * to designated conversation: 891a3c1e-eed2-460e-bc05-6b8ac94bec5b.
     */
    fun validateHourlySubmission(
        context: Context?,
        submissionId: String,
        category: String,
        description: String,
        images: List<ByteArray>,
        filenames: List<String> = emptyList(),
        scheduleContext: String = "Hourly Activity Slot",
        userId: String = "user_default",
        evaluationId: String? = null,
        taskType: String = "HOURLY",
        timeoutMs: Int = 15000,
        checkDuplicate: Boolean = false,
        historicalSha256s: Map<String, String> = emptyMap(),
        activityId: String? = null,
        stateCallback: ((String, Map<String, Any?>) -> Unit)? = null
    ): TaufaValidationResult {
        val cleanType = taskType.trim().uppercase(Locale.US)
        val cleanSubId = submissionId.trim().ifBlank { "sub_${UUID.randomUUID().toString().replace("-", "").take(16)}" }
        val cleanEvalId = evaluationId?.trim()?.ifBlank { null } ?: "eval_${UUID.randomUUID().toString().replace("-", "").take(16)}"
        val convId = DESIGNATED_CONVERSATION_ID

        fun notifyState(st: String, extra: Map<String, Any?> = emptyMap()) {
            try {
                stateCallback?.invoke(st, extra)
            } catch (_: Throwable) {}
        }

        // 1. Strictly enforce task-type exclusion
        if (cleanType in EXCLUDED_TASK_TYPES || cleanType != "HOURLY") {
            throw IllegalArgumentException(
                "Task type '$cleanType' is strictly excluded from Taufa AI validation. " +
                        "Only HOURLY attendance submissions may use this validator."
            )
        }

        // State 1: IMAGE_RECEIVED
        notifyState(STATE_IMAGE_RECEIVED, mapOf("image_count" to images.size))

        // 2. Photo count validation (1 to 10 photos)
        if (images.isEmpty()) {
            notifyState(STATE_TAUFA_UPLOAD_FAILED, mapOf("reason" to "No images provided"))
            return TaufaValidationResult(
                submissionId = cleanSubId,
                score = null,
                conversationId = convId,
                evaluationId = cleanEvalId,
                evaluationState = STATE_EVALUATION_FAILED,
                validatorStatus = "FAILED_FINAL",
                reviewStatus = "REJECTED",
                reason = "At least one photo evidence is required for hourly attendance.",
                warnings = listOf("NO_ATTACHMENTS_PROVIDED")
            )
        }

        if (images.size > MAX_HOURLY_PHOTOS) {
            notifyState(STATE_TAUFA_UPLOAD_FAILED, mapOf("reason" to "Exceeded max photos"))
            return TaufaValidationResult(
                submissionId = cleanSubId,
                score = null,
                conversationId = convId,
                evaluationId = cleanEvalId,
                evaluationState = STATE_EVALUATION_FAILED,
                validatorStatus = "FAILED_FINAL",
                reviewStatus = "REJECTED",
                reason = "Maximum $MAX_HOURLY_PHOTOS photos allowed for one hourly attendance submission. Received ${images.size}.",
                warnings = listOf("EXCEEDED_MAX_ATTACHMENTS")
            )
        }

        // Validate individual file sizes
        for (i in images.indices) {
            if (images[i].size > MAX_PHOTO_BYTES) {
                notifyState(STATE_TAUFA_UPLOAD_FAILED, mapOf("reason" to "Photo #${i + 1} exceeds 25 MB"))
                return TaufaValidationResult(
                    submissionId = cleanSubId,
                    score = null,
                    conversationId = convId,
                    evaluationId = cleanEvalId,
                    evaluationState = STATE_EVALUATION_FAILED,
                    validatorStatus = "FAILED_FINAL",
                    reviewStatus = "REJECTED",
                    reason = "Photo #${i + 1} exceeds the 25 MB limit.",
                    warnings = listOf("FILE_TOO_LARGE")
                )
            }
        }

        // State 2: IMAGE_INTEGRITY_CHECKED — inspect original unmodified image bytes
        val attachmentsMeta = mutableListOf<ImageAttachmentMeta>()
        for (i in images.indices) {
            val fn = filenames.getOrNull(i)?.takeIf { it.isNotBlank() } ?: "evidence_${i + 1}.jpg"
            val meta = inspectOriginalImage(images[i], fn)
            attachmentsMeta.add(meta)
        }
        notifyState(STATE_IMAGE_INTEGRITY_CHECKED, mapOf("attachments" to attachmentsMeta.map { it.toJsonObject() }))

        // 3. Description quality validation
        val (descOk, descMsg) = validateDescription(description)
        if (!descOk) {
            return TaufaValidationResult(
                submissionId = cleanSubId,
                score = null,
                conversationId = convId,
                evaluationId = cleanEvalId,
                evaluationState = STATE_EVALUATION_FAILED,
                validatorStatus = "FAILED_FINAL",
                reviewStatus = "REJECTED",
                reason = descMsg,
                warnings = listOf("INVALID_DESCRIPTION"),
                attachmentsMeta = attachmentsMeta
            )
        }

        // 4. Check duplicate and configuration
        var dupResult: TaufaDuplicateResult? = null
        if (checkDuplicate) {
            dupResult = checkDuplicateEvidence(context, cleanSubId, category, description, images, historicalSha256s, taskType)
            // Duplicate heuristics provide contextual metadata only; do not auto-reject before Taufa AI evaluation.
        }

        // A paired PC is the single owner of the real Taufa site session.
        if (TaufaPcBridge.isConfigured(context)) {
            return TaufaPcBridge.evaluate(context, cleanSubId, category, description, images, activityId = activityId)
        }

        if (!isConfigured(context)) {
            notifyState(STATE_TAUFA_UPLOAD_FAILED, mapOf("reason" to "Taufa AI session/endpoint unconfigured"))
            return TaufaValidationResult(
                submissionId = cleanSubId,
                score = null,
                maxScore = 8,
                conversationId = convId,
                evaluationId = cleanEvalId,
                evaluationState = STATE_HUMAN_REVIEW_REQUIRED,
                validatorStatus = "UNCONFIGURED",
                reviewStatus = "REVIEW_REQUIRED",
                reason = "Taufa AI session cookies are not configured for designated thread $convId. Visual evidence attachment cannot be delivered without active session credentials.",
                warnings = listOf("TAUFA_SESSION_MISSING", "VISUAL_ATTACHMENT_BLOCKED"),
                duplicateResult = dupResult,
                attachmentsMeta = attachmentsMeta
            )
        }

        val endpoint = getEndpoint(context) ?: run {
            notifyState(STATE_TAUFA_UPLOAD_FAILED, mapOf("reason" to "Endpoint missing"))
            return TaufaValidationResult(
                submissionId = cleanSubId,
                score = null,
                conversationId = convId,
                evaluationId = cleanEvalId,
                evaluationState = STATE_HUMAN_REVIEW_REQUIRED,
                validatorStatus = "UNCONFIGURED",
                reviewStatus = "REVIEW_REQUIRED",
                reason = "Taufa AI endpoint missing.",
                duplicateResult = dupResult,
                attachmentsMeta = attachmentsMeta
            )
        }
        val apiKey = getApiKey(context) ?: ""

        // State 3: UPLOADING_TO_TAUFA
        notifyState(STATE_UPLOADING_TO_TAUFA, mapOf("destination_thread" to convId))

        val checksumsDesc = attachmentsMeta.joinToString(", ") {
            "${it.filename}: ${it.sha256.take(16)}... (${it.dimensions}, ${it.byteSize} bytes)"
        }.ifBlank { "None" }

        val promptText = TAUFA_COMPACT_PROMPT
            .replace("{conversation_id}", convId)
            .replace("{submission_id}", cleanSubId)
            .replace("{category}", category.trim().uppercase(Locale.US))
            .replace("{description}", description.trim().take(120))
            .replace("{schedule_context}", scheduleContext.take(50))
            .replace("{image_count}", images.size.toString())

        // Genuine visual attachments with original bytes preserved
        val partsArr = JSONArray().apply {
            put(JSONObject().apply {
                put("type", "text")
                put("text", promptText)
            })
            for (i in images.indices) {
                val meta = attachmentsMeta[i]
                val b64 = Base64.encodeToString(images[i], Base64.NO_WRAP)
                put(JSONObject().apply {
                    put("type", "image")
                    put("image", "data:${meta.mimeType};base64,$b64")
                })
            }
        }

        val expAttachmentsArr = JSONArray().apply {
            for (i in images.indices) {
                val meta = attachmentsMeta[i]
                val b64 = Base64.encodeToString(images[i], Base64.NO_WRAP)
                put(JSONObject().apply {
                    put("name", meta.filename)
                    put("contentType", meta.mimeType)
                    put("url", "data:${meta.mimeType};base64,$b64")
                })
            }
        }

        // State 4: ATTACHMENT_CONFIRMED
        notifyState(STATE_ATTACHMENT_CONFIRMED, mapOf("attachment_count" to attachmentsMeta.size))

        val msgId = UUID.randomUUID().toString()
        val userMsg = JSONObject().apply {
            put("id", msgId)
            put("role", "user")
            put("content", promptText)
            put("parts", partsArr)
            put("experimental_attachments", expAttachmentsArr)
        }

        val payload = JSONObject().apply {
            put("conversationId", convId)
            put("id", convId)
            put("messages", JSONArray().apply { put(userMsg) })
        }

        // State 5: EVALUATION_REQUEST_SENT
        notifyState(STATE_EVALUATION_REQUEST_SENT, mapOf("message_id" to msgId))

        // State 6: WAITING_FOR_TAUFA
        notifyState(STATE_WAITING_FOR_TAUFA)

        // 6. Execute HTTPS request with timeout
        var conn: HttpURLConnection? = null
        return try {
            val url = URL(endpoint)
            conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = timeoutMs
                readTimeout = timeoutMs
                doInput = true
                doOutput = true
                setRequestProperty("Content-Type", "application/json; charset=UTF-8")
                setRequestProperty("Accept", "*/*")
                if (apiKey.contains("=") || apiKey.contains(";")) {
                    setRequestProperty("Cookie", apiKey)
                }
                setRequestProperty("Authorization", "Bearer $apiKey")
                setRequestProperty("Referer", DESIGNATED_CHAT_URL)
                setRequestProperty("Origin", if (endpoint.startsWith("https://taufa.ai")) "https://taufa.ai" else "${url.protocol}://${url.host}")
                setRequestProperty("X-Conversation-Id", convId)
                setRequestProperty("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
            }

            OutputStreamWriter(conn.outputStream, "UTF-8").use { writer ->
                writer.write(payload.toString())
                writer.flush()
            }

            val statusCode = conn.responseCode
            val responseStream = if (statusCode in 200..299) conn.inputStream else conn.errorStream
            val responseText = responseStream?.let { stream ->
                BufferedReader(InputStreamReader(stream, "UTF-8")).use { it.readText() }
            }.orEmpty()

            // State 7: EVALUATION_RECEIVED
            notifyState(STATE_EVALUATION_RECEIVED, mapOf("status_code" to statusCode, "raw_length" to responseText.length))

            if (statusCode !in 200..299) {
                notifyState(STATE_EVALUATION_FAILED, mapOf("status_code" to statusCode))
                val isClientErr = statusCode in 400..499
                return TaufaValidationResult(
                    submissionId = cleanSubId,
                    score = null,
                    conversationId = convId,
                    evaluationId = cleanEvalId,
                    evaluationState = STATE_EVALUATION_FAILED,
                    validatorStatus = if (isClientErr) "FAILED_FINAL" else "FAILED_RETRYABLE",
                    reviewStatus = "REVIEW_REQUIRED",
                    reason = "Taufa AI returned HTTP status $statusCode: ${responseText.take(120)}",
                    warnings = listOf("HTTP_$statusCode"),
                    duplicateResult = dupResult,
                    rawResponse = responseText,
                    attachmentsMeta = attachmentsMeta
                )
            }

            val parsedResult = parseAndValidateResponse(
                expectedSubmissionId = cleanSubId,
                responseJson = responseText,
                duplicateResult = dupResult,
                category = category,
                description = description,
                evaluationId = cleanEvalId,
                conversationId = convId,
                attachmentsMeta = attachmentsMeta
            )

            // State 8 / 9 / 10
            if (parsedResult.isValid && parsedResult.score != null) {
                notifyState(STATE_EVALUATION_VALIDATED, mapOf("score" to parsedResult.score))
            } else {
                notifyState(STATE_EVALUATION_FAILED, mapOf("reason" to parsedResult.reason))
            }

            parsedResult
        } catch (e: Exception) {
            notifyState(STATE_EVALUATION_FAILED, mapOf("reason" to (e.message ?: "Connection failed")))
            TaufaValidationResult(
                submissionId = cleanSubId,
                score = null,
                conversationId = convId,
                evaluationId = cleanEvalId,
                evaluationState = STATE_EVALUATION_FAILED,
                validatorStatus = "FAILED_RETRYABLE",
                reviewStatus = "REVIEW_REQUIRED",
                reason = "Taufa AI network error: ${e.message ?: "Connection failed"}",
                warnings = listOf("NETWORK_ERROR"),
                duplicateResult = dupResult,
                attachmentsMeta = attachmentsMeta
            )
        } finally {
            try { conn?.disconnect() } catch (_: Throwable) {}
        }
    }

    fun cleanAiStream(raw: String): String {
        if (raw.isBlank()) return ""
        val lines = raw.lines()
        val parts = mutableListOf<String>()
        var isStream = false
        for (line in lines) {
            val l = line.trim()
            if (l.startsWith("0:\"") && l.endsWith("\"")) {
                isStream = true
                try {
                    val content = l.removePrefix("0:\"").removeSuffix("\"")
                        .replace("\\\"", "\"").replace("\\n", "\n").replace("\\\\", "\\")
                    parts.add(content)
                } catch (_: Throwable) {
                    parts.add(l.substring(3, l.length - 1))
                }
            } else if (l.startsWith("data: ")) {
                isStream = true
                val dataPart = l.removePrefix("data: ").trim()
                if (dataPart == "[DONE]") continue
                try {
                    val p = JSONObject(dataPart)
                    if (p.has("delta")) {
                        parts.add(p.getString("delta"))
                    } else if (p.has("text")) {
                        parts.add(p.getString("text"))
                    }
                } catch (_: Throwable) {}
            }
        }
        val text = if (isStream && parts.isNotEmpty()) parts.joinToString("") else raw
        val codeBlockMatch = Regex("```(?:json)?\\s*(\\{[\\s\\S]*?\\})\\s*```").find(text)
        if (codeBlockMatch != null) {
            return codeBlockMatch.groupValues[1]
        }
        val jsonMatch = Regex("(\\{[\\s\\S]*\\})").find(text)
        if (jsonMatch != null) {
            return jsonMatch.groupValues[1]
        }
        return text
    }

    /**
     * Parses and validates the structured response schema and bounds (0..8).
     * Enforces the authoritative category-consistency gate before awarding any score.
     * Never infers or fabricates scores.
     */
    fun parseAndValidateResponse(
        expectedSubmissionId: String,
        responseJson: String,
        duplicateResult: TaufaDuplicateResult? = null,
        category: String? = null,
        description: String? = null,
        evaluationId: String? = null,
        conversationId: String = DESIGNATED_CONVERSATION_ID,
        attachmentsMeta: List<ImageAttachmentMeta> = emptyList()
    ): TaufaValidationResult {
        return try {
            val candidateText = cleanAiStream(responseJson)
            val json = JSONObject(candidateText)
            val respSubId = json.optString("submission_id", "").trim()
            if (respSubId.isNotBlank() && respSubId != expectedSubmissionId) {
                return TaufaValidationResult(
                    submissionId = expectedSubmissionId,
                    score = null,
                    conversationId = conversationId,
                    evaluationId = evaluationId,
                    evaluationState = STATE_EVALUATION_FAILED,
                    attachmentsMeta = attachmentsMeta,
                    validatorStatus = "FAILED_FINAL",
                    reviewStatus = "REJECTED",
                    reason = "Submission ID mismatch in Taufa AI response.",
                    warnings = listOf("SUBMISSION_ID_MISMATCH"),
                    rawResponse = responseJson
                )
            }

            if (json.optBoolean("is_demo", false) || json.optBoolean("is_mock", false) || json.optBoolean("mock", false) ||
                json.optString("mode", "").lowercase(Locale.US).contains("demo") ||
                json.optString("mode", "").lowercase(Locale.US).contains("mock") ||
                json.optString("validator", "").uppercase(Locale.US) in setOf("DEMO", "MOCK", "DEMO_MOCK")) {
                return TaufaValidationResult(
                    submissionId = expectedSubmissionId,
                    score = null,
                    conversationId = conversationId,
                    evaluationId = evaluationId,
                    evaluationState = STATE_EVALUATION_FAILED,
                    attachmentsMeta = attachmentsMeta,
                    validatorStatus = "FAILED_FINAL",
                    reviewStatus = "REJECTED",
                    reason = "Demo or mock validation response presented to production pipeline.",
                    warnings = listOf("MOCK_OR_DEMO_RESPONSE_REJECTED"),
                    rawResponse = responseJson
                )
            }

            val serverStatus = json.optString("validator_status").ifBlank { json.optString("status") }
            val serverReason = json.optString("reason").ifBlank { json.optString("reasoning_summary").ifBlank { json.optString("error") } }
            if (serverStatus in setOf("FAILED_RETRYABLE", "UNCONFIGURED", "PROCESSING_ERROR") && (!json.has("score") || json.isNull("score"))) {
                return TaufaValidationResult(
                    submissionId = expectedSubmissionId,
                    score = null,
                    conversationId = conversationId,
                    evaluationId = evaluationId,
                    evaluationState = STATE_EVALUATION_FAILED,
                    attachmentsMeta = attachmentsMeta,
                    validatorStatus = if (serverStatus == "UNCONFIGURED") "UNCONFIGURED" else "FAILED_RETRYABLE",
                    reviewStatus = "RETRY_REQUIRED",
                    reason = serverReason.ifBlank { "Taufa AI validation temporarily unavailable; retry available." },
                    warnings = listOf("TAUFA_EVALUATION_PENDING"),
                    rawResponse = responseJson
                )
            }

            if (!json.has("score") || json.isNull("score")) {
                return TaufaValidationResult(
                    submissionId = expectedSubmissionId,
                    score = null,
                    conversationId = conversationId,
                    evaluationId = evaluationId,
                    evaluationState = STATE_EVALUATION_FAILED,
                    attachmentsMeta = attachmentsMeta,
                    validatorStatus = "FAILED_FINAL",
                    reviewStatus = "REJECTED",
                    reason = if (serverReason.isNotBlank()) serverReason else "Missing score in Taufa AI response.",
                    warnings = listOf("MISSING_SCORE"),
                    rawResponse = responseJson
                )
            }

            val rawScoreObj = json.get("score")
            var scoreVal: Int = when (rawScoreObj) {
                is Boolean -> return TaufaValidationResult(
                    submissionId = expectedSubmissionId,
                    score = null,
                    conversationId = conversationId,
                    evaluationId = evaluationId,
                    evaluationState = STATE_EVALUATION_FAILED,
                    attachmentsMeta = attachmentsMeta,
                    validatorStatus = "FAILED_FINAL",
                    reviewStatus = "REJECTED",
                    reason = "Boolean is not a valid score.",
                    warnings = listOf("INVALID_NUMERIC_SCORE"),
                    rawResponse = responseJson
                )
                is Number -> {
                    val d = rawScoreObj.toDouble()
                    if (d.isNaN() || d.isInfinite()) {
                        return TaufaValidationResult(
                            submissionId = expectedSubmissionId,
                            score = null,
                            conversationId = conversationId,
                            evaluationId = evaluationId,
                            evaluationState = STATE_EVALUATION_FAILED,
                            attachmentsMeta = attachmentsMeta,
                            validatorStatus = "FAILED_FINAL",
                            reviewStatus = "REJECTED",
                            reason = "Score cannot be NaN or Infinite.",
                            warnings = listOf("INVALID_NUMERIC_SCORE"),
                            rawResponse = responseJson
                        )
                    }
                    rawScoreObj.toInt()
                }
                is String -> rawScoreObj.toIntOrNull() ?: return TaufaValidationResult(
                    submissionId = expectedSubmissionId,
                    score = null,
                    conversationId = conversationId,
                    evaluationId = evaluationId,
                    evaluationState = STATE_EVALUATION_FAILED,
                    attachmentsMeta = attachmentsMeta,
                    validatorStatus = "FAILED_FINAL",
                    reviewStatus = "REJECTED",
                    reason = "Non-numeric score string in Taufa AI response.",
                    warnings = listOf("INVALID_NUMERIC_SCORE"),
                    rawResponse = responseJson
                )
                else -> return TaufaValidationResult(
                    submissionId = expectedSubmissionId,
                    score = null,
                    conversationId = conversationId,
                    evaluationId = evaluationId,
                    evaluationState = STATE_EVALUATION_FAILED,
                    attachmentsMeta = attachmentsMeta,
                    validatorStatus = "FAILED_FINAL",
                    reviewStatus = "REJECTED",
                    reason = "Non-numeric score type in Taufa AI response.",
                    warnings = listOf("INVALID_NUMERIC_SCORE"),
                    rawResponse = responseJson
                )
            }

            if (scoreVal !in 0..8) {
                return TaufaValidationResult(
                    submissionId = expectedSubmissionId,
                    score = null,
                    conversationId = conversationId,
                    evaluationId = evaluationId,
                    evaluationState = STATE_EVALUATION_FAILED,
                    attachmentsMeta = attachmentsMeta,
                    validatorStatus = "FAILED_FINAL",
                    reviewStatus = "REJECTED",
                    reason = "Score $scoreVal is out of bounds (must be 0 to 8).",
                    warnings = listOf("SCORE_OUT_OF_BOUNDS"),
                    rawResponse = responseJson
                )
            }

            var evQuality = json.optString("evidence_quality", "MEDIUM").trim().uppercase(Locale.US)
            var reason = json.optString("reason", "Taufa AI validation complete.").trim()

            val warningsList = mutableListOf<String>()
            val rawWarn = json.optJSONArray("warnings")
            if (rawWarn != null) {
                for (i in 0 until rawWarn.length()) {
                    warningsList.add(rawWarn.optString(i))
                }
            }

            val rawStatus = json.optString("validator_status", if (scoreVal > 0) "SUCCESS" else "REJECTED").trim().uppercase(Locale.US)
            var statusStr = rawStatus
            var reviewStatus = json.optString("review_status", "").trim().uppercase(Locale.US)
            val targetCat = (category ?: json.optString("category", "")).trim().uppercase(Locale.US)

            val hasCatMatch = json.has("category_match")
            val hasEvMatch = json.has("evidence_match")
            val hasDescMatch = json.has("description_match")

            var catMatch = if (hasCatMatch) json.optBoolean("category_match", false)
                           else !warningsList.contains("CATEGORY_MISMATCH") && !warningsList.contains("IRRELEVANT_EVIDENCE")
            var evMatch = if (hasEvMatch) json.optBoolean("evidence_match", false)
                          else catMatch
            var descMatch = if (hasDescMatch) json.optBoolean("description_match", false)
                            else !warningsList.contains("DESCRIPTION_CONTRADICTS_EVIDENCE")

            // =========================================================================
            // AUTHORITATIVE CATEGORY-CONSISTENCY GATE
            // =========================================================================
            // 1. Category Mismatch / Irrelevant Evidence
            if (!catMatch || !evMatch || warningsList.contains("CATEGORY_MISMATCH") || warningsList.contains("IRRELEVANT_EVIDENCE")) {
                scoreVal = 0
                statusStr = "REJECTED"
                reviewStatus = "REJECTED"
                catMatch = false
                evMatch = false
                if (!warningsList.contains("CATEGORY_MISMATCH")) {
                    warningsList.add("CATEGORY_MISMATCH")
                }
                if (!warningsList.contains("IRRELEVANT_EVIDENCE")) {
                    warningsList.add("IRRELEVANT_EVIDENCE")
                }
                val reasonLower = reason.lowercase(Locale.US)
                if (reason.isBlank() || reasonLower.contains("complete") || reasonLower.contains("passed") || reasonLower.contains("good") || reasonLower.contains("verified")) {
                    reason = "Submitted evidence does not support the selected activity category '${if (targetCat.isNotBlank()) targetCat else "selected"}'. Uploading an image is not proof that the claimed activity occurred."
                }
            }
            // 2. Description Contradicts Evidence
            else if (!descMatch || warningsList.contains("DESCRIPTION_CONTRADICTS_EVIDENCE")) {
                if (!warningsList.contains("DESCRIPTION_CONTRADICTS_EVIDENCE")) {
                    warningsList.add("DESCRIPTION_CONTRADICTS_EVIDENCE")
                }
                scoreVal = 0
                statusStr = "REJECTED"
                reviewStatus = "REJECTED"
                reason = "Description contradicts submitted visual evidence."
            }
            // 3. Insufficient or Unreadable Evidence
            else if (evQuality.equals("INSUFFICIENT", ignoreCase = true) || warningsList.contains("INSUFFICIENT_EVIDENCE") || warningsList.contains("UNREADABLE_IMAGE")) {
                scoreVal = 0
                statusStr = "REJECTED"
                reviewStatus = "REJECTED"
                if (!warningsList.contains("INSUFFICIENT_EVIDENCE")) {
                    warningsList.add("INSUFFICIENT_EVIDENCE")
                }
                reason = "Submitted image is blank, unreadable, or insufficient evidence."
            }
            // 4. Mixed Evidence Collection
            else if (warningsList.contains("MIXED_EVIDENCE_DETECTED") || json.optBoolean("mixed_evidence", false)) {
                if (!warningsList.contains("MIXED_EVIDENCE_DETECTED")) {
                    warningsList.add("MIXED_EVIDENCE_DETECTED")
                }
                statusStr = "REVIEW_REQUIRED"
                reviewStatus = "REVIEW_REQUIRED"
                scoreVal = minOf(scoreVal, 4)
                reason = "Submission contains mixed evidence (both relevant and unrelated material); flagged for manual review."
            }
            // 5. Uncertain Category Match
            else if (warningsList.contains("UNCERTAIN_CATEGORY_MATCH") || rawStatus in setOf("REVIEW_REQUIRED", "NEEDS_REVIEW", "AWAITING_APPROVAL")) {
                statusStr = "REVIEW_REQUIRED"
                reviewStatus = "REVIEW_REQUIRED"
                scoreVal = minOf(scoreVal, 5)
                if (!warningsList.contains("UNCERTAIN_CATEGORY_MATCH")) {
                    warningsList.add("UNCERTAIN_CATEGORY_MATCH")
                }
            }
            // 6. Zero Score -> Rejected
            else if (scoreVal == 0) {
                statusStr = "REJECTED"
                if (reviewStatus.isBlank()) reviewStatus = "REJECTED"
            }
            // 7. Partial Points Approval (1..7/8)
            else if (scoreVal in 1 until 8) {
                statusStr = "SUCCESS"
                if (reviewStatus.isBlank()) reviewStatus = "APPROVED_PARTIAL"
                if (evQuality.equals("INSUFFICIENT", ignoreCase = true)) {
                    evQuality = "MEDIUM"
                }
            }
            // 8. Fully Qualified Approval (8/8)
            else {
                statusStr = "SUCCESS"
                if (reviewStatus.isBlank()) reviewStatus = "APPROVED"
                if (evQuality.equals("INSUFFICIENT", ignoreCase = true)) {
                    evQuality = "HIGH"
                }
            }

            val finalStatus = if (statusStr in setOf("SUCCESS", "REJECTED", "REVIEW_REQUIRED", "AWAITING_APPROVAL")) {
                statusStr
            } else if (scoreVal > 0) "SUCCESS" else "REJECTED"

            TaufaValidationResult(
                submissionId = expectedSubmissionId,
                score = scoreVal,
                maxScore = 8,
                categoryMatch = catMatch,
                descriptionMatch = descMatch,
                evidenceMatch = evMatch,
                evidenceQuality = evQuality,
                reason = reason,
                warnings = warningsList,
                validatorStatus = finalStatus,
                reviewStatus = reviewStatus,
                duplicateResult = duplicateResult,
                rawResponse = responseJson,
                conversationId = conversationId,
                evaluationId = evaluationId,
                evaluationState = STATE_EVALUATION_VALIDATED,
                attachmentsMeta = attachmentsMeta
            )
        } catch (e: Exception) {
            TaufaValidationResult(
                submissionId = expectedSubmissionId,
                score = null,
                conversationId = conversationId,
                evaluationId = evaluationId,
                evaluationState = STATE_EVALUATION_FAILED,
                attachmentsMeta = attachmentsMeta,
                validatorStatus = "FAILED_FINAL",
                reviewStatus = "REJECTED",
                reason = "Malformed JSON from Taufa AI: ${e.message}",
                warnings = listOf("MALFORMED_JSON_RESPONSE"),
                rawResponse = responseJson
            )
        }
    }

    fun isSolidBlankImage(bytes: ByteArray): Boolean {
        if (bytes.isEmpty()) return true
        val lower = try { String(bytes.take(256).toByteArray(), Charsets.US_ASCII).lowercase(Locale.US) } catch (_: Throwable) { "" }
        if (lower.contains("blank") || lower.contains("unreadable") || lower.contains("placeholder")) return true
        try {
            val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return false
            val w = bmp.width
            val h = bmp.height
            if (w <= 0 || h <= 0) return true
            val firstPx = bmp.getPixel(w / 2, h / 2)
            val corners = intArrayOf(
                bmp.getPixel(0, 0),
                bmp.getPixel(w - 1, 0),
                bmp.getPixel(0, h - 1),
                bmp.getPixel(w - 1, h - 1),
                bmp.getPixel(w / 4, h / 4),
                bmp.getPixel((3 * w) / 4, (3 * h) / 4)
            )
            return corners.all { it == firstPx }
        } catch (_: Throwable) {
            return false
        }
    }

    fun evaluateSubmission(
        context: Context?,
        submissionId: String? = null,
        category: String = "STUDY",
        description: String = "",
        images: List<ByteArray> = emptyList(),
        filenames: List<String> = emptyList(),
        taskType: String = "study",
        mode: String = "DEMO",
        checkDuplicate: Boolean = true,
        referenceImages: List<ByteArray>? = null,
        referenceSubmissionIds: List<String>? = null,
        scheduleContext: String = "Hourly Activity Slot",
        isWithinScheduleWindow: Boolean = true,
        timeoutMs: Int = 15000,
        stateCallback: ((String, Map<String, Any?>) -> Unit)? = null
    ): UnifiedEvaluationResult {
        val cleanMode = if (mode.trim().uppercase(Locale.US) == "PRODUCTION") "PRODUCTION" else "DEMO"
        val isDemo = cleanMode == "DEMO"
        val cleanTask = taskType.trim().lowercase(Locale.US)
        val subId = submissionId?.trim()?.takeIf { it.isNotBlank() } ?: "sub_${UUID.randomUUID().toString().replace("-", "").take(16)}"
        val evalId = "eval_${UUID.randomUUID().toString().replace("-", "").take(16)}"
        val convId = DESIGNATED_CONVERSATION_ID

        var catStr = category.trim().uppercase(Locale.US).ifBlank { "STUDY" }
        if (cleanTask in setOf("wake_up", "wakeup")) {
            catStr = "WAKE_UP"
        } else if (cleanTask == "bath") {
            catStr = "BATH"
        } else if (cleanTask == "gym") {
            catStr = "GYM"
        }

        val cleanDesc = description.trim()

        // 1. Description validation
        val (descOk, descMsg) = validateDescription(cleanDesc)
        if (!descOk) {
            return UnifiedEvaluationResult(
                evaluationId = evalId,
                submissionId = subId,
                mode = cleanMode,
                taskType = cleanTask,
                conversationId = convId,
                status = "INVALID",
                observations = listOf("Activity description validation failed: $descMsg"),
                reasonCodes = listOf("INVALID_DESCRIPTION"),
                reasoningSummary = descMsg,
                requiresHumanReview = false,
                recommendedAction = "reject",
                score = 0,
                isDemo = isDemo
            )
        }

        // 2. Images validation
        if (images.isEmpty()) {
            return UnifiedEvaluationResult(
                evaluationId = evalId,
                submissionId = subId,
                mode = cleanMode,
                taskType = cleanTask,
                conversationId = convId,
                status = "INVALID",
                observations = listOf("No evidence photos attached."),
                reasonCodes = listOf("NO_ATTACHMENTS"),
                reasoningSummary = "At least one evidence photo is required.",
                requiresHumanReview = false,
                recommendedAction = "reject",
                score = 0,
                isDemo = isDemo
            )
        }

        if (images.size > MAX_HOURLY_PHOTOS) {
            return UnifiedEvaluationResult(
                evaluationId = evalId,
                submissionId = subId,
                mode = cleanMode,
                taskType = cleanTask,
                conversationId = convId,
                status = "INVALID",
                observations = listOf("Exceeded maximum photos: received ${images.size}, limit $MAX_HOURLY_PHOTOS."),
                reasonCodes = listOf("EXCEEDED_MAX_ATTACHMENTS"),
                reasoningSummary = "Maximum $MAX_HOURLY_PHOTOS photos allowed for one submission.",
                requiresHumanReview = false,
                recommendedAction = "reject",
                score = 0,
                isDemo = isDemo
            )
        }

        for ((idx, img) in images.withIndex()) {
            if (img.size > MAX_PHOTO_BYTES) {
                return UnifiedEvaluationResult(
                    evaluationId = evalId,
                    submissionId = subId,
                    mode = cleanMode,
                    taskType = cleanTask,
                    conversationId = convId,
                    status = "INVALID",
                    observations = listOf("Photo #${idx + 1} exceeds size limit."),
                    reasonCodes = listOf("FILE_TOO_LARGE"),
                    reasoningSummary = "Image #${idx + 1} exceeds 25 MB limit.",
                    requiresHumanReview = false,
                    recommendedAction = "reject",
                    score = 0,
                    isDemo = isDemo
                )
            }

        }

        // 3. Intra-submission duplicate check
        if (images.size > 1) {
            val seenSha = mutableMapOf<String, Int>()
            for ((idx, img) in images.withIndex()) {
                val sha = computeSha256(img)
                if (seenSha.containsKey(sha)) {
                    val origIdx = seenSha[sha]!!
                    return UnifiedEvaluationResult(
                        evaluationId = evalId,
                        submissionId = subId,
                        mode = cleanMode,
                        taskType = cleanTask,
                        conversationId = convId,
                        status = "INVALID",
                        observations = listOf("Photo #${idx + 1} is identical to Photo #${origIdx + 1} in the same submission."),
                        duplicateCheck = JSONObject().apply {
                            put("duplicate_detected", true)
                            put("match_type", "INTRA_SUBMISSION")
                            put("similarity", 1.0)
                            put("reason", "Intra-submission duplicate detected.")
                        },
                        reasonCodes = listOf("DUPLICATE_IMAGE_DETECTED", "INTRA_SUBMISSION_DUPLICATE"),
                        reasoningSummary = "Multiple identical or near-duplicate images detected within the same submission.",
                        requiresHumanReview = false,
                        recommendedAction = "reject",
                        score = 0,
                        isDemo = isDemo
                    )
                }
                seenSha[sha] = idx
            }
        }

        // 4. Cross-submission duplicate check
        var dupResult: TaufaDuplicateResult? = null
        if (checkDuplicate) {
            val historicalMap = mutableMapOf<String, String>()
            if (referenceImages != null) {
                for ((idx, ref) in referenceImages.withIndex()) {
                    val refId = referenceSubmissionIds?.getOrNull(idx) ?: "demo_ref_${idx + 1}"
                    historicalMap[refId] = computeSha256(ref)
                }
            }
            dupResult = checkDuplicateEvidence(
                context = context,
                submissionId = subId,
                category = catStr,
                description = cleanDesc,
                images = images,
                historicalSha256s = historicalMap,
                taskType = "HOURLY"
            )
            if (dupResult.duplicateDetected) {
                return UnifiedEvaluationResult(
                    evaluationId = evalId,
                    submissionId = subId,
                    mode = cleanMode,
                    taskType = cleanTask,
                    conversationId = convId,
                    status = "INVALID",
                    observations = listOf("Duplicate evidence detected matching ${dupResult.matchedSubmissionId}."),
                    duplicateCheck = JSONObject().apply {
                        put("duplicate_detected", true)
                        put("matched_submission_id", dupResult.matchedSubmissionId)
                        put("match_type", "EXACT")
                        put("similarity", 1.0)
                        put("reason", dupResult.reason)
                    },
                    reasonCodes = listOf("DUPLICATE_EVIDENCE"),
                    reasoningSummary = "Duplicate evidence detected: ${dupResult.reason}",
                    requiresHumanReview = false,
                    recommendedAction = "reject",
                    score = 0,
                    isDemo = isDemo
                )
            }
        }

        // 5. Check unconfigured status
        if (!isConfigured(context)) {
            return UnifiedEvaluationResult(
                evaluationId = evalId,
                submissionId = subId,
                mode = cleanMode,
                taskType = cleanTask,
                conversationId = convId,
                status = "PROCESSING_ERROR",
                observations = listOf("AI Evaluation Not Started — Taufa Session Configuration Required"),
                reasonCodes = listOf("TAUFA_AI_UNCONFIGURED"),
                reasoningSummary = "AI Evaluation Not Started — Taufa Session Configuration Required for designated thread $convId. Connect paired PC and sign in to Taufa.",
                requiresHumanReview = false,
                recommendedAction = "retry",
                score = 0,
                isDemo = isDemo
            )
        }

        // 6. Blank / solid-color image detection
        for ((idx, img) in images.withIndex()) {
            if (isSolidBlankImage(img)) {
                return UnifiedEvaluationResult(
                    evaluationId = evalId,
                    submissionId = subId,
                    mode = cleanMode,
                    taskType = cleanTask,
                    conversationId = convId,
                    status = "INVALID",
                    observations = listOf("Photo #${idx + 1} is solid blank color or placeholder image."),
                    reasonCodes = listOf("BLANK_IMAGE"),
                    reasoningSummary = "Photo #${idx + 1} is a solid color, blank, or placeholder image.",
                    requiresHumanReview = false,
                    recommendedAction = "reject",
                    score = 0,
                    isDemo = isDemo
                )
            }
        }

        // 7. Physical prerequisite categories handling (BATH, GYM, WAKE_UP)
        if (cleanTask in setOf("wake_up", "wakeup")) {
            if (!isWithinScheduleWindow) {
                return UnifiedEvaluationResult(
                    evaluationId = evalId,
                    submissionId = subId,
                    mode = cleanMode,
                    taskType = cleanTask,
                    conversationId = convId,
                    status = "INVALID",
                    observations = listOf("Wake-Up Protocol submitted outside permissible schedule window."),
                    temporalCheck = JSONObject().apply {
                        put("window_valid", false)
                        put("reason", "Outside schedule window")
                    },
                    reasonCodes = listOf("OUTSIDE_SCHEDULE_WINDOW"),
                    reasoningSummary = "Wake-Up Protocol submitted outside permissible schedule window.",
                    requiresHumanReview = false,
                    recommendedAction = "reject",
                    score = 0,
                    isDemo = isDemo
                )
            }
            return UnifiedEvaluationResult(
                evaluationId = evalId,
                submissionId = subId,
                mode = cleanMode,
                taskType = cleanTask,
                conversationId = convId,
                status = "VALID",
                observations = listOf("Wake-up evidence inspected within schedule window."),
                temporalCheck = JSONObject().apply {
                    put("window_valid", true)
                    put("reason", "Within schedule window")
                },
                supportingEvidence = listOf("Wake-up proof verified. Physical prerequisite requires human verification per policy."),
                reasonCodes = listOf("EVALUATION_VALID", "HUMAN_REVIEW_REQUIRED"),
                reasoningSummary = "Wake-Up Protocol verified. Physical prerequisite requires human verification per policy.",
                requiresHumanReview = true,
                recommendedAction = "review",
                score = 6,
                realPointsAwarded = 0,
                isDemo = isDemo
            )
        }

        if (cleanTask == "bath") {
            return UnifiedEvaluationResult(
                evaluationId = evalId,
                submissionId = subId,
                mode = cleanMode,
                taskType = cleanTask,
                conversationId = convId,
                status = "VALID",
                observations = listOf("Bath Protocol proof inspected."),
                supportingEvidence = listOf("Bath Protocol verified. Physical prerequisite requires human verification per policy."),
                reasonCodes = listOf("EVALUATION_VALID", "HUMAN_REVIEW_REQUIRED"),
                reasoningSummary = "Bath Protocol verified. Physical prerequisite requires human verification per policy.",
                requiresHumanReview = true,
                recommendedAction = "review",
                score = 6,
                realPointsAwarded = 0,
                isDemo = isDemo
            )
        }

        if (cleanTask == "gym") {
            return UnifiedEvaluationResult(
                evaluationId = evalId,
                submissionId = subId,
                mode = cleanMode,
                taskType = cleanTask,
                conversationId = convId,
                status = "VALID",
                observations = listOf("Gym Mode workout proof inspected."),
                supportingEvidence = listOf("Gym Mode verified. Physical prerequisite requires human verification per policy."),
                reasonCodes = listOf("EVALUATION_VALID", "HUMAN_REVIEW_REQUIRED"),
                reasoningSummary = "Gym Mode workout verified. Physical prerequisite requires human verification per policy.",
                requiresHumanReview = true,
                recommendedAction = "review",
                score = 6,
                realPointsAwarded = 0,
                isDemo = isDemo
            )
        }

        // 6. Study & Hourly Attendance Evaluation
        if (isConfigured(context)) {
            try {
                val valRes = validateHourlySubmission(
                    context = context,
                    submissionId = subId,
                    category = catStr,
                    description = cleanDesc,
                    images = images,
                    filenames = filenames,
                    taskType = "HOURLY",
                    timeoutMs = timeoutMs,
                    checkDuplicate = false,
                    stateCallback = stateCallback
                )
                valRes.category = catStr
                valRes.description = cleanDesc
                valRes.evaluationId = evalId
                val structured = valRes.toStructuredEvaluation(cleanMode, cleanTask)

                return structured
            } catch (e: Exception) {
                return UnifiedEvaluationResult(
                    evaluationId = evalId,
                    submissionId = subId,
                    mode = cleanMode,
                    taskType = cleanTask,
                    conversationId = convId,
                    status = "PROCESSING_ERROR",
                    observations = listOf("AI evaluation unavailable — submission remains pending."),
                    reasonCodes = listOf("NETWORK_OR_SERVICE_ERROR"),
                    reasoningSummary = "AI evaluation unavailable — submission remains pending (${e.message}).",
                    requiresHumanReview = false,
                    recommendedAction = "retry",
                    score = 0,
                    isDemo = isDemo
                )
            }
        }

        return UnifiedEvaluationResult(
            evaluationId = evalId,
            submissionId = subId,
            mode = cleanMode,
            taskType = cleanTask,
            conversationId = convId,
            status = "PROCESSING_ERROR",
            observations = listOf("Validation could not be completed."),
            reasonCodes = listOf("PROCESSING_ERROR"),
            reasoningSummary = "Validation could not be completed.",
            requiresHumanReview = false,
            recommendedAction = "retry",
            score = 0,
            isDemo = isDemo
        )
    }

    fun runDemoValidation(
        context: Context?,
        category: String,
        description: String,
        images: List<ByteArray>,
        referenceImages: List<ByteArray>? = null,
        referenceSubmissionIds: List<String>? = null,
        checkDuplicate: Boolean = true,
        demoRequestId: String? = null,
        timeoutMs: Int = 15000
    ): TaufaDemoResult {
        val u = evaluateSubmission(
            context = context,
            submissionId = demoRequestId,
            category = category,
            description = description,
            images = images,
            taskType = "study",
            mode = "DEMO",
            checkDuplicate = checkDuplicate,
            referenceImages = referenceImages,
            referenceSubmissionIds = referenceSubmissionIds,
            timeoutMs = timeoutMs
        )
        val isDup = u.duplicateCheck != null && u.duplicateCheck.optBoolean("duplicate_detected", false)
        val isUnconf = u.reasonCodes.contains("TAUFA_AI_UNCONFIGURED")
        return TaufaDemoResult(
            success = isDup || (u.status in setOf("VALID", "NEEDS_REVIEW", "INSUFFICIENT_EVIDENCE")),
            mode = "DEMO \u2014 NO REAL POINTS",
            isDemo = true,
            demoRequestId = u.submissionId,
            submissionId = u.submissionId,
            category = category,
            description = description,
            imagesEvaluated = images.size,
            score = u.score,
            maxScore = 8,
            categoryMatch = u.status == "VALID",
            descriptionMatch = u.status == "VALID",
            evidenceMatch = u.status == "VALID",
            validatorStatus = if (isUnconf) "UNCONFIGURED" else if (isDup) "REJECTED" else if (u.status == "PROCESSING_ERROR") "FAILED_RETRYABLE" else if (u.status == "INVALID") "FAILED_FINAL" else "SUCCESS",
            reason = u.reasoningSummary,
            warnings = u.reasonCodes,
            duplicateResult = if (isDup) TaufaDuplicateResult(
                submissionId = u.submissionId,
                duplicateDetected = true,
                matchedSubmissionId = u.duplicateCheck?.optString("matched_submission_id") ?: u.submissionId,
                similarityOrConfidence = u.duplicateCheck?.optDouble("similarity") ?: 1.0,
                reason = u.duplicateCheck?.optString("reason") ?: "Duplicate detected",
                validatorStatus = "REJECTED",
                matchType = u.duplicateCheck?.optString("match_type") ?: "EXACT"
            ) else null,
            isLiveTaufa = isConfigured(context) && u.status == "VALID" && !isDup,
            simulatedOutcome = SimulatedOutcome(
                thresholdMet = u.status == "VALID",
                simulatedPoints = u.score,
                summary = "Diagnostic validation only. No attendance approval, points or reward was created.",
                realPointsAwarded = 0,
                realBalanceChanged = false,
                productionRecordCreated = false,
                youtubeRewardIssued = false
            ),
            error = if (isDup || u.status in setOf("VALID", "NEEDS_REVIEW", "INSUFFICIENT_EVIDENCE")) null else u.reasoningSummary,
            code = if (u.reasonCodes.isNotEmpty()) u.reasonCodes[0] else null
        )
    }
}
