package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ProofApprovalReceiptDataTest {
    private fun receipt(type: String = "PROOF_APPROVED", audit: JSONObject = JSONObject(), stored: Boolean = true) =
        ProofApprovalReceiptData(JSONObject().put("event_type", type).put("points", 7).put("audit", audit),
            null, null, stored, false, "Points Posting Pending", "Asia/Kolkata")

    @Test fun approvalDoesNotImplyCreditOrInventAValidationScore() {
        val result = receipt()
        assertTrue(result.approved)
        assertFalse(result.credited)
        assertNull(result.ai)
        assertEquals(7, result.event.getInt("points"))
    }
    @Test fun rejectedPendingAndMissingEventsAreNotApproved() {
        assertFalse(receipt("PROOF_REJECTED").approved)
        assertFalse(receipt("PROOF_SUBMITTED").approved)
        assertFalse(receipt(stored = false).approved)
    }
    @Test fun validationValuesAndLongIdentifiersRemainUnchanged() {
        val id = "sub_" + "a".repeat(500)
        val ai = JSONObject().put("score", 3).put("category_match", false).put("validator_status", "FAILED")
        val result = receipt(audit = JSONObject().put("submission_id", id).put("taufa_ai", ai))
        assertEquals(id, result.value("submission_id"))
        assertFalse(result.ai!!.getBoolean("category_match"))
        assertFalse(result.ai!!.has("max_score"))
        assertEquals("FAILED", result.ai!!.getString("validator_status"))
    }
    @Test fun provenanceConflictIsFlaggedWithoutRewritingTheAudit() {
        val audit = JSONObject().put("action", "AI_APPROVAL").put("reviewed_by", "Taufa_AI")
            .put("approval_source", "AUTHENTICATED_HUMAN_ADMIN").put("Admin", "Taufa_AI")
        val before = audit.toString()
        assertTrue(receipt(audit = audit).provenanceWarning)
        assertEquals(before, audit.toString())
        audit.put("Admin", "Named human administrator")
        assertFalse(receipt(audit = audit).provenanceWarning)
    }
}
