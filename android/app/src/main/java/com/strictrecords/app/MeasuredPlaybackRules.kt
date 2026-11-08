package com.strictrecords.app

/** Credits only bounded media-position progress on an observed player route. */
object MeasuredPlaybackRules {
    fun credit(previousPositionMs: Int, positionMs: Int, elapsedMs: Long, routeVerified: Boolean): Long {
        val delta = positionMs.toLong() - previousPositionMs
        if (!routeVerified || elapsedMs !in 0..1000 || delta < 0 || delta > elapsedMs + 1500) return 0
        return delta
    }
}
