package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class RestrictedUsageTimelineTest {
    private fun started(restricted: Boolean? = true, interactive: Boolean = true) = RestrictedUsageTimeline().apply {
        begin("com.spotify.music", 1000, restricted, interactive, "account|date|zone|revision|score")
    }
    @Test fun fiftyNineSecondsDoesNotCreateAFullMinute() {
        val tracker = started()
        assertNull(tracker.confirm(60_000, true))
        assertEquals(59_000, tracker.remainderMs())
    }
    @Test fun sixtySecondsProducesOneEvidenceMinute() {
        val tracker = started()
        val minutes = tracker.confirm(61_000, true)!!
        assertEquals(1, minutes.count)
        assertEquals(0, tracker.remainderMs())
    }
    @Test fun twoMinutesFortySecondsBatchesTwoAndCarriesForty() {
        val tracker = started()
        val minutes = tracker.confirm(161_000, true)!!
        assertEquals(2, minutes.count)
        assertEquals(40_000, tracker.remainderMs())
        assertEquals(1, tracker.confirm(181_000, true)!!.count)
        assertEquals(0, tracker.remainderMs())
    }
    @Test fun duplicateEventsAndReplayedConfirmationsDoNotRepeatEvidence() {
        val tracker = started()
        val first = tracker.confirm(61_000, true)!!
        repeat(20) {
            tracker.begin("com.spotify.music", 61_000, true, true, "account|date|zone|revision|score")
            assertNull(tracker.confirm(61_000, true))
        }
        assertEquals(first.idempotencyMaterial("account", "device"), first.copy().idempotencyMaterial("account", "device"))
        assertNotEquals(first.idempotencyMaterial("account", "device"), first.idempotencyMaterial("other", "device"))
    }
    @Test fun aFreshProcessCannotChargeTheUnobservedGapOrReplayAnOldMinute() {
        val tracker = started()
        assertNotNull(tracker.confirm(61_000, true))
        val restarted = RestrictedUsageTimeline()
        assertNull(restarted.confirm(200_000, true))
        restarted.begin("com.spotify.music", 200_000, true, true, "key")
        assertNotNull(restarted.confirm(260_000, true))
    }
    @Test fun backgroundAudioAfterPauseDoesNotAccrue() {
        val tracker = started()
        assertNull(tracker.end(31_000, true))
        assertNull(tracker.confirm(631_000, true))
        assertNull(tracker.session)
    }
    @Test fun lockOrUncertainObservationExcludesTheIntervalAndRemainder() {
        val tracker = started()
        assertNull(tracker.confirm(60_000, true))
        assertNull(tracker.confirm(61_000, false))
        assertNull(tracker.session)
        assertEquals(0, tracker.remainderMs())
        assertNull(started(interactive = false).confirm(200_000, true))
    }
    @Test fun eligibleAndUnknownEligibilityCannotAccrueRestrictedMinutes() {
        assertNull(started(false).confirm(181_000, true))
        assertEquals(0, started(false).session!!.confirmedMs)
        assertNull(started(null).session)
    }
    @Test fun aFinancialPauseClearsTheSessionWithoutCountingFinancialTime() {
        val tracker = started()
        tracker.gap("Protected financial session.")
        assertNull(tracker.confirm(181_000, true))
    }
    @Test fun authoritativeEligibilityRevisionChangeStartsANewInterval() {
        val tracker = started()
        tracker.confirm(60_000, true)
        tracker.begin("com.spotify.music", 61_000, false, true, "new-revision")
        assertNull(tracker.confirm(181_000, true))
        assertEquals(0, tracker.session!!.confirmedMs)
    }
    @Test fun clockReversalNeverAddsNegativeOrManufacturedTime() {
        val tracker = started()
        assertNull(tracker.confirm(999, true))
        assertNull(tracker.session)
    }
}
