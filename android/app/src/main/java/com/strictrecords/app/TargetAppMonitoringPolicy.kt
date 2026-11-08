package com.strictrecords.app

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.SystemClock

/**
 * The single scope gate for observing and enforcing selected entertainment apps.
 * This policy never reads app content, awards points, pauses music or opens overlays.
 * Existing scoring/enforcement services run only after a MONITOR observation.
 */
object TargetAppMonitoringPolicy {
    const val MONITOR = "MONITOR"
    const val IGNORE = "IGNORE"
    const val UNKNOWN = "UNKNOWN"
    const val MAX_FOREGROUND_AGE_MS = 5_000L
    const val PREFERENCES = "target_app_monitoring"
    private const val SELECTED_TARGETS = "selected_target_ids"
    private const val EXPLICIT_USAGE_CONSENT = "explicit_usage_consent"
    private const val PROTECTED_PACKAGES = "protected_packages"
    private const val INSTALLED_CACHE_MS = 30_000L

    enum class TargetApp(val id: String, val displayName: String, val packageNames: Set<String>, val category: String = "Social Video") {
        INSTAGRAM("instagram", "Instagram", setOf(
            "com.instagram.android",
            "com.instagram.lite",
            "com.instander.android",
            "com.aeroinsta.android"
        )),
        YOUTUBE("youtube", "YouTube", setOf(
            "com.google.android.youtube",
            "com.android.youtube",
            "app.revanced.android.youtube",
            "app.rvx.android.youtube",
            "com.google.android.apps.youtube.mango"
        )),
        SAMSUNG_MUSIC("samsung_music", "Samsung Music", setOf("com.sec.android.app.music"), "Music/Audio"),
        SPOTIFY("spotify", "Spotify", setOf("com.spotify.music", "com.spotify.lite"), "Music/Audio"),
        GAANA("gaana", "Gaana", setOf("com.gaana"), "Music/Audio"),
        JIOSAAVN("jiosaavn", "JioSaavn", setOf("com.jio.media.jiobeats"), "Music/Audio");

        val isMusic: Boolean get() = category == "Music/Audio"
    }

    data class InstalledTarget(
        val target: TargetApp,
        val installedPackages: Set<String>,
        val selected: Boolean
    ) {
        val available: Boolean get() = installedPackages.isNotEmpty()
    }

    data class Observation(
        val status: String,
        val target: TargetApp?,
        val packageName: String?,
        val reason: String
    ) {
        val shouldMonitor: Boolean get() = status == MONITOR
    }

    private val byId = TargetApp.values().associateBy { it.id }
    private val byPackage = TargetApp.values().flatMap { target -> target.packageNames.map { it to target } }.toMap()
    private val cacheLock = Any()
    @Volatile private var installedCache: Map<String, Set<String>>? = null
    @Volatile private var installedCacheAt = 0L
    @Volatile private var installedCacheOwner = ""

    /** Android package identity is exact and case-sensitive. Display-name aliases are not identities. */
    fun targetForPackage(packageName: String?): TargetApp? = byPackage[packageName]

    fun isTargetPackage(packageName: String?): Boolean = targetForPackage(packageName) != null

    fun targetForId(id: String): TargetApp? = byId[id]

    val DEFAULT_FINANCIAL_PACKAGES = setOf(
        // Navi
        "com.navi.mutualfunds",
        "com.navi.navi",
        // UPI & Payment Apps
        "com.google.android.apps.nbu.paisa.user",
        "com.phonepe.app",
        "net.one97.paytm",
        "in.org.npci.upiapp",
        "com.dreamplug.androidapp",
        "com.amazon.mShop.android.shopping",
        "com.mobikwik_new",
        "com.freecharge.android",
        // Banking Applications
        "com.sbi.lotusintouch",
        "com.sbi.upi",
        "com.csam.icici.bank.imobile",
        "com.snapwork.hdfc",
        "com.hdfcbank.payzapp",
        "com.axis.mobile",
        "com.msf.kbank.mobile",
        "com.bankofbaroda.mconnect",
        "com.pnbebank.mbanking",
        "com.canarabank.ai1",
        "com.indusind.indie",
        "com.rblbank.mobank",
        "com.idfcfirstbank.optimus",
        "com.aufinbank.aubank",
        "com.federalbank.fedmobile",
        "com.unionbank.online",
        // Credential Managers
        "com.x8bit.bitwarden",
        "com.onepassword.android",
        "com.dashlane",
        "com.lastpass.lpandroid",
        "keepass2android.keepass2android",
        // System Settings & Recovery
        "com.android.settings",
        "com.google.android.packageinstaller",
        "com.android.packageinstaller"
    )

