package com.strictrecords.app

import android.content.Context
import android.content.pm.PackageManager
import android.Manifest
import android.os.Build
import android.speech.SpeechRecognizer
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.net.URLEncoder
import java.util.Locale

/** Only this paired server receives device credentials. Redirects are never followed. */
object StrictBotNetworkPolicy {
    fun normalizeEndpoint(raw: String): String? = runCatching {
        val uri = URI(raw.trim())
        if (uri.scheme !in listOf("http", "https") || uri.host.isNullOrBlank() ||
            uri.rawUserInfo != null || uri.rawQuery != null || uri.rawFragment != null ||
            uri.rawPath !in listOf(null, "", "/") || uri.port !in -1..65535 || uri.port == 0
        ) return null
        // A physical Android phone's loopback is never the paired Windows computer.
        if (uri.host.lowercase(Locale.US) in setOf("localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0")) return null
        raw.trim().trimEnd('/')
    }.getOrNull()

    fun failureMessage(status: Int): String = when (status) {
        401, 403 -> "The PC rejected this device's session. Reconnect in Device Center; no change was confirmed."
        404 -> "The running PC app does not support StrictBot chat. Start the updated Windows app."
        409 -> "The operation conflicts with the current state. Refresh and review the latest proposal."
        429 -> "The PC is busy. Wait briefly, then retry the same request."
        in 500..599 -> "The PC service failed to process this request. Retry to check its recorded result."
        else -> "The PC did not accept this request (HTTP $status). No completed result was received."
    }
}

class StrictBotClient(private val context: Context, private val db: StrictRecordsLocalDb) {
    data class Result(val data: JSONObject? = null, val error: String = "", val offline: Boolean = false, val retryable: Boolean = false) {
        val success: Boolean get() = data?.optBoolean("success", false) == true
    }

    fun isRegistered(): Boolean {
        val reg = db.getDeviceRegistration() ?: return false
        return reg.optString("device_id").isNotBlank() && reg.optString("device_token").isNotBlank() &&
            StrictBotNetworkPolicy.normalizeEndpoint(reg.optString("server_endpoint")) != null
    }

    fun accountScope(): String = registrationScope(db.getDeviceRegistration())

    private fun registrationScope(registration: JSONObject?): String = registration?.let {
        "${it.optString("account_id")}:${it.optString("device_id")}:${it.optString("registered_at")}"
    } ?: "local-unpaired"

    fun chat(message: String, conversationId: String, requestId: String, expectedScope: String): Result = request(
        "/api/strictbot/chat", JSONObject().apply {
            put("message", message)
            put("request_id", requestId)
            if (conversationId.isNotBlank()) put("conversation_id", conversationId)
            permissionSnapshot(message)?.let { put("client_permission_state", it) }
        }, expectedScope = expectedScope
    )

    fun action(action: String, inputs: JSONObject, requestId: String, expectedScope: String): Result = request(
        "/api/strictbot/action", JSONObject().apply {
            put("action", action)
            put("inputs", inputs)
            put("request_id", requestId)
            put("confirmation", true)
        }, expectedScope = expectedScope
    )

    fun history(conversationId: String, expectedScope: String): Result {
        val query = if (conversationId.isBlank()) "" else "?conversation_id=" + URLEncoder.encode(conversationId, "UTF-8")
        return request("/api/strictbot/history$query", expectedScope = expectedScope)
    }

    fun context(topic: String = "next", expectedScope: String): Result = request(
        "/api/strictbot/context?topic=" + URLEncoder.encode(topic, "UTF-8"), permissionState = permissionSnapshot(topic), expectedScope = expectedScope
    )

