package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class UsageQueryRangeTest {
    @Test fun exclusiveWatermarkPreventsBoundaryReplayAfterRestart() {
        val first = UsageQueryRange.next(1000, 2000, 900000)!!
        val restored = UsageQueryRange.next(first.endExclusiveMs, 3000, 900000)!!
        assertTrue(first.contains(1999))
        assertFalse(first.contains(2000))
        assertTrue(restored.contains(2000))
        assertFalse(restored.contains(1999))
    }
    @Test fun repeatedRangeOrClockReversalCannotReplayProcessedEvents() {
        assertNull(UsageQueryRange.next(2000, 2000, 900000))
        assertNull(UsageQueryRange.next(2000, 1999, 900000))
    }
    @Test fun historicalQueriesAreBoundedAndDoNotInventMissingHistory() {
        val range = UsageQueryRange.next(1000, 1000000, 900000)!!
        assertEquals(100000, range.startMs)
        assertFalse(range.contains(99999))
        assertEquals(900000, range.endExclusiveMs - range.startMs)
    }
}
