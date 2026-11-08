package com.strictrecords.app

import android.accessibilityservice.AccessibilityService
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
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.animation.AccelerateInterpolator
import android.view.animation.DecelerateInterpolator
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.util.Log
import java.lang.ref.WeakReference
import java.util.concurrent.atomic.AtomicLong

/**
 * Manages the StrictBot restriction overlay for restricted apps (YouTube, Instagram, Samsung Music, Spotify).
 */
object StrictBotOverlayController {

    private val mainHandler = Handler(Looper.getMainLooper())

    @Volatile
    private var activeOverlayView: View? = null

    @Volatile
    private var activeWindowManager: WindowManager? = null

    @Volatile
    private var activePopupPackage: String = ""
    @Volatile
    private var activeSpeech: StrictBotPopupSpeech? = null
    @Volatile
    private var activeBotView: DisciplineBotWalkingView? = null
    private val presentationEpoch = AtomicLong()
    @Volatile private var fallbackActivity: WeakReference<RestrictionLockActivity>? = null
    private data class PendingFallback(val pkg: String, val entry: Long, val epoch: Long)
    @Volatile private var pendingFallback: PendingFallback? = null
    // Main-thread only. A single short recheck absorbs window-root attachment
    // gaps without allowing repeated observations to extend the deadline.
    private val foregroundRecheck = PopupForegroundRecheck()

    private val entrySession = PopupEntrySession()
    private var lastRecordedEntry = -1L

    fun onNonRestrictedForegroundObserved(foregroundPkg: String) {
        if (ownsFallbackForeground(foregroundPkg)) return
        if (foregroundPkg.isNotBlank()) {
            if (!TargetAppMonitoringPolicy.isTargetPackage(foregroundPkg)) entrySession.observeExit()
            else entrySession.observeTarget(foregroundPkg)
        }
        // Never keep overlay showing when user transitions to home launcher or non-restricted app
        hidePopupImmediate()
    }

    fun isDismissedForCurrentSession(targetPackage: String, nowMs: Long = System.currentTimeMillis()): Boolean {
        entrySession.observeTarget(targetPackage)
        return !entrySession.canPresent(targetPackage)
    }

    fun markSessionDismissed(targetPackage: String) { entrySession.dismiss(targetPackage) }

    internal fun prepareDemoAccessRecheck(context: Context) {
        if (isRestrictionOverlayShowing() || pendingFallback != null || FinancialSafePause.isPauseActive(context)) return
        val pkg = StrictAccessibilityService.activeInstance?.confirmedForegroundTargetPackage() ?: return
        if (TargetAppMonitoringPolicy.mayMonitor(context, pkg) &&
            !FinancialSafePause.isProtectedFinancialPackage(context, pkg)) entrySession.onEligibilityChanged(pkg)
    }


    /** Unknown metadata cannot create a popup or count as a new app visit.
     * Recheck an already visible shield on the main thread, where the worker's
     * 150ms window-query timeout cannot cause a detach/reattach cycle. */
    fun onForegroundUncertain(context: Context) {
        val epoch = presentationEpoch.get()
        mainHandler.post {
            if (presentationEpoch.get() != epoch) return@post
            if (ownsFallbackForeground(context.packageName)) return@post
            val view = activeOverlayView ?: return@post
            val expectedPackage = activePopupPackage
            val request = foregroundRecheck.schedule(expectedPackage, SystemClock.uptimeMillis()) ?: return@post
            val check = Runnable {
                if (!foregroundRecheck.complete(request)) return@Runnable
                if (presentationEpoch.get() != epoch || activeOverlayView !== view) return@Runnable
                val live = StrictAccessibilityService.activeInstance?.confirmedForegroundPackage()
                if (FinancialSafePause.isProtectedFinancialPackage(context, live)) {
                    FinancialSafePause.onProtectedPackageObserved(context, live.orEmpty())
                    onNonRestrictedForegroundObserved(live.orEmpty())
                    return@Runnable
                }
                val mayKeep = live == expectedPackage && HardwareDeviceVerifier.isMonitoringScreenAvailable(context) &&
                    !FinancialSafePause.isPauseActive(context) && TargetAppMonitoringPolicy.mayMonitor(context, live)
                if (mayKeep) return@Runnable
                if (!live.isNullOrBlank()) onNonRestrictedForegroundObserved(live)
                else hidePopupImmediate()
            }
            mainHandler.postDelayed(check, request.remainingMs(SystemClock.uptimeMillis()))
        }
    }

