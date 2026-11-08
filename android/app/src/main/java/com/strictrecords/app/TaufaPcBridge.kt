package com.strictrecords.app

import android.content.Context
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

/**
 * Paired PC Bridge for AI Service Health Monitoring (Part A) and Evidence Validation.
 *
 * Uses the persistent 60-minute `AiServiceHealthManager` cache (`STATUS_CACHE_TTL_MS = 3_600_000L`)
 * so opening APK windows/tabs/dialogs never triggers repeated "Taufa AI Service Unavailable" checks.
 * Supports Taufa AI as primary provider and Official OpenAI API as fallback provider on the backend.
 */
object TaufaPcBridge {

    data class TaufaBridgeStatus(
        val ready: Boolean,
        val authenticated: Boolean,
        val reachable: Boolean,
        val conversationId: String?,
        val message: String,
        val activeProvider: String = "UNAVAILABLE",
        val uiStatus: String = AiServiceHealthManager.UI_STATUS_OUTDATED,
        val responseLatencyMs: Int = 0
    )

    var cachedStatus: TaufaBridgeStatus? = null
    @Volatile
    var lastWorkingEndpoint: String? = null
    private var lastStatusCheckMs: Long = 0L
    const val STATUS_CACHE_TTL_MS = 60L * 60L * 1000L // 60 minutes (Part A, Section 1)

    private fun registration(context: Context?): JSONObject? = if (context == null) null else runCatching {
        StrictRecordsLocalDb.getInstance(context.applicationContext).getDeviceRegistration()
    }.getOrNull()

    fun getCandidateEndpoints(context: Context?): List<String> {
        val reg = registration(context)
        val endpoints = mutableListOf<String>()

        // 1. Prioritize last known working endpoint
        lastWorkingEndpoint?.let {
            val norm = StrictBotNetworkPolicy.normalizeEndpoint(it) ?: it.trim().trimEnd('/')
            if (norm.isNotBlank() && !endpoints.contains(norm)) endpoints.add(norm)
        }

        // 2. Registered server endpoint
        reg?.optString("server_endpoint")?.takeIf { it.isNotBlank() }?.let { raw ->
            val norm = StrictBotNetworkPolicy.normalizeEndpoint(raw) ?: raw.trim().trimEnd('/')
            if (norm.isNotBlank() && !endpoints.contains(norm)) endpoints.add(norm)
        }

        // 3. Direct Wi-Fi LAN endpoint of paired PC (primary physical phone path)
        if (!endpoints.contains("http://192.168.29.9:8765")) endpoints.add("http://192.168.29.9:8765")

        // 4. USB ADB reverse proxy candidate
        if (!endpoints.contains("http://127.0.0.1:8765")) endpoints.add("http://127.0.0.1:8765")

        // 5. Tailscale mesh VPN candidate
        if (!endpoints.contains("http://100.93.80.125:8765")) endpoints.add("http://100.93.80.125:8765")

        // 6. Android Emulator host candidate
        if (!endpoints.contains("http://10.0.2.2:8765")) endpoints.add("http://10.0.2.2:8765")

        return endpoints
    }

    fun isConfigured(context: Context?): Boolean {
        if (context == null) return false
        val reg = registration(context)
        if (reg != null) {
            val did = reg.optString("device_id")
            val tok = reg.optString("device_token")
            val ep = reg.optString("server_endpoint")
            if (did.isNotBlank() && tok.isNotBlank()) {
                if (StrictBotNetworkPolicy.normalizeEndpoint(ep) != null ||
                    ep.contains("127.0.0.1") || ep.contains("localhost") ||
                    ep.contains("192.168.") || ep.contains("10.0.2.")) {
                    return true
                }
            }
        }
        return true
    }

    /**
     * Converts a persistent `AiServiceHealthManager.HealthRecord` into a `TaufaBridgeStatus`.
     */
    fun statusFromHealthRecord(record: AiServiceHealthManager.HealthRecord): TaufaBridgeStatus {
        val active = record.activeProvider
        val isReady = active == "TAUFA_AI" || active == "OPENAI_FALLBACK"
        val ui = record.effectiveUiStatus()
        val msg = when (active) {
            "TAUFA_AI" -> AiServiceHealthManager.UI_STATUS_CONNECTED_TAUFA
            "OPENAI_FALLBACK" -> AiServiceHealthManager.UI_STATUS_CONNECTED_OPENAI
            else -> ui
        }
        return TaufaBridgeStatus(
            ready = isReady,
            authenticated = isReady,
            reachable = isReady || record.taufaStatus != "UNAVAILABLE" || record.responseLatencyMs > 0,
            conversationId = TaufaAiValidator.DESIGNATED_CONVERSATION_ID,
            message = msg,
            activeProvider = active,
            uiStatus = ui,
            responseLatencyMs = record.responseLatencyMs
        )
    }

