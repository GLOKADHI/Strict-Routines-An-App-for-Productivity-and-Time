package com.strictrecords.app

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.ContentValues
import android.content.Context
import android.os.SystemClock
import org.json.JSONObject
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicBoolean

/** Bounded UsageEvents reconciliation using the existing service, not a new permanent service.
 * No backend handler authorizes these penalties yet: evidence is stored with a
 * BLOCKED status, and neither score_events nor any sync queue is written here. */
object UsageAccessMonitor {
    const val INTERVAL_MS = 15_000L
    private const val MAX_RANGE_MS = 15 * 60_000L
    private const val MAX_GAP_MS = 45_000L
    private const val MAX_SCORE_AGE_MS = 120_000L
    private val timeline = RestrictedUsageTimeline()
    private var store: UsageMonitoringStore? = null
    private var owner = ""
    private var checkpoint = JSONObject()
    private var lastElapsed = 0L
    private var currentPackage: String? = null
    private var source = "UNAVAILABLE"
    private var failure = "Not reconciled yet."
    private var eligibilityKey = ""
    private var eligibilityValidFromMs = 0L
    private var interactive = false
    private var unlocked = false
    private var initialized = false
    private var lastSuccessMs = 0L
    private var confirmedScoreText = "Unavailable"
    private val reconciling = AtomicBoolean(false)
    private val idleGapPending = AtomicBoolean(false)
    private var lastAttemptElapsed = 0L
    @Volatile private var publishedForeground = Foreground(null, 0L, false)
    @Volatile private var publishedDiagnostics = "Usage monitoring has not reconciled yet."

    data class Foreground(val packageName: String?, val observedAtMs: Long, val confirmed: Boolean)

    fun foreground(now: Long = System.currentTimeMillis()): Foreground {
        val snapshot = publishedForeground
        return snapshot.copy(confirmed = !idleGapPending.get() && snapshot.confirmed &&
            now >= snapshot.observedAtMs && now - snapshot.observedAtMs <= MAX_GAP_MS)
    }

    fun onScreenUnavailable() {
        // Non-blocking: never wait for a UsageEvents query on the screen receiver/UI thread.
        idleGapPending.set(true)
        publishedForeground = Foreground(null, 0L, false)
    }

    private fun digest(text: String): String = MessageDigest.getInstance("SHA-256")
        .digest(text.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }

    fun reconcile(context: Context) {
        // Resume and the existing service can request the same batch. Skip overlapping
        // requests instead of queueing them or blocking foreground/diagnostics readers.
        if (!reconciling.compareAndSet(false, true)) return
        try {
            val elapsed = SystemClock.elapsedRealtime()
            if (!idleGapPending.get() && lastAttemptElapsed > 0L && elapsed - lastAttemptElapsed in 0L until 1000L) return
            lastAttemptElapsed = elapsed
            if (idleGapPending.getAndSet(false)) fail("Screen lock/non-interactive gap: fresh entry required.")
            if (!HardwareDeviceVerifier.isMonitoringScreenAvailable(context)) {
                fail("Device locked/non-interactive; usage query deferred until unlock.")
                return
            }
            reconcileInternal(context)
        } catch (error: Exception) {
            fail("Usage reconciliation failed: ${error.javaClass.simpleName}; interval excluded.")
        } finally {
            publishedForeground = Foreground(currentPackage, lastSuccessMs, initialized && currentPackage != null)
            publishedDiagnostics = buildDiagnosticText()
            reconciling.set(false)
        }
    }

