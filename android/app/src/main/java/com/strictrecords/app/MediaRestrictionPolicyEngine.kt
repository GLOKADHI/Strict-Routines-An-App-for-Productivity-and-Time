package com.strictrecords.app

import android.content.Context
import android.media.session.PlaybackState
import android.util.Log
import org.json.JSONObject
import java.util.Locale
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * Part C (Sections 13–19): Deterministic Authoritative Media Permission Policy Engine.
 *
 * Evaluates foreground and background music, video, OTT, social video, PiP, and browser
 * media playback against:
 * - Current Strict Session phase (`FOCUS_ACTIVE` vs `FOCUS_BUFFER` / `PROOF_SUBMISSION_OPEN` / `INACTIVE`)
 * - Authoritative points balance (`pointsBalance` vs threshold `80`)
 * - Active temporary rewards (`YOUTUBE_REWARD`, `GYM_MODE_MUSIC`, `EDUCATIONAL_VIDEO`)
 * - Expiry timestamps so expired entitlements immediately stop active playback.
 */
object MediaRestrictionPolicyEngine {
    private const val TAG = "MediaPolicyEngine"
    const val POLICY_VERSION = "sr_media_policy_v2.4"

    // Section 14: Standardized Permission Decisions
    const val DECISION_ALLOWED = "ALLOWED"
    const val DECISION_BLOCKED = "BLOCKED"
    const val DECISION_TEMPORARILY_ALLOWED = "TEMPORARILY_ALLOWED"
    const val DECISION_AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    const val DECISION_POLICY_UNAVAILABLE = "POLICY_UNAVAILABLE"
    const val DECISION_ALLOWED_BY_SUNNXT_SCHEDULE = "ALLOWED_BY_SUNNXT_SCHEDULE"
    const val DECISION_ALLOWED_BY_REWARD = "ALLOWED_BY_REWARD"

    const val STANDARD_RESTRICTION_MESSAGE =
        "Media playback is currently restricted by StrictRecords. SUN NXT is permitted daily from 8:25 PM to 9:35 PM. Other media requires an active authorized reward or schedule."

    val SUN_NXT_PACKAGES = setOf(
        "com.suntv.sunnxt",
        "com.sunsnxt.app"
    )

    // Section 19: Standardized Audit Event Codes
    const val EVENT_PROHIBITED_MEDIA_DETECTED = "PROHIBITED_MEDIA_DETECTED"
    const val EVENT_BACKGROUND_AUDIO_BLOCKED = "BACKGROUND_AUDIO_BLOCKED"
    const val EVENT_VIDEO_PLAYBACK_BLOCKED = "VIDEO_PLAYBACK_BLOCKED"
    const val EVENT_REWARD_EXPIRED_REVOCATION = "REWARD_EXPIRED_REVOCATION"
    const val EVENT_AUTHORIZED_MEDIA_ALLOWED = "AUTHORIZED_MEDIA_ALLOWED"

    val MUSIC_PACKAGES = setOf(
        "com.spotify.music",
        "com.spotify.lite",
        "com.sec.android.app.music",
        "com.google.android.apps.youtube.music",
        "com.amazon.mp3",
        "com.apple.android.music",
        "com.gaana",
        "com.jio.media.jiobeats",
        "com.wynk.music",
        "com.soundcloud.android"
    )

    val VIDEO_AND_OTT_PACKAGES = setOf(
        "com.google.android.youtube",
        "com.android.youtube",
        "app.revanced.android.youtube",
        "app.rvx.android.youtube",
        "com.google.android.apps.youtube.mango",
        "com.netflix.mediaclient",
        "com.amazon.avod.thirdpartyclient",
        "in.startv.hotstar",
        "com.jio.media.ondemand",
        "com.sonyliv",
        "com.graymatrix.did",
        "org.videolan.vlc",
        "com.mxtech.videoplayer.ad",
        "com.mxtech.videoplayer.pro"
    )

    val SOCIAL_VIDEO_PACKAGES = setOf(
        "com.instagram.android",
        "com.instagram.lite",
        "com.instander.android",
        "com.aeroinsta.android",
        "com.zhiliaoapp.musically",
        "com.ss.android.ugc.trill",
        "com.snapchat.android",
        "com.facebook.katana",
        "com.twitter.android",
        "com.linkedin.android"
    )

