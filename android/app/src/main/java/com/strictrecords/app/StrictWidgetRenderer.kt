package com.strictrecords.app

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.SizeF
import android.view.View
import android.widget.RemoteViews

/** RemoteViews-safe views only. No animations, live counters, bitmaps or side-effect actions. */
object StrictWidgetRenderer {
    /** Use exact launcher sizes on Android 12+, and paired orientation layouts on older hosts. */
    fun forHost(context: Context,id: Int,options: StrictWidgetOptions,snapshot: StrictWidgetSnapshot,host: Bundle): RemoteViews {
        if(Build.VERSION.SDK_INT >= 31) {
            val sizes=host.getParcelableArrayList<SizeF>(AppWidgetManager.OPTION_APPWIDGET_SIZES).orEmpty()
                .filter { it.width >= 80 && it.height >= 80 }.distinct().take(8)
            if(sizes.isNotEmpty()) return RemoteViews(sizes.associateWith { size ->
                render(context,id,options,snapshot,size.width.toInt(),size.height.toInt())
            })
        }
        val minWidth=host.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH,if(options.kind.name.startsWith("S")) 160 else 320)
        val maxWidth=host.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_WIDTH,minWidth)
        val minHeight=host.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT,if(options.kind.name.startsWith("L")) 340 else 180)
        val maxHeight=host.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT,minHeight)
        val portrait=render(context,id,options,snapshot,minWidth,maxHeight)
        return if(minWidth==maxWidth && minHeight==maxHeight) portrait else
            RemoteViews(render(context,id,options,snapshot,maxWidth,minHeight),portrait)
    }
    fun render(context: Context, id: Int, options: StrictWidgetOptions, snapshot: StrictWidgetSnapshot,
               width: Int = 300, height: Int = 220): RemoteViews {
        val k = options.kind
        val fontScale=context.resources.configuration.fontScale.coerceAtLeast(1f)
        val small = k.name.startsWith("S") || width < 220 || height < 180 ||
            (!k.name.startsWith("L") && height < 250 && fontScale > 1.25f)
        val large = k.name.startsWith("L") && height >= 260 && !small
        val layout = if (small) R.layout.strict_widget_small else if (large) R.layout.strict_widget_large else R.layout.strict_widget_medium
        val views = RemoteViews(context.packageName, layout)
        val primary = Color.parseColor(if (options.light) "#102536" else "#EDF6FF")
        val muted = Color.parseColor(if (options.light) "#36546D" else "#ACC4DB")
        val accent = Color.parseColor(if (options.light) "#006D58" else "#00DCA0")
        views.setInt(R.id.sw_root,"setBackgroundResource",if(options.light) R.drawable.strict_widget_light else R.drawable.strict_widget_dark)
        if(small) {
            val density=context.resources.displayMetrics.density
            views.setViewPadding(R.id.sw_root,(10*density).toInt(),(8*density).toInt(),(10*density).toInt(),(8*density).toInt())
        }
        views.setTextViewText(R.id.sw_brand,"◆ StrictRoutines")
        views.setTextViewText(R.id.sw_label,k.label)
        views.setTextColor(R.id.sw_brand,primary)
        views.setTextColor(R.id.sw_label,muted)
        views.setTextColor(R.id.sw_title,primary)
        views.setTextColor(R.id.sw_detail,muted)
        views.setTextColor(R.id.sw_value,accent)
        views.setTextColor(R.id.sw_fresh,muted)
        val now = snapshot.now
        val tasks = snapshot.tasks
        val next = StrictWidgetTime.next(tasks,now)
        fun window(t: StrictWidgetTask) = if (t.start > 0 && t.end > t.start)
            "${ScheduleClock.display(t.start,snapshot.zone)}–${ScheduleClock.display(t.end,snapshot.zone)}" else "Window unavailable"
        fun countdown(t: StrictWidgetTask): String = when(t.state(now)) {
            "OPEN" -> "Closes in ${StrictWidgetTime.remaining(now,t.end)}"
            "UPCOMING" -> "Opens in ${StrictWidgetTime.remaining(now,t.start)}"
            else -> t.state(now).replace('_',' ')
        }
        var title = "Open StrictRoutines"
        var detail = "No valid saved data"
        var value = "Refresh in app"
        var valueColor=accent
        val rows = mutableListOf<Pair<String,String>>()
        when(k.domain) {
            "tasks" -> {
                val selected = next ?: tasks.lastOrNull()
                title = selected?.let { StrictWidgetTime.title(it) } ?: "No eligible task"
                detail = selected?.let { if(small || k==StrictWidgetKind.M1) window(it) else "${window(it)} · ${it.state(now)}" } ?: "Open Tasks to review today's schedule"
                value = selected?.let { countdown(it) } ?: "Nothing scheduled"
                valueColor=when(selected?.state(now)) {
                    "UPCOMING" -> Color.parseColor(if(options.light) "#00718A" else "#36CCF5")
                    "MISSED","REJECTED","REJECTED_LATE" -> Color.parseColor(if(options.light) "#B32336" else "#FF8292")
                    else -> accent
                }
                val relevant = StrictWidgetTime.rows(tasks,now,k==StrictWidgetKind.L1)
                relevant.forEach { task ->
                    val status=if(k==StrictWidgetKind.L1) task.state(now) else countdown(task)
                    rows.add(StrictWidgetTime.title(task) to "Proof ${window(task)} · $status${if(task.interval.isNotBlank()) "\n${task.interval}" else ""}")
                }
            }
            "points" -> {
                title = "${snapshot.score ?: "—"} / ${snapshot.json.optInt("max",100)} points"
                detail = "Confirmed ledger"
                value = if(k == StrictWidgetKind.S3) "Today's ledger" else snapshot.json.optString("reward","Reward state unknown")
                if(options.categories) snapshot.json.optJSONObject("categories")?.let { b ->
                    listOf(Triple("wake_up","bath","Wake/sleep · Bath"),Triple("hourly","gym","Hourly · Gym"),Triple("focus","penalties","Focus · Deductions")).forEach { (left,right,label) ->
                        rows.add(label to "${b.optInt(left)} pts · ${b.optInt(right)} pts")
                    }
                }
                rows.add("GYM Mode" to snapshot.json.optString("gym","Status unknown"))
            }
            "monitoring" -> {
                title = snapshot.json.optString("monitoring","Status unknown")
                detail = "Accessibility: ${snapshot.json.optString("accessibility","Unknown")}"
                value = "Usage Access: ${snapshot.json.optString("usage","Unknown")}"
                if(snapshot.json.optString("usage")!="Granted") valueColor=Color.parseColor(if(options.light) "#835C00" else "#FFD16B")
                rows.add("Network" to (snapshot.json.optString("network","Unknown")+
                    if(options.battery) " · Battery ${snapshot.json.optString("battery","Unknown")}" else ""))
                rows.add("Companions" to snapshot.json.optString("companions","PC / browser: live status unknown"))
                rows.add("Enforcement" to "Service status does not confirm an individual block")
            }
        }
        if(snapshot.stale) {
            value = "STALE · Open app to refresh"
            valueColor=Color.parseColor(if(options.light) "#835C00" else "#FFD16B")
            detail = "Cached ${snapshot.json.optString("date","date unknown")} · Current window state unavailable"
            if(k.domain == "monitoring") { title="Live status unknown"; detail="Cached connection state is unavailable"; rows.clear() }
        }
        views.setTextViewText(R.id.sw_title,title)
        views.setTextViewText(R.id.sw_detail,detail)
        views.setTextViewText(R.id.sw_value,value)
        views.setTextColor(R.id.sw_value,valueColor)
        views.setViewVisibility(R.id.sw_detail,if(options.details && (!options.compact || k.domain=="tasks") && height >= 135) View.VISIBLE else View.GONE)
        views.setViewVisibility(R.id.sw_label,if(small && height < 190) View.GONE else View.VISIBLE)
        views.setInt(R.id.sw_fresh,"setMaxLines",if(small) 1 else 2)
        views.setInt(R.id.sw_value,"setMaxLines",if(small) 1 else 3)
        if(small) {
            views.setInt(R.id.sw_title,"setMaxLines",1)
            views.setTextViewTextSize(R.id.sw_brand,android.util.TypedValue.COMPLEX_UNIT_SP,11f)
            views.setTextViewTextSize(R.id.sw_detail,android.util.TypedValue.COMPLEX_UNIT_SP,10f)
            views.setTextViewTextSize(R.id.sw_fresh,android.util.TypedValue.COMPLEX_UNIT_SP,9f)
        }
        views.setViewVisibility(R.id.sw_progress,if(options.progress && k.domain == "points" && snapshot.score != null) View.VISIBLE else View.GONE)
        views.setProgressBar(R.id.sw_progress,snapshot.json.optInt("max",100), (snapshot.score ?: 0).coerceAtLeast(0),false)
        val stamp = if(snapshot.at > 0) ScheduleClock.display(snapshot.at,snapshot.zone) else "never"
        views.setTextViewText(R.id.sw_fresh,"${if(snapshot.stale) "Stale" else "Updated"} $stamp · ${if(k.domain == "tasks") "Open Tasks" else if(k.domain == "points") "Open Rewards" else "Open Devices"}")
        views.setTextViewTextSize(R.id.sw_title,android.util.TypedValue.COMPLEX_UNIT_SP,if(small) 13f else 19f)
        views.setTextViewTextSize(R.id.sw_value,android.util.TypedValue.COMPLEX_UNIT_SP,if(small) 14f else if(k == StrictWidgetKind.M1) 20f else 14f)
        if(!small) {
            views.removeAllViews(R.id.sw_rows)
            val rowPitch = if(k.domain in setOf("points","monitoring")) 43 else 70
            val rowLimit = if(options.compact) 1 else if(large) ((height-160)/(rowPitch*fontScale)).toInt().coerceIn(1,6) else ((height-45)/(56*fontScale)).toInt().coerceIn(1,3)
            rows.take(rowLimit).forEach { (name,body) ->
                val row = RemoteViews(context.packageName,R.layout.strict_widget_row)
                row.setTextViewText(R.id.sw_row_title,name)
                row.setTextViewText(R.id.sw_row_body,body)
                row.setTextColor(R.id.sw_row_title,primary)
                row.setTextColor(R.id.sw_row_body,muted)
                views.addView(R.id.sw_rows,row)
            }
            views.setViewVisibility(R.id.sw_rows,if(options.details && rows.isNotEmpty()) View.VISIBLE else View.GONE)
        }
        val intent = Intent(context,MainActivity::class.java).apply {
            data=Uri.parse("strictroutines://widget/$id/${k.name}")
            putExtra("OPEN_TAB",k.tab)
            flags=Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        views.setOnClickPendingIntent(R.id.sw_root,PendingIntent.getActivity(context,id,intent,PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
        views.setContentDescription(R.id.sw_root,"StrictRoutines ${k.label}. $title. $detail. $value. Updated $stamp. Tap to open ${k.tab.lowercase()}.")
        return views
    }
}
