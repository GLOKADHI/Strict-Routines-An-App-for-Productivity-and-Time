package com.strictrecords.app

/** Main-thread state for one bounded recheck of an already visible popup.
 * Repeated unknown observations cannot move its deadline. Cancellation makes
 * a delayed callback harmless even after another popup starts a new recheck. */
class PopupForegroundRecheck(private val delayMs: Long = 180L) {
    class Request internal constructor(val packageName: String, val deadlineMs: Long) {
        fun remainingMs(nowMs: Long): Long = (deadlineMs - nowMs).coerceAtLeast(0L)
    }

    private var pending: Request? = null

    fun schedule(visiblePackage: String, nowMs: Long): Request? {
        if (visiblePackage.isBlank() || pending != null) return null
        return Request(visiblePackage, nowMs + delayMs).also { pending = it }
    }

    fun complete(request: Request): Boolean {
        if (pending !== request) return false
        pending = null
        return true
    }

    fun cancel() { pending = null }
}
