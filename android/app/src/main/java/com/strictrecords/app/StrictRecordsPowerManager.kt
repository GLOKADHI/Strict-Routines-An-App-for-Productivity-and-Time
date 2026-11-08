package com.strictrecords.app

import android.app.AlarmManager
import android.app.KeyguardManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.Build
import android.os.PowerManager
import android.util.Log
import java.util.Calendar
import java.util.TimeZone

/**
 * Schedule-Aware Centralized Power Optimization Manager for StrictRecords Android APK.
 *
 * Dynamically adjusts APK background resource consumption based on scheduled routines
 * and screen / device-lock lifecycle states.
 *
 * Supports:
 * - Deterministic power profiles: NORMAL_MODE, NIGHT_QUIET_MODE (01:00-07:00 IST),
 *   STRICT_SESSION_LOW_POWER, TRAVEL_LOW_POWER, MOVIE_LOW_POWER, BATH_LOW_POWER,
 *   CUSTOM_ACTIVITY_LOW_POWER, RESUMING_NORMAL_MODE.
 * - Automatic suspension upon screen off & device lock (SCREEN_LOCKED_SUSPENDED).
 * - Automatic resumption upon unlock (RESUMING_FROM_SCREEN_LOCK -> ACTIVE).
 * - Preservation of Strict Session participant identity & leases during device sleep.
 * - Deferral of non-urgent sync & immediate suspension of camera/sensors/polling.
 * - Full state persistence across process terminations.
 */
object StrictRecordsPowerManager {

    private const val TAG = "StrictRecordsPower"
    private const val PREFS_NAME = "strict_power_state"
    private const val PARTICIPANT_PREFS = "strict_session_participant"
    const val TIMEZONE_IST = "Asia/Kolkata"

    const val ACTION_NIGHT_QUIET_START = "com.strictrecords.app.ACTION_NIGHT_QUIET_START"
    const val ACTION_NIGHT_QUIET_END = "com.strictrecords.app.ACTION_NIGHT_QUIET_END"
    const val ACTION_POWER_TRANSITION = "com.strictrecords.app.ACTION_POWER_TRANSITION"

    enum class PowerProfile {
        NORMAL_MODE,
        NIGHT_QUIET_MODE,           // 1:00 AM - 7:00 AM Asia/Kolkata
        STRICT_SESSION_LOW_POWER,   // 3-device Strict Session active
        TRAVEL_LOW_POWER,           // Travel activity scheduled
        MOVIE_LOW_POWER,            // Movie routine scheduled
        BATH_LOW_POWER,             // Bath routine scheduled
        CUSTOM_ACTIVITY_LOW_POWER,  // Sleeping, Gym, Study, Prayer, Exercise, etc.
        RESUMING_NORMAL_MODE
    }

    enum class PowerState {
        SCREEN_ON_ACTIVE,
        SCREEN_OFF,
        DEVICE_LOCKED,
        SCREEN_LOCKED_SUSPENDED,
        PROCESS_TERMINATED,
        RESUMING_FROM_SCREEN_LOCK,
        SESSION_RESTORING,
        SESSION_SYNCHRONIZING,
        ACTIVE
    }

    data class PowerProfileConfig(
        val profile: PowerProfile,
        val cameraAllowed: Boolean,
        val microphoneAllowed: Boolean = false, // Never used in background
        val gpsMode: String,                    // "OFF", "MINIMAL", "ON_DEMAND", "NORMAL"
        val sensorPollingAllowed: Boolean,
        val backgroundPollingAllowed: Boolean,
        val syncBatchingEnabled: Boolean,
        val nonurgentSyncDeferred: Boolean,
        val animationsAllowed: Boolean,
        val strictSessionStatus: String,        // "ACTIVE", "LOCKED_SLEEPING", "NONE"
        val criticalTasksPreserved: Boolean = true
    )

