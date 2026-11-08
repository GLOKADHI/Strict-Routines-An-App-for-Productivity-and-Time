package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Deterministic unit tests for the Three-Heart System,
 * Independent Balance Decoupling, Single-Deduction Guarantee,
 * and Shared Evidence Validator.
 */
class ThreeHeartAccountabilityTest {

    // ── 1. Independent Balance Decoupling ──

    @Test
    fun testIndependentBalances_travelAndGymAreStrictlyDecoupled() {
        var travelHearts = ThreeHeartAccountabilityEngine.INITIAL_HEARTS
        var gymHearts = ThreeHeartAccountabilityEngine.INITIAL_HEARTS

        assertEquals(3, travelHearts)
        assertEquals(3, gymHearts)

        // Deduct 1 Travel heart (due to missed travel proof)
        val travelEval = ThreeHeartAccountabilityEngine.evaluateDeduction(
            domain = ThreeHeartAccountabilityEngine.Domain.TRAVEL,
            activityId = "ETR-20261103-001",
            currentHearts = travelHearts,
            isDeadlineExpiredWithoutSubmission = true,
            isFakeOrManipulated = false,
            alreadyDeductedActivityIds = emptySet(),
            alreadyFulfilledActivityIds = emptySet()
        )
        assertTrue(travelEval.shouldDeduct)
        travelHearts = travelEval.newHearts

        // Travel hearts became 2; Gym hearts MUST remain exactly 3
        assertEquals(2, travelHearts)
        assertEquals(3, gymHearts)

        // Deduct 2 Gym hearts (two separate gym missed proofs)
        val gymEval1 = ThreeHeartAccountabilityEngine.evaluateDeduction(
            domain = ThreeHeartAccountabilityEngine.Domain.GYM,
            activityId = "GYM-ACT-001",
            currentHearts = gymHearts,
            isDeadlineExpiredWithoutSubmission = true,
            isFakeOrManipulated = false,
            alreadyDeductedActivityIds = emptySet(),
            alreadyFulfilledActivityIds = emptySet()
        )
        assertTrue(gymEval1.shouldDeduct)
        gymHearts = gymEval1.newHearts

        val gymEval2 = ThreeHeartAccountabilityEngine.evaluateDeduction(
            domain = ThreeHeartAccountabilityEngine.Domain.GYM,
            activityId = "GYM-ACT-002",
            currentHearts = gymHearts,
            isDeadlineExpiredWithoutSubmission = true,
            isFakeOrManipulated = false,
            alreadyDeductedActivityIds = setOf("GYM-ACT-001"),
            alreadyFulfilledActivityIds = emptySet()
        )
        assertTrue(gymEval2.shouldDeduct)
        gymHearts = gymEval2.newHearts

        // Gym hearts became 1; Travel hearts MUST remain unaffected at 2
        assertEquals(1, gymHearts)
        assertEquals(2, travelHearts)
    }

    // ── 2. Visual Badge Formatting ──

    @Test
    fun testFormatHeartsBadge() {
        val b3 = ThreeHeartAccountabilityEngine.formatHeartsBadge(3, "TRAVEL")
        assertTrue(b3.contains("♥ ♥ ♥"))
        assertTrue(b3.contains("3/3 hearts remaining"))

        val b2 = ThreeHeartAccountabilityEngine.formatHeartsBadge(2, "TRAVEL")
        assertTrue(b2.contains("♥ ♥ ♡"))
        assertTrue(b2.contains("2/3 hearts remaining"))

        val b1 = ThreeHeartAccountabilityEngine.formatHeartsBadge(1, "GYM")
        assertTrue(b1.contains("♥ ♡ ♡"))
        assertTrue(b1.contains("1/3 hearts remaining"))

        val b0 = ThreeHeartAccountabilityEngine.formatHeartsBadge(0, "GYM")
        assertTrue(b0.contains("♡ ♡ ♡"))
        assertTrue(b0.contains("0/3 hearts remaining"))
    }

    // ── 3. Single-Deduction Guarantee ──

