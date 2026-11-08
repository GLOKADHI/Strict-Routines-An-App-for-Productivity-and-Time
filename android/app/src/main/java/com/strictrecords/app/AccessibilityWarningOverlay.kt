package com.strictrecords.app

import android.animation.AnimatorSet
import android.animation.ObjectAnimator
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.RectF
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
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast

/**
 * Centered blurred Accessibility warning modal with procedural animated crowned robot.
 *
 * Implements:
 * - Prominent centered frosted dark-glass dialog with subtle cyan/blue border
 * - Red & white vector shield badge
 * - Header: "TURN ON ACCESSIBILITY"
 * - Subtitle: "StrictRecords accessibility has been off for 5 minutes. Turn it back on to restore monitoring."
 * - Info Card: "Why it's needed? Allows StrictRecords to monitor app usage and apply your discipline rules."
 * - Primary Action: "⚙ OPEN ACCESSIBILITY SETTINGS"
 * - Secondary Action: "⌂ BACK TO HOME"
 * - Grounded procedural crowned robot (DisciplineBotWalkingView) with 4-stage entrance:
 *   1. Enters naturally from left edge with alternating gait and arm swing
 *   2. Gradually slows down
 *   3. Stops with both feet firmly grounded and turns to face the user
 *   4. Raises hand with pointing gesture toward card and delivers speech bubble
 * - Sleek speech bubble: "Please turn on the accessibility" with tail directed toward robot hand
 * - TTS speech audio synthesis
 * - Preserves standing warning posture across app switches without re-triggering entrance walk
 */
internal object AccessibilityWarningOverlay {

    private const val TAG = "AccessibilityWarning"
    private val mainHandler = Handler(Looper.getMainLooper())

    private var root: View? = null
    private var windowManager: WindowManager? = null
    private var robotView: DisciplineBotWalkingView? = null
    private var speechBubble: SpeechBubbleLayout? = null
    private var speech: StrictBotPopupSpeech? = null
    private var targetPackage = ""
    private var watchdog: Runnable? = null
    private var lastSettingsLaunch = -2_000L
    private var settingsPending = false
    private var hasCompletedEntrance = false

    internal var blurRequested = false
        private set

    private fun eligible(context: Context, pkg: String): Boolean {
        val a11yStatus = AccessibilityGraceManager.getUiStatus(context)
        val warningActive = AccessibilityGraceManager.centeredWarningState(context).active ||
            (a11yStatus.isInterrupted && a11yStatus.remainingGraceMs <= 0L) ||
            a11yStatus.overdueSeconds > 0L

        if (!warningActive ||
            !HardwareDeviceVerifier.isMonitoringScreenAvailable(context) ||
            FinancialSafePause.isPauseActive(context) ||
            FinancialSafePause.isProtectedFinancialPackage(context, pkg) ||
            FloatingAccessibilityCountdownOverlay.isSettingsPackage(pkg) ||
            FloatingAccessibilityCountdownOverlay.isSystemExcluded(pkg)) {
            return false
        }

        val live = HardwareDeviceVerifier.detectForegroundObservation(context)
        return (live.status == TargetAppMonitoringPolicy.MONITOR && live.packageName == pkg) ||
            TargetAppMonitoringPolicy.isTargetPackage(pkg)
    }

    fun isShowing(): Boolean = root != null

