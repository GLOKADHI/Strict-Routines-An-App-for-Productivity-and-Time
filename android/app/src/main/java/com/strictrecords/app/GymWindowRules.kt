package com.strictrecords.app

import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Shared, deterministic Gym task scheduling, submission window rules,
 * workout reservation hold periods, and hourly attendance conflict suppression.
 *
 * Architecture:
 * - Expected Gym finish = planned start + expected duration (default: 90 minutes).
 * - Gym proof window opens at expected finish (or actual finish if early).
 * - Proof window duration is 30 minutes (start-inclusive, end-exclusive).
 * - Planned Gym session reserves the activity interval [plannedStart, expectedFinish).
 * - Hourly attendance affected: any slot whose activity interval [T - 60m, T) overlaps
 *   the Gym reservation interval [plannedStart, expectedFinish).
 * - When user finishes early and submits proof, the future portion of the Gym hold is released,
 *   allowing subsequent hourly slots to resume their normal submission schedule.
 */
object GymWindowRules {

    const val DEFAULT_GYM_DURATION_MINUTES = ProductPolicy.GYM_WORKOUT_MINUTES
    const val DEFAULT_GYM_PROOF_WINDOW_MINUTES = ProductPolicy.GYM_PROOF_MINUTES
    val SUPPORTED_DURATIONS = ProductPolicy.GYM_WORKOUT_DURATIONS

    data class GymWindow(
        val date: String,
        val plannedHhmm: String,
        val durationMinutes: Int = DEFAULT_GYM_DURATION_MINUTES,
        val plannedMs: Long,
        val expectedFinishMs: Long,
        val windowStartMs: Long,
        val windowEndMs: Long,
        val reminderMs: Long,
        val reservedStartMs: Long,
        val reservedEndMs: Long,
        val plannedIso: String,
        val expectedFinishIso: String,
        val windowStartIso: String,
        val windowEndIso: String,
        val notificationIso: String,
        val plannedDisplay: String,
        val expectedFinishDisplay: String,
        val windowDisplay: String,
        val hourSlot: String,
        val isEarlyFinish: Boolean = false,
        val actualFinishMs: Long? = null
    ) {
        val plannedHhmmClean: String get() {
            val parts = plannedHhmm.split(":")
            val h = parts.getOrNull(0)?.toIntOrNull() ?: 18
            val m = parts.getOrNull(1)?.toIntOrNull() ?: 0
            return String.format(Locale.US, "%02d%02d", h, m)
        }
    }

