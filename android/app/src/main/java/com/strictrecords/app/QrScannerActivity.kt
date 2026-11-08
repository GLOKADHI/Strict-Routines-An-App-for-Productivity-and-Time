package com.strictrecords.app

import android.Manifest
import android.animation.ValueAnimator
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.hardware.Camera
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.provider.Settings
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.ViewGroup
import android.view.animation.LinearInterpolator
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.common.GlobalHistogramBinarizer
import com.google.zxing.common.HybridBinarizer
import org.json.JSONObject
import java.util.EnumMap
import java.util.concurrent.Executors

/**
 * Built-In Real Hardware Camera QR Scanner Activity (Sections 3, 5, 10).
 * Uses Android hardware Camera preview + Google ZXing PlanarYUVLuminanceSource
 * to decode StrictRecords PC pairing QR codes in real time without external apps.
 */
@Suppress("DEPRECATION")
class QrScannerActivity : Activity(), SurfaceHolder.Callback, Camera.PreviewCallback {

    private lateinit var rootFrame: FrameLayout
    private lateinit var surfaceView: SurfaceView
    private lateinit var overlayView: ScannerReticleView
    private lateinit var statusBanner: TextView
    private lateinit var stateOverlayContainer: LinearLayout
    private lateinit var stateTitleTv: TextView
    private lateinit var stateDescTv: TextView
    private lateinit var stateActionBtn: TextView
    private lateinit var stateSecondaryBtn: TextView

    private val cameraLock = Any()
    private var camera: Camera? = null
    private var currentCameraId = Camera.CameraInfo.CAMERA_FACING_BACK
    private var isSurfaceReady = false
    private var isPreviewActive = false
    private var isTorchOn = false

    @Volatile
    private var isDecodingFrame = false

    @Volatile
    private var hasLockedValidQr = false

    private val decodeExecutor = Executors.newSingleThreadExecutor()
    private val decodeLock = Any()
    private var rot90Buf: ByteArray? = null
    private var rot270Buf: ByteArray? = null

    private var lastInvalidQrText = ""
    private var lastInvalidQrTime = 0L

    private val mainHandler = Handler(Looper.getMainLooper())
    private var autoFocusRunnable: Runnable? = null

    private val qrReader = MultiFormatReader().apply {
        val hints = EnumMap<DecodeHintType, Any>(DecodeHintType::class.java)
        hints[DecodeHintType.POSSIBLE_FORMATS] = listOf(BarcodeFormat.QR_CODE)
        hints[DecodeHintType.TRY_HARDER] = java.lang.Boolean.TRUE
        hints[DecodeHintType.CHARACTER_SET] = "UTF-8"
        setHints(hints)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        selectDefaultCameraId()
        buildScannerUi()
        setContentView(rootFrame)
        checkAndRequestCameraPermission()
    }

    private fun selectDefaultCameraId() {
        val numCameras = Camera.getNumberOfCameras()
        var backId = -1
        var frontId = -1
        for (i in 0 until numCameras) {
            val info = Camera.CameraInfo()
            Camera.getCameraInfo(i, info)
            if (info.facing == Camera.CameraInfo.CAMERA_FACING_BACK && backId == -1) backId = i
            if (info.facing == Camera.CameraInfo.CAMERA_FACING_FRONT && frontId == -1) frontId = i
        }
        currentCameraId = when {
            backId != -1 -> backId
            frontId != -1 -> frontId
            else -> 0
        }
    }

