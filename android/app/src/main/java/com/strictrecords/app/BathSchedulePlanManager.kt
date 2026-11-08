package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Authoritative Daily Bath Plan model and deterministic first-morning popup rules.
 *
 * Requirements:
 * - Independent of Wake-Up Verification: triggers based on daily plan confirmation state.
 * - Displays on first eligible morning app launch (default 6:00–10:00 AM) or recovery fallback.
 * - Queues if a critical verification dialog is actively displaying.
 * - Step A: College attendance decision (YES — COLLEGE / NO — HOME).
 * - Step B: Displays ONLY the applicable schedule (College 2 baths vs Non-College 1 bath).
 * - Step C: Confirmation review before persisting.
 * - Prevents duplicate occurrences, duplicate popup triggers, and duplicate point transactions.
 * - Preserves existing timing formulas: planned -> 1h activity interval -> 90m delay -> 20m photo window.
 */
data class DailyBathPlan(
    val date: String,
    val timezone: String = "Asia/Kolkata",
    val collegeDecision: String, // "YES" or "NO"
    val confirmationStatus: String = "CONFIRMED", // "CONFIRMED" or "UNCONFIRMED"
    val revision: Int = 1,
    val selectedBathTimes: List<String>,
    val activityIntervals: List<String>,
    val reservedHourlyOccurrenceIds: List<String>,
    val photoSubmissionWindows: List<String>,
    val createdAt: String,
    val updatedAt: String
) {
    val isCollege: Boolean get() = collegeDecision.equals("YES", ignoreCase = true)

    fun toJson(): JSONObject = JSONObject().apply {
        put("date", date)
        put("timezone", timezone)
        put("college_decision", collegeDecision)
        put("confirmation_status", confirmationStatus)
        put("revision", revision)
        put("selected_bath_times", JSONArray(selectedBathTimes))
        put("activity_intervals", JSONArray(activityIntervals))
        put("reserved_hourly_act_ids", JSONArray(reservedHourlyOccurrenceIds))
        put("photo_submission_windows", JSONArray(photoSubmissionWindows))
        put("created_at", createdAt)
        put("updated_at", updatedAt)
    }

    companion object {
        fun fromJson(json: JSONObject): DailyBathPlan {
            val date = json.optString("date")
            val tz = json.optString("timezone", "Asia/Kolkata")
            val college = json.optString("college_decision", "NO")
            val status = json.optString("confirmation_status", "CONFIRMED")
            val rev = json.optInt("revision", 1)

            val times = mutableListOf<String>()
            val timesArr = json.optJSONArray("selected_bath_times")
            if (timesArr != null) {
                for (i in 0 until timesArr.length()) times.add(timesArr.getString(i))
            }

            val intervals = mutableListOf<String>()
            val intervalsArr = json.optJSONArray("activity_intervals")
            if (intervalsArr != null) {
                for (i in 0 until intervalsArr.length()) intervals.add(intervalsArr.getString(i))
            }

            val hourlyIds = mutableListOf<String>()
            val hourlyArr = json.optJSONArray("reserved_hourly_act_ids")
            if (hourlyArr != null) {
                for (i in 0 until hourlyArr.length()) hourlyIds.add(hourlyArr.getString(i))
            }

            val photoWindows = mutableListOf<String>()
            val photoArr = json.optJSONArray("photo_submission_windows")
            if (photoArr != null) {
                for (i in 0 until photoArr.length()) photoWindows.add(photoArr.getString(i))
            }

            val created = json.optString("created_at")
            val updated = json.optString("updated_at")

            return DailyBathPlan(
                date = date,
                timezone = tz,
                collegeDecision = college,
                confirmationStatus = status,
                revision = rev,
                selectedBathTimes = times,
                activityIntervals = intervals,
                reservedHourlyOccurrenceIds = hourlyIds,
                photoSubmissionWindows = photoWindows,
                createdAt = created,
                updatedAt = updated
            )
        }
    }
}

object BathScheduleMorningPopupRules {

    data class PopupTriggerResult(
        val shouldShow: Boolean,
        val shouldQueue: Boolean,
        val reason: String
    )

    data class ValidationResult(
        val isValid: Boolean,
        val errorMessage: String? = null
    )

    const val DEFAULT_MORNING_WINDOW_START_HOUR = 6
    const val DEFAULT_MORNING_WINDOW_END_HOUR = 10

