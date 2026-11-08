package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Deterministic regression tests with controllable clock for Gym task scheduling
 * and 5-minute pre-opening Quick Action visibility.
 *
 * Algorithm under test:
 * - gymPlannedTime = userSelectedGymTime
 * - gymWindowStart = gymPlannedTime + 2 hours
 * - gymWindowEnd   = gymWindowStart + 1 hour (1-hour window duration)
 * - gymQuickActionVisibleAt = gymWindowStart - 5 minutes
 *
 * Submission window is start-inclusive and end-exclusive:
 * gymWindowStart <= currentTime < gymWindowEnd
 */
class GymWindowRulesTest {

    private val zone = TimeZone.getTimeZone("Asia/Kolkata")

    private fun timeMs(dateStr: String, hhmm: String): Long {
        val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            timeZone = zone
            isLenient = false
        }
        return sdf.parse("$dateStr $hhmm")!!.time
    }

    /**
     * Requirement 1: For an 8:00–8:30 PM window, the action is hidden before 7:55 PM.
     */
    @Test
    fun test1_actionIsHiddenBefore825PmFor830to930Window() {
        // Planned gym at 6:30 PM (18:30) with 90m duration -> proof window 8:00–8:30 PM (20:00–20:30)
        val window = GymWindowRules.calculateGymWindow("2026-10-15", "18:30", "Asia/Kolkata")
        assertEquals(timeMs("2026-10-15", "20:00"), window.windowStartMs)
        assertEquals(timeMs("2026-10-15", "20:30"), window.windowEndMs)
        assertEquals(timeMs("2026-10-15", "19:55"), window.reminderMs)

        // Clock at 7:54 PM (1 ms before 7:55 PM)
        val clockMs = timeMs("2026-10-15", "19:54")
        val clockJustBefore = window.reminderMs - 1L

        assertEquals("UPCOMING", GymWindowRules.state(clockMs, window.windowStartMs, window.windowEndMs))
        assertEquals("UPCOMING", GymWindowRules.state(clockJustBefore, window.windowStartMs, window.windowEndMs))
        assertFalse("Quick Action must be hidden before 7:55 PM",
            GymWindowRules.isQuickActionVisible("SCHEDULED", clockMs, window.windowStartMs, window.windowEndMs))
        assertFalse("Quick Action must be hidden 1 ms before 7:55 PM",
            GymWindowRules.isQuickActionVisible("SCHEDULED", clockJustBefore, window.windowStartMs, window.windowEndMs))
        assertFalse("Submission must not be allowed before window",
            GymWindowRules.isSubmissionWithinWindow(clockMs, window.windowStartMs, window.windowEndMs))
    }

    /**
     * Requirement 2: At 7:55 PM, the reminder appears (REMINDER_VISIBLE).
     */
    @Test
    fun test2_at825PmReminderAppears() {
        val window = GymWindowRules.calculateGymWindow("2026-10-15", "18:30", "Asia/Kolkata")
        val clockMs = timeMs("2026-10-15", "19:55")

        assertEquals(clockMs, window.reminderMs)
        assertEquals("REMINDER_VISIBLE", GymWindowRules.state(clockMs, window.windowStartMs, window.windowEndMs))
        assertEquals("REMINDER_VISIBLE", GymWindowRules.resolveOccurrenceState("SCHEDULED", clockMs, window.windowStartMs, window.windowEndMs))
        assertTrue("Quick Action must become visible at 7:55 PM",
            GymWindowRules.isQuickActionVisible("SCHEDULED", clockMs, window.windowStartMs, window.windowEndMs))
    }

    /**
     * Requirement 3: At 7:59 PM, submission remains unavailable.
     */
    @Test
    fun test3_at829PmSubmissionRemainsUnavailable() {
        val window = GymWindowRules.calculateGymWindow("2026-10-15", "18:30", "Asia/Kolkata")
        val clockMs = timeMs("2026-10-15", "19:59")
        val clockMs1SecBeforeOpen = window.windowStartMs - 1000L

        // Quick action is visible as reminder
        assertEquals("REMINDER_VISIBLE", GymWindowRules.state(clockMs, window.windowStartMs, window.windowEndMs))
        assertTrue(GymWindowRules.isQuickActionVisible("SCHEDULED", clockMs, window.windowStartMs, window.windowEndMs))

        // But submission is strictly unavailable
        assertFalse("Submission must remain unavailable at 7:59 PM",
            GymWindowRules.isSubmissionWithinWindow(clockMs, window.windowStartMs, window.windowEndMs))
        assertFalse("Submission must remain unavailable 1 second before 8:00 PM",
            GymWindowRules.isSubmissionWithinWindow(clockMs1SecBeforeOpen, window.windowStartMs, window.windowEndMs))
        assertFalse("Submission must remain unavailable 1 ms before 8:00 PM",
            GymWindowRules.isSubmissionWithinWindow(window.windowStartMs - 1L, window.windowStartMs, window.windowEndMs))
    }

    /**
     * Requirement 4: At 8:00 PM, submission becomes available (start-inclusive).
     */
    @Test
    fun test4_at830PmSubmissionBecomesAvailable() {
        val window = GymWindowRules.calculateGymWindow("2026-10-15", "18:30", "Asia/Kolkata")
        val clockMs = timeMs("2026-10-15", "20:00")

        assertEquals(clockMs, window.windowStartMs)
        assertEquals("OPEN", GymWindowRules.state(clockMs, window.windowStartMs, window.windowEndMs))
        assertEquals("OPEN", GymWindowRules.resolveOccurrenceState("SCHEDULED", clockMs, window.windowStartMs, window.windowEndMs))
        assertTrue("Quick Action must be visible at 8:00 PM",
            GymWindowRules.isQuickActionVisible("SCHEDULED", clockMs, window.windowStartMs, window.windowEndMs))
        assertTrue("Submission must become available at 8:00 PM (start-inclusive)",
            GymWindowRules.isSubmissionWithinWindow(clockMs, window.windowStartMs, window.windowEndMs))
    }

    /**
     * Requirement 5: At 8:29 PM, submission remains available.
     */
    @Test
    fun test5_at929PmSubmissionRemainsAvailable() {
        val window = GymWindowRules.calculateGymWindow("2026-10-15", "18:30", "Asia/Kolkata")
        val clockMs = timeMs("2026-10-15", "20:15") // mid-window
        val clockMs829 = timeMs("2026-10-15", "20:29") // 8:29 PM
        val clockMsEndMinus1 = window.windowEndMs - 1L // 1 ms before close

        assertEquals("OPEN", GymWindowRules.state(clockMs829, window.windowStartMs, window.windowEndMs))
        assertTrue(GymWindowRules.isQuickActionVisible("SCHEDULED", clockMs829, window.windowStartMs, window.windowEndMs))
        assertTrue("Submission must remain available at 8:29 PM",
            GymWindowRules.isSubmissionWithinWindow(clockMs829, window.windowStartMs, window.windowEndMs))
        assertTrue("Submission must remain available 1 ms before 8:30 PM",
            GymWindowRules.isSubmissionWithinWindow(clockMsEndMinus1, window.windowStartMs, window.windowEndMs))
    }

    /**
     * Requirement 6: At 8:30 PM, submission closes (end-exclusive).
     */
    @Test
    fun test6_at930PmSubmissionCloses() {
        val window = GymWindowRules.calculateGymWindow("2026-10-15", "18:30", "Asia/Kolkata")
        val clockMs = timeMs("2026-10-15", "20:30")

        assertEquals(clockMs, window.windowEndMs)
        assertFalse("Submission must close exactly at 8:30 PM (end-exclusive)",
            GymWindowRules.isSubmissionWithinWindow(clockMs, window.windowStartMs, window.windowEndMs))
        assertFalse("Submission must remain closed after 8:30 PM",
            GymWindowRules.isSubmissionWithinWindow(clockMs + 1000L, window.windowStartMs, window.windowEndMs))
        assertEquals("CLOSED", GymWindowRules.state(clockMs, window.windowStartMs, window.windowEndMs))
        assertEquals("EXPIRED", GymWindowRules.resolveOccurrenceState("SCHEDULED", clockMs, window.windowStartMs, window.windowEndMs))
        assertFalse("Quick Action must be hidden when closed",
            GymWindowRules.isQuickActionVisible("SCHEDULED", clockMs, window.windowStartMs, window.windowEndMs))
    }

    /**
     * Requirement 7: A submitted proof does not generate another reminder.
     */
    @Test
    fun test7_submittedProofDoesNotGenerateAnotherReminder() {
        val window = GymWindowRules.calculateGymWindow("2026-10-15", "18:30", "Asia/Kolkata")
        val reminderClockMs = timeMs("2026-10-15", "19:56")

        // If status is already SUBMITTED or VERIFIED, resolveOccurrenceState keeps real status
        assertEquals("SUBMITTED", GymWindowRules.resolveOccurrenceState("SUBMITTED", reminderClockMs, window.windowStartMs, window.windowEndMs))
        assertEquals("APPROVED", GymWindowRules.resolveOccurrenceState("VERIFIED", reminderClockMs, window.windowStartMs, window.windowEndMs))
        assertFalse("Submitted occurrence must not be visible as a new submission reminder",
            GymWindowRules.isQuickActionVisible("SUBMITTED", reminderClockMs, window.windowStartMs, window.windowEndMs))
        assertFalse("Verified occurrence must not be visible as a reminder",
            GymWindowRules.isQuickActionVisible("VERIFIED", reminderClockMs, window.windowStartMs, window.windowEndMs))
    }

    /**
     * Requirement 8: Planned Gym time generates expected finish and 30-min proof window.
     */
    @Test
    fun test8_planned540PmGenerates740to840ProofWindowAnd735Reminder() {
        val window = GymWindowRules.calculateGymWindow("2026-10-15", "17:40", "Asia/Kolkata")
        assertEquals("17:40", window.plannedHhmm)
        assertEquals("05:40 PM", window.plannedDisplay)
        assertEquals("07:10 PM–07:40 PM", window.windowDisplay)
        assertEquals("19:10-19:40", window.hourSlot)

        val expectedPlannedMs = timeMs("2026-10-15", "17:40")
        val expectedStartMs = timeMs("2026-10-15", "19:10") // 17:40 + 90 min
        val expectedEndMs = timeMs("2026-10-15", "19:40")   // 19:10 + 30 min
        val expectedReminderMs = timeMs("2026-10-15", "19:05") // 7:05 PM

        assertEquals(expectedPlannedMs, window.plannedMs)
        assertEquals(expectedStartMs, window.windowStartMs)
        assertEquals(expectedEndMs, window.windowEndMs)
        assertEquals(expectedReminderMs, window.reminderMs)

        // 5 minutes before 7:10 PM is exactly 7:05 PM
        assertEquals(5 * 60 * 1000L, window.windowStartMs - window.reminderMs)
        assertEquals(90 * 60 * 1000L, window.windowStartMs - window.plannedMs)
        assertEquals(30 * 60 * 1000L, window.windowEndMs - window.windowStartMs)

        // State progression:
        // 7:04 PM -> UPCOMING
        assertEquals("UPCOMING", GymWindowRules.state(timeMs("2026-10-15", "19:04"), window.windowStartMs, window.windowEndMs))
        // 7:05 PM -> REMINDER_VISIBLE
        assertEquals("REMINDER_VISIBLE", GymWindowRules.state(timeMs("2026-10-15", "19:05"), window.windowStartMs, window.windowEndMs))
        // 7:09 PM -> REMINDER_VISIBLE (submission false)
        assertEquals("REMINDER_VISIBLE", GymWindowRules.state(timeMs("2026-10-15", "19:09"), window.windowStartMs, window.windowEndMs))
        assertFalse(GymWindowRules.isSubmissionWithinWindow(timeMs("2026-10-15", "19:09"), window.windowStartMs, window.windowEndMs))
        // 7:10 PM -> OPEN (submission true)
        assertEquals("OPEN", GymWindowRules.state(timeMs("2026-10-15", "19:10"), window.windowStartMs, window.windowEndMs))
        assertTrue(GymWindowRules.isSubmissionWithinWindow(timeMs("2026-10-15", "19:10"), window.windowStartMs, window.windowEndMs))
        // 7:40 PM -> CLOSED (submission false)
        assertEquals("CLOSED", GymWindowRules.state(timeMs("2026-10-15", "19:40"), window.windowStartMs, window.windowEndMs))
        assertFalse(GymWindowRules.isSubmissionWithinWindow(timeMs("2026-10-15", "19:40"), window.windowStartMs, window.windowEndMs))
    }

    /**
     * Rollover across midnight.
     */
    @Test
    fun test_nearMidnightHandlesDateRollover() {
        val win = GymWindowRules.calculateGymWindow("2026-10-15", "23:15", "Asia/Kolkata")
        val expectedStartMs = timeMs("2026-10-16", "00:45")
        val expectedEndMs = timeMs("2026-10-16", "01:15")
        val expectedReminderMs = timeMs("2026-10-16", "00:40")

        assertEquals(expectedStartMs, win.windowStartMs)
        assertEquals(expectedEndMs, win.windowEndMs)
        assertEquals(expectedReminderMs, win.reminderMs)

        assertFalse(GymWindowRules.isSubmissionWithinWindow(expectedStartMs - 1L, win.windowStartMs, win.windowEndMs))
        assertTrue(GymWindowRules.isSubmissionWithinWindow(expectedStartMs, win.windowStartMs, win.windowEndMs))
        assertFalse(GymWindowRules.isSubmissionWithinWindow(expectedEndMs, win.windowStartMs, win.windowEndMs))
    }

    /**
     * Hourly conflict checks preserved.
     */
    @Test
    fun test_hourlyConflictChecksPreserved() {
        val gymPlannedM = 17 * 60 + 40
        assertFalse(GymWindowRules.conflictsWithHourlyMinutes(17 * 60, gymPlannedM))
        assertFalse(GymWindowRules.conflictsWithHourlyMinutes(18 * 60, gymPlannedM))
        assertFalse(GymWindowRules.conflictsWithHourlyMinutes(19 * 60, gymPlannedM))
        assertTrue(GymWindowRules.conflictsWithHourlyMinutes(20 * 60, gymPlannedM))
        assertFalse(GymWindowRules.conflictsWithHourlyMinutes(21 * 60, gymPlannedM))
    }

    /**
     * Requirement 16: Existing Bath, hourly attendance, wake-up verification, reward, and scoring unchanged.
     */
    @Test
    fun test16_existingBathHourlyWakeupScoringUnchanged() {
        assertEquals("ON_TIME", DisciplineScoringRules.evaluateWakeUpAttendance("07:05"))
        assertEquals("LATE", DisciplineScoringRules.evaluateWakeUpAttendance("07:06"))
        assertTrue(DisciplineScoringRules.canFinalizeWakeUp(true, true, 100.0))
        assertFalse(DisciplineScoringRules.canFinalizeWakeUp(true, true, 99.0))

        val hourlySlots = HourlyWindowRules.slots("2026-10-15", "07:00")
        assertEquals(17, hourlySlots.size)
        assertEquals("ACT-20261015-0700-HOURLY", hourlySlots[0].id)

        val pts = listOf(15, 5, 10, 10, 35, 15, 8, -5, -4)
        assertEquals(89, DisciplineScoringRules.calculateDailyScore(pts))
    }
}
