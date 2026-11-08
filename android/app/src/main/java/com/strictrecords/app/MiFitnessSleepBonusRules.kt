package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import kotlin.math.ceil
import kotlin.math.max

/**
 * Pure deterministic scoring, deadline, and policy rules for Mi Fitness sleep bonus.
 * Shared across Android application, background managers, and JVM unit tests.
 */
object MiFitnessSleepBonusRules {
    const val MAX_BONUS_POINTS = 5
    const val DEADLINE_HOUR = 12 // 12:00 PM (noon)
    const val DEADLINE_MINUTE = 0
    const val WAKE_CUTOFF_HOUR = 9 // 09:30 AM
    const val WAKE_CUTOFF_MINUTE = 30
    const val WAKE_CUTOFF_TOTAL_MINUTES = 9 * 60 + 30 // 570
    const val TRANSACTION_REASON = "MI_FITNESS_SLEEP_BONUS"

    data class ClockTime(
        val hour24: Int,
        val minute: Int,
        val isPreviousDay: Boolean = false,
        val rawText: String = ""
    ) {
        val formatted12h: String
            get() {
                val h12 = if (hour24 % 12 == 0) 12 else hour24 % 12
                val amPm = if (hour24 < 12) "AM" else "PM"
                return String.format(Locale.US, "%d:%02d %s", h12, minute, amPm)
            }

        val formatted24h: String
            get() = String.format(Locale.US, "%02d:%02d", hour24, minute)

        val totalMinutesSinceMidnight: Int
            get() = hour24 * 60 + minute
    }

    data class EvaluationResult(
        val eligible: Boolean,
        val points: Int,
        val status: String,
        val reason: String,
        val lateHourBands: Int = 0,
        val deadlinePassed: Boolean = false,
        val wakeUpPastCutoff: Boolean = false,
        val dateMismatch: Boolean = false,
        val onTimePoints: Int = 0
    )

    data class ParsedSleepData(
        val reportDate: String?, // Normalized ISO YYYY-MM-DD
        val sleepOnset: ClockTime?,
        val wakeUp: ClockTime?,
        val durationMinutes: Int? = null,
        val durationText: String? = null,
        val stages: Map<String, String> = emptyMap(),
        val sleepScore: Int? = null,
        val rawOcrText: String = "",
        val isAmbiguous: Boolean = false,
        val ambiguityReason: String = ""
    )

    /**
     * Resolves a parsed time into ClockTime, detecting whether sleep onset occurred on the
     * previous day (before midnight) or on the report date (after midnight).
     */
    fun resolveClockTime(hour12or24: Int, minute: Int, isPm: Boolean?, rawText: String = ""): ClockTime {
        val hour24: Int
        val isPrevDay: Boolean

        if (isPm != null) {
            hour24 = when {
                isPm && hour12or24 < 12 -> hour12or24 + 12
                !isPm && hour12or24 == 12 -> 0
                else -> hour12or24
            }
            // Times from 12:00 PM to 11:59 PM (or >= 18:00) represent sleep beginning before midnight
            isPrevDay = isPm || hour24 >= 18
        } else {
            hour24 = hour12or24
            isPrevDay = hour24 >= 18
        }

        return ClockTime(
            hour24 = hour24,
            minute = minute,
            isPreviousDay = isPrevDay,
            rawText = rawText
        )
    }

    /**
     * Parses time strings like "11:30 PM", "12:14 AM", "23:30", "07:00" into a ClockTime instance.
     */
    fun parseTimeString(raw: String): ClockTime? {
        val s = raw.trim().uppercase()
        if (s.isBlank()) return null
        val isPm = if (s.contains("PM")) true else if (s.contains("AM")) false else null
        val clean = s.replace("AM", "").replace("PM", "").trim()
        val parts = clean.split(":")
        if (parts.size < 2) return null
        val h = parts[0].toIntOrNull() ?: return null
        val m = parts[1].toIntOrNull() ?: return null
        return resolveClockTime(h, m, isPm, raw)
    }

    /**
     * Calculates the number of late-hour bands entered after midnight.
     * Rule:
     * - Determine the number of minutes elapsed since midnight at sleep onset.
     * - Calculate the number of late-hour bands as the greater of 1 or the ceiling of elapsed minutes divided by 60.
     * - Sleep bonus = maximum of zero and 5 minus the late-hour bands.
     */
    fun calculateLateHourBands(elapsedMinutes: Int): Int {
        if (elapsedMinutes <= 0) return 1
        return maxOf(1, ceil(elapsedMinutes.toDouble() / 60.0).toInt())
    }