    /**
     * Determines whether the First-Morning Bath Schedule Popup should be shown, queued, or suppressed.
     *
     * Rules:
     * 1. If today's plan is already confirmed, do not show the popup again automatically.
     * 2. If today's plan is unconfirmed, trigger on first eligible morning app launch (6:00–10:00 AM).
     * 3. If first opened later than morning window and unconfirmed, show as a recovery fallback.
     * 4. If a critical dialog (e.g. active Wake-Up verification screen) is active, queue it.
     * 5. Suppress duplicate popups if already displayed in this foreground session.
     * 6. Independent of Wake-Up Verification completion: incomplete, failed, skipped, or completed wake-up does NOT suppress bath popup.
     */
    fun shouldShowFirstMorningPopup(
        date: String,
        isConfirmed: Boolean,
        popupShownDate: String?,
        isCriticalDialogActive: Boolean,
        clockMs: Long,
        timezone: String = "Asia/Kolkata"
    ): PopupTriggerResult {
        if (isConfirmed) {
            return PopupTriggerResult(shouldShow = false, shouldQueue = false, reason = "ALREADY_CONFIRMED")
        }

        if (popupShownDate == date) {
            return PopupTriggerResult(shouldShow = false, shouldQueue = false, reason = "ALREADY_SHOWN_TODAY")
        }

        if (isCriticalDialogActive) {
            return PopupTriggerResult(shouldShow = false, shouldQueue = true, reason = "QUEUED_CRITICAL_DIALOG_ACTIVE")
        }

        val zone = TimeZone.getTimeZone(timezone)
        val cal = Calendar.getInstance(zone).apply { timeInMillis = clockMs }
        val hour = cal.get(Calendar.HOUR_OF_DAY)
        val minute = cal.get(Calendar.MINUTE)

        val isMorningWindow = (hour in DEFAULT_MORNING_WINDOW_START_HOUR until DEFAULT_MORNING_WINDOW_END_HOUR) ||
            (hour == DEFAULT_MORNING_WINDOW_END_HOUR && minute == 0)

        return if (isMorningWindow) {
            PopupTriggerResult(shouldShow = true, shouldQueue = false, reason = "MORNING_FIRST_OPEN")
        } else {
            // Recovery fallback if opened outside/later than morning window
            PopupTriggerResult(shouldShow = true, shouldQueue = false, reason = "RECOVERY_FALLBACK")
        }
    }

    /**
     * Validates Step A & B inputs.
     * Ensures college decision is made and planned times are within permitted ranges.
     * "Do not silently select a default bath time when the user has not made the required choice."
     */
    fun validateSelection(
        collegeDecision: String?,
        selectedTimes: List<String>?,
        windowStartOverride: String? = null,
        windowEndOverride: String? = null,
        sessionCount: Int? = null
    ): ValidationResult {
        if (collegeDecision.isNullOrBlank()) {
            return ValidationResult(isValid = false, errorMessage = "Please select whether you are attending college today.")
        }
        val cleanCollege = collegeDecision.uppercase(Locale.US).trim()
        if (cleanCollege !in listOf("YES", "NO")) {
            return ValidationResult(isValid = false, errorMessage = "College decision must be YES or NO.")
        }
        val isCollege = cleanCollege == "YES"
        val requiredCount = sessionCount?.coerceIn(1, 2) ?: (if (isCollege) 2 else 1)

        if (selectedTimes == null || selectedTimes.size < requiredCount) {
            val label = if (requiredCount == 2) "all 2 bath sessions" else "today's bath time"
            return ValidationResult(isValid = false, errorMessage = "Please select planned time for $label.")
        }

        for (i in 0 until requiredCount) {
            val t = selectedTimes[i]
            if (t.isBlank()) {
                val label = if (requiredCount == 2) "Bath #${i + 1}" else "Bath"
                return ValidationResult(isValid = false, errorMessage = "Please choose a planned time for $label.")
            }
            val (minStart, maxEnd) = if (isCollege) {
                if (i == 0) BathWindowRules.DEFAULT_COLLEGE_BATH1_WINDOW_START to BathWindowRules.DEFAULT_COLLEGE_BATH1_WINDOW_END
                else BathWindowRules.DEFAULT_COLLEGE_BATH2_WINDOW_START to BathWindowRules.DEFAULT_COLLEGE_BATH2_WINDOW_END
            } else {
                if (i == 0) (windowStartOverride ?: BathWindowRules.DEFAULT_NON_COLLEGE_WINDOW_START) to (windowEndOverride ?: BathWindowRules.DEFAULT_NON_COLLEGE_WINDOW_END)
                else "16:00" to "21:00"
            }
            if (!BathWindowRules.isValidPlannedBathTime(t, minStart, maxEnd)) {
                val label = if (requiredCount == 2) "Bath #${i + 1}" else "Bath"
                return ValidationResult(
                    isValid = false,
                    errorMessage = "$label time (${DisciplineScoringRules.formatTime12h(t)}) must fall within $minStart–$maxEnd."
                )
            }
        }
        return ValidationResult(isValid = true)
    }

