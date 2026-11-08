package com.strictrecords.app

import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Deterministic rules and state transitions for the 10:00 AM daily schedule deadline.
 */
object ScheduleDeadlineRules {

    const val DEADLINE_HOUR = 10
    const val DEADLINE_MINUTE = 0
    const val DEFAULT_TIMEZONE = "Asia/Kolkata"

    enum class ScheduleDeadlineState {
        ON_TIME_CONFIRMED,
        OVERDUE_UNCONFIRMED,
        COMPLETION_REQUIRED,
        SUBMISSION_IN_PROGRESS,
        SAVE_FAILED,
        CONFIRMED_AND_RELEASED
    }

    enum class ScheduleConfirmationState {
        DRAFT,
        VALIDATING,
        SAVING_LOCALLY,
        PENDING_SYNC,
        SUBMITTING,
        AWAITING_ACKNOWLEDGEMENT,
        CONFIRMED,
        SUBMITTED_LATE,
        RETRY_REQUIRED,
        FAILED;

        val isProgress: Boolean
            get() = this in listOf(VALIDATING, SAVING_LOCALLY, PENDING_SYNC, SUBMITTING, AWAITING_ACKNOWLEDGEMENT)

        val isTerminalSuccess: Boolean
            get() = this == CONFIRMED || this == SUBMITTED_LATE

        fun displayStatus(): String = when (this) {
            DRAFT -> "DRAFT"
            VALIDATING -> "VALIDATING SCHEDULE..."
            SAVING_LOCALLY -> "SAVING LOCALLY..."
            PENDING_SYNC -> "PREPARING SYNC..."
            SUBMITTING -> "SUBMITTING TO SERVER..."
            AWAITING_ACKNOWLEDGEMENT -> "FINALIZING CONFIRMATION..."
            CONFIRMED -> "SCHEDULE CONFIRMED ✓"
            SUBMITTED_LATE -> "SCHEDULE SUBMITTED (LATE) ✓"
            RETRY_REQUIRED -> "CONFIRMATION TIMED OUT"
            FAILED -> "CONFIRMATION FAILED"
        }
    }

    sealed class ScheduleDeadlineEvent {
        object DeadlinePassed : ScheduleDeadlineEvent()
        object DeviceUnlocked : ScheduleDeadlineEvent()
        object AppOpened : ScheduleDeadlineEvent()
        object SubmissionStarted : ScheduleDeadlineEvent()
        data class SubmissionFailed(val reason: String) : ScheduleDeadlineEvent()
        object SubmissionVerifiedAndConfirmed : ScheduleDeadlineEvent()
        object ResetToDraft : ScheduleDeadlineEvent()
    }

    data class ValidationResult(
        val isValid: Boolean,
        val errorMessage: String? = null
    )

    data class ScheduleSubmissionData(
        val date: String,
        val collegeAttendance: String?, // "YES" or "NO"
        val collegeStartHhmm: String? = null,
        val collegeEndHhmm: String? = null,
        val hasExam: Boolean = false,
        val examStartHhmm: String? = null,
        val examEndHhmm: String? = null,
        val bathSessionCount: Int = 1,
        val bathTimes: List<String> = emptyList(),
        val wakeUpTargetHhmm: String = "07:00",
        val gymTargetHhmm: String = "18:00",
        val gymDurationMinutes: Int = 90,
        val gymEnabled: Boolean = true,
        val gymRequiredByAdmin: Boolean = false
    )

    fun getDeadlineMs(
        dateStr: String,
        timezone: String = DEFAULT_TIMEZONE,
        deadlineHour: Int = DEADLINE_HOUR,
        deadlineMinute: Int = DEADLINE_MINUTE
    ): Long {
        val cal = Calendar.getInstance(TimeZone.getTimeZone(timezone), Locale.US)
        val parts = dateStr.split("-").mapNotNull { it.toIntOrNull() }
        if (parts.size == 3) {
            cal.set(Calendar.YEAR, parts[0])
            cal.set(Calendar.MONTH, parts[1] - 1)
            cal.set(Calendar.DAY_OF_MONTH, parts[2])
        }
        cal.set(Calendar.HOUR_OF_DAY, deadlineHour)
        cal.set(Calendar.MINUTE, deadlineMinute)
        cal.set(Calendar.SECOND, 0)
        cal.set(Calendar.MILLISECOND, 0)
        return cal.timeInMillis
    }

    fun isPastDeadline(
        nowMs: Long,
        dateStr: String,
        timezone: String = DEFAULT_TIMEZONE,
        deadlineHour: Int = DEADLINE_HOUR,
        deadlineMinute: Int = DEADLINE_MINUTE
    ): Boolean {
        val deadlineMs = getDeadlineMs(dateStr, timezone, deadlineHour, deadlineMinute)
        return nowMs >= deadlineMs
    }

