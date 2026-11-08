package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TargetAppMonitoringPolicyTest {
    @Test fun noSavedSelectionAndClearedSelectionNeverRestoreDefaults() {
        assertTrue(TargetAppMonitoringPolicy.resolveSelectedTargetIds(null).isEmpty())
        assertTrue(TargetAppMonitoringPolicy.resolveSelectedTargetIds(emptySet()).isEmpty())
        assertEquals(setOf("spotify"), TargetAppMonitoringPolicy.resolveSelectedTargetIds(setOf("spotify", "unknown")))
    }

    @Test fun financialExclusionsDoNotExcludeUnrelatedSelectedEntertainmentApps() {
        val now = 100_000L
        val targets = TargetAppMonitoringPolicy.TargetApp.values()
        val selected = targets.map { it.id }.toSet()
        val installed = targets.flatMap { it.packageNames }.toSet()
        val financial = TargetAppMonitoringPolicy.DEFAULT_FINANCIAL_PACKAGES +
            FinancialSafePause.PROTECTED_FINANCIAL_PACKAGES
        for (pkg in installed) {
            assertTrue(pkg, TargetAppMonitoringPolicy.decide(pkg, now, now, selected,
                installed, true, true, protectedPackages = financial).shouldMonitor)
        }
        for (pkg in financial) {
            assertFalse(pkg, TargetAppMonitoringPolicy.decide(pkg, now, now, selected,
                installed, true, true, protectedPackages = financial).shouldMonitor)
        }
        assertFalse(TargetAppMonitoringPolicy.decide("com.spotify.music", now, now, selected,
            installed, true, true, protectedPackages = financial + "com.spotify.music").shouldMonitor)
    }

    private val youtube = "com.google.android.youtube"
    private val now = 100_000L

    private fun observation(
        packageName: String? = youtube,
        timestamp: Long = now,
        selected: Set<String> = setOf("youtube"),
        installed: Set<String> = setOf(youtube),
        consent: Boolean = true,
        permission: Boolean = true,
        confirmed: Boolean = true
    ) = TargetAppMonitoringPolicy.decide(packageName, timestamp, now, selected, installed, consent, permission, confirmed)

    @Test fun onlyTheSixExplicitAppFamiliesAreTargets() {
        assertEquals(setOf("instagram", "youtube", "samsung_music", "spotify", "gaana", "jiosaavn"),
            TargetAppMonitoringPolicy.TargetApp.values().map { it.id }.toSet())
        assertEquals("instagram", TargetAppMonitoringPolicy.targetForPackage("com.instagram.android")?.id)
        assertEquals("samsung_music", TargetAppMonitoringPolicy.targetForPackage("com.sec.android.app.music")?.id)
        assertEquals("spotify", TargetAppMonitoringPolicy.targetForPackage("com.spotify.music")?.id)
    }

    @Test fun newMusicTargetsUseVerifiedPackageIdentityAndTheExistingThreshold() {
        for ((id, pkg) in listOf("gaana" to "com.gaana", "jiosaavn" to "com.jio.media.jiobeats")) {
            assertEquals(id, TargetAppMonitoringPolicy.targetForPackage(pkg)?.id)
            assertTrue(TargetAppMonitoringPolicy.targetForId(id)!!.isMusic)
            assertEquals(TargetAppMonitoringPolicy.MONITOR,
                TargetAppMonitoringPolicy.decide(pkg, now, now, setOf(id), setOf(pkg), true, true).status)
            assertFalse(TargetAppMonitoringPolicy.decide(pkg, now, now, setOf(id), emptySet(), true, true).shouldMonitor)
            for (score in listOf(-10, 0, 59, 60, 79, 80)) {
                val decision = DisciplineScoringRules.evaluateRestrictionDecision(score, pkg)
                assertEquals(60, decision.requiredScore)
                assertEquals(score < 60, decision.isRestricted)
            }
            assertNull(TargetAppMonitoringPolicy.targetForPackage(pkg + ".fake"))
        }
        for (pkg in listOf("com.android.chrome", "masstamilan.dev", "com.gaana.bank")) {
            assertNull(TargetAppMonitoringPolicy.targetForPackage(pkg))
            assertTrue(TargetAppMonitoringPolicy.isProtected(pkg))
        }
    }

    @Test fun aliasesBrowsersAndLookalikePackagesNeverMatch() {
        for (candidate in listOf("YouTube", "youtube", "com.brave.browser", "com.chrome.youtube", "com.instagram.android.clone",
            "com.google.android.youtube.fake", "com.google.android.apps.youtube.music", "COM.GOOGLE.ANDROID.YOUTUBE",
            "com.bank.youtube.debug", "Instagram", "Samsung Music", "Spotify",
            "com.sec.android.app.launcher", "com.android.settings", "com.samsung.android.honeyboard", "com.example.spotify")) {
            assertNull(candidate, TargetAppMonitoringPolicy.targetForPackage(candidate))
            assertEquals(candidate, TargetAppMonitoringPolicy.IGNORE, observation(packageName = candidate).status)
        }
    }

    @Test fun officialPackageVariantsRemainExactIdentities() {
        assertEquals("instagram", TargetAppMonitoringPolicy.targetForPackage("com.instagram.lite")?.id)
        assertEquals("spotify", TargetAppMonitoringPolicy.targetForPackage("com.spotify.lite")?.id)
        assertEquals("youtube", TargetAppMonitoringPolicy.targetForPackage("com.android.youtube")?.id)
    }

    @Test fun nonTargetAppsAreIgnoredEvenWhenOtherFactsAreUnknown() {
        val result = observation("com.example.notes", 0L, emptySet(), emptySet(), false, false, false)
        assertEquals(TargetAppMonitoringPolicy.IGNORE, result.status)
        assertFalse(result.shouldMonitor)
    }

    @Test fun selectionCannotExpandThePackageAllowlist() {
        assertEquals(setOf("youtube", "spotify"), TargetAppMonitoringPolicy.sanitizeSelection(
            setOf("youtube", "spotify", "chrome", "com.example.app", "*")))
        assertEquals(TargetAppMonitoringPolicy.IGNORE, observation(selected = emptySet()).status)
        assertEquals(TargetAppMonitoringPolicy.IGNORE, observation(selected = setOf("spotify")).status)
    }

    @Test fun consentAndOsPermissionAreIndependentMandatoryGates() {
        assertEquals(TargetAppMonitoringPolicy.IGNORE, observation(consent = false).status)
        assertEquals(TargetAppMonitoringPolicy.UNKNOWN, observation(permission = false).status)
        assertTrue(observation().shouldMonitor)
    }

    @Test fun uninstalledOrInvisibleTargetsHaveNoMonitoringOrEnforcement() {
        assertEquals(TargetAppMonitoringPolicy.IGNORE, observation(installed = emptySet()).status)
        assertEquals(TargetAppMonitoringPolicy.IGNORE, observation(installed = setOf("com.spotify.music")).status)
    }

    @Test fun foregroundUnknownPausedFutureAndStaleAllFailOpen() {
        for (result in listOf(observation(packageName = null), observation(packageName = ""), observation(timestamp = 0L),
            observation(timestamp = now + 1L), observation(timestamp = now - 5_001L), observation(confirmed = false))) {
            assertEquals(TargetAppMonitoringPolicy.UNKNOWN, result.status)
            assertFalse(result.shouldMonitor)
        }
        assertTrue(observation(timestamp = now - 5_000L).shouldMonitor)
    }

    @Test fun revokingConsentStopsASamePackageForegroundObservationImmediately() {
        assertTrue(observation().shouldMonitor)
        assertFalse(observation(consent = false).shouldMonitor)
    }

    @Test fun protectedOverrideWinsOverSelectedInstalledTarget() {
        assertFalse(TargetAppMonitoringPolicy.decide(youtube, now, now, setOf("youtube"), setOf(youtube), true, true,
            protectedPackages = setOf(youtube)).shouldMonitor)
        assertTrue(TargetAppMonitoringPolicy.isProtected("com.google.android.apps.nbu.paisa.user", emptySet()))
        assertTrue(TargetAppMonitoringPolicy.isProtected("com.my.bank.actual", emptySet()))
        assertTrue(TargetAppMonitoringPolicy.isProtected(null, emptySet()))
        assertFalse(TargetAppMonitoringPolicy.isProtected(youtube, emptySet()))
        assertTrue(TargetAppMonitoringPolicy.isProtected(youtube, setOf(youtube)))
    }

    @Test fun monitoringNeverChangesExistingEarnedScoreThresholds() {
        val reward = DisciplineScoringRules.evaluateRestrictionDecision(79, youtube)
        val music = DisciplineScoringRules.evaluateRestrictionDecision(59, "com.spotify.music")
        assertEquals(80, reward.requiredScore)
        assertEquals(60, music.requiredScore)
        assertTrue(reward.isRestricted)
        assertTrue(music.isRestricted)
        assertFalse(DisciplineScoringRules.evaluateRestrictionDecision(80, youtube).isRestricted)
        assertFalse(DisciplineScoringRules.evaluateRestrictionDecision(60, "com.spotify.music").isRestricted)
    }

    @Test fun targetAppScoreTierThresholds() {
        val yt = "com.google.android.youtube"
        val ig = "com.instagram.android"
        val spotify = "com.spotify.music"
        val samsungMusic = "com.sec.android.app.music"

        // Tier 1: Score < 60 -> All 4 apps restricted
        for (score in listOf(0, 30, 59)) {
            assertTrue(DisciplineScoringRules.evaluateRestrictionDecision(score, yt).isRestricted)
            assertTrue(DisciplineScoringRules.evaluateRestrictionDecision(score, ig).isRestricted)
            assertTrue(DisciplineScoringRules.evaluateRestrictionDecision(score, spotify).isRestricted)
            assertTrue(DisciplineScoringRules.evaluateRestrictionDecision(score, samsungMusic).isRestricted)
        }

        // Tier 2: Score 60..79 -> Spotify & Samsung Music allowed; YouTube & Instagram restricted
        for (score in listOf(60, 70, 79)) {
            assertTrue(DisciplineScoringRules.evaluateRestrictionDecision(score, yt).isRestricted)
            assertTrue(DisciplineScoringRules.evaluateRestrictionDecision(score, ig).isRestricted)
            assertFalse(DisciplineScoringRules.evaluateRestrictionDecision(score, spotify).isRestricted)
            assertFalse(DisciplineScoringRules.evaluateRestrictionDecision(score, samsungMusic).isRestricted)
        }

        // Tier 3: Score >= 80 -> All 4 apps allowed
        for (score in listOf(80, 95, 100)) {
            assertFalse(DisciplineScoringRules.evaluateRestrictionDecision(score, yt).isRestricted)
            assertFalse(DisciplineScoringRules.evaluateRestrictionDecision(score, ig).isRestricted)
            assertFalse(DisciplineScoringRules.evaluateRestrictionDecision(score, spotify).isRestricted)
            assertFalse(DisciplineScoringRules.evaluateRestrictionDecision(score, samsungMusic).isRestricted)
        }
    }
}
