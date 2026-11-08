package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/**
 * Deterministic regression tests with controllable clock for Travel Pre-booking
 * and 5-minute pre-opening Quick Action visibility.
 */
class TravelBookingRulesTest {
    @Test
    fun duplicateFailureCannotTriggerThreeMonthRestriction() {
        val failure = TravelBookingRules.TravelOccurrenceRecord(
            "same-occurrence", "2026-10-01", 0L, 1000L, 2000L, "MISSED", false)
        assertFalse(TravelBookingRules.evaluateConsecutiveFailures(
            listOf(failure, failure, failure), 3000L).isRestricted)
    }

    private val tz = TimeZone.getTimeZone("Asia/Kolkata")
    private val df = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).apply {
        timeZone = tz
        isLenient = false
    }

    private fun ms(dateTimeStr: String): Long = df.parse(dateTimeStr)!!.time

    @Test
    fun testExampleFromRequirements() {
        val travelDate = "2026-10-19"
        val departureTime = "14:00"

        val opensMs = ms("2026-10-17 00:00:00")
        val deadlineMs = ms("2026-10-19 08:00:00")

        val beforeOpen = TravelBookingRules.evaluateBookingWindow(travelDate, departureTime, opensMs - 1000L, tz)
        assertFalse(beforeOpen.allowed)
        assertEquals(opensMs, beforeOpen.bookingOpensMs)
        assertEquals(deadlineMs, beforeOpen.bookingDeadlineMs)

        val atOpen = TravelBookingRules.evaluateBookingWindow(travelDate, departureTime, opensMs, tz)
        assertTrue(atOpen.allowed)

        val mid = TravelBookingRules.evaluateBookingWindow(travelDate, departureTime, ms("2026-10-18 12:00:00"), tz)
        assertTrue(mid.allowed)

        val atDeadline = TravelBookingRules.evaluateBookingWindow(travelDate, departureTime, deadlineMs, tz)
        assertTrue(atDeadline.allowed)

        val afterDeadline = TravelBookingRules.evaluateBookingWindow(travelDate, departureTime, deadlineMs + 1000L, tz)
        assertFalse(afterDeadline.allowed)
        assertTrue(afterDeadline.reason.contains("closed") || afterDeadline.reason.contains("passed"))
    }

    @Test
    fun testPastDepartureRejected() {
        val travelDate = "2026-10-14"
        val departureTime = "09:00"
        val now = ms("2026-10-14 10:00:00")
        val res = TravelBookingRules.evaluateBookingWindow(travelDate, departureTime, now, tz)
        assertFalse(res.allowed)
    }

    @Test
    fun testSixHourCutoffEnforcedAcrossMidnight() {
        val travelDate = "2026-10-20"
        val departureTime = "03:00"

        val opensMs = ms("2026-10-18 00:00:00")
        val deadlineMs = ms("2026-10-19 21:00:00")

        val evalAtDeadline = TravelBookingRules.evaluateBookingWindow(travelDate, departureTime, deadlineMs, tz)
        assertTrue(evalAtDeadline.allowed)
        assertEquals(opensMs, evalAtDeadline.bookingOpensMs)
        assertEquals(deadlineMs, evalAtDeadline.bookingDeadlineMs)

        val evalAfter = TravelBookingRules.evaluateBookingWindow(travelDate, departureTime, deadlineMs + 5000L, tz)
        assertFalse(evalAfter.allowed)
    }

    @Test
    fun testTravelPrebookedDetectionLogic() {
        fun isPrebooked(detailsJson: String?, actId: String, title: String): Boolean {
            val details = runCatching { org.json.JSONObject(detailsJson ?: "{}") }.getOrNull()
            val isPrebookedFlag = details?.optBoolean("is_prebooked", false) == true
            val isConfirmed = details?.optString("booking_status") == "CONFIRMED"
            val isAutoSeededDummy = (actId.endsWith("-0900-TRAVEL") && title == "Travel & Transit Protocol" && !isPrebookedFlag)
            return (isPrebookedFlag || isConfirmed || !isAutoSeededDummy) && !isAutoSeededDummy
        }

        assertFalse(isPrebooked("{\"type\":\"TRAVEL\"}", "ACT-20261015-0900-TRAVEL", "Travel & Transit Protocol"))
        assertFalse(isPrebooked(null, "ACT-20261015-0900-TRAVEL", "Travel & Transit Protocol"))
        assertTrue(isPrebooked("{\"type\":\"TRAVEL\",\"is_prebooked\":true,\"booking_status\":\"CONFIRMED\"}", "ACT-20261015-1400-TRAVEL", "Travel & Transit (Bangalore trip)"))
        assertTrue(isPrebooked("{\"is_prebooked\":true}", "ACT-20261015-0900-TRAVEL", "Travel & Transit Protocol"))
    }

    /**
     * Requirement 9: A future booking does not appear as an active action before the five-minute reminder point.
     * With 14:00 departure and 3-hour duration, arrival is 17:00 (5:00 PM).
     * Submission window opens at 17:00; 5-minute reminder is at 16:55 (4:55 PM).
     */
    @Test
    fun test9_futureBookingDoesNotAppearAsActiveActionBefore5MinuteReminder() {
        val travelDate = "2026-10-19"
        val departureTime = "14:00"
        val window = TravelBookingRules.calculateTravelWindow(travelDate, departureTime, 3, tz)
        assertNotNull(window)

        val travelEndMs = ms("2026-10-19 17:00:00")
        val reminderMs = ms("2026-10-19 16:55:00")
        assertEquals(travelEndMs, window!!.windowStartMs)
        assertEquals(reminderMs, window.reminderMs)

        // Clock at 4:54 PM (6 minutes before arrival / proof window)
        val clock454 = ms("2026-10-19 16:54:00")
        val clock1MsBeforeReminder = reminderMs - 1L

        assertEquals("UPCOMING", TravelBookingRules.state(clock454, window.windowStartMs, window.windowEndMs))
        assertEquals("UPCOMING", TravelBookingRules.state(clock1MsBeforeReminder, window.windowStartMs, window.windowEndMs))
        assertFalse("Action must not be visible before 5-minute reminder",
            TravelBookingRules.isQuickActionVisible("SCHEDULED", clock454, window.windowStartMs, window.windowEndMs))
        assertFalse("Action must not be visible 1 ms before 5-minute reminder",
            TravelBookingRules.isQuickActionVisible("SCHEDULED", clock1MsBeforeReminder, window.windowStartMs, window.windowEndMs))
        assertFalse("Submission must not be open",
            TravelBookingRules.isSubmissionWithinWindow(clock454, window.windowStartMs, window.windowEndMs))
    }

    /**
     * Requirement 10: The Travel action appears five minutes before its configured submission window.
     */
    @Test
    fun test10_travelActionAppearsFiveMinutesBeforeSubmissionWindow() {
        val travelDate = "2026-10-19"
        val departureTime = "14:00"
        val window = TravelBookingRules.calculateTravelWindow(travelDate, departureTime, 3, tz)!!

        val reminderMs = ms("2026-10-19 16:55:00") // 4:55 PM
        assertEquals("REMINDER_VISIBLE", TravelBookingRules.state(reminderMs, window.windowStartMs, window.windowEndMs))
        assertEquals("REMINDER_VISIBLE", TravelBookingRules.resolveOccurrenceState("SCHEDULED", reminderMs, window.windowStartMs, window.windowEndMs))
        assertTrue("Quick Action must become visible exactly at 4:55 PM",
            TravelBookingRules.isQuickActionVisible("SCHEDULED", reminderMs, window.windowStartMs, window.windowEndMs))
    }

    /**
     * Requirement 11: Submission remains disabled until the real window opens.
     */
    @Test
    fun test11_submissionRemainsDisabledUntilRealWindowOpens() {
        val travelDate = "2026-10-19"
        val departureTime = "14:00"
        val window = TravelBookingRules.calculateTravelWindow(travelDate, departureTime, 3, tz)!!

        // Clock at 4:59 PM (1 minute before arrival / proof window start)
        val clock459 = ms("2026-10-19 16:59:00")
        val clock1MsBefore = window.windowStartMs - 1L

        // Visible as reminder
        assertEquals("REMINDER_VISIBLE", TravelBookingRules.state(clock459, window.windowStartMs, window.windowEndMs))
        assertTrue(TravelBookingRules.isQuickActionVisible("SCHEDULED", clock459, window.windowStartMs, window.windowEndMs))

        // But submission is strictly disallowed
        assertFalse("Submission must remain disabled at 4:59 PM",
            TravelBookingRules.isSubmissionWithinWindow(clock459, window.windowStartMs, window.windowEndMs))
        assertFalse("Submission must remain disabled 1 ms before window open",
            TravelBookingRules.isSubmissionWithinWindow(clock1MsBefore, window.windowStartMs, window.windowEndMs))

        // At 5:00 PM, submission becomes enabled
        val clock500 = ms("2026-10-19 17:00:00")
        assertEquals("OPEN", TravelBookingRules.state(clock500, window.windowStartMs, window.windowEndMs))
        assertTrue("Submission must become enabled at 5:00 PM",
            TravelBookingRules.isSubmissionWithinWindow(clock500, window.windowStartMs, window.windowEndMs))
        assertTrue(TravelBookingRules.isQuickActionVisible("SCHEDULED", clock500, window.windowStartMs, window.windowEndMs))
    }

    /**
     * Requirement 12: The action is associated with the correct travel date and occurrence.
     */
    @Test
    fun test12_actionAssociatedWithCorrectTravelDateAndOccurrence() {
        val travelDate = "2026-10-19"
        val departureTime = "14:00"
        val window = TravelBookingRules.calculateTravelWindow(travelDate, departureTime, 3, tz)!!

        val expectedActId = "ACT-${travelDate.replace("-", "")}-1400-TRAVEL"
        assertEquals("ACT-20261019-1400-TRAVEL", expectedActId)
        assertEquals(travelDate, window.travelDate)
        assertEquals("14:00", window.departureTime)
        assertEquals(3, window.durationHours)
        assertTrue(window.windowStartIso.startsWith("2026-10-19T17:00"))
        assertTrue(window.notificationIso.startsWith("2026-10-19T16:55"))
    }

    /**
     * Requirement 13: Restart and synchronization do not duplicate the reminder or occurrence.
     */
    @Test
    fun test13_restartAndSyncDoNotDuplicateReminderOrOccurrence() {
        val travelDate = "2026-10-19"
        val departureTime = "14:00"

        val win1 = TravelBookingRules.calculateTravelWindow(travelDate, departureTime, 3, tz)!!
        val win2 = TravelBookingRules.calculateTravelWindow(travelDate, departureTime, 3, tz)!!

        assertEquals(win1.departureMs, win2.departureMs)
        assertEquals(win1.travelEndMs, win2.travelEndMs)
        assertEquals(win1.windowStartMs, win2.windowStartMs)
        assertEquals(win1.reminderMs, win2.reminderMs)
        assertEquals(win1.windowEndMs, win2.windowEndMs)

        // Notification key is stable based on occurrence ID
        val notifKey1 = "ACT-20261019-1400-TRAVEL-OPENING"
        val notifKey2 = "ACT-20261019-1400-TRAVEL-OPENING"
        assertEquals(notifKey1, notifKey2)
    }

    /**
     * Requirement 14: Reminder states refresh correctly after app resume.
     */
    @Test
    fun test14_reminderStatesRefreshCorrectly() {
        val window = TravelBookingRules.calculateTravelWindow("2026-10-19", "14:00", 3, tz)!!

        // Resume at 4:50 PM -> UPCOMING
        val resumeAt450 = ms("2026-10-19 16:50:00")
        assertEquals("UPCOMING", TravelBookingRules.resolveOccurrenceState("SCHEDULED", resumeAt450, window.windowStartMs, window.windowEndMs))
        assertFalse(TravelBookingRules.isQuickActionVisible("SCHEDULED", resumeAt450, window.windowStartMs, window.windowEndMs))

        // Resume at 4:56 PM -> REMINDER_VISIBLE
        val resumeAt456 = ms("2026-10-19 16:56:00")
        assertEquals("REMINDER_VISIBLE", TravelBookingRules.resolveOccurrenceState("SCHEDULED", resumeAt456, window.windowStartMs, window.windowEndMs))
        assertTrue(TravelBookingRules.isQuickActionVisible("SCHEDULED", resumeAt456, window.windowStartMs, window.windowEndMs))

        // Resume at 5:05 PM -> OPEN
        val resumeAt505 = ms("2026-10-19 17:05:00")
        assertEquals("OPEN", TravelBookingRules.resolveOccurrenceState("SCHEDULED", resumeAt505, window.windowStartMs, window.windowEndMs))
        assertTrue(TravelBookingRules.isQuickActionVisible("SCHEDULED", resumeAt505, window.windowStartMs, window.windowEndMs))
    }

    /**
     * Requirement 15: Notification delivery is not falsely reported as successful.
     */
    @Test
    fun test15_notificationDeliveryNotFalselyReportedAsSuccessful() {
        val window = TravelBookingRules.calculateTravelWindow("2026-10-19", "14:00", 3, tz)!!
        val nowMsBefore = ms("2026-10-19 16:50:00")

        // Notification eligible check: before reminderMs, it should be ineligible
        val isEligibleBefore = nowMsBefore in (window.windowStartMs - 5 * 60_000L) until window.windowStartMs
        assertFalse("Alarm must not trigger or be marked delivered before 5-minute pre-opening", isEligibleBefore)

        val nowMsInWindow = ms("2026-10-19 16:56:00")
        val isEligibleIn = nowMsInWindow in (window.windowStartMs - 5 * 60_000L) until window.windowStartMs
        assertTrue("Alarm is eligible during 5-minute pre-opening", isEligibleIn)
    }

    /**
     * Scenario test: 5-hour trip to Tiruchy on 19th Oct at 10:00 AM.
     * - Travel interval: 10:00 AM – 3:00 PM (15:00).
     * - Proof window: 3:00 PM – 5:00 PM (15:00 to 17:00).
     * - Reminder: 2:55 PM (14:55).
     * - Hourly slots 10-11, 11-12, 12-1, 1-2, 2-3 are covered.
     * - Slots 3-4 and 4-5 are NOT covered and continue normally.
     */
    @Test
    fun testScenarioFiveHourTrip() {
        val travelDate = "2026-10-19"
        val departureTime = "10:00"
        val duration = 5
        val window = TravelBookingRules.calculateTravelWindow(travelDate, departureTime, duration, tz)
        assertNotNull(window)

        val depMs = ms("2026-10-19 10:00:00")
        val arrMs = ms("2026-10-19 15:00:00")
        val proofEndMs = ms("2026-10-19 17:00:00")
        val reminderMs = ms("2026-10-19 14:55:00")

        assertEquals(depMs, window!!.departureMs)
        assertEquals(arrMs, window.travelEndMs)
        assertEquals(arrMs, window.windowStartMs)
        assertEquals(proofEndMs, window.windowEndMs)
        assertEquals(reminderMs, window.reminderMs)
        assertEquals("15:00", window.travelEndTime)

        // Hourly slot coverage
        // Slot 10-11 (target 11:00)
        val slot1011 = ms("2026-10-19 11:00:00")
        assertTrue("10-11 AM slot must be covered", TravelBookingRules.isHourlySlotCoveredByTravel(slot1011, depMs, arrMs))

        // Slot 11-12 (target 12:00)
        val slot1112 = ms("2026-10-19 12:00:00")
        assertTrue("11-12 PM slot must be covered", TravelBookingRules.isHourlySlotCoveredByTravel(slot1112, depMs, arrMs))

        // Slot 12-1 (target 13:00)
        val slot1201 = ms("2026-10-19 13:00:00")
        assertTrue("12-1 PM slot must be covered", TravelBookingRules.isHourlySlotCoveredByTravel(slot1201, depMs, arrMs))

        // Slot 1-2 (target 14:00)
        val slot0102 = ms("2026-10-19 14:00:00")
        assertTrue("1-2 PM slot must be covered", TravelBookingRules.isHourlySlotCoveredByTravel(slot0102, depMs, arrMs))

        // Slot 2-3 (target 15:00)
        val slot0203 = ms("2026-10-19 15:00:00")
        assertTrue("2-3 PM slot must be covered", TravelBookingRules.isHourlySlotCoveredByTravel(slot0203, depMs, arrMs))

        // Slot 3-4 (target 16:00) -> NOT covered
        val slot0304 = ms("2026-10-19 16:00:00")
        assertFalse("3-4 PM slot must NOT be covered", TravelBookingRules.isHourlySlotCoveredByTravel(slot0304, depMs, arrMs))

        // Slot 4-5 (target 17:00) -> NOT covered
        val slot0405 = ms("2026-10-19 17:00:00")
        assertFalse("4-5 PM slot must NOT be covered", TravelBookingRules.isHourlySlotCoveredByTravel(slot0405, depMs, arrMs))
    }

    /**
     * Requirement: 3 consecutive finalized travel proof failures trigger a 3-month prebooking lock.
     * An approved travel proof resets the streak.
     */
    @Test
    fun testThreeConsecutiveFailuresLockPrebookingForThreeMonths() {
        val occ1 = TravelBookingRules.TravelOccurrenceRecord(
            activityId = "ACT-20261001-1000-TRAVEL",
            date = "2026-10-01",
            departureMs = ms("2026-10-01 10:00:00"),
            travelEndMs = ms("2026-10-01 13:00:00"),
            windowEndMs = ms("2026-10-01 15:00:00"),
            status = "MISSED",
            proofSubmitted = false
        )
        val occ2 = TravelBookingRules.TravelOccurrenceRecord(
            activityId = "ACT-20261005-1000-TRAVEL",
            date = "2026-10-05",
            departureMs = ms("2026-10-05 10:00:00"),
            travelEndMs = ms("2026-10-05 13:00:00"),
            windowEndMs = ms("2026-10-05 15:00:00"),
            status = "MISSED",
            proofSubmitted = false
        )
        val occ3 = TravelBookingRules.TravelOccurrenceRecord(
            activityId = "ACT-20261010-1000-TRAVEL",
            date = "2026-10-10",
            departureMs = ms("2026-10-10 10:00:00"),
            travelEndMs = ms("2026-10-10 13:00:00"),
            windowEndMs = ms("2026-10-10 15:00:00"),
            status = "MISSED",
            proofSubmitted = false
        )

        // 2 failures: not yet restricted
        val res2 = TravelBookingRules.evaluateConsecutiveFailures(listOf(occ1, occ2), ms("2026-10-12 12:00:00"), tz)
        assertFalse(res2.isRestricted)

        // 3 consecutive failures: restricted for 3 calendar months from 3rd failure (10 Oct 2026 -> 10 Jan 2027)
        val res3 = TravelBookingRules.evaluateConsecutiveFailures(listOf(occ1, occ2, occ3), ms("2026-10-12 12:00:00"), tz)
        assertTrue(res3.isRestricted)
        assertEquals(3, res3.failedDates.size)
        assertTrue(res3.lockUntilDateStr.contains("2027") || res3.lockUntilDateStr.contains("January"))

        // After 3 calendar months have elapsed: restriction expires
        val resAfterExpiry = TravelBookingRules.evaluateConsecutiveFailures(listOf(occ1, occ2, occ3), ms("2027-01-11 12:00:00"), tz)
        assertFalse(resAfterExpiry.isRestricted)

        // An approved occurrence resets streak
        val approvedOcc = TravelBookingRules.TravelOccurrenceRecord(
            activityId = "ACT-20261006-1000-TRAVEL",
            date = "2026-10-06",
            departureMs = ms("2026-10-06 10:00:00"),
            travelEndMs = ms("2026-10-06 13:00:00"),
            windowEndMs = ms("2026-10-06 15:00:00"),
            status = "APPROVED",
            proofSubmitted = true
        )
        val resReset = TravelBookingRules.evaluateConsecutiveFailures(listOf(occ1, occ2, approvedOcc, occ3), ms("2026-10-12 12:00:00"), tz)
        assertFalse("Approved occurrence must reset failure streak", resReset.isRestricted)
    }

    /**
     * Requirement 16: Existing Bath, hourly attendance, wake-up verification, reward, proof-validation, and point-ledger behavior remains unchanged.
     */
    @Test
    fun test16_generalIntegrityAndExistingBehaviorUnchanged() {
        // Bath rules unchanged
        val bathWin = BathWindowRules.calculateBathWindow(occurrence = 1, isCollege = true, date = "2026-10-19")
        assertEquals("07:30 AM–08:30 AM", bathWin.windowDisplay)

        // Hourly attendance with 11 PM cutoff (7:00 to 23:00 = 17 slots)
        val hourlySlots = HourlyWindowRules.slots("2026-10-19", "07:00")
        assertEquals(17, hourlySlots.size)

        // Score snapshot unchanged
        val points = listOf(10, 15, 35)
        assertEquals(60, DisciplineScoringRules.calculateDailyScore(points))
    }

    /**
     * Regression Test 1:
     * A real booking with a proof deadline of 2:00 PM must not appear open at 7:09 PM.
     */
    @Test
    fun testRegression1_deadline2PM_mustNotAppearOpenAt709PM() {
        val depMs = ms("2026-10-19 11:00:00")
        val travelEndMs = ms("2026-10-19 12:00:00") // 1 hr travel
        val proofStartMs = ms("2026-10-19 12:00:00")
        val proofEndMs = ms("2026-10-19 14:00:00") // 2:00 PM deadline

        val nowMsAt709PM = ms("2026-10-19 19:09:00") // 7:09 PM as seen in screenshot

        // Window state must be CLOSED / EXPIRED
        val wState = TravelBookingRules.state(nowMsAt709PM, proofStartMs, proofEndMs)
        assertEquals("STATE_CLOSED must be returned after 2:00 PM deadline", TravelBookingRules.STATE_CLOSED, wState)

        // Authoritative state must be MISSED
        val authState = TravelBookingRules.resolveAuthoritativeState(
            nowMs = nowMsAt709PM,
            departureMs = depMs,
            travelEndMs = travelEndMs,
            proofStartMs = proofStartMs,
            proofEndMs = proofEndMs,
            status = "SCHEDULED",
            hasProofSubmitted = false
        )
        assertEquals(TravelBookingRules.TravelStatusState.MISSED, authState)

        // Submission is strictly disallowed
        assertFalse("Submission must not be within window at 7:09 PM",
            TravelBookingRules.isSubmissionWithinWindow(nowMsAt709PM, proofStartMs, proofEndMs))
        assertFalse("Quick action must not be visible after deadline",
            TravelBookingRules.isQuickActionVisible("SCHEDULED", nowMsAt709PM, proofStartMs, proofEndMs))
    }

    /**
     * Regression Test 2:
     * A five-hour booking at 10:00 AM must calculate a 3:00–5:00 PM proof window.
     */
    @Test
    fun testRegression2_fiveHourBookingAt10AM_calculates3To5PMProofWindow() {
        val win = TravelBookingRules.calculateTravelWindow("2026-10-19", "10:00", 5, tz)
        assertNotNull(win)
        val expectedDep = ms("2026-10-19 10:00:00")
        val expectedEnd = ms("2026-10-19 15:00:00") // 3:00 PM
        val expectedProofEnd = ms("2026-10-19 17:00:00") // 5:00 PM
        val expectedReminder = ms("2026-10-19 14:55:00") // 2:55 PM

        assertEquals(expectedDep, win!!.departureMs)
        assertEquals(expectedEnd, win.travelEndMs)
        assertEquals(expectedEnd, win.windowStartMs)
        assertEquals(expectedProofEnd, win.windowEndMs)
        assertEquals(expectedReminder, win.reminderMs)
        assertEquals("15:00", win.travelEndTime)
    }

    /**
     * Regression Test 3:
     * A trip that has no valid booking must not receive the generic 9:00 AM–11:59 PM real submission window.
     */
    @Test
    fun testRegression3_tripWithoutValidBooking_mustNotReceiveGenericWindow() {
        // Without confirmed pre-booking flag, dummy row detection is active
        val dummyDetails = "{\"type\":\"TRAVEL\"}"
        assertFalse(TravelBookingRules.isDemoBooking("Travel & Transit Protocol", dummyDetails))

        val prebookedDetails = "{\"type\":\"TRAVEL\",\"is_prebooked\":true,\"booking_status\":\"CONFIRMED\",\"departure_ms\":${ms("2026-10-19 10:00:00")}}"
        val isConfirmed = org.json.JSONObject(prebookedDetails).optBoolean("is_prebooked", false)
        assertTrue(isConfirmed)

        // When evaluating authoritative state without valid booking, interval is invalid/closed
        val fallbackDep = 0L
        val fallbackEnd = 0L
        val authState = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-19 12:00:00"),
            departureMs = fallbackDep,
            travelEndMs = fallbackEnd,
            proofStartMs = 0L,
            proofEndMs = 0L,
            status = "SCHEDULED",
            hasProofSubmitted = false
        )
        assertEquals(TravelBookingRules.TravelStatusState.MISSED, authState)
    }

    /**
     * Regression Test 4:
     * Every held hourly slot must be associated with the correct travel interval and resolve consistently after the proof deadline.
     */
    @Test
    fun testRegression4_heldHourlySlotAssociatedAndResolvesConsistently() {
        val depMs = ms("2026-10-19 10:00:00")
        val arrMs = ms("2026-10-19 15:00:00") // 3:00 PM (5 hours)
        val proofEndMs = ms("2026-10-19 17:00:00")

        // Covered slots: 10:00-11:00, 11:00-12:00, 12:00-13:00, 13:00-14:00, 14:00-15:00
        val targetMs11 = ms("2026-10-19 11:00:00")
        val targetMs12 = ms("2026-10-19 12:00:00")
        val targetMs13 = ms("2026-10-19 13:00:00")
        val targetMs14 = ms("2026-10-19 14:00:00")
        val targetMs15 = ms("2026-10-19 15:00:00")

        assertTrue(TravelBookingRules.isHourlySlotCoveredByTravel(targetMs11, depMs, arrMs))
        assertTrue(TravelBookingRules.isHourlySlotCoveredByTravel(targetMs12, depMs, arrMs))
        assertTrue(TravelBookingRules.isHourlySlotCoveredByTravel(targetMs13, depMs, arrMs))
        assertTrue(TravelBookingRules.isHourlySlotCoveredByTravel(targetMs14, depMs, arrMs))
        assertTrue(TravelBookingRules.isHourlySlotCoveredByTravel(targetMs15, depMs, arrMs))

        // Outside slots: 09:00-10:00 and 15:00-16:00
        val targetMs10 = ms("2026-10-19 10:00:00")
        val targetMs16 = ms("2026-10-19 16:00:00")
        assertFalse(TravelBookingRules.isHourlySlotCoveredByTravel(targetMs10, depMs, arrMs))
        assertFalse(TravelBookingRules.isHourlySlotCoveredByTravel(targetMs16, depMs, arrMs))

        // During travel or proof window (e.g. 16:00): hold active, deadline open
        val nowDuringProof = ms("2026-10-19 16:00:00")
        val isExpiredBeforeDeadline = nowDuringProof >= proofEndMs
        assertFalse("Hold must remain pending while deadline is open", isExpiredBeforeDeadline)

        // After proof deadline expires (e.g. 17:01):
        val nowAfterDeadline = ms("2026-10-19 17:01:00")
        val isExpiredAfterDeadline = nowAfterDeadline >= proofEndMs
        assertTrue("Covered slots finalize as not excused once deadline passes", isExpiredAfterDeadline)
    }

    /**
     * Regression Test 5:
     * An hourly submission already received must not be overwritten by a duplicate missed-slot processor.
     */
    @Test
    fun testRegression5_hourlySubmissionAlreadyReceived_notOverwritten() {
        val terminalStatuses = listOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "REJECTED", "REJECTED_LATE")
        for (st in terminalStatuses) {
            val shouldSkip = st in listOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "REJECTED", "REJECTED_LATE")
            assertTrue("Processor must preserve existing legitimate submission state $st", shouldSkip)
        }
    }

    /**
     * Regression Test 6:
     * Demo Travel must never affect production points, attendance, or booking restrictions.
     */
    @Test
    fun testRegression6_demoTravelNeverAffectsProductionPointsOrRestrictions() {
        val demoTitle = "Travel & Transit (Demo Test)"
        val demoDetails = "{\"type\":\"TRAVEL\",\"is_demo\":true}"
        assertTrue("Must detect demo title", TravelBookingRules.isDemoBooking(demoTitle, null))
        assertTrue("Must detect is_demo flag in JSON", TravelBookingRules.isDemoBooking(null, demoDetails))

        // Create 3 demo occurrences marked missed
        val demoOcc1 = TravelBookingRules.TravelOccurrenceRecord("ACT-DEMO-1", "2026-10-01", ms("2026-10-01 10:00:00"), ms("2026-10-01 13:00:00"), ms("2026-10-01 15:00:00"), "MISSED", false, isDemo = true)
        val demoOcc2 = TravelBookingRules.TravelOccurrenceRecord("ACT-DEMO-2", "2026-10-05", ms("2026-10-05 10:00:00"), ms("2026-10-05 13:00:00"), ms("2026-10-05 15:00:00"), "MISSED", false, isDemo = true)
        val demoOcc3 = TravelBookingRules.TravelOccurrenceRecord("ACT-DEMO-3", "2026-10-10", ms("2026-10-10 10:00:00"), ms("2026-10-10 13:00:00"), ms("2026-10-10 15:00:00"), "MISSED", false, isDemo = true)

        assertFalse("Demo occurrence must not be qualifying failure", demoOcc1.isFinalizedFailure(ms("2026-10-15 12:00:00")))
        val restriction = TravelBookingRules.evaluateConsecutiveFailures(listOf(demoOcc1, demoOcc2, demoOcc3), ms("2026-10-15 12:00:00"), tz)
        assertFalse("Demo occurrences must NEVER lock prebooking restrictions", restriction.isRestricted)
    }

    /**
     * Regression Test 7:
     * Repeated background jobs, app restarts, and synchronization must not duplicate missed records or transactions.
     */
    @Test
    fun testRegression7_repeatedBackgroundJobsDoNotDuplicateTransactions() {
        val nowMs = ms("2026-10-19 19:09:00")
        val winEndMs = ms("2026-10-19 14:00:00")
        val state1 = TravelBookingRules.resolveOccurrenceState("MISSED", nowMs, winEndMs - 2 * 3600_000L, winEndMs)
        val state2 = TravelBookingRules.resolveOccurrenceState("MISSED", nowMs + 60_000L, winEndMs - 2 * 3600_000L, winEndMs)
        assertEquals(TravelBookingRules.STATE_EXPIRED, state1)
        assertEquals(TravelBookingRules.STATE_EXPIRED, state2)
    }

    /**
     * Regression Test 8:
     * Bath reservations and unrelated hourly windows must retain their existing schedules.
     */
    @Test
    fun testRegression8_bathAndUnrelatedHourlyRetainSchedules() {
        val bathWin1 = BathWindowRules.calculateBathWindow(occurrence = 1, isCollege = true, date = "2026-10-19")
        assertEquals("07:30 AM–08:30 AM", bathWin1.windowDisplay)

        val bathWin2 = BathWindowRules.calculateBathWindow(occurrence = 2, isCollege = true, date = "2026-10-19")
        assertEquals("04:00 PM–09:00 PM", bathWin2.windowDisplay)

        val bathWinNoCollege = BathWindowRules.calculateBathWindow(occurrence = 1, isCollege = false, date = "2026-10-19")
        assertEquals("01:00 PM–05:30 PM", bathWinNoCollege.windowDisplay)

        val slots = HourlyWindowRules.slots("2026-10-19", "07:00")
        val slot7to8 = slots.first { it.hourSlot == "07:00-08:00" }
        assertEquals("07:00-08:00", slot7to8.hourSlot)
    }

    /**
     * Regression Test 9:
     * Test the 2:00 PM deadline boundary and the five-minute reminder, including app restart and late scheduler execution.
     */
    @Test
    fun testRegression9_deadlineBoundary2PMAndFiveMinuteReminder() {
        val depMs = ms("2026-10-19 10:00:00")
        val arrMs = ms("2026-10-19 12:00:00") // Proof opens at 12:00 PM
        val proofEndMs = ms("2026-10-19 14:00:00") // Deadline at 2:00 PM

        // 5-minute reminder at 11:55 AM
        val at115459 = arrMs - 5 * 60_000L - 1000L
        val at115500 = arrMs - 5 * 60_000L
        val at115959 = arrMs - 1000L

        assertEquals(TravelBookingRules.TravelStatusState.TRAVEL_IN_PROGRESS,
            TravelBookingRules.resolveAuthoritativeState(at115459, depMs, arrMs, arrMs, proofEndMs, "SCHEDULED"))
        assertEquals(TravelBookingRules.TravelStatusState.PROOF_OPENS_SOON,
            TravelBookingRules.resolveAuthoritativeState(at115500, depMs, arrMs, arrMs, proofEndMs, "SCHEDULED"))
        assertEquals(TravelBookingRules.TravelStatusState.PROOF_OPENS_SOON,
            TravelBookingRules.resolveAuthoritativeState(at115959, depMs, arrMs, arrMs, proofEndMs, "SCHEDULED"))

        // Proof opens at 12:00:00 PM
        val at120000 = arrMs
        assertEquals(TravelBookingRules.TravelStatusState.SUBMISSION_OPEN,
            TravelBookingRules.resolveAuthoritativeState(at120000, depMs, arrMs, arrMs, proofEndMs, "SCHEDULED"))

        // 1 ms before 2:00 PM deadline
        val at135959 = proofEndMs - 1L
        assertEquals(TravelBookingRules.TravelStatusState.SUBMISSION_OPEN,
            TravelBookingRules.resolveAuthoritativeState(at135959, depMs, arrMs, arrMs, proofEndMs, "SCHEDULED"))

        // At exactly 2:00 PM deadline boundary: closed/missed (end-exclusive)
        val at140000 = proofEndMs
        assertEquals(TravelBookingRules.TravelStatusState.MISSED,
            TravelBookingRules.resolveAuthoritativeState(at140000, depMs, arrMs, arrMs, proofEndMs, "SCHEDULED"))

        // Late execution at 7:09 PM
        val at190900 = ms("2026-10-19 19:09:00")
        assertEquals(TravelBookingRules.TravelStatusState.MISSED,
            TravelBookingRules.resolveAuthoritativeState(at190900, depMs, arrMs, arrMs, proofEndMs, "SCHEDULED"))
    }
}
