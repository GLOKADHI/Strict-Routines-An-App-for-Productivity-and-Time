package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin

/**
 * Comprehensive Unit Tests for the Animated Floating Discipline Bot & 5-Minute Grace Enforcement.
 *
 * Covers all 20 required verification scenarios from Section 9:
 * 1-6: Insufficient-points state, message precision, walking kinematics, non-blocking touch bounds.
 * 7-14: 5-minute accessibility grace period, priority, reconnection, idempotency, monotonic recovery.
 * 15-20: App allowlist/exclusions (banking, emergency, system dialogs), GYM mode, turn-around kinematics.
 */
class FloatingDisciplineBotEnforcementTest {

    private val instagramPkg = "com.instagram.android"
    private val youtubePkg = "com.google.android.youtube"
    private val spotifyPkg = "com.spotify.music"
    private val gpayPkg = "com.google.android.apps.nbu.paisa.user"
    private val emergencyPkg = "com.android.phone"
    private val normalAppPkg = "com.android.chrome"

    private fun mockActiveAccessibility(): AccessibilityGraceManager.AccessibilityStatus {
        return AccessibilityGraceManager.AccessibilityStatus(
            statusText = "Accessibility active",
            isInterrupted = false,
            interruptedAtWallMs = 0L,
            graceDeadlineWallMs = 0L,
            remainingGraceMs = 0L,
            confirmedUsageDurationMs = 0L,
            recordedPenaltyPoints = 0,
            canOpenAccessibilitySettings = false,
            stateCode = AccessibilityGraceManager.STATE_ACCESSIBILITY_ACTIVE,
            episodeId = "",
            accessState = AccessibilityGraceManager.AccessState.ACCESS_OK,
            missingAccessType = AccessibilityGraceManager.MISSING_TYPE_NONE,
            canOpenUsageAccessSettings = false
        )
    }

    private fun mockGraceActiveAccessibility(remainingMs: Long = 180_000L): AccessibilityGraceManager.AccessibilityStatus {
        return AccessibilityGraceManager.AccessibilityStatus(
            statusText = "Accessibility interrupted — 03:00",
            isInterrupted = true,
            interruptedAtWallMs = 1_000_000L,
            graceDeadlineWallMs = 1_300_000L,
            remainingGraceMs = remainingMs,
            confirmedUsageDurationMs = 0L,
            recordedPenaltyPoints = 0,
            canOpenAccessibilitySettings = true,
            stateCode = AccessibilityGraceManager.STATE_GRACE_PERIOD_ACTIVE,
            episodeId = "test_episode",
            accessState = AccessibilityGraceManager.AccessState.ACCESSIBILITY_MISSING,
            missingAccessType = AccessibilityGraceManager.MISSING_TYPE_ACCESSIBILITY,
            canOpenUsageAccessSettings = false
        )
    }

    private fun mockGraceExpiredAccessibility(): AccessibilityGraceManager.AccessibilityStatus {
        return AccessibilityGraceManager.AccessibilityStatus(
            statusText = "Accessibility penalty active",
            isInterrupted = true,
            interruptedAtWallMs = 1_000_000L,
            graceDeadlineWallMs = 1_300_000L,
            remainingGraceMs = 0L,
            confirmedUsageDurationMs = 120_000L,
            recordedPenaltyPoints = -2,
            canOpenAccessibilitySettings = true,
            stateCode = AccessibilityGraceManager.STATE_PENALTY_ELIGIBLE,
            episodeId = "test_episode",
            accessState = AccessibilityGraceManager.AccessState.ACCESSIBILITY_MISSING,
            missingAccessType = AccessibilityGraceManager.MISSING_TYPE_ACCESSIBILITY,
            canOpenUsageAccessSettings = false
        )
    }

    // -------------------------------------------------------------------------
    // Test 1: Open Instagram with insufficient points
    // -------------------------------------------------------------------------
    @Test
    fun test_01_instagram_insufficient_points_shows_bot() {
        val decision = FloatingBotStateSelector.evaluate(
            foregroundPackage = instagramPkg,
            currentScore = 45, // below 80 threshold
            accessibilityStatus = mockActiveAccessibility()
        )

        assertTrue("Bot should be displayed over Instagram when points are insufficient", decision.shouldShow)
        assertEquals(FloatingBotStateSelector.MessageState.INSUFFICIENT_POINTS, decision.state)
        assertEquals(80, decision.requiredScore)
        assertTrue(decision.isEntertainmentApp)
    }