    @Test
    fun testSingleDeductionGuarantee_neverDeductsTwiceForSameActivity() {
        val activityId = "ETR-20261103-999"

        // First deduction: deadline expired
        val firstEval = ThreeHeartAccountabilityEngine.evaluateDeduction(
            domain = ThreeHeartAccountabilityEngine.Domain.TRAVEL,
            activityId = activityId,
            currentHearts = 3,
            isDeadlineExpiredWithoutSubmission = true,
            isFakeOrManipulated = false,
            alreadyDeductedActivityIds = emptySet(),
            alreadyFulfilledActivityIds = emptySet()
        )
        assertTrue(firstEval.shouldDeduct)
        assertEquals(2, firstEval.newHearts)
        assertEquals("DEDUCT_DEADLINE_EXPIRED", firstEval.decision)

        // Subsequent check with activityId now in alreadyDeducted set
        val secondEval = ThreeHeartAccountabilityEngine.evaluateDeduction(
            domain = ThreeHeartAccountabilityEngine.Domain.TRAVEL,
            activityId = activityId,
            currentHearts = 2,
            isDeadlineExpiredWithoutSubmission = true,
            isFakeOrManipulated = true, // even if also fake!
            alreadyDeductedActivityIds = setOf(activityId),
            alreadyFulfilledActivityIds = emptySet()
        )
        assertFalse("Second deduction must be blocked by single-deduction guarantee", secondEval.shouldDeduct)
        assertEquals(2, secondEval.newHearts)
        assertEquals("IGNORED_DUPLICATE_DEDUCTION", secondEval.decision)
        assertEquals("ALREADY_DEDUCTED", secondEval.errorCode)
    }

    // ── 4. Valid Submission Preserves Hearts ──

    @Test
    fun testFulfilledEvidence_blocksFutureDeductions() {
        val activityId = "GYM-20261103-WORKOUT"

        // If evidence was already fulfilled, deadline expiration later must NOT deduct
        val eval = ThreeHeartAccountabilityEngine.evaluateDeduction(
            domain = ThreeHeartAccountabilityEngine.Domain.GYM,
            activityId = activityId,
            currentHearts = 3,
            isDeadlineExpiredWithoutSubmission = true,
            isFakeOrManipulated = false,
            alreadyDeductedActivityIds = emptySet(),
            alreadyFulfilledActivityIds = setOf(activityId)
        )
        assertFalse(eval.shouldDeduct)
        assertEquals(3, eval.newHearts)
        assertEquals("IGNORED_ALREADY_FULFILLED", eval.decision)
    }

    // ── 5. Technical Errors Never Deduct Hearts ──

    @Test
    fun testTechnicalOrPendingReview_neverDeductsHearts() {
        val eval = ThreeHeartAccountabilityEngine.evaluateDeduction(
            domain = ThreeHeartAccountabilityEngine.Domain.TRAVEL,
            activityId = "ETR-PENDING",
            currentHearts = 3,
            isDeadlineExpiredWithoutSubmission = false, // Not expired
            isFakeOrManipulated = false, // Not fake
            alreadyDeductedActivityIds = emptySet(),
            alreadyFulfilledActivityIds = emptySet()
        )
        assertFalse("Pending or non-failing state should not deduct", eval.shouldDeduct)
        assertEquals(3, eval.newHearts)
        assertEquals("NO_DEDUCTION", eval.decision)
    }

    // ── 6. Heart Exhaustion Lockout ──

    @Test
    fun testHeartExhaustion_locksDomain() {
        val eval = ThreeHeartAccountabilityEngine.evaluateDeduction(
            domain = ThreeHeartAccountabilityEngine.Domain.TRAVEL,
            activityId = "ETR-LOCKED",
            currentHearts = 0,
            isDeadlineExpiredWithoutSubmission = true,
            isFakeOrManipulated = false,
            alreadyDeductedActivityIds = emptySet(),
            alreadyFulfilledActivityIds = emptySet()
        )
        assertFalse(eval.shouldDeduct)
        assertEquals(0, eval.newHearts)
        assertTrue(eval.reason.contains("already exhausted"))
    }

    // ── 7. Shared Evidence Validator Tests ──

    @Test
    fun testSharedEvidenceValidator_travelReceiptVerification() {
        val nowMs = System.currentTimeMillis()
        val validOcr = "Uber Technologies Inc.\nTrip total: ₹245\nDrop off completed at 7:30 PM\nDriver rating 4.9"
        val sampleImage = ByteArray(1024) { 1 }

        val result = SharedEvidenceValidator.validateTravelEvidence(
            activityId = "ETR-UBER-001",
            departureMs = nowMs - 7200_000L,
            returnMs = nowMs - 3600_000L,
            deadlineMs = nowMs + 3600_000L,
            nowMs = nowMs,
            photoBytes = sampleImage,
            fileName = "uber_receipt.jpg",
            ocrText = validOcr,
            userDescription = "Cab ride back from work",
            knownHashesFromOtherActivities = emptySet()
        )

        assertTrue("Valid Uber receipt should be accepted", result.isGenuine)
        assertEquals("VERIFIED", result.status)
        assertFalse(result.deductHeart)
        assertEquals("PRESERVE", result.heartAction)
        assertNotNull(result.sha256Hash)
    }

