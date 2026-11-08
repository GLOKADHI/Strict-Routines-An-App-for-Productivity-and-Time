package com.strictrecords.app

/** Matches backend window_state.py without awarding attendance or points. */
object WindowStateRules {
    private val preservedTerminalSubmissions = setOf(
        "SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED",
        "COMPLETED", "REJECTED", "REJECTED_LATE", "PENDING_SYNC",
        "SKIPPED", "EXCUSED", "CANCELLED"
    )
    fun state(saved: String, nowMs: Long, startMs: Long, endMs: Long, eligible: Boolean = true): String = when {
        saved in preservedTerminalSubmissions -> saved
        startMs == Long.MIN_VALUE || endMs <= startMs -> "INVALID_WINDOW"
        nowMs >= endMs -> if (eligible) "MISSED" else "EXPIRED"
        nowMs >= startMs -> "OPEN"
        else -> "UPCOMING"
    }
    fun wakeUpWindowOpen(minuteOfDay: Int): Boolean = minuteOfDay in 420 until 570
}