    val BROWSER_MEDIA_PACKAGES = setOf(
        "com.android.chrome",
        "com.brave.browser",
        "org.mozilla.firefox",
        "com.sec.android.app.sbrowser",
        "com.microsoft.emmx",
        "com.opera.browser",
        "com.duckduckgo.mobile.android"
    )

    val GYM_ALLOWED_MUSIC_PACKAGES = setOf(
        "com.spotify.music",
        "com.spotify.lite",
        "com.sec.android.app.music"
    )

    val ALL_MONITORED_MEDIA_PACKAGES: Set<String> =
        MUSIC_PACKAGES + VIDEO_AND_OTT_PACKAGES + SOCIAL_VIDEO_PACKAGES + BROWSER_MEDIA_PACKAGES + SUN_NXT_PACKAGES

    // Tracks previously active temporary authorizations per package to detect AUTHORIZATION_EXPIRED
    private val lastActiveEntitlementByPkg = ConcurrentHashMap<String, Long>()
    // Deduplicates violation reports within a 15-second window per package + eventCode
    private val recentViolationTimestamps = ConcurrentHashMap<String, Long>()

    data class MediaPermissionEvaluation(
        val decision: String, // ALLOWED | BLOCKED | TEMPORARILY_ALLOWED | AUTHORIZATION_EXPIRED | POLICY_UNAVAILABLE
        val packageName: String,
        val mediaCategory: String, // MUSIC | VIDEO_OTT | SOCIAL_VIDEO | BROWSER_MEDIA | OTHER
        val mediaState: String,
        val sessionPhase: String,
        val pointsBalance: Int,
        val requiredPoints: Int,
        val activeReward: Boolean,
        val authorizationSource: String,
        val policyVersion: String = POLICY_VERSION,
        val expiryTimestampMs: Long = 0L,
        val reason: String,
        val eventCode: String
    ) {
        val isPermitted: Boolean
            get() = decision == DECISION_ALLOWED || decision == DECISION_TEMPORARILY_ALLOWED || decision == DECISION_ALLOWED_BY_SUNNXT_SCHEDULE || decision == DECISION_ALLOWED_BY_REWARD

        val isBlockedOrExpired: Boolean
            get() = decision == DECISION_BLOCKED || decision == DECISION_AUTHORIZATION_EXPIRED

        fun toJson(): JSONObject = JSONObject().apply {
            put("decision", decision)
            put("package_name", packageName)
            put("media_category", mediaCategory)
            put("media_state", mediaState)
            put("session_phase", sessionPhase)
            put("points_balance", pointsBalance)
            put("required_points", requiredPoints)
            put("active_reward", activeReward)
            put("authorization_source", authorizationSource)
            put("policy_version", policyVersion)
            put("expiry_timestamp_ms", expiryTimestampMs)
            put("reason", reason)
            put("event_code", eventCode)
        }
    }

    fun isMediaOrVideoPackage(packageName: String?): Boolean {
        if (packageName.isNullOrBlank()) return false
        return packageName in ALL_MONITORED_MEDIA_PACKAGES || TargetAppMonitoringPolicy.isTargetPackage(packageName)
    }

    fun classifyMediaCategory(packageName: String): String {
        return when (packageName) {
            in MUSIC_PACKAGES -> "MUSIC"
            in VIDEO_AND_OTT_PACKAGES -> "VIDEO_OTT"
            in SOCIAL_VIDEO_PACKAGES -> "SOCIAL_VIDEO"
            in BROWSER_MEDIA_PACKAGES -> "BROWSER_MEDIA"
            else -> if (TargetAppMonitoringPolicy.targetForPackage(packageName)?.isMusic == true) "MUSIC" else "VIDEO_OTT"
        }
    }

    fun isSunNxtScheduledWindowOpen(nowMs: Long = System.currentTimeMillis()): Boolean {
        return try {
            val tz = java.util.TimeZone.getTimeZone("Asia/Kolkata")
            val cal = java.util.Calendar.getInstance(tz)
            cal.timeInMillis = nowMs
            val hour = cal.get(java.util.Calendar.HOUR_OF_DAY)
            val minute = cal.get(java.util.Calendar.MINUTE)
            val currentMinutes = hour * 60 + minute
            val startMinutes = 20 * 60 + 25 // 8:25 PM = 1225
            val endMinutes = 21 * 60 + 35   // 9:35 PM = 1295
            currentMinutes in startMinutes until endMinutes
        } catch (_: Exception) {
            false
        }
    }

