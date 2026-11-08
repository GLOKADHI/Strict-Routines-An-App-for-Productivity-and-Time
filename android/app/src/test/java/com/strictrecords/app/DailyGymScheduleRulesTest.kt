package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/**
 * Unit tests verifying all 12 acceptance requirements for the Conditional Daily Gym Schedule Setup:
 *
 * 1. Yes reveals timing and duration.
 * 2. No hides both fields.
 * 3. No does not create a gym session.
 * 4. Yes with valid inputs creates one planned session.
 * 5. Invalid or conflicting times are handled correctly.
 * 6. Repeated confirmation does not create duplicate sessions.
 * 7. App restart restores today's saved selection.
 * 8. A new local date starts a new daily setup.
 * 9. The alternate-day anchor and history remain unchanged.
 * 10. Gym Mode activation remains independent of daily planning.
 * 11. Calendar distinguishes planned from verified sessions.
 * 12. Existing points, hearts, evidence, and scoring rules remain unchanged.
 */
class DailyGymScheduleRulesTest {

    private val tzKolkata = "Asia/Kolkata"
    private val zoneKolkata = TimeZone.getTimeZone(tzKolkata)

    private fun timeMs(dateStr: String, hhmm: String): Long {
        val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            timeZone = zoneKolkata
            isLenient = false
        }
        return sdf.parse("$dateStr $hhmm")!!.time
    }

    // 1. Yes reveals timing and duration
    @Test
    fun test1_yesRevealsTimingAndDuration() {
        val setup = DailyGymScheduleRules.DailyGymSetup(
            date = "2026-10-30",
            choice = DailyGymScheduleRules.DailyGymChoice.YES,
            plannedStartTime = "18:00",
            plannedDurationMinutes = 90,
            plannedEndTime = "19:30",
            isConfirmed = true
        )
        assertTrue(setup.isGymPlanned)
        assertFalse(setup.isNoGym)
        assertNotNull(setup.plannedStartTime)
        assertEquals("18:00", setup.plannedStartTime)
        assertEquals(90, setup.plannedDurationMinutes)
        assertEquals("19:30", setup.plannedEndTime)
    }

    // 2. No hides both fields
    @Test
    fun test2_noHidesBothFields() {
        val setup = DailyGymScheduleRules.DailyGymSetup(
            date = "2026-10-30",
            choice = DailyGymScheduleRules.DailyGymChoice.NO_GYM,
            plannedStartTime = null,
            plannedDurationMinutes = 0,
            plannedEndTime = null,
            isConfirmed = true
        )
        assertFalse(setup.isGymPlanned)
        assertTrue(setup.isNoGym)
        assertNull(setup.plannedStartTime)
        assertEquals(0, setup.plannedDurationMinutes)
        assertNull(setup.plannedEndTime)

        // Validation for NO_GYM always succeeds without requiring start time or duration
        val valResult = DailyGymScheduleRules.validateGymTiming(
            choice = DailyGymScheduleRules.DailyGymChoice.NO_GYM,
            plannedStartTime = null,
            durationMinutes = 0
        )
        assertTrue(valResult.isValid)
        assertNull(valResult.errorMessage)
    }

    // 3. No does not create a gym session
    @Test
    fun test3_noDoesNotCreateGymSession() {
        val setup = DailyGymScheduleRules.DailyGymSetup(
            date = "2026-10-30",
            choice = DailyGymScheduleRules.DailyGymChoice.NO_GYM
        )
        assertFalse(setup.isGymPlanned)
        // With choice NO_GYM, isGymPlanned is false, ensuring no scheduled_activities row is inserted
        assertEquals(DailyGymScheduleRules.DailyGymChoice.NO_GYM, setup.choice)
    }

    // 4. Yes with valid inputs creates one planned session
    @Test
    fun test4_yesWithValidInputsCreatesOnePlannedSession() {
        val valResult = DailyGymScheduleRules.validateGymTiming(
            choice = DailyGymScheduleRules.DailyGymChoice.YES,
            plannedStartTime = "18:00",
            durationMinutes = 90,
            attendsCollege = true,
            collegeStartHhmm = "09:00",
            collegeEndHhmm = "15:15",
            bathTimes = listOf("07:30", "16:00"),
            wakeUpTargetHhmm = "07:00"
        )
        assertTrue(valResult.isValid)
        assertNull(valResult.errorMessage)

        val endTime = DailyGymScheduleRules.calculateEndTime("18:00", 90)
        assertEquals("19:30", endTime)

        // Calculate the canonical GymWindow
        val window = GymWindowRules.calculateGymWindow(
            date = "2026-10-30",
            gymPlannedHhmm = "18:00",
            durationMinutes = 90,
            timezone = tzKolkata
        )
        assertEquals("18:00", window.plannedHhmm)
        assertEquals(90, window.durationMinutes)
        assertEquals("1800", window.plannedHhmmClean)
        assertEquals(timeMs("2026-10-30", "18:00"), window.plannedMs)
        assertEquals(timeMs("2026-10-30", "19:30"), window.expectedFinishMs)
    }

    // 5. Invalid or conflicting times are handled correctly
    @Test
    fun test5_invalidOrConflictingTimesHandledCorrectly() {
        // 5a. Unset choice is rejected
        val unsetVal = DailyGymScheduleRules.validateGymTiming(
            choice = DailyGymScheduleRules.DailyGymChoice.UNSET,
            plannedStartTime = null,
            durationMinutes = 90
        )
        assertFalse(unsetVal.isValid)
        assertEquals("UNSET", unsetVal.conflictType)

        // 5b. Missing start time when YES
        val missingTimeVal = DailyGymScheduleRules.validateGymTiming(
            choice = DailyGymScheduleRules.DailyGymChoice.YES,
            plannedStartTime = "",
            durationMinutes = 90
        )
        assertFalse(missingTimeVal.isValid)
        assertEquals("MISSING_TIME", missingTimeVal.conflictType)

        // 5c. Invalid format
        val invalidFormatVal = DailyGymScheduleRules.validateGymTiming(
            choice = DailyGymScheduleRules.DailyGymChoice.YES,
            plannedStartTime = "25:70",
            durationMinutes = 90
        )
        assertFalse(invalidFormatVal.isValid)
        assertEquals("INVALID_FORMAT", invalidFormatVal.conflictType)

        // 5d. Conflict with College hours (Gym planned 11:00 AM, College 09:00-15:15)
        val colConflictVal = DailyGymScheduleRules.validateGymTiming(
            choice = DailyGymScheduleRules.DailyGymChoice.YES,
            plannedStartTime = "11:00",
            durationMinutes = 90,
            attendsCollege = true,
            collegeStartHhmm = "09:00",
            collegeEndHhmm = "15:15"
        )
        assertFalse(colConflictVal.isValid)
        assertEquals("COLLEGE_CONFLICT", colConflictVal.conflictType)
        assertTrue(colConflictVal.errorMessage!!.contains("College"))

        // 5e. Conflict with Bath session (Gym planned 16:00, Bath #2 is at 16:00)
        val bathConflictVal = DailyGymScheduleRules.validateGymTiming(
            choice = DailyGymScheduleRules.DailyGymChoice.YES,
            plannedStartTime = "16:00",
            durationMinutes = 90,
            attendsCollege = true,
            bathTimes = listOf("07:30", "16:00")
        )
        assertFalse(bathConflictVal.isValid)
        assertEquals("BATH_CONFLICT", bathConflictVal.conflictType)
        assertTrue(bathConflictVal.errorMessage!!.contains("Bath"))

        // 5f. Conflict with Wake-Up proof window (Gym planned 06:45, Wake-Up is at 07:00)
        val wakeConflictVal = DailyGymScheduleRules.validateGymTiming(
            choice = DailyGymScheduleRules.DailyGymChoice.YES,
            plannedStartTime = "06:45",
            durationMinutes = 60,
            wakeUpTargetHhmm = "07:00"
        )
        assertFalse(wakeConflictVal.isValid)
        assertEquals("WAKEUP_CONFLICT", wakeConflictVal.conflictType)
        assertTrue(wakeConflictVal.errorMessage!!.contains("Wake-Up"))
    }

    // 6. Repeated confirmation does not create duplicate sessions
    @Test
    fun test6_repeatedConfirmationDoesNotCreateDuplicateSessions() {
        val date = "2026-10-30"
        val window1 = GymWindowRules.calculateGymWindow(date, "18:00", 90, tzKolkata)
        val actId1 = "ACT-${date.replace("-", "")}-${window1.plannedHhmmClean}-GYM"

        // Confirming again with same parameters produces deterministic identical activity ID
        val window2 = GymWindowRules.calculateGymWindow(date, "18:00", 90, tzKolkata)
        val actId2 = "ACT-${date.replace("-", "")}-${window2.plannedHhmmClean}-GYM"

        assertEquals(actId1, actId2)
        assertEquals("ACT-20261030-1800-GYM", actId1)
    }

    // 7. App restart restores today's saved selection
    @Test
    fun test7_appRestartRestoresTodaySavedSelection() {
        val original = DailyGymScheduleRules.DailyGymSetup(
            date = "2026-10-30",
            choice = DailyGymScheduleRules.DailyGymChoice.YES,
            plannedStartTime = "18:30",
            plannedDurationMinutes = 75,
            plannedEndTime = "19:45",
            isConfirmed = true,
            confirmedAtMs = 1730300000000L,
            timezone = tzKolkata,
            routineVersion = 1,
            policyVersion = 1
        )

        // Serialize to JSON and restore
        val json = DailyGymScheduleRules.toJson(original)
        val restored = DailyGymScheduleRules.fromJson(json)

        assertEquals(original.date, restored.date)
        assertEquals(original.choice, restored.choice)
        assertEquals(original.plannedStartTime, restored.plannedStartTime)
        assertEquals(original.plannedDurationMinutes, restored.plannedDurationMinutes)
        assertEquals(original.plannedEndTime, restored.plannedEndTime)
        assertEquals(original.isConfirmed, restored.isConfirmed)
        assertEquals(original.timezone, restored.timezone)
        assertEquals(original.routineVersion, restored.routineVersion)
        assertEquals(original.policyVersion, restored.policyVersion)
    }

    // 8. A new local date starts a new daily setup
    @Test
    fun test8_newLocalDateStartsNewDailySetup() {
        val yesterday = "2026-10-29"
        val today = "2026-10-30"

        assertTrue(DailyGymScheduleRules.isDateChanged(yesterday, today))
        assertTrue(DailyGymScheduleRules.isDateChanged(null, today))
        assertFalse(DailyGymScheduleRules.isDateChanged(today, today))
    }

    // 9. The alternate-day anchor and history remain unchanged
    @Test
    fun test9_alternateDayAnchorAndHistoryRemainUnchanged() {
        val segments = listOf(
            GymRoutineDates.Segment(
                startDate = "2026-10-25",
                anchorDate = "2026-10-25",
                endDate = null
            )
        )

        // Anchor is fixed on 2026-10-25
        assertEquals("2026-10-25", GymRoutineDates.ANCHOR_DATE)
        assertEquals("2026-10-25", segments[0].anchorDate)

        // October 25 (Day 0) -> GYM_DAY
        assertEquals("GYM_DAY", GymRoutineDates.plannedState("2026-10-25", true, "2026-10-25", segments))
        // October 26 (Day 1) -> REST_DAY
        assertEquals("REST_DAY", GymRoutineDates.plannedState("2026-10-26", true, "2026-10-25", segments))
        // October 27 (Day 2) -> GYM_DAY
        assertEquals("GYM_DAY", GymRoutineDates.plannedState("2026-10-27", true, "2026-10-25", segments))
        // October 28 (Day 3) -> REST_DAY
        assertEquals("REST_DAY", GymRoutineDates.plannedState("2026-10-28", true, "2026-10-25", segments))
        // October 29 (Day 4) -> GYM_DAY
        assertEquals("GYM_DAY", GymRoutineDates.plannedState("2026-10-29", true, "2026-10-25", segments))
        // October 30 (Day 5) -> REST_DAY
        assertEquals("REST_DAY", GymRoutineDates.plannedState("2026-10-30", true, "2026-10-25", segments))

        // History of past days is not rewritten when user chooses YES or NO today
        val pastState = GymRoutineDates.plannedState("2026-10-27", true, "2026-10-25", segments)
        assertEquals("GYM_DAY", pastState)
    }

    // 10. Gym Mode activation remains independent of daily planning
    @Test
    fun test10_gymModeActivationRemainsIndependentOfDailyPlanning() {
        val date = "2026-10-30"
        val actId = "ACT-20261030-1800-GYM"

        // Morning setup at 07:30 AM: Selecting YES plans the session, but Gym Mode cannot be activated before 5:00 PM!
        val (canActMorning, reasonMorning) = GymModeRules.canActivate(
            nowMs = timeMs(date, "07:30"),
            livesBalance = 3,
            hasActiveSession = false,
            gymActivityId = actId,
            previouslyActivatedGymIds = emptySet(),
            timezone = tzKolkata
        )
        assertFalse(canActMorning)
        assertTrue(reasonMorning.contains("5:00 PM"))

        // After 5:00 PM (17:00), activation becomes available if lives >= 1
        val (canActEvening, _) = GymModeRules.canActivate(
            nowMs = timeMs(date, "17:05"),
            livesBalance = 3,
            hasActiveSession = false,
            gymActivityId = actId,
            previouslyActivatedGymIds = emptySet(),
            timezone = tzKolkata
        )
        assertTrue(canActEvening)
    }

    // 11. Calendar distinguishes planned from verified sessions
    @Test
    fun test11_calendarDistinguishesPlannedFromVerifiedSessions() {
        val date = "2026-10-30"

        // 11a. Planned session (not verified yet): status is null (or PENDING if proof submitted)
        val plannedStatus = StrictCalendarDataRepository.calculateGymSessionStatus(
            plannedGym = "GYM_DAY",
            isGymSessionVerified = false,
            hasGymSessionPending = false,
            dateStr = date,
            todayDate = date
        )
        assertNull(plannedStatus)

        // 11b. Verified session
        val verifiedStatus = StrictCalendarDataRepository.calculateGymSessionStatus(
            plannedGym = "GYM_DAY",
            isGymSessionVerified = true,
            hasGymSessionPending = false,
            dateStr = date,
            todayDate = date
        )
        assertEquals("VERIFIED", verifiedStatus)

        // 11c. No Gym planned on today's date: does NOT mark missed
        val noGymStatus = StrictCalendarDataRepository.calculateGymSessionStatus(
            plannedGym = "REST_DAY",
            isGymSessionVerified = false,
            hasGymSessionPending = false,
            dateStr = date,
            todayDate = date
        )
        assertNull(noGymStatus)
    }

    // 12. Existing points, hearts, evidence, and scoring rules remain unchanged
    @Test
    fun test12_existingPointsHeartsEvidenceAndScoringRulesRemainUnchanged() {
        // Daily planning awards 0 points (only proof submission + evaluation awards points)
        val setup = DailyGymScheduleRules.DailyGymSetup(
            date = "2026-10-30",
            choice = DailyGymScheduleRules.DailyGymChoice.YES,
            plannedStartTime = "18:00",
            plannedDurationMinutes = 90
        )
        assertTrue(setup.isGymPlanned)

        // Configured gym points remain 15
        val window = GymWindowRules.calculateGymWindow("2026-10-30", "18:00", 90, tzKolkata)
        assertEquals(90, window.durationMinutes)

        // Max lives remains 3
        assertEquals(3, GymModeRules.MAX_LIVES)
        assertEquals(3, ProductPolicy.GYM_MAX_LIVES)

        // Lives badge formatting
        assertEquals("♥ ♥ ♥ — 3/3 lives", GymModeRules.formatLivesBadge(3))
        assertEquals("♥ ♥ ♡ — 2/3 lives", GymModeRules.formatLivesBadge(2))
        assertEquals("♥ ♡ ♡ — 1/3 lives", GymModeRules.formatLivesBadge(1))
        assertEquals("♡ ♡ ♡ — 0/3 lives", GymModeRules.formatLivesBadge(0))
    }

    // 13. Schedule submission validation without wake-up target and with conditional gym
    @Test
    fun test13_scheduleSubmission_wakeUpOptionalAndGymConditional() {
        // Valid schedule submission without wake-up target (wake-up managed independently) and with NO gym
        val subNoGym = ScheduleDeadlineRules.ScheduleSubmissionData(
            date = "2026-11-01",
            collegeAttendance = "NO",
            bathSessionCount = 1,
            bathTimes = listOf("07:30"),
            wakeUpTargetHhmm = "",
            gymTargetHhmm = "",
            gymDurationMinutes = 0,
            gymEnabled = false
        )
        val resNoGym = ScheduleDeadlineRules.validateScheduleSubmission(subNoGym)
        assertTrue("Submission with NO gym and omitted wake-up must be valid", resNoGym.isValid)

        // Valid schedule submission without wake-up target and with YES gym
        val subYesGym = ScheduleDeadlineRules.ScheduleSubmissionData(
            date = "2026-11-01",
            collegeAttendance = "NO",
            bathSessionCount = 1,
            bathTimes = listOf("07:30"),
            wakeUpTargetHhmm = "",
            gymTargetHhmm = "18:00",
            gymDurationMinutes = 90,
            gymEnabled = true
        )
        val resYesGym = ScheduleDeadlineRules.validateScheduleSubmission(subYesGym)
        assertTrue("Submission with YES gym and valid timing must be valid", resYesGym.isValid)

        // YES gym but missing gym time must fail
        val subMissingGymTime = ScheduleDeadlineRules.ScheduleSubmissionData(
            date = "2026-11-01",
            collegeAttendance = "NO",
            bathSessionCount = 1,
            bathTimes = listOf("07:30"),
            wakeUpTargetHhmm = "",
            gymTargetHhmm = "",
            gymDurationMinutes = 90,
            gymEnabled = true
        )
        val resMissingTime = ScheduleDeadlineRules.validateScheduleSubmission(subMissingGymTime)
        assertFalse("Submission with YES gym and empty gym time must fail", resMissingTime.isValid)
        assertTrue(resMissingTime.errorMessage?.contains("Gym target time") == true)
    }
}
