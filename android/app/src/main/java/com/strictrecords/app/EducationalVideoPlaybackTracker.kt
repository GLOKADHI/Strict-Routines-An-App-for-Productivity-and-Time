package com.strictrecords.app

import android.content.Context
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.PlaybackState
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Educational Video Playback Tracker for Android.
 * Monitors real playback progress via Android MediaSession through NotificationListenerService.
 * Reconciles disjoint watched intervals:
 * - Forward seeks do not credit skipped time.
 * - Backward seeks do not double-count replayed segments.
 * - Speed (e.g. 2x) accurately consumed.
 * - Stops and marks COMPLETED when full duration is watched.
 */
object EducationalVideoPlaybackTracker {
    private const val TAG = "EduPlaybackTracker"
    private val mainHandler by lazy { runCatching { Handler(Looper.getMainLooper()) }.getOrNull() }
    private val isTracking = AtomicBoolean(false)

    @Volatile var activeVideoId: String? = null
        private set
    @Volatile var allowedDurationSeconds: Int = 0
        private set
    @Volatile var consumedSeconds: Float = 0f
        private set
    @Volatile var remainingSeconds: Int = 0
        private set
    @Volatile var lastPlaybackState: String = "PAUSED"
        private set
    @Volatile var isCompleted: Boolean = false
        private set

    private var lastObservedPositionMs: Long = -1L
    private val watchedIntervals = mutableListOf<Pair<Float, Float>>()
    private var pollRunnable: Runnable? = null

    fun startTracking(videoId: String, durationSeconds: Int) {
        activeVideoId = videoId
        allowedDurationSeconds = durationSeconds
        consumedSeconds = 0f
        remainingSeconds = durationSeconds
        isCompleted = false
        watchedIntervals.clear()
        lastObservedPositionMs = -1L
        isTracking.set(true)

        scheduleNextPoll()
        try {
            Log.i(TAG, "Started educational playback tracking for video: $videoId (duration: $durationSeconds s)")
        } catch (_: Exception) {}
    }

    fun stopTracking() {
        isTracking.set(false)
        pollRunnable?.let { r -> mainHandler?.removeCallbacks(r) }
        pollRunnable = null
    }

    private fun scheduleNextPoll() {
        if (!isTracking.get()) return
        pollRunnable = Runnable {
            pollActiveMediaSession()
            scheduleNextPoll()
        }
        mainHandler?.postDelayed(pollRunnable!!, 1000L)
    }

    private fun pollActiveMediaSession() {
        val listener = StrictMediaNotificationListener.activeInstance ?: return
        val controllers = StrictMediaNotificationListener.getActiveMediaControllers(listener)
        val ytController = controllers.firstOrNull { it.packageName in setOf(
            "com.google.android.youtube", "com.android.youtube", "app.revanced.android.youtube"
        ) } ?: return

        val pbState = ytController.playbackState ?: return

        val state = pbState.state
        val isPlaying = state == PlaybackState.STATE_PLAYING
        lastPlaybackState = when (state) {
            PlaybackState.STATE_PLAYING -> "PLAYING"
            PlaybackState.STATE_PAUSED -> "PAUSED"
            PlaybackState.STATE_BUFFERING -> "BUFFERING"
            PlaybackState.STATE_STOPPED, PlaybackState.STATE_NONE -> "STOPPED"
            else -> "OTHER"
        }

        val curPosMs = pbState.position
        val curPosSec = (curPosMs / 1000f).coerceAtLeast(0f)

        if (isPlaying && lastObservedPositionMs >= 0) {
            val deltaMs = curPosMs - lastObservedPositionMs
            val deltaSec = deltaMs / 1000f
            // Continuous progression: delta between 0.1s and 2.5s
            if (deltaSec in 0.1f..2.5f) {
                val start = (lastObservedPositionMs / 1000f).coerceAtLeast(0f)
                val end = curPosSec
                watchedIntervals.add(start to end)
                recalculateWatchedProgress()
            }
        }

        lastObservedPositionMs = curPosMs
    }

    private fun recalculateWatchedProgress() {
        if (watchedIntervals.isEmpty()) return
        val sorted = watchedIntervals.sortedBy { it.first }
        val merged = mutableListOf<Pair<Float, Float>>()
        var current = sorted[0]
        for (i in 1 until sorted.size) {
            val next = sorted[i]
            if (next.first <= current.second + 0.5f) {
                current = current.first to maxOf(current.second, next.second)
            } else {
                merged.add(current)
                current = next
            }
        }
        merged.add(current)

        var totalWatched = 0f
        for (iv in merged) {
            totalWatched += (iv.second - iv.first)
        }

        consumedSeconds = totalWatched
        val rem = (allowedDurationSeconds - totalWatched).toInt().coerceAtLeast(0)
        remainingSeconds = rem

        if (rem <= 0 && allowedDurationSeconds > 0) {
            isCompleted = true
            stopTracking()
            try {
                Log.i(TAG, "Educational video $activeVideoId completed! Re-locking YouTube.")
            } catch (_: Exception) {}
        }
    }

    fun initForTesting(videoId: String, durationSeconds: Int) {
        resetForTesting()
        activeVideoId = videoId
        allowedDurationSeconds = durationSeconds
        remainingSeconds = durationSeconds
    }

    fun recordIntervalForTesting(start: Float, end: Float) {
        watchedIntervals.add(start to end)
        recalculateWatchedProgress()
    }

    fun resetForTesting() {
        stopTracking()
        activeVideoId = null
        allowedDurationSeconds = 0
        consumedSeconds = 0f
        remainingSeconds = 0
        lastPlaybackState = "PAUSED"
        isCompleted = false
        lastObservedPositionMs = -1L
        watchedIntervals.clear()
    }
}
