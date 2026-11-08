package com.strictrecords.app

import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import kotlin.math.abs

/** Presentation only. No score mutation, proof decision, or policy evaluation occurs here. */
object FarmModel {
    enum class Period { DAILY, WEEKLY, MONTHLY }
    enum class Light { SUNRISE, MORNING, AFTERNOON, SUNSET, NIGHT }
    enum class Weather { CALM, RAIN, DROUGHT, STORM }
    enum class Action { IDLE, WALKING, INSPECTING_CROPS, WATERING, PLANTING, APPLYING_NUTRIENTS, CELEBRATING, RESTING, STORM_RESPONSE, RECOVERING }
    data class Range(val start: Long, val end: Long, val label: String)
    data class Transaction(
        val id: String, val time: Long, val date: String, val type: String, val title: String,
        val points: Int, val confirmed: Boolean, val origin: String, val category: String = "",
        val reference: String = "", val reason: String = "", val partial: Boolean = false,
        val proofReward: Boolean = false, val storm: Boolean = false, val reverses: String = "",
        val status: String = if (confirmed) "Confirmed" else "Pending verification"
    )
    data class Slot(val id: String, val date: String, val end: Long, val status: String, val required: Boolean, val timelyPending: Boolean)
    data class Snapshot(val account: String, val events: List<Transaction>, val slots: List<Slot>, val balance: Int,
                        val now: Long, val timezone: String, val sync: String, val complete: Boolean = true)
    data class Totals(val earned: Int, val deducted: Int, val resets: Int, val adjustments: Int,
                      val rewards: Int, val partial: Int, val penalties: Int, val missed: Int,
                      val excused: Int, val pending: Int, val android: Int, val windows: Int) {
        val net get() = earned - deducted
        val ledgerChange get() = net + resets
    }
    data class Plot(val growth: Float = 0f, val moisture: Float = .55f, val health: Float = 1f)
    fun blendPlot(from: Plot, to: Plot, fraction: Float): Plot {
        val t = fraction.coerceIn(0f, 1f).let { it * it * (3f - 2f * it) }
        return Plot(from.growth + (to.growth - from.growth) * t,
            from.moisture + (to.moisture - from.moisture) * t,
            from.health + (to.health - from.health) * t)
    }
    data class Effect(val id: String, val time: Long, val weather: Weather, val action: Action, val message: String, val amount: Int)
    data class Projection(val plots: List<Plot>, val effects: List<Effect>, val condition: Weather, val earnedGrowth: Int)
    data class Lighting(val phase: Light, val next: Light, val blend: Float, val hour: Float)
    /** Shared by Home instances in this process; a new process always restores silently. */
    class VisualSession {
        private val hydratedScopes = mutableSetOf<String>()
        fun fresh(scope: String, effects: List<Effect>, seen: Set<String>, initialized: Boolean, now: Long): List<Effect> {
            val result = freshEffects(effects, seen, initialized && scope in hydratedScopes, now)
            hydratedScopes += scope
            return result
        }
    }
    private val resetTypes = setOf("DAILY_RESET", "INITIAL_RESET")
    private val adjustmentTypes = setOf("REVERSAL", "SCORE_CORRECTION", "ADMIN_SCORE_ADJUSTMENT", "RECONCILIATION_COMPENSATION")

    fun range(period: Period, anchor: Long, zone: String, locale: Locale = Locale.getDefault()): Range {
        val cal = Calendar.getInstance(TimeZone.getTimeZone(zone), locale).apply {
            timeInMillis = anchor; set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
        }
        if (period == Period.WEEKLY) cal.add(Calendar.DAY_OF_MONTH, -((7 + cal.get(Calendar.DAY_OF_WEEK) - cal.firstDayOfWeek) % 7))
        if (period == Period.MONTHLY) cal.set(Calendar.DAY_OF_MONTH, 1)
        val start = cal.timeInMillis
        cal.add(when (period) { Period.DAILY -> Calendar.DAY_OF_MONTH; Period.WEEKLY -> Calendar.WEEK_OF_YEAR; Period.MONTHLY -> Calendar.MONTH }, 1)
        val end = cal.timeInMillis
        val pattern = if (period == Period.MONTHLY) "MMMM yyyy" else if (period == Period.DAILY) "EEE, d MMM yyyy" else "d MMM yyyy"
        val format = SimpleDateFormat(pattern, locale).apply { timeZone = TimeZone.getTimeZone(zone) }
        return Range(start, end, if (period == Period.WEEKLY) "${format.format(Date(start))} – ${format.format(Date(end - 1))}" else format.format(Date(start)))
    }

