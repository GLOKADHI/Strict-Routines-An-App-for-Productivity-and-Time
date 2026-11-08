package com.strictrecords.app

import android.app.Activity
import android.app.DatePickerDialog
import android.app.Dialog
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.lang.ref.WeakReference
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.atomic.AtomicBoolean

/** Settings-only native UI. Planned dates never mark a workout verified or modify the ledger. */
object GymRoutineUi {
    fun createEntry(activity: Activity, db: StrictRecordsLocalDb, onOpenDeviceCenter: (() -> Unit)? = null): View = Controller(activity, db, onOpenDeviceCenter = onOpenDeviceCenter).entry

    fun createTasksDashboard(activity: Activity, db: StrictRecordsLocalDb, onOpenSettings: () -> Unit, onOpenDeviceCenter: (() -> Unit)? = null): View =
        Controller(activity, db, onOpenSettings = onOpenSettings, onOpenDeviceCenter = onOpenDeviceCenter).dashboard


    private const val BG = "#0C1420"
    private const val SURFACE = "#142231"
    private const val EDGE = "#244154"
    private const val CYAN = "#50D7EF"
    private const val GREEN = "#45E8AE"
    private const val TEXT = "#EDF5FB"
    private const val MUTED = "#A8BBCB"
    private const val WARNING = "#F4C46A"
    private const val RED = "#FF8C91"

