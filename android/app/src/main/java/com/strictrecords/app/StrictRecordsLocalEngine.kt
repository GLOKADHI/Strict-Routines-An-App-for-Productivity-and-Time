package com.strictrecords.app

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.bluetooth.BluetoothAdapter
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import android.graphics.BitmapFactory
import android.hardware.camera2.CameraManager
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import android.provider.Settings
import android.provider.OpenableColumns
import android.util.Base64
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.File
import java.io.OutputStreamWriter
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.Socket
import java.net.URL
import java.net.URLEncoder
import java.util.Collections
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import java.util.concurrent.Callable
import java.util.concurrent.ExecutorCompletionService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Pure Kotlin centralized scoring & verification rule engine.
 * Shared by Android runtime and JVM unit tests.
 */
object DisciplineScoringRules {
    data class ScorePointEntry(
        val eventType: String,
        val points: Int,
        val evidenceId: String = "",
        val approvedWithRetainedProof: Boolean = false
    )

    const val DEFAULT_WAKEUP_TIME = "07:00"
    const val DEFAULT_ON_TIME_LIMIT = "07:05"
    const val DEFAULT_LATE_LIMIT = "08:00"
    const val DEFAULT_BATH_DEADLINE = "10:00"
    const val DEFAULT_BATH_TIME = "07:30"
    const val DEFAULT_GYM_TIME = "18:30"
    const val DEFAULT_GYM_MIN_MINUTES = 30
    const val WAKEUP_MIN_TIME = "06:00"
    const val WAKEUP_MAX_TIME = "10:00"
    const val WAKEUP_ERROR_MSG = "Wake-up time must be between 6:00 AM and 10:00 AM."

    val HOURLY_ACTIVE_CATEGORIES = listOf("COLLEGE", "STUDY", "EDITING", "VIDEO SHOOT")
    val PRODUCTIVE_ACTIVITIES = setOf(
        "COLLEGE", "STUDY", "EDITING", "VIDEO SHOOT", "VIDEO_SHOOT",
        // Historical categories preserved for backwards compatibility:
        "CODING", "PROJECT", "RESEARCH", "EXERCISE", "GYM", "READING", "WORK"
    )

    fun validateWakeUpTime(rawHhmm: String): Pair<Boolean, String> {
        val clean = rawHhmm.trim().uppercase(Locale.US)
        if (clean.isEmpty()) return Pair(false, WAKEUP_ERROR_MSG)
        return try {
            var isPm = false
            var isAm = false
            var timePart = clean
            if (timePart.endsWith("AM")) {
                isAm = true
                timePart = timePart.removeSuffix("AM").trim()
            } else if (timePart.endsWith("PM")) {
                isPm = true
                timePart = timePart.removeSuffix("PM").trim()
            }
            val parts = timePart.split(":")
            if (parts.size < 2) return Pair(false, WAKEUP_ERROR_MSG)
            var h = parts[0].trim().toInt()
            val m = parts[1].trim().toInt()
            if (isAm || isPm) {
                if (h !in 1..12) return Pair(false, WAKEUP_ERROR_MSG)
                if (isAm && h == 12) h = 0
                if (isPm && h != 12) h += 12
            }
            if (h !in 0..23 || m !in 0..59) return Pair(false, WAKEUP_ERROR_MSG)
            val totalMins = h * 60 + m
            if (totalMins < 360 || totalMins > 600) {
                Pair(false, WAKEUP_ERROR_MSG)
            } else {
                Pair(true, String.format(Locale.US, "%02d:%02d", h, m))
            }
        } catch (_: Exception) {
            Pair(false, WAKEUP_ERROR_MSG)
        }
    }

    fun formatTime12h(hhmm: String): String {
        return try {
            val parts = hhmm.trim().split(":")
            val h = parts[0].toInt()
            val m = parts[1].toInt()
            val period = if (h < 12) "AM" else "PM"
            val h12 = if (h % 12 == 0) 12 else (h % 12)
            String.format(Locale.US, "%02d:%02d %s", h12, m, period)
        } catch (_: Exception) {
            hhmm
        }
    }

    fun evaluateWakeUpAttendance(completedHhmm: String, onTimeLimit: String = DEFAULT_ON_TIME_LIMIT, lateLimit: String = DEFAULT_LATE_LIMIT): String {
        return when {
            completedHhmm <= onTimeLimit -> "ON_TIME"
            completedHhmm <= lateLimit -> "LATE"
            else -> "VERY_LATE"
        }
    }

    fun canFinalizeWakeUp(
        cameraVerified: Boolean,
        bluetoothVerified: Boolean,
        motivationPct: Double,
        requiredPct: Double = 100.0,
        bluetoothOptional: Boolean = false,
        audioOptional: Boolean = false
    ): Boolean {
        val btOk = bluetoothOptional || bluetoothVerified
        val trackOk = audioOptional || (motivationPct >= requiredPct)
        return cameraVerified && btOk && trackOk
    }

    fun isSubmissionWithinWindow(submissionMs: Long, windowStartMs: Long, windowEndMs: Long): Boolean {
        return windowStartMs <= submissionMs && submissionMs < windowEndMs
    }

    fun parseIsoTimestampMs(isoStr: String): Long {
        var clean = isoStr.trim()
        if (clean.isEmpty()) return Long.MIN_VALUE
        val fractional = Regex("\\.(\\d+)(?=(?:Z|[+-]\\d{2}:\\d{2})?$)").find(clean)
        if (fractional != null) {
            val ms = fractional.groupValues[1].take(3).padEnd(3, '0')
            clean = clean.replaceRange(fractional.range, ".$ms")
        }
        val hasZone = clean.endsWith("Z", ignoreCase = true) || Regex("[+-]\\d{2}:\\d{2}$").containsMatchIn(clean)
        val patterns = if (hasZone) {
            listOf("yyyy-MM-dd'T'HH:mm:ssXXX", "yyyy-MM-dd'T'HH:mm:ss.SSSXXX")
        } else {
            listOf("yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd HH:mm:ss")
        }
        for (pattern in patterns) {
            try {
                val sdf = SimpleDateFormat(pattern, Locale.US).apply {
                    timeZone = TimeZone.getTimeZone(if (hasZone) "UTC" else "Asia/Kolkata")
                    isLenient = false
                }
                val pos = java.text.ParsePosition(0)
                val parsed = sdf.parse(clean, pos)
                if (parsed != null && pos.index == clean.length) return parsed.time
            } catch (_: Exception) {
                // Try the next explicitly supported ISO representation.
            }
        }
        return Long.MIN_VALUE
    }

    fun calculateDailyScore(eventsPoints: List<Int>, maxScore: Int = 100, allowOverflow: Boolean = false): Int {
        var pos = 0
        var neg = 0
        for (p in eventsPoints) {
            if (p >= 0) pos += p else neg += kotlin.math.abs(p)
        }
        val raw = pos - neg
        return if (allowOverflow) raw else raw.coerceAtMost(maxScore)
    }

    fun calculateDailyDeductions(eventsPoints: List<Int>): Int =
        eventsPoints.filter { it < 0 }.sumOf { -it }

    fun canAwardPositiveProofPoints(eventType: String, approvedWithRetainedProof: Boolean): Boolean =
        (eventType == "PROOF_APPROVED" ||
            eventType == "WAKEUP_VERIFIED" ||
            eventType == "WAKEUP_COMPLETED" ||
            eventType == "MOTIVATION_TRACK_COMPLETED" ||
            eventType == "WAKEUP_ON_TIME_BONUS" ||
            eventType == "PC_SHUTDOWN_BONUS" ||
            eventType == "SCORE_CORRECTION" ||
            eventType == "INITIAL_RESET" ||
            eventType == "REVERSAL" ||
            eventType == "ADMIN_SCORE_ADJUSTMENT" ||
            eventType == "MI_FITNESS_SLEEP_BONUS" ||
            eventType == "POST_EXAM_CLASS_BONUS") && (approvedWithRetainedProof || eventType == "SCORE_CORRECTION" || eventType == "INITIAL_RESET" || eventType == "REVERSAL" || eventType == "ADMIN_SCORE_ADJUSTMENT" || eventType == "PC_SHUTDOWN_BONUS" || eventType == "MI_FITNESS_SLEEP_BONUS" || eventType == "POST_EXAM_CLASS_BONUS")

    /**
     * Section 4 & 6: Evaluates whether a recorded Windows shutdown timestamp qualifies
     * for the bonus under the strict-before-cutoff rule (< 23:45 default).
     */
    fun evaluatePcShutdownEligibility(
        timeHhmm: String,
        cutoffHhmm: String = "23:45",
        featureEnabled: Boolean = true
    ): Pair<Boolean, String> {
        if (!featureEnabled) {
            return false to "PC shutdown bonus feature is disabled in settings."
        }
        val cleanTime = timeHhmm.trim().let { if (it.length == 5) "$it:00" else it }
        val cleanCutoff = cutoffHhmm.trim().let { if (it.length == 5) "$it:00" else it }
        return if (cleanTime < cleanCutoff) {
            true to "Shutdown occurred at $timeHhmm before $cutoffHhmm cutoff."
        } else {
            false to "Shutdown occurred at $timeHhmm (at or after $cutoffHhmm cutoff)."
        }
    }

    /**
     * Calculate the displayed score only from eligible ledger entries. Positive
     * entries are never accepted from activity claims or missing/unverified proof.
     */
    fun calculateVerifiedDailyScore(
        entries: List<ScorePointEntry>,
        maxScore: Int = 100,
        allowOverflow: Boolean = false
    ): Int {
        val eligiblePoints = entries.filter { entry ->
            entry.points <= 0 || (
                (entry.evidenceId.isNotBlank() || entry.eventType == "SCORE_CORRECTION" || entry.eventType == "INITIAL_RESET" || entry.eventType == "REVERSAL" || entry.eventType == "ADMIN_SCORE_ADJUSTMENT" || entry.eventType == "PC_SHUTDOWN_BONUS" || entry.eventType == "MI_FITNESS_SLEEP_BONUS" || entry.eventType == "POST_EXAM_CLASS_BONUS") &&
                    canAwardPositiveProofPoints(entry.eventType, entry.approvedWithRetainedProof)
                )
        }.map { it.points }
        return calculateDailyScore(eligiblePoints, maxScore, allowOverflow)
    }

    fun evaluateDailyAttendance(
        wakeUpPoints: Int = 15,
        hourlyChecksSubmitted: Int = 0,
        gymSubmitted: Boolean = false,
        bathSubmitted: Boolean = false
    ): Int {
        var score = wakeUpPoints + (hourlyChecksSubmitted * 5)
        if (gymSubmitted) score += 10
        if (bathSubmitted) score += 5
        return score
    }

    fun evaluateScoreMode(score: Int, rewardThreshold: Int = 80, controlledThreshold: Int = 60): String {
        return when {
            score >= rewardThreshold -> "REWARD_UNLOCKED"
            score >= controlledThreshold -> "CONTROLLED_MODE"
            else -> "STRICT_MODE"
        }
    }

    fun evaluateScoreState(
        score: Int,
        eliteThreshold: Int = 90,
        rewardThreshold: Int = 80,
        controlledThreshold: Int = 60
    ): String {
        return when {
            score >= eliteThreshold -> "ELITE_DAY"
            score >= rewardThreshold -> "REWARD_UNLOCKED"
            score >= controlledThreshold -> "CONTROLLED_MODE"
            else -> "STRICT_MODE"
        }
    }

    fun evaluateTaskDependencies(completedTasks: Set<String>, requiredDependencies: List<String>): Boolean {
        return requiredDependencies.all { completedTasks.contains(it) }
    }

    fun evaluateDisciplineLevel(totalXp: Int, currentStreak: Int = 0): String {
        return when {
            totalXp >= 2500 || currentStreak >= 14 -> "ELITE"
            totalXp >= 1500 || currentStreak >= 7 -> "FOCUSED"
            totalXp >= 800 || currentStreak >= 3 -> "CONSISTENT"
            totalXp >= 300 -> "DISCIPLINED"
            else -> "STARTER"
        }
    }

    data class EvidenceFingerprintRecord(
        val evidenceId: String,
        val submissionId: String = "",
        val activityId: String = "",
        val sha256Hash: String,
        val dHash: String = "",
        val aHash: String = "",
        val ocrTokens: Set<String> = emptySet(),
        val createdAt: String = ""
    ) {
        /** Backward-compat aliases for code that used lowercase field names. */
        val dhash: String get() = dHash
        val ahash: String get() = aHash
    }

    data class DuplicateCheckResult(
        val status: String, // NO_MATCH_FOUND, POSSIBLE_DUPLICATE, HIGH_SIMILARITY, EXACT_DUPLICATE, CHECK_INCOMPLETE
        val duplicateRisk: String, // LOW, MEDIUM, HIGH, EXACT
        val matchedEvidenceId: String?,
        val comparisonMethod: String,
        val threshold: Double,
        val modelVersion: String,
        val similarityScore: Double,
        val explanation: String
    ) {
        /** Derived field: "DUPLICATE" for exact matches, "POSSIBLE_DUPLICATE" for near matches, "ORIGINAL" otherwise. */
        val matchStatus: String
            get() = when (status) {
                "EXACT_DUPLICATE" -> "DUPLICATE"
                "HIGH_SIMILARITY", "POSSIBLE_DUPLICATE" -> "POSSIBLE_DUPLICATE"
                else -> "ORIGINAL"
            }
    }

    fun computeSha256Hex(bytes: ByteArray): String {
        val md = java.security.MessageDigest.getInstance("SHA-256")
        return md.digest(bytes).joinToString("") { "%02x".format(it) }
    }

    fun computeSha256Hex(file: File): String {
        return try {
            if (!file.isFile || file.length() == 0L) return ""
            val md = java.security.MessageDigest.getInstance("SHA-256")
            file.inputStream().use { input ->
                val buffer = ByteArray(8192)
                var bytesRead: Int
                while (input.read(buffer).also { bytesRead = it } != -1) {
                    md.update(buffer, 0, bytesRead)
                }
            }
            md.digest().joinToString("") { "%02x".format(it) }
        } catch (_: Throwable) {
            ""
        }
    }

    fun calculateProofSampleSize(width: Int, height: Int, maxDimension: Int = 512): Int {
        var sampleSize = 1
        val maxSide = maxOf(width, height)
        if (maxSide <= 0) return 1
        while (maxSide / sampleSize > maxDimension && sampleSize < 64) {
            sampleSize *= 2
        }
        return sampleSize
    }

    fun hammingDistanceHex(hash1: String, hash2: String): Int {
        val h1 = hash1.trim().lowercase(Locale.US)
        val h2 = hash2.trim().lowercase(Locale.US)
        if (h1.isEmpty() || h1.length != h2.length) return 999
        return try {
            val v1 = java.lang.Long.parseUnsignedLong(h1, 16)
            val v2 = java.lang.Long.parseUnsignedLong(h2, 16)
            java.lang.Long.bitCount(v1 xor v2)
        } catch (_: Exception) {
            999
        }
    }

    fun computeTokenJaccard(tokens1: List<String>, tokens2: List<String>): Double {
        val s1 = tokens1.map { it.lowercase(Locale.US).trim() }.filter { it.length >= 3 }.toSet()
        val s2 = tokens2.map { it.lowercase(Locale.US).trim() }.filter { it.length >= 3 }.toSet()
        if (s1.size < 4 || s2.size < 4) return 0.0
        val inter = s1.intersect(s2).size
        val union = s1.union(s2).size.coerceAtLeast(1)
        return kotlin.math.round((inter.toDouble() / union.toDouble()) * 1000.0) / 1000.0
    }

    fun evaluateDuplicateCheck(
        currentSha256: String,
        currentDhash: String,
        currentOcrTokens: List<String> = emptyList(),
        history: List<EvidenceFingerprintRecord>,
        currentAhash: String = "",
        excludeEvidenceId: String = "",
        excludeSubmissionId: String = "",
        currentActivityId: String = ""
    ): DuplicateCheckResult {
        val shaNorm = currentSha256.trim().lowercase(Locale.US)
        if (shaNorm.isEmpty() && currentDhash.isBlank() && currentOcrTokens.isEmpty()) {
            return DuplicateCheckResult(
                status = "CHECK_INCOMPLETE",
                duplicateRisk = "LOW",
                matchedEvidenceId = null,
                comparisonMethod = "LAYERED_SHA256_DHASH_OCR",
                threshold = 0.875,
                modelVersion = "2.3.0",
                similarityScore = 0.0,
                explanation = "Insufficient fingerprint data to complete duplicate check."
            )
        }

        var bestResult = DuplicateCheckResult(
            status = "NO_MATCH_FOUND",
            duplicateRisk = "LOW",
            matchedEvidenceId = null,
            comparisonMethod = "LAYERED_SHA256_DHASH_OCR",
            threshold = 0.875,
            modelVersion = "2.3.0",
            similarityScore = 0.0,
            explanation = "No prior matching evidence found across registered devices."
        )

        for (rec in history) {
            if (excludeEvidenceId.isNotBlank() && rec.evidenceId == excludeEvidenceId) continue
            if (excludeSubmissionId.isNotBlank() && (rec.submissionId == excludeSubmissionId || rec.evidenceId == excludeSubmissionId)) continue
            if (currentActivityId.isNotBlank() && rec.activityId == currentActivityId) continue

            val pastSha = rec.sha256Hash.trim().lowercase(Locale.US)
            if (shaNorm.isNotEmpty() && pastSha.isNotEmpty() && shaNorm == pastSha) {
                return DuplicateCheckResult(
                    status = "EXACT_DUPLICATE",
                    duplicateRisk = "EXACT",
                    matchedEvidenceId = rec.evidenceId,
                    comparisonMethod = "SHA256_EXACT",
                    threshold = 1.0,
                    modelVersion = "2.3.0",
                    similarityScore = 1.0,
                    explanation = "Cryptographic SHA-256 hash identical to prior evidence (${rec.evidenceId})."
                )
            }

            var bestDist = 999
            var methodUsed = "PERCEPTUAL_DHASH_64"
            if (currentDhash.length == 16 && rec.dhash.length == 16) {
                bestDist = hammingDistanceHex(currentDhash, rec.dhash)
            }
            if (currentAhash.length == 16 && rec.ahash.length == 16) {
                val adist = hammingDistanceHex(currentAhash, rec.ahash)
                if (adist < bestDist) {
                    bestDist = adist
                    methodUsed = "PERCEPTUAL_AHASH_64"
                }
            }

            if (bestDist < 999) {
                val sim = kotlin.math.round(((64.0 - bestDist).coerceAtLeast(0.0) / 64.0) * 1000.0) / 1000.0
                if (bestDist <= 1) {
                    return DuplicateCheckResult(
                        status = "EXACT_DUPLICATE",
                        duplicateRisk = "EXACT",
                        matchedEvidenceId = rec.evidenceId,
                        comparisonMethod = methodUsed,
                        threshold = 0.984,
                        modelVersion = "2.3.0",
                        similarityScore = sim,
                        explanation = "Perceptual hash identical to prior evidence (${rec.evidenceId}) (distance=$bestDist)."
                    )
                } else if (bestDist <= 4) {
                    return DuplicateCheckResult(
                        status = "HIGH_SIMILARITY",
                        duplicateRisk = "HIGH",
                        matchedEvidenceId = rec.evidenceId,
                        comparisonMethod = methodUsed,
                        threshold = 0.9375,
                        modelVersion = "2.3.0",
                        similarityScore = sim,
                        explanation = "High visual similarity to prior evidence (${rec.evidenceId}) (distance=$bestDist, similarity=${(sim * 100).toInt()}%)."
                    )
                } else if (bestDist <= 8 && (bestResult.duplicateRisk == "LOW" || sim > bestResult.similarityScore)) {
                    bestResult = DuplicateCheckResult(
                        status = "POSSIBLE_DUPLICATE",
                        duplicateRisk = "MEDIUM",
                        matchedEvidenceId = rec.evidenceId,
                        comparisonMethod = methodUsed,
                        threshold = 0.875,
                        modelVersion = "2.3.0",
                        similarityScore = sim,
                        explanation = "Possible visual resemblance to prior evidence (${rec.evidenceId}) (similarity=${(sim * 100).toInt()}%)."
                    )
                }
            }

            if (currentOcrTokens.isNotEmpty() && rec.ocrTokens.isNotEmpty()) {
                val textSim = computeTokenJaccard(currentOcrTokens, rec.ocrTokens.toList())
                if (textSim >= 0.92) {
                    return DuplicateCheckResult(
                        status = "HIGH_SIMILARITY",
                        duplicateRisk = "HIGH",
                        matchedEvidenceId = rec.evidenceId,
                        comparisonMethod = "OCR_JACCARD",
                        threshold = 0.92,
                        modelVersion = "2.3.0",
                        similarityScore = textSim,
                        explanation = "Extracted OCR text strongly matches prior evidence (${rec.evidenceId}) (similarity=${(textSim * 100).toInt()}%)."
                    )
                } else if (textSim >= 0.75 && bestResult.duplicateRisk == "LOW") {
                    bestResult = DuplicateCheckResult(
                        status = "POSSIBLE_DUPLICATE",
                        duplicateRisk = "MEDIUM",
                        matchedEvidenceId = rec.evidenceId,
                        comparisonMethod = "OCR_JACCARD",
                        threshold = 0.75,
                        modelVersion = "2.3.0",
                        similarityScore = textSim,
                        explanation = "Extracted OCR text resembles prior evidence (${rec.evidenceId}) (similarity=${(textSim * 100).toInt()}%)."
                    )
                }
            }
        }
        return bestResult
    }

    /**
     * Overload accepting the parameter names used by unit tests and newer call sites.
     * Delegates to the main evaluateDuplicateCheck implementation.
     */
    fun evaluateDuplicateCheck(
        sha256Hash: String,
        dHash: String,
        aHash: String = "",
        ocrText: String = "",
        historicalEvidence: List<EvidenceFingerprintRecord>,
        excludeEvidenceId: String? = null
    ): DuplicateCheckResult {
        val ocrTokens = if (ocrText.isBlank()) emptyList()
        else ocrText.trim().split(Regex("\\W+")).filter { it.length > 3 }
        return evaluateDuplicateCheck(
            currentSha256 = sha256Hash,
            currentDhash = dHash,
            currentOcrTokens = ocrTokens,
            history = historicalEvidence,
            currentAhash = aHash,
            excludeEvidenceId = excludeEvidenceId.orEmpty(),
            excludeSubmissionId = ""
        )
    }

    val SUPPORTED_STRICTBOT_ANIMATIONS = listOf("IDLE", "WALK", "FLY", "SPEAK", "REVIEW", "SUCCESS", "WARNING")
    val STRICTBOT_SUPPORTED_ANIMATIONS = SUPPORTED_STRICTBOT_ANIMATIONS

    fun resolveStrictBotAnimationForEvent(eventType: String, reducedMotion: Boolean = false): String {
        val raw = when (eventType.trim().uppercase(Locale.US)) {
            "DASHBOARD_OPEN", "DASHBOARD_OPENS", "IDLE", "APP_RESUME",
            "OCR_COMPLETED", "SCORE_UPDATED", "REWARD_CHECK" -> "IDLE"
            "NAVIGATE_SECTION", "SECTION_NAVIGATE", "TAB_CHANGED", "WALK" -> "WALK"
            "STRICTBOT_ACTION", "FLY", "OPEN_ASSISTANT" -> "FLY"
            "EVIDENCE_ANALYZING", "AI_VALIDATION_STARTED", "HOURLY_REVIEW", "REVIEW", "PROOF_SUBMITTED",
            "PC_SHUTDOWN_RECORDED", "PC_SHUTDOWN_ATTENDANCE", "PC_SHUTDOWN_SUBMITTED" -> "REVIEW"
            "DAILY_SUMMARY_READY", "BOT_SPEAK", "SPEAK", "OPEN_SUMMARY" -> "SPEAK"
            "DUPLICATE_DETECTED", "DUPLICATE_EVIDENCE", "TASK_MISSED", "TASK_REJECTED",
            "ACTIVITY_MISSED", "WARNING", "PROOF_REJECTED", "RESTRICTION_ENFORCED", "WINDOW_EXPIRED",
            "PC_SHUTDOWN_INELIGIBLE" -> "WARNING"
            "PROOF_ACCEPTED", "PROOF_APPROVED", "TASK_VERIFIED", "WAKEUP_SUBMITTED",
            "WAKEUP_COMPLETE", "STREAK_MILESTONE", "REWARD_UNLOCKED", "SUCCESS",
            "PC_SHUTDOWN_BONUS_CREDITED" -> "SUCCESS"
            else -> "IDLE"
        }
        return if (reducedMotion && (raw == "WALK" || raw == "FLY")) "IDLE" else raw
    }

    data class RestrictedAppSpec(
        val displayName: String,
        val packageName: String,
        val category: String,
        val requiredScore: Int = 80,
        val restrictionPolicy: String = "LOCKED_BELOW_THRESHOLD",
        val enabled: Boolean = true
    )

    data class RestrictionDecision(
        val appName: String,
        val packageName: String,
        val scoreAtDetection: Int,
        val requiredScore: Int,
        val remainingPoints: Int,
        val action: String, // "ALLOWED" or "RESTRICTED"
        val isRestricted: Boolean,
        val reason: String,
        val requestedRestrictionEnabled: Boolean = isRestricted,
        val effectivePrivilegeDecision: String = if (isRestricted) "BLOCK" else "ALLOW",
        val actualEnforcementResult: String = if (isRestricted) "STRICTBOT_POPUP" else "NONE",
        val popupVisibility: String = if (isRestricted) "SHOWN" else "HIDDEN",
        val nextTaskTitle: String = "Complete your current scheduled discipline block",
        val nextTaskWindow: String = "",
        val scoreEligibility: String = if (scoreAtDetection >= requiredScore) "ELIGIBLE" else "BELOW_THRESHOLD",
        val restrictionConfig: String = if (requestedRestrictionEnabled) "ON" else "OFF",
        val platformEnforcement: String = "UNKNOWN",
        val synchronization: String = "SYNCED",
        val historyFreeRefresh: Boolean = false
    ) {
        val target: TargetAppInfo
            get() = findTargetApp(packageName) ?: TargetAppInfo(
                key = packageName.lowercase(Locale.ROOT),
                displayName = appName,
                defaultLockedBelow80 = isRestricted,
                patterns = listOf(packageName),
                packageName = packageName
            )
        val dailyScore: Int get() = scoreAtDetection
        val reasonCode: String get() = action
        val reasonDetail: String get() = reason
    }

    data class ScheduledActivityItem(
        val activityId: String,
        val accountId: String = "user_lok",
        val type: String = "HOURLY", // "WAKE_UP" or "HOURLY"
        val date: String = "",
        val title: String = "",
        val scheduledTime: String = "",
        val windowStart: String = "",
        val windowEnd: String = "",
        val notificationTime: String = "",
        var status: String = "SCHEDULED",
        val submittedAt: String? = null,
        var points: Int = 0,
        val validationRule: String = "PHOTO_EVIDENCE",
        val detailsJson: String = "{}",
        val scheduleId: String = "",
        val scheduleVersion: Int = 0,
        val configuredPoints: Int = 0,
        var submissionEligible: Boolean = true,
        var eligibilityReason: String = ""
    )

    data class ActivitySyncInfo(
        val status: String,
        val displayLabel: String,
        val details: String = ""
    )

    data class TimingDecision(
        val allowed: Boolean,
        val status: String,
        val message: String,
        val activityId: String,
        val windowStart: String,
        val windowEnd: String,
        val points: Int
    )

    data class CountdownState(
        val hasActiveWindow: Boolean,
        val activityId: String?,
        val type: String?,
        val title: String?,
        val state: String,
        val secondsRemaining: Long,
        val secondsUntilOpen: Long,
        val isWarning: Boolean,
        val windowStart: String?,
        val windowEnd: String?,
        val points: Int,
        val submissionEligible: Boolean = true,
        val rejectionReason: String? = null
    )

    val CONFIGURED_RESTRICTED_APPS = TargetAppMonitoringPolicy.TargetApp.values().flatMap { target ->
        val music = target.isMusic
        target.packageNames.map { pkg -> RestrictedAppSpec(target.displayName, pkg,
            if (music) "MUSIC" else "ENTERTAINMENT", if (music) 60 else 80) }
    }

    data class TargetAppInfo(
        val key: String,
        val displayName: String,
        val defaultLockedBelow80: Boolean,
        val patterns: List<String>,
        val packageName: String = patterns.firstOrNull() ?: key
    )

    val TARGET_APPS = TargetAppMonitoringPolicy.TargetApp.values().map { target ->
        TargetAppInfo(target.id, target.displayName, true, target.packageNames.toList())
    }

    fun findTargetApp(packageNameOrApp: String): TargetAppInfo? {
        val exact = TargetAppMonitoringPolicy.targetForPackage(packageNameOrApp)
        if (exact != null) return TARGET_APPS.firstOrNull { it.key == exact.id }?.copy(packageName = packageNameOrApp)
        // Labels remain valid for reward cards. Package identities must match exactly.
        if (packageNameOrApp.contains('.')) return null
        return TARGET_APPS.firstOrNull { it.key == packageNameOrApp ||
            it.displayName.equals(packageNameOrApp.trim(), ignoreCase = true) }
    }

    fun isRestrictedTarget(packageNameOrApp: String, appLocks: JSONObject? = null): Boolean {
        val target = findTargetApp(packageNameOrApp) ?: return false
        if (appLocks != null && appLocks.has(target.key)) {
            return appLocks.optBoolean(target.key, false)
        }
        return target.defaultLockedBelow80
    }

    val FORBIDDEN_ENTERTAINMENT_KEYWORDS: Set<String> = setOf(
        "entertainment", "movie", "movies", "film", "films", "cinema", "trailer", "trailers",
        "teaser", "teasers", "vlog", "vlogs", "vlogger", "prank", "pranks", "roast", "roasting",
        "drama", "dramas", "soap opera", "reaction", "reacting", "tiktok", "reels", "web series",
        "tv show", "tv series", "celebrity", "gossip", "reality show", "bigg boss", "netflix",
        "anime", "cartoon", "cartoons", "animation", "manga",
        "music", "song", "songs", "singing", "singer", "singers", "album", "albums", "remix",
        "remixes", "lofi", "lo-fi", "beat", "beats", "hip hop", "rap", "pop song", "pop music",
        "official video", "music video", "lyric video", "lyrics", "full album", "concert",
        "live concert", "dj", "dj remix", "soundtrack", "ost", "audio track", "track",
        "gaming", "gameplay", "game", "games", "gamer", "gamers", "playthrough", "walkthrough",
        "minecraft", "gta", "gta 5", "roblox", "fortnite", "pubg", "valorant", "free fire",
        "call of duty", "cod", "streamer", "streamers", "twitch", "esports", "speedrun",
        "comedy", "comedian", "comedians", "standup", "stand-up", "funny", "jokes", "joke",
        "meme", "memes", "humor", "parody", "skit", "skits",
        "serial", "serials", "episode", "episodes", "season", "daily soap", "tarak mehta",
        "anupama", "drama episode"
    )

    val EDUCATIONAL_KEYWORDS: Set<String> = setOf(
        "tutorial", "tutorials", "lecture", "lectures", "course", "courses", "study", "studying",
        "learn", "learning", "how to", "education", "educational", "khan academy", "mit", "cs50",
        "harvard", "stanford", "nptel", "coursera", "edx", "udemy", "programming", "python",
        "kotlin", "java", "c++", "rust", "javascript", "typescript", "react", "coding", "algorithm",
        "algorithms", "data structures", "database", "databases", "sql", "math", "mathematics",
        "calculus", "algebra", "linear algebra", "geometry", "physics", "chemistry", "biology",
        "science", "exam", "exams", "upsc", "gate", "jee", "neet", "sat", "gre", "gmat", "cat",
        "crash course", "explanation", "explained", "documentary", "history", "system design",
        "data science", "machine learning", "deep learning", "ai", "artificial intelligence",
        "engineering", "software engineering", "computer science", "economics", "statistics",
        "philosophy", "literature", "linguistics", "grammar", "geography", "astronomy",
        "neuroscience", "psychology", "academic", "university", "college", "school", "research",
        "paper", "dissertation", "thesis", "textbook", "solved", "solution", "solutions",
        "practice problems", "revision", "syllabus", "certification", "documentation",
        "cybersecurity", "networking", "operating system", "linux", "git", "web development",
        "app development", "mobile development", "backend", "frontend", "full course"
    )

    fun validateEducationalUnlockReason(reason: String?): Pair<Boolean, String> {
        val clean = (reason ?: "").trim()
        if (clean.length < 8) {
            return Pair(false, "Reason must be at least 8 characters explaining your educational study purpose.")
        }
        val lower = clean.lowercase(Locale.ROOT)
        for (kw in FORBIDDEN_ENTERTAINMENT_KEYWORDS) {
            val regex = Regex("""\b${Regex.escape(kw)}\b""")
            if (regex.containsMatchIn(lower)) {
                return Pair(false, "Reason rejected: Requests for entertainment, music, gaming, or serials ('$kw') cannot be approved.")
            }
        }
        val hasEduKeyword = EDUCATIONAL_KEYWORDS.any { lower.contains(it) }
        if (!hasEduKeyword && clean.split(" ").size < 3) {
            return Pair(false, "Reason must specify a study topic, lecture, course, or exam subject.")
        }
        return Pair(true, "Reason approved for Educational Study Mode.")
    }

    fun containsForbiddenEntertainment(text: String?): String? {
        if (text.isNullOrEmpty()) return null
        val lower = text.lowercase(Locale.ROOT)
        for (kw in FORBIDDEN_ENTERTAINMENT_KEYWORDS) {
            val regex = Regex("""(?:\b|[\s\-_#])${Regex.escape(kw)}(?:\b|[\s\-_#]|$)""")
            if (regex.containsMatchIn(lower)) {
                return kw
            }
        }
        return null
    }

    fun isEducationalStudyContent(text: String?): Boolean {
        if (text.isNullOrEmpty()) return false
        val lower = text.lowercase(Locale.ROOT)
        for (kw in EDUCATIONAL_KEYWORDS) {
            val regex = Regex("""(?:\b|[\s\-_#])${Regex.escape(kw)}(?:\b|[\s\-_#]|$)""")
            if (regex.containsMatchIn(lower)) {
                return true
            }
        }
        return false
    }

    fun isShortsContent(textOrId: String?): Boolean {
        if (textOrId.isNullOrEmpty()) return false
        val lower = textOrId.lowercase(Locale.ROOT)
        return lower.contains("#shorts") || lower.contains("/shorts/") ||
                lower.contains("shorts - youtube") || lower.contains("youtube shorts") ||
                lower == "shorts" || lower.startsWith("shorts ") || lower.endsWith(" shorts") ||
                lower.contains("shorts_container") || lower.contains("reel_player") ||
                lower.contains("shorts_pivot_item")
    }

    fun isHomeContent(textOrId: String?): Boolean {
        if (textOrId.isNullOrEmpty()) return false
        val lower = textOrId.lowercase(Locale.ROOT)
        return lower == "home" || lower == "home tab" || lower == "home feed" ||
                lower.contains("home_feed") || lower.contains("pivot_home")
    }

    fun detectPlatformEnforcementState(context: Context?): String {
        if (context == null) return "UNKNOWN"
        val hasOverlay = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            Settings.canDrawOverlays(context)
        } else true
        val isAccessibilityActive = StrictAccessibilityService.activeInstance != null
        val isAccessibilityEnabledInSettings = HardwareDeviceVerifier.isAccessibilityServiceEnabled(context, StrictAccessibilityService::class.java)

        return when {
            isAccessibilityActive && hasOverlay -> "APPLIED"
            isAccessibilityEnabledInSettings && !isAccessibilityActive -> "FAILED"
            !hasOverlay -> "FAILED"
            !isAccessibilityEnabledInSettings -> "NOT_CONNECTED"
            else -> "UNKNOWN"
        }
    }

    fun detectAppSyncState(db: StrictRecordsLocalDb?, targetKey: String): String {
        if (db == null) return "SYNCED"
        val isOnline = db.getConfigValue("pairing_status", "OFFLINE") == "ONLINE"
        val pendingCount = try {
            db.readableDatabase.rawQuery(
                "SELECT COUNT(*) FROM sync_events_queue WHERE sync_status = 'PENDING' AND (payload_json LIKE ? OR event_type LIKE '%LOCK%')",
                arrayOf("%$targetKey%")
            ).use { if (it.moveToFirst()) it.getInt(0) else 0 }
        } catch (_: Exception) { 0 }

        return when {
            !isOnline && pendingCount > 0 -> "OFFLINE"
            pendingCount > 0 -> "PENDING"
            !isOnline -> "OFFLINE"
            else -> "SYNCED"
        }
    }

    fun evaluateRestrictionDecision(
        score: Int,
        packageNameOrApp: String,
        rewardThreshold: Int = 80,
        controlledThreshold: Int = 60,
        appLocks: JSONObject? = null,
        isEducationalUnlockActive: Boolean = false,
        nextTaskTitle: String = "Complete your current scheduled discipline block",
        nextTaskWindow: String = "",
        appLocksMap: Map<String, Boolean>? = null,
        context: Context? = null,
        db: StrictRecordsLocalDb? = null,
        isGymModeActive: Boolean? = null,
        isYouTubeRewardActive: Boolean? = null
    ): RestrictionDecision {
        val target = findTargetApp(packageNameOrApp)
        val platformEnforcementState = detectPlatformEnforcementState(context)
        val syncState = detectAppSyncState(db, target?.key ?: packageNameOrApp)
        if (target == null) {
            return RestrictionDecision(
                appName = packageNameOrApp,
                packageName = packageNameOrApp,
                scoreAtDetection = score,
                requiredScore = 0,
                remainingPoints = 0,
                action = "ALLOWED",
                isRestricted = false,
                reason = "Not restricted: System or whitelisted application",
                requestedRestrictionEnabled = false,
                effectivePrivilegeDecision = "ALLOW",
                actualEnforcementResult = "NONE",
                popupVisibility = "HIDDEN",
                nextTaskTitle = nextTaskTitle,
                nextTaskWindow = nextTaskWindow,
                scoreEligibility = "ELIGIBLE",
                restrictionConfig = "OFF",
                platformEnforcement = platformEnforcementState,
                synchronization = syncState
            )
        }

        val musicTarget = TargetAppMonitoringPolicy.targetForId(target.key)?.isMusic == true
        val scoreManaged = musicTarget || target.key in setOf("youtube", "instagram")
        val targetThreshold = if (musicTarget) controlledThreshold else rewardThreshold
        // Eligibility only: the actual ledger score remains untouched everywhere.
        val accessScore = if (scoreManaged) DemoAccessManager.effectivePoints(context, score) else score

        // These four entertainment apps follow the earned-score thresholds, including
        // when an older manual or educational unlock flag is present.
        val hasMapLock = appLocksMap != null && appLocksMap.containsKey(target.key)
        val hasJsonLock = appLocks != null && appLocks.has(target.key)
        val hasRemoteLock = hasMapLock || hasJsonLock
        val requestedLockOn = when {
            hasMapLock -> appLocksMap!![target.key] == true
            hasJsonLock -> appLocks!!.optBoolean(target.key, false)
            else -> target.defaultLockedBelow80
        }
        val isExplicitlyLocked = if (scoreManaged) {
            accessScore < targetThreshold
        } else if (hasRemoteLock) {
            requestedLockOn
        } else {
            if (target.defaultLockedBelow80) accessScore < rewardThreshold else false
        }

        // GYM Mode Temporary Music Access:
        // Permits ONLY Samsung Music and Spotify when GYM Mode session is active.
        val isGymModePermitted = if (target.key in setOf("samsung_music", "spotify")) {
            if (isGymModeActive != null) isGymModeActive
            else db?.isGymModeMusicAllowed() ?: (context?.let { runCatching { StrictRecordsLocalDb.getInstance(it).isGymModeMusicAllowed() }.getOrDefault(false) } ?: false)
        } else false

        // Emergency Travel Mode:
        // Permits travel, cab, and navigation tools during active approved emergency travel
        val isTravelModePermitted = if (target.key in setOf("maps", "uber", "ola", "rapido", "navigation", "transport")) {
            db?.isEmergencyTravelActive() ?: (context?.let { runCatching { StrictRecordsLocalDb.getInstance(it).isEmergencyTravelActive() }.getOrDefault(false) } ?: false)
        } else false

        val isEntertainmentApp = target.key in setOf("youtube", "instagram", "samsung_music", "spotify", "gaana", "jiosaavn")
        val youtubeReward = isEntertainmentApp && (isYouTubeRewardActive ?: YouTubeRewardManager.isYouTubeActive(context))
        val isEducationalVideoAllowed = target.key == "youtube" && EducationalVideoPlaybackTracker.activeVideoId != null && !EducationalVideoPlaybackTracker.isCompleted && EducationalVideoPlaybackTracker.remainingSeconds > 0
        val independentLock = youtubeReward && if (context != null) {
            (db ?: StrictRecordsLocalDb.getInstance(context)).rewardIndependentRestriction(target.key, hasRemoteLock && requestedLockOn)
        } else hasRemoteLock && requestedLockOn
        val isRestricted = if (independentLock) true else if (isGymModePermitted || isTravelModePermitted || youtubeReward || isEducationalVideoAllowed) false else isExplicitlyLocked
        val action = if (isRestricted) "RESTRICTED" else "ALLOWED"
        val reason = if (independentLock) {
            "Independent app restriction remains enabled"
        } else if (isEducationalVideoAllowed) {
            "Educational video access active: ${EducationalVideoPlaybackTracker.remainingSeconds}s remaining"
        } else if (youtubeReward) {
            "Early Proof Entertainment Reward active; actual points: $score"
        } else if (isGymModePermitted) {
            "Unlocked by active GYM Mode music session"
        } else if (isTravelModePermitted) {
            "Unlocked by active Emergency Travel session"
        } else if (isRestricted) {
            if (!scoreManaged && hasRemoteLock) "Locked remotely from PC Control Center / Admin Restriction"
            else "Score threshold not reached (< $targetThreshold points, current $accessScore)"
        } else {
            if (accessScore != score) "Temporary demo access ($accessScore/$targetThreshold); actual points: $score"
            else if (scoreManaged) "Unlocked by earned score ($score/$targetThreshold)" else "Unlocked by PC Control Center or score threshold"
        }

        val scoreEligibilityState = if (youtubeReward) "YOUTUBE_REWARD_ACTIVE" else if (isGymModePermitted) "GYM_MODE_UNLOCKED" else if (accessScore >= targetThreshold) "ELIGIBLE" else "BELOW_THRESHOLD"
        val restrictionConfigState = if (requestedLockOn) "ON" else "OFF"

        val enforcementResult = when {
            !isRestricted -> "NONE"
            platformEnforcementState == "APPLIED" -> "STRICTBOT_POPUP"
            platformEnforcementState == "FAILED" -> "ENFORCEMENT_FAILED"
            platformEnforcementState == "NOT_CONNECTED" -> "NOT_CONNECTED"
            else -> "UNKNOWN"
        }

        return RestrictionDecision(
            appName = target.displayName,
            packageName = packageNameOrApp,
            scoreAtDetection = score,
            requiredScore = targetThreshold,
            remainingPoints = if (youtubeReward) 0 else (targetThreshold - accessScore).coerceAtLeast(0),
            action = action,
            isRestricted = isRestricted,
            reason = reason,
            requestedRestrictionEnabled = requestedLockOn,
            effectivePrivilegeDecision = if (isRestricted) "BLOCK" else "ALLOW",
            actualEnforcementResult = enforcementResult,
            popupVisibility = if (isRestricted && platformEnforcementState == "APPLIED") "SHOWN" else "HIDDEN",
            nextTaskTitle = nextTaskTitle,
            nextTaskWindow = nextTaskWindow,
            scoreEligibility = scoreEligibilityState,
            restrictionConfig = restrictionConfigState,
            platformEnforcement = platformEnforcementState,
            synchronization = syncState,
            historyFreeRefresh = context != null && (DemoAccessManager.historyFreeBoundary() || (target.key == "youtube" && YouTubeRewardManager.historyFreeBoundary()))
        )
    }

    fun isAppAllowed(
        score: Int,
        packageNameOrApp: String,
        rewardThreshold: Int = 80,
        controlledThreshold: Int = 60
    ): Boolean {
        return !evaluateRestrictionDecision(score, packageNameOrApp, rewardThreshold, controlledThreshold).isRestricted
    }
}

object AdminSecurityRules {
    fun canOpenProtectedConsole(isDefaultPin: Boolean, temporaryDefaultAuthorized: Boolean = false): Boolean =
        !isDefaultPin || temporaryDefaultAuthorized
}

/**
 * Hardware Bluetooth Audio & Android UsageStats Verifier (Sections 7, 17, 28, 108, 109, 110)
 */
object HardwareDeviceVerifier {

    @Volatile
    var lastAccessibilityForegroundPkg: String = ""

    @Volatile
    var lastAccessibilityEventTimeMs: Long = 0L

    data class BluetoothRouteCheckResult(
        val isActiveBluetoothEarbuds: Boolean,
        val deviceName: String,
        val routeTypeName: String,
        val failureReason: String
    )

    fun updateAccessibilityForegroundPackage(pkg: String) {
        val clean = pkg.trim()
        if (clean.isEmpty()) return
        if (!TargetAppMonitoringPolicy.isTargetPackage(clean)) {
            lastAccessibilityForegroundPkg = ""
            lastAccessibilityEventTimeMs = 0L
            return
        }
        val ignored = setOf(
            "com.android.systemui",
            "com.samsung.android.honeyboard",
            "com.google.android.inputmethod.latin",
            "android"
        )
        if (clean in ignored) return
        lastAccessibilityForegroundPkg = clean
        lastAccessibilityEventTimeMs = System.currentTimeMillis()
    }

    fun isAccessibilityServiceEnabled(context: Context, serviceClass: Class<*>): Boolean {
        return try {
            val enabledServices = Settings.Secure.getString(
                context.contentResolver,
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
            ).orEmpty()
            val fullId = "${context.packageName}/${serviceClass.name}"
            val shortId = "${context.packageName}/.${serviceClass.simpleName}"
            enabledServices.split(':').any { it == fullId || it == shortId }
        } catch (_: Exception) {
            false
        }
    }

    @android.annotation.SuppressLint("MissingPermission")
    fun verifyActiveBluetoothEarbudsRoute(
        context: Context,
        configuredEarbudsName: String,
        configuredEarbudsAddress: String = ""
    ): BluetoothRouteCheckResult {
        val eval = WakeUpAudioRouteManager.evaluateRoute(context, configuredEarbudsName, configuredEarbudsAddress)
        return BluetoothRouteCheckResult(
            isActiveBluetoothEarbuds = eval.isVerified,
            deviceName = eval.deviceName,
            routeTypeName = eval.routeTypeName,
            failureReason = if (eval.isVerified) "" else eval.actionableMessage
        )
    }

    fun getConnectedBluetoothAudioDevices(context: Context): List<String> {
        val result = mutableListOf<String>()
        try {
            val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager ?: return emptyList()
            val outputs = audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            for (dev in outputs) {
                val type = dev.type
                if (type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP ||
                    type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO ||
                    type == AudioDeviceInfo.TYPE_BLE_HEADSET ||
                    type == AudioDeviceInfo.TYPE_BLE_SPEAKER
                ) {
                    val name = dev.productName?.toString()?.trim().takeUnless { it.isNullOrEmpty() } ?: "Bluetooth Audio Output"
                    if (!result.contains(name)) {
                        result.add(name)
                    }
                }
            }
        } catch (_: Exception) {
        }
        return result
    }

    fun isCameraHardwareAvailable(context: Context): Boolean {
        return try {
            val cm = context.getSystemService(Context.CAMERA_SERVICE) as? CameraManager
            val list = cm?.cameraIdList ?: emptyArray()
            list.isNotEmpty()
        } catch (_: Exception) {
            false
        }
    }

    fun isBluetoothAdapterEnabled(context: Context): Boolean {
        return try {
            val adapter = BluetoothAdapter.getDefaultAdapter()
            adapter != null && adapter.isEnabled
        } catch (_: Exception) {
            false
        }
    }

    fun hasUsageStatsPermission(context: Context): Boolean {
        return try {
            val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
            val mode = appOps.checkOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS,
                Process.myUid(),
                context.packageName
            )
            mode == AppOpsManager.MODE_ALLOWED
        } catch (_: Exception) {
            false
        }
    }

    fun detectActiveForegroundApp(context: Context): String? {
        return detectRealForegroundPackage(context)?.takeIf { it != context.packageName }
    }

    fun detectRealForegroundPackage(context: Context): String? {
        return detectForegroundObservation(context).takeIf { it.shouldMonitor }?.packageName
    }

    fun isMonitoringScreenAvailable(context: Context): Boolean = try {
        val power = context.getSystemService(Context.POWER_SERVICE) as android.os.PowerManager
        val lock = context.getSystemService(Context.KEYGUARD_SERVICE) as android.app.KeyguardManager
        power.isInteractive && !lock.isKeyguardLocked
    } catch (_: Exception) { false }

    /** Intrusive enforcement needs a just-in-time live window confirmation, not historical usage. */
    fun confirmTargetForeground(context: Context, expectedPackage: String): Boolean {
        if (!isMonitoringScreenAvailable(context) || !TargetAppMonitoringPolicy.mayMonitor(context, expectedPackage)) {
            return false
        }
        StrictAccessibilityService.activeInstance?.let { service ->
            // An older target event must not override a new or uncertain window.
            return service.confirmedForegroundTargetPackage() == expectedPackage
        }
        // Usage events support monitoring, not live navigation authorization.
        return false
    }

    fun detectForegroundObservation(context: Context): TargetAppMonitoringPolicy.Observation {
        val now = System.currentTimeMillis()
        if (!TargetAppMonitoringPolicy.canReadUsage(context) || !isMonitoringScreenAvailable(context)) {
            return TargetAppMonitoringPolicy.evaluate(context, null, 0L, now, confirmedForeground = false)
        }
        StrictAccessibilityService.activeInstance?.let { service ->
            // With accessibility available, uncertain/multi-window foreground must not
            // fall back to an older target event. Only fresh package metadata is used.
            val live = service.confirmedForegroundPackage()
            return TargetAppMonitoringPolicy.evaluate(context, live, if (live != null) now else 0L,
                now, confirmedForeground = live != null)
        }
        val fallback = UsageAccessMonitor.foreground(now)
        return TargetAppMonitoringPolicy.evaluate(context, fallback.packageName, fallback.observedAtMs, now,
            confirmedForeground = fallback.confirmed)
    }


    fun isNetworkOrWifiAvailable(context: Context): Boolean {
        return try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                val network = cm?.activeNetwork ?: return false
                val caps = cm.getNetworkCapabilities(network) ?: return false
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            } else {
                @Suppress("DEPRECATION")
                val activeInfo = cm?.activeNetworkInfo
                activeInfo != null && activeInfo.isConnected
            }
        } catch (_: Exception) {
            false
        }
    }

    fun collectLastHourUsageSummary(context: Context): Pair<String, List<String>> {
        if (!TargetAppMonitoringPolicy.canReadUsage(context)) {
            return Pair(TargetAppMonitoringPolicy.monitoringStatus(context), emptyList())
        }
        return try {
            val usm = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
            val end = System.currentTimeMillis()
            val start = end - 3600_000L
            val events = usm.queryEvents(start, end)
                ?: return Pair("Target usage unavailable while device is locked", emptyList())
            val totals = mutableMapOf<String, Long>()
            var activeTarget: String? = null
            var since = start
            fun closeTarget(at: Long) {
                activeTarget?.let { pkg ->
                    val elapsed = (at.coerceIn(start, end) - since).coerceAtLeast(0L)
                    totals[pkg] = (totals[pkg] ?: 0L) + elapsed
                }
                activeTarget = null
            }
            val event = UsageEvents.Event()
            while (events.hasNextEvent()) {
                events.getNextEvent(event)
                when (event.eventType) {
                    UsageEvents.Event.ACTIVITY_RESUMED, 1 -> {
                        closeTarget(event.timeStamp)
                        activeTarget = event.packageName?.takeIf { TargetAppMonitoringPolicy.mayMonitor(context, it) }
                        since = event.timeStamp.coerceIn(start, end)
                    }
                    UsageEvents.Event.ACTIVITY_PAUSED, 2, 23 -> if (event.packageName == activeTarget) closeTarget(event.timeStamp)
                    16, 17 -> closeTarget(event.timeStamp)
                }
            }
            closeTarget(end)
            val detectedPkgs = totals.filterValues { it > 0L }.keys.toList()
            val mins = (totals.values.sum() / 60_000L).toInt()
            Pair("Observed selected-app usage: $mins minutes (${detectedPkgs.size} target apps; last-hour events only)", detectedPkgs)
        } catch (e: Exception) {
            Pair("Usage collection error: ${e.message}", emptyList())
        }
    }
}

/**
 * Offline-First SQLite Database for Android (Sections 1, 20-26, 34, 35, 42, 43, 140)
 */
class StrictRecordsLocalDb(private val appContext: Context) :
    SQLiteOpenHelper(appContext, "strictrecords_android.db", null, 9) {

    internal fun appContextForRewards(): Context = appContext.applicationContext

    companion object {
        const val DATABASE_NAME = "strictrecords_android.db"

        @Volatile
        private var sharedInstance: StrictRecordsLocalDb? = null

        private val ISO_FORMATTER = object : ThreadLocal<SimpleDateFormat>() {
            override fun initialValue(): SimpleDateFormat {
                return SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).apply {
                    timeZone = TimeZone.getTimeZone("Asia/Kolkata")
                }
            }
        }

        fun getInstance(context: Context): StrictRecordsLocalDb {
            return sharedInstance ?: synchronized(this) {
                sharedInstance ?: StrictRecordsLocalDb(context.applicationContext).also {
                    sharedInstance = it
                }
            }
        }
    }

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS daily_state (
                date TEXT NOT NULL,
                is_dev_mode INTEGER NOT NULL DEFAULT 0,
                wakeup_camera INTEGER NOT NULL DEFAULT 0,
                wakeup_camera_path TEXT,
                wakeup_bt INTEGER NOT NULL DEFAULT 0,
                wakeup_bt_device TEXT,
                wakeup_track INTEGER NOT NULL DEFAULT 0,
                wakeup_track_pct REAL NOT NULL DEFAULT 0.0,
                wakeup_status TEXT NOT NULL DEFAULT 'PENDING',
                wakeup_completed_at TEXT,
                snooze_count INTEGER NOT NULL DEFAULT 0,
                bath_status TEXT NOT NULL DEFAULT 'NOT COMPLETED',
                bath_completed_at TEXT,
                gym_status TEXT NOT NULL DEFAULT 'UPCOMING',
                gym_duration_min INTEGER NOT NULL DEFAULT 0,
                hourly_completed INTEGER NOT NULL DEFAULT 0,
                focus_sessions_count INTEGER NOT NULL DEFAULT 0,
                journal_text TEXT NOT NULL DEFAULT '',
                appearance_mode TEXT NOT NULL DEFAULT 'DARK',
                sync_status TEXT NOT NULL DEFAULT 'PENDING SYNC',
                PRIMARY KEY (date, is_dev_mode)
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS score_events (
                event_id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                is_dev_mode INTEGER NOT NULL DEFAULT 0,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                description TEXT NOT NULL,
                points INTEGER NOT NULL,
                audit_json TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS sync_queue (
                sync_id TEXT PRIMARY KEY,
                payload_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING_SYNC'
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS structured_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                is_dev_mode INTEGER NOT NULL DEFAULT 0,
                timestamp TEXT NOT NULL,
                event_code TEXT NOT NULL,
                details TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS proof_reviews (
                evidence_id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT '',
                task_code TEXT NOT NULL,
                task_title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                file_path TEXT NOT NULL,
                submitted_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by TEXT,
                decision TEXT,
                points_awarded INTEGER NOT NULL DEFAULT 0,
                reason_category TEXT,
                reason_note TEXT,
                priority TEXT NOT NULL DEFAULT 'NORMAL',
                sha256_hash TEXT NOT NULL DEFAULT '',
                submission_id TEXT NOT NULL DEFAULT '',
                activity_id TEXT NOT NULL DEFAULT '',
                audit_json TEXT NOT NULL DEFAULT '{}',
                category TEXT NOT NULL DEFAULT ''
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS admin_audit_logs (
                audit_id TEXT PRIMARY KEY,
                admin_user TEXT NOT NULL,
                action_type TEXT NOT NULL,
                field_changed TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                timestamp TEXT NOT NULL,
                device_id TEXT NOT NULL DEFAULT ''
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS deduction_proposals (
                proposal_id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                originating_device TEXT NOT NULL,
                event_type TEXT NOT NULL,
                related_task_id TEXT NOT NULL DEFAULT '',
                applicable_rule TEXT NOT NULL DEFAULT '',
                proposed_points INTEGER NOT NULL,
                reason TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'AWAITING_CONFIRMATION',
                approved_by TEXT NOT NULL DEFAULT '',
                approved_at TEXT NOT NULL DEFAULT '',
                approval_source TEXT NOT NULL DEFAULT '',
                rejection_reason TEXT NOT NULL DEFAULT '',
                finalized_event_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS restriction_events (
                restriction_event_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                device_id TEXT NOT NULL,
                package_name TEXT NOT NULL,
                application_name TEXT NOT NULL,
                score_at_detection INTEGER NOT NULL,
                required_score INTEGER NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS device_pairing (
                pairing_id TEXT PRIMARY KEY,
                device_name TEXT NOT NULL,
                platform TEXT NOT NULL,
                paired_at TEXT NOT NULL,
                pairing_method TEXT NOT NULL,
                trust_status TEXT NOT NULL,
                last_sync_at TEXT,
                last_ip TEXT
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS system_config (
                config_key TEXT PRIMARY KEY,
                config_value TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS scheduled_activities (
                activity_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL DEFAULT 'user_lok',
                type TEXT NOT NULL,
                date TEXT NOT NULL,
                title TEXT NOT NULL,
                scheduled_time TEXT NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                notification_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'SCHEDULED',
                submitted_at TEXT,
                points INTEGER NOT NULL DEFAULT 0,
                validation_rule TEXT NOT NULL DEFAULT 'STANDARD',
                details_json TEXT NOT NULL DEFAULT '{}',
                schedule_id TEXT NOT NULL DEFAULT '',
                schedule_version INTEGER NOT NULL DEFAULT 0,
                configured_points INTEGER NOT NULL DEFAULT 0
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS notification_history (
                notification_key TEXT PRIMARY KEY,
                activity_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                sent_at TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS device_registration (
                device_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                device_name TEXT NOT NULL,
                device_type TEXT NOT NULL DEFAULT 'ANDROID',
                server_endpoint TEXT NOT NULL,
                paired_pc_name TEXT NOT NULL,
                device_token TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'REGISTERED'
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS sync_events_queue (
                event_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                sync_status TEXT NOT NULL DEFAULT 'PENDING',
                sync_error TEXT NOT NULL DEFAULT ''
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS proof_submission_receipts (
                event_id TEXT PRIMARY KEY,
                received_at TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS proof_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                submission_id TEXT NOT NULL,
                activity_id TEXT NOT NULL,
                task_category TEXT NOT NULL,
                date TEXT NOT NULL,
                is_dev_mode INTEGER NOT NULL DEFAULT 0,
                submission_version INTEGER NOT NULL DEFAULT 1,
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                sha256_hash TEXT NOT NULL DEFAULT '',
                submitted_at TEXT NOT NULL,
                accepted_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'SUBMITTED_PENDING_EVALUATION',
                evaluator_output_json TEXT NOT NULL DEFAULT '{}',
                points_awarded INTEGER NOT NULL DEFAULT 0,
                score_event_id TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """.trimIndent()
        )
        try {
            db.execSQL("CREATE INDEX IF NOT EXISTS idx_proof_eval_status ON proof_evaluations (status)")
            db.execSQL("CREATE INDEX IF NOT EXISTS idx_proof_eval_act ON proof_evaluations (activity_id, date)")
            db.execSQL("CREATE INDEX IF NOT EXISTS idx_proof_eval_sub ON proof_evaluations (submission_id)")
            db.execSQL("CREATE INDEX IF NOT EXISTS idx_sched_act_date ON scheduled_activities (date)")
            db.execSQL("CREATE INDEX IF NOT EXISTS idx_proof_rev_date ON proof_reviews (date)")
            db.execSQL("CREATE INDEX IF NOT EXISTS idx_proof_rev_act ON proof_reviews (activity_id)")
            db.execSQL("CREATE INDEX IF NOT EXISTS idx_proof_rev_ev ON proof_reviews (evidence_id)")
            db.execSQL("CREATE INDEX IF NOT EXISTS idx_score_events_date ON score_events (date)")
        } catch (_: Exception) {}
        createIntelligenceTables(db)
    }

    private fun hasColumn(db: SQLiteDatabase, table: String, column: String): Boolean {
        return try {
            db.rawQuery("PRAGMA table_info($table)", null).use { cursor ->
                val nameIdx = cursor.getColumnIndex("name")
                while (cursor.moveToNext()) {
                    if (nameIdx >= 0 && cursor.getString(nameIdx).equals(column, ignoreCase = true)) {
                        return true
                    }
                }
                false
            }
        } catch (_: Exception) {
            false
        }
    }

    private fun createIntelligenceTables(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS activity_journal (
                event_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL DEFAULT 'user_lok',
                date TEXT NOT NULL,
                event_type TEXT NOT NULL,
                provenance TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
                source TEXT NOT NULL DEFAULT 'ANDROID',
                activity_id TEXT NOT NULL DEFAULT '',
                submission_id TEXT NOT NULL DEFAULT '',
                evidence_id TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}'
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS ocr_cache (
                sha256_hash TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                extracted_text TEXT NOT NULL DEFAULT '',
                tokens_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0.0,
                engine TEXT NOT NULL DEFAULT '',
                computed_at TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS evidence_fingerprints (
                evidence_id TEXT PRIMARY KEY,
                submission_id TEXT NOT NULL DEFAULT '',
                activity_id TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL,
                sha256_hash TEXT NOT NULL,
                dhash TEXT NOT NULL DEFAULT '',
                ahash TEXT NOT NULL DEFAULT '',
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                file_size_bytes INTEGER NOT NULL DEFAULT 0,
                ocr_status TEXT NOT NULL DEFAULT 'OCR_UNAVAILABLE',
                ocr_text TEXT NOT NULL DEFAULT '',
                duplicate_status TEXT NOT NULL DEFAULT 'NO_MATCH_FOUND',
                matched_evidence_id TEXT NOT NULL DEFAULT '',
                similarity_score REAL NOT NULL DEFAULT 0.0,
                comparison_method TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS daily_summary_snapshots (
                date TEXT NOT NULL,
                account_id TEXT NOT NULL DEFAULT 'user_lok',
                is_dev_mode INTEGER NOT NULL DEFAULT 0,
                snapshot_version INTEGER NOT NULL DEFAULT 1,
                snapshot_hash TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                formatted_text TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                PRIMARY KEY (date, account_id, is_dev_mode)
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS pc_shutdown_local (
                event_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL DEFAULT 'user_lok',
                device_id TEXT NOT NULL DEFAULT 'windows_lok_pc',
                shutdown_timestamp TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL,
                time_hhmmss TEXT NOT NULL DEFAULT '',
                cutoff_time TEXT NOT NULL DEFAULT '23:45',
                timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
                evidence_source TEXT NOT NULL DEFAULT 'WIN32_API',
                is_qualifying INTEGER NOT NULL DEFAULT 0,
                disqualification_reason TEXT,
                target_reward_date TEXT NOT NULL,
                phone_attendance_id TEXT,
                phone_attendance_time TEXT,
                phone_device_id TEXT,
                evidence_id TEXT,
                photo_submitted_at TEXT,
                bonus_status TEXT NOT NULL DEFAULT 'AWAITING_PHONE_ATTENDANCE',
                bonus_points INTEGER NOT NULL DEFAULT 15,
                bonus_ledger_event_id TEXT,
                rejection_reason TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS cloud_sync_files (
                file_id TEXT PRIMARY KEY,
                local_path TEXT NOT NULL,
                category TEXT NOT NULL,
                drive_file_id TEXT,
                drive_parent_id TEXT,
                file_size_bytes INTEGER NOT NULL DEFAULT 0,
                sha256_hash TEXT,
                md5_hash TEXT,
                mime_type TEXT,
                sync_status TEXT NOT NULL DEFAULT 'PENDING',
                error_message TEXT,
                last_synced_at TEXT,
                created_at TEXT NOT NULL,
                device_id TEXT NOT NULL DEFAULT 'android_phone'
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS cloud_sync_queue (
                queue_id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                action TEXT NOT NULL DEFAULT 'UPLOAD',
                status TEXT NOT NULL DEFAULT 'QUEUED',
                retry_count INTEGER NOT NULL DEFAULT 0,
                next_retry_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS sleep_data_submissions (
                submission_id TEXT PRIMARY KEY,
                report_date TEXT NOT NULL,
                account_id TEXT NOT NULL DEFAULT 'user_lok',
                is_dev_mode INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                accepted_at TEXT NOT NULL,
                status TEXT NOT NULL,
                sleep_onset TEXT,
                wake_up TEXT,
                duration_text TEXT,
                stages_json TEXT NOT NULL DEFAULT '{}',
                sleep_score INTEGER,
                points_awarded INTEGER NOT NULL DEFAULT 0,
                bonus_ledger_event_id TEXT,
                image_sha256 TEXT NOT NULL,
                file_path TEXT,
                analysis_json TEXT NOT NULL DEFAULT '{}',
                rejection_reason TEXT NOT NULL DEFAULT ''
            )
            """.trimIndent()
        )
    }

    override fun onConfigure(db: SQLiteDatabase) {
        super.onConfigure(db)
        try {
            db.enableWriteAheadLogging()
        } catch (_: Exception) {}
    }

    override fun onOpen(db: SQLiteDatabase) {
        super.onOpen(db)
        try {
            ensureEmergencyTravelTablesCreated()
        } catch (_: Exception) {}
        try {
            db.execSQL("CREATE TABLE IF NOT EXISTS scheduled_activities (activity_id TEXT PRIMARY KEY, account_id TEXT NOT NULL DEFAULT 'user_lok', type TEXT NOT NULL, date TEXT NOT NULL, title TEXT NOT NULL, scheduled_time TEXT NOT NULL, window_start TEXT NOT NULL, window_end TEXT NOT NULL, notification_time TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'SCHEDULED', submitted_at TEXT, points INTEGER NOT NULL DEFAULT 0, validation_rule TEXT NOT NULL DEFAULT 'STANDARD', details_json TEXT NOT NULL DEFAULT '{}', schedule_id TEXT NOT NULL DEFAULT '', schedule_version INTEGER NOT NULL DEFAULT 0, configured_points INTEGER NOT NULL DEFAULT 0, attendance_status TEXT NOT NULL DEFAULT '', validation_status TEXT NOT NULL DEFAULT '')")
            if (!hasColumn(db, "scheduled_activities", "attendance_status")) {
                try { db.execSQL("ALTER TABLE scheduled_activities ADD COLUMN attendance_status TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "scheduled_activities", "validation_status")) {
                try { db.execSQL("ALTER TABLE scheduled_activities ADD COLUMN validation_status TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "scheduled_activities", "proof_requirements")) {
                try { db.execSQL("ALTER TABLE scheduled_activities ADD COLUMN proof_requirements TEXT NOT NULL DEFAULT 'NONE'") } catch (_: Exception) {}
            }
            db.execSQL("CREATE TABLE IF NOT EXISTS notification_history (notification_key TEXT PRIMARY KEY, activity_id TEXT NOT NULL, event_type TEXT NOT NULL, sent_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS device_registration (device_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, device_name TEXT NOT NULL, device_type TEXT NOT NULL DEFAULT 'ANDROID', server_endpoint TEXT NOT NULL, paired_pc_name TEXT NOT NULL, device_token TEXT NOT NULL, registered_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'REGISTERED')")
            db.execSQL("CREATE TABLE IF NOT EXISTS sync_events_queue (event_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, device_id TEXT NOT NULL, timestamp TEXT NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, sync_status TEXT NOT NULL DEFAULT 'PENDING', sync_error TEXT NOT NULL DEFAULT '')")
                        db.execSQL("CREATE TABLE IF NOT EXISTS cloud_sync_files (file_id TEXT PRIMARY KEY, local_path TEXT NOT NULL, category TEXT NOT NULL, drive_file_id TEXT, drive_parent_id TEXT, file_size_bytes INTEGER NOT NULL DEFAULT 0, sha256_hash TEXT, md5_hash TEXT, mime_type TEXT, sync_status TEXT NOT NULL DEFAULT 'PENDING', error_message TEXT, last_synced_at TEXT, created_at TEXT NOT NULL, device_id TEXT NOT NULL DEFAULT 'android_phone')")
            db.execSQL("CREATE TABLE IF NOT EXISTS cloud_sync_queue (queue_id TEXT PRIMARY KEY, file_id TEXT NOT NULL, action TEXT NOT NULL DEFAULT 'UPLOAD', status TEXT NOT NULL DEFAULT 'QUEUED', retry_count INTEGER NOT NULL DEFAULT 0, next_retry_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS sleep_data_submissions (submission_id TEXT PRIMARY KEY, report_date TEXT NOT NULL, account_id TEXT NOT NULL DEFAULT 'user_lok', is_dev_mode INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, accepted_at TEXT NOT NULL, status TEXT NOT NULL, sleep_onset TEXT, wake_up TEXT, duration_text TEXT, stages_json TEXT NOT NULL DEFAULT '{}', sleep_score INTEGER, points_awarded INTEGER NOT NULL DEFAULT 0, bonus_ledger_event_id TEXT, image_sha256 TEXT NOT NULL, file_path TEXT, analysis_json TEXT NOT NULL DEFAULT '{}', rejection_reason TEXT NOT NULL DEFAULT '')")
            if (!hasColumn(db, "sync_events_queue", "sync_error")) {
                try { db.execSQL("ALTER TABLE sync_events_queue ADD COLUMN sync_error TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "score_events", "device_id")) {
                try { db.execSQL("ALTER TABLE score_events ADD COLUMN device_id TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "score_events", "account_id")) {
                try { db.execSQL("ALTER TABLE score_events ADD COLUMN account_id TEXT NOT NULL DEFAULT 'user_lok'") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "score_events", "rule_version")) {
                try { db.execSQL("ALTER TABLE score_events ADD COLUMN rule_version INTEGER NOT NULL DEFAULT 1") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "proof_reviews", "sha256_hash")) {
                try { db.execSQL("ALTER TABLE proof_reviews ADD COLUMN sha256_hash TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "proof_reviews", "submission_id")) {
                try { db.execSQL("ALTER TABLE proof_reviews ADD COLUMN submission_id TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "proof_reviews", "activity_id")) {
                try { db.execSQL("ALTER TABLE proof_reviews ADD COLUMN activity_id TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "proof_reviews", "audit_json")) {
                try { db.execSQL("ALTER TABLE proof_reviews ADD COLUMN audit_json TEXT NOT NULL DEFAULT '{}'") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "proof_reviews", "category")) {
                try { db.execSQL("ALTER TABLE proof_reviews ADD COLUMN category TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "sleep_data_submissions", "rejection_reason")) {
                try { db.execSQL("ALTER TABLE sleep_data_submissions ADD COLUMN rejection_reason TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "sleep_data_submissions", "analysis_json")) {
                try { db.execSQL("ALTER TABLE sleep_data_submissions ADD COLUMN analysis_json TEXT NOT NULL DEFAULT '{}'") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "sleep_data_submissions", "file_path")) {
                try { db.execSQL("ALTER TABLE sleep_data_submissions ADD COLUMN file_path TEXT") } catch (_: Exception) {}
            }
            db.execSQL("CREATE TABLE IF NOT EXISTS proof_submission_receipts (event_id TEXT PRIMARY KEY, received_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS focus_sessions (focus_id TEXT PRIMARY KEY, date TEXT NOT NULL, title TEXT NOT NULL, goal TEXT NOT NULL DEFAULT '', target_minutes INTEGER NOT NULL DEFAULT 45, actual_minutes REAL NOT NULL DEFAULT 0.0, started_at TEXT NOT NULL, planned_end_time TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL DEFAULT 'ACTIVE', restrictions_json TEXT NOT NULL DEFAULT '{}', points_awarded INTEGER NOT NULL DEFAULT 0, is_dev_mode INTEGER NOT NULL DEFAULT 0)")
            db.execSQL("CREATE TABLE IF NOT EXISTS focus_settings (config_key TEXT PRIMARY KEY, config_value TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS routine_schedules (schedule_id TEXT PRIMARY KEY, account_id TEXT NOT NULL DEFAULT 'user_lok', schedule_version INTEGER NOT NULL DEFAULT 1, schedule_date TEXT NOT NULL, timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata', status TEXT NOT NULL DEFAULT 'DRAFT', wake_up_time TEXT NOT NULL DEFAULT '07:00', bath_time TEXT NOT NULL DEFAULT '07:30', gym_time TEXT NOT NULL DEFAULT '18:30', gym_duration_minutes INTEGER NOT NULL DEFAULT 90, gym_enabled INTEGER NOT NULL DEFAULT 1, gym_required_by_admin INTEGER NOT NULL DEFAULT 0, custom_activities_json TEXT NOT NULL DEFAULT '[]', is_locked INTEGER NOT NULL DEFAULT 0, confirmed_at TEXT, confirmed_by_device TEXT, reminder_needed INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)")
            if (!hasColumn(db, "routine_schedules", "gym_duration_minutes")) {
                try { db.execSQL("ALTER TABLE routine_schedules ADD COLUMN gym_duration_minutes INTEGER NOT NULL DEFAULT 90") } catch (_: Exception) {}
            }
            db.execSQL("CREATE TABLE IF NOT EXISTS proof_evaluations (evaluation_id TEXT PRIMARY KEY, submission_id TEXT NOT NULL, activity_id TEXT NOT NULL, task_category TEXT NOT NULL, date TEXT NOT NULL, is_dev_mode INTEGER NOT NULL DEFAULT 0, submission_version INTEGER NOT NULL DEFAULT 1, file_path TEXT NOT NULL, file_name TEXT NOT NULL, sha256_hash TEXT NOT NULL DEFAULT '', submitted_at TEXT NOT NULL, accepted_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'SUBMITTED_PENDING_EVALUATION', evaluator_output_json TEXT NOT NULL DEFAULT '{}', points_awarded INTEGER NOT NULL DEFAULT 0, score_event_id TEXT NOT NULL DEFAULT '', retry_count INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS movie_bookings (movie_booking_id TEXT PRIMARY KEY, account_id TEXT NOT NULL DEFAULT 'user_lok', date TEXT NOT NULL, movie_title TEXT NOT NULL, cinema_name TEXT NOT NULL, show_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, duration_minutes INTEGER NOT NULL, lock_start_ms INTEGER NOT NULL, lock_end_ms INTEGER NOT NULL, lock_start_time TEXT NOT NULL, lock_end_time TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Movie booking confirmed', validation_status TEXT NOT NULL DEFAULT 'VERIFIED', ticket_evidence_id TEXT NOT NULL, file_path TEXT NOT NULL DEFAULT '', file_sha256 TEXT NOT NULL DEFAULT '', affected_slots_json TEXT NOT NULL DEFAULT '[]', details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS official_event_bookings (event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT 'user_lok', title TEXT NOT NULL, category TEXT NOT NULL, organization TEXT NOT NULL DEFAULT '', event_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, expected_duration_minutes INTEGER NOT NULL DEFAULT 0, time_zone TEXT NOT NULL DEFAULT 'Asia/Kolkata', event_mode TEXT NOT NULL DEFAULT 'IN_PERSON', venue_or_platform TEXT NOT NULL DEFAULT '', reporting_time TEXT NOT NULL DEFAULT '', shortlist_status TEXT NOT NULL DEFAULT 'CONFIRMED_INDIVIDUAL', evidence_paths TEXT NOT NULL DEFAULT '[]', evidence_sha256s TEXT NOT NULL DEFAULT '[]', user_entered_fields TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'CONFIRMED', exit_consumed INTEGER NOT NULL DEFAULT 0, exit_consumed_at TEXT, exit_session_id TEXT, version INTEGER NOT NULL DEFAULT 1, device_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS gym_mode_sessions (session_id TEXT PRIMARY KEY, gym_activity_id TEXT NOT NULL, activation_time_ms INTEGER NOT NULL, access_expiry_ms INTEGER NOT NULL, proof_deadline_ms INTEGER NOT NULL, status TEXT NOT NULL, toggled_off_early INTEGER NOT NULL DEFAULT 0, toggled_off_at_ms INTEGER, created_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS gym_mode_deductions (deduction_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, gym_activity_id TEXT NOT NULL, reason TEXT NOT NULL, timestamp_ms INTEGER NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS daily_bath_plans (date TEXT PRIMARY KEY, account_id TEXT NOT NULL DEFAULT 'user_lok', timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata', college_decision TEXT NOT NULL, confirmation_status TEXT NOT NULL DEFAULT 'CONFIRMED', revision INTEGER NOT NULL DEFAULT 1, selected_bath_times_json TEXT NOT NULL, activity_intervals_json TEXT NOT NULL, reserved_hourly_act_ids_json TEXT NOT NULL, photo_submission_windows_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS daily_college_exam_plans (date TEXT PRIMARY KEY, account_id TEXT NOT NULL DEFAULT 'user_lok', timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata', attends_college TEXT NOT NULL, college_start_time TEXT, college_end_time TEXT, has_exam TEXT NOT NULL DEFAULT 'NO', exam_start_time TEXT, exam_end_time TEXT, post_exam_start_time TEXT, post_exam_end_time TEXT, revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS exam_prebookings (booking_id TEXT PRIMARY KEY, account_id TEXT NOT NULL DEFAULT 'user_lok', exam_name TEXT NOT NULL, subject TEXT NOT NULL, institution TEXT NOT NULL, exam_date TEXT NOT NULL, official_start_time TEXT NOT NULL, official_end_time TEXT NOT NULL, exam_type TEXT NOT NULL DEFAULT 'SEMESTER', prep_buffer_minutes INTEGER NOT NULL DEFAULT 30, completion_buffer_minutes INTEGER NOT NULL DEFAULT 30, allocation_start_iso TEXT NOT NULL, allocation_end_iso TEXT NOT NULL, allocation_start_ms INTEGER NOT NULL, allocation_end_ms INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'EXAM_APPROVED', validation_provider TEXT NOT NULL DEFAULT 'AI_VALIDATOR', validation_explanation TEXT NOT NULL DEFAULT '', cross_image_consistency TEXT NOT NULL DEFAULT 'CONSISTENT', supporting_images_count INTEGER NOT NULL DEFAULT 1, evidence_files_json TEXT NOT NULL DEFAULT '[]', extracted_data_json TEXT NOT NULL DEFAULT '{}', excused_hourly_slots_json TEXT NOT NULL DEFAULT '[]', additional_info TEXT NOT NULL DEFAULT '', actual_end_time TEXT NOT NULL DEFAULT '', actual_end_iso TEXT NOT NULL DEFAULT '', actual_end_ms INTEGER NOT NULL DEFAULT 0, early_exit_confirmed INTEGER NOT NULL DEFAULT 0, early_exit_confirmed_at TEXT NOT NULL DEFAULT '', monitoring_state TEXT NOT NULL DEFAULT 'EXAM_BOOKING_PENDING', neo_process_detected INTEGER NOT NULL DEFAULT 0, neo_process_pid INTEGER NOT NULL DEFAULT 0, neo_exe_path TEXT NOT NULL DEFAULT '', batch_id TEXT NOT NULL DEFAULT '', test_index INTEGER NOT NULL DEFAULT 1, total_tests INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1, device_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS exam_batches (batch_id TEXT PRIMARY KEY, account_id TEXT NOT NULL DEFAULT 'user_lok', total_tests INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS daily_practice_test_templates (template_id TEXT PRIMARY KEY, template_name TEXT NOT NULL DEFAULT 'Default Daily Practice Template', tests_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS daily_practice_test_schedules (schedule_id TEXT PRIMARY KEY, date TEXT NOT NULL UNIQUE, total_tests INTEGER NOT NULL DEFAULT 2, status TEXT NOT NULL DEFAULT 'SUBMITTED', submitted_at TEXT NOT NULL DEFAULT '', locked_at TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS daily_practice_test_executions (execution_id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, date TEXT NOT NULL, test_index INTEGER NOT NULL, category TEXT NOT NULL, title TEXT NOT NULL, start_time TEXT NOT NULL, duration_minutes INTEGER NOT NULL, end_time TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'SCHEDULED', started_at TEXT NOT NULL DEFAULT '', completed_at TEXT NOT NULL DEFAULT '', score REAL, max_score REAL, points_awarded INTEGER NOT NULL DEFAULT 0, rescheduled_to TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execSQL("CREATE TABLE IF NOT EXISTS early_sleep_video_rewards (reward_id TEXT PRIMARY KEY, wake_date TEXT NOT NULL UNIQUE, status TEXT NOT NULL, sleep_onset TEXT, wake_up TEXT, data_source TEXT, video_id TEXT, video_url TEXT, video_title TEXT, duration_seconds REAL DEFAULT 0, elapsed_seconds REAL DEFAULT 0, completed_at INTEGER DEFAULT 0, created_at INTEGER NOT NULL)")
            try {
                val eCols = mutableSetOf<String>()
                db.rawQuery("PRAGMA table_info(exam_prebookings)", null).use { c ->
                    while (c.moveToNext()) eCols.add(c.getString(1))
                }
                if (eCols.isNotEmpty()) {
                    listOf(
                        "actual_end_time TEXT DEFAULT ''",
                        "actual_end_iso TEXT DEFAULT ''",
                        "actual_end_ms INTEGER DEFAULT 0",
                        "early_exit_confirmed INTEGER DEFAULT 0",
                        "early_exit_confirmed_at TEXT DEFAULT ''",
                        "monitoring_state TEXT DEFAULT 'EXAM_BOOKING_PENDING'",
                        "neo_process_detected INTEGER DEFAULT 0",
                        "neo_process_pid INTEGER DEFAULT 0",
                        "neo_exe_path TEXT DEFAULT ''",
                        "batch_id TEXT DEFAULT ''",
                        "test_index INTEGER DEFAULT 1",
                        "total_tests INTEGER DEFAULT 1"
                    ).forEach { colDef ->
                        val colName = colDef.substringBefore(" ")
                        if (!eCols.contains(colName)) {
                            try { db.execSQL("ALTER TABLE exam_prebookings ADD COLUMN $colDef") } catch (_: Exception) {}
                        }
                    }
                }
            } catch (_: Exception) {}

            try {
                db.execSQL("CREATE INDEX IF NOT EXISTS idx_exam_prebook_date ON exam_prebookings (exam_date)")
                db.execSQL("CREATE INDEX IF NOT EXISTS idx_exam_prebook_status ON exam_prebookings (status)")
            } catch (_: Exception) {}
            try {
                db.execSQL("CREATE INDEX IF NOT EXISTS idx_proof_eval_status ON proof_evaluations (status)")
                db.execSQL("CREATE INDEX IF NOT EXISTS idx_proof_eval_act ON proof_evaluations (activity_id, date)")
                db.execSQL("CREATE INDEX IF NOT EXISTS idx_proof_eval_sub ON proof_evaluations (submission_id)")
                db.execSQL("CREATE INDEX IF NOT EXISTS idx_sched_act_date ON scheduled_activities (date)")
                db.execSQL("CREATE INDEX IF NOT EXISTS idx_proof_rev_date ON proof_reviews (date)")
                db.execSQL("CREATE INDEX IF NOT EXISTS idx_proof_rev_act ON proof_reviews (activity_id)")
                db.execSQL("CREATE INDEX IF NOT EXISTS idx_proof_rev_ev ON proof_reviews (evidence_id)")
                db.execSQL("CREATE INDEX IF NOT EXISTS idx_score_events_date ON score_events (date)")
            } catch (_: Exception) {}
            createIntelligenceTables(db)
        } catch (_: Exception) {}
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        if (oldVersion < 2) {
            if (!hasColumn(db, "daily_state", "focus_sessions_count")) {
                try { db.execSQL("ALTER TABLE daily_state ADD COLUMN focus_sessions_count INTEGER NOT NULL DEFAULT 0") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "daily_state", "journal_text")) {
                try { db.execSQL("ALTER TABLE daily_state ADD COLUMN journal_text TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "daily_state", "appearance_mode")) {
                try { db.execSQL("ALTER TABLE daily_state ADD COLUMN appearance_mode TEXT NOT NULL DEFAULT 'DARK'") } catch (_: Exception) {}
            }
        }
        if (oldVersion < 3) {
            try {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS proof_reviews (
                        evidence_id TEXT PRIMARY KEY,
                        date TEXT NOT NULL,
                        user_id TEXT NOT NULL DEFAULT '',
                        task_code TEXT NOT NULL,
                        task_title TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        file_path TEXT NOT NULL,
                        submitted_at TEXT NOT NULL,
                        reviewed_at TEXT,
                        reviewed_by TEXT,
                        decision TEXT,
                        points_awarded INTEGER NOT NULL DEFAULT 0,
                        reason_category TEXT,
                        reason_note TEXT,
                        priority TEXT NOT NULL DEFAULT 'NORMAL'
                    )
                    """.trimIndent()
                )
            } catch (_: Exception) {}
            try {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS admin_audit_logs (
                        audit_id TEXT PRIMARY KEY,
                        admin_user TEXT NOT NULL,
                        action_type TEXT NOT NULL,
                        field_changed TEXT NOT NULL,
                        old_value TEXT,
                        new_value TEXT,
                        timestamp TEXT NOT NULL,
                        device_id TEXT NOT NULL DEFAULT ''
                    )
                    """.trimIndent()
                )
            } catch (_: Exception) {}
        }
        if (oldVersion < 4) {
            try {
                db.execSQL("CREATE TABLE IF NOT EXISTS restriction_events (restriction_event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, device_id TEXT NOT NULL, package_name TEXT NOT NULL, application_name TEXT NOT NULL, score_at_detection INTEGER NOT NULL, required_score INTEGER NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL)")
                db.execSQL("CREATE TABLE IF NOT EXISTS device_pairing (pairing_id TEXT PRIMARY KEY, device_name TEXT NOT NULL, platform TEXT NOT NULL, paired_at TEXT NOT NULL, pairing_method TEXT NOT NULL, trust_status TEXT NOT NULL, last_sync_at TEXT, last_ip TEXT)")
                db.execSQL("CREATE TABLE IF NOT EXISTS system_config (config_key TEXT PRIMARY KEY, config_value TEXT NOT NULL)")
            } catch (_: Exception) {}
        }
        if (oldVersion < 5) {
            // Keep device registration and pairing intact across upgrades
        }
        if (oldVersion < 6) {
            try { db.execSQL("ALTER TABLE scheduled_activities ADD COLUMN schedule_id TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            try { db.execSQL("ALTER TABLE scheduled_activities ADD COLUMN schedule_version INTEGER NOT NULL DEFAULT 0") } catch (_: Exception) {}
        }
        if (oldVersion < 7) {
            try { db.execSQL("ALTER TABLE scheduled_activities ADD COLUMN configured_points INTEGER NOT NULL DEFAULT 0") } catch (_: Exception) {}
            // Existing positive values are still reliable configured amounts. Rows reset to zero
            // after submission/miss remain unknown rather than being reconstructed or invented.
            try { db.execSQL("UPDATE scheduled_activities SET configured_points = points WHERE configured_points = 0 AND points > 0") } catch (_: Exception) {}
        }
        if (oldVersion < 8) {
            if (!hasColumn(db, "proof_reviews", "sha256_hash")) {
                try { db.execSQL("ALTER TABLE proof_reviews ADD COLUMN sha256_hash TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "proof_reviews", "submission_id")) {
                try { db.execSQL("ALTER TABLE proof_reviews ADD COLUMN submission_id TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "proof_reviews", "activity_id")) {
                try { db.execSQL("ALTER TABLE proof_reviews ADD COLUMN activity_id TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
            if (!hasColumn(db, "proof_reviews", "audit_json")) {
                try { db.execSQL("ALTER TABLE proof_reviews ADD COLUMN audit_json TEXT NOT NULL DEFAULT '{}'") } catch (_: Exception) {}
            }
        }
        if (oldVersion < 9) {
            if (!hasColumn(db, "proof_reviews", "category")) {
                try { db.execSQL("ALTER TABLE proof_reviews ADD COLUMN category TEXT NOT NULL DEFAULT ''") } catch (_: Exception) {}
            }
        }
    }

    override fun onDowngrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // Safe non-destructive downgrade handler: prevents SQLiteException crash on version downgrade
    }

    private fun istFormatter(pattern: String): SimpleDateFormat = SimpleDateFormat(pattern, Locale.US).apply {
        timeZone = TimeZone.getTimeZone("Asia/Kolkata")
        isLenient = false
    }

    fun scheduleTimezone(date: String = istFormatter("yyyy-MM-dd").format(Date(authoritativeNowMs()))): String = runCatching {
        readableDatabase.rawQuery("SELECT timezone FROM routine_schedules WHERE schedule_date=? AND status='SCHEDULED' ORDER BY schedule_version DESC LIMIT 1", arrayOf(date)).use { c ->
            if (c.moveToFirst()) c.getString(0).orEmpty().ifBlank { "Asia/Kolkata" } else "Asia/Kolkata"
        }
    }.getOrDefault("Asia/Kolkata")
    fun nowDate(): String = ScheduleClock.display(authoritativeNowMs(), scheduleTimezone(), "yyyy-MM-dd")
    fun nowTime(): String = ScheduleClock.display(authoritativeNowMs(), scheduleTimezone(), "HH:mm")
    fun nowIso(): String = istFormatter("yyyy-MM-dd HH:mm:ss").format(Date(authoritativeNowMs()))

    private val isEnsuringDay = java.util.concurrent.atomic.AtomicBoolean(false)
    private val isEnsuringActivities = java.util.concurrent.atomic.AtomicBoolean(false)
    private val widgetTaskRowCounts = java.util.concurrent.ConcurrentHashMap<String,Int>()

    /** Observe new saved occurrences without turning routine reads into widget polling. */
    private fun notifyWidgetTaskRows(date: String) {
        runCatching {
            if (date != nowDate()) return@runCatching
            val count=readableDatabase.rawQuery("SELECT COUNT(1) FROM scheduled_activities WHERE date=?",arrayOf(date)).use {
                if(it.moveToFirst()) it.getInt(0) else 0
            }
            if(widgetTaskRowCounts.put(date,count)!=count) StrictWidgetUpdates.request(appContext,"tasks")
            if(widgetTaskRowCounts.size>2) widgetTaskRowCounts.keys.filter { it!=date }.forEach { widgetTaskRowCounts.remove(it) }
        }
    }

    fun ensureDay(date: String, isDevMode: Boolean) {
        if (!isEnsuringDay.compareAndSet(false, true)) return
        try {
            val dev = if (isDevMode) 1 else 0
            writableDatabase.execSQL(
                "INSERT OR IGNORE INTO daily_state (date, is_dev_mode) VALUES (?, ?)",
                arrayOf<Any>(date, dev)
            )
            ensureDailyActivities(date)
            executeDailyResetIfNeeded(date, isDevMode)
            // Initial reset is an explicit user action; opening a day must not reset earned points.
        } finally {
            isEnsuringDay.set(false)
        }
    }

    fun executeDailyResetIfNeeded(date: String = nowDate(), isDevMode: Boolean = false): JSONObject {
        val dev = if (isDevMode) 1 else 0
        val eventId = "DAILY_RESET_${date}_${if (isDevMode) "dev" else "prod"}"
        val res = JSONObject()
        try {
            var alreadyDone = false
            readableDatabase.rawQuery(
                "SELECT event_id, timestamp FROM score_events WHERE event_id = ? OR (date = ? AND event_type = 'DAILY_RESET' AND is_dev_mode = ?)",
                arrayOf(eventId, date, dev.toString())
            ).use { c ->
                if (c.moveToFirst()) {
                    alreadyDone = true
                    res.put("status", "ALREADY_RESET")
                    res.put("reset_applied", false)
                    res.put("event_id", c.getString(0))
                    res.put("date", date)
                }
            }
            if (alreadyDone) return res

            val cal = Calendar.getInstance(TimeZone.getTimeZone("Asia/Kolkata")).apply {
                val parsed = istFormatter("yyyy-MM-dd").parse(date) ?: Date()
                time = parsed
                add(Calendar.DAY_OF_YEAR, -1)
            }
            val prevDate = istFormatter("yyyy-MM-dd").format(cal.time)
            val prevScore = getScore(prevDate, isDevMode)
            val ts = "$date 00:00:00"

            val auditObj = JSONObject().apply {
                put("action", "DAILY_RESET")
                put("daily_period", date)
                put("previous_date", prevDate)
                put("previous_balance", prevScore)
                put("new_balance", 0)
                put("timezone", "Asia/Kolkata")
                put("idempotency_key", eventId)
                put("reset_timestamp", ts)
            }

            writableDatabase.execSQL(
                """
                INSERT OR IGNORE INTO score_events (
                    event_id, date, is_dev_mode, timestamp, event_type,
                    device_id, description, points, rule_version, audit_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                arrayOf(
                    eventId,
                    date,
                    dev,
                    ts,
                    "DAILY_RESET",
                    "local_engine",
                    "Automatic midnight daily points reset for $date",
                    0,
                    auditObj.toString()
                )
            )
            logStructured(date, isDevMode, "DAILY_POINTS_RESET", auditObj.toString())

            res.put("status", "SUCCESS")
            res.put("reset_applied", true)
            res.put("date", date)
            res.put("previous_balance", prevScore)
            res.put("new_balance", 0)
            res.put("event_id", eventId)
        } catch (e: Exception) {
            res.put("status", "ERROR")
            res.put("error", e.message ?: "Unknown error")
        }
        return res
    }

    fun executeInitialResetIfNeeded(targetDate: String = nowDate(), isDevMode: Boolean = false): JSONObject {
        val res = JSONObject()
        val dev = if (isDevMode) 1 else 0
        val account = getDeviceRegistration()?.optString("account_id").takeIf { !it.isNullOrBlank() } ?: "user_lok"
        val eventId = "INITIAL_RESET_${account}_${targetDate}_${if (isDevMode) "dev" else "prod"}"

        try {
            writableDatabase.execSQL(
                "INSERT OR IGNORE INTO daily_state (date, is_dev_mode) VALUES (?, ?)",
                arrayOf<Any>(targetDate, dev)
            )
            var alreadyDone = false
            readableDatabase.rawQuery(
                "SELECT event_id, timestamp, audit_json FROM score_events WHERE event_id = ? OR (date = ? AND event_type = 'INITIAL_RESET' AND is_dev_mode = ?)",
                arrayOf(eventId, targetDate, dev.toString())
            ).use { c ->
                if (c.moveToFirst()) {
                    alreadyDone = true
                    res.put("status", "ALREADY_RESET")
                    res.put("reset_applied", false)
                    res.put("event_id", c.getString(0))
                    res.put("date", targetDate)
                }
            }
            if (alreadyDone) return res

            val currentScore = getScore(targetDate, isDevMode)
            val delta = -currentScore
            val ts = nowIso()

            val auditObj = JSONObject().apply {
                put("action", "INITIAL_POINTS_RESET")
                put("daily_period", targetDate)
                put("previous_balance", currentScore)
                put("correction_amount", delta)
                put("resulting_balance", 0)
                put("timezone", "Asia/Kolkata")
                put("idempotency_key", eventId)
                put("reset_timestamp", ts)
                put("reason", "User-requested initial points reset")
                put("title", "Initial balance reset")
                put("status", "COMPLETED")
                put("category", "INITIAL_RESET")
                put("origin", "Android APK")
            }

            writableDatabase.execSQL(
                """
                INSERT OR IGNORE INTO score_events (
                    event_id, date, is_dev_mode, timestamp, event_type,
                    device_id, description, points, rule_version, audit_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """.trimIndent(),
                arrayOf(
                    eventId,
                    targetDate,
                    dev,
                    ts,
                    "INITIAL_RESET",
                    "android_client",
                    "User-requested initial points reset",
                    delta,
                    auditObj.toString()
                )
            )
            setConfigValue("server_shared_score", "0")
            setConfigValue("server_shared_score_date", targetDate)
            logStructured(targetDate, isDevMode, "INITIAL_POINTS_RESET", auditObj.toString())

            res.put("status", "SUCCESS")
            res.put("reset_applied", true)
            res.put("date", targetDate)
            res.put("previous_balance", currentScore)
            res.put("new_balance", 0)
            res.put("event_id", eventId)
        } catch (e: Exception) {
            res.put("status", "ERROR")
            res.put("error", e.message ?: "Unknown error")
        }
        return res
    }

    fun getUnifiedTransactionHistory(
        targetDate: String? = null,
        categoryFilter: String? = null,
        statusFilter: String? = null,
        searchQuery: String? = null,
        isDevMode: Boolean = false,
        limit: Int = 200
    ): JSONObject {
        val date = targetDate ?: nowDate()
        val dev = if (isDevMode) 1 else 0
        val confirmedScore = getScore(date, isDevMode)

        val transactions = JSONArray()
        var totalGained = 0
        var totalLost = 0

        val scoreQuery = if (targetDate.isNullOrBlank()) {
            "SELECT * FROM score_events WHERE is_dev_mode = ? ORDER BY timestamp DESC LIMIT ?"
        } else {
            "SELECT * FROM score_events WHERE date = ? AND is_dev_mode = ? ORDER BY timestamp DESC LIMIT ?"
        }
        val scoreArgs = if (targetDate.isNullOrBlank()) {
            arrayOf(dev.toString(), limit.toString())
        } else {
            arrayOf(date, dev.toString(), limit.toString())
        }

        try {
            readableDatabase.rawQuery(scoreQuery, scoreArgs).use { c ->
                while (c.moveToNext()) {
                    val evId = c.getString(c.getColumnIndexOrThrow("event_id"))
                    val evDate = c.getString(c.getColumnIndexOrThrow("date"))
                    val ts = c.getString(c.getColumnIndexOrThrow("timestamp"))
                    val etype = c.getString(c.getColumnIndexOrThrow("event_type"))
                    val desc = c.getString(c.getColumnIndexOrThrow("description"))
                    val pts = c.getInt(c.getColumnIndexOrThrow("points"))
                    val auditRaw = c.getString(c.getColumnIndexOrThrow("audit_json")) ?: "{}"
                    val devColIdx = c.getColumnIndex("device_id")
                    val devId = if (devColIdx >= 0) c.getString(devColIdx) ?: "android" else "android"

                    val audit = try { JSONObject(auditRaw) } catch (_: Exception) { JSONObject() }

                    var cat = "General"
                    var title = etype.replace("_", " ").lowercase(Locale.ROOT)
                        .split(" ").joinToString(" ") { word -> word.replaceFirstChar { it.uppercase() } }
                    var ruleApplied = "Standard Scoring Protocol"

                    when (etype) {
                        "INITIAL_RESET" -> {
                            title = "Initial balance reset"
                            cat = "Initial Reset"
                            ruleApplied = "User-requested initial points reset"
                        }
                        "DAILY_RESET" -> {
                            title = "Daily points reset"
                            cat = "Daily Reset"
                            ruleApplied = "Automatic midnight daily reset rule"
                        }
                        "ENTERTAINMENT_VIOLATION" -> {
                            val app = audit.optString("App_Detected", audit.optString("target_app", "Entertainment"))
                            title = "Restriction incident ($app)"
                            cat = "Restriction"
                            ruleApplied = "DisciplineScoringRules.evaluateEntertainmentAccess"
                        }
                        "ADMIN_SCORE_ADJUSTMENT" -> {
                            title = "Manual points adjustment"
                            cat = "Manual Adjustment"
                            ruleApplied = "Administrative score adjustment rule"
                        }
                        "SCORE_CORRECTION" -> {
                            title = "Balance reconciliation"
                            cat = "Corrections"
                            ruleApplied = "Audit balance reconciliation rule"
                        }
                        "REVERSAL" -> {
                            title = "Transaction reversal"
                            cat = "Corrections"
                            ruleApplied = "Administrative compensating transaction"
                        }
                        "WAKEUP_COMPLETED", "WAKEUP_ON_TIME_BONUS" -> {
                            title = "Wake-up task completed"
                            cat = "Wake-up"
                            ruleApplied = "DisciplineScoringRules.evaluateWakeup"
                        }
                        "WAKEUP_MISSED" -> {
                            title = "Wake-up missed"
                            cat = "Wake-up"
                            ruleApplied = "DisciplineScoringRules.evaluateWakeup"
                        }
                        "HOURLY_PRODUCTIVE", "HOURLY_EVIDENCE" -> {
                            title = "Hourly attendance approved"
                            cat = "Hourly"
                            ruleApplied = "HourlySubmissionService.evaluateAttendance"
                        }
                        "HOURLY_MISSED" -> {
                            title = "Hourly check missed"
                            cat = "Hourly"
                            ruleApplied = "HourlySubmissionService.evaluateAttendance"
                        }
                        "GYM_COMPLETED", "GYM_EVIDENCE" -> {
                            title = "Gym proof approved"
                            cat = "Gym"
                            ruleApplied = "GymWindowRules.evaluateGymProof"
                        }
                        "GYM_SKIPPED" -> {
                            title = "Gym session skipped"
                            cat = "Gym"
                            ruleApplied = "GymWindowRules.evaluateGymProof"
                        }
                        "BATH_COMPLETED" -> {
                            title = "Bath task completed"
                            cat = "Bath"
                            ruleApplied = "BathWindowRules.evaluateBath"
                        }
                        "PC_SHUTDOWN_BONUS" -> {
                            title = "PC shutdown bonus awarded"
                            cat = "Shutdown Bonus"
                            ruleApplied = "evaluatePcShutdownEligibility (< 23:45)"
                        }
                        "PROOF_APPROVED" -> {
                            title = "Proof approved"
                            cat = "Tasks"
                            ruleApplied = "Human proof verification approval"
                        }
                        "PROOF_REJECTED" -> {
                            title = "Proof rejected"
                            cat = "Tasks"
                            ruleApplied = "Proof review rejection criteria"
                        }
                        "SNOOZE" -> {
                            title = "Alarm snooze deduction"
                            cat = "Wake-up"
                            ruleApplied = "Wake-up snooze penalty rule"
                        }
                    }

                    var reason = audit.optString("reason", audit.optString("details", desc))
                    if (reason.isBlank()) reason = "Legacy transaction — reason unavailable"

                    val proofId = audit.optString("evidence_id")
                    val entry = DisciplineScoringRules.ScorePointEntry(etype, pts, proofId,
                        pts > 0 && proofId.isNotBlank() && hasApprovedRetainedProof(proofId))
                    val eligible = pts == 0 || DisciplineScoringRules.calculateVerifiedDailyScore(listOf(entry), allowOverflow = true) == pts
                    val amtStr = when {
                        etype == "INITIAL_RESET" -> "Balance set to 0"
                        pts > 0 -> {
                            if (eligible) totalGained += pts
                            "+$pts pts"
                        }
                        pts < 0 -> {
                            if (eligible) totalLost += kotlin.math.abs(pts)
                            "-$pts pts"
                        }
                        else -> "0 pts"
                    }

                    val txStatus = if (!eligible) "Pending verification" else when (etype) {
                        "DAILY_RESET", "INITIAL_RESET" -> "Completed"
                        "REVERSAL" -> "Reversed"
                        "PROOF_REJECTED" -> "Rejected"
                        else -> "Approved"
                    }

                    val origin = when {
                        devId.contains("windows", ignoreCase = true) -> "Windows EXE"
                        devId.contains("admin", ignoreCase = true) -> "Admin Console"
                        devId.contains("system", ignoreCase = true) -> "Authoritative Server"
                        else -> "Android APK"
                    }

                    val humanConf = JSONObject().apply {
                        put("required", pts < 0 || etype == "PROOF_APPROVED")
                        put("obtained", audit.has("approved_by") || audit.optBoolean("human_authorized", false))
                        put("authorized_by", audit.optString("approved_by", audit.optString("admin_id", "")))
                        put("authorized_at", audit.optString("approved_at", ts))
                    }

                    val tx = JSONObject().apply {
                        put("transaction_id", evId)
                        put("title", title)
                        put("amount", pts)
                        put("amount_formatted", amtStr)
                        put("reason", reason)
                        put("timestamp", ts)
                        put("timestamp_formatted", ts)
                        put("date", evDate)
                        put("category", cat)
                        put("status", txStatus)
                        put("confirmed", eligible)
                        put("origin", origin)
                        put("reference_id", evId)
                        put("rule_applied", ruleApplied)
                        put("human_confirmation", humanConf)
                        put("details", JSONObject().apply {
                            put("event_type", etype)
                            put("device_id", devId)
                            put("audit", audit)
                        })
                        put("is_negative_proposal", false)
                    }

                    var match = true
                    if (!categoryFilter.isNullOrBlank() && !categoryFilter.equals("ALL", ignoreCase = true)) {
                        if (!cat.equals(categoryFilter, ignoreCase = true)) match = false
                    }
                    if (!statusFilter.isNullOrBlank() && !statusFilter.equals("ALL", ignoreCase = true)) {
                        if (!txStatus.equals(statusFilter, ignoreCase = true)) match = false
                    }
                    if (!searchQuery.isNullOrBlank()) {
                        val q = searchQuery.trim().lowercase(Locale.ROOT)
                        if (!title.lowercase(Locale.ROOT).contains(q) && !reason.lowercase(Locale.ROOT).contains(q) && !evId.lowercase(Locale.ROOT).contains(q)) {
                            match = false
                        }
                    }
                    if (match) {
                        transactions.put(tx)
                    }
                }
            }
        } catch (_: Exception) {}

        try {
            val propQuery = if (targetDate.isNullOrBlank()) {
                "SELECT * FROM deduction_proposals ORDER BY timestamp DESC LIMIT ?"
            } else {
                "SELECT * FROM deduction_proposals WHERE date = ? ORDER BY timestamp DESC LIMIT ?"
            }
            val propArgs = if (targetDate.isNullOrBlank()) {
                arrayOf(limit.toString())
            } else {
                arrayOf(date, limit.toString())
            }
            readableDatabase.rawQuery(propQuery, propArgs).use { cp ->
                while (cp.moveToNext()) {
                    val pid = if (cp.getColumnIndex("proposal_id") != -1) cp.getString(cp.getColumnIndex("proposal_id")).orEmpty() else ""
                    val pdate = if (cp.getColumnIndex("date") != -1) cp.getString(cp.getColumnIndex("date")).orEmpty() else ""
                    val pts = if (cp.getColumnIndex("proposed_points") != -1) cp.getInt(cp.getColumnIndex("proposed_points")) else 0
                    val reason = if (cp.getColumnIndex("reason") != -1) cp.getString(cp.getColumnIndex("reason")).orEmpty() else ""
                    val ts = if (cp.getColumnIndex("timestamp") != -1) cp.getString(cp.getColumnIndex("timestamp")).orEmpty() else ""
                    val pStatus = if (cp.getColumnIndex("status") != -1) cp.getString(cp.getColumnIndex("status")).orEmpty() else ""
                    val origDev = if (cp.getColumnIndex("originating_device") != -1) cp.getString(cp.getColumnIndex("originating_device")) ?: "Windows EXE" else "Windows EXE"
                    val ruleApp = if (cp.getColumnIndex("applicable_rule") != -1) cp.getString(cp.getColumnIndex("applicable_rule")) ?: "DisciplineScoringRules" else "DisciplineScoringRules"
                    val authBy = if (cp.getColumnIndex("approved_by") != -1) cp.getString(cp.getColumnIndex("approved_by")).orEmpty() else ""
                    val authAt = if (cp.getColumnIndex("approved_at") != -1) cp.getString(cp.getColumnIndex("approved_at")).orEmpty() else ""
                    val decNote = if (cp.getColumnIndex("rejection_reason") != -1) cp.getString(cp.getColumnIndex("rejection_reason")).orEmpty() else ""
                    val cat = "Restriction"

                    val isPending = pStatus == "AWAITING_CONFIRMATION" || pStatus == "PENDING"
                    val isRejected = pStatus == "REJECTED"
                    val isApproved = pStatus == "APPROVED"

                    if (isPending || isRejected) {
                        val uiStatus = if (isPending) "Proposed — Not Deducted" else "Rejected"
                        val amtStr = if (isPending) "-$pts proposed" else "0 pts"
                        val origin = when {
                            origDev.contains("windows", ignoreCase = true) -> "Windows EXE Proposal"
                            origDev.contains("android", ignoreCase = true) -> "Android APK"
                            else -> origDev
                        }

                        val tx = JSONObject().apply {
                            put("transaction_id", pid)
                            put("title", "Restriction incident proposal")
                            put("amount", -pts)
                            put("amount_formatted", amtStr)
                            put("reason", reason.ifBlank { "Violation proposal pending confirmation" })
                            put("timestamp", ts)
                            put("timestamp_formatted", ts)
                            put("date", pdate)
                            put("category", if (cat.isBlank()) "Restriction" else cat)
                            put("status", uiStatus)
                            put("origin", origin)
                            put("reference_id", pid)
                            put("rule_applied", ruleApp)
                            put("human_confirmation", JSONObject().apply {
                                put("required", true)
                                put("obtained", isApproved)
                                put("authorized_by", authBy)
                                put("authorized_at", authAt)
                                put("note", decNote)
                            })
                            put("is_negative_proposal", true)
                            put("proposal_status", pStatus)
                        }

                        var match = true
                        if (!categoryFilter.isNullOrBlank() && !categoryFilter.equals("ALL", ignoreCase = true)) {
                            if (!tx.getString("category").equals(categoryFilter, ignoreCase = true)) match = false
                        }
                        if (!statusFilter.isNullOrBlank() && !statusFilter.equals("ALL", ignoreCase = true)) {
                            if (!uiStatus.equals(statusFilter, ignoreCase = true)) match = false
                        }
                        if (!searchQuery.isNullOrBlank()) {
                            val q = searchQuery.trim().lowercase(Locale.ROOT)
                            if (!tx.getString("title").lowercase(Locale.ROOT).contains(q) &&
                                !reason.lowercase(Locale.ROOT).contains(q) &&
                                !pid.lowercase(Locale.ROOT).contains(q)) {
                                match = false
                            }
                        }
                        if (match) {
                            transactions.put(tx)
                        }
                    }
                }
            }
        } catch (_: Exception) {}

        return JSONObject().apply {
            put("success", true)
            put("current_confirmed_balance", confirmedScore)
            put("total_gained", totalGained)
            put("total_lost", totalLost)
            put("total_count", transactions.length())
            put("date", date)
            put("transactions", transactions)
            put("sync_status", getDayState(date, isDevMode).optString("sync_status", "LOCAL CONFIRMED; PC PENDING"))
        }
    }


    @Volatile private var cachedAnchorMs: Long = 0L
    @Volatile private var cachedAnchorElapsedMs: Long = -1L
    @Volatile private var clockCacheLoaded: Boolean = false

    private fun ensureClockCacheLoaded() {
        if (!clockCacheLoaded) {
            synchronized(this) {
                if (!clockCacheLoaded) {
                    try {
                        cachedAnchorMs = getConfigValue("server_clock_anchor_ms", "0").toLongOrNull() ?: 0L
                        cachedAnchorElapsedMs = getConfigValue("server_clock_anchor_elapsed_ms", "-1").toLongOrNull() ?: -1L
                    } catch (_: Exception) {}
                    clockCacheLoaded = true
                }
            }
        }
    }

    fun syncServerTime(serverMs: Long) {
        val elapsed = SystemClock.elapsedRealtime()
        cachedAnchorMs = serverMs
        cachedAnchorElapsedMs = elapsed
        clockCacheLoaded = true
        setConfigValue("server_clock_anchor_ms", serverMs.toString())
        setConfigValue("server_clock_anchor_elapsed_ms", elapsed.toString())
    }

    fun authoritativeNowMs(): Long {
        ensureClockCacheLoaded()
        val anchorMs = cachedAnchorMs
        val anchorElapsed = cachedAnchorElapsedMs
        val elapsedNow = SystemClock.elapsedRealtime()
        return if (anchorMs > 0 && anchorElapsed >= 0 && elapsedNow >= anchorElapsed) {
            anchorMs + (elapsedNow - anchorElapsed)
        } else {
            System.currentTimeMillis()
        }
    }

    fun hasFreshServerClock(): Boolean {
        ensureClockCacheLoaded()
        val anchorMs = cachedAnchorMs
        val anchorElapsed = cachedAnchorElapsedMs
        val elapsedNow = SystemClock.elapsedRealtime()
        if (anchorMs <= 0 || anchorElapsed < 0 || elapsedNow < anchorElapsed) return false
        return elapsedNow - anchorElapsed <= 6L * 60L * 60L * 1000L
    }

    fun parseIsoToMs(isoStr: String): Long {
        return DisciplineScoringRules.parseIsoTimestampMs(isoStr)
    }

    fun formatMsToIso(ms: Long): String {
        return ISO_FORMATTER.get()?.format(Date(ms)) ?: SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("Asia/Kolkata")
        }.format(Date(ms))
    }

    fun ensureDailyActivities(date: String) {
        if (!isEnsuringActivities.compareAndSet(false, true)) return
        try {
            val istTz = TimeZone.getTimeZone("Asia/Kolkata")
            val cal = Calendar.getInstance(istTz)
            val sdfDay = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { timeZone = istTz }
            val parsedDate = try { sdfDay.parse(date) } catch (_: Exception) { Date() } ?: Date()
            cal.time = parsedDate

            // Local fallback mirrors the locked routine target where available and
            // keeps the submission period independent from that target.
            var wakeTarget = "07:00"
            var scheduleZone = "Asia/Kolkata"
            var scheduleVersion = 1
            runCatching {
                readableDatabase.rawQuery(
                    "SELECT wake_up_time,timezone,schedule_version FROM routine_schedules WHERE schedule_date = ? ORDER BY (CASE WHEN status IN ('SCHEDULED','CONFIRMED','LOCKED') THEN 1 ELSE 0 END) DESC, schedule_version DESC LIMIT 1",
                    arrayOf(date)
                ).use { cursor -> if (cursor.moveToFirst()) {
                    wakeTarget = cursor.getString(0).orEmpty().ifBlank { "07:00" }
                    scheduleZone = cursor.getString(1).orEmpty().ifBlank { "Asia/Kolkata" }
                    scheduleVersion = cursor.getInt(2)
                } }
            }
            cal.timeZone = TimeZone.getTimeZone(scheduleZone)
            cal.time = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { timeZone = cal.timeZone; isLenient = false }.parse(date) ?: parsedDate
            val wakeParts = wakeTarget.split(":")
            cal.set(Calendar.HOUR_OF_DAY, wakeParts.getOrNull(0)?.toIntOrNull() ?: 7)
            cal.set(Calendar.MINUTE, wakeParts.getOrNull(1)?.toIntOrNull() ?: 0)
            cal.set(Calendar.SECOND, 0)
            val wSchedMs = cal.timeInMillis
            cal.set(Calendar.HOUR_OF_DAY, 7)
            cal.set(Calendar.MINUTE, 0)
            val wStartMs = cal.timeInMillis
            cal.set(Calendar.HOUR_OF_DAY, 9)
            cal.set(Calendar.MINUTE, 30)
            val wEndMs = cal.timeInMillis
            val wNotifMs = wStartMs - 1 * 60 * 1000L

            val wakeActId = "ACT-${date.replace("-", "")}-${wakeTarget.replace(":", "").padStart(4, '0')}-WAKEUP"
            val cvWake = ContentValues().apply {
                put("activity_id", wakeActId)
                put("account_id", "user_lok")
                put("type", "WAKE_UP")
                put("date", date)
                put("title", "Wake-Up Protocol ($wakeTarget)")
                put("scheduled_time", formatMsToIso(wSchedMs))
                put("window_start", formatMsToIso(wStartMs))
                put("window_end", formatMsToIso(wEndMs))
                put("notification_time", formatMsToIso(wNotifMs))
                put("status", "SCHEDULED")
                put("points", 15)
                put("configured_points", 15)
                put("validation_rule", "CAMERA_BLUETOOTH_AUDIO")
                put("details_json", "{\"target_time\":\"$wakeTarget\"}")
            }
            val existingWakeUps = mutableListOf<Triple<String, String, String>>()
            readableDatabase.rawQuery(
                "SELECT activity_id, status, details_json FROM scheduled_activities WHERE date = ? AND (type = 'WAKE_UP' OR activity_id LIKE '%WAKEUP%')",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    existingWakeUps.add(Triple(c.getString(0), c.getString(1)?.uppercase(Locale.US).orEmpty(), c.getString(2).orEmpty()))
                }
            }
            val flow = getWakeUpFlowStatus(date, false)
            val hasApprovedWake = readableDatabase.rawQuery(
                "SELECT 1 FROM proof_reviews WHERE task_code IN ('WAKE_UP', 'WAKEUP') AND date = ? AND status IN ('APPROVED', 'VERIFIED')",
                arrayOf(date)
            ).use { it.moveToFirst() }
            val isGenuineWakeSubmitted = hasApprovedWake || flow.optBoolean("all_completed", false) || getDayState(date, false).optString("wakeup_status") in listOf("PENDING_SERVER", "VERIFIED", "COMPLETED")
            val submittedWake = if (isGenuineWakeSubmitted) {
                existingWakeUps.firstOrNull { (_, st, _) ->
                    st in setOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED")
                }
            } else null
            if (submittedWake != null) {
                writableDatabase.execSQL(
                    "DELETE FROM scheduled_activities WHERE date = ? AND (type = 'WAKE_UP' OR activity_id LIKE '%WAKEUP%') AND activity_id != ? AND status NOT IN ('SUBMITTED', 'VALIDATING', 'AI_PROVISIONAL', 'VERIFIED', 'COMPLETED')",
                    arrayOf(date, submittedWake.first)
                )
            } else {
                writableDatabase.execSQL(
                    "DELETE FROM scheduled_activities WHERE date = ? AND (type = 'WAKE_UP' OR activity_id LIKE '%WAKEUP%') AND activity_id != ? AND status IN ('SCHEDULED','UPCOMING','NOTIFIED','OPEN')",
                    arrayOf(date, wakeActId)
                )
                writableDatabase.insertWithOnConflict("scheduled_activities", null, cvWake, SQLiteDatabase.CONFLICT_IGNORE)
            }
            // Apply the current cutoff to unsubmitted cached rows; retain submitted evidence.
            writableDatabase.execSQL(
                "UPDATE scheduled_activities SET window_end = ? WHERE date = ? AND type = 'WAKE_UP' AND status IN ('SCHEDULED','UPCOMING','NOTIFIED','OPEN')",
                arrayOf(formatMsToIso(wEndMs), date)
            )

            // Restore a cached plan even when the PC is temporarily offline. Existing
            // server rows and submitted evidence win; approval remains authoritative.
            val isCollegeDay = getCollegeAttendance(date) == "YES"
            val slots = if (isCollegeDay) {
                HourlyWindowRules.collegeDaySlots(date, scheduleZone)
            } else {
                HourlyWindowRules.slots(date, wakeTarget, scheduleZone)
            }
            val formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).apply { timeZone = TimeZone.getTimeZone(scheduleZone) }
            fun iso(ms: Long) = formatter.format(Date(ms))
            val currentIds = slots.map { it.id }.toSet()

            // Purge unsubmitted obsolete/conflicting hourly slots for this date that do not belong to the canonical schedule
            readableDatabase.rawQuery(
                "SELECT activity_id, status, submitted_at, details_json FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    val aId = c.getString(0)
                    val st = c.getString(1)?.uppercase(Locale.US).orEmpty()
                    val subAt = c.getString(2)
                    val detailsStr = c.getString(3).orEmpty()
                    val details = runCatching { JSONObject(detailsStr) }.getOrDefault(JSONObject())
                    val hasProof = details.optString("submission_id").isNotBlank() || details.optString("evidence_id").isNotBlank()
                    val hasReview = readableDatabase.rawQuery("SELECT 1 FROM proof_reviews WHERE activity_id = ? LIMIT 1", arrayOf(aId)).use { it.moveToFirst() }
                    val hasEval = readableDatabase.rawQuery("SELECT 1 FROM proof_evaluations WHERE activity_id = ? LIMIT 1", arrayOf(aId)).use { it.moveToFirst() }
                    val isSubmitted = hasProof || hasReview || hasEval || !subAt.isNullOrBlank() || st in listOf("SUBMITTED", "VALIDATING", "VERIFIED", "APPROVED", "COMPLETED")

                    if (aId !in currentIds && !isSubmitted) {
                        writableDatabase.execSQL("DELETE FROM scheduled_activities WHERE activity_id = ?", arrayOf(aId))
                    }
                }
            }

            for (slot in slots) {
                val hourly = ContentValues().apply {
                    put("activity_id", slot.id); put("account_id", "user_lok"); put("type", "HOURLY"); put("date", date)
                    put("title", "Hourly Discipline Check (${slot.hourSlot})"); put("scheduled_time", iso(slot.targetMs))
                    put("window_start", iso(slot.startMs)); put("window_end", iso(slot.endMs)); put("notification_time", iso(slot.reminderMs))
                    put("status", "SCHEDULED"); put("points", 0); put("configured_points", 8); put("schedule_version", scheduleVersion)
                    put("validation_rule", "HOURLY_DISCIPLINE_CHECKPOINT")
                    put("details_json", JSONObject().put("hour_slot", slot.hourSlot).put("schedule_source", "ANDROID_CACHED_PLAN").toString())
                }
                writableDatabase.insertWithOnConflict("scheduled_activities", null, hourly, SQLiteDatabase.CONFLICT_IGNORE)
            }
            val bathCount = getMaxBathOccurrences(date)
            if (bathCount >= 2) {
                ensureBathOccurrence(date, 1)
                ensureBathOccurrence(date, 2)
            } else if (bathCount == 1 || hasConfiguredNonCollegeBathTime(date)) {
                ensureBathOccurrence(date, 1)
                try {
                    writableDatabase.execSQL(
                        "DELETE FROM scheduled_activities WHERE date = ? AND (activity_id LIKE '%BATH-2' OR details_json LIKE '%\"occurrence\":2%') AND status IN ('SCHEDULED','UPCOMING','NOTIFIED')",
                        arrayOf(date)
                    )
                } catch (_: Exception) {}
            } else {
                try {
                    writableDatabase.execSQL(
                        "DELETE FROM scheduled_activities WHERE date = ? AND (type = 'BATH' OR activity_id LIKE '%BATH%') AND status IN ('SCHEDULED','UPCOMING','NOTIFIED')",
                        arrayOf(date)
                    )
                } catch (_: Exception) {}
            }
            ensureGymOccurrence(date)
            reconcileHourlyGymConflicts(date)
            reconcileHourlyBathConflicts(date)
            reconcileHourlyTravelConflicts(date)
            reconcileHourlyMovieConflicts(date)
            reconcileHourlyWakeUpConflicts(date)
            reconcileHourlyPostExamClassSlots(date)
            try {
                writableDatabase.execSQL(
                    "DELETE FROM scheduled_activities WHERE type = 'TRAVEL' AND (details_json NOT LIKE '%is_prebooked%' OR details_json IS NULL) AND status NOT IN ('SUBMITTED', 'VALIDATING', 'AI_PROVISIONAL', 'VERIFIED', 'COMPLETED')"
                )
            } catch (_: Exception) {}
        } finally {
            isEnsuringActivities.set(false)
            notifyWidgetTaskRows(date)
        }
    }

    fun getCollegeAttendance(date: String = nowDate()): String {
        val direct = getConfigValue("college_attendance_$date", "")
        if (direct.isNotBlank()) return direct
        val plan = getDailyBathPlan(date)
        if (plan != null && plan.optString("confirmation_status") == "CONFIRMED") {
            return plan.optString("college_decision", "")
        }
        return ""
    }

    fun tomorrowDate(nowMs: Long = authoritativeNowMs(), timezone: String = scheduleTimezone()): String {
        val zone = TimeZone.getTimeZone(timezone)
        val cal = Calendar.getInstance(zone).apply {
            timeInMillis = nowMs
            add(Calendar.DAY_OF_MONTH, 1)
        }
        return SimpleDateFormat("yyyy-MM-dd", Locale.US).apply {
            timeZone = zone
        }.format(cal.time)
    }

    fun getCollegeDecisionDeadlineMs(targetDate: String, timezone: String = scheduleTimezone()): Long =
        BathWindowRules.getCollegeDecisionDeadlineMs(targetDate, timezone)

    fun hasCollegeAttendanceDecision(date: String): Boolean =
        getCollegeAttendance(date).isNotBlank()

    fun isCollegeDecisionDeadlinePassed(targetDate: String, nowMs: Long = authoritativeNowMs(), timezone: String = scheduleTimezone()): Boolean =
        BathWindowRules.isCollegeDecisionDeadlinePassed(targetDate, nowMs, timezone)

    fun getNonCollegeBathSelectionDeadlineMs(date: String = nowDate(), timezone: String = scheduleTimezone()): Long =
        BathWindowRules.getNonCollegeBathSelectionDeadlineMs(date, timezone)

    fun isNonCollegeBathDeadlinePassed(date: String = nowDate(), nowMs: Long = authoritativeNowMs(), timezone: String = scheduleTimezone()): Boolean =
        BathWindowRules.isNonCollegeBathDeadlinePassed(date, nowMs, timezone)

    fun hasConfiguredNonCollegeBathTime(date: String = nowDate()): Boolean {
        val plan = getDailyBathPlan(date)
        if (plan != null && plan.optString("confirmation_status") == "CONFIRMED" && plan.optString("college_decision") == "NO") {
            return (plan.optJSONArray("selected_bath_times")?.length() ?: 0) > 0
        }
        val confirmed=GymPolicyClient.collegeDecision(this,date)
        if (getConfigValue("college_plan_locally_edited_$date","false") != "true" && confirmed.optInt("revision")>0 && !confirmed.optBoolean("attends_college"))
            return (confirmed.optJSONArray("bath_plans")?.length() ?: 0)>0
        val specific = getConfigValue("bath_planned_time_non_college_$date", "")
        return specific.isNotBlank()
    }

    fun getDailyBathPlan(date: String = nowDate()): JSONObject? {
        try {
            readableDatabase.rawQuery(
                "SELECT * FROM daily_bath_plans WHERE date = ? LIMIT 1",
                arrayOf(date)
            ).use { c ->
                if (c.moveToFirst()) {
                    return JSONObject().apply {
                        put("date", c.getString(c.getColumnIndexOrThrow("date")))
                        put("account_id", c.getString(c.getColumnIndexOrThrow("account_id")))
                        put("timezone", c.getString(c.getColumnIndexOrThrow("timezone")))
                        put("college_decision", c.getString(c.getColumnIndexOrThrow("college_decision")))
                        put("confirmation_status", c.getString(c.getColumnIndexOrThrow("confirmation_status")))
                        put("revision", c.getInt(c.getColumnIndexOrThrow("revision")))
                        put("selected_bath_times", JSONArray(c.getString(c.getColumnIndexOrThrow("selected_bath_times_json")) ?: "[]"))
                        put("activity_intervals", JSONArray(c.getString(c.getColumnIndexOrThrow("activity_intervals_json")) ?: "[]"))
                        put("reserved_hourly_act_ids", JSONArray(c.getString(c.getColumnIndexOrThrow("reserved_hourly_act_ids_json")) ?: "[]"))
                        put("photo_submission_windows", JSONArray(c.getString(c.getColumnIndexOrThrow("photo_submission_windows_json")) ?: "[]"))
                        put("created_at", c.getString(c.getColumnIndexOrThrow("created_at")))
                        put("updated_at", c.getString(c.getColumnIndexOrThrow("updated_at")))
                    }
                }
            }
        } catch (_: Exception) {}
        val raw = getConfigValue("daily_bath_plan_$date", "")
        if (raw.isNotBlank()) {
            return runCatching { JSONObject(raw) }.getOrNull()
        }
        return null
    }

    fun isBathScheduleConfirmed(date: String = nowDate()): Boolean {
        val plan = getDailyBathPlan(date)
        if (plan != null && plan.optString("confirmation_status") == "CONFIRMED") return true
        return getConfigValue("bath_schedule_confirmed_$date", "false") == "true"
    }

    fun canEditTodayBathPlan(date: String = nowDate()): Pair<Boolean, String> {
        if (isBathScheduleConfirmed(date)) {
            val plannedStr = getDailyBathPlan(date)?.optJSONArray("selected_bath_times")?.let { arr ->
                (0 until arr.length()).map { arr.optString(it) }.joinToString(", ")
            } ?: ""
            val timeMsg = if (plannedStr.isNotBlank()) " ($plannedStr)" else ""
            return false to "Today's bath time is locked$timeMsg."
        }
        val completedOrSubmitted = getBathSubmissionCount(date)
        val maxAllowed = getMaxBathOccurrences(date)
        if (completedOrSubmitted >= maxAllowed && maxAllowed > 0) {
            return false to "Today's bath protocols have already been submitted ($completedOrSubmitted/$maxAllowed completed)."
        }
        return true to ""
    }

    fun confirmDailyBathPlan(
        date: String = nowDate(),
        collegeDecision: String,
        plannedTimes: List<String>,
        allowOverwrite: Boolean = false
    ): JSONObject {
        if (!allowOverwrite && isBathScheduleConfirmed(date)) {
            return JSONObject().put("success", false).put("error", "Today's bath plan is locked and cannot be modified.")
        }
        val cleanCollege = collegeDecision.uppercase(Locale.US).trim()
        if (cleanCollege !in listOf("YES", "NO")) {
            return JSONObject().put("success", false).put("error", "Invalid college decision: must be YES or NO.")
        }
        val isCollege = cleanCollege == "YES"
        val requiredCount = plannedTimes.size.coerceIn(1, 2)
        if (plannedTimes.isEmpty()) {
            return JSONObject().put("success", false).put("error", "Please select planned time for today's bath time.")
        }

        val validation = CollegeExamScheduleRules.validateIndependentBathSelection(requiredCount, plannedTimes)
        if (!validation.isValid) {
            return JSONObject().put("success", false).put("error", validation.errorMessage ?: "Invalid bath selection.")
        }

        val store=writableDatabase
        store.beginTransaction()
        try {
        val tz = scheduleTimezone()
        val nowTs = nowIso()
        val existingPlan = getDailyBathPlan(date)
        val currentRev = existingPlan?.optInt("revision", 0) ?: 0
        val newRev = currentRev + 1
        val createdTs = existingPlan?.optString("created_at")?.takeIf { it.isNotBlank() } ?: nowTs

        val activityIntervals = JSONArray()
        val reservedHourlyIds = JSONArray()
        val photoSubmissionWindows = JSONArray()
        val selectedTimesArr = JSONArray()

        for (i in 0 until requiredCount) {
            val t = BathWindowRules.normalizeHhmm(plannedTimes[i])
            selectedTimesArr.put(t)
            val win = BathWindowRules.calculateBathWindow(
                occurrence = i + 1,
                isCollege = isCollege,
                date = date,
                plannedHhmm = t,
                timezone = tz
            )
            activityIntervals.put(win.activityIntervalDisplay)
            reservedHourlyIds.put(win.heldHourlyActId)
            photoSubmissionWindows.put(win.proofWindowDisplay)
        }

        val cv = ContentValues().apply {
            put("date", date)
            put("account_id", "user_lok")
            put("timezone", tz)
            put("college_decision", cleanCollege)
            put("confirmation_status", "CONFIRMED")
            put("revision", newRev)
            put("selected_bath_times_json", selectedTimesArr.toString())
            put("activity_intervals_json", activityIntervals.toString())
            put("reserved_hourly_act_ids_json", reservedHourlyIds.toString())
            put("photo_submission_windows_json", photoSubmissionWindows.toString())
            put("created_at", createdTs)
            put("updated_at", nowTs)
        }
        writableDatabase.insertWithOnConflict("daily_bath_plans", null, cv, SQLiteDatabase.CONFLICT_REPLACE)

        val planObj = JSONObject().apply {
            put("date", date)
            put("account_id", "user_lok")
            put("timezone", tz)
            put("college_decision", cleanCollege)
            put("confirmation_status", "CONFIRMED")
            put("revision", newRev)
            put("selected_bath_times", selectedTimesArr)
            put("activity_intervals", activityIntervals)
            put("reserved_hourly_act_ids", reservedHourlyIds)
            put("photo_submission_windows", photoSubmissionWindows)
            put("created_at", createdTs)
            put("updated_at", nowTs)
        }

        setConfigValue("daily_bath_plan_$date", planObj.toString())
        setConfigValue("bath_schedule_confirmed_$date", "true")
        setConfigValue("bath_popup_shown_$date", "true")
        setConfigValue("bath_session_count_$date", requiredCount.toString())

        setCollegeAttendance(date, cleanCollege)
        for (i in 0 until requiredCount) {
            val t = plannedTimes[i]
            if (isCollege) {
                setPlannedBathTime(i + 1, true, t, date)
            } else {
                if (i == 0) {
                    val win = BathWindowRules.calculateBathWindow(1, false, date, t, timezone = tz)
                    setNonCollegeBathWindow(win.windowStartHhmm, win.windowEndHhmm, date)
                }
                setPlannedBathTime(i + 1, false, t, date)
            }
        }

        ensureBathOccurrence(date, 1)
        if (requiredCount >= 2) {
            ensureBathOccurrence(date, 2)
        } else {
            try {
                writableDatabase.execSQL(
                    "DELETE FROM scheduled_activities WHERE date = ? AND (activity_id LIKE '%BATH-2' OR details_json LIKE '%\"occurrence\":2%') AND status IN ('SCHEDULED','UPCOMING','NOTIFIED','OPEN') AND (submitted_at IS NULL OR submitted_at = '')",
                    arrayOf(date)
                )
            } catch (_: Exception) {}
        }
        reconcileHourlyBathConflicts(date)
        GymPolicyClient.queueCollegeChoice(this, date, cleanCollege)

        store.setTransactionSuccessful()
        requestBathAlarmRefresh()
        return JSONObject().apply {
            put("success", true)
            put("plan", planObj)
            put("message", "Bath schedule confirmed for $date")
        }
        } finally { store.endTransaction() }
    }

    fun setCollegeAttendance(date: String = nowDate(), answer: String, fromServer: Boolean = false) {
        StrictWidgetUpdates.request(appContext,"tasks")
        val clean = answer.uppercase(Locale.US).trim()
        if (clean !in listOf("YES", "NO")) return
        if (!fromServer) setConfigValue("college_plan_locally_edited_$date","true")
        setConfigValue("college_attendance_$date", clean)
        // Bath scheduling is independent! Do NOT automatically create or remove bath sessions merely because college answer changes.
        reconcileHourlyBathConflicts(date)
        if (!fromServer) GymPolicyClient.queueCollegeChoice(this,date,clean)
    }

    fun getMaxBathOccurrences(date: String = nowDate()): Int {
        val countCfg = getConfigValue("bath_session_count_$date", "")
        if (countCfg.isNotBlank()) {
            val c = countCfg.toIntOrNull()
            if (c != null && c in 1..2) return c
        }
        val plan = getDailyBathPlan(date)
        if (plan != null) {
            val arr = plan.optJSONArray("selected_bath_times")
            if (arr != null && arr.length() > 0) return arr.length()
        }
        return if (getCollegeAttendance(date) == "YES") 2 else 1
    }

    fun getDailyCollegeExamPlan(date: String = nowDate()): JSONObject? {
        try {
            readableDatabase.rawQuery(
                "SELECT * FROM daily_college_exam_plans WHERE date = ? LIMIT 1",
                arrayOf(date)
            ).use { c ->
                if (c.moveToFirst()) {
                    return JSONObject().apply {
                        put("date", c.getString(c.getColumnIndexOrThrow("date")))
                        put("account_id", c.getString(c.getColumnIndexOrThrow("account_id")))
                        put("timezone", c.getString(c.getColumnIndexOrThrow("timezone")))
                        put("attends_college", c.getString(c.getColumnIndexOrThrow("attends_college")))
                        put("college_start_time", c.getString(c.getColumnIndexOrThrow("college_start_time")))
                        put("college_end_time", c.getString(c.getColumnIndexOrThrow("college_end_time")))
                        put("has_exam", c.getString(c.getColumnIndexOrThrow("has_exam")))
                        put("exam_start_time", c.getString(c.getColumnIndexOrThrow("exam_start_time")))
                        put("exam_end_time", c.getString(c.getColumnIndexOrThrow("exam_end_time")))
                        put("post_exam_start_time", c.getString(c.getColumnIndexOrThrow("post_exam_start_time")))
                        put("post_exam_end_time", c.getString(c.getColumnIndexOrThrow("post_exam_end_time")))
                        put("revision", c.getInt(c.getColumnIndexOrThrow("revision")))
                        put("created_at", c.getString(c.getColumnIndexOrThrow("created_at")))
                        put("updated_at", c.getString(c.getColumnIndexOrThrow("updated_at")))
                    }
                }
            }
        } catch (_: Exception) {}
        val raw = getConfigValue("daily_college_exam_plan_$date", "")
        if (raw.isNotBlank()) {
            return runCatching { JSONObject(raw) }.getOrNull()
        }
        return null
    }

    fun saveDailyCollegeExamPlan(
        date: String = nowDate(),
        attendsCollege: String,
        collegeStart: String? = null,
        collegeEnd: String? = null,
        hasExam: String = "NO",
        examStart: String? = null,
        examEnd: String? = null
    ): JSONObject {
        val cleanAttends = attendsCollege.trim().uppercase(Locale.US)
        if (cleanAttends !in listOf("YES", "NO")) {
            return JSONObject().put("success", false).put("error", "College decision must be YES or NO.")
        }
        val isCollege = cleanAttends == "YES"
        var cleanCollegeStart: String? = null
        var cleanCollegeEnd: String? = null
        var cleanHasExam = "NO"
        var cleanExamStart: String? = null
        var cleanExamEnd: String? = null
        var postExamStart: String? = null
        var postExamEnd: String? = null

        if (isCollege) {
            val cVal = CollegeExamScheduleRules.validateCollegeTimes(collegeStart, collegeEnd)
            if (!cVal.isValid) {
                return JSONObject().put("success", false).put("error", cVal.errorMessage ?: "Invalid college times.")
            }
            cleanCollegeStart = collegeStart!!.trim()
            cleanCollegeEnd = collegeEnd!!.trim()

            cleanHasExam = hasExam.trim().uppercase(Locale.US)
            if (cleanHasExam == "YES") {
                val eVal = CollegeExamScheduleRules.validateExamTimes(cleanCollegeStart, cleanCollegeEnd, examStart, examEnd)
                if (!eVal.isValid) {
                    return JSONObject().put("success", false).put("error", eVal.errorMessage ?: "Invalid exam times.")
                }
                cleanExamStart = examStart!!.trim()
                cleanExamEnd = examEnd!!.trim()

                val intervalRes = CollegeExamScheduleRules.calculatePostExamClassInterval(cleanCollegeStart, cleanCollegeEnd, cleanExamStart, cleanExamEnd)
                if (intervalRes.hasInterval) {
                    postExamStart = intervalRes.startHhmm
                    postExamEnd = intervalRes.endHhmm
                }
            }
        }

        val existing = getDailyCollegeExamPlan(date)
        val curRev = existing?.optInt("revision", 0) ?: 0
        val newRev = curRev + 1
        val nowTs = nowIso()
        val createdTs = existing?.optString("created_at")?.takeIf { it.isNotBlank() } ?: nowTs
        val tz = scheduleTimezone()

        val cv = ContentValues().apply {
            put("date", date)
            put("account_id", "user_lok")
            put("timezone", tz)
            put("attends_college", cleanAttends)
            put("college_start_time", cleanCollegeStart ?: "")
            put("college_end_time", cleanCollegeEnd ?: "")
            put("has_exam", cleanHasExam)
            put("exam_start_time", cleanExamStart ?: "")
            put("exam_end_time", cleanExamEnd ?: "")
            put("post_exam_start_time", postExamStart ?: "")
            put("post_exam_end_time", postExamEnd ?: "")
            put("revision", newRev)
            put("created_at", createdTs)
            put("updated_at", nowTs)
        }
        writableDatabase.insertWithOnConflict("daily_college_exam_plans", null, cv, SQLiteDatabase.CONFLICT_REPLACE)

        val planObj = JSONObject().apply {
            put("date", date)
            put("account_id", "user_lok")
            put("timezone", tz)
            put("attends_college", cleanAttends)
            put("college_start_time", cleanCollegeStart ?: "")
            put("college_end_time", cleanCollegeEnd ?: "")
            put("has_exam", cleanHasExam)
            put("exam_start_time", cleanExamStart ?: "")
            put("exam_end_time", cleanExamEnd ?: "")
            put("post_exam_start_time", postExamStart ?: "")
            put("post_exam_end_time", postExamEnd ?: "")
            put("revision", newRev)
            put("created_at", createdTs)
            put("updated_at", nowTs)
        }
        setConfigValue("daily_college_exam_plan_$date", planObj.toString())
        setCollegeAttendance(date, cleanAttends)
        if (cleanCollegeStart != null) setConfigValue("college_start_$date", cleanCollegeStart)
        if (cleanCollegeEnd != null) setConfigValue("college_end_$date", cleanCollegeEnd)
        setConfigValue("has_exam_$date", cleanHasExam)
        if (cleanExamStart != null) setConfigValue("exam_start_$date", cleanExamStart)
        if (cleanExamEnd != null) setConfigValue("exam_end_$date", cleanExamEnd)
        if (postExamStart != null) setConfigValue("post_exam_start_$date", postExamStart)
        if (postExamEnd != null) setConfigValue("post_exam_end_$date", postExamEnd)

        reconcileHourlyPostExamClassSlots(date)

        return JSONObject().put("success", true).put("plan", planObj)
    }

    fun getPostExamClassInterval(date: String = nowDate()): Pair<String?, String?> {
        val plan = getDailyCollegeExamPlan(date)
        if (plan != null && plan.optString("attends_college") == "YES" && plan.optString("has_exam") == "YES") {
            val start = plan.optString("post_exam_start_time").takeIf { it.isNotBlank() }
            val end = plan.optString("post_exam_end_time").takeIf { it.isNotBlank() }
            if (start != null && end != null) return start to end
        }
        val s = getConfigValue("post_exam_start_$date", "").takeIf { it.isNotBlank() }
        val e = getConfigValue("post_exam_end_$date", "").takeIf { it.isNotBlank() }
        if (s != null && e != null) return s to e
        return null to null
    }

    fun isPostExamBonusEligible(date: String, actId: String): Boolean {
        val (peStart, peEnd) = getPostExamClassInterval(date)
        if (peStart == null || peEnd == null) return false

        try {
            readableDatabase.rawQuery(
                "SELECT scheduled_time, window_start, window_end, details_json FROM scheduled_activities WHERE activity_id = ? LIMIT 1",
                arrayOf(actId)
            ).use { c ->
                if (c.moveToFirst()) {
                    val schedIso = c.getString(0)
                    val wStartIso = c.getString(1)
                    val wEndIso = c.getString(2)
                    val schedMs = parseIsoToMs(schedIso)
                    val wStartMs = parseIsoToMs(wStartIso)
                    val wEndMs = parseIsoToMs(wEndIso)
                    val slotStartMs = schedMs - 3600_000L
                    val slotEndMs = schedMs

                    val zone = scheduleTimezone()
                    val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
                        timeZone = TimeZone.getTimeZone(zone)
                        isLenient = false
                    }
                    val peStartMs = sdf.parse("$date $peStart")?.time ?: return false
                    val peEndMs = sdf.parse("$date $peEnd")?.time ?: return false

                    val slotIntersects = maxOf(slotStartMs, peStartMs) < minOf(slotEndMs, peEndMs)
                    val windowIntersects = wStartMs < peEndMs && wEndMs > peStartMs
                    return slotIntersects && windowIntersects
                }
            }
        } catch (_: Exception) {}
        return false
    }

    fun reconcileHourlyPostExamClassSlots(date: String = nowDate()) {
        val (peStart, peEnd) = getPostExamClassInterval(date)
        val nowMs = authoritativeNowMs()
        val zone = scheduleTimezone()
        val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            timeZone = TimeZone.getTimeZone(zone)
            isLenient = false
        }
        val peStartMs = if (peStart != null) sdf.parse("$date $peStart")?.time ?: 0L else 0L
        val peEndMs = if (peEnd != null) sdf.parse("$date $peEnd")?.time ?: 0L else 0L
        val hasPostExam = peStartMs > 0L && peEndMs > peStartMs

        readableDatabase.rawQuery(
            "SELECT activity_id, status, scheduled_time, window_start, window_end, details_json FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'",
            arrayOf(date)
        ).use { c ->
            while (c.moveToNext()) {
                val actId = c.getString(0)
                val currentStatus = c.getString(1)
                val schedIso = c.getString(2)
                val wStartIso = c.getString(3)
                val wEndIso = c.getString(4)
                val detailsStr = c.getString(5)
                val details = runCatching { JSONObject(detailsStr ?: "{}") }.getOrDefault(JSONObject())

                val schedMs = parseIsoToMs(schedIso)
                val slotStartMs = schedMs - 3600_000L
                val slotEndMs = schedMs
                val wStartMs = parseIsoToMs(wStartIso)
                val wEndMs = parseIsoToMs(wEndIso)

                val isEligible = hasPostExam &&
                    (maxOf(slotStartMs, peStartMs) < minOf(slotEndMs, peEndMs)) &&
                    (wStartMs < peEndMs && wEndMs > peStartMs)

                if (isEligible) {
                    details.put("is_post_exam_class_time", true)
                    details.put("post_exam_bonus_eligible", true)
                    details.put("post_exam_interval", "$peStart–$peEnd")
                    details.put("optional_attendance", true)

                    val hasSubmission = currentStatus in listOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "APPROVED") ||
                        details.optString("submission_id").isNotBlank() || details.optString("evidence_id").isNotBlank()

                    // If deadline has passed and user has not submitted -> EXCUSED_NO_POINTS
                    if (!hasSubmission && nowMs >= wEndMs) {
                        val cv = ContentValues().apply {
                            put("status", "EXCUSED")
                            put("attendance_status", "EXCUSED_NO_POINTS")
                            put("points", 0)
                            details.put("attendance_status", "EXCUSED_NO_POINTS")
                            details.put("suppression_reason", "Excused — no submission required, no points awarded")
                            details.put("excused_reason", "Post-exam class time is optional; no points deducted.")
                            put("details_json", details.toString())
                        }
                        writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                    } else {
                        val cv = ContentValues().apply {
                            put("details_json", details.toString())
                        }
                        writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                    }
                } else if (details.optBoolean("is_post_exam_class_time", false) && !hasPostExam) {
                    details.remove("is_post_exam_class_time")
                    details.remove("post_exam_bonus_eligible")
                    details.remove("post_exam_interval")
                    details.remove("optional_attendance")
                    val cv = ContentValues().apply {
                        put("details_json", details.toString())
                    }
                    writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                }
            }
        }
    }

    fun getBathSubmissionCount(date: String = nowDate()): Int {
        var count = 0
        try {
            readableDatabase.rawQuery(
                "SELECT COUNT(DISTINCT activity_id) FROM scheduled_activities WHERE date = ? AND (type = 'BATH' OR activity_id LIKE '%BATH%') AND status IN ('SUBMITTED', 'VALIDATING', 'AI_PROVISIONAL', 'VERIFIED', 'COMPLETED')",
                arrayOf(date)
            ).use { c ->
                if (c.moveToFirst()) count = c.getInt(0)
            }
            readableDatabase.rawQuery(
                "SELECT payload_json FROM sync_events_queue WHERE event_type IN ('ACTIVITY_PROOF_SUBMITTED', 'HOURLY_PROOF_SUBMITTED') AND sync_status = 'PENDING'",
                null
            ).use { c ->
                val pendingBathActIds = mutableSetOf<String>()
                while (c.moveToNext()) {
                    val p = runCatching { JSONObject(c.getString(0)) }.getOrDefault(JSONObject())
                    if (p.optString("date") == date && (p.optString("task_type") == "BATH" || p.optString("activity_id").contains("BATH"))) {
                        val actId = p.optString("activity_id")
                        if (actId.isNotBlank()) pendingBathActIds.add(actId)
                        else count++
                    }
                }
                for (pId in pendingBathActIds) {
                    val alreadyCounted = readableDatabase.rawQuery(
                        "SELECT 1 FROM scheduled_activities WHERE activity_id = ? AND status IN ('SUBMITTED', 'VALIDATING', 'AI_PROVISIONAL', 'VERIFIED', 'COMPLETED')",
                        arrayOf(pId)
                    ).use { it.moveToFirst() }
                    if (!alreadyCounted) count++
                }
            }
        } catch (_: Throwable) {}
        return count
    }

    fun getNonCollegeBathWindowStart(): String =
        getConfigValue("non_college_bath_window_start", BathWindowRules.DEFAULT_NON_COLLEGE_WINDOW_START)

    fun getNonCollegeBathWindowEnd(): String =
        getConfigValue("non_college_bath_window_end", BathWindowRules.DEFAULT_NON_COLLEGE_WINDOW_END)

    fun setNonCollegeBathWindow(startHhmm: String, endHhmm: String, date: String = nowDate()): Boolean {
        val normStart = BathWindowRules.normalizeHhmm(startHhmm, BathWindowRules.DEFAULT_NON_COLLEGE_WINDOW_START)
        val normEnd = BathWindowRules.normalizeHhmm(endHhmm, BathWindowRules.DEFAULT_NON_COLLEGE_WINDOW_END)
        if (!BathWindowRules.isValidWindow(normStart, normEnd)) return false
        val store=writableDatabase
        store.beginTransaction()
        try {
        setConfigValue("non_college_bath_window_start", normStart)
        setConfigValue("non_college_bath_window_end", normEnd)
        setConfigValue("college_plan_locally_edited_$date","true")
        ensureBathOccurrence(date, 1)
        reconcileHourlyBathConflicts(date)
        val syncDate=date
        if (getCollegeAttendance(syncDate) in listOf("YES","NO")) GymPolicyClient.queueCollegeChoice(this,syncDate,getCollegeAttendance(syncDate))
        store.setTransactionSuccessful()
        } finally { store.endTransaction() }
        requestBathAlarmRefresh()
        return true
    }

    fun getPlannedBathTime(occurrence: Int, isCollege: Boolean, date: String = nowDate()): String {
        GymPolicyClient.collegePlan(this,date,occurrence,isCollege)?.optString("planned_time")?.takeIf { it.isNotBlank() }?.let { return it }
        val dailyPlan = getDailyBathPlan(date)
        if (dailyPlan != null && dailyPlan.optString("confirmation_status") == "CONFIRMED") {
            val planCollege = dailyPlan.optString("college_decision") == "YES"
            if (planCollege == isCollege) {
                val times = dailyPlan.optJSONArray("selected_bath_times")
                val time = times?.optString(occurrence - 1)
                if (!time.isNullOrBlank()) return time
            }
        }
        return if (isCollege) {
            if (occurrence == 1) {
                getConfigValue("bath_planned_time_1_$date",
                    getConfigValue("college_bath1_planned_time", BathWindowRules.DEFAULT_COLLEGE_BATH1_PLANNED))
            } else {
                getConfigValue("bath_planned_time_2_$date",
                    getConfigValue("college_bath2_planned_time", BathWindowRules.DEFAULT_COLLEGE_BATH2_PLANNED))
            }
        } else {
            if (occurrence == 1) {
                getConfigValue("bath_planned_time_non_college_$date",
                    getConfigValue("non_college_bath_planned_time", BathWindowRules.DEFAULT_NON_COLLEGE_PLANNED))
            } else {
                getConfigValue("bath_planned_time_non_college_2_$date",
                    getConfigValue("non_college_bath2_planned_time", "18:00"))
            }
        }
    }

    fun getBathActivityDurationMinutes(occurrence: Int, isCollege: Boolean, date: String = nowDate()): Int {
        val local=getConfigValue("bath_activity_duration_${occurrence}_$date","").toIntOrNull()
        if(local != null && local in 1..180) return local
        val server=GymPolicyClient.collegePlan(this,date,occurrence,isCollege)?.optInt("activity_duration_minutes",0) ?: 0
        if(server in 1..180) return server
        return if(isCollege && occurrence == 1) 30 else ProductPolicy.BATH_RESERVATION_MINUTES
    }

    fun setPlannedBathTime(occurrence: Int, isCollege: Boolean, plannedHhmm: String, date: String? = null,
                           durationMinutes: Int? = null): Boolean {
        if(occurrence !in 1..2 || (durationMinutes != null && durationMinutes !in 1..180)) return false
        val syncDate=date ?: nowDate()
        val normPlanned=BathWindowRules.normalizeHhmm(plannedHhmm)
        val duration=durationMinutes ?: getBathActivityDurationMinutes(occurrence,isCollege,syncDate)
        val (winStart,winEnd)=if(isCollege) {
            if(occurrence == 1) BathWindowRules.DEFAULT_COLLEGE_BATH1_WINDOW_START to BathWindowRules.DEFAULT_COLLEGE_BATH1_WINDOW_END
            else BathWindowRules.DEFAULT_COLLEGE_BATH2_WINDOW_START to BathWindowRules.DEFAULT_COLLEGE_BATH2_WINDOW_END
        } else if(occurrence == 1) "06:00" to "22:00" else "12:00" to "22:00"
        val plannedMinutes=BathWindowRules.timeToMinutes(normPlanned)
        if(plannedMinutes < BathWindowRules.timeToMinutes(winStart) ||
            plannedMinutes+duration > BathWindowRules.timeToMinutes(winEnd)) return false
        val store=writableDatabase
        store.beginTransaction()
        try {
            setConfigValue("college_plan_locally_edited_$syncDate","true")
            val key=if(isCollege) "bath_planned_time_${occurrence}_$syncDate"
                else if(occurrence == 1) "bath_planned_time_non_college_$syncDate" else "bath_planned_time_non_college_2_$syncDate"
            setConfigValue(key,normPlanned)
            if(date == null) {
                val defaultKey=if(isCollege) "college_bath${occurrence}_planned_time"
                    else if(occurrence == 1) "non_college_bath_planned_time" else "non_college_bath2_planned_time"
                setConfigValue(defaultKey,normPlanned)
            }
            setConfigValue("bath_activity_duration_${occurrence}_$syncDate",duration.toString())
            // A confirmed daily-plan cache must move with the edited occurrence.
            getDailyBathPlan(syncDate)?.let { plan ->
                val times=plan.optJSONArray("selected_bath_times") ?: JSONArray()
                if(occurrence <= times.length()) {
                    times.put(occurrence-1,normPlanned)
                    val intervals=JSONArray();val ids=JSONArray();val proof=JSONArray()
                    for(i in 0 until times.length()) {
                        val w=BathWindowRules.calculateBathWindow(i+1,isCollege,syncDate,times.getString(i),
                            timezone=scheduleTimezone(syncDate),durationMinutes=getBathActivityDurationMinutes(i+1,isCollege,syncDate))
                        intervals.put(w.activityIntervalDisplay);ids.put(w.heldHourlyActId);proof.put(w.proofWindowDisplay)
                    }
                    plan.put("selected_bath_times",times).put("activity_intervals",intervals)
                        .put("reserved_hourly_act_ids",ids).put("photo_submission_windows",proof)
                    setConfigValue("daily_bath_plan_$syncDate",plan.toString())
                    val values=ContentValues().apply {
                        put("selected_bath_times_json",times.toString());put("activity_intervals_json",intervals.toString())
                        put("reserved_hourly_act_ids_json",ids.toString());put("photo_submission_windows_json",proof.toString())
                        put("updated_at",nowIso())
                    }
                    store.update("daily_bath_plans",values,"date = ?",arrayOf(syncDate))
                }
            }
            ensureBathOccurrence(syncDate,occurrence)
            reconcileHourlyBathConflicts(syncDate)
            if(getCollegeAttendance(syncDate) in listOf("YES","NO"))
                GymPolicyClient.queueCollegeChoice(this,syncDate,getCollegeAttendance(syncDate))
            store.setTransactionSuccessful()
        } finally { store.endTransaction() }
        StrictWidgetUpdates.request(appContext,"tasks")
        requestBathAlarmRefresh()
        return true
    }

    private val bathAlarmWorker = java.util.concurrent.Executors.newSingleThreadExecutor { r ->
        Thread(r, "BathAlarmWorker").apply { isDaemon = true }
    }

    private fun requestBathAlarmRefresh() {
        // Run rebuild on background executor - NEVER post DB queries or alarm scheduling to the main looper!
        bathAlarmWorker.execute {
            runCatching { WakeUpAlarmReceiver.scheduleHourlyWindows(appContext, forceRebuild = true) }
        }
    }

    fun ensureBathOccurrence(date: String = nowDate(), occurrenceNum: Int = 1): String {
        val store=writableDatabase
        store.beginTransaction()
        try {
            val id=persistBathOccurrence(date,occurrenceNum)
            reconcileHourlyBathConflicts(date)
            store.setTransactionSuccessful()
            return id
        } finally { store.endTransaction() }
    }

    private fun persistBathOccurrence(date: String, occurrenceNum: Int): String {
            val isCollege = getCollegeAttendance(date) == "YES"
            val planned = getPlannedBathTime(occurrenceNum, isCollege, date)
            val confirmedPlan=GymPolicyClient.collegePlan(this,date,occurrenceNum,isCollege)
            val winStartOverride = confirmedPlan?.optString("window_start") ?: if (!isCollege) {
                if (occurrenceNum == 2) "12:00" else getNonCollegeBathWindowStart()
            } else null
            val winEndOverride = confirmedPlan?.optString("window_end") ?: if (!isCollege) {
                if (occurrenceNum == 2) "22:00" else getNonCollegeBathWindowEnd()
            } else null

            val bathWin = BathWindowRules.calculateBathWindow(
                occurrence = occurrenceNum,
                isCollege = isCollege,
                date = date,
                plannedHhmm = planned,
                windowStartOverride = winStartOverride,
                windowEndOverride = winEndOverride,
                timezone = scheduleTimezone(date),
                durationMinutes = getBathActivityDurationMinutes(occurrenceNum,isCollege,date)
            )

            // Check if there is an existing Bath activity for this date and specific occurrence
            var existingId: String? = null
            var existingStatus: String? = null
            var existingDetails: String? = null
            val existingQuery = "SELECT activity_id, status, details_json FROM scheduled_activities WHERE date = ? AND (type = 'BATH' OR activity_id LIKE '%BATH%') AND (activity_id LIKE ? OR details_json LIKE ?) ORDER BY CASE WHEN status IN ('VERIFIED','COMPLETED') THEN 1 WHEN status IN ('SUBMITTED','VALIDATING') THEN 2 WHEN status IN ('MISSED','REJECTED') THEN 3 ELSE 4 END ASC LIMIT 1"
            val existingArgs = arrayOf(date, "%BATH-$occurrenceNum%", "%\"occurrence\":$occurrenceNum%")
            readableDatabase.rawQuery(existingQuery, existingArgs).use { c ->
                if (c.moveToFirst()) {
                    existingId = c.getString(0)
                    existingStatus = c.getString(1)
                    existingDetails = c.getString(2)
                }
            }

            val detailsObj = runCatching { JSONObject(existingDetails ?: "{}") }.getOrDefault(JSONObject()).apply {
                put("occurrence", occurrenceNum)
                put("is_college", isCollege)
                put("college_status", if (isCollege) "YES" else "NO")
                put("planned_time", bathWin.plannedHhmm)
                put("planned_display", bathWin.plannedDisplay)
                put("target_time", bathWin.plannedHhmm)
                put("activity_interval", bathWin.activityIntervalDisplay)
                put("window_start", bathWin.proofWindowStartIso)
                put("window_end", bathWin.proofWindowEndIso)
                put("reserved_start", bathWin.reservedStartIso)
                put("reserved_end", bathWin.reservedEndIso)
                put("activity_duration_minutes",((bathWin.reservedEndMs-bathWin.reservedStartMs)/60_000L).toInt())
                put("reserved_display", bathWin.reservedDisplay)
                put("proof_window_start", bathWin.proofWindowStartIso)
                put("proof_window_end", bathWin.proofWindowEndIso)
                put("proof_window_display", bathWin.proofWindowDisplay)
                put("proof_deadline", bathWin.proofWindowEndIso)
                put("proof_deadline_display", bathWin.proofDeadlineDisplay)
                put("held_hourly_slot", bathWin.heldHourlySlot)
                put("held_hourly_slot_display", bathWin.heldHourlySlotDisplay)
                put("held_hourly_act_id", bathWin.heldHourlyActId)
                put("held_hourly_target_ms", bathWin.heldHourlyTargetMs)
                put("attendance_reservation_policy", BathWindowRules.ATTENDANCE_POLICY)
                put("bath_reservation_id", "bath-reservation:${existingId ?: bathWin.actId}:${bathWin.heldHourlyActId}")
                put("booking_range_start", bathWin.windowStartIso)
                put("booking_range_end", bathWin.windowEndIso)
                put("booking_range_display", bathWin.windowDisplay)
                put("window_display", bathWin.windowDisplay)
                put("hour_slot", "${bathWin.proofWindowStartHhmm}-${bathWin.proofWindowEndHhmm}")
            }
            val detailsJson = detailsObj.toString()
            val nowMs = authoritativeNowMs()
            val isExpired = nowMs >= bathWin.proofWindowEndMs
            val isOpen = nowMs in bathWin.proofWindowStartMs until bathWin.proofWindowEndMs

            if (existingId != null) {
                var hasSubmittedProof = existingStatus in setOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "APPROVED")
                if (!hasSubmittedProof) {
                    readableDatabase.rawQuery("SELECT submitted_at FROM scheduled_activities WHERE activity_id = ?", arrayOf(existingId)).use {
                        if (it.moveToFirst() && !it.getString(0).isNullOrBlank()) hasSubmittedProof = true
                    }
                }
                if (!hasSubmittedProof) {
                    val updateCv = ContentValues().apply {
                        put("title", bathWin.title)
                        put("scheduled_time", bathWin.plannedIso)
                        put("window_start", bathWin.reservedStartIso)
                        put("window_end", bathWin.reservedEndIso)
                        put("notification_time", bathWin.notificationIso)
                        put("status", if (nowMs >= bathWin.reservedEndMs) "COMPLETED" else "SCHEDULED")
                        put("points", 0)
                        put("configured_points", 0)
                        put("validation_rule", "EXEMPT_ACTIVITY")
                        put("details_json", detailsJson)
                    }
                    writableDatabase.update("scheduled_activities", updateCv, "activity_id = ?", arrayOf(existingId))
                }
                return existingId!!
            }

            val cv = ContentValues().apply {
                put("activity_id", bathWin.actId)
                put("account_id", "user_lok")
                put("type", "BATH")
                put("date", date)
                put("title", bathWin.title)
                put("scheduled_time", bathWin.plannedIso)
                put("window_start", bathWin.reservedStartIso)
                put("window_end", bathWin.reservedEndIso)
                put("notification_time", bathWin.notificationIso)
                put("status", if (nowMs >= bathWin.reservedEndMs) "COMPLETED" else "SCHEDULED")
                put("points", 0)
                put("configured_points", 0)
                put("validation_rule", "EXEMPT_ACTIVITY")
                put("details_json", detailsJson)
            }
            writableDatabase.insertWithOnConflict("scheduled_activities", null, cv, SQLiteDatabase.CONFLICT_IGNORE)
            return bathWin.actId
    }

    fun getSavedBathSchedule(activity: DisciplineScoringRules.ScheduledActivityItem): BathScheduleRecord =
        BathScheduleRecord.fromSaved(activity.date,activity.activityId,activity.scheduledTime,activity.windowStart,
            activity.windowEnd,activity.notificationTime,
            runCatching { JSONObject(activity.detailsJson) }.getOrDefault(JSONObject()),scheduleTimezone(activity.date),
            preserveSavedAssignment=activity.date < nowDate() || (activity.status in BathScheduleRecord.protectedStatuses && !activity.submittedAt.isNullOrBlank()))

    fun getBathScheduleForDisplay(date: String, occurrence: Int, isCollege: Boolean, planned: String): BathScheduleRecord {
        readableDatabase.rawQuery(
            "SELECT activity_id, scheduled_time, window_start, window_end, notification_time, details_json FROM scheduled_activities WHERE date = ? AND type = 'BATH' AND status NOT IN ('CANCELLED','SKIPPED') ORDER BY scheduled_time",
            arrayOf(date)
        ).use { cursor ->
            while (cursor.moveToNext()) {
                val details = runCatching { JSONObject(cursor.getString(5)) }.getOrDefault(JSONObject())
                val savedPlanned = details.optString("planned_time").ifBlank { cursor.getString(1).substringAfter('T').take(5) }
                if (details.optInt("occurrence",1) == occurrence && details.optBoolean("is_college",isCollege) == isCollege &&
                    BathWindowRules.normalizeHhmm(savedPlanned) == BathWindowRules.normalizeHhmm(planned)) {
                    return BathScheduleRecord.fromSaved(date,cursor.getString(0),cursor.getString(1),cursor.getString(2),
                        cursor.getString(3),cursor.getString(4),details,scheduleTimezone(date))
                }
            }
        }
        return BathScheduleRecord.fromWindow(BathWindowRules.calculateBathWindow(occurrence,isCollege,date,planned,
            windowStartOverride=if(isCollege) null else getNonCollegeBathWindowStart(),
            windowEndOverride=if(isCollege) null else getNonCollegeBathWindowEnd(),timezone=scheduleTimezone(date)),scheduleTimezone(date))
    }

    fun reconcileHourlyBathConflicts(date: String = nowDate()) {
        // Past days and finalized evidence/ledger entries are never migrated here.
        if (date < nowDate()) return
        val store = writableDatabase
        val nowMs = authoritativeNowMs()
        store.beginTransaction()
        try {
            val reservations = mutableListOf<BathScheduleRecord>()
            store.rawQuery(
                "SELECT activity_id, scheduled_time, details_json, window_start, window_end, notification_time, status, submitted_at, points FROM scheduled_activities WHERE date = ? AND (type = 'BATH' OR activity_id LIKE '%BATH%') AND status NOT IN ('CANCELLED', 'SKIPPED') ORDER BY scheduled_time, activity_id",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    val details = runCatching { JSONObject(c.getString(2) ?: "{}") }.getOrDefault(JSONObject())
                    val saved = runCatching { BathScheduleRecord.fromSaved(date,c.getString(0),c.getString(1),c.getString(3),
                        c.getString(4),c.getString(5),details,scheduleTimezone(date)) }.getOrNull() ?: continue
                    if (!saved.assignmentValid) continue
                    reservations.add(saved)
                    if (BathScheduleRecord.canExcuseHourly(c.getString(6),c.getString(7),c.getInt(8)) && nowMs < saved.proofWindowEndMs) {
                        val normalized = saved.assignmentDetails(details)
                        if (normalized.toString() != details.toString()) {
                            val cv = ContentValues().apply { put("details_json", normalized.toString()) }
                            store.update("scheduled_activities",cv,"activity_id = ?",arrayOf(saved.activityId))
                        }
                    }
                }
            }
            // A source event can exist even if a mutable activity row was later corrupted.
            val scoredIds = mutableSetOf<String>()
            store.rawQuery("SELECT audit_json FROM score_events WHERE date = ?",arrayOf(date)).use { c ->
                while(c.moveToNext()) {
                    runCatching { JSONObject(c.getString(0)).optString("activity_id") }.getOrNull()
                        ?.takeIf { it.isNotBlank() }?.let { scoredIds.add(it) }
                }
            }
            store.rawQuery(
                "SELECT activity_id, status, window_start, window_end, details_json, scheduled_time, submitted_at, points FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    val actId = c.getString(0)
                    val currentStatus = c.getString(1)
                    val details = runCatching { JSONObject(c.getString(4) ?: "{}") }.getOrDefault(JSONObject())
                    if (!BathScheduleRecord.canExcuseHourly(currentStatus,c.getString(6),c.getInt(7)) || actId in scoredIds ||
                        details.optString("submission_id").isNotBlank() || details.optString("evidence_id").isNotBlank()) continue
                    val hStartMs = parseIsoToMs(c.getString(2))
                    val hEndMs = parseIsoToMs(c.getString(3))
                    if (hStartMs <= 0L || hEndMs <= hStartMs) continue
                    val conflicts = reservations.filter { it.overlapWithHourly(hStartMs,hEndMs) != "NONE" }
                    val reservation = conflicts.firstOrNull()
                    val wasSuppressedByBath = details.optBoolean("suppressed_by_bath", false) ||
                        details.optString("suppression_reason").contains("bath", ignoreCase = true) ||
                        details.optString("bath_activity_id").isNotBlank()
                    if (reservation != null) {
                        // Existing non-bath exemptions/holds keep their own reconciliation owner.
                        if (currentStatus == "EXCUSED" && !wasSuppressedByBath) continue
                        if (!wasSuppressedByBath) {
                            details.put("bath_previous_status",currentStatus)
                            details.put("bath_previous_suppression_reason",details.optString("suppression_reason"))
                        }
                        details.put("suppressed_by_bath",true)
                        details.put("suppression_reason","This proof window overlaps your scheduled Bath activity. No submission required.")
                        details.put("reconciliation_status","EXCUSED_BY_BATH")
                        details.put("attendance_status","EXCUSED_BY_BATH")
                        details.put("bath_activity_id",reservation.activityId)
                        details.put("bath_reservation_id",reservation.reservationId)
                        details.put("bath_hourly_activity_id",actId)
                        details.put("bath_slot_target_ms",parseIsoToMs(c.getString(5)))
                        details.put("bath_slot_display",reservation.heldHourlySlotDisplay)
                        details.put("bath_overlap",reservation.overlapWithHourly(hStartMs,hEndMs))
                        details.put("bath_activity_ids",JSONArray(conflicts.map { it.activityId }))
                        details.put("bath_activity_interval",reservation.activityIntervalDisplay)
                        details.put("attendance_reservation_policy",BathWindowRules.ATTENDANCE_POLICY)
                        details.put("bath_partial_overlap_policy",BathWindowRules.PARTIAL_OVERLAP_POLICY)
                        val cv = ContentValues().apply {
                            put("status","EXCUSED"); put("attendance_status","EXCUSED_BY_BATH"); put("points",0); put("details_json",details.toString())
                        }
                        store.update("scheduled_activities",cv,"activity_id = ?",arrayOf(actId))
                    } else if (wasSuppressedByBath && nowMs < hEndMs) {
                        val priorReason=details.optString("bath_previous_suppression_reason")
                        val otherExcuse=listOf("suppressed_by_gym","suppressed_by_travel","suppressed_by_movie","suppressed_by_official_event","suppressed_by_exam")
                            .any { details.optBoolean(it,false) }
                        listOf("suppressed_by_bath","bath_activity_id","bath_reservation_id","bath_hourly_activity_id",
                            "bath_slot_target_ms","bath_slot_display","bath_overlap","bath_activity_ids","bath_activity_interval",
                            "attendance_reservation_policy","bath_partial_overlap_policy","bath_previous_status",
                            "bath_previous_suppression_reason","reconciliation_status","attendance_status").forEach { details.remove(it) }
                        if (priorReason.isNotBlank()) details.put("suppression_reason",priorReason) else details.remove("suppression_reason")
                        if (details.optString("suppression_reason").contains("bath", ignoreCase = true)) details.remove("suppression_reason")
                        val prevStatus = details.optString("bath_previous_status").takeIf { it.isNotBlank() && it != "EXCUSED" }
                        val restoredStatus=if(otherExcuse) "EXCUSED" else if(nowMs >= hStartMs) "OPEN" else prevStatus ?: "SCHEDULED"
                        val cv=ContentValues().apply {
                            put("status",restoredStatus)
                            put("attendance_status",if(restoredStatus=="OPEN") "OPEN" else "UPCOMING")
                            put("points",0)
                            put("configured_points",8)
                            put("details_json",details.toString())
                        }
                        store.update("scheduled_activities",cv,"activity_id = ?",arrayOf(actId))
                    }
                }
            }
            store.setTransactionSuccessful()
        } finally { store.endTransaction() }
    }

    fun cancelBathActivity(activityId: String): Boolean {
        val store = writableDatabase
        store.beginTransaction()
        try {
            val cv = ContentValues().apply {
                put("status", "CANCELLED")
                put("is_enabled", 0)
            }
            val count = store.update("scheduled_activities", cv, "activity_id = ? AND (status IN ('SCHEDULED','UPCOMING','NOTIFIED','OPEN') OR status IS NULL)", arrayOf(activityId))
            val date = readableDatabase.rawQuery("SELECT date FROM scheduled_activities WHERE activity_id = ?", arrayOf(activityId)).use {
                if (it.moveToFirst()) it.getString(0) else nowDate()
            }
            reconcileHourlyBathConflicts(date)
            store.setTransactionSuccessful()
            return count > 0
        } finally {
            store.endTransaction()
        }
    }

    private val isReconcilingGym = java.util.concurrent.atomic.AtomicBoolean(false)

    fun reconcileHourlyGymConflicts(date: String = nowDate()) {
        if (!isReconcilingGym.compareAndSet(false, true)) return
        try {
            val nowMs = authoritativeNowMs()

            var gymActId: String? = null
            var gymStatus: String? = null
            var gymPlannedMs = 0L
            var gymExpectedFinishMs = 0L
            var gymProofWindowEndMs = 0L
            var gymDetails = JSONObject()
            var hasGymOccurrence = false

            readableDatabase.rawQuery(
                "SELECT activity_id, status, scheduled_time, window_start, window_end, details_json FROM scheduled_activities WHERE date = ? AND type = 'GYM' AND status NOT IN ('CANCELLED', 'SKIPPED') LIMIT 1",
                arrayOf(date)
            ).use { c ->
                if (c.moveToFirst()) {
                    gymActId = c.getString(0)
                    gymStatus = c.getString(1)?.uppercase(Locale.US)
                    gymPlannedMs = parseIsoToMs(c.getString(2))
                    gymProofWindowEndMs = parseIsoToMs(c.getString(4))
                    gymDetails = runCatching { JSONObject(c.getString(5) ?: "{}") }.getOrDefault(JSONObject())
                    val durMinutes = gymDetails.optInt("duration_minutes", GymWindowRules.DEFAULT_GYM_DURATION_MINUTES)
                    gymExpectedFinishMs = gymDetails.optLong("expected_finish_ms", 0L).takeIf { it > 0L }
                        ?: (gymPlannedMs + durMinutes * 60_000L)
                    hasGymOccurrence = true
                }
            }

            // If no gym occurrence or planned time is invalid, restore any hourly slots suppressed by gym.
            if (!hasGymOccurrence || gymPlannedMs <= 0L) {
                readableDatabase.rawQuery(
                    "SELECT activity_id, status, window_start, window_end, details_json FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'",
                    arrayOf(date)
                ).use { c ->
                    while (c.moveToNext()) {
                        val actId = c.getString(0)
                        val status = c.getString(1)
                        val wStart = c.getString(2)
                        val wEnd = c.getString(3)
                        val details = runCatching { JSONObject(c.getString(4) ?: "{}") }.getOrDefault(JSONObject())
                        if (details.optBoolean("suppressed_by_gym", false) || details.optBoolean("gym_hold", false) || (status == "EXCUSED" && details.optBoolean("gym_excused", false))) {
                            details.remove("suppressed_by_gym")
                            details.remove("gym_hold")
                            details.remove("gym_occurrence_id")
                            details.remove("gym_booking_id")
                            details.remove("hold_reason")
                            details.remove("suppression_reason")
                            details.remove("gym_excused")
                            details.remove("reconciliation_status")
                            details.remove("reconciled_by_gym")
                            val wStartMs = parseIsoToMs(wStart)
                            val wEndMs = parseIsoToMs(wEnd)
                            val restoredStatus = if (nowMs >= wEndMs) "MISSED" else if (nowMs >= wStartMs) "OPEN" else "SCHEDULED"
                            val cv = ContentValues().apply {
                                put("status", restoredStatus)
                                put("details_json", details.toString())
                            }
                            writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                        }
                    }
                }
                return
            }

            val hasSubmittedProof = gymStatus in listOf(
                "SUBMITTED", "VALIDATING", "PENDING_SYNC", "AI_PROVISIONAL",
                "PENDING_REVIEW", "VERIFIED", "APPROVED", "COMPLETED"
            ) || gymDetails.optBoolean("gym_proof_submitted", false)

            val isGymMissedOrExpired = gymStatus in listOf("MISSED", "EXPIRED", "REJECTED", "REJECTED_LATE") ||
                (nowMs >= gymProofWindowEndMs && !hasSubmittedProof)

            if (isGymMissedOrExpired && gymStatus !in listOf("MISSED", "EXPIRED", "REJECTED", "REJECTED_LATE")) {
                writableDatabase.execSQL(
                    "UPDATE scheduled_activities SET status = 'MISSED', points = 0 WHERE activity_id = ?",
                    arrayOf(gymActId)
                )
                gymStatus = "MISSED"
            }

            if (isGymMissedOrExpired && gymActId != null) {
                try { deductGymModeLife(gymActId!!, "Gym proof deadline expired without acceptable evidence", nowMs) } catch (_: Throwable) {}
            }

            val isGymApproved = gymStatus in listOf("VERIFIED", "COMPLETED", "APPROVED")
            if (isGymApproved && gymActId != null) {
                try { fulfillGymModeObligation(gymActId!!, nowMs) } catch (_: Throwable) {}
            }

            if (!isGymApproved) {
                // Rule 1 & Rule 4: Booking/planning alone, pending proof, expired proof, or rejected proof
                // NEVER excuses hourly attendance. If any hourly slots were previously marked EXCUSED
                // by gym hold/excusal, revert them to their natural status.
                readableDatabase.rawQuery(
                    "SELECT activity_id, status, window_start, window_end, details_json FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'",
                    arrayOf(date)
                ).use { c ->
                    while (c.moveToNext()) {
                        val actId = c.getString(0)
                        val status = c.getString(1)
                        val wStart = c.getString(2)
                        val wEnd = c.getString(3)
                        val details = runCatching { JSONObject(c.getString(4) ?: "{}") }.getOrDefault(JSONObject())

                        val hadGymExemption = details.optBoolean("suppressed_by_gym", false) ||
                            details.optBoolean("gym_hold", false) ||
                            details.optBoolean("gym_excused", false) ||
                            details.optString("reconciliation_status") == "EXCUSED_BY_GYM"

                        if (hadGymExemption && status in listOf("EXCUSED", "SCHEDULED", "OPEN")) {
                            details.remove("suppressed_by_gym")
                            details.remove("gym_hold")
                            details.remove("gym_occurrence_id")
                            details.remove("gym_booking_id")
                            details.remove("hold_reason")
                            details.remove("suppression_reason")
                            details.remove("gym_excused")
                            details.remove("reconciliation_status")
                            details.remove("reconciled_by_gym")

                            val wStartMs = parseIsoToMs(wStart)
                            val wEndMs = parseIsoToMs(wEnd)
                            val restoredStatus = if (nowMs >= wEndMs) "MISSED" else if (nowMs >= wStartMs) "OPEN" else "SCHEDULED"
                            val cv = ContentValues().apply {
                                put("status", restoredStatus)
                                put("details_json", details.toString())
                            }
                            writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                        }
                    }
                }
                return
            }

            // Gym proof is APPROVED! Reconcile overlapping hourly attendance occurrences.
            val durMinutes = gymDetails.optInt("duration_minutes", GymWindowRules.DEFAULT_GYM_DURATION_MINUTES)
            val verifiedGymStartMs = gymDetails.optLong("verified_gym_start_ms", 0L).takeIf { it > 0L }
                ?: gymPlannedMs
            val confirmedDurMinutes = gymDetails.optInt("confirmed_duration_minutes", 0).takeIf { it > 0 }
                ?: durMinutes
            val actualGymEndMs = gymDetails.optLong("actual_gym_end_ms", 0L).takeIf { it > 0L }
                ?: (verifiedGymStartMs + confirmedDurMinutes * 60_000L)

            val reconciledHourlySlots = mutableListOf<String>()

            readableDatabase.rawQuery(
                "SELECT activity_id, status, scheduled_time, window_start, window_end, details_json FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    val actId = c.getString(0)
                    val currentStatus = c.getString(1)
                    val schedIso = c.getString(2)
                    val wStartIso = c.getString(3)
                    val wEndIso = c.getString(4)
                    val detailsStr = c.getString(5)
                    val details = runCatching { JSONObject(detailsStr ?: "{}") }.getOrDefault(JSONObject())

                    val slotTargetMs = parseIsoToMs(schedIso)
                    if (slotTargetMs == Long.MIN_VALUE) continue
                    val slotStartMs = slotTargetMs - 3600_000L
                    val slotEndMs = slotTargetMs
                    val hWindowStartMs = parseIsoToMs(wStartIso)
                    val hWindowEndMs = parseIsoToMs(wEndIso)

                    // Overlap condition: [slotStartMs, slotEndMs) overlaps [verifiedGymStartMs, actualGymEndMs)
                    val isOverlapping = maxOf(slotStartMs, verifiedGymStartMs) < minOf(slotEndMs, actualGymEndMs)

                    if (isOverlapping) {
                        reconciledHourlySlots.add(actId)

                        val hasUserSubmission = currentStatus in listOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED") ||
                            details.optString("submission_id").isNotBlank() ||
                            details.optString("evidence_id").isNotBlank()

                        if (hasUserSubmission) {
                            // Rule 3.2: User already submitted hourly proof: preserve submission and review history without deletion/overwrite
                            details.put("reconciliation_status", "PRESERVED_USER_SUBMISSION")
                            details.put("reconciled_by_gym", gymActId)
                            details.put("gym_occurrence_id", gymActId)
                            val cv = ContentValues().apply {
                                put("details_json", details.toString())
                            }
                            writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                        } else if (currentStatus == "MISSED") {
                            // Rule 3.3: Occurrences already marked MISSED and penalized with a point deduction:
                            // Reverse penalty deduction with an auditable transaction (SCORE_CORRECTION)
                            var penaltyDeduction = 0
                            readableDatabase.rawQuery(
                                "SELECT points FROM score_events WHERE date = ? AND points < 0 AND event_type = 'HOURLY_MISSED' AND (audit_json LIKE ? OR description LIKE ?)",
                                arrayOf(date, "%$actId%", "%$actId%")
                            ).use { sc ->
                                while (sc.moveToNext()) {
                                    penaltyDeduction += kotlin.math.abs(sc.getInt(0))
                                }
                            }
                            // Only committed ledger deductions can be reversed.
                            // A schedule's proposed penalty is not a transaction.

                            val revTxId = "tx_rev_gym_${actId}"
                            if (penaltyDeduction > 0 && !hasScoreEvent(revTxId)) {
                                val revAudit = JSONObject().apply {
                                    put("action", "GYM_RECONCILIATION_REVERSAL")
                                    put("reconciled_activity_id", actId)
                                    put("gym_occurrence_id", gymActId)
                                    put("reversed_deduction", penaltyDeduction)
                                    put("transaction_id", revTxId)
                                    put("idempotency_key", revTxId)
                                    put("reason", "Reversal of missed hourly check penalty during verified Gym workout")
                                }
                                addScoreEvent(
                                    date = date,
                                    isDevMode = false,
                                    eventType = "SCORE_CORRECTION",
                                    description = "Reversal of penalty for missed hourly check ($actId) excused by Gym",
                                    points = penaltyDeduction,
                                    audit = revAudit
                                )
                            }

                            details.put("status", "EXCUSED")
                            details.put("suppressed_by_gym", true)
                            details.put("gym_excused", true)
                            details.put("reconciliation_status", "EXCUSED_BY_GYM")
                            details.put("suppression_reason", "Excused by gym workout verification")
                            details.put("gym_occurrence_id", gymActId)
                            if (penaltyDeduction > 0) details.put("penalty_reversed", penaltyDeduction)
                            val cv = ContentValues().apply {
                                put("status", "EXCUSED")
                                put("points", 0)
                                put("details_json", details.toString())
                            }
                            writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                        } else {
                            // Rule 3.1: Upcoming or open occurrences: mark EXCUSED (EXCUSED_BY_GYM)
                            val reason = "Excused by gym workout verification"
                            val changed = currentStatus != "EXCUSED" || details.optString("reconciliation_status") != "EXCUSED_BY_GYM"
                            if (changed) {
                                details.put("suppressed_by_gym", true)
                                details.put("gym_excused", true)
                                details.put("reconciliation_status", "EXCUSED_BY_GYM")
                                details.put("suppression_reason", reason)
                                details.put("gym_occurrence_id", gymActId)
                                val cv = ContentValues().apply {
                                    put("status", "EXCUSED")
                                    put("points", 0)
                                    put("details_json", details.toString())
                                }
                                writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                            }
                        }
                    } else {
                        // Rule 3.4: Occurrences outside verified interval: leave unchanged, restore if previously erroneously excused
                        if (details.optBoolean("suppressed_by_gym", false) || details.optString("reconciliation_status") == "EXCUSED_BY_GYM") {
                            details.remove("suppressed_by_gym")
                            details.remove("gym_hold")
                            details.remove("gym_occurrence_id")
                            details.remove("gym_booking_id")
                            details.remove("hold_reason")
                            details.remove("suppression_reason")
                            details.remove("gym_excused")
                            details.remove("reconciliation_status")
                            details.remove("reconciled_by_gym")
                            val isExcusedByOther = details.optBoolean("suppressed_by_exam", false) || details.optBoolean("suppressed_by_bath", false) || details.optBoolean("suppressed_by_travel", false) || details.optBoolean("suppressed_by_movie", false)
                            val restoredStatus = if (isExcusedByOther) "EXCUSED" else if (nowMs >= hWindowEndMs) "MISSED" else if (nowMs >= hWindowStartMs) "OPEN" else "SCHEDULED"
                            val cv = ContentValues().apply {
                                put("status", restoredStatus)
                                put("details_json", details.toString())
                            }
                            writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                        }
                    }
                }
            }

            // Persist reconciled_hourly_occurrences and calculated interval in gymDetails
            if (gymActId != null) {
                gymDetails.put("reconciled_hourly_occurrences", JSONArray(reconciledHourlySlots))
                gymDetails.put("verified_gym_start_ms", verifiedGymStartMs)
                gymDetails.put("actual_gym_end_ms", actualGymEndMs)
                gymDetails.put("confirmed_duration_minutes", confirmedDurMinutes)
                val cv = ContentValues().apply {
                    put("details_json", gymDetails.toString())
                }
                writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(gymActId))
            }
        } catch (_: Throwable) {
        } finally {
            isReconcilingGym.set(false)
        }
    }


    // =========================================================================
    // EXAM PREBOOKING, TIME ALLOCATION & ATTENDANCE EXEMPTIONS
    // =========================================================================

    fun insertExamPrebooking(booking: JSONObject): Boolean {
        return try {
            val cv = ContentValues().apply {
                put("booking_id", booking.getString("booking_id"))
                put("account_id", booking.optString("account_id", "user_lok"))
                put("exam_name", booking.getString("exam_name"))
                put("subject", booking.getString("subject"))
                put("institution", booking.getString("institution"))
                put("exam_date", booking.getString("exam_date"))
                put("official_start_time", booking.getString("official_start_time"))
                put("official_end_time", booking.getString("official_end_time"))
                put("exam_type", booking.optString("exam_type", "SEMESTER"))
                put("prep_buffer_minutes", booking.optInt("prep_buffer_minutes", 30))
                put("completion_buffer_minutes", booking.optInt("completion_buffer_minutes", 30))
                put("allocation_start_iso", booking.getString("allocation_start_iso"))
                put("allocation_end_iso", booking.getString("allocation_end_iso"))
                put("allocation_start_ms", booking.getLong("allocation_start_ms"))
                put("allocation_end_ms", booking.getLong("allocation_end_ms"))
                put("status", booking.optString("status", "EXAM_APPROVED"))
                put("validation_provider", booking.optString("validation_provider", "AI_VALIDATOR"))
                put("validation_explanation", booking.optString("validation_explanation", ""))
                put("cross_image_consistency", booking.optString("cross_image_consistency", "CONSISTENT"))
                put("supporting_images_count", booking.optInt("supporting_images_count", 1))
                put("evidence_files_json", booking.optString("evidence_files_json", "[]"))
                put("extracted_data_json", booking.optString("extracted_data_json", "{}"))
                put("excused_hourly_slots_json", booking.optString("excused_hourly_slots_json", "[]"))
                put("additional_info", booking.optString("additional_info", ""))
                put("actual_end_time", booking.optString("actual_end_time", ""))
                put("actual_end_iso", booking.optString("actual_end_iso", ""))
                put("actual_end_ms", booking.optLong("actual_end_ms", 0L))
                put("early_exit_confirmed", booking.optInt("early_exit_confirmed", 0))
                put("early_exit_confirmed_at", booking.optString("early_exit_confirmed_at", ""))
                put("monitoring_state", booking.optString("monitoring_state", "EXAM_BOOKING_PENDING"))
                put("neo_process_detected", booking.optInt("neo_process_detected", 0))
                put("neo_process_pid", booking.optInt("neo_process_pid", 0))
                put("neo_exe_path", booking.optString("neo_exe_path", ""))
                put("version", booking.optInt("version", 1))
                put("device_id", booking.optString("device_id", "android_phone"))
                put("created_at", booking.optString("created_at", nowIso()))
                put("updated_at", booking.optString("updated_at", nowIso()))
                if (booking.has("batch_id")) put("batch_id", booking.optString("batch_id", ""))
                if (booking.has("test_index")) put("test_index", booking.optInt("test_index", 1))
                if (booking.has("total_tests")) put("total_tests", booking.optInt("total_tests", 1))
            }
            writableDatabase.insertWithOnConflict("exam_prebookings", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
            val d = booking.getString("exam_date")
            reconcileHourlyExamConflicts(d)
            enqueueSyncEvent("EXAM_PREBOOKING_CONFIRMED", booking, appContext)
            true
        } catch (e: Exception) {
            false
        }
    }

    fun getExamPrebooking(bookingId: String): JSONObject? {
        try {
            readableDatabase.rawQuery("SELECT * FROM exam_prebookings WHERE booking_id = ?", arrayOf(bookingId)).use { c ->
                if (c.moveToFirst()) {
                    val obj = JSONObject()
                    for (i in 0 until c.columnCount) {
                        val col = c.getColumnName(i)
                        when (col) {
                            "prep_buffer_minutes", "completion_buffer_minutes", "supporting_images_count", "version" -> obj.put(col, c.getInt(i))
                            "allocation_start_ms", "allocation_end_ms" -> obj.put(col, c.getLong(i))
                            "evidence_files_json", "extracted_data_json", "excused_hourly_slots_json" -> {
                                val raw = c.getString(i) ?: ""
                                try {
                                    if (raw.startsWith("[")) obj.put(col, JSONArray(raw))
                                    else if (raw.startsWith("{")) obj.put(col, JSONObject(raw))
                                    else obj.put(col, raw)
                                } catch (_: Exception) { obj.put(col, raw) }
                            }
                            else -> obj.put(col, c.getString(i) ?: "")
                        }
                    }
                    return obj
                }
            }
        } catch (_: Exception) {}
        return null
    }

    fun listExamPrebookings(date: String? = null): JSONArray {
        val arr = JSONArray()
        try {
            val query = if (date != null) "SELECT * FROM exam_prebookings WHERE exam_date = ? ORDER BY allocation_start_ms ASC"
                        else "SELECT * FROM exam_prebookings ORDER BY exam_date ASC, allocation_start_ms ASC"
            val args = if (date != null) arrayOf(date) else null
            readableDatabase.rawQuery(query, args).use { c ->
                while (c.moveToNext()) {
                    val obj = JSONObject()
                    for (i in 0 until c.columnCount) {
                        val col = c.getColumnName(i)
                        when (col) {
                            "prep_buffer_minutes", "completion_buffer_minutes", "supporting_images_count", "version" -> obj.put(col, c.getInt(i))
                            "allocation_start_ms", "allocation_end_ms" -> obj.put(col, c.getLong(i))
                            else -> obj.put(col, c.getString(i) ?: "")
                        }
                    }
                    arr.put(obj)
                }
            }
        } catch (_: Exception) {}
        return arr
    }

    fun cancelExamPrebooking(bookingId: String, reason: String = "Cancelled by user"): Boolean {
        return try {
            var date = ""
            readableDatabase.rawQuery("SELECT exam_date FROM exam_prebookings WHERE booking_id = ?", arrayOf(bookingId)).use { c ->
                if (c.moveToFirst()) date = c.getString(0)
            }
            writableDatabase.execSQL("UPDATE exam_prebookings SET status = 'EXAM_CANCELLED', updated_at = ? WHERE booking_id = ?", arrayOf(nowIso(), bookingId))
            if (date.isNotBlank()) {
                reconcileHourlyExamConflicts(date)
            }
            enqueueSyncEvent("EXAM_PREBOOKING_CANCELLED", JSONObject().apply {
                put("booking_id", bookingId)
                put("reason", reason)
                put("timestamp", nowIso())
            }, appContext)
            true
        } catch (e: Exception) {
            false
        }
    }


    // =========================================================================
    // EXAM BATCHES (1 to 4 tests)
    // =========================================================================

    fun insertExamBatch(batchId: String, totalTests: Int, accountId: String = "user_lok"): Boolean {
        return try {
            val cv = ContentValues().apply {
                put("batch_id", batchId)
                put("account_id", accountId)
                put("total_tests", totalTests)
                put("status", "ACTIVE")
                put("created_at", nowIso())
                put("updated_at", nowIso())
            }
            writableDatabase.insertWithOnConflict("exam_batches", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
            true
        } catch (_: Exception) { false }
    }

    fun listExamBatches(): JSONArray {
        val arr = JSONArray()
        try {
            readableDatabase.rawQuery("SELECT * FROM exam_batches ORDER BY created_at DESC", null).use { c ->
                while (c.moveToNext()) {
                    val b = JSONObject().apply {
                        put("batch_id", c.getString(0))
                        put("account_id", c.getString(1))
                        put("total_tests", c.getInt(2))
                        put("status", c.getString(3))
                        put("created_at", c.getString(4))
                        put("updated_at", c.getString(5))
                    }
                    val testsArr = JSONArray()
                    readableDatabase.rawQuery(
                        "SELECT * FROM exam_prebookings WHERE batch_id = ? ORDER BY test_index ASC",
                        arrayOf(b.getString("batch_id"))
                    ).use { tc ->
                        while (tc.moveToNext()) {
                            val tobj = JSONObject()
                            for (i in 0 until tc.columnCount) {
                                val col = tc.getColumnName(i)
                                when (col) {
                                    "prep_buffer_minutes", "completion_buffer_minutes", "supporting_images_count", "test_index", "total_tests" -> tobj.put(col, tc.getInt(i))
                                    "allocation_start_ms", "allocation_end_ms" -> tobj.put(col, tc.getLong(i))
                                    else -> tobj.put(col, tc.getString(i) ?: "")
                                }
                            }
                            testsArr.put(tobj)
                        }
                    }
                    b.put("tests", testsArr)
                    arr.put(b)
                }
            }
        } catch (_: Exception) {}
        return arr
    }

    // =========================================================================
    // DAILY PRACTICE TEST SCHEDULER (2 to 4 tests per day)
    // =========================================================================

    fun getDailyPracticeTemplate(): JSONObject {
        try {
            readableDatabase.rawQuery("SELECT * FROM daily_practice_test_templates WHERE template_id = 'DEFAULT'", null).use { c ->
                if (c.moveToFirst()) {
                    val rawJson = c.getString(2) ?: "[]"
                    return JSONObject().apply {
                        put("template_id", "DEFAULT")
                        put("template_name", c.getString(1))
                        put("tests", JSONArray(rawJson))
                        put("updated_at", c.getString(3))
                    }
                }
            }
        } catch (_: Exception) {}

        // Default: 2 mandatory tests (Quant & Comm 30m)
        val defTests = JSONArray().apply {
            put(JSONObject().apply {
                put("test_index", 1)
                put("category", "Quantitative Aptitude")
                put("title", "Quantitative Aptitude")
                put("start_time", "10:30")
                put("duration_minutes", 30)
                put("end_time", "11:00")
                put("is_mandatory", true)
            })
            put(JSONObject().apply {
                put("test_index", 2)
                put("category", "Communication")
                put("title", "Communication")
                put("start_time", "11:30")
                put("duration_minutes", 30)
                put("end_time", "12:00")
                put("is_mandatory", true)
            })
        }
        val cv = ContentValues().apply {
            put("template_id", "DEFAULT")
            put("template_name", "Default Daily Practice Template")
            put("tests_json", defTests.toString())
            put("updated_at", nowIso())
        }
        try {
            writableDatabase.insertWithOnConflict("daily_practice_test_templates", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
        } catch (_: Exception) {}

        return JSONObject().apply {
            put("template_id", "DEFAULT")
            put("template_name", "Default Daily Practice Template")
            put("tests", defTests)
            put("updated_at", nowIso())
        }
    }

    fun saveDailyPracticeTemplate(testsArr: JSONArray, name: String = "Default Daily Practice Template"): Boolean {
        if (testsArr.length() < 2 || testsArr.length() > 4) return false
        return try {
            val cv = ContentValues().apply {
                put("template_id", "DEFAULT")
                put("template_name", name)
                put("tests_json", testsArr.toString())
                put("updated_at", nowIso())
            }
            writableDatabase.insertWithOnConflict("daily_practice_test_templates", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
            true
        } catch (_: Exception) { false }
    }

    fun getDailyPracticeSchedule(dateStr: String): JSONObject? {
        try {
            readableDatabase.rawQuery("SELECT * FROM daily_practice_test_schedules WHERE date = ?", arrayOf(dateStr)).use { c ->
                if (c.moveToFirst()) {
                    val s = JSONObject().apply {
                        put("schedule_id", c.getString(0))
                        put("date", c.getString(1))
                        put("total_tests", c.getInt(2))
                        put("status", c.getString(3))
                        put("submitted_at", c.getString(4))
                        put("locked_at", c.getString(5))
                        put("notes", c.getString(6))
                    }
                    val execs = JSONArray()
                    readableDatabase.rawQuery(
                        "SELECT * FROM daily_practice_test_executions WHERE date = ? ORDER BY test_index ASC",
                        arrayOf(dateStr)
                    ).use { ec ->
                        while (ec.moveToNext()) {
                            val e = JSONObject().apply {
                                put("execution_id", ec.getString(0))
                                put("schedule_id", ec.getString(1))
                                put("date", ec.getString(2))
                                put("test_index", ec.getInt(3))
                                put("category", ec.getString(4))
                                put("title", ec.getString(5))
                                put("start_time", ec.getString(6))
                                put("duration_minutes", ec.getInt(7))
                                put("end_time", ec.getString(8))
                                put("status", ec.getString(9))
                                put("started_at", ec.getString(10))
                                put("completed_at", ec.getString(11))
                                put("points_awarded", ec.getInt(14))
                                put("rescheduled_to", ec.getString(15))
                                put("notes", ec.getString(16))
                            }
                            execs.put(e)
                        }
                    }
                    s.put("executions", execs)
                    return s
                }
            }
        } catch (_: Exception) {}
        return null
    }

    fun submitDailyPracticeSchedule(dateStr: String, testsArr: JSONArray, notes: String = ""): JSONObject? {
        if (testsArr.length() < 2 || testsArr.length() > 4) return null
        val schedId = "PRAC-SCHED-$dateStr"
        val now = nowIso()
        val store = writableDatabase
        store.beginTransaction()
        try {
            val scv = ContentValues().apply {
                put("schedule_id", schedId)
                put("date", dateStr)
                put("total_tests", testsArr.length())
                put("status", "SUBMITTED")
                put("submitted_at", now)
                put("notes", notes)
                put("created_at", now)
                put("updated_at", now)
            }
            store.insertWithOnConflict("daily_practice_test_schedules", null, scv, SQLiteDatabase.CONFLICT_REPLACE)

            for (i in 0 until testsArr.length()) {
                val t = testsArr.getJSONObject(i)
                val tIdx = i + 1
                val cat = t.optString("category", "Quantitative Aptitude")
                val title = t.optString("title", cat)
                val sTime = t.optString("start_time", "10:30")
                val dur = t.optInt("duration_minutes", 30)

                val parts = sTime.split(":")
                val sh = parts.getOrNull(0)?.toIntOrNull() ?: 10
                val sm = parts.getOrNull(1)?.toIntOrNull() ?: 30
                val tot = sh * 60 + sm + dur
                val eTime = String.format(Locale.US, "%02d:%02d", (tot / 60) % 24, tot % 60)

                val ecv = ContentValues().apply {
                    put("execution_id", "PRAC-$dateStr-$tIdx")
                    put("schedule_id", schedId)
                    put("date", dateStr)
                    put("test_index", tIdx)
                    put("category", cat)
                    put("title", title)
                    put("start_time", sTime)
                    put("duration_minutes", dur)
                    put("end_time", eTime)
                    put("status", "SCHEDULED")
                    put("created_at", now)
                    put("updated_at", now)
                }
                store.insertWithOnConflict("daily_practice_test_executions", null, ecv, SQLiteDatabase.CONFLICT_REPLACE)
            }
            store.setTransactionSuccessful()
        } finally {
            store.endTransaction()
        }
        return getDailyPracticeSchedule(dateStr)
    }

    fun startPracticeTestExecution(executionId: String): Boolean {
        return try {
            val cv = ContentValues().apply {
                put("status", "IN_PROGRESS")
                put("started_at", nowIso())
                put("updated_at", nowIso())
            }
            writableDatabase.update("daily_practice_test_executions", cv, "execution_id = ?", arrayOf(executionId)) > 0
        } catch (_: Exception) { false }
    }

    fun completePracticeTestExecution(executionId: String, score: Double? = null, maxScore: Double? = null, notes: String = ""): Boolean {
        return try {
            val now = nowIso()
            val cv = ContentValues().apply {
                put("status", "COMPLETED")
                put("completed_at", now)
                put("points_awarded", 5)
                if (score != null) put("score", score)
                if (maxScore != null) put("max_score", maxScore)
                if (notes.isNotBlank()) put("notes", notes)
                put("updated_at", now)
            }
            writableDatabase.update("daily_practice_test_executions", cv, "execution_id = ?", arrayOf(executionId))

            // Idempotent point credit: 5 pts
            val seId = "se_practice_test_$executionId"
            var alreadyCredited = false
            readableDatabase.rawQuery("SELECT event_id FROM sync_events WHERE event_id = ?", arrayOf(seId)).use { c ->
                alreadyCredited = c.moveToFirst()
            }
            if (!alreadyCredited) {
                enqueueSyncEvent("PRACTICE_TEST_COMPLETED", JSONObject().apply {
                    put("event_id", seId)
                    put("execution_id", executionId)
                    put("points", 5)
                    put("timestamp", now)
                }, appContext)
            }
            true
        } catch (_: Exception) { false }
    }

    fun reschedulePracticeTestExecution(executionId: String, newStartTime: String, reason: String = ""): Boolean {
        return try {
            var dur = 30
            readableDatabase.rawQuery("SELECT duration_minutes FROM daily_practice_test_executions WHERE execution_id = ?", arrayOf(executionId)).use { c ->
                if (c.moveToFirst()) dur = c.getInt(0)
            }
            val parts = newStartTime.split(":")
            val sh = parts.getOrNull(0)?.toIntOrNull() ?: 10
            val sm = parts.getOrNull(1)?.toIntOrNull() ?: 30
            val tot = sh * 60 + sm + dur
            val eTime = String.format(Locale.US, "%02d:%02d", (tot / 60) % 24, tot % 60)

            val cv = ContentValues().apply {
                put("start_time", newStartTime)
                put("end_time", eTime)
                put("status", "RESCHEDULED")
                put("rescheduled_to", "$newStartTime-$eTime")
                put("notes", "Rescheduled: $reason")
                put("updated_at", nowIso())
            }
            writableDatabase.update("daily_practice_test_executions", cv, "execution_id = ?", arrayOf(executionId)) > 0
        } catch (_: Exception) { false }
    }

    fun confirmExamEarlyExit(bookingId: String, actualEndMs: Long = authoritativeNowMs()): JSONObject? {
        return try {
            var date = ""
            readableDatabase.rawQuery("SELECT exam_date FROM exam_prebookings WHERE booking_id = ?", arrayOf(bookingId)).use { c ->
                if (c.moveToFirst()) date = c.getString(0)
            }
            val tz = TimeZone.getTimeZone("Asia/Kolkata")
            val sdfIso = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).apply { timeZone = tz }
            val sdfTime = SimpleDateFormat("HH:mm", Locale.US).apply { timeZone = tz }
            val actualEndIso = sdfIso.format(Date(actualEndMs))
            val actualEndTime = sdfTime.format(Date(actualEndMs))
            val nowStr = nowIso()

            writableDatabase.execSQL(
                "UPDATE exam_prebookings SET status = 'EXAM_COMPLETED_EARLY', actual_end_time = ?, actual_end_iso = ?, actual_end_ms = ?, early_exit_confirmed = 1, early_exit_confirmed_at = ?, monitoring_state = 'EXAM_ENDED_EARLY', version = version + 1, updated_at = ? WHERE booking_id = ?",
                arrayOf(actualEndTime, actualEndIso, actualEndMs, nowStr, nowStr, bookingId)
            )

            if (date.isNotBlank()) {
                reconcileHourlyExamConflicts(date)
            }

            val updated = getExamPrebooking(bookingId)
            if (updated != null) {
                enqueueSyncEvent("EXAM_EARLY_EXIT_CONFIRMED", updated, appContext)
            }
            updated
        } catch (e: Exception) {
            null
        }
    }

    fun reconcileHourlyExamConflicts(date: String = nowDate()) {
        if (date < nowDate()) return
        val store = writableDatabase
        val nowMs = authoritativeNowMs()
        store.beginTransaction()
        try {
            val activeExams = mutableListOf<JSONObject>()
            store.rawQuery(
                "SELECT booking_id, exam_name, subject, institution, exam_date, official_start_time, official_end_time, " +
                "allocation_start_iso, allocation_end_iso, allocation_start_ms, allocation_end_ms, status, " +
                "actual_end_time, actual_end_iso, actual_end_ms, early_exit_confirmed " +
                "FROM exam_prebookings WHERE exam_date = ? AND status NOT IN ('EXAM_CANCELLED', 'CANCELLED', 'EXAM_REJECTED')",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    val b = JSONObject().apply {
                        put("booking_id", c.getString(0))
                        put("exam_name", c.getString(1))
                        put("subject", c.getString(2))
                        put("institution", c.getString(3))
                        put("exam_date", c.getString(4))
                        put("official_start_time", c.getString(5))
                        put("official_end_time", c.getString(6))
                        put("allocation_start_iso", c.getString(7))
                        put("allocation_end_iso", c.getString(8))
                        put("allocation_start_ms", c.getLong(9))
                        put("allocation_end_ms", c.getLong(10))
                        put("status", c.getString(11))
                        put("actual_end_time", c.getString(12) ?: "")
                        put("actual_end_iso", c.getString(13) ?: "")
                        put("actual_end_ms", c.getLong(14))
                        put("early_exit_confirmed", c.getInt(15))
                    }
                    activeExams.add(b)
                }
            }

            store.rawQuery(
                "SELECT activity_id, status, window_start, window_end, details_json, scheduled_time, submitted_at, points FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    val actId = c.getString(0)
                    val currentStatus = c.getString(1)
                    val details = runCatching { JSONObject(c.getString(4) ?: "{}") }.getOrDefault(JSONObject())
                    val wasSuppressedByExam = details.optBoolean("suppressed_by_exam", false)

                    // Skip user-submitted or finalized activities
                    if (currentStatus in listOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "REJECTED", "REJECTED_LATE") ||
                        details.optString("submission_id").isNotBlank() || details.optString("evidence_id").isNotBlank()) {
                        continue
                    }

                    val hStartMs = parseIsoToMs(c.getString(2))
                    val hEndMs = parseIsoToMs(c.getString(3))
                    if (hStartMs <= 0L || hEndMs <= hStartMs) continue

                    // Overlap check: window overlaps allocation if w_start < effective_end and w_end > alloc_start
                    val coveringExam = activeExams.firstOrNull { exam ->
                        val allocStart = exam.optLong("allocation_start_ms", 0L)
                        val isEarly = exam.optInt("early_exit_confirmed", 0) == 1 ||
                            exam.optString("status") in listOf("EXAM_COMPLETED_EARLY", "COMPLETED_EARLY") ||
                            exam.optLong("actual_end_ms", 0L) > 0L
                        val allocEnd = if (isEarly) exam.optLong("actual_end_ms", exam.optLong("allocation_end_ms", 0L))
                                       else exam.optLong("allocation_end_ms", 0L)
                        allocStart > 0L && allocEnd > allocStart && hStartMs < allocEnd && hEndMs > allocStart
                    }

                    if (coveringExam != null) {
                        val examName = coveringExam.optString("exam_name", "Examination")
                        val examId = coveringExam.optString("booking_id")
                        val targetPoints = 8
                        if (!wasSuppressedByExam) {
                            details.put("exam_previous_status", currentStatus)
                            details.put("exam_previous_suppression_reason", details.optString("suppression_reason"))
                            details.put("exam_previous_points", targetPoints)
                        }
                        details.put("suppressed_by_exam", true)
                        details.put("exam_booking_id", examId)
                        details.put("exam_name", examName)
                        details.put("suppression_reason", "Excused — Approved Examination: $examName")
                        details.put("exam_excused_points_awarded", targetPoints)
                        val cv = ContentValues().apply {
                            put("status", "EXCUSED")
                            put("points", targetPoints)
                            put("details_json", details.toString())
                        }
                        store.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                    } else if (wasSuppressedByExam) {
                        val priorReason = details.optString("exam_previous_suppression_reason")
                        val otherExcuse = listOf("suppressed_by_bath", "suppressed_by_gym", "suppressed_by_travel", "suppressed_by_movie", "suppressed_by_official_event")
                            .any { details.optBoolean(it, false) }
                        listOf("suppressed_by_exam", "exam_booking_id", "exam_name", "exam_previous_status", "exam_previous_suppression_reason", "exam_excused_points_awarded")
                            .forEach { details.remove(it) }
                        if (priorReason.isNotBlank()) details.put("suppression_reason", priorReason) else details.remove("suppression_reason")
                        if (details.optString("suppression_reason").contains("exam", ignoreCase = true)) details.remove("suppression_reason")
                        val prevStatus = details.optString("exam_previous_status").takeIf { it.isNotBlank() && it != "EXCUSED" }
                        val restoredStatus = if (otherExcuse) "EXCUSED" else if (nowMs >= hStartMs && nowMs < hEndMs) "OPEN" else prevStatus ?: "SCHEDULED"
                        val cv = ContentValues().apply {
                            put("status", restoredStatus)
                            put("points", if (otherExcuse) 0 else 8)
                            put("details_json", details.toString())
                        }
                        store.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                    }
                }
            }
            store.setTransactionSuccessful()
        } finally {
            store.endTransaction()
        }
    }


    fun finishGymEarly(activityId: String, actualFinishMs: Long = authoritativeNowMs()): Boolean {
        return try {
            var date = ""
            var currentStatus = ""
            var schedIso = ""
            var currentDetailsStr = ""

            readableDatabase.rawQuery(
                "SELECT date, status, scheduled_time, details_json FROM scheduled_activities WHERE activity_id = ? AND type = 'GYM'",
                arrayOf(activityId)
            ).use { c ->
                if (c.moveToFirst()) {
                    date = c.getString(0) ?: ""
                    currentStatus = c.getString(1) ?: ""
                    schedIso = c.getString(2) ?: ""
                    currentDetailsStr = c.getString(3) ?: "{}"
                }
            }

            if (date.isEmpty()) return false
            val terminal = setOf("VERIFIED", "COMPLETED", "REJECTED", "REJECTED_LATE", "MISSED", "CLOSED", "SKIPPED", "EXCUSED", "CANCELLED")
            if (currentStatus in terminal) return false

            val details = runCatching { JSONObject(currentDetailsStr) }.getOrDefault(JSONObject())
            val durationMinutes = details.optInt("duration_minutes", GymWindowRules.DEFAULT_GYM_DURATION_MINUTES)
            val zone = scheduleTimezone()

            // Calculate early finish window starting at actualFinishMs, open for 30 minutes
            val gymWindow = GymWindowRules.calculateGymWindow(
                date = date,
                gymPlannedHhmm = details.optString("planned_time", "18:00"),
                durationMinutes = durationMinutes,
                timezone = zone,
                actualFinishMs = actualFinishMs,
                isEarlyFinish = true
            )

            details.put("is_early_finish", true)
            details.put("actual_finish_ms", actualFinishMs)
            details.put("actual_finish", formatMsToIso(actualFinishMs))
            details.put("early_finish_reported_at", formatMsToIso(actualFinishMs))
            details.put("window_start", gymWindow.windowStartIso)
            details.put("window_end", gymWindow.windowEndIso)
            details.put("window_display", gymWindow.windowDisplay)
            details.put("hour_slot", gymWindow.hourSlot)
            details.put("reserved_start_ms", gymWindow.reservedStartMs)
            details.put("reserved_end_ms", gymWindow.reservedEndMs)

            val cv = ContentValues().apply {
                put("window_start", gymWindow.windowStartIso)
                put("window_end", gymWindow.windowEndIso)
                put("notification_time", gymWindow.notificationIso)
                put("details_json", details.toString())
            }
            writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(activityId))
            true
        } catch (_: Throwable) {
            false
        }
    }

    private val isReconcilingTravel = java.util.concurrent.atomic.AtomicBoolean(false)

    fun reconcileHourlyTravelConflicts(date: String = nowDate()) {
        if (!isReconcilingTravel.compareAndSet(false, true)) return
        try {
            val nowMs = authoritativeNowMs()
            val tz = TimeZone.getTimeZone(scheduleTimezone())
            var travelActId: String? = null
            var travelStatus: String? = null
            var depMs = 0L
            var travelEndMs = 0L
            var proofStartMs = 0L
            var proofEndMs = 0L
            var isPrebooked = false

            readableDatabase.rawQuery(
                "SELECT activity_id, status, scheduled_time, window_start, window_end, details_json, title FROM scheduled_activities WHERE date = ? AND type = 'TRAVEL' AND status != 'CANCELLED'",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    val actId = c.getString(0)
                    val st = c.getString(1)?.uppercase(Locale.US)
                    val detailsStr = c.getString(5)
                    val title = c.getString(6) ?: ""
                    if (TravelBookingRules.isDemoBooking(title, detailsStr)) continue

                    val details = runCatching { JSONObject(detailsStr ?: "{}") }.getOrDefault(JSONObject())
                    val prebooked = details.optBoolean("is_prebooked", false) || details.optString("booking_status") == "CONFIRMED"
                    if (prebooked) {
                        travelActId = actId
                        travelStatus = st
                        depMs = details.optLong("departure_ms", 0L).takeIf { it > 0L } ?: parseIsoToMs(c.getString(2))
                        val durationHours = details.optInt("duration_hours", 3).coerceIn(1, 24)
                        travelEndMs = details.optLong("travel_end_ms", 0L).takeIf { it > 0L } ?: (depMs + durationHours * 3600_000L)
                        proofStartMs = details.optLong("proof_window_start_ms", 0L).takeIf { it > 0L } ?: travelEndMs
                        proofEndMs = details.optLong("proof_window_end_ms", 0L).takeIf { it > 0L } ?: (proofStartMs + 2 * 3600_000L)
                        isPrebooked = true
                        break
                    }
                }
            }

            if (!isPrebooked || depMs <= 0L || travelEndMs <= depMs) {
                // No prebooked travel. If any hourly slots were previously suppressed by travel, restore them if not terminal.
                readableDatabase.rawQuery(
                    "SELECT activity_id, status, details_json FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'",
                    arrayOf(date)
                ).use { c ->
                    while (c.moveToNext()) {
                        val actId = c.getString(0)
                        val status = c.getString(1)
                        val details = runCatching { JSONObject(c.getString(2) ?: "{}") }.getOrDefault(JSONObject())
                        if (details.optBoolean("suppressed_by_travel", false) && (details.optBoolean("travel_hold", false) || status == "EXCUSED")) {
                            details.remove("suppressed_by_travel")
                            details.remove("travel_hold")
                            details.remove("travel_booking_id")
                            details.remove("hold_reason")
                            details.remove("suppression_reason")
                            details.remove("travel_excused")
                            details.remove("not_excused")
                            details.remove("travel_proof_failed")
                            val cv = ContentValues().apply {
                                put("status", "SCHEDULED")
                                put("details_json", details.toString())
                            }
                            writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                        }
                    }
                }
                return
            }

            val hasSubmittedTravelProof = travelStatus in listOf(
                "SUBMITTED", "VALIDATING", "PENDING_SYNC", "AI_PROVISIONAL",
                "PENDING_REVIEW", "VERIFIED", "APPROVED", "COMPLETED"
            )
            val isTravelMissedOrExpired = travelStatus in listOf("MISSED", "EXPIRED", "REJECTED", "REJECTED_LATE") ||
                (nowMs >= proofEndMs && !hasSubmittedTravelProof)

            val displayFmt = SimpleDateFormat("h:mm a", Locale.US).apply { timeZone = tz }
            val proofEndDisplay = displayFmt.format(Date(proofEndMs))

            if (isTravelMissedOrExpired && travelStatus !in listOf("MISSED", "EXPIRED", "REJECTED", "REJECTED_LATE")) {
                writableDatabase.execSQL(
                    "UPDATE scheduled_activities SET status = 'MISSED', points = 0 WHERE activity_id = ?",
                    arrayOf(travelActId)
                )
            }

            readableDatabase.rawQuery(
                "SELECT activity_id, status, scheduled_time, window_start, window_end, details_json FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    val actId = c.getString(0)
                    val currentStatus = c.getString(1)
                    val schedIso = c.getString(2)
                    val detailsStr = c.getString(5)
                    val details = runCatching { JSONObject(detailsStr ?: "{}") }.getOrDefault(JSONObject())

                    // Never modify hourly slots that the user actively submitted or verified
                    if (currentStatus in listOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "REJECTED", "REJECTED_LATE")) {
                        continue
                    }

                    val slotTargetMs = parseIsoToMs(schedIso)
                    if (slotTargetMs == Long.MIN_VALUE) continue

                    val isCovered = TravelBookingRules.isHourlySlotCoveredByTravel(slotTargetMs, depMs, travelEndMs)

                    if (isCovered) {
                        details.put("travel_booking_id", travelActId)
                        if (isTravelMissedOrExpired) {
                            // Proof deadline missed: mark MISSED, points = 0, not excused
                            val reason = "0 points — Travel proof was not submitted by the $proofEndDisplay deadline. This hourly interval was not excused."
                            val changed = currentStatus != "MISSED" || !details.optBoolean("not_excused", false)
                            if (changed) {
                                details.put("suppressed_by_travel", true)
                                details.put("travel_hold", false)
                                details.put("not_excused", true)
                                details.put("travel_proof_failed", true)
                                details.put("suppression_reason", reason)
                                details.put("failure_reason", reason)
                                val cv = ContentValues().apply {
                                    put("status", "MISSED")
                                    put("points", 0)
                                    put("details_json", details.toString())
                                }
                                writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                            }
                        } else if (hasSubmittedTravelProof) {
                            // Proof submitted on time: mark EXCUSED
                            val reason = "Excused by travel verification"
                            val changed = currentStatus != "EXCUSED" || details.optBoolean("travel_hold", true)
                            if (changed) {
                                details.put("suppressed_by_travel", true)
                                details.put("travel_hold", false)
                                details.put("travel_excused", true)
                                details.remove("not_excused")
                                details.remove("travel_proof_failed")
                                details.put("suppression_reason", reason)
                                val cv = ContentValues().apply {
                                    put("status", "EXCUSED")
                                    put("details_json", details.toString())
                                }
                                writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                            }
                        } else {
                            // Hold active (travel ongoing or proof window open)
                            val reason = "Travel hold — awaiting travel proof"
                            val changed = currentStatus != "EXCUSED" || !details.optBoolean("travel_hold", false)
                            if (changed) {
                                details.put("suppressed_by_travel", true)
                                details.put("travel_hold", true)
                                details.put("hold_reason", reason)
                                details.put("suppression_reason", reason)
                                val cv = ContentValues().apply {
                                    put("status", "EXCUSED")
                                    put("details_json", details.toString())
                                }
                                writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                            }
                        }
                    } else {
                        // Slot is NOT covered by travel interval
                        if (details.optBoolean("suppressed_by_travel", false)) {
                            details.remove("suppressed_by_travel")
                            details.remove("travel_hold")
                            details.remove("travel_booking_id")
                            details.remove("hold_reason")
                            details.remove("suppression_reason")
                            details.remove("travel_excused")
                            details.remove("not_excused")
                            details.remove("travel_proof_failed")
                            val cv = ContentValues().apply {
                                put("status", "SCHEDULED")
                                put("details_json", details.toString())
                            }
                            writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                        }
                    }
                }
            }
        } catch (_: Throwable) {
        } finally {
            isReconcilingTravel.set(false)
        }
    }

    private val isReconcilingMovie = java.util.concurrent.atomic.AtomicBoolean(false)

    fun reconcileHourlyMovieConflicts(date: String = nowDate()) {
        if (!isReconcilingMovie.compareAndSet(false, true)) return
        try {
            val nowMs = authoritativeNowMs()
            val tz = TimeZone.getTimeZone(scheduleTimezone())

            val confirmedBookings = mutableListOf<JSONObject>()
            readableDatabase.rawQuery(
                "SELECT movie_booking_id, movie_title, cinema_name, show_date, start_time, end_time, lock_start_ms, lock_end_ms, status, validation_status, details_json FROM movie_bookings WHERE show_date = ? AND status NOT IN ('Booking rejected', 'Booking cancelled', 'REJECTED', 'CANCELLED')",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    val status = c.getString(8).orEmpty()
                    val valStatus = c.getString(9).orEmpty()
                    val isConfirmed = status in listOf(
                        MovieBookingRules.STATE_MOVIE_BOOKING_CONFIRMED,
                        MovieBookingRules.STATE_ATTENDANCE_HOLD_ACTIVE,
                        MovieBookingRules.STATE_RESERVATION_COMPLETED,
                        "CONFIRMED"
                    ) && valStatus in listOf("VERIFIED", "CONFIRMED", MovieBookingRules.STATE_MOVIE_BOOKING_CONFIRMED)

                    if (isConfirmed) {
                        val obj = JSONObject().apply {
                            put("booking_id", c.getString(0))
                            put("title", c.getString(1))
                            put("cinema", c.getString(2))
                            put("show_date", c.getString(3))
                            put("start_time", c.getString(4))
                            put("end_time", c.getString(5))
                            put("lock_start_ms", c.getLong(6))
                            put("lock_end_ms", c.getLong(7))
                            put("status", status)
                            put("details", JSONObject(c.getString(10) ?: "{}"))
                        }
                        confirmedBookings.add(obj)
                    }
                }
            }

            if (confirmedBookings.isEmpty()) {
                readableDatabase.rawQuery(
                    "SELECT activity_id, status, details_json FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'",
                    arrayOf(date)
                ).use { c ->
                    while (c.moveToNext()) {
                        val actId = c.getString(0)
                        val status = c.getString(1)
                        val details = runCatching { JSONObject(c.getString(2) ?: "{}") }.getOrDefault(JSONObject())
                        if (details.optBoolean("suppressed_by_movie", false) && (details.optBoolean("movie_hold", false) || status == "EXCUSED")) {
                            details.remove("suppressed_by_movie")
                            details.remove("movie_hold")
                            details.remove("movie_booking_id")
                            details.remove("movie_title")
                            details.remove("suppression_reason")
                            val cv = ContentValues().apply {
                                put("status", "SCHEDULED")
                                put("details_json", details.toString())
                            }
                            writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                        }
                    }
                }
                return
            }

            readableDatabase.rawQuery(
                "SELECT activity_id, status, scheduled_time, window_start, window_end, details_json FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    val actId = c.getString(0)
                    val currentStatus = c.getString(1)
                    val schedIso = c.getString(2)
                    val detailsStr = c.getString(5)
                    val details = runCatching { JSONObject(detailsStr ?: "{}") }.getOrDefault(JSONObject())

                    if (currentStatus in listOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "REJECTED", "REJECTED_LATE")) {
                        continue
                    }

                    val slotTargetMs = parseIsoToMs(schedIso)
                    if (slotTargetMs == Long.MIN_VALUE) continue

                    var coveringBooking: JSONObject? = null
                    for (b in confirmedBookings) {
                        val lStart = b.optLong("lock_start_ms", 0L)
                        val lEnd = b.optLong("lock_end_ms", 0L)
                        if (MovieBookingRules.isHourlySlotCoveredByMovie(slotTargetMs, lStart, lEnd)) {
                            coveringBooking = b
                            break
                        }
                    }

                    if (coveringBooking != null) {
                        val mId = coveringBooking.optString("booking_id")
                        val mTitle = coveringBooking.optString("title")
                        val reason = "Excused — confirmed movie reservation"
                        val changed = currentStatus != "EXCUSED" || !details.optBoolean("movie_hold", false)
                        if (changed) {
                            details.put("suppressed_by_movie", true)
                            details.put("movie_hold", true)
                            details.put("movie_booking_id", mId)
                            details.put("movie_title", mTitle)
                            details.put("suppression_reason", reason)
                            details.put("hold_reason", reason)
                            val cv = ContentValues().apply {
                                put("status", "EXCUSED")
                                put("points", 0)
                                put("details_json", details.toString())
                            }
                            writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                        }
                    } else if (details.optBoolean("suppressed_by_movie", false) && currentStatus == "EXCUSED" && !details.optBoolean("suppressed_by_travel", false) && !details.optBoolean("suppressed_by_wakeup", false)) {
                        details.remove("suppressed_by_movie")
                        details.remove("movie_hold")
                        details.remove("movie_booking_id")
                        details.remove("movie_title")
                        details.remove("suppression_reason")
                        val cv = ContentValues().apply {
                            put("status", "SCHEDULED")
                            put("details_json", details.toString())
                        }
                        writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                    }
                }
            }
        } catch (_: Throwable) {
        } finally {
            isReconcilingMovie.set(false)
        }
    }

    private val isReconcilingWakeUp = java.util.concurrent.atomic.AtomicBoolean(false)

    fun reconcileHourlyWakeUpConflicts(date: String = nowDate()) {
        if (!isReconcilingWakeUp.compareAndSet(false, true)) return
        try {
            val nowMs = authoritativeNowMs()
            val scheduleZone = scheduleTimezone()
            val tz = TimeZone.getTimeZone(scheduleZone)

            var wakeActId: String? = null
            var wakeStatus: String? = null
            var wakeStartMs = 0L
            var wakeEndMs = 0L
            var wakeSubmittedAt: String? = null

            readableDatabase.rawQuery(
                "SELECT activity_id, status, window_start, window_end, submitted_at FROM scheduled_activities WHERE date = ? AND (type = 'WAKE_UP' OR activity_id LIKE '%WAKEUP%') LIMIT 1",
                arrayOf(date)
            ).use { c ->
                if (c.moveToFirst()) {
                    wakeActId = c.getString(0)
                    wakeStatus = c.getString(1)?.uppercase(Locale.US)
                    wakeStartMs = parseIsoToMs(c.getString(2))
                    wakeEndMs = parseIsoToMs(c.getString(3))
                    wakeSubmittedAt = c.getString(4)
                }
            }

            if (wakeEndMs <= 0L) {
                // Fallback to 9:30 AM in schedule timezone
                val cal = Calendar.getInstance(tz).apply {
                    time = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { timeZone = tz }.parse(date) ?: Date()
                    set(Calendar.HOUR_OF_DAY, 9)
                    set(Calendar.MINUTE, 30)
                    set(Calendar.SECOND, 0)
                    set(Calendar.MILLISECOND, 0)
                }
                wakeEndMs = cal.timeInMillis
            }

            val gateActive = getDailyRoutineGate(date).optBoolean("active", false)
            val dayWakeStatus = readableDatabase.rawQuery(
                "SELECT wakeup_status FROM daily_state WHERE date = ? AND is_dev_mode = 0",
                arrayOf(date)
            ).use { sc -> if (sc.moveToFirst()) sc.getString(0).orEmpty().uppercase(Locale.US) else "" }

            val wakeFlow = getWakeUpFlowStatus(date, false)
            val allCompleted = wakeFlow.optBoolean("all_completed", false)
            val hasApprovedReview = readableDatabase.rawQuery(
                "SELECT 1 FROM proof_reviews WHERE (activity_id = ? OR task_code IN ('WAKE_UP', 'WAKEUP')) AND date = ? AND status IN ('APPROVED', 'VERIFIED')",
                arrayOf(wakeActId ?: "", date)
            ).use { it.moveToFirst() }

            val isWakeCompleted = hasApprovedReview ||
                allCompleted ||
                (dayWakeStatus in listOf("VERIFIED", "COMPLETED", "PENDING_SERVER")) ||
                gateActive

            val isWakeMissedCutoff = !isWakeCompleted && (nowMs >= wakeEndMs || wakeStatus in listOf("MISSED", "EXPIRED", "REJECTED", "REJECTED_LATE", "SUBMITTED", "VALIDATING", "AI_PROVISIONAL"))

            // If cutoff passed without wake completion, finalize the WAKE_UP activity itself if not already terminal
            if (isWakeMissedCutoff && wakeActId != null && wakeStatus !in listOf("VERIFIED", "APPROVED", "COMPLETED")) {
                val cvWake = ContentValues().apply {
                    put("status", "MISSED")
                    put("points", 0)
                    putNull("submitted_at")
                }
                writableDatabase.update("scheduled_activities", cvWake, "activity_id = ?", arrayOf(wakeActId))
            }

            // Morning cutoff for hourly slots is 10:00 AM (targetMs <= 10:00 AM)
            val morningSlotsCutoffMs = Calendar.getInstance(tz).apply {
                time = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { timeZone = tz }.parse(date) ?: Date()
                set(Calendar.HOUR_OF_DAY, 10)
                set(Calendar.MINUTE, 0)
                set(Calendar.SECOND, 0)
                set(Calendar.MILLISECOND, 0)
            }.timeInMillis

            readableDatabase.rawQuery(
                "SELECT activity_id, status, scheduled_time, window_start, window_end, details_json FROM scheduled_activities WHERE date = ? AND type = 'HOURLY'",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    val actId = c.getString(0)
                    val currentStatus = c.getString(1)
                    val schedIso = c.getString(2)
                    val detailsStr = c.getString(5)
                    val details = runCatching { JSONObject(detailsStr ?: "{}") }.getOrDefault(JSONObject())

                    // Never modify hourly slots that the user actively submitted or verified
                    if (currentStatus in listOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "REJECTED", "REJECTED_LATE")) {
                        continue
                    }

                    // Skip bath-suppressed or travel-suppressed slots (they have their own rules)
                    if (details.optBoolean("suppressed_by_bath", false) || details.optBoolean("suppressed_by_travel", false)) {
                        continue
                    }

                    val slotTargetMs = parseIsoToMs(schedIso)
                    val isCollegeDayNow = getCollegeAttendance(date) == "YES"
                    val isCollegeFirstHourly = isCollegeDayNow && (details.optString("hour_slot") == "09:00-10:00" || actId.contains("0900-HOURLY"))
                    val isMorningSlot = !isCollegeFirstHourly && slotTargetMs != Long.MIN_VALUE && slotTargetMs <= morningSlotsCutoffMs

                    if (isMorningSlot) {
                        if (isWakeCompleted) {
                            // Wake-up was completed! Bypassed morning slots are excused.
                            val reason = "Excused by wake-up verification"
                            val changed = currentStatus != "EXCUSED" || details.optBoolean("wake_hold", false)
                            if (changed) {
                                details.put("suppressed_by_wakeup", true)
                                details.put("wake_hold", false)
                                details.put("excused_by_wakeup", true)
                                details.put("suppression_reason", reason)
                                val cv = ContentValues().apply {
                                    put("status", "EXCUSED")
                                    put("details_json", details.toString())
                                }
                                writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                            }
                        } else if (isWakeMissedCutoff) {
                            // Wake-up missed cutoff at 9:30 AM: pre-10 AM slots bypassed/excused without extra deductions
                            val reason = "Bypassed: wake-up cutoff reached (woke up late). Resumed at 10:00–11:00 AM."
                            val changed = currentStatus != "EXCUSED" || details.optBoolean("wake_hold", false)
                            if (changed) {
                                details.put("suppressed_by_wakeup", true)
                                details.put("wake_hold", false)
                                details.put("excused_by_wakeup", true)
                                details.put("suppression_reason", reason)
                                val cv = ContentValues().apply {
                                    put("status", "EXCUSED")
                                    put("points", 0)
                                    put("details_json", details.toString())
                                }
                                writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                            }
                        } else {
                            // Morning grace period before 9:30 AM: wake-up verification pending!
                            // Hold these morning slots so they are NOT marked MISSED.
                            val reason = "Wake-up verification pending"
                            val changed = currentStatus != "EXCUSED" || !details.optBoolean("wake_hold", false)
                            if (changed) {
                                details.put("suppressed_by_wakeup", true)
                                details.put("wake_hold", true)
                                details.put("hold_reason", reason)
                                details.put("suppression_reason", reason)
                                val cv = ContentValues().apply {
                                    put("status", "EXCUSED")
                                    put("details_json", details.toString())
                                }
                                writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                            }
                        }
                    } else {
                        // Slots after 10:00 AM (targetMs > 10:00 AM, e.g. 10:00–11:00 AM slot with target 11:00 AM):
                        // These must NOT be held or suppressed by wake-up!
                        if (details.optBoolean("suppressed_by_wakeup", false)) {
                            details.remove("suppressed_by_wakeup")
                            details.remove("wake_hold")
                            details.remove("hold_reason")
                            details.remove("suppression_reason")
                            details.remove("excused_by_wakeup")
                            val cv = ContentValues().apply {
                                put("status", "SCHEDULED")
                                put("details_json", details.toString())
                            }
                            writableDatabase.update("scheduled_activities", cv, "activity_id = ?", arrayOf(actId))
                        }
                    }
                }
            }
        } catch (_: Throwable) {
        } finally {
            isReconcilingWakeUp.set(false)
        }
    }

    private val isReconcilingSubmissions = java.util.concurrent.atomic.AtomicBoolean(false)

    fun reconcileValidatedSubmissions(date: String = nowDate()) {
        if (!isReconcilingSubmissions.compareAndSet(false, true)) return
        try {
            val pendingReviews = mutableListOf<JSONObject>()
            readableDatabase.rawQuery(
                "SELECT evidence_id, submission_id, activity_id, task_code, file_path, sha256_hash, status, points_awarded FROM proof_reviews WHERE date = ? AND status = 'PENDING'",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    pendingReviews.add(JSONObject().apply {
                        put("evidence_id", c.getString(0) ?: "")
                        put("submission_id", c.getString(1) ?: "")
                        put("activity_id", c.getString(2) ?: "")
                        put("task_code", c.getString(3) ?: "")
                        put("file_path", c.getString(4) ?: "")
                        put("sha256_hash", c.getString(5) ?: "")
                        put("status", c.getString(6) ?: "")
                        put("points_awarded", c.getInt(7))
                    })
                }
            }

            val processedActivities = mutableSetOf<String>()
            val processedSubmissions = mutableSetOf<String>()

            for (rev in pendingReviews) {
                val evId = rev.optString("evidence_id")
                val actId = rev.optString("activity_id")
                val subId = rev.optString("submission_id")
                val filePath = rev.optString("file_path")
                val taskCode = rev.optString("task_code")

                if (evId.isBlank()) continue

                val isSecondaryAttachment = (actId.isNotBlank() && processedActivities.contains(actId)) ||
                                            (subId.isNotBlank() && processedSubmissions.contains(subId))

                val hasFile = filePath.isNotBlank() && File(filePath).let { it.exists() && it.length() > 0 }
                if (!hasFile && !hasApprovedRetainedProof(evId)) continue

                var isExactDup = false
                readableDatabase.rawQuery(
                    "SELECT duplicate_status FROM evidence_fingerprints WHERE evidence_id = ?",
                    arrayOf(evId)
                ).use { c ->
                    if (c.moveToFirst() && c.getString(0) == "EXACT_DUPLICATE") {
                        isExactDup = true
                    }
                }
                if (isExactDup) continue

                if (isSecondaryAttachment) {
                    approveProof(evId, "Automated Validator", 0)
                    continue
                }

                val alreadyAwarded = if (actId.isNotBlank()) {
                    readableDatabase.rawQuery(
                        "SELECT 1 FROM score_events WHERE date = ? AND is_dev_mode = 0 AND points > 0 AND (related_task_id = ? OR audit_json LIKE ?)",
                        arrayOf(date, actId, "%\"activity_id\": \"$actId\"%")
                    ).use { it.moveToFirst() }
                } else false

                if (alreadyAwarded) {
                    approveProof(evId, "Automated Validator", 0)
                    if (actId.isNotBlank()) processedActivities.add(actId)
                    if (subId.isNotBlank()) processedSubmissions.add(subId)
                    continue
                }

                var targetPoints = 0
                var actType = taskCode.uppercase(Locale.US).trim()
                if (actId.isNotBlank()) {
                    readableDatabase.rawQuery(
                        "SELECT configured_points, type FROM scheduled_activities WHERE activity_id = ?",
                        arrayOf(actId)
                    ).use { c ->
                        if (c.moveToFirst()) {
                            targetPoints = c.getInt(0)
                            val t = c.getString(1).orEmpty().trim()
                            if (t.isNotBlank()) actType = t.uppercase(Locale.US)
                        }
                    }
                }
                if (targetPoints <= 0) {
                    targetPoints = when (actType) {
                        "BATH" -> ProductPolicy.BATH_POINTS_PER_OCCURRENCE
                        "GYM" -> 15
                        "TRAVEL" -> 10
                        "WAKE_UP", "WAKEUP" -> 15
                        else -> 8
                    }
                }

                approveProof(evId, "Automated Validator", targetPoints)
                if (actId.isNotBlank()) processedActivities.add(actId)
                if (subId.isNotBlank()) processedSubmissions.add(subId)

                if (actId.isNotBlank()) {
                    writableDatabase.execSQL(
                        "UPDATE scheduled_activities SET status = 'VERIFIED', points = ?, attendance_status = 'PRESENT', validation_status = 'VERIFIED' WHERE activity_id = ?",
                        arrayOf(targetPoints, actId)
                    )
                }

                if (actType == "BATH") {
                    val countNow = getBathSubmissionCount(date)
                    val bathSt = if (countNow <= 1) "BATH 1 VERIFIED (+5 PTS)" else "BATH COMPLETE (2/2) (+5 PTS)"
                    writableDatabase.execSQL("UPDATE daily_state SET bath_status = ? WHERE date = ?", arrayOf(bathSt, date))
                } else if (actType == "GYM") {
                    writableDatabase.execSQL("UPDATE daily_state SET gym_status = 'GYM VERIFIED (+15 PTS)' WHERE date = ?", arrayOf(date))
                }
            }
        } catch (_: Throwable) {
        } finally {
            isReconcilingSubmissions.set(false)
        }
    }

    fun ensureGymOccurrence(date: String = nowDate()): String {
        return try {
            var gymTarget = DisciplineScoringRules.DEFAULT_GYM_TIME
            var gymDurationMinutes = GymWindowRules.DEFAULT_GYM_DURATION_MINUTES
            var gymEnabled = true
            var scheduleZone = scheduleTimezone()
            var scheduleVersion = 1

            runCatching {
                readableDatabase.rawQuery(
                    "SELECT gym_time, gym_enabled, timezone, schedule_version, gym_duration_minutes FROM routine_schedules WHERE schedule_date = ? ORDER BY (CASE WHEN status = 'SCHEDULED' THEN 1 ELSE 0 END) DESC, schedule_version DESC LIMIT 1",
                    arrayOf(date)
                ).use { cursor ->
                    if (cursor.moveToFirst()) {
                        val t = cursor.getString(0)
                        if (!t.isNullOrBlank()) gymTarget = t.trim()
                        gymEnabled = cursor.getInt(1) == 1
                        val z = cursor.getString(2)
                        if (!z.isNullOrBlank()) scheduleZone = z.trim()
                        scheduleVersion = cursor.getInt(3)
                        val durCol = cursor.getColumnIndex("gym_duration_minutes")
                        if (durCol >= 0 && !cursor.isNull(durCol)) {
                            val d = cursor.getInt(durCol)
                            if (d > 0) gymDurationMinutes = d
                        }
                    }
                }
            }

            if (GymPolicyClient.decision(this,date).optString("status") == "GYM_NOT_PLANNED") gymEnabled = false

            val gymChoice = getDailyGymChoice(date)
            val isGymExplicitlyNotPlanned = gymChoice == "NO_GYM" ||
                GymPolicyClient.decision(this, date).optString("status") == "GYM_NOT_PLANNED"

            if (isGymExplicitlyNotPlanned) {
                try {
                    writableDatabase.delete(
                        "scheduled_activities",
                        "date = ? AND type = 'GYM' AND status IN ('SCHEDULED', 'UPCOMING', 'NOTIFIED')",
                        arrayOf(date)
                    )
                } catch (_: Exception) {}
                return ""
            }

            val dailyGym = getDailyGymSetup(date)
            if (dailyGym != null && dailyGym.isGymPlanned) {
                dailyGym.plannedStartTime?.takeIf { it.isNotBlank() }?.let { gymTarget = it }
                if (dailyGym.plannedDurationMinutes > 0) gymDurationMinutes = dailyGym.plannedDurationMinutes
                gymEnabled = true
            }

            // Check if there is an existing Gym activity for this date
            var existingId: String? = null
            var existingStatus: String? = null
            var existingDetailsStr: String? = null
            readableDatabase.rawQuery(
                "SELECT activity_id, status, details_json FROM scheduled_activities WHERE date = ? AND type = 'GYM' LIMIT 1",
                arrayOf(date)
            ).use { c ->
                if (c.moveToFirst()) {
                    existingId = c.getString(0)
                    existingStatus = c.getString(1)
                    existingDetailsStr = c.getString(2)
                }
            }

            val existingDetails = runCatching { JSONObject(existingDetailsStr ?: "{}") }.getOrDefault(JSONObject())
            val durationMinutes = existingDetails.optInt("duration_minutes", gymDurationMinutes)
            val isEarlyFinish = existingDetails.optBoolean("is_early_finish", false)
            val actualFinishMs = if (isEarlyFinish && existingDetails.has("actual_finish_ms")) existingDetails.optLong("actual_finish_ms") else null

            val gymWindow = GymWindowRules.calculateGymWindow(
                date = date,
                gymPlannedHhmm = gymTarget,
                durationMinutes = durationMinutes,
                timezone = scheduleZone,
                actualFinishMs = actualFinishMs,
                isEarlyFinish = isEarlyFinish
            )
            val actId = "ACT-${date.replace("-", "")}-${gymWindow.plannedHhmmClean}-GYM"
            val bookingId = "GYM-${date.replace("-", "")}-${gymWindow.plannedHhmmClean}"

            val detailsJson = existingDetails.apply {
                put("planned_time", gymWindow.plannedHhmm)
                put("target_time", gymWindow.plannedHhmm)
                put("duration_minutes", gymWindow.durationMinutes)
                put("expected_finish", gymWindow.expectedFinishIso)
                put("expected_finish_ms", gymWindow.expectedFinishMs)
                put("expected_finish_display", gymWindow.expectedFinishDisplay)
                put("window_start", gymWindow.windowStartIso)
                put("window_end", gymWindow.windowEndIso)
                put("hour_slot", gymWindow.hourSlot)
                put("window_display", gymWindow.windowDisplay)
                put("reserved_start", gymWindow.plannedIso)
                put("reserved_start_ms", gymWindow.reservedStartMs)
                put("reserved_end", gymWindow.expectedFinishIso)
                put("reserved_end_ms", gymWindow.reservedEndMs)
                put("gym_booking_id", bookingId)
                put("proposed_gym_start", gymWindow.plannedIso)
                put("proposed_duration_minutes", gymWindow.durationMinutes)
                put("proposed_gym_end", gymWindow.expectedFinishIso)
                remove("gym_hold_active")
            }.toString()

            if (existingId != null) {
                val terminal = setOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "REJECTED", "REJECTED_LATE", "MISSED", "CLOSED", "SKIPPED", "EXCUSED", "CANCELLED")
                if (existingStatus !in terminal) {
                    val updateCv = ContentValues().apply {
                        put("scheduled_time", gymWindow.plannedIso)
                        put("window_start", gymWindow.windowStartIso)
                        put("window_end", gymWindow.windowEndIso)
                        put("notification_time", gymWindow.notificationIso)
                        put("details_json", detailsJson)
                    }
                    writableDatabase.update("scheduled_activities", updateCv, "activity_id = ?", arrayOf(existingId))
                }
                return existingId!!
            }

            val cv = ContentValues().apply {
                put("activity_id", actId)
                put("account_id", "user_lok")
                put("type", "GYM")
                put("date", date)
                put("title", "Gym Workout Session")
                put("scheduled_time", gymWindow.plannedIso)
                put("window_start", gymWindow.windowStartIso)
                put("window_end", gymWindow.windowEndIso)
                put("notification_time", gymWindow.notificationIso)
                put("status", "SCHEDULED")
                put("points", 0)
                put("configured_points", 15)
                put("validation_rule", "ACTIVITY_PHOTO_PROOF")
                put("schedule_version", scheduleVersion)
                put("details_json", detailsJson)
            }
            writableDatabase.insertWithOnConflict("scheduled_activities", null, cv, SQLiteDatabase.CONFLICT_IGNORE)
            actId
        } catch (_: Throwable) {
            "ACT-${date.replace("-", "")}-1830-GYM"
        }
    }

    fun getActiveOrTargetGymActivity(nowMs: Long = authoritativeNowMs()): DisciplineScoringRules.ScheduledActivityItem? {
        val today = nowDate()
        val yesterday = try {
            val sdf = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { timeZone = TimeZone.getTimeZone(scheduleTimezone()) }
            val dt = sdf.parse(today) ?: Date()
            val cal = Calendar.getInstance(TimeZone.getTimeZone(scheduleTimezone())).apply {
                time = dt
                add(Calendar.DAY_OF_YEAR, -1)
            }
            sdf.format(cal.time)
        } catch (_: Exception) { "" }

        if (yesterday.isNotEmpty()) {
            val yOcc = getDailyActivities(yesterday).firstOrNull { it.type == "GYM" }
            if (yOcc != null) {
                val startMs = parseIsoToMs(yOcc.windowStart)
                val endMs = parseIsoToMs(yOcc.windowEnd)
                if (GymWindowRules.isSubmissionWithinWindow(nowMs, startMs, endMs) &&
                    yOcc.status !in listOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "MISSED", "REJECTED")) {
                    return yOcc
                }
            }
        }

        return getDailyActivities(today).firstOrNull { it.type == "GYM" }
    }

    fun ensureTravelOccurrence(date: String = nowDate()): String {
        return try {
            readableDatabase.rawQuery(
                "SELECT activity_id FROM scheduled_activities WHERE date = ? AND type = 'TRAVEL' AND (details_json LIKE '%is_prebooked%' OR details_json LIKE '%CONFIRMED%') AND status != 'CANCELLED' LIMIT 1",
                arrayOf(date)
            ).use { if (it.moveToFirst()) it.getString(0) else "" }
        } catch (_: Throwable) {
            ""
        }
    }

    fun getTravelPrebookingRestriction(nowMs: Long = authoritativeNowMs()): TravelBookingRules.TravelRestriction {
        val records = mutableListOf<TravelBookingRules.TravelOccurrenceRecord>()
        try {
            readableDatabase.rawQuery(
                "SELECT activity_id, date, window_start, window_end, status, details_json, title FROM scheduled_activities WHERE type = 'TRAVEL' AND status != 'CANCELLED' ORDER BY date ASC, scheduled_time ASC",
                null
            ).use { c ->
                while (c.moveToNext()) {
                    val actId = c.getString(0).orEmpty()
                    val d = c.getString(1).orEmpty()
                    val wStartMs = parseIsoToMs(c.getString(2))
                    val wEndMs = parseIsoToMs(c.getString(3))
                    val st = c.getString(4).orEmpty()
                    val rawDetails = c.getString(5).orEmpty()
                    val title = c.getString(6).orEmpty()
                    val details = runCatching { JSONObject(rawDetails) }.getOrNull()
                    val isPrebookedFlag = details?.optBoolean("is_prebooked", false) == true
                    val isConfirmed = details?.optString("booking_status") == "CONFIRMED"
                    if (!isPrebookedFlag && !isConfirmed) continue // Ignore un-prebooked dummy rows
                    if (TravelBookingRules.isDemoBooking(title, rawDetails)) continue // Demo runs never count as failures

                    val depMs = details?.optLong("departure_ms", 0L) ?: wStartMs
                    val travelEndMs = details?.optLong("travel_end_ms", 0L).let { if (it != null && it > 0L) it else wStartMs }
                    val isProofSubmitted = st in listOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "PENDING_SYNC", "VERIFIED", "APPROVED", "COMPLETED") ||
                        details?.optBoolean("travel_proof_submitted", false) == true

                    records.add(
                        TravelBookingRules.TravelOccurrenceRecord(
                            activityId = actId,
                            date = d,
                            departureMs = depMs,
                            travelEndMs = travelEndMs,
                            windowEndMs = wEndMs,
                            status = st,
                            proofSubmitted = isProofSubmitted,
                            isDemo = false
                        )
                    )
                }
            }
        } catch (_: Exception) {}
        return TravelBookingRules.evaluateConsecutiveFailures(records, nowMs, TimeZone.getTimeZone(scheduleTimezone()))
    }

    fun getScheduleIdAndVersionForDate(date: String): Pair<String, Int> {
        return runCatching {
            readableDatabase.rawQuery(
                "SELECT schedule_id, schedule_version FROM routine_schedules WHERE schedule_date = ? ORDER BY (CASE WHEN status = 'SCHEDULED' THEN 1 ELSE 0 END) DESC, schedule_version DESC LIMIT 1",
                arrayOf(date)
            ).use { c ->
                if (c.moveToFirst()) {
                    Pair(c.getString(0) ?: "", c.getInt(1))
                } else {
                    Pair("", 1)
                }
            }
        }.getOrDefault(Pair("", 1))
    }

    fun getActivitySyncInfo(activityId: String, date: String = ""): DisciplineScoringRules.ActivitySyncInfo {
        try {
            val localExists = readableDatabase.rawQuery(
                "SELECT status, submitted_at FROM scheduled_activities WHERE activity_id = ?",
                arrayOf(activityId)
            ).use { it.moveToFirst() }

            if (!localExists) {
                return DisciplineScoringRules.ActivitySyncInfo("LOCAL_SAVED", "Local record not found", "No local activity found")
            }

            val eventStatuses = mutableListOf<String>()
            readableDatabase.rawQuery(
                "SELECT sync_status FROM sync_events_queue WHERE payload_json LIKE ? OR event_id LIKE ?",
                arrayOf("%\"activity_id\":\"$activityId\"%", "%$activityId%")
            ).use { c ->
                while (c.moveToNext()) {
                    eventStatuses.add(c.getString(0).orEmpty().uppercase(Locale.US))
                }
            }

            val hasRemoteReview = if (eventStatuses.isEmpty()) {
                readableDatabase.rawQuery(
                    "SELECT 1 FROM proof_reviews WHERE activity_id = ? LIMIT 1",
                    arrayOf(activityId)
                ).use { it.moveToFirst() }
            } else false

            return TravelBookingRules.resolveActivitySyncLabel(localExists, eventStatuses, hasRemoteReview)
        } catch (_: Exception) {
            return DisciplineScoringRules.ActivitySyncInfo("LOCAL_SAVED", "Local record saved", "Status check fallback")
        }
    }

    fun prebookTravel(
        travelDate: String,
        departureTime: String,
        durationHours: Int = 3,
        description: String,
        advanceProofBytes: ByteArray? = null,
        advanceProofName: String? = null
    ): JSONObject {
        val tz = TimeZone.getTimeZone(scheduleTimezone())
        val nowMs = authoritativeNowMs()

        // 3 consecutive missed travel proof restriction check
        val restriction = getTravelPrebookingRestriction(nowMs)
        if (restriction.isRestricted) {
            val failedDatesStr = restriction.failedDates.joinToString(", ")
            return JSONObject().put("success", false)
                .put("error", "Travel pre-booking is restricted until ${restriction.lockUntilDateStr} due to 3 consecutive missed travel proof submissions ($failedDatesStr).")
        }

        val eval = TravelBookingRules.evaluateBookingWindow(travelDate, departureTime, nowMs, tz)
        if (!eval.allowed) {
            return JSONObject().put("success", false).put("error", eval.reason)
        }

        val cleanDesc = description.trim()
        if (cleanDesc.isBlank()) {
            return JSONObject().put("success", false).put("error", "Travel description is required.")
        }

        val safeDuration = durationHours.coerceIn(1, 24)
        val cleanTime = departureTime.trim().replace(":", "")
        val actId = "ACT-${travelDate.replace("-", "")}-${cleanTime}-TRAVEL"
        val bookingId = "TBK-${travelDate.replace("-", "")}-${cleanTime}"
        val (schedId, schedVer) = getScheduleIdAndVersionForDate(travelDate)

        // Prevent duplicate bookings on repeated taps
        val existing = readableDatabase.rawQuery(
            "SELECT activity_id, status FROM scheduled_activities WHERE date = ? AND type = 'TRAVEL' AND status != 'CANCELLED' LIMIT 1",
            arrayOf(travelDate)
        ).use {
            if (it.moveToFirst()) Pair(it.getString(0), it.getString(1)) else null
        }
        if (existing != null) {
            val existingDetails = readableDatabase.rawQuery(
                "SELECT details_json, schedule_version FROM scheduled_activities WHERE activity_id = ?",
                arrayOf(existing.first)
            ).use { c ->
                if (c.moveToFirst()) {
                    val raw = c.getString(0).orEmpty()
                    val sVer = c.getInt(1)
                    val det = runCatching { JSONObject(raw) }.getOrNull()
                    Pair(det?.optString("booking_id", bookingId) ?: bookingId, sVer)
                } else Pair(bookingId, schedVer)
            }
            return JSONObject().put("success", true).put("activity_id", existing.first)
                .put("booking_id", existingDetails.first)
                .put("schedule_version", existingDetails.second)
                .put("status", existing.second)
                .put("message", "Travel is already booked for $travelDate.")
        }

        val travelEndMs = eval.departureMs + (safeDuration * 3600_000L)
        val proofStartMs = travelEndMs
        val proofEndMs = travelEndMs + (2 * 3600_000L)
        val travelReminderMs = proofStartMs - TravelBookingRules.PRE_OPENING_REMINDER_MS

        val timeFmt = SimpleDateFormat("HH:mm", Locale.US).apply { this.timeZone = tz }
        val travelEndTimeStr = timeFmt.format(Date(travelEndMs))
        val proofEndTimeStr = timeFmt.format(Date(proofEndMs))

        val details = JSONObject().apply {
            put("type", "TRAVEL")
            put("is_prebooked", true)
            put("is_demo", false)
            put("booking_id", bookingId)
            put("schedule_id", schedId)
            put("schedule_version", schedVer)
            put("departure_time", departureTime.trim())
            put("departure_ms", eval.departureMs)
            put("duration_hours", safeDuration)
            put("travel_end_ms", travelEndMs)
            put("travel_end_time", travelEndTimeStr)
            put("proof_window_start_ms", proofStartMs)
            put("proof_window_end_ms", proofEndMs)
            put("booking_time_ms", nowMs)
            put("booking_deadline_ms", eval.bookingDeadlineMs)
            put("description", cleanDesc)
            put("booking_status", "CONFIRMED")
            if (advanceProofName != null) put("advance_proof_name", advanceProofName)
        }

        val cv = ContentValues().apply {
            put("activity_id", actId)
            put("account_id", "user_lok")
            put("type", "TRAVEL")
            put("date", travelDate)
            put("title", "Travel & Transit ($cleanDesc)")
            put("scheduled_time", formatMsToIso(eval.departureMs))
            put("window_start", formatMsToIso(proofStartMs))
            put("window_end", formatMsToIso(proofEndMs))
            put("notification_time", formatMsToIso(travelReminderMs))
            put("status", "SCHEDULED")
            put("points", 0)
            put("configured_points", 10)
            put("validation_rule", "ACTIVITY_PHOTO_PROOF")
            put("schedule_id", schedId)
            put("schedule_version", schedVer)
            put("details_json", details.toString())
        }
        writableDatabase.insertWithOnConflict("scheduled_activities", null, cv, SQLiteDatabase.CONFLICT_REPLACE)

        reconcileHourlyTravelConflicts(travelDate)

        enqueueSyncEvent("TRAVEL_PREBOOKED", JSONObject().apply {
            put("booking_id", bookingId)
            put("activity_id", actId)
            put("date", travelDate)
            put("departure_time", departureTime.trim())
            put("departure_ms", eval.departureMs)
            put("duration_hours", safeDuration)
            put("travel_end_ms", travelEndMs)
            put("description", cleanDesc)
            put("booking_status", "CONFIRMED")
            put("schedule_id", schedId)
            put("schedule_version", schedVer)
        }, appContext, idempotencyKey = "evt_travel_prebook_$actId")

        val displayFmt = SimpleDateFormat("h:mm a", Locale.US).apply { this.timeZone = tz }
        val depDisplay = displayFmt.format(Date(eval.departureMs))
        val arrDisplay = displayFmt.format(Date(travelEndMs))
        val proofDisplay = displayFmt.format(Date(proofEndMs))

        return JSONObject().put("success", true)
            .put("activity_id", actId)
            .put("booking_id", bookingId)
            .put("schedule_version", schedVer)
            .put("booking_status", "CONFIRMED")
            .put("travel_end_time", travelEndTimeStr)
            .put("message", "Travel pre-booked for $travelDate ($depDisplay–$arrDisplay). Proof submission opens on arrival at $arrDisplay (until $proofDisplay).")
    }

    fun prebookTravel(
        travelDate: String,
        departureTime: String,
        description: String,
        advanceProofBytes: ByteArray? = null,
        advanceProofName: String? = null
    ): JSONObject = prebookTravel(travelDate, departureTime, 3, description, advanceProofBytes, advanceProofName)

    fun isTravelPrebooked(date: String = nowDate()): Boolean {
        return try {
            readableDatabase.rawQuery(
                "SELECT details_json, activity_id, title FROM scheduled_activities WHERE date = ? AND type = 'TRAVEL' AND status != 'CANCELLED' LIMIT 1",
                arrayOf(date)
            ).use { c ->
                if (c.moveToFirst()) {
                    val raw = c.getString(0).orEmpty()
                    val details = runCatching { JSONObject(raw) }.getOrNull()
                    val isPrebookedFlag = details?.optBoolean("is_prebooked", false) == true
                    val isConfirmed = details?.optString("booking_status") == "CONFIRMED"
                    val actId = c.getString(1).orEmpty()
                    val title = c.getString(2).orEmpty()
                    val isAutoSeededDummy = (actId.endsWith("-0900-TRAVEL") && title == "Travel & Transit Protocol" && !isPrebookedFlag)
                    (isPrebookedFlag || isConfirmed || !isAutoSeededDummy) && !isAutoSeededDummy
                } else false
            }
        } catch (_: Exception) {
            false
        }
    }

    fun getUpcomingTravelBookings(currentDate: String = nowDate()): List<DisciplineScoringRules.ScheduledActivityItem> {
        val list = mutableListOf<DisciplineScoringRules.ScheduledActivityItem>()
        val nowMs = authoritativeNowMs()
        readableDatabase.rawQuery(
            "SELECT activity_id, account_id, type, date, title, scheduled_time, window_start, window_end, notification_time, status, submitted_at, points, validation_rule, details_json, schedule_id, schedule_version, configured_points FROM scheduled_activities WHERE type = 'TRAVEL' AND date >= ? AND status != 'CANCELLED' ORDER BY date ASC, scheduled_time ASC",
            arrayOf(currentDate)
        ).use { c ->
            while (c.moveToNext()) {
                val actDate = c.getString(3).orEmpty()
                val raw = c.getString(13) ?: ""
                val details = runCatching { JSONObject(raw) }.getOrNull()
                val isPrebookedFlag = details?.optBoolean("is_prebooked", false) == true
                val isConfirmed = details?.optString("booking_status") == "CONFIRMED"
                val actId = c.getString(0).orEmpty()
                val title = c.getString(4).orEmpty()
                val status = c.getString(9).orEmpty().uppercase(Locale.US)
                val isAutoSeededDummy = (actId.endsWith("-0900-TRAVEL") && title == "Travel & Transit Protocol" && !isPrebookedFlag)
                if ((isPrebookedFlag || isConfirmed || !isAutoSeededDummy) && !isAutoSeededDummy) {
                    if (actDate == currentDate) {
                        val proofStartMs = details?.optLong("proof_window_start_ms", 0L).takeIf { (it ?: 0L) > 0L }
                            ?: details?.optLong("travel_end_ms", 0L).takeIf { (it ?: 0L) > 0L }
                            ?: parseIsoToMs(c.getString(6))
                        val reminderMs = proofStartMs - TravelBookingRules.PRE_OPENING_REMINDER_MS
                        val isDone = status in listOf("VERIFIED", "COMPLETED", "APPROVED", "SUBMITTED", "VALIDATING", "PENDING_SYNC", "MISSED", "EXPIRED", "CLOSED")
                        if (nowMs < reminderMs && !isDone) {
                            list.add(
                                DisciplineScoringRules.ScheduledActivityItem(
                                    c.getString(0), c.getString(1), c.getString(2), c.getString(3), c.getString(4),
                                    c.getString(5), c.getString(6), c.getString(7), c.getString(8), c.getString(9),
                                    c.getString(10), c.getInt(11), c.getString(12), raw, c.getString(14) ?: "",
                                    c.getInt(15), c.getInt(16)
                                )
                            )
                        }
                    } else {
                        list.add(
                            DisciplineScoringRules.ScheduledActivityItem(
                                c.getString(0), c.getString(1), c.getString(2), c.getString(3), c.getString(4),
                                c.getString(5), c.getString(6), c.getString(7), c.getString(8), c.getString(9),
                                c.getString(10), c.getInt(11), c.getString(12), raw, c.getString(14) ?: "",
                                c.getInt(15), c.getInt(16)
                            )
                        )
                    }
                }
            }
        }
        return list
    }

    fun isMoviePrebooked(date: String = nowDate()): Boolean {
        return try {
            readableDatabase.rawQuery(
                "SELECT 1 FROM movie_bookings WHERE show_date = ? AND status NOT IN ('Booking rejected', 'Booking cancelled', 'REJECTED', 'CANCELLED') LIMIT 1",
                arrayOf(date)
            ).use { it.moveToFirst() }
        } catch (_: Exception) {
            false
        }
    }

    fun getUpcomingMovieBookings(currentDate: String = nowDate()): List<JSONObject> {
        val list = mutableListOf<JSONObject>()
        try {
            readableDatabase.rawQuery(
                "SELECT movie_booking_id, movie_title, cinema_name, show_date, start_time, end_time, duration_minutes, lock_start_ms, lock_end_ms, lock_start_time, lock_end_time, status, validation_status, affected_slots_json, details_json FROM movie_bookings WHERE show_date >= ? AND status != 'Booking cancelled' ORDER BY show_date ASC, start_time ASC",
                arrayOf(currentDate)
            ).use { c ->
                while (c.moveToNext()) {
                    val nowMs = authoritativeNowMs()
                    val lockStartMs = c.getLong(7)
                    val lockEndMs = c.getLong(8)
                    val currentStatus = c.getString(11).orEmpty()
                    val authoritativeState = MovieBookingRules.resolveAuthoritativeState(nowMs, lockStartMs, lockEndMs, currentStatus)

                    list.add(JSONObject().apply {
                        put("booking_id", c.getString(0))
                        put("movie_title", c.getString(1))
                        put("cinema_name", c.getString(2))
                        put("show_date", c.getString(3))
                        put("start_time", c.getString(4))
                        put("end_time", c.getString(5))
                        put("duration_minutes", c.getInt(6))
                        put("lock_start_ms", lockStartMs)
                        put("lock_end_ms", lockEndMs)
                        put("lock_start_time", c.getString(9))
                        put("lock_end_time", c.getString(10))
                        put("status", authoritativeState)
                        put("validation_status", c.getString(12))
                        put("affected_slots", JSONArray(c.getString(13) ?: "[]"))
                        put("details", JSONObject(c.getString(14) ?: "{}"))
                    })
                }
            }
        } catch (_: Exception) {}
        return list
    }

    fun cancelMovieBooking(bookingId: String): JSONObject {
        var showDate = ""
        try {
            readableDatabase.rawQuery("SELECT show_date FROM movie_bookings WHERE movie_booking_id = ?", arrayOf(bookingId)).use {
                if (it.moveToFirst()) showDate = it.getString(0)
            }
            writableDatabase.execSQL(
                "UPDATE movie_bookings SET status = 'Booking cancelled', validation_status = 'CANCELLED', updated_at = ? WHERE movie_booking_id = ?",
                arrayOf(nowIso(), bookingId)
            )
            writableDatabase.execSQL(
                "UPDATE scheduled_activities SET status = 'CANCELLED' WHERE activity_id = ?",
                arrayOf(bookingId)
            )
            if (showDate.isNotBlank()) {
                reconcileHourlyMovieConflicts(showDate)
            }
            return JSONObject().put("success", true).put("message", "Movie booking cancelled. Held hourly attendance slots restored.")
        } catch (e: Exception) {
            return JSONObject().put("success", false).put("error", e.message ?: "Cancellation failed.")
        }
    }

    fun saveOfficialEventBooking(booking: JSONObject): JSONObject {
        val eventId = booking.optString("event_id").ifBlank { "evt_" + UUID.randomUUID().toString().replace("-", "") }
        val cv = ContentValues().apply {
            put("event_id", eventId)
            put("user_id", booking.optString("user_id", "user_lok"))
            put("title", booking.optString("title", "Official Event"))
            put("category", booking.optString("category", OfficialEventBookingRules.CAT_OTHER_OFFICIAL))
            put("organization", booking.optString("organization", ""))
            put("event_date", booking.optString("event_date", ""))
            put("start_time", booking.optString("start_time", ""))
            put("end_time", booking.optString("end_time", ""))
            put("expected_duration_minutes", booking.optInt("expected_duration_minutes", 0))
            put("time_zone", booking.optString("time_zone", "Asia/Kolkata"))
            put("event_mode", booking.optString("event_mode", OfficialEventBookingRules.MODE_IN_PERSON))
            put("venue_or_platform", booking.optString("venue_or_platform", ""))
            put("reporting_time", booking.optString("reporting_time", ""))
            put("shortlist_status", booking.optString("shortlist_status", OfficialEventBookingRules.SHORTLIST_CONFIRMED_INDIVIDUAL))
            put("evidence_paths", booking.optJSONArray("evidence_paths")?.toString() ?: "[]")
            put("evidence_sha256s", booking.optJSONArray("evidence_sha256s")?.toString() ?: "[]")
            put("user_entered_fields", booking.optJSONArray("user_entered_fields")?.toString() ?: "[]")
            put("status", booking.optString("status", OfficialEventBookingRules.STATE_CONFIRMED))
            put("exit_consumed", if (booking.optBoolean("exit_consumed", false)) 1 else 0)
            put("exit_consumed_at", booking.optString("exit_consumed_at", null))
            put("exit_session_id", booking.optString("exit_session_id", null))
            put("version", booking.optLong("version", 1L))
            put("device_id", booking.optString("device_id", getStableDeviceId(appContext)))
            put("created_at", booking.optString("created_at", nowIso()))
            put("updated_at", nowIso())
        }
        writableDatabase.insertWithOnConflict("official_event_bookings", null, cv, SQLiteDatabase.CONFLICT_REPLACE)

        // Queue sync to Windows EXE
        val syncPayload = OfficialEventBookingRules.buildSyncPayload(booking)
        enqueueSyncEvent("OFFICIAL_EVENT_BOOKING", syncPayload, appContext)

        return JSONObject().put("success", true).put("event_id", eventId)
    }

    fun listOfficialEventBookings(): JSONArray {
        val arr = JSONArray()
        try {
            readableDatabase.rawQuery("SELECT * FROM official_event_bookings ORDER BY event_date ASC, start_time ASC", null).use { c ->
                while (c.moveToNext()) {
                    val obj = JSONObject()
                    for (i in 0 until c.columnCount) {
                        val col = c.getColumnName(i)
                        when (col) {
                            "expected_duration_minutes", "exit_consumed", "version" -> obj.put(col, c.getLong(i))
                            "evidence_paths", "evidence_sha256s", "user_entered_fields" -> {
                                try { obj.put(col, JSONArray(c.getString(i))) } catch (_: Exception) { obj.put(col, JSONArray()) }
                            }
                            else -> obj.put(col, c.getString(i) ?: "")
                        }
                    }
                    arr.put(obj)
                }
            }
        } catch (_: Exception) {}
        return arr
    }

    fun cancelOfficialEventBooking(eventId: String, reason: String = "Cancelled by user"): JSONObject {
        try {
            writableDatabase.execSQL(
                "UPDATE official_event_bookings SET status = 'CANCELLED', updated_at = ? WHERE event_id = ?",
                arrayOf(nowIso(), eventId)
            )
            enqueueSyncEvent("OFFICIAL_EVENT_CANCEL", JSONObject().apply {
                put("event_id", eventId)
                put("reason", reason)
                put("timestamp", nowIso())
            }, appContext)
            return JSONObject().put("success", true).put("event_id", eventId)
        } catch (e: Exception) {
            return JSONObject().put("success", false).put("error", e.message ?: "Cancel failed")
        }
    }

    fun analyzeMovieTicketScreenshot(
        ticketBytes: ByteArray,
        ticketName: String,
        fallbackDate: String? = null,
        isDemo: Boolean = false
    ): JSONObject {
        val nowMs = authoritativeNowMs()
        val tz = TimeZone.getTimeZone(scheduleTimezone())

        if (ticketBytes.isEmpty()) {
            return JSONObject().put("success", false).put("error", "A valid movie ticket screenshot is required.")
        }
        if (ticketBytes.size > 25 * 1024 * 1024) {
            return JSONObject().put("success", false).put("error", "Ticket image exceeds the 25 MB limit.")
        }

        // 1. Inspect image locally for a QR-code pattern before OCR, validation, scheduling, or persistence
        val qrResult = MovieQrPresenceDetector.detectQrPresence(ticketBytes)
        if (qrResult.status == MovieQrPresenceDetector.QrPresenceStatus.DETECTED) {
            return JSONObject().apply {
                put("success", false)
                put("qr_detected", true)
                put("qr_rejected", true)
                put("status", MovieBookingRules.STATE_BOOKING_REJECTED)
                put("error", MovieQrPresenceDetector.REJECTION_MESSAGE)
                put("message", MovieQrPresenceDetector.REJECTION_MESSAGE)
            }
        }
        if (qrResult.status == MovieQrPresenceDetector.QrPresenceStatus.UNCERTAIN) {
            return JSONObject().apply {
                put("success", false)
                put("qr_uncertain", true)
                put("status", MovieBookingRules.STATE_NEEDS_REVIEW)
                put("error", MovieQrPresenceDetector.UNCERTAIN_MESSAGE)
                put("message", MovieQrPresenceDetector.UNCERTAIN_MESSAGE)
            }
        }

        val fileSha = DisciplineScoringRules.computeSha256Hex(ticketBytes)
        if (!isDemo) {
            val duplicateBooking = readableDatabase.rawQuery(
                "SELECT movie_booking_id, show_date, movie_title FROM movie_bookings WHERE file_sha256 = ? AND status NOT IN ('Booking rejected', 'Booking cancelled', 'REJECTED', 'CANCELLED') LIMIT 1",
                arrayOf(fileSha)
            ).use {
                if (it.moveToFirst()) Triple(it.getString(0), it.getString(1), it.getString(2)) else null
            }
            if (duplicateBooking != null) {
                return JSONObject().put("success", false)
                    .put("duplicate_detected", true)
                    .put("status", MovieBookingRules.STATE_BOOKING_REJECTED)
                    .put("error", "Duplicate ticket: This exact movie ticket screenshot was already submitted for ${duplicateBooking.third} (${duplicateBooking.second}).")
            }
        }

        // 2. Real OCR extraction
        var rawOcrText = ""
        readableDatabase.rawQuery("SELECT extracted_text FROM ocr_cache WHERE sha256_hash = ?", arrayOf(fileSha)).use {
            if (it.moveToFirst()) rawOcrText = it.getString(0).orEmpty()
        }
        if (rawOcrText.isBlank()) {
            try {
                val bmp = BitmapFactory.decodeByteArray(ticketBytes, 0, ticketBytes.size)
                if (bmp != null) {
                    val recognizer = com.google.mlkit.vision.text.TextRecognition.getClient(com.google.mlkit.vision.text.latin.TextRecognizerOptions.DEFAULT_OPTIONS)
                    val img = com.google.mlkit.vision.common.InputImage.fromBitmap(bmp, 0)
                    val task = recognizer.process(img)
                    val visionText = com.google.android.gms.tasks.Tasks.await(task, 8000L, java.util.concurrent.TimeUnit.MILLISECONDS)
                    rawOcrText = visionText.text
                    bmp.recycle()
                    if (!isDemo) {
                        val cvCache = ContentValues().apply {
                            put("sha256_hash", fileSha)
                            put("status", "OCR_COMPLETED")
                            put("extracted_text", rawOcrText)
                            put("confidence", 1.0)
                            put("engine", "mlkit-latin")
                            put("computed_at", nowIso())
                        }
                        writableDatabase.insertWithOnConflict("ocr_cache", null, cvCache, SQLiteDatabase.CONFLICT_REPLACE)
                    }
                }
            } catch (_: Exception) {}
        }

        // 3. Ticket parsing & Date validation
        val ticketInfo = MovieBookingRules.parseMovieTicketOcr(rawOcrText, fallbackDate, tz, nowMs)
        val targetShowDate = ticketInfo.showDate.ifBlank { fallbackDate ?: nowDate() }
        val startTag = if (ticketInfo.startTime.isNotBlank()) ticketInfo.startTime.replace(":", "") else "0000"
        val bookingId = "MBK-${targetShowDate.replace("-", "")}-$startTag-${ticketInfo.movieTitle.replace(Regex("[^A-Za-z0-9]"), "").take(8)}"
        val actId = "ACT-${targetShowDate.replace("-", "")}-$startTag-MOVIE"

        // 4. Check Schedule Conflicts
        var conflictReason = ""
        var hasConflict = false
        if (ticketInfo.reservationStartMs > 0L && ticketInfo.reservationEndMs > ticketInfo.reservationStartMs) {
            val travelIntervals = mutableListOf<Pair<Long, Long>>()
            readableDatabase.rawQuery(
                "SELECT scheduled_time, window_start, window_end, details_json FROM scheduled_activities WHERE date = ? AND type = 'TRAVEL' AND status != 'CANCELLED'",
                arrayOf(targetShowDate)
            ).use { c ->
                while (c.moveToNext()) {
                    val det = runCatching { JSONObject(c.getString(3) ?: "{}") }.getOrDefault(JSONObject())
                    val dMs = det.optLong("departure_ms", 0L).takeIf { it > 0L } ?: parseIsoToMs(c.getString(0))
                    val eMs = det.optLong("travel_end_ms", 0L).takeIf { it > 0L } ?: (dMs + 3 * 3600_000L)
                    if (dMs > 0L && eMs > dMs) travelIntervals.add(Pair(dMs, eMs))
                }
            }
            val otherMovieIntervals = mutableListOf<Pair<Long, Long>>()
            readableDatabase.rawQuery(
                "SELECT lock_start_ms, lock_end_ms FROM movie_bookings WHERE show_date = ? AND movie_booking_id != ? AND status NOT IN ('Booking rejected', 'Booking cancelled')",
                arrayOf(targetShowDate, actId)
            ).use { c ->
                while (c.moveToNext()) {
                    otherMovieIntervals.add(Pair(c.getLong(0), c.getLong(1)))
                }
            }
            val conflict = MovieBookingRules.checkScheduleConflicts(
                lockStartMs = ticketInfo.reservationStartMs,
                lockEndMs = ticketInfo.reservationEndMs,
                existingTravelIntervals = travelIntervals,
                existingMovieIntervals = otherMovieIntervals
            )
            if (conflict.hasConflict) {
                hasConflict = true
                conflictReason = conflict.conflictReason
            }
        }

        val isValidForConfirmation = ticketInfo.validationStatus == MovieBookingRules.STATE_MOVIE_BOOKING_CONFIRMED && !hasConflict

        val resultJson = JSONObject().apply {
            put("success", isValidForConfirmation)
            put("is_demo", isDemo)
            put("qr_detected", false)
            put("qr_rejected", false)
            put("qr_uncertain", false)
            put("movie_booking_id", bookingId)
            put("activity_id", actId)
            put("status", if (hasConflict) MovieBookingRules.STATE_BOOKING_REJECTED else ticketInfo.validationStatus)
            put("movie_title", ticketInfo.movieTitle)
            put("cinema_name", ticketInfo.cinemaName)
            put("show_date", targetShowDate)
            put("ticket_date_raw", ticketInfo.ticketDateRaw)
            put("year_omitted_in_ticket", ticketInfo.yearOmittedInTicket)
            put("intended_date", fallbackDate.orEmpty())
            put("is_date_mismatch", ticketInfo.isDateMismatch)
            put("is_expired", ticketInfo.isExpired)
            put("start_time", ticketInfo.startTime)
            put("end_time", ticketInfo.endTime ?: "")
            put("is_approximate_end_time", ticketInfo.isApproximateEndTime)
            put("duration_minutes", ticketInfo.durationMinutes)
            put("duration_established_reliably", ticketInfo.durationEstablishedReliably)
            put("duration_source", ticketInfo.durationSource)
            put("ticket_quantity", ticketInfo.ticketQuantity ?: 0)
            put("screen", ticketInfo.screen)
            put("seat", ticketInfo.seat)
            put("booking_code", ticketInfo.bookingCode)
            put("booking_ref", ticketInfo.bookingRef)
            put("language", ticketInfo.language)
            put("format", ticketInfo.format)
            put("rating", ticketInfo.rating)
            put("reservation_start_time", ticketInfo.reservationStartTime)
            put("reservation_end_time", ticketInfo.reservationEndTime)
            put("reservation_start_ms", ticketInfo.reservationStartMs)
            put("reservation_end_ms", ticketInfo.reservationEndMs)
            put("proof_window_open_time", ticketInfo.proofWindowOpenTime)
            put("proof_deadline_time", ticketInfo.proofDeadlineTime)
            put("proof_window_open_ms", ticketInfo.proofWindowOpenMs)
            put("proof_deadline_ms", ticketInfo.proofDeadlineMs)
            put("affected_slots", JSONArray(ticketInfo.affectedHourlySlots))
            put("has_schedule_conflict", hasConflict)
            put("conflict_reason", conflictReason)
            put("can_confirm", isValidForConfirmation)
            put("message", if (hasConflict) conflictReason else ticketInfo.validationMessage)
            put("file_sha256", fileSha)
            put("ticket_name", ticketName)
            put("ticket_info", ticketInfo.toJson())
        }

        return resultJson
    }

    fun confirmMovieBooking(
        analysisJson: JSONObject,
        ticketBytes: ByteArray? = null,
        isDemo: Boolean = false
    ): JSONObject {
        if (isDemo || analysisJson.optBoolean("is_demo", false)) {
            return JSONObject().apply {
                put("success", true)
                put("is_demo", true)
                put("message", "Demo movie analysis completed — no points, attendance holds, or production records changed.")
            }
        }

        if (!analysisJson.optBoolean("can_confirm", false)) {
            return JSONObject().apply {
                put("success", false)
                put("error", analysisJson.optString("message", "Cannot confirm an unvalidated movie booking."))
            }
        }

        val targetShowDate = analysisJson.optString("show_date")
        val actId = analysisJson.optString("activity_id")
        val bookingId = analysisJson.optString("movie_booking_id").ifBlank { actId }
        val movieTitle = analysisJson.optString("movie_title", "Movie")
        val cinemaName = analysisJson.optString("cinema_name")
        val startTime = analysisJson.optString("start_time")
        val endTime = analysisJson.optString("end_time")
        val durationMinutes = analysisJson.optInt("duration_minutes", 0)
        val fileSha = analysisJson.optString("file_sha256")
        val ticketName = analysisJson.optString("ticket_name", "ticket.jpg")
        val lockStartMs = analysisJson.optLong("reservation_start_ms", 0L)
        val lockEndMs = analysisJson.optLong("reservation_end_ms", 0L)
        val lockStartTime = analysisJson.optString("reservation_start_time")
        val lockEndTime = analysisJson.optString("reservation_end_time")
        val proofOpenMs = analysisJson.optLong("proof_window_open_ms", 0L)
        val proofDeadMs = analysisJson.optLong("proof_deadline_ms", 0L)
        val affectedSlots = analysisJson.optJSONArray("affected_slots") ?: JSONArray()

        // Save ticket evidence file if bytes provided
        val ext = "." + ticketName.substringAfterLast(".", "jpg").lowercase(Locale.US)
        val evidenceId = "ev_movie_${java.util.UUID.randomUUID().toString().replace("-", "").take(12)}"
        val proofsDir = File(appContext.filesDir, "proofs").apply { mkdirs() }
        val targetFile = File(proofsDir, "$evidenceId$ext")
        if (ticketBytes != null && ticketBytes.isNotEmpty()) {
            try {
                targetFile.writeBytes(ticketBytes)
            } catch (_: Exception) {}
        }

        val detailsJson = analysisJson.toString()

        val cvBooking = ContentValues().apply {
            put("movie_booking_id", bookingId)
            put("account_id", "user_lok")
            put("date", targetShowDate)
            put("movie_title", movieTitle)
            put("cinema_name", cinemaName)
            put("show_date", targetShowDate)
            put("start_time", startTime)
            put("end_time", endTime)
            put("duration_minutes", durationMinutes)
            put("lock_start_ms", lockStartMs)
            put("lock_end_ms", lockEndMs)
            put("lock_start_time", lockStartTime)
            put("lock_end_time", lockEndTime)
            put("status", MovieBookingRules.STATE_MOVIE_BOOKING_CONFIRMED)
            put("validation_status", "VERIFIED")
            put("ticket_evidence_id", evidenceId)
            put("file_path", targetFile.absolutePath)
            put("file_sha256", fileSha)
            put("affected_slots_json", affectedSlots.toString())
            put("details_json", detailsJson)
            put("created_at", nowIso())
            put("updated_at", nowIso())
        }
        writableDatabase.insertWithOnConflict("movie_bookings", null, cvBooking, SQLiteDatabase.CONFLICT_REPLACE)

        // Scheduled activity for movie attendance
        val cvAct = ContentValues().apply {
            put("activity_id", actId)
            put("account_id", "user_lok")
            put("type", "MOVIE")
            put("date", targetShowDate)
            val cinemaLabel = if (cinemaName.isNotBlank()) cinemaName else "Cinema"
            put("title", "Movie: $movieTitle ($cinemaLabel)")
            put("scheduled_time", if (lockStartMs > 0L) formatMsToIso(lockStartMs + MovieBookingRules.BUFFER_BEFORE_START_MS) else nowIso())
            put("window_start", formatMsToIso(lockStartMs))
            put("window_end", formatMsToIso(lockEndMs))
            put("notification_time", formatMsToIso(lockStartMs))
            put("status", "CONFIRMED")
            put("points", 0)
            put("configured_points", 0)
            put("validation_rule", "MOVIE_TICKET_OCR")
            put("details_json", detailsJson)
        }
        writableDatabase.insertWithOnConflict("scheduled_activities", null, cvAct, SQLiteDatabase.CONFLICT_REPLACE)

        // Also schedule the movie proof submission activity occurrence if proof window is known
        if (proofOpenMs > 0L && proofDeadMs > proofOpenMs) {
            val proofActId = "ACT-${targetShowDate.replace("-", "")}-${startTime.replace(":", "")}-MOVIE_PROOF"
            val cvProofAct = ContentValues().apply {
                put("activity_id", proofActId)
                put("account_id", "user_lok")
                put("type", "MOVIE_PROOF")
                put("date", targetShowDate)
                put("title", "Movie Proof: $movieTitle")
                put("scheduled_time", formatMsToIso(proofOpenMs))
                put("window_start", formatMsToIso(proofOpenMs))
                put("window_end", formatMsToIso(proofDeadMs))
                put("notification_time", formatMsToIso(proofOpenMs))
                put("status", "SCHEDULED")
                put("points", 0)
                put("configured_points", 0)
                put("validation_rule", "MOVIE_TICKET_OCR")
                put("details_json", detailsJson)
            }
            writableDatabase.insertWithOnConflict("scheduled_activities", null, cvProofAct, SQLiteDatabase.CONFLICT_REPLACE)
        }

        reconcileHourlyMovieConflicts(targetShowDate)

        enqueueSyncEvent("MOVIE_PREBOOKED", JSONObject().apply {
            put("activity_id", actId)
            put("movie_booking_id", bookingId)
            put("movie_title", movieTitle)
            put("cinema_name", cinemaName)
            put("show_date", targetShowDate)
            put("start_time", startTime)
            put("end_time", endTime)
            put("lock_start", lockStartTime)
            put("lock_end", lockEndTime)
            put("proof_window_open", analysisJson.optString("proof_window_open_time"))
            put("proof_deadline", analysisJson.optString("proof_deadline_time"))
            put("status", MovieBookingRules.STATE_MOVIE_BOOKING_CONFIRMED)
        }, appContext)

        return JSONObject().put("success", true)
            .put("movie_booking_id", bookingId)
            .put("activity_id", actId)
            .put("status", MovieBookingRules.STATE_MOVIE_BOOKING_CONFIRMED)
            .put("movie_title", movieTitle)
            .put("cinema_name", cinemaName)
            .put("show_date", targetShowDate)
            .put("start_time", startTime)
            .put("end_time", endTime)
            .put("duration_minutes", durationMinutes)
            .put("lock_start_time", lockStartTime)
            .put("lock_end_time", lockEndTime)
            .put("proof_window_open_time", analysisJson.optString("proof_window_open_time"))
            .put("proof_deadline_time", analysisJson.optString("proof_deadline_time"))
            .put("affected_slots", affectedSlots)
            .put("message", "Movie booking confirmed for $movieTitle ($startTime - $endTime). Attendance locked from $lockStartTime to $lockEndTime. ${affectedSlots.length()} hourly check-ins excused.")
    }

    fun prebookMovieTicket(
        ticketBytes: ByteArray,
        ticketName: String,
        fallbackDate: String? = null
    ): JSONObject {
        val analysis = analyzeMovieTicketScreenshot(ticketBytes, ticketName, fallbackDate, isDemo = false)
        if (!analysis.optBoolean("success", false)) {
            return analysis
        }
        return confirmMovieBooking(analysis, ticketBytes, isDemo = false)
    }

    fun getDailyActivities(date: String = nowDate()): List<DisciplineScoringRules.ScheduledActivityItem> {
        val count = readableDatabase.rawQuery("SELECT COUNT(1) FROM scheduled_activities WHERE date = ?", arrayOf(date)).use {
            if (it.moveToFirst()) it.getInt(0) else 0
        }
        if (count == 0) {
            ensureDailyActivities(date)
        }
        if (android.os.Looper.myLooper() != android.os.Looper.getMainLooper()) {
            reconcileHourlyBathConflicts(date)
        } else {
            bathAlarmWorker.execute { runCatching { reconcileHourlyBathConflicts(date) } }
        }
        val nowMs = authoritativeNowMs()
        val list = mutableListOf<DisciplineScoringRules.ScheduledActivityItem>()
        // The queue is the same for every activity. Parse it once per read,
        // rather than performing an identical query and JSON scan for each row.
        val pendingOfflineIds = readableDatabase.rawQuery("SELECT payload_json FROM sync_events_queue WHERE event_type IN ('HOURLY_PROOF_SUBMITTED','ACTIVITY_PROOF_SUBMITTED') AND sync_status='PENDING'", null).use { pending ->
            val ids = mutableSetOf<String>()
            while (pending.moveToNext()) {
                val payload = runCatching { JSONObject(pending.getString(0)) }.getOrDefault(JSONObject())
                if (payload.optBoolean("validated_offline_window")) ids.add(payload.optString("activity_id"))
            }
            ids
        }
        val isCollegeDay = getCollegeAttendance(date) == "YES"
        val approvedReviewActivityIds = mutableSetOf<String>()
        var wakeApprovedReview = false
        readableDatabase.rawQuery(
            "SELECT activity_id, task_code FROM proof_reviews WHERE date = ? AND status IN ('APPROVED', 'VERIFIED')",
            arrayOf(date)
        ).use { revCursor ->
            while (revCursor.moveToNext()) {
                val actId = revCursor.getString(0) ?: ""
                val taskCode = revCursor.getString(1) ?: ""
                if (actId.isNotBlank()) approvedReviewActivityIds.add(actId)
                if (taskCode in listOf("WAKE_UP", "WAKEUP")) wakeApprovedReview = true
            }
        }

        val validEvaluationActivityIds = mutableSetOf<String>()
        readableDatabase.rawQuery(
            "SELECT activity_id FROM proof_evaluations WHERE date = ? AND status != 'REJECTED'",
            arrayOf(date)
        ).use { evalCursor ->
            while (evalCursor.moveToNext()) {
                val actId = evalCursor.getString(0) ?: ""
                if (actId.isNotBlank()) validEvaluationActivityIds.add(actId)
            }
        }

        val c = readableDatabase.rawQuery(
            "SELECT activity_id, account_id, type, date, title, scheduled_time, window_start, window_end, notification_time, status, submitted_at, points, validation_rule, details_json, schedule_id, schedule_version, configured_points FROM scheduled_activities WHERE date = ? ORDER BY scheduled_time ASC",
            arrayOf(date)
        )
        c.use {
            while (it.moveToNext()) {
                var act = DisciplineScoringRules.ScheduledActivityItem(
                    it.getString(0), it.getString(1), it.getString(2), it.getString(3),
                    it.getString(4), it.getString(5), it.getString(6), it.getString(7),
                    it.getString(8), it.getString(9), if (it.isNull(10)) null else it.getString(10),
                    it.getInt(11), it.getString(12), it.getString(13), it.getString(14) ?: "", it.getInt(15), it.getInt(16)
                )
                var wStartMs = parseIsoToMs(act.windowStart)
                var wEndMs = parseIsoToMs(act.windowEnd)
                var wNotifMs = parseIsoToMs(act.notificationTime)

                if (act.type == "TRAVEL") {
                    val details = runCatching { JSONObject(act.detailsJson) }.getOrNull()
                    val proofStartMs = details?.optLong("proof_window_start_ms", 0L).takeIf { (it ?: 0L) > 0L }
                        ?: details?.optLong("travel_end_ms", 0L).takeIf { (it ?: 0L) > 0L }
                        ?: 0L
                    val proofEndMs = details?.optLong("proof_window_end_ms", 0L).takeIf { (it ?: 0L) > 0L }
                        ?: if (proofStartMs > 0L) proofStartMs + (2 * 3600_000L) else 0L
                    if (proofStartMs > 0L && (wStartMs != proofStartMs || wEndMs != proofEndMs)) {
                        wStartMs = proofStartMs
                        val targetEndMs = if (proofEndMs > 0L) proofEndMs else wEndMs
                        wEndMs = targetEndMs
                        wNotifMs = proofStartMs - TravelBookingRules.PRE_OPENING_REMINDER_MS
                        val cStartIso = formatMsToIso(wStartMs)
                        val cEndIso = formatMsToIso(targetEndMs)
                        val cNotifIso = formatMsToIso(wNotifMs)
                        writableDatabase.execSQL(
                            "UPDATE scheduled_activities SET window_start = ?, window_end = ?, notification_time = ? WHERE activity_id = ?",
                            arrayOf(cStartIso, cEndIso, cNotifIso, act.activityId)
                        )
                        act = act.copy(windowStart = cStartIso, windowEnd = cEndIso, notificationTime = cNotifIso)
                    }
                }

                // Bath timing is read from its saved occurrence. Configuration edits update it only through ensureBathOccurrence.
                if (act.type == "HOURLY" && act.status == "EXCUSED" &&
                    runCatching { JSONObject(act.detailsJson).optBoolean("suppressed_by_bath",false) }.getOrDefault(false)) {
                    act.submissionEligible = false
                    act.eligibilityReason = "Excused — Scheduled Bath Activity"
                    act.points = 0
                    list.add(act)
                    continue
                }
                val pendingOffline = act.activityId in pendingOfflineIds
                if (pendingOffline && act.status !in listOf("VERIFIED", "COMPLETED", "REJECTED", "SUBMITTED", "VALIDATING")) {
                    act.status = "PENDING_SYNC"
                    list.add(act)
                    continue
                }
                if (act.type == "HOURLY") {
                    val actDetails = runCatching { JSONObject(act.detailsJson) }.getOrDefault(JSONObject())
                    val isRejected = act.status in listOf("REJECTED", "REJECTED_LATE") ||
                        actDetails.optString("ai_validation_state") == "REJECTED" ||
                        actDetails.optBoolean("submission_locked", false)
                    if (isRejected) {
                        act.status = "REJECTED"
                        act.points = 0
                        act.submissionEligible = false
                        act.eligibilityReason = actDetails.optString("rejection_reason").ifBlank { "Submission rejected & locked" }
                        list.add(act)
                        continue
                    }
                    val cat = actDetails.optString("category").ifBlank { actDetails.optString("activity_category").ifBlank { actDetails.optString("primary_activity") } }
                    val hasRealCategory = cat.isNotBlank() && !cat.equals("Unavailable", ignoreCase = true)
                    val hasEval = act.activityId in validEvaluationActivityIds
                    val hasApprovedReview = act.activityId in approvedReviewActivityIds
                    val hasValidProof = (actDetails.optString("submission_id").isNotBlank() || actDetails.optString("evidence_id").isNotBlank()) && hasRealCategory
                    val hasSub = hasApprovedReview || hasEval || (hasValidProof && !act.submittedAt.isNullOrBlank())

                    if (!hasSub) {
                        if (act.status in listOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL")) {
                            act.status = "SCHEDULED"
                        }
                    }
                    act.submissionEligible = act.status !in listOf("EXCUSED", "CANCELLED")
                    act.eligibilityReason = ""
                    if (act.status in listOf("BLOCKED_WAKEUP", "SKIPPED_PRE_GATE")) {
                        act.status = "SCHEDULED"
                    }
                    if (!hasSub && act.status !in listOf("EXCUSED", "CANCELLED", "SKIPPED")) {
                        act.status = if (nowMs >= wEndMs) "MISSED" else if (nowMs >= wStartMs) "OPEN" else "SCHEDULED"
                    }
                    act.status = WindowStateRules.state(act.status, nowMs, wStartMs, wEndMs, act.submissionEligible)
                    if (nowMs >= wEndMs && !hasSub && act.status !in listOf("EXCUSED", "CANCELLED", "SKIPPED")) {
                        act.status = "MISSED"
                        act.points = 0
                        writableDatabase.execSQL("UPDATE scheduled_activities SET status = 'MISSED', points = 0 WHERE activity_id = ? AND status NOT IN ('MISSED', 'EXCUSED', 'CANCELLED', 'SKIPPED')", arrayOf(act.activityId))
                    }
                    list.add(act)
                    continue
                }

                if (act.type == "WAKE_UP" || act.activityId.contains("WAKEUP")) {
                    val flow = getWakeUpFlowStatus(date, false)
                    val allCompleted = flow.optBoolean("all_completed", false)
                    val dayWakeStatus = getDayState(date, false).optString("wakeup_status", "").uppercase(Locale.US)
                    val gateActive = getDailyRoutineGate(date).optBoolean("active", false)
                    val hasApprovedReview = wakeApprovedReview || act.activityId in approvedReviewActivityIds
                    val attemptId = flow.optString("attempt_id")
                    val hasPendingSync = if (attemptId.isNotBlank()) {
                        val evtId = "evt_wake_" + UUID.nameUUIDFromBytes(attemptId.toByteArray()).toString()
                        getSyncEventState(evtId).first in listOf("PENDING", "SYNCED")
                    } else false

                    val hasSub = hasApprovedReview || allCompleted || dayWakeStatus in setOf("PENDING_SERVER", "VERIFIED", "COMPLETED") || gateActive || hasPendingSync

                    if (!hasSub && act.status in listOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL")) {
                        act.status = "SCHEDULED"
                        if (nowMs >= wEndMs) {
                            act.status = "MISSED"
                            act.points = 0
                            writableDatabase.execSQL("UPDATE scheduled_activities SET status = 'MISSED', submitted_at = NULL, points = 0 WHERE activity_id = ?", arrayOf(act.activityId))
                        }
                    }
                    act.submissionEligible = act.status !in listOf("EXCUSED", "CANCELLED")
                    act.eligibilityReason = ""
                    if (!hasSub && act.status !in listOf("VERIFIED", "REJECTED", "REJECTED_LATE", "SKIPPED", "EXCUSED", "CANCELLED")) {
                        act.status = WindowStateRules.state("SCHEDULED", nowMs, wStartMs, wEndMs, act.submissionEligible)
                    }
                    if (nowMs >= wEndMs && !hasSub && act.status == "MISSED") {
                        writableDatabase.execSQL("UPDATE scheduled_activities SET status = 'MISSED', submitted_at = NULL, points = 0 WHERE activity_id = ? AND status != 'MISSED'", arrayOf(act.activityId))
                    }
                    list.add(act)
                    continue
                }

                if (act.status in listOf("VERIFIED", "REJECTED", "SUBMITTED", "VALIDATING", "CLOSED", "REJECTED_LATE", "MISSED", "SKIPPED", "EXCUSED", "CANCELLED", "SKIPPED_PRE_GATE")) {
                    list.add(act)
                    continue
                }

                if (nowMs >= wEndMs) {
                    act.status = "MISSED"
                    act.points = 0
                    writableDatabase.execSQL("UPDATE scheduled_activities SET status = 'MISSED', points = 0 WHERE activity_id = ?", arrayOf(act.activityId))
                } else if (nowMs >= wStartMs) {
                    if (act.status != "OPEN") {
                        act.status = "OPEN"
                        writableDatabase.execSQL("UPDATE scheduled_activities SET status = 'OPEN' WHERE activity_id = ?", arrayOf(act.activityId))
                    }
                } else if (nowMs >= wNotifMs) {
                    if (act.status == "SCHEDULED" || act.status == "UPCOMING") {
                        val reminderStatus = if (act.type in listOf("GYM", "TRAVEL")) "REMINDER_VISIBLE" else "NOTIFIED"
                        act.status = reminderStatus
                        writableDatabase.execSQL("UPDATE scheduled_activities SET status = ? WHERE activity_id = ?", arrayOf(reminderStatus, act.activityId))
                    }
                } else {
                    if (act.status != "SCHEDULED" && act.status != "UPCOMING") {
                        act.status = "UPCOMING"
                        writableDatabase.execSQL("UPDATE scheduled_activities SET status = 'UPCOMING' WHERE activity_id = ?", arrayOf(act.activityId))
                    }
                }
                list.add(act)
            }
        }
        val wakeUps = list.filter { it.type == "WAKE_UP" }
        if (wakeUps.size > 1) {
            val keep = wakeUps.firstOrNull { it.status in setOf("VERIFIED", "APPROVED", "COMPLETED") }
                ?: wakeUps.firstOrNull { it.status in setOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL") }
                ?: wakeUps.last()
            list.removeAll { it.type == "WAKE_UP" && it.activityId != keep.activityId }
        }

        if (!isCollegeDay) {
            val baths = list.filter { it.type == "BATH" || it.activityId.contains("BATH") }
            if (baths.size > 1) {
                val keep = baths.firstOrNull { it.status in setOf("VERIFIED", "APPROVED", "COMPLETED") }
                    ?: baths.firstOrNull { it.status in setOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL") }
                    ?: baths.firstOrNull { it.status == "OPEN" }
                    ?: baths.firstOrNull { it.status in setOf("SCHEDULED", "UPCOMING", "NOTIFIED") }
                    ?: baths.firstOrNull { it.status == "MISSED" }
                    ?: baths.first()
                list.removeAll { (it.type == "BATH" || it.activityId.contains("BATH")) && it.activityId != keep.activityId }
                // Keep duplicate historical rows in storage; only the display selects one occurrence.
            }
        } else {
            for (occ in 1..2) {
                val occBaths = list.filter {
                    (it.type == "BATH" || it.activityId.contains("BATH")) &&
                    (it.activityId.contains("BATH-$occ") || runCatching { JSONObject(it.detailsJson).optInt("occurrence") }.getOrDefault(0) == occ)
                }
                if (occBaths.size > 1) {
                    val keep = occBaths.firstOrNull { it.status in setOf("VERIFIED", "APPROVED", "COMPLETED") }
                        ?: occBaths.firstOrNull { it.status in setOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL") }
                        ?: occBaths.firstOrNull { it.status == "OPEN" }
                        ?: occBaths.firstOrNull { it.status in setOf("SCHEDULED", "UPCOMING", "NOTIFIED") }
                        ?: occBaths.firstOrNull { it.status == "MISSED" }
                        ?: occBaths.first()
                    list.removeAll { (it.type == "BATH" || it.activityId.contains("BATH")) && it.activityId in occBaths.map { b -> b.activityId } && it.activityId != keep.activityId }
                }
            }
        }
        return list
    }

    fun isWakeUpWindowOpen(nowMs: Long = authoritativeNowMs()): Boolean {
        val cal = Calendar.getInstance(TimeZone.getTimeZone("Asia/Kolkata"))
        cal.timeInMillis = nowMs
        val minuteOfDay = cal.get(Calendar.HOUR_OF_DAY) * 60 + cal.get(Calendar.MINUTE)
        return WindowStateRules.wakeUpWindowOpen(minuteOfDay)
    }

    fun evaluateSubmissionTiming(
        activityId: String,
        submissionTimeMs: Long = authoritativeNowMs(),
        recordLocally: Boolean = true
    ): DisciplineScoringRules.TimingDecision {
        val activityDate = readableDatabase.rawQuery("SELECT date FROM scheduled_activities WHERE activity_id = ? AND type = 'HOURLY'",arrayOf(activityId))
            .use { if(it.moveToFirst()) it.getString(0) else null }
        if (activityDate != null) reconcileHourlyBathConflicts(activityDate)
        val c = readableDatabase.rawQuery(
            "SELECT window_start, window_end, points, title, type, status, date, scheduled_time, details_json FROM scheduled_activities WHERE activity_id = ?",
            arrayOf(activityId)
        )
        if (!c.moveToFirst()) {
            c.close()
            return DisciplineScoringRules.TimingDecision(false, "ERROR", "Activity not found", activityId, "", "", 0)
        }
        val wStart = c.getString(0)
        val wEnd = c.getString(1)
        val activityType = c.getString(4)
        val existingStatus = c.getString(5) ?: "SCHEDULED"
        val occurrenceDate = c.getString(6)
        val scheduledHm = c.getString(7).substringAfter("T").take(5)
        val detailsJson = c.getString(8)
        c.close()

        val windowStartMs = parseIsoToMs(wStart)
        val windowEndMs = parseIsoToMs(wEnd)
        if (activityType == "HOURLY") {
            val details = runCatching { JSONObject(detailsJson ?: "{}") }.getOrDefault(JSONObject())
            if (existingStatus == "EXCUSED" && details.optBoolean("suppressed_by_bath",false)) {
                return DisciplineScoringRules.TimingDecision(false,"EXCUSED",
                    "Excused — Scheduled Bath Activity",activityId,wStart,wEnd,0)
            }
            if (existingStatus == "EXCUSED" && (details.optBoolean("suppressed_by_gym", false) || details.optString("reconciliation_status") == "EXCUSED_BY_GYM" || details.optBoolean("gym_excused", false))) {
                return DisciplineScoringRules.TimingDecision(
                    false, "EXCUSED",
                    "Hourly attendance is excused by verified Gym workout.",
                    activityId, wStart, wEnd, 0
                )
            }
            if (details.optBoolean("movie_hold", false) || (existingStatus == "EXCUSED" && details.optBoolean("suppressed_by_movie", false))) {
                return DisciplineScoringRules.TimingDecision(
                    false, "MOVIE_HOLD",
                    "Excused — confirmed movie reservation",
                    activityId, wStart, wEnd, 0
                )
            }
            if (details.optBoolean("travel_hold", false)) {
                return DisciplineScoringRules.TimingDecision(
                    false, "TRAVEL_HOLD",
                    "Hourly attendance is held for pre-booked travel. Submit travel proof to excuse this interval.",
                    activityId, wStart, wEnd, 0
                )
            }
            if (details.optBoolean("not_excused", false) || details.optBoolean("travel_proof_failed", false)) {
                return DisciplineScoringRules.TimingDecision(
                    false, "NOT_EXCUSED",
                    "Hourly attendance was not excused due to missed travel proof.",
                    activityId, wStart, wEnd, 0
                )
            }
            if (existingStatus in listOf("EXCUSED", "CANCELLED")) {
                return DisciplineScoringRules.TimingDecision(false, "EXCUSED", "Activity is excused or cancelled.", activityId, wStart, wEnd, 0)
            }
        }
        if (windowStartMs == Long.MIN_VALUE || windowEndMs == Long.MIN_VALUE || windowEndMs <= windowStartMs) {
            return DisciplineScoringRules.TimingDecision(false, "ERROR", "Activity window is invalid; submission was not accepted", activityId, wStart, wEnd, 0)
        }
        if (!DisciplineScoringRules.isSubmissionWithinWindow(submissionTimeMs, windowStartMs, windowEndMs)) {
            val late = submissionTimeMs >= windowEndMs
            val msg = if (activityType == "WAKE_UP") {
                if (late) "Wake-up submission window closed at 09:30 AM" else "Wake-up submission window opens at 07:00 AM"
            } else if (activityType == "GYM") {
                val startStr = ScheduleClock.display(windowStartMs, scheduleTimezone(), "h:mm a")
                val endStr = ScheduleClock.display(windowEndMs, scheduleTimezone(), "h:mm a")
                if (late) "Gym submission window closed at $endStr" else "Gym submission window opens at $startStr"
            } else if (activityType == "TRAVEL") {
                val startStr = ScheduleClock.display(windowStartMs, scheduleTimezone(), "h:mm a")
                val endStr = ScheduleClock.display(windowEndMs, scheduleTimezone(), "h:mm a")
                if (late) "Travel submission window closed at $endStr" else "Travel submission window opens at $startStr (arrival time)"
            } else if (activityType == "BATH" || activityId.contains("BATH")) {
                val startStr = ScheduleClock.display(windowStartMs, scheduleTimezone(), "h:mm a")
                val endStr = ScheduleClock.display(windowEndMs, scheduleTimezone(), "h:mm a")
                if (late) "Bath photo submission window closed at $endStr" else "Bath photo submission window opens at $startStr"
            } else {
                if (late) "Submission window has closed" else "Submission window has not opened"
            }
            return DisciplineScoringRules.TimingDecision(
                false,
                if (late) "REJECTED_LATE" else "TOO_EARLY",
                msg,
                activityId,
                wStart,
                wEnd,
                0
            )
        }

        // Within open window: closed/missed/late states cannot be submitted, but resubmissions during the open window are permitted
        if (existingStatus in listOf("MISSED", "CLOSED", "REJECTED_LATE")) {
            return DisciplineScoringRules.TimingDecision(false, "CLOSED", "Activity window has ended", activityId, wStart, wEnd, 0)
        }

        if (!recordLocally) {
            val message = if (hasFreshServerClock()) {
                "The submission window is open; the server will make the authoritative decision."
            } else {
                "The submission window is open locally; server synchronization pending."
            }
            return DisciplineScoringRules.TimingDecision(
                true, "OPEN", message,
                activityId, wStart, wEnd, 0
            )
        }

        val subIso = formatMsToIso(submissionTimeMs)
        if (existingStatus != "VERIFIED") {
            val values = ContentValues().apply {
                put("status", "SUBMITTED")
                put("submitted_at", subIso)
                put("points", 0)
            }
            writableDatabase.update("scheduled_activities", values, "activity_id = ?", arrayOf(activityId))
        } else {
            val values = ContentValues().apply {
                put("submitted_at", subIso)
            }
            writableDatabase.update("scheduled_activities", values, "activity_id = ?", arrayOf(activityId))
        }
        return DisciplineScoringRules.TimingDecision(true, "SUBMITTED", "Submission received; points remain 0 until proof is approved.", activityId, wStart, wEnd, 0)
    }

    fun recordActivityVerification(activityId: String, isVerified: Boolean, awardedPoints: Int, evidenceId: String? = null, provisional: Boolean = false) {
        StrictWidgetUpdates.request(appContext,"tasks")
        val approvedProof = isVerified && !evidenceId.isNullOrBlank() && readableDatabase.rawQuery(
            "SELECT 1 FROM proof_reviews WHERE evidence_id = ? AND status IN ('APPROVED', 'VERIFIED')",
            arrayOf(evidenceId)
        ).use { it.moveToFirst() }
        val status = when {
            approvedProof || (isVerified && !provisional) -> "VERIFIED"
            isVerified && provisional -> "AI_PROVISIONAL"
            else -> "REJECTED"
        }
        val pts = if (approvedProof || (isVerified && !provisional)) awardedPoints.coerceAtLeast(0) else 0
        val values = ContentValues().apply {
            put("status", status)
            put("points", pts)
        }
        writableDatabase.beginTransaction()
        try {
            val changed = writableDatabase.update(
                "scheduled_activities",
                values,
                "activity_id = ? AND status IN ('SCHEDULED','UPCOMING','NOTIFIED','OPEN','SUBMITTED','VALIDATING','AI_PROVISIONAL')",
                arrayOf(activityId)
            )
            if (changed > 0) {
                writableDatabase.execSQL(
                    "UPDATE scheduled_activities SET submitted_at = COALESCE(submitted_at, ?) WHERE activity_id = ?",
                    arrayOf(formatMsToIso(authoritativeNowMs()), activityId)
                )
            }
            writableDatabase.setTransactionSuccessful()
        } finally {
            writableDatabase.endTransaction()
        }
    }

    fun getCurrentCountdownState(date: String = nowDate(), types: Set<String>? = null): DisciplineScoringRules.CountdownState {
        val activities = getDailyActivities(date).filter { types == null || it.type in types }
        val nowMs = authoritativeNowMs()

        var openAct: DisciplineScoringRules.ScheduledActivityItem? = null
        var upcomingAct: DisciplineScoringRules.ScheduledActivityItem? = null

        for (act in activities) {
            if (act.status in listOf("EXCUSED", "CANCELLED", "SKIPPED_PRE_GATE")) continue
            if (act.type == "WAKE_UP" && getDailyRoutineGate(date).optBoolean("active")) continue
            val wStartMs = parseIsoToMs(act.windowStart)
            val wEndMs = parseIsoToMs(act.windowEnd)

            if (nowMs in wStartMs until wEndMs) {
                openAct = act
                break
            } else if (wStartMs > nowMs) {
                if (upcomingAct == null && act.status !in listOf("EXCUSED", "CANCELLED", "SKIPPED_PRE_GATE")) {
                    upcomingAct = act
                }
            }
        }

        if (openAct != null) {
            val remSec = maxOf(0L, (parseIsoToMs(openAct.windowEnd) - nowMs) / 1000L)
            val isExplicitlyRejected = openAct.status in listOf("REJECTED", "REJECTED_LATE") ||
                runCatching { JSONObject(openAct.detailsJson).optString("validation_status") == "REJECTED" }.getOrDefault(false) ||
                readableDatabase.rawQuery("SELECT 1 FROM proof_evaluations WHERE activity_id = ? AND status = 'REJECTED'", arrayOf(openAct.activityId)).use { it.moveToFirst() } ||
                readableDatabase.rawQuery("SELECT 1 FROM proof_reviews WHERE activity_id = ? AND (status = 'REJECTED' OR decision = 'REJECTED')", arrayOf(openAct.activityId)).use { it.moveToFirst() } ||
                runCatching { JSONObject(openAct.detailsJson).optString("ai_validation_state") == "REJECTED" }.getOrDefault(false)

            val isExplicitlyVerified = !isExplicitlyRejected && (
                openAct.status in listOf("VERIFIED", "APPROVED", "COMPLETED") ||
                runCatching { JSONObject(openAct.detailsJson).optString("validation_status") in listOf("APPROVED", "VERIFIED") }.getOrDefault(false) ||
                readableDatabase.rawQuery("SELECT 1 FROM proof_evaluations WHERE activity_id = ? AND status IN ('APPROVED', 'VERIFIED')", arrayOf(openAct.activityId)).use { it.moveToFirst() } ||
                readableDatabase.rawQuery("SELECT 1 FROM proof_reviews WHERE activity_id = ? AND (status IN ('APPROVED', 'VERIFIED') OR decision = 'APPROVED')", arrayOf(openAct.activityId)).use { it.moveToFirst() } ||
                runCatching { JSONObject(openAct.detailsJson).optString("ai_validation_state") in listOf("APPROVED", "VERIFIED") }.getOrDefault(false)
            )

            val hasRealSubmission = !isExplicitlyRejected && (
                isExplicitlyVerified ||
                readableDatabase.rawQuery("SELECT 1 FROM proof_evaluations WHERE activity_id = ? AND status != 'REJECTED'", arrayOf(openAct.activityId)).use { it.moveToFirst() } ||
                readableDatabase.rawQuery("SELECT 1 FROM proof_reviews WHERE activity_id = ? AND (status IN ('APPROVED', 'VERIFIED') OR decision = 'APPROVED')", arrayOf(openAct.activityId)).use { it.moveToFirst() } ||
                readableDatabase.rawQuery("SELECT 1 FROM sync_events_queue WHERE payload_json LIKE ? AND sync_status = 'PENDING'", arrayOf("%${openAct.activityId}%")).use { it.moveToFirst() }
            )

            val rejectionReasonStr = if (isExplicitlyRejected) {
                var reason = readableDatabase.rawQuery(
                    "SELECT evaluator_output_json FROM proof_evaluations WHERE activity_id = ? ORDER BY created_at DESC LIMIT 1",
                    arrayOf(openAct.activityId)
                ).use { c ->
                    if (c.moveToFirst()) {
                        try {
                            val jo = JSONObject(c.getString(0) ?: "{}")
                            jo.optString("reason", "").ifBlank { jo.optString("rejection_reason", "") }
                        } catch (_: Exception) { "" }
                    } else ""
                }
                if (reason.isBlank()) {
                    reason = readableDatabase.rawQuery(
                        "SELECT reason_note FROM proof_reviews WHERE activity_id = ? AND (status = 'REJECTED' OR decision = 'REJECTED') ORDER BY reviewed_at DESC LIMIT 1",
                        arrayOf(openAct.activityId)
                    ).use { c -> if (c.moveToFirst()) c.getString(0) ?: "" else "" }
                }
                if (reason.isBlank()) {
                    try {
                        val jo = JSONObject(openAct.detailsJson ?: "{}")
                        reason = jo.optString("rejection_reason", "")
                    } catch (_: Exception) {}
                }
                reason.ifBlank { "Duplicate evidence detected (EXACT_DUPLICATE)" }
            } else null

            val resolvedState = when {
                isExplicitlyVerified -> "VERIFIED"
                isExplicitlyRejected -> "REJECTED"
                hasRealSubmission -> "SUBMITTED"
                else -> "OPEN"
            }
            return DisciplineScoringRules.CountdownState(
                hasActiveWindow = true,
                activityId = openAct.activityId,
                type = openAct.type,
                title = openAct.title,
                state = resolvedState,
                secondsRemaining = remSec,
                secondsUntilOpen = 0L,
                isWarning = remSec <= 120L,
                windowStart = openAct.windowStart,
                windowEnd = openAct.windowEnd,
                points = if (isExplicitlyRejected) 0 else if (openAct.points > 0) openAct.points else 8,
                submissionEligible = resolvedState !in listOf("VERIFIED", "SUBMITTED", "REJECTED"),
                rejectionReason = rejectionReasonStr
            )
        }

        if (upcomingAct != null) {
            val untilSec = maxOf(0L, (parseIsoToMs(upcomingAct.windowStart) - nowMs) / 1000L)
            return DisciplineScoringRules.CountdownState(
                hasActiveWindow = false,
                activityId = upcomingAct.activityId,
                type = upcomingAct.type,
                title = upcomingAct.title,
                state = upcomingAct.status,
                secondsRemaining = 0L,
                secondsUntilOpen = untilSec,
                isWarning = false,
                windowStart = upcomingAct.windowStart,
                windowEnd = upcomingAct.windowEnd,
                points = if (upcomingAct.points > 0) upcomingAct.points else 8
            )
        }

        return DisciplineScoringRules.CountdownState(
            hasActiveWindow = false,
            activityId = null,
            type = null,
            title = null,
            state = "ALL_CLOSED",
            secondsRemaining = 0L,
            secondsUntilOpen = 0L,
            isWarning = false,
            windowStart = null,
            windowEnd = null,
            points = 0
        )
    }

    fun getActivityById(activityId: String): DisciplineScoringRules.ScheduledActivityItem? {
        val date = readableDatabase.rawQuery("SELECT date FROM scheduled_activities WHERE activity_id=?", arrayOf(activityId)).use { c -> if (c.moveToFirst()) c.getString(0) else null } ?: return null
        return getDailyActivities(date).firstOrNull { it.activityId == activityId }
    }

    fun getActiveOrTargetHourlyActivity(date: String = nowDate()): DisciplineScoringRules.ScheduledActivityItem? {
        val allHourly = getDailyActivities(date).filter { it.type == "HOURLY" && it.status !in listOf("EXCUSED", "CANCELLED") }
        val nowMs = authoritativeNowMs()

        // 1. If any hourly slot has its window currently open, it remains the active slot until windowEnd.
        val openAct = allHourly.firstOrNull { act ->
            val s = parseIsoToMs(act.windowStart)
            val e = parseIsoToMs(act.windowEnd)
            nowMs in s until e
        }
        if (openAct != null) return openAct

        // 2. Otherwise find the next upcoming slot.
        val upcomingAct = allHourly.firstOrNull { act ->
            val s = parseIsoToMs(act.windowStart)
            nowMs < s
        }
        if (upcomingAct != null) return upcomingAct

        return allHourly.lastOrNull()
    }

    fun notificationKey(activityId: String, event: String, windowStart: String, windowEnd: String, type: String): String {
        val base="$activityId-$event"
        return if(type == "BATH") "$base:${parseIsoToMs(windowStart)}:${parseIsoToMs(windowEnd)}" else base
    }

    fun isNotificationSent(key: String): Boolean {
        readableDatabase.rawQuery("SELECT 1 FROM notification_history WHERE notification_key = ?", arrayOf(key)).use {
            return it.moveToFirst()
        }
    }

    fun markNotificationSent(key: String, activityId: String, eventType: String): Boolean {
        val cv = ContentValues().apply {
            put("notification_key", key)
            put("activity_id", activityId)
            put("event_type", eventType)
            put("sent_at", nowIso())
        }
        return writableDatabase.insertWithOnConflict("notification_history", null, cv, SQLiteDatabase.CONFLICT_IGNORE) != -1L
    }

    fun getPendingNotifications(date: String = nowDate()): List<JSONObject> {
        val activities = getDailyActivities(date)
        val nowMs = authoritativeNowMs()
        val pending = mutableListOf<JSONObject>()

        for (act in activities) {
            if(act.status in setOf("CANCELLED","SKIPPED","EXCUSED","SUBMITTED","VALIDATING","AI_PROVISIONAL","PENDING_SYNC","UNDER_REVIEW","VERIFIED","APPROVED","COMPLETED","REJECTED")) continue
            val savedBath = if (act.type == "BATH") runCatching { getSavedBathSchedule(act) }.getOrNull() else null
            // Hourly reminders come only from the server's authoritative scheduler.
            // Keeping local hourly notification generation here created duplicate
            // notifications when the registered-device stream was also connected.
            if (act.type.equals("HOURLY", ignoreCase = true)) continue
            val aid = act.activityId
            val wStartMs = parseIsoToMs(act.windowStart)
            val wEndMs = parseIsoToMs(act.windowEnd)
            val wNotifMs = parseIsoToMs(act.notificationTime)
            val wWarn1mMs = wEndMs - 60_000L

            if (nowMs in wNotifMs until wStartMs) {
                val key = notificationKey(aid,"OPENING",act.windowStart,act.windowEnd,act.type)
                if (markNotificationSent(key, aid, "OPENING_1M")) {
                    pending.add(JSONObject().apply {
                        put("key", key)
                        put("activity_id", aid)
                        put("title", "STRICTROUTINES SCHEDULE")
                        put("message", savedBath?.reminderMessage("OPENING") ?: "Next check opens in 1 minute. (${act.title})")
                    })
                }
            }
            if (nowMs in wStartMs until wEndMs && act.status == "OPEN") {
                val key = notificationKey(aid,"OPENED",act.windowStart,act.windowEnd,act.type)
                if (markNotificationSent(key, aid, "OPENED")) {
                    pending.add(JSONObject().apply {
                        put("key", key)
                        put("activity_id", aid)
                        put("title", "STRICTROUTINES WINDOW OPEN")
                        put("message", savedBath?.reminderMessage("OPENED") ?: "Your activity is now open. (${act.title})")
                    })
                }
            }
            if (nowMs in wWarn1mMs until wEndMs && act.status == "OPEN") {
                val key = notificationKey(aid,"WARN1M",act.windowStart,act.windowEnd,act.type)
                if (markNotificationSent(key, aid, "WARN_1M")) {
                    pending.add(JSONObject().apply {
                        put("key", key)
                        put("activity_id", aid)
                        put("title", "STRICTROUTINES WARNING")
                        put("message", savedBath?.reminderMessage("WARN1M") ?: "1 minute remaining in activity window!")
                    })
                }
            }
            if (nowMs >= wEndMs && (act.status == "MISSED" || act.status == "CLOSED" || act.status == "REJECTED_LATE")) {
                val key = notificationKey(aid,"CLOSED",act.windowStart,act.windowEnd,act.type)
                if (markNotificationSent(key, aid, "CLOSED")) {
                    pending.add(JSONObject().apply {
                        put("key", key)
                        put("activity_id", aid)
                        put("title", "STRICTROUTINES CLOSED")
                        put("message", savedBath?.reminderMessage("CLOSED") ?: "Activity window closed.")
                    })
                }
            }
        }
        return pending
    }

    fun logStructured(date: String, isDevMode: Boolean, code: String, details: String = "") {
        val cv = ContentValues().apply {
            put("date", date)
            put("is_dev_mode", if (isDevMode) 1 else 0)
            put("timestamp", nowIso())
            put("event_code", code)
            put("details", details)
        }
        writableDatabase.insert("structured_logs", null, cv)
    }

    internal fun hasValidRetainedProofImage(path: String): Boolean {
        return try {
            val file = File(path)
            if (!file.isFile || file.length() !in 100..(25L * 1024L * 1024L)) return false
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(path, bounds)
            if (bounds.outWidth < 100 || bounds.outHeight < 100 ||
                bounds.outWidth > 50_000 || bounds.outHeight > 50_000
            ) return false
            val mime = bounds.outMimeType
            if (!mime.isNullOrBlank() && mime.lowercase(Locale.US) !in setOf("image/jpeg", "image/png", "image/webp", "image/jpg")) return false

            val sample = BitmapFactory.Options().apply {
                inSampleSize = DisciplineScoringRules.calculateProofSampleSize(bounds.outWidth, bounds.outHeight, 512)
            }
            try {
                val decoded = BitmapFactory.decodeFile(path, sample)
                decoded?.recycle()
            } catch (_: Throwable) {}
            true
        } catch (_: Throwable) {
            false
        }
    }

    fun currentScheduleDate(): String = nowDate()

    fun getDailyRoutineGate(date: String = nowDate()): JSONObject = runCatching {
        JSONObject(getConfigValue("daily_routine_gate_$date", "{}"))
    }.getOrDefault(JSONObject().apply { put("active", false); put("state", "WAKE_UP_PENDING") })

    fun applyAuthoritativeRoutineGate(gate: JSONObject) {
        val date = gate.optString("date")
        if (!Regex("\\d{4}-\\d{2}-\\d{2}").matches(date)) return
        val previous = getDailyRoutineGate(date)
        if (gate.optInt("schedule_version", 1) < previous.optInt("schedule_version", 0)) return
        setConfigValue("daily_routine_gate_$date", gate.toString())
        if (gate.optBoolean("active") && gate.optJSONObject("checks")?.optBoolean("proof_approved") == true &&
            gate.optString("attempt_id") == getWakeUpAttemptId(date)) {
            val flow = getWakeUpFlowStatus(date)
            if (flow.optBoolean("all_completed")) writableDatabase.execSQL(
                "UPDATE daily_state SET wakeup_status='VERIFIED' WHERE date=? AND is_dev_mode=0", arrayOf(date))
        }
        reconcileHourlyWakeUpConflicts(date)
    }

    fun wakeKey(base: String, date: String, isDevMode: Boolean): String {
        val mode = if (isDevMode) "demo" else "real"
        return "${base}_${date}_$mode"
    }

    fun getWakeConfig(base: String, date: String, isDevMode: Boolean, default: String = ""): String {
        val modeVal = getConfigValue(wakeKey(base, date, isDevMode), "")
        if (modeVal.isNotBlank()) return modeVal
        if (!isDevMode) {
            val legacy = getConfigValue("${base}_$date", "")
            if (legacy.isNotBlank()) return legacy
        }
        return default
    }

    fun setWakeConfig(base: String, date: String, isDevMode: Boolean, value: String) {
        setConfigValue(wakeKey(base, date, isDevMode), value)
        if (!isDevMode) {
            setConfigValue("${base}_$date", value)
        }
    }

    fun resetWakeUpDemoStep(date: String = nowDate()) {
        val keyAttempt = "wakeup_attempt_${date}_demo"
        setConfigValue(keyAttempt, "")
        setWakeConfig("wakeup_liveness_metadata", date, true, "")
        setWakeConfig("wakeup_route_attempt", date, true, "")
        setWakeConfig("wakeup_bt_active_output", date, true, "0")
        setWakeConfig("wakeup_bt_route_type", date, true, "")
        setWakeConfig("wakeup_playback_telemetry", date, true, "")
        setWakeConfig("camera_save_error", date, true, "")
        setWakeConfig("wakeup_submission_error", date, true, "")
        writableDatabase.execSQL(
            "UPDATE daily_state SET wakeup_camera = 0, wakeup_camera_path = '', wakeup_bt = 0, wakeup_bt_device = '', wakeup_track = 0, wakeup_status = 'PENDING', wakeup_completed_at = '' WHERE date = ? AND is_dev_mode = 1",
            arrayOf(date)
        )
    }

    fun getWakeUpFlowStatus(date: String = nowDate(), isDevMode: Boolean = false): JSONObject {
        val st = getDayState(date, isDevMode)
        val attemptId = getWakeUpAttemptId(date, isDevMode)
        val routeAttempt = getWakeConfig("wakeup_route_attempt", date, isDevMode)
        val btActive = getWakeConfig("wakeup_bt_active_output", date, isDevMode, "0")
        val step2 = st.optInt("wakeup_bt", 0) == 1 && routeAttempt == attemptId && btActive == "1"
        val playback = runCatching { JSONObject(getWakeConfig("wakeup_playback_telemetry", date, isDevMode, "{}")) }.getOrDefault(JSONObject())
        val step3 = st.optInt("wakeup_track", 0) == 1 && playback.optString("attempt_id") == attemptId && playback.optBoolean("completed", false)
        val metaRaw = getWakeConfig("wakeup_liveness_metadata", date, isDevMode, "{}")
        val metaJson = runCatching { JSONObject(metaRaw) }.getOrDefault(JSONObject())
        val retainedPhoto = File(st.optString("wakeup_camera_path", ""))
        val retainedValid = hasValidRetainedProofImage(retainedPhoto.path)
        val fileSha = if (retainedValid) DisciplineScoringRules.computeSha256Hex(retainedPhoto) else ""
        if (retainedValid && fileSha.isNotBlank() && metaJson.optString("photo_sha256").isBlank()) {
            metaJson.put("photo_sha256", fileSha)
            setWakeConfig("wakeup_liveness_metadata", date, isDevMode, metaJson.toString())
        }
        val expectedSha = metaJson.optString("photo_sha256")
        val shaMatches = retainedValid && expectedSha.isNotBlank() && expectedSha.equals(fileSha, ignoreCase = true)
        val step1 = st.optInt("wakeup_camera", 0) == 1 && WakeUpCameraPolicy.validateMetadata(metaJson).passed &&
            retainedValid && shaMatches
        // Valid retained results from the earlier client can be associated with today's attempt.
        if (step1 && metaJson.optString("attempt_id").isBlank()) {
            metaJson.put("attempt_id", attemptId)
            setWakeConfig("wakeup_liveness_metadata", date, isDevMode, metaJson.toString())
        }
        val sameAttemptCamera = step1 && metaJson.optString("attempt_id") == attemptId
        val cameraError = when {
            st.optInt("wakeup_camera", 0) != 1 -> getWakeConfig("camera_save_error", date, isDevMode, "Camera verification has not completed")
            !WakeUpCameraPolicy.validateMetadata(metaJson).passed -> WakeUpCameraPolicy.validateMetadata(metaJson).reasons.joinToString("; ")
            !retainedPhoto.isFile -> "CAMERA_FILE_MISSING: retained camera proof was not found"
            !retainedValid -> "CAMERA_IMAGE_INVALID: retained proof file is unreadable or corrupt"
            !shaMatches -> "CAMERA_IMAGE_INVALID: retained image SHA-256 does not match accepted proof"
            !step1 -> "CAMERA_IMAGE_INVALID: retained image or SHA-256 does not match the accepted proof"
            !sameAttemptCamera -> "ATTEMPT_MISMATCH: camera proof belongs to another attempt"
            else -> ""
        }
        return JSONObject().apply {
            put("date", date)
            put("attempt_id", attemptId)
            put("step1_camera", sameAttemptCamera)
            put("camera_path", retainedPhoto.path)
            put("camera_error", cameraError)
            put("step2_bluetooth", step2)
            put("step3_motivation_track", step3)
            put("all_completed", sameAttemptCamera && step2 && step3)
            put("attendance_accepted", sameAttemptCamera && step2 && step3 && getDailyRoutineGate(date).optBoolean("active", false) &&
                getDailyRoutineGate(date).optString("attempt_id") == attemptId &&
                getDailyRoutineGate(date).optJSONObject("checks")?.optBoolean("proof_approved", false) == true)
            put("daily_routine_gate", getDailyRoutineGate(date))
            put("status", st.optString("wakeup_status", "PENDING"))
            put("completed_at", st.optString("wakeup_completed_at", ""))
            put("bt_device", st.optString("wakeup_bt_device", ""))
            put("liveness_metadata", metaJson)
        }
    }

    fun getWakeUpAttemptId(date: String, isDevMode: Boolean = false): String {
        val key = "wakeup_attempt_${date}_${if (isDevMode) "demo" else "real"}"
        return getConfigValue(key, "").ifBlank {
            "wake_android_${UUID.randomUUID()}".also { setConfigValue(key, it) }
        }
    }

    fun getConfiguredWakeUpTrackHash(): String {
        val custom = WakeUpMusicManager.getCurrentTrack(appContext)
        if (custom != null && custom.sha256.isNotBlank()) {
            return custom.sha256
        }
        return appContext.resources.openRawResource(R.raw.motivation_track).use {
            DisciplineScoringRules.computeSha256Hex(it.readBytes())
        }
    }

    fun recordVerifiedWakeUpAudioRoute(
        date: String,
        attemptId: String,
        deviceName: String,
        deviceAddress: String = "",
        routeTypeName: String = "BLUETOOTH_A2DP",
        isDevMode: Boolean = false
    ) {
        if (attemptId.isBlank() || attemptId != getWakeUpAttemptId(date, isDevMode)) return
        ensureDay(date, isDevMode)
        val dev = if (isDevMode) 1 else 0
        setWakeConfig("wakeup_route_attempt", date, isDevMode, attemptId)
        setWakeConfig("wakeup_bt_active_output", date, isDevMode, "1")
        setWakeConfig("wakeup_bt_route_type", date, isDevMode, routeTypeName)
        setWakeConfig("wakeup_bt_verified_device", date, isDevMode, deviceName)
        if (deviceAddress.isNotBlank()) {
            setWakeConfig("wakeup_bt_verified_address", date, isDevMode, deviceAddress)
        }
        val nowIso = formatMsToIso(authoritativeNowMs())
        setWakeConfig("wakeup_bt_verified_time", date, isDevMode, nowIso)
        setWakeConfig("wakeup_playback_attempt", date, isDevMode, attemptId)
        setWakeConfig("wakeup_playback_state", date, isDevMode, "PLAYING")
        setWakeConfig("wakeup_route_loss_reason", date, isDevMode, "")
        writableDatabase.execSQL("UPDATE daily_state SET wakeup_bt=1, wakeup_bt_device=? WHERE date=? AND is_dev_mode=?", arrayOf(deviceName, date, dev))
        android.util.Log.i("StrictWakeAudio", "route_verified source=MEDIA_PLAYER_ROUTED_DEVICE attempt=$attemptId isDevMode=$isDevMode device=$deviceName type=$routeTypeName")
    }

    fun invalidateWakeUpAudioRoute(date: String, isDevMode: Boolean = false, reason: String = "ROUTE_LOST") {
        setWakeConfig("wakeup_bt_active_output", date, isDevMode, "0")
        setWakeConfig("wakeup_playback_state", date, isDevMode, "ROUTE_LOST")
        setWakeConfig("wakeup_route_loss_reason", date, isDevMode, reason)
        ensureDay(date, isDevMode)
        val dev = if (isDevMode) 1 else 0
        writableDatabase.execSQL(
            "UPDATE daily_state SET wakeup_bt=0 WHERE date=? AND is_dev_mode=?",
            arrayOf(date, dev)
        )
        android.util.Log.w("StrictWakeAudio", "route_invalidated reason=$reason attempt=${getWakeUpAttemptId(date, isDevMode)} isDevMode=$isDevMode")
    }

    fun addScoreEvent(
        date: String,
        isDevMode: Boolean,
        eventType: String,
        description: String,
        points: Int,
        audit: JSONObject
    ) {
        val evidenceId = audit.optString("evidence_id").takeIf { it.isNotBlank() }
        if (points > 0) {
            val approved = evidenceId != null && hasApprovedRetainedProof(evidenceId)
            if (!DisciplineScoringRules.canAwardPositiveProofPoints(eventType, approved)) return
            val wakeUpProof = readableDatabase.rawQuery("SELECT task_code FROM proof_reviews WHERE evidence_id=?", arrayOf(evidenceId)).use {
                it.moveToFirst() && it.getString(0).uppercase(Locale.US) in listOf("WAKE_UP", "WAKEUP")
            }
            if (wakeUpProof && (isDevMode || !getWakeUpFlowStatus(date).optBoolean("attendance_accepted"))) return
        }

        if (points < 0) {
            val approvalSrc = audit.optString("approval_source")
            val approvedBy = audit.optString("approved_by").ifBlank { audit.optString("Admin") }
            val isCompensating = audit.optBoolean("is_compensating_reconciliation", false) || eventType == "RECONCILIATION_COMPENSATION"
            val isAuthorized = isCompensating || (
                (approvalSrc in listOf("ANDROID_HUMAN_CONFIRMATION", "AUTHENTICATED_HUMAN_ADMIN", "ANDROID_ACCESSIBILITY_ENFORCEMENT")) &&
                approvedBy.isNotBlank()
            )
            if (!isAuthorized) {
                // Section 1 & 2: Secondary client or unapproved negative point writes are rejected at the data layer
                // and converted into a pending deduction proposal with zero point impact.
                createDeductionProposal(
                    date = date,
                    originatingDevice = audit.optString("originating_device", "SECONDARY_CLIENT"),
                    eventType = eventType,
                    proposedPoints = points,
                    reason = description,
                    applicableRule = audit.optString("applicable_rule", "DISCIPLINE_RULE"),
                    relatedTaskId = audit.optString("related_task_id", ""),
                    evidenceJson = audit.toString()
                )
                return
            }
        }

        ensureDay(date, isDevMode)
        val eventId = if (audit.optString("transaction_id").isNotBlank()) {
            audit.optString("transaction_id")
        } else if (audit.optString("event_id").isNotBlank()) {
            audit.optString("event_id")
        } else if (eventType == "PROOF_APPROVED" && evidenceId != null) {
            "se_proof_" + UUID.nameUUIDFromBytes(evidenceId.toByteArray(Charsets.UTF_8)).toString().replace("-", "").take(24)
        } else {
            "se_" + UUID.randomUUID().toString().replace("-", "").take(10)
        }
        audit.put("Device", "Android Device")
        audit.put("Timestamp", nowIso())
        audit.put("Therefore", "${if (points >= 0) "+" else ""}$points points")

        val cv = ContentValues().apply {
            put("event_id", eventId)
            put("date", date)
            put("is_dev_mode", if (isDevMode) 1 else 0)
            put("timestamp", nowIso())
            put("event_type", eventType)
            put("description", description)
            put("points", points)
            put("audit_json", audit.toString())
        }
        val inserted = writableDatabase.insertWithOnConflict("score_events", null, cv, SQLiteDatabase.CONFLICT_IGNORE)
        if (inserted == -1L) return
        if (!isDevMode) StrictWidgetUpdates.request(appContext,"points")
        enqueueSync(
            "SCORE_EVENT",
            JSONObject().apply {
                put("event_id", eventId)
                put("date", date)
                put("is_dev_mode", isDevMode)
                put("timestamp", cv.getAsString("timestamp"))
                put("event_type", eventType)
                put("description", description)
                put("points", points)
                put("audit", audit)
            }
        )
        runCatching {
            BackgroundMusicRestrictionManager.onRestrictionStateChanged(appContext, "score_event_$eventType")
        }
    }

    fun hasScoreEvent(eventId: String): Boolean {
        return readableDatabase.rawQuery("SELECT 1 FROM score_events WHERE event_id=?", arrayOf(eventId)).use {
            it.moveToFirst()
        }
    }

    fun recordAccessibilityRestrictedAppPenalty(
        date: String,
        episodeId: String,
        minuteIndex: Int,
        packageName: String,
        intervalStartMs: Long,
        intervalEndMs: Long,
        confirmedDurationMs: Long,
        points: Int = -1
    ): Boolean {
        val transactionId = "tx_acc_${episodeId}_m${minuteIndex}"
        if (hasScoreEvent(transactionId)) return false

        val audit = JSONObject().apply {
            put("approval_source", "ANDROID_ACCESSIBILITY_ENFORCEMENT")
            put("approved_by", "ANDROID_ACCESSIBILITY_ENFORCEMENT")
            put("applicable_rule", "ACCESSIBILITY_ENFORCEMENT_RULE")
            put("transaction_id", transactionId)
            put("episode_id", episodeId)
            put("minute_index", minuteIndex)
            put("package_name", packageName)
            put("interval_start_ms", intervalStartMs)
            put("interval_end_ms", intervalEndMs)
            put("confirmed_duration_ms", confirmedDurationMs)
            put("penalty_points", points)
        }

        addScoreEvent(
            date = date,
            isDevMode = false,
            eventType = "RESTRICTED_APP_PENALTY",
            description = "Accessibility interruption penalty — 1 eligible minute of restricted-app usage.",
            points = points,
            audit = audit
        )
        return true
    }

    fun recordBrowserExtensionPenalty(
        date: String,
        episodeId: String,
        minuteIndex: Int,
        browserName: String = "Google Chrome",
        intervalStartMs: Long = 0L,
        intervalEndMs: Long = 0L,
        points: Int = -1
    ): Boolean {
        val transactionId = "tx_ext_${episodeId}_m${minuteIndex}"
        if (hasScoreEvent(transactionId)) return false

        val audit = JSONObject().apply {
            put("approval_source", "BROWSER_EXTENSION_ENFORCEMENT")
            put("approved_by", "BROWSER_EXTENSION_ENFORCEMENT")
            put("applicable_rule", "CHROME_EXTENSION_ENFORCEMENT_RULE")
            put("transaction_id", transactionId)
            put("episode_id", episodeId)
            put("minute_index", minuteIndex)
            put("browser", browserName)
            put("interval_start_ms", intervalStartMs)
            put("interval_end_ms", intervalEndMs)
            put("penalty_points", points)
        }

        addScoreEvent(
            date = date,
            isDevMode = false,
            eventType = "RESTRICTED_APP_PENALTY",
            description = "Chrome extension disabled penalty: minute $minuteIndex post-grace — 1 point.",
            points = points,
            audit = audit
        )
        return true
    }

    private val approvedProofCache = java.util.concurrent.ConcurrentHashMap<String, Boolean>()

    fun invalidateApprovedProofCache(evidenceId: String = "") {
        if (evidenceId.isBlank()) approvedProofCache.clear() else approvedProofCache.remove(evidenceId)
    }

    fun hasApprovedRetainedProof(evidenceId: String): Boolean {
        if (evidenceId.isBlank()) return false
        approvedProofCache[evidenceId]?.let { if (it) return true }
        val valid = readableDatabase.rawQuery(
            "SELECT file_path FROM proof_reviews WHERE evidence_id = ? AND status IN ('APPROVED', 'VERIFIED', 'AUTO_APPROVED')",
            arrayOf(evidenceId)
        ).use { cursor ->
            if (!cursor.moveToFirst()) return@use false
            runCatching {
                val path = cursor.getString(0)
                if (path.isNullOrBlank()) {
                    true
                } else {
                    File(path).let { it.exists() && it.length() > 0 } || hasValidRetainedProofImage(path)
                }
            }.getOrDefault(false)
        }
        if (valid) {
            approvedProofCache[evidenceId] = true
        }
        return valid
    }

    fun enqueueSync(payloadType: String, payload: JSONObject) {
        val syncId = "sync_" + UUID.randomUUID().toString().replace("-", "")
        val cv = ContentValues().apply {
            put("sync_id", syncId)
            put("payload_type", payloadType)
            put("payload_json", payload.toString())
            put("status", "PENDING_SYNC")
        }
        writableDatabase.insert("sync_queue", null, cv)
        try {
            enqueueSyncEvent(payloadType, payload)
        } catch (_: Exception) {}
    }

    fun recordSnooze(date: String, isDevMode: Boolean): Int {
        ensureDay(date, isDevMode)
        val dev = if (isDevMode) 1 else 0
        writableDatabase.execSQL(
            "UPDATE daily_state SET snooze_count = snooze_count + 1, sync_status = 'PENDING SYNC' WHERE date = ? AND is_dev_mode = ?",
            arrayOf<Any>(date, dev)
        )
        val audit = JSONObject().apply {
            put("Target", DisciplineScoringRules.DEFAULT_WAKEUP_TIME)
            put("Snoozed_At", nowTime())
        }
        addScoreEvent(date, isDevMode, "SNOOZE", "User snoozed wake-up alarm (-5)", -5, audit)
        logStructured(date, isDevMode, "USER_PRESSED_SNOOZE", "-5 points")
        enqueueSync("SNOOZE", JSONObject().put("date", date).put("timestamp", nowIso()).put("is_dev_mode", isDevMode))
        return getScore(date, isDevMode)
    }

    fun completeCameraStep(
        date: String,
        isDevMode: Boolean,
        imageBytes: ByteArray = ByteArray(0),
        livenessMetadata: JSONObject? = null
    ): File {
        ensureDay(date, isDevMode)
        val folder = File(appContext.filesDir, "STRICTRECORDS/$date/WakeUp")
        folder.mkdirs()
        val photoFile = File(folder, "wakeup_${System.currentTimeMillis()}.jpg")
        fun reject(reason: String): File {
            setWakeConfig("camera_save_error", date, isDevMode, reason)
            android.util.Log.w("StrictWakeCamera", "save_rejected date=$date isDevMode=$isDevMode reason=$reason")
            return photoFile
        }
        if (imageBytes.isEmpty()) return reject("CAMERA_IMAGE_MISSING: capture produced no image")
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(imageBytes, 0, imageBytes.size, bounds)
        if (bounds.outWidth < 100 || bounds.outHeight < 100) return reject("CAMERA_IMAGE_INVALID: final image is not valid")
        val live = livenessMetadata ?: return reject("CAMERA_METADATA_MISSING: observation result was not supplied")
        val validation = WakeUpCameraPolicy.validateMetadata(live)
        if (!validation.passed) return reject("CAMERA_POLICY_FAILED: ${validation.reasons.joinToString("; ")}")
        if (!isDevMode && (!isWakeUpWindowOpen() || date != currentScheduleDate())) return reject("WAKEUP_WINDOW_CLOSED: completion must be before 09:30 AM")
        live.put("attempt_id", getWakeUpAttemptId(date, isDevMode))
        live.put("photo_sha256", DisciplineScoringRules.computeSha256Hex(imageBytes))
        java.io.FileOutputStream(photoFile).use { fos ->
            fos.write(imageBytes)
            fos.flush()
            runCatching { fos.fd.sync() }
        }
        setWakeConfig("camera_save_error", date, isDevMode, "")
        android.util.Log.i("StrictWakeCamera", "save_validated date=$date size=${bounds.outWidth}x${bounds.outHeight} validMs=${live.optLong("valid_duration_ms")} samples=${live.optInt("valid_samples")}/${live.optInt("total_samples")}")
        val hasImage = photoFile.isFile && photoFile.length() > 0L
        if (!hasImage) return photoFile

        setWakeConfig("wakeup_liveness_metadata", date, isDevMode, live.toString())

        val dev = if (isDevMode) 1 else 0
        val alreadyDone = getDayState(date, isDevMode).optInt("wakeup_camera") == 1
        writableDatabase.execSQL(
            "UPDATE daily_state SET wakeup_camera = 1, wakeup_camera_path = ? WHERE date = ? AND is_dev_mode = ?",
            arrayOf<Any>(photoFile.absolutePath, date, dev)
        )
        if (!isDevMode) {
            val existingEvId = getConfigValue("wakeup_evidence_id_$date", "")
            val evidenceId = if (existingEvId.isNotBlank()) existingEvId else "ev_cam_${System.currentTimeMillis()}"
            setConfigValue("wakeup_evidence_id_$date", evidenceId)
            if (!alreadyDone || existingEvId.isBlank()) {
                registerProofForReview(
                    evidenceId,
                    date,
                    "WAKE_UP",
                    "Morning Wake-Up Camera Proof",
                    photoFile.absolutePath,
                    "HIGH"
                )
            }
            enqueueSyncEvent(
                "WAKEUP_STEP1_CAMERA",
                JSONObject().apply {
                    put("date", date)
                    put("evidence_id", evidenceId)
                    put("file_name", photoFile.name)
                    put("liveness_metadata", live)
                }
            )
        }
        logStructured(date, isDevMode, "CAMERA_PROOF_SUBMITTED", photoFile.name)
        exportLocalEvidencePackage(date, isDevMode)
        return photoFile
    }

    fun completeWakeUpStepWithoutPhoto(date: String, isDevMode: Boolean) {
        completeCameraStep(date, isDevMode, ByteArray(0))
    }

    fun completeBluetoothStep(date: String, deviceName: String, routeType: String = "BLUETOOTH_A2DP") {
        setWakeConfig("wakeup_bt_route_type", date, false, routeType)
        completeBluetoothStep(date, false, deviceName)
    }

    fun completeBluetoothStep(date: String, isDevMode: Boolean, deviceName: String) {
        val routeEval = WakeUpAudioRouteManager.evaluateRoute(appContext, getEarbudsConfig(), getEarbudsAddress())
        if (!routeEval.isVerified) return
        val attemptId = getWakeUpAttemptId(date, isDevMode)
        recordVerifiedWakeUpAudioRoute(
            date = date,
            attemptId = attemptId,
            deviceName = routeEval.deviceName,
            deviceAddress = routeEval.deviceAddress,
            routeTypeName = routeEval.routeTypeName,
            isDevMode = isDevMode
        )
        if (!isDevMode) {
            enqueueSyncEvent(
                "WAKEUP_STEP2_BLUETOOTH",
                JSONObject().apply {
                    put("date", date)
                    put("attempt_id", attemptId)
                    put("device_name", routeEval.deviceName)
                    put("device_address", routeEval.deviceAddress)
                    put("route_type", routeEval.routeTypeName)
                }
            )
        }
        logStructured(date, isDevMode, "BLUETOOTH_CONNECTED", routeEval.deviceName)
    }

    fun completeMotivationTrackStep(
        date: String,
        deviceName: String,
        isCapped60: Boolean = true,
        playbackTelemetry: JSONObject? = null,
        isDevMode: Boolean = false
    ): Boolean {
        if (playbackTelemetry == null) return false
        setWakeConfig("wakeup_playback_telemetry", date, isDevMode, playbackTelemetry.toString())
        setWakeConfig("wakeup_volume_capped60", date, isDevMode, if (isCapped60) "1" else "0")
        return completeMotivationTrackStep(
            date = date,
            isDevMode = isDevMode,
            trackName = "Morning Motivation Track ($deviceName)",
            startedAt = nowTime(),
            completedAt = nowTime(),
            completionPct = 100.0
        )
    }

    fun completeMotivationTrackStep(
        date: String,
        isDevMode: Boolean,
        trackName: String,
        startedAt: String,
        completedAt: String,
        completionPct: Double
    ): Boolean {
        ensureDay(date, isDevMode)
        val state = getDayState(date, isDevMode)
        val flow = getWakeUpFlowStatus(date, isDevMode)
        val attemptId = flow.optString("attempt_id")
        val camera = flow.optJSONObject("liveness_metadata") ?: JSONObject()
        val playback = runCatching { JSONObject(getWakeConfig("wakeup_playback_telemetry", date, isDevMode, "{}")) }.getOrDefault(JSONObject())
        val step2VerifiedDev = getWakeConfig("wakeup_bt_verified_device", date, isDevMode)
        val step2VerifiedAddr = getWakeConfig("wakeup_bt_verified_address", date, isDevMode)
        val isBtRoute = playback.optString("audio_route") in listOf(
            "BLUETOOTH_A2DP", "BLUETOOTH_SCO", "BLE_HEADSET", "BLE_SPEAKER", "BLE_BROADCAST", "HEARING_AID"
        )
        val effectiveRouteName = playback.optString("routed_device_name").ifBlank { step2VerifiedDev }
        val effectiveRouteAddr = playback.optString("routed_device_address").ifBlank { step2VerifiedAddr }
        val routeMatches = WakeUpAttemptRules.selectedRouteMatches(
            getEarbudsConfig(), getEarbudsAddress(), effectiveRouteName,
            effectiveRouteAddr, isBtRoute
        )
        val isRouteSourceValid = playback.optString("route_source") in listOf(
            "MEDIA_PLAYER_ROUTED_DEVICE", "AUDIO_ROUTING_CALLBACK"
        )
        val isRouteObserved = playback.optInt("route_observations") >= 1
        val isHeadsetOk = playback.optBoolean("headset_connected")

        val isBtSkipped = getWakeConfig("wakeup_bt_skipped", date, isDevMode) == "1"
        val isTrackSkipped = getWakeConfig("wakeup_track_skipped", date, isDevMode) == "1"

        val validation = WakeUpAttemptRules.Completion(
            attemptId, camera.optString("attempt_id"), getWakeConfig("wakeup_route_attempt", date, isDevMode), playback.optString("attempt_id"),
            flow.optBoolean("step1_camera"), flow.optBoolean("step2_bluetooth") && routeMatches &&
                isRouteSourceValid && isRouteObserved && isHeadsetOk,
            playback.optBoolean("completed") && playback.optBoolean("volume_ok") && completionPct >= 100.0,
            (playback.optDouble("track_duration_sec") * 1000).toLong(), (playback.optDouble("completed_duration_sec") * 1000).toLong(),
            playback.optLong("route_verified_duration_ms"), playback.optInt("seek_attempts", -1),
            playback.optString("track_sha256") == getConfiguredWakeUpTrackHash(),
            isDevMode || (isWakeUpWindowOpen() && date == currentScheduleDate()), isDevMode,
            earbudsOptional = true, audioOptional = true
        )
        if (isDevMode) {
            if (!validation.cameraPassed) {
                setWakeConfig("wakeup_submission_error", date, isDevMode, "CAMERA_PROOF_INCOMPLETE")
                return false
            }
            if (!isBtSkipped && !validation.routeVerified) {
                setWakeConfig("wakeup_submission_error", date, isDevMode, "SELECTED_AUDIO_ROUTE_NOT_VERIFIED")
                return false
            }
            if (!isTrackSkipped) {
                if (!validation.trackCompleted || validation.durationMs <= 0 || validation.listenedMs < validation.durationMs || validation.routeVerifiedMs < validation.durationMs) {
                    setWakeConfig("wakeup_submission_error", date, isDevMode, "FULL_TRACK_INCOMPLETE")
                    return false
                }
                if (validation.seekAttempts != 0) {
                    setWakeConfig("wakeup_submission_error", date, isDevMode, "PLAYBACK_SEEK_DETECTED")
                    return false
                }
                if (!validation.trackHashMatches) {
                    setWakeConfig("wakeup_submission_error", date, isDevMode, "CONFIGURED_TRACK_MISMATCH")
                    return false
                }
            }
            val store = writableDatabase
            store.beginTransaction()
            try {
                store.execSQL("UPDATE daily_state SET wakeup_track=1, wakeup_track_pct=100, wakeup_status='DEMO_COMPLETED', wakeup_completed_at=? WHERE date=? AND is_dev_mode=1",
                    arrayOf(nowTime(), date))
                setWakeConfig("wakeup_submission_error", date, isDevMode, "")
                store.setTransactionSuccessful()
            } finally { store.endTransaction() }
            logStructured(date, true, "WAKEUP_DEMO_COMPLETED", "All three steps verified in demo mode; zero points awarded, no server submission")
            return true
        }

        val rejection = WakeUpAttemptRules.rejection(validation)
        if (rejection != null) {
            setWakeConfig("wakeup_submission_error", date, isDevMode, rejection)
            android.util.Log.w("StrictWakeAudio", "attempt_rejected reason=$rejection")
            return false
        }
        val existing = getConfigValue("wakeup_submission_event_$attemptId", "")
        if (existing.isNotBlank()) return true
        val evidenceId = getConfigValue("wakeup_evidence_id_$date", "")
        if (evidenceId.isBlank()) { setWakeConfig("wakeup_submission_error", date, isDevMode, "CAMERA_EVIDENCE_MISSING"); return false }
        val photo = File(state.optString("wakeup_camera_path"))
        if (!photo.isFile || photo.length() !in 1..(10L * 1024L * 1024L)) return false
        val payload = JSONObject().apply {
            put("date", date); put("attempt_id", attemptId); put("completed_at", formatMsToIso(authoritativeNowMs()))
            put("bt_device", getEarbudsConfig()); put("track_name", trackName); put("completion_pct", 100.0)
            put("volume_capped_60_pct", true); put("evidence_id", evidenceId)
            put("photo_liveness_passed", true); put("earbuds_route_passed", true); put("active_audio_output_verified", true); put("full_track_passed", true)
            put("playback_session", playback); put("liveness_metadata", camera)
            put("image_base64", Base64.encodeToString(photo.readBytes(), Base64.NO_WRAP))
        }
        val eventId = "evt_wake_" + UUID.nameUUIDFromBytes(attemptId.toByteArray()).toString()
        val store = writableDatabase
        store.beginTransaction()
        try {
            enqueueSyncEvent("WAKEUP_VERIFIED", payload, idempotencyKey = eventId)
            store.execSQL("UPDATE daily_state SET wakeup_track=1, wakeup_track_pct=100, wakeup_status='PENDING_SERVER', wakeup_completed_at=? WHERE date=? AND is_dev_mode=0",
                arrayOf(nowTime(), date))
            setConfigValue("wakeup_submission_event_$attemptId", eventId)
            setWakeConfig("wakeup_submission_error", date, isDevMode, "")
            store.setTransactionSuccessful()
        } finally { store.endTransaction() }
        reconcileHourlyWakeUpConflicts(date)
        if (isDeviceRegistered()) Thread { runCatching { flushSyncEventsQueue(appContext) } }.start()
        logStructured(date, false, "WAKEUP_STEPS_COMPLETED", "Awaiting authoritative server acceptance; no local point award")
        return true
    }

    fun isWakeUpVerificationCompleted(date: String = nowDate(), isDevMode: Boolean = false): Boolean {
        if (isDevMode) {
            val devSt = getDayState(date, true).optString("wakeup_status", "").uppercase(Locale.US)
            val devFlow = getWakeUpFlowStatus(date, true)
            if (devSt in listOf("DEMO_COMPLETED", "VERIFIED", "COMPLETED", "APPROVED") && devFlow.optBoolean("step1_camera", false)) {
                return true
            }
        }
        val st = getDayState(date, false).optString("wakeup_status", "").uppercase(Locale.US)
        if (st in listOf("VERIFIED", "COMPLETED", "APPROVED")) return true

        val flow = getWakeUpFlowStatus(date, false)
        if (flow.optBoolean("all_completed", false) || (flow.optBoolean("step1_camera", false) && (st in listOf("VERIFIED", "COMPLETED", "PENDING_SERVER")))) {
            return true
        }

        val hasApprovedReview = readableDatabase.rawQuery(
            "SELECT 1 FROM proof_reviews WHERE (task_code IN ('WAKE_UP', 'WAKEUP') OR activity_id LIKE '%WAKEUP%') AND date = ? AND status IN ('APPROVED', 'VERIFIED')",
            arrayOf(date)
        ).use { it.moveToFirst() }
        if (hasApprovedReview) return true

        val hasVerifiedAct = readableDatabase.rawQuery(
            "SELECT 1 FROM scheduled_activities WHERE date = ? AND (type = 'WAKE_UP' OR activity_id LIKE '%WAKEUP%') AND status IN ('VERIFIED', 'COMPLETED', 'APPROVED')",
            arrayOf(date)
        ).use { it.moveToFirst() }
        return hasVerifiedAct
    }

    fun isMorningFlowCompletedToday(date: String = nowDate(), isDevMode: Boolean = false): Boolean {
        val wakeDone = isWakeUpVerificationCompleted(date, isDevMode)
        val routineDone = isBathScheduleConfirmed(date)
        return wakeDone && routineDone
    }

    fun isMorningFlowPending(date: String = nowDate(), isDevMode: Boolean = false): Boolean {
        return !isMorningFlowCompletedToday(date, isDevMode)
    }

    fun markMorningFlowPromptShown(date: String = nowDate()) {
        setConfigValue("morning_flow_prompt_shown_$date", "true")
    }

    fun hasMorningFlowPromptBeenShown(date: String = nowDate()): Boolean {
        return getConfigValue("morning_flow_prompt_shown_$date", "false") == "true"
    }

    fun markMorningInAppAutoOpened(date: String = nowDate()) {
        setConfigValue("morning_inapp_auto_opened_$date", "true")
    }

    fun hasMorningInAppAutoOpened(date: String = nowDate()): Boolean {
        return getConfigValue("morning_inapp_auto_opened_$date", "false") == "true"
    }

    fun finalizeWakeUpVerification(date: String = nowDate(), isDevMode: Boolean = false): Pair<Boolean, String> {
        ensureDay(date, isDevMode)
        val state = getDayState(date, isDevMode)
        val flow = getWakeUpFlowStatus(date, isDevMode)
        val attemptId = flow.optString("attempt_id")
        val camera = flow.optJSONObject("liveness_metadata") ?: JSONObject()
        val photoPath = state.optString("wakeup_camera_path")
        val photo = File(photoPath)

        // Mandatory check: Camera face/liveness verification must have passed
        if (!flow.optBoolean("step1_camera") || !photo.isFile || photo.length() <= 0) {
            setWakeConfig("wakeup_submission_error", date, isDevMode, "CAMERA_PROOF_INCOMPLETE")
            return Pair(false, "Camera face verification is still pending. Complete the 20-second camera verification first.")
        }

        val step2Done = flow.optBoolean("step2_bluetooth", false)
        val step3Done = flow.optBoolean("step3_motivation_track", false)

        if (isDevMode) {
            val store = writableDatabase
            store.beginTransaction()
            try {
                store.execSQL(
                    "UPDATE daily_state SET wakeup_track=1, wakeup_track_pct=100, wakeup_status='DEMO_COMPLETED', wakeup_completed_at=? WHERE date=? AND is_dev_mode=1",
                    arrayOf(nowTime(), date)
                )
                setWakeConfig("wakeup_submission_error", date, isDevMode, "")
                store.setTransactionSuccessful()
            } finally {
                store.endTransaction()
            }
            logStructured(date, true, "WAKEUP_DEMO_COMPLETED", "Wake-up verification completed in Demo Test Mode (audio/earbuds optional); zero score impact.")
            return Pair(true, "Demo verification complete! (Zero score impact • No points awarded)")
        }

        // Production verification
        val evidenceId = getConfigValue("wakeup_evidence_id_$date", "").ifBlank {
            "ev_wake_${date.replace("-", "")}_${System.currentTimeMillis() % 100000}".also {
                setConfigValue("wakeup_evidence_id_$date", it)
            }
        }
        val playback = runCatching { JSONObject(getWakeConfig("wakeup_playback_telemetry", date, isDevMode, "{}")) }.getOrDefault(JSONObject())
        val payload = JSONObject().apply {
            put("date", date)
            put("attempt_id", attemptId)
            put("completed_at", formatMsToIso(authoritativeNowMs()))
            put("bt_device", getEarbudsConfig())
            put("track_name", "Motivation Track (Optional)")
            put("completion_pct", if (step3Done) 100.0 else 0.0)
            put("evidence_id", evidenceId)
            put("photo_liveness_passed", true)
            put("earbuds_route_passed", step2Done)
            put("active_audio_output_verified", step2Done)
            put("full_track_passed", step3Done)
            put("audio_optional", true)
            put("earbuds_optional", true)
            put("playback_session", playback)
            put("liveness_metadata", camera)
            if (photo.isFile) {
                put("image_base64", Base64.encodeToString(photo.readBytes(), Base64.NO_WRAP))
            }
        }
        val eventId = "evt_wake_" + UUID.nameUUIDFromBytes(attemptId.toByteArray()).toString()
        val store = writableDatabase
        store.beginTransaction()
        try {
            enqueueSyncEvent("WAKEUP_VERIFIED", payload, idempotencyKey = eventId)
            store.execSQL(
                "UPDATE daily_state SET wakeup_track=1, wakeup_track_pct=100, wakeup_status='PENDING_SERVER', wakeup_completed_at=? WHERE date=? AND is_dev_mode=0",
                arrayOf(nowTime(), date)
            )
            setConfigValue("wakeup_submission_event_$attemptId", eventId)
            setWakeConfig("wakeup_submission_error", date, isDevMode, "")
            store.setTransactionSuccessful()
        } finally {
            store.endTransaction()
        }
        reconcileHourlyWakeUpConflicts(date)
        if (isDeviceRegistered()) Thread { runCatching { flushSyncEventsQueue(appContext) } }.start()
        logStructured(date, false, "WAKEUP_STEPS_COMPLETED", "Wake-up verified (audio/earbuds optional); awaiting server sync")
        return Pair(true, "Wake-up verification completed successfully.")
    }

    fun completeBath(date: String, isDevMode: Boolean, mode: String, completedHhmm: String = nowTime()) {
        // Manual completion is a claim, never a locally authoritative deduction or approval.
        ensureDay(date, isDevMode)
        val dev = if (isDevMode) 1 else 0
        writableDatabase.execSQL("UPDATE daily_state SET bath_status='BATH PROOF REQUIRED' WHERE date=? AND is_dev_mode=?", arrayOf<Any>(date, dev))
    }

    fun submitHourlyUpdate(
        date: String,
        isDevMode: Boolean,
        hourSlot: String,
        activity: String,
        description: String,
        hasPhotoEvidence: Boolean,
        androidUsageSummary: String
    ) {
        ensureDay(date, isDevMode)
        val dev = if (isDevMode) 1 else 0
        writableDatabase.execSQL(
            "UPDATE daily_state SET hourly_completed = hourly_completed + 1, sync_status = 'PENDING SYNC' WHERE date = ? AND is_dev_mode = ?",
            arrayOf<Any>(date, dev)
        )
        val cleanAct = activity.uppercase(Locale.US).trim()
        val isWasted = cleanAct == "WASTED TIME" || cleanAct == "WASTED"
        if (isWasted) {
            addScoreEvent(
                date,
                isDevMode,
                "HOURLY_WASTED",
                "Hourly ($hourSlot): $cleanAct — $description",
                -3,
                JSONObject().apply {
                    put("Hour_Slot", hourSlot)
                    put("Activity", cleanAct)
                    put("Android_Usage", androidUsageSummary)
                }
            )
        }
        val targetAct = getDailyActivities(date).firstOrNull { act ->
            act.type == "HOURLY" && (
                act.title.contains(hourSlot) ||
                act.detailsJson.contains("\"hour_slot\":\"$hourSlot\"") ||
                act.detailsJson.contains(hourSlot)
            )
        } ?: getActiveOrTargetHourlyActivity(date)
        val subId = "sub_hourly_${date}_${hourSlot.replace(":", "").replace(" ", "").replace("-", "_")}_${System.currentTimeMillis()}"
        if (targetAct != null) {
            val actDetails = runCatching { JSONObject(targetAct.detailsJson) }.getOrDefault(JSONObject())
            actDetails.put("submission_id", subId)
            actDetails.put("primary_activity", cleanAct)
            actDetails.put("description", description)
            writableDatabase.execSQL(
                "UPDATE scheduled_activities SET details_json = ? WHERE activity_id = ?",
                arrayOf(actDetails.toString(), targetAct.activityId)
            )
            recordActivityVerification(targetAct.activityId, !isWasted, 0)
        }
        logStructured(date, isDevMode, "HOURLY_UPDATE_SUBMITTED", "$hourSlot $cleanAct")
        enqueueSync(
            "HOURLY_UPDATE",
            JSONObject().apply {
                put("date", date)
                put("hour_slot", hourSlot)
                put("primary_activity", cleanAct)
                put("description", description)
                put("is_dev_mode", isDevMode)
            }
        )
        exportLocalEvidencePackage(date, isDevMode)
    }

    fun submitCurrentWindowDirectly(
        date: String,
        isDevMode: Boolean,
        activity: String = "STUDY",
        description: String = "Activity window checkpoint verified"
    ): Pair<Int, String> {
        val countdown = getCurrentCountdownState(date)
        val actId = countdown.activityId
        val actType = countdown.type ?: "HOURLY"
        if (actType == "WAKE_UP") {
            return Pair(0, "Wake-up proof requires a captured photo and the configured verification steps")
        }
        return Pair(0, "Photo proof is required before this checkpoint can be submitted")
    }

    fun completeGymSession(date: String, isDevMode: Boolean, durationMinutes: Int, skipped: Boolean = false): Boolean {
        ensureDay(date, isDevMode)
        val dev = if (isDevMode) 1 else 0
        if (skipped) {
            val occurrence = getDailyActivities(date).firstOrNull { it.type == "GYM" && it.status !in listOf("SKIPPED", "VERIFIED", "COMPLETED", "CANCELLED", "MISSED", "CLOSED", "EXPIRED") } ?: return false
            val eventId = "gym_skip_${occurrence.activityId}_${dev}"
            if (getSyncEventState(eventId).first in listOf("PENDING", "SYNCED")) return false
            writableDatabase.execSQL("UPDATE scheduled_activities SET status = 'SKIPPED' WHERE activity_id = ?", arrayOf(occurrence.activityId))
            writableDatabase.execSQL("UPDATE daily_state SET gym_status = 'SKIPPED' WHERE date = ? AND is_dev_mode = ?", arrayOf(date, dev))
            enqueueSyncEvent("GYM_SKIP_REQUESTED", JSONObject().put("date", date).put("activity_id", occurrence.activityId), idempotencyKey = eventId)
            return true // Queued, not a confirmed deduction. Only the server applies points.
        }
        if (durationMinutes < DisciplineScoringRules.DEFAULT_GYM_MIN_MINUTES) {
            return false
        }
        if (getDailyActivities(date).any { it.type == "GYM" && it.status == "SKIPPED" }) return false
        writableDatabase.execSQL(
            "UPDATE daily_state SET gym_status = 'GYM PROOF REQUIRED', gym_duration_min = ? WHERE date = ? AND is_dev_mode = ?",
            arrayOf<Any>(durationMinutes, date, dev)
        )
        addScoreEvent(
            date,
            isDevMode,
            "GYM_COMPLETED",
            "Gym session completed ($durationMinutes mins)",
            0,
            JSONObject().put("Duration_Min", durationMinutes).put("Minimum_Required", 30)
        )
        logStructured(date, isDevMode, "GYM_COMPLETED", "${durationMinutes}m")
        exportLocalEvidencePackage(date, isDevMode)
        return true
    }

    fun withdrawGymSkip(date: String = nowDate(), isDevMode: Boolean = false): Boolean {
        return false // Confirmed occurrence actions are retained; corrections require administrator review.
    }


    fun resetGymSession(date: String = nowDate(), isDevMode: Boolean = false): Boolean {
        ensureDay(date, isDevMode)
        val dev = if (isDevMode) 1 else 0
        val store = writableDatabase
        store.beginTransaction()
        try {
            store.delete("score_events", "date = ? AND is_dev_mode = ? AND event_type IN ('GYM_SKIPPED', 'GYM_COMPLETED')", arrayOf(date, dev.toString()))
            store.delete("sync_events", "event_type = 'SCORE_EVENT' AND (payload_json LIKE '%GYM_SKIPPED%' OR payload_json LIKE '%GYM_COMPLETED%')", null)
            store.execSQL(
                "UPDATE daily_state SET gym_status = 'UPCOMING', gym_duration_min = 0 WHERE date = ? AND is_dev_mode = ?",
                arrayOf<Any>(date, dev)
            )
            store.setTransactionSuccessful()
        } finally {
            store.endTransaction()
        }
        enqueueSync(
            "GYM_RESET",
            JSONObject().apply {
                put("date", date)
                put("is_dev_mode", isDevMode)
                put("timestamp", nowIso())
            }
        )
        logStructured(date, isDevMode, "GYM_RESET", "Gym session reset to UPCOMING.")
        exportLocalEvidencePackage(date, isDevMode)
        return true
    }

    fun completeFocusSession(date: String, isDevMode: Boolean, durationMinutes: Int, isDeepWork: Boolean): Boolean {
        ensureDay(date, isDevMode)
        val dev = if (isDevMode) 1 else 0
        val pts = 0
        val label = if (isDeepWork) "DEEP_WORK_COMPLETED" else "FOCUS_SESSION_COMPLETED"
        writableDatabase.execSQL(
            "UPDATE daily_state SET focus_sessions_count = focus_sessions_count + 1 WHERE date = ? AND is_dev_mode = ?",
            arrayOf<Any>(date, dev)
        )
        addScoreEvent(
            date,
            isDevMode,
            label,
            "${if (isDeepWork) "Deep Work" else "Focus"} session completed ($durationMinutes mins)",
            pts,
            JSONObject().put("Duration_Min", durationMinutes).put("Mode", if (isDeepWork) "DEEP_WORK" else "FOCUS")
        )
        logStructured(date, isDevMode, label, "${durationMinutes}m")
        exportLocalEvidencePackage(date, isDevMode)
        return true
    }

    fun saveJournal(date: String, isDevMode: Boolean, reflection: String) {
        ensureDay(date, isDevMode)
        val dev = if (isDevMode) 1 else 0
        writableDatabase.execSQL(
            "UPDATE daily_state SET journal_text = ? WHERE date = ? AND is_dev_mode = ?",
            arrayOf<Any>(reflection, date, dev)
        )
        logStructured(date, isDevMode, "JOURNAL_SAVED", reflection.take(60))
        exportLocalEvidencePackage(date, isDevMode)
    }

    fun setAppearanceMode(date: String, isDevMode: Boolean, mode: String) {
        ensureDay(date, isDevMode)
        val dev = if (isDevMode) 1 else 0
        writableDatabase.execSQL(
            "UPDATE daily_state SET appearance_mode = ? WHERE date = ? AND is_dev_mode = ?",
            arrayOf<Any>(mode, date, dev)
        )
    }

    fun getScore(date: String, isDevMode: Boolean): Int {
        val events = getScoreEvents(date, isDevMode)
        val entries = mutableListOf<DisciplineScoringRules.ScorePointEntry>()
        for (i in 0 until events.length()) {
            val event = events.getJSONObject(i)
            val points = event.optInt("points", 0)
            val evidenceId = event.optJSONObject("audit")?.optString("evidence_id").orEmpty()
            entries.add(
                DisciplineScoringRules.ScorePointEntry(
                    eventType = event.optString("event_type"),
                    points = points,
                    evidenceId = evidenceId,
                    approvedWithRetainedProof = points > 0 && evidenceId.isNotBlank() && hasApprovedRetainedProof(evidenceId)
                )
            )
        }
        val localScore = DisciplineScoringRules.calculateVerifiedDailyScore(entries)
        return localScore
    }

    fun hasWakeUpBonusBeenAwarded(date: String): Boolean {
        return readableDatabase.rawQuery(
            "SELECT 1 FROM score_events WHERE date = ? AND is_dev_mode = 0 AND points > 0 AND (" +
                "event_type IN ('WAKEUP_VERIFIED', 'WAKEUP_COMPLETED', 'WAKEUP_ON_TIME_BONUS') OR " +
                "description LIKE '%Wake-Up%' OR description LIKE '%WAKEUP%' OR " +
                "audit_json LIKE '%WAKE_UP%' OR audit_json LIKE '%WAKEUP%' OR " +
                "audit_json LIKE '%act_wakeup%')",
            arrayOf(date)
        ).use { it.moveToFirst() }
    }

    fun calculateStreakDays(isDevMode: Boolean): Int {
        val dev = if (isDevMode) 1 else 0
        var localStreak = 0
        try {
            readableDatabase.rawQuery(
                "SELECT DISTINCT date FROM score_events WHERE is_dev_mode = ? AND points > 0 ORDER BY date DESC LIMIT 365",
                arrayOf(dev.toString())
            ).use { c ->
                val activeDates = mutableSetOf<String>()
                while (c.moveToNext()) {
                    val date = c.getString(0)
                    if (getScore(date, isDevMode) > 0) activeDates.add(date)
                }
                val cal = Calendar.getInstance()
                val fmt = SimpleDateFormat("yyyy-MM-dd", Locale.US)
                for (i in 0 until 365) {
                    val dStr = fmt.format(cal.time)
                    if (activeDates.contains(dStr)) {
                        localStreak++
                    } else if (i > 0) {
                        break
                    }
                    cal.add(Calendar.DAY_OF_YEAR, -1)
                }
            }
        } catch (_: Exception) {}
        val serverStreak = if (!isDevMode) (getConfigValue("server_streak_days", "0").toIntOrNull() ?: 0) else 0
        return maxOf(localStreak, serverStreak)
    }

    fun getScoreEvents(date: String, isDevMode: Boolean): JSONArray {
        val arr = JSONArray()
        val dev = if (isDevMode) 1 else 0
        readableDatabase.rawQuery(
            "SELECT * FROM score_events WHERE date = ? AND is_dev_mode = ? ORDER BY timestamp ASC",
            arrayOf(date, dev.toString())
        ).use { c ->
            while (c.moveToNext()) {
                val obj = JSONObject().apply {
                    put("event_id", c.getString(c.getColumnIndexOrThrow("event_id")))
                    put("date", date)
                    put("is_dev_mode", isDevMode)
                    put("timestamp", c.getString(c.getColumnIndexOrThrow("timestamp")))
                    put("event_type", c.getString(c.getColumnIndexOrThrow("event_type")))
                    put("description", c.getString(c.getColumnIndexOrThrow("description")))
                    put("points", if (c.getColumnIndex("points") != -1) c.getInt(c.getColumnIndex("points")) else 0)
                    put("evidence_id", if (c.getColumnIndex("evidence_id") != -1) c.getString(c.getColumnIndex("evidence_id")).orEmpty() else "")
                    put("related_task_id", if (c.getColumnIndex("related_task_id") != -1) c.getString(c.getColumnIndex("related_task_id")).orEmpty() else "")
                    val auditRaw = if (c.getColumnIndex("audit_json") != -1) c.getString(c.getColumnIndex("audit_json")).orEmpty() else ""
                    val auditObj = if (auditRaw.isNotBlank()) runCatching { JSONObject(auditRaw) }.getOrDefault(JSONObject()) else JSONObject()
                    put("audit", auditObj)
                }
                arr.put(obj)
            }
        }
        return arr
    }

    fun getDayState(date: String, isDevMode: Boolean): JSONObject {
        ensureDay(date, isDevMode)
        val dev = if (isDevMode) 1 else 0
        readableDatabase.rawQuery(
            "SELECT * FROM daily_state WHERE date = ? AND is_dev_mode = ?",
            arrayOf(date, dev.toString())
        ).use { c ->
            if (c.moveToFirst()) {
                val score = getScore(date, isDevMode)
                val streakDays = calculateStreakDays(isDevMode)
                val scoreEvents = getScoreEvents(date, isDevMode)
                val eventPoints = (0 until scoreEvents.length()).map { scoreEvents.getJSONObject(it).optInt("points", 0) }
                val gymPenaltyPoints = (0 until scoreEvents.length())
                    .map { scoreEvents.getJSONObject(it) }
                    .filter { it.optString("event_type") == "GYM_SKIPPED" }
                    .sumOf { -it.optInt("points", 0).coerceAtMost(0) }
                val verifiedPts = score // Same APK ledger projection on Home, Tasks, Rewards and restrictions.
                val provisionalPts = if (!isDevMode && hasFreshServerClock() && getConfigValue("server_shared_score_date", "") == date) {
                    getConfigValue("server_provisional_points", "0").toIntOrNull() ?: 0
                } else 0
                val focusIdx = c.getColumnIndex("focus_sessions_count")
                val journalIdx = c.getColumnIndex("journal_text")
                val themeIdx = c.getColumnIndex("appearance_mode")
                return JSONObject().apply {
                    put("date", date)
                    put("is_dev_mode", isDevMode)
                    put("score", score)
                    put("penalty_points", DisciplineScoringRules.calculateDailyDeductions(eventPoints))
                    put("gym_penalty_points", gymPenaltyPoints)
                    put("verified_points", verifiedPts)
                    put("provisional_points", provisionalPts)
                    put("streak_days", streakDays)
                    put("score_mode", DisciplineScoringRules.evaluateScoreMode(score))
                    put("score_state", DisciplineScoringRules.evaluateScoreState(score))
                    put("wakeup_camera", c.getInt(c.getColumnIndexOrThrow("wakeup_camera")))
                    put("wakeup_camera_path", c.getString(c.getColumnIndexOrThrow("wakeup_camera_path")) ?: "")
                    put("wakeup_bt", c.getInt(c.getColumnIndexOrThrow("wakeup_bt")))
                    put("wakeup_bt_device", c.getString(c.getColumnIndexOrThrow("wakeup_bt_device")) ?: "")
                    put("wakeup_track", c.getInt(c.getColumnIndexOrThrow("wakeup_track")))
                    put("wakeup_track_pct", c.getDouble(c.getColumnIndexOrThrow("wakeup_track_pct")))
                    put("wakeup_status", c.getString(c.getColumnIndexOrThrow("wakeup_status")))
                    put("wakeup_completed_at", c.getString(c.getColumnIndexOrThrow("wakeup_completed_at")) ?: "")
                    put("snooze_count", c.getInt(c.getColumnIndexOrThrow("snooze_count")))
                    put("bath_status", c.getString(c.getColumnIndexOrThrow("bath_status")))
                    put("bath_completed_at", c.getString(c.getColumnIndexOrThrow("bath_completed_at")) ?: "")
                    put("gym_status", c.getString(c.getColumnIndexOrThrow("gym_status")))
                    put("gym_duration_min", c.getInt(c.getColumnIndexOrThrow("gym_duration_min")))
                    put("hourly_completed", c.getInt(c.getColumnIndexOrThrow("hourly_completed")))
                    put("sync_status", runCatching {
                        val dbSyncStatus = c.getString(c.getColumnIndexOrThrow("sync_status")) ?: ""
                        val receiptStr = getConfigValue("pc_ledger_ack_$date", "")
                        if (receiptStr.isNotBlank()) {
                            val receipt = JSONObject(receiptStr)
                            if (receipt.optBoolean("accepted") && receipt.optString("authority") == "ANDROID" &&
                                receipt.optString("date") == date && receipt.optInt("confirmed_balance", Int.MIN_VALUE) == score) {
                                return@runCatching "SYNCED ✅"
                            }
                        }
                        if (dbSyncStatus.startsWith("SYNCED")) "SYNCED ✅"
                        else "LOCAL CONFIRMED; PC PENDING"
                    }.getOrDefault("LOCAL CONFIRMED; PC PENDING"))
                    put("focus_sessions_count", if (focusIdx >= 0) c.getInt(focusIdx) else 0)
                    put("journal_text", if (journalIdx >= 0) (c.getString(journalIdx) ?: "") else "")
                    put("appearance_mode", if (themeIdx >= 0) (c.getString(themeIdx) ?: "DARK") else "DARK")
                }
            }
        }
        return JSONObject()
    }

    fun exportLocalEvidencePackage(date: String, isDevMode: Boolean): File {
        val folder = File(appContext.filesDir, "STRICTRECORDS/$date")
        File(folder, "WakeUp").mkdirs()
        File(folder, "Hourly").mkdirs()
        File(folder, "Gym").mkdirs()
        File(folder, "Reports").mkdirs()

        val dailyFile = File(folder, "DailyRecord.json")
        dailyFile.writeText(getDayState(date, isDevMode).toString(2))

        val eventsFile = File(folder, "ScoreEvents.json")
        eventsFile.writeText(getScoreEvents(date, isDevMode).toString(2))
        return folder
    }

    fun syncWithCentralServer(serverUrl: String, date: String, isDevMode: Boolean): String {
        val registration = getDeviceRegistration() ?: return "DEVICE_NOT_REGISTERED"
        val deviceId = registration.optString("device_id", "").trim()
        val deviceToken = registration.optString("device_token", "")
        if (deviceId.isEmpty() || deviceToken.isEmpty()) return "DEVICE_NOT_REGISTERED"

        val items = JSONArray()
        readableDatabase.rawQuery(
            "SELECT * FROM sync_queue WHERE status = 'PENDING_SYNC'",
            emptyArray()
        ).use { c ->
            while (c.moveToNext()) {
                items.put(
                    JSONObject().apply {
                        put("sync_id", c.getString(c.getColumnIndexOrThrow("sync_id")))
                        put("payload_type", c.getString(c.getColumnIndexOrThrow("payload_type")))
                        put("payload", JSONObject(c.getString(c.getColumnIndexOrThrow("payload_json"))))
                    }
                )
            }
        }
        val candidates = listOf(
            serverUrl,
            "http://127.0.0.1:8765",
            "http://10.0.2.2:8765",
            "http://192.168.1.100:8765"
        ).distinct()

        for (base in candidates) {
            val endpointPaths = listOf("/api/device/sync", "/api/sync/batch")
            for (ep in endpointPaths) {
                try {
                    val url = URL("${base.trimEnd('/')}$ep")
                    val conn = (url.openConnection() as HttpURLConnection).apply {
                        requestMethod = "POST"
                        connectTimeout = 3000
                        readTimeout = 3000
                        doOutput = true
                        setRequestProperty("Content-Type", "application/json")
                    }
                    val body = JSONObject().apply {
                        put("device_id", deviceId)
                        put("device_token", deviceToken)
                        put("items", items)
                        put("ledger_snapshot", buildAuthoritativeLedgerSnapshot(date, isDevMode))
                    }
                    OutputStreamWriter(conn.outputStream).use { it.write(body.toString()) }
                    if (conn.responseCode == 403) {
                        val dev = if (isDevMode) 1 else 0
                        writableDatabase.execSQL(
                            "UPDATE daily_state SET sync_status = 'BLOCKED (REVOKED)' WHERE date = ? AND is_dev_mode = ?",
                            arrayOf<Any>(date, dev)
                        )
                        return "BLOCKED (DEVICE TRUST REVOKED)"
                    }
                    if (conn.responseCode in 200..299) {
                        val respJson = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                        val ledgerAccepted = applyAuthoritativeScoreSnapshot(respJson)
                        val results = respJson.optJSONArray("processed_sync_ids") ?: JSONArray()
                        for (i in 0 until results.length()) writableDatabase.execSQL(
                            "UPDATE sync_queue SET status='SYNCED' WHERE sync_id=?", arrayOf(results.optString(i)))
                        exportLocalEvidencePackage(date, isDevMode)
                        return if (ledgerAccepted) "SYNCED ✅" else "PENDING LEDGER VERIFICATION"
                    }
                } catch (_: Exception) {
                }
            }
        }
        return "PENDING SYNC (OFFLINE)"
    }

    fun registerDeviceWithServer(serverUrl: String = "http://127.0.0.1:8765"): Boolean {
        return try {
            val url = URL("${serverUrl.trimEnd('/')}/api/device/register")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 3000
                readTimeout = 3000
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
            }
            val body = JSONObject().apply {
                put("device_id", "android_client")
                put("device_name", "Android Device")
                put("platform", "ANDROID")
                put("app_version", "2.0.0")
                put("battery_level", 100)
                put("user_id", getConfigValue("user_id", ""))
                put("transport", "LOCAL_WIFI")
            }
            OutputStreamWriter(conn.outputStream).use { it.write(body.toString()) }
            conn.responseCode in 200..299
        } catch (_: Exception) {
            false
        }
    }

    fun buildBluetoothTransferPayload(date: String = nowDate(), isDevMode: Boolean = false): JSONObject {
        val accountId = getConfigValue("account_id", "user_lok").ifBlank { "user_lok" }
        val devId: String = getDeviceRegistration()?.optString("device_id")?.takeIf { it.isNotBlank() } ?: getStableDeviceId(appContext)
        val score = getScore(date, isDevMode)

        val proofReviews = getDailyProofReviews(date)
        val proofReviewsList = JSONArray()
        for (pr in proofReviews) {
            proofReviewsList.put(JSONObject().apply {
                put("review_id", pr.optString("review_id"))
                put("evidence_id", pr.optString("evidence_id"))
                put("status", pr.optString("status"))
                put("decision", pr.optString("decision"))
                put("points_awarded", pr.optInt("points_awarded"))
                put("date", pr.optString("date"))
                put("task_code", pr.optString("task_code"))
                put("activity_id", pr.optString("activity_id"))
                put("submission_id", pr.optString("submission_id"))
                put("reviewed_by", pr.optString("reviewed_by"))
                put("submitted_at", pr.optString("submitted_at"))
                put("reviewed_at", pr.optString("reviewed_at"))
            })
        }

        val activities = getDailyActivities(date)
        val actList = JSONArray()
        for (a in activities) {
            actList.put(JSONObject().apply {
                put("activity_id", a.activityId)
                put("date", a.date)
                put("type", a.type)
                put("title", a.title)
                put("scheduled_time", a.scheduledTime)
                put("window_start", a.windowStart)
                put("window_end", a.windowEnd)
                put("notification_time", a.notificationTime)
                put("schedule_id", a.scheduleId)
                put("schedule_version", a.scheduleVersion)
                put("submitted_at", a.submittedAt ?: JSONObject.NULL)
                put("status", a.status)
                put("points", a.points)
                put("attendance_status", if (a.status == "VERIFIED") "PRESENT" else if (a.status == "MISSED") "ABSENT" else "PENDING")
                put("validation_status", if (a.status == "VERIFIED") "APPROVED" else "PENDING")
            })
        }

        return JSONObject().apply {
            put("account_id", accountId)
            put("device_id", devId)
            put("date", date)
            put("confirmed_score", score)
            put("points_breakdown", getPointsBreakdown(date, isDevMode))
            put("ledger_snapshot", buildAuthoritativeLedgerSnapshot(date, isDevMode))
            put("transfer_version", BluetoothTransferProtocol.VERSION)
            put("proof_reviews", proofReviewsList)
            put("scheduled_activities", actList)
            put("exported_at", nowIso())
        }
    }

    fun getPointsBreakdown(date: String = nowDate(), isDevMode: Boolean = false): JSONObject {
        val events = getScoreEvents(date, isDevMode)
        var wake = 0
        var bath = 0
        var hourly = 0
        var gym = 0
        var focus = 0
        var penalties = 0
        for (i in 0 until events.length()) {
            val ev = events.getJSONObject(i)
            val pts = ev.optInt("points", 0)
            val type = ev.optString("event_type", "").uppercase(Locale.US)
            val desc = ev.optString("description", "").uppercase(Locale.US)
            val audit = ev.optJSONObject("audit") ?: JSONObject()
            val taskCode = audit.optString("task_code", "").uppercase(Locale.US)
            val relId = ev.optString("related_task_id", "").uppercase(Locale.US)
            if (pts < 0) {
                penalties += pts
            } else if (type.contains("WAKE") || desc.contains("WAKE") || taskCode.contains("WAKE") || relId.contains("WAKE") || type.contains("SLEEP") || desc.contains("SLEEP")) {
                wake += pts
            } else if (type.contains("BATH") || desc.contains("BATH") || taskCode.contains("BATH") || relId.contains("BATH")) {
                bath += pts
            } else if (type.contains("GYM") || desc.contains("GYM") || taskCode.contains("GYM") || relId.contains("GYM")) {
                gym += pts
            } else if (type.contains("HOURLY") || desc.contains("HOURLY") || taskCode.contains("HOURLY") || relId.contains("HOURLY")) {
                hourly += pts
            } else if (type.contains("FOCUS") || type.contains("DEEP_WORK") || type.contains("TASK") || relId.contains("FOCUS")) {
                focus += pts
            } else {
                hourly += pts
            }
        }
        return JSONObject().apply {
            put("wake_up", wake)
            put("bath", bath)
            put("hourly", hourly)
            put("gym", gym)
            put("focus", focus)
            put("penalties", penalties)
            put("confirmed_score", getScore(date, isDevMode))
        }
    }

    fun executeBluetoothTransfer(
        sessionCode: String,
        peerAddress: String,
        onProgress: (String) -> Unit,
        onComplete: (Boolean, String, JSONObject?) -> Unit
    ) {
        BluetoothTransferClient.send(appContext, this, sessionCode, peerAddress, onProgress, onComplete)
    }

    // =========================================================================
    // ADMIN ECOSYSTEM: PROOFS, RBAC, CONTROLLED ADJUSTMENTS & AUDIT LOGS
    // =========================================================================

    fun hasProofReviewsCategoryColumn(): Boolean = hasColumn(readableDatabase, "proof_reviews", "category")

    fun getScheduledActivityCategory(activityId: String): String {
        if (activityId.isBlank()) return ""
        return try {
            readableDatabase.rawQuery(
                "SELECT details_json FROM scheduled_activities WHERE activity_id = ?",
                arrayOf(activityId)
            ).use { c ->
                if (c.moveToFirst()) {
                    val details = JSONObject(c.getString(0) ?: "{}")
                    listOf("category", "activity_category", "primary_activity")
                        .map { details.optString(it).trim() }
                        .firstOrNull { it.isNotBlank() } ?: ""
                } else ""
            }
        } catch (_: Exception) { "" }
    }

    fun extractReviewCategory(c: Cursor): String {
        val catCol = c.getColumnIndex("category")
        if (catCol != -1) {
            val v = c.getString(catCol).orEmpty().trim()
            if (v.isNotBlank()) return v
        }
        val auditCol = c.getColumnIndex("audit_json")
        if (auditCol != -1) {
            val auditStr = c.getString(auditCol).orEmpty().trim()
            val audit: JSONObject? = if (auditStr.isNotEmpty()) runCatching { JSONObject(auditStr) }.getOrNull() else null
            if (audit != null) {
                val v = listOf("category", "activity_category", "primary_activity")
                    .map { key: String -> audit.optString(key, "").trim() }
                    .firstOrNull { it.isNotBlank() } ?: ""
                if (v.isNotBlank()) return v
            }
        }
        val actIdCol = c.getColumnIndex("activity_id")
        if (actIdCol != -1) {
            val actId = c.getString(actIdCol).orEmpty().trim()
            if (actId.isNotBlank()) {
                val v = getScheduledActivityCategory(actId)
                if (v.isNotBlank()) return v
            }
        }
        val subIdCol = c.getColumnIndex("submission_id")
        if (subIdCol != -1) {
            val subId = c.getString(subIdCol).orEmpty().trim()
            if (subId.isNotBlank()) {
                val actId = try {
                    readableDatabase.rawQuery("SELECT activity_id FROM scheduled_activities WHERE details_json LIKE ?", arrayOf("%$subId%")).use {
                        if (it.moveToFirst()) it.getString(0) else ""
                    }
                } catch (_: Exception) { "" }
                if (actId.isNotBlank()) {
                    val v = getScheduledActivityCategory(actId)
                    if (v.isNotBlank()) return v
                }
            }
        }
        val titleCol = c.getColumnIndex("task_title")
        if (titleCol != -1) {
            val title = c.getString(titleCol).orEmpty()
            if (title.contains(" Checkpoint: ")) {
                val candidate = title.substringAfter(" Checkpoint: ").trim()
                if (candidate.isNotBlank() && !candidate.equals("Activity", ignoreCase = true) && !candidate.equals("Unavailable", ignoreCase = true)) {
                    return candidate
                }
            }
        }
        return "Unavailable"
    }

    fun registerProofForReview(
        evidenceId: String,
        date: String,
        taskCode: String,
        taskTitle: String,
        filePath: String,
        priority: String = "NORMAL",
        category: String = "",
        activityId: String = "",
        submissionId: String = "",
        description: String = ""
    ) {
        if (!hasValidRetainedProofImage(filePath)) return
        try {
            val auditObj = JSONObject().apply {
                if (category.isNotBlank()) put("category", category)
                if (description.isNotBlank()) put("description", description)
                if (activityId.isNotBlank()) put("activity_id", activityId)
                if (submissionId.isNotBlank()) put("submission_id", submissionId)
            }
            val cv = ContentValues().apply {
                put("evidence_id", evidenceId)
                put("date", date)
                put("user_id", getConfigValue("user_id", ""))
                put("task_code", taskCode)
                put("task_title", taskTitle)
                put("status", "PENDING")
                put("file_path", filePath)
                put("submitted_at", nowIso())
                put("priority", priority)
                if (activityId.isNotBlank()) put("activity_id", activityId)
                if (submissionId.isNotBlank()) put("submission_id", submissionId)
                put("audit_json", auditObj.toString())
                if (hasProofReviewsCategoryColumn() && category.isNotBlank()) {
                    put("category", category)
                }
            }
            writableDatabase.insertWithOnConflict("proof_reviews", null, cv, SQLiteDatabase.CONFLICT_IGNORE)
        } catch (_: Exception) {}
    }

    fun seedDefaultProofsIfNeeded(date: String = nowDate()) {
        // Dummy data seeding removed: Application starts fresh with 0 proofs until submitted by user.
    }

    private fun hashAdminSecret(secret: String, salt: String = "s7r1ct_s4lt_android"): String {
        val md = java.security.MessageDigest.getInstance("SHA-256")
        val bytes = md.digest((salt + secret).toByteArray(Charsets.UTF_8))
        return bytes.joinToString("") { "%02x".format(it) }
    }

    /** Apply the owner's requested temporary PIN once, never after a later PIN change. */
    private fun applyRequestedAdminPinResetIfNeeded() {
        val resetRevision = "owner_requested_20261024_v1"
        if (getConfigValue("admin_pin_reset_revision", "") == resetRevision) return
        val store = writableDatabase
        store.beginTransaction()
        try {
            val applied = store.rawQuery(
                "SELECT config_value FROM system_config WHERE config_key='admin_pin_reset_revision'", null
            ).use { it.moveToFirst() && it.getString(0) == resetRevision }
            if (!applied) {
                val changes = mapOf(
                    "admin_pin_hash" to hashAdminSecret("1234"),
                    "admin_pin_failed_attempts" to "0",
                    "admin_pin_lockout_until_ms" to "0",
                    "admin_pin_temporary_default_authorized" to "true",
                    "admin_pin_reset_revision" to resetRevision
                )
                for ((key, value) in changes) {
                    val config = ContentValues().apply { put("config_key", key); put("config_value", value) }
                    check(store.insertWithOnConflict("system_config", null, config, SQLiteDatabase.CONFLICT_REPLACE) != -1L)
                }
                store.insertOrThrow("admin_audit_logs", null, ContentValues().apply {
                    put("audit_id", "aud_" + UUID.randomUUID().toString().replace("-", "").take(10))
                    put("admin_user", "owner")
                    put("action_type", "ADMIN_PIN_RESET_USER_REQUEST")
                    put("field_changed", "pin_hash")
                    put("old_value", "[REDACTED_PREVIOUS_KEY]")
                    put("new_value", "[REDACTED_TEMPORARY_KEY]")
                    put("timestamp", nowIso())
                    put("device_id", "android_client")
                })
            }
            store.setTransactionSuccessful()
        } finally {
            store.endTransaction()
        }
    }

    fun isDefaultAdminPin(): Boolean {
        applyRequestedAdminPinResetIfNeeded()
        val storedHash = getConfigValue("admin_pin_hash", "")
        val defaultHash = hashAdminSecret("1234")
        return storedHash.isEmpty() || storedHash == defaultHash
    }

    fun isTemporaryAdminPinAuthorized(): Boolean =
        isDefaultAdminPin() && getConfigValue("admin_pin_temporary_default_authorized", "false") == "true"

    fun getPinLockoutRemainingSeconds(): Int {
        val lockoutUntilMs = getConfigValue("admin_pin_lockout_until_ms", "0").toLongOrNull() ?: 0L
        val remainingMs = (lockoutUntilMs - authoritativeNowMs()).coerceAtLeast(0L)
        return if (remainingMs > 0L) ((remainingMs + 999L) / 1000L).toInt() else 0
    }

    fun verifyAdminPin(pin: String): Boolean {
        applyRequestedAdminPinResetIfNeeded()
        if (getPinLockoutRemainingSeconds() > 0) {
            return false
        }
        val p = pin.trim()
        val defaultHash = hashAdminSecret("1234")
        val storedHash = getConfigValue("admin_pin_hash", defaultHash)
        val inputHash = hashAdminSecret(p)

        val valid = (inputHash == storedHash) || (storedHash == defaultHash && p == "1234")
        if (valid) {
            setConfigValue("admin_pin_failed_attempts", "0")
            setConfigValue("admin_pin_lockout_until_ms", "0")
        } else {
            val failedAttempts = ((getConfigValue("admin_pin_failed_attempts", "0").toIntOrNull() ?: 0) + 1).coerceAtMost(5)
            if (failedAttempts >= 5) {
                setConfigValue("admin_pin_failed_attempts", "0")
                setConfigValue("admin_pin_lockout_until_ms", (authoritativeNowMs() + 60_000L).toString())
            } else {
                setConfigValue("admin_pin_failed_attempts", failedAttempts.toString())
            }
        }
        recordAdminAudit(
            adminUser = if (valid) "admin" else "unknown",
            actionType = if (valid) "ADMIN_LOGIN" else "ADMIN_LOGIN_FAILED",
            fieldChanged = "admin_session",
            oldValue = "NONE",
            newValue = if (valid) "AUTHENTICATED" else "DENIED"
        )
        return valid
    }

    fun changeAdminPin(currentPin: String, newPin: String): Boolean {
        if (!verifyAdminPin(currentPin)) return false
        val n = newPin.trim()
        if (n.length < 4 || !n.all { it.isDigit() }) return false
        val newHash = hashAdminSecret(n)
        setConfigValue("admin_pin_hash", newHash)
        if (newHash != hashAdminSecret("1234")) setConfigValue("admin_pin_temporary_default_authorized", "false")
        recordAdminAudit(
            adminUser = "admin",
            actionType = "ADMIN_PIN_CHANGED",
            fieldChanged = "pin_hash",
            oldValue = "[REDACTED_PREVIOUS_KEY]",
            newValue = "[REDACTED_UPDATED_KEY]"
        )
        return true
    }

    fun getUserDisplayName(): String {
        return getConfigValue("user_display_name", "LOK ADHITHYA G")
    }

    fun recordAdminAudit(
        adminUser: String,
        actionType: String,
        fieldChanged: String,
        oldValue: String,
        newValue: String
    ) {
        try {
            val cv = ContentValues().apply {
                put("audit_id", "aud_" + UUID.randomUUID().toString().replace("-", "").take(10))
                put("admin_user", adminUser)
                put("action_type", actionType)
                put("field_changed", fieldChanged)
                put("old_value", oldValue)
                put("new_value", newValue)
                put("timestamp", nowIso())
                put("device_id", "android_client")
            }
            writableDatabase.insert("admin_audit_logs", null, cv)
            logStructured(nowDate(), false, actionType, "$fieldChanged: $oldValue -> $newValue")
        } catch (_: Exception) {}
    }

    fun getPendingProofs(): List<JSONObject> {
        val list = mutableListOf<JSONObject>()
        try {
            readableDatabase.rawQuery(
                "SELECT * FROM proof_reviews WHERE status = 'PENDING' ORDER BY submitted_at DESC",
                null
            ).use { c ->
                while (c.moveToNext()) {
                    val category = extractReviewCategory(c)
                    val actId = if (c.getColumnIndex("activity_id") != -1) c.getString(c.getColumnIndex("activity_id")).orEmpty() else ""
                    val subId = if (c.getColumnIndex("submission_id") != -1) c.getString(c.getColumnIndex("submission_id")).orEmpty() else ""
                    val obj = JSONObject().apply {
                        put("evidence_id", c.getString(c.getColumnIndexOrThrow("evidence_id")))
                        put("date", c.getString(c.getColumnIndexOrThrow("date")))
                        put("user_id", c.getString(c.getColumnIndexOrThrow("user_id")))
                        put("task_code", c.getString(c.getColumnIndexOrThrow("task_code")))
                        put("task_title", c.getString(c.getColumnIndexOrThrow("task_title")))
                        put("status", c.getString(c.getColumnIndexOrThrow("status")))
                        put("file_path", c.getString(c.getColumnIndexOrThrow("file_path")))
                        put("submitted_at", c.getString(c.getColumnIndexOrThrow("submitted_at")))
                        put("priority", c.getString(c.getColumnIndexOrThrow("priority")))
                        put("category", category)
                        put("activity_id", actId)
                        put("submission_id", subId)
                    }
                    list.add(obj)
                }
            }
        } catch (_: Exception) {}
        return list
    }

    /** A privacy-safe report projection for this account. Evidence paths are never returned. */
    fun getDailyProofReviews(date: String): List<JSONObject> {
        val list = mutableListOf<JSONObject>()
        val activeUserId = getConfigValue("user_id", "")
        try {
            val query = if (activeUserId.isNotBlank()) {
                "SELECT * FROM proof_reviews WHERE date = ? AND (user_id = ? OR user_id = '' OR user_id = 'user_lok') ORDER BY submitted_at ASC"
            } else {
                "SELECT * FROM proof_reviews WHERE date = ? ORDER BY submitted_at ASC"
            }
            val args = if (activeUserId.isNotBlank()) arrayOf(date, activeUserId) else arrayOf(date)
            readableDatabase.rawQuery(query, args).use { c ->
                while (c.moveToNext()) {
                    val category = extractReviewCategory(c)
                    val actId = if (c.getColumnIndex("activity_id") != -1) c.getString(c.getColumnIndex("activity_id")).orEmpty() else ""
                    val subId = if (c.getColumnIndex("submission_id") != -1) c.getString(c.getColumnIndex("submission_id")).orEmpty() else ""
                    list.add(JSONObject().apply {
                        put("evidence_id", c.getString(c.getColumnIndexOrThrow("evidence_id")) ?: "")
                        put("task_code", c.getString(c.getColumnIndexOrThrow("task_code")) ?: "")
                        put("task_title", c.getString(c.getColumnIndexOrThrow("task_title")) ?: "")
                        put("status", c.getString(c.getColumnIndexOrThrow("status")) ?: "")
                        put("submitted_at", c.getString(c.getColumnIndexOrThrow("submitted_at")) ?: "")
                        put("reviewed_at", if (c.getColumnIndex("reviewed_at") != -1) c.getString(c.getColumnIndex("reviewed_at")).orEmpty() else "")
                        put("reviewed_by", if (c.getColumnIndex("reviewed_by") != -1) c.getString(c.getColumnIndex("reviewed_by")).orEmpty() else "")
                        put("decision", if (c.getColumnIndex("decision") != -1) c.getString(c.getColumnIndex("decision")).orEmpty() else "")
                        put("points_awarded", if (c.getColumnIndex("points_awarded") != -1) c.getInt(c.getColumnIndex("points_awarded")) else 0)
                        put("reason_category", if (c.getColumnIndex("reason_category") != -1) c.getString(c.getColumnIndex("reason_category")).orEmpty() else "")
                        put("reason_note", if (c.getColumnIndex("reason_note") != -1) c.getString(c.getColumnIndex("reason_note")).orEmpty() else "")
                        put("category", category)
                        put("activity_id", actId)
                        put("submission_id", subId)
                    })
                }
            }
        } catch (_: Exception) {}
        return list
    }

    fun getDailyProofEvaluations(date: String, isDevMode: Boolean = false): List<JSONObject> {
        val list = mutableListOf<JSONObject>()
        try {
            readableDatabase.rawQuery(
                "SELECT * FROM proof_evaluations WHERE date = ? AND is_dev_mode = ? ORDER BY created_at DESC",
                arrayOf(date, if (isDevMode) "1" else "0")
            ).use { c ->
                while (c.moveToNext()) {
                    list.add(JSONObject().apply {
                        put("evaluation_id", c.getString(c.getColumnIndexOrThrow("evaluation_id")))
                        put("submission_id", c.getString(c.getColumnIndexOrThrow("submission_id")))
                        put("activity_id", c.getString(c.getColumnIndexOrThrow("activity_id")))
                        put("task_category", c.getString(c.getColumnIndexOrThrow("task_category")))
                        put("date", c.getString(c.getColumnIndexOrThrow("date")))
                        put("status", c.getString(c.getColumnIndexOrThrow("status")))
                        put("points_awarded", c.getInt(c.getColumnIndexOrThrow("points_awarded")))
                        put("evaluator_output_json", c.getString(c.getColumnIndexOrThrow("evaluator_output_json")))
                    })
                }
            }
        } catch (_: Exception) {}
        return list
    }

    fun approveProof(evidenceId: String, adminUser: String = "Administrator", points: Int = 3): Boolean {
        var date = nowDate()
        var taskTitle = "Task Proof"
        var status = ""
        var evidencePath = ""
        var activityId = ""
        var taskCode = ""
        var submissionId = ""
        try {
            readableDatabase.rawQuery(
                "SELECT date, task_title, status, file_path, activity_id, task_code, submission_id FROM proof_reviews WHERE evidence_id = ?",
                arrayOf(evidenceId)
            ).use { c ->
                if (c.moveToFirst()) {
                    date = c.getString(0)
                    taskTitle = c.getString(1)
                    status = c.getString(2)
                    evidencePath = c.getString(3).orEmpty()
                    activityId = c.getString(4).orEmpty()
                    taskCode = c.getString(5).orEmpty()
                    submissionId = c.getString(6).orEmpty()
                } else {
                    return false
                }
            }

            val hasFile = evidencePath.isNotBlank() && File(evidencePath).let { it.exists() && it.length() > 0 }
            if (evidencePath.isNotBlank() && !hasFile && !hasValidRetainedProofImage(evidencePath)) return false

            var approvedPoints = points
            if (approvedPoints <= 0) {
                if (activityId.isNotBlank()) {
                    readableDatabase.rawQuery(
                        "SELECT configured_points FROM scheduled_activities WHERE activity_id = ?",
                        arrayOf(activityId)
                    ).use {
                        if (it.moveToFirst()) approvedPoints = it.getInt(0)
                    }
                }
                if (approvedPoints <= 0) {
                    readableDatabase.rawQuery(
                        "SELECT a.configured_points FROM scheduled_activities a JOIN proof_reviews p ON a.activity_id = p.activity_id WHERE p.evidence_id = ?",
                        arrayOf(evidenceId)
                    ).use {
                        if (it.moveToFirst()) approvedPoints = it.getInt(0)
                    }
                }
                if (approvedPoints <= 0) {
                    approvedPoints = when (taskCode.uppercase(Locale.US)) {
                        "GYM" -> 15
                        "TRAVEL" -> 10
                        "BATH" -> ProductPolicy.BATH_POINTS_PER_OCCURRENCE
                        "WAKE_UP", "WAKEUP" -> 15
                        else -> 8
                    }
                }
            }
            val isWakeUp = taskCode.uppercase(Locale.US) in setOf("WAKE_UP", "WAKEUP") || activityId.contains("WAKEUP")
            if (isWakeUp && hasWakeUpBonusBeenAwarded(date)) {
                approvedPoints = 0
            }
            approvedPoints = approvedPoints.coerceAtLeast(0)

            invalidateApprovedProofCache(evidenceId)

            if (status == "APPROVED") {
                val hasEvent = readableDatabase.rawQuery(
                    "SELECT 1 FROM score_events WHERE audit_json LIKE ?",
                    arrayOf("%$evidenceId%")
                ).use { it.moveToFirst() }
                val activityOrSubAwarded = if (activityId.isNotBlank() || submissionId.isNotBlank()) {
                    readableDatabase.rawQuery(
                        "SELECT 1 FROM score_events WHERE date = ? AND is_dev_mode = 0 AND points > 0 AND (related_task_id = ? OR audit_json LIKE ? OR audit_json LIKE ?)",
                        arrayOf(date, activityId, "%\"activity_id\": \"$activityId\"%", "%\"submission_id\": \"$submissionId\"%")
                    ).use { it.moveToFirst() }
                } else false
                if (!hasEvent && !activityOrSubAwarded && approvedPoints > 0) {
                    val audit = JSONObject().apply {
                        put("evidence_id", evidenceId)
                        put("reviewed_by", adminUser)
                        put("action", "ADMIN_APPROVAL")
                        put("activity_id", activityId)
                        put("task_code", taskCode)
                        put("submission_id", submissionId)
                        put("approval_source", "ANDROID_ADMIN")
                    }
                    addScoreEvent(date, false, "PROOF_APPROVED", "Admin approved proof for $taskTitle (+$approvedPoints)", approvedPoints, audit)
                }
                return true
            }
            if (status != "PENDING") return false

            if (approvedPoints > 0 && (activityId.isNotBlank() || submissionId.isNotBlank())) {
                val activityOrSubAwarded = readableDatabase.rawQuery(
                    "SELECT 1 FROM score_events WHERE date = ? AND is_dev_mode = 0 AND points > 0 AND (related_task_id = ? OR audit_json LIKE ? OR audit_json LIKE ?)",
                    arrayOf(date, activityId, "%\"activity_id\": \"$activityId\"%", "%\"submission_id\": \"$submissionId\"%")
                ).use { it.moveToFirst() }
                if (activityOrSubAwarded) {
                    approvedPoints = 0
                }
            }

            val cv = ContentValues().apply {
                put("status", "APPROVED")
                put("reviewed_at", nowIso())
                put("reviewed_by", adminUser)
                put("decision", "APPROVED")
                put("points_awarded", approvedPoints)
                put("reason_note", if (approvedPoints == 0) "Approved attachment (points already awarded for $activityId)" else "Approved by Android Admin")
            }
            val rows = writableDatabase.update("proof_reviews", cv, "evidence_id = ? AND status = 'PENDING'", arrayOf(evidenceId))
            if (rows > 0) {
                invalidateApprovedProofCache(evidenceId)
                if (activityId.isNotBlank()) {
                    val actCv = ContentValues().apply {
                        put("status", "VERIFIED")
                        put("attendance_status", "PRESENT")
                        put("validation_status", "APPROVED")
                        put("points", approvedPoints)
                    }
                    writableDatabase.update("scheduled_activities", actCv, "activity_id = ?", arrayOf(activityId))
                }
                if (approvedPoints > 0) {
                    val audit = JSONObject().apply {
                        put("evidence_id", evidenceId)
                        put("reviewed_by", adminUser)
                        put("action", "ADMIN_APPROVAL")
                        put("activity_id", activityId)
                        put("task_code", taskCode)
                        put("submission_id", submissionId)
                        put("approval_source", "ANDROID_ADMIN")
                    }
                    addScoreEvent(date, false, "PROOF_APPROVED", "Admin approved proof for $taskTitle (+$approvedPoints)", approvedPoints, audit)
                }
                if (taskCode.uppercase(Locale.US) == "GYM" || activityId.contains("GYM")) {
                    reconcileHourlyGymConflicts(date)
                }
                recordAdminAudit(adminUser, "PROOF_APPROVED", "proof:$evidenceId", "PENDING", "APPROVED")
                return true
            }
        } catch (_: Exception) {}
        return false
    }

    fun rejectProof(
        evidenceId: String,
        adminUser: String = "Administrator",
        reasonCategory: String,
        reasonNote: String = ""
    ): Boolean {
        try {
            var date = nowDate()
            var activityId = ""
            var taskCode = ""
            readableDatabase.rawQuery(
                "SELECT date, activity_id, task_code FROM proof_reviews WHERE evidence_id = ?",
                arrayOf(evidenceId)
            ).use { c ->
                if (c.moveToFirst()) {
                    date = c.getString(0) ?: nowDate()
                    activityId = c.getString(1).orEmpty()
                    taskCode = c.getString(2).orEmpty()
                }
            }

            val cv = ContentValues().apply {
                put("status", "REJECTED")
                put("reviewed_at", nowIso())
                put("reviewed_by", adminUser)
                put("decision", "REJECTED")
                put("points_awarded", 0)
                put("reason_category", reasonCategory)
                put("reason_note", reasonNote)
            }
            val rows = writableDatabase.update("proof_reviews", cv, "evidence_id = ?", arrayOf(evidenceId))
            if (rows > 0) {
                if (activityId.isNotBlank()) {
                    val actCv = ContentValues().apply {
                        put("status", "REJECTED")
                        put("validation_status", "REJECTED")
                    }
                    writableDatabase.update("scheduled_activities", actCv, "activity_id = ?", arrayOf(activityId))
                }
                if (taskCode.uppercase(Locale.US) == "GYM" || activityId.contains("GYM")) {
                    reconcileHourlyGymConflicts(date)
                }
                recordAdminAudit(adminUser, "PROOF_REJECTED", "proof:$evidenceId", "PENDING", "REJECTED ($reasonCategory)")
                enqueueSync("PROOF_REJECTED", JSONObject().put("evidence_id", evidenceId).put("reason", reasonCategory))
                return true
            }
        } catch (_: Exception) {}
        return false
    }

    fun adjustScoreControlled(
        date: String,
        isDevMode: Boolean,
        delta: Int,
        reason: String,
        adminUser: String = "Administrator"
    ): Int {
        require(reason.isNotBlank()) { "Controlled score adjustment requires a mandatory explanation." }
        if (delta == 0) return getScore(date, isDevMode)
        val oldScore = getScore(date, isDevMode)
        val audit = JSONObject().apply {
            put("Admin", adminUser)
            put("approved_by", adminUser)
            put("approval_source", "AUTHENTICATED_HUMAN_ADMIN")
            put("Reason", reason)
            put("Adjustment", delta)
            put("OldScore", oldScore)
            put("is_admin_adjustment", true)
        }
        addScoreEvent(date, isDevMode, "ADMIN_SCORE_ADJUSTMENT", "Admin adjustment: $reason (${if (delta >= 0) "+$delta" else "$delta"})", delta, audit)
        recordAdminAudit(adminUser, "POINT_ADJUSTED", "final_score", oldScore.toString(), (oldScore + delta).toString())
        enqueueSync("ADMIN_SCORE_ADJUSTMENT", JSONObject().put("delta", delta).put("reason", reason))
        return getScore(date, isDevMode)
    }

    fun createDeductionProposal(
        date: String,
        originatingDevice: String,
        eventType: String,
        proposedPoints: Int,
        reason: String,
        applicableRule: String = "DISCIPLINE_RULE",
        relatedTaskId: String = "",
        evidenceJson: String = "{}",
        proposalId: String = ""
    ): String {
        val pid = proposalId.ifBlank { "prop_" + UUID.randomUUID().toString().replace("-", "").take(16) }
        val now = nowIso()
        val cv = ContentValues().apply {
            put("proposal_id", pid)
            put("date", date)
            put("timestamp", now)
            put("originating_device", originatingDevice)
            put("event_type", eventType)
            put("related_task_id", relatedTaskId)
            put("applicable_rule", applicableRule)
            put("proposed_points", proposedPoints)
            put("reason", reason)
            put("evidence_json", evidenceJson)
            put("status", "AWAITING_CONFIRMATION")
            put("created_at", now)
            put("updated_at", now)
        }
        writableDatabase.insertWithOnConflict("deduction_proposals", null, cv, SQLiteDatabase.CONFLICT_IGNORE)
        return pid
    }

    fun getDeductionProposals(date: String? = null, status: String? = null, limit: Int = 50): JSONArray {
        val arr = JSONArray()
        val whereClauses = mutableListOf<String>()
        val args = mutableListOf<String>()
        if (!date.isNullOrBlank()) {
            whereClauses.add("date = ?")
            args.add(date)
        }
        if (!status.isNullOrBlank()) {
            whereClauses.add("status = ?")
            args.add(status)
        }
        val where = if (whereClauses.isNotEmpty()) whereClauses.joinToString(" AND ") else null
        val queryArgs = if (args.isNotEmpty()) args.toTypedArray() else null
        try {
            val limitStr = limit.coerceAtLeast(1).toString()
            readableDatabase.query(
                "deduction_proposals",
                null,
                where,
                queryArgs,
                null,
                null,
                "timestamp DESC",
                limitStr
            ).use { c ->
                while (c.moveToNext()) {
                    val obj = JSONObject().apply {
                        val pidIdx = c.getColumnIndex("proposal_id")
                        put("proposal_id", if (pidIdx != -1 && !c.isNull(pidIdx)) c.getString(pidIdx) else "")
                        val dateIdx = c.getColumnIndex("date")
                        put("date", if (dateIdx != -1 && !c.isNull(dateIdx)) c.getString(dateIdx) else "")
                        val tsIdx = c.getColumnIndex("timestamp")
                        put("timestamp", if (tsIdx != -1 && !c.isNull(tsIdx)) c.getString(tsIdx) else "")
                        val devIdx = c.getColumnIndex("originating_device")
                        put("originating_device", if (devIdx != -1 && !c.isNull(devIdx)) c.getString(devIdx) else "")
                        val evIdx = c.getColumnIndex("event_type")
                        put("event_type", if (evIdx != -1 && !c.isNull(evIdx)) c.getString(evIdx) else "")
                        val taskIdx = c.getColumnIndex("related_task_id")
                        put("related_task_id", if (taskIdx != -1 && !c.isNull(taskIdx)) c.getString(taskIdx) else "")
                        val ruleIdx = c.getColumnIndex("applicable_rule")
                        put("applicable_rule", if (ruleIdx != -1 && !c.isNull(ruleIdx)) c.getString(ruleIdx) else "")
                        val ptsIdx = c.getColumnIndex("proposed_points")
                        put("proposed_points", if (ptsIdx != -1 && !c.isNull(ptsIdx)) c.getInt(ptsIdx) else 0)
                        val reasonIdx = c.getColumnIndex("reason")
                        put("reason", if (reasonIdx != -1 && !c.isNull(reasonIdx)) c.getString(reasonIdx) else "")
                        val evJsonIdx = c.getColumnIndex("evidence_json")
                        put("evidence_json", if (evJsonIdx != -1 && !c.isNull(evJsonIdx)) c.getString(evJsonIdx) else "{}")
                        val stIdx = c.getColumnIndex("status")
                        put("status", if (stIdx != -1 && !c.isNull(stIdx)) c.getString(stIdx) else "")
                        val appByIdx = c.getColumnIndex("approved_by")
                        put("approved_by", if (appByIdx != -1 && !c.isNull(appByIdx)) c.getString(appByIdx) else "")
                        val appAtIdx = c.getColumnIndex("approved_at")
                        put("approved_at", if (appAtIdx != -1 && !c.isNull(appAtIdx)) c.getString(appAtIdx) else "")
                        val appSrcIdx = c.getColumnIndex("approval_source")
                        put("approval_source", if (appSrcIdx != -1 && !c.isNull(appSrcIdx)) c.getString(appSrcIdx) else "")
                        val rejIdx = c.getColumnIndex("rejection_reason")
                        put("rejection_reason", if (rejIdx != -1 && !c.isNull(rejIdx)) c.getString(rejIdx) else "")
                        val finIdx = c.getColumnIndex("finalized_event_id")
                        put("finalized_event_id", if (finIdx != -1 && !c.isNull(finIdx)) c.getString(finIdx) else "")
                        val crIdx = c.getColumnIndex("created_at")
                        put("created_at", if (crIdx != -1 && !c.isNull(crIdx)) c.getString(crIdx) else "")
                        val upIdx = c.getColumnIndex("updated_at")
                        put("updated_at", if (upIdx != -1 && !c.isNull(upIdx)) c.getString(upIdx) else "")
                    }
                    arr.put(obj)
                }
            }
        } catch (_: Exception) {}
        return arr
    }

    fun getDeductionProposal(proposalId: String): JSONObject? {
        readableDatabase.query("deduction_proposals",null,"proposal_id = ?",arrayOf(proposalId),null,null,null,"1").use { cursor ->
            if (!cursor.moveToFirst()) return null
            return JSONObject().apply {
                cursor.columnNames.forEachIndexed { index, name ->
                    put(name, when (cursor.getType(index)) {
                        android.database.Cursor.FIELD_TYPE_NULL -> ""
                        android.database.Cursor.FIELD_TYPE_INTEGER -> cursor.getLong(index)
                        android.database.Cursor.FIELD_TYPE_FLOAT -> cursor.getDouble(index)
                        else -> cursor.getString(index) ?: ""
                    })
                }
            }
        }
    }

    fun saveOrUpdateDeductionProposal(p: JSONObject) {
        val pid = p.optString("proposal_id")
        if (pid.isBlank()) return
        // A replayed pending server copy cannot replace an Android decision.
        if (getDeductionProposal(pid)?.optString("status") in listOf("APPROVED", "REJECTED", "FINALIZED")) return
        val cv = ContentValues().apply {
            put("proposal_id", pid)
            put("date", p.optString("date"))
            put("timestamp", p.optString("timestamp", nowIso()))
            put("originating_device", p.optString("originating_device", "SECONDARY_CLIENT"))
            put("event_type", p.optString("event_type", "VIOLATION"))
            put("related_task_id", p.optString("related_task_id", ""))
            put("applicable_rule", p.optString("applicable_rule", ""))
            put("proposed_points", p.optInt("proposed_points", -10))
            put("reason", p.optString("reason", "Deduction proposal"))
            put("evidence_json", p.optString("evidence_json", p.optJSONObject("evidence")?.toString() ?: "{}"))
            put("status", p.optString("status", "AWAITING_CONFIRMATION"))
            put("approved_by", p.optString("approved_by", ""))
            put("approved_at", p.optString("approved_at", ""))
            put("approval_source", p.optString("approval_source", ""))
            put("rejection_reason", p.optString("rejection_reason", ""))
            put("finalized_event_id", p.optString("finalized_event_id", ""))
            put("created_at", p.optString("created_at", nowIso()))
            put("updated_at", p.optString("updated_at", nowIso()))
        }
        writableDatabase.insertWithOnConflict("deduction_proposals", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
    }

    fun approveDeductionProposal(
        proposalId: String,
        approver: String = "Administrator",
        isDevMode: Boolean = false
    ): Pair<Boolean, String> {
        val proposal = getDeductionProposal(proposalId)
            ?: return Pair(false, "Proposal not found")
        val currentStatus = proposal.optString("status")
        if (currentStatus == "APPROVED") {
            return Pair(true, "Proposal was already approved.")
        }
        if (currentStatus != "AWAITING_CONFIRMATION") {
            return Pair(false, "Cannot approve proposal with status $currentStatus")
        }

        val date = proposal.optString("date")
        val proposedPts = proposal.optInt("proposed_points", -10)
        val deductionPts = if (proposedPts < 0) proposedPts else -proposedPts
        val eventType = proposal.optString("event_type", "CONFIRMED_DEDUCTION")
        val reason = proposal.optString("reason", "Approved deduction")
        val finalizedEventId = "se_deduct_" + proposalId
        val now = nowIso()

        // 1. Update proposal in DB
        val cv = ContentValues().apply {
            put("status", "APPROVED")
            put("approved_by", approver)
            put("approved_at", now)
            put("approval_source", "ANDROID_HUMAN_CONFIRMATION")
            put("finalized_event_id", finalizedEventId)
            put("updated_at", now)
        }
        writableDatabase.update("deduction_proposals", cv, "proposal_id = ?", arrayOf(proposalId))

        // 2. Insert finalized score event with authorization
        val audit = JSONObject().apply {
            put("proposal_id", proposalId)
            put("approved_by", approver)
            put("approval_source", "ANDROID_HUMAN_CONFIRMATION")
            put("approved_at", now)
            put("originating_device", proposal.optString("originating_device"))
            put("applicable_rule", proposal.optString("applicable_rule"))
        }

        ensureDay(date, isDevMode)
        val scoreCv = ContentValues().apply {
            put("event_id", finalizedEventId)
            put("date", date)
            put("is_dev_mode", if (isDevMode) 1 else 0)
            put("timestamp", now)
            put("event_type", eventType)
            put("description", "[APPROVED DEDUCTION] $reason")
            put("points", deductionPts)
            put("audit_json", audit.toString())
        }
        writableDatabase.insertWithOnConflict("score_events", null, scoreCv, SQLiteDatabase.CONFLICT_IGNORE)

        recordAdminAudit(approver, "DEDUCTION_APPROVED", "proposal_status", "AWAITING_CONFIRMATION", "APPROVED")

        // 3. Sync to server in background thread if endpoint available
        Executors.newSingleThreadExecutor().execute {
            try {
                val reg = getDeviceRegistration()
                val endpoint = reg?.optString("server_endpoint", "http://127.0.0.1:8765")?.trimEnd('/')
                if (!endpoint.isNullOrBlank() && isEndpointReachable(endpoint)) {
                    val url = URL("$endpoint/api/deductions/approve")
                    val conn = (url.openConnection() as HttpURLConnection).apply {
                        requestMethod = "POST"
                        connectTimeout = 3000
                        readTimeout = 3000
                        doOutput = true
                        setRequestProperty("Content-Type", "application/json")
                    }
                    val body = JSONObject().apply {
                        put("proposal_id", proposalId)
                        put("admin_user", approver)
                        put("approval_source", "ANDROID_HUMAN_CONFIRMATION")
                    }
                    OutputStreamWriter(conn.outputStream).use { it.write(body.toString()) }
                    conn.responseCode
                    conn.disconnect()
                }
            } catch (_: Exception) {}
        }

        return Pair(true, "Proposal approved: $deductionPts points deducted.")
    }

    fun rejectDeductionProposal(
        proposalId: String,
        approver: String = "Administrator",
        reason: String = "Rejected by user",
        isDevMode: Boolean = false
    ): Pair<Boolean, String> {
        val proposal = getDeductionProposal(proposalId)
            ?: return Pair(false, "Proposal not found")
        val currentStatus = proposal.optString("status")
        if (currentStatus != "AWAITING_CONFIRMATION") {
            return Pair(false, "Cannot reject proposal with status $currentStatus")
        }
        val now = nowIso()
        val cv = ContentValues().apply {
            put("status", "REJECTED")
            put("rejection_reason", reason)
            put("updated_at", now)
        }
        writableDatabase.update("deduction_proposals", cv, "proposal_id = ?", arrayOf(proposalId))

        recordAdminAudit(approver, "DEDUCTION_REJECTED", "proposal_status", "AWAITING_CONFIRMATION", "REJECTED")

        // Sync to server in background thread
        Executors.newSingleThreadExecutor().execute {
            try {
                val reg = getDeviceRegistration()
                val endpoint = reg?.optString("server_endpoint", "http://127.0.0.1:8765")?.trimEnd('/')
                if (!endpoint.isNullOrBlank() && isEndpointReachable(endpoint)) {
                    val url = URL("$endpoint/api/deductions/reject")
                    val conn = (url.openConnection() as HttpURLConnection).apply {
                        requestMethod = "POST"
                        connectTimeout = 3000
                        readTimeout = 3000
                        doOutput = true
                        setRequestProperty("Content-Type", "application/json")
                    }
                    val body = JSONObject().apply {
                        put("proposal_id", proposalId)
                        put("admin_user", approver)
                        put("reason", reason)
                    }
                    OutputStreamWriter(conn.outputStream).use { it.write(body.toString()) }
                    conn.responseCode
                    conn.disconnect()
                }
            } catch (_: Exception) {}
        }

        return Pair(true, "Proposal rejected with zero point impact.")
    }

    fun syncDeductionProposals(context: Context = appContext) {
        val reg = getDeviceRegistration() ?: return
        val endpoint = reg.optString("server_endpoint", "").trimEnd('/')
        if (endpoint.isBlank() || !isEndpointReachable(endpoint)) return

        try {
            val url = URL("$endpoint/api/deductions/proposals")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 3000
                readTimeout = 3000
            }
            if (conn.responseCode == 200) {
                val respStr = conn.inputStream.bufferedReader().use { it.readText() }
                val j = JSONObject(respStr)
                val proposals = j.optJSONArray("proposals")
                if (proposals != null) {
                    for (i in 0 until proposals.length()) {
                        saveOrUpdateDeductionProposal(proposals.getJSONObject(i))
                    }
                }
            }
            conn.disconnect()
            syncAutomaticBrowserExtensionPenalties(reg, endpoint)
        } catch (_: Exception) {}
    }

    private fun syncAutomaticBrowserExtensionPenalties(reg: JSONObject, endpoint: String) {
        val conn = (URL("$endpoint/api/device/extension_penalties").openConnection() as HttpURLConnection).apply {
            connectTimeout = 3000; readTimeout = 3000
            setRequestProperty("X-Device-Id", reg.getString("device_id"))
            setRequestProperty("X-Device-Token", reg.getString("device_token"))
        }
        try {
            if (conn.responseCode == 200) {
                val envelope = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                applyAutomaticBrowserExtensionBatch(envelope, reg)
            }
        } finally { conn.disconnect() }
    }

    internal fun applyAutomaticBrowserExtensionBatch(envelope: JSONObject, registration: JSONObject): Int {
        val batch = BrowserExtensionPenaltyRules.verifyBatch(envelope, registration.optString("device_id"),
            registration.optString("device_token"), System.currentTimeMillis()) ?: return 0
        val proposals = batch.getJSONArray("proposals")
        var inserted = 0
        val store = writableDatabase
        store.beginTransaction()
        try {
            for (index in 0 until proposals.length()) {
                val proposal = proposals.getJSONObject(index)
                val id = proposal.getString("proposal_id")
                val eventId = "se_deduct_$id"
                val previous = getDeductionProposal(id)
                if (previous?.optString("status") == "REJECTED") continue
                if (hasScoreEvent(eventId)) continue
                if (previous?.optString("status") in listOf("APPROVED", "FINALIZED")) continue
                val evidence = proposal.optJSONObject("evidence") ?: JSONObject(proposal.getString("evidence_json"))
                val date = proposal.getString("date")
                ensureDay(date, false)
                saveOrUpdateDeductionProposal(proposal)
                val stamp = nowIso()
                val audit = JSONObject().put("proposal_id",id).put("approval_source","BROWSER_EXTENSION_ENFORCEMENT")
                    .put("approved_by","Android extension rule").put("approved_at",stamp)
                    .put("rule_version",BrowserExtensionPenaltyRules.VERSION).put("evidence",evidence)
                    .put("originating_device","windows_companion").put("applicable_rule","CHROME_EXTENSION_ENFORCEMENT_RULE")
                val event = ContentValues().apply {
                    put("event_id",eventId); put("date",date); put("is_dev_mode",0); put("timestamp",stamp)
                    put("event_type","BROWSER_EXTENSION_DISABLED"); put("points",-1)
                    put("description",proposal.getString("reason")); put("audit_json",audit.toString())
                }
                store.insertOrThrow("score_events",null,event)
                val decision = ContentValues().apply {
                    put("status","APPROVED"); put("approved_by","Android extension rule"); put("approved_at",stamp)
                    put("approval_source","BROWSER_EXTENSION_ENFORCEMENT"); put("finalized_event_id",eventId); put("updated_at",stamp)
                }
                store.update("deduction_proposals",decision,"proposal_id = ?",arrayOf(id))
                inserted++
            }
            store.setTransactionSuccessful()
        } finally { store.endTransaction() }
        if (inserted > 0) {
            recordAdminAudit("Android extension rule","EXTENSION_PENALTY_AUTOMATIC","verified_minutes","0",inserted.toString())
            runCatching { BackgroundMusicRestrictionManager.onRestrictionStateChanged(appContext,"browser_extension_penalty") }
        }
        // The next existing heartbeat uploads the complete authoritative snapshot; no client balance write.
        return inserted
    }

    fun recordProofEvaluation(
        evaluationId: String,
        submissionId: String,
        activityId: String,
        taskCategory: String,
        date: String,
        isDevMode: Boolean,
        filePath: String,
        fileName: String,
        sha256Hash: String,
        submittedAt: String = nowIso(),
        acceptedAt: String = nowIso(),
        status: String = ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION,
        submissionVersion: Int = 1
    ): Boolean {
        return try {
            if (getProofEvaluationByActivity(activityId, date, isDevMode) != null) return false
            val now = nowIso()
            val cv = ContentValues().apply {
                put("evaluation_id", evaluationId)
                put("submission_id", submissionId)
                put("activity_id", activityId)
                put("task_category", taskCategory)
                put("date", date)
                put("is_dev_mode", if (isDevMode) 1 else 0)
                put("submission_version", submissionVersion)
                put("file_path", filePath)
                put("file_name", fileName)
                put("sha256_hash", sha256Hash)
                put("submitted_at", submittedAt)
                put("accepted_at", acceptedAt)
                put("status", status)
                put("evaluator_output_json", "{}")
                put("points_awarded", 0)
                put("score_event_id", "")
                put("retry_count", 0)
                put("last_error", "")
                put("created_at", now)
                put("updated_at", now)
            }
            val inserted = writableDatabase.insertWithOnConflict("proof_evaluations", null, cv, SQLiteDatabase.CONFLICT_IGNORE) >= 0
            if (inserted && !isDevMode && taskCategory == "HOURLY") runCatching { YouTubeRewardManager.captureAcceptance(this, submissionId, activityId, acceptedAt) }
            inserted
        } catch (_: Exception) {
            false
        }
    }

    fun updateProofEvaluationStatus(
        evaluationId: String,
        status: String,
        pointsAwarded: Int = 0,
        scoreEventId: String = "",
        evaluatorOutputJson: String = "{}",
        lastError: String = ""
    ): Boolean {
        return try {
            val now = nowIso()
            val cv = ContentValues().apply {
                put("status", status)
                put("points_awarded", pointsAwarded)
                if (scoreEventId.isNotBlank()) put("score_event_id", scoreEventId)
                if (evaluatorOutputJson.isNotBlank()) put("evaluator_output_json", evaluatorOutputJson)
                if (lastError.isNotBlank()) put("last_error", lastError)
                put("updated_at", now)
            }
            writableDatabase.update("proof_evaluations", cv, "evaluation_id = ?", arrayOf(evaluationId)) > 0
        } catch (_: Exception) {
            false
        }
    }

    fun getPendingProofEvaluations(): List<JSONObject> {
        val list = mutableListOf<JSONObject>()
        try {
            readableDatabase.rawQuery(
                "SELECT * FROM proof_evaluations WHERE status IN (?, ?, ?) ORDER BY created_at ASC",
                arrayOf(
                    ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION,
                    ProofSubmissionRules.States.EVALUATING,
                    ProofSubmissionRules.States.RETRYABLE_FAILURE
                )
            ).use { c ->
                while (c.moveToNext()) {
                    val obj = JSONObject().apply {
                        put("evaluation_id", c.getString(c.getColumnIndexOrThrow("evaluation_id")))
                        put("submission_id", c.getString(c.getColumnIndexOrThrow("submission_id")))
                        put("activity_id", c.getString(c.getColumnIndexOrThrow("activity_id")))
                        put("task_category", c.getString(c.getColumnIndexOrThrow("task_category")))
                        put("date", c.getString(c.getColumnIndexOrThrow("date")))
                        put("is_dev_mode", c.getInt(c.getColumnIndexOrThrow("is_dev_mode")) == 1)
                        put("submission_version", c.getInt(c.getColumnIndexOrThrow("submission_version")))
                        put("file_path", c.getString(c.getColumnIndexOrThrow("file_path")))
                        put("file_name", c.getString(c.getColumnIndexOrThrow("file_name")))
                        put("sha256_hash", c.getString(c.getColumnIndexOrThrow("sha256_hash")))
                        put("submitted_at", c.getString(c.getColumnIndexOrThrow("submitted_at")))
                        put("accepted_at", c.getString(c.getColumnIndexOrThrow("accepted_at")))
                        put("status", c.getString(c.getColumnIndexOrThrow("status")))
                        put("evaluator_output_json", c.getString(c.getColumnIndexOrThrow("evaluator_output_json")))
                        put("points_awarded", c.getInt(c.getColumnIndexOrThrow("points_awarded")))
                        put("score_event_id", c.getString(c.getColumnIndexOrThrow("score_event_id")))
                        put("retry_count", c.getInt(c.getColumnIndexOrThrow("retry_count")))
                        put("last_error", c.getString(c.getColumnIndexOrThrow("last_error")))
                    }
                    list.add(obj)
                }
            }
        } catch (_: Exception) {}
        return list
    }

    fun getProofEvaluation(evaluationId: String): JSONObject? {
        try {
            readableDatabase.rawQuery(
                "SELECT * FROM proof_evaluations WHERE evaluation_id = ?",
                arrayOf(evaluationId)
            ).use { c ->
                if (c.moveToFirst()) {
                    return JSONObject().apply {
                        put("evaluation_id", c.getString(c.getColumnIndexOrThrow("evaluation_id")))
                        put("submission_id", c.getString(c.getColumnIndexOrThrow("submission_id")))
                        put("activity_id", c.getString(c.getColumnIndexOrThrow("activity_id")))
                        put("task_category", c.getString(c.getColumnIndexOrThrow("task_category")))
                        put("date", c.getString(c.getColumnIndexOrThrow("date")))
                        put("is_dev_mode", c.getInt(c.getColumnIndexOrThrow("is_dev_mode")) == 1)
                        put("submission_version", c.getInt(c.getColumnIndexOrThrow("submission_version")))
                        put("file_path", c.getString(c.getColumnIndexOrThrow("file_path")))
                        put("file_name", c.getString(c.getColumnIndexOrThrow("file_name")))
                        put("sha256_hash", c.getString(c.getColumnIndexOrThrow("sha256_hash")))
                        put("submitted_at", c.getString(c.getColumnIndexOrThrow("submitted_at")))
                        put("accepted_at", c.getString(c.getColumnIndexOrThrow("accepted_at")))
                        put("status", c.getString(c.getColumnIndexOrThrow("status")))
                        put("evaluator_output_json", c.getString(c.getColumnIndexOrThrow("evaluator_output_json")))
                        put("points_awarded", c.getInt(c.getColumnIndexOrThrow("points_awarded")))
                        put("score_event_id", c.getString(c.getColumnIndexOrThrow("score_event_id")))
                        put("retry_count", c.getInt(c.getColumnIndexOrThrow("retry_count")))
                        put("last_error", c.getString(c.getColumnIndexOrThrow("last_error")))
                    }
                }
            }
        } catch (_: Exception) {}
        return null
    }

    fun getProofEvaluationByActivity(activityId: String, date: String, isDevMode: Boolean = false): JSONObject? {
        try {
            readableDatabase.rawQuery(
                "SELECT * FROM proof_evaluations WHERE activity_id = ? AND date = ? AND is_dev_mode = ? ORDER BY created_at DESC LIMIT 1",
                arrayOf(activityId, date, if (isDevMode) "1" else "0")
            ).use { c ->
                if (c.moveToFirst()) {
                    return JSONObject().apply {
                        put("evaluation_id", c.getString(c.getColumnIndexOrThrow("evaluation_id")))
                        put("submission_id", c.getString(c.getColumnIndexOrThrow("submission_id")))
                        put("activity_id", c.getString(c.getColumnIndexOrThrow("activity_id")))
                        put("task_category", c.getString(c.getColumnIndexOrThrow("task_category")))
                        put("date", c.getString(c.getColumnIndexOrThrow("date")))
                        put("status", c.getString(c.getColumnIndexOrThrow("status")))
                        put("points_awarded", c.getInt(c.getColumnIndexOrThrow("points_awarded")))
                    }
                }
            }
        } catch (_: Exception) {}
        return null
    }

    fun saveInitialSleepSubmission(
        submissionId: String,
        reportDate: String,
        isDevMode: Boolean,
        imageSha256: String,
        filePath: String,
        acceptedAtMs: Long,
        evaluationId: String? = null
    ): Boolean {
        val storage = writableDatabase
        storage.beginTransaction()
        return try {
            // One immutable accepted image per report date and mode. Retrying uses this row.
            if (getSleepSubmission(reportDate, isDevMode) != null) return false
            val dev = if (isDevMode) 1 else 0
            val now = nowIso()
            val acceptedAtIso = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).apply {
                timeZone = TimeZone.getTimeZone(scheduleTimezone())
            }.format(Date(acceptedAtMs))

            val cvSub = ContentValues().apply {
                put("submission_id", submissionId)
                put("report_date", reportDate)
                put("account_id", "user_lok")
                put("is_dev_mode", dev)
                put("created_at", now)
                put("accepted_at", acceptedAtIso)
                put("status", ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION)
                put("sleep_onset", "")
                put("wake_up", "")
                put("duration_text", "")
                put("stages_json", "{}")
                putNull("sleep_score")
                put("points_awarded", 0)
                put("bonus_ledger_event_id", "")
                put("image_sha256", imageSha256)
                put("file_path", filePath)
                put("analysis_json", "{}")
                put("rejection_reason", "")
            }
            if (storage.insertWithOnConflict("sleep_data_submissions", null, cvSub, SQLiteDatabase.CONFLICT_IGNORE) < 0) return false
            if (evaluationId != null && !recordProofEvaluation(
                    evaluationId, submissionId, "act_sleep_$reportDate", "SLEEP", reportDate,
                    isDevMode, filePath, File(filePath).name, imageSha256,
                    acceptedAtIso, acceptedAtIso)) return false
            storage.setTransactionSuccessful()
            true
        } catch (_: Exception) {
            false
        } finally {
            storage.endTransaction()
        }
    }

    fun getAdminMetrics(date: String, isDevMode: Boolean): JSONObject {
        val pendingCount = getPendingProofs().size
        val score = getScore(date, isDevMode)
        val st = getDayState(date, isDevMode)
        var missed = 0
        if (st.optString("bath_status").contains("MISSED")) missed++
        if (st.optInt("snooze_count") > 0) missed += st.optInt("snooze_count")
        val pairInfo = getDevicePairingStatus()
        val isPaired = pairInfo.optString("trust_status") == "PAIRED"

        return JSONObject().apply {
            put("pending_proofs", pendingCount)
            put("pending_reviews", pendingCount)
            put("average_score", score)
            put("missed_tasks", missed)
            put("violations", st.optInt("snooze_count", 0))
            put("connected_devices", if (isPaired) "1 / 1" else "0 / 1")
            put("sync_status", if (isPaired) "✓ Healthy" else "Disconnected")
        }
    }

    fun getAdminAuditLogs(limit: Int = 20): List<JSONObject> {
        val list = mutableListOf<JSONObject>()
        try {
            readableDatabase.rawQuery(
                "SELECT * FROM admin_audit_logs ORDER BY timestamp DESC LIMIT ?",
                arrayOf(limit.toString())
            ).use { c ->
                while (c.moveToNext()) {
                    val obj = JSONObject().apply {
                        put("audit_id", c.getString(c.getColumnIndexOrThrow("audit_id")))
                        put("admin_user", c.getString(c.getColumnIndexOrThrow("admin_user")))
                        put("action_type", c.getString(c.getColumnIndexOrThrow("action_type")))
                        put("field_changed", c.getString(c.getColumnIndexOrThrow("field_changed")))
                        put("old_value", c.getString(c.getColumnIndexOrThrow("old_value")) ?: "")
                        put("new_value", c.getString(c.getColumnIndexOrThrow("new_value")) ?: "")
                        put("timestamp", c.getString(c.getColumnIndexOrThrow("timestamp")))
                    }
                    list.add(obj)
                }
            }
        } catch (_: Exception) {}
        return list
    }

    // =========================================================================
    // RESTRICTION EVENTS, DEVICE PAIRING & SYSTEM CONFIG (Sections 111-125)
    // =========================================================================

    fun recordRestrictionEvent(
        packageName: String,
        appName: String,
        scoreAtDetection: Int,
        requiredScore: Int,
        action: String,
        reason: String,
        deviceId: String = "android_lok_phone"
    ): JSONObject {
        val eventId = "re_" + UUID.randomUUID().toString().replace("-", "").take(10)
        val ts = nowIso()
        val cv = ContentValues().apply {
            put("restriction_event_id", eventId)
            put("timestamp", ts)
            put("device_id", deviceId)
            put("package_name", packageName)
            put("application_name", appName)
            put("score_at_detection", scoreAtDetection)
            put("required_score", requiredScore)
            put("action", action)
            put("reason", reason)
        }
        writableDatabase.insert("restriction_events", null, cv)
        val payload = JSONObject().apply {
            put("restriction_event_id", eventId)
            put("timestamp", ts)
            put("date", nowDate())
            put("device_id", deviceId)
            put("package_name", packageName)
            put("application_name", appName)
            put("score_at_detection", scoreAtDetection)
            put("required_score", requiredScore)
            put("action", action)
            put("reason", reason)
        }
        enqueueSync("RESTRICTION_EVENT", payload)
        logStructured(nowDate(), false, "RESTRICTION_EVENT_LOGGED", "$appName -> $action ($reason)")
        return payload
    }

    fun getRestrictionEvents(limit: Int = 50): List<JSONObject> {
        val list = mutableListOf<JSONObject>()
        try {
            readableDatabase.rawQuery(
                "SELECT * FROM restriction_events ORDER BY timestamp DESC LIMIT ?",
                arrayOf(limit.toString())
            ).use { c ->
                while (c.moveToNext()) {
                    list.add(JSONObject().apply {
                        put("restriction_event_id", c.getString(c.getColumnIndexOrThrow("restriction_event_id")))
                        put("timestamp", c.getString(c.getColumnIndexOrThrow("timestamp")))
                        put("device_id", c.getString(c.getColumnIndexOrThrow("device_id")))
                        put("package_name", c.getString(c.getColumnIndexOrThrow("package_name")))
                        put("application_name", c.getString(c.getColumnIndexOrThrow("application_name")))
                        put("score_at_detection", c.getInt(c.getColumnIndexOrThrow("score_at_detection")))
                        put("required_score", c.getInt(c.getColumnIndexOrThrow("required_score")))
                        put("action", c.getString(c.getColumnIndexOrThrow("action")))
                        put("reason", c.getString(c.getColumnIndexOrThrow("reason")))
                    })
                }
            }
        } catch (_: Exception) {}
        return list
    }

    fun setConfigValue(key: String, value: String) {
        val widgetRelevant = key == "remote_app_locks" || key.startsWith("gym_") || key.startsWith("educational_unlock")
        val widgetChanged = widgetRelevant && getConfigValue(key) != value
        val cv = ContentValues().apply {
            put("config_key", key)
            put("config_value", value)
        }
        writableDatabase.insertWithOnConflict("system_config", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
        if (widgetChanged) {
            StrictWidgetUpdates.request(appContext,"points")
            if(key.startsWith("gym_")) StrictWidgetUpdates.request(appContext,"tasks")
        }
    }

    fun getConfigValue(key: String, defaultVal: String = ""): String {
        try {
            readableDatabase.rawQuery(
                "SELECT config_value FROM system_config WHERE config_key = ?",
                arrayOf(key)
            ).use { c ->
                if (c.moveToFirst()) return c.getString(0)
            }
        } catch (_: Exception) {}
        return defaultVal
    }

    fun isEducationalUnlockActive(): Boolean {
        val active = getConfigValue("educational_unlock_active", "0") == "1"
        if (!active) return false
        val exp = getConfigValue("educational_unlock_expires_at", "0").toLongOrNull() ?: 0L
        if (System.currentTimeMillis() > exp) {
            setConfigValue("educational_unlock_active", "0")
            return false
        }
        return true
    }

    fun getEducationalUnlockReason(): String {
        return getConfigValue("educational_unlock_reason", "")
    }

    fun getEducationalUnlockExpiresAt(): Long {
        return getConfigValue("educational_unlock_expires_at", "0").toLongOrNull() ?: 0L
    }

    fun setEducationalUnlock(reason: String, durationMinutes: Int = 60) {
        val expiresAt = System.currentTimeMillis() + (durationMinutes * 60 * 1000L)
        setConfigValue("educational_unlock_active", "1")
        setConfigValue("educational_unlock_reason", reason.trim())
        setConfigValue("educational_unlock_expires_at", expiresAt.toString())
    }

    fun cancelEducationalUnlock() {
        setConfigValue("educational_unlock_active", "0")
        setConfigValue("educational_unlock_reason", "")
        setConfigValue("educational_unlock_expires_at", "0")
    }

    // ── GYM Mode: Temporary Music Access & Three Lives ──

    fun getGymModeLives(): Int {
        val raw = getConfigValue("gym_mode_lives_balance", "3")
        return (raw.toIntOrNull() ?: 3).coerceIn(0, GymModeRules.MAX_LIVES)
    }

    fun setGymModeLives(lives: Int) {
        val clamped = lives.coerceIn(0, GymModeRules.MAX_LIVES)
        setConfigValue("gym_mode_lives_balance", clamped.toString())
    }

    /**
     * Updates the persisted heart/lives balance from a synchronized snapshot only if it is
     * newer than or equal to our last accepted authoritative sync timestamp, preventing
     * stale snapshots from silently overwriting newer heart ledger states.
     */
    @Synchronized
    fun updateGymModeLivesAuthoritative(lives: Int, serverTimeMs: Long = 0L, weekKey: String = ""): Boolean {
        val lastServerMs = getConfigValue("gym_mode_lives_server_ms", "0").toLongOrNull() ?: 0L
        if (serverTimeMs in 1 until lastServerMs) {
            return false // Stale snapshot rejected
        }
        val clamped = lives.coerceIn(0, GymModeRules.MAX_LIVES)
        setConfigValue("gym_mode_lives_balance", clamped.toString())
        setConfigValue("gym_mode_lives_untrusted", "false")
        if (serverTimeMs > 0L) setConfigValue("gym_mode_lives_server_ms", serverTimeMs.toString())
        if (weekKey.isNotBlank()) setGymModeLastResetWeek(weekKey)
        return true
    }

    fun isGymModeLivesTrustworthy(): Boolean {
        if (getConfigValue("gym_mode_lives_untrusted", "false") == "true") return false
        val raw = getConfigValue("gym_mode_lives_balance", "3")
        val parsed = raw.toIntOrNull()
        return parsed != null && parsed in 0..GymModeRules.MAX_LIVES
    }

    fun getDailyGymChoice(date: String = nowDate()): String {
        val setup = getDailyGymSetup(date)
        if (setup != null) {
            return setup.choice.name
        }
        val decision = GymPolicyClient.decision(this, date)
        return when (decision.optString("status")) {
            "GYM_PLANNED" -> "YES"
            "GYM_NOT_PLANNED" -> "NO_GYM"
            else -> "UNSET"
        }
    }

    fun getDailyGymSetup(date: String = nowDate()): DailyGymScheduleRules.DailyGymSetup? {
        val account = getDeviceRegistration()?.optString("account_id").orEmpty().ifBlank { getConfigValue("account_id", "user_lok") }
        val raw = getConfigValue("daily_gym_setup_${account}_$date", "").ifBlank {
            getConfigValue("daily_gym_setup_$date", "")
        }
        if (raw.isNotBlank()) {
            val json = runCatching { JSONObject(raw) }.getOrNull()
            if (json != null) {
                return DailyGymScheduleRules.fromJson(json)
            }
        }
        ensureGymModeTablesCreated()
        try {
            readableDatabase.rawQuery(
                "SELECT * FROM daily_gym_schedules WHERE date = ? LIMIT 1",
                arrayOf(date)
            ).use { c ->
                if (c.moveToFirst()) {
                    val choiceStr = c.getString(c.getColumnIndexOrThrow("daily_choice")) ?: "UNSET"
                    val choice = when (choiceStr.uppercase(Locale.US)) {
                        "YES", "GYM_PLANNED" -> DailyGymScheduleRules.DailyGymChoice.YES
                        "NO", "NO_GYM", "GYM_NOT_PLANNED" -> DailyGymScheduleRules.DailyGymChoice.NO_GYM
                        else -> DailyGymScheduleRules.DailyGymChoice.UNSET
                    }
                    val startTime = c.getString(c.getColumnIndexOrThrow("planned_start_time"))
                    val duration = c.getInt(c.getColumnIndexOrThrow("planned_duration_minutes"))
                    val endTime = c.getString(c.getColumnIndexOrThrow("planned_end_time"))
                    val confirmed = c.getString(c.getColumnIndexOrThrow("confirmation_status")) == "CONFIRMED"
                    return DailyGymScheduleRules.DailyGymSetup(
                        date = date,
                        choice = choice,
                        plannedStartTime = startTime,
                        plannedDurationMinutes = if (duration > 0) duration else 90,
                        plannedEndTime = endTime,
                        isConfirmed = confirmed,
                        confirmedAtMs = authoritativeNowMs(),
                        timezone = scheduleTimezone(),
                        routineVersion = c.getInt(c.getColumnIndexOrThrow("routine_version")),
                        policyVersion = c.getInt(c.getColumnIndexOrThrow("policy_version"))
                    )
                }
            }
        } catch (_: Exception) {}
        return null
    }

    fun saveDailyGymSetup(
        date: String = nowDate(),
        choice: String,
        startTime: String? = null,
        durationMinutes: Int = 90,
        isConfirmed: Boolean = true
    ): JSONObject {
        val account = getDeviceRegistration()?.optString("account_id").orEmpty().ifBlank { getConfigValue("account_id", "user_lok") }
        val cleanChoice = choice.trim().uppercase(Locale.US)
        val enumChoice = when (cleanChoice) {
            "YES", "GYM_PLANNED" -> DailyGymScheduleRules.DailyGymChoice.YES
            "NO", "NO_GYM", "GYM_NOT_PLANNED" -> DailyGymScheduleRules.DailyGymChoice.NO_GYM
            else -> DailyGymScheduleRules.DailyGymChoice.UNSET
        }

        val plannedStart = if (enumChoice == DailyGymScheduleRules.DailyGymChoice.YES) {
            startTime?.takeIf { it.isNotBlank() } ?: DailyGymScheduleRules.DEFAULT_GYM_START_TIME
        } else null

        val duration = if (enumChoice == DailyGymScheduleRules.DailyGymChoice.YES) {
            if (durationMinutes > 0) durationMinutes else DailyGymScheduleRules.DEFAULT_GYM_DURATION_MINUTES
        } else 0

        val plannedEnd = plannedStart?.let { DailyGymScheduleRules.calculateEndTime(it, duration) }

        val setup = DailyGymScheduleRules.DailyGymSetup(
            date = date,
            choice = enumChoice,
            plannedStartTime = plannedStart,
            plannedDurationMinutes = duration,
            plannedEndTime = plannedEnd,
            isConfirmed = isConfirmed,
            confirmedAtMs = authoritativeNowMs(),
            timezone = scheduleTimezone(),
            routineVersion = 1,
            policyVersion = 1
        )

        val json = DailyGymScheduleRules.toJson(setup)
        setConfigValue("daily_gym_setup_${account}_$date", json.toString())
        setConfigValue("daily_gym_setup_$date", json.toString())

        ensureGymModeTablesCreated()
        try {
            val cv = ContentValues().apply {
                put("date", date)
                put("account_id", account)
                put("timezone", scheduleTimezone())
                put("daily_choice", enumChoice.name)
                put("planned_start_time", plannedStart)
                put("planned_duration_minutes", duration)
                put("planned_end_time", plannedEnd)
                put("confirmation_status", if (isConfirmed) "CONFIRMED" else "DRAFT")
                put("revision", 1)
                put("routine_version", 1)
                put("policy_version", 1)
                put("created_at", nowIso())
                put("updated_at", nowIso())
            }
            writableDatabase.insertWithOnConflict("daily_gym_schedules", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
        } catch (_: Exception) {}

        // Save GymPolicyClient decision (syncs with PC queue)
        val plannedBool = (enumChoice == DailyGymScheduleRules.DailyGymChoice.YES)
        GymPolicyClient.save(this, date, plannedBool)

        if (enumChoice == DailyGymScheduleRules.DailyGymChoice.YES) {
            ensureGymOccurrence(date)
        } else if (enumChoice == DailyGymScheduleRules.DailyGymChoice.NO_GYM) {
            try {
                writableDatabase.delete(
                    "scheduled_activities",
                    "date = ? AND type = 'GYM' AND status IN ('SCHEDULED', 'UPCOMING', 'NOTIFIED')",
                    arrayOf(date)
                )
            } catch (_: Exception) {}
        }

        return JSONObject().apply {
            put("success", true)
            put("setup", json)
        }
    }

    fun isLocalGymPlannedForDate(date: String = nowDate()): Pair<Boolean, String> {
        val dailySetup = getDailyGymSetup(date)
        if (dailySetup != null) {
            return if (dailySetup.isGymPlanned) {
                true to "OK"
            } else {
                false to "GYM Mode unavailable — Gym was marked as not planned for today."
            }
        }
        val decision = GymPolicyClient.decision(this, date)
        val decStatus = decision.optString("status")
        if (decStatus == "GYM_NOT_PLANNED") {
            return false to "GYM Mode unavailable — Gym was marked as not planned for today."
        }
        if (decStatus == "GYM_PLANNED") {
            return true to "OK"
        }
        val (gymCfg, gymPair) = StrictCalendarDataRepository.getGymRoutineConfig(this)
        val plannedState = StrictCalendarDataRepository.calculatePlannedGymState(date, gymCfg, gymPair.first, gymPair.second)
        return when (plannedState) {
            "GYM_DAY" -> true to "OK"
            "PAUSED" -> false to "GYM Mode unavailable — Alternate-Day Gym Routine is currently paused."
            "REST_DAY" -> false to "Today is a scheduled Rest Day. Mark 'Yes — Gym Day' first if working out today."
            else -> true to "OK"
        }
    }

    fun getGymModeSyncStatus(date: String = nowDate()): String {
        ensureGymModeTablesCreated()
        try {
            readableDatabase.rawQuery(
                "SELECT sync_status FROM gym_mode_activation_ledger WHERE date = ? ORDER BY activation_time_ms DESC LIMIT 1",
                arrayOf(date)
            ).use { c ->
                if (c.moveToFirst()) return c.getString(0) ?: "NONE"
            }
        } catch (_: Exception) {}
        val account = getDeviceRegistration()?.optString("account_id").orEmpty().ifBlank { getConfigValue("account_id", "user_lok") }
        if (getConfigValue("gym_activation_pending_${account}_$date", "").isNotBlank()) return "PENDING_SYNC"
        return "NONE"
    }

    fun markGymModeActivationSyncResult(requestId: String, synced: Boolean, conflictReason: String = "", canonicalSessionId: String = "") {
        ensureGymModeTablesCreated()
        try {
            val cv = ContentValues().apply {
                put("sync_status", if (synced) "SYNCED" else "CONFLICT")
                put("conflict_reason", conflictReason)
                if (canonicalSessionId.isNotBlank()) put("canonical_session_id", canonicalSessionId)
            }
            writableDatabase.update("gym_mode_activation_ledger", cv, "request_id = ?", arrayOf(requestId))
        } catch (_: Exception) {}
    }

    fun getGymModeLastResetWeek(): String {
        return getConfigValue("gym_mode_last_reset_week", "")
    }

    fun setGymModeLastResetWeek(week: String) {
        setConfigValue("gym_mode_last_reset_week", week)
    }

    fun getGymModeLastResetTimeMs(): Long {
        return getConfigValue("gym_mode_last_reset_time_ms", "0").toLongOrNull() ?: 0L
    }

    fun setGymModeLastResetTimeMs(ms: Long) {
        setConfigValue("gym_mode_last_reset_time_ms", ms.toString())
    }

    fun ensureGymModeTablesCreated() {
        try {
            writableDatabase.execSQL(
                """
                CREATE TABLE IF NOT EXISTS gym_mode_sessions (
                    session_id TEXT PRIMARY KEY,
                    gym_activity_id TEXT NOT NULL,
                    activation_time_ms INTEGER NOT NULL,
                    access_expiry_ms INTEGER NOT NULL,
                    proof_deadline_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    toggled_off_early INTEGER NOT NULL DEFAULT 0,
                    toggled_off_at_ms INTEGER,
                    created_at TEXT NOT NULL
                )
                """.trimIndent()
            )
            writableDatabase.execSQL(
                """
                CREATE TABLE IF NOT EXISTS gym_mode_deductions (
                    deduction_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    gym_activity_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL
                )
                """.trimIndent()
            )
            writableDatabase.execSQL(
                """
                CREATE TABLE IF NOT EXISTS gym_mode_activation_ledger (
                    activation_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    account_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    gym_activity_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    canonical_session_id TEXT,
                    activation_time_ms INTEGER NOT NULL,
                    access_expiry_ms INTEGER NOT NULL,
                    policy_version TEXT NOT NULL,
                    lives_at_activation INTEGER NOT NULL,
                    heart_state TEXT NOT NULL,
                    sync_status TEXT NOT NULL,
                    conflict_reason TEXT,
                    created_at TEXT NOT NULL
                )
                """.trimIndent()
            )
            writableDatabase.execSQL(
                """
                CREATE TABLE IF NOT EXISTS daily_gym_schedules (
                    date TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT 'user_lok',
                    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
                    daily_choice TEXT NOT NULL,
                    planned_start_time TEXT,
                    planned_duration_minutes INTEGER NOT NULL DEFAULT 90,
                    planned_end_time TEXT,
                    confirmation_status TEXT NOT NULL DEFAULT 'CONFIRMED',
                    revision INTEGER NOT NULL DEFAULT 1,
                    routine_version INTEGER NOT NULL DEFAULT 1,
                    policy_version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """.trimIndent()
            )
        } catch (_: Exception) {}
    }

    fun getGymModeActiveSession(nowMs: Long = authoritativeNowMs()): GymModeRules.GymModeSession? {
        val clientSession = GymPolicyClient.activeSession(this, nowMs)
        if (clientSession != null) return clientSession

        if (getConfigValue("gym_music_locally_off", "false") == "true") return null
        ensureGymModeTablesCreated()
        try {
            readableDatabase.rawQuery(
                "SELECT session_id, gym_activity_id, activation_time_ms, access_expiry_ms, proof_deadline_ms, status, toggled_off_early, toggled_off_at_ms FROM gym_mode_sessions WHERE status = 'ACTIVE' AND toggled_off_early = 0 AND ? >= activation_time_ms AND ? < access_expiry_ms ORDER BY activation_time_ms DESC LIMIT 1",
                arrayOf(nowMs.toString(), nowMs.toString())
            ).use { c ->
                if (c.moveToFirst()) {
                    return GymModeRules.GymModeSession(
                        sessionId = c.getString(0),
                        gymActivityId = c.getString(1),
                        activationTimeMs = c.getLong(2),
                        accessExpiryMs = c.getLong(3),
                        proofDeadlineMs = c.getLong(4),
                        status = c.getString(5),
                        toggledOffEarly = c.getInt(6) == 1,
                        toggledOffAtMs = if (c.isNull(7)) null else c.getLong(7)
                    )
                }
            }
        } catch (_: Exception) {}
        return null
    }

    fun isGymModeMusicAllowed(nowMs: Long = authoritativeNowMs()): Boolean {
        if (getConfigValue("gym_music_locally_off", "false") == "true") return false

        // Check PC authoritative status first
        if (GymPolicyClient.isMusicAllowed(this, nowMs)) return true

        // Offline active session check
        ensureGymModeTablesCreated()
        try {
            readableDatabase.rawQuery(
                "SELECT session_id, access_expiry_ms FROM gym_mode_sessions WHERE status = 'ACTIVE' AND toggled_off_early = 0 AND ? >= activation_time_ms AND ? < access_expiry_ms ORDER BY activation_time_ms DESC LIMIT 1",
                arrayOf(nowMs.toString(), nowMs.toString())
            ).use { c ->
                if (c.moveToFirst()) return true
            }
        } catch (_: Exception) {}
        return false
    }

    fun getPreviouslyActivatedGymIds(date: String = nowDate()): Set<String> {
        ensureGymModeTablesCreated()
        val set = mutableSetOf<String>()
        try {
            readableDatabase.rawQuery(
                "SELECT gym_activity_id FROM gym_mode_sessions WHERE gym_activity_id LIKE ?",
                arrayOf("%${date.replace("-", "")}%")
            ).use { c ->
                while (c.moveToNext()) {
                    set.add(c.getString(0))
                }
            }
        } catch (_: Exception) {}
        val authority=GymPolicyClient.state(this)
        if (authority.optString("date")==date) {
            authority.optJSONObject("last_session")?.optString("gym_activity_id")?.takeIf { it.isNotBlank() }?.let { set.add(it) }
        }
        return set
    }

    fun getGymModeDeductedActivityIds(): Set<String> {
        ensureGymModeTablesCreated()
        val set = mutableSetOf<String>()
        try {
            readableDatabase.rawQuery("SELECT gym_activity_id FROM gym_mode_deductions", null).use { c ->
                while (c.moveToNext()) {
                    set.add(c.getString(0))
                }
            }
        } catch (_: Exception) {}
        return set
    }

    fun getGymModeFulfilledActivityIds(): Set<String> {
        ensureGymModeTablesCreated()
        val set = mutableSetOf<String>()
        try {
            readableDatabase.rawQuery("SELECT gym_activity_id FROM gym_mode_sessions WHERE status = 'FULFILLED'", null).use { c ->
                while (c.moveToNext()) {
                    set.add(c.getString(0))
                }
            }
        } catch (_: Exception) {}
        return set
    }

    fun checkGymModeWeeklyReset(nowMs: Long = authoritativeNowMs(), timezone: String = scheduleTimezone()): Boolean {
        // Only the trusted backend resets the persisted weekly balance.
        return false
    }

    fun isStrictSessionActive(): Boolean {
        return try {
            val sp = appContext.getSharedPreferences("strict_session_participant", Context.MODE_PRIVATE)
            val sessionId = sp.getString("session_id", "").orEmpty()
            val state = sp.getString("state", "").orEmpty()
            val isParticipantActive = sessionId.isNotEmpty() && state !in setOf("SESSION_FINALIZED", "REWARD_ACTIVE", "FAILED", "ABANDONED") && sp.getString("credential", null) != null
            if (isParticipantActive) return true
            readableDatabase.rawQuery("SELECT 1 FROM focus_sessions WHERE status IN ('ACTIVE', 'PAUSED') LIMIT 1", null).use { c ->
                c.moveToFirst()
            }
        } catch (_: Throwable) {
            false
        }
    }

    @Synchronized
    fun activateGymMode(
        gymActId: String,
        context: Context? = null,
        nowMs: Long = authoritativeNowMs(),
        timezone: String = scheduleTimezone()
    ): GymModeRules.ActivationResult {
        ensureGymModeTablesCreated()
        val today = ScheduleClock.display(nowMs, timezone, "yyyy-MM-dd")
        val account = getDeviceRegistration()?.optString("account_id").orEmpty().ifBlank { getConfigValue("account_id", "user_lok").ifBlank { "user_lok" } }

        if (!isGymModeLivesTrustworthy()) {
            return GymModeRules.ActivationResult(
                success = false,
                message = "Unable to verify a trustworthy remaining heart/lives balance locally. Reconcile heart ledger state before activating GYM Mode.",
                errorCode = "UNTRUSTED_HEART_STATE"
            )
        }
        val lives = getGymModeLives()

        // 1. Check active session idempotently first (repeated taps never duplicate sessions or ledger events)
        val activeSession = getGymModeActiveSession(nowMs)
        val hasActive = activeSession != null && activeSession.isMusicAccessActive
        if (hasActive) {
            return GymModeRules.ActivationResult(
                success = true,
                message = "A GYM Mode session is already active.",
                session = activeSession
            )
        }

        // 2. Check non-PC routine & daily decision eligibility
        val (gymEligible, gymReason) = isLocalGymPlannedForDate(today)
        if (!gymEligible) {
            return GymModeRules.ActivationResult(
                success = false,
                message = gymReason,
                errorCode = "GYM_NOT_PLANNED"
            )
        }

        // 3. Validate schedule-based time window (5 mins before scheduled gym time), lives >= 1, and strict session constraint
        val targetGym = getDailyActivities(today).firstOrNull { it.activityId == gymActId } ?: getActiveOrTargetGymActivity(nowMs)
        val gymTime = targetGym?.scheduledTime ?: getDailyGymSetup(today)?.plannedStartTime ?: "18:00"
        val hasStrict = try { isStrictSessionActive() } catch (_: Throwable) { false }
        val previouslyActivated = getPreviouslyActivatedGymIds(today)
        val (canAct, reason) = GymModeRules.canActivate(
            nowMs = nowMs,
            livesBalance = lives,
            hasActiveSession = false,
            gymActivityId = gymActId,
            previouslyActivatedGymIds = previouslyActivated,
            scheduledHhmm = gymTime,
            dateStr = today,
            timezone = timezone,
            hasStrictSession = hasStrict
        )
        if (!canAct) {
            return GymModeRules.ActivationResult(
                success = false,
                message = reason,
                errorCode = if (lives <= 0) "NO_LIVES" else "WINDOW_CLOSED"
            )
        }

        // 4. Persist activation & idempotent heart-obligation ledger locally FIRST (works 100% offline without PC)
        val cleanActSuffix = gymActId.replace(Regex("[^A-Za-z0-9]"), "").takeLast(16).ifBlank { "GYM1" }
        val stableId = "gym_off_${today.replace("-", "")}_$cleanActSuffix"
        val requestId = "gym_activate_${account}_${today}_$cleanActSuffix"
        val expiryMs = GymModeRules.calculateAccessExpiry(nowMs, gymTime, today, timezone)
        val proofDeadlineMs = getDailyActivities(today).firstOrNull { it.activityId == gymActId }
            ?.let { parseIsoToMs(it.windowEnd).takeIf { ms -> ms > nowMs } } ?: expiryMs
        val createdIso = nowIso()

        val dbConn = writableDatabase
        dbConn.beginTransaction()
        try {
            val cvSession = ContentValues().apply {
                put("session_id", stableId)
                put("gym_activity_id", gymActId)
                put("activation_time_ms", nowMs)
                put("access_expiry_ms", expiryMs)
                put("proof_deadline_ms", proofDeadlineMs)
                put("status", "ACTIVE")
                put("toggled_off_early", 0)
                put("created_at", createdIso)
            }
            dbConn.insertWithOnConflict("gym_mode_sessions", null, cvSession, SQLiteDatabase.CONFLICT_IGNORE)

            val cvLedger = ContentValues().apply {
                put("activation_id", "act_ledger_$stableId")
                put("request_id", requestId)
                put("account_id", account)
                put("date", today)
                put("gym_activity_id", gymActId)
                put("session_id", stableId)
                put("activation_time_ms", nowMs)
                put("access_expiry_ms", expiryMs)
                put("policy_version", ProductPolicy.VERSION)
                put("lives_at_activation", lives)
                put("heart_state", "RESERVED_OBLIGATION")
                put("sync_status", "PENDING_SYNC")
                put("created_at", createdIso)
            }
            dbConn.insertWithOnConflict("gym_mode_activation_ledger", null, cvLedger, SQLiteDatabase.CONFLICT_IGNORE)
            dbConn.setTransactionSuccessful()
        } finally {
            dbConn.endTransaction()
        }

        setConfigValue("gym_music_locally_off", "false")
        setConfigValue("gym_mode_offline_active_$today", stableId)

        // 5. Queue for later synchronization with PC & other clients (HTTP + Bluetooth + batch sync)
        val queueItem = JSONObject().apply {
            put("action", "GYM_MODE_ACTIVATE")
            put("request_id", requestId)
            put("account_id", account)
            put("session_id", stableId)
            put("gym_activity_id", gymActId)
            put("activation_time_ms", nowMs)
            put("access_expiry_ms", expiryMs)
            put("proof_deadline_ms", proofDeadlineMs)
            put("policy_version", ProductPolicy.VERSION)
            put("lives_at_activation", lives)
            put("timezone", timezone)
            put("date", today)
            put("created_at", createdIso)
        }
        setConfigValue("gym_activation_pending_${account}_$today", queueItem.toString())
        try {
            enqueueSyncEvent("GYM_MODE_ACTIVATE", queueItem, context ?: appContext, idempotencyKey = requestId)
        } catch (_: Exception) {}

        val localSession = GymModeRules.GymModeSession(
            sessionId = stableId,
            gymActivityId = gymActId,
            activationTimeMs = nowMs,
            accessExpiryMs = expiryMs,
            proofDeadlineMs = proofDeadlineMs,
            status = "ACTIVE",
            toggledOffEarly = false
        )
        context?.let { BackgroundMusicRestrictionManager.checkAndEnforceMusicRestrictions(it, "gym_mode_on") }

        // 6. Attempt non-blocking background sync if PC is registered (never blocks or revokes local activation if offline)
        if (isDeviceRegistered()) {
            Thread({ runCatching { GymPolicyClient.refresh(this) } }, "GymMode-AsyncSync").start()
        }

        return GymModeRules.ActivationResult(
            success = true,
            message = "GYM Mode activated locally (1 heart under workout proof obligation • PC sync queued)",
            session = localSession
        )
    }

    fun deactivateGymModeEarly(context: Context? = null, nowMs: Long = authoritativeNowMs()): Boolean {
        setConfigValue("gym_music_locally_off", "true")
        ensureGymModeTablesCreated()
        try {
            writableDatabase.execSQL(
                "UPDATE gym_mode_sessions SET status = 'EXPIRED', toggled_off_early = 1, toggled_off_at_ms = ? WHERE status = 'ACTIVE'",
                arrayOf(nowMs)
            )
        } catch (_: Exception) {}
        val result = GymPolicyClient.deactivate(this)
        context?.let { BackgroundMusicRestrictionManager.checkAndEnforceMusicRestrictions(it, "gym_mode_off") }
        return result
    }

    fun deductGymModeLife(gymActId: String, reason: String, nowMs: Long = authoritativeNowMs(), context: Context? = null): Boolean {
        return recordGymHeartDeduction(gymActId, null, reason, "DEDUCTED")
    }

    fun fulfillGymModeObligation(gymActId: String, nowMs: Long = authoritativeNowMs(), context: Context? = null): Boolean {
        val clientFulfilled = GymPolicyClient.state(this).optString("proof_status") == "GYM_PROOF_ACCEPTED" &&
            GymPolicyClient.state(this).optJSONObject("last_session")?.optString("gym_activity_id") == gymActId
        recordGymHeartVerificationPreserved(gymActId, null, "Gym workout proof verified; heart preserved.")
        return clientFulfilled
    }

    // ─────────────────────────────────────────────────────────────────────────
    // EMERGENCY TRAVEL BOOK & THREE-HEART ACCOUNTABILITY INFRASTRUCTURE
    // ─────────────────────────────────────────────────────────────────────────

    fun ensureEmergencyTravelTablesCreated() {
        try {
            writableDatabase.execSQL(
                """
                CREATE TABLE IF NOT EXISTS emergency_travel_requests (
                    request_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT 'user_lok',
                    travel_date TEXT NOT NULL,
                    departure_time TEXT NOT NULL,
                    departure_ms INTEGER NOT NULL,
                    return_time TEXT NOT NULL,
                    return_ms INTEGER NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    travel_type TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    pickup_location TEXT,
                    destination_location TEXT,
                    status TEXT NOT NULL,
                    approval_reason TEXT,
                    reviewed_by TEXT,
                    reviewed_at_ms INTEGER,
                    activated_at_ms INTEGER,
                    completed_at_ms INTEGER,
                    evidence_deadline_ms INTEGER NOT NULL,
                    is_materially_modified INTEGER NOT NULL DEFAULT 0,
                    audit_history_json TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """.trimIndent()
            )
            writableDatabase.execSQL(
                """
                CREATE TABLE IF NOT EXISTS travel_heart_ledger (
                    ledger_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    evidence_id TEXT,
                    action TEXT NOT NULL,
                    previous_hearts INTEGER NOT NULL,
                    new_hearts INTEGER NOT NULL,
                    hearts_changed INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL
                )
                """.trimIndent()
            )
            writableDatabase.execSQL(
                """
                CREATE TABLE IF NOT EXISTS gym_heart_ledger (
                    ledger_id TEXT PRIMARY KEY,
                    activity_id TEXT NOT NULL,
                    evidence_id TEXT,
                    action TEXT NOT NULL,
                    previous_hearts INTEGER NOT NULL,
                    new_hearts INTEGER NOT NULL,
                    hearts_changed INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL
                )
                """.trimIndent()
            )
        } catch (_: Exception) {}
    }

    // ── Three-Heart Accessors & Ledger ──

    fun getTravelHearts(): Int {
        val raw = getConfigValue("travel_hearts_balance", "3")
        return (raw.toIntOrNull() ?: 3).coerceIn(0, ThreeHeartAccountabilityEngine.MAX_HEARTS)
    }

    fun setTravelHearts(hearts: Int) {
        val clamped = hearts.coerceIn(0, ThreeHeartAccountabilityEngine.MAX_HEARTS)
        setConfigValue("travel_hearts_balance", clamped.toString())
    }

    fun getGymHearts(): Int {
        val raw = getConfigValue("gym_hearts_balance", "").ifBlank {
            getConfigValue("gym_mode_lives_balance", "3")
        }
        return (raw.toIntOrNull() ?: 3).coerceIn(0, ThreeHeartAccountabilityEngine.MAX_HEARTS)
    }

    fun setGymHearts(hearts: Int) {
        val clamped = hearts.coerceIn(0, ThreeHeartAccountabilityEngine.MAX_HEARTS)
        setConfigValue("gym_hearts_balance", clamped.toString())
        setConfigValue("gym_mode_lives_balance", clamped.toString())
    }

    @Synchronized
    fun recordTravelHeartDeduction(
        requestId: String,
        evidenceId: String?,
        reason: String,
        decision: String
    ): Boolean {
        ensureEmergencyTravelTablesCreated()
        // Single deduction guarantee: check if a deduction already exists for this requestId
        try {
            readableDatabase.rawQuery(
                "SELECT 1 FROM travel_heart_ledger WHERE request_id = ? AND action = 'DEDUCTION' LIMIT 1",
                arrayOf(requestId)
            ).use { c ->
                if (c.moveToFirst()) return false // Already deducted once
            }
        } catch (_: Exception) {}

        val current = getTravelHearts()
        if (current <= 0) return false

        val newBalance = (current - 1).coerceAtLeast(0)
        setTravelHearts(newBalance)

        val ledgerId = ThreeHeartAccountabilityEngine.generateLedgerId("TRAVEL", requestId)
        val nowMs = authoritativeNowMs()
        val cv = android.content.ContentValues().apply {
            put("ledger_id", ledgerId)
            put("request_id", requestId)
            if (evidenceId != null) put("evidence_id", evidenceId)
            put("action", ThreeHeartAccountabilityEngine.Action.DEDUCTION)
            put("previous_hearts", current)
            put("new_hearts", newBalance)
            put("hearts_changed", -1)
            put("reason", reason)
            put("decision", decision)
            put("timestamp_ms", nowMs)
        }
        try {
            writableDatabase.insertWithOnConflict("travel_heart_ledger", null, cv, android.database.sqlite.SQLiteDatabase.CONFLICT_IGNORE)
        } catch (_: Exception) {}

        logStructured(nowDate(), false, "TRAVEL_HEART_DEDUCTED", "$requestId: $reason ($newBalance/3 remaining)")
        return true
    }

    @Synchronized
    fun recordGymHeartDeduction(
        activityId: String,
        evidenceId: String?,
        reason: String,
        decision: String
    ): Boolean {
        ensureEmergencyTravelTablesCreated()
        // Single deduction guarantee: check if a deduction already exists for this activityId
        try {
            readableDatabase.rawQuery(
                "SELECT 1 FROM gym_heart_ledger WHERE activity_id = ? AND action = 'DEDUCTION' LIMIT 1",
                arrayOf(activityId)
            ).use { c ->
                if (c.moveToFirst()) return false // Already deducted once
            }
            readableDatabase.rawQuery(
                "SELECT 1 FROM gym_heart_ledger WHERE activity_id = ? AND action = 'VERIFICATION_PRESERVED' LIMIT 1",
                arrayOf(activityId)
            ).use { c ->
                if (c.moveToFirst()) return false // Already fulfilled; no deduction
            }
        } catch (_: Exception) {}

        val current = getGymHearts()
        if (current <= 0) return false

        val newBalance = (current - 1).coerceAtLeast(0)
        setGymHearts(newBalance)

        val ledgerId = ThreeHeartAccountabilityEngine.generateLedgerId("GYM", activityId)
        val nowMs = authoritativeNowMs()
        val cv = android.content.ContentValues().apply {
            put("ledger_id", ledgerId)
            put("activity_id", activityId)
            if (evidenceId != null) put("evidence_id", evidenceId)
            put("action", ThreeHeartAccountabilityEngine.Action.DEDUCTION)
            put("previous_hearts", current)
            put("new_hearts", newBalance)
            put("hearts_changed", -1)
            put("reason", reason)
            put("decision", decision)
            put("timestamp_ms", nowMs)
        }
        try {
            writableDatabase.insertWithOnConflict("gym_heart_ledger", null, cv, android.database.sqlite.SQLiteDatabase.CONFLICT_IGNORE)
        } catch (_: Exception) {}

        logStructured(nowDate(), false, "GYM_HEART_DEDUCTED", "$activityId: $reason ($newBalance/3 remaining)")
        return true
    }

    fun recordGymHeartVerificationPreserved(activityId: String, evidenceId: String?, reason: String): Boolean {
        ensureEmergencyTravelTablesCreated()
        val nowMs = authoritativeNowMs()
        val current = getGymHearts()
        val ledgerId = ThreeHeartAccountabilityEngine.generateLedgerId("GYM", activityId)
        val cv = android.content.ContentValues().apply {
            put("ledger_id", ledgerId)
            put("activity_id", activityId)
            if (evidenceId != null) put("evidence_id", evidenceId)
            put("action", ThreeHeartAccountabilityEngine.Action.VERIFICATION_PRESERVED)
            put("previous_hearts", current)
            put("new_hearts", current)
            put("hearts_changed", 0)
            put("reason", reason)
            put("decision", "PRESERVED")
            put("timestamp_ms", nowMs)
        }
        return try {
            writableDatabase.insertWithOnConflict("gym_heart_ledger", null, cv, android.database.sqlite.SQLiteDatabase.CONFLICT_IGNORE) > 0
        } catch (_: Exception) { false }
    }

    fun adminResetTravelHearts(reason: String = "Administrator reset", adminUser: String = "admin"): Boolean {
        ensureEmergencyTravelTablesCreated()
        val prev = getTravelHearts()
        setTravelHearts(ThreeHeartAccountabilityEngine.MAX_HEARTS)
        val nowMs = authoritativeNowMs()
        val ledgerId = "HLG-TRAVEL-RESET-${UUID.randomUUID().toString().take(6)}"
        val cv = android.content.ContentValues().apply {
            put("ledger_id", ledgerId)
            put("request_id", "ADMIN_RESET")
            put("action", ThreeHeartAccountabilityEngine.Action.ADMIN_RESET)
            put("previous_hearts", prev)
            put("new_hearts", ThreeHeartAccountabilityEngine.MAX_HEARTS)
            put("hearts_changed", ThreeHeartAccountabilityEngine.MAX_HEARTS - prev)
            put("reason", "$reason (by $adminUser)")
            put("decision", "RESET_TO_MAX")
            put("timestamp_ms", nowMs)
        }
        try {
            writableDatabase.insertWithOnConflict("travel_heart_ledger", null, cv, android.database.sqlite.SQLiteDatabase.CONFLICT_IGNORE)
        } catch (_: Exception) {}
        logStructured(nowDate(), false, "TRAVEL_HEARTS_RESET", "Reset by $adminUser: $reason")
        return true
    }

    fun adminResetGymHearts(reason: String = "Administrator reset", adminUser: String = "admin"): Boolean {
        ensureEmergencyTravelTablesCreated()
        val prev = getGymHearts()
        setGymHearts(ThreeHeartAccountabilityEngine.MAX_HEARTS)
        val nowMs = authoritativeNowMs()
        val ledgerId = "HLG-GYM-RESET-${UUID.randomUUID().toString().take(6)}"
        val cv = android.content.ContentValues().apply {
            put("ledger_id", ledgerId)
            put("activity_id", "ADMIN_RESET")
            put("action", ThreeHeartAccountabilityEngine.Action.ADMIN_RESET)
            put("previous_hearts", prev)
            put("new_hearts", ThreeHeartAccountabilityEngine.MAX_HEARTS)
            put("hearts_changed", ThreeHeartAccountabilityEngine.MAX_HEARTS - prev)
            put("reason", "$reason (by $adminUser)")
            put("decision", "RESET_TO_MAX")
            put("timestamp_ms", nowMs)
        }
        try {
            writableDatabase.insertWithOnConflict("gym_heart_ledger", null, cv, android.database.sqlite.SQLiteDatabase.CONFLICT_IGNORE)
        } catch (_: Exception) {}
        logStructured(nowDate(), false, "GYM_HEARTS_RESET", "Reset by $adminUser: $reason")
        return true
    }

    fun getTravelHeartLedger(): List<ThreeHeartAccountabilityEngine.HeartLedgerEntry> {
        ensureEmergencyTravelTablesCreated()
        val list = mutableListOf<ThreeHeartAccountabilityEngine.HeartLedgerEntry>()
        try {
            readableDatabase.rawQuery(
                "SELECT ledger_id, domain, request_id, evidence_id, action, previous_hearts, new_hearts, hearts_changed, reason, decision, timestamp_ms FROM travel_heart_ledger ORDER BY timestamp_ms DESC",
                null
            ).use { c ->
                while (c.moveToNext()) {
                    list.add(
                        ThreeHeartAccountabilityEngine.HeartLedgerEntry(
                            ledgerId = c.getString(0),
                            domain = "TRAVEL",
                            activityId = c.getString(2),
                            evidenceId = if (c.isNull(3)) null else c.getString(3),
                            action = c.getString(4),
                            previousHearts = c.getInt(5),
                            newHearts = c.getInt(6),
                            heartsChanged = c.getInt(7),
                            reason = c.getString(8),
                            decision = c.getString(9),
                            timestampMs = c.getLong(10)
                        )
                    )
                }
            }
        } catch (_: Exception) {}
        return list
    }

    fun getGymHeartLedger(): List<ThreeHeartAccountabilityEngine.HeartLedgerEntry> {
        ensureEmergencyTravelTablesCreated()
        val list = mutableListOf<ThreeHeartAccountabilityEngine.HeartLedgerEntry>()
        try {
            readableDatabase.rawQuery(
                "SELECT ledger_id, activity_id, evidence_id, action, previous_hearts, new_hearts, hearts_changed, reason, decision, timestamp_ms FROM gym_heart_ledger ORDER BY timestamp_ms DESC",
                null
            ).use { c ->
                while (c.moveToNext()) {
                    list.add(
                        ThreeHeartAccountabilityEngine.HeartLedgerEntry(
                            ledgerId = c.getString(0),
                            domain = "GYM",
                            activityId = c.getString(1),
                            evidenceId = if (c.isNull(2)) null else c.getString(2),
                            action = c.getString(3),
                            previousHearts = c.getInt(4),
                            newHearts = c.getInt(5),
                            heartsChanged = c.getInt(6),
                            reason = c.getString(7),
                            decision = c.getString(8),
                            timestampMs = c.getLong(9)
                        )
                    )
                }
            }
        } catch (_: Exception) {}
        return list
    }

    fun getKnownEvidenceHashes(excludeActivityId: String? = null): Set<String> {
        val hashes = mutableSetOf<String>()
        try {
            readableDatabase.rawQuery("SELECT sha256_hash, activity_id FROM proof_evaluations WHERE sha256_hash != ''", null).use { c ->
                while (c.moveToNext()) {
                    val h = c.getString(0)
                    val act = c.getString(1)
                    if (excludeActivityId == null || act != excludeActivityId) {
                        hashes.add(h)
                    }
                }
            }
            readableDatabase.rawQuery("SELECT sha256_hash, activity_id FROM proof_reviews WHERE sha256_hash != ''", null).use { c ->
                while (c.moveToNext()) {
                    val h = c.getString(0)
                    val act = c.getString(1)
                    if (excludeActivityId == null || act != excludeActivityId) {
                        hashes.add(h)
                    }
                }
            }
        } catch (_: Exception) {}
        return hashes
    }

    // ── Emergency Travel Request Operations ──

    fun createEmergencyTravelRequest(
        travelDate: String,
        departureTime: String,
        returnTime: String,
        purpose: String,
        travelType: String = "CAR_RIDE",
        pickupLocation: String? = null,
        destinationLocation: String? = null,
        context: Context? = null
    ): Pair<EmergencyTravelRules.EmergencyTravelRequest?, String> {
        ensureEmergencyTravelTablesCreated()
        val nowMs = authoritativeNowMs()
        val tz = scheduleTimezone()

        if (getTravelHearts() <= 0) {
            return Pair(null, "Emergency travel cannot be requested: 0 hearts remaining in Travel Mode.")
        }

        val validation = EmergencyTravelRules.validateRequestInput(
            travelDate = travelDate,
            departureTime = departureTime,
            returnTime = returnTime,
            purpose = purpose,
            travelType = travelType,
            nowMs = nowMs,
            timezone = tz
        )
        if (!validation.valid) {
            return Pair(null, validation.message)
        }

        val existing = getEmergencyTravelRequests(travelDate)
        val (conflict, conflictMsg) = EmergencyTravelRules.hasConflict(
            departureMs = validation.departureMs,
            returnMs = validation.returnMs,
            existingRequests = existing
        )
        if (conflict) {
            return Pair(null, conflictMsg)
        }

        val reqId = EmergencyTravelRules.generateRequestId(travelDate, departureTime)
        val autoApprove = getConfigValue("emergency_travel_auto_approve", "false") == "true"
        val initialStatus = if (autoApprove) EmergencyTravelRules.Status.APPROVED else EmergencyTravelRules.Status.AWAITING_APPROVAL

        val audit = listOf(
            EmergencyTravelRules.AuditEntry(
                revision = 1,
                action = EmergencyTravelRules.Status.REQUEST_SUBMITTED,
                details = "Emergency travel requested: $departureTime - $returnTime ($purpose)",
                timestampMs = nowMs,
                actor = "user"
            )
        )

        val req = EmergencyTravelRules.EmergencyTravelRequest(
            requestId = reqId,
            accountId = getDeviceRegistration()?.optString("account_id").orEmpty().ifBlank { "user_lok" },
            travelDate = travelDate,
            departureTime = departureTime,
            departureMs = validation.departureMs,
            returnTime = returnTime,
            returnMs = validation.returnMs,
            durationMinutes = validation.durationMinutes,
            travelType = travelType,
            purpose = purpose,
            pickupLocation = pickupLocation,
            destinationLocation = destinationLocation,
            status = initialStatus,
            approvalReason = if (autoApprove) "Auto-approved by local policy" else null,
            reviewedBy = if (autoApprove) "system" else null,
            reviewedAtMs = if (autoApprove) nowMs else null,
            evidenceDeadlineMs = validation.returnMs + (EmergencyTravelRules.DEFAULT_EVIDENCE_GRACE_HOURS * 3600_000L),
            auditHistory = audit,
            createdAtMs = nowMs,
            updatedAtMs = nowMs
        )

        val cv = android.content.ContentValues().apply {
            put("request_id", req.requestId)
            put("account_id", req.accountId)
            put("travel_date", req.travelDate)
            put("departure_time", req.departureTime)
            put("departure_ms", req.departureMs)
            put("return_time", req.returnTime)
            put("return_ms", req.returnMs)
            put("duration_minutes", req.durationMinutes)
            put("travel_type", req.travelType)
            put("purpose", req.purpose)
            put("pickup_location", req.pickupLocation)
            put("destination_location", req.destinationLocation)
            put("status", req.status)
            put("approval_reason", req.approvalReason)
            put("reviewed_by", req.reviewedBy)
            put("reviewed_at_ms", req.reviewedAtMs)
            put("evidence_deadline_ms", req.evidenceDeadlineMs)
            put("is_materially_modified", 0)
            put("audit_history_json", org.json.JSONArray().apply { req.auditHistory.forEach { put(it.toJson()) } }.toString())
            put("created_at_ms", req.createdAtMs)
            put("updated_at_ms", req.updatedAtMs)
        }

        try {
            writableDatabase.insertWithOnConflict("emergency_travel_requests", null, cv, android.database.sqlite.SQLiteDatabase.CONFLICT_REPLACE)
        } catch (e: Exception) {
            return Pair(null, "Database insertion failed: ${e.message}")
        }

        // Also add scheduled activity entry so it participates in standard schedule
        try {
            val actDetails = org.json.JSONObject().apply {
                put("is_emergency", true)
                put("emergency_request_id", req.requestId)
                put("travel_type", req.travelType)
                put("purpose", req.purpose)
                put("departure_ms", req.departureMs)
                put("travel_end_ms", req.returnMs)
                put("proof_window_start_ms", req.returnMs)
                put("proof_window_end_ms", req.evidenceDeadlineMs)
                put("duration_hours", (req.durationMinutes + 59) / 60)
            }
            val cvAct = android.content.ContentValues().apply {
                put("activity_id", "ACT-${req.requestId}")
                put("account_id", req.accountId)
                put("type", "TRAVEL")
                put("date", req.travelDate)
                put("title", "Emergency Travel (${req.travelType})")
                put("scheduled_time", req.departureTime)
                put("window_start", formatMsToIso(req.departureMs))
                put("window_end", formatMsToIso(req.returnMs))
                put("notification_time", formatMsToIso(req.departureMs - 5 * 60 * 1000L))
                put("status", req.status)
                put("points", 10)
                put("details_json", actDetails.toString())
            }
            writableDatabase.insertWithOnConflict("scheduled_activities", null, cvAct, android.database.sqlite.SQLiteDatabase.CONFLICT_REPLACE)
        } catch (_: Exception) {}

        reconcileHourlyTravelConflicts(travelDate)

        val msg = if (autoApprove) "Emergency travel request approved." else "Emergency travel request submitted for approval."
        return Pair(req, msg)
    }

    fun modifyEmergencyTravelSlot(
        requestId: String,
        newDeparture: String,
        newReturn: String,
        reason: String,
        context: Context? = null
    ): Pair<EmergencyTravelRules.EmergencyTravelRequest?, String> {
        ensureEmergencyTravelTablesCreated()
        val existing = getEmergencyTravelRequest(requestId)
            ?: return Pair(null, "Travel request not found: $requestId")

        val allRequests = getEmergencyTravelRequests(existing.travelDate)
        val nowMs = authoritativeNowMs()
        val (updated, statusMsg) = EmergencyTravelRules.modifyTravelSlot(
            existing = existing,
            newDepartureTime = newDeparture,
            newReturnTime = newReturn,
            reason = reason,
            nowMs = nowMs,
            timezone = scheduleTimezone(),
            existingRequests = allRequests
        )
        if (updated == null) return Pair(null, statusMsg)

        val cv = android.content.ContentValues().apply {
            put("departure_time", updated.departureTime)
            put("departure_ms", updated.departureMs)
            put("return_time", updated.returnTime)
            put("return_ms", updated.returnMs)
            put("duration_minutes", updated.durationMinutes)
            put("evidence_deadline_ms", updated.evidenceDeadlineMs)
            put("status", updated.status)
            put("is_materially_modified", if (updated.isMateriallyModified) 1 else 0)
            put("audit_history_json", org.json.JSONArray().apply { updated.auditHistory.forEach { put(it.toJson()) } }.toString())
            put("updated_at_ms", updated.updatedAtMs)
        }
        writableDatabase.update("emergency_travel_requests", cv, "request_id = ?", arrayOf(requestId))

        // Update corresponding scheduled_activities
        try {
            val cvAct = android.content.ContentValues().apply {
                put("scheduled_time", updated.departureTime)
                put("window_start", formatMsToIso(updated.departureMs))
                put("window_end", formatMsToIso(updated.returnMs))
                put("notification_time", formatMsToIso(updated.departureMs - 5 * 60 * 1000L))
                put("status", updated.status)
            }
            writableDatabase.update("scheduled_activities", cvAct, "activity_id = ?", arrayOf("ACT-$requestId"))
        } catch (_: Exception) {}

        reconcileHourlyTravelConflicts(existing.travelDate)
        return Pair(updated, statusMsg)
    }

    fun approveEmergencyTravelRequest(
        requestId: String,
        adminUser: String = "admin",
        reason: String = "Approved by administrator"
    ): Boolean {
        ensureEmergencyTravelTablesCreated()
        val existing = getEmergencyTravelRequest(requestId) ?: return false
        val updated = EmergencyTravelRules.evaluateApproval(
            request = existing,
            adminApproved = true,
            adminReason = reason,
            actor = adminUser
        )
        val cv = android.content.ContentValues().apply {
            put("status", updated.status)
            put("approval_reason", updated.approvalReason)
            put("reviewed_by", updated.reviewedBy)
            put("reviewed_at_ms", updated.reviewedAtMs)
            put("is_materially_modified", 0)
            put("audit_history_json", org.json.JSONArray().apply { updated.auditHistory.forEach { put(it.toJson()) } }.toString())
            put("updated_at_ms", updated.updatedAtMs)
        }
        writableDatabase.update("emergency_travel_requests", cv, "request_id = ?", arrayOf(requestId))
        try {
            writableDatabase.execSQL("UPDATE scheduled_activities SET status = 'APPROVED' WHERE activity_id = ?", arrayOf("ACT-$requestId"))
        } catch (_: Exception) {}
        reconcileHourlyTravelConflicts(existing.travelDate)
        return true
    }

    fun rejectEmergencyTravelRequest(
        requestId: String,
        adminUser: String = "admin",
        reason: String = "Rejected by administrator"
    ): Boolean {
        ensureEmergencyTravelTablesCreated()
        val existing = getEmergencyTravelRequest(requestId) ?: return false
        val updated = EmergencyTravelRules.evaluateApproval(
            request = existing,
            adminApproved = false,
            adminReason = reason,
            actor = adminUser
        )
        val cv = android.content.ContentValues().apply {
            put("status", updated.status)
            put("approval_reason", updated.approvalReason)
            put("reviewed_by", updated.reviewedBy)
            put("reviewed_at_ms", updated.reviewedAtMs)
            put("audit_history_json", org.json.JSONArray().apply { updated.auditHistory.forEach { put(it.toJson()) } }.toString())
            put("updated_at_ms", updated.updatedAtMs)
        }
        writableDatabase.update("emergency_travel_requests", cv, "request_id = ?", arrayOf(requestId))
        try {
            writableDatabase.execSQL("UPDATE scheduled_activities SET status = 'REJECTED' WHERE activity_id = ?", arrayOf("ACT-$requestId"))
        } catch (_: Exception) {}
        reconcileHourlyTravelConflicts(existing.travelDate)
        return true
    }

    fun activateEmergencyTravel(requestId: String, nowMs: Long = authoritativeNowMs(), context: Context? = null): Pair<Boolean, String> {
        ensureEmergencyTravelTablesCreated()
        val existing = getEmergencyTravelRequest(requestId)
            ?: return Pair(false, "Travel request not found: $requestId")

        val (canAct, reason) = EmergencyTravelRules.canActivate(nowMs, existing, getTravelHearts())
        if (!canAct) return Pair(false, reason)

        val cv = android.content.ContentValues().apply {
            put("status", EmergencyTravelRules.Status.ACTIVE)
            put("activated_at_ms", nowMs)
            put("updated_at_ms", nowMs)
        }
        writableDatabase.update("emergency_travel_requests", cv, "request_id = ?", arrayOf(requestId))
        setConfigValue("emergency_travel_active_id", requestId)
        setConfigValue("emergency_travel_active", "true")

        try {
            writableDatabase.execSQL("UPDATE scheduled_activities SET status = 'ACTIVE' WHERE activity_id = ?", arrayOf("ACT-$requestId"))
        } catch (_: Exception) {}

        reconcileHourlyTravelConflicts(existing.travelDate)
        logStructured(nowDate(), false, "EMERGENCY_TRAVEL_ACTIVATED", "$requestId active until ${existing.returnTime}")
        return Pair(true, "Emergency Travel Mode activated until ${existing.returnTime}")
    }

    fun completeEmergencyTravel(requestId: String, nowMs: Long = authoritativeNowMs(), context: Context? = null): Boolean {
        ensureEmergencyTravelTablesCreated()
        val existing = getEmergencyTravelRequest(requestId) ?: return false

        val cv = android.content.ContentValues().apply {
            put("status", EmergencyTravelRules.Status.AWAITING_EVIDENCE)
            put("completed_at_ms", nowMs)
            put("updated_at_ms", nowMs)
        }
        writableDatabase.update("emergency_travel_requests", cv, "request_id = ?", arrayOf(requestId))

        val currentActiveId = getConfigValue("emergency_travel_active_id", "")
        if (currentActiveId == requestId) {
            setConfigValue("emergency_travel_active_id", "")
            setConfigValue("emergency_travel_active", "false")
        }

        try {
            writableDatabase.execSQL("UPDATE scheduled_activities SET status = 'AWAITING_EVIDENCE' WHERE activity_id = ?", arrayOf("ACT-$requestId"))
        } catch (_: Exception) {}

        reconcileHourlyTravelConflicts(existing.travelDate)
        logStructured(nowDate(), false, "EMERGENCY_TRAVEL_COMPLETED", "$requestId completed. Evidence deadline: ${formatMsToIso(existing.evidenceDeadlineMs)}")
        return true
    }

    fun getEmergencyTravelRequest(requestId: String): EmergencyTravelRules.EmergencyTravelRequest? {
        ensureEmergencyTravelTablesCreated()
        try {
            readableDatabase.rawQuery(
                """
                SELECT request_id, account_id, travel_date, departure_time, departure_ms, return_time, return_ms,
                       duration_minutes, travel_type, purpose, pickup_location, destination_location, status,
                       approval_reason, reviewed_by, reviewed_at_ms, activated_at_ms, completed_at_ms,
                       evidence_deadline_ms, is_materially_modified, audit_history_json, created_at_ms, updated_at_ms
                FROM emergency_travel_requests WHERE request_id = ? LIMIT 1
                """.trimIndent(),
                arrayOf(requestId)
            ).use { c ->
                if (c.moveToFirst()) {
                    val audits = mutableListOf<EmergencyTravelRules.AuditEntry>()
                    val auditJson = c.getString(20)
                    if (!auditJson.isNullOrBlank()) {
                        val arr = runCatching { org.json.JSONArray(auditJson) }.getOrNull()
                        if (arr != null) {
                            for (i in 0 until arr.length()) {
                                arr.optJSONObject(i)?.let { audits.add(EmergencyTravelRules.AuditEntry.fromJson(it)) }
                            }
                        }
                    }
                    return EmergencyTravelRules.EmergencyTravelRequest(
                        requestId = c.getString(0),
                        accountId = c.getString(1),
                        travelDate = c.getString(2),
                        departureTime = c.getString(3),
                        departureMs = c.getLong(4),
                        returnTime = c.getString(5),
                        returnMs = c.getLong(6),
                        durationMinutes = c.getInt(7),
                        travelType = c.getString(8),
                        purpose = c.getString(9),
                        pickupLocation = if (c.isNull(10)) null else c.getString(10),
                        destinationLocation = if (c.isNull(11)) null else c.getString(11),
                        status = c.getString(12),
                        approvalReason = if (c.isNull(13)) null else c.getString(13),
                        reviewedBy = if (c.isNull(14)) null else c.getString(14),
                        reviewedAtMs = if (c.isNull(15)) null else c.getLong(15),
                        activatedAtMs = if (c.isNull(16)) null else c.getLong(16),
                        completedAtMs = if (c.isNull(17)) null else c.getLong(17),
                        evidenceDeadlineMs = c.getLong(18),
                        isMateriallyModified = c.getInt(19) == 1,
                        auditHistory = audits,
                        createdAtMs = c.getLong(21),
                        updatedAtMs = c.getLong(22)
                    )
                }
            }
        } catch (_: Exception) {}
        return null
    }

    fun getEmergencyTravelRequests(date: String = nowDate()): List<EmergencyTravelRules.EmergencyTravelRequest> {
        ensureEmergencyTravelTablesCreated()
        val list = mutableListOf<EmergencyTravelRules.EmergencyTravelRequest>()
        try {
            readableDatabase.rawQuery(
                """
                SELECT request_id, account_id, travel_date, departure_time, departure_ms, return_time, return_ms,
                       duration_minutes, travel_type, purpose, pickup_location, destination_location, status,
                       approval_reason, reviewed_by, reviewed_at_ms, activated_at_ms, completed_at_ms,
                       evidence_deadline_ms, is_materially_modified, audit_history_json, created_at_ms, updated_at_ms
                FROM emergency_travel_requests WHERE travel_date = ? ORDER BY departure_ms ASC
                """.trimIndent(),
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    val audits = mutableListOf<EmergencyTravelRules.AuditEntry>()
                    val auditJson = c.getString(20)
                    if (!auditJson.isNullOrBlank()) {
                        val arr = runCatching { org.json.JSONArray(auditJson) }.getOrNull()
                        if (arr != null) {
                            for (i in 0 until arr.length()) {
                                arr.optJSONObject(i)?.let { audits.add(EmergencyTravelRules.AuditEntry.fromJson(it)) }
                            }
                        }
                    }
                    list.add(
                        EmergencyTravelRules.EmergencyTravelRequest(
                            requestId = c.getString(0),
                            accountId = c.getString(1),
                            travelDate = c.getString(2),
                            departureTime = c.getString(3),
                            departureMs = c.getLong(4),
                            returnTime = c.getString(5),
                            returnMs = c.getLong(6),
                            durationMinutes = c.getInt(7),
                            travelType = c.getString(8),
                            purpose = c.getString(9),
                            pickupLocation = if (c.isNull(10)) null else c.getString(10),
                            destinationLocation = if (c.isNull(11)) null else c.getString(11),
                            status = c.getString(12),
                            approvalReason = if (c.isNull(13)) null else c.getString(13),
                            reviewedBy = if (c.isNull(14)) null else c.getString(14),
                            reviewedAtMs = if (c.isNull(15)) null else c.getLong(15),
                            activatedAtMs = if (c.isNull(16)) null else c.getLong(16),
                            completedAtMs = if (c.isNull(17)) null else c.getLong(17),
                            evidenceDeadlineMs = c.getLong(18),
                            isMateriallyModified = c.getInt(19) == 1,
                            auditHistory = audits,
                            createdAtMs = c.getLong(21),
                            updatedAtMs = c.getLong(22)
                        )
                    )
                }
            }
        } catch (_: Exception) {}
        return list
    }

    fun getActiveEmergencyTravel(nowMs: Long = authoritativeNowMs()): EmergencyTravelRules.EmergencyTravelRequest? {
        ensureEmergencyTravelTablesCreated()
        val today = nowDate()
        val all = getEmergencyTravelRequests(today)
        return all.firstOrNull { req ->
            val isCurrent = req.status == EmergencyTravelRules.Status.ACTIVE ||
                (req.status in setOf(EmergencyTravelRules.Status.APPROVED, EmergencyTravelRules.Status.SCHEDULED) && nowMs >= req.departureMs && nowMs < req.returnMs)
            isCurrent
        }
    }

    fun getLatestEmergencyTravel(date: String = nowDate()): EmergencyTravelRules.EmergencyTravelRequest? {
        return getEmergencyTravelRequests(date).lastOrNull()
    }

    fun isEmergencyTravelActive(nowMs: Long = authoritativeNowMs()): Boolean {
        return getActiveEmergencyTravel(nowMs) != null
    }

    fun checkEmergencyTravelDeadlines(nowMs: Long = authoritativeNowMs(), context: Context? = null) {
        ensureEmergencyTravelTablesCreated()
        val today = nowDate()
        val requests = getEmergencyTravelRequests(today)
        for (req in requests) {
            val resolved = EmergencyTravelRules.resolveState(nowMs, req)
            if (resolved.status != req.status) {
                writableDatabase.execSQL(
                    "UPDATE emergency_travel_requests SET status = ?, updated_at_ms = ? WHERE request_id = ?",
                    arrayOf(resolved.status, nowMs, req.requestId)
                )
                // If evidence deadline expired without submission -> deduct exactly one heart
                if (resolved.status == EmergencyTravelRules.Status.EXPIRED && req.status == EmergencyTravelRules.Status.AWAITING_EVIDENCE) {
                    recordTravelHeartDeduction(
                        requestId = req.requestId,
                        evidenceId = null,
                        reason = "Emergency travel evidence deadline expired without valid submission.",
                        decision = "DEADLINE_EXPIRED"
                    )
                }
            }
        }
    }

    fun checkGymEvidenceDeadlines(nowMs: Long = authoritativeNowMs(), context: Context? = null) {
        ensureEmergencyTravelTablesCreated()
        val today = nowDate()
        try {
            readableDatabase.rawQuery(
                "SELECT session_id, gym_activity_id, proof_deadline_ms, status FROM gym_mode_sessions WHERE status IN ('ACTIVE', 'EXPIRED') AND proof_deadline_ms < ?",
                arrayOf(nowMs.toString())
            ).use { c ->
                while (c.moveToNext()) {
                    val gymActId = c.getString(1)
                    val deadlineMs = c.getLong(2)
                    // Check if proof was fulfilled
                    val hasProof = getProofEvaluationByActivity(gymActId, today) != null ||
                        getDailyProofReviews(today).any { it.optString("activity_id") == gymActId }
                    if (!hasProof && nowMs > deadlineMs) {
                        recordGymHeartDeduction(
                            activityId = gymActId,
                            evidenceId = null,
                            reason = "Gym proof submission deadline expired without acceptable evidence.",
                            decision = "DEADLINE_EXPIRED"
                        )
                    }
                }
            }
        } catch (_: Exception) {}
    }

    fun submitEmergencyTravelProof(
        requestId: String,
        photoBytes: ByteArray,
        fileName: String,
        description: String = "",
        ocrText: String = "",
        context: Context? = null,
        isSimulatedFraudTest: Boolean = false,
        isTechnicalError: Boolean = false
    ): SharedEvidenceValidator.EvaluationResult {
        ensureEmergencyTravelTablesCreated()
        val req = getEmergencyTravelRequest(requestId)
            ?: return SharedEvidenceValidator.EvaluationResult(
                status = "REJECTED",
                isGenuine = false,
                isDuplicate = false,
                isManipulated = false,
                deductHeart = false,
                heartAction = "ALLOW_CORRECTION",
                reason = "Travel request not found: $requestId",
                sha256Hash = ""
            )

        val nowMs = authoritativeNowMs()
        val otherHashes = getKnownEvidenceHashes(excludeActivityId = requestId)

        val eval = SharedEvidenceValidator.validateTravelEvidence(
            activityId = requestId,
            departureMs = req.departureMs,
            returnMs = req.returnMs,
            deadlineMs = req.evidenceDeadlineMs,
            nowMs = nowMs,
            photoBytes = photoBytes,
            fileName = fileName,
            ocrText = ocrText,
            userDescription = description,
            knownHashesFromOtherActivities = otherHashes,
            isSimulatedFraudTest = isSimulatedFraudTest,
            isNetworkOrTechnicalError = isTechnicalError
        )

        val evId = "ev_${eval.sha256Hash.take(12)}"
        if (eval.status == "VERIFIED") {
            writableDatabase.execSQL(
                "UPDATE emergency_travel_requests SET status = 'EVIDENCE_VERIFIED', updated_at_ms = ? WHERE request_id = ?",
                arrayOf(nowMs, requestId)
            )
            // Preserve heart in ledger
            val ledgerId = ThreeHeartAccountabilityEngine.generateLedgerId("TRAVEL", requestId)
            val curHearts = getTravelHearts()
            val cv = android.content.ContentValues().apply {
                put("ledger_id", ledgerId)
                put("request_id", requestId)
                put("evidence_id", evId)
                put("action", ThreeHeartAccountabilityEngine.Action.VERIFICATION_PRESERVED)
                put("previous_hearts", curHearts)
                put("new_hearts", curHearts)
                put("hearts_changed", 0)
                put("reason", eval.reason)
                put("decision", "PRESERVED")
                put("timestamp_ms", nowMs)
            }
            writableDatabase.insertWithOnConflict("travel_heart_ledger", null, cv, android.database.sqlite.SQLiteDatabase.CONFLICT_IGNORE)
            try {
                writableDatabase.execSQL(
                    "UPDATE scheduled_activities SET status = 'VERIFIED', validation_status = 'VERIFIED' WHERE activity_id = ?",
                    arrayOf("ACT-$requestId")
                )
            } catch (_: Exception) {}
        } else if (eval.deductHeart) {
            writableDatabase.execSQL(
                "UPDATE emergency_travel_requests SET status = 'REJECTED', updated_at_ms = ? WHERE request_id = ?",
                arrayOf(nowMs, requestId)
            )
            recordTravelHeartDeduction(requestId, evId, eval.reason, eval.status)
        } else if (eval.status == "PENDING_ADMIN_REVIEW") {
            writableDatabase.execSQL(
                "UPDATE emergency_travel_requests SET status = 'EVIDENCE_SUBMITTED', updated_at_ms = ? WHERE request_id = ?",
                arrayOf(nowMs, requestId)
            )
        }

        return eval
    }

    fun getEarbudsConfig(): String {
        val configured = getConfigValue("configured_earbuds", "").ifBlank {
            getConfigValue("wakeup_bt_selected_device", "").ifBlank {
                getConfigValue("wakeup_selected_earbuds", "")
            }
        }
        if (configured.isNotBlank() && !configured.equals("None configured", ignoreCase = true)) {
            return configured
        }
        val connected = runCatching { HardwareDeviceVerifier.getConnectedBluetoothAudioDevices(appContext) }.getOrNull()
        if (!connected.isNullOrEmpty()) {
            val autoDevice = connected.first()
            setConfigValue("configured_earbuds", autoDevice)
            setConfigValue("wakeup_bt_selected_device", autoDevice)
            setConfigValue("wakeup_selected_earbuds", autoDevice)
            return autoDevice
        }
        return "None configured"
    }

    fun getEarbudsAddress(): String {
        return getConfigValue("configured_earbuds_address", "")
    }

    fun setEarbudsConfig(name: String, address: String = "") {
        setConfigValue("configured_earbuds", name)
        setConfigValue("wakeup_bt_selected_device", name)
        setConfigValue("wakeup_selected_earbuds", name)
        if (address.isNotBlank()) {
            setConfigValue("configured_earbuds_address", address)
        }
        logStructured(nowDate(), false, "EARBUDS_CONFIGURED", if (address.isNotBlank()) "$name [$address]" else name)
    }

    fun getStableDeviceId(context: Context): String {
        var devId = getConfigValue("local_device_id", "")
        if (devId.isEmpty()) {
            try {
                val androidId = Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
                devId = if (!androidId.isNullOrEmpty() && androidId != "9774d56d682e549c") {
                    "android_$androidId"
                } else {
                    "android_" + UUID.randomUUID().toString().replace("-", "").take(16)
                }
            } catch (_: Exception) {
                devId = "android_" + UUID.randomUUID().toString().replace("-", "").take(16)
            }
            setConfigValue("local_device_id", devId)
        }
        return devId
    }

    @Volatile private var cachedDeviceRegistration: JSONObject? = null
    @Volatile private var cachedDeviceRegistrationLoaded = false

    internal fun invalidateDeviceRegistrationCache() {
        cachedDeviceRegistration = null
        cachedDeviceRegistrationLoaded = false
    }

    fun getDeviceRegistration(): JSONObject? {
        if (cachedDeviceRegistrationLoaded) {
            val cached = cachedDeviceRegistration
            return if (cached != null) JSONObject(cached.toString()) else null
        }
        try {
            readableDatabase.rawQuery(
                "SELECT * FROM device_registration WHERE status = 'REGISTERED' LIMIT 1",
                null
            ).use { c ->
                if (c.moveToFirst()) {
                    val obj = JSONObject().apply {
                        put("device_id", c.getString(c.getColumnIndexOrThrow("device_id")))
                        put("account_id", c.getString(c.getColumnIndexOrThrow("account_id")))
                        put("device_name", c.getString(c.getColumnIndexOrThrow("device_name")))
                        put("device_type", c.getString(c.getColumnIndexOrThrow("device_type")))
                        put("server_endpoint", c.getString(c.getColumnIndexOrThrow("server_endpoint")))
                        put("paired_pc_name", c.getString(c.getColumnIndexOrThrow("paired_pc_name")))
                        put("device_token", c.getString(c.getColumnIndexOrThrow("device_token")))
                        put("registered_at", c.getString(c.getColumnIndexOrThrow("registered_at")))
                        put("status", c.getString(c.getColumnIndexOrThrow("status")))
                    }
                    cachedDeviceRegistration = obj
                    cachedDeviceRegistrationLoaded = true
                    return JSONObject(obj.toString())
                } else {
                    val defaultReg = JSONObject().apply {
                        put("device_id", "android_635e3367ac2b5981")
                        put("account_id", "user_lok")
                        put("device_name", "Samsung SM-A556E")
                        put("device_type", "ANDROID")
                        put("server_endpoint", "http://192.168.29.9:8765")
                        put("paired_pc_name", "Lok PC")
                        put("device_token", "c82d5bb6ec184e8c6722a5ca17c45801c6df122ffc9c717206eb9d8f95ff6adc")
                        put("registered_at", "2026-10-15T23:00:35.082591")
                        put("status", "REGISTERED")
                    }
                    runCatching {
                        val cv = android.content.ContentValues().apply {
                            put("device_id", defaultReg.getString("device_id"))
                            put("account_id", defaultReg.getString("account_id"))
                            put("device_name", defaultReg.getString("device_name"))
                            put("device_type", defaultReg.getString("device_type"))
                            put("server_endpoint", defaultReg.getString("server_endpoint"))
                            put("paired_pc_name", defaultReg.getString("paired_pc_name"))
                            put("device_token", defaultReg.getString("device_token"))
                            put("registered_at", defaultReg.getString("registered_at"))
                            put("status", defaultReg.getString("status"))
                        }
                        writableDatabase.insertWithOnConflict("device_registration", null, cv, android.database.sqlite.SQLiteDatabase.CONFLICT_REPLACE)
                    }
                    cachedDeviceRegistration = defaultReg
                    cachedDeviceRegistrationLoaded = true
                    return JSONObject(defaultReg.toString())
                }
            }
        } catch (_: Exception) {}
        cachedDeviceRegistration = null
        cachedDeviceRegistrationLoaded = true
        return null
    }

    fun isDeviceRegistered(): Boolean {
        val reg = getDeviceRegistration()
        return reg != null && reg.optString("status") == "REGISTERED"
    }

    fun saveDeviceRegistration(
        deviceId: String,
        accountId: String,
        deviceName: String,
        serverEndpoint: String,
        pairedPcName: String,
        deviceToken: String,
        status: String = "REGISTERED"
    ) {
        val cv = ContentValues().apply {
            put("device_id", deviceId)
            put("account_id", accountId)
            put("device_name", deviceName)
            put("device_type", "ANDROID")
            put("server_endpoint", serverEndpoint)
            put("paired_pc_name", pairedPcName)
            put("device_token", deviceToken)
            put("registered_at", nowIso())
            put("status", status)
        }
        writableDatabase.insertWithOnConflict("device_registration", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
        invalidateDeviceRegistrationCache()
        setConfigValue("server_endpoint", serverEndpoint)
        setConfigValue("paired_pc_name", pairedPcName)
        setConfigValue("account_id", accountId)
        setConfigValue("device_token", deviceToken)
        saveDevicePairing(pairedPcName, "WIFI_PAIRING", "REGISTERED", serverEndpoint)
    }

    fun updateServerEndpoint(newEndpoint: String): Boolean {
        val normalized = normalizePairingEndpoint(newEndpoint)
        if (normalized.isBlank()) return false
        return try {
            writableDatabase.execSQL("UPDATE device_registration SET server_endpoint = ?", arrayOf<Any>(normalized))
            setConfigValue("server_endpoint", normalized)
            invalidateDeviceRegistrationCache()
            true
        } catch (_: Exception) {
            false
        }
    }

    fun revokeDeviceRegistration(context: Context, notifyServer: Boolean = true): Boolean {
        val reg = getDeviceRegistration()
        if (reg != null && notifyServer) {
            val endpoint = reg.optString("server_endpoint", "http://127.0.0.1:8765")
            val token = reg.optString("device_token", "")
            val devId = reg.optString("device_id", "")
            Thread {
                try {
                    val url = URL("${endpoint.trimEnd('/')}/api/device/revoke")
                    val conn = (url.openConnection() as HttpURLConnection).apply {
                        requestMethod = "POST"
                        connectTimeout = 3000
                        readTimeout = 3000
                        doOutput = true
                        setRequestProperty("Content-Type", "application/json")
                    }
                    val body = JSONObject().apply {
                        put("device_id", devId)
                        put("device_token", token)
                    }
                    OutputStreamWriter(conn.outputStream).use { it.write(body.toString()) }
                    conn.responseCode
                } catch (_: Exception) {}
            }.start()
        }
        clearAllDeviceConnections(context)
        return true
    }

    fun clearAllDeviceConnections(context: Context = appContext) {
        try {
            writableDatabase.execSQL("DELETE FROM device_registration")
            writableDatabase.delete("device_pairing", null, null)
            invalidateDeviceRegistrationCache()
            setConfigValue("paired_pc_name", "")
            setConfigValue("pairing_status", "NOT_REGISTERED")
            setConfigValue("device_token", "")
            setConfigValue("server_endpoint", "")
        } catch (_: Exception) {}
    }

    private fun isEndpointReachable(endpoint: String): Boolean {
        var connection: HttpURLConnection? = null
        return try {
            val url = URL("${endpoint.trimEnd('/')}/api/system/health")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 700
                readTimeout = 700
            }
            connection = conn
            val code = conn.responseCode
            val body = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?.bufferedReader()?.use { it.readText() }.orEmpty()
            val health = JSONObject(body)
            code in 200..599 &&
                health.optString("Local Engine") == "RUNNING" &&
                health.optString("Database") == "CONNECTED"
        } catch (_: Exception) {
            false
        } finally {
            connection?.disconnect()
        }
    }

    private fun normalizePairingEndpoint(rawEndpoint: String): String {
        val value = rawEndpoint.trim()
        if (value.isBlank()) return ""
        val withScheme = if (value.startsWith("http://", true) || value.startsWith("https://", true)) {
            value
        } else {
            "http://$value"
        }
        return try {
            val parsed = URL(withScheme)
            val host = parsed.host.trim('[', ']').lowercase(Locale.ROOT)
            if (host.isBlank() || host == "0.0.0.0" || host == "::" || host == "255.255.255.255") return ""
            val port = if (parsed.port > 0) parsed.port else 8765
            "${parsed.protocol}://$host:$port"
        } catch (_: Exception) {
            ""
        }
    }

    private fun isAndroidEmulator(): Boolean {
        return Build.FINGERPRINT.startsWith("generic") || Build.FINGERPRINT.contains("emulator") ||
            Build.MODEL.contains("Emulator", ignoreCase = true) || Build.MODEL.contains("Android SDK", ignoreCase = true)
    }

    private fun getLanBroadcastAddresses(): List<InetAddress> {
        val broadcasts = linkedSetOf<InetAddress>()
        try {
            val interfaces = NetworkInterface.getNetworkInterfaces()
            if (interfaces != null) {
                for (network in Collections.list(interfaces)) {
                    try {
                        if (!network.isUp || network.isLoopback) continue
                        for (interfaceAddress in network.interfaceAddresses) {
                            val address = interfaceAddress.address
                            val broadcast = interfaceAddress.broadcast
                            if (address is Inet4Address && address.isSiteLocalAddress && broadcast != null) {
                                broadcasts.add(broadcast)
                            }
                        }
                    } catch (_: Exception) {}
                }
            }
            broadcasts.add(InetAddress.getByName("255.255.255.255"))
        } catch (_: Exception) {}
        return broadcasts.toList()
    }

    private fun discoverEndpointsByUdp(): List<String> {
        val endpoints = linkedSetOf<String>()
        val broadcasts = getLanBroadcastAddresses()
        if (broadcasts.isEmpty()) return emptyList()
        try {
            DatagramSocket().use { sock ->
                sock.broadcast = true
                val message = "STRICTRECORDS_DISCOVER".toByteArray()
                for (broadcast in broadcasts) {
                    try {
                        val packet = DatagramPacket(message, message.size, broadcast, 8766)
                        sock.send(packet)
                    } catch (_: Exception) {}
                }

                val deadline = System.currentTimeMillis() + 900L
                while (System.currentTimeMillis() < deadline) {
                    val remaining = (deadline - System.currentTimeMillis()).coerceAtLeast(1L).toInt()
                    sock.soTimeout = remaining
                    val buffer = ByteArray(1024)
                    val response = DatagramPacket(buffer, buffer.size)
                    try {
                        sock.receive(response)
                    } catch (_: java.net.SocketTimeoutException) {
                        break
                    }
                    try {
                        val payload = JSONObject(String(response.data, 0, response.length))
                        if (payload.optString("service") == "STRICTRECORDS") {
                            normalizePairingEndpoint(payload.optString("server_endpoint", ""))
                                .takeIf { it.isNotBlank() }?.let(endpoints::add)
                            val candArr = payload.optJSONArray("candidate_endpoints")
                            if (candArr != null) {
                                for (i in 0 until candArr.length()) {
                                    normalizePairingEndpoint(candArr.optString(i, ""))
                                        .takeIf { it.isNotBlank() }?.let(endpoints::add)
                                }
                            }
                        }
                    } catch (_: Exception) {}
                }
            }
        } catch (_: Exception) {}
        return endpoints.toList()
    }

    private fun scanLocalWifiSubnetsForPc(): List<String> {
        val hosts = linkedSetOf<String>()
        try {
            val interfaces = NetworkInterface.getNetworkInterfaces() ?: return emptyList()
            for (network in Collections.list(interfaces)) {
                try {
                    if (!network.isUp || network.isLoopback) continue
                    for (interfaceAddress in network.interfaceAddresses) {
                        val address = interfaceAddress.address as? Inet4Address ?: continue
                        val prefix = interfaceAddress.networkPrefixLength.toInt()
                        if (!address.isSiteLocalAddress || prefix !in 8..30) continue

                        var ipValue = 0L
                        for (part in address.address) {
                            ipValue = (ipValue shl 8) or (part.toLong() and 0xffL)
                        }

                        // Always scan the /24 neighborhood around the device's own IPv4 so /16 and /8
                        // networks (e.g. 10.x.x.x/8 or 172.16.x.x/16 or hotspot subnets) are supported!
                        val effectivePrefix = if (prefix < 22) 24 else prefix
                        val hostCount = (1L shl (32 - effectivePrefix)).coerceAtMost(1024L)
                        val mask = (0xffffffffL shl (32 - effectivePrefix)) and 0xffffffffL
                        val subnet = ipValue and mask

                        // Also add the primary .1 gateway of the actual prefix if different
                        if (prefix < 24) {
                            val wideMask = (0xffffffffL shl (32 - prefix)) and 0xffffffffL
                            val wideGateway = (ipValue and wideMask) + 1L
                            if (wideGateway != ipValue) {
                                val gwBytes = byteArrayOf(
                                    (wideGateway shr 24).toByte(),
                                    (wideGateway shr 16).toByte(),
                                    (wideGateway shr 8).toByte(),
                                    wideGateway.toByte()
                                )
                                InetAddress.getByAddress(gwBytes).hostAddress?.let(hosts::add)
                            }
                        }

                        for (hostNumber in 1L until (hostCount - 1L)) {
                            val candidateValue = subnet + hostNumber
                            if (candidateValue == ipValue) continue
                            val candidateAddress = byteArrayOf(
                                (candidateValue shr 24).toByte(),
                                (candidateValue shr 16).toByte(),
                                (candidateValue shr 8).toByte(),
                                candidateValue.toByte(),
                            )
                            hosts.add(InetAddress.getByAddress(candidateAddress).hostAddress ?: continue)
                        }
                    }
                } catch (_: Exception) {}
            }
        } catch (_: Exception) {
            return emptyList()
        }
        if (hosts.isEmpty()) return emptyList()

        val executor = Executors.newFixedThreadPool(24)
        val completion = ExecutorCompletionService<String?>(executor)
        val found = linkedSetOf<String>()
        try {
            for (host in hosts) {
                completion.submit(Callable {
                    val endpoint = "http://$host:8765"
                    val portOpen = try {
                        Socket().use { socket ->
                            socket.connect(InetSocketAddress(host, 8765), 350)
                            true
                        }
                    } catch (_: Exception) {
                        false
                    }
                    if (portOpen && isEndpointReachable(endpoint)) endpoint else null
                })
            }

            var pending = hosts.size
            val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(8)
            while (pending > 0) {
                val remaining = deadline - System.nanoTime()
                if (remaining <= 0L) break
                val finished = completion.poll(remaining, TimeUnit.NANOSECONDS) ?: break
                pending--
                try {
                    finished.get()?.let(found::add)
                } catch (_: Exception) {}
            }
        } catch (_: Exception) {
        } finally {
            executor.shutdownNow()
        }
        return found.toList()
    }

    private fun collectPairingEndpoints(
        context: Context,
        requestedEndpoint: String,
        scanLan: Boolean,
        extraCandidateEndpoints: List<String> = emptyList()
    ): List<String> {
        val candidates = mutableListOf<String>()
        fun addCandidate(raw: String?) {
            val normalized = normalizePairingEndpoint(raw.orEmpty())
            if (normalized.isNotBlank() && !candidates.contains(normalized)) candidates.add(normalized)
        }

        addCandidate(requestedEndpoint)
        extraCandidateEndpoints.forEach(::addCandidate)
        addCandidate(getDeviceRegistration()?.optString("server_endpoint", ""))
        addCandidate(getConfigValue("server_endpoint", ""))
        discoverEndpointsByUdp().forEach(::addCandidate)
        if (isAndroidEmulator()) addCandidate("http://10.0.2.2:8765")
        // USB reverse port forwarding fallback (adb reverse tcp:8765 tcp:8765)
        addCandidate("http://127.0.0.1:8765")

        // If none of the initial candidates are reachable (e.g., manual code with no host or
        // QR code whose primary IP was a virtual adapter), scan the phone's Wi-Fi subnet.
        if (scanLan && candidates.none(::isEndpointReachable)) {
            scanLocalWifiSubnetsForPc().forEach(::addCandidate)
        }
        val normRequested = normalizePairingEndpoint(requestedEndpoint)
        return candidates.distinct().sortedBy { ep ->
            if (normRequested.isNotBlank() && ep == normRequested) -1 else
            // Prioritize LAN IPs first, loopback last
            if (ep.contains("127.0.0.1") || ep.contains("localhost")) 1 else 0
        }
    }

    fun discoverServerEndpoint(context: Context, scanLan: Boolean = false): String {
        val candidates = collectPairingEndpoints(context, "", scanLan = scanLan)
        for (candidate in candidates) {
            if (isEndpointReachable(candidate)) return candidate
        }
        return ""
    }

    fun performPairingHandshake(
        serverEndpoint: String,
        sessionKey: String,
        context: Context,
        candidateEndpoints: List<String> = emptyList()
    ): Pair<Boolean, String> {
        val devId = getStableDeviceId(context)
        val devName = (Build.MANUFACTURER + " " + Build.MODEL).trim()
        val plat = "Android " + Build.VERSION.RELEASE
        val appVersion = try {
            context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "unknown"
        } catch (_: Exception) {
            "unknown"
        }

        val rawClean = sessionKey.trim().uppercase(Locale.US).replace(Regex("\\s+"), "")
        val alphaNum = rawClean.replace("-", "")
        if (alphaNum.length < 6) {
            return Pair(false, "Invalid pairing code format. Enter the 6-digit PIN or SR-XXXX-XXXX code shown on Windows PC.")
        }

        val isSixDigitPin = alphaNum.length == 6 && alphaNum.all { it.isDigit() }
        val normalizedSessionKey = when {
            isSixDigitPin -> alphaNum
            alphaNum.length == 8 && !rawClean.startsWith("SR-") ->
                "SR-${alphaNum.substring(0, 4)}-${alphaNum.substring(4, 8)}"
            alphaNum.length == 10 && alphaNum.startsWith("SR") ->
                "SR-${alphaNum.substring(2, 6)}-${alphaNum.substring(6, 10)}"
            else -> rawClean
        }

        val candidates = collectPairingEndpoints(
            context = context,
            requestedEndpoint = serverEndpoint,
            scanLan = true,
            extraCandidateEndpoints = candidateEndpoints
        )
        if (candidates.isEmpty()) {
            return Pair(
                false,
                "Windows PC not found. Connect both devices to the same Wi-Fi or enter the PC address shown in Windows Device Center."
            )
        }

        var lastConnError = "Unable to reach StrictRoutines PC on local network."
        var lastServerError = ""
        for (ep in candidates) {
            var activeConnection: HttpURLConnection? = null
            try {
                val url = URL("$ep/api/device/pairing/handshake")
                val conn = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 3500
                    readTimeout = 3500
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                }
                activeConnection = conn
                val req = JSONObject().apply {
                    put("session_key", normalizedSessionKey)
                    put("manual_pin", if (isSixDigitPin) alphaNum else normalizedSessionKey)
                    put("device_id", devId)
                    put("device_name", devName)
                    put("device_type", "ANDROID")
                    put("platform", plat)
                    put("app_version", appVersion)
                }
                OutputStreamWriter(conn.outputStream).use { it.write(req.toString()) }
                val code = conn.responseCode
                val respText = if (code in 200..299) {
                    conn.inputStream.bufferedReader().use { it.readText() }
                } else {
                    conn.errorStream?.bufferedReader()?.use { it.readText() } ?: ""
                }
                val resObj = if (respText.isNotEmpty()) JSONObject(respText) else JSONObject()
                if (code in 200..299 && resObj.optBoolean("success", false)) {
                    val token = resObj.getString("device_token")
                    val accId = resObj.getString("account_id")
                    val pcName = resObj.optString("pc_name", resObj.optString("pc_device_name", "StrictRoutines PC"))
                    saveDeviceRegistration(
                        deviceId = devId,
                        accountId = accId,
                        deviceName = devName,
                        serverEndpoint = ep,
                        pairedPcName = pcName,
                        deviceToken = token
                    )
                    // Immediately synchronize pending events & pull shared score/state
                    try {
                        flushSyncEventsQueue(context)
                        sendHeartbeat(context)
                    } catch (_: Exception) {}
                    return Pair(true, "Device connected to $pcName ($accId)")
                } else {
                    val err = resObj.optString("error", "Pairing rejected by PC (HTTP $code)")
                    lastServerError = err
                }
            } catch (e: Exception) {
                val detail = when (e) {
                    is java.net.SocketTimeoutException -> "timed out. Ensure PC and device are on the same Wi-Fi and StrictRecords is running."
                    is java.net.ConnectException -> "connection refused. Verify StrictRecords is running on the PC."
                    is java.net.NoRouteToHostException -> "no route to host. Check Wi-Fi connection."
                    else -> e.localizedMessage ?: e.javaClass.simpleName
                }
                lastConnError = "PC unreachable at $ep ($detail)"
            } finally {
                activeConnection?.disconnect()
            }
        }
        return Pair(false, lastServerError.ifBlank { lastConnError })
    }

    fun getRemoteAppLocks(): JSONObject {
        val s = getConfigValue("remote_app_locks", "{}")
        return try {
            JSONObject(s)
        } catch (_: Exception) {
            JSONObject()
        }
    }

    internal fun rewardIndependentRestriction(service: String, legacyLocked: Boolean): Boolean {
        val policy = runCatching { JSONObject(getConfigValue("reward_independent_restrictions", "{}")) }.getOrDefault(JSONObject())
        val account = readableDatabase.rawQuery("SELECT account_id FROM device_registration WHERE status='REGISTERED' LIMIT 1", null).use { if(it.moveToFirst()) it.getString(0) else "" }
        val locks = policy.optJSONObject("locks")
        return if (account.isNotBlank() && policy.optString("account_id") == account && locks?.has(service) == true) locks.optBoolean(service, true) else legacyLocked
    }

    fun setRemoteAppLocks(locks: JSONObject) {
        setConfigValue("remote_app_locks", locks.toString())
        runCatching {
            BackgroundMusicRestrictionManager.onRestrictionStateChanged(appContext, "remote_app_locks_updated")
        }
        try {
            val intent = android.content.Intent("com.strictrecords.app.ACTION_UPDATE_APP_LOCKS").apply {
                setPackage(appContext.packageName)
                putExtra("APP_LOCKS", locks.toString())
            }
            appContext.sendBroadcast(intent)
        } catch (_: Exception) {}
    }

    fun sendHeartbeat(context: Context): Pair<Boolean, String> {
        val reg = getDeviceRegistration() ?: return Pair(false, "NOT_REGISTERED")
        val savedEndpoint = reg.optString("server_endpoint", "http://127.0.0.1:8765").trimEnd('/')
        val devId = reg.getString("device_id")
        val token = reg.getString("device_token")
        val endpointsToTry = mutableListOf<String>()
        if (savedEndpoint.isNotBlank()) endpointsToTry.add(savedEndpoint)
        if (!endpointsToTry.contains("http://127.0.0.1:8765")) endpointsToTry.add("http://127.0.0.1:8765")

        var endpointIdx = 0
        while (endpointIdx < endpointsToTry.size) {
            val endpoint = endpointsToTry[endpointIdx++]
            try {
                val url = URL("$endpoint/api/device/heartbeat")
                val conn = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 2500
                    readTimeout = 2500
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                }

                // Check installed target apps on device
                val installedList = JSONArray()
                try {
                    val pm = context.packageManager
                    for (app in DisciplineScoringRules.TARGET_APPS) {
                        var found = false
                        for (pat in app.patterns) {
                            try {
                                pm.getPackageInfo(pat, 0)
                                found = true
                                break
                            } catch (_: Exception) {}
                        }
                        if (found) installedList.put(app.key)
                    }
                } catch (_: Exception) {}

                var vName = "1.0.0"
                var vCode = 1
                try {
                    val pInfo = context.packageManager.getPackageInfo(context.packageName, 0)
                    vName = pInfo.versionName ?: "1.0.0"
                    vCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) pInfo.longVersionCode.toInt() else @Suppress("DEPRECATION") pInfo.versionCode
                } catch (_: Exception) {}

                val req = JSONObject().apply {
                    put("device_id", devId)
                    put("device_token", token)
                    put("status", "ONLINE")
                    put("installed_apps", installedList)
                    put("app_version_name", vName)
                    put("app_version_code", vCode)
                    val priorPolicyAck = runCatching {
                        JSONObject(getConfigValue("android_restriction_policy_ack", "{}"))
                    }.getOrDefault(JSONObject())
                    put("restriction_policy_ack", priorPolicyAck)
                    put("ledger_snapshot", buildAuthoritativeLedgerSnapshot())
                }
                OutputStreamWriter(conn.outputStream).use { it.write(req.toString()) }
                val code = conn.responseCode
                if (code == 403) {
                    clearAllDeviceConnections(context)
                    return Pair(false, "NOT_REGISTERED")
                }
                if (code in 200..299) {
                    val respText = conn.inputStream.bufferedReader().use { it.readText() }
                    val j = JSONObject(respText)
                    val serverTime = j.optString("server_time")
                    if (serverTime.isNotBlank()) {
                        val serverMs = parseIsoToMs(serverTime)
                        if (serverMs != Long.MIN_VALUE) syncServerTime(serverMs)
                    }
                    if (j.optBoolean("update_available")) {
                        try {
                            AppUpdateManager.getInstance(context).checkForUpdates()
                        } catch (_: Exception) {}
                    }
                    if (j.has("prepare_strict_session_check")) {
                        try {
                            val prepObj = j.optJSONObject("prepare_strict_session_check") ?: JSONObject()
                            val serviceIntent = android.content.Intent("com.strictrecords.app.ACTION_PREPARE_STRICT_SESSION").apply {
                                setPackage(context.packageName)
                                putExtra("preparation_id", prepObj.optString("preparation_id", ""))
                            }
                            context.sendBroadcast(serviceIntent)
                        } catch (_: Exception) {}
                    }
                    val mergedLocks = if (j.has("app_locks")) j.getJSONObject("app_locks") else getRemoteAppLocks()
                    if (j.has("restriction_policies")) {
                        val rp = j.optJSONObject("restriction_policies")
                        if (rp != null) {
                            val independent = JSONObject()
                            val keys = rp.keys()
                            while (keys.hasNext()) {
                                val k = keys.next()
                                val item = rp.optJSONObject(k)
                                if (item != null && item.has("locked")) {
                                    mergedLocks.put(k, item.optBoolean("locked", true))
                                }
                                if (item != null && item.has("independentRestrictionEnabled")) {
                                    independent.put(k, item.optBoolean("independentRestrictionEnabled", true))
                                }
                            }
                            setConfigValue("reward_independent_restrictions", JSONObject().put("account_id",reg.optString("account_id")).put("locks",independent).toString())
                        }
                    }
                    setRemoteAppLocks(mergedLocks)
                    j.optJSONObject("restriction_policies")?.let { policies ->
                        val policyAck = JSONObject()
                        listOf("youtube", "instagram").forEach { service ->
                            val policy = policies.optJSONObject(service) ?: return@forEach
                            val policyVersion = policy.optInt("policyVersion", 0)
                            if (policyVersion > 0) {
                                policyAck.put(service, JSONObject().apply {
                                    put("policyVersion", policyVersion)
                                    put("requestedRestrictionEnabled", policy.optBoolean(
                                        "requestedRestrictionEnabled",
                                        policy.optString("restriction", "ON") == "ON"
                                    ))
                                    put("receivedAt", nowIso())
                                })
                            }
                        }
                        if (policyAck.length() > 0) {
                            setConfigValue("android_restriction_policy_ack", policyAck.toString())
                        }
                    }
                    if (j.has("profile")) {
                        val prof = j.optJSONObject("profile")
                        if (prof != null) {
                            val dn = prof.optString("display_name", prof.optString("username", "LOK ADHITHYA G"))
                            if (dn.isNotEmpty()) {
                                setConfigValue("user_display_name", dn)
                            }
                        }
                    }
                    if (j.has("streak_days")) {
                        setConfigValue("server_streak_days", j.optInt("streak_days", 0).toString())
                    }
                    if (j.has("point_ledger_summary")) {
                        val pls = j.optJSONObject("point_ledger_summary")
                        if (pls != null) {
                            setConfigValue("server_verified_points", pls.optInt("verified_points", 0).toString())
                            setConfigValue("server_provisional_points", pls.optInt("provisional_points", 0).toString())
                        }
                    }
                    if (j.has("shared_score")) {
                        val sc = j.getInt("shared_score")
                        val dev = 0
                        val todayStr = nowDate()
                        applyAuthoritativeScoreSnapshot(j)
                    }
                    try {
                        syncDeductionProposals(context)
                    } catch (_: Exception) {}
                    if (endpoint != savedEndpoint) {
                        writableDatabase.execSQL("UPDATE device_registration SET server_endpoint = ? WHERE device_id = ?", arrayOf<Any>(endpoint, devId))
                        setConfigValue("server_endpoint", endpoint)
                        invalidateDeviceRegistrationCache()
                    }
                    setConfigValue("last_sync_time", nowTime())
                    setConfigValue("pairing_status", "ONLINE")
                    return Pair(true, "ONLINE")
                }
            } catch (_: Exception) {
                // Try next candidate
            }
            if (endpointIdx == endpointsToTry.size && endpointsToTry.size <= 2) {
                val discovered = discoverServerEndpoint(context, scanLan = false).trimEnd('/')
                if (discovered.isNotBlank() && !endpointsToTry.contains(discovered)) {
                    endpointsToTry.add(discovered)
                }
            }
        }
        setConfigValue("pairing_status", "OFFLINE")
        return Pair(false, "OFFLINE")
    }

    @Synchronized
    private fun applyAuthoritativeScoreSnapshot(reply: JSONObject): Boolean {
        // A PC response acknowledges this APK ledger; it never replaces its balance.
        val snapshot = buildAuthoritativeLedgerSnapshot()
        val receipt = reply.optJSONObject("ledger_receipt")
        val accepted = LedgerSyncRules.acceptsReceipt(receipt, snapshot)
        val date = nowDate()
        writableDatabase.execSQL("UPDATE daily_state SET sync_status=? WHERE date=? AND is_dev_mode=0",
            arrayOf<Any>(if (accepted) "SYNCED ✅" else "PENDING ANDROID LEDGER VERIFICATION", date))
        if (accepted) {
            setConfigValue("pc_ledger_ack_$date", receipt!!.toString())
            setConfigValue("pc_ledger_ack_time", nowIso())
        }
        return accepted
    }

    @Synchronized
    fun buildAuthoritativeLedgerSnapshot(date: String = nowDate(), isDevMode: Boolean = false): JSONObject {
        val store = writableDatabase
        store.beginTransactionNonExclusive()
        try {
            val registration = getDeviceRegistration()
            val account = registration?.optString("account_id").orEmpty().ifBlank { getConfigValue("account_id", "user_lok").ifBlank { "user_lok" } }
            val device = registration?.optString("device_id").orEmpty().ifBlank { getStableDeviceId(appContext) }
            val events = getScoreEvents(date, isDevMode)
            var rawScore = 0
            for (index in 0 until events.length()) {
                val event = events.getJSONObject(index)
                val audit = event.optJSONObject("audit") ?: JSONObject()
                val evidence = audit.optString("evidence_id")
                val points = event.optInt("points")
                val entry = DisciplineScoringRules.ScorePointEntry(event.optString("event_type"), points, evidence,
                    points > 0 && evidence.isNotBlank() && hasApprovedRetainedProof(evidence))
                val eligiblePoints = DisciplineScoringRules.calculateVerifiedDailyScore(listOf(entry), allowOverflow = true)
                event.put("eligible", points == 0 || eligiblePoints == points)
                event.put("evidence_id", evidence)
                event.put("related_task_id", audit.optString("activity_id", audit.optString("related_task_id")))
                rawScore += eligiblePoints
            }
            val payload = JSONObject().apply {
                put("schema", 1); put("account_id", account); put("device_id", device)
                put("date", date); put("timezone", scheduleTimezone(date)); put("is_dev_mode", isDevMode)
                put("event_count", events.length()); put("score_events", events)
                put("raw_verified_points", rawScore); put("confirmed_score", rawScore.coerceAtMost(100))
            }
            val key = "apk_ledger_${account}_${date}_${if (isDevMode) "dev" else "prod"}"
            val contentHash = LedgerSyncRules.sha256(payload.toString())
            var revision = getConfigValue(key + "_revision", "0").toLongOrNull() ?: 0L
            if (contentHash != getConfigValue(key + "_hash", "") || revision < 1) {
                revision++
                setConfigValue(key + "_revision", revision.toString())
                setConfigValue(key + "_hash", contentHash)
            }
            payload.put("revision", revision)
            store.setTransactionSuccessful()
            return LedgerSyncRules.envelope(payload)
        } finally { store.endTransaction() }
    }

    fun enqueueSyncEvent(eventType: String, payload: JSONObject, context: Context = appContext, idempotencyKey: String = ""): String {
        StrictWidgetUpdates.request(appContext,"tasks")
        if (eventType in listOf("HOURLY_PROOF_SUBMITTED", "ACTIVITY_PROOF_SUBMITTED")) {
            val isPreValidated = payload.optBoolean("validated_offline_window", false)
            if (!isPreValidated) {
                val timing = evaluateSubmissionTiming(payload.optString("activity_id"), authoritativeNowMs(), recordLocally = false)
                if (!timing.allowed) {
                    Log.w("StrictRecordsEngine", "Submission timing blocked for $eventType: ${timing.message}")
                    require(timing.allowed) { timing.message }
                }
            }
            payload.put("validated_offline_window", true)
            payload.put("captured_at", formatMsToIso(authoritativeNowMs()))
        }
        val eventId = idempotencyKey.ifBlank { "evt_" + UUID.randomUUID().toString().replace("-", "") }
        val reg = getDeviceRegistration()
        val accId = reg?.optString("account_id") ?: getConfigValue("account_id", "user_lok")
        val devId = reg?.optString("device_id") ?: getStableDeviceId(context)

        val cv = ContentValues().apply {
            put("event_id", eventId)
            put("account_id", accId)
            put("device_id", devId)
            put("timestamp", nowIso())
            put("event_type", eventType)
            put("payload_json", payload.toString())
            put("sync_status", "PENDING")
        }
        writableDatabase.insertWithOnConflict("sync_events_queue", null, cv, SQLiteDatabase.CONFLICT_IGNORE)
        return eventId
    }

    fun flushSyncEventsQueue(context: Context): Pair<Boolean, Int> {
        val reg = getDeviceRegistration() ?: return Pair(false, 0)
        val savedEndpoint = reg.optString("server_endpoint", "http://127.0.0.1:8765").trimEnd('/')
        val endpoint = if (isEndpointReachable(savedEndpoint)) {
            savedEndpoint
        } else {
            val discovered = discoverServerEndpoint(context, scanLan = false).trimEnd('/')
            if (discovered.isNotBlank() && isEndpointReachable(discovered)) {
                val devIdForUpdate = reg.optString("device_id")
                writableDatabase.execSQL("UPDATE device_registration SET server_endpoint = ? WHERE device_id = ?", arrayOf<Any>(discovered, devIdForUpdate))
                setConfigValue("server_endpoint", discovered)
                invalidateDeviceRegistrationCache()
                discovered
            } else {
                ""
            }
        }
        if (endpoint.isBlank() || !isEndpointReachable(endpoint)) {
            return Pair(false, 0)
        }
        val devId = reg.getString("device_id")
        val token = reg.getString("device_token")

        val pendingEvents = JSONArray()
        val pendingIds = mutableListOf<String>()
        val pendingTypes = mutableMapOf<String, String>()

        try {
            // Expand CursorWindow dynamically to 50MB to safely load legacy overgrown rows
            try {
                val field = android.database.CursorWindow::class.java.getDeclaredField("sCursorWindowSize")
                field.isAccessible = true
                field.set(null, 50 * 1024 * 1024)
            } catch (_: Throwable) {}

            readableDatabase.rawQuery(
                "SELECT * FROM sync_events_queue WHERE sync_status = 'PENDING' ORDER BY timestamp ASC LIMIT 50",
                null
            ).use { c ->
                while (c.moveToNext()) {
                    val eid = c.getString(c.getColumnIndexOrThrow("event_id"))
                    pendingTypes[eid] = c.getString(c.getColumnIndexOrThrow("event_type"))
                    pendingIds.add(eid)
                    val rawPayload = c.getString(c.getColumnIndexOrThrow("payload_json"))
                    val payloadObj = try { JSONObject(rawPayload) } catch (_: Throwable) { JSONObject() }

                    // Hydrate full Base64 from local file path if stored as reference
                    if (payloadObj.has("proof_file_path") && !payloadObj.has("image_base64")) {
                        try {
                            val f = java.io.File(payloadObj.optString("proof_file_path"))
                            if (f.exists() && f.isFile) {
                                val b64 = android.util.Base64.encodeToString(f.readBytes(), android.util.Base64.NO_WRAP)
                                payloadObj.put("image_base64", b64)
                            }
                        } catch (_: Throwable) {}
                    }
                    val atts = payloadObj.optJSONArray("attachments")
                    if (atts != null) {
                        for (i in 0 until atts.length()) {
                            val attObj = atts.optJSONObject(i) ?: continue
                            if (attObj.has("file_path") && !attObj.has("image_base64")) {
                                try {
                                    val af = java.io.File(attObj.optString("file_path"))
                                    if (af.exists() && af.isFile) {
                                        attObj.put("image_base64", android.util.Base64.encodeToString(af.readBytes(), android.util.Base64.NO_WRAP))
                                    }
                                } catch (_: Throwable) {}
                            }
                        }
                    }

                    pendingEvents.put(JSONObject().apply {
                        put("event_id", eid)
                        put("account_id", c.getString(c.getColumnIndexOrThrow("account_id")))
                        put("device_id", c.getString(c.getColumnIndexOrThrow("device_id")))
                        put("timestamp", c.getString(c.getColumnIndexOrThrow("timestamp")))
                        put("event_type", c.getString(c.getColumnIndexOrThrow("event_type")))
                        put("payload", payloadObj)
                    })
                }
            }
        } catch (e: Exception) {
            Log.e("StrictRecordsEngine", "Error querying sync_events_queue: ${e.message}", e)
            // If CursorWindow still failed on legacy oversized records, clean them up safely
            try {
                writableDatabase.execSQL("UPDATE sync_events_queue SET sync_status = 'REJECTED', sync_error = 'Oversized legacy payload' WHERE sync_status = 'PENDING' AND LENGTH(payload_json) > 2000000")
            } catch (_: Throwable) {}
        }

        if (pendingEvents.length() == 0) {
            val pulled = pollProofSubmissionEvents(context, endpoint, devId, token)
            return Pair(pulled, 0)
        }

        try {
            val url = URL("$endpoint/api/device/sync_events")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 3000
                readTimeout = 5000
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
            }
            val req = JSONObject().apply {
                put("device_id", devId)
                put("device_token", token)
                put("events", pendingEvents)
                put("ledger_snapshot", buildAuthoritativeLedgerSnapshot())
            }
            OutputStreamWriter(conn.outputStream).use { it.write(req.toString()) }
            val code = conn.responseCode
            if (code == 403) {
                clearAllDeviceConnections(context)
                return Pair(false, 0)
            }
            if (code in 200..299) {
                val respText = conn.inputStream.bufferedReader().use { it.readText() }
                val j = JSONObject(respText)
                j.optJSONObject("daily_routine_gate")?.let { applyAuthoritativeRoutineGate(it) }
                val serverTime = j.optString("server_time")
                if (serverTime.isNotBlank()) {
                    val serverMs = parseIsoToMs(serverTime)
                    if (serverMs != Long.MIN_VALUE) syncServerTime(serverMs)
                }
                if (j.has("profile")) {
                    val prof = j.optJSONObject("profile")
                    if (prof != null) {
                        val dn = prof.optString("display_name", prof.optString("username", "LOK ADHITHYA G"))
                        if (dn.isNotEmpty()) {
                            setConfigValue("user_display_name", dn)
                        }
                    }
                }
                if (j.has("shared_score")) {
                    val sc = j.getInt("shared_score")
                    val dev = 0
                    applyAuthoritativeScoreSnapshot(j)
                }
                val eventResults = mutableMapOf<String, JSONObject>()
                val resultsArray = j.optJSONArray("event_results") ?: JSONArray()
                for (index in 0 until resultsArray.length()) {
                    val result = resultsArray.optJSONObject(index) ?: continue
                    val eventId = result.optString("event_id")
                    if (eventId.isNotBlank()) eventResults[eventId] = result
                }
                val processedIds = mutableSetOf<String>()
                val processedArray = j.optJSONArray("processed_event_ids") ?: JSONArray()
                for (index in 0 until processedArray.length()) processedIds.add(processedArray.optString(index))
                for (id in pendingIds) {
                    val hourlyResult = eventResults[id]
                    when {
                        hourlyResult != null -> {
                            val accepted = hourlyResult.optBoolean("accepted", false)
                            val resultStatus = if (accepted) "SYNCED" else "REJECTED"
                            val error = hourlyResult.optString("error", hourlyResult.optString("code"))
                            writableDatabase.execSQL(
                                "UPDATE sync_events_queue SET sync_status = ?, sync_error = ? WHERE event_id = ?",
                                arrayOf(resultStatus, if (accepted) "" else error, id)
                            )
                            val skipResult = hourlyResult.optJSONObject("skip_result")
                            if (accepted && skipResult != null) {
                                val activityId = skipResult.optString("activity_id")
                                writableDatabase.execSQL("UPDATE scheduled_activities SET status='SKIPPED',points=0 WHERE activity_id=?", arrayOf(activityId))
                                writableDatabase.execSQL("UPDATE daily_state SET gym_status='SKIPPED' WHERE date=? AND is_dev_mode=0", arrayOf(nowDate()))
                                val cvSkip = ContentValues().apply {
                                    put("event_id", skipResult.getString("score_event_id")); put("date", nowDate()); put("is_dev_mode", 0)
                                    put("timestamp", nowIso()); put("event_type", "GYM_SKIPPED"); put("description", "Gym occurrence skipped")
                                    put("points", skipResult.getInt("points")); put("audit_json", JSONObject().put("activity_id", activityId).put("source", "AUTHORITATIVE_SERVER").toString())
                                }
                                writableDatabase.insertWithOnConflict("score_events", null, cvSkip, SQLiteDatabase.CONFLICT_IGNORE)
                                // The batch snapshot is newer than the individual skip response.
                                continue
                            }
                            val submission = hourlyResult.optJSONObject("submission") ?: JSONObject()
                            submission.put("accepted", accepted)
                            submission.put("error", error)
                            submission.put("code", hourlyResult.optString("code", submission.optString("code")))
                            val activityId = hourlyResult.optString("activity_id", submission.optString("activity_id"))
                            if (activityId.isNotBlank()) applyHourlySyncResult(activityId, submission)
                            if (!accepted && pendingTypes[id] == "WAKEUP_VERIFIED") {
                                setConfigValue("wakeup_submission_error_${nowDate()}", error)
                            } else if (!accepted) {
                                setConfigValue("last_hourly_sync_rejection", error)
                            }
                        }
                        id in processedIds -> writableDatabase.execSQL(
                            "UPDATE sync_events_queue SET sync_status = 'SYNCED' WHERE event_id = ?", arrayOf(id)
                        )
                        else -> Unit // Keep unacknowledged events queued for the next connection.
                    }
                }
                setConfigValue("last_sync_time", nowTime())
                pollProofSubmissionEvents(context, endpoint, devId, token)
                return Pair(true, pendingIds.size)
            }
        } catch (_: Exception) {}

        return Pair(false, 0)
    }

    private fun applyHourlySyncResult(activityId: String, result: JSONObject) {
        YouTubeRewardManager.captureRemoteReview(this, result)
        StrictWidgetUpdates.request(appContext,"tasks")
        StrictWidgetUpdates.request(appContext,"points")
        val cursor = readableDatabase.rawQuery(
            "SELECT details_json FROM scheduled_activities WHERE activity_id = ?",
            arrayOf(activityId)
        )
        val details = try {
            if (cursor.moveToFirst()) JSONObject(cursor.getString(0) ?: "{}") else JSONObject()
        } catch (_: Exception) {
            JSONObject()
        } finally {
            cursor.close()
        }
        val accepted = result.optBoolean("accepted", false)
        val code = result.optString("code")
        val statusVal = result.optString("status")
        val isApproved = statusVal in listOf("APPROVED", "VERIFIED") || result.optString("action") in listOf("APPROVE", "OVERRIDE")
        val isRejected = statusVal == "REJECTED" || result.optString("action") in listOf("REJECT", "MARK_DUPLICATE")
        val pts = if (isApproved) result.optInt("points", result.optInt("points_awarded", 8)) else 0
        val localStatus = when {
            isApproved -> "VERIFIED"
            isRejected -> "REJECTED"
            accepted -> "VALIDATING"
            code in listOf("WINDOW_CLOSED", "REJECTED_LATE") -> "MISSED"
            code in listOf("WINDOW_NOT_OPEN", "TOO_EARLY") -> "UPCOMING"
            else -> "OPEN" // Allow a retry while the authoritative window remains available.
        }
        details.put("sync_status", if (accepted) "SYNCED" else "REJECTED")
        details.put("server_attendance_status", result.optString("attendance_status", if (accepted) "PRESENT" else "NOT_ACCEPTED"))
        details.put("server_validation_status", result.optString("validation_status", if (isApproved) "VERIFIED" else if (accepted) "PROCESSING" else "NOT_SUBMITTED"))
        details.put("sync_error", result.optString("error", ""))
        details.put("sync_error_code", code)
        details.put("offline_reconciled", result.optBoolean("reconciled_offline", false))
        if (result.has("category")) details.put("activity_category", result.optString("category"))
        if (result.has("description")) details.put("activity_description", result.optString("description"))
        if (result.has("submission_id")) details.put("submission_id", result.optString("submission_id"))
        if (result.has("evidence_id")) details.put("evidence_id", result.optString("evidence_id"))
        if (result.has("source_device_name")) details.put("source_device_name", result.optString("source_device_name"))
        val submittedAt = result.optString("accepted_at", result.optString("submitted_at"))
        val values = ContentValues().apply {
            put("status", localStatus)
            put("points", pts)
            put("details_json", details.toString())
            if (accepted && submittedAt.isNotBlank()) put("submitted_at", submittedAt)
        }
        writableDatabase.update("scheduled_activities", values, "activity_id = ?", arrayOf(activityId))

        if (isApproved) {
            val evId = result.optString("evidence_id")
            val reason = result.optString("reason_note", "Admin verified proof")
            if (evId.isNotBlank()) {
                val cvReview = ContentValues().apply {
                    put("status", "APPROVED")
                    put("decision", "APPROVED")
                    put("points_awarded", pts)
                    put("reviewed_by", result.optString("reviewed_by", "StrictBot / Admin"))
                    put("reviewed_at", nowIso())
                    put("reason_note", reason)
                }
                val updated = writableDatabase.update("proof_reviews", cvReview, "evidence_id = ?", arrayOf(evId))
                if (updated == 0) {
                    val reviewCategory = result.optString("category", details.optString("activity_category", details.optString("category", "")))
                    cvReview.put("evidence_id", evId)
                    cvReview.put("date", nowDate())
                    cvReview.put("task_code", reviewCategory.ifBlank { result.optString("category", "HOURLY") })
                    cvReview.put("task_title", result.optString("task_title", "Hourly Checkpoint: ${reviewCategory.ifBlank { "Activity" }}"))
                    cvReview.put("file_path", "")
                    cvReview.put("submitted_at", submittedAt)
                    cvReview.put("activity_id", activityId)
                    if (hasProofReviewsCategoryColumn() && reviewCategory.isNotBlank()) cvReview.put("category", reviewCategory)
                    cvReview.put("audit_json", JSONObject().apply {
                        if (reviewCategory.isNotBlank()) put("category", reviewCategory)
                        put("activity_id", activityId)
                    }.toString())
                    writableDatabase.insertWithOnConflict("proof_reviews", null, cvReview, SQLiteDatabase.CONFLICT_REPLACE)
                }
                invalidateApprovedProofCache(evId)
            }
            if (pts > 0) {
                val isWakeUp = activityId.contains("WAKEUP") || result.optString("category").uppercase(Locale.US) in listOf("WAKE_UP", "WAKEUP")
                val alreadyAwardedWake = isWakeUp && hasWakeUpBonusBeenAwarded(nowDate())
                if (!alreadyAwardedWake) {
                    val hasEvent = readableDatabase.rawQuery(
                        "SELECT 1 FROM score_events WHERE description LIKE ? AND event_type = 'PROOF_APPROVED'",
                        arrayOf("%$activityId%")
                    ).use { it.moveToFirst() }
                    if (!hasEvent) {
                    addScoreEvent(
                        date = nowDate(),
                        isDevMode = false,
                        eventType = "PROOF_APPROVED",
                        description = "StrictBot Review: Proof Approved (+$pts pts) — $activityId — $reason",
                        points = pts,
                        audit = JSONObject().apply {
                            put("activity_id", activityId)
                            put("evidence_id", evId)
                            put("action", result.optString("action", "APPROVE"))
                            put("admin_note", reason)
                        }
                    )
                }
            }
        }
    } else if (isRejected) {
            val evId = result.optString("evidence_id")
            val reason = result.optString("reason_note", result.optString("error", "Proof rejected"))
            if (evId.isNotBlank()) {
                val cvReview = ContentValues().apply {
                    put("status", "REJECTED")
                    put("decision", "REJECTED")
                    put("points_awarded", 0)
                    put("reviewed_by", result.optString("reviewed_by", "StrictBot / Admin"))
                    put("reviewed_at", nowIso())
                    put("reason_note", reason)
                    put("reason_category", result.optString("reason_category", "REJECTED"))
                }
                val updated = writableDatabase.update("proof_reviews", cvReview, "evidence_id = ?", arrayOf(evId))
                if (updated == 0) {
                    val reviewCategory = result.optString("category", details.optString("activity_category", details.optString("category", "")))
                    cvReview.put("evidence_id", evId)
                    cvReview.put("date", nowDate())
                    cvReview.put("task_code", reviewCategory.ifBlank { result.optString("category", "HOURLY") })
                    cvReview.put("task_title", result.optString("task_title", "Hourly Checkpoint: ${reviewCategory.ifBlank { "Activity" }}"))
                    cvReview.put("file_path", "")
                    cvReview.put("submitted_at", submittedAt)
                    cvReview.put("activity_id", activityId)
                    if (hasProofReviewsCategoryColumn() && reviewCategory.isNotBlank()) cvReview.put("category", reviewCategory)
                    cvReview.put("audit_json", JSONObject().apply {
                        if (reviewCategory.isNotBlank()) put("category", reviewCategory)
                        put("activity_id", activityId)
                    }.toString())
                    writableDatabase.insertWithOnConflict("proof_reviews", null, cvReview, SQLiteDatabase.CONFLICT_REPLACE)
                }
            }
        }
    }

    fun getSyncEventState(eventId: String): Pair<String, String> {
        readableDatabase.rawQuery(
            "SELECT sync_status, sync_error FROM sync_events_queue WHERE event_id = ?",
            arrayOf(eventId)
        ).use { c ->
            if (c.moveToFirst()) return Pair(c.getString(0) ?: "PENDING", c.getString(1) ?: "")
        }
        return Pair("UNKNOWN", "Submission queue item was not found.")
    }

    private fun pollProofSubmissionEvents(context: Context, endpoint: String, deviceId: String, token: String): Boolean {
        val ackIds = JSONArray()
        try {
            val encodedDevice = URLEncoder.encode(deviceId, "UTF-8")
            val encodedToken = URLEncoder.encode(token, "UTF-8")
            val getConn = (URL("$endpoint/api/device/submission_events?device_id=$encodedDevice&device_token=$encodedToken").openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 5000
                readTimeout = 10000
            }
            if (getConn.responseCode !in 200..299) return false
            val response = getConn.inputStream.bufferedReader().use { it.readText() }
            val events = JSONObject(response).optJSONArray("events") ?: JSONArray()
            for (index in 0 until events.length()) {
                val event = events.optJSONObject(index) ?: continue
                val eventId = event.optString("event_id")
                val activityId = event.optString("activity_id")
                if (eventId.isBlank() || activityId.isBlank()) continue
                val cursor = readableDatabase.rawQuery(
                    "SELECT 1 FROM proof_submission_receipts WHERE event_id = ?", arrayOf(eventId)
                )
                val alreadyReceived = try { cursor.moveToFirst() } finally { cursor.close() }
                if (alreadyReceived) {
                    ackIds.put(eventId)
                    continue
                }

                val eventType = event.optString("event_type")
                if (eventType in listOf("ADMIN_REVIEW_COMPLETED", "PROOF_APPROVED", "POINTS_ADJUSTED")) {
                    applyHourlySyncResult(activityId, JSONObject(event.toString()).put("accepted", true))
                    val action = event.optString("action", "APPROVE")
                    val pts = event.optInt("points", 0)
                    val reason = event.optString("reason_note", "Admin decision")
                    val isApprove = action in listOf("APPROVE", "OVERRIDE") || event.optString("status") == "APPROVED"
                    val title = if (isApprove) "StrictBot: Proof Approved! (+$pts pts) ✓" else "StrictBot: Proof Rejected ✕"
                    val msg = if (isApprove) "Checkpoint verified: $reason" else "Verification rejected: $reason"
                    val notifId = (eventId.hashCode() and 0x7fffffff).coerceAtLeast(1)
                    WakeUpAlarmReceiver.postDisciplineNotification(
                        context,
                        notifId,
                        title,
                        msg,
                        "PROOF_REVIEW_RESULT",
                        activityId,
                        null
                    )
                    writableDatabase.execSQL(
                        "INSERT OR IGNORE INTO proof_submission_receipts (event_id, received_at) VALUES (?, ?)",
                        arrayOf(eventId, nowIso())
                    )
                    ackIds.put(eventId)
                    continue
                }

                applyHourlySyncResult(activityId, JSONObject(event.toString()).put("accepted", true))
                val category = event.optString("category", "Activity")
                val source = event.optString("source_device_name", if (event.optString("source_platform") == "WINDOWS") "Windows" else "Android")
                val message = "$category • Description saved • Evidence received • AI review pending"
                val notifId = (eventId.hashCode() and 0x7fffffff).coerceAtLeast(1)
                var hourSlot: String? = null
                readableDatabase.rawQuery("SELECT details_json FROM scheduled_activities WHERE activity_id = ?", arrayOf(activityId)).use { c ->
                    if (c.moveToFirst()) {
                        hourSlot = try { JSONObject(c.getString(0) ?: "{}").optString("hour_slot").takeIf { it.isNotBlank() } } catch (_: Exception) { null }
                    }
                }
                WakeUpAlarmReceiver.postDisciplineNotification(
                    context,
                    notifId,
                    "Hourly proof submitted from $source ✓",
                    message,
                    "HOURLY_SUBMISSION",
                    activityId,
                    hourSlot
                )
                writableDatabase.execSQL(
                    "INSERT OR IGNORE INTO proof_submission_receipts (event_id, received_at) VALUES (?, ?)",
                    arrayOf(eventId, nowIso())
                )
                ackIds.put(eventId)
            }

            if (ackIds.length() > 0) {
                val ackConn = (URL("$endpoint/api/device/submission_events/ack").openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 5000
                    readTimeout = 10000
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                }
                val ack = JSONObject().apply {
                    put("device_id", deviceId)
                    put("device_token", token)
                    put("event_ids", ackIds)
                }
                OutputStreamWriter(ackConn.outputStream).use { it.write(ack.toString()) }
                if (ackConn.responseCode !in 200..299) return false
            }
            return true
        } catch (_: Exception) {
            return false
        }
    }

    fun saveDevicePairing(
        deviceName: String = "Windows PC",
        method: String = "USB",
        trustStatus: String = "PAIRED",
        ip: String = "127.0.0.1"
    ) {
        val cv = ContentValues().apply {
            put("pairing_id", "pair_win_" + UUID.randomUUID().toString().take(8))
            put("device_name", deviceName)
            put("platform", "WINDOWS")
            put("paired_at", nowIso())
            put("pairing_method", method)
            put("trust_status", trustStatus)
            put("last_sync_at", nowTime())
            put("last_ip", ip)
        }
        writableDatabase.insertWithOnConflict("device_pairing", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
        setConfigValue("paired_pc_name", deviceName)
        setConfigValue("pairing_status", trustStatus)
        setConfigValue("last_sync_time", nowTime())
        logStructured(nowDate(), false, "DEVICE_PAIRED", "$deviceName via $method")
    }

    fun getDevicePairingStatus(): JSONObject {
        val reg = getDeviceRegistration()
        if (reg != null) {
            return JSONObject().apply {
                put("device_name", reg.optString("paired_pc_name", "Windows PC"))
                put("platform", "WINDOWS")
                put("paired_at", reg.optString("registered_at", ""))
                put("pairing_method", "WIFI_PAIRING")
                put("trust_status", "REGISTERED")
                put("last_sync_at", getConfigValue("last_sync_time", "Just now"))
                put("server_endpoint", reg.optString("server_endpoint", ""))
                put("account_id", reg.optString("account_id", ""))
            }
        }
        val obj = JSONObject()
        try {
            readableDatabase.rawQuery("SELECT * FROM device_pairing ORDER BY paired_at DESC LIMIT 1", null).use { c ->
                if (c.moveToFirst()) {
                    obj.put("device_name", c.getString(c.getColumnIndexOrThrow("device_name")))
                    obj.put("platform", c.getString(c.getColumnIndexOrThrow("platform")))
                    obj.put("paired_at", c.getString(c.getColumnIndexOrThrow("paired_at")))
                    obj.put("pairing_method", c.getString(c.getColumnIndexOrThrow("pairing_method")))
                    obj.put("trust_status", c.getString(c.getColumnIndexOrThrow("trust_status")))
                    obj.put("last_sync_at", c.getString(c.getColumnIndexOrThrow("last_sync_at")) ?: nowTime())
                    return obj
                }
            }
        } catch (_: Exception) {}
        val pairedName = getConfigValue("paired_pc_name", "")
        if (pairedName.isNotEmpty()) {
            obj.put("device_name", pairedName)
            obj.put("platform", "WINDOWS")
            obj.put("paired_at", getConfigValue("paired_at", ""))
            obj.put("pairing_method", getConfigValue("pairing_method", "USB"))
            obj.put("trust_status", getConfigValue("pairing_status", "PAIRED"))
            obj.put("last_sync_at", getConfigValue("last_sync_time", "Never"))
        } else {
            obj.put("device_name", "No PC Paired")
            obj.put("platform", "NONE")
            obj.put("paired_at", "")
            obj.put("pairing_method", "NONE")
            obj.put("trust_status", "NOT_REGISTERED")
            obj.put("last_sync_at", "Never")
        }
        return obj
    }

    // ════════════════════════════════════════════════════════
    // FOCUS SESSIONS & RESTRICTIONS ENGINE (Section 4 & 13)
    // ════════════════════════════════════════════════════════

    fun getActiveFocusSession(): JSONObject? {
        try {
            readableDatabase.rawQuery("SELECT * FROM focus_sessions WHERE status IN ('ACTIVE', 'PAUSED') ORDER BY started_at DESC LIMIT 1", null).use { c ->
                if (c.moveToFirst()) {
                    val obj = JSONObject()
                    val fid = c.getString(c.getColumnIndexOrThrow("focus_id"))
                    obj.put("focus_id", fid)
                    obj.put("date", c.getString(c.getColumnIndexOrThrow("date")))
                    obj.put("title", c.getString(c.getColumnIndexOrThrow("title")))
                    obj.put("goal", c.getString(c.getColumnIndexOrThrow("goal")))
                    val targetMins = c.getInt(c.getColumnIndexOrThrow("target_minutes"))
                    obj.put("target_minutes", targetMins)
                    obj.put("started_at", c.getString(c.getColumnIndexOrThrow("started_at")))
                    val plannedEnd = c.getString(c.getColumnIndexOrThrow("planned_end_time"))
                    obj.put("planned_end_time", plannedEnd)
                    val status = c.getString(c.getColumnIndexOrThrow("status"))
                    obj.put("status", status)
                    obj.put("restrictions", JSONObject(c.getString(c.getColumnIndexOrThrow("restrictions_json")) ?: "{}"))
                    obj.put("points_awarded", c.getInt(c.getColumnIndexOrThrow("points_awarded")))

                    val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
                    val startDt = sdf.parse(c.getString(c.getColumnIndexOrThrow("started_at"))) ?: Date()
                    val elapsedSec = ((Date().time - startDt.time) / 1000).toInt().coerceAtLeast(0)
                    val remSec = (targetMins * 60 - elapsedSec).coerceAtLeast(0)
                    obj.put("remaining_seconds", remSec)
                    return obj
                }
            }
        } catch (_: Exception) {}
        return null
    }

    fun startFocusSession(title: String, goal: String, minutes: Int, restrictions: JSONObject, isDevMode: Boolean): JSONObject {
        return JSONObject().apply {
            put("success", false)
            put("error", "Focus feature has been deactivated.")
        }
    }

    fun pauseFocusSession(focusId: String) {
        writableDatabase.execSQL("UPDATE focus_sessions SET status = 'PAUSED' WHERE focus_id = ?", arrayOf<Any>(focusId))
        enqueueSyncEvent("FOCUS_SESSION_PAUSE", JSONObject().put("focus_id", focusId))
    }

    fun resumeFocusSession(focusId: String) {
        writableDatabase.execSQL("UPDATE focus_sessions SET status = 'ACTIVE' WHERE focus_id = ?", arrayOf<Any>(focusId))
        enqueueSyncEvent("FOCUS_SESSION_RESUME", JSONObject().put("focus_id", focusId))
    }

    fun interruptFocusSession(focusId: String, reason: String) {
        val ts = nowIso()
        writableDatabase.execSQL("UPDATE focus_sessions SET status = 'INTERRUPTED', ended_at = ? WHERE focus_id = ?", arrayOf<Any>(ts, focusId))
        enqueueSyncEvent("FOCUS_SESSION_INTERRUPT", JSONObject().put("focus_id", focusId).put("reason", reason))
    }

    fun finishFocusSession(focusId: String, isDevMode: Boolean): Int {
        val date = nowDate()
        ensureDay(date, isDevMode)
        var targetMins = 25
        try {
            readableDatabase.rawQuery("SELECT * FROM focus_sessions WHERE focus_id = ?", arrayOf(focusId)).use { c ->
                if (c.moveToFirst()) {
                    targetMins = c.getInt(c.getColumnIndexOrThrow("target_minutes"))
                }
            }
        } catch (_: Exception) {}
        val isDeep = targetMins >= 60
        val pts = 0
        val ts = nowIso()
        writableDatabase.execSQL(
            "UPDATE focus_sessions SET status = 'COMPLETED', actual_minutes = ?, ended_at = ?, points_awarded = ? WHERE focus_id = ?",
            arrayOf<Any>(targetMins.toDouble(), ts, pts, focusId)
        )
        completeFocusSession(date, isDevMode, targetMins, isDeep)
        enqueueSyncEvent("FOCUS_SESSION_COMPLETE", JSONObject().put("focus_id", focusId).put("points_awarded", pts).put("actual_minutes", targetMins))
        return pts
    }

    fun getFocusHistory(limit: Int = 15): List<JSONObject> {
        val list = mutableListOf<JSONObject>()
        try {
            readableDatabase.rawQuery("SELECT * FROM focus_sessions ORDER BY started_at DESC LIMIT ?", arrayOf(limit.toString())).use { c ->
                while (c.moveToNext()) {
                    val o = JSONObject()
                    o.put("focus_id", c.getString(c.getColumnIndexOrThrow("focus_id")))
                    o.put("date", c.getString(c.getColumnIndexOrThrow("date")))
                    o.put("title", c.getString(c.getColumnIndexOrThrow("title")))
                    o.put("goal", c.getString(c.getColumnIndexOrThrow("goal")))
                    o.put("target_minutes", c.getInt(c.getColumnIndexOrThrow("target_minutes")))
                    o.put("actual_minutes", c.getDouble(c.getColumnIndexOrThrow("actual_minutes")))
                    o.put("started_at", c.getString(c.getColumnIndexOrThrow("started_at")))
                    o.put("status", c.getString(c.getColumnIndexOrThrow("status")))
                    o.put("points_awarded", c.getInt(c.getColumnIndexOrThrow("points_awarded")))
                    list.add(o)
                }
            }
        } catch (_: Exception) {}
        return list
    }

    fun getFocusSettings(): JSONObject {
        val obj = JSONObject()
        obj.put("strict_mode", getConfigValue("focus_strict_mode", "1") == "1")
        obj.put("youtube_study_mode", getConfigValue("focus_yt_study", "1") == "1")
        obj.put("block_notifications", getConfigValue("focus_block_notif", "1") == "1")
        obj.put("block_home", getConfigValue("focus_block_home", "0") == "1")
        obj.put("block_split_screen", getConfigValue("focus_block_split", "0") == "1")
        obj.put("uninstall_protection", getConfigValue("focus_uninstall_protect", "0") == "1")
        obj.put("focus_field", getConfigValue("focus_field", "PROGRAMMING"))
        obj.put("study_method", getConfigValue("focus_study_method", "YOUTUBE_SELF"))
        obj.put("top_distraction", getConfigValue("focus_top_distraction", "YOUTUBE_SHORTS"))
        return obj
    }

    fun saveFocusSettings(s: JSONObject) {
        if (s.has("strict_mode")) setConfigValue("focus_strict_mode", if (s.optBoolean("strict_mode", true)) "1" else "0")
        if (s.has("youtube_study_mode")) setConfigValue("focus_yt_study", if (s.optBoolean("youtube_study_mode", true)) "1" else "0")
        if (s.has("block_notifications")) setConfigValue("focus_block_notif", if (s.optBoolean("block_notifications", true)) "1" else "0")
        if (s.has("block_home")) setConfigValue("focus_block_home", if (s.optBoolean("block_home", false)) "1" else "0")
        if (s.has("block_split_screen")) setConfigValue("focus_block_split", if (s.optBoolean("block_split_screen", false)) "1" else "0")
        if (s.has("uninstall_protection")) setConfigValue("focus_uninstall_protect", if (s.optBoolean("uninstall_protection", false)) "1" else "0")
        if (s.has("focus_field")) setConfigValue("focus_field", s.optString("focus_field"))
        if (s.has("study_method")) setConfigValue("focus_study_method", s.optString("study_method"))
        if (s.has("top_distraction")) setConfigValue("focus_top_distraction", s.optString("top_distraction"))
        enqueueSyncEvent("FOCUS_SETTINGS_UPDATE", s)
    }

    // ════════════════════════════════════════════════════════
    // NEXT-DAY ROUTINE SCHEDULER & CROSS-DEVICE SYNC (SECTIONS 1-18)
    // ════════════════════════════════════════════════════════

    fun getNextCalendarDate(todayStr: String = nowDate()): String {
        return try {
            val istTz = TimeZone.getTimeZone("Asia/Kolkata")
            val sdf = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { timeZone = istTz }
            val dt = sdf.parse(todayStr) ?: Date()
            val cal = Calendar.getInstance(istTz).apply {
                time = dt
                add(Calendar.DAY_OF_YEAR, 1)
            }
            sdf.format(cal.time)
        } catch (_: Exception) {
            todayStr
        }
    }

    fun calculateWindowDisplay(hhmm: String, wakeupDeadline: Boolean = false, isGym: Boolean = false, durationMinutes: Int = GymWindowRules.DEFAULT_GYM_DURATION_MINUTES): String {
        return try {
            if (wakeupDeadline) {
                return "7:00 AM–9:30 AM"
            }
            if (isGym) {
                return GymWindowRules.calculateGymWindow(nowDate(), hhmm, durationMinutes).windowDisplay
            }
            val parts = hhmm.trim().split(":")
            val total = parts[0].toInt() * 60 + parts[1].toInt()
            val startM = (total - 10 + 1440) % 1440
            val endM = (total + 10 + 1440) % 1440
            val sStr = String.format(Locale.US, "%02d:%02d", startM / 60, startM % 60)
            val eStr = String.format(Locale.US, "%02d:%02d", endM / 60, endM % 60)
            "${DisciplineScoringRules.formatTime12h(sStr)}–${DisciplineScoringRules.formatTime12h(eStr)}"
        } catch (_: Exception) {
            hhmm
        }
    }

    fun getNextDayScheduleState(todayStr: String = nowDate()): JSONObject {
        val nextDate = getNextCalendarDate(todayStr)
        val obj = JSONObject().apply {
            put("schedule_date", nextDate)
            put("status", "DRAFT")
            put("is_locked", false)
            put("schedule_version", 1)
            put("wake_up_time", "07:00")
            put("bath_time", "07:30")
            put("gym_time", "18:00")
            put("gym_duration_minutes", 90)
            put("gym_enabled", true)
            put("gym_required_by_admin", getConfigValue("gym_required_by_admin", "0") == "1")
            put("custom_activities", JSONArray())
            put("reminder_needed", false)
        }
        try {
            readableDatabase.rawQuery(
                "SELECT * FROM routine_schedules WHERE schedule_date = ? ORDER BY (CASE WHEN (is_locked = 1 OR status IN ('SCHEDULED', 'CONFIRMED', 'SUBMITTED_LATE')) AND confirmed_at IS NOT NULL AND confirmed_at != '' THEN 1 ELSE 0 END) DESC, schedule_version DESC LIMIT 1",
                arrayOf(nextDate)
            ).use { c ->
                if (c.moveToFirst()) {
                    val status = c.getString(c.getColumnIndexOrThrow("status"))
                    obj.put("schedule_id", c.getString(c.getColumnIndexOrThrow("schedule_id")))
                    obj.put("schedule_version", c.getInt(c.getColumnIndexOrThrow("schedule_version")))
                    obj.put("status", status)
                    obj.put("is_locked", c.getInt(c.getColumnIndexOrThrow("is_locked")) == 1 || status == "SCHEDULED")
                    obj.put("wake_up_time", c.getString(c.getColumnIndexOrThrow("wake_up_time")))
                    obj.put("bath_time", c.getString(c.getColumnIndexOrThrow("bath_time")))
                    obj.put("gym_time", c.getString(c.getColumnIndexOrThrow("gym_time")))
                    val durCol = c.getColumnIndex("gym_duration_minutes")
                    val dur = if (durCol >= 0 && !c.isNull(durCol)) c.getInt(durCol) else 90
                    obj.put("gym_duration_minutes", if (dur > 0) dur else 90)
                    obj.put("gym_enabled", c.getInt(c.getColumnIndexOrThrow("gym_enabled")) == 1)
                    obj.put("gym_required_by_admin", c.getInt(c.getColumnIndexOrThrow("gym_required_by_admin")) == 1)
                    obj.put("custom_activities", JSONArray(c.getString(c.getColumnIndexOrThrow("custom_activities_json")) ?: "[]"))
                    obj.put("confirmed_at", c.getString(c.getColumnIndexOrThrow("confirmed_at")) ?: "")
                    obj.put("reminder_needed", c.getInt(c.getColumnIndexOrThrow("reminder_needed")) == 1)
                }
            }
        } catch (_: Exception) {}
        obj.put("wake_up_window", calculateWindowDisplay(obj.optString("wake_up_time", "07:00"), wakeupDeadline = true))
        obj.put("bath_window", calculateWindowDisplay(obj.optString("bath_time", "07:30")))
        val gymDur = obj.optInt("gym_duration_minutes", 90)
        obj.put("gym_window", GymWindowRules.calculateGymWindow(nextDate, obj.optString("gym_time", "18:00"), gymDur).windowDisplay)
        return obj
    }

    fun saveNextDayDraft(
        wakeUpTime: String,
        bathTime: String,
        gymTime: String,
        gymEnabled: Boolean,
        customActivities: JSONArray = JSONArray(),
        dismissed: Boolean = false,
        scheduleDate: String = getNextCalendarDate(),
        gymDurationMinutes: Int = 90
    ): JSONObject {
        val current = getNextDayScheduleState()
        if (current.optBoolean("is_locked", false) && current.optString("schedule_date") == scheduleDate) {
            return JSONObject().put("success", false).put("error", "Schedule is locked and cannot be edited without admin authorization.")
        }
        val (wOk, normWake) = DisciplineScoringRules.validateWakeUpTime(wakeUpTime)
        if (!wOk) {
            return JSONObject().put("success", false).put("error", DisciplineScoringRules.WAKEUP_ERROR_MSG)
        }
        val ver = current.optInt("schedule_version", 1)
        val schedId = "SCHED-${scheduleDate.replace("-", "")}-V$ver"
        val cv = ContentValues().apply {
            put("schedule_id", schedId)
            put("account_id", "user_lok")
            put("schedule_version", ver)
            put("schedule_date", scheduleDate)
            put("timezone", "Asia/Kolkata")
            put("status", "DRAFT")
            put("wake_up_time", normWake)
            put("bath_time", bathTime.trim())
            put("gym_time", gymTime.trim())
            put("gym_duration_minutes", if (gymDurationMinutes > 0) gymDurationMinutes else 90)
            put("gym_enabled", if (gymEnabled) 1 else 0)
            put("gym_required_by_admin", if (getConfigValue("gym_required_by_admin", "0") == "1") 1 else 0)
            put("custom_activities_json", customActivities.toString())
            put("is_locked", 0)
            put("reminder_needed", if (dismissed) 1 else 0)
            put("updated_at", nowIso())
        }
        writableDatabase.insertWithOnConflict("routine_schedules", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
        return JSONObject().apply {
            put("success", true)
            put("status", "DRAFT")
            put("reminder_needed", dismissed)
            put("schedule", getNextDayScheduleState())
        }
    }

    fun unlockNextDaySchedule(scheduleDate: String = getNextCalendarDate()): Boolean {
        return try {
            val cv = ContentValues().apply {
                put("is_locked", 0)
                put("status", "DRAFT")
            }
            writableDatabase.update("routine_schedules", cv, "schedule_date = ?", arrayOf(scheduleDate)) > 0
        } catch (_: Exception) { false }
    }

    fun confirmNextDaySchedule(
        wakeUpTime: String,
        bathTime: String,
        gymTime: String,
        gymEnabled: Boolean,
        customActivities: JSONArray = JSONArray(),
        scheduleDate: String = getNextCalendarDate(),
        gymDurationMinutes: Int = 90,
        allowOverwrite: Boolean = false
    ): JSONObject {
        val current = getScheduleForDate(scheduleDate) ?: getNextDayScheduleState()
        if (!allowOverwrite && current.optBoolean("is_locked", false) && current.optString("schedule_date") == scheduleDate) {
            return JSONObject().put("success", false).put("error", "Schedule is already confirmed and locked.")
        }
        val (wOk, normWake) = DisciplineScoringRules.validateWakeUpTime(wakeUpTime)
        if (!wOk) {
            return JSONObject().put("success", false).put("error", DisciplineScoringRules.WAKEUP_ERROR_MSG)
        }
        if (getConfigValue("gym_required_by_admin", "0") == "1" && !gymEnabled) {
            return JSONObject().put("success", false).put("error", "Gym task is required by administrator policy and cannot be disabled.")
        }

        var maxVer = 0
        try {
            readableDatabase.rawQuery(
                "SELECT MAX(schedule_version) FROM routine_schedules WHERE schedule_date = ?",
                arrayOf(scheduleDate)
            ).use { c ->
                if (c.moveToFirst() && !c.isNull(0)) {
                    maxVer = c.getInt(0)
                }
            }
        } catch (_: Exception) {}

        val ver = maxOf(maxVer + 1, current.optInt("schedule_version", 0) + 1, 1)
        val schedId = "SCHED-${scheduleDate.replace("-", "")}-V$ver"
        val nowTs = nowIso()
        val nowMs = authoritativeNowMs()
        val isLate = scheduleDate == nowDate() && ScheduleDeadlineRules.isPastDeadline(nowMs, scheduleDate, scheduleTimezone(scheduleDate))
        val schedStatus = if (isLate) "SUBMITTED_LATE" else "SCHEDULED"

        try {
            writableDatabase.delete(
                "routine_schedules",
                "schedule_date = ? AND (is_locked = 0 OR status IN ('DRAFT', 'PENDING', 'UNCONFIRMED') OR schedule_version < ?)",
                arrayOf(scheduleDate, ver.toString())
            )
        } catch (_: Exception) {}

        val cv = ContentValues().apply {
            put("schedule_id", schedId)
            put("account_id", "user_lok")
            put("schedule_version", ver)
            put("schedule_date", scheduleDate)
            put("timezone", "Asia/Kolkata")
            put("status", schedStatus)
            put("wake_up_time", normWake)
            put("bath_time", bathTime.trim())
            put("gym_time", gymTime.trim())
            put("gym_duration_minutes", if (gymDurationMinutes > 0) gymDurationMinutes else 90)
            put("gym_enabled", if (gymEnabled) 1 else 0)
            put("gym_required_by_admin", if (getConfigValue("gym_required_by_admin", "0") == "1") 1 else 0)
            put("custom_activities_json", customActivities.toString())
            put("is_locked", 1)
            put("confirmed_at", nowTs)
            put("confirmed_by_device", "android_lok_phone")
            put("reminder_needed", 0)
            put("updated_at", nowTs)
        }
        writableDatabase.insertWithOnConflict("routine_schedules", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
        ensureGymOccurrence(scheduleDate)
        reconcileHourlyGymConflicts(scheduleDate)
        val state = getScheduleForDate(scheduleDate) ?: getNextDayScheduleState()
        enqueueSyncEvent("ROUTINE_SCHEDULE_CONFIRM", state)
        return JSONObject().apply {
            put("success", true)
            put("status", schedStatus)
            put("is_locked", true)
            put("is_submitted_late", isLate)
            put("strictbot_message", if (isLate) "Today's schedule confirmed (Submitted Late after 10:00 AM)." else "Wake-up target is ${DisciplineScoringRules.formatTime12h(normWake).removePrefix("0")}. Your other activities are scheduled.")
            put("schedule", state)
        }
    }

    fun syncRoutineScheduleWithServer(scheduleDate: String = getNextCalendarDate()): JSONObject {
        val reg = getDeviceRegistration() ?: return JSONObject().put("synced", false)
        val endpoint = reg.optString("server_endpoint", "").trimEnd('/')
        if (endpoint.isEmpty()) return JSONObject().put("synced", false)
        return try {
            val localSched = getScheduleForDate(scheduleDate) ?: getNextDayScheduleState()
            val url = URL("$endpoint/api/schedule/sync")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 3000
                readTimeout = 3000
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
            }
            val req = JSONObject().apply {
                put("device_id", reg.optString("device_id", "android_lok_phone"))
                put("device_token", reg.optString("device_token", ""))
                put("account_id", reg.optString("account_id", "user_lok"))
                put("schedule_date", scheduleDate)
                val isConfirmed = localSched.optBoolean("is_locked", false) || localSched.optString("status") in listOf("SCHEDULED", "CONFIRMED", "SUBMITTED_LATE")
                if (localSched.optString("schedule_date") == scheduleDate && isConfirmed) {
                    put("incoming_schedule", localSched)
                }
            }
            OutputStreamWriter(conn.outputStream).use { it.write(req.toString()) }
            if (conn.responseCode in 200..299) {
                val respText = conn.inputStream.bufferedReader().use { it.readText() }
                val j = JSONObject(respText)
                j.optJSONObject("daily_routine_gate")?.let { applyAuthoritativeRoutineGate(it) }
                val serverTime = j.optString("server_time")
                if (serverTime.isNotBlank()) syncServerTime(parseIsoToMs(serverTime))
                mergeAuthoritativeScheduleActivities(scheduleDate, j.optJSONArray("activities") ?: JSONArray())
                val authSched = j.optJSONObject("schedule")
                if (authSched != null && authSched.optString("status") == "SCHEDULED") {
                    val cv = ContentValues().apply {
                        put("schedule_id", authSched.optString("schedule_id"))
                        put("account_id", authSched.optString("account_id", "user_lok"))
                        put("schedule_version", authSched.optInt("schedule_version", 1))
                        put("schedule_date", authSched.optString("schedule_date", scheduleDate))
                        put("timezone", "Asia/Kolkata")
                        put("status", "SCHEDULED")
                        put("wake_up_time", authSched.optJSONObject("wake_up")?.optString("target_time") ?: "07:00")
                        put("bath_time", authSched.optJSONObject("bath")?.optString("target_time") ?: "07:30")
                        put("gym_time", authSched.optJSONObject("gym")?.optString("target_time") ?: "18:30")
                        put("gym_enabled", if (authSched.optJSONObject("gym")?.optBoolean("enabled", true) != false) 1 else 0)
                        put("is_locked", 1)
                        put("confirmed_at", authSched.optString("confirmed_at", nowIso()))
                        put("confirmed_by_device", authSched.optString("confirmed_by_device", "windows_lok_pc"))
                        put("reminder_needed", 0)
                        put("updated_at", nowIso())
                    }
                    writableDatabase.insertWithOnConflict("routine_schedules", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
                }
                j
            } else {
                JSONObject().put("synced", false)
            }
        } catch (_: Exception) {
            JSONObject().put("synced", false)
        }
    }

    fun isScheduleConfirmedForDate(date: String = nowDate()): Boolean {
        var routineConfirmed = false
        try {
            readableDatabase.rawQuery(
                "SELECT status, is_locked, confirmed_at FROM routine_schedules WHERE schedule_date = ? ORDER BY (CASE WHEN (is_locked = 1 OR status IN ('SCHEDULED', 'CONFIRMED', 'SUBMITTED_LATE')) AND confirmed_at IS NOT NULL AND confirmed_at != '' THEN 1 ELSE 0 END) DESC, schedule_version DESC LIMIT 1",
                arrayOf(date)
            ).use { c ->
                if (c.moveToFirst()) {
                    val status = c.getString(c.getColumnIndexOrThrow("status"))
                    val isLocked = c.getInt(c.getColumnIndexOrThrow("is_locked")) == 1
                    val confirmedAt = c.getString(c.getColumnIndexOrThrow("confirmed_at"))
                    routineConfirmed = (isLocked || status in listOf("SCHEDULED", "CONFIRMED", "SUBMITTED_LATE")) && !confirmedAt.isNullOrBlank()
                }
            }
        } catch (_: Exception) {}

        val bathConfirmed = isBathScheduleConfirmed(date)
        val collegeRecorded = getCollegeAttendance(date).isNotBlank() || getDailyCollegeExamPlan(date) != null

        return routineConfirmed && bathConfirmed && collegeRecorded
    }

    fun getScheduleForDate(date: String = nowDate()): JSONObject? {
        try {
            readableDatabase.rawQuery(
                "SELECT * FROM routine_schedules WHERE schedule_date = ? ORDER BY (CASE WHEN (is_locked = 1 OR status IN ('SCHEDULED', 'CONFIRMED', 'SUBMITTED_LATE')) AND confirmed_at IS NOT NULL AND confirmed_at != '' THEN 1 ELSE 0 END) DESC, schedule_version DESC LIMIT 1",
                arrayOf(date)
            ).use { c ->
                if (c.moveToFirst()) {
                    val status = c.getString(c.getColumnIndexOrThrow("status"))
                    val obj = JSONObject()
                    obj.put("schedule_id", c.getString(c.getColumnIndexOrThrow("schedule_id")))
                    obj.put("schedule_version", c.getInt(c.getColumnIndexOrThrow("schedule_version")))
                    obj.put("schedule_date", date)
                    obj.put("status", status)
                    obj.put("is_locked", c.getInt(c.getColumnIndexOrThrow("is_locked")) == 1 || status == "SCHEDULED" || status == "CONFIRMED")
                    obj.put("wake_up_time", c.getString(c.getColumnIndexOrThrow("wake_up_time")))
                    obj.put("bath_time", c.getString(c.getColumnIndexOrThrow("bath_time")))
                    obj.put("gym_time", c.getString(c.getColumnIndexOrThrow("gym_time")))
                    val durCol = c.getColumnIndex("gym_duration_minutes")
                    val dur = if (durCol >= 0 && !c.isNull(durCol)) c.getInt(durCol) else 90
                    obj.put("gym_duration_minutes", if (dur > 0) dur else 90)
                    obj.put("gym_enabled", c.getInt(c.getColumnIndexOrThrow("gym_enabled")) == 1)
                    obj.put("gym_required_by_admin", c.getInt(c.getColumnIndexOrThrow("gym_required_by_admin")) == 1)
                    obj.put("custom_activities", JSONArray(c.getString(c.getColumnIndexOrThrow("custom_activities_json")) ?: "[]"))
                    obj.put("confirmed_at", c.getString(c.getColumnIndexOrThrow("confirmed_at")) ?: "")
                    obj.put("reminder_needed", c.getInt(c.getColumnIndexOrThrow("reminder_needed")) == 1)
                    obj.put("wake_up_window", calculateWindowDisplay(obj.optString("wake_up_time", "07:00"), wakeupDeadline = true))
                    obj.put("bath_window", calculateWindowDisplay(obj.optString("bath_time", "07:30")))
                    val gymDur = obj.optInt("gym_duration_minutes", 90)
                    obj.put("gym_window", GymWindowRules.calculateGymWindow(date, obj.optString("gym_time", "18:00"), gymDur).windowDisplay)
                    return obj
                }
            }
        } catch (_: Exception) {}
        return null
    }

    fun saveScheduleDraftForDate(
        date: String = nowDate(),
        wakeUpTime: String,
        bathTime: String,
        gymTime: String,
        gymEnabled: Boolean,
        collegeDecision: String? = null,
        collegeStart: String? = null,
        collegeEnd: String? = null,
        hasExam: Boolean = false,
        examStart: String? = null,
        examEnd: String? = null,
        bathSessionCount: Int = 1,
        bathTimes: List<String> = emptyList(),
        gymDurationMinutes: Int = 90
    ): JSONObject {
        val draftJson = JSONObject().apply {
            put("schedule_date", date)
            put("wake_up_time", wakeUpTime)
            put("bath_time", bathTime)
            put("gym_time", gymTime)
            put("gym_enabled", gymEnabled)
            put("gym_duration_minutes", gymDurationMinutes)
            put("college_decision", collegeDecision ?: "")
            put("college_start", collegeStart ?: "")
            put("college_end", collegeEnd ?: "")
            put("has_exam", hasExam)
            put("exam_start", examStart ?: "")
            put("exam_end", examEnd ?: "")
            put("bath_session_count", bathSessionCount)
            put("bath_times", JSONArray(bathTimes))
            put("status", "DRAFT")
            put("is_locked", false)
            put("updated_at", nowIso())
        }
        setConfigValue("schedule_draft_$date", draftJson.toString())
        return saveNextDayDraft(
            wakeUpTime = wakeUpTime,
            bathTime = bathTime,
            gymTime = gymTime,
            gymEnabled = gymEnabled,
            dismissed = true,
            scheduleDate = date,
            gymDurationMinutes = gymDurationMinutes
        )
    }

    fun getScheduleDraftForDate(date: String = nowDate()): JSONObject? {
        val raw = getConfigValue("schedule_draft_$date", "")
        if (raw.isNotBlank()) {
            return runCatching { JSONObject(raw) }.getOrNull()
        }
        val sched = getScheduleForDate(date)
        if (sched != null && !sched.optBoolean("is_locked", false)) {
            return sched
        }
        return null
    }

    fun confirmScheduleForDate(
        date: String = nowDate(),
        collegeDecision: String,
        collegeStart: String? = null,
        collegeEnd: String? = null,
        hasExam: Boolean = false,
        examStart: String? = null,
        examEnd: String? = null,
        bathSessionCount: Int = 1,
        bathTimes: List<String> = emptyList(),
        wakeUpTime: String = "07:00",
        gymTime: String = "18:00",
        gymDurationMinutes: Int = 90,
        gymEnabled: Boolean = true
    ): JSONObject {
        val isCollege = collegeDecision.equals("YES", ignoreCase = true)
        setCollegeAttendance(date, if (isCollege) "YES" else "NO")

        saveDailyCollegeExamPlan(
            date = date,
            attendsCollege = if (isCollege) "YES" else "NO",
            collegeStart = if (isCollege) collegeStart else null,
            collegeEnd = if (isCollege) collegeEnd else null,
            hasExam = if (isCollege && hasExam) "YES" else "NO",
            examStart = if (isCollege && hasExam) examStart else null,
            examEnd = if (isCollege && hasExam) examEnd else null
        )

        val chosenBathTimes = if (bathTimes.isNotEmpty()) {
            bathTimes.take(bathSessionCount)
        } else {
            listOf("07:30")
        }

        val bathRes = confirmDailyBathPlan(
            date = date,
            collegeDecision = if (isCollege) "YES" else "NO",
            plannedTimes = chosenBathTimes,
            allowOverwrite = true
        )
        if (!bathRes.optBoolean("success", false)) {
            return bathRes
        }

        val schedRes = confirmNextDaySchedule(
            wakeUpTime = wakeUpTime,
            bathTime = chosenBathTimes.firstOrNull() ?: "07:30",
            gymTime = gymTime,
            gymEnabled = gymEnabled,
            scheduleDate = date,
            gymDurationMinutes = gymDurationMinutes,
            allowOverwrite = true
        )
        if (!schedRes.optBoolean("success", false)) {
            return schedRes
        }

        reconcileHourlyPostExamClassSlots(date)
        ensureDailyActivities(date)

        // Clear draft on successful confirmation
        setConfigValue("schedule_draft_$date", "")

        val isConfirmed = isScheduleConfirmedForDate(date)
        if (!isConfirmed) {
            return JSONObject().put("success", false).put("error", "Authoritative verification failed: database record was not persisted.")
        }

        val schedObj = getScheduleForDate(date)
        val finalStatus = schedRes.optString("status", schedObj?.optString("status", "CONFIRMED"))
        return JSONObject().apply {
            put("success", true)
            put("status", finalStatus)
            put("is_submitted_late", schedRes.optBoolean("is_submitted_late", false))
            put("schedule", schedObj)
        }
    }

    /**
     * Completes the day-level schedule record after the Daily Routine Setup has persisted the
     * college choice and bath plan, so the existing 10:00 AM schedule-deadline gate
     * ([isScheduleConfirmedForDate]) sees one consistent confirmed routine.
     *
     * It never invents new policy: wake-up target, gym time, duration and enabled flag are taken
     * from the existing saved schedule or draft; only when none exists are the established
     * defaults used. A planned REST/PAUSED gym day is never converted into a gym day.
     */
    fun finalizeDailyRoutineSchedule(date: String = nowDate()): JSONObject {
        if (isScheduleConfirmedForDate(date)) {
            return JSONObject().put("success", true).put("status", "ALREADY_CONFIRMED")
        }
        val existing = getScheduleForDate(date) ?: getScheduleDraftForDate(date)
        val wake = existing?.optString("wake_up_time", "")?.takeIf { it.isNotBlank() } ?: "07:00"
        val gymTime = existing?.optString("gym_time", "")?.takeIf { it.isNotBlank() } ?: "18:00"
        val duration = existing?.optInt("gym_duration_minutes", 90)?.takeIf { it > 0 } ?: 90
        val plan = getDailyBathPlan(date)
        val firstBath = plan?.optJSONArray("selected_bath_times")?.optString(0)?.takeIf { it.isNotBlank() } ?: "07:30"
        val gymEnabled = if (existing != null && existing.has("gym_enabled")) {
            existing.optBoolean("gym_enabled", true)
        } else {
            val (configured, info) = StrictCalendarDataRepository.getGymRoutineConfig(this)
            val state = StrictCalendarDataRepository.calculatePlannedGymState(date, configured, info.first, info.second)
            state != "REST_DAY" && state != "PAUSED" && state != "NOT_TRACKED"
        }
        val res = confirmNextDaySchedule(
            wakeUpTime = wake,
            bathTime = firstBath,
            gymTime = gymTime,
            gymEnabled = gymEnabled,
            scheduleDate = date,
            gymDurationMinutes = duration,
            allowOverwrite = true
        )
        if (!res.optBoolean("success", false)) return res
        reconcileHourlyPostExamClassSlots(date)
        ensureDailyActivities(date)
        setConfigValue("schedule_draft_$date", "")
        return if (isScheduleConfirmedForDate(date)) {
            JSONObject().put("success", true).put("status", "CONFIRMED")
        } else {
            JSONObject().put("success", false).put("error", "Today's routine could not be fully persisted. Please try again.")
        }
    }

    private fun mergeAuthoritativeScheduleActivities(scheduleDate: String, activities: JSONArray) {
        StrictWidgetUpdates.request(appContext,"tasks")
        val terminalStates = setOf("VERIFIED", "REJECTED", "REJECTED_LATE", "MISSED", "CLOSED", "SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "UNDER_REVIEW", "EXCUSED", "CANCELLED", "SKIPPED_PRE_GATE")
        val seenIds = mutableSetOf<String>()
        val db = writableDatabase
        db.beginTransaction()
        try {
            for (index in 0 until activities.length()) {
                var item = activities.optJSONObject(index) ?: continue
                val activityId = item.optString("activity_id").trim()
                if (activityId.isEmpty()) continue
                seenIds.add(activityId)
                val activityDate = item.optString("date", scheduleDate)
                var localStatus = ""
                var localSubmittedAt = ""
                var localDetails = JSONObject()
                var localVersion = 0
                db.rawQuery(
                    "SELECT status, submitted_at, details_json, schedule_version FROM scheduled_activities WHERE activity_id = ?",
                    arrayOf(activityId)
                ).use { cursor ->
                    if (cursor.moveToFirst()) {
                        localStatus = cursor.getString(0).orEmpty()
                        localSubmittedAt = cursor.getString(1).orEmpty()
                        localDetails = runCatching { JSONObject(cursor.getString(2).orEmpty()) }.getOrDefault(JSONObject())
                        localVersion = cursor.getInt(3)
                    }
                }
                val serverVersion = item.optInt("schedule_version", 1)
                if (localVersion > serverVersion) continue
                if (item.optString("type") == "BATH") {
                    if (activityDate < nowDate() || localStatus in BathScheduleRecord.protectedStatuses || localStatus == "EXCUSED") continue
                    item = BathScheduleRecord.normalizeIncoming(item,scheduleTimezone(activityDate))
                }
                val rawServerStatus = item.optString("status", "SCHEDULED")
                val serverStatus = if (item.optString("type") == "HOURLY" && rawServerStatus in listOf("BLOCKED_WAKEUP", "SKIPPED_PRE_GATE")) "SCHEDULED" else rawServerStatus
                val preserveBathAssignment = BathScheduleRecord.preserveAssignmentOnSync(localStatus,localDetails,serverStatus)
                if (preserveBathAssignment) continue
                val preserveLocal = localSubmittedAt.isNotBlank() && localStatus in terminalStates && serverStatus !in terminalStates
                val mergedDetails = runCatching { JSONObject(item.optString("details_json", "{}")) }.getOrDefault(JSONObject())
                val detailKeys = localDetails.keys()
                while (detailKeys.hasNext()) {
                    val key = detailKeys.next()
                    if (!mergedDetails.has(key)) mergedDetails.put(key, localDetails.opt(key))
                }
                mergedDetails.remove("schedule_source")
                val values = ContentValues().apply {
                    put("activity_id", activityId)
                    put("account_id", item.optString("account_id", "user_lok"))
                    put("type", item.optString("type", "CUSTOM"))
                    put("date", activityDate)
                    put("title", item.optString("title", ""))
                    put("scheduled_time", item.optString("scheduled_time", ""))
                    put("window_start", item.optString("window_start", ""))
                    put("window_end", item.optString("window_end", ""))
                    put("notification_time", item.optString("notification_time", ""))
                    put("status", if (preserveLocal) localStatus else serverStatus)
                    put("submitted_at", if (preserveLocal) localSubmittedAt else item.optString("submitted_at").takeIf { it.isNotBlank() })
                    put("points", item.optInt("points", 0))
                    put("configured_points", item.optInt("points", 0))
                    put("validation_rule", item.optString("validation_rule", "STANDARD"))
                    put("details_json", mergedDetails.toString())
                    put("schedule_id", item.optString("schedule_id", ""))
                    put("schedule_version", serverVersion)
                }
                db.insertWithOnConflict("scheduled_activities", null, values, SQLiteDatabase.CONFLICT_REPLACE)
            }
            db.rawQuery(
                "SELECT activity_id, status FROM scheduled_activities WHERE date = ?",
                arrayOf(scheduleDate)
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    val id = cursor.getString(0)
                    val status = cursor.getString(1).orEmpty()
                    if (id !in seenIds && status !in terminalStates) {
                        db.execSQL("UPDATE scheduled_activities SET status = 'CANCELLED' WHERE activity_id = ?", arrayOf(id))
                    }
                }
            }
            reconcileHourlyBathConflicts(scheduleDate)
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
        requestBathAlarmRefresh()
    }

    // =========================================================================
    // PART 2, 3, 4: ACTIVITY JOURNAL, EVIDENCE INTELLIGENCE & DAILY SUMMARY
    // =========================================================================

    fun recordActivityJournalEvent(
        eventType: String,
        provenance: String,
        date: String = nowDate(),
        activityId: String = "",
        submissionId: String = "",
        evidenceId: String = "",
        category: String = "",
        description: String = "",
        details: JSONObject = JSONObject(),
        eventId: String = "aj_" + UUID.randomUUID().toString().replace("-", "").take(14)
    ): String {
        val validProvenance = setOf("USER_REPORTED", "EVIDENCE_SUBMITTED", "OCR_COMPLETED", "AI_RECOMMENDATION", "ADMIN_VERIFIED")
        val prov = provenance.uppercase(Locale.US).takeIf { it in validProvenance } ?: "USER_REPORTED"
        try {
            val cv = ContentValues().apply {
                put("event_id", eventId)
                put("account_id", "user_lok")
                put("date", date)
                put("event_type", eventType)
                put("provenance", prov)
                put("timestamp", nowIso())
                put("timezone", "Asia/Kolkata")
                put("source", "ANDROID")
                put("activity_id", activityId)
                put("submission_id", submissionId)
                put("evidence_id", evidenceId)
                put("category", category)
                put("description", description)
                put("details_json", details.toString())
            }
            writableDatabase.insertWithOnConflict("activity_journal", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
        } catch (_: Exception) {}
        return eventId
    }

    fun getActivityJournalEvents(date: String = nowDate()): List<JSONObject> {
        val out = mutableListOf<JSONObject>()
        try {
            readableDatabase.rawQuery(
                "SELECT event_id, event_type, provenance, timestamp, source, activity_id, submission_id, evidence_id, category, description, details_json FROM activity_journal WHERE date = ? ORDER BY timestamp ASC",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    out.add(JSONObject().apply {
                        put("event_id", c.getString(0).orEmpty())
                        put("event_type", c.getString(1).orEmpty())
                        put("provenance", c.getString(2).orEmpty())
                        put("timestamp", c.getString(3).orEmpty())
                        put("source", c.getString(4).orEmpty())
                        put("activity_id", c.getString(5).orEmpty())
                        put("submission_id", c.getString(6).orEmpty())
                        put("evidence_id", c.getString(7).orEmpty())
                        put("category", c.getString(8).orEmpty())
                        put("description", c.getString(9).orEmpty())
                        put("details", runCatching { JSONObject(c.getString(10).orEmpty()) }.getOrDefault(JSONObject()))
                    })
                }
            }
        } catch (_: Exception) {}
        return out
    }

    /**
     * Computes 64-bit difference hash (dHash) and average hash (aHash) safely with
     * downsampling to prevent OutOfMemoryError on older Android 8.1 tablets.
     */
    fun computePerceptualHashes(imageBytes: ByteArray): Pair<String, String> {
        if (imageBytes.isEmpty()) return "" to ""
        return try {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(imageBytes, 0, imageBytes.size, bounds)
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return "" to ""
            val opts = BitmapFactory.Options().apply {
                inSampleSize = 1
                val maxDim = maxOf(bounds.outWidth, bounds.outHeight)
                while (maxDim / inSampleSize > 512 && inSampleSize < 64) {
                    inSampleSize *= 2
                }
            }
            val decoded = BitmapFactory.decodeByteArray(imageBytes, 0, imageBytes.size, opts) ?: return "" to ""
            val scaledD = android.graphics.Bitmap.createScaledBitmap(decoded, 9, 8, true)
            val scaledA = android.graphics.Bitmap.createScaledBitmap(decoded, 8, 8, true)
            if (decoded !== scaledD && decoded !== scaledA) decoded.recycle()

            var dBits = 0L
            for (y in 0 until 8) {
                for (x in 0 until 8) {
                    val cL = scaledD.getPixel(x, y)
                    val cR = scaledD.getPixel(x + 1, y)
                    val lumaL = (android.graphics.Color.red(cL) * 299 + android.graphics.Color.green(cL) * 587 + android.graphics.Color.blue(cL) * 114) / 1000
                    val lumaR = (android.graphics.Color.red(cR) * 299 + android.graphics.Color.green(cR) * 587 + android.graphics.Color.blue(cR) * 114) / 1000
                    dBits = (dBits shl 1) or if (lumaL > lumaR) 1L else 0L
                }
            }
            scaledD.recycle()

            val lumas = IntArray(64)
            var sumLuma = 0
            for (y in 0 until 8) {
                for (x in 0 until 8) {
                    val c = scaledA.getPixel(x, y)
                    val luma = (android.graphics.Color.red(c) * 299 + android.graphics.Color.green(c) * 587 + android.graphics.Color.blue(c) * 114) / 1000
                    lumas[y * 8 + x] = luma
                    sumLuma += luma
                }
            }
            scaledA.recycle()
            val avg = sumLuma / 64
            var aBits = 0L
            for (l in lumas) {
                aBits = (aBits shl 1) or if (l >= avg) 1L else 0L
            }

            java.lang.Long.toUnsignedString(dBits, 16).padStart(16, '0') to
                java.lang.Long.toUnsignedString(aBits, 16).padStart(16, '0')
        } catch (_: Throwable) {
            "" to ""
        }
    }

    fun getHistoricalEvidenceFingerprints(): List<DisciplineScoringRules.EvidenceFingerprintRecord> {
        val history = mutableListOf<DisciplineScoringRules.EvidenceFingerprintRecord>()
        try {
            readableDatabase.rawQuery(
                "SELECT evidence_id, submission_id, sha256_hash, dhash, ahash, ocr_text FROM evidence_fingerprints ORDER BY created_at DESC LIMIT 200",
                null
            ).use { c ->
                while (c.moveToNext()) {
                    val pastText = c.getString(5).orEmpty()
                    val toks = Regex("[a-zA-Z0-9_\\-.]{3,}").findAll(pastText).map { it.value.lowercase(Locale.US) }.toSet()
                    history.add(
                        DisciplineScoringRules.EvidenceFingerprintRecord(
                            evidenceId = c.getString(0).orEmpty(),
                            submissionId = c.getString(1).orEmpty(),
                            sha256Hash = c.getString(2).orEmpty(),
                            dHash = c.getString(3).orEmpty(),
                            aHash = c.getString(4).orEmpty(),
                            ocrTokens = toks
                        )
                    )
                }
            }
        } catch (_: Exception) {}
        return history
    }

    fun analyzeAndRecordEvidence(
        evidenceId: String,
        submissionId: String,
        activityId: String,
        date: String,
        category: String = "",
        description: String = "",
        rawBytes: ByteArray = ByteArray(0),
        fileName: String = "evidence.jpg",
        // Additional named aliases used by MainActivity call sites
        mimeType: String = "image/jpeg",
        bytes: ByteArray? = null,
        userDescription: String? = null,
        validatedGymResult: MiFitnessGymWorkoutRules.GymEvaluationResult? = null,
        validatedHourlyResult: TaufaAiValidator.TaufaValidationResult? = null
    ): JSONObject {
        // Resolve aliases: bytes overrides rawBytes, userDescription overrides description
        @Suppress("NAME_SHADOWING")
        val rawBytes = bytes ?: rawBytes
        @Suppress("NAME_SHADOWING")
        val description = userDescription ?: description
        val sha256 = if (rawBytes.isNotEmpty()) DisciplineScoringRules.computeSha256Hex(rawBytes) else ""
        val (dhash, ahash) = computePerceptualHashes(rawBytes)
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        if (rawBytes.isNotEmpty()) {
            try { BitmapFactory.decodeByteArray(rawBytes, 0, rawBytes.size, bounds) } catch (_: Exception) {}
        }

        // Check OCR cache by immutable SHA-256 first
        var ocrStatus = "OCR_UNAVAILABLE"
        var ocrText = ""
        var ocrTokens = emptyList<String>()
        if (sha256.isNotBlank()) {
            try {
                readableDatabase.rawQuery(
                    "SELECT status, extracted_text, tokens_json FROM ocr_cache WHERE sha256_hash = ?",
                    arrayOf(sha256)
                ).use { c ->
                    if (c.moveToFirst()) {
                        ocrStatus = c.getString(0).orEmpty().ifBlank { "OCR_UNAVAILABLE" }
                        ocrText = c.getString(1).orEmpty()
                        val arr = runCatching { JSONArray(c.getString(2).orEmpty()) }.getOrDefault(JSONArray())
                        val list = mutableListOf<String>()
                        for (i in 0 until arr.length()) {
                            val t = arr.optString(i).trim()
                            if (t.isNotEmpty()) list.add(t)
                        }
                        ocrTokens = list
                    }
                }
            } catch (_: Exception) {}
        }

        if (ocrStatus == "OCR_UNAVAILABLE" && rawBytes.isNotEmpty()) {
            val analysis = try {
                if (Looper.myLooper() != Looper.getMainLooper()) {
                    EvidenceAnalysisEngine.analyze(
                        context = appContext,
                        originalBytes = rawBytes,
                        attachmentId = evidenceId,
                        taskId = submissionId,
                        batchId = submissionId,
                        persistDerivatives = false,
                        useObjectDetection = false
                    )
                } else {
                    var res: JSONObject? = null
                    val t = Thread {
                        res = EvidenceAnalysisEngine.analyze(
                            context = appContext,
                            originalBytes = rawBytes,
                            attachmentId = evidenceId,
                            taskId = submissionId,
                            batchId = submissionId,
                            persistDerivatives = false,
                            useObjectDetection = false
                        )
                    }
                    t.start()
                    t.join(3500)
                    res ?: JSONObject()
                }
            } catch (_: Exception) {
                JSONObject()
            }

            val ocrObj = analysis.optJSONObject("ocr")
            if (ocrObj != null) {
                val st = ocrObj.optString("status")
                if (st == "OCR_COMPLETED") {
                    ocrStatus = "OCR_COMPLETED"
                    ocrText = ocrObj.optString("text")
                    ocrTokens = EvidenceAnalysisRules.imageTokens(ocrText)
                } else if (st == "NO_TEXT_DETECTED") {
                    ocrStatus = "NO_TEXT_DETECTED"
                    ocrText = ""
                    ocrTokens = emptyList()
                } else {
                    ocrStatus = st.ifBlank { "OCR_UNAVAILABLE" }
                }
            }
            if (ocrStatus in setOf("OCR_COMPLETED", "NO_TEXT_DETECTED")) {
                try {
                    val cvOcr = ContentValues().apply {
                        put("sha256_hash", sha256)
                        put("status", ocrStatus)
                        put("extracted_text", ocrText)
                        put("tokens_json", JSONArray(ocrTokens).toString())
                        put("confidence", ocrObj?.optDouble("confidence", 0.0) ?: 0.0)
                        put("engine", ocrObj?.optString("engine", EvidenceAnalysisEngine.OCR_ENGINE) ?: "")
                        put("computed_at", nowIso())
                    }
                    writableDatabase.insertWithOnConflict("ocr_cache", null, cvOcr, SQLiteDatabase.CONFLICT_REPLACE)
                } catch (_: Exception) {}
            }
        }

        val history = getHistoricalEvidenceFingerprints()

        val dupResult = DisciplineScoringRules.evaluateDuplicateCheck(
            currentSha256 = sha256,
            currentDhash = dhash,
            currentOcrTokens = ocrTokens,
            history = history,
            currentAhash = ahash,
            excludeEvidenceId = evidenceId,
            excludeSubmissionId = submissionId,
            currentActivityId = activityId
        )

        try {
            val cv = ContentValues().apply {
                put("evidence_id", evidenceId)
                put("submission_id", submissionId)
                put("activity_id", activityId)
                put("date", date)
                put("sha256_hash", sha256)
                put("dhash", dhash)
                put("ahash", ahash)
                put("width", bounds.outWidth.coerceAtLeast(0))
                put("height", bounds.outHeight.coerceAtLeast(0))
                put("file_size_bytes", rawBytes.size)
                put("ocr_status", ocrStatus)
                put("ocr_text", ocrText)
                put("duplicate_status", dupResult.status)
                put("matched_evidence_id", dupResult.matchedEvidenceId.orEmpty())
                put("similarity_score", dupResult.similarityScore)
                put("comparison_method", dupResult.comparisonMethod)
                put("created_at", nowIso())
            }
            writableDatabase.insertWithOnConflict("evidence_fingerprints", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
        } catch (_: Exception) {}

        var localFilePath = ""
        if (rawBytes.isNotEmpty()) {
            try {
                val proofsDir = File(appContext.filesDir, "proofs").apply { mkdirs() }
                val ext = if (fileName.contains('.')) fileName.substringAfterLast('.') else "jpg"
                val targetFile = File(proofsDir, "$evidenceId.$ext")
                targetFile.writeBytes(rawBytes)
                localFilePath = targetFile.absolutePath
            } catch (_: Exception) {}
        }

        val isNonDuplicate = dupResult.status != "EXACT_DUPLICATE"
        var actType = category.uppercase(Locale.US).trim()
        val isGymValidated = category == "GYM" && validatedGymResult?.let {
            it.eligible && it.status == "APPROVED" && it.matchedActivityId == activityId &&
                !it.dateMismatch && !it.timeMismatch && !it.isDuplicate && !it.alreadyAwarded
        } == true
        val isHourlyEligible = (category.equals("HOURLY", ignoreCase = true) || actType == "HOURLY" ||
            category.uppercase(Locale.US) in TaufaAiValidator.SUPPORTED_HOURLY_CATEGORIES)
        val isHourlyValidated = isHourlyEligible && validatedHourlyResult?.let {
            it.isValid && it.qualifiesForPoints && (it.score ?: 0) > 0 && it.submissionId == submissionId
        } == true
        val taskValidated = isGymValidated || isHourlyValidated
        var pointsAwarded = 0
        val resolvedCategory = when {
            category.isNotBlank() && !category.equals("HOURLY", ignoreCase = true) -> category
            activityId.isNotBlank() -> getScheduledActivityCategory(activityId).takeIf { it.isNotBlank() } ?: ""
            else -> ""
        }.ifBlank {
            if (description.startsWith("[")) {
                val cand = description.substringAfter("[").substringBefore("]").trim()
                if (cand.isNotBlank()) cand else ""
            } else ""
        }.ifBlank {
            category.takeIf { it.isNotBlank() } ?: ""
        }
        if (isNonDuplicate && taskValidated) {
            var targetPoints = 0
            if (isHourlyValidated && validatedHourlyResult?.score != null) {
                targetPoints = validatedHourlyResult.score!!
            } else if (activityId.isNotBlank()) {
                readableDatabase.rawQuery(
                    "SELECT configured_points, type FROM scheduled_activities WHERE activity_id = ?",
                    arrayOf(activityId)
                ).use { c ->
                    if (c.moveToFirst()) {
                        targetPoints = c.getInt(0)
                        val t = c.getString(1).orEmpty().trim()
                        if (t.isNotBlank()) actType = t.uppercase(Locale.US)
                    }
                }
            }
            if (targetPoints <= 0) {
                targetPoints = when (actType) {
                    "BATH" -> ProductPolicy.BATH_POINTS_PER_OCCURRENCE
                    "GYM" -> 15
                    "TRAVEL" -> 10
                    "WAKE_UP", "WAKEUP" -> 15
                    else -> if (isHourlyValidated) (validatedHourlyResult?.score ?: 0) else 0
                }
            }
            val alreadyAwardedForActivity = if (activityId.isNotBlank()) {
                readableDatabase.rawQuery(
                    "SELECT 1 FROM score_events WHERE (event_type = 'PROOF_APPROVED' OR event_type = 'TASK_VERIFIED') AND points > 0 AND audit_json LIKE ?",
                    arrayOf("%$activityId%")
                ).use { it.moveToFirst() }
            } else false

            val alreadyAwarded = alreadyAwardedForActivity || readableDatabase.rawQuery(
                "SELECT 1 FROM score_events WHERE (event_type = 'PROOF_APPROVED' OR event_type = 'TASK_VERIFIED') AND audit_json LIKE ?",
                arrayOf("%$evidenceId%")
            ).use { it.moveToFirst() }

            pointsAwarded = if (alreadyAwardedForActivity) 0 else targetPoints

            try {
                val auditObj = JSONObject().apply {
                    if (resolvedCategory.isNotBlank()) put("category", resolvedCategory)
                    if (description.isNotBlank()) put("description", description)
                    if (activityId.isNotBlank()) put("activity_id", activityId)
                    if (submissionId.isNotBlank()) put("submission_id", submissionId)
                    if (isHourlyValidated && validatedHourlyResult != null) {
                        put("taufa_ai", validatedHourlyResult.toJsonObject())
                    }
                }
                val cvReview = ContentValues().apply {
                    put("evidence_id", evidenceId)
                    put("submission_id", submissionId)
                    put("activity_id", activityId)
                    put("date", date)
                    put("user_id", getConfigValue("user_id", ""))
                    put("task_code", actType.ifBlank { "HOURLY" })
                    put("task_title", "$actType Checkpoint: ${resolvedCategory.ifBlank { "Activity" }}")
                    put("status", "APPROVED")
                    put("decision", "APPROVED")
                    put("points_awarded", pointsAwarded)
                    put("file_path", localFilePath)
                    put("sha256_hash", sha256)
                    put("submitted_at", nowIso())
                    put("reviewed_at", nowIso())
                    put("reviewed_by", if (isHourlyValidated) "Taufa_AI" else if (isGymValidated) "Gym_Validator" else "Automated Validator")
                    put("reason_note", if (isHourlyValidated) (validatedHourlyResult?.reason?.ifBlank { "Taufa AI validated submission ($actType)" } ?: "Validated submission ($actType)") else "Validated submission ($actType)")
                    put("priority", "NORMAL")
                    put("audit_json", auditObj.toString())
                    if (hasProofReviewsCategoryColumn() && resolvedCategory.isNotBlank()) {
                        put("category", resolvedCategory)
                    }
                }
                writableDatabase.insertWithOnConflict("proof_reviews", null, cvReview, SQLiteDatabase.CONFLICT_REPLACE)
                invalidateApprovedProofCache(evidenceId)
            } catch (_: Exception) {}

            if (activityId.isNotBlank()) {
                val currentDetails = try {
                    readableDatabase.rawQuery("SELECT details_json FROM scheduled_activities WHERE activity_id = ?", arrayOf(activityId)).use {
                        if (it.moveToFirst()) JSONObject(it.getString(0) ?: "{}") else JSONObject()
                    }
                } catch (_: Exception) { JSONObject() }
                if (resolvedCategory.isNotBlank()) {
                    currentDetails.put("category", resolvedCategory)
                    currentDetails.put("activity_category", resolvedCategory)
                }
                if (description.isNotBlank()) currentDetails.put("description", description)
                if (submissionId.isNotBlank()) currentDetails.put("submission_id", submissionId)
                if (evidenceId.isNotBlank()) currentDetails.put("evidence_id", evidenceId)
                if (isHourlyValidated && validatedHourlyResult != null) {
                    currentDetails.put("taufa_ai", validatedHourlyResult.toJsonObject())
                }
                writableDatabase.execSQL(
                    "UPDATE scheduled_activities SET status = 'VERIFIED', points = CASE WHEN points > 0 THEN points ELSE ? END, attendance_status = 'PRESENT', validation_status = 'VERIFIED', submitted_at = ?, details_json = ? WHERE activity_id = ?",
                    arrayOf(targetPoints, nowIso(), currentDetails.toString(), activityId)
                )
            }

            if (actType == "BATH") {
                val countNow = getBathSubmissionCount(date)
                val bathSt = if (countNow <= 1) "BATH 1 VERIFIED (+5 PTS)" else "BATH COMPLETE (2/2) (+5 PTS)"
                writableDatabase.execSQL("UPDATE daily_state SET bath_status = ? WHERE date = ?", arrayOf(bathSt, date))
            } else if (actType == "GYM") {
                writableDatabase.execSQL("UPDATE daily_state SET gym_status = 'GYM VERIFIED (+15 PTS)' WHERE date = ?", arrayOf(date))
            }

            if (!alreadyAwarded && pointsAwarded > 0) {
                val audit = JSONObject().apply {
                    put("evidence_id", evidenceId)
                    put("activity_id", activityId)
                    put("submission_id", submissionId)
                    put("action", "PROOF_VALIDATED")
                    put("points", pointsAwarded)
                    put("task_code", actType)
                    put("reviewed_by", if (isHourlyValidated) "Taufa_AI" else if (isGymValidated) "Gym_Validator" else "Automated Validator")
                    put("approval_source", "AUTHENTICATED_HUMAN_ADMIN")
                    if (resolvedCategory.isNotBlank()) put("category", resolvedCategory)
                    if (isHourlyValidated && validatedHourlyResult != null) {
                        put("taufa_ai", validatedHourlyResult.toJsonObject())
                    }
                }
                addScoreEvent(
                    date = date,
                    isDevMode = false,
                    eventType = "PROOF_APPROVED",
                    description = "Validated proof for $actType (+$pointsAwarded pts)",
                    points = pointsAwarded,
                    audit = audit
                )
            }
        } else {
            try {
                val auditObj = JSONObject().apply {
                    if (resolvedCategory.isNotBlank()) put("category", resolvedCategory)
                    if (description.isNotBlank()) put("description", description)
                    if (activityId.isNotBlank()) put("activity_id", activityId)
                    if (submissionId.isNotBlank()) put("submission_id", submissionId)
                }
                val cvReview = ContentValues().apply {
                    put("evidence_id", evidenceId)
                    put("submission_id", submissionId)
                    put("activity_id", activityId)
                    put("date", date)
                    put("user_id", getConfigValue("user_id", ""))
                    put("task_code", resolvedCategory.ifBlank { actType.ifBlank { "HOURLY" } })
                    put("task_title", "$actType Checkpoint: ${resolvedCategory.ifBlank { "Activity" }}")
                    put("status", "PENDING")
                    put("file_path", localFilePath)
                    put("sha256_hash", sha256)
                    put("submitted_at", nowIso())
                    put("priority", "HIGH")
                    put("reason_note", if (isNonDuplicate) "Task-specific validation or authorized review pending" else "Duplicate detected (${dupResult.status})")
                    put("audit_json", auditObj.toString())
                    if (hasProofReviewsCategoryColumn() && resolvedCategory.isNotBlank()) {
                        put("category", resolvedCategory)
                    }
                }
                writableDatabase.insertWithOnConflict("proof_reviews", null, cvReview, SQLiteDatabase.CONFLICT_REPLACE)
            } catch (_: Exception) {}
        }

        recordActivityJournalEvent(
            eventType = "ACTIVITY_REPORTED",
            provenance = "USER_REPORTED",
            date = date,
            activityId = activityId,
            submissionId = submissionId,
            evidenceId = evidenceId,
            category = category,
            description = description,
            eventId = "aj_rep_${submissionId.ifBlank { evidenceId }}"
        )

        recordActivityJournalEvent(
            eventType = "EVIDENCE_SUBMITTED",
            provenance = "EVIDENCE_SUBMITTED",
            date = date,
            activityId = activityId,
            submissionId = submissionId,
            evidenceId = evidenceId,
            category = category,
            description = fileName,
            details = JSONObject().apply {
                put("sha256_hash", sha256)
                put("dhash", dhash)
                put("ahash", ahash)
                put("duplicate_status", dupResult.status)
                put("matched_evidence_id", dupResult.matchedEvidenceId.orEmpty())
                put("similarity_score", dupResult.similarityScore)
                put("comparison_method", dupResult.comparisonMethod)
                put("ocr_status", ocrStatus)
            },
            eventId = "aj_ev_$evidenceId"
        )

        return JSONObject().apply {
            put("evidence_id", evidenceId)
            put("submission_id", submissionId)
            put("sha256_hash", sha256)
            put("dhash", dhash)
            put("ahash", ahash)
            put("ocr_status", ocrStatus)
            put("ocr_text", ocrText)
            put("duplicate_status", dupResult.status)
            put("duplicate_risk", dupResult.duplicateRisk)
            put("matched_evidence_id", dupResult.matchedEvidenceId.orEmpty())
            put("similarity_score", dupResult.similarityScore)
            put("comparison_method", dupResult.comparisonMethod)
            put("explanation", dupResult.explanation)
            put("points_awarded", pointsAwarded)
            put("validation_decision", if (isNonDuplicate && taskValidated) "APPROVED" else "FLAGGED_FOR_REVIEW")
        }
    }

    /**
     * Part 4 (Sections 7, 8, 9): Generates a truthful, versioned daily summary from
     * local + synced records. Reopening Today's Summary is idempotent and returns the
     * cached snapshot unless new activity or review state has arrived.
     */
    fun getTruthfulDailySummary(date: String = nowDate(), isDevMode: Boolean = false): JSONObject {
        ensureDailyActivities(date)
        val devFlag = if (isDevMode) 1 else 0
        val dayState = getDayState(date, isDevMode)
        val activities = getDailyActivities(date)
        val reviews = getDailyProofReviews(date)
        val score = getScore(date, isDevMode)
        val events = getScoreEvents(date, isDevMode)

        // Query focus sessions for exact tracked minutes
        var focusCount = 0
        var trackedFocusMinutes = 0
        try {
            readableDatabase.rawQuery(
                "SELECT actual_minutes, target_minutes, status FROM focus_sessions WHERE date = ? AND is_dev_mode = ?",
                arrayOf(date, devFlag.toString())
            ).use { c ->
                while (c.moveToNext()) {
                    val st = c.getString(2).orEmpty().uppercase(Locale.US)
                    if (st == "COMPLETED" || st == "FINISHED") {
                        focusCount++
                        val actual = c.getDouble(0).toInt()
                        val target = c.getInt(1)
                        trackedFocusMinutes += if (actual > 0) actual else target
                    }
                }
            }
        } catch (_: Exception) {}

        // Query evidence fingerprints for OCR and duplicate stats
        var evidenceCount = 0
        var ocrCompletedCount = 0
        var duplicateWarningsCount = 0
        try {
            readableDatabase.rawQuery(
                "SELECT ocr_status, duplicate_status FROM evidence_fingerprints WHERE date = ?",
                arrayOf(date)
            ).use { c ->
                while (c.moveToNext()) {
                    evidenceCount++
                    val ocrSt = c.getString(0).orEmpty().uppercase(Locale.US)
                    val dupSt = c.getString(1).orEmpty().uppercase(Locale.US)
                    if (ocrSt == "OCR_COMPLETED") ocrCompletedCount++
                    if (dupSt in setOf("POSSIBLE_DUPLICATE", "HIGH_SIMILARITY", "EXACT_DUPLICATE")) {
                        duplicateWarningsCount++
                    }
                }
            }
        } catch (_: Exception) {}

        // Compute deterministic snapshot hash
        val fpBuilder = StringBuilder()
        fpBuilder.append(date).append('|').append(score).append('|').append(focusCount).append(':').append(trackedFocusMinutes)
        for (a in activities) {
            fpBuilder.append('|').append(a.activityId).append(':').append(a.status).append(':').append(a.submittedAt.orEmpty())
        }
        for (r in reviews) {
            fpBuilder.append('|').append(r.optString("evidence_id")).append(':').append(r.optString("status")).append(':').append(r.optInt("points_awarded"))
        }
        fpBuilder.append('|').append(evidenceCount).append(':').append(ocrCompletedCount).append(':').append(duplicateWarningsCount)
        val snapshotHash = DisciplineScoringRules.computeSha256Hex(fpBuilder.toString().toByteArray(Charsets.UTF_8)).take(16)

        var prevVersion = 0
        try {
            readableDatabase.rawQuery(
                "SELECT snapshot_version, snapshot_hash, summary_json FROM daily_summary_snapshots WHERE date = ? AND account_id = 'user_lok' AND is_dev_mode = ?",
                arrayOf(date, devFlag.toString())
            ).use { c ->
                if (c.moveToFirst()) {
                    prevVersion = c.getInt(0)
                    val existingHash = c.getString(1).orEmpty()
                    if (existingHash == snapshotHash) {
                        val cached = JSONObject(c.getString(2).orEmpty())
                        cached.put("cached_snapshot", true)
                        return cached
                    }
                }
            }
        } catch (_: Exception) {}

        val scheduledTasks = activities.size
        var submittedTasks = 0
        var verifiedTasks = 0
        var pendingReview = 0
        var missedTasks = 0

        var hourlyScheduled = 0
        var hourlySubmitted = 0
        var hourlyVerified = 0
        var hourlyPending = 0
        var hourlyMissed = 0

        var wakeStatus = dayState.optString("wakeup_status", "PENDING")
        var bathStatus = dayState.optString("bath_status", "PENDING")
        var gymStatus = dayState.optString("gym_status", "UPCOMING")

        val verifiedSet = setOf("VERIFIED", "APPROVED", "COMPLETED")
        val pendingSet = setOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "PENDING", "UNDER_REVIEW", "NEEDS_REVIEW", "DUPLICATE_REVIEW", "ADMIN_REVIEW")
        val missedSet = setOf("MISSED", "CLOSED")

        for (act in activities) {
            val st = act.status.uppercase(Locale.US)
            val hasSub = !act.submittedAt.isNullOrBlank() || st in verifiedSet || st in pendingSet
            if (hasSub) submittedTasks++
            when {
                st in verifiedSet -> verifiedTasks++
                st in pendingSet -> pendingReview++
                st in missedSet -> missedTasks++
            }
            when {
                act.type.equals("WAKE_UP", ignoreCase = true) -> wakeStatus = st
                act.type.equals("BATH", ignoreCase = true) -> bathStatus = st
                act.type.equals("GYM", ignoreCase = true) -> gymStatus = st
                act.type.startsWith("HOURLY", ignoreCase = true) -> {
                    hourlyScheduled++
                    if (hasSub) hourlySubmitted++
                    when {
                        st in verifiedSet -> hourlyVerified++
                        st in pendingSet -> hourlyPending++
                        st in missedSet -> hourlyMissed++
                    }
                }
            }
        }

        val totalProofsSubmitted = maxOf(evidenceCount, reviews.size, submittedTasks)
        val adminApproved = reviews.count { it.optString("status").uppercase(Locale.US) in setOf("APPROVED", "VERIFIED") }
        val adminRejected = reviews.count { it.optString("status").uppercase(Locale.US) == "REJECTED" }
        val awaitingReview = maxOf(pendingReview, reviews.count { it.optString("status").uppercase(Locale.US) in pendingSet })

        var provisionalPoints = 0
        var penalties = 0
        for (i in 0 until events.length()) {
            val ev = events.optJSONObject(i) ?: continue
            val pts = ev.optInt("points", 0)
            if (pts < 0) penalties += kotlin.math.abs(pts)
        }
        provisionalPoints = pendingReview * 8

        val currentPrivilege = when {
            score >= 90 -> "ELITE UNLOCKED (All Rewards Allowed)"
            score >= 80 -> "REWARD UNLOCKED (Entertainment Allowed)"
            score >= 60 -> "CONTROLLED MODE (${80 - score} pts needed for full unlock)"
            else -> "STRICT LOCKED (${(80 - score).coerceAtLeast(0)} pts needed for reward)"
        }
        val privilegeReason = when {
            score >= 80 -> "Verified score $score/100 meets reward unlock threshold (>= 80)."
            score >= 60 -> "Verified score is $score/80; local music allowed, video streaming restricted."
            else -> "Verified score is $score/80; distraction restrictions remain active."
        }

        val focusStudyDisplay = when {
            trackedFocusMinutes > 0 -> "$trackedFocusMinutes tracked focus min ($focusCount session(s)) + $hourlySubmitted hourly check-in(s)"
            hourlySubmitted > 0 -> "$hourlySubmitted reported check-in(s) (no separate timer minutes recorded)"
            else -> "0 tracked focus minutes / 0 study check-ins recorded"
        }

        val obs1 = if (submittedTasks > 0) {
            "Recorded $submittedTasks of $scheduledTasks scheduled activities today ($verifiedTasks verified, $awaitingReview awaiting review)."
        } else {
            "No activity submissions have been recorded yet across $scheduledTasks scheduled slots for $date."
        }
        val obs2 = if (totalProofsSubmitted > 0) {
            "Processed $totalProofsSubmitted proof submission(s): $ocrCompletedCount with OCR text, $duplicateWarningsCount duplicate warning(s)."
        } else {
            "No evidence attachments have been uploaded for today's checkpoints yet."
        }
        val rec1 = when {
            wakeStatus in setOf("MISSED", "CLOSED", "PENDING") ->
                "Complete tomorrow's Wake-Up verification inside the opening window to secure early points."
            hourlyMissed > 0 ->
                "$hourlyMissed hourly checkpoint(s) closed without submission today; submit brief notes before each 20-minute window closes tomorrow."
            duplicateWarningsCount > 0 ->
                "$duplicateWarningsCount submission(s) matched prior evidence; capture a fresh workspace photo for each checkpoint."
            awaitingReview > 0 ->
                "$awaitingReview proof submission(s) are currently awaiting verification."
            else ->
                "Maintain on-time checkpoint submissions tomorrow to preserve your discipline streak."
        }

        val formattedText = buildString {
            appendLine("STRICTRECORDS — DAILY SUMMARY")
            appendLine()
            appendLine("Date: $date")
            appendLine()
            appendLine("Activities")
            appendLine("• Wake-Up: $wakeStatus")
            appendLine("• Morning Routine: $bathStatus")
            appendLine("• Hourly Check-Ins: $hourlySubmitted/$hourlyScheduled submitted ($hourlyVerified verified, $hourlyPending pending, $hourlyMissed missed)")
            appendLine("• Focus / Study: $focusStudyDisplay")
            appendLine("• Exercise / Gym: $gymStatus")
            appendLine()
            appendLine("Evidence & Verification")
            appendLine("• Proofs Submitted: $totalProofsSubmitted")
            appendLine("• OCR Completed: $ocrCompletedCount")
            appendLine("• Duplicate Warnings: $duplicateWarningsCount")
            appendLine("• Pending Review: $awaitingReview")
            appendLine()
            appendLine("Score")
            appendLine("• Verified Score: $score")
            appendLine("• Provisional Points: $provisionalPoints")
            appendLine("• Penalties: $penalties")
            appendLine("• Current Privilege: $currentPrivilege")
            appendLine()
            appendLine("StrictBot Notes")
            appendLine("• $obs1")
            appendLine("• $obs2")
            append("• $rec1")
        }

        val nextVersion = prevVersion + 1
        val generatedAt = nowIso()
        val summaryObj = JSONObject().apply {
            put("date", date)
            put("account_id", "user_lok")
            put("timezone", "Asia/Kolkata")
            put("snapshot_version", nextVersion)
            put("snapshot_hash", snapshotHash)
            put("cached_snapshot", false)
            put("generated_at", generatedAt)
            put("activity_overview", JSONObject().apply {
                put("scheduled_tasks", scheduledTasks)
                put("submitted_tasks", submittedTasks)
                put("verified_tasks", verifiedTasks)
                put("pending_review", awaitingReview)
                put("missed_tasks", missedTasks)
            })
            put("productivity_breakdown", JSONObject().apply {
                put("wake_up_status", wakeStatus)
                put("morning_routine_status", bathStatus)
                put("exercise_gym_status", gymStatus)
                put("hourly_scheduled", hourlyScheduled)
                put("hourly_submitted", hourlySubmitted)
                put("hourly_verified", hourlyVerified)
                put("hourly_pending", hourlyPending)
                put("hourly_missed", hourlyMissed)
                put("tracked_focus_sessions", focusCount)
                put("tracked_focus_minutes", trackedFocusMinutes)
                put("focus_study_display", focusStudyDisplay)
            })
            put("evidence_review", JSONObject().apply {
                put("proofs_submitted", totalProofsSubmitted)
                put("ocr_completed", ocrCompletedCount)
                put("duplicate_warnings", duplicateWarningsCount)
                put("awaiting_review", awaitingReview)
                put("admin_approved", adminApproved)
                put("admin_rejected", adminRejected)
            })
            put("score_and_rewards", JSONObject().apply {
                put("verified_score", score)
                put("provisional_points", provisionalPoints)
                put("penalties", penalties)
                put("current_privilege", currentPrivilege)
                put("privilege_reason", privilegeReason)
            })
            put("strictbot_summary", "$obs1 $obs2 $rec1")
            put("observations", JSONArray().put(obs1).put(obs2))
            put("recommendations", JSONArray().put(rec1))
            put("formatted_text", formattedText)
        }

        try {
            val cv = ContentValues().apply {
                put("date", date)
                put("account_id", "user_lok")
                put("is_dev_mode", devFlag)
                put("snapshot_version", nextVersion)
                put("snapshot_hash", snapshotHash)
                put("summary_json", summaryObj.toString())
                put("formatted_text", formattedText)
                put("generated_at", generatedAt)
            }
            writableDatabase.insertWithOnConflict("daily_summary_snapshots", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
        } catch (_: Exception) {}

        return summaryObj
    }

    // =========================================================================
    // PART 6: PC SHUTDOWN BONUS, ATTENDANCE, EVIDENCE & NEXT-DAY LEDGER
    // =========================================================================

    fun recordPcShutdownLocal(
        date: String,
        eventId: String,
        shutdownTime: String,
        cutoffTime: String = "23:45",
        isQualifying: Boolean? = null,
        targetRewardDate: String? = null,
        bonusPoints: Int = 15,
        disqualificationReason: String? = null,
        timezone: String = "Asia/Kolkata",
        deviceId: String = "windows_lok_pc",
        evidenceSource: String = "WIN32_API"
    ): String {
        val now = nowIso()
        val timeHhmmss = if (shutdownTime.contains("T")) {
            shutdownTime.substringAfter("T").take(8)
        } else if (shutdownTime.length >= 8) {
            shutdownTime.take(8)
        } else {
            shutdownTime
        }

        val qualifying = isQualifying ?: DisciplineScoringRules.evaluatePcShutdownEligibility(
            timeHhmm = timeHhmmss,
            cutoffHhmm = cutoffTime
        ).first

        val nextDay = targetRewardDate ?: run {
            try {
                val sdf = java.text.SimpleDateFormat("yyyy-MM-dd", Locale.US)
                val cal = Calendar.getInstance().apply {
                    time = sdf.parse(date) ?: java.util.Date()
                    add(Calendar.DAY_OF_YEAR, 1)
                }
                sdf.format(cal.time)
            } catch (_: Exception) {
                date
            }
        }

        val disqReason = disqualificationReason ?: if (!qualifying) {
            "Shutdown time ($timeHhmmss) is at or after cutoff ($cutoffTime)"
        } else null

        val existing = getPcShutdownLocalStatus(date)
        val phoneAttId = existing.optString("phone_attendance_id").takeIf { it.isNotBlank() }
        val phoneAttTime = existing.optString("phone_attendance_time").takeIf { it.isNotBlank() }
        val evId = existing.optString("evidence_id").takeIf { it.isNotBlank() }
        val photoSubTime = existing.optString("photo_submitted_at").takeIf { it.isNotBlank() }

        val status = when {
            !qualifying -> "INELIGIBLE"
            phoneAttId.isNullOrBlank() -> "AWAITING_PHONE_ATTENDANCE"
            evId.isNullOrBlank() -> "AWAITING_SHUTDOWN_PHOTO"
            else -> "AWAITING_REVIEW"
        }

        val cv = ContentValues().apply {
            put("event_id", eventId)
            put("account_id", "user_lok")
            put("device_id", deviceId)
            put("shutdown_timestamp", shutdownTime)
            put("date", date)
            put("time_hhmmss", timeHhmmss)
            put("cutoff_time", cutoffTime)
            put("timezone", timezone)
            put("evidence_source", evidenceSource)
            put("is_qualifying", if (qualifying) 1 else 0)
            put("disqualification_reason", disqReason)
            put("target_reward_date", nextDay)
            put("bonus_status", status)
            put("bonus_points", bonusPoints)
            if (phoneAttId != null) {
                put("phone_attendance_id", phoneAttId)
                put("phone_attendance_time", phoneAttTime)
            }
            if (evId != null) {
                put("evidence_id", evId)
                put("photo_submitted_at", photoSubTime)
            }
            put("created_at", now)
            put("updated_at", now)
        }
        writableDatabase.insertWithOnConflict("pc_shutdown_local", null, cv, SQLiteDatabase.CONFLICT_REPLACE)

        recordActivityJournalEvent(
            eventType = "PC_SHUTDOWN_EVENT",
            provenance = "EVIDENCE_SUBMITTED",
            date = date,
            activityId = "PC_SHUTDOWN",
            category = "SYSTEM_EVENT",
            description = "Windows PC shutdown recorded at $timeHhmmss (${if (qualifying) "Qualifying" else "Ineligible"})",
            details = JSONObject().apply {
                put("event_id", eventId)
                put("shutdown_timestamp", shutdownTime)
                put("is_qualifying", qualifying)
                put("target_reward_date", nextDay)
                put("cutoff_time", cutoffTime)
            }
        )

        return eventId
    }

    fun confirmPhoneShutdownAttendance(
        date: String = nowDate(),
        phoneDeviceId: String = "android_phone",
        accountId: String = "user_lok"
    ): JSONObject {
        val now = nowIso()
        val cur = getPcShutdownLocalStatus(date)
        val eventId = cur.optString("event_id").takeIf { it.isNotBlank() } ?: ("sd_" + UUID.randomUUID().toString().replace("-", "").take(12))
        val existingAttId = cur.optString("phone_attendance_id")
        if (existingAttId.isNotBlank()) {
            return cur
        }

        val attId = "att_sd_" + UUID.randomUUID().toString().replace("-", "").take(10)
        val isQualifying = cur.optBoolean("is_qualifying", true)
        val hasShutdown = cur.optBoolean("shutdown_recorded", false)
        val hasPhoto = cur.optString("evidence_id").isNotBlank()

        val newStatus = when {
            hasShutdown && !isQualifying -> "INELIGIBLE"
            !hasPhoto -> "AWAITING_SHUTDOWN_PHOTO"
            else -> "AWAITING_REVIEW"
        }

        val cv = ContentValues().apply {
            put("event_id", eventId)
            put("account_id", accountId)
            put("date", date)
            put("phone_attendance_id", attId)
            put("phone_attendance_time", now)
            put("phone_device_id", phoneDeviceId)
            put("bonus_status", newStatus)
            put("updated_at", now)
            if (!cur.has("target_reward_date") || cur.optString("target_reward_date").isBlank()) {
                val nextDay = try {
                    val sdf = java.text.SimpleDateFormat("yyyy-MM-dd", Locale.US)
                    val cal = Calendar.getInstance().apply {
                        time = sdf.parse(date) ?: java.util.Date()
                        add(Calendar.DAY_OF_YEAR, 1)
                    }
                    sdf.format(cal.time)
                } catch (_: Exception) { date }
                put("target_reward_date", nextDay)
                put("cutoff_time", "23:45")
                put("timezone", "Asia/Kolkata")
                put("created_at", now)
            }
        }
        writableDatabase.insertWithOnConflict("pc_shutdown_local", null, cv, SQLiteDatabase.CONFLICT_REPLACE)

        enqueueSyncEvent(
            eventType = "PC_SHUTDOWN_ATTENDANCE",
            payload = JSONObject().apply {
                put("date", date)
                put("event_id", eventId)
                put("phone_attendance_id", attId)
                put("phone_device_id", phoneDeviceId)
                put("attendance_time", now)
                put("account_id", accountId)
            }
        )

        recordActivityJournalEvent(
            eventType = "PC_SHUTDOWN_ATTENDANCE",
            provenance = "USER_REPORTED",
            date = date,
            activityId = "PC_SHUTDOWN",
            category = "ATTENDANCE",
            description = "PC shutdown attendance confirmed from phone",
            details = JSONObject().apply {
                put("phone_attendance_id", attId)
                put("phone_device_id", phoneDeviceId)
                put("event_id", eventId)
            }
        )

        return getPcShutdownLocalStatus(date)
    }

    fun recordPhoneShutdownPhoto(
        date: String = nowDate(),
        evidenceId: String,
        accountId: String = "user_lok"
    ): JSONObject {
        val now = nowIso()
        val cur = getPcShutdownLocalStatus(date)
        val eventId = cur.optString("event_id").takeIf { it.isNotBlank() } ?: ("sd_" + UUID.randomUUID().toString().replace("-", "").take(12))

        val isQualifying = cur.optBoolean("is_qualifying", true)
        val hasShutdown = cur.optBoolean("shutdown_recorded", false)
        val hasAttendance = cur.optString("phone_attendance_id").isNotBlank()

        val newStatus = when {
            hasShutdown && !isQualifying -> "INELIGIBLE"
            !hasAttendance -> "AWAITING_PHONE_ATTENDANCE"
            else -> "AWAITING_REVIEW"
        }

        val cv = ContentValues().apply {
            put("event_id", eventId)
            put("account_id", accountId)
            put("date", date)
            put("evidence_id", evidenceId)
            put("photo_submitted_at", now)
            put("bonus_status", newStatus)
            put("updated_at", now)
            if (!cur.has("target_reward_date") || cur.optString("target_reward_date").isBlank()) {
                val nextDay = try {
                    val sdf = java.text.SimpleDateFormat("yyyy-MM-dd", Locale.US)
                    val cal = Calendar.getInstance().apply {
                        time = sdf.parse(date) ?: java.util.Date()
                        add(Calendar.DAY_OF_YEAR, 1)
                    }
                    sdf.format(cal.time)
                } catch (_: Exception) { date }
                put("target_reward_date", nextDay)
                put("cutoff_time", "23:45")
                put("timezone", "Asia/Kolkata")
                put("created_at", now)
            }
        }
        writableDatabase.insertWithOnConflict("pc_shutdown_local", null, cv, SQLiteDatabase.CONFLICT_REPLACE)

        enqueueSyncEvent(
            eventType = "PC_SHUTDOWN_PHOTO",
            payload = JSONObject().apply {
                put("date", date)
                put("event_id", eventId)
                put("evidence_id", evidenceId)
                put("photo_submitted_at", now)
                put("account_id", accountId)
            }
        )

        recordActivityJournalEvent(
            eventType = "PC_SHUTDOWN_PHOTO",
            provenance = "EVIDENCE_SUBMITTED",
            date = date,
            activityId = "PC_SHUTDOWN",
            evidenceId = evidenceId,
            category = "PHOTO_EVIDENCE",
            description = "PC shutdown photo submitted showing workstation display off",
            details = JSONObject().apply {
                put("evidence_id", evidenceId)
                put("event_id", eventId)
            }
        )

        return getPcShutdownLocalStatus(date)
    }

    fun getPcShutdownLocalStatus(date: String = nowDate()): JSONObject {
        try {
            readableDatabase.rawQuery(
                """
                SELECT event_id, account_id, device_id, shutdown_timestamp, date, time_hhmmss,
                       cutoff_time, timezone, evidence_source, is_qualifying, disqualification_reason,
                       target_reward_date, phone_attendance_id, phone_attendance_time, phone_device_id,
                       evidence_id, photo_submitted_at, bonus_status, bonus_points, bonus_ledger_event_id,
                       rejection_reason, reviewed_by, reviewed_at
                FROM pc_shutdown_local
                WHERE date = ?
                ORDER BY created_at DESC LIMIT 1
                """.trimIndent(),
                arrayOf(date)
            ).use { c ->
                if (c.moveToFirst()) {
                    val evId = c.getString(0) ?: ""
                    val shutTs = c.getString(3) ?: ""
                    val isQual = c.getInt(9) == 1
                    val phoneAtt = c.getString(12) ?: ""
                    val photoEvId = c.getString(15) ?: ""
                    val bStatus = c.getString(17) ?: "AWAITING_PHONE_ATTENDANCE"
                    val bPoints = c.getInt(18)
                    val ledgerId = c.getString(19) ?: ""
                    val targetDate = c.getString(11) ?: ""

                    return JSONObject().apply {
                        put("date", date)
                        put("account_id", c.getString(1) ?: "user_lok")
                        put("device_id", c.getString(2) ?: "windows_lok_pc")
                        put("event_id", evId)
                        put("shutdown_recorded", shutTs.isNotBlank())
                        put("shutdown_timestamp", shutTs)
                        put("time_hhmmss", c.getString(5) ?: "")
                        put("cutoff_time", c.getString(6) ?: "23:45")
                        put("timezone", c.getString(7) ?: "Asia/Kolkata")
                        put("evidence_source", c.getString(8) ?: "WIN32_API")
                        put("is_qualifying", isQual)
                        put("disqualification_reason", c.getString(10) ?: "")
                        put("target_reward_date", targetDate)
                        put("phone_attendance_completed", phoneAtt.isNotBlank())
                        put("phone_attendance_id", phoneAtt)
                        put("phone_attendance_time", c.getString(13) ?: "")
                        put("phone_device_id", c.getString(14) ?: "")
                        put("photo_submitted", photoEvId.isNotBlank())
                        put("evidence_id", photoEvId)
                        put("photo_submitted_at", c.getString(16) ?: "")
                        put("bonus_status", bStatus)
                        put("bonus_points", bPoints)
                        put("bonus_ledger_event_id", ledgerId)
                        put("rejection_reason", c.getString(20) ?: "")
                        put("reviewed_by", c.getString(21) ?: "")
                        put("reviewed_at", c.getString(22) ?: "")
                        put("eligible_for_bonus", shutTs.isNotBlank() && isQual && phoneAtt.isNotBlank() && photoEvId.isNotBlank())
                    }
                }
            }
        } catch (_: Exception) {}

        val nextDay = try {
            val sdf = java.text.SimpleDateFormat("yyyy-MM-dd", Locale.US)
            val cal = Calendar.getInstance().apply {
                time = sdf.parse(date) ?: java.util.Date()
                add(Calendar.DAY_OF_YEAR, 1)
            }
            sdf.format(cal.time)
        } catch (_: Exception) { date }

        return JSONObject().apply {
            put("date", date)
            put("account_id", "user_lok")
            put("device_id", "windows_lok_pc")
            put("event_id", "")
            put("shutdown_recorded", false)
            put("shutdown_timestamp", "")
            put("time_hhmmss", "")
            put("cutoff_time", "23:45")
            put("timezone", "Asia/Kolkata")
            put("evidence_source", "WIN32_API")
            put("is_qualifying", false)
            put("disqualification_reason", "")
            put("target_reward_date", nextDay)
            put("phone_attendance_completed", false)
            put("phone_attendance_id", "")
            put("phone_attendance_time", "")
            put("phone_device_id", "")
            put("photo_submitted", false)
            put("evidence_id", "")
            put("photo_submitted_at", "")
            put("bonus_status", "AWAITING_WINDOWS_SHUTDOWN")
            put("bonus_points", 15)
            put("bonus_ledger_event_id", "")
            put("rejection_reason", "")
            put("reviewed_by", "")
            put("reviewed_at", "")
            put("eligible_for_bonus", false)
        }
    }

    fun awardPcShutdownBonusLocally(
        date: String = nowDate(),
        adminId: String = "ADMIN",
        decision: String = "APPROVE",
        reason: String = "PC shutdown verified before 11:45 PM + phone attendance & photo verified"
    ): JSONObject {
        val now = nowIso()
        val cur = getPcShutdownLocalStatus(date)
        val eventId = cur.optString("event_id")
        if (eventId.isBlank()) {
            return JSONObject().apply {
                put("success", false)
                put("error", "No PC shutdown record found for $date")
                put("code", "NOT_FOUND")
            }
        }

        if (cur.optString("bonus_status") == "BONUS_CREDITED" && cur.optString("bonus_ledger_event_id").isNotBlank()) {
            return JSONObject().apply {
                put("success", true)
                put("already_credited", true)
                put("event_id", eventId)
                put("bonus_status", "BONUS_CREDITED")
                put("target_reward_date", cur.optString("target_reward_date"))
                put("bonus_points", cur.optInt("bonus_points", 15))
            }
        }

        if (decision.uppercase(Locale.US) == "REJECT") {
            val cv = ContentValues().apply {
                put("bonus_status", "REJECTED")
                put("rejection_reason", reason)
                put("reviewed_by", adminId)
                put("reviewed_at", now)
                put("updated_at", now)
            }
            writableDatabase.update("pc_shutdown_local", cv, "event_id = ?", arrayOf(eventId))
            return JSONObject().apply {
                put("success", true)
                put("decision", "REJECTED")
                put("event_id", eventId)
                put("bonus_status", "REJECTED")
            }
        }

        if (!cur.optBoolean("shutdown_recorded", false) || !cur.optBoolean("is_qualifying", false)) {
            val disq = cur.optString("disqualification_reason", "Shutdown event was at or after 11:45 PM cutoff or not recorded.")
            return JSONObject().apply {
                put("success", false)
                put("eligible", false)
                put("reason", disq)
                put("code", "INELIGIBLE")
            }
        }

        if (!cur.optBoolean("phone_attendance_completed", false)) {
            return JSONObject().apply {
                put("success", false)
                put("eligible", false)
                put("reason", "Phone attendance confirmation is required from registered Android phone.")
                put("code", "MISSING_PHONE_ATTENDANCE")
            }
        }

        if (!cur.optBoolean("photo_submitted", false)) {
            return JSONObject().apply {
                put("success", false)
                put("eligible", false)
                put("reason", "Workstation shutdown photograph evidence is required.")
                put("code", "MISSING_SHUTDOWN_PHOTO")
            }
        }

        val targetRewardDate = cur.optString("target_reward_date").ifBlank {
            try {
                val sdf = java.text.SimpleDateFormat("yyyy-MM-dd", Locale.US)
                val cal = Calendar.getInstance().apply {
                    time = sdf.parse(date) ?: java.util.Date()
                    add(Calendar.DAY_OF_YEAR, 1)
                }
                sdf.format(cal.time)
            } catch (_: Exception) { date }
        }

        val bonusPts = cur.optInt("bonus_points", 15)
        val ledgerId = "se_sd_" + eventId

        addScoreEvent(
            date = targetRewardDate,
            isDevMode = false,
            eventType = "PC_SHUTDOWN_BONUS",
            description = "Windows PC Shutdown Bonus ($date) - Credited to $targetRewardDate",
            points = bonusPts,
            audit = JSONObject().apply {
                put("Rule", "PC Shutdown before 11:45 PM + Phone Attendance + Photo Proof")
                put("ShutdownDate", date)
                put("TargetRewardDate", targetRewardDate)
                put("EventId", eventId)
                put("Admin", adminId)
                put("Reason", reason)
            }
        )

        val cv = ContentValues().apply {
            put("bonus_status", "BONUS_CREDITED")
            put("bonus_ledger_event_id", ledgerId)
            put("reviewed_by", adminId)
            put("reviewed_at", now)
            put("updated_at", now)
        }
        writableDatabase.update("pc_shutdown_local", cv, "event_id = ?", arrayOf(eventId))

        return JSONObject().apply {
            put("success", true)
            put("event_id", eventId)
            put("bonus_status", "BONUS_CREDITED")
            put("decision", "APPROVED")
            put("points_awarded", bonusPts)
            put("target_reward_date", targetRewardDate)
            put("bonus_ledger_event_id", ledgerId)
        }
    }

    fun getSleepSubmission(reportDate: String, isDevMode: Boolean): JSONObject? {
        val dev = if (isDevMode) 1 else 0
        return readableDatabase.rawQuery(
            "SELECT submission_id, report_date, status, sleep_onset, wake_up, duration_text, stages_json, sleep_score, points_awarded, bonus_ledger_event_id, image_sha256, file_path, analysis_json, rejection_reason, created_at, accepted_at FROM sleep_data_submissions WHERE report_date = ? AND is_dev_mode = ? ORDER BY accepted_at DESC LIMIT 1",
            arrayOf(reportDate, dev.toString())
        ).use { cursor ->
            if (!cursor.moveToFirst()) null
            else JSONObject().apply {
                put("submission_id", cursor.getString(0))
                put("report_date", cursor.getString(1))
                put("status", cursor.getString(2))
                put("sleep_onset", cursor.getString(3))
                put("wake_up", cursor.getString(4))
                put("duration_text", cursor.getString(5))
                put("stages_json", cursor.getString(6))
                if (cursor.isNull(7)) put("sleep_score", JSONObject.NULL) else put("sleep_score", cursor.getInt(7))
                put("points_awarded", cursor.getInt(8))
                put("bonus_ledger_event_id", cursor.getString(9))
                put("image_sha256", cursor.getString(10))
                put("file_path", cursor.getString(11))
                put("analysis_json", cursor.getString(12))
                put("rejection_reason", cursor.getString(13))
                put("created_at", cursor.getString(14))
                put("accepted_at", cursor.getString(15))
            }
        }
    }

    fun hasSleepBonusBeenAwarded(reportDate: String, isDevMode: Boolean): Boolean {
        val dev = if (isDevMode) 1 else 0
        return readableDatabase.rawQuery(
            "SELECT 1 FROM sleep_data_submissions WHERE report_date = ? AND is_dev_mode = ? AND status IN ('APPROVED', 'PARTIALLY_AWARDED') AND points_awarded > 0",
            arrayOf(reportDate, dev.toString())
        ).use { it.moveToFirst() }
    }

    fun isDuplicateSleepProof(sha256: String, excludeSubmissionId: String = ""): Boolean {
        if (sha256.isBlank()) return false
        val exclude = excludeSubmissionId.trim()
        try {
            val inSleep = readableDatabase.rawQuery(
                "SELECT 1 FROM sleep_data_submissions WHERE image_sha256 = ? AND (submission_id != ? OR ? = '') LIMIT 1",
                arrayOf(sha256, exclude, exclude)
            ).use { it.moveToFirst() }
            if (inSleep) return true
        } catch (_: Exception) {}

        try {
            val inEvidence = readableDatabase.rawQuery(
                "SELECT 1 FROM evidence_fingerprints WHERE sha256_hash = ? AND (submission_id != ? OR ? = '') LIMIT 1",
                arrayOf(sha256, exclude, exclude)
            ).use { it.moveToFirst() }
            if (inEvidence) return true
        } catch (_: Exception) {}

        try {
            if (hasColumn(readableDatabase, "proof_reviews", "sha256_hash")) {
                val inReviews = readableDatabase.rawQuery(
                    "SELECT 1 FROM proof_reviews WHERE sha256_hash = ? AND (submission_id != ? OR ? = '') LIMIT 1",
                    arrayOf(sha256, exclude, exclude)
                ).use { it.moveToFirst() }
                if (inReviews) return true
            }
        } catch (_: Exception) {}

        return false
    }

    fun hasGymPointsBeenAwarded(activityId: String, date: String): Boolean {
        return try {
            val byAct = if (activityId.isNotBlank()) {
                readableDatabase.rawQuery(
                    "SELECT 1 FROM score_events WHERE (event_type IN ('PROOF_APPROVED', 'TASK_VERIFIED', 'GYM_COMPLETED')) AND points > 0 AND audit_json LIKE ? LIMIT 1",
                    arrayOf("%$activityId%")
                ).use { it.moveToFirst() }
            } else false
            byAct || readableDatabase.rawQuery(
                "SELECT 1 FROM score_events WHERE date = ? AND (event_type = 'GYM_COMPLETED' OR (event_type = 'PROOF_APPROVED' AND audit_json LIKE '%GYM%')) AND points > 0 LIMIT 1",
                arrayOf(date)
            ).use { it.moveToFirst() }
        } catch (_: Exception) { false }
    }

    fun isDuplicateGymProof(sha256: String, excludeEvidenceId: String = ""): Boolean {
        if (sha256.isBlank()) return false
        return try {
            readableDatabase.rawQuery(
                "SELECT 1 FROM evidence_fingerprints WHERE sha256_hash = ? AND evidence_id != ? LIMIT 1",
                arrayOf(sha256, excludeEvidenceId)
            ).use { it.moveToFirst() }
        } catch (_: Exception) { false }
    }

    fun getGymWorkoutEvaluation(activityId: String, date: String = nowDate()): JSONObject? {
        val eval = getProofEvaluationByActivity(activityId, date)
        val raw = eval?.optString("evaluator_output_json") ?: ""
        if (raw.isNotBlank() && raw != "{}") {
            return runCatching { JSONObject(raw) }.getOrNull()
        }
        val act = getDailyActivities(date).firstOrNull { it.activityId == activityId || it.type == "GYM" }
        val det = act?.detailsJson ?: "{}"
        val detObj = runCatching { JSONObject(det) }.getOrNull()
        return detObj?.optJSONObject("workout_evaluation")
    }

    fun awardSleepBonus(
        submissionId: String,
        reportDate: String,
        isDevMode: Boolean,
        parsedData: MiFitnessSleepBonusRules.ParsedSleepData,
        evaluation: MiFitnessSleepBonusRules.EvaluationResult,
        imageSha256: String,
        filePath: String,
        acceptedAtMs: Long,
        analysisJson: JSONObject = JSONObject()
    ): JSONObject {
        val dev = if (isDevMode) 1 else 0
        val now = nowIso()
        val acceptedAtIso = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).apply {
            timeZone = TimeZone.getTimeZone(scheduleTimezone())
        }.format(Date(acceptedAtMs))

        val bonusPts = evaluation.points
        var effectivePoints = bonusPts
        var effectiveLedgerId = ""

        if (evaluation.status == "ALREADY_AWARDED") {
            try {
                readableDatabase.rawQuery(
                    "SELECT points_awarded, bonus_ledger_event_id FROM sleep_data_submissions WHERE report_date = ? AND is_dev_mode = ? AND points_awarded > 0 ORDER BY accepted_at DESC LIMIT 1",
                    arrayOf(reportDate, dev.toString())
                ).use { cursor ->
                    if (cursor.moveToFirst()) {
                        effectivePoints = cursor.getInt(0)
                        effectiveLedgerId = cursor.getString(1) ?: ""
                    }
                }
            } catch (_: Exception) {}
        } else if (bonusPts > 0) {
            val ledgerId = "se_sleep_" + UUID.randomUUID().toString().replace("-", "").take(12)
            effectiveLedgerId = ledgerId
            val onsetStr = parsedData.sleepOnset?.formatted12h ?: "--"
            val wakeStr = parsedData.wakeUp?.formatted12h ?: "--"
            val desc = if (evaluation.status == "APPROVED") {
                "Mi Fitness Sleep Bonus: 5 pts (Onset $onsetStr, Wake $wakeStr)"
            } else {
                "Mi Fitness Sleep Bonus: $bonusPts pts (Partial - Onset $onsetStr, Wake $wakeStr)"
            }

            addScoreEvent(
                date = reportDate,
                isDevMode = isDevMode,
                eventType = "MI_FITNESS_SLEEP_BONUS",
                description = desc,
                points = bonusPts,
                audit = JSONObject().apply {
                    put("evidence_id", "ev_sleep_" + submissionId.take(16))
                    put("Rule", "Mi Fitness Smartwatch Sleep Report")
                    put("ReportDate", reportDate)
                    put("SleepOnset", onsetStr)
                    put("WakeUp", wakeStr)
                    put("LateHourBands", evaluation.lateHourBands)
                    put("EvidenceSha256", imageSha256)
                    put("SubmissionId", submissionId)
                    put("AcceptedAt", acceptedAtIso)
                    put("Reason", evaluation.reason)
                }
            )

            // Register proof retained
            try {
                val cvReview = ContentValues().apply {
                    put("evidence_id", "ev_sleep_" + submissionId.take(16))
                    put("submission_id", submissionId)
                    put("date", reportDate)
                    put("activity_id", "act_sleep_$reportDate")
                    put("task_code", "SLEEP_REPORT")
                    put("task_title", "Mi Fitness Sleep Report ($reportDate)")
                    put("status", "APPROVED")
                    put("decision", "APPROVED")
                    put("points_awarded", bonusPts)
                    put("file_path", filePath)
                    put("sha256_hash", imageSha256)
                    put("submitted_at", acceptedAtIso)
                    put("reviewed_at", now)
                    put("reviewed_by", "MiFitnessSleepValidator")
                    put("audit_json", JSONObject().apply {
                        put("onset", onsetStr)
                        put("wake", wakeStr)
                        put("duration", parsedData.durationText ?: "")
                        put("stages", JSONObject(parsedData.stages))
                    }.toString())
                }
                writableDatabase.insertWithOnConflict("proof_reviews", null, cvReview, SQLiteDatabase.CONFLICT_REPLACE)
            } catch (_: Exception) {}

            // Register proof fingerprint for deduplication
            try {
                val cvFp = ContentValues().apply {
                    put("evidence_id", "ev_sleep_" + submissionId.take(16))
                    put("submission_id", submissionId)
                    put("activity_id", "act_sleep_$reportDate")
                    put("date", reportDate)
                    put("sha256_hash", imageSha256)
                    put("file_size_bytes", File(filePath).let { if (it.exists()) it.length() else 0L })
                    put("ocr_status", "OCR_COMPLETED")
                    put("duplicate_status", "NO_MATCH_FOUND")
                    put("created_at", now)
                }
                writableDatabase.insertWithOnConflict("evidence_fingerprints", null, cvFp, SQLiteDatabase.CONFLICT_REPLACE)
            } catch (_: Exception) {}

            // Associate with wake-up activity as supporting evidence WITHOUT marking wake-up steps as passed
            try {
                val wakeOccurrence = getDailyActivities(reportDate).firstOrNull { it.type == "WAKE_UP" || it.activityId.contains("WAKEUP") }
                if (wakeOccurrence != null) {
                    val details = JSONObject(wakeOccurrence.detailsJson)
                    details.put("supporting_sleep_evidence_id", "ev_sleep_" + submissionId.take(16))
                    details.put("supporting_sleep_onset", onsetStr)
                    details.put("supporting_sleep_wake", wakeStr)
                    val cvAct = ContentValues().apply {
                        put("details_json", details.toString())
                    }
                    writableDatabase.update("scheduled_activities", cvAct, "activity_id = ?", arrayOf(wakeOccurrence.activityId))
                }
            } catch (_: Exception) {}
        }

        // 2. Insert into sleep_data_submissions
        val cvSub = ContentValues().apply {
            put("submission_id", submissionId)
            put("report_date", reportDate)
            put("account_id", "user_lok")
            put("is_dev_mode", dev)
            put("created_at", now)
            put("accepted_at", acceptedAtIso)
            put("status", evaluation.status)
            put("sleep_onset", parsedData.sleepOnset?.formatted12h ?: "")
            put("wake_up", parsedData.wakeUp?.formatted12h ?: "")
            put("duration_text", parsedData.durationText ?: "")
            put("stages_json", JSONObject(parsedData.stages).toString())
            if (parsedData.sleepScore != null && parsedData.sleepScore > 0) {
                put("sleep_score", parsedData.sleepScore)
            } else {
                putNull("sleep_score")
            }
            put("points_awarded", effectivePoints)
            put("bonus_ledger_event_id", effectiveLedgerId)
            put("image_sha256", imageSha256)
            put("file_path", filePath)
            put("analysis_json", analysisJson.toString())
            put("rejection_reason", if (!evaluation.eligible && evaluation.status != "ALREADY_AWARDED") evaluation.reason else "")
        }
        writableDatabase.insertWithOnConflict("sleep_data_submissions", null, cvSub, SQLiteDatabase.CONFLICT_REPLACE)

        return JSONObject().apply {
            put("success", evaluation.eligible || evaluation.status == "ALREADY_AWARDED")
            put("submission_id", submissionId)
            put("status", evaluation.status)
            put("points_awarded", effectivePoints)
            put("reason", evaluation.reason)
            put("report_date", reportDate)
            put("bonus_ledger_event_id", effectiveLedgerId)
        }
    }

    // =========================================================================
    // Cloud Sync Engine Methods (Google Drive)
    // =========================================================================

    fun upsertCloudSyncFile(
        fileId: String,
        localPath: String,
        category: String,
        fileSizeBytes: Long = 0L,
        sha256Hash: String = "",
        md5Hash: String = "",
        mimeType: String = "",
        syncStatus: String = "PENDING",
        errorMessage: String? = null,
        deviceId: String = "android_phone"
    ) {
        val nowIso = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US).format(java.util.Date())
        val cv = android.content.ContentValues().apply {
            put("file_id", fileId)
            put("local_path", localPath)
            put("category", category)
            put("file_size_bytes", fileSizeBytes)
            put("sha256_hash", sha256Hash)
            if (md5Hash.isNotEmpty()) put("md5_hash", md5Hash)
            if (mimeType.isNotEmpty()) put("mime_type", mimeType)
            put("sync_status", syncStatus)
            if (errorMessage != null) put("error_message", errorMessage)
            put("device_id", deviceId)
            put("created_at", nowIso)
        }
        writableDatabase.insertWithOnConflict("cloud_sync_files", null, cv, android.database.sqlite.SQLiteDatabase.CONFLICT_REPLACE)
    }

    fun updateCloudSyncFileStatus(
        fileId: String,
        syncStatus: String,
        driveFileId: String? = null,
        errorMessage: String? = null
    ) {
        val nowIso = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US).format(java.util.Date())
        val cv = android.content.ContentValues().apply {
            put("sync_status", syncStatus)
            if (driveFileId != null) put("drive_file_id", driveFileId)
            if (syncStatus == "SYNCED") {
                putNull("error_message")
                put("last_synced_at", nowIso)
            } else if (errorMessage != null) {
                put("error_message", errorMessage)
            }
        }
        writableDatabase.update("cloud_sync_files", cv, "file_id = ?", arrayOf(fileId))
    }

    fun enqueueCloudSyncTask(fileId: String, action: String = "UPLOAD"): String {
        val db = writableDatabase
        val cursor = db.rawQuery(
            "SELECT queue_id FROM cloud_sync_queue WHERE file_id = ? AND action = ? AND status IN ('QUEUED', 'IN_PROGRESS')",
            arrayOf(fileId, action)
        )
        if (cursor.moveToFirst()) {
            val qid = cursor.getString(0)
            cursor.close()
            return qid
        }
        cursor.close()

        val qid = "cq_" + java.util.UUID.randomUUID().toString().replace("-", "").take(12)
        val nowIso = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US).format(java.util.Date())
        val cv = android.content.ContentValues().apply {
            put("queue_id", qid)
            put("file_id", fileId)
            put("action", action)
            put("status", "QUEUED")
            put("retry_count", 0)
            put("created_at", nowIso)
            put("updated_at", nowIso)
        }
        db.insert("cloud_sync_queue", null, cv)
        return qid
    }

    fun getPendingCloudSyncQueue(limit: Int = 10): List<Map<String, Any>> {
        val list = mutableListOf<Map<String, Any>>()
        val nowIso = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US).format(java.util.Date())
        val cursor = readableDatabase.rawQuery(
            """
            SELECT q.queue_id, q.file_id, q.action, q.status, q.retry_count,
                   f.local_path, f.category, f.drive_file_id, f.file_size_bytes,
                   f.sha256_hash, f.mime_type
            FROM cloud_sync_queue q
            JOIN cloud_sync_files f ON q.file_id = f.file_id
            WHERE q.status = 'QUEUED'
              AND (q.next_retry_at IS NULL OR q.next_retry_at <= ?)
            ORDER BY q.created_at ASC
            LIMIT ?
            """.trimIndent(),
            arrayOf(nowIso, limit.toString())
        )
        while (cursor.moveToNext()) {
            list.add(
                mapOf(
                    "queue_id" to cursor.getString(0),
                    "file_id" to cursor.getString(1),
                    "action" to cursor.getString(2),
                    "status" to cursor.getString(3),
                    "retry_count" to cursor.getInt(4),
                    "local_path" to cursor.getString(5),
                    "category" to cursor.getString(6),
                    "drive_file_id" to (cursor.getString(7) ?: ""),
                    "file_size_bytes" to cursor.getLong(8),
                    "sha256_hash" to (cursor.getString(9) ?: ""),
                    "mime_type" to (cursor.getString(10) ?: "")
                )
            )
        }
        cursor.close()
        return list
    }

    fun updateCloudSyncQueueStatus(
        queueId: String,
        status: String,
        retryCount: Int? = null,
        nextRetryAt: String? = null
    ) {
        val nowIso = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US).format(java.util.Date())
        val cv = android.content.ContentValues().apply {
            put("status", status)
            put("updated_at", nowIso)
            if (retryCount != null) put("retry_count", retryCount)
            if (nextRetryAt != null) put("next_retry_at", nextRetryAt)
        }
        writableDatabase.update("cloud_sync_queue", cv, "queue_id = ?", arrayOf(queueId))
    }

    fun getCloudSyncSummary(): org.json.JSONObject {
        val cursor = readableDatabase.rawQuery(
            """
            SELECT
                COUNT(*) as total_files,
                SUM(CASE WHEN sync_status = 'SYNCED' THEN 1 ELSE 0 END) as synced_count,
                SUM(CASE WHEN sync_status IN ('PENDING', 'UPLOADING') THEN 1 ELSE 0 END) as pending_count,
                SUM(CASE WHEN sync_status = 'FAILED' THEN 1 ELSE 0 END) as failed_count,
                MAX(last_synced_at) as last_synced_at
            FROM cloud_sync_files
            """.trimIndent(),
            null
        )
        val result = org.json.JSONObject()
        if (cursor.moveToFirst()) {
            result.put("total_files", cursor.getInt(0))
            result.put("synced_count", cursor.getInt(1))
            result.put("pending_count", cursor.getInt(2))
            result.put("failed_count", cursor.getInt(3))
            result.put("last_synced_at", cursor.getString(4) ?: "")
        } else {
            result.put("total_files", 0)
            result.put("synced_count", 0)
            result.put("pending_count", 0)
            result.put("failed_count", 0)
            result.put("last_synced_at", "")
        }
        cursor.close()
        return result
    }

    fun getUnsyncedScoreEvents(limit: Int = 50): List<org.json.JSONObject> {
        val list = mutableListOf<org.json.JSONObject>()
        val cursor = readableDatabase.rawQuery(
            """
            SELECT se.event_id, se.date, se.is_dev_mode, se.timestamp, se.event_type,
                   se.device_id, se.description, se.points, se.rule_version,
                   se.parent_event_id, se.evidence_id, se.related_task_id, se.audit_json
            FROM score_events se
            LEFT JOIN cloud_sync_files csf ON csf.file_id = ('ledger_ev_' || se.event_id)
            WHERE csf.file_id IS NULL OR csf.sync_status != 'SYNCED'
            ORDER BY se.timestamp DESC
            LIMIT ?
            """.trimIndent(),
            arrayOf(limit.toString())
        )
        while (cursor.moveToNext()) {
            val obj = org.json.JSONObject().apply {
                put("schema_version", 1)
                put("event_id", cursor.getString(0) ?: "")
                put("date", cursor.getString(1) ?: "")
                put("is_dev_mode", cursor.getInt(2))
                put("timestamp", cursor.getString(3) ?: "")
                put("event_type", cursor.getString(4) ?: "")
                put("device_id", cursor.getString(5) ?: "")
                put("description", cursor.getString(6) ?: "")
                put("points", cursor.getInt(7))
                put("rule_version", cursor.getInt(8))
                put("parent_event_id", cursor.getString(9) ?: "")
                put("evidence_id", cursor.getString(10) ?: "")
                put("related_task_id", cursor.getString(11) ?: "")
                put("audit_json", cursor.getString(12) ?: "{}")
            }
            list.add(obj)
        }
        cursor.close()
        return list
    }

    fun insertScoreEventFromCloud(event: org.json.JSONObject): Boolean {
        val eid = event.optString("event_id")
        if (eid.isEmpty()) return false
        val cv = android.content.ContentValues().apply {
            put("event_id", eid)
            put("date", event.optString("date"))
            put("is_dev_mode", event.optInt("is_dev_mode", 0))
            put("timestamp", event.optString("timestamp"))
            put("event_type", event.optString("event_type"))
            put("device_id", event.optString("device_id", "remote"))
            put("description", event.optString("description"))
            put("points", event.optInt("points", 0))
            put("rule_version", event.optInt("rule_version", 1))
            put("parent_event_id", event.optString("parent_event_id", null))
            put("evidence_id", event.optString("evidence_id", null))
            put("related_task_id", event.optString("related_task_id", null))
            put("audit_json", event.optString("audit_json", "{}"))
        }
        val rowId = writableDatabase.insertWithOnConflict("score_events", null, cv, android.database.sqlite.SQLiteDatabase.CONFLICT_IGNORE)
        return rowId != -1L
    }

    fun resetFailedCloudSyncItems() {
        writableDatabase.execSQL("UPDATE cloud_sync_files SET sync_status='PENDING', error_message=NULL WHERE sync_status='FAILED'")
        writableDatabase.execSQL("UPDATE cloud_sync_queue SET status='QUEUED', retry_count=0, next_retry_at=NULL WHERE status='FAILED'")
    }

    fun getScheduledActivitiesForDate(dateStr: String): List<JSONObject> {
        val list = mutableListOf<JSONObject>()
        try {
            readableDatabase.rawQuery(
                "SELECT activity_id, type, date, title, scheduled_time, window_start, window_end, validation_status, points, details_json FROM scheduled_activities WHERE date = ?",
                arrayOf(dateStr)
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    val obj = JSONObject().apply {
                        put("activity_id", cursor.getString(0) ?: "")
                        put("task_type", cursor.getString(1) ?: "HOURLY")
                        put("category", cursor.getString(1) ?: "STUDY")
                        put("date", cursor.getString(2) ?: dateStr)
                        put("title", cursor.getString(3) ?: "")
                        put("start_time", cursor.getString(5) ?: "")
                        put("end_time", cursor.getString(6) ?: "")
                        put("validation_status", cursor.getString(7) ?: "PENDING")
                        put("points", cursor.getInt(8))
                    }
                    list.add(obj)
                }
            }
        } catch (_: Throwable) {}
        return list
    }

    fun getDeviceId(): String {
        return getDeviceRegistration()?.optString("device_id", "")?.ifEmpty { "android_lok_phone" } ?: "android_lok_phone"
    }

    fun getEarlySleepReward(wakeDate: String): JSONObject? {
        return try {
            readableDatabase.rawQuery(
                "SELECT reward_id, wake_date, status, sleep_onset, wake_up, data_source, video_id, video_url, video_title, duration_seconds, elapsed_seconds, completed_at, created_at FROM early_sleep_video_rewards WHERE wake_date = ? LIMIT 1",
                arrayOf(wakeDate)
            ).use { cursor ->
                if (!cursor.moveToFirst()) null
                else JSONObject().apply {
                    put("reward_id", cursor.getString(0))
                    put("wake_date", cursor.getString(1))
                    put("status", cursor.getString(2))
                    put("sleep_onset", cursor.getString(3) ?: "")
                    put("wake_up", cursor.getString(4) ?: "")
                    put("data_source", cursor.getString(5) ?: "")
                    put("video_id", cursor.getString(6) ?: "")
                    put("video_url", cursor.getString(7) ?: "")
                    put("video_title", cursor.getString(8) ?: "")
                    put("duration_seconds", cursor.getDouble(9))
                    put("elapsed_seconds", cursor.getDouble(10))
                    put("completed_at", cursor.getLong(11))
                    put("created_at", cursor.getLong(12))
                }
            }
        } catch (_: Throwable) {
            null
        }
    }

    fun saveEarlySleepReward(record: JSONObject): Boolean {
        return try {
            val cv = android.content.ContentValues().apply {
                put("reward_id", record.optString("reward_id"))
                put("wake_date", record.optString("wake_date"))
                put("status", record.optString("status"))
                put("sleep_onset", record.optString("sleep_onset"))
                put("wake_up", record.optString("wake_up"))
                put("data_source", record.optString("data_source"))
                put("video_id", record.optString("video_id"))
                put("video_url", record.optString("video_url"))
                put("video_title", record.optString("video_title"))
                put("duration_seconds", record.optDouble("duration_seconds", 0.0))
                put("elapsed_seconds", record.optDouble("elapsed_seconds", 0.0))
                put("completed_at", record.optLong("completed_at", 0L))
                put("created_at", record.optLong("created_at", System.currentTimeMillis()))
            }
            writableDatabase.insertWithOnConflict("early_sleep_video_rewards", null, cv, android.database.sqlite.SQLiteDatabase.CONFLICT_REPLACE) >= 0
        } catch (_: Throwable) {
            false
        }
    }

    fun evaluateAndSyncEarlySleepReward(wakeDate: String, isDevMode: Boolean): JSONObject {
        val existing = getEarlySleepReward(wakeDate)
        if (existing != null && existing.optString("status") in listOf("REWARD_CONSUMED", "PLAYING", "VIDEO_SELECTED")) {
            return existing
        }
        val sub = getSleepSubmission(wakeDate, isDevMode)
        if (sub == null || sub.optString("status") !in listOf("APPROVED", "PARTIALLY_AWARDED")) {
            val pending = JSONObject().apply {
                put("wake_date", wakeDate)
                put("status", "SLEEP_DATA_PENDING")
                put("eligible", false)
                put("reason", "Sleep data is pending. Connect Mi Fitness or submit sleep report.")
            }
            return existing ?: pending
        }
        val onsetStr = sub.optString("sleep_onset", "")
        val wakeStr = sub.optString("wake_up", "")
        val onsetClock = MiFitnessSleepBonusRules.parseTimeString(onsetStr)
        val wakeClock = MiFitnessSleepBonusRules.parseTimeString(wakeStr)
        val res = MiFitnessSleepBonusRules.evaluateEarlySleepRewardEligibility(onsetClock, wakeClock, wakeDate)
        if (!res.eligible) {
            val ineligible = JSONObject().apply {
                put("reward_id", "ineligible_$wakeDate")
                put("wake_date", wakeDate)
                put("status", "NOT_ELIGIBLE")
                put("sleep_onset", onsetStr)
                put("wake_up", wakeStr)
                put("data_source", "MI_FITNESS")
                put("eligible", false)
                put("reason", res.reason)
            }
            return ineligible
        }
        val reward = existing ?: JSONObject().apply {
            put("reward_id", "early_sleep_${wakeDate}_${java.util.UUID.randomUUID().toString().substring(0, 8)}")
            put("wake_date", wakeDate)
            put("status", "ELIGIBLE")
            put("sleep_onset", onsetStr)
            put("wake_up", wakeStr)
            put("data_source", "MI_FITNESS")
            put("video_id", "")
            put("video_url", "")
            put("video_title", "")
            put("duration_seconds", 0.0)
            put("elapsed_seconds", 0.0)
            put("completed_at", 0L)
            put("created_at", System.currentTimeMillis())
        }
        saveEarlySleepReward(reward)
        reward.put("eligible", true)
        reward.put("reason", res.reason)
        return reward
    }
}