    /**
     * Normalizes a 12-hour or 24-hour time string into "HH:mm" 24-hour format.
     */
    fun normalizeHhmm(raw: String): String {
        val clean = raw.trim().uppercase(Locale.US)
        if (clean.isEmpty()) return DisciplineScoringRules.DEFAULT_GYM_TIME
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
            if (parts.size < 2) return DisciplineScoringRules.DEFAULT_GYM_TIME
            var h = parts[0].trim().toInt()
            val m = parts[1].trim().toInt()
            if (isAm || isPm) {
                if (h !in 1..12) return DisciplineScoringRules.DEFAULT_GYM_TIME
                if (isAm && h == 12) h = 0
                if (isPm && h != 12) h += 12
            }
            if (h !in 0..23 || m !in 0..59) return DisciplineScoringRules.DEFAULT_GYM_TIME
            String.format(Locale.US, "%02d:%02d", h, m)
        } catch (_: Exception) {
            DisciplineScoringRules.DEFAULT_GYM_TIME
        }
    }

    /**
     * Backward-compatible 3-parameter overload with default 90-minute duration.
     */
    fun calculateGymWindow(
        date: String,
        gymPlannedHhmm: String,
        timezone: String
    ): GymWindow {
        return calculateGymWindow(
            date = date,
            gymPlannedHhmm = gymPlannedHhmm,
            durationMinutes = DEFAULT_GYM_DURATION_MINUTES,
            timezone = timezone
        )
    }

    /**
     * Calculates the Gym planned workout timestamp, expected finish time, reserved interval,
     * and the 30-minute proof submission window.
     * Uses epoch milliseconds and Calendar for deterministic timezone & date rollover handling.
     */
    fun calculateGymWindow(
        date: String,
        gymPlannedHhmm: String,
        durationMinutes: Int = DEFAULT_GYM_DURATION_MINUTES,
        timezone: String = "Asia/Kolkata",
        actualFinishMs: Long? = null,
        isEarlyFinish: Boolean = false,
        proofWindowDurationMinutes: Int = DEFAULT_GYM_PROOF_WINDOW_MINUTES
    ): GymWindow {
        val norm = normalizeHhmm(gymPlannedHhmm)
        val parts = norm.split(":").map { it.toInt() }
        val hour = parts[0]
        val minute = parts[1]

        val zone = TimeZone.getTimeZone(timezone)
        val sdfDay = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply {
            timeZone = zone
            isLenient = false
        }
        val dayDate = try { sdfDay.parse(date) } catch (_: Exception) { Date() } ?: Date()

        val cal = Calendar.getInstance(zone).apply {
            time = dayDate
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        val plannedMs = cal.timeInMillis
        val safeDurationMinutes = if (durationMinutes > 0) durationMinutes else DEFAULT_GYM_DURATION_MINUTES
        val expectedFinishMs = plannedMs + safeDurationMinutes * 60_000L

        val reservedStartMs = plannedMs
        val reservedEndMs = if (isEarlyFinish && actualFinishMs != null && actualFinishMs >= plannedMs) {
            actualFinishMs
        } else {
            expectedFinishMs
        }

        val windowStartMs = if (isEarlyFinish && actualFinishMs != null && actualFinishMs >= plannedMs) {
            actualFinishMs
        } else {
            expectedFinishMs
        }
        val safeProofDuration = if (proofWindowDurationMinutes > 0) proofWindowDurationMinutes else DEFAULT_GYM_PROOF_WINDOW_MINUTES
        val windowEndMs = windowStartMs + safeProofDuration * 60_000L
        val reminderMs = windowStartMs - PRE_OPENING_REMINDER_MS

        val isoFormatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).apply {
            timeZone = zone
        }
        val plannedIso = isoFormatter.format(Date(plannedMs))
        val expectedFinishIso = isoFormatter.format(Date(expectedFinishMs))
        val windowStartIso = isoFormatter.format(Date(windowStartMs))
        val windowEndIso = isoFormatter.format(Date(windowEndMs))
        val notificationIso = isoFormatter.format(Date(reminderMs))

        val displayFormatter = SimpleDateFormat("hh:mm a", Locale.US).apply {
            timeZone = zone
        }
        val plannedDisplay = displayFormatter.format(Date(plannedMs))
        val expectedFinishDisplay = displayFormatter.format(Date(expectedFinishMs))
        val startDisplay = displayFormatter.format(Date(windowStartMs))
        val endDisplay = displayFormatter.format(Date(windowEndMs))
        val windowDisplay = "$startDisplay–$endDisplay"

        val time24Formatter = SimpleDateFormat("HH:mm", Locale.US).apply {
            timeZone = zone
        }
        val hourSlot = "${time24Formatter.format(Date(windowStartMs))}-${time24Formatter.format(Date(windowEndMs))}"

        return GymWindow(
            date = date,
            plannedHhmm = norm,
            durationMinutes = safeDurationMinutes,
            plannedMs = plannedMs,
            expectedFinishMs = expectedFinishMs,
            windowStartMs = windowStartMs,
            windowEndMs = windowEndMs,
            reminderMs = reminderMs,
            reservedStartMs = reservedStartMs,
            reservedEndMs = reservedEndMs,
            plannedIso = plannedIso,
            expectedFinishIso = expectedFinishIso,
            windowStartIso = windowStartIso,
            windowEndIso = windowEndIso,
            notificationIso = notificationIso,
            plannedDisplay = plannedDisplay,
            expectedFinishDisplay = expectedFinishDisplay,
            windowDisplay = windowDisplay,
            hourSlot = hourSlot,
            isEarlyFinish = isEarlyFinish,
            actualFinishMs = actualFinishMs
        )
    }

    /**
     * Five-minute pre-opening reminder duration.
     */
    const val PRE_OPENING_REMINDER_MS = 5 * 60 * 1000L

    const val STATE_UPCOMING = "UPCOMING"
    const val STATE_REMINDER_VISIBLE = "REMINDER_VISIBLE"
    const val STATE_OPEN = "OPEN"
    const val STATE_SUBMITTED = "SUBMITTED"
    const val STATE_APPROVED = "APPROVED"
    const val STATE_REJECTED = "REJECTED"
    const val STATE_EXPIRED = "EXPIRED"
    const val STATE_CLOSED = "CLOSED"

    /**
     * Start-inclusive and end-exclusive submission eligibility:
     * gymWindowStart <= currentTime < gymWindowEnd
     */
    fun isSubmissionWithinWindow(submissionMs: Long, windowStartMs: Long, windowEndMs: Long): Boolean {
        return submissionMs in windowStartMs until windowEndMs
    }

    /**
     * Evaluates window state:
     * UPCOMING -> REMINDER_VISIBLE (5m before start) -> OPEN -> CLOSED
     */
    fun state(nowMs: Long, windowStartMs: Long, windowEndMs: Long): String = when {
        nowMs >= windowEndMs -> STATE_CLOSED
        nowMs >= windowStartMs -> STATE_OPEN
        nowMs >= windowStartMs - PRE_OPENING_REMINDER_MS -> STATE_REMINDER_VISIBLE
        else -> STATE_UPCOMING
    }

    /**
     * Evaluates occurrence state respecting existing submissions and review status.
     */
    fun resolveOccurrenceState(
        currentStatus: String,
        nowMs: Long,
        windowStartMs: Long,
        windowEndMs: Long
    ): String {
        val s = currentStatus.uppercase(Locale.US)
        if (s in listOf("VERIFIED", "COMPLETED", "APPROVED")) return STATE_APPROVED
        if (s in listOf("REJECTED", "REJECTED_LATE")) return STATE_REJECTED
        if (s in listOf("SUBMITTED", "VALIDATING", "PENDING_SYNC", "AI_PROVISIONAL", "PENDING_REVIEW")) return STATE_SUBMITTED
        if (s in listOf("SKIPPED", "EXCUSED", "CANCELLED")) return s
        if (s in listOf("MISSED", "EXPIRED", "CLOSED")) return STATE_EXPIRED

        return when {
            nowMs >= windowEndMs -> STATE_EXPIRED
            nowMs >= windowStartMs -> STATE_OPEN
            nowMs >= windowStartMs - PRE_OPENING_REMINDER_MS -> STATE_REMINDER_VISIBLE
            else -> STATE_UPCOMING
        }
    }

    /**
     * Determines whether the Quick Action should be visible on the dashboard.
     * Visible during REMINDER_VISIBLE and OPEN states, unless already completed/submitted.
     */
    fun isQuickActionVisible(
        currentStatus: String,
        nowMs: Long,
        windowStartMs: Long,
        windowEndMs: Long
    ): Boolean {
        val resolved = resolveOccurrenceState(currentStatus, nowMs, windowStartMs, windowEndMs)
        return resolved == STATE_REMINDER_VISIBLE || resolved == STATE_OPEN
    }

    /**
     * Checks if an hourly slot's 1-hour activity interval [slotTargetMs - 60m, slotTargetMs)
     * overlaps with the Gym reservation interval [gymReservedStartMs, gymReservedEndMs).
     */
    fun isHourlySlotAffectedByGym(
        slotTargetMs: Long,
        gymReservedStartMs: Long,
        gymReservedEndMs: Long
    ): Boolean {
        val slotStartMs = slotTargetMs - 3600_000L
        val slotEndMs = slotTargetMs
        return maxOf(slotStartMs, gymReservedStartMs) < minOf(slotEndMs, gymReservedEndMs)
    }

    /**
     * Checks if an hourly slot's interval [slotStartMs, slotEndMs)
     * overlaps with the Gym reservation interval [gymReservedStartMs, gymReservedEndMs).
     */
    fun isHourlySlotAffectedByGym(
        slotStartMs: Long,
        slotEndMs: Long,
        gymReservedStartMs: Long,
        gymReservedEndMs: Long
    ): Boolean {
        return maxOf(slotStartMs, gymReservedStartMs) < minOf(slotEndMs, gymReservedEndMs)
    }

    /**
     * Overlap condition between the 20-minute hourly submission window [hourlyWindowStartMs, hourlyWindowEndMs)
     * and the reserved Gym activity period [gymReservedStartMs, gymReservedEndMs):
     */
    fun isHourlyOverlappingGym(
        hourlyWindowStartMs: Long,
        hourlyWindowEndMs: Long,
        gymReservedStartMs: Long,
        gymReservedEndMs: Long
    ): Boolean {
        return maxOf(hourlyWindowStartMs, gymReservedStartMs) < minOf(hourlyWindowEndMs, gymReservedEndMs)
    }

    /**
     * Checks if the 20-minute hourly checkpoint window [hStartMs, hEndMs] overlaps
     * with the Gym PROOF window [gymWindowStartMs, gymWindowEndMs].
     */
    fun conflictsWithHourly(
        hourlyTargetMs: Long,
        gymWindowStartMs: Long,
        gymWindowEndMs: Long
    ): Boolean {
        val hStartMs = hourlyTargetMs - 10 * 60 * 1000L
        val hEndMs = hourlyTargetMs + 10 * 60 * 1000L
        return maxOf(hStartMs, gymWindowStartMs) < minOf(hEndMs, gymWindowEndMs)
    }

    /**
     * Checks whether an hourly checkpoint minute-of-day overlaps with the Gym proof window.
     * Preserves backward compatibility for legacy 2-arg calls: workout=120m, proof=60m.
     */
    fun conflictsWithHourlyMinutes(
        hourlyMinuteOfDay: Int,
        gymPlannedMinuteOfDay: Int
    ): Boolean {
        val hStart = hourlyMinuteOfDay - 10
        val hEnd = hourlyMinuteOfDay + 10
        val gymProofStart = gymPlannedMinuteOfDay + 120
        val gymProofEnd = gymProofStart + 60
        return maxOf(hStart, gymProofStart) < minOf(hEnd, gymProofEnd)
    }

    fun conflictsWithHourlyMinutes(
        hourlyMinuteOfDay: Int,
        gymPlannedMinuteOfDay: Int,
        gymDurationMinutes: Int,
        proofWindowMinutes: Int = DEFAULT_GYM_PROOF_WINDOW_MINUTES
    ): Boolean {
        val hStart = hourlyMinuteOfDay - 10
        val hEnd = hourlyMinuteOfDay + 10
        val gymProofStart = gymPlannedMinuteOfDay + gymDurationMinutes
        val gymProofEnd = gymProofStart + proofWindowMinutes
        return maxOf(hStart, gymProofStart) < minOf(hEnd, gymProofEnd)
    }
}