    private fun cancelForegroundRecheck() {
        foregroundRecheck.cancel()
    }

    fun ownsFallbackForeground(pkg: String): Boolean = fallbackActivity?.get()?.let {
        pkg == it.packageName && it.isPopupResumed && !it.isFinishing && !it.isDestroyed
    } == true

    internal fun ownsOverlayForeground(pkg: String?): Boolean = !pkg.isNullOrBlank() &&
        activeOverlayView?.isAttachedToWindow == true && activePopupPackage == pkg

    internal fun registerFallback(activity: RestrictionLockActivity, pkg: String, entry: Long, restoring: Boolean = false): Boolean {
        val pending = pendingFallback
        val expected = pending != null && pending.pkg == pkg && pending.entry == entry && pending.epoch == presentationEpoch.get()
        if (!entrySession.isPresentationCurrent(pkg, entry) ||
            (fallbackActivity?.get() !== activity && !expected && !restoring)) return false
        pendingFallback = null
        fallbackActivity = WeakReference(activity)
        return true
    }

    internal fun releaseFallback(activity: RestrictionLockActivity, pkg: String, entry: Long) {
        if (fallbackActivity?.get() !== activity) return
        fallbackActivity = null
        if (!activity.isChangingConfigurations && entrySession.isPresentationCurrent(pkg, entry)) entrySession.release(pkg)
    }

    fun pauseMediaAudio(context: Context, targetPackage: String) {
        BackgroundMusicRestrictionManager.pauseRestrictedPlayback(context, targetPackage)
    }

    fun closeRestrictedApp(context: Context, targetPackage: String) {
        if (FinancialSafePause.isPauseActive(context) || FinancialSafePause.isProtectedFinancialPackage(context, targetPackage)) return
        Log.i("StrictRestriction", "platform=Android target=$targetPackage action=close_restricted_app")
        // Retain dismissal until a real launcher/non-target observation. A
        // delayed target event during Home navigation must not reopen the card.
        // 1. Immediately pause media playback (stops background audio in Spotify, Samsung Music, YouTube)
        pauseMediaAudio(context, targetPackage)

        // 2. Perform HOME global action via AccessibilityService if active
        try {
            StrictAccessibilityService.activeInstance?.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
        } catch (_: Throwable) {}

        // 3. Launch HOME intent to return to launcher and close foreground application
        try {
            val homeIntent = Intent(Intent.ACTION_MAIN).apply {
                addCategory(Intent.CATEGORY_HOME)
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED
            }
            context.startActivity(homeIntent)
        } catch (e: Throwable) {
            Log.e("StrictRestriction", "Failed to launch home intent: ${e.message}")
        }
    }

    /**
     * Shows the prominent centered StrictBot restriction card over the restricted app,
     * with exactly one green action: BACK TO HOME.
     */
    fun showStrictBotPopup(
        context: Context,
        decision: DisciplineScoringRules.RestrictionDecision
    ) {
        val now = System.currentTimeMillis()
        val pkg = decision.target.packageName
        if (!decision.isRestricted) {
            hidePopupImmediate()
            return
        }
        // Never show restriction overlay during Financial Safe Pause or over protected banking/UPI apps
        if (FinancialSafePause.isPauseActive(context, now) || FinancialSafePause.isProtectedFinancialPackage(context, pkg)) {
            hidePopupImmediate()
            return
        }
        val entryId = entrySession.observeTarget(pkg)
        val requestEpoch = presentationEpoch.get()
        mainHandler.post {
            if (presentationEpoch.get() != requestEpoch) return@post
            if (FinancialSafePause.isPauseActive(context, System.currentTimeMillis())) {
                hidePopupImmediate()
                return@post
            }
            fallbackActivity?.get()?.takeIf { it.isPopupResumed && !it.isFinishing && !it.isDestroyed }?.let {
                if (it.restrictedPackage == pkg) {
                    it.updateRestriction(decision)
                    return@post
                }
            }
            // Validate again on the UI thread immediately before creating a
            // window. Queued or cached target events cannot show over a new app.
            if (!HardwareDeviceVerifier.isMonitoringScreenAvailable(context) ||
                !TargetAppMonitoringPolicy.mayMonitor(context, pkg)) {
                hidePopupImmediate()
                return@post
            }
            StrictAccessibilityService.activeInstance?.let { service ->
                val live = service.confirmedForegroundPackage()
                if (live != pkg) {
                    if (live.isNullOrBlank()) onForegroundUncertain(context)
                    else {
                        if (FinancialSafePause.isProtectedFinancialPackage(context, live)) {
                            FinancialSafePause.onProtectedPackageObserved(context, live)
                        }
                        onNonRestrictedForegroundObserved(live)
                    }
                    return@post
                }
            }
            cancelForegroundRecheck()
            if (activeOverlayView != null && activePopupPackage == pkg) {
                updateCard(decision)
                return@post
            }
            if (pendingFallback?.let { it.pkg == pkg && it.entry == entryId } == true) return@post
            if (!entrySession.claim(pkg, entryId)) return@post
            val continuingMotion=activeBotView?.model?.takeIf {
                activeOverlayView!=null && activePopupPackage.isNotBlank() && activePopupPackage!=pkg
            }
            hidePopup(cancelPendingRequests = false)

            val a11yContext: Context? = when {
                context is AccessibilityService -> context
                StrictAccessibilityService.activeInstance != null -> StrictAccessibilityService.activeInstance
                else -> null
            }

            val canDrawAppOverlay = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                Settings.canDrawOverlays(context)
            } else {
                true
            }

            val overlayHostContext = a11yContext ?: if (canDrawAppOverlay) context.applicationContext else null
            val windowType = when {
                a11yContext != null -> WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
                canDrawAppOverlay -> if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                } else {
                    @Suppress("DEPRECATION")
                    WindowManager.LayoutParams.TYPE_PHONE
                }
                else -> -1
            }

