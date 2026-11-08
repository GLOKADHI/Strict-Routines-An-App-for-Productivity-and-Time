package com.strictrecords.app

object AlarmScheduleRules {
    fun shouldRebuild(previous: String?, current: String, forced: Boolean): Boolean = forced || previous != current

    /** An alarm or queued event belongs to the exact current proof-window version. */
    fun matchesCurrentWindow(eventStartMs: Long, eventEndMs: Long, currentStartMs: Long, currentEndMs: Long): Boolean =
        eventStartMs == currentStartMs && eventEndMs == currentEndMs && currentEndMs > currentStartMs
}
