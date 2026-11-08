package com.strictrecords.app

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.ConcurrentHashMap

/** Authenticated, account-scoped routine cache; the PC is the only routine authority. */
object GymRoutineClient {
    private val refreshing = AtomicBoolean(false)
    private val automaticAttempts = ConcurrentHashMap<String, Long>()
    data class Query(val month: String, val filter: String = "THIS_MONTH", val startDate: String = "", val endDate: String = "") {
        fun suffix(): String {
            require(GymRoutineDates.isDate("$month-01")) { "Invalid calendar month" }
            require(filter in setOf("THIS_WEEK", "THIS_MONTH", "LAST_MONTH", "CUSTOM")) { "Invalid statistics filter" }
            val params = linkedMapOf("month" to month, "filter" to filter)
            if (filter == "CUSTOM") {
                require(GymRoutineDates.isDate(startDate) && GymRoutineDates.isDate(endDate) &&
                    startDate >= GymRoutineDates.LAUNCH_DATE && endDate >= startDate) { "Choose a valid tracked date range" }
                require(GymRoutineDates.calendarDaysBetween(startDate, endDate) <= 365L) { "Choose at most 366 calendar days" }
                params["start_date"] = startDate; params["end_date"] = endDate
            }
            return params.entries.joinToString("&", "?") { (key, value) -> "$key=${URLEncoder.encode(value, "UTF-8")}" }
        }
    }
    enum class ConnectionState {
        PAIRED_ONLINE,
        NOT_PAIRED,
        PC_UNAVAILABLE,
        AUTH_EXPIRED,
        SYNC_FAILED
    }
    data class Result(val success: Boolean, val message: String, val offline: Boolean = false, val conflict: Boolean = false, val authExpired: Boolean = false)

    fun getConnectionState(db: StrictRecordsLocalDb, lastResult: Result? = null): ConnectionState {
        if (!hasRegistration(db)) return ConnectionState.NOT_PAIRED
        if (lastResult != null) {
            if (lastResult.authExpired) return ConnectionState.AUTH_EXPIRED
            if (lastResult.offline) return ConnectionState.PC_UNAVAILABLE
            if (!lastResult.success) return ConnectionState.SYNC_FAILED
            return ConnectionState.PAIRED_ONLINE
        }
        val pairingStatus = db.getConfigValue("pairing_status", "ONLINE")
        return if (pairingStatus == "ONLINE") ConnectionState.PAIRED_ONLINE else ConnectionState.PC_UNAVAILABLE
    }

    private fun account(db: StrictRecordsLocalDb): String = db.getDeviceRegistration()?.optString("account_id").orEmpty()
        .ifBlank { db.getConfigValue("account_id", "user_lok") }
    // Routine-cache writes need no points/tasks widget invalidation.
    private fun prefix(db: StrictRecordsLocalDb) = "alternate_gym_routine_${account(db)}_"
    private fun json(value: String): JSONObject = runCatching { JSONObject(value) }.getOrDefault(JSONObject())
    private fun queryKey(db: StrictRecordsLocalDb, query: Query): String = prefix(db) + "query_" +
        MessageDigest.getInstance("SHA-256").digest(query.suffix().toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }

    @Synchronized fun routine(db: StrictRecordsLocalDb): JSONObject = json(db.getConfigValue(prefix(db) + "state", "{}"))
    @Synchronized fun cached(db: StrictRecordsLocalDb, query: Query): JSONObject =
        json(db.getConfigValue(queryKey(db, query), "{}"))
    @Synchronized fun pending(db: StrictRecordsLocalDb): JSONObject = json(db.getConfigValue(prefix(db) + "pending", "{}"))
    fun hasRegistration(db: StrictRecordsLocalDb): Boolean = db.getDeviceRegistration()?.let {
        it.optString("account_id").isNotBlank() && it.optString("device_id").isNotBlank() &&
            it.optString("device_token").isNotBlank() && it.optString("server_endpoint").isNotBlank()
    } == true

    private fun persist(db: StrictRecordsLocalDb, key: String, value: String) {
        db.setConfigValue(key, value)
        check(db.getConfigValue(key, "") == value) { "The phone could not persist this routine change" }
    }

