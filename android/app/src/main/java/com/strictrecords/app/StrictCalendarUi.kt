package com.strictrecords.app

import android.app.Activity
import android.app.Dialog
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.util.Locale

/**
 * Interactive Monthly Calendar and Date Details UI for StrictRecords.
 * Integrates alternate-day Gym Routine, daily scheduled activities,
 * verification statuses, and schedule confirmation.
 */
object StrictCalendarUi {

    private const val BG = "#0C1420"
    private const val SURFACE = "#142231"
    private const val ELEVATED = "#1A2C3D"
    private const val EDGE = "#244154"
    private const val CYAN = "#50D7EF"
    private const val GREEN = "#45E8AE"
    private const val TEXT = "#EDF5FB"
    private const val MUTED = "#A8BBCB"
    private const val WARNING = "#F4C46A"
    private const val RED = "#FF8C91"

    private class BoundedScroll(context: Context) : ScrollView(context) {
        override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
            val cap = (resources.displayMetrics.heightPixels * 0.90f).toInt()
            val available = if (View.MeasureSpec.getMode(heightMeasureSpec) == View.MeasureSpec.UNSPECIFIED) cap
            else minOf(cap, View.MeasureSpec.getSize(heightMeasureSpec))
            super.onMeasure(widthMeasureSpec, View.MeasureSpec.makeMeasureSpec(available, View.MeasureSpec.AT_MOST))
        }
    }

    private fun dp(activity: Activity, value: Int) = (activity.resources.displayMetrics.density * value).toInt()
    private fun color(value: String) = Color.parseColor(value)

    private fun bg(
        activity: Activity,
        fill: String = SURFACE,
        stroke: String = EDGE,
        radius: Int = 16,
        strokeWidthDp: Int = 1
    ) = GradientDrawable().apply {
        setColor(color(fill))
        cornerRadius = dp(activity, radius).toFloat()
        if (strokeWidthDp > 0) {
            setStroke(dp(activity, strokeWidthDp), color(stroke))
        }
    }

    /**
     * Entry card displayed on the Home dashboard for 1-tap calendar access.
     */
    fun createHomeCalendarCard(activity: Activity, db: StrictRecordsLocalDb, onOpenCalendar: () -> Unit): View {
        val todayStr = StrictCalendarDataRepository.today(db.authoritativeNowMs())
        val todayDetails = StrictCalendarDataRepository.loadDateDetails(db, todayStr)

        val card = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            val pad = dp(activity, 14)
            setPadding(pad, pad, pad, pad)
            background = bg(activity, SURFACE, EDGE, 18, 1)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(activity, 12) }
        }

        // Header Row: Icon + Title + Open Button
        val topRow = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(activity, 8) }
        }

        val titleCol = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }

        titleCol.addView(TextView(activity).apply {
            text = "📅 INTERACTIVE CALENDAR"
            textSize = 14f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(TEXT))
            letterSpacing = 0.04f
        })

        titleCol.addView(TextView(activity).apply {
            text = "Month view • Gym routine • Daily schedules & verifications"
            textSize = 11.5f
            setTextColor(color(MUTED))
            setPadding(0, dp(activity, 2), 0, 0)
        })

        topRow.addView(titleCol)

        val openBtn = Button(activity).apply {
            text = "Open Calendar →"
            textSize = 12f
            isAllCaps = false
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(CYAN))
            background = bg(activity, BG, CYAN, 12, 1)
            minimumHeight = dp(activity, 38)
            setPadding(dp(activity, 12), dp(activity, 6), dp(activity, 12), dp(activity, 6))
            setOnClickListener { onOpenCalendar() }
        }
        topRow.addView(openBtn)
        card.addView(topRow)

        // Today Status Summary Row
        val summaryPill = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(activity, 10), dp(activity, 6), dp(activity, 10), dp(activity, 6))
            background = bg(activity, ELEVATED, EDGE, 10, 1)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }

        val gymBadge = when (todayDetails.gymState) {
            "GYM_DAY" -> "💪 Today: GYM Day"
            "REST_DAY" -> "🛋️ Today: REST Day"
            "PAUSED" -> "⏸️ Routine Paused"
            else -> "📅 Today: Active"
        }
        val gymBadgeColor = when (todayDetails.gymState) {
            "GYM_DAY" -> CYAN
            "PAUSED" -> WARNING
            else -> MUTED
        }

        summaryPill.addView(TextView(activity).apply {
            text = gymBadge
            textSize = 12f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(gymBadgeColor))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })

        val actCountText = if (todayDetails.activities.isNotEmpty()) {
            "${todayDetails.activities.size} Activities"
        } else {
            "No Scheduled Activities"
        }

        summaryPill.addView(TextView(activity).apply {
            text = actCountText
            textSize = 11.5f
            setTextColor(color(MUTED))
        })

        card.addView(summaryPill)
        card.setOnClickListener { onOpenCalendar() }
        return card
    }

    /**
     * Opens the interactive monthly calendar dialog.
     */
    fun showCalendar(
        activity: Activity,
        db: StrictRecordsLocalDb,
        initialDate: String? = null,
        onOpenDeviceCenter: (() -> Unit)? = null
    ) {
        val todayStr = StrictCalendarDataRepository.today(db.authoritativeNowMs())
        var currentMonth = initialDate?.take(7) ?: todayStr.take(7)
        var selectedDate = initialDate ?: todayStr
        var scoreEventsExpanded = false

        fun triggerOpenDeviceCenter() {
            onOpenDeviceCenter?.invoke() ?: run {
                try {
                    val method = activity.javaClass.getDeclaredMethod("showDeviceCenterDialog")
                    method.isAccessible = true
                    method.invoke(activity)
                } catch (_: Exception) {
                    android.widget.Toast.makeText(activity, "Open Settings → Device Center & PC Pairing", android.widget.Toast.LENGTH_SHORT).show()
                }
            }
        }

        val dialog = Dialog(activity)
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE)

        val rootLayout = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            val pad = dp(activity, 16)
            setPadding(pad, pad, pad, pad)
            background = bg(activity, BG, EDGE, 22, 1)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }

        val scroll = BoundedScroll(activity).apply {
            isFillViewport = false
            addView(rootLayout)
        }
        dialog.setContentView(scroll)

        dialog.window?.apply {
            setBackgroundDrawable(bg(activity, BG, EDGE, 22, 1))
            setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
            clearFlags(WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS)
            decorView.setOnApplyWindowInsetsListener { _, insets ->
                scroll.setPadding(0, 0, 0, insets.systemWindowInsetBottom.coerceAtLeast(0))
                insets
            }
        }

        fun redraw() {
            rootLayout.removeAllViews()

            // 1. Top Bar: Title + Close Button
            val topBar = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { bottomMargin = dp(activity, 12) }
            }

            val titleCol = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            }

            titleCol.addView(TextView(activity).apply {
                text = "CALENDAR & SCHEDULES"
                textSize = 16f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(TEXT))
                letterSpacing = 0.05f
            })

            titleCol.addView(TextView(activity).apply {
                text = "Asia/Kolkata • Daily Schedules, Gym Routine & Verifications"
                textSize = 11.5f
                setTextColor(color(MUTED))
            })

            topBar.addView(titleCol)

            val closeBtn = TextView(activity).apply {
                text = "✕"
                textSize = 18f
                gravity = Gravity.CENTER
                setTextColor(color(MUTED))
                setPadding(dp(activity, 10), dp(activity, 6), dp(activity, 10), dp(activity, 6))
                background = bg(activity, SURFACE, EDGE, 99, 1)
                isClickable = true
                isFocusable = true
                setOnClickListener { dialog.dismiss() }
            }
            topBar.addView(closeBtn)
            rootLayout.addView(topBar)

            // 2. Month Navigation Bar: [ ‹ ] Month Year [ › ] + Today & Refresh
            val navCard = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(activity, 12), dp(activity, 10), dp(activity, 12), dp(activity, 10))
                background = bg(activity, SURFACE, EDGE, 14, 1)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { bottomMargin = dp(activity, 10) }
            }

            val monthRow = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )
            }

            val prevBtn = Button(activity).apply {
                text = "‹"
                textSize = 20f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(CYAN))
                background = bg(activity, BG, EDGE, 10, 1)
                minimumHeight = dp(activity, 44)
                layoutParams = LinearLayout.LayoutParams(dp(activity, 48), dp(activity, 44))
                setOnClickListener {
                    currentMonth = StrictCalendarDataRepository.addMonths(currentMonth, -1)
                    selectedDate = "$currentMonth-01"
                    redraw()
                }
            }
            monthRow.addView(prevBtn)

            val monthLabel = TextView(activity).apply {
                text = StrictCalendarDataRepository.formatMonthDisplay(currentMonth)
                textSize = 17f
                setTypeface(typeface, Typeface.BOLD)
                gravity = Gravity.CENTER
                setTextColor(color(TEXT))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            }
            monthRow.addView(monthLabel)

            val nextBtn = Button(activity).apply {
                text = "›"
                textSize = 20f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(CYAN))
                background = bg(activity, BG, EDGE, 10, 1)
                minimumHeight = dp(activity, 44)
                layoutParams = LinearLayout.LayoutParams(dp(activity, 48), dp(activity, 44))
                setOnClickListener {
                    currentMonth = StrictCalendarDataRepository.addMonths(currentMonth, 1)
                    selectedDate = "$currentMonth-01"
                    redraw()
                }
            }
            monthRow.addView(nextBtn)
            navCard.addView(monthRow)

            // Shortcuts Row: [Today] [Refresh]
            val shortcutRow = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER
                setPadding(0, dp(activity, 8), 0, 0)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )
            }

            val todayBtn = Button(activity).apply {
                text = "● Today (${StrictCalendarDataRepository.formatShortDateDisplay(todayStr)})"
                textSize = 12f
                isAllCaps = false
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(GREEN))
                background = bg(activity, BG, GREEN, 8, 1)
                minimumHeight = dp(activity, 36)
                setPadding(dp(activity, 14), 0, dp(activity, 14), 0)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    dp(activity, 36)
                ).apply { rightMargin = dp(activity, 8) }
                setOnClickListener {
                    currentMonth = todayStr.take(7)
                    selectedDate = todayStr
                    redraw()
                }
            }
            shortcutRow.addView(todayBtn)

            val refreshBtn = Button(activity).apply {
                text = "🔄 Refresh"
                textSize = 12f
                isAllCaps = false
                setTextColor(color(CYAN))
                background = bg(activity, BG, EDGE, 8, 1)
                minimumHeight = dp(activity, 36)
                setPadding(dp(activity, 14), 0, dp(activity, 14), 0)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    dp(activity, 36)
                )
                setOnClickListener {
                    redraw()
                }
            }
            shortcutRow.addView(refreshBtn)
            navCard.addView(shortcutRow)
            rootLayout.addView(navCard)

            // 3. Weekday Header Row: SUN MON TUE WED THU FRI SAT
            val weekdaysRow = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                setPadding(dp(activity, 4), dp(activity, 6), dp(activity, 4), dp(activity, 6))
                background = bg(activity, SURFACE, EDGE, 8, 1)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { bottomMargin = dp(activity, 4) }
            }

            val weekdayNames = listOf("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
            weekdayNames.forEach { name ->
                weekdaysRow.addView(TextView(activity).apply {
                    text = name
                    textSize = 11f
                    setTypeface(typeface, Typeface.BOLD)
                    gravity = Gravity.CENTER
                    setTextColor(color(MUTED))
                    layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                })
            }
            rootLayout.addView(weekdaysRow)

            // 4. Monthly Calendar Grid (Mon–Sun alignment)
            val monthDays = StrictCalendarDataRepository.loadMonthDays(db, currentMonth, todayStr)
            val blanks = if (monthDays.isNotEmpty()) (monthDays.first().weekday + 6) % 7 else 0
            val totalCells = ((monthDays.size + blanks + 6) / 7) * 7

            val gridCard = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(activity, 2), dp(activity, 2), dp(activity, 2), dp(activity, 2))
                background = bg(activity, BG, EDGE, 12, 1)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { bottomMargin = dp(activity, 10) }
            }

            for (week in 0 until (totalCells / 7)) {
                val row = LinearLayout(activity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    layoutParams = LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT
                    )
                }

                for (dayCol in 0..6) {
                    val index = week * 7 + dayCol - blanks
                    if (index !in monthDays.indices) {
                        row.addView(View(activity).apply {
                            layoutParams = LinearLayout.LayoutParams(0, dp(activity, 52), 1f)
                        })
                    } else {
                        val dayItem = monthDays[index]
                        val isSelected = (dayItem.date == selectedDate)
                        val isToday = dayItem.isToday

                        val cell = LinearLayout(activity).apply {
                            orientation = LinearLayout.VERTICAL
                            gravity = Gravity.CENTER
                            setPadding(dp(activity, 2), dp(activity, 3), dp(activity, 2), dp(activity, 3))
                            minimumHeight = dp(activity, 52)
                            isClickable = true
                            isFocusable = true

                            val cellBg = when {
                                isSelected && isToday -> "#16393F"
                                isSelected -> "#1C3B4D"
                                isToday -> "#0D2B22"
                                else -> SURFACE
                            }
                            val cellBorder = when {
                                isSelected -> CYAN
                                isToday -> GREEN
                                else -> EDGE
                            }
                            val borderWidth = if (isSelected || isToday) 2 else 1
                            background = bg(activity, cellBg, cellBorder, 8, borderWidth)

                            layoutParams = LinearLayout.LayoutParams(0, dp(activity, 52), 1f).apply {
                                setMargins(dp(activity, 1), dp(activity, 2), dp(activity, 1), dp(activity, 2))
                            }

                            setOnClickListener {
                                selectedDate = dayItem.date
                                redraw()
                            }
                        }

                        // Day Number row (with Today indicator tag if both selected and today)
                        val numRow = LinearLayout(activity).apply {
                            orientation = LinearLayout.HORIZONTAL
                            gravity = Gravity.CENTER
                        }

                        val dayNumText = TextView(activity).apply {
                            text = dayItem.dayOfMonth.toString()
                            textSize = 12f
                            setTypeface(typeface, if (isSelected || isToday) Typeface.BOLD else Typeface.NORMAL)
                            setTextColor(color(if (isSelected) CYAN else if (isToday) GREEN else TEXT))
                            gravity = Gravity.CENTER
                        }
                        numRow.addView(dayNumText)

                        if (isToday) {
                            numRow.addView(View(activity).apply {
                                background = bg(activity, GREEN, GREEN, 99, 0)
                                layoutParams = LinearLayout.LayoutParams(dp(activity, 4), dp(activity, 4)).apply {
                                    leftMargin = dp(activity, 2)
                                }
                            })
                        }
                        cell.addView(numRow)

                        // Gym / Session Status Badge
                        val gymStatusText = when {
                            dayItem.gymSessionStatus == "VERIFIED" -> "✓ VERIFIED"
                            dayItem.gymSessionStatus == "PENDING" -> "⏳ PEND"
                            dayItem.gymSessionStatus == "MISSED" -> "✕ MISSED"
                            dayItem.gymState == "GYM_DAY" -> "GYM"
                            dayItem.gymState == "REST_DAY" -> "REST"
                            dayItem.gymState == "PAUSED" -> "PAUSE"
                            else -> "·"
                        }
                        val gymStatusColor = when {
                            dayItem.gymSessionStatus == "VERIFIED" -> GREEN
                            dayItem.gymSessionStatus == "PENDING" -> WARNING
                            dayItem.gymSessionStatus == "MISSED" -> RED
                            dayItem.gymState == "GYM_DAY" -> CYAN
                            dayItem.gymState == "PAUSED" -> WARNING
                            else -> MUTED
                        }

                        val statusLabel = TextView(activity).apply {
                            text = gymStatusText
                            textSize = 8.5f
                            setTypeface(typeface, Typeface.BOLD)
                            setTextColor(color(gymStatusColor))
                            gravity = Gravity.CENTER
                            maxLines = 1
                        }
                        cell.addView(statusLabel)

                        // Activity Indicators Row (small dots)
                        if (dayItem.totalActivities > 0) {
                            val dotsRow = LinearLayout(activity).apply {
                                orientation = LinearLayout.HORIZONTAL
                                gravity = Gravity.CENTER
                                layoutParams = LinearLayout.LayoutParams(
                                    ViewGroup.LayoutParams.WRAP_CONTENT,
                                    ViewGroup.LayoutParams.WRAP_CONTENT
                                ).apply { topMargin = dp(activity, 2) }
                            }

                            if (dayItem.verifiedActivities > 0) {
                                dotsRow.addView(createDot(activity, GREEN))
                            }
                            if (dayItem.pendingActivities > 0) {
                                dotsRow.addView(createDot(activity, WARNING))
                            }
                            if (dayItem.missedActivities > 0) {
                                dotsRow.addView(createDot(activity, RED))
                            }
                            if (dayItem.verifiedActivities == 0 && dayItem.pendingActivities == 0 && dayItem.missedActivities == 0) {
                                dotsRow.addView(createDot(activity, CYAN))
                            }
                            cell.addView(dotsRow)
                        }

                        row.addView(cell)
                    }
                }
                gridCard.addView(row)
            }
            rootLayout.addView(gridCard)

            // 5. Calendar Indicators Legend (Structured responsive chips)
            val legendCard = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(activity, 12), dp(activity, 10), dp(activity, 12), dp(activity, 10))
                background = bg(activity, SURFACE, EDGE, 12, 1)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { bottomMargin = dp(activity, 12) }
            }

            legendCard.addView(TextView(activity).apply {
                text = "CALENDAR INDICATORS LEGEND"
                textSize = 10f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(MUTED))
                letterSpacing = 0.05f
                setPadding(0, 0, 0, dp(activity, 6))
            })

            // Legend Row 1: Gym Routine Pattern
            val legRow1 = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            }
            legRow1.addView(legendChip(activity, "● GYM", CYAN, "Planned Gym"))
            legRow1.addView(legendChip(activity, "● REST", MUTED, "Rest Day"))
            legRow1.addView(legendChip(activity, "● PAUSE", WARNING, "Routine Paused"))
            legendCard.addView(legRow1)

            // Legend Row 2: Verification State
            val legRow2 = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    topMargin = dp(activity, 4)
                }
            }
            legRow2.addView(legendChip(activity, "✓ VERIFIED", GREEN, "Verified Workout"))
            legRow2.addView(legendChip(activity, "⏳ PENDING", WARNING, "Pending Review"))
            legRow2.addView(legendChip(activity, "✕ MISSED", RED, "Missed Session"))
            legendCard.addView(legRow2)

            // Legend Row 3: Calendar Day Indicators
            val legRow3 = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    topMargin = dp(activity, 4)
                }
            }
            legRow3.addView(legendChip(activity, "● TODAY", GREEN, "Current Date"))
            legRow3.addView(legendChip(activity, "● SELECTED", CYAN, "Selected Date"))
            legRow3.addView(legendChip(activity, "••• TASKS", CYAN, "Daily Tasks"))
            legendCard.addView(legRow3)

            rootLayout.addView(legendCard)

            // 6. Selected Date Details Section
            val dateDetails = StrictCalendarDataRepository.loadDateDetails(db, selectedDate, todayStr)
            renderDateDetailsSection(
                activity = activity,
                parent = rootLayout,
                details = dateDetails,
                scoreEventsExpanded = scoreEventsExpanded,
                onToggleScoreEvents = {
                    scoreEventsExpanded = !scoreEventsExpanded
                    redraw()
                },
                onOpenDeviceCenter = {
                    dialog.dismiss()
                    triggerOpenDeviceCenter()
                }
            )

            // 7. Close Button
            val bottomCloseBtn = Button(activity).apply {
                text = "Close Calendar"
                textSize = 14f
                setTextColor(color(MUTED))
                background = bg(activity, BG, EDGE, 12, 1)
                minimumHeight = dp(activity, 48)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    dp(activity, 48)
                ).apply { topMargin = dp(activity, 12) }
                setOnClickListener { dialog.dismiss() }
            }
            rootLayout.addView(bottomCloseBtn)
        }

        redraw()
        dialog.show()
        dialog.window?.setLayout(
            minOf(activity.resources.displayMetrics.widthPixels - dp(activity, 24), dp(activity, 720)),
            ViewGroup.LayoutParams.WRAP_CONTENT
        )
    }

    private fun createDot(activity: Activity, hexColor: String): View {
        return View(activity).apply {
            background = bg(activity, hexColor, hexColor, 99, 0)
            layoutParams = LinearLayout.LayoutParams(dp(activity, 4), dp(activity, 4)).apply {
                setMargins(dp(activity, 1), 0, dp(activity, 1), 0)
            }
        }
    }

    private fun legendChip(activity: Activity, symbol: String, hexColor: String, label: String): View {
        return LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(activity, 6), dp(activity, 4), dp(activity, 6), dp(activity, 4))
            background = bg(activity, BG, EDGE, 8, 1)
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                setMargins(dp(activity, 2), 0, dp(activity, 2), 0)
            }
            addView(TextView(activity).apply {
                text = symbol
                textSize = 9.5f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(hexColor))
                setPadding(0, 0, dp(activity, 3), 0)
            })
            addView(TextView(activity).apply {
                text = label
                textSize = 9f
                setTextColor(color(MUTED))
                maxLines = 1
            })
        }
    }

    private fun renderDateDetailsSection(
        activity: Activity,
        parent: LinearLayout,
        details: StrictCalendarDataRepository.DateDetails,
        scoreEventsExpanded: Boolean,
        onToggleScoreEvents: () -> Unit,
        onOpenDeviceCenter: () -> Unit
    ) {
        val detailsCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(activity, 14), dp(activity, 12), dp(activity, 14), dp(activity, 12))
            background = bg(activity, SURFACE, EDGE, 16, 1)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }

        // Header: Date & Day Badge (NO ambiguous generic "CONFIRMED" badge here!)
        val headerRow = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(activity, 10) }
        }

        val dateCol = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }

        dateCol.addView(TextView(activity).apply {
            text = details.formattedDate
            textSize = 15.5f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(TEXT))
        })

        val dateSubtitle = if (details.isToday) {
            "● TODAY (CURRENT LOCAL DATE)"
        } else {
            "DATE DETAILS • Asia/Kolkata"
        }
        val dateSubColor = if (details.isToday) GREEN else MUTED
        dateCol.addView(TextView(activity).apply {
            text = dateSubtitle
            textSize = 11f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(dateSubColor))
            setPadding(0, dp(activity, 2), 0, 0)
        })
        headerRow.addView(dateCol)
        val collegeBadgeText = when (details.collegeAttendance) {
            "YES" -> "🎓 COLLEGE DAY"
            "NO" -> "🏠 NON-COLLEGE"
            else -> "○ ATTENDANCE PENDING"
        }
        val collegeBadgeColor = when (details.collegeAttendance) {
            "YES" -> CYAN
            "NO" -> GREEN
            else -> MUTED
        }
        headerRow.addView(TextView(activity).apply {
            text = collegeBadgeText
            textSize = 10f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(collegeBadgeColor))
            setPadding(dp(activity, 8), dp(activity, 4), dp(activity, 8), dp(activity, 4))
            background = bg(activity, BG, collegeBadgeColor, 99, 1)
        })
        detailsCard.addView(headerRow)

        // ─────────────────────────────────────────────────────────────────────────
        // 1. Wake-Up Verification Card
        // ─────────────────────────────────────────────────────────────────────────
        val wakeCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(activity, 12), dp(activity, 10), dp(activity, 12), dp(activity, 10))
            background = bg(activity, ELEVATED, EDGE, 12, 1)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(activity, 8) }
        }

        val wakeHeader = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        wakeHeader.addView(TextView(activity).apply {
            text = "🌅 WAKE-UP VERIFICATION"
            textSize = 12f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(CYAN))
            letterSpacing = 0.04f
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })

        val wakeStatusText = when (details.wakeUpStatus) {
            "VERIFIED" -> "✓ VERIFIED"
            "REQUIRED" -> "⏳ REQUIRED"
            "MISSED" -> "✕ MISSED"
            else -> "○ UPCOMING"
        }
        val wakeStatusColor = when (details.wakeUpStatus) {
            "VERIFIED" -> GREEN
            "REQUIRED" -> WARNING
            "MISSED" -> RED
            else -> MUTED
        }
        wakeHeader.addView(TextView(activity).apply {
            text = wakeStatusText
            textSize = 10f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(wakeStatusColor))
            setPadding(dp(activity, 8), dp(activity, 3), dp(activity, 8), dp(activity, 3))
            background = bg(activity, BG, wakeStatusColor, 99, 1)
        })
        wakeCard.addView(wakeHeader)

        val wakeSubMsg = when (details.wakeUpStatus) {
            "VERIFIED" -> "Camera face verification verified ✓${if (details.wakeUpCompletedAt != null) " (${StrictCalendarDataRepository.formatTime12h(details.wakeUpCompletedAt)})" else ""}"
            "REQUIRED" -> "First morning unlock verification pending. Complete camera liveness check."
            "MISSED" -> "Wake-up verification window closed for this calendar date."
            else -> "Scheduled morning wake-up protocol (Camera liveness verification)."
        }
        wakeCard.addView(TextView(activity).apply {
            text = wakeSubMsg
            textSize = 10.5f
            setTextColor(color(MUTED))
            setPadding(0, dp(activity, 3), 0, 0)
        })
        detailsCard.addView(wakeCard)

        // ─────────────────────────────────────────────────────────────────────────
        // 2. Daily Bath Protocol Schedule Card
        // ─────────────────────────────────────────────────────────────────────────
        val bathCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(activity, 12), dp(activity, 10), dp(activity, 12), dp(activity, 10))
            background = bg(activity, ELEVATED, EDGE, 12, 1)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(activity, 8) }
        }

        val bathHeader = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        bathHeader.addView(TextView(activity).apply {
            text = "🚿 BATH PROTOCOL (${details.bathSessionCount} SESSION${if (details.bathSessionCount > 1) "S" else ""})"
            textSize = 12f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(CYAN))
            letterSpacing = 0.04f
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })

        val bathStatusText = if (details.isBathPlanConfirmed) "✓ CONFIRMED" else "○ UNCONFIRMED"
        val bathStatusColor = if (details.isBathPlanConfirmed) GREEN else WARNING
        bathHeader.addView(TextView(activity).apply {
            text = bathStatusText
            textSize = 10f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(bathStatusColor))
            setPadding(dp(activity, 8), dp(activity, 3), dp(activity, 8), dp(activity, 3))
            background = bg(activity, BG, bathStatusColor, 99, 1)
        })
        bathCard.addView(bathHeader)

        // Bath Session 1 Info
        val b1Row = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(activity, 8), dp(activity, 6), dp(activity, 8), dp(activity, 6))
            background = bg(activity, BG, EDGE, 8, 1)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(activity, 6)
            }
        }
        b1Row.addView(TextView(activity).apply {
            text = "Bath Session #1: ${details.bath1Status}"
            textSize = 11f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(if (details.bath1Status in listOf("VERIFIED", "APPROVED", "COMPLETED")) GREEN else TEXT))
        })
        b1Row.addView(TextView(activity).apply {
            text = "• Activity Window: ${details.bath1WindowDisplay}\n• Proof Submission Window: ${details.bath1ProofWindowDisplay}"
            textSize = 10f
            setTextColor(color(MUTED))
            setPadding(0, dp(activity, 2), 0, 0)
        })
        bathCard.addView(b1Row)

        // Bath Session 2 Info (if configured)
        if (details.bathSessionCount >= 2 && details.bath2WindowDisplay != null) {
            val b2Row = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(activity, 8), dp(activity, 6), dp(activity, 8), dp(activity, 6))
                background = bg(activity, BG, EDGE, 8, 1)
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    topMargin = dp(activity, 4)
                }
            }
            b2Row.addView(TextView(activity).apply {
                text = "Bath Session #2: ${details.bath2Status ?: "PLANNED"}"
                textSize = 11f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(if (details.bath2Status in listOf("VERIFIED", "APPROVED", "COMPLETED")) GREEN else TEXT))
            })
            b2Row.addView(TextView(activity).apply {
                text = "• Activity Window: ${details.bath2WindowDisplay}\n• Proof Submission Window: ${details.bath2ProofWindowDisplay ?: "—"}"
                textSize = 10f
                setTextColor(color(MUTED))
                setPadding(0, dp(activity, 2), 0, 0)
            })
            bathCard.addView(b2Row)
        }
        detailsCard.addView(bathCard)

        // ─────────────────────────────────────────────────────────────────────────
        // 3. Alternate-Day Gym Routine & GYM Mode Card
        // ─────────────────────────────────────────────────────────────────────────
        val gymCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(activity, 12), dp(activity, 10), dp(activity, 12), dp(activity, 10))
            background = bg(activity, ELEVATED, EDGE, 12, 1)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(activity, 10) }
        }

        val gymRow = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        gymRow.addView(TextView(activity).apply {
            text = "💪 ALTERNATE-DAY GYM ROUTINE"
            textSize = 12f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(CYAN))
            letterSpacing = 0.04f
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })

        val gymTitle = when (details.gymState) {
            "GYM_DAY" -> "💪 GYM DAY"
            "REST_DAY" -> "🛋️ REST DAY"
            "PAUSED" -> "⏸️ PAUSED"
            "SETUP_REQUIRED" -> "⚙️ SETUP REQUIRED"
            else -> "📅 NOT TRACKED"
        }
        val gymTitleColor = when (details.gymState) {
            "GYM_DAY" -> CYAN
            "PAUSED" -> WARNING
            "SETUP_REQUIRED" -> WARNING
            else -> MUTED
        }

        gymRow.addView(TextView(activity).apply {
            text = gymTitle
            textSize = 10f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(gymTitleColor))
            setPadding(dp(activity, 8), dp(activity, 3), dp(activity, 8), dp(activity, 3))
            background = bg(activity, BG, gymTitleColor, 99, 1)
        })
        gymCard.addView(gymRow)

        val gymSubtitle = when (details.gymState) {
            "GYM_DAY" -> "Alternate-day cycle: Planned gym workout session"
            "REST_DAY" -> "Alternate-day cycle: Scheduled recovery/rest day"
            "PAUSED" -> "Routine paused: ${details.gymPauseReason ?: "Reason on file"}"
            "SETUP_REQUIRED" -> "Initial routine setup has not been confirmed on PC."
            else -> "Date prior to routine start (25 October 2026)"
        }
        gymCard.addView(TextView(activity).apply {
            text = gymSubtitle
            textSize = 10.5f
            setTextColor(color(MUTED))
            setPadding(0, dp(activity, 3), 0, dp(activity, 4))
        })

        val routineAnchorText = if (details.gymRoutineAnchor != null) {
            "Current Routine Anchor: ${StrictCalendarDataRepository.formatShortDateDisplay(details.gymRoutineAnchor)} (Every other day)"
        } else {
            "Anchor: 25 October 2026 (Every other day)"
        }
        gymCard.addView(TextView(activity).apply {
            text = routineAnchorText
            textSize = 11f
            setTextColor(color(MUTED))
        })

        val dailyGymSetup = details.dailyGymSetup
        if (dailyGymSetup != null) {
            val dailyChoiceText = if (dailyGymSetup.isGymPlanned) {
                val startDisplay = dailyGymSetup.plannedStartTime?.let { StrictCalendarDataRepository.formatTime12h(it) } ?: "Planned"
                val endDisplay = dailyGymSetup.plannedEndTime?.let { StrictCalendarDataRepository.formatTime12h(it) } ?: ""
                "Daily Choice: Gym Planned ($startDisplay${if (endDisplay.isNotBlank()) "–$endDisplay" else ""}, ${dailyGymSetup.plannedDurationMinutes} min)"
            } else {
                "Daily Choice: No Gym Planned Today"
            }
            gymCard.addView(TextView(activity).apply {
                text = "• $dailyChoiceText"
                textSize = 10.5f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(if (dailyGymSetup.isGymPlanned) CYAN else MUTED))
                setPadding(0, dp(activity, 3), 0, dp(activity, 2))
            })
        }

        // GYM Mode Section Inside Routine Card
        val gymModeBox = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(activity, 8), dp(activity, 6), dp(activity, 8), dp(activity, 6))
            background = bg(activity, BG, EDGE, 8, 1)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(activity, 6)
            }
        }
        val gymModeBadge = when (details.gymModeStatus) {
            "OFFLINE_ACTIVE" -> "⚡ GYM MODE ACTIVE (OFFLINE — SYNC PENDING)"
            "ACTIVE" -> "⚡ GYM MODE ACTIVE (SYNCED)"
            else -> "○ GYM MODE INACTIVE"
        }
        val gymModeBadgeColor = when (details.gymModeStatus) {
            "OFFLINE_ACTIVE" -> WARNING
            "ACTIVE" -> GREEN
            else -> MUTED
        }
        gymModeBox.addView(TextView(activity).apply {
            text = gymModeBadge
            textSize = 10.5f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(gymModeBadgeColor))
        })
        gymModeBox.addView(TextView(activity).apply {
            text = "• Rule: Activation window opens at 5:00 PM (until 8:30 PM)\n• Hearts/Lives Balance: ${GymModeRules.formatLivesBadge(details.gymLivesBalance)}"
            textSize = 10f
            setTextColor(color(MUTED))
            setPadding(0, dp(activity, 2), 0, 0)
        })
        gymCard.addView(gymModeBox)

        // Session verification status if recorded
        if (details.gymSessionStatus != null) {
            val sessionColor = when (details.gymSessionStatus) {
                "VERIFIED" -> GREEN
                "PENDING" -> WARNING
                else -> RED
            }
            val sessRow = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, dp(activity, 4), 0, 0)
            }
            sessRow.addView(TextView(activity).apply {
                text = "Session Verification: "
                textSize = 11f
                setTextColor(color(MUTED))
            })
            sessRow.addView(TextView(activity).apply {
                text = details.gymSessionStatus
                textSize = 10f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(sessionColor))
                setPadding(dp(activity, 6), dp(activity, 2), dp(activity, 6), dp(activity, 2))
                background = bg(activity, BG, sessionColor, 99, 1)
            })
            gymCard.addView(sessRow)
        }

        // Action button to open Device Center if setup required
        if (details.gymState == "SETUP_REQUIRED") {
            val pcBtn = Button(activity).apply {
                text = "📱 Connect to PC (Device Center)"
                textSize = 11.5f
                isAllCaps = false
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(CYAN))
                background = bg(activity, BG, CYAN, 8, 1)
                minimumHeight = dp(activity, 36)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    dp(activity, 36)
                ).apply { topMargin = dp(activity, 8) }
                setOnClickListener { onOpenDeviceCenter() }
            }
            gymCard.addView(pcBtn)
        }

        detailsCard.addView(gymCard)

        // ─────────────────────────────────────────────────────────────────────────
        // 4. Synchronization Status Card
        // ─────────────────────────────────────────────────────────────────────────
        val syncCard = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(activity, 10), dp(activity, 8), dp(activity, 10), dp(activity, 8))
            background = bg(activity, ELEVATED, EDGE, 10, 1)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(activity, 10) }
        }
        val syncText = if (details.isSyncPending) "⏳ Offline Sync Pending: Queued events waiting for PC connection" else "✓ Synchronization: Up to date with PC Engine"
        val syncColor = if (details.isSyncPending) WARNING else GREEN
        syncCard.addView(TextView(activity).apply {
            text = syncText
            textSize = 10.5f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(syncColor))
        })
        detailsCard.addView(syncCard)

        // ─────────────────────────────────────────────────────────────────────────
        // 3. Scheduled Activities List (Grouped by Period)
        // ─────────────────────────────────────────────────────────────────────────
        detailsCard.addView(TextView(activity).apply {
            text = "SCHEDULED ACTIVITIES (${details.activities.size})"
            textSize = 11.5f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(MUTED))
            letterSpacing = 0.05f
            setPadding(0, dp(activity, 4), 0, dp(activity, 6))
        })

        if (details.activities.isEmpty()) {
            val emptyCard = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER
                setPadding(dp(activity, 14), dp(activity, 14), dp(activity, 14), dp(activity, 14))
                background = bg(activity, BG, EDGE, 12, 1)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { bottomMargin = dp(activity, 8) }
            }

            emptyCard.addView(TextView(activity).apply {
                text = "📋 No scheduled activities recorded for this date."
                textSize = 12f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(MUTED))
                gravity = Gravity.CENTER
            })
            detailsCard.addView(emptyCard)
        } else {
            // Group activities into Morning (<12:00), Afternoon (12:00-17:00), Evening (>=17:00)
            fun getPeriod(timeStr: String): String {
                val clean = if (timeStr.contains("T")) timeStr.substringAfter("T").take(5) else timeStr.take(5)
                val hour = clean.split(":").firstOrNull()?.toIntOrNull() ?: 8
                return when {
                    hour < 12 -> "MORNING"
                    hour < 17 -> "AFTERNOON"
                    else -> "EVENING"
                }
            }

            val grouped = details.activities.groupBy { getPeriod(it.scheduledTime.ifBlank { it.windowStart }) }
            val periodNames = listOf(
                "MORNING" to "🌅 MORNING (until 12:00 PM)",
                "AFTERNOON" to "☀️ AFTERNOON (12:00 PM – 5:00 PM)",
                "EVENING" to "🌙 EVENING & NIGHT (after 5:00 PM)"
            )

            periodNames.forEach { (periodKey, periodTitle) ->
                val periodActs = grouped[periodKey].orEmpty()
                if (periodActs.isNotEmpty()) {
                    detailsCard.addView(TextView(activity).apply {
                        text = periodTitle
                        textSize = 10.5f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(color(CYAN))
                        setPadding(dp(activity, 2), dp(activity, 4), dp(activity, 2), dp(activity, 4))
                    })

                    periodActs.forEach { act ->
                        val actCard = LinearLayout(activity).apply {
                            orientation = LinearLayout.VERTICAL
                            setPadding(dp(activity, 10), dp(activity, 8), dp(activity, 10), dp(activity, 8))
                            background = bg(activity, BG, EDGE, 10, 1)
                            layoutParams = LinearLayout.LayoutParams(
                                ViewGroup.LayoutParams.MATCH_PARENT,
                                ViewGroup.LayoutParams.WRAP_CONTENT
                            ).apply { bottomMargin = dp(activity, 5) }
                        }

                        val row1 = LinearLayout(activity).apply {
                            orientation = LinearLayout.HORIZONTAL
                            gravity = Gravity.CENTER_VERTICAL
                        }

                        val icon = when (act.type) {
                            "WAKE_UP" -> "🌅"
                            "BATH" -> "🚿"
                            "GYM" -> "💪"
                            "HOURLY" -> "⏰"
                            "COLLEGE" -> "🎓"
                            "TRAVEL" -> "🚆"
                            else -> "📌"
                        }

                        row1.addView(TextView(activity).apply {
                            text = "$icon ${act.title.ifBlank { act.type }}"
                            textSize = 12.5f
                            setTypeface(typeface, Typeface.BOLD)
                            setTextColor(color(TEXT))
                            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                        })

                        val statusColor = when (act.status) {
                            "VERIFIED", "APPROVED", "COMPLETED" -> GREEN
                            "SUBMITTED", "PENDING" -> WARNING
                            "MISSED", "REJECTED" -> RED
                            "EXCUSED" -> MUTED
                            else -> CYAN
                        }

                        row1.addView(TextView(activity).apply {
                            text = act.status
                            textSize = 9.5f
                            setTypeface(typeface, Typeface.BOLD)
                            setTextColor(color(statusColor))
                            setPadding(dp(activity, 6), dp(activity, 2), dp(activity, 6), dp(activity, 2))
                            background = bg(activity, SURFACE, statusColor, 99, 1)
                        })
                        actCard.addView(row1)

                        // Clear distinction between Target Slot vs Enforced Window
                        val slotFormatted = StrictCalendarDataRepository.formatTime12h(act.scheduledTime)
                        val winStart = StrictCalendarDataRepository.formatTime12h(act.windowStart)
                        val winEnd = StrictCalendarDataRepository.formatTime12h(act.windowEnd)
                        val timeStr = "Target Slot: $slotFormatted  •  Enforced Window: $winStart – $winEnd"
                        actCard.addView(TextView(activity).apply {
                            text = timeStr
                            textSize = 10.5f
                            setTextColor(color(MUTED))
                            setPadding(0, dp(activity, 3), 0, 0)
                        })

                        if (act.submittedAt != null || act.points != 0) {
                            val subRow = LinearLayout(activity).apply {
                                orientation = LinearLayout.HORIZONTAL
                                gravity = Gravity.CENTER_VERTICAL
                                setPadding(0, dp(activity, 2), 0, 0)
                            }
                            if (act.submittedAt != null) {
                                subRow.addView(TextView(activity).apply {
                                    text = "Submitted: ${StrictCalendarDataRepository.formatTime12h(act.submittedAt)}"
                                    textSize = 10f
                                    setTextColor(color(GREEN))
                                    layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                                })
                            } else {
                                subRow.addView(View(activity).apply {
                                    layoutParams = LinearLayout.LayoutParams(0, 0, 1f)
                                })
                            }
                            if (act.points != 0) {
                                subRow.addView(TextView(activity).apply {
                                    text = if (act.points > 0) "+${act.points} pts" else "${act.points} pts"
                                    textSize = 10f
                                    setTypeface(typeface, Typeface.BOLD)
                                    setTextColor(color(if (act.points > 0) GREEN else RED))
                                })
                            }
                            actCard.addView(subRow)
                        }

                        detailsCard.addView(actCard)
                    }
                }
            }
        }

        // ─────────────────────────────────────────────────────────────────────────
        // 4. Score Events & Ledger Summary Section
        // ─────────────────────────────────────────────────────────────────────────
        if (details.scoreEvents.isNotEmpty()) {
            val netPoints = details.scoreEvents.sumOf { it.points }
            val netPointsStr = if (netPoints >= 0) "+$netPoints" else "$netPoints"

            detailsCard.addView(TextView(activity).apply {
                text = "SCORE EVENTS (${details.scoreEvents.size})"
                textSize = 11.5f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(MUTED))
                letterSpacing = 0.05f
                setPadding(0, dp(activity, 8), 0, dp(activity, 4))
            })

            // Summary Card
            val summaryCard = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(activity, 10), dp(activity, 6), dp(activity, 10), dp(activity, 6))
                background = bg(activity, ELEVATED, EDGE, 10, 1)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { bottomMargin = dp(activity, 6) }
            }

            summaryCard.addView(TextView(activity).apply {
                text = "Total Events: ${details.scoreEvents.size}"
                textSize = 11f
                setTextColor(color(MUTED))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            })

            summaryCard.addView(TextView(activity).apply {
                text = "Net Impact: $netPointsStr pts"
                textSize = 11.5f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(if (netPoints >= 0) GREEN else RED))
            })
            detailsCard.addView(summaryCard)

            // Events List (Top 3 by default, expandable)
            val eventsToShow = if (scoreEventsExpanded || details.scoreEvents.size <= 3) {
                details.scoreEvents
            } else {
                details.scoreEvents.take(3)
            }

            eventsToShow.forEach { ev ->
                val evRow = LinearLayout(activity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                    setPadding(dp(activity, 6), dp(activity, 4), dp(activity, 6), dp(activity, 4))
                    background = bg(activity, BG, EDGE, 8, 1)
                    layoutParams = LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT
                    ).apply { bottomMargin = dp(activity, 3) }
                }

                evRow.addView(TextView(activity).apply {
                    text = "${if (ev.points > 0) "+" else ""}${ev.points} pts"
                    textSize = 11f
                    setTypeface(typeface, Typeface.BOLD)
                    setTextColor(color(if (ev.points >= 0) GREEN else RED))
                    layoutParams = LinearLayout.LayoutParams(dp(activity, 56), ViewGroup.LayoutParams.WRAP_CONTENT)
                })

                val descCol = LinearLayout(activity).apply {
                    orientation = LinearLayout.VERTICAL
                    layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                }

                descCol.addView(TextView(activity).apply {
                    text = ev.description.ifBlank { ev.eventType }
                    textSize = 11f
                    setTextColor(color(TEXT))
                })

                val timeClean = if (ev.timestamp.contains("T")) ev.timestamp.substringAfter("T").take(8) else ev.timestamp.take(8)
                if (timeClean.isNotBlank()) {
                    descCol.addView(TextView(activity).apply {
                        text = "Time: $timeClean"
                        textSize = 9.5f
                        setTextColor(color(MUTED))
                    })
                }
                evRow.addView(descCol)
                detailsCard.addView(evRow)
            }

            if (details.scoreEvents.size > 3) {
                val toggleBtn = TextView(activity).apply {
                    text = if (scoreEventsExpanded) "▲ Show fewer events" else "▼ Show all (${details.scoreEvents.size} events)"
                    textSize = 11f
                    setTypeface(typeface, Typeface.BOLD)
                    gravity = Gravity.CENTER
                    setTextColor(color(CYAN))
                    setPadding(0, dp(activity, 6), 0, dp(activity, 4))
                    isClickable = true
                    isFocusable = true
                    setOnClickListener { onToggleScoreEvents() }
                }
                detailsCard.addView(toggleBtn)
            }
        }

        parent.addView(detailsCard)
    }
}
