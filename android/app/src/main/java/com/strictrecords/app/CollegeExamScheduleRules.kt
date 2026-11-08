package com.strictrecords.app

import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

/**
 * Authoritative rules for College/Exam planning, post-exam class time calculation,
 * optional attendance bonus eligibility, and independent bath scheduling.
 */
object CollegeExamScheduleRules {

    data class ValidationResult(
        val isValid: Boolean,
        val errorMessage: String? = null
    )

    data class IntervalResult(
        val hasInterval: Boolean,
        val startHhmm: String? = null,
        val endHhmm: String? = null,
        val durationMinutes: Int = 0,
        val errorMessage: String? = null
    ) {
        val displayRange: String get() = if (hasInterval && startHhmm != null && endHhmm != null) {
            "${DisciplineScoringRules.formatTime12h(startHhmm)}–${DisciplineScoringRules.formatTime12h(endHhmm)}"
        } else ""
    }

    data class SlotEligibilityResult(
        val isEligible: Boolean,
        val reason: String
    )

    data class CollegeExamPlan(
        val date: String,
        val timezone: String = "Asia/Kolkata",
        val attendsCollege: Boolean,
        val collegeStartHhmm: String? = null,
        val collegeEndHhmm: String? = null,
        val hasExam: Boolean = false,
        val examStartHhmm: String? = null,
        val examEndHhmm: String? = null,
        val postExamClassStartHhmm: String? = null,
        val postExamClassEndHhmm: String? = null,
        val revision: Int = 1,
        val createdAt: String = "",
        val updatedAt: String = ""
    ) {
        val hasPostExamClassTime: Boolean
            get() = hasExam && !postExamClassStartHhmm.isNullOrBlank() && !postExamClassEndHhmm.isNullOrBlank()

        val postExamClassDisplay: String
            get() = if (hasPostExamClassTime) {
                "${DisciplineScoringRules.formatTime12h(postExamClassStartHhmm!!)}–${DisciplineScoringRules.formatTime12h(postExamClassEndHhmm!!)}"
            } else ""

        val collegeHoursDisplay: String
            get() = if (attendsCollege && !collegeStartHhmm.isNullOrBlank() && !collegeEndHhmm.isNullOrBlank()) {
                "${DisciplineScoringRules.formatTime12h(collegeStartHhmm)}–${DisciplineScoringRules.formatTime12h(collegeEndHhmm)}"
            } else ""

        val examHoursDisplay: String
            get() = if (hasExam && !examStartHhmm.isNullOrBlank() && !examEndHhmm.isNullOrBlank()) {
                "${DisciplineScoringRules.formatTime12h(examStartHhmm)}–${DisciplineScoringRules.formatTime12h(examEndHhmm)}"
            } else ""

        fun toJson(): JSONObject = JSONObject().apply {
            put("date", date)
            put("timezone", timezone)
            put("attends_college", if (attendsCollege) "YES" else "NO")
            put("college_start_time", collegeStartHhmm ?: "")
            put("college_end_time", collegeEndHhmm ?: "")
            put("has_exam", if (hasExam) "YES" else "NO")
            put("exam_start_time", examStartHhmm ?: "")
            put("exam_end_time", examEndHhmm ?: "")
            put("post_exam_start_time", postExamClassStartHhmm ?: "")
            put("post_exam_end_time", postExamClassEndHhmm ?: "")
            put("revision", revision)
            put("created_at", createdAt)
            put("updated_at", updatedAt)
        }

        companion object {
            fun fromJson(json: JSONObject): CollegeExamPlan {
                val date = json.optString("date")
                val tz = json.optString("timezone", "Asia/Kolkata")
                val attends = json.optString("attends_college").equals("YES", ignoreCase = true) ||
                    json.optBoolean("attends_college", false)
                val cStart = json.optString("college_start_time").takeIf { it.isNotBlank() }
                val cEnd = json.optString("college_end_time").takeIf { it.isNotBlank() }
                val hasExam = json.optString("has_exam").equals("YES", ignoreCase = true) ||
                    json.optBoolean("has_exam", false)
                val eStart = json.optString("exam_start_time").takeIf { it.isNotBlank() }
                val eEnd = json.optString("exam_end_time").takeIf { it.isNotBlank() }
                val peStart = json.optString("post_exam_start_time").takeIf { it.isNotBlank() }
                val peEnd = json.optString("post_exam_end_time").takeIf { it.isNotBlank() }
                val rev = json.optInt("revision", 1)
                val cr = json.optString("created_at")
                val up = json.optString("updated_at")

                return CollegeExamPlan(
                    date = date,
                    timezone = tz,
                    attendsCollege = attends,
                    collegeStartHhmm = cStart,
                    collegeEndHhmm = cEnd,
                    hasExam = hasExam,
                    examStartHhmm = eStart,
                    examEndHhmm = eEnd,
                    postExamClassStartHhmm = peStart,
                    postExamClassEndHhmm = peEnd,
                    revision = rev,
                    createdAt = cr,
                    updatedAt = up
                )
            }
        }
    }

