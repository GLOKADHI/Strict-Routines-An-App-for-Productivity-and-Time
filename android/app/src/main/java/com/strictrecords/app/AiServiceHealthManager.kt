package com.strictrecords.app

import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
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
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Part A (Sections 1–6): Persistent AI Service Health Monitoring & Provider Failover Manager.
 *
 * - Replaces repeated on-window-open "Taufa AI Service Unavailable" requests with a single
 *   persistent AI service health record and a 60-minute (3,600,000 ms) periodic check.
 * - Never issues a new AI test request when opening the dashboard, scheduling window,
 *   proof-submission window, activity screen, switching tabs, or returning from background
 *   unless the cached record is older than 60 minutes or manually retried in Settings.
 * - Coordinates primary provider (Taufa AI) and fallback provider (Official OpenAI API)
 *   via the backend health coordinator (`/api/ai_health/status` and `/api/ai_health/check`).
 */
object AiServiceHealthManager {
    private const val TAG = "AiServiceHealth"
    const val PREFS_NAME = "strictrecords_ai_health_v1"
    const val KEY_HEALTH_RECORD = "ai_service_health_record_json"
    const val HEALTH_CHECK_INTERVAL_MS = 60L * 60L * 1000L // 60 minutes (3,600,000 ms)
    const val JOB_ID_PERIODIC_HEALTH = 94101

    // Standardized UI status labels (Part A, Section 4)
    const val UI_STATUS_CHECKING = "AI STATUS: CHECKING"
    const val UI_STATUS_CONNECTED_TAUFA = "AI STATUS: CONNECTED – TAUFA AI"
    const val UI_STATUS_CONNECTED_OPENAI = "AI STATUS: CONNECTED – OPENAI FALLBACK"
    const val UI_STATUS_DEGRADED = "AI STATUS: DEGRADED"
    const val UI_STATUS_UNAVAILABLE = "AI STATUS: UNAVAILABLE"
    const val UI_STATUS_OUTDATED = "AI STATUS: LAST CHECK OUTDATED"

    data class OpenAiProfileSafe(
        val profileId: String = "",
        val name: String = "",
        val priority: Int = 1,
        val model: String = "gpt-4o-mini",
        val enabled: Boolean = true,
        val isConfigured: Boolean = false,
        val status: String = "NOT_CONFIGURED",
        val lastChecked: String = "",
        val lastError: String = "",
        val latencyMs: Int = 0
    ) {
        fun toJson(): JSONObject = JSONObject().apply {
            put("profile_id", profileId)
            put("name", name)
            put("priority", priority)
            put("model", model)
            put("enabled", enabled)
            put("is_configured", isConfigured)
            put("status", status)
            put("last_checked", lastChecked)
            put("last_error", lastError)
            put("response_latency_ms", latencyMs)
        }

        companion object {
            fun fromJson(json: JSONObject): OpenAiProfileSafe {
                return OpenAiProfileSafe(
                    profileId = json.optString("profile_id", ""),
                    name = json.optString("name", ""),
                    priority = json.optInt("priority", 1),
                    model = json.optString("model", "gpt-4o-mini"),
                    enabled = json.optBoolean("enabled", true),
                    isConfigured = json.optBoolean("is_configured", false),
                    status = json.optString("status", "NOT_CONFIGURED").uppercase(Locale.US),
                    lastChecked = json.optString("last_checked", ""),
                    lastError = json.optString("last_error", ""),
                    latencyMs = json.optInt("response_latency_ms", 0)
                )
            }
        }
    }

