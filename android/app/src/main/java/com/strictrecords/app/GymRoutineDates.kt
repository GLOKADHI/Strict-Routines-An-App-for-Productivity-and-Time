package com.strictrecords.app

import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.GregorianCalendar
import java.util.Locale
import java.util.TimeZone

/** Date-only preview rules. This class never creates attendance or points. */
object GymRoutineDates {
    const val LAUNCH_DATE = "2026-10-25"
    const val ANCHOR_DATE = LAUNCH_DATE
    const val TIMEZONE = "Asia/Kolkata"
    data class Segment(val startDate: String, val anchorDate: String, val endDate: String? = null)

    fun isGymDay(value: String, anchorDate: String = ANCHOR_DATE): Boolean {
        if (!isDate(value) || !isDate(anchorDate)) return false
        val offset = calendarDaysBetween(anchorDate, value)
        return offset >= 0L && offset % 2L == 0L
    }

    private fun formatter(pattern: String, zone: String = "UTC") = SimpleDateFormat(pattern, Locale.ENGLISH).apply {
        timeZone = TimeZone.getTimeZone(zone)
        isLenient = false
    }

    fun isDate(value: String): Boolean = value.matches(Regex("\\d{4}-\\d{2}-\\d{2}")) && runCatching {
        val parsed = formatter("yyyy-MM-dd").parse(value) ?: return@runCatching false
        formatter("yyyy-MM-dd").format(parsed) == value
    }.getOrDefault(false)

    private fun date(value: String): Date {
        require(isDate(value)) { "Use a valid date in YYYY-MM-DD format" }
        return formatter("yyyy-MM-dd").parse(value)!!
    }

    fun today(nowMs: Long): String = formatter("yyyy-MM-dd", TIMEZONE).format(Date(nowMs))
    fun month(value: String): String = value.take(7)
    fun addDays(value: String, days: Int): String = GregorianCalendar(TimeZone.getTimeZone("UTC")).run {
        time = date(value); add(Calendar.DAY_OF_MONTH, days); formatter("yyyy-MM-dd").format(time)
    }
    fun addMonths(value: String, months: Int): String = GregorianCalendar(TimeZone.getTimeZone("UTC")).run {
        time = date("${value.take(7)}-01"); add(Calendar.MONTH, months); formatter("yyyy-MM").format(time)
    }
    fun monthDays(value: String): List<String> {
        val first = "$value-01"
        require(isDate(first)) { "Invalid calendar month" }
        val calendar = GregorianCalendar(TimeZone.getTimeZone("UTC")).apply { time = date(first) }
        return (1..calendar.getActualMaximum(Calendar.DAY_OF_MONTH)).map { "$value-${it.toString().padStart(2, '0')}" }
    }
    /** Sunday is column zero. */
    fun weekday(value: String): Int = GregorianCalendar(TimeZone.getTimeZone("UTC")).run {
        time = date(value); get(Calendar.DAY_OF_WEEK) - Calendar.SUNDAY
    }
    fun display(value: String, pattern: String = "EEE, d MMM yyyy"): String =
        if (isDate(value)) formatter(pattern).format(date(value)) else "—"
    fun displayMonth(value: String): String = display("$value-01", "MMMM yyyy")

    /** UTC is only an encoding of the date fields: no local elapsed-time/DST arithmetic. */
    fun calendarDaysBetween(first: String, second: String): Long =
        (date(second).time / 86_400_000L) - (date(first).time / 86_400_000L)

    fun plannedState(value: String, configured: Boolean, startDate: String, segments: List<Segment>): String {
        require(isDate(value))
        if (!configured) return "SETUP_REQUIRED"
        if (!isDate(startDate) || value < startDate) return "NOT_TRACKED"
        val firstTracked = segments.map { it.startDate }.filter { isDate(it) }.minOrNull()
        if (firstTracked != null && value < firstTracked) return "NOT_TRACKED"
        val segment = segments.filter { isDate(it.startDate) && isDate(it.anchorDate) &&
            value >= it.startDate && (it.endDate == null || (isDate(it.endDate) && value < it.endDate)) }
            .maxByOrNull { it.startDate } ?: return "PAUSED"
        val offset = calendarDaysBetween(segment.anchorDate, value)
        return if (offset >= 0 && offset % 2L == 0L) "GYM_DAY" else if (offset >= 0) "REST_DAY" else "NOT_TRACKED"
    }

    fun nextDate(from: String, state: String, configured: Boolean, startDate: String, segments: List<Segment>): String? {
        if (!configured || !isDate(from) || !isDate(startDate)) return null
        // Start with today, including a future confirmed resume segment.
        val first = maxOf(from, startDate)
        val candidates = segments.filter { it.endDate == null || it.endDate > first }.mapNotNull { segment ->
            val begin = maxOf(first, segment.startDate, segment.anchorDate)
            if (!isDate(begin)) return@mapNotNull null
            val offset = calendarDaysBetween(segment.anchorDate, begin)
            val wantEven = state == "GYM_DAY"
            val candidate = if ((offset % 2L == 0L) == wantEven) begin else addDays(begin, 1)
            if (segment.endDate == null || candidate < segment.endDate) candidate else null
        }
        return candidates.minOrNull()
    }
}
