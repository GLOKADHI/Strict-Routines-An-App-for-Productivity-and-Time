package com.strictrecords.app

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.graphics.Color
import android.os.Build
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import androidx.core.app.NotificationCompat
import java.util.Locale
import java.util.UUID

/**
 * Coordinates Accessibility service interruption detection, the five-minute grace timer,
 * Usage Access fallback reconstruction, and idempotent restricted-app penalty scoring.
 */
object AccessibilityGraceManager {

    private const val TAG = "AccessibilityGrace"
    const val PREFS_NAME = "accessibility_grace_state"

    const val GRACE_DURATION_MS = 5 * 60_000L // 5 minutes (300,000 ms)
    const val BLOCK_DURATION_MS = 60_000L     // 1 minute (60,000 ms)
    const val NOTIFICATION_ID = 9101
    const val CHANNEL_ID = "strict_accessibility_grace"
    const val CHANNEL_NAME = "Accessibility Enforcement"

    const val KEY_EPISODE_ID = "episode_id"
    const val KEY_IS_INTERRUPTED = "is_interrupted"
    const val KEY_INTERRUPTED_WALL_MS = "interrupted_wall_ms"
    const val KEY_INTERRUPTED_ELAPSED_MS = "interrupted_elapsed_ms"
    const val KEY_RECONNECTED_WALL_MS = "reconnected_wall_ms"
    const val KEY_DEDUCTED_BLOCKS = "deducted_blocks"
    const val KEY_TOTAL_PENALTY = "total_penalty"
    const val KEY_TOTAL_CONFIRMED_USAGE_MS = "total_confirmed_usage_ms"
    const val KEY_LAST_STATUS = "last_status"
    const val KEY_ALARM_SCHEDULED_MS = "alarm_scheduled_ms"
    const val KEY_GRACE_DEADLINE_WALL_MS = "grace_deadline_wall_ms"
    const val KEY_MISSING_ACCESS_TYPE = "missing_access_type"

    const val MISSING_TYPE_NONE = "NONE"
    const val MISSING_TYPE_ACCESSIBILITY = "ACCESSIBILITY"
    const val MISSING_TYPE_USAGE_ACCESS = "USAGE_ACCESS"
    const val MISSING_TYPE_BOTH = "BOTH"

    const val STATE_ACCESSIBILITY_ACTIVE = "ACCESSIBILITY ACTIVE"
    const val STATE_ACCESSIBILITY_INTERRUPTED = "ACCESSIBILITY INTERRUPTED"
    const val STATE_GRACE_PERIOD_ACTIVE = "GRACE PERIOD ACTIVE"
    const val STATE_PENALTY_ELIGIBLE = "PENALTY ELIGIBLE"
    const val STATE_RESTORED_MONITORING_ACTIVE = "RESTORED — MONITORING ACTIVE"
    const val STATE_MONITORING_STATUS_UNKNOWN = "MONITORING STATUS UNKNOWN"

    enum class AccessState {
        ACCESS_OK,
        ACCESSIBILITY_MISSING,
        USAGE_ACCESS_MISSING,
        BOTH_ACCESS_MISSING,
        ACCESS_CHECK_FAILED,
        RESTORING,
        RECHECK_REQUIRED
    }

    data class AccessibilityStatus(
        val statusText: String,
        val isInterrupted: Boolean,
        val interruptedAtWallMs: Long,
        val graceDeadlineWallMs: Long,
        val remainingGraceMs: Long,
        val confirmedUsageDurationMs: Long,
        val recordedPenaltyPoints: Int,
        val canOpenAccessibilitySettings: Boolean,
        val stateCode: String = STATE_ACCESSIBILITY_ACTIVE,
        val episodeId: String = "",
        val accessState: AccessState = AccessState.ACCESS_OK,
        val missingAccessType: String = MISSING_TYPE_NONE,
        val canOpenUsageAccessSettings: Boolean = false,
        val overdueSeconds: Long = 0L
    ) {
        val effectiveCountdownMs: Long
            get() = if (overdueSeconds > 0L) -overdueSeconds * 1000L else remainingGraceMs
    }

    data class UsageEventModel(
        val packageName: String,
        val eventType: Int,
        val timestampMs: Long
    )

    data class BlockUsageResult(
        val confirmedUsageDurationMs: Long,
        val primaryPackage: String?,
        val isCompleteMinute: Boolean,
        val hasUncertainGap: Boolean
    )

    data class ReconcileResult(
        val episodeId: String,
        val evaluatedBlocks: Int,
        val newDeductionsCount: Int,
        val totalDeductedPoints: Int,
        val totalConfirmedUsageMs: Long,
        val isStillInterrupted: Boolean
    )

    interface GraceStore {
        fun getLong(key: String, def: Long): Long
        fun putLong(key: String, value: Long)
        fun getBoolean(key: String, def: Boolean): Boolean
        fun putBoolean(key: String, value: Boolean)
        fun getString(key: String, def: String): String
        fun putString(key: String, value: String)
        fun getStringSet(key: String, def: Set<String>): Set<String>
        fun putStringSet(key: String, value: Set<String>)
        fun clear()
    }

    class SharedPrefsGraceStore(private val prefs: SharedPreferences) : GraceStore {
        override fun getLong(key: String, def: Long): Long = prefs.getLong(key, def)
        override fun putLong(key: String, value: Long) { prefs.edit().putLong(key, value).apply() }
        override fun getBoolean(key: String, def: Boolean): Boolean = prefs.getBoolean(key, def)
        override fun putBoolean(key: String, value: Boolean) { prefs.edit().putBoolean(key, value).apply() }
        override fun getString(key: String, def: String): String = prefs.getString(key, def).orEmpty()
        override fun putString(key: String, value: String) { prefs.edit().putString(key, value).apply() }
        override fun getStringSet(key: String, def: Set<String>): Set<String> = prefs.getStringSet(key, def) ?: def
        override fun putStringSet(key: String, value: Set<String>) { prefs.edit().putStringSet(key, value).apply() }
        override fun clear() { prefs.edit().clear().apply() }
    }

    class InMemoryGraceStore : GraceStore {
        private val longMap = mutableMapOf<String, Long>()
        private val boolMap = mutableMapOf<String, Boolean>()
        private val strMap = mutableMapOf<String, String>()
        private val setMap = mutableMapOf<String, Set<String>>()