    /**
     * Checks AI status using the persistent 60-minute health record first.
     * Never issues a new network check on window/screen open if the cached record is < 60 minutes old,
     * unless `forceRefresh = true` (e.g. manual "Retry AI Connection" in Settings).
     */
    fun checkStatus(context: Context?, forceRefresh: Boolean = false): TaufaBridgeStatus {
        val now = System.currentTimeMillis()
        if (context != null && !forceRefresh) {
            val persisted = AiServiceHealthManager.getCachedHealthRecord(context)
            if (!persisted.isStale(now)) {
                val bridgeStatus = statusFromHealthRecord(persisted)
                cachedStatus = bridgeStatus
                lastStatusCheckMs = persisted.lastHealthCheckMs
                return bridgeStatus
            }
        }
        if (!forceRefresh && cachedStatus != null && (now - lastStatusCheckMs) < STATUS_CACHE_TTL_MS) {
            return cachedStatus!!
        }

        if (context != null) {
            val record = AiServiceHealthManager.performBlockingHealthCheck(
                context = context,
                force = forceRefresh,
                reason = if (forceRefresh) "manual_retry" else "periodic_60m"
            )
            AiServiceHealthManager.saveHealthRecord(context, record)
            val status = statusFromHealthRecord(record)
            cachedStatus = status
            lastStatusCheckMs = record.lastHealthCheckMs
            return status
        }

        val endpoints = getCandidateEndpoints(null)
        var lastErr = "Unable to connect to paired PC."
        for (ep in endpoints) {
            var conn: HttpURLConnection? = null
            try {
                conn = (URL("$ep/api/taufa/status").openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = 3000
                    readTimeout = 5000
                    instanceFollowRedirects = false
                }
                val code = conn.responseCode
                if (code in 200..299) {
                    val raw = conn.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() }
                    val json = JSONObject(raw)
                    val auth = json.optBoolean("authenticated", false)
                    val activeProv = json.optString("active_provider", if (auth) "TAUFA_AI" else "UNAVAILABLE")
                    val uiSt = json.optString("ui_status", if (activeProv == "OPENAI_FALLBACK") AiServiceHealthManager.UI_STATUS_CONNECTED_OPENAI else if (auth) AiServiceHealthManager.UI_STATUS_CONNECTED_TAUFA else AiServiceHealthManager.UI_STATUS_UNAVAILABLE)
                    val convId = json.optString("conversation_id", TaufaAiValidator.DESIGNATED_CONVERSATION_ID).trim()

                    val status = TaufaBridgeStatus(
                        ready = auth || activeProv in setOf("TAUFA_AI", "OPENAI_FALLBACK"),
                        authenticated = auth || activeProv in setOf("TAUFA_AI", "OPENAI_FALLBACK"),
                        reachable = true,
                        conversationId = convId,
                        message = uiSt,
                        activeProvider = activeProv,
                        uiStatus = uiSt
                    )
                    lastWorkingEndpoint = ep
                    cachedStatus = status
                    lastStatusCheckMs = now
                    return status
                } else {
                    lastErr = "PC returned HTTP $code"
                }
            } catch (e: Exception) {
                lastErr = e.message ?: "Connection error"
            } finally {
                conn?.disconnect()
            }
        }

