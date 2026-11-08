package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test
import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

class GymModeRulesTest {

    private val tz = "Asia/Kolkata"

    private fun makeTimestamp(year: Int, month: Int, day: Int, hour: Int, minute: Int, second: Int = 0): Long {
        val cal = Calendar.getInstance(TimeZone.getTimeZone(tz), Locale.US).apply {
            set(Calendar.YEAR, year)
            set(Calendar.MONTH, month - 1)
            set(Calendar.DAY_OF_MONTH, day)
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, second)
            set(Calendar.MILLISECOND, 0)
        }
        return cal.timeInMillis
    }

    // ── 1. Activation Window Boundary Tests ──

    @Test
    fun testActivationWindow_at459PM_isRejected() {
        // 4:59 PM (16:59)
        val timeMs = makeTimestamp(2026, 10, 19, 16, 59)
        assertFalse("4:59 PM should be rejected", GymModeRules.isWithinActivationWindow(timeMs, tz))

        val (canAct, reason) = GymModeRules.canActivate(
            nowMs = timeMs,
            livesBalance = 3,
            hasActiveSession = false,
            gymActivityId = "ACT-20261019-GYM",
            previouslyActivatedGymIds = emptySet(),
            timezone = tz
        )
        assertFalse(canAct)
        assertTrue(reason.contains("5:00 PM"))
    }

    @Test
    fun testActivationWindow_at500PM_isAllowed() {
        // 5:00 PM (17:00)
        val timeMs = makeTimestamp(2026, 10, 19, 17, 0)
        assertTrue("5:00 PM should be allowed", GymModeRules.isWithinActivationWindow(timeMs, tz))

        val (canAct, reason) = GymModeRules.canActivate(
            nowMs = timeMs,
            livesBalance = 3,
            hasActiveSession = false,
            gymActivityId = "ACT-20261019-GYM",
            previouslyActivatedGymIds = emptySet(),
            timezone = tz
        )
        assertTrue(canAct)
        assertEquals("OK", reason)
    }

    @Test
    fun testActivationWindow_at600PM_isAllowed() {
        // 6:00 PM (18:00)
        val timeMs = makeTimestamp(2026, 10, 19, 18, 0)
        assertTrue("6:00 PM should be allowed", GymModeRules.isWithinActivationWindow(timeMs, tz))

        val (canAct, _) = GymModeRules.canActivate(
            nowMs = timeMs,
            livesBalance = 3,
            hasActiveSession = false,
            gymActivityId = "ACT-20261019-GYM",
            previouslyActivatedGymIds = emptySet(),
            timezone = tz
        )
        assertTrue(canAct)
    }

    @Test
    fun testActivationWindow_at829PM_isAllowed() {
        // 8:29 PM (20:29)
        val timeMs = makeTimestamp(2026, 10, 19, 20, 29)
        assertTrue("8:29 PM should be allowed", GymModeRules.isWithinActivationWindow(timeMs, tz))

        val (canAct, _) = GymModeRules.canActivate(
            nowMs = timeMs,
            livesBalance = 3,
            hasActiveSession = false,
            gymActivityId = "ACT-20261019-GYM",
            previouslyActivatedGymIds = emptySet(),
            timezone = tz
        )
        assertTrue(canAct)
    }

    @Test
    fun testActivationWindow_at830PM_isRejected() {
        // 8:30 PM (20:30) - exact cutoff
        val timeMs = makeTimestamp(2026, 10, 19, 20, 30)
        assertFalse("8:30 PM should be rejected", GymModeRules.isWithinActivationWindow(timeMs, tz))

        val (canAct, reason) = GymModeRules.canActivate(
            nowMs = timeMs,
            livesBalance = 3,
            hasActiveSession = false,
            gymActivityId = "ACT-20261019-GYM",
            previouslyActivatedGymIds = emptySet(),
            timezone = tz
        )
        assertFalse(canAct)
        assertTrue(reason.contains("closed for today at 8:30 PM"))
    }

    @Test
    fun testActivationWindow_at900PM_isRejected() {
        // 9:00 PM (21:00)
        val timeMs = makeTimestamp(2026, 10, 19, 21, 0)
        assertFalse("9:00 PM should be rejected", GymModeRules.isWithinActivationWindow(timeMs, tz))

        val (canAct, _) = GymModeRules.canActivate(
            nowMs = timeMs,
            livesBalance = 3,
            hasActiveSession = false,
            gymActivityId = "ACT-20261019-GYM",
            previouslyActivatedGymIds = emptySet(),
            timezone = tz
        )
        assertFalse(canAct)
    }

    // ── 2. Access Duration & Cutoff Calculations ──

    @Test
    fun testDurationCalculation_at500PM_expiresAt830PM() {
        val actMs = makeTimestamp(2026, 10, 19, 17, 0)
        val expiryMs = GymModeRules.calculateAccessExpiry(actMs, tz)
        val expectedMs = makeTimestamp(2026, 10, 19, 20, 30)
        assertEquals(expectedMs, expiryMs)
    }

    @Test
    fun testDurationCalculation_at530PM_expiresAt830PM() {
        val actMs = makeTimestamp(2026, 10, 19, 17, 30)
        val expiryMs = GymModeRules.calculateAccessExpiry(actMs, tz)
        val expectedMs = makeTimestamp(2026, 10, 19, 20, 30)
        assertEquals(expectedMs, expiryMs)
    }

    @Test
    fun testDurationCalculation_at700PM_expiresAt830PM_dueToCutoff() {
        // 7:00 PM + 2 hours = 9:00 PM, but capped at 8:30 PM
        val actMs = makeTimestamp(2026, 10, 19, 19, 0)
        val expiryMs = GymModeRules.calculateAccessExpiry(actMs, tz)
        val expectedCutoffMs = makeTimestamp(2026, 10, 19, 20, 30)
        assertEquals(expectedCutoffMs, expiryMs)
    }

    @Test
    fun testDurationCalculation_at800PM_expiresAt830PM_30MinutesMax() {
        val actMs = makeTimestamp(2026, 10, 19, 20, 0)
        val expiryMs = GymModeRules.calculateAccessExpiry(actMs, tz)
        val expectedCutoffMs = makeTimestamp(2026, 10, 19, 20, 30)
        assertEquals(expectedCutoffMs, expiryMs)
    }

    @Test
    fun testDurationCalculation_at829PM_expiresAt830PM_1MinuteMax() {
        val actMs = makeTimestamp(2026, 10, 19, 20, 29)
        val expiryMs = GymModeRules.calculateAccessExpiry(actMs, tz)
        val expectedCutoffMs = makeTimestamp(2026, 10, 19, 20, 30)
        assertEquals(expectedCutoffMs, expiryMs)
        assertEquals(60_000L, expiryMs - actMs)
    }

    // ── 3. Application Scope Tests ──

    @Test
    fun testApplicationScope_allowsSamsungMusicAndSpotify() {
        assertTrue(GymModeRules.isTargetAllowedUnderGymMode("com.sec.android.app.music", true))
        assertTrue(GymModeRules.isTargetAllowedUnderGymMode("com.spotify.music", true))
        assertTrue(GymModeRules.isTargetAllowedUnderGymMode("com.spotify.lite", true))
        assertTrue(GymModeRules.isTargetAllowedUnderGymMode("spotify", true))
        assertTrue(GymModeRules.isTargetAllowedUnderGymMode("samsung_music", true))
        assertTrue(GymModeRules.isTargetAllowedUnderGymMode("Samsung Music", true))
        assertTrue(GymModeRules.isTargetAllowedUnderGymMode("Spotify", true))
    }

    @Test
    fun testApplicationScope_strictlyBlocksOtherApps() {
        assertFalse(GymModeRules.isTargetAllowedUnderGymMode("com.google.android.youtube", true))
        assertFalse(GymModeRules.isTargetAllowedUnderGymMode("youtube", true))
        assertFalse(GymModeRules.isTargetAllowedUnderGymMode("YouTube", true))
        assertFalse(GymModeRules.isTargetAllowedUnderGymMode("com.instagram.android", true))
        assertFalse(GymModeRules.isTargetAllowedUnderGymMode("instagram", true))
        assertFalse(GymModeRules.isTargetAllowedUnderGymMode("Instagram", true))
        assertFalse(GymModeRules.isTargetAllowedUnderGymMode("com.netflix.mediaclient", true))
        assertFalse(GymModeRules.isTargetAllowedUnderGymMode("netflix", true))
    }

    @Test
    fun testApplicationScope_whenGymModeInactive_allowsNothing() {
        assertFalse(GymModeRules.isTargetAllowedUnderGymMode("com.sec.android.app.music", false))
        assertFalse(GymModeRules.isTargetAllowedUnderGymMode("com.spotify.music", false))
        assertFalse(GymModeRules.isTargetAllowedUnderGymMode("spotify", false))
    }

    // ── 4. Early Deactivation Tests ──

    @Test
    fun testEarlyDeactivation_revokesAccessImmediately() {
        val actMs = makeTimestamp(2026, 10, 19, 16, 0)
        val expiryMs = makeTimestamp(2026, 10, 19, 18, 0)
        val session = GymModeRules.GymModeSession(
            sessionId = "gym_session_1",
            gymActivityId = "ACT-20261019-GYM",
            activationTimeMs = actMs,
            accessExpiryMs = expiryMs,
            proofDeadlineMs = makeTimestamp(2026, 10, 19, 18, 30),
            status = "ACTIVE",
            toggledOffEarly = false
        )

        // Active at 4:30 PM
        val midTimeMs = makeTimestamp(2026, 10, 19, 16, 30)
        assertTrue(GymModeRules.isMusicAccessActive(midTimeMs, session))

        // Toggled off early at 4:45 PM
        val toggledOffSession = session.copy(
            toggledOffEarly = true,
            toggledOffAtMs = makeTimestamp(2026, 10, 19, 16, 45)
        )
        assertFalse(GymModeRules.isMusicAccessActive(midTimeMs, toggledOffSession))
        assertFalse(toggledOffSession.isMusicAccessActive)
    }

    // ── 5. Automatic Expiration Tests ──

    @Test
    fun testAutomaticExpiration_locksAtExpiry() {
        val actMs = makeTimestamp(2026, 10, 19, 16, 0)
        val expiryMs = makeTimestamp(2026, 10, 19, 18, 0)
        val session = GymModeRules.GymModeSession(
            sessionId = "gym_session_1",
            gymActivityId = "ACT-20261019-GYM",
            activationTimeMs = actMs,
            accessExpiryMs = expiryMs,
            proofDeadlineMs = makeTimestamp(2026, 10, 19, 18, 30),
            status = "ACTIVE"
        )

        // 1 second before expiry: active
        assertTrue(GymModeRules.isMusicAccessActive(expiryMs - 1000L, session))
        // Exactly at expiry: inactive
        assertFalse(GymModeRules.isMusicAccessActive(expiryMs, session))
        // 1 second after expiry: inactive
        assertFalse(GymModeRules.isMusicAccessActive(expiryMs + 1000L, session))
    }

    // ── 6. Persistence & Restart Recovery ──

    @Test
    fun testPersistenceAndRestartRecovery_jsonSerialization() {
        val session = GymModeRules.GymModeSession(
            sessionId = "sess_12345",
            gymActivityId = "ACT-20261019-GYM",
            activationTimeMs = 1792410000000L,
            accessExpiryMs = 1792417200000L,
            proofDeadlineMs = 1792419000000L,
            status = "ACTIVE",
            toggledOffEarly = false
        )

        val json = session.toJson()
        val restored = GymModeRules.GymModeSession.fromJson(json)

        assertEquals(session.sessionId, restored.sessionId)
        assertEquals(session.gymActivityId, restored.gymActivityId)
        assertEquals(session.activationTimeMs, restored.activationTimeMs)
        assertEquals(session.accessExpiryMs, restored.accessExpiryMs)
        assertEquals(session.proofDeadlineMs, restored.proofDeadlineMs)
        assertEquals(session.status, restored.status)
        assertEquals(session.toggledOffEarly, restored.toggledOffEarly)
    }

    // ── 7. Three-Life System & Badge Formatting ──

    @Test
    fun testFormatLivesBadge() {
        assertEquals("♥ ♥ ♥ — 3/3 lives", GymModeRules.formatLivesBadge(3))
        assertEquals("♥ ♥ ♡ — 2/3 lives", GymModeRules.formatLivesBadge(2))
        assertEquals("♥ ♡ ♡ — 1/3 lives", GymModeRules.formatLivesBadge(1))
        assertEquals("♡ ♡ ♡ — 0/3 lives", GymModeRules.formatLivesBadge(0))
    }

    @Test
    fun testZeroLives_disablesActivation() {
        val nowMs = makeTimestamp(2026, 10, 19, 17, 0)
        val (canAct, reason) = GymModeRules.canActivate(
            nowMs = nowMs,
            livesBalance = 0,
            hasActiveSession = false,
            gymActivityId = "ACT-20261019-GYM",
            previouslyActivatedGymIds = emptySet(),
            timezone = tz
        )
        assertFalse("Activation must be disabled when 0 lives remain", canAct)
        assertTrue(reason.contains("No lives remaining"))
    }

    // ── 8. Single Deduction Guarantee ──

    @Test
    fun testSingleDeductionGuarantee() {
        val gymActId = "ACT-20261019-GYM"

        // First missed proof evaluation -> should deduct
        val (deduct1, _) = GymModeRules.shouldDeductLife(
            gymActivityId = gymActId,
            isFakeOrManipulated = false,
            isProofDeadlineExpiredWithoutSubmission = true,
            alreadyDeductedGymIds = emptySet(),
            alreadyFulfilledGymIds = emptySet()
        )
        assertTrue("First missed deadline must deduct life", deduct1)

        // Subsequent check for same gym session -> must NOT deduct again
        val (deduct2, reason2) = GymModeRules.shouldDeductLife(
            gymActivityId = gymActId,
            isFakeOrManipulated = false,
            isProofDeadlineExpiredWithoutSubmission = true,
            alreadyDeductedGymIds = setOf(gymActId),
            alreadyFulfilledGymIds = emptySet()
        )
        assertFalse("Second check for same session must NOT deduct life", deduct2)
        assertTrue(reason2.contains("single deduction guarantee"))
    }

    @Test
    fun testSingleDeduction_whenProofAlreadyFulfilled_neverDeducts() {
        val gymActId = "ACT-20261019-GYM"
        val (deduct, reason) = GymModeRules.shouldDeductLife(
            gymActivityId = gymActId,
            isFakeOrManipulated = false,
            isProofDeadlineExpiredWithoutSubmission = true,
            alreadyDeductedGymIds = emptySet(),
            alreadyFulfilledGymIds = setOf(gymActId)
        )
        assertFalse(deduct)
        assertTrue(reason.contains("already fulfilled"))
    }

    // ── 9. Session Reuse Prevention ──

    @Test
    fun testSessionReusePrevention_cannotReuseSameGymSession() {
        val nowMs = makeTimestamp(2026, 10, 19, 17, 0)
        val (canAct, reason) = GymModeRules.canActivate(
            nowMs = nowMs,
            livesBalance = 3,
            hasActiveSession = false,
            gymActivityId = "ACT-20261019-GYM",
            previouslyActivatedGymIds = setOf("ACT-20261019-GYM"),
            timezone = tz
        )
        assertFalse("Same Gym session cannot be reused for multiple activations", canAct)
        assertTrue(reason.contains("already been used"))
    }

    // ── 10. Weekly Reset & Clock Manipulation Protection ──

    @Test
    fun testWeeklyReset_detectsNewWeekOnMonday() {
        // Monday Oct 19, 2026 00:01
        val mondayTimeMs = makeTimestamp(2026, 10, 19, 0, 1)
        val lastWeekKey = "2026-W42"
        val currentWeekKey = GymModeRules.getIsoWeekKey(mondayTimeMs, tz)

        // If last week was 2026-W42 and current is 2026-W43
        val shouldReset = GymModeRules.shouldPerformWeeklyReset(
            lastResetWeekKey = "2026-W42",
            lastResetTimestampMs = makeTimestamp(2026, 10, 12, 0, 0),
            nowMs = mondayTimeMs,
            timezone = tz
        )
        assertTrue("Should perform weekly reset on Monday of new week", shouldReset)
    }

    @Test
    fun testWeeklyReset_clockRollbackProtection() {
        val lastResetTimeMs = makeTimestamp(2026, 10, 19, 0, 0)
        // Clock manipulated backwards to Oct 15
        val manipulatedTimeMs = makeTimestamp(2026, 10, 15, 12, 0)

        val shouldReset = GymModeRules.shouldPerformWeeklyReset(
            lastResetWeekKey = "2026-W41",
            lastResetTimestampMs = lastResetTimeMs,
            nowMs = manipulatedTimeMs,
            timezone = tz
        )
        assertFalse("Backward clock change must refuse reset", shouldReset)
    }
}