    /**
     * Builds the Step C confirmation review structure showing:
     * - Today's date
     * - College or Non-College decision
     * - Planned bath time(s)
     * - Calculated one-hour activity interval
     * - Assigned hourly attendance slot under existing mapping policy
     * - Calculated photo-submission window and deadline
     */
    fun buildConfirmationReview(
        date: String,
        collegeDecision: String,
        selectedTimes: List<String>,
        timezone: String = "Asia/Kolkata",
        sessionCount: Int? = null
    ): JSONObject {
        val isCollege = collegeDecision.equals("YES", ignoreCase = true)
        val count = sessionCount?.coerceIn(1, 2) ?: (if (isCollege) 2 else (if (selectedTimes.size in 1..2) selectedTimes.size else 1))
        val items = JSONArray()

        for (i in 0 until count) {
            val planned = BathWindowRules.normalizeHhmm(selectedTimes.getOrNull(i) ?: if (i == 0) "07:30" else "16:00")
            val win = BathWindowRules.calculateBathWindow(
                occurrence = i + 1,
                isCollege = isCollege,
                date = date,
                plannedHhmm = planned,
                timezone = timezone
            )
            items.put(JSONObject().apply {
                put("occurrence", i + 1)
                put("title", win.title)
                put("planned_hhmm", win.plannedHhmm)
                put("planned_display", win.plannedDisplay)
                put("activity_interval", win.activityIntervalDisplay)
                put("held_hourly_slot", win.heldHourlySlot)
                put("held_hourly_slot_display", win.heldHourlySlotDisplay)
                put("held_hourly_act_id", win.heldHourlyActId)
                put("proof_window_display", win.proofWindowDisplay)
                put("proof_deadline_display", win.proofDeadlineDisplay)
                put("proof_deadline_iso", win.proofDeadlineIso)
            })
        }

        return JSONObject().apply {
            put("date", date)
            put("college_decision", if (isCollege) "YES" else "NO")
            put("college_decision_display", if (isCollege) "YES — ATTENDING COLLEGE ($count Bath${if (count > 1) "s" else ""} scheduled)" else "NO — STAYING HOME ($count Bath${if (count > 1) "s" else ""} scheduled)")
            put("baths", items)
        }
    }

    /**
     * Determines whether the user can edit today's bath plan.
     * Critical: Wake-Up Verification completion NEVER locks out bath plan editing!
     * Only completed/submitted bath proofs prevent re-planning.
     */
    fun canEditPlan(
        isWakeUpCompleted: Boolean,
        bathsSubmittedCount: Int,
        maxBaths: Int
    ): Pair<Boolean, String> {
        if (maxBaths > 0 && bathsSubmittedCount >= maxBaths) {
            return false to "Today's bath protocols have already been submitted ($bathsSubmittedCount/$maxBaths completed)."
        }
        return true to ""
    }
}

object BathSchedulePlanManager {
    fun validateSelection(
        date: String,
        collegeDecision: String,
        selectedTimes: List<String>,
        timezone: String = "Asia/Kolkata",
        sessionCount: Int = selectedTimes.size.coerceIn(1, 2)
    ): CollegeExamScheduleRules.ValidationResult {
        val cleanCollege = collegeDecision.uppercase(Locale.US).trim()
        if (cleanCollege !in listOf("YES", "NO")) {
            return CollegeExamScheduleRules.ValidationResult(false, "College decision must be YES or NO.")
        }
        return CollegeExamScheduleRules.validateIndependentBathSelection(
            sessionCount = sessionCount,
            selectedTimes = selectedTimes
        )
    }
}
