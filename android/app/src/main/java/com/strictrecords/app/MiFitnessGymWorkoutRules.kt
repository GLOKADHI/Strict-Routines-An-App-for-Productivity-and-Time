package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Pure deterministic validation, scoring, and session-matching rules for Mi Fitness Gym workouts.
 * Shared across Android application, background evaluation pipeline, and JVM unit tests.
 *
 * Requirements:
 * 1. Do not change existing Gym point rules (15 pts standard award).
 * 2. Asynchronous evaluation; no points awarded for upload alone.
 * 3. Exact matching of workout date and scheduled session occurrence.
 * 4. Preservation of distinction between workout calories and total calories.
 * 5. Extraction of actual values without fabricating defaults when OCR fails.
 */
object MiFitnessGymWorkoutRules {

    const val GYM_POINTS = 15
    const val MIN_PLAUSIBLE_DURATION_SECONDS = 60 // At least 1 minute of activity
    const val MAX_PLAUSIBLE_DURATION_SECONDS = 12 * 3600 // Up to 12 hours
    const val MIN_PLAUSIBLE_HR = 40
    const val MAX_PLAUSIBLE_HR = 240
    const val MAX_PLAUSIBLE_CALORIES = 8000

    data class WorkoutClockTime(
        val hour24: Int,
        val minute: Int,
        val second: Int = 0,
        val isPm: Boolean = false,
        val rawText: String = ""
    ) {
        val formatted12h: String
            get() {
                val h12 = if (hour24 % 12 == 0) 12 else hour24 % 12
                val amPm = if (hour24 < 12) "AM" else "PM"
                return if (second > 0) {
                    String.format(Locale.US, "%d:%02d:%02d %s", h12, minute, second, amPm)
                } else {
                    String.format(Locale.US, "%d:%02d %s", h12, minute, amPm)
                }
            }

        val formatted24h: String
            get() = if (second > 0) {
                String.format(Locale.US, "%02d:%02d:%02d", hour24, minute, second)
            } else {
                String.format(Locale.US, "%02d:%02d", hour24, minute)
            }

        val totalSecondsSinceMidnight: Int
            get() = hour24 * 3600 + minute * 60 + second

        fun toJson(): JSONObject = JSONObject().apply {
            put("hour24", hour24)
            put("minute", minute)
            put("second", second)
            put("is_pm", isPm)
            put("formatted_12h", formatted12h)
            put("formatted_24h", formatted24h)
            put("raw_text", rawText)
        }
    }

    data class ParsedWorkoutData(
        val workoutDate: String?, // Normalized ISO YYYY-MM-DD
        val workoutDateRaw: String? = null,
        val workoutTime: WorkoutClockTime? = null,
        val workoutType: String? = null,
        val durationSeconds: Int? = null,
        val durationDisplay: String? = null,
        val workoutCalories: Int? = null,
        val totalCalories: Int? = null,
        val avgHeartRate: Int? = null,
        val maxHeartRate: Int? = null,
        val heartRateZones: Map<String, String> = emptyMap(),
        val trainingEffect: String? = null,
        val dataSource: String? = null,
        val rawOcrText: String = "",
        val isAmbiguous: Boolean = false,
        val ambiguityReason: String = ""
    ) {
        fun toJson(): JSONObject = JSONObject().apply {
            put("workout_date", workoutDate ?: JSONObject.NULL)
            put("workout_date_raw", workoutDateRaw ?: JSONObject.NULL)
            put("workout_time", workoutTime?.toJson() ?: JSONObject.NULL)
            put("workout_type", workoutType ?: JSONObject.NULL)
            put("duration_seconds", durationSeconds ?: JSONObject.NULL)
            put("duration_display", durationDisplay ?: JSONObject.NULL)
            put("workout_calories", workoutCalories ?: JSONObject.NULL)
            put("total_calories", totalCalories ?: JSONObject.NULL)
            put("avg_heart_rate", avgHeartRate ?: JSONObject.NULL)
            put("max_heart_rate", maxHeartRate ?: JSONObject.NULL)
            put("heart_rate_zones", JSONObject(heartRateZones))
            put("training_effect", trainingEffect ?: JSONObject.NULL)
            put("data_source", dataSource ?: JSONObject.NULL)
            put("is_ambiguous", isAmbiguous)
            put("ambiguity_reason", ambiguityReason)
        }
    }

