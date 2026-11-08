package com.strictrecords.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaMetadata
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Media Playback Foreground Service for Wake-Up Motivation Track Verification.
 *
 * Guarantees:
 * - Single persistent playback session and MediaSession owned by this foreground service.
 * - Continuous background playback, Bluetooth earbud route monitoring, anti-seek protection,
 *   and verified listening progress across app minimization, switching apps, screen lock, and screen off.
 * - Publishes genuine MediaSession PlaybackState and MediaMetadata for system media integration.
 * - Notification with transport controls (Play/Pause, Stop) surviving Activity recreation.
 * - Exercises the identical verification pipeline for both Real and Demo modes.
 */
class WakeUpPlaybackService : Service() {

    interface PlaybackListener {
        fun onStatus(message: String, progress: Int, positionMs: Int, durationMs: Int)
        fun onComplete(telemetry: JSONObject, isDemo: Boolean)
        fun onError(errorMessage: String)
    }

    companion object {
        private const val TAG = "WakeUpPlaybackService"
        const val CHANNEL_ID = "wakeup_playback_channel"
        const val NOTIFICATION_ID = 9101

        const val ACTION_START_OR_RESUME = "com.strictrecords.app.action.WAKEUP_PLAYBACK_START_OR_RESUME"
        const val ACTION_PAUSE = "com.strictrecords.app.action.WAKEUP_PLAYBACK_PAUSE"
        const val ACTION_STOP = "com.strictrecords.app.action.WAKEUP_PLAYBACK_STOP"

        const val EXTRA_DATE = "extra_date"
        const val EXTRA_ATTEMPT_ID = "extra_attempt_id"
        const val EXTRA_IS_DEMO = "extra_is_demo"

        @Volatile
        var activeSession: WakeUpAudioSession? = null
            private set

        @Volatile
        var isServiceRunning: Boolean = false
            private set

        private val listeners = CopyOnWriteArrayList<PlaybackListener>()

        fun registerListener(listener: PlaybackListener) {
            if (!listeners.contains(listener)) {
                listeners.add(listener)
            }
            activeSession?.let { session ->
                listener.onStatus(
                    session.lastStatusMessage.ifBlank { "Ready to verify motivation track playback." },
                    session.currentProgressPct,
                    session.currentPositionMs,
                    session.trackDurationMs
                )
            }
        }

        fun unregisterListener(listener: PlaybackListener) {
            listeners.remove(listener)
        }

        fun isPlaybackActive(): Boolean {
            return activeSession?.isBusy == true
        }

        fun isSessionPresent(): Boolean {
            val s = activeSession
            return s != null && !s.isClosed
        }

        fun startOrResume(context: Context, date: String, attemptId: String, demo: Boolean) {
            val intent = Intent(context, WakeUpPlaybackService::class.java).apply {
                action = ACTION_START_OR_RESUME
                putExtra(EXTRA_DATE, date)
                putExtra(EXTRA_ATTEMPT_ID, attemptId)
                putExtra(EXTRA_IS_DEMO, demo)
            }
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    ContextCompat.startForegroundService(context, intent)
                } else {
                    context.startService(intent)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to start WakeUpPlaybackService: ${e.message}", e)
            }
        }

        fun pause(context: Context, reason: String = "Playback paused") {
            activeSession?.pause(reason)
        }

