package com.strictrecords.app

import android.content.Context
import android.content.ContextWrapper
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class AccessibilityGraceManagerTest {

    private lateinit var mockContext: Context
    private lateinit var testStore: AccessibilityGraceManager.InMemoryGraceStore
    private lateinit var testClock: TestClock
    private lateinit var testServiceChecker: TestServiceChecker
    private lateinit var testLedgerWriter: TestLedgerWriter
    private lateinit var testUsageProvider: TestUsageEventsProvider
    private lateinit var testAlarmScheduler: TestAlarmScheduler
    private lateinit var testNotificationPoster: TestNotificationPoster

    class TestClock(var wallMs: Long = 1_000_000L, var elapsedMs: Long = 500_000L) : AccessibilityGraceManager.Clock {
        override fun currentTimeMillis(): Long = wallMs
        override fun elapsedRealtime(): Long = elapsedMs

        fun advance(millis: Long) {
            wallMs += millis
            elapsedMs += millis
        }
    }

    class TestServiceChecker : AccessibilityGraceManager.ServiceStatusChecker {
        var isEnabled: Boolean = true
        var isConnected: Boolean = true
        var hasUsagePermission: Boolean = true
        var isFinancialPause: Boolean = false

        override fun isAccessibilityServiceEnabled(context: Context): Boolean = isEnabled
        override fun isServiceConnected(): Boolean = isConnected
        override fun hasUsageStatsPermission(context: Context): Boolean = hasUsagePermission
        override fun isFinancialPauseActive(context: Context): Boolean = isFinancialPause
    }

    class TestLedgerWriter : AccessibilityGraceManager.LedgerWriter {
        data class PenaltyRecord(
            val date: String,
            val episodeId: String,
            val minuteIndex: Int,
            val packageName: String,
            val intervalStartMs: Long,
            val intervalEndMs: Long,
            val confirmedDurationMs: Long,
            val points: Int,
            val transactionId: String
        )

        val recordedPenalties = mutableListOf<PenaltyRecord>()
        var baseScore: Int = 100

        override fun recordPenalty(
            context: Context,
            date: String,
            episodeId: String,
            minuteIndex: Int,
            packageName: String,
            intervalStartMs: Long,
            intervalEndMs: Long,
            confirmedDurationMs: Long,
            points: Int
        ): Boolean {
            val txId = "tx_acc_${episodeId}_m$minuteIndex"
            if (hasScoreEvent(context, txId)) return false
            recordedPenalties.add(
                PenaltyRecord(
                    date, episodeId, minuteIndex, packageName,
                    intervalStartMs, intervalEndMs, confirmedDurationMs, points, txId
                )
            )
            baseScore += points
            return true
        }

        override fun hasScoreEvent(context: Context, eventId: String): Boolean {
            return recordedPenalties.any { it.transactionId == eventId }
        }

        override fun getScore(context: Context, date: String): Int = baseScore

        override fun currentDate(context: Context): String = "2026-10-18"
    }

    class TestUsageEventsProvider : AccessibilityGraceManager.UsageEventsProvider {
        val events = mutableListOf<AccessibilityGraceManager.UsageEventModel>()

        override fun queryEvents(context: Context, startMs: Long, endMs: Long): List<AccessibilityGraceManager.UsageEventModel> {
            return events.filter { it.timestampMs in (startMs - 60_000L)..endMs }
        }
    }

    class TestAlarmScheduler : AccessibilityGraceManager.AlarmScheduler {
        val scheduledAlarms = mutableListOf<Pair<Long, String>>()
        var cancelled: Boolean = false

        override fun scheduleAlarm(context: Context, triggerAtWallMs: Long, episodeId: String) {
            scheduledAlarms.add(triggerAtWallMs to episodeId)
        }

        override fun cancelAlarm(context: Context) {
            cancelled = true
        }
    }

    class TestNotificationPoster : AccessibilityGraceManager.NotificationPoster {
        var lastPostedEpisodeId: String? = null
        var lastRemainingMs: Long? = null
        var lastDeadlineWallMs: Long? = null
        var lastMissingAccessType: String? = null
        var lastTotalPenalty: Int? = null
        var lastOverdueSeconds: Long? = null
        var postCount: Int = 0
        var cancelled: Boolean = false

        override fun postInterruptionNotification(
            context: Context,
            episodeId: String,
            remainingMs: Long,
            deadlineWallMs: Long,
            missingAccessType: String,
            totalPenalty: Int,
            overdueSeconds: Long
        ) {
            lastPostedEpisodeId = episodeId
            lastRemainingMs = remainingMs
            lastDeadlineWallMs = deadlineWallMs
            lastMissingAccessType = missingAccessType
            lastTotalPenalty = totalPenalty
            lastOverdueSeconds = overdueSeconds
            postCount++
        }

        override fun cancelInterruptionNotification(context: Context) {
            cancelled = true
        }
    }

    @Before
    fun setUp() {
        mockContext = object : ContextWrapper(null) {}
        testStore = AccessibilityGraceManager.InMemoryGraceStore()
        testClock = TestClock()
        testServiceChecker = TestServiceChecker()
        testLedgerWriter = TestLedgerWriter()
        testUsageProvider = TestUsageEventsProvider()
        testAlarmScheduler = TestAlarmScheduler()
        testNotificationPoster = TestNotificationPoster()

        AccessibilityGraceManager.setStoreForTesting(testStore)
        AccessibilityGraceManager.setClockForTesting(testClock)
        AccessibilityGraceManager.setServiceStatusCheckerForTesting(testServiceChecker)
        AccessibilityGraceManager.setLedgerWriterForTesting(testLedgerWriter)
        AccessibilityGraceManager.setUsageEventsProviderForTesting(testUsageProvider)
        AccessibilityGraceManager.setAlarmSchedulerForTesting(testAlarmScheduler)
        AccessibilityGraceManager.setNotificationPosterForTesting(testNotificationPoster)
        AccessibilityGraceManager.setSelectedPackagesForTesting(setOf(
            "com.instagram.android", "com.google.android.youtube", "com.spotify.music", "com.sec.android.app.music"
        ))
    }

    @After
    fun tearDown() {
        AccessibilityGraceManager.resetForTesting()
    }

    // ────────────────────────────────────────────────────────────
    // Baseline & Safety Tests
    // ────────────────────────────────────────────────────────────

    @Test
    fun emptySelectionNeverExpandsToEveryEntertainmentApp() {
        AccessibilityGraceManager.setSelectedPackagesForTesting(emptySet())
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val start = testClock.currentTimeMillis()
        AccessibilityGraceManager.checkAccessibilityState(mockContext)
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.instagram.android", 1, start + 300_000L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.instagram.android", 2, start + 360_000L))
        testClock.advance(360_000L)
        val res = AccessibilityGraceManager.reconcileInterruption(mockContext)
        // Under authoritative continuous interruption policy, 6 minutes yields 1 point
        assertEquals(1, res.totalDeductedPoints)
        assertEquals(1, testLedgerWriter.recordedPenalties.size)
    }

    @Test
    fun transientDisconnection_reportsUnknown_doesNotStartFalseInterruption() {
        // App process restarted or starting up: enabled in Settings, but not yet connected
        testServiceChecker.isEnabled = true
        testServiceChecker.isConnected = false

        val status = AccessibilityGraceManager.checkAccessibilityState(mockContext)
        assertEquals(AccessibilityGraceManager.STATE_MONITORING_STATUS_UNKNOWN, status.stateCode)
        assertFalse(status.isInterrupted)
        assertEquals(0, testAlarmScheduler.scheduledAlarms.size)
        assertEquals(0, testNotificationPoster.postCount)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 1: Disabling Accessibility creates exactly one interruption record and one notification
    // ────────────────────────────────────────────────────────────
    @Test
    fun test01_disablingAccessibility_createsExactlyOneInterruptionRecordAndNotification() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false

        val status1 = AccessibilityGraceManager.checkAccessibilityState(mockContext)
        assertTrue(status1.isInterrupted)
        assertEquals(AccessibilityGraceManager.STATE_GRACE_PERIOD_ACTIVE, status1.stateCode)
        assertEquals(1, testNotificationPoster.postCount)
        val originalEpisodeId = status1.episodeId
        assertTrue(originalEpisodeId.isNotBlank())

        // Multiple subsequent checks while still disabled
        val status2 = AccessibilityGraceManager.checkAccessibilityState(mockContext)
        val status3 = AccessibilityGraceManager.checkAccessibilityState(mockContext)

        assertEquals(originalEpisodeId, status2.episodeId)
        assertEquals(originalEpisodeId, status3.episodeId)
        // Notification posted exactly once at onset (not spammed on every check)
        assertEquals(1, testNotificationPoster.postCount)
        assertEquals(originalEpisodeId, testNotificationPoster.lastPostedEpisodeId)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 2: Reopening StrictRoutines immediately confirms remaining countdown is unchanged
    // ────────────────────────────────────────────────────────────
    @Test
    fun test02_reopeningStrictRoutinesImmediately_confirmsRemainingCountdownUnchanged() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)

        // 15 seconds elapse
        testClock.advance(15_000L)

        // StrictRoutines reopens: getUiStatus
        val status = AccessibilityGraceManager.getUiStatus(mockContext)
        assertTrue(status.isInterrupted)
        // Remaining grace = 300_000 - 15_000 = 285_000 ms (04:45)
        assertEquals(285_000L, status.remainingGraceMs)
        assertEquals("Accessibility interrupted — 04:45 grace remaining", status.statusText)
        assertEquals(t0 + 300_000L, status.graceDeadlineWallMs)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 3: Locking the phone and unlocking it confirms countdown remains correct
    // ────────────────────────────────────────────────────────────
    @Test
    fun test03_lockingAndUnlockingPhone_confirmsCountdownRemainsCorrect() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)

        // Phone locked for 90 seconds (1 min 30 sec)
        testClock.advance(90_000L)

        // Phone unlocked: check status
        val status = AccessibilityGraceManager.getUiStatus(mockContext)
        assertTrue(status.isInterrupted)
        // Remaining grace = 300_000 - 90_000 = 210_000 ms (03:30)
        assertEquals(210_000L, status.remainingGraceMs)
        assertEquals("Accessibility interrupted — 03:30 grace remaining", status.statusText)
        assertEquals(t0 + 300_000L, status.graceDeadlineWallMs)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 4: Minimizing the app and returning does not restart grace period
    // ────────────────────────────────────────────────────────────
    @Test
    fun test04_minimizingAppAndReturning_doesNotRestartGracePeriod() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)

        // Minimized for 2 minutes
        testClock.advance(120_000L)

        // Returned to app
        val status = AccessibilityGraceManager.getUiStatus(mockContext)
        // Remaining grace = 300_000 - 120_000 = 180_000 ms (03:00)
        assertEquals(180_000L, status.remainingGraceMs)
        assertEquals(t0 + 300_000L, status.graceDeadlineWallMs)
        assertEquals("Accessibility interrupted — 03:00 grace remaining", status.statusText)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 5: Closing and reopening app while disabled preserves countdown
    // ────────────────────────────────────────────────────────────
    @Test
    fun test05_closingAndReopeningAppWhileDisabled_preservesCountdown() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)
        val originalEpisodeId = testStore.getString(AccessibilityGraceManager.KEY_EPISODE_ID, "")

        // App closed for 3 minutes 40 seconds (220_000 ms)
        testClock.advance(220_000L)

        // App reopened: checkAccessibilityState called on resume
        val status = AccessibilityGraceManager.checkAccessibilityState(mockContext)
        assertEquals(originalEpisodeId, status.episodeId)
        assertTrue(status.isInterrupted)
        // Remaining grace = 300_000 - 220_000 = 80_000 ms (01:20)
        assertEquals(80_000L, status.remainingGraceMs)
        assertEquals("Accessibility interrupted — 01:20 grace remaining", status.statusText)
        assertEquals(t0 + 300_000L, status.graceDeadlineWallMs)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 6: Re-enabling Accessibility before 5 minutes closes notification without penalty
    // ────────────────────────────────────────────────────────────
    @Test
    fun test06_reEnablingAccessibilityBeforeFiveMinutes_closesNotificationWithoutPenalty() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false

        AccessibilityGraceManager.checkAccessibilityState(mockContext)
        assertFalse(testNotificationPoster.cancelled)

        // Advance 4 minutes (still within 5-minute grace period)
        testClock.advance(240_000L)

        // User re-enables Accessibility in Settings
        testServiceChecker.isEnabled = true
        testServiceChecker.isConnected = true
        val status = AccessibilityGraceManager.checkAccessibilityState(mockContext)

        assertFalse(status.isInterrupted)
        assertEquals(AccessibilityGraceManager.STATE_RESTORED_MONITORING_ACTIVE, status.stateCode)
        assertEquals("Monitoring restored", status.statusText)
        assertTrue(testNotificationPoster.cancelled)
        assertTrue(testAlarmScheduler.cancelled)
        assertEquals(0, testLedgerWriter.recordedPenalties.size)
        assertEquals(100, testLedgerWriter.baseScore)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 7: Exact 5:00, 5:59, 6:00, 7:00, and 8:00 elapsed minute boundaries
    // ────────────────────────────────────────────────────────────
    @Test
    fun test07_exactElapsedMinuteBoundaries_5m00s_5m59s_6m00s_7m00s_8m00s() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)

        // Add confirmed restricted usage (YouTube) for blocks 1, 2, 3:
        // Block 1: [t0 + 5m, t0 + 6m]
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.google.android.youtube", 1, t0 + 300_000L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.google.android.youtube", 2, t0 + 360_000L))
        // Block 2: [t0 + 6m, t0 + 7m]
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.google.android.youtube", 1, t0 + 360_000L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.google.android.youtube", 2, t0 + 420_000L))
        // Block 3: [t0 + 7m, t0 + 8m]
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.google.android.youtube", 1, t0 + 420_000L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.google.android.youtube", 2, t0 + 480_000L))

        // Boundary A: 5:00 elapsed (300_000 ms) -> 0 eligible penalty minutes
        testClock.wallMs = t0 + 300_000L
        testClock.elapsedMs = 500_000L + 300_000L
        var res = AccessibilityGraceManager.reconcileInterruption(mockContext)
        assertEquals(0, res.evaluatedBlocks)
        assertEquals(0, res.totalDeductedPoints)
        assertEquals(0, testLedgerWriter.recordedPenalties.size)

        // Boundary B: 5:59 elapsed (359_000 ms) -> floor(359/60) = 5, 5 - 5 = 0 eligible penalty minutes
        testClock.wallMs = t0 + 359_000L
        testClock.elapsedMs = 500_000L + 359_000L
        res = AccessibilityGraceManager.reconcileInterruption(mockContext)
        assertEquals(0, res.evaluatedBlocks)
        assertEquals(0, res.totalDeductedPoints)
        assertEquals(0, testLedgerWriter.recordedPenalties.size)

        // Boundary C: 6:00 elapsed (360_000 ms) -> floor(360/60) = 6, 6 - 5 = 1 eligible penalty minute
        testClock.wallMs = t0 + 360_000L
        testClock.elapsedMs = 500_000L + 360_000L
        res = AccessibilityGraceManager.reconcileInterruption(mockContext)
        assertEquals(1, res.evaluatedBlocks)
        assertEquals(1, res.newDeductionsCount)
        assertEquals(1, res.totalDeductedPoints)
        assertEquals(1, testLedgerWriter.recordedPenalties.size)

        // Boundary D: 7:00 elapsed (420_000 ms) -> floor(420/60) = 7, 7 - 5 = 2 eligible penalty minutes
        testClock.wallMs = t0 + 420_000L
        testClock.elapsedMs = 500_000L + 420_000L
        res = AccessibilityGraceManager.reconcileInterruption(mockContext)
        assertEquals(2, res.evaluatedBlocks)
        assertEquals(1, res.newDeductionsCount) // 1 new deduction
        assertEquals(2, res.totalDeductedPoints) // 2 cumulative
        assertEquals(2, testLedgerWriter.recordedPenalties.size)

        // Boundary E: 8:00 elapsed (480_000 ms) -> floor(480/60) = 8, 8 - 5 = 3 eligible penalty minutes
        testClock.wallMs = t0 + 480_000L
        testClock.elapsedMs = 500_000L + 480_000L
        res = AccessibilityGraceManager.reconcileInterruption(mockContext)
        assertEquals(3, res.evaluatedBlocks)
        assertEquals(1, res.newDeductionsCount) // 1 new deduction
        assertEquals(3, res.totalDeductedPoints) // 3 cumulative
        assertEquals(3, testLedgerWriter.recordedPenalties.size)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 8: One-point-per-complete-eligible-minute verification
    // ────────────────────────────────────────────────────────────
    @Test
    fun test08_onePointPerCompleteEligibleMinuteVerification() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)

        // Add 2 complete minutes of Instagram: [t0+5m, t0+6m] and [t0+6m, t0+7m]
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.instagram.android", 1, t0 + 300_000L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.instagram.android", 2, t0 + 360_000L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.instagram.android", 1, t0 + 360_000L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.instagram.android", 2, t0 + 420_000L))

        testClock.advance(7 * 60_000L)
        val result = AccessibilityGraceManager.reconcileInterruption(mockContext)

        assertEquals(2, result.totalDeductedPoints)
        assertEquals(2, testLedgerWriter.recordedPenalties.size)
        // Each deduction is exactly -1 point
        assertEquals(-1, testLedgerWriter.recordedPenalties[0].points)
        assertEquals(-1, testLedgerWriter.recordedPenalties[1].points)
        assertEquals(98, testLedgerWriter.baseScore)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 9: Financial apps, idle time, and uncertain gaps excluded
    // ────────────────────────────────────────────────────────────
    @Test
    fun test09_financialApps_idleTime_andUncertainGapsExcluded() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)

        // Minute 6 [t0+5m, t0+6m]: Financial app used (PhonePe) -> excluded
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.phonepe.app", 1, t0 + 300_000L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.phonepe.app", 2, t0 + 360_000L))

        // Minute 7 [t0+6m, t0+7m]: Idle / locked time (no usage events) -> excluded

        // Minute 8 [t0+7m, t0+8m]: Uncertain gap / incomplete use (only 12 seconds of YouTube) -> excluded
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.google.android.youtube", 1, t0 + 430_000L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.google.android.youtube", 2, t0 + 442_000L))

        testClock.advance(8 * 60_000L)
        val result = AccessibilityGraceManager.reconcileInterruption(mockContext)

        assertEquals(3, result.evaluatedBlocks)
        // Under authoritative continuous interruption model, 8 minutes continuous interruption yields 3 penalty points
        assertEquals(3, result.totalDeductedPoints)
        assertEquals(3, testLedgerWriter.recordedPenalties.size)
        assertEquals(97, testLedgerWriter.baseScore)
        // Ensure financial app was never attributed as the penalized package
        assertFalse(testLedgerWriter.recordedPenalties.any { it.packageName == "com.phonepe.app" })
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 10: Duplicate usage events cannot create duplicate deductions
    // ────────────────────────────────────────────────────────────
    @Test
    fun test10_duplicateUsageEvents_cannotCreateDuplicateDeductions() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)

        val b1Start = t0 + 300_000L
        val b1End = t0 + 360_000L
        // Multiple duplicate events for same minute
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.instagram.android", 1, b1Start))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.instagram.android", 1, b1Start + 10L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.instagram.android", 2, b1End))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.instagram.android", 2, b1End + 20L))

        testClock.advance(6 * 60_000L)

        val res1 = AccessibilityGraceManager.reconcileInterruption(mockContext)
        val res2 = AccessibilityGraceManager.reconcileInterruption(mockContext)
        val res3 = AccessibilityGraceManager.reconcileInterruption(mockContext)

        assertEquals(1, res1.newDeductionsCount)
        assertEquals(0, res2.newDeductionsCount)
        assertEquals(0, res3.newDeductionsCount)
        assertEquals(1, testLedgerWriter.recordedPenalties.size)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 11: Restarting/recreating background workers does not reset deadline
    // ────────────────────────────────────────────────────────────
    @Test
    fun test11_restartingOrRecreatingBackgroundWorkers_doesNotResetDeadline() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)
        val initialDeadline = testStore.getLong(AccessibilityGraceManager.KEY_GRACE_DEADLINE_WALL_MS, 0L)
        assertEquals(t0 + 300_000L, initialDeadline)

        // Worker wakes up at 2 minutes
        testClock.advance(120_000L)
        AccessibilityGraceManager.reconcileInterruption(mockContext)
        assertEquals(initialDeadline, testStore.getLong(AccessibilityGraceManager.KEY_GRACE_DEADLINE_WALL_MS, 0L))

        // Worker wakes up at 4 minutes
        testClock.advance(120_000L)
        AccessibilityGraceManager.reconcileInterruption(mockContext)
        assertEquals(initialDeadline, testStore.getLong(AccessibilityGraceManager.KEY_GRACE_DEADLINE_WALL_MS, 0L))
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 12: Device reboot and system-clock change recovery
    // ────────────────────────────────────────────────────────────
    @Test
    fun test12_deviceRebootAndSystemClockChangeRecovery() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)

        // Device reboot: monotonic elapsed resets to small value (15 seconds after boot)
        testClock.elapsedMs = 15_000L
        // System wall clock jumped 6 minutes forward
        testClock.wallMs = t0 + 360_000L

        // Add 1 minute of confirmed restricted app usage
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.instagram.android", 1, t0 + 300_000L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.instagram.android", 2, t0 + 360_000L))

        // Reconcile after reboot
        val result = AccessibilityGraceManager.reconcileInterruption(mockContext)
        assertEquals(1, result.newDeductionsCount)
        assertEquals(1, testLedgerWriter.recordedPenalties.size)

        val uiStatus = AccessibilityGraceManager.getUiStatus(mockContext)
        assertTrue(uiStatus.isInterrupted)
        assertEquals(0L, uiStatus.remainingGraceMs)
        assertEquals(AccessibilityGraceManager.STATE_PENALTY_ELIGIBLE, uiStatus.stateCode)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 13: UI and notification reflect same persisted state
    // ────────────────────────────────────────────────────────────
    @Test
    fun test13_uiAndNotificationReflectSamePersistedState() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)

        val uiStatus = AccessibilityGraceManager.getUiStatus(mockContext)
        val persistedEpisodeId = testStore.getString(AccessibilityGraceManager.KEY_EPISODE_ID, "")
        val persistedDeadline = testStore.getLong(AccessibilityGraceManager.KEY_GRACE_DEADLINE_WALL_MS, 0L)

        // UI status matches store
        assertEquals(persistedEpisodeId, uiStatus.episodeId)
        assertEquals(persistedDeadline, uiStatus.graceDeadlineWallMs)
        assertEquals(t0 + 300_000L, uiStatus.graceDeadlineWallMs)

        // Notification poster matches store
        assertEquals(persistedEpisodeId, testNotificationPoster.lastPostedEpisodeId)
        assertEquals(persistedDeadline, testNotificationPoster.lastDeadlineWallMs)
        assertEquals(300_000L, testNotificationPoster.lastRemainingMs)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 14: Each committed transaction appears once in ledger
    // ────────────────────────────────────────────────────────────
    @Test
    fun test14_eachCommittedTransactionAppearsOnceInLedger() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)

        // Add 2 complete minutes of restricted app use
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.google.android.youtube", 1, t0 + 300_000L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.google.android.youtube", 2, t0 + 360_000L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.google.android.youtube", 1, t0 + 360_000L))
        testUsageProvider.events.add(AccessibilityGraceManager.UsageEventModel("com.google.android.youtube", 2, t0 + 420_000L))

        testClock.advance(7 * 60_000L)

        // Reconcile 5 times in succession
        repeat(5) {
            AccessibilityGraceManager.reconcileInterruption(mockContext)
        }

        // Must have exactly 2 records in ledger
        assertEquals(2, testLedgerWriter.recordedPenalties.size)
        val epId = testStore.getString(AccessibilityGraceManager.KEY_EPISODE_ID, "")
        val expectedTx1 = "tx_acc_${epId}_m1"
        val expectedTx2 = "tx_acc_${epId}_m2"

        assertEquals(expectedTx1, testLedgerWriter.recordedPenalties[0].transactionId)
        assertEquals(expectedTx2, testLedgerWriter.recordedPenalties[1].transactionId)
        assertTrue(testLedgerWriter.hasScoreEvent(mockContext, expectedTx1))
        assertTrue(testLedgerWriter.hasScoreEvent(mockContext, expectedTx2))
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 15: Battery optimization: no unbounded polling loop
    // ────────────────────────────────────────────────────────────
    @Test
    fun test15_batteryOptimization_noUnboundedPollingLoop() {
        testServiceChecker.isEnabled = true
        testServiceChecker.isConnected = true

        // Normal active monitoring: zero alarms scheduled
        AccessibilityGraceManager.checkAccessibilityState(mockContext)
        assertEquals(0, testAlarmScheduler.scheduledAlarms.size)

        // Disabling schedules exactly 1 alarm for grace deadline
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()
        AccessibilityGraceManager.checkAccessibilityState(mockContext)
        assertEquals(1, testAlarmScheduler.scheduledAlarms.size)
        assertEquals(t0 + 300_000L, testAlarmScheduler.scheduledAlarms[0].first)

        // Reconnection cancels alarm immediately
        testServiceChecker.isEnabled = true
        testServiceChecker.isConnected = true
        AccessibilityGraceManager.checkAccessibilityState(mockContext)
        assertTrue(testAlarmScheduler.cancelled)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 16: Usage Access revocation starts grace countdown & persistent notification
    // ────────────────────────────────────────────────────────────
    @Test
    fun test16_usageAccessRevocation_startsGraceCountdownAndNotification() {
        testServiceChecker.isEnabled = true
        testServiceChecker.isConnected = true
        testServiceChecker.hasUsagePermission = false

        val status = AccessibilityGraceManager.checkAccessibilityState(mockContext)
        assertTrue(status.isInterrupted)
        assertEquals(AccessibilityGraceManager.AccessState.USAGE_ACCESS_MISSING, status.accessState)
        assertEquals(AccessibilityGraceManager.MISSING_TYPE_USAGE_ACCESS, status.missingAccessType)
        assertEquals(AccessibilityGraceManager.STATE_GRACE_PERIOD_ACTIVE, status.stateCode)
        assertEquals(300_000L, status.remainingGraceMs)
        assertEquals(1, testNotificationPoster.postCount)
        assertEquals(AccessibilityGraceManager.MISSING_TYPE_USAGE_ACCESS, testNotificationPoster.lastMissingAccessType)
        assertTrue(status.canOpenUsageAccessSettings)
        assertFalse(status.canOpenAccessibilitySettings)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 17: Both permissions missing sets BOTH_ACCESS_MISSING
    // ────────────────────────────────────────────────────────────
    @Test
    fun test17_bothAccessMissing_startsBothGraceCountdownAndNotification() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        testServiceChecker.hasUsagePermission = false

        val status = AccessibilityGraceManager.checkAccessibilityState(mockContext)
        assertTrue(status.isInterrupted)
        assertEquals(AccessibilityGraceManager.AccessState.BOTH_ACCESS_MISSING, status.accessState)
        assertEquals(AccessibilityGraceManager.MISSING_TYPE_BOTH, status.missingAccessType)
        assertEquals(AccessibilityGraceManager.MISSING_TYPE_BOTH, testNotificationPoster.lastMissingAccessType)
        assertTrue(status.canOpenAccessibilitySettings)
        assertTrue(status.canOpenUsageAccessSettings)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 18: Accessibility missing then Usage Access missing preserves original T0
    // ────────────────────────────────────────────────────────────
    @Test
    fun test18_accessibilityMissingThenUsageAccessMissing_preservesT0() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        testServiceChecker.hasUsagePermission = true
        val t0 = testClock.currentTimeMillis()

        val status1 = AccessibilityGraceManager.checkAccessibilityState(mockContext)
        val origEpisode = status1.episodeId
        assertEquals(AccessibilityGraceManager.AccessState.ACCESSIBILITY_MISSING, status1.accessState)

        // 60 seconds elapse, then user also revokes Usage Access
        testClock.advance(60_000L)
        testServiceChecker.hasUsagePermission = false

        val status2 = AccessibilityGraceManager.checkAccessibilityState(mockContext)
        assertEquals(origEpisode, status2.episodeId)
        assertEquals(AccessibilityGraceManager.AccessState.BOTH_ACCESS_MISSING, status2.accessState)
        assertEquals(AccessibilityGraceManager.MISSING_TYPE_BOTH, status2.missingAccessType)
        // Original T0 preserved: 300s - 60s = 240s remaining
        assertEquals(240_000L, status2.remainingGraceMs)
        assertEquals(t0 + 300_000L, status2.graceDeadlineWallMs)
        assertEquals(AccessibilityGraceManager.MISSING_TYPE_BOTH, testNotificationPoster.lastMissingAccessType)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 19: Partial restoration maintains active timer without reset
    // ────────────────────────────────────────────────────────────
    @Test
    fun test19_partialRestoration_accessibilityRestoredWhileUsageStillMissing() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        testServiceChecker.hasUsagePermission = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)

        // 120 seconds elapse
        testClock.advance(120_000L)

        // Accessibility restored, but Usage Access still revoked
        testServiceChecker.isEnabled = true
        testServiceChecker.isConnected = true
        val status = AccessibilityGraceManager.checkAccessibilityState(mockContext)

        assertTrue(status.isInterrupted)
        assertEquals(AccessibilityGraceManager.AccessState.USAGE_ACCESS_MISSING, status.accessState)
        assertEquals(AccessibilityGraceManager.MISSING_TYPE_USAGE_ACCESS, status.missingAccessType)
        // Countdown preserved: 300s - 120s = 180s remaining
        assertEquals(180_000L, status.remainingGraceMs)
        assertEquals(t0 + 300_000L, status.graceDeadlineWallMs)
        assertFalse(testAlarmScheduler.cancelled)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 20: Full restoration resolves interruption and cancels notification
    // ────────────────────────────────────────────────────────────
    @Test
    fun test20_fullRestoration_resolvesInterruptionAndCancelsNotification() {
        testServiceChecker.isEnabled = true
        testServiceChecker.isConnected = true
        testServiceChecker.hasUsagePermission = false

        AccessibilityGraceManager.checkAccessibilityState(mockContext)
        assertFalse(testNotificationPoster.cancelled)

        testClock.advance(30_000L)

        // Restore Usage Access
        testServiceChecker.hasUsagePermission = true
        val status = AccessibilityGraceManager.checkAccessibilityState(mockContext)

        assertFalse(status.isInterrupted)
        assertEquals(AccessibilityGraceManager.AccessState.RESTORING, status.accessState)
        assertEquals("Monitoring restored", status.statusText)
        assertTrue(testNotificationPoster.cancelled)
        assertTrue(testAlarmScheduler.cancelled)

        // After 16 seconds, state settles to ACCESS_OK
        testClock.advance(16_000L)
        val settledStatus = AccessibilityGraceManager.getUiStatus(mockContext)
        assertEquals(AccessibilityGraceManager.AccessState.ACCESS_OK, settledStatus.accessState)
        assertEquals("Accessibility active", settledStatus.statusText)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 21: Rapid toggles OFF/ON/OFF preserve single authoritative episode
    // ────────────────────────────────────────────────────────────
    @Test
    fun test21_rapidToggles_preservesSingleAuthoritativeEpisode() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        testServiceChecker.hasUsagePermission = true
        val t0 = testClock.currentTimeMillis()

        val s1 = AccessibilityGraceManager.checkAccessibilityState(mockContext)
        val epId = s1.episodeId

        // Rapid checks within 2 seconds
        repeat(10) {
            testClock.advance(200L)
            val s = AccessibilityGraceManager.checkAccessibilityState(mockContext)
            assertEquals(epId, s.episodeId)
            assertTrue(s.isInterrupted)
        }

        // Countdown monotonically decreasing: 300_000 - 2000 = 298_000 ms
        val finalStatus = AccessibilityGraceManager.getUiStatus(mockContext)
        assertEquals(298_000L, finalStatus.remainingGraceMs)
        assertEquals(t0 + 300_000L, finalStatus.graceDeadlineWallMs)
        // Notification posted once at onset
        assertEquals(1, testNotificationPoster.postCount)
    }

    // ────────────────────────────────────────────────────────────
    // Scenario 22: Exact required examples matching specification table
    // ────────────────────────────────────────────────────────────
    @Test
    fun test22_userRequiredExamples_exactElapsedCountdownAndPenalties() {
        testServiceChecker.isEnabled = false
        testServiceChecker.isConnected = false
        val t0 = testClock.currentTimeMillis()

        AccessibilityGraceManager.checkAccessibilityState(mockContext)

        data class TestCase(val elapsedSec: Long, val expectedRemainingMs: Long, val expectedOverdueSec: Long, val expectedCumulativePenalty: Int)
        val cases = listOf(
            TestCase(299L, 1000L, 0L, 0),    // 4 min 59 sec -> 00:01 remaining, 0 pts
            TestCase(300L, 0L, 0L, 0),       // 5 min -> -00:00, 0 pts
            TestCase(330L, 0L, 30L, 0),      // 5 min 30 sec -> -00:30, 0 pts
            TestCase(360L, 0L, 60L, 1),      // 6 min -> -01:00, 1 point
            TestCase(405L, 0L, 105L, 1),     // 6 min 45 sec -> -01:45, 1 point
            TestCase(420L, 0L, 120L, 2),     // 7 min -> -02:00, 2 points
            TestCase(480L, 0L, 180L, 3),     // 8 min -> -03:00, 3 points
            TestCase(600L, 0L, 300L, 5)      // 10 min -> -05:00, 5 points
        )

        for (c in cases) {
            testClock.wallMs = t0 + c.elapsedSec * 1000L
            testClock.elapsedMs = 500_000L + c.elapsedSec * 1000L

            val recon = AccessibilityGraceManager.reconcileInterruption(mockContext)
            assertEquals("Elapsed ${c.elapsedSec}s cumulative penalty mismatch", c.expectedCumulativePenalty, recon.totalDeductedPoints)

            val ui = AccessibilityGraceManager.getUiStatus(mockContext)
            assertEquals("Elapsed ${c.elapsedSec}s remainingGraceMs mismatch", c.expectedRemainingMs, ui.remainingGraceMs)
            assertEquals("Elapsed ${c.elapsedSec}s overdueSeconds mismatch", c.expectedOverdueSec, ui.overdueSeconds)
            assertEquals("Elapsed ${c.elapsedSec}s recordedPenaltyPoints mismatch", c.expectedCumulativePenalty, ui.recordedPenaltyPoints)
        }
    }
}
