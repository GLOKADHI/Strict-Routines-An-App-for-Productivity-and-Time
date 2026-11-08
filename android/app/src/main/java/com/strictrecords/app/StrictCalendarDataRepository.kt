package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.GregorianCalendar
import java.util.Locale
import java.util.TimeZone

/**
 * Read-only data repository for the StrictRecords Interactive Calendar.
 * Safely aggregates scheduled activities, gym routine status, verifications,
 * and score events across months and dates without creating duplicate records
 * or mutating scores.
 */
object StrictCalendarDataRepository {

    const val TIMEZONE = "Asia/Kolkata"
    const val DEFAULT_LAUNCH_DATE = "2026-10-25"

    data class MonthDaySummary(
        val date: String,                  // "YYYY-MM-DD"
        val dayOfMonth: Int,               // 1..31
        val weekday: Int,                  // 0=Sun .. 6=Sat
        val isToday: Boolean,
        val gymState: String,              // "GYM_DAY", "REST_DAY", "PAUSED", "NOT_TRACKED", "SETUP_REQUIRED"
        val gymSessionStatus: String?,     // "VERIFIED", "PENDING", "MISSED", null
        val totalActivities: Int,
        val verifiedActivities: Int,
        val pendingActivities: Int,
        val missedActivities: Int,
        val isScheduleConfirmed: Boolean
    )

    data class ActivityItem(
        val id: String,
        val type: String,
        val title: String,
        val scheduledTime: String,
        val windowStart: String,
        val windowEnd: String,
        val status: String,                // "SCHEDULED", "SUBMITTED", "PENDING", "VERIFIED", "APPROVED", "COMPLETED", "MISSED", "REJECTED", "EXCUSED"
        val submittedAt: String?,
        val points: Int,
        val configuredPoints: Int,
        val detailsJson: String
    )

    data class ScoreEventItem(
        val eventType: String,
        val description: String,
        val points: Int,
        val timestamp: String
    )

    data class DateDetails(
        val date: String,
        val formattedDate: String,         // "Sunday, 25 October 2026"
        val isToday: Boolean,
        val gymState: String,
        val gymSessionStatus: String?,
        val gymRoutineAnchor: String?,
        val gymPauseReason: String?,
        val isScheduleConfirmed: Boolean,
        val isBathPlanConfirmed: Boolean,
        val committedBathTimes: String,
        val collegeAttendance: String,
        val activities: List<ActivityItem>,
        val scoreEvents: List<ScoreEventItem>,
        val hasAnyData: Boolean,
        val wakeUpStatus: String = "NOT_STARTED",
        val wakeUpCompletedAt: String? = null,
        val bathSessionCount: Int = 1,
        val bath1WindowDisplay: String = "7:30 AM–8:00 AM",
        val bath1ProofWindowDisplay: String = "8:00 AM–8:30 AM",
        val bath1Status: String = "PLANNED",
        val bath2WindowDisplay: String? = null,
        val bath2ProofWindowDisplay: String? = null,
        val bath2Status: String? = null,
        val gymModeStatus: String = "INACTIVE",
        val gymLivesBalance: Int = 3,
        val isSyncPending: Boolean = false,
        val dailyGymSetup: DailyGymScheduleRules.DailyGymSetup? = null
    )

    private fun formatter(pattern: String, zone: String = TIMEZONE) =
        SimpleDateFormat(pattern, Locale.ENGLISH).apply {
            timeZone = TimeZone.getTimeZone(zone)
            isLenient = false
        }

    fun isDate(value: String): Boolean = GymRoutineDates.isDate(value)

    fun today(nowMs: Long): String = GymRoutineDates.today(nowMs)

    fun month(value: String): String = GymRoutineDates.month(value)

    fun addDays(value: String, days: Int): String = GymRoutineDates.addDays(value, days)

    fun addMonths(value: String, months: Int): String = GymRoutineDates.addMonths(value, months)

    fun monthDays(monthStr: String): List<String> = GymRoutineDates.monthDays(monthStr)

    fun weekday(dateStr: String): Int = GymRoutineDates.weekday(dateStr)

    fun formatMonthDisplay(monthStr: String): String = GymRoutineDates.displayMonth(monthStr)

