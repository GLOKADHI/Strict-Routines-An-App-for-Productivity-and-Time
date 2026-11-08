package com.strictrecords.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * BroadcastReceiver triggered by AlarmManager at the 5-minute grace expiry
 * and minute-boundary checks to reconcile restricted-app usage during accessibility interruption.
 */
class AccessibilityGraceAlarmReceiver : BroadcastReceiver() {

    companion object {
        const val ACTION_CHECK_GRACE = "com.strictrecords.app.ACTION_CHECK_ACCESSIBILITY_GRACE"
        const val EXTRA_EPISODE_ID = "episode_id"
    }

    override fun onReceive(context: Context, intent: Intent?) {
        Log.i("StrictRestriction", "AccessibilityGraceAlarmReceiver triggered: action=${intent?.action}")
        try {
            AccessibilityGraceManager.reconcileInterruption(context)
        } catch (e: Exception) {
            Log.e("StrictRestriction", "Error in AccessibilityGraceAlarmReceiver: ${e.message}")
        }
    }
}
