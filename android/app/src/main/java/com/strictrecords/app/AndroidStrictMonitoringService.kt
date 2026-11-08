package com.strictrecords.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import androidx.core.app.NotificationCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.util.Collections
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Android Foreground Service for PC-Controlled Strict Session Companion Monitoring.
 *
 * Runs with foreground service type dataSync (Android 14+ compliant).
 * Performs lightweight UsageEvents monitoring and transmits periodic heartbeats
 * and immediate usage violation alerts to the primary Windows session controller.
 *
 * Strictly adheres to:
 * - NO camera or microphone access or permissions.
 * - NO continuous wake locks; sleeps naturally when the screen is locked.
 * - Screen lock/unlock lifecycle tracking without false alarms.
 * - Phase-aware permitted-use enforcement (FOCUS_ACTIVE vs FOCUS_BUFFER / PROOF_SUBMISSION_OPEN).
 */
class AndroidStrictMonitoringService : Service() {

    companion object {
        private const val TAG = "AndroidStrictMonitor"
        private const val NOTIFICATION_ID = 90210
        private const val CHANNEL_ID = "strict_session_monitoring_channel"

        const val ACTION_START_MONITORING = "com.strictrecords.app.ACTION_START_STRICT_MONITORING"
        const val ACTION_STOP_MONITORING = "com.strictrecords.app.ACTION_STOP_STRICT_MONITORING"
        const val ACTION_PAUSE_MONITORING = "com.strictrecords.app.ACTION_PAUSE_STRICT_MONITORING"
        const val ACTION_RESUME_MONITORING = "com.strictrecords.app.ACTION_RESUME_STRICT_MONITORING"
        const val ACTION_UPDATE_POLICY = "com.strictrecords.app.ACTION_UPDATE_STRICT_POLICY"
        const val ACTION_UPDATE_PHASE = "com.strictrecords.app.ACTION_UPDATE_STRICT_PHASE"
        const val ACTION_SYNC_USAGE = "com.strictrecords.app.ACTION_SYNC_STRICT_USAGE"
        const val ACTION_HEARTBEAT_TICK = "com.strictrecords.app.ACTION_MONITOR_HEARTBEAT_TICK"

        // State Machine Constants
        const val STATE_ANDROID_NOT_JOINED = "ANDROID_NOT_JOINED"
        const val STATE_ANDROID_NOT_REGISTERED = "ANDROID_NOT_REGISTERED"
        const val STATE_ANDROID_CONNECTED = "ANDROID_CONNECTED"
        const val STATE_ANDROID_START_PENDING = "ANDROID_START_PENDING"
        const val STATE_ANDROID_MONITORING_ACTIVE = "ANDROID_MONITORING_ACTIVE"
        const val STATE_ANDROID_PERMISSION_REQUIRED = "ANDROID_PERMISSION_REQUIRED"
        const val STATE_ANDROID_SCREEN_LOCKED = "ANDROID_SCREEN_LOCKED"
        const val STATE_ANDROID_LOCKED_SLEEPING = "ANDROID_LOCKED_SLEEPING"
        const val STATE_ANDROID_UNLOCKED = "ANDROID_UNLOCKED"
        const val STATE_ANDROID_APP_USAGE_DETECTED = "ANDROID_APP_USAGE_DETECTED"
        const val STATE_ANDROID_USAGE_WARNING = "ANDROID_USAGE_WARNING"
        const val STATE_ANDROID_CONNECTION_DEGRADED = "ANDROID_CONNECTION_DEGRADED"
        const val STATE_ANDROID_CONNECTION_LOST = "ANDROID_CONNECTION_LOST"
        const val STATE_ANDROID_RECONNECTING = "ANDROID_RECONNECTING"
        const val STATE_ANDROID_MONITORING_RESTORED = "ANDROID_MONITORING_RESTORED"
        const val STATE_ANDROID_SYNCING = "ANDROID_SYNCING"
        const val STATE_ANDROID_MONITORING_UNAVAILABLE = "ANDROID_MONITORING_UNAVAILABLE"
        const val STATE_ANDROID_SESSION_COMPLETED = "ANDROID_SESSION_COMPLETED"

        @Volatile
        var isServiceRunning: Boolean = false
            private set

        @Volatile
        var currentMonitoringState: String = STATE_ANDROID_NOT_JOINED
            private set

        @Volatile
        var currentSessionId: String = ""
            private set

        @Volatile
        var currentSessionPhase: String = "FOCUS_ACTIVE"
            private set

        @Volatile
        var isPhoneUsageAllowed: Boolean = false
            private set

        @Volatile
        var phaseExpiresAtMs: Long = 0L
            private set
    }

