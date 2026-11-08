package com.strictrecords.app

/** Package metadata only. Inactive background windows are not foreground evidence. */
object ForegroundWindowRules {
    /** Window-layer changes are not app entries. Overlays/IME/shade must not erase a target. */
    fun isApplicationTransition(eventType: Int, packageName: String?, ownPackage: String): Boolean =
        eventType == 32 && !packageName.isNullOrBlank() && packageName != ownPackage &&
            packageName !in setOf("android", "com.android.systemui", "com.samsung.android.honeyboard",
                "com.google.android.inputmethod.latin", "com.samsung.android.app.cocktailbarservice")

    /** Package-only platform events; stale candidates need fresh Usage Access corroboration. */
    fun confirmedEventPackage(candidate: String?, observedAtMs: Long, nowMs: Long,
                              usagePackage: String?, usageObservedAtMs: Long, usageConfirmed: Boolean,
                              keepPresentedTarget: Boolean = false): String? {
        if (candidate.isNullOrBlank() || observedAtMs <= 0L || nowMs < observedAtMs) return null
        // Keep an already attached shield for this uninterrupted target entry.
        // A genuine app exit clears the candidate; this cannot authorize a new popup.
        if (keepPresentedTarget) return candidate
        if (nowMs - observedAtMs <= TargetAppMonitoringPolicy.MAX_FOREGROUND_AGE_MS) return candidate
        return candidate.takeIf { usageConfirmed && usagePackage == candidate && usageObservedAtMs > 0L &&
            nowMs >= usageObservedAtMs && nowMs - usageObservedAtMs <= 20_000L }
    }

    data class Window(val packageName: String?, val application: Boolean,
                      val current: Boolean, val ownOverlay: Boolean = false)

    fun confirmedPackage(windows: List<Window>): String? {
        val current = windows.filter { it.current && !it.ownOverlay }
        if (current.isEmpty()) {
            // Touching our shield can make it the active accessibility window.
            // Keep the one visible application below that shield; an overlay
            // alone or multiple visible applications still cannot confirm it.
            if (windows.none { it.current && it.ownOverlay }) return null
            val applications = windows.filter { it.application && !it.ownOverlay }
            if (applications.isEmpty() || applications.any { it.packageName.isNullOrBlank() }) return null
            return applications.mapNotNull { it.packageName }.distinct().singleOrNull()
        }
        if (current.any { !it.application || it.packageName.isNullOrBlank() }) return null
        return current.mapNotNull { it.packageName }.distinct().singleOrNull()
    }
}
