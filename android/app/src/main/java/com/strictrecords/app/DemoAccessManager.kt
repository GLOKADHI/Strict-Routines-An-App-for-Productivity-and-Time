package com.strictrecords.app

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** Four-field configuration store, isolated from StrictRecordsLocalDb and sync queues. */
object DemoAccessManager {
    private const val PREFS = "demo_access_state"
    private val lease = DemoAccessLease()
    private val busy = AtomicBoolean(false)
    private val worker by lazy { Executors.newSingleThreadExecutor() }
    private val main by lazy { Handler(Looper.getMainLooper()) }
    private var initialized = false
    @Volatile private var verifiedScope = ""
    @Volatile private var latest = JSONObject()
    @Volatile private var message = "Verify availability with the shared demo-access service."
    @Volatile private var wasActive = false
    @Volatile private var checkedAtElapsed = 0L
    @Volatile private var boundaryAtElapsed = 0L
    private var expiryTask: Runnable? = null

    private data class Binding(val origin: String, val account: String, val device: String, val token: String) {
        val scope: String get() = "$origin|$account|$device|${LedgerSyncRules.sha256(token)}"
    }
    private fun binding(context: Context): Binding? = runCatching {
        val db = StrictRecordsLocalDb.getInstance(context)
        // A read-only lookup avoids legacy registration-recovery side effects.
        // Only the authority's credential check can authenticate this binding.
        val reg = db.readableDatabase.rawQuery("SELECT account_id,device_id,device_token,server_endpoint FROM device_registration WHERE status='REGISTERED' LIMIT 1", null).use {
            if (!it.moveToFirst()) return null
            listOf(it.getString(0), it.getString(1), it.getString(2), it.getString(3))
        }
        // Retain an explicitly configured cloud authority; never switch authorities
        // on a network failure and accidentally grant a second weekly allowance.
        val configuredOrigin = db.getConfigValue("points_cloud_origin", "").trim().trimEnd('/')
        val origin = if (configuredOrigin.isNotBlank()) {
            configuredOrigin.takeIf { PointsCloudSync.validOrigin(it) } ?: return null
        } else DemoAccessEndpoint.fromPairedServer(reg[3]) ?: return null
        Binding(origin, reg[0], reg[1], reg[2])
            .takeIf { it.account.isNotBlank() && it.device.isNotBlank() && it.token.isNotBlank() }
    }.getOrNull()

    @Synchronized private fun initialize(context: Context) {
        if (initialized) return
        initialized = true
        // Process death/reboot cannot revive a grant from wall-clock preferences.
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (prefs.getString("status", "") == "ACTIVE") prefs.edit().putString("status", "EXPIRED").commit()
    }

    fun effectivePoints(context: Context?, actualPoints: Int): Int {
        if (context == null) return actualPoints
        initialize(context)
        val current = binding(context)
        val remaining = lease.remaining(current?.scope.orEmpty(), SystemClock.elapsedRealtime())
        if (remaining == 0L && wasActive) expire(context)
        return if (remaining > 0L) DemoAccessLease.ACCESS_POINTS else actualPoints
    }

    internal fun historyFreeBoundary(): Boolean = wasActive ||
        (boundaryAtElapsed > 0 && SystemClock.elapsedRealtime() - boundaryAtElapsed in 0..2000L)

    fun view(context: Context): JSONObject {
        initialize(context)
        val current = binding(context)
        val remaining = lease.remaining(current?.scope.orEmpty(), SystemClock.elapsedRealtime())
        if (remaining == 0L && wasActive) expire(context)
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val sameAccount = current != null && prefs.getString("authenticated_user_reference", "") == current.account
        return JSONObject().apply {
            put("active", remaining > 0L); put("remaining_ms", remaining)
            put("effective_access_points", if (remaining > 0L) 81 else JSONObject.NULL)
            put("configured", current != null); put("busy", busy.get())
            put("available", current != null && verifiedScope == current.scope && latest.optBoolean("available") && !busy.get() && remaining == 0L)
            put("last_successful_activation_ms", if (sameAccount) prefs.getLong("last_successful_activation_ms", 0) else 0)
            put("next_eligible_ms", if (sameAccount) prefs.getLong("last_successful_activation_ms", 0).takeIf { it > 0 }?.plus(DemoAccessLease.COOLDOWN_MS) ?: 0 else 0)
            val next = latest.optLong("next_eligible_ms")
            val elapsed = SystemClock.elapsedRealtime() - checkedAtElapsed
            put("cooldown_remaining_ms", if (current != null && verifiedScope == current.scope && next > 0 && elapsed >= 0)
                (next - latest.optLong("server_now_ms") - elapsed).coerceAtLeast(0) else JSONObject.NULL)
            put("message", if (current == null) "Pair this device with your Windows PC to verify demo availability. Cooldown cannot be verified offline." else message)
        }
    }

    fun refresh(context: Context, finished: (Boolean) -> Unit = {}) = request(context, false, finished)
    fun activate(context: Context, finished: (Boolean) -> Unit) = request(context, true, finished)