    private fun request(path: String, body: JSONObject? = null, permissionState: JSONObject? = null, expectedScope: String): Result {
        val registration = db.getDeviceRegistration()
            ?: return Result(error = "This phone is not paired. Local records are available; connect a PC to use authorized actions.", offline = true)
        if (registrationScope(registration) != expectedScope)
            return Result(error = "The paired account changed before the request was sent. Reopen StrictBot to use the current account.")
        val endpoint = StrictBotNetworkPolicy.normalizeEndpoint(registration.optString("server_endpoint"))
            ?: return Result(error = "The saved PC address is invalid. Reconnect in Device Center.", offline = true)
        val deviceId = registration.optString("device_id")
        val token = registration.optString("device_token")
        if (deviceId.isBlank() || token.isBlank()) return Result(error = "The paired device credential is missing. Reconnect in Device Center.", offline = true)
        var connection: HttpURLConnection? = null
        return try {
            connection = (URL(endpoint + path).openConnection() as HttpURLConnection).apply {
                requestMethod = if (body == null) "GET" else "POST"
                connectTimeout = 4500
                readTimeout = 15000
                instanceFollowRedirects = false
                setRequestProperty("Accept", "application/json")
                setRequestProperty("X-Device-Id", deviceId)
                setRequestProperty("X-Device-Token", token)
                permissionState?.let { setRequestProperty("X-Client-Permission-State", it.toString()) }
                if (body != null) {
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json; charset=utf-8")
                }
            }
            body?.apply { put("device_id", deviceId); put("device_token", token) }?.let { payload ->
                connection.outputStream.bufferedWriter(Charsets.UTF_8).use { it.write(payload.toString()) }
            }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val raw = stream?.bufferedReader(Charsets.UTF_8)?.use { reader ->
                val buffer = CharArray(4096)
                val value = StringBuilder()
                while (value.length < 1024 * 1024) {
                    val count = reader.read(buffer)
                    if (count < 0) break
                    value.append(buffer, 0, count)
                }
                if (value.length >= 1024 * 1024) throw IllegalStateException("Response too large")
                value.toString()
            }.orEmpty()
            val json = runCatching { JSONObject(raw) }.getOrNull()
            if (code !in 200..299) {
                Result(error = if (code in setOf(401, 403, 404)) StrictBotNetworkPolicy.failureMessage(code)
                    else json?.optString("error", "")?.takeIf { it.isNotBlank() } ?: StrictBotNetworkPolicy.failureMessage(code),
                    retryable = (code >= 500 || code == 429) && json?.optString("state") != "FAILED")
            } else if (json == null) {
                Result(error = "The PC returned an unreadable response. No completed result was received.", retryable = true)
            } else if (!json.optBoolean("success", false)) {
                Result(data = json, error = json.optString("error").ifBlank { "The PC did not confirm this operation." })
            } else Result(data = json)
        } catch (_: Exception) {
            Result(error = "The paired PC is unreachable or its response timed out. Local records may be stale; retry to check the result.", offline = true, retryable = true)
        } finally {
            connection?.disconnect()
        }
    }

    /** Send capability facts only when they are relevant to the request, without evidence or tokens. */
    fun permissionSnapshot(message: String): JSONObject? {
        val text = message.lowercase(Locale.US)
        val selected = linkedSetOf<PermissionCapability>()
        when {
            "permission" in text || "capability" in text -> selected.addAll(PermissionCapability.values())
            "wake" in text -> selected.addAll(listOf(PermissionCapability.CAMERA, PermissionCapability.BLUETOOTH, PermissionCapability.NOTIFICATIONS, PermissionCapability.EXACT_ALARM))
            "camera" in text || "photo" in text -> selected.add(PermissionCapability.CAMERA)
            "bluetooth" in text || "earbud" in text || "audio" in text -> selected.add(PermissionCapability.BLUETOOTH)
            "notification" in text || "alarm" in text -> selected.addAll(listOf(PermissionCapability.NOTIFICATIONS, PermissionCapability.EXACT_ALARM))
            "connect" in text || "sync" in text || "wifi" in text || "device" in text -> selected.add(PermissionCapability.LOCAL_NETWORK_SYNC)
            "block" in text || "restriction" in text || "music" in text || "spotify" in text -> selected.addAll(listOf(PermissionCapability.USAGE_STATS, PermissionCapability.OVERLAY, PermissionCapability.ACCESSIBILITY, PermissionCapability.NOTIFICATION_LISTENER))
        }
        if (selected.isEmpty() && "voice" !in text && "microphone" !in text) return null
        return JSONObject().apply {
            put("source", "ANDROID_RUNTIME")
            put("checked_at", db.nowIso())
            for (capability in selected) put(capability.name, JSONObject().apply {
                put("status", DisciplinePermissionManager.evaluateCapabilityStatus(context, capability).name)
                put("required", capability.isRequired)
                put("enables", capability.displayName)
                if (capability == PermissionCapability.USAGE_STATS) {
                    put("usage_access_granted", HardwareDeviceVerifier.hasUsageStatsPermission(context))
                    put("explicit_monitoring_consent", TargetAppMonitoringPolicy.hasUsageConsent(context))
                    put("selected_target_ids", JSONArray(TargetAppMonitoringPolicy.selectedTargetIds(context).sorted()))
                    put("monitoring_enabled", TargetAppMonitoringPolicy.canReadUsage(context))
                }
            })
            if ("voice" in text || "microphone" in text || "permission" in text) {
                put("VOICE_INPUT", JSONObject().apply {
                    put("supported", SpeechRecognizer.isRecognitionAvailable(context))
                    put("microphone_granted", Build.VERSION.SDK_INT < Build.VERSION_CODES.M || context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED)
                    put("required", false)
                })
            }
        }
    }

