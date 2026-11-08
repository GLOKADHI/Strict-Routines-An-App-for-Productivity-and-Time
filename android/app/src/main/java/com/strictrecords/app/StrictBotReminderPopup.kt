package com.strictrecords.app

import android.app.NotificationManager
import android.app.Notification
import android.app.KeyguardManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.os.PowerManager
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/** A compact, temporary companion to the existing task notification, not a task gate. */
internal object StrictBotReminderPopup {
    private val main = Handler(Looper.getMainLooper())
    private val events = BotAnnouncementPolicy()
    private var view: View? = null
    private var manager: WindowManager? = null
    private var announcement: StrictBotAnnouncementView? = null
    private var check: Runnable? = null

    private fun mayShow(context: Context): Boolean {
        if (!HardwareDeviceVerifier.isMonitoringScreenAvailable(context) ||
            FinancialSafePause.isPauseActive(context) || StrictBotOverlayController.isRestrictionOverlayShowing()) return false
        val foreground = StrictAccessibilityService.activeInstance?.confirmedForegroundPackage().orEmpty()
        // Unknown foreground, the proof app itself, settings and protected system
        // screens retain the normal notification instead of receiving an overlay.
        if (foreground.isBlank() || foreground == context.packageName ||
            foreground in FloatingBotStateSelector.SYSTEM_CRITICAL_PACKAGES ||
            foreground.startsWith("com.android.settings") ||
            FinancialSafePause.isProtectedFinancialPackage(context, foreground)) return false
        val notifications = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        return notifications.areNotificationsEnabled() &&
            (Build.VERSION.SDK_INT < 26 || (notifications.getNotificationChannel(WakeUpAlarmReceiver.CHANNEL_ID)?.importance
                ?: NotificationManager.IMPORTANCE_NONE) >= NotificationManager.IMPORTANCE_HIGH) &&
            notifications.currentInterruptionFilter == NotificationManager.INTERRUPTION_FILTER_ALL
    }