    @Test
    fun testSharedEvidenceValidator_gymWorkoutVerification() {
        val nowMs = System.currentTimeMillis()
        val validOcr = "Mi Fitness - Strength Training\nDuration: 48 min\nActive calories: 310 kcal\nAvg Heart Rate: 132 bpm"
        val sampleImage = ByteArray(1024) { 2 }

        val result = SharedEvidenceValidator.validateGymEvidence(
            activityId = "GYM-ACT-MI-FIT",
            scheduledStartMs = nowMs - 7200_000L,
            scheduledEndMs = nowMs - 3600_000L,
            deadlineMs = nowMs + 3600_000L,
            nowMs = nowMs,
            photoBytes = sampleImage,
            fileName = "gym_workout.jpg",
            ocrText = validOcr,
            userDescription = "Chest and triceps workout",
            knownHashesFromOtherActivities = emptySet()
        )

        assertTrue("Valid Gym proof should be accepted", result.isGenuine)
        assertEquals("VERIFIED", result.status)
        assertFalse(result.deductHeart)
        assertEquals("PRESERVE", result.heartAction)
    }

    @Test
    fun testSharedEvidenceValidator_reusedImageDeductsHeart() {
        val nowMs = System.currentTimeMillis()
        val sampleImage = ByteArray(1024) { 3 }
        val imageHash = SharedEvidenceValidator.computeSha256(sampleImage)

        // Simulate hash already used in a prior submission
        val result = SharedEvidenceValidator.validateTravelEvidence(
            activityId = "ETR-REUSE-001",
            departureMs = nowMs - 7200_000L,
            returnMs = nowMs - 3600_000L,
            deadlineMs = nowMs + 3600_000L,
            nowMs = nowMs,
            photoBytes = sampleImage,
            fileName = "reused.jpg",
            ocrText = "Uber trip receipt ₹200",
            userDescription = "Ride",
            knownHashesFromOtherActivities = setOf(imageHash)
        )

        assertFalse("Reused image must not be genuine", result.isGenuine)
        assertTrue(result.isDuplicate)
        assertTrue(result.isManipulated)
        assertTrue(result.deductHeart)
        assertEquals("FRAUDULENT", result.status)
        assertEquals("DEDUCT_FRAUD", result.heartAction)
    }

    @Test
    fun testSharedEvidenceValidator_technicalErrorNeverDeductsHearts() {
        val nowMs = System.currentTimeMillis()
        val sampleImage = ByteArray(1024) { 4 }

        val result = SharedEvidenceValidator.validateTravelEvidence(
            activityId = "ETR-NETWORK-ERR",
            departureMs = nowMs - 7200_000L,
            returnMs = nowMs - 3600_000L,
            deadlineMs = nowMs + 3600_000L,
            nowMs = nowMs,
            photoBytes = sampleImage,
            fileName = "network_fail.jpg",
            ocrText = "",
            userDescription = "",
            isNetworkOrTechnicalError = true
        )

        assertFalse("Technical error is not verified", result.isGenuine)
        assertFalse("Technical error must never deduct hearts", result.deductHeart)
        assertEquals("RETRYABLE_ERROR", result.status)
        assertEquals("ALLOW_CORRECTION", result.heartAction)
    }

    // ── 8. Ledger Serialization Roundtrip ──

    @Test
    fun testLedgerSerialization() {
        val entry = ThreeHeartAccountabilityEngine.HeartLedgerEntry(
            ledgerId = "LED-001",
            domain = ThreeHeartAccountabilityEngine.Domain.TRAVEL,
            activityId = "ETR-20261103-001",
            evidenceId = "EVD-001",
            action = ThreeHeartAccountabilityEngine.Action.DEDUCTION,
            previousHearts = 3,
            newHearts = 2,
            heartsChanged = -1,
            reason = "Evidence deadline expired without submission",
            decision = "DEDUCT_ONE_HEART",
            timestampMs = 1730640000000L
        )

        val json = entry.toJson()
        val restored = ThreeHeartAccountabilityEngine.HeartLedgerEntry.fromJson(json)

        assertEquals(entry.ledgerId, restored.ledgerId)
        assertEquals(entry.domain, restored.domain)
        assertEquals(entry.activityId, restored.activityId)
        assertEquals(entry.evidenceId, restored.evidenceId)
        assertEquals(entry.action, restored.action)
        assertEquals(entry.previousHearts, restored.previousHearts)
        assertEquals(entry.newHearts, restored.newHearts)
        assertEquals(entry.heartsChanged, restored.heartsChanged)
        assertEquals(entry.reason, restored.reason)
        assertEquals(entry.decision, restored.decision)
        assertEquals(entry.timestampMs, restored.timestampMs)
    }
}
