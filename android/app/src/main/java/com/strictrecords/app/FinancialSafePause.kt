package com.strictrecords.app

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import java.util.Locale

/**
 * FinancialSafePause coordinates the mandatory 5-minute enforcement suspension
 * when protected financial, UPI, banking, or credential management applications are in use.
 *
 * Rules:
 * 1. Never show or retain restriction overlays above banking, UPI, payment, authentication,
 *    or password-manager applications.
 * 2. When any protected app enters the foreground:
 *    - Immediately enter Financial Safe Pause.
 *    - Keep protection active throughout the session, then set a five-minute after-exit deadline.
 *    - Dismiss all active restriction overlays and popups immediately.
 *    - Suppress all enforcement actions, popups, and navigation.
 *    - Never inspect financial screen contents or sensitive details.
 * 3. Resume enforcement ONLY after BOTH conditions are satisfied:
 *    - 5 full minutes have elapsed since the protected session ended.
 *    - No protected financial app is currently in the foreground.
 * 4. State is persisted in SharedPreferences across application process restarts.
 */
object FinancialSafePause {

    const val TAG = "FinancialSafePause"
    const val PREFS_NAME = "financial_safe_pause"
    const val KEY_PAUSE_STARTED_AT = "pause_started_at_ms"
    const val KEY_RESUME_DEADLINE = "resume_deadline_ms"
    const val KEY_LAST_PROTECTED_PACKAGE = "last_protected_package"
    const val KEY_IS_PROTECTED_IN_FOREGROUND = "is_protected_app_in_foreground"
    private const val KEY_LAST_OBSERVATION = "last_foreground_observation_ms"

    const val PAUSE_DURATION_MS = 5 * 60_000L // 5 minutes (300,000 ms)

    interface SafePauseStore {
        fun getLong(key: String, def: Long): Long
        fun putLong(key: String, value: Long)
        fun getBoolean(key: String, def: Boolean): Boolean
        fun putBoolean(key: String, value: Boolean)
        fun getString(key: String, def: String): String
        fun putString(key: String, value: String)
        fun clear()
    }

    class SharedPrefsSafePauseStore(private val prefs: SharedPreferences) : SafePauseStore {
        override fun getLong(key: String, def: Long): Long = prefs.getLong(key, def)
        override fun putLong(key: String, value: Long) { prefs.edit().putLong(key, value).apply() }
        override fun getBoolean(key: String, def: Boolean): Boolean = prefs.getBoolean(key, def)
        override fun putBoolean(key: String, value: Boolean) { prefs.edit().putBoolean(key, value).apply() }
        override fun getString(key: String, def: String): String = prefs.getString(key, def).orEmpty()
        override fun putString(key: String, value: String) { prefs.edit().putString(key, value).apply() }
        override fun clear() { prefs.edit().clear().apply() }
    }

    class InMemorySafePauseStore : SafePauseStore {
        private val longMap = mutableMapOf<String, Long>()
        private val boolMap = mutableMapOf<String, Boolean>()
        private val strMap = mutableMapOf<String, String>()

        override fun getLong(key: String, def: Long): Long = longMap[key] ?: def
        override fun putLong(key: String, value: Long) { longMap[key] = value }
        override fun getBoolean(key: String, def: Boolean): Boolean = boolMap[key] ?: def
        override fun putBoolean(key: String, value: Boolean) { boolMap[key] = value }
        override fun getString(key: String, def: String): String = strMap[key] ?: def
        override fun putString(key: String, value: String) { strMap[key] = value }
        override fun clear() {
            longMap.clear()
            boolMap.clear()
            strMap.clear()
        }
    }