    fun move(period: Period, anchor: Long, amount: Int, zone: String): Long = Calendar.getInstance(TimeZone.getTimeZone(zone)).apply {
        timeInMillis = anchor
        add(when (period) { Period.DAILY -> Calendar.DAY_OF_MONTH; Period.WEEKLY -> Calendar.WEEK_OF_YEAR; Period.MONTHLY -> Calendar.MONTH }, amount)
    }.timeInMillis

    fun lighting(now: Long, zone: String): Lighting {
        val c = Calendar.getInstance(TimeZone.getTimeZone(zone)).apply { timeInMillis = now }
        val h = c.get(Calendar.HOUR_OF_DAY) + c.get(Calendar.MINUTE) / 60f + c.get(Calendar.SECOND) / 3600f
        val phase = when { h < 5 -> Light.NIGHT; h < 7 -> Light.SUNRISE; h < 12 -> Light.MORNING; h < 17 -> Light.AFTERNOON; h < 19 -> Light.SUNSET; else -> Light.NIGHT }
        // Blend during the last 30 minutes of each phase; continuous across midnight.
        val end = when (phase) { Light.SUNRISE -> 7f; Light.MORNING -> 12f; Light.AFTERNOON -> 17f; Light.SUNSET -> 19f; Light.NIGHT -> if (h < 5) 5f else 29f }
        val next = when (phase) { Light.NIGHT -> Light.SUNRISE; Light.SUNRISE -> Light.MORNING; Light.MORNING -> Light.AFTERNOON; Light.AFTERNOON -> Light.SUNSET; Light.SUNSET -> Light.NIGHT }
        return Lighting(phase, next, ((h - (end - .5f)) / .5f).coerceIn(0f, 1f), h)
    }

    fun unique(events: List<Transaction>): List<Transaction> = events.groupBy { it.id }.values.map { rows ->
        // DB primary key is authoritative. Conflicting duplicate snapshots fail closed for visual effects.
        if (rows.distinct().size > 1) rows.first().copy(confirmed = false, status = "Conflicting duplicate; refresh required") else rows.first()
    }.sortedWith(compareBy<Transaction> { it.time }.thenBy { it.id })

    fun missed(slot: Slot, now: Long) = slot.required && slot.status == "MISSED" && slot.end > 0 && slot.end <= now && !slot.timelyPending
    fun totals(snapshot: Snapshot, range: Range): Totals {
        val rows = unique(snapshot.events).filter { it.time >= range.start && it.time < range.end }
        val economic = rows.filter { it.confirmed && it.type !in resetTypes }
        val slots = snapshot.slots.distinctBy { it.id }.filter { it.end >= range.start && it.end < range.end }
        return Totals(economic.sumOf { it.points.coerceAtLeast(0) }, economic.sumOf { -it.points.coerceAtMost(0) },
            rows.filter { it.confirmed && it.type in resetTypes }.sumOf { it.points },
            economic.filter { it.type in adjustmentTypes }.sumOf { it.points },
            economic.count { it.proofReward && it.points > 0 }, economic.count { it.proofReward && it.partial && it.points > 0 },
            economic.count { it.points < 0 && it.type !in adjustmentTypes }, slots.count { missed(it, snapshot.now) },
            slots.count { it.status == "EXCUSED" }, rows.count { !it.confirmed } + slots.count { it.timelyPending },
            economic.filter { it.origin == "Android APK" }.sumOf { it.points }, economic.filter { it.origin == "Windows EXE" }.sumOf { it.points })
    }

