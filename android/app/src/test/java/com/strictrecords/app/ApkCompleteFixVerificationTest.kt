package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Automated JUnit verification for the 3-part APK Complete Fix:
 * - Part A: AI Service Health Monitoring (60-minute cache TTL, UI status labels, provider fallback)
 * - Part B: Reliable Background Upload & Processing (all 13 upload states, progress %, resumable offset)
 * - Part C: Music & Video Playback Restrictions (`evaluateMediaPermission` deterministic matrix)
 */
class ApkCompleteFixVerificationTest {

    @Test
    fun partA_healthRecord60MinuteStalenessAndUiStatus() {
        val now = 1_762_000_000_000L
        val freshTaufa = AiServiceHealthManager.HealthRecord(
            taufaStatus = "CONNECTED",
            openaiStatus = "UNCONFIGURED",
            activeProvider = "TAUFA_AI",
            lastHealthCheckMs = now - (15 * 60 * 1000L), // 15 minutes ago
            lastSuccessfulProvider = "TAUFA_AI",
            responseLatencyMs = 135,
            uiStatus = AiServiceHealthManager.UI_STATUS_CONNECTED_TAUFA
        )
        assertFalse("Record checked 15m ago must not be stale", freshTaufa.isStale(now))
        assertEquals(AiServiceHealthManager.UI_STATUS_CONNECTED_TAUFA, freshTaufa.effectiveUiStatus(now))

        // After 61 minutes without a check, isStale marks it stale for background refresh
        val after61Min = now + (46 * 60 * 1000L)
        assertTrue("Record > 60m old must be marked stale", freshTaufa.isStale(after61Min))

        // OpenAI Fallback active provider status
        val fallbackRecord = freshTaufa.copy(
            taufaStatus = "UNAVAILABLE",
            openaiStatus = "CONNECTED",
            activeProvider = "OPENAI_FALLBACK",
            uiStatus = AiServiceHealthManager.UI_STATUS_CONNECTED_OPENAI
        )
        assertEquals(AiServiceHealthManager.UI_STATUS_CONNECTED_OPENAI, fallbackRecord.effectiveUiStatus(now))
        val bridgeSt = TaufaPcBridge.statusFromHealthRecord(fallbackRecord)
        assertTrue(bridgeSt.ready)
        assertEquals("OPENAI_FALLBACK", bridgeSt.activeProvider)
    }

    @Test
    fun partB_all13UploadStatesAndProgressCalculation() {
        val allStates = PersistentUploadQueueManager.States.NEEDS_UPLOAD_STATES +
            PersistentUploadQueueManager.States.NEEDS_RECONCILE_STATES +
            PersistentUploadQueueManager.States.TERMINAL_STATES
        val expectedStates = setOf(
            "CREATED",
            "QUEUED",
            "UPLOADING",
            "UPLOAD_PAUSED",
            "UPLOAD_RETRY_PENDING",
            "UPLOADED",
            "SERVER_PROCESSING",
            "AI_VALIDATION_PENDING",
            "AI_VALIDATING",
            "APPROVED",
            "REJECTED",
            "MANUAL_REVIEW",
            "COMPLETED"
        )
        assertEquals(expectedStates, allStates)

        val uploadingRec = PersistentUploadQueueManager.QueuedUploadRecord(
            submissionId = "sub_test_1",
            sessionId = "daily_2026-11-06",
            activityId = "act_1",
            taskType = "HOURLY",
            date = "2026-11-06",
            category = "STUDY",
            description = "Algorithms study",
            evidenceFileReference = "/data/user/0/com.strictrecords.app/files/durable_proofs/sub_test_1.jpg",
            fileName = "proof.jpg",
            fileSize = 524_288L,
            mimeType = "image/jpeg",
            sha256Checksum = "abc123",
            createdAt = "2026-11-06T11:00:00+05:30",
            uploadOffset = 262_144L,
            retryCount = 0,
            lastUploadError = "",
            state = PersistentUploadQueueManager.States.UPLOADING,
            serverAcknowledgement = "ack_1",
            aiValidationStatus = "PENDING",
            pointsTransactionStatus = "PENDING",
            pointsAwarded = 0
        )
        assertEquals(40, uploadingRec.progressPercent)
        assertFalse(uploadingRec.state in PersistentUploadQueueManager.States.TERMINAL_STATES)

        val approvedRec = uploadingRec.copy(
            uploadOffset = 524_288L,
            state = PersistentUploadQueueManager.States.APPROVED,
            pointsAwarded = 8
        )
        assertEquals(100, approvedRec.progressPercent)
        assertTrue(approvedRec.state in PersistentUploadQueueManager.States.TERMINAL_STATES)
    }

