package com.strictrecords.app

import android.app.Instrumentation
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Bundle
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Runs the real device's Android FaceDetector against user-provided reproduction frames. */
class CameraReplayInstrumentation : Instrumentation() {
    private var mode = "replay"
    override fun onCreate(arguments: Bundle?) { mode = arguments?.getString("mode") ?: "replay"; super.onCreate(arguments); start() }
    override fun onStart() {
        if (mode == "widgets") { StrictWidgetDeviceRegression.run(this); return }
        if (mode == "extension_penalty") { ExtensionPenaltyDeviceRegression.run(this); return }
        if (mode == "restriction_popup") { RestrictionPopupRegression.run(this); return }
        if (mode == "ledger") { LedgerDeviceRegression.run(this); return }
        if (mode == "audio") { runAudioDemo(); return }
        if (mode == "save") { runSaveRegression(); return }
        if (mode == "camera") { runLiveCameraDemo(); return }
        val report = JSONObject()
        try {
            val frames = File(targetContext.filesDir, "camera-replay-fixture").listFiles()?.filter { it.extension == "jpg" }?.sortedBy { it.name }.orEmpty()
            check(frames.isNotEmpty()) { "No reproduction frames" }
            val results = JSONArray()
            for (backend in listOf("legacy", "bundled")) for (width in if (backend == "legacy") listOf(160, 320, 480, 640) else listOf(320, 480, 640)) {
                var detected = 0; var valid = 0; var elapsed = 0L
                val outcomes = JSONArray()
                frames.forEach { file ->
                    val image = BitmapFactory.decodeFile(file.absolutePath)
                    val start = SystemClock.elapsedRealtime()
                    val t = if (backend == "legacy") CameraFaceAnalysisEngine.analyzeLegacyFrame(image, System.currentTimeMillis(), 5.0, width) else CameraFaceAnalysisEngine.analyzeFrame(image, System.currentTimeMillis(), 5.0, width)
                    val ms = SystemClock.elapsedRealtime() - start
                    elapsed += ms
                    if (t.faceDetectionStatus == CameraFaceAnalysisEngine.FaceDetectionStatus.DETECTED) detected++
                    if (t.isValidForLiveness) valid++
                    outcomes.put(JSONObject().put("frame", file.name).put("face_count", t.faceCount).put("confidence", t.analysisConfidence.toDouble())
                        .put("quality_valid", t.isValidForLiveness).put("detector_ms", ms).put("position", t.facePositionStatus.name))
                    image.recycle()
                }
                results.put(JSONObject().put("backend", backend).put("width", width).put("frames", frames.size).put("detected", detected).put("quality_valid", valid)
                    .put("average_detector_ms", elapsed.toDouble() / frames.size).put("outcomes", outcomes))
            }
            report.put("resolution_results", results)
            val policy = WakeUpCameraPolicy.Session()
            for (i in 0..110) policy.observe(i + 1L, 1000L + i * 200, 1025L + i * 200, true)
            val lastNow = policy.evidence(22025).lastFrameMs + 25
            val example = CameraFaceAnalysisEngine.analyzeFrame(Bitmap.createBitmap(320, 240, Bitmap.Config.ARGB_8888), System.currentTimeMillis())
                .copy(leftEyeVisibility = CameraFaceAnalysisEngine.EyeVisibilityStatus.VISIBLE, lightingStatus = CameraFaceAnalysisEngine.LightingStatus.GOOD,
                    headPoseStatus = CameraFaceAnalysisEngine.HeadPoseStatus.ACCEPTABLE)
            val metadata = policy.metadata(lastNow, example)
            check(WakeUpCameraPolicy.validateMetadata(metadata).passed) { "Live and saved rules disagree" }
            metadata.put("valid_samples", 1)
            check(!WakeUpCameraPolicy.validateMetadata(metadata).passed) { "Invalid metadata accepted" }
            report.put("live_saved_validation_agreement", true)
            report.put("note", "Recorded frame replay and policy test, not a live attendance proof")
            File(targetContext.filesDir, "camera_resolution_benchmark.json").writeText(report.toString(2))
            finish(android.app.Activity.RESULT_OK, Bundle().apply { putString("report", report.toString()) })
        } catch (e: Throwable) {
            finish(android.app.Activity.RESULT_CANCELED, Bundle().apply { putString("error", e.stackTraceToString()) })
        }
    }

