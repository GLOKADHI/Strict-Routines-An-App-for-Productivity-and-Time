package com.strictrecords.app

import android.graphics.Color
import org.junit.Assert.*
import org.junit.Test

class FloatingAccessibilityCountdownOverlayTest {

    @Test
    fun testCountdownFormattingAccurateIncludingOverdueNegative() {
        // Positive countdown phase
        assertEquals("05:00", FloatingAccessibilityCountdownOverlay.formatCountdown(300_000L))
        assertEquals("04:59", FloatingAccessibilityCountdownOverlay.formatCountdown(299_000L))
        assertEquals("04:58", FloatingAccessibilityCountdownOverlay.formatCountdown(298_000L))
        assertEquals("02:30", FloatingAccessibilityCountdownOverlay.formatCountdown(150_000L))
        assertEquals("01:00", FloatingAccessibilityCountdownOverlay.formatCountdown(60_000L))
        assertEquals("00:30", FloatingAccessibilityCountdownOverlay.formatCountdown(30_000L))
        assertEquals("00:01", FloatingAccessibilityCountdownOverlay.formatCountdown(1_000L))

        // Exact zero time
        assertEquals("00:00", FloatingAccessibilityCountdownOverlay.formatCountdown(0L))

        // Overdue negative phase (as required by specification)
        assertEquals("-00:01", FloatingAccessibilityCountdownOverlay.formatCountdown(-1_000L))
        assertEquals("-00:02", FloatingAccessibilityCountdownOverlay.formatCountdown(-2_000L))
        assertEquals("-00:30", FloatingAccessibilityCountdownOverlay.formatCountdown(-30_000L))
        assertEquals("-01:00", FloatingAccessibilityCountdownOverlay.formatCountdown(-60_000L))
        assertEquals("-05:00", FloatingAccessibilityCountdownOverlay.formatCountdown(-300_000L))
    }

    @Test
    fun testSubtextFormattingAccurate() {
        // Critical countdown subtext
        assertEquals("1s remaining", FloatingAccessibilityCountdownOverlay.formatSubtext(1_000L, FloatingAccessibilityCountdownOverlay.UrgencyState.CRITICAL))
        assertEquals("15s remaining", FloatingAccessibilityCountdownOverlay.formatSubtext(15_000L, FloatingAccessibilityCountdownOverlay.UrgencyState.CRITICAL))
        assertEquals("30s remaining", FloatingAccessibilityCountdownOverlay.formatSubtext(30_000L, FloatingAccessibilityCountdownOverlay.UrgencyState.CRITICAL))

        // Exactly zero time: TIME'S UP
        assertEquals("TIME'S UP", FloatingAccessibilityCountdownOverlay.formatSubtext(0L, FloatingAccessibilityCountdownOverlay.UrgencyState.COMPLETED))

        // Overdue seconds: Overdue (1s), Overdue (2s)
        assertEquals("Overdue (1s)", FloatingAccessibilityCountdownOverlay.formatSubtext(-1_000L, FloatingAccessibilityCountdownOverlay.UrgencyState.COMPLETED))
        assertEquals("Overdue (2s)", FloatingAccessibilityCountdownOverlay.formatSubtext(-2_000L, FloatingAccessibilityCountdownOverlay.UrgencyState.COMPLETED))
        assertEquals("Overdue (45s)", FloatingAccessibilityCountdownOverlay.formatSubtext(-45_000L, FloatingAccessibilityCountdownOverlay.UrgencyState.COMPLETED))

        // Overdue minutes: Overdue (1m), Overdue (5m)
        assertEquals("Overdue (1m)", FloatingAccessibilityCountdownOverlay.formatSubtext(-60_000L, FloatingAccessibilityCountdownOverlay.UrgencyState.COMPLETED))
        assertEquals("Overdue (5m)", FloatingAccessibilityCountdownOverlay.formatSubtext(-300_000L, FloatingAccessibilityCountdownOverlay.UrgencyState.COMPLETED))
    }

