package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class DemoAccessLeaseTest {
    private val server = 1_793_446_400_000L
    private fun grant(lease: DemoAccessLease, received: Long = 1000L) =
        lease.accept("account:device", server, server + 30000, server, 1000, received)

    @Test fun actualBalancesRemainIndependentAndLatestActualIsRestored() {
        for (actual in listOf(-7, 0, 45, 100)) {
            val lease = DemoAccessLease(); assertTrue(grant(lease))
            assertEquals(81, lease.effective(actual, "account:device", 1000))
            assertEquals(81, lease.effective(actual, "account:device", 30999))
            assertEquals(actual, lease.effective(actual, "account:device", 31000))
        }
        val lease = DemoAccessLease(); grant(lease)
        assertEquals(81, lease.effective(-7, "account:device", 2000))
        assertEquals(45, lease.effective(45, "account:device", 31000))
    }
    @Test fun existingControllerAppliesEachThreshold() {
        val lease = DemoAccessLease(); grant(lease)
        val effective = lease.effective(-7, "account:device", 2000)
        for (target in listOf("YouTube", "Instagram", "Spotify", "Samsung Music", "Gaana", "JioSaavn"))
            assertFalse(target, DisciplineScoringRules.evaluateRestrictionDecision(effective, target).isRestricted)
        assertTrue(DisciplineScoringRules.evaluateRestrictionDecision(effective, "YouTube", rewardThreshold = 90).isRestricted)
        assertTrue(DisciplineScoringRules.evaluateRestrictionDecision(effective, "Spotify", controlledThreshold = 90).isRestricted)
        assertTrue(DisciplineScoringRules.evaluateRestrictionDecision(lease.effective(-7,"account:device",31000), "YouTube").isRestricted)
    }
    @Test fun accessibilityGraceAndNonTargetExclusionsKeepPriority() {
        val decision = FloatingBotStateSelector.evaluate("com.google.android.youtube", 81, null, confirmedAccessibilityWarning = true)
        assertEquals(FloatingBotStateSelector.MessageState.ACCESSIBILITY_DISABLED, decision.state)
        assertFalse(FloatingBotStateSelector.evaluate("com.phonepe.app", 81, null, confirmedAccessibilityWarning = true).shouldShow)
        assertFalse(DisciplineScoringRules.evaluateRestrictionDecision(81, "com.phonepe.app").isRestricted)
    }
    @Test fun restartRebootAccountSwitchAndClockRollbackCannotReviveGrant() {
        val lease = DemoAccessLease(); grant(lease)
        assertEquals(-7, lease.effective(-7, "other-account", 2000))
        assertEquals(-7, lease.effective(-7, "account:device", 2000))
        grant(lease)
        assertEquals(-7, lease.effective(-7, "account:device", 999))
        assertEquals(-7, DemoAccessLease().effective(-7, "account:device", 2000))
        assertEquals(7L * 24 * 60 * 60 * 1000, DemoAccessLease.COOLDOWN_MS)
    }
    @Test fun expiryCanRearmPopupWithoutAFakeAppVisitOrHistoryIdentity() {
        val session = PopupEntrySession()
        val visit = session.observeTarget("com.google.android.youtube")
        assertTrue(session.claim("com.google.android.youtube", visit))
        session.dismiss("com.google.android.youtube")
        assertFalse(session.canPresent("com.google.android.youtube"))
        session.onEligibilityChanged("com.phonepe.app")
        assertFalse(session.canPresent("com.google.android.youtube"))
        session.onEligibilityChanged("com.google.android.youtube")
        assertEquals(visit, session.currentId())
        assertTrue(session.canPresent("com.google.android.youtube"))
    }
    @Test fun delayedAndInvalidReceiptsNeverExtendThirtySeconds() {
        val lease = DemoAccessLease()
        assertFalse(grant(lease, 31000))
        assertFalse(lease.accept("account",server,server+60000,server,1000,1000))
        assertTrue(grant(lease, 6000))
        assertEquals(25000L, lease.remaining("account:device",6000))
        assertEquals(0L, lease.remaining("account:device",31000))
    }
}
