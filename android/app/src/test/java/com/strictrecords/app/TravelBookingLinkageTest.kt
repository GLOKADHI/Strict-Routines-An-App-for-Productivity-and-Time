package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/**
 * Deterministic Regression Tests for Section G:
 * Travel Prebooking-to-Occurrence Linkage and Audit Integrity.
 */
class TravelBookingLinkageTest {

    private val tz = TimeZone.getTimeZone("Asia/Kolkata")
    private val df = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).apply {
        timeZone = tz
        isLenient = false
    }

    private fun ms(dateTimeStr: String): Long = df.parse(dateTimeStr)!!.time

    /**
     * Requirement 1: A confirmed booking generating the correct travel occurrence.
     */
    @Test
    fun testReq1_confirmedBookingGeneratesCorrectTravelOccurrence() {
        val travelDate = "2026-10-17"
        val departureTime = "11:05"

        val actId = TravelBookingRules.generateActivityId(travelDate, departureTime)
        val bookingId = TravelBookingRules.generateBookingId(travelDate, departureTime)

        assertEquals("ACT-20261017-1105-TRAVEL", actId)
        assertEquals("TBK-20261017-1105", bookingId)
    }

    /**
     * Requirement 2: The occurrence retaining its booking reference.
     */
    @Test
    fun testReq2_occurrenceRetainsBookingReference() {
        // Modern booking with explicit booking_id
        val explicitRef = TravelBookingRules.formatBookingReference("TBK-20261017-1105", isPrebooked = true)
        assertEquals("TBK-20261017-1105", explicitRef)

        // Historical / legacy unlinked prebooking: must be reported honestly without fabricating an ID
        val legacyRef = TravelBookingRules.formatBookingReference(null, isPrebooked = true)
        assertEquals("Not recorded / unlinked (legacy prebooking)", legacyRef)

        val legacyBlankRef = TravelBookingRules.formatBookingReference("", isPrebooked = true)
        assertEquals("Not recorded / unlinked (legacy prebooking)", legacyBlankRef)

        // Non-prebooked occurrence
        val nonPrebookedRef = TravelBookingRules.formatBookingReference(null, isPrebooked = false)
        assertEquals("", nonPrebookedRef)
    }

    /**
     * Requirement 3: The correct trip interval and proof window.
     */
    @Test
    fun testReq3_correctTripIntervalAndProofWindow() {
        val travelDate = "2026-10-17"
        val departureTime = "11:05"
        val durationHours = 3

        val win = TravelBookingRules.calculateTravelWindow(travelDate, departureTime, durationHours, tz)
        assertNotNull(win)

        val depMs = ms("2026-10-17 11:05:00")
        val arrMs = ms("2026-10-17 14:05:00") // 11:05 + 3h = 14:05 (2:05 PM)
        val proofEndMs = ms("2026-10-17 16:05:00") // 14:05 + 2h = 16:05 (4:05 PM)
        val reminderMs = arrMs - 5 * 60_000L // 14:00 (2:00 PM)

        assertEquals(depMs, win!!.departureMs)
        assertEquals(arrMs, win.travelEndMs)
        assertEquals("14:05", win.travelEndTime)
        assertEquals(arrMs, win.windowStartMs) // Proof window opens on arrival
        assertEquals(proofEndMs, win.windowEndMs) // Proof window closes 2 hours later
        assertEquals(reminderMs, win.reminderMs) // 5-minute pre-opening reminder
    }

    /**
     * Requirement 4: A booking made the previous day appearing on the correct date.
     */
    @Test
    fun testReq4_bookingMadePreviousDayAppearingOnCorrectDate() {
        val travelDate = "2026-10-17"
        val departureTime = "11:05"
        val bookedOnMs = ms("2026-10-16 15:00:00") // Prebooked yesterday afternoon

        val eval = TravelBookingRules.evaluateBookingWindow(travelDate, departureTime, bookedOnMs, tz)
        assertTrue("Booking from previous day must be allowed within the valid window", eval.allowed)

        val win = TravelBookingRules.calculateTravelWindow(travelDate, departureTime, 3, tz)
        assertNotNull(win)
        assertEquals("2026-10-17", win!!.travelDate)
        assertEquals(ms("2026-10-17 11:05:00"), win.departureMs)
        assertEquals(ms("2026-10-17 14:05:00"), win.windowStartMs)
    }

    /**
     * Requirement 5: The proof action appearing during the eligible window.
     */
    @Test
    fun testReq5_proofActionAppearingDuringEligibleWindow() {
        val depMs = ms("2026-10-17 11:05:00")
        val arrMs = ms("2026-10-17 14:05:00") // Proof window opens at 2:05 PM
        val deadlineMs = ms("2026-10-17 16:05:00") // Proof window closes at 4:05 PM

        // 1. Before departure (10:30 AM): PREBOOKED -> card displays countdown / "⏳ PROOF OPENS AT 2:05 PM"
        val statePre = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-17 10:30:00"),
            departureMs = depMs, travelEndMs = arrMs, proofStartMs = arrMs, proofEndMs = deadlineMs,
            status = "SCHEDULED"
        )
        assertEquals(TravelBookingRules.TravelStatusState.PREBOOKED, statePre)

        // 2. During trip (12:30 PM): TRAVEL_IN_PROGRESS -> "⏳ PROOF OPENS AT 2:05 PM"
        val stateTransit = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-17 12:30:00"),
            departureMs = depMs, travelEndMs = arrMs, proofStartMs = arrMs, proofEndMs = deadlineMs,
            status = "SCHEDULED"
        )
        assertEquals(TravelBookingRules.TravelStatusState.TRAVEL_IN_PROGRESS, stateTransit)

        // 3. Arriving soon (2:01 PM): PROOF_OPENS_SOON -> "⏳ PROOF OPENS AT 2:05 PM"
        val stateSoon = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-17 14:01:00"),
            departureMs = depMs, travelEndMs = arrMs, proofStartMs = arrMs, proofEndMs = deadlineMs,
            status = "SCHEDULED"
        )
        assertEquals(TravelBookingRules.TravelStatusState.PROOF_OPENS_SOON, stateSoon)

        // 4. On arrival at 2:05 PM: SUBMISSION_OPEN -> primary button "SUBMIT TRAVEL PROOF" is visible and enabled
        val stateOpen = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-17 14:05:00"),
            departureMs = depMs, travelEndMs = arrMs, proofStartMs = arrMs, proofEndMs = deadlineMs,
            status = "SCHEDULED"
        )
        assertEquals(TravelBookingRules.TravelStatusState.SUBMISSION_OPEN, stateOpen)

        // 5. During window at 3:15 PM: SUBMISSION_OPEN
        val stateMid = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-17 15:15:00"),
            departureMs = depMs, travelEndMs = arrMs, proofStartMs = arrMs, proofEndMs = deadlineMs,
            status = "SCHEDULED"
        )
        assertEquals(TravelBookingRules.TravelStatusState.SUBMISSION_OPEN, stateMid)

        // 6. At deadline boundary 4:05:00 PM: MISSED
        val stateMissed = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-17 16:05:00"),
            departureMs = depMs, travelEndMs = arrMs, proofStartMs = arrMs, proofEndMs = deadlineMs,
            status = "SCHEDULED"
        )
        assertEquals(TravelBookingRules.TravelStatusState.MISSED, stateMissed)
    }

    /**
     * Requirement 6: A valid submission becoming pending review.
     */
    @Test
    fun testReq6_validSubmissionBecomesPendingReview() {
        val depMs = ms("2026-10-17 11:05:00")
        val arrMs = ms("2026-10-17 14:05:00")
        val deadlineMs = ms("2026-10-17 16:05:00")

        // Submitted during window at 2:30 PM
        val stateSubmitted = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-17 14:30:00"),
            departureMs = depMs, travelEndMs = arrMs, proofStartMs = arrMs, proofEndMs = deadlineMs,
            status = "SUBMITTED",
            hasProofSubmitted = true
        )
        assertEquals(TravelBookingRules.TravelStatusState.SUBMITTED_PENDING_REVIEW, stateSubmitted)

        // Submission payload verification
        val payload = TravelBookingRules.buildSubmissionPayload(
            submissionId = "sub_and_test12345",
            evidenceId = "ev_test12345",
            activityId = "ACT-20261017-1105-TRAVEL",
            bookingId = "TBK-20261017-1105",
            date = "2026-10-17",
            scheduleVersion = 1,
            departureTime = "11:05",
            durationHours = 3,
            description = "Diwali Shopping",
            previewBase64 = "base64data"
        )
        assertEquals("sub_and_test12345", payload.optString("submission_id"))
        assertEquals("ev_test12345", payload.optString("evidence_id"))
        assertEquals("ACT-20261017-1105-TRAVEL", payload.optString("activity_id"))
        assertEquals("TBK-20261017-1105", payload.optString("booking_id"))
        assertEquals("TRAVEL", payload.optString("task_type"))
        assertEquals("2026-10-17", payload.optString("date"))
        assertEquals(1, payload.optInt("schedule_version"))
        assertEquals("11:05 (3h)", payload.optString("hour_slot"))
        assertEquals("Diwali Shopping", payload.optString("description"))
    }

    /**
     * Requirement 7: A missed task synchronizing successfully without being mislabeled as an approved task.
     */
    @Test
    fun testReq7_missedTaskSynchronizesWithoutBeingMislabeledAsApproved() {
        val depMs = ms("2026-10-17 11:05:00")
        val arrMs = ms("2026-10-17 14:05:00")
        val deadlineMs = ms("2026-10-17 16:05:00")

        // 4:10 PM after deadline with no submission
        val stateMissed = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-17 16:10:00"),
            departureMs = depMs, travelEndMs = arrMs, proofStartMs = arrMs, proofEndMs = deadlineMs,
            status = "MISSED",
            hasProofSubmitted = false
        )
        assertEquals(TravelBookingRules.TravelStatusState.MISSED, stateMissed)

        // Synchronization confirmed for the missed occurrence
        val syncInfo = TravelBookingRules.resolveActivitySyncLabel(
            localExists = true,
            syncStatuses = listOf("SYNCED")
        )
        assertEquals("SYNCED", syncInfo.status)
        assertEquals("Synchronization confirmed", syncInfo.displayLabel)

        // Crucial: Task status remains MISSED; it is not mislabeled as APPROVED/VERIFIED
        assertNotEquals(TravelBookingRules.TravelStatusState.VERIFIED, stateMissed)
    }

    /**
     * Requirement 8: Missing schedule metadata being reported honestly.
     */
    @Test
    fun testReq8_missingScheduleMetadataReportedHonestly() {
        // Missing schedule version must be reported as "Not recorded"
        assertEquals("Not recorded", TravelBookingRules.formatScheduleVersion(0))
        assertEquals("Not recorded", TravelBookingRules.formatScheduleVersion(-1))
        assertEquals("1", TravelBookingRules.formatScheduleVersion(1))
        assertEquals("2", TravelBookingRules.formatScheduleVersion(2))

        // AI analysis when no submission was made must be "None (no submission recorded)", not "Not recorded locally"
        val noSubAnalysis = TravelBookingRules.formatAiAnalysisStatus(
            submittedAt = null,
            reviewStatus = null
        )
        assertEquals("None (no submission recorded)", noSubAnalysis)

        // When submitted but not reviewed yet
        val awaitingAnalysis = TravelBookingRules.formatAiAnalysisStatus(
            submittedAt = "2026-10-17T14:30:00",
            reviewStatus = "PENDING"
        )
        assertEquals("Awaiting evaluation", awaitingAnalysis)

        // When evaluated and verified
        val verifiedAnalysis = TravelBookingRules.formatAiAnalysisStatus(
            submittedAt = "2026-10-17T14:30:00",
            reviewStatus = "VERIFIED",
            reviewPoints = 10
        )
        assertEquals("Verified (+10 pts)", verifiedAnalysis)

        // When evaluated and rejected
        val rejectedAnalysis = TravelBookingRules.formatAiAnalysisStatus(
            submittedAt = "2026-10-17T14:30:00",
            reviewStatus = "REJECTED"
        )
        assertEquals("Rejected by evaluator", rejectedAnalysis)
    }

    /**
     * Requirement 9: Failed synchronization not being labeled SYNCED.
     */
    @Test
    fun testReq9_failedSynchronizationNotLabeledSynced() {
        // Failed event
        val failed = TravelBookingRules.resolveActivitySyncLabel(
            localExists = true,
            syncStatuses = listOf("FAILED")
        )
        assertEquals("SYNC_FAILED", failed.status)
        assertEquals("Synchronization failed", failed.displayLabel)
        assertFalse(failed.displayLabel.contains("confirmed", ignoreCase = true))

        // Pending event
        val pending = TravelBookingRules.resolveActivitySyncLabel(
            localExists = true,
            syncStatuses = listOf("PENDING")
        )
        assertEquals("AWAITING_SYNC", pending.status)
        assertEquals("Awaiting synchronization", pending.displayLabel)

        // Syncing event
        val syncing = TravelBookingRules.resolveActivitySyncLabel(
            localExists = true,
            syncStatuses = listOf("SYNCING")
        )
        assertEquals("SYNC_IN_PROGRESS", syncing.status)
        assertEquals("Synchronization in progress", syncing.displayLabel)

        // Partially synced (one synced, one failed)
        val partialFailed = TravelBookingRules.resolveActivitySyncLabel(
            localExists = true,
            syncStatuses = listOf("SYNCED", "FAILED")
        )
        assertEquals("PARTIALLY_SYNCED", partialFailed.status)
        assertEquals("Partially synchronized or missing linked records", partialFailed.displayLabel)

        // Partially synced (one synced, one pending)
        val partialPending = TravelBookingRules.resolveActivitySyncLabel(
            localExists = true,
            syncStatuses = listOf("SYNCED", "PENDING")
        )
        assertEquals("PARTIALLY_SYNCED", partialPending.status)
        assertEquals("Partially synchronized or missing linked records", partialPending.displayLabel)
    }

    /**
     * Requirement 10: Successful synchronization of the booking, occurrence, and associated submission records.
     */
    @Test
    fun testReq10_successfulSynchronizationOfBookingOccurrenceAndSubmission() {
        // When all sync events are confirmed
        val allSynced = TravelBookingRules.resolveActivitySyncLabel(
            localExists = true,
            syncStatuses = listOf("SYNCED", "SYNCED")
        )
        assertEquals("SYNCED", allSynced.status)
        assertEquals("Synchronization confirmed", allSynced.displayLabel)

        // When record exists locally and remote review confirms it
        val remoteConfirmed = TravelBookingRules.resolveActivitySyncLabel(
            localExists = true,
            syncStatuses = emptyList(),
            hasRemoteReview = true
        )
        assertEquals("SYNCED", remoteConfirmed.status)
        assertEquals("Synchronization confirmed", remoteConfirmed.displayLabel)
    }

    /**
     * Requirement 11: Prevention of duplicate occurrence generation.
     */
    @Test
    fun testReq11_preventionOfDuplicateOccurrenceGeneration() {
        val travelDate = "2026-10-17"
        val departureTime = "11:05"

        val actId1 = TravelBookingRules.generateActivityId(travelDate, departureTime)
        val actId2 = TravelBookingRules.generateActivityId(travelDate, departureTime)
        val bookingId1 = TravelBookingRules.generateBookingId(travelDate, departureTime)
        val bookingId2 = TravelBookingRules.generateBookingId(travelDate, departureTime)

        assertEquals("ACT-20261017-1105-TRAVEL", actId1)
        assertEquals(actId1, actId2)
        assertEquals("TBK-20261017-1105", bookingId1)
        assertEquals(bookingId1, bookingId2)
    }

    /**
     * Requirement 12: Preservation of hourly attendance exemptions and existing point rules.
     */
    @Test
    fun testReq12_preservationOfHourlyAttendanceExemptionsAndPointRules() {
        val depMs = ms("2026-10-17 11:05:00")
        val arrMs = ms("2026-10-17 14:05:00") // 3 hours

        // Hourly check slot 10:00-11:00 (target 11:00) -> before departure -> NOT covered
        val slot11 = ms("2026-10-17 11:00:00")
        assertFalse(TravelBookingRules.isHourlySlotCoveredByTravel(slot11, depMs, arrMs))

        // Hourly check slot 11:00-12:00 (target 12:00) -> activity 11:00-12:00 -> start (11:00) is before 11:05 -> NOT fully covered
        val slot12 = ms("2026-10-17 12:00:00")
        assertFalse(TravelBookingRules.isHourlySlotCoveredByTravel(slot12, depMs, arrMs))

        // Hourly check slot 12:00-13:00 (target 13:00) -> activity 12:00-13:00 -> within [11:05, 14:05] -> COVERED
        val slot13 = ms("2026-10-17 13:00:00")
        assertTrue(TravelBookingRules.isHourlySlotCoveredByTravel(slot13, depMs, arrMs))

        // Hourly check slot 13:00-14:00 (target 14:00) -> activity 13:00-14:00 -> within [11:05, 14:05] -> COVERED
        val slot14 = ms("2026-10-17 14:00:00")
        assertTrue(TravelBookingRules.isHourlySlotCoveredByTravel(slot14, depMs, arrMs))

        // Hourly check slot 14:00-15:00 (target 15:00) -> activity 14:00-15:00 -> target (15:00) > 14:05 -> NOT covered
        val slot15 = ms("2026-10-17 15:00:00")
        assertFalse(TravelBookingRules.isHourlySlotCoveredByTravel(slot15, depMs, arrMs))

        // Verify points for missed travel proof are 0
        val recordMissed = TravelBookingRules.TravelOccurrenceRecord(
            activityId = "ACT-20261017-1105-TRAVEL",
            date = "2026-10-17",
            departureMs = depMs,
            travelEndMs = arrMs,
            windowEndMs = ms("2026-10-17 16:05:00"),
            status = "MISSED",
            proofSubmitted = false
        )
        assertTrue(recordMissed.isFinalizedFailure(ms("2026-10-17 16:10:00")))
    }
}