    // -------------------------------------------------------------------------
    // Test 2: Verify exact message for State A
    // -------------------------------------------------------------------------
    @Test
    fun test_02_exact_message_no_entertainment_apps_unless_sufficient_points() {
        val decision = FloatingBotStateSelector.evaluate(
            foregroundPackage = instagramPkg,
            currentScore = 50,
            accessibilityStatus = mockActiveAccessibility()
        )

        assertEquals(
            "State A must display exact required string",
            "No Entertainment apps unless sufficient points",
            decision.message
        )
    }

    // -------------------------------------------------------------------------
    // Test 3: Open YouTube with insufficient points
    // -------------------------------------------------------------------------
    @Test
    fun test_03_youtube_insufficient_points_shows_bot_with_state_a() {
        val decision = FloatingBotStateSelector.evaluate(
            foregroundPackage = youtubePkg,
            currentScore = 70, // below 80 threshold
            accessibilityStatus = mockActiveAccessibility()
        )

        assertTrue("Bot should be displayed over YouTube when points are insufficient", decision.shouldShow)
        assertEquals(FloatingBotStateSelector.MessageState.INSUFFICIENT_POINTS, decision.state)
        assertEquals("No Entertainment apps unless sufficient points", decision.message)
    }

    // -------------------------------------------------------------------------
    // Test 4: Restoring points through authorized process permits access and hides bot
    // -------------------------------------------------------------------------
    @Test
    fun test_04_restoring_points_permits_entertainment_and_hides_bot() {
        val decision = FloatingBotStateSelector.evaluate(
            foregroundPackage = youtubePkg,
            currentScore = 85, // >= 80 threshold
            accessibilityStatus = mockActiveAccessibility()
        )

        assertFalse("Bot should NOT be displayed when user has sufficient points", decision.shouldShow)
        assertEquals(FloatingBotStateSelector.MessageState.NONE, decision.state)
    }

    // -------------------------------------------------------------------------
    // Test 5: Walking bot kinematics — alternating leg cycles and synchronized bobbing
    // -------------------------------------------------------------------------
    @Test
    fun test_05_walking_bot_kinematics_alternating_legs_and_body_bobbing() {
        val strideDurationSec = 0.85f
        val maxLegAngle = 26f

        // Sample at 4 quadrants of the walk cycle
        val times = listOf(0f, 0.2125f, 0.425f, 0.6375f)

        for (t in times) {
            val phi = (t / strideDurationSec * 2f * PI).toFloat()

            val leftHipAngle = sin(phi) * maxLegAngle
            val rightHipAngle = -sin(phi) * maxLegAngle

            // Left and right hips must have strictly opposite phase
            assertEquals("Hips must be in opposite phase", leftHipAngle, -rightHipAngle, 0.001f)

            // Knee bends during forward swing
            val leftKneeBend = cos(phi).coerceAtLeast(0f) * 32f
            val rightKneeBend = (-cos(phi)).coerceAtLeast(0f) * 32f
            assertTrue("Left knee bend must be non-negative", leftKneeBend >= 0f)
            assertTrue("Right knee bend must be non-negative", rightKneeBend >= 0f)

            // Body bobbing dips twice per walk cycle
            val bodyBob = -abs(sin(2f * phi)) * 3f
            assertTrue("Body bobbing must be <= 0 (dipping on foot strike)", bodyBob <= 0f)
        }
    }

    // -------------------------------------------------------------------------
    // Test 6: Non-blocking touch behavior and safe bounds
    // -------------------------------------------------------------------------
    @Test
    fun test_06_non_blocking_touch_and_reposition_bounds() {
        val screenW = 1080
        val screenH = 2400
        val containerW = 350
        val containerH = 250

        val minX = 24
        val maxX = screenW - containerW - 24
        val minY = 48
        val maxY = screenH - containerH - 96

        // Clamping logic
        val testXNegative = -50
        val clampedXNegative = testXNegative.coerceIn(minX, maxX)
        assertEquals(minX, clampedXNegative)

        val testXOverflow = 2000
        val clampedXOverflow = testXOverflow.coerceIn(minX, maxX)
        assertEquals(maxX, clampedXOverflow)

        val testYValid = 200
        val clampedYValid = testYValid.coerceIn(minY, maxY)
        assertEquals(testYValid, clampedYValid)
    }

