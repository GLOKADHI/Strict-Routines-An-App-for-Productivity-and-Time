package com.strictrecords.app

import android.accessibilityservice.AccessibilityService
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.graphics.Color

/**
 * Manages the animated floating discipline bot overlay over configured entertainment apps
 * (e.g. Instagram, YouTube).
 *
 * Requirements:
 * - Appears near the bottom of the screen when entertainment access is not permitted.
 * - Displays:
 *   1. "Please turn on the accessibility" (when accessibility is disabled > 5 min)
 *   2. "No Entertainment apps unless sufficient points" (when points are below threshold)
 * - Uses compact WRAP_CONTENT overlay window with FLAG_NOT_TOUCH_MODAL so touches outside
 *   the character are never blocked.
 * - Respects screen insets, banking safety, emergency screens, and permission state.
 */
object FloatingDisciplineBotOverlay {

    private const val TAG = "FloatingDisciplineBot"
    private val mainHandler = Handler(Looper.getMainLooper())

    @Volatile
    private var activeOverlayView: View? = null

    @Volatile
    private var activeWindowManager: WindowManager? = null

    @Volatile
    private var currentPackage: String = ""

    @Volatile
    private var currentState: FloatingBotStateSelector.MessageState = FloatingBotStateSelector.MessageState.NONE

    private var walkingBotView: DisciplineBotWalkingView? = null
    private var announcementView: StrictBotAnnouncementView? = null

    // Layout position memory
    private var lastRecordedX: Int = -1
    private var lastRecordedY: Int = -1

    fun isShowing(): Boolean = activeOverlayView != null

    /**
     * Central entry point to evaluate and update the floating bot overlay for the current foreground app.
     */
    fun updateForForegroundApp(context: Context, foregroundPackage: String?) {
        val appContext = context.applicationContext
        mainHandler.post {
            val db = StrictRecordsLocalDb.getInstance(appContext)
            val today = db.nowDate()
            val score = db.getScore(today, false)
            val isFinancialPause = FinancialSafePause.isPauseActive(appContext)
            val isGymMode = db.isGymModeMusicAllowed()
            val appLocks = db.getRemoteAppLocks()
            val appLocksMap = if (appLocks != null) {
                val map = mutableMapOf<String, Boolean>()
                val keys = appLocks.keys()
                while (keys.hasNext()) {
                    val k = keys.next()
                    map[k] = appLocks.optBoolean(k, false)
                }
                map
            } else null

            val a11yStatus = AccessibilityGraceManager.checkAccessibilityState(appContext)

            val decision = FloatingBotStateSelector.evaluate(
                foregroundPackage = foregroundPackage,
                currentScore = score,
                accessibilityStatus = a11yStatus,
                context = appContext,
                isFinancialPauseActive = isFinancialPause,
                isEducationalUnlockActive = db.isEducationalUnlockActive(),
                isGymModeActive = isGymMode,
                appLocksMap = appLocksMap,
                confirmedAccessibilityWarning = AccessibilityGraceManager.centeredWarningState(appContext).active
            )

            if (!decision.shouldShow) {
                hide()
                return@post
            }

            if (decision.requiresAccessibilityAction) {
                hideImmediate()
                StrictBotOverlayController.hidePopupImmediate()
                AccessibilityWarningOverlay.showOrUpdate(context, decision.foregroundPackage)
            } else {
                AccessibilityWarningOverlay.hide()
                val pkg = decision.foregroundPackage
                val countdown = db.getCurrentCountdownState(today)
                val restrictionDecision = DisciplineScoringRules.evaluateRestrictionDecision(
                    score = score,
                    packageNameOrApp = pkg,
                    appLocks = appLocks,
                    isEducationalUnlockActive = db.isEducationalUnlockActive(),
                    nextTaskTitle = countdown.title ?: "Complete your current scheduled task",
                    nextTaskWindow = "${countdown.windowStart.orEmpty()}–${countdown.windowEnd.orEmpty()}",
                    context = appContext,
                    db = db,
                    isGymModeActive = isGymMode
                )
                if (restrictionDecision.isRestricted) {
                    hideImmediate()
                    StrictBotOverlayController.showStrictBotPopup(context, restrictionDecision)
                } else {
                    hide()
                }
            }
        }
    }

