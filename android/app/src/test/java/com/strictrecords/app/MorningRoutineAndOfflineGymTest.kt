package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.text.SimpleDateFormat
import java.time.ZoneId
import java.util.Locale
import java.util.TimeZone

/**
 * Unit tests covering:
 * Part A: Morning Wake-Up window, once-per-day popup & Routine Setup (1 or 2 baths, college & non-college)
 * Part B: Bath session timing (Fixed college 7:30-8:00 activity, 8:00-8:30 proof window, 9:00-10:00 first hourly) and late wake-up Cases A, B, C, D, E
 * Part C: Offline Gym Mode activation after 5:00 PM, single-use obligation, and lives ledger safety
 * Part D: Calendar Mon-Sun alignment, planned vs verified Gym separation, rest/paused states
 */
class MorningRoutineAndOfflineGymTest {

    private val tzKolkata = "Asia/Kolkata"
    private val zoneKolkata = TimeZone.getTimeZone(tzKolkata)

    private fun timeMs(dateStr: String, hhmm: String): Long {
        val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            timeZone = zoneKolkata
            isLenient = false
        }
        return sdf.parse("$dateStr $hhmm")!!.time
    }

    // ── PART A: MORNING WAKE-UP WINDOW & ONCE-PER-DAY ROUTINE FLOW ──

    @Test
    fun testMorningWindow_boundaries() {
        val date = "2026-10-28"
        val zone = ZoneId.of(tzKolkata)

        assertFalse(MorningWakeUpManager.isMorningWindow(timeMs(date, "05:59"), zone))
        assertTrue(MorningWakeUpManager.isMorningWindow(timeMs(date, "06:00"), zone))
        assertTrue(MorningWakeUpManager.isMorningWindow(timeMs(date, "07:30"), zone))
        assertTrue(MorningWakeUpManager.isMorningWindow(timeMs(date, "10:00"), zone))
        assertFalse(MorningWakeUpManager.isMorningWindow(timeMs(date, "10:01"), zone))
    }

    @Test
    fun testMorningPopupRules_oncePerDay_queuesBehindWakeUp_andRespectsConfirmation() {
        val date = "2026-10-28"
        val morningMs = timeMs(date, "07:10")

        // While Wake-Up Verification dialog is active, Routine Setup queues instead of overlapping
        val queued = BathScheduleMorningPopupRules.shouldShowFirstMorningPopup(
            date = date,
            isConfirmed = false,
            popupShownDate = null,
            isCriticalDialogActive = true,
            clockMs = morningMs,
            timezone = tzKolkata
        )
        assertFalse(queued.shouldShow)
        assertTrue(queued.shouldQueue)
        assertEquals("QUEUED_CRITICAL_DIALOG_ACTIVE", queued.reason)

        // After Wake-Up finishes (no critical dialog active) and routine unconfirmed -> opens
        val firstOpen = BathScheduleMorningPopupRules.shouldShowFirstMorningPopup(
            date = date,
            isConfirmed = false,
            popupShownDate = null,
            isCriticalDialogActive = false,
            clockMs = morningMs,
            timezone = tzKolkata
        )
        assertTrue(firstOpen.shouldShow)
        assertEquals("MORNING_FIRST_OPEN", firstOpen.reason)

        // Subsequent unlocks / resumes in the same foreground session do not duplicate
        val duplicateSameDay = BathScheduleMorningPopupRules.shouldShowFirstMorningPopup(
            date = date,
            isConfirmed = false,
            popupShownDate = date,
            isCriticalDialogActive = false,
            clockMs = timeMs(date, "07:25"),
            timezone = tzKolkata
        )
        assertFalse(duplicateSameDay.shouldShow)
        assertEquals("ALREADY_SHOWN_TODAY", duplicateSameDay.reason)

        // App restart after routine confirmation does not reopen automatically
        val afterRestartConfirmed = BathScheduleMorningPopupRules.shouldShowFirstMorningPopup(
            date = date,
            isConfirmed = true,
            popupShownDate = null,
            isCriticalDialogActive = false,
            clockMs = timeMs(date, "08:05"),
            timezone = tzKolkata
        )
        assertFalse(afterRestartConfirmed.shouldShow)
        assertEquals("ALREADY_CONFIRMED", afterRestartConfirmed.reason)
    }

    @Test
    fun testConfirmationReview_oneBathCreatesNoSecondBath_twoBathsRemainDistinct() {
        val date = "2026-10-28"

        // 1-bath college day -> exactly 1 bath entry, no nonexistent 2nd session
        val reviewCollege1 = BathScheduleMorningPopupRules.buildConfirmationReview(
            date = date,
            collegeDecision = "YES",
            selectedTimes = listOf("07:30"),
            timezone = tzKolkata,
            sessionCount = 1
        )
        assertEquals("YES", reviewCollege1.getString("college_decision"))
        assertEquals(1, reviewCollege1.getJSONArray("baths").length())

        // 2-bath college day -> 2 distinct bath entries
        val reviewCollege2 = BathScheduleMorningPopupRules.buildConfirmationReview(
            date = date,
            collegeDecision = "YES",
            selectedTimes = listOf("07:30", "16:30"),
            timezone = tzKolkata,
            sessionCount = 2
        )
        val collegeBaths = reviewCollege2.getJSONArray("baths")
        assertEquals(2, collegeBaths.length())
        assertEquals(1, collegeBaths.getJSONObject(0).getInt("occurrence"))
        assertEquals(2, collegeBaths.getJSONObject(1).getInt("occurrence"))
        assertEquals("07:30", collegeBaths.getJSONObject(0).getString("planned_hhmm"))
        assertEquals("16:30", collegeBaths.getJSONObject(1).getString("planned_hhmm"))

        // 2-bath non-college day -> 2 distinct bath entries
        val reviewHome2 = BathScheduleMorningPopupRules.buildConfirmationReview(
            date = date,
            collegeDecision = "NO",
            selectedTimes = listOf("13:00", "18:00"),
            timezone = tzKolkata,
            sessionCount = 2
        )
        val homeBaths = reviewHome2.getJSONArray("baths")
        assertEquals("NO", reviewHome2.getString("college_decision"))
        assertEquals(2, homeBaths.length())
        assertEquals("13:00", homeBaths.getJSONObject(0).getString("planned_hhmm"))
        assertEquals("18:00", homeBaths.getJSONObject(1).getString("planned_hhmm"))
    }

    // ── PART B: BATH TIMING & LATE WAKE-UP HANDLING ─────────────────

    @Test
    fun testCollegeBath1_fixedActivityAndProofWindows_andFirstHourlyAt9AM() {
        val date = "2026-10-28"
        val b1 = BathWindowRules.calculateBathWindow(
            occurrence = 1,
            isCollege = true,
            date = date,
            plannedHhmm = "07:30",
            timezone = tzKolkata
        )

        // Activity window: 7:30 AM to 8:00 AM (30 min duration), distinct from proof window
        assertEquals("7:30–8:00 AM", b1.activityIntervalDisplay)
        assertEquals("07:30 AM–08:00 AM", b1.reservedDisplay)
        assertEquals(timeMs(date, "07:30"), b1.reservedStartMs)
        assertEquals(timeMs(date, "08:00"), b1.reservedEndMs)

        // Proof window: 8:00 AM to 8:30 AM (expires at 8:30 AM)
        assertEquals("8:00–8:30 AM", b1.proofWindowDisplay)
        assertEquals(timeMs(date, "08:00"), b1.proofWindowStartMs)
        assertEquals(timeMs(date, "08:30"), b1.proofWindowEndMs)
        assertEquals("8:30 AM", b1.proofDeadlineDisplay)
        assertNotEquals(b1.reservedStartMs, b1.proofWindowStartMs)

        // College day hourly schedule starts at 9:00-10:00 AM (window 09:00-10:00)
        val collegeSlots = HourlyWindowRules.collegeDaySlots(date, tzKolkata)
        assertTrue(collegeSlots.isNotEmpty())
        assertEquals("09:00-10:00", collegeSlots.first().hourSlot)
        assertEquals(timeMs(date, "09:00"), collegeSlots.first().startMs)
        assertEquals(timeMs(date, "10:00"), collegeSlots.first().endMs)
    }

    @Test
    fun testLateWakeUp_CaseA_wakingBefore0800() {
        val date = "2026-10-28"
        val b1 = BathWindowRules.calculateBathWindow(1, true, date, "07:30", timezone = tzKolkata)
        val b2 = BathWindowRules.calculateBathWindow(2, true, date, "16:00", timezone = tzKolkata)

        val wakeUpMs = timeMs(date, "07:15")
        val eval = BathWindowRules.evaluateLateWakeUp(wakeUpMs, true, b1, b2)

        assertEquals(BathWindowRules.BathLateWakeUpState.BEFORE_ACTIVITY, eval.state)
        assertEquals("07:30 AM–08:00 AM", eval.scheduledActivityWindow)
        assertEquals("08:00 AM–08:30 AM", eval.proofWindow)
        assertTrue(eval.bath2Feasible)
    }

    @Test
    fun testLateWakeUp_CaseB_wakingBetween0800And0830() {
        val date = "2026-10-28"
        val b1 = BathWindowRules.calculateBathWindow(1, true, date, "07:30", timezone = tzKolkata)
        val b2 = BathWindowRules.calculateBathWindow(2, true, date, "16:00", timezone = tzKolkata)

        val wakeUpMs = timeMs(date, "08:12")
        val eval = BathWindowRules.evaluateLateWakeUp(wakeUpMs, true, b1, b2)

        assertEquals(BathWindowRules.BathLateWakeUpState.PROOF_WINDOW_OPEN, eval.state)
        assertTrue(eval.isProofOpen)
        assertFalse(eval.isMissed)
        assertTrue(eval.proofWindowRemainingMinutes in 17..18)
        assertTrue(eval.message.contains("Photo proof window is currently OPEN"))
        assertTrue(eval.bath2Feasible)
    }

    @Test
    fun testLateWakeUp_CaseC_wakingAtOrAfter0830_doesNotShiftSchedule() {
        val date = "2026-10-28"
        val b1 = BathWindowRules.calculateBathWindow(1, true, date, "07:30", timezone = tzKolkata)
        val b2 = BathWindowRules.calculateBathWindow(2, true, date, "16:00", timezone = tzKolkata)

        val wakeUpMs = timeMs(date, "08:45")
        val eval = BathWindowRules.evaluateLateWakeUp(wakeUpMs, true, b1, b2)

        assertEquals(BathWindowRules.BathLateWakeUpState.EXPIRED_MISSED, eval.state)
        assertTrue(eval.isMissed)
        assertFalse(eval.isProofOpen)
        assertEquals("07:30 AM–08:00 AM", eval.scheduledActivityWindow)
        assertEquals("08:00 AM–08:30 AM", eval.proofWindow)
        assertTrue(eval.message.contains("expired at 08:30 AM"))
        assertTrue(eval.bath2Feasible)
    }

    @Test
    fun testLateWakeUp_CaseD_nonCollegeDay_and_CaseE_twoBathFeasibility() {
        val date = "2026-10-28"
        val b1 = BathWindowRules.calculateBathWindow(1, false, date, "13:00", timezone = tzKolkata)
        val b2 = BathWindowRules.calculateBathWindow(2, false, date, "18:00", timezone = tzKolkata)

        // Non-college Bath 1 planned 13:00 -> activity 13:00-14:00, proof 14:30-14:50
        assertEquals(timeMs(date, "13:00"), b1.reservedStartMs)
        assertEquals(timeMs(date, "14:00"), b1.reservedEndMs)
        assertEquals(timeMs(date, "14:30"), b1.proofWindowStartMs)
        assertEquals(timeMs(date, "14:50"), b1.proofWindowEndMs)

        // During activity interval
        val evalInAct = BathWindowRules.evaluateLateWakeUp(timeMs(date, "13:30"), false, b1, b2)
        assertEquals(BathWindowRules.BathLateWakeUpState.IN_ACTIVITY, evalInAct.state)
        assertTrue(evalInAct.bath2Feasible)

        // Very late wake-up after Bath 2 window ends (21:15) -> Bath 2 infeasible
        val evalAfterB2 = BathWindowRules.evaluateLateWakeUp(timeMs(date, "21:15"), false, b1, b2)
        assertEquals(BathWindowRules.BathLateWakeUpState.EXPIRED_MISSED, evalAfterB2.state)
        assertFalse(evalAfterB2.bath2Feasible)
    }

    @Test
    fun testBathSchedulePlanManager_supportsOneOrTwoBaths_andRejectsOverlaps() {
        val date = "2026-10-28"

        assertTrue(BathSchedulePlanManager.validateSelection(date, "YES", listOf("07:30"), tzKolkata, 1).isValid)
        assertTrue(BathSchedulePlanManager.validateSelection(date, "YES", listOf("07:30", "16:00"), tzKolkata, 2).isValid)
        assertTrue(BathSchedulePlanManager.validateSelection(date, "NO", listOf("13:00"), tzKolkata, 1).isValid)
        assertTrue(BathSchedulePlanManager.validateSelection(date, "NO", listOf("13:00", "18:00"), tzKolkata, 2).isValid)

        // Colliding bath times (< 60 min apart) rejected
        assertFalse(BathSchedulePlanManager.validateSelection(date, "NO", listOf("13:00", "13:30"), tzKolkata, 2).isValid)
    }

    // ── PART C: OFFLINE GYM MODE ACTIVATION (AFTER 5:00 PM) ─────────

    @Test
    fun testGymMode_activationWindow_opensAt5PM_andBlocksDuplicateActivation() {
        val date = "2026-10-28"
        val actId = "ACT-20261028-1830-GYM"

        // 4:59 PM (16:59) -> rejected
        val (canAct459, reason459) = GymModeRules.canActivate(
            nowMs = timeMs(date, "16:59"),
            livesBalance = 3,
            hasActiveSession = false,
            gymActivityId = actId,
            previouslyActivatedGymIds = emptySet(),
            timezone = tzKolkata
        )
        assertFalse(canAct459)
        assertTrue(reason459.contains("5:00 PM"))

        // 5:00 PM (17:00) with >= 1 life -> eligible offline
        val (canAct500, _) = GymModeRules.canActivate(
            nowMs = timeMs(date, "17:00"),
            livesBalance = 1,
            hasActiveSession = false,
            gymActivityId = actId,
            previouslyActivatedGymIds = emptySet(),
            timezone = tzKolkata
        )
        assertTrue(canAct500)

        // Duplicate tap while session is active -> rejected without consuming another life
        val (canActDupActive, _) = GymModeRules.canActivate(
            nowMs = timeMs(date, "17:01"),
            livesBalance = 1,
            hasActiveSession = true,
            gymActivityId = actId,
            previouslyActivatedGymIds = setOf(actId),
            timezone = tzKolkata
        )
        assertFalse(canActDupActive)

        // Re-activating same single-use gym activity after early deactivation -> rejected
        val (canActReuse, _) = GymModeRules.canActivate(
            nowMs = timeMs(date, "17:30"),
            livesBalance = 1,
            hasActiveSession = false,
            gymActivityId = actId,
            previouslyActivatedGymIds = setOf(actId),
            timezone = tzKolkata
        )
        assertFalse(canActReuse)

        // 8:30 PM (20:30) -> hard cutoff
        val (canAct830, reason830) = GymModeRules.canActivate(
            nowMs = timeMs(date, "20:30"),
            livesBalance = 3,
            hasActiveSession = false,
            gymActivityId = "ACT-20261028-1900-GYM",
            previouslyActivatedGymIds = emptySet(),
            timezone = tzKolkata
        )
        assertFalse(canAct830)
        assertTrue(reason830.contains("closed for today"))
    }

    @Test
    fun testGymMode_rejectedWhenNoLivesRemaining() {
        val date = "2026-10-28"
        val (canAct, reason) = GymModeRules.canActivate(
            nowMs = timeMs(date, "17:30"),
            livesBalance = 0,
            hasActiveSession = false,
            gymActivityId = "ACT-$date-GYM-1",
            previouslyActivatedGymIds = emptySet(),
            timezone = tzKolkata
        )
        assertFalse(canAct)
        assertTrue(reason.contains("No lives remaining"))
    }

    @Test
    fun testGymMode_accessExpiryAlwaysHardCutoff2030() {
        val date = "2026-10-28"
        assertEquals(timeMs(date, "20:30"), GymModeRules.calculateAccessExpiry(timeMs(date, "17:00"), tzKolkata))
        assertEquals(timeMs(date, "20:30"), GymModeRules.calculateAccessExpiry(timeMs(date, "18:45"), tzKolkata))
    }

    // ── PART D: CALENDAR ALIGNMENT & PLANNED VS VERIFIED GYM STATES ─

    @Test
    fun testCalendar_monSunAlignment_andRealDateWeekdays() {
        // 2026-10-25 is Sunday (weekday=0 -> Mon-0 index 6)
        val sunWeekday = StrictCalendarDataRepository.weekday("2026-10-25")
        assertEquals(0, sunWeekday)
        assertEquals(6, (sunWeekday + 6) % 7)

        // 2026-10-26 is Monday (weekday=1 -> Mon-0 index 0)
        val monWeekday = StrictCalendarDataRepository.weekday("2026-10-26")
        assertEquals(1, monWeekday)
        assertEquals(0, (monWeekday + 6) % 7)

        // 2026-10-29 is Thursday (weekday=4 -> Mon-0 index 3)
        val thuWeekday = StrictCalendarDataRepository.weekday("2026-10-29")
        assertEquals(4, thuWeekday)
        assertEquals(3, (thuWeekday + 6) % 7)
    }

    @Test
    fun testCalendar_plannedGymVsRestVsPaused_andVerifiedSeparation() {
        val segActive = listOf(
            GymRoutineDates.Segment(startDate = "2026-10-25", anchorDate = "2026-10-25", endDate = "2026-10-28")
        )
        // Anchor day 2026-10-25 -> GYM_DAY
        assertEquals("GYM_DAY", StrictCalendarDataRepository.calculatePlannedGymState("2026-10-25", true, "2026-10-25", segActive))
        // Next day 2026-10-26 -> REST_DAY
        assertEquals("REST_DAY", StrictCalendarDataRepository.calculatePlannedGymState("2026-10-26", true, "2026-10-25", segActive))
        // Day after 2026-10-27 -> GYM_DAY
        assertEquals("GYM_DAY", StrictCalendarDataRepository.calculatePlannedGymState("2026-10-27", true, "2026-10-25", segActive))
        // After segment endDate 2026-10-28 -> PAUSED
        assertEquals("PAUSED", StrictCalendarDataRepository.calculatePlannedGymState("2026-10-28", true, "2026-10-25", segActive))

        // Planned gym day today without verification is PENDING or null, NEVER VERIFIED
        assertEquals(
            "PENDING",
            StrictCalendarDataRepository.calculateGymSessionStatus("GYM_DAY", isGymSessionVerified = false, hasGymSessionPending = true, dateStr = "2026-10-29", todayDate = "2026-10-29")
        )
        assertNull(
            StrictCalendarDataRepository.calculateGymSessionStatus("REST_DAY", isGymSessionVerified = false, hasGymSessionPending = false, dateStr = "2026-10-29", todayDate = "2026-10-29")
        )
        // Past unverified planned gym day -> MISSED
        assertEquals(
            "MISSED",
            StrictCalendarDataRepository.calculateGymSessionStatus("GYM_DAY", isGymSessionVerified = false, hasGymSessionPending = false, dateStr = "2026-10-27", todayDate = "2026-10-29")
        )
        // Verified session -> VERIFIED
        assertEquals(
            "VERIFIED",
            StrictCalendarDataRepository.calculateGymSessionStatus("GYM_DAY", isGymSessionVerified = true, hasGymSessionPending = false, dateStr = "2026-10-29", todayDate = "2026-10-29")
        )
    }
}
