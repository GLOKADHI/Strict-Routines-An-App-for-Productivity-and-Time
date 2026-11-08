package com.strictrecords.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Reports a completed APK replacement after Android's package installer finishes. */
class AppUpdateInstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val pendingResult = goAsync()
        AppUpdateManager.getInstance(context).reportInstalledAfterPackageReplacement {
            pendingResult.finish()
        }
    }
}