    /**
     * Authoritative evaluation of schedule state for the given date.
     * Note: A confirmed schedule for yesterday (or any other date) does NOT satisfy today.
     */
    fun evaluateState(
        isConfirmedForDate: Boolean,
        nowMs: Long,
        dateStr: String,
        timezone: String = DEFAULT_TIMEZONE,
        currentState: ScheduleDeadlineState? = null
    ): ScheduleDeadlineState {
        if (isConfirmedForDate) {
            val deadlineMs = getDeadlineMs(dateStr, timezone)
            return if (nowMs < deadlineMs) {
                ScheduleDeadlineState.ON_TIME_CONFIRMED
            } else {
                ScheduleDeadlineState.CONFIRMED_AND_RELEASED
            }
        }

        val pastDeadline = isPastDeadline(nowMs, dateStr, timezone)
        if (!pastDeadline) {
            // Before 10:00 AM on dateStr: normal flow, not overdue yet
            return ScheduleDeadlineState.ON_TIME_CONFIRMED
        }

        // At or after 10:00 AM and unconfirmed
        return when (currentState) {
            ScheduleDeadlineState.SUBMISSION_IN_PROGRESS -> ScheduleDeadlineState.SUBMISSION_IN_PROGRESS
            ScheduleDeadlineState.SAVE_FAILED -> ScheduleDeadlineState.SAVE_FAILED
            ScheduleDeadlineState.COMPLETION_REQUIRED -> ScheduleDeadlineState.COMPLETION_REQUIRED
            else -> ScheduleDeadlineState.OVERDUE_UNCONFIRMED
        }
    }

    /**
     * Deterministic state transitions.
     */
    fun transition(
        currentState: ScheduleDeadlineState,
        event: ScheduleDeadlineEvent,
        isConfirmed: Boolean,
        isPastDeadline: Boolean
    ): ScheduleDeadlineState {
        if (isConfirmed) {
            return ScheduleDeadlineState.CONFIRMED_AND_RELEASED
        }

        return when (event) {
            is ScheduleDeadlineEvent.DeadlinePassed -> {
                if (isPastDeadline) ScheduleDeadlineState.OVERDUE_UNCONFIRMED else currentState
            }
            is ScheduleDeadlineEvent.DeviceUnlocked,
            is ScheduleDeadlineEvent.AppOpened -> {
                if (isPastDeadline) ScheduleDeadlineState.COMPLETION_REQUIRED else ScheduleDeadlineState.ON_TIME_CONFIRMED
            }
            is ScheduleDeadlineEvent.SubmissionStarted -> {
                if (currentState == ScheduleDeadlineState.SUBMISSION_IN_PROGRESS) {
                    currentState // reject duplicate concurrent trigger
                } else {
                    ScheduleDeadlineState.SUBMISSION_IN_PROGRESS
                }
            }
            is ScheduleDeadlineEvent.SubmissionFailed -> {
                ScheduleDeadlineState.SAVE_FAILED
            }
            is ScheduleDeadlineEvent.SubmissionVerifiedAndConfirmed -> {
                ScheduleDeadlineState.CONFIRMED_AND_RELEASED
            }
            is ScheduleDeadlineEvent.ResetToDraft -> {
                if (isPastDeadline) ScheduleDeadlineState.COMPLETION_REQUIRED else ScheduleDeadlineState.ON_TIME_CONFIRMED
            }
        }
    }

    /**
     * Validates all required schedule completion fields.
     */
    fun validateScheduleSubmission(data: ScheduleSubmissionData): ValidationResult {
        // 1. College attendance check
        val college = data.collegeAttendance?.trim()?.uppercase(Locale.US)
        if (college.isNullOrBlank() || college !in listOf("YES", "NO")) {
            return ValidationResult(false, "College attendance decision is required: Please select YES or NO.")
        }

        if (college == "YES") {
            val cVal = CollegeExamScheduleRules.validateCollegeTimes(data.collegeStartHhmm, data.collegeEndHhmm)
            if (!cVal.isValid) {
                return ValidationResult(false, cVal.errorMessage ?: "Invalid college times.")
            }
            if (data.hasExam) {
                val eVal = CollegeExamScheduleRules.validateExamTimes(
                    data.collegeStartHhmm,
                    data.collegeEndHhmm,
                    data.examStartHhmm,
                    data.examEndHhmm
                )
                if (!eVal.isValid) {
                    return ValidationResult(false, eVal.errorMessage ?: "Invalid exam times.")
                }
            }
        }

        // 2. Bath sessions check
        if (data.bathSessionCount !in 1..2) {
            return ValidationResult(false, "Bath sessions count must be 1 or 2.")
        }
        if (data.bathTimes.size < data.bathSessionCount) {
            return ValidationResult(false, "Please select planned time for all required bath sessions.")
        }
        val bathTimesToCheck = data.bathTimes.take(data.bathSessionCount)
        val bVal = CollegeExamScheduleRules.validateIndependentBathSelection(data.bathSessionCount, bathTimesToCheck)
        if (!bVal.isValid) {
            return ValidationResult(false, bVal.errorMessage ?: "Invalid bath session times.")
        }

        // 3. Wake-up target check (Wake-Up Target is removed from schedule configuration and managed independently)
        if (data.wakeUpTargetHhmm.isNotBlank()) {
            val (wakeValid, wakeErr) = DisciplineScoringRules.validateWakeUpTime(data.wakeUpTargetHhmm)
            if (!wakeValid) {
                return ValidationResult(false, wakeErr)
            }
        }

        // 4. Gym check (First step in schedule configuration)
        if (data.gymRequiredByAdmin && !data.gymEnabled) {
            return ValidationResult(false, "Gym task is required by administrator policy and cannot be disabled.")
        }
        if (data.gymEnabled) {
            if (data.gymTargetHhmm.isBlank()) {
                return ValidationResult(false, "Gym target time is required.")
            }
            if (data.gymDurationMinutes <= 0) {
                return ValidationResult(false, "Gym duration must be greater than 0 minutes.")
            }
        }

        return ValidationResult(true)
    }
}
