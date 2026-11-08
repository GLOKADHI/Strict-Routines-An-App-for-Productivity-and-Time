package com.strictrecords.app

import android.app.admin.DeviceAdminReceiver
import android.content.Context
import android.content.Intent

/**
 * StrictRecords Optional Device Administration Receiver (Section 6).
 * Enforces legitimate platform-level protection without covert hooks.
 */
class StrictRecordsAdminReceiver : DeviceAdminReceiver() {
    override fun onEnabled(context: Context, intent: Intent) {
        super.onEnabled(context, intent)
    }

    override fun onDisabled(context: Context, intent: Intent) {
        super.onDisabled(context, intent)
    }
}
