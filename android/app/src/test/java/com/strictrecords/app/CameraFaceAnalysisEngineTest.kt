package com.strictrecords.app

import android.graphics.PointF
import android.graphics.RectF
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CameraFaceAnalysisEngineTest {

    @Test
    fun testLightingStatusThresholds() {
        assertEquals("GOOD", CameraFaceAnalysisEngine.LightingStatus.GOOD.label)
        assertEquals(CameraFaceAnalysisEngine.AnalysisStatusColor.GREEN, CameraFaceAnalysisEngine.LightingStatus.GOOD.color)

        assertEquals("TOO DARK", CameraFaceAnalysisEngine.LightingStatus.TOO_DARK.label)
        assertEquals(CameraFaceAnalysisEngine.AnalysisStatusColor.AMBER, CameraFaceAnalysisEngine.LightingStatus.TOO_DARK.color)

        assertEquals("TOO BRIGHT", CameraFaceAnalysisEngine.LightingStatus.TOO_BRIGHT.label)
        assertEquals(CameraFaceAnalysisEngine.AnalysisStatusColor.AMBER, CameraFaceAnalysisEngine.LightingStatus.TOO_BRIGHT.color)
    }

    @Test
    fun testFaceDetectionAndPositionStatuses() {
        assertEquals("DETECTED", CameraFaceAnalysisEngine.FaceDetectionStatus.DETECTED.label)
        assertEquals(CameraFaceAnalysisEngine.AnalysisStatusColor.GREEN, CameraFaceAnalysisEngine.FaceDetectionStatus.DETECTED.color)

        assertEquals("CENTERED", CameraFaceAnalysisEngine.FacePositionStatus.CENTERED.label)
        assertEquals(CameraFaceAnalysisEngine.AnalysisStatusColor.GREEN, CameraFaceAnalysisEngine.FacePositionStatus.CENTERED.color)

        assertEquals("TOO CLOSE", CameraFaceAnalysisEngine.FacePositionStatus.TOO_CLOSE.label)
        assertEquals(CameraFaceAnalysisEngine.AnalysisStatusColor.AMBER, CameraFaceAnalysisEngine.FacePositionStatus.TOO_CLOSE.color)

        assertEquals("TOO FAR", CameraFaceAnalysisEngine.FacePositionStatus.TOO_FAR.label)
        assertEquals(CameraFaceAnalysisEngine.AnalysisStatusColor.AMBER, CameraFaceAnalysisEngine.FacePositionStatus.TOO_FAR.color)
    }

    @Test
    fun testEyeVisibilityAndHeadPoseStatuses() {
        assertEquals("VISIBLE", CameraFaceAnalysisEngine.EyeVisibilityStatus.VISIBLE.label)
        assertEquals(CameraFaceAnalysisEngine.AnalysisStatusColor.GREEN, CameraFaceAnalysisEngine.EyeVisibilityStatus.VISIBLE.color)

        assertEquals("OBSCURED", CameraFaceAnalysisEngine.EyeVisibilityStatus.PARTIALLY_OBSCURED.label)
        assertEquals(CameraFaceAnalysisEngine.AnalysisStatusColor.AMBER, CameraFaceAnalysisEngine.EyeVisibilityStatus.PARTIALLY_OBSCURED.color)

        assertEquals("ACCEPTABLE", CameraFaceAnalysisEngine.HeadPoseStatus.ACCEPTABLE.label)
        assertEquals(CameraFaceAnalysisEngine.AnalysisStatusColor.GREEN, CameraFaceAnalysisEngine.HeadPoseStatus.ACCEPTABLE.color)
    }

    @Test
    fun testLiveCameraAnalysisTelemetryValidity() {
        val validTelemetry = CameraFaceAnalysisEngine.LiveCameraAnalysisTelemetry(
            cameraAvailable = true,
            permissionGranted = true,
            frameStreamActive = true,
            frameFps = 5.5,
            lastFrameAgeMs = 120L,
            brightnessScore = 110.0,
            contrastScore = 24.0,
            lightingStatus = CameraFaceAnalysisEngine.LightingStatus.GOOD,
            faceDetectionStatus = CameraFaceAnalysisEngine.FaceDetectionStatus.DETECTED,
            faceCount = 1,
            faceBoundingBox = RectF(0.3f, 0.2f, 0.7f, 0.8f),
            leftEyeNormalized = PointF(0.42f, 0.40f),
            rightEyeNormalized = PointF(0.58f, 0.40f),
            facePositionStatus = CameraFaceAnalysisEngine.FacePositionStatus.CENTERED,
            leftEyeVisibility = CameraFaceAnalysisEngine.EyeVisibilityStatus.VISIBLE,
            rightEyeVisibility = CameraFaceAnalysisEngine.EyeVisibilityStatus.VISIBLE,
            headPoseStatus = CameraFaceAnalysisEngine.HeadPoseStatus.ACCEPTABLE,
            eulerAngles = Triple(0f, 2f, 1f),
            analysisConfidence = 0.85f,
            overallReadiness = CameraFaceAnalysisEngine.OverallReadiness.READY,
            actionableGuidance = "Camera and face analysis ready",
            timestampMs = System.currentTimeMillis()
        )
        assertTrue(validTelemetry.isValidForLiveness)

        val darkTelemetry = validTelemetry.copy(
            lightingStatus = CameraFaceAnalysisEngine.LightingStatus.TOO_DARK,
            brightnessScore = 20.0
        )
        assertFalse(darkTelemetry.isValidForLiveness)

        val noFaceTelemetry = validTelemetry.copy(
            faceDetectionStatus = CameraFaceAnalysisEngine.FaceDetectionStatus.NO_FACE,
            faceBoundingBox = null
        )
        assertFalse(noFaceTelemetry.isValidForLiveness)

        val uncenteredTelemetry = validTelemetry.copy(
            facePositionStatus = CameraFaceAnalysisEngine.FacePositionStatus.OFF_CENTER_LEFT
        )
        assertFalse(uncenteredTelemetry.isValidForLiveness)
    }

    @Test
    fun testReadinessTestSummaryEvaluation() {
        val summarySuccess = CameraFaceAnalysisEngine.ReadinessTestSummary(
            passed = true,
            testDurationSec = 10,
            totalFramesAnalyzed = 45,
            validObservationCount = 40,
            validRatio = 88.9,
            lightingPassRatio = 100.0,
            averageBrightness = 115.0,
            faceObservedRatio = 95.0,
            eyesObservedRatio = 90.0,
            failures = emptyList(),
            summaryMessage = "Passed"
        )
        assertTrue(summarySuccess.passed)
        assertEquals(0, summarySuccess.failures.size)

        val summaryFailure = CameraFaceAnalysisEngine.ReadinessTestSummary(
            passed = false,
            testDurationSec = 10,
            totalFramesAnalyzed = 45,
            validObservationCount = 10,
            validRatio = 22.2,
            lightingPassRatio = 40.0,
            averageBrightness = 30.0,
            faceObservedRatio = 50.0,
            eyesObservedRatio = 30.0,
            failures = listOf("Lighting was poor", "Face was missing during test"),
            summaryMessage = "Failed"
        )
        assertFalse(summaryFailure.passed)
        assertEquals(2, summaryFailure.failures.size)
    }
}
