package com.strictrecords.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.session.MediaController
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.util.Log
import android.view.KeyEvent
import androidx.core.app.NotificationCompat
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Part C (Sections 13–19): Background & Foreground Music/Video Playback Restriction Manager.
 *
 * Monitors active Android MediaSessions (`MediaSessionManager` + `StrictMediaNotificationListener`)
 * and enforces deterministic playback permissions via `MediaRestrictionPolicyEngine` and
 * `DisciplineScoringRules`.
 */
object BackgroundMusicRestrictionManager {
    private const val TAG = "RestrictedPlayback"
    const val CHANNEL_ID = "strict_music_restriction_alerts"
    val MONITORED_MUSIC_PACKAGES = setOf(
        "com.spotify.music",
        "com.spotify.lite",
        "com.sec.android.app.music",
        "com.gaana",
        "com.jio.media.jiobeats"
    )
    private val workerDelegate = lazy {
        HandlerThread("StrictRecords-MediaControl", android.os.Process.THREAD_PRIORITY_BACKGROUND)
            .apply { start() }.let { Handler(it.looper) }
    }
    private val worker by workerDelegate
    private val checkQueued = AtomicBoolean(false)
    private val enforcer = RestrictedPlaybackEnforcer()
    private data class Subscription(val controller: MediaController, val callback: MediaController.Callback)
    private val subscriptions = mutableMapOf<MediaSession.Token, Subscription>()
    private val rechecks = mutableMapOf<MediaSession.Token, Runnable>()
    private val lastStatus = mutableMapOf<String, String>()
    private var fallbackAt = 0L

    fun isMonitoredMusicPackage(packageName: String?): Boolean =
        TargetAppMonitoringPolicy.targetForPackage(packageName)?.isMusic == true

    fun isMonitoredPlaybackPackage(packageName: String?): Boolean {
        if (packageName.isNullOrBlank() || packageName == "com.strictrecords.app") return false
        if (packageName in TargetAppMonitoringPolicy.DEFAULT_FINANCIAL_PACKAGES) return false
        return TargetAppMonitoringPolicy.isTargetPackage(packageName) ||
            packageName in MediaRestrictionPolicyEngine.MUSIC_PACKAGES ||
            packageName in MediaRestrictionPolicyEngine.VIDEO_AND_OTT_PACKAGES ||
            packageName in MediaRestrictionPolicyEngine.SOCIAL_VIDEO_PACKAGES
    }

    fun isMediaSessionControlAvailable(context: Context): Boolean =
        StrictMediaNotificationListener.isNotificationAccessGranted(context)

    fun mayControlPackageSession(context: Context, packageName: String?): Boolean {
        if (packageName.isNullOrBlank() || packageName == context.packageName) return false
        if (FinancialSafePause.isPauseActive(context) || FinancialSafePause.isProtectedFinancialPackage(context, packageName)) return false
        if (packageName in TargetAppMonitoringPolicy.DEFAULT_FINANCIAL_PACKAGES) return false
        if (TargetAppMonitoringPolicy.mayMonitor(context, packageName)) return true
        if (!TargetAppMonitoringPolicy.hasUsageConsent(context)) return false
        return packageName in MediaRestrictionPolicyEngine.ALL_MONITORED_MEDIA_PACKAGES
    }

    fun evaluatePackageRestriction(context: Context, packageName: String): DisciplineScoringRules.RestrictionDecision {
        val db = StrictRecordsLocalDb.getInstance(context)
        val baseDecision = DisciplineScoringRules.evaluateRestrictionDecision(
            db.getScore(db.nowDate(), false),
            packageName,
            appLocks = db.getRemoteAppLocks(),
            isEducationalUnlockActive = db.isEducationalUnlockActive(),
            context = context,
            db = db
        )
        val mediaPolicy = MediaRestrictionPolicyEngine.evaluateLivePackage(context, packageName, "PLAYING")
        if (mediaPolicy.isBlockedOrExpired && !baseDecision.isRestricted) {
            return baseDecision.copy(
                action = "RESTRICTED",
                isRestricted = true,
                reason = mediaPolicy.reason,
                effectivePrivilegeDecision = "BLOCK"
            )
        }
        return baseDecision
    }

    fun onActiveSessionsChanged(context: Context, controllers: List<MediaController>?) {
        val app = context.applicationContext
        worker.post { reconcile(app, controllers.orEmpty()) }
    }

