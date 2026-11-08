package com.strictrecords.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors

/**
 * ProofEvaluationManager coordinates:
 * - Stage A: Immediate durable submission acceptance (<50ms, zero UI thread blocking).
 * - Stage B: Asynchronous background proof evaluation (OCR, duplicate checking, tampering, scoring).
 *
 * Supported task domains:
 * 1. Hourly Attendance proof submissions.
 * 2. Gym proof submissions.
 * 3. Mi Fitness / smartwatch sleep-data submissions.
 */
object ProofEvaluationManager {

    private val bgExecutor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val listeners = CopyOnWriteArrayList<(ProofSubmissionRules.EvaluationEvent) -> Unit>()
    private val inFlightSubmissions = ConcurrentHashMap<String, Long>()

    fun addListener(listener: (ProofSubmissionRules.EvaluationEvent) -> Unit) {
        if (!listeners.contains(listener)) {
            listeners.add(listener)
        }
    }

    fun removeListener(listener: (ProofSubmissionRules.EvaluationEvent) -> Unit) {
        listeners.remove(listener)
    }

    private fun notifyListeners(event: ProofSubmissionRules.EvaluationEvent) {
        mainHandler.post {
            for (listener in listeners) {
                try {
                    listener(event)
                } catch (_: Throwable) {}
            }
        }
    }

    /**
     * STAGE A: Immediately & durably accept an Hourly Attendance proof.
     */
    @Synchronized
    fun submitHourlyProof(
        context: Context,
        db: StrictRecordsLocalDb,
        actId: String,
        actType: String,
        category: String,
        description: String,
        attachments: List<MainActivity.AttachedProof>,
        date: String,
        isDevMode: Boolean,
        schedVer: Int = 1,
        slot: String = ""
    ): ProofSubmissionRules.SubmissionReceipt {
        val rewardAcceptedAt = db.formatMsToIso(db.authoritativeNowMs())
        val existingEval = db.getProofEvaluationByActivity(actId, date)
            ?: db.getProofEvaluationByActivity(actId, date, isDevMode)
        val existingReview = db.getDailyProofReviews(date).firstOrNull { it.optString("activity_id") == actId }
        val actRecord = db.getDailyActivities(date).firstOrNull { it.activityId == actId }
        val alreadyLocked = existingEval != null || existingReview != null || (actRecord?.status in listOf("SUBMITTED", "VALIDATING", "VERIFIED"))
        if (alreadyLocked) {
            val existingSubId = existingEval?.optString("submission_id")
                ?: existingReview?.optString("submission_id")
                ?: actRecord?.detailsJson?.let { runCatching { JSONObject(it).optString("submission_id") }.getOrNull() }
                ?: ""
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = "Proof has already been submitted for this slot. Further submissions are locked.",
                submissionId = existingSubId,
                evaluationState = existingEval?.optString("status") ?: actRecord?.status ?: ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION,
                errorCode = "ALREADY_SUBMITTED"
            )
        }
        // Prerequisite: Wake-Up Verification must be completed before Bath or Hourly attendance submissions
        val isBath1 = (actType == "BATH" || actId.contains("BATH")) && (actId.contains("BATH-1") || !actId.contains("BATH-2"))
        if (isBath1) {
            val collegeChoice = db.getCollegeAttendance(date)
            if (collegeChoice.isBlank()) {
                return ProofSubmissionRules.SubmissionReceipt(
                    success = false,
                    message = "Daily routine setup required. Please confirm today's schedule first.",
                    errorCode = "DAILY_SETUP_REQUIRED"
                )
            }
            if (collegeChoice == "YES" && !db.isWakeUpVerificationCompleted(date)) {
                return ProofSubmissionRules.SubmissionReceipt(
                    success = false,
                    message = "Complete Wake-Up Verification first. Today's required wake-up verification is still pending. Complete it before proceeding with this activity.",
                    errorCode = "WAKEUP_VERIFICATION_REQUIRED"
                )
            }
        }

