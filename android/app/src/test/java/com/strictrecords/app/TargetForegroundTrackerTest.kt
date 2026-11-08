package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TargetForegroundTrackerTest {
    private val youtube = "com.google.android.youtube"
    private val spotify = "com.spotify.music"
    private val bank = "com.example.bank"

    private fun tracker() = TargetForegroundTracker(setOf(youtube, spotify))

    private fun observation(tracker: TargetForegroundTracker, now: Long) = TargetAppMonitoringPolicy.decide(
        tracker.packageName, tracker.observedAtMs, now,
        selectedIds = setOf("youtube", "spotify"),
        installedPackages = setOf(youtube, spotify),
        usageConsent = true, usagePermissionGranted = true, confirmedForeground = true
    )

    @Test fun rapidTargetBankTargetSwitchNeverRetainsTheBankIdentity() {
        val tracker = tracker()
        tracker.onResumed(youtube, 100L)
        assertEquals(youtube, tracker.packageName)
        tracker.onResumed(bank, 101L)
        assertNull(tracker.packageName)
        assertEquals(0L, tracker.observedAtMs)
        tracker.onResumed(spotify, 102L)
        assertEquals(spotify, tracker.packageName)
        assertEquals(102L, tracker.observedAtMs)
    }

    @Test fun currentTargetPauseClearsImmediatelyWithoutAGraceWindow() {
        val tracker = tracker()
        tracker.onResumed(youtube, 1_000L)
        tracker.onPaused(youtube, 1_001L)
        assertNull(tracker.packageName)
        assertEquals(0L, tracker.observedAtMs)
        assertFalse(observation(tracker, 1_001L).shouldMonitor)
    }

    @Test fun pauseOfOldTargetDoesNotReplaceANewerTargetOrRenewFreshness() {
        val tracker = tracker()
        tracker.onResumed(youtube, 100L)
        tracker.onResumed(spotify, 200L)
        tracker.onPaused(youtube, 201L)
        assertEquals(spotify, tracker.packageName)
        assertEquals(200L, tracker.observedAtMs)
        tracker.onPaused(bank, 202L)
        assertEquals(spotify, tracker.packageName)
        assertEquals(200L, tracker.observedAtMs)
    }

    @Test fun pauseAfterSwitchToAnUnrelatedAppCannotRestoreTheOldTarget() {
        val tracker = tracker()
        tracker.onResumed(youtube, 100L)
        tracker.onResumed(bank, 101L)
        tracker.onPaused(youtube, 102L)
        assertNull(tracker.packageName)
        assertEquals(0L, tracker.observedAtMs)
    }

    @Test fun constructorCannotExpandTargetsToBanksBrowsersOrLookalikes() {
        val tracker = TargetForegroundTracker(setOf(youtube, bank, "com.android.chrome", "$youtube.fake", "YouTube"))
        for ((index, pkg) in listOf(bank, "com.android.chrome", "$youtube.fake", "YouTube").withIndex()) {
            tracker.onResumed(youtube, 100L + index * 2)
            assertEquals(youtube, tracker.packageName)
            tracker.onResumed(pkg, 101L + index * 2)
            assertNull(pkg, tracker.packageName)
        }
        tracker.onResumed(youtube, 200L)
        assertEquals(youtube, tracker.packageName)
        tracker.onResumed(spotify, 201L)
        assertNull(tracker.packageName) // A real target is still excluded if not selected.
    }

    @Test fun emptySelectionMissingPackageAndUnknownPauseAreFailOpen() {
        val empty = TargetForegroundTracker(emptySet())
        empty.onResumed(youtube, 100L)
        assertNull(empty.packageName)
        val tracker = tracker()
        for ((index, pkg) in listOf(null, "", "   ").withIndex()) {
            tracker.onResumed(youtube, 200L + index * 2)
            assertEquals(youtube, tracker.packageName)
            tracker.onResumed(pkg, 201L + index * 2)
            assertNull(tracker.packageName)
        }
        tracker.onResumed(youtube, 206L)
        tracker.onPaused(null, 207L)
        assertNull(tracker.packageName)
    }

    @Test fun nonpositiveAndOutOfOrderEventsClearWithoutResurrectingEarlierTargets() {
        val tracker = tracker()
        tracker.onResumed(youtube, 100L)
        tracker.onResumed(bank, 200L)
        tracker.onResumed(youtube, 150L)
        assertNull(tracker.packageName)
        tracker.onResumed(youtube, 160L)
        assertNull(tracker.packageName)
        tracker.onResumed(spotify, 201L)
        assertEquals(spotify, tracker.packageName)
        tracker.onPaused(youtube, 180L)
        assertNull(tracker.packageName)
        tracker.onResumed(youtube, 202L)
        tracker.onPaused(youtube, 0L)
        assertNull(tracker.packageName)
        tracker.onResumed(youtube, -1L)
        assertEquals(0L, tracker.observedAtMs)
    }

    @Test fun equalTimestampPauseStillClearsAndLaterResumeCanStartANewObservation() {
        val tracker = tracker()
        tracker.onResumed(youtube, 100L)
        tracker.onPaused(youtube, 100L)
        assertNull(tracker.packageName)
        tracker.onResumed(spotify, 100L)
        assertEquals(spotify, tracker.packageName)
    }

    @Test fun screenOffAndKeyguardClearTheCandidateWithoutKeepingStaleTime() {
        val tracker = tracker()
        tracker.onResumed(youtube, 100L)
        tracker.clear(101L)
        assertNull(tracker.packageName)
        assertEquals(0L, tracker.observedAtMs)
        tracker.onResumed(spotify, 102L)
        tracker.clear(103L)
        assertFalse(observation(tracker, 103L).shouldMonitor)
    }

    @Test fun sharedPolicyRejectsOldAndFutureTimesWithoutTheTrackerInventingFreshness() {
        val now = 100_000L
        val old = tracker()
        old.onResumed(youtube, now - TargetAppMonitoringPolicy.MAX_FOREGROUND_AGE_MS - 1L)
        assertEquals(now - TargetAppMonitoringPolicy.MAX_FOREGROUND_AGE_MS - 1L, old.observedAtMs)
        assertEquals(TargetAppMonitoringPolicy.UNKNOWN, observation(old, now).status)
        val future = tracker()
        future.onResumed(youtube, now + 1L)
        assertEquals(TargetAppMonitoringPolicy.UNKNOWN, observation(future, now).status)
        val fresh = tracker()
        fresh.onResumed(youtube, now)
        assertTrue(observation(fresh, now).shouldMonitor)
    }

    @Test fun rapidSwitchingBetweenTrackedAppsAndBankingUpiAppsFailsOpenAndNeverRetainsPaymentIdentity() {
        val tracker = TargetForegroundTracker(setOf(
            youtube, spotify, "com.instagram.android", "com.sec.android.app.music"
        ))
        val paymentApps = listOf(
            "com.google.android.apps.nbu.paisa.user", // Google Pay
            "com.phonepe.app",                        // PhonePe
            "net.one97.paytm",                        // Paytm
            "in.org.npci.upiapp",                     // BHIM
            "com.sbi.upi",                            // SBI YONO / UPI
            "com.snapwork.hdfc",                      // HDFC Bank
            "com.icicibank.mobile",                   // ICICI iMobile
            "com.axis.mobile"                         // Axis Mobile
        )

        var time = 1_000L
        for (payApp in paymentApps) {
            // User opens YouTube
            time += 100L
            tracker.onResumed(youtube, time)
            assertEquals(youtube, tracker.packageName)
            assertEquals(time, tracker.observedAtMs)
            val ytObs = TargetAppMonitoringPolicy.decide(
                tracker.packageName, tracker.observedAtMs, time,
                selectedIds = setOf("youtube", "spotify", "instagram", "samsung_music"),
                installedPackages = setOf(youtube, spotify, "com.instagram.android", "com.sec.android.app.music"),
                usageConsent = true, usagePermissionGranted = true, confirmedForeground = true
            )
            assertTrue(ytObs.shouldMonitor)

            // User switches to payment/UPI/banking app (e.g., checkout/payment flow)
            time += 50L
            tracker.onResumed(payApp, time)
            assertNull("Payment app $payApp must clear candidate", tracker.packageName)
            assertEquals(0L, tracker.observedAtMs)
            val payObs = TargetAppMonitoringPolicy.decide(
                tracker.packageName, tracker.observedAtMs, time,
                selectedIds = setOf("youtube", "spotify", "instagram", "samsung_music"),
                installedPackages = setOf(youtube, spotify, "com.instagram.android", "com.sec.android.app.music"),
                usageConsent = true, usagePermissionGranted = true, confirmedForeground = true
            )
            assertFalse("Payment app $payApp must fail open and never be monitored", payObs.shouldMonitor)
            assertEquals(TargetAppMonitoringPolicy.UNKNOWN, payObs.status)

            // Payment app pauses (e.g. OTP prompt or biometric dialog)
            time += 20L
            tracker.onPaused(payApp, time)
            assertNull(tracker.packageName)

            // User switches to Spotify
            time += 100L
            tracker.onResumed(spotify, time)
            assertEquals(spotify, tracker.packageName)
            assertEquals(time, tracker.observedAtMs)
        }
    }
}