    fun formatDateDisplay(dateStr: String): String = GymRoutineDates.display(dateStr, "EEEE, d MMMM yyyy")

    fun formatShortDateDisplay(dateStr: String): String = GymRoutineDates.display(dateStr, "d MMM yyyy")

    fun formatTime12h(timeIsoOrHhMm: String): String {
        if (timeIsoOrHhMm.isBlank()) return "—"
        val clean = timeIsoOrHhMm.trim()
        val timePart = if (clean.contains("T")) {
            clean.substringAfter("T").take(5)
        } else {
            clean.take(5)
        }
        val parts = timePart.split(":")
        if (parts.size >= 2) {
            val h = parts[0].toIntOrNull() ?: return clean
            val m = parts[1].toIntOrNull() ?: return clean
            val amPm = if (h >= 12) "PM" else "AM"
            val h12 = if (h % 12 == 0) 12 else h % 12
            return "%d:%02d %s".format(Locale.US, h12, m, amPm)
        }
        return clean
    }

    /**
     * Resolves Gym Routine configuration from local DB or defaults to LAUNCH_DATE anchor.
     */
    fun getGymRoutineConfig(db: StrictRecordsLocalDb): Pair<Boolean, Pair<String, List<GymRoutineDates.Segment>>> {
        val routine = GymRoutineClient.routine(db)
        val isConfigured = routine.optString("routine_id").let { it.isNotBlank() && it != "null" }
        val startDate = routine.optString("start_date", GymRoutineDates.LAUNCH_DATE).ifBlank { GymRoutineDates.LAUNCH_DATE }
        val items = routine.optJSONArray("segments") ?: JSONArray()
        val segments = (0 until items.length()).mapNotNull { idx ->
            val part = items.optJSONObject(idx) ?: return@mapNotNull null
            val start = part.optString("start_date")
            val anchor = part.optString("anchor_date")
            val end = if (part.isNull("end_date")) null else part.optString("end_date").ifBlank { null }
            if (isDate(start) && isDate(anchor) && (end == null || isDate(end))) {
                GymRoutineDates.Segment(start, anchor, end)
            } else null
        }
        val effectiveSegments = if (segments.isNotEmpty()) {
            segments
        } else {
            listOf(GymRoutineDates.Segment(startDate, startDate))
        }
        return Pair(isConfigured || segments.isNotEmpty(), Pair(startDate, effectiveSegments))
    }

    /**
     * Calculates the planned gym state for a given date using established routine anchor rules.
     */
    fun calculatePlannedGymState(
        dateStr: String,
        isConfigured: Boolean,
        startDate: String,
        segments: List<GymRoutineDates.Segment>
    ): String {
        return GymRoutineDates.plannedState(dateStr, isConfigured, startDate, segments)
    }

    /**
     * Calculates the gym session execution status for a given calendar date.
     */
    fun calculateGymSessionStatus(
        plannedGym: String,
        isGymSessionVerified: Boolean,
        hasGymSessionPending: Boolean,
        dateStr: String,
        todayDate: String
    ): String? = when {
        isGymSessionVerified -> "VERIFIED"
        plannedGym == "GYM_DAY" && hasGymSessionPending -> "PENDING"
        plannedGym == "GYM_DAY" && dateStr < todayDate && !isGymSessionVerified -> "MISSED"
        else -> null
    }

