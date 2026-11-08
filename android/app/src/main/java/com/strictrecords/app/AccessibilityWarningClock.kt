package com.strictrecords.app

/** Presentation-only continuous-off timer. It cannot create a score transaction. */
internal object AccessibilityWarningClock {
    const val GRACE_MS = 300_000L
    enum class Evidence { DISABLED, ENABLED_DISCONNECTED, ENABLED_CONNECTED, UNKNOWN }
    data class State(val wall: Long = -1, val elapsed: Long = -1, val boot: Int = -1, val active: Boolean = false) {
        fun encode() = "$wall|$elapsed|$boot|$active"
        companion object {
            fun decode(value: String): State = runCatching {
                val parts = value.split('|')
                State(parts[0].toLong(), parts[1].toLong(), parts[2].toInt(), parts[3].toBooleanStrict())
            }.getOrDefault(State())
        }
    }
    fun observe(old: State, evidence: Evidence, wall: Long, elapsed: Long, boot: Int): State {
        if (evidence == Evidence.UNKNOWN) return old
        if (evidence == Evidence.ENABLED_CONNECTED) return State()
        if (evidence == Evidence.ENABLED_DISCONNECTED) return if (old.active) old else State()
        val started = if (old.wall < 0 || old.elapsed < 0) State(wall, elapsed, boot) else old
        val duration = if (started.boot == boot && elapsed >= started.elapsed) elapsed - started.elapsed
            else (wall - started.wall).coerceAtLeast(0)
        return started.copy(active = started.active || duration >= GRACE_MS)
    }
}
