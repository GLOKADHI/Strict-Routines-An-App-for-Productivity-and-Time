package com.strictrecords.app

import android.graphics.Bitmap
import android.graphics.PointF
import android.graphics.RectF
import android.util.Log
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.face.FaceDetection
import com.google.mlkit.vision.face.FaceDetectorOptions
import com.google.mlkit.vision.face.FaceLandmark
import java.util.concurrent.TimeUnit
import kotlin.math.*

/** Bundled model runs offline. Face/eye presence is not identity recognition or anti-spoofing. */
object BundledFaceAnalysis {
    private val detector by lazy {
        FaceDetection.getClient(FaceDetectorOptions.Builder()
            .setPerformanceMode(FaceDetectorOptions.PERFORMANCE_MODE_FAST)
            .setLandmarkMode(FaceDetectorOptions.LANDMARK_MODE_ALL)
            .setClassificationMode(FaceDetectorOptions.CLASSIFICATION_MODE_ALL)
            .setMinFaceSize(0.15f).enableTracking().build())
    }
    private var pending: com.google.android.gms.tasks.Task<List<com.google.mlkit.vision.face.Face>>? = null

    @Synchronized
    fun analyze(source: Bitmap, lastFrameTimestampMs: Long, fps: Double, detectionWidth: Int): CameraFaceAnalysisEngine.LiveCameraAnalysisTelemetry {
        val now = System.currentTimeMillis()
        val width = min(source.width, detectionWidth.coerceIn(320, 640))
        val height = max(2, (source.height * width.toFloat() / source.width).roundToInt())
        val scaled = Bitmap.createScaledBitmap(source, width, height, true)
        val bitmap = if (scaled === source) source.copy(Bitmap.Config.ARGB_8888, false) else scaled
        var mean = 0.0; var sumSq = 0.0; var count = 0
        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
        for (y in height / 10 until height * 9 / 10 step 3) for (x in width / 10 until width * 9 / 10 step 3) {
            val p = pixels[y * width + x]
            val luma = 0.299 * ((p shr 16) and 255) + 0.587 * ((p shr 8) and 255) + 0.114 * (p and 255)
            mean += luma; sumSq += luma * luma; count++
        }
        mean /= max(1, count)
        val contrast = sqrt(max(0.0, sumSq / max(1, count) - mean * mean))
        val lighting = when {
            mean < 45 -> CameraFaceAnalysisEngine.LightingStatus.TOO_DARK
            mean > 230 -> CameraFaceAnalysisEngine.LightingStatus.TOO_BRIGHT
            contrast < 12 -> CameraFaceAnalysisEngine.LightingStatus.POOR_CONTRAST
            else -> CameraFaceAnalysisEngine.LightingStatus.GOOD
        }
        fun absent(message: String, unavailable: Boolean = false) = CameraFaceAnalysisEngine.unavailableTelemetry(message).copy(
            cameraAvailable = true, permissionGranted = true, frameStreamActive = now - lastFrameTimestampMs in 0..750,
            frameFps = fps, lastFrameAgeMs = max(0, now - lastFrameTimestampMs), brightnessScore = mean,
            contrastScore = contrast, lightingStatus = lighting,
            faceDetectionStatus = if (unavailable) CameraFaceAnalysisEngine.FaceDetectionStatus.NOT_EVALUATED else CameraFaceAnalysisEngine.FaceDetectionStatus.NO_FACE,
            overallReadiness = CameraFaceAnalysisEngine.OverallReadiness.NOT_READY,
            analysisWidth = width, analysisHeight = height)
        try {
            if (pending?.isComplete == false) return absent("Face detector initializing; waiting for current analysis", true)
            val task = detector.process(InputImage.fromBitmap(bitmap, 0))
            pending = task
            val faces = Tasks.await(task, 2, TimeUnit.SECONDS)
            if (faces.isEmpty()) return absent(if (lighting == CameraFaceAnalysisEngine.LightingStatus.GOOD) "Position your face clearly within the camera frame" else lighting.message)
            val face = faces.maxByOrNull { it.boundingBox.width() * it.boundingBox.height() }!!
            val b = face.boundingBox
            val box = RectF(b.left.toFloat() / width, b.top.toFloat() / height, b.right.toFloat() / width, b.bottom.toFloat() / height)
            val left = face.getLandmark(FaceLandmark.LEFT_EYE)?.position
            val right = face.getLandmark(FaceLandmark.RIGHT_EYE)?.position
            val eyesDistance = if (left != null && right != null) hypot(left.x - right.x, left.y - right.y) / width else 0f
            val position = when {
                box.left <= 0 || box.top <= 0 || box.right >= 1 || box.bottom >= 1 -> CameraFaceAnalysisEngine.FacePositionStatus.OUTSIDE_FRAME
                eyesDistance < 0.11f || b.width() < 100 -> CameraFaceAnalysisEngine.FacePositionStatus.TOO_FAR
                eyesDistance > 0.42f -> CameraFaceAnalysisEngine.FacePositionStatus.TOO_CLOSE
                box.centerX() < 0.26f -> CameraFaceAnalysisEngine.FacePositionStatus.OFF_CENTER_LEFT
                box.centerX() > 0.74f -> CameraFaceAnalysisEngine.FacePositionStatus.OFF_CENTER_RIGHT
                box.centerY() !in 0.22f..0.78f -> CameraFaceAnalysisEngine.FacePositionStatus.OFF_CENTER_VERTICAL
                else -> CameraFaceAnalysisEngine.FacePositionStatus.CENTERED
            }
            fun eye(p: PointF?, open: Float?) = when {
                p == null -> CameraFaceAnalysisEngine.EyeVisibilityStatus.UNAVAILABLE
                open != null && open < 0.20f -> CameraFaceAnalysisEngine.EyeVisibilityStatus.PARTIALLY_OBSCURED
                else -> CameraFaceAnalysisEngine.EyeVisibilityStatus.VISIBLE
            }
            val le = eye(left, face.leftEyeOpenProbability); val re = eye(right, face.rightEyeOpenProbability)
            val head = when {
                abs(face.headEulerAngleY) > 24 -> CameraFaceAnalysisEngine.HeadPoseStatus.LOOK_FORWARD
                abs(face.headEulerAngleX) > 24 || abs(face.headEulerAngleZ) > 22 -> CameraFaceAnalysisEngine.HeadPoseStatus.HEAD_TILTED
                else -> CameraFaceAnalysisEngine.HeadPoseStatus.ACCEPTABLE
            }
            val single = faces.size == 1
            val ready = single && lighting == CameraFaceAnalysisEngine.LightingStatus.GOOD && position == CameraFaceAnalysisEngine.FacePositionStatus.CENTERED &&
                head == CameraFaceAnalysisEngine.HeadPoseStatus.ACCEPTABLE && (le == CameraFaceAnalysisEngine.EyeVisibilityStatus.VISIBLE || re == CameraFaceAnalysisEngine.EyeVisibilityStatus.VISIBLE)
            val message = when {
                !single -> "Only one face should be in the frame"
                lighting != CameraFaceAnalysisEngine.LightingStatus.GOOD -> lighting.message
                position != CameraFaceAnalysisEngine.FacePositionStatus.CENTERED -> position.message
                head != CameraFaceAnalysisEngine.HeadPoseStatus.ACCEPTABLE -> head.message
                !ready -> "Keep eyes visible and face the camera"
                else -> "Camera and face presence analysis ready"
            }
            return absent(message).copy(faceDetectionStatus = if (single) CameraFaceAnalysisEngine.FaceDetectionStatus.DETECTED else CameraFaceAnalysisEngine.FaceDetectionStatus.MULTIPLE_FACES,
                faceCount = faces.size, faceBoundingBox = box, facePositionStatus = position,
                leftEyeNormalized = left?.let { PointF(it.x / width, it.y / height) }, rightEyeNormalized = right?.let { PointF(it.x / width, it.y / height) },
                leftEyeVisibility = le, rightEyeVisibility = re, headPoseStatus = head,
                eulerAngles = Triple(face.headEulerAngleY, face.headEulerAngleX, face.headEulerAngleZ),
                // ML Kit does not expose a general face confidence. Do not invent one.
                analysisConfidence = 0f, overallReadiness = if (ready) CameraFaceAnalysisEngine.OverallReadiness.READY else CameraFaceAnalysisEngine.OverallReadiness.NEEDS_ADJUSTMENT)
        } catch (e: Throwable) {
            Log.e("StrictWakeCamera", "detector_failure=${e.javaClass.simpleName}")
            return absent("Face analysis unavailable; waiting for a fresh detector result", true)
        } finally {
            // InputImage may still be in use after timeout. Recycle only when its task finishes.
            val task = pending
            if (bitmap !== source) {
                if (task != null && !task.isComplete) task.addOnCompleteListener { bitmap.recycle() } else bitmap.recycle()
            }
        }
    }
}
