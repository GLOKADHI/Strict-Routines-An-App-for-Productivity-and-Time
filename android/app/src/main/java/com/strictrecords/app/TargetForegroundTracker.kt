package com.strictrecords.app

/**
 * Pure foreground-event reducer. It retains a target candidate, never an unrelated
 * package identity. A caller must still validate consent, installation, freshness
 * and live foreground confirmation through TargetAppMonitoringPolicy.
 */
class TargetForegroundTracker(allowedPackages: Set<String>) {
    private val allowedTargetPackages = allowedPackages
        .filter { TargetAppMonitoringPolicy.isTargetPackage(it) }.toSet()

    private var candidatePackage: String? = null
    private var candidateObservedAtMs = 0L
    private var latestEventAtMs = 0L

    val packageName: String? get() = candidatePackage
    val observedAtMs: Long get() = candidateObservedAtMs

    fun onResumed(pkg: String?, atMs: Long) {
        if (!acceptTimestamp(atMs)) return
        if (pkg != null && pkg in allowedTargetPackages) {
            candidatePackage = pkg
            candidateObservedAtMs = atMs
        } else {
            clearCandidate()
        }
    }

    fun onPaused(pkg: String?, atMs: Long) {
        if (!acceptTimestamp(atMs)) return
        if (pkg.isNullOrBlank() || pkg == candidatePackage) clearCandidate()
        // A different package's pause neither replaces the candidate nor extends
        // its freshness. No package identity from this event is retained.
    }

    fun clear(atMs: Long) {
        acceptTimestamp(atMs)
        clearCandidate()
    }

    private fun acceptTimestamp(atMs: Long): Boolean {
        if (atMs <= 0L || atMs < latestEventAtMs) {
            clearCandidate()
            return false
        }
        // Keep this watermark even after clearing so a late old target resume
        // cannot resurrect the app that preceded a newer unrelated event.
        latestEventAtMs = atMs
        return true
    }

    private fun clearCandidate() {
        candidatePackage = null
        candidateObservedAtMs = 0L
    }
}
