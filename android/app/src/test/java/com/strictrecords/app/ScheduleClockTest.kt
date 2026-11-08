package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Test

class ScheduleClockTest {
    @Test fun absoluteClockRespectsScheduleZoneAndMidnight() {
        val instant = DisciplineScoringRules.parseIsoTimestampMs("2026-10-13T00:15:00+05:30")
        assertEquals("12:15 AM", ScheduleClock.display(instant, "Asia/Kolkata"))
        assertEquals("2026-10-12 2:45 PM", ScheduleClock.display(instant, "America/New_York", "yyyy-MM-dd h:mm a"))
        assertEquals("12:00 PM", ScheduleClock.display(DisciplineScoringRules.parseIsoTimestampMs("2026-10-13T12:00:00+05:30"), "Asia/Kolkata"))
    }
}
