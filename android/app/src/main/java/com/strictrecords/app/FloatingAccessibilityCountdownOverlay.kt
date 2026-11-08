package com.strictrecords.app

import android.animation.ObjectAnimator
import android.animation.PropertyValuesHolder
import android.animation.ValueAnimator
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import java.util.Locale

/**
 * Persistent floating accessibility countdown overlay for StrictRecords.
 *
 * Implements:
 * - Top-right floating pill over eligible apps during the accessibility grace period and overdue phase
 * - Exact live countdown: 04:59, 04:58, ..., 00:01
 * - Exact zero-time display: 00:00 TIME'S UP
 * - Overdue negative counter: -00:01, -00:02, -01:00, -05:00 with Overdue (1s/1m/5m) subline
 * - Three visual urgency states:
 *   - Normal (5:00 to 2:01): Cyan border & text (#00D2FF), calm robot expression
 *   - Warning (2:00 to 0:31): Amber border & text (#FF9500), concerned robot, subtle pulse
 *   - Critical (0:30 to 0:01): Red border & text (#FF3B30), alert robot, warning pulse
 *   - Completed (0:00 & Overdue): Persistent Red border (#FF3B30), TIME'S UP or Overdue counter, triggers centered warning
 * - Compact StrictRecords crowned robot head with subtle breathing & blinking
 * - Minimize / expand control (chevron toggle; minimized view is a compact icon pill)
 * - Tapping the expanded pill opens Android Accessibility Settings or reopens the warning modal
 * - Hidden over Settings screens so as not to cover the system accessibility toggle
 * - Excluded from banking, UPI, and emergency dialers
 * - In-place updates every second without recreating the window view
 * - Complete recovery when AccessibilityService is confirmed enabled and connected
 */
object FloatingAccessibilityCountdownOverlay {

    private const val TAG = "FloatingA11yOverlay"

    enum class UrgencyState(val borderColor: Int, val textColor: Int) {
        NORMAL(Color.parseColor("#00D2FF"), Color.parseColor("#00D2FF")),
        WARNING(Color.parseColor("#FF9500"), Color.parseColor("#FF9500")),
        CRITICAL(Color.parseColor("#FF3B30"), Color.parseColor("#FF3B30")),
        COMPLETED(Color.parseColor("#FF3B30"), Color.parseColor("#FF3B30"))
    }

    private val mainHandler: Handler? by lazy {
        runCatching {
            val looper = Looper.getMainLooper()
            if (looper != null) Handler(looper) else null
        }.getOrNull()
    }

    @Volatile private var windowManager: WindowManager? = null
    @Volatile private var rootView: View? = null
    @Volatile private var cardContainer: FrameLayout? = null
    @Volatile private var expandedView: LinearLayout? = null
    @Volatile private var minimizedView: FrameLayout? = null
    @Volatile private var botViewExpanded: CompactStrictBotView? = null
    @Volatile private var botViewMinimized: CompactStrictBotView? = null
    @Volatile private var labelTextView: TextView? = null
    @Volatile private var countdownTextView: TextView? = null
    @Volatile private var subtextView: TextView? = null
    @Volatile private var pulseAnimator: ObjectAnimator? = null

    @Volatile private var isAttached = false
    @Volatile private var isMinimized = false
    @Volatile private var isTemporarilyHidden = false
    @Volatile private var currentUrgencyState = UrgencyState.NORMAL
    @Volatile private var lastKnownRemainingMs = 300_000L
    @Volatile private var lastSettingsTapMs = 0L

    private var tickRunnable: Runnable? = null
    private var appContext: Context? = null

    // Pure logic methods for unit testing & state transitions

    fun getUrgencyState(remainingMs: Long): UrgencyState {
        return when {
            remainingMs <= 0L -> UrgencyState.COMPLETED
            remainingMs <= 30_000L -> UrgencyState.CRITICAL
            remainingMs <= 120_000L -> UrgencyState.WARNING
            else -> UrgencyState.NORMAL
        }
    }

    fun formatCountdown(remainingMs: Long): String {
        return if (remainingMs > 0L) {
            val safeMs = remainingMs.coerceAtLeast(0L)
            val mm = safeMs / 60_000L
            val ss = (safeMs % 60_000L) / 1000L
            "%02d:%02d".format(Locale.US, mm, ss)
        } else if (remainingMs == 0L) {
            "00:00"
        } else {
            val overdueMs = -remainingMs
            val mm = overdueMs / 60_000L
            val ss = (overdueMs % 60_000L) / 1000L
            "-%02d:%02d".format(Locale.US, mm, ss)
        }
    }