    fun showOrUpdate(context: Context, pkg: String) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { showOrUpdate(context, pkg) }
            return
        }

        val app = context.applicationContext
        if (!runCatching { eligible(app, pkg) }.getOrDefault(false)) {
            hide()
            return
        }

        if (root != null && targetPackage == pkg) return
        hide()

        val host: Context = StrictAccessibilityService.activeInstance ?: app
        val type = when {
            Settings.canDrawOverlays(app) && Build.VERSION.SDK_INT >= 26 -> WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            Settings.canDrawOverlays(app) -> {
                @Suppress("DEPRECATION")
                WindowManager.LayoutParams.TYPE_PHONE
            }
            host is android.accessibilityservice.AccessibilityService -> WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
            else -> return
        }

        val wm = host.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val density = host.resources.displayMetrics.density
        fun dp(v: Float) = (v * density).toInt()

        val openSettings = {
            val now = SystemClock.uptimeMillis()
            if (!settingsPending && now - lastSettingsLaunch >= 1_000L) {
                settingsPending = true
                lastSettingsLaunch = now
                speech?.stop()
                try {
                    app.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).apply {
                        flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
                    })
                    Toast.makeText(app, "StrictRecords • Turn ON Accessibility to resume monitoring", Toast.LENGTH_SHORT).show()
                } catch (_: Exception) {
                    settingsPending = false
                    try {
                        app.startActivity(Intent(Settings.ACTION_SETTINGS).apply {
                            flags = Intent.FLAG_ACTIVITY_NEW_TASK
                        })
                    } catch (_: Exception) {}
                }
            }
        }

        // Full Screen Scrim Container
        val fullScreenRoot = FrameLayout(host).apply {
            setBackgroundColor(Color.parseColor("#B3060B14"))
            isClickable = true
            isFocusable = true
        }

        // 1. Centered Warning Card
        val card = LinearLayout(host).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(22f), dp(18f), dp(22f), dp(20f))
            background = GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                intArrayOf(Color.parseColor("#E6131D2A"), Color.parseColor("#E60A101C"))
            ).apply {
                cornerRadius = dp(24f).toFloat()
                setStroke(dp(1.5f), Color.parseColor("#00D2FF"))
            }
            isClickable = true
            isFocusable = true
            setOnClickListener { openSettings() }
        }

        // Shield Badge View
        val shieldBadge = ShieldBadgeView(host).apply {
            layoutParams = LinearLayout.LayoutParams(dp(50f), dp(50f)).apply {
                bottomMargin = dp(8f)
            }
        }
        card.addView(shieldBadge)

        // Title: TURN ON ACCESSIBILITY
        val title = TextView(host).apply {
            text = "TURN ON ACCESSIBILITY"
            textSize = 20.5f
            setTextColor(Color.WHITE)
            setTypeface(typeface, Typeface.BOLD)
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }
        card.addView(title)

        // Subtitle
        val subtitle = TextView(host).apply {
            text = "StrictRecords accessibility has been off for 5 minutes. Turn it back on to restore monitoring."
            textSize = 13.5f
            setTextColor(Color.parseColor("#CBD5E1"))
            gravity = Gravity.CENTER
            setLineSpacing(0f, 1.15f)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(6f)
                bottomMargin = dp(14f)
            }
        }
        card.addView(subtitle)

        // Info Card Row: "Why it's needed?"
        val infoCard = LinearLayout(host).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(12f), dp(10f), dp(12f), dp(10f))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#B30F1722"))
                cornerRadius = dp(14f).toFloat()
                setStroke(dp(1f), Color.parseColor("#243345"))
            }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(16f)
            }
            setOnClickListener { openSettings() }
        }

        val gearIcon = TextView(host).apply {
            text = "⚙"
            textSize = 18f
            setTextColor(Color.parseColor("#38BDF8"))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                marginEnd = dp(10f)
            }
        }
        infoCard.addView(gearIcon)

        val infoTextColumn = LinearLayout(host).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.0f)
        }

        val infoHeader = TextView(host).apply {
            text = "Why it's needed?"
            textSize = 13f
            setTextColor(Color.WHITE)
            setTypeface(typeface, Typeface.BOLD)
        }
        infoTextColumn.addView(infoHeader)

        val infoDesc = TextView(host).apply {
            text = "Allows StrictRecords to monitor app usage and apply your discipline rules."
            textSize = 11.5f
            setTextColor(Color.parseColor("#94A3B8"))
        }
        infoTextColumn.addView(infoDesc)
        infoCard.addView(infoTextColumn)

        val chevron = TextView(host).apply {
            text = "›"
            textSize = 18f
            setTextColor(Color.parseColor("#64748B"))
            setTypeface(typeface, Typeface.BOLD)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                marginStart = dp(6f)
            }
        }
        infoCard.addView(chevron)
        card.addView(infoCard)

        // Primary Button: OPEN ACCESSIBILITY SETTINGS
        val settingsButton = Button(host).apply {
            text = "⚙ OPEN ACCESSIBILITY SETTINGS"
            isAllCaps = false
            textSize = 14f
            setTypeface(typeface, Typeface.BOLD)
            minimumHeight = dp(52f)
            setTextColor(Color.WHITE)
            setPadding(dp(12f), dp(10f), dp(12f), dp(10f))
            background = GradientDrawable(
                GradientDrawable.Orientation.LEFT_RIGHT,
                intArrayOf(Color.parseColor("#149BFF"), Color.parseColor("#00BBD6"))
            ).apply {
                cornerRadius = dp(16f).toFloat()
            }
            setOnClickListener { openSettings() }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }
        card.addView(settingsButton)

        // Secondary Button: BACK TO HOME
        val homeButton = Button(host).apply {
            text = "⌂ BACK TO HOME"
            isAllCaps = false
            textSize = 14f
            setTypeface(typeface, Typeface.BOLD)
            minimumHeight = dp(48f)
            setTextColor(Color.parseColor("#CAD7E2"))
            setPadding(dp(12f), dp(8f), dp(12f), dp(8f))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#1F2B3A"))
                cornerRadius = dp(16f).toFloat()
                setStroke(dp(1f), Color.parseColor("#3B4F63"))
            }
            setOnClickListener {
                hide()
                try {
                    val homeIntent = Intent(Intent.ACTION_MAIN).apply {
                        addCategory(Intent.CATEGORY_HOME)
                        flags = Intent.FLAG_ACTIVITY_NEW_TASK
                    }
                    app.startActivity(homeIntent)
                } catch (_: Exception) {}
            }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(8f)
            }
        }
        card.addView(homeButton)

        val cardLayoutParams = FrameLayout.LayoutParams(dp(335f), ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER)
        fullScreenRoot.addView(card, cardLayoutParams)

        // 2. Grounded Crowned Robot & Speech Bubble Container (Bottom-Left)
        val bottomContainer = LinearLayout(host).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.BOTTOM or Gravity.START
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(300f),
                Gravity.BOTTOM or Gravity.START
            ).apply {
                leftMargin = dp(8f)
                bottomMargin = dp(6f)
            }
        }

        val robot = DisciplineBotWalkingView(host).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(185f))
            setPresentation(scale = 1.6f, stationary = hasCompletedEntrance)
        }
        robotView = robot
        bottomContainer.addView(robot)

        val bubble = SpeechBubbleLayout(host).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply {
                bottomMargin = dp(4f)
                marginStart = dp(12f)
            }
            visibility = if (hasCompletedEntrance) View.VISIBLE else View.INVISIBLE
            setOnClickListener { openSettings() }
        }

        val bubbleText = TextView(host).apply {
            text = "Please turn on\nthe accessibility"
            textSize = 13f
            setTextColor(Color.WHITE)
            setTypeface(typeface, Typeface.BOLD)
            gravity = Gravity.CENTER
            setLineSpacing(0f, 1.15f)
        }
        bubble.addView(bubbleText)
        speechBubble = bubble
        bottomContainer.addView(bubble)
        bottomContainer.removeView(robot)
        bottomContainer.addView(robot)

        fullScreenRoot.addView(bottomContainer)

        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
            type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_DIM_BEHIND,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.FILL
            dimAmount = 0.65f
        }

        blurRequested = false
        if (Build.VERSION.SDK_INT >= 31) {
            runCatching {
                if (wm.isCrossWindowBlurEnabled) {
                    params.flags = params.flags or WindowManager.LayoutParams.FLAG_BLUR_BEHIND
                    params.blurBehindRadius = dp(20f)
                    blurRequested = true
                }
            }
        }

        try {
            wm.addView(fullScreenRoot, params)
            root = fullScreenRoot
            windowManager = wm
            targetPackage = pkg
            settingsPending = false

            speech = StrictBotPopupSpeech(app,
                onSpeaking = { speaking -> if(root===fullScreenRoot)robot.setSpeaking(speaking) },
                onStatus = {})
            robot.setWarning(true)
            fun announce() {
                if(root!==fullScreenRoot)return
                hasCompletedEntrance=true
                bubble.visibility=View.VISIBLE
                bubble.alpha=0f
                bubble.animate().alpha(1f).setDuration(240L).start()
                speech?.speak("Please turn on the accessibility. Open Accessibility Settings to enable StrictRoutines.",
                    "accessibility-warning:${SystemClock.uptimeMillis()}")
            }
            if(!hasCompletedEntrance) robot.playEntrance { announce() }
            else { robot.setPresentation(1.6f,stationary=true);announce() }

            val check = object : Runnable {
                override fun run() {
                    if (root !== fullScreenRoot) return
                    if (!runCatching { eligible(app, pkg) }.getOrDefault(false)) {
                        hide()
                        return
                    }
                    mainHandler.postDelayed(this, 700L)
                }
            }
            watchdog = check
            mainHandler.postDelayed(check, 700L)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to display warning overlay: ${e.message}")
            hide()
        }
    }

    fun hide() {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { hide() }
            return
        }
        watchdog?.let { mainHandler.removeCallbacks(it) }
        watchdog = null
        speech?.close()
        speech = null
        robotView?.stopAnimation()
        robotView = null
        speechBubble = null
        val old = root
        root = null
        targetPackage = ""
        settingsPending = false
        if (old != null) {
            runCatching { windowManager?.removeView(old) }
        }
        windowManager = null
        blurRequested = false
    }

    fun resetEntranceState() {
        hasCompletedEntrance = false
    }

    // Custom Views for Vector Shield Badge and Speech Bubble Layout

    private class ShieldBadgeView(context: Context) : View(context) {
        private val circlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.FILL
            color = Color.parseColor("#1A149BFF")
        }
        private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = 2f * resources.displayMetrics.density
            color = Color.parseColor("#38BDF8")
        }
        private val shieldLeftPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.FILL
            color = Color.parseColor("#DC2626")
        }
        private val shieldRightPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.FILL
            color = Color.parseColor("#EF4444")
        }
        private val whiteCrestPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = 2.2f * resources.displayMetrics.density
            strokeCap = Paint.Cap.ROUND
            color = Color.WHITE
        }
        private val path = Path()

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            val w = width.toFloat()
            val h = height.toFloat()
            val cx = w / 2f
            val cy = h / 2f
            val r = minOf(w, h) / 2f - 4f

            // Outer ring and background
            canvas.drawCircle(cx, cy, r, circlePaint)
            canvas.drawCircle(cx, cy, r, ringPaint)

            // Shield icon
            val sw = r * 0.95f
            val sh = r * 1.15f
            val sl = cx - sw / 2f
            val st = cy - sh / 2f

            // Left half
            path.reset()
            path.moveTo(cx, st)
            path.lineTo(sl, st + sh * 0.2f)
            path.quadTo(sl, st + sh * 0.7f, cx, st + sh)
            path.lineTo(cx, st)
            path.close()
            canvas.drawPath(path, shieldLeftPaint)

            // Right half
            path.reset()
            path.moveTo(cx, st)
            path.lineTo(sl + sw, st + sh * 0.2f)
            path.quadTo(sl + sw, st + sh * 0.7f, cx, st + sh)
            path.lineTo(cx, st)
            path.close()
            canvas.drawPath(path, shieldRightPaint)

            // White crest accent
            path.reset()
            path.moveTo(cx - sw * 0.18f, cy)
            path.lineTo(cx - sw * 0.04f, cy + sh * 0.16f)
            path.lineTo(cx + sw * 0.22f, cy - sh * 0.14f)
            canvas.drawPath(path, whiteCrestPaint)
        }
    }

    private class SpeechBubbleLayout(context: Context) : LinearLayout(context) {
        private val bgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.FILL
            color = Color.parseColor("#E60A101C")
        }
        private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = 1.5f * resources.displayMetrics.density
            color = Color.parseColor("#00D2FF")
        }
        private val tailPath = Path()
        private val rect = RectF()

        init {
            setWillNotDraw(false)
            orientation = VERTICAL
            val density = resources.displayMetrics.density
            setPadding((18 * density).toInt(), (10 * density).toInt(), (14 * density).toInt(), (10 * density).toInt())
        }

        override fun onDraw(canvas: Canvas) {
            val density = resources.displayMetrics.density
            val tailW = 10f * density
            val w = width.toFloat()
            val h = height.toFloat()
            val radius = 14f * density

            rect.set(tailW, 1f * density, w - 1f * density, h - 1f * density)
            canvas.drawRoundRect(rect, radius, radius, bgPaint)
            canvas.drawRoundRect(rect, radius, radius, strokePaint)

            val tailCenterY = h / 2f
            tailPath.reset()
            tailPath.moveTo(tailW, tailCenterY - 7f * density)
            tailPath.lineTo(1f * density, tailCenterY)
            tailPath.lineTo(tailW, tailCenterY + 7f * density)
            tailPath.close()

            canvas.drawPath(tailPath, bgPaint)
            canvas.drawLine(tailW, tailCenterY - 7f * density, 1f * density, tailCenterY, strokePaint)
            canvas.drawLine(1f * density, tailCenterY, tailW, tailCenterY + 7f * density, strokePaint)

            super.onDraw(canvas)
        }
    }
}