    /**
     * Calculates bonus points from late-hour bands: max(0, 5 - lateHourBands).
     */
    fun calculateLateBonusPoints(lateHourBands: Int): Int {
        return maxOf(0, MAX_BONUS_POINTS - lateHourBands)
    }

    /**
     * Validates whether the report date extracted from screenshot matches claimed report date.
     */
    fun validateReportDate(extractedDate: String?, claimedDate: String): Boolean {
        if (extractedDate.isNullOrBlank()) return false
        val normExtracted = normalizeDateToIso(extractedDate) ?: return false
        val normClaimed = normalizeDateToIso(claimedDate) ?: claimedDate
        return normExtracted == normClaimed
    }

    /**
     * Normalizes dates (MM/DD/YYYY, YYYY-MM-DD, or Month Day Year) to ISO YYYY-MM-DD.
     * Strictly avoids swapping month and day.
     */
    fun normalizeDateToIso(rawDate: String): String? {
        val s = rawDate.trim()
        if (s.matches(Regex("^\\d{4}-\\d{2}-\\d{2}$"))) {
            val p = s.split("-")
            val y = p[0].toIntOrNull() ?: return null
            val m = p[1].toIntOrNull() ?: return null
            val d = p[2].toIntOrNull() ?: return null
            if (m in 1..12 && d in 1..31) return s
            return null
        }

        // Format MM/DD/YYYY or M/D/YYYY
        val slashMatch = Regex("^(\\d{1,2})[/.-](\\d{1,2})[/.-](\\d{4})$").find(s)
        if (slashMatch != null) {
            val month = slashMatch.groupValues[1].toIntOrNull() ?: return null
            val day = slashMatch.groupValues[2].toIntOrNull() ?: return null
            val year = slashMatch.groupValues[3].toIntOrNull() ?: return null
            if (month in 1..12 && day in 1..31) {
                return String.format(Locale.US, "%04d-%02d-%02d", year, month, day)
            } else if (month in 13..31 && day in 1..12) {
                return String.format(Locale.US, "%04d-%02d-%02d", year, day, month)
            }
        }

        // Named month patterns, e.g. "October 15, 2026", "Oct 15, 2026", "15 Oct 2026"
        val monthNames = mapOf(
            "jan" to 1, "feb" to 2, "mar" to 3, "apr" to 4, "may" to 5, "jun" to 6,
            "jul" to 7, "aug" to 8, "sep" to 9, "oct" to 10, "nov" to 11, "dec" to 12
        )

        val monthFirstMatch = Regex("(?i)\\b(jan[a-z]*|feb[a-z]*|mar[a-z]*|apr[a-z]*|may|jun[a-z]*|jul[a-z]*|aug[a-z]*|sep[a-z]*|oct[a-z]*|nov[a-z]*|dec[a-z]*)\\s+(\\d{1,2})(?:st|nd|rd|th)?(?:[\\s,]+(\\d{4}))?\\b").find(s)
        if (monthFirstMatch != null) {
            val mKey = monthFirstMatch.groupValues[1].lowercase(Locale.ROOT).take(3)
            val month = monthNames[mKey] ?: return null
            val day = monthFirstMatch.groupValues[2].toIntOrNull() ?: return null
            val year = monthFirstMatch.groupValues[3].takeIf { it.isNotBlank() }?.toIntOrNull() ?: 2026
            if (day in 1..31) {
                return String.format(Locale.US, "%04d-%02d-%02d", year, month, day)
            }
        }

        val dayFirstMatch = Regex("(?i)\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+(jan[a-z]*|feb[a-z]*|mar[a-z]*|apr[a-z]*|may|jun[a-z]*|jul[a-z]*|aug[a-z]*|sep[a-z]*|oct[a-z]*|nov[a-z]*|dec[a-z]*)(?:[\\s,]+(\\d{4}))?\\b").find(s)
        if (dayFirstMatch != null) {
            val day = dayFirstMatch.groupValues[1].toIntOrNull() ?: return null
            val mKey = dayFirstMatch.groupValues[2].lowercase(Locale.ROOT).take(3)
            val month = monthNames[mKey] ?: return null
            val year = dayFirstMatch.groupValues[3].takeIf { it.isNotBlank() }?.toIntOrNull() ?: 2026
            if (day in 1..31) {
                return String.format(Locale.US, "%04d-%02d-%02d", year, month, day)
            }
        }

        return null
    }

    /**
     * Evaluates upload deadline.
     * Deadline is 12:00:00.000 PM on the report date in the target timezone.
     */
    fun isDeadlinePassed(
        submissionTimestampMs: Long,
        reportDate: String,
        timezone: String = "Asia/Kolkata"
    ): Boolean {
        val deadlineMs = getDeadlineTimestampMs(reportDate, timezone) ?: return false
        return submissionTimestampMs > deadlineMs
    }

