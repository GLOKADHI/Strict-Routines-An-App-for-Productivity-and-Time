package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BackgroundMusicRestrictionTest {

    @Test
    fun testMonitoredMusicPackages() {
        // Spotify packages
        assertTrue(BackgroundMusicRestrictionManager.isMonitoredMusicPackage("com.spotify.music"))
        assertTrue(BackgroundMusicRestrictionManager.isMonitoredMusicPackage("com.spotify.lite"))

        // Samsung Music
        assertTrue(BackgroundMusicRestrictionManager.isMonitoredMusicPackage("com.sec.android.app.music"))

        // Other supported music apps
        assertTrue(BackgroundMusicRestrictionManager.isMonitoredMusicPackage("com.gaana"))
        assertTrue(BackgroundMusicRestrictionManager.isMonitoredMusicPackage("com.jio.media.jiobeats"))

        // StrictRecords must never be treated as an external music app
        assertFalse(BackgroundMusicRestrictionManager.isMonitoredMusicPackage("com.strictrecords.app"))

        // Unrelated apps
        assertFalse(BackgroundMusicRestrictionManager.isMonitoredMusicPackage("com.google.android.youtube"))
        assertFalse(BackgroundMusicRestrictionManager.isMonitoredMusicPackage("com.android.chrome"))
        assertFalse(BackgroundMusicRestrictionManager.isMonitoredMusicPackage(""))
    }

    @Test
    fun testMusicAccessDecisionThresholds() {
        // Spotify: requires 60 points
        val spotifyDenied = DisciplineScoringRules.evaluateRestrictionDecision(59, "com.spotify.music")
        assertTrue("Spotify should be restricted at score 59", spotifyDenied.isRestricted)
        assertEquals(60, spotifyDenied.requiredScore)
        assertEquals(1, spotifyDenied.remainingPoints)

        val spotifyAllowed = DisciplineScoringRules.evaluateRestrictionDecision(60, "com.spotify.music")
        assertFalse("Spotify should be permitted at score 60", spotifyAllowed.isRestricted)
        assertEquals("ALLOWED", spotifyAllowed.action)

        val spotifyHighScore = DisciplineScoringRules.evaluateRestrictionDecision(85, "com.spotify.music")
        assertFalse("Spotify should be permitted at score 85", spotifyHighScore.isRestricted)

        // Samsung Music: requires 60 points
        val samsungDenied = DisciplineScoringRules.evaluateRestrictionDecision(59, "com.sec.android.app.music")
        assertTrue("Samsung Music should be restricted at score 59", samsungDenied.isRestricted)
        assertEquals(60, samsungDenied.requiredScore)

        val samsungAllowed = DisciplineScoringRules.evaluateRestrictionDecision(60, "com.sec.android.app.music")
        assertFalse("Samsung Music should be permitted at score 60", samsungAllowed.isRestricted)

        // Gaana & JioSaavn: require 60 points
        val gaanaDenied = DisciplineScoringRules.evaluateRestrictionDecision(50, "com.gaana")
        assertTrue(gaanaDenied.isRestricted)
        assertEquals(60, gaanaDenied.requiredScore)

        val jioSaavnAllowed = DisciplineScoringRules.evaluateRestrictionDecision(60, "com.jio.media.jiobeats")
        assertFalse(jioSaavnAllowed.isRestricted)

        // Non-music app (YouTube): requires 80 points
        val ytAt60 = DisciplineScoringRules.evaluateRestrictionDecision(60, "com.google.android.youtube")
        assertTrue("YouTube requires 80 points, so restricted at score 60", ytAt60.isRestricted)
        assertEquals(80, ytAt60.requiredScore)
    }

    @Test
    fun testWakeUpPlaybackIsolation() {
        // WakeUpPlaybackService package must not be classified as a music package
        assertFalse(BackgroundMusicRestrictionManager.isMonitoredMusicPackage("com.strictrecords.app"))
        
        // WakeUp verification active state protects playback audio
        assertFalse(WakeUpPlaybackService.isPlaybackActive())
    }

    @Test
    fun testMonitoredMusicPackagesListContainsExpectedPackages() {
        val packages = BackgroundMusicRestrictionManager.MONITORED_MUSIC_PACKAGES
        assertTrue(packages.contains("com.spotify.music"))
        assertTrue(packages.contains("com.spotify.lite"))
        assertTrue(packages.contains("com.sec.android.app.music"))
        assertTrue(packages.contains("com.gaana"))
        assertTrue(packages.contains("com.jio.media.jiobeats"))
        assertFalse(packages.contains("com.strictrecords.app"))
    }
}
