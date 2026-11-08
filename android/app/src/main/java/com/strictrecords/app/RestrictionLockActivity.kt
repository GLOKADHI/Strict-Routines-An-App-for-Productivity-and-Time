package com.strictrecords.app

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import org.json.JSONObject

class RestrictionLockActivity : Activity() {

    companion object {
        const val EXTRA_PACKAGE_NAME = "EXTRA_PACKAGE_NAME"
        const val EXTRA_SESSION_ID = "EXTRA_SESSION_ID"
        const val EXTRA_APP_NAME = "EXTRA_APP_NAME"
        const val EXTRA_CURRENT_SCORE = "EXTRA_CURRENT_SCORE"
        const val EXTRA_REQUIRED_SCORE = "EXTRA_REQUIRED_SCORE"
        const val EXTRA_REASON_CODE = "EXTRA_REASON_CODE"
        const val EXTRA_REASON_DETAIL = "EXTRA_REASON_DETAIL"
        const val EXTRA_NEXT_TASK_TITLE = "EXTRA_NEXT_TASK_TITLE"
        const val EXTRA_NEXT_TASK_WINDOW = "EXTRA_NEXT_TASK_WINDOW"
    }

    private var currentScore: Int = 0
    private var requiredScore: Int = 80
    private var targetAppName: String = ""
    private var targetPkgName: String = ""
    private var reasonCode: String = "SCORE_BELOW_THRESHOLD"
    private var reasonDetail: String = ""
    private var nextTaskTitle: String = ""
    private var nextTaskWindow: String = ""
    private var cardContainer: FrameLayout? = null
    private var isDismissing: Boolean = false
    private var sessionId: Long = -1L
    internal val restrictedPackage: String get() = targetPkgName
    internal val restrictionSessionId: Long get() = sessionId
    @Volatile internal var isPopupResumed: Boolean = false
        private set
    private val handler = Handler(Looper.getMainLooper())
    private val refreshRestriction = object : Runnable {
        override fun run() {
            if (!isPopupResumed || isFinishing || isDismissing) return
            if (FinancialSafePause.isPauseActive(this@RestrictionLockActivity) ||
                !TargetAppMonitoringPolicy.mayMonitor(this@RestrictionLockActivity, targetPkgName)) {
                dismissFromController()
                return
            }
            runCatching {
                val db = StrictRecordsLocalDb.getInstance(this@RestrictionLockActivity)
                val decision = DisciplineScoringRules.evaluateRestrictionDecision(
                    db.getScore(db.nowDate(), false), targetPkgName,
                    appLocks = db.getRemoteAppLocks(), isEducationalUnlockActive = db.isEducationalUnlockActive(),
                    context = this@RestrictionLockActivity, db = db
                )
                if (decision.isRestricted) updateRestriction(decision)
                else dismissSmoothly(markSession = false)
            }.onFailure {
                Log.w("RestrictionLock", "Restriction status refresh unavailable: ${it.javaClass.simpleName}")
            }
            if (isPopupResumed && !isFinishing && !isDismissing) handler.postDelayed(this, 1000L)
        }
    }

    private val unlockReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            var locksStr = intent?.getStringExtra("APP_LOCKS")
            val b64 = intent?.getStringExtra("APP_LOCKS_B64")
            if (!b64.isNullOrEmpty()) {
                try {
                    val decoded = String(android.util.Base64.decode(b64, android.util.Base64.DEFAULT), Charsets.UTF_8)
                    locksStr = decoded
                } catch (_: Exception) {}
            }
            if (!locksStr.isNullOrEmpty()) {
                try {
                    val locksObj = JSONObject(locksStr)
                    val db = StrictRecordsLocalDb.getInstance(this@RestrictionLockActivity)
                    val today = db.nowDate()
                    val score = db.getScore(today, false)
                    val decision = DisciplineScoringRules.evaluateRestrictionDecision(score, targetPkgName, appLocks = locksObj, context = this@RestrictionLockActivity, db = db)
                    if (!decision.isRestricted) {
                        Log.i("RestrictionLock", "Target app '$targetPkgName' unlocked remotely. Dismissing popup.")
                        dismissSmoothly(markSession = false)
                    }
                } catch (_: Exception) {}
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        overridePendingTransition(0, 0)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            onBackInvokedDispatcher.registerOnBackInvokedCallback(
                android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT
            ) { }
        }

        readExtras(intent)
        if (targetPkgName.isBlank() || FinancialSafePause.isPauseActive(this) ||
            FinancialSafePause.isProtectedFinancialPackage(this, targetPkgName) ||
            !StrictBotOverlayController.registerFallback(this, targetPkgName, sessionId, restoring = savedInstanceState != null)) {
            finish()
            return
        }

