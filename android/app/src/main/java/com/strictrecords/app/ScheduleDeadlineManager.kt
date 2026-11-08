package com.strictrecords.app

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log

/**
 * Manages the 10:00 AM schedule deadline, device unlock prompts,
 * high-priority notifications, and schedule reconciliation.
 */
object ScheduleDeadlineManager {

    private const val TAG = "ScheduleDeadlineManager"
    const val NOTIF_ID_SCHEDULE_DEADLINE = 5005
    const val ACTION_SCHEDULE_DEADLINE_10AM = "com.strictrecords.app.ACTION_SCHEDULE_DEADLINE_10AM"
    private const val CHANNEL_ID = "strictrecords_discipline_channel"

    /**
     * Evaluates the authoritative schedule status for today.
     */
    fun evaluateScheduleStatus(
        context: Context,
        db: StrictRecordsLocalDb,
        nowMs: Long = db.authoritativeNowMs(),
        todayStr: String = db.nowDate(),
        currentUiState: ScheduleDeadlineRules.ScheduleDeadlineState? = null
    ): ScheduleDeadlineRules.ScheduleDeadlineState {
        val isConfirmed = db.isScheduleConfirmedForDate(todayStr)
        val tz = db.scheduleTimezone()
        return ScheduleDeadlineRules.evaluateState(
            isConfirmedForDate = isConfirmed,
            nowMs = nowMs,
            dateStr = todayStr,
            timezone = tz,
            currentState = currentUiState
        )
    }

    /**
     * Called when the device is unlocked (ACTION_USER_PRESENT).
     * If 10:00 AM has passed and today's schedule is unconfirmed, triggers the prompt.
     */
    fun handleDeviceUnlock(context: Context, db: StrictRecordsLocalDb) {
        val today = db.nowDate()
        val nowMs = db.authoritativeNowMs()
        val status = evaluateScheduleStatus(context, db, nowMs, today)

        if (status == ScheduleDeadlineRules.ScheduleDeadlineState.OVERDUE_UNCONFIRMED ||
            status == ScheduleDeadlineRules.ScheduleDeadlineState.COMPLETION_REQUIRED
        ) {
            Log.i(TAG, "Device unlocked after 10:00 AM deadline and schedule for $today is unconfirmed -> posting deadline prompt")
            postDeadlineNotification(context)

            // Notify running MainActivity if active
            val intent = Intent(MainActivity.ACTION_SCHEDULE_RESTRICTION_TRIGGER).apply {
                setPackage(context.packageName)
            }
            context.sendBroadcast(intent)
        } else if (status == ScheduleDeadlineRules.ScheduleDeadlineState.CONFIRMED_AND_RELEASED ||
                   status == ScheduleDeadlineRules.ScheduleDeadlineState.ON_TIME_CONFIRMED) {
            cancelDeadlineNotification(context)
        }
    }

    /**
     * Called when the 10:00 AM deadline alarm fires.
     */
    fun handleDeadlinePassed(context: Context, db: StrictRecordsLocalDb) {
        val today = db.nowDate()
        val status = evaluateScheduleStatus(context, db, db.authoritativeNowMs(), today)
        if (status == ScheduleDeadlineRules.ScheduleDeadlineState.OVERDUE_UNCONFIRMED ||
            status == ScheduleDeadlineRules.ScheduleDeadlineState.COMPLETION_REQUIRED
        ) {
            Log.i(TAG, "10:00 AM deadline passed for $today with unconfirmed schedule -> notifying user")
            postDeadlineNotification(context)
            val intent = Intent(MainActivity.ACTION_SCHEDULE_RESTRICTION_TRIGGER).apply {
                setPackage(context.packageName)
            }
            context.sendBroadcast(intent)
        }
        schedule10AmAlarm(context, db)
    }

    /**
     * Schedules the next exact 10:00 AM alarm.
     */
    fun schedule10AmAlarm(context: Context, db: StrictRecordsLocalDb) {
        val am = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
        val tz = db.scheduleTimezone()
        val today = db.nowDate()
        val nowMs = db.authoritativeNowMs()
        var targetMs = ScheduleDeadlineRules.getDeadlineMs(today, tz)

        if (nowMs >= targetMs) {
            // Already past 10:00 AM today, schedule for tomorrow
            val tomorrow = db.getNextCalendarDate(today)
            targetMs = ScheduleDeadlineRules.getDeadlineMs(tomorrow, tz)
        }

        val intent = Intent(context, WakeUpAlarmReceiver::class.java).apply {
            action = ACTION_SCHEDULE_DEADLINE_10AM
        }
        val pi = PendingIntent.getBroadcast(
            context,
            5005,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val wallTrigger = System.currentTimeMillis() + maxOf(1000L, targetMs - nowMs)
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, wallTrigger, pi)
            } else {
                am.setExact(AlarmManager.RTC_WAKEUP, wallTrigger, pi)
            }
            Log.i(TAG, "Scheduled 10:00 AM deadline alarm at trigger: $wallTrigger (in ${(targetMs - nowMs) / 1000}s)")
        } catch (_: SecurityException) {
            am.set(AlarmManager.RTC_WAKEUP, wallTrigger, pi)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to schedule 10 AM deadline alarm: ${e.message}")
        }
    }

    /**
     * Posts a high-priority, persistent notification directing the user to complete today's schedule.
     */
    fun postDeadlineNotification(context: Context) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val chan = NotificationChannel(
                CHANNEL_ID,
                "StrictRoutines Discipline Alerts",
                NotificationManager.IMPORTANCE_HIGH
            )
            nm.createNotificationChannel(chan)
        }

        val tapIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra("OPEN_MODAL", "SCHEDULE_COMPLETION")
            putExtra("TASK_TYPE", "SCHEDULE_COMPLETION")
        }
        val contentPi = PendingIntent.getActivity(
            context,
            NOTIF_ID_SCHEDULE_DEADLINE,
            tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val actionIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra("OPEN_MODAL", "SCHEDULE_COMPLETION")
            putExtra("TASK_TYPE", "SCHEDULE_COMPLETION")
        }
        val actionPi = PendingIntent.getActivity(
            context,
            NOTIF_ID_SCHEDULE_DEADLINE xor 10000,
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
            .setContentTitle("Today's Schedule Is Pending")
            .setContentText("You missed the 10:00 AM schedule deadline. Complete and confirm today's schedule to restore normal app access.")
            .setContentIntent(contentPi)
            .setOngoing(true)
            .setAutoCancel(false)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            builder.setCategory(Notification.CATEGORY_REMINDER)
            builder.setPriority(Notification.PRIORITY_MAX)
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.KITKAT_WATCH) {
            val action = Notification.Action.Builder(
                android.R.drawable.ic_menu_edit,
                "📝 COMPLETE SCHEDULE",
                actionPi
            ).build()
            builder.addAction(action)
        }

        nm.notify(NOTIF_ID_SCHEDULE_DEADLINE, builder.build())
    }

    /**
     * Cancels the deadline notification.
     */
    fun cancelDeadlineNotification(context: Context) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
        nm.cancel(NOTIF_ID_SCHEDULE_DEADLINE)
    }
}