        override fun getLong(key: String, def: Long): Long = longMap[key] ?: def
        override fun putLong(key: String, value: Long) { longMap[key] = value }
        override fun getBoolean(key: String, def: Boolean): Boolean = boolMap[key] ?: def
        override fun putBoolean(key: String, value: Boolean) { boolMap[key] = value }
        override fun getString(key: String, def: String): String = strMap[key] ?: def
        override fun putString(key: String, value: String) { strMap[key] = value }
        override fun getStringSet(key: String, def: Set<String>): Set<String> = setMap[key] ?: def
        override fun putStringSet(key: String, value: Set<String>) { setMap[key] = value.toSet() }
        override fun clear() {
            longMap.clear()
            boolMap.clear()
            strMap.clear()
            setMap.clear()
        }
    }

    interface Clock {
        fun currentTimeMillis(): Long
        fun elapsedRealtime(): Long
    }

    class SystemClockProvider : Clock {
        override fun currentTimeMillis(): Long = System.currentTimeMillis()
        override fun elapsedRealtime(): Long = SystemClock.elapsedRealtime()
    }

    interface UsageEventsProvider {
        fun queryEvents(context: Context, startMs: Long, endMs: Long): List<UsageEventModel>
    }

    class SystemUsageEventsProvider : UsageEventsProvider {
        override fun queryEvents(context: Context, startMs: Long, endMs: Long): List<UsageEventModel> {
            val list = mutableListOf<UsageEventModel>()
            try {
                val selected = getRestrictedPackages(context)
                if (selected.isEmpty()) return list
                val usm = context.getSystemService(Context.USAGE_STATS_SERVICE) as? UsageStatsManager ?: return list
                val events = usm.queryEvents(startMs, endMs) ?: return list
                val event = UsageEvents.Event()
                var count = 0
                while (events.hasNextEvent()) {
                    if (++count > 5000) break
                    events.getNextEvent(event)
                    // Keep opaque transition/screen boundaries to end a target interval,
                    // but never retain the identity of an unrelated or excluded app.
                    if (event.eventType !in setOf(1, 2, 23, 15, 16, 17, 18, 26, 27)) continue
                    list.add(
                        UsageEventModel(
                            packageName = event.packageName.orEmpty().takeIf { it in selected }.orEmpty(),
                            eventType = event.eventType,
                            timestampMs = event.timeStamp
                        )
                    )
                }
            } catch (e: Exception) {
                Log.w(TAG, "Error querying UsageEvents: ${e.message}")
            }
            return list
        }
    }

    interface ServiceStatusChecker {
        fun isAccessibilityStateKnown(context: Context): Boolean = true
        fun isAccessibilityServiceEnabled(context: Context): Boolean
        fun isServiceConnected(): Boolean
        fun hasUsageStatsPermission(context: Context): Boolean
        fun isFinancialPauseActive(context: Context): Boolean
    }

    class DefaultServiceStatusChecker : ServiceStatusChecker {
        override fun isAccessibilityStateKnown(context: Context): Boolean = runCatching {
            Settings.Secure.getInt(context.contentResolver, Settings.Secure.ACCESSIBILITY_ENABLED)
            Settings.Secure.getString(context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES)
            true
        }.getOrDefault(false)
        override fun isAccessibilityServiceEnabled(context: Context): Boolean {
            return HardwareDeviceVerifier.isAccessibilityServiceEnabled(context, StrictAccessibilityService::class.java)
        }
        override fun isServiceConnected(): Boolean {
            return StrictAccessibilityService.activeInstance != null
        }
        override fun hasUsageStatsPermission(context: Context): Boolean {
            return HardwareDeviceVerifier.hasUsageStatsPermission(context)
        }
        override fun isFinancialPauseActive(context: Context): Boolean {
            return FinancialSafePause.isPauseActive(context)
        }
    }

    interface LedgerWriter {
        fun recordPenalty(
            context: Context,
            date: String,
            episodeId: String,
            minuteIndex: Int,
            packageName: String,
            intervalStartMs: Long,
            intervalEndMs: Long,
            confirmedDurationMs: Long,
            points: Int
        ): Boolean
        fun hasScoreEvent(context: Context, eventId: String): Boolean
        fun getScore(context: Context, date: String): Int
        fun currentDate(context: Context): String
    }

    class LocalDbLedgerWriter : LedgerWriter {
        override fun recordPenalty(
            context: Context,
            date: String,
            episodeId: String,
            minuteIndex: Int,
            packageName: String,
            intervalStartMs: Long,
            intervalEndMs: Long,
            confirmedDurationMs: Long,
            points: Int
        ): Boolean {
            val db = StrictRecordsLocalDb.getInstance(context)
            return db.recordAccessibilityRestrictedAppPenalty(
                date = date,
                episodeId = episodeId,
                minuteIndex = minuteIndex,
                packageName = packageName,
                intervalStartMs = intervalStartMs,
                intervalEndMs = intervalEndMs,
                confirmedDurationMs = confirmedDurationMs,
                points = points
            )
        }

        override fun hasScoreEvent(context: Context, eventId: String): Boolean {
            val db = StrictRecordsLocalDb.getInstance(context)
            return db.hasScoreEvent(eventId)
        }

        override fun getScore(context: Context, date: String): Int {
            val db = StrictRecordsLocalDb.getInstance(context)
            return db.getScore(date, false)
        }

        override fun currentDate(context: Context): String {
            return try {
                StrictRecordsLocalDb.getInstance(context).nowDate()
            } catch (_: Exception) {
                java.text.SimpleDateFormat("yyyy-MM-dd", Locale.US).format(java.util.Date())
            }
        }
    }

    interface AlarmScheduler {
        fun scheduleAlarm(context: Context, triggerAtWallMs: Long, episodeId: String)
        fun cancelAlarm(context: Context)
    }

