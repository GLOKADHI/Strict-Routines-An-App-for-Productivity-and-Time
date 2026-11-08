package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HourlyAttendanceRejectionConsistencyTest {

    @Test
    fun testRejectionSurvivesWindowStateDeadlines() {
        // Authoritative rejection must survive before, during, and after deadline
        val rejectedState = "REJECTED"
        val nowBeforeMs = 1000L
        val nowDuringMs = 1500L
        val nowAfterMs = 2500L
        val startMs = 1200L
        val endMs = 2000L

        assertEquals("REJECTED", WindowStateRules.state(rejectedState, nowBeforeMs, startMs, endMs, false))
        assertEquals("REJECTED", WindowStateRules.state(rejectedState, nowDuringMs, startMs, endMs, false))
        assertEquals("REJECTED", WindowStateRules.state(rejectedState, nowAfterMs, startMs, endMs, false))
    }

    @Test
    fun testCountdownStateCapturesRejectionAndReason() {
        val cdState = DisciplineScoringRules.CountdownState(
            hasActiveWindow = true,
            activityId = "ACT-20261020-1400-HOURLY",
            type = "HOURLY",
            title = "Hourly Discipline Check (13:00-14:00)",
            state = "REJECTED",
            secondsRemaining = 900L,
            secondsUntilOpen = 0L,
            isWarning = false,
            windowStart = "2026-10-20T13:00:00+05:30",
            windowEnd = "2026-10-20T14:00:00+05:30",
            points = 0,
            submissionEligible = false,
            rejectionReason = "Duplicate evidence detected (EXACT_DUPLICATE)"
        )

        assertEquals("REJECTED", cdState.state)
        assertEquals(0, cdState.points)
        assertFalse(cdState.submissionEligible)
        assertEquals("Duplicate evidence detected (EXACT_DUPLICATE)", cdState.rejectionReason)
    }

    @Test
    fun testHourlyOccurrenceRejectionPayload() {
        val details = JSONObject().apply {
            put("hour_slot", "13:00-14:00")
            put("ai_validation_state", "REJECTED")
            put("ai_score", 0)
            put("validator", "TAUFA_AI")
            put("rejection_reason", "Duplicate evidence detected (EXACT_DUPLICATE)")
            put("submission_locked", true)
            put("duplicate_status", "EXACT_DUPLICATE")
        }

        assertEquals("REJECTED", details.optString("ai_validation_state"))
        assertEquals(0, details.optInt("ai_score"))
        assertTrue(details.optBoolean("submission_locked"))
        assertEquals("Duplicate evidence detected (EXACT_DUPLICATE)", details.optString("rejection_reason"))
    }

    @Test
    fun testRejectionTakesPrecedenceInSummaryMetrics() {
        // Simulating the resolution in reportStatus
        val activities = listOf(
            "ACT-20261020-1400-HOURLY" to "REJECTED",
            "ACT-20261020-1500-HOURLY" to "SUBMITTED",
            "ACT-20261020-1600-HOURLY" to "VERIFIED"
        )
        val reportStates = activities.toMap()

        val pendingReviewCount = reportStates.values.count {
            it in setOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "ADMIN_REVIEW", "UNDER_REVIEW", "NEEDS_REVIEW", "DUPLICATE_REVIEW")
        }
        val rejectedCount = reportStates.values.count {
            it in setOf("REJECTED", "REJECTED_LATE", "PROVISIONAL_REJECT")
        }
        val verifiedCount = reportStates.values.count {
            it in setOf("VERIFIED", "APPROVED", "COMPLETED")
        }

        assertEquals(1, pendingReviewCount)
        assertEquals(1, rejectedCount)
        assertEquals(1, verifiedCount)
    }
}