    fun protectedPackages(context: Context): Set<String> {
        val userSaved = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .getStringSet(PROTECTED_PACKAGES, emptySet()).orEmpty().toSet()
        return DEFAULT_FINANCIAL_PACKAGES + userSaved
    }

    fun saveProtectedPackages(context: Context, packages: Set<String>) {
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit()
            .putStringSet(PROTECTED_PACKAGES, packages.filter { Regex("[A-Za-z0-9_]+(\\.[A-Za-z0-9_]+)+").matches(it) }.toSet()).apply()
    }

    // Financial exclusions are always enforced. The legacy financial_safe_mode
    // preference is left in storage for compatibility, but no longer disables
    // monitoring or overlays for unrelated, selected entertainment apps.

    // Non-target packages and financial/UPI apps are protected by default, regardless of their name/category.
    fun isProtected(packageName: String?, exclusions: Set<String> = emptySet()): Boolean =
        packageName.isNullOrBlank() || packageName in DEFAULT_FINANCIAL_PACKAGES || FinancialSafePause.isProtectedFinancialPackage(null, packageName) || targetForPackage(packageName) == null || packageName in exclusions

    fun sanitizeSelection(ids: Set<String>): Set<String> = ids.intersect(byId.keys)

    val DEFAULT_SELECTED_TARGETS = setOf("youtube", "samsung_music", "instagram", "spotify")

    /** No saved choice is not consent. An explicitly empty choice stays empty. */
    fun resolveSelectedTargetIds(saved: Set<String>?): Set<String> = sanitizeSelection(saved.orEmpty())

    fun selectedTargetIds(context: Context): Set<String> {
        val prefs = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
        val saved = prefs.getStringSet(SELECTED_TARGETS, null)
        return resolveSelectedTargetIds(saved)
    }

    fun selectedTargets(context: Context): Set<TargetApp> = selectedTargetIds(context).mapNotNull { byId[it] }.toSet()

    fun saveSelectedTargets(context: Context, ids: Set<String>) {
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit()
            .putStringSet(SELECTED_TARGETS, sanitizeSelection(ids)).apply()
    }

    fun hasUsageConsent(context: Context): Boolean = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
        .getBoolean(EXPLICIT_USAGE_CONSENT, false)

    fun setUsageConsent(context: Context, accepted: Boolean) {
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit()
            .putBoolean(EXPLICIT_USAGE_CONSENT, accepted).apply()
    }

    fun invalidateInstalledPackages() {
        synchronized(cacheLock) {
            installedCache = null
            installedCacheAt = 0L
            installedCacheOwner = ""
        }
    }

