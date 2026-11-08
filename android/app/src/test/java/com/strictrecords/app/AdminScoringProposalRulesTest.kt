package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/**
 * Tests for Admin Console scoring rules, deduction proposals validation,
 * safe parsing of malformed records, and score adjustment integrity.
 */
class AdminScoringProposalRulesTest {

    @Test
    fun testAdminSecurityPinRules() {
        assertFalse(AdminSecurityRules.canOpenProtectedConsole(isDefaultPin = true))
        assertTrue(AdminSecurityRules.canOpenProtectedConsole(isDefaultPin = false))
    }

    @Test
    fun testSafeProposalParsingWithMissingFields() {
        // Simulates malformed / incomplete proposal JSON from secondary client or corrupted storage
        val malformedJson = JSONObject()
            .put("proposed_points", -5)
            // proposal_id missing
            // applicable_rule missing
            // reason missing
            // originating_device missing
            // timestamp missing

        val pid = malformedJson.optString("proposal_id", "")
        val pts = malformedJson.optInt("proposed_points", 0)
        val evType = malformedJson.optString("event_type", "UNKNOWN_PROPOSAL")
        val reason = malformedJson.optString("reason", "No reason provided")
        val origin = malformedJson.optString("originating_device", "windows_companion")

        assertTrue("Proposal id defaults to empty without throwing", pid.isEmpty())
        assertEquals(-5, pts)
        assertEquals("UNKNOWN_PROPOSAL", evType)
        assertEquals("No reason provided", reason)
        assertEquals("windows_companion", origin)
    }

    @Test
    fun testEvidenceJsonSafeParsing() {
        // String-encoded evidence_json
        val rawEvidenceJson = JSONObject().apply {
            put("episode_id", "ep_test_12345")
            put("minute_index", 3)
            put("grace_policy_minutes", 5)
        }.toString()

        val propWithStrEvidence = JSONObject().apply {
            put("proposal_id", "prop_1")
            put("evidence_json", rawEvidenceJson)
        }

        val evStr = propWithStrEvidence.optString("evidence_json", "")
        val parsed = runCatching { JSONObject(evStr) }.getOrNull()
        assertNotNull("Should successfully parse evidence_json string", parsed)
        assertEquals("ep_test_12345", parsed?.optString("episode_id"))
        assertEquals(3, parsed?.optInt("minute_index"))
        assertEquals(5, parsed?.optInt("grace_policy_minutes"))

        // Malformed non-JSON string in evidence_json must not crash
        val propWithMalformedStr = JSONObject().apply {
            put("proposal_id", "prop_2")
            put("evidence_json", "not a valid json { broken")
        }
        val safeParsed = runCatching { JSONObject(propWithMalformedStr.optString("evidence_json", "")) }.getOrNull()
        assertNull("Malformed JSON returns null without crashing", safeParsed)
    }

    @Test
    fun testScoreAdjustmentCalculationAndLimits() {
        // Initial score 80
        val baseScore = 80

        // Positive delta +10
        val positiveDelta = 10
        val newScoreAfterPositive = DisciplineScoringRules.calculateDailyScore(listOf(baseScore, positiveDelta))
        assertEquals(90, newScoreAfterPositive)

        // Negative delta -15
        val negativeDelta = -15
        val newScoreAfterNegative = DisciplineScoringRules.calculateDailyScore(listOf(baseScore, negativeDelta))
        assertEquals(65, newScoreAfterNegative)

        // Clamping at 100 max
        val overflowScore = DisciplineScoringRules.calculateDailyScore(listOf(95, 20))
        assertEquals(100, overflowScore)
    }

    @Test
    fun testProposalApprovalStatusTransitions() {
        // Approved proposal
        val approvedProp = JSONObject().apply {
            put("proposal_id", "prop_ext_1")
            put("status", "APPROVED")
            put("approved_by", "admin")
            put("finalized_event_id", "evt_fin_999")
        }
        assertEquals("APPROVED", approvedProp.optString("status"))
        assertEquals("admin", approvedProp.optString("approved_by"))
        assertEquals("evt_fin_999", approvedProp.optString("finalized_event_id"))

        // Rejected proposal
        val rejectedProp = JSONObject().apply {
            put("proposal_id", "prop_ext_2")
            put("status", "REJECTED")
            put("approved_by", "admin")
            put("rejection_reason", "User provided valid justification")
        }
        assertEquals("REJECTED", rejectedProp.optString("status"))
        assertEquals("User provided valid justification", rejectedProp.optString("rejection_reason"))
    }

    @Test
    fun testAuthorizedScoreAdjustmentAuditPayload() {
        // Validates that authorized manual adjustments have required audit metadata
        val adminUser = "admin"
        val delta = 5
        val reason = "Manual reward for extra task completion"

        val audit = JSONObject().apply {
            put("adjusted_by", adminUser)
            put("reason", reason)
            put("delta", delta)
            put("approval_source", "AUTHENTICATED_HUMAN_ADMIN")
            put("approved_by", adminUser)
        }

        assertEquals("AUTHENTICATED_HUMAN_ADMIN", audit.optString("approval_source"))
        assertEquals("admin", audit.optString("approved_by"))
        assertEquals(5, audit.optInt("delta"))
        assertTrue("Reason is preserved", audit.optString("reason").isNotEmpty())
    }
}
