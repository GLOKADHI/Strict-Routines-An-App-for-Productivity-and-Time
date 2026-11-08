package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AIReviewProgressAndBathLockTest {

    // ── 1. AI Review Progress Stages and Monotonic Percentages ──

    @Test
    fun testPipelineStagesMonotonicPercentage() {
        val stages = (1..8).toList()
        var lastPercent = 0
        for (stage in stages) {
            val percent = ProofSubmissionRules.PipelineStages.getPercent(stage)
            assertTrue("Stage $stage percent ($percent) must be greater than last ($lastPercent)", percent > lastPercent)
            assertTrue("Stage $stage percent ($percent) must be in 1..100", percent in 1..100)
            lastPercent = percent
        }
        assertEquals(12, ProofSubmissionRules.PipelineStages.getPercent(ProofSubmissionRules.PipelineStages.STAGE_1_RECEIVED))
        assertEquals(25, ProofSubmissionRules.PipelineStages.getPercent(ProofSubmissionRules.PipelineStages.STAGE_2_QUEUED))
        assertEquals(38, ProofSubmissionRules.PipelineStages.getPercent(ProofSubmissionRules.PipelineStages.STAGE_3_INPUT_VALIDATION))
        assertEquals(50, ProofSubmissionRules.PipelineStages.getPercent(ProofSubmissionRules.PipelineStages.STAGE_4_EVIDENCE_EXTRACTION))
        assertEquals(63, ProofSubmissionRules.PipelineStages.getPercent(ProofSubmissionRules.PipelineStages.STAGE_5_AI_ANALYSIS))
        assertEquals(75, ProofSubmissionRules.PipelineStages.getPercent(ProofSubmissionRules.PipelineStages.STAGE_6_MATCHING_CHECKS))
        assertEquals(88, ProofSubmissionRules.PipelineStages.getPercent(ProofSubmissionRules.PipelineStages.STAGE_7_PERSISTENCE))
        assertEquals(100, ProofSubmissionRules.PipelineStages.getPercent(ProofSubmissionRules.PipelineStages.STAGE_8_COMPLETE))
    }

    @Test
    fun testPipelineStageNames() {
        assertEquals("Submission received", ProofSubmissionRules.PipelineStages.getStageName(1))
        assertEquals("Image uploaded", ProofSubmissionRules.PipelineStages.getStageName(2))
        assertEquals("Original evidence preserved", ProofSubmissionRules.PipelineStages.getStageName(3))
        assertEquals("Local evidence checks", ProofSubmissionRules.PipelineStages.getStageName(4))
        assertEquals("Taufa request dispatched", ProofSubmissionRules.PipelineStages.getStageName(5))
        assertEquals("Taufa response received", ProofSubmissionRules.PipelineStages.getStageName(6))
        assertEquals("Criteria evaluated", ProofSubmissionRules.PipelineStages.getStageName(7))
        assertEquals("Final decision recorded", ProofSubmissionRules.PipelineStages.getStageName(8))
    }

    @Test
    fun testStatusLabelsCompleteness() {
        assertEquals("Submitted", ProofSubmissionRules.StatusLabels.SUBMITTED)
        assertEquals("Queued", ProofSubmissionRules.StatusLabels.QUEUED)
        assertEquals("Processing", ProofSubmissionRules.StatusLabels.PROCESSING)
        assertEquals("AI analysis complete", ProofSubmissionRules.StatusLabels.AI_ANALYSIS_COMPLETE)
        assertEquals("Awaiting admin/human review", ProofSubmissionRules.StatusLabels.AWAITING_ADMIN_REVIEW)
        assertEquals("Approved — full points", ProofSubmissionRules.StatusLabels.APPROVED_FULL_POINTS)
        assertEquals("Approved — partial points", ProofSubmissionRules.StatusLabels.APPROVED_PARTIAL_POINTS)
        assertEquals("Rejected", ProofSubmissionRules.StatusLabels.REJECTED)
        assertEquals("Failed — retry available", ProofSubmissionRules.StatusLabels.FAILED_RETRY_AVAILABLE)
    }

    // ── 2. Hours Points: Full vs Partial vs Rejection ──

    @Test
    fun testTaufaFullPointsApproval() {
        val result = TaufaAiValidator.TaufaValidationResult(
            submissionId = "sub_full_123",
            score = 8,
            maxScore = 8,
            categoryMatch = true,
            descriptionMatch = true,
            evidenceMatch = true,
            evidenceQuality = "HIGH",
            reason = "Category and evidence match perfectly.",
            validatorStatus = "SUCCESS",
            reviewStatus = "APPROVED"
        )
        assertTrue(result.isValid)
        assertTrue(result.qualifiesForPoints)
        assertTrue(result.isFullPoints)
        assertFalse(result.isPartialPoints)
        assertEquals(8, result.score)
    }

    @Test
    fun testTaufaPartialPointsApproval() {
        val result = TaufaAiValidator.TaufaValidationResult(
            submissionId = "sub_partial_123",
            score = 5,
            maxScore = 8,
            categoryMatch = true,
            descriptionMatch = true,
            evidenceMatch = true,
            evidenceQuality = "MEDIUM",
            reason = "Evidence supports category with moderate detail.",
            validatorStatus = "SUCCESS",
            reviewStatus = "APPROVED_PARTIAL"
        )
        assertTrue(result.isValid)
        assertTrue(result.qualifiesForPoints)
        assertFalse(result.isFullPoints)
        assertTrue(result.isPartialPoints)
        assertEquals(5, result.score)
    }

    @Test
    fun testTaufaRejectionZeroPoints() {
        val result = TaufaAiValidator.TaufaValidationResult(
            submissionId = "sub_rej_123",
            score = 0,
            maxScore = 8,
            categoryMatch = false,
            descriptionMatch = false,
            evidenceMatch = false,
            evidenceQuality = "INSUFFICIENT",
            reason = "Submitted image is blank or irrelevant.",
            validatorStatus = "REJECTED",
            reviewStatus = "REJECTED"
        )
        assertTrue(result.isValid)
        assertFalse(result.qualifiesForPoints)
        assertFalse(result.isFullPoints)
        assertFalse(result.isPartialPoints)
        assertEquals(0, result.score)
    }

    @Test
    fun testTaufaParseAndValidateResponseFullScore() {
        val json = """
            {
                "submission_id": "sub_test_888",
                "score": 8,
                "max_score": 8,
                "category_match": true,
                "description_match": true,
                "evidence_match": true,
                "evidence_quality": "HIGH",
                "status": "SUCCESS",
                "reason": "Clear evidence of study session"
            }
        """.trimIndent()
        val parsed = TaufaAiValidator.parseAndValidateResponse(
            expectedSubmissionId = "sub_test_888",
            responseJson = json,
            category = "STUDY"
        )
        assertTrue(parsed.isValid)
        assertTrue(parsed.qualifiesForPoints)
        assertTrue(parsed.isFullPoints)
        assertFalse(parsed.isPartialPoints)
        assertEquals(8, parsed.score)
        assertEquals("SUCCESS", parsed.validatorStatus)
        assertEquals("APPROVED", parsed.reviewStatus)
    }

    @Test
    fun testTaufaParseAndValidateResponsePartialScore() {
        val json = """
            {
                "submission_id": "sub_test_555",
                "score": 5,
                "max_score": 8,
                "category_match": true,
                "description_match": true,
                "evidence_match": true,
                "evidence_quality": "MEDIUM",
                "status": "SUCCESS",
                "reason": "Partial evidence of study activity"
            }
        """.trimIndent()
        val parsed = TaufaAiValidator.parseAndValidateResponse(
            expectedSubmissionId = "sub_test_555",
            responseJson = json,
            category = "STUDY"
        )
        assertTrue(parsed.isValid)
        assertTrue(parsed.qualifiesForPoints)
        assertFalse(parsed.isFullPoints)
        assertTrue(parsed.isPartialPoints)
        assertEquals(5, parsed.score)
        assertEquals("SUCCESS", parsed.validatorStatus)
        assertEquals("APPROVED_PARTIAL", parsed.reviewStatus)
    }

    @Test
    fun testTaufaParseAndValidateCategoryMismatchFailsClosed() {
        val json = """
            {
                "submission_id": "sub_test_mismatch",
                "score": 8,
                "max_score": 8,
                "category_match": false,
                "description_match": true,
                "evidence_match": false,
                "evidence_quality": "HIGH",
                "status": "SUCCESS",
                "reason": "Submitted game screen does not match study"
            }
        """.trimIndent()
        val parsed = TaufaAiValidator.parseAndValidateResponse(
            expectedSubmissionId = "sub_test_mismatch",
            responseJson = json,
            category = "STUDY"
        )
        assertTrue(parsed.isValid)
        assertFalse(parsed.qualifiesForPoints)
        assertEquals(0, parsed.score)
        assertEquals("REJECTED", parsed.validatorStatus)
        assertEquals("REJECTED", parsed.reviewStatus)
        assertTrue(parsed.warnings.contains("CATEGORY_MISMATCH"))
    }

    @Test
    fun testEvaluateLocalPolicyFullPointsAcademic() {
        val images = listOf(ByteArray(100) { 1 })
        val res = TaufaAiValidator.evaluateLocalPolicy(
            submissionId = "sub_local_full",
            category = "STUDY",
            description = "Completed calculus chapter 4 notes and assignment",
            images = images
        )
        assertTrue(res.isValid)
        assertTrue(res.qualifiesForPoints)
        assertTrue(res.isFullPoints)
        assertEquals(8, res.score)
        assertEquals("SUCCESS", res.validatorStatus)
        assertEquals("APPROVED", res.reviewStatus)
    }

    @Test
    fun testEvaluateLocalPolicyPartialPoints() {
        val images = listOf(ByteArray(100) { 1 })
        val res = TaufaAiValidator.evaluateLocalPolicy(
            submissionId = "sub_local_partial",
            category = "STUDY",
            description = "Quick study revision",
            images = images
        )
        assertTrue(res.isValid)
        assertTrue(res.qualifiesForPoints)
        assertTrue(res.isPartialPoints)
        assertEquals(5, res.score)
        assertEquals("SUCCESS", res.validatorStatus)
        assertEquals("APPROVED_PARTIAL", res.reviewStatus)
    }

    @Test
    fun testEvaluateLocalPolicyConflictingKeywordsFailsClosed() {
        val images = listOf(ByteArray(100) { 1 })
        val res = TaufaAiValidator.evaluateLocalPolicy(
            submissionId = "sub_local_conflict",
            category = "STUDY",
            description = "Studying Minecraft gaming strategies",
            images = images
        )
        assertTrue(res.isValid)
        assertFalse(res.qualifiesForPoints)
        assertEquals(0, res.score)
        assertEquals("REJECTED", res.validatorStatus)
        assertEquals("REJECTED", res.reviewStatus)
        assertTrue(res.warnings.contains("CATEGORY_MISMATCH"))
    }

    @Test
    fun testEvaluateLocalPolicyUnsupportedCategoryFailsClosed() {
        val images = listOf(ByteArray(100) { 1 })
        val res = TaufaAiValidator.evaluateLocalPolicy(
            submissionId = "sub_local_unsupported",
            category = "GAMING",
            description = "Playing high tier ranked games",
            images = images
        )
        assertTrue(res.isValid)
        assertFalse(res.qualifiesForPoints)
        assertEquals(0, res.score)
        assertEquals("REJECTED", res.validatorStatus)
        assertEquals("REJECTED", res.reviewStatus)
        assertTrue(res.warnings.contains("UNSUPPORTED_HOURLY_CATEGORY"))
    }
}
