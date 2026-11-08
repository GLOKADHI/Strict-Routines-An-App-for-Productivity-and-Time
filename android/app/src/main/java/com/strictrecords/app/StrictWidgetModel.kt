package com.strictrecords.app

/** Presentation only. Scores and windows are supplied by the existing local engine. */
enum class StrictWidgetKind(val label: String, val cells: String, val tab: String, val domain: String) {
    S1("Next task", "2 × 2", "TIMELINE", "tasks"),
    S2("Window status", "2 × 2", "TIMELINE", "tasks"),
    S3("Today's points", "2 × 2", "REWARDS", "points"),
    S4("Monitoring", "2 × 2", "DEVICES", "monitoring"),
    M1("Upcoming tasks", "4 × 2", "TIMELINE", "tasks"),
    M2("Points & next reward", "4 × 2", "REWARDS", "points"),
    L1("Daily schedule", "4 × 4", "TIMELINE", "tasks"),
    L2("Points & categories", "4 × 4", "REWARDS", "points"),
    L3("Monitoring & devices", "4 × 4", "DEVICES", "monitoring");
    companion object {
        fun parse(value: String?, fallback: StrictWidgetKind = M1) = values().firstOrNull { it.name == value } ?: fallback
    }
}

data class StrictWidgetOptions(
    val kind: StrictWidgetKind = StrictWidgetKind.M1,
    val categories: Boolean = true,
    val details: Boolean = true,
    val progress: Boolean = true,
    val compact: Boolean = false,
    val light: Boolean = false,
    val battery: Boolean = false
) {
    /** A placed widget can change content within its provider's launcher size family. */
    fun forProvider(fallback: StrictWidgetKind) = if(kind.cells == fallback.cells) this else copy(kind=fallback)
}

data class StrictWidgetTask(val id: String, val title: String, val start: Long, val end: Long,
    val savedStatus: String, val interval: String = "", val eligible: Boolean = true) {
    fun state(now: Long) = if (savedStatus in setOf("PENDING","APPROVED","UNDER_REVIEW","CLOSED","EXPIRED","MISSED")) savedStatus
        else WindowStateRules.state(savedStatus, now, start, end, eligible)
}

object StrictWidgetTime {
    fun remaining(now: Long, deadline: Long): String {
        val minutes = ((deadline - now).coerceAtLeast(0) + 59_999) / 60_000
        return if (minutes < 60) "$minutes min" else "${minutes / 60}h ${minutes % 60}m"
    }
    fun next(tasks: List<StrictWidgetTask>, now: Long): StrictWidgetTask? =
        tasks.filter { it.eligible && it.state(now) in setOf("OPEN", "UPCOMING") }
            .sortedWith(compareBy<StrictWidgetTask> { if (it.state(now) == "OPEN") 0 else 1 }.thenBy { it.start }).firstOrNull()

    fun relevant(tasks: List<StrictWidgetTask>): List<StrictWidgetTask> = tasks
        .filter { it.savedStatus != "CANCELLED" }.distinctBy { it.id }.sortedBy { it.start }

    fun rows(tasks: List<StrictWidgetTask>, now: Long, history: Boolean): List<StrictWidgetTask> {
        val clean=relevant(tasks)
        val nextId=next(clean,now)?.id
        val upcoming=clean.filter { it.id!=nextId && (it.end>now || it.state(now) in setOf("PENDING_SYNC","SUBMITTED","VALIDATING","UNDER_REVIEW")) }
        return if(history && upcoming.isEmpty()) clean.takeLast(5).filter { it.id!=nextId } else upcoming
    }
    fun title(task: StrictWidgetTask) = if(task.title.startsWith("Hourly Discipline Check")) "Hourly check" else task.title

    /** One non-wakeup alarm shared by all instances; never an exact or repeating alarm. */
    fun nextRefresh(now: Long, tasks: List<StrictWidgetTask>, countdown: Boolean): Long {
        val boundary = if(countdown) tasks.filter { it.eligible && it.state(now) in setOf("OPEN","UPCOMING") }
            .flatMap { listOf(it.start, it.end) }.filter { it > now }.minOrNull() else null
        val cadence = if (countdown && boundary != null) 5 * 60_000L else 30 * 60_000L
        return minOf(now + cadence, boundary ?: Long.MAX_VALUE).coerceAtLeast(now + 60_000L)
    }
}