    fun isPlaybackStateActive(playbackState: Int): Boolean {
        return playbackState in setOf(
            PlaybackState.STATE_PLAYING,
            PlaybackState.STATE_FAST_FORWARDING,
            PlaybackState.STATE_REWINDING,
            PlaybackState.STATE_BUFFERING,
            PlaybackState.STATE_CONNECTING,
            PlaybackState.STATE_SKIPPING_TO_PREVIOUS,
            PlaybackState.STATE_SKIPPING_TO_NEXT,
            PlaybackState.STATE_SKIPPING_TO_QUEUE_ITEM
        )
    }

    /**
     * Section 14: Single deterministic policy function for all foreground and background
     * music/video/OTT/social/browser playback evaluations.
     */
    fun evaluateMediaPermission(
        packageName: String,
        mediaState: String,
        sessionPhase: String,
        pointsBalance: Int,
        activeReward: Boolean,
        authorizationSource: String,
        rewardExpiryMs: Long = 0L,
        isGymMusicAllowed: Boolean = false,
        isEducationalVideoAllowed: Boolean = false,
        educationalRemainingSec: Int = 0,
        isRemoteExplicitlyLocked: Boolean = false,
        requiredPointsThreshold: Int = 80,
        nowMs: Long = System.currentTimeMillis()
    ): MediaPermissionEvaluation {
        val cleanPkg = packageName.trim()
        if (cleanPkg.isBlank()) {
            return MediaPermissionEvaluation(
                decision = DECISION_POLICY_UNAVAILABLE,
                packageName = "",
                mediaCategory = "OTHER",
                mediaState = mediaState,
                sessionPhase = sessionPhase,
                pointsBalance = pointsBalance,
                requiredPoints = requiredPointsThreshold,
                activeReward = false,
                authorizationSource = "NONE",
                policyVersion = POLICY_VERSION,
                expiryTimestampMs = 0L,
                reason = "Package name unavailable for media policy evaluation.",
                eventCode = EVENT_PROHIBITED_MEDIA_DETECTED
            )
        }

        val category = classifyMediaCategory(cleanPkg)
        val normPhase = sessionPhase.trim().uppercase(Locale.US)
        val normSource = authorizationSource.trim().uppercase(Locale.US)

        // VLC Daily Unblock Override for today alone (2026-11-08)
        if (cleanPkg == "org.videolan.vlc") {
            val tz = java.util.TimeZone.getTimeZone("Asia/Kolkata")
            val cal = java.util.Calendar.getInstance(tz).apply { timeInMillis = nowMs }
            val todayStr = String.format(Locale.US, "%04d-%02d-%02d",
                cal.get(java.util.Calendar.YEAR),
                cal.get(java.util.Calendar.MONTH) + 1,
                cal.get(java.util.Calendar.DAY_OF_MONTH)
            )
            if (todayStr == "2026-11-08") {
                val endOfDayCal = java.util.Calendar.getInstance(tz).apply {
                    timeInMillis = nowMs
                    set(java.util.Calendar.HOUR_OF_DAY, 23)
                    set(java.util.Calendar.MINUTE, 59)
                    set(java.util.Calendar.SECOND, 59)
                    set(java.util.Calendar.MILLISECOND, 999)
                }
                return MediaPermissionEvaluation(
                    decision = DECISION_ALLOWED,
                    packageName = cleanPkg,
                    mediaCategory = "VIDEO_OTT",
                    mediaState = mediaState,
                    sessionPhase = normPhase,
                    pointsBalance = pointsBalance,
                    requiredPoints = requiredPointsThreshold,
                    activeReward = false,
                    authorizationSource = "DAILY_UNBLOCK_OVERRIDE",
                    policyVersion = POLICY_VERSION,
                    expiryTimestampMs = endOfDayCal.timeInMillis,
                    reason = "VLC Media Player authorized for today alone (2026-11-08).",
                    eventCode = EVENT_AUTHORIZED_MEDIA_ALLOWED
                )
            }
        }

        // SUN NXT Evaluation: Player-specific allowance daily from 8:25 PM to 9:35 PM IST
        if (cleanPkg in SUN_NXT_PACKAGES) {
            if (isSunNxtScheduledWindowOpen(nowMs)) {
                return MediaPermissionEvaluation(
                    decision = DECISION_ALLOWED_BY_SUNNXT_SCHEDULE,
                    packageName = cleanPkg,
                    mediaCategory = "VIDEO_OTT",
                    mediaState = mediaState,
                    sessionPhase = normPhase,
                    pointsBalance = pointsBalance,
                    requiredPoints = requiredPointsThreshold,
                    activeReward = false,
                    authorizationSource = "SUNNXT_SCHEDULE",
                    policyVersion = POLICY_VERSION,
                    expiryTimestampMs = 0L,
                    reason = "SUN NXT authorized under daily scheduled allowance (8:25 PM–9:35 PM IST).",
                    eventCode = EVENT_AUTHORIZED_MEDIA_ALLOWED
                )
            } else {
                return MediaPermissionEvaluation(
                    decision = DECISION_BLOCKED,
                    packageName = cleanPkg,
                    mediaCategory = "VIDEO_OTT",
                    mediaState = mediaState,
                    sessionPhase = normPhase,
                    pointsBalance = pointsBalance,
                    requiredPoints = requiredPointsThreshold,
                    activeReward = false,
                    authorizationSource = "NONE",
                    policyVersion = POLICY_VERSION,
                    expiryTimestampMs = 0L,
                    reason = STANDARD_RESTRICTION_MESSAGE,
                    eventCode = EVENT_VIDEO_PLAYBACK_BLOCKED
                )
            }
        }

        // 1. Strict Session FOCUS_ACTIVE phase blocks all entertainment media unless approved educational video
        if (normPhase == "FOCUS_ACTIVE") {
            if (isEducationalVideoAllowed && cleanPkg in VIDEO_AND_OTT_PACKAGES && educationalRemainingSec > 0) {
                val expMs = nowMs + (educationalRemainingSec * 1000L)
                lastActiveEntitlementByPkg[cleanPkg] = expMs
                return MediaPermissionEvaluation(
                    decision = DECISION_TEMPORARILY_ALLOWED,
                    packageName = cleanPkg,
                    mediaCategory = category,
                    mediaState = mediaState,
                    sessionPhase = normPhase,
                    pointsBalance = pointsBalance,
                    requiredPoints = requiredPointsThreshold,
                    activeReward = true,
                    authorizationSource = "EDUCATIONAL_VIDEO",
                    policyVersion = POLICY_VERSION,
                    expiryTimestampMs = expMs,
                    reason = "Approved educational video playback active during study session (${educationalRemainingSec}s remaining).",
                    eventCode = EVENT_AUTHORIZED_MEDIA_ALLOWED
                )
            }
            val blockCode = if (category == "MUSIC") EVENT_BACKGROUND_AUDIO_BLOCKED else EVENT_VIDEO_PLAYBACK_BLOCKED
            return MediaPermissionEvaluation(
                decision = DECISION_BLOCKED,
                packageName = cleanPkg,
                mediaCategory = category,
                mediaState = mediaState,
                sessionPhase = normPhase,
                pointsBalance = pointsBalance,
                requiredPoints = requiredPointsThreshold,
                activeReward = false,
                authorizationSource = "STRICT_SESSION_FOCUS_ACTIVE",
                policyVersion = POLICY_VERSION,
                expiryTimestampMs = 0L,
                reason = "Media and video playback are strictly prohibited during Strict Session FOCUS_ACTIVE.",
                eventCode = blockCode
            )
        }

        // 2. Check explicit remote lock override
        if (isRemoteExplicitlyLocked) {
            val blockCode = if (category == "MUSIC") EVENT_BACKGROUND_AUDIO_BLOCKED else EVENT_VIDEO_PLAYBACK_BLOCKED
            return MediaPermissionEvaluation(
                decision = DECISION_BLOCKED,
                packageName = cleanPkg,
                mediaCategory = category,
                mediaState = mediaState,
                sessionPhase = normPhase,
                pointsBalance = pointsBalance,
                requiredPoints = requiredPointsThreshold,
                activeReward = false,
                authorizationSource = "REMOTE_PC_LOCK",
                policyVersion = POLICY_VERSION,
                expiryTimestampMs = 0L,
                reason = "Locked by PC Control Center / Admin Restriction.",
                eventCode = blockCode
            )
        }

        // 3. Check active Gym Mode music entitlement (Spotify / Samsung Music only)
        if (isGymMusicAllowed && cleanPkg in GYM_ALLOWED_MUSIC_PACKAGES) {
            val expMs = if (rewardExpiryMs > nowMs) rewardExpiryMs else (nowMs + 60 * 60_000L)
            lastActiveEntitlementByPkg[cleanPkg] = expMs
            return MediaPermissionEvaluation(
                decision = DECISION_TEMPORARILY_ALLOWED,
                packageName = cleanPkg,
                mediaCategory = category,
                mediaState = mediaState,
                sessionPhase = normPhase,
                pointsBalance = pointsBalance,
                requiredPoints = requiredPointsThreshold,
                activeReward = true,
                authorizationSource = "GYM_MODE_MUSIC",
                policyVersion = POLICY_VERSION,
                expiryTimestampMs = expMs,
                reason = "Temporarily allowed during active Gym Mode music session.",
                eventCode = EVENT_AUTHORIZED_MEDIA_ALLOWED
            )
        }

        // 4. Check approved Educational Video entitlement (YouTube)
        if (isEducationalVideoAllowed && cleanPkg in VIDEO_AND_OTT_PACKAGES && educationalRemainingSec > 0) {
            val expMs = nowMs + (educationalRemainingSec * 1000L)
            lastActiveEntitlementByPkg[cleanPkg] = expMs
            return MediaPermissionEvaluation(
                decision = DECISION_TEMPORARILY_ALLOWED,
                packageName = cleanPkg,
                mediaCategory = category,
                mediaState = mediaState,
                sessionPhase = normPhase,
                pointsBalance = pointsBalance,
                requiredPoints = requiredPointsThreshold,
                activeReward = true,
                authorizationSource = "EDUCATIONAL_VIDEO",
                policyVersion = POLICY_VERSION,
                expiryTimestampMs = expMs,
                reason = "Temporarily allowed for approved educational video (${educationalRemainingSec}s remaining).",
                eventCode = EVENT_AUTHORIZED_MEDIA_ALLOWED
            )
        }

        // 5. Check active YouTube / Early Proof Entertainment Reward
        if (activeReward) {
            if (rewardExpiryMs in 1..nowMs) {
                lastActiveEntitlementByPkg.remove(cleanPkg)
                return MediaPermissionEvaluation(
                    decision = DECISION_AUTHORIZATION_EXPIRED,
                    packageName = cleanPkg,
                    mediaCategory = category,
                    mediaState = mediaState,
                    sessionPhase = normPhase,
                    pointsBalance = pointsBalance,
                    requiredPoints = requiredPointsThreshold,
                    activeReward = false,
                    authorizationSource = normSource.ifBlank { "YOUTUBE_REWARD" },
                    policyVersion = POLICY_VERSION,
                    expiryTimestampMs = rewardExpiryMs,
                    reason = "Temporary media reward expired; active playback revoked.",
                    eventCode = EVENT_REWARD_EXPIRED_REVOCATION
                )
            }
            val expMs = if (rewardExpiryMs > nowMs) rewardExpiryMs else (nowMs + 30 * 60_000L)
            lastActiveEntitlementByPkg[cleanPkg] = expMs
            return MediaPermissionEvaluation(
                decision = DECISION_TEMPORARILY_ALLOWED,
                packageName = cleanPkg,
                mediaCategory = category,
                mediaState = mediaState,
                sessionPhase = normPhase,
                pointsBalance = pointsBalance,
                requiredPoints = requiredPointsThreshold,
                activeReward = true,
                authorizationSource = normSource.ifBlank { "YOUTUBE_REWARD" },
                policyVersion = POLICY_VERSION,
                expiryTimestampMs = expMs,
                reason = "Temporarily allowed by active reward (${normSource.ifBlank { "YOUTUBE_REWARD" }}).",
                eventCode = EVENT_AUTHORIZED_MEDIA_ALLOWED
            )
        }

        // 6. Check if a previously active temporary reward for this package just expired
        val priorExpiry = lastActiveEntitlementByPkg.remove(cleanPkg)
        if (priorExpiry != null && pointsBalance < requiredPointsThreshold) {
            return MediaPermissionEvaluation(
                decision = DECISION_AUTHORIZATION_EXPIRED,
                packageName = cleanPkg,
                mediaCategory = category,
                mediaState = mediaState,
                sessionPhase = normPhase,
                pointsBalance = pointsBalance,
                requiredPoints = requiredPointsThreshold,
                activeReward = false,
                authorizationSource = "EXPIRED_ENTITLEMENT",
                policyVersion = POLICY_VERSION,
                expiryTimestampMs = priorExpiry,
                reason = "Temporary reward authorization expired and points balance ($pointsBalance) is below $requiredPointsThreshold.",
                eventCode = EVENT_REWARD_EXPIRED_REVOCATION
            )
        }

        // 7. Check Earned Daily Score Threshold (>= 80 points)
        // Note: Browsers playing media in background/foreground are only blocked when points < threshold
        if (pointsBalance >= requiredPointsThreshold) {
            return MediaPermissionEvaluation(
                decision = DECISION_ALLOWED,
                packageName = cleanPkg,
                mediaCategory = category,
                mediaState = mediaState,
                sessionPhase = normPhase,
                pointsBalance = pointsBalance,
                requiredPoints = requiredPointsThreshold,
                activeReward = false,
                authorizationSource = "EARNED_SCORE_THRESHOLD",
                policyVersion = POLICY_VERSION,
                expiryTimestampMs = 0L,
                reason = "Allowed by earned daily score ($pointsBalance/$requiredPointsThreshold).",
                eventCode = EVENT_AUTHORIZED_MEDIA_ALLOWED
            )
        }

        // 8. Otherwise BLOCKED
        val eventCode = when (category) {
            "MUSIC" -> EVENT_BACKGROUND_AUDIO_BLOCKED
            "VIDEO_OTT", "SOCIAL_VIDEO", "BROWSER_MEDIA" -> EVENT_VIDEO_PLAYBACK_BLOCKED
            else -> EVENT_PROHIBITED_MEDIA_DETECTED
        }
        return MediaPermissionEvaluation(
            decision = DECISION_BLOCKED,
            packageName = cleanPkg,
            mediaCategory = category,
            mediaState = mediaState,
            sessionPhase = normPhase,
            pointsBalance = pointsBalance,
            requiredPoints = requiredPointsThreshold,
            activeReward = false,
            authorizationSource = "NONE",
            policyVersion = POLICY_VERSION,
            expiryTimestampMs = 0L,
            reason = STANDARD_RESTRICTION_MESSAGE,
            eventCode = eventCode
        )
    }

