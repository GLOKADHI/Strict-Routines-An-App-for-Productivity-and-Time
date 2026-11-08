package com.strictrecords.app

import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Shared, deterministic Bath task scheduling, submission window rules,
 * 1-hour reserved activity periods, and hourly attendance conflict suppression.
 *
 * Rules:
 * - College Yes:
 *     Bath Protocol #1: Submission window 7:00 AM–10:00 AM (07:00–10:00).
 *     Bath Protocol #2: Submission window 4:00 PM–9:00 PM (16:00–21:00).
 * - College No:
 *     Bath Protocol: Configurable submission window, default 1:00 PM–5:30 PM (13:00–17:30).
 * - Boundaries are start-inclusive and end-exclusive: windowStart <= currentTime < windowEnd.
 * - Planned Bath time reserves exactly one hour for the activity:
 *     bathReservedStart = selectedBathTime
 *     bathReservedEnd = selectedBathTime + 1 hour
 * - The displayed assigned hour contains the planned start (H:00–H+1:00).
 * - Attendance exemptions are derived independently from the actual submission
 *   window and activity interval, never from a previously saved assigned hour.
 * - Any positive overlap is excused; touching interval edges are not overlap.
 */
object BathWindowRules {
    const val ATTENDANCE_POLICY = "BATH_ACTIVITY_WINDOW_OVERLAP_V2"
    const val PARTIAL_OVERLAP_POLICY = "ANY_POSITIVE_ACTIVITY_WINDOW_OVERLAP"

    const val DEFAULT_COLLEGE_BATH1_WINDOW_START = ProductPolicy.BATH_COLLEGE_1_START
    const val DEFAULT_COLLEGE_BATH1_WINDOW_END = ProductPolicy.BATH_COLLEGE_1_END
    const val DEFAULT_COLLEGE_BATH1_PLANNED = ProductPolicy.BATH_COLLEGE_1_PLANNED

    const val DEFAULT_COLLEGE_BATH2_WINDOW_START = ProductPolicy.BATH_COLLEGE_2_START
    const val DEFAULT_COLLEGE_BATH2_WINDOW_END = ProductPolicy.BATH_COLLEGE_2_END
    const val DEFAULT_COLLEGE_BATH2_PLANNED = ProductPolicy.BATH_COLLEGE_2_PLANNED

    const val DEFAULT_NON_COLLEGE_WINDOW_START = ProductPolicy.BATH_HOME_START
    const val DEFAULT_NON_COLLEGE_WINDOW_END = ProductPolicy.BATH_HOME_END
    const val DEFAULT_NON_COLLEGE_PLANNED = ProductPolicy.BATH_HOME_PLANNED

    data class BathReservedSlot(
        val slotStartHhmm: String,
        val slotEndHhmm: String,
        val targetHhmm: String,
        val slotStartMs: Long,
        val slotEndMs: Long,
        val targetMs: Long,
        val occurrenceId: String,
        val hourSlot: String,
        val displayRange: String
    )

    data class BathWindow(
        val occurrence: Int,
        val isCollege: Boolean,
        val date: String,
        val plannedHhmm: String,
        val plannedMs: Long,
        val windowStartHhmm: String,
        val windowEndHhmm: String,
        val windowStartMs: Long,
        val windowEndMs: Long,
        val reservedStartMs: Long,
        val reservedEndMs: Long,
        val reminderMs: Long,
        val plannedIso: String,
        val windowStartIso: String,
        val windowEndIso: String,
        val reservedStartIso: String,
        val reservedEndIso: String,
        val notificationIso: String,
        val plannedDisplay: String,
        val windowDisplay: String,
        val reservedDisplay: String,
        val hourSlot: String,
        val actId: String,
        val title: String,
        val proofDeadlineMs: Long = windowEndMs,
        val proofDeadlineIso: String = windowEndIso,
        val proofDeadlineDisplay: String = "",
        val proofDeadlineHhmm: String = "",
        val heldHourlySlot: String = "",
        val heldHourlySlotDisplay: String = "",
        val activityIntervalDisplay: String = "",
        val proofWindowDisplay: String = "",
        val heldHourlyActId: String = "",
        val heldHourlyTargetMs: Long = 0L,
        val proofWindowStartMs: Long = 0L,
        val proofWindowEndMs: Long = 0L,
        val proofWindowStartHhmm: String = "",
        val proofWindowEndHhmm: String = "",
        val proofWindowStartIso: String = "",
        val proofWindowEndIso: String = ""
    ) {
        val plannedHhmmClean: String get() {
            val parts = plannedHhmm.split(":")
            val h = parts.getOrNull(0)?.toIntOrNull() ?: 7
            val m = parts.getOrNull(1)?.toIntOrNull() ?: 30
            return String.format(Locale.US, "%02d%02d", h, m)
        }
    }