    fun formatSubtext(remainingMs: Long, urgency: UrgencyState): String {
        return when {
            remainingMs > 0L -> {
                val sec = (remainingMs + 999L) / 1000L
                if (urgency == UrgencyState.CRITICAL) {
                    "${sec}s remaining"
                } else if (urgency == UrgencyState.WARNING) {
                    val mm = remainingMs / 60_000L
                    val ss = (remainingMs % 60_000L) / 1000L
                    if (mm > 0) "${mm}m ${ss}s left" else "${ss}s left"
                } else {
                    ""
                }
            }
            remainingMs == 0L -> "TIME'S UP"
            else -> {
                val overdueSec = (-remainingMs) / 1000L
                if (overdueSec < 60L) {
                    "Overdue (${overdueSec}s)"
                } else {
                    val overdueMin = overdueSec / 60L
                    "Overdue (${overdueMin}m)"
                }
            }
        }
    }

    fun isSettingsPackage(packageName: String?): Boolean {
        val pkg = packageName?.trim().orEmpty()
        if (pkg.isBlank()) return false
        return pkg == "com.android.settings" ||
            pkg.startsWith("com.android.settings.") ||
            pkg.startsWith("com.google.android.settings") ||
            pkg.startsWith("com.samsung.android.settings") ||
            pkg.contains(".settings") ||
            pkg == "com.miui.securitycenter"
    }

    fun isSystemExcluded(packageName: String?): Boolean {
        val pkg = packageName?.trim().orEmpty()
        if (pkg.isBlank()) return false
        return pkg in FloatingBotStateSelector.SYSTEM_CRITICAL_PACKAGES ||
            pkg.startsWith("com.android.systemui") ||
            pkg.contains("dialer") ||
            pkg.contains("emergency")
    }

    fun isPackageEligible(context: Context?, packageName: String?): Boolean {
        val pkg = packageName?.trim().orEmpty()
        if (pkg.isBlank()) return true // default to visible over launcher/home
        if (isSettingsPackage(pkg)) return false
        if (isSystemExcluded(pkg)) return false
        if (context != null) {
            if (FinancialSafePause.isPauseActive(context)) return false
            if (FinancialSafePause.isProtectedFinancialPackage(context, pkg)) return false
        }
        return true
    }

    // Public Lifecycle & View Controls

    fun isShowing(): Boolean = isAttached && rootView != null

    fun isMinimized(): Boolean = isMinimized

    fun showOrUpdate(context: Context, remainingMs: Long? = null) {
        val handler = mainHandler
        if (handler != null && Looper.myLooper() != handler.looper) {
            handler.post { showOrUpdate(context, remainingMs) }
            return
        }

        val app = context.applicationContext
        appContext = app

        // 1. Verify overlay permission
        if (!Settings.canDrawOverlays(app)) {
            Log.w(TAG, "Overlay permission not granted; cannot display floating countdown")
            hide()
            return
        }

        // 2. Check accessibility state - if genuinely enabled and connected, do not show
        val isA11yEnabled = HardwareDeviceVerifier.isAccessibilityServiceEnabled(app, StrictAccessibilityService::class.java)
        val isConnected = StrictAccessibilityService.activeInstance != null
        if (isA11yEnabled && isConnected) {
            hide()
            return
        }

        val currentRemaining = remainingMs ?: run {
            val status = AccessibilityGraceManager.getUiStatus(app)
            status.effectiveCountdownMs
        }
        lastKnownRemainingMs = currentRemaining

        if (rootView == null) {
            createAndAttachOverlay(app, currentRemaining)
        } else {
            updateViewContent(currentRemaining)
        }

        startTicker()
    }

    fun hide() {
        val handler = mainHandler
        if (handler != null && Looper.myLooper() != handler.looper) {
            handler.post { hide() }
            return
        }

        stopTicker()
        stopPulseAnimation()

        rootView?.let { view ->
            try {
                windowManager?.removeView(view)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to remove overlay view: ${e.message}")
            }
        }

        rootView = null
        cardContainer = null
        expandedView = null
        minimizedView = null
        botViewExpanded = null
        botViewMinimized = null
        labelTextView = null
        countdownTextView = null
        subtextView = null
        windowManager = null
        isAttached = false
        isTemporarilyHidden = false
    }

