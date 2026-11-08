package com.strictrecords.app

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Looper
import android.util.Log
import android.app.Notification
import java.util.Calendar
import java.util.Locale

class WakeUpAlarmReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        val db = StrictRecordsLocalDb.getInstance(context)
        val today = db.nowDate()
        val hourlyId = intent.getStringExtra("ACTIVITY_ID").orEmpty()
        if (hourlyId.isNotBlank() && action in listOf(ACTION_ACTIVITY_PRE_OPENING, ACTION_ACTIVITY_OPENED, ACTION_ACTIVITY_WARN1M, ACTION_ACTIVITY_CLOSED, ACTION_HOURLY_CHECKPOINT)) {
            val activity = db.getDailyActivities(today).firstOrNull { it.activityId == hourlyId } ?: return
            val start = db.parseIsoToMs(activity.windowStart)
            val end = db.parseIsoToMs(activity.windowEnd)
            if ((activity.type == "HOURLY" && !activity.submissionEligible) ||
                activity.status in listOf("EXCUSED", "CANCELLED", "SUBMITTED", "VALIDATING", "VERIFIED", "COMPLETED", "PENDING_SYNC", "SKIPPED", "REJECTED")) return
            if (intent.hasExtra("WINDOW_END_MS") && intent.getLongExtra("WINDOW_END_MS", -1) != end) return
            if (intent.hasExtra("WINDOW_START_MS") && !AlarmScheduleRules.matchesCurrentWindow(
                    intent.getLongExtra("WINDOW_START_MS", -1), intent.getLongExtra("WINDOW_END_MS", -1), start, end)) return
            val event = when (action) { ACTION_ACTIVITY_PRE_OPENING -> "OPENING"; ACTION_ACTIVITY_WARN1M -> "WARN1M"; ACTION_ACTIVITY_CLOSED -> "CLOSED"; else -> "OPENED" }
            val nowMs = db.authoritativeNowMs()
            val isGymOrTravel = activity.type in listOf("GYM", "TRAVEL") || hourlyId.contains("GYM", ignoreCase = true) || hourlyId.contains("TRAVEL", ignoreCase = true)
            val preOffset = if (isGymOrTravel) 5 * 60_000L else 60_000L
            val isEligible = when (event) {
                "OPENING" -> nowMs in (start - preOffset) until start
                "OPENED" -> nowMs in start until end
                "WARN1M" -> nowMs in (end - 60_000L) until end
                "CLOSED" -> nowMs in end until (end + 120_000L)
                else -> false
            }
            if (!isEligible) return
        }

        fun eventNotificationKey(actId: String,event: String): String {
            val act=db.getDailyActivities(today).firstOrNull { it.activityId == actId }
            return if(act == null) "$actId-$event" else db.notificationKey(actId,event,act.windowStart,act.windowEnd,act.type)
        }

        when (action) {
            ACTION_ACTIVITY_PRE_OPENING -> {
                val title = intent.getStringExtra("ACTIVITY_TITLE") ?: "Scheduled Check"
                val actId = intent.getStringExtra("ACTIVITY_ID") ?: ""
                val notifKey = eventNotificationKey(actId,"OPENING")
                val isGym = actId.contains("GYM", ignoreCase = true)
                val isTravel = actId.contains("TRAVEL", ignoreCase = true)
                if (db.markNotificationSent(notifKey, actId, if (isGym || isTravel) "OPENING_5M" else "OPENING_1M")) {
                    val notifTitle = when {
                        isGym -> "STRICTROUTINES — GYM OPENS IN 5 MINUTES"
                        isTravel -> "STRICTROUTINES — TRAVEL OPENS IN 5 MINUTES"
                        else -> "STRICTROUTINES — OPENS IN 1 MINUTE"
                    }
                    val notifMsg = when {
                        actId.contains("BATH", ignoreCase = true) -> db.getDailyActivities(today)
                            .firstOrNull { it.activityId == actId }?.let { db.getSavedBathSchedule(it).reminderMessage("OPENING") }
                            ?: return
                        isGym -> "Gym proof submission opens in 5 minutes."
                        isTravel -> "Travel proof submission opens in 5 minutes."
                        else -> "Next activity opens in 1 minute. ($title)"
                    }
                    postDisciplineNotification(
                        context,
                        3001,
                        notifTitle,
                        notifMsg,
                        if (actId.contains("HOURLY")) "HOURLY_CHECK" else "ACTIVITY_OPENING",
                        actId,
                        intent.getStringExtra("HOUR_SLOT")
                    )
                }
            }
            ACTION_ACTIVITY_OPENED -> {
                val title = intent.getStringExtra("ACTIVITY_TITLE") ?: "Scheduled Check"
                val slot = intent.getStringExtra("HOUR_SLOT")
                val actId = intent.getStringExtra("ACTIVITY_ID") ?: ""
                val notifKey = eventNotificationKey(actId,"OPENED")
                val isWakeUp = actId.contains("WAKEUP", ignoreCase = true) || actId.contains("WAKE_UP", ignoreCase = true)
                val wakeUpStatus = if (isWakeUp) db.getWakeUpFlowStatus(today) else null
                val isWakeUpDone = wakeUpStatus?.optBoolean("all_completed", false) == true ||
                    (wakeUpStatus?.optBoolean("step1_camera", false) == true &&
                     wakeUpStatus?.optBoolean("step2_bluetooth", false) == true &&
                     wakeUpStatus?.optBoolean("step3_motivation_track", false) == true)
                val act = db.getDailyActivities(today).firstOrNull { it.activityId == actId }
                val alreadySubmitted = isWakeUpDone || act?.status in listOf("SUBMITTED", "VALIDATING", "VERIFIED", "COMPLETED", "EXCUSED", "CANCELLED")
                val nowMs = db.authoritativeNowMs()
                val windowEnd = act?.let { db.parseIsoToMs(it.windowEnd) } ?: intent.getLongExtra("WINDOW_END_MS", Long.MAX_VALUE)
                val isExpired = nowMs >= windowEnd
                if (!alreadySubmitted && !isExpired && db.markNotificationSent(notifKey, actId, "OPENED")) {
                    val isHourly = !isWakeUp && actId.contains("HOURLY", ignoreCase = true)
                    postDisciplineNotification(
                        context,
                        3002,
                        if (isWakeUp) "STRICTROUTINES — WAKE-UP VERIFICATION OPEN" else "STRICTROUTINES — ACTIVITY OPEN",
                        if (act?.type == "BATH") db.getSavedBathSchedule(act).reminderMessage("OPENED")
                        else if (isWakeUp) "Complete your 3-step Wake-Up Verification now." else "Your activity is now open. ($title)",
                        when {
                            isWakeUp -> "WAKEUP_CHALLENGE"
                            isHourly -> "HOURLY_CHECK"
                            else -> "ACTIVITY_OPEN"
                        },
                        actId,
                        slot
                    )
                }
            }
            ACTION_ACTIVITY_WARN1M -> {
                val slot = intent.getStringExtra("HOUR_SLOT")
                val actId = intent.getStringExtra("ACTIVITY_ID") ?: ""
                val notifKey = eventNotificationKey(actId,"WARN1M")
                val isWakeUp = actId.contains("WAKEUP", ignoreCase = true) || actId.contains("WAKE_UP", ignoreCase = true)
                val alreadySubmitted = if (isWakeUp) {
                    db.getWakeUpFlowStatus(today).optBoolean("all_completed", false)
                } else {
                    db.getDailyActivities(today).any {
                        it.activityId == actId && it.status in listOf("SUBMITTED", "VALIDATING", "VERIFIED", "COMPLETED")
                    }
                }
                if (!alreadySubmitted && db.markNotificationSent(notifKey, actId, "WARN_1M")) {
                    val bath = db.getDailyActivities(today).firstOrNull { it.activityId == actId && it.type == "BATH" }
                    postDisciplineNotification(
                        context,
                        3003,
                        "STRICTROUTINES — 1 MINUTE REMAINING",
                        if (bath != null) db.getSavedBathSchedule(bath).reminderMessage("WARN1M")
                        else if (isWakeUp) "1 minute left in Wake-Up Verification window!" else "1 minute remaining in activity window. Submit now!",
                        when {
                            isWakeUp -> "WAKEUP_CHALLENGE"
                            actId.contains("HOURLY", ignoreCase = true) -> "HOURLY_CHECK"
                            else -> "ACTIVITY_EXPIRING"
                        },
                        actId,
                        slot
                    )
                }
            }
            ACTION_ACTIVITY_CLOSED -> {
                val slot = intent.getStringExtra("HOUR_SLOT")
                val actId = intent.getStringExtra("ACTIVITY_ID") ?: ""
                val notifKey = eventNotificationKey(actId,"CLOSED")
                val isWakeUp = actId.contains("WAKEUP", ignoreCase = true) || actId.contains("WAKE_UP", ignoreCase = true)
                val alreadySubmitted = if (isWakeUp) {
                    db.getWakeUpFlowStatus(today).optBoolean("all_completed", false)
                } else {
                    db.getDailyActivities(today).any {
                        it.activityId == actId && it.status in listOf("SUBMITTED", "VALIDATING", "VERIFIED", "COMPLETED")
                    }
                }
                if (!alreadySubmitted && db.markNotificationSent(notifKey, actId, "CLOSED")) {
                    val bath = db.getDailyActivities(today).firstOrNull { it.activityId == actId && it.type == "BATH" }
                    postDisciplineNotification(
                        context,
                        3004,
                        "STRICTROUTINES — WINDOW CLOSED",
                        if (bath != null) db.getSavedBathSchedule(bath).reminderMessage("CLOSED") else "Activity window closed.",
                        if (isWakeUp) "WAKEUP_CHALLENGE" else "ACTIVITY_CLOSED",
                        actId,
                        slot
                    )
                }
            }
            ACTION_WAKEUP_PREP -> {
                postDisciplineNotification(
                    context,
                    1001,
                    "STRICTROUTINES — 08:49 AM PREP",
                    "Next activity opens in 1 minute. Wake-Up window 08:50 - 09:10.",
                    "WAKEUP_CHALLENGE",
                    "ACT-WAKEUP"
                )
            }
            ACTION_WAKEUP_ALARM -> {
                val wakeUpStatus = db.getWakeUpFlowStatus(today)
                val isWakeUpDone = wakeUpStatus.optBoolean("all_completed", false) ||
                    (wakeUpStatus.optBoolean("step1_camera", false) &&
                     wakeUpStatus.optBoolean("step2_bluetooth", false) &&
                     wakeUpStatus.optBoolean("step3_motivation_track", false))
                val act = db.getDailyActivities(today).firstOrNull { it.type == "WAKE_UP" || it.activityId.contains("WAKEUP") }
                val nowMs = db.authoritativeNowMs()
                val windowEnd = act?.let { db.parseIsoToMs(it.windowEnd) } ?: Long.MAX_VALUE
                val isExpired = nowMs >= windowEnd
                val alreadySubmitted = isWakeUpDone || act?.status in listOf("SUBMITTED", "VALIDATING", "VERIFIED", "COMPLETED", "EXCUSED", "CANCELLED")
                if (!alreadySubmitted && !isExpired) {
                    db.logStructured(today, false, "ALARM_TRIGGERED", "08:50 AM Wake-Up Window Open")
                    postDisciplineNotification(
                        context,
                        1002,
                        "STRICTROUTINES WAKE-UP (08:50 - 09:10)",
                        "YOUR WAKE-UP WINDOW IS OPEN. [ VERIFY WAKE-UP ]",
                        "WAKEUP_CHALLENGE",
                        "ACT-WAKEUP"
                    )
                }
            }
            ACTION_HOURLY_CHECKPOINT -> {
                val slot = intent.getStringExtra("HOUR_SLOT") ?: "10:00-11:00"
                val actId = intent.getStringExtra("ACTIVITY_ID") ?: "ACT-${today.replace("-", "")}-${slot.takeLast(5).take(2)}00-HOURLY"
                val notifKey = eventNotificationKey(actId,"OPENED")
                val alreadySubmitted = db.getDailyActivities(today).any {
                    it.activityId == actId && it.status in listOf("SUBMITTED", "VALIDATING", "VERIFIED", "COMPLETED")
                }
                if (!alreadySubmitted && !db.isNotificationSent(notifKey)) {
                    db.markNotificationSent(notifKey, actId, "OPENED")
                    db.logStructured(today, false, "HOURLY_CHECKPOINT_TRIGGERED", slot)
                    postDisciplineNotification(
                        context,
                        1003,
                        "⏱ STRICTROUTINES — Hourly Window Open ($slot)",
                        "Your hourly activity is now open. Window closes in 20 minutes.",
                        "HOURLY_CHECK",
                        actId,
                        slot
                    )
                }
            }
            ACTION_GYM_REMINDER -> {
                postDisciplineNotification(
                    context,
                    1004,
                    "🏋️ GYM TIME (06:00 PM)",
                    "TIME TO TRAIN. Start your 30+ minute gym session now.",
                    "GYM_TIME"
                )
            }
            Intent.ACTION_BOOT_COMPLETED -> {
                scheduleDefaultDailyRoutine(context, "BOOT_COMPLETED")
                StrictEnforcementService.start(context)
            }
            ScheduleDeadlineManager.ACTION_SCHEDULE_DEADLINE_10AM -> {
                ScheduleDeadlineManager.handleDeadlinePassed(context, db)
            }
            Intent.ACTION_TIME_CHANGED, Intent.ACTION_TIMEZONE_CHANGED, "android.intent.action.TIME_SET" -> {
                scheduleDefaultDailyRoutine(context, "TIME_SET")
            }
            Intent.ACTION_MY_PACKAGE_REPLACED -> {
                scheduleDefaultDailyRoutine(context, "MY_PACKAGE_REPLACED")
            }
        }
    }

    companion object {
        const val CHANNEL_ID = "strictrecords_discipline_channel"
        const val ACTION_WAKEUP_PREP = "com.strictrecords.app.ACTION_WAKEUP_PREP"
        const val ACTION_WAKEUP_ALARM = "com.strictrecords.app.ACTION_WAKEUP_ALARM"
        const val ACTION_HOURLY_CHECKPOINT = "com.strictrecords.app.ACTION_HOURLY_CHECKPOINT"
        const val ACTION_GYM_REMINDER = "com.strictrecords.app.ACTION_GYM_REMINDER"
        const val ACTION_ACTIVITY_PRE_OPENING = "com.strictrecords.app.ACTION_ACTIVITY_PRE_OPENING"
        const val ACTION_ACTIVITY_OPENED = "com.strictrecords.app.ACTION_ACTIVITY_OPENED"
        const val ACTION_ACTIVITY_WARN1M = "com.strictrecords.app.ACTION_ACTIVITY_WARN1M"
        const val ACTION_ACTIVITY_CLOSED = "com.strictrecords.app.ACTION_ACTIVITY_CLOSED"

        fun scheduleDefaultDailyRoutine(context: Context, source: String = "MANUAL") {
            val db = StrictRecordsLocalDb.getInstance(context)
            ScheduleDeadlineManager.schedule10AmAlarm(context, db)
            val deadlineStatus = ScheduleDeadlineManager.evaluateScheduleStatus(context, db)
            if (deadlineStatus == ScheduleDeadlineRules.ScheduleDeadlineState.OVERDUE_UNCONFIRMED ||
                deadlineStatus == ScheduleDeadlineRules.ScheduleDeadlineState.COMPLETION_REQUIRED) {
                ScheduleDeadlineManager.postDeadlineNotification(context)
            } else if (deadlineStatus == ScheduleDeadlineRules.ScheduleDeadlineState.CONFIRMED_AND_RELEASED ||
                       deadlineStatus == ScheduleDeadlineRules.ScheduleDeadlineState.ON_TIME_CONFIRMED) {
                ScheduleDeadlineManager.cancelDeadlineNotification(context)
            }
            db.setConfigValue("diag_last_scheduler_restoration_source", source)
            val am = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return

            // Hourly windows are delivered by the server's authoritative schedule
            // over the registered-device event stream. Cancel the older local
            // hourly alarms so a device cannot emit a second scheduler's alerts.
            for (h in 10..22) {
                cancelAtTime(context, am, 3100 + h, ACTION_ACTIVITY_PRE_OPENING)
                cancelAtTime(context, am, 3200 + h, ACTION_ACTIVITY_OPENED)
                cancelAtTime(context, am, 3300 + h, ACTION_ACTIVITY_WARN1M)
                cancelAtTime(context, am, 3400 + h, ACTION_ACTIVITY_CLOSED)
            }
            scheduleHourlyWindows(context, forceRebuild = true)

            // Replace obsolete fixed-time wake/gym alarms with identified cached occurrences.
            for (code in listOf(2001, 2002, 2003, 2004, 2050)) {
                for (action in listOf(ACTION_ACTIVITY_PRE_OPENING, ACTION_WAKEUP_ALARM, ACTION_ACTIVITY_WARN1M, ACTION_ACTIVITY_CLOSED, ACTION_GYM_REMINDER)) cancelAtTime(context, am, code, action)
            }

        }

        private val alarmWorker = java.util.concurrent.Executors.newSingleThreadExecutor { r ->
            Thread(r, "WakeUpAlarmWorker").apply { isDaemon = true }
        }

        fun scheduleHourlyWindows(context: Context, forceRebuild: Boolean = false) {
            if (Looper.myLooper() == Looper.getMainLooper()) {
                alarmWorker.execute {
                    scheduleHourlyWindows(context, forceRebuild)
                }
                return
            }
            try {
                val db = StrictRecordsLocalDb.getInstance(context)
                val am = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
                val now = db.authoritativeNowMs()
                val eligible = try {
                    db.getDailyActivities(db.nowDate()).filter {
                        it.type in listOf("HOURLY", "WAKE_UP", "BATH", "GYM", "TRAVEL") &&
                        (it.type != "HOURLY" || it.submissionEligible) &&
                        it.status in listOf("UPCOMING", "NOTIFIED", "REMINDER_VISIBLE", "OPEN", "CLOSED")
                    }
                } catch (_: Throwable) { emptyList() }
                val preferences = context.getSharedPreferences("hourly_cached_alarms", Context.MODE_PRIVATE)
                val fingerprint = "${db.nowDate()}:${db.hasFreshServerClock()}:${(now - System.currentTimeMillis()) / 60000}:" + eligible.joinToString { "${it.activityId}:${it.windowStart}:${it.windowEnd}:${it.status}" }
                if (!AlarmScheduleRules.shouldRebuild(preferences.getString("fingerprint", null), fingerprint, forceRebuild)) return
                val savedKeys = preferences.getStringSet("keys", emptySet()).orEmpty()
                for (key in savedKeys) {
                    try {
                        val savedAction = when (key.substringBefore(':').substringAfterLast('-')) {
                            "OPENING" -> ACTION_ACTIVITY_PRE_OPENING
                            "OPENED" -> ACTION_ACTIVITY_OPENED
                            "WARN1M" -> ACTION_ACTIVITY_WARN1M
                            else -> ACTION_ACTIVITY_CLOSED
                        }
                        val intent = Intent(context, WakeUpAlarmReceiver::class.java).setAction(savedAction).setData(android.net.Uri.parse("strictroutines://hourly/$key"))
                        val pi = PendingIntent.getBroadcast(context, 0, intent, PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE)
                        if (pi != null) { am.cancel(pi); pi.cancel() }
                    } catch (_: Throwable) {}
                }
                val newKeys = mutableSetOf<String>()
                var nextEarliestAlarmMs = Long.MAX_VALUE
                var nextEarliestAlarmKey = ""
                for (act in eligible) {
                    val start = db.parseIsoToMs(act.windowStart)
                    val end = db.parseIsoToMs(act.windowEnd)
                    if (start <= 0L || end <= 0L || end <= start) continue
                    val isGymOrTravel = act.type in listOf("GYM", "TRAVEL") || act.activityId.contains("GYM") || act.activityId.contains("TRAVEL")
                    val preOffset = if (isGymOrTravel) 5 * 60_000L else 60_000L
                    val events = listOf(
                        Triple("OPENING", start - preOffset, ACTION_ACTIVITY_PRE_OPENING),
                        Triple("OPENED", start, ACTION_ACTIVITY_OPENED),
                        Triple("WARN1M", end - 60_000L, ACTION_ACTIVITY_WARN1M),
                        Triple("CLOSED", end, ACTION_ACTIVITY_CLOSED)
                    )
                    for ((suffix, at, action) in events) {
                        try {
                            val key = db.notificationKey(act.activityId, suffix, act.windowStart, act.windowEnd, act.type)
                            val isEligibleNow = when (suffix) {
                                "OPENING" -> now in (start - preOffset) until start
                                "OPENED" -> now in start until end
                                "WARN1M" -> now in (end - 60_000L) until end
                                "CLOSED" -> now in end until (end + 120_000L)
                                else -> false
                            }
                            if (db.isNotificationSent(key) || (at <= now && !isEligibleNow)) continue
                            val intent = Intent(context, WakeUpAlarmReceiver::class.java).apply {
                                this.action = action; data = android.net.Uri.parse("strictroutines://hourly/$key")
                                putExtra("ACTIVITY_ID", act.activityId); putExtra("ACTIVITY_TITLE", act.title)
                                putExtra("WINDOW_START_MS", start); putExtra("WINDOW_END_MS", end)
                                putExtra("HOUR_SLOT", runCatching { org.json.JSONObject(act.detailsJson).optString("hour_slot") }.getOrDefault(""))
                            }
                            val pi = PendingIntent.getBroadcast(context, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
                            val wallTrigger = System.currentTimeMillis() + maxOf(1000L, at - now)
                            if (wallTrigger > System.currentTimeMillis()) {
                                try {
                                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !am.canScheduleExactAlarms()) {
                                        am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, wallTrigger, pi)
                                    } else {
                                        am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, wallTrigger, pi)
                                    }
                                } catch (_: SecurityException) {
                                    try {
                                        am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, wallTrigger, pi)
                                    } catch (_: Throwable) {}
                                } catch (e: Exception) {
                                    db.setConfigValue("diag_last_failure_reason", e.message ?: "Alarm registration failed")
                                }
                            }
                            newKeys.add(key)
                            if (at > now && at < nextEarliestAlarmMs) {
                                nextEarliestAlarmMs = at
                                nextEarliestAlarmKey = key
                            }
                        } catch (_: Throwable) {}
                    }
                }
                preferences.edit().putString("fingerprint", fingerprint).putStringSet("keys", newKeys).apply()

                // Persist truthful diagnostics
                db.setConfigValue("diag_last_scheduler_restoration_time", db.nowIso())
                if (nextEarliestAlarmMs != Long.MAX_VALUE) {
                    db.setConfigValue("diag_next_alarm_time", db.formatMsToIso(nextEarliestAlarmMs))
                    db.setConfigValue("diag_next_alarm_key", nextEarliestAlarmKey)
                } else {
                    db.setConfigValue("diag_next_alarm_time", "None upcoming today")
                    db.setConfigValue("diag_next_alarm_key", "None")
                }
            } catch (t: Throwable) {
                Log.e("WakeUpAlarmReceiver", "scheduleHourlyWindows error: ${t.message}", t)
            }
        }

        private fun scheduleAtTime(
            context: Context,
            am: AlarmManager,
            hour: Int,
            minute: Int,
            requestCode: Int,
            action: String,
            hourSlot: String?,
            activityTitle: String? = null,
            activityId: String? = null
        ) {
            val cal = Calendar.getInstance().apply {
                set(Calendar.HOUR_OF_DAY, hour)
                set(Calendar.MINUTE, minute)
                set(Calendar.SECOND, 0)
                set(Calendar.MILLISECOND, 0)
                if (timeInMillis <= System.currentTimeMillis()) {
                    add(Calendar.DAY_OF_YEAR, 1)
                }
            }
            val intent = Intent(context, WakeUpAlarmReceiver::class.java).apply {
                this.action = action
                if (hourSlot != null) putExtra("HOUR_SLOT", hourSlot)
                if (activityTitle != null) putExtra("ACTIVITY_TITLE", activityTitle)
                if (activityId != null) putExtra("ACTIVITY_ID", activityId)
            }
            val pi = PendingIntent.getBroadcast(
                context,
                requestCode,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            try {
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, cal.timeInMillis, pi)
            } catch (_: SecurityException) {
                am.set(AlarmManager.RTC_WAKEUP, cal.timeInMillis, pi)
            }
        }

        private fun cancelAtTime(context: Context, am: AlarmManager, requestCode: Int, action: String) {
            val intent = Intent(context, WakeUpAlarmReceiver::class.java).apply { this.action = action }
            val pi = PendingIntent.getBroadcast(
                context,
                requestCode,
                intent,
                PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE
            ) ?: return
            am.cancel(pi)
            pi.cancel()
        }

        fun cancelHourlyNotifications(context: Context) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
            nm.cancel(1003)
            nm.cancel(3001)
            nm.cancel(3002)
            nm.cancel(3003)
        }

        fun postDisciplineNotification(
            context: Context,
            notifId: Int,
            title: String,
            message: String,
            modeExtra: String,
            activityId: String? = null,
            hourSlot: String? = null
        ) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val chan = NotificationChannel(
                    CHANNEL_ID,
                    "StrictRoutines Discipline Alerts",
                    NotificationManager.IMPORTANCE_HIGH
                )
                nm.createNotificationChannel(chan)
            }
            val taskTypeExtra = when {
                modeExtra.startsWith("WAKEUP") || (activityId?.contains("WAKEUP", ignoreCase = true) == true) -> "WAKE_UP"
                modeExtra.startsWith("HOURLY") || (activityId?.contains("HOURLY", ignoreCase = true) == true) -> "HOURLY_CHECKIN"
                else -> "ACTIVITY"
            }
            val tapIntent = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
                putExtra("OPEN_MODAL", modeExtra)
                putExtra("TASK_TYPE", taskTypeExtra)
                if (!activityId.isNullOrBlank()) putExtra("ACTIVITY_ID", activityId)
                if (!hourSlot.isNullOrBlank()) putExtra("HOUR_SLOT", hourSlot)
            }
            val contentPi = PendingIntent.getActivity(
                context,
                activityId?.hashCode() ?: notifId,
                tapIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )

            val actionIntent = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
                putExtra("OPEN_MODAL", modeExtra)
                putExtra("TASK_TYPE", taskTypeExtra)
                if (!activityId.isNullOrBlank()) putExtra("ACTIVITY_ID", activityId)
                if (!hourSlot.isNullOrBlank()) putExtra("HOUR_SLOT", hourSlot)
            }
            val actionPi = PendingIntent.getActivity(
                context,
                (activityId?.hashCode() ?: notifId) xor 10000,
                actionIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )

            val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                Notification.Builder(context, CHANNEL_ID)
            } else {
                @Suppress("DEPRECATION")
                Notification.Builder(context)
            }

            builder
                .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
                .setContentTitle(title)
                .setContentText(message)
                .setContentIntent(contentPi)
                .setOngoing(true)
                .setAutoCancel(true)
                .setVisibility(Notification.VISIBILITY_PUBLIC)

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                builder.setCategory(Notification.CATEGORY_REMINDER)
                builder.setPriority(Notification.PRIORITY_MAX)
            }

            // Locked/screen-off scheduled alarms use the OS-authorized alarm
            // surface. The ordinary notification/action is always retained.
            if (taskTypeExtra == "WAKE_UP") {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.KITKAT_WATCH) {
                    val action = Notification.Action.Builder(
                        android.R.drawable.ic_lock_idle_alarm,
                        "☀️ VERIFY WAKE-UP",
                        actionPi
                    ).build()
                    builder.addAction(action)
                }
            } else if (taskTypeExtra == "HOURLY_CHECKIN") {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.KITKAT_WATCH) {
                    val action = Notification.Action.Builder(
                        android.R.drawable.ic_menu_edit,
                        "⚡ SUBMIT PROOF",
                        actionPi
                    ).build()
                    builder.addAction(action)
                }
            }

            StrictBotReminderPopup.configureLockScreenAlert(context, builder, title, message,
                modeExtra, activityId, notifId, contentPi)
            val notif = builder.build()
            nm.notify(notifId, notif)
            StrictBotReminderPopup.show(context, title, message,
                "reminder:${activityId.orEmpty()}:$modeExtra:$notifId", contentPi)
        }
    }
}