    fun getDeadlineTimestampMs(
        reportDate: String,
        timezone: String = "Asia/Kolkata"
    ): Long? {
        val isoDate = normalizeDateToIso(reportDate) ?: return null
        val parts = isoDate.split("-")
        if (parts.size != 3) return null
        val tz = TimeZone.getTimeZone(timezone)
        val cal = Calendar.getInstance(tz).apply {
            set(Calendar.YEAR, parts[0].toInt())
            set(Calendar.MONTH, parts[1].toInt() - 1)
            set(Calendar.DAY_OF_MONTH, parts[2].toInt())
            set(Calendar.HOUR_OF_DAY, DEADLINE_HOUR)
            set(Calendar.MINUTE, DEADLINE_MINUTE)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        return cal.timeInMillis
    }

    fun getWindowStartTimestampMs(
        reportDate: String,
        timezone: String = "Asia/Kolkata"
    ): Long? {
        val deadlineMs = getDeadlineTimestampMs(reportDate, timezone) ?: return null
        val tz = TimeZone.getTimeZone(timezone)
        val cal = Calendar.getInstance(tz).apply {
            timeInMillis = deadlineMs
            set(Calendar.HOUR_OF_DAY, 0)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        return cal.timeInMillis
    }

    /**
     * Authoritative evaluation of a sleep report submission.
     */
    fun evaluateSleepBonus(
        extractedDate: String?,
        onset: ClockTime?,
        wake: ClockTime?,
        claimedDate: String,
        submissionTimestampMs: Long,
        timezone: String = "Asia/Kolkata",
        isDuplicate: Boolean = false,
        alreadyAwarded: Boolean = false,
        ignoreDeadline: Boolean = false,
        reconstructedWakeDate: String? = null,
        isConsistent: Boolean = true
    ): EvaluationResult {
        if (isDuplicate) {
            return EvaluationResult(
                eligible = false,
                points = 0,
                status = "DUPLICATE_SCREENSHOT",
                reason = "This screenshot has already been submitted for this report date."
            )
        }

        if (extractedDate.isNullOrBlank() || onset == null || wake == null) {
            return EvaluationResult(
                eligible = false,
                points = 0,
                status = "NEEDS_REVIEW",
                reason = "The report date could not be verified. Please submit a readable screenshot."
            )
        }

        if (!validateReportDate(extractedDate, claimedDate)) {
            return EvaluationResult(
                eligible = false,
                points = 0,
                status = "DATE_MISMATCH",
                reason = "This screenshot belongs to a different report date.",
                dateMismatch = true
            )
        }

        if (reconstructedWakeDate != null && !validateReportDate(reconstructedWakeDate, claimedDate)) {
            return EvaluationResult(
                eligible = false,
                points = 0,
                status = "INCONSISTENT_TIMESTAMPS",
                reason = "The sleep report's timestamps are inconsistent. Verification failed."
            )
        }

        if (!isConsistent) {
            return EvaluationResult(
                eligible = false,
                points = 0,
                status = "INCONSISTENT_TIMESTAMPS",
                reason = "The sleep report's timestamps are inconsistent. Verification failed."
            )
        }

        val windowStart = getWindowStartTimestampMs(claimedDate, timezone)
        if (!ignoreDeadline && windowStart != null && submissionTimestampMs < windowStart) {
            return EvaluationResult(
                eligible = false,
                points = 0,
                status = "WINDOW_NOT_OPEN",
                reason = "Sleep data upload window for $claimedDate opens at 12:00 AM."
            )
        }

        // Calculate potential points if submitted on-time
        val wakeMinutes = wake.totalMinutesSinceMidnight
        val wakePastCutoff = wakeMinutes > WAKE_CUTOFF_TOTAL_MINUTES
        val elapsedMinutes = if (onset.isPreviousDay) 0 else onset.totalMinutesSinceMidnight
        val lateBands = if (onset.isPreviousDay) 0 else calculateLateHourBands(elapsedMinutes)
        val onTimePts = if (wakePastCutoff) {
            0
        } else if (onset.isPreviousDay) {
            MAX_BONUS_POINTS
        } else {
            calculateLateBonusPoints(lateBands)
        }

        if (!ignoreDeadline && isDeadlinePassed(submissionTimestampMs, claimedDate, timezone)) {
            val potentialDesc = if (onTimePts > 0) " (Algorithm calculation: would have been +$onTimePts points if submitted before 12:00 PM)" else " (Algorithm calculation: 0 points)"
            return EvaluationResult(
                eligible = false,
                points = 0,
                status = "DEADLINE_PASSED",
                reason = "Submission deadline of 12:00 PM for $claimedDate has passed$potentialDesc.",
                deadlinePassed = true,
                onTimePoints = onTimePts
            )
        }

        if (wakePastCutoff) {
            return EvaluationResult(
                eligible = false,
                points = 0,
                status = "WAKE_AFTER_CUTOFF",
                reason = "Wake-up time (${wake.formatted12h}) is after the 9:30 AM cutoff.",
                wakeUpPastCutoff = true,
                onTimePoints = 0
            )
        }

        if (alreadyAwarded) {
            return EvaluationResult(
                eligible = false,
                points = 0,
                status = "ALREADY_AWARDED",
                reason = "Sleep bonus has already been awarded for $claimedDate.",
                lateHourBands = lateBands,
                onTimePoints = onTimePts
            )
        }

        // Onset before midnight
        if (onset.isPreviousDay) {
            return EvaluationResult(
                eligible = true,
                points = MAX_BONUS_POINTS,
                status = "APPROVED",
                reason = "Sleep onset before midnight (${onset.formatted12h}) and wake-up on time (${wake.formatted12h}).",
                lateHourBands = 0,
                onTimePoints = MAX_BONUS_POINTS
            )
        }

        // Onset after midnight on report date
        val bonus = calculateLateBonusPoints(lateBands)

        return if (bonus > 0) {
            EvaluationResult(
                eligible = true,
                points = bonus,
                status = "PARTIALLY_AWARDED",
                reason = "Sleep onset after midnight at ${onset.formatted12h} ($lateBands hour bands late; $bonus points awarded).",
                lateHourBands = lateBands,
                onTimePoints = bonus
            )
        } else {
            EvaluationResult(
                eligible = false,
                points = 0,
                status = "ZERO_POINTS",
                reason = "Sleep onset at ${onset.formatted12h} exceeded the allowable bonus threshold (0 points awarded).",
                lateHourBands = lateBands,
                onTimePoints = 0
            )
        }
    }

    data class EarlySleepEligibilityResult(
        val eligible: Boolean,
        val status: String,
        val reason: String,
        val conditionAMet: Boolean,
        val conditionBMet: Boolean,
        val onsetFormatted: String?,
        val wakeFormatted: String?
    )

    /**
     * Pure deterministic evaluation for the Early Sleep Reward (EARLY_SLEEP_ONE_VIDEO):
     * Condition A: Actual sleep onset strictly before 12:15 AM (00:15). Onset >= 00:15 does not qualify.
     * Condition B: Actual final wake-up strictly before 9:30 AM (09:30). Wake-up >= 09:30 does not qualify.
     * Associated with the wake-up date across midnight.
     */
    fun evaluateEarlySleepRewardEligibility(
        onset: ClockTime?,
        wake: ClockTime?,
        reportDate: String
    ): EarlySleepEligibilityResult {
        if (onset == null || wake == null) {
            return EarlySleepEligibilityResult(
                eligible = false,
                status = "SLEEP_DATA_PENDING",
                reason = "Sleep data is pending. Connect Mi Fitness or submit sleep report.",
                conditionAMet = false,
                conditionBMet = false,
                onsetFormatted = null,
                wakeFormatted = null
            )
        }

        // Condition A: Sleep onset strictly before 12:15 AM
        // If onset occurred on the previous day (before midnight, e.g. 11:30 PM), it is before 00:15.
        // If onset occurred after midnight, total minutes since midnight must be strictly < 15.
        val condA = onset.isPreviousDay || onset.totalMinutesSinceMidnight < 15

        // Condition B: Wake-up strictly before 9:30 AM (570 minutes)
        val condB = wake.totalMinutesSinceMidnight < 570

        val eligible = condA && condB
        val reason = when {
            eligible -> "Verified: Sleep onset ${onset.formatted12h} is before 12:15 AM and wake-up ${wake.formatted12h} is before 9:30 AM."
            !condA && !condB -> "Sleep onset (${onset.formatted12h}) >= 12:15 AM and wake-up (${wake.formatted12h}) >= 9:30 AM."
            !condA -> "Sleep onset (${onset.formatted12h}) is at or after 12:15 AM cutoff (must be before 12:15 AM)."
            else -> "Wake-up time (${wake.formatted12h}) is at or after 9:30 AM cutoff (must be before 9:30 AM)."
        }

        return EarlySleepEligibilityResult(
            eligible = eligible,
            status = if (eligible) "ELIGIBLE" else "NOT_ELIGIBLE",
            reason = reason,
            conditionAMet = condA,
            conditionBMet = condB,
            onsetFormatted = onset.formatted12h,
            wakeFormatted = wake.formatted12h
        )
    }
}