    fun configureLockScreenAlert(context: Context, builder: Notification.Builder, title: String,
        message: String, mode: String, activityId: String?, notifId: Int, openTask: PendingIntent) {
        runCatching {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val km = context.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
            val pm = context.getSystemService(Context.POWER_SERVICE) as PowerManager
            val allowed = Build.VERSION.SDK_INT < 34 || nm.canUseFullScreenIntent()
            val channelEnabled = Build.VERSION.SDK_INT < 26 ||
                (nm.getNotificationChannel(WakeUpAlarmReceiver.CHANNEL_ID)?.importance ?: NotificationManager.IMPORTANCE_NONE) >= NotificationManager.IMPORTANCE_HIGH
            if (!BotReminderLaunchPolicy.useFullScreen(mode, activityId, km.isKeyguardLocked,
                    pm.isInteractive, allowed, nm.areNotificationsEnabled() && channelEnabled) ||
                FinancialSafePause.isPauseActive(context) ||
                nm.currentInterruptionFilter != NotificationManager.INTERRUPTION_FILTER_ALL) return
            val created = SystemClock.elapsedRealtime()
            val screen = Intent(context, StrictBotReminderActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                putExtra("TITLE", title); putExtra("MESSAGE", message)
                putExtra("MODE", mode); putExtra("ACTIVITY_ID", activityId.orEmpty())
                putExtra("EVENT_KEY", "reminder:${activityId.orEmpty()}:$mode:$notifId")
                putExtra("CREATED_ELAPSED", created)
                putExtra("EXPIRES_ELAPSED", created + BotReminderLaunchPolicy.DISPLAY_MS)
                putExtra("OPEN_TASK", openTask)
            }
            val fullScreen = PendingIntent.getActivity(context, notifId xor 0x5B070,
                screen, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            builder.setCategory(Notification.CATEGORY_ALARM).setFullScreenIntent(fullScreen, true)
        }
        // No forced activity launch or wake-lock fallback when Android denies it.
    }

    fun show(context: Context, title: String, message: String, eventKey: String, openTask: PendingIntent) {
        val app = context.applicationContext
        main.post {
            if (!runCatching { mayShow(app) }.getOrDefault(false)) return@post
            val service = StrictAccessibilityService.activeInstance
            val host: Context = service ?: app
            val type = when {
                service != null -> WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
                Settings.canDrawOverlays(app) && Build.VERSION.SDK_INT >= 26 -> WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                Settings.canDrawOverlays(app) -> {
                    @Suppress("DEPRECATION")
                    WindowManager.LayoutParams.TYPE_PHONE
                }
                else -> return@post
            }
            if (!events.claim(eventKey, SystemClock.uptimeMillis(), true, true)) return@post
            hide()
            val density = host.resources.displayMetrics.density
            fun dp(value: Int) = (value * density).toInt()
            val wm = host.getSystemService(Context.WINDOW_SERVICE) as WindowManager
            val card = LinearLayout(host).apply {
                orientation = LinearLayout.VERTICAL; setPadding(dp(10), dp(10), dp(10), dp(10))
                background = GradientDrawable().apply {
                    setColor(Color.parseColor("#101C27")); cornerRadius = dp(16).toFloat()
                    setStroke(dp(1), Color.parseColor("#24AFA1"))
                }
            }
            card.addView(TextView(host).apply {
                text = title.removePrefix("STRICTROUTINES — "); textSize = 13f
                setTextColor(Color.parseColor("#5DE4C5")); setPadding(dp(2), 0, dp(2), dp(8))
            })
            val bot = StrictBotAnnouncementView(host, message, eventKey)
            card.addView(bot, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT))
            val actions = LinearLayout(host).apply { orientation = LinearLayout.HORIZONTAL }
            actions.addView(Button(host).apply {
                text = "Open task"; isAllCaps = false; textSize = 12f; minimumHeight = dp(44)
                setOnClickListener {
                    hide()
                    runCatching { openTask.send() }
                    // The original notification remains available if Android declines
                    // a background activity launch. Existing form checks still apply.
                }
            }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            actions.addView(Button(host).apply {
                text = "Dismiss"; isAllCaps = false; textSize = 12f; minimumHeight = dp(44)
                setOnClickListener { hide() }
            }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            card.addView(actions)
            val scroll = ScrollView(host).apply { addView(card); isFillViewport = false }
            val width = minOf(dp(340), (host.resources.displayMetrics.widthPixels - dp(24)).coerceAtLeast(1))
            val heightLimit = (host.resources.displayMetrics.heightPixels * 0.75f).toInt().coerceAtLeast(1)
            val params = WindowManager.LayoutParams(width, WindowManager.LayoutParams.WRAP_CONTENT, type,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                PixelFormat.TRANSLUCENT).apply { gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL; y = dp(12) }
            scroll.measure(View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(heightLimit, View.MeasureSpec.AT_MOST))
            params.height = minOf(scroll.measuredHeight, heightLimit)
            try {
                wm.addView(scroll, params)
                view = scroll; manager = wm; announcement = bot
                val expires = SystemClock.uptimeMillis() + 20_000L
                val watchdog = object : Runnable {
                    override fun run() {
                        if (view !== scroll) return
                        if (SystemClock.uptimeMillis() >= expires || !runCatching { mayShow(app) }.getOrDefault(false)) hide()
                        else main.postDelayed(this, 1_000L)
                    }
                }
                check = watchdog
                main.postDelayed(watchdog, 1_000L)
            } catch (_: Exception) {
                bot.releaseSpeech()
                // Missing/revoked overlay permission never affects the normal alert.
            }
        }
    }

    private fun hide() {
        check?.let { main.removeCallbacks(it) }; check = null
        announcement?.releaseSpeech(); announcement = null
        val old = view; view = null
        if (old != null) runCatching { manager?.removeView(old) }
        manager = null
    }
}
