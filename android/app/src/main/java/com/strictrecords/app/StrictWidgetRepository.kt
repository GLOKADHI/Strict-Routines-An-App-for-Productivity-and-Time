package com.strictrecords.app

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import org.json.JSONArray
import org.json.JSONObject

data class StrictWidgetSnapshot(val json: JSONObject, val stale: Boolean = false) {
    val at get() = json.optLong("at")
    val now get() = json.optLong("now", at)
    val zone get() = json.optString("zone", "Asia/Kolkata")
    val score get() = (json.opt("score") as? Number)?.toInt()
    val tasks: List<StrictWidgetTask> get() {
        val a = json.optJSONArray("tasks") ?: return emptyList()
        return StrictWidgetTime.relevant((0 until a.length()).mapNotNull { i -> runCatching { a.getJSONObject(i).let {
            StrictWidgetTask(it.getString("id"), it.getString("title"), it.getLong("start"), it.getLong("end"),
                it.getString("status"), it.optString("interval"), it.optBoolean("eligible", true))
        } }.getOrNull() })
    }
}

/** One bounded, local, read-only snapshot shared by an update batch. No sync or policy mutations. */
object StrictWidgetRepository {
    private var recent: StrictWidgetSnapshot? = null
    private var recentAt = 0L
    fun invalidate() { recent=null }
    fun load(context: Context): StrictWidgetSnapshot {
        val elapsed=android.os.SystemClock.elapsedRealtime()
        recent?.takeIf { elapsed-recentAt in 0L..2_000L }?.let { return it }
        val store = StrictWidgetStore(context)
        return try {
            val db = StrictRecordsLocalDb.getInstance(context)
            val now = db.authoritativeNowMs()
            val date = db.nowDate()
            val zone = db.scheduleTimezone(date)
            val tasks = JSONArray()
            // Do not call getDailyActivities/getDayState: those reconcile and persist business state.
            db.readableDatabase.rawQuery("SELECT activity_id,type,title,scheduled_time,window_start,window_end,status,details_json,notification_time FROM scheduled_activities WHERE date=? AND status != 'CANCELLED' ORDER BY scheduled_time LIMIT 96", arrayOf(date)).use { c ->
                while (c.moveToNext()) {
                    val details = runCatching { JSONObject(c.getString(7)) }.getOrDefault(JSONObject())
                    if(details.optBoolean("is_demo") || details.optBoolean("is_dev_mode") || details.optBoolean("demo")) continue
                    val start = db.parseIsoToMs(c.getString(4))
                    val end = db.parseIsoToMs(c.getString(5))
                    var interval = ""
                    if (c.getString(1) == "BATH") {
                        interval = runCatching {
                            val bath = BathScheduleRecord.fromSaved(date,c.getString(0),c.getString(3),c.getString(4),c.getString(5),c.getString(8),details,zone)
                            "Bath ${bath.activityIntervalDisplay}\nAssigned attendance ${bath.heldHourlySlotDisplay}"
                        }.getOrDefault("Bath activity interval unavailable")
                    } else if (c.getString(1) == "GYM") {
                        // Use persisted activity endpoints only; never infer activity start from proof opening.
                        val planned = db.parseIsoToMs(c.getString(3))
                        val finish = details.optLong("expected_finish_ms", 0L).takeIf { it > planned }
                            ?: details.optString("reserved_end").takeIf { it.isNotBlank() }?.let { db.parseIsoToMs(it) }
                        interval = if (planned > 0 && finish != null && finish > planned)
                            "Workout ${ScheduleClock.display(planned,zone)}–${ScheduleClock.display(finish,zone)}"
                        else if (planned > 0) "Workout starts ${ScheduleClock.display(planned,zone)} · Finish unavailable" else "Workout interval unavailable"
                    }
                    val saved = c.getString(6).uppercase(java.util.Locale.US)
                    // These states represent blocked/held eligibility, not an open submission window.
                    val eligible = saved !in setOf("BLOCKED_WAKEUP","SKIPPED_PRE_GATE","EXCUSED","SKIPPED","CANCELLED") &&
                        !details.optBoolean("travel_hold") && !details.optBoolean("movie_hold") && !details.optBoolean("submission_locked")
                    val status = when {
                        saved in setOf("APPROVED","UNDER_REVIEW","CLOSED","EXPIRED","MISSED") -> saved
                        !eligible -> if (saved == "EXCUSED") saved else "PENDING"
                        else -> saved
                    }
                    tasks.put(JSONObject().put("id",c.getString(0)).put("title",c.getString(2)).put("start",start)
                        .put("end",end).put("status",status).put("eligible",eligible).put("interval",interval))
                }
            }
            val score = db.getScore(date, false)
            val gymActive = GymPolicyClient.isMusicAllowed(db, now)
            val decisions = DisciplineScoringRules.CONFIGURED_RESTRICTED_APPS.distinctBy { it.displayName }.map { target ->
                DisciplineScoringRules.evaluateRestrictionDecision(score,target.displayName,appLocks=db.getRemoteAppLocks(),isGymModeActive=gymActive)
            }
            val next = decisions.filter { it.isRestricted }.minByOrNull { it.remainingPoints }
            val reward = if (next != null) "${next.appName}: ${next.remainingPoints} points to ${next.requiredScore} · Restricted"
                else "Score privileges met at update · Check Rewards for current access"
            val accessibility = StrictAccessibilityService.activeInstance != null
            val usage = HardwareDeviceVerifier.hasUsageStatsPermission(context)
            val monitoring = when {
                FinancialSafePause.isPauseActive(context) -> "Paused for financial app"
                !TargetAppMonitoringPolicy.hasUsageConsent(context) -> "Off · Consent required"
                TargetAppMonitoringPolicy.selectedTargetIds(context).isEmpty() -> "Off · No selected apps"
                !accessibility && !usage -> "Permission required"
                !StrictEnforcementService.isServiceRunning() -> "Background monitor stopped"
                accessibility -> "Accessibility connected · Monitor running"
                usage -> "Usage fallback · Monitor running"
                else -> "Unknown"
            }
            val connectivity = context.getSystemService(ConnectivityManager::class.java)
            val caps = connectivity?.getNetworkCapabilities(connectivity.activeNetwork)
            val network = if (caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED) == true) "Internet validated" else "Offline or unvalidated"
            val battery = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
            val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
            val data = JSONObject().put("at",System.currentTimeMillis()).put("now",now).put("date",date).put("zone",zone)
                .put("score",score).put("max",100).put("categories",db.getPointsBreakdown(date,false)).put("tasks",tasks)
                .put("reward",reward).put("monitoring",monitoring).put("accessibility",if(accessibility) "Connected" else "Not connected")
                .put("usage",if(usage) "Granted" else "Not granted").put("network",network)
                .put("companions","PC / browser: live status unknown")
                .put("gym",if(gymActive) "Active at update · Open Rewards to check live lease" else "GYM music not confirmed active")
                .put("battery",if(level >= 0 && scale > 0) "${level * 100 / scale}%" else "Unknown")
            store.cache(data)
            StrictWidgetSnapshot(data).also { recent=it; recentAt=elapsed }
        } catch (_: Exception) {
            // Cached values are visibly stale; do not imply that cached grants or services remain active.
            val cached = store.cached() ?: JSONObject()
            StrictWidgetSnapshot(cached, stale=true)
        }
    }
}
