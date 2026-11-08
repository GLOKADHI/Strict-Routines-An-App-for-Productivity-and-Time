package com.strictrecords.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Lightweight, CPU-friendly broadcast receiver for device reboot and system time/package change events.
 *
 * Handles:
 * - android.intent.action.BOOT_COMPLETED
 * - android.intent.action.LOCKED_BOOT_COMPLETED
 * - android.intent.action.MY_PACKAGE_REPLACED
 * - android.intent.action.TIME_SET
 * - android.intent.action.TIMEZONE_CHANGED
 *
 * Restores daily routine schedule and registers upcoming alarms/reminders without
 * launching any full-screen Activity or tight polling loops.
 */
class StrictRecordsBootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        if (action != Intent.ACTION_LOCKED_BOOT_COMPLETED) StrictWidgetUpdates.request(context)
        Log.i("StrictRecordsBootReceiver", "Received broadcast: $action")

        val db = StrictRecordsLocalDb.getInstance(context)
        val today = db.nowDate()

        // 1. Record restoration source diagnostic
        val source = when (action) {
            Intent.ACTION_BOOT_COMPLETED -> "BOOT_COMPLETED"
            Intent.ACTION_LOCKED_BOOT_COMPLETED -> "LOCKED_BOOT_COMPLETED"
            Intent.ACTION_MY_PACKAGE_REPLACED -> "MY_PACKAGE_REPLACED"
            Intent.ACTION_TIME_CHANGED, "android.intent.action.TIME_SET" -> "TIME_SET"
            Intent.ACTION_TIMEZONE_CHANGED -> "TIMEZONE_CHANGED"
            else -> action
        }
        db.setConfigValue("diag_last_scheduler_restoration_source", source)
        db.setConfigValue("diag_last_scheduler_restoration_time", db.nowIso())

        // 2. Lightweight routine restoration (no full-screen Activity, no tight polling loops)
        try {
            db.ensureDailyActivities(today)
            WakeUpAlarmReceiver.scheduleDefaultDailyRoutine(context, source)
        } catch (e: Exception) {
            Log.e("StrictRecordsBootReceiver", "Failed to restore routine on $source", e)
            db.setConfigValue("diag_last_failure_reason", e.message ?: "Restoration failed")
        }

        // 2b. Power optimization & Night Quiet Mode schedule restoration
        try {
            StrictRecordsPowerManager.scheduleNightQuietAlarms(context)
            StrictRecordsPowerManager.onScheduleTransition(context)
        } catch (e: Exception) {
            Log.w("StrictRecordsBootReceiver", "Failed to restore power schedule on $source", e)
        }

        // 3. Start background enforcement service if supported
        BankingBreakController.restore(context)
        if (action == Intent.ACTION_BOOT_COMPLETED || action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            try {
                StrictEnforcementService.start(context)
            } catch (e: Exception) {
                Log.w("StrictRecordsBootReceiver", "Could not start StrictEnforcementService", e)
            }
        }

        // 4. Reconcile Accessibility interruption state and alarms if interrupted
        try {
            val pendingResult = goAsync()
            java.util.concurrent.Executors.newSingleThreadExecutor().execute {
                try {
                    AccessibilityGraceManager.checkAccessibilityState(context)
                } catch (e: Exception) {
                    Log.e("StrictRecordsBootReceiver", "Failed to reconcile accessibility state", e)
                } finally {
                    try {
                        pendingResult.finish()
                    } catch (_: Exception) {}
                }
            }
        } catch (e: Exception) {
            Log.e("StrictRecordsBootReceiver", "Failed to dispatch async accessibility check", e)
        }
    }
}
