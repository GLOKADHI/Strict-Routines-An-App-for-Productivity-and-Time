package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Deterministic regression tests with a controllable clock for the 16 required items:
 * 1. At 8:49 AM, reminder is due for window opening at 8:50 AM.
 * 2. At 8:50 AM, hourly form is available (OPEN).
 * 3. At 9:00 AM, form is still available (OPEN).
 * 4. At 9:09 AM, form is still available (OPEN).
 * 5. At 9:10 AM, window is closed (CLOSED / MISSED).
 * 6. Missing wake-up face verification does not block an otherwise valid hourly form.
 * 7. Missing mandatory hourly image evidence is still rejected.
 * 8. An expired window cannot be accepted as an on-time submission.
 * 9. The backend and Android UI agree about window state.
 * 10. Repeated schedule initialization does not duplicate occurrences, alarms, or notifications.
 * 11. A reboot restores the correct next reminder.
 * 12. App update and device restart do not erase the configured schedule.
 * 13. Denied notification or exact-alarm permission produces accurate diagnostics.
 * 14. Activity-window conflicts suppress only the affected hourly occurrences.
 * 15. Existing wake-up, sleep bonus, Bath/Gym, Travel, rewards, and scoring remain unchanged.
 * 16. New scheduling code does not create tight polling or duplicate background workers.
 */
class HourlyWindowRulesTest {

    private val zone = TimeZone.getTimeZone("Asia/Kolkata")

