package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Test

class MeasuredPlaybackRulesTest {
    @Test fun creditsOnlyObservedProgress() {
        assertEquals(250L, MeasuredPlaybackRules.credit(1000, 1250, 250, true))
        assertEquals(0L, MeasuredPlaybackRules.credit(1000, 1250, 250, false))
        assertEquals(0L, MeasuredPlaybackRules.credit(1000, 500, 250, true))
        assertEquals(0L, MeasuredPlaybackRules.credit(1000, 10000, 250, true))
        assertEquals(0L, MeasuredPlaybackRules.credit(1000, 10000, 9000, true))
    }
    @Test fun completionDoesNotInventMissingDuration() {
        val measured = MeasuredPlaybackRules.credit(0, 250, 250, true) +
            MeasuredPlaybackRules.credit(250, 1000, 750, false)
        assertEquals(250L, measured)
    }
}
