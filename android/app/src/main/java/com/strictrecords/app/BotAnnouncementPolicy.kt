package com.strictrecords.app

/** Presentation-only speech eligibility. It never changes tasks, scores or access. */
internal class BotAnnouncementPolicy(private val cooldownMs: Long = 60_000L) {
    private val announced = linkedMapOf<String, Long>()

    @Synchronized
    fun claim(eventKey: String, nowMs: Long, enabled: Boolean, safeToSpeak: Boolean, manual: Boolean = false): Boolean {
        if (!enabled || !safeToSpeak || eventKey.isBlank()) return false
        if (manual) return true
        val previous = announced[eventKey]
        if (previous != null && nowMs >= previous && nowMs - previous < cooldownMs) return false
        announced.remove(eventKey)
        announced[eventKey] = nowMs
        while (announced.size > 64) announced.remove(announced.keys.first())
        return true
    }
}