    fun localReply(message: String): String {
        val text = message.lowercase(Locale.US)
        val prefix = "Local read-only snapshot (PC state unavailable; last sync: ${db.getConfigValue("last_sync_time", "never")}). "
        return try {
            val date = db.nowDate()
            when {
                listOf("travel", "adjust", "reschedule", "change", "approve", "unlock", "grant").any { it in text } ->
                    prefix + "Schedule and policy changes require the authenticated PC service. Reconnect to prepare a proposal and review its notice deadline. Nothing was changed."
                "permission" in text || "microphone" in text || "voice" in text -> {
                    val permissions = permissionSnapshot(message) ?: JSONObject()
                    prefix + "Phone capability checks: " + permissions.keys().asSequence().filter { it !in setOf("source", "checked_at") }
                        .joinToString("; ") { key -> "$key: ${permissions.optJSONObject(key)?.optString("status")?.takeIf { it.isNotBlank() } ?: permissions.optJSONObject(key)?.toString().orEmpty()}" } + ". Open Permissions to repair the relevant setting. Voice input is optional; text remains available."
                }
                "wake" in text -> {
                    val wake = db.getWakeUpFlowStatus(date, false)
                    prefix + "Saved wake-up checks: photo ${if (wake.optBoolean("step1_camera")) "saved" else "missing"}, Bluetooth route ${if (wake.optBoolean("step2_bluetooth")) "recorded" else "missing"}, full track ${if (wake.optBoolean("step3_motivation_track")) "recorded" else "incomplete"}. Status: ${wake.optString("status", "unknown")}. A local check is not a PC approval."
                }
                "score" in text || "point" in text || "reward" in text || "privilege" in text -> {
                    val summary = db.getTruthfulDailySummary(date, false)
                    prefix + (summary.optJSONObject("score_and_rewards")?.toString(2) ?: "Saved score: ${db.getScore(date, false)}. Current PC privilege state is unknown.")
                }
                "evidence" in text || "proof" in text || "ocr" in text || "image" in text || "photo" in text -> {
                    val lastSummary = db.getConfigValue("last_evidence_summary", "")
                    if (lastSummary.isNotBlank()) {
                        prefix + "Latest analyzed image proof:\n" + lastSummary
                    } else {
                        prefix + "No recent image analysis recorded. Open 'Test Image & Text Analysis' in Diagnostics or submit a task proof to analyze an image."
                    }
                }
                "next" in text || "schedule" in text || "task" in text || "window" in text -> {
                    val wake = db.getWakeUpFlowStatus(date, false)
                    val gate = db.getDailyRoutineGate(date)
                    if (!gate.optBoolean("active", false)) prefix +
                        if (!wake.optBoolean("all_completed")) "Wake-up verification is incomplete in local records. Open the wake-up task to complete the missing checks; hourly eligibility must be confirmed by the routine gate."
                        else "Wake-up checks are recorded locally, but routine eligibility is not confirmed. Last saved gate: ${gate.optString("state", "UNKNOWN")}. Reconnect to receive the authoritative verification result; hourly attendance remains inactive."
                    else {
                        val next = db.getDailyActivities(date).firstOrNull { it.status in listOf("PENDING", "OPEN", "ACTIVE", "UPCOMING", "SCHEDULED") }
                        prefix + if (next == null) "No pending activity is recorded locally. Reconnect to confirm the current schedule." else "Saved activity: ${next.title}; scheduled ${next.scheduledTime}; status ${next.status}. Open Tasks to review its evidence requirements and current window."
                    }
                }
                else -> prefix + db.getTruthfulDailySummary(date, false).optString("strictbot_summary", "No daily summary is recorded yet. Open Tasks to review saved records.")
            }
        } catch (_: Exception) {
            prefix + "Local records could not be read. Open Device Center to reconnect; no operation was performed."
        }
    }
}
