package com.strictrecords.app

import android.app.AlertDialog
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import java.text.DateFormat
import java.util.Date

/** Settings-only action with a scoped countdown; no points writes or history UI. */
object DemoAccessSettingsCard {
    fun create(context: Context, actualPoints: () -> Int): View {
        fun dp(n: Int) = (n * context.resources.displayMetrics.density).toInt()
        val handler = Handler(Looper.getMainLooper())
        val card = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(20), dp(20), dp(20))
            background = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(Color.rgb(19, 32, 42), Color.rgb(15, 23, 32))).apply {
                cornerRadius = dp(22).toFloat(); setStroke(dp(1), Color.rgb(40, 66, 77))
            }
            layoutParams = LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(16) }
        }
        fun label(size: Float, color: String, bold: Boolean = false) = TextView(context).apply {
            textSize = size; setTextColor(Color.parseColor(color))
            if (bold) setTypeface(typeface, Typeface.BOLD)
            setLineSpacing(dp(3).toFloat(), 1f)
            layoutParams = LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(10) }
        }.also { card.addView(it) }
        label(13f, "#55DDBA", true).text = "DEMO APPS UNLOCKED"
        label(13f, "#AAB7C5").text = "Temporarily simulate sufficient points to test entertainment-app unlocking. Available once every 7 days for 30 seconds."
        val balance = label(17f, "#F0F4F8", true)
        val countdown = label(22f, "#55DDBA", true)
        val dates = label(12f, "#AAB7C5")
        val status = label(12f, "#F7C676")
        val activate = Button(context).apply {
            text = "ACTIVATE DEMO UNLOCK"; isAllCaps = false
            setTextColor(Color.rgb(9, 24, 28)); textSize = 14f; setTypeface(typeface, Typeface.BOLD)
            minHeight = dp(50)
            background = GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT, intArrayOf(Color.rgb(62, 222, 172), Color.rgb(50, 181, 206))).apply { cornerRadius = dp(14).toFloat() }
            layoutParams = LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(6); bottomMargin = dp(6) }
        }
        card.addView(activate)
        val refresh = Button(context).apply { text = "Refresh availability"; isAllCaps = false; textSize = 12f }
        card.addView(refresh, LinearLayout.LayoutParams(-1, -2))
        fun date(ms: Long) = if (ms <= 0) "Not used" else DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(ms))
        var previousActive = false
        fun update() {
            val state = DemoAccessManager.view(context)
            val active = state.optBoolean("active")
            balance.text = "Actual Points: ${actualPoints()}"
            countdown.text = if (active) "DEMO APPS UNLOCKED — 00:${((state.optLong("remaining_ms") + 999) / 1000).toString().padStart(2, '0')}\nTemporary Demo Access: 81" else "Temporary Demo Access: inactive"
            dates.text = "Last activation: ${date(state.optLong("last_successful_activation_ms"))}\nNext eligible activation: ${if (state.optLong("next_eligible_ms") > 0) date(state.optLong("next_eligible_ms")) else "Verify with service"}"
            if (!state.isNull("cooldown_remaining_ms") && state.optLong("cooldown_remaining_ms") > 0) {
                val seconds = state.optLong("cooldown_remaining_ms") / 1000
                dates.append("\nCooldown: ${seconds / 86400}d ${(seconds / 3600) % 24}h ${(seconds / 60) % 60}m")
            }
            status.text = state.optString("message")
            activate.isEnabled = state.optBoolean("available") && !active
            activate.alpha = if (activate.isEnabled) 1f else .45f
            refresh.isEnabled = state.optBoolean("configured") && !state.optBoolean("busy")
            if (previousActive && !active) Toast.makeText(context, "Demo access expired. Your original points balance and restrictions have been restored.", Toast.LENGTH_LONG).show()
            previousActive = active
        }
        activate.setOnClickListener {
            AlertDialog.Builder(context).setTitle("Demo Apps Unlocked")
                .setMessage("Temporarily set your effective entertainment-access score to 81 for 30 seconds? Your real points and history will remain unchanged.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Activate") { _, _ ->
                    activate.isEnabled = false
                    DemoAccessManager.activate(context) { update() }
                }.show()
        }
        refresh.setOnClickListener { refresh.isEnabled = false; DemoAccessManager.refresh(context) { update() } }
        val tick = object : Runnable {
            override fun run() { if (card.isAttachedToWindow) { update(); handler.postDelayed(this, 1000) } }
        }
        card.addOnAttachStateChangeListener(object : View.OnAttachStateChangeListener {
            override fun onViewAttachedToWindow(v: View) { update(); DemoAccessManager.refresh(context) { if (card.isAttachedToWindow) update() }; handler.post(tick) }
            override fun onViewDetachedFromWindow(v: View) { handler.removeCallbacks(tick) }
        })
        update()
        return card
    }
}
