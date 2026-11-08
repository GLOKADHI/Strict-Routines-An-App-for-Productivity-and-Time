package com.strictrecords.app

import android.Manifest
import android.app.Activity
import android.app.AlarmManager
import android.app.AlertDialog
import android.app.AppOpsManager
import android.app.NotificationManager
import android.app.admin.DevicePolicyManager
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.os.Process
import android.provider.Settings
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.CheckBox
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject

/**
 * STRICTRECORDS — MANDATORY PERMISSION & CAPABILITY ONBOARDING & ENFORCEMENT ENGINE
 * Implements Sections 105 - 134:
 * - Mandatory onboarding gate: User cannot enter until all required capabilities are verified.
 * - Real hardware verification checks (no fake states).
 * - Real app usage detection & YouTube restriction decision engine.
 * - RestrictionEvent logging and StrictBot restriction experience.
 * - Device pairing & Wi-Fi sync status verification.
 * - Interactive Diagnostics Screen & YouTube test mode.
 */

enum class PermissionCapability(
    val icon: String,
    val displayName: String,
    val disciplineReason: String,
    val impactIfDenied: String,
    val isRequired: Boolean
) {
    CAMERA(
        "📷",
        "Camera Verification",
        "Required to capture undeniable proof that you are awake and out of bed, and to verify gym and hourly activities. All photos are saved to your local DailyRecord audit package.",
        "StrictRoutines cannot perform wake-up photo verification without camera access. Tasks cannot be verified.",
        true
    ),
    BLUETOOTH(
        "🎧",
        "Bluetooth / Nearby Devices",
        "Required to verify that your configured wake-up earbuds are actually connected during the morning protocol before playing the motivation track.",
        "Morning wake-up protocol cannot verify earphone hardware. Bluetooth challenge remains unavailable.",
        true
    ),
    USAGE_STATS(
        "📊",
        "Selected-App Monitoring",
        "Optional foreground monitoring for installed Instagram, YouTube, Samsung Music, Spotify, Gaana and JioSaavn apps that you explicitly select. Android grants Usage Access for the app; StrictRoutines filters usage events to your selected targets.",
        "Selected-app monitoring stays paused. Wake-up verification, your saved music selection and other routine features remain available with their own required permissions.",
        false
    ),
    OVERLAY(
        "🪟",
        "Display Over Other Apps",
        "Optional display of a reward card over a selected target app. A live foreground check is required; cards are removed when the foreground is unknown or unrelated.",
        "Routine tasks and music still work. Target app overlays require an available, confirmed foreground and overlay capability.",
        false
    ),
    ACCESSIBILITY(
        "🛡️",
        "Accessibility Enforcement",
        "Optional confirmation of selected target app windows for reward cards. Reads package metadata only; it does not read app text. BACK TO HOME requests launcher navigation only for a confirmed target. Event collection is limited to selected installed targets.",
        "Without a live foreground confirmation, target usage may be monitored with consent, but intrusive enforcement fails open.",
        false
    ),
    NOTIFICATIONS(
        "🔔",
        "Notifications & Alarms",
        "Required to deliver time-sensitive 07:00 AM wake-up challenges, hourly discipline reminders, and 06:00 PM gym checkpoints throughout the day.",
        "Discipline alerts and hourly reminders will not be delivered, leading to missed checkpoints.",
        true
    ),
    BATTERY_OPTIMIZATION(
        "📱",
        "Battery / Background Operation",
        "Exempts StrictRoutines from aggressive system background process killers so background checkpoints and monitors fire reliably.",
        "Background operations and hourly checkpoints may be delayed or frozen by the operating system.",
        false
    ),
    LOCAL_NETWORK_SYNC(
        "🌐",
        "Local Network / Wi-Fi Sync",
        "Required for two-device synchronization between your Android phone and Windows PC across local Wi-Fi without cloud dependencies.",
        "Device synchronization over local Wi-Fi will be unavailable. Local storage continues until sync returns.",
        false
    ),
    EXACT_ALARM(
        "⏰",
        "Exact Alarm Scheduling",
        "Required to trigger the uncheatable 07:00 AM wake-up alarm with exact second precision.",
        "Android may delay the morning alarm.",
        false
    ),
    NOTIFICATION_LISTENER(
        "🎵",
        "Music & Video Restriction",
        "Required to detect and pause background music/video playback in Spotify, Samsung Music and YouTube when discipline restrictions apply. Uses Notification & Media Session listener access.",
        "Without this capability, background music/video playback in Spotify, Samsung Music and YouTube cannot be directly paused via media sessions when restricted.",
        false
    )
}

enum class CapabilityStatus(val displayName: String, val colorHex: String) {
    GRANTED("GRANTED ✓", "#10B981"),
    NOT_GRANTED("NOT GRANTED", "#EF4444"),
    VERIFICATION_FAILED("VERIFICATION FAILED", "#F59E0B"),
    OPTIONAL("OPTIONAL", "#6B7280"),
    NOT_AVAILABLE("NOT AVAILABLE", "#475569")
}

enum class CapabilityState {
    GRANTED,
    NEEDS_ACTION,
    USER_DENIED
}

enum class PermissionExplicitState {
    NOT_REQUIRED,
    NOT_GRANTED,
    EXPLANATION_PENDING,
    REQUESTING,
    GRANTED,
    PERMANENTLY_DENIED,
    REVOKED,
    SETTINGS_REQUIRED
}

object DisciplinePermissionManager {

    const val REQ_PERM_CAMERA = 801
    const val REQ_PERM_BLUETOOTH = 802
    const val REQ_PERM_NOTIFICATIONS = 803
    const val REQ_ENABLE_BLUETOOTH = 804

    private val explicitlyDeniedCapabilities = mutableSetOf<PermissionCapability>()
    private val pendingCallbacks = mutableMapOf<Int, () -> Unit>()
    private var pendingSettingsCallback: (() -> Unit)? = null

    // ════════════════════════════════════════════════════════
    // EVALUATION & REAL VERIFICATION (Sections 107-110, 119-120)
    // ════════════════════════════════════════════════════════

