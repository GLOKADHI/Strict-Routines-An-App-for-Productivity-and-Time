package com.strictrecords.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.Application
import android.app.KeyguardManager
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.ImageFormat
import android.graphics.Matrix
import android.graphics.RectF
import android.graphics.SurfaceTexture
import android.hardware.camera2.*
import android.media.Image
import android.media.ImageReader
import android.os.*
import android.util.Log
import android.util.Size
import android.view.Surface
import android.view.TextureView
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.abs
import kotlin.math.min

/** One Camera2 session, real YUV frame timestamps and a bounded detection worker. */
class WakeUpCameraSession(
    private val activity: Activity,
    private val owner: String,
    private val preview: TextureView,
    private val onTelemetry: (CameraFaceAnalysisEngine.LiveCameraAnalysisTelemetry) -> Unit,
    private val onFrame: ((CameraFaceAnalysisEngine.LiveCameraAnalysisTelemetry, Bitmap, Long, Long, Long) -> Unit)? = null,
    private val onState: ((WakeUpCameraPolicy.State, String) -> Unit)? = null
) {
    companion object {
        private var activeOwner: WakeUpCameraSession? = null
        fun inUse(): Boolean = synchronized(this) { activeOwner?.running == true }
    }
    private val ui = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private val busy = AtomicBoolean(false)
    private var cameraThread: HandlerThread? = null
    private var cameraHandler: Handler? = null
    private var camera: CameraDevice? = null
    private var capture: CameraCaptureSession? = null
    private var reader: ImageReader? = null
    private var previewSurface: Surface? = null
    @Volatile private var epoch = 0
    @Volatile private var running = false
    @Volatile private var foreground = true
    private var appResumed = true
    private var opening = false
    private var recoveryPending = false
    private var recoveries = 0
    private var lastFrameArrivalMs = 0L
    private var lastSubmittedMs = 0L
    private var lastSensorNs = 0L
    private var firstSensorNs = 0L
    private var firstSensorElapsedMs = 0L
    private var stable = 0
    private var lastFaceBox: RectF? = null
    private var previousQualityMs = 0L
    private var startedMs = 0L
    private var frames = 0
    private var frameSequence = 0L
    private var reportedStale = false
    private var rotation = 0
    private var front = true
    private var sensorOrientation = 0
    private var displayDegrees = 0
    private var streamSize = Size(640, 480)
    private var contentRect = RectF(0f, 0f, 1f, 1f)

    private fun log(message: String) = Log.i("StrictWakeCamera", "owner=$owner epoch=$epoch $message")
    private fun state(s: WakeUpCameraPolicy.State, message: String) {
        log("state=$s $message")
        onState?.invoke(s, message)
        onTelemetry(CameraFaceAnalysisEngine.unavailableTelemetry(message, s == WakeUpCameraPolicy.State.INITIALIZING))
    }

    private val lifecycle = object : Application.ActivityLifecycleCallbacks {
        override fun onActivityPaused(a: Activity) { if (a === activity && running) { appResumed = false; suspendCamera("App backgrounded; no observation time counted") } }
        override fun onActivityResumed(a: Activity) {
            if (a === activity && running) { appResumed = true; foreground = true; recoveries = 0; openWhenReady() }
        }
        override fun onActivityDestroyed(a: Activity) { if (a === activity) stop() }
        override fun onActivityCreated(a: Activity, b: Bundle?) {}
        override fun onActivityStarted(a: Activity) {}
        override fun onActivityStopped(a: Activity) {}
        override fun onActivitySaveInstanceState(a: Activity, b: Bundle) {}
    }

    private val watchdog = object : Runnable {
        override fun run() {
            if (!running) return
            val locked = (activity.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager).isKeyguardLocked
            if (locked && foreground) suspendCamera("Screen locked; observation paused")
            if (!locked && foreground) {
                if (activity.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
                    foreground = false; releaseCamera(); state(WakeUpCameraPolicy.State.FAILED, "Camera permission unavailable. Grant it, then retry")
                } else if (!recoveryPending && SystemClock.elapsedRealtime() - lastFrameArrivalMs > 4_000L) {
                    recover("No fresh camera frames received")
                } else if (!opening && !recoveryPending && !reportedStale && SystemClock.elapsedRealtime() - lastFrameArrivalMs > WakeUpCameraPolicy.MAX_FRAME_AGE_MS) {
                    reportedStale = true
                    state(WakeUpCameraPolicy.State.PAUSED, "Waiting for fresh camera evidence")
                } else if (readDisplayDegrees() != displayDegrees && !recoveryPending) {
                    recover("Display rotation changed")
                }
            }
            ui.postDelayed(this, 500L)
        }
    }

    fun start() {
        if (running) return
        synchronized(Companion) {
            if (activeOwner != null && activeOwner !== this && activeOwner?.running == true) {
                state(WakeUpCameraPolicy.State.FAILED, "Camera is already in use by another check")
                return
            }
            activeOwner = this
        }
        running = true; foreground = true; startedMs = SystemClock.elapsedRealtime()
        activity.application.registerActivityLifecycleCallbacks(lifecycle)
        preview.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
            override fun onSurfaceTextureAvailable(s: SurfaceTexture, w: Int, h: Int) { if (appResumed) foreground = true; openWhenReady() }
            override fun onSurfaceTextureSizeChanged(s: SurfaceTexture, w: Int, h: Int) { fitPreview() }
            override fun onSurfaceTextureDestroyed(s: SurfaceTexture): Boolean {
                if (running) suspendCamera("Camera surface interrupted")
                return true
            }
            override fun onSurfaceTextureUpdated(s: SurfaceTexture) {}
        }
        openWhenReady()
        ui.post(watchdog)
    }

    private fun suspendCamera(reason: String) {
        foreground = false; recoveryPending = false
        ui.removeCallbacksAndMessages(recoveryToken)
        releaseCamera()
        state(WakeUpCameraPolicy.State.PAUSED, reason)
    }

    private val recoveryToken = Any()
    private fun recover(reason: String) {
        if (!running || !foreground || recoveryPending) return
        releaseCamera()
        recoveries++
        if (recoveries > WakeUpCameraPolicy.MAX_RECOVERY_ATTEMPTS) {
            foreground = false
            state(WakeUpCameraPolicy.State.FAILED, "$reason. Recovery failed; tap Retry")
            return
        }
        recoveryPending = true
        state(WakeUpCameraPolicy.State.RECOVERING, "$reason. Reopening camera ($recoveries/${WakeUpCameraPolicy.MAX_RECOVERY_ATTEMPTS})")
        ui.postAtTime({ recoveryPending = false; openWhenReady() }, recoveryToken,
            SystemClock.uptimeMillis() + recoveries * 500L)
    }

    @SuppressLint("MissingPermission")
    private fun openWhenReady() {
        if (!running || !foreground || opening || camera != null || !preview.isAvailable) return
        if (activity.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            foreground = false
            state(WakeUpCameraPolicy.State.FAILED, "Camera permission is required. Grant permission, then retry")
            return
        }
        opening = true; val token = ++epoch
        stable = 0; lastFaceBox = null; firstSensorNs = 0; lastSensorNs = 0
        lastFrameArrivalMs = SystemClock.elapsedRealtime()
        state(WakeUpCameraPolicy.State.INITIALIZING, "Opening front camera")
        val thread = HandlerThread("WakeUpCapture-$owner").apply { start() }
        cameraThread = thread; val handler = Handler(thread.looper); cameraHandler = handler
        try {
            val manager = activity.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val id = manager.cameraIdList.firstOrNull { manager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_FRONT }
                ?: throw IllegalStateException("No front camera available")
            val chars = manager.getCameraCharacteristics(id)
            front = chars.get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_FRONT
            sensorOrientation = chars.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0
            displayDegrees = readDisplayDegrees()
            // Clockwise rotation for raw ImageReader images (ML Kit camera input convention).
            rotation = if (front) (sensorOrientation + displayDegrees) % 360 else (sensorOrientation - displayDegrees + 360) % 360
            val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP) ?: error("No camera stream configuration")
            val previews = map.getOutputSizes(SurfaceTexture::class.java).toList()
            val sizes = map.getOutputSizes(ImageFormat.YUV_420_888).filter { it in previews }
            streamSize = sizes.filter { it.width in 320..1280 && it.height in 240..960 }
                .minByOrNull { abs(it.width - 640) + abs(it.height - 480) } ?: sizes.minByOrNull { it.width * it.height }
                ?: error("No supported preview/YUV configuration")
            fitPreview()
            preview.surfaceTexture!!.setDefaultBufferSize(streamSize.width, streamSize.height)
            previewSurface = Surface(preview.surfaceTexture)
            val input = ImageReader.newInstance(streamSize.width, streamSize.height, ImageFormat.YUV_420_888, 2)
            reader = input
            input.setOnImageAvailableListener({ source ->
                val image = try { source.acquireLatestImage() } catch (_: Throwable) { null } ?: return@setOnImageAvailableListener
                val arrival = SystemClock.elapsedRealtime()
                if (!running || token != epoch || !foreground || image.timestamp <= lastSensorNs ||
                    arrival - lastSubmittedMs < WakeUpCameraPolicy.SAMPLE_INTERVAL_MS || !busy.compareAndSet(false, true)) {
                    image.close(); return@setOnImageAvailableListener
                }
                val sensorNs = image.timestamp; lastSensorNs = sensorNs; lastSubmittedMs = arrival; lastFrameArrivalMs = arrival
                reportedStale = false
                val frameId = ++frameSequence
                val frameMs = arrival
                worker.execute {
                    var bitmap: Bitmap? = null
                    try {
                        val raw = convertYuv(image)
                        val transform = Matrix().apply { postRotate(rotation.toFloat()); if (front) postScale(-1f, 1f) }
                        bitmap = Bitmap.createBitmap(raw, 0, 0, raw.width, raw.height, transform, true)
                        if (bitmap !== raw) raw.recycle()
                        val started = SystemClock.elapsedRealtime()
                        var t = CameraFaceAnalysisEngine.analyzeFrame(bitmap!!, arrival,
                            1000.0 / maxOf(1L, arrival - previousQualityMs), 480)
                        val now = SystemClock.elapsedRealtime()
                        if (!running || token != epoch || !foreground) return@execute
                        val age = now - frameMs
                        val quality = t.isValidForLiveness && age in 0..WakeUpCameraPolicy.MAX_FRAME_AGE_MS
                        val box = t.faceBoundingBox
                        val previous = lastFaceBox
                        val consistent = box != null && (previous == null ||
                            (abs(box.centerX() - previous.centerX()) < 0.15f && abs(box.centerY() - previous.centerY()) < 0.15f))
                        stable = if (quality && consistent && now - previousQualityMs <= WakeUpCameraPolicy.STABLE_WINDOW_MS) stable + 1 else if (quality) 1 else 0
                        previousQualityMs = now; lastFaceBox = if (quality) box?.let { RectF(it) } else null
                        t = t.copy(frameStreamActive = age in 0..WakeUpCameraPolicy.MAX_FRAME_AGE_MS,
                            lastFrameAgeMs = maxOf(0, age), frameId = frameId, capturedElapsedMs = frameMs,
                            previewContentRect = RectF(contentRect), analysisWidth = bitmap!!.width, analysisHeight = bitmap!!.height)
                        if (!quality || stable < WakeUpCameraPolicy.STABLE_FRAMES) {
                            t = t.copy(
                                overallReadiness = if (quality) CameraFaceAnalysisEngine.OverallReadiness.INITIALIZING else t.overallReadiness,
                                actionableGuidance = if (quality) "Hold steady ($stable/${WakeUpCameraPolicy.STABLE_FRAMES} fresh face frames)" else if (age > WakeUpCameraPolicy.MAX_FRAME_AGE_MS) "Waiting for a fresh frame" else t.actionableGuidance
                            )
                        }
                        frames++
                        log("frame=$sensorNs size=${bitmap!!.width}x${bitmap!!.height} detectorMs=${now - started} ageMs=$age face=${t.faceCount} quality=$quality stable=$stable light=${t.brightnessScore.toInt()}")
                        val result = t; val output = bitmap!!; bitmap = null
                        ui.post {
                            try {
                                if (running && foreground && token == epoch && SystemClock.elapsedRealtime() - frameMs <= WakeUpCameraPolicy.MAX_FRAME_AGE_MS) {
                                    onTelemetry(result); onFrame?.invoke(result, output, frameId, frameMs, SystemClock.elapsedRealtime())
                                }
                            } finally { output.recycle() }
                        }
                    } catch (e: Throwable) {
                        ui.post { if (token == epoch) recover("Frame processing error: ${e.javaClass.simpleName}") }
                    } finally { bitmap?.recycle(); image.close(); busy.set(false) }
                }
            }, handler)
            log("config=${streamSize.width}x${streamSize.height} sensor=$sensorOrientation display=$displayDegrees rotation=$rotation")
            manager.openCamera(id, object : CameraDevice.StateCallback() {
                override fun onOpened(device: CameraDevice) {
                    if (!running || token != epoch || !foreground) { device.close(); return }
                    camera = device; opening = false
                    try {
                        val surfaces = listOf(previewSurface!!, input.surface)
                        val request = device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
                            surfaces.forEach { addTarget(it) }; set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO)
                            if (Build.VERSION.SDK_INT >= 31) set(CaptureRequest.SCALER_ROTATE_AND_CROP, CaptureRequest.SCALER_ROTATE_AND_CROP_NONE)
                        }
                        device.createCaptureSession(surfaces, object : CameraCaptureSession.StateCallback() {
                            override fun onConfigured(session: CameraCaptureSession) {
                                if (!running || token != epoch) { session.close(); return }
                                capture = session
                                try { session.setRepeatingRequest(request.build(), null, handler) }
                                catch (e: Throwable) { ui.post { if (token == epoch) recover("Capture request failed") } }
                            }
                            override fun onConfigureFailed(session: CameraCaptureSession) { session.close(); ui.post { if (token == epoch) recover("Capture session configuration failed") } }
                        }, handler)
                    } catch (e: Throwable) { ui.post { if (token == epoch) recover("Camera session failed: ${e.javaClass.simpleName}") } }
                }
                override fun onDisconnected(device: CameraDevice) { device.close(); ui.post { if (token == epoch) recover("Camera disconnected") } }
                override fun onError(device: CameraDevice, error: Int) { device.close(); ui.post { if (token == epoch) recover("Camera error $error") } }
            }, handler)
        } catch (e: Throwable) { recover("Camera initialization failed: ${e.javaClass.simpleName}") }
    }

    private fun readDisplayDegrees(): Int = when (activity.windowManager.defaultDisplay.rotation) {
        Surface.ROTATION_90 -> 90; Surface.ROTATION_180 -> 180; Surface.ROTATION_270 -> 270; else -> 0
    }

    private fun fitPreview() {
        val w = preview.width.toFloat(); val h = preview.height.toFloat()
        if (w <= 0 || h <= 0) return
        val orientedW = if (rotation % 180 == 0) streamSize.width else streamSize.height
        val orientedH = if (rotation % 180 == 0) streamSize.height else streamSize.width
        val scale = min(w / orientedW, h / orientedH)
        val dw = orientedW * scale; val dh = orientedH * scale
        contentRect = RectF((w - dw) / (2 * w), (h - dh) / (2 * h), (w + dw) / (2 * w), (h + dh) / (2 * h))
        preview.setTransform(Matrix().apply {
            postRotate(-displayDegrees.toFloat(), w / 2, h / 2)
            postScale(dw / w, dh / h, w / 2, h / 2)
        })
    }

    private fun convertYuv(image: Image): Bitmap {
        val w = image.width; val h = image.height
        val planes = image.planes
        val data = planes.map { p -> ByteArray(p.buffer.remaining()).also { p.buffer.duplicate().get(it) } }
        val pixels = IntArray(w * h)
        for (y in 0 until h) for (x in 0 until w) {
            val yy = (data[0][y * planes[0].rowStride + x * planes[0].pixelStride].toInt() and 255) - 16
            val u = (data[1][(y / 2) * planes[1].rowStride + (x / 2) * planes[1].pixelStride].toInt() and 255) - 128
            val v = (data[2][(y / 2) * planes[2].rowStride + (x / 2) * planes[2].pixelStride].toInt() and 255) - 128
            val r = ((298 * maxOf(0, yy) + 409 * v + 128) shr 8).coerceIn(0, 255)
            val g = ((298 * maxOf(0, yy) - 100 * u - 208 * v + 128) shr 8).coerceIn(0, 255)
            val b = ((298 * maxOf(0, yy) + 516 * u + 128) shr 8).coerceIn(0, 255)
            pixels[y * w + x] = -0x1000000 or (r shl 16) or (g shl 8) or b
        }
        return Bitmap.createBitmap(pixels, w, h, Bitmap.Config.ARGB_8888)
    }

    private fun releaseCamera() {
        ++epoch; opening = false; stable = 0; lastFaceBox = null
        try { capture?.close() } catch (_: Throwable) {}; capture = null
        try { camera?.close() } catch (_: Throwable) {}; camera = null
        try { reader?.close() } catch (_: Throwable) {}; reader = null
        previewSurface?.release(); previewSurface = null
        cameraThread?.quitSafely(); cameraThread = null; cameraHandler = null
    }

    fun stop() {
        if (!running) return
        running = false; ++epoch
        ui.removeCallbacks(watchdog); ui.removeCallbacksAndMessages(recoveryToken)
        activity.application.unregisterActivityLifecycleCallbacks(lifecycle)
        releaseCamera(); worker.shutdown()
        synchronized(Companion) { if (activeOwner === this) activeOwner = null }
        log("stopped frames=$frames elapsedMs=${SystemClock.elapsedRealtime() - startedMs}")
    }
}