    private fun runLiveCameraDemo() {
        val done = java.util.concurrent.CountDownLatch(1)
        var result: JSONObject? = null
        val activity = startActivitySync(android.content.Intent().setClassName(targetContext.packageName, "com.strictrecords.app.MainActivity").addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
        runOnMainSync {
            CameraFaceAnalysisEngine.showCameraAndFaceCheckDialog(activity, 20) { summary ->
                result = JSONObject().put("demo", true).put("passed", summary.passed)
                    .put("required_valid_seconds", summary.testDurationSec).put("frames", summary.totalFramesAnalyzed)
                    .put("valid_ratio", summary.validRatio).put("summary", summary.summaryMessage)
                File(targetContext.filesDir, "wake_camera_demo_result.json").writeText(result!!.toString(2))
                done.countDown()
            }
        }
        val completed = done.await(90, java.util.concurrent.TimeUnit.SECONDS)
        if (completed) SystemClock.sleep(8000) // Allow capture of the existing result dialog.
        finish(if (completed && result?.optBoolean("passed") == true) android.app.Activity.RESULT_OK else android.app.Activity.RESULT_CANCELED,
            Bundle().apply { putString("camera_demo", result?.toString() ?: "NOT VERIFIED: demo did not complete") })
    }

    private fun runAudioDemo() {
        val done = java.util.concurrent.CountDownLatch(1)
        var result: JSONObject? = null
        var session: WakeUpAudioSession? = null
        val activity = startActivitySync(android.content.Intent().setClassName(targetContext.packageName, "com.strictrecords.app.MainActivity").addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
        val db = StrictRecordsLocalDb(targetContext)
        runOnMainSync {
            session = WakeUpAudioSession(activity, db, db.nowDate(), "demo_audio_${System.currentTimeMillis()}",
                onStatus = { _, _ -> }, onComplete = { result = it; done.countDown() }, demo = true)
            session!!.startOrResume()
        }
        val completed = done.await(25, java.util.concurrent.TimeUnit.SECONDS)
        runOnMainSync { session?.stop() }
        db.close()
        File(targetContext.filesDir, "wake_audio_demo_result.json").writeText((result ?: JSONObject().put("completed", false).put("reason", "No validated full playback before timeout")).toString(2))
        finish(if (completed) android.app.Activity.RESULT_OK else android.app.Activity.RESULT_CANCELED,
            Bundle().apply { putString("audio_demo", result?.toString() ?: "NOT VERIFIED: no completed audio result") })
    }

    private fun runSaveRegression() {
        val root = File(targetContext.cacheDir, "camera-save-regression-${java.util.UUID.randomUUID()}")
        check(root.canonicalPath.startsWith(targetContext.cacheDir.canonicalPath + File.separator))
        root.mkdirs()
        val isolated = object : android.content.ContextWrapper(targetContext) {
            override fun getApplicationContext(): android.content.Context = this
            override fun getFilesDir(): File = File(root, "files").apply { mkdirs() }
            override fun getDatabasePath(name: String): File = File(root, name)
            override fun openOrCreateDatabase(name: String, mode: Int, factory: android.database.sqlite.SQLiteDatabase.CursorFactory?): android.database.sqlite.SQLiteDatabase = android.database.sqlite.SQLiteDatabase.openOrCreateDatabase(getDatabasePath(name), factory)
            override fun openOrCreateDatabase(name: String, mode: Int, factory: android.database.sqlite.SQLiteDatabase.CursorFactory?, handler: android.database.DatabaseErrorHandler?): android.database.sqlite.SQLiteDatabase = android.database.sqlite.SQLiteDatabase.openOrCreateDatabase(getDatabasePath(name).path, factory, handler)
        }
        try {
            val fixture = File(targetContext.filesDir, "camera-replay-fixture").listFiles()!!.first { it.extension == "jpg" }.readBytes()
            val policy = WakeUpCameraPolicy.Session()
            for (i in 0..110) policy.observe(i + 1L, 1000 + i * 200L, 1025 + i * 200L, true)
            val t = CameraFaceAnalysisEngine.unavailableTelemetry("Test metadata").copy(
                leftEyeVisibility = CameraFaceAnalysisEngine.EyeVisibilityStatus.VISIBLE,
                lightingStatus = CameraFaceAnalysisEngine.LightingStatus.GOOD, headPoseStatus = CameraFaceAnalysisEngine.HeadPoseStatus.ACCEPTABLE)
            val now = policy.evidence(22025).lastFrameMs + 25
            val metadata = policy.metadata(now, t)
            val date = "2026-10-13"
            var db = StrictRecordsLocalDb(isolated)
            val file = db.completeCameraStep(date, true, fixture, metadata)
            check(file.isFile)
            check(db.getDayState(date, true).optString("wakeup_camera_path") == file.absolutePath) { "Saved image path missing from day state" }
            check(db.getWakeUpFlowStatus(date, true).optBoolean("step1_camera")) { "Saved valid proof rejected" }
            check(!db.getWakeUpFlowStatus(date, true).optBoolean("all_completed"))
            check(db.getScore(date, true) == 0)
            db.close(); db = StrictRecordsLocalDb(isolated)
            check(db.getWakeUpFlowStatus(date, true).optBoolean("step1_camera")) { "Reload lost accepted camera result" }
            check(db.getScore(date, true) == 0); db.close()
            finish(android.app.Activity.RESULT_OK, Bundle().apply { putString("save_regression", "PASS: path, hash, accepted camera after DB reload, remaining steps pending, zero points; isolated test database") })
        } catch (e: Throwable) { finish(android.app.Activity.RESULT_CANCELED, Bundle().apply { putString("error", e.stackTraceToString()) }) }
        finally { root.deleteRecursively() }
    }
}
