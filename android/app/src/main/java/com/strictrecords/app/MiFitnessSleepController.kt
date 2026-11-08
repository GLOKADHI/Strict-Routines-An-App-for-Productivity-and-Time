package com.strictrecords.app

import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import java.util.concurrent.Executors

/**
 * Controller for the Mi Fitness Sleep Data Bonus workflow.
 * Manages Home-screen card telemetry, screenshot OCR & VLM parsing,
 * deterministic scoring evaluation, and user confirmation dialogs.
 */
object MiFitnessSleepController {

    private val bgExecutor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

    data class SleepCardTelemetry(
        val status: String, // "NOT_UPLOADED", "PROCESSING", "APPROVED", "PARTIALLY_AWARDED", "REJECTED", "DEADLINE_PASSED"
        val statusLabel: String,
        val statusColorHex: String,
        val pointsAwarded: Int,
        val isCompleted: Boolean,
        val isDeadlinePassed: Boolean,
        val summaryLine: String,
        val onsetTime: String?,
        val wakeUpTime: String?
    )

    fun getSleepCardTelemetry(db: StrictRecordsLocalDb, date: String, isDevMode: Boolean): SleepCardTelemetry {
        val submission = db.getSleepSubmission(date, isDevMode)
        val nowMs = db.authoritativeNowMs()
        val deadlinePassed = MiFitnessSleepBonusRules.isDeadlinePassed(nowMs, date, db.scheduleTimezone())

        val pendingEval = if (submission == null) db.getProofEvaluationByActivity("act_sleep_$date", date, isDevMode) else null
        if (pendingEval != null && pendingEval.optString("status") in listOf(
            ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION,
            ProofSubmissionRules.States.EVALUATING
        )) {
            return SleepCardTelemetry(
                status = "EVALUATING",
                statusLabel = "Verification Pending",
                statusColorHex = "#D29922",
                pointsAwarded = 0,
                isCompleted = false,
                isDeadlinePassed = deadlinePassed,
                summaryLine = "Sleep report submitted — verification pending",
                onsetTime = null,
                wakeUpTime = null
            )
        }

        if (submission != null) {
            val st = submission.optString("status", "NEEDS_REVIEW")
            val pts = submission.optInt("points_awarded", 0)
            val onset = submission.optString("sleep_onset", "").takeIf { it.isNotBlank() }
            val wake = submission.optString("wake_up", "").takeIf { it.isNotBlank() }

            return when (st) {
                ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION,
                ProofSubmissionRules.States.EVALUATING,
                "PENDING_EVALUATION",
                "SUBMITTED" -> SleepCardTelemetry(
                    status = "EVALUATING",
                    statusLabel = "Verification Pending",
                    statusColorHex = "#D29922",
                    pointsAwarded = 0,
                    isCompleted = false,
                    isDeadlinePassed = deadlinePassed,
                    summaryLine = "Sleep report submitted — verification pending",
                    onsetTime = onset,
                    wakeUpTime = wake
                )
                "APPROVED" -> SleepCardTelemetry(
                    status = "APPROVED",
                    statusLabel = "Approved (+$pts pts)",
                    statusColorHex = "#3FB950",
                    pointsAwarded = pts,
                    isCompleted = true,
                    isDeadlinePassed = deadlinePassed,
                    summaryLine = "Full 5-point bonus awarded • Onset: $onset • Wake: $wake",
                    onsetTime = onset,
                    wakeUpTime = wake
                )
                "PARTIALLY_AWARDED" -> SleepCardTelemetry(
                    status = "PARTIALLY_AWARDED",
                    statusLabel = "Partially awarded (+$pts pts)",
                    statusColorHex = "#58A6FF",
                    pointsAwarded = pts,
                    isCompleted = true,
                    isDeadlinePassed = deadlinePassed,
                    summaryLine = "Partial bonus awarded ($pts pts) • Onset: $onset • Wake: $wake",
                    onsetTime = onset,
                    wakeUpTime = wake
                )
                "ALREADY_AWARDED" -> SleepCardTelemetry(
                    status = if (pts == 5) "APPROVED" else "PARTIALLY_AWARDED",
                    statusLabel = if (pts == 5) "Approved (+$pts pts)" else "Partially awarded (+$pts pts)",
                    statusColorHex = if (pts == 5) "#3FB950" else "#58A6FF",
                    pointsAwarded = pts,
                    isCompleted = true,
                    isDeadlinePassed = deadlinePassed,
                    summaryLine = if (pts == 5) "Full 5-point bonus awarded • Onset: $onset • Wake: $wake" else "Partial bonus awarded ($pts pts) • Onset: $onset • Wake: $wake",
                    onsetTime = onset,
                    wakeUpTime = wake
                )
                "DEADLINE_PASSED" -> SleepCardTelemetry(
                    status = "DEADLINE_PASSED",
                    statusLabel = "Deadline passed",
                    statusColorHex = "#8B949E",
                    pointsAwarded = 0,
                    isCompleted = false,
                    isDeadlinePassed = true,
                    summaryLine = "12:00 PM submission deadline has passed for $date",
                    onsetTime = onset,
                    wakeUpTime = wake
                )
                "REJECTED", "WAKE_AFTER_CUTOFF", "DATE_MISMATCH", "ZERO_POINTS", "INCONSISTENT_TIMESTAMPS", "DUPLICATE_SCREENSHOT" -> SleepCardTelemetry(
                    status = "REJECTED",
                    statusLabel = "Rejected (0 pts)",
                    statusColorHex = "#F85149",
                    pointsAwarded = 0,
                    isCompleted = true,
                    isDeadlinePassed = deadlinePassed,
                    summaryLine = submission.optString("rejection_reason", "Sleep data was not eligible for bonus"),
                    onsetTime = onset,
                    wakeUpTime = wake
                )
                else -> SleepCardTelemetry(
                    status = "NEEDS_REVIEW",
                    statusLabel = "Needs review",
                    statusColorHex = "#D29922",
                    pointsAwarded = 0,
                    isCompleted = false,
                    isDeadlinePassed = deadlinePassed,
                    summaryLine = "Critical fields require clarification or re-upload",
                    onsetTime = onset,
                    wakeUpTime = wake
                )
            }
        }

        return if (deadlinePassed) {
            SleepCardTelemetry(
                status = "DEADLINE_PASSED",
                statusLabel = "Deadline passed",
                statusColorHex = "#8B949E",
                pointsAwarded = 0,
                isCompleted = false,
                isDeadlinePassed = true,
                summaryLine = "Upload deadline of 12:00 PM for $date has passed",
                onsetTime = null,
                wakeUpTime = null
            )
        } else {
            SleepCardTelemetry(
                status = "NOT_UPLOADED",
                statusLabel = "Not uploaded",
                statusColorHex = "#E3B341",
                pointsAwarded = 0,
                isCompleted = false,
                isDeadlinePassed = false,
                summaryLine = "Upload daily Mi Fitness sleep screenshot before 12:00 PM (up to 5 pts)",
                onsetTime = null,
                wakeUpTime = null
            )
        }
    }

