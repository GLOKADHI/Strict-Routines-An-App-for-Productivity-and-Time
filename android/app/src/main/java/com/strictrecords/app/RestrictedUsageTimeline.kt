package com.strictrecords.app

/** Confirmed intervals only. This reducer records evidence; it never changes points. */
class RestrictedUsageTimeline {
    data class Session(val packageName: String, val startedAtMs: Long, var throughMs: Long,
                       val eligibilityKey: String, val restricted: Boolean, var confirmedMs: Long = 0L,
                       var evidencedMinutes: Long = 0L)
    data class Minutes(val packageName: String, val startedAtMs: Long, val endedAtMs: Long,
                       val firstMinute: Long, val lastMinute: Long, val confirmedMs: Long,
                       val eligibilityKey: String) {
        val count: Long get() = lastMinute - firstMinute + 1
        fun idempotencyMaterial(account: String, device: String): String =
            "ANDROID_RESTRICTED_APP_USAGE|$account|$device|$packageName|$startedAtMs|$firstMinute|$lastMinute"
    }
    var session: Session? = null
        private set
    var lastGap: String = "Fresh foreground entry required."
        private set

    fun begin(pkg: String, atMs: Long, restricted: Boolean?, interactive: Boolean, eligibilityKey: String) {
        if (pkg.isBlank() || atMs <= 0 || !interactive || restricted == null || eligibilityKey.isBlank()) {
            gap("Foreground, screen state or authoritative eligibility is unavailable.")
            return
        }
        val existing = session
        if (existing?.packageName == pkg && existing.eligibilityKey == eligibilityKey &&
            existing.restricted == restricted && atMs >= existing.throughMs) return
        session = Session(pkg, atMs, atMs, eligibilityKey, restricted)
        lastGap = ""
    }

    fun confirm(atMs: Long, reliable: Boolean): Minutes? {
        val active = session ?: return null
        if (!reliable || atMs < active.throughMs) {
            gap("Uncertain foreground, device lock, clock change or monitoring gap.")
            return null
        }
        if (active.restricted) active.confirmedMs += atMs - active.throughMs
        active.throughMs = atMs
        val complete = active.confirmedMs / 60_000L
        if (complete <= active.evidencedMinutes) return null
        val result = Minutes(active.packageName, active.startedAtMs, atMs,
            active.evidencedMinutes + 1, complete, active.confirmedMs, active.eligibilityKey)
        active.evidencedMinutes = complete
        return result
    }

    fun end(atMs: Long, reliable: Boolean): Minutes? {
        val result = confirm(atMs, reliable)
        session = null // Remainders never cross an exit, gap, lock or eligibility change.
        return result
    }

    fun gap(reason: String) { session = null; lastGap = reason }
    fun remainderMs(): Long = (session?.confirmedMs ?: 0L) % 60_000L
}