    /**
     * Evaluates a live package on the device using current SQLite state, Strict Session phase,
     * YouTube reward state, Gym mode state, and Educational video state.
     */
    fun evaluateLivePackage(
        context: Context,
        packageName: String,
        mediaState: String = "PLAYING"
    ): MediaPermissionEvaluation {
        val app = context.applicationContext
        val db = StrictRecordsLocalDb.getInstance(app)
        val today = db.nowDate()
        val score = db.getScore(today, false)
        val strictActive = AndroidStrictMonitoringService.isServiceRunning || db.isStrictSessionActive()
        val sessionPhase = if (strictActive) {
            AndroidStrictMonitoringService.currentSessionPhase.ifBlank { "FOCUS_ACTIVE" }
        } else {
            "INACTIVE"
        }

        val ytActive = YouTubeRewardManager.isYouTubeActive(app)
        val ytRemainingSec = YouTubeRewardManager.remainingSeconds(app)
        val nowMs = System.currentTimeMillis()
        val rewardExpiryMs = if (ytActive && ytRemainingSec > 0) nowMs + (ytRemainingSec * 1000L) else 0L

        val gymMusicAllowed = db.isGymModeMusicAllowed()
        val eduAllowed = EducationalVideoPlaybackTracker.activeVideoId != null &&
            !EducationalVideoPlaybackTracker.isCompleted &&
            EducationalVideoPlaybackTracker.remainingSeconds > 0
        val eduRemainingSec = EducationalVideoPlaybackTracker.remainingSeconds.coerceAtLeast(0)

        val baseDecision = DisciplineScoringRules.evaluateRestrictionDecision(
            score = score,
            packageNameOrApp = packageName,
            appLocks = db.getRemoteAppLocks(),
            isEducationalUnlockActive = db.isEducationalUnlockActive(),
            context = app,
            db = db
        )

        val isRemoteExplicitlyLocked = baseDecision.isRestricted &&
            baseDecision.reason.contains("PC Control Center", ignoreCase = true)

        val authSource = when {
            eduAllowed && packageName in VIDEO_AND_OTT_PACKAGES -> "EDUCATIONAL_VIDEO"
            gymMusicAllowed && packageName in GYM_ALLOWED_MUSIC_PACKAGES -> "GYM_MODE_MUSIC"
            ytActive -> "YOUTUBE_REWARD"
            score >= baseDecision.requiredScore -> "EARNED_SCORE_THRESHOLD"
            else -> "NONE"
        }

        return evaluateMediaPermission(
            packageName = packageName,
            mediaState = mediaState,
            sessionPhase = sessionPhase,
            pointsBalance = score,
            activeReward = ytActive && !isRemoteExplicitlyLocked,
            authorizationSource = authSource,
            rewardExpiryMs = rewardExpiryMs,
            isGymMusicAllowed = gymMusicAllowed,
            isEducationalVideoAllowed = eduAllowed,
            educationalRemainingSec = eduRemainingSec,
            isRemoteExplicitlyLocked = isRemoteExplicitlyLocked,
            requiredPointsThreshold = baseDecision.requiredScore,
            nowMs = nowMs
        )
    }

