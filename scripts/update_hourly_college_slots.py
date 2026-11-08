import sys

path = 'android/app/src/main/java/com/strictrecords/app/HourlyWindowRules.kt'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

college_method = '''    /**
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

        // 2. Subsequent hourly submissions: Follow configured hourly schedule from 10:00 AM onwards
        val actualEndHour = minOf(endHour, 23)
        for (hour in 10..actualEndHour) {
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
'''

target = '    fun state(nowMs: Long'
assert target in content
content = content.replace(target, college_method + '\n' + target)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated HourlyWindowRules.kt with collegeDaySlots")