    private var executor: ScheduledExecutorService? = null
    private val isScreenLocked = AtomicBoolean(false)
    private var screenReceiver: BroadcastReceiver? = null

    private var sessionId: String = ""
    private var userId: String = ""
    private var deviceId: String = ""
    private var serverEndpoint: String = ""
    private var submissionWindowOpen: Boolean = false
    private var reportSequence: Int = 0
    private var lastReportTimeMs: Long = System.currentTimeMillis()
    private val pendingUnauthorizedEvents = Collections.synchronizedList(mutableListOf<JSONObject>())

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        registerScreenStateReceiver()
        isServiceRunning = true
        Log.i(TAG, "AndroidStrictMonitoringService created.")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action ?: ACTION_START_MONITORING

        if (action == ACTION_STOP_MONITORING) {
            Log.i(TAG, "Received ACTION_STOP_MONITORING.")
            currentMonitoringState = STATE_ANDROID_SESSION_COMPLETED
            currentSessionPhase = "SESSION_COMPLETED"
            isPhoneUsageAllowed = false
            stopMonitoring()
            return START_NOT_STICKY
        }
        if (action == ACTION_PAUSE_MONITORING) {
            Log.i(TAG, "Strict Session paused by Windows.")
            currentMonitoringState = "ANDROID_PAUSED"
            currentSessionPhase = "SESSION_PAUSED"
            updateOngoingNotification()
            return START_STICKY
        }
        if (action == ACTION_RESUME_MONITORING) {
            Log.i(TAG, "Strict Session resumed by Windows.")
            currentMonitoringState = STATE_ANDROID_MONITORING_ACTIVE
            currentSessionPhase = "FOCUS_ACTIVE"
            updateOngoingNotification()
            executor?.execute { send30SecondUsageReport() }
            return START_STICKY
        }
        if (action == ACTION_UPDATE_POLICY) {
            val pkgs = intent?.getStringExtra("allowed_packages")
            if (!pkgs.isNullOrBlank()) {
                StrictSessionUsageMonitor.updateAllowedPackages(pkgs)
            }
            return START_STICKY
        }
        if (action == ACTION_UPDATE_PHASE) {
            val newPhase = intent?.getStringExtra("session_phase") ?: "FOCUS_ACTIVE"
            val phoneUsage = intent?.getStringExtra("phone_usage") ?: "PHONE_USAGE_RESTRICTED"
            val allowedBool = intent?.getBooleanExtra("is_phone_usage_allowed", phoneUsage == "PHONE_USAGE_ALLOWED") ?: (phoneUsage == "PHONE_USAGE_ALLOWED")
            val expiresMs = intent?.getLongExtra("phase_expires_at_ms", 0L) ?: 0L
            val pkgs = intent?.getStringExtra("allowed_packages")

            currentSessionPhase = newPhase
            isPhoneUsageAllowed = allowedBool || (newPhase in setOf("FOCUS_BUFFER", "PROOF_SUBMISSION_OPEN"))
            phaseExpiresAtMs = expiresMs

            if (!pkgs.isNullOrBlank()) {
                StrictSessionUsageMonitor.updateAllowedPackages(pkgs)
            }

            Log.i(TAG, "Phase updated: $currentSessionPhase, phoneUsageAllowed: $isPhoneUsageAllowed, expires: $phaseExpiresAtMs")
            BackgroundMusicRestrictionManager.onRestrictionStateChanged(this, "strict_session_phase_$currentSessionPhase")
            updateOngoingNotification()

            executor?.execute {
                reconcileUsageEvents()
                send30SecondUsageReport()
            }
            return START_STICKY
        }
        if (action == ACTION_SYNC_USAGE) {
            executor?.execute {
                reconcileUsageEvents()
                send30SecondUsageReport()
            }
            return START_STICKY
        }

