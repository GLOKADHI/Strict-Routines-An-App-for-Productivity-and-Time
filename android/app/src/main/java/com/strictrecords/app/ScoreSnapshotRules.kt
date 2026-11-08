package com.strictrecords.app

object ScoreSnapshotRules {
    fun displayScore(localVerifiedScore: Int, serverScore: Int?, trustedSnapshot: Boolean): Int {
        // Windows acknowledges replicated records. Only local confirmed transactions set the APK score.
        return localVerifiedScore.coerceAtMost(100)
    }

    fun shouldApply(savedRevision: Long, savedTimeMs: Long, incomingRevision: Long, incomingTimeMs: Long): Boolean =
        incomingRevision >= 0 && incomingTimeMs > 0 && incomingRevision >= savedRevision &&
            (incomingRevision > savedRevision || incomingTimeMs >= savedTimeMs)
}