    fun action(category: String): Action = when (category.uppercase(Locale.US).replace(' ', '_')) {
        "BATH", "HYGIENE", "BATH_COMPLETED", "WAKE_UP", "WAKEUP", "WAKEUP_COMPLETED" -> Action.WATERING
        "STUDY", "ACADEMIC_STUDY", "COLLEGE", "READING" -> Action.INSPECTING_CROPS
        // Documented presentation mapping only; nutrients never award extra points.
        "CODING", "EDITING", "PROGRAMMING", "DEEP_WORK", "CODING/PROGRAMMING" -> Action.APPLYING_NUTRIENTS
        "GYM", "EXERCISE", "WORKOUT", "GYM_COMPLETED" -> Action.CELEBRATING
        else -> Action.INSPECTING_CROPS
    }

    fun project(snapshot: Snapshot): Projection {
        val rows = unique(snapshot.events).filter { it.confirmed && it.time > 0 && it.time <= snapshot.now }
        val reversed = rows.filter { it.type in adjustmentTypes && it.reverses.isNotBlank() }.map { it.reverses }.toSet()
        val effects = mutableListOf<Effect>()
        rows.filter { it.id !in reversed }.forEach { e ->
            if (e.points > 0 && e.proofReward) effects += Effect(e.id, e.time, Weather.RAIN, action(e.category),
                "${if (e.partial) "Partial reward" else "Proof rewarded"} · +${e.points} pts", e.points)
            else if (e.points < 0 && e.storm) effects += Effect(e.id, e.time, Weather.STORM, Action.STORM_RESPONSE,
                "Confirmed penalty · ${e.points} pts", e.points)
        }
        snapshot.slots.distinctBy { it.id }.filter { missed(it, snapshot.now) }.forEach {
            effects += Effect("missed:${it.id}:${it.end}", it.end, Weather.DROUGHT, Action.INSPECTING_CROPS, "Required submission missed · soil needs water", 0)
        }
        val sorted = effects.sortedWith(compareBy<Effect> { it.time }.thenBy { it.id })
        val plots = MutableList(6) { Plot() }
        sorted.forEach { e ->
            val index = (e.id.hashCode().toLong().let { abs(it) } % plots.size).toInt()
            val plot = plots[index]
            plots[index] = when (e.weather) {
                Weather.RAIN -> plot.copy(growth = (plot.growth + (e.amount * .025f).coerceAtMost(.35f)).coerceAtMost(1f), moisture = (plot.moisture + .32f).coerceAtMost(1f), health = (plot.health + .15f).coerceAtMost(1f))
                Weather.STORM -> plot.copy(health = (plot.health - .22f).coerceAtLeast(.15f), growth = (plot.growth - .10f).coerceAtLeast(0f))
                Weather.DROUGHT -> plot.copy(moisture = (plot.moisture - (.12f + (snapshot.now - e.time).coerceAtLeast(0) / 86400000f * .08f).coerceAtMost(.30f)).coerceAtLeast(0f))
                else -> plot
            }
        }
        val recentStorm = sorted.lastOrNull()?.let { it.weather == Weather.STORM && snapshot.now - it.time < 90000 } == true
        val dry = plots.any { it.moisture < .35f }
        return Projection(plots, sorted, if (recentStorm) Weather.STORM else if (dry) Weather.DROUGHT else Weather.CALM,
            sorted.filter { it.weather == Weather.RAIN }.sumOf { it.amount })
    }

    /** First render hydrates history silently; only fresh IDs after that can animate once. */
    fun freshEffects(effects: List<Effect>, seen: Set<String>, initialized: Boolean, now: Long): List<Effect> =
        if (!initialized) emptyList() else effects.filter { it.id !in seen && it.time <= now && now - it.time <= 120000 }
}
