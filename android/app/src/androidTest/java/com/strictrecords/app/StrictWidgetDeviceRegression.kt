package com.strictrecords.app

import android.app.Activity
import android.app.Instrumentation
import android.appwidget.AppWidgetHost
import android.appwidget.AppWidgetHostView
import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Intent
import android.graphics.Bitmap
import android.os.Bundle
import android.os.SystemClock
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.Spinner
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

/** Exercises real framework binding/rendering in a debug-only host. No proof or score fixtures. */
object StrictWidgetDeviceRegression {
    fun run(instrumentation: Instrumentation) {
        val context=instrumentation.targetContext
        val report=JSONObject()
        val results=JSONArray()
        val configurationResults=JSONArray()
        val root=File(context.filesDir,"widget-regression").apply { mkdirs() }
        val manager=AppWidgetManager.getInstance(context)
        val ids=mutableListOf<Int>()
        var host: AppWidgetHost?=null
        var activity: WidgetTestHostActivity?=null
        try {
            check(android.os.Build.VERSION.SDK_INT >= 29)
            instrumentation.uiAutomation.adoptShellPermissionIdentity("android.permission.BIND_APPWIDGET")
            val before=StrictRecordsLocalDb.getInstance(context).getScore(context.let { StrictRecordsLocalDb.getInstance(it).nowDate() },false)
            val screen=instrumentation.startActivitySync(Intent(context,WidgetTestHostActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as WidgetTestHostActivity
            activity=screen
            instrumentation.runOnMainSync { host=AppWidgetHost(screen,61021); host!!.startListening() }
            val snapshot=StrictWidgetUpdates.worker.submit<StrictWidgetSnapshot> { StrictWidgetRepository.invalidate(); StrictWidgetRepository.load(context) }.get(15,TimeUnit.SECONDS)
            check(!snapshot.stale) { "Repository unavailable" }
            check(snapshot.score == before) { "Authoritative score mismatch" }
            for ((kind,provider) in StrictWidgetUpdates.providers) {
                val component=ComponentName(context,provider)
                val info=manager.installedProviders.first { it.provider==component }
                check(info.configure==ComponentName(context,StrictWidgetConfigurationActivity::class.java))
                val id=host!!.allocateAppWidgetId(); ids.add(id)
                val width=if(kind.name.startsWith("S")) 170 else 340
                val height=if(kind.name.startsWith("L")) 400 else 220
                val options=Bundle().apply {
                    putInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH,width); putInt(AppWidgetManager.OPTION_APPWIDGET_MAX_WIDTH,width)
                    putInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT,height); putInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT,height)
                    putParcelableArrayList(AppWidgetManager.OPTION_APPWIDGET_SIZES,arrayListOf(android.util.SizeF(width.toFloat(),height.toFloat())))
                }
                check(manager.bindAppWidgetIdIfAllowed(id,component,options)) { "Framework binding failed: $kind" }
                StrictWidgetStore(context).save(id,StrictWidgetOptions(kind,battery=true))
                lateinit var view: AppWidgetHostView
                instrumentation.runOnMainSync {
                    view=host!!.createView(screen,id,info)
                    screen.frame.removeAllViews()
                    val density=screen.resources.displayMetrics.density
                    screen.frame.addView(view,android.widget.FrameLayout.LayoutParams((width*density).toInt(),(height*density).toInt()))
                }
                StrictWidgetUpdates.worker.submit { StrictWidgetUpdates.refresh(context,intArrayOf(id)) }.get(15,TimeUnit.SECONDS)
                SystemClock.sleep(500); instrumentation.waitForIdleSync()
                instrumentation.runOnMainSync {
                    val texts=texts(view)
                    check(texts.any { it.contains("StrictRoutines") }) { "RemoteViews inflation error: $kind $texts" }
                    check(texts.none { it.contains("Problem loading") }) { "Host error: $kind" }
                    check(view.findViewById<View>(R.id.sw_root) != null) { "Missing widget root $kind" }
                    if(kind.domain=="points") check(texts.any { it.contains("$before / 100 points") }) { "Score text mismatch: $texts" }
                    if(kind.domain=="monitoring") check(texts.none { it.contains("ALL OK") })
                }
                screenshot(instrumentation,File(root,"${kind.name}-dark.png"))
                // Exercise configuration and independent instance settings using the real activity.
                if(kind==StrictWidgetKind.S3) {
                    val config=openConfiguration(instrumentation,id)
                    try {
                        instrumentation.runOnMainSync {
                            check(findOrNull(config.window.decorView,"Show category summaries")==null) { "Small points widget exposes unsupported categories" }
                            check(findOrNull(config.window.decorView,"Compact presentation")==null) { "Small points widget exposes unsupported compact mode" }
                            check(findOrNull(config.window.decorView,"Show score progress") is CheckBox)
                            find(config.window.decorView,"Light theme").let { (it as CheckBox).performClick() }
                            find(config.window.decorView,"Save widget").performClick()
                        }
                        await(instrumentation,"Configuration did not return to its host") { config.isFinishing }
                    } finally {
                        instrumentation.runOnMainSync { if(!config.isFinishing) config.finish() }
                    }
                    instrumentation.waitForIdleSync()
                    check(StrictWidgetStore(context).load(id,kind).light) { "Config did not persist" }
                    val other=ids.first()
                    check(!StrictWidgetStore(context).load(other,StrictWidgetKind.S1).light) { "Settings leaked across instances" }
                    screenshot(instrumentation,File(root,"S3-light.png"))
                    configurationResults.put(JSONObject().put("kind","S3").put("appropriate_controls",true).put("theme_saved",true))
                }
                if(kind==StrictWidgetKind.M2) {
                    checkPointsConfiguration(instrumentation,id,ids.first(),before)
                    configurationResults.put(JSONObject().put("kind","M2").put("live_options",true)
                        .put("save_reopen",true).put("instance_isolation",true))
                }
                if(kind==StrictWidgetKind.L3) {
                    checkBatteryAndCancel(instrumentation,id)
                    configurationResults.put(JSONObject().put("kind","L3").put("battery_preview",true).put("unsaved_cancel",true))
                }
                // Trigger a genuine framework resize/options update, then wait for the provider.
                val resizedHeight=if(kind.name.startsWith("L")) 270 else 150
                options.putInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT,resizedHeight)
                options.putInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT,resizedHeight)
                options.putParcelableArrayList(AppWidgetManager.OPTION_APPWIDGET_SIZES,arrayListOf(android.util.SizeF(width.toFloat(),resizedHeight.toFloat())))
                manager.updateAppWidgetOptions(id,options)
                StrictWidgetUpdates.worker.submit { StrictWidgetUpdates.refresh(context,intArrayOf(id)) }.get(15,TimeUnit.SECONDS)
                instrumentation.runOnMainSync {
                    val density=screen.resources.displayMetrics.density
                    view.layoutParams=view.layoutParams.apply { this.height=(resizedHeight*density).toInt() }
                }
                SystemClock.sleep(300); instrumentation.waitForIdleSync()
                instrumentation.runOnMainSync {
                    if(kind==StrictWidgetKind.S1 && snapshot.tasks.isNotEmpty())
                        check(view.findViewById<View>(R.id.sw_detail)?.visibility==View.VISIBLE) { "Small task lost its submission window after resize" }
                }
                screenshot(instrumentation,File(root,"${kind.name}-resized.png"))
                results.put(JSONObject().put("kind",kind.name).put("bound",true).put("rendered",true).put("resized",true))
            }
            checkGalleryConfiguration(instrumentation,ids.first())
            configurationResults.put(JSONObject().put("screen","gallery").put("toggle_reuses_gallery_views",true)
                .put("placed_instance_refreshed_on_return",true))
            report.put("configuration_checks",configurationResults)
            report.put("widgets",results).put("authoritative_score",before)
            check(StrictRecordsLocalDb.getInstance(context).getScore(snapshot.json.getString("date"),false)==before) { "Read-only widget checks changed points" }
            report.put("score_unchanged",true).put("configuration_persisted",true).put("instance_isolation",true)
                .put("host","Debug framework AppWidgetHost, not the launcher's home-screen grid")
                .put("android_api",android.os.Build.VERSION.SDK_INT).put("success",true)
            val monitor=instrumentation.addMonitor(MainActivity::class.java.name,null,false)
            instrumentation.runOnMainSync { screen.frame.findViewById<View>(R.id.sw_root).performClick() }
            val opened=monitor.waitForActivityWithTimeout(12_000L) as? MainActivity ?: error("Widget tap did not open MainActivity")
            instrumentation.removeMonitor(monitor)
            check(opened.intent.getStringExtra("OPEN_TAB")=="DEVICES") { "Widget opened the wrong tab" }
            instrumentation.runOnMainSync {
                val field=MainActivity::class.java.getDeclaredField("activeTab").apply { isAccessible=true }
                check(field.get(opened)=="DEVICES") { "MainActivity did not navigate to Devices" }
            }
            report.put("tap_navigation","DEVICES")
        } catch (error: Throwable) {
            report.put("success",false).put("error",error.stackTraceToString()).put("widgets",results)
                .put("configuration_checks",configurationResults)
        } finally {
            ids.forEach { id -> runCatching { host?.deleteAppWidgetId(id); StrictWidgetStore(context).remove(id) } }
            instrumentation.runOnMainSync { host?.stopListening(); activity?.finish() }
            instrumentation.uiAutomation.dropShellPermissionIdentity()
            root.resolve("report.json").writeText(report.toString(2))
            instrumentation.finish(if(report.optBoolean("success")) Activity.RESULT_OK else Activity.RESULT_CANCELED,
                Bundle().apply { putString("widget_report",report.toString()) })
        }
    }
    private fun openConfiguration(instrumentation: Instrumentation,id: Int): Activity {
        val context=instrumentation.targetContext
        val activity=instrumentation.startActivitySync(Intent(context,StrictWidgetConfigurationActivity::class.java)
            .setAction(AppWidgetManager.ACTION_APPWIDGET_CONFIGURE).putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID,id)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        try {
            // Wait behind the real onResume repository read, then drain its UI callback.
            StrictWidgetUpdates.worker.submit {}.get(15,TimeUnit.SECONDS)
            instrumentation.waitForIdleSync()
            await(instrumentation,"Configuration preview was not created") { selectedPreview(activity).childCount>0 }
            return activity
        } catch(error: Throwable) {
            instrumentation.runOnMainSync { activity.finish() }
            throw error
        }
    }
    private fun checkPointsConfiguration(instrumentation: Instrumentation,id: Int,otherId: Int,score: Int) {
        val context=instrumentation.targetContext
        val store=StrictWidgetStore(context)
        val otherKind=StrictWidgetUpdates.defaultKind(context,otherId) ?: error("Missing comparison widget")
        val otherBefore=StrictWidgetStore.encode(store.load(otherId,otherKind))
        val before=StrictWidgetStore.encode(store.load(id,StrictWidgetKind.M2))
        val config=openConfiguration(instrumentation,id)
        try {
            await(instrumentation,"Points configuration did not load the authoritative score") {
                selectedPreview(config).findViewById<TextView>(R.id.sw_title)?.text?.toString()=="$score / 100 points"
            }
            instrumentation.runOnMainSync {
                val preview=selectedPreview(config)
                check(preview.findViewById<View>(R.id.sw_progress).visibility==View.VISIBLE)
                check(texts(preview).contains("Wake/sleep · Bath")) { "Category preview missing before toggle" }
                (find(config.window.decorView,"Show score progress") as CheckBox).performClick()
                check(selectedPreview(config).findViewById<View>(R.id.sw_progress).visibility==View.GONE) { "Progress preview ignored its option" }
                (find(config.window.decorView,"Show category summaries") as CheckBox).performClick()
                check(texts(selectedPreview(config)).none { it in setOf("Wake/sleep · Bath","Hourly · Gym","Focus · Deductions") }) { "Category preview ignored its option" }
                (find(config.window.decorView,"Show ledger and reward details") as CheckBox).performClick()
                check(selectedPreview(config).findViewById<View>(R.id.sw_rows).visibility==View.GONE)
                check(selectedPreview(config).findViewById<View>(R.id.sw_detail).visibility==View.GONE)
                check(!(find(config.window.decorView,"Show category summaries") as CheckBox).isEnabled) { "Hidden categories control remains enabled" }
            }
            check(StrictWidgetStore.encode(store.load(id,StrictWidgetKind.M2))==before) { "Live preview saved an unconfirmed draft" }
            check(StrictWidgetStore.encode(store.load(otherId,otherKind))==otherBefore) { "Preview altered another instance" }
            instrumentation.runOnMainSync { find(config.window.decorView,"Save widget").performClick() }
            await(instrumentation,"Points configuration did not finish saving") { config.isFinishing }
        } finally {
            instrumentation.runOnMainSync { if(!config.isFinishing) config.finish() }
        }
        val saved=store.load(id,StrictWidgetKind.M2)
        check(!saved.progress && !saved.categories && !saved.details) { "Points content settings did not persist" }
        check(StrictWidgetStore.encode(store.load(otherId,otherKind))==otherBefore) { "Saving altered another instance" }
        val reopened=openConfiguration(instrumentation,id)
        try {
            instrumentation.runOnMainSync {
                listOf("Show score progress","Show category summaries","Show ledger and reward details").forEach { label ->
                    check(!(find(reopened.window.decorView,label) as CheckBox).isChecked) { "Reopened option lost its value: $label" }
                }
                check(selectedPreview(reopened).findViewById<View>(R.id.sw_progress).visibility==View.GONE)
                check(selectedPreview(reopened).findViewById<View>(R.id.sw_rows).visibility==View.GONE)
                find(reopened.window.decorView,"Cancel").performClick()
            }
            await(instrumentation,"Reopened configuration did not cancel") { reopened.isFinishing }
        } finally {
            instrumentation.runOnMainSync { if(!reopened.isFinishing) reopened.finish() }
        }
    }
    private fun checkBatteryAndCancel(instrumentation: Instrumentation,id: Int) {
        val store=StrictWidgetStore(instrumentation.targetContext)
        val before=StrictWidgetStore.encode(store.load(id,StrictWidgetKind.L3))
        val config=openConfiguration(instrumentation,id)
        try {
            await(instrumentation,"Monitoring configuration did not load its network row") {
                texts(selectedPreview(config)).any { it.contains("Battery ") }
            }
            instrumentation.runOnMainSync {
                (find(config.window.decorView,"Show battery percentage") as CheckBox).performClick()
                check(texts(selectedPreview(config)).none { it.contains("Battery ") }) { "Battery preview did not hide" }
                (find(config.window.decorView,"Show battery percentage") as CheckBox).performClick()
                check(texts(selectedPreview(config)).any { it.contains("Battery ") }) { "Battery preview did not return" }
                (find(config.window.decorView,"Light theme") as CheckBox).performClick()
                find(config.window.decorView,"Cancel").performClick()
            }
            await(instrumentation,"Monitoring draft did not cancel") { config.isFinishing }
            check(StrictWidgetStore.encode(store.load(id,StrictWidgetKind.L3))==before) { "Cancel persisted unsaved monitoring options" }
        } finally {
            instrumentation.runOnMainSync { if(!config.isFinishing) config.finish() }
        }
    }
    private fun checkGalleryConfiguration(instrumentation: Instrumentation,id: Int) {
        val context=instrumentation.targetContext
        val store=StrictWidgetStore(context)
        val original=store.load(id,StrictWidgetKind.S1)
        val gallery=instrumentation.startActivitySync(Intent(context,StrictWidgetConfigurationActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        var editor: Activity?=null
        try {
            StrictWidgetUpdates.worker.submit {}.get(15,TimeUnit.SECONDS)
            instrumentation.waitForIdleSync()
            instrumentation.runOnMainSync {
                val holder=gallery.window.decorView.findViewWithTag<ViewGroup>("widget_gallery_S1")
                    ?: error("Gallery lacks the small-widget preview")
                val rendered=holder.getChildAt(0) ?: error("Gallery preview is empty")
                (find(gallery.window.decorView,"Light theme") as CheckBox).performClick()
                check(gallery.window.decorView.findViewWithTag<ViewGroup>("widget_gallery_S1")===holder) { "Toggle rebuilt gallery holder" }
                check(holder.getChildAt(0)===rendered) { "Toggle unnecessarily rebuilt the gallery's RemoteViews" }
                check(!selectedPreview(gallery).findViewById<View>(R.id.sw_root).isClickable) { "Selected preview executes home-screen navigation" }
            }
            val monitor=instrumentation.addMonitor(StrictWidgetConfigurationActivity::class.java.name,null,false)
            try {
                instrumentation.runOnMainSync { find(gallery.window.decorView,"#$id · ${original.kind.label}").performClick() }
                editor=monitor.waitForActivityWithTimeout(10_000L) ?: error("Gallery did not open instance configuration")
            } finally {
                instrumentation.removeMonitor(monitor)
            }
            val opened=editor ?: error("Missing instance editor")
            StrictWidgetUpdates.worker.submit {}.get(15,TimeUnit.SECONDS)
            instrumentation.waitForIdleSync()
            instrumentation.runOnMainSync {
                val spinner=findSpinner(opened.window.decorView) ?: error("Instance type selector missing")
                val position=(0 until spinner.count).first { spinner.getItemAtPosition(it).toString().startsWith("S2 ·") }
                spinner.setSelection(position)
            }
            await(instrumentation,"Instance type preview did not change") {
                selectedPreview(opened).findViewById<TextView>(R.id.sw_label)?.text?.toString()==StrictWidgetKind.S2.label
            }
            instrumentation.runOnMainSync { find(opened.window.decorView,"Save widget").performClick() }
            await(instrumentation,"Instance type editor did not finish") { opened.isFinishing }
            await(instrumentation,"Gallery retained the previous instance label on return") {
                findOrNull(gallery.window.decorView,"#$id · ${StrictWidgetKind.S2.label}")!=null
            }
            check(store.load(id,StrictWidgetKind.S1).kind==StrictWidgetKind.S2)
        } finally {
            // Restore only this test-allocated instance; never reset other home-screen widgets.
            store.save(id,original)
            instrumentation.runOnMainSync {
                editor?.let { if(!it.isFinishing) it.finish() }
                gallery.finish()
            }
            StrictWidgetUpdates.worker.submit { StrictWidgetUpdates.refresh(context,intArrayOf(id)) }.get(15,TimeUnit.SECONDS)
        }
    }
    private fun selectedPreview(activity: Activity): ViewGroup =
        activity.window.decorView.findViewWithTag<ViewGroup>("widget_selected_preview") ?: error("Selected preview missing")
    private fun await(instrumentation: Instrumentation,message: String,timeoutMs: Long=10_000L,condition: () -> Boolean) {
        val deadline=SystemClock.elapsedRealtime()+timeoutMs
        do {
            var ready=false
            instrumentation.runOnMainSync { ready=condition() }
            if(ready) return
            SystemClock.sleep(50)
        } while(SystemClock.elapsedRealtime()<deadline)
        error(message)
    }
    private fun findSpinner(view: View): Spinner? = if(view is Spinner) view else if(view is ViewGroup)
        (0 until view.childCount).mapNotNull { findSpinner(view.getChildAt(it)) }.firstOrNull() else null
    private fun findOrNull(view: View,text: String): View? = if(view is TextView && view.text.toString()==text) view else if(view is ViewGroup)
        (0 until view.childCount).mapNotNull { findOrNull(view.getChildAt(it),text) }.firstOrNull() else null
    private fun texts(view: View): List<String> = if(view is ViewGroup) (0 until view.childCount).flatMap { texts(view.getChildAt(it)) }
        else if(view is TextView) listOf(view.text.toString()) else emptyList()
    private fun find(view: View,text: String): View = if(view is TextView && view.text.toString()==text) view
        else if(view is ViewGroup) (0 until view.childCount).mapNotNull { runCatching { find(view.getChildAt(it),text) }.getOrNull() }.firstOrNull() ?: error("Missing control $text")
        else error("Missing control $text")
    private fun screenshot(instrumentation: Instrumentation,file: File) {
        val foreground=instrumentation.uiAutomation.rootInActiveWindow?.packageName?.toString()
        check(foreground==instrumentation.targetContext.packageName) { "Screenshot obscured by $foreground" }
        val bitmap=instrumentation.uiAutomation.takeScreenshot() ?: error("Screenshot unavailable")
        file.outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG,100,it) }; bitmap.recycle()
    }
}