    // Comprehensive registry of protected financial, banking, UPI, crypto, and credential applications.
    val PROTECTED_FINANCIAL_PACKAGES: Set<String> = setOf(
        // UPI & Wallets
        "com.phonepe.app",
        "com.google.android.apps.nbu.paisa.user",
        "net.one97.paytm",
        "in.org.npci.upiapp", // BHIM
        "com.dreamplug.androidapp", // CRED
        "com.amazon.mShop.android.shopping", // Amazon Pay
        "com.mobikwik_new",
        "com.freecharge.android",
        "com.myairtelapp",
        "com.jio.myjio",
        "com.tatadigital.tcp", // Tata Neu
        "org.altruist.BajajFinserv", // Bajaj Finserv
        "com.samsung.android.spay", // Samsung Pay / Wallet
        "com.samsung.android.spaymini",

        // Navi & Lending
        "com.navi.mutualfunds",
        "com.navi.navi",

        // Major Indian & Global Banks
        "com.sbi.lotusintouch", // SBI YONO
        "com.sbi.upi", // SBI YONO Lite / Pay
        "com.sbi.SBIFreedomPlus",
        "com.csam.icici.bank.imobile", // ICICI iMobile
        "com.icicibank.pockets",
        "com.snapwork.hdfc", // HDFC MobileBanking
        "com.hdfcbank.payzapp", // HDFC PayZapp
        "com.axis.mobile", // Axis Mobile
        "com.msf.kbank.mobile", // Kotak 811
        "com.bankofbaroda.mconnect", // BoB World
        "com.pnbebank.mbanking", // PNB One
        "com.canarabank.ai1", // Canara ai1
        "com.indusind.indie", // IndusInd Indie
        "com.rblbank.mobank", // RBL MoBank
        "com.idfcfirstbank.optimus", // IDFC FIRST Mobile
        "com.aufinbank.aubank", // AU 0101
        "com.federalbank.fedmobile", // FedMobile
        "com.unionbank.online", // Vyom Union Bank
        "com.boi.mpay", // BOI Mobile
        "com.infrasofttech.indianbank", // IndOASIS
        "com.infrasoft.cbi", // Cent Mobile
        "com.yesbank",
        "com.sc.bca", // Standard Chartered
        "net.hsbc.hsbcmobilebanking",
        "com.citibank.mobile.in",

        // Trading & Investments
        "com.zerodha.kite3",
        "com.nextbillion.groww",
        "com.msf.angelmobile",
        "in.upstox.app",
        "com.dhan.live",
        "com.fivepaisa.trade",
        "com.coindcx",
        "com.binance.dev",
        "com.wrx.offline",
        "com.coinswitch.kuber",

        // Password & Credential Managers
        "com.x8bit.bitwarden",
        "com.onepassword.android",
        "com.dashlane",
        "com.lastpass.lpandroid",
        "keepass2android.keepass2android",
        "me.proton.pass",
        "io.enpass.app",
        "com.azure.authenticator",
        "com.google.android.apps.authenticator2",
        "com.twofasapp",
        "com.beemdevelopment.aegis",
        "com.authy.authy",

        // System Settings, Security & Package Installers
        "com.android.settings",
        "com.google.android.packageinstaller",
        "com.android.packageinstaller",
        "com.samsung.android.packageinstaller",
        "com.google.android.permissioncontroller"
    )

    private val lock = Any()

    @Volatile
    private var customStore: SafePauseStore? = null

    @Volatile
    private var memStartedAtMs: Long = 0L

    @Volatile
    private var memResumeDeadlineMs: Long = 0L

    @Volatile
    private var memLastProtectedPackage: String = ""

    @Volatile
    private var memIsProtectedInForeground: Boolean = false
    @Volatile private var memLastObservationMs = 0L

    @Volatile
    private var isInitialized: Boolean = false

    data class DiagnosticsState(
        val isActive: Boolean,
        val remainingMs: Long,
        val isProtectedInForeground: Boolean,
        val lastProtectedPackage: String,
        val resumeDeadlineMs: Long,
        val pauseStartedAtMs: Long
    )

    fun setStoreForTesting(store: SafePauseStore?) {
        synchronized(lock) {
            customStore = store
            isInitialized = false
        }
    }