    /**
     * Multi-strategy installed package check. Correctly handles pre-installed system apps,
     * deep-sleeping/suspended apps (where app.enabled is false), multi-user profiles, and Android 11+ visibility.
     */
    private fun isPackageInstalled(context: Context, packageName: String): Boolean {
        val pm = context.packageManager

        // 1. PackageInfo query including uninstalled/disabled/frozen components
        try {
            val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                (PackageManager.MATCH_UNINSTALLED_PACKAGES or PackageManager.MATCH_DISABLED_COMPONENTS).toLong()
            } else {
                @Suppress("DEPRECATION")
                (PackageManager.GET_UNINSTALLED_PACKAGES or PackageManager.GET_DISABLED_COMPONENTS).toLong()
            }
            val info = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                pm.getPackageInfo(packageName, PackageManager.PackageInfoFlags.of(flags))
            } else {
                @Suppress("DEPRECATION")
                pm.getPackageInfo(packageName, flags.toInt())
            }
            if (info.packageName == packageName && (info.applicationInfo?.flags ?: 0) and android.content.pm.ApplicationInfo.FLAG_INSTALLED != 0) return true
        } catch (_: PackageManager.NameNotFoundException) {
        } catch (_: Exception) {}

        // 2. ApplicationInfo query including uninstalled/disabled/frozen components
        try {
            val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                (PackageManager.MATCH_UNINSTALLED_PACKAGES or PackageManager.MATCH_DISABLED_COMPONENTS).toLong()
            } else {
                @Suppress("DEPRECATION")
                (PackageManager.GET_UNINSTALLED_PACKAGES or PackageManager.GET_DISABLED_COMPONENTS).toLong()
            }
            val app = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                pm.getApplicationInfo(packageName, PackageManager.ApplicationInfoFlags.of(flags))
            } else {
                @Suppress("DEPRECATION")
                pm.getApplicationInfo(packageName, flags.toInt())
            }
            if (app.packageName == packageName && app.flags and android.content.pm.ApplicationInfo.FLAG_INSTALLED != 0) return true
        } catch (_: PackageManager.NameNotFoundException) {
        } catch (_: Exception) {}

        // 3. Fallback standard ApplicationInfo query
        try {
            val app = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                pm.getApplicationInfo(packageName, PackageManager.ApplicationInfoFlags.of(0))
            } else {
                @Suppress("DEPRECATION")
                pm.getApplicationInfo(packageName, 0)
            }
            if (app.packageName == packageName && app.flags and android.content.pm.ApplicationInfo.FLAG_INSTALLED != 0) return true
        } catch (_: PackageManager.NameNotFoundException) {
        } catch (_: Exception) {}

        // 4. Fallback launch intent check
        try {
            if (pm.getLaunchIntentForPackage(packageName) != null) return true
        } catch (_: Exception) {}

        return false
    }

    private fun resolveInstalledPackages(context: Context, forceRefresh: Boolean): Map<String, Set<String>> {
        val now = SystemClock.elapsedRealtime()
        installedCache?.takeIf {
            !forceRefresh && installedCacheOwner == context.packageName &&
                now >= installedCacheAt && now - installedCacheAt <= INSTALLED_CACHE_MS
        }?.let { return it }
        return synchronized(cacheLock) {
            val result = TargetApp.values().associate { target ->
                target.id to target.packageNames.filter { packageName ->
                    isPackageInstalled(context, packageName)
                }.toSet()
            }
            installedCache = result
            installedCacheAt = now
            installedCacheOwner = context.packageName
            result
        }
    }

    fun installedTargets(context: Context, forceRefresh: Boolean = false): List<InstalledTarget> {
        val selected = selectedTargetIds(context)
        val installed = resolveInstalledPackages(context, forceRefresh)
        return TargetApp.values().map { target -> InstalledTarget(target, installed[target.id].orEmpty(), target.id in selected) }
    }

    fun canReadUsage(context: Context): Boolean {
        if (!hasUsageConsent(context)) return false
        val selected = selectedTargetIds(context)
        if (selected.isEmpty()) return false
        if (StrictAccessibilityService.activeInstance != null) return true
        if (!HardwareDeviceVerifier.hasUsageStatsPermission(context)) return false
        return resolveInstalledPackages(context, false).any { (id, packages) -> id in selected && packages.isNotEmpty() }
    }

    /** Used before reading any target statistics or acting on a foreground observation. */
    fun selectedTarget(context: Context, packageName: String?): TargetApp? {
        if (FinancialSafePause.isPauseActive(context) || isProtected(packageName, protectedPackages(context))) return null
        val target = targetForPackage(packageName) ?: return null
        if (!hasUsageConsent(context) || target.id !in selectedTargetIds(context)) return null
        val hasPermission = StrictAccessibilityService.activeInstance != null || HardwareDeviceVerifier.hasUsageStatsPermission(context)
        if (!hasPermission) return null
        return target
    }

    fun mayMonitor(context: Context, packageName: String?): Boolean =
        !FinancialSafePause.isPauseActive(context) && selectedTarget(context, packageName) != null

    fun monitoringStatus(context: Context): String {
        val graceStatus = AccessibilityGraceManager.getUiStatus(context)
        if (graceStatus.isInterrupted ||
            graceStatus.statusText.startsWith("Accessibility interrupted") ||
            graceStatus.statusText.startsWith("Grace period expired") ||
            graceStatus.statusText.startsWith("Penalty recorded") ||
            graceStatus.statusText == "Usage Access required to verify restricted-app usage" ||
            graceStatus.statusText == "Financial Safe Pause — excluded app" ||
            graceStatus.statusText == "Monitoring restored"
        ) {
            return graceStatus.statusText
        }

        return when {
            FinancialSafePause.isPauseActive(context) -> "Financial Safe Pause — excluded app"
            !hasUsageConsent(context) -> "Monitoring off: explicit consent has not been given."
            selectedTargetIds(context).isEmpty() -> "Monitoring off: no target apps are selected."
            !HardwareDeviceVerifier.hasUsageStatsPermission(context) && StrictAccessibilityService.activeInstance == null ->
                "Usage Access required to verify restricted-app usage"
            !canReadUsage(context) -> "Monitoring unavailable: no selected app is installed and visible."
            StrictAccessibilityService.activeInstance == null ->
                if (!HardwareDeviceVerifier.isAccessibilityServiceEnabled(context, StrictAccessibilityService::class.java)) {
                    graceStatus.statusText
                } else {
                    "Accessibility active"
                }
            !StrictEnforcementService.isServiceRunning() -> "Accessibility active; background monitor stopped."
            else -> "Accessibility active"
        }
    }

    fun evaluate(
        context: Context,
        packageName: String?,
        observedAtMs: Long,
        nowMs: Long = System.currentTimeMillis(),
        confirmedForeground: Boolean = true
    ): Observation {
        if (FinancialSafePause.isPauseActive(context, nowMs) || isProtected(packageName, protectedPackages(context)))
            return Observation(if (packageName.isNullOrBlank()) UNKNOWN else IGNORE, null, packageName, "Protected or unresolved foreground app.")
        val target = targetForPackage(packageName)
        // The fast path for unrelated apps does not request permissions or query packages.
        if (!packageName.isNullOrBlank() && target == null) return Observation(IGNORE, null, packageName, "Not a target app.")
        val selected = selectedTargetIds(context)
        val consented = hasUsageConsent(context)
        val hasPermission = consented && selected.isNotEmpty() && (StrictAccessibilityService.activeInstance != null || HardwareDeviceVerifier.hasUsageStatsPermission(context))
        val installed = if (hasPermission && target != null && target.id in selected) {
            val fromCache = resolveInstalledPackages(context, false)[target.id].orEmpty()
            if (!packageName.isNullOrBlank() && packageName in target.packageNames) {
                fromCache + packageName
            } else fromCache
        } else emptySet()
        return decide(packageName, observedAtMs, nowMs, selected, installed, consented, hasPermission, confirmedForeground)
    }

    /** Pure policy for deterministic tests and for detectors with externally obtained facts. */
    fun decide(
        packageName: String?,
        observedAtMs: Long,
        nowMs: Long,
        selectedIds: Set<String>,
        installedPackages: Set<String>,
        usageConsent: Boolean,
        usagePermissionGranted: Boolean,
        confirmedForeground: Boolean = true,
        protectedPackages: Set<String> = emptySet()
    ): Observation {
        val target = targetForPackage(packageName)
        if (packageName.isNullOrBlank()) return Observation(UNKNOWN, null, packageName, "Foreground app is unknown.")
        if (packageName in DEFAULT_FINANCIAL_PACKAGES || packageName in protectedPackages) return Observation(IGNORE, null, packageName, "Protected app exclusion.")
        if (target == null) return Observation(IGNORE, null, packageName, "Not a target app.")
        if (!usageConsent) return Observation(IGNORE, target, packageName, "Monitoring consent is off.")
        if (target.id !in sanitizeSelection(selectedIds)) return Observation(IGNORE, target, packageName, "This target is not selected.")
        if (!usagePermissionGranted) return Observation(UNKNOWN, target, packageName, "Usage Access is not granted.")
        if (packageName !in installedPackages) return Observation(IGNORE, target, packageName, "The selected package is not installed or visible.")
        if (!confirmedForeground || observedAtMs <= 0L || nowMs < observedAtMs || nowMs - observedAtMs > MAX_FOREGROUND_AGE_MS)
            return Observation(UNKNOWN, target, packageName, "Foreground observation is missing, stale or unconfirmed.")
        return Observation(MONITOR, target, packageName, "Fresh selected-app foreground observation with consent and Usage Access.")
    }
}
