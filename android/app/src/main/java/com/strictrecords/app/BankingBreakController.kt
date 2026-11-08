package com.strictrecords.app

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log

/** Suspends entertainment monitoring, then resumes it without opening an Activity.
 * The system-owned Accessibility binding stays enabled; no secure settings are changed. */
object BankingBreakController {
    private const val ACTION_RESUME = "com.strictrecords.app.ACTION_RESUME_BANKING_BREAK"
    private const val REQUEST_CODE = 65005
    private val handler = Handler(Looper.getMainLooper())
    private var scheduledDeadline = 0L
    private var resumeTask: Runnable? = null
    private var widgetPhase: BankingBreakPlan.Phase? = null

    fun restore(context: Context) {
        val app = context.applicationContext
        handler.post { applyState(app) }
    }

    private fun applyState(context: Context) {
        val now = System.currentTimeMillis()
        val state = FinancialSafePause.getDiagnosticsState(context, now)
        val currentPhase = BankingBreakPlan.phase(state.resumeDeadlineMs,state.isProtectedInForeground,now)
        if (widgetPhase != currentPhase) {
            widgetPhase=currentPhase
            StrictWidgetUpdates.request(context,"monitoring")
        }
        when (BankingBreakPlan.phase(state.resumeDeadlineMs, state.isProtectedInForeground, now)) {
            BankingBreakPlan.Phase.SUSPENDED -> {
                StrictBotOverlayController.hidePopupImmediate()
                HardwareDeviceVerifier.lastAccessibilityForegroundPkg = ""
                HardwareDeviceVerifier.lastAccessibilityEventTimeMs = 0L
                StrictAccessibilityService.activeInstance?.suspendForBankingBreak()
                StrictEnforcementService.activeInstance?.suspendTargetMonitoring()
                scheduleResume(context, state.resumeDeadlineMs, now)
            }
            BankingBreakPlan.Phase.WAITING_FOR_BANK_EXIT -> {
                cancelResume(context)
                StrictEnforcementService.activeInstance?.suspendTargetMonitoring()
                // Only foreground package metadata is needed to observe the bank exit.
                StrictAccessibilityService.activeInstance?.suspendForBankingBreak()
            }
            BankingBreakPlan.Phase.MONITORING -> {
                cancelResume(context)
                StrictAccessibilityService.activeInstance?.resumeAfterBankingBreak()
                StrictEnforcementService.activeInstance?.resumeTargetMonitoring()
            }
        }
    }

    private fun resumeIntent(context: Context, flags: Int): PendingIntent? = PendingIntent.getBroadcast(
        context, REQUEST_CODE, Intent(context, BankingBreakResumeReceiver::class.java).setAction(ACTION_RESUME),
        flags or PendingIntent.FLAG_IMMUTABLE)

    private fun scheduleResume(context: Context, deadline: Long, now: Long) {
        if (scheduledDeadline == deadline && resumeTask != null) return
        resumeTask?.let { handler.removeCallbacks(it) }
        scheduledDeadline = deadline
        resumeTask = Runnable {
            if (scheduledDeadline != deadline) return@Runnable
            resumeTask = null
            applyState(context)
        }.also { handler.postDelayed(it, (deadline - now).coerceAtLeast(0L)) }
        val alarms = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
        val pending = resumeIntent(context, PendingIntent.FLAG_UPDATE_CURRENT) ?: return
        try {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || alarms.canScheduleExactAlarms()) {
                alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, deadline, pending)
            } else {
                alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, deadline, pending)
                Log.w("StrictRestriction", "platform=Android banking_break=resume_alarm_inexact reason=exact_alarm_unavailable")
            }
        } catch (_: SecurityException) {
            // Permission can be revoked between checking and scheduling.
            runCatching { alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, deadline, pending) }
            Log.w("StrictRestriction", "platform=Android banking_break=resume_alarm_inexact reason=permission_changed")
        }
        Log.i("StrictRestriction", "platform=Android banking_break=suspended resumeDeadlineMs=$deadline")
    }

    private fun cancelResume(context: Context) {
        resumeTask?.let { handler.removeCallbacks(it) }
        resumeTask = null
        scheduledDeadline = 0L
        val pending = resumeIntent(context, PendingIntent.FLAG_NO_CREATE) ?: return
        (context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager)?.cancel(pending)
    }

    internal fun receiveResume(context: Context, action: String?) {
        if (action != ACTION_RESUME) return
        restore(context)
        // A system alarm can recreate the process after normal process death.
        // start() is idempotent and handles background-start rejection itself.
        StrictEnforcementService.start(context)
    }
}

class BankingBreakResumeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        BankingBreakController.receiveResume(context, intent.action)
    }
}