    /** One outstanding request keeps its original request ID and expected server revision. */
    @Synchronized fun queue(db: StrictRecordsLocalDb, action: String, fields: JSONObject = JSONObject()): Result = try {
        check(!refreshing.get()) { "Wait for the current PC refresh before confirming another change" }
        check(hasRegistration(db)) { "Pair this phone with the PC in Device Center before configuring the routine" }
        check(pending(db).optString("request_id").isBlank()) { "Confirm or discard the outstanding request before making another change" }
        require(action in setOf("SETUP", "PAUSE", "RESUME")) { "Invalid routine action" }
        val saved = routine(db)
        val configured = saved.optString("routine_id").let { it.isNotBlank() && it != "null" }
        when (action) {
            "SETUP" -> check(!configured) { "A confirmed routine already exists. Refresh its current state" }
            "PAUSE" -> {
                check(configured) { "Confirm the initial setup first" }
                val category = fields.optString("reason_category")
                require(category in setOf("ILLNESS", "INJURY", "TRAVEL", "WORK_STUDY", "PERSONAL", "OTHER")) { "Select a pause reason" }
                require(category != "OTHER" || fields.optString("note").isNotBlank()) { "Add a short reason for Other" }
            }
            "RESUME" -> {
                val date = fields.optString("reactivation_date")
                require(configured && GymRoutineDates.isDate(date) && date >= maxOf(
                    GymRoutineDates.today(db.authoritativeNowMs()), GymRoutineDates.LAUNCH_DATE)) { "Choose today or a later tracked date" }
            }
        }
        val body = JSONObject().put("action", action).put("account_id", account(db))
            .put("request_id", UUID.randomUUID().toString()).put("expected_revision", saved.optLong("revision", 0L))
            .put("timezone", GymRoutineDates.TIMEZONE)
        if (action == "SETUP") body.put("start_date", GymRoutineDates.LAUNCH_DATE)
        if (action == "PAUSE") {
            body.put("reason_category", fields.getString("reason_category"))
                .put("note", fields.optString("note").trim().take(500))
        }
        if (action == "RESUME") body.put("reactivation_date", fields.getString("reactivation_date"))
        persist(db, prefix(db) + "pending", body.toString())
        Result(true, "Request saved on this phone. Waiting for PC confirmation")
    } catch (error: Exception) { Result(false, error.message ?: "The request could not be saved") }

    /** Discarding an unacknowledged request never changes the authoritative routine. */
    @Synchronized fun discardPending(db: StrictRecordsLocalDb): Result = try {
        check(!refreshing.get()) { "Wait for the current PC refresh before discarding a request" }
        persist(db, prefix(db) + "pending", "")
        Result(true, "Unconfirmed request discarded. Refresh to read the PC's current routine")
    } catch (error: Exception) { Result(false, error.message ?: "Could not discard the request") }

