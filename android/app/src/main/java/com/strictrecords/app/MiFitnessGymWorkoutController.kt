package com.strictrecords.app

import android.app.AlertDialog
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors

/**
 * Controller for Mi Fitness Gym Workout Screenshot evaluation, user-facing details modal,
 * and diagnostic sandbox test dialog.
 */
object MiFitnessGymWorkoutController {

    private val bgExecutor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

    fun surfaceBg(context: Context, fillHex: String, radiusDp: Int = 20, strokeHex: String? = null, strokeDp: Int = 1): GradientDrawable {
        val density = context.resources.displayMetrics.density
        return GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = radiusDp * density
            setColor(Color.parseColor(fillHex))
            if (strokeHex != null && strokeDp > 0) {
                setStroke((strokeDp * density).toInt(), Color.parseColor(strokeHex))
            }
        }
    }

    data class GymCardTelemetry(
        val status: String, // "NOT_SUBMITTED", "PENDING_EVALUATION", "EVALUATING", "APPROVED", "NEEDS_REVIEW", "REJECTED"
        val statusLabel: String,
        val statusColorHex: String,
        val pointsAwarded: Int,
        val isCompleted: Boolean,
        val summaryLine: String,
        val workoutDate: String? = null,
        val workoutTime: String? = null,
        val workoutType: String? = null,
        val durationDisplay: String? = null,
        val workoutCalories: Int? = null,
        val totalCalories: Int? = null,
        val avgHr: Int? = null,
        val maxHr: Int? = null
    )

    fun getGymCardTelemetry(
        db: StrictRecordsLocalDb,
        activityId: String,
        date: String
    ): GymCardTelemetry {
        val eval = db.getProofEvaluationByActivity(activityId, date)
        val act = db.getDailyActivities(date).firstOrNull { it.activityId == activityId || it.type == "GYM" }

        val evalStatus = eval?.optString("status") ?: ""
        val points = eval?.optInt("points_awarded", 0) ?: 0
        val rawOutput = eval?.optString("evaluator_output_json") ?: "{}"
        val outputObj = runCatching { JSONObject(rawOutput) }.getOrDefault(JSONObject())

        val detailsObj = runCatching {
            if (!act?.detailsJson.isNullOrBlank()) JSONObject(act!!.detailsJson) else JSONObject()
        }.getOrDefault(JSONObject())
        val workoutEvalObj = detailsObj.optJSONObject("workout_evaluation") ?: outputObj

        val parsedObj = workoutEvalObj.optJSONObject("parsed_data") ?: workoutEvalObj

        val wDate = parsedObj.optString("workout_date", "").takeIf { it.isNotBlank() }
        val wTime = parsedObj.optJSONObject("workout_time")?.optString("formatted_12h")
            ?: parsedObj.optString("workout_time", "").takeIf { it.isNotBlank() }
        val wType = parsedObj.optString("workout_type", "").takeIf { it.isNotBlank() }
        val durDisp = parsedObj.optString("duration_display", "").takeIf { it.isNotBlank() }
        val wkCal = parsedObj.optInt("workout_calories", -1).takeIf { it >= 0 }
        val totCal = parsedObj.optInt("total_calories", -1).takeIf { it >= 0 }
        val avgHr = parsedObj.optInt("avg_heart_rate", -1).takeIf { it > 0 }
        val maxHr = parsedObj.optInt("max_heart_rate", -1).takeIf { it > 0 }

        return when {
            evalStatus == ProofSubmissionRules.States.APPROVED || act?.status in listOf("VERIFIED", "APPROVED") -> {
                GymCardTelemetry(
                    status = "APPROVED",
                    statusLabel = "Approved (+${points.coerceAtLeast(15)} pts)",
                    statusColorHex = MainActivity.CLR_GREEN,
                    pointsAwarded = points.coerceAtLeast(15),
                    isCompleted = true,
                    summaryLine = "Mi Fitness Gym workout validated (+15 pts)",
                    workoutDate = wDate,
                    workoutTime = wTime,
                    workoutType = wType,
                    durationDisplay = durDisp,
                    workoutCalories = wkCal,
                    totalCalories = totCal,
                    avgHr = avgHr,
                    maxHr = maxHr
                )
            }
            evalStatus in listOf(ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION, ProofSubmissionRules.States.EVALUATING, "SUBMITTED", "PENDING_EVALUATION") -> {
                GymCardTelemetry(
                    status = "EVALUATING",
                    statusLabel = "Analysis Pending",
                    statusColorHex = MainActivity.CLR_CYAN,
                    pointsAwarded = 0,
                    isCompleted = false,
                    summaryLine = "Gym proof submitted — workout analysis pending",
                    workoutDate = wDate,
                    workoutTime = wTime,
                    workoutType = wType,
                    durationDisplay = durDisp,
                    workoutCalories = wkCal,
                    totalCalories = totCal,
                    avgHr = avgHr,
                    maxHr = maxHr
                )
            }
            workoutEvalObj.optBoolean("date_mismatch", false) || evalStatus in listOf("FLAGGED_FOR_REVIEW", "NEEDS_REVIEW") -> {
                GymCardTelemetry(
                    status = "NEEDS_REVIEW",
                    statusLabel = "Needs Review",
                    statusColorHex = MainActivity.CLR_AMBER,
                    pointsAwarded = 0,
                    isCompleted = false,
                    summaryLine = "Workout date does not match the scheduled Gym session. This proof requires review.",
                    workoutDate = wDate,
                    workoutTime = wTime,
                    workoutType = wType,
                    durationDisplay = durDisp,
                    workoutCalories = wkCal,
                    totalCalories = totCal,
                    avgHr = avgHr,
                    maxHr = maxHr
                )
            }
            evalStatus == ProofSubmissionRules.States.REJECTED || act?.status in listOf("REJECTED", "MISSED") -> {
                GymCardTelemetry(
                    status = "REJECTED",
                    statusLabel = "Rejected (0 pts)",
                    statusColorHex = MainActivity.CLR_RED,
                    pointsAwarded = 0,
                    isCompleted = false,
                    summaryLine = workoutEvalObj.optString("reason", "Gym workout verification rejected"),
                    workoutDate = wDate,
                    workoutTime = wTime,
                    workoutType = wType,
                    durationDisplay = durDisp,
                    workoutCalories = wkCal,
                    totalCalories = totCal,
                    avgHr = avgHr,
                    maxHr = maxHr
                )
            }
            else -> {
                GymCardTelemetry(
                    status = "NOT_SUBMITTED",
                    statusLabel = "Upcoming / Open",
                    statusColorHex = MainActivity.CLR_TEXT_SECONDARY,
                    pointsAwarded = 0,
                    isCompleted = false,
                    summaryLine = "Attach Mi Fitness workout proof during scheduled window"
                )
            }
        }
    }

    /**
     * Shows the user-facing Gym Proof Details modal with all extracted fields,
     * session matching status, and integrity evaluation breakdown.
     */
    fun showGymProofDetailsDialog(
        activity: MainActivity,
        db: StrictRecordsLocalDb,
        targetAct: DisciplineScoringRules.ScheduledActivityItem? = null
    ) {
        fun dp(v: Number) = TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), activity.resources.displayMetrics
        ).toInt()

        val act = targetAct ?: db.getActiveOrTargetGymActivity()
            ?: db.getDailyActivities().firstOrNull { it.type == "GYM" }
            ?: DisciplineScoringRules.ScheduledActivityItem(
                activityId = "ACT-${db.nowDate().replace("-", "")}-1830-GYM",
                date = db.nowDate(),
                scheduledTime = "19:40-20:40",
                windowStart = "",
                windowEnd = "",
                status = "UPCOMING",
                type = "GYM",
                title = "Gym Session",
                scheduleVersion = 1,
                detailsJson = "{}"
            )

        val date = act.date
        val actId = act.activityId
        val eval = db.getProofEvaluationByActivity(actId, date)
        val evalStatus = eval?.optString("status") ?: ""
        val points = eval?.optInt("points_awarded", 0) ?: act.points
        val rawOutput = eval?.optString("evaluator_output_json") ?: "{}"
        val outputObj = runCatching { JSONObject(rawOutput) }.getOrDefault(JSONObject())

        val detailsObj = runCatching {
            if (act.detailsJson.isNotBlank()) JSONObject(act.detailsJson) else JSONObject()
        }.getOrDefault(JSONObject())
        val workoutEvalObj = detailsObj.optJSONObject("workout_evaluation") ?: outputObj

        val parsedObj = workoutEvalObj.optJSONObject("parsed_data") ?: workoutEvalObj

        val scroll = ScrollView(activity)
        val root = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(18), dp(20), dp(18))
            background = surfaceBg(activity, MainActivity.CLR_MODAL, 20, MainActivity.CLR_BORDER, 1)
        }
        scroll.addView(root)

        // Header
        val headerRow = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(12)
            }
        }
        val icon = ImageView(activity).apply {
            setImageResource(R.drawable.strictbot_head)
            layoutParams = LinearLayout.LayoutParams(dp(28), dp(28)).apply { rightMargin = dp(10) }
        }
        headerRow.addView(icon)

        val titleCol = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        titleCol.addView(TextView(activity).apply {
            text = "🏋️ GYM WORKOUT ANALYSIS"
            textSize = 14f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor(MainActivity.CLR_PINK))
            letterSpacing = 0.05f
        })
        titleCol.addView(TextView(activity).apply {
            text = "Mi Fitness Screenshot OCR & Session Validation"
            textSize = 10.5f
            setTextColor(Color.parseColor(MainActivity.CLR_TEXT_SECONDARY))
        })
        headerRow.addView(titleCol)
        root.addView(headerRow)

        // Status Banner Card
        val isApproved = evalStatus == ProofSubmissionRules.States.APPROVED || act.status in listOf("VERIFIED", "APPROVED")
        val isPending = evalStatus in listOf(ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION, ProofSubmissionRules.States.EVALUATING, "SUBMITTED", "PENDING_EVALUATION")
        val isNeedsReview = workoutEvalObj.optBoolean("date_mismatch", false) || evalStatus in listOf("FLAGGED_FOR_REVIEW", "NEEDS_REVIEW")

        val statusColor = when {
            isApproved -> MainActivity.CLR_GREEN
            isNeedsReview -> MainActivity.CLR_AMBER
            isPending -> MainActivity.CLR_CYAN
            else -> MainActivity.CLR_RED
        }

        val statusTitle = when {
            isApproved -> "✓ APPROVED (+${points.coerceAtLeast(15)} PTS)"
            isNeedsReview -> "⚠️ NEEDS REVIEW (0 PTS)"
            isPending -> "⏳ ANALYSIS PENDING"
            else -> "❌ REJECTED (0 PTS)"
        }

        val statusDesc = when {
            isApproved -> workoutEvalObj.optString("reason", "Valid Mi Fitness Gym workout verified (+15 pts).")
            isNeedsReview && workoutEvalObj.optBoolean("date_mismatch", false) ->
                "Workout date does not match the scheduled Gym session. This proof requires review."
            isPending ->
                "Gym proof submitted successfully. Duplicate, authenticity, and session-matching checks are pending."
            else -> workoutEvalObj.optString("reason", "Workout proof does not meet acceptance criteria.")
        }

        val statusCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(12), dp(14), dp(12))
            background = surfaceBg(activity, MainActivity.CLR_SURFACE, 14, statusColor, 1)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(14)
            }
        }
        statusCard.addView(TextView(activity).apply {
            text = statusTitle
            textSize = 12.5f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor(statusColor))
        })
        statusCard.addView(TextView(activity).apply {
            text = statusDesc
            textSize = 11f
            setTextColor(Color.WHITE)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(4)
            }
        })
        root.addView(statusCard)

        // Extracted Fields Card
        root.addView(TextView(activity).apply {
            text = "EXTRACTED WORKOUT DATA"
            textSize = 11f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor(MainActivity.CLR_TEXT_PRIMARY))
            letterSpacing = 0.04f
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(6)
            }
        })

        val fieldsCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(10), dp(14), dp(10))
            background = surfaceBg(activity, MainActivity.CLR_ELEVATED, 12, MainActivity.CLR_BORDER, 1)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(14)
            }
        }

        fun addRow(label: String, value: String?, isAccent: Boolean = false) {
            val row = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, dp(4), 0, dp(4))
            }
            row.addView(TextView(activity).apply {
                text = label
                textSize = 11f
                setTextColor(Color.parseColor(MainActivity.CLR_TEXT_SECONDARY))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.1f)
            })
            row.addView(TextView(activity).apply {
                text = value?.ifBlank { "Not detected" } ?: "Not detected"
                textSize = 11f
                typeface = if (isAccent) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
                setTextColor(if (value.isNullOrBlank() || value == "Not detected") Color.parseColor(MainActivity.CLR_TEXT_DIM)
                else if (isAccent) Color.parseColor(MainActivity.CLR_GREEN) else Color.WHITE)
                gravity = Gravity.END
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.4f)
            })
            fieldsCard.addView(row)
        }

        val extDate = parsedObj.optString("workout_date", "").takeIf { it.isNotBlank() }
        val extTime = parsedObj.optJSONObject("workout_time")?.optString("formatted_12h")
            ?: parsedObj.optString("workout_time", "").takeIf { it.isNotBlank() }
        val dateTimeDisplay = if (extDate != null && extTime != null) "$extDate • $extTime"
        else extDate ?: extTime

        val wkCal = parsedObj.optInt("workout_calories", -1).takeIf { it >= 0 }
        val totCal = parsedObj.optInt("total_calories", -1).takeIf { it >= 0 }
        val durDisp = parsedObj.optString("duration_display", "").takeIf { it.isNotBlank() }
            ?: parsedObj.optInt("duration_seconds", -1).takeIf { it > 0 }?.let { MiFitnessGymWorkoutRules.formatDuration(it) }

        addRow("Workout Date & Time", dateTimeDisplay)
        addRow("Workout Type", parsedObj.optString("workout_type", "").takeIf { it.isNotBlank() })
        addRow("Duration", durDisp)
        addRow("Workout Calories", wkCal?.let { "$it kcal" }, isAccent = true)
        addRow("Total Calories", totCal?.let { "$it kcal" })

        val avgHr = parsedObj.optInt("avg_heart_rate", -1).takeIf { it > 0 }
        val maxHr = parsedObj.optInt("max_heart_rate", -1).takeIf { it > 0 }
        addRow("Average Heart Rate", avgHr?.let { "$it BPM" })
        addRow("Maximum Heart Rate", maxHr?.let { "$it BPM" })

        addRow("Training Effect", parsedObj.optString("training_effect", "").takeIf { it.isNotBlank() })
        addRow("Data Source", parsedObj.optString("data_source", "").takeIf { it.isNotBlank() })

        val zonesObj = parsedObj.optJSONObject("heart_rate_zones")
        if (zonesObj != null && zonesObj.length() > 0) {
            val zonesSb = StringBuilder()
            val keys = zonesObj.keys()
            while (keys.hasNext()) {
                val k = keys.next()
                zonesSb.append("$k: ${zonesObj.optString(k)}  ")
            }
            addRow("HR Zones", zonesSb.toString().trim())
        }

        root.addView(fieldsCard)

        // Session Matching & Window Card
        root.addView(TextView(activity).apply {
            text = "SESSION MATCHING & WINDOWS"
            textSize = 11f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor(MainActivity.CLR_TEXT_PRIMARY))
            letterSpacing = 0.04f
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(6)
            }
        })

        val matchCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(10), dp(14), dp(10))
            background = surfaceBg(activity, MainActivity.CLR_ELEVATED, 12, MainActivity.CLR_BORDER, 1)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(14)
            }
        }

        val plannedTime = detailsObj.optString("planned_time", "18:30")
        val gymWin = GymWindowRules.calculateGymWindow(date, plannedTime, db.scheduleTimezone())

        fun addMatchRow(label: String, value: String, isOk: Boolean) {
            val row = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, dp(4), 0, dp(4))
            }
            row.addView(TextView(activity).apply {
                text = label
                textSize = 11f
                setTextColor(Color.parseColor(MainActivity.CLR_TEXT_SECONDARY))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.1f)
            })
            row.addView(TextView(activity).apply {
                text = value
                textSize = 11f
                typeface = Typeface.DEFAULT_BOLD
                setTextColor(Color.parseColor(if (isOk) MainActivity.CLR_GREEN else MainActivity.CLR_AMBER))
                gravity = Gravity.END
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.4f)
            })
            matchCard.addView(row)
        }

        val dateMatches = extDate == date
        addMatchRow("Scheduled Session", "$date • $plannedTime", true)
        addMatchRow("Session Date Match", if (dateMatches) "Matched ($date)" else "Mismatch ($extDate vs $date)", dateMatches)
        addMatchRow("Submission Window", gymWin.windowDisplay, true)

        val endIso = workoutEvalObj.optString("calculated_end_time_iso", "")
        if (endIso.isNotBlank()) {
            val endDisplay = ScheduleClock.display(db.parseIsoToMs(endIso), db.scheduleTimezone(), "hh:mm a")
            addMatchRow("Calculated Workout End", "$endDisplay (Derived)", true)
        }

        val recArr = detailsObj.optJSONArray("reconciled_hourly_occurrences")
        if (recArr != null && recArr.length() > 0) {
            val recSlots = (0 until recArr.length()).map { recArr.getString(it) }.joinToString(", ")
            addMatchRow("Reconciled Hourly Slots", recSlots, true)
        }

        root.addView(matchCard)

        // Close button
        val btnClose = Button(activity).apply {
            text = "CLOSE"
            setTextColor(Color.parseColor(MainActivity.CLR_TEXT_SECONDARY))
            textSize = 11.5f
            typeface = Typeface.DEFAULT_BOLD
            background = surfaceBg(activity, MainActivity.CLR_SURFACE, 10, MainActivity.CLR_BORDER, 1)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }

        var dialogRef: AlertDialog? = null
        btnClose.setOnClickListener { dialogRef?.dismiss() }
        root.addView(btnClose)

        dialogRef = AlertDialog.Builder(activity)
            .setView(scroll)
            .create()
        dialogRef.window?.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        dialogRef.show()
    }

    /**
     * Diagnostic sandbox mode: allows testing any workout screenshot at any time
     * with zero score impact, running real ML Kit OCR and showing all extracted fields.
     */
    fun showGymWorkoutDemoTestDialog(
        activity: MainActivity,
        db: StrictRecordsLocalDb
    ) {
        fun dp(v: Number) = TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), activity.resources.displayMetrics
        ).toInt()

        val date = db.nowDate()
        val scroll = ScrollView(activity)
        val root = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(18), dp(18), dp(18))
            background = surfaceBg(activity, MainActivity.CLR_MODAL, 20, MainActivity.CLR_BORDER, 1)
        }
        scroll.addView(root)

        root.addView(TextView(activity).apply {
            text = "🏋️ Demo Test: Mi Fitness Gym OCR"
            textSize = 16f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.WHITE)
        })
        root.addView(TextView(activity).apply {
            text = "Diagnostic Mode • Zero Score Impact • Any Time of Day"
            textSize = 11.5f
            setTextColor(Color.parseColor(MainActivity.CLR_TEXT_SECONDARY))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(2)
                bottomMargin = dp(12)
            }
        })

        val banner = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(10), dp(12), dp(10))
            background = surfaceBg(activity, MainActivity.CLR_SURFACE, 12, MainActivity.CLR_CYAN, 1)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(14)
            }
        }
        banner.addView(TextView(activity).apply {
            text = "ℹ️ OCR & EXTRACTION SANDBOX"
            textSize = 11f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor(MainActivity.CLR_CYAN))
        })
        banner.addView(TextView(activity).apply {
            text = "Select any Mi Fitness Gym workout screenshot to test:\n" +
                    "• OCR extraction of Date, Time, Type, Duration & HR.\n" +
                    "• Separation of Workout Calories vs Total Calories.\n" +
                    "• Session-matching against scheduled occurrences.\n" +
                    "🔒 Diagnostic sandbox — zero score impact."
            textSize = 10.5f
            setTextColor(Color.parseColor(MainActivity.CLR_TEXT_PRIMARY))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(4)
            }
        })
        root.addView(banner)

        val progressRow = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            visibility = View.GONE
            setPadding(0, dp(8), 0, dp(8))
        }
        progressRow.addView(ProgressBar(activity).apply {
            layoutParams = LinearLayout.LayoutParams(dp(22), dp(22)).apply { rightMargin = dp(10) }
        })
        val progressText = TextView(activity).apply {
            text = "Running on-device ML Kit OCR..."
            textSize = 11.5f
            setTextColor(Color.parseColor(MainActivity.CLR_CYAN))
        }
        progressRow.addView(progressText)
        root.addView(progressRow)

        val resultsCard = TextView(activity).apply {
            visibility = View.GONE
            textSize = 11f
            typeface = Typeface.MONOSPACE
            setTextColor(Color.parseColor(MainActivity.CLR_TEXT_PRIMARY))
            setPadding(dp(12), dp(10), dp(12), dp(10))
            background = surfaceBg(activity, MainActivity.CLR_ELEVATED, 10, MainActivity.CLR_BORDER, 1)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(12)
            }
        }
        root.addView(resultsCard)

        var dialogRef: AlertDialog? = null

        val btnSelect = Button(activity).apply {
            text = "🖼️ SELECT WORKOUT SCREENSHOT"
            setTextColor(Color.parseColor("#070A0C"))
            textSize = 12f
            typeface = Typeface.DEFAULT_BOLD
            background = surfaceBg(activity, MainActivity.CLR_GREEN, 10)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(8)
            }
            setOnClickListener {
                activity.pickGymScreenshot { uris ->
                    if (uris.isEmpty()) return@pickGymScreenshot
                    progressRow.visibility = View.VISIBLE
                    resultsCard.visibility = View.GONE
                    progressText.text = "Running OCR & analyzing workout..."

                    bgExecutor.execute {
                        try {
                            val uri = uris.first()
                            val analysis = EvidenceAnalysisEngine.analyzeUri(
                                activity.applicationContext,
                                uri,
                                attachmentId = "gym_demo_${System.currentTimeMillis()}",
                                taskId = "gym_demo_$date",
                                batchId = "gym_demo_batch"
                            )
                            val ocr = analysis.optJSONObject("ocr")
                            val text = ocr?.optString("text") ?: ""
                            val primarySha256 = analysis.optJSONObject("original")?.optString("sha256") ?: ""

                            val validation = MiFitnessGymWorkoutValidator.validate(
                                ocrText = text,
                                defaultYear = date.take(4).toIntOrNull() ?: 2026,
                                claimedDate = date,
                                sourceImageId = "gym_demo_upload",
                                sourceIntegrityHash = primarySha256
                            )
                            val eval = MiFitnessGymWorkoutRules.evaluateGymWorkout(
                                parsed = validation.toParsedWorkoutData(),
                                claimedDate = validation.workoutDate ?: date,
                                scheduledActivityId = "DEMO-GYM",
                                plannedTimeHhmm = "18:30"
                            )

                            mainHandler.post {
                                progressRow.visibility = View.GONE
                                resultsCard.visibility = View.VISIBLE
                                resultsCard.text = buildString {
                                    appendLine("🏋️ GYM WORKOUT OCR & VALIDATION RESULTS")
                                    appendLine("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                                    appendLine("• Status: ${eval.status} (${eval.decision})")
                                    appendLine("• Award: ${eval.points} pts (sandbox mode)")
                                    appendLine("• Reason: ${eval.reason}")
                                    appendLine("────────────────────────────────────────")
                                    appendLine("• Workout Date: ${validation.workoutDate ?: "Not detected"}")
                                    appendLine("• Workout Time: ${validation.workoutTime?.formatted12h ?: "Not detected"}")
                                    appendLine("• Workout Type: ${validation.workoutType ?: "Not detected"}")
                                    appendLine("• Duration: ${validation.durationDisplay ?: "Not detected"} (${validation.durationSeconds ?: 0}s)")
                                    appendLine("• Workout Calories: ${validation.workoutCalories?.let { "$it kcal" } ?: "Not detected"}")
                                    appendLine("• Total Calories: ${validation.totalCalories?.let { "$it kcal" } ?: "Not detected"}")
                                    appendLine("• Avg HR: ${validation.avgHeartRate?.let { "$it BPM" } ?: "Not detected"}")
                                    appendLine("• Max HR: ${validation.maxHeartRate?.let { "$it BPM" } ?: "Not detected"}")
                                    appendLine("• Zones: ${if (validation.heartRateZones.isNotEmpty()) validation.heartRateZones.entries.joinToString { "${it.key}: ${it.value}" } else "Not detected"}")
                                    appendLine("• Training Effect: ${validation.trainingEffect ?: "Not detected"}")
                                    appendLine("• Data Source: ${validation.dataSource ?: "Not detected"}")
                                    if (eval.calculatedEndTimeIso != null) {
                                        appendLine("• Calculated End: ${eval.calculatedEndTimeIso}")
                                    }
                                    if (eval.discrepancyReasons.isNotEmpty()) {
                                        appendLine("────────────────────────────────────────")
                                        appendLine("⚠️ Discrepancies:")
                                        eval.discrepancyReasons.forEach { appendLine("  - $it") }
                                    }
                                }
                            }
                        } catch (e: Exception) {
                            mainHandler.post {
                                progressRow.visibility = View.GONE
                                resultsCard.visibility = View.VISIBLE
                                resultsCard.text = "Error analyzing workout screenshot: ${e.message}"
                            }
                        }
                    }
                }
            }
        }
        root.addView(btnSelect)

        val btnClose = Button(activity).apply {
            text = "CLOSE"
            setTextColor(Color.parseColor(MainActivity.CLR_TEXT_SECONDARY))
            textSize = 11f
            background = surfaceBg(activity, MainActivity.CLR_SURFACE, 10, MainActivity.CLR_BORDER, 1)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            setOnClickListener { dialogRef?.dismiss() }
        }
        root.addView(btnClose)

        dialogRef = AlertDialog.Builder(activity)
            .setView(scroll)
            .create()
        dialogRef.window?.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        dialogRef.show()
    }
}
