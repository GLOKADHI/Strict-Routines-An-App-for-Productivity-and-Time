package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class BotAnnouncementPolicyTest {
    @Test fun pollingDoesNotRepeatTheSamePopupSpeech() {
        val policy = BotAnnouncementPolicy()
        assertTrue(policy.claim("restriction:youtube:entry1", 1_000, true, true))
        for (now in 2_000L..60_000L step 1_000) {
            assertFalse(policy.claim("restriction:youtube:entry1", now, true, true))
        }
        assertTrue(policy.claim("restriction:youtube:entry1", 61_000, true, true))
    }

    @Test fun differentTaskAndRestrictionEventsCanAnnounce() {
        val policy = BotAnnouncementPolicy()
        assertTrue(policy.claim("task:hourly1", 1_000, true, true))
        assertTrue(policy.claim("task:hourly2", 1_001, true, true))
        assertTrue(policy.claim("restriction:instagram", 1_002, true, true))
    }

    @Test fun mutedOrUnsafeEventsAreNotConsumedOrReplayed() {
        val policy = BotAnnouncementPolicy()
        assertFalse(policy.claim("task:1", 1_000, false, true))
        assertFalse(policy.claim("task:1", 1_001, true, false))
        assertFalse(policy.claim("task:1", 1_002, true, false, manual = true))
        assertTrue(policy.claim("task:1", 1_003, true, true))
        assertFalse(policy.claim("task:1", 1_004, false, true, manual = true))
    }

    @Test fun explicitReplayBypassesOnlyTheCooldown() {
        val policy = BotAnnouncementPolicy()
        assertTrue(policy.claim("task:1", 100, true, true))
        assertTrue(policy.claim("task:1", 101, true, true, manual = true))
        assertFalse(policy.claim("task:1", 102, true, true))
        assertFalse(policy.claim("", 103, true, true, manual = true))
    }

    @Test fun monotonicClockResetAndHistoryEvictionDoNotBlockNewEvents() {
        val policy = BotAnnouncementPolicy()
        assertTrue(policy.claim("old", 20_000, true, true))
        assertTrue(policy.claim("old", 100, true, true))
        for (index in 1..65) assertTrue(policy.claim("task:$index", 101, true, true))
        assertTrue(policy.claim("old", 102, true, true))
    }
}
