package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class WakeUpCameraPolicyTest {
    private fun complete(session: WakeUpCameraPolicy.Session, start: Long = 1000L, startId: Long = 1L, count: Int = 110) {
        repeat(count) { i -> session.observe(startId + i, start + i * 200L, start + i * 200L + 25L, true) }
    }
    @Test fun freshStableObservationsMustCompleteFullDuration() {
        val s = WakeUpCameraPolicy.Session()
        s.observe(1, 1000, 1020, true); s.observe(2, 1200, 1220, true)
        assertEquals(0L, s.validMs)
        complete(s, 1400, 3, 95)
        assertFalse(WakeUpCameraPolicy.validate(s.evidence(20225)).passed)
        complete(s, 20400, 98, 15)
        assertTrue(WakeUpCameraPolicy.validate(s.evidence(21625)).passed)
    }
    @Test fun missingDuplicateStaleAndFutureFramesNeverAddEvidence() {
        val s = WakeUpCameraPolicy.Session()
        complete(s, count = 12)
        val before = s.validMs; val samples = s.validSamples
        s.observe(12, 3200, 3400, true)
        s.observe(13, 3400, 4400, true)
        s.observe(14, 5000, 4500, true)
        assertEquals(before, s.validMs); assertEquals(samples, s.validSamples)
        assertFalse(WakeUpCameraPolicy.validate(s.evidence(4500)).passed)
    }
    @Test fun briefAndLongDetectionLossPauseWithoutCountingMissingTime() {
        val s = WakeUpCameraPolicy.Session()
        complete(s, count = 25)
        val before = s.validMs
        repeat(30) { i -> s.observe(26L + i, 6000L + i * 200, 6020L + i * 200, false) }
        assertEquals(before, s.validMs)
        complete(s, 12200, 56, 3)
        assertEquals(before, s.validMs)
        s.observe(59, 12800, 12820, true)
        assertEquals(before + 200, s.validMs)
    }
    @Test fun cameraAndBackgroundRecoveryPreserveProgressWithinGrace() {
        val s = WakeUpCameraPolicy.Session()
        complete(s, count = 30)
        val before = s.validMs
        s.pause("Camera disconnected", 7000, true)
        complete(s, 16000, 31, 3)
        assertEquals(before, s.validMs)
        s.observe(34, 16600, 16625, true)
        assertEquals(before + 200, s.validMs)
    }
    @Test fun expiredGraceRequiresFreshObservationInterval() {
        val s = WakeUpCameraPolicy.Session()
        complete(s, count = 30)
        s.pause("Background", 7000)
        complete(s, 40000, 31, 3)
        assertEquals(0L, s.validMs)
        assertFalse(WakeUpCameraPolicy.validate(s.evidence(40425)).passed)
    }
    @Test fun acceptanceUsesActualCountsAndSeventyPercentBoundary() {
        val e = WakeUpCameraPolicy.Evidence(20000, 70, 100, 1000, 22000, 22025, true, true, 100)
        assertTrue(WakeUpCameraPolicy.validate(e).passed)
        assertFalse(WakeUpCameraPolicy.validate(e.copy(validSamples = 69)).passed)
        assertFalse(WakeUpCameraPolicy.validate(e.copy(validSamples = 65)).passed)
        assertFalse(WakeUpCameraPolicy.validate(e.copy(eligibleSamples = 0)).passed)
        assertFalse(WakeUpCameraPolicy.validate(e.copy(uniqueFrames = 99)).passed)
        assertFalse(WakeUpCameraPolicy.validate(e.copy(acceptedAtMs = 23000)).passed)
        assertFalse(WakeUpCameraPolicy.validate(e.copy(finalFrameValid = false)).passed)
        assertFalse(WakeUpCameraPolicy.validate(e.copy(validMs = 19999)).passed)
    }
    @Test fun failedSessionCannotBecomePassedFromLaterCallbacks() {
        val s = WakeUpCameraPolicy.Session()
        s.fail("Permission revoked")
        complete(s)
        assertEquals(0L, s.validMs)
        assertEquals(WakeUpCameraPolicy.State.FAILED, s.state)
    }

    @Test fun metadataValidationMatchesPassedSessionAuthoritatively() {
        val s = WakeUpCameraPolicy.Session()
        complete(s, count = 110)
        val validation = WakeUpCameraPolicy.validate(s.evidence(23000))
        assertTrue(validation.passed)
        val telemetry = CameraFaceAnalysisEngine.LiveCameraAnalysisTelemetry(
            cameraAvailable = true, permissionGranted = true, frameStreamActive = true,
            frameFps = 5.0, lastFrameAgeMs = 50, brightnessScore = 120.0, contrastScore = 20.0,
            lightingStatus = CameraFaceAnalysisEngine.LightingStatus.GOOD,
            faceDetectionStatus = CameraFaceAnalysisEngine.FaceDetectionStatus.DETECTED,
            faceCount = 1, faceBoundingBox = null, leftEyeNormalized = null, rightEyeNormalized = null,
            facePositionStatus = CameraFaceAnalysisEngine.FacePositionStatus.CENTERED,
            leftEyeVisibility = CameraFaceAnalysisEngine.EyeVisibilityStatus.VISIBLE,
            rightEyeVisibility = CameraFaceAnalysisEngine.EyeVisibilityStatus.VISIBLE,
            headPoseStatus = CameraFaceAnalysisEngine.HeadPoseStatus.ACCEPTABLE,
            eulerAngles = null, analysisConfidence = 0.9f,
            overallReadiness = CameraFaceAnalysisEngine.OverallReadiness.READY,
            actionableGuidance = "Ready", timestampMs = System.currentTimeMillis()
        )
        val meta = s.metadata(23000, telemetry)
        val metaValidation = WakeUpCameraPolicy.validateMetadata(meta)
        assertTrue(metaValidation.reasons.joinToString(), metaValidation.passed)
    }

    @Test fun specificFailureReasonsSurfacedForStaleOrInsufficient() {
        val e = WakeUpCameraPolicy.Evidence(15000, 70, 100, 1000, 22000, 22025, true, true, 100)
        val v = WakeUpCameraPolicy.validate(e)
        assertFalse(v.passed)
        assertTrue(v.reasons.contains("20s of valid observation required"))

        val stale = e.copy(validMs = 20000, acceptedAtMs = 24000)
        val vStale = WakeUpCameraPolicy.validate(stale)
        assertFalse(vStale.passed)
        assertTrue(vStale.reasons.contains("Final evidence is stale"))
    }
}