    /**
     * Loads summary indicators for all days of the specified month in a batch query.
     */
    fun loadMonthDays(
        db: StrictRecordsLocalDb,
        monthStr: String,
        todayDate: String = GymRoutineDates.today(db.authoritativeNowMs())
    ): List<MonthDaySummary> {
        val days = monthDays(monthStr)
        if (days.isEmpty()) return emptyList()

        val startMonthDate = days.first()
        val endMonthDate = days.last()

        val (isConfigured, routineInfo) = getGymRoutineConfig(db)
        val (startDate, segments) = routineInfo

        // Batch query 1: Scheduled activities for month
        val activitiesByDate = mutableMapOf<String, MutableList<ActivityItem>>()
        try {
            db.readableDatabase.rawQuery(
                """
                SELECT activity_id, type, date, title, scheduled_time, window_start, window_end,
                       status, submitted_at, points, details_json, configured_points
                FROM scheduled_activities
                WHERE date >= ? AND date <= ?
                ORDER BY date ASC, scheduled_time ASC
                """.trimIndent(),
                arrayOf(startMonthDate, endMonthDate)
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    val act = ActivityItem(
                        id = cursor.getString(0) ?: "",
                        type = cursor.getString(1) ?: "",
                        title = cursor.getString(3) ?: "",
                        scheduledTime = cursor.getString(4) ?: "",
                        windowStart = cursor.getString(5) ?: "",
                        windowEnd = cursor.getString(6) ?: "",
                        status = cursor.getString(7) ?: "SCHEDULED",
                        submittedAt = cursor.getString(8),
                        points = cursor.getInt(9),
                        detailsJson = cursor.getString(10) ?: "{}",
                        configuredPoints = cursor.getInt(11)
                    )
                    val dateKey = cursor.getString(2) ?: ""
                    activitiesByDate.getOrPut(dateKey) { mutableListOf() }.add(act)
                }
            }
        } catch (_: Exception) {}