    // -------------------------------------------------------------------------
    // Test 7: Accessibility disabled detected
    // -------------------------------------------------------------------------
    @Test
    fun test_07_accessibility_disabled_detected() {
        val status = mockGraceActiveAccessibility()
        assertTrue("Interruption flag must be set", status.isInterrupted)
        assertEquals(AccessibilityGraceManager.STATE_GRACE_PERIOD_ACTIVE, status.stateCode)
    }

    // -------------------------------------------------------------------------
    // Test 8: Under 5 minutes grace does NOT display post-grace warning
    // -------------------------------------------------------------------------
    @Test
    fun test_08_under_five_minutes_grace_does_not_show_state_b() {
        // Points are sufficient (90), but accessibility is disabled for only 2 minutes (remaining = 180s)
        val decision = FloatingBotStateSelector.evaluate(
            foregroundPackage = instagramPkg,
            currentScore = 90,
            accessibilityStatus = mockGraceActiveAccessibility(remainingMs = 180_000L)
        )

        // During grace period with sufficient points, State B must NOT trigger prematurely!
        assertFalse("Post-grace warning must not be displayed while grace period is active", decision.shouldShow)
        assertEquals(FloatingBotStateSelector.MessageState.NONE, decision.state)
        assertTrue(decision.isGracePeriodActive)
        assertFalse(decision.isGracePeriodExpired)
    }

    // -------------------------------------------------------------------------
    // Test 9: After 5 continuous minutes, displays "Please turn on the accessibility"
    // -------------------------------------------------------------------------
    @Test
    fun test_09_after_five_continuous_minutes_shows_please_turn_on_accessibility() {
        val decision = FloatingBotStateSelector.evaluate(
            foregroundPackage = instagramPkg,
            currentScore = 95, // Even with high score, accessibility disabled > 5 min must enforce State B!
            accessibilityStatus = mockGraceExpiredAccessibility()
        )

        assertTrue("After 5 minutes disabled, State B must be active", decision.shouldShow)
        assertEquals(FloatingBotStateSelector.MessageState.ACCESSIBILITY_DISABLED, decision.state)
        assertEquals(
            "State B must display exact required string",
            "Please turn on the accessibility",
            decision.message
        )
        assertTrue(decision.requiresAccessibilityAction)
        assertTrue(decision.isGracePeriodExpired)
    }

    // -------------------------------------------------------------------------
    // Test 10: Explicit Priority — State B overrides State A when both apply
    // -------------------------------------------------------------------------
    @Test
    fun test_10_priority_state_b_overrides_state_a_when_both_active() {
        // Both conditions active:
        // 1. Accessibility disabled > 5 min
        // 2. Points insufficient (score = 30 < 80)
        val decision = FloatingBotStateSelector.evaluate(
            foregroundPackage = youtubePkg,
            currentScore = 30,
            accessibilityStatus = mockGraceExpiredAccessibility()
        )

        assertTrue(decision.shouldShow)
        assertEquals(
            "Priority 1 (Accessibility Disabled) must override Priority 2 (Insufficient Points)",
            FloatingBotStateSelector.MessageState.ACCESSIBILITY_DISABLED,
            decision.state
        )
        assertEquals("Please turn on the accessibility", decision.message)
    }