        // Retrieve session parameters
        val incomingSessionId = intent?.getStringExtra("session_id").orEmpty()
        val incomingUserId = intent?.getStringExtra("user_id").orEmpty()

        val db = StrictRecordsLocalDb.getInstance(this)
        val reg = db.getDeviceRegistration()
        deviceId = reg?.optString("device_id").orEmpty().ifBlank { "android_companion" }
        userId = incomingUserId.ifBlank { reg?.optString("account_id").orEmpty().ifBlank { "user_lok" } }
        serverEndpoint = reg?.optString("server_endpoint", "http://127.0.0.1:8765")?.trimEnd('/') ?: "http://127.0.0.1:8765"

        if (incomingSessionId.isNotBlank()) {
            sessionId = incomingSessionId
            currentSessionId = sessionId
        }

        // Initialize persistent event cursor to now
        StrictSessionUsageMonitor.setEventCursorMs(this, System.currentTimeMillis())
        StrictSessionUsageMonitor.clearRecentTransitions()
        currentSessionPhase = "FOCUS_ACTIVE"
        isPhoneUsageAllowed = false

        val notification = buildOngoingNotification("StrictRecords Strict Session Monitoring Active (FOCUS ACTIVE)")
        startForeground(NOTIFICATION_ID, notification)

        val hasUsage = StrictSessionUsageMonitor.hasUsagePermission(this)
        if (!hasUsage) {
            currentMonitoringState = STATE_ANDROID_PERMISSION_REQUIRED
            Log.w(TAG, "PACKAGE_USAGE_STATS permission not granted! Reporting PERMISSION_REQUIRED to Windows.")
            executor?.execute {
                transmitPermissionRevoked()
            }
        } else {
            currentMonitoringState = STATE_ANDROID_MONITORING_ACTIVE
            sendStartAcknowledgment()
        }

