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
 * Unit tests for Emergency Travel Booking, Approval Workflow,
 * Dynamic Travel Slot Modification, and Schedule Activation.
 */
class EmergencyTravelRulesTest {

    private val tz = "Asia/Kolkata"
    private val timeZoneObj = TimeZone.getTimeZone(tz)
    private val df = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
        timeZone = timeZoneObj
        isLenient = false
    }

    private fun ms(dateTimeStr: String): Long = df.parse(dateTimeStr)!!.time

    // ── 1. Advance Booking Shortly Before Departure ──

    @Test
    fun testAdvanceBookingShortlyBeforeDeparture_isAllowed() {
        // Example from requirements:
        // Request submitted at 5:40 PM (17:40) for planned departure at 6:00 PM (18:00)
        val nowMs = ms("2026-11-03 17:40")
        val travelDate = "2026-11-03"
        val departureTime = "18:00"
        val returnTime = "20:00"
        val purpose = "Urgent client meeting across town"
        val travelType = "CAR_RIDE"

        val result = EmergencyTravelRules.validateRequestInput(
            travelDate = travelDate,
            departureTime = departureTime,
            returnTime = returnTime,
            purpose = purpose,
            travelType = travelType,
            nowMs = nowMs,
            timezone = tz
        )

        assertTrue(result.valid)
        assertEquals(ms("2026-11-03 18:00"), result.departureMs)
        assertEquals(ms("2026-11-03 20:00"), result.returnMs)
        assertEquals(120, result.durationMinutes)
    }

    @Test
    fun testPastDeparture_isRejected() {
        // Request submitted at 6:10 PM for 6:00 PM departure (> 5 mins past)
        val nowMs = ms("2026-11-03 18:10")
        val result = EmergencyTravelRules.validateRequestInput(
            travelDate = "2026-11-03",
            departureTime = "18:00",
            returnTime = "20:00",
            purpose = "Travel",
            travelType = "CAR_RIDE",
            nowMs = nowMs,
            timezone = tz
        )
        assertFalse(result.valid)
        assertTrue(result.message.contains("past"))
    }

    @Test
    fun testReturnBeforeDeparture_isRejected() {
        val nowMs = ms("2026-11-03 17:00")
        val result = EmergencyTravelRules.validateRequestInput(
            travelDate = "2026-11-03",
            departureTime = "18:00",
            returnTime = "17:30",
            purpose = "Invalid times",
            travelType = "CAR_RIDE",
            nowMs = nowMs,
            timezone = tz
        )
        assertFalse(result.valid)
        assertTrue(result.message.contains("strictly after"))
    }

    @Test
    fun testTooShortOrEmptyPurpose_isRejected() {
        val nowMs = ms("2026-11-03 17:00")
        val result = EmergencyTravelRules.validateRequestInput(
            travelDate = "2026-11-03",
            departureTime = "18:00",
            returnTime = "19:00",
            purpose = "  ",
            travelType = "CAR_RIDE",
            nowMs = nowMs,
            timezone = tz
        )
        assertFalse(result.valid)
        assertTrue(result.message.contains("purpose is required"))
    }

    @Test
    fun testDurationLimits() {
        val nowMs = ms("2026-11-03 17:00")
        // < 15 min
        val tooShort = EmergencyTravelRules.validateRequestInput(
            travelDate = "2026-11-03",
            departureTime = "18:00",
            returnTime = "18:10",
            purpose = "Quick errand",
            travelType = "CAR_RIDE",
            nowMs = nowMs,
            timezone = tz
        )
        assertFalse(tooShort.valid)
        assertTrue(tooShort.message.contains("Minimum travel duration"))

        // > 12 hours
        val tooLong = EmergencyTravelRules.validateRequestInput(
            travelDate = "2026-11-03",
            departureTime = "06:00",
            returnTime = "23:00", // 17 hours
            purpose = "Long trip",
            travelType = "CAR_RIDE",
            nowMs = ms("2026-11-03 05:00"),
            timezone = tz
        )
        assertFalse(tooLong.valid)
        assertTrue(tooLong.message.contains("Maximum travel duration"))
    }

    // ── 2. Approval Workflow ──

    @Test
    fun testApprovalWorkflow_adminApprovalAndRejection() {
        val baseRequest = EmergencyTravelRules.EmergencyTravelRequest(
            requestId = "ETR-TEST-001",
            travelDate = "2026-11-03",
            departureTime = "18:00",
            departureMs = ms("2026-11-03 18:00"),
            returnTime = "20:00",
            returnMs = ms("2026-11-03 20:00"),
            durationMinutes = 120,
            purpose = "Emergency travel",
            status = EmergencyTravelRules.Status.AWAITING_APPROVAL
        )

        // Admin approves
        val approved = EmergencyTravelRules.evaluateApproval(
            request = baseRequest,
            adminApproved = true,
            adminReason = "Approved by admin lok",
            actor = "admin"
        )
        assertEquals(EmergencyTravelRules.Status.APPROVED, approved.status)
        assertEquals("admin", approved.reviewedBy)
        assertEquals("Approved by admin lok", approved.approvalReason)
        assertTrue(approved.auditHistory.any { it.action == "APPROVAL_GRANTED" })

        // Admin rejects
        val rejected = EmergencyTravelRules.evaluateApproval(
            request = baseRequest,
            adminApproved = false,
            adminReason = "No genuine emergency shown",
            actor = "admin"
        )
        assertEquals(EmergencyTravelRules.Status.REJECTED, rejected.status)
        assertEquals("admin", rejected.reviewedBy)
        assertEquals("No genuine emergency shown", rejected.approvalReason)
        assertTrue(rejected.auditHistory.any { it.action == "APPROVAL_REJECTED" })
    }

    @Test
    fun testUnapprovedRequest_cannotBypassRestrictions() {
        val unapprovedReq = EmergencyTravelRules.EmergencyTravelRequest(
            requestId = "ETR-UNAPPROVED",
            travelDate = "2026-11-03",
            departureTime = "18:00",
            departureMs = ms("2026-11-03 18:00"),
            returnTime = "20:00",
            returnMs = ms("2026-11-03 20:00"),
            durationMinutes = 120,
            purpose = "Unapproved trip",
            status = EmergencyTravelRules.Status.AWAITING_APPROVAL
        )

        // At 18:00, cannot activate because status is not APPROVED
        val (canAct, reason) = EmergencyTravelRules.canActivate(
            nowMs = ms("2026-11-03 18:00"),
            request = unapprovedReq,
            travelHeartsRemaining = 3
        )
        assertFalse(canAct)
        assertTrue(reason.contains("not approved"))
    }

    // ── 3. Dynamic Travel Slot Modification ──

    @Test
    fun testDynamicTravelSlotModification_materialChangeRequiresReapproval() {
        // Original: 6:00 PM - 8:00 PM (120 min), already approved
        val originalReq = EmergencyTravelRules.EmergencyTravelRequest(
            requestId = "ETR-MOD-001",
            travelDate = "2026-11-03",
            departureTime = "18:00",
            departureMs = ms("2026-11-03 18:00"),
            returnTime = "20:00",
            returnMs = ms("2026-11-03 20:00"),
            durationMinutes = 120,
            purpose = "Client visit",
            status = EmergencyTravelRules.Status.APPROVED
        )

        // User shifts departure to 6:30 PM (30 min shift -> material!) and return to 8:30 PM
        val nowMs = ms("2026-11-03 17:45")
        val (modified, msg) = EmergencyTravelRules.modifyTravelSlot(
            existing = originalReq,
            newDepartureTime = "18:30",
            newReturnTime = "20:30",
            reason = "Meeting delayed by client",
            nowMs = nowMs,
            timezone = tz,
            existingRequests = emptyList()
        )

        assertNotNull(modified)
        assertEquals("18:30", modified!!.departureTime)
        assertEquals("20:30", modified.returnTime)
        assertTrue(modified.isMateriallyModified)
        // Material change resets status to AWAITING_APPROVAL
        assertEquals(EmergencyTravelRules.Status.AWAITING_APPROVAL, modified.status)
        assertTrue(msg.contains("renewed administrator approval"))
        assertTrue(modified.auditHistory.any { it.action == "MATERIAL_SLOT_MODIFICATION" })
    }

    @Test
    fun testDynamicTravelSlotModification_minorShiftPreservesApproval() {
        // Original: 6:00 PM - 8:00 PM (120 min), approved
        val originalReq = EmergencyTravelRules.EmergencyTravelRequest(
            requestId = "ETR-MOD-002",
            travelDate = "2026-11-03",
            departureTime = "18:00",
            departureMs = ms("2026-11-03 18:00"),
            returnTime = "20:00",
            returnMs = ms("2026-11-03 20:00"),
            durationMinutes = 120,
            purpose = "Doctor appointment",
            status = EmergencyTravelRules.Status.APPROVED
        )

        // Minor adjustment: 5 min shift (18:05 to 20:05, <= 15m shift, same duration)
        val nowMs = ms("2026-11-03 17:50")
        val (modified, msg) = EmergencyTravelRules.modifyTravelSlot(
            existing = originalReq,
            newDepartureTime = "18:05",
            newReturnTime = "20:05",
            reason = "Traffic slight delay",
            nowMs = nowMs,
            timezone = tz,
            existingRequests = emptyList()
        )

        assertNotNull(modified)
        assertFalse(modified!!.isMateriallyModified)
        assertEquals(EmergencyTravelRules.Status.APPROVED, modified.status)
        assertTrue(msg.contains("updated successfully"))
    }

    @Test
    fun testConflictPrevention() {
        val existing = EmergencyTravelRules.EmergencyTravelRequest(
            requestId = "ETR-EXISTING",
            travelDate = "2026-11-03",
            departureTime = "18:00",
            departureMs = ms("2026-11-03 18:00"),
            returnTime = "20:00",
            returnMs = ms("2026-11-03 20:00"),
            durationMinutes = 120,
            purpose = "First trip",
            status = EmergencyTravelRules.Status.APPROVED
        )

        // Attempt overlapping slot: 19:00 - 21:00
        val (conflict, reason) = EmergencyTravelRules.hasConflict(
            departureMs = ms("2026-11-03 19:00"),
            returnMs = ms("2026-11-03 21:00"),
            existingRequests = listOf(existing)
        )
        assertTrue(conflict)
        assertTrue(reason.contains("Conflicting travel slot exists"))

        // Non-overlapping slot: 20:30 - 22:00
        val (nonConflict, _) = EmergencyTravelRules.hasConflict(
            departureMs = ms("2026-11-03 20:30"),
            returnMs = ms("2026-11-03 22:00"),
            existingRequests = listOf(existing)
        )
        assertFalse(nonConflict)
    }

    // ── 4. Activation, Expiry and Heart Lockout ──

    @Test
    fun testActivationWindow_5MinutesBeforeDeparture() {
        val approvedReq = EmergencyTravelRules.EmergencyTravelRequest(
            requestId = "ETR-ACT-001",
            travelDate = "2026-11-03",
            departureTime = "18:00",
            departureMs = ms("2026-11-03 18:00"),
            returnTime = "20:00",
            returnMs = ms("2026-11-03 20:00"),
            durationMinutes = 120,
            purpose = "Office travel",
            status = EmergencyTravelRules.Status.APPROVED
        )

        // At 17:54 (6 min before departure): not yet open
        val (tooEarly, msgEarly) = EmergencyTravelRules.canActivate(
            nowMs = ms("2026-11-03 17:54"),
            request = approvedReq,
            travelHeartsRemaining = 3
        )
        assertFalse(tooEarly)
        assertTrue(msgEarly.contains("5 minutes before"))

        // At 17:55 (5 min before departure): opens
        val (canAct5m, _) = EmergencyTravelRules.canActivate(
            nowMs = ms("2026-11-03 17:55"),
            request = approvedReq,
            travelHeartsRemaining = 3
        )
        assertTrue(canAct5m)

        // During travel at 19:00: can activate
        val (canActMid, _) = EmergencyTravelRules.canActivate(
            nowMs = ms("2026-11-03 19:00"),
            request = approvedReq,
            travelHeartsRemaining = 3
        )
        assertTrue(canActMid)

        // At 20:00 (return time reached): expired
        val (expired, msgExpired) = EmergencyTravelRules.canActivate(
            nowMs = ms("2026-11-03 20:00"),
            request = approvedReq,
            travelHeartsRemaining = 3
        )
        assertFalse(expired)
        assertTrue(msgExpired.contains("expired"))
    }

    @Test
    fun testZeroHearts_locksTravelActivation() {
        val approvedReq = EmergencyTravelRules.EmergencyTravelRequest(
            requestId = "ETR-LOCKED",
            travelDate = "2026-11-03",
            departureTime = "18:00",
            departureMs = ms("2026-11-03 18:00"),
            returnTime = "20:00",
            returnMs = ms("2026-11-03 20:00"),
            durationMinutes = 120,
            purpose = "Trip",
            status = EmergencyTravelRules.Status.APPROVED
        )

        // Even during valid travel time, 0 hearts blocks activation
        val (canAct, reason) = EmergencyTravelRules.canActivate(
            nowMs = ms("2026-11-03 18:30"),
            request = approvedReq,
            travelHeartsRemaining = 0
        )
        assertFalse(canAct)
        assertTrue(reason.contains("Zero hearts remaining"))
    }

    @Test
    fun testStateResolutionLifecycle() {
        val baseReq = EmergencyTravelRules.EmergencyTravelRequest(
            requestId = "ETR-LIFECYCLE",
            travelDate = "2026-11-03",
            departureTime = "18:00",
            departureMs = ms("2026-11-03 18:00"),
            returnTime = "20:00",
            returnMs = ms("2026-11-03 20:00"),
            durationMinutes = 120,
            purpose = "Life cycle",
            status = EmergencyTravelRules.Status.APPROVED,
            evidenceDeadlineMs = ms("2026-11-03 22:00") // 2h after return
        )

        // Before departure: status remains APPROVED
        val st1 = EmergencyTravelRules.resolveState(ms("2026-11-03 17:50"), baseReq)
        assertEquals(EmergencyTravelRules.Status.APPROVED, st1.status)

        // At departure: transitions to ACTIVE
        val st2 = EmergencyTravelRules.resolveState(ms("2026-11-03 18:00"), baseReq)
        assertEquals(EmergencyTravelRules.Status.ACTIVE, st2.status)

        // At return time: transitions from ACTIVE to AWAITING_EVIDENCE
        val st3 = EmergencyTravelRules.resolveState(ms("2026-11-03 20:01"), st2)
        assertEquals(EmergencyTravelRules.Status.AWAITING_EVIDENCE, st3.status)

        // Past evidence deadline: transitions to EXPIRED
        val st4 = EmergencyTravelRules.resolveState(ms("2026-11-03 22:05"), st3)
        assertEquals(EmergencyTravelRules.Status.EXPIRED, st4.status)
    }

    @Test
    fun testSerializationRoundtrip() {
        val original = EmergencyTravelRules.EmergencyTravelRequest(
            requestId = "ETR-JSON-TEST",
            travelDate = "2026-11-03",
            departureTime = "18:00",
            departureMs = ms("2026-11-03 18:00"),
            returnTime = "20:00",
            returnMs = ms("2026-11-03 20:00"),
            durationMinutes = 120,
            travelType = "BUS",
            purpose = "Bus ticket to city center",
            pickupLocation = "Station A",
            destinationLocation = "Terminal B",
            status = EmergencyTravelRules.Status.APPROVED,
            approvalReason = "Verified bus pass",
            reviewedBy = "admin",
            reviewedAtMs = 123456789L,
            evidenceDeadlineMs = ms("2026-11-03 22:00"),
            auditHistory = listOf(
                EmergencyTravelRules.AuditEntry(1, "REQUEST_CREATED", "Created", 123450000L, "user"),
                EmergencyTravelRules.AuditEntry(2, "APPROVAL_GRANTED", "Verified", 123456789L, "admin")
            )
        )

        val json = original.toJson()
        val restored = EmergencyTravelRules.EmergencyTravelRequest.fromJson(json)

        assertEquals(original.requestId, restored.requestId)
        assertEquals(original.travelDate, restored.travelDate)
        assertEquals(original.departureTime, restored.departureTime)
        assertEquals(original.returnTime, restored.returnTime)
        assertEquals(original.travelType, restored.travelType)
        assertEquals(original.pickupLocation, restored.pickupLocation)
        assertEquals(original.destinationLocation, restored.destinationLocation)
        assertEquals(original.status, restored.status)
        assertEquals(original.approvalReason, restored.approvalReason)
        assertEquals(2, restored.auditHistory.size)
        assertEquals("APPROVAL_GRANTED", restored.auditHistory[1].action)
    }
}