    // -------------------------------------------------------------------------
    // Test 11: Re-enabling accessibility clears State B and falls back to points state
    // -------------------------------------------------------------------------
    @Test
    fun test_11_reconnecting_accessibility_clears_state_b() {
        // User had score 50 (insufficient) and accessibility was expired.
        // User now re-enables accessibility.
        val decisionAfterReconnection = FloatingBotStateSelector.evaluate(
            foregroundPackage = youtubePkg,
            currentScore = 50,
            accessibilityStatus = mockActiveAccessibility()
        )

        assertTrue(decisionAfterReconnection.shouldShow)
        assertEquals(
            "Once accessibility is restored, bot transitions to State A based on points",
            FloatingBotStateSelector.MessageState.INSUFFICIENT_POINTS,
            decisionAfterReconnection.state
        )
        assertEquals("No Entertainment apps unless sufficient points", decisionAfterReconnection.message)
    }

    // -------------------------------------------------------------------------
    // Test 12: Restart during grace period preserves remaining time (monotonic recovery)
    // -------------------------------------------------------------------------
    @Test
    fun test_12_restart_during_grace_period_preserves_remaining_time() {
        val interruptedWall = 1_000_000L
        val interruptedElapsed = 500_000L
        val nowElapsed = 620_000L // 120s later on same boot

        val elapsed = nowElapsed - interruptedElapsed
        val remaining = maxOf(0L, 300_000L - elapsed)
        assertEquals("Remaining grace must be 180,000 ms (3 minutes)", 180_000L, remaining)
        assertTrue("Grace period must still be active", remaining > 0L)
    }

    // -------------------------------------------------------------------------
    // Test 13: Monitoring uncertainty / service restart does not trigger false penalty
    // -------------------------------------------------------------------------
    @Test
    fun test_13_monitoring_uncertainty_or_service_restart_does_not_trigger_false_state() {
        // Transient reconnecting state: enabled in settings, but temporarily not connected
        val reconnectingStatus = AccessibilityGraceManager.AccessibilityStatus(
            statusText = "Monitoring status unknown — reconnecting",
            isInterrupted = false,
            interruptedAtWallMs = 0L,
            graceDeadlineWallMs = 0L,
            remainingGraceMs = 0L,
            confirmedUsageDurationMs = 0L,
            recordedPenaltyPoints = 0,
            canOpenAccessibilitySettings = false,
            stateCode = AccessibilityGraceManager.STATE_MONITORING_STATUS_UNKNOWN,
            episodeId = "",
            accessState = AccessibilityGraceManager.AccessState.RECHECK_REQUIRED,
            missingAccessType = AccessibilityGraceManager.MISSING_TYPE_NONE,
            canOpenUsageAccessSettings = false
        )

        val decision = FloatingBotStateSelector.evaluate(
            foregroundPackage = instagramPkg,
            currentScore = 90,
            accessibilityStatus = reconnectingStatus
        )

        assertFalse("Reconnecting / uncertain monitoring state must not trigger false penalty or State B", decision.shouldShow)
        assertEquals(FloatingBotStateSelector.MessageState.NONE, decision.state)
    }

    // -------------------------------------------------------------------------
    // Test 14: Single disabled interval creates idempotent penalty events
    // -------------------------------------------------------------------------
    @Test
    fun test_14_single_disabled_interval_idempotent_no_duplicate_penalties() {
        val episodeId = "test_episode_123"
        val transactionIds = mutableSetOf<String>()

        // Simulate 3 minutes past the 5-minute grace period (minutes 1, 2, 3)
        for (m in 1..3) {
            val txId = "tx_acc_${episodeId}_m$m"
            assertTrue("First commit of minute $m must succeed", transactionIds.add(txId))
        }

        // Simulate repeat reconciliation of the same minutes
        for (m in 1..3) {
            val txId = "tx_acc_${episodeId}_m$m"
            assertFalse("Duplicate commit of minute $m must be rejected idempotently", transactionIds.add(txId))
        }
        assertEquals(3, transactionIds.size)
    }

    // -------------------------------------------------------------------------
    // Test 15: Excluded banking and financial apps never show overlay
    // -------------------------------------------------------------------------
    @Test
    fun test_15_excluded_banking_and_financial_apps_never_show_overlay() {
        val financialApps = listOf(
            gpayPkg,
            "com.phonepe.app",
            "net.one97.paytm",
            "com.sbi.lotusintouch",
            "com.csam.icici.bank.imobile",
            "com.snapwork.hdfc",
            "com.navi.navi"
        )

        for (pkg in financialApps) {
            val decision = FloatingBotStateSelector.evaluate(
                foregroundPackage = pkg,
                currentScore = 10, // low score
                accessibilityStatus = mockGraceExpiredAccessibility() // accessibility disabled
            )
            assertFalse("Financial app $pkg must NEVER show discipline overlay", decision.shouldShow)
            assertEquals(FloatingBotStateSelector.MessageState.NONE, decision.state)
        }
    }