    fun setScreenTemporarilyHidden(hidden: Boolean) {
        val handler = mainHandler
        if (handler != null && Looper.myLooper() != handler.looper) {
            handler.post { setScreenTemporarilyHidden(hidden) }
            return
        }
        isTemporarilyHidden = hidden
        rootView?.visibility = if (hidden) View.GONE else View.VISIBLE
    }

    fun toggleMinimize() {
        val handler = mainHandler
        if (handler != null && Looper.myLooper() != handler.looper) {
            handler.post { toggleMinimize() }
            return
        }
        setMinimizedState(!isMinimized)
    }

    fun setMinimizedState(minimized: Boolean) {
        if (isMinimized == minimized || rootView == null) return
        isMinimized = minimized

        val context = appContext ?: return
        val density = context.resources.displayMetrics.density

        if (minimized) {
            expandedView?.visibility = View.GONE
            minimizedView?.visibility = View.VISIBLE
            updateLayoutParams(width = (54 * density).toInt(), height = (54 * density).toInt())
        } else {
            minimizedView?.visibility = View.GONE
            expandedView?.visibility = View.VISIBLE
            updateLayoutParams(width = (172 * density).toInt(), height = (62 * density).toInt())
        }

        updateBorder(currentUrgencyState)
    }

    // Window Creation & Layout

    @SuppressLint("ClickableViewAccessibility")
    private fun createAndAttachOverlay(context: Context, remainingMs: Long) {
        val wm = context.getSystemService(Context.WINDOW_SERVICE) as? WindowManager ?: return
        windowManager = wm

        val density = context.resources.displayMetrics.density
        fun dp(v: Float) = (v * density).toInt()

        val windowType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }

        val initialWidth = if (isMinimized) dp(54f) else dp(172f)
        val initialHeight = if (isMinimized) dp(54f) else dp(62f)

        // Calculate status bar inset so overlay stays clearly below system status bar and cutouts
        val statusBarHeight = getStatusBarHeight(context)
        val topMargin = statusBarHeight + dp(8f)
        val rightMargin = dp(12f)

        val params = WindowManager.LayoutParams(
            initialWidth,
            initialHeight,
            windowType,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.END
            x = rightMargin
            y = topMargin
        }

        // Card Container
        val card = FrameLayout(context).apply {
            clipChildren = false
            clipToPadding = false
        }
        cardContainer = card

