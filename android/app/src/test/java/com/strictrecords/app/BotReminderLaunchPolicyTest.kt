package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class BotReminderLaunchPolicyTest {
    @Test fun dueTaskAlarmMayShowWhenLockedOrScreenOff() {
        assertTrue(BotReminderLaunchPolicy.useFullScreen("HOURLY_CHECK", "hourly1", true, true, true, true))
        assertTrue(BotReminderLaunchPolicy.useFullScreen("ACTIVITY_OPEN", "bath1", false, false, true, true))
        assertTrue(BotReminderLaunchPolicy.useFullScreen("WAKEUP_CHALLENGE", "wake1", true, false, true, true))
        assertTrue(BotReminderLaunchPolicy.useFullScreen("ACTIVITY_EXPIRING", "gym1", true, true, true, true))
    }
    @Test fun unlockedPhoneUsesCompactPopupInsteadOfFullScreen() {
        assertFalse(BotReminderLaunchPolicy.useFullScreen("HOURLY_CHECK", "hourly1", false, true, true, true))
    }
    @Test fun revokedPermissionsAlwaysKeepTheNotificationFallback() {
        assertFalse(BotReminderLaunchPolicy.useFullScreen("WAKEUP_CHALLENGE", "wake1", true, false, false, true))
        assertFalse(BotReminderLaunchPolicy.useFullScreen("WAKEUP_CHALLENGE", "wake1", true, false, true, false))
    }
    @Test fun unscheduledOrClosedEventsCannotWakeTheScreen() {
        assertFalse(BotReminderLaunchPolicy.useFullScreen("HOURLY_CHECK", "", true, false, true, true))
        assertFalse(BotReminderLaunchPolicy.useFullScreen("ACTIVITY_CLOSED", "hourly1", true, false, true, true))
        assertFalse(BotReminderLaunchPolicy.useFullScreen("UNRECOGNIZED", "hourly1", true, false, true, true))
    }
    @Test fun staleFutureRebootAndUnboundedIntentsCannotOpenTheAlarmScreen() {
        assertTrue(BotReminderLaunchPolicy.isFresh(100, 20_100, 101))
        assertFalse(BotReminderLaunchPolicy.isFresh(100, 20_100, 20_100))
        assertFalse(BotReminderLaunchPolicy.isFresh(100, 20_100, 99))
        assertFalse(BotReminderLaunchPolicy.isFresh(100, 30_100, 101))
        assertFalse(BotReminderLaunchPolicy.isFresh(-1, 100, 1))
        assertFalse(BotReminderLaunchPolicy.isFresh(100, 100, 100))
    }
}