    private fun resolveStore(context: Context?): SafePauseStore {
        customStore?.let { return it }
        val ctx = context ?: throw IllegalStateException("Context required to access SharedPreferences safe pause store")
        return SharedPrefsSafePauseStore(ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE))
    }

    private fun ensureLoaded(context: Context?) {
        if (isInitialized) return
        synchronized(lock) {
            if (isInitialized) return
            try {
                val store = resolveStore(context)
                memStartedAtMs = store.getLong(KEY_PAUSE_STARTED_AT, 0L)
                memResumeDeadlineMs = store.getLong(KEY_RESUME_DEADLINE, 0L)
                memLastProtectedPackage = store.getString(KEY_LAST_PROTECTED_PACKAGE, "")
                memIsProtectedInForeground = store.getBoolean(KEY_IS_PROTECTED_IN_FOREGROUND, false)
                memLastObservationMs = store.getLong(KEY_LAST_OBSERVATION, 0L)
            } catch (_: Exception) {
                // If context unavailable or running in pure unit test without customStore
            }
            isInitialized = true
        }
    }

    /**
     * Identifies whether a given package name corresponds to a protected financial,
     * UPI, banking, trading, credential manager, or security application.
     */
    fun isProtectedFinancialPackage(context: Context?, packageName: String?): Boolean {
        if (packageName.isNullOrBlank()) return false
        val clean = packageName.trim()

        // 1. Direct match with static protected set
        if (clean in PROTECTED_FINANCIAL_PACKAGES) return true

        // 2. TargetAppMonitoringPolicy DEFAULT_FINANCIAL_PACKAGES match
        if (clean in TargetAppMonitoringPolicy.DEFAULT_FINANCIAL_PACKAGES) return true

        // 3. User-configured protected packages if context is available
        if (context != null) {
            try {
                val userProtected = TargetAppMonitoringPolicy.protectedPackages(context)
                if (clean in userProtected) return true
            } catch (_: Throwable) {}
        }

        // 4. Pattern matching on known financial package namespaces
        val lower = clean.lowercase(Locale.ROOT)
        if (lower.startsWith("com.sbi.") ||
            lower.startsWith("com.hdfc") ||
            lower.startsWith("com.icici") ||
            lower.startsWith("com.axis") ||
            lower.startsWith("com.kotak") ||
            lower.startsWith("com.phonepe.") ||
            lower.startsWith("net.one97.paytm") ||
            lower.contains(".banking") ||
            lower.endsWith(".bank") ||
            lower.contains("authenticator") ||
            lower.contains("passwordmanager")
        ) {
            // Safety guard: ensure not a recognized entertainment target
            if (!TargetAppMonitoringPolicy.isTargetPackage(clean)) {
                return true
            }
        }

        return false
    }

    /**
     * Invoked when any protected financial app enters the foreground.
     * Starts one five-minute deadline per visit, marks the protected app active,
     * immediately dismisses any overlay and suspends target monitoring.
     */
    fun onProtectedPackageObserved(
        context: Context?,
        packageName: String,
        nowMs: Long = System.currentTimeMillis()
    ) {
        ensureLoaded(context)
        var deadlineChanged = false
        var actualDeadline = 0L
        synchronized(lock) {
            if (nowMs < memLastObservationMs) return
            memLastObservationMs = nowMs
            val active = isPauseActiveInternal(nowMs)
            val startedAt = if (active && memStartedAtMs > 0L) memStartedAtMs else nowMs
            val deadline = BankingBreakPlan.deadlineForObservation(memResumeDeadlineMs,
                memLastProtectedPackage, memIsProtectedInForeground, packageName, nowMs, PAUSE_DURATION_MS)
            deadlineChanged = deadline != memResumeDeadlineMs || !memIsProtectedInForeground || memLastProtectedPackage != packageName
            actualDeadline = deadline

            memStartedAtMs = startedAt
            memResumeDeadlineMs = deadline
            memLastProtectedPackage = packageName
            memIsProtectedInForeground = true

            try {
                val store = resolveStore(context)
                store.putLong(KEY_PAUSE_STARTED_AT, startedAt)
                store.putLong(KEY_RESUME_DEADLINE, deadline)
                store.putString(KEY_LAST_PROTECTED_PACKAGE, packageName)
                store.putBoolean(KEY_IS_PROTECTED_IN_FOREGROUND, true)
                store.putLong(KEY_LAST_OBSERVATION, nowMs)
            } catch (e: Exception) {
                Log.e(TAG, "Failed to persist FinancialSafePause state: ${e.message}")
            }
        }

        if (deadlineChanged) Log.i(TAG, "platform=Android action=financial_safe_pause_triggered package=$packageName resumeDeadlineMs=$actualDeadline")

        // Immediately dismiss any active restriction overlay or popups
        try {
            StrictBotOverlayController.hidePopupImmediate()
        } catch (_: Throwable) {}
        if (context != null && deadlineChanged) BankingBreakController.restore(context)
    }

    /**
     * Invoked when a non-protected package (or home launcher) enters the foreground.
     * Marks the protected app as no longer active and STARTS the full five-minute
     * after-exit cooldown. Repeated unrelated foreground events cannot extend it.
     */
    fun onNonProtectedPackageObserved(
        context: Context?,
        packageName: String?,
        nowMs: Long = System.currentTimeMillis()
    ) {
        ensureLoaded(context)
        var changed = false
        synchronized(lock) {
            if (nowMs < memLastObservationMs || packageName.isNullOrBlank() ||
                isProtectedFinancialPackage(context, packageName)) return
            memLastObservationMs = nowMs
            if (memIsProtectedInForeground) {
                memIsProtectedInForeground = false
                memResumeDeadlineMs = BankingBreakPlan.deadlineAfterExit(memResumeDeadlineMs, nowMs, PAUSE_DURATION_MS)
                changed = true
                try {
                    val store = resolveStore(context)
                    store.putBoolean(KEY_IS_PROTECTED_IN_FOREGROUND, false)
                    store.putLong(KEY_RESUME_DEADLINE, memResumeDeadlineMs)
                    store.putLong(KEY_LAST_OBSERVATION, nowMs)
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to update foreground state: ${e.message}")
                }
                Log.i(TAG, "platform=Android action=financial_app_exited package=${packageName.orEmpty()} remainingPauseMs=${memResumeDeadlineMs - nowMs}")
            }
        }
        if (context != null && changed) BankingBreakController.restore(context)
    }

    /**
     * Returns true if enforcement must be paused.
     * Enforcement resumes ONLY after:
     * 1. The five-minute after-exit cooldown has expired (nowMs >= resumeDeadlineMs), AND
     * 2. No protected financial app is currently in the foreground (!isProtectedInForeground).
     */
    fun isPauseActive(context: Context?, nowMs: Long = System.currentTimeMillis()): Boolean {
        ensureLoaded(context)
        return isPauseActiveInternal(nowMs)
    }

    private fun isPauseActiveInternal(nowMs: Long): Boolean {
        if (memIsProtectedInForeground) return true
        return nowMs < memResumeDeadlineMs
    }

    /**
     * Returns the remaining time in milliseconds for the current safe pause.
     * A protected app can keep enforcement paused after the timer reaches zero;
     * the countdown itself must not misleadingly restart at five minutes.
     */
    fun remainingPauseMs(context: Context?, nowMs: Long = System.currentTimeMillis()): Long {
        ensureLoaded(context)
        return maxOf(0L, memResumeDeadlineMs - nowMs)
    }

    /**
     * Returns a snapshot of the current FinancialSafePause state for diagnostics.
     */
    fun getDiagnosticsState(context: Context?, nowMs: Long = System.currentTimeMillis()): DiagnosticsState {
        ensureLoaded(context)
        val active = isPauseActiveInternal(nowMs)
        val remaining = remainingPauseMs(context, nowMs)
        return DiagnosticsState(
            isActive = active,
            remainingMs = remaining,
            isProtectedInForeground = memIsProtectedInForeground,
            lastProtectedPackage = memLastProtectedPackage,
            resumeDeadlineMs = memResumeDeadlineMs,
            pauseStartedAtMs = memStartedAtMs
        )
    }

    /**
     * Testing / reset helper.
     */
    fun resetForTesting(context: Context? = null) {
        synchronized(lock) {
            memStartedAtMs = 0L
            memResumeDeadlineMs = 0L
            memLastProtectedPackage = ""
            memIsProtectedInForeground = false
            memLastObservationMs = 0L
            isInitialized = true
            try {
                resolveStore(context).clear()
            } catch (_: Exception) {}
        }
        if (context != null) BankingBreakController.restore(context)
    }
}
