package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class PopupForegroundRecheckTest {
    @Test fun repeatedUnknownEventsCannotPostponeTheFirstDeadline() {
        val state = PopupForegroundRecheck()
        val request = state.schedule("youtube", 1000L)!!
        assertEquals(1180L, request.deadlineMs)
        repeat(180) { assertNull(state.schedule("youtube", 1000L + it)) }
        assertEquals(1L, request.remainingMs(1179L))
        assertEquals(0L, request.remainingMs(1180L))
        assertEquals(0L, request.remainingMs(5000L))
        assertTrue(state.complete(request))
        assertFalse(state.complete(request))
    }

    @Test fun aFreshConfirmationCancelsTheOldCallback() {
        val state = PopupForegroundRecheck()
        val request = state.schedule("youtube", 1000L)!!
        state.cancel()
        assertFalse(state.complete(request))
        assertNotNull(state.schedule("youtube", 2000L))
    }

    @Test fun aStaleCallbackCannotClearTheNextAppsRecheck() {
        val state = PopupForegroundRecheck()
        val old = state.schedule("youtube", 1000L)!!
        state.cancel()
        val current = state.schedule("spotify", 1100L)!!
        assertFalse(state.complete(old))
        assertNull(state.schedule("spotify", 1150L))
        assertEquals("spotify", current.packageName)
        assertEquals(1280L, current.deadlineMs)
        assertTrue(state.complete(current))
    }

    @Test fun noVisibleTargetMeansNoRetentionRequest() {
        val state = PopupForegroundRecheck()
        assertNull(state.schedule("", 1000L))
        assertNull(state.schedule(" ", 1000L))
        assertNotNull(state.schedule("youtube", 1000L))
    }
}
