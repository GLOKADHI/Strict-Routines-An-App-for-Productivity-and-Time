package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProofSubmissionRulesTest {

    @Test
    fun testValidHourlySubmissionInput() {
        val bytes = ByteArray(1024) { 1 }
        val res = ProofSubmissionRules.validateSubmissionInput(
            taskType = "HOURLY",
            hasPhoto = true,
            fileBytes = bytes,
            fileName = "screenshot.jpg",
            category = "CODING",
            timingAllowed = true
        )
        assertTrue(res.valid)
        assertEquals("Validation passed.", res.message)
    }

    @Test
    fun testHourlyMissingCategoryRejected() {
        val bytes = ByteArray(1024) { 1 }
        val res = ProofSubmissionRules.validateSubmissionInput(
            taskType = "HOURLY",
            hasPhoto = true,
            fileBytes = bytes,
            fileName = "screenshot.jpg",
            category = "",
            timingAllowed = true
        )
        assertFalse(res.valid)
        assertEquals("CATEGORY_REQUIRED", res.errorCode)
    }

    @Test
    fun testGymMissingPhotoRejected() {
        val res = ProofSubmissionRules.validateSubmissionInput(
            taskType = "GYM",
            hasPhoto = false,
            fileBytes = null,
            fileName = "",
            category = "GYM",
            timingAllowed = true
        )
        assertFalse(res.valid)
        assertEquals("ATTACHMENT_REQUIRED", res.errorCode)
        assertTrue(res.message.contains("photo proof is mandatory for Gym", ignoreCase = true))
    }

    @Test
    fun testBathMissingPhotoRejected() {
        val res = ProofSubmissionRules.validateSubmissionInput(
            taskType = "BATH",
            hasPhoto = false,
            fileBytes = ByteArray(0),
            fileName = "",
            category = "BATH",
            timingAllowed = true
        )
        assertFalse(res.valid)
        assertEquals("ATTACHMENT_REQUIRED", res.errorCode)
    }

    @Test
    fun testSleepMissingPhotoRejected() {
        val res = ProofSubmissionRules.validateSubmissionInput(
            taskType = "SLEEP",
            hasPhoto = false,
            fileBytes = null,
            fileName = "",
            category = "SLEEP",
            timingAllowed = true
        )
        assertFalse(res.valid)
        assertEquals("ATTACHMENT_REQUIRED", res.errorCode)
    }

    @Test
    fun testWindowClosedTimingRejection() {
        val bytes = ByteArray(1024) { 1 }
        val res = ProofSubmissionRules.validateSubmissionInput(
            taskType = "HOURLY",
            hasPhoto = true,
            fileBytes = bytes,
            fileName = "screenshot.png",
            category = "READING",
            timingAllowed = false,
            timingMessage = "Submission window closed at 11:00 AM"
        )
        assertFalse(res.valid)
        assertEquals("WINDOW_CLOSED", res.errorCode)
        assertEquals("Submission window closed at 11:00 AM", res.message)
    }

    @Test
    fun testFileExceeding25MbRejected() {
        val largeBytes = ByteArray((25 * 1024 * 1024) + 1)
        val res = ProofSubmissionRules.validateSubmissionInput(
            taskType = "HOURLY",
            hasPhoto = true,
            fileBytes = largeBytes,
            fileName = "huge.jpg",
            category = "CODING",
            timingAllowed = true
        )
        assertFalse(res.valid)
        assertEquals("FILE_TOO_LARGE", res.errorCode)
    }

    @Test
    fun testSupportedAndUnsupportedExtensions() {
        val bytes = ByteArray(500)
        // Supported: jpg, jpeg, png, webp
        assertTrue(ProofSubmissionRules.validateSubmissionInput("HOURLY", true, bytes, "proof.jpg", "CODING").valid)
        assertTrue(ProofSubmissionRules.validateSubmissionInput("HOURLY", true, bytes, "proof.jpeg", "CODING").valid)
        assertTrue(ProofSubmissionRules.validateSubmissionInput("HOURLY", true, bytes, "proof.png", "CODING").valid)
        assertTrue(ProofSubmissionRules.validateSubmissionInput("HOURLY", true, bytes, "proof.webp", "CODING").valid)

        // Unsupported: pdf, txt, exe
        val unsupportedPdf = ProofSubmissionRules.validateSubmissionInput("HOURLY", true, bytes, "proof.pdf", "CODING")
        assertFalse(unsupportedPdf.valid)
        assertEquals("UNSUPPORTED_FILE", unsupportedPdf.errorCode)

        val unsupportedTxt = ProofSubmissionRules.validateSubmissionInput("HOURLY", true, bytes, "proof.txt", "CODING")
        assertFalse(unsupportedTxt.valid)
        assertEquals("UNSUPPORTED_FILE", unsupportedTxt.errorCode)
    }

    @Test
    fun testDeduplicationInProgress() {
        assertTrue(ProofSubmissionRules.isDuplicateInProgress(ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION))
        assertTrue(ProofSubmissionRules.isDuplicateInProgress(ProofSubmissionRules.States.EVALUATING))
        assertTrue(ProofSubmissionRules.isDuplicateInProgress("VALIDATING"))
        assertFalse(ProofSubmissionRules.isDuplicateInProgress(ProofSubmissionRules.States.APPROVED))
        assertFalse(ProofSubmissionRules.isDuplicateInProgress(ProofSubmissionRules.States.REJECTED))
        assertFalse(ProofSubmissionRules.isDuplicateInProgress(ProofSubmissionRules.States.FINALIZED))
        assertFalse(ProofSubmissionRules.isDuplicateInProgress(null))
    }

    @Test
    fun testStateTransitions() {
        val states = ProofSubmissionRules.States

        // Happy path
        assertTrue(ProofSubmissionRules.isValidTransition(states.SUBMITTED_PENDING_EVALUATION, states.EVALUATING))
        assertTrue(ProofSubmissionRules.isValidTransition(states.EVALUATING, states.APPROVED))
        assertTrue(ProofSubmissionRules.isValidTransition(states.APPROVED, states.FINALIZED))

        // Rejection path
        assertTrue(ProofSubmissionRules.isValidTransition(states.EVALUATING, states.REJECTED))
        assertTrue(ProofSubmissionRules.isValidTransition(states.REJECTED, states.FINALIZED))

        // Retryable failure path
        assertTrue(ProofSubmissionRules.isValidTransition(states.EVALUATING, states.RETRYABLE_FAILURE))
        assertTrue(ProofSubmissionRules.isValidTransition(states.RETRYABLE_FAILURE, states.EVALUATING))

        // Invalid transitions
        assertFalse(ProofSubmissionRules.isValidTransition(states.SUBMITTED_PENDING_EVALUATION, states.FINALIZED))
        assertFalse(ProofSubmissionRules.isValidTransition(states.FINALIZED, states.EVALUATING))
    }

    @Test
    fun testPointsCannotBeAwardedWhilePendingOrEvaluating() {
        assertFalse(ProofSubmissionRules.canAwardPoints(ProofSubmissionRules.States.PREPARING_SUBMISSION))
        assertFalse(ProofSubmissionRules.canAwardPoints(ProofSubmissionRules.States.SAVED_LOCALLY_PENDING_UPLOAD))
        assertFalse(ProofSubmissionRules.canAwardPoints(ProofSubmissionRules.States.UPLOAD_IN_PROGRESS))
        assertFalse(ProofSubmissionRules.canAwardPoints(ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION))
        assertFalse(ProofSubmissionRules.canAwardPoints(ProofSubmissionRules.States.EVALUATING))
        assertFalse(ProofSubmissionRules.canAwardPoints(ProofSubmissionRules.States.REJECTED))
        assertFalse(ProofSubmissionRules.canAwardPoints(ProofSubmissionRules.States.RETRYABLE_FAILURE))

        assertTrue(ProofSubmissionRules.canAwardPoints(ProofSubmissionRules.States.APPROVED))
        assertTrue(ProofSubmissionRules.canAwardPoints(ProofSubmissionRules.States.FINALIZED))
    }

    @Test
    fun testUserFacingAcceptanceMessages() {
        assertEquals(
            "Proof submitted successfully — evaluation pending.",
            ProofSubmissionRules.acceptanceMessage("HOURLY")
        )
        assertEquals(
            "Gym proof submitted — workout analysis pending.",
            ProofSubmissionRules.acceptanceMessage("GYM")
        )
        assertEquals(
            "Proof submitted successfully — evaluation pending.",
            ProofSubmissionRules.acceptanceMessage("BATH")
        )
        assertEquals(
            "Sleep report submitted — verification pending.",
            ProofSubmissionRules.acceptanceMessage("SLEEP")
        )
    }
}