    private class BoundedScroll(context: Context) : ScrollView(context) {
        override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
            val cap = (resources.displayMetrics.heightPixels * 0.86f).toInt()
            val available = if (View.MeasureSpec.getMode(heightMeasureSpec) == View.MeasureSpec.UNSPECIFIED) cap
                else minOf(cap, View.MeasureSpec.getSize(heightMeasureSpec))
            super.onMeasure(widthMeasureSpec, View.MeasureSpec.makeMeasureSpec(available, View.MeasureSpec.AT_MOST))
        }
    }

    private class Controller(val activity: Activity, val db: StrictRecordsLocalDb, val onOpenSettings: (() -> Unit)? = null, val onOpenDeviceCenter: (() -> Unit)? = null) {
        private var lastResult: GymRoutineClient.Result? = null

        fun openDeviceCenter() {
            onOpenDeviceCenter?.invoke() ?: run {
                try {
                    val method = activity.javaClass.getDeclaredMethod("showDeviceCenterDialog")
                    method.isAccessible = true
                    method.invoke(activity)
                } catch (_: Exception) {
                    android.widget.Toast.makeText(activity, "Open Settings → Device Center & PC Pairing", android.widget.Toast.LENGTH_SHORT).show()
                }
            }
        }
        val entry = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        val dashboard = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        private val busy = AtomicBoolean(false)
        private var message = "Confirmed changes are shared through your registered PC"
        private var failed = false
        private val today get() = GymRoutineDates.today(db.authoritativeNowMs())
        private fun query() = GymRoutineClient.Query(GymRoutineDates.month(today))
        private fun dp(value: Int) = (activity.resources.displayMetrics.density * value).toInt()
        private fun color(value: String) = Color.parseColor(value)
        private fun receivedTime(value: Long): String = SimpleDateFormat("d MMM yyyy, h:mm a", Locale.ENGLISH).apply {
            timeZone = TimeZone.getTimeZone(GymRoutineDates.TIMEZONE)
        }.format(Date(value)) + " IST"
        private fun bg(fill: String = SURFACE, stroke: String = EDGE, radius: Int = 16) = GradientDrawable().apply {
            setColor(color(fill)); cornerRadius = dp(radius).toFloat(); setStroke(dp(1), color(stroke))
        }
        private fun column(padding: Int = 0): LinearLayout = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            if (padding > 0) setPadding(dp(padding), dp(padding), dp(padding), dp(padding))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }
        private fun label(value: String, size: Float = 14f, tint: String = TEXT, bold: Boolean = false): TextView = TextView(activity).apply {
            text = value; textSize = size; setTextColor(color(tint)); setLineSpacing(dp(2).toFloat(), 1f)
            if (bold) setTypeface(typeface, Typeface.BOLD)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(8) }
        }
        private fun heading(parent: LinearLayout, text: String) { parent.addView(label(text, 16f, TEXT, true)) }
        private fun caption(parent: LinearLayout, text: String, tint: String = MUTED) { parent.addView(label(text, 13f, tint)) }
        private fun button(title: String, accent: String = CYAN, enabled: Boolean = true, action: () -> Unit): Button = Button(activity).apply {
            text = title; textSize = 14f; isAllCaps = false; setTextColor(color(accent))
            background = bg(BG, accent, 12); minHeight = dp(48); minimumHeight = dp(48)
            setPadding(dp(12), dp(8), dp(12), dp(8)); isEnabled = enabled; alpha = if (enabled) 1f else 0.5f
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(8) }
            setOnClickListener { action() }
        }
        private fun actions(parent: LinearLayout, first: Button, second: Button) {
            val row = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
            listOf(first, second).forEachIndexed { index, view ->
                view.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    if (index == 0) rightMargin = dp(4) else leftMargin = dp(4)
                    bottomMargin = dp(8)
                }; row.addView(view)
            }
            parent.addView(row)
        }
        private fun card(parent: LinearLayout): LinearLayout = column(14).apply {
            background = bg(); layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(12) }
            parent.addView(this)
        }
        private fun field(parent: LinearLayout, title: String, value: String, tint: String = TEXT) {
            parent.addView(label(title, 11f, MUTED, true).apply { (layoutParams as LinearLayout.LayoutParams).bottomMargin = dp(2) })
            parent.addView(label(value, 14f, tint))
        }
        private fun configured(routine: JSONObject) = routine.optString("routine_id").let { it.isNotBlank() && it != "null" }
        private fun nullable(obj: JSONObject, key: String): String = if (obj.isNull(key)) "" else obj.optString(key)
        private fun segments(routine: JSONObject): List<GymRoutineDates.Segment> {
            val items = routine.optJSONArray("segments") ?: JSONArray()
            return (0 until items.length()).mapNotNull { index ->
                val part = items.optJSONObject(index) ?: return@mapNotNull null
                val start = part.optString("start_date"); val anchor = part.optString("anchor_date")
                val end = nullable(part, "end_date").ifBlank { null }
                if (!GymRoutineDates.isDate(start) || !GymRoutineDates.isDate(anchor) ||
                    (end != null && !GymRoutineDates.isDate(end))) null else GymRoutineDates.Segment(start, anchor, end)
            }
        }
        private fun planned(routine: JSONObject, date: String): String = GymRoutineDates.plannedState(date,
            configured(routine), routine.optString("start_date", GymRoutineDates.LAUNCH_DATE), segments(routine))
        private fun next(routine: JSONObject, state: String): String = GymRoutineDates.nextDate(today, state, configured(routine),
            routine.optString("start_date", GymRoutineDates.LAUNCH_DATE), segments(routine))?.let { GymRoutineDates.display(it) } ?: "None while paused"
        private fun stateText(state: String) = when (state) {
            "GYM_DAY" -> "✓ Planned Gym Day"
            "REST_DAY" -> "✕ Rest Day"
            "PAUSED" -> "⏸ Routine Paused"
            "NOT_TRACKED" -> "Not Tracked"
            else -> "Setup Required"
        }
        private fun stateTint(state: String) = when (state) { "GYM_DAY" -> GREEN; "PAUSED" -> WARNING; else -> MUTED }
        private fun status(routine: JSONObject): String = when {
            !configured(routine) -> "Setup Required"
            today < routine.optString("start_date", GymRoutineDates.LAUNCH_DATE) && routine.optString("status") == "PAUSED" -> "Paused"
            today < routine.optString("start_date", GymRoutineDates.LAUNCH_DATE) -> "Not Started"
            planned(routine, today) == "PAUSED" -> "Paused"
            planned(routine, today) == "NOT_TRACKED" -> "Not Started"
            else -> "Active"
        }
        init {
            renderEntry()
            renderDashboard()
            // One refresh when either section attaches, then explicit refreshes only.
            val attachListener = object : View.OnAttachStateChangeListener {
                override fun onViewAttachedToWindow(view: View) {
                    view.removeOnAttachStateChangeListener(this)
                    refresh(query(), automatic = true)
                }
                override fun onViewDetachedFromWindow(view: View) = Unit
            }
            entry.addOnAttachStateChangeListener(attachListener)
            dashboard.addOnAttachStateChangeListener(attachListener)
        }

        private fun renderEntry() {
            entry.removeAllViews(); entry.setPadding(dp(16), dp(16), dp(16), dp(12)); entry.background = bg()
            entry.layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(12) }
            heading(entry, "Alternate-Day Gym Routine")
            caption(entry, "Plan your gym days every other day. Pause when necessary and resume when you are ready.")
            val routine = GymRoutineClient.routine(db)
            field(entry, "CONFIRMED ROUTINE", status(routine), if (status(routine) == "Paused") WARNING else CYAN)
            if (configured(routine)) {
                field(entry, "TODAY · ${GymRoutineDates.display(today, "d MMM")}", stateText(planned(routine, today)), stateTint(planned(routine, today)))
                field(entry, "START / CURRENT ANCHOR", "${GymRoutineDates.display(routine.optString("start_date"), "d MMM yyyy")} / ${GymRoutineDates.display(routine.optString("anchor_date"), "d MMM yyyy")}")
                field(entry, "NEXT PLANNED GYM DAY", next(routine, "GYM_DAY"), GREEN)
                field(entry, "NEXT REST DAY", next(routine, "REST_DAY"))
                nullable(routine, "pending_resume_date").takeIf { it.isNotBlank() }?.let {
                    caption(entry, "Resume scheduled for ${GymRoutineDates.display(it)}", WARNING)
                }
            } else caption(entry, "Starts Sunday, 25 October 2026 · Asia/Kolkata. Confirm setup once to save the routine.")
            caption(entry, "This is a plan, not attendance. An explicit daily gym choice takes precedence. Gym Mode still needs its separate activation, eligibility and verified evidence.")
            routine.optJSONObject("today")?.takeIf { it.optString("date") == today }?.let { day ->
                if (day.optBoolean("planning_exception")) caption(entry, "Today's existing daily choice or booked session overrides the alternate-day pattern.", WARNING)
            }
            val pending = GymRoutineClient.pending(db)
            val outstanding = pending.optString("request_id").isNotBlank()
            if (outstanding) {
                caption(entry, if (pending.optBoolean("_conflict")) "Change not accepted: ${pending.optString("_error")}" else
                    "${pending.optString("action").lowercase().replaceFirstChar { it.uppercase() }} request pending PC confirmation. The confirmed routine remains in force.", WARNING)
                entry.addView(button("Review pending request", WARNING) { showPending() })
            }
            caption(entry, if (busy.get()) "Checking the registered PC…" else message, if (failed) WARNING else MUTED)
            actions(entry, button("View Calendar") { StrictCalendarUi.showCalendar(activity, db) }, button("Statistics") { showStatistics() })
            when {
                !configured(routine) -> entry.addView(button("Review Initial Setup", GREEN, !busy.get() && !outstanding) { showSetup() })
                status(routine) == "Paused" -> entry.addView(button("Resume Routine", GREEN, !busy.get() && !outstanding && routine.optBoolean("can_resume", true)) { showResume() })
                else -> entry.addView(button("Pause Routine", WARNING, !busy.get() && !outstanding && routine.optBoolean("can_pause", true)) { showPause() })
            }
            entry.addView(button(if (busy.get()) "Refreshing…" else "Refresh from PC", CYAN, !busy.get()) { refresh(query()) })
            val connState = GymRoutineClient.getConnectionState(db, lastResult)
            when (connState) {
                GymRoutineClient.ConnectionState.NOT_PAIRED -> {
                    caption(entry, "Pair this phone with your PC to manage the routine.", WARNING)
                    entry.addView(button("📱 Connect to PC (Device Center)", CYAN) { openDeviceCenter() })
                }
                GymRoutineClient.ConnectionState.AUTH_EXPIRED -> {
                    caption(entry, "Reconnect to your PC to renew the pairing.", RED)
                    entry.addView(button("📱 Connect to PC (Device Center)", CYAN) { openDeviceCenter() })
                }
                GymRoutineClient.ConnectionState.PC_UNAVAILABLE -> {
                    caption(entry, "This phone is paired, but the PC is currently unavailable.", WARNING)
                    actions(entry,
                        button("🔄 Retry PC", CYAN, !busy.get()) { refresh(query()) },
                        button("📱 Device Center", MUTED) { openDeviceCenter() }
                    )
                }
                GymRoutineClient.ConnectionState.SYNC_FAILED -> {
                    caption(entry, "Connected, but the latest routine could not be refreshed.", WARNING)
                }
                GymRoutineClient.ConnectionState.PAIRED_ONLINE -> {
                    val pcName = db.getDeviceRegistration()?.optString("paired_pc_name", "Windows PC") ?: "Windows PC"
                    caption(entry, "● Connected to $pcName", GREEN)
                }
            }
        }

        private abstract inner class Sheet(val title: String) {
            val dialog = Dialog(activity)
            val body = column(18)
            init {
                dialog.requestWindowFeature(Window.FEATURE_NO_TITLE)
                val scroll = BoundedScroll(activity).apply { isFillViewport = false; addView(body) }
                dialog.setContentView(scroll)
                dialog.window?.apply {
                    setBackgroundDrawable(bg(BG, EDGE, 22)); setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
                    clearFlags(WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS)
                    decorView.setOnApplyWindowInsetsListener { _, insets ->
                        scroll.setPadding(0, 0, 0, insets.systemWindowInsetBottom.coerceAtLeast(0)); insets
                    }
                }
            }
            fun show() {
                redraw(); dialog.show()
                dialog.window?.setLayout(minOf(activity.resources.displayMetrics.widthPixels - dp(24), dp(720)), ViewGroup.LayoutParams.WRAP_CONTENT)
            }
            fun reset() { body.removeAllViews(); heading(body, title) }
            fun close() { body.addView(button("Close", MUTED) { dialog.dismiss() }) }
            abstract fun redraw()
        }

        private fun refresh(request: GymRoutineClient.Query, sheet: Sheet? = null, automatic: Boolean = false) {
            if (!busy.compareAndSet(false, true)) return
            renderEntry(); renderDashboard(); sheet?.redraw()
            val weakController = WeakReference(this); val weakSheet = sheet?.let { WeakReference(it) }
            val database = db; val main = Handler(Looper.getMainLooper())
            Thread({
                val result = GymRoutineClient.refresh(database, request, automatic)
                main.post {
                    val controller = weakController.get() ?: return@post
                    controller.busy.set(false)
                    if (controller.activity.isFinishing || controller.activity.isDestroyed) return@post
                    controller.lastResult = result
                    controller.message = result.message; controller.failed = !result.success
                    controller.renderEntry()
                    controller.renderDashboard()
                    weakSheet?.get()?.takeIf { it.dialog.isShowing }?.redraw()
                }
            }, "GymRoutine-Refresh").start()
        }

        private fun confirm(action: String, fields: JSONObject, sheet: Sheet) {
            if (busy.get()) return
            val result = GymRoutineClient.queue(db, action, fields)
            message = result.message; failed = !result.success
            if (result.success) { sheet.dialog.dismiss(); refresh(query()) } else {
                android.widget.Toast.makeText(activity, result.message, android.widget.Toast.LENGTH_LONG).show(); sheet.redraw()
            }
            renderEntry()
            renderDashboard()
        }

        private fun renderDashboard() {
            dashboard.removeAllViews()
            dashboard.setPadding(dp(16), dp(16), dp(16), dp(14))
            dashboard.background = bg()
            dashboard.layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply {
                topMargin = dp(10)
                bottomMargin = dp(18)
            }

            // Header Row: Section Title + Subtitle + Routine Status Pill
            val headerRow = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { bottomMargin = dp(8) }
            }

            val titleCol = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            }
            titleCol.addView(label("GYM ROUTINE STATISTICS", 15f, TEXT, bold = true).apply {
                (layoutParams as LinearLayout.LayoutParams).bottomMargin = dp(2)
            })
            titleCol.addView(label("Alternate-Day Routine • Anchor: 25 Oct 2026 (Asia/Kolkata)", 11f, MUTED).apply {
                (layoutParams as LinearLayout.LayoutParams).bottomMargin = 0
            })
            headerRow.addView(titleCol)

            val routine = GymRoutineClient.routine(db)
            val routineStatus = status(routine)
            val statusColor = when (routineStatus) {
                "Active" -> GREEN
                "Paused" -> WARNING
                else -> MUTED
            }

            val statusPill = TextView(activity).apply {
                text = routineStatus.uppercase(Locale.ENGLISH)
                textSize = 10f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(statusColor))
                setPadding(dp(10), dp(4), dp(10), dp(4))
                background = bg(BG, statusColor, 99)
            }
            headerRow.addView(statusPill)
            dashboard.addView(headerRow)

            // Cycle & schedule state
            val cycleInfo = card(dashboard)
            val plannedToday = planned(routine, today)
            field(cycleInfo, "TODAY'S PLANNED STATE", stateText(plannedToday), stateTint(plannedToday))
            field(cycleInfo, "NEXT PLANNED GYM DAY", next(routine, "GYM_DAY"), GREEN)
            field(cycleInfo, "NEXT REST DAY", next(routine, "REST_DAY"))

            // Confirmed Statistics & Performance
            val cached = runCatching { GymRoutineClient.cached(db, query()) }.getOrDefault(JSONObject())
            val stats = cached.optJSONObject("statistics")

            heading(dashboard, "Session & Points Performance")
            if (stats == null) {
                metricRow(dashboard, "Planned gym days", JSONObject().put("k", 0), "k", "Verified sessions", "k")
                metricRow(dashboard, "Pending sessions", JSONObject().put("k", 0), "k", "Missed sessions", "k")
                metricRow(dashboard, "Verified points", JSONObject().put("k", "0 pts"), "k", "Deductions", "k")
                metricRow(dashboard, "Net gym points", JSONObject().put("k", "0 pts"), "k", "Provisional points", "k")
                metricRow(dashboard, "Verified streak", JSONObject().put("k", "0 days"), "k", "Completion rate", "k")
                caption(dashboard, "No gym sessions recorded yet since routine launch (25 October 2026). Planned dates do not count as completed workouts.", MUTED)
            } else {
                metricRow(dashboard, "Planned gym days", stats, "planned_gym_days", "Verified sessions", "verified_sessions")
                metricRow(dashboard, "Pending sessions", stats, "pending_sessions", "Missed sessions", "missed_sessions")
                metricRow(dashboard, "Verified points", stats, "verified_points", "Deductions", "deductions")
                metricRow(dashboard, "Net gym points", stats, "net_points", "Provisional points", "provisional_points")
                val streakStr = if (!stats.isNull("streak") && stats.has("streak")) stats.opt("streak").toString() + " days" else "0 days"
                val rateStr = if (!stats.isNull("completion_rate") && stats.has("completion_rate")) "${"%.1f".format(stats.optDouble("completion_rate"))}%" else "0.0%"
                val summaryObj = JSONObject().put("streak_val", streakStr).put("rate_val", rateStr)
                metricRow(dashboard, "Verified streak", summaryObj, "streak_val", "Completion rate", "rate_val")

                // Points trend if recorded
                val daily = stats.optJSONArray("daily") ?: JSONArray()
                val rows = (0 until daily.length()).mapNotNull { daily.optJSONObject(it) }.filter {
                    GymRoutineDates.isDate(it.optString("date")) && it.optString("date") <= today && it.has("net_points") && !it.isNull("net_points")
                }
                if (rows.isNotEmpty()) {
                    heading(dashboard, "Recorded Net Points Trend")
                    val shown = rows.takeLast(5)
                    val max = shown.maxOfOrNull { kotlin.math.abs(it.optInt("net_points")) }?.coerceAtLeast(1) ?: 1
                    shown.forEach { day ->
                        val points = day.optInt("net_points")
                        val row = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
                        row.addView(label(GymRoutineDates.display(day.optString("date"), "d MMM"), 11f, MUTED).apply { layoutParams = LinearLayout.LayoutParams(dp(60), ViewGroup.LayoutParams.WRAP_CONTENT) })
                        val track = LinearLayout(activity).apply { background = bg(BG); layoutParams = LinearLayout.LayoutParams(0, dp(8), 1f) }
                        track.addView(View(activity).apply { background = bg(if (points < 0) RED else GREEN); layoutParams = LinearLayout.LayoutParams(0, dp(8), kotlin.math.abs(points).toFloat() / max) })
                        track.addView(View(activity).apply { layoutParams = LinearLayout.LayoutParams(0, dp(8), (1f - kotlin.math.abs(points).toFloat() / max).coerceAtLeast(0f)) })
                        row.addView(track)
                        row.addView(label(if (points > 0) "+$points" else "$points", 11f, if (points < 0) RED else GREEN).apply { gravity = Gravity.END; layoutParams = LinearLayout.LayoutParams(dp(45), ViewGroup.LayoutParams.WRAP_CONTENT) })
                        dashboard.addView(row)
                    }
                }

                // Recent history items (top 3)
                val history = stats.optJSONArray("history") ?: JSONArray()
                if (history.length() > 0) {
                    heading(dashboard, "Recent Transactions")
                    for (i in 0 until minOf(3, history.length())) {
                        val item = history.optJSONObject(i) ?: continue
                        val point = item.optInt("points")
                        val hCard = card(dashboard)
                        heading(hCard, "${GymRoutineDates.display(item.optString("date"), "d MMM yyyy")} • ${if (point > 0) "+$point" else "$point"} pts")
                        caption(hCard, item.optString("description").ifBlank { item.optString("event_type", "Recorded gym transaction") })
                    }
                }

                if (stats.optBoolean("empty_state")) {
                    caption(dashboard, "No gym sessions recorded in this period since routine start (25 Oct 2026). Planned dates are not completed workouts.", MUTED)
                }
            }

            // Interactive Navigation & Controls
            actions(
                dashboard,
                button("📅 View Calendar") { StrictCalendarUi.showCalendar(activity, db) },
                button("📊 Full History & Trends") { showStatistics() }
            )

            if (onOpenSettings != null) {
                dashboard.addView(button("⚙️ Routine Controls & Pause/Resume (Settings)", WARNING) {
                    onOpenSettings.invoke()
                })
            }

            dashboard.addView(button(if (busy.get()) "Refreshing…" else "🔄 Refresh Gym Statistics", CYAN, !busy.get()) {
                refresh(query())
            })

            caption(dashboard, if (busy.get()) "Checking the registered PC…" else message, if (failed) WARNING else MUTED)
            if (cached.optLong("_received_at_ms") > 0) {
                caption(dashboard, "Actual records last received: ${receivedTime(cached.optLong("_received_at_ms"))}.", MUTED)
            }
        }

        private fun showPending() {
            object : Sheet("Pending routine change") {
                override fun redraw() {
                    reset(); val pending = GymRoutineClient.pending(db)
                    if (pending.optString("request_id").isBlank()) caption(body, "There is no outstanding request.") else {
                        field(body, "REQUEST", pending.optString("action"))
                        field(body, "REFERENCE", pending.optString("request_id"))
                        field(body, "BASED ON REVISION", pending.optLong("expected_revision").toString())
                        if (pending.has("reactivation_date")) field(body, "REQUESTED ANCHOR", pending.optString("reactivation_date"))
                        if (pending.has("reason_category")) field(body, "REASON", pending.optString("reason_category").replace('_', ' '))
                        caption(body, if (pending.optBoolean("_conflict")) pending.optString("_error") else "Not yet confirmed by the PC. No local calendar or scoring policy has been changed.", WARNING)
                        body.addView(button("Retry / Refresh", CYAN, !busy.get()) { refresh(query(), this) })
                        body.addView(button("Discard Unconfirmed Request", WARNING, !busy.get()) {
                            message = GymRoutineClient.discardPending(db).message
                            dialog.dismiss(); renderEntry(); refresh(query())
                        })
                        caption(body, "If the PC already received this request, discarding its local retry cannot undo the confirmed server change. Refresh reads the actual state.")
                    }; close()
                }
            }.show()
        }

        private fun showSetup() {
            object : Sheet("Review initial gym routine") {
                override fun redraw() {
                    reset(); field(body, "START DATE / FIRST PLANNED GYM DAY", "Sunday, 25 October 2026")
                    field(body, "FREQUENCY", "Every other calendar day · Asia/Kolkata")
                    field(body, "NEXT PLANNED GYM DAY", "Tuesday, 27 October 2026")
                    field(body, "PAUSE / RESUME", "Available, with retained cycle and reason history")
                    field(body, "STATISTICS TRACKING", "From 25 October 2026; actual confirmed sessions and ledger only")
                    caption(body, "If setup is confirmed later, earlier unconfigured dates are not backfilled as gym obligations. Planning does not activate Gym Mode, use lives, award points or verify attendance.")
                    caption(body, "The routine becomes confirmed only after the registered PC accepts the request. Offline changes remain pending.", WARNING)
                    body.addView(button("Preview October Calendar") { StrictCalendarUi.showCalendar(activity, db, "2026-10-25") })
                    body.addView(button("Confirm Setup", GREEN, !busy.get() && GymRoutineClient.hasRegistration(db)) { confirm("SETUP", JSONObject(), this) })
                    if (!GymRoutineClient.hasRegistration(db)) {
                        caption(body, "Pair with the PC before confirming this setup.", WARNING)
                        body.addView(button("📱 Connect to PC (Device Center)", CYAN) {
                            dialog.dismiss()
                            openDeviceCenter()
                        })
                    }
                    close()
                }
            }.show()
        }

        private fun showPause() {
            val categories = listOf("ILLNESS" to "Illness or recovery", "INJURY" to "Injury or medical advice", "TRAVEL" to "Travel",
                "WORK_STUDY" to "Academic or work commitments", "PERSONAL" to "Personal circumstances", "OTHER" to "Other")
            var selected = ""; var note = ""; var error = ""
            object : Sheet("Pause gym routine") {
                override fun redraw() {
                    reset(); caption(body, "Pauses planned gym days from the effective local date. Existing daily choices, completed or pending sessions, evidence and points remain governed by their current rules.")
                    caption(body, "Choose a reason. No sensitive medical details are required. Pausing the routine itself has no point cost.", WARNING)
                    categories.forEach { (code, text) -> body.addView(button(if (selected == code) "● $text" else "○ $text", if (selected == code) GREEN else MUTED) {
                        selected = code; redraw()
                    }) }
                    val input = EditText(activity).apply {
                        setText(note); hint = if (selected == "OTHER") "Short reason (required)" else "Optional short note"
                        setTextColor(color(TEXT)); setHintTextColor(color(MUTED)); textSize = 14f; minHeight = dp(48)
                        maxLines = 4; filters = arrayOf(android.text.InputFilter.LengthFilter(500)); background = bg()
                        setPadding(dp(12), dp(10), dp(12), dp(10))
                        addTextChangedListener(object : android.text.TextWatcher {
                            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
                            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) { note = s?.toString().orEmpty() }
                            override fun afterTextChanged(s: android.text.Editable?) = Unit
                        })
                    }; body.addView(input)
                    if (error.isNotBlank()) caption(body, error, RED)
                    body.addView(button("Confirm Pause", WARNING, !busy.get()) {
                        if (selected.isBlank() || (selected == "OTHER" && note.isBlank())) {
                            error = if (selected.isBlank()) "Select a pause reason" else "Add a short reason for Other"; redraw()
                        } else confirm("PAUSE", JSONObject().put("reason_category", selected).put("note", note), this)
                    }); body.addView(button("Cancel", MUTED) { dialog.dismiss() })
                }
            }.show()
        }

        private fun showResume() {
            var anchor = maxOf(today, GymRoutineDates.LAUNCH_DATE)
            var dateError = ""
            object : Sheet("Review new gym cycle") {
                override fun redraw() {
                    reset(); field(body, "REACTIVATION DATE / NEW ANCHOR", GymRoutineDates.display(anchor), GREEN)
                    body.addView(button("Choose Reactivation Date") {
                        val pieces = anchor.split('-').map { it.toInt() }
                        DatePickerDialog(activity, { _, year, month, day ->
                            val candidate = "%04d-%02d-%02d".format(year, month + 1, day)
                            if (candidate >= maxOf(today, GymRoutineDates.LAUNCH_DATE)) { anchor = candidate; dateError = "" }
                            else dateError = "Choose today or a later tracked date"
                            redraw()
                        }, pieces[0], pieces[1] - 1, pieces[2]).show()
                    })
                    if (dateError.isNotBlank()) caption(body, dateError, RED)
                    field(body, "FIRST PLANNED GYM DAY", GymRoutineDates.display(anchor))
                    field(body, "FOLLOWING REST DAY", GymRoutineDates.display(GymRoutineDates.addDays(anchor, 1)))
                    field(body, "NEXT PLANNED GYM DAY", GymRoutineDates.display(GymRoutineDates.addDays(anchor, 2)))
                    caption(body, "The selected date starts a fresh alternate-day cycle. The routine remains paused until that date if it is in the future. Previous cycles, pause reasons, actual sessions and ledger entries stay intact.")
                    caption(body, "This does not activate Gym Mode or grant music access. The PC must confirm the new cycle before it becomes authoritative.", WARNING)
                    body.addView(button("Confirm Resume", GREEN, !busy.get()) { confirm("RESUME", JSONObject().put("reactivation_date", anchor), this) })
                    body.addView(button("Cancel", MUTED) { dialog.dismiss() })
                }
            }.show()
        }

        private fun showCalendar(preview: Boolean) {
            var month = if (preview) "2026-10" else GymRoutineDates.month(today)
            var selected = if (preview) GymRoutineDates.LAUNCH_DATE else today
            val screen = object : Sheet(if (preview) "Initial calendar preview" else "Gym routine calendar") {
                override fun redraw() {
                    reset()
                    val calendarSheet = this
                    val saved = GymRoutineClient.routine(db)
                    val routine = if (preview) JSONObject().put("routine_id", "PREVIEW").put("start_date", GymRoutineDates.LAUNCH_DATE)
                        .put("segments", JSONArray().put(JSONObject().put("start_date", GymRoutineDates.LAUNCH_DATE).put("anchor_date", GymRoutineDates.LAUNCH_DATE))) else saved
                    val request = GymRoutineClient.Query(month)
                    val cached = if (preview) JSONObject() else GymRoutineClient.cached(db, request)
                    if (preview) caption(body, "Unconfirmed plan preview · no attendance, points, access or obligations are saved.", WARNING)
                    else caption(body, "${status(routine)} · ${GymRoutineDates.TIMEZONE}. Markers describe the plan, not attendance.")
                    val nav = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
                    nav.addView(button("‹", action = {}).apply {
                        contentDescription = "Previous month"; layoutParams = LinearLayout.LayoutParams(dp(48), dp(48))
                        setOnClickListener { month = GymRoutineDates.addMonths(month, -1); selected = "$month-01"; redraw(); if (!preview) refresh(GymRoutineClient.Query(month), calendarSheet) }
                    })
                    nav.addView(label(GymRoutineDates.displayMonth(month), 16f, TEXT, true).apply {
                        gravity = Gravity.CENTER; layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                    })
                    nav.addView(button("›", action = {}).apply {
                        contentDescription = "Next month"; layoutParams = LinearLayout.LayoutParams(dp(48), dp(48))
                        setOnClickListener { month = GymRoutineDates.addMonths(month, 1); selected = "$month-01"; redraw(); if (!preview) refresh(GymRoutineClient.Query(month), calendarSheet) }
                    }); body.addView(nav)
                    val weekdays = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
                    listOf("S", "M", "T", "W", "T", "F", "S").forEach { text -> weekdays.addView(label(text, 12f, MUTED, true).apply {
                        gravity = Gravity.CENTER; layoutParams = LinearLayout.LayoutParams(0, dp(30), 1f)
                    }) }; body.addView(weekdays)
                    val days = GymRoutineDates.monthDays(month); val blanks = GymRoutineDates.weekday(days.first())
                    val count = ((days.size + blanks + 6) / 7) * 7
                    val actual = cached.optJSONArray("calendar") ?: JSONArray()
                    val dayRecords = (0 until actual.length()).mapNotNull { actual.optJSONObject(it) }.associateBy { it.optString("date") }
                    for (week in 0 until count / 7) {
                        val row = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
                        for (day in 0..6) {
                            val index = week * 7 + day - blanks
                            if (index !in days.indices) row.addView(View(activity).apply { layoutParams = LinearLayout.LayoutParams(0, dp(56), 1f) }) else {
                                val date = days[index]; val state = planned(routine, date)
                                val marker = when (state) { "GYM_DAY" -> "✓"; "REST_DAY" -> "✕"; "PAUSED" -> "Ⅱ"; else -> "·" }
                                val dayLabel = date.takeLast(2).toInt().toString() + "\n" + marker
                                val cell = TextView(activity).apply {
                                    text = dayLabel; textSize = 12f; gravity = Gravity.CENTER; setTextColor(color(stateTint(state)))
                                    contentDescription = "${GymRoutineDates.display(date)}. ${stateText(state)}${if (date == today) ". Today" else ""}"
                                    minHeight = dp(56); isClickable = true; isFocusable = true
                                    background = bg(if (date == selected) "#1C3B4D" else BG, if (date == selected) CYAN else if (date == today) GREEN else EDGE, 8)
                                    layoutParams = LinearLayout.LayoutParams(0, dp(56), 1f).apply { setMargins(dp(1), dp(2), dp(1), dp(2)) }
                                    setOnClickListener { selected = date; redraw() }
                                }; row.addView(cell)
                            }
                        }; body.addView(row)
                    }
                    caption(body, "✓ Planned gym   ✕ Rest   Ⅱ Paused   · Not tracked\nCyan border: selected · Green border: today")
                    val details = card(body); heading(details, GymRoutineDates.display(selected))
                    field(details, "PLANNED STATE", stateText(planned(routine, selected)), stateTint(planned(routine, selected)))
                    val record = dayRecords[selected]
                    if (preview) caption(details, "Preview only. No session has been verified by this calendar.")
                    else if (record == null) caption(details, "Session and points data for this month have not been fetched. Refresh with the PC to view actual records; absence from the cache does not mean zero.", WARNING)
                    else {
                        field(details, "RECORDED ATTENDANCE", nullable(record, "attendance_status").ifBlank { "No recorded session status" })
                        if (!record.isNull("effective_daily_intent") && record.has("effective_daily_intent")) field(details, "EFFECTIVE DAILY INTENT",
                            if (record.optBoolean("effective_daily_intent")) "Gym planned" else "No gym planned")
                        nullable(record, "daily_intent_source").takeIf { it.isNotBlank() }?.let { caption(details, "Intent source: ${it.replace('_', ' ')}") }
                        if (record.optBoolean("planning_exception")) caption(details, "An existing daily choice or booked session overrides this routine's pattern. The calendar does not rewrite that choice.", WARNING)
                        if (record.has("verified_points") && !record.isNull("verified_points")) field(details, "VERIFIED POINTS / RECORDED DEDUCTIONS", "${record.optInt("verified_points")} / ${record.optInt("deductions")}")
                        else caption(details, "Verified point attribution is unavailable.")
                        val ids = record.optJSONArray("session_ids") ?: JSONArray()
                        if (ids.length() > 0) caption(details, "Session references: ${(0 until ids.length()).joinToString(", ") { ids.optString(it) }}")
                        nullable(record, "pause_reason").takeIf { it.isNotBlank() }?.let { field(details, "PAUSE CONTEXT", it.replace('_', ' ')) }
                    }
                    if (!preview && cached.optLong("_received_at_ms") > 0) caption(body, "Actual records last received: ${receivedTime(cached.optLong("_received_at_ms"))}. Cached records can be out of date.")
                    actions(body, button("Today") { month = GymRoutineDates.month(today); selected = today; redraw(); if (!preview) refresh(GymRoutineClient.Query(month), this) },
                        button(if (busy.get()) "Refreshing…" else "Refresh", CYAN, !preview && !busy.get()) { refresh(request, this) })
                    if (!preview) caption(body, message, if (failed) WARNING else MUTED)
                    close()
                }
            }
            screen.show(); if (!preview) refresh(GymRoutineClient.Query(month), screen)
        }

        private fun showStatistics() {
            var filter = "THIS_MONTH"; var from = maxOf(GymRoutineDates.LAUNCH_DATE, today.take(7) + "-01"); var to = today
            var historyPage = 0; var eventPage = 0
            fun request() = GymRoutineClient.Query(GymRoutineDates.month(today), filter, from, to)
            val screen = object : Sheet("Gym Routine Statistics") {
                override fun redraw() {
                    reset(); val routine = GymRoutineClient.routine(db)
                    field(body, "CONFIRMED ROUTINE", status(routine), CYAN); field(body, "NEXT PLANNED GYM DAY", next(routine, "GYM_DAY"), GREEN)
                    caption(body, "Actual sessions and attributable score-ledger transactions from 25 October 2026. Planned days never count as workouts.")
                    val chips = listOf("THIS_WEEK" to "This week", "THIS_MONTH" to "This month", "LAST_MONTH" to "Last month", "CUSTOM" to "Custom")
                    chips.chunked(2).forEach { pair -> actions(body,
                        button(pair[0].second, if (filter == pair[0].first) GREEN else MUTED) { filter = pair[0].first; historyPage = 0; redraw(); if (filter != "CUSTOM") refresh(request(), this) },
                        button(pair[1].second, if (filter == pair[1].first) GREEN else MUTED) { filter = pair[1].first; historyPage = 0; redraw(); if (filter != "CUSTOM") refresh(request(), this) }) }
                    if (filter == "CUSTOM") {
                        val startInput = EditText(activity).apply { setText(from); hint = "From YYYY-MM-DD"; textSize = 14f; setTextColor(color(TEXT)); setHintTextColor(color(MUTED)); minHeight = dp(48); isSingleLine = true }
                        val endInput = EditText(activity).apply { setText(to); hint = "To YYYY-MM-DD"; textSize = 14f; setTextColor(color(TEXT)); setHintTextColor(color(MUTED)); minHeight = dp(48); isSingleLine = true }
                        body.addView(label("Custom range · up to 366 days, from 25 October 2026", 12f, MUTED)); body.addView(startInput); body.addView(endInput)
                        body.addView(button("Apply Range", CYAN, !busy.get()) {
                            val start = startInput.text.toString().trim(); val end = endInput.text.toString().trim()
                            if (!GymRoutineDates.isDate(start) || !GymRoutineDates.isDate(end) || start < GymRoutineDates.LAUNCH_DATE || end < start || end > today || GymRoutineDates.calendarDaysBetween(start, end) > 365) {
                                message = "Use valid tracked dates from 25 October 2026 through today, at most 366 days"; failed = true; redraw()
                            } else { from = start; to = end; historyPage = 0; refresh(request(), this) }
                        })
                    }
                    val cached = runCatching { GymRoutineClient.cached(db, request()) }.getOrDefault(JSONObject())
                    val stats = cached.optJSONObject("statistics")
                    if (stats == null) caption(body, "No confirmed statistics for this filter are cached. Refresh from the PC to load actual sessions and ledger history.", WARNING)
                    else {
                        field(body, "REPORTED PERIOD", if (stats.optBoolean("has_tracked_period", true)) "${GymRoutineDates.display(stats.optString("start_date"), "d MMM yyyy")} – ${GymRoutineDates.display(stats.optString("end_date"), "d MMM yyyy")}" else "No tracked dates in this period · tracking begins 25 October 2026")
                        metricRow(body, "Planned gym days", stats, "planned_gym_days", "Verified sessions", "verified_sessions")
                        metricRow(body, "Pending sessions", stats, "pending_sessions", "Missed sessions", "missed_sessions")
                        metricRow(body, "Verified points", stats, "verified_points", "Deductions", "deductions")
                        metricRow(body, "Net gym points", stats, "net_points", "Provisional points", "provisional_points")
                        if (stats.has("recorded_unverified_points")) field(body, "RECORDED CREDIT · NOT VERIFIED", stats.opt("recorded_unverified_points").toString(), WARNING)
                        if (stats.optInt("recorded_adjustment_credits") > 0) field(body, "RECORDED CORRECTION CREDITS", stats.optInt("recorded_adjustment_credits").toString(), CYAN)
                        if (!stats.isNull("completion_rate") && stats.has("completion_rate")) field(body, "COMPLETION RATE", "${"%.1f".format(stats.optDouble("completion_rate"))}% · elapsed planned dates only")
                        if (!stats.isNull("streak") && stats.has("streak")) field(body, "VERIFIED GYM STREAK", stats.opt("streak").toString())
                        if (stats.optBoolean("empty_state")) caption(body, "No gym sessions recorded in this period since the routine started. Planned dates are not completed workouts.")
                        if (stats.optInt("unattributed_count") > 0) caption(body, "${stats.optInt("unattributed_count")} transaction(s) could not be reliably attributed to gym activity and are excluded.", WARNING)
                        heading(body, "Recorded gym points trend")
                        val daily = stats.optJSONArray("daily") ?: JSONArray()
                        val rows = (0 until daily.length()).mapNotNull { daily.optJSONObject(it) }.filter {
                            GymRoutineDates.isDate(it.optString("date")) && it.optString("date") <= today && it.has("net_points") && !it.isNull("net_points") }
                        if (rows.isEmpty()) caption(body, "No confirmed daily point data in this period. No trend is inferred.") else {
                            caption(body, "Actual daily net gym points · ${rows.size} recorded date(s). Future plans are excluded.")
                            // Show the most recent seven reported dates; the ledger below contains every eligible entry.
                            val shown = rows.takeLast(7); val max = shown.maxOfOrNull { kotlin.math.abs(it.optInt("net_points")) }?.coerceAtLeast(1) ?: 1
                            shown.forEach { day ->
                                val points = day.optInt("net_points"); val row = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
                                row.addView(label(GymRoutineDates.display(day.optString("date"), "d MMM"), 12f, MUTED).apply { layoutParams = LinearLayout.LayoutParams(dp(66), ViewGroup.LayoutParams.WRAP_CONTENT) })
                                val track = LinearLayout(activity).apply { background = bg(BG); layoutParams = LinearLayout.LayoutParams(0, dp(10), 1f) }
                                track.addView(View(activity).apply { background = bg(if (points < 0) RED else GREEN); layoutParams = LinearLayout.LayoutParams(0, dp(10), kotlin.math.abs(points).toFloat() / max) })
                                track.addView(View(activity).apply { layoutParams = LinearLayout.LayoutParams(0, dp(10), (1f - kotlin.math.abs(points).toFloat() / max).coerceAtLeast(0f)) })
                                row.addView(track); row.addView(label(if (points > 0) "+$points" else "$points", 12f, if (points < 0) RED else GREEN).apply { gravity = Gravity.END; layoutParams = LinearLayout.LayoutParams(dp(45), ViewGroup.LayoutParams.WRAP_CONTENT) }); body.addView(row)
                            }
                        }
                        heading(body, "Gym Points History")
                        val history = stats.optJSONArray("history") ?: JSONArray()
                        if (history.length() == 0) caption(body, "No attributable gym point transactions in this period.")
                        else {
                            historyPage = historyPage.coerceIn(0, (history.length() - 1) / 10)
                            for (index in historyPage * 10 until minOf(history.length(), historyPage * 10 + 10)) {
                                val item = history.optJSONObject(index) ?: continue; val point = item.optInt("points")
                                val row = card(body); heading(row, "${GymRoutineDates.display(item.optString("date"), "d MMM yyyy")} · ${if (point > 0) "+$point" else "$point"} pts")
                                caption(row, item.optString("description").ifBlank { item.optString("event_type", "Recorded gym transaction") })
                                caption(row, "${item.optString("verification_status", "Recorded")} · Ref: ${item.optString("event_id", "Unavailable")}")
                                nullable(item, "evidence_id").takeIf { it.isNotBlank() }?.let { caption(row, "Evidence: $it") }
                            }
                            caption(body, "Page ${historyPage + 1} / ${(history.length() + 9) / 10} · ${history.length()} transactions")
                            actions(body, button("Previous", MUTED, historyPage > 0) { historyPage--; redraw() }, button("Next", MUTED, (historyPage + 1) * 10 < history.length()) { historyPage++; redraw() })
                        }
                        caption(body, "Last received: ${receivedTime(cached.optLong("_received_at_ms"))}. Cached statistics may be out of date.")
                    }
                    heading(body, "Routine Events History")
                    val events = routine.optJSONArray("events") ?: JSONArray()
                    if (events.length() == 0) caption(body, "No confirmed setup, pause or resume events.") else {
                        val ordered = (0 until events.length()).mapNotNull { events.optJSONObject(it) }.asReversed()
                        eventPage = eventPage.coerceIn(0, (ordered.size - 1) / 5)
                        ordered.drop(eventPage * 5).take(5).forEach { event ->
                            val row = card(body); heading(row, "${event.optString("event_type")} · ${GymRoutineDates.display(event.optString("effective_date"), "d MMM yyyy")}")
                            nullable(event, "anchor_date").takeIf { it.isNotBlank() }?.let { caption(row, "Cycle anchor: ${GymRoutineDates.display(it)}") }
                            nullable(event, "reason_category").takeIf { it.isNotBlank() }?.let { caption(row, "Reason: ${it.replace('_', ' ')}") }
                            nullable(event, "note").takeIf { it.isNotBlank() }?.let { caption(row, it) }
                            caption(row, "Revision ${event.optInt("revision")} · ${event.optString("event_id")}")
                        }
                        if (ordered.size > 5) actions(body, button("Earlier events", MUTED, (eventPage + 1) * 5 < ordered.size) { eventPage++; redraw() }, button("Newer events", MUTED, eventPage > 0) { eventPage--; redraw() })
                    }
                    caption(body, message, if (failed) WARNING else MUTED)
                    actions(body, button("View Calendar") { StrictCalendarUi.showCalendar(activity, db) }, button(if (busy.get()) "Refreshing…" else "Refresh", CYAN, !busy.get()) { refresh(request(), this) })
                    close()
                }
            }
            screen.show(); refresh(request(), screen)
        }

        private fun metricRow(parent: LinearLayout, left: String, stats: JSONObject, leftKey: String, right: String, rightKey: String) {
            val row = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
            listOf(left to leftKey, right to rightKey).forEachIndexed { index, (title, key) ->
                val cell = column(12).apply {
                    background = bg(); layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                        if (index == 0) rightMargin = dp(4) else leftMargin = dp(4); bottomMargin = dp(8)
                    }
                }
                cell.addView(label(title, 12f, MUTED)); cell.addView(label(if (!stats.has(key) || stats.isNull(key)) "Not available" else stats.opt(key).toString(), 20f, CYAN, true))
                row.addView(cell)
            }; parent.addView(row)
        }
    }
}
