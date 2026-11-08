package com.strictrecords.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Handles actions from the StrictRecords update notification:
 * - [ Update Now ]: Starts download & installation and brings up progress UI.
 * - [ Later ]: Dismisses the update notification.
 */
class UpdateActionReceiver : BroadcastReceiver() {

    companion object {
        private const val TAG = "UpdateActionReceiver"
        const val ACTION_UPDATE_NOW = "com.strictrecords.app.ACTION_UPDATE_NOW"
        const val ACTION_UPDATE_LATER = "com.strictrecords.app.ACTION_UPDATE_LATER"
    }

    override fun onReceive(context: Context, intent: Intent?) {
        val action = intent?.action ?: return
        Log.i(TAG, "Received update action: $action")
        val mgr = AppUpdateManager.getInstance(context)

        when (action) {
            ACTION_UPDATE_NOW -> {
                val rel = mgr.latestRelease
                // Launch MainActivity to display update progress dialog
                val mainIntent = Intent(context, MainActivity::class.java).apply {
                    this.action = AppUpdateManager.UPDATE_ACTION_VIEW
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
                    rel?.let {
                        putExtra("release_version_name", it.versionName)
                        putExtra("release_version_code", it.versionCode)
                        putExtra("auto_start", true)
                    }
                }
                context.startActivity(mainIntent)

                // Start downloading immediately if release is available
                if (rel != null) {
                    mgr.startDownloadAndInstall(rel)
                } else {
                    mgr.checkForUpdates(forceUserInitiated = true) { success, newRel, _ ->
                        if (success && newRel != null) {
                            mgr.startDownloadAndInstall(newRel)
                        }
                    }
                }
            }

            ACTION_UPDATE_LATER -> {
                mgr.cancelUpdateNotification()
            }
        }
    }
}
