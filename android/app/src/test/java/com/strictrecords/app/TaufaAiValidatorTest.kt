package com.strictrecords.app

import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class TaufaAiValidatorTest {

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
    fun testIsConfiguredFalseByDefault() {
        assertFalse(TaufaAiValidator.isConfigured(null))
    }

    @Test
    fun testIsConfiguredTrueWithValidHttpsEndpointAndKey() {
        TaufaAiValidator.customEndpoint = "https://api.taufa.example.com/v1/validate"
        TaufaAiValidator.customApiKey = "secret-token-12345"
        assertTrue(TaufaAiValidator.isConfigured(null))
    }

    @Test
    fun testMeaningfulDescriptionValidInputs() {
        val validDescriptions = listOf(
            "Completed chapter 4 physics numericals on rotational motion and torque",
            "Wrote unit tests for auth service and fixed regression in token renewal",
            "Edited 10 minute video on Premiere Pro including B-roll cuts and color grade",
            "Attended computer science lecture on distributed consensus algorithms",
            "Prepared slide presentation for client quarterly review meeting"
        )
        for (desc in validDescriptions) {
            val (ok, msg) = TaufaAiValidator.validateDescription(desc)
            assertTrue("Expected description to pass: '$desc', but got msg: '$msg'", ok)
            assertEquals("", msg)
        }
    }

    @Test
    fun testMeaningfulDescriptionRejectsBlankAndShort() {
        val invalidShort = listOf(
            "",
            "   ",
            "abc",
            "work",
            "code"
        )
        for (desc in invalidShort) {
            val (ok, msg) = TaufaAiValidator.validateDescription(desc)
            assertFalse("Expected '$desc' to be rejected", ok)
            assertTrue(msg.isNotBlank())
        }
    }

    @Test
    fun testMeaningfulDescriptionRejectsGenericFiller() {
        val fillerExamples = listOf(
            "test",
            "done",
            "ok",
            "asdf",
            "nothing",
            "na",
            "idk",
            "completed",
            "working",
            "sleeping",
            "chilling",
            "done done",
            "test test test"
        )
        for (desc in fillerExamples) {
            val (ok, msg) = TaufaAiValidator.validateDescription(desc)
            assertFalse("Expected filler '$desc' to be rejected", ok)
            assertTrue("Expected generic description warning, got: $msg", msg.contains("Generic", ignoreCase = true) || msg.contains("genuine", ignoreCase = true) || msg.contains("short", ignoreCase = true))
        }
    }

    @Test
    fun testMeaningfulDescriptionRejectsRepetitiveNoise() {
        val noiseExamples = listOf(
            "aaaaaaaaaaa",
            "1111111111",
            "abababababab"
        )
        for (desc in noiseExamples) {
            val (ok, _) = TaufaAiValidator.validateDescription(desc)
            assertFalse("Expected repetitive noise '$desc' to be rejected", ok)
        }
    }

    @Test
    fun testExcludesNonHourlyTaskTypesStrictly() {
        val excludedTypes = listOf("BATH", "GYM", "SLEEP", "WAKE_UP", "WAKEUP", "TRAVEL", "MOVIE")
        val dummyImage = ByteArray(100) { 1 }

        for (taskType in excludedTypes) {
            try {
                TaufaAiValidator.validateHourlySubmission(
                    context = null,
                    submissionId = "sub_test",
                    category = "GENERAL",
                    description = "Studied algorithms for 60 minutes",
                    images = listOf(dummyImage),
                    taskType = taskType
                )
                fail("Expected IllegalArgumentException for excluded task type: $taskType")
            } catch (e: IllegalArgumentException) {
                assertTrue(e.message?.contains("strictly excluded", ignoreCase = true) == true)
            }
        }
    }

    @Test
    fun testRejectsEmptyPhotos() {
        val res = TaufaAiValidator.validateHourlySubmission(
            context = null,
            submissionId = "sub_1",
            category = "STUDY",
            description = "Studied algorithms for 60 minutes",
            images = emptyList(),
            taskType = "HOURLY"
        )
        assertEquals("FAILED_FINAL", res.validatorStatus)
        assertTrue(res.reason.contains("At least one photo", ignoreCase = true))
        assertNull(res.score)
    }

    @Test
    fun testRejectsMoreThanTenPhotos() {
        val dummyImages = (1..11).map { ByteArray(100) { 1 } }
        val res = TaufaAiValidator.validateHourlySubmission(
            context = null,
            submissionId = "sub_1",
            category = "STUDY",
            description = "Studied algorithms for 60 minutes",
            images = dummyImages,
            taskType = "HOURLY"
        )
        assertEquals("FAILED_FINAL", res.validatorStatus)
        assertTrue(res.reason.contains("Maximum 10 photos allowed", ignoreCase = true))
        assertNull(res.score)
    }

    @Test
    fun testAcceptsOneToTenPhotosWhenUnconfigured() {
        val singleImage = listOf(ByteArray(100) { 1 })
        val res1 = TaufaAiValidator.validateHourlySubmission(
            context = null,
            submissionId = "sub_single",
            category = "STUDY",
            description = "Studied algorithms for 60 minutes",
            images = singleImage,
            taskType = "HOURLY"
        )
        assertEquals("UNCONFIGURED", res1.validatorStatus)
        assertNull(res1.score)

        val tenImages = (1..10).map { ByteArray(100) { 1 } }
        val res10 = TaufaAiValidator.validateHourlySubmission(
            context = null,
            submissionId = "sub_ten",
            category = "STUDY",
            description = "Studied algorithms for 60 minutes",
            images = tenImages,
            taskType = "HOURLY"
        )
        assertEquals("UNCONFIGURED", res10.validatorStatus)
        assertNull(res10.score)
    }

    @Test
    fun testParseAndValidateResponseValidScores() {
        val validScores = listOf(1, 4, 7, 8)
        for (s in validScores) {
            val json = JSONObject().apply {
                put("submission_id", "sub_test_123")
                put("score", s)
                put("max_score", 8)
                put("category_match", true)
                put("description_match", true)
                put("evidence_match", true)
                put("evidence_quality", "HIGH")
                put("reason", "Verified attendance")
                put("validator_status", "SUCCESS")
            }.toString()

            val parsed = TaufaAiValidator.parseAndValidateResponse("sub_test_123", json)
            assertNotNull(parsed)
            assertEquals("SUCCESS", parsed.validatorStatus)
            assertEquals(s, parsed.score)
            assertTrue(parsed.isValid)
            assertTrue(parsed.qualifiesForPoints)
            if (s == 8) {
                assertTrue(parsed.isFullPoints)
                assertFalse(parsed.isPartialPoints)
            } else {
                assertFalse(parsed.isFullPoints)
                assertTrue(parsed.isPartialPoints)
            }
        }

        // Score 0 is marked REJECTED
        val jsonZero = JSONObject().apply {
            put("submission_id", "sub_test_zero")
            put("score", 0)
            put("max_score", 8)
            put("category_match", true)
            put("description_match", true)
            put("evidence_match", true)
            put("validator_status", "SUCCESS")
        }.toString()
        val parsedZero = TaufaAiValidator.parseAndValidateResponse("sub_test_zero", jsonZero)
        assertEquals("REJECTED", parsedZero.validatorStatus)
        assertEquals(0, parsedZero.score)
        assertFalse(parsedZero.qualifiesForPoints)
    }

    @Test
    fun testCategoryConsistencyGateCapsScoreAndRejectsIrrelevantEvidence() {
        val json = JSONObject().apply {
            put("submission_id", "sub_irrelevant")
            put("score", 7)
            put("max_score", 8)
            put("category_match", false)
            put("description_match", true)
            put("evidence_match", false)
            put("reason", "Irrelevant selfie uploaded")
            put("validator_status", "SUCCESS")
        }.toString()

        val parsed = TaufaAiValidator.parseAndValidateResponse("sub_irrelevant", json, category = "STUDY")
        assertEquals(0, parsed.score)
        assertEquals("REJECTED", parsed.validatorStatus)
        assertEquals("REJECTED", parsed.reviewStatus)
        assertFalse(parsed.categoryMatch)
        assertFalse(parsed.evidenceMatch)
        assertFalse(parsed.qualifiesForPoints)
        assertTrue(parsed.warnings.contains("CATEGORY_MISMATCH"))
        assertTrue(parsed.warnings.contains("IRRELEVANT_EVIDENCE"))
    }

    @Test
    fun testCategoryConsistencyGateRejectsDescriptionMismatch() {
        val json = JSONObject().apply {
            put("submission_id", "sub_desc_mismatch")
            put("score", 8)
            put("max_score", 8)
            put("category_match", true)
            put("description_match", false)
            put("evidence_match", true)
            put("validator_status", "SUCCESS")
        }.toString()

        val parsed = TaufaAiValidator.parseAndValidateResponse("sub_desc_mismatch", json, category = "STUDY")
        assertEquals(0, parsed.score)
        assertEquals("REJECTED", parsed.validatorStatus)
        assertFalse(parsed.qualifiesForPoints)
        assertTrue(parsed.warnings.contains("DESCRIPTION_CONTRADICTS_EVIDENCE"))
    }

    @Test
    fun testCategoryConsistencyGateRejectsInsufficientEvidence() {
        val json = JSONObject().apply {
            put("submission_id", "sub_blank")
            put("score", 6)
            put("max_score", 8)
            put("category_match", true)
            put("description_match", true)
            put("evidence_match", true)
            put("evidence_quality", "INSUFFICIENT")
            put("validator_status", "SUCCESS")
        }.toString()

        val parsed = TaufaAiValidator.parseAndValidateResponse("sub_blank", json, category = "STUDY")
        assertEquals(0, parsed.score)
        assertEquals("REJECTED", parsed.validatorStatus)
        assertFalse(parsed.qualifiesForPoints)
        assertTrue(parsed.warnings.contains("INSUFFICIENT_EVIDENCE"))
    }

    @Test
    fun testParseAndValidateResponseRejectsOutOfRangeScores() {
        val invalidScores = listOf(-1, 9, 10, 100)
        for (s in invalidScores) {
            val json = JSONObject().apply {
                put("submission_id", "sub_test_123")
                put("score", s)
                put("max_score", 8)
                put("reason", "Out of bounds score")
                put("validator_status", "SUCCESS")
            }.toString()

            val parsed = TaufaAiValidator.parseAndValidateResponse("sub_test_123", json)
            assertNotNull(parsed)
            assertEquals("FAILED_FINAL", parsed?.validatorStatus)
            assertNull(parsed?.score)
            assertFalse(parsed?.isValid == true)
        }
    }

    @Test
    fun testNeverAutoAwardsFullMarksOnMalformedOrMissingScore() {
        val malformedJson = JSONObject().apply {
            put("submission_id", "sub_test_123")
            put("score", JSONObject.NULL)
            put("reason", "No score provided")
        }.toString()

        val parsed = TaufaAiValidator.parseAndValidateResponse("sub_test_123", malformedJson)
        assertNotNull(parsed)
        assertNull(parsed?.score)
        assertFalse(parsed?.isValid == true)
        assertEquals("FAILED_FINAL", parsed?.validatorStatus)
    }

    @Test
    fun testMismatchedSubmissionIdIsRejected() {
        val json = JSONObject().apply {
            put("submission_id", "sub_different_id")
            put("score", 8)
            put("max_score", 8)
            put("validator_status", "SUCCESS")
        }.toString()

        val parsed = TaufaAiValidator.parseAndValidateResponse("sub_expected_id", json)
        assertNotNull(parsed)
        assertEquals("FAILED_FINAL", parsed?.validatorStatus)
        assertNull(parsed?.score)
        assertFalse(parsed?.isValid == true)
        assertTrue(parsed?.reason?.contains("mismatch", ignoreCase = true) == true)
    }

    @Test
    fun testTaufaPromptIncludesProofScoringRubric() {
        val rubric = TaufaAiValidator.TAUFA_PROOF_SCORING_RUBRIC
        assertTrue(rubric.contains("Score\tWhat it looks like"))
        assertTrue(rubric.contains("0\tBlank, unreadable, selfie, wallpaper, or content that contradicts the claim"))
        assertTrue(rubric.contains("1\tBarely relevant, or a decorative/title graphic with a topic caption but no actual content"))
        assertTrue(rubric.contains("2\tReal material but near-empty page, gibberish description, or no way to tie it to the claimed activity"))
        assertTrue(rubric.contains("3\tRight subject, but weak proof: recycled or identical evidence reused from another slot"))
        assertTrue(rubric.contains("4\tPartial: correct material visible, description only loosely matched, or content too thin for an hour"))
        assertTrue(rubric.contains("5\tCredible and on-topic, but sparse, no timestamp, or the claim is vaguer than the evidence supports"))
        assertTrue(rubric.contains("6\tAuthentic and clearly matching, timestamped, with one real gap"))
        assertTrue(rubric.contains("7\tStrong, specific, timestamped evidence matching category and description closely"))
        assertTrue(rubric.contains("8\tExemplary: multiple timestamped pages of worked material that matches the described topics point for point"))

        val prompt = TaufaAiValidator.TAUFA_SCORING_PROMPT
        assertTrue(prompt.contains("SCORING SYSTEM FOR PROOF:"))
        assertTrue(prompt.contains("Score\tWhat it looks like"))

        val instruction = TaufaAiValidator.TAUFA_VALIDATION_SYSTEM_INSTRUCTION
        assertTrue(instruction.contains("SCORING SYSTEM FOR PROOF:"))
        assertTrue(instruction.contains("Score\tWhat it looks like"))
    }
}
