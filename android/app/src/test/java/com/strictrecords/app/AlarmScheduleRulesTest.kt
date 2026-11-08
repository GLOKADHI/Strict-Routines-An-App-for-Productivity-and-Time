package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class AlarmScheduleRulesTest {
    @Test fun rebootClockChangeAndResumeRestoreAlarmsEvenWithSavedFingerprint() {
        assertFalse(AlarmScheduleRules.shouldRebuild("same", "same", false))
        assertTrue(AlarmScheduleRules.shouldRebuild("same", "same", true))
        assertTrue(AlarmScheduleRules.shouldRebuild("old", "new", false))
        assertTrue(AlarmScheduleRules.shouldRebuild(null, "new", false))
    }
    @Test fun lateReminderNeverOpensExpiredWindow() {
        val slot = HourlyWindowRules.slots("2026-10-14", "09:00").first()
        assertTrue(HourlyWindowRules.notificationEligible("OPENING", slot.reminderMs, slot.startMs, slot.endMs))
        assertFalse(HourlyWindowRules.notificationEligible("OPENING", slot.startMs, slot.startMs, slot.endMs))
        assertFalse(HourlyWindowRules.notificationEligible("OPENED", slot.endMs, slot.startMs, slot.endMs))
        assertFalse(HourlyWindowRules.notificationEligible("CLOSED", slot.endMs + 120000, slot.startMs, slot.endMs))
    }

    @Test fun reschedulingInvalidatesOldStartEvenWhenDeadlineIsUnchanged() {
        assertTrue(AlarmScheduleRules.matchesCurrentWindow(1000L, 4000L, 1000L, 4000L))
        assertFalse(AlarmScheduleRules.matchesCurrentWindow(1000L, 4000L, 2000L, 4000L))
        assertFalse(AlarmScheduleRules.matchesCurrentWindow(1000L, 4000L, 1000L, 5000L))
        assertFalse(AlarmScheduleRules.matchesCurrentWindow(4000L, 1000L, 4000L, 1000L))
    }
}
