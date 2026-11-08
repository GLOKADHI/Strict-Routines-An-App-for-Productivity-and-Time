package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class YouTubeRewardLeaseTest {
    @Test fun fixedDeadlineSurvivesSwitchingAndNeverUsesWallClock() {
        val lease=YouTubeRewardLease()
        assertTrue(lease.accept("account",1000,1201000,1000,5000,5001))
        assertEquals(1199998L,lease.remaining("account",5002))
        assertEquals(0L,lease.remaining("other-account",5003))
    }
    @Test fun restartOfflineAndUncertainReceiptsDoNotInventTwentyMinutes() {
        assertEquals(0L,YouTubeRewardLease().remaining("account",10))
        val lease=YouTubeRewardLease()
        assertFalse(lease.accept("a",1000,1201001,1000,0,1))
        assertTrue(lease.accept("a",1000,1201000,1200000,0,1))
        assertEquals(0L,lease.remaining("a",1000))
        assertTrue(lease.accept("a",1000,1201000,1000,100,101))
        assertEquals(0L,lease.remaining("a",30102))
    }
    @Test fun refreshingTheSameActivationCannotExtendItsElapsedDeadline() {
        val lease=YouTubeRewardLease()
        assertTrue(lease.accept("a",1000,1201000,1000,0,1))
        // Even a clock correction in a subsequent trusted response cannot move
        // this process's original end later.
        assertTrue(lease.accept("a",1000,1201000,1000,5000,5001))
        assertEquals(1194998L,lease.remaining("a",5002))
    }
    @Test fun youtubeOnlyAndIndependentRestrictionsRemain() {
        for(pkg in listOf("com.google.android.youtube","com.instagram.android","com.spotify.music","com.sec.android.app.music")) {
            val r=DisciplineScoringRules.evaluateRestrictionDecision(0,pkg,isYouTubeRewardActive=true)
            assertFalse(r.isRestricted)
            assertEquals(0,r.scoreAtDetection)
        }
        assertTrue(DisciplineScoringRules.evaluateRestrictionDecision(0,"com.google.android.youtube",isYouTubeRewardActive=false).isRestricted)
        assertTrue(DisciplineScoringRules.evaluateRestrictionDecision(0,"com.google.android.youtube",isYouTubeRewardActive=true,appLocksMap=mapOf("youtube" to true)).isRestricted)
        assertFalse(DisciplineScoringRules.evaluateRestrictionDecision(0,"com.google.android.apps.nbu.paisa.user",isYouTubeRewardActive=true).isRestricted)
    }
    @Test fun accessibilityDisabledHasPriorityOverReward() {
        val r=FloatingBotStateSelector.evaluate("com.google.android.youtube",0,null,confirmedAccessibilityWarning=true,isYouTubeRewardActive=true)
        assertEquals(FloatingBotStateSelector.MessageState.ACCESSIBILITY_DISABLED,r.state)
        assertFalse(FloatingBotStateSelector.evaluate("com.google.android.apps.nbu.paisa.user",0,null,isYouTubeRewardActive=true).shouldShow)
    }
}
