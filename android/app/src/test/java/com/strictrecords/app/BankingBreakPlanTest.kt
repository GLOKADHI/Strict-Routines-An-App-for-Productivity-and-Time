package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class BankingBreakPlanTest {
    private val duration = 300_000L
    private val start = 1_000_000L
    private val deadline = start + duration

    @Test fun aBankLaunchSuspendsMonitoringForFiveFullMinutes() {
        assertEquals(BankingBreakPlan.Phase.WAITING_FOR_BANK_EXIT, BankingBreakPlan.phase(deadline, true, start))
        assertEquals(BankingBreakPlan.Phase.WAITING_FOR_BANK_EXIT, BankingBreakPlan.phase(deadline, true, deadline - 1))
    }

    @Test fun leavingBankEarlyDoesNotCancelTheFiveMinuteBreak() {
        assertEquals(BankingBreakPlan.Phase.SUSPENDED, BankingBreakPlan.phase(deadline, false, start + 10_000))
        assertEquals(BankingBreakPlan.Phase.MONITORING, BankingBreakPlan.phase(deadline, false, deadline))
    }

    @Test fun aBankStillInFrontKeepsEnforcementPausedAfterTheTimer() {
        assertEquals(BankingBreakPlan.Phase.WAITING_FOR_BANK_EXIT, BankingBreakPlan.phase(deadline, true, deadline))
        assertEquals(BankingBreakPlan.Phase.WAITING_FOR_BANK_EXIT, BankingBreakPlan.phase(deadline, true, deadline + duration))
        assertEquals(BankingBreakPlan.Phase.MONITORING, BankingBreakPlan.phase(deadline, false, deadline + duration))
    }

    @Test fun restoredDeadlinesUseTheOriginalTimeInsteadOfStartingAnotherFiveMinutes() {
        assertEquals(BankingBreakPlan.Phase.WAITING_FOR_BANK_EXIT, BankingBreakPlan.phase(deadline, true, deadline - 1000))
        assertEquals(BankingBreakPlan.Phase.MONITORING, BankingBreakPlan.phase(deadline, false, deadline + 1000))
    }

    @Test fun repeatedBankEventsNeverPostponeTheSameVisit() {
        for (offset in listOf(1L, 1000L, duration - 1, duration, duration * 2)) {
            assertEquals(deadline, BankingBreakPlan.deadlineForObservation(deadline, "bank", true,
                "bank", start + offset, duration))
        }
    }

    @Test fun aDifferentBankOrGenuineReentryGetsANewFiveMinutes() {
        assertEquals(deadline + 10_000, BankingBreakPlan.deadlineForObservation(deadline,
            "bank", true, "other.bank", start + 10_000, duration))
        assertEquals(deadline + duration, BankingBreakPlan.deadlineForObservation(deadline,
            "bank", false, "bank", deadline, duration))
    }

    @Test fun aNewBreakCannotShortenAnExistingDeadline() {
        assertEquals(deadline, BankingBreakPlan.deadlineForObservation(deadline,
            "bank", false, "other.bank", start - 1000, duration))
    }

    @Test fun fiveMinutesStartAfterTheProtectedSessionEnds() {
        assertEquals(1660000L, BankingBreakPlan.deadlineAfterExit(deadline, 1360000L, duration))
        assertEquals(BankingBreakPlan.Phase.SUSPENDED, BankingBreakPlan.phase(1660000L, false, 1659999L))
        assertEquals(BankingBreakPlan.Phase.MONITORING, BankingBreakPlan.phase(1660000L, false, 1660000L))
    }

    @Test fun noSavedPauseMeansNormalMonitoring() {
        assertEquals(BankingBreakPlan.Phase.MONITORING, BankingBreakPlan.phase(0, false, start))
        assertEquals(deadline, BankingBreakPlan.deadlineForObservation(0, "", false, "bank", start, duration))
    }
}
