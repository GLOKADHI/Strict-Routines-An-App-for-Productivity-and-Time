package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class RestrictedPlaybackEnforcerTest {
    private class Player(override val packageName: String, override val identity: Any = packageName) : RestrictedPlaybackEnforcer.Player {
        override var state=3
        override var canStop=true
        var pauses=0;var stops=0;var keys=0
        override fun pause() { pauses++ }
        override fun stop() { stops++ }
        override fun pauseButton() { keys++ }
    }
    @Test fun musicAndVideoReceivePauseAndOnlyConfirmedPauseReportsSuccess() {
        for(pkg in listOf("com.spotify.music","com.sec.android.app.music","com.google.android.youtube")) {
            val engine=RestrictedPlaybackEnforcer();val player=Player(pkg)
            assertEquals("PAUSE_REQUESTED",engine.enforce(player,1000,true).status)
            assertEquals(1,player.pauses)
            player.state=2
            assertEquals("PAUSED",engine.enforce(player,1100,true).status)
            assertEquals(0,player.stops)
            assertEquals("IDLE",engine.enforce(player,1200,true).status)
        }
    }
    @Test fun refusalHasOnlyTwoCommandAttemptsAndNoFalseSuccess() {
        val engine=RestrictedPlaybackEnforcer();val player=Player("com.spotify.music")
        engine.enforce(player,1000,true)
        repeat(100) { engine.enforce(player,1100,true) }
        assertEquals(1,player.pauses)
        assertEquals("STOP_REQUESTED",engine.enforce(player,1350,true).status)
        assertEquals(1,player.stops);assertEquals(1,player.keys)
        assertEquals("STILL_PLAYING",engine.enforce(player,2100,true).status)
        repeat(100) { engine.enforce(player,3000+it.toLong(),true) }
        assertEquals(1,player.stops);assertEquals(1,player.pauses)
    }
    @Test fun quickResumeIsRetriedInsteadOfDroppedByThrottle() {
        val engine=RestrictedPlaybackEnforcer();val player=Player("com.sec.android.app.music")
        engine.enforce(player,1000,true);player.state=2;engine.enforce(player,1010,true)
        player.state=3
        assertEquals("PAUSE_REQUESTED",engine.enforce(player,1020,true).status)
        assertEquals(2,player.pauses)
    }
    @Test fun eligibleDeselectedSafePauseAndOwnSessionsReceiveNoCommands() {
        val engine=RestrictedPlaybackEnforcer()
        for(pkg in listOf("com.spotify.music","com.sec.android.app.music","com.google.android.youtube","com.strictrecords.app","com.phonepe.app")) {
            val player=Player(pkg)
            assertEquals("NOT_RESTRICTED",engine.enforce(player,1000,false).status)
            assertEquals(0,player.pauses+player.stops+player.keys)
        }
    }
    @Test fun sessionTokensSeparateTwoPlayersInTheSamePackage() {
        val engine=RestrictedPlaybackEnforcer();val first=Player("com.spotify.music","first");val second=Player("com.spotify.music","second")
        engine.enforce(first,1000,true);engine.enforce(second,1000,true)
        assertEquals(1,first.pauses);assertEquals(1,second.pauses)
        engine.forget(first.identity);engine.enforce(first,1100,true)
        assertEquals(2,first.pauses);assertEquals(1,second.pauses)
    }
    @Test fun pauseOnlyPlayersUseBoundedSessionKeyFallback() {
        val engine=RestrictedPlaybackEnforcer();val player=Player("com.google.android.youtube");player.canStop=false
        engine.enforce(player,1000,true);engine.enforce(player,1350,true)
        assertEquals(0,player.stops);assertEquals(2,player.pauses);assertEquals(1,player.keys)
    }
    @Test fun bufferingAndConnectingArePausedButAlreadyStoppedIsIdle() {
        for(state in listOf(3,4,5,6,8,9,10,11)) {
            val player=Player("com.google.android.youtube");player.state=state
            assertEquals("PAUSE_REQUESTED",RestrictedPlaybackEnforcer().enforce(player,1000,true).status)
        }
        val player=Player("com.spotify.music");player.state=1
        assertEquals("IDLE",RestrictedPlaybackEnforcer().enforce(player,1000,true).status)
        assertEquals(0,player.pauses)
    }
    @Test fun selectionGateIncludesVideoWithoutChangingMusicClassification() {
        assertTrue(BackgroundMusicRestrictionManager.isMonitoredPlaybackPackage("com.google.android.youtube"))
        assertFalse(BackgroundMusicRestrictionManager.isMonitoredMusicPackage("com.google.android.youtube"))
        assertFalse(BackgroundMusicRestrictionManager.isMonitoredPlaybackPackage("com.strictrecords.app"))
        assertFalse(BackgroundMusicRestrictionManager.isMonitoredPlaybackPackage("com.phonepe.app"))
    }
    @Test fun unsupportedPauseTriesOnlyTheSameSessionsFallback() {
        var keys=0;var stops=0
        val player=object:RestrictedPlaybackEnforcer.Player {
            override val identity="fallback"
            override val packageName="com.google.android.youtube"
            override val state=3
            override val canStop=true
            override fun pause() { throw IllegalStateException("Not supported") }
            override fun pauseButton() { keys++ }
            override fun stop() { stops++ }
        }
        assertEquals("STOP_REQUESTED",RestrictedPlaybackEnforcer().enforce(player,1000,true).status)
        assertEquals(1,keys);assertEquals(1,stops)
    }
    @Test fun missingStateCannotBeReportedAsPaused() {
        val engine=RestrictedPlaybackEnforcer();val player=Player("com.spotify.music")
        engine.enforce(player,1000,true);player.state=0
        assertEquals("STATE_UNAVAILABLE",engine.enforce(player,1400,true).status)
    }
}
