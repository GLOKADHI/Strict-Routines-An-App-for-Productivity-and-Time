package com.strictrecords.app
import org.junit.Assert.*
import org.junit.Test

class WindowStateRulesTest {
    @Test fun boundariesDoNotInventApproval() {
        assertEquals("UPCOMING", WindowStateRules.state("BLOCKED_WAKEUP", 99, 100, 200, false))
        assertEquals("OPEN", WindowStateRules.state("BLOCKED_WAKEUP", 100, 100, 200, false))
        assertEquals("EXPIRED", WindowStateRules.state("BLOCKED_WAKEUP", 200, 100, 200, false))
        assertEquals("MISSED", WindowStateRules.state("UPCOMING", 200, 100, 200))
    }
    @Test fun recordedStatesSurviveDeadline() {
        for (saved in listOf("SUBMITTED", "PENDING_SYNC", "VERIFIED", "REJECTED", "EXCUSED"))
            assertEquals(saved, WindowStateRules.state(saved, 1000, 100, 200, false))
    }
    @Test fun cutoffIs0930() {
        assertFalse(WindowStateRules.wakeUpWindowOpen(419))
        assertTrue(WindowStateRules.wakeUpWindowOpen(420))
        assertTrue(WindowStateRules.wakeUpWindowOpen(569))
        assertFalse(WindowStateRules.wakeUpWindowOpen(570))
    }
}