    @Test
    fun testUrgencyStateTransitions() {
        // Normal state: 5:00 to 2:01 remaining (> 120_000ms)
        assertEquals(
            FloatingAccessibilityCountdownOverlay.UrgencyState.NORMAL,
            FloatingAccessibilityCountdownOverlay.getUrgencyState(300_000L)
        )
        assertEquals(
            FloatingAccessibilityCountdownOverlay.UrgencyState.NORMAL,
            FloatingAccessibilityCountdownOverlay.getUrgencyState(250_000L)
        )
        assertEquals(
            FloatingAccessibilityCountdownOverlay.UrgencyState.NORMAL,
            FloatingAccessibilityCountdownOverlay.getUrgencyState(120_001L)
        )

        // Warning state: 2:00 to 0:31 remaining (30_001ms .. 120_000ms)
        assertEquals(
            FloatingAccessibilityCountdownOverlay.UrgencyState.WARNING,
            FloatingAccessibilityCountdownOverlay.getUrgencyState(120_000L)
        )
        assertEquals(
            FloatingAccessibilityCountdownOverlay.UrgencyState.WARNING,
            FloatingAccessibilityCountdownOverlay.getUrgencyState(90_000L)
        )
        assertEquals(
            FloatingAccessibilityCountdownOverlay.UrgencyState.WARNING,
            FloatingAccessibilityCountdownOverlay.getUrgencyState(30_001L)
        )

        // Critical state: 0:30 to 0:01 remaining (1ms .. 30_000ms)
        assertEquals(
            FloatingAccessibilityCountdownOverlay.UrgencyState.CRITICAL,
            FloatingAccessibilityCountdownOverlay.getUrgencyState(30_000L)
        )
        assertEquals(
            FloatingAccessibilityCountdownOverlay.UrgencyState.CRITICAL,
            FloatingAccessibilityCountdownOverlay.getUrgencyState(15_000L)
        )
        assertEquals(
            FloatingAccessibilityCountdownOverlay.UrgencyState.CRITICAL,
            FloatingAccessibilityCountdownOverlay.getUrgencyState(1_000L)
        )

        // Grace period completed & overdue: 0:00 and negative
        assertEquals(
            FloatingAccessibilityCountdownOverlay.UrgencyState.COMPLETED,
            FloatingAccessibilityCountdownOverlay.getUrgencyState(0L)
        )
        assertEquals(
            FloatingAccessibilityCountdownOverlay.UrgencyState.COMPLETED,
            FloatingAccessibilityCountdownOverlay.getUrgencyState(-1_000L)
        )
        assertEquals(
            FloatingAccessibilityCountdownOverlay.UrgencyState.COMPLETED,
            FloatingAccessibilityCountdownOverlay.getUrgencyState(-60_000L)
        )
        assertEquals(
            FloatingAccessibilityCountdownOverlay.UrgencyState.COMPLETED,
            FloatingAccessibilityCountdownOverlay.getUrgencyState(-300_000L)
        )
    }

    @Test
    fun testUrgencyColorsMatchDesignSpecification() {
        // Normal: Cyan/Blue (#00D2FF)
        assertEquals(
            Color.parseColor("#00D2FF"),
            FloatingAccessibilityCountdownOverlay.UrgencyState.NORMAL.borderColor
        )
        assertEquals(
            Color.parseColor("#00D2FF"),
            FloatingAccessibilityCountdownOverlay.UrgencyState.NORMAL.textColor
        )

        // Warning: Amber/Orange (#FF9500)
        assertEquals(
            Color.parseColor("#FF9500"),
            FloatingAccessibilityCountdownOverlay.UrgencyState.WARNING.borderColor
        )
        assertEquals(
            Color.parseColor("#FF9500"),
            FloatingAccessibilityCountdownOverlay.UrgencyState.WARNING.textColor
        )

        // Critical: Red (#FF3B30)
        assertEquals(
            Color.parseColor("#FF3B30"),
            FloatingAccessibilityCountdownOverlay.UrgencyState.CRITICAL.borderColor
        )
        assertEquals(
            Color.parseColor("#FF3B30"),
            FloatingAccessibilityCountdownOverlay.UrgencyState.CRITICAL.textColor
        )

        // Completed / Overdue: Red (#FF3B30)
        assertEquals(
            Color.parseColor("#FF3B30"),
            FloatingAccessibilityCountdownOverlay.UrgencyState.COMPLETED.borderColor
        )
        assertEquals(
            Color.parseColor("#FF3B30"),
            FloatingAccessibilityCountdownOverlay.UrgencyState.COMPLETED.textColor
        )
    }