    data class GymEvaluationResult(
        val eligible: Boolean,
        val points: Int,
        val status: String, // "APPROVED", "NEEDS_REVIEW", "REJECTED", "ALREADY_AWARDED"
        val decision: String, // "APPROVED", "FLAGGED_FOR_REVIEW", "REJECTED"
        val reason: String,
        val dateMismatch: Boolean = false,
        val timeMismatch: Boolean = false,
        val isDuplicate: Boolean = false,
        val alreadyAwarded: Boolean = false,
        val matchedActivityId: String? = null,
        val submissionWindowStatus: String = "UNKNOWN", // "WITHIN_WINDOW", "WINDOW_MISMATCH", "OUTSIDE_WINDOW"
        val calculatedEndTimeIso: String? = null,
        val discrepancyReasons: List<String> = emptyList(),
        val fieldStatus: Map<String, String> = emptyMap()
    ) {
        fun toJson(): JSONObject = JSONObject().apply {
            put("eligible", eligible)
            put("points", points)
            put("status", status)
            put("decision", decision)
            put("reason", reason)
            put("date_mismatch", dateMismatch)
            put("time_mismatch", timeMismatch)
            put("is_duplicate", isDuplicate)
            put("already_awarded", alreadyAwarded)
            put("matched_activity_id", matchedActivityId ?: JSONObject.NULL)
            put("submission_window_status", submissionWindowStatus)
            put("calculated_end_time_iso", calculatedEndTimeIso ?: JSONObject.NULL)
            put("discrepancy_reasons", JSONArray(discrepancyReasons))
            put("field_status", JSONObject(fieldStatus))
        }
    }

    /**
     * Normalizes a raw date string into ISO "YYYY-MM-DD" format.
     * Supports:
     * - "13/10/2026", "13-10-2026" (DD/MM/YYYY)
     * - "10/13/2026" (MM/DD/YYYY)
     * - "2026-10-13", "2026/10/13" (YYYY-MM-DD)
     * - "13 October 2026", "13 Oct 2026"
     * - "October 13, 2026", "Oct 13, 2026"
     */
    fun normalizeDateToIso(
        rawDate: String,
        defaultYear: Int = 2026,
        preferredDay: Int? = null,
        preferredMonth: Int? = null
    ): String? {
        val clean = rawDate.trim().replace(",", " ")
        if (clean.isBlank()) return null

        // 1. Text Month formats: "13 Oct 2026" or "13 October 2026" or "October 13 2026"
        val textMonthRegex = Regex("""(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})""", RegexOption.IGNORE_CASE)
        val m1 = textMonthRegex.find(clean)
        if (m1 != null) {
            val d = m1.groupValues[1].toIntOrNull() ?: 1
            val mName = m1.groupValues[2].lowercase(Locale.US)
            val y = m1.groupValues[3].toIntOrNull() ?: defaultYear
            val mNum = monthNameToNumber(mName)
            if (mNum in 1..12 && d in 1..31) {
                return String.format(Locale.US, "%04d-%02d-%02d", y, mNum, d)
            }
        }

        val textMonthPreRegex = Regex("""([A-Za-z]{3,9})\s+(\d{1,2})\s+(\d{4})""", RegexOption.IGNORE_CASE)
        val m2 = textMonthPreRegex.find(clean)
        if (m2 != null) {
            val mName = m2.groupValues[1].lowercase(Locale.US)
            val d = m2.groupValues[2].toIntOrNull() ?: 1
            val y = m2.groupValues[3].toIntOrNull() ?: defaultYear
            val mNum = monthNameToNumber(mName)
            if (mNum in 1..12 && d in 1..31) {
                return String.format(Locale.US, "%04d-%02d-%02d", y, mNum, d)
            }
        }

        // 2. ISO YYYY-MM-DD or YYYY/MM/DD
        val isoRegex = Regex("""(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})""")
        val mIso = isoRegex.find(clean)
        if (mIso != null) {
            val y = mIso.groupValues[1].toInt()
            val m = mIso.groupValues[2].toInt()
            val d = mIso.groupValues[3].toInt()
            if (m in 1..12 && d in 1..31) {
                return String.format(Locale.US, "%04d-%02d-%02d", y, m, d)
            }
        }

        // 3. Delimited numeric date: D/M/Y or M/D/Y
        val numRegex = Regex("""(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})""")
        val mNum = numRegex.find(clean)
        if (mNum != null) {
            val p1 = mNum.groupValues[1].toInt()
            val p2 = mNum.groupValues[2].toInt()
            var y = mNum.groupValues[3].toInt()
            if (y < 100) y += 2000

            val day: Int
            val month: Int

            if (p1 > 12 && p2 <= 12) {
                // Must be DD/MM/YYYY (e.g. 13/10/2026)
                day = p1
                month = p2
            } else if (p2 > 12 && p1 <= 12) {
                // Must be MM/DD/YYYY (e.g. 10/13/2026)
                month = p1
                day = p2
            } else if (p1 <= 12 && p2 <= 12) {
                // Ambiguous: check if preferredDay/Month clarifies
                if (preferredDay != null && preferredMonth != null) {
                    if (p1 == preferredDay && p2 == preferredMonth) {
                        day = p1
                        month = p2
                    } else if (p1 == preferredMonth && p2 == preferredDay) {
                        month = p1
                        day = p2
                    } else {
                        // Default to DD/MM/YYYY (Mi Fitness international standard)
                        day = p1
                        month = p2
                    }
                } else {
                    // Default to DD/MM/YYYY
                    day = p1
                    month = p2
                }
            } else {
                return null
            }

            if (month in 1..12 && day in 1..31) {
                return String.format(Locale.US, "%04d-%02d-%02d", y, month, day)
            }
        }

        return null
    }