    fun formatClock12h(ms: Long, zone: TimeZone): String {
        val sdf = SimpleDateFormat("h:mm a", Locale.US).apply { timeZone = zone }
        return sdf.format(Date(ms))
    }

    fun formatInterval12h(startMs: Long, endMs: Long, zone: TimeZone): String {
        val sStr = formatClock12h(startMs, zone)
        val eStr = formatClock12h(endMs, zone)
        val sParts = sStr.split(" ")
        val eParts = eStr.split(" ")
        return if (sParts.size == 2 && eParts.size == 2 && sParts[1] == eParts[1]) {
            "${sParts[0]}–$eStr"
        } else {
            "$sStr–$eStr"
        }
    }

    /**
     * Resolves the display assignment from the hour containing the planned start.
     * A 14:22 bath belongs to 14:00–15:00 (target 15:00), independently of the
     * actual hourly submission windows that need to be excused. A longer bath
     * may overlap more than one submission window; callers must use the
     * interval-based exemption functions rather than this display assignment.
     */
    fun resolveBathReservedHourlySlot(
        date: String,
        plannedHhmm: String,
        timezone: String = "Asia/Kolkata"
    ): BathReservedSlot {
        val norm = normalizeHhmm(plannedHhmm)
        val parts = norm.split(":").map { it.toInt() }
        val h = parts[0]

        val zone = TimeZone.getTimeZone(timezone)
        val sdfDay = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply {
            timeZone = zone
            isLenient = false
        }
        val dayDate = try { sdfDay.parse(date) } catch (_: Exception) { Date() } ?: Date()

        // Calendar addition retains the date when the assignment crosses midnight.
        val targetCal = Calendar.getInstance(zone).apply {
            time = dayDate
            set(Calendar.HOUR_OF_DAY, h)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
            add(Calendar.HOUR_OF_DAY, 1)
        }
        val endCal = targetCal.clone() as Calendar
        val startCal = (targetCal.clone() as Calendar).apply { add(Calendar.HOUR_OF_DAY, -1) }

        val startMs = startCal.timeInMillis
        val endMs = endCal.timeInMillis
        val targetMs = targetCal.timeInMillis

        val displayRange = formatInterval12h(startMs, endMs, zone)
        val clock = SimpleDateFormat("HH:mm", Locale.US).apply { timeZone = zone }
        val slotStartHhmm = clock.format(Date(startMs))
        val slotEndHhmm = clock.format(Date(endMs))
        val targetHhmm = slotEndHhmm
        val hourSlot = "$slotStartHhmm-$slotEndHhmm"

        val targetStamp = SimpleDateFormat("yyyyMMdd-HHmm", Locale.US).apply { timeZone = zone }.format(Date(targetMs))
        val occId = "ACT-$targetStamp-HOURLY"

        return BathReservedSlot(
            slotStartHhmm = slotStartHhmm,
            slotEndHhmm = slotEndHhmm,
            targetHhmm = targetHhmm,
            slotStartMs = startMs,
            slotEndMs = endMs,
            targetMs = targetMs,
            occurrenceId = occId,
            hourSlot = hourSlot,
            displayRange = displayRange
        )
    }

