package com.strictrecords.app

import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Pure calculation rules for Travel Pre-booking window.
 *
 * Rules:
 * - Booking opens at 12:00 AM on the calendar date two days before the selected travel date.
 * - Booking closes six hours before the selected planned departure time.
 * - Booking is rejected after the cutoff.
 * - Past departures cannot be booked as future Travel occurrences.
 *
 * Formula:
 * bookingOpens = startOfTravelDateMinusTwoCalendarDays
 * bookingDeadline = plannedDepartureDateTime - 6 hours
 * bookingAllowed = bookingOpens <= now <= bookingDeadline
 */
object TravelBookingRules {

    data class WindowEvaluation(
        val allowed: Boolean,
        val reason: String,
        val bookingOpensMs: Long,
        val bookingDeadlineMs: Long,
        val departureMs: Long
    )

    fun evaluateBookingWindow(
        travelDate: String,
        departureTime: String,
        nowMs: Long,
        timeZone: TimeZone
    ): WindowEvaluation {
        val cleanDate = travelDate.trim()
        val cleanTime = departureTime.trim()

        val dateTimeDf = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            this.timeZone = timeZone
            isLenient = false
        }
        val dateOnlyDf = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply {
            this.timeZone = timeZone
            isLenient = false
        }

        val depDate = runCatching { dateTimeDf.parse("$cleanDate $cleanTime") }.getOrNull()
            ?: return WindowEvaluation(
                allowed = false,
                reason = "Invalid travel date or departure time format. Use YYYY-MM-DD and HH:mm.",
                bookingOpensMs = 0L,
                bookingDeadlineMs = 0L,
                departureMs = 0L
            )

        val departureMs = depDate.time

        val travelStartCal = Calendar.getInstance(timeZone).apply {
            time = dateOnlyDf.parse(cleanDate)!!
            set(Calendar.HOUR_OF_DAY, 0)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }

        // bookingOpens = startOfTravelDateMinusTwoCalendarDays (12:00 AM two days before travelDate)
        val opensCal = (travelStartCal.clone() as Calendar).apply {
            add(Calendar.DAY_OF_YEAR, -2)
        }
        val bookingOpensMs = opensCal.timeInMillis

        // bookingDeadline = plannedDepartureDateTime - 6 hours
        val bookingDeadlineMs = departureMs - (6L * 3600L * 1000L)

        val displayDf = SimpleDateFormat("MMM d, yyyy h:mm a", Locale.US).apply {
            this.timeZone = timeZone
        }

        if (departureMs <= nowMs) {
            return WindowEvaluation(
                allowed = false,
                reason = "Cannot pre-book a past departure time.",
                bookingOpensMs = bookingOpensMs,
                bookingDeadlineMs = bookingDeadlineMs,
                departureMs = departureMs
            )
        }

        if (nowMs < bookingOpensMs) {
            val opensFormatted = displayDf.format(Date(bookingOpensMs))
            return WindowEvaluation(
                allowed = false,
                reason = "Booking window has not opened yet. Opens on $opensFormatted (12:00 AM two calendar days before travel).",
                bookingOpensMs = bookingOpensMs,
                bookingDeadlineMs = bookingDeadlineMs,
                departureMs = departureMs
            )
        }

        if (nowMs > bookingDeadlineMs) {
            val deadlineFormatted = displayDf.format(Date(bookingDeadlineMs))
            return WindowEvaluation(
                allowed = false,
                reason = "Booking window closed. Pre-booking must be completed at least 6 hours before departure (Deadline was $deadlineFormatted).",
                bookingOpensMs = bookingOpensMs,
                bookingDeadlineMs = bookingDeadlineMs,
                departureMs = departureMs
            )
        }

        return WindowEvaluation(
            allowed = true,
            reason = "Booking window is open.",
            bookingOpensMs = bookingOpensMs,
            bookingDeadlineMs = bookingDeadlineMs,
            departureMs = departureMs
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

    data class TravelWindow(
        val travelDate: String,
        val departureTime: String,
        val departureMs: Long,
        val durationHours: Int,
        val travelEndMs: Long,
        val travelEndTime: String,
        val windowStartMs: Long,
        val windowEndMs: Long,
        val reminderMs: Long,
        val windowStartIso: String,
        val windowEndIso: String,
        val notificationIso: String
    )

    fun calculateTravelWindow(
        travelDate: String,
        departureTime: String,
        durationHours: Int = 3,
        timeZone: TimeZone = TimeZone.getTimeZone("Asia/Kolkata")
    ): TravelWindow? {
        val cleanDate = travelDate.trim()
        val cleanTime = departureTime.trim()
        val dateTimeDf = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            this.timeZone = timeZone
            isLenient = false
        }
        val depDate = runCatching { dateTimeDf.parse("$cleanDate $cleanTime") }.getOrNull() ?: return null
        val departureMs = depDate.time

        val safeDuration = durationHours.coerceIn(1, 24)
        val travelEndMs = departureMs + (safeDuration * 3600_000L)
        val timeFmt = SimpleDateFormat("HH:mm", Locale.US).apply { this.timeZone = timeZone }
        val travelEndTime = timeFmt.format(Date(travelEndMs))

        // Proof submission window opens at travelEndMs and closes 2 hours later
        val windowStartMs = travelEndMs
        val windowEndMs = travelEndMs + (2 * 3600_000L)
        val reminderMs = windowStartMs - PRE_OPENING_REMINDER_MS

        val isoDf = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).apply {
            this.timeZone = timeZone
        }

