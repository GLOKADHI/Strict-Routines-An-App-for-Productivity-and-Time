package com.strictrecords.app

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class TaufaAiDemoTest {

    private val sampleImg1 = "Sample Image Bytes 1 for unit testing".toByteArray(Charsets.UTF_8)
    private val sampleImg2 = "Sample Image Bytes 2 for unit testing".toByteArray(Charsets.UTF_8)

    @Before
    fun setUp() {
        TaufaAiValidator.customEndpoint = null
        TaufaAiValidator.customApiKey = null
    }

    @After
    fun tearDown() {
        TaufaAiValidator.customEndpoint = null
        TaufaAiValidator.customApiKey = null
    }

    @Test
    fun testDemoValidationRequiresAtLeastOneImage() {
        val res = TaufaAiValidator.runDemoValidation(
            context = null,
            category = "STUDY",
            description = "Completed operating systems assignment on scheduling algorithms.",
            images = emptyList()
        )
        assertFalse(res.success)
        assertEquals("NO_ATTACHMENTS", res.code)
        assertEquals("DEMO — NO REAL POINTS", res.mode)
        assertTrue(res.isDemo)
    }

    @Test
    fun testDemoValidationRejectsExceedingMaxImages() {
        val elevenImages = (1..11).map { "Img$it".toByteArray(Charsets.UTF_8) }
        val res = TaufaAiValidator.runDemoValidation(
            context = null,
            category = "STUDY",
            description = "Completed operating systems assignment on scheduling algorithms.",
            images = elevenImages
        )
        assertFalse(res.success)
        assertEquals("EXCEEDED_MAX_ATTACHMENTS", res.code)
    }

    @Test
    fun testDemoValidationRejectsInvalidDescription() {
        val res = TaufaAiValidator.runDemoValidation(
            context = null,
            category = "STUDY",
            description = "done",
            images = listOf(sampleImg1)
        )
        assertFalse(res.success)
        assertEquals("INVALID_DESCRIPTION", res.code)
    }

    @Test
    fun testDemoValidationUnconfiguredReturnsUnconfiguredError() {
        val res = TaufaAiValidator.runDemoValidation(
            context = null,
            category = "STUDY",
            description = "Studied algorithms textbook chapter on graph traversal depth first and breadth first search.",
            images = listOf(sampleImg1, sampleImg2)
        )
        assertFalse(res.success)
        assertTrue(res.isDemo)
        assertEquals("DEMO — NO REAL POINTS", res.mode)
        assertEquals("UNCONFIGURED", res.validatorStatus)
        assertEquals("TAUFA_AI_UNCONFIGURED", res.code)
        assertFalse(res.isMock)
        assertFalse(res.isLiveTaufa)
        assertEquals(2, res.imagesEvaluated)
        assertEquals(0, res.score)
        assertEquals(8, res.maxScore)
        assertNotNull(res.error)
    }

    @Test
    fun testDemoDuplicateDetectionIntraSubmission() {
        // Two identical image byte arrays inside the same demo request
        val identicalImg = "Identical photo data for duplicate test".toByteArray(Charsets.UTF_8)
        val res = TaufaAiValidator.runDemoValidation(
            context = null,
            category = "STUDY",
            description = "Studied algorithms textbook chapter on graph traversal depth first and breadth first search.",
            images = listOf(identicalImg, identicalImg.copyOf()),
            checkDuplicate = true
        )
        assertTrue(res.success)
        assertEquals(0, res.score)
        assertEquals("REJECTED", res.validatorStatus)
        assertNotNull(res.duplicateResult)
        assertTrue(res.duplicateResult!!.duplicateDetected)

        val outcome = res.simulatedOutcome
        assertNotNull(outcome)
        assertFalse(outcome!!.thresholdMet)
        assertEquals(0, outcome.simulatedPoints)
        assertEquals(0, outcome.realPointsAwarded)
        assertFalse(outcome.realBalanceChanged)
    }

    @Test
    fun testDemoDuplicateDetectionAgainstReferenceImages() {
        val refImg = "Reference Image Data From Prior Occurrence".toByteArray(Charsets.UTF_8)
        val res = TaufaAiValidator.runDemoValidation(
            context = null,
            category = "STUDY",
            description = "Studied algorithms textbook chapter on graph traversal depth first and breadth first search.",
            images = listOf(refImg.copyOf()),
            referenceImages = listOf(refImg),
            referenceSubmissionIds = listOf("ref_sub_prior_101"),
            checkDuplicate = true
        )
        assertTrue(res.success)
        assertEquals(0, res.score)
        assertEquals("REJECTED", res.validatorStatus)
        assertNotNull(res.duplicateResult)
        assertTrue(res.duplicateResult!!.duplicateDetected)
    }

    @Test
    fun testDemoResultToJsonSerialization() {
        val dupImg = "Duplicate serialization test bytes".toByteArray(Charsets.UTF_8)
        val res = TaufaAiValidator.runDemoValidation(
            context = null,
            category = "EDITING",
            description = "Color graded 4K sequence in DaVinci Resolve with LUT and curves adjustment.",
            images = listOf(dupImg, dupImg.copyOf()),
            checkDuplicate = true
        )
        val json = res.toJsonObject()
        assertTrue(json.optBoolean("is_demo"))
        assertEquals("DEMO — NO REAL POINTS", json.optString("mode"))
        assertEquals("EDITING", json.optString("category"))
        assertEquals(2, json.optInt("images_evaluated"))

        val simObj = json.optJSONObject("simulated_outcome")
        assertNotNull(simObj)
        assertEquals(0, simObj!!.optInt("real_points_awarded"))
        assertFalse(simObj.optBoolean("real_balance_changed"))
        assertFalse(simObj.optBoolean("production_record_created"))
        assertFalse(simObj.optBoolean("youtube_reward_issued"))
    }
}
