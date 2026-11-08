package com.strictrecords.app

import android.app.AlarmManager
import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** Preserve the existing component and widget IDs as the upcoming-tasks widget. */
open class StrictRecordsWidgetProvider : AppWidgetProvider() {
    open val kind = StrictWidgetKind.M1
    override fun onReceive(context: Context, intent: Intent) {
        val pending = goAsync()
        val released=AtomicBoolean(false)
        val handler=Handler(Looper.getMainLooper())
        val release=Runnable { if(released.compareAndSet(false,true)) pending.finish() }
        handler.postDelayed(release,8_000L)
        StrictWidgetUpdates.worker.execute {
            try {
                if (intent.action == StrictWidgetUpdates.ACTION_REFRESH) StrictWidgetUpdates.refresh(context)
                else super.onReceive(context,intent)
            } catch (_: Exception) { /* Isolate launcher and data errors. */ }
            finally { handler.removeCallbacks(release); release.run() }
        }
    }
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) { StrictWidgetUpdates.refresh(context,ids) }
    override fun onAppWidgetOptionsChanged(context: Context, manager: AppWidgetManager, id: Int, options: Bundle) {
        StrictWidgetUpdates.refresh(context,intArrayOf(id))
    }
    override fun onDeleted(context: Context, ids: IntArray) {
        ids.forEach { StrictWidgetStore(context).remove(it); StrictWidgetUpdates.forget(it) }
        StrictWidgetUpdates.reschedule(context)
    }
    override fun onRestored(context: Context, oldIds: IntArray, newIds: IntArray) {
        oldIds.zip(newIds.toList()).forEach { (old,new) -> StrictWidgetStore(context).move(old,new,kind) }
        StrictWidgetUpdates.refresh(context,newIds)
    }
    override fun onDisabled(context: Context) { StrictWidgetUpdates.reschedule(context) }
    companion object { fun updateAllWidgets(context: Context) = StrictWidgetUpdates.request(context) }
}
class StrictNextTaskWidget : StrictRecordsWidgetProvider() { override val kind = StrictWidgetKind.S1 }
class StrictWindowWidget : StrictRecordsWidgetProvider() { override val kind = StrictWidgetKind.S2 }
class StrictPointsWidget : StrictRecordsWidgetProvider() { override val kind = StrictWidgetKind.S3 }
class StrictMonitoringWidget : StrictRecordsWidgetProvider() { override val kind = StrictWidgetKind.S4 }
class StrictRewardWidget : StrictRecordsWidgetProvider() { override val kind = StrictWidgetKind.M2 }
class StrictScheduleWidget : StrictRecordsWidgetProvider() { override val kind = StrictWidgetKind.L1 }
class StrictCategoriesWidget : StrictRecordsWidgetProvider() { override val kind = StrictWidgetKind.L2 }
class StrictDevicesWidget : StrictRecordsWidgetProvider() { override val kind = StrictWidgetKind.L3 }