    data class HealthRecord(
        val lastHealthCheck: String = "",
        val lastHealthCheckMs: Long = 0L,
        val nextHealthCheck: String = "",
        val nextHealthCheckMs: Long = 0L,
        val taufaStatus: String = "NOT_CHECKED",
        val openaiStatus: String = "NOT_CHECKED",
        val activeProvider: String = "UNAVAILABLE", // TAUFA_AI | OPENAI_FALLBACK | UNAVAILABLE
        val lastSuccessfulProvider: String = "",
        val responseLatencyMs: Int = 0,
        val lastError: String = "",
        val healthCheckRequestId: String = "",
        val uiStatus: String = UI_STATUS_OUTDATED,
        val isChecking: Boolean = false,
        val configVersion: Int = 1,
        val profiles: List<OpenAiProfileSafe> = emptyList(),
        val lastSyncTimestamp: String = ""
    ) {
        val isAvailable: Boolean
            get() = activeProvider == "TAUFA_AI" || activeProvider == "OPENAI_FALLBACK"

        fun isStale(nowMs: Long = System.currentTimeMillis()): Boolean {
            if (lastHealthCheckMs <= 0L) return true
            return (nowMs - lastHealthCheckMs) >= HEALTH_CHECK_INTERVAL_MS
        }

        fun effectiveUiStatus(nowMs: Long = System.currentTimeMillis()): String {
            if (isChecking) return UI_STATUS_CHECKING
            if (lastHealthCheckMs <= 0L) return UI_STATUS_OUTDATED
            return when {
                activeProvider == "TAUFA_AI" -> UI_STATUS_CONNECTED_TAUFA
                activeProvider == "OPENAI_FALLBACK" -> UI_STATUS_CONNECTED_OPENAI
                taufaStatus == "DEGRADED" || openaiStatus == "DEGRADED" -> UI_STATUS_DEGRADED
                isStale(nowMs) && !isAvailable -> UI_STATUS_OUTDATED
                else -> UI_STATUS_UNAVAILABLE
            }
        }

        fun toJson(): JSONObject = JSONObject().apply {
            put("last_health_check", lastHealthCheck)
            put("last_health_check_ms", lastHealthCheckMs)
            put("next_health_check", nextHealthCheck)
            put("next_health_check_ms", nextHealthCheckMs)
            put("taufa_status", taufaStatus)
            put("openai_status", openaiStatus)
            put("active_provider", activeProvider)
            put("last_successful_provider", lastSuccessfulProvider)
            put("response_latency", responseLatencyMs)
            put("last_error", lastError)
            put("health_check_request_id", healthCheckRequestId)
            put("ui_status", uiStatus)
            put("is_checking", isChecking)
            put("config_version", configVersion)
            put("last_sync_timestamp", lastSyncTimestamp)
            val arr = JSONArray()
            for (p in profiles) arr.put(p.toJson())
            put("profiles", arr)
        }

        companion object {
            fun fromJson(json: JSONObject): HealthRecord {
                val active = json.optString("active_provider", "UNAVAILABLE").uppercase(Locale.US)
                val taufa = json.optString("taufa_status", "NOT_CHECKED").uppercase(Locale.US)
                val openai = json.optString("openai_status", "NOT_CHECKED").uppercase(Locale.US)
                val lastMs = json.optLong("last_health_check_ms", 0L)
                val nextMs = json.optLong("next_health_check_ms", if (lastMs > 0L) lastMs + HEALTH_CHECK_INTERVAL_MS else 0L)
                val ver = json.optInt("config_version", 1)
                val syncTs = json.optString("last_sync_timestamp", "")

                val profList = mutableListOf<OpenAiProfileSafe>()
                val arr = json.optJSONArray("profiles")
                if (arr != null) {
                    for (i in 0 until arr.length()) {
                        val obj = arr.optJSONObject(i)
                        if (obj != null) {
                            profList.add(OpenAiProfileSafe.fromJson(obj))
                        }
                    }
                }
                if (profList.isEmpty()) {
                    for (i in 1..5) {
                        profList.add(OpenAiProfileSafe(
                            profileId = "openai_$i",
                            name = "OpenAI API $i",
                            priority = i,
                            model = "gpt-4o-mini",
                            enabled = (i == 1),
                            status = if (i == 1 && openai == "CONNECTED") "CONNECTED" else "NOT_CONFIGURED"
                        ))
                    }
                }

                val ui = json.optString("ui_status", "").ifBlank {
                    when {
                        active == "TAUFA_AI" -> UI_STATUS_CONNECTED_TAUFA
                        active == "OPENAI_FALLBACK" -> UI_STATUS_CONNECTED_OPENAI
                        taufa == "DEGRADED" || openai == "DEGRADED" -> UI_STATUS_DEGRADED
                        lastMs <= 0L -> UI_STATUS_OUTDATED
                        else -> UI_STATUS_UNAVAILABLE
                    }
                }
                return HealthRecord(
                    lastHealthCheck = json.optString("last_health_check", formatIso(lastMs)),
                    lastHealthCheckMs = lastMs,
                    nextHealthCheck = json.optString("next_health_check", formatIso(nextMs)),
                    nextHealthCheckMs = nextMs,
                    taufaStatus = taufa,
                    openaiStatus = openai,
                    activeProvider = active,
                    lastSuccessfulProvider = json.optString("last_successful_provider", ""),
                    responseLatencyMs = json.optInt("response_latency", 0),
                    lastError = json.optString("last_error", ""),
                    healthCheckRequestId = json.optString("health_check_request_id", ""),
                    uiStatus = ui,
                    isChecking = json.optBoolean("is_checking", false),
                    configVersion = ver,
                    profiles = profList,
                    lastSyncTimestamp = syncTs
                )
            }
        }
    }

