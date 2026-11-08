package com.strictrecords.app

/** Timing policy for the automatic banking break. No Android APIs or score changes. */
object BankingBreakPlan {
    enum class Phase { SUSPENDED, WAITING_FOR_BANK_EXIT, MONITORING }

    fun phase(deadlineMs: Long, protectedInForeground: Boolean, nowMs: Long): Phase = when {
        protectedInForeground -> Phase.WAITING_FOR_BANK_EXIT
        nowMs < deadlineMs -> Phase.SUSPENDED
        else -> Phase.MONITORING
    }

    /** Repeated window events from one banking visit must not restart five minutes. */
    fun deadlineForObservation(previousDeadlineMs: Long, previousPackage: String,
                               protectedInForeground: Boolean, observedPackage: String,
                               nowMs: Long, durationMs: Long): Long =
        if (protectedInForeground && previousPackage == observedPackage) previousDeadlineMs
        else maxOf(previousDeadlineMs, nowMs + durationMs)

    fun deadlineAfterExit(previousDeadlineMs: Long, exitAtMs: Long, durationMs: Long): Long =
        maxOf(previousDeadlineMs, exitAtMs + durationMs)
}