    private fun reconcile(context: Context, controllers: List<MediaController>) {
        val permitted = controllers.filter {
            it.packageName != context.packageName &&
                (isMonitoredPlaybackPackage(it.packageName) || it.packageName in MediaRestrictionPolicyEngine.BROWSER_MEDIA_PACKAGES) &&
                mayControlPackageSession(context, it.packageName)
        }
        val current = permitted.map { it.sessionToken }.toSet()
        subscriptions.keys.toList().filter { it !in current }.forEach { detach(it) }
        for (controller in permitted) {
            val token = controller.sessionToken
            if (token !in subscriptions) {
                val callback = object : MediaController.Callback() {
                    override fun onPlaybackStateChanged(state: PlaybackState?) {
                        if (state != null) enforce(context, controller)
                    }
                    override fun onSessionDestroyed() { detach(token) }
                }
                runCatching {
                    controller.registerCallback(callback, worker)
                    subscriptions[token] = Subscription(controller, callback)
                }.onFailure { Log.w(TAG, "Session callback unavailable: ${it.javaClass.simpleName}") }
            }
            enforce(context, controller)
        }
    }

    private fun detach(token: MediaSession.Token) {
        subscriptions.remove(token)?.let { runCatching { it.controller.unregisterCallback(it.callback) } }
        rechecks.remove(token)?.let { worker.removeCallbacks(it) }
        enforcer.forget(token)
    }

    fun onListenerDisconnected() {
        if (!workerDelegate.isInitialized()) return
        worker.post { subscriptions.keys.toList().forEach { detach(it) }; enforcer.clear() }
    }