    private val worker = Executors.newSingleThreadExecutor { r ->
        Thread(r, "StrictRecords-AiHealthWorker").apply { isDaemon = true }
    }
    private val mainHandler = Handler(Looper.getMainLooper())
    private val checkInFlight = AtomicBoolean(false)
    private val listeners = CopyOnWriteArrayList<(HealthRecord) -> Unit>()

    @Volatile
    private var memoryCache: HealthRecord? = null

    fun addListener(listener: (HealthRecord) -> Unit) {
        if (!listeners.contains(listener)) {
            listeners.add(listener)
        }
    }

    fun removeListener(listener: (HealthRecord) -> Unit) {
        listeners.remove(listener)
    }

    private fun notifyListeners(record: HealthRecord) {
        mainHandler.post {
            for (l in listeners) {
                runCatching { l(record) }
            }
        }
    }

    fun formatIso(ms: Long): String {
        if (ms <= 0L) return ""
        return try {
            SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).apply {
                timeZone = TimeZone.getTimeZone("Asia/Kolkata")
            }.format(Date(ms))
        } catch (_: Exception) {
            ""
        }
    }

    /**
     * Reads the persistent AI health record from local storage without making ANY network call.
     */
    fun getCachedHealthRecord(context: Context): HealthRecord {
        memoryCache?.let { return it }
        synchronized(this) {
            memoryCache?.let { return it }
            val app = context.applicationContext
            val prefs = app.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            var raw = prefs.getString(KEY_HEALTH_RECORD, null)
            if (raw.isNullOrBlank()) {
                raw = runCatching {
                    StrictRecordsLocalDb.getInstance(app).getConfigValue(KEY_HEALTH_RECORD, "")
                }.getOrNull()
            }
            val loaded = if (!raw.isNullOrBlank()) {
                runCatching { HealthRecord.fromJson(JSONObject(raw)) }.getOrDefault(HealthRecord())
            } else {
                HealthRecord()
            }
            memoryCache = loaded
            return loaded
        }
    }

    /**
     * Persists the authoritative AI health record to SharedPreferences and SQLite.
     */
    fun saveHealthRecord(context: Context, record: HealthRecord) {
        val app = context.applicationContext
        val raw = record.toJson().toString()
        synchronized(this) {
            memoryCache = record
            app.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit()
                .putString(KEY_HEALTH_RECORD, raw)
                .apply()
            runCatching {
                StrictRecordsLocalDb.getInstance(app).setConfigValue(KEY_HEALTH_RECORD, raw)
            }
        }
        notifyListeners(record)
    }

    /**
     * Schedules the single unique 60-minute periodic background job via Android JobScheduler.
     * Also performs an opportunistic refresh ONLY if the cached record is stale (> 60 minutes old).
     */
    fun initializeAndSchedulePeriodicCheck(context: Context) {
        val app = context.applicationContext
        try {
            val scheduler = app.getSystemService(Context.JOB_SCHEDULER_SERVICE) as? JobScheduler
            val existingJob = scheduler?.allPendingJobs?.firstOrNull { it.id == JOB_ID_PERIODIC_HEALTH }
            if (existingJob == null) {
                val component = ComponentName(app, AiHealthCheckJobService::class.java)
                val jobInfo = JobInfo.Builder(JOB_ID_PERIODIC_HEALTH, component)
                    .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                    .setPeriodic(HEALTH_CHECK_INTERVAL_MS)
                    .setPersisted(true)
                    .build()
                scheduler?.schedule(jobInfo)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to schedule periodic AI health job: ${e.message}")
        }
        refreshIfStaleAsync(app, reason = "app_init_or_resume")
        synchronizeConfigWithBackend(app, force = false)
    }

    /**
     * Opportunistic check: runs a background health check ONLY if the persistent record
     * has never been checked or is older than 60 minutes (3,600,000 ms).
     */
    fun refreshIfStaleAsync(
        context: Context,
        reason: String = "opportunistic_stale_check",
        onComplete: ((HealthRecord) -> Unit)? = null
    ) {
        val current = getCachedHealthRecord(context)
        if (!current.isStale()) {
            onComplete?.let { cb -> mainHandler.post { cb(current) } }
            return
        }
        executeHealthCheckAsync(context, force = false, reason = reason, onComplete = onComplete)
    }

    /**
     * Manual "Retry AI Connection" action in Settings: forces an immediate real health check.
     */
    fun retryAiConnectionNow(context: Context, onComplete: ((HealthRecord) -> Unit)? = null) {
        executeHealthCheckAsync(context, force = true, reason = "manual_settings_retry", onComplete = onComplete)
    }

    fun executeHealthCheckAsync(
        context: Context,
        force: Boolean,
        reason: String,
        onComplete: ((HealthRecord) -> Unit)? = null
    ) {
        val app = context.applicationContext
        val cached = getCachedHealthRecord(app)
        if (!force && !cached.isStale()) {
            onComplete?.let { cb -> mainHandler.post { cb(cached) } }
            return
        }
        if (!checkInFlight.compareAndSet(false, true)) {
            onComplete?.let { cb -> mainHandler.post { cb(getCachedHealthRecord(app)) } }
            return
        }

        val checkingState = cached.copy(isChecking = true, uiStatus = UI_STATUS_CHECKING)
        memoryCache = checkingState
        notifyListeners(checkingState)

        worker.execute {
            try {
                val updated = performBlockingHealthCheck(app, force = force, reason = reason)
                saveHealthRecord(app, updated)
                onComplete?.let { cb -> mainHandler.post { cb(updated) } }
            } finally {
                checkInFlight.set(false)
            }
        }
    }

    /**
     * Executes a single lightweight real health check against the paired backend coordinator
     * (`/api/ai_health/check`), which probes Taufa AI first and Official OpenAI API fallback second.
     */
    fun performBlockingHealthCheck(
        context: Context,
        force: Boolean = false,
        reason: String = "scheduled_60m"
    ): HealthRecord {
        val app = context.applicationContext
        val cached = getCachedHealthRecord(app)
        val nowMs = System.currentTimeMillis()
        if (!force && !cached.isStale(nowMs)) {
            return cached
        }

        val requestId = "ai_hc_${UUID.randomUUID().toString().replace("-", "").take(16)}"
        val startElapsed = System.currentTimeMillis()
        val db = StrictRecordsLocalDb.getInstance(app)
        val reg = db.getDeviceRegistration()
        val candidateEndpoints = TaufaPcBridge.getCandidateEndpoints(app)
        var lastError = "PC server unreachable"

        for (endpointBase in candidateEndpoints) {
            var conn: HttpURLConnection? = null
            try {
                val url = URL("${endpointBase.trimEnd('/')}/api/ai_health/check")
                conn = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 5_000
                    readTimeout = 10_000
                    doInput = true
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json; charset=UTF-8")
                    setRequestProperty("X-Device-Id", reg?.optString("device_id").orEmpty())
                    setRequestProperty("X-Device-Token", reg?.optString("device_token").orEmpty())
                }
                val reqBody = JSONObject().apply {
                    put("force", force)
                    put("reason", reason)
                    put("health_check_request_id", requestId)
                }
                OutputStreamWriter(conn.outputStream, Charsets.UTF_8).use { writer ->
                    writer.write(reqBody.toString())
                    writer.flush()
                }
                val code = conn.responseCode
                val stream = if (code in 200..299) conn.inputStream else conn.errorStream
                val bodyText = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
                val measuredLatency = (System.currentTimeMillis() - startElapsed).toInt().coerceAtLeast(1)

                if (code in 200..299 && bodyText.isNotBlank()) {
                    TaufaPcBridge.lastWorkingEndpoint = endpointBase
                    val json = JSONObject(bodyText)
                    val parsed = HealthRecord.fromJson(json)
                    val finalMs = if (parsed.lastHealthCheckMs > 0L) parsed.lastHealthCheckMs else System.currentTimeMillis()
                    return parsed.copy(
                        lastHealthCheck = parsed.lastHealthCheck.ifBlank { formatIso(finalMs) },
                        lastHealthCheckMs = finalMs,
                        nextHealthCheck = parsed.nextHealthCheck.ifBlank { formatIso(finalMs + HEALTH_CHECK_INTERVAL_MS) },
                        nextHealthCheckMs = if (parsed.nextHealthCheckMs > 0L) parsed.nextHealthCheckMs else finalMs + HEALTH_CHECK_INTERVAL_MS,
                        responseLatencyMs = if (parsed.responseLatencyMs > 0) parsed.responseLatencyMs else measuredLatency,
                        healthCheckRequestId = parsed.healthCheckRequestId.ifBlank { requestId },
                        isChecking = false
                    )
                } else {
                    lastError = "Backend AI health endpoint returned HTTP $code"
                }
            } catch (e: Exception) {
                lastError = "${e.javaClass.simpleName}: ${e.message}"
            } finally {
                runCatching { conn?.disconnect() }
            }
        }

        val completedMs = System.currentTimeMillis()
        val measuredLatency = (completedMs - startElapsed).toInt().coerceAtLeast(1)
        return HealthRecord(
            lastHealthCheck = formatIso(completedMs),
            lastHealthCheckMs = completedMs,
            nextHealthCheck = formatIso(completedMs + HEALTH_CHECK_INTERVAL_MS),
            nextHealthCheckMs = completedMs + HEALTH_CHECK_INTERVAL_MS,
            taufaStatus = "UNAVAILABLE",
            openaiStatus = "UNAVAILABLE",
            activeProvider = "UNAVAILABLE",
            lastSuccessfulProvider = cached.lastSuccessfulProvider,
            responseLatencyMs = measuredLatency,
            lastError = lastError,
            healthCheckRequestId = requestId,
            uiStatus = UI_STATUS_UNAVAILABLE,
            isChecking = false
        )
    }

    /**
     * Updates cached provider health after a live validation response arrives from the server.
     */
    fun recordLiveValidationOutcome(
        context: Context,
        provider: String,
        success: Boolean,
        latencyMs: Int = 0,
        errorReason: String = ""
    ) {
        val current = getCachedHealthRecord(context)
        val nowMs = System.currentTimeMillis()
        val normProvider = provider.uppercase(Locale.US)
        val updated = when {
            success && normProvider.contains("OPENAI") -> current.copy(
                lastHealthCheck = formatIso(nowMs),
                lastHealthCheckMs = nowMs,
                nextHealthCheck = formatIso(nowMs + HEALTH_CHECK_INTERVAL_MS),
                nextHealthCheckMs = nowMs + HEALTH_CHECK_INTERVAL_MS,
                openaiStatus = "CONNECTED",
                activeProvider = "OPENAI_FALLBACK",
                lastSuccessfulProvider = "OPENAI_FALLBACK",
                responseLatencyMs = if (latencyMs > 0) latencyMs else current.responseLatencyMs,
                lastError = "",
                uiStatus = UI_STATUS_CONNECTED_OPENAI,
                isChecking = false
            )
            success -> current.copy(
                lastHealthCheck = formatIso(nowMs),
                lastHealthCheckMs = nowMs,
                nextHealthCheck = formatIso(nowMs + HEALTH_CHECK_INTERVAL_MS),
                nextHealthCheckMs = nowMs + HEALTH_CHECK_INTERVAL_MS,
                taufaStatus = "CONNECTED",
                activeProvider = "TAUFA_AI",
                lastSuccessfulProvider = "TAUFA_AI",
                responseLatencyMs = if (latencyMs > 0) latencyMs else current.responseLatencyMs,
                lastError = "",
                uiStatus = UI_STATUS_CONNECTED_TAUFA,
                isChecking = false
            )
            else -> current.copy(
                lastError = errorReason.ifBlank { current.lastError },
                isChecking = false
            )
        }
        saveHealthRecord(context, updated)
    }

    /**
     * Synchronizes safe AI configuration metadata (Version, Priority, 5 Profiles) from backend.
     * Guaranteed ZERO API keys handled or received on Android.
     */
    fun synchronizeConfigWithBackend(
        context: Context,
        force: Boolean = false,
        onComplete: ((HealthRecord) -> Unit)? = null
    ) {
        val app = context.applicationContext
        worker.execute {
            try {
                val db = StrictRecordsLocalDb.getInstance(app)
                val reg = db.getDeviceRegistration()
                val candidateEndpoints = TaufaPcBridge.getCandidateEndpoints(app)
                var updated = getCachedHealthRecord(app)

                for (endpointBase in candidateEndpoints) {
                    var conn: HttpURLConnection? = null
                    try {
                        val url = URL("${endpointBase.trimEnd('/')}/api/ai/config")
                        conn = (url.openConnection() as HttpURLConnection).apply {
                            requestMethod = "GET"
                            connectTimeout = 4_000
                            readTimeout = 6_000
                            setRequestProperty("X-Device-Id", reg?.optString("device_id").orEmpty())
                            setRequestProperty("X-Device-Token", reg?.optString("device_token").orEmpty())
                        }
                        val code = conn.responseCode
                        if (code in 200..299) {
                            val bodyText = conn.inputStream.bufferedReader().use { it.readText() }
                            val json = JSONObject(bodyText)
                            if (json.optBoolean("success", true)) {
                                val serverVer = json.optInt("config_version", 1)
                                val arr = json.optJSONArray("profiles")
                                val profs = mutableListOf<OpenAiProfileSafe>()
                                if (arr != null) {
                                    for (i in 0 until arr.length()) {
                                        val obj = arr.optJSONObject(i)
                                        if (obj != null) profs.add(OpenAiProfileSafe.fromJson(obj))
                                    }
                                }
                                val active = json.optString("active_provider", updated.activeProvider)
                                val nowIso = formatIso(System.currentTimeMillis())
                                updated = updated.copy(
                                    configVersion = serverVer,
                                    profiles = if (profs.isNotEmpty()) profs else updated.profiles,
                                    activeProvider = if (active.isNotBlank()) active else updated.activeProvider,
                                    lastSyncTimestamp = nowIso
                                )
                                saveHealthRecord(app, updated)
                                TaufaPcBridge.lastWorkingEndpoint = endpointBase
                                break
                            }
                        }
                    } catch (e: Exception) {
                        Log.d(TAG, "Config sync endpoint failed on $endpointBase: ${e.message}")
                    } finally {
                        runCatching { conn?.disconnect() }
                    }
                }
                mainHandler.post { onComplete?.invoke(updated) }
            } catch (e: Exception) {
                Log.w(TAG, "synchronizeConfigWithBackend failed: ${e.message}")
                mainHandler.post { onComplete?.invoke(getCachedHealthRecord(app)) }
            }
        }
    }
}

