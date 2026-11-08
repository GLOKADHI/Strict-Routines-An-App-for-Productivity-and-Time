package com.strictrecords.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

/**
 * Manages the once-per-local-day Morning Wake-Up & Daily Routine Setup flow.
 *
 * Triggered on the user's first eligible phone unlock in the morning:
 * 1. Reconciles actual persisted wake-up verification state for the current local date.
 * 2. If wake-up verification is pending, directs the user to Wake-Up Verification.
 * 3. After verification completes, opens Daily Routine Setup.
 * 4. Suppresses duplicate prompts once confirmed for today.
 *
 * Handles Android Background Activity Launch (BAL) restrictions:
 * - Posts high-priority heads-up notification directing user to the flow.
 * - Broadcasts to foregrounded MainActivity if already active.
 * - Automatically presents on app resume when foregrounded.
 */
object MorningWakeUpManager {

    private const val TAG = "MorningWakeUpManager"
    const val NOTIF_ID_MORNING_FLOW = 5010
    const val ACTION_START_MORNING_FLOW = "com.strictrecords.app.ACTION_START_MORNING_FLOW"
    const val EXTRA_START_MORNING_FLOW = "EXTRA_START_MORNING_FLOW"
    private const val CHANNEL_ID = "strictrecords_morning_routine_channel"

    const val MORNING_WINDOW_START_HOUR = 6
    const val MORNING_WINDOW_END_HOUR = 10

    /**
     * Determines whether the morning flow is eligible to trigger right now.
     */
    fun isWithinMorningWindow(nowMs: Long, timezone: String = "Asia/Kolkata"): Boolean {
        val cal = Calendar.getInstance(TimeZone.getTimeZone(timezone)).apply {
            timeInMillis = nowMs
        }
        val hour = cal.get(Calendar.HOUR_OF_DAY)
        val minute = cal.get(Calendar.MINUTE)
        return (hour in MORNING_WINDOW_START_HOUR until MORNING_WINDOW_END_HOUR) ||
            (hour == MORNING_WINDOW_END_HOUR && minute == 0)
    }

    /**
     * Determines whether the morning flow is eligible to trigger for the current user/date.
     */
    fun isMorningFlowEligible(db: StrictRecordsLocalDb, nowMs: Long): Boolean {
        val today = db.nowDate()
        if (db.isMorningFlowCompletedToday(today)) return false
        val tz = db.scheduleTimezone()
        return isWithinMorningWindow(nowMs, tz)
    }

    fun isMorningWindow(nowMs: Long, zone: java.time.ZoneId = java.time.ZoneId.of("Asia/Kolkata")): Boolean {
        return isWithinMorningWindow(nowMs, zone.id)
    }

    /**
     * Called when the device is unlocked (ACTION_USER_PRESENT).
     */
    fun handleMorningUnlock(context: Context, db: StrictRecordsLocalDb) {
        val today = db.nowDate()
        val nowMs = db.authoritativeNowMs()
        val tz = db.scheduleTimezone()

        val isCompleted = db.isMorningFlowCompletedToday(today)
        if (isCompleted) {
            cancelMorningNotification(context)
            return
        }

        val inMorningWindow = isWithinMorningWindow(nowMs, tz)
        val hasPromptedToday = db.hasMorningFlowPromptBeenShown(today)

        if (!hasPromptedToday && inMorningWindow) {
            db.markMorningFlowPromptShown(today)
            Log.i(TAG, "First eligible morning unlock detected for $today -> triggering morning flow")

            val isDev = db.getConfigValue("is_dev_mode", "false") == "true"
            val wakeDone = db.isWakeUpVerificationCompleted(today, isDev)
            val notifTitle = if (!wakeDone) {
                "STRICTROUTINES — WAKE-UP VERIFICATION REQUIRED"
            } else {
                "STRICTROUTINES — CONFIRM TODAY'S ROUTINE"
            }
            val notifMsg = if (!wakeDone) {
                "Good morning! Complete your camera wake-up verification to begin your day."
            } else {
                "Wake-up verified ✓ Set your college & bath routine for today."
            }

            postMorningNotification(context, notifTitle, notifMsg)

            // Notify running activity if foregrounded
            try {
                val intent = Intent(ACTION_START_MORNING_FLOW).apply {
                    setPackage(context.packageName)
                    putExtra(EXTRA_START_MORNING_FLOW, true)
                }
                context.sendBroadcast(intent)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to broadcast morning flow trigger: ${e.message}")
            }
        }
    }

    /**
     * Posts a high-priority heads-up notification directing the user to complete the morning flow.
     */
    fun postMorningNotification(context: Context, title: String, message: String) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val chan = NotificationChannel(
                CHANNEL_ID,
                "StrictRoutines Morning Flow",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Daily morning wake-up verification and routine setup alerts"
                enableVibration(true)
                setShowBadge(true)
            }
            nm.createNotificationChannel(chan)
        }

        val tapIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra(EXTRA_START_MORNING_FLOW, true)
            putExtra("OPEN_MODAL", "MORNING_FLOW")
        }
        val contentPi = PendingIntent.getActivity(
            context,
            NOTIF_ID_MORNING_FLOW,
            tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notif = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle(title)
            .setContentText(message)
            .setStyle(NotificationCompat.BigTextStyle().bigText(message))
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setAutoCancel(true)
            .setContentIntent(contentPi)
            .setOngoing(false)
            .build()

        try {
            nm.notify(NOTIF_ID_MORNING_FLOW, notif)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to post morning notification: ${e.message}")
        }
    }

    /**
     * Cancels the morning flow notification.
     */
    fun cancelMorningNotification(context: Context) {
        try {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
            nm?.cancel(NOTIF_ID_MORNING_FLOW)
        } catch (_: Exception) {}
    }
}