        return TravelWindow(
            travelDate = cleanDate,
            departureTime = cleanTime,
            departureMs = departureMs,
            durationHours = safeDuration,
            travelEndMs = travelEndMs,
            travelEndTime = travelEndTime,
            windowStartMs = windowStartMs,
            windowEndMs = windowEndMs,
            reminderMs = reminderMs,
            windowStartIso = isoDf.format(Date(windowStartMs)),
            windowEndIso = isoDf.format(Date(windowEndMs)),
            notificationIso = isoDf.format(Date(reminderMs))
        )
    }

    fun calculateTravelWindow(
        travelDate: String,
        departureTime: String,
        timeZone: TimeZone
    ): TravelWindow? = calculateTravelWindow(travelDate, departureTime, 3, timeZone)

    /**
     * Checks if an hourly slot is covered by a travel interval [departureMs, travelEndMs].
     * The slot activity period is [slotTargetMs - 3600_000L, slotTargetMs].
     * It is covered if its activity period falls within the travel interval:
     * (slotTargetMs - 3600_000L >= departureMs) && (slotTargetMs <= travelEndMs).
     */
    fun isHourlySlotCoveredByTravel(slotTargetMs: Long, departureMs: Long, travelEndMs: Long): Boolean {
        val slotStartMs = slotTargetMs - 3600_000L
        val slotEndMs = slotTargetMs
        return slotStartMs >= departureMs && slotEndMs <= travelEndMs
    }

    enum class TravelStatusState {
        PREBOOKED,
        TRAVEL_IN_PROGRESS,
        PROOF_OPENS_SOON,
        SUBMISSION_OPEN,
        SUBMITTED_PENDING_REVIEW,
        VERIFIED,
        MISSED,
        REJECTED
    }

    fun isDemoBooking(title: String?, detailsJson: String?): Boolean {
        val d = runCatching { org.json.JSONObject(detailsJson ?: "{}") }.getOrNull()
        if (d != null && d.has("is_demo")) {
            return d.optBoolean("is_demo", false)
        }
        val cleanTitle = (title ?: "").lowercase(Locale.US)
        return cleanTitle.contains("(simulated demo)") || cleanTitle.contains("demo test") || cleanTitle.startsWith("[demo]") || cleanTitle.contains("(demo)")
    }

    fun resolveAuthoritativeState(
        nowMs: Long,
        departureMs: Long,
        travelEndMs: Long,
        proofStartMs: Long,
        proofEndMs: Long,
        status: String,
        hasProofSubmitted: Boolean = false
    ): TravelStatusState {
        val s = status.uppercase(Locale.US)
        if (s in listOf("VERIFIED", "APPROVED", "COMPLETED")) return TravelStatusState.VERIFIED
        if (s in listOf("REJECTED", "REJECTED_LATE")) return TravelStatusState.REJECTED
        if (s in listOf("SUBMITTED", "VALIDATING", "PENDING_SYNC", "AI_PROVISIONAL", "PENDING_REVIEW") || hasProofSubmitted) {
            return TravelStatusState.SUBMITTED_PENDING_REVIEW
        }
        if (s in listOf("MISSED", "EXPIRED", "CLOSED", "SKIPPED", "CANCELLED")) return TravelStatusState.MISSED

        return when {
            nowMs >= proofEndMs -> TravelStatusState.MISSED
            nowMs in proofStartMs until proofEndMs -> TravelStatusState.SUBMISSION_OPEN
            nowMs in (proofStartMs - PRE_OPENING_REMINDER_MS) until proofStartMs -> TravelStatusState.PROOF_OPENS_SOON
            nowMs in departureMs until travelEndMs -> TravelStatusState.TRAVEL_IN_PROGRESS
            nowMs < departureMs -> TravelStatusState.PREBOOKED
            else -> TravelStatusState.MISSED
        }
    }

    fun generateBookingId(date: String, departureTime: String): String =
        "TBK-${date.replace("-", "")}-${departureTime.trim().replace(":", "")}"

    fun generateActivityId(date: String, departureTime: String): String =
        "ACT-${date.replace("-", "")}-${departureTime.trim().replace(":", "")}-TRAVEL"

    fun formatScheduleVersion(scheduleVersion: Int): String =
        if (scheduleVersion > 0) scheduleVersion.toString() else "Not recorded"

    fun formatBookingReference(bookingId: String?, isPrebooked: Boolean): String =
        if (!bookingId.isNullOrBlank()) bookingId else if (isPrebooked) "Not recorded / unlinked (legacy prebooking)" else ""

    fun resolveActivitySyncLabel(localExists: Boolean, syncStatuses: List<String>, hasRemoteReview: Boolean = false): DisciplineScoringRules.ActivitySyncInfo {
        if (!localExists) {
            return DisciplineScoringRules.ActivitySyncInfo("LOCAL_SAVED", "Local record not found", "No local activity found")
        }
        val normalized = syncStatuses.map { it.trim().uppercase(Locale.US) }
        if (normalized.isEmpty()) {
            return if (hasRemoteReview) {
                DisciplineScoringRules.ActivitySyncInfo("SYNCED", "Synchronization confirmed", "Verified with server")
            } else {
                DisciplineScoringRules.ActivitySyncInfo("LOCAL_SAVED", "Local record saved", "Saved on device; not queued for synchronization")
            }
        }
        val failedCount = normalized.count { it == "FAILED" }
        val pendingCount = normalized.count { it == "PENDING" }
        val syncingCount = normalized.count { it == "SYNCING" }
        val syncedCount = normalized.count { it == "SYNCED" }

        return when {
            failedCount > 0 && syncedCount > 0 -> DisciplineScoringRules.ActivitySyncInfo(
                "PARTIALLY_SYNCED",
                "Partially synchronized or missing linked records",
                "$syncedCount confirmed, $failedCount failed"
            )
            failedCount > 0 -> DisciplineScoringRules.ActivitySyncInfo(
                "SYNC_FAILED",
                "Synchronization failed",
                "$failedCount sync event(s) failed"
            )
            syncingCount > 0 -> DisciplineScoringRules.ActivitySyncInfo(
                "SYNC_IN_PROGRESS",
                "Synchronization in progress",
                "Synchronizing with server"
            )
            pendingCount > 0 && syncedCount > 0 -> DisciplineScoringRules.ActivitySyncInfo(
                "PARTIALLY_SYNCED",
                "Partially synchronized or missing linked records",
                "$syncedCount confirmed, $pendingCount pending"
            )
            pendingCount > 0 -> DisciplineScoringRules.ActivitySyncInfo(
                "AWAITING_SYNC",
                "Awaiting synchronization",
                "Queued for synchronization"
            )
            syncedCount > 0 && syncedCount == normalized.size -> DisciplineScoringRules.ActivitySyncInfo(
                "SYNCED",
                "Synchronization confirmed",
                "All $syncedCount event(s) confirmed"
            )
            else -> DisciplineScoringRules.ActivitySyncInfo(
                "LOCAL_SAVED",
                "Local record saved",
                "Saved on device"
            )
        }
    }

    fun formatAiAnalysisStatus(
        submittedAt: String?,
        reviewStatus: String?,
        reviewPoints: Int = 0,
        localEvalStatus: String? = null,
        localEvalPoints: Int = 0
    ): String {
        val revNorm = reviewStatus?.trim()?.uppercase(Locale.US)
        return when {
            revNorm in listOf("VERIFIED", "APPROVED") -> "Verified (+$reviewPoints pts)"
            revNorm in listOf("REJECTED", "REJECTED_LATE") -> "Rejected by evaluator"
            revNorm == "PENDING" -> "Awaiting evaluation"
            !localEvalStatus.isNullOrBlank() -> "$localEvalStatus (+$localEvalPoints pts)"
            submittedAt.isNullOrBlank() && reviewStatus.isNullOrBlank() -> "None (no submission recorded)"
            else -> "Awaiting evaluation"
        }
    }

    fun buildSubmissionPayload(
        submissionId: String,
        evidenceId: String,
        activityId: String,
        bookingId: String?,
        date: String,
        scheduleVersion: Int,
        departureTime: String,
        durationHours: Int,
        description: String,
        previewBase64: String = "",
        isDevMode: Boolean = false
    ): JSONObject = JSONObject().apply {
        put("submission_id", submissionId)
        put("evidence_id", evidenceId)
        put("activity_id", activityId)
        if (!bookingId.isNullOrBlank()) put("booking_id", bookingId)
        put("task_type", "TRAVEL")
        put("date", date)
        put("is_dev_mode", isDevMode)
        put("schedule_version", scheduleVersion)
        put("hour_slot", if (departureTime.isNotBlank()) "$departureTime (${durationHours}h)" else "TRAVEL")
        put("category", "TRAVEL")
        put("primary_activity", "TRAVEL")
        put("description", description)
        put("evidence_filename", "travel_proof.jpg")
        put("image_count", 1)
        put("image_base64", previewBase64)
    }

    data class TravelRestriction(
        val isRestricted: Boolean,
        val lockUntilMs: Long,
        val lockUntilDateStr: String,
        val failedOccurrences: List<String>,
        val failedDates: List<String>
    )

    data class TravelOccurrenceRecord(
        val activityId: String,
        val date: String,
        val departureMs: Long,
        val travelEndMs: Long,
        val windowEndMs: Long,
        val status: String,
        val proofSubmitted: Boolean,
        val isDemo: Boolean = false
    ) {
        fun isFinalizedFailure(nowMs: Long): Boolean {
            if (isDemo) return false // Demo tests never count as qualifying failures
            val s = status.uppercase(Locale.US)
            if (s in listOf("VERIFIED", "APPROVED", "COMPLETED", "SUBMITTED", "VALIDATING", "PENDING_SYNC", "AI_PROVISIONAL", "PENDING_REVIEW")) {
                return false
            }
            if (s in listOf("MISSED", "EXPIRED", "REJECTED", "REJECTED_LATE")) {
                return true
            }
            return nowMs >= windowEndMs && !proofSubmitted
        }
    }

    /**
     * Evaluates whether 3 consecutive qualifying travel proof failures have triggered a 3-month restriction.
     * Only distinct, finalized failures count.
     * An approved/verified travel occurrence resets the consecutive failure streak.
     */
    fun evaluateConsecutiveFailures(
        occurrences: List<TravelOccurrenceRecord>,
        nowMs: Long,
        timeZone: TimeZone = TimeZone.getTimeZone("Asia/Kolkata")
    ): TravelRestriction {
        val consecutiveFailures = mutableListOf<TravelOccurrenceRecord>()
        var restrictionActive = false
        var lockUntilMs = 0L
        var lockedDates = emptyList<String>()
        var lockedOccurrences = emptyList<String>()

        // Retried sync/background results represent the same persisted occurrence.
        for (occ in occurrences.associateBy { it.activityId }.values.sortedBy { it.windowEndMs }) {
            val status = occ.status.uppercase(Locale.US)
            if (status in listOf("VERIFIED", "APPROVED", "COMPLETED")) {
                consecutiveFailures.clear()
            } else if (occ.isFinalizedFailure(nowMs)) {
                consecutiveFailures.add(occ)
                if (consecutiveFailures.size == 3) {
                    val thirdFailureMs = occ.windowEndMs
                    val cal = Calendar.getInstance(timeZone).apply {
                        timeInMillis = thirdFailureMs
                        add(Calendar.MONTH, 3)
                    }
                    val lockMs = cal.timeInMillis
                    if (nowMs < lockMs) {
                        restrictionActive = true
                        lockUntilMs = lockMs
                        lockedDates = consecutiveFailures.map { it.date }
                        lockedOccurrences = consecutiveFailures.map { it.activityId }
                    } else {
                        consecutiveFailures.clear()
                    }
                }
            }
        }

        val dateFmt = SimpleDateFormat("d MMMM yyyy", Locale.US).apply { this.timeZone = timeZone }
        val lockDateStr = if (lockUntilMs > 0L) dateFmt.format(Date(lockUntilMs)) else ""

        return TravelRestriction(
            isRestricted = restrictionActive,
            lockUntilMs = lockUntilMs,
            lockUntilDateStr = lockDateStr,
            failedOccurrences = lockedOccurrences,
            failedDates = lockedDates
        )
    }

    /**
     * Start-inclusive and end-exclusive submission eligibility:
     * travelWindowStart <= currentTime < travelWindowEnd
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
        nowMs in windowStartMs until windowEndMs -> STATE_OPEN
        nowMs in (windowStartMs - PRE_OPENING_REMINDER_MS) until windowStartMs -> STATE_REMINDER_VISIBLE
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
            nowMs in windowStartMs until windowEndMs -> STATE_OPEN
            nowMs in (windowStartMs - PRE_OPENING_REMINDER_MS) until windowStartMs -> STATE_REMINDER_VISIBLE
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
}