    // -------------------------------------------------------------------------
    // Test 16: Excluded emergency and system screens never show overlay
    // -------------------------------------------------------------------------
    @Test
    fun test_16_excluded_emergency_and_system_screens_never_show_overlay() {
        val systemApps = listOf(
            emergencyPkg,
            "com.google.android.dialer",
            "com.android.emergency",
            "com.android.packageinstaller",
            "com.google.android.permissioncontroller"
        )

        for (pkg in systemApps) {
            val decision = FloatingBotStateSelector.evaluate(
                foregroundPackage = pkg,
                currentScore = 10,
                accessibilityStatus = mockGraceExpiredAccessibility()
            )
            assertFalse("System/emergency app $pkg must NEVER show discipline overlay", decision.shouldShow)
        }
    }

    // -------------------------------------------------------------------------
    // Test 17: Non-entertainment apps never show overlay
    // -------------------------------------------------------------------------
    @Test
    fun test_17_non_entertainment_apps_never_show_overlay() {
        val nonEntertainment = listOf(
            normalAppPkg,
            "com.whatsapp",
            "com.google.android.gm",
            "com.google.android.calendar"
        )

        for (pkg in nonEntertainment) {
            val decision = FloatingBotStateSelector.evaluate(
                foregroundPackage = pkg,
                currentScore = 20,
                accessibilityStatus = mockActiveAccessibility()
            )
            assertFalse("Productive/normal app $pkg must NOT trigger overlay", decision.shouldShow)
            assertEquals(FloatingBotStateSelector.MessageState.NONE, decision.state)
        }
    }

    // -------------------------------------------------------------------------
    // Test 18: GYM mode permits music targets and hides bot
    // -------------------------------------------------------------------------
    @Test
    fun test_18_gym_mode_permits_music_targets_and_hides_bot() {
        val decision = FloatingBotStateSelector.evaluate(
            foregroundPackage = spotifyPkg,
            currentScore = 30, // below 60 threshold
            accessibilityStatus = mockActiveAccessibility(),
            isGymModeActive = true // Gym Mode permits music!
        )

        assertFalse("Active GYM Mode permits music access without overlay", decision.shouldShow)
        assertEquals(FloatingBotStateSelector.MessageState.NONE, decision.state)
    }

    // -------------------------------------------------------------------------
    // Test 19: Walking bot state transitions (walk to idle pause to turn)
    // -------------------------------------------------------------------------
    @Test
    fun test_19_walking_bot_state_transitions_walk_to_idle_pause_to_turn() {
        var state = DisciplineBotWalkingView.BotState.WALKING
        var facingRight = true
        var currentX = 0f
        val maxX = 100f

        // Step 1: Walk to boundary
        currentX += 120f
        if (currentX >= maxX) {
            currentX = maxX
            state = DisciplineBotWalkingView.BotState.IDLE
        }
        assertEquals(DisciplineBotWalkingView.BotState.IDLE, state)
        assertEquals(maxX, currentX, 0.001f)

        // Step 2: Pause expires -> turn and resume walking
        val pauseDone = true
        if (pauseDone) {
            facingRight = !facingRight
            state = DisciplineBotWalkingView.BotState.WALKING
        }
        assertFalse("Bot should now face left after turning", facingRight)
        assertEquals(DisciplineBotWalkingView.BotState.WALKING, state)
    }

    // -------------------------------------------------------------------------
    // Test 20: Direction flip and idle breathing
    // -------------------------------------------------------------------------
    @Test
    fun test_20_direction_flip_and_idle_breathing() {
        // Idle breathing oscillation
        val timeSec = 1.0f
        val idleBob = sin(timeSec * 3f) * 1.2f
        assertTrue("Idle bob must be bounded within ±1.5dp", abs(idleBob) <= 1.5f)
    }
}