    private fun enforce(context: Context, controller: MediaController) {
        val pkg = controller.packageName
        val token = controller.sessionToken
        rechecks.remove(token)?.let { worker.removeCallbacks(it) }
        try {
            val authorized = pkg != context.packageName &&
                (isMonitoredPlaybackPackage(pkg) || pkg in MediaRestrictionPolicyEngine.BROWSER_MEDIA_PACKAGES) &&
                mayControlPackageSession(context, pkg)
            val pbState = controller.playbackState?.state ?: PlaybackState.STATE_NONE
            val isPlaying = MediaRestrictionPolicyEngine.isPlaybackStateActive(pbState)
            val mediaPolicy = if (authorized) {
                MediaRestrictionPolicyEngine.evaluateLivePackage(
                    context,
                    pkg,
                    if (isPlaying) "PLAYING" else "PAUSED"
                )
            } else null
            val decision = if (authorized) evaluatePackageRestriction(context, pkg) else null
            val shouldBlock = (decision?.isRestricted == true) || (mediaPolicy?.isBlockedOrExpired == true)

            val player = object : RestrictedPlaybackEnforcer.Player {
                override val identity: Any = token
                override val packageName = pkg
                override val state: Int get() = controller.playbackState?.state ?: PlaybackState.STATE_NONE
                override val canStop: Boolean get() = ((controller.playbackState?.actions ?: 0L) and PlaybackState.ACTION_STOP) != 0L
                override fun pause() { controller.transportControls.pause() }
                override fun stop() { controller.transportControls.stop() }
                override fun pauseButton() {
                    controller.dispatchMediaButtonEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_MEDIA_PAUSE))
                    controller.dispatchMediaButtonEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_MEDIA_PAUSE))
                }
            }
            val result = enforcer.enforce(player, SystemClock.elapsedRealtime(), shouldBlock)
            if (decision != null && result.status !in setOf("NOT_RESTRICTED", "IDLE")) {
                report(context, decision, result.status)
                if (mediaPolicy != null) {
                    MediaRestrictionPolicyEngine.recordAndReportMediaEvent(context, mediaPolicy, result.status)
                }
            } else if (result.status == "NOT_RESTRICTED") {
                cancelNotification(context, pkg)
            }
            result.recheckAfterMs?.let { delay ->
                val check = Runnable { rechecks.remove(token); enforce(context, controller) }
                rechecks[token] = check
                worker.postDelayed(check, delay)
            }
        } catch (error: Exception) {
            lastStatus[pkg] = "CONTROL_UNAVAILABLE"
            Log.w(TAG, "Media control unavailable for selected target: ${error.javaClass.simpleName}")
        }
    }

    fun checkAndEnforceMusicRestrictions(context: Context, triggerReason: String = "routine") {
        if (!checkQueued.compareAndSet(false, true)) return
        val app = context.applicationContext
        worker.post {
            checkQueued.set(false)
            if (!isMediaSessionControlAvailable(app)) {
                subscriptions.keys.toList().forEach { detach(it) }
                return@post
            }
            reconcile(app, StrictMediaNotificationListener.getActiveMediaControllers(app))
        }
    }

    /** At the popup boundary only; never from a remembered background package. */
    fun pauseRestrictedPlayback(context: Context, packageName: String) {
        if (!isMonitoredPlaybackPackage(packageName) || !mayControlPackageSession(context, packageName)) return
        checkAndEnforceMusicRestrictions(context, "popup")
        if (isMediaSessionControlAvailable(context)) return
        if (WakeUpPlaybackService.isPlaybackActive() ||
            StrictAccessibilityService.activeInstance?.confirmedForegroundTargetPackage() != packageName) return
        val app = context.applicationContext
        worker.post {
            if (WakeUpPlaybackService.isPlaybackActive() || !mayControlPackageSession(app, packageName)) return@post
            val now = SystemClock.elapsedRealtime()
            val decision = evaluatePackageRestriction(app, packageName)
            if (now - fallbackAt < 1000L || !decision.isRestricted) return@post
            if (StrictAccessibilityService.activeInstance?.confirmedForegroundTargetPackage() != packageName) return@post
            fallbackAt = now
            runCatching {
                for (action in listOf(KeyEvent.ACTION_DOWN, KeyEvent.ACTION_UP)) {
                    app.sendBroadcast(
                        Intent(Intent.ACTION_MEDIA_BUTTON)
                            .setPackage(packageName)
                            .putExtra(Intent.EXTRA_KEY_EVENT, KeyEvent(action, KeyEvent.KEYCODE_MEDIA_PAUSE))
                    )
                }
            }
            val mediaPolicy = MediaRestrictionPolicyEngine.evaluateLivePackage(app, packageName, "FOREGROUND_BLOCKED")
            MediaRestrictionPolicyEngine.recordAndReportMediaEvent(app, mediaPolicy, "MEDIA_BUTTON_PAUSE_FALLBACK")
            report(app, decision, "MEDIA_ACCESS_REQUIRED")
        }
    }

    fun onTargetAppForegrounded(context: Context, foregroundPkg: String) {
        if (isMonitoredPlaybackPackage(foregroundPkg) || foregroundPkg in MediaRestrictionPolicyEngine.BROWSER_MEDIA_PACKAGES) {
            checkAndEnforceMusicRestrictions(context, "foreground")
        }
    }

    fun onTargetAppBackgrounded(context: Context, backgroundedPkg: String) {
        if (isMonitoredPlaybackPackage(backgroundedPkg) || backgroundedPkg in MediaRestrictionPolicyEngine.BROWSER_MEDIA_PACKAGES) {
            checkAndEnforceMusicRestrictions(context, "background")
        }
    }

    fun onRestrictionStateChanged(context: Context, reason: String) {
        checkAndEnforceMusicRestrictions(context, reason)
    }

    fun onMusicAppNotificationPosted(context: Context, packageName: String) {
        if (isMonitoredPlaybackPackage(packageName) || packageName in MediaRestrictionPolicyEngine.BROWSER_MEDIA_PACKAGES) {
            checkAndEnforceMusicRestrictions(context, "notification")
        }
    }

    fun onMusicAppNotificationRemoved(context: Context, packageName: String) {
        if (isMonitoredPlaybackPackage(packageName) || packageName in MediaRestrictionPolicyEngine.BROWSER_MEDIA_PACKAGES) {
            checkAndEnforceMusicRestrictions(context, "notification_removed")
        }
    }

    private fun notificationId(pkg: String) = 9300 + (pkg.hashCode() and 0xffff)

    private fun cancelNotification(context: Context, pkg: String) {
        lastStatus.remove(pkg)
        (context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager)?.cancel(notificationId(pkg))
    }

    private fun report(context: Context, decision: DisciplineScoringRules.RestrictionDecision, status: String) {
        if (lastStatus[decision.packageName] == status) return
        lastStatus[decision.packageName] = status
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
        if (Build.VERSION.SDK_INT >= 26 && manager.getNotificationChannel(CHANNEL_ID) == null) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Restricted playback", NotificationManager.IMPORTANCE_DEFAULT)
            )
        }
        val detail = when (status) {
            "PAUSED" -> "Playback is paused."
            "MEDIA_ACCESS_REQUIRED" -> "Enable Media & Notification Access in StrictRoutines to control this app's background playback."
            "STILL_PLAYING", "CONTROL_FAILED" -> "The player has not confirmed it stopped. Background playback could not be stopped."
            "STATE_UNAVAILABLE" -> "The player did not report its playback state; pause could not be verified."
            else -> "Requesting playback pause…"
        }
        val intent = Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val pending = PendingIntent.getActivity(
            context,
            notificationId(decision.packageName),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val message = "$detail Current score: ${decision.dailyScore}; required: ${decision.requiredScore}."
        runCatching {
            manager.notify(
                notificationId(decision.packageName),
                NotificationCompat.Builder(context, CHANNEL_ID)
                    .setSmallIcon(R.drawable.ic_launcher)
                    .setContentTitle("StrictBot • ${decision.appName}")
                    .setContentText(message)
                    .setStyle(NotificationCompat.BigTextStyle().bigText(message))
                    .setContentIntent(pending)
                    .setOnlyAlertOnce(true)
                    .setAutoCancel(true)
                    .build()
            )
        }
    }
}