        // 1. Expanded layout (Horizontal pill: Robot | Labels & Countdown & Subtext | Minimize Chevron)
        val expanded = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(6f), dp(4f), dp(8f), dp(4f))
            visibility = if (isMinimized) View.GONE else View.VISIBLE
            isClickable = true
            isFocusable = true
            setOnClickListener { handlePillTap(context) }
        }
        expandedView = expanded

        // Crowned Robot Head
        val botExpanded = CompactStrictBotView(context).apply {
            val botSize = dp(40f)
            layoutParams = LinearLayout.LayoutParams(botSize, botSize).apply {
                marginEnd = dp(6f)
            }
        }
        botViewExpanded = botExpanded
        expanded.addView(botExpanded)

        // Text Column
        val textLayout = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.0f)
        }

        val label = TextView(context).apply {
            text = "ACCESSIBILITY OFF"
            textSize = 9.5f
            setTextColor(Color.parseColor("#CAD7E2"))
            setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD))
            includeFontPadding = false
        }
        labelTextView = label
        textLayout.addView(label)

        val countdown = TextView(context).apply {
            text = formatCountdown(remainingMs)
            textSize = 16.5f
            setTextColor(UrgencyState.NORMAL.textColor)
            setTypeface(Typeface.create("sans-serif", Typeface.BOLD))
            includeFontPadding = false
        }
        countdownTextView = countdown
        textLayout.addView(countdown)

        val subtext = TextView(context).apply {
            text = formatSubtext(remainingMs, UrgencyState.NORMAL)
            textSize = 9.5f
            setTextColor(UrgencyState.NORMAL.textColor)
            setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD))
            includeFontPadding = false
            visibility = if (text.isNotBlank()) View.VISIBLE else View.GONE
        }
        subtextView = subtext
        textLayout.addView(subtext)

        expanded.addView(textLayout)

        // Chevron minimize button
        val chevronContainer = FrameLayout(context).apply {
            val chevronSize = dp(24f)
            layoutParams = LinearLayout.LayoutParams(chevronSize, chevronSize).apply {
                marginStart = dp(4f)
            }
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(Color.argb(38, 255, 255, 255))
            }
            isClickable = true
            isFocusable = true
            setOnClickListener { toggleMinimize() }
        }

        val chevronText = TextView(context).apply {
            text = "⌄"
            textSize = 15f
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            layoutParams = FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        }
        chevronContainer.addView(chevronText)
        expanded.addView(chevronContainer)

        card.addView(expanded, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        // 2. Minimized layout (Circular/Square pill with just the crowned robot head)
        val minimized = FrameLayout(context).apply {
            visibility = if (isMinimized) View.VISIBLE else View.GONE
            isClickable = true
            isFocusable = true
            setOnClickListener { toggleMinimize() }
        }
        minimizedView = minimized

        val botMinimized = CompactStrictBotView(context).apply {
            val botSize = dp(38f)
            layoutParams = FrameLayout.LayoutParams(botSize, botSize, Gravity.CENTER)
        }
        botViewMinimized = botMinimized
        minimized.addView(botMinimized)

        card.addView(minimized, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        try {
            wm.addView(card, params)
            rootView = card
            isAttached = true
            updateViewContent(remainingMs)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to attach floating overlay to window manager: ${e.message}")
            hide()
        }
    }

    private fun updateLayoutParams(width: Int, height: Int) {
        val root = rootView ?: return
        val wm = windowManager ?: return
        val params = root.layoutParams as? WindowManager.LayoutParams ?: return
        params.width = width
        params.height = height
        try {
            wm.updateViewLayout(root, params)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to update layout params: ${e.message}")
        }
    }

    private fun updateBorder(urgency: UrgencyState) {
        val card = cardContainer ?: return
        val density = card.resources.displayMetrics.density
        val cornerRadius = if (isMinimized) 27f * density else 24f * density
        val strokeWidth = (1.5f * density).toInt()

        val bg = GradientDrawable().apply {
            setColor(Color.argb(230, 10, 16, 29)) // dark translucent (#E60A101D)
            this.cornerRadius = cornerRadius
            setStroke(strokeWidth, urgency.borderColor)
        }
        card.background = bg
    }

    private fun updateViewContent(remainingMs: Long) {
        val urgency = getUrgencyState(remainingMs)
        val timeString = formatCountdown(remainingMs)
        val sublineText = formatSubtext(remainingMs, urgency)

        // Update labels and text
        labelTextView?.text = "ACCESSIBILITY OFF"
        countdownTextView?.text = timeString
        countdownTextView?.setTextColor(urgency.textColor)

        subtextView?.text = sublineText
        subtextView?.setTextColor(urgency.textColor)
        subtextView?.visibility = if (sublineText.isNotBlank()) View.VISIBLE else View.GONE

        // Update bot expression
        val compactState = when (urgency) {
            UrgencyState.NORMAL -> CompactStrictBotView.UrgencyState.NORMAL
            UrgencyState.WARNING -> CompactStrictBotView.UrgencyState.WARNING
            UrgencyState.CRITICAL -> CompactStrictBotView.UrgencyState.CRITICAL
            UrgencyState.COMPLETED -> CompactStrictBotView.UrgencyState.COMPLETED
        }
        botViewExpanded?.setUrgencyState(compactState)
        botViewMinimized?.setUrgencyState(compactState)

        // Urgency state transition changes
        if (urgency != currentUrgencyState) {
            currentUrgencyState = urgency
            updateBorder(urgency)
            updatePulseAnimation(urgency)
        } else if (cardContainer?.background == null) {
            updateBorder(urgency)
        }
    }

    private fun updatePulseAnimation(urgency: UrgencyState) {
        stopPulseAnimation()
        val card = cardContainer ?: return

        when (urgency) {
            UrgencyState.WARNING -> {
                // Subtle pulse in warning state (1.5s cycle)
                pulseAnimator = ObjectAnimator.ofPropertyValuesHolder(
                    card,
                    PropertyValuesHolder.ofFloat(View.SCALE_X, 1.0f, 1.025f, 1.0f),
                    PropertyValuesHolder.ofFloat(View.SCALE_Y, 1.0f, 1.025f, 1.0f)
                ).apply {
                    duration = 1500L
                    repeatCount = ValueAnimator.INFINITE
                    start()
                }
            }
            UrgencyState.CRITICAL, UrgencyState.COMPLETED -> {
                // Slightly faster alert pulse in critical & completed states
                pulseAnimator = ObjectAnimator.ofPropertyValuesHolder(
                    card,
                    PropertyValuesHolder.ofFloat(View.SCALE_X, 1.0f, 1.035f, 1.0f),
                    PropertyValuesHolder.ofFloat(View.SCALE_Y, 1.0f, 1.035f, 1.0f)
                ).apply {
                    duration = 800L
                    repeatCount = ValueAnimator.INFINITE
                    start()
                }
            }
            UrgencyState.NORMAL -> {
                card.scaleX = 1.0f
                card.scaleY = 1.0f
            }
        }
    }

    private fun stopPulseAnimation() {
        pulseAnimator?.cancel()
        pulseAnimator = null
        cardContainer?.scaleX = 1.0f
        cardContainer?.scaleY = 1.0f
    }

    // 1-Second Periodic Countdown Ticker

    private fun startTicker() {
        if (tickRunnable != null) return
        val runnable = object : Runnable {
            override fun run() {
                val app = appContext
                if (app == null || !isAttached || rootView == null) {
                    stopTicker()
                    return
                }

                // 1. Verify accessibility recovery
                val isA11yEnabled = HardwareDeviceVerifier.isAccessibilityServiceEnabled(app, StrictAccessibilityService::class.java)
                val isConnected = StrictAccessibilityService.activeInstance != null
                if (isA11yEnabled && isConnected) {
                    Log.i(TAG, "Accessibility restored and connected. Removing floating overlay.")
                    hide()
                    return
                }

                // 2. Query active foreground app to check eligibility / settings exclusion
                val currentPkg = HardwareDeviceVerifier.detectActiveForegroundApp(app)
                    ?: UsageAccessMonitor.foreground().packageName.orEmpty()

                val screenInteractive = HardwareDeviceVerifier.isMonitoringScreenAvailable(app)
                val shouldHideForScreen = !screenInteractive || isSettingsPackage(currentPkg) || !isPackageEligible(app, currentPkg)

                if (shouldHideForScreen) {
                    if (rootView?.visibility != View.GONE) {
                        rootView?.visibility = View.GONE
                    }
                } else {
                    if (rootView?.visibility != View.VISIBLE) {
                        rootView?.visibility = View.VISIBLE
                    }
                }

                // 3. Compute remaining/overdue time using monotonic clock & persisted state
                val status = AccessibilityGraceManager.getUiStatus(app)
                val effectiveMs = status.effectiveCountdownMs
                lastKnownRemainingMs = effectiveMs

                updateViewContent(effectiveMs)

                // 4. Trigger centered warning overlay when grace period completes (at 00:00 and overdue)
                if (effectiveMs <= 0L && !shouldHideForScreen && currentPkg.isNotBlank() &&
                    TargetAppMonitoringPolicy.isTargetPackage(currentPkg)) {
                    AccessibilityWarningOverlay.showOrUpdate(app, currentPkg)
                }

                mainHandler?.postDelayed(this, 1000L)
            }
        }
        tickRunnable = runnable
        mainHandler?.postDelayed(runnable, 1000L)
    }

    private fun stopTicker() {
        tickRunnable?.let { mainHandler?.removeCallbacks(it) }
        tickRunnable = null
    }

    private fun handlePillTap(context: Context) {
        val app = context.applicationContext
        val status = AccessibilityGraceManager.getUiStatus(app)
        val currentPkg = HardwareDeviceVerifier.detectActiveForegroundApp(app)
            ?: UsageAccessMonitor.foreground().packageName.orEmpty()

        if (status.effectiveCountdownMs <= 0L && currentPkg.isNotBlank() &&
            TargetAppMonitoringPolicy.isTargetPackage(currentPkg)) {
            // Overdue state: reopen centered warning modal
            AccessibilityWarningOverlay.showOrUpdate(app, currentPkg)
        } else {
            // Grace state or general tap: proceed directly to Android settings
            openAccessibilitySettings(context)
        }
    }

    private fun openAccessibilitySettings(context: Context) {
        val now = SystemClock.uptimeMillis()
        if (now - lastSettingsTapMs < 1000L) return
        lastSettingsTapMs = now

        try {
            val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            }
            context.startActivity(intent)
            Toast.makeText(context, "StrictRecords • Turn ON Accessibility to resume monitoring", Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to launch accessibility settings: ${e.message}")
            try {
                context.startActivity(Intent(Settings.ACTION_SETTINGS).apply {
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK
                })
            } catch (_: Exception) {}
        }
    }

    private fun getStatusBarHeight(context: Context): Int {
        var result = 0
        val resourceId = context.resources.getIdentifier("status_bar_height", "dimen", "android")
        if (resourceId > 0) {
            result = context.resources.getDimensionPixelSize(resourceId)
        }
        val density = context.resources.displayMetrics.density
        return if (result > 0) result else (32 * density).toInt()
    }
}