    private fun reconcileInternal(context: Context) {
        val app = context.applicationContext
        val now = System.currentTimeMillis()
        if (!TargetAppMonitoringPolicy.hasUsageConsent(app)) {
            fail("Monitoring consent is off.")
            return
        }
        if (TargetAppMonitoringPolicy.selectedTargetIds(app).isEmpty()) {
            fail("Monitoring off: no target apps are selected.")
            return
        }
        if (!HardwareDeviceVerifier.hasUsageStatsPermission(app)) {
            fail("Usage Access unavailable; no usage-minute evidence can be accrued.")
            if (StrictAccessibilityService.activeInstance != null) source = "ACCESSIBILITY connected; Usage Access fallback unavailable"
            return
        }
        val db = StrictRecordsLocalDb.getInstance(app)
        val registration = db.getDeviceRegistration()
        val account = registration?.optString("account_id").orEmpty()
        val device = registration?.optString("device_id").orEmpty()
        val identity = "$account|$device"
        val storage = store ?: UsageMonitoringStore(app).also { store = it }
        if (!initialized || owner != identity) {
            checkpoint = storage.load(identity)
            owner = identity
            timeline.gap("Ordinary restart/account change: unobserved interval excluded; fresh entry required.")
            initialized = true
            lastElapsed = 0L
        }
        val elapsed = SystemClock.elapsedRealtime()
        val previousCursor = checkpoint.optLong("cursor_ms", 0L)
        val end = now - 1000L // Do not consume events on the still-changing query boundary.
        if (end <= previousCursor) {
            if (end < previousCursor - MAX_GAP_MS) fail("Clock moved backwards; reconciliation waits for a fresh time boundary.")
            return
        }
        val contiguous = lastElapsed > 0 && elapsed >= lastElapsed && elapsed - lastElapsed <= MAX_GAP_MS &&
            previousCursor > 0 && end - previousCursor <= MAX_GAP_MS &&
            kotlin.math.abs((end - previousCursor) - (elapsed - lastElapsed)) <= 2000L
        val range = UsageQueryRange.next(previousCursor, end, MAX_RANGE_MS) ?: return
        val start = range.startMs
        if (!contiguous) {
            timeline.gap("Restart, delayed execution or missing history: interval excluded.")
            interactive = false; unlocked = false; currentPackage = null
        }
        val today = db.nowDate()
        val timezone = db.scheduleTimezone()
        val revision = db.getConfigValue("apk_ledger_${account}_${today}_prod_revision", "0")
        val score = db.getScore(today, false)
        val confirmedEligibility = account.isNotBlank() && device.isNotBlank()
        val key = if (confirmedEligibility) "$account|$today|$timezone|$revision|$score" else ""
        confirmedScoreText = if (confirmedEligibility) "$score (Android ledger; $today $timezone)" else "Unavailable"
        if (key != eligibilityKey) {
            timeline.gap("Authoritative eligibility changed or is unavailable; fresh entry required.")
            eligibilityKey = key
            eligibilityValidFromMs = end
        }
        val selected = TargetAppMonitoringPolicy.installedTargets(app).filter { it.selected }
            .flatMap { it.installedPackages }.toSet()
        val exclusions = TargetAppMonitoringPolicy.protectedPackages(app)
        if (timeline.session?.packageName?.let { it !in selected || it in exclusions } == true) {
            timeline.gap("Target selection or financial exclusions changed; interval excluded.")
            currentPackage = null
        }
        val evidence = mutableListOf<ContentValues>()
        var lastEvent = checkpoint.optLong("last_event_ms", 0L)
        var anyEvents = false
        var localFailure = if (confirmedEligibility) "" else "Authoritative score snapshot unavailable/stale: no restricted minutes accrued."
        fun record(minutes: RestrictedUsageTimeline.Minutes?, eventType: String) {
            if (minutes == null) return
            evidence.add(ContentValues().apply {
                put("idempotency_key", digest(minutes.idempotencyMaterial(account, device)))
                put("account_id", account); put("device_id", device); put("package_name", minutes.packageName)
                put("started_at_ms", minutes.startedAtMs); put("ended_at_ms", minutes.endedAtMs)
                put("scoring_date", today); put("timezone", timezone); put("complete_minutes", minutes.count)
                put("source", "ANDROID_RESTRICTED_APP_USAGE_MONITORING")
                put("evidence_json", JSONObject().put("query_start_ms", start).put("query_end_ms", end)
                    .put("event_type", eventType).put("score_revision", revision).put("confirmed_score", score)
                    .put("first_minute", minutes.firstMinute).put("last_minute", minutes.lastMinute)
                    .put("confirmed_ms", minutes.confirmedMs).put("eligibility_key", minutes.eligibilityKey).toString())
                put("ledger_status", "BLOCKED_UNSUPPORTED_LEDGER"); put("committed_points", 0)
            })
        }
        try {
            val manager = app.getSystemService(Context.USAGE_STATS_SERVICE) as? UsageStatsManager
                ?: return fail("UsageStatsManager unavailable.")
            val events = manager.queryEvents(start, end) ?: return fail("UsageEvents history unavailable (possibly locked).")
            val event = UsageEvents.Event()
            var processed = 0
            while (events.hasNextEvent()) {
                if (++processed > 5000) {
                    fail("Usage event batch exceeded the 5000-event bound; interval excluded.")
                    val skipped = JSONObject().put("cursor_ms", end).put("last_event_ms", lastEvent)
                        .put("last_gap", failure).put("source", source)
                    storage.save(identity, skipped, emptyList())
                    checkpoint = skipped
                    return
                }
                events.getNextEvent(event)
                val at = event.timeStamp
                if (!range.contains(at)) continue
                anyEvents = true
                lastEvent = maxOf(lastEvent, at)
                val pkg = event.packageName.orEmpty()
                when (event.eventType) {
                    15 -> interactive = true
                    18 -> unlocked = true
                    16, 17, 26, 27 -> {
                        record(timeline.end(at, contiguous && interactive && unlocked), "SCREEN_LOCK_OR_SHUTDOWN")
                        if (event.eventType != 17) interactive = false
                        unlocked = false; currentPackage = null
                    }
                    1 -> {
                        if (pkg.isBlank()) {
                            timeline.gap("Missing package on a foreground transition; interval excluded.")
                            currentPackage = null
                            continue
                        }
                        if (currentPackage != pkg) record(timeline.end(at, contiguous && interactive && unlocked), "FOREGROUND_TRANSITION")
                        if (FinancialSafePause.isProtectedFinancialPackage(app, pkg)) {
                            FinancialSafePause.onProtectedPackageObserved(app, pkg, at)
                            timeline.gap("Protected financial session excluded."); currentPackage = null
                        } else {
                            FinancialSafePause.onNonProtectedPackageObserved(app, pkg, at)
                            currentPackage = pkg.takeIf { it in selected && it !in exclusions }
                            if (currentPackage != null && contiguous && at >= eligibilityValidFromMs && !FinancialSafePause.isPauseActive(app, at)) {
                                val restricted = score?.takeIf { confirmedEligibility }?.let {
                                    DisciplineScoringRules.evaluateRestrictionDecision(it, pkg,
                                        appLocks = db.getRemoteAppLocks(), isEducationalUnlockActive = db.isEducationalUnlockActive()).isRestricted
                                }
                                timeline.begin(pkg, at, restricted, interactive && unlocked, key)
                            }
                        }
                    }
                    2, 23 -> {
                        if (pkg.isBlank()) {
                            timeline.gap("Missing package on a background transition; interval excluded.")
                            currentPackage = null
                        } else if (pkg == currentPackage) {
                            record(timeline.end(at, contiguous && interactive && unlocked), "ACTIVITY_PAUSED")
                            currentPackage = null
                        }
                    }
                }
            }
            // Initial/current device state is an anchor for subsequent ranges,
            // never a claim about the unobserved interval before this query.
            val screenAvailable = HardwareDeviceVerifier.isMonitoringScreenAvailable(app)
            if (!screenAvailable) {
                timeline.gap("Device locked/non-interactive; unclosed interval excluded.")
                currentPackage = null
            }
            interactive = screenAvailable; unlocked = screenAvailable
            if (FinancialSafePause.isPauseActive(app)) timeline.gap("Financial safe pause active; no penalties.")
            val live = StrictAccessibilityService.activeInstance?.confirmedForegroundTargetPackage()
            if (screenAvailable && live != null && live == currentPackage && timeline.session?.packageName == live) {
                record(timeline.confirm(end, contiguous), "LIVE_WINDOW_PACKAGE_CONFIRMATION")
            }
            if (!anyEvents && !contiguous) localFailure = "No usable foreground events in bounded history; awaiting a genuine entry."
            failure = localFailure.ifBlank { timeline.lastGap }
            source = if (StrictAccessibilityService.activeInstance != null) "ACCESSIBILITY + USAGE_EVENTS" else "USAGE_EVENTS (monitoring only)"
            val nextCheckpoint = JSONObject().put("cursor_ms", end)
                .put("last_event_ms", lastEvent).put("last_gap", failure).put("source", source)
            storage.save(identity, nextCheckpoint, evidence)
            checkpoint = nextCheckpoint
            lastElapsed = elapsed; lastSuccessMs = now
        } catch (error: Exception) {
            fail("Usage reconciliation failed: ${error.javaClass.simpleName}; interval excluded.")
        }
    }

    private fun fail(reason: String) {
        timeline.gap(reason); failure = reason; source = "UNAVAILABLE"; currentPackage = null; lastElapsed = 0L
    }

    fun diagnosticText(): String = publishedDiagnostics

    private fun buildDiagnosticText(): String {
        val active = timeline.session
        return "Source: $source\nConfirmed authoritative score: $confirmedScoreText\nCurrent target: ${currentPackage ?: "Unknown"}\n" +
            "Confirmed restricted duration: ${(active?.confirmedMs ?: 0) / 1000}s\n" +
            "Uncommitted complete minutes: ${active?.evidencedMinutes ?: 0}; remainder: ${timeline.remainderMs() / 1000}s\n" +
            "Last reconciled event: ${checkpoint.optLong("last_event_ms", 0)}; query cursor: ${checkpoint.optLong("cursor_ms", 0)}\n" +
            "Last gap/failure: ${failure.ifBlank { "None recorded in current bounded interval" }}\n" +
            "Committed penalty points: 0; sync: BLOCKED — authoritative ledger does not support this source."
    }
}
