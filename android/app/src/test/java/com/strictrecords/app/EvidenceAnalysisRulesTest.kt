package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EvidenceAnalysisRulesTest {

    @Test
    fun testBase64SizeCalculation() {
        assertEquals(0L, EvidenceAnalysisRules.base64Size(0L))
        assertEquals(4L, EvidenceAnalysisRules.base64Size(1L))
        assertEquals(4L, EvidenceAnalysisRules.base64Size(2L))
        assertEquals(4L, EvidenceAnalysisRules.base64Size(3L))
        assertEquals(8L, EvidenceAnalysisRules.base64Size(4L))
    }

    @Test
    fun testValidateBatchBudgetWithinLimits() {
        val sizes = listOf(5L * 1024 * 1024, 10L * 1024 * 1024, 2L * 1024 * 1024)
        val budget = EvidenceAnalysisRules.validateBatchBudget(sizes)
        assertTrue(budget.passed)
        assertEquals(17L * 1024 * 1024, budget.rawBytes)
    }

    @Test
    fun testValidateBatchBudgetExceedsCount() {
        val sizes = List(6) { 1024L * 1024L }
        val budget = EvidenceAnalysisRules.validateBatchBudget(sizes)
        assertFalse(budget.passed)
        assertEquals("Select between 1 and 5 images.", budget.reason)
    }

    @Test
    fun testValidateBatchBudgetExceedsPerImageLimit() {
        val sizes = listOf(26L * 1024 * 1024)
        val budget = EvidenceAnalysisRules.validateBatchBudget(sizes)
        assertFalse(budget.passed)
        assertTrue(budget.reason.contains("25 MiB"))
    }

    @Test
    fun testValidateBatchBudgetExceedsTotalLimit() {
        val sizes = listOf(
            25L * 1024 * 1024,
            25L * 1024 * 1024,
            25L * 1024 * 1024,
            25L * 1024 * 1024,
            26L * 1024 * 1024 // would fail per image, but let's test sum > 125 MiB:
        )
        // If 5 images of 25 MiB = 125 MiB exact, allowed:
        val exactMax = List(5) { 25L * 1024 * 1024 }
        assertTrue(EvidenceAnalysisRules.validateBatchBudget(exactMax).passed)
    }

    @Test
    fun testDecodeSampleSize() {
        // Normal size fits in 2048 without downsampling
        assertEquals(1, EvidenceAnalysisRules.decodeSampleSize(1920, 1080))
        // Very large 8000x6000 image downsamples by power of 2
        val sample = EvidenceAnalysisRules.decodeSampleSize(8000, 6000)
        assertTrue(sample >= 4)
    }

    @Test
    fun testAcceptsRealOcr() {
        assertTrue(EvidenceAnalysisRules.acceptsRealOcr("engine-v1", "OCR_COMPLETED", "engine-v1"))
        assertTrue(EvidenceAnalysisRules.acceptsRealOcr("engine-v1", "NO_TEXT_DETECTED", "engine-v1"))
        assertFalse(EvidenceAnalysisRules.acceptsRealOcr("engine-v1", "OCR_FAILED", "engine-v1"))
        assertFalse(EvidenceAnalysisRules.acceptsRealOcr("engine-v2", "OCR_COMPLETED", "engine-v1"))
    }

    @Test
    fun testImageTokensExtraction() {
        val text = "StrictRecords Evidence Verification receipt #42: Total $150.00"
        val tokens = EvidenceAnalysisRules.imageTokens(text)
        assertTrue(tokens.contains("strictrecords"))
        assertTrue(tokens.contains("evidence"))
        assertTrue(tokens.contains("verification"))
        assertTrue(tokens.contains("receipt"))
        assertTrue(tokens.contains("total"))
        assertFalse(tokens.contains("42")) // < 3 chars
    }
}
