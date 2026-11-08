package com.strictrecords.app

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Process
import android.provider.Settings
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Collections
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID

/**
 * Android Application Usage Monitoring Engine for Strict Session.
 *
 * Uses Android's supported UsageStatsManager and UsageEvents APIs to monitor
 * foreground application transitions and screen lifecycle states.
 *
 * Implements:
 * - PACKAGE_USAGE_STATS permission checking and direct shortcut to Settings.
 * - Configurable package allowlist (StrictRecords, System UI, Keyguard, Keyboards, Emergency Phone).
 * - Persistent event cursor tracking to prevent duplicate violation events.
 * - Phase-aware phone usage policy evaluation (FOCUS_ACTIVE vs FOCUS_BUFFER / PROOF_SUBMISSION_OPEN).
 * - Rolling recent foreground app transitions tracking for consolidated 30s reporting.
 * - Single-strike per unapproved app entry deduplication.
 * - Screen lock / unlock transition handling without false alarms.
 */
object StrictSessionUsageMonitor {

    private const val TAG = "StrictSessionUsage"
    private const val PREFS_NAME = "strict_usage_monitor_prefs"
    private const val KEY_LAST_CURSOR_MS = "last_event_cursor_ms"

    @Volatile
    var lastUnauthorizedPackage: String = ""

    @Volatile
    private var lastRecordedForegroundPackage: String = ""

    @Volatile
    private var lastRecordedForegroundStartMs: Long = 0L

    data class AppTransition(
        val packageName: String,
        val appLabel: String,
        val category: String,
        val startTimestamp: String,
        val endTimestamp: String,
        val durationSeconds: Long
    ) {
        fun toJsonObject(): JSONObject = JSONObject().apply {
            put("package_name", packageName)
            put("app_name", appLabel)
            put("category", category)
            put("start_time", startTimestamp)
            put("end_time", endTimestamp)
            put("duration_seconds", durationSeconds)
        }
    }

    private val recentTransitions = Collections.synchronizedList(mutableListOf<AppTransition>())

    fun addTransition(transition: AppTransition) {
        synchronized(recentTransitions) {
            recentTransitions.add(transition)
            while (recentTransitions.size > 30) {
                recentTransitions.removeAt(0)
            }
        }
    }

    fun getRecentTransitionsJson(): JSONArray {
        val arr = JSONArray()
        synchronized(recentTransitions) {
            for (t in recentTransitions) {
                arr.put(t.toJsonObject())
            }
        }
        return arr
    }

    fun clearRecentTransitions() {
        recentTransitions.clear()
        lastUnauthorizedPackage = ""
        lastRecordedForegroundPackage = ""
        lastRecordedForegroundStartMs = 0L
    }

