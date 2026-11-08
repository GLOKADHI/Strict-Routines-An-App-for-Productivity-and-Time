package com.strictrecords.app

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import java.util.Calendar
import java.util.TimeZone

/**
 * Settings Card UI for StrictRecords Schedule-Aware Battery Optimization & Power Manager.
 *
 * Displays:
 * - Current Active Profile badge
 * - Scheduled period & Next transition boundary
 * - Subsystem state grid (Camera, Microphone, GPS, Background Polling, Nonurgent Sync, Critical Tasks)
 * - Next scheduled routine activity
 */
object StrictRecordsPowerSettingsCard {

    private const val CLR_BG = "#0f172a"
    private const val CLR_BORDER = "#1e293b"
    private const val CLR_TEXT_TITLE = "#f8fafc"
    private const val CLR_TEXT_SUB = "#94a3b8"
    private const val CLR_CYAN = "#38bdf8"
    private const val CLR_GREEN = "#22c55e"
    private const val CLR_AMBER = "#f59e0b"
    private const val CLR_INDIGO = "#6366f1"

    fun create(context: Context, onRefresh: () -> Unit = {}): View {
        fun dp(v: Int): Int = (v * context.resources.displayMetrics.density).toInt()

        val card = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(16))
            background = GradientDrawable().apply {
                setColor(Color.parseColor(CLR_BG))
                cornerRadius = dp(18).toFloat()
                setStroke(dp(1), Color.parseColor(CLR_BORDER))
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(12) }
        }

        // Header Row: Title + Profile Badge
        val headerRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(10)
            }
        }

        val titleCol = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        titleCol.addView(TextView(context).apply {
            text = "⚡ StrictRecords Power Manager"
            textSize = 14.5f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.parseColor(CLR_TEXT_TITLE))
        })
        titleCol.addView(TextView(context).apply {
            text = "Schedule-Aware Battery Optimization"
            textSize = 11f
            setTextColor(Color.parseColor(CLR_TEXT_SUB))
        })
        headerRow.addView(titleCol)

        val profile = StrictRecordsPowerManager.getCurrentProfile(context)
        val isSuspended = StrictRecordsPowerManager.isSuspended(context)
        val cfg = StrictRecordsPowerManager.getCurrentConfig(context)

        val (badgeLabel, badgeBg, badgeTextColor) = when (profile) {
            StrictRecordsPowerManager.PowerProfile.NIGHT_QUIET_MODE ->
                Triple("NIGHT QUIET (01:00-07:00)", "#1e1b4b", "#a5b4fc")
            StrictRecordsPowerManager.PowerProfile.STRICT_SESSION_LOW_POWER ->
                Triple("STRICT SESSION", "#450a0a", "#fca5a5")
            StrictRecordsPowerManager.PowerProfile.TRAVEL_LOW_POWER ->
                Triple("TRAVEL LOW POWER", "#431407", "#fdba74")
            StrictRecordsPowerManager.PowerProfile.MOVIE_LOW_POWER ->
                Triple("MOVIE LOW POWER", "#3b0764", "#d8b4fe")
            StrictRecordsPowerManager.PowerProfile.BATH_LOW_POWER ->
                Triple("BATH LOW POWER", "#082f49", "#7dd3fc")
            StrictRecordsPowerManager.PowerProfile.CUSTOM_ACTIVITY_LOW_POWER ->
                Triple("ACTIVITY LOW POWER", "#14532d", "#86efac")
            StrictRecordsPowerManager.PowerProfile.RESUMING_NORMAL_MODE ->
                Triple("RESUMING NORMAL", "#172554", "#93c5fd")
            StrictRecordsPowerManager.PowerProfile.NORMAL_MODE ->
                Triple("NORMAL MODE", "#064e3b", "#6ee7b7")
        }

        val badge = TextView(context).apply {
            text = badgeLabel
            textSize = 10f
            setTypeface(typeface, Typeface.BOLD)
            setPadding(dp(8), dp(4), dp(8), dp(4))
            setTextColor(Color.parseColor(badgeTextColor))
            background = GradientDrawable().apply {
                setColor(Color.parseColor(badgeBg))
                cornerRadius = dp(8).toFloat()
            }
        }
        headerRow.addView(badge)
        card.addView(headerRow)

        // Status Summary line
        val stateName = if (isSuspended) "SUSPENDED (SCREEN OFF / LOCKED)" else "ACTIVE (FOREGROUND / INTERACTIVE)"
        val summaryLine = TextView(context).apply {
            text = "Status: $stateName • Timezone: Asia/Kolkata"
            textSize = 11f
            setTextColor(Color.parseColor(if (isSuspended) CLR_AMBER else CLR_GREEN))
            setPadding(0, 0, 0, dp(10))
        }
        card.addView(summaryLine)

        // Subsystem configuration grid
        val grid = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#090d16"))
                cornerRadius = dp(10).toFloat()
                setStroke(dp(1), Color.parseColor("#172033"))
            }
            setPadding(dp(12), dp(10), dp(12), dp(10))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(10)
            }
        }

        fun addRow(label: String, value: String, isGreen: Boolean) {
            val r = LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    bottomMargin = dp(4)
                }
            }
            r.addView(TextView(context).apply {
                text = label
                textSize = 11.5f
                setTextColor(Color.parseColor(CLR_TEXT_SUB))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            })
            r.addView(TextView(context).apply {
                text = value
                textSize = 11.5f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(Color.parseColor(if (isGreen) CLR_GREEN else CLR_AMBER))
            })
            grid.addView(r)
        }

        addRow("Camera:", if (cfg.cameraAllowed) "ALLOWED" else "OFF (SUSPENDED)", cfg.cameraAllowed)
        addRow("Microphone:", "NOT USED (BACKGROUND)", true)
        addRow("GPS / Location:", if (cfg.gpsMode == "OFF") "OFF" else cfg.gpsMode, cfg.gpsMode != "OFF")
        addRow("Background Polling:", if (cfg.backgroundPollingAllowed) "NORMAL" else "SUSPENDED", cfg.backgroundPollingAllowed)
        addRow("Nonurgent Sync:", if (cfg.nonurgentSyncDeferred) "DEFERRED (BATCHED)" else "ACTIVE", !cfg.nonurgentSyncDeferred)
        addRow("Critical Tasks:", "PRESERVED", true)

        card.addView(grid)

        // Next Scheduled Transition row
        val cal = Calendar.getInstance(TimeZone.getTimeZone("Asia/Kolkata"))
        val curH = cal.get(Calendar.HOUR_OF_DAY)
        val nextBoundary = if (curH in 1..6) "07:00 AM IST (Exit Night Quiet Mode)" else "01:00 AM IST (Enter Night Quiet Mode)"

        val footer = TextView(context).apply {
            text = "Next Scheduled Boundary: $nextBoundary\nOverlapping schedules strictly follow safety-first priority order."
            textSize = 10.5f
            setTextColor(Color.parseColor("#64748b"))
            setLineSpacing(0f, 1.25f)
        }
        card.addView(footer)

        return card
    }
}
