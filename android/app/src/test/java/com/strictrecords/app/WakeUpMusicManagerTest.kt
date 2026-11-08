package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test
import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

class WakeUpMusicManagerTest {

    private val kolkataTz = TimeZone.getTimeZone("Asia/Kolkata")

    private fun makeCal(year: Int, month0: Int, day: Int, hour: Int = 12, min: Int = 0): Calendar {
        return Calendar.getInstance(kolkataTz).apply {
            set(year, month0, day, hour, min, 0)
            set(Calendar.MILLISECOND, 0)
        }
    }

    @Test
    fun testOctober16LocksUntilNovember1() {
        // 16 October 2026 15:30:00 IST
        val cal = makeCal(2026, Calendar.OCTOBER, 16, 15, 30)
        val lockUntilMs = WakeUpMusicManager.calculateNextMonthLockUntilMs(cal.timeInMillis, kolkataTz)

        val targetCal = Calendar.getInstance(kolkataTz).apply { timeInMillis = lockUntilMs }
        assertEquals(2026, targetCal.get(Calendar.YEAR))
        assertEquals(Calendar.NOVEMBER, targetCal.get(Calendar.MONTH))
        assertEquals(1, targetCal.get(Calendar.DAY_OF_MONTH))
        assertEquals(0, targetCal.get(Calendar.HOUR_OF_DAY))
        assertEquals(0, targetCal.get(Calendar.MINUTE))
        assertEquals(0, targetCal.get(Calendar.SECOND))
        assertEquals(0, targetCal.get(Calendar.MILLISECOND))

        val dateStr = WakeUpMusicManager.calculateNextMonthLockDateString(cal.timeInMillis, kolkataTz)
        assertEquals("1 November", dateStr)
    }

    @Test
    fun testEndOfMonthLocksUntilNextFirst() {
        // 31 October 2026 23:59:59 IST
        val cal = makeCal(2026, Calendar.OCTOBER, 31, 23, 59)
        val lockUntilMs = WakeUpMusicManager.calculateNextMonthLockUntilMs(cal.timeInMillis, kolkataTz)

        val targetCal = Calendar.getInstance(kolkataTz).apply { timeInMillis = lockUntilMs }
        assertEquals(Calendar.NOVEMBER, targetCal.get(Calendar.MONTH))
        assertEquals(1, targetCal.get(Calendar.DAY_OF_MONTH))
    }

    @Test
    fun testDecemberBoundaryLocksUntilJanuary1NextYear() {
        // 15 December 2026
        val cal = makeCal(2026, Calendar.DECEMBER, 15, 10, 0)
        val lockUntilMs = WakeUpMusicManager.calculateNextMonthLockUntilMs(cal.timeInMillis, kolkataTz)

        val targetCal = Calendar.getInstance(kolkataTz).apply { timeInMillis = lockUntilMs }
        assertEquals(2027, targetCal.get(Calendar.YEAR))
        assertEquals(Calendar.JANUARY, targetCal.get(Calendar.MONTH))
        assertEquals(1, targetCal.get(Calendar.DAY_OF_MONTH))
        assertEquals(0, targetCal.get(Calendar.HOUR_OF_DAY))

        val dateStr = WakeUpMusicManager.calculateNextMonthLockDateString(cal.timeInMillis, kolkataTz)
        assertEquals("1 January", dateStr)
    }

    @Test
    fun testFebruaryLocksUntilMarch1() {
        // 28 February 2026
        val cal = makeCal(2026, Calendar.FEBRUARY, 28, 8, 0)
        val lockUntilMs = WakeUpMusicManager.calculateNextMonthLockUntilMs(cal.timeInMillis, kolkataTz)

        val targetCal = Calendar.getInstance(kolkataTz).apply { timeInMillis = lockUntilMs }
        assertEquals(Calendar.MARCH, targetCal.get(Calendar.MONTH))
        assertEquals(1, targetCal.get(Calendar.DAY_OF_MONTH))
    }

    @Test
    fun testFirstDayOfMonthLocksUntilNextFirst() {
        // 1 November 2026 00:00:01
        val cal = makeCal(2026, Calendar.NOVEMBER, 1, 0, 0)
        val lockUntilMs = WakeUpMusicManager.calculateNextMonthLockUntilMs(cal.timeInMillis, kolkataTz)

        val targetCal = Calendar.getInstance(kolkataTz).apply { timeInMillis = lockUntilMs }
        assertEquals(Calendar.DECEMBER, targetCal.get(Calendar.MONTH))
        assertEquals(1, targetCal.get(Calendar.DAY_OF_MONTH))
    }

    @Test
    fun testEarbudRouteMatchingFuzzyAndRedactedAddress() {
        // Redacted address on Android 10+ (Galaxy A55): selected address empty, route address empty, names match
        assertTrue(WakeUpAttemptRules.selectedRouteMatches(
            selectedName = "Galaxy Buds2 Pro",
            selectedAddress = "",
            routeName = "Galaxy Buds2 Pro",
            routeAddress = "",
            bluetooth = true
        ))

        // Fuzzy name match (prefix / case-insensitive)
        assertTrue(WakeUpAttemptRules.selectedRouteMatches(
            selectedName = "Galaxy Buds2 Pro",
            selectedAddress = "",
            routeName = "Galaxy Buds2 Pro (LE)",
            routeAddress = "",
            bluetooth = true
        ))

        // Phone speaker is always rejected even if routeIsExternalAudio is true
        assertFalse(WakeUpAttemptRules.selectedRouteMatches(
            selectedName = "Galaxy Buds2 Pro",
            selectedAddress = "AA:BB:CC:DD:EE:FF",
            routeName = "Phone Speaker",
            routeAddress = "",
            bluetooth = true
        ))

        // Route not external audio is rejected
        assertFalse(WakeUpAttemptRules.selectedRouteMatches(
            selectedName = "Galaxy Buds2 Pro",
            selectedAddress = "AA:BB:CC:DD:EE:FF",
            routeName = "Galaxy Buds2 Pro",
            routeAddress = "AA:BB:CC:DD:EE:FF",
            bluetooth = false
        ))
    }
}
