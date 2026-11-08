package com.strictrecords.app

import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/** Read-only view of one saved bath occurrence; never derives its activity from the proof opening. */
data class BathScheduleRecord(
    val activityId: String,
    val plannedMs: Long,
    val activityEndMs: Long,
    val proofWindowStartMs: Long,
    val proofWindowEndMs: Long,
    val reminderMs: Long,
    val assignedSlot: BathWindowRules.BathReservedSlot,
    val timezone: String,
    val assignmentValid: Boolean = true
) {
    private val zone get() = TimeZone.getTimeZone(timezone)
    val plannedDisplay get() = BathWindowRules.formatClock12h(plannedMs, zone)
    val activityIntervalDisplay get() = BathWindowRules.formatInterval12h(plannedMs, activityEndMs, zone)
    val proofWindowDisplay get() = BathWindowRules.formatInterval12h(proofWindowStartMs, proofWindowEndMs, zone)
    val heldHourlySlotDisplay get() = if (assignmentValid) assignedSlot.displayRange else "Unavailable — saved attendance reservation needs review"
    val reservationId get() = "bath-reservation:$activityId:$plannedMs:$activityEndMs:${BathWindowRules.ATTENDANCE_POLICY}"
    val proofOpeningDisplay get() = ""
    val proofDeadlineDisplay get() = ""
    fun matchesHourly(occurrenceId: String, targetMs: Long): Boolean =
        assignmentValid && assignedSlot.occurrenceId == occurrenceId && assignedSlot.targetMs == targetMs
    fun overlapWithHourly(windowStartMs: Long, windowEndMs: Long): String =
        if (!assignmentValid) "NONE" else BathWindowRules.classifyAttendanceOverlap(
            windowStartMs, windowEndMs, plannedMs, activityEndMs)

    fun assignmentDetails(original: JSONObject): JSONObject = JSONObject(original.toString()).apply {
        put("held_hourly_act_id",assignedSlot.occurrenceId)
        put("held_hourly_target_ms",assignedSlot.targetMs)
        put("held_hourly_slot",assignedSlot.hourSlot)
        put("held_hourly_slot_display",heldHourlySlotDisplay)
        put("attendance_reservation_policy",BathWindowRules.ATTENDANCE_POLICY)
        put("bath_reservation_id",reservationId)
        put("activity_interval",activityIntervalDisplay)
        put("proof_requirements","NONE")
        put("validation_rule","EXEMPT_ACTIVITY")
    }

    fun proofStatus(open: Boolean): String = "Scheduled activity · No proof required"

    fun reminderMessage(event: String): String = "Scheduled Bath ($activityIntervalDisplay) · No proof required" 

    companion object {
        fun fromWindow(window: BathWindowRules.BathWindow, timezone: String) = BathScheduleRecord(
            window.actId, window.plannedMs, window.reservedEndMs, window.proofWindowStartMs,
            window.proofWindowEndMs, window.reminderMs,
            BathWindowRules.resolveBathReservedHourlySlot(window.date, window.plannedHhmm, timezone), timezone)

        fun fromSaved(date: String, activityId: String, scheduledTime: String, windowStart: String,
                      windowEnd: String, notificationTime: String, details: JSONObject, timezone: String,
                      preserveSavedAssignment: Boolean = false): BathScheduleRecord {
            fun parse(value: String): Long? = runCatching {
                SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).apply { isLenient = false }
                    .parse(value)?.time
            }.getOrNull()
            val plannedHhmm = details.optString("planned_time").ifBlank { scheduledTime.substringAfter('T', "").take(5) }
            require(plannedHhmm.isNotBlank()) { "Saved bath planned time is unavailable" }
            val duration = details.optInt("activity_duration_minutes",0).takeIf { it in 1..180 }
            val calculated = BathWindowRules.calculateBathWindow(details.optInt("occurrence",1),
                details.optBoolean("is_college", false), date, plannedHhmm, timezone=timezone,durationMinutes=duration)
            val parsedScheduled = parse(scheduledTime)
            val planned = if (parsedScheduled != null && ScheduleClock.display(parsedScheduled, timezone, "HH:mm") == BathWindowRules.normalizeHhmm(plannedHhmm)) {
                parsedScheduled
            } else {
                calculated.plannedMs
            }
            val activityEnd = parse(details.optString("reserved_end"))?.takeIf { it > planned && (it - planned) <= 180 * 60_000L }
                ?: (planned + (calculated.reservedEndMs - calculated.plannedMs))
            val parsedStart = parse(windowStart) ?: parse(details.optString("proof_window_start"))
            val start = if (parsedStart != null && parsedStart >= activityEnd) parsedStart else calculated.proofWindowStartMs
            val parsedEnd = parse(windowEnd) ?: parse(details.optString("proof_window_end"))
            val end = if (parsedEnd != null && parsedEnd > start) parsedEnd else calculated.proofWindowEndMs
            val canonical = BathWindowRules.resolveBathReservedHourlySlot(date,
                ScheduleClock.display(planned,timezone,"HH:mm"),timezone)
            // Historical display retains an internally consistent saved assignment.
            // Mutable occurrences always derive the assignment from their current activity.
            val savedTarget = details.optLong("held_hourly_target_ms",0L)
            val savedId = details.optString("held_hourly_act_id")
            val expectedId = if (savedTarget > 0L) "ACT-${ScheduleClock.display(savedTarget,timezone,"yyyyMMdd-HHmm")}-HOURLY" else ""
            val identityValid = !preserveSavedAssignment || savedId.isBlank() || savedId == (if(savedTarget > 0L) expectedId else canonical.occurrenceId)
            val assigned = if (preserveSavedAssignment && savedTarget > 0L && savedId == expectedId) {
                val slotStart = savedTarget - 3_600_000L
                val startHm = ScheduleClock.display(slotStart,timezone,"HH:mm")
                val endHm = ScheduleClock.display(savedTarget,timezone,"HH:mm")
                BathWindowRules.BathReservedSlot(startHm,endHm,endHm,slotStart,savedTarget,savedTarget,
                    savedId,"$startHm-$endHm",BathWindowRules.formatInterval12h(slotStart,savedTarget,TimeZone.getTimeZone(timezone)))
            } else canonical
            return BathScheduleRecord(activityId,planned,activityEnd,
                start,end,parse(notificationTime) ?: start-60_000L,assigned,timezone,identityValid && activityEnd > planned)
        }

        val protectedStatuses = setOf("SUBMITTED","VALIDATING","AI_PROVISIONAL","PENDING_SYNC","UNDER_REVIEW",
            "VERIFIED","APPROVED","COMPLETED")

        fun canExcuseHourly(status: String, submittedAt: String?, points: Int): Boolean =
            status !in protectedStatuses && submittedAt.isNullOrBlank()

        /** Translate the planning-range wire format once, before a new/open occurrence is saved. */
        fun normalizeIncoming(activity: JSONObject, timezone: String): JSONObject {
            val result = JSONObject(activity.toString())
            val details = runCatching { JSONObject(result.optString("details_json","{}")) }.getOrDefault(JSONObject())
            val planned = result.getString("scheduled_time").substringAfter('T').take(5)
            val legacyDuration=runCatching {
                val parser=SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX",Locale.US)
                val start=parser.parse(result.getString("scheduled_time"))!!.time
                val end=parser.parse(details.getString("reserved_end"))!!.time
                ((end-start)/60_000L).toInt().takeIf { it in 1..180 && end-start == it*60_000L }
            }.getOrNull()
            val window = BathWindowRules.calculateBathWindow(details.optInt("occurrence",1),details.optBoolean("is_college",false),
                result.getString("date"),planned,timezone=timezone,
                durationMinutes=details.optInt("activity_duration_minutes",0).takeIf { it in 1..180 } ?: legacyDuration)
            result.put("window_start",window.proofWindowStartIso).put("window_end",window.proofWindowEndIso)
                .put("notification_time",window.notificationIso)
            details.put("planned_time",window.plannedHhmm).put("planned_display",window.plannedDisplay)
                .put("reserved_start",window.reservedStartIso).put("reserved_end",window.reservedEndIso)
                .put("activity_duration_minutes",((window.reservedEndMs-window.reservedStartMs)/60_000L).toInt())
                .put("activity_interval",window.activityIntervalDisplay)
                .put("proof_window_start",window.proofWindowStartIso).put("proof_window_end",window.proofWindowEndIso)
                .put("proof_window_display",window.proofWindowDisplay).put("proof_deadline",window.proofWindowEndIso)
            val saved=fromSaved(result.getString("date"),result.getString("activity_id"),window.plannedIso,
                window.proofWindowStartIso,window.proofWindowEndIso,window.notificationIso,details,timezone)
            return result.put("details_json",saved.assignmentDetails(details).toString())
        }

        fun preserveAssignmentOnSync(status: String, details: JSONObject, incomingStatus: String): Boolean =
            status == "EXCUSED" && details.optBoolean("suppressed_by_bath") &&
                incomingStatus in setOf("SCHEDULED","UPCOMING","NOTIFIED","OPEN","MISSED","CLOSED","PENDING_SYNC","EXCUSED")
    }
}
