package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class PopupEntrySessionTest {
    @Test fun homeNavigationKeepsDismissalUntilAConfirmedExit() {
        val session = PopupEntrySession()
        val entry = session.observeTarget("youtube")
        assertTrue(session.claim("youtube", entry))
        session.dismiss("youtube")
        // Delayed app events while the Home action is in flight are the same visit.
        repeat(30) {
            assertEquals(entry, session.observeTarget("youtube"))
            session.release("youtube")
            assertFalse(session.claim("youtube", entry))
        }
        session.observeExit()
        val next = session.observeTarget("youtube")
        assertTrue(next > entry)
        assertTrue(session.claim("youtube", next))
    }

    @Test fun aFallbackKeepsItsClaimUntilItAttachesOrFails() {
        val session = PopupEntrySession()
        val id = session.observeTarget("youtube")
        assertTrue(session.claim("youtube", id))
        repeat(100) {
            assertTrue(session.isPresentationCurrent("youtube", id))
            assertFalse(session.claim("youtube", id))
        }
        session.failedAttach("youtube", id)
        assertFalse(session.isPresentationCurrent("youtube", id))
        assertTrue(session.claim("youtube", id))
        session.observeExit()
        assertFalse(session.isPresentationCurrent("youtube", id))
    }
    @Test fun aStaleFallbackCannotRegisterAfterDismissalOrAnotherVisit() {
        val session = PopupEntrySession()
        val old = session.observeTarget("youtube")
        assertTrue(session.claim("youtube", old))
        session.dismiss("youtube")
        assertFalse(session.isPresentationCurrent("youtube", old))
        session.observeExit()
        val current = session.observeTarget("youtube")
        assertTrue(session.claim("youtube", current))
        assertFalse(session.isPresentationCurrent("youtube", old))
        assertTrue(session.isPresentationCurrent("youtube", current))
    }
    @Test fun uncertaintyDoesNotPermanentlySuppressTheSameEntry() {
        val session = PopupEntrySession()
        val id = session.observeTarget("youtube")
        assertTrue(session.claim("youtube", id))
        session.release("youtube")
        assertEquals(id, session.observeTarget("youtube"))
        assertTrue(session.claim("youtube", id))
        session.dismiss("youtube")
        session.release("youtube")
        assertFalse(session.claim("youtube", id))
    }
    @Test fun simultaneousCallbacksClaimExactlyOnePresentation() {
        val session = PopupEntrySession()
        val id = session.observeTarget("spotify")
        val wins = java.util.concurrent.atomic.AtomicInteger()
        val threads = (1..20).map { Thread { if (session.claim("spotify", id)) wins.incrementAndGet() } }
        threads.forEach { it.start() }; threads.forEach { it.join() }
        assertEquals(1, wins.get())
    }
    @Test fun onePopupPerVisitEvenAfterDismissalOrLongPollingGap() {
        val session = PopupEntrySession()
        val id = session.observeTarget("youtube")
        assertTrue(session.claim("youtube", id))
        repeat(100) { assertEquals(id, session.observeTarget("youtube")); assertFalse(session.claim("youtube", id)) }
        session.dismiss("youtube")
        assertFalse(session.canPresent("youtube"))
        session.observeExit()
        assertTrue(session.claim("youtube", session.observeTarget("youtube")))
    }
    @Test fun switchedPackageDisposesStaleQueuedCreation() {
        val session = PopupEntrySession()
        val old = session.observeTarget("youtube")
        val new = session.observeTarget("instagram")
        assertFalse(session.claim("youtube", old))
        assertTrue(session.claim("instagram", new))
        session.observeExit()
        assertFalse(session.claim("instagram", new))
    }
    @Test fun failedCapabilityCanRetryWithoutAwardingOrDuplicatingShownPopup() {
        val session = PopupEntrySession()
        val id = session.observeTarget("youtube")
        assertTrue(session.claim("youtube", id))
        session.failedAttach("youtube", id)
        assertTrue(session.claim("youtube", id))
        assertFalse(session.claim("youtube", id))
    }
}