/**
 * AI Services & Validation Status Card for Settings (Section 4).
 * Displays Taufa AI primary and all 5 OpenAI fallback profiles with synchronized metadata.
 */
object AiHealthSettingsCard {
    fun create(context: Context, onRefresh: () -> Unit): View {
        val density = context.resources.displayMetrics.density
        fun dp(v: Int): Int = (v * density).toInt()
        fun cardBg(fillHex: String, radiusDp: Int, strokeHex: String, strokeDp: Int = 1): GradientDrawable {
            return GradientDrawable().apply {
                shape = GradientDrawable.RECTANGLE
                cornerRadius = dp(radiusDp).toFloat()
                setColor(Color.parseColor(fillHex))
                setStroke(dp(strokeDp), Color.parseColor(strokeHex))
            }
        }

        val health = AiServiceHealthManager.getCachedHealthRecord(context)
        val uiStatus = health.effectiveUiStatus()
        val accentHex = when (uiStatus) {
            AiServiceHealthManager.UI_STATUS_CONNECTED_TAUFA,
            AiServiceHealthManager.UI_STATUS_CONNECTED_OPENAI -> "#10B981"
            AiServiceHealthManager.UI_STATUS_CHECKING -> "#06B6D4"
            AiServiceHealthManager.UI_STATUS_DEGRADED,
            AiServiceHealthManager.UI_STATUS_OUTDATED -> "#F59E0B"
            else -> "#EF4444"
        }

        val root = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(16))
            background = cardBg("#111726", 18, accentHex, 1)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(14) }
        }

        // Section Title & Version Header
        val headerRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(10)
            }
        }

        val titleCol = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }

        val titleRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        titleRow.addView(TextView(context).apply {
            text = "AI SERVICES & VALIDATION STATUS"
            textSize = 12.5f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.parseColor("#F8FAFC"))
        })

        titleRow.addView(TextView(context).apply {
            text = " v${health.configVersion}"
            textSize = 10f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.parseColor("#60A5FA"))
            setPadding(dp(6), dp(2), dp(6), dp(2))
            background = cardBg("#1E293B", 6, "#3B82F6", 1)
            val lp = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                leftMargin = dp(6)
            }
            layoutParams = lp
        })
        titleCol.addView(titleRow)

        titleCol.addView(TextView(context).apply {
            text = "Authoritative Centralized AI Validation Chain"
            textSize = 9.5f
            setTextColor(Color.parseColor("#94A3B8"))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(2)
            }
        })
        headerRow.addView(titleCol)

        val retryBtn = TextView(context).apply {
            text = if (health.isChecking) "Syncing…" else "Retry & Sync"
            textSize = 11f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            setPadding(dp(12), dp(7), dp(12), dp(7))
            background = cardBg("#1E293B", 8, "#3B82F6", 1)
            setOnClickListener {
                text = "Syncing…"
                isEnabled = false
                AiServiceHealthManager.retryAiConnectionNow(context) {
                    AiServiceHealthManager.synchronizeConfigWithBackend(context, force = true) {
                        onRefresh()
                    }
                }
            }
        }
        headerRow.addView(retryBtn)
        root.addView(headerRow)

        // PRIMARY PROVIDER CARD
        val taufaRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(12), dp(10), dp(12), dp(10))
            background = cardBg("#0F172A", 10, "#1E293B", 1)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(12)
            }
        }
        val taufaTextCol = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        taufaTextCol.addView(TextView(context).apply {
            text = "PRIMARY PROVIDER: Taufa AI"
            textSize = 11f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.parseColor("#E2E8F0"))
        })
        val taufaCheckTime = if (health.lastHealthCheck.length >= 19) health.lastHealthCheck.substring(11, 19) else "Not checked yet"
        val taufaLatency = if (health.responseLatencyMs > 0) " • ${health.responseLatencyMs}ms" else ""
        taufaTextCol.addView(TextView(context).apply {
            text = "Last checked: $taufaCheckTime$taufaLatency"
            textSize = 9.5f
            setTextColor(Color.parseColor("#94A3B8"))
        })
        taufaRow.addView(taufaTextCol)

        val taufaStatusPill = TextView(context).apply {
            val isConnected = health.taufaStatus == "CONNECTED"
            text = health.taufaStatus
            textSize = 10f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.parseColor(if (isConnected) "#10B981" else "#EF4444"))
            setPadding(dp(8), dp(4), dp(8), dp(4))
            background = cardBg(
                if (isConnected) "#064E3B" else "#450A0A",
                8,
                if (isConnected) "#10B981" else "#EF4444",
                1
            )
        }
        taufaRow.addView(taufaStatusPill)
        root.addView(taufaRow)

        // OPENAI FALLBACK PROFILES HEADER
        root.addView(TextView(context).apply {
            text = "OPENAI FALLBACK PROFILES"
            textSize = 10.5f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.parseColor("#94A3B8"))
            letterSpacing = 0.05f
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(6)
            }
        })

        // PROFILES CONTAINER
        val profList = if (health.profiles.isNotEmpty()) health.profiles else (1..5).map { i ->
            AiServiceHealthManager.OpenAiProfileSafe(
                profileId = "openai_$i",
                name = "OpenAI API $i",
                priority = i,
                model = "gpt-4o-mini",
                enabled = (i == 1),
                status = if (i == 1 && health.openaiStatus == "CONNECTED") "CONNECTED" else "NOT_CONFIGURED"
            )
        }

        for (p in profList.sortedBy { it.priority }.take(5)) {
            val pRow = LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(10), dp(8), dp(10), dp(8))
                background = cardBg("#0F172A", 8, "#1E293B", 1)
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    bottomMargin = dp(4)
                }
            }

            // Priority badge
            pRow.addView(TextView(context).apply {
                text = "#${p.priority}"
                textSize = 10f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(Color.parseColor("#38BDF8"))
                setPadding(dp(6), dp(2), dp(6), dp(2))
                background = cardBg("#0C4A6E", 4, "#0284C7", 1)
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    rightMargin = dp(8)
                }
            })

            // Name & Model
            val nameCol = LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            }
            nameCol.addView(TextView(context).apply {
                text = p.name.ifBlank { "OpenAI API ${p.priority}" }
                textSize = 10.5f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(Color.parseColor("#E2E8F0"))
            })
            val modelText = "${p.model}" + (if (p.latencyMs > 0) " • ${p.latencyMs}ms" else "")
            nameCol.addView(TextView(context).apply {
                text = modelText
                textSize = 9f
                setTextColor(Color.parseColor("#64748B"))
            })
            pRow.addView(nameCol)

            // Status pill
            val (stColor, stBg, stBorder) = when {
                !p.enabled -> Triple("#64748B", "#1E293B", "#334155")
                p.status == "CONNECTED" -> Triple("#10B981", "#064E3B", "#10B981")
                p.status == "DEGRADED" -> Triple("#F59E0B", "#451A03", "#F59E0B")
                p.status == "UNAVAILABLE" -> Triple("#EF4444", "#450A0A", "#EF4444")
                else -> Triple("#94A3B8", "#1E293B", "#334155")
            }

            pRow.addView(TextView(context).apply {
                text = if (!p.enabled) "DISABLED" else p.status
                textSize = 9f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(Color.parseColor(stColor))
                setPadding(dp(6), dp(3), dp(6), dp(3))
                background = cardBg(stBg, 6, stBorder, 1)
            })

            root.addView(pRow)
        }

        // FOOTER / SYNC INFO
        val pendingUploads = runCatching {
            PersistentUploadQueueManager.getPendingRecords(context).size
        }.getOrDefault(0)

        val footerText = buildString {
            append("Active: ${health.activeProvider.replace('_', ' ')}")
            val syncTime = if (health.lastSyncTimestamp.length >= 19) health.lastSyncTimestamp.substring(11, 19) else "Auto-synced"
            append(" • Synced: $syncTime")
            if (pendingUploads > 0) {
                append(" • Uploads: $pendingUploads pending")
            }
        }
        root.addView(TextView(context).apply {
            text = footerText
            textSize = 9.5f
            setTextColor(Color.parseColor("#64748B"))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(6)
            }
        })

        return root
    }
}

/**
 * Android JobService for the 60-minute periodic AI service health check (Part A, Section 3).
 */
class AiHealthCheckJobService : JobService() {
    override fun onStartJob(params: JobParameters?): Boolean {
        AiServiceHealthManager.executeHealthCheckAsync(
            context = applicationContext,
            force = false,
            reason = "periodic_60m_job"
        ) {
            jobFinished(params, false)
        }
        return true
    }

    override fun onStopJob(params: JobParameters?): Boolean = true
}
