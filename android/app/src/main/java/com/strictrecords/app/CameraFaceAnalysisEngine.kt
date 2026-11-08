package com.strictrecords.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.PointF
import android.graphics.RectF
import android.graphics.SurfaceTexture
import android.graphics.Typeface
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.GradientDrawable
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.media.FaceDetector
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.util.TypedValue
import android.view.Gravity
import android.view.Surface
import android.view.TextureView
import android.view.View
import android.view.ViewGroup
import android.view.ViewOutlineProvider
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * STRICTRECORDS — REAL-TIME CAMERA AND FACE ANALYSIS ENGINE
 *
 * Implements genuine on-device camera preview, lighting estimation, 2D face detection,
 * eye landmark visibility, head pose orientation, real-time bounding box overlay,
 * and live 10-second readiness testing.
 *
 * Fully compatible with older devices (Android 7.1.1 / API 25) and modern devices (Android 14 / One UI 6).
 * Shared across Settings -> Camera Check and Wake-Up Verification Step 1.
 */
object CameraFaceAnalysisEngine {

    // =========================================================================
    // 1. DATA MODELS & TELEMETRY
    // =========================================================================

    enum class AnalysisStatusColor(val hex: String) {
        GREEN("#10B981"),
        AMBER("#F59E0B"),
        RED("#EF4444"),
        GRAY("#64748B")
    }

    enum class LightingStatus(val label: String, val color: AnalysisStatusColor, val message: String) {
        GOOD("GOOD", AnalysisStatusColor.GREEN, "Lighting looks good"),
        TOO_DARK("TOO DARK", AnalysisStatusColor.AMBER, "Too dark — turn on a light or face a brighter area"),
        TOO_BRIGHT("TOO BRIGHT", AnalysisStatusColor.AMBER, "Too bright — avoid harsh glare"),
        POOR_CONTRAST("POOR CONTRAST", AnalysisStatusColor.AMBER, "Poor contrast — face a light source"),
        NOT_EVALUATED("NOT EVALUATED", AnalysisStatusColor.GRAY, "Waiting for camera preview")
    }

    enum class FaceDetectionStatus(val label: String, val color: AnalysisStatusColor) {
        DETECTED("DETECTED", AnalysisStatusColor.GREEN),
        NO_FACE("NO FACE", AnalysisStatusColor.RED),
        MULTIPLE_FACES("MULTIPLE", AnalysisStatusColor.AMBER),
        NOT_EVALUATED("NOT EVALUATED", AnalysisStatusColor.GRAY)
    }

    enum class FacePositionStatus(val label: String, val color: AnalysisStatusColor, val message: String) {
        CENTERED("CENTERED", AnalysisStatusColor.GREEN, "Face centered"),
        TOO_CLOSE("TOO CLOSE", AnalysisStatusColor.AMBER, "Move slightly back"),
        TOO_FAR("TOO FAR", AnalysisStatusColor.AMBER, "Move slightly closer"),
        OFF_CENTER_LEFT("OFF-CENTER LEFT", AnalysisStatusColor.AMBER, "Center your face"),
        OFF_CENTER_RIGHT("OFF-CENTER RIGHT", AnalysisStatusColor.AMBER, "Center your face"),
        OFF_CENTER_VERTICAL("OFF-CENTER", AnalysisStatusColor.AMBER, "Center your face in frame"),
        OUTSIDE_FRAME("OUTSIDE FRAME", AnalysisStatusColor.RED, "Face partially outside frame"),
        NOT_EVALUATED("NOT EVALUATED", AnalysisStatusColor.GRAY, "Waiting for face")
    }

    enum class EyeVisibilityStatus(val label: String, val color: AnalysisStatusColor) {
        VISIBLE("VISIBLE", AnalysisStatusColor.GREEN),
        PARTIALLY_OBSCURED("OBSCURED", AnalysisStatusColor.AMBER),
        UNAVAILABLE("UNAVAILABLE", AnalysisStatusColor.RED),
        NOT_EVALUATED("NOT EVALUATED", AnalysisStatusColor.GRAY)
    }

    enum class HeadPoseStatus(val label: String, val color: AnalysisStatusColor, val message: String) {
        ACCEPTABLE("ACCEPTABLE", AnalysisStatusColor.GREEN, "Head pose is level"),
        HEAD_TILTED("TILTED", AnalysisStatusColor.AMBER, "Keep head facing camera directly"),
        LOOK_FORWARD("TURNED AWAY", AnalysisStatusColor.AMBER, "Look directly towards camera"),
        NOT_EVALUATED("NOT EVALUATED", AnalysisStatusColor.GRAY, "Waiting for face")
    }

    enum class OverallReadiness(val label: String, val color: AnalysisStatusColor) {
        READY("READY", AnalysisStatusColor.GREEN),
        NEEDS_ADJUSTMENT("NEEDS ADJUSTMENT", AnalysisStatusColor.AMBER),
        NOT_READY("NOT READY", AnalysisStatusColor.RED),
        UNAVAILABLE("UNAVAILABLE", AnalysisStatusColor.RED),
        INITIALIZING("INITIALIZING", AnalysisStatusColor.GRAY)
    }

