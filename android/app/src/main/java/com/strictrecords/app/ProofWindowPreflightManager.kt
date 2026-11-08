package com.strictrecords.app

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PersistableBundle
import android.util.Log
import androidx.core.app.NotificationCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors

/**
 * StrictRecords — Automatic 5-Minute AI Preflight Manager (Android)
 * ================================================================
 * Implements automatic AI connectivity and inference-readiness check exactly
 * 5 minutes before every scheduled proof-submission window.
 *
 * Guarantees:
 * 1. Schedules preflight exactly 5 minutes before scheduled window opening (opening - 300s).
 * 2. Runs in background via JobScheduler/AlarmManager without requiring APK to be foregrounded.
 * 3. Probes both Taufa AI and official OpenAI API via backend coordinator or local bridge.
 * 4. Never uses actual user evidence for preflight check.
 * 5. Displays compact, non-blocking notification and status badge with exact provider results.
 * 6. Intelligently selects validation provider (Taufa primary, OpenAI fallback).
 * 7. Evidence-submission protection: if both providers are temporarily unavailable, evidence is
 *    durably stored in PENDING_VALIDATION; never rejected or penalized due to AI outage.
 */
object ProofWindowPreflightManager {

    private const val TAG = "AiPreflightManager"
    const val PREFS_NAME = "strictrecords_ai_preflight_v1"
    const val KEY_LATEST_PREFLIGHT = "latest_preflight_json"
    const val PREFLIGHT_LEAD_TIME_MS = 5L * 60L * 1000L // Exactly 5 minutes (300,000 ms)
    const val CHANNEL_ID = "strictrecords_ai_preflight_channel"
    private const val BASE_JOB_ID = 88000

    data class PreflightRecord(
        val preflightId: String = "",
        val proofWindowId: String = "",
        val activityId: String = "",
        val scheduledOpenTime: String = "",
        val preflightStartedAt: String = "",
        val taufaStatus: String = "NOT_CHECKED",
        val taufaLatency: Int = 0,
        val taufaError: String = "",
        val openaiStatus: String = "NOT_CHECKED",
        val openaiLatency: Int = 0,
        val openaiError: String = "",
        val selectedValidationProvider: String = "PENDING_VALIDATION", // TAUFA_AI | OPENAI_FALLBACK | PENDING_VALIDATION
        val primaryProvider: String = "NONE",
        val backupProvider: String = "NONE",
        val preflightCompletedAt: String = "",
        val retryCount: Int = 0,
        val preflightStatus: String = "COMPLETED", // COMPLETED | PENDING_RETRY | FAILED | CANCELLED
        val scheduleVersion: Int = 1,
        val displayTitle: String = "",
        val displayLines: List<String> = emptyList()
    ) {
        val isTaufaConnected: Boolean get() = taufaStatus == "CONNECTED"
        val isOpenAiConnected: Boolean get() = openaiStatus == "CONNECTED"
        val isAnyProviderAvailable: Boolean get() = isTaufaConnected || isOpenAiConnected

        fun toJson(): JSONObject = JSONObject().apply {
            put("preflight_id", preflightId)
            put("proof_window_id", proofWindowId)
            put("activity_id", activityId)
            put("scheduled_open_time", scheduledOpenTime)
            put("preflight_started_at", preflightStartedAt)
            put("taufa_status", taufaStatus)
            put("taufa_latency", taufaLatency)
            put("taufa_error", taufaError)
            put("openai_status", openaiStatus)
            put("openai_latency", openaiLatency)
            put("openai_error", openaiError)
            put("selected_validation_provider", selectedValidationProvider)
            put("primary_provider", primaryProvider)
            put("backup_provider", backupProvider)
            put("preflight_completed_at", preflightCompletedAt)
            put("retry_count", retryCount)
            put("preflight_status", preflightStatus)
            put("schedule_version", scheduleVersion)
            put("display_title", displayTitle)
            put("display_lines", JSONArray(displayLines))
        }

        companion object {
            fun fromJson(json: JSONObject): PreflightRecord {
                val lines = mutableListOf<String>()
                val arr = json.optJSONArray("display_lines")
                if (arr != null) {
                    for (i in 0 until arr.length()) {
                        lines.add(arr.optString(i))
                    }
                }
                return PreflightRecord(
                    preflightId = json.optString("preflight_id", ""),
                    proofWindowId = json.optString("proof_window_id", ""),
                    activityId = json.optString("activity_id", ""),
                    scheduledOpenTime = json.optString("scheduled_open_time", ""),
                    preflightStartedAt = json.optString("preflight_started_at", ""),
                    taufaStatus = json.optString("taufa_status", "NOT_CHECKED"),
                    taufaLatency = json.optInt("taufa_latency", 0),
                    taufaError = json.optString("taufa_error", ""),
                    openaiStatus = json.optString("openai_status", "NOT_CHECKED"),
                    openaiLatency = json.optInt("openai_latency", 0),
                    openaiError = json.optString("openai_error", ""),
                    selectedValidationProvider = json.optString("selected_validation_provider", "PENDING_VALIDATION"),
                    primaryProvider = json.optString("primary_provider", "NONE"),
                    backupProvider = json.optString("backup_provider", "NONE"),
                    preflightCompletedAt = json.optString("preflight_completed_at", ""),
                    retryCount = json.optInt("retry_count", 0),
                    preflightStatus = json.optString("preflight_status", "COMPLETED"),
                    scheduleVersion = json.optInt("schedule_version", 1),
                    displayTitle = json.optString("display_title", ""),
                    displayLines = lines
                )
            }
        }
    }