        startMonitoringLoop()
        BackgroundMusicRestrictionManager.onRestrictionStateChanged(this, "strict_session_start")
        return START_STICKY
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Strict Session Monitoring",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Ongoing notification while PC Strict Session mobile usage monitoring is active"
                setShowBadge(false)
            }
            val nm = getSystemService(NotificationManager::class.java)
            nm?.createNotificationChannel(channel)
        }
    }

    private fun buildOngoingNotification(contentText: String): Notification {
        val openIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pendingIntent = PendingIntent.getActivity(
            this, 0, openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Strict Session Active")
            .setContentText(contentText)
            .setSmallIcon(R.drawable.ic_launcher)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(pendingIntent)
            .build()
    }

    private fun updateOngoingNotification() {
        try {
            val policyText = if (isPhoneUsageAllowed) "Phone Allowed" else "Phone Restricted"
            val text = "Phase: $currentSessionPhase · $policyText"
            val notification = buildOngoingNotification(text)
            val nm = getSystemService(NotificationManager::class.java)
            nm?.notify(NOTIFICATION_ID, notification)
        } catch (_: Exception) {}
    }

    private fun registerScreenStateReceiver() {
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_USER_PRESENT)
        }

        screenReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                when (intent?.action) {
                    Intent.ACTION_SCREEN_OFF -> {
                        Log.i(TAG, "Screen OFF detected -> ANDROID_SCREEN_LOCKED (LOCKED_SLEEPING).")
                        isScreenLocked.set(true)
                        currentMonitoringState = STATE_ANDROID_SCREEN_LOCKED
                        executor?.execute { send30SecondUsageReport(forceScreenState = "LOCKED_SLEEPING") }
                    }
                    Intent.ACTION_SCREEN_ON -> {
                        Log.d(TAG, "Screen ON detected (Device may still be locked).")
                    }
                    Intent.ACTION_USER_PRESENT -> {
                        Log.i(TAG, "User unlocked device -> ANDROID_UNLOCKED. Reconciling usage events.")
                        isScreenLocked.set(false)
                        currentMonitoringState = STATE_ANDROID_UNLOCKED
                        executor?.execute {
                            reconcileUsageEvents()
                            currentMonitoringState = STATE_ANDROID_MONITORING_ACTIVE
                            send30SecondUsageReport(forceScreenState = "UNLOCKED")
                        }
                    }
                }
            }
        }
        registerReceiver(screenReceiver, filter)
    }

    private fun startMonitoringLoop() {
        executor?.shutdownNow()
        executor = Executors.newScheduledThreadPool(2)

        // 1. Lightweight 1.5-second watcher for immediate unauthorized app foreground transitions
        executor?.scheduleWithFixedDelay({
            try {
                if (!isScreenLocked.get()) {
                    reconcileUsageEvents()
                }
            } catch (e: Exception) {
                Log.e(TAG, "Immediate transition watcher error: ${e.message}")
            }
        }, 1, 1500, TimeUnit.MILLISECONDS)

        // 2. Authoritative 30-second usage reporting cadence
        executor?.scheduleWithFixedDelay({
            try {
                send30SecondUsageReport()
            } catch (e: Exception) {
                Log.e(TAG, "30-second usage report error: ${e.message}")
            }
        }, 5, 30, TimeUnit.SECONDS)
    }

    private fun send30SecondUsageReport(forceScreenState: String? = null) {
        try {
            val nowMs = System.currentTimeMillis()
            val intervalStartIso = StrictSessionUsageMonitor.formatIso(lastReportTimeMs)
            val intervalEndIso = StrictSessionUsageMonitor.formatIso(nowMs)
            val screenState = forceScreenState ?: if (isScreenLocked.get()) "LOCKED_SLEEPING" else "UNLOCKED"
            val hasPermission = StrictSessionUsageMonitor.hasUsagePermission(this)
            val monitorStatus = if (isScreenLocked.get()) "LOCKED_SLEEPING" else if (!hasPermission) "MONITORING_UNAVAILABLE" else "ACTIVE"
            val usageAccess = if (hasPermission) "ENABLED" else "REVOKED"

            val (fgPkg, lastTime) = if (!isScreenLocked.get() && hasPermission) {
                StrictSessionUsageMonitor.getCurrentForegroundPackage(this)
            } else {
                Pair(if (isScreenLocked.get()) "SCREEN_LOCKED" else "UNKNOWN", 0L)
            }

            val appLabel = if (fgPkg.isNotBlank() && fgPkg != "UNKNOWN" && fgPkg != "SCREEN_LOCKED") {
                StrictSessionUsageMonitor.getAppLabel(this, fgPkg)
            } else {
                fgPkg
            }
            val category = StrictSessionUsageMonitor.getAppCategory(this, fgPkg)
            val foregroundDurationSeconds = if (lastTime > 0L) ((nowMs - lastTime) / 1000L).coerceAtLeast(0L).toInt() else 0

            reportSequence++

            // Drain current pending unauthorized events
            val unauthEventsJson = JSONArray()
            synchronized(pendingUnauthorizedEvents) {
                for (ev in pendingUnauthorizedEvents) {
                    unauthEventsJson.put(ev)
                }
                pendingUnauthorizedEvents.clear()
            }

            val payload = JSONObject().apply {
                put("session_id", sessionId)
                put("device_id", deviceId)
                put("user_id", userId)
                put("report_sequence", reportSequence)
                put("timestamp", intervalEndIso)
                put("interval_start", intervalStartIso)
                put("interval_end", intervalEndIso)
                put("current_app", fgPkg)
                put("current_app_label", appLabel)
                put("current_category", category)
                put("foreground_duration_seconds", foregroundDurationSeconds)
                put("unauthorized_events", unauthEventsJson)
                put("screen_state", screenState)
                put("monitor_status", monitorStatus)
                put("usage_access", usageAccess)
                put("session_phase", currentSessionPhase)
                put("phone_usage", if (isPhoneUsageAllowed) "PHONE_USAGE_ALLOWED" else "PHONE_USAGE_RESTRICTED")
                put("phase_expires_at_ms", phaseExpiresAtMs)
                put("recent_transitions", StrictSessionUsageMonitor.getRecentTransitionsJson())
                put("submission_window_status", if (submissionWindowOpen || currentSessionPhase == "PROOF_SUBMISSION_OPEN") "OPEN" else "CLOSED")
            }

            val url = URL("$serverEndpoint/api/strict_session/android/usage_report")
            val resp = postJson(url, payload)
            if (resp != null) {
                val active = resp.optBoolean("session_active", true)
                val windowStatus = resp.optString("submission_window_status", "CLOSED")
                submissionWindowOpen = (windowStatus.uppercase() == "OPEN")

                val serverPhase = resp.optString("session_phase", "")
                val serverPhoneUsage = resp.optString("phone_usage", "")
                val serverExpiresAtMs = resp.optLong("phase_expires_at_ms", 0L)

                if (serverPhase.isNotBlank()) {
                    currentSessionPhase = serverPhase
                    isPhoneUsageAllowed = (serverPhoneUsage == "PHONE_USAGE_ALLOWED") || (serverPhase in setOf("FOCUS_BUFFER", "PROOF_SUBMISSION_OPEN"))
                    if (serverExpiresAtMs > 0L) {
                        phaseExpiresAtMs = serverExpiresAtMs
                    }
                    updateOngoingNotification()
                }

                if (!active) {
                    Log.i(TAG, "Windows reported Strict Session is inactive. Stopping monitoring service.")
                    Handler(Looper.getMainLooper()).post { stopMonitoring() }
                }
            }
            lastReportTimeMs = nowMs
        } catch (e: Exception) {
            Log.w(TAG, "Failed to send 30-second usage report: ${e.message}")
        }
    }

    private fun reconcileUsageEvents() {
        try {
            val now = System.currentTimeMillis()

            // Failsafe timer check: If allowed window expired without Windows renewal, revert to restricted
            if (isPhoneUsageAllowed && phaseExpiresAtMs > 0L && now >= phaseExpiresAtMs) {
                Log.w(TAG, "Phase failsafe expired at $phaseExpiresAtMs! Reverting to FOCUS_ACTIVE.")
                isPhoneUsageAllowed = false
                currentSessionPhase = "FOCUS_ACTIVE"
                updateOngoingNotification()
            }

            val hasPerm = StrictSessionUsageMonitor.hasUsagePermission(this)
            if (!hasPerm) {
                if (currentMonitoringState != STATE_ANDROID_PERMISSION_REQUIRED) {
                    currentMonitoringState = STATE_ANDROID_PERMISSION_REQUIRED
                    executor?.execute { transmitPermissionRevoked() }
                }
                return
            }

            val violations = StrictSessionUsageMonitor.queryUsageEvents(
                context = this,
                sessionId = sessionId,
                userId = userId,
                deviceId = deviceId,
                sessionPhase = currentSessionPhase,
                isPhoneUsageAllowed = isPhoneUsageAllowed,
                isSubmissionWindowOpen = (currentSessionPhase == "PROOF_SUBMISSION_OPEN" || submissionWindowOpen),
                nowMs = now
            )

            for (violation in violations) {
                currentMonitoringState = STATE_ANDROID_APP_USAGE_DETECTED
                transmitUsageViolation(violation)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error reconciling usage events: ${e.message}")
        }
    }

    private fun transmitPermissionRevoked() {
        try {
            val payload = JSONObject().apply {
                put("session_id", sessionId)
                put("user_id", userId)
                put("device_id", deviceId)
                put("status", "PERMISSION_REQUIRED")
                put("state", STATE_ANDROID_PERMISSION_REQUIRED)
                put("usage_access", "REVOKED")
                put("error", "Android Usage Access permission missing. Enable in Android Settings.")
                put("timestamp", StrictRecordsLocalDb.getInstance(this@AndroidStrictMonitoringService).nowIso())
            }
            val url = URL("$serverEndpoint/api/strict_session/android/ack_start")
            postJson(url, payload)
        } catch (_: Exception) {}
    }

    private fun sendStartAcknowledgment() {
        executor?.execute {
            try {
                val url = URL("$serverEndpoint/api/strict_session/android/ack_start")
                val payload = JSONObject().apply {
                    put("session_id", sessionId)
                    put("user_id", userId)
                    put("device_id", deviceId)
                    put("event_type", "ANDROID_SESSION_STARTED_ACK")
                    put("status", "ANDROID_SESSION_STARTED_ACK")
                    put("apk_version", BuildConfig.VERSION_NAME)
                    put("usage_access_granted", true)
                    put("monitoring_service_ready", true)
                    put("ack_timestamp", StrictRecordsLocalDb.getInstance(this@AndroidStrictMonitoringService).nowIso())
                    put("state", STATE_ANDROID_MONITORING_ACTIVE)
                    put("usage_access", "ENABLED")
                    put("session_phase", currentSessionPhase)
                    put("phone_usage", if (isPhoneUsageAllowed) "PHONE_USAGE_ALLOWED" else "PHONE_USAGE_RESTRICTED")
                    put("timestamp", StrictRecordsLocalDb.getInstance(this@AndroidStrictMonitoringService).nowIso())
                }
                postJson(url, payload)
                Log.i(TAG, "START_STRICT_SESSION acknowledged to $url (ANDROID_MONITOR_STARTED)")
            } catch (e: Exception) {
                Log.w(TAG, "Failed to send start acknowledgment: ${e.message}")
            }
        }
    }

    private fun transmitUsageViolation(violation: StrictSessionUsageMonitor.UsageViolation) {
        val payload = violation.toJsonObject()
        pendingUnauthorizedEvents.add(payload)

        // 1. Immediately post to Windows server
        try {
            val url = URL("$serverEndpoint/api/strict_session/android/usage_event")
            val resp = postJson(url, payload)
            if (resp != null && resp.optBoolean("success", false)) {
                Log.i(TAG, "Violation successfully received by Windows controller: ${violation.packageName}")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Direct violation post failed, saving to sync queue: ${e.message}")
        }

        // 2. Persist to local SQLite sync_events_queue for offline resilience
        try {
            val db = StrictRecordsLocalDb.getInstance(this)
            db.enqueueSyncEvent("ANDROID_APP_USAGE_DETECTED", payload, this)
        } catch (e: Exception) {
            Log.e(TAG, "Error saving violation to local db: ${e.message}")
        }

        // 3. Update state to warning
        currentMonitoringState = STATE_ANDROID_USAGE_WARNING
    }

    private fun postJson(url: URL, json: JSONObject, timeoutMs: Int = 5000): JSONObject? {
        var conn: HttpURLConnection? = null
        return try {
            conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.connectTimeout = timeoutMs
            conn.readTimeout = timeoutMs
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8")
            conn.setRequestProperty("X-Device-Id", deviceId)

            OutputStreamWriter(conn.outputStream, "UTF-8").use { it.write(json.toString()) }

            val code = conn.responseCode
            if (code in 200..299) {
                val text = conn.inputStream.bufferedReader().use { it.readText() }
                if (text.isNotBlank()) JSONObject(text) else JSONObject().put("success", true)
            } else {
                null
            }
        } catch (e: Exception) {
            null
        } finally {
            conn?.disconnect()
        }
    }

    private fun stopMonitoring() {
        currentMonitoringState = STATE_ANDROID_NOT_JOINED
        isServiceRunning = false
        executor?.shutdownNow()
        executor = null
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
        stopSelf()
        Log.i(TAG, "AndroidStrictMonitoringService stopped.")
    }

    override fun onDestroy() {
        screenReceiver?.let {
            try { unregisterReceiver(it) } catch (_: Exception) {}
            screenReceiver = null
        }
        executor?.shutdownNow()
        executor = null
        isServiceRunning = false
        currentMonitoringState = STATE_ANDROID_NOT_JOINED
        super.onDestroy()
        Log.i(TAG, "AndroidStrictMonitoringService destroyed.")
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