    data class LiveCameraAnalysisTelemetry(
        val cameraAvailable: Boolean,
        val permissionGranted: Boolean,
        val frameStreamActive: Boolean,
        val frameFps: Double,
        val lastFrameAgeMs: Long,
        val brightnessScore: Double,
        val contrastScore: Double,
        val lightingStatus: LightingStatus,
        val faceDetectionStatus: FaceDetectionStatus,
        val faceCount: Int,
        val faceBoundingBox: RectF?, // in normalized coordinates [0..1]
        val leftEyeNormalized: PointF?,
        val rightEyeNormalized: PointF?,
        val facePositionStatus: FacePositionStatus,
        val leftEyeVisibility: EyeVisibilityStatus,
        val rightEyeVisibility: EyeVisibilityStatus,
        val headPoseStatus: HeadPoseStatus,
        val eulerAngles: Triple<Float, Float, Float>?, // yaw, pitch, roll in degrees
        val analysisConfidence: Float,
        val overallReadiness: OverallReadiness,
        val actionableGuidance: String,
        val timestampMs: Long,
        val frameId: Long = 0L,
        val capturedElapsedMs: Long = 0L,
        val previewContentRect: RectF? = null,
        val analysisWidth: Int = 0,
        val analysisHeight: Int = 0
    ) {
        val isValidForLiveness: Boolean
            get() = cameraAvailable && frameStreamActive &&
                    lightingStatus == LightingStatus.GOOD &&
                    faceDetectionStatus == FaceDetectionStatus.DETECTED &&
                    facePositionStatus == FacePositionStatus.CENTERED &&
                    (leftEyeVisibility == EyeVisibilityStatus.VISIBLE || rightEyeVisibility == EyeVisibilityStatus.VISIBLE) &&
                    headPoseStatus == HeadPoseStatus.ACCEPTABLE
    }

    data class ReadinessTestSummary(
        val passed: Boolean,
        val testDurationSec: Int,
        val totalFramesAnalyzed: Int,
        val validObservationCount: Int,
        val validRatio: Double,
        val lightingPassRatio: Double,
        val averageBrightness: Double,
        val faceObservedRatio: Double,
        val eyesObservedRatio: Double,
        val failures: List<String>,
        val summaryMessage: String
    )

    // Camera Ownership Guard to prevent dual camera open collisions
    @Volatile
    private var currentCameraOwner: String? = null

    fun isCameraInUse(): Boolean = WakeUpCameraSession.inUse()

    // =========================================================================
    // 2. ON-DEVICE FRAME ANALYSIS ALGORITHM
    // =========================================================================

    private const val LOW_LIGHT_THRESHOLD = 45.0
    private const val HIGH_LIGHT_THRESHOLD = 230.0
    private const val LOW_CONTRAST_THRESHOLD = 12.0

    /**
     * Analyzes an input preview Bitmap for:
     * - Mean luminance and local contrast
     * - Built-in Android FaceDetector presence, confidence, bounding box
     * - Centering and distance suitability
     * - Eye landmark regions contrast (left & right eye visibility)
     * - Head pose estimation (Euler angles yaw, pitch, roll)
     */
    fun analyzeFrame(sourceBitmap: Bitmap, lastFrameTimestampMs: Long, recentFps: Double = 5.0, detectionWidth: Int = 480): LiveCameraAnalysisTelemetry =
        BundledFaceAnalysis.analyze(sourceBitmap, lastFrameTimestampMs, recentFps, detectionWidth)