    /**
     * Builds the visually consistent Home-screen card for "Upload Sleep Data".
     */
    fun buildHomeScreenCard(
        activity: MainActivity,
        db: StrictRecordsLocalDb,
        isDevMode: Boolean,
        onAction: () -> Unit
    ): LinearLayout {
        fun dp(v: Number) = TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), activity.resources.displayMetrics
        ).toInt()

        val date = db.nowDate()
        val telemetry = getSleepCardTelemetry(db, date, isDevMode)

        return LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            val pad = dp(12)
            setPadding(pad, pad, pad, pad)
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#161B22"))
                cornerRadius = dp(14).toFloat()
                setStroke(dp(1), Color.parseColor("#30363D"))
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(8) }

            // Header Row: Title & Points Pill
            val headerRow = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            }

            val iconBox = FrameLayout(activity).apply {
                layoutParams = LinearLayout.LayoutParams(dp(32), dp(32)).apply { rightMargin = dp(10) }
                background = GradientDrawable().apply {
                    setColor(Color.parseColor("#0D2322"))
                    cornerRadius = dp(8).toFloat()
                    setStroke(dp(1), Color.parseColor("#1E3F3B"))
                }
            }
            iconBox.addView(TextView(activity).apply {
                text = "🌙"
                textSize = 15f
                gravity = Gravity.CENTER
                layoutParams = FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            })
            headerRow.addView(iconBox)

            val titleCol = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            }
            titleCol.addView(TextView(activity).apply {
                text = "UPLOAD SLEEP DATA"
                textSize = 12f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                setTextColor(Color.WHITE)
                letterSpacing = 0.04f
            })
            titleCol.addView(TextView(activity).apply {
                text = "Mi Fitness / Xiaomi Smartwatch Sleep Report"
                textSize = 10f
                setTextColor(Color.parseColor("#8B949E"))
            })
            headerRow.addView(titleCol)

            // Status Badge
            val statusBadge = TextView(activity).apply {
                text = telemetry.statusLabel
                textSize = 10f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                setTextColor(Color.parseColor(telemetry.statusColorHex))
                background = GradientDrawable().apply {
                    setColor(Color.parseColor("#21262D"))
                    cornerRadius = dp(99).toFloat()
                    setStroke(dp(1), Color.parseColor(telemetry.statusColorHex))
                }
                setPadding(dp(8), dp(3), dp(8), dp(3))
            }
            headerRow.addView(statusBadge)
            addView(headerRow)

            // Info & Rules Row
            val infoRow = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, dp(8), 0, dp(4))
            }
            infoRow.addView(TextView(activity).apply {
                text = "⚡ Bonus: up to 5 pts   •   ⏰ Deadline: 12:00 PM"
                textSize = 11f
                typeface = android.graphics.Typeface.MONOSPACE
                setTextColor(Color.parseColor("#7EE787"))
            })
            addView(infoRow)

            // Summary description
            val descTv = TextView(activity).apply {
                text = telemetry.summaryLine
                textSize = 10.5f
                setTextColor(Color.parseColor("#8B949E"))
                setPadding(0, 0, 0, dp(8))
            }
            addView(descTv)

            // Action Button
            val actionBtn = Button(activity).apply {
                text = when {
                    telemetry.isCompleted && telemetry.pointsAwarded > 0 -> "✓ VIEW SLEEP REPORT (+${telemetry.pointsAwarded} PTS)"
                    telemetry.isCompleted -> "🔍 VIEW SLEEP REPORT (0 PTS)"
                    telemetry.status == "EVALUATING" -> "⏳ VIEW SLEEP REPORT (PENDING)"
                    telemetry.isDeadlinePassed -> "⏰ DEADLINE PASSED"
                    else -> "🌙 SELECT SLEEP SCREENSHOT"
                }
                setTextColor(if (telemetry.isDeadlinePassed && !telemetry.isCompleted) Color.parseColor("#8B949E") else Color.WHITE)
                textSize = 11.5f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                background = GradientDrawable().apply {
                    setColor(if (telemetry.isCompleted || telemetry.isDeadlinePassed) Color.parseColor("#21262D") else Color.parseColor("#238636"))
                    cornerRadius = dp(8).toFloat()
                    setStroke(dp(1), Color.parseColor("#30363D"))
                }
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                isEnabled = !telemetry.isDeadlinePassed || telemetry.isCompleted
                setOnClickListener { onAction() }
            }
            addView(actionBtn)

            setOnClickListener { onAction() }
        }
    }

    /**
     * Opens the Sleep Data Upload & Review Dialog.
     */
    fun showSleepUploadDialog(
        activity: MainActivity,
        db: StrictRecordsLocalDb,
        isDevMode: Boolean,
        onUpdated: () -> Unit
    ) {
        fun dp(v: Number) = TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), activity.resources.displayMetrics
        ).toInt()

        val date = db.nowDate()
        val scroll = ScrollView(activity)
        val root = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(16))
            setBackgroundColor(Color.parseColor("#0D1117"))
        }
        scroll.addView(root)

        val titleTv = TextView(activity).apply {
            text = "🌙 Mi Fitness Sleep Data Bonus"
            textSize = 16f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(Color.WHITE)
        }
        root.addView(titleTv)

        val subtitleTv = TextView(activity).apply {
            text = "Report Date: $date • Award up to 5 points"
            textSize = 11.5f
            setTextColor(Color.parseColor("#8B949E"))
            setPadding(0, dp(2), 0, dp(12))
        }
        root.addView(subtitleTv)

        // Existing submission check
        val existing = db.getSleepSubmission(date, isDevMode)
        val telemetry = getSleepCardTelemetry(db, date, isDevMode)

        // Policy Explainer Card
        val policyCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(10), dp(12), dp(10))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#161B22"))
                cornerRadius = dp(10).toFloat()
                setStroke(dp(1), Color.parseColor("#30363D"))
            }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(12)
            }
        }
        policyCard.addView(TextView(activity).apply {
            text = "SCORING RULES (DETERMINISTIC):"
            textSize = 10.5f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor("#58A6FF"))
        })
        policyCard.addView(TextView(activity).apply {
            text = "• Sleep onset before midnight (e.g. 11:48 PM) + wake by 9:30 AM = 5 points\n" +
                "• Sleep onset after midnight = -1 point per hour entered (12:30 AM = 4 pts, 1:30 AM = 3 pts, 2:30 AM = 2 pts)\n" +
                "• Wake-up after 9:30 AM = 0 points\n" +
                "• Upload deadline: strictly 12:00 PM on report date\n" +
                "• Independent bonus: does not replace mandatory 3-step wake-up checks"
            textSize = 10f
            setTextColor(Color.parseColor("#C9D1D9"))
            setPadding(0, dp(4), 0, 0)
        })
        root.addView(policyCard)

        // Status / Submission Card
        val statusCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(10), dp(12), dp(10))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#161B22"))
                cornerRadius = dp(10).toFloat()
                setStroke(dp(1), Color.parseColor(telemetry.statusColorHex))
            }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(12)
            }
        }
        statusCard.addView(TextView(activity).apply {
            text = "CURRENT SUBMISSION STATUS: ${telemetry.statusLabel.uppercase(Locale.US)}"
            textSize = 11f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor(telemetry.statusColorHex))
        })
        statusCard.addView(TextView(activity).apply {
            text = telemetry.summaryLine
            textSize = 10.5f
            setTextColor(Color.parseColor("#8B949E"))
            setPadding(0, dp(3), 0, 0)
        })
        if (existing != null) {
            statusCard.addView(TextView(activity).apply {
                val pts = existing.optInt("points_awarded", 0)
                val rawScore = if (existing.isNull("sleep_score")) null else existing.optInt("sleep_score", -1).takeIf { it > 0 }
                val scorePart = if (rawScore != null) "   •   Sleep report score: $rawScore" else ""
                val bonusLine = when (existing.optString("status", "")) {
                    "APPROVED", "PARTIALLY_AWARDED", "ALREADY_AWARDED" -> "StrictRecords bonus: +$pts point${if (pts == 1) "" else "s"}"
                    ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION,
                    ProofSubmissionRules.States.EVALUATING -> "StrictRecords bonus: Pending evaluation"
                    else -> if (pts > 0) "StrictRecords bonus: +$pts points" else "StrictRecords bonus: 0 points"
                }
                text = "Sleep Onset: ${existing.optString("sleep_onset", "--")}   •   Wake: ${existing.optString("wake_up", "--")}\n" +
                    "Duration: ${existing.optString("duration_text", "--")}$scorePart\n" +
                    bonusLine
                textSize = 10f
                typeface = android.graphics.Typeface.MONOSPACE
                setTextColor(Color.parseColor("#58A6FF"))
                setPadding(0, dp(4), 0, 0)
            })
        }
        root.addView(statusCard)

        val dialog = AlertDialog.Builder(activity)
            .setView(scroll)
            .setCancelable(true)
            .create()

        val pendingEval = db.getProofEvaluationByActivity("act_sleep_$date", date, isDevMode)
        val hasExistingSubmission = existing != null || pendingEval != null || telemetry.isCompleted || telemetry.status == "EVALUATING"

        if (hasExistingSubmission) {
            val lockedCard = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(12), dp(10), dp(12), dp(10))
                background = GradientDrawable().apply {
                    setColor(Color.parseColor("#161B22"))
                    cornerRadius = dp(8).toFloat()
                    setStroke(dp(1), Color.parseColor("#30363D"))
                }
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    bottomMargin = dp(8)
                }
                val lockMsg = when (telemetry.status) {
                    "EVALUATING" -> "⏳ Sleep report submitted — verification pending. Submission locked."
                    "APPROVED", "PARTIALLY_AWARDED" -> "✅ Sleep report verified & bonus awarded. Additional submissions are closed."
                    "REJECTED" -> "🔒 Sleep report evaluated for this date. Additional screenshot submissions are closed."
                    else -> "🔒 Sleep report already evaluated for this date. Additional screenshot submissions are closed."
                }
                addView(TextView(activity).apply {
                    text = lockMsg
                    setTextColor(Color.parseColor("#8B949E"))
                    textSize = 11.5f
                    typeface = android.graphics.Typeface.DEFAULT_BOLD
                    gravity = Gravity.CENTER
                })
            }
            root.addView(lockedCard)
        } else if (telemetry.isDeadlinePassed) {
            val deadlineCard = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(12), dp(10), dp(12), dp(10))
                background = GradientDrawable().apply {
                    setColor(Color.parseColor("#161B22"))
                    cornerRadius = dp(8).toFloat()
                    setStroke(dp(1), Color.parseColor("#30363D"))
                }
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    bottomMargin = dp(8)
                }
                addView(TextView(activity).apply {
                    text = "⏰ 12:00 PM deadline has passed. New submissions for this date cannot be accepted."
                    setTextColor(Color.parseColor("#8B949E"))
                    textSize = 11.5f
                    typeface = android.graphics.Typeface.DEFAULT_BOLD
                    gravity = Gravity.CENTER
                })
            }
            root.addView(deadlineCard)
        } else {
            // Launch screenshot picker for immediate acceptance & background evaluation
            val selectScreenshotBtn = Button(activity).apply {
                text = "📁 SELECT MI FITNESS SCREENSHOT"
                setTextColor(Color.WHITE)
                textSize = 11.5f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                background = GradientDrawable().apply {
                    setColor(Color.parseColor("#21262D"))
                    cornerRadius = dp(8).toFloat()
                    setStroke(dp(1), Color.parseColor("#30363D"))
                }
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    bottomMargin = dp(8)
                }
                setOnClickListener {
                    activity.pickSleepScreenshot { uris ->
                        if (uris.isEmpty()) return@pickSleepScreenshot
                        if (uris.size > 1) {
                            Toast.makeText(activity, "Please submit only one sleep-report screenshot.", Toast.LENGTH_LONG).show()
                            return@pickSleepScreenshot
                        }
                        val receipt = ProofEvaluationManager.submitSleepReport(
                            context = activity,
                            db = db,
                            uris = uris,
                            date = date,
                            isDevMode = isDevMode
                        )
                        if (!receipt.success) {
                            Toast.makeText(activity, receipt.message, Toast.LENGTH_LONG).show()
                            return@pickSleepScreenshot
                        }

                        Toast.makeText(activity, receipt.message, Toast.LENGTH_LONG).show()
                        dialog.dismiss()
                        onUpdated()
                    }
                }
            }
            root.addView(selectScreenshotBtn)
        }

        val closeBtn = Button(activity).apply {
            text = "CLOSE"
            setTextColor(Color.parseColor("#94A3B8"))
            textSize = 11f
            background = GradientDrawable().apply {
                cornerRadius = dp(8).toFloat()
                setColor(Color.parseColor("#161B22"))
            }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            setOnClickListener { dialog.dismiss() }
        }
        root.addView(closeBtn)

        dialog.show()
    }

    /**
     * Diagnostic Demo Test for Mi Fitness Sleep Data.
     * Evaluates screenshots via OCR & deterministic scoring without applying the 12:00 PM deadline,
     * showing the user exactly what points the algorithm gives and the complete calculation breakdown.
     * Zero score impact.
     */
    fun showSleepDemoTestDialog(
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
            setPadding(dp(16), dp(16), dp(16), dp(16))
            setBackgroundColor(Color.parseColor("#0D1117"))
        }
        scroll.addView(root)

        val titleTv = TextView(activity).apply {
            text = "🌙 Demo Test: Mi Sleep Algorithm"
            textSize = 16f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(Color.WHITE)
        }
        root.addView(titleTv)

        val subtitleTv = TextView(activity).apply {
            text = "Diagnostic Mode • Zero Score Impact • Any Time of Day"
            textSize = 11.5f
            setTextColor(Color.parseColor("#8B949E"))
            setPadding(0, dp(2), 0, dp(12))
        }
        root.addView(subtitleTv)

        // Explainer banner
        val infoBanner = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(10), dp(12), dp(10))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#161B22"))
                cornerRadius = dp(10).toFloat()
                setStroke(dp(1), Color.parseColor("#388BFD"))
            }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(12)
            }
        }
        infoBanner.addView(TextView(activity).apply {
            text = "ℹ️ ALGORITHM VERIFICATION SANDBOX"
            textSize = 11f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor("#58A6FF"))
        })
        infoBanner.addView(TextView(activity).apply {
            text = "Upload any Mi Fitness sleep report screenshot at any time to test:\n" +
                "1. OCR detection of Report Date, Sleep Onset & Wake-Up times.\n" +
                "2. Verification of duration, sleep stages & device source.\n" +
                "3. Deterministic point calculation (0 to 5 points) without deadline restrictions.\n" +
                "🔒 Nothing is saved to your score or database."
            textSize = 10f
            setTextColor(Color.parseColor("#C9D1D9"))
            setPadding(0, dp(4), 0, 0)
        })
        root.addView(infoBanner)

        // Progress container
        val progressContainer = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            visibility = View.GONE
            setPadding(0, dp(8), 0, dp(8))
        }
        val progressBar = ProgressBar(activity).apply {
            layoutParams = LinearLayout.LayoutParams(dp(22), dp(22)).apply { rightMargin = dp(10) }
        }
        val progressText = TextView(activity).apply {
            text = "Analyzing screenshot via OCR..."
            textSize = 11f
            setTextColor(Color.parseColor("#7EE787"))
        }
        progressContainer.addView(progressBar)
        progressContainer.addView(progressText)
        root.addView(progressContainer)

        // Results Card
        val resultsCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            setPadding(dp(12), dp(12), dp(12), dp(12))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#161B22"))
                cornerRadius = dp(10).toFloat()
                setStroke(dp(1), Color.parseColor("#30363D"))
            }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(12)
            }
        }

        // Status badge
        val badgeTv = TextView(activity).apply {
            textSize = 12f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setPadding(dp(10), dp(8), dp(10), dp(8))
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(10)
            }
        }
        resultsCard.addView(badgeTv)

        val resultsDetailsTv = TextView(activity).apply {
            textSize = 10.5f
            typeface = android.graphics.Typeface.MONOSPACE
            setTextColor(Color.WHITE)
        }
        resultsCard.addView(resultsDetailsTv)
        root.addView(resultsCard)

        val dialog = AlertDialog.Builder(activity)
            .setView(scroll)
            .setCancelable(true)
            .create()

        // Select Screenshot Button
        val selectBtn = Button(activity).apply {
            text = "📁 SELECT MI FITNESS SCREENSHOT TO TEST"
            setTextColor(Color.WHITE)
            textSize = 11.5f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#238636"))
                cornerRadius = dp(8).toFloat()
            }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(8)
            }
            setOnClickListener {
                activity.pickSleepScreenshot { uris ->
                    if (uris.isEmpty()) return@pickSleepScreenshot
                    if (uris.size > 1) {
                        Toast.makeText(activity, "Please submit only one sleep-report screenshot.", Toast.LENGTH_LONG).show()
                        return@pickSleepScreenshot
                    }
                    progressContainer.visibility = View.VISIBLE
                    resultsCard.visibility = View.GONE
                    progressText.text = "Reading & analyzing screenshot..."

                    val testTimestamp = System.currentTimeMillis()
                    bgExecutor.execute {
                        try {
                            val uri = uris.first()
                            val analysis = EvidenceAnalysisEngine.analyzeUri(
                                activity.applicationContext,
                                uri,
                                attachmentId = "sleep_demo_${System.currentTimeMillis()}",
                                taskId = "sleep_demo_$date",
                                batchId = "sleep_demo_batch"
                            )
                            val ocr = analysis.optJSONObject("ocr")
                            val text = ocr?.optString("text") ?: ""
                            val blocks = ocr?.optJSONArray("blocks") ?: JSONArray()
                            val primarySha256 = analysis.optJSONObject("original")?.optString("sha256") ?: ""

                            val validationResult = MiFitnessRegionValidator.validate(
                                ocrText = text,
                                blocksJson = blocks,
                                defaultYear = date.split("-").firstOrNull()?.toIntOrNull() ?: 2026,
                                sourceImageId = "sleep_demo_$date",
                                sourceIntegrityHash = primarySha256
                            )
                            val parsed = validationResult.toParsedSleepData()

                            // For demo mode, evaluate with ignoreDeadline = true
                            val claimedDate = parsed.reportDate ?: date
                            val evaluation = MiFitnessSleepBonusRules.evaluateSleepBonus(
                                extractedDate = parsed.reportDate,
                                onset = parsed.sleepOnset,
                                wake = parsed.wakeUp,
                                claimedDate = claimedDate,
                                submissionTimestampMs = testTimestamp,
                                timezone = db.scheduleTimezone(),
                                isDuplicate = false,
                                alreadyAwarded = false,
                                ignoreDeadline = true,
                                reconstructedWakeDate = validationResult.reconstructedWakeUpDateTime?.substringBefore("T"),
                                isConsistent = !parsed.isAmbiguous && (validationResult.displayedDurationMinutes == null ||
                                        validationResult.calculatedDurationMinutes == null ||
                                        validationResult.displayedDurationMinutes <= validationResult.calculatedDurationMinutes)
                            )

                            val isDeadlineActuallyPassed = MiFitnessSleepBonusRules.isDeadlinePassed(
                                testTimestamp, claimedDate, db.scheduleTimezone()
                            )

                            mainHandler.post {
                                progressContainer.visibility = View.GONE
                                resultsCard.visibility = View.VISIBLE

                                val pts = evaluation.points
                                val isEligible = evaluation.eligible && pts > 0
                                if (isEligible) {
                                    badgeTv.text = "✅ ALGORITHM AWARDS +$pts POINTS"
                                    badgeTv.setTextColor(Color.WHITE)
                                    badgeTv.background = GradientDrawable().apply {
                                        setColor(Color.parseColor("#238636"))
                                        cornerRadius = dp(6).toFloat()
                                    }
                                } else {
                                    badgeTv.text = "❌ ALGORITHM AWARDS 0 POINTS (${evaluation.status})"
                                    badgeTv.setTextColor(Color.WHITE)
                                    badgeTv.background = GradientDrawable().apply {
                                        setColor(Color.parseColor("#DA3633"))
                                        cornerRadius = dp(6).toFloat()
                                    }
                                }

                                val onsetDisp = parsed.sleepOnset?.let {
                                    val timing = if (it.isPreviousDay) "Previous day (before midnight)" else "After midnight"
                                    "${it.formatted12h} ($timing)"
                                } ?: "⚠️ NOT DETECTED"

                                val wakeDisp = parsed.wakeUp?.let {
                                    val onTime = if (it.totalMinutesSinceMidnight <= MiFitnessSleepBonusRules.WAKE_CUTOFF_TOTAL_MINUTES) "On time (<= 09:30 AM)" else "LATE (> 09:30 AM cutoff)"
                                    "${it.formatted12h} ($onTime)"
                                } ?: "⚠️ NOT DETECTED"

                                val sb = StringBuilder()
                                sb.append("EXTRACTED REPORT DATA:\n")
                                sb.append("• Report Date: ${validationResult.reportDate ?: "⚠️ Not detected"}\n")
                                sb.append("• Sleep Onset: $onsetDisp\n")
                                sb.append("• Wake-Up Time: $wakeDisp\n")
                                if (validationResult.displayedDurationMinutes != null) {
                                    val dispText = validationResult.displayedDurationText ?: "${validationResult.displayedDurationMinutes / 60}h ${validationResult.displayedDurationMinutes % 60}m"
                                    sb.append("• Displayed Duration: $dispText (${validationResult.displayedDurationMinutes} mins)\n")
                                }
                                if (validationResult.calculatedDurationMinutes != null) {
                                    sb.append("• Calculated Duration: ${validationResult.calculatedDurationMinutes} mins\n")
                                }
                                if (validationResult.sleepScore != null) {
                                    sb.append("• Sleep Score: ${validationResult.sleepScore}\n")
                                }
                                if (validationResult.sourceLabel != null) {
                                    sb.append("• Device Source: ${validationResult.sourceLabel}\n")
                                }

                                sb.append("\nSCORING BREAKDOWN:\n")
                                sb.append("• Base Award: 5 points maximum\n")
                                if (parsed.sleepOnset != null) {
                                    if (parsed.sleepOnset.isPreviousDay) {
                                        sb.append("• Sleep Onset Rule: Before midnight -> 0 penalty (Full 5 pts eligible)\n")
                                    } else {
                                        val lateBands = evaluation.lateHourBands
                                        sb.append("• Sleep Onset Rule: After midnight at ${parsed.sleepOnset.formatted12h} -> $lateBands hour band(s) late (-$lateBands pts)\n")
                                    }
                                }
                                if (parsed.wakeUp != null) {
                                    val wakeMin = parsed.wakeUp.totalMinutesSinceMidnight
                                    if (wakeMin <= MiFitnessSleepBonusRules.WAKE_CUTOFF_TOTAL_MINUTES) {
                                        sb.append("• Wake-Up Rule: ${parsed.wakeUp.formatted12h} is <= 9:30 AM -> Pass\n")
                                    } else {
                                        sb.append("• Wake-Up Rule: ${parsed.wakeUp.formatted12h} is AFTER 9:30 AM cutoff -> 0 points\n")
                                    }
                                }
                                sb.append("• Calculated Algorithm Award: +$pts points\n")
                                sb.append("• Status Reason: ${evaluation.reason}\n")

                                sb.append("\nDEADLINE TIMING CHECK:\n")
                                if (isDeadlineActuallyPassed) {
                                    sb.append("⚠️ Currently PAST 12:00 PM: In regular production mode, upload must be completed before 12:00 PM noon on the report date.\n")
                                    sb.append("💡 If submitted before 12:00 PM, you will receive: +$pts points!\n")
                                } else {
                                    sb.append("✅ Currently BEFORE 12:00 PM: Submitting this in the main Sleep card now will award +$pts points!\n")
                                }

                                resultsDetailsTv.text = sb.toString()
                            }
                        } catch (e: Exception) {
                            mainHandler.post {
                                progressContainer.visibility = View.GONE
                                Toast.makeText(activity, "Error analyzing screenshot: ${e.message}", Toast.LENGTH_LONG).show()
                            }
                        }
                    }
                }
            }
        }
        root.addView(selectBtn)

        val closeBtn = Button(activity).apply {
            text = "CLOSE DEMO"
            setTextColor(Color.parseColor("#94A3B8"))
            textSize = 11f
            background = GradientDrawable().apply {
                cornerRadius = dp(8).toFloat()
                setColor(Color.parseColor("#161B22"))
            }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            setOnClickListener { dialog.dismiss() }
        }
        root.addView(closeBtn)

        dialog.show()
    }
}
