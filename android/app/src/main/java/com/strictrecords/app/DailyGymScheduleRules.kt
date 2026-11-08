package com.strictrecords.app

import org.json.JSONObject
import java.util.Locale

/**
 * Deterministic business rules and models for the conditional first-time daily Gym Schedule Setup.
 *
 * Implements:
 * 1. Initial question: "Are you going to the gym today?" -> Yes / No.
 * 2. If YES: reveals planned start time, planned duration, estimated end time, and validation against
 *    schedule conflicts (Bath sessions, College hours, Wake-up windows).
 * 3. If NO: hides timing/duration, records choice as NO_GYM, creates no Gym session.
 * 4. Preserves underlying alternate-day Gym Routine history, anchor, and pause states.
 * 5. Separates morning planning from after-5-PM Gym Mode activation and verification rules.
 * 6. Persists and restores setup under authenticated user and local date (Asia/Kolkata).
 */
object DailyGymScheduleRules {

    const val TIMEZONE = "Asia/Kolkata"
    const val DEFAULT_GYM_START_TIME = "18:00"
    const val DEFAULT_GYM_DURATION_MINUTES = ProductPolicy.GYM_WORKOUT_MINUTES // 90
    val SUPPORTED_DURATIONS = ProductPolicy.GYM_WORKOUT_DURATIONS // [45, 60, 75, 90, 120]

    enum class DailyGymChoice {
        UNSET,
        YES,
        NO_GYM
    }

    data class DailyGymSetup(
        val date: String,
        val choice: DailyGymChoice,
        val plannedStartTime: String? = null,
        val plannedDurationMinutes: Int = DEFAULT_GYM_DURATION_MINUTES,
        val plannedEndTime: String? = null,
        val isConfirmed: Boolean = false,
        val confirmedAtMs: Long = 0L,
        val timezone: String = TIMEZONE,
        val routineVersion: Int = 1,
        val policyVersion: Int = 1
    ) {
        val isGymPlanned: Boolean get() = choice == DailyGymChoice.YES
        val isNoGym: Boolean get() = choice == DailyGymChoice.NO_GYM
    }

    data class GymValidationResult(
        val isValid: Boolean,
        val errorMessage: String? = null,
        val conflictType: String? = null
    )

    fun calculateEndTime(startTimeHhmm: String, durationMinutes: Int): String {
        val parts = startTimeHhmm.trim().split(":")
        val h = parts.getOrNull(0)?.toIntOrNull() ?: 18
        val m = parts.getOrNull(1)?.toIntOrNull() ?: 0
        val totalMins = (h * 60 + m + durationMinutes) % 1440
        val endH = totalMins / 60
        val endM = totalMins % 60
        return String.format(Locale.US, "%02d:%02d", endH, endM)
    }

    private fun parseMinutes(hhmm: String?): Int? {
        if (hhmm.isNullOrBlank()) return null
        val parts = hhmm.trim().split(":")
        if (parts.size < 2) return null
        val h = parts[0].toIntOrNull() ?: return null
        val m = parts[1].toIntOrNull() ?: return null
        if (h !in 0..23 || m !in 0..59) return null
        return h * 60 + m
    }

    private fun intervalsOverlap(start1: Int, end1: Int, start2: Int, end2: Int): Boolean {
        return maxOf(start1, start2) < minOf(end1, end2)
    }