    private fun monthNameToNumber(name: String): Int = when {
        name.startsWith("jan") -> 1
        name.startsWith("feb") -> 2
        name.startsWith("mar") -> 3
        name.startsWith("apr") -> 4
        name.startsWith("may") -> 5
        name.startsWith("jun") -> 6
        name.startsWith("jul") -> 7
        name.startsWith("aug") -> 8
        name.startsWith("sep") -> 9
        name.startsWith("oct") -> 10
        name.startsWith("nov") -> 11
        name.startsWith("dec") -> 12
        else -> -1
    }

    /**
     * Parses a 12-hour or 24-hour clock time into WorkoutClockTime.
     * e.g. "6:36 pm", "6:36:01 PM", "18:36", "06:36 pm"
     */
    fun parseClockTime(rawTime: String): WorkoutClockTime? {
        val clean = rawTime.trim().lowercase(Locale.US)
        val timeRegex = Regex("""(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?""")
        val match = timeRegex.find(clean) ?: return null

        var h = match.groupValues[1].toIntOrNull() ?: return null
        val m = match.groupValues[2].toIntOrNull() ?: return null
        val s = match.groupValues[3].takeIf { it.isNotBlank() }?.toIntOrNull() ?: 0
        val amPm = match.groupValues[4].takeIf { it.isNotBlank() }

        val isPm = when (amPm) {
            "pm" -> true
            "am" -> false
            else -> h >= 12
        }

        if (amPm != null) {
            if (h !in 1..12 || m !in 0..59 || s !in 0..59) return null
            if (isPm && h < 12) h += 12
            if (!isPm && h == 12) h = 0
        } else {
            if (h !in 0..23 || m !in 0..59 || s !in 0..59) return null
        }

        return WorkoutClockTime(
            hour24 = h,
            minute = m,
            second = s,
            isPm = isPm,
            rawText = rawTime.trim()
        )
    }

    /**
     * Parses duration text into total seconds.
     * Supports:
     * - "01:04:01" (HH:MM:SS) -> 3841 sec
     * - "04:01" (MM:SS) -> 241 sec
     * - "1 hr 4 mins 1 sec" -> 3841 sec
     * - "1h 4m 1s" -> 3841 sec
     * - "64 mins" -> 3840 sec
     */
    fun parseDurationToSeconds(rawDuration: String): Int? {
        val clean = rawDuration.trim()
        if (clean.isBlank()) return null

        // 1. HH:MM:SS format
        val hms = Regex("""(\d{1,2}):(\d{2}):(\d{2})""").find(clean)
        if (hms != null) {
            val h = hms.groupValues[1].toInt()
            val m = hms.groupValues[2].toInt()
            val s = hms.groupValues[3].toInt()
            return h * 3600 + m * 60 + s
        }

        // 2. MM:SS format
        val ms = Regex("""^(\d{1,3}):(\d{2})$""").find(clean)
        if (ms != null) {
            val m = ms.groupValues[1].toInt()
            val s = ms.groupValues[2].toInt()
            return m * 60 + s
        }

        // 3. Text format: e.g. "1 hr 4 mins 1 sec", "1h 4m 1s", "1 hour, 4 minutes, 1 second"
        var totalSec = 0
        var foundAny = false

        val hMatch = Regex("""(\d+)\s*(?:hour|hours|hr|hrs|h)\b""", RegexOption.IGNORE_CASE).find(clean)
        if (hMatch != null) {
            totalSec += (hMatch.groupValues[1].toIntOrNull() ?: 0) * 3600
            foundAny = true
        }

        val mMatch = Regex("""(\d+)\s*(?:minute|minutes|min|mins|m)\b""", RegexOption.IGNORE_CASE).find(clean)
        if (mMatch != null) {
            totalSec += (mMatch.groupValues[1].toIntOrNull() ?: 0) * 60
            foundAny = true
        }

        val sMatch = Regex("""(\d+)\s*(?:second|seconds|sec|secs|s)\b""", RegexOption.IGNORE_CASE).find(clean)
        if (sMatch != null) {
            totalSec += (sMatch.groupValues[1].toIntOrNull() ?: 0)
            foundAny = true
        }

        return if (foundAny && totalSec > 0) totalSec else null
    }