object StrictWidgetUpdates {
    const val ACTION_REFRESH = "com.strictrecords.app.WIDGET_REFRESH"
    val worker = Executors.newSingleThreadExecutor { task -> Thread(task,"StrictWidgetRead").apply { isDaemon=true } }
    private val handler = Handler(Looper.getMainLooper())
    private val domains = mutableSetOf<String>()
    private var queued = false
    private val rendered = mutableMapOf<Int,String>()
    fun forget(id: Int) { rendered.remove(id) }
    val providers = linkedMapOf(
        StrictWidgetKind.S1 to StrictNextTaskWidget::class.java, StrictWidgetKind.S2 to StrictWindowWidget::class.java,
        StrictWidgetKind.S3 to StrictPointsWidget::class.java, StrictWidgetKind.S4 to StrictMonitoringWidget::class.java,
        StrictWidgetKind.M1 to StrictRecordsWidgetProvider::class.java, StrictWidgetKind.M2 to StrictRewardWidget::class.java,
        StrictWidgetKind.L1 to StrictScheduleWidget::class.java, StrictWidgetKind.L2 to StrictCategoriesWidget::class.java,
        StrictWidgetKind.L3 to StrictDevicesWidget::class.java)
    fun instances(context: Context): Map<Int,StrictWidgetKind> {
        val manager = AppWidgetManager.getInstance(context)
        return providers.flatMap { (kind,clazz) -> manager.getAppWidgetIds(ComponentName(context,clazz)).map { it to kind } }.toMap()
    }
    fun defaultKind(context: Context,id: Int): StrictWidgetKind? {
        val provider = AppWidgetManager.getInstance(context).getAppWidgetInfo(id)?.provider ?: return null
        return providers.entries.firstOrNull { ComponentName(context,it.value) == provider }?.key
    }
    /** Coalesce sync/config/ledger bursts. No DB read or executor work on the caller thread. */
    fun request(context: Context, domain: String = "all") {
        val app = context.applicationContext
        handler.post {
            domains.add(domain)
            if (!queued) {
                queued=true
                handler.postDelayed({
                    val selected=domains.toSet(); domains.clear(); queued=false
                    worker.execute { runCatching { StrictWidgetRepository.invalidate(); refresh(app,domains=selected) } }
                },1500L)
            }
        }
    }
    fun refresh(context: Context, ids: IntArray? = null, domains: Set<String> = setOf("all")) {
        val all = instances(context)
        if (all.isEmpty()) { cancel(context); return }
        val store = StrictWidgetStore(context)
        val selected=all.filter { (id,fallback) -> (ids == null || id in ids) &&
            ("all" in domains || store.load(id,fallback).kind.domain in domains) }
        if(selected.isEmpty()) return
        val snapshot=StrictWidgetRepository.load(context)
        val manager=AppWidgetManager.getInstance(context)
        selected.forEach { (id,fallback) -> runCatching {
            val o=manager.getAppWidgetOptions(id)
            val width=o.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH,if(fallback.name.startsWith("S")) 160 else 320)
            val height=o.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT,if(fallback.name.startsWith("L")) 340 else 180)
            val maxWidth=o.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_WIDTH,width)
            val maxHeight=o.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT,height)
            val config=store.load(id,fallback)
            val keys=when(config.kind.domain) {
                "points" -> listOf("date","score","max","categories","reward","gym")
                "monitoring" -> listOf("monitoring","accessibility","usage","network","companions","battery")
                else -> listOf("date","tasks")
            }
            val cadence=if(config.kind.domain=="tasks") 60_000L else 30*60_000L
            val signature="${StrictWidgetStore.encode(config)}|$width|$height|$maxWidth|$maxHeight|${o.get(AppWidgetManager.OPTION_APPWIDGET_SIZES)}|${snapshot.stale}|${snapshot.now/cadence}|"+
                keys.joinToString("|") { snapshot.json.opt(it).toString() }
            if(ids != null || rendered[id] != signature) {
                manager.updateAppWidget(id,StrictWidgetRenderer.forHost(context,id,config,snapshot,o))
                rendered[id]=signature
            }
        } }
        schedule(context,all,snapshot)
    }
    fun reschedule(context: Context) {
        val all=instances(context)
        if(all.isEmpty()) cancel(context) else schedule(context,all,StrictWidgetRepository.load(context))
    }
    private fun alarm(context: Context) = PendingIntent.getBroadcast(context,9107,
        Intent(context,StrictRecordsWidgetProvider::class.java).setAction(ACTION_REFRESH),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    private fun cancel(context: Context) { context.getSystemService(AlarmManager::class.java)?.cancel(alarm(context)) }
    private fun schedule(context: Context, all: Map<Int,StrictWidgetKind>, snapshot: StrictWidgetSnapshot) {
        val store=StrictWidgetStore(context)
        val countdown=all.any { (id,k) -> store.load(id,k).kind.domain=="tasks" }
        val delay=if(snapshot.stale) 30*60_000L else StrictWidgetTime.nextRefresh(snapshot.now,snapshot.tasks,countdown)-snapshot.now
        // Non-wakeup and inexact; same PendingIntent replaces the previous alarm. Doze can delay it.
        context.getSystemService(AlarmManager::class.java)?.setWindow(AlarmManager.RTC,System.currentTimeMillis()+delay,10*60_000L,alarm(context))
    }
}