        fun stop(context: Context) {
            val intent = Intent(context, WakeUpPlaybackService::class.java).apply {
                action = ACTION_STOP
            }
            try {
                context.startService(intent)
            } catch (_: Exception) {}
        }
    }

    private val uiHandler = Handler(Looper.getMainLooper())
    private var wakeLock: PowerManager.WakeLock? = null
    private var lastNotificationUpdateMs = 0L
    private var mediaSession: MediaSession? = null

    override fun onCreate() {
        super.onCreate()
        isServiceRunning = true
        ensureNotificationChannel()
        initMediaSession()
        acquireWakeLock()
    }

    private fun initMediaSession() {
        try {
            val session = MediaSession(this, "WakeUpPlaybackSession").apply {
                setCallback(object : MediaSession.Callback() {
                    override fun onPlay() {
                        activeSession?.startOrResume()
                    }

                    override fun onPause() {
                        activeSession?.pause("Playback paused by user")
                    }

                    override fun onStop() {
                        handleStop()
                    }
                })
                setFlags(MediaSession.FLAG_HANDLES_MEDIA_BUTTONS or MediaSession.FLAG_HANDLES_TRANSPORT_CONTROLS)
                isActive = true
            }
            mediaSession = session
        } catch (e: Exception) {
            Log.w(TAG, "MediaSession initialization error: ${e.message}")
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action ?: ACTION_START_OR_RESUME

        when (action) {
            ACTION_STOP -> {
                handleStop()
                return START_NOT_STICKY
            }
            ACTION_PAUSE -> {
                activeSession?.pause("Playback paused by user")
                updateNotification(
                    activeSession?.lastStatusMessage ?: "Playback paused",
                    isPlaying = false,
                    progressPct = activeSession?.currentProgressPct ?: 0,
                    posMs = activeSession?.currentPositionMs ?: 0,
                    durMs = activeSession?.trackDurationMs ?: 0
                )
                return START_NOT_STICKY
            }
            ACTION_START_OR_RESUME -> {
                val date = intent?.getStringExtra(EXTRA_DATE) ?: StrictRecordsLocalDb.getInstance(this).nowDate()
                val attemptId = intent?.getStringExtra(EXTRA_ATTEMPT_ID) ?: "attempt_${System.currentTimeMillis()}"
                val demo = intent?.getBooleanExtra(EXTRA_IS_DEMO, false) ?: false

                startForegroundWithInitialNotification(demo)
                handleStartOrResume(date, attemptId, demo)
                return START_NOT_STICKY
            }
            else -> return START_NOT_STICKY
        }
    }

    private fun handleStartOrResume(date: String, attemptId: String, demo: Boolean) {
        acquireWakeLock()

        val existing = activeSession
        if (existing != null && !existing.isClosed && existing.date == date && existing.demo == demo) {
            if (existing.isPaused) {
                existing.startOrResume()
            }
            return
        }

        // Clean up any stale session before creating new one
        existing?.stop()

        val db = StrictRecordsLocalDb.getInstance(this)
        val newSession = WakeUpAudioSession(
            context = applicationContext,
            db = db,
            date = date,
            attemptId = attemptId,
            demo = demo,
            onStatusUpdate = { message, progressPct, posMs, durMs ->
                dispatchStatus(message, progressPct, posMs, durMs, demo)
            },
            onCompleteCallback = { telemetry ->
                dispatchComplete(telemetry, demo)
            }
        )
        activeSession = newSession
        newSession.startOrResume()
    }

    private fun dispatchStatus(message: String, progressPct: Int, posMs: Int, durMs: Int, demo: Boolean) {
        val now = System.currentTimeMillis()
        val isPlaying = activeSession?.isBusy == true

        // Update MediaSession state
        try {
            val stateBuilder = PlaybackState.Builder()
                .setActions(PlaybackState.ACTION_PLAY or PlaybackState.ACTION_PAUSE or PlaybackState.ACTION_STOP or PlaybackState.ACTION_PLAY_PAUSE)
                .setState(
                    if (isPlaying) PlaybackState.STATE_PLAYING else PlaybackState.STATE_PAUSED,
                    posMs.toLong(),
                    if (isPlaying) 1.0f else 0.0f
                )
            mediaSession?.setPlaybackState(stateBuilder.build())

            val trackInfo = WakeUpMusicManager.getCurrentTrack(this)
            val trackTitle = trackInfo?.title ?: "Motivation Track"
            val metaBuilder = MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE, trackTitle)
                .putString(MediaMetadata.METADATA_KEY_ARTIST, "StrictRecords Wake-Up Verification")
                .putLong(MediaMetadata.METADATA_KEY_DURATION, durMs.toLong())
            mediaSession?.setMetadata(metaBuilder.build())
        } catch (_: Exception) {}

        // Throttle foreground notification updates to at most once per 800ms
        if (now - lastNotificationUpdateMs >= 800L || !isPlaying) {
            lastNotificationUpdateMs = now
            val trackInfo = WakeUpMusicManager.getCurrentTrack(this)
            val trackTitle = trackInfo?.title ?: "Motivation Track"
            val posSec = posMs / 1000
            val durSec = durMs / 1000
            val timeStr = String.format("%d:%02d / %d:%02d", posSec / 60, posSec % 60, durSec / 60, durSec % 60)
            val earbuds = activeSession?.lastRouteDeviceName?.takeIf { it.isNotBlank() } ?: "Earbuds"

            val body = if (isPlaying) {
                "$timeStr ($progressPct%) • $earbuds\n$trackTitle"
            } else {
                message
            }
            updateNotification(body, isPlaying = isPlaying, progressPct = progressPct, posMs = posMs, durMs = durMs)
        }

        uiHandler.post {
            for (listener in listeners) {
                try {
                    listener.onStatus(message, progressPct, posMs, durMs)
                } catch (e: Exception) {
                    Log.w(TAG, "Error in playback listener: ${e.message}")
                }
            }
        }
    }

    private fun dispatchComplete(telemetry: JSONObject, demo: Boolean) {
        releaseWakeLock()

        val completionText = if (demo) {
            "Demo verification completed — no points or attendance changed"
        } else {
            "Full wake-up track completed on verified earbuds ✓"
        }

        // Update MediaSession state to stopped
        try {
            val stateBuilder = PlaybackState.Builder()
                .setActions(PlaybackState.ACTION_STOP)
                .setState(PlaybackState.STATE_STOPPED, activeSession?.trackDurationMs?.toLong() ?: 0L, 0.0f)
            mediaSession?.setPlaybackState(stateBuilder.build())
        } catch (_: Exception) {}

        updateNotification(completionText, isPlaying = false, progressPct = 100, completed = true)

        uiHandler.post {
            for (listener in listeners) {
                try {
                    listener.onComplete(telemetry, demo)
                } catch (e: Exception) {
                    Log.w(TAG, "Error in completion listener: ${e.message}")
                }
            }
        }

        // Keep completion notification for a brief period then detach
        uiHandler.postDelayed({
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                    stopForeground(STOP_FOREGROUND_DETACH)
                } else {
                    @Suppress("DEPRECATION")
                    stopForeground(false)
                }
            } catch (_: Exception) {}
            stopSelf()
        }, 5000L)
    }

    private fun handleStop() {
        activeSession?.stop()
        activeSession = null
        releaseWakeLock()
        try {
            val stateBuilder = PlaybackState.Builder()
                .setActions(PlaybackState.ACTION_STOP)
                .setState(PlaybackState.STATE_STOPPED, 0L, 0.0f)
            mediaSession?.setPlaybackState(stateBuilder.build())
        } catch (_: Exception) {}
        try {
            stopForeground(true)
        } catch (_: Exception) {}
        stopSelf()
    }

    private fun startForegroundWithInitialNotification(demo: Boolean) {
        val title = if (demo) "StrictRecords • [Demo] Wake-Up Verification" else "StrictRecords • Wake-Up Verification"
        val notif = buildNotification(title, "Verifying Bluetooth earbuds route and preparing playback...", 0, false)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
        } else {
            startForeground(NOTIFICATION_ID, notif)
        }
    }

    private fun updateNotification(
        body: String,
        isPlaying: Boolean,
        progressPct: Int,
        completed: Boolean = false,
        posMs: Int = 0,
        durMs: Int = 0
    ) {
        val demo = activeSession?.demo == true
        val title = when {
            completed -> if (demo) "Demo Verification Complete" else "Wake-Up Verification Complete"
            demo -> "StrictRecords • [Demo] Wake-Up Verification"
            else -> "StrictRecords • Wake-Up Verification"
        }
        val notif = buildNotification(title, body, progressPct, isPlaying)
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
        nm?.notify(NOTIFICATION_ID, notif)
    }

    private fun buildNotification(title: String, body: String, progressPct: Int, isPlaying: Boolean): Notification {
        val openIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra("OPEN_WAKEUP_DIALOG", true)
            putExtra("IS_DEV_MODE", activeSession?.demo == true)
        }
        val contentPi = PendingIntent.getActivity(
            this,
            201,
            openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        // Transport action intents
        val playPauseIntent = Intent(this, WakeUpPlaybackService::class.java).apply {
            action = if (isPlaying) ACTION_PAUSE else ACTION_START_OR_RESUME
            putExtra(EXTRA_DATE, activeSession?.date ?: StrictRecordsLocalDb.getInstance(this@WakeUpPlaybackService).nowDate())
            putExtra(EXTRA_ATTEMPT_ID, activeSession?.attemptId ?: "")
            putExtra(EXTRA_IS_DEMO, activeSession?.demo == true)
        }
        val playPausePi = PendingIntent.getService(
            this,
            202,
            playPauseIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val stopIntent = Intent(this, WakeUpPlaybackService::class.java).apply {
            action = ACTION_STOP
        }
        val stopPi = PendingIntent.getService(
            this,
            203,
            stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val sessionToken = mediaSession?.sessionToken

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && sessionToken != null) {
            val nb = Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_launcher)
                .setContentTitle(title)
                .setContentText(body)
                .setContentIntent(contentPi)
                .setOngoing(isPlaying)
                .setOnlyAlertOnce(true)
                .setCategory(Notification.CATEGORY_TRANSPORT)
                .setStyle(
                    Notification.MediaStyle()
                        .setMediaSession(sessionToken)
                        .setShowActionsInCompactView(0)
                )

            val playPauseActionTitle = if (isPlaying) "Pause" else "Play"
            val playPauseActionIcon = android.R.drawable.ic_media_pause.takeIf { isPlaying } ?: android.R.drawable.ic_media_play
            nb.addAction(Notification.Action.Builder(playPauseActionIcon, playPauseActionTitle, playPausePi).build())
            nb.addAction(Notification.Action.Builder(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stopPi).build())

            if (progressPct in 1..99) {
                nb.setProgress(100, progressPct, false)
            } else {
                nb.setProgress(0, 0, false)
            }
            return nb.build()
        }

        // NotificationCompat fallback
        val builder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(contentPi)
            .setOngoing(isPlaying)
            .setOnlyAlertOnce(true)
            .setCategory(NotificationCompat.CATEGORY_TRANSPORT)
            .setPriority(NotificationCompat.PRIORITY_LOW)

        val playPauseActionTitle = if (isPlaying) "Pause" else "Play"
        val playPauseActionIcon = android.R.drawable.ic_media_pause.takeIf { isPlaying } ?: android.R.drawable.ic_media_play
        builder.addAction(playPauseActionIcon, playPauseActionTitle, playPausePi)
        builder.addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stopPi)

        if (progressPct in 1..99) {
            builder.setProgress(100, progressPct, false)
        } else {
            builder.setProgress(0, 0, false)
        }

        return builder.build()
    }

    private fun ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                val channel = NotificationChannel(
                    CHANNEL_ID,
                    "Wake-Up Motivation Music",
                    NotificationManager.IMPORTANCE_LOW
                ).apply {
                    description = "Persistent notification for wake-up music playback and Bluetooth earbud route verification"
                    setShowBadge(false)
                    lockscreenVisibility = Notification.VISIBILITY_PUBLIC
                }
                nm.createNotificationChannel(channel)
            }
        }
    }

    private fun acquireWakeLock() {
        if (wakeLock == null) {
            val pm = getSystemService(Context.POWER_SERVICE) as? PowerManager
            wakeLock = pm?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "StrictRecords:WakeUpPlaybackWakeLock")?.apply {
                setReferenceCounted(false)
            }
        }
        try {
            if (wakeLock?.isHeld == false) {
                wakeLock?.acquire(15 * 60 * 1000L) // 15-minute safety timeout
            }
        } catch (e: Exception) {
            Log.w(TAG, "WakeLock acquire error: ${e.message}")
        }
    }

    private fun releaseWakeLock() {
        try {
            if (wakeLock?.isHeld == true) {
                wakeLock?.release()
            }
        } catch (e: Exception) {
            Log.w(TAG, "WakeLock release error: ${e.message}")
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        isServiceRunning = false
        activeSession?.stop()
        activeSession = null
        try {
            mediaSession?.isActive = false
            mediaSession?.release()
            mediaSession = null
        } catch (_: Exception) {}
        releaseWakeLock()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
