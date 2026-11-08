package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class ForegroundWindowRulesTest {
    @Test fun overlayLayerAndTransientEventsDoNotEraseMusicTargetOrCreateNewVisits() {
        val own="com.strictrecords.app"
        for(pkg in listOf("com.spotify.music","com.sec.android.app.music","com.google.android.youtube")) {
            val tracker=TargetForegroundTracker(setOf(pkg));val entries=PopupEntrySession()
            if(ForegroundWindowRules.isApplicationTransition(32,pkg,own)) tracker.onResumed(pkg,1000)
            val entry=entries.observeTarget(pkg);assertTrue(entries.claim(pkg,entry))
            for(source in listOf(null,own,"android","com.android.systemui","com.samsung.android.honeyboard")) {
                assertFalse(ForegroundWindowRules.isApplicationTransition(4194304,source,own))
                assertFalse(ForegroundWindowRules.isApplicationTransition(32,source,own))
                assertEquals(pkg,tracker.packageName)
                assertFalse(entries.claim(pkg,entry))
            }
            assertEquals(pkg,ForegroundWindowRules.confirmedEventPackage(pkg,1000,90000,null,0,false,true))
            assertTrue(ForegroundWindowRules.isApplicationTransition(32,"com.sec.android.app.launcher",own))
            tracker.onResumed(null,90001);entries.observeExit()
            assertNull(ForegroundWindowRules.confirmedEventPackage(tracker.packageName,tracker.observedAtMs,90002,null,0,false,true))
        }
    }
    @Test fun staleNewPopupStillNeedsCorroborationAndBankingExitIsNeverIgnored() {
        val pkg="com.spotify.music"
        assertNull(ForegroundWindowRules.confirmedEventPackage(pkg,1000,90000,null,0,false))
        assertEquals(pkg,ForegroundWindowRules.confirmedEventPackage(pkg,1000,30000,pkg,15000,true))
        assertNull(ForegroundWindowRules.confirmedEventPackage(pkg,1000,40001,pkg,15000,true))
        assertTrue(ForegroundWindowRules.isApplicationTransition(32,"com.phonepe.app","com.strictrecords.app"))
    }
    @Test fun packageEventsNeedFreshnessOrMatchingFreshUsageWithoutReadingNodes() {
        val pkg = "com.google.android.youtube"
        assertEquals(pkg, ForegroundWindowRules.confirmedEventPackage(pkg, 1_000L, 2_000L, null, 0L, false))
        assertNull(ForegroundWindowRules.confirmedEventPackage(pkg, 1_000L, 10_000L, null, 0L, false))
        assertEquals(pkg, ForegroundWindowRules.confirmedEventPackage(pkg, 1_000L, 10_000L, pkg, 9_000L, true))
        assertNull(ForegroundWindowRules.confirmedEventPackage(pkg, 1_000L, 10_000L, "com.phonepe.app", 9_000L, true))
        assertNull(ForegroundWindowRules.confirmedEventPackage(null, 1_000L, 10_000L, pkg, 9_000L, true))
        assertNull(ForegroundWindowRules.confirmedEventPackage(pkg, 11_000L, 10_000L, pkg, 9_000L, true))
    }

    private val youtube = ForegroundWindowRules.Window("com.google.android.youtube", true, true)
    @Test fun inactiveBackgroundWindowsDoNotHideTheCurrentTarget() {
        assertEquals(youtube.packageName, ForegroundWindowRules.confirmedPackage(listOf(youtube,
            ForegroundWindowRules.Window("com.example.notes", true, false))))
    }
    @Test fun ownOverlayNeverConfirmsItselfOrInvalidatesTheTarget() {
        assertEquals(youtube.packageName, ForegroundWindowRules.confirmedPackage(listOf(youtube,
            ForegroundWindowRules.Window("com.strictrecords.app", false, true, true))))
        assertNull(ForegroundWindowRules.confirmedPackage(listOf(
            ForegroundWindowRules.Window("com.strictrecords.app", false, true, true))))
    }
    @Test fun touchingOurOverlayDoesNotInvalidateTheOneApplicationBelowIt() {
        val overlay = ForegroundWindowRules.Window("com.strictrecords.app", false, true, true)
        assertEquals(youtube.packageName, ForegroundWindowRules.confirmedPackage(listOf(
            overlay, youtube.copy(current = false))))
        assertNull(ForegroundWindowRules.confirmedPackage(listOf(overlay,
            youtube.copy(current = false), ForegroundWindowRules.Window("com.example.notes", true, false))))
        assertNull(ForegroundWindowRules.confirmedPackage(listOf(overlay,
            ForegroundWindowRules.Window(null, true, false))))
    }
    @Test fun aCurrentSystemOrFinancialWindowStillOverridesOurOverlay() {
        val overlay = ForegroundWindowRules.Window("com.strictrecords.app", false, true, true)
        assertNull(ForegroundWindowRules.confirmedPackage(listOf(overlay,
            youtube.copy(current = false), ForegroundWindowRules.Window("com.android.systemui", false, true))))
        assertEquals("com.phonepe.app", ForegroundWindowRules.confirmedPackage(listOf(overlay,
            youtube.copy(current = false), ForegroundWindowRules.Window("com.phonepe.app", true, true))))
        assertNull(ForegroundWindowRules.confirmedPackage(listOf(youtube.copy(current = false))))
    }
    @Test fun ambiguousOrMissingForegroundIsRejected() {
        assertNull(ForegroundWindowRules.confirmedPackage(listOf(youtube,
            ForegroundWindowRules.Window("com.phonepe.app", true, true))))
        assertNull(ForegroundWindowRules.confirmedPackage(listOf(youtube,
            ForegroundWindowRules.Window(null, false, true))))
        assertNull(ForegroundWindowRules.confirmedPackage(emptyList()))
    }
    @Test fun bankingForegroundIsReturnedToTheExclusionGate() {
        val pkg = "com.phonepe.app"
        assertEquals(pkg, ForegroundWindowRules.confirmedPackage(listOf(ForegroundWindowRules.Window(pkg,true,true))))
        assertTrue(TargetAppMonitoringPolicy.isProtected(pkg))
    }
}
