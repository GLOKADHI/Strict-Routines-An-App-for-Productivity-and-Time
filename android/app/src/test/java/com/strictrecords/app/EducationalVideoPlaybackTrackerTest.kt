package com.strictrecords.app

import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class EducationalVideoPlaybackTrackerTest {

    @Before
    fun setUp() {
        EducationalVideoPlaybackTracker.resetForTesting()
    }

    @After
    fun tearDown() {
        EducationalVideoPlaybackTracker.resetForTesting()
    }

    @Test
    fun testInitialState() {
        EducationalVideoPlaybackTracker.initForTesting("dQw4w9WgXcQ", 1205)
        assertEquals("dQw4w9WgXcQ", EducationalVideoPlaybackTracker.activeVideoId)
        assertEquals(1205, EducationalVideoPlaybackTracker.allowedDurationSeconds)
        assertEquals(1205, EducationalVideoPlaybackTracker.remainingSeconds)
        assertEquals(0f, EducationalVideoPlaybackTracker.consumedSeconds, 0.001f)
        assertFalse(EducationalVideoPlaybackTracker.isCompleted)
    }

    @Test
    fun testContinuousPlaybackAccumulation() {
        EducationalVideoPlaybackTracker.initForTesting("dQw4w9WgXcQ", 60)
        EducationalVideoPlaybackTracker.recordIntervalForTesting(0f, 10f)
        assertEquals(10f, EducationalVideoPlaybackTracker.consumedSeconds, 0.001f)
        assertEquals(50, EducationalVideoPlaybackTracker.remainingSeconds)
        assertFalse(EducationalVideoPlaybackTracker.isCompleted)

        EducationalVideoPlaybackTracker.recordIntervalForTesting(10f, 25f)
        assertEquals(25f, EducationalVideoPlaybackTracker.consumedSeconds, 0.001f)
        assertEquals(35, EducationalVideoPlaybackTracker.remainingSeconds)
    }

    @Test
    fun testForwardSeekDoesNotCreditSkippedSegment() {
        EducationalVideoPlaybackTracker.initForTesting("dQw4w9WgXcQ", 100)
        // Watch 0 to 10
        EducationalVideoPlaybackTracker.recordIntervalForTesting(0f, 10f)
        // User jumps forward to 50 and watches 50 to 60
        EducationalVideoPlaybackTracker.recordIntervalForTesting(50f, 60f)

        // Watched time is 10 + 10 = 20 seconds, NOT 60 seconds
        assertEquals(20f, EducationalVideoPlaybackTracker.consumedSeconds, 0.001f)
        assertEquals(80, EducationalVideoPlaybackTracker.remainingSeconds)
    }

    @Test
    fun testBackwardSeekDoesNotDoubleCountReplayedSegment() {
        EducationalVideoPlaybackTracker.initForTesting("dQw4w9WgXcQ", 100)
        // Watch 0 to 30
        EducationalVideoPlaybackTracker.recordIntervalForTesting(0f, 30f)
        assertEquals(30f, EducationalVideoPlaybackTracker.consumedSeconds, 0.001f)

        // Replay 10 to 25
        EducationalVideoPlaybackTracker.recordIntervalForTesting(10f, 25f)
        // Interval is fully contained in [0, 30], so total consumed remains 30
        assertEquals(30f, EducationalVideoPlaybackTracker.consumedSeconds, 0.001f)
        assertEquals(70, EducationalVideoPlaybackTracker.remainingSeconds)
    }

    @Test
    fun testCompletionMarksCompletedAndStopsAtZero() {
        EducationalVideoPlaybackTracker.initForTesting("dQw4w9WgXcQ", 30)
        EducationalVideoPlaybackTracker.recordIntervalForTesting(0f, 30f)

        assertTrue(EducationalVideoPlaybackTracker.isCompleted)
        assertEquals(0, EducationalVideoPlaybackTracker.remainingSeconds)
        assertEquals(30f, EducationalVideoPlaybackTracker.consumedSeconds, 0.001f)
    }
}
