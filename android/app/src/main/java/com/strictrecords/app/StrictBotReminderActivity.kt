package com.strictrecords.app

import android.app.Activity
import android.app.KeyguardManager
import android.app.PendingIntent
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/** OS-delivered scheduled alarm surface. It never dismisses authentication itself. */
class StrictBotReminderActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private var bot: StrictBotAnnouncementView? = null
    private var eventKey = ""
    private var expires = 0L
    override fun onCreate(state: Bundle?) { super.onCreate(state); present(intent) }
    override fun onNewIntent(next: Intent?) {
        super.onNewIntent(next)
        if (next != null) { setIntent(next); present(next) }
    }

    private fun present(source: Intent) {
        main.removeCallbacksAndMessages(null)
        bot?.releaseSpeech()
        val created = source.getLongExtra("CREATED_ELAPSED", -1L)
        expires = source.getLongExtra("EXPIRES_ELAPSED", -1L)
        if (!BotReminderLaunchPolicy.isFresh(created, expires, SystemClock.elapsedRealtime()) ||
            FinancialSafePause.isPauseActive(this)) { finish(); return }
        val id = source.getStringExtra("ACTIVITY_ID").orEmpty()
        val db = StrictRecordsLocalDb.getInstance(this)
        val mode = source.getStringExtra("MODE").orEmpty()
        if (id.isNotBlank() || mode == "GYM_TIME") {
            val task = runCatching { db.getDailyActivities(db.nowDate()).firstOrNull {
                it.activityId == id || (id == "ACT-WAKEUP" && it.type == "WAKE_UP") ||
                    (id.isBlank() && mode == "GYM_TIME" && it.type == "GYM")
            } }.getOrNull()
            if (task == null || task.status in listOf("SUBMITTED", "VALIDATING", "VERIFIED", "APPROVED", "COMPLETED", "EXCUSED", "CANCELLED", "SKIPPED")) {
                finish(); return
            }
            if (db.authoritativeNowMs() >= db.parseIsoToMs(task.windowEnd)) { finish(); return }
        }
        if (Build.VERSION.SDK_INT >= 27) { setShowWhenLocked(true); setTurnScreenOn(true) }
        else {
            @Suppress("DEPRECATION")
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        eventKey = source.getStringExtra("EVENT_KEY").orEmpty()
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(dp(16), dp(16), dp(16), dp(16))
            background = GradientDrawable().apply { setColor(Color.parseColor("#10212A")); cornerRadius = dp(18).toFloat() }
        }
        card.addView(TextView(this).apply {
            text = source.getStringExtra("TITLE") ?: "StrictRoutines reminder"
            textSize = 17f; setTextColor(Color.WHITE); setPadding(0, 0, 0, dp(12))
        })
        val character = StrictBotAnnouncementView(this,
            source.getStringExtra("MESSAGE") ?: "A scheduled activity needs your attention.", eventKey,
            allowWhileLocked = true)
        bot = character
        card.addView(character, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))
        card.addView(TextView(this).apply {
            text = "Unlock to open your task. This reminder closes automatically."
            textSize = 12f; setTextColor(Color.parseColor("#B1C7D3")); setPadding(0, dp(10), 0, dp(6))
        })
        card.addView(Button(this).apply {
            text = "Open task"; isAllCaps = false
            setOnClickListener { openAfterUnlock(source) }
        })
        card.addView(Button(this).apply {
            text = "Dismiss reminder"; isAllCaps = false
            setOnClickListener { finish() }
        })
        setContentView(RestrictionPopupSurface(this, card))
        main.postDelayed({ finish() }, (expires - SystemClock.elapsedRealtime()).coerceAtLeast(1L))
    }

    private fun openAfterUnlock(source: Intent) {
        bot?.pauseSpeech()
        val key = eventKey
        val keyguard = getSystemService(KEYGUARD_SERVICE) as KeyguardManager
        val open = {
            if (!isFinishing && !isDestroyed && key == eventKey && SystemClock.elapsedRealtime() < expires) {
                @Suppress("DEPRECATION")
                val pending = if (Build.VERSION.SDK_INT >= 33) source.getParcelableExtra("OPEN_TASK", PendingIntent::class.java)
                    else source.getParcelableExtra<PendingIntent>("OPEN_TASK")
                runCatching { pending?.send() }
                finish()
            }
        }
        if (!keyguard.isKeyguardLocked) open()
        else if (Build.VERSION.SDK_INT >= 26) keyguard.requestDismissKeyguard(this,
            object : KeyguardManager.KeyguardDismissCallback() { override fun onDismissSucceeded() { open() } })
        // On API 24–25 the normal lock-screen notification remains available
        // after the user unlocks; no deprecated keyguard-disabling API is used.
    }
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    override fun onPause() { bot?.pauseSpeech(); bot?.bot?.stopAnimation(); super.onPause() }
    override fun onResume() { super.onResume(); if (SystemClock.elapsedRealtime() >= expires) finish() else bot?.bot?.startAnimation() }
    override fun onDestroy() { main.removeCallbacksAndMessages(null); bot?.releaseSpeech(); super.onDestroy() }
}