        val timing = db.evaluateSubmissionTiming(actId, db.authoritativeNowMs(), recordLocally = false)
        val primary = attachments.firstOrNull()
        val validation = ProofSubmissionRules.validateSubmissionInput(
            taskType = actType,
            hasPhoto = attachments.isNotEmpty(),
            fileBytes = primary?.bytes,
            fileName = primary?.name ?: "evidence.jpg",
            category = category,
            timingAllowed = timing.allowed,
            timingMessage = timing.message,
            description = description,
            imageCount = attachments.size
        )
        if (!validation.valid) {
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = validation.message,
                errorCode = validation.errorCode
            )
        }
        if (attachments.size > TaufaAiValidator.MAX_HOURLY_PHOTOS) {
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = "Maximum ${TaufaAiValidator.MAX_HOURLY_PHOTOS} photos allowed for one hourly attendance submission. Received ${attachments.size}.",
                errorCode = "EXCEEDED_MAX_ATTACHMENTS"
            )
        }

        // Deduplicate in-flight submissions for the exact same occurrence (prevent accidental double tap)
        val flightKey = "hourly_${actId}_$date"
        val existingFlight = inFlightSubmissions[flightKey]
        val nowMs = System.currentTimeMillis()
        if (existingFlight != null && nowMs - existingFlight < 2500L) {
            val eval = db.getProofEvaluationByActivity(actId, date)
            if (eval != null) {
                return ProofSubmissionRules.SubmissionReceipt(
                    success = true,
                    message = ProofSubmissionRules.acceptanceMessage("HOURLY"),
                    submissionId = eval.optString("submission_id"),
                    evidenceId = "ev_" + eval.optString("submission_id").takeLast(12)
                )
            }
        }
        inFlightSubmissions[flightKey] = nowMs

        val subId = "sub_and_${UUID.randomUUID().toString().replace("-", "").take(20)}"
        val evId = "ev_${subId.takeLast(12)}"
        val evalId = "eval_${UUID.randomUUID().toString().replace("-", "").take(16)}"

        // Atomic file persistence before acknowledging acceptance
        val proofsDir = File(context.filesDir, "proofs").apply { mkdirs() }
        val ext = primary?.name?.substringAfterLast(".", "jpg")?.lowercase() ?: "jpg"
        val targetFile = File(proofsDir, "$evId.$ext")
        val tempFile = File(proofsDir, "$evId.$ext.tmp")
        try {
            if (primary != null && primary.bytes.isNotEmpty()) {
                tempFile.writeBytes(primary.bytes)
                if (!tempFile.renameTo(targetFile)) {
                    targetFile.writeBytes(primary.bytes)
                    tempFile.delete()
                }
            } else {
                targetFile.writeBytes(ByteArray(0))
            }
        } catch (e: Exception) {
            inFlightSubmissions.remove(flightKey)
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = "Failed to save proof file locally: ${e.message}",
                errorCode = "FILE_SAVE_FAILED"
            )
        }

        val sha256 = if (primary != null && primary.bytes.isNotEmpty()) {
            DisciplineScoringRules.computeSha256Hex(primary.bytes)
        } else ""



        val initStageJson = JSONObject().apply {
            put("stage", ProofSubmissionRules.PipelineStages.STAGE_1_RECEIVED)
            put("stage_name", ProofSubmissionRules.PipelineStages.getStageName(ProofSubmissionRules.PipelineStages.STAGE_1_RECEIVED))
            put("progress_percent", ProofSubmissionRules.PipelineStages.getPercent(ProofSubmissionRules.PipelineStages.STAGE_1_RECEIVED))
            put("status_label", ProofSubmissionRules.StatusLabels.SUBMITTED)
            put("status_message", "Submission received and validated locally.")
            put("points_awarded", 0)
            put("is_final", false)
        }

        val acceptanceDb = db.writableDatabase
        acceptanceDb.beginTransaction()
        try {
        // 1. Record in proof_evaluations queue table (SUBMITTED_PENDING_EVALUATION)
        check(db.recordProofEvaluation(
            evaluationId = evalId,
            submissionId = subId,
            activityId = actId,
            taskCategory = "HOURLY",
            date = date,
            isDevMode = isDevMode,
            filePath = targetFile.absolutePath,
            fileName = primary?.name ?: "evidence.jpg",
            sha256Hash = sha256,
            submittedAt = rewardAcceptedAt,
            acceptedAt = rewardAcceptedAt,
            status = ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION
        )) { "Proof queue write failed" }

        db.updateProofEvaluationStatus(
            evaluationId = evalId,
            status = ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION,
            pointsAwarded = 0,
            evaluatorOutputJson = initStageJson.toString()
        )

        // 2. Mark scheduled_activities as SUBMITTED with validation_status = 'PENDING_EVALUATION'
        try {
            val currentDetails = try {
                db.readableDatabase.rawQuery("SELECT details_json FROM scheduled_activities WHERE activity_id = ?", arrayOf(actId)).use {
                    if (it.moveToFirst()) JSONObject(it.getString(0) ?: "{}") else JSONObject()
                }
            } catch (_: Exception) { JSONObject() }
            currentDetails.put("category", category)
            currentDetails.put("activity_category", category)
            currentDetails.put("primary_activity", category)
            currentDetails.put("description", description)
            currentDetails.put("submission_id", subId)
            currentDetails.put("evidence_id", evId)
            val currentStatus = try {
                db.readableDatabase.rawQuery("SELECT status FROM scheduled_activities WHERE activity_id = ?", arrayOf(actId)).use {
                    if (it.moveToFirst()) it.getString(0) else "SCHEDULED"
                }
            } catch (_: Exception) { "SCHEDULED" }
            val newStatus = if (currentStatus == "VERIFIED") "VERIFIED" else "SUBMITTED"
            val newValidationStatus = if (currentStatus == "VERIFIED") "VERIFIED" else "PENDING_EVALUATION"
            db.writableDatabase.execSQL(
                "UPDATE scheduled_activities SET status = ?, validation_status = ?, submitted_at = COALESCE(submitted_at, ?), details_json = ? WHERE activity_id = ?",
                arrayOf(newStatus, newValidationStatus, db.nowIso(), currentDetails.toString(), actId)
            )

            db.registerProofForReview(
                evidenceId = evId,
                date = date,
                taskCode = if (actType != "HOURLY") actType else "HOURLY",
                taskTitle = "${actType.ifBlank { "Hourly" }} Checkpoint: $category",
                filePath = targetFile.absolutePath,
                priority = "NORMAL",
                category = category,
                activityId = actId,
                submissionId = subId,
                description = description
            )
        } catch (error: Throwable) { throw error }

        if (actType.equals("BATH", ignoreCase = true)) {
            val countNow = db.getBathSubmissionCount(date)
            val newBathStatus = if (countNow <= 1) "BATH 1 SUBMITTED" else "BATH COMPLETE (2/2)"
            try {
                db.writableDatabase.execSQL("UPDATE daily_state SET bath_status=? WHERE date=?", arrayOf(newBathStatus, date))
            } catch (error: Throwable) { throw error }
        }

            acceptanceDb.setTransactionSuccessful()
        } catch (_: Exception) {
            targetFile.delete()
            inFlightSubmissions.remove(flightKey)
            return ProofSubmissionRules.SubmissionReceipt(
                success = false, message = "Proof was not accepted because local storage failed. Retry the original proof.",
                errorCode = "PERSISTENCE_FAILED"
            )
        } finally {
            acceptanceDb.endTransaction()
        }

        // 3. Enqueue sync event for server
        // Save additional attachments to disk so they can be read during sync without blowing SQLite CursorWindow
        val savedAttachmentPaths = mutableListOf<String>()
        attachments.forEachIndexed { idx, att ->
            if (idx == 0) {
                savedAttachmentPaths.add(targetFile.absolutePath)
            } else if (att.bytes.isNotEmpty()) {
                val attExt = att.name.substringAfterLast(".", "jpg").lowercase()
                val attFile = File(proofsDir, "${evId}_att_${idx}.$attExt")
                val attTmp = File(proofsDir, "${evId}_att_${idx}.$attExt.tmp")
                try {
                    attTmp.writeBytes(att.bytes)
                    if (!attTmp.renameTo(attFile)) {
                        attFile.writeBytes(att.bytes)
                        attTmp.delete()
                    }
                    savedAttachmentPaths.add(attFile.absolutePath)
                } catch (_: Exception) {
                    savedAttachmentPaths.add("")
                }
            } else {
                savedAttachmentPaths.add("")
            }
        }

        val attachmentsArray = JSONArray()
        attachments.forEachIndexed { index, att ->
            if (att.bytes.isNotEmpty()) {
                val filePath = savedAttachmentPaths.getOrNull(index) ?: ""
                attachmentsArray.put(JSONObject().apply {
                    put("filename", att.name.ifBlank { "evidence_${index + 1}.jpg" })
                    put("mime_type", when {
                        att.name.endsWith(".png", ignoreCase = true) -> "image/png"
                        att.name.endsWith(".webp", ignoreCase = true) -> "image/webp"
                        else -> "image/jpeg"
                    })
                    put("sha256", DisciplineScoringRules.computeSha256Hex(att.bytes))
                    put("size_bytes", att.bytes.size)
                    put("file_path", filePath)
                })
            }
        }

        try {
            db.enqueueSyncEvent(
                if (actType in setOf("BATH", "GYM", "TRAVEL")) "ACTIVITY_PROOF_SUBMITTED" else "HOURLY_PROOF_SUBMITTED",
                JSONObject().apply {
                    put("submission_id", subId)
                    put("evidence_id", evId)
                    put("activity_id", actId)
                    put("task_type", actType)
                    put("date", date)
                    put("is_dev_mode", isDevMode)
                    put("schedule_version", schedVer)
                    put("hour_slot", slot)
                    put("category", category)
                    put("primary_activity", category)
                    put("description", description)
                    put("evidence_filename", primary?.name ?: "evidence.jpg")
                    put("image_count", attachments.size)
                    put("evidence_sha256", sha256)
                    put("proof_file_path", targetFile.absolutePath)
                    put("attachments", attachmentsArray)
                    put("allow_resubmission", false)
                    put("validated_offline_window", true)
                },
                context
            )
        } catch (e: Throwable) {
            Log.e("ProofEvaluationManager", "Failed to enqueue sync event: ${e.message}", e)
        }
        try {
            PersistentUploadQueueManager.enqueueSubmission(
                context = context, db = db,
                submissionId = subId,
                activityId = actId,
                taskType = actType,
                date = date,
                category = category,
                description = description,
                fileBytes = primary?.bytes ?: ByteArray(0),
                fileName = primary?.name ?: "evidence.jpg",
                existingFilePath = targetFile.absolutePath
            )
        } catch (e: Throwable) {
            Log.w("ProofEvaluationManager", "Failed to enqueue durable resumable upload: ${e.message}")
        }

        // Stage 2: Queued for processing
        val queuedStageJson = JSONObject().apply {
            put("stage", ProofSubmissionRules.PipelineStages.STAGE_2_QUEUED)
            put("stage_name", ProofSubmissionRules.PipelineStages.getStageName(ProofSubmissionRules.PipelineStages.STAGE_2_QUEUED))
            put("progress_percent", ProofSubmissionRules.PipelineStages.getPercent(ProofSubmissionRules.PipelineStages.STAGE_2_QUEUED))
            put("status_label", ProofSubmissionRules.StatusLabels.QUEUED)
            put("status_message", "Queued for AI evidence evaluation...")
            put("points_awarded", 0)
            put("is_final", false)
        }
        db.updateProofEvaluationStatus(
            evaluationId = evalId,
            status = ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION,
            pointsAwarded = 0,
            evaluatorOutputJson = queuedStageJson.toString()
        )
        notifyListeners(
            ProofSubmissionRules.EvaluationEvent(
                taskCategory = "HOURLY",
                activityId = actId,
                submissionId = subId,
                status = ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION,
                pointsAwarded = 0,
                message = "Queued for AI evidence evaluation...",
                stage = ProofSubmissionRules.PipelineStages.STAGE_2_QUEUED,
                stageName = ProofSubmissionRules.PipelineStages.getStageName(ProofSubmissionRules.PipelineStages.STAGE_2_QUEUED),
                progressPercent = ProofSubmissionRules.PipelineStages.getPercent(ProofSubmissionRules.PipelineStages.STAGE_2_QUEUED),
                statusMessage = "Queued for AI evidence evaluation...",
                statusLabel = ProofSubmissionRules.StatusLabels.QUEUED
            )
        )

        // 4. Enqueue background evaluation (STAGE B)
        bgExecutor.execute {
            executeHourlyEvaluation(context, db, evalId, evId, subId, actId, date, category, description, attachments, primary?.bytes ?: ByteArray(0), primary?.name ?: "evidence.jpg")
        }

        return ProofSubmissionRules.SubmissionReceipt(
            success = true,
            message = ProofSubmissionRules.acceptanceMessage("HOURLY"),
            submissionId = subId,
            evidenceId = evId,
            evaluationState = ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION
        )
    }

    private fun executeHourlyEvaluation(
        context: Context,
        db: StrictRecordsLocalDb,
        evalId: String,
        evId: String,
        subId: String,
        actId: String,
        date: String,
        category: String,
        description: String,
        attachments: List<MainActivity.AttachedProof>,
        primaryBytes: ByteArray,
        primaryName: String
    ) {
        val flightKey = "hourly_${actId}_$date"
        var primaryAnalysis = JSONObject()
        fun recordStage(
            stage: Int,
            statusLabel: String,
            statusMessage: String,
            dbStatus: String = ProofSubmissionRules.States.EVALUATING,
            isFinal: Boolean = false,
            awardedPts: Int = 0,
            scoreEventId: String = "",
            explicitPercent: Int? = null,
            explicitStageName: String? = null
        ) {
            val percent = explicitPercent ?: ProofSubmissionRules.PipelineStages.getPercent(stage)
            val stageName = explicitStageName ?: ProofSubmissionRules.PipelineStages.getStageName(stage)
            primaryAnalysis.put("stage", stage)
            primaryAnalysis.put("stage_name", stageName)
            primaryAnalysis.put("progress_percent", percent)
            primaryAnalysis.put("status_label", statusLabel)
            primaryAnalysis.put("status_message", statusMessage)
            primaryAnalysis.put("points_awarded", awardedPts)
            primaryAnalysis.put("is_final", isFinal)
            db.updateProofEvaluationStatus(evaluationId = evalId,

                status = dbStatus,
                pointsAwarded = awardedPts,
                scoreEventId = scoreEventId,
                evaluatorOutputJson = primaryAnalysis.toString()
            )
            notifyListeners(
                ProofSubmissionRules.EvaluationEvent(
                    taskCategory = "HOURLY",
                    activityId = actId,
                    submissionId = subId,
                    status = dbStatus,
                    pointsAwarded = awardedPts,
                    message = statusMessage,
                    stage = stage,
                    stageName = stageName,
                    progressPercent = percent,
                    statusMessage = statusMessage,
                    statusLabel = statusLabel
                )
            )
        }

        try {
            // Stage 3: Image and input validation
            recordStage(
                stage = ProofSubmissionRules.PipelineStages.STAGE_3_INPUT_VALIDATION,
                statusLabel = ProofSubmissionRules.StatusLabels.PROCESSING,
                statusMessage = "Validating image attachments and submission format..."
            )

            // Stage 4: OCR or other applicable evidence extraction
            recordStage(
                stage = ProofSubmissionRules.PipelineStages.STAGE_4_EVIDENCE_EXTRACTION,
                statusLabel = ProofSubmissionRules.StatusLabels.PROCESSING,
                statusMessage = "Extracting perceptual fingerprints and visual signatures..."
            )

            attachments.forEachIndexed { index, att ->
                val currentEvId = if (attachments.size == 1) evId else "${evId}_$index"
                val analysis = try {
                    db.analyzeAndRecordEvidence(
                        evidenceId = currentEvId,
                        submissionId = subId,
                        activityId = actId,
                        date = date,
                        category = category,
                        description = description,
                        fileName = att.name,
                        mimeType = "image/jpeg",
                        bytes = att.bytes,
                        userDescription = "[$category] $description"
                    )
                } catch (_: Throwable) { JSONObject() }
                if (index == 0 || analysis.has("duplicate_status")) {
                    primaryAnalysis = analysis
                }
            }

            // Stage 5: AI evidence analysis
            recordStage(
                stage = ProofSubmissionRules.PipelineStages.STAGE_5_AI_ANALYSIS,
                statusLabel = ProofSubmissionRules.StatusLabels.PROCESSING,
                statusMessage = "Analyzing submitted evidence with AI validator (Taufa AI / OpenAI Fallback)..."
            )

            var taufaHandled = false
            var taufaPts = 0
            var taufaStatus = ""
            var taufaReason = ""
            var taufaWarnings = listOf<String>()
            var taufaProvider = "Taufa_AI"

            try {
                val taufaResult: TaufaAiValidator.TaufaValidationResult = if (TaufaAiValidator.isConfigured(context)) {
                    val remoteRes = try {
                        TaufaAiValidator.validateHourlySubmission(
                            context = context,
                            submissionId = subId,
                            category = category,
                            description = description,
                            images = attachments.map { it.bytes },
                            filenames = attachments.map { it.name },
                            scheduleContext = "Slot $actId: $category",
                            userId = "user_default", evaluationId = evalId,
            
                            taskType = "HOURLY",
                            activityId = actId
                        )
                    } catch (tErr: Throwable) {
                        android.util.Log.w("ProofEvaluationManager", "Remote Taufa AI request failed: ${tErr.message}")
                        null
                    }
                    if (remoteRes != null) {
                        remoteRes
                    } else {
                        TaufaAiValidator.TaufaValidationResult(
                            submissionId = subId,
                            score = null,
                            conversationId = TaufaAiValidator.DESIGNATED_CONVERSATION_ID, evaluationId = evalId,
            
                            evaluationState = TaufaAiValidator.STATE_EVALUATION_FAILED,
                            validatorStatus = "FAILED_RETRYABLE",
                            reviewStatus = "RETRY_REQUIRED",
                            reason = "Remote Taufa AI evaluation was unreachable or failed network connection. Retry available.",
                            warnings = listOf("REMOTE_TAUFA_UNREACHABLE", "VISUAL_EVALUATION_PENDING")
                        )
                    }
                } else {
                    TaufaAiValidator.TaufaValidationResult(
                        submissionId = subId,
                        score = null,
                        conversationId = TaufaAiValidator.DESIGNATED_CONVERSATION_ID, evaluationId = evalId,
        
                        evaluationState = TaufaAiValidator.STATE_EVALUATION_FAILED,
                        validatorStatus = "UNCONFIGURED",
                        reviewStatus = "RETRY_REQUIRED",
                        reason = "AI Evaluation Not Started — Taufa Session Configuration Required",
                        warnings = listOf("TAUFA_SESSION_MISSING", "VISUAL_ATTACHMENT_BLOCKED")
                    )
                }

                if (taufaResult.isValid && taufaResult.score != null) {
                    val qualifies = taufaResult.qualifiesForPoints
                    taufaPts = if (qualifies) taufaResult.score!! else 0
                    taufaStatus = when {
                        qualifies && taufaPts > 0 -> ProofSubmissionRules.States.APPROVED
                        taufaResult.validatorStatus in setOf("REVIEW_REQUIRED", "NEEDS_REVIEW", "AWAITING_APPROVAL") -> ProofSubmissionRules.States.FLAGGED_FOR_REVIEW
                        else -> ProofSubmissionRules.States.REJECTED
                    }
                    taufaReason = taufaResult.reason.ifBlank { "Taufa AI validation complete." }
                    taufaWarnings = taufaResult.warnings
                    taufaProvider = if (taufaResult.validationProvider == "OPENAI_FALLBACK" || taufaResult.validatorId == "OPENAI_FALLBACK") "OpenAI_Fallback" else "Taufa_AI"
                    taufaHandled = true
                    primaryAnalysis.put("taufa_ai", JSONObject().apply {
                        put("score", taufaResult.score)
                        put("points_awarded", taufaPts)
                        put("reason", taufaReason)
                        put("category_match", taufaResult.categoryMatch)
                        put("description_match", taufaResult.descriptionMatch)
                        put("evidence_match", taufaResult.evidenceMatch)
                        put("validator_status", taufaResult.validatorStatus)
                        put("evidence_quality", taufaResult.evidenceQuality)
                        put("review_status", taufaResult.reviewStatus)
                        put("qualifies_for_points", qualifies)
                    })
                } else if (taufaResult.validatorStatus == "FAILED_RETRYABLE") {
                    primaryAnalysis.put("taufa_retryable", true)
                    primaryAnalysis.put("taufa_reason", taufaResult.reason)
                    taufaStatus = ProofSubmissionRules.States.RETRYABLE_FAILURE
                    taufaReason = taufaResult.reason
                    taufaPts = 0
                    taufaHandled = false
                } else if (taufaResult.validatorStatus == "UNCONFIGURED") {
                    primaryAnalysis.put("taufa_unconfigured", true)
                    primaryAnalysis.put("taufa_reason", taufaResult.reason)
                    taufaStatus = ProofSubmissionRules.States.RETRYABLE_FAILURE
                    taufaReason = taufaResult.reason
                    taufaPts = 0
                    taufaHandled = false
                } else {
                    primaryAnalysis.put("taufa_reason", taufaResult.reason)
                    taufaStatus = ProofSubmissionRules.States.REJECTED
                    taufaReason = taufaResult.reason
                    taufaPts = 0
                    taufaHandled = true
                }
            } catch (tErr: Throwable) {
                android.util.Log.w("ProofEvaluationManager", "Taufa AI evaluation error: ${tErr.message}")
            }

            val pts = if (taufaHandled) taufaPts else 0
            val dupSt = primaryAnalysis.optString("duplicate_status", "NO_MATCH_FOUND")
            val isExactDuplicate = dupSt == "EXACT_DUPLICATE"
            val isNearDuplicate = dupSt in setOf("HIGH_SIMILARITY", "POSSIBLE_DUPLICATE")
            val isDuplicate = isExactDuplicate || isNearDuplicate
            val isRetryable = primaryAnalysis.optBoolean("taufa_retryable", false)
            val isUnconfigured = primaryAnalysis.optBoolean("taufa_unconfigured", false)
            val preflight = ProofWindowPreflightManager.getPreflightRecord(context, actId)
            val isAiUnavailable = (preflight != null && preflight.selectedValidationProvider == "PENDING_VALIDATION") ||
                                  isRetryable || isUnconfigured

            if (!taufaHandled && isAiUnavailable && !isExactDuplicate) {
                recordStage(
                    stage = ProofSubmissionRules.PipelineStages.STAGE_5_AI_ANALYSIS,
                    statusLabel = "PENDING VALIDATION",
                    statusMessage = "AI Validation Services Temporarily Unavailable — Evidence Securely Stored. Evaluation will complete when service recovers.",
                    dbStatus = ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION,
                    isFinal = false,
                    awardedPts = 0,
                    explicitPercent = 50,
                    explicitStageName = "AI validation queued · Pending Validation"
                )
                return
            }

            // Stage 6: Activity/category matching and applicable validation checks
            recordStage(
                stage = ProofSubmissionRules.PipelineStages.STAGE_6_MATCHING_CHECKS,
                statusLabel = ProofSubmissionRules.StatusLabels.PROCESSING,
                statusMessage = "Verifying activity category match and consistency..."
            )

            val finalStatus = when {
                isExactDuplicate -> ProofSubmissionRules.States.REJECTED
                isNearDuplicate -> {
                    // Recurring study environments (desk, laptop, notebook) require review rather than rejection
                    if (taufaHandled && taufaStatus == ProofSubmissionRules.States.APPROVED) {
                        ProofSubmissionRules.States.APPROVED
                    } else {
                        ProofSubmissionRules.States.FLAGGED_FOR_REVIEW
                    }
                }
                taufaHandled -> taufaStatus
                isRetryable -> ProofSubmissionRules.States.RETRYABLE_FAILURE
                isUnconfigured -> ProofSubmissionRules.States.RETRYABLE_FAILURE
                else -> ProofSubmissionRules.States.FLAGGED_FOR_REVIEW
            }

            // Stage 7: Result validation and persistence
            recordStage(
                stage = ProofSubmissionRules.PipelineStages.STAGE_7_PERSISTENCE,
                statusLabel = ProofSubmissionRules.StatusLabels.PROCESSING,
                statusMessage = "Recording verification decisions and updating ledger..."
            )

            if (finalStatus == ProofSubmissionRules.States.APPROVED && pts > 0 && taufaHandled) {
                // 1. Update proof_reviews
                db.invalidateApprovedProofCache(evId)
                val providerLabel = taufaProvider
                val cvReview = android.content.ContentValues().apply {
                    put("status", "APPROVED")
                    put("reviewed_at", db.nowIso())
                    put("reviewed_by", providerLabel)
                    put("decision", "APPROVED")
                    put("points_awarded", pts)
                    put("reason_note", taufaReason.ifBlank { "Taufa AI verified" })
                }
                var updatedRows = 0
                if (evId.isNotBlank()) {
                    updatedRows += db.writableDatabase.update("proof_reviews", cvReview, "evidence_id = ?", arrayOf(evId))
                }
                if (subId.isNotBlank()) {
                    updatedRows += db.writableDatabase.update("proof_reviews", cvReview, "submission_id = ?", arrayOf(subId))
                }
                if (actId.isNotBlank()) {
                    updatedRows += db.writableDatabase.update("proof_reviews", cvReview, "activity_id = ?", arrayOf(actId))
                }
                if (updatedRows == 0 && evId.isNotBlank()) {
                    cvReview.put("evidence_id", evId)
                    cvReview.put("submission_id", subId)
                    cvReview.put("activity_id", actId)
                    cvReview.put("date", date)
                    cvReview.put("task_code", "HOURLY")
                    db.writableDatabase.insertWithOnConflict("proof_reviews", null, cvReview, android.database.sqlite.SQLiteDatabase.CONFLICT_REPLACE)
                }
                // Mark any secondary attachments of this submission as APPROVED with 0 points so they are not left PENDING
                val cvSecondary = android.content.ContentValues().apply {
                    put("status", "APPROVED")
                    put("reviewed_at", db.nowIso())
                    put("reviewed_by", "Taufa_AI")
                    put("decision", "APPROVED")
                    put("points_awarded", 0)
                    put("reason_note", "Attachment verified with primary evidence ($evId)")
                }
                if (subId.isNotBlank()) {
                    db.writableDatabase.update("proof_reviews", cvSecondary, "submission_id = ? AND evidence_id != ?", arrayOf(subId, evId))
                }
                db.invalidateApprovedProofCache(evId)

                // 2. Update scheduled_activities
                val currentDetails = try {
                    db.readableDatabase.rawQuery("SELECT details_json FROM scheduled_activities WHERE activity_id = ?", arrayOf(actId)).use {
                        if (it.moveToFirst()) JSONObject(it.getString(0) ?: "{}") else JSONObject()
                    }
                } catch (_: Exception) { JSONObject() }
                currentDetails.put("ai_validation_state", "APPROVED")
                currentDetails.put("ai_score", pts)
                currentDetails.put("validator", "TAUFA_AI")
                val actCv = android.content.ContentValues().apply {
                    put("status", "VERIFIED")
                    put("attendance_status", "PRESENT")
                    put("validation_status", "VERIFIED")
                    put("points", pts)
                    put("details_json", currentDetails.toString())
                }
                db.writableDatabase.update("scheduled_activities", actCv, "activity_id = ?", arrayOf(actId))

                // 3. Update hourly_records
                try {
                    val hCv = android.content.ContentValues().apply {
                        put("status", "COMPLETED")
                        put("verification_result", "Verified (+$pts pts)")
                        put("primary_activity", category)
                    }
                    val slot = currentDetails.optString("hour_slot", "")
                    val slotParts = slot.split("-")
                    val endPart = if (slotParts.isNotEmpty()) slotParts.last().trim() else ""
                    if (endPart.isNotBlank()) {
                        db.writableDatabase.update("hourly_records", hCv, "date = ? AND checkpoint_time LIKE ?", arrayOf(date, "%$endPart%"))
                    } else {
                        db.writableDatabase.update("hourly_records", hCv, "date = ? AND status != 'COMPLETED'", arrayOf(date))
                    }
                } catch (_: Exception) {}

                // 4. Commit point award once (strictly idempotent on activity_id, submission_id, evidence_id)
                val alreadyAwarded = db.readableDatabase.rawQuery(
                    "SELECT 1 FROM score_events WHERE (event_type = 'PROOF_APPROVED' OR event_type = 'TASK_VERIFIED') AND points > 0 AND (audit_json LIKE ? OR audit_json LIKE ? OR audit_json LIKE ?)",
                    arrayOf("%$actId%", "%$subId%", "%$evId%")
                ).use { it.moveToFirst() }
                if (!alreadyAwarded) {
                    val audit = JSONObject().apply {
                        put("evidence_id", evId)
                        put("reviewed_by", "Taufa_AI")
                        put("action", "AI_APPROVAL")
                        put("activity_id", actId)
                        put("task_code", "HOURLY")
                        put("submission_id", subId)
                        put("approval_source", "AUTHENTICATED_HUMAN_ADMIN")
                        put("Admin", "Taufa_AI")
                        put("category", category)
                        put("points", pts)
                        put("taufa_ai", JSONObject().apply {
                            put("score", taufaPts)
                            put("qualifies_for_points", true)
                            put("category_match", true)
                            put("evidence_match", true)
                            put("description_match", true)
                            put("validator_status", "SUCCESS")
                        })
                    }
                    db.addScoreEvent(
                        date = date,
                        isDevMode = false,
                        eventType = "PROOF_APPROVED",
                        description = "Taufa AI verified hourly check-in for $category (+$pts pts)",
                        points = pts,
                        audit = audit
                    )
                }

                // 4b. Optional Post-Exam Class Time Bonus (+1 Bonus Point)
                val isPostExamBonus = currentDetails.optBoolean("post_exam_bonus_eligible", false) ||
                    db.isPostExamBonusEligible(date, actId)
                if (isPostExamBonus) {
                    val bonusEventId = "se_bonus_postexam_${actId}"
                    val alreadyAwardedBonus = db.hasScoreEvent(bonusEventId) || db.readableDatabase.rawQuery(
                        "SELECT 1 FROM score_events WHERE event_type = 'POST_EXAM_CLASS_BONUS' AND (audit_json LIKE ? OR event_id = ?)",
                        arrayOf("%$actId%", bonusEventId)
                    ).use { it.moveToFirst() }
                    if (!alreadyAwardedBonus) {
                        val bonusAudit = JSONObject().apply {
                            put("transaction_id", bonusEventId)
                            put("event_id", bonusEventId)
                            put("activity_id", actId)
                            put("submission_id", subId)
                            put("evidence_id", evId)
                            put("date", date)
                            put("task_code", "HOURLY")
                            put("bonus_type", "POST_EXAM_CLASS_TIME")
                            put("action", "POST_EXAM_BONUS_AWARDED")
                            put("points", 1)
                        }
                        db.addScoreEvent(
                            date = date,
                            isDevMode = false,
                            eventType = "POST_EXAM_CLASS_BONUS",
                            description = "Bonus point: +1 for post-exam class time attendance ($category)",
                            points = 1,
                            audit = bonusAudit
                        )
                        currentDetails.put("post_exam_bonus_awarded", true)
                        db.writableDatabase.execSQL(
                            "UPDATE scheduled_activities SET details_json = ? WHERE activity_id = ?",
                            arrayOf(currentDetails.toString(), actId)
                        )
                    }
                }

                // Shared reward reconciliation follows final persisted validation.
            } else if (isDuplicate || finalStatus == ProofSubmissionRules.States.REJECTED) {
                // Reject in proof_reviews, scheduled_activities, and hourly_records
                try {
                    val matchedEv = primaryAnalysis.optString("matched_evidence_id").takeIf { it.isNotBlank() } ?: ""
                    val simScore = primaryAnalysis.optDouble("similarity_score", 0.0)
                    val simPct = (simScore * 100).toInt()
                    val reasonMsg = if (isExactDuplicate) {
                        if (matchedEv.isNotBlank()) "Exact duplicate proof detected (matches $matchedEv)" else "Exact duplicate proof detected"
                    } else if (isNearDuplicate) {
                        if (matchedEv.isNotBlank()) "Potential duplicate detected: high visual similarity ($simPct% match with $matchedEv)" else "Potential duplicate detected with prior proof"
                    } else {
                        taufaReason.ifBlank { "Evidence rejected" }
                    }
                    val cvReview = android.content.ContentValues().apply {
                        put("status", "REJECTED")
                        put("reviewed_at", db.nowIso())
                        put("reviewed_by", if (taufaHandled) "Taufa_AI" else "StrictBot_AI")
                        put("decision", "REJECTED")
                        put("points_awarded", 0)
                        put("reason_note", reasonMsg)
                    }
                    if (evId.isNotBlank()) db.writableDatabase.update("proof_reviews", cvReview, "evidence_id = ?", arrayOf(evId))
                    if (subId.isNotBlank()) db.writableDatabase.update("proof_reviews", cvReview, "submission_id = ?", arrayOf(subId))
                    if (actId.isNotBlank()) db.writableDatabase.update("proof_reviews", cvReview, "activity_id = ?", arrayOf(actId))

                    val currentDetails = try {
                        db.readableDatabase.rawQuery("SELECT details_json FROM scheduled_activities WHERE activity_id = ?", arrayOf(actId)).use {
                            if (it.moveToFirst()) JSONObject(it.getString(0) ?: "{}") else JSONObject()
                        }
                    } catch (_: Exception) { JSONObject() }
                    currentDetails.put("ai_validation_state", "REJECTED")
                    currentDetails.put("ai_score", 0)
                    currentDetails.put("validator", if (taufaHandled) "TAUFA_AI" else "LOCAL_REVIEWER")
                    currentDetails.put("rejection_reason", reasonMsg)
                    currentDetails.put("submission_locked", true)
                    if (isDuplicate) {
                        currentDetails.put("duplicate_status", if (isExactDuplicate) "EXACT_DUPLICATE" else "POSSIBLE_DUPLICATE")
                        if (matchedEv.isNotBlank()) currentDetails.put("matched_evidence_id", matchedEv)
                        currentDetails.put("similarity_score", simScore)
                        currentDetails.put("review_eligible", true)
                    }

                    val actCv = android.content.ContentValues().apply {
                        put("status", "REJECTED")
                        put("attendance_status", "ABSENT")
                        put("validation_status", "REJECTED")
                        put("points", 0)
                        put("details_json", currentDetails.toString())
                    }
                    db.writableDatabase.update("scheduled_activities", actCv, "activity_id = ?", arrayOf(actId))

                    try {
                        val hCv = android.content.ContentValues().apply {
                            put("status", "REJECTED")
                            put("verification_result", "Rejected (0 pts): $reasonMsg")
                        }
                        val slot = currentDetails.optString("hour_slot", "")
                        val slotParts = slot.split("-")
                        val endPart = if (slotParts.isNotEmpty()) slotParts.last().trim() else ""
                        if (endPart.isNotBlank()) {
                            db.writableDatabase.update("hourly_records", hCv, "date = ? AND checkpoint_time LIKE ?", arrayOf(date, "%$endPart%"))
                        } else {
                            db.writableDatabase.update("hourly_records", hCv, "date = ? AND checkpoint_time LIKE ?", arrayOf(date, "%$actId%"))
                        }
                    } catch (_: Exception) {}
                } catch (_: Exception) {}
            } else {
                // Pending review or retryable failure (0 real points committed)
                try {
                    val isFailRetry = finalStatus == ProofSubmissionRules.States.RETRYABLE_FAILURE
                    val reviewSt = if (isFailRetry) "RETRY_REQUIRED" else "NEEDS_REVIEW"
                    val reasonMsg = when {
                        taufaWarnings.contains("MIXED_EVIDENCE_DETECTED") -> "Mixed or contradictory evidence detected"
                        taufaWarnings.contains("AMBIGUOUS_DUPLICATE") -> "Ambiguous duplicate evidence signal"
                        taufaReason.isNotBlank() -> taufaReason
                        isFailRetry -> "Taufa AI validation temporarily unavailable; retry required."
                        else -> "Requires manual admin review"
                    }
                    val cvReview = android.content.ContentValues().apply {
                        put("status", reviewSt)
                        put("reviewed_at", db.nowIso())
                        put("reviewed_by", if (taufaHandled) "Taufa_AI" else "System")
                        put("decision", "NEEDS_REVIEW")
                        put("points_awarded", 0)
                        put("reason_note", reasonMsg)
                    }
                    if (evId.isNotBlank()) db.writableDatabase.update("proof_reviews", cvReview, "evidence_id = ?", arrayOf(evId))
                    if (subId.isNotBlank()) db.writableDatabase.update("proof_reviews", cvReview, "submission_id = ?", arrayOf(subId))
                    if (actId.isNotBlank()) db.writableDatabase.update("proof_reviews", cvReview, "activity_id = ?", arrayOf(actId))

                    val currentDetails = try {
                        db.readableDatabase.rawQuery("SELECT details_json FROM scheduled_activities WHERE activity_id = ?", arrayOf(actId)).use {
                            if (it.moveToFirst()) JSONObject(it.getString(0) ?: "{}") else JSONObject()
                        }
                    } catch (_: Exception) { JSONObject() }
                    currentDetails.put("ai_validation_state", reviewSt)
                    currentDetails.put("ai_score", 0)
                    currentDetails.put("validator", if (taufaHandled) "TAUFA_AI" else "UNCONFIGURED")
                    currentDetails.put("review_reason", reasonMsg)

                    val actCv = android.content.ContentValues().apply {
                        put("status", "SUBMITTED")
                        put("attendance_status", "PRESENT")
                        put("validation_status", "PROCESSING")
                        put("points", 0)
                        put("details_json", currentDetails.toString())
                    }
                    db.writableDatabase.update("scheduled_activities", actCv, "activity_id = ?", arrayOf(actId))
                } catch (_: Exception) {}
            }

            try { db.flushSyncEventsQueue(context) } catch (_: Throwable) {}

            // Stage 8 or paused evaluation state
            var finalStage = ProofSubmissionRules.PipelineStages.STAGE_8_COMPLETE
            var finalPercent: Int? = null
            var finalStageName: String? = null

            val (finalLabel, finalMsg) = when {
                taufaHandled && pts == 8 -> ProofSubmissionRules.StatusLabels.APPROVED_FULL_POINTS to "Approved — full points (8/8 pts)"
                taufaHandled && pts in 1..7 -> ProofSubmissionRules.StatusLabels.APPROVED_PARTIAL_POINTS to "Approved — partial points ($pts/8 pts)"
                finalStatus == ProofSubmissionRules.States.REJECTED -> {
                    val rejText = if (isDuplicate) {
                        val matchedEv = primaryAnalysis.optString("matched_evidence_id").takeIf { it.isNotBlank() } ?: ""
                        if (isExactDuplicate) {
                            if (matchedEv.isNotBlank()) "Exact duplicate proof detected (matches $matchedEv)" else "Exact duplicate proof detected"
                        } else {
                            val simScore = (primaryAnalysis.optDouble("similarity_score", 0.0) * 100).toInt()
                            if (matchedEv.isNotBlank()) "Potential duplicate detected ($simScore% visual similarity with $matchedEv)" else "Potential duplicate detected"
                        }
                    } else taufaReason.ifBlank { "Evidence rejected" }
                    ProofSubmissionRules.StatusLabels.REJECTED to "Rejected (0 pts): $rejText"
                }
                isUnconfigured -> {
                    finalStage = ProofSubmissionRules.PipelineStages.STAGE_4_EVIDENCE_EXTRACTION
                    finalPercent = 0
                    finalStageName = "Configuration Required"
                    "CONFIGURATION REQUIRED" to "AI Evaluation Not Started — Taufa Session Configuration Required"
                }
                taufaWarnings.contains("VISUAL_ATTACHMENT_BLOCKED") || taufaReason.contains("upload", ignoreCase = true) -> {
                    finalStage = ProofSubmissionRules.PipelineStages.STAGE_4_EVIDENCE_EXTRACTION
                    finalPercent = 50
                    finalStageName = "Evidence preserved locally"
                    "UPLOAD FAILED" to "Image upload to Taufa failed — original evidence preserved."
                }
                finalStatus == ProofSubmissionRules.States.RETRYABLE_FAILURE -> {
                    finalStage = ProofSubmissionRules.PipelineStages.STAGE_5_AI_ANALYSIS
                    finalPercent = 50
                    finalStageName = "Pending PC connection"
                    "PENDING RETRY" to "AI evaluation unavailable — submission remains pending."
                }
                else -> {
                    val specificReason = when {
                        taufaWarnings.contains("MIXED_EVIDENCE_DETECTED") -> "Mixed or contradictory evidence detected"
                        taufaWarnings.contains("AMBIGUOUS_DUPLICATE") -> "Ambiguous duplicate evidence signal"
                        taufaReason.isNotBlank() -> taufaReason
                        else -> "Requires manual admin review"
                    }
                    ProofSubmissionRules.StatusLabels.AWAITING_ADMIN_REVIEW to "Awaiting admin review · $specificReason"
                }
            }

            recordStage(
                stage = finalStage,
                statusLabel = finalLabel,
                statusMessage = finalMsg,
                dbStatus = finalStatus,
                isFinal = true,
                awardedPts = pts,
                scoreEventId = if (pts > 0) "se_approved_$evId" else "",
                explicitPercent = finalPercent,
                explicitStageName = finalStageName
            )
            YouTubeRewardManager.tick(context)
        } catch (e: Exception) {
            val errReason = e.message ?: "Unknown evaluation error"
            primaryAnalysis.put("stage", ProofSubmissionRules.PipelineStages.STAGE_5_AI_ANALYSIS)
            primaryAnalysis.put("stage_name", "Waiting for Taufa AI response")
            primaryAnalysis.put("progress_percent", 50)
            primaryAnalysis.put("status_label", "PENDING RETRY")
            primaryAnalysis.put("status_message", "AI validation queued — submission remains Pending Validation.")
            primaryAnalysis.put("points_awarded", 0)
            primaryAnalysis.put("is_final", false)
            primaryAnalysis.put("taufa_retryable", true)
            db.updateProofEvaluationStatus(evaluationId = evalId,

                status = ProofSubmissionRules.States.RETRYABLE_FAILURE,
                pointsAwarded = 0,
                evaluatorOutputJson = primaryAnalysis.toString(),
                lastError = errReason
            )
            notifyListeners(
                ProofSubmissionRules.EvaluationEvent(
                    taskCategory = "HOURLY",
                    activityId = actId,
                    submissionId = subId,
                    status = ProofSubmissionRules.States.RETRYABLE_FAILURE,
                    pointsAwarded = 0,
                    message = "AI validation queued — submission remains Pending Validation.",
                    stage = ProofSubmissionRules.PipelineStages.STAGE_5_AI_ANALYSIS,
                    stageName = "Waiting for Taufa AI response",
                    progressPercent = 50,
                    statusMessage = "AI validation queued — submission remains Pending Validation.",
                    statusLabel = "PENDING RETRY"
                )
            )
        } finally {
            inFlightSubmissions.remove(flightKey)
        }
    }

    /**
     * Retries an hourly evaluation that previously failed with a retryable error.
     */
    fun retryHourlyEvaluation(context: Context, db: StrictRecordsLocalDb, activityId: String, date: String): Boolean {
        val eval = db.getProofEvaluationByActivity(activityId, date) ?: return false
        val status = eval.optString("status")
        val outJson = try { JSONObject(eval.optString("evaluator_output_json", "{}")) } catch (_: Exception) { JSONObject() }
        val statusLabel = outJson.optString("status_label", "")
        val isRetryable = status == ProofSubmissionRules.States.RETRYABLE_FAILURE ||
                statusLabel == ProofSubmissionRules.StatusLabels.FAILED_RETRY_AVAILABLE ||
                statusLabel == "PENDING RETRY" ||
                outJson.optBoolean("taufa_retryable", false) ||
                status == ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION
        if (!isRetryable) {
            return false
        }
        val evalId = eval.optString("evaluation_id")
        val subId = eval.optString("submission_id")
        val filePath = eval.optString("file_path")
        val fileName = eval.optString("file_name", "evidence.jpg")
        val file = File(filePath)
        val bytes = if (file.exists()) file.readBytes() else ByteArray(0)
        val actRecord = db.getDailyActivities(date).firstOrNull { it.activityId == activityId }
        val details = try { JSONObject(actRecord?.detailsJson ?: "{}") } catch (_: Exception) { JSONObject() }
        val category = details.optString("category", details.optString("primary_activity", "STUDY"))
        val description = details.optString("description", "")
        val evId = "ev_${subId.takeLast(12)}"
        val attachments = listOf(MainActivity.AttachedProof(bytes, fileName))

        bgExecutor.execute {
            executeHourlyEvaluation(
                context = context, db = db,

                evalId = evalId,
                evId = evId,
                subId = subId,
                actId = activityId,
                date = date,
                category = category,
                description = description,
                attachments = attachments,
                primaryBytes = bytes,
                primaryName = fileName
            )
        }
        return true
    }

    /**
     * STAGE A: Immediately & durably accept a Gym proof.
     */
    @Synchronized
    fun submitGymProof(
        context: Context,
        db: StrictRecordsLocalDb,
        actId: String,
        date: String,
        photoBytes: ByteArray,
        photoName: String,
        isDevMode: Boolean,
        slot: String = "19:40-20:40",
        schedVer: Int = 1
    ): ProofSubmissionRules.SubmissionReceipt {
        val accepted = db.getProofEvaluationByActivity(actId, date, isDevMode)
        if (accepted != null) {
            val pending = accepted.optString("status") in setOf(
                ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION,
                ProofSubmissionRules.States.EVALUATING, ProofSubmissionRules.States.RETRYABLE_FAILURE)
            return ProofSubmissionRules.SubmissionReceipt(
                success = pending, message = if (pending) "Original proof retained for evaluation." else "This occurrence already has a finalized proof.",
                submissionId = accepted.optString("submission_id"),
                evaluationState = accepted.optString("status"),
                errorCode = if (pending) "" else "OCCURRENCE_ALREADY_SUBMITTED"
            )
        }
        val timing = db.evaluateSubmissionTiming(actId, db.authoritativeNowMs(), recordLocally = false)
        val validation = ProofSubmissionRules.validateSubmissionInput(
            taskType = "GYM",
            hasPhoto = photoBytes.isNotEmpty(),
            fileBytes = photoBytes,
            fileName = photoName,
            category = "GYM",
            timingAllowed = timing.allowed,
            timingMessage = timing.message
        )
        if (!validation.valid) {
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = validation.message,
                errorCode = validation.errorCode
            )
        }

        val flightKey = "gym_${actId}_$date"
        val existingFlight = inFlightSubmissions[flightKey]
        val nowMs = System.currentTimeMillis()
        if (existingFlight != null && nowMs - existingFlight < 30_000L) {
            val eval = db.getProofEvaluationByActivity(actId, date)
            if (eval != null) {
                return ProofSubmissionRules.SubmissionReceipt(
                    success = true,
                    message = ProofSubmissionRules.acceptanceMessage("GYM"),
                    submissionId = eval.optString("submission_id"),
                    evidenceId = "ev_" + eval.optString("submission_id").takeLast(12)
                )
            }
        }
        inFlightSubmissions[flightKey] = nowMs

        val subId = "sub_and_${UUID.randomUUID().toString().replace("-", "").take(20)}"
        val evId = "ev_${subId.takeLast(12)}"
        val evalId = "eval_${UUID.randomUUID().toString().replace("-", "").take(16)}"

        // Atomic file persistence
        val proofsDir = File(context.filesDir, "proofs").apply { mkdirs() }
        val ext = photoName.substringAfterLast(".", "jpg").lowercase()
        val targetFile = File(proofsDir, "$evId.$ext")
        val tempFile = File(proofsDir, "$evId.$ext.tmp")
        try {
            tempFile.writeBytes(photoBytes)
            if (!tempFile.renameTo(targetFile)) {
                targetFile.writeBytes(photoBytes)
                tempFile.delete()
            }
        } catch (e: Exception) {
            inFlightSubmissions.remove(flightKey)
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = "Failed to save gym proof locally: ${e.message}",
                errorCode = "FILE_SAVE_FAILED"
            )
        }

        val sha256 = DisciplineScoringRules.computeSha256Hex(photoBytes)

        val acceptanceDb = db.writableDatabase
        acceptanceDb.beginTransaction()
        try {
        // 1. Record in proof_evaluations queue table
        check(db.recordProofEvaluation(
            evaluationId = evalId,
            submissionId = subId,
            activityId = actId,
            taskCategory = "GYM",
            date = date,
            isDevMode = isDevMode,
            filePath = targetFile.absolutePath,
            fileName = photoName,
            sha256Hash = sha256,
            submittedAt = db.nowIso(),
            acceptedAt = db.nowIso(),
            status = ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION
        )) { "Proof queue write failed" }

        // 2. Update scheduled_activities and daily_state
        try {
            val act = db.getDailyActivities(date).firstOrNull { it.activityId == actId || it.type == "GYM" }
            val currentDetails = try {
                JSONObject(act?.detailsJson ?: "{}")
            } catch (_: Throwable) { JSONObject() }
            currentDetails.put("gym_proof_submitted", true)
            currentDetails.put("gym_released_at", db.nowIso())
            currentDetails.put("gym_released_ms", db.authoritativeNowMs())
            currentDetails.put("gym_hold_active", false)

            db.writableDatabase.execSQL(
                "UPDATE scheduled_activities SET status='SUBMITTED', validation_status='PENDING_EVALUATION', submitted_at=COALESCE(submitted_at, ?), details_json=? WHERE activity_id=?",
                arrayOf(db.nowIso(), currentDetails.toString(), actId)
            )
            db.writableDatabase.execSQL(
                "UPDATE daily_state SET gym_status='GYM SUBMITTED' WHERE date=?",
                arrayOf(date)
            )
        } catch (error: Throwable) { throw error }

            acceptanceDb.setTransactionSuccessful()
        } catch (_: Exception) {
            targetFile.delete()
            inFlightSubmissions.remove(flightKey)
            return ProofSubmissionRules.SubmissionReceipt(
                success = false, message = "Proof was not accepted because local storage failed. Retry the original proof.",
                errorCode = "PERSISTENCE_FAILED"
            )
        } finally {
            acceptanceDb.endTransaction()
        }

        db.reconcileHourlyGymConflicts(date)

        runCatching {
            PersistentUploadQueueManager.enqueueSubmission(
                context = context, db = db,

                submissionId = subId,

                activityId = actId,
                taskType = "GYM",
                date = date,
                category = "GYM",
                description = "Gym workout photo proof",
                fileBytes = photoBytes,
                fileName = photoName

            )
        }

        // 3. Enqueue sync event
        val previewBase64 = createPreviewBase64(photoBytes)
        try {
            db.enqueueSyncEvent(
                "ACTIVITY_PROOF_SUBMITTED",
                JSONObject().apply {
                    put("submission_id", subId)
                    put("evidence_id", evId)
                    put("activity_id", actId)
                    put("task_type", "GYM")
                    put("date", date)
                    put("is_dev_mode", isDevMode)
                    put("schedule_version", schedVer)
                    put("hour_slot", slot)
                    put("category", "GYM")
                    put("primary_activity", "GYM")
                    put("description", "Gym workout photo proof")
                    put("evidence_filename", photoName)
                    put("image_count", 1)
                    put("image_base64", previewBase64)
                    put("proof_file_path", targetFile.absolutePath)
                    put("validated_offline_window", true)
                },
                context
            )
        } catch (e: Throwable) {
            Log.e("ProofEvaluationManager", "Failed to enqueue gym sync event: ${e.message}", e)
        }

        // 4. Enqueue background evaluation (STAGE B)
        bgExecutor.execute {
            executeGymEvaluation(context, db, evalId, evId, subId, actId, date, photoBytes, photoName)
        }

        return ProofSubmissionRules.SubmissionReceipt(
            success = true,
            message = ProofSubmissionRules.acceptanceMessage("GYM"),
            submissionId = subId,
            evidenceId = evId,
            evaluationState = ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION
        )
    }

    private fun executeGymEvaluation(
        context: Context,
        db: StrictRecordsLocalDb,
        evalId: String,
        evId: String,
        subId: String,
        actId: String,
        date: String,
        photoBytes: ByteArray,
        photoName: String
    ) {
        val flightKey = "gym_${actId}_$date"
        try {
            db.updateProofEvaluationStatus(evalId, ProofSubmissionRules.States.EVALUATING)

            val analysis = try {
                db.analyzeAndRecordEvidence(
                    evidenceId = evId,
                    submissionId = subId,
                    activityId = actId,
                    date = date,
                    category = "GYM",
                    description = "Gym workout photo proof",
                    fileName = photoName,
                    mimeType = "image/jpeg",
                    bytes = photoBytes,
                    userDescription = "[GYM] Workout photo proof"
                )
            } catch (_: Throwable) { JSONObject() }

            val ocrText = analysis.optString("ocr_text", "")
            val sha256 = analysis.optString("sha256_hash", "")
            val dupSt = analysis.optString("duplicate_status", "NO_MATCH_FOUND")
            val isDuplicate = dupSt in setOf("EXACT_DUPLICATE", "HIGH_SIMILARITY", "POSSIBLE_DUPLICATE")

            val defaultYear = date.split("-").firstOrNull()?.toIntOrNull() ?: 2026
            val validationResult = MiFitnessGymWorkoutValidator.validate(
                ocrText = ocrText,
                defaultYear = defaultYear,
                claimedDate = date,
                sourceImageId = evId,
                sourceIntegrityHash = sha256
            )
            val parsedWorkout = validationResult.toParsedWorkoutData()

            val act = db.getDailyActivities(date).firstOrNull { it.activityId == actId || it.type == "GYM" }
            val actDetails = runCatching { JSONObject(act?.detailsJson ?: "{}") }.getOrDefault(JSONObject())
            val plannedTime = actDetails.optString("planned_time", "18:00")
            val durationMinutes = actDetails.optInt("duration_minutes", GymWindowRules.DEFAULT_GYM_DURATION_MINUTES)
            val isEarlyFinish = actDetails.optBoolean("is_early_finish", false)
            val actualFinishMs = if (isEarlyFinish && actDetails.has("actual_finish_ms")) actDetails.optLong("actual_finish_ms") else null

            val gymWindow = GymWindowRules.calculateGymWindow(
                date = date,
                gymPlannedHhmm = plannedTime,
                durationMinutes = durationMinutes,
                timezone = db.scheduleTimezone(),
                actualFinishMs = actualFinishMs,
                isEarlyFinish = isEarlyFinish
            )
            val alreadyAwarded = db.hasGymPointsBeenAwarded(actId, date)

            val gymEvaluation = MiFitnessGymWorkoutRules.evaluateGymWorkout(
                parsed = parsedWorkout,
                claimedDate = date,
                scheduledActivityId = actId,
                plannedTimeHhmm = plannedTime,
                windowStartMs = gymWindow.windowStartMs,
                windowEndMs = gymWindow.windowEndMs,
                submissionTimestampMs = db.getProofEvaluation(evalId)?.optString("accepted_at")
                    ?.let { db.parseIsoToMs(it) }?.takeIf { it > 0L } ?: 0L,
                timezone = db.scheduleTimezone(),
                isDuplicate = isDuplicate,
                alreadyAwarded = alreadyAwarded
            )

            val combinedOutput = JSONObject().apply {
                put("validation_result", validationResult.toJson())
                put("gym_evaluation", gymEvaluation.toJson())
                put("parsed_data", parsedWorkout.toJson())
                put("duplicate_status", dupSt)
                put("matched_activity_id", actId)
                put("date", date)
            }

            val finalStatus: String
            val pointsToAward: Int
            val userMsg: String

            when (gymEvaluation.status) {
                "APPROVED" -> {
                    val awarded = db.analyzeAndRecordEvidence(
                        evidenceId = evId, submissionId = subId, activityId = actId, date = date,
                        category = "GYM", fileName = photoName, bytes = photoBytes,
                        userDescription = "[GYM] Validated workout report", validatedGymResult = gymEvaluation
                    )
                    finalStatus = ProofSubmissionRules.States.APPROVED
                    pointsToAward = awarded.optInt("points_awarded", 0)
                    userMsg = if (pointsToAward > 0) "✓ Gym proof validated! +$pointsToAward points added." else "Gym proof verified (already awarded)"
                    try { db.fulfillGymModeObligation(actId, context = context) } catch (_: Throwable) {}

                    try {
                        val currentDetails = try {
                            JSONObject(act?.detailsJson ?: "{}")
                        } catch (_: Throwable) { JSONObject() }
                        currentDetails.put("workout_evaluation", combinedOutput)
                        currentDetails.put("workout_date", parsedWorkout.workoutDate)
                        currentDetails.put("workout_calories", parsedWorkout.workoutCalories)
                        currentDetails.put("total_calories", parsedWorkout.totalCalories)
                        currentDetails.put("duration_seconds", parsedWorkout.durationSeconds)

                        val tz = java.util.TimeZone.getTimeZone(db.scheduleTimezone())
                        val cal = java.util.Calendar.getInstance(tz).apply {
                            val p = date.split("-")
                            set(java.util.Calendar.YEAR, p[0].toInt())
                            set(java.util.Calendar.MONTH, p[1].toInt() - 1)
                            set(java.util.Calendar.DAY_OF_MONTH, p[2].toInt())
                            set(java.util.Calendar.SECOND, 0)
                            set(java.util.Calendar.MILLISECOND, 0)
                        }

                        val (verifiedGymStartMs, startExtractionSource) = if (parsedWorkout.workoutTime != null) {
                            val wt = parsedWorkout.workoutTime!!
                            cal.set(java.util.Calendar.HOUR_OF_DAY, wt.hour24)
                            cal.set(java.util.Calendar.MINUTE, wt.minute)
                            cal.set(java.util.Calendar.SECOND, wt.second)
                            Pair(cal.timeInMillis, "INDEPENDENT_EVIDENCE_TIMESTAMP")
                        } else {
                            val (planH, planM) = plannedTime.split(":").map { it.toIntOrNull() ?: 0 }.let {
                                Pair(it.getOrElse(0) { 18 }, it.getOrElse(1) { 0 })
                            }
                            cal.set(java.util.Calendar.HOUR_OF_DAY, planH)
                            cal.set(java.util.Calendar.MINUTE, planM)
                            cal.set(java.util.Calendar.SECOND, 0)
                            Pair(cal.timeInMillis, "CONFIRMED_PROPOSED_BOOKING")
                        }

                        val confirmedDurationMinutes = if (parsedWorkout.durationSeconds != null && parsedWorkout.durationSeconds!! > 0) {
                            (parsedWorkout.durationSeconds!! + 59) / 60
                        } else {
                            durationMinutes
                        }

                        val actualGymEndMs = verifiedGymStartMs + confirmedDurationMinutes * 60_000L
                        val isoSdf = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", java.util.Locale.US).apply { timeZone = tz }
                        val verifiedGymStartIso = isoSdf.format(java.util.Date(verifiedGymStartMs))
                        val actualGymEndIso = isoSdf.format(java.util.Date(actualGymEndMs))

                        currentDetails.put("proposed_gym_start", gymWindow.plannedIso)
                        currentDetails.put("proposed_duration_minutes", durationMinutes)
                        currentDetails.put("proposed_gym_end", gymWindow.expectedFinishIso)

                        currentDetails.put("verified_gym_start", verifiedGymStartIso)
                        currentDetails.put("verified_gym_start_ms", verifiedGymStartMs)
                        currentDetails.put("verified_gym_start_source", startExtractionSource)
                        currentDetails.put("confirmed_duration_minutes", confirmedDurationMinutes)
                        currentDetails.put("actual_gym_end", actualGymEndIso)
                        currentDetails.put("actual_gym_end_ms", actualGymEndMs)

                        db.writableDatabase.execSQL(
                            "UPDATE scheduled_activities SET status = 'VERIFIED', validation_status = 'VERIFIED', points = CASE WHEN points > 0 THEN points ELSE 15 END, details_json = ? WHERE activity_id = ?",
                            arrayOf(currentDetails.toString(), actId)
                        )
                        db.writableDatabase.execSQL(
                            "UPDATE daily_state SET gym_status = 'GYM VERIFIED (+15 PTS)' WHERE date = ?",
                            arrayOf(date)
                        )
                    } catch (_: Throwable) {}
                }
                "ALREADY_AWARDED" -> {
                    finalStatus = ProofSubmissionRules.States.APPROVED
                    pointsToAward = 0
                    userMsg = "Gym occurrence already awarded points for this date."
                    try { db.fulfillGymModeObligation(actId, context = context) } catch (_: Throwable) {}
                }
                "NEEDS_REVIEW" -> {
                    finalStatus = "FLAGGED_FOR_REVIEW"
                    pointsToAward = 0
                    userMsg = gymEvaluation.reason

                    try {
                        val currentDetails = try {
                            JSONObject(act?.detailsJson ?: "{}")
                        } catch (_: Throwable) { JSONObject() }
                        currentDetails.put("workout_evaluation", combinedOutput)

                        db.writableDatabase.execSQL(
                            "UPDATE scheduled_activities SET status = 'SUBMITTED', validation_status = 'NEEDS_REVIEW', details_json = ? WHERE activity_id = ?",
                            arrayOf(currentDetails.toString(), actId)
                        )
                        db.writableDatabase.execSQL(
                            "UPDATE daily_state SET gym_status = 'GYM PENDING REVIEW' WHERE date = ?",
                            arrayOf(date)
                        )
                    } catch (_: Throwable) {}
                }
                else -> { // REJECTED
                    finalStatus = ProofSubmissionRules.States.REJECTED
                    pointsToAward = 0
                    userMsg = gymEvaluation.reason
                    try { db.deductGymModeLife(actId, gymEvaluation.reason, context = context) } catch (_: Throwable) {}

                    try {
                        val currentDetails = try {
                            JSONObject(act?.detailsJson ?: "{}")
                        } catch (_: Throwable) { JSONObject() }
                        currentDetails.put("workout_evaluation", combinedOutput)
                        currentDetails.put("ai_validation_state", "REJECTED")
                        currentDetails.put("rejection_reason", gymEvaluation.reason)
                        currentDetails.put("submission_locked", true)

                        val actCv = android.content.ContentValues().apply {
                            put("status", "REJECTED")
                            put("attendance_status", "ABSENT")
                            put("validation_status", "REJECTED")
                            put("points", 0)
                            put("details_json", currentDetails.toString())
                        }
                        db.writableDatabase.update("scheduled_activities", actCv, "activity_id = ?", arrayOf(actId))
                        db.writableDatabase.execSQL(
                            "UPDATE daily_state SET gym_status = 'GYM REJECTED' WHERE date = ?",
                            arrayOf(date)
                        )
                    } catch (_: Throwable) {}
                }
            }

            db.updateProofEvaluationStatus(evaluationId = evalId,

                status = finalStatus,
                pointsAwarded = pointsToAward,
                scoreEventId = if (pointsToAward > 0) "se_approved_$evId" else "",
                evaluatorOutputJson = combinedOutput.toString()
            )
            db.reconcileHourlyGymConflicts(date)

        runCatching {
            PersistentUploadQueueManager.enqueueSubmission(
                context = context, db = db,

                submissionId = subId,

                activityId = actId,
                taskType = "GYM",
                date = date,
                category = "GYM",
                description = "Gym workout photo proof",
                fileBytes = photoBytes,
                fileName = photoName

            )
        }

            try { db.flushSyncEventsQueue(context) } catch (_: Throwable) {}

            notifyListeners(
                ProofSubmissionRules.EvaluationEvent(
                    taskCategory = "GYM",
                    activityId = actId,
                    submissionId = subId,
                    status = finalStatus,
                    pointsAwarded = pointsToAward,
                    message = userMsg
                )
            )
        } catch (e: Exception) {
            db.updateProofEvaluationStatus(evaluationId = evalId,

                status = ProofSubmissionRules.States.RETRYABLE_FAILURE,
                lastError = e.message ?: "Unknown evaluation error"
            )
        } finally {
            inFlightSubmissions.remove(flightKey)
        }
    }

    /**
     * STAGE A: Immediately & durably accept a Mi Fitness sleep report submission.
     */
    @Synchronized
    fun submitSleepReport(
        context: Context,
        db: StrictRecordsLocalDb,
        uris: List<Uri>,
        date: String,
        isDevMode: Boolean
    ): ProofSubmissionRules.SubmissionReceipt {
        val existingSub = db.getSleepSubmission(date, isDevMode)
        if (existingSub != null) {
            val status = existingSub.optString("status", "").uppercase(Locale.US)
            val isPending = status == ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION ||
                    status == ProofSubmissionRules.States.EVALUATING ||
                    status == "SUBMITTED" ||
                    status == ProofSubmissionRules.States.RETRYABLE_FAILURE
            if (!isPending && status.isNotBlank()) {
                return ProofSubmissionRules.SubmissionReceipt(
                    success = false,
                    message = "Sleep report already evaluated for this date. Additional screenshot submissions are closed.",
                    errorCode = "SLEEP_REPORT_ALREADY_EVALUATED"
                )
            }
        }

        val existingEval = db.getProofEvaluationByActivity("act_sleep_$date", date, isDevMode)
        if (existingEval != null) {
            val evalStatus = existingEval.optString("status", "").uppercase(Locale.US)
            if (evalStatus in listOf(ProofSubmissionRules.States.FINALIZED, ProofSubmissionRules.States.APPROVED, ProofSubmissionRules.States.REJECTED)) {
                return ProofSubmissionRules.SubmissionReceipt(
                    success = false,
                    message = "Sleep report already evaluated for this date. Additional screenshot submissions are closed.",
                    errorCode = "SLEEP_REPORT_ALREADY_EVALUATED"
                )
            }
        }

        // Return the original durable receipt; never replace its accepted screenshot.
        if (existingSub != null) {
            resumePendingEvaluations(context, db)
            return ProofSubmissionRules.SubmissionReceipt(
                success = true,
                message = "Original sleep screenshot retained; evaluation will resume.",
                submissionId = existingSub.optString("submission_id"),
                evaluationState = existingSub.optString("status")
            )
        }

        if (uris.isEmpty()) {
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = "Please select at least one Mi Fitness sleep screenshot.",
                errorCode = "ATTACHMENT_REQUIRED"
            )
        }

        if (uris.size > 1) {
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = "Please submit only one sleep-report screenshot.",
                errorCode = "MULTIPLE_ATTACHMENTS_PROHIBITED"
            )
        }

        val primaryUri = uris.first()
        val mimeType = try {
            context.contentResolver.getType(primaryUri)?.lowercase(Locale.ROOT)
        } catch (_: Exception) { null }

        if (mimeType != null && !mimeType.startsWith("image/")) {
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = "Invalid file type. Please submit a valid image screenshot.",
                errorCode = "UNSUPPORTED_FILE_TYPE"
            )
        }

        val subId = "sleep_sub_" + UUID.randomUUID().toString().replace("-", "").take(16)
        val evalId = "eval_sleep_" + UUID.randomUUID().toString().replace("-", "").take(16)
        val acceptedAt = db.authoritativeNowMs()

        // Read and persist the single screenshot atomically
        val proofsDir = File(context.filesDir, "proofs").apply { mkdirs() }
        val targetFile = File(proofsDir, "sleep_report_${date}_${subId}.jpg")
        val tempFile = File(proofsDir, "sleep_report_${date}_${subId}.jpg.tmp")

        try {
            context.contentResolver.openInputStream(primaryUri)?.use { input ->
                FileOutputStream(tempFile).use { output -> input.copyTo(output) }
            }
            if (!tempFile.renameTo(targetFile)) {
                targetFile.writeBytes(tempFile.readBytes())
                tempFile.delete()
            }
        } catch (e: Exception) {
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = "Failed to store screenshot: ${e.message}",
                errorCode = "FILE_SAVE_FAILED"
            )
        }

        if (!targetFile.exists() || targetFile.length() <= 0) {
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = "Empty screenshot file.",
                errorCode = "INVALID_FILE"
            )
        }

        val fileBytes = try { targetFile.readBytes() } catch (_: Exception) { ByteArray(0) }
        if (fileBytes.size < 4) {
            targetFile.delete()
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = "Invalid file type. Please submit a valid image screenshot.",
                errorCode = "UNSUPPORTED_FILE_TYPE"
            )
        }

        // Validate image magic bytes: JPEG (FF D8 FF), PNG (89 50 4E 47), WebP (RIFF....WEBP)
        val isJpeg = fileBytes.size >= 3 &&
                (fileBytes[0] == 0xFF.toByte() && fileBytes[1] == 0xD8.toByte() && fileBytes[2] == 0xFF.toByte())
        val isPng = fileBytes.size >= 4 &&
                (fileBytes[0] == 0x89.toByte() && fileBytes[1] == 0x50.toByte() && fileBytes[2] == 0x4E.toByte() && fileBytes[3] == 0x47.toByte())
        val isWebp = fileBytes.size >= 12 &&
                (fileBytes[0] == 'R'.code.toByte() && fileBytes[1] == 'I'.code.toByte() && fileBytes[2] == 'F'.code.toByte() && fileBytes[3] == 'F'.code.toByte()) &&
                (fileBytes[8] == 'W'.code.toByte() && fileBytes[9] == 'E'.code.toByte() && fileBytes[10] == 'B'.code.toByte() && fileBytes[11] == 'P'.code.toByte())

        if (!isJpeg && !isPng && !isWebp) {
            targetFile.delete()
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = "Invalid file type. Please submit a valid image screenshot.",
                errorCode = "UNSUPPORTED_FILE_TYPE"
            )
        }

        val sha256 = try {
            DisciplineScoringRules.computeSha256Hex(fileBytes)
        } catch (_: Exception) { "" }

        // Both durable records commit together before acceptance is acknowledged.
        val persisted = db.saveInitialSleepSubmission(
            submissionId = subId, reportDate = date, isDevMode = isDevMode,
            imageSha256 = sha256, filePath = targetFile.absolutePath,
            acceptedAtMs = acceptedAt, evaluationId = evalId
        )
        if (!persisted) {
            targetFile.delete()
            return ProofSubmissionRules.SubmissionReceipt(
                success = false, message = "Sleep screenshot was not accepted. Retry the original submission.",
                errorCode = "PERSISTENCE_FAILED"
            )
        }

        // 3. Enqueue background evaluation (STAGE B)
        bgExecutor.execute {
            executeSleepEvaluation(context, db, evalId, subId, date, isDevMode, targetFile, sha256, acceptedAt, uris)
        }

        return ProofSubmissionRules.SubmissionReceipt(
            success = true,
            message = ProofSubmissionRules.acceptanceMessage("SLEEP"),
            submissionId = subId,
            evidenceId = "ev_sleep_" + subId.take(16),
            evaluationState = ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION
        )
    }

    private fun executeSleepEvaluation(
        context: Context,
        db: StrictRecordsLocalDb,
        evalId: String,
        subId: String,
        date: String,
        isDevMode: Boolean,
        targetFile: File,
        sha256: String,
        acceptedAtMs: Long,
        uris: List<Uri>
    ) {
        try {
            db.updateProofEvaluationStatus(evalId, ProofSubmissionRules.States.EVALUATING)

            val primaryUri = Uri.fromFile(targetFile)
            val analysis = EvidenceAnalysisEngine.analyzeUri(
                context.applicationContext,
                primaryUri,
                attachmentId = "sleep_analysis_${System.currentTimeMillis()}",
                taskId = "sleep_report_$date",
                batchId = "sleep_batch_$date"
            )
            val analysisResultObj = analysis
            val ocr = analysis.optJSONObject("ocr")
            val text = ocr?.optString("text") ?: ""
            val blocks = ocr?.optJSONArray("blocks") ?: JSONArray()
            val primarySha = sha256.ifBlank { analysis.optJSONObject("original")?.optString("sha256") ?: "" }

            val validationResult = MiFitnessRegionValidator.validate(
                ocrText = text,
                blocksJson = blocks,
                defaultYear = date.split("-").firstOrNull()?.toIntOrNull() ?: 2026,
                sourceImageId = "sleep_report_$date",
                sourceIntegrityHash = primarySha
            )
            val parsed = validationResult.toParsedSleepData()

            val isDup = db.isDuplicateSleepProof(primarySha, excludeSubmissionId = subId)
            val alreadyAwarded = db.hasSleepBonusBeenAwarded(date, isDevMode)

            val evaluation = MiFitnessSleepBonusRules.evaluateSleepBonus(
                extractedDate = parsed.reportDate,
                onset = parsed.sleepOnset,
                wake = parsed.wakeUp,
                claimedDate = date,
                submissionTimestampMs = acceptedAtMs,
                timezone = db.scheduleTimezone(),
                isDuplicate = isDup,
                alreadyAwarded = alreadyAwarded,
                reconstructedWakeDate = validationResult.reconstructedWakeUpDateTime?.substringBefore("T"),
                isConsistent = !parsed.isAmbiguous && (validationResult.displayedDurationMinutes == null ||
                        validationResult.calculatedDurationMinutes == null ||
                        validationResult.displayedDurationMinutes <= validationResult.calculatedDurationMinutes)
            )

            val awardRes = db.awardSleepBonus(
                submissionId = subId,
                reportDate = date,
                isDevMode = isDevMode,
                parsedData = parsed,
                evaluation = evaluation,
                imageSha256 = primarySha,
                filePath = targetFile.absolutePath,
                acceptedAtMs = acceptedAtMs,
                analysisJson = analysisResultObj
            )

            val awarded = awardRes.optInt("points_awarded", 0)
            val finalStatus = if (evaluation.eligible || evaluation.status == "ALREADY_AWARDED") ProofSubmissionRules.States.APPROVED else ProofSubmissionRules.States.REJECTED

            db.updateProofEvaluationStatus(evaluationId = evalId,

                status = ProofSubmissionRules.States.FINALIZED,
                pointsAwarded = awarded,
                scoreEventId = awardRes.optString("bonus_ledger_event_id"),
                evaluatorOutputJson = awardRes.toString()
            )

            try { db.flushSyncEventsQueue(context) } catch (_: Throwable) {}

            notifyListeners(
                ProofSubmissionRules.EvaluationEvent(
                    taskCategory = "SLEEP",
                    activityId = "act_sleep_$date",
                    submissionId = subId,
                    status = finalStatus,
                    pointsAwarded = awarded,
                    message = if (awarded > 0) {
                        "🏆 Sleep bonus of +$awarded points awarded!"
                    } else if (evaluation.status == "ALREADY_AWARDED") {
                        "Replacement sleep report accepted. Bonus already awarded for $date."
                    } else {
                        "Sleep report evaluated: ${evaluation.reason}"
                    }
                )
            )
        } catch (e: Exception) {
            db.updateProofEvaluationStatus(evaluationId = evalId,

                status = ProofSubmissionRules.States.RETRYABLE_FAILURE,
                lastError = e.message ?: "Unknown evaluation error"
            )
        }
    }

    /**
     * Resumes pending evaluations on app launch or process restart.
     */
    fun resumePendingEvaluations(context: Context, db: StrictRecordsLocalDb) {
        bgExecutor.execute {
            try {
                val pendingList = db.getPendingProofEvaluations()
                for (item in pendingList) {
                    val evalId = item.optString("evaluation_id")
                    val subId = item.optString("submission_id")
                    val actId = item.optString("activity_id")
                    val cat = item.optString("task_category")
                    val date = item.optString("date")
                    val filePath = item.optString("file_path")
                    val file = File(filePath)

                    if (!file.exists() || file.length() == 0L) {
                        db.updateProofEvaluationStatus(evalId, ProofSubmissionRules.States.RETRYABLE_FAILURE, lastError = "Evidence file missing on disk")
                        continue
                    }

                    when (cat.uppercase(Locale.US)) {
                        "HOURLY" -> {
                            val bytes = file.readBytes()
                            executeHourlyEvaluation(
                                context, db, evalId, "ev_${subId.takeLast(12)}", subId, actId, date,
                                category = "HOURLY", description = "Resumed hourly evaluation",
                                attachments = listOf(MainActivity.AttachedProof(bytes, file.name)),
                                primaryBytes = bytes, primaryName = file.name
                            )
                        }
                        "GYM" -> {
                            val bytes = file.readBytes()
                            executeGymEvaluation(
                                context, db, evalId, "ev_${subId.takeLast(12)}", subId, actId, date,
                                photoBytes = bytes, photoName = file.name
                            )
                        }
                        "SLEEP" -> {
                            executeSleepEvaluation(
                                context, db, evalId, subId, date,
                                isDevMode = item.optBoolean("is_dev_mode", false),
                                targetFile = file,
                                sha256 = item.optString("sha256_hash"),
                                acceptedAtMs = db.parseIsoToMs(item.optString("accepted_at")).takeIf { it > 0 } ?: System.currentTimeMillis(),
                                uris = listOf(Uri.fromFile(file))
                            )
                        }
                    }
                }
            } catch (_: Exception) {}
        }
    }

    private fun createPreviewBase64(bytes: ByteArray?): String {
        if (bytes == null || bytes.isEmpty()) return ""
        return try {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
            var sampleSize = 1
            val maxOriginalDim = maxOf(bounds.outWidth, bounds.outHeight)
            while (maxOriginalDim / sampleSize > 1280) { sampleSize *= 2 }
            val decodeOpts = BitmapFactory.Options().apply { inSampleSize = sampleSize }
            val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, decodeOpts)
            if (bmp != null) {
                val outStream = ByteArrayOutputStream()
                var quality = 80
                bmp.compress(Bitmap.CompressFormat.JPEG, quality, outStream)
                while (outStream.size() > 400 * 1024 && quality > 30) {
                    outStream.reset()
                    quality -= 15
                    bmp.compress(Bitmap.CompressFormat.JPEG, quality, outStream)
                }
                bmp.recycle()
                Base64.encodeToString(outStream.toByteArray(), Base64.NO_WRAP)
            } else {
                Base64.encodeToString(bytes.take(200 * 1024).toByteArray(), Base64.NO_WRAP)
            }
        } catch (_: Throwable) { "" }
    }
}