    private fun request(context: Context, activate: Boolean, finished: (Boolean) -> Unit) {
        val app = context.applicationContext
        initialize(app)
        val current = binding(app)
        if (current == null || !busy.compareAndSet(false, true)) { finished(false); return }
        worker.execute {
            var success = false
            var conn: HttpURLConnection? = null
            val started = SystemClock.elapsedRealtime()
            try {
                conn = URL(current.origin + if (activate) "/v1/demo-access/activate" else "/v1/demo-access/state").openConnection() as HttpURLConnection
                conn.instanceFollowRedirects = false; conn.connectTimeout = 5000; conn.readTimeout = 10000
                conn.requestMethod = if (activate) "POST" else "GET"
                conn.setRequestProperty("Authorization", "Bearer ${current.token}")
                conn.setRequestProperty("X-Device-Id", current.device)
                conn.setRequestProperty("Content-Type", "application/json")
                if (activate) { conn.doOutput = true; conn.outputStream.use { it.write("{}".toByteArray()) } }
                val code = conn.responseCode
                val raw = (if (code in 200..299) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { reader ->
                    val chars = CharArray(2048); val result = StringBuilder()
                    while (true) { val n = reader.read(chars); if (n < 0) break; result.append(chars, 0, n); require(result.length <= 65536) }
                    result.toString()
                }.orEmpty()
                val response = JSONObject(raw)
                require(binding(app)?.scope == current.scope) { "Account changed" }
                val state = response.optJSONObject("demo_access")
                if (code == 200 || code == 409) {
                    require(state != null && state.getString("account_id") == current.account)
                    val last = if (state.isNull("last_successful_activation_ms")) 0L else state.getLong("last_successful_activation_ms")
                    val expiry = if (state.isNull("expires_at_ms")) 0L else state.getLong("expires_at_ms")
                    require(last == 0L || expiry - last == DemoAccessLease.DURATION_MS)
                    val saved = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                        .putLong("last_successful_activation_ms", last).putLong("expires_at_ms", expiry)
                        .putString("status", state.getString("status"))
                        .putString("authenticated_user_reference", current.account).commit()
                    require(saved) { "Demo state could not be retained" }
                    latest = state; verifiedScope = current.scope; checkedAtElapsed = SystemClock.elapsedRealtime()
                    if (state.getString("status") != "ACTIVE" && wasActive) expire(app)
                    message = if (state.optBoolean("available")) "Available once every seven full days." else "Weekly activation consumed. The next eligible time is shown below."
                    if (activate && code == 200 && response.optBoolean("success")) {
                        require(state.optInt("effective_access_points") == 81 && state.getString("status") == "ACTIVE")
                        success = lease.accept(current.scope, last, expiry, state.getLong("server_now_ms"), started, SystemClock.elapsedRealtime())
                        require(success) { "Activation receipt arrived too late" }
                        wasActive = true
                        message = "Demo access active. Real points and history are unchanged."
                        main.post { arm(app); notifyPolicy(app) }
                    } else success = !activate
                } else error("Demo authority rejected request")
            } catch (_: Exception) {
                verifiedScope = ""
                message = "Availability could not be verified. No additional demo access was granted; reconnect and refresh."
            } finally {
                conn?.disconnect(); busy.set(false)
                main.post { finished(success) }
            }
        }
    }

    private fun arm(context: Context) {
        expiryTask?.let { main.removeCallbacks(it) }
        val remaining = view(context).optLong("remaining_ms")
        expiryTask = Runnable { effectivePoints(context, 0); if (wasActive) arm(context) }
        main.postDelayed(expiryTask!!, remaining.coerceAtLeast(1))
        val alarm = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val intent = PendingIntent.getBroadcast(context, 7382, Intent(context, DemoAccessExpiryReceiver::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        runCatching {
            val at = SystemClock.elapsedRealtime() + remaining
            if (Build.VERSION.SDK_INT < 31 || alarm.canScheduleExactAlarms()) alarm.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, intent)
            else alarm.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, intent)
        }
    }

    @Synchronized fun expire(context: Context) {
        lease.clear()
        if (!wasActive) return
        wasActive = false
        boundaryAtElapsed = SystemClock.elapsedRealtime()
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("status", "EXPIRED").commit()
        message = "Demo access expired. Your original points balance and restrictions have been restored."
        main.post { notifyPolicy(context.applicationContext) }
    }

    private fun notifyPolicy(context: Context) {
        // Existing target/financial/foreground checks still own every action.
        UsageAccessMonitor.onScreenUnavailable() // Exclude an uncertain boundary from usage evidence.
        BackgroundMusicRestrictionManager.onRestrictionStateChanged(context, "demo_access_boundary")
        StrictBotOverlayController.prepareDemoAccessRecheck(context)
        StrictAccessibilityService.activeInstance?.onDemoAccessChanged()
        StrictEnforcementService.activeInstance?.onDemoAccessChanged()
        StrictWidgetUpdates.request(context, "demo_access")
    }
}

class DemoAccessExpiryReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) { DemoAccessManager.effectivePoints(context, 0) }
}