        // Batch query 2: Approved proof reviews
        val approvedReviews = mutableSetOf<String>() // Set of "$date:$taskCode" or "$date:act_$activityId"
        try {
            db.readableDatabase.rawQuery(
                """
                SELECT activity_id, date, status, task_code
                FROM proof_reviews
                WHERE date >= ? AND date <= ? AND status IN ('APPROVED', 'VERIFIED')
                """.trimIndent(),
                arrayOf(startMonthDate, endMonthDate)
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    val actId = cursor.getString(0) ?: ""
                    val d = cursor.getString(1) ?: ""
                    val taskCode = cursor.getString(3) ?: ""
                    if (actId.isNotBlank()) approvedReviews.add("$d:act_$actId")
                    if (taskCode.isNotBlank()) approvedReviews.add("$d:$taskCode")
                }
            }
        } catch (_: Exception) {}

        // Batch query 3: Proof evaluations
        val validEvaluations = mutableSetOf<String>()
        try {
            db.readableDatabase.rawQuery(
                """
                SELECT activity_id, date, status, task_type, score
                FROM proof_evaluations
                WHERE date >= ? AND date <= ? AND status NOT IN ('REJECTED')
                """.trimIndent(),
                arrayOf(startMonthDate, endMonthDate)
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    val actId = cursor.getString(0) ?: ""
                    val d = cursor.getString(1) ?: ""
                    val taskType = cursor.getString(3) ?: ""
                    if (actId.isNotBlank()) validEvaluations.add("$d:act_$actId")
                    if (taskType.isNotBlank()) validEvaluations.add("$d:$taskType")
                }
            }
        } catch (_: Exception) {}

        // Batch query 4: Daily state (for gym completion)
        val gymCompletedDates = mutableSetOf<String>()
        try {
            db.readableDatabase.rawQuery(
                """
                SELECT date, gym_status
                FROM daily_state
                WHERE date >= ? AND date <= ?
                """.trimIndent(),
                arrayOf(startMonthDate, endMonthDate)
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    val d = cursor.getString(0) ?: ""
                    val st = cursor.getString(1) ?: ""
                    if (st.startsWith("GYM COMPLETE") || st == "VERIFIED") {
                        gymCompletedDates.add(d)
                    }
                }
            }
        } catch (_: Exception) {}

        // Batch query 5: Routine schedules confirmed
        val confirmedSchedules = mutableSetOf<String>()
        try {
            db.readableDatabase.rawQuery(
                """
                SELECT schedule_date, status, is_locked, confirmed_at
                FROM routine_schedules
                WHERE schedule_date >= ? AND schedule_date <= ?
                """.trimIndent(),
                arrayOf(startMonthDate, endMonthDate)
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    val d = cursor.getString(0) ?: ""
                    val status = cursor.getString(1) ?: ""
                    val isLocked = cursor.getInt(2) == 1
                    val confirmedAt = cursor.getString(3) ?: ""
                    if ((isLocked || status == "SCHEDULED" || status == "CONFIRMED") && confirmedAt.isNotBlank()) {
                        confirmedSchedules.add(d)
                    }
                }
            }
        } catch (_: Exception) {}

        // Construct MonthDaySummary for each calendar day
        return days.map { dateStr ->
            val dayOfMonth = dateStr.takeLast(2).toInt()
            val weekdayVal = weekday(dateStr)
            val isToday = (dateStr == todayDate)
            val plannedGym = calculatePlannedGymState(dateStr, isConfigured, startDate, segments)

            val dayActivities = activitiesByDate[dateStr].orEmpty()
            val totalActs = dayActivities.size
            var verifiedActs = 0
            var pendingActs = 0
            var missedActs = 0

            var isGymSessionVerified = gymCompletedDates.contains(dateStr) ||
                    approvedReviews.contains("$dateStr:GYM") ||
                    validEvaluations.contains("$dateStr:GYM")

            for (act in dayActivities) {
                val actVerified = act.status in setOf("VERIFIED", "APPROVED", "COMPLETED") ||
                        approvedReviews.contains("$dateStr:act_${act.id}") ||
                        validEvaluations.contains("$dateStr:act_${act.id}")
                val actPending = act.status in setOf("SUBMITTED", "PENDING")
                val actMissed = act.status in setOf("MISSED", "REJECTED")

                if (act.type == "GYM" && actVerified) {
                    isGymSessionVerified = true
                }

                if (actVerified) verifiedActs++
                else if (actPending) pendingActs++
                else if (actMissed) missedActs++
            }

            val gymSessionStatus: String? = calculateGymSessionStatus(
                plannedGym = plannedGym,
                isGymSessionVerified = isGymSessionVerified,
                hasGymSessionPending = dayActivities.any { it.type == "GYM" && it.status in setOf("SUBMITTED", "PENDING") },
                dateStr = dateStr,
                todayDate = todayDate
            )

            val scheduleConfirmed = confirmedSchedules.contains(dateStr)

            MonthDaySummary(
                date = dateStr,
                dayOfMonth = dayOfMonth,
                weekday = weekdayVal,
                isToday = isToday,
                gymState = plannedGym,
                gymSessionStatus = gymSessionStatus,
                totalActivities = totalActs,
                verifiedActivities = verifiedActs,
                pendingActivities = pendingActs,
                missedActivities = missedActs,
                isScheduleConfirmed = scheduleConfirmed
            )
        }
    }

    /**
     * Loads complete, detailed read-only information for a single tapped date.
     */
    fun loadDateDetails(
        db: StrictRecordsLocalDb,
        dateStr: String,
        todayDate: String = GymRoutineDates.today(db.authoritativeNowMs())
    ): DateDetails {
        val (isConfigured, routineInfo) = getGymRoutineConfig(db)
        val (startDate, segments) = routineInfo
        val gymState = calculatePlannedGymState(dateStr, isConfigured, startDate, segments)

        // Read active routine segment & anchor info
        val matchingSegment = segments.filter { isDate(it.startDate) && isDate(it.anchorDate) &&
                dateStr >= it.startDate && (it.endDate == null || (isDate(it.endDate) && dateStr < it.endDate)) }
            .maxByOrNull { it.startDate }
        val anchorDate = matchingSegment?.anchorDate ?: startDate

        // Read scheduled activities (READ-ONLY, no ensureDailyActivities)
        val activities = mutableListOf<ActivityItem>()
        try {
            db.readableDatabase.rawQuery(
                """
                SELECT activity_id, type, date, title, scheduled_time, window_start, window_end,
                       status, submitted_at, points, details_json, configured_points
                FROM scheduled_activities
                WHERE date = ?
                ORDER BY scheduled_time ASC
                """.trimIndent(),
                arrayOf(dateStr)
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    activities.add(
                        ActivityItem(
                            id = cursor.getString(0) ?: "",
                            type = cursor.getString(1) ?: "",
                            title = cursor.getString(3) ?: "",
                            scheduledTime = cursor.getString(4) ?: "",
                            windowStart = cursor.getString(5) ?: "",
                            windowEnd = cursor.getString(6) ?: "",
                            status = cursor.getString(7) ?: "SCHEDULED",
                            submittedAt = cursor.getString(8),
                            points = cursor.getInt(9),
                            detailsJson = cursor.getString(10) ?: "{}",
                            configuredPoints = cursor.getInt(11)
                        )
                    )
                }
            }
        } catch (_: Exception) {}

        // Read proof reviews for this date
        val approvedReviewIds = mutableSetOf<String>()
        var gymReviewApproved = false
        var gymReviewPending = false
        try {
            db.readableDatabase.rawQuery(
                "SELECT activity_id, task_code, status FROM proof_reviews WHERE date = ?",
                arrayOf(dateStr)
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    val actId = cursor.getString(0) ?: ""
                    val taskCode = cursor.getString(1) ?: ""
                    val st = cursor.getString(2) ?: ""
                    if (st in setOf("APPROVED", "VERIFIED")) {
                        if (actId.isNotBlank()) approvedReviewIds.add(actId)
                        if (taskCode == "GYM") gymReviewApproved = true
                    } else if (st in setOf("PENDING", "SUBMITTED")) {
                        if (taskCode == "GYM") gymReviewPending = true
                    }
                }
            }
        } catch (_: Exception) {}

        // Check daily state
        var isGymCompletedInDailyState = false
        try {
            db.readableDatabase.rawQuery(
                "SELECT gym_status FROM daily_state WHERE date = ?",
                arrayOf(dateStr)
            ).use { cursor ->
                if (cursor.moveToFirst()) {
                    val st = cursor.getString(0) ?: ""
                    if (st.startsWith("GYM COMPLETE") || st == "VERIFIED") {
                        isGymCompletedInDailyState = true
                    }
                }
            }
        } catch (_: Exception) {}

        val isGymVerified = isGymCompletedInDailyState || gymReviewApproved ||
                activities.any { it.type == "GYM" && it.status in setOf("VERIFIED", "APPROVED", "COMPLETED") }

        val gymSessionStatus: String? = calculateGymSessionStatus(
            plannedGym = gymState,
            isGymSessionVerified = isGymVerified,
            hasGymSessionPending = gymReviewPending || activities.any { it.type == "GYM" && it.status in setOf("SUBMITTED", "PENDING") },
            dateStr = dateStr,
            todayDate = todayDate
        )

        // Schedule confirmation
        val isScheduleConfirmed = db.isScheduleConfirmedForDate(dateStr)
        val isBathPlanConfirmed = db.isBathScheduleConfirmed(dateStr)
        val bathPlan = if (isBathPlanConfirmed) db.getDailyBathPlan(dateStr) else null
        val committedBathTimes = bathPlan?.optJSONArray("selected_bath_times")?.let { arr ->
            (0 until arr.length()).map { formatTime12h(arr.optString(it)) }.joinToString(", ")
        } ?: ""

        val collegeAttendance = db.getCollegeAttendance(dateStr)

        // Read score events for this date
        val scoreEvents = mutableListOf<ScoreEventItem>()
        try {
            db.readableDatabase.rawQuery(
                "SELECT event_type, description, points, timestamp FROM score_events WHERE date = ? ORDER BY timestamp ASC",
                arrayOf(dateStr)
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    scoreEvents.add(
                        ScoreEventItem(
                            eventType = cursor.getString(0) ?: "",
                            description = cursor.getString(1) ?: "",
                            points = cursor.getInt(2),
                            timestamp = cursor.getString(3) ?: ""
                        )
                    )
                }
            }
        } catch (_: Exception) {}

        // Check if any actual record exists for this date
        val hasAnyData = activities.isNotEmpty() ||
                scoreEvents.isNotEmpty() ||
                isScheduleConfirmed ||
                isBathPlanConfirmed ||
                collegeAttendance.isNotBlank() ||
                isGymVerified

        val routine = GymRoutineClient.routine(db)
        val pauseReason = if (gymState == "PAUSED") {
            routine.optString("reason_category").replace('_', ' ').ifBlank { null }
        } else null

        val isWakeUpDone = db.isWakeUpVerificationCompleted(dateStr)
        val wakeUpStatus = if (isWakeUpDone) "VERIFIED" else if (dateStr < todayDate) "MISSED" else if (dateStr == todayDate) "REQUIRED" else "PLANNED"
        val wakeUpCompletedAt = activities.firstOrNull { it.type == "WAKE_UP" || it.id.contains("WAKEUP") }?.submittedAt

        val bathCount = db.getMaxBathOccurrences(dateStr)
        val isCollege = collegeAttendance == "YES"
        val b1Planned = bathPlan?.optJSONArray("selected_bath_times")?.optString(0) ?: db.getPlannedBathTime(1, isCollege, dateStr).ifBlank { "07:30" }
        val b1Win = BathWindowRules.calculateBathWindow(1, isCollege, dateStr, b1Planned, timezone = TIMEZONE)
        val b1Act = activities.firstOrNull { it.type == "BATH" && (it.id.endsWith("-1") || it.id.endsWith("BATH") || !it.id.endsWith("-2")) }
        val b1Status = b1Act?.status ?: if (dateStr < todayDate) "MISSED" else "PLANNED"

        val (b2WinDisplay, b2ProofDisplay, b2Status) = if (bathCount >= 2) {
            val b2Planned = bathPlan?.optJSONArray("selected_bath_times")?.optString(1) ?: db.getPlannedBathTime(2, isCollege, dateStr).ifBlank { "16:00" }
            val b2Win = BathWindowRules.calculateBathWindow(2, isCollege, dateStr, b2Planned, timezone = TIMEZONE)
            val b2Act = activities.firstOrNull { it.type == "BATH" && it.id.endsWith("-2") }
            Triple(b2Win.activityIntervalDisplay, b2Win.proofWindowDisplay, b2Act?.status ?: if (dateStr < todayDate) "MISSED" else "PLANNED")
        } else {
            Triple(null, null, null)
        }

        val gymLives = db.getGymModeLives()
        val gymSyncForDate = db.getGymModeSyncStatus(dateStr)
        val activeGymSession = if (dateStr == todayDate) db.getGymModeActiveSession() else null
        val hadActivationOnDate = db.getPreviouslyActivatedGymIds(dateStr).isNotEmpty() || gymSyncForDate != "NONE"
        val gymModeStatus = when {
            activeGymSession != null && activeGymSession.isMusicAccessActive -> {
                if (gymSyncForDate == "PENDING_SYNC" || activeGymSession.sessionId.startsWith("gym_off_")) "OFFLINE_ACTIVE" else "ACTIVE"
            }
            gymSyncForDate == "CONFLICT" -> "SYNC_CONFLICT"
            gymSyncForDate == "PENDING_SYNC" -> "ACTIVATED_PENDING_SYNC"
            hadActivationOnDate -> "ACTIVATED"
            else -> "INACTIVE"
        }
        val hasQueuePending = try {
            db.readableDatabase.rawQuery("SELECT 1 FROM sync_queue WHERE status = 'PENDING_SYNC' LIMIT 1", null).use { it.moveToFirst() }
        } catch (_: Exception) { false }
        val isSyncPending = (gymSyncForDate == "PENDING_SYNC") || (dateStr == todayDate && hasQueuePending)
        val dailyGymSetup = db.getDailyGymSetup(dateStr)

        return DateDetails(
            date = dateStr,
            formattedDate = formatDateDisplay(dateStr),
            isToday = (dateStr == todayDate),
            gymState = gymState,
            gymSessionStatus = gymSessionStatus,
            gymRoutineAnchor = anchorDate,
            gymPauseReason = pauseReason,
            isScheduleConfirmed = isScheduleConfirmed,
            isBathPlanConfirmed = isBathPlanConfirmed,
            committedBathTimes = committedBathTimes,
            collegeAttendance = collegeAttendance,
            activities = activities,
            scoreEvents = scoreEvents,
            hasAnyData = hasAnyData,
            wakeUpStatus = wakeUpStatus,
            wakeUpCompletedAt = wakeUpCompletedAt,
            bathSessionCount = bathCount,
            bath1WindowDisplay = b1Win.activityIntervalDisplay,
            bath1ProofWindowDisplay = b1Win.proofWindowDisplay,
            bath1Status = b1Status,
            bath2WindowDisplay = b2WinDisplay,
            bath2ProofWindowDisplay = b2ProofDisplay,
            bath2Status = b2Status,
            gymModeStatus = gymModeStatus,
            gymLivesBalance = gymLives,
            isSyncPending = isSyncPending,
            dailyGymSetup = dailyGymSetup
        )
    }
}