    /**
     * Converts "HH:mm" to total minutes since midnight (0..1439).
     * Returns null if string format is invalid.
     */
    fun parseMinutes(hhmm: String?): Int? {
        if (hhmm.isNullOrBlank()) return null
        val parts = hhmm.trim().split(":")
        if (parts.size != 2) return null
        val h = parts[0].toIntOrNull() ?: return null
        val m = parts[1].toIntOrNull() ?: return null
        if (h !in 0..23 || m !in 0..59) return null
        return h * 60 + m
    }

    /**
     * Formats minutes of day into standard "HH:mm".
     */
    fun formatMinutes(minutes: Int): String {
        val normalized = ((minutes % 1440) + 1440) % 1440
        return String.format(Locale.US, "%02d:%02d", normalized / 60, normalized % 60)
    }

    /**
     * Validates college start and end times.
     * College end must be later than college start under same-day scheduling.
     */
    fun validateCollegeTimes(startHhmm: String?, endHhmm: String?): ValidationResult {
        val startMins = parseMinutes(startHhmm)
            ?: return ValidationResult(false, "Invalid college start time. Must be in HH:mm format.")
        val endMins = parseMinutes(endHhmm)
            ?: return ValidationResult(false, "Invalid college end time. Must be in HH:mm format.")

        if (endMins <= startMins) {
            return ValidationResult(false, "College end time (${DisciplineScoringRules.formatTime12h(endHhmm!!)}) must be later than college start time (${DisciplineScoringRules.formatTime12h(startHhmm!!)}).")
        }
        return ValidationResult(true)
    }

    /**
     * Validates exam start and end times in relation to college times.
     * Requirements:
     * - Exam start must precede exam end.
     * - Exam interval must fall within the college period (examStart >= collegeStart && examEnd <= collegeEnd).
     */
    fun validateExamTimes(
        collegeStartHhmm: String?,
        collegeEndHhmm: String?,
        examStartHhmm: String?,
        examEndHhmm: String?
    ): ValidationResult {
        val cVal = validateCollegeTimes(collegeStartHhmm, collegeEndHhmm)
        if (!cVal.isValid) return cVal

        val cStartMins = parseMinutes(collegeStartHhmm)!!
        val cEndMins = parseMinutes(collegeEndHhmm)!!

        val eStartMins = parseMinutes(examStartHhmm)
            ?: return ValidationResult(false, "Invalid exam start time. Must be in HH:mm format.")
        val eEndMins = parseMinutes(examEndHhmm)
            ?: return ValidationResult(false, "Invalid exam end time. Must be in HH:mm format.")

        if (eEndMins <= eStartMins) {
            return ValidationResult(false, "Exam end time (${DisciplineScoringRules.formatTime12h(examEndHhmm!!)}) must be later than exam start time (${DisciplineScoringRules.formatTime12h(examStartHhmm!!)}).")
        }

        if (eStartMins < cStartMins || eEndMins > cEndMins) {
            val cRangeDisplay = "${DisciplineScoringRules.formatTime12h(collegeStartHhmm!!)}–${DisciplineScoringRules.formatTime12h(collegeEndHhmm!!)}"
            val eRangeDisplay = "${DisciplineScoringRules.formatTime12h(examStartHhmm!!)}–${DisciplineScoringRules.formatTime12h(examEndHhmm!!)}"
            return ValidationResult(
                false,
                "Exam interval ($eRangeDisplay) must fall within college hours ($cRangeDisplay)."
            )
        }

        return ValidationResult(true)
    }

    /**
     * Calculates post-exam class time interval:
     * When user has an exam and exam ends before configured college end time,
     * classify the remaining interval from exam end to college end as post-exam class time.
     *
     * Example: College 9:00 AM–3:15 PM, Exam 9:15 AM–11:15 AM -> 11:15 AM–3:15 PM.
     * If exam end equals college end -> no post-exam interval (hasInterval = false).
     */
    fun calculatePostExamClassInterval(
        collegeStartHhmm: String?,
        collegeEndHhmm: String?,
        examStartHhmm: String?,
        examEndHhmm: String?
    ): IntervalResult {
        val validation = validateExamTimes(collegeStartHhmm, collegeEndHhmm, examStartHhmm, examEndHhmm)
        if (!validation.isValid) {
            return IntervalResult(false, null, null, 0, validation.errorMessage)
        }

        val eEndMins = parseMinutes(examEndHhmm)!!
        val cEndMins = parseMinutes(collegeEndHhmm)!!

        if (eEndMins == cEndMins) {
            return IntervalResult(
                hasInterval = false,
                startHhmm = null,
                endHhmm = null,
                durationMinutes = 0,
                errorMessage = null
            )
        }

        val duration = cEndMins - eEndMins
        return IntervalResult(
            hasInterval = true,
            startHhmm = formatMinutes(eEndMins),
            endHhmm = formatMinutes(cEndMins),
            durationMinutes = duration,
            errorMessage = null
        )
    }

