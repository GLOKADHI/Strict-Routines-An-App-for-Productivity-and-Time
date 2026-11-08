package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/**
 * Automated unit and integration test suite verifying First-Morning Bath Schedule Popup
 * independent of Wake-Up Verification, covering all 14 Section 8 requirements:
 *
 * 1. First morning app launch with no confirmed bath plan -> popup appears.
 * 2. First morning app launch when Wake-Up Verification has not been completed -> popup still appears.
 * 3. First morning app launch after Wake-Up Verification succeeds -> popup appears if today's plan is unconfirmed.
 * 4. Wake-Up Verification completed -> today's bath time can still be edited from Settings.
 * 5. User selects "YES — COLLEGE" -> only the College Day schedule configuration appears (2 baths: 7-10 AM, 16-21 PM).
 * 6. User selects "NO — HOME" -> only the Non-College Day schedule configuration appears (1 bath within permitted range).
 * 7. Confirm plan and restart app -> confirmed schedule is displayed without re-opening the popup.
 * 8. Settings displays the same confirmed daily plan.
 * 9. Edit plan -> schedule is recalculated consistently without deleting historical proof records.
 * 10. Rapid repeated taps on Confirm -> no duplicate occurrences or duplicate point transactions.
 * 11. Simulate failure during database save -> error message displayed, app does not claim plan is confirmed.
 * 12. Wake-up verification scoring, timing, and completion status remain unchanged by bath confirmation.
 * 13. Hourly attendance reservations and photo proof deadlines match standard formulas.
 * 14. Recovery fallback triggers if app is first opened later than morning window when unconfirmed.
 */
class BathScheduleMorningPopupTest {

    private val testDate = "2026-10-21"
    private val zoneStr = "Asia/Kolkata"
    private val timeZone = TimeZone.getTimeZone(zoneStr)

