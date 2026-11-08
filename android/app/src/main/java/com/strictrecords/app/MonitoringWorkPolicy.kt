package com.strictrecords.app

/** Scheduling only: does not decide eligibility, scores or financial protection. */
internal object MonitoringWorkPolicy {
    const val UI_SNAPSHOT_INTERVAL_MS = 30_000L
    const val WINDOW_EVENT_INTERVAL_MS = 500L
    const val UPDATE_CHECK_INTERVAL_MS = 15 * 60_000L

    fun retryDelayMs(consecutiveFailures: Int): Long =
        (30_000L * (1L shl (consecutiveFailures - 1).coerceIn(0, 4))).coerceAtMost(300_000L)

    fun secondsUntil(deadlineMs: Long, nowMs: Long): Long =
        if (deadlineMs <= nowMs) 0 else (deadlineMs - nowMs) / 1000L
}