            if (overlayHostContext != null && windowType != -1) {
                val shown = attachSlidingOverlayWindow(overlayHostContext, windowType, decision, continuingMotion)
                if (shown) return@post
            }

            // Keep the presentation claim while Android creates the fallback.
            // Releasing it here used to enqueue the same activity repeatedly.
            val pending = PendingFallback(pkg, entryId, presentationEpoch.get())
            pendingFallback = pending
            try {
                val intent = Intent(context, RestrictionLockActivity::class.java).apply {
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                    putExtra(RestrictionLockActivity.EXTRA_PACKAGE_NAME, pkg)
                    putExtra(RestrictionLockActivity.EXTRA_SESSION_ID, entryId)
                    putExtra(RestrictionLockActivity.EXTRA_APP_NAME, decision.appName)
                    putExtra(RestrictionLockActivity.EXTRA_CURRENT_SCORE, decision.dailyScore)
                    putExtra(RestrictionLockActivity.EXTRA_REQUIRED_SCORE, decision.requiredScore)
                    putExtra(RestrictionLockActivity.EXTRA_REASON_CODE, decision.action)
                    putExtra(RestrictionLockActivity.EXTRA_REASON_DETAIL, decision.reason)
                    putExtra(RestrictionLockActivity.EXTRA_NEXT_TASK_TITLE, decision.nextTaskTitle)
                    putExtra(RestrictionLockActivity.EXTRA_NEXT_TASK_WINDOW, decision.nextTaskWindow)
                }
                context.startActivity(intent)
                // Background activity launch restrictions can reject a launch
                // without throwing. Allow a later observation to retry it.
                mainHandler.postDelayed({
                    if (pendingFallback === pending) {
                        pendingFallback = null
                        entrySession.failedAttach(pkg, entryId)
                        Log.w("StrictRestriction", "platform=Android target=$pkg session=$entryId popup=fallback_not_attached")
                    }
                }, 2000L)
            } catch (e: Exception) {
                pendingFallback = null
                entrySession.failedAttach(pkg, entryId)
                Log.e("StrictRestriction", "Fallback launch failed: ${e.message}")
            }
        }
    }

    private fun attachSlidingOverlayWindow(
        hostContext: Context,
        windowType: Int,
        decision: DisciplineScoringRules.RestrictionDecision,
        continuingMotion: PremiumBotMotion? = null
    ): Boolean {
        val targetPackage = decision.target.packageName
        if (FinancialSafePause.isPauseActive(hostContext) || FinancialSafePause.isProtectedFinancialPackage(hostContext, targetPackage)) {
            return false
        }
        return try {
            val wm = hostContext.getSystemService(Context.WINDOW_SERVICE) as WindowManager
            val density = hostContext.resources.displayMetrics.density
            fun dp(v: Float) = (v * density).toInt()

            val card = buildStrictBotCardView(
                context = hostContext,
                decision = decision,
                onBackToHome = {
                    returnToHome(hostContext, targetPackage)
                }
            )

            // Build coordinated robot assistant container
            val robotContainer = LinearLayout(hostContext).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.START or Gravity.BOTTOM
                tag = "coordinated-robot-container"
            }

            // 1. Speech bubble positioned above the robot
            val bubble = DisciplineSpeechBubbleView(hostContext).apply {
                tag = "coordinated-robot-bubble"
                val bubbleMsg = if (decision.dailyScore < decision.requiredScore)
                    FloatingBotStateSelector.MSG_INSUFFICIENT_POINTS
                else
                    "Great! You have enough points."
                setState(FloatingBotStateSelector.MessageState.INSUFFICIENT_POINTS, bubbleMsg)
                visibility = View.INVISIBLE
                alpha = 0f
                scaleX = 0.8f
                scaleY = 0.8f
            }
            val availableWidth = (hostContext.resources.displayMetrics.widthPixels - dp(32f)).coerceAtLeast(1)
            val bubbleLp = LinearLayout.LayoutParams(
                minOf(dp(250f), availableWidth),
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply {
                bottomMargin = dp(4f)
            }
            robotContainer.addView(bubble, bubbleLp)

            // 2. Articulated crowned robot
            val botView = DisciplineBotWalkingView(hostContext).apply {
                tag = "coordinated-robot-bot"
                setPresentation(scale = 1.85f, stationary = false)
                setWarning(false)
            }
            continuingMotion?.let { botView.continueMotion(it) }
            activeBotView = botView
            val botLp = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(185f))
            robotContainer.addView(botView, botLp)

            val outerContainer = RestrictionPopupSurface(hostContext, card, robotContainer)

            val lp = WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                windowType,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT
            ).apply {
                gravity = Gravity.FILL
                width = WindowManager.LayoutParams.MATCH_PARENT
                height = WindowManager.LayoutParams.MATCH_PARENT
                x = 0
                y = 0
            }

            wm.addView(outerContainer, lp)
            val entry = entrySession.currentId()
            Log.i("StrictRestriction", "platform=Android target=$targetPackage session=$entry eligibility=restricted popup=attached score=${decision.dailyScore} threshold=${decision.requiredScore}")
            if (lastRecordedEntry != entry) {
                lastRecordedEntry = entry
                if (!decision.historyFreeRefresh) {
                    runCatching { StrictRecordsLocalDb.getInstance(hostContext).recordRestrictionEvent(
                        targetPackage, decision.appName, decision.dailyScore, decision.requiredScore, "RESTRICTED", decision.reason) }
                }
            }
            activeOverlayView = outerContainer
            activeWindowManager = wm
            activePopupPackage = decision.target.packageName

            // Pause media in the background
            pauseMediaAudio(hostContext, targetPackage)

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                outerContainer.post {
                    outerContainer.findOnBackInvokedDispatcher()?.registerOnBackInvokedCallback(
                        android.window.OnBackInvokedDispatcher.PRIORITY_OVERLAY
                    ) {
                        returnToHome(hostContext, targetPackage)
                    }
                }
            }

            animateCardEntrance(card)
            animateRobotEntrance(robotContainer, bubble, botView, decision, hostContext, entry)
            if(BuildConfig.DEBUG)Log.i("StrictBotMotion","target=$targetPackage state=${botView.currentAnimState} continued=${continuingMotion!=null}")
            true
        } catch (error: Throwable) {
            Log.e("StrictRestriction", "platform=Android target=$targetPackage session=${entrySession.currentId()} popup=attach_failed reason=${error.javaClass.simpleName}")
            false
        }
    }

    private fun animateRobotEntrance(
        robotContainer: View,
        bubble: DisciplineSpeechBubbleView,
        botView: DisciplineBotWalkingView,
        decision: DisciplineScoringRules.RestrictionDecision,
        hostContext: Context,
        sessionId: Long
    ) {
        // Root travel and the gait share the View's clock. Do not translate the container.
        botView.playEntrance {
            if (activeOverlayView == null || activeBotView !== botView) return@playEntrance
            if(botView.currentAnimState==PremiumBotMotion.BotAnimState.IDLE)
                botView.setAnimState(PremiumBotMotion.BotAnimState.GESTURING)
            bubble.visibility = View.VISIBLE
            bubble.alpha = 0f
            bubble.scaleX = 1f; bubble.scaleY = 1f
            bubble.animate().alpha(1f).setDuration(220L).start()
            val message = if(decision.isRestricted)
                "No Entertainment apps unless sufficient points."
                else "Great! You have enough points. Enjoy your application."
            activeSpeech?.close()
            activeSpeech = StrictBotPopupSpeech(hostContext,
                onSpeaking = { value -> mainHandler.post { if(activeBotView===botView)botView.setSpeaking(value) } },
                onStatus = {})
            activeSpeech?.speak(message,"restriction:${decision.target.packageName}:$sessionId",manual=true)
        }
    }

    fun isRestrictionOverlayShowing(): Boolean = activeOverlayView != null ||
        fallbackActivity?.get()?.let { it.isPopupResumed && !it.isFinishing && !it.isDestroyed } == true

    fun updateVisibleRestriction(decision: DisciplineScoringRules.RestrictionDecision) {
        val epoch = presentationEpoch.get()
        mainHandler.post {
            if (presentationEpoch.get() != epoch) return@post
            cancelForegroundRecheck()
            updateCard(decision)
            fallbackActivity?.get()?.takeIf { it.restrictedPackage == decision.target.packageName }
                ?.updateRestriction(decision)
        }
    }

    private fun updateCard(decision: DisciplineScoringRules.RestrictionDecision) {
        if (activePopupPackage != decision.target.packageName) return
        updateCardText(activeOverlayView, decision)
    }

    internal fun updateCardText(root: View?, decision: DisciplineScoringRules.RestrictionDecision) {
        fun setChangedText(tag: String, value: String) {
            root?.findViewWithTag<TextView>(tag)?.let { if (it.text.toString() != value) it.text = value }
        }
        setChangedText("restriction-status", "Status: ${decision.reason}")
        setChangedText("restriction-score",
            "Score: ${decision.dailyScore}/100 • Unlocks at: ${decision.requiredScore} points")
    }

    internal fun animateCardEntrance(card: View) {
        card.translationY = 20f * card.resources.displayMetrics.density
        card.alpha = 0f
        card.animate().translationY(0f).alpha(1f).setDuration(180L)
            .setInterpolator(DecelerateInterpolator()).start()
    }

    internal fun animateCardExit(card: View, afterDismiss: () -> Unit) {
        card.animate().cancel()
        card.animate().translationY(16f * card.resources.displayMetrics.density).alpha(0f)
            .setDuration(120L).setInterpolator(AccelerateInterpolator())
            .withEndAction(afterDismiss).start()
    }

    /** Revalidate before a user-requested Home action touches the external app. */
    private fun leaveTargetOnUserRequest(context: Context, expectedPackage: String) {
        animateAndDismissOverlay {
            closeRestrictedApp(context, expectedPackage)
        }
    }

    fun returnToHome(context: Context, expectedPackage: String = "") {
        if (expectedPackage.isNotBlank()) {
            markSessionDismissed(expectedPackage)
            leaveTargetOnUserRequest(context, expectedPackage)
        } else {
            hidePopupImmediate()
        }
    }

    fun buildStrictBotCardView(
        context: Context,
        decision: DisciplineScoringRules.RestrictionDecision,
        onBackToHome: () -> Unit
    ): LinearLayout {
        val density = context.resources.displayMetrics.density
        fun dp(v: Int): Int = (v * density).toInt()

        val card = LinearLayout(context).apply {
            tag = "restriction-card"
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(22), dp(20), dp(22), dp(20))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#0D1520"))
                cornerRadius = dp(20).toFloat()
                setStroke(dp(2), Color.parseColor("#168BFF"))
            }
        }

        val shieldBadge = FrameLayout(context).apply {
            layoutParams = LinearLayout.LayoutParams(dp(56), dp(56)).apply {
                gravity = Gravity.CENTER_HORIZONTAL
                bottomMargin = dp(14)
            }
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(Color.parseColor("#082238"))
                setStroke(dp(2), Color.parseColor("#0284C7"))
            }
            addView(TextView(context).apply {
                text = "🛡️"
                textSize = 24f
                gravity = Gravity.CENTER
            }, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        }
        card.addView(shieldBadge)

        val headline = TextView(context).apply {
            text = "Entertainment Access Blocked"
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f)
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, dp(6))
        }
        card.addView(headline)

        val message = TextView(context).apply {
            text = "This app opens only when sufficient points are available."
            setTextColor(Color.parseColor("#ADB8C6"))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13.5f)
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, dp(16))
        }
        card.addView(message)

        val appName = decision.appName.ifBlank { decision.target.displayName }
        val statusBox = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(12), dp(14), dp(12))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#111A25"))
                cornerRadius = dp(12).toFloat()
                setStroke(dp(1), Color.parseColor("#243244"))
            }
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(18) }
        }

        val targetRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(6)
            }
        }
        targetRow.addView(TextView(context).apply {
            text = "TARGET APP"
            setTextColor(Color.parseColor("#40B7FF"))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            typeface = Typeface.DEFAULT_BOLD
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        })
        targetRow.addView(TextView(context).apply {
            text = appName
            setTextColor(Color.parseColor("#FF8C42"))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            typeface = Typeface.DEFAULT_BOLD
        })
        statusBox.addView(targetRow)

        statusBox.addView(TextView(context).apply {
            tag = "restriction-score"
            text = "Score: ${decision.dailyScore}/100 • Unlocks at: ${decision.requiredScore} points"
            setTextColor(Color.parseColor("#56DCED"))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            typeface = Typeface.DEFAULT_BOLD
            setPadding(0, 0, 0, dp(4))
        })

        statusBox.addView(TextView(context).apply {
            tag = "restriction-status"
            text = "Status: ${decision.reason}"
            setTextColor(Color.parseColor("#D2DFE9"))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
        })

        card.addView(statusBox)

        card.addView(Button(context).apply {
            text = "🏠 Back to Home"
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            typeface = Typeface.DEFAULT_BOLD
            minimumHeight = dp(52)
            setPadding(dp(16), dp(14), dp(16), dp(14))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#10B981"))
                cornerRadius = dp(12).toFloat()
            }
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            )
            setOnClickListener {
                isEnabled = false
                activeSpeech?.stop()
                onBackToHome()
            }
        })

        return card
    }

    private fun animateAndDismissOverlay(afterDismiss: (() -> Unit)?) {
        val view = activeOverlayView
        if (view == null) {
            afterDismiss?.invoke()
            return
        }
        val card = view.findViewWithTag<View>("restriction-card") ?: view
        animateCardExit(card) {
            if (activeOverlayView !== view) return@animateCardExit
            hidePopupImmediate()
            afterDismiss?.invoke()
        }
    }

    fun hidePopupImmediate() {
        hidePopup(cancelPendingRequests = true)
    }

    private fun hidePopup(cancelPendingRequests: Boolean) {
        if (cancelPendingRequests) presentationEpoch.incrementAndGet()
        activeSpeech?.stop()
        activeSpeech?.close()
        activeSpeech = null
        activeBotView?.stopAnimation()
        activeBotView = null
        val expectedView = activeOverlayView
        val expectedFallback = fallbackActivity?.get()
        val expectedPending = pendingFallback
        val hide = Runnable {
            cancelForegroundRecheck()
            if (pendingFallback === expectedPending) {
                pendingFallback = null
                expectedPending?.let { entrySession.failedAttach(it.pkg, it.entry) }
            }
            if (expectedFallback != null && fallbackActivity?.get() === expectedFallback) {
                if (entrySession.isPresentationCurrent(expectedFallback.restrictedPackage, expectedFallback.restrictionSessionId)) {
                    entrySession.release(expectedFallback.restrictedPackage)
                }
                fallbackActivity = null
                expectedFallback.dismissFromController()
            }
            // A delayed cleanup from an older observation must not remove a new shield.
            if (activeOverlayView !== expectedView) return@Runnable
            val view = activeOverlayView
            val wm = activeWindowManager
            if (activePopupPackage.isNotBlank()) {
                Log.i("StrictRestriction", "platform=Android target=$activePopupPackage session=${entrySession.currentId()} popup=detached")
                entrySession.release(activePopupPackage)
            }
            activeOverlayView = null
            activeWindowManager = null
            activePopupPackage = ""
            if (view != null && wm != null) {
                try {
                    wm.removeViewImmediate(view)
                } catch (_: Throwable) {
                    try {
                        wm.removeView(view)
                    } catch (_: Throwable) {}
                }
            }
        }
        if (Looper.myLooper() == Looper.getMainLooper()) hide.run()
        else mainHandler.post(hide)
    }
}