    private fun request(registration: JSONObject, suffix: String, body: JSONObject? = null): JSONObject {
        val endpoint = registration.optString("server_endpoint").trimEnd('/')
        require(endpoint.isNotBlank()) { "The registered PC address is unavailable" }
        val url = URL(endpoint + "/api/gym/routine" + suffix)
        require(url.protocol == "http" || url.protocol == "https") { "The registered PC address is invalid" }
        val connection = url.openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 4000; connection.readTimeout = 5000
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("X-Device-Id", registration.getString("device_id"))
            connection.setRequestProperty("X-Device-Token", registration.getString("device_token"))
            connection.setRequestProperty("Content-Type", "application/json")
            if (body != null) {
                connection.requestMethod = "POST"; connection.doOutput = true
                connection.outputStream.bufferedWriter(Charsets.UTF_8).use { it.write(body.toString()) }
            }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val raw = stream?.bufferedReader(Charsets.UTF_8)?.use { reader ->
                val out = StringBuilder(); val buffer = CharArray(4096)
                while (true) {
                    val count = reader.read(buffer); if (count < 0) break
                    check(out.length + count <= 2_000_000) { "PC response exceeded the safe size limit" }
                    out.append(buffer, 0, count)
                }
                out.toString()
            } ?: "{}"
            return json(raw).put("_http_status", status).apply {
                if (status !in 200..299) put("success", false)
                if (!has("success")) put("success", false)
            }
        } finally { connection.disconnect() }
    }

    @Synchronized private fun accept(db: StrictRecordsLocalDb, response: JSONObject, query: Query? = null) {
        check(response.optBoolean("success")) { response.optString("error", "PC did not confirm this request") }
        val incoming = response.optJSONObject("routine") ?: error("PC did not return a routine snapshot")
        val previous = routine(db)
        check(GymRoutineSyncRules.mayReplace(account(db), incoming.optString("account_id"), previous.optLong("revision", 0),
            incoming.optLong("revision", -1))) { "A stale or mismatched PC snapshot was ignored. Refresh again" }
        persist(db, prefix(db) + "state", incoming.toString())
        if (query != null) {
            val copy = JSONObject(response.toString()).put("_received_at_ms", System.currentTimeMillis())
            val key = queryKey(db, query)
            persist(db, key, copy.toString())
            val indexKey = prefix(db) + "cache_index"
            val old = runCatching { org.json.JSONArray(db.getConfigValue(indexKey, "[]")) }.getOrDefault(org.json.JSONArray())
            val keys = (0 until old.length()).map { old.optString(it) }.filter { it != key && it.startsWith(prefix(db) + "query_") } + key
            keys.dropLast(12).forEach { persist(db, it, "") }
            persist(db, indexKey, org.json.JSONArray(keys.takeLast(12)).toString())
        }
    }

    /** Call on a worker thread only; no repeating polling, no access or score grants. */
    fun refresh(db: StrictRecordsLocalDb, query: Query, automatic: Boolean = false): Result {
        if (!hasRegistration(db)) return Result(false, "Pair this phone with your PC to manage the routine.", offline = true)
        val registration = db.getDeviceRegistration()?.let { JSONObject(it.toString()) }
            ?: return Result(false, "PC registration changed. Refresh after pairing completes")
        val requestAccount = registration.optString("account_id")
        if (automatic) {
            val key = prefix(db) + query.suffix(); val now = android.os.SystemClock.elapsedRealtime()
            val last = automaticAttempts[key]
            if (!GymRoutineSyncRules.automaticRefreshDue(now, last)) return Result(true, "An automatic check was attempted recently. Use Refresh for an immediate PC check")
            automaticAttempts[key] = now
        }
        if (!refreshing.compareAndSet(false, true)) return Result(false, "A routine refresh is already in progress. Try again shortly")
        var mutationError: Result? = null
        try {
            val queued = pending(db)
            if (queued.optString("request_id").isNotBlank() && !queued.optBoolean("_conflict")) {
                check(queued.optString("account_id") == requestAccount) { "Pending request belongs to a different account" }
                val response = request(registration, "", queued)
                if (response.optBoolean("success")) {
                    val incoming = response.optJSONObject("routine") ?: error("PC did not return a routine snapshot")
                    val receipt = response.optJSONObject("mutation") ?: error("PC did not return a mutation acknowledgment")
                    check(GymRoutineSyncRules.mayAcknowledge(requestAccount, incoming.optString("account_id"), queued.optString("request_id"), receipt.optString("request_id"), receipt.optBoolean("committed"))) { "PC did not acknowledge this exact request" }
                    check(incoming.optString("account_id") == requestAccount && account(db) == requestAccount) { "The account changed during confirmation" }
                    // An acknowledged idempotent replay can contain an older
                    // snapshot. Clear only its exact pending command, retain
                    // the newer cache, then GET the current authoritative state.
                    if (incoming.optLong("revision", 0L) >= routine(db).optLong("revision", 0L)) accept(db, response)
                    // An idempotent retry uses the exact same durable request.
                    if (pending(db).optString("request_id") == queued.optString("request_id"))
                        persist(db, prefix(db) + "pending", "")
                } else {
                    val status = response.optInt("_http_status")
                    val conflict = status in setOf(400, 401, 403, 409, 422) || response.optString("code") in
                        setOf("REVISION_CONFLICT", "INVALID_STATE", "INVALID_DATE", "UNAUTHORIZED", "ACCOUNT_MISMATCH")
                    val message = response.optString("error").ifBlank { "The PC did not accept the routine change" }
                    if (conflict && account(db) == requestAccount && pending(db).optString("request_id") == queued.optString("request_id"))
                        persist(db, prefix(db) + "pending", queued.put("_conflict", true).put("_error", message).toString())
                    mutationError = Result(false, message, conflict = conflict)
                }
            }
            val response = request(registration, query.suffix())
            if (!response.optBoolean("success")) {
                val status = response.optInt("_http_status")
                val isAuth = status in setOf(401, 403) || response.optString("code") == "UNAUTHORIZED"
                if (isAuth) return Result(false, "Reconnect to your PC to renew the pairing.", authExpired = true)
                return mutationError ?: Result(false,
                    response.optString("error").ifBlank { "Connected, but the latest routine could not be refreshed." })
            }
            accept(db, response, query)
            return mutationError ?: if (pending(db).optBoolean("_conflict")) Result(false,
                pending(db).optString("_error", "A routine change was rejected. Review the refreshed state"), conflict = true)
            else Result(true, "Confirmed routine and recorded gym data refreshed from the PC")
        } catch (_: Exception) {
            val queued = pending(db).optString("request_id").isNotBlank()
            return Result(false, if (queued) "This phone is paired, but the PC is currently unavailable. The request remains pending."
                else "This phone is paired, but the PC is currently unavailable.", offline = true)
        } finally { refreshing.set(false) }
    }
}