    private fun dp(v: Int): Int =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    private fun pillBg(hexColor: String, radiusDp: Int, strokeHex: String? = null, strokeWidthDp: Int = 1): GradientDrawable =
        GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(radiusDp).toFloat()
            setColor(Color.parseColor(hexColor))
            if (strokeHex != null) {
                setStroke(dp(strokeWidthDp), Color.parseColor(strokeHex))
            }
        }

    private fun buildScannerUi() {
        rootFrame = FrameLayout(this).apply {
            setBackgroundColor(Color.parseColor("#07090D"))
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        }

        // 1. Live Camera SurfaceView
        surfaceView = SurfaceView(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        }
        surfaceView.holder.addCallback(this)
        rootFrame.addView(surfaceView)

        // 2. Reticle + Laser Viewfinder Overlay
        overlayView = ScannerReticleView(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
            setOnTouchListener { _, event ->
                if (event.action == MotionEvent.ACTION_UP) {
                    triggerManualFocus()
                }
                true
            }
        }
        rootFrame.addView(overlayView)

        // 3. Top Header Bar
        val topBar = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(28), dp(20), dp(16))
            background = pillBg("#CC0B0F19", 0)
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP
            )
        }

        val headerRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }

        val titleCol = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        titleCol.addView(TextView(this).apply {
            text = "📷 SCAN PC PAIRING QR CODE"
            textSize = 15f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            letterSpacing = 0.04f
        })
        titleCol.addView(TextView(this).apply {
            text = "Open Windows StrictRoutines → Device Center → + Connect Android"
            textSize = 11f
            setTextColor(Color.parseColor("#94A3B8"))
        })
        headerRow.addView(titleCol)

        val closeBtn = TextView(this).apply {
            text = "✕ CLOSE"
            textSize = 11f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            background = pillBg("#1E293B", 99, "#475569", 1)
            setPadding(dp(14), dp(8), dp(14), dp(8))
            setOnClickListener {
                setResult(RESULT_CANCELED)
                finish()
            }
        }
        headerRow.addView(closeBtn)
        topBar.addView(headerRow)

        statusBanner = TextView(this).apply {
            text = "● Initializing camera..."
            textSize = 12f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.parseColor("#10B981"))
            background = pillBg("#131926", 12, "#3B82F6", 1)
            setPadding(dp(12), dp(8), dp(12), dp(8))
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(12) }
        }
        topBar.addView(statusBanner)
        rootFrame.addView(topBar)

        // 4. Bottom Controls Bar (Switch Camera, Torch, Manual Code Fallback)
        val bottomBar = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(20), dp(16), dp(20), dp(28))
            background = pillBg("#DD0B0F19", 0)
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM
            )
        }

        val controlRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(10) }
        }

        val switchCamBtn = TextView(this).apply {
            text = "🔄 Flip Camera"
            textSize = 12f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            background = pillBg("#1E293B", 99, "#6366F1", 1)
            setPadding(dp(16), dp(10), dp(16), dp(10))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                rightMargin = dp(6)
            }
            gravity = Gravity.CENTER
            setOnClickListener { flipCamera() }
        }
        controlRow.addView(switchCamBtn)

        val torchBtn = TextView(this).apply {
            text = "🔦 Torch"
            textSize = 12f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            background = pillBg("#1E293B", 99, "#06B6D4", 1)
            setPadding(dp(16), dp(10), dp(16), dp(10))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                leftMargin = dp(6)
            }
            gravity = Gravity.CENTER
            setOnClickListener { toggleTorch() }
        }
        controlRow.addView(torchBtn)
        bottomBar.addView(controlRow)

        val manualBtn = TextView(this).apply {
            text = "⌨️ Enter Pairing Code Manually Instead"
            textSize = 12.5f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.parseColor("#93C5FD"))
            background = pillBg("#172554", 99, "#3B82F6", 1)
            setPadding(dp(18), dp(11), dp(18), dp(11))
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
            setOnClickListener {
                val res = Intent().putExtra("MANUAL_ENTRY_REQUESTED", true)
                setResult(RESULT_CANCELED, res)
                finish()
            }
        }
        bottomBar.addView(manualBtn)
        rootFrame.addView(bottomBar)

        // 5. Permission / Error / Retry State Card Overlay
        stateOverlayContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(24), dp(24), dp(24), dp(24))
            background = pillBg("#F2131926", 24, "#6366F1", 1)
            visibility = View.GONE
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.CENTER
            ).apply {
                leftMargin = dp(24)
                rightMargin = dp(24)
            }
        }

        stateTitleTv = TextView(this).apply {
            text = "Camera Permission Required"
            textSize = 17f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(8) }
        }
        stateOverlayContainer.addView(stateTitleTv)

        stateDescTv = TextView(this).apply {
            text = "StrictRoutines needs camera access to scan the one-time pairing QR code displayed on your Windows PC."
            textSize = 12.5f
            setTextColor(Color.parseColor("#94A3B8"))
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(18) }
        }
        stateOverlayContainer.addView(stateDescTv)

        stateActionBtn = TextView(this).apply {
            text = "GRANT CAMERA PERMISSION"
            textSize = 13f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            background = pillBg("#6366F1", 99)
            setPadding(dp(20), dp(12), dp(20), dp(12))
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(10) }
        }
        stateOverlayContainer.addView(stateActionBtn)

        stateSecondaryBtn = TextView(this).apply {
            text = "ENTER PAIRING CODE MANUALLY"
            textSize = 12f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.parseColor("#94A3B8"))
            background = pillBg("#1E293B", 99, "#475569", 1)
            setPadding(dp(18), dp(10), dp(18), dp(10))
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
            setOnClickListener {
                val res = Intent().putExtra("MANUAL_ENTRY_REQUESTED", true)
                setResult(RESULT_CANCELED, res)
                finish()
            }
        }
        stateOverlayContainer.addView(stateSecondaryBtn)
        rootFrame.addView(stateOverlayContainer)
    }

    private fun hasCameraPermission(): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        } else true
    }

    private fun checkAndRequestCameraPermission() {
        if (hasCameraPermission()) {
            stateOverlayContainer.visibility = View.GONE
            startCameraIfReady()
        } else {
            statusBanner.text = "⚠️ Waiting for camera permission..."
            statusBanner.setTextColor(Color.parseColor("#F59E0B"))
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                requestPermissions(arrayOf(Manifest.permission.CAMERA), REQ_CAMERA_PERMISSION)
            }
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_CAMERA_PERMISSION) {
            if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                stateOverlayContainer.visibility = View.GONE
                startCameraIfReady()
            } else {
                showPermissionDeniedState()
            }
        }
    }

    private fun showPermissionDeniedState() {
        statusBanner.text = "⚠️ Camera permission denied"
        statusBanner.setTextColor(Color.parseColor("#EF4444"))
        stateOverlayContainer.visibility = View.VISIBLE
        stateTitleTv.text = "📷 Camera Permission Needed"
        stateDescTv.text = "To scan the PC pairing QR code, enable Camera permission in Android Settings → Apps → StrictRoutines → Permissions, or enter the pairing code manually."
        stateActionBtn.text = "⚙️ OPEN ANDROID SETTINGS"
        stateActionBtn.setOnClickListener {
            try {
                val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                    data = Uri.fromParts("package", packageName, null)
                }
                startActivity(intent)
            } catch (_: Exception) {}
        }
    }

    private fun showCameraErrorState(reason: String) {
        statusBanner.text = "⚠️ Camera error: $reason"
        statusBanner.setTextColor(Color.parseColor("#EF4444"))
        stateOverlayContainer.visibility = View.VISIBLE
        stateTitleTv.text = "⚠️ Camera Unavailable"
        stateDescTv.text = "Could not open camera preview ($reason).\nYou can retry scanning or enter the 8-character pairing code manually."
        stateActionBtn.text = "🔄 RETRY CAMERA SCAN"
        stateActionBtn.setOnClickListener {
            stateOverlayContainer.visibility = View.GONE
            hasLockedValidQr = false
            startCameraIfReady()
        }
    }

    override fun onResume() {
        super.onResume()
        if (hasCameraPermission()) {
            stateOverlayContainer.visibility = View.GONE
            startCameraIfReady()
        }
    }

    override fun onPause() {
        stopCameraPreviewAndRelease()
        super.onPause()
    }

    override fun onDestroy() {
        stopCameraPreviewAndRelease()
        overlayView.stopAnimation()
        try {
            decodeExecutor.shutdownNow()
        } catch (_: Exception) {}
        super.onDestroy()
    }

    override fun surfaceCreated(holder: SurfaceHolder) {
        isSurfaceReady = true
        startCameraIfReady()
    }

    override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
        isSurfaceReady = true
        if (hasCameraPermission() && camera == null) {
            startCameraIfReady()
        }
    }

    override fun surfaceDestroyed(holder: SurfaceHolder) {
        isSurfaceReady = false
        stopCameraPreviewAndRelease()
    }

    private fun flipCamera() {
        val count = Camera.getNumberOfCameras()
        if (count <= 1) {
            statusBanner.text = "ℹ️ Single camera detected on this device"
            return
        }
        stopCameraPreviewAndRelease()
        currentCameraId = (currentCameraId + 1) % count
        hasLockedValidQr = false
        startCameraIfReady()
    }

    private fun toggleTorch() {
        synchronized(cameraLock) {
            val cam = camera ?: return
            try {
                val params = cam.parameters
                val modes = params.supportedFlashModes ?: emptyList()
                if (!modes.contains(Camera.Parameters.FLASH_MODE_TORCH)) {
                    statusBanner.text = "ℹ️ Torch not supported on active camera"
                    return
                }
                isTorchOn = !isTorchOn
                params.flashMode = if (isTorchOn) Camera.Parameters.FLASH_MODE_TORCH else Camera.Parameters.FLASH_MODE_OFF
                cam.parameters = params
            } catch (_: Exception) {}
        }
    }

    private fun triggerManualFocus() {
        synchronized(cameraLock) {
            val cam = camera ?: return
            try {
                statusBanner.text = "🔍 Focusing..."
                statusBanner.setTextColor(Color.parseColor("#38BDF8"))
                cam.cancelAutoFocus()
                cam.autoFocus { success, _ ->
                    mainHandler.post {
                        statusBanner.text = if (success) "🟢 Focused — Align QR code" else "🟢 Align QR code inside frame"
                        statusBanner.setTextColor(Color.parseColor("#10B981"))
                    }
                    mainHandler.postDelayed({
                        try {
                            synchronized(cameraLock) {
                                camera?.cancelAutoFocus()
                            }
                        } catch (_: Exception) {}
                    }, 2500L)
                }
            } catch (_: Exception) {}
        }
    }

    private fun setCameraDisplayOrientation(activity: Activity, cameraId: Int, camera: Camera) {
        val info = Camera.CameraInfo()
        Camera.getCameraInfo(cameraId, info)
        val rotation = activity.windowManager.defaultDisplay.rotation
        var degrees = 0
        when (rotation) {
            Surface.ROTATION_0 -> degrees = 0
            Surface.ROTATION_90 -> degrees = 90
            Surface.ROTATION_180 -> degrees = 180
            Surface.ROTATION_270 -> degrees = 270
        }
        var result: Int
        if (info.facing == Camera.CameraInfo.CAMERA_FACING_FRONT) {
            result = (info.orientation + degrees) % 360
            result = (360 - result) % 360
        } else {
            result = (info.orientation - degrees + 360) % 360
        }
        camera.setDisplayOrientation(result)
    }

    private fun startCameraIfReady() {
        if (!isSurfaceReady || !hasCameraPermission() || hasLockedValidQr) return
        synchronized(cameraLock) {
            if (camera != null) return
            try {
                val numCams = Camera.getNumberOfCameras()
                val cam = if (numCams > 0) Camera.open(currentCameraId.coerceIn(0, numCams - 1)) else Camera.open()
                camera = cam

                val params = cam.parameters
                val supportedSizes = params.supportedPreviewSizes
                if (!supportedSizes.isNullOrEmpty()) {
                    val dm = resources.displayMetrics
                    val sW = dm.widthPixels
                    val sH = dm.heightPixels
                    val targetRatio = if (sH > sW) sH.toFloat() / sW else sW.toFloat() / sH

                    val best = supportedSizes.minByOrNull {
                        val ratio = it.width.toFloat() / it.height
                        val ratioDiff = kotlin.math.abs(ratio - targetRatio)
                        val resDiff = kotlin.math.abs(it.width - 1280) + kotlin.math.abs(it.height - 800)
                        ratioDiff * 800 + resDiff
                    } ?: supportedSizes.first()
                    params.setPreviewSize(best.width, best.height)
                }

                val focusModes = params.supportedFocusModes ?: emptyList()
                when {
                    focusModes.contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE) ->
                        params.focusMode = Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE
                    focusModes.contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_VIDEO) ->
                        params.focusMode = Camera.Parameters.FOCUS_MODE_CONTINUOUS_VIDEO
                    focusModes.contains(Camera.Parameters.FOCUS_MODE_AUTO) ->
                        params.focusMode = Camera.Parameters.FOCUS_MODE_AUTO
                }

                try {
                    cam.parameters = params
                } catch (_: Exception) {}

                setCameraDisplayOrientation(this, currentCameraId, cam)
                cam.setPreviewDisplay(surfaceView.holder)
                cam.setPreviewCallback(this)
                cam.startPreview()
                isPreviewActive = true

                try {
                    cam.cancelAutoFocus()
                } catch (_: Exception) {}

                statusBanner.text = "🟢 Camera Active — Align QR or Tap Screen to Focus"
                statusBanner.setTextColor(Color.parseColor("#10B981"))
            } catch (e: Exception) {
                stopCameraPreviewAndRelease()
                showCameraErrorState(e.message ?: "Hardware busy")
            }
        }
    }

    private fun stopCameraPreviewAndRelease() {
        autoFocusRunnable?.let { mainHandler.removeCallbacks(it) }
        synchronized(cameraLock) {
            val cam = camera ?: return
            camera = null
            isPreviewActive = false
            try {
                cam.setPreviewCallback(null)
                cam.stopPreview()
            } catch (_: Exception) {}
            try {
                cam.release()
            } catch (_: Exception) {}
        }
    }

    override fun onPreviewFrame(data: ByteArray?, cam: Camera?) {
        if (data == null || cam == null || isDecodingFrame || hasLockedValidQr) return
        val previewSize = try {
            cam.parameters.previewSize
        } catch (_: Exception) {
            null
        } ?: return

        val width = previewSize.width
        val height = previewSize.height
        if (width <= 0 || height <= 0 || data.size < width * height) return

        isDecodingFrame = true
        val frameCopy = data.copyOf()

        decodeExecutor.execute {
            try {
                if (!hasLockedValidQr) {
                    val decoded = decodeQrFromYuv(frameCopy, width, height)
                    if (!decoded.isNullOrEmpty() && !hasLockedValidQr) {
                        mainHandler.post { handleDecodedQrCandidate(decoded) }
                    }
                }
            } catch (_: Exception) {
            } finally {
                isDecodingFrame = false
            }
        }
    }

    private fun tryDecodeSource(source: PlanarYUVLuminanceSource): String? {
        synchronized(decodeLock) {
            // 1. Try HybridBinarizer (standard high-contrast)
            try {
                val bitmap = BinaryBitmap(HybridBinarizer(source))
                val text = qrReader.decodeWithState(bitmap).text
                if (!text.isNullOrEmpty()) return text
            } catch (_: Exception) {
            } finally {
                qrReader.reset()
            }

            // 2. Try GlobalHistogramBinarizer (critical for screen scanning / glare!)
            try {
                val bitmap = BinaryBitmap(GlobalHistogramBinarizer(source))
                val text = qrReader.decodeWithState(bitmap).text
                if (!text.isNullOrEmpty()) return text
            } catch (_: Exception) {
            } finally {
                qrReader.reset()
            }

            // 3. Try inverted luminance
            try {
                val bitmap = BinaryBitmap(HybridBinarizer(source.invert()))
                val text = qrReader.decodeWithState(bitmap).text
                if (!text.isNullOrEmpty()) return text
            } catch (_: Exception) {
            } finally {
                qrReader.reset()
            }

            return null
        }
    }

    private fun decodeQrFromYuv(yuv: ByteArray, width: Int, height: Int): String? {
        val framePixels = width * height
        if (yuv.size < framePixels) return null

        val newW = height
        val newH = width

        // ── ANGLE 1: 90° Clockwise (Standard portrait orientation for back camera) ──
        if (rot90Buf == null || rot90Buf!!.size != framePixels) {
            rot90Buf = ByteArray(framePixels)
        }
        val rot90 = rot90Buf!!
        for (x in 0 until width) {
            val xOffset = x * height
            for (y in 0 until height) {
                rot90[xOffset + (height - 1 - y)] = yuv[y * width + x]
            }
        }

        // 1a. Try center crop of 90° frame (focused on reticle)
        val cropSize90 = (kotlin.math.min(newW, newH) * 0.72f).toInt()
        val cropL90 = (newW - cropSize90) / 2
        val cropT90 = (newH - cropSize90) / 2
        val cropSource90 = PlanarYUVLuminanceSource(rot90, newW, newH, cropL90, cropT90, cropSize90, cropSize90, false)
        var res = tryDecodeSource(cropSource90)
        if (res != null) return res

        // 1b. Try full frame 90°
        val fullSource90 = PlanarYUVLuminanceSource(rot90, newW, newH, 0, 0, newW, newH, false)
        res = tryDecodeSource(fullSource90)
        if (res != null) return res

        // ── ANGLE 2: 0° (Native landscape) ──
        val cropSize0 = (kotlin.math.min(width, height) * 0.72f).toInt()
        val cropL0 = (width - cropSize0) / 2
        val cropT0 = (height - cropSize0) / 2
        val cropSource0 = PlanarYUVLuminanceSource(yuv, width, height, cropL0, cropT0, cropSize0, cropSize0, false)
        res = tryDecodeSource(cropSource0)
        if (res != null) return res

        val fullSource0 = PlanarYUVLuminanceSource(yuv, width, height, 0, 0, width, height, false)
        res = tryDecodeSource(fullSource0)
        if (res != null) return res

        // ── ANGLE 3: 270° Clockwise (90° CCW, front camera or reverse portrait) ──
        if (rot270Buf == null || rot270Buf!!.size != framePixels) {
            rot270Buf = ByteArray(framePixels)
        }
        val rot270 = rot270Buf!!
        for (x in 0 until width) {
            val xOffset = (width - 1 - x) * height
            for (y in 0 until height) {
                rot270[xOffset + y] = yuv[y * width + x]
            }
        }
        val fullSource270 = PlanarYUVLuminanceSource(rot270, newW, newH, 0, 0, newW, newH, false)
        res = tryDecodeSource(fullSource270)
        if (res != null) return res

        // ── ANGLE 4: 180° ──
        val rot180 = ByteArray(framePixels)
        for (y in 0 until height) {
            val yOffset = (height - 1 - y) * width
            val origY = y * width
            for (x in 0 until width) {
                rot180[yOffset + (width - 1 - x)] = yuv[origY + x]
            }
        }
        val fullSource180 = PlanarYUVLuminanceSource(rot180, width, height, 0, 0, width, height, false)
        res = tryDecodeSource(fullSource180)
        if (res != null) return res

        return null
    }

    companion object {
        private const val REQ_CAMERA_PERMISSION = 801

        fun isValidStrictRecordsQrPayload(raw: String): Pair<Boolean, String> {
            val trimmed = raw.trim()
            val regex = Regex("(?i)SR-[A-Z0-9]{4}-[A-Z0-9]{4}")
            if (regex.containsMatchIn(trimmed)) {
                return Pair(true, "")
            }
            val sixDigitPinRegex = Regex("^\\d{6}$")
            if (sixDigitPinRegex.matches(trimmed)) {
                return Pair(true, "")
            }
            if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
                val m = Regex("\"(?:session_key|key|manual_pin|pin)\"\\s*:\\s*\"([^\"]+)\"").find(trimmed)
                if (m != null && m.groupValues[1].trim().isNotEmpty()) {
                    return Pair(true, "")
                }
                return try {
                    val obj = JSONObject(trimmed)
                    val skey = obj.optString("session_key", obj.optString("key", obj.optString("manual_pin", obj.optString("pin", "")))).trim()
                    if (skey.isNotEmpty()) {
                        Pair(true, "")
                    } else {
                        Pair(false, "QR JSON is missing 'session_key'.")
                    }
                } catch (_: Throwable) {
                    Pair(false, "Malformed QR JSON payload.")
                }
            }
            if (trimmed.uppercase().startsWith("SR-") && trimmed.length in 8..18) {
                return Pair(true, "")
            }
            return Pair(false, "Unsupported QR code. Please scan the QR code shown in Windows StrictRoutines → Device Center.")
        }
    }

    private fun handleDecodedQrCandidate(rawText: String) {
        if (hasLockedValidQr) return

        val (isValid, errReason) = isValidStrictRecordsQrPayload(rawText)
        if (!isValid) {
            val now = System.currentTimeMillis()
            if (rawText != lastInvalidQrText || now - lastInvalidQrTime > 3000L) {
                lastInvalidQrText = rawText
                lastInvalidQrTime = now
                vibrateShort(false)
                statusBanner.text = "⚠️ $errReason"
                statusBanner.setTextColor(Color.parseColor("#F59E0B"))
            }
            return
        }

        hasLockedValidQr = true
        vibrateShort(true)
        statusBanner.text = "✓ Valid StrictRoutines QR Detected! Connecting..."
        statusBanner.setTextColor(Color.parseColor("#10B981"))
        stopCameraPreviewAndRelease()

        val resultIntent = Intent().apply {
            putExtra("SCAN_RESULT", rawText.trim())
        }
        setResult(RESULT_OK, resultIntent)
        finish()
    }

    private fun vibrateShort(success: Boolean) {
        try {
            val vib = getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator ?: return
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vib.vibrate(VibrationEffect.createOneShot(if (success) 60L else 120L, VibrationEffect.DEFAULT_AMPLITUDE))
            } else {
                vib.vibrate(if (success) 60L else 120L)
            }
        } catch (_: Exception) {}
    }

    /**
     * Custom Viewfinder Reticle & Animated Laser Overlay
     */
    private inner class ScannerReticleView(context: Context) : View(context) {
        private val maskPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.parseColor("#8807090D")
            style = Paint.Style.FILL
        }
        private val framePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.parseColor("#6366F1")
            style = Paint.Style.STROKE
            strokeWidth = dp(2).toFloat()
        }
        private val cornerPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.parseColor("#10B981")
            style = Paint.Style.STROKE
            strokeWidth = dp(5).toFloat()
            strokeCap = Paint.Cap.ROUND
        }
        private val laserPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.parseColor("#CC8B5CF6")
            style = Paint.Style.STROKE
            strokeWidth = dp(3).toFloat()
        }

        private var laserFraction = 0f
        private val laserAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 2000L
            repeatCount = ValueAnimator.INFINITE
            repeatMode = ValueAnimator.REVERSE
            interpolator = LinearInterpolator()
            addUpdateListener {
                laserFraction = it.animatedValue as Float
                postInvalidateOnAnimation()
            }
        }

        init {
            laserAnimator.start()
        }

        fun stopAnimation() {
            laserAnimator.cancel()
        }

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            val w = width.toFloat()
            val h = height.toFloat()
            val boxSize = (kotlin.math.min(w, h) * 0.64f).coerceIn(dp(200).toFloat(), dp(320).toFloat())
            val left = (w - boxSize) / 2f
            val top = (h - boxSize) / 2f
            val right = left + boxSize
            val bottom = top + boxSize
            val rect = RectF(left, top, right, bottom)
            val r = dp(20).toFloat()

            // Draw outer dimmed mask around viewfinder cutout
            val path = Path().apply {
                addRect(0f, 0f, w, h, Path.Direction.CW)
                addRoundRect(rect, r, r, Path.Direction.CCW)
            }
            canvas.drawPath(path, maskPaint)

            // Draw subtle frame border
            canvas.drawRoundRect(rect, r, r, framePaint)

            // Draw bright corner brackets
            val cLen = dp(28).toFloat()
            // Top-left
            canvas.drawLine(left, top + cLen, left, top + dp(8), cornerPaint)
            canvas.drawLine(left + dp(8), top, left + cLen, top, cornerPaint)
            // Top-right
            canvas.drawLine(right - cLen, top, right - dp(8), top, cornerPaint)
            canvas.drawLine(right, top + dp(8), right, top + cLen, cornerPaint)
            // Bottom-left
            canvas.drawLine(left, bottom - cLen, left, bottom - dp(8), cornerPaint)
            canvas.drawLine(left + dp(8), bottom, left + cLen, bottom, cornerPaint)
            // Bottom-right
            canvas.drawLine(right - cLen, bottom, right - dp(8), bottom, cornerPaint)
            canvas.drawLine(right, bottom - cLen, right, bottom - dp(8), cornerPaint)

            // Draw animated horizontal laser scan line
            val laserY = top + dp(16) + (boxSize - dp(32)) * laserFraction
            canvas.drawLine(left + dp(16), laserY, right - dp(16), laserY, laserPaint)
        }
    }
}