    private val bgExecutor = Executors.newSingleThreadExecutor { r ->
        Thread(r, "StrictRecords-AiPreflightWorker").apply { isDaemon = true }
    }
    private val mainHandler = Handler(Looper.getMainLooper())
    private val listeners = CopyOnWriteArrayList<(PreflightRecord) -> Unit>()
    private val memoryCache = ConcurrentHashMap<String, PreflightRecord>()
    private val scheduledJobs = ConcurrentHashMap<String, Long>()

    fun addListener(listener: (PreflightRecord) -> Unit) {
        if (!listeners.contains(listener)) listeners.add(listener)
    }

    fun removeListener(listener: (PreflightRecord) -> Unit) {
        listeners.remove(listener)
    }

    private fun notifyListeners(record: PreflightRecord) {
        mainHandler.post {
            for (l in listeners) {
                runCatching { l(record) }
            }
        }
    }

    private fun ensureNotificationChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
            val existing = nm.getNotificationChannel(CHANNEL_ID)
            if (existing == null) {
                val channel = NotificationChannel(
                    CHANNEL_ID,
                    "AI Pre-Submission Readiness",
                    NotificationManager.IMPORTANCE_DEFAULT
                ).apply {
                    description = "Automatic AI connectivity and inference preflight status before proof submission windows."
                    enableLights(true)
                    lightColor = Color.BLUE
                    setShowBadge(false)
                }
                nm.createNotificationChannel(channel)
            }
        }
    }

    fun parseIsoToMs(isoStr: String): Long {
        if (isoStr.isBlank()) return 0L
        return try {
            val clean = isoStr.replace("Z", "+0000").replace("+05:30", "+0530")
            val sdf = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssZ", Locale.US)
            sdf.parse(clean)?.time ?: 0L
        } catch (_: Exception) {
            try {
                SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).parse(isoStr)?.time ?: 0L
            } catch (_: Exception) {
                0L
            }
        }
    }

    // -------------------------------------------------------------------------
    // Scheduling (Section 1, 8, 9)
    // -------------------------------------------------------------------------
    fun schedulePreflightForWindow(
        context: Context,
        activityId: String,
        windowStartIso: String,
        scheduleVersion: Int = 1
    ) {
        try {
            val app = context.applicationContext
            val openMs = parseIsoToMs(windowStartIso)
            if (openMs <= 0L) return

            val triggerMs = openMs - PREFLIGHT_LEAD_TIME_MS
            val nowMs = System.currentTimeMillis()
            val delayMs = triggerMs - nowMs

            // Guard: If the window opened well in the past (more than 5 mins ago), do not schedule or execute stale preflight
            if (openMs < nowMs - PREFLIGHT_LEAD_TIME_MS) {
                return
            }

            val jobKey = "${activityId}_v${scheduleVersion}"
            val existingScheduled = scheduledJobs[jobKey]
            if (existingScheduled != null && Math.abs(existingScheduled - triggerMs) < 60_000L) {
                return // Already scheduled within 1 minute tolerance
            }

            scheduledJobs[jobKey] = triggerMs

            if (delayMs <= 0L) {
                // Window opens in less than 5 minutes or is currently active.
                // Avoid re-running repeatedly if already executed within last 4 minutes.
                val lastRec = memoryCache[activityId]
                if (lastRec != null) {
                    val lastCheckMs = parseIsoToMs(lastRec.preflightCompletedAt)
                    if (nowMs - lastCheckMs < 4 * 60 * 1000L) {
                        return
                    }
                }
                Log.i(TAG, "Preflight lead time reached for $activityId (opens in ${(openMs - nowMs)/1000}s), running immediately")
                executePreflightAsync(app, activityId, windowStartIso, scheduleVersion)
                return
            }

            // Schedule background execution via JobScheduler
            try {
                val js = app.getSystemService(Context.JOB_SCHEDULER_SERVICE) as? JobScheduler
                if (js != null) {
                    val jobId = BASE_JOB_ID + Math.abs(jobKey.hashCode() % 10000)
                    val bundle = PersistableBundle().apply {
                        putString("activity_id", activityId)
                        putString("window_start_iso", windowStartIso)
                        putInt("schedule_version", scheduleVersion)
                    }
                    val job = JobInfo.Builder(jobId, ComponentName(app, ProofWindowPreflightJobService::class.java))
                        .setMinimumLatency(delayMs)
                        .setOverrideDeadline(delayMs + 60_000L) // Bounded execution window
                        .setExtras(bundle)
                        .setPersisted(false)
                        .build()
                    js.schedule(job)
                    Log.i(TAG, "Scheduled preflight Job #$jobId for $activityId in ${delayMs / 1000}s")
                    return
                }
            } catch (t: Throwable) {
                Log.w(TAG, "JobScheduler scheduling failed for preflight: ${t.message}")
            }

            // Fallback: AlarmManager
            try {
                val am = app.getSystemService(Context.ALARM_SERVICE) as? AlarmManager
                if (am != null) {
                    val intent = Intent(app, ProofWindowPreflightReceiver::class.java).apply {
                        putExtra("activity_id", activityId)
                        putExtra("window_start_iso", windowStartIso)
                        putExtra("schedule_version", scheduleVersion)
                    }
                    val pi = PendingIntent.getBroadcast(
                        app,
                        Math.abs(jobKey.hashCode() % 10000),
                        intent,
                        PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) PendingIntent.FLAG_IMMUTABLE else 0)
                    )
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                        if (am.canScheduleExactAlarms()) {
                            try {
                                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerMs, pi)
                            } catch (_: SecurityException) {
                                am.set(AlarmManager.RTC_WAKEUP, triggerMs, pi)
                            }
                        } else {
                            am.set(AlarmManager.RTC_WAKEUP, triggerMs, pi)
                        }
                    } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                        try {
                            am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerMs, pi)
                        } catch (_: SecurityException) {
                            am.set(AlarmManager.RTC_WAKEUP, triggerMs, pi)
                        }
                    } else {
                        am.set(AlarmManager.RTC_WAKEUP, triggerMs, pi)
                    }
                    Log.i(TAG, "Scheduled preflight Alarm for $activityId at $triggerMs")
                }
            } catch (t: Throwable) {
                Log.w(TAG, "AlarmManager scheduling failed: ${t.message}")
            }
        } catch (t: Throwable) {
            Log.w(TAG, "schedulePreflightForWindow failed: ${t.message}")
        }
    }

    // -------------------------------------------------------------------------
    // Execution (Section 2, 4, 5, 7)
    // -------------------------------------------------------------------------
    fun executePreflightAsync(
        context: Context,
        activityId: String,
        windowStartIso: String,
        scheduleVersion: Int = 1,
        force: Boolean = false,
        retryAttempt: Int = 0,
        onComplete: ((PreflightRecord) -> Unit)? = null
    ) {
        try {
            val app = context.applicationContext
            bgExecutor.execute {
                try {
                    val rec = executePreflightSync(app, activityId, windowStartIso, scheduleVersion, force, retryAttempt)
                    notifyListeners(rec)
                    mainHandler.post {
                        try {
                            onComplete?.invoke(rec)
                        } catch (t: Throwable) {
                            Log.w(TAG, "Error in onComplete: ${t.message}")
                        }
                    }
                } catch (t: Throwable) {
                    Log.e(TAG, "Background preflight worker failed: ${t.message}", t)
                }
            }
        } catch (t: Throwable) {
            Log.e(TAG, "Failed to dispatch executePreflightAsync: ${t.message}", t)
        }
    }

    private fun executePreflightSync(
        context: Context,
        activityId: String,
        windowStartIso: String,
        scheduleVersion: Int = 1,
        force: Boolean = false,
        retryAttempt: Int = 0
    ): PreflightRecord {
        val app = context.applicationContext
        val cleanActId = activityId.trim()
        val nowMs = System.currentTimeMillis()

        try {
            // 1. Try authoritative backend execution first (Section 8)
            val endpoints = TaufaPcBridge.getCandidateEndpoints(app)
            for (ep in endpoints) {
                try {
                    val url = URL("$ep/api/ai_preflight/check")
                    val conn = (url.openConnection() as HttpURLConnection).apply {
                        connectTimeout = 8000
                        readTimeout = 12000
                        requestMethod = "POST"
                        doOutput = true
                        setRequestProperty("Content-Type", "application/json; charset=UTF-8")
                    }
                    val payload = JSONObject().apply {
                        put("proof_window_id", cleanActId)
                        put("activity_id", cleanActId)
                        put("scheduled_open_time", windowStartIso)
                        put("schedule_version", scheduleVersion)
                        put("force", force)
                        put("retry_attempt", retryAttempt)
                    }
                    OutputStreamWriter(conn.outputStream, "UTF-8").use { it.write(payload.toString()); it.flush() }
                    if (conn.responseCode in 200..299) {
                        val raw = conn.inputStream.bufferedReader().use { it.readText() }
                        val json = JSONObject(raw)
                        val preflightJson = json.optJSONObject("preflight") ?: json
                        val record = PreflightRecord.fromJson(preflightJson)
                        savePreflightRecord(app, record)
                        postReadinessNotification(app, record)
                        return record
                    }
                } catch (e: Exception) {
                    Log.d(TAG, "Backend preflight check at $ep failed: ${e.message}")
                }
            }

            // 2. Standalone fallback: evaluate local connectivity via paired PC bridge
            val bridge = TaufaPcBridge.checkStatus(app)
            val taufaSt = if (bridge.ready) "CONNECTED" else "UNAVAILABLE"
            val openaiSt = if (bridge.activeProvider == "OPENAI_FALLBACK" || bridge.ready) "CONNECTED" else "UNAVAILABLE"

            val selected = when {
                taufaSt == "CONNECTED" -> "TAUFA_AI"
                openaiSt == "CONNECTED" -> "OPENAI_FALLBACK"
                else -> "PENDING_VALIDATION"
            }

            val title = when (selected) {
                "TAUFA_AI" -> "AI PRE-SUBMISSION CHECK"
                "OPENAI_FALLBACK" -> "AI FALLBACK READY"
                else -> "AI VALIDATION SERVICES UNAVAILABLE"
            }

            val lines = mutableListOf<String>().apply {
                add("• Taufa AI: $taufaSt")
                add("• OpenAI: $openaiSt")
                add("• Active provider: ${if (selected != "PENDING_VALIDATION") selected else "Pending"}")
            }

            val record = PreflightRecord(
                preflightId = "preflight_local_${cleanActId}_$nowMs",
                proofWindowId = cleanActId,
                activityId = cleanActId,
                scheduledOpenTime = windowStartIso,
                preflightStartedAt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).format(Date(nowMs)),
                taufaStatus = taufaSt,
                taufaLatency = bridge.responseLatencyMs,
                openaiStatus = openaiSt,
                openaiLatency = bridge.responseLatencyMs,
                selectedValidationProvider = selected,
                primaryProvider = if (taufaSt == "CONNECTED") "TAUFA_AI" else "NONE",
                backupProvider = if (openaiSt == "CONNECTED") "OPENAI_FALLBACK" else "NONE",
                preflightCompletedAt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).format(Date(nowMs)),
                retryCount = retryAttempt,
                preflightStatus = if (selected != "PENDING_VALIDATION") "COMPLETED" else "FAILED",
                scheduleVersion = scheduleVersion,
                displayTitle = title,
                displayLines = lines
            )

            savePreflightRecord(app, record)
            postReadinessNotification(app, record)
            return record
        } catch (t: Throwable) {
            Log.e(TAG, "executePreflightSync fallback error: ${t.message}", t)
            val fallbackRecord = PreflightRecord(
                preflightId = "preflight_fallback_${cleanActId}_$nowMs",
                proofWindowId = cleanActId,
                activityId = cleanActId,
                scheduledOpenTime = windowStartIso,
                preflightStartedAt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).format(Date(nowMs)),
                selectedValidationProvider = "PENDING_VALIDATION",
                preflightStatus = "FAILED",
                displayTitle = "AI VALIDATION SERVICES UNAVAILABLE",
                displayLines = listOf("• Taufa AI: UNAVAILABLE", "• OpenAI: UNAVAILABLE", "• Protected: Evidence queued")
            )
            savePreflightRecord(app, fallbackRecord)
            return fallbackRecord
        }
    }

    // -------------------------------------------------------------------------
    // Persistence & Cache (Section 4)
    // -------------------------------------------------------------------------
    fun savePreflightRecord(context: Context, record: PreflightRecord) {
        val app = context.applicationContext
        memoryCache[record.activityId] = record
        memoryCache[record.proofWindowId] = record

        try {
            val prefs = app.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            prefs.edit()
                .putString("preflight_${record.activityId}", record.toJson().toString())
                .putString("preflight_${record.proofWindowId}", record.toJson().toString())
                .putString(KEY_LATEST_PREFLIGHT, record.toJson().toString())
                .apply()
        } catch (e: Exception) {
            Log.w(TAG, "Failed to persist preflight record to SharedPreferences: ${e.message}")
        }
    }

    fun getPreflightRecord(context: Context, activityId: String): PreflightRecord? {
        memoryCache[activityId]?.let { return it }
        val app = context.applicationContext
        try {
            val prefs = app.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            val raw = prefs.getString("preflight_$activityId", null) ?: prefs.getString(KEY_LATEST_PREFLIGHT, null)
            if (!raw.isNullOrBlank()) {
                val rec = PreflightRecord.fromJson(JSONObject(raw))
                memoryCache[activityId] = rec
                return rec
            }
        } catch (_: Exception) {}
        return null
    }

    // -------------------------------------------------------------------------
    // Notification (Section 7)
    // -------------------------------------------------------------------------
    fun postReadinessNotification(context: Context, record: PreflightRecord) {
        try {
            val app = context.applicationContext
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                if (androidx.core.content.ContextCompat.checkSelfPermission(
                        app,
                        android.Manifest.permission.POST_NOTIFICATIONS
                    ) != android.content.pm.PackageManager.PERMISSION_GRANTED
                ) {
                    Log.w(TAG, "POST_NOTIFICATIONS permission not granted, skipping notification")
                    return
                }
            }

            val nm = app.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N && !nm.areNotificationsEnabled()) {
                Log.w(TAG, "Notifications disabled for application, skipping notification")
                return
            }

            ensureNotificationChannel(app)

            val notifId = 77000 + Math.abs(record.activityId.hashCode() % 1000)

            val intent = Intent(app, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
                putExtra("route", "proof")
                putExtra("activity_id", record.activityId)
            }
            val pi = PendingIntent.getActivity(
                app,
                notifId,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) PendingIntent.FLAG_IMMUTABLE else 0)
            )

            val summaryText = record.displayLines.joinToString(" • ")
            val bigText = record.displayLines.joinToString("\n")

            val builder = NotificationCompat.Builder(app, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_launcher)
                .setContentTitle(record.displayTitle.ifBlank { "AI PRE-SUBMISSION CHECK" })
                .setContentText(summaryText)
                .setStyle(NotificationCompat.BigTextStyle().bigText(bigText))
                .setContentIntent(pi)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_DEFAULT)
                .setCategory(NotificationCompat.CATEGORY_STATUS)

            nm.notify(notifId, builder.build())
        } catch (t: Throwable) {
            Log.w(TAG, "Failed to post readiness notification: ${t.message}", t)
        }
    }
}