    // Retained only for instrumented reproduction comparisons. Production uses the bundled detector.
    fun analyzeLegacyFrame(
        sourceBitmap: Bitmap,
        lastFrameTimestampMs: Long,
        recentFps: Double = 5.0,
        detectionWidth: Int = 480
    ): LiveCameraAnalysisTelemetry {
        val now = System.currentTimeMillis()
        val frameAge = if (lastFrameTimestampMs > 0L) max(0L, now - lastFrameTimestampMs) else 0L

        // Preserve input aspect ratio. The legacy detector requires RGB_565 and even width.
        val requestedW = min(detectionWidth.coerceIn(160, 640), sourceBitmap.width).coerceAtLeast(2)
        val targetW = requestedW - requestedW % 2
        val targetH = max(2, (sourceBitmap.height * targetW.toFloat() / sourceBitmap.width).roundToInt())

        val scaledArgb = Bitmap.createScaledBitmap(sourceBitmap, targetW, targetH, true)
        val w = scaledArgb.width
        val h = scaledArgb.height
        val pixels = IntArray(w * h)
        scaledArgb.getPixels(pixels, 0, w, 0, 0, w, h)

        // 1. Illumination & Contrast Analysis
        var lumaSum = 0.0
        var lumaSqSum = 0.0
        var lumaCount = 0
        val startX = (w * 0.1f).toInt()
        val endX = (w * 0.9f).toInt()
        val startY = (h * 0.1f).toInt()
        val endY = (h * 0.9f).toInt()
        for (y in startY until endY step 2) {
            val rowOffset = y * w
            for (x in startX until endX step 2) {
                val c = pixels[rowOffset + x]
                val r = (c shr 16) and 0xFF
                val g = (c shr 8) and 0xFF
                val b = c and 0xFF
                val luma = 0.299 * r + 0.587 * g + 0.114 * b
                lumaSum += luma
                lumaSqSum += luma * luma
                lumaCount++
            }
        }

        val brightness = if (lumaCount > 0) lumaSum / lumaCount else 0.0
        val meanSq = if (lumaCount > 0) lumaSqSum / lumaCount else 0.0
        val variance = max(0.0, meanSq - (brightness * brightness))
        val contrast = kotlin.math.sqrt(variance)

        val lightingStatus = when {
            brightness < LOW_LIGHT_THRESHOLD -> LightingStatus.TOO_DARK
            brightness > HIGH_LIGHT_THRESHOLD -> LightingStatus.TOO_BRIGHT
            contrast < LOW_CONTRAST_THRESHOLD -> LightingStatus.POOR_CONTRAST
            else -> LightingStatus.GOOD
        }

        // 2. Built-in Face Detector Execution
        val rgb565 = scaledArgb.copy(Bitmap.Config.RGB_565, false)
        val detector = FaceDetector(w, h, 2)
        val faces = arrayOfNulls<FaceDetector.Face>(2)
        val facesCount = try {
            detector.findFaces(rgb565, faces)
        } catch (_: Throwable) {
            0
        }
        rgb565.recycle()

        // If low light or zero faces detected
        if (facesCount == 0 || faces[0] == null || faces[0]!!.confidence() < 0.28f) {
            scaledArgb.recycle()
            val faceStatus = if (facesCount > 1) FaceDetectionStatus.MULTIPLE_FACES else FaceDetectionStatus.NO_FACE
            val guidance = if (lightingStatus != LightingStatus.GOOD) {
                lightingStatus.message
            } else {
                "Position your face clearly within the camera frame"
            }

            return LiveCameraAnalysisTelemetry(
                cameraAvailable = true,
                permissionGranted = true,
                frameStreamActive = frameAge < 2500L,
                frameFps = recentFps,
                lastFrameAgeMs = frameAge,
                brightnessScore = brightness,
                contrastScore = contrast,
                lightingStatus = lightingStatus,
                faceDetectionStatus = faceStatus,
                faceCount = facesCount,
                faceBoundingBox = null,
                leftEyeNormalized = null,
                rightEyeNormalized = null,
                facePositionStatus = FacePositionStatus.NOT_EVALUATED,
                leftEyeVisibility = EyeVisibilityStatus.UNAVAILABLE,
                rightEyeVisibility = EyeVisibilityStatus.UNAVAILABLE,
                headPoseStatus = HeadPoseStatus.NOT_EVALUATED,
                eulerAngles = null,
                analysisConfidence = faces[0]?.confidence() ?: 0f,
                overallReadiness = if (lightingStatus == LightingStatus.GOOD) OverallReadiness.NOT_READY else OverallReadiness.NEEDS_ADJUSTMENT,
                actionableGuidance = guidance,
                timestampMs = now
            )
        }

        val primaryFace = faces[0]!!
        val confidence = primaryFace.confidence()
        val mid = PointF()
        primaryFace.getMidPoint(mid)
        val eyesDist = primaryFace.eyesDistance()

        // Euler Angles (Yaw, Pitch, Roll)
        val pitch = try { primaryFace.pose(FaceDetector.Face.EULER_X) } catch (_: Throwable) { 0f }
        val yaw = try { primaryFace.pose(FaceDetector.Face.EULER_Y) } catch (_: Throwable) { 0f }
        val roll = try { primaryFace.pose(FaceDetector.Face.EULER_Z) } catch (_: Throwable) { 0f }

        val headPose = when {
            abs(yaw) > 24f -> HeadPoseStatus.LOOK_FORWARD
            abs(pitch) > 24f || abs(roll) > 22f -> HeadPoseStatus.HEAD_TILTED
            else -> HeadPoseStatus.ACCEPTABLE
        }

        // Bounding Box Calculation in Normalized Coordinates [0..1]
        val halfW = eyesDist * 1.15f
        val topH = eyesDist * 1.25f
        val botH = eyesDist * 1.55f
        val leftPx = max(0f, mid.x - halfW)
        val rightPx = min(w.toFloat(), mid.x + halfW)
        val topPx = max(0f, mid.y - topH)
        val botPx = min(h.toFloat(), mid.y + botH)

        val normBox = RectF(
            leftPx / w.toFloat(),
            topPx / h.toFloat(),
            rightPx / w.toFloat(),
            botPx / h.toFloat()
        )

        val leftEyeNorm = PointF((mid.x - eyesDist * 0.5f) / w.toFloat(), mid.y / h.toFloat())
        val rightEyeNorm = PointF((mid.x + eyesDist * 0.5f) / w.toFloat(), mid.y / h.toFloat())

        // Distance & Centering Evaluation
        val eyesDistRatio = eyesDist / w.toFloat()
        val isOffLeft = mid.x < w * 0.26f
        val isOffRight = mid.x > w * 0.74f
        val isOffTop = mid.y < h * 0.22f
        val isOffBot = mid.y > h * 0.78f
        val isOutside = leftPx <= 1f || rightPx >= (w - 1f) || topPx <= 1f || botPx >= (h - 1f)

        val posStatus = when {
            isOutside -> FacePositionStatus.OUTSIDE_FRAME
            eyesDistRatio < 0.11f -> FacePositionStatus.TOO_FAR
            eyesDistRatio > 0.42f -> FacePositionStatus.TOO_CLOSE
            isOffLeft -> FacePositionStatus.OFF_CENTER_LEFT
            isOffRight -> FacePositionStatus.OFF_CENTER_RIGHT
            isOffTop || isOffBot -> FacePositionStatus.OFF_CENTER_VERTICAL
            else -> FacePositionStatus.CENTERED
        }

        // 3. Eye Landmark Visibility via Regional Contrast
        val halfEyeDist = eyesDist * 0.5f
        val eyeRadiusX = max(4, (eyesDist * 0.22f).roundToInt())
        val eyeRadiusY = max(3, (eyesDist * 0.17f).roundToInt())

        val leftEyeContrast = computeRegionContrast(
            pixels, w, h,
            (mid.x - halfEyeDist).roundToInt(),
            mid.y.roundToInt(),
            eyeRadiusX, eyeRadiusY
        )
        val rightEyeContrast = computeRegionContrast(
            pixels, w, h,
            (mid.x + halfEyeDist).roundToInt(),
            mid.y.roundToInt(),
            eyeRadiusX, eyeRadiusY
        )
        scaledArgb.recycle()

        val leftEyeVis = when {
            leftEyeContrast >= 8.5 -> EyeVisibilityStatus.VISIBLE
            leftEyeContrast >= 4.8 -> EyeVisibilityStatus.PARTIALLY_OBSCURED
            else -> EyeVisibilityStatus.UNAVAILABLE
        }
        val rightEyeVis = when {
            rightEyeContrast >= 8.5 -> EyeVisibilityStatus.VISIBLE
            rightEyeContrast >= 4.8 -> EyeVisibilityStatus.PARTIALLY_OBSCURED
            else -> EyeVisibilityStatus.UNAVAILABLE
        }

        val faceDetectedStatus = if (facesCount > 1) FaceDetectionStatus.MULTIPLE_FACES else FaceDetectionStatus.DETECTED

        // Actionable guidance priority
        val guidance = when {
            lightingStatus == LightingStatus.TOO_DARK -> LightingStatus.TOO_DARK.message
            lightingStatus == LightingStatus.TOO_BRIGHT -> LightingStatus.TOO_BRIGHT.message
            posStatus != FacePositionStatus.CENTERED -> posStatus.message
            headPose != HeadPoseStatus.ACCEPTABLE -> headPose.message
            leftEyeVis == EyeVisibilityStatus.UNAVAILABLE && rightEyeVis == EyeVisibilityStatus.UNAVAILABLE -> "Keep eyes open and visible to the camera"
            else -> "Camera and face analysis ready"
        }

        val isReady = lightingStatus == LightingStatus.GOOD &&
                faceDetectedStatus == FaceDetectionStatus.DETECTED &&
                posStatus == FacePositionStatus.CENTERED &&
                headPose == HeadPoseStatus.ACCEPTABLE &&
                (leftEyeVis == EyeVisibilityStatus.VISIBLE || rightEyeVis == EyeVisibilityStatus.VISIBLE)

        val overall = when {
            isReady -> OverallReadiness.READY
            lightingStatus != LightingStatus.GOOD || posStatus != FacePositionStatus.CENTERED || headPose != HeadPoseStatus.ACCEPTABLE -> OverallReadiness.NEEDS_ADJUSTMENT
            else -> OverallReadiness.NOT_READY
        }

        return LiveCameraAnalysisTelemetry(
            cameraAvailable = true,
            permissionGranted = true,
            frameStreamActive = frameAge < 2500L,
            frameFps = recentFps,
            lastFrameAgeMs = frameAge,
            brightnessScore = brightness,
            contrastScore = contrast,
            lightingStatus = lightingStatus,
            faceDetectionStatus = faceDetectedStatus,
            faceCount = facesCount,
            faceBoundingBox = normBox,
            leftEyeNormalized = leftEyeNorm,
            rightEyeNormalized = rightEyeNorm,
            facePositionStatus = posStatus,
            leftEyeVisibility = leftEyeVis,
            rightEyeVisibility = rightEyeVis,
            headPoseStatus = headPose,
            eulerAngles = Triple(yaw, pitch, roll),
            analysisConfidence = confidence,
            overallReadiness = overall,
            actionableGuidance = guidance,
            timestampMs = now
        )
    }