    @Test
    fun partC_mediaPermissionPolicyMatrix() {
        val nowMs = 1_762_000_000_000L

        // 1. Active Strict Session (FOCUS_ACTIVE) blocks Spotify, YouTube, Netflix, Instagram
        for (pkg in listOf(
            "com.spotify.music",
            "com.google.android.youtube",
            "com.netflix.mediaclient",
            "com.instagram.android"
        )) {
            val res = MediaRestrictionPolicyEngine.evaluateMediaPermission(
                packageName = pkg,
                mediaState = "PLAYING",
                sessionPhase = "FOCUS_ACTIVE",
                pointsBalance = 95,
                activeReward = true,
                authorizationSource = "YOUTUBE_REWARD",
                rewardExpiryMs = nowMs + 600_000L,
                nowMs = nowMs
            )
            assertEquals(
                "Package $pkg must be BLOCKED during FOCUS_ACTIVE",
                MediaRestrictionPolicyEngine.DECISION_BLOCKED,
                res.decision
            )
        }

        // 2. Music app (Spotify) outside Strict Session: blocked when points < 80, allowed when points >= 80
        val musicLowPoints = MediaRestrictionPolicyEngine.evaluateMediaPermission(
            packageName = "com.spotify.music",
            mediaState = "PLAYING",
            sessionPhase = "INACTIVE",
            pointsBalance = 65,
            activeReward = false,
            authorizationSource = "NONE",
            nowMs = nowMs
        )
        assertEquals(MediaRestrictionPolicyEngine.DECISION_BLOCKED, musicLowPoints.decision)

        val musicHighPoints = MediaRestrictionPolicyEngine.evaluateMediaPermission(
            packageName = "com.spotify.music",
            mediaState = "PLAYING",
            sessionPhase = "INACTIVE",
            pointsBalance = 85,
            activeReward = false,
            authorizationSource = "POINTS_THRESHOLD",
            nowMs = nowMs
        )
        assertEquals(MediaRestrictionPolicyEngine.DECISION_ALLOWED, musicHighPoints.decision)

        // 3. Gym Mode: Spotify & Samsung Music allowed even if points < 80, but Netflix/YouTube video still blocked without reward
        val gymSpotify = MediaRestrictionPolicyEngine.evaluateMediaPermission(
            packageName = "com.spotify.music",
            mediaState = "PLAYING",
            sessionPhase = "INACTIVE",
            pointsBalance = 50,
            activeReward = false,
            authorizationSource = "GYM_MODE_MUSIC",
            isGymMusicAllowed = true,
            nowMs = nowMs
        )
        assertEquals(MediaRestrictionPolicyEngine.DECISION_TEMPORARILY_ALLOWED, gymSpotify.decision)

        val gymNetflix = MediaRestrictionPolicyEngine.evaluateMediaPermission(
            packageName = "com.netflix.mediaclient",
            mediaState = "PLAYING",
            sessionPhase = "INACTIVE",
            pointsBalance = 70,
            activeReward = false,
            authorizationSource = "NONE",
            isGymMusicAllowed = true,
            nowMs = nowMs
        )
        assertEquals(MediaRestrictionPolicyEngine.DECISION_BLOCKED, gymNetflix.decision)

        // 4. YouTube Reward active vs expired
        val ytActiveReward = MediaRestrictionPolicyEngine.evaluateMediaPermission(
            packageName = "com.google.android.youtube",
            mediaState = "PLAYING",
            sessionPhase = "INACTIVE",
            pointsBalance = 75,
            activeReward = true,
            authorizationSource = "YOUTUBE_REWARD",
            rewardExpiryMs = nowMs + 300_000L,
            nowMs = nowMs
        )
        assertEquals(MediaRestrictionPolicyEngine.DECISION_TEMPORARILY_ALLOWED, ytActiveReward.decision)

        val ytExpiredReward = MediaRestrictionPolicyEngine.evaluateMediaPermission(
            packageName = "com.google.android.youtube",
            mediaState = "PLAYING",
            sessionPhase = "INACTIVE",
            pointsBalance = 75,
            activeReward = false,
            authorizationSource = "YOUTUBE_REWARD",
            rewardExpiryMs = nowMs - 1_000L,
            nowMs = nowMs
        )
        assertEquals(MediaRestrictionPolicyEngine.DECISION_AUTHORIZATION_EXPIRED, ytExpiredReward.decision)

        // 5. Educational video explicitly approved
        val eduVideo = MediaRestrictionPolicyEngine.evaluateMediaPermission(
            packageName = "com.google.android.youtube",
            mediaState = "PLAYING",
            sessionPhase = "FOCUS_ACTIVE",
            pointsBalance = 60,
            activeReward = true,
            authorizationSource = "EDUCATIONAL_VIDEO",
            isEducationalVideoAllowed = true,
            educationalRemainingSec = 600,
            nowMs = nowMs
        )
        assertEquals(MediaRestrictionPolicyEngine.DECISION_TEMPORARILY_ALLOWED, eduVideo.decision)
    }
}
