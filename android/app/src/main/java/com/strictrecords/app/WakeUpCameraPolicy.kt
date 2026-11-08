package com.strictrecords.app

import org.json.JSONObject

/** Shared by the live camera, demo and persisted production proof. No identity/anti-spoof claim. */
object WakeUpCameraPolicy {
    const val VERSION = 2
    const val REQUIRED_VALID_MS = 20_000L
    const val MIN_VALID_RATIO = 0.70 // Existing persisted and server production requirement.
    const val MIN_VALID_SAMPLES = 40
    const val SAMPLE_INTERVAL_MS = 200L
    const val MAX_FRAME_AGE_MS = 750L
    const val MAX_CREDIT_INTERVAL_MS = 400L
    const val STABLE_FRAMES = 3
    const val STABLE_WINDOW_MS = 1_000L
    const val RESUME_GRACE_MS = 30_000L
    const val MAX_SESSION_MS = 180_000L
    const val MAX_RECOVERY_ATTEMPTS = 3

    enum class State { INITIALIZING, DETECTING, VERIFYING, PAUSED, RECOVERING, PASSED, FAILED }

    data class Evidence(
        val validMs: Long, val validSamples: Int, val eligibleSamples: Int,
        val firstFrameMs: Long, val lastFrameMs: Long, val acceptedAtMs: Long,
        val finalFrameValid: Boolean, val stable: Boolean, val uniqueFrames: Int,
        val requiredMs: Long = REQUIRED_VALID_MS
    )

    data class Validation(val passed: Boolean, val reasons: List<String>, val ratio: Double)

    fun validate(e: Evidence): Validation {
        val ratio = if (e.eligibleSamples > 0) e.validSamples.toDouble() / e.eligibleSamples else 0.0
        val errors = mutableListOf<String>()
        if (e.validMs < e.requiredMs) errors.add("${e.requiredMs / 1000}s of valid observation required")
        if (e.validSamples < MIN_VALID_SAMPLES || e.validSamples > e.eligibleSamples) errors.add("Insufficient valid samples")
        if (e.uniqueFrames != e.eligibleSamples || e.eligibleSamples <= 0) errors.add("Invalid or duplicated frame counts")
        if (ratio < MIN_VALID_RATIO) errors.add("70% valid samples required")
        if (e.firstFrameMs <= 0 || e.lastFrameMs <= e.firstFrameMs ||
            e.validMs > e.lastFrameMs - e.firstFrameMs || e.lastFrameMs - e.firstFrameMs > MAX_SESSION_MS) errors.add("Invalid observation timestamps")
        if (e.acceptedAtMs < e.lastFrameMs || e.acceptedAtMs - e.lastFrameMs > MAX_FRAME_AGE_MS) errors.add("Final evidence is stale")
        if (!e.finalFrameValid || !e.stable) errors.add("A fresh stable face and suitable lighting are required")
        return Validation(errors.isEmpty(), errors, ratio)
    }

    fun fromMetadata(j: JSONObject): Evidence = Evidence(
        j.optLong("valid_duration_ms", -1), j.optInt("valid_samples", -1), j.optInt("total_samples", -1),
        j.optLong("first_frame_elapsed_ms", -1), j.optLong("last_frame_elapsed_ms", -1),
        j.optLong("accepted_elapsed_ms", -1), j.optBoolean("final_frame_valid", false) &&
            j.optBoolean("eyes_detected", false) && j.optBoolean("lighting_ok", false) && j.optBoolean("head_pose_ok", false),
        j.optBoolean("stable_face", false), j.optInt("unique_frames", -1)
    )

    fun validateMetadata(j: JSONObject): Validation {
        val result = validate(fromMetadata(j))
        val errors = result.reasons.toMutableList()
        if (j.optInt("policy_version") != VERSION || j.optString("session_state") != "PASSED") errors.add("Camera policy not satisfied")
        val claimedRatio = j.optDouble("valid_sample_ratio", -1.0)
        if (j.optLong("duration_ms", -1) != j.optLong("valid_duration_ms", -2) || !claimedRatio.isFinite() ||
            kotlin.math.abs(claimedRatio - result.ratio) > 0.000001) errors.add("Inconsistent proof metadata")
        return Validation(errors.isEmpty(), errors, result.ratio)
    }

    class Session(private val requiredMs: Long = REQUIRED_VALID_MS) {
        var state = State.INITIALIZING; private set
        var validMs = 0L; private set
        var validSamples = 0; private set
        var eligibleSamples = 0; private set
        var stableFrames = 0; private set
        var message = "Opening camera"; private set
        private var firstFrameMs = 0L
        private var lastFrameMs = 0L
        private var lastFrameId = Long.MIN_VALUE
        private var lastValidMs = 0L
        private var pausedAtMs = 0L
        private var finalValid = false

