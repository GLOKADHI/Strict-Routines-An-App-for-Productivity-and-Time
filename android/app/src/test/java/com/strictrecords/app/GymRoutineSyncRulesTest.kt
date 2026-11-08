package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class GymRoutineSyncRulesTest {
    @Test fun exactCommittedAcknowledgmentCanClearAnOlderReplayWithoutReplacingNewerSnapshot() {
        assertTrue(GymRoutineSyncRules.mayAcknowledge("owner", "owner", "request-a", "request-a", true))
        assertFalse(GymRoutineSyncRules.mayReplace("owner", "owner", 9, 2))
        assertFalse(GymRoutineSyncRules.mayAcknowledge("owner", "other", "request-a", "request-a", true))
        assertFalse(GymRoutineSyncRules.mayAcknowledge("owner", "owner", "request-a", "request-b", true))
        assertFalse(GymRoutineSyncRules.mayAcknowledge("owner", "owner", "request-a", "request-a", false))
    }
    @Test fun staleSnapshotCannotReplaceNewerRevision() {
        assertFalse(GymRoutineSyncRules.mayReplace("owner", "owner", 9, 8))
        assertTrue(GymRoutineSyncRules.mayReplace("owner", "owner", 9, 9))
        assertTrue(GymRoutineSyncRules.mayReplace("owner", "owner", 9, 10))
    }
    @Test fun foreignOrMissingAccountCannotReplaceCache() {
        assertFalse(GymRoutineSyncRules.mayReplace("owner", "other", 0, 100))
        assertFalse(GymRoutineSyncRules.mayReplace("", "", 0, 0))
        assertFalse(GymRoutineSyncRules.mayReplace("owner", "owner", 0, -1))
    }
    @Test fun automaticRefreshIsThrottledButAllowsElapsedClockReset() {
        assertTrue(GymRoutineSyncRules.automaticRefreshDue(100, null))
        assertFalse(GymRoutineSyncRules.automaticRefreshDue(100, 100))
        assertFalse(GymRoutineSyncRules.automaticRefreshDue(30_099, 100))
        assertTrue(GymRoutineSyncRules.automaticRefreshDue(30_100, 100))
        assertTrue(GymRoutineSyncRules.automaticRefreshDue(50, 100))
    }
}