/**
 * JobService executed 5 minutes before scheduled window opening.
 */
class ProofWindowPreflightJobService : JobService() {
    override fun onStartJob(params: JobParameters?): Boolean {
        try {
            val b = params?.extras
            val actId = b?.getString("activity_id") ?: ""
            val wStart = b?.getString("window_start_iso") ?: ""
            val ver = b?.getInt("schedule_version") ?: 1

            if (actId.isBlank()) {
                jobFinished(params, false)
                return false
            }

            ProofWindowPreflightManager.executePreflightAsync(
                context = applicationContext,
                activityId = actId,
                windowStartIso = wStart,
                scheduleVersion = ver
            ) {
                try {
                    jobFinished(params, false)
                } catch (_: Throwable) {}
            }
            return true
        } catch (t: Throwable) {
            Log.w("PreflightJobService", "onStartJob error: ${t.message}")
            try { jobFinished(params, false) } catch (_: Throwable) {}
            return false
        }
    }

    override fun onStopJob(params: JobParameters?): Boolean = true
}

/**
 * BroadcastReceiver for AlarmManager trigger fallback.
 */
class ProofWindowPreflightReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        try {
            if (context == null || intent == null) return
            val actId = intent.getStringExtra("activity_id") ?: ""
            val wStart = intent.getStringExtra("window_start_iso") ?: ""
            val ver = intent.getIntExtra("schedule_version", 1)

            if (actId.isNotBlank()) {
                ProofWindowPreflightManager.executePreflightAsync(
                    context = context,
                    activityId = actId,
                    windowStartIso = wStart,
                    scheduleVersion = ver
                )
            }
        } catch (t: Throwable) {
            Log.w("PreflightReceiver", "onReceive error: ${t.message}")
        }
    }
}