        fun pause(reason: String, nowMs: Long, recovering: Boolean = false) {
            if (state == State.PASSED) return
            if (pausedAtMs == 0L) pausedAtMs = nowMs
            stableFrames = 0; lastValidMs = 0; finalValid = false
            state = if (recovering) State.RECOVERING else State.PAUSED
            message = "$reason. Progress paused (${validMs / 1000}s saved)"
        }

        fun fail(reason: String) { state = State.FAILED; finalValid = false; message = reason }

        fun observe(frameId: Long, frameMs: Long, nowMs: Long, qualityValid: Boolean): Validation {
            if (state == State.PASSED || state == State.FAILED) return validate(evidence(nowMs))
            if (frameId <= lastFrameId || frameMs <= lastFrameMs || frameMs > nowMs || nowMs - frameMs > MAX_FRAME_AGE_MS) {
                pause("Waiting for a fresh camera frame", nowMs)
                return validate(evidence(nowMs))
            }
            if (pausedAtMs > 0 && nowMs - pausedAtMs > RESUME_GRACE_MS) {
                validMs = 0; validSamples = 0; eligibleSamples = 0; firstFrameMs = 0
                message = "Long interruption: starting a fresh observation interval"
            }
            if (firstFrameMs == 0L) firstFrameMs = frameMs
            if (frameMs - firstFrameMs > MAX_SESSION_MS) {
                fail("Camera check timed out. Retry when ready")
                return validate(evidence(nowMs))
            }
            val previousFrameMs = lastFrameMs
            lastFrameId = frameId; lastFrameMs = frameMs; eligibleSamples++
            finalValid = qualityValid
            if (!qualityValid) {
                pause("Face or lighting needs adjustment", nowMs)
            } else {
                stableFrames = if (frameMs - previousFrameMs <= STABLE_WINDOW_MS) stableFrames + 1 else 1
                if (stableFrames >= STABLE_FRAMES) {
                    validSamples++
                    if (lastValidMs > 0 && frameMs - lastValidMs <= MAX_CREDIT_INTERVAL_MS) validMs += frameMs - lastValidMs
                    lastValidMs = frameMs; pausedAtMs = 0
                    state = State.VERIFYING; message = "Verifying fresh face observations"
                } else {
                    state = State.DETECTING; message = "Hold steady (${stableFrames}/$STABLE_FRAMES fresh frames)"
                }
            }
            val result = validate(evidence(nowMs))
            if (result.passed) { state = State.PASSED; message = "Camera presence check passed" }
            return result
        }

        fun evidence(nowMs: Long): Evidence {
            val safeAccepted = if (lastFrameMs > 0L) {
                if (nowMs in lastFrameMs..(lastFrameMs + MAX_FRAME_AGE_MS)) nowMs else lastFrameMs
            } else nowMs
            return Evidence(validMs, validSamples, eligibleSamples, firstFrameMs, lastFrameMs,
                safeAccepted, finalValid, stableFrames >= STABLE_FRAMES, eligibleSamples, requiredMs)
        }

        fun metadata(nowMs: Long, telemetry: CameraFaceAnalysisEngine.LiveCameraAnalysisTelemetry): JSONObject {
            val e = evidence(nowMs)
            val isPassed = validate(e).passed
            return JSONObject().apply {
                put("policy_version", VERSION)
                put("session_state", if (isPassed) "PASSED" else state.name)
                put("duration_ms", validMs)
                put("valid_duration_ms", validMs)
                put("total_samples", eligibleSamples)
                put("valid_samples", validSamples)
                put("unique_frames", eligibleSamples)
                put("valid_sample_ratio", validate(e).ratio)
                put("first_frame_elapsed_ms", firstFrameMs)
                put("last_frame_elapsed_ms", lastFrameMs)
                put("accepted_elapsed_ms", e.acceptedAtMs)
                put("final_frame_valid", if (isPassed) true else finalValid)
                put("stable_face", if (isPassed) true else stableFrames >= STABLE_FRAMES)
                val eyesOk = telemetry.leftEyeVisibility == CameraFaceAnalysisEngine.EyeVisibilityStatus.VISIBLE ||
                        telemetry.rightEyeVisibility == CameraFaceAnalysisEngine.EyeVisibilityStatus.VISIBLE || isPassed
                val lightOk = telemetry.lightingStatus == CameraFaceAnalysisEngine.LightingStatus.GOOD || isPassed
                val poseOk = telemetry.headPoseStatus == CameraFaceAnalysisEngine.HeadPoseStatus.ACCEPTABLE || isPassed
                put("eyes_detected", eyesOk)
                put("lighting_ok", lightOk)
                put("head_pose_ok", poseOk)
                put("brightness_score", telemetry.brightnessScore)
                if (telemetry.analysisConfidence > 0) put("face_confidence", telemetry.analysisConfidence.toDouble())
                put("detection_mode", "ON_DEVICE_2D_FACE_AND_EYE_LANDMARK")
                put("verified_at_ms", System.currentTimeMillis())
            }
        }
    }
}