        StrictBotOverlayController.pauseMediaAudio(this, targetPkgName)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                    WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
            )
        }

        buildSlidingPopupUi()

        try {
            val filter = IntentFilter("com.strictrecords.app.ACTION_UPDATE_APP_LOCKS")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                registerReceiver(unlockReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
            } else {
                registerReceiver(unlockReceiver, filter)
            }
        } catch (_: Exception) {}
    }

    override fun onDestroy() {
        handler.removeCallbacks(refreshRestriction)
        StrictBotOverlayController.releaseFallback(this, targetPkgName, sessionId)
        super.onDestroy()
        try {
            unregisterReceiver(unlockReceiver)
        } catch (_: Exception) {}
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        if (intent != null) {
            setIntent(intent)
            readExtras(intent)
            if (targetPkgName.isBlank() || FinancialSafePause.isPauseActive(this) ||
                FinancialSafePause.isProtectedFinancialPackage(this, targetPkgName) ||
                !StrictBotOverlayController.registerFallback(this, targetPkgName, sessionId)) {
                finish()
                return
            }
            StrictBotOverlayController.pauseMediaAudio(this, targetPkgName)
            buildSlidingPopupUi()
        }
    }

    override fun onResume() {
        super.onResume()
        isPopupResumed = true
        handler.removeCallbacks(refreshRestriction)
        handler.post(refreshRestriction)
    }

    override fun onPause() {
        isPopupResumed = false
        handler.removeCallbacks(refreshRestriction)
        super.onPause()
    }

    internal fun dismissFromController() {
        handler.removeCallbacks(refreshRestriction)
        // Remove the visible card immediately on a real foreground/safety exit;
        // the activity's default exit animation must not keep it above that app.
        window.decorView.visibility = View.GONE
        if (!isFinishing) {
            finish()
            overridePendingTransition(0, 0)
        }
    }

    internal fun updateRestriction(decision: DisciplineScoringRules.RestrictionDecision) {
        if (decision.target.packageName != targetPkgName || isFinishing || isDismissing) return
        currentScore = decision.dailyScore
        requiredScore = decision.requiredScore
        reasonDetail = decision.reason
        StrictBotOverlayController.updateCardText(cardContainer, decision)
    }

    private fun readExtras(i: Intent?) {
        currentScore = i?.getIntExtra(EXTRA_CURRENT_SCORE, 0) ?: 0
        requiredScore = i?.getIntExtra(EXTRA_REQUIRED_SCORE, 80) ?: 80
        targetAppName = i?.getStringExtra(EXTRA_APP_NAME) ?: ""
        targetPkgName = i?.getStringExtra(EXTRA_PACKAGE_NAME) ?: ""
        sessionId = i?.getLongExtra(EXTRA_SESSION_ID, -1L) ?: -1L
        reasonCode = i?.getStringExtra(EXTRA_REASON_CODE) ?: "SCORE_BELOW_THRESHOLD"
        reasonDetail = i?.getStringExtra(EXTRA_REASON_DETAIL) ?: ""
        nextTaskTitle = i?.getStringExtra(EXTRA_NEXT_TASK_TITLE) ?: ""
        nextTaskWindow = i?.getStringExtra(EXTRA_NEXT_TASK_WINDOW) ?: ""
    }

    private fun buildSlidingPopupUi() {
        isDismissing = false
        val decision = DisciplineScoringRules.RestrictionDecision(
            appName = targetAppName,
            packageName = targetPkgName,
            scoreAtDetection = currentScore,
            requiredScore = requiredScore,
            remainingPoints = (requiredScore - currentScore).coerceAtLeast(0),
            action = reasonCode,
            isRestricted = true,
            reason = reasonDetail.ifBlank { "Discipline score $currentScore/$requiredScore required." },
            nextTaskTitle = nextTaskTitle.ifBlank { "Complete your current scheduled discipline block" },
            nextTaskWindow = nextTaskWindow
        )

        val card = StrictBotOverlayController.buildStrictBotCardView(
            context = this,
            decision = decision,
            onBackToHome = {
                StrictBotOverlayController.returnToHome(this, targetPkgName)
                dismissSmoothly(markSession = true)
            }
        )

        val wrapper = RestrictionPopupSurface(this, card)

        cardContainer = wrapper
        setContentView(wrapper)
        StrictBotOverlayController.animateCardEntrance(card)
    }

    private fun dismissSmoothly(markSession: Boolean) {
        if (isDismissing) return
        isDismissing = true
        if (markSession) {
            StrictBotOverlayController.markSessionDismissed(targetPkgName)
        }
        val target = cardContainer
        if (target == null) {
            finish()
            overridePendingTransition(0, 0)
            return
        }
        val card = target.findViewWithTag<View>("restriction-card") ?: target
        StrictBotOverlayController.animateCardExit(card) {
            if (cardContainer !== target) return@animateCardExit
            finish()
            overridePendingTransition(0, 0)
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        // The restriction screen has a single explicit navigation button.
    }
}
