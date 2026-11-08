package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class TaufaRetryMappingTest {
    @Test fun transientAndUnconfiguredResultsRequestRetryWithoutHumanReview() {
        for (status in listOf("UNCONFIGURED", "FAILED_RETRYABLE", "PROCESSING_ERROR", "UNKNOWN")) {
            for (mode in listOf("DEMO", "PRODUCTION")) {
                val result = TaufaAiValidator.TaufaValidationResult(
                    submissionId = "retry-test", validatorStatus = status
                ).toStructuredEvaluation(mode, "study")
                assertEquals("PROCESSING_ERROR", result.status)
                assertEquals("retry", result.recommendedAction)
                assertFalse(result.requiresHumanReview)
            }
        }
    }

    @Test fun physicalPrerequisitesKeepHumanReviewAfterSuccessfulAssessment() {
        for (task in listOf("bath", "gym", "wake_up")) {
            val result = TaufaAiValidator.TaufaValidationResult(
                submissionId = "physical-test", score = 7, categoryMatch = true,
                descriptionMatch = true, evidenceMatch = true, evidenceQuality = "HIGH",
                validatorStatus = "SUCCESS", reviewStatus = "APPROVED"
            ).toStructuredEvaluation("PRODUCTION", task)
            assertEquals("VALID", result.status)
            assertEquals("review", result.recommendedAction)
            assertTrue(result.requiresHumanReview)
        }
    }

    @Test fun rejectedEvidenceRemainsRejected() {
        val result = TaufaAiValidator.TaufaValidationResult(
            submissionId = "invalid-test", score = 0, validatorStatus = "FAILED_FINAL"
        ).toStructuredEvaluation("PRODUCTION", "study")
        assertEquals("INVALID", result.status)
        assertEquals("reject", result.recommendedAction)
        assertFalse(result.requiresHumanReview)
    }
}
