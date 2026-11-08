package com.strictrecords.app

/** Bounded commands for one identified external session. No global audio/focus or scoring writes. */
class RestrictedPlaybackEnforcer {
    interface Player {
        val identity: Any
        val packageName: String
        val state: Int
        val canStop: Boolean
        fun pause()
        fun stop()
        fun pauseButton()
    }
    data class Result(val status: String, val recheckAfterMs: Long? = null)
    private data class Attempt(var count: Int, var nextAt: Long)
    private val attempts = mutableMapOf<Any, Attempt>()

    fun forget(identity: Any) { attempts.remove(identity) }
    fun clear() { attempts.clear() }

    fun enforce(player: Player, nowMs: Long, authorized: Boolean): Result {
        if (!authorized) { forget(player.identity); return Result("NOT_RESTRICTED") }
        // Android PlaybackState constants: PLAYING, FAST_FORWARDING, REWINDING,
        // BUFFERING, CONNECTING and the three SKIPPING states.
        val playing = player.state in setOf(3, 4, 5, 6, 8, 9, 10, 11)
        if (!playing) {
            val requested = attempts.remove(player.identity) != null
            return Result(if (requested && player.state in setOf(1, 2)) "PAUSED"
                else if (requested) "STATE_UNAVAILABLE" else "IDLE")
        }
        val prior = attempts[player.identity]
        if (prior != null && nowMs < prior.nextAt) return Result("PAUSE_REQUESTED", prior.nextAt - nowMs)
        if (prior?.count == 2) return Result("STILL_PLAYING")
        return try {
            if (prior == null) {
                try { player.pause() } catch (_: Exception) {
                    player.pauseButton()
                    if (player.canStop) player.stop()
                    attempts[player.identity] = Attempt(2, nowMs + 750)
                    return Result("STOP_REQUESTED", 750)
                }
                attempts[player.identity] = Attempt(1, nowMs + 350)
                Result("PAUSE_REQUESTED", 350)
            } else {
                player.pauseButton()
                if (player.canStop) player.stop() else player.pause()
                prior.count = 2; prior.nextAt = nowMs + 750
                Result("STOP_REQUESTED", 750)
            }
        } catch (_: Exception) {
            attempts[player.identity] = Attempt(2, nowMs)
            Result("CONTROL_FAILED")
        }
    }
}
