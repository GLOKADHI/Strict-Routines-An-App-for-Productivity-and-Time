package com.strictrecords.app

/** Monotonic, bounded retries shared by screen-on and screen-off sync ticks. */
class BluetoothAutoSyncPolicy {
    private var nextAt = 0L
    private var failureDelay = 15_000L
    @Synchronized fun isDue(now: Long) = now >= nextAt
    @Synchronized fun succeeded(now: Long) { nextAt = now + 60_000L; failureDelay = 15_000L }
    @Synchronized fun failed(now: Long) { nextAt = now + failureDelay; failureDelay = (failureDelay * 2).coerceAtMost(300_000L) }
    @Synchronized fun retryNow() { nextAt = 0L; failureDelay = 15_000L }
}
