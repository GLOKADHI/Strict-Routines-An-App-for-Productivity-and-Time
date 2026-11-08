package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test
import java.util.TimeZone

class GymRoutineDatesTest {
    private val initial = listOf(GymRoutineDates.Segment("2026-10-25", "2026-10-25"))
    private fun state(date: String, parts: List<GymRoutineDates.Segment> = initial) =
        GymRoutineDates.plannedState(date, true, GymRoutineDates.LAUNCH_DATE, parts)

    @Test fun initialAlternateDaysContinueIntoNovember() {
        listOf("2026-10-25", "2026-10-27", "2026-10-29", "2026-10-31", "2026-11-02", "2026-11-04").forEach {
            assertEquals(it, "GYM_DAY", state(it))
        }
        listOf("2026-10-26", "2026-10-28", "2026-10-30", "2026-11-01", "2026-11-03").forEach {
            assertEquals(it, "REST_DAY", state(it))
        }
    }

    @Test fun missingConfigurationNeverImpliesActiveGym() {
        assertEquals("SETUP_REQUIRED", GymRoutineDates.plannedState("2026-10-25", false, "2026-10-25", initial))
        assertEquals("NOT_TRACKED", state("2026-10-24"))
    }

    @Test fun pauseIntervalsAndExclusiveEndsPreserveHistory() {
        val parts = listOf(GymRoutineDates.Segment("2026-10-25", "2026-10-25", "2026-10-26"),
            GymRoutineDates.Segment("2026-10-28", "2026-10-28"))
        assertEquals("GYM_DAY", state("2026-10-25", parts))
        assertEquals("PAUSED", state("2026-10-26", parts))
        assertEquals("PAUSED", state("2026-10-27", parts))
        assertEquals("GYM_DAY", state("2026-10-28", parts))
        assertEquals("REST_DAY", state("2026-10-29", parts))
        assertEquals("GYM_DAY", state("2026-10-30", parts))
        assertEquals("GYM_DAY", state("2026-11-01", parts))
    }

    @Test fun multipleResumeAnchorsDoNotRewriteOldCycles() {
        val parts = listOf(GymRoutineDates.Segment("2026-10-25", "2026-10-25", "2026-10-29"),
            GymRoutineDates.Segment("2026-11-01", "2026-11-01", "2026-11-05"),
            GymRoutineDates.Segment("2026-11-10", "2026-11-10"))
        assertEquals("GYM_DAY", state("2026-10-27", parts))
        assertEquals("PAUSED", state("2026-10-29", parts))
        assertEquals("GYM_DAY", state("2026-11-03", parts))
        assertEquals("PAUSED", state("2026-11-09", parts))
        assertEquals("GYM_DAY", state("2026-11-10", parts))
        assertEquals("REST_DAY", state("2026-11-11", parts))
    }

    @Test fun lateConfirmationDoesNotBackfillObligations() {
        val late = listOf(GymRoutineDates.Segment("2026-10-28", "2026-10-25"))
        assertEquals("NOT_TRACKED", state("2026-10-25", late))
        assertEquals("NOT_TRACKED", state("2026-10-27", late))
        assertEquals("REST_DAY", state("2026-10-28", late))
        assertEquals("GYM_DAY", state("2026-10-29", late))
    }

    @Test fun futureResumeLeavesInterveningDatesPaused() {
        val parts = listOf(GymRoutineDates.Segment("2026-10-25", "2026-10-25", "2026-10-26"),
            GymRoutineDates.Segment("2026-11-01", "2026-11-01"))
        assertEquals("PAUSED", state("2026-10-31", parts))
        assertEquals("2026-11-01", GymRoutineDates.nextDate("2026-10-28", "GYM_DAY", true, "2026-10-25", parts))
        assertEquals("2026-11-02", GymRoutineDates.nextDate("2026-10-28", "REST_DAY", true, "2026-10-25", parts))
    }

    @Test fun nextPlannedDatesIncludeTodayOnlyWhenApplicable() {
        assertEquals("2026-10-25", GymRoutineDates.nextDate("2026-10-24", "GYM_DAY", true, "2026-10-25", initial))
        assertEquals("2026-10-26", GymRoutineDates.nextDate("2026-10-25", "REST_DAY", true, "2026-10-25", initial))
        assertEquals("2026-10-27", GymRoutineDates.nextDate("2026-10-26", "GYM_DAY", true, "2026-10-25", initial))
        assertEquals("2026-10-26", GymRoutineDates.nextDate("2026-10-26", "REST_DAY", true, "2026-10-25", initial))
    }

    @Test fun pausedWithoutFutureCycleHasNoNextDate() {
        val parts = listOf(GymRoutineDates.Segment("2026-10-25", "2026-10-25", "2026-10-26"))
        assertNull(GymRoutineDates.nextDate("2026-10-28", "GYM_DAY", true, "2026-10-25", parts))
        assertNull(GymRoutineDates.nextDate("2026-10-25", "GYM_DAY", false, "2026-10-25", initial))
    }

    @Test fun canonicalDatesRejectImpossibleAndPartialValues() {
        listOf("2026-02-29", "2026-13-01", "2026-10-32", "2026-1-01", "2026-10-25x", "", "2026-00-01").forEach {
            assertFalse(it, GymRoutineDates.isDate(it))
        }
        assertTrue(GymRoutineDates.isDate("2028-02-29"))
    }

    @Test fun leapDatesAndYearBoundariesUseCalendarDays() {
        assertEquals(2L, GymRoutineDates.calendarDaysBetween("2028-02-28", "2028-03-01"))
        assertEquals("2028-02-29", GymRoutineDates.addDays("2028-02-28", 1))
        assertEquals("2027-01-01", GymRoutineDates.addDays("2026-12-31", 1))
        assertEquals(29, GymRoutineDates.monthDays("2028-02").size)
        assertEquals(28, GymRoutineDates.monthDays("2027-02").size)
    }

    @Test fun calendarMonthNavigationAndSundayColumnAreStable() {
        assertEquals("2026-11", GymRoutineDates.addMonths("2026-10", 1))
        assertEquals("2026-09", GymRoutineDates.addMonths("2026-10", -1))
        assertEquals("2027-01", GymRoutineDates.addMonths("2026-12", 1))
        assertEquals(0, GymRoutineDates.weekday("2026-10-25"))
        assertEquals(4, GymRoutineDates.weekday("2026-10-01"))
    }

    @Test fun indiaMidnightIsIndependentOfDeviceTimezone() {
        // Derive the exact boundary from date-only encoding to avoid a device clock dependency.
        val indiaMidnight = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.ENGLISH).apply {
            timeZone = TimeZone.getTimeZone("Asia/Kolkata")
        }.parse("2026-10-25 00:00:00")!!.time
        assertEquals("2026-10-24", GymRoutineDates.today(indiaMidnight - 1))
        assertEquals("2026-10-25", GymRoutineDates.today(indiaMidnight))
    }

    @Test fun daylightSavingDeviceZoneDoesNotMoveSchedule() {
        val old = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("America/New_York"))
            assertEquals(2L, GymRoutineDates.calendarDaysBetween("2026-10-31", "2026-11-02"))
            assertEquals("GYM_DAY", state("2026-11-02"))
            assertEquals("2026-11-02", GymRoutineDates.addDays("2026-10-31", 2))
        } finally { TimeZone.setDefault(old) }
    }
}
