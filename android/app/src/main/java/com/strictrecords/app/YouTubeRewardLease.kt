package com.strictrecords.app

/** A server receipt anchored to elapsed time; wall-clock edits cannot extend it. */
class YouTubeRewardLease {
    private var owner = ""
    private var deadline = 0L
    private var received = 0L
    private var verified = 0L
    private var activationKey = 0L
    @Synchronized fun clear() { owner = ""; deadline = 0; received = 0; verified = 0; activationKey = 0 }
    @Synchronized fun accept(scope: String, activation: Long, expiry: Long, serverNow: Long, started: Long, ended: Long): Boolean {
        val priorDeadline = if (owner == scope && activationKey == activation && ended >= received) deadline else 0L
        clear()
        if (scope.isBlank() || expiry - activation != 1_200_000L || activation <= 0 || serverNow !in activation until expiry || ended < started || ended - started > 30_000) return false
        owner = scope; received = ended; verified = ended
        activationKey = activation
        deadline = (started + (expiry - serverNow)).let { if(priorDeadline > 0) minOf(it,priorDeadline) else it }
        return remaining(scope, ended) > 0
    }
    @Synchronized fun remaining(scope: String, elapsed: Long): Long {
        if (scope != owner || elapsed < received || elapsed >= deadline || elapsed - verified > 30_000) { clear(); return 0 }
        return deadline - elapsed
    }
}