    private fun computeRegionContrast(
        pixels: IntArray,
        w: Int,
        h: Int,
        cx: Int,
        cy: Int,
        rx: Int,
        ry: Int
    ): Double {
        val x0 = max(0, cx - rx)
        val x1 = min(w - 1, cx + rx)
        val y0 = max(0, cy - ry)
        val y1 = min(h - 1, cy + ry)
        if (x1 <= x0 || y1 <= y0) return 0.0

        var sum = 0.0
        var sumSq = 0.0
        var count = 0
        for (y in y0..y1) {
            val row = y * w
            for (x in x0..x1) {
                val c = pixels[row + x]
                val r = (c shr 16) and 0xFF
                val g = (c shr 8) and 0xFF
                val b = c and 0xFF
                val luma = 0.299 * r + 0.587 * g + 0.114 * b
                sum += luma
                sumSq += luma * luma
                count++
            }
        }
        if (count <= 1) return 0.0
        val mean = sum / count
        val variance = max(0.0, (sumSq / count) - (mean * mean))
        return kotlin.math.sqrt(variance)
    }

    // =========================================================================
    // 3. DYNAMIC FACE FRAME OVERLAY (Real-time tracking bounding box)
    // =========================================================================

    class FaceOverlayView(context: Context) : View(context) {
        private val boxPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = 4f
            color = Color.parseColor("#10B981")
        }