    /**
     * Attaches or updates the floating bot overlay with the given decision.
     */
    @SuppressLint("ClickableViewAccessibility")
    private fun showOrUpdate(context: Context, decision: FloatingBotStateSelector.Decision) {
        val appContext = context.applicationContext

        // If overlay already showing for the same state and package, just update message/warning
        if (activeOverlayView != null && currentPackage == decision.foregroundPackage) {
            if (currentState != decision.state) {
                currentState = decision.state
                announcementView?.setMessage(decision.message, "floating:${decision.foregroundPackage}:${decision.state}")
                walkingBotView?.setWarning(decision.state == FloatingBotStateSelector.MessageState.ACCESSIBILITY_DISABLED)
            }
            return
        }

        // Determine available overlay window type
        val a11yContext: Context? = when {
            context is AccessibilityService -> context
            StrictAccessibilityService.activeInstance != null -> StrictAccessibilityService.activeInstance
            else -> null
        }

        val canDrawAppOverlay = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            Settings.canDrawOverlays(appContext)
        } else true

        // Priority 1: Accessibility overlay when a11y service is active
        // Priority 2: System application overlay when overlay permission is granted
        val (hostContext, windowType) = when {
            a11yContext != null -> Pair(a11yContext, WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY)
            canDrawAppOverlay -> {
                val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                } else {
                    @Suppress("DEPRECATION")
                    WindowManager.LayoutParams.TYPE_PHONE
                }
                Pair(appContext, type)
            }
            else -> Pair(null, -1)
        }

        if (hostContext == null || windowType == -1) {
            Log.w(TAG, "Cannot show floating bot overlay: no overlay permission or active accessibility service")
            return
        }

        // Clean up previous overlay before creating new one
        hideImmediate()

        try {
            val wm = hostContext.getSystemService(Context.WINDOW_SERVICE) as WindowManager
            val density = hostContext.resources.displayMetrics.density
            fun dp(v: Float): Int = (v * density).toInt()

            // 1. Build Overlay Container Layout (Vertical: Speech bubble on top, Walking bot below)
            val container = LinearLayout(hostContext).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.START or Gravity.BOTTOM
                setPadding(dp(4f), dp(4f), dp(4f), dp(4f))
            }

            val announcement = StrictBotAnnouncementView(hostContext, decision.message,
                "floating:${decision.foregroundPackage}:${decision.state}")
            announcementView = announcement
            val bot = announcement.bot
            bot.setWarning(decision.state == FloatingBotStateSelector.MessageState.ACCESSIBILITY_DISABLED)
            walkingBotView = bot
            val availableWidth = (hostContext.resources.displayMetrics.widthPixels - dp(32f)).coerceAtLeast(1)
            container.addView(announcement, LinearLayout.LayoutParams(
                minOf(dp(244f), availableWidth), LinearLayout.LayoutParams.WRAP_CONTENT))
            container.addView(TextView(hostContext).apply {
                text = if (decision.requiresAccessibilityAction) "Open Accessibility Settings →" else "Open restriction details →"
                textSize = 12f; setTextColor(Color.parseColor("#56DCED")); minHeight = dp(44f)
                gravity = Gravity.CENTER_VERTICAL; isFocusable = true
                setOnClickListener { handleBubbleAction(hostContext, currentState) }
            }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))

            // 4. Compact Window Layout Params
            val screenW = hostContext.resources.displayMetrics.widthPixels
            val screenH = hostContext.resources.displayMetrics.heightPixels

            val defaultX = dp(16f)
            val defaultY = dp(48f) // comfortable distance above bottom navigation bar

            val initialX = if (lastRecordedX in 0..(screenW - dp(100f))) lastRecordedX else defaultX
            val initialY = if (lastRecordedY in 0..(screenH - dp(100f))) lastRecordedY else defaultY

            val lp = WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                windowType,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT
            ).apply {
                gravity = Gravity.BOTTOM or Gravity.START
                x = initialX
                y = initialY
            }

            // 5. Enable user to drag/reposition the bot along the screen bottom if desired
            var initialTouchX = 0f
            var initialTouchY = 0f
            var initialLpX = 0
            var initialLpY = 0
            var isDragging = false

            container.setOnTouchListener { v, event ->
                when (event.action) {
                    MotionEvent.ACTION_DOWN -> {
                        initialTouchX = event.rawX
                        initialTouchY = event.rawY
                        initialLpX = lp.x
                        initialLpY = lp.y
                        isDragging = false
                        true
                    }
                    MotionEvent.ACTION_MOVE -> {
                        val dx = (event.rawX - initialTouchX).toInt()
                        val dy = (initialTouchY - event.rawY).toInt() // invert Y for bottom gravity
                        if (Math.abs(dx) > dp(5f) || Math.abs(dy) > dp(5f)) {
                            isDragging = true
                            val minX = dp(8f)
                            val maxX = screenW - container.width - dp(8f)
                            val minY = dp(16f)
                            val maxY = screenH - container.height - dp(32f)

                            lp.x = (initialLpX + dx).coerceIn(minX, maxX.coerceAtLeast(minX))
                            lp.y = (initialLpY + dy).coerceIn(minY, maxY.coerceAtLeast(minY))
                            try {
                                wm.updateViewLayout(container, lp)
                            } catch (_: Exception) {}
                        }
                        true
                    }
                    MotionEvent.ACTION_UP -> {
                        if (!isDragging) {
                            // Tap on container / bot -> trigger action
                            handleBubbleAction(hostContext, currentState)
                        } else {
                            lastRecordedX = lp.x
                            lastRecordedY = lp.y
                        }
                        true
                    }
                    else -> false
                }
            }

            wm.addView(container, lp)
            activeOverlayView = container
            activeWindowManager = wm
            currentPackage = decision.foregroundPackage
            currentState = decision.state

            bot.startAnimation()
            if (decision.state == FloatingBotStateSelector.MessageState.INSUFFICIENT_POINTS) {
                // One detailed popup for a new target entry; the existing popup
                // session/foreground checks control eligibility and duplication.
                announcement.setAutoSpeak(false)
                showRestrictionDetails(hostContext)
                container.postDelayed({
                    if (activeOverlayView === container && !StrictBotOverlayController.isRestrictionOverlayShowing()) {
                        announcement.setAutoSpeak(true)
                    }
                }, 250L)
            }
            Log.i(TAG, "Floating discipline bot attached for target=${decision.foregroundPackage} state=${decision.state}")

        } catch (e: Exception) {
            Log.e(TAG, "Failed to attach floating bot overlay: ${e.message}", e)
            hideImmediate()
        }
    }

    private fun handleBubbleAction(context: Context, state: FloatingBotStateSelector.MessageState) {
        when (state) {
            FloatingBotStateSelector.MessageState.ACCESSIBILITY_DISABLED -> {
                // Open Accessibility Settings
                try {
                    val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).apply {
                        flags = Intent.FLAG_ACTIVITY_NEW_TASK
                    }
                    context.startActivity(intent)
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to open accessibility settings: ${e.message}")
                }
            }
            FloatingBotStateSelector.MessageState.INSUFFICIENT_POINTS -> {
                showRestrictionDetails(context)
            }
            FloatingBotStateSelector.MessageState.NONE -> {}
        }
    }

    private fun showRestrictionDetails(context: Context) {
        runCatching {
            val db = StrictRecordsLocalDb.getInstance(context)
            val decision = DisciplineScoringRules.evaluateRestrictionDecision(
                db.getScore(db.nowDate(), false), currentPackage,
                appLocks = db.getRemoteAppLocks(),
                isEducationalUnlockActive = db.isEducationalUnlockActive(),
                context = context, db = db)
            if (decision?.isRestricted == true) {
                announcementView?.pauseSpeech()
                StrictBotOverlayController.showStrictBotPopup(context, decision)
            }
        }.onFailure { Log.w(TAG, "Restriction details unavailable: ${it.javaClass.simpleName}") }
    }

    /**
     * Hides and cleanly detaches the floating bot overlay window.
     */
    fun hide() {
        mainHandler.post {
            hideImmediate()
            AccessibilityWarningOverlay.hide()
            StrictBotOverlayController.hidePopupImmediate()
        }
    }

    private fun hideImmediate() {
        try {
            walkingBotView?.stopAnimation()
            walkingBotView = null
            announcementView?.releaseSpeech()
            announcementView = null

            val view = activeOverlayView
            val wm = activeWindowManager
            if (view != null && wm != null) {
                wm.removeView(view)
            }
        } catch (e: Exception) {
            Log.d(TAG, "Safe dismiss of floating overlay: ${e.message}")
        } finally {
            activeOverlayView = null
            activeWindowManager = null
            currentPackage = ""
            currentState = FloatingBotStateSelector.MessageState.NONE
        }
    }
}