    /**
     * Section 16 & 19: Records a single deduplicated media restriction audit event locally
     * and forwards it to the Windows controller without creating duplicate violations.
     */
    fun recordAndReportMediaEvent(
        context: Context,
        evaluation: MediaPermissionEvaluation,
        enforcementAction: String
    ) {
        val nowMs = System.currentTimeMillis()
        val dedupKey = "${evaluation.packageName}|${evaluation.eventCode}|$enforcementAction"
        val lastMs = recentViolationTimestamps[dedupKey] ?: 0L
        if (nowMs - lastMs < 15_000L) {
            return
        }
        recentViolationTimestamps[dedupKey] = nowMs

        try {
            val app = context.applicationContext
            val db = StrictRecordsLocalDb.getInstance(app)
            val detailsJson = evaluation.toJson().apply {
                put("enforcement_action", enforcementAction)
                put("timestamp_ms", nowMs)
            }
            app.getSharedPreferences("strictrecords_media_policy_v1", Context.MODE_PRIVATE)
                .edit()
                .putString("last_media_policy_event_json", detailsJson.toString())
                .apply()

            if (evaluation.isBlockedOrExpired) {
                val syncPayload = JSONObject().apply {
                    put("event_id", "media_enf_${UUID.randomUUID().toString().replace("-", "").take(16)}")
                    put("event_code", evaluation.eventCode)
                    put("package_name", evaluation.packageName)
                    put("media_category", evaluation.mediaCategory)
                    put("media_state", evaluation.mediaState)
                    put("session_phase", evaluation.sessionPhase)
                    put("decision", evaluation.decision)
                    put("points_balance", evaluation.pointsBalance)
                    put("required_points", evaluation.requiredPoints)
                    put("authorization_source", evaluation.authorizationSource)
                    put("policy_version", evaluation.policyVersion)
                    put("enforcement_action", enforcementAction)
                    put("reason", evaluation.reason)
                    put("timestamp", db.nowIso())
                }
                db.enqueueSyncEvent("MEDIA_RESTRICTION_ENFORCED", syncPayload, app)
            }
            Log.i(
                TAG,
                "MediaPolicy [${evaluation.eventCode}] pkg=${evaluation.packageName} decision=${evaluation.decision} action=$enforcementAction reason=${evaluation.reason}"
            )
        } catch (e: Exception) {
            Log.w(TAG, "Failed to log media restriction event: ${e.message}")
        }
    }
}