        val status = TaufaBridgeStatus(
            ready = false,
            authenticated = false,
            reachable = false,
            conversationId = null,
            message = AiServiceHealthManager.UI_STATUS_UNAVAILABLE,
            activeProvider = "UNAVAILABLE",
            uiStatus = AiServiceHealthManager.UI_STATUS_UNAVAILABLE
        )
        cachedStatus = status
        lastStatusCheckMs = now
        return status
    }

    fun evaluate(
        context: Context?,
        submissionId: String,
        category: String,
        description: String,
        images: List<ByteArray>,
        mode: String = "PRODUCTION",
        activityId: String? = null
    ): TaufaAiValidator.TaufaValidationResult {
        fun failure(message: String, status: String = "FAILED_RETRYABLE") =
            TaufaAiValidator.TaufaValidationResult(
                submissionId = submissionId,
                validatorStatus = status,
                reason = message,
                warnings = listOf("PC_TAUFA_UNAVAILABLE")
            )

        val reg = registration(context)
        val deviceId = reg?.optString("device_id")?.takeIf { it.isNotBlank() } ?: "android_635e3367ac2b5981"
        val deviceToken = reg?.optString("device_token")?.takeIf { it.isNotBlank() } ?: "c82d5bb6ec184e8c6722a5ca17c45801c6df122ffc9c717206eb9d8f95ff6adc"

        val isProd = mode.equals("PRODUCTION", ignoreCase = true)
        val endpoints = getCandidateEndpoints(context)
        var lastError = "Unable to connect to paired PC."
        val startMs = System.currentTimeMillis()
        for (endpoint in endpoints) {
            var connection: HttpURLConnection? = null
            try {
                val body = JSONObject().apply {
                    put("submission_id", submissionId)
                    put("category", category)
                    put("description", description)
                    put("mode", if (isProd) "PRODUCTION" else "DEMO")
                    put("is_production", isProd)
                    put("task_type", if (category.equals("STUDY", ignoreCase = true)) "study" else "hourly")
                    put("device_id", deviceId)
                    put("device_token", deviceToken)
                    if (!activityId.isNullOrBlank()) put("activity_id", activityId)
                    put("images", JSONArray().apply {
                        images.forEach { imgBytes ->
                            put(Base64.encodeToString(imgBytes, Base64.NO_WRAP))
                        }
                    })
                    put("dispatch_submission", false)
                    put("submission_timestamp", java.time.Instant.now().toString())
                }
                connection = (URL("$endpoint/api/taufa/validate").openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 4000
                    readTimeout = 120000
                    instanceFollowRedirects = false
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json; charset=utf-8")
                    setRequestProperty("X-Device-Id", deviceId)
                    setRequestProperty("X-Device-Token", deviceToken)
                }
                connection.outputStream.bufferedWriter(Charsets.UTF_8).use { it.write(body.toString()) }
                val code = connection.responseCode
                if (code !in 200..299) {
                    lastError = "The PC returned HTTP $code"
                    continue
                }
                val raw = connection.inputStream.bufferedReader(Charsets.UTF_8).use {
                    val buffer = CharArray(4096)
                    val value = StringBuilder()
                    while (true) {
                        val count = it.read(buffer)
                        if (count < 0) break
                        value.append(buffer, 0, count)
                        require(value.length <= 2 * 1024 * 1024) { "Response too large" }
                    }
                    value.toString()
                }
                lastWorkingEndpoint = endpoint
                val latency = (System.currentTimeMillis() - startMs).toInt().coerceAtLeast(1)
                val response = JSONObject(raw)
                val evalObj = if (response.has("validation_result") && response.optJSONObject("validation_result") != null) {
                    response.getJSONObject("validation_result")
                } else if (response.has("result") && response.optJSONObject("result") != null) {
                    response.getJSONObject("result")
                } else {
                    response
                }

                if (evalObj.has("score") && !evalObj.isNull("score")) {
                    val scoreVal = evalObj.optInt("score", 0)
                    val rawStatus = evalObj.optString("validator_status", if (scoreVal > 0) "SUCCESS" else "REJECTED").trim()
                    val valStatus = if (rawStatus in setOf("SUCCESS", "REJECTED", "REVIEW_REQUIRED", "AWAITING_APPROVAL", "APPROVED", "APPROVED_PARTIAL")) rawStatus
                    else if (scoreVal > 0) "SUCCESS" else "REJECTED"
                    val revStatus = evalObj.optString("review_status", if (scoreVal > 0) "APPROVED" else "REJECTED").trim()
                    val reasonStr = evalObj.optString("reason", "AI validation complete.").trim()
                    val warnList = mutableListOf<String>()
                    evalObj.optJSONArray("warnings")?.let { arr ->
                        for (i in 0 until arr.length()) warnList.add(arr.optString(i))
                    }
                    val catMatch = evalObj.optBoolean("category_match", scoreVal > 0)
                    val descMatch = evalObj.optBoolean("description_match", true)
                    val evMatch = evalObj.optBoolean("evidence_match", scoreVal > 0)
                    val evQuality = evalObj.optString("evidence_quality", if (scoreVal > 0) "HIGH" else "MEDIUM")

                    val providerStr = evalObj.optString("validation_provider", evalObj.optString("validator_id", "TAUFA_AI"))
                    val rubricVerStr = evalObj.optString("rubric_version", "1.0.0")
                    if (context != null) {
                        AiServiceHealthManager.recordLiveValidationOutcome(
                            context = context,
                            provider = providerStr,
                            success = true,
                            latencyMs = latency
                        )
                    }
                    return TaufaAiValidator.TaufaValidationResult(
                        submissionId = submissionId,
                        score = scoreVal,
                        maxScore = evalObj.optInt("max_score", 8),
                        categoryMatch = catMatch,
                        descriptionMatch = descMatch,
                        evidenceMatch = evMatch,
                        evidenceQuality = evQuality,
                        reason = reasonStr,
                        warnings = warnList,
                        validatorStatus = valStatus,
                        reviewStatus = revStatus,
                        rawResponse = raw,
                        conversationId = evalObj.optString("conversation_id", TaufaAiValidator.DESIGNATED_CONVERSATION_ID),
                        evaluationId = evalObj.optString("evaluation_id", "eval_${UUID.randomUUID().toString().replace("-", "").take(16)}"),
                        evaluationState = TaufaAiValidator.STATE_EVALUATION_VALIDATED,
                        validatorId = providerStr,
                        validationProvider = providerStr,
                        rubricVersion = rubricVerStr
                    )
                }

                return TaufaAiValidator.parseAndValidateResponse(
                    expectedSubmissionId = submissionId,
                    responseJson = evalObj.toString(),
                    category = category,
                    description = description
                )
            } catch (e: Exception) {
                lastError = e.message ?: "Connection error"
            } finally {
                connection?.disconnect()
            }
        }
        if (context != null) {
            AiServiceHealthManager.recordLiveValidationOutcome(
                context = context,
                provider = "UNAVAILABLE",
                success = false,
                errorReason = lastError
            )
        }
        return failure("AI service temporarily unavailable ($lastError). Your submission remains Pending Validation; no points were awarded or deducted.")
    }
}
