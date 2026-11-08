package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DisciplineScoringRulesTest {

    @Test
    fun testDefaultAdminPinCannotOpenProtectedConsole() {
        assertFalse(AdminSecurityRules.canOpenProtectedConsole(isDefaultPin = true))
        assertTrue(AdminSecurityRules.canOpenProtectedConsole(isDefaultPin = false))
    }

    @Test
    fun testWakeUpAttendanceThresholds() {
        assertEquals("ON_TIME", DisciplineScoringRules.evaluateWakeUpAttendance("07:04"))
        assertEquals("ON_TIME", DisciplineScoringRules.evaluateWakeUpAttendance("07:05"))
        assertEquals("LATE", DisciplineScoringRules.evaluateWakeUpAttendance("07:06"))
        assertEquals("LATE", DisciplineScoringRules.evaluateWakeUpAttendance("08:00"))
        assertEquals("VERY_LATE", DisciplineScoringRules.evaluateWakeUpAttendance("08:05"))
    }

    @Test
    fun testThreeStepWakeUpGate() {
        // Must have Camera + Bluetooth + 100% Motivation Track
        assertFalse(DisciplineScoringRules.canFinalizeWakeUp(false, true, 100.0))
        assertFalse(DisciplineScoringRules.canFinalizeWakeUp(true, false, 100.0))
        assertFalse(DisciplineScoringRules.canFinalizeWakeUp(true, true, 99.0))
        assertTrue(DisciplineScoringRules.canFinalizeWakeUp(true, true, 100.0))
    }

    @Test
    fun testDailyScoreCalculationAndClamping() {
        val pts = listOf(15, 5, 10, 10, 35, 15, 8, -5, -4)
        assertEquals(89, DisciplineScoringRules.calculateDailyScore(pts))
        assertEquals(0, DisciplineScoringRules.calculateDailyScore(emptyList()))
        assertEquals(100, DisciplineScoringRules.calculateDailyScore(listOf(60, 60)))
        assertEquals(-15, DisciplineScoringRules.calculateDailyScore(listOf(-5, -10)))
    }

    @Test
    fun testGymSkipDeductsEarnedPointsAndRemainsVisibleAtZero() {
        for ((earned, expected) in listOf(80 to 70, 60 to 50, 5 to -5, 0 to -10)) {
            val entries = listOf(
                DisciplineScoringRules.ScorePointEntry("PROOF_APPROVED", earned, "retained-proof", true),
                DisciplineScoringRules.ScorePointEntry("GYM_SKIPPED", -10)
            )
            assertEquals(expected, DisciplineScoringRules.calculateVerifiedDailyScore(entries))
            assertEquals(10, DisciplineScoringRules.calculateDailyDeductions(entries.map { it.points }))
        }
        assertEquals(45, DisciplineScoringRules.calculateDailyDeductions(listOf(-10, -5, -5, -5, -5, -5, -5, -5)))
    }

    @Test
    fun testPositivePointsRequireApprovedProofWithRetainedEvidence() {
        assertFalse(DisciplineScoringRules.canAwardPositiveProofPoints("HOURLY_PRODUCTIVE", true))
        assertFalse(DisciplineScoringRules.canAwardPositiveProofPoints("PROOF_APPROVED", false))
        assertTrue(DisciplineScoringRules.canAwardPositiveProofPoints("PROOF_APPROVED", true))
    }

    @Test
    fun testFreshScoreAndUnprovedPositiveEventsStayAtZero() {
        assertEquals(0, DisciplineScoringRules.calculateVerifiedDailyScore(emptyList()))
        assertEquals(
            0,
            DisciplineScoringRules.calculateVerifiedDailyScore(
                listOf(
                    DisciplineScoringRules.ScorePointEntry("HOURLY_PRODUCTIVE", 25, "claim", true),
                    DisciplineScoringRules.ScorePointEntry("PROOF_APPROVED", 40, "", true),
                    DisciplineScoringRules.ScorePointEntry("PROOF_APPROVED", 60, "pending-proof", false)
                )
            )
        )
    }

    @Test
    fun testOnlyApprovedRetainedProofAddsPositiveScore() {
        assertEquals(
            12,
            DisciplineScoringRules.calculateVerifiedDailyScore(
                listOf(
                    DisciplineScoringRules.ScorePointEntry("HOURLY_PRODUCTIVE", 50, "claim", true),
                    DisciplineScoringRules.ScorePointEntry("PROOF_APPROVED", 12, "retained-proof", true)
                )
            )
        )
        assertEquals(
            -5,
            DisciplineScoringRules.calculateVerifiedDailyScore(
                listOf(DisciplineScoringRules.ScorePointEntry("SNOOZE", -5))
            )
        )
    }

    @Test
    fun testHourlySubmissionWindowUsesHalfOpenBoundaries() {
        val start = 1_000_000L
        val end = start + 20 * 60 * 1000L
        assertFalse(DisciplineScoringRules.isSubmissionWithinWindow(start - 1, start, end))
        assertTrue(DisciplineScoringRules.isSubmissionWithinWindow(start, start, end))
        assertTrue(DisciplineScoringRules.isSubmissionWithinWindow(end - 1, start, end))
        assertFalse(DisciplineScoringRules.isSubmissionWithinWindow(end, start, end))
    }

    @Test
    fun testIsoOffsetsAreConvertedToInstantsAndInvalidTimesFailClosed() {
        val ist = DisciplineScoringRules.parseIsoTimestampMs("2026-10-12T09:50:00+05:30")
        val utc = DisciplineScoringRules.parseIsoTimestampMs("2026-10-12T04:20:00Z")
        assertEquals(ist, utc)
        assertEquals(Long.MIN_VALUE, DisciplineScoringRules.parseIsoTimestampMs("not-a-time"))
    }

    @Test
    fun testEntertainmentModesAndLocalMusicException() {
        // Score 48: STRICT_MODE -> YouTube & Instagram blocked, Local MP3 allowed
        assertEquals("STRICT_MODE", DisciplineScoringRules.evaluateScoreMode(48))
        assertFalse(DisciplineScoringRules.isAppAllowed(48, "com.google.android.youtube"))
        assertFalse(DisciplineScoringRules.isAppAllowed(48, "com.instagram.android"))
        assertTrue(DisciplineScoringRules.isAppAllowed(48, "Local MP3 Player"))

        // Score 68: CONTROLLED_MODE -> Local MP3 allowed, YouTube & Instagram restricted
        assertEquals("CONTROLLED_MODE", DisciplineScoringRules.evaluateScoreMode(68))
        assertTrue(DisciplineScoringRules.isAppAllowed(68, "Local MP3 Player"))
        assertFalse(DisciplineScoringRules.isAppAllowed(68, "com.google.android.youtube"))
        assertFalse(DisciplineScoringRules.isAppAllowed(68, "com.instagram.android"))

        // Score 84: REWARD_UNLOCKED -> All allowed
        assertEquals("REWARD_UNLOCKED", DisciplineScoringRules.evaluateScoreMode(84))
        assertTrue(DisciplineScoringRules.isAppAllowed(84, "com.google.android.youtube"))
        assertTrue(DisciplineScoringRules.isAppAllowed(84, "com.instagram.android"))
    }

    @Test
    fun testFourTierScoreStatesAndDisciplineLevels() {
        assertEquals("STRICT_MODE", DisciplineScoringRules.evaluateScoreState(45))
        assertEquals("CONTROLLED_MODE", DisciplineScoringRules.evaluateScoreState(72))
        assertEquals("REWARD_UNLOCKED", DisciplineScoringRules.evaluateScoreState(85))
        assertEquals("ELITE_DAY", DisciplineScoringRules.evaluateScoreState(95))

        assertEquals("STARTER", DisciplineScoringRules.evaluateDisciplineLevel(50))
        assertEquals("DISCIPLINED", DisciplineScoringRules.evaluateDisciplineLevel(350))
        assertEquals("CONSISTENT", DisciplineScoringRules.evaluateDisciplineLevel(900))
        assertEquals("FOCUSED", DisciplineScoringRules.evaluateDisciplineLevel(1600))
        assertEquals("ELITE", DisciplineScoringRules.evaluateDisciplineLevel(2600))
    }

    @Test
    fun testTaskDependencyEvaluation() {
        assertFalse(
            DisciplineScoringRules.evaluateTaskDependencies(
                completedTasks = setOf("WAKE_UP"),
                requiredDependencies = listOf("WAKE_UP", "BATH")
            )
        )
        assertTrue(
            DisciplineScoringRules.evaluateTaskDependencies(
                completedTasks = setOf("WAKE_UP", "BATH", "STUDY_BLOCK"),
                requiredDependencies = listOf("WAKE_UP", "BATH")
            )
        )
    }

    @Test
    fun testWakeUpVerifiedAwardsPositivePointsOnlyWithRetainedEvidence() {
        assertTrue(DisciplineScoringRules.canAwardPositiveProofPoints("WAKEUP_VERIFIED", true))
        assertFalse(DisciplineScoringRules.canAwardPositiveProofPoints("WAKEUP_VERIFIED", false))
        assertEquals(
            20,
            DisciplineScoringRules.calculateVerifiedDailyScore(
                listOf(
                    DisciplineScoringRules.ScorePointEntry("WAKEUP_VERIFIED", 20, "wakeup-evidence-1", true)
                )
            )
        )
    }

    @Test
    fun testSamsungYouTubeAndInstagramTargetAppDetectionAndRestrictionDecisions() {
        val ytTarget = DisciplineScoringRules.findTargetApp("com.google.android.youtube")
        val ytMusicTarget = DisciplineScoringRules.findTargetApp("com.google.android.apps.youtube.music")
        val igTarget = DisciplineScoringRules.findTargetApp("com.instagram.android")
        val igLiteTarget = DisciplineScoringRules.findTargetApp("com.instagram.lite")

        assertTrue(ytTarget != null && ytTarget.key == "youtube")
        // YouTube Music is not one of the four user-requested monitoring targets.
        assertNull(ytMusicTarget)
        assertTrue(igTarget != null && igTarget.key == "instagram")
        assertTrue(igLiteTarget != null && igLiteTarget.key == "instagram")
        assertEquals("com.instagram.lite", igLiteTarget?.packageName)
        for (pkg in listOf("com.android.chrome", "com.google.android.apps.nbu.paisa.user", "com.bank.youtube.debug")) {
            assertNull(DisciplineScoringRules.findTargetApp(pkg))
            assertFalse(DisciplineScoringRules.evaluateRestrictionDecision(0, pkg,
                appLocksMap = mapOf("chrome" to true, "youtube" to true)).isRestricted)
        }

        val lowScoreDecision = DisciplineScoringRules.evaluateRestrictionDecision(
            score = 40,
            packageNameOrApp = "com.google.android.youtube",
            appLocks = null
        )
        assertTrue(lowScoreDecision.isRestricted)
        assertEquals("RESTRICTED", lowScoreDecision.action)

        val manualLockHighScoreDecision = DisciplineScoringRules.evaluateRestrictionDecision(
            score = 95,
            packageNameOrApp = "com.instagram.android",
            appLocksMap = mapOf("instagram" to true)
        )
        assertFalse(manualLockHighScoreDecision.isRestricted)
        assertEquals("ALLOWED", manualLockHighScoreDecision.action)
    }

    @Test
    fun testScoreUnlockBoundariesForVideoSocialAndMusic() {
        for ((pkg, key, threshold) in listOf(
            Triple("com.google.android.youtube", "youtube", 80),
            Triple("com.instagram.android", "instagram", 80),
            Triple("com.sec.android.app.music", "samsung_music", 60),
            Triple("com.spotify.music", "spotify", 60)
        )) {
            for (score in listOf(0, 59, 60, 79, 80, 81, 100)) {
                for (remoteLock in listOf(false, true)) {
                    val decision = DisciplineScoringRules.evaluateRestrictionDecision(
                        score, pkg, appLocksMap = mapOf(key to remoteLock)
                    )
                    assertEquals("$pkg at $score", score < threshold, decision.isRestricted)
                    assertEquals(threshold, decision.requiredScore)
                    assertEquals(maxOf(0, threshold - score), decision.remainingPoints)
                }
            }
        }
        assertEquals("ALLOWED", DisciplineScoringRules.evaluateRestrictionDecision(
            80, "com.google.android.youtube", isEducationalUnlockActive = true
        ).action)
        assertTrue(DisciplineScoringRules.evaluateRestrictionDecision(
            79, "com.google.android.youtube", isEducationalUnlockActive = true
        ).isRestricted)
    }

    @Test
    fun testStrictBotSessionDismissalResetsOnNonRestrictedApp() {
        StrictBotOverlayController.onNonRestrictedForegroundObserved("com.sec.android.app.launcher")
        assertFalse(StrictBotOverlayController.isDismissedForCurrentSession("com.google.android.youtube"))

        StrictBotOverlayController.markSessionDismissed("com.google.android.youtube")
        assertTrue(StrictBotOverlayController.isDismissedForCurrentSession("com.google.android.youtube"))

        // Switching to a non-restricted app resets session dismissal so reopening YouTube shows popup again
        StrictBotOverlayController.onNonRestrictedForegroundObserved("com.sec.android.app.launcher")
        assertFalse(StrictBotOverlayController.isDismissedForCurrentSession("com.google.android.youtube"))
    }

    @Test
    fun testQrAndSixDigitPinPayloadValidation() {
        assertTrue(QrScannerActivity.isValidStrictRecordsQrPayload("482910").first)
        assertTrue(QrScannerActivity.isValidStrictRecordsQrPayload("SR-ABCD-1234").first)
        assertTrue(
            QrScannerActivity.isValidStrictRecordsQrPayload(
                """{"session_key":"482910","server_endpoint":"http://192.168.1.25:8765","candidate_endpoints":["http://192.168.1.25:8765"]}"""
            ).first
        )
        assertFalse(QrScannerActivity.isValidStrictRecordsQrPayload("https://example.com").first)
    }

    @Test
    fun testEventDrivenStrictBotAnimationResolver() {
        assertEquals("IDLE", DisciplineScoringRules.resolveStrictBotAnimationForEvent("DASHBOARD_OPEN"))
        assertEquals("WALK", DisciplineScoringRules.resolveStrictBotAnimationForEvent("NAVIGATE_SECTION"))
        assertEquals("FLY", DisciplineScoringRules.resolveStrictBotAnimationForEvent("STRICTBOT_ACTION"))
        assertEquals("REVIEW", DisciplineScoringRules.resolveStrictBotAnimationForEvent("EVIDENCE_ANALYZING"))
        assertEquals("IDLE", DisciplineScoringRules.resolveStrictBotAnimationForEvent("OCR_COMPLETED"))
        assertEquals("WARNING", DisciplineScoringRules.resolveStrictBotAnimationForEvent("DUPLICATE_DETECTED"))
        assertEquals("SUCCESS", DisciplineScoringRules.resolveStrictBotAnimationForEvent("PROOF_ACCEPTED"))
        assertEquals("WARNING", DisciplineScoringRules.resolveStrictBotAnimationForEvent("TASK_MISSED"))
        assertEquals("SPEAK", DisciplineScoringRules.resolveStrictBotAnimationForEvent("DAILY_SUMMARY_READY"))
    }

    @Test
    fun testLayeredDuplicateDetectionSha256PerceptualAndOcrSimilarity() {
        val sampleBytes = "strict-records-proof-image-payload".toByteArray()
        val sha = DisciplineScoringRules.computeSha256Hex(sampleBytes)
        assertEquals(64, sha.length)

        val priorExact = DisciplineScoringRules.EvidenceFingerprintRecord(
            evidenceId = "ev_prior_1",
            submissionId = "sub_prior_1",
            activityId = "act_1",
            sha256Hash = sha,
            dHash = "0123456789abcdef",
            aHash = "fedcba9876543210",
            ocrTokens = setOf("kotlin", "gradle", "build", "passed", "tests", "verified"),
            createdAt = "2026-10-12T10:00:00+05:30"
        )

        // 1. Self-exclusion prevents matching itself
        val selfCheck = DisciplineScoringRules.evaluateDuplicateCheck(
            sha256Hash = sha,
            dHash = "0123456789abcdef",
            aHash = "fedcba9876543210",
            ocrText = "kotlin gradle build passed tests verified",
            historicalEvidence = listOf(priorExact),
            excludeEvidenceId = "ev_prior_1"
        )
        assertEquals("NO_MATCH_FOUND", selfCheck.status)
        assertEquals("ORIGINAL", selfCheck.matchStatus)

        // 2. Exact SHA-256 match against a prior submission
        val exactMatch = DisciplineScoringRules.evaluateDuplicateCheck(
            sha256Hash = sha,
            dHash = "0000000000000000",
            aHash = "0000000000000000",
            ocrText = "",
            historicalEvidence = listOf(priorExact),
            excludeEvidenceId = "ev_new_2"
        )
        assertEquals("EXACT_DUPLICATE", exactMatch.status)
        assertEquals("SHA256_EXACT", exactMatch.comparisonMethod)
        assertEquals("ev_prior_1", exactMatch.matchedEvidenceId)

        // 3. Perceptual dHash distance <= 4 -> HIGH_SIMILARITY
        // "0123456789abcdef" vs "0123456789abcde0" has 4 bits difference in last hex nibble ('f' = 1111 vs '0' = 0000)
        val perceptualHigh = DisciplineScoringRules.evaluateDuplicateCheck(
            sha256Hash = "a".repeat(64),
            dHash = "0123456789abcde0",
            aHash = "1111111111111111",
            ocrText = "",
            historicalEvidence = listOf(priorExact)
        )
        assertEquals("HIGH_SIMILARITY", perceptualHigh.status)
        assertTrue(perceptualHigh.comparisonMethod.startsWith("PERCEPTUAL_"))

        // 4. OCR Token Jaccard similarity >= 0.92 -> HIGH_SIMILARITY
        val ocrHigh = DisciplineScoringRules.evaluateDuplicateCheck(
            sha256Hash = "b".repeat(64),
            dHash = "ffffffffffffffff",
            aHash = "0000000000000000",
            ocrText = "kotlin gradle build passed tests verified",
            historicalEvidence = listOf(priorExact)
        )
        assertEquals("HIGH_SIMILARITY", ocrHigh.status)
        assertEquals("OCR_JACCARD", ocrHigh.comparisonMethod)

        // 5. Missing hash -> CHECK_INCOMPLETE
        val incomplete = DisciplineScoringRules.evaluateDuplicateCheck(
            sha256Hash = "",
            dHash = "",
            aHash = "",
            ocrText = "",
            historicalEvidence = listOf(priorExact)
        )
        assertEquals("CHECK_INCOMPLETE", incomplete.status)
    }

    @Test
    fun testStreamingFileSha256MatchesBytes() {
        val sampleBytes = "test-camera-proof-bytes-${System.currentTimeMillis()}".toByteArray()
        val expectedSha = DisciplineScoringRules.computeSha256Hex(sampleBytes)
        val tempFile = java.io.File.createTempFile("test_proof_", ".jpg")
        try {
            tempFile.writeBytes(sampleBytes)
            val fileSha = DisciplineScoringRules.computeSha256Hex(tempFile)
            assertEquals(expectedSha, fileSha)
        } finally {
            tempFile.delete()
        }
    }

    @Test
    fun testCalculateProofSampleSizeNoDivideByZero() {
        // Zero and negative dimensions must not throw / by zero and should return 1
        assertEquals(1, DisciplineScoringRules.calculateProofSampleSize(0, 0, 512))
        assertEquals(1, DisciplineScoringRules.calculateProofSampleSize(-100, -50, 512))

        // Small dimensions <= 512 should have sampleSize 1
        assertEquals(1, DisciplineScoringRules.calculateProofSampleSize(100, 100, 512))
        assertEquals(1, DisciplineScoringRules.calculateProofSampleSize(512, 384, 512))

        // 1080p should step up to 4 (1920 / 4 = 480 <= 512)
        assertEquals(4, DisciplineScoringRules.calculateProofSampleSize(1920, 1080, 512))

        // High-res camera image (4032x3024)
        assertEquals(8, DisciplineScoringRules.calculateProofSampleSize(4032, 3024, 512))
    }
}


