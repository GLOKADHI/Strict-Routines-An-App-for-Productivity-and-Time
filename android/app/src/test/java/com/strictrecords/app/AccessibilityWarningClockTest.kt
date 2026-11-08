package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class AccessibilityWarningClockTest {
    private val off=AccessibilityWarningClock.Evidence.DISABLED
    @Test fun onlyFiveContinuousConfirmedMinutesActivate(){
        val start=AccessibilityWarningClock.observe(AccessibilityWarningClock.State(),off,1_000,20_000,1)
        assertFalse(AccessibilityWarningClock.observe(start,off,300_999,319_999,1).active)
        assertTrue(AccessibilityWarningClock.observe(start,off,301_000,320_000,1).active)
    }
    @Test fun unknownTelemetryCannotStartOrActivateWarning(){
        val empty=AccessibilityWarningClock.State()
        assertEquals(empty,AccessibilityWarningClock.observe(empty,AccessibilityWarningClock.Evidence.UNKNOWN,900_000,900_000,1))
        val start=AccessibilityWarningClock.observe(empty,off,1_000,1_000,1)
        assertFalse(AccessibilityWarningClock.observe(start,AccessibilityWarningClock.Evidence.UNKNOWN,900_000,900_000,1).active)
    }
    @Test fun transientEnabledRebindBreaksUnconfirmedOffPeriod(){
        val start=AccessibilityWarningClock.observe(AccessibilityWarningClock.State(),off,1_000,1_000,1)
        val rebound=AccessibilityWarningClock.observe(start,AccessibilityWarningClock.Evidence.ENABLED_DISCONNECTED,290_000,290_000,1)
        assertEquals(AccessibilityWarningClock.State(),rebound)
        assertFalse(AccessibilityWarningClock.observe(rebound,off,301_000,301_000,1).active)
    }
    @Test fun activeWarningRequiresEnabledAndConnectedForRecovery(){
        val active=AccessibilityWarningClock.State(1_000,1_000,1,true)
        assertTrue(AccessibilityWarningClock.observe(active,AccessibilityWarningClock.Evidence.ENABLED_DISCONNECTED,500_000,500_000,1).active)
        assertEquals(AccessibilityWarningClock.State(),AccessibilityWarningClock.observe(active,
            AccessibilityWarningClock.Evidence.ENABLED_CONNECTED,500_000,500_000,1))
    }
    @Test fun sameBootUsesMonotonicTimeDespiteWallClockJump(){
        val start=AccessibilityWarningClock.State(1_000,1_000,1)
        assertFalse(AccessibilityWarningClock.observe(start,off,99_000_000,2_000,1).active)
        assertTrue(AccessibilityWarningClock.observe(start,off,0,301_000,1).active)
    }
    @Test fun rebootAndProcessRestartPreserveElapsedEvidence(){
        val saved=AccessibilityWarningClock.State(1_000,900_000,1).encode()
        val restored=AccessibilityWarningClock.State.decode(saved)
        assertTrue(AccessibilityWarningClock.observe(restored,off,301_000,20_000,2).active)
        assertFalse(AccessibilityWarningClock.observe(restored,off,200_000,20_000,2).active)
    }
    @Test fun corruptStorageDoesNotInventAnExpiredEpisode(){
        assertEquals(AccessibilityWarningClock.State(),AccessibilityWarningClock.State.decode("broken"))
        assertFalse(AccessibilityWarningClock.observe(AccessibilityWarningClock.State.decode("broken"),off,9_000_000,1_000,1).active)
    }
}
