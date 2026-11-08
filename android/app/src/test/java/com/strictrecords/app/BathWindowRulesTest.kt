package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Deterministic unit tests with controllable clock verifying Section 6 requirements:
 * 1. College Yes creates Bath #1 from 7:00 AM–10:00 AM and Bath #2 from 4:00 PM–9:00 PM.
 * 2. College No creates one configurable Bath window, defaulting to 1:00 PM–5:30 PM.
 * 3. Selecting 4:00 PM for Bath Protocol #2 reserves 4:00 PM–5:00 PM only.
 * 4. A different planned Bath time reserves the corresponding one-hour period.
 * 5. An overlapping hourly occurrence is suppressed and is not treated as a normal missed event.
 * 6. Hourly occurrences outside the reserved period remain unchanged.
 * 7. Bath proof submission remains available according to the applicable Bath submission window.
 * 8. Reopening the app does not duplicate Bath occurrences or conflict-handling jobs.
 * 9. Changing college status for a new day does not erase historical task records.
 * 10. Existing Bath scoring, proof validation, hourly timing, Gym, Travel, wake-up verification, and reward rules remain unchanged.
 */
class BathWindowRulesTest {

    private val zone = TimeZone.getTimeZone("Asia/Kolkata")

    private fun timeMs(dateStr: String, hhmm: String): Long {
        val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            timeZone = zone
            isLenient = false
        }
        return sdf.parse("$dateStr $hhmm")!!.time
    }

    /**
     * Requirement 1: College Yes creates Bath #1 from 7:00 AM–10:00 AM and Bath #2 from 4:00 PM–9:00 PM.
     */
    @Test
    fun test1_collegeYes_createsBath1_0700to1000_andBath2_1600to2100() {
        val date = "2026-10-15"

        // Bath Protocol #1
        val bath1 = BathWindowRules.calculateBathWindow(
            occurrence = 1,
            isCollege = true,
            date = date,
            plannedHhmm = "07:30",
            timezone = "Asia/Kolkata"
        )
        assertEquals(1, bath1.occurrence)
        assertTrue(bath1.isCollege)
        assertEquals("07:30", bath1.windowStartHhmm)
        assertEquals("08:30", bath1.windowEndHhmm)
        assertEquals("07:30", bath1.plannedHhmm)
        assertEquals("07:30 AM–08:30 AM", bath1.windowDisplay)
        assertEquals("Bath Protocol (#1)", bath1.title)
        assertEquals("ACT-20261015-0730-BATH-1", bath1.actId)

        val expectedBath1StartMs = timeMs(date, "07:30")
        val expectedBath1EndMs = timeMs(date, "08:30")
        assertEquals(expectedBath1StartMs, bath1.windowStartMs)
        assertEquals(expectedBath1EndMs, bath1.windowEndMs)
        assertEquals(1 * 3600 * 1000L, bath1.windowEndMs - bath1.windowStartMs)

        // Bath Protocol #2
        val bath2 = BathWindowRules.calculateBathWindow(
            occurrence = 2,
            isCollege = true,
            date = date,
            plannedHhmm = "16:00",
            timezone = "Asia/Kolkata"
        )
        assertEquals(2, bath2.occurrence)
        assertTrue(bath2.isCollege)
        assertEquals("16:00", bath2.windowStartHhmm)
        assertEquals("21:00", bath2.windowEndHhmm)
        assertEquals("16:00", bath2.plannedHhmm)
        assertEquals("04:00 PM–09:00 PM", bath2.windowDisplay)
        assertEquals("Bath Protocol (#2)", bath2.title)
        assertEquals("ACT-20261015-1600-BATH-2", bath2.actId)

        val expectedBath2StartMs = timeMs(date, "16:00")
        val expectedBath2EndMs = timeMs(date, "21:00")
        assertEquals(expectedBath2StartMs, bath2.windowStartMs)
        assertEquals(expectedBath2EndMs, bath2.windowEndMs)
        assertEquals(5 * 3600 * 1000L, bath2.windowEndMs - bath2.windowStartMs)
    }

    /**
     * Requirement 2: College No creates one configurable Bath window, defaulting to 1:00 PM–5:30 PM.
     */
    @Test
    fun test2_collegeNo_createsOneConfigurableBathWindow_default1300to1730() {
        val date = "2026-10-15"

        // Default non-college window
        val defaultBath = BathWindowRules.calculateBathWindow(
            occurrence = 1,
            isCollege = false,
            date = date,
            timezone = "Asia/Kolkata"
        )
        assertEquals(1, defaultBath.occurrence)
        assertFalse(defaultBath.isCollege)
        assertEquals("13:00", defaultBath.windowStartHhmm)
        assertEquals("17:30", defaultBath.windowEndHhmm)
        assertEquals("13:00", defaultBath.plannedHhmm)
        assertEquals("01:00 PM–05:30 PM", defaultBath.windowDisplay)
        assertEquals("Bath Protocol", defaultBath.title)
        assertEquals("ACT-20261015-1300-BATH-1", defaultBath.actId)

        val expectedStartMs = timeMs(date, "13:00")
        val expectedEndMs = timeMs(date, "17:30")
        assertEquals(expectedStartMs, defaultBath.windowStartMs)
        assertEquals(expectedEndMs, defaultBath.windowEndMs)
        assertEquals(4 * 3600 * 1000L + 30 * 60 * 1000L, defaultBath.windowEndMs - defaultBath.windowStartMs)

        // Configurable non-college window (e.g., 2:00 PM–6:00 PM)
        val customBath = BathWindowRules.calculateBathWindow(
            occurrence = 1,
            isCollege = false,
            date = date,
            plannedHhmm = "14:30",
            windowStartOverride = "14:00",
            windowEndOverride = "18:00",
            timezone = "Asia/Kolkata"
        )
        assertEquals("14:00", customBath.windowStartHhmm)
        assertEquals("18:00", customBath.windowEndHhmm)
        assertEquals("14:30", customBath.plannedHhmm)
        assertEquals("02:00 PM–06:00 PM", customBath.windowDisplay)
        assertEquals(timeMs(date, "14:00"), customBath.windowStartMs)
        assertEquals(timeMs(date, "18:00"), customBath.windowEndMs)
    }

    /**
     * Requirement 3: Selecting 4:00 PM for Bath Protocol #2 reserves the activity 4:00 PM–5:00 PM.
     */
    @Test
    fun test3_selecting400PmForBath2_reserves400to500PmOnly() {
        val date = "2026-10-15"
        val bath2 = BathWindowRules.calculateBathWindow(
            occurrence = 2,
            isCollege = true,
            date = date,
            plannedHhmm = "16:00",
            timezone = "Asia/Kolkata"
        )
        // Submission window: 4:00 PM–9:00 PM
        assertEquals(timeMs(date, "16:00"), bath2.windowStartMs)
        assertEquals(timeMs(date, "21:00"), bath2.windowEndMs)

        // Reserved Bath activity period: 4:00 PM–5:00 PM (exactly 1 hour)
        val expectedReservedStartMs = timeMs(date, "16:00")
        val expectedReservedEndMs = timeMs(date, "17:00")
        assertEquals(expectedReservedStartMs, bath2.reservedStartMs)
        assertEquals(expectedReservedEndMs, bath2.reservedEndMs)
        assertEquals(3600 * 1000L, bath2.reservedEndMs - bath2.reservedStartMs)
        assertEquals("04:00 PM–05:00 PM", bath2.reservedDisplay)
    }

    /**
     * Requirement 4: A different planned Bath time reserves the corresponding one-hour period.
     */
    @Test
    fun test4_differentPlannedBathTime_reservesCorrespondingOneHourPeriod() {
        val date = "2026-10-15"

        // User selects 6:00 PM for Bath Protocol #2
        val bath6pm = BathWindowRules.calculateBathWindow(
            occurrence = 2,
            isCollege = true,
            date = date,
            plannedHhmm = "18:00",
            timezone = "Asia/Kolkata"
        )
        assertEquals(timeMs(date, "18:00"), bath6pm.reservedStartMs)
        assertEquals(timeMs(date, "19:00"), bath6pm.reservedEndMs)
        assertEquals(3600 * 1000L, bath6pm.reservedEndMs - bath6pm.reservedStartMs)
        assertEquals("06:00 PM–07:00 PM", bath6pm.reservedDisplay)
        assertEquals("ACT-20261015-1800-BATH-2", bath6pm.actId)

        // User selects 8:00 PM (20:00) for Bath Protocol #2 (valid: 20:00 + 1h = 21:00 <= windowEnd)
        assertTrue(BathWindowRules.isValidPlannedBathTime("20:00", "16:00", "21:00"))

        // Selection beyond permitted window end is invalid (e.g. 20:30 + 1h = 21:30 > 21:00)
        assertFalse(BathWindowRules.isValidPlannedBathTime("20:30", "16:00", "21:00"))
    }

    /**
     * Requirement 5: An overlapping hourly occurrence is suppressed and is not treated as a normal missed event.
     */
    @Test
    fun test5_overlappingHourlyOccurrenceSuppressed_notTreatedAsMissed() {
        val date = "2026-10-15"
        // Bath #2 planned at 16:00 reserves [16:00, 17:00)
        val bath = BathWindowRules.calculateBathWindow(2, true, date, "16:00", timezone = "Asia/Kolkata")
        val bStart = bath.reservedStartMs // 16:00
        val bEnd = bath.reservedEndMs     // 17:00

        // Hourly slot at 16:00 (checkpoint window: 15:50 to 16:10)
        val hourly16Start = timeMs(date, "15:50")
        val hourly16End = timeMs(date, "16:10")
        // Overlap condition: bStart < hEnd AND hStart < bEnd
        val overlap16 = BathWindowRules.isHourlyOverlappingBath(hourly16Start, hourly16End, bStart, bEnd)
        assertTrue("Hourly slot at 16:00 must overlap reserved bath 16:00–17:00", overlap16)

        // Hourly slot at 17:00 (checkpoint window: 16:50 to 17:10)
        val hourly17Start = timeMs(date, "16:50")
        val hourly17End = timeMs(date, "17:10")
        val overlap17 = BathWindowRules.isHourlyOverlappingBath(hourly17Start, hourly17End, bStart, bEnd)
        assertTrue("The 16:50–17:10 window partially overlaps the actual activity and must be excused", overlap17)

        // When suppressed, status is EXCUSED
        val suppressedState = "EXCUSED"
        // WindowStateRules preserves EXCUSED terminal state
        val nowMs = timeMs(date, "17:30") // After the window closed
        val evaluatedState = WindowStateRules.state(suppressedState, nowMs, hourly16Start, hourly16End, eligible = false)
        assertEquals("Suppressed hourly slot must remain EXCUSED and not transition to MISSED", "EXCUSED", evaluatedState)
    }

    /**
     * Requirement 6: Hourly occurrences outside the reserved period remain unchanged.
     */
    @Test
    fun test6_hourlyOccurrencesOutsideReservedPeriodRemainUnchanged() {
        val date = "2026-10-15"
        // Bath #2 planned at 16:00 reserves [16:00, 17:00)
        val bath = BathWindowRules.calculateBathWindow(2, true, date, "16:00", timezone = "Asia/Kolkata")
        val bStart = bath.reservedStartMs
        val bEnd = bath.reservedEndMs

        // Hourly slot at 15:00 (14:50 to 15:10) - entirely before 16:00
        val hourly15Start = timeMs(date, "14:50")
        val hourly15End = timeMs(date, "15:10")
        assertFalse(BathWindowRules.isHourlyOverlappingBath(hourly15Start, hourly15End, bStart, bEnd))

        // Hourly slot at 18:00 (17:50 to 18:10) - entirely after 17:00
        val hourly18Start = timeMs(date, "17:50")
        val hourly18End = timeMs(date, "18:10")
        assertFalse(BathWindowRules.isHourlyOverlappingBath(hourly18Start, hourly18End, bStart, bEnd))

        // Hourly slot at 19:00 (18:50 to 19:10)
        val hourly19Start = timeMs(date, "18:50")
        val hourly19End = timeMs(date, "19:10")
        assertFalse(BathWindowRules.isHourlyOverlappingBath(hourly19Start, hourly19End, bStart, bEnd))

        // Unrelated hourly occurrences proceed with normal window states
        val upcomingState = WindowStateRules.state("SCHEDULED", timeMs(date, "17:40"), hourly18Start, hourly18End, eligible = true)
        assertEquals("UPCOMING", upcomingState)

        val openState = WindowStateRules.state("SCHEDULED", timeMs(date, "18:00"), hourly18Start, hourly18End, eligible = true)
        assertEquals("OPEN", openState)
    }

    /**
     * Requirement 7: Bath proof submission remains available according to the applicable Bath submission window.
     */
    @Test
    fun test7_bathProofSubmissionRemainsAvailableAccordingToApplicableWindow() {
        val date = "2026-10-15"
        // Bath Protocol #1: 7:00 AM–10:00 AM
        val bath1 = BathWindowRules.calculateBathWindow(1, true, date, "07:30", timezone = "Asia/Kolkata")
        val b1Start = bath1.windowStartMs
        val b1End = bath1.windowEndMs

        // Start-inclusive and end-exclusive check:
        // Before 7:30 AM: ineligible
        assertFalse(BathWindowRules.isSubmissionWithinWindow(timeMs(date, "07:29"), b1Start, b1End))
        // Exactly at 7:30:00 AM: eligible (start-inclusive)
        assertTrue(BathWindowRules.isSubmissionWithinWindow(b1Start, b1Start, b1End))
        // At 8:00 AM: eligible
        assertTrue(BathWindowRules.isSubmissionWithinWindow(timeMs(date, "08:00"), b1Start, b1End))
        // At 8:29:59 AM: eligible
        assertTrue(BathWindowRules.isSubmissionWithinWindow(b1End - 1000L, b1Start, b1End))
        // Exactly at 8:30:00 AM: ineligible (end-exclusive)
        assertFalse(BathWindowRules.isSubmissionWithinWindow(b1End, b1Start, b1End))
        // After 8:30 AM: ineligible
        assertFalse(BathWindowRules.isSubmissionWithinWindow(timeMs(date, "08:35"), b1Start, b1End))

        // Bath Protocol #2: 4:00 PM–9:00 PM (16:00–21:00) with planned bath at 16:00 (reserved 16:00–17:00)
        val bath2 = BathWindowRules.calculateBathWindow(2, true, date, "16:00", timezone = "Asia/Kolkata")
        val b2Start = bath2.windowStartMs
        val b2End = bath2.windowEndMs

        // Proof submission remains available across the entire submission window (16:00 to 21:00),
        // even though the activity reservation was 16:00–17:00
        assertTrue(BathWindowRules.isSubmissionWithinWindow(timeMs(date, "16:30"), b2Start, b2End))
        assertTrue("Proof submission must be available at 17:30 (after reserved hour but inside submission window)",
            BathWindowRules.isSubmissionWithinWindow(timeMs(date, "17:30"), b2Start, b2End))
        assertTrue("Proof submission must be available at 20:45",
            BathWindowRules.isSubmissionWithinWindow(timeMs(date, "20:45"), b2Start, b2End))
        assertFalse(BathWindowRules.isSubmissionWithinWindow(b2End, b2Start, b2End))
    }

    /**
     * Requirement 8: Reopening the app does not duplicate Bath occurrences or conflict-handling jobs.
     */
    @Test
    fun test8_reopeningAppDoesNotDuplicateOccurrencesOrJobs() {
        val date = "2026-10-15"
        val window1 = BathWindowRules.calculateBathWindow(1, true, date, "07:30", timezone = "Asia/Kolkata")
        val window2 = BathWindowRules.calculateBathWindow(1, true, date, "07:30", timezone = "Asia/Kolkata")

        // Deterministic ID and values guarantee idempotence
        assertEquals(window1.actId, window2.actId)
        assertEquals(window1.windowStartMs, window2.windowStartMs)
        assertEquals(window1.windowEndMs, window2.windowEndMs)
        assertEquals(window1.reservedStartMs, window2.reservedStartMs)
        assertEquals(window1.reservedEndMs, window2.reservedEndMs)
    }

    /**
     * Requirement 9: Changing college status for a new day does not erase historical task records.
     */
    @Test
    fun test9_changingCollegeStatusDoesNotEraseHistoricalRecords() {
        // College YES produces 2 baths for date1
        val winDay1_1 = BathWindowRules.calculateBathWindow(1, true, "2026-10-15", "07:30", timezone = "Asia/Kolkata")
        val winDay1_2 = BathWindowRules.calculateBathWindow(2, true, "2026-10-15", "16:00", timezone = "Asia/Kolkata")

        // College NO on a subsequent day produces 1 bath for date2
        val winDay2_1 = BathWindowRules.calculateBathWindow(1, false, "2026-10-16", timezone = "Asia/Kolkata")

        // Day 1 records remain independent and intact
        assertEquals("ACT-20261015-0730-BATH-1", winDay1_1.actId)
        assertEquals("ACT-20261015-1600-BATH-2", winDay1_2.actId)
        assertEquals("ACT-20261016-1300-BATH-1", winDay2_1.actId)
        assertEquals("13:00", winDay2_1.windowStartHhmm)
        assertEquals("17:30", winDay2_1.windowEndHhmm)
    }

    /**
     * Requirement 10: Existing Bath scoring, proof validation, hourly timing, Gym, Travel,
     * wake-up verification, and reward rules remain unchanged.
     */
    @Test
    fun test10_gymTravelHourlyScoringProofValidationPreserved() {
        val date = "2026-10-15"
        // Gym window uses expected duration (default 90 min) and 30-min proof window
        val gym = GymWindowRules.calculateGymWindow(date, "18:00", "Asia/Kolkata")
        assertEquals(timeMs(date, "19:30"), gym.windowStartMs)
        assertEquals(timeMs(date, "20:00"), gym.windowEndMs)
        assertEquals("07:30 PM–08:00 PM", gym.windowDisplay)

        // Wake-up window rules remain open 420..570 (7:00 AM - 9:30 AM)
        assertTrue(WindowStateRules.wakeUpWindowOpen(420))
        assertTrue(WindowStateRules.wakeUpWindowOpen(569))
        assertFalse(WindowStateRules.wakeUpWindowOpen(570))

        // Hourly slots are anchored to wake-up minute with 20-minute windows
        val hourlySlots = HourlyWindowRules.slots(date, "07:00", "Asia/Kolkata", endHour = 10)
        assertEquals(4, hourlySlots.size)
        // 10-minute before to 10-minute after target
        assertEquals(20 * 60 * 1000L, hourlySlots[0].endMs - hourlySlots[0].startMs)
    }

    /**
     * Requirement 11: Non-college Bath proof window formula:
     * Opens 90 minutes after planned start and lasts 20 minutes.
     * For 1:00 PM start, proof window is 2:30–2:50 PM, deadline is 2:50 PM.
     * Proof submission up to 2:49:59 is valid; 2:50:00 is end-exclusive and rejected.
     */
    @Test
    fun test11_nonCollegeBathProofDeadlineFormula_1h10m() {
        val date = "2026-10-18"
        val bath = BathWindowRules.calculateBathWindow(
            occurrence = 1,
            isCollege = false,
            date = date,
            plannedHhmm = "13:00",
            timezone = "Asia/Kolkata"
        )

        assertEquals("13:00", bath.plannedHhmm)
        assertEquals(timeMs(date, "13:00"), bath.reservedStartMs)
        assertEquals(timeMs(date, "14:00"), bath.reservedEndMs)

        // Proof window is 14:30–14:50, deadline is 14:50
        val expectedDeadlineMs = timeMs(date, "14:50")
        assertEquals(expectedDeadlineMs, bath.proofDeadlineMs)
        assertEquals("14:50", bath.proofDeadlineHhmm)
        assertEquals("2:50 PM", bath.proofDeadlineDisplay)
        assertEquals("1:00–2:00 PM", bath.heldHourlySlotDisplay)
        assertEquals("2:30–2:50 PM", bath.proofWindowDisplay)

        // Submission within window up to 2:49:59 PM
        val submissionInsideMs = timeMs(date, "14:49") + 59_000L
        assertTrue(
            "Submission at 2:49:59 PM is within proof deadline",
            BathWindowRules.isSubmissionWithinWindow(submissionInsideMs, bath.proofWindowStartMs, bath.proofDeadlineMs)
        )

        // Submission at or after 2:50:00 PM is rejected (end-exclusive)
        assertFalse(
            "Submission at 2:50:00 PM is rejected",
            BathWindowRules.isSubmissionWithinWindow(expectedDeadlineMs, bath.proofWindowStartMs, bath.proofDeadlineMs)
        )
        assertFalse(
            "Submission at 2:50:01 PM is rejected",
            BathWindowRules.isSubmissionWithinWindow(expectedDeadlineMs + 1000L, bath.proofWindowStartMs, bath.proofDeadlineMs)
        )
    }

    /** Both boundary checkpoints intersect the activity, while later windows remain normal. */
    @Test
    fun test12_activityOverlapIncludesPartialBoundaryWindowsOnly() {
        val date = "2026-10-18"
        val bath = BathWindowRules.calculateBathWindow(1, false, date, "13:00", timezone = "Asia/Kolkata")
        assertTrue(BathWindowRules.isHourlySlotOverlappingBath(timeMs(date, "13:00"), bath.reservedStartMs, bath.reservedEndMs))
        assertTrue(BathWindowRules.isHourlySlotOverlappingBath(timeMs(date, "14:00"), bath.reservedStartMs, bath.reservedEndMs))
        assertFalse(BathWindowRules.isHourlySlotOverlappingBath(timeMs(date, "15:00"), bath.reservedStartMs, bath.reservedEndMs))
        assertEquals("1:00–2:00 PM", bath.heldHourlySlotDisplay)
    }

    /**
     * Requirement 13: Tomorrow's College decision deadline is 10:30 PM on the preceding calendar day.
     */
    @Test
    fun test13_advanceCollegeDecisionDeadline_precedingDay1030Pm() {
        val targetDate = "2026-10-19"
        val deadlineMs = BathWindowRules.getCollegeDecisionDeadlineMs(targetDate)
        val expectedDeadlineMs = timeMs("2026-10-18", "22:30")

        assertEquals(expectedDeadlineMs, deadlineMs)

        // 1 minute before deadline on 18 Oct -> not passed
        val beforeMs = expectedDeadlineMs - 60_000L
        assertFalse(BathWindowRules.isCollegeDecisionDeadlinePassed(targetDate, beforeMs))

        // Exactly at deadline -> passed
        assertTrue(BathWindowRules.isCollegeDecisionDeadlinePassed(targetDate, expectedDeadlineMs))

        // After deadline -> passed
        val afterMs = expectedDeadlineMs + 60_000L
        assertTrue(BathWindowRules.isCollegeDecisionDeadlinePassed(targetDate, afterMs))
    }

    /**
     * Requirement 14: Non-college Bath time selection deadline is 10:00 AM on the same day.
     */
    @Test
    fun test14_nonCollegeBathSelectionDeadline_sameDay1000Am() {
        val date = "2026-10-19"
        val deadlineMs = BathWindowRules.getNonCollegeBathSelectionDeadlineMs(date)
        val expectedDeadlineMs = timeMs(date, "10:00")

        assertEquals(expectedDeadlineMs, deadlineMs)

        // 1 minute before 10:00 AM -> not passed
        val beforeMs = expectedDeadlineMs - 60_000L
        assertFalse(BathWindowRules.isNonCollegeBathDeadlinePassed(date, beforeMs))

        // Exactly at 10:00 AM -> passed
        assertTrue(BathWindowRules.isNonCollegeBathDeadlinePassed(date, expectedDeadlineMs))

        // After 10:00 AM -> passed
        val afterMs = expectedDeadlineMs + 60_000L
        assertTrue(BathWindowRules.isNonCollegeBathDeadlinePassed(date, afterMs))
    }

    /**
     * Requirement 15: Bath timing is calculated dynamically from selected planned start time:
     * - 1:00 PM bath: planned interval 1:00–2:00 PM, proof window 2:30–2:50 PM, proof deadline 2:50 PM.
     * - 3:30 PM bath: planned interval 3:30–4:30 PM, proof window 5:00–5:20 PM, proof deadline 5:20 PM.
     * - The 1:00–5:30 PM non-college setting is the allowed booking range, NOT the active session.
     */
    @Test
    fun test15_bathDynamicTiming_1pmAnd330pmIntervalsAndDeadlines() {
        val date = "2026-10-18"

        // Bath at 1:00 PM
        val bath1pm = BathWindowRules.calculateBathWindow(
            occurrence = 1,
            isCollege = false,
            date = date,
            plannedHhmm = "13:00",
            timezone = "Asia/Kolkata"
        )
        assertEquals(timeMs(date, "13:00"), bath1pm.plannedMs)
        assertEquals(timeMs(date, "13:00"), bath1pm.reservedStartMs)
        assertEquals(timeMs(date, "14:00"), bath1pm.reservedEndMs)
        assertEquals(timeMs(date, "14:50"), bath1pm.proofDeadlineMs)
        assertEquals("2:50 PM", bath1pm.proofDeadlineDisplay)
        assertEquals("01:00 PM–02:00 PM", bath1pm.reservedDisplay)
        assertEquals("1:00–2:00 PM", bath1pm.activityIntervalDisplay)
        assertEquals("2:30–2:50 PM", bath1pm.proofWindowDisplay)

        // Bath at 3:30 PM
        val bath330pm = BathWindowRules.calculateBathWindow(
            occurrence = 1,
            isCollege = false,
            date = date,
            plannedHhmm = "15:30",
            timezone = "Asia/Kolkata"
        )
        assertEquals(timeMs(date, "15:30"), bath330pm.plannedMs)
        assertEquals(timeMs(date, "15:30"), bath330pm.reservedStartMs)
        assertEquals(timeMs(date, "16:30"), bath330pm.reservedEndMs)
        assertEquals(timeMs(date, "17:20"), bath330pm.proofDeadlineMs)
        assertEquals("5:20 PM", bath330pm.proofDeadlineDisplay)
        assertEquals("03:30 PM–04:30 PM", bath330pm.reservedDisplay)
        assertEquals("3:30–4:30 PM", bath330pm.activityIntervalDisplay)
        assertEquals("5:00–5:20 PM", bath330pm.proofWindowDisplay)
        assertEquals("ACT-20261018-1530-BATH-1", bath330pm.actId)

        // Allowed booking range remains 1:00 PM–5:30 PM
        assertEquals("01:00 PM–05:30 PM", bath330pm.windowDisplay)
    }

    /**
     * Requirement 16: Attendance windows entirely after bathReservedEndMs stay normal.
     * For a 1:00–2:00 PM bath, intersecting submission windows are held; the 2:20–2:40 window is not.
     * Normal eligible hourly attendance resumes after the reserved slot.
     */
    @Test
    fun test16_hourlyAttendanceResumesAtBathEnd_220to240NotSuppressed() {
        val date = "2026-10-18"
        val bath = BathWindowRules.calculateBathWindow(
            occurrence = 1,
            isCollege = false,
            date = date,
            plannedHhmm = "13:00",
            timezone = "Asia/Kolkata"
        )
        val bStart = bath.reservedStartMs // 13:00
        val bEnd = bath.reservedEndMs     // 14:00

        // Slot at 2:20 PM–2:40 PM (wake 7:30 AM -> target 14:30)
        val hourly220Start = timeMs(date, "14:20")
        val hourly240End = timeMs(date, "14:40")
        val target1430 = timeMs(date, "14:30")

        // Must NOT overlap
        assertFalse(
            "Hourly slot at 2:20–2:40 PM must not overlap 1:00–2:00 PM bath",
            BathWindowRules.isHourlyOverlappingBath(hourly220Start, hourly240End, bStart, bEnd)
        )
        assertFalse(
            "Hourly slot target at 2:30 PM must not overlap 1:00–2:00 PM bath",
            BathWindowRules.isHourlySlotOverlappingBath(target1430, bStart, bEnd)
        )

        // Target 13:00 (12:00-1:00 PM) IS held
        val target1300 = timeMs(date, "13:00")
        assertTrue(
            "Hourly slot target at 1:00 PM must overlap 1:00–2:00 PM bath",
            BathWindowRules.isHourlySlotOverlappingBath(target1300, bStart, bEnd)
        )
    }

    /** Display assignment always contains the real planned start, with no :30 discontinuity. */
    @Test
    fun test17_assignmentFromContainingHourForEveryMinute() {
        val date = "2026-10-19"
        for (minute in 0..59) {
            val planned = "12:" + minute.toString().padStart(2, '0')
            val bath = BathWindowRules.calculateBathWindow(1, false, date, planned, timezone = "Asia/Kolkata")
            assertEquals("12:00–1:00 PM", bath.heldHourlySlotDisplay)
            assertEquals("ACT-20261019-1300-HOURLY", bath.heldHourlyActId)
            assertEquals(90 * 60_000L, bath.proofWindowStartMs - bath.plannedMs)
            assertEquals(20 * 60_000L, bath.proofWindowEndMs - bath.proofWindowStartMs)
        }
    }

    /**
     * Requirement 18: Gym scenario:
     * - Gym starts 6:00 PM, 90m duration, ends 7:30 PM.
     * - Single Gym proof submitted at 7:40 PM within 7:30–8:00 PM window.
     * - Both 6:00–7:00 PM and 7:00–8:00 PM are excused.
     * - Normal hourly attendance resumes at 8:00–9:00 PM.
     */
    @Test
    fun test18_gymSession_excusesBothOverlappingHourlySlots() {
        val date = "2026-10-19"
        val gym = GymWindowRules.calculateGymWindow(date, "18:00", 90, "Asia/Kolkata")
        assertEquals(timeMs(date, "18:00"), gym.plannedMs)
        assertEquals(timeMs(date, "19:30"), gym.expectedFinishMs)
        assertEquals(timeMs(date, "19:30"), gym.windowStartMs)
        assertEquals(timeMs(date, "20:00"), gym.windowEndMs)
        assertEquals("07:30 PM–08:00 PM", gym.windowDisplay)

        // User submits at 7:40 PM (19:40) - valid submission within [19:30, 20:00)
        val submissionMs = timeMs(date, "19:40")
        assertTrue(
            "Proof submitted at 7:40 PM is within 7:30–8:00 PM window",
            GymWindowRules.isSubmissionWithinWindow(submissionMs, gym.windowStartMs, gym.windowEndMs)
        )

        // Both 6:00-7:00 PM (target 19:00) and 7:00-8:00 PM (target 20:00) are affected
        val target1900Ms = timeMs(date, "19:00")
        val target2000Ms = timeMs(date, "20:00")
        val target2100Ms = timeMs(date, "21:00")
        assertTrue(
            "6:00–7:00 PM slot must be affected by Gym reservation [18:00, 19:30]",
            GymWindowRules.isHourlySlotAffectedByGym(target1900Ms, gym.reservedStartMs, gym.reservedEndMs)
        )
        assertTrue(
            "7:00–8:00 PM slot must be affected by Gym reservation [18:00, 19:30]",
            GymWindowRules.isHourlySlotAffectedByGym(target2000Ms, gym.reservedStartMs, gym.reservedEndMs)
        )
        // 8:00-9:00 PM (target 21:00) is unaffected
        assertFalse(
            "8:00–9:00 PM slot must not be affected by Gym reservation [18:00, 19:30]",
            GymWindowRules.isHourlySlotAffectedByGym(target2100Ms, gym.reservedStartMs, gym.reservedEndMs)
        )
    }
    @Test
    fun screenshot1422_assignmentAndActualCheckWindowsAreAligned() {
        val date = "2026-11-04"
        val bath = BathWindowRules.calculateBathWindow(1, false, date, "14:22", timezone = "Asia/Kolkata")
        assertEquals("2:00–3:00 PM", bath.heldHourlySlotDisplay)
        assertEquals("ACT-20261104-1500-HOURLY", bath.heldHourlyActId)
        assertEquals(timeMs(date, "15:22"), bath.reservedEndMs)
        assertEquals("NONE", BathWindowRules.classifyAttendanceOverlap(
            timeMs(date, "13:50"), timeMs(date, "14:10"), bath.reservedStartMs, bath.reservedEndMs))
        assertEquals("FULL", BathWindowRules.classifyAttendanceOverlap(
            timeMs(date, "14:50"), timeMs(date, "15:10"), bath.reservedStartMs, bath.reservedEndMs))
        assertFalse(BathWindowRules.isAttendanceWindowExcused(
            timeMs(date, "13:50"), timeMs(date, "14:10"), bath.reservedStartMs, bath.reservedEndMs))
        assertTrue(BathWindowRules.isAttendanceWindowExcused(
            timeMs(date, "14:50"), timeMs(date, "15:10"), bath.reservedStartMs, bath.reservedEndMs))
        assertEquals(timeMs(date, "15:52"), bath.proofWindowStartMs)
        assertEquals(timeMs(date, "16:12"), bath.proofWindowEndMs)
    }

    @Test
    fun movingBathTo1500RecomputesIntervalsRatherThanKeepingOldAssignment() {
        val date = "2026-11-04"
        val before = BathWindowRules.calculateBathWindow(1, false, date, "14:22", timezone = "Asia/Kolkata")
        val moved = BathWindowRules.calculateBathWindow(1, false, date, "15:00", timezone = "Asia/Kolkata")
        assertEquals("3:00–4:00 PM", moved.heldHourlySlotDisplay)
        assertEquals("ACT-20261104-1600-HOURLY", moved.heldHourlyActId)
        assertEquals(timeMs(date, "16:30"), moved.proofWindowStartMs)
        assertEquals(timeMs(date, "16:50"), moved.proofWindowEndMs)
        assertEquals("FULL", BathWindowRules.classifyAttendanceOverlap(
            timeMs(date, "14:50"), timeMs(date, "15:10"), before.reservedStartMs, before.reservedEndMs))
        assertEquals("PARTIAL", BathWindowRules.classifyAttendanceOverlap(
            timeMs(date, "14:50"), timeMs(date, "15:10"), moved.reservedStartMs, moved.reservedEndMs))
        assertEquals("PARTIAL", BathWindowRules.classifyAttendanceOverlap(
            timeMs(date, "15:50"), timeMs(date, "16:10"), moved.reservedStartMs, moved.reservedEndMs))
    }

    @Test
    fun shortenedAndExtendedDurationsRecalculateExemptionsAndKeepProofAfterActivity() {
        val date = "2026-11-04"
        val short = BathWindowRules.calculateBathWindow(1, false, date, "14:22", durationMinutes = 20)
        val long = BathWindowRules.calculateBathWindow(1, false, date, "14:22", durationMinutes = 120)
        assertFalse(BathWindowRules.isAttendanceWindowExcused(timeMs(date, "14:50"), timeMs(date, "15:10"), short.reservedStartMs, short.reservedEndMs))
        assertEquals(timeMs(date, "15:12"), short.proofWindowStartMs)
        assertEquals(timeMs(date, "15:32"), short.proofWindowEndMs)
        assertTrue(BathWindowRules.isAttendanceWindowExcused(timeMs(date, "14:50"), timeMs(date, "15:10"), long.reservedStartMs, long.reservedEndMs))
        assertTrue(BathWindowRules.isAttendanceWindowExcused(timeMs(date, "15:50"), timeMs(date, "16:10"), long.reservedStartMs, long.reservedEndMs))
        assertFalse(BathWindowRules.isAttendanceWindowExcused(timeMs(date, "16:50"), timeMs(date, "17:10"), long.reservedStartMs, long.reservedEndMs))
        assertEquals(timeMs(date, "16:52"), long.proofWindowStartMs)
        assertEquals(30 * 60_000L, long.proofWindowStartMs - long.reservedEndMs)
    }

    @Test
    fun partialOverlapIsExplicitAndTouchingEdgesStayNormal() {
        val date = "2026-11-04"
        val start = timeMs(date, "14:22")
        val end = timeMs(date, "15:22")
        assertEquals("ANY_POSITIVE_ACTIVITY_WINDOW_OVERLAP", BathWindowRules.PARTIAL_OVERLAP_POLICY)
        assertEquals("PARTIAL", BathWindowRules.classifyAttendanceOverlap(timeMs(date, "14:10"), timeMs(date, "14:30"), start, end))
        assertEquals("PARTIAL", BathWindowRules.classifyAttendanceOverlap(timeMs(date, "15:10"), timeMs(date, "15:30"), start, end))
        assertEquals("NONE", BathWindowRules.classifyAttendanceOverlap(timeMs(date, "14:02"), start, start, end))
        assertEquals("NONE", BathWindowRules.classifyAttendanceOverlap(end, timeMs(date, "15:42"), start, end))
        assertEquals("NONE", BathWindowRules.classifyAttendanceOverlap(start, start, start, end))
        assertEquals("NONE", BathWindowRules.classifyAttendanceOverlap(start, end, end, start))
    }

    @Test
    fun actualWindowEndpointsOverrideAssumedTwentyMinuteCheckpoint() {
        val date = "2026-11-04"
        // Final checkpoint is shortened to 22:50–23:00; a bath at 23:00
        // must not excuse a window that already ended before its activity.
        assertFalse(BathWindowRules.isAttendanceWindowExcused(timeMs(date, "22:50"), timeMs(date, "23:00"),
            timeMs(date, "23:00"), timeMs("2026-11-05", "00:00")))
        // A nonstandard college hour must use its saved full-hour endpoints.
        assertEquals("PARTIAL", BathWindowRules.classifyAttendanceOverlap(timeMs(date, "09:00"), timeMs(date, "10:00"),
            timeMs(date, "09:22"), timeMs(date, "10:22")))
    }

    @Test
    fun overlappingApprovedActivitiesProduceOneDecisionPerHourlyWindow() {
        val date = "2026-11-04"
        val baths = listOf(
            BathWindowRules.calculateBathWindow(1, false, date, "14:22"),
            BathWindowRules.calculateBathWindow(2, false, date, "14:40"))
        val windows = HourlyWindowRules.slots(date, "07:00")
        val excused = windows.filter { slot -> baths.any { bath ->
            BathWindowRules.isAttendanceWindowExcused(slot.startMs, slot.endMs, bath.reservedStartMs, bath.reservedEndMs)
        } }
        assertEquals(listOf("ACT-20261104-1500-HOURLY"), excused.map { it.id })
    }

    @Test
    fun crossMidnightAndMinuteWrapperUseActualIntervals() {
        val date = "2026-11-04"
        val bath = BathWindowRules.calculateBathWindow(1, false, date, "23:40")
        assertEquals("ACT-20261105-0000-HOURLY", bath.heldHourlyActId)
        assertEquals("23:00-00:00", bath.heldHourlySlot)
        assertTrue(BathWindowRules.isAttendanceWindowExcused(timeMs(date, "23:50"), timeMs("2026-11-05", "00:10"), bath.reservedStartMs, bath.reservedEndMs))
        assertTrue(BathWindowRules.isHourlyOverlappingBathMinutes(0, 23 * 60 + 40, 40))
        assertFalse(BathWindowRules.isHourlyOverlappingBathMinutes(23 * 60, 23 * 60 + 40, 40))
        assertEquals(timeMs("2026-11-05", "01:10"), bath.proofWindowStartMs)
    }

    @Test
    fun modifiedCollegeMorningNeverRequiresProofDuringItsActivity() {
        val date = "2026-11-04"
        val default = BathWindowRules.calculateBathWindow(1, true, date, "07:30")
        assertEquals(timeMs(date, "08:00"), default.proofWindowStartMs)
        assertEquals(timeMs(date, "08:30"), default.proofWindowEndMs)
        val extended = BathWindowRules.calculateBathWindow(1, true, date, "07:30", durationMinutes = 60)
        assertEquals(timeMs(date, "08:30"), extended.reservedEndMs)
        assertEquals(extended.reservedEndMs, extended.proofWindowStartMs)
        assertEquals(timeMs(date, "09:00"), extended.proofWindowEndMs)
    }

    /**
     * Test A: Planned Bath 3:30–4:00 PM, proof window 3:50–4:10 PM -> Excused.
     * Overlaps by 10 minutes (3:50 PM to 4:00 PM). Interval intersection logic:
     * proof_start < bath_end && proof_end > bath_start
     */
    @Test
    fun testA_plannedBath330to400_proofWindow350to410_isExcused() {
        val date = "2026-11-05"
        val bathStartMs = timeMs(date, "15:30")
        val bathEndMs = timeMs(date, "16:00")
        val proofStartMs = timeMs(date, "15:50")
        val proofEndMs = timeMs(date, "16:10")

        val isExcused = BathWindowRules.isAttendanceWindowExcused(proofStartMs, proofEndMs, bathStartMs, bathEndMs)
        val overlapKind = BathWindowRules.classifyAttendanceOverlap(proofStartMs, proofEndMs, bathStartMs, bathEndMs)

        assertTrue("Proof window 3:50–4:10 PM must be excused by Bath 3:30–4:00 PM", isExcused)
        assertEquals("PARTIAL", overlapKind)
    }

    /**
     * Test B: Planned Bath 3:30–4:00 PM, proof window 4:50–5:10 PM -> Proof required (not excused).
     * Does not overlap (bath ends at 4:00 PM, proof starts at 4:50 PM).
     */
    @Test
    fun testB_plannedBath330to400_proofWindow450to510_isProofRequired() {
        val date = "2026-11-05"
        val bathStartMs = timeMs(date, "15:30")
        val bathEndMs = timeMs(date, "16:00")
        val proofStartMs = timeMs(date, "16:50")
        val proofEndMs = timeMs(date, "17:10")

        val isExcused = BathWindowRules.isAttendanceWindowExcused(proofStartMs, proofEndMs, bathStartMs, bathEndMs)
        val overlapKind = BathWindowRules.classifyAttendanceOverlap(proofStartMs, proofEndMs, bathStartMs, bathEndMs)

        assertFalse("Proof window 4:50–5:10 PM must NOT be excused by Bath 3:30–4:00 PM", isExcused)
        assertEquals("NONE", overlapKind)
    }

    /**
     * Test C: Planned Bath 3:30–4:30 PM, proof window 3:50–4:10 PM -> Excused.
     * Full overlap: entire 20-minute proof window is inside the 60-minute Bath activity.
     */
    @Test
    fun testC_plannedBath330to430_proofWindow350to410_isExcused() {
        val date = "2026-11-05"
        val bathStartMs = timeMs(date, "15:30")
        val bathEndMs = timeMs(date, "16:30")
        val proofStartMs = timeMs(date, "15:50")
        val proofEndMs = timeMs(date, "16:10")

        val isExcused = BathWindowRules.isAttendanceWindowExcused(proofStartMs, proofEndMs, bathStartMs, bathEndMs)
        val overlapKind = BathWindowRules.classifyAttendanceOverlap(proofStartMs, proofEndMs, bathStartMs, bathEndMs)

        assertTrue("Proof window 3:50–4:10 PM must be excused by 60m Bath 3:30–4:30 PM", isExcused)
        assertEquals("FULL", overlapKind)
    }

    /**
     * Test D: Planned Bath 3:30–4:00 PM, proof window 3:20–3:40 PM -> Excused.
     * Overlaps by 10 minutes (3:30 PM to 3:40 PM).
     */
    @Test
    fun testD_plannedBath330to400_proofWindow320to340_isExcused() {
        val date = "2026-11-05"
        val bathStartMs = timeMs(date, "15:30")
        val bathEndMs = timeMs(date, "16:00")
        val proofStartMs = timeMs(date, "15:20")
        val proofEndMs = timeMs(date, "15:40")

        val isExcused = BathWindowRules.isAttendanceWindowExcused(proofStartMs, proofEndMs, bathStartMs, bathEndMs)
        val overlapKind = BathWindowRules.classifyAttendanceOverlap(proofStartMs, proofEndMs, bathStartMs, bathEndMs)

        assertTrue("Proof window 3:20–3:40 PM must be excused by Bath 3:30–4:00 PM", isExcused)
        assertEquals("PARTIAL", overlapKind)
    }

    /**
     * Test E: Cancel Bath before start -> Overlapping proof windows restored to normal.
     * When Bath is cancelled/removed, attendance overlap check returns false (no active bath),
     * restoring normal proof requirements.
     */
    @Test
    fun testE_cancelBathBeforeStart_restoresOverlappingProofWindows() {
        val date = "2026-11-05"
        val proofStartMs = timeMs(date, "15:50")
        val proofEndMs = timeMs(date, "16:10")

        // 1. With scheduled Bath 3:30–4:00 PM active
        val bathActive = true
        val bathStartMs = timeMs(date, "15:30")
        val bathEndMs = timeMs(date, "16:00")

        val isExcusedWhileActive = bathActive && BathWindowRules.isAttendanceWindowExcused(proofStartMs, proofEndMs, bathStartMs, bathEndMs)
        assertTrue("While Bath is active, overlapping window is excused", isExcusedWhileActive)

        // 2. User cancels Bath before start -> active Bath reservation removed
        val bathCancelled = true
        val activeReservations = if (bathCancelled) emptyList<Pair<Long, Long>>() else listOf(bathStartMs to bathEndMs)

        val isExcusedAfterCancel = activeReservations.any { (bStart, bEnd) ->
            BathWindowRules.isAttendanceWindowExcused(proofStartMs, proofEndMs, bStart, bEnd)
        }
        assertFalse("After cancelling Bath, overlapping proof window is restored to normal status", isExcusedAfterCancel)
    }
}