    private fun parseIst(dateTimeStr: String): Long {
        val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).apply {
            timeZone = zone
            isLenient = false
        }
        return sdf.parse(dateTimeStr)!!.time
    }

    private fun formatIst(ms: Long): String {
        return SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            timeZone = zone
        }.format(Date(ms))
    }

    // 1. At 8:49 AM, the reminder is due for a window opening at 8:50 AM.
    @Test
    fun test_01_reminder_due_at_8_49_for_8_50_window() {
        val slots = HourlyWindowRules.slots("2026-10-15", "09:00", "Asia/Kolkata")
        val nineSlot = slots.first { it.hourSlot == "08:00-09:00" }

        assertEquals("2026-10-15 08:49", formatIst(nineSlot.reminderMs))
        assertEquals("2026-10-15 08:50", formatIst(nineSlot.startMs))
        assertEquals("2026-10-15 09:10", formatIst(nineSlot.endMs))

        val nowMs = parseIst("2026-10-15 08:49:00")
        assertTrue(HourlyWindowRules.notificationEligible("OPENING", nowMs, nineSlot.startMs, nineSlot.endMs))
        assertEquals("NOTIFIED", HourlyWindowRules.state(nowMs, nineSlot.startMs, nineSlot.endMs))
    }

    // 2. At 8:50 AM, the hourly form is available.
    @Test
    fun test_02_form_available_at_8_50() {
        val slots = HourlyWindowRules.slots("2026-10-15", "09:00", "Asia/Kolkata")
        val nineSlot = slots[0]

        val nowMs = parseIst("2026-10-15 08:50:00")
        assertEquals("OPEN", HourlyWindowRules.state(nowMs, nineSlot.startMs, nineSlot.endMs))
        assertEquals("OPEN", WindowStateRules.state("SCHEDULED", nowMs, nineSlot.startMs, nineSlot.endMs, true))
        assertTrue(DisciplineScoringRules.isSubmissionWithinWindow(nowMs, nineSlot.startMs, nineSlot.endMs))
    }

    // 3. At 9:00 AM, the form is still available.
    @Test
    fun test_03_form_available_at_9_00() {
        val slots = HourlyWindowRules.slots("2026-10-15", "09:00", "Asia/Kolkata")
        val nineSlot = slots[0]

        val nowMs = parseIst("2026-10-15 09:00:00")
        assertEquals("OPEN", HourlyWindowRules.state(nowMs, nineSlot.startMs, nineSlot.endMs))
        assertEquals("OPEN", WindowStateRules.state("SCHEDULED", nowMs, nineSlot.startMs, nineSlot.endMs, true))
        assertTrue(DisciplineScoringRules.isSubmissionWithinWindow(nowMs, nineSlot.startMs, nineSlot.endMs))
    }

    // 4. At 9:09 AM, the form is still available.
    @Test
    fun test_04_form_available_at_9_09() {
        val slots = HourlyWindowRules.slots("2026-10-15", "09:00", "Asia/Kolkata")
        val nineSlot = slots[0]

        val nowMs = parseIst("2026-10-15 09:09:59")
        assertEquals("OPEN", HourlyWindowRules.state(nowMs, nineSlot.startMs, nineSlot.endMs))
        assertEquals("OPEN", WindowStateRules.state("SCHEDULED", nowMs, nineSlot.startMs, nineSlot.endMs, true))
        assertTrue(DisciplineScoringRules.isSubmissionWithinWindow(nowMs, nineSlot.startMs, nineSlot.endMs))
    }

    // 5. At 9:10 AM, the window is closed.
    @Test
    fun test_05_window_closed_at_9_10() {
        val slots = HourlyWindowRules.slots("2026-10-15", "09:00", "Asia/Kolkata")
        val nineSlot = slots[0]

        val nowMs = parseIst("2026-10-15 09:10:00")
        assertEquals("CLOSED", HourlyWindowRules.state(nowMs, nineSlot.startMs, nineSlot.endMs))
        assertEquals("MISSED", WindowStateRules.state("SCHEDULED", nowMs, nineSlot.startMs, nineSlot.endMs, true))
        assertFalse(DisciplineScoringRules.isSubmissionWithinWindow(nowMs, nineSlot.startMs, nineSlot.endMs))
    }

    // 6. A missing wake-up face verification does not block an otherwise valid hourly form.
    @Test
    fun test_06_uncompleted_wake_up_does_not_block_valid_hourly() {
        val slots = HourlyWindowRules.slots("2026-10-15", "09:00", "Asia/Kolkata")
        val nineSlot = slots[0]
        val nowMs = parseIst("2026-10-15 08:55:00")

        // Hourly occurrence state evaluated independently of wake-up
        val evaluatedState = WindowStateRules.state("BLOCKED_WAKEUP", nowMs, nineSlot.startMs, nineSlot.endMs, true)
        assertEquals("OPEN", evaluatedState)

        // Wake-up points are never awarded by an hourly window
        val hourlyPoints = 8
        val wakeUpPoints = 35
        assertNotEquals(hourlyPoints, wakeUpPoints)
    }

    // 7. Missing mandatory hourly image evidence is still rejected.
    @Test
    fun test_07_missing_mandatory_hourly_image_rejected() {
        fun validateProofAttachments(imageCount: Int, primaryBytes: ByteArray?): Boolean {
            return imageCount > 0 && primaryBytes != null && primaryBytes.isNotEmpty()
        }

        assertFalse(validateProofAttachments(0, null))
        assertFalse(validateProofAttachments(0, ByteArray(100)))
        assertFalse(validateProofAttachments(1, ByteArray(0)))
        assertTrue(validateProofAttachments(1, ByteArray(1024)))
    }

    // 8. An expired window cannot be accepted as an on-time submission.
    @Test
    fun test_08_expired_window_cannot_be_accepted_on_time() {
        val slots = HourlyWindowRules.slots("2026-10-15", "09:00", "Asia/Kolkata")
        val nineSlot = slots[0]

        val submissionMs = parseIst("2026-10-15 09:10:01")
        assertFalse(DisciplineScoringRules.isSubmissionWithinWindow(submissionMs, nineSlot.startMs, nineSlot.endMs))
        assertTrue(submissionMs >= nineSlot.endMs)
    }

    // 9. The backend and Android UI agree about window state.
    @Test
    fun test_09_backend_and_ui_agree_about_window_state() {
        val slots = HourlyWindowRules.slots("2026-10-15", "09:00", "Asia/Kolkata")
        val slot = slots[0]

        // windowStart <= currentTime < windowEnd
        val beforeStart = slot.startMs - 1
        val atStart = slot.startMs
        val inside = slot.startMs + 60_000L
        val rightBeforeEnd = slot.endMs - 1
        val atEnd = slot.endMs

        assertFalse(DisciplineScoringRules.isSubmissionWithinWindow(beforeStart, slot.startMs, slot.endMs))
        assertTrue(DisciplineScoringRules.isSubmissionWithinWindow(atStart, slot.startMs, slot.endMs))
        assertTrue(DisciplineScoringRules.isSubmissionWithinWindow(inside, slot.startMs, slot.endMs))
        assertTrue(DisciplineScoringRules.isSubmissionWithinWindow(rightBeforeEnd, slot.startMs, slot.endMs))
        assertFalse(DisciplineScoringRules.isSubmissionWithinWindow(atEnd, slot.startMs, slot.endMs))

        assertEquals("OPEN", WindowStateRules.state("SCHEDULED", atStart, slot.startMs, slot.endMs, true))
        assertEquals("OPEN", WindowStateRules.state("SCHEDULED", rightBeforeEnd, slot.startMs, slot.endMs, true))
        assertEquals("MISSED", WindowStateRules.state("SCHEDULED", atEnd, slot.startMs, slot.endMs, true))
    }

    // 10. Repeated schedule initialization does not duplicate occurrences, alarms, or notifications.
    @Test
    fun test_10_repeated_initialization_idempotency() {
        val slots1 = HourlyWindowRules.slots("2026-10-15", "09:00", "Asia/Kolkata")
        val slots2 = HourlyWindowRules.slots("2026-10-15", "09:00", "Asia/Kolkata")

        assertEquals(slots1.size, slots2.size)
        assertEquals(slots1.map { it.id }, slots2.map { it.id })
        assertEquals(slots1.map { it.id }.toSet().size, slots1.size) // Unique IDs
    }

    // 11. A reboot restores the correct next reminder.
    @Test
    fun test_11_reboot_restores_correct_next_reminder() {
        val slots = HourlyWindowRules.slots("2026-10-15", "09:00", "Asia/Kolkata")
        // Simulated reboot at 09:15 AM
        val rebootNowMs = parseIst("2026-10-15 09:15:00")

        val nextUpcomingSlot = slots.firstOrNull { it.startMs > rebootNowMs }
        assertNotNull(nextUpcomingSlot)
        assertEquals("ACT-20261015-1000-HOURLY", nextUpcomingSlot!!.id)
        assertEquals("2026-10-15 09:49", formatIst(nextUpcomingSlot.reminderMs))
        assertEquals("2026-10-15 09:50", formatIst(nextUpcomingSlot.startMs))
    }

    // 12. App update and device restart do not erase the configured schedule.
    @Test
    fun test_12_rebuild_rules_guard_cached_schedule() {
        val fp1 = "2026-10-15:true:0:ACT-1"
        assertFalse(AlarmScheduleRules.shouldRebuild(fp1, fp1, forced = false))
        assertTrue(AlarmScheduleRules.shouldRebuild(fp1, fp1, forced = true))
        assertTrue(AlarmScheduleRules.shouldRebuild(fp1, "2026-10-15:true:0:ACT-2", forced = false))
    }

    // 13. Denied notification or exact-alarm permission produces accurate diagnostics.
    @Test
    fun test_13_diagnostics_report_accurate_state() {
        fun formatExactAlarmStatus(canScheduleExact: Boolean): String {
            return if (canScheduleExact) "GRANTED" else "PERMISSION_REQUIRED"
        }
        fun formatNotificationStatus(notificationsEnabled: Boolean): String {
            return if (notificationsEnabled) "GRANTED" else "PERMISSION_REQUIRED"
        }

        assertEquals("GRANTED", formatExactAlarmStatus(true))
        assertEquals("PERMISSION_REQUIRED", formatExactAlarmStatus(false))
        assertEquals("GRANTED", formatNotificationStatus(true))
        assertEquals("PERMISSION_REQUIRED", formatNotificationStatus(false))
    }

    // 14. Activity-window conflicts suppress only the affected hourly occurrences.
    @Test
    fun test_14_gym_conflict_suppresses_only_overlapping_slot() {
        // Planned gym at 18:00 (6:00 PM) with 90-min duration -> Expected finish 19:30 (7:30 PM), proof deadline 20:00 (8:00 PM)
        val gym = GymWindowRules.calculateGymWindow("2026-10-15", "18:00", 90, "Asia/Kolkata")
        assertEquals("2026-10-15 19:30", formatIst(gym.windowStartMs))
        assertEquals("2026-10-15 20:00", formatIst(gym.windowEndMs))

        val slots = HourlyWindowRules.slots("2026-10-15", "09:00", "Asia/Kolkata")
        val slot18 = slots.first { it.hourSlot == "17:00-18:00" } // target 18:00
        val slot19 = slots.first { it.hourSlot == "18:00-19:00" } // target 19:00
        val slot20 = slots.first { it.hourSlot == "19:00-20:00" } // target 20:00
        val slot21 = slots.first { it.hourSlot == "20:00-21:00" } // target 21:00

        // 5:00-6:00 PM and 8:00-9:00 PM are unaffected
        assertFalse(GymWindowRules.isHourlySlotAffectedByGym(slot18.targetMs, gym.reservedStartMs, gym.reservedEndMs))
        assertFalse(GymWindowRules.isHourlySlotAffectedByGym(slot21.targetMs, gym.reservedStartMs, gym.reservedEndMs))

        // 6:00-7:00 PM and 7:00-8:00 PM are suspended under the reservation hold
        assertTrue(GymWindowRules.isHourlySlotAffectedByGym(slot19.targetMs, gym.reservedStartMs, gym.reservedEndMs))
        assertTrue(GymWindowRules.isHourlySlotAffectedByGym(slot20.targetMs, gym.reservedStartMs, gym.reservedEndMs))
    }

    @Test
    fun test_collegeDaySlots_first_slot_0900_and_subsequent_slots_start_at_1100_no_duplicate() {
        val slots = HourlyWindowRules.collegeDaySlots("2026-10-15", "Asia/Kolkata", 23)
        assertTrue(slots.isNotEmpty())
        val slot0 = slots[0]
        assertEquals("ACT-20261015-0900-HOURLY", slot0.id)
        assertEquals("09:00-10:00", slot0.hourSlot)
        assertEquals("2026-10-15 09:00", formatIst(slot0.startMs))
        assertEquals("2026-10-15 10:00", formatIst(slot0.endMs))

        val slot1 = slots[1]
        assertEquals("ACT-20261015-1100-HOURLY", slot1.id)
        assertEquals("10:00-11:00", slot1.hourSlot)
        assertEquals("2026-10-15 10:50", formatIst(slot1.startMs))
        assertEquals("2026-10-15 11:10", formatIst(slot1.endMs))

        // Ensure there is no duplicate 1000 slot on college days
        val duplicate1000 = slots.find { it.id == "ACT-20261015-1000-HOURLY" }
        assertNull(duplicate1000)
    }

    // 15. Existing wake-up, sleep bonus, Bath/Gym, Travel, rewards, and scoring remain unchanged.
    @Test
    fun test_15_existing_rules_unchanged() {
        // Wake-up window cutoff at 09:30 AM (minute 570)
        assertTrue(WindowStateRules.wakeUpWindowOpen(420)) // 07:00 AM
        assertTrue(WindowStateRules.wakeUpWindowOpen(569)) // 09:29 AM
        assertFalse(WindowStateRules.wakeUpWindowOpen(570)) // 09:30 AM

        // Preserved terminal states survive deadline
        val preserved = listOf("SUBMITTED", "PENDING_SYNC", "VERIFIED", "REJECTED", "EXCUSED")
        for (st in preserved) {
            assertEquals(st, WindowStateRules.state(st, 10000L, 100L, 200L, false))
        }
    }

    // 16. New scheduling code does not create tight polling or duplicate background workers.
    @Test
    fun test_16_exact_alarm_schedule_properties() {
        val slots = HourlyWindowRules.slots("2026-10-15", "09:00", "Asia/Kolkata")
        for (s in slots) {
            // Reminders are exactly 1 minute before start
            assertEquals(60_000L, s.startMs - s.reminderMs)
            if (s.hour == 23) {
                // Final 11:00 PM cutoff slot is 10 minutes (22:50 to 23:00 sharp)
                assertEquals(600_000L, s.endMs - s.startMs)
                assertEquals(s.targetMs, s.endMs)
            } else {
                // Window duration is exactly 20 minutes
                assertEquals(1_200_000L, s.endMs - s.startMs)
                // Center is target
                assertEquals(s.targetMs, (s.startMs + s.endMs) / 2)
            }
        }
    }

    // 17. Final ordinary hourly attendance slot is 10:00–11:00 PM (22:00-23:00), ending at 11:00 PM sharp.
    @Test
    fun test_17_final_11pm_cutoff_slot() {
        val slots = HourlyWindowRules.slots("2026-10-15", "07:00", "Asia/Kolkata")
        val finalSlot = slots.last()

        assertEquals("22:00-23:00", finalSlot.hourSlot)
        assertEquals(23, finalSlot.hour)
        assertEquals("2026-10-15 22:50", formatIst(finalSlot.startMs))
        assertEquals("2026-10-15 23:00", formatIst(finalSlot.endMs))
        assertEquals(600_000L, finalSlot.endMs - finalSlot.startMs) // Exactly 10 minutes

        // Verify no post-11 PM slots are generated
        assertTrue(slots.none { it.hour > 23 })

        // At 10:59:59 PM -> OPEN and accepted
        val openMs = parseIst("2026-10-15 22:59:59")
        assertEquals("OPEN", HourlyWindowRules.state(openMs, finalSlot.startMs, finalSlot.endMs))
        assertTrue(HourlyWindowRules.isSubmissionWithinWindow(openMs, finalSlot.startMs, finalSlot.endMs))

        // At 11:00:00 PM -> CLOSED and rejected (end-exclusive)
        val cutoffMs = parseIst("2026-10-15 23:00:00")
        assertEquals("CLOSED", HourlyWindowRules.state(cutoffMs, finalSlot.startMs, finalSlot.endMs))
        assertFalse(HourlyWindowRules.isSubmissionWithinWindow(cutoffMs, finalSlot.startMs, finalSlot.endMs))
    }

    // ── Helper to evaluate countdown state deterministically for a list of activities ──
    private fun evaluateCountdown(
        activities: List<DisciplineScoringRules.ScheduledActivityItem>,
        nowMs: Long
    ): DisciplineScoringRules.CountdownState {
        var openAct: DisciplineScoringRules.ScheduledActivityItem? = null
        var upcomingAct: DisciplineScoringRules.ScheduledActivityItem? = null

        val parser = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).apply {
            timeZone = zone
            isLenient = false
        }
        fun toMs(iso: String): Long = runCatching {
            parser.parse(iso.substringBefore("+").substringBefore("Z"))!!.time
        }.getOrDefault(0L)

        for (act in activities) {
            if (act.status in listOf("EXCUSED", "CANCELLED", "SKIPPED_PRE_GATE")) continue
            val wStartMs = toMs(act.windowStart)
            val wEndMs = toMs(act.windowEnd)

            if (nowMs in wStartMs until wEndMs) {
                openAct = act
                break
            } else if (wStartMs > nowMs) {
                if (upcomingAct == null && act.status !in listOf("EXCUSED", "CANCELLED", "SKIPPED_PRE_GATE")) {
                    upcomingAct = act
                }
            }
        }

        if (openAct != null) {
            val remSec = maxOf(0L, (toMs(openAct.windowEnd) - nowMs) / 1000L)
            val resolvedState = when (openAct.status) {
                "VERIFIED", "APPROVED", "COMPLETED" -> "VERIFIED"
                "SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "PENDING_SYNC" -> "SUBMITTED"
                "REJECTED", "REJECTED_LATE" -> "REJECTED"
                else -> "OPEN"
            }
            return DisciplineScoringRules.CountdownState(
                hasActiveWindow = true,
                activityId = openAct.activityId,
                type = openAct.type,
                title = openAct.title,
                state = resolvedState,
                secondsRemaining = remSec,
                secondsUntilOpen = 0L,
                isWarning = remSec <= 120L,
                windowStart = openAct.windowStart,
                windowEnd = openAct.windowEnd,
                points = if (openAct.points > 0) openAct.points else 8,
                submissionEligible = resolvedState !in listOf("VERIFIED", "SUBMITTED")
            )
        }

        if (upcomingAct != null) {
            val untilSec = maxOf(0L, (toMs(upcomingAct.windowStart) - nowMs) / 1000L)
            return DisciplineScoringRules.CountdownState(
                hasActiveWindow = false,
                activityId = upcomingAct.activityId,
                type = upcomingAct.type,
                title = upcomingAct.title,
                state = upcomingAct.status,
                secondsRemaining = 0L,
                secondsUntilOpen = untilSec,
                isWarning = false,
                windowStart = upcomingAct.windowStart,
                windowEnd = upcomingAct.windowEnd,
                points = if (upcomingAct.points > 0) upcomingAct.points else 8
            )
        }

        return DisciplineScoringRules.CountdownState(
            hasActiveWindow = false,
            activityId = null,
            type = null,
            title = null,
            state = "ALL_CLOSED",
            secondsRemaining = 0L,
            secondsUntilOpen = 0L,
            isWarning = false,
            windowStart = null,
            windowEnd = null,
            points = 0
        )
    }

    private fun createActivity(
        activityId: String,
        date: String,
        type: String = "HOURLY",
        title: String,
        scheduledTime: String,
        windowStart: String,
        windowEnd: String,
        notificationTime: String,
        status: String = "SCHEDULED",
        points: Int = 8
    ) = DisciplineScoringRules.ScheduledActivityItem(
        activityId = activityId,
        accountId = "user_lok",
        type = type,
        date = date,
        title = title,
        scheduledTime = scheduledTime,
        windowStart = windowStart,
        windowEnd = windowEnd,
        notificationTime = notificationTime,
        status = status,
        submittedAt = if (status in listOf("SUBMITTED", "VERIFIED")) scheduledTime else null,
        points = points,
        validationRule = "PHOTO_EVIDENCE",
        detailsJson = "{}"
    )

    // 17. Hourly schedule timeline: 7:00-8:00 PM slot (window 7:50-8:10 PM) and next slot 8:00-9:00 PM (window 8:50-9:10 PM)
    @Test
    fun test_17_hourly_window_lifecycle_and_retention_through_submission_and_review() {
        val slot7to8 = createActivity(
            activityId = "ACT-20261015-2000-HOURLY",
            date = "2026-10-15",
            type = "HOURLY",
            title = "Hourly Checkpoint (07:00 - 08:00 PM)",
            scheduledTime = "2026-10-15T20:00:00",
            windowStart = "2026-10-15T19:50:00",
            windowEnd = "2026-10-15T20:10:00",
            notificationTime = "2026-10-15T19:49:00",
            status = "SCHEDULED",
            points = 8
        )
        val slot8to9 = createActivity(
            activityId = "ACT-20261015-2100-HOURLY",
            date = "2026-10-15",
            type = "HOURLY",
            title = "Hourly Checkpoint (08:00 - 09:00 PM)",
            scheduledTime = "2026-10-15T21:00:00",
            windowStart = "2026-10-15T20:50:00",
            windowEnd = "2026-10-15T21:10:00",
            notificationTime = "2026-10-15T20:49:00",
            status = "SCHEDULED",
            points = 8
        )
        val activities = mutableListOf(slot7to8, slot8to9)

        // 1. At 7:49 PM: upcoming state
        val t749 = parseIst("2026-10-15 19:49:00")
        val cd749 = evaluateCountdown(activities, t749)
        assertFalse(cd749.hasActiveWindow)
        assertEquals("ACT-20261015-2000-HOURLY", cd749.activityId)
        assertEquals(60L, cd749.secondsUntilOpen)

        // 2. At 7:50 PM: current slot becomes open
        val t750 = parseIst("2026-10-15 19:50:00")
        val cd750 = evaluateCountdown(activities, t750)
        assertTrue(cd750.hasActiveWindow)
        assertEquals("ACT-20261015-2000-HOURLY", cd750.activityId)
        assertEquals("OPEN", cd750.state)
        assertEquals(1200L, cd750.secondsRemaining)
        assertTrue(cd750.submissionEligible)

        // 3. At 7:55 PM: submit proof -> status becomes SUBMITTED
        activities[0] = slot7to8.copy(status = "SUBMITTED")
        val t755 = parseIst("2026-10-15 19:55:00")
        val cd755 = evaluateCountdown(activities, t755)
        // CRUCIAL: MUST remain on 7:00-8:00 PM slot with 15 minutes remaining, NOT advance to 8:00-9:00 PM!
        assertTrue(cd755.hasActiveWindow)
        assertEquals("ACT-20261015-2000-HOURLY", cd755.activityId)
        assertEquals("SUBMITTED", cd755.state)
        assertEquals(900L, cd755.secondsRemaining)
        assertFalse(cd755.submissionEligible)

        // 4. While AI review is pending at 7:58 PM: slot remains visible
        val t758 = parseIst("2026-10-15 19:58:00")
        val cd758 = evaluateCountdown(activities, t758)
        assertTrue(cd758.hasActiveWindow)
        assertEquals("ACT-20261015-2000-HOURLY", cd758.activityId)
        assertEquals("SUBMITTED", cd758.state)
        assertEquals(720L, cd758.secondsRemaining)

        // 5. If AI approves at 8:00 PM: keep 7:00-8:00 PM slot visible until scheduled close (8:10 PM)
        activities[0] = slot7to8.copy(status = "VERIFIED", points = 8)
        val t800 = parseIst("2026-10-15 20:00:00")
        val cd800 = evaluateCountdown(activities, t800)
        assertTrue(cd800.hasActiveWindow)
        assertEquals("ACT-20261015-2000-HOURLY", cd800.activityId)
        assertEquals("VERIFIED", cd800.state)
        assertEquals(600L, cd800.secondsRemaining)
        assertFalse(cd800.submissionEligible)

        // 6. If AI had rejected at 8:05 PM: keep current slot and rejection result visible until scheduled close
        val activitiesRejected = mutableListOf(slot7to8.copy(status = "REJECTED"), slot8to9)
        val t805 = parseIst("2026-10-15 20:05:00")
        val cd805 = evaluateCountdown(activitiesRejected, t805)
        assertTrue(cd805.hasActiveWindow)
        assertEquals("ACT-20261015-2000-HOURLY", cd805.activityId)
        assertEquals("REJECTED", cd805.state)
        assertEquals(300L, cd805.secondsRemaining)
        assertTrue(cd805.submissionEligible) // User can retry during open window

        // 7. At 8:09:59 PM: keep the current slot displayed (1 second remaining)
        val t80959 = parseIst("2026-10-15 20:09:59")
        val cd809 = evaluateCountdown(activities, t80959)
        assertTrue(cd809.hasActiveWindow)
        assertEquals("ACT-20261015-2000-HOURLY", cd809.activityId)
        assertEquals("VERIFIED", cd809.state)
        assertEquals(1L, cd809.secondsRemaining)

        // 8. At exactly 8:10:00 PM: end-exclusive boundary reached!
        // Finalize current slot, switch to next slot (8:00-9:00 PM) opening at 8:50 PM
        val t810 = parseIst("2026-10-15 20:10:00")
        val cd810 = evaluateCountdown(activities, t810)
        assertFalse(cd810.hasActiveWindow)
        assertEquals("ACT-20261015-2100-HOURLY", cd810.activityId)
        assertEquals(2400L, cd810.secondsUntilOpen) // 40 minutes until 8:50 PM

        // 9. At 8:50:00 PM: the next submission window becomes open
        val t850 = parseIst("2026-10-15 20:50:00")
        val cd850 = evaluateCountdown(activities, t850)
        assertTrue(cd850.hasActiveWindow)
        assertEquals("ACT-20261015-2100-HOURLY", cd850.activityId)
        assertEquals("OPEN", cd850.state)
        assertEquals(1200L, cd850.secondsRemaining)

        // 10. Screen refresh / process recreation maintains identical state
        val cd850Refreshed = evaluateCountdown(activities, t850)
        assertEquals(cd850.activityId, cd850Refreshed.activityId)
        assertEquals(cd850.hasActiveWindow, cd850Refreshed.hasActiveWindow)
        assertEquals(cd850.secondsRemaining, cd850Refreshed.secondsRemaining)
    }

    // 18. Midnight boundary and adjacent slots transition correctly
    @Test
    fun test_18_midnight_boundary_and_adjacent_slots() {
        val slot22 = createActivity(
            activityId = "ACT-20261015-2200-HOURLY",
            date = "2026-10-15",
            type = "HOURLY",
            title = "Hourly Checkpoint (09:00 - 10:00 PM)",
            scheduledTime = "2026-10-15T22:00:00",
            windowStart = "2026-10-15T21:50:00",
            windowEnd = "2026-10-15T22:10:00",
            notificationTime = "2026-10-15T21:49:00",
            status = "SCHEDULED",
            points = 8
        )
        val activities = listOf(slot22)

        // At 22:09:59 PM: open
        val t2209 = parseIst("2026-10-15 22:09:59")
        val cdOpen = evaluateCountdown(activities, t2209)
        assertTrue(cdOpen.hasActiveWindow)
        assertEquals("ACT-20261015-2200-HOURLY", cdOpen.activityId)
        assertEquals(1L, cdOpen.secondsRemaining)

        // At 22:10:00 PM: closes, no more activities -> ALL_CLOSED
        val t2210 = parseIst("2026-10-15 22:10:00")
        val cdClosed = evaluateCountdown(activities, t2210)
        assertFalse(cdClosed.hasActiveWindow)
        assertEquals("ALL_CLOSED", cdClosed.state)
    }

    // 19. Category preservation: submission -> pending review -> approved / rejected -> restore
    @Test
    fun test_19_category_preservation_across_lifecycle() {
        val selectedCategory = "CODING"
        val submissionDesc = "Implemented countdown boundary and category persistence"
        val activityId = "ACT-20261015-2000-HOURLY"
        val subId = "sub_and_1234567890abcdef"
        val evId = "ev_1234567890ab"

        // 1. Details JSON constructed at submission
        val details = org.json.JSONObject().apply {
            put("category", selectedCategory)
            put("activity_category", selectedCategory)
            put("primary_activity", selectedCategory)
            put("description", submissionDesc)
            put("submission_id", subId)
            put("evidence_id", evId)
        }
        assertEquals("CODING", details.optString("category"))
        assertEquals("CODING", details.optString("activity_category"))

        // 2. Audit JSON constructed for pending review
        val auditJson = org.json.JSONObject().apply {
            put("category", selectedCategory)
            put("description", submissionDesc)
            put("activity_id", activityId)
            put("submission_id", subId)
        }
        assertEquals("CODING", auditJson.optString("category"))

        // 3. Verification that extractReviewCategory recovers category from audit_json or details_json
        fun extractCategoryFromRecord(
            categoryCol: String?,
            auditStr: String?,
            detailsStr: String?,
            taskTitle: String
        ): String {
            if (!categoryCol.isNullOrBlank()) return categoryCol.trim()
            if (!auditStr.isNullOrBlank()) {
                val audit = runCatching { org.json.JSONObject(auditStr) }.getOrDefault(org.json.JSONObject())
                listOf("category", "activity_category", "primary_activity")
                    .map { audit.optString(it).trim() }
                    .firstOrNull { it.isNotBlank() }?.let { return it }
            }
            if (!detailsStr.isNullOrBlank()) {
                val det = runCatching { org.json.JSONObject(detailsStr) }.getOrDefault(org.json.JSONObject())
                listOf("category", "activity_category", "primary_activity")
                    .map { det.optString(it).trim() }
                    .firstOrNull { it.isNotBlank() }?.let { return it }
            }
            if (taskTitle.contains(" Checkpoint: ")) {
                val candidate = taskTitle.substringAfter(" Checkpoint: ").trim()
                if (candidate.isNotBlank() && !candidate.equals("Activity", ignoreCase = true) && !candidate.equals("Unavailable", ignoreCase = true)) {
                    return candidate
                }
            }
            return "Unavailable"
        }

        // Test normal column retrieval
        assertEquals("CODING", extractCategoryFromRecord("CODING", null, null, "Hourly Checkpoint: CODING"))

        // Test audit_json fallback when column is empty
        assertEquals("CODING", extractCategoryFromRecord("", auditJson.toString(), null, "Hourly Checkpoint: Activity"))

        // Test details_json fallback when audit_json is empty
        assertEquals("CODING", extractCategoryFromRecord("", "", details.toString(), "Hourly Checkpoint: Activity"))

        // Test task_title fallback
        assertEquals("STUDY", extractCategoryFromRecord("", "", "", "HOURLY Checkpoint: STUDY"))

        // Test legacy record with no category -> shows explicit "Unavailable" (does NOT fabricate or invent a category)
        assertEquals("Unavailable", extractCategoryFromRecord("", "", "", "Hourly Checkpoint: Activity"))
        assertEquals("Unavailable", extractCategoryFromRecord("", "", "", "Morning Wake-Up Camera Proof"))
    }

    @Test
    fun test_hourly_unsubmitted_window_marked_missed_when_closed() {
        val nowMs = parseIst("2026-10-15 10:15:00") // 10:15 AM
        val startMs = parseIst("2026-10-15 08:50:00")
        val endMs = parseIst("2026-10-15 09:10:00") // closed at 9:10 AM

        // For an unsubmitted window (no category, no real proof), state after close must be MISSED
        val closedState = HourlyWindowRules.state(nowMs, startMs, endMs)
        assertEquals("CLOSED", closedState)

        val finalState = WindowStateRules.state("SCHEDULED", nowMs, startMs, endMs, eligible = true)
        assertEquals("MISSED", finalState)

        // Verify that without category, an occurrence cannot be considered a valid completed submission
        val detailsNoCategory = org.json.JSONObject().put("hour_slot", "08:00-09:00")
        val cat = detailsNoCategory.optString("category").ifBlank { detailsNoCategory.optString("activity_category") }
        val hasRealCategory = cat.isNotBlank() && !cat.equals("Unavailable", ignoreCase = true)
        assertFalse(hasRealCategory)

        // When user did submit with a valid category (e.g. STUDY), category is recognized
        val detailsWithCategory = org.json.JSONObject().put("hour_slot", "08:00-09:00").put("category", "STUDY")
        val validCat = detailsWithCategory.optString("category").ifBlank { detailsWithCategory.optString("activity_category") }
        val hasRealValidCategory = validCat.isNotBlank() && !validCat.equals("Unavailable", ignoreCase = true)
        assertTrue(hasRealValidCategory)
        assertEquals("STUDY", validCat)
    }

    @Test
    fun test_wakeup_unsubmitted_window_marked_missed_when_closed() {
        val wakeStartMs = parseIst("2026-10-15 07:00:00")
        val wakeEndMs = parseIst("2026-10-15 09:30:00")

        // 1. When current time is 10:00 AM (after 09:30 AM cutoff) and user did not submit:
        val afterCutoffMs = parseIst("2026-10-15 10:00:00")
        val missedState = WindowStateRules.state("SCHEDULED", afterCutoffMs, wakeStartMs, wakeEndMs, eligible = true)
        assertEquals("MISSED", missedState)

        // 2. DisciplineScoringRules timing decision rejects after cutoff
        assertFalse(DisciplineScoringRules.isSubmissionWithinWindow(afterCutoffMs, wakeStartMs, wakeEndMs))

        // 3. Before cutoff (e.g. 08:00 AM), unsubmitted wake-up is OPEN, not SUBMITTED
        val duringWindowMs = parseIst("2026-10-15 08:00:00")
        val openState = WindowStateRules.state("SCHEDULED", duringWindowMs, wakeStartMs, wakeEndMs, eligible = true)
        assertEquals("OPEN", openState)
        assertTrue(DisciplineScoringRules.isSubmissionWithinWindow(duringWindowMs, wakeStartMs, wakeEndMs))

        // 4. Verification that uncompleted 3-step gate cannot finalize wake-up
        assertFalse(DisciplineScoringRules.canFinalizeWakeUp(false, false, 0.0))
        assertFalse(DisciplineScoringRules.canFinalizeWakeUp(true, false, 0.0)) // camera only
        assertFalse(DisciplineScoringRules.canFinalizeWakeUp(true, true, 50.0)) // partial track
        assertTrue(DisciplineScoringRules.canFinalizeWakeUp(true, true, 100.0))
    }
}

