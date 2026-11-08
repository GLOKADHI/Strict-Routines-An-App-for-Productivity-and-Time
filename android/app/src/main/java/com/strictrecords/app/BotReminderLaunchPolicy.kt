package com.strictrecords.app

/** Only due scheduled alarms can request the OS lock-screen presentation. */
internal object BotReminderLaunchPolicy {
    const val DISPLAY_MS = 20_000L
    fun useFullScreen(mode: String, activityId: String?, locked: Boolean, interactive: Boolean,
                      allowed: Boolean, notificationsEnabled: Boolean): Boolean {
        if (!allowed || !notificationsEnabled || (!locked && interactive)) return false
        return mode in setOf("WAKEUP_CHALLENGE", "WAKEUP_ALARM", "GYM_TIME") ||
            (!activityId.isNullOrBlank() && mode in setOf("HOURLY_CHECK", "ACTIVITY_OPEN", "ACTIVITY_OPENING", "ACTIVITY_EXPIRING"))
    }
    fun isFresh(created: Long, expires: Long, now: Long): Boolean =
        created >= 0L && now >= created && now < expires && expires - created in 1L..DISPLAY_MS
}