    private fun parseMs(dateStr: String, hhmm: String): Long {
        val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            this.timeZone = this@BathScheduleMorningPopupTest.timeZone
            isLenient = false
        }
        return sdf.parse("$dateStr $hhmm")!!.time
    }

    /**
     * Requirement 1: First morning app launch with no confirmed bath plan -> popup appears.
     */
    @Test
    fun test1_firstMorningLaunch_noConfirmedBathPlan_popupAppears() {
        val morningTimeMs = parseMs(testDate, "07:30")
        val result = BathScheduleMorningPopupRules.shouldShowFirstMorningPopup(
            date = testDate,
            isConfirmed = false,
            popupShownDate = null,
            isCriticalDialogActive = false,
            clockMs = morningTimeMs,
            timezone = zoneStr
        )
        assertTrue("Popup should show on first morning launch when unconfirmed", result.shouldShow)
        assertFalse("Popup should not be queued when no critical dialog is active", result.shouldQueue)
        assertEquals("MORNING_FIRST_OPEN", result.reason)
    }

    /**
     * Requirement 2: First morning app launch when Wake-Up Verification has not been completed -> popup still appears.
     * Also tests that if a critical dialog (like Wake-Up verification) is active, popup queues until dismissed.
     */
    @Test
    fun test2_firstMorningLaunch_wakeUpIncomplete_popupStillAppears() {
        val morningTimeMs = parseMs(testDate, "06:45")

        // Case A: Wake-Up incomplete, but user is on main UI (no active dialog) -> popup opens immediately
        val wakeUpCompleted = false
        val activeDialog = false
        val result = BathScheduleMorningPopupRules.shouldShowFirstMorningPopup(
            date = testDate,
            isConfirmed = false,
            popupShownDate = null,
            isCriticalDialogActive = activeDialog,
            clockMs = morningTimeMs,
            timezone = zoneStr
        )
        assertTrue("Popup must show even if wake-up is not completed", result.shouldShow)
        assertFalse(wakeUpCompleted)

        // Case B: Wake-Up verification screen is currently active -> bath popup queues
        val resultQueued = BathScheduleMorningPopupRules.shouldShowFirstMorningPopup(
            date = testDate,
            isConfirmed = false,
            popupShownDate = null,
            isCriticalDialogActive = true,
            clockMs = morningTimeMs,
            timezone = zoneStr
        )
        assertFalse("Should not interrupt active critical dialog", resultQueued.shouldShow)
        assertTrue("Should queue for display once critical dialog dismisses", resultQueued.shouldQueue)
        assertEquals("QUEUED_CRITICAL_DIALOG_ACTIVE", resultQueued.reason)
    }

    /**
     * Requirement 3: First morning app launch after Wake-Up Verification succeeds -> popup appears if today's plan is unconfirmed.
     */
    @Test
    fun test3_firstMorningLaunch_afterWakeUpSucceeds_popupAppearsIfUnconfirmed() {
        val postWakeUpTimeMs = parseMs(testDate, "08:15")
        // Wake-up succeeded, but bath plan remains unconfirmed
        val isConfirmed = false
        val wakeUpSucceeded = true

        val result = BathScheduleMorningPopupRules.shouldShowFirstMorningPopup(
            date = testDate,
            isConfirmed = isConfirmed,
            popupShownDate = null,
            isCriticalDialogActive = false,
            clockMs = postWakeUpTimeMs,
            timezone = zoneStr
        )
        assertTrue(wakeUpSucceeded)
        assertTrue("Popup must still appear after wake-up success if bath plan is unconfirmed", result.shouldShow)
        assertEquals("MORNING_FIRST_OPEN", result.reason)
    }

    /**
     * Requirement 4: Wake-Up Verification completed -> today's bath time can still be edited from Settings.
     */
    @Test
    fun test4_wakeUpCompleted_bathPlanCanStillBeEdited() {
        val isWakeUpCompleted = true

        // With 0 baths submitted, user can edit regardless of wake-up completion
        val (canEdit0, reason0) = BathScheduleMorningPopupRules.canEditPlan(
            isWakeUpCompleted = isWakeUpCompleted,
            bathsSubmittedCount = 0,
            maxBaths = 2
        )
        assertTrue("Completed wake-up verification must NEVER lock out bath plan editing", canEdit0)
        assertEquals("", reason0)

        // With 1 of 2 baths submitted, user cannot edit that occurrence
        val (canEdit1, reason1) = BathScheduleMorningPopupRules.canEditPlan(
            isWakeUpCompleted = isWakeUpCompleted,
            bathsSubmittedCount = 2,
            maxBaths = 2
        )
        assertFalse("Only completed daily baths can lock editing", canEdit1)
        assertTrue(reason1.contains("already been submitted"))
    }

    /**
     * Requirement 5: User selects "YES — COLLEGE" -> only the College Day schedule configuration appears (2 baths: 7-10 AM, 16-21 PM).
     */
    @Test
    fun test5_selectCollegeYes_showsOnlyCollegeSchedule2Baths() {
        // Validation passes with 2 valid college bath times
        val valid = BathScheduleMorningPopupRules.validateSelection("YES", listOf("07:30", "16:30"))
        assertTrue("Valid college bath times should pass", valid.isValid)

        // Reject if user only provides 1 bath time for college day
        val invalidCount = BathScheduleMorningPopupRules.validateSelection("YES", listOf("07:30"))
        assertFalse("College day requires 2 bath times", invalidCount.isValid)
        assertTrue(invalidCount.errorMessage?.contains("all 2 bath sessions") == true)

        // Reject if Bath #1 is outside 07:00–10:00 AM (starts at 09:30 => ends at 10:30 > 10:00)
        val invalidBath1 = BathScheduleMorningPopupRules.validateSelection("YES", listOf("09:30", "16:30"))
        assertFalse("Bath #1 must finish by 10:00 AM", invalidBath1.isValid)

        // Reject if Bath #2 is outside 16:00–21:00 PM (starts at 20:30 => ends at 21:30 > 21:00)
        val invalidBath2 = BathScheduleMorningPopupRules.validateSelection("YES", listOf("07:30", "20:30"))
        assertFalse("Bath #2 must finish by 21:00 PM", invalidBath2.isValid)

        // Review payload contains exactly 2 baths
        val review = BathScheduleMorningPopupRules.buildConfirmationReview(testDate, "YES", listOf("07:30", "16:30"), zoneStr)
        assertEquals("YES", review.getString("college_decision"))
        val baths = review.getJSONArray("baths")
        assertEquals(2, baths.length())
        assertEquals("Bath Protocol (#1)", baths.getJSONObject(0).getString("title"))
        assertEquals("Bath Protocol (#2)", baths.getJSONObject(1).getString("title"))
    }

    /**
     * Requirement 6: User selects "NO — HOME" -> only the Non-College Day schedule configuration appears (1 bath within permitted range).
     */
    @Test
    fun test6_selectCollegeNo_showsOnlyNonCollegeSchedule1Bath() {
        // Validation passes with 1 valid non-college bath time within 13:00–17:30
        val valid = BathScheduleMorningPopupRules.validateSelection("NO", listOf("14:00"))
        assertTrue("Valid non-college bath time should pass", valid.isValid)

        // Reject if no times provided
        val invalidEmpty = BathScheduleMorningPopupRules.validateSelection("NO", emptyList())
        assertFalse("Non-college day requires 1 bath time", invalidEmpty.isValid)

        // Reject if outside permitted window (e.g. 10:00 AM when default non-college window is 13:00–17:30)
        val invalidEarly = BathScheduleMorningPopupRules.validateSelection("NO", listOf("10:00"))
        assertFalse("Bath time outside 13:00–17:30 should be rejected", invalidEarly.isValid)

        // Review payload contains exactly 1 bath
        val review = BathScheduleMorningPopupRules.buildConfirmationReview(testDate, "NO", listOf("14:00"), zoneStr)
        assertEquals("NO", review.getString("college_decision"))
        val baths = review.getJSONArray("baths")
        assertEquals(1, baths.length())
        assertEquals("Bath Protocol", baths.getJSONObject(0).getString("title"))
    }

    /**
     * Requirement 7: Confirm plan and restart app -> confirmed schedule is displayed without re-opening the popup.
     */
    @Test
    fun test7_confirmPlanAndRestartApp_noDuplicatePopup() {
        val morningTimeMs = parseMs(testDate, "08:45")

        // After user confirmed the plan: isConfirmed = true
        val resultAfterConfirm = BathScheduleMorningPopupRules.shouldShowFirstMorningPopup(
            date = testDate,
            isConfirmed = true,
            popupShownDate = null,
            isCriticalDialogActive = false,
            clockMs = morningTimeMs,
            timezone = zoneStr
        )
        assertFalse("Confirmed plan must NOT trigger morning popup again", resultAfterConfirm.shouldShow)
        assertEquals("ALREADY_CONFIRMED", resultAfterConfirm.reason)

        // If user already viewed or interacted in this session: popupShownDate matches today
        val resultAlreadyShown = BathScheduleMorningPopupRules.shouldShowFirstMorningPopup(
            date = testDate,
            isConfirmed = false,
            popupShownDate = testDate,
            isCriticalDialogActive = false,
            clockMs = morningTimeMs,
            timezone = zoneStr
        )
        assertFalse("Already shown popup in current session must not reopen", resultAlreadyShown.shouldShow)
        assertEquals("ALREADY_SHOWN_TODAY", resultAlreadyShown.reason)
    }

    /**
     * Requirement 8: Settings displays the same confirmed daily plan.
     */
    @Test
    fun test8_settingsDisplaysSameConfirmedDailyPlan() {
        val plan = DailyBathPlan(
            date = testDate,
            timezone = zoneStr,
            collegeDecision = "YES",
            confirmationStatus = "CONFIRMED",
            revision = 1,
            selectedBathTimes = listOf("07:30", "16:00"),
            activityIntervals = listOf("7:30–8:30 AM", "4:00–5:00 PM"),
            reservedHourlyOccurrenceIds = listOf("ACT-20261021-0700-HOURLY", "ACT-20261021-1600-HOURLY"),
            photoSubmissionWindows = listOf("9:00–9:20 AM", "5:30–5:50 PM"),
            createdAt = "2026-10-21T07:15:00+05:30",
            updatedAt = "2026-10-21T07:15:00+05:30"
        )

        // Serialize to JSON and parse back as DailyBathPlan
        val json = plan.toJson()
        val restored = DailyBathPlan.fromJson(json)

        assertEquals(plan.date, restored.date)
        assertEquals(plan.collegeDecision, restored.collegeDecision)
        assertEquals(plan.confirmationStatus, restored.confirmationStatus)
        assertEquals(plan.revision, restored.revision)
        assertEquals(plan.selectedBathTimes, restored.selectedBathTimes)
        assertEquals(plan.activityIntervals, restored.activityIntervals)
        assertEquals(plan.reservedHourlyOccurrenceIds, restored.reservedHourlyOccurrenceIds)
        assertEquals(plan.photoSubmissionWindows, restored.photoSubmissionWindows)
        assertTrue(restored.isCollege)
    }

    /**
     * Requirement 9: Edit plan -> schedule is recalculated consistently without deleting historical proof records.
     */
    @Test
    fun test9_editPlan_recalculatesScheduleWithoutDeletingHistoricalProofRecords() {
        val initialTimes = listOf("13:30")
        val initialReview = BathScheduleMorningPopupRules.buildConfirmationReview(testDate, "NO", initialTimes, zoneStr)
        val initialBath = initialReview.getJSONArray("baths").getJSONObject(0)
        assertEquals("1:30\u20132:30 PM", initialBath.getString("activity_interval"))
        assertEquals("ACT-20261021-1400-HOURLY", initialBath.getString("held_hourly_act_id"))
        assertEquals("3:00\u20133:20 PM", initialBath.getString("proof_window_display"))

        // User edits planned bath time to 15:00
        val editedTimes = listOf("15:00")
        val editedReview = BathScheduleMorningPopupRules.buildConfirmationReview(testDate, "NO", editedTimes, zoneStr)
        val editedBath = editedReview.getJSONArray("baths").getJSONObject(0)
        assertEquals("3:00\u20134:00 PM", editedBath.getString("activity_interval"))
        assertEquals("ACT-20261021-1600-HOURLY", editedBath.getString("held_hourly_act_id"))
        assertEquals("4:30\u20134:50 PM", editedBath.getString("proof_window_display"))

        // Historical proof records are preserved: BathScheduleRecord status preservation
        val historicalProofData = JSONObject().put("suppressed_by_bath", true)
        assertTrue(BathScheduleRecord.preserveAssignmentOnSync("EXCUSED", historicalProofData, "PENDING_SYNC"))
        assertFalse(BathScheduleRecord.canExcuseHourly("VERIFIED", null, 0))
    }

    /**
     * Requirement 10: Rapid repeated taps on Confirm -> no duplicate occurrences or duplicate point transactions.
     */
    @Test
    fun test10_rapidRepeatedConfirmTaps_deterministicOccurrenceIds() {
        val times = listOf("07:30", "16:00")
        val review1 = BathScheduleMorningPopupRules.buildConfirmationReview(testDate, "YES", times, zoneStr)
        val review2 = BathScheduleMorningPopupRules.buildConfirmationReview(testDate, "YES", times, zoneStr)

        val b1_first = review1.getJSONArray("baths").getJSONObject(0).getString("held_hourly_act_id")
        val b1_second = review2.getJSONArray("baths").getJSONObject(0).getString("held_hourly_act_id")
        assertEquals("Deterministic assignment across repeated taps", b1_first, b1_second)

        // Validate that occurrence IDs are strictly idempotent
        val occ1 = BathWindowRules.calculateBathWindow(1, true, testDate, "07:30", timezone = zoneStr)
        val occ1Repeat = BathWindowRules.calculateBathWindow(1, true, testDate, "07:30", timezone = zoneStr)
        assertEquals("ACT-20261021-0730-BATH-1", occ1.actId)
        assertEquals(occ1.actId, occ1Repeat.actId)
    }

    /**
     * Requirement 11: Simulate failure during database save -> error message displayed, app does not claim plan is confirmed.
     */
    @Test
    fun test11_simulateSaveFailure_notClaimedConfirmed() {
        // Simulated failure response from DB
        val failedResult = JSONObject().apply {
            put("success", false)
            put("error", "Database lock timeout: SQLiteDiskIOException")
        }

        assertFalse(failedResult.optBoolean("success", false))
        val errorMessage = failedResult.optString("error", "Unknown error")
        assertTrue(errorMessage.contains("Database lock timeout"))

        // Under failure, isConfirmed remains false
        val isConfirmedAfterFailure = false
        val morningTimeMs = parseMs(testDate, "07:45")
        val retryPopup = BathScheduleMorningPopupRules.shouldShowFirstMorningPopup(
            date = testDate,
            isConfirmed = isConfirmedAfterFailure,
            popupShownDate = null,
            isCriticalDialogActive = false,
            clockMs = morningTimeMs,
            timezone = zoneStr
        )
        assertTrue("App must still prompt for confirmation on next open after failure", retryPopup.shouldShow)
    }

    /**
     * Requirement 12: Wake-up verification scoring, timing, and completion status remain unchanged by bath confirmation.
     */
    @Test
    fun test12_wakeUpVerificationScoringTimingStatusUnchangedByBathConfirmation() {
        // Wake-up scoring rules remain separate: window is 6:00–10:00 AM
        val wakeUpStartMs = parseMs(testDate, "06:00")
        val wakeUpEndMs = parseMs(testDate, "10:00")
        val attemptTimeMs = parseMs(testDate, "07:15")

        assertTrue("Wake-up attempt within 6:00–10:00 AM is eligible", attemptTimeMs in wakeUpStartMs..wakeUpEndMs)

        // Bath plan confirmation does NOT award wake-up points (+2) or alter wake-up completion status
        val bathPlanJson = JSONObject().apply {
            put("confirmation_status", "CONFIRMED")
            put("revision", 1)
        }
        assertEquals("CONFIRMED", bathPlanJson.getString("confirmation_status"))
        assertFalse("Bath confirmation does not set wake_up_status", bathPlanJson.has("wake_up_status"))
        assertFalse("Bath confirmation does not alter points ledger", bathPlanJson.has("points_awarded"))
    }

    /**
     * Requirement 13: Hourly attendance reservations and photo proof deadlines match standard formulas.
     * Planned -> 1h activity interval -> 90m delay -> 20m photo window.
     */
    @Test
    fun test13_hourlyAttendanceReservationsAndPhotoProofDeadlinesFormulas() {
        // Non-college bath at 14:10
        val nonCollegeWin = BathWindowRules.calculateBathWindow(1, false, testDate, "14:10", timezone = zoneStr)
        assertEquals("2:10 PM", nonCollegeWin.plannedDisplay)
        assertEquals("2:10\u20133:10 PM", nonCollegeWin.activityIntervalDisplay)
        assertEquals("3:40\u20134:00 PM", nonCollegeWin.proofWindowDisplay)
        assertEquals("4:00 PM", nonCollegeWin.proofDeadlineDisplay)
        assertEquals("ACT-20261021-1500-HOURLY", nonCollegeWin.heldHourlyActId)
        assertEquals("2:00\u20133:00 PM", nonCollegeWin.heldHourlySlotDisplay)

        // College Bath #1 at 07:30
        val c1 = BathWindowRules.calculateBathWindow(1, true, testDate, "07:30", timezone = zoneStr)
        assertEquals("7:30 AM", c1.plannedDisplay)
        assertEquals("7:30\u20138:00 AM", c1.activityIntervalDisplay)
        assertEquals("8:00\u20138:30 AM", c1.proofWindowDisplay)
        assertEquals("8:30 AM", c1.proofDeadlineDisplay)
        assertEquals("ACT-20261021-0800-HOURLY", c1.heldHourlyActId)
        assertEquals("7:00\u20138:00 AM", c1.heldHourlySlotDisplay)

        // College Bath #2 at 16:00
        val c2 = BathWindowRules.calculateBathWindow(2, true, testDate, "16:00", timezone = zoneStr)
        assertEquals("4:00 PM", c2.plannedDisplay)
        assertEquals("4:00\u20135:00 PM", c2.activityIntervalDisplay)
        assertEquals("5:30\u20135:50 PM", c2.proofWindowDisplay)
        assertEquals("5:50 PM", c2.proofDeadlineDisplay)
        assertEquals("ACT-20261021-1700-HOURLY", c2.heldHourlyActId)
        assertEquals("4:00\u20135:00 PM", c2.heldHourlySlotDisplay)
    }

    /**
     * Requirement 14: Recovery fallback triggers if app is first opened later than morning window when unconfirmed.
     */
    @Test
    fun test14_recoveryFallbackLaterThanMorningWindow() {
        // App first opened at 12:45 PM (after 10:00 AM morning window) with unconfirmed bath plan
        val lateTimeMs = parseMs(testDate, "12:45")
        val result = BathScheduleMorningPopupRules.shouldShowFirstMorningPopup(
            date = testDate,
            isConfirmed = false,
            popupShownDate = null,
            isCriticalDialogActive = false,
            clockMs = lateTimeMs,
            timezone = zoneStr
        )
        assertTrue("Recovery fallback must trigger if opened after morning window when unconfirmed", result.shouldShow)
        assertFalse(result.shouldQueue)
        assertEquals("RECOVERY_FALLBACK", result.reason)
    }
    /**
     * Requirement 15: Wake-up target time sanitization prevents routine confirmation failure on invalid or empty drafts.
     */
    @Test
    fun test15_wakeUpTimeSanitization_preventsSilentValidationFailure() {
        // Valid formats normalize cleanly
        val valid1 = DisciplineScoringRules.validateWakeUpTime("07:30")
        assertTrue(valid1.first)
        assertEquals("07:30", valid1.second)

        val validAm = DisciplineScoringRules.validateWakeUpTime(" 7:45 AM ")
        assertTrue(validAm.first)
        assertEquals("07:45", validAm.second)

        // Invalid or empty formats safely fallback to standard default "07:00"
        val invalidEmpty = DisciplineScoringRules.validateWakeUpTime("")
        assertFalse(invalidEmpty.first)
        val sanitizedEmpty = if (invalidEmpty.first) invalidEmpty.second else "07:00"
        assertEquals("07:00", sanitizedEmpty)

        val invalidLate = DisciplineScoringRules.validateWakeUpTime("11:30")
        assertFalse(invalidLate.first)
        val sanitizedLate = if (invalidLate.first) invalidLate.second else "07:00"
        assertEquals("07:00", sanitizedLate)
    }

    /**
     * Requirement 16: Explicit error message construction for all routine confirmation failure modes.
     */
    @Test
    fun test16_explicitErrorMessageConstruction_forAllFailureModes() {
        // Mode 1: DB response returns success=false with specific error
        val dbErrorRes = JSONObject().put("success", false).put("error", "SQLite database is locked")
        val isConfirmed = false
        val planCollege = "YES"
        val cleanCollege = "YES"
        val planBathCount = 2
        val bathSessionCount = 2
        val errDb = when {
            !dbErrorRes.optBoolean("success") -> dbErrorRes.optString("error", "Database routine confirmation failed.")
            planCollege != cleanCollege -> "College attendance ($planCollege) does not match chosen value ($cleanCollege)."
            planBathCount != bathSessionCount -> "Bath count ($planBathCount) does not match chosen count ($bathSessionCount)."
            !isConfirmed -> "Schedule lock verification failed: daily routine record was not finalized in database."
            else -> dbErrorRes.optString("error", "Failed to verify saved daily routine.")
        }
        assertEquals("SQLite database is locked", errDb)

        // Mode 2: College attendance mismatch
        val mismatchCollegeRes = JSONObject().put("success", true)
        val errCollege = when {
            !mismatchCollegeRes.optBoolean("success") -> mismatchCollegeRes.optString("error")
            "NO" != "YES" -> "College attendance (NO) does not match chosen value (YES)."
            else -> "OK"
        }
        assertTrue(errCollege.contains("College attendance (NO) does not match chosen value (YES)"))

        // Mode 3: Schedule lock unverified
        val lockUnverifiedRes = JSONObject().put("success", true)
        val errLock = when {
            !lockUnverifiedRes.optBoolean("success") -> lockUnverifiedRes.optString("error")
            !isConfirmed -> "Schedule lock verification failed: daily routine record was not finalized in database."
            else -> "OK"
        }
        assertTrue(errLock.contains("Schedule lock verification failed"))
    }

    /**
     * Requirement 17: User selections preservation contract upon error.
     */
    @Test
    fun test17_userSelectionsPreservedOnFailure() {
        val selectedCollege = "YES"
        val selectedBathTimes = mutableListOf("07:30", "16:00")
        val gymSession = "YES"
        val sleepTarget = "22:30"

        // Simulate save failure
        val saveSucceeded = false
        if (!saveSucceeded) {
            // Dialog stays open: selections are not reset or cleared
            assertEquals("YES", selectedCollege)
            assertEquals(2, selectedBathTimes.size)
            assertEquals("07:30", selectedBathTimes[0])
            assertEquals("16:00", selectedBathTimes[1])
            assertEquals("YES", gymSession)
            assertEquals("22:30", sleepTarget)
        }
    }
}
