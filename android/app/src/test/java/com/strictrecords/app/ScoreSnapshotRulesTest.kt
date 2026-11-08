package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class ScoreSnapshotRulesTest {
    @Test fun aStaleOrLowerServerScoreCannotOverwriteAuthoritativeApkScore() {
        // Criterion 5: Windows EXE reconnects with stale lower score -> APK authoritative score preserved
        assertEquals(80, ScoreSnapshotRules.displayScore(80, 20, true))
        assertEquals(0, ScoreSnapshotRules.displayScore(0, -30, true))
        assertEquals(80, ScoreSnapshotRules.displayScore(80, 110, true))
    }
    @Test fun aHigherServerCacheCannotInventLocalTransactions() {
        assertEquals(80, ScoreSnapshotRules.displayScore(80, 90, true))
    }
    @Test fun anUntrustedOrMissingSnapshotCannotReplaceTheVerifiedLocalScore() {
        assertEquals(40, ScoreSnapshotRules.displayScore(40, 90, false))
        assertEquals(40, ScoreSnapshotRules.displayScore(40, null, true))
    }
    @Test fun staleResponsesCannotOverwriteNewerLedgerRevision() {
        assertFalse(ScoreSnapshotRules.shouldApply(81, 3000, 80, 4000))
        assertFalse(ScoreSnapshotRules.shouldApply(81, 3000, 81, 2999))
        assertTrue(ScoreSnapshotRules.shouldApply(81, 3000, 82, 2999))
        assertTrue(ScoreSnapshotRules.shouldApply(81, 3000, 81, 3001))
        assertFalse(ScoreSnapshotRules.shouldApply(-1, 0, -1, 1000))
    }
}