    fun getAppCategory(context: Context, packageName: String): String {
        if (packageName in ALLOWED_PACKAGES) {
            return "SYSTEM"
        }
        return try {
            val pm = context.packageManager
            val appInfo = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                pm.getApplicationInfo(packageName, PackageManager.ApplicationInfoFlags.of(0))
            } else {
                @Suppress("DEPRECATION")
                pm.getApplicationInfo(packageName, 0)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                when (appInfo.category) {
                    android.content.pm.ApplicationInfo.CATEGORY_GAME -> "GAMES"
                    android.content.pm.ApplicationInfo.CATEGORY_AUDIO,
                    android.content.pm.ApplicationInfo.CATEGORY_VIDEO,
                    android.content.pm.ApplicationInfo.CATEGORY_IMAGE -> "ENTERTAINMENT"
                    android.content.pm.ApplicationInfo.CATEGORY_SOCIAL -> "SOCIAL"
                    android.content.pm.ApplicationInfo.CATEGORY_NEWS -> "NEWS"
                    android.content.pm.ApplicationInfo.CATEGORY_PRODUCTIVITY -> "PRODUCTIVITY"
                    else -> "OTHER"
                }
            } else {
                "OTHER"
            }
        } catch (_: Exception) {
            "UNKNOWN"
        }
    }

    fun getCurrentForegroundPackage(context: Context): Pair<String, Long> {
        val usageStatsManager = context.getSystemService(Context.USAGE_STATS_SERVICE) as? UsageStatsManager
            ?: return Pair("UNKNOWN", 0L)
        val now = System.currentTimeMillis()
        try {
            val events = usageStatsManager.queryEvents(now - 30_000, now)
            val event = UsageEvents.Event()
            var latestPkg = ""
            var latestTime = 0L
            while (events.hasNextEvent()) {
                events.getNextEvent(event)
                if (event.eventType == UsageEvents.Event.ACTIVITY_RESUMED) {
                    if (event.timeStamp >= latestTime) {
                        latestTime = event.timeStamp
                        latestPkg = event.packageName ?: ""
                    }
                }
            }
            if (latestPkg.isNotBlank()) {
                return Pair(latestPkg, latestTime)
            }
        } catch (_: Exception) {}
        return Pair("UNKNOWN", 0L)
    }

    private val DEFAULT_ALLOWED_PACKAGES: Set<String> = setOf(
        // StrictRecords APK itself
        "com.strictrecords.app",

        // Android System UI & Navigation
        "android",
        "com.android.systemui",
        "com.android.keyguard",
        "com.android.settings",

        // Permission & Security Controllers
        "com.android.permissioncontroller",
        "com.google.android.permissioncontroller",

        // Standard Launchers / Home Screens (Viewing Home/Lockscreen is NOT a violation)
        "com.sec.android.app.launcher",
        "com.google.android.apps.nexuslauncher",
        "com.android.launcher",
        "com.android.launcher3",
        "com.miui.home",
        "com.oppo.launcher",
        "com.oneplus.launcher",

        // Keyboards / Input Methods
        "com.google.android.inputmethod.latin",
        "com.samsung.android.honeyboard",
        "com.touchtype.swiftkey",
        "com.nuance.swype.dtc",

        // Emergency Phone & System Telecom
        "com.samsung.android.dialer",
        "com.google.android.dialer",
        "com.android.dialer",
        "com.android.phone",
        "com.android.server.telecom",
        "com.android.incallui",
        "com.samsung.android.incallui",

        // Standard Document Pickers / File Choosers (Permitted for Proof Submission)
        "com.google.android.documentsui",
        "com.android.documentsui",
        "com.sec.android.app.myfiles"
    )

    private val dynamicAllowedPackages = Collections.synchronizedSet(mutableSetOf<String>())

    val ALLOWED_PACKAGES: Set<String>
        get() = DEFAULT_ALLOWED_PACKAGES + dynamicAllowedPackages

    fun updateAllowedPackages(packagesCsvOrJson: String) {
        try {
            dynamicAllowedPackages.clear()
            val trimmed = packagesCsvOrJson.trim()
            if (trimmed.startsWith("[")) {
                val arr = JSONArray(trimmed)
                for (i in 0 until arr.length()) {
                    val p = arr.getString(i).trim()
                    if (p.isNotBlank()) dynamicAllowedPackages.add(p)
                }
            } else {
                trimmed.split(",").forEach {
                    val p = it.trim()
                    if (p.isNotBlank()) dynamicAllowedPackages.add(p)
                }
            }
            Log.i(TAG, "Dynamic allowed packages updated: $dynamicAllowedPackages")
        } catch (e: Exception) {
            Log.w(TAG, "Error updating allowed packages: ${e.message}")
        }
    }

    data class UsageViolation(
        val eventId: String,
        val sessionId: String,
        val userId: String,
        val deviceId: String,
        val packageName: String,
        val appLabel: String,
        val category: String = "OTHER",
        val eventTimestamp: String,
        val detectionTimestamp: String,
        val detectionSource: String = "ANDROID_USAGE_STATS",
        val sessionPhase: String = "FOCUS_ACTIVE",
        val submissionWindowStatus: String = "CLOSED",
        val processingStatus: String = "UNACKNOWLEDGED"
    ) {
        fun toJsonObject(): JSONObject {
            return JSONObject().apply {
                put("event_id", eventId)
                put("session_id", sessionId)
                put("user_id", userId)
                put("device_id", deviceId)
                put("package_name", packageName)
                put("app_label", appLabel)
                put("category", category)
                put("event_timestamp", eventTimestamp)
                put("detection_timestamp", detectionTimestamp)
                put("detection_source", detectionSource)
                put("session_phase", sessionPhase)
                put("submission_window_status", submissionWindowStatus)
                put("processing_status", processingStatus)
            }
        }
    }

    fun hasUsagePermission(context: Context): Boolean {
        return try {
            val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as? AppOpsManager ?: return false
            val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                appOps.unsafeCheckOpNoThrow(
                    AppOpsManager.OPSTR_GET_USAGE_STATS,
                    Process.myUid(),
                    context.packageName
                )
            } else {
                @Suppress("DEPRECATION")
                appOps.checkOpNoThrow(
                    AppOpsManager.OPSTR_GET_USAGE_STATS,
                    Process.myUid(),
                    context.packageName
                )
            }
            mode == AppOpsManager.MODE_ALLOWED
        } catch (e: Exception) {
            Log.e(TAG, "Error checking usage stats permission: ${e.message}")
            false
        }
    }

    fun openUsageAccessSettings(context: Context) {
        try {
            val intent = Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(intent)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to open usage access settings: ${e.message}")
        }
    }

    fun setEventCursorMs(context: Context, cursorMs: Long) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putLong(KEY_LAST_CURSOR_MS, cursorMs)
            .apply()
    }

    fun getEventCursorMs(context: Context): Long {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getLong(KEY_LAST_CURSOR_MS, 0L)
    }

    fun formatIso(ms: Long): String {
        val sdf = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
        sdf.timeZone = TimeZone.getTimeZone("UTC")
        return sdf.format(Date(ms))
    }

    fun getAppLabel(context: Context, packageName: String): String {
        return try {
            val pm = context.packageManager
            val appInfo = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                pm.getApplicationInfo(packageName, PackageManager.ApplicationInfoFlags.of(0))
            } else {
                @Suppress("DEPRECATION")
                pm.getApplicationInfo(packageName, 0)
            }
            pm.getApplicationLabel(appInfo).toString()
        } catch (_: Exception) {
            packageName
        }
    }

    @Synchronized
    fun queryUsageEvents(
        context: Context,
        sessionId: String,
        userId: String,
        deviceId: String,
        sessionPhase: String = "FOCUS_ACTIVE",
        isPhoneUsageAllowed: Boolean = false,
        isSubmissionWindowOpen: Boolean = false,
        nowMs: Long = System.currentTimeMillis()
    ): List<UsageViolation> {
        if (!hasUsagePermission(context)) {
            Log.w(TAG, "PACKAGE_USAGE_STATS permission not granted. Skipping event query.")
            return emptyList()
        }

        val usageStatsManager = context.getSystemService(Context.USAGE_STATS_SERVICE) as? UsageStatsManager
        if (usageStatsManager == null) {
            Log.e(TAG, "UsageStatsManager unavailable.")
            return emptyList()
        }

        var cursorMs = getEventCursorMs(context)
        if (cursorMs <= 0L) {
            cursorMs = nowMs - 10_000L
            setEventCursorMs(context, cursorMs)
        }

        if (cursorMs >= nowMs) {
            return emptyList()
        }

        val violations = mutableListOf<UsageViolation>()
        var highestEventMs = cursorMs

        try {
            val events = usageStatsManager.queryEvents(cursorMs, nowMs)
            val event = UsageEvents.Event()

            while (events.hasNextEvent()) {
                events.getNextEvent(event)
                val eventTime = event.timeStamp
                if (eventTime > highestEventMs) {
                    highestEventMs = eventTime
                }

                val pkg = event.packageName ?: continue

                // Check foreground application transitions
                if (event.eventType == UsageEvents.Event.ACTIVITY_RESUMED) {
                    // Log previous transition if package changed
                    if (lastRecordedForegroundPackage.isNotBlank() && lastRecordedForegroundPackage != pkg && lastRecordedForegroundStartMs > 0L && eventTime >= lastRecordedForegroundStartMs) {
                        val durationSec = ((eventTime - lastRecordedForegroundStartMs) / 1000L).coerceAtLeast(1L)
                        val transition = AppTransition(
                            packageName = lastRecordedForegroundPackage,
                            appLabel = getAppLabel(context, lastRecordedForegroundPackage),
                            category = getAppCategory(context, lastRecordedForegroundPackage),
                            startTimestamp = formatIso(lastRecordedForegroundStartMs),
                            endTimestamp = formatIso(eventTime),
                            durationSeconds = durationSec
                        )
                        addTransition(transition)
                    }

                    lastRecordedForegroundPackage = pkg
                    lastRecordedForegroundStartMs = eventTime

                    val isAllowed = pkg in ALLOWED_PACKAGES
                    if (!isAllowed) {
                        // Phone usage not allowed during FOCUS_ACTIVE
                        if (!isPhoneUsageAllowed) {
                            if (pkg == lastUnauthorizedPackage) {
                                // Deduplication: continuous stay in same app generates ONE violation on initial entry
                                continue
                            }
                            lastUnauthorizedPackage = pkg
                            val appLabel = getAppLabel(context, pkg)
                            val category = getAppCategory(context, pkg)
                            val violation = UsageViolation(
                                eventId = "phone_violation_" + UUID.randomUUID().toString().replace("-", "").take(16),
                                sessionId = sessionId,
                                userId = userId,
                                deviceId = deviceId,
                                packageName = pkg,
                                appLabel = appLabel,
                                category = category,
                                eventTimestamp = formatIso(eventTime),
                                detectionTimestamp = formatIso(nowMs),
                                detectionSource = "ANDROID_USAGE_STATS",
                                sessionPhase = sessionPhase,
                                submissionWindowStatus = if (isSubmissionWindowOpen) "OPEN" else "CLOSED",
                                processingStatus = "UNACKNOWLEDGED"
                            )
                            violations.add(violation)
                            Log.w(TAG, "UNAUTHORIZED APP DETECTED during $sessionPhase: $pkg ($appLabel) at $eventTime")
                        } else {
                            // Phone usage is allowed during FOCUS_BUFFER or PROOF_SUBMISSION_OPEN
                            Log.d(TAG, "App opened during allowed phase ($sessionPhase): $pkg")
                        }
                    } else {
                        // Allowed app/launcher active -> reset unauthorized state so next entry triggers new event
                        lastUnauthorizedPackage = ""
                    }
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error querying UsageEvents: ${e.message}", e)
        }

        // Advance persistent cursor past highest processed event
        setEventCursorMs(context, (highestEventMs + 1L).coerceAtLeast(nowMs))
        return violations
    }
}
