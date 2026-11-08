package com.strictrecords.app

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityWindowInfo
import android.app.ActivityManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.util.Log

import java.util.concurrent.FutureTask
import java.util.concurrent.TimeUnit
import java.util.concurrent.Executors

/** Optional package-scoped foreground confirmation. No text extraction or navigation actions. */
class StrictAccessibilityService : AccessibilityService() {
    companion object {
        @Volatile var activeInstance: StrictAccessibilityService? = null
    }

    private val handler = Handler(Looper.getMainLooper())
    private var bankingCheckPending = false
    private val decisionWorker = Executors.newSingleThreadExecutor { task ->
        Thread(task, "StrictRecords-Eligibility").apply { priority = Thread.NORM_PRIORITY - 1 }
    }
    private var evaluationInFlight = false
    private var evaluationRequested = false
    private var decisionEpoch = 0L
    private var lastEvaluationPackage: String? = null
    private var lastEvaluationAt = 0L
    private var lastContentCheckAtMs = 0L
    private var lastContentPackage: String? = null
    private var lastPhoneAlertSentMs = 0L
    private val packageEvents = TargetForegroundTracker(TargetAppMonitoringPolicy.TargetApp.values()
        .flatMap { it.packageNames }.toSet())
    private val preferences by lazy { getSharedPreferences("target_app_monitoring", MODE_PRIVATE) }
    private val selectionListener = SharedPreferences.OnSharedPreferenceChangeListener { _, _ ->
        handler.post {
            decisionEpoch++
            packageEvents.clear(System.currentTimeMillis())
            lastEvaluationPackage = null
            refreshPackageFilter()
            FloatingDisciplineBotOverlay.hide()
            StrictBotOverlayController.hidePopupImmediate()
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        activeInstance = this
        StrictWidgetUpdates.request(applicationContext, "monitoring")
        preferences.registerOnSharedPreferenceChangeListener(selectionListener)
        if (!TargetAppMonitoringPolicy.hasUsageConsent(this)) {
            TargetAppMonitoringPolicy.setUsageConsent(this, true)
        }
        if (TargetAppMonitoringPolicy.selectedTargetIds(this).isEmpty()) {
            TargetAppMonitoringPolicy.saveSelectedTargets(this, TargetAppMonitoringPolicy.DEFAULT_SELECTED_TARGETS)
        }
        refreshPackageFilter()
        BankingBreakController.restore(this)
        StrictEnforcementService.start(applicationContext)
        AccessibilityGraceManager.onAccessibilityReconnected(this)
        FloatingAccessibilityCountdownOverlay.hide()
        AccessibilityWarningOverlay.hide()
        Log.i("StrictRestriction", "platform=Android accessibility=connected")

        val now = System.currentTimeMillis()
        val livePkg = detectCurrentForegroundPackage()
        if (!livePkg.isNullOrBlank()) {
            if (FinancialSafePause.isProtectedFinancialPackage(this, livePkg)) {
                FinancialSafePause.onProtectedPackageObserved(this, livePkg, now)
                StrictBotOverlayController.hidePopupImmediate()
            } else {
                val selected = livePkg.takeIf { TargetAppMonitoringPolicy.mayMonitor(this, it) }
                packageEvents.onResumed(selected, now)
                handler.post { evaluateCurrentTarget() }
            }
        }
        handler.postDelayed({ evaluateCurrentTarget() }, 200L)
        handler.postDelayed({ evaluateCurrentTarget() }, 600L)
    }

    fun refreshPackageFilter() {
        val info = serviceInfo ?: AccessibilityServiceInfo()
        info.packageNames = null
        val pause = FinancialSafePause.getDiagnosticsState(this)
        val suspended = pause.isActive
        info.eventTypes = if (suspended) {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
        } else {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED or
                AccessibilityEvent.TYPE_WINDOWS_CHANGED or
                AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED
        }
        info.feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
        info.notificationTimeout = 80L
        info.flags = AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS or
            AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
        setServiceInfo(info)
    }

    fun detectCurrentForegroundPackage(): String? {
        fun isSystemIgnored(p: String): Boolean =
            p.isBlank() || p in setOf(
                "android", "com.android.systemui", "com.samsung.android.honeyboard",
                "com.google.android.inputmethod.latin", "com.samsung.android.app.cocktailbarservice"
            ) || p == packageName

        try {
            val root = rootInActiveWindow
            val pkg = root?.packageName?.toString()
            if (!pkg.isNullOrBlank() && !isSystemIgnored(pkg)) {
                return pkg
            }
        } catch (_: Throwable) {}

        try {
            val winList = windows
            if (!winList.isNullOrEmpty()) {
                val appWin = winList.firstOrNull { it.isActive && it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
                    ?: winList.firstOrNull { it.isFocused && it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
                    ?: winList.firstOrNull { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
                val pkg = appWin?.root?.packageName?.toString() ?: appWin?.title?.toString()
                if (!pkg.isNullOrBlank() && !isSystemIgnored(pkg)) {
                    return pkg
                }
            }
        } catch (_: Throwable) {}

        try {
            if (HardwareDeviceVerifier.hasUsageStatsPermission(this)) {
                val usm = getSystemService(Context.USAGE_STATS_SERVICE) as? UsageStatsManager
                val now = System.currentTimeMillis()
                val events = usm?.queryEvents(now - 15_000L, now)
                if (events != null) {
                    val event = UsageEvents.Event()
                    var latestPkg: String? = null
                    var latestTime = 0L
                    while (events.hasNextEvent()) {
                        events.getNextEvent(event)
                        if (event.eventType == UsageEvents.Event.ACTIVITY_RESUMED ||
                            event.eventType == 1) {
                            if (event.timeStamp >= latestTime) {
                                latestTime = event.timeStamp
                                latestPkg = event.packageName
                            }
                        }
                    }
                    if (!latestPkg.isNullOrBlank() && !isSystemIgnored(latestPkg)) {
                        return latestPkg
                    }
                }
            }
        } catch (_: Throwable) {}

        try {
            val am = getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager
            val procs = am?.runningAppProcesses
            val fgProc = procs?.firstOrNull { it.importance == ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND }
            if (fgProc != null && fgProc.pkgList.isNotEmpty()) {
                val p = fgProc.pkgList[0]
                if (!p.isNullOrBlank() && !isSystemIgnored(p)) return p
            }
        } catch (_: Throwable) {}

        return null
    }

    internal fun suspendForBankingBreak() {
        decisionEpoch++
        lastEvaluationPackage = null
        evaluationRequested = false
        handler.removeCallbacks(reevaluate)
        handler.removeCallbacks(bankingResumeCheck)
        bankingCheckPending = false
        refreshPackageFilter() // Package transitions only; never retrieve windows during the safe pause.
        FloatingDisciplineBotOverlay.hide()
        StrictBotOverlayController.hidePopupImmediate()
    }

    internal fun checkAfterBankingBreak() {
        refreshPackageFilter()
        if (bankingCheckPending) return
        bankingCheckPending = true
        handler.postDelayed(bankingResumeCheck, 120L)
    }

    internal fun resumeAfterBankingBreak() {
        handler.removeCallbacks(bankingResumeCheck)
        bankingCheckPending = false
        refreshPackageFilter()
        handler.removeCallbacks(reevaluate)
        handler.post(reevaluate)
    }

    private val bankingResumeCheck = Runnable {
        bankingCheckPending = false
        val now = System.currentTimeMillis()
        val pause = FinancialSafePause.getDiagnosticsState(this, now)
        if (now < pause.resumeDeadlineMs || !TargetAppMonitoringPolicy.hasUsageConsent(this)) return@Runnable
        // One package-only check after the timer; never read text or act on the bank.
        // Unknown/locked foreground keeps enforcement paused until a fresh window event.
        if (!HardwareDeviceVerifier.isMonitoringScreenAvailable(this)) return@Runnable
        val pkg = readForegroundWindowPackage() ?: return@Runnable
        if (FinancialSafePause.isProtectedFinancialPackage(this, pkg)) {
            FinancialSafePause.onProtectedPackageObserved(this, pkg, now)
        } else {
            FinancialSafePause.onNonProtectedPackageObserved(this, pkg, now)
        }
    }

    /** Fresh package events, corroborated by Usage Access when older; never read app content. */
    fun confirmedForegroundTargetPackage(): String? = confirmedForegroundPackage()?.takeIf { TargetAppMonitoringPolicy.mayMonitor(this, it) }

    fun confirmedForegroundPackage(): String? {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            val future = FutureTask { confirmedForegroundPackage() }
            handler.post(future)
            return try {
                future.get(150, TimeUnit.MILLISECONDS)
            } catch (_: Exception) {
                // A busy UI must not accumulate stale window reads after the caller times out.
                future.cancel(false)
                handler.removeCallbacks(future)
                null
            }
        }
        if (FinancialSafePause.isPauseActive(this) || !TargetAppMonitoringPolicy.canReadUsage(this)) return null
        return readForegroundWindowPackage()
    }

    private fun readForegroundWindowPackage(): String? {
        val now = System.currentTimeMillis()
        val usage = UsageAccessMonitor.foreground(now)
        val candidate = ForegroundWindowRules.confirmedEventPackage(packageEvents.packageName,
            packageEvents.observedAtMs, now, usage.packageName, usage.observedAtMs, usage.confirmed,
            keepPresentedTarget = StrictBotOverlayController.ownsOverlayForeground(packageEvents.packageName))
            ?.takeIf { TargetAppMonitoringPolicy.mayMonitor(this, it) }

        if (candidate != null) return candidate

        val live = detectCurrentForegroundPackage()?.takeIf { TargetAppMonitoringPolicy.mayMonitor(this, it) }
        if (live != null) {
            packageEvents.onResumed(live, now)
            return live
        }
        return null
    }

    fun onOwnActivityResumed() {
        if (!StrictBotOverlayController.ownsFallbackForeground(packageName)) {
            packageEvents.clear(System.currentTimeMillis())
            FloatingDisciplineBotOverlay.hide()
            StrictBotOverlayController.hidePopupImmediate()
        }
    }

    private val reevaluate = Runnable { evaluateCurrentTarget() }
    private var demoBoundaryRequested = false

    fun onDemoAccessChanged() {
        handler.post {
            demoBoundaryRequested = true
            decisionEpoch++
            lastEvaluationPackage = null
            handler.removeCallbacks(reevaluate)
            handler.post(reevaluate)
        }
    }

    fun onYouTubeRewardChanged() {
        handler.post {
            val pkg=confirmedForegroundTargetPackage() ?: return@post
            if(TargetAppMonitoringPolicy.targetForPackage(pkg)?.id != "youtube") return@post
            demoBoundaryRequested=true
            decisionEpoch++
            lastEvaluationPackage=null
            handler.removeCallbacks(reevaluate)
            handler.post(reevaluate)
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        val eventType = event.eventType
        if (eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED &&
            eventType != AccessibilityEvent.TYPE_WINDOWS_CHANGED &&
            eventType != AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) return

        val eventPkg = event.packageName?.toString()
        val now = System.currentTimeMillis()

        if (eventType == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) {
            if (eventPkg.isNullOrBlank() || (eventPkg == lastContentPackage && now - lastContentCheckAtMs < 500L)) return
            lastContentCheckAtMs = now
            lastContentPackage = eventPkg
        }

        if (!TargetAppMonitoringPolicy.hasUsageConsent(this) || TargetAppMonitoringPolicy.selectedTargetIds(this).isEmpty()) {
            FloatingDisciplineBotOverlay.hide()
            StrictBotOverlayController.hidePopupImmediate()
            return
        }
        val pause = FinancialSafePause.getDiagnosticsState(this)
        if (pause.isActive) {
            if (eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED &&
                !eventPkg.isNullOrBlank() && (eventPkg != packageName || event.className?.toString() == MainActivity::class.java.name) && eventPkg !in setOf(
                    "com.android.systemui", "android", "com.samsung.android.honeyboard", "com.google.android.inputmethod.latin")) {
                if (FinancialSafePause.isProtectedFinancialPackage(this, eventPkg)) FinancialSafePause.onProtectedPackageObserved(this, eventPkg)
                else FinancialSafePause.onNonProtectedPackageObserved(this, eventPkg)
            }
            FloatingDisciplineBotOverlay.hide()
            StrictBotOverlayController.hidePopupImmediate()
            return
        }

        if (!eventPkg.isNullOrBlank() && FinancialSafePause.isProtectedFinancialPackage(this, eventPkg)) {
            packageEvents.clear(now)
            decisionEpoch++
            lastEvaluationPackage = null
            FinancialSafePause.onProtectedPackageObserved(this, eventPkg)
            StrictBotOverlayController.onNonRestrictedForegroundObserved(eventPkg)
            StrictBotOverlayController.hidePopupImmediate()
            FloatingDisciplineBotOverlay.hide()
            return
        }

        val transition = ForegroundWindowRules.isApplicationTransition(eventType, eventPkg, packageName) ||
            (eventType == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED && TargetAppMonitoringPolicy.isTargetPackage(eventPkg))

        if (transition && !eventPkg.isNullOrBlank()) {
            val selected = eventPkg.takeIf { TargetAppMonitoringPolicy.mayMonitor(this, it) }
            packageEvents.onResumed(selected, now)
            HardwareDeviceVerifier.updateAccessibilityForegroundPackage(eventPkg)
            if (!TargetAppMonitoringPolicy.mayMonitor(this, eventPkg)) {
                val previous = lastEvaluationPackage
                lastEvaluationPackage = null
                decisionEpoch++
                StrictBotOverlayController.onNonRestrictedForegroundObserved(eventPkg)
                FloatingDisciplineBotOverlay.hide()
                if (previous != null) BackgroundMusicRestrictionManager.onTargetAppBackgrounded(this, previous)
                return
            }
        }
        if (FinancialSafePause.isPauseActive(this)) {
            FloatingDisciplineBotOverlay.hide()
            StrictBotOverlayController.hidePopupImmediate()
            return
        }
        evaluateCurrentTarget()
        if (!evaluationInFlight && lastEvaluationPackage == null) {
            handler.removeCallbacks(reevaluate)
            handler.postDelayed(reevaluate, 120L)
        }
    }

    private fun evaluateCurrentTarget() {
        handler.removeCallbacks(reevaluate)
        if (activeInstance !== this) return
        if (FinancialSafePause.isPauseActive(this)) {
            FloatingDisciplineBotOverlay.hide()
        StrictBotOverlayController.hidePopupImmediate()
            return
        }
        val pm = getSystemService(Context.POWER_SERVICE) as? PowerManager
        val km = getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager
        if (pm?.isInteractive != true || km?.isKeyguardLocked == true || StrictRecordsPowerManager.isSuspended(this)) {
            FloatingDisciplineBotOverlay.hide()
        StrictBotOverlayController.hidePopupImmediate()
            return
        }
        val pkg = confirmedForegroundPackage()
        if (pkg == null) {
            StrictBotOverlayController.onForegroundUncertain(this)
            return
        }
        if (StrictBotOverlayController.ownsFallbackForeground(pkg)) return
        if (FinancialSafePause.isProtectedFinancialPackage(this, pkg)) {
            FinancialSafePause.onProtectedPackageObserved(this, pkg)
            StrictBotOverlayController.onNonRestrictedForegroundObserved(pkg)
            return
        }

        // Strict Session Cross-Device Phone Usage Monitoring
        try {
            val dbEngine = StrictRecordsLocalDb.getInstance(this)
            if (dbEngine.isStrictSessionActive()) {
                val isOwnApp = pkg == packageName
                val isSystemApp = pkg in setOf(
                    "android", "com.android.systemui", "com.samsung.android.honeyboard",
                    "com.google.android.inputmethod.latin", "com.samsung.android.dialer",
                    "com.google.android.dialer", "com.android.phone", "com.android.server.telecom"
                )
                if (!isOwnApp && !isSystemApp) {
                    val nowMs = System.currentTimeMillis()
                    if (nowMs - lastPhoneAlertSentMs > 30_000L) {
                        lastPhoneAlertSentMs = nowMs
                        val alertId = "phone_alert_" + java.util.UUID.randomUUID().toString().replace("-", "").take(16)
                        val appLabel = try {
                            val pm = packageManager
                            val info = pm.getApplicationInfo(pkg, 0)
                            pm.getApplicationLabel(info).toString()
                        } catch (_: Exception) { pkg }
                        val alertPayload = org.json.JSONObject().apply {
                            put("event_id", alertId)
                            put("device", "ANDROID")
                            put("package_name", pkg)
                            put("app_label", appLabel)
                            put("timestamp", dbEngine.nowIso())
                            put("reason", "Mobile app usage outside authorized proof submission window during Strict Session")
                        }
                        dbEngine.enqueueSyncEvent("PHONE_USAGE_DETECTED", alertPayload, this)
                    }
                }
            }
        } catch (_: Exception) {}
        if (!TargetAppMonitoringPolicy.mayMonitor(this, pkg)) {
            val prevPkg = lastEvaluationPackage
            if (prevPkg != null && BackgroundMusicRestrictionManager.isMonitoredPlaybackPackage(prevPkg)) {
                BackgroundMusicRestrictionManager.onTargetAppBackgrounded(this, prevPkg)
            }
            lastEvaluationPackage = null
            StrictBotOverlayController.onNonRestrictedForegroundObserved(pkg)
            return
        }
        val observation = TargetAppMonitoringPolicy.evaluate(this, pkg, System.currentTimeMillis())
        if (!observation.shouldMonitor) {
            FloatingDisciplineBotOverlay.hide()
        StrictBotOverlayController.hidePopupImmediate()
            return
        }
        if (evaluationInFlight) { evaluationRequested = true; return }
        val elapsed = SystemClock.elapsedRealtime()
        val delay = if (pkg == lastEvaluationPackage) {
            (MonitoringWorkPolicy.WINDOW_EVENT_INTERVAL_MS - (elapsed - lastEvaluationAt)).coerceAtLeast(0L)
        } else 0L
        if (delay > 0L) { handler.postDelayed(reevaluate, delay); return }
        lastEvaluationPackage = pkg
        lastEvaluationAt = elapsed
        evaluationInFlight = true
        evaluationRequested = false
        val epoch = decisionEpoch
        val rearmForDemoBoundary = demoBoundaryRequested
        val rewardBoundary = TargetAppMonitoringPolicy.targetForPackage(pkg)?.id == "youtube" && YouTubeRewardManager.historyFreeBoundary()
        val demoBoundary = demoBoundaryRequested || DemoAccessManager.historyFreeBoundary() || rewardBoundary
        demoBoundaryRequested = false
        decisionWorker.execute {
            val decision = try {
                val db = StrictRecordsLocalDb.getInstance(applicationContext)
                val today = db.nowDate()
                val countdown = if (demoBoundary) null else db.getCurrentCountdownState(today)
                DisciplineScoringRules.evaluateRestrictionDecision(
                    db.getScore(today, false), pkg,
                    appLocks = db.getRemoteAppLocks(),
                    isEducationalUnlockActive = if (demoBoundary) false else db.isEducationalUnlockActive(),
                    nextTaskTitle = countdown?.title ?: "Complete your current scheduled task",
                    nextTaskWindow = "${countdown?.windowStart.orEmpty()}–${countdown?.windowEnd.orEmpty()}",
                    context = this,
                    db = db
                ).copy(historyFreeRefresh = demoBoundary || DemoAccessManager.historyFreeBoundary() || rewardBoundary)
            } catch (error: Exception) {
                Log.w("StrictRestriction", "Eligibility read failed: ${error.javaClass.simpleName}")
                null
            }
            handler.post {
                evaluationInFlight = false
                // Fresh package, permission, screen and financial checks at the UI boundary.
                // A completed worker must never bring a shield back over a bank or a new app.
                if (activeInstance !== this) return@post
                if (epoch == decisionEpoch && !FinancialSafePause.isPauseActive(this) &&
                    HardwareDeviceVerifier.isMonitoringScreenAvailable(this) &&
                    TargetAppMonitoringPolicy.mayMonitor(this, pkg) && confirmedForegroundPackage() == pkg) {
                    if (decision?.isRestricted == true) {
                        if (BackgroundMusicRestrictionManager.isMonitoredPlaybackPackage(pkg)) {
                            BackgroundMusicRestrictionManager.onTargetAppForegrounded(this, pkg)
                        }
                        if (rearmForDemoBoundary) StrictBotOverlayController.prepareDemoAccessRecheck(this)
                        StrictBotOverlayController.showStrictBotPopup(this, decision)
                    }
                    else {
                        FloatingDisciplineBotOverlay.hide()
                        StrictBotOverlayController.hidePopupImmediate()
                    }
                }
                if (evaluationRequested) { evaluationRequested = false; handler.post(reevaluate) }
            }
        }
    }

    override fun onInterrupt() {
        packageEvents.clear(System.currentTimeMillis())
        FloatingDisciplineBotOverlay.hide()
        StrictBotOverlayController.hidePopupImmediate()
    }

    override fun onUnbind(intent: Intent?): Boolean {
        decisionEpoch++
        evaluationRequested = false
        handler.removeCallbacks(reevaluate)
        handler.removeCallbacks(bankingResumeCheck)
        bankingCheckPending = false
        if (activeInstance === this) activeInstance = null
        StrictWidgetUpdates.request(applicationContext,"monitoring")
        FloatingDisciplineBotOverlay.hide()
        StrictBotOverlayController.hidePopupImmediate()
        StrictEnforcementService.start(applicationContext)
        AccessibilityGraceManager.checkAccessibilityState(this)
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        decisionEpoch++
        decisionWorker.shutdownNow()
        Log.i("StrictRestriction", "platform=Android accessibility=disconnected")
        preferences.unregisterOnSharedPreferenceChangeListener(selectionListener)
        handler.removeCallbacks(reevaluate)
        handler.removeCallbacks(bankingResumeCheck)
        bankingCheckPending = false
        if (activeInstance === this) activeInstance = null
        FloatingDisciplineBotOverlay.hide()
        StrictBotOverlayController.hidePopupImmediate()
        StrictEnforcementService.start(applicationContext)
        AccessibilityGraceManager.checkAccessibilityState(this)
        super.onDestroy()
    }
}
