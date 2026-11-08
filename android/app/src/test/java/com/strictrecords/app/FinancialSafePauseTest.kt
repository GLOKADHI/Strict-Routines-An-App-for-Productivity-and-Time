package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class FinancialSafePauseTest {

    @Test fun fullFiveMinutesStartAfterALongFinancialSessionEnds() {
        val start = 1_000_000L
        FinancialSafePause.onProtectedPackageObserved(null, "com.phonepe.app", start)
        val exit = start + 20 * 60_000L
        assertTrue(FinancialSafePause.isPauseActive(null, exit))
        FinancialSafePause.onNonProtectedPackageObserved(null, "com.example.notes", exit)
        assertEquals(exit + 300_000L, FinancialSafePause.getDiagnosticsState(null, exit).resumeDeadlineMs)
        assertTrue(FinancialSafePause.isPauseActive(null, exit + 299_999L))
        assertFalse(FinancialSafePause.isPauseActive(null, exit + 300_000L))
    }

    @Test fun missingOrProtectedExitIdentityCannotEndTheFinancialSession() {
        val start = 1_000_000L
        FinancialSafePause.onProtectedPackageObserved(null, "com.phonepe.app", start)
        for (pkg in listOf(null, "", "com.phonepe.app")) {
            FinancialSafePause.onNonProtectedPackageObserved(null, pkg, start + 1)
            assertTrue(FinancialSafePause.getDiagnosticsState(null, start + 1).isProtectedInForeground)
        }
    }

    @Test fun staleUsageHistoryCannotResurrectAnExitedFinancialSession() {
        val start = 1_000_000L
        FinancialSafePause.onProtectedPackageObserved(null, "com.phonepe.app", start)
        FinancialSafePause.onNonProtectedPackageObserved(null, "com.example.notes", start + 5000)
        FinancialSafePause.onProtectedPackageObserved(null, "com.phonepe.app", start + 1000)
        assertFalse(FinancialSafePause.getDiagnosticsState(null, start + 6000).isProtectedInForeground)
        assertEquals(start + 305_000, FinancialSafePause.getDiagnosticsState(null, start + 6000).resumeDeadlineMs)
    }

    @Test fun processRestartRestoresTheOriginalDeadlineAndForegroundState() {
        val start = 1_000_000L
        FinancialSafePause.onProtectedPackageObserved(null, "com.phonepe.app", start)
        // Reload the persisted test store, as a fresh process would.
        FinancialSafePause.setStoreForTesting(store)
        val restored = FinancialSafePause.getDiagnosticsState(null, start + 120_000L)
        assertEquals(start + 300_000L, restored.resumeDeadlineMs)
        assertEquals(180_000L, restored.remainingMs)
        assertTrue(restored.isProtectedInForeground)
        assertTrue(restored.isActive)
        FinancialSafePause.onNonProtectedPackageObserved(null, "com.sec.android.app.launcher", start + 301_000L)
        FinancialSafePause.setStoreForTesting(store)
        assertTrue(FinancialSafePause.isPauseActive(null, start + 301_000L))
        assertFalse(FinancialSafePause.isPauseActive(null, start + 601_000L))
    }

    @Test fun repeatedEventsDoNotRestartTheSameBankingVisit() {
        val start = 1_000_000L
        FinancialSafePause.onProtectedPackageObserved(null, "com.phonepe.app", start)
        repeat(100) { FinancialSafePause.onProtectedPackageObserved(null, "com.phonepe.app", start + it * 5000L) }
        assertEquals(start + 300_000L, FinancialSafePause.getDiagnosticsState(null, start + 500_000L).resumeDeadlineMs)
    }

    @Test fun elapsedTimerWaitsForTheBankExitWithoutShowingAFakeFiveMinuteCountdown() {
        val start = 1_000_000L
        FinancialSafePause.onProtectedPackageObserved(null, "com.phonepe.app", start)
        assertTrue(FinancialSafePause.isPauseActive(null, start + 300_000L))
        assertEquals(0L, FinancialSafePause.remainingPauseMs(null, start + 300_000L))
        FinancialSafePause.onNonProtectedPackageObserved(null, "com.sec.android.app.launcher", start + 300_001L)
        assertTrue(FinancialSafePause.isPauseActive(null, start + 300_001L))
        assertFalse(FinancialSafePause.isPauseActive(null, start + 600_001L))
    }

    @Test fun aNewVisitToTheSameBankGetsItsOwnFiveMinutes() {
        val start = 1_000_000L
        FinancialSafePause.onProtectedPackageObserved(null, "com.phonepe.app", start)
        FinancialSafePause.onNonProtectedPackageObserved(null, "com.sec.android.app.launcher", start + 10_000L)
        FinancialSafePause.onProtectedPackageObserved(null, "com.phonepe.app", start + 20_000L)
        assertEquals(start + 320_000L, FinancialSafePause.getDiagnosticsState(null, start + 20_000L).resumeDeadlineMs)
    }

    private lateinit var store: FinancialSafePause.InMemorySafePauseStore

    @Before
    fun setUp() {
        store = FinancialSafePause.InMemorySafePauseStore()
        FinancialSafePause.setStoreForTesting(store)
        FinancialSafePause.resetForTesting()
    }

    @Test
    fun testProtectedPackageRecognition() {
        val protectedList = listOf(
            "com.phonepe.app",
            "com.google.android.apps.nbu.paisa.user",
            "net.one97.paytm",
            "in.org.npci.upiapp",
            "com.dreamplug.androidapp",
            "com.navi.mutualfunds",
            "com.navi.navi",
            "com.sbi.lotusintouch",
            "com.sbi.upi",
            "com.csam.icici.bank.imobile",
            "com.snapwork.hdfc",
            "com.hdfcbank.payzapp",
            "com.axis.mobile",
            "com.msf.kbank.mobile",
            "com.x8bit.bitwarden",
            "com.onepassword.android",
            "com.android.settings"
        )
        for (pkg in protectedList) {
            assertTrue("Expected '$pkg' to be recognized as protected", FinancialSafePause.isProtectedFinancialPackage(null, pkg))
        }

        val nonProtectedList = listOf(
            "com.google.android.youtube",
            "com.instagram.android",
            "com.spotify.music",
            "com.sec.android.app.music",
            "com.sec.android.app.launcher",
            "com.android.chrome"
        )
        for (pkg in nonProtectedList) {
            assertFalse("Expected '$pkg' NOT to be protected", FinancialSafePause.isProtectedFinancialPackage(null, pkg))
        }
    }

    @Test
    fun testPauseActivationAnd5MinuteSuspensionWindow() {
        val t0 = 1_000_000L
        assertFalse(FinancialSafePause.isPauseActive(null, t0))

        // PhonePe opens at t0
        FinancialSafePause.onProtectedPackageObserved(null, "com.phonepe.app", t0)
        assertTrue(FinancialSafePause.isPauseActive(null, t0))
        assertEquals(300_000L, FinancialSafePause.remainingPauseMs(null, t0))

        // User exits PhonePe to home screen 20 seconds later at t0 + 20_000L
        val t1 = t0 + 20_000L
        FinancialSafePause.onNonProtectedPackageObserved(null, "com.sec.android.app.launcher", t1)

        // Safe pause MUST still be active! (5-minute rule)
        assertTrue(FinancialSafePause.isPauseActive(null, t1))
        assertEquals(300_000L, FinancialSafePause.remainingPauseMs(null, t1))

        // At t1 + 299_000L (4 min 59s), still active
        val tAlmostEnd = t1 + 299_000L
        assertTrue(FinancialSafePause.isPauseActive(null, tAlmostEnd))
        assertEquals(1_000L, FinancialSafePause.remainingPauseMs(null, tAlmostEnd))

        // At t0 + 300_000L (exactly 5 minutes), pause expires
        val tEnd = t1 + 300_000L
        assertFalse(FinancialSafePause.isPauseActive(null, tEnd))
        assertEquals(0L, FinancialSafePause.remainingPauseMs(null, tEnd))

        // At t1 + 305_000L, still expired
        assertFalse(FinancialSafePause.isPauseActive(null, t1 + 305_000L))
    }

    @Test
    fun testProtectedAppInForegroundExceeding5Minutes() {
        val t0 = 1_000_000L
        // SBI YONO opens at t0
        FinancialSafePause.onProtectedPackageObserved(null, "com.sbi.lotusintouch", t0)

        // 6 minutes pass and user is STILL in SBI YONO (no non-protected observation)
        val t6Min = t0 + 360_000L
        // Enforcement MUST remain suspended because protected app is still in foreground
        assertTrue(FinancialSafePause.isPauseActive(null, t6Min))

        // Now user exits SBI YONO at t6Min
        FinancialSafePause.onNonProtectedPackageObserved(null, "com.sec.android.app.launcher", t6Min)

        // Since 5 minutes from t0 already passed, enforcement can now resume
        assertTrue(FinancialSafePause.isPauseActive(null, t6Min))
        assertFalse(FinancialSafePause.isPauseActive(null, t6Min + 300_000L))
    }

    @Test
    fun testSuccessiveProtectedAppsExtendDeadline() {
        val t0 = 1_000_000L
        // PhonePe opens at t0 (deadline t0 + 300_000L)
        FinancialSafePause.onProtectedPackageObserved(null, "com.phonepe.app", t0)

        // 2 minutes later, user switches to GPay
        val t2Min = t0 + 120_000L
        FinancialSafePause.onProtectedPackageObserved(null, "com.google.android.apps.nbu.paisa.user", t2Min)

        // Deadline should now be t2Min + 300_000L = t0 + 420_000L
        val tOldDeadline = t0 + 300_000L
        FinancialSafePause.onNonProtectedPackageObserved(null, "com.sec.android.app.launcher", t2Min + 10_000L)

        // At old deadline (t0 + 300_000L), pause must still be active!
        assertTrue(FinancialSafePause.isPauseActive(null, tOldDeadline))

        // At new deadline (t0 + 420_000L), pause expires
        val tNewDeadline = t2Min + 310_000L
        assertFalse(FinancialSafePause.isPauseActive(null, tNewDeadline))
    }

    @Test
    fun testDiagnosticsState() {
        val t0 = 1_000_000L
        FinancialSafePause.onProtectedPackageObserved(null, "com.x8bit.bitwarden", t0)

        val diag = FinancialSafePause.getDiagnosticsState(null, t0)
        assertTrue(diag.isActive)
        assertTrue(diag.isProtectedInForeground)
        assertEquals("com.x8bit.bitwarden", diag.lastProtectedPackage)
        assertEquals(300_000L, diag.remainingMs)
        assertEquals(t0 + 300_000L, diag.resumeDeadlineMs)
        assertEquals(t0, diag.pauseStartedAtMs)
    }
}