    /**
     * Validates user selection and checks for schedule conflicts with college, bath, and wake-up.
     */
    fun validateGymTiming(
        choice: DailyGymChoice,
        plannedStartTime: String?,
        durationMinutes: Int,
        attendsCollege: Boolean = false,
        collegeStartHhmm: String? = null,
        collegeEndHhmm: String? = null,
        bathTimes: List<String> = emptyList(),
        wakeUpTargetHhmm: String? = null
    ): GymValidationResult {
        when (choice) {
            DailyGymChoice.UNSET -> {
                return GymValidationResult(
                    isValid = false,
                    errorMessage = "Please answer: Are you going to the gym today?",
                    conflictType = "UNSET"
                )
            }
            DailyGymChoice.NO_GYM -> {
                // Selecting NO requires no timing or duration; always valid
                return GymValidationResult(isValid = true)
            }
            DailyGymChoice.YES -> {
                if (plannedStartTime.isNullOrBlank()) {
                    return GymValidationResult(
                        isValid = false,
                        errorMessage = "Please select a planned gym start time.",
                        conflictType = "MISSING_TIME"
                    )
                }

                val gymStartMins = parseMinutes(plannedStartTime)
                if (gymStartMins == null) {
                    return GymValidationResult(
                        isValid = false,
                        errorMessage = "Invalid gym start time format: $plannedStartTime",
                        conflictType = "INVALID_FORMAT"
                    )
                }

                if (durationMinutes !in 30..240) {
                    return GymValidationResult(
                        isValid = false,
                        errorMessage = "Gym duration must be between 30 and 240 minutes.",
                        conflictType = "INVALID_DURATION"
                    )
                }

                val gymEndMins = gymStartMins + durationMinutes
                val calculatedEnd = calculateEndTime(plannedStartTime, durationMinutes)

                // 1. Conflict check: College hours
                if (attendsCollege) {
                    val colStart = parseMinutes(collegeStartHhmm)
                    val colEnd = parseMinutes(collegeEndHhmm)
                    if (colStart != null && colEnd != null && colStart < colEnd) {
                        if (intervalsOverlap(gymStartMins, gymEndMins, colStart, colEnd)) {
                            return GymValidationResult(
                                isValid = false,
                                errorMessage = "Planned gym session ($plannedStartTime–$calculatedEnd) overlaps with College hours ($collegeStartHhmm–$collegeEndHhmm).",
                                conflictType = "COLLEGE_CONFLICT"
                            )
                        }
                    }
                }

                // 2. Conflict check: Bath sessions
                for (bTime in bathTimes) {
                    val bMins = parseMinutes(bTime) ?: continue
                    // Bath activity window is [bMins - 15, bMins + 15]
                    val bActStart = maxOf(0, bMins - 15)
                    val bActEnd = minOf(1440, bMins + 15)
                    if (intervalsOverlap(gymStartMins, gymEndMins, bActStart, bActEnd)) {
                        return GymValidationResult(
                            isValid = false,
                            errorMessage = "Planned gym session ($plannedStartTime–$calculatedEnd) overlaps with Bath session ($bTime).",
                            conflictType = "BATH_CONFLICT"
                        )
                    }
                }

                // 3. Conflict check: Wake-up proof window
                if (!wakeUpTargetHhmm.isNullOrBlank()) {
                    val wMins = parseMinutes(wakeUpTargetHhmm)
                    if (wMins != null) {
                        val wStart = maxOf(0, wMins - 10)
                        val wEnd = minOf(1440, wMins + 10)
                        if (intervalsOverlap(gymStartMins, gymEndMins, wStart, wEnd)) {
                            return GymValidationResult(
                                isValid = false,
                                errorMessage = "Planned gym session ($plannedStartTime–$calculatedEnd) overlaps with Wake-Up window ($wakeUpTargetHhmm).",
                                conflictType = "WAKEUP_CONFLICT"
                            )
                        }
                    }
                }

                return GymValidationResult(isValid = true)
            }
        }
    }

    fun isDateChanged(savedDate: String?, currentDate: String): Boolean {
        if (savedDate.isNullOrBlank()) return true
        return savedDate.trim() != currentDate.trim()
    }

    fun toJson(setup: DailyGymSetup): JSONObject = JSONObject().apply {
        put("date", setup.date)
        put("choice", setup.choice.name)
        put("daily_choice", setup.choice.name)
        put("is_gym_planned", setup.isGymPlanned)
        put("planned_start_time", setup.plannedStartTime.orEmpty())
        put("planned_duration_minutes", setup.plannedDurationMinutes)
        put("planned_end_time", setup.plannedEndTime.orEmpty())
        put("is_confirmed", setup.isConfirmed)
        put("confirmed_at_ms", setup.confirmedAtMs)
        put("timezone", setup.timezone)
        put("routine_version", setup.routineVersion)
        put("policy_version", setup.policyVersion)
    }

    fun fromJson(json: JSONObject): DailyGymSetup {
        val choiceStr = json.optString("daily_choice", json.optString("choice", "UNSET"))
        val choice = when (choiceStr.uppercase(Locale.US)) {
            "YES", "GYM_PLANNED" -> DailyGymChoice.YES
            "NO", "NO_GYM", "GYM_NOT_PLANNED" -> DailyGymChoice.NO_GYM
            else -> DailyGymChoice.UNSET
        }
        val start = json.optString("planned_start_time").takeIf { it.isNotBlank() }
        val dur = json.optInt("planned_duration_minutes", DEFAULT_GYM_DURATION_MINUTES).let { if (it > 0) it else DEFAULT_GYM_DURATION_MINUTES }
        val end = json.optString("planned_end_time").takeIf { it.isNotBlank() }
            ?: start?.let { calculateEndTime(it, dur) }

        return DailyGymSetup(
            date = json.optString("date"),
            choice = choice,
            plannedStartTime = start,
            plannedDurationMinutes = dur,
            plannedEndTime = end,
            isConfirmed = json.optBoolean("is_confirmed", false) || json.optString("confirmation_status") == "CONFIRMED",
            confirmedAtMs = json.optLong("confirmed_at_ms", 0L),
            timezone = json.optString("timezone", TIMEZONE),
            routineVersion = json.optInt("routine_version", 1),
            policyVersion = json.optInt("policy_version", 1)
        )
    }
}
