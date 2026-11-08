package com.strictrecords.app

/** Only temporary eligibility, never a points balance or ledger entry. */
class DemoAccessLease {
    companion object {
        const val ACCESS_POINTS = 81
        const val DURATION_MS = 30_000L
        const val COOLDOWN_MS = 7L * 24 * 60 * 60 * 1000
    }
    private var owner = ""
    private var receivedAt = 0L
    private var deadline = 0L

    @Synchronized fun accept(identity: String, last: Long, expiry: Long, serverNow: Long,
                             requestStarted: Long, received: Long): Boolean {
        clear()
        if (identity.isBlank() || last <= 0 || expiry - last != DURATION_MS ||
            serverNow !in last until expiry || requestStarted < 0 ||
            received - requestStarted !in 0..DURATION_MS) return false
        // Include network time in the budget. Delayed replies can shorten a grant,
        // but cannot extend it beyond the authority's 30-second test period.
        val end = requestStarted + (expiry - serverNow).coerceAtMost(DURATION_MS)
        if (end <= received) return false
        owner = identity; receivedAt = received; deadline = end
        return true
    }
    @Synchronized fun remaining(identity: String, elapsed: Long): Long {
        if (identity != owner || elapsed < receivedAt || elapsed >= deadline) { clear(); return 0 }
        return deadline - elapsed
    }
    @Synchronized fun effective(actualPoints: Int, identity: String, elapsed: Long): Int =
        if (remaining(identity, elapsed) > 0) ACCESS_POINTS else actualPoints
    @Synchronized fun clear() { owner = ""; receivedAt = 0; deadline = 0 }
}
