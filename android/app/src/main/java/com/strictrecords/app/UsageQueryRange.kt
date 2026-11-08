package com.strictrecords.app

/** queryEvents uses an inclusive start and exclusive end. Persist the exclusive
 * end only after evidence and cursor commit atomically. No historical tail is guessed. */
data class UsageQueryRange(val startMs: Long, val endExclusiveMs: Long) {
    fun contains(timestampMs: Long): Boolean = timestampMs >= startMs && timestampMs < endExclusiveMs
    companion object {
        fun next(cursorMs: Long, endExclusiveMs: Long, maxRangeMs: Long): UsageQueryRange? {
            if (endExclusiveMs <= 0 || endExclusiveMs <= cursorMs || maxRangeMs <= 0) return null
            return UsageQueryRange(maxOf(1L, cursorMs, endExclusiveMs - maxRangeMs), endExclusiveMs)
        }
    }
}
