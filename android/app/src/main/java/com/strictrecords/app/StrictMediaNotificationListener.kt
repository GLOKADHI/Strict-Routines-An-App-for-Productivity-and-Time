package com.strictrecords.app

import android.content.ComponentName
import android.content.Context
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.os.Build
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log

/**
 * Standard Android NotificationListenerService for accessing active MediaSessions
 * to enforce background playback restrictions on Spotify and Samsung Music.
 *
 * Uses only public, supported Android MediaSession APIs and user-granted Notification Access.
 */
class StrictMediaNotificationListener : NotificationListenerService() {

    companion object {
        private const val TAG = "StrictMediaListener"

        @Volatile
        var activeInstance: StrictMediaNotificationListener? = null
            private set

        fun getComponentName(context: Context): ComponentName {
            return ComponentName(context, StrictMediaNotificationListener::class.java)
        }

        fun isConnected(): Boolean = activeInstance != null

        fun isNotificationAccessGranted(context: Context): Boolean {
            return try {
                val flat = Settings.Secure.getString(context.contentResolver, "enabled_notification_listeners")
                flat?.split(":")?.any { entry ->
                    val cn = ComponentName.unflattenFromString(entry)
                    cn == getComponentName(context)
                } == true
            } catch (_: Exception) {
                false
            }
        }

        fun getActiveMediaControllers(context: Context): List<MediaController> {
            if (isNotificationAccessGranted(context)) {
                try {
                    val msm = context.getSystemService(Context.MEDIA_SESSION_SERVICE) as? MediaSessionManager
                    val sessions = msm?.getActiveSessions(getComponentName(context))
                    if (sessions != null) return sessions
                } catch (e: Exception) {
                    Log.w(TAG, "getActiveSessions error: ${e.message}")
                }
            }
            return emptyList()
        }
    }

    private var sessionManager: MediaSessionManager? = null
    private val sessionsChangedListener = MediaSessionManager.OnActiveSessionsChangedListener { controllers ->
        Log.d(TAG, "Active media sessions changed: count=${controllers?.size ?: 0}")
        BackgroundMusicRestrictionManager.onActiveSessionsChanged(applicationContext, controllers)
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        activeInstance = this
        Log.i(TAG, "StrictMediaNotificationListener connected successfully")
        try {
            sessionManager = getSystemService(Context.MEDIA_SESSION_SERVICE) as? MediaSessionManager
            sessionManager?.addOnActiveSessionsChangedListener(
                sessionsChangedListener,
                getComponentName(this)
            )
            val initialSessions = sessionManager?.getActiveSessions(getComponentName(this))
            BackgroundMusicRestrictionManager.onActiveSessionsChanged(applicationContext, initialSessions)
        } catch (e: Exception) {
            Log.e(TAG, "Error registering active sessions listener: ${e.message}", e)
        }
    }

    override fun onListenerDisconnected() {
        super.onListenerDisconnected()
        try {
            sessionManager?.removeOnActiveSessionsChangedListener(sessionsChangedListener)
        } catch (_: Exception) {}
        activeInstance = null
        BackgroundMusicRestrictionManager.onListenerDisconnected()
        Log.i(TAG, "StrictMediaNotificationListener disconnected")
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val pkg = sbn?.packageName ?: return
        if (pkg == packageName) return // Never monitor our own notifications
        if (BackgroundMusicRestrictionManager.isMonitoredPlaybackPackage(pkg)) {
            BackgroundMusicRestrictionManager.onMusicAppNotificationPosted(applicationContext, pkg)
        }
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification?) {
        val pkg = sbn?.packageName ?: return
        if (pkg == packageName) return
        if (BackgroundMusicRestrictionManager.isMonitoredPlaybackPackage(pkg)) {
            BackgroundMusicRestrictionManager.onMusicAppNotificationRemoved(applicationContext, pkg)
        }
    }

    override fun onDestroy() {
        try {
            sessionManager?.removeOnActiveSessionsChangedListener(sessionsChangedListener)
        } catch (_: Exception) {}
        if (activeInstance === this) activeInstance = null
        BackgroundMusicRestrictionManager.onListenerDisconnected()
        super.onDestroy()
    }
}
