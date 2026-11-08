package com.strictrecords.app

import android.app.KeyguardManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.graphics.Color
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Network
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.provider.Settings
import android.util.Log
import org.json.JSONObject
import org.json.JSONArray
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

class StrictEnforcementService : Service() {

    companion object {
        private const val TAG = "StrictEnforcement"
        private const val NOTIFICATION_ID = 9001
        private const val CHANNEL_ID = "strict_enforcement_shield"

        private val isRunning = AtomicBoolean(false)
        @Volatile internal var activeInstance: StrictEnforcementService? = null
            private set

        fun start(context: Context) {
            if (isRunning.get()) return
            try {
                val intent = Intent(context, StrictEnforcementService::class.java)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    try {
                        context.startService(intent)
                    } catch (_: IllegalStateException) {
                        context.startForegroundService(intent)
                    }
                } else {
                    context.startService(intent)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to start StrictEnforcementService: ${e.message}")
            }
        }

        fun isServiceRunning(): Boolean = isRunning.get()
    }

    private var executor: ScheduledExecutorService? = null
    private var monitoringTask: ScheduledFuture<*>? = null
    private var usageTask: ScheduledFuture<*>? = null
    private var syncTask: ScheduledFuture<*>? = null
    private var bluetoothSyncTask: ScheduledFuture<*>? = null
    private var bluetoothSyncCoordinator: BluetoothAutoSyncCoordinator? = null
    private var permissionWatchTask: ScheduledFuture<*>? = null
    private val accessibilityObserver = object : android.database.ContentObserver(android.os.Handler(android.os.Looper.getMainLooper())) {
        override fun onChange(selfChange: Boolean) {
            super.onChange(selfChange)
            Log.d(TAG, "Accessibility settings change detected via ContentObserver")
            val a11yStatus = AccessibilityGraceManager.checkAccessibilityState(this@StrictEnforcementService)
            val isA11yDisabled = !HardwareDeviceVerifier.isAccessibilityServiceEnabled(this@StrictEnforcementService, StrictAccessibilityService::class.java)
            if (isA11yDisabled || a11yStatus.isInterrupted) {
                if (android.provider.Settings.canDrawOverlays(this@StrictEnforcementService)) {
                    FloatingAccessibilityCountdownOverlay.showOrUpdate(this@StrictEnforcementService, a11yStatus.remainingGraceMs)
                }
            } else {
                FloatingAccessibilityCountdownOverlay.hide()
            }
        }
    }
    private var appOpsListener: android.app.AppOpsManager.OnOpChangedListener? = null
    private var powerManager: PowerManager? = null
    private lateinit var db: StrictRecordsLocalDb
    private val updateStreamRunning = AtomicBoolean(false)
    @Volatile private var updateStreamConnection: HttpURLConnection? = null
    @Volatile private var lastUpdateManifestCheckAt = 0L
    private var updateStreamThread: Thread? = null
    private val streamSignal = java.lang.Object()
    private val streamWakeVersion = AtomicLong()
    private var connectivityManager: ConnectivityManager? = null
    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            requestServerReconnect()
            PersistentUploadQueueManager.triggerQueueProcessingAsync(this@StrictEnforcementService)
            AiServiceHealthManager.refreshIfStaleAsync(this@StrictEnforcementService, "network_available")
        }
        override fun onLost(network: Network) {
            updateStreamConnection?.disconnect()
            requestServerReconnect()
        }
    }

    internal fun requestServerReconnect() {
        streamWakeVersion.incrementAndGet()
        synchronized(streamSignal) { streamSignal.notifyAll() }
    }

    private fun pauseApkUpdateStream() {
        updateStreamConnection?.disconnect()
        requestServerReconnect()
    }

    private fun resumeApkUpdateStream() {
        requestServerReconnect()
    }

    private fun waitForStreamRetry(delayMs: Long, version: Long) {
        synchronized(streamSignal) {
            if (updateStreamRunning.get() && streamWakeVersion.get() == version) {
                if (delayMs == Long.MAX_VALUE) streamSignal.wait()
                else streamSignal.wait(delayMs)
            }
        }
    }

    private var lastTriggerTime: Long = 0L
    private var lastTriggerPkg: String? = null
    private var currentAppLocks: JSONObject? = null
    private var lastMonitoringStatus = ""
    private var lastScoreCached = -1
    private var lastScoreCacheTime = 0L
    private var lastLocksCached: JSONObject? = null
    private var lastLocksCacheTime = 0L
        private var lastHeartbeatTime = 0L
    private var commandPollTask: ScheduledFuture<*>? = null

    private val prepareSessionReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action == "com.strictrecords.app.ACTION_PREPARE_STRICT_SESSION") {
                val prepId = intent.getStringExtra("preparation_id").orEmpty()
                Log.i(TAG, "Dynamic receiver got ACTION_PREPARE_STRICT_SESSION (prepId=$prepId)")
                val payload = JSONObject().apply {
                    if (prepId.isNotBlank()) put("preparation_id", prepId)
                }
                Thread({
                    runCatching { handlePrepareStrictSessionCheck(payload) }
                }, "StrictSession-DynamicPrepareAck").start()
            }
        }
    }

    private val monitoringPreferencesListener = SharedPreferences.OnSharedPreferenceChangeListener { _, _ ->
        clearTargetOverlay()
    }

    private val screenStateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            when (intent?.action) {
                Intent.ACTION_SCREEN_OFF -> {
                    StrictRecordsPowerManager.onScreenOff(this@StrictEnforcementService)
                    stopInteractiveMonitoring()
                    clearTargetOverlay()
                    // Keep SSE stream connected on network so PREPARE_STRICT_SESSION_CHECK & START_STRICT_SESSION arrive while phone screen is off
                    FloatingAccessibilityCountdownOverlay.setScreenTemporarilyHidden(true)
                    AccessibilityGraceManager.checkAccessibilityState(this@StrictEnforcementService)
                    BackgroundMusicRestrictionManager.onRestrictionStateChanged(this@StrictEnforcementService, "screen_off")
                    PersistentUploadQueueManager.triggerQueueProcessingAsync(this@StrictEnforcementService)
                    Log.d(TAG, "Screen turned OFF -> pausing enforcement and update stream to save battery")
                }
                Intent.ACTION_SCREEN_ON -> {
                    StrictRecordsPowerManager.onScreenOn(this@StrictEnforcementService)
                    AccessibilityGraceManager.checkAccessibilityState(this@StrictEnforcementService)
                    // Screen turned on but may still be locked with PIN/fingerprint
                    if (false && !isDeviceUnlockedAndInteractive()) {
                        stopInteractiveMonitoring()
                        clearTargetOverlay()
                        // Keep SSE stream connected on network so PREPARE_STRICT_SESSION_CHECK & START_STRICT_SESSION arrive while phone screen is off
                    } else {
                        startUsageReconciliation()
                        resumeTargetMonitoring()
                        startBackgroundSync()
                        resumeApkUpdateStream()
                    }
                }
                Intent.ACTION_USER_PRESENT -> {
                    StrictRecordsPowerManager.onDeviceUnlocked(this@StrictEnforcementService)
                    FloatingAccessibilityCountdownOverlay.setScreenTemporarilyHidden(false)
                    val a11yStatus = AccessibilityGraceManager.checkAccessibilityState(this@StrictEnforcementService)
                    val isA11yDisabled = !HardwareDeviceVerifier.isAccessibilityServiceEnabled(this@StrictEnforcementService, StrictAccessibilityService::class.java)
                    if (isA11yDisabled || a11yStatus.isInterrupted) {
                        if (android.provider.Settings.canDrawOverlays(this@StrictEnforcementService)) {
                            FloatingAccessibilityCountdownOverlay.showOrUpdate(this@StrictEnforcementService, a11yStatus.remainingGraceMs)
                        }
                    } else {
                        FloatingAccessibilityCountdownOverlay.hide()
                    }
                    ScheduleDeadlineManager.handleDeviceUnlock(this@StrictEnforcementService, db)
                    MorningWakeUpManager.handleMorningUnlock(this@StrictEnforcementService, db)
                    startUsageReconciliation()
                    resumeTargetMonitoring()
                    startBackgroundSync()
                    resumeApkUpdateStream()
                    BackgroundMusicRestrictionManager.onRestrictionStateChanged(this@StrictEnforcementService, "user_unlocked")
                    PersistentUploadQueueManager.triggerQueueProcessingAsync(this@StrictEnforcementService)
                    AiServiceHealthManager.refreshIfStaleAsync(this@StrictEnforcementService, "user_unlocked")
                    Log.d(TAG, "Device unlocked -> enforcement active for app usage")
                }
            }
        }
    }

    private val appLocksReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            var locksStr = intent?.getStringExtra("APP_LOCKS")
            val b64 = intent?.getStringExtra("APP_LOCKS_B64")
            if (!b64.isNullOrEmpty()) {
                try {
                    val decoded = String(android.util.Base64.decode(b64, android.util.Base64.DEFAULT), Charsets.UTF_8)
                    locksStr = decoded
                } catch (_: Exception) {}
            }
            if (!locksStr.isNullOrEmpty()) {
                try {
                    val locksObj = JSONObject(locksStr)
                    db.setConfigValue("remote_app_locks", locksObj.toString())
                    currentAppLocks = locksObj
                    lastLocksCached = locksObj
                    lastLocksCacheTime = System.currentTimeMillis()
                    clearTargetOverlay()
                    Log.i(TAG, "Updated remote app locks via broadcast: $locksStr")
                    BackgroundMusicRestrictionManager.onRestrictionStateChanged(this@StrictEnforcementService, "broadcast_app_locks")
                } catch (e: Exception) {
                    Log.e(TAG, "Error parsing app locks broadcast: ${e.message}")
                }
            }
        }
    }

    private fun isDeviceUnlockedAndInteractive(): Boolean {
        val pm = powerManager ?: (getSystemService(Context.POWER_SERVICE) as? PowerManager)
        if (pm?.isInteractive != true) return false
        val km = getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager
        if (km?.isKeyguardLocked == true) return false
        return true
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startRoutineForeground()
        isRunning.set(true)
        StrictWidgetUpdates.request(applicationContext,"monitoring")
        activeInstance = this
        db = StrictRecordsLocalDb.getInstance(this)
        AiServiceHealthManager.initializeAndSchedulePeriodicCheck(this)
        PersistentUploadQueueManager.triggerQueueProcessingAsync(this)
        PointsCloudSync.schedule(this)
        PointsCloudSync.request(this)
        ScheduleDeadlineManager.schedule10AmAlarm(this, db)
        bluetoothSyncCoordinator = BluetoothAutoSyncCoordinator(this, db)
        getSharedPreferences(TargetAppMonitoringPolicy.PREFERENCES, Context.MODE_PRIVATE).registerOnSharedPreferenceChangeListener(monitoringPreferencesListener)
        powerManager = getSystemService(Context.POWER_SERVICE) as? PowerManager

        try {
            val filter = IntentFilter("com.strictrecords.app.ACTION_UPDATE_APP_LOCKS")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                registerReceiver(appLocksReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
            } else {
                registerReceiver(appLocksReceiver, filter)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to register appLocksReceiver: ${e.message}")
        }

        try {
            val screenFilter = IntentFilter().apply {
                addAction(Intent.ACTION_SCREEN_OFF)
                addAction(Intent.ACTION_SCREEN_ON)
                addAction(Intent.ACTION_USER_PRESENT)
            }
            registerReceiver(screenStateReceiver, screenFilter)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to register screenStateReceiver: ${e.message}")
        }

        try {
            contentResolver.registerContentObserver(
                android.provider.Settings.Secure.getUriFor(android.provider.Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES),
                false,
                accessibilityObserver
            )
        } catch (e: Exception) {
            Log.w(TAG, "Failed to register accessibility ContentObserver: ${e.message}")
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            try {
                val appOps = getSystemService(Context.APP_OPS_SERVICE) as? android.app.AppOpsManager
                val listener = android.app.AppOpsManager.OnOpChangedListener { op, pkg ->
                    if (pkg == packageName && op == android.app.AppOpsManager.OPSTR_GET_USAGE_STATS) {
                        Log.d(TAG, "Usage access op changed for $pkg")
                        StrictWidgetUpdates.request(applicationContext,"monitoring")
                        AccessibilityGraceManager.checkAccessibilityState(this@StrictEnforcementService)
                    }
                }
                appOps?.startWatchingMode(android.app.AppOpsManager.OPSTR_GET_USAGE_STATS, packageName, listener)
                appOpsListener = listener
            } catch (e: Exception) {
                Log.w(TAG, "Failed to register AppOps listener: ${e.message}")
            }
        }

        // Schedule alarms once at startup (never in a 15-second loop)
        try {
            WakeUpAlarmReceiver.scheduleHourlyWindows(this)
        } catch (_: Exception) {}

        startEnforcementLoop()
        BankingBreakController.restore(this)
        connectivityManager = getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        runCatching { connectivityManager?.registerDefaultNetworkCallback(networkCallback) }
        try {
            val prepFilter = IntentFilter("com.strictrecords.app.ACTION_PREPARE_STRICT_SESSION")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                registerReceiver(prepareSessionReceiver, prepFilter, Context.RECEIVER_EXPORTED)
            } else {
                registerReceiver(prepareSessionReceiver, prepFilter)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to register prepareSessionReceiver: ${e.message}")
        }

        startCommandPolling()
        startApkUpdateEventListener()
        Log.i(TAG, "StrictEnforcementService started successfully.")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        createNotificationChannel()
        startRoutineForeground()
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startRoutineForeground() {
        val localDb = StrictRecordsLocalDb.getInstance(this)
        val automatic = localDb.getConfigValue("bluetooth_auto_enabled", "false") == "true" &&
            (Build.VERSION.SDK_INT < 31 || checkSelfPermission(android.Manifest.permission.BLUETOOTH_CONNECT) == android.content.pm.PackageManager.PERMISSION_GRANTED)
        if (Build.VERSION.SDK_INT >= 34) {
            val types = android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE or
                (if (automatic) android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE else 0)
            startForeground(NOTIFICATION_ID, buildForegroundNotification(), types)
        } else if (Build.VERSION.SDK_INT >= 29 && automatic) {
            startForeground(NOTIFICATION_ID, buildForegroundNotification(), android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else startForeground(NOTIFICATION_ID, buildForegroundNotification())
    }

    internal fun refreshAutomaticBluetoothSync() {
        startRoutineForeground()
        if (db.getConfigValue("bluetooth_auto_enabled", "false") != "true") BluetoothTransferClient.cancelAutomatic()
        bluetoothSyncCoordinator?.retryNow()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
            val chan = NotificationChannel(
                CHANNEL_ID,
                "StrictRoutines Protection Shield",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Enforcing daily discipline rules, focus sessions, and entertainment restrictions"
                enableLights(false)
                enableVibration(false)
                setShowBadge(false)
            }
            nm.createNotificationChannel(chan)
        }
    }

    private fun buildForegroundNotification(statusText: String = TargetAppMonitoringPolicy.monitoringStatus(this)): Notification {
        val tapIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val contentPi = PendingIntent.getActivity(
            this,
            0,
            tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }

        return builder
            .setContentTitle("StrictRoutines • Routine service")
            .setContentText(statusText)
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentIntent(contentPi)
            .setOngoing(true)
            .build()
    }

    private fun startEnforcementLoop() {
        executor?.shutdownNow()
        executor = Executors.newScheduledThreadPool(4)
        monitoringTask = null
        usageTask = null
        syncTask = null
        permissionWatchTask?.cancel(false)
        permissionWatchTask = executor?.scheduleWithFixedDelay({
            try {
                val a11yStatus = AccessibilityGraceManager.checkAccessibilityState(this)
                val isA11yDisabled = !HardwareDeviceVerifier.isAccessibilityServiceEnabled(this, StrictAccessibilityService::class.java)
                if (isA11yDisabled || a11yStatus.isInterrupted) {
                    if (android.provider.Settings.canDrawOverlays(this)) {
                        FloatingAccessibilityCountdownOverlay.showOrUpdate(this, a11yStatus.remainingGraceMs)
                    }
                } else {
                    FloatingAccessibilityCountdownOverlay.hide()
                }
                val status = TargetAppMonitoringPolicy.monitoringStatus(this)
                if (status != lastMonitoringStatus) {
                    updateMonitoringNotification()
                }
            } catch (e: Exception) {
                Log.w(TAG, "Permission watch tick failed: ${e.message}")
            }
        }, 1, 3, TimeUnit.SECONDS)
        resumeTargetMonitoring()
        startUsageReconciliation()
        startBackgroundSync()
        startCommandPolling()
        // Independent of screen lock, Wi-Fi and the interactive monitoring loop.
        bluetoothSyncTask?.cancel(false)
        bluetoothSyncTask = executor?.scheduleWithFixedDelay({
            try {
                bluetoothSyncCoordinator?.tick()
                YouTubeRewardManager.tick(applicationContext)
                GymPolicyClient.refresh(db)
            }
            catch (_: Exception) { Log.w(TAG, "Automatic Bluetooth sync will retry") }
        }, 5, 15, TimeUnit.SECONDS)
    }

    @Synchronized private fun startBackgroundSync() {
        if (!isRunning.get() || !isDeviceUnlockedAndInteractive()) return
        val scheduler = executor ?: return
        if (scheduler.isShutdown || syncTask?.let { !it.isCancelled && !it.isDone } == true) return
        syncTask = scheduler.scheduleWithFixedDelay({
            try {
                if (!StrictRecordsPowerManager.isBackgroundPollingAllowed(this@StrictEnforcementService)) return@scheduleWithFixedDelay
                if (isDeviceUnlockedAndInteractive() && db.isDeviceRegistered() && HardwareDeviceVerifier.isNetworkOrWifiAvailable(this)) {
                    db.flushSyncEventsQueue(this)
                    val now = System.currentTimeMillis()
                    if (now - lastHeartbeatTime >= TimeUnit.MINUTES.toMillis(5)) {
                        lastHeartbeatTime = now
                        db.sendHeartbeat(this)
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "Background sync tick warning: ${e.message}")
            }
        }, 15, 60, TimeUnit.SECONDS)
    }

    @Synchronized private fun stopInteractiveMonitoring() {
        monitoringTask?.cancel(false)
        monitoringTask = null
        usageTask?.cancel(false)
        usageTask = null
        syncTask?.cancel(false)
        syncTask = null
        UsageAccessMonitor.onScreenUnavailable()
    }

    @Synchronized private fun startUsageReconciliation() {
        if (!isRunning.get() || !isDeviceUnlockedAndInteractive()) return
        val scheduler = executor ?: return
        if (scheduler.isShutdown || usageTask?.let { !it.isCancelled && !it.isDone } == true) return
        usageTask = scheduler.scheduleWithFixedDelay({
            if (isDeviceUnlockedAndInteractive()) UsageAccessMonitor.reconcile(this)
            else UsageAccessMonitor.onScreenUnavailable()
        }, 0, UsageAccessMonitor.INTERVAL_MS, TimeUnit.MILLISECONDS)
    }

    @Synchronized internal fun suspendTargetMonitoring() {
        monitoringTask?.cancel(false)
        monitoringTask = null
        clearTargetOverlay()
        updateMonitoringNotification()
    }

    @Synchronized internal fun resumeTargetMonitoring() {
        if (!isRunning.get() || FinancialSafePause.isPauseActive(this) || !isDeviceUnlockedAndInteractive()) {
            suspendTargetMonitoring()
            return
        }
        val scheduler = executor ?: return
        if (scheduler.isShutdown || monitoringTask?.let { !it.isCancelled && !it.isDone } == true) return
        // Revalidate current authoritative eligibility after the break rather than
        // reuse a pre-bank foreground, score or lock cache.
        lastTriggerPkg = null
        lastTriggerTime = 0L
        lastScoreCached = -1
        lastScoreCacheTime = 0L
        lastLocksCached = null
        lastLocksCacheTime = 0L
        monitoringTask = scheduler.scheduleWithFixedDelay({
            try {
                enforceDisciplineTick()
            } catch (e: Exception) {
                Log.e(TAG, "Error in enforcement loop tick: ${e.message}")
            }
        }, 1000, UsageAccessMonitor.INTERVAL_MS, TimeUnit.MILLISECONDS)
        updateMonitoringNotification()
        Log.i("StrictRestriction", "platform=Android banking_break=monitoring_resumed")
    }

    private fun updateMonitoringNotification() {
        val status = TargetAppMonitoringPolicy.monitoringStatus(this)
        lastMonitoringStatus = status
        (getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager)
            ?.notify(NOTIFICATION_ID, buildForegroundNotification(status))
    }

    /** Listens for APK releases published by the Windows app while the phone is on the same Wi-Fi. */
    private fun startApkUpdateEventListener() {
        if (!updateStreamRunning.compareAndSet(false, true)) return

        updateStreamThread = Thread({
            var failures = 0
            var lastNetworkVersion = streamWakeVersion.get()
            while (updateStreamRunning.get() && !Thread.currentThread().isInterrupted) {
                val networkVersion = streamWakeVersion.get()
                if (lastNetworkVersion != networkVersion) { failures = 0; lastNetworkVersion = networkVersion }
                if (false && !isDeviceUnlockedAndInteractive()) {
                    try { waitForStreamRetry(Long.MAX_VALUE, networkVersion) } catch (_: InterruptedException) { break }
                    continue
                }
                if (!isConnectedToNetwork()) {
                    try { waitForStreamRetry(60_000L, networkVersion) } catch (_: InterruptedException) { break }
                    continue
                }

                var connection: HttpURLConnection? = null
                try {
                    val registration = db.getDeviceRegistration()
                    val deviceId = registration?.optString("device_id").orEmpty()
                    val deviceToken = registration?.optString("device_token").orEmpty()
                    if (deviceId.isBlank() || deviceToken.isBlank()) {
                        waitForStreamRetry(60_000L, networkVersion)
                        continue
                    }
                    val manager = AppUpdateManager.getInstance(this)
                    val endpoint = manager.getBaseServerUrl().trimEnd('/') + "/api/events/stream"
                    val streamConnection = (URL(endpoint).openConnection() as HttpURLConnection).apply {
                        requestMethod = "GET"
                        connectTimeout = 8000
                        readTimeout = 25000 // The server sends an SSE heartbeat every ten seconds.
                        setRequestProperty("Accept", "text/event-stream")
                        setRequestProperty("X-Device-Id", deviceId)
                        setRequestProperty("X-Device-Token", deviceToken)
                    }
                    connection = streamConnection
                    updateStreamConnection = streamConnection
                    if (streamConnection.responseCode !in 200..299) {
                        throw IllegalStateException("Update stream returned HTTP ${streamConnection.responseCode}")
                    }

                    maybeCheckForPublishedUpdate(manager)
                    reconcileOpenHourlyWindow(manager)
                    pollAndExecuteAdminCommands(manager, deviceId, deviceToken)
                    runCatching { handlePrepareStrictSessionCheck(JSONObject()) }
                    streamConnection.inputStream.bufferedReader().use { reader ->
                        var eventType = ""
                        val eventData = StringBuilder()
                        while (updateStreamRunning.get() && !Thread.currentThread().isInterrupted) {
                            if (false && !isDeviceUnlockedAndInteractive()) break
                            val line = reader.readLine() ?: break
                            failures = 0 // A live stream (including heartbeats) restores normal reconnect delay.
                            when {
                                line.startsWith("event:") -> eventType = line.substringAfter(':').trim()
                                line.startsWith("data:") -> {
                                    if (eventData.isNotEmpty()) eventData.append('\n')
                                    eventData.append(line.substringAfter(':').trimStart())
                                }
                                line.isEmpty() -> {
                                    if (eventType == "APK_UPDATE_AVAILABLE" && isProductionAndroidRelease(eventData.toString())) {
                                        manager.checkForUpdates(forceUserInitiated = true)
                                    } else if (eventType in setOf("HOURLY_WINDOW_OPEN", "HOURLY_REMINDER", "HOURLY_WINDOW_CLOSED")) {
                                        postHourlyWindowNotification(eventType, eventData.toString())
                                    } else if (eventType in setOf("DAILY_ROUTINE_ACTIVE", "ROUTINE_SCHEDULE_SYNC", "STRICTBOT_STATE_CHANGED")) {
                                        runCatching {
                                            val event = JSONObject(eventData.toString())
                                            val gate = event.optJSONObject("daily_routine_gate") ?: if (eventType == "DAILY_ROUTINE_ACTIVE") event else null
                                            gate?.let { db.applyAuthoritativeRoutineGate(it) }
                                            db.syncRoutineScheduleWithServer(db.nowDate())
                                        }
                                    } else if (eventType == "START_STRICT_SESSION") {
                                        runCatching {
                                            val payload = JSONObject(eventData.toString())
                                            executeSessionCommand("START_STRICT_SESSION", payload)
                                        }
                                    } else if (eventType in setOf("STOP_STRICT_SESSION", "END_STRICT_SESSION", "SESSION_FINALIZED", "SESSION_ABORTED")) {
                                        runCatching {
                                            val payload = try { JSONObject(eventData.toString()) } catch (_: Exception) { JSONObject() }
                                            executeSessionCommand("STOP_STRICT_SESSION", payload)
                                        }
                                    } else if (eventType == "PAUSE_STRICT_SESSION") {
                                        runCatching {
                                            executeSessionCommand("PAUSE_STRICT_SESSION", JSONObject())
                                        }
                                    } else if (eventType == "RESUME_STRICT_SESSION") {
                                        runCatching {
                                            executeSessionCommand("RESUME_STRICT_SESSION", JSONObject())
                                        }
                                    } else if (eventType == "UPDATE_SESSION_PHASE") {
                                        runCatching {
                                            executeSessionCommand("UPDATE_SESSION_PHASE", JSONObject(eventData.toString()))
                                        }
                                    } else if (eventType == "UPDATE_ALLOWED_APPS_POLICY") {
                                        runCatching {
                                            executeSessionCommand("UPDATE_ALLOWED_APPS_POLICY", JSONObject(eventData.toString()))
                                        }
                                    } else if (eventType == "PREPARE_STRICT_SESSION_CHECK") {
                                        runCatching {
                                            val payload = try { JSONObject(eventData.toString()) } catch (_: Exception) { JSONObject() }
                                            executeSessionCommand("PREPARE_STRICT_SESSION_CHECK", payload)
                                        }
                                    } else if (eventType == "REQUEST_USAGE_SYNC") {
                                        runCatching {
                                            executeSessionCommand("REQUEST_USAGE_SYNC", JSONObject())
                                        }
                                    }
                                    eventType = ""
                                    eventData.setLength(0)
                                }
                            }
                        }
                    }
                } catch (e: Exception) {
                    if (updateStreamRunning.get()) Log.d(TAG, "APK update stream reconnecting: ${e.message}")
                } finally {
                    connection?.disconnect()
                    if (updateStreamConnection === connection) updateStreamConnection = null
                }

                if (updateStreamRunning.get()) {
                    failures = (failures + 1).coerceAtMost(5)
                    val fastRetryMs = (1500L * failures).coerceIn(1500L, 8000L)
                    try { waitForStreamRetry(fastRetryMs, networkVersion) } catch (_: InterruptedException) { break }
                }
            }
        }, "StrictRoutines-ApkUpdate-Stream").apply {
            isDaemon = true
            start()
        }
    }

    private fun maybeCheckForPublishedUpdate(manager: AppUpdateManager) {
        val now = System.currentTimeMillis()
        if (now - lastUpdateManifestCheckAt < TimeUnit.MINUTES.toMillis(15)) return
        lastUpdateManifestCheckAt = now
        manager.checkForUpdates()
    }

    private fun isProductionAndroidRelease(payload: String): Boolean {
        return try {
            val event = JSONObject(payload)
            event.optString("platform", "ANDROID").equals("ANDROID", ignoreCase = true) &&
                event.optString("channel", "PRODUCTION").equals("PRODUCTION", ignoreCase = true)
        } catch (_: Exception) {
            false
        }
    }

    private fun reconcileOpenHourlyWindow(manager: AppUpdateManager) {
        var connection: HttpURLConnection? = null
        try {
            val endpoint = manager.getBaseServerUrl().trimEnd('/') + "/api/hourly/status"
            connection = (URL(endpoint).openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 5000
                readTimeout = 5000
                db.getDeviceRegistration()?.let { registration ->
                    setRequestProperty("X-Device-Id", registration.optString("device_id"))
                    setRequestProperty("X-Device-Token", registration.optString("device_token"))
                }
            }
            if (connection.responseCode !in 200..299) return
            val payload = connection.inputStream.bufferedReader().use { it.readText() }
            val status = JSONObject(payload)
            status.optJSONObject("daily_routine_gate")?.let { db.applyAuthoritativeRoutineGate(it) }
            val activity = status.optJSONObject("hourly_activity") ?: status.optJSONObject("open_activity") ?: return
            if (!status.optBoolean("is_window_open", false) || activity.optString("status") != "OPEN") return
            val activityId = activity.optString("activity_id")
            if (activityId.isBlank()) return
            val details = try { JSONObject(activity.optString("details_json", "{}")) } catch (_: Exception) { JSONObject() }
            val windowEnd = status.optString("window_end", activity.optString("window_end"))
            postHourlyWindowNotification(
                "HOURLY_WINDOW_OPEN",
                JSONObject().apply {
                    put("event_id", "$activityId-OPENED")
                    put("activity_id", activityId)
                    put("event_type", "HOURLY_WINDOW_OPEN")
                    put("slot", status.optString("activity_slot", details.optString("hour_slot", activity.optString("title"))))
                    put("title", activity.optString("title", "Hourly checkpoint"))
                    put("window_start", status.optString("window_start", activity.optString("window_start")))
                    put("window_end", windowEnd)
                    put("attendance_status", "OPEN")
                    put("validation_status", "NOT_SUBMITTED")
                }.toString()
            )
        } catch (e: Exception) {
            Log.d(TAG, "Could not reconcile active hourly window: ${e.message}")
        } finally {
            connection?.disconnect()
        }
    }

    private fun postHourlyWindowNotification(eventType: String, rawPayload: String) {
        try {
            val event = JSONObject(rawPayload)
            event.optJSONObject("daily_routine_gate")?.let { db.applyAuthoritativeRoutineGate(it) }
            val gate = db.getDailyRoutineGate(db.nowDate())
            val activationMs = db.parseIsoToMs(gate.optString("activated_at"))
            val eventStartMs = db.parseIsoToMs(event.optString("window_start"))
            if (!gate.optBoolean("active", false) || activationMs == Long.MIN_VALUE || eventStartMs == Long.MIN_VALUE || eventStartMs < activationMs) return
            val eventId = event.optString("event_id")
            val activityId = event.optString("activity_id")
            if (eventId.isBlank() || activityId.isBlank() || db.isNotificationSent(eventId)) return
            val endMs = db.parseIsoToMs(event.optString("window_end"))
            val suffix = eventId.substringAfterLast('-')
            if (!HourlyWindowRules.notificationEligible(suffix, db.authoritativeNowMs(), eventStartMs, endMs)) return

            // A queued server event can arrive after a Bath reschedule. Reconcile
            // the saved schedule before posting; an obsolete event must never
            // demand hourly proof during the newly approved activity interval.
            val currentActivity = db.getDailyActivities(db.nowDate()).firstOrNull { it.activityId == activityId }
            if (currentActivity != null) {
                val currentDetails = runCatching { JSONObject(currentActivity.detailsJson) }.getOrDefault(JSONObject())
                if (currentActivity.status in setOf("EXCUSED", "CANCELLED", "SKIPPED") ||
                    currentDetails.optBoolean("suppressed_by_bath", false)) return
                if (!AlarmScheduleRules.matchesCurrentWindow(eventStartMs, endMs,
                        db.parseIsoToMs(currentActivity.windowStart), db.parseIsoToMs(currentActivity.windowEnd))) return
            }

            val isClosed = eventType == "HOURLY_WINDOW_CLOSED"
            val isReminder = eventType == "HOURLY_REMINDER"
            val slot = event.optString("slot", event.optString("title", "Hourly checkpoint"))
            val deadline = event.optString("window_end").let { value ->
                if (value.length >= 16) value.substring(11, 16) else value
            }
            val attendance = event.optString("attendance_status", if (isClosed) "ABSENT" else "OPEN")
            val validation = event.optString("validation_status", "NOT_SUBMITTED")
            val title = when {
                isClosed -> "STRICTRECORDS · HOURLY WINDOW CLOSED"
                isReminder -> "STRICTRECORDS · HOURLY CHECK-IN REMINDER"
                else -> "STRICTRECORDS · HOURLY CHECK-IN"
            }
            val message = when {
                isClosed && attendance.equals("PRESENT", true) -> "$slot closed · PRESENT · AI review ${validation.replace('_', ' ').lowercase()}"
                isClosed -> "$slot closed · ABSENT · no accepted proof before the deadline"
                isReminder -> "$slot · submit before ${deadline.ifBlank { "the deadline" }}"
                else -> "$slot is open · submit before ${deadline.ifBlank { "the deadline" }}"
            }

            val channelId = "strictrecords_hourly_checkins"
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                nm.createNotificationChannel(NotificationChannel(
                    channelId,
                    "StrictRoutines Hourly Check-ins",
                    NotificationManager.IMPORTANCE_HIGH
                ).apply {
                    description = "Authoritative hourly attendance window notifications"
                    enableVibration(true)
                    setShowBadge(true)
                })
            }

            val notificationId = 10_000 + (eventId.hashCode() and 0x7fffffff) % 100_000
            val modal = if (isClosed) "HOURLY_CLOSED" else "HOURLY_CHECK"
            fun activityPendingIntent(requestCode: Int): PendingIntent {
                val intent = Intent(this, MainActivity::class.java).apply {
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
                    putExtra("OPEN_MODAL", modal)
                    putExtra("ACTIVITY_ID", activityId)
                    putExtra("HOUR_SLOT", slot)
                    putExtra("ATTENDANCE_STATUS", attendance)
                    putExtra("VALIDATION_STATUS", validation)
                }
                return PendingIntent.getActivity(
                    this, requestCode, intent,
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
                )
            }

            val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                Notification.Builder(this, channelId)
            } else {
                @Suppress("DEPRECATION")
                Notification.Builder(this)
            }
            builder.setSmallIcon(R.drawable.ic_launcher)
            .setLargeIcon(android.graphics.BitmapFactory.decodeResource(resources, R.drawable.strictbot_head))
                .setContentTitle(title)
                .setContentText(message)
                .setStyle(Notification.BigTextStyle().bigText(message))
                .setCategory(Notification.CATEGORY_REMINDER)
                .setPriority(Notification.PRIORITY_HIGH)
                .setContentIntent(activityPendingIntent(notificationId))
                .setAutoCancel(true)

            if (!isClosed && Build.VERSION.SDK_INT >= Build.VERSION_CODES.KITKAT_WATCH) {
                builder.addAction(Notification.Action.Builder(
                    android.R.drawable.ic_menu_edit,
                    "Submit Now",
                    activityPendingIntent(notificationId + 100_000)
                ).build())
            }

            if (!db.markNotificationSent(eventId, activityId, eventType)) return
            nm.notify(notificationId, builder.build())
        } catch (e: Exception) {
            Log.w(TAG, "Could not create hourly notification: ${e.message}")
        }
    }

    private fun isConnectedToNetwork(): Boolean {
        val manager = getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return false
        val network = manager.activeNetwork ?: return false
        val capabilities = manager.getNetworkCapabilities(network) ?: return false
        // Support Wi-Fi, VPN (Tailscale), Ethernet and Cellular transports
        return capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
               capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN) ||
               capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) ||
               capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)
    }

    private fun isConnectedToWifi(): Boolean = isConnectedToNetwork()

    internal fun onDemoAccessChanged() {
        lastScoreCacheTime = 0L
        lastTriggerTime = 0L
        // The normal monitoring task keeps its cadence. A demo boundary must
        // not invoke permission-penalty accounting or fabricate a usage event.
    }

    private fun enforceDisciplineTick() {
        YouTubeRewardManager.tick(applicationContext)
        AccessibilityGraceManager.checkAccessibilityState(this)

        val monitoringStatus = TargetAppMonitoringPolicy.monitoringStatus(this)
        if (monitoringStatus != lastMonitoringStatus) {
            lastMonitoringStatus = monitoringStatus
            Log.i("StrictRestriction", "platform=Android monitoring=$monitoringStatus")
            StrictAccessibilityService.activeInstance?.refreshPackageFilter()
            (getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager)?.notify(NOTIFICATION_ID, buildForegroundNotification())
        }

        // Usage Access reconciliation has its own bounded task, including while
        // financial safety suppresses all intrusive enforcement.
        if (StrictAccessibilityService.activeInstance == null) {
            val a11yStatus = AccessibilityGraceManager.checkAccessibilityState(this)
            if (a11yStatus.isInterrupted && a11yStatus.remainingGraceMs <= 0L &&
                a11yStatus.stateCode == AccessibilityGraceManager.STATE_PENALTY_ELIGIBLE) {
                if (isDeviceUnlockedAndInteractive() && !FinancialSafePause.isPauseActive(this) && TargetAppMonitoringPolicy.canReadUsage(this)) {
                    val fg = HardwareDeviceVerifier.detectActiveForegroundApp(this) ?: UsageAccessMonitor.foreground().packageName.orEmpty()
                    if (fg.isNotBlank() && TargetAppMonitoringPolicy.isTargetPackage(fg)) {
                        FloatingDisciplineBotOverlay.updateForForegroundApp(this, fg)
                        return
                    }
                }
            }
            clearTargetOverlay()
            return
        }
        if (!isRunning.get() || FinancialSafePause.isPauseActive(this)) {
            clearTargetOverlay()
            return
        }

        // Run ONLY when mobile is unlocked and screen is interactive
        if (!isDeviceUnlockedAndInteractive() || !TargetAppMonitoringPolicy.canReadUsage(this)) {
            clearTargetOverlay()
            return
        }

        // Check foreground application
        val currentForegroundPkg = HardwareDeviceVerifier.lastAccessibilityForegroundPkg.ifBlank {
            HardwareDeviceVerifier.detectActiveForegroundApp(this) ?: ""
        }

        // If financial app in foreground, pause and clear overlay
        if (FinancialSafePause.isProtectedFinancialPackage(this, currentForegroundPkg)) {
            FinancialSafePause.onProtectedPackageObserved(this, currentForegroundPkg)
            clearTargetOverlay(currentForegroundPkg)
            return
        } else if (currentForegroundPkg.isNotBlank()) {
            FinancialSafePause.onNonProtectedPackageObserved(this, currentForegroundPkg)
        }
        if (FinancialSafePause.isPauseActive(this)) {
            clearTargetOverlay(currentForegroundPkg)
            return
        }

        // Run ONLY when in usage of apps that are selected restricted targets!
        // If launcher, system UI, our own app, or non-restricted apps (e.g. phone, settings, messaging, chrome):
        // do zero expensive work and return immediately.
        if (currentForegroundPkg.isBlank() || currentForegroundPkg == packageName ||
            TargetAppMonitoringPolicy.selectedTarget(this, currentForegroundPkg) == null) {
            clearTargetOverlay(currentForegroundPkg)
            BackgroundMusicRestrictionManager.checkAndEnforceMusicRestrictions(this, "enforce_tick_background")
            return
        }

        // The detector confirms a fresh selected target. An old cached package
        // or an unknown foreground observation cannot authorize a restriction.
        val observation = HardwareDeviceVerifier.detectForegroundObservation(this)
        if (observation.status != TargetAppMonitoringPolicy.MONITOR) {
            if (observation.status == TargetAppMonitoringPolicy.UNKNOWN) StrictBotOverlayController.onForegroundUncertain(this)
            else clearTargetOverlay(observation.packageName.orEmpty())
            return
        }
        val fgPkg = observation.packageName.orEmpty()
        if (FinancialSafePause.isProtectedFinancialPackage(this, fgPkg)) {
            FinancialSafePause.onProtectedPackageObserved(this, fgPkg)
            clearTargetOverlay(fgPkg)
            return
        }
        if (fgPkg.isBlank() || fgPkg == packageName || TargetAppMonitoringPolicy.selectedTarget(this, fgPkg) == null) {
            clearTargetOverlay(fgPkg)
            return
        }
        if (!HardwareDeviceVerifier.confirmTargetForeground(this, fgPkg)) {
            StrictBotOverlayController.onForegroundUncertain(this)
            return
        }

        val now = System.currentTimeMillis()
        val appLocks = if (now - lastLocksCacheTime < 10_000L && lastLocksCached != null) {
            lastLocksCached!!
        } else {
            db.getRemoteAppLocks().also {
                lastLocksCached = it
                lastLocksCacheTime = now
                currentAppLocks = it
            }
        }

        val today = db.nowDate()
        val score = if (now - lastScoreCacheTime < 10_000L && lastScoreCached >= 0) {
            lastScoreCached
        } else {
            db.getScore(today, false).also {
                lastScoreCached = it
                lastScoreCacheTime = now
            }
        }
        val isEduActive = db.isEducationalUnlockActive()
        val decision = DisciplineScoringRules.evaluateRestrictionDecision(
            score = score,
            packageNameOrApp = fgPkg,
            appLocks = appLocks,
            isEducationalUnlockActive = isEduActive,
            context = this,
            db = db
        )

        if (!decision.isRestricted) {
            clearTargetOverlay(fgPkg)
            return
        }

        // If user already dismissed the StrictBot productivity popup for this foreground session, do not re-trigger
        if (StrictBotOverlayController.isDismissedForCurrentSession(fgPkg)) {
            StrictBotOverlayController.updateVisibleRestriction(decision)
            return
        }

        // Throttle triggers to avoid spamming intents
        if (fgPkg == lastTriggerPkg && now - lastTriggerTime < 2200L) {
            return
        }
        lastTriggerTime = now
        lastTriggerPkg = fgPkg

        // Recheck selection, permission, and freshness at the mutation boundary
        val currentObservation = HardwareDeviceVerifier.detectForegroundObservation(this)
        if (!isRunning.get() || currentObservation.status != TargetAppMonitoringPolicy.MONITOR || currentObservation.packageName != fgPkg ||
            !HardwareDeviceVerifier.confirmTargetForeground(this, fgPkg)) {
            if (isRunning.get() && (currentObservation.status == TargetAppMonitoringPolicy.UNKNOWN ||
                    currentObservation.packageName == fgPkg)) StrictBotOverlayController.onForegroundUncertain(this)
            else clearTargetOverlay(currentObservation.packageName.orEmpty())
            return
        }

        Log.w(TAG, "Restricted app '${decision.appName}' ($fgPkg) detected! Score=$score/${decision.requiredScore}. Showing StrictBot popup.")
        if (BackgroundMusicRestrictionManager.isMonitoredPlaybackPackage(fgPkg)) {
            BackgroundMusicRestrictionManager.onTargetAppForegrounded(this, fgPkg)
        }
        FloatingDisciplineBotOverlay.updateForForegroundApp(this, fgPkg)
    }

    private fun clearTargetOverlay(foregroundPackage: String = "") {
        if (StrictBotOverlayController.ownsFallbackForeground(foregroundPackage)) return
        lastTriggerPkg = null
        lastTriggerTime = 0L
        FloatingDisciplineBotOverlay.hide()
        StrictBotOverlayController.hidePopupImmediate()
        if (foregroundPackage.isNotBlank()) StrictBotOverlayController.onNonRestrictedForegroundObserved(foregroundPackage)
    }

    override fun onDestroy() {
        isRunning.set(false)
        FloatingAccessibilityCountdownOverlay.hide()
        StrictWidgetUpdates.request(applicationContext,"monitoring")
        if (activeInstance === this) activeInstance = null
        monitoringTask?.cancel(false)
        monitoringTask = null
        usageTask?.cancel(false)
        usageTask = null
        syncTask?.cancel(false)
        syncTask = null
        permissionWatchTask?.cancel(false)
        permissionWatchTask = null
        try { contentResolver.unregisterContentObserver(accessibilityObserver) } catch (_: Exception) {}
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            appOpsListener?.let {
                try {
                    val appOps = getSystemService(Context.APP_OPS_SERVICE) as? android.app.AppOpsManager
                    appOps?.stopWatchingMode(it)
                } catch (_: Exception) {}
            }
            appOpsListener = null
        }
        bluetoothSyncTask?.cancel(false)
        bluetoothSyncTask = null
        bluetoothSyncCoordinator?.stop()
        bluetoothSyncCoordinator = null
        updateStreamRunning.set(false)
        synchronized(streamSignal) { streamSignal.notifyAll() }
        updateStreamConnection?.disconnect()
        updateStreamThread?.interrupt()
        runCatching { connectivityManager?.unregisterNetworkCallback(networkCallback) }
        try { unregisterReceiver(appLocksReceiver) } catch (_: Exception) {}
        try { unregisterReceiver(screenStateReceiver) } catch (_: Exception) {}
        try { unregisterReceiver(prepareSessionReceiver) } catch (_: Exception) {}
        try { commandPollTask?.cancel(false) } catch (_: Exception) {}
        commandPollTask = null
        getSharedPreferences(TargetAppMonitoringPolicy.PREFERENCES, Context.MODE_PRIVATE).unregisterOnSharedPreferenceChangeListener(monitoringPreferencesListener)
        executor?.shutdownNow()
        clearTargetOverlay()
        super.onDestroy()
        Log.i(TAG, "StrictEnforcementService destroyed.")
    }

    internal fun executeSessionCommand(cmdType: String, payload: JSONObject) {
        when (cmdType) {
            "START_STRICT_SESSION" -> {
                val sId = payload.optString("session_id")
                val uId = payload.optString("user_id")
                val serviceIntent = Intent(this, AndroidStrictMonitoringService::class.java).apply {
                    action = AndroidStrictMonitoringService.ACTION_START_MONITORING
                    putExtra("session_id", sId)
                    putExtra("user_id", uId)
                    putExtra("start_timestamp", payload.optString("start_timestamp"))
                }
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    startForegroundService(serviceIntent)
                } else {
                    startService(serviceIntent)
                }
            }
            "STOP_STRICT_SESSION", "END_STRICT_SESSION", "SESSION_FINALIZED", "SESSION_ABORTED" -> {
                val stopIntent = Intent(this, AndroidStrictMonitoringService::class.java).apply {
                    action = AndroidStrictMonitoringService.ACTION_STOP_MONITORING
                }
                startService(stopIntent)
            }
            "PAUSE_STRICT_SESSION" -> {
                val pauseIntent = Intent(this, AndroidStrictMonitoringService::class.java).apply {
                    action = AndroidStrictMonitoringService.ACTION_PAUSE_MONITORING
                }
                startService(pauseIntent)
            }
            "RESUME_STRICT_SESSION" -> {
                val resumeIntent = Intent(this, AndroidStrictMonitoringService::class.java).apply {
                    action = AndroidStrictMonitoringService.ACTION_RESUME_MONITORING
                }
                startService(resumeIntent)
            }
            "UPDATE_SESSION_PHASE" -> {
                val phaseIntent = Intent(this, AndroidStrictMonitoringService::class.java).apply {
                    action = AndroidStrictMonitoringService.ACTION_UPDATE_PHASE
                    putExtra("session_id", payload.optString("session_id"))
                    putExtra("session_phase", payload.optString("session_phase"))
                    putExtra("phone_usage", payload.optString("phone_usage"))
                    putExtra("is_phone_usage_allowed", payload.optBoolean("is_phone_usage_allowed", false))
                    putExtra("phase_expires_at", payload.optString("phase_expires_at"))
                    putExtra("phase_expires_at_ms", payload.optLong("phase_expires_at_ms", 0L))
                    putExtra("allowed_packages", payload.optString("allowed_packages"))
                }
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    startForegroundService(phaseIntent)
                } else {
                    startService(phaseIntent)
                }
            }
            "UPDATE_ALLOWED_APPS_POLICY" -> {
                val updateIntent = Intent(this, AndroidStrictMonitoringService::class.java).apply {
                    action = AndroidStrictMonitoringService.ACTION_UPDATE_POLICY
                    putExtra("allowed_packages", payload.optString("allowed_packages"))
                }
                startService(updateIntent)
            }
            "PREPARE_STRICT_SESSION_CHECK" -> {
                Thread({ runCatching { handlePrepareStrictSessionCheck(payload) } }, "StrictSession-PrepareAck").start()
            }
            "REQUEST_USAGE_SYNC" -> {
                val syncIntent = Intent(this, AndroidStrictMonitoringService::class.java).apply {
                    action = AndroidStrictMonitoringService.ACTION_SYNC_USAGE
                }
                startService(syncIntent)
            }
        }
    }

    @Synchronized internal fun startCommandPolling() {
        val scheduler = executor ?: return
        if (scheduler.isShutdown || commandPollTask?.let { !it.isCancelled && !it.isDone } == true) return
        commandPollTask = scheduler.scheduleWithFixedDelay({
            try {
                if (isRunning.get() && HardwareDeviceVerifier.isNetworkOrWifiAvailable(this@StrictEnforcementService)) {
                    val reg = db.getDeviceRegistration()
                    val devId = reg?.optString("device_id").orEmpty()
                    val devToken = reg?.optString("device_token").orEmpty()
                    if (devId.isNotBlank() && devToken.isNotBlank()) {
                        val mgr = AppUpdateManager.getInstance(this@StrictEnforcementService)
                        pollAndExecuteAdminCommands(mgr, devId, devToken)
                    }
                }
            } catch (_: Exception) {}
        }, 1000, 3500, TimeUnit.MILLISECONDS)
    }

    internal fun pollAndExecuteAdminCommands(manager: AppUpdateManager, deviceId: String, deviceToken: String) {
        val candidateUrls = linkedSetOf<String>().apply {
            val base = manager.getBaseServerUrl().trimEnd('/')
            if (base.isNotBlank()) add(base)
            val reg = db.getDeviceRegistration()
            val ep = reg?.optString("server_endpoint").orEmpty().trimEnd('/')
            if (ep.isNotBlank()) add(ep)
            add("http://100.93.80.125:8765")
            add("http://192.168.29.9:8765")
        }
        for (baseUrl in candidateUrls) {
            var conn: HttpURLConnection? = null
            try {
                val endpoint = "$baseUrl/api/device/command/poll?device_id=" + java.net.URLEncoder.encode(deviceId, "UTF-8")
                conn = (URL(endpoint).openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = 3000
                    readTimeout = 3000
                    setRequestProperty("X-Device-Id", deviceId)
                    setRequestProperty("X-Device-Token", deviceToken)
                }
                if (conn.responseCode !in 200..299) continue
                val body = conn.inputStream.bufferedReader().use { it.readText() }
                val json = JSONObject(body)
                val commands = json.optJSONArray("commands") ?: continue
                for (i in 0 until commands.length()) {
                    val cmd = commands.getJSONObject(i)
                    val cmdId = cmd.optString("command_id")
                    val cmdType = cmd.optString("command_type")
                    val payloadStr = cmd.optString("payload_json", "{}")
                    val payload = try { JSONObject(payloadStr) } catch (_: Exception) { JSONObject() }
                    executeSessionCommand(cmdType, payload)
                    ackAdminCommand(baseUrl, deviceId, deviceToken, cmdId)
                }
                break
            } catch (_: Exception) {
            } finally {
                try { conn?.disconnect() } catch (_: Exception) {}
            }
        }
    }

    private fun ackAdminCommand(baseUrl: String, deviceId: String, deviceToken: String, cmdId: String) {
        var conn: HttpURLConnection? = null
        try {
            val ackUrl = "$baseUrl/api/device/command/ack"
            conn = (URL(ackUrl).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 3000
                readTimeout = 3000
                doOutput = true
                setRequestProperty("Content-Type", "application/json; charset=UTF-8")
                setRequestProperty("X-Device-Id", deviceId)
                setRequestProperty("X-Device-Token", deviceToken)
            }
            val payload = JSONObject().apply {
                put("command_id", cmdId)
                put("device_id", deviceId)
                put("device_token", deviceToken)
                put("success", true)
            }
            java.io.OutputStreamWriter(conn.outputStream, "UTF-8").use { it.write(payload.toString()) }
            conn.responseCode
        } catch (_: Exception) {
        } finally {
            try { conn?.disconnect() } catch (_: Exception) {}
        }
    }

    @Volatile
    private var lastKnownPreparationId: String = ""

    internal fun handlePrepareStrictSessionCheck(payload: JSONObject) {
        try {
            val registration = db.getDeviceRegistration()
            val deviceId = registration?.optString("device_id").orEmpty()
            val deviceToken = registration?.optString("device_token").orEmpty()
            val isAuthenticated = deviceId.isNotBlank() && deviceToken.isNotBlank()
            val manager = AppUpdateManager.getInstance(this)
            val hasUsageAccess = StrictSessionUsageMonitor.hasUsagePermission(this)
            val usageManager = getSystemService(Context.USAGE_STATS_SERVICE)
            val usageMonitorAvailable = usageManager != null
            val serviceReady = isRunning.get()
            var prepId = payload.optString("preparation_id", payload.optString("prep_id", "")).trim()
            if (prepId.isNotBlank()) {
                lastKnownPreparationId = prepId
            } else if (lastKnownPreparationId.isNotBlank()) {
                prepId = lastKnownPreparationId
            }
            val nowIso = db.nowIso()

            val missingPermissions = JSONArray()
            if (!hasUsageAccess) {
                missingPermissions.put("PACKAGE_USAGE_STATS")
            }
            val readinessStatus = if (hasUsageAccess) "READY" else "PERMISSION_REQUIRED"
            val errorDetails = if (!hasUsageAccess) "Usage Access (PACKAGE_USAGE_STATS) permission required." else ""

            val ackPayload = JSONObject().apply {
                put("event_type", "ANDROID_STRICT_READY_ACK")
                put("device_id", deviceId)
                put("preparation_id", prepId)
                put("apk_version", BuildConfig.VERSION_NAME)
                put("authenticated", isAuthenticated)
                put("backend_connected", true)
                put("transport_connected", true)
                put("usage_access_granted", hasUsageAccess)
                put("usage_monitor_available", usageMonitorAvailable)
                put("monitoring_service_startable", true)
                put("monitoring_service_ready", serviceReady)
                put("permissions_required", missingPermissions)
                put("readiness_status", readinessStatus)
                put("acknowledgement_timestamp", nowIso)
                put("ack_timestamp", nowIso)
                put("error_details", errorDetails)
                put("connectivity_status", "CONNECTED")
                put("session_ready", hasUsageAccess && serviceReady)
            }

            val candidateUrls = linkedSetOf<String>().apply {
                val base = manager.getBaseServerUrl().trimEnd('/')
                if (base.isNotBlank()) add(base)
                val ep = registration?.optString("server_endpoint").orEmpty().trimEnd('/')
                if (ep.isNotBlank()) add(ep)
                add("http://100.93.80.125:8765")
                add("http://192.168.29.9:8765")
            }

            var delivered = false
            for (baseUrl in candidateUrls) {
                var conn: HttpURLConnection? = null
                try {
                    val ackUrl = "$baseUrl/api/strict_session/android/prepare_ack"
                    conn = (URL(ackUrl).openConnection() as HttpURLConnection).apply {
                        requestMethod = "POST"
                        connectTimeout = 3000
                        readTimeout = 3000
                        doOutput = true
                        setRequestProperty("Content-Type", "application/json; charset=UTF-8")
                        setRequestProperty("X-Device-Id", deviceId)
                        if (deviceToken.isNotBlank()) setRequestProperty("X-Device-Token", deviceToken)
                    }
                    java.io.OutputStreamWriter(conn.outputStream, "UTF-8").use { it.write(ackPayload.toString()) }
                    val code = conn.responseCode
                    if (code in 200..299) {
                        Log.i(TAG, "PREPARE_STRICT_SESSION_CHECK -> ANDROID_STRICT_READY_ACK delivered to $ackUrl (HTTP $code, prep=$prepId, usage=$hasUsageAccess, ready=${hasUsageAccess && serviceReady})")
                        delivered = true
                        break
                    } else {
                        Log.w(TAG, "ACK attempt to $ackUrl returned HTTP $code")
                    }
                } catch (ce: Exception) {
                    Log.d(TAG, "ACK attempt to $baseUrl failed: ${ce.message}")
                } finally {
                    try { conn?.disconnect() } catch (_: Exception) {}
                }
            }
            if (!delivered) {
                Log.w(TAG, "Could not deliver ANDROID_STRICT_READY_ACK to any candidate URL")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to send ANDROID_STRICT_READY_ACK: ${e.message}")
        }
    }
    private fun _unusedMarker(conn: HttpURLConnection) { try {
            conn.responseCode
        } catch (_: Exception) {}
    }

}