        private val eyePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = 3f
            color = Color.parseColor("#38BDF8")
        }

        private val guidePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = 3f
            color = Color.parseColor("#4B5563")
        }

        private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.parseColor("#CCFFFFFF")
            textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 11f, resources.displayMetrics)
            textAlign = Paint.Align.CENTER
            typeface = Typeface.DEFAULT_BOLD
        }

        private var targetBox: RectF? = null
        private var currentBox: RectF? = null
        private var leftEye: PointF? = null
        private var rightEye: PointF? = null
        private var statusColor = AnalysisStatusColor.GRAY
        private var statusMsg = "Align face in preview"

        fun updateTelemetry(telemetry: LiveCameraAnalysisTelemetry) {
            statusColor = telemetry.overallReadiness.color
            statusMsg = telemetry.actionableGuidance

            val rect = telemetry.previewContentRect ?: RectF(0f, 0f, 1f, 1f)
            fun mapX(x: Float) = (rect.left + x * rect.width()) * width
            fun mapY(y: Float) = (rect.top + y * rect.height()) * height
            val box = telemetry.faceBoundingBox
            if (box != null) {
                targetBox = RectF(mapX(box.left), mapY(box.top), mapX(box.right), mapY(box.bottom))
                val le = telemetry.leftEyeNormalized
                leftEye = if (le != null) PointF(mapX(le.x), mapY(le.y)) else null
                val re = telemetry.rightEyeNormalized
                rightEye = if (re != null) PointF(mapX(re.x), mapY(re.y)) else null
            } else {
                targetBox = null
                leftEye = null
                rightEye = null
            }
            boxPaint.color = Color.parseColor(statusColor.hex)
            invalidate()
        }

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            val w = width.toFloat()
            val h = height.toFloat()
            if (w <= 0f || h <= 0f) return

            val tgt = targetBox
            if (tgt != null) {
                // Smooth moving bounding box via exponential interpolation
                val cur = currentBox
                if (cur == null) {
                    currentBox = RectF(tgt)
                } else {
                    cur.left = cur.left * 0.60f + tgt.left * 0.40f
                    cur.top = cur.top * 0.60f + tgt.top * 0.40f
                    cur.right = cur.right * 0.60f + tgt.right * 0.40f
                    cur.bottom = cur.bottom * 0.60f + tgt.bottom * 0.40f
                }

                currentBox?.let { b ->
                    // Draw rounded dynamic face box
                    canvas.drawRoundRect(b, 16f, 16f, boxPaint)

                    // Draw corner accent brackets
                    val cornerLen = min(b.width(), b.height()) * 0.22f
                    boxPaint.strokeWidth = 7f
                    // Top-Left
                    canvas.drawLine(b.left, b.top, b.left + cornerLen, b.top, boxPaint)
                    canvas.drawLine(b.left, b.top, b.left, b.top + cornerLen, boxPaint)
                    // Top-Right
                    canvas.drawLine(b.right - cornerLen, b.top, b.right, b.top, boxPaint)
                    canvas.drawLine(b.right, b.top, b.right, b.top + cornerLen, boxPaint)
                    // Bottom-Left
                    canvas.drawLine(b.left, b.bottom - cornerLen, b.left, b.bottom, boxPaint)
                    canvas.drawLine(b.left, b.bottom, b.left + cornerLen, b.bottom, boxPaint)
                    // Bottom-Right
                    canvas.drawLine(b.right - cornerLen, b.bottom, b.right, b.bottom, boxPaint)
                    canvas.drawLine(b.right, b.bottom - cornerLen, b.right, b.bottom, boxPaint)
                    boxPaint.strokeWidth = 4f

                    // Draw Eye Landmark Markers
                    leftEye?.let { canvas.drawCircle(it.x, it.y, 7f, eyePaint) }
                    rightEye?.let { canvas.drawCircle(it.x, it.y, 7f, eyePaint) }
                }
            } else {
                currentBox = null
                // Default subtle center frame guideline
                val gw = w * 0.54f
                val gh = h * 0.66f
                val gx = (w - gw) / 2f
                val gy = (h - gh) / 2f
                canvas.drawRoundRect(RectF(gx, gy, gx + gw, gy + gh), 22f, 22f, guidePaint)
                canvas.drawText("[ Face Placement Guide ]", w / 2f, gy + gh - 16f, textPaint)
            }
        }
    }

    // =========================================================================
    // 4. CAMERA2 SESSION MANAGER (Handles Front Camera, Mirroring, Lifecycle)
    // =========================================================================

    class LiveCameraSession(
        activity: Activity,
        ownerTag: String,
        textureView: TextureView,
        onTelemetryUpdate: (LiveCameraAnalysisTelemetry) -> Unit
    ) {
        private val session = WakeUpCameraSession(activity, ownerTag, textureView, onTelemetryUpdate)
        fun start() = session.start()
        fun stop() = session.stop()
    }

    fun unavailableTelemetry(guidance: String, initializing: Boolean = false) = LiveCameraAnalysisTelemetry(
        cameraAvailable = false, permissionGranted = false, frameStreamActive = false,
        frameFps = 0.0, lastFrameAgeMs = 0, brightnessScore = 0.0, contrastScore = 0.0,
        lightingStatus = LightingStatus.NOT_EVALUATED, faceDetectionStatus = FaceDetectionStatus.NOT_EVALUATED,
        faceCount = 0, faceBoundingBox = null, leftEyeNormalized = null, rightEyeNormalized = null,
        facePositionStatus = FacePositionStatus.NOT_EVALUATED, leftEyeVisibility = EyeVisibilityStatus.NOT_EVALUATED,
        rightEyeVisibility = EyeVisibilityStatus.NOT_EVALUATED, headPoseStatus = HeadPoseStatus.NOT_EVALUATED,
        eulerAngles = null, analysisConfidence = 0f,
        overallReadiness = if (initializing) OverallReadiness.INITIALIZING else OverallReadiness.UNAVAILABLE,
        actionableGuidance = guidance, timestampMs = System.currentTimeMillis()
    )

    // =========================================================================
    // 5. LIVE 10-SECOND READINESS TEST RUNNER
    // =========================================================================

    class LiveReadinessTestRunner(
        private val activity: Activity,
        private val testDurationSec: Int = 10,
        private val onProgress: (secLeft: Int, telemetry: LiveCameraAnalysisTelemetry) -> Unit,
        private val onComplete: (ReadinessTestSummary) -> Unit
    ) {
        private val ui = Handler(Looper.getMainLooper())
        private var active = false
        private var policy = WakeUpCameraPolicy.Session(testDurationSec * 1000L)
        private var lightFrames = 0
        private var faceFrames = 0
        private var eyeFrames = 0
        private var luma = 0.0
        private var lastId = 0L
        private var diagnosticFrames = 0
        private var previousState = WakeUpCameraPolicy.State.INITIALIZING
        private val timeout = Runnable { if (active) finish(listOf("Verification did not complete before the session timeout")) }
        fun startTest() {
            active = true; policy = WakeUpCameraPolicy.Session(testDurationSec * 1000L)
            lightFrames = 0; faceFrames = 0; eyeFrames = 0; luma = 0.0; lastId = 0; diagnosticFrames = 0
            previousState = WakeUpCameraPolicy.State.INITIALIZING
            ui.postDelayed(timeout, WakeUpCameraPolicy.MAX_SESSION_MS)
            android.util.Log.i("StrictWakeCamera", "demo_start requiredValidMs=${testDurationSec * 1000L}")
        }
        fun onIncomingFrame(t: LiveCameraAnalysisTelemetry) {
            if (!active) return
            val now = android.os.SystemClock.elapsedRealtime()
            if (!t.frameStreamActive || t.frameId <= 0) {
                policy.pause(t.actionableGuidance, now)
                logPolicyTransition()
                onProgress(max(0, testDurationSec - (policy.validMs / 1000).toInt()), t)
                return
            }
            if (t.frameId <= lastId) return
            lastId = t.frameId
            val result = policy.observe(t.frameId, t.capturedElapsedMs, now, t.isValidForLiveness)
            logPolicyTransition()
            diagnosticFrames++
            luma += t.brightnessScore
            if (t.lightingStatus == LightingStatus.GOOD) lightFrames++
            if (t.faceDetectionStatus == FaceDetectionStatus.DETECTED) faceFrames++
            if (t.leftEyeVisibility == EyeVisibilityStatus.VISIBLE || t.rightEyeVisibility == EyeVisibilityStatus.VISIBLE) eyeFrames++
            onProgress(max(0, testDurationSec - (policy.validMs / 1000).toInt()), t)
            if (result.passed) {
                val savedValidation = if (testDurationSec == 20) WakeUpCameraPolicy.validateMetadata(policy.metadata(now, t)) else result
                android.util.Log.i("StrictWakeCamera", "demo_complete live=${result.passed} saved=${savedValidation.passed} validMs=${policy.validMs} valid=${policy.validSamples} total=${policy.eligibleSamples}")
                finish(savedValidation.reasons)
            } else if (policy.state == WakeUpCameraPolicy.State.FAILED) finish(listOf(policy.message))
        }
        fun cancelTest() { active = false; ui.removeCallbacks(timeout) }
        private fun logPolicyTransition() {
            if (previousState != policy.state) {
                android.util.Log.i("StrictWakeCamera", "demo_state=${policy.state} validMs=${policy.validMs} valid=${policy.validSamples} total=${policy.eligibleSamples} reason=${policy.message}")
                previousState = policy.state
            }
        }
        private fun finish(failures: List<String>) {
            if (!active) return
            active = false; ui.removeCallbacks(timeout)
            val count = policy.eligibleSamples
            fun percent(n: Int) = if (diagnosticFrames > 0) n * 100.0 / diagnosticFrames else 0.0
            val passed = failures.isEmpty()
            onComplete(ReadinessTestSummary(passed, testDurationSec, count, policy.validSamples,
                if (count > 0) policy.validSamples * 100.0 / count else 0.0, percent(lightFrames), if (diagnosticFrames > 0) luma / diagnosticFrames else 0.0,
                percent(faceFrames), percent(eyeFrames), failures,
                if (passed) "${testDurationSec}s of valid camera presence verified under the shared 70% policy. Demo only; no attendance or points."
                else "Camera check incomplete: ${failures.joinToString("; ")}. No attendance or points."))
        }
    }

    // =========================================================================
    // 6. SETTINGS CAMERA & FACE CHECK INTERFACE
    // =========================================================================

    /**
     * Renders the dedicated Camera & Face Check Dialog compliant with Section 5:
     * - Live Camera Preview with FaceOverlayView
     * - Telemetry grid: Camera, Face Detection, Face Position, Lighting, Left Eye, Right Eye, Head Position, Frame Updates, Overall Readiness
     * - Action buttons: [ Run 10-Second Check ], [ Retest Camera ], [ Close ]
     */
    fun showCameraAndFaceCheckDialog(activity: Activity, testDurationSec: Int = 10, onAuditComplete: ((ReadinessTestSummary) -> Unit)? = null) {
        val dp = { v: Int ->
            TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), activity.resources.displayMetrics).toInt()
        }

        var cameraSession: LiveCameraSession? = null
        var testRunner: LiveReadinessTestRunner? = null
        var isTestRunning = false

        val root = FrameLayout(activity).apply {
            setBackgroundColor(Color.parseColor("#070A0F"))
            setPadding(dp(14), dp(14), dp(14), dp(14))
        }

        val scroll = ScrollView(activity).apply {
            isVerticalScrollBarEnabled = false
            overScrollMode = View.OVER_SCROLL_NEVER
        }
        val content = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
        }
        scroll.addView(content)
        root.addView(scroll)

        // Header Title
        content.addView(TextView(activity).apply {
            text = "CAMERA & FACE CHECK"
            textSize = 16f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(0, dp(4), 0, dp(2))
            letterSpacing = 0.06f
        })

        content.addView(TextView(activity).apply {
            text = "Real camera frames, lighting and 2D face presence. Demo only; no points or attendance. This does not verify identity or prevent spoofing."
            textSize = 11f
            setTextColor(Color.parseColor("#94A3B8"))
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(0, 0, 0, dp(12))
        })

        // Preview Container
        val previewContainer = FrameLayout(activity).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(230)
            ).apply { bottomMargin = dp(10) }
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#0A0E17"))
                cornerRadius = dp(14).toFloat()
                setStroke(dp(1), Color.parseColor("#1F2937"))
            }
            clipToOutline = true
            outlineProvider = ViewOutlineProvider.BACKGROUND
        }

        val textureView = TextureView(activity).apply {
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        }
        val overlayView = FaceOverlayView(activity).apply {
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        }

        previewContainer.addView(textureView)
        previewContainer.addView(overlayView)
        content.addView(previewContainer)

        // Actionable guidance pill
        val guidanceTv = TextView(activity).apply {
            text = "Initializing front camera..."
            textSize = 11.5f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor("#38BDF8"))
            gravity = Gravity.CENTER
            setPadding(dp(10), dp(7), dp(10), dp(7))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#111827"))
                cornerRadius = dp(8).toFloat()
                setStroke(dp(1), Color.parseColor("#1F2937"))
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(12) }
        }
        content.addView(guidanceTv)

        // Telemetry Grid
        val gridCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(10), dp(12), dp(10))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#0F172A"))
                cornerRadius = dp(12).toFloat()
                setStroke(dp(1), Color.parseColor("#1E293B"))
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(14) }
        }

        fun makeTelemetryRow(label: String): Pair<TextView, TextView> {
            val row = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, dp(4), 0, dp(4))
            }
            val titleTv = TextView(activity).apply {
                text = label
                textSize = 11.5f
                setTextColor(Color.parseColor("#94A3B8"))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            }
            val valTv = TextView(activity).apply {
                text = "CHECKING..."
                textSize = 11f
                typeface = Typeface.DEFAULT_BOLD
                setTextColor(Color.parseColor(AnalysisStatusColor.GRAY.hex))
                gravity = Gravity.END
            }
            row.addView(titleTv)
            row.addView(valTv)
            gridCard.addView(row)
            return Pair(titleTv, valTv)
        }

        val rowCamera = makeTelemetryRow("Camera")
        val rowFace = makeTelemetryRow("Face Detection")
        val rowPos = makeTelemetryRow("Face Position")
        val rowLight = makeTelemetryRow("Lighting")
        val rowLeftEye = makeTelemetryRow("Left Eye")
        val rowRightEye = makeTelemetryRow("Right Eye")
        val rowHead = makeTelemetryRow("Head Position")
        val rowUpdates = makeTelemetryRow("Frame Updates")

        // Overall Readiness Row
        val divider = View(activity).apply {
            setBackgroundColor(Color.parseColor("#1E293B"))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(1)).apply {
                topMargin = dp(6)
                bottomMargin = dp(6)
            }
        }
        gridCard.addView(divider)
        val rowReadiness = makeTelemetryRow("Overall Readiness")
        content.addView(gridCard)

        // Test Progress Bar (for 10s check)
        val testProgressBar = ProgressBar(activity, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
            progress = 0
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(6)
            ).apply { bottomMargin = dp(10) }
        }
        content.addView(testProgressBar)

        // Action Buttons Row
        val btnRow = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(8) }
        }

        val runTestBtn = Button(activity).apply {
            text = "Run $testDurationSec-Second Check"
            textSize = 11.5f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.WHITE)
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#2563EB"))
                cornerRadius = dp(8).toFloat()
            }
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                rightMargin = dp(6)
            }
        }

        val retestBtn = Button(activity).apply {
            text = "Retest Camera"
            textSize = 11.5f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.WHITE)
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#374151"))
                cornerRadius = dp(8).toFloat()
            }
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                leftMargin = dp(6)
            }
        }
        btnRow.addView(runTestBtn)
        btnRow.addView(retestBtn)
        content.addView(btnRow)

        val closeBtn = Button(activity).apply {
            text = "Close"
            textSize = 11.5f
            setTextColor(Color.parseColor("#94A3B8"))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#111827"))
                cornerRadius = dp(8).toFloat()
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }
        content.addView(closeBtn)

        val dialog = AlertDialog.Builder(activity)
            .setView(root)
            .setCancelable(false)
            .create()

        fun applyTelemetryToUi(t: LiveCameraAnalysisTelemetry) {
            overlayView.updateTelemetry(t)

            guidanceTv.text = t.actionableGuidance
            guidanceTv.setTextColor(Color.parseColor(t.overallReadiness.color.hex))

            rowCamera.second.text = if (t.cameraAvailable) "READY" else "UNAVAILABLE"
            rowCamera.second.setTextColor(Color.parseColor(if (t.cameraAvailable) AnalysisStatusColor.GREEN.hex else AnalysisStatusColor.RED.hex))

            rowFace.second.text = "${t.faceDetectionStatus.label} (${t.faceCount})"
            rowFace.second.setTextColor(Color.parseColor(t.faceDetectionStatus.color.hex))

            rowPos.second.text = t.facePositionStatus.label
            rowPos.second.setTextColor(Color.parseColor(t.facePositionStatus.color.hex))

            rowLight.second.text = "${t.lightingStatus.label} (${t.brightnessScore.roundToInt()}/255)"
            rowLight.second.setTextColor(Color.parseColor(t.lightingStatus.color.hex))

            rowLeftEye.second.text = t.leftEyeVisibility.label
            rowLeftEye.second.setTextColor(Color.parseColor(t.leftEyeVisibility.color.hex))

            rowRightEye.second.text = t.rightEyeVisibility.label
            rowRightEye.second.setTextColor(Color.parseColor(t.rightEyeVisibility.color.hex))

            rowHead.second.text = t.headPoseStatus.label
            rowHead.second.setTextColor(Color.parseColor(t.headPoseStatus.color.hex))

            rowUpdates.second.text = if (t.frameStreamActive) "LIVE (${t.frameFps} FPS)" else "STALLED (${t.lastFrameAgeMs}ms ago)"
            rowUpdates.second.setTextColor(Color.parseColor(if (t.frameStreamActive) AnalysisStatusColor.GREEN.hex else AnalysisStatusColor.RED.hex))

            rowReadiness.second.text = t.overallReadiness.label
            rowReadiness.second.setTextColor(Color.parseColor(t.overallReadiness.color.hex))
        }

        fun restartCamera() {
            cameraSession?.stop()
            cameraSession = LiveCameraSession(activity, "SettingsCameraCheck", textureView) { telemetry ->
                applyTelemetryToUi(telemetry)
                if (isTestRunning) {
                    testRunner?.onIncomingFrame(telemetry)
                }
            }
            cameraSession?.start()
        }

        runTestBtn.setOnClickListener {
            if (isTestRunning) return@setOnClickListener
            isTestRunning = true
            testProgressBar.visibility = View.VISIBLE
            testProgressBar.progress = 0
            runTestBtn.isEnabled = false
            retestBtn.isEnabled = false
            runTestBtn.text = "Testing... (${testDurationSec}s valid observation required)"

            testRunner = LiveReadinessTestRunner(
                activity,
                testDurationSec = testDurationSec,
                onProgress = { secLeft, telemetry ->
                    val elapsedPct = ((testDurationSec - secLeft) * 100 / testDurationSec).coerceIn(0, 100)
                    testProgressBar.progress = elapsedPct
                    runTestBtn.text = "Testing... (${secLeft}s left)"
                },
                onComplete = { summary ->
                    onAuditComplete?.invoke(summary)
                    isTestRunning = false
                    testProgressBar.visibility = View.GONE
                    runTestBtn.isEnabled = true
                    retestBtn.isEnabled = true
                    runTestBtn.text = "Run $testDurationSec-Second Check"

                    AlertDialog.Builder(activity)
                        .setTitle(if (summary.passed) "✅ READINESS AUDIT: PASSED" else "❌ READINESS AUDIT: FAILED")
                        .setMessage(
                            "${summary.summaryMessage}\n\n" +
                            "• Analyzed Frames: ${summary.totalFramesAnalyzed}\n" +
                            "• Valid Observation Ratio: ${summary.validRatio}%\n" +
                            "• Lighting Pass Ratio: ${summary.lightingPassRatio}% (Avg: ${summary.averageBrightness}/255)\n" +
                            "• Face Observed Ratio: ${summary.faceObservedRatio}%\n" +
                            "• Eyes Observed Ratio: ${summary.eyesObservedRatio}%\n\n" +
                            if (summary.failures.isNotEmpty()) "Identified Issues:\n• " + summary.failures.joinToString("\n• ") else "Demo completed using fresh camera observations. Production evidence is validated separately."
                        )
                        .setPositiveButton("OK", null)
                        .show()
                }
            )
            testRunner?.startTest()
        }

        retestBtn.setOnClickListener {
            restartCamera()
        }

        closeBtn.setOnClickListener {
            testRunner?.cancelTest()
            cameraSession?.stop()
            dialog.dismiss()
        }

        dialog.setOnDismissListener {
            testRunner?.cancelTest()
            cameraSession?.stop()
        }

        dialog.show()
        restartCamera()
    }
}