    @Test
    fun testSettingsPackageExcludedToPreventCoveringToggle() {
        assertTrue(FloatingAccessibilityCountdownOverlay.isSettingsPackage("com.android.settings"))
        assertTrue(FloatingAccessibilityCountdownOverlay.isSettingsPackage("com.android.settings.accessibility"))
        assertTrue(FloatingAccessibilityCountdownOverlay.isSettingsPackage("com.google.android.settings"))
        assertTrue(FloatingAccessibilityCountdownOverlay.isSettingsPackage("com.samsung.android.settings"))
        assertTrue(FloatingAccessibilityCountdownOverlay.isSettingsPackage("com.miui.securitycenter"))

        assertFalse(FloatingAccessibilityCountdownOverlay.isSettingsPackage("com.google.android.youtube"))
        assertFalse(FloatingAccessibilityCountdownOverlay.isSettingsPackage("com.instagram.android"))
        assertFalse(FloatingAccessibilityCountdownOverlay.isSettingsPackage("com.sec.android.app.music"))
        assertFalse(FloatingAccessibilityCountdownOverlay.isSettingsPackage("com.strictrecords.app"))
        assertFalse(FloatingAccessibilityCountdownOverlay.isSettingsPackage("com.android.chrome"))
    }

    @Test
    fun testSystemExcludedPackages() {
        assertTrue(FloatingAccessibilityCountdownOverlay.isSystemExcluded("com.android.systemui"))
        assertTrue(FloatingAccessibilityCountdownOverlay.isSystemExcluded("com.android.phone"))
        assertTrue(FloatingAccessibilityCountdownOverlay.isSystemExcluded("com.google.android.dialer"))
        assertTrue(FloatingAccessibilityCountdownOverlay.isSystemExcluded("com.google.android.emergency"))

        assertFalse(FloatingAccessibilityCountdownOverlay.isSystemExcluded("com.sec.android.app.music"))
        assertFalse(FloatingAccessibilityCountdownOverlay.isSystemExcluded("com.google.android.youtube"))
        assertFalse(FloatingAccessibilityCountdownOverlay.isSystemExcluded("com.instagram.android"))
        assertFalse(FloatingAccessibilityCountdownOverlay.isSystemExcluded("com.android.chrome"))
    }

    @Test
    fun testPackageEligibility() {
        // Monitored apps (YouTube, Instagram, Samsung Music) and home are eligible
        assertTrue(FloatingAccessibilityCountdownOverlay.isPackageEligible(null, "com.sec.android.app.music"))
        assertTrue(FloatingAccessibilityCountdownOverlay.isPackageEligible(null, "com.google.android.youtube"))
        assertTrue(FloatingAccessibilityCountdownOverlay.isPackageEligible(null, "com.instagram.android"))
        assertTrue(FloatingAccessibilityCountdownOverlay.isPackageEligible(null, "com.android.chrome"))
        assertTrue(FloatingAccessibilityCountdownOverlay.isPackageEligible(null, ""))

        // Settings is NOT eligible (hidden so system toggle is not covered)
        assertFalse(FloatingAccessibilityCountdownOverlay.isPackageEligible(null, "com.android.settings"))

        // System emergency / dialer is NOT eligible
        assertFalse(FloatingAccessibilityCountdownOverlay.isPackageEligible(null, "com.google.android.emergency"))
        assertFalse(FloatingAccessibilityCountdownOverlay.isPackageEligible(null, "com.google.android.dialer"))
    }
}
