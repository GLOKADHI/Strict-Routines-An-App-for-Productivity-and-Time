package com.strictrecords.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * BroadcastReceiver for scheduled power optimization transitions:
 * - 01:00 AM IST Night Quiet Mode entry
 * - 07:00 AM IST Night Quiet Mode exit & resumption of normal monitoring
 * - Generic schedule transition alarms
 */
class StrictRecordsPowerReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        Log.i("StrictRecordsPowerReceiver", "Handling power boundary broadcast: $action")

        when (action) {
            StrictRecordsPowerManager.ACTION_NIGHT_QUIET_START -> {
                Log.i("StrictRecordsPowerReceiver", "01:00 AM IST reached -> entering NIGHT_QUIET_MODE")
                StrictRecordsPowerManager.onScheduleTransition(context)
            }
            StrictRecordsPowerManager.ACTION_NIGHT_QUIET_END -> {
                Log.i("StrictRecordsPowerReceiver", "07:00 AM IST reached -> ending NIGHT_QUIET_MODE, reconciling schedules")
                StrictRecordsPowerManager.onScheduleTransition(context)
                StrictRecordsPowerManager.reconcileAfterResume(context)
            }
            StrictRecordsPowerManager.ACTION_POWER_TRANSITION -> {
                StrictRecordsPowerManager.onScheduleTransition(context)
            }
        }
    }
}
