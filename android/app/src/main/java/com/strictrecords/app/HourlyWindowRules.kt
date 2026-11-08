package com.strictrecords.app

import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

/** The existing 20-minute checkpoints, anchored to the configured wake-up minute. */
object HourlyWindowRules {
    data class Slot(
        val id: String,
        val targetMs: Long,
        val startMs: Long,
        val endMs: Long,
        val reminderMs: Long,
        val hourSlot: String,
        val hour: Int = 0
    )

    fun slots(date: String, timeZone: TimeZone): List<Slot> {
        return (1..23).map { h ->
            val target = Calendar.getInstance(timeZone).apply {
                val parser = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { this.timeZone = timeZone; isLenient = false }
                time = requireNotNull(parser.parse(date))
                set(Calendar.HOUR_OF_DAY, h)
                set(Calendar.MINUTE, 0)
                set(Calendar.SECOND, 0)
                set(Calendar.MILLISECOND, 0)
            }.timeInMillis
            val slotStr = String.format(Locale.US, "%02d:00-%02d:00", h - 1, h)
            val startMs = target - 600_000L
            val endMs = if (h == 23) target else (target + 600_000L)
            val reminderMs = startMs - 60_000L
            Slot("ACT-${date.replace("-", "")}-${String.format(Locale.US, "%02d00", h % 24)}-HOURLY", target, startMs, endMs, reminderMs, slotStr, h)
        }
    }

    fun slots(date: String, wakeTime: String, timezone: String = "Asia/Kolkata", endHour: Int = 23): List<Slot> {
        require(Regex("\\d{2}:\\d{2}").matches(wakeTime))
        val parts = wakeTime.split(":").map { it.toInt() }
        require(parts[0] in 0..23 && parts[1] in 0..59)
        val zone = TimeZone.getTimeZone(timezone)
        val parser = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { timeZone = zone; isLenient = false }
        val day = requireNotNull(parser.parse(date))
        val actualEndHour = minOf(endHour, 23)
        return (parts[0]..actualEndHour).map { hour ->
            val isFinal11PmSlot = (hour == 23)
            val cal = Calendar.getInstance(zone).apply {
                time = day
                set(Calendar.HOUR_OF_DAY, hour)
                set(Calendar.MINUTE, if (isFinal11PmSlot) 0 else parts[1])
                set(Calendar.SECOND, 0)
                set(Calendar.MILLISECOND, 0)
            }
            val target = cal.timeInMillis
            val hm = if (isFinal11PmSlot) "2300" else String.format(Locale.US, "%02d%02d", hour, parts[1])
            val slot = if (isFinal11PmSlot) "22:00-23:00" else String.format(Locale.US, "%02d:%02d-%02d:%02d", (hour + 23) % 24, parts[1], hour, parts[1])
            val startMs = target - 600_000L
            val endMs = if (isFinal11PmSlot) target else (target + 600_000L)
            val reminderMs = startMs - 60_000L
            Slot("ACT-${date.replace("-", "")}-${hm}-HOURLY", target, startMs, endMs, reminderMs, slot, hour)
        }
    }
    /**
     * Preserves the fixed college-day hourly schedule:
     * - First hourly submission window: 9:00 AM–10:00 AM
     * - Subsequent hourly submissions: Follow configured hourly schedule (10:00 AM through 11:00 PM)
     * Invariant: Never shifted or recalculated from wake-up start or completion timestamp.
     */
    fun collegeDaySlots(date: String, timezone: String = "Asia/Kolkata", endHour: Int = 23): List<Slot> {
        val zone = TimeZone.getTimeZone(timezone)
        val parser = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { timeZone = zone; isLenient = false }
        val day = requireNotNull(parser.parse(date))
        val slots = mutableListOf<Slot>()

        // 1. First hourly submission window: 9:00 AM–10:00 AM (fixed and invariant)
        val cal9 = Calendar.getInstance(zone).apply {
            time = day
            set(Calendar.HOUR_OF_DAY, 9)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        val cal10 = Calendar.getInstance(zone).apply {
            time = day
            set(Calendar.HOUR_OF_DAY, 10)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        val target9 = cal10.timeInMillis
        val start9 = cal9.timeInMillis
        val end9 = cal10.timeInMillis
        val reminder9 = start9 - 60_000L
        slots.add(
            Slot(
                id = "ACT-${date.replace("-", "")}-0900-HOURLY",
                targetMs = target9,
                startMs = start9,
                endMs = end9,
                reminderMs = reminder9,
                hourSlot = "09:00-10:00",
                hour = 9
            )
        )

        // 2. Subsequent hourly submissions: Follow configured hourly schedule from 10:00 AM–11:00 AM (h=11) onwards
        val actualEndHour = minOf(endHour, 23)
        for (hour in 11..actualEndHour) {
            val isFinal11PmSlot = (hour == 23)
            val cal = Calendar.getInstance(zone).apply {
                time = day
                set(Calendar.HOUR_OF_DAY, hour)
                set(Calendar.MINUTE, 0)
                set(Calendar.SECOND, 0)
                set(Calendar.MILLISECOND, 0)
            }
            val target = cal.timeInMillis
            val hm = String.format(Locale.US, "%02d00", hour)
            val slot = if (isFinal11PmSlot) "22:00-23:00" else String.format(Locale.US, "%02d:00-%02d:00", (hour + 23) % 24, hour)
            val startMs = target - 600_000L
            val endMs = if (isFinal11PmSlot) target else (target + 600_000L)
            val reminderMs = startMs - 60_000L
            slots.add(
                Slot(
                    id = "ACT-${date.replace("-", "")}-${hm}-HOURLY",
                    targetMs = target,
                    startMs = startMs,
                    endMs = endMs,
                    reminderMs = reminderMs,
                    hourSlot = slot,
                    hour = hour
                )
            )
        }
        return slots
    }

    fun state(nowMs: Long, startMs: Long, endMs: Long): String = when {
        nowMs >= endMs -> "CLOSED"
        nowMs >= startMs -> "OPEN"
        nowMs >= startMs - 60_000L -> "NOTIFIED"
        else -> "UPCOMING"
    }
    fun notificationEligible(action: String, nowMs: Long, startMs: Long, endMs: Long): Boolean = when (action) {
        "OPENING" -> nowMs in (startMs - 60_000L) until startMs
        "OPENED" -> nowMs in startMs until endMs
        "WARN1M" -> nowMs in (endMs - 60_000L) until endMs
        "CLOSED" -> nowMs in endMs until (endMs + 120_000L)
        else -> false
    }

    fun isSubmissionWithinWindow(submissionMs: Long, startMs: Long, endMs: Long): Boolean =
        submissionMs in startMs until endMs
}