    class SystemAlarmScheduler : AlarmScheduler {
        override fun scheduleAlarm(context: Context, triggerAtWallMs: Long, episodeId: String) {
            try {
                val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
                val intent = Intent(context, AccessibilityGraceAlarmReceiver::class.java).apply {
                    action = AccessibilityGraceAlarmReceiver.ACTION_CHECK_GRACE
                    putExtra(AccessibilityGraceAlarmReceiver.EXTRA_EPISODE_ID, episodeId)
                }
                val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
                } else {
                    PendingIntent.FLAG_UPDATE_CURRENT
                }
                val pendingIntent = PendingIntent.getBroadcast(context, 8001, intent, flags)
                val canExact = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    alarmManager.canScheduleExactAlarms()
                } else {
                    true
                }
                if (canExact) {
                    alarmManager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtWallMs, pendingIntent)
                } else {
                    alarmManager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtWallMs, pendingIntent)
                }
                Log.i(TAG, "Scheduled grace alarm for $triggerAtWallMs (exact=$canExact)")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to schedule alarm: ${e.message}")
            }
        }

        override fun cancelAlarm(context: Context) {
            try {
                val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
                val intent = Intent(context, AccessibilityGraceAlarmReceiver::class.java).apply {
                    action = AccessibilityGraceAlarmReceiver.ACTION_CHECK_GRACE
                }
                val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE
                } else {
                    PendingIntent.FLAG_NO_CREATE
                }
                val pendingIntent = PendingIntent.getBroadcast(context, 8001, intent, flags)
                if (pendingIntent != null) {
                    alarmManager.cancel(pendingIntent)
                    pendingIntent.cancel()
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to cancel alarm: ${e.message}")
            }
        }
    }

    interface NotificationPoster {
        fun postInterruptionNotification(
            context: Context,
            episodeId: String,
            remainingMs: Long,
            deadlineWallMs: Long = 0L,
            missingAccessType: String = MISSING_TYPE_ACCESSIBILITY,
            totalPenalty: Int = 0,
            overdueSeconds: Long = 0L
        )
        fun cancelInterruptionNotification(context: Context)
    }

    class SystemNotificationPoster : NotificationPoster {
        override fun postInterruptionNotification(
            context: Context,
            episodeId: String,
            remainingMs: Long,
            deadlineWallMs: Long,
            missingAccessType: String,
            totalPenalty: Int,
            overdueSeconds: Long
        ) {
            try {
                val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    val channel = NotificationChannel(
                        CHANNEL_ID,
                        CHANNEL_NAME,
                        NotificationManager.IMPORTANCE_HIGH
                    ).apply {
                        description = "Alerts when StrictRoutines Accessibility or Usage Access is disabled"
                        enableLights(true)
                        lightColor = Color.RED
                        lockscreenVisibility = NotificationCompat.VISIBILITY_PUBLIC
                    }
                    nm.createNotificationChannel(channel)
                }

                val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
                } else {
                    PendingIntent.FLAG_UPDATE_CURRENT
                }

                val accIntent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).apply {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
                val accPendingIntent = PendingIntent.getActivity(context, 8002, accIntent, flags)

                val usageIntent = Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS).apply {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
                val usagePendingIntent = PendingIntent.getActivity(context, 8003, usageIntent, flags)

                val mm = (remainingMs.coerceAtLeast(0) / 60_000L)
                val ss = ((remainingMs.coerceAtLeast(0) % 60_000L) / 1000L)
                val timeStr = "%02d:%02d remaining".format(Locale.US, mm, ss)

                val effectiveDeadlineMs = if (deadlineWallMs > 0L) deadlineWallMs else (System.currentTimeMillis() + remainingMs)
                val deadlineTimeStr = try {
                    java.text.SimpleDateFormat("h:mm:ss a", Locale.getDefault()).format(java.util.Date(effectiveDeadlineMs))
                } catch (_: Exception) {
                    ""
                }

                val title = when (missingAccessType) {
                    MISSING_TYPE_USAGE_ACCESS -> "StrictRoutines Usage Access is OFF"
                    MISSING_TYPE_BOTH -> "StrictRoutines Permissions Missing"
                    else -> "StrictRoutines Accessibility is OFF"
                }

                val effectiveOverdueSec = if (overdueSeconds > 0L) overdueSeconds else maxOf(0L, (System.currentTimeMillis() - effectiveDeadlineMs) / 1000L)
                val overdueMm = effectiveOverdueSec / 60L
                val overdueSs = effectiveOverdueSec % 60L
                val overdueStr = "-%02d:%02d".format(Locale.US, overdueMm, overdueSs)

                val contentText = if (remainingMs > 0) {
                    when (missingAccessType) {
                        MISSING_TYPE_USAGE_ACCESS -> {
                            if (deadlineTimeStr.isNotBlank()) {
                                "Remaining grace: $timeStr (by $deadlineTimeStr). Grant Usage Access to avoid penalties."
                            } else {
                                "Usage Access revoked. Grant within $timeStr grace to avoid penalties."
                            }
                        }
                        MISSING_TYPE_BOTH -> {
                            if (deadlineTimeStr.isNotBlank()) {
                                "Remaining grace: $timeStr (by $deadlineTimeStr). Re-enable permissions to avoid penalties."
                            } else {
                                "Permissions disabled. Re-enable within $timeStr grace to avoid penalties."
                            }
                        }
                        else -> {
                            if (deadlineTimeStr.isNotBlank()) {
                                "Remaining grace: $timeStr (by $deadlineTimeStr). Re-enable to avoid penalties."
                            } else {
                                "Accessibility disabled in Settings. Re-enable within $timeStr grace to avoid penalties."
                            }
                        }
                    }
                } else {
                    if (totalPenalty > 0) {
                        "Overdue: $overdueStr. $totalPenalty point(s) deducted. Re-enable to resume monitoring."
                    } else {
                        "Overdue: $overdueStr. Grace period ended at $deadlineTimeStr. Re-enable to avoid penalties."
                    }
                }

                val primaryPendingIntent = if (missingAccessType == MISSING_TYPE_USAGE_ACCESS) usagePendingIntent else accPendingIntent

                val builder = NotificationCompat.Builder(context, CHANNEL_ID)
                    .setSmallIcon(android.R.drawable.stat_notify_error)
                    .setContentTitle(title)
                    .setContentText(contentText)
                    .setStyle(NotificationCompat.BigTextStyle().bigText(contentText))
                    .setPriority(NotificationCompat.PRIORITY_HIGH)
                    .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                    .setOngoing(true)
                    .setOnlyAlertOnce(true)
                    .setContentIntent(primaryPendingIntent)

                when (missingAccessType) {
                    MISSING_TYPE_USAGE_ACCESS -> {
                        builder.addAction(android.R.drawable.ic_menu_preferences, "OPEN USAGE ACCESS", usagePendingIntent)
                    }
                    MISSING_TYPE_BOTH -> {
                        builder.addAction(android.R.drawable.ic_menu_preferences, "ACCESSIBILITY", accPendingIntent)
                        builder.addAction(android.R.drawable.ic_menu_preferences, "USAGE ACCESS", usagePendingIntent)
                    }
                    else -> {
                        builder.addAction(android.R.drawable.ic_menu_preferences, "OPEN SETTINGS", accPendingIntent)
                    }
                }

                if (remainingMs > 0) {
                    builder.setWhen(effectiveDeadlineMs)
                    builder.setUsesChronometer(true)
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                        builder.setChronometerCountDown(true)
                    }
                }

                val notification = builder.build()
                nm.notify(NOTIFICATION_ID, notification)
            } catch (e: Exception) {
                Log.e(TAG, "Failed to post interruption notification: ${e.message}")
            }
        }

        override fun cancelInterruptionNotification(context: Context) {
            try {
                val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
                nm.cancel(NOTIFICATION_ID)
            } catch (e: Exception) {
                Log.e(TAG, "Failed to cancel notification: ${e.message}")
            }
        }
    }

    // Testing / Dependency Injections
    @Volatile private var customStore: GraceStore? = null
    @Volatile private var customClock: Clock? = null
    @Volatile private var customUsageEventsProvider: UsageEventsProvider? = null
    @Volatile private var customServiceChecker: ServiceStatusChecker? = null
    @Volatile private var customLedgerWriter: LedgerWriter? = null
    @Volatile private var customAlarmScheduler: AlarmScheduler? = null
    @Volatile private var customNotificationPoster: NotificationPoster? = null
    @Volatile private var customSelectedPackages: Set<String>? = null

    fun setStoreForTesting(store: GraceStore?) { customStore = store }
    fun setClockForTesting(clock: Clock?) { customClock = clock }
    fun setUsageEventsProviderForTesting(provider: UsageEventsProvider?) { customUsageEventsProvider = provider }
    fun setServiceStatusCheckerForTesting(checker: ServiceStatusChecker?) { customServiceChecker = checker }
    fun setLedgerWriterForTesting(writer: LedgerWriter?) { customLedgerWriter = writer }
    fun setAlarmSchedulerForTesting(scheduler: AlarmScheduler?) { customAlarmScheduler = scheduler }
    fun setNotificationPosterForTesting(poster: NotificationPoster?) { customNotificationPoster = poster }
    internal fun setSelectedPackagesForTesting(packages: Set<String>?) { customSelectedPackages = packages }

    fun resetForTesting() {
        customStore?.clear()
        customStore = null
        customClock = null
        customUsageEventsProvider = null
        customServiceChecker = null
        customLedgerWriter = null
        customAlarmScheduler = null
        customNotificationPoster = null
        customSelectedPackages = null
    }

    private fun resolveStore(context: Context): GraceStore {
        return customStore ?: SharedPrefsGraceStore(context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE))
    }

    private fun resolveClock(): Clock {
        return customClock ?: SystemClockProvider()
    }

    private fun resolveUsageProvider(): UsageEventsProvider {
        return customUsageEventsProvider ?: SystemUsageEventsProvider()
    }

    private fun resolveServiceChecker(): ServiceStatusChecker {
        return customServiceChecker ?: DefaultServiceStatusChecker()
    }

    private fun resolveLedgerWriter(): LedgerWriter {
        return customLedgerWriter ?: LocalDbLedgerWriter()
    }

    private fun resolveAlarmScheduler(): AlarmScheduler {
        return customAlarmScheduler ?: SystemAlarmScheduler()
    }

    private fun resolveNotificationPoster(): NotificationPoster {
        return customNotificationPoster ?: SystemNotificationPoster()
    }

    /**
     * Periodic or event-driven check for Accessibility and Usage Access state.
     * Starts grace timer when either required permission is disabled by user.
     */
    fun checkAccessibilityState(context: Context): AccessibilityStatus {
        val checker = resolveServiceChecker()
        val clock = resolveClock()
        val store = resolveStore(context)

        try {
            val isFinancialPause = checker.isFinancialPauseActive(context)
            if (isFinancialPause) {
                return getUiStatus(context)
            }

            val isEnabledInSettings = checker.isAccessibilityServiceEnabled(context)
            val isConnected = checker.isServiceConnected()
            val hasUsage = checker.hasUsageStatsPermission(context)
            if (!checker.isAccessibilityStateKnown(context)) throw IllegalStateException("Accessibility state is unknown")
            val warningBefore = centeredWarningState(context)
            val evidence = when {
                !isEnabledInSettings -> AccessibilityWarningClock.Evidence.DISABLED
                isConnected -> AccessibilityWarningClock.Evidence.ENABLED_CONNECTED
                else -> AccessibilityWarningClock.Evidence.ENABLED_DISCONNECTED
            }
            val boot = runCatching { Settings.Global.getInt(context.contentResolver, Settings.Global.BOOT_COUNT) }.getOrDefault(-1)
            val warningAfter = AccessibilityWarningClock.observe(warningBefore, evidence,
                clock.currentTimeMillis(), clock.elapsedRealtime(), boot)
            if (warningAfter != warningBefore) store.putString("centered_accessibility_clock", warningAfter.encode())
            val isInterrupted = store.getBoolean(KEY_IS_INTERRUPTED, false)

            val isAccOk = isEnabledInSettings && isConnected
            val isUsageOk = hasUsage

            if (isAccOk && isUsageOk) {
                if (isInterrupted) {
                    onAccessRestored(context, clock.currentTimeMillis(), clock.elapsedRealtime())
                } else {
                    runCatching { FloatingAccessibilityCountdownOverlay.hide() }
                }
            } else if (!isInterrupted) {
                // If enabled in settings but not yet connected, and usage is ok:
                // Transient rebind/startup grace — do not start false interruption
                if (isEnabledInSettings && !isConnected && isUsageOk) {
                    return getUiStatus(context)
                }

                // Genuine missing access!
                val missingType = when {
                    (!isEnabledInSettings || !isConnected) && !hasUsage -> MISSING_TYPE_BOTH
                    (!isEnabledInSettings || !isConnected) -> MISSING_TYPE_ACCESSIBILITY
                    else -> MISSING_TYPE_USAGE_ACCESS
                }
                onAccessMissing(context, missingType, clock.currentTimeMillis(), clock.elapsedRealtime())
            } else {
                // Already interrupted — preserve original T0, but update missing type if changed!
                val currentMissingType = store.getString(KEY_MISSING_ACCESS_TYPE, MISSING_TYPE_ACCESSIBILITY)
                val newMissingType = when {
                    (!isEnabledInSettings || !isConnected) && !hasUsage -> MISSING_TYPE_BOTH
                    (!isEnabledInSettings || !isConnected) -> MISSING_TYPE_ACCESSIBILITY
                    !hasUsage -> MISSING_TYPE_USAGE_ACCESS
                    else -> MISSING_TYPE_NONE
                }

                if (newMissingType != MISSING_TYPE_NONE && newMissingType != currentMissingType) {
                    store.putString(KEY_MISSING_ACCESS_TYPE, newMissingType)
                    val interruptedWallMs = store.getLong(KEY_INTERRUPTED_WALL_MS, clock.currentTimeMillis())
                    val deadlineWallMs = store.getLong(KEY_GRACE_DEADLINE_WALL_MS, interruptedWallMs + GRACE_DURATION_MS)
                    val remainingMs = maxOf(0L, deadlineWallMs - clock.currentTimeMillis())
                    val episodeId = store.getString(KEY_EPISODE_ID, "")
                    val totalPenalty = store.getLong(KEY_TOTAL_PENALTY, 0L).toInt()
                    val overdueSec = maxOf(0L, (clock.currentTimeMillis() - deadlineWallMs) / 1000L)
                    resolveNotificationPoster().postInterruptionNotification(
                        context, episodeId, remainingMs, deadlineWallMs, newMissingType, totalPenalty, overdueSec
                    )
                }

                // Reconcile/update countdown from stored state without resetting timer
                reconcileInterruption(context, clock.currentTimeMillis())
            }

            return getUiStatus(context)
        } catch (e: Exception) {
            Log.e(TAG, "Error in checkAccessibilityState: ${e.message}", e)
            return AccessibilityStatus(
                statusText = "Access check failed — retry required",
                isInterrupted = store.getBoolean(KEY_IS_INTERRUPTED, false),
                interruptedAtWallMs = store.getLong(KEY_INTERRUPTED_WALL_MS, 0L),
                graceDeadlineWallMs = store.getLong(KEY_GRACE_DEADLINE_WALL_MS, 0L),
                remainingGraceMs = 0L,
                confirmedUsageDurationMs = 0L,
                recordedPenaltyPoints = 0,
                canOpenAccessibilitySettings = true,
                stateCode = STATE_MONITORING_STATUS_UNKNOWN,
                episodeId = store.getString(KEY_EPISODE_ID, ""),
                accessState = AccessState.ACCESS_CHECK_FAILED,
                missingAccessType = store.getString(KEY_MISSING_ACCESS_TYPE, MISSING_TYPE_NONE),
                canOpenUsageAccessSettings = true
            )
        }
    }

    /**
     * Triggered when a required permission (Accessibility or Usage Access) becomes unavailable.
     * Preserves original T0 across rapid toggles and restarts.
     */
    fun onAccessMissing(
        context: Context,
        missingType: String = MISSING_TYPE_ACCESSIBILITY,
        nowWallMs: Long = resolveClock().currentTimeMillis(),
        nowElapsedMs: Long = resolveClock().elapsedRealtime()
    ) {
        val checker = resolveServiceChecker()
        if (checker.isFinancialPauseActive(context)) {
            return
        }

        val store = resolveStore(context)
        val alreadyInterrupted = store.getBoolean(KEY_IS_INTERRUPTED, false)
        if (alreadyInterrupted) {
            val currentType = store.getString(KEY_MISSING_ACCESS_TYPE, MISSING_TYPE_ACCESSIBILITY)
            if (missingType != currentType) {
                store.putString(KEY_MISSING_ACCESS_TYPE, missingType)
                val episodeId = store.getString(KEY_EPISODE_ID, "")
                val deadlineWallMs = store.getLong(KEY_GRACE_DEADLINE_WALL_MS, nowWallMs + GRACE_DURATION_MS)
                val remainingMs = maxOf(0L, deadlineWallMs - nowWallMs)
                val totalPenalty = store.getLong(KEY_TOTAL_PENALTY, 0L).toInt()
                val overdueSec = maxOf(0L, (nowWallMs - deadlineWallMs) / 1000L)
                resolveNotificationPoster().postInterruptionNotification(
                    context, episodeId, remainingMs, deadlineWallMs, missingType, totalPenalty, overdueSec
                )
            }
            return
        }

        val episodeId = "acc_ep_${nowWallMs}_" + UUID.randomUUID().toString().take(8)
        val alarmWallMs = nowWallMs + GRACE_DURATION_MS
        store.putString(KEY_EPISODE_ID, episodeId)
        store.putBoolean(KEY_IS_INTERRUPTED, true)
        store.putString(KEY_MISSING_ACCESS_TYPE, missingType)
        store.putLong(KEY_INTERRUPTED_WALL_MS, nowWallMs)
        store.putLong(KEY_INTERRUPTED_ELAPSED_MS, nowElapsedMs)
        store.putLong(KEY_GRACE_DEADLINE_WALL_MS, alarmWallMs)
        store.putString(KEY_LAST_STATUS, when (missingType) {
            MISSING_TYPE_USAGE_ACCESS -> "StrictRoutines Usage Access is OFF"
            MISSING_TYPE_BOTH -> "StrictRoutines Permissions Missing"
            else -> "StrictRoutines Accessibility is OFF"
        })
        store.putLong(KEY_RECONNECTED_WALL_MS, 0L)
        store.putStringSet(KEY_DEDUCTED_BLOCKS, emptySet())
        store.putLong(KEY_TOTAL_PENALTY, 0L)
        store.putLong(KEY_TOTAL_CONFIRMED_USAGE_MS, 0L)

        // Schedule exact/inexact alarm for 5-minute grace expiry
        store.putLong(KEY_ALARM_SCHEDULED_MS, alarmWallMs)
        resolveAlarmScheduler().scheduleAlarm(context, alarmWallMs, episodeId)

        // Show actionable notification
        resolveNotificationPoster().postInterruptionNotification(
            context, episodeId, GRACE_DURATION_MS, alarmWallMs, missingType, 0, 0L
        )

        // Show floating countdown overlay if accessibility is missing and overlay permission is granted
        if (missingType in setOf(MISSING_TYPE_ACCESSIBILITY, MISSING_TYPE_BOTH)) {
            runCatching {
                if (Settings.canDrawOverlays(context)) {
                    FloatingAccessibilityCountdownOverlay.showOrUpdate(context, GRACE_DURATION_MS)
                }
            }
        }

        Log.i(TAG, "Access missing started: type=$missingType episode=$episodeId at=$nowWallMs")
    }

    /**
     * Triggered when Accessibility service is genuinely disabled by user in Settings.
     */
    fun onAccessibilityDisabledByUser(
        context: Context,
        nowWallMs: Long = resolveClock().currentTimeMillis(),
        nowElapsedMs: Long = resolveClock().elapsedRealtime()
    ) {
        onAccessMissing(context, MISSING_TYPE_ACCESSIBILITY, nowWallMs, nowElapsedMs)
    }

    /**
     * Triggered when all required permissions are restored.
     */
    fun onAccessRestored(
        context: Context,
        nowWallMs: Long = resolveClock().currentTimeMillis(),
        nowElapsedMs: Long = resolveClock().elapsedRealtime()
    ) {
        val store = resolveStore(context)
        val isInterrupted = store.getBoolean(KEY_IS_INTERRUPTED, false)
        if (!isInterrupted) return

        store.putLong(KEY_RECONNECTED_WALL_MS, nowWallMs)

        // Perform final reconciliation up to reconnection time
        reconcileInterruption(context, nowWallMs)

        store.putBoolean(KEY_IS_INTERRUPTED, false)
        store.putString(KEY_MISSING_ACCESS_TYPE, MISSING_TYPE_NONE)
        store.putString(KEY_LAST_STATUS, "Monitoring restored")
        store.putLong(KEY_GRACE_DEADLINE_WALL_MS, 0L)
        FloatingDisciplineBotOverlay.hide()
        runCatching {
            FloatingAccessibilityCountdownOverlay.hide()
            AccessibilityWarningOverlay.hide()
            AccessibilityWarningOverlay.resetEntranceState()
        }

        // Cancel pending alarm & notification
        resolveAlarmScheduler().cancelAlarm(context)
        resolveNotificationPoster().cancelInterruptionNotification(context)

        Log.i(TAG, "Access restored and reconciled at $nowWallMs")
    }

    /**
     * Triggered when Accessibility service is reconnected by Android.
     * Must check complete monitoring state (both Accessibility AND Usage Access)
     * before marking monitoring as restored.
     */
    fun onAccessibilityReconnected(
        context: Context,
        nowWallMs: Long = resolveClock().currentTimeMillis(),
        nowElapsedMs: Long = resolveClock().elapsedRealtime()
    ) {
        checkAccessibilityState(context)
    }

    internal fun centeredWarningState(context: Context): AccessibilityWarningClock.State =
        AccessibilityWarningClock.State.decode(resolveStore(context).getString("centered_accessibility_clock", ""))

    /**
     * Reconciles the interruption episode using the exact authoritative formula:
     *   elapsedSeconds = max(0, confirmedInterruptionDurationSeconds)
     *   overdueSeconds = max(0, elapsedSeconds - 300)
     *   cumulativePenaltyPoints = floor(overdueSeconds / 60)
     *
     * Ledger commits each minute deduction exactly once with idempotent transaction IDs:
     *   tx_acc_${episodeId}_m${minute}
     */
    fun reconcileInterruption(
        context: Context,
        evaluationTimeWallMs: Long = resolveClock().currentTimeMillis()
    ): ReconcileResult {
        val store = resolveStore(context)
        val episodeId = store.getString(KEY_EPISODE_ID, "")
        if (episodeId.isBlank()) {
            return ReconcileResult("", 0, 0, 0, 0L, false)
        }

        val interruptedWallMs = store.getLong(KEY_INTERRUPTED_WALL_MS, 0L)
        if (interruptedWallMs <= 0L) {
            return ReconcileResult(episodeId, 0, 0, 0, 0L, false)
        }

        val isInterrupted = store.getBoolean(KEY_IS_INTERRUPTED, false)
        val reconnectedWallMs = store.getLong(KEY_RECONNECTED_WALL_MS, 0L)

        val upperTimeBound = if (reconnectedWallMs > 0L) {
            minOf(evaluationTimeWallMs, reconnectedWallMs)
        } else {
            evaluationTimeWallMs
        }

        val graceEndMs = interruptedWallMs + GRACE_DURATION_MS

        // Exact formula:
        // elapsedSeconds = max(0, (upperTimeBound - interruptedWallMs) / 1000)
        // overdueSeconds = max(0, elapsedSeconds - 300)
        // totalEligibleBlocks = floor(overdueSeconds / 60)
        val elapsedWholeSeconds = maxOf(0L, (upperTimeBound - interruptedWallMs) / 1000L)
        val overdueSeconds = maxOf(0L, elapsedWholeSeconds - 300L)
        val totalEligibleBlocks = minOf(720, (overdueSeconds / 60L).toInt())

        val deductedBlocks = store.getStringSet(KEY_DEDUCTED_BLOCKS, emptySet()).toMutableSet()
        var totalPenalty = store.getLong(KEY_TOTAL_PENALTY, 0L).toInt()
        var totalConfirmedUsageMs = store.getLong(KEY_TOTAL_CONFIRMED_USAGE_MS, 0L)
        var newDeductions = 0

        val checker = resolveServiceChecker()
        val hasUsagePermission = checker.hasUsageStatsPermission(context)
        val restrictedPackages = getRestrictedPackages(context)
        val financialPackages = FinancialSafePause.PROTECTED_FINANCIAL_PACKAGES

        val ledgerWriter = resolveLedgerWriter()
        val usageProvider = resolveUsageProvider()
        val dbDate = ledgerWriter.currentDate(context)
        val missingType = store.getString(KEY_MISSING_ACCESS_TYPE, MISSING_TYPE_ACCESSIBILITY)

        if (totalEligibleBlocks > 0) {
            for (k in 1..totalEligibleBlocks) {
                val blockKey = k.toString()
                val transactionId = "tx_acc_${episodeId}_m$k"

                if (deductedBlocks.contains(blockKey) || ledgerWriter.hasScoreEvent(context, transactionId)) {
                    deductedBlocks.add(blockKey)
                    continue
                }

                val blockStartMs = graceEndMs + (k - 1) * BLOCK_DURATION_MS
                val blockEndMs = graceEndMs + k * BLOCK_DURATION_MS

                var primaryPkg: String? = null
                if (hasUsagePermission) {
                    val events = usageProvider.queryEvents(context, blockStartMs - 60_000L, blockEndMs)
                    val usageResult = analyzeBlockUsage(events, blockStartMs, blockEndMs, restrictedPackages, financialPackages)
                    totalConfirmedUsageMs += usageResult.confirmedUsageDurationMs
                    primaryPkg = usageResult.primaryPackage
                }

                val recorded = ledgerWriter.recordPenalty(
                    context = context,
                    date = dbDate,
                    episodeId = episodeId,
                    minuteIndex = k,
                    packageName = primaryPkg ?: missingType,
                    intervalStartMs = blockStartMs,
                    intervalEndMs = blockEndMs,
                    confirmedDurationMs = 60_000L,
                    points = -1
                )
                if (recorded) {
                    newDeductions++
                    totalPenalty += 1
                }

                deductedBlocks.add(blockKey)
            }

            store.putStringSet(KEY_DEDUCTED_BLOCKS, deductedBlocks)
            store.putLong(KEY_TOTAL_PENALTY, totalPenalty.toLong())
            store.putLong(KEY_TOTAL_CONFIRMED_USAGE_MS, totalConfirmedUsageMs)
        }

        // If still interrupted, schedule next boundary check and update notification
        if (isInterrupted) {
            val nextAlarmWallMs = if (upperTimeBound < graceEndMs) {
                graceEndMs
            } else {
                graceEndMs + (totalEligibleBlocks + 1) * BLOCK_DURATION_MS
            }
            val lastScheduledAlarm = store.getLong(KEY_ALARM_SCHEDULED_MS, 0L)
            if (lastScheduledAlarm != nextAlarmWallMs) {
                store.putLong(KEY_ALARM_SCHEDULED_MS, nextAlarmWallMs)
                resolveAlarmScheduler().scheduleAlarm(context, nextAlarmWallMs, episodeId)
            }
            if (upperTimeBound >= graceEndMs || newDeductions > 0) {
                val remaining = (graceEndMs - upperTimeBound).coerceAtLeast(0)
                resolveNotificationPoster().postInterruptionNotification(
                    context, episodeId, remaining, graceEndMs, missingType, totalPenalty, overdueSeconds
                )
            }
        }

        return ReconcileResult(
            episodeId = episodeId,
            evaluatedBlocks = totalEligibleBlocks,
            newDeductionsCount = newDeductions,
            totalDeductedPoints = totalPenalty,
            totalConfirmedUsageMs = totalConfirmedUsageMs,
            isStillInterrupted = isInterrupted
        )
    }

    /**
     * Pure function to analyze UsageEvents in a 1-minute block [blockStartMs, blockEndMs].
     * Excludes screen-off, locked time, unverified gaps, and financial apps.
     * Returns whether at least 1 complete minute (>= 59.5s) of restricted-app foreground usage occurred.
     */
    fun analyzeBlockUsage(
        events: List<UsageEventModel>,
        blockStartMs: Long,
        blockEndMs: Long,
        restrictedPackages: Set<String>,
        financialPackages: Set<String>
    ): BlockUsageResult {
        if (events.isEmpty() || blockEndMs <= blockStartMs) {
            return BlockUsageResult(0L, null, isCompleteMinute = false, hasUncertainGap = true)
        }

        val sortedEvents = events.sortedBy { it.timestampMs }

        // Determine state at blockStartMs from lookback events
        var initialPkg: String? = null
        var isInteractive = true
        var isUnlocked = true
        var lastLookbackTimestamp = 0L

        for (e in sortedEvents) {
            if (e.timestampMs > blockStartMs) break
            lastLookbackTimestamp = e.timestampMs
            when (e.eventType) {
                15 -> isInteractive = true // SCREEN_INTERACTIVE
                16 -> { isInteractive = false; initialPkg = null } // SCREEN_NON_INTERACTIVE
                17 -> isUnlocked = true // KEYGUARD_HIDDEN
                18, 26, 27 -> { isUnlocked = false; initialPkg = null } // KEYGUARD_SHOWN
                1 -> { // ACTIVITY_RESUMED
                    val pkg = e.packageName
                    initialPkg = if (financialPackages.contains(pkg)) null else if (restrictedPackages.contains(pkg)) pkg else null
                }
                2, 23 -> { // ACTIVITY_PAUSED / STOPPED
                    if (e.packageName == initialPkg) initialPkg = null
                }
            }
        }

        // If the last lookback event is older than 60s, don't assume continuous usage across unverified gap
        if (initialPkg != null && lastLookbackTimestamp > 0 && blockStartMs - lastLookbackTimestamp > 60_000L) {
            initialPkg = null
        }

        var currentPkg = initialPkg
        var lastTime = blockStartMs
        val packageDurations = mutableMapOf<String, Long>()

        for (e in sortedEvents) {
            if (e.timestampMs <= blockStartMs) continue
            if (e.timestampMs > blockEndMs) break

            val eventTime = e.timestampMs
            val delta = eventTime - lastTime

            if (delta > 0 && isInteractive && isUnlocked && currentPkg != null &&
                restrictedPackages.contains(currentPkg) && !financialPackages.contains(currentPkg)) {
                packageDurations[currentPkg] = (packageDurations[currentPkg] ?: 0L) + delta
            }

            lastTime = eventTime

            when (e.eventType) {
                15 -> isInteractive = true
                16 -> { isInteractive = false; currentPkg = null }
                17 -> isUnlocked = true
                18, 26, 27 -> { isUnlocked = false; currentPkg = null }
                1 -> {
                    val pkg = e.packageName
                    currentPkg = if (financialPackages.contains(pkg)) null else if (restrictedPackages.contains(pkg)) pkg else null
                }
                2, 23 -> {
                    if (e.packageName == currentPkg) currentPkg = null
                }
            }
        }

        // Tail segment up to blockEndMs
        val tailDelta = blockEndMs - lastTime
        if (tailDelta > 0 && isInteractive && isUnlocked && currentPkg != null &&
            restrictedPackages.contains(currentPkg) && !financialPackages.contains(currentPkg)) {
            packageDurations[currentPkg] = (packageDurations[currentPkg] ?: 0L) + tailDelta
        }

        val totalUsageMs = packageDurations.values.sum()
        val primaryPackage = packageDurations.maxByOrNull { it.value }?.key
        val isCompleteMinute = totalUsageMs >= 59_500L

        return BlockUsageResult(
            confirmedUsageDurationMs = totalUsageMs,
            primaryPackage = primaryPackage,
            isCompleteMinute = isCompleteMinute,
            hasUncertainGap = false
        )
    }

    /**
     * Determines UI status according to the exact strings specified in Section 7.
     */
    fun getUiStatus(
        context: Context,
        nowWallMs: Long = resolveClock().currentTimeMillis(),
        nowElapsedMs: Long = resolveClock().elapsedRealtime()
    ): AccessibilityStatus {
        val checker = resolveServiceChecker()
        val store = resolveStore(context)

        // 1. Financial Safe Pause
        if (checker.isFinancialPauseActive(context)) {
            return AccessibilityStatus(
                statusText = "Financial Safe Pause — excluded app",
                isInterrupted = false,
                interruptedAtWallMs = 0L,
                graceDeadlineWallMs = 0L,
                remainingGraceMs = 0L,
                confirmedUsageDurationMs = 0L,
                recordedPenaltyPoints = 0,
                canOpenAccessibilitySettings = false,
                stateCode = STATE_ACCESSIBILITY_ACTIVE,
                episodeId = "",
                accessState = AccessState.ACCESS_OK,
                missingAccessType = MISSING_TYPE_NONE,
                canOpenUsageAccessSettings = false
            )
        }

        val isEnabledInSettings = checker.isAccessibilityServiceEnabled(context)
        val isConnected = checker.isServiceConnected()
        val hasUsage = checker.hasUsageStatsPermission(context)
        val isInterrupted = store.getBoolean(KEY_IS_INTERRUPTED, false)
        val storedMissingType = store.getString(KEY_MISSING_ACCESS_TYPE, MISSING_TYPE_NONE)
        val interruptedWallMs = store.getLong(KEY_INTERRUPTED_WALL_MS, 0L)
        val interruptedElapsedMs = store.getLong(KEY_INTERRUPTED_ELAPSED_MS, 0L)
        val reconnectedWallMs = store.getLong(KEY_RECONNECTED_WALL_MS, 0L)
        val totalPenalty = store.getLong(KEY_TOTAL_PENALTY, 0L).toInt()
        val confirmedUsageMs = store.getLong(KEY_TOTAL_CONFIRMED_USAGE_MS, 0L)
        val episodeId = store.getString(KEY_EPISODE_ID, "")

        // 2. Both Connected & Active
        if (isEnabledInSettings && isConnected && hasUsage) {
            val recentlyRestored = reconnectedWallMs > 0 && (nowWallMs - reconnectedWallMs in 0L..15_000L)
            val text = if (recentlyRestored) "Monitoring restored" else "Accessibility active"
            val code = if (recentlyRestored) STATE_RESTORED_MONITORING_ACTIVE else STATE_ACCESSIBILITY_ACTIVE
            return AccessibilityStatus(
                statusText = text,
                isInterrupted = false,
                interruptedAtWallMs = 0L,
                graceDeadlineWallMs = 0L,
                remainingGraceMs = 0L,
                confirmedUsageDurationMs = confirmedUsageMs,
                recordedPenaltyPoints = totalPenalty,
                canOpenAccessibilitySettings = false,
                stateCode = code,
                episodeId = "",
                accessState = if (recentlyRestored) AccessState.RESTORING else AccessState.ACCESS_OK,
                missingAccessType = MISSING_TYPE_NONE,
                canOpenUsageAccessSettings = false
            )
        }

        // 3. Accessibility enabled in Settings, but transiently disconnected while NOT already interrupted (e.g. startup / reboot)
        if (isEnabledInSettings && !isConnected && hasUsage && !isInterrupted) {
            return AccessibilityStatus(
                statusText = "Monitoring status unknown — reconnecting",
                isInterrupted = false,
                interruptedAtWallMs = 0L,
                graceDeadlineWallMs = 0L,
                remainingGraceMs = 0L,
                confirmedUsageDurationMs = confirmedUsageMs,
                recordedPenaltyPoints = totalPenalty,
                canOpenAccessibilitySettings = false,
                stateCode = STATE_MONITORING_STATUS_UNKNOWN,
                episodeId = "",
                accessState = AccessState.RECHECK_REQUIRED,
                missingAccessType = MISSING_TYPE_NONE,
                canOpenUsageAccessSettings = false
            )
        }

        // 4. Missing Access (Accessibility, Usage Access, or Both)
        val effectiveMissingType = when {
            (!isEnabledInSettings || !isConnected) && !hasUsage -> MISSING_TYPE_BOTH
            (!isEnabledInSettings || !isConnected) -> MISSING_TYPE_ACCESSIBILITY
            !hasUsage -> MISSING_TYPE_USAGE_ACCESS
            storedMissingType != MISSING_TYPE_NONE -> storedMissingType
            else -> MISSING_TYPE_ACCESSIBILITY
        }

        val effectiveAccessState = when (effectiveMissingType) {
            MISSING_TYPE_BOTH -> AccessState.BOTH_ACCESS_MISSING
            MISSING_TYPE_USAGE_ACCESS -> AccessState.USAGE_ACCESS_MISSING
            else -> AccessState.ACCESSIBILITY_MISSING
        }

        val effectiveStart = if (interruptedWallMs > 0L) interruptedWallMs else nowWallMs
        val graceDeadline = effectiveStart + GRACE_DURATION_MS

        val isSameBoot = interruptedElapsedMs > 0L && nowElapsedMs >= interruptedElapsedMs &&
                (nowElapsedMs - interruptedElapsedMs) <= (GRACE_DURATION_MS + 24 * 3600_000L)
        val remainingGrace = if (isSameBoot) {
            maxOf(0L, GRACE_DURATION_MS - (nowElapsedMs - interruptedElapsedMs))
        } else {
            maxOf(0L, graceDeadline - nowWallMs)
        }

        val elapsedWholeSeconds = if (isSameBoot) {
            maxOf(0L, (nowElapsedMs - interruptedElapsedMs) / 1000L)
        } else {
            maxOf(0L, (nowWallMs - effectiveStart) / 1000L)
        }
        val overdueSec = maxOf(0L, elapsedWholeSeconds - 300L)
        val overdueMm = overdueSec / 60L
        val overdueSs = overdueSec % 60L
        val overdueFmt = "-%02d:%02d".format(Locale.US, overdueMm, overdueSs)

        val stateCode = if (remainingGrace > 0L) {
            STATE_GRACE_PERIOD_ACTIVE
        } else {
            STATE_PENALTY_ELIGIBLE
        }

        val mm = remainingGrace / 60_000L
        val ss = (remainingGrace % 60_000L) / 1000L
        val timeFmt = "%02d:%02d".format(Locale.US, mm, ss)

        val missingPrefix = when (effectiveMissingType) {
            MISSING_TYPE_BOTH -> "Permissions missing"
            MISSING_TYPE_USAGE_ACCESS -> "Usage Access revoked"
            else -> "Accessibility interrupted"
        }

        val statusText = when {
            remainingGrace > 0L -> {
                "$missingPrefix — %s grace remaining".format(timeFmt)
            }
            totalPenalty > 0 -> {
                "$missingPrefix — $overdueFmt overdue ($totalPenalty pt(s) deducted)"
            }
            else -> {
                "$missingPrefix — $overdueFmt overdue"
            }
        }

        return AccessibilityStatus(
            statusText = statusText,
            isInterrupted = true,
            interruptedAtWallMs = effectiveStart,
            graceDeadlineWallMs = graceDeadline,
            remainingGraceMs = remainingGrace,
            confirmedUsageDurationMs = confirmedUsageMs,
            recordedPenaltyPoints = totalPenalty,
            canOpenAccessibilitySettings = (!isEnabledInSettings || !isConnected),
            stateCode = stateCode,
            episodeId = episodeId,
            accessState = effectiveAccessState,
            missingAccessType = effectiveMissingType,
            canOpenUsageAccessSettings = !hasUsage,
            overdueSeconds = overdueSec
        )
    }

    private fun getRestrictedPackages(context: Context): Set<String> {
        customSelectedPackages?.let { return it }
        return try {
            if (!TargetAppMonitoringPolicy.hasUsageConsent(context)) return emptySet()
            val targets = TargetAppMonitoringPolicy.installedTargets(context)
            val selected = targets.filter { it.selected }.flatMap { it.installedPackages }.toSet()
            selected
        } catch (_: Exception) {
            emptySet()
        }
    }
}
