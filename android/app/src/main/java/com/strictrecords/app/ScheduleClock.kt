package com.strictrecords.app

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

object ScheduleClock {
    fun display(timestampMs: Long, timezone: String, pattern: String = "h:mm a"): String =
        SimpleDateFormat(pattern, Locale.US).apply { timeZone = TimeZone.getTimeZone(timezone) }.format(Date(timestampMs))
}