    private fun getPrefs(context: Context): SharedPreferences {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    /**
     * Returns true if current time falls within Night Quiet window (01:00 to 07:00 Asia/Kolkata).
     */
    fun isNightQuietHours(nowMs: Long = System.currentTimeMillis(), timezone: String = TIMEZONE_IST): Boolean {
        val cal = Calendar.getInstance(TimeZone.getTimeZone(timezone))
        cal.timeInMillis = nowMs
        val hour = cal.get(Calendar.HOUR_OF_DAY)
        // 01:00:00 to 06:59:59 is night quiet mode (ends at 07:00)
        return hour in 1..6
    }

    /**
     * Resolves the effective power profile based on deterministic priority:
     * 1. Active Strict Session participant requirements -> STRICT_SESSION_LOW_POWER
     * 2. Night Quiet Mode (01:00 - 07:00 IST) -> NIGHT_QUIET_MODE
     * 3. Scheduled activities (Travel, Movie, Bath, Custom)
     * 4. Normal background operation -> NORMAL_MODE
     */
    fun evaluateEffectiveProfile(context: Context, nowMs: Long = System.currentTimeMillis()): PowerProfile {
        try {
            val db = StrictRecordsLocalDb.getInstance(context)

            // Priority 1: Strict Session active
            if (db.isStrictSessionActive()) {
                return PowerProfile.STRICT_SESSION_LOW_POWER
            }

            // Priority 2: Night Quiet Mode (01:00 - 07:00 Asia/Kolkata)
            val tz = db.scheduleTimezone().ifBlank { TIMEZONE_IST }
            if (isNightQuietHours(nowMs, tz)) {
                return PowerProfile.NIGHT_QUIET_MODE
            }

            // Priority 3: Active scheduled routines
            val today = db.nowDate()
            val activities = db.getDailyActivities(today)
            val cal = Calendar.getInstance(TimeZone.getTimeZone(tz))
            cal.timeInMillis = nowMs
            val curMinutes = cal.get(Calendar.HOUR_OF_DAY) * 60 + cal.get(Calendar.MINUTE)

            for (act in activities) {
                if (act.status == "COMPLETED" || act.status == "SKIPPED") continue
                val startMin = parseTimeToMinutes(act.windowStart.ifBlank { act.scheduledTime })
                if (startMin < 0) continue
                var endMin = parseTimeToMinutes(act.windowEnd)
                if (endMin <= startMin) {
                    endMin = startMin + 60
                }
                if (curMinutes in startMin until endMin) {
                    val name = (act.title + " " + act.activityId + " " + act.type + " " + act.detailsJson).lowercase()
                    return when {
                        name.contains("travel") || name.contains("journey") -> PowerProfile.TRAVEL_LOW_POWER
                        name.contains("movie") || name.contains("cinema") -> PowerProfile.MOVIE_LOW_POWER
                        name.contains("bath") || name.contains("shower") -> PowerProfile.BATH_LOW_POWER
                        else -> PowerProfile.CUSTOM_ACTIVITY_LOW_POWER
                    }
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error evaluating effective profile: ${e.message}")
        }
        return PowerProfile.NORMAL_MODE
    }

    private fun parseTimeToMinutes(timeStr: String?): Int {
        if (timeStr.isNullOrBlank()) return -1
        val parts = timeStr.trim().split(":")
        if (parts.size >= 2) {
            val h = parts[0].toIntOrNull() ?: 0
            val m = parts[1].toIntOrNull() ?: 0
            return h * 60 + m
        }
        return -1
    }

    /**
     * Resolves configuration for a given profile and suspension state.
     */
    fun getConfig(profile: PowerProfile, isSuspended: Boolean): PowerProfileConfig {
        if (isSuspended) {
            return PowerProfileConfig(
                profile = profile,
                cameraAllowed = false,
                microphoneAllowed = false,
                gpsMode = "OFF",
                sensorPollingAllowed = false,
                backgroundPollingAllowed = false,
                syncBatchingEnabled = true,
                nonurgentSyncDeferred = true,
                animationsAllowed = false,
                strictSessionStatus = if (profile == PowerProfile.STRICT_SESSION_LOW_POWER) "LOCKED_SLEEPING" else "NONE",
                criticalTasksPreserved = true
            )
        }

        return when (profile) {
            PowerProfile.NORMAL_MODE -> PowerProfileConfig(
                profile = profile,
                cameraAllowed = true,
                microphoneAllowed = false,
                gpsMode = "NORMAL",
                sensorPollingAllowed = true,
                backgroundPollingAllowed = true,
                syncBatchingEnabled = false,
                nonurgentSyncDeferred = false,
                animationsAllowed = true,
                strictSessionStatus = "NONE",
                criticalTasksPreserved = true
            )
            PowerProfile.NIGHT_QUIET_MODE -> PowerProfileConfig(
                profile = profile,
                cameraAllowed = false,
                microphoneAllowed = false,
                gpsMode = "OFF",
                sensorPollingAllowed = false,
                backgroundPollingAllowed = false,
                syncBatchingEnabled = true,
                nonurgentSyncDeferred = true,
                animationsAllowed = false,
                strictSessionStatus = "NONE",
                criticalTasksPreserved = true
            )
            PowerProfile.STRICT_SESSION_LOW_POWER -> PowerProfileConfig(
                profile = profile,
                cameraAllowed = false,
                microphoneAllowed = false,
                gpsMode = "OFF",
                sensorPollingAllowed = false,
                backgroundPollingAllowed = false,
                syncBatchingEnabled = true,
                nonurgentSyncDeferred = true,
                animationsAllowed = false,
                strictSessionStatus = "ACTIVE",
                criticalTasksPreserved = true
            )
            PowerProfile.TRAVEL_LOW_POWER -> PowerProfileConfig(
                profile = profile,
                cameraAllowed = false,
                microphoneAllowed = false,
                gpsMode = "MINIMAL",
                sensorPollingAllowed = false,
                backgroundPollingAllowed = false,
                syncBatchingEnabled = true,
                nonurgentSyncDeferred = true,
                animationsAllowed = false,
                strictSessionStatus = "NONE",
                criticalTasksPreserved = true
            )
            PowerProfile.MOVIE_LOW_POWER,
            PowerProfile.BATH_LOW_POWER,
            PowerProfile.CUSTOM_ACTIVITY_LOW_POWER -> PowerProfileConfig(
                profile = profile,
                cameraAllowed = false,
                microphoneAllowed = false,
                gpsMode = "OFF",
                sensorPollingAllowed = false,
                backgroundPollingAllowed = false,
                syncBatchingEnabled = true,
                nonurgentSyncDeferred = true,
                animationsAllowed = false,
                strictSessionStatus = "NONE",
                criticalTasksPreserved = true
            )
            PowerProfile.RESUMING_NORMAL_MODE -> PowerProfileConfig(
                profile = profile,
                cameraAllowed = true,
                microphoneAllowed = false,
                gpsMode = "NORMAL",
                sensorPollingAllowed = true,
                backgroundPollingAllowed = true,
                syncBatchingEnabled = false,
                nonurgentSyncDeferred = false,
                animationsAllowed = true,
                strictSessionStatus = "NONE",
                criticalTasksPreserved = true
            )
        }
    }

    /**
     * Checks whether device is currently considered locked / suspended.
     */
    fun isSuspended(context: Context): Boolean {
        val prefs = getPrefs(context)
        val stateName = prefs.getString("current_power_state", PowerState.SCREEN_ON_ACTIVE.name)
        if (stateName == PowerState.SCREEN_LOCKED_SUSPENDED.name || stateName == PowerState.DEVICE_LOCKED.name || stateName == PowerState.SCREEN_OFF.name) {
            return true
        }
        val pm = context.getSystemService(Context.POWER_SERVICE) as? PowerManager
        if (pm?.isInteractive == false) return true
        val km = context.getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager
        return km?.isKeyguardLocked == true
    }

    fun getCurrentProfile(context: Context): PowerProfile {
        val prefs = getPrefs(context)
        val name = prefs.getString("current_profile", null)
        return try {
            if (name != null) PowerProfile.valueOf(name) else evaluateEffectiveProfile(context)
        } catch (_: Exception) {
            PowerProfile.NORMAL_MODE
        }
    }

    fun getCurrentPowerState(context: Context): PowerState {
        val prefs = getPrefs(context)
        val name = prefs.getString("current_power_state", null)
        return try {
            if (name != null) PowerState.valueOf(name) else PowerState.ACTIVE
        } catch (_: Exception) {
            PowerState.ACTIVE
        }
    }

    fun getCurrentConfig(context: Context): PowerProfileConfig {
        val profile = getCurrentProfile(context)
        val suspended = isSuspended(context)
        return getConfig(profile, suspended)
    }

    fun isCameraAllowed(context: Context): Boolean = getCurrentConfig(context).cameraAllowed
    fun isBackgroundPollingAllowed(context: Context): Boolean = getCurrentConfig(context).backgroundPollingAllowed
    fun isNonurgentSyncDeferred(context: Context): Boolean = getCurrentConfig(context).nonurgentSyncDeferred

    // ==========================================
    // LIFECYCLE & SCREEN TRANSITION HANDLERS
    // ==========================================

    /**
     * Screen turned OFF (ACTION_SCREEN_OFF).
     * Enters SCREEN_LOCKED_SUSPENDED immediately to conserve power.
     */
    fun onScreenOff(context: Context) {
        val now = System.currentTimeMillis()
        val prefs = getPrefs(context)
        val effectiveProfile = evaluateEffectiveProfile(context, now)

        prefs.edit()
            .putString("current_power_state", PowerState.SCREEN_LOCKED_SUSPENDED.name)
            .putString("current_profile", effectiveProfile.name)
            .putLong("last_screen_locked_at", now)
            .putBoolean("deferred_sync_pending", true)
            .apply()

        Log.i(TAG, "Screen OFF -> entering SCREEN_LOCKED_SUSPENDED under profile: $effectiveProfile")

        // Strict Session participant: transition to LOCKED_SLEEPING
        try {
            val partPrefs = context.getSharedPreferences(PARTICIPANT_PREFS, Context.MODE_PRIVATE)
            val sessionId = partPrefs.getString("session_id", "").orEmpty()
            if (sessionId.isNotEmpty()) {
                partPrefs.edit()
                    .putString("state", "LOCKED_SLEEPING")
                    .putLong("locked_sleeping_since", now)
                    .apply()
                Log.i(TAG, "Strict Session participant $sessionId state updated to LOCKED_SLEEPING (no disconnect penalty)")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error updating participant state on screen off: ${e.message}")
        }
    }

    /**
     * Device locked event (Keyguard active).
     */
    fun onDeviceLocked(context: Context) {
        val now = System.currentTimeMillis()
        val prefs = getPrefs(context)
        val effectiveProfile = evaluateEffectiveProfile(context, now)

        prefs.edit()
            .putString("current_power_state", PowerState.SCREEN_LOCKED_SUSPENDED.name)
            .putString("current_profile", effectiveProfile.name)
            .putLong("last_screen_locked_at", now)
            .apply()

        Log.i(TAG, "Device LOCKED -> entering SCREEN_LOCKED_SUSPENDED under profile: $effectiveProfile")
    }

    /**
     * Screen turned ON (ACTION_SCREEN_ON).
     * If keyguard is locked, device remains in SCREEN_LOCKED_SUSPENDED.
     */
    fun onScreenOn(context: Context) {
        val km = context.getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager
        if (km?.isKeyguardLocked == true) {
            Log.d(TAG, "Screen ON but device remains keyguard locked -> staying suspended")
            return
        }
        onDeviceUnlocked(context)
    }

    /**
     * User dismissed lockscreen (ACTION_USER_PRESENT).
     * Transitions from SCREEN_LOCKED_SUSPENDED -> RESUMING_FROM_SCREEN_LOCK -> ACTIVE.
     */
    fun onDeviceUnlocked(context: Context) {
        val now = System.currentTimeMillis()
        val prefs = getPrefs(context)
        val prevProfile = getCurrentProfile(context)

        prefs.edit()
            .putString("current_power_state", PowerState.RESUMING_FROM_SCREEN_LOCK.name)
            .putLong("last_resumed_at", now)
            .apply()

        Log.i(TAG, "Device UNLOCKED -> resuming from screen lock (previous profile: $prevProfile)")

        // Reconcile Strict Session state
        try {
            val partPrefs = context.getSharedPreferences(PARTICIPANT_PREFS, Context.MODE_PRIVATE)
            val sessionId = partPrefs.getString("session_id", "").orEmpty()
            if (sessionId.isNotEmpty()) {
                partPrefs.edit()
                    .putString("state", "ACTIVE")
                    .remove("locked_sleeping_since")
                    .apply()
                Log.i(TAG, "Strict Session participant $sessionId resumed to ACTIVE without QR re-prompt")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error resuming participant state: ${e.message}")
        }

        reconcileAfterResume(context)

        val newProfile = evaluateEffectiveProfile(context, now)
        prefs.edit()
            .putString("current_power_state", PowerState.SCREEN_ON_ACTIVE.name)
            .putString("current_profile", newProfile.name)
            .apply()
    }

    /**
     * Reconciles missed schedules, overdue tasks, and deferred synchronization after resume.
     */
    fun reconcileAfterResume(context: Context) {
        try {
            val db = StrictRecordsLocalDb.getInstance(context)
            val now = System.currentTimeMillis()

            // 1. Process deadline checks
            ScheduleDeadlineManager.handleDeviceUnlock(context, db)

            // 2. Flush pending sync if deferred
            val prefs = getPrefs(context)
            if (prefs.getBoolean("deferred_sync_pending", false)) {
                db.flushSyncEventsQueue(context)
                prefs.edit().putBoolean("deferred_sync_pending", false).apply()
                Log.d(TAG, "Flushed deferred background sync events on resume")
            }

            // 3. Reconcile validated submissions
            db.reconcileValidatedSubmissions(db.nowDate())
        } catch (e: Exception) {
            Log.w(TAG, "Error during power manager resume reconciliation: ${e.message}")
        }
    }

    /**
     * Called when a scheduled transition alarm fires (e.g., 01:00 AM or 07:00 AM IST).
     */
    fun onScheduleTransition(context: Context) {
        val now = System.currentTimeMillis()
        val newProfile = evaluateEffectiveProfile(context, now)
        val isSusp = isSuspended(context)

        getPrefs(context).edit()
            .putString("current_profile", newProfile.name)
            .apply()

        Log.i(TAG, "Schedule transition applied: $newProfile (suspended=$isSusp)")
        scheduleNightQuietAlarms(context)
    }

    /**
     * Configures daily inexact alarms for 1:00 AM and 7:00 AM Asia/Kolkata boundaries.
     */
    fun scheduleNightQuietAlarms(context: Context) {
        try {
            val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
            val tz = TimeZone.getTimeZone(TIMEZONE_IST)
            val now = System.currentTimeMillis()

            fun getNextOccurrence(hour: Int, minute: Int): Long {
                val cal = Calendar.getInstance(tz).apply {
                    timeInMillis = now
                    set(Calendar.HOUR_OF_DAY, hour)
                    set(Calendar.MINUTE, minute)
                    set(Calendar.SECOND, 0)
                    set(Calendar.MILLISECOND, 0)
                }
                if (cal.timeInMillis <= now) {
                    cal.add(Calendar.DAY_OF_YEAR, 1)
                }
                return cal.timeInMillis
            }

            // 1:00 AM Start Intent
            val startIntent = Intent(context, StrictRecordsPowerReceiver::class.java).apply {
                action = ACTION_NIGHT_QUIET_START
            }
            val startPi = PendingIntent.getBroadcast(
                context, 7101, startIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) PendingIntent.FLAG_IMMUTABLE else 0)
            )
            val startMs = getNextOccurrence(1, 0)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                alarmManager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, startMs, startPi)
            } else {
                alarmManager.set(AlarmManager.RTC_WAKEUP, startMs, startPi)
            }

            // 7:00 AM End Intent
            val endIntent = Intent(context, StrictRecordsPowerReceiver::class.java).apply {
                action = ACTION_NIGHT_QUIET_END
            }
            val endPi = PendingIntent.getBroadcast(
                context, 7102, endIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) PendingIntent.FLAG_IMMUTABLE else 0)
            )
            val endMs = getNextOccurrence(7, 0)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                alarmManager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, endMs, endPi)
            } else {
                alarmManager.set(AlarmManager.RTC_WAKEUP, endMs, endPi)
            }

            Log.i(TAG, "Scheduled Night Quiet boundaries: Start=01:00 IST ($startMs), End=07:00 IST ($endMs)")
        } catch (e: Exception) {
            Log.w(TAG, "Failed to schedule Night Quiet boundary alarms: ${e.message}")
        }
    }
}