    /**
     * Formats duration in seconds to standard "HH:mm:ss" or human-readable "X hr Y mins Z sec".
     */
    fun formatDuration(seconds: Int, humanReadable: Boolean = false): String {
        val h = seconds / 3600
        val m = (seconds % 3600) / 60
        val s = seconds % 60
        return if (humanReadable) {
            buildString {
                if (h > 0) append("$h hr ")
                if (m > 0 || h > 0) append("$m min ")
                append("$s sec")
            }.trim()
        } else {
            String.format(Locale.US, "%02d:%02d:%02d", h, m, s)
        }
    }

    /**
     * Calculates the workout end time as an ISO string given start date, start time, and duration seconds.
     * Correctly rolls over midnight across calendar days when an overnight workout occurs.
     */
    fun calculateEndTimeIso(
        startDateIso: String,
        startTime: WorkoutClockTime,
        durationSeconds: Int,
        timezone: String = "Asia/Kolkata"
    ): String? {
        return try {
            val tz = TimeZone.getTimeZone(timezone)
            val cal = Calendar.getInstance(tz).apply {
                val p = startDateIso.split("-")
                set(Calendar.YEAR, p[0].toInt())
                set(Calendar.MONTH, p[1].toInt() - 1)
                set(Calendar.DAY_OF_MONTH, p[2].toInt())
                set(Calendar.HOUR_OF_DAY, startTime.hour24)
                set(Calendar.MINUTE, startTime.minute)
                set(Calendar.SECOND, startTime.second)
                set(Calendar.MILLISECOND, 0)
                add(Calendar.SECOND, durationSeconds)
            }
            val isoSdf = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).apply {
                timeZone = tz
            }
            isoSdf.format(cal.time)
        } catch (_: Exception) { null }
    }

    /**
     * Evaluates workout validity against the intended Gym occurrence and rules.
     */
    fun evaluateGymWorkout(
        parsed: ParsedWorkoutData,
        claimedDate: String,
        scheduledActivityId: String? = null,
        plannedTimeHhmm: String? = null,
        windowStartMs: Long = 0L,
        windowEndMs: Long = 0L,
        submissionTimestampMs: Long = System.currentTimeMillis(),
        timezone: String = "Asia/Kolkata",
        isDuplicate: Boolean = false,
        alreadyAwarded: Boolean = false
    ): GymEvaluationResult {
        val discrepancies = mutableListOf<String>()
        val fieldStatus = mutableMapOf<String, String>()

        // 1. Duplicate detection check
        if (isDuplicate) {
            return GymEvaluationResult(
                eligible = false,
                points = 0,
                status = "REJECTED",
                decision = "REJECTED",
                reason = "Duplicate screenshot detected. Evidence was previously submitted.",
                isDuplicate = true,
                alreadyAwarded = alreadyAwarded,
                matchedActivityId = scheduledActivityId,
                discrepancyReasons = listOf("Duplicate screenshot detected")
            )
        }

        // 2. Idempotency / Already awarded check
        if (alreadyAwarded) {
            return GymEvaluationResult(
                eligible = false,
                points = 0,
                status = "ALREADY_AWARDED",
                decision = "APPROVED",
                reason = "Gym occurrence already awarded points for this date.",
                isDuplicate = false,
                alreadyAwarded = true,
                matchedActivityId = scheduledActivityId
            )
        }

        // 3. Date Matching Check
        var dateMismatch = false
        if (parsed.workoutDate == null) {
            fieldStatus["workoutDate"] = "NOT_DETECTED"
            return GymEvaluationResult(
                eligible = false,
                points = 0,
                status = "NEEDS_REVIEW",
                decision = "FLAGGED_FOR_REVIEW",
                reason = "Workout date could not be extracted from screenshot. This proof requires review.",
                dateMismatch = true,
                matchedActivityId = scheduledActivityId,
                discrepancyReasons = listOf("Workout date missing from OCR evidence")
            )
        } else {
            fieldStatus["workoutDate"] = "DETECTED: ${parsed.workoutDate}"
            if (parsed.workoutDate != claimedDate) {
                dateMismatch = true
                discrepancies.add("Extracted workout date (${parsed.workoutDate}) does not match scheduled session ($claimedDate)")
                return GymEvaluationResult(
                    eligible = false,
                    points = 0,
                    status = "NEEDS_REVIEW",
                    decision = "FLAGGED_FOR_REVIEW",
                    reason = "Workout date does not match the scheduled Gym session. This proof requires review.",
                    dateMismatch = true,
                    matchedActivityId = scheduledActivityId,
                    discrepancyReasons = discrepancies
                )
            }
        }

        // 4. Workout Duration Validation
        val durSec = parsed.durationSeconds
        if (durSec == null || durSec <= 0) {
            fieldStatus["duration"] = "NOT_DETECTED_OR_ZERO"
            return GymEvaluationResult(
                eligible = false,
                points = 0,
                status = "NEEDS_REVIEW",
                decision = "FLAGGED_FOR_REVIEW",
                reason = "Workout duration is missing or non-positive. This proof requires review.",
                matchedActivityId = scheduledActivityId,
                discrepancyReasons = listOf("Workout duration is missing or non-positive")
            )
        } else {
            fieldStatus["duration"] = "VALID: ${durSec}s (${parsed.durationDisplay ?: formatDuration(durSec)})"
        }

        // 5. Workout Calories vs Total Calories Consistency Check
        val wkCal = parsed.workoutCalories
        val totCal = parsed.totalCalories
        if (wkCal != null) fieldStatus["workoutCalories"] = "DETECTED: $wkCal kcal"
        if (totCal != null) fieldStatus["totalCalories"] = "DETECTED: $totCal kcal"

        if (wkCal != null && totCal != null && wkCal > totCal) {
            discrepancies.add("Workout calories ($wkCal kcal) exceed total calories ($totCal kcal)")
        }

        // 6. Heart Rate Consistency Check
        val avgHr = parsed.avgHeartRate
        val maxHr = parsed.maxHeartRate
        if (avgHr != null) fieldStatus["avgHeartRate"] = "DETECTED: $avgHr BPM"
        if (maxHr != null) fieldStatus["maxHeartRate"] = "DETECTED: $maxHr BPM"

        if (avgHr != null && maxHr != null && avgHr > maxHr) {
            discrepancies.add("Average heart rate ($avgHr BPM) exceeds maximum heart rate ($maxHr BPM)")
        }

        // 7. Calculated End Time & Timing Check
        var calculatedEndIso: String? = null
        if (parsed.workoutDate != null && parsed.workoutTime != null && durSec > 0) {
            calculatedEndIso = calculateEndTimeIso(parsed.workoutDate, parsed.workoutTime, durSec, timezone)
        }

        // 8. Submission Window Status
        var windowStatus = "UNKNOWN"
        if (windowStartMs > 0L && windowEndMs > 0L) {
            windowStatus = when {
                submissionTimestampMs in windowStartMs until windowEndMs -> "WITHIN_WINDOW"
                submissionTimestampMs < windowStartMs -> "BEFORE_WINDOW"
                else -> "OUTSIDE_WINDOW"
            }
        }

        // 9. Final Decision
        val hasCriticalDiscrepancy = discrepancies.isNotEmpty()
        val finalStatus = if (hasCriticalDiscrepancy) "NEEDS_REVIEW" else "APPROVED"
        val finalDecision = if (hasCriticalDiscrepancy) "FLAGGED_FOR_REVIEW" else "APPROVED"
        val finalReason = if (hasCriticalDiscrepancy) {
            "Workout verification flagged: ${discrepancies.joinToString("; ")}"
        } else {
            "Valid Mi Fitness Gym workout verified (+15 pts)."
        }
        val pointsToAward = if (!hasCriticalDiscrepancy) GYM_POINTS else 0

        return GymEvaluationResult(
            eligible = !hasCriticalDiscrepancy,
            points = pointsToAward,
            status = finalStatus,
            decision = finalDecision,
            reason = finalReason,
            dateMismatch = dateMismatch,
            matchedActivityId = scheduledActivityId,
            submissionWindowStatus = windowStatus,
            calculatedEndTimeIso = calculatedEndIso,
            discrepancyReasons = discrepancies,
            fieldStatus = fieldStatus
        )
    }
}