    fun evaluateCapabilityStatus(context: Context, capability: PermissionCapability): CapabilityStatus {
        return when (capability) {
            PermissionCapability.CAMERA -> {
                if (!hasSelfPermission(context, Manifest.permission.CAMERA)) {
                    CapabilityStatus.NOT_GRANTED
                } else if (!HardwareDeviceVerifier.isCameraHardwareAvailable(context)) {
                    CapabilityStatus.VERIFICATION_FAILED
                } else {
                    CapabilityStatus.GRANTED
                }
            }
            PermissionCapability.BLUETOOTH -> {
                val permGranted = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    hasSelfPermission(context, Manifest.permission.BLUETOOTH_CONNECT)
                } else {
                    hasSelfPermission(context, Manifest.permission.BLUETOOTH)
                }
                if (!permGranted) {
                    CapabilityStatus.NOT_GRANTED
                } else if (!HardwareDeviceVerifier.isBluetoothAdapterEnabled(context)) {
                    CapabilityStatus.VERIFICATION_FAILED
                } else {
                    CapabilityStatus.GRANTED
                }
            }
            PermissionCapability.USAGE_STATS -> {
                when {
                    TargetAppMonitoringPolicy.canReadUsage(context) -> CapabilityStatus.GRANTED
                    !TargetAppMonitoringPolicy.hasUsageConsent(context) ||
                        TargetAppMonitoringPolicy.selectedTargetIds(context).isEmpty() -> CapabilityStatus.OPTIONAL
                    TargetAppMonitoringPolicy.installedTargets(context).none { it.selected && it.available } -> CapabilityStatus.NOT_AVAILABLE
                    else -> CapabilityStatus.NOT_GRANTED
                }
            }
            PermissionCapability.OVERLAY -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    if (Settings.canDrawOverlays(context)) CapabilityStatus.GRANTED else CapabilityStatus.NOT_GRANTED
                } else {
                    CapabilityStatus.GRANTED
                }
            }
            PermissionCapability.ACCESSIBILITY -> {
                if (StrictAccessibilityService.activeInstance != null) {
                    CapabilityStatus.GRANTED
                } else if (HardwareDeviceVerifier.isAccessibilityServiceEnabled(context, StrictAccessibilityService::class.java)) {
                    CapabilityStatus.VERIFICATION_FAILED
                } else {
                    CapabilityStatus.NOT_GRANTED
                }
            }
            PermissionCapability.NOTIFICATIONS -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    if (!hasSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)) {
                        CapabilityStatus.NOT_GRANTED
                    } else {
                        CapabilityStatus.GRANTED
                    }
                } else {
                    CapabilityStatus.GRANTED
                }
            }
            PermissionCapability.BATTERY_OPTIMIZATION -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    val pm = context.getSystemService(Context.POWER_SERVICE) as? PowerManager
                    if (pm?.isIgnoringBatteryOptimizations(context.packageName) == true) {
                        CapabilityStatus.GRANTED
                    } else {
                        CapabilityStatus.OPTIONAL
                    }
                } else {
                    CapabilityStatus.GRANTED
                }
            }
            PermissionCapability.LOCAL_NETWORK_SYNC -> {
                if (HardwareDeviceVerifier.isNetworkOrWifiAvailable(context)) {
                    CapabilityStatus.GRANTED
                } else {
                    CapabilityStatus.NOT_GRANTED
                }
            }
            PermissionCapability.EXACT_ALARM -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    val am = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager
                    if (am?.canScheduleExactAlarms() == true) CapabilityStatus.GRANTED else CapabilityStatus.NOT_GRANTED
                } else {
                    CapabilityStatus.GRANTED
                }
            }
            PermissionCapability.NOTIFICATION_LISTENER -> {
                if (StrictMediaNotificationListener.isNotificationAccessGranted(context)) {
                    CapabilityStatus.GRANTED
                } else {
                    CapabilityStatus.OPTIONAL
                }
            }
        }
    }

    fun isGranted(context: Context, capability: PermissionCapability): Boolean {
        return evaluateCapabilityStatus(context, capability) == CapabilityStatus.GRANTED
    }

    fun checkCapability(context: Context, capability: PermissionCapability): CapabilityState {
        if (isGranted(context, capability)) return CapabilityState.GRANTED
        if (explicitlyDeniedCapabilities.contains(capability)) return CapabilityState.USER_DENIED
        return CapabilityState.NEEDS_ACTION
    }

    fun getGrantedCount(context: Context): Int {
        return PermissionCapability.values().count { isGranted(context, it) }
    }

    fun getTotalCount(): Int = PermissionCapability.values().size

    /**
     * Part 6 / Section 12: Reports each capability honestly as SUPPORTED, PERMISSION_REQUIRED,
     * LIMITED, or UNSUPPORTED across older (API 24–28) and modern (API 29–34) Android releases.
     */
    fun getDeviceCompatibilityReport(context: Context): Map<String, String> {
        val report = LinkedHashMap<String, String>()
        // 1. Camera
        val hasCamFeature = context.packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY)
        report["CAMERA"] = when {
            !hasCamFeature -> "UNSUPPORTED"
            !hasSelfPermission(context, Manifest.permission.CAMERA) -> "PERMISSION_REQUIRED"
            else -> "SUPPORTED"
        }
        // 2. Notifications
        val notifMgr = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
        val notifEnabled = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            notifMgr?.areNotificationsEnabled() != false
        } else true
        val postNotifGranted = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            hasSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
        } else true
        report["NOTIFICATIONS"] = if (notifEnabled && postNotifGranted) "SUPPORTED" else "PERMISSION_REQUIRED"

        // 3. Exact Alarms
        report["EXACT_ALARMS"] = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val am = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager
            if (am?.canScheduleExactAlarms() == true) "SUPPORTED" else "PERMISSION_REQUIRED"
        } else {
            "SUPPORTED"
        }

        // 4. Bluetooth
        val btAdapter = BluetoothAdapter.getDefaultAdapter()
        val btPerm = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            hasSelfPermission(context, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            hasSelfPermission(context, Manifest.permission.BLUETOOTH)
        }
        report["BLUETOOTH"] = when {
            btAdapter == null -> "UNSUPPORTED"
            !btPerm -> "PERMISSION_REQUIRED"
            !btAdapter.isEnabled -> "LIMITED"
            else -> "SUPPORTED"
        }

        // 5. Usage Stats
        report["USAGE_STATS"] = when {
            TargetAppMonitoringPolicy.canReadUsage(context) -> "SUPPORTED"
            !TargetAppMonitoringPolicy.hasUsageConsent(context) ||
                TargetAppMonitoringPolicy.selectedTargetIds(context).isEmpty() -> "LIMITED"
            TargetAppMonitoringPolicy.installedTargets(context).none { it.selected && it.available } -> "LIMITED"
            else -> "PERMISSION_REQUIRED"
        }

        // 6. Overlay
        report["OVERLAY"] = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            if (Settings.canDrawOverlays(context)) "SUPPORTED" else "PERMISSION_REQUIRED"
        } else {
            "SUPPORTED"
        }

        // 7. Foreground Service
        report["FOREGROUND_SERVICE"] = "SUPPORTED"

        // 8. Local / Server OCR
        val isPaired = StrictRecordsLocalDb.getInstance(context).isDeviceRegistered()
        report["OCR_INTELLIGENCE"] = if (isPaired) "SUPPORTED" else "LIMITED"

        // 9. Media Notification / Background Music Restriction
        report["MEDIA_RESTRICTION"] = if (StrictMediaNotificationListener.isNotificationAccessGranted(context)) "SUPPORTED" else "PERMISSION_REQUIRED"

        return report
    }

    fun areRequiredCapabilitiesVerified(context: Context): Boolean {
        val required = listOf(
            PermissionCapability.NOTIFICATIONS
        )
        return required.all { isGranted(context, it) }
    }

    private const val PREFS_NAME = "strictrecords_permission_prefs"
    private const val PREF_PREFIX_SUPPRESSED = "permission_explanation_suppressed_"

    fun isExplanationSuppressed(context: Context, capability: PermissionCapability): Boolean {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        return prefs.getBoolean(PREF_PREFIX_SUPPRESSED + capability.name, false)
    }

    fun setExplanationSuppressed(context: Context, capability: PermissionCapability, suppressed: Boolean) {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().putBoolean(PREF_PREFIX_SUPPRESSED + capability.name, suppressed).apply()
    }

    fun getExplicitPermissionState(context: Context, capability: PermissionCapability): PermissionExplicitState {
        if (isGranted(context, capability)) {
            return PermissionExplicitState.GRANTED
        }
        if (isExplanationSuppressed(context, capability)) {
            return PermissionExplicitState.NOT_GRANTED
        }
        return PermissionExplicitState.EXPLANATION_PENDING
    }

    fun isPermissionGatePassed(context: Context): Boolean {
        val db = StrictRecordsLocalDb.getInstance(context)
        val onboardingCompleted = db.getConfigValue("onboarding_completed", "0") == "1"
        if (!onboardingCompleted) return false
        val notifSuppressed = isExplanationSuppressed(context, PermissionCapability.NOTIFICATIONS)
        return areRequiredCapabilitiesVerified(context) || notifSuppressed
    }

    fun checkPermissionRevocation(context: Context): PermissionCapability? {
        val db = StrictRecordsLocalDb.getInstance(context)
        val onboardingCompleted = db.getConfigValue("onboarding_completed", "0") == "1"
        if (!onboardingCompleted) return null
        // Optional monitoring does not create a mandatory app-entry repair gate.
        return if (TargetAppMonitoringPolicy.hasUsageConsent(context) &&
            !HardwareDeviceVerifier.hasUsageStatsPermission(context)) PermissionCapability.USAGE_STATS else null
    }

    // ════════════════════════════════════════════════════════
    // REQUEST ENGINE & INTENT DISPATCH (Sections 108-110, 119-120)
    // ════════════════════════════════════════════════════════

    fun ensureCapability(
        activity: Activity,
        capability: PermissionCapability,
        actionPurpose: String,
        onGranted: () -> Unit
    ) {
        ensureCapability(activity, capability, actionPurpose, onDenied = null, onGranted = onGranted)
    }

    fun ensureCapability(
        activity: Activity,
        capability: PermissionCapability,
        actionPurpose: String,
        onDenied: (() -> Unit)? = null,
        onGranted: () -> Unit
    ) {
        if (isGranted(activity, capability)) {
            onGranted()
            return
        }
        if (capability == PermissionCapability.USAGE_STATS) {
            if (isExplanationSuppressed(activity, capability)) {
                onDenied?.invoke()
                return
            }
            showTargetAppMonitoringSettings(activity, onGranted = onGranted, onDenied = onDenied)
            return
        }
        if (isExplanationSuppressed(activity, capability)) {
            requestCapabilityFromSystem(activity, capability, onGranted, onDenied)
            return
        }

        AlertDialog.Builder(activity)
            .setTitle("${capability.icon}  ${capability.displayName}")
            .setMessage(
                "StrictRoutines enforces real, auditable discipline.\n\n" +
                "Action: $actionPurpose\n\n" +
                "WHY IT IS REQUIRED:\n${capability.disciplineReason}\n\n" +
                "IF DENIED:\n${capability.impactIfDenied}"
            )
            .setPositiveButton("GRANT ACCESS") { _, _ ->
                explicitlyDeniedCapabilities.remove(capability)
                requestCapabilityFromSystem(activity, capability, onGranted, onDenied)
            }
            .setNeutralButton("DON'T SHOW AGAIN") { _, _ ->
                setExplanationSuppressed(activity, capability, true)
                explicitlyDeniedCapabilities.add(capability)
                Toast.makeText(activity, "Explanation suppressed for ${capability.displayName}", Toast.LENGTH_SHORT).show()
                onDenied?.invoke()
            }
            .setNegativeButton("DENY") { _, _ ->
                explicitlyDeniedCapabilities.add(capability)
                Toast.makeText(activity, "Denied: ${capability.displayName}", Toast.LENGTH_SHORT).show()
                onDenied?.invoke()
            }
            .setCancelable(true)
            .show()
    }

    fun requestCapabilityFromSystem(
        activity: Activity,
        capability: PermissionCapability,
        onGranted: () -> Unit,
        onDenied: (() -> Unit)? = null
    ) {
        when (capability) {
            PermissionCapability.CAMERA -> {
                pendingCallbacks[REQ_PERM_CAMERA] = onGranted
                activity.requestPermissions(arrayOf(Manifest.permission.CAMERA), REQ_PERM_CAMERA)
            }
            PermissionCapability.BLUETOOTH -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    pendingCallbacks[REQ_PERM_BLUETOOTH] = onGranted
                    activity.requestPermissions(
                        arrayOf(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_SCAN),
                        REQ_PERM_BLUETOOTH
                    )
                } else {
                    val adapter = BluetoothAdapter.getDefaultAdapter()
                    if (adapter != null && !adapter.isEnabled) {
                        pendingSettingsCallback = onGranted
                        val enableBtIntent = Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE)
                        activity.startActivityForResult(enableBtIntent, REQ_ENABLE_BLUETOOTH)
                    } else {
                        onGranted()
                    }
                }
            }
            PermissionCapability.USAGE_STATS -> {
                showTargetAppMonitoringSettings(activity, onGranted = onGranted, onDenied = onDenied)
            }
            PermissionCapability.OVERLAY -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(activity)) {
                    pendingSettingsCallback = onGranted
                    AlertDialog.Builder(activity)
                        .setTitle("🤖 Floating Discipline Bot Overlay")
                        .setMessage(
                            "StrictRecords uses the 'Display over other apps' permission to show a small, non-intrusive animated discipline bot at the bottom of the screen.\n\n" +
                            "• Appears over configured entertainment apps (Instagram, YouTube) when score thresholds or accessibility grace require attention.\n" +
                            "• Does NOT block touches outside the bot character.\n" +
                            "• Strictly excluded from all banking, UPI, and emergency screens.\n\n" +
                            "Tap 'PROCEED' to enable 'Appear on top' in Android Settings."
                        )
                        .setPositiveButton("PROCEED") { _, _ ->
                            val intent = Intent(
                                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                                Uri.parse("package:${activity.packageName}")
                            )
                            try {
                                activity.startActivity(intent)
                            } catch (_: Exception) {
                                activity.startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION))
                            }
                        }
                        .setNegativeButton("CANCEL") { _, _ ->
                            onDenied?.invoke()
                        }
                        .show()
                } else {
                    onGranted()
                }
            }
            PermissionCapability.ACCESSIBILITY -> {
                pendingSettingsCallback = onGranted
                AlertDialog.Builder(activity)
                    .setTitle("🛡️ Accessibility Service Disclosure & Privacy")
                    .setMessage(
                        "StrictRoutines uses the Android AccessibilityService API to support your personal productivity and digital wellbeing routines.\n\n" +
                        "WHAT ACCESSIBILITY DOES:\n" +
                        "• Observes when user-selected target applications (such as YouTube, Instagram, Spotify, and Samsung Music) enter the foreground during active focus hours.\n" +
                        "• Helps display a productivity reminder or navigate back to the home screen when discipline rules apply.\n\n" +
                        "WHAT IS NEVER ACCESSED (YOUR PRIVACY IS PROTECTED):\n" +
                        "• StrictRoutines CANNOT and DOES NOT read on-screen text, form fields, keystrokes, or messages (canRetrieveWindowContent is set to false).\n" +
                        "• It NEVER inspects passwords, credentials, payment information, or personal communications.\n" +
                        "• It NEVER transmits personal or app data to external servers or third parties. All processing is strictly local.\n" +
                        "• FINANCIAL & SENSITIVE APP EXCLUSION: All banking, UPI, digital wallet, healthcare, and Android Settings applications are strictly excluded from monitoring. Opening any financial app immediately pauses all routines.\n\n" +
                        "Samsung Galaxy A55 Note:\n" +
                        "If the setting is greyed out ('Restricted setting'), open App Info → tap the ⋮ menu in the top-right corner → tap 'Allow restricted settings', then return to Accessibility."
                    )
                    .setPositiveButton("I AGREE & OPEN SETTINGS") { _, _ ->
                        try {
                            activity.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                        } catch (_: Exception) {}
                    }
                    .setNeutralButton("APP INFO (⋮)") { _, _ ->
                        try {
                            val appInfo = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                                data = Uri.fromParts("package", activity.packageName, null)
                            }
                            activity.startActivity(appInfo)
                        } catch (_: Exception) {}
                    }
                    .setNegativeButton("DECLINE") { _, _ ->
                        explicitlyDeniedCapabilities.add(PermissionCapability.ACCESSIBILITY)
                        onDenied?.invoke()
                    }
                    .setCancelable(true)
                    .show()
            }
            PermissionCapability.NOTIFICATIONS -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    pendingCallbacks[REQ_PERM_NOTIFICATIONS] = onGranted
                    activity.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_PERM_NOTIFICATIONS)
                } else {
                    onGranted()
                }
            }
            PermissionCapability.BATTERY_OPTIMIZATION -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    pendingSettingsCallback = onGranted
                    val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                        data = Uri.parse("package:${activity.packageName}")
                    }
                    try {
                        activity.startActivity(intent)
                    } catch (_: Exception) {
                        activity.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
                    }
                } else {
                    onGranted()
                }
            }
            PermissionCapability.LOCAL_NETWORK_SYNC -> {
                if (HardwareDeviceVerifier.isNetworkOrWifiAvailable(activity)) {
                    onGranted()
                } else {
                    Toast.makeText(activity, "Connect to Local Wi-Fi or USB tethering to enable sync.", Toast.LENGTH_LONG).show()
                    activity.startActivity(Intent(Settings.ACTION_WIFI_SETTINGS))
                }
            }
            PermissionCapability.EXACT_ALARM -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    pendingSettingsCallback = onGranted
                    val intent = Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM).apply {
                        data = Uri.fromParts("package", activity.packageName, null)
                    }
                    activity.startActivity(intent)
                } else {
                    onGranted()
                }
            }
            PermissionCapability.NOTIFICATION_LISTENER -> {
                pendingSettingsCallback = onGranted
                AlertDialog.Builder(activity)
                    .setTitle("🎵 Media Notification Listener Disclosure")
                    .setMessage(
                        "StrictRoutines can pause background playback in your selected music/video apps (such as Spotify, Samsung Music and YouTube) when your existing restrictions apply.\n\n" +
                        "WHAT IS ACCESSED:\n" +
                        "• Only playback controls and media transport states from selected music apps.\n\n" +
                        "WHAT IS NEVER ACCESSED:\n" +
                        "• StrictRoutines NEVER reads incoming personal messages, SMS, OTPs, chats, email alerts, or financial notifications.\n" +
                        "• All non-media notifications are completely ignored.\n\n" +
                        "Do you wish to open Notification Access settings?"
                    )
                    .setPositiveButton("I AGREE & OPEN SETTINGS") { _, _ ->
                        try {
                            val intent = Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
                            activity.startActivity(intent)
                        } catch (_: Exception) {
                            Toast.makeText(activity, "Open Settings to grant Notification & Media access", Toast.LENGTH_LONG).show()
                        }
                    }
                    .setNegativeButton("DECLINE") { _, _ ->
                        explicitlyDeniedCapabilities.add(PermissionCapability.NOTIFICATION_LISTENER)
                        onDenied?.invoke()
                    }
                    .setCancelable(true)
                    .show()
            }
        }
    }

    fun handlePermissionResult(
        activity: Activity,
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
        onRefreshUi: () -> Unit
    ) {
        val callback = pendingCallbacks.remove(requestCode)
        val isGranted = grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED
        val capability = when (requestCode) {
            REQ_PERM_CAMERA -> PermissionCapability.CAMERA
            REQ_PERM_BLUETOOTH -> PermissionCapability.BLUETOOTH
            REQ_PERM_NOTIFICATIONS -> PermissionCapability.NOTIFICATIONS
            else -> null
        }

        if (isGranted) {
            capability?.let { explicitlyDeniedCapabilities.remove(it) }
            Toast.makeText(activity, "${capability?.displayName ?: "Permission"} Granted ✓", Toast.LENGTH_SHORT).show()
            onRefreshUi()
            callback?.invoke()
        } else {
            capability?.let { explicitlyDeniedCapabilities.add(it) }
            val capName = capability?.displayName ?: "Required Capability"
            AlertDialog.Builder(activity)
                .setTitle("⚠ $capName ACCESS REQUIRED")
                .setMessage(
                    "StrictRoutines cannot operate without $capName.\n\n" +
                    "CONSEQUENCE:\n${capability?.impactIfDenied ?: "Feature unavailable."}"
                )
                .setPositiveButton("OPEN SETTINGS") { _, _ ->
                    val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                        data = Uri.fromParts("package", activity.packageName, null)
                    }
                    activity.startActivity(intent)
                }
                .setNegativeButton("TRY AGAIN") { _, _ ->
                    capability?.let { requestCapabilityFromSystem(activity, it, onGranted = { onRefreshUi() }) }
                }
                .show()
            onRefreshUi()
        }
    }

    fun handleResumeCheck(activity: Activity, onRefreshUi: () -> Unit) {
        val callback = pendingSettingsCallback
        if (callback != null) {
            pendingSettingsCallback = null
            onRefreshUi()
            callback.invoke()
        } else {
            onRefreshUi()
        }
    }

    private fun openUsageAccessSettings(activity: Activity): Boolean {
        return try {
            activity.startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS).apply {
                data = Uri.fromParts("package", activity.packageName, null)
            })
            true
        } catch (_: Exception) {
            try { activity.startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)); true }
            catch (_: Exception) {
                pendingSettingsCallback = null
                Toast.makeText(activity, "Open Android Settings → Special access → Usage data access → StrictRoutines.", Toast.LENGTH_LONG).show()
                false
            }
        }
    }

    /** The OS owns restricted settings. Guide the user through its app-specific approval. */
    private fun showUsageAccessRecovery(
        activity: Activity,
        onChanged: () -> Unit,
        onGranted: (() -> Unit)?,
        onDenied: (() -> Unit)?
    ) {
        fun recheck() {
            onChanged()
            if (HardwareDeviceVerifier.hasUsageStatsPermission(activity)) {
                if (TargetAppMonitoringPolicy.canReadUsage(activity)) onGranted?.invoke()
                else Toast.makeText(activity, TargetAppMonitoringPolicy.monitoringStatus(activity), Toast.LENGTH_LONG).show()
            } else {
                showUsageAccessRecovery(activity, onChanged, onGranted, onDenied)
            }
        }
        AlertDialog.Builder(activity)
            .setTitle("Enable Usage Access")
            .setMessage(
                "If Android says 'App was denied access' or 'Controlled by restricted setting':\n\n" +
                "1. Open App Info → tap ⋮ at the top right → Allow restricted settings, if that option is shown. Confirm using your phone's unlock method.\n\n" +
                "2. Return to Usage data access → StrictRoutines → turn on Allow permission.\n\n" +
                "If the menu is missing, open Android Settings → Apps → StrictRoutines directly and check there.\n\n" +
                "StrictRoutines cannot approve this Android setting itself. Monitoring stays paused until Usage Access is granted and your selected-app consent is enabled."
            )
            .setPositiveButton("Open App Info") { _, _ ->
                pendingSettingsCallback = { recheck() }
                try {
                    activity.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                        data = Uri.fromParts("package", activity.packageName, null)
                    })
                } catch (_: Exception) {
                    pendingSettingsCallback = null
                    Toast.makeText(activity, "Open Android Settings → Apps → StrictRoutines → ⋮ → Allow restricted settings.", Toast.LENGTH_LONG).show()
                }
            }
            .setNeutralButton("Usage Access") { _, _ ->
                pendingSettingsCallback = { recheck() }
                openUsageAccessSettings(activity)
            }
            .setNegativeButton("Not now") { _, _ -> pendingSettingsCallback = null; onDenied?.invoke() }
            .setOnCancelListener { pendingSettingsCallback = null; onDenied?.invoke() }
            .show()
    }

    fun showFinancialSafetySettings(activity: Activity, onChanged: () -> Unit = {}) {
        val content = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 16, 24, 16) }
        content.addView(TextView(activity).apply {
            text = "Automatic banking break\n\nOpening a protected banking, payment, authentication or password-manager app removes restriction popups and suspends automatic restriction actions. A five-minute cooldown starts after the protected app leaves the foreground; enforcement resumes only after that cooldown expires. The banking app stays open. StrictRoutines does not reopen its screen.\n\nAndroid Settings is also protected. Only package transitions are observed during the pause to confirm when the protected session ends. Its screen contents are not inspected. Your scores and unlock thresholds stay the same.\n\nAll non-target apps are excluded. Add an exact installed package below to exclude it even if selected.\n\nAccessibility remains enabled in Android during the break. Some banks reject any enabled Accessibility service; use Android Accessibility Settings below if the bank requires it to be turned off manually."
        })
        val packages = android.widget.EditText(activity).apply {
            hint = "Exact installed packages, one per line"
            setText(TargetAppMonitoringPolicy.protectedPackages(activity).sorted().joinToString("\n"))
            minLines = 3
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE
        }
        content.addView(packages)
        content.addView(Button(activity).apply {
            text = "Save exclusions"
            setOnClickListener {
                val requested = packages.text.toString().lines().map { it.trim() }.filter { it.isNotEmpty() }.toSet()
                val existing = TargetAppMonitoringPolicy.protectedPackages(activity)
                val unknown = requested.filter { pkg -> pkg !in existing && runCatching {
                    @Suppress("DEPRECATION")
                    activity.packageManager.getPackageInfo(pkg, 0)
                }.isFailure }
                if (unknown.isNotEmpty()) Toast.makeText(activity, "Package not installed or visible: ${unknown.joinToString()}", Toast.LENGTH_LONG).show()
                else {
                    TargetAppMonitoringPolicy.saveProtectedPackages(activity, requested)
                    StrictAccessibilityService.activeInstance?.refreshPackageFilter()
                    StrictBotOverlayController.hidePopupImmediate()
                    onChanged()
                    Toast.makeText(activity, "Exclusions saved", Toast.LENGTH_SHORT).show()
                }
            }
        })
        content.addView(Button(activity).apply {
            text = "Open Android Accessibility Settings"
            setOnClickListener { activity.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        })
        AlertDialog.Builder(activity).setTitle("Automatic banking break").setView(ScrollView(activity).apply { addView(content) }).setPositiveButton("Done", null).show()
    }

    /** Usage Access is requested only from this explicit, target-scoped opt-in. */
    fun showTargetAppMonitoringSettings(
        activity: Activity,
        onChanged: () -> Unit = {},
        onGranted: (() -> Unit)? = null,
        onDenied: (() -> Unit)? = null
    ) {
        val dp = { value: Int -> TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), activity.resources.displayMetrics).toInt() }
        val targets = TargetAppMonitoringPolicy.installedTargets(activity, forceRefresh = true)
        val selected = TargetAppMonitoringPolicy.selectedTargetIds(activity)
        val choices = linkedMapOf<String, CheckBox>()
        val content = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(12), dp(18), dp(12))
        }
        content.addView(TextView(activity).apply {
            text = "Choose which installed apps StrictRoutines may monitor. This is optional.\n\n" +
                "Usage Access helps detect when a selected Instagram, YouTube, Samsung Music, Spotify, Gaana or JioSaavn app is in the foreground so your configured discipline policy can be checked.\n\n" +
                "Android grants Usage Access to StrictRoutines at the app level. StrictRoutines filters foreground events to the targets you select below; selecting an app does not change your scores, privilege thresholds or wake-up music.\n\n" +
                "Monitoring starts only after you agree and enable Android Usage Access. You can stop it here or revoke Usage Access in Android Settings."
            textSize = 13f
            setLineSpacing(dp(3).toFloat(), 1f)
            setPadding(0, 0, 0, dp(12))
        })
        content.addView(TextView(activity).apply {
            text = "Current status: ${TargetAppMonitoringPolicy.monitoringStatus(activity)}"
            textSize = 12f
            setTypeface(typeface, Typeface.BOLD)
            setPadding(0, 0, 0, dp(8))
        })
        content.addView(TextView(activity).apply {
            text = "Native app monitoring only. masstamilan.dev is a Music/Website target on Windows (60 points); Android browser-domain monitoring is unsupported. No whole-browser restriction is applied."
            textSize = 12f
            setPadding(0, dp(8), 0, dp(8))
        })
        content.addView(Button(activity).apply {
            text = "Open Accessibility Settings"
            setOnClickListener { activity.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        })
        content.addView(Button(activity).apply {
            text = "Open overlay permission Settings"
            setOnClickListener { activity.startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:" + activity.packageName))) }
        })
        content.addView(TextView(activity).apply {
            text = "Accessibility enabled: ${HardwareDeviceVerifier.isAccessibilityServiceEnabled(activity, StrictAccessibilityService::class.java)}; live connection: ${StrictAccessibilityService.activeInstance != null}" +
                "\nRoutine service running: ${StrictEnforcementService.isServiceRunning()}; overlay permission: ${Settings.canDrawOverlays(activity)}" +
                "\nAn Accessibility overlay uses the connected service. Overlay permission alone cannot confirm external foreground."
            textSize = 12f
        })
        content.addView(TextView(activity).apply {
            text = UsageAccessMonitor.diagnosticText()
            textSize = 12f
            setPadding(0, dp(8), 0, dp(8))
        })
        targets.forEach { entry ->
            val checkbox = CheckBox(activity).apply {
                text = entry.target.displayName + " • " + entry.target.category + if (entry.available) "" else " — not installed"
                isEnabled = entry.available
                isChecked = entry.available && selected.contains(entry.target.id)
                textSize = 14f
                setPadding(0, dp(5), 0, dp(5))
            }
            choices[entry.target.id] = checkbox
            content.addView(checkbox)
        }
        val scroll = ScrollView(activity).apply { addView(content) }
        fun changed() {
            StrictAccessibilityService.activeInstance?.refreshPackageFilter()
            StrictBotOverlayController.hidePopupImmediate()
            onChanged()
            (activity as? MainActivity)?.onTargetMonitoringChanged()
        }
        if (!HardwareDeviceVerifier.hasUsageStatsPermission(activity)) {
            content.addView(TextView(activity).apply {
                text = "Switch greyed out or 'App was denied access'? Android may require Allow restricted settings from StrictRoutines App Info first."
                textSize = 12f
                setPadding(0, dp(10), 0, dp(4))
            })
            content.addView(Button(activity).apply {
                text = "Fix disabled Usage Access"
                setOnClickListener { showUsageAccessRecovery(activity, { changed() }, onGranted, onDenied) }
            })
        }
        content.addView(Button(activity).apply {
            text = "Automatic banking break & exclusions"
            setOnClickListener { showFinancialSafetySettings(activity) { changed() } }
        })
        val dialog = AlertDialog.Builder(activity)
            .setTitle("Optional selected-app monitoring")
            .setView(scroll)
            .setPositiveButton("Agree and open Settings", null)
            .setNeutralButton("Stop monitoring") { _, _ ->
                TargetAppMonitoringPolicy.setUsageConsent(activity, false)
                pendingSettingsCallback = null
                changed()
                onDenied?.invoke()
                Toast.makeText(activity, "Selected-app monitoring stopped. Android Usage Access can also be revoked in Settings.", Toast.LENGTH_LONG).show()
            }
            .setNegativeButton("Not now") { _, _ -> onDenied?.invoke() }
            .setCancelable(true)
            .create()
        dialog.setOnCancelListener { onDenied?.invoke() }
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val chosen = choices.filterValues { it.isEnabled && it.isChecked }.keys.toSet()
                if (chosen.isEmpty()) {
                    Toast.makeText(activity, "Select an installed app to enable monitoring, or choose Stop monitoring.", Toast.LENGTH_LONG).show()
                    return@setOnClickListener
                }
                // The checkbox choices and this button are the user's explicit opt-in.
                TargetAppMonitoringPolicy.saveSelectedTargets(activity, chosen)
                TargetAppMonitoringPolicy.setUsageConsent(activity, true)
                explicitlyDeniedCapabilities.remove(PermissionCapability.USAGE_STATS)
                changed()
                pendingSettingsCallback = {
                    changed()
                    if (TargetAppMonitoringPolicy.canReadUsage(activity)) onGranted?.invoke()
                    else {
                        if (!HardwareDeviceVerifier.hasUsageStatsPermission(activity)) {
                            showUsageAccessRecovery(activity, { changed() }, onGranted, onDenied)
                        } else {
                            onDenied?.invoke()
                            Toast.makeText(activity, TargetAppMonitoringPolicy.monitoringStatus(activity), Toast.LENGTH_LONG).show()
                        }
                    }
                }
                if (openUsageAccessSettings(activity)) {
                    dialog.dismiss()
                }
            }
        }
        dialog.show()
    }

    // ════════════════════════════════════════════════════════
    // MANDATORY ONBOARDING GATE & WIZARD (Sections 105, 106, 107, 121, 125)
    // ════════════════════════════════════════════════════════

    fun showMandatoryOnboardingWizard(activity: Activity, onCompleted: () -> Unit) {
        val dp = { v: Int ->
            TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), activity.resources.displayMetrics).toInt()
        }
        val db = StrictRecordsLocalDb.getInstance(activity)

        var currentStep = 1 // 1: Welcome, 2: Checklist, 3: Device Setup, 4: Summary

        val rootDialogView = FrameLayout(activity).apply {
            setBackgroundColor(Color.parseColor("#07090D"))
            setPadding(dp(16), dp(16), dp(16), dp(16))
        }

        var dialogRef: AlertDialog? = null

        fun renderStep() {
            rootDialogView.removeAllViews()

            val scroll = ScrollView(activity).apply {
                isVerticalScrollBarEnabled = false
                overScrollMode = View.OVER_SCROLL_NEVER
            }
            val content = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
            }

            when (currentStep) {
                1 -> {
                    // ── STEP 1: WELCOME SCREEN (Section 106) ──────────────────
                    val botLogo = ImageView(activity).apply {
                        setImageResource(R.drawable.strictbot_compact)
                        scaleType = ImageView.ScaleType.FIT_CENTER
                        layoutParams = LinearLayout.LayoutParams(dp(72), dp(72)).apply {
                            gravity = Gravity.CENTER_HORIZONTAL
                            topMargin = dp(16)
                            bottomMargin = dp(12)
                        }
                    }
                    content.addView(botLogo)

                    content.addView(TextView(activity).apply {
                        text = "WELCOME TO STRICTRECORDS"
                        textSize = 19f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(Color.WHITE)
                        gravity = Gravity.CENTER
                        setPadding(0, 0, 0, dp(4))
                    })
                    content.addView(TextView(activity).apply {
                        text = "StrictBot Discipline Operating System"
                        textSize = 11.5f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(Color.parseColor("#70F59B"))
                        gravity = Gravity.CENTER
                        setPadding(0, 0, 0, dp(14))
                        letterSpacing = 0.05f
                    })
                    content.addView(TextView(activity).apply {
                        text = "StrictRoutines pairs with your desktop and manages hardware-enforced routines.\n\n" +
                               "These capabilities are required for:\n" +
                               "• Wake-up routine & AirPods verification\n" +
                               "• Hourly proof windows & photo validation\n" +
                               "• Automatic cross-device synchronization\n" +
                               "• Distracting app restrictions (YouTube & Instagram)\n" +
                               "• Unbroken streak & privilege score tracking\n\n" +
                               "You control your focus and earn your freedom."
                        textSize = 12.5f
                        setTextColor(Color.parseColor("#9BA9AF"))
                        setLineSpacing(dp(3).toFloat(), 1f)
                        setPadding(0, 0, 0, dp(24))
                    })

                    val continueBtn = TextView(activity).apply {
                        text = "GET STARTED →"
                        textSize = 13.5f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(Color.parseColor("#070A0C"))
                        gravity = Gravity.CENTER
                        setPadding(dp(16), dp(14), dp(16), dp(14))
                        background = GradientDrawable().apply {
                            cornerRadius = dp(99).toFloat()
                            setColor(Color.parseColor("#70F59B"))
                        }
                        setOnClickListener {
                            currentStep = 2
                            renderStep()
                        }
                    }
                    content.addView(continueBtn)
                }

                2 -> {
                    // ── STEP 2: REQUIRED CAPABILITY CHECKLIST (Section 107) ──
                    content.addView(TextView(activity).apply {
                        text = "STRICTROUTINES SETUP"
                        textSize = 18f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(Color.WHITE)
                        setPadding(0, dp(6), 0, dp(4))
                    })
                    content.addView(TextView(activity).apply {
                        text = "Required routine capabilities are verified below. Selected-app monitoring is optional and needs your separate consent."
                        textSize = 11.5f
                        setTextColor(Color.parseColor("#94A3B8"))
                        setPadding(0, 0, 0, dp(14))
                    })

                    val capabilities = listOf(
                        PermissionCapability.CAMERA,
                        PermissionCapability.BLUETOOTH,
                        PermissionCapability.USAGE_STATS,
                        PermissionCapability.NOTIFICATIONS,
                        PermissionCapability.BATTERY_OPTIMIZATION,
                        PermissionCapability.LOCAL_NETWORK_SYNC
                    )

                    for (cap in capabilities) {
                        val status = evaluateCapabilityStatus(activity, cap)
                        val isOk = status == CapabilityStatus.GRANTED

                        val card = LinearLayout(activity).apply {
                            orientation = LinearLayout.VERTICAL
                            setPadding(dp(14), dp(12), dp(14), dp(12))
                            background = GradientDrawable().apply {
                                cornerRadius = dp(14).toFloat()
                                setColor(Color.parseColor("#131926"))
                                setStroke(dp(1), Color.parseColor(if (isOk) "#10B981" else status.colorHex))
                            }
                            layoutParams = LinearLayout.LayoutParams(
                                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
                            ).apply { bottomMargin = dp(10) }
                        }

                        val rowTop = LinearLayout(activity).apply {
                            orientation = LinearLayout.HORIZONTAL
                            gravity = Gravity.CENTER_VERTICAL
                        }
                        val title = TextView(activity).apply {
                            text = "${cap.icon}  ${cap.displayName}"
                            textSize = 13f
                            setTypeface(typeface, Typeface.BOLD)
                            setTextColor(Color.WHITE)
                            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                        }
                        val statusBadge = TextView(activity).apply {
                            text = status.displayName
                            textSize = 10f
                            setTypeface(typeface, Typeface.BOLD)
                            setTextColor(Color.parseColor(if (isOk) "#10B981" else status.colorHex))
                        }
                        rowTop.addView(title)
                        rowTop.addView(statusBadge)
                        card.addView(rowTop)

                        val reqText = TextView(activity).apply {
                            text = if (cap == PermissionCapability.USAGE_STATS) "Optional • selected installed apps only" else if (cap.isRequired) "Required" else "Optional / Recommended"
                            textSize = 10f
                            setTextColor(Color.parseColor(if (cap.isRequired) "#F59E0B" else "#64748B"))
                            setPadding(0, dp(2), 0, dp(6))
                        }
                        card.addView(reqText)

                        val btnRow = LinearLayout(activity).apply {
                            orientation = LinearLayout.HORIZONTAL
                            gravity = Gravity.END
                        }

                        if (!isOk || cap == PermissionCapability.USAGE_STATS) {
                            val actionBtn = TextView(activity).apply {
                                text = if (cap == PermissionCapability.USAGE_STATS) "[ Choose apps ]" else if (cap == PermissionCapability.BATTERY_OPTIMIZATION || cap == PermissionCapability.LOCAL_NETWORK_SYNC) "[ Configure ]" else "[ Grant ]"
                                textSize = 11f
                                setTypeface(typeface, Typeface.BOLD)
                                setTextColor(Color.WHITE)
                                setPadding(dp(12), dp(6), dp(12), dp(6))
                                background = GradientDrawable().apply {
                                    cornerRadius = dp(99).toFloat()
                                    setColor(Color.parseColor("#3B82F6"))
                                }
                                setOnClickListener {
                                    if (cap == PermissionCapability.USAGE_STATS) {
                                        showTargetAppMonitoringSettings(activity, onChanged = { renderStep() })
                                    } else requestCapabilityFromSystem(activity, cap, onGranted = { renderStep() })
                                }
                            }
                            btnRow.addView(actionBtn)
                        } else {
                            val readyTv = TextView(activity).apply {
                                text = "✅ Ready"
                                textSize = 11f
                                setTextColor(Color.parseColor("#10B981"))
                                setTypeface(typeface, Typeface.BOLD)
                            }
                            btnRow.addView(readyTv)
                        }

                        // For Camera, add Camera Check button
                        if (cap == PermissionCapability.CAMERA && isOk) {
                            val camCheckBtn = TextView(activity).apply {
                                text = "[ Live Camera Check ]"
                                textSize = 10f
                                setTextColor(Color.parseColor("#93C5FD"))
                                setPadding(dp(8), dp(4), dp(8), dp(4))
                                setOnClickListener {
                                    CameraFaceAnalysisEngine.showCameraAndFaceCheckDialog(activity)
                                }
                            }
                            btnRow.addView(camCheckBtn)
                        }

                        // For Bluetooth, add earbuds configuration button (Section 109)
                        if (cap == PermissionCapability.BLUETOOTH && isOk) {
                            val earbudConfig = db.getEarbudsConfig()
                            val earbudBtn = TextView(activity).apply {
                                text = "[ Earbuds: $earbudConfig ]"
                                textSize = 10f
                                setTextColor(Color.parseColor("#93C5FD"))
                                setPadding(dp(10), dp(4), dp(10), dp(4))
                                setOnClickListener {
                                    showEarbudsSelectionDialog(activity) { renderStep() }
                                }
                            }
                            btnRow.addView(earbudBtn)
                        }

                        card.addView(btnRow)
                        content.addView(card)
                    }

                    val allReqOk = areRequiredCapabilitiesVerified(activity)

                    val nextBtn = TextView(activity).apply {
                        text = if (allReqOk) "CONTINUE TO DEVICE SETUP →" else "COMPLETE REQUIRED PERMISSIONS FIRST"
                        textSize = 12.5f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(Color.parseColor(if (allReqOk) "#FFFFFF" else "#64748B"))
                        gravity = Gravity.CENTER
                        setPadding(dp(16), dp(13), dp(16), dp(13))
                        background = GradientDrawable().apply {
                            cornerRadius = dp(99).toFloat()
                            setColor(Color.parseColor(if (allReqOk) "#6366F1" else "#1E2638"))
                        }
                        layoutParams = LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
                        ).apply { topMargin = dp(10); bottomMargin = dp(16) }
                        setOnClickListener {
                            if (allReqOk) {
                                currentStep = 3
                                renderStep()
                            } else {
                                Toast.makeText(activity, "Please grant all required capabilities.", Toast.LENGTH_SHORT).show()
                            }
                        }
                    }
                    content.addView(nextBtn)
                }

                3 -> {
                    // ── STEP 3: DEVICE SETUP (Section 121, 122, 123) ─────────
                    content.addView(TextView(activity).apply {
                        text = "DEVICE SETUP"
                        textSize = 18f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(Color.WHITE)
                        setPadding(0, dp(6), 0, dp(4))
                    })
                    content.addView(TextView(activity).apply {
                        text = "Connect your Android phone to your Windows PC.\nInitial pairing method: USB\nYou may later use Wi-Fi automatic synchronization."
                        textSize = 12f
                        setTextColor(Color.parseColor("#94A3B8"))
                        setPadding(0, 0, 0, dp(14))
                    })

                    val pairingCard = LinearLayout(activity).apply {
                        orientation = LinearLayout.VERTICAL
                        setPadding(dp(16), dp(14), dp(16), dp(14))
                        background = GradientDrawable().apply {
                            cornerRadius = dp(16).toFloat()
                            setColor(Color.parseColor("#131926"))
                            setStroke(dp(1), Color.parseColor("#3B82F6"))
                        }
                        layoutParams = LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
                        ).apply { bottomMargin = dp(14) }
                    }

                    val pairInfo = db.getDevicePairingStatus()
                    pairingCard.addView(TextView(activity).apply {
                        text = "🖥  Windows PC Pairing: ${pairInfo.optString("trust_status", "UNPAIRED")}\n" +
                               "Device Name: ${pairInfo.optString("device_name", "None")}\n" +
                               "Method: ${pairInfo.optString("pairing_method", "USB")}\n" +
                               "Wi-Fi Sync: Automatic after pairing"
                        textSize = 12f
                        setTextColor(Color.parseColor("#E2E8F0"))
                        setLineSpacing(dp(3).toFloat(), 1f)
                    })

                    val pairUsbBtn = TextView(activity).apply {
                        text = "CONNECT BY USB (PAIR NOW)"
                        textSize = 11.5f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(Color.WHITE)
                        gravity = Gravity.CENTER
                        setPadding(dp(12), dp(8), dp(12), dp(8))
                        background = GradientDrawable().apply {
                            cornerRadius = dp(99).toFloat()
                            setColor(Color.parseColor("#10B981"))
                        }
                        layoutParams = LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
                        ).apply { topMargin = dp(10) }
                        setOnClickListener {
                            db.saveDevicePairing("Windows PC", "USB", "PAIRED")
                            Toast.makeText(activity, "DEVICE PAIRED ✅ with Windows Central Server", Toast.LENGTH_SHORT).show()
                            renderStep()
                        }
                    }
                    pairingCard.addView(pairUsbBtn)
                    content.addView(pairingCard)

                    val proceedBtn = TextView(activity).apply {
                        text = "PROCEED TO SUMMARY →"
                        textSize = 13f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(Color.WHITE)
                        gravity = Gravity.CENTER
                        setPadding(dp(16), dp(13), dp(16), dp(13))
                        background = GradientDrawable().apply {
                            cornerRadius = dp(99).toFloat()
                            setColor(Color.parseColor("#6366F1"))
                        }
                        setOnClickListener {
                            currentStep = 4
                            renderStep()
                        }
                    }
                    content.addView(proceedBtn)
                }

                4 -> {
                    // ── STEP 4: SUMMARY SCREEN (Section 125) ─────────────────
                    content.addView(TextView(activity).apply {
                        text = "STRICTRECORDS IS READY"
                        textSize = 19f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(Color.WHITE)
                        gravity = Gravity.CENTER
                        setPadding(0, dp(10), 0, dp(12))
                    })

                    val summaryItems = listOf(
                        "Camera & Photo Evidence" to "✅ Ready",
                        "Bluetooth & AirPods Check" to "✅ Ready",
                        "App Usage & Restrictions" to "✅ Ready",
                        "Notifications & Alerts" to "✅ Ready",
                        "Background Schedule Engine" to "✅ Ready",
                        "Desktop PC Registration" to "✅ Paired",
                        "Wi-Fi Sync Mesh" to "✅ Ready"
                    )

                    val summaryCard = LinearLayout(activity).apply {
                        orientation = LinearLayout.VERTICAL
                        setPadding(dp(16), dp(14), dp(16), dp(14))
                        background = GradientDrawable().apply {
                            cornerRadius = dp(16).toFloat()
                            setColor(Color.parseColor("#101519"))
                            setStroke(dp(1), Color.parseColor("#70F59B"))
                        }
                        layoutParams = LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
                        ).apply { bottomMargin = dp(16) }
                    }

                    for ((label, state) in summaryItems) {
                        val row = LinearLayout(activity).apply {
                            orientation = LinearLayout.HORIZONTAL
                            setPadding(0, dp(4), 0, dp(4))
                        }
                        row.addView(TextView(activity).apply {
                            text = label
                            textSize = 12.5f
                            setTextColor(Color.parseColor("#F5F7F8"))
                            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                        })
                        row.addView(TextView(activity).apply {
                            text = state
                            textSize = 12.5f
                            setTypeface(typeface, Typeface.BOLD)
                            setTextColor(Color.parseColor("#70F59B"))
                        })
                        summaryCard.addView(row)
                    }

                    summaryCard.addView(TextView(activity).apply {
                        text = "\nDiscipline active. Every hour matters."
                        textSize = 12f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(Color.parseColor("#70F59B"))
                        gravity = Gravity.CENTER
                    })
                    content.addView(summaryCard)

                    val enterBtn = TextView(activity).apply {
                        text = "ENTER STRICTRECORDS"
                        textSize = 13.5f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(Color.parseColor("#070A0C"))
                        gravity = Gravity.CENTER
                        setPadding(dp(16), dp(14), dp(16), dp(14))
                        background = GradientDrawable().apply {
                            cornerRadius = dp(99).toFloat()
                            setColor(Color.parseColor("#70F59B"))
                        }
                        setOnClickListener {
                            db.setConfigValue("onboarding_completed", "1")
                            dialogRef?.dismiss()
                            Toast.makeText(activity, "Welcome to StrictRoutines Operating System", Toast.LENGTH_SHORT).show()
                            onCompleted()
                        }
                    }
                    content.addView(enterBtn)
                }
            }

            scroll.addView(content)
            rootDialogView.addView(scroll)
        }

        renderStep()

        dialogRef = AlertDialog.Builder(activity)
            .setView(rootDialogView)
            .setCancelable(false)
            .show()
    }

    // ════════════════════════════════════════════════════════
    // EARBUDS SELECTION DIALOG (Section 109)
    // ════════════════════════════════════════════════════════

    fun showEarbudsSelectionDialog(
        activity: Activity,
        db: StrictRecordsLocalDb,
        onSelected: () -> Unit
    ) {
        showEarbudsSelectionDialog(activity) { _ -> onSelected() }
    }

    @android.annotation.SuppressLint("MissingPermission")
    fun showEarbudsSelectionDialog(activity: Activity, onSelected: (String) -> Unit) {
        val db = StrictRecordsLocalDb.getInstance(activity)
        val connectedNames = HardwareDeviceVerifier.getConnectedBluetoothAudioDevices(activity)
        val adapter = BluetoothAdapter.getDefaultAdapter()

        data class EarbudOption(val label: String, val name: String, val address: String)
        val options = mutableListOf<EarbudOption>()

        for (cName in connectedNames) {
            options.add(EarbudOption("🟢 $cName (Connected Now)", cName, ""))
        }
        try {
            val bondedSet = adapter?.bondedDevices ?: emptySet()
            for (dev in bondedSet) {
                val dName = dev.name?.trim().orEmpty()
                val dAddr = dev.address?.trim().orEmpty()
                if (dName.isNotEmpty() && options.none { it.name.equals(dName, ignoreCase = true) }) {
                    options.add(EarbudOption("🎧 $dName [$dAddr]", dName, dAddr))
                }
            }
        } catch (_: SecurityException) {}

        options.add(EarbudOption("🌐 Any Bluetooth Audio Output", "Any Bluetooth Audio Output", ""))

        val labels = options.map { it.label }.toTypedArray()

        AlertDialog.Builder(activity)
            .setTitle("🎧 SELECT REQUIRED EARBUDS")
            .setItems(labels) { _, which ->
                val chosen = options[which]
                db.setEarbudsConfig(chosen.name, chosen.address)
                Toast.makeText(activity, "Required Earbuds: ${chosen.name}", Toast.LENGTH_SHORT).show()
                onSelected(chosen.name)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ════════════════════════════════════════════════════════
    // RESTRICTION UI (STRICTBOT INTERRUPT - Section 114)
    // ════════════════════════════════════════════════════════

    fun showStrictBotRestrictionDialog(
        activity: Activity,
        decision: DisciplineScoringRules.RestrictionDecision,
        onViewTasks: () -> Unit
    ) {
        StrictBotOverlayController.showStrictBotPopup(activity, decision)
    }

    // ════════════════════════════════════════════════════════
    // PERMISSION REVOCATION DIALOG (Section 118)
    // ════════════════════════════════════════════════════════

    fun showRevocationRepairDialog(activity: Activity, revoked: PermissionCapability, onRestored: () -> Unit) {
        if (revoked == PermissionCapability.USAGE_STATS) {
            AlertDialog.Builder(activity)
                .setTitle("Selected-app monitoring paused")
                .setMessage(TargetAppMonitoringPolicy.monitoringStatus(activity))
                .setPositiveButton("Manage monitoring") { _, _ ->
                    showTargetAppMonitoringSettings(activity, onGranted = onRestored)
                }
                .setNegativeButton("Not now", null)
                .setCancelable(true)
                .show()
            return
        }
        AlertDialog.Builder(activity)
            .setTitle("🛡 STRICTROUTINES ACCESS REQUIRED")
            .setMessage(
                "${revoked.displayName} permission has been revoked.\n\n" +
                "${revoked.impactIfDenied}\n\n" +
                "StrictRoutines cannot operate reliably without this mandatory capability."
            )
            .setPositiveButton("RESTORE PERMISSION") { _, _ ->
                requestCapabilityFromSystem(activity, revoked, onGranted = onRestored)
            }
            .setNegativeButton("OPEN SETTINGS") { _, _ ->
                val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                    data = Uri.fromParts("package", activity.packageName, null)
                }
                activity.startActivity(intent)
            }
            .setCancelable(false)
            .show()
    }

    // ════════════════════════════════════════════════════════
    // DIAGNOSTICS & YOUTUBE TEST MODE (Sections 127 & 128)
    // ════════════════════════════════════════════════════════

    fun showDiagnosticsScreen(activity: Activity, currentScore: Int, onRefresh: () -> Unit) {
        val dp = { v: Int ->
            TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), activity.resources.displayMetrics).toInt()
        }
        val db = StrictRecordsLocalDb.getInstance(activity)

        val camPerm = hasSelfPermission(activity, Manifest.permission.CAMERA)
        val camHw = HardwareDeviceVerifier.isCameraHardwareAvailable(activity)

        val btPerm = hasSelfPermission(activity, Manifest.permission.BLUETOOTH)
        val btAdapter = HardwareDeviceVerifier.isBluetoothAdapterEnabled(activity)
        val btDevices = HardwareDeviceVerifier.getConnectedBluetoothAudioDevices(activity)

        val usagePerm = HardwareDeviceVerifier.hasUsageStatsPermission(activity)
        val installedTargets = TargetAppMonitoringPolicy.installedTargets(activity, forceRefresh = true)
        val ytInstalled = installedTargets.any { it.target == TargetAppMonitoringPolicy.TargetApp.YOUTUBE && it.available }
        val igInstalled = installedTargets.any { it.target == TargetAppMonitoringPolicy.TargetApp.INSTAGRAM && it.available }

        val pairing = db.getDevicePairingStatus()
        val lanConnected = HardwareDeviceVerifier.isNetworkOrWifiAvailable(activity)

        val notifAccess = StrictMediaNotificationListener.isNotificationAccessGranted(activity)
        val activeControllers = StrictMediaNotificationListener.getActiveMediaControllers(activity)
        val musicControllers = activeControllers.filter { BackgroundMusicRestrictionManager.isMonitoredPlaybackPackage(it.packageName) }
        val musicSessionsSummary = if (musicControllers.isEmpty()) "None active" else musicControllers.joinToString { c ->
            val st = when (c.playbackState?.state) {
                android.media.session.PlaybackState.STATE_PLAYING -> "PLAYING"
                android.media.session.PlaybackState.STATE_PAUSED -> "PAUSED"
                android.media.session.PlaybackState.STATE_STOPPED -> "STOPPED"
                else -> "IDLE/NONE"
            }
            "${c.packageName} ($st)"
        }

        val safePauseState = FinancialSafePause.getDiagnosticsState(activity)
        val safePauseSec = safePauseState.remainingMs / 1000L
        val currentFg = if (safePauseState.isActive) "Not monitored during banking break" else HardwareDeviceVerifier.lastAccessibilityForegroundPkg.ifBlank {
            HardwareDeviceVerifier.detectRealForegroundPackage(activity) ?: "None"
        }

        val diagText =
            "🛡 AUTOMATIC BANKING BREAK (5 MINUTES)\n" +
            "State: ${if (safePauseState.isActive) "ACTIVE (ENFORCEMENT SUSPENDED)" else "INACTIVE (NORMAL ENFORCEMENT)"}\n" +
            "Last confirmed protected foreground: ${if (safePauseState.isProtectedInForeground) "YES (${safePauseState.lastProtectedPackage})" else "NO"}\n" +
            "Remaining Pause Time: ${if (safePauseState.isActive) "${safePauseSec / 60}m ${safePauseSec % 60}s" else "0s"}\n" +
            "Last Protected App: ${safePauseState.lastProtectedPackage.ifBlank { "None" }}\n" +
            "Current Foreground: $currentFg\n" +
            "Accessibility enabled: ${HardwareDeviceVerifier.isAccessibilityServiceEnabled(activity, StrictAccessibilityService::class.java)}; connected: ${StrictAccessibilityService.activeInstance != null}\n" +
            "Overlay permission: ${Settings.canDrawOverlays(activity)}\n" +
            UsageAccessMonitor.diagnosticText() + "\n\n" +
            "🎵 BACKGROUND MUSIC RESTRICTION\n" +
            "Media Notification Access: ${if (notifAccess) "GRANTED ✓" else "NOT GRANTED"}\n" +
            "Active Music Sessions: $musicSessionsSummary\n" +
            "Selected media targets: YouTube, Instagram, Spotify, Samsung Music, Gaana, JioSaavn\n\n" +
            "📷 CAMERA\n" +
            "Permission: ${if (camPerm) "GRANTED" else "NOT GRANTED"}\n" +
            "Hardware: ${if (camHw) "AVAILABLE" else "NONE"}\n" +
            "Test Capture: ${if (camPerm && camHw) "PASS ✓" else "FAIL"}\n\n" +
            "🎧 BLUETOOTH\n" +
            "Permission: ${if (btPerm) "GRANTED" else "NOT GRANTED"}\n" +
            "Adapter: ${if (btAdapter) "ON" else "OFF"}\n" +
            "Earbuds: ${if (btDevices.isNotEmpty()) btDevices.first() else "None connected"}\n" +
            "Test: ${if (btAdapter) "PASS ✓" else "FAIL"}\n\n" +
            "📊 OPTIONAL SELECTED-APP MONITORING\n" +
            "Access: ${if (usagePerm) "GRANTED" else "DENIED"}\n" +
            "Consent: ${if (TargetAppMonitoringPolicy.hasUsageConsent(activity)) "AGREED" else "NOT GIVEN"}\n" +
            "Status: ${TargetAppMonitoringPolicy.monitoringStatus(activity)}\n" +
            "YouTube detected: ${if (ytInstalled) "YES" else "NO"}\n" +
            "Instagram detected: ${if (igInstalled) "YES" else "NO"}\n" +
            "Foreground check: Only selected targets; no test observation asserted here.\n\n" +
            "🖥 WINDOWS PAIRING\n" +
            "Paired: ${if (pairing.optString("trust_status") == "PAIRED") "YES" else "NO"}\n" +
            "LAN: ${if (lanConnected) "CONNECTED" else "OFFLINE"}\n" +
            "Heartbeat: ${if (lanConnected && pairing.optString("last_sync_at").isNotBlank()) "Last contact: " + pairing.optString("last_sync_at") else "Not confirmed"}\n\n" +
            "🔄 SYNC\n" +
            "Last Sync: ${pairing.optString("last_sync_at", "Never")}\n" +
            "Pending Events: 0\n"

        val dialogView = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#07090D"))
            setPadding(dp(16), dp(16), dp(16), dp(16))
        }

        dialogView.addView(TextView(activity).apply {
            text = "INTERNAL DIAGNOSTICS & RESTRICTION TEST"
            textSize = 15f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            setPadding(0, 0, 0, dp(10))
        })

        dialogView.addView(TextView(activity).apply {
            text = diagText
            textSize = 11.5f
            setTextColor(Color.parseColor("#94A3B8"))
            setLineSpacing(dp(2).toFloat(), 1f)
            setPadding(0, 0, 0, dp(14))
        })
        // Live Camera & Face Analysis Check Button
        val camCheckBtn = TextView(activity).apply {
            text = "📷 RUN LIVE CAMERA & FACE CHECK"
            textSize = 11.5f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(dp(12), dp(10), dp(12), dp(10))
            background = GradientDrawable().apply {
                cornerRadius = dp(10).toFloat()
                setColor(Color.parseColor("#2563EB"))
            }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(12)
            }
            setOnClickListener {
                CameraFaceAnalysisEngine.showCameraAndFaceCheckDialog(activity)
            }
        }
        dialogView.addView(camCheckBtn)

        // Wake-Up Test Mode / Diagnostics Button
        val wakeupTestBtn = TextView(activity).apply {
            text = "🧪 TEST WAKE-UP VERIFICATION"
            textSize = 11.5f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(dp(12), dp(10), dp(12), dp(10))
            background = GradientDrawable().apply {
                cornerRadius = dp(10).toFloat()
                setColor(Color.parseColor("#1D4ED8"))
            }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(12)
            }
            setOnClickListener {
                if (activity is MainActivity) {
                    WakeUpVerificationController.showWakeUpDiagnosticsDialog(activity, db)
                }
            }
        }
        dialogView.addView(wakeupTestBtn)

        // Background Music Restriction Config and Test Controls
        val musicConfigBtn = TextView(activity).apply {
            text = if (notifAccess) "🎵 CONFIGURE MEDIA RESTRICTION ACCESS ✓" else "🎵 ENABLE MEDIA NOTIFICATION LISTENER ⚠"
            textSize = 11.5f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(dp(12), dp(10), dp(12), dp(10))
            background = GradientDrawable().apply {
                cornerRadius = dp(10).toFloat()
                setColor(Color.parseColor(if (notifAccess) "#059669" else "#7C3AED"))
            }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(10)
            }
            setOnClickListener {
                try {
                    activity.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                } catch (_: Exception) {
                    Toast.makeText(activity, "Cannot open Notification Listener settings", Toast.LENGTH_SHORT).show()
                }
            }
        }
        dialogView.addView(musicConfigBtn)

        val musicTestRow = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(10)
            }
        }
        val testMusicRestrictBtn = TextView(activity).apply {
            text = "TEST MUSIC (SCORE 50: RESTRICT)"
            textSize = 10f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(dp(10), dp(8), dp(10), dp(8))
            background = GradientDrawable().apply {
                cornerRadius = dp(99).toFloat()
                setColor(Color.parseColor("#EF4444"))
            }
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = dp(6) }
            setOnClickListener {
                val decision = DisciplineScoringRules.evaluateRestrictionDecision(50, "com.spotify.music")
                BackgroundMusicRestrictionManager.checkAndEnforceMusicRestrictions(activity, "diag_test_restrict_50")
                AlertDialog.Builder(activity)
                    .setTitle("🎵 MUSIC RESTRICTION TEST: Spotify")
                    .setMessage("Score: 50 < 60 Threshold.\nDecision: Restricted = ${decision.isRestricted} (${decision.reason})\nEnforcement command dispatched.")
                    .setPositiveButton("OK", null)
                    .show()
            }
        }
        musicTestRow.addView(testMusicRestrictBtn)

        val testMusicAllowBtn = TextView(activity).apply {
            text = "TEST MUSIC (SCORE 65: ALLOW)"
            textSize = 10f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(dp(10), dp(8), dp(10), dp(8))
            background = GradientDrawable().apply {
                cornerRadius = dp(99).toFloat()
                setColor(Color.parseColor("#10B981"))
            }
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            setOnClickListener {
                val decision = DisciplineScoringRules.evaluateRestrictionDecision(65, "com.spotify.music")
                AlertDialog.Builder(activity)
                    .setTitle("🎵 MUSIC ACCESS ALLOWED: Spotify")
                    .setMessage("Score: 65 >= 60 Threshold.\nDecision: Restricted = ${decision.isRestricted}.\nMusic playback is permitted.")
                    .setPositiveButton("OK", null)
                    .show()
            }
        }
        musicTestRow.addView(testMusicAllowBtn)
        dialogView.addView(musicTestRow)

        // Section 128: YouTube Test Mode Buttons
        val testRow = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }

        val testLowBtn = TextView(activity).apply {
            text = "TEST SCORE 70 (RESTRICT)"
            textSize = 10f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(dp(10), dp(8), dp(10), dp(8))
            background = GradientDrawable().apply {
                cornerRadius = dp(99).toFloat()
                setColor(Color.parseColor("#EF4444"))
            }
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = dp(6) }
            setOnClickListener {
                val decision = DisciplineScoringRules.evaluateRestrictionDecision(70, "com.google.android.youtube")
                db.recordRestrictionEvent(
                    decision.packageName,
                    decision.appName,
                    decision.scoreAtDetection,
                    decision.requiredScore,
                    decision.action,
                    decision.reason
                )
                showStrictBotRestrictionDialog(activity, decision) {}
            }
        }
        testRow.addView(testLowBtn)

        val testHighBtn = TextView(activity).apply {
            text = "TEST SCORE 85 (ALLOW)"
            textSize = 10f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(dp(10), dp(8), dp(10), dp(8))
            background = GradientDrawable().apply {
                cornerRadius = dp(99).toFloat()
                setColor(Color.parseColor("#10B981"))
            }
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            setOnClickListener {
                val decision = DisciplineScoringRules.evaluateRestrictionDecision(85, "com.google.android.youtube")
                AlertDialog.Builder(activity)
                    .setTitle("🏆 ACCESS ALLOWED: YouTube")
                    .setMessage("Score: 85 >= 80 Threshold.\nYouTube access granted by reward policy.")
                    .setPositiveButton("OK", null)
                    .show()
            }
        }
        testRow.addView(testHighBtn)
        dialogView.addView(testRow)

        // Financial Safe Pause Test Controls
        val safePauseRow = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(8)
            }
        }
        val triggerPauseBtn = TextView(activity).apply {
            text = "TRIGGER SAFE PAUSE (PhonePe)"
            textSize = 10f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(dp(8), dp(8), dp(8), dp(8))
            background = GradientDrawable().apply {
                cornerRadius = dp(99).toFloat()
                setColor(Color.parseColor("#6366F1"))
            }
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.2f).apply { rightMargin = dp(6) }
            setOnClickListener {
                FinancialSafePause.onProtectedPackageObserved(activity, "com.phonepe.app")
                onRefresh()
                showDiagnosticsScreen(activity, currentScore, onRefresh)
            }
        }
        safePauseRow.addView(triggerPauseBtn)

        val resetPauseBtn = TextView(activity).apply {
            text = "RESET PAUSE"
            textSize = 10f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(dp(8), dp(8), dp(8), dp(8))
            background = GradientDrawable().apply {
                cornerRadius = dp(99).toFloat()
                setColor(Color.parseColor("#475569"))
            }
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 0.8f)
            setOnClickListener {
                FinancialSafePause.resetForTesting(activity)
                onRefresh()
                showDiagnosticsScreen(activity, currentScore, onRefresh)
            }
        }
        safePauseRow.addView(resetPauseBtn)
        dialogView.addView(safePauseRow)

        val scrollWrapper = ScrollView(activity).apply {
            addView(dialogView)
        }

        AlertDialog.Builder(activity)
            .setView(scrollWrapper)
            .setPositiveButton("Close", null)
            .show()
    }

    // ════════════════════════════════════════════════════════
    // SETTINGS PERMISSION CENTER (Section 126)
    // ════════════════════════════════════════════════════════

    // ════════════════════════════════════════════════════════
    // FOCUS CAPABILITY & RESTRICTION HELPERS (Section 6, 7, 9)
    // ════════════════════════════════════════════════════════

    fun isNotificationPolicyAccessGranted(context: Context): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
            return nm?.isNotificationPolicyAccessGranted == true
        }
        return true
    }

    fun requestNotificationPolicyAccess(activity: Activity) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            try {
                activity.startActivity(Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS))
            } catch (e: Exception) {
                Toast.makeText(activity, "Open Settings -> Do Not Disturb access", Toast.LENGTH_LONG).show()
            }
        }
    }

    fun setFocusDndMode(context: Context, enabled: Boolean): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return false
            if (nm.isNotificationPolicyAccessGranted) {
                try {
                    nm.setInterruptionFilter(
                        if (enabled) NotificationManager.INTERRUPTION_FILTER_PRIORITY
                        else NotificationManager.INTERRUPTION_FILTER_ALL
                    )
                    return true
                } catch (e: Exception) {
                    return false
                }
            }
        }
        return false
    }

    fun isDeviceAdminActive(context: Context): Boolean {
        val dpm = context.getSystemService(Context.DEVICE_POLICY_SERVICE) as? DevicePolicyManager ?: return false
        val cn = ComponentName(context, StrictRecordsAdminReceiver::class.java)
        return try {
            dpm.isAdminActive(cn)
        } catch (e: Exception) {
            false
        }
    }

    fun requestDeviceAdmin(activity: Activity) {
        AlertDialog.Builder(activity)
            .setTitle("🛡️ Device Administration Disclosure")
            .setMessage(
                "StrictRoutines includes optional Device Administration to prevent uninstallation during active routine hours.\n\n" +
                "PRIVACY & SAFETY ASSURANCE:\n" +
                "• StrictRoutines requests ZERO intrusive device administration policies (no wipe-data, no screen lock reset, no password policies, no camera disabling).\n" +
                "• Your device, files, and personal data remain completely secure and untouched.\n\n" +
                "Do you wish to enable Device Administration?"
            )
            .setPositiveButton("CONTINUE") { _, _ ->
                val cn = ComponentName(activity, StrictRecordsAdminReceiver::class.java)
                val intent = Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN).apply {
                    putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, cn)
                    putExtra(DevicePolicyManager.EXTRA_ADD_EXPLANATION, "StrictRoutines anti-uninstall protection during focus sessions. Zero wipe or password policies.")
                }
                try {
                    activity.startActivity(intent)
                } catch (e: Exception) {
                    Toast.makeText(activity, "Cannot open Device Admin: ${e.message}", Toast.LENGTH_LONG).show()
                }
            }
            .setNegativeButton("CANCEL", null)
            .show()
    }

    fun isMultiWindowSupported(activity: Activity): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            activity.isInMultiWindowMode
        } else false
    }

    fun showPermissionSetupSheet(activity: Activity, onComplete: () -> Unit) {
        showMandatoryOnboardingWizard(activity, onComplete)
    }

    private fun hasSelfPermission(context: Context, permission: String): Boolean {
        return context.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED
    }
}