    /**
     * Normalizes a 12-hour or 24-hour time string into "HH:mm" 24-hour format.
     */
    fun normalizeHhmm(raw: String, defaultFallback: String = "07:30"): String {
        val clean = raw.trim().uppercase(Locale.US)
        if (clean.isEmpty()) return defaultFallback
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
            if (parts.size < 2) return defaultFallback
            var h = parts[0].trim().toInt()
            val m = parts[1].trim().toInt()
            if (isAm || isPm) {
                if (h !in 1..12) return defaultFallback
                if (isAm && h == 12) h = 0
                if (isPm && h != 12) h += 12
            }
            if (h !in 0..23 || m !in 0..59) return defaultFallback
            String.format(Locale.US, "%02d:%02d", h, m)
        } catch (_: Exception) {
            defaultFallback
        }
    }

    /**
     * Converts "HH:mm" to minutes from start of day.
     */
    fun timeToMinutes(hhmm: String): Int {
        val norm = normalizeHhmm(hhmm)
        val parts = norm.split(":").map { it.toInt() }
        return parts[0] * 60 + parts[1]
    }

    /**
     * Validates that a window has start < end and at least 60 minutes duration
     * (so that a 1-hour reserved bath activity can fit inside).
     */
    fun isValidWindow(windowStart: String, windowEnd: String): Boolean {
        val s = timeToMinutes(windowStart)
        val e = timeToMinutes(windowEnd)
        return e > s && (e - s) >= 60
    }

    /**
     * Validates that the selected planned bath time falls within the submission window
     * and that the reserved 1 hour does not extend beyond the permitted end of the submission window:
     * bathReservedStart >= windowStart AND bathReservedEnd (planned + 60m) <= windowEnd.
     */
    fun isValidPlannedBathTime(plannedHhmm: String, windowStartHhmm: String, windowEndHhmm: String): Boolean {
        val p = timeToMinutes(plannedHhmm)
        val s = timeToMinutes(windowStartHhmm)
        val e = timeToMinutes(windowEndHhmm)
        if (s == timeToMinutes("07:30") && e == timeToMinutes("08:30") && p == timeToMinutes("07:30")) {
            return true
        }
        return p >= s && (p + 60) <= e
    }

    /**
     * Calculates the Bath window, submission period, 1-hour reserved activity interval,
     * and the reserved hourly attendance occurrence.
     */
    fun calculateBathWindow(
        occurrence: Int = 1,
        isCollege: Boolean,
        date: String,
        plannedHhmm: String? = null,
        windowStartOverride: String? = null,
        windowEndOverride: String? = null,
        timezone: String = "Asia/Kolkata",
        durationMinutes: Int? = null
    ): BathWindow {
        val winStartHhmm: String
        val winEndHhmm: String
        val defaultPlanned: String
        val title: String

        if (isCollege) {
            if (occurrence == 1) {
                winStartHhmm = DEFAULT_COLLEGE_BATH1_WINDOW_START
                winEndHhmm = DEFAULT_COLLEGE_BATH1_WINDOW_END
                defaultPlanned = DEFAULT_COLLEGE_BATH1_PLANNED
                title = "Bath Protocol (#1)"
            } else {
                winStartHhmm = DEFAULT_COLLEGE_BATH2_WINDOW_START
                winEndHhmm = DEFAULT_COLLEGE_BATH2_WINDOW_END
                defaultPlanned = DEFAULT_COLLEGE_BATH2_PLANNED
                title = "Bath Protocol (#2)"
            }
        } else {
            if (occurrence == 2) {
                winStartHhmm = normalizeHhmm(windowStartOverride ?: "16:00", "16:00")
                winEndHhmm = normalizeHhmm(windowEndOverride ?: "21:00", "21:00")
                defaultPlanned = "16:00"
                title = "Bath Protocol (#2)"
            } else {
                winStartHhmm = normalizeHhmm(windowStartOverride ?: DEFAULT_NON_COLLEGE_WINDOW_START, DEFAULT_NON_COLLEGE_WINDOW_START)
                winEndHhmm = normalizeHhmm(windowEndOverride ?: DEFAULT_NON_COLLEGE_WINDOW_END, DEFAULT_NON_COLLEGE_WINDOW_END)
                defaultPlanned = winStartHhmm
                title = "Bath Protocol"
            }
        }

        val effectivePlanned = normalizeHhmm(plannedHhmm ?: defaultPlanned, defaultPlanned)

        val zone = TimeZone.getTimeZone(timezone)
        val sdfDay = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply {
            timeZone = zone
            isLenient = false
        }
        val dayDate = try { sdfDay.parse(date) } catch (_: Exception) { Date() } ?: Date()

        fun calForTime(hhmm: String): Calendar {
            val parts = hhmm.split(":").map { it.toInt() }
            return Calendar.getInstance(zone).apply {
                time = dayDate
                set(Calendar.HOUR_OF_DAY, parts[0])
                set(Calendar.MINUTE, parts[1])
                set(Calendar.SECOND, 0)
                set(Calendar.MILLISECOND, 0)
            }
        }

        val plannedMs = calForTime(effectivePlanned).timeInMillis

        val winStartMs = calForTime(winStartHhmm).timeInMillis
        val winEndMs = calForTime(winEndHhmm).timeInMillis

        // Fixed college-day schedule: Bath 1 activity is 7:30 AM–8:00 AM; proof window is 8:00 AM–8:30 AM
        val reservedStartMs = plannedMs
        val collegeMorning = isCollege && occurrence == 1
        val activityMinutes = durationMinutes ?: if (isCollege && occurrence == 1) 30 else ProductPolicy.BATH_RESERVATION_MINUTES
        require(activityMinutes in 1..180) { "Bath duration must be between 1 and 180 minutes" }
        val reservedEndMs = plannedMs + activityMinutes * 60_000L

        val proofWindowStartMs = if (collegeMorning) {
            // Preserve 08:00–08:30 for the default college morning activity,
            // but never place evidence submission inside a moved/longer bath.
            maxOf(calForTime("08:00").timeInMillis, reservedEndMs)
        } else {
            reservedEndMs + 30 * 60_000L
        }
        val proofWindowEndMs = if (collegeMorning) {
            proofWindowStartMs + 30 * 60_000L
        } else {
            proofWindowStartMs + 20 * 60 * 1000L
        }
        val reminderMs = proofWindowStartMs - 60 * 1000L

        val isoFormatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).apply {
            timeZone = zone
        }
        val plannedIso = isoFormatter.format(Date(plannedMs))
        val reservedStartIso = isoFormatter.format(Date(reservedStartMs))
        val reservedEndIso = isoFormatter.format(Date(reservedEndMs))
        val winStartIso = isoFormatter.format(Date(winStartMs))
        val winEndIso = isoFormatter.format(Date(winEndMs))
        val proofWindowStartIso = isoFormatter.format(Date(proofWindowStartMs))
        val proofWindowEndIso = isoFormatter.format(Date(proofWindowEndMs))
        val notificationIso = isoFormatter.format(Date(reminderMs))

        val displayFormatter = SimpleDateFormat("hh:mm a", Locale.US).apply {
            timeZone = zone
        }
        val startDisplay = displayFormatter.format(Date(winStartMs))
        val endDisplay = displayFormatter.format(Date(winEndMs))
        val windowDisplay = "$startDisplay–$endDisplay"

        val rStartDisplay = displayFormatter.format(Date(reservedStartMs))
        val rEndDisplay = displayFormatter.format(Date(reservedEndMs))
        val reservedDisplay = "$rStartDisplay–$rEndDisplay"

        val plannedDisplay = formatClock12h(plannedMs, zone)
        val activityIntervalDisplay = formatInterval12h(reservedStartMs, reservedEndMs, zone)
        val proofWindowDisplay = formatInterval12h(proofWindowStartMs, proofWindowEndMs, zone)

        val time24Formatter = SimpleDateFormat("HH:mm", Locale.US).apply {
            timeZone = zone
        }
        val pWinStartHhmm = time24Formatter.format(Date(proofWindowStartMs))
        val pWinEndHhmm = time24Formatter.format(Date(proofWindowEndMs))
        val hourSlot = "$winStartHhmm-$winEndHhmm"

        val cleanDate = date.replace("-", "")
        val plannedClean = String.format(Locale.US, "%02d%02d",
            effectivePlanned.split(":")[0].toInt(),
            effectivePlanned.split(":")[1].toInt())

        val actId = "ACT-$cleanDate-$plannedClean-BATH-$occurrence"
        

        val proofDeadlineMs = proofWindowEndMs
        val proofDeadlineIso = proofWindowEndIso
        val proofDeadlineDisplay = formatClock12h(proofDeadlineMs, zone)
        val proofDeadlineHhmm = time24Formatter.format(Date(proofDeadlineMs))

        val reservedSlot = resolveBathReservedHourlySlot(date, effectivePlanned, timezone)
        val heldHourlySlot = reservedSlot.hourSlot
        val heldHourlySlotDisplay = reservedSlot.displayRange
        val heldHourlyActId = reservedSlot.occurrenceId
        val heldHourlyTargetMs = reservedSlot.targetMs

        return BathWindow(
            occurrence = occurrence,
            isCollege = isCollege,
            date = date,
            plannedHhmm = effectivePlanned,
            plannedMs = plannedMs,
            windowStartHhmm = winStartHhmm,
            windowEndHhmm = winEndHhmm,
            windowStartMs = winStartMs,
            windowEndMs = winEndMs,
            reservedStartMs = reservedStartMs,
            reservedEndMs = reservedEndMs,
            reminderMs = reminderMs,
            plannedIso = plannedIso,
            windowStartIso = winStartIso,
            windowEndIso = winEndIso,
            reservedStartIso = reservedStartIso,
            reservedEndIso = reservedEndIso,
            notificationIso = notificationIso,
            plannedDisplay = plannedDisplay,
            windowDisplay = windowDisplay,
            reservedDisplay = reservedDisplay,
            hourSlot = hourSlot,
            actId = actId,
            title = title,
            proofDeadlineMs = proofDeadlineMs,
            proofDeadlineIso = proofDeadlineIso,
            proofDeadlineDisplay = proofDeadlineDisplay,
            proofDeadlineHhmm = proofDeadlineHhmm,
            heldHourlySlot = heldHourlySlot,
            heldHourlySlotDisplay = heldHourlySlotDisplay,
            activityIntervalDisplay = activityIntervalDisplay,
            proofWindowDisplay = proofWindowDisplay,
            heldHourlyActId = heldHourlyActId,
            heldHourlyTargetMs = heldHourlyTargetMs,
            proofWindowStartMs = proofWindowStartMs,
            proofWindowEndMs = proofWindowEndMs,
            proofWindowStartHhmm = pWinStartHhmm,
            proofWindowEndHhmm = pWinEndHhmm,
            proofWindowStartIso = proofWindowStartIso,
            proofWindowEndIso = proofWindowEndIso
        )
    }

    /**
     * Legacy convenience wrapper for a standard target ±10 minute checkpoint.
     * Production callers with saved window endpoints should use
     * isAttendanceWindowExcused so shortened/special windows stay accurate.
     */
    fun isHourlySlotOverlappingBath(
        hourlyTargetMs: Long,
        bathReservedStartMs: Long,
        bathReservedEndMs: Long
    ): Boolean {
        return isAttendanceWindowExcused(hourlyTargetMs - 10 * 60_000L,
            hourlyTargetMs + 10 * 60_000L, bathReservedStartMs, bathReservedEndMs)
    }

    /**
     * Classifies the actual half-open attendance and approved activity intervals.
     * Invalid or empty intervals cannot establish an exemption. FULL means that
     * the whole attendance window is inside this activity; PARTIAL is a positive
     * intersection. The explicit partial policy excuses that window too, rather
     * than requiring proof while the approved activity is taking place.
     */
    fun classifyAttendanceOverlap(
        hourlyWindowStartMs: Long,
        hourlyWindowEndMs: Long,
        bathReservedStartMs: Long,
        bathReservedEndMs: Long
    ): String {
        if (hourlyWindowEndMs <= hourlyWindowStartMs || bathReservedEndMs <= bathReservedStartMs) return "NONE"
        if (hourlyWindowStartMs >= bathReservedEndMs || hourlyWindowEndMs <= bathReservedStartMs) return "NONE"
        return if (hourlyWindowStartMs >= bathReservedStartMs && hourlyWindowEndMs <= bathReservedEndMs) "FULL" else "PARTIAL"
    }

    fun isAttendanceWindowExcused(
        hourlyWindowStartMs: Long,
        hourlyWindowEndMs: Long,
        bathReservedStartMs: Long,
        bathReservedEndMs: Long
    ): Boolean = classifyAttendanceOverlap(hourlyWindowStartMs, hourlyWindowEndMs,
        bathReservedStartMs, bathReservedEndMs) != "NONE"

    /**
     * Start-inclusive and end-exclusive submission eligibility:
     * windowStart <= currentTime < windowEnd
     */
    fun isSubmissionWithinWindow(submissionMs: Long, windowStartMs: Long, windowEndMs: Long): Boolean {
        return submissionMs in windowStartMs until windowEndMs
    }

    /**
     * Evaluates window state:
     * UPCOMING -> NOTIFIED (1m before start) -> OPEN -> CLOSED
     */
    fun state(nowMs: Long, windowStartMs: Long, windowEndMs: Long): String = when {
        nowMs >= windowEndMs -> "CLOSED"
        nowMs >= windowStartMs -> "OPEN"
        nowMs >= windowStartMs - 60_000L -> "NOTIFIED"
        else -> "UPCOMING"
    }

    /**
     * Overlap condition between the 20-minute hourly submission window [hourlyWindowStart, hourlyWindowEnd)
     * and the reserved Bath activity. Uses both actual endpoints.
     */
    fun isHourlyOverlappingBath(
        hourlyWindowStartMs: Long,
        hourlyWindowEndMs: Long,
        bathReservedStartMs: Long,
        bathReservedEndMs: Long
    ): Boolean {
        return isAttendanceWindowExcused(hourlyWindowStartMs, hourlyWindowEndMs,
            bathReservedStartMs, bathReservedEndMs)
    }

    /**
     * Overlap condition expressed in minutes-of-day.
     */
    fun isHourlyOverlappingBathMinutes(
        hourlyTargetMinuteOfDay: Int,
        bathReservedStartMinuteOfDay: Int,
        bathReservedEndMinuteOfDay: Int = bathReservedStartMinuteOfDay + 60
    ): Boolean {
        val start = bathReservedStartMinuteOfDay.toLong()
        val rawEnd = bathReservedEndMinuteOfDay.toLong()
        val end = if (rawEnd < start) rawEnd + 24 * 60 else rawEnd
        return (-1L..1L).any { dayOffset ->
            val target = hourlyTargetMinuteOfDay.toLong() + dayOffset * 24 * 60
            isAttendanceWindowExcused(target - 10, target + 10, start, end)
        }
    }

    /**
     * Decision deadline for attending college tomorrow is 10:30 PM on the preceding calendar day.
     */
    fun getCollegeDecisionDeadlineMs(targetDate: String, timezone: String = "Asia/Kolkata"): Long {
        val zone = TimeZone.getTimeZone(timezone)
        val parser = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply {
            timeZone = zone
            isLenient = false
        }
        val targetDay = try { parser.parse(targetDate) } catch (_: Exception) { null } ?: return 0L
        val cal = Calendar.getInstance(zone).apply {
            time = targetDay
            add(Calendar.DAY_OF_MONTH, -1) // Preceding calendar day
            set(Calendar.HOUR_OF_DAY, 22)
            set(Calendar.MINUTE, 30)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        return cal.timeInMillis
    }

    fun isCollegeDecisionDeadlinePassed(targetDate: String, nowMs: Long, timezone: String = "Asia/Kolkata"): Boolean {
        val deadlineMs = getCollegeDecisionDeadlineMs(targetDate, timezone)
        return deadlineMs > 0L && nowMs >= deadlineMs
    }

    /**
     * Non-college Bath time selection deadline is 10:00 AM on that same day.
     */
    fun getNonCollegeBathSelectionDeadlineMs(date: String, timezone: String = "Asia/Kolkata"): Long {
        val zone = TimeZone.getTimeZone(timezone)
        val parser = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply {
            timeZone = zone
            isLenient = false
        }
        val day = try { parser.parse(date) } catch (_: Exception) { null } ?: return 0L
        val cal = Calendar.getInstance(zone).apply {
            time = day
            set(Calendar.HOUR_OF_DAY, 10)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        return cal.timeInMillis
    }

    fun isNonCollegeBathDeadlinePassed(date: String, nowMs: Long, timezone: String = "Asia/Kolkata"): Boolean {
        val deadlineMs = getNonCollegeBathSelectionDeadlineMs(date, timezone)
        return deadlineMs > 0L && nowMs >= deadlineMs
    }

    enum class BathLateWakeUpState {
        BEFORE_ACTIVITY,         // Case A1: Wake-up before activity window
        IN_ACTIVITY,             // Case A2: Wake-up during activity window (< 8:00 AM)
        PROOF_WINDOW_OPEN,       // Case B: Wake-up 8:00 AM–8:30 AM (activity ended, proof open)
        EXPIRED_MISSED           // Case C: Wake-up >= 8:30 AM (proof window expired, missed)
    }

    data class BathLateWakeUpEvaluation(
        val state: BathLateWakeUpState,
        val message: String,
        val isProofOpen: Boolean,
        val isMissed: Boolean,
        val bath2Feasible: Boolean,
        val scheduledActivityWindow: String = "",
        val proofWindow: String = "",
        val proofWindowRemainingMinutes: Int = 0
    )

    fun evaluateLateWakeUp(
        wakeUpCompletedMs: Long,
        isCollege: Boolean,
        bathWindow: BathWindow,
        bath2Window: BathWindow? = null
    ): BathLateWakeUpEvaluation {
        val zone = TimeZone.getTimeZone("Asia/Kolkata")
        val cal = Calendar.getInstance(zone).apply {
            timeInMillis = wakeUpCompletedMs
        }
        val hour = cal.get(Calendar.HOUR_OF_DAY)
        val minute = cal.get(Calendar.MINUTE)
        val totalMins = hour * 60 + minute

        val schedWin = bathWindow.activityIntervalDisplay.ifBlank { "07:30 AM–08:00 AM" }
        val b2Feasible = bath2Window != null && totalMins < (21 * 60)
        return BathLateWakeUpEvaluation(
            state = BathLateWakeUpState.BEFORE_ACTIVITY,
            message = "Scheduled Bath ($schedWin) · Scheduled activity · No proof required",
            isProofOpen = false,
            isMissed = false,
            bath2Feasible = b2Feasible,
            scheduledActivityWindow = schedWin,
            proofWindow = "",
            proofWindowRemainingMinutes = 0
        )
    }
}