    /**
     * Determines whether an hourly slot and its submission window intersects
     * the post-exam class-time interval.
     *
     * Boundary rule:
     * - The slot interval [slotStart, slotEnd) must intersect [postExamStart, postExamEnd).
     * - The submission window [windowStart, windowEnd) must overlap the post-exam period
     *   (windowStart < postExamEnd && windowEnd > postExamStart).
     * - Slots completely outside are never marked eligible.
     */
    fun isSlotIntersectingPostExamInterval(
        slotStartMinutes: Int,
        slotEndMinutes: Int,
        windowStartMinutes: Int,
        windowEndMinutes: Int,
        postExamStartMinutes: Int,
        postExamEndMinutes: Int
    ): SlotEligibilityResult {
        if (postExamEndMinutes <= postExamStartMinutes) {
            return SlotEligibilityResult(false, "Post-exam class time interval is empty or invalid.")
        }

        val slotIntersects = maxOf(slotStartMinutes, postExamStartMinutes) < minOf(slotEndMinutes, postExamEndMinutes)
        val windowIntersects = windowStartMinutes < postExamEndMinutes && windowEndMinutes > postExamStartMinutes

        return if (slotIntersects && windowIntersects) {
            SlotEligibilityResult(true, "Slot intersects post-exam class time ($postExamStartMinutes–$postExamEndMinutes). Optional attendance bonus applies.")
        } else {
            SlotEligibilityResult(false, "Slot lies outside the post-exam class-time interval.")
        }
    }

    /**
     * Overload taking HH:mm strings.
     */
    fun isSlotIntersectingPostExamInterval(
        slotStartHhmm: String,
        slotEndHhmm: String,
        windowStartHhmm: String,
        windowEndHhmm: String,
        postExamStartHhmm: String,
        postExamEndHhmm: String
    ): SlotEligibilityResult {
        val sStart = parseMinutes(slotStartHhmm) ?: return SlotEligibilityResult(false, "Invalid slot start time.")
        val sEnd = parseMinutes(slotEndHhmm) ?: return SlotEligibilityResult(false, "Invalid slot end time.")
        val wStart = parseMinutes(windowStartHhmm) ?: return SlotEligibilityResult(false, "Invalid window start time.")
        val wEnd = parseMinutes(windowEndHhmm) ?: return SlotEligibilityResult(false, "Invalid window end time.")
        val peStart = parseMinutes(postExamStartHhmm) ?: return SlotEligibilityResult(false, "Invalid post-exam start time.")
        val peEnd = parseMinutes(postExamEndHhmm) ?: return SlotEligibilityResult(false, "Invalid post-exam end time.")

        return isSlotIntersectingPostExamInterval(sStart, sEnd, wStart, wEnd, peStart, peEnd)
    }

    /**
     * Validates independent Bath selection for the tomorrow planning workflow (Branch A - No college).
     * Count must be 1 or 2.
     * Each selected time must be a valid HH:mm within the permitted range.
     * If 2 bath sessions: must not collide/overlap (at least 60 minutes apart).
     */
    fun validateIndependentBathSelection(
        sessionCount: Int,
        selectedTimes: List<String>,
        windowStartHhmm: String = "06:00",
        windowEndHhmm: String = "22:00"
    ): ValidationResult {
        if (sessionCount !in listOf(1, 2)) {
            return ValidationResult(false, "Bath session count must be 1 or 2.")
        }

        if (selectedTimes.size < sessionCount) {
            val label = if (sessionCount == 1) "Bath Session 1" else "both Bath Session 1 and Bath Session 2"
            return ValidationResult(false, "Please select planned time for $label.")
        }

        val minMins = parseMinutes(windowStartHhmm) ?: (6 * 60)
        val maxMins = parseMinutes(windowEndHhmm) ?: (22 * 60)

        val parsedMins = mutableListOf<Int>()
        for (i in 0 until sessionCount) {
            val t = selectedTimes[i]
            val mins = parseMinutes(t)
                ?: return ValidationResult(false, "Invalid time format for Bath Session ${i + 1}: '$t'.")

            if (mins < minMins || mins > maxMins) {
                return ValidationResult(
                    false,
                    "Bath Session ${i + 1} (${DisciplineScoringRules.formatTime12h(t)}) must fall within permitted window ${DisciplineScoringRules.formatTime12h(windowStartHhmm)}–${DisciplineScoringRules.formatTime12h(windowEndHhmm)}."
                )
            }
            parsedMins.add(mins)
        }

        if (sessionCount == 2) {
            val diff = kotlin.math.abs(parsedMins[0] - parsedMins[1])
            if (diff < 60) {
                return ValidationResult(
                    false,
                    "Bath Session 1 and Bath Session 2 must be separated by at least 60 minutes to prevent collision."
                )
            }
        }

        return ValidationResult(true)
    }
}
