package com.strictrecords.app

import android.app.Activity
import android.app.Dialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.GradientDrawable
import android.text.SpannableString
import android.text.style.ForegroundColorSpan
import android.view.Gravity
import android.view.View
import android.widget.*
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone
import java.util.WeakHashMap
import java.util.concurrent.Executors

/** Native, read-only receipt. No approval/score writers or synthetic evidence. */
internal class ProofApprovalReceiptDialog private constructor(
    private val host: Activity, private val db: StrictRecordsLocalDb, private val selected: JSONObject
) {
    companion object {
        private val open = WeakHashMap<Activity, Dialog>()
        private val images = object : android.util.LruCache<String, ByteArray>(12 * 1024 * 1024) {
            override fun sizeOf(key: String, value: ByteArray) = value.size
        }
        fun show(host: Activity, db: StrictRecordsLocalDb, event: JSONObject) {
            open[host]?.dismiss()
            ProofApprovalReceiptDialog(host, db, event).show()
        }
    }
    private val dialog = Dialog(host)
    private val io = Executors.newSingleThreadExecutor { r -> Thread(r, "ProofReceiptRead").apply { isDaemon = true } }
    private val body = column()
    private var viewer: Dialog? = null
    private val foreground = Color.rgb(238, 240, 247)
    private val muted = Color.rgb(170, 177, 194)
    private val green = Color.rgb(60, 215, 139)
    private val amber = Color.rgb(255, 196, 80)
    private fun dp(n: Int) = (n * host.resources.displayMetrics.density).toInt()
    private fun column() = LinearLayout(host).apply { orientation = LinearLayout.VERTICAL }
    private fun surface(color: Int) = GradientDrawable().apply {
        setColor(color); cornerRadius = dp(16).toFloat(); setStroke(dp(1), Color.rgb(66, 71, 89))
    }
    private fun text(value: String, size: Float = 14f, color: Int = foreground, bold: Boolean = false) =
        TextView(host).apply {
            text = value; textSize = size; setTextColor(color)
            if (bold) setTypeface(typeface, Typeface.BOLD)
            setPadding(0, dp(3), 0, dp(3)); setTextIsSelectable(true)
        }
    private fun button(label: String, action: () -> Unit) = TextView(host).apply {
        text = label; textSize = 14f; setTextColor(this@ProofApprovalReceiptDialog.foreground); gravity = Gravity.CENTER
        minHeight = dp(48); setPadding(dp(12), dp(8), dp(12), dp(8))
        background = surface(Color.rgb(84, 49, 194))
        isClickable = true; isFocusable = true; contentDescription = label
        setOnClickListener { action() }
    }
    private fun card(title: String, target: LinearLayout = body): LinearLayout = column().apply {
        setPadding(dp(14), dp(10), dp(14), dp(12))
        background = surface(Color.rgb(29, 32, 44))
        layoutParams = LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(12) }
        addView(text(title, 16f, bold = true)); target.addView(this)
    }
    private fun copy(label: String, value: String) {
        val copied = runCatching {
            (host.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
                .setPrimaryClip(ClipData.newPlainText(label, value))
        }.isSuccess
        Toast.makeText(host, if (copied) label + " copied" else
            "The complete value could not be copied to the system clipboard.", Toast.LENGTH_SHORT).show()
    }
    private fun show() {
        val root = column().apply {
            setPadding(dp(14), dp(12), dp(14), dp(12)); background = surface(Color.rgb(16, 19, 29))
        }
        val top = LinearLayout(host).apply { gravity = Gravity.CENTER_VERTICAL }
        top.addView(text("Proof receipt", 19f, bold = true), LinearLayout.LayoutParams(0, -2, 1f))
        top.addView(button("Close ×") { dialog.dismiss() }); root.addView(top)
        root.addView(ScrollView(host).apply { isFillViewport = true; addView(body) }, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(button("Close") { dialog.dismiss() }, LinearLayout.LayoutParams(-1, dp(48)))
        body.addView(text("Loading approval, evidence metadata and points ledger…", color = muted))
        dialog.setContentView(root)
        dialog.setOnDismissListener {
            viewer?.dismiss(); io.shutdownNow()
            if (open[host] === dialog) open.remove(host)
        }
        open[host] = dialog; dialog.show()
        dialog.window?.apply {
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            val metrics = host.resources.displayMetrics
            setLayout(minOf(metrics.widthPixels - dp(20), dp(980)), (metrics.heightPixels * .9f).toInt())
        }
        io.execute {
            val result = runCatching { ProofApprovalReceiptRepository(db).load(selected) }
            host.runOnUiThread {
                if (!dialog.isShowing || host.isFinishing) return@runOnUiThread
                result.fold({ render(it) }, {
                    body.removeAllViews()
                    body.addView(text("Receipt unavailable. The stored event could not be loaded.", color = amber))
                    technical(selected)
                })
            }
        }
    }
    private fun render(data: ProofApprovalReceiptData) {
        body.removeAllViews()
        val event = data.event
        val rejected = event.optString("event_type").contains("REJECT")
        body.addView(text(when {
            data.approved -> "✓  PROOF APPROVED"
            !data.authoritative -> "Receipt unavailable"
            rejected -> "Proof rejected"
            else -> "Proof review pending"
        }, 21f, if (data.approved) green else amber, true))
        body.addView(text(when {
            data.approved -> "Your submission has been reviewed and approved."
            rejected -> "Your submission was rejected. Review the recorded findings below."
            else -> "Approval and points have not been confirmed."
        }, color = muted))
        card("Points award").apply {
            val points = event.opt("points")?.takeUnless { it == JSONObject.NULL }
            addView(text(if (points == null) "Amount unavailable" else
                (if (event.optInt("points") > 0) "+" else "") + points + " points",
                22f, if (data.credited) amber else muted, true))
            addView(text(data.ledgerMessage, color = if (data.credited) green else amber))
        }
        val split = LinearLayout(host).apply {
            orientation = if (host.resources.configuration.screenWidthDp >= 720) LinearLayout.HORIZONTAL else LinearLayout.VERTICAL
        }
        body.addView(split)
        val left = column(); val right = column()
        if (split.orientation == LinearLayout.HORIZONTAL) {
            split.addView(left, LinearLayout.LayoutParams(0, -2, 1f).apply { rightMargin = dp(6) })
            split.addView(right, LinearLayout.LayoutParams(0, -2, 1f).apply { leftMargin = dp(6) })
        } else { split.addView(left); split.addView(right) }
        val evidenceCard = card("Submitted Evidence", left)
        val placeholder = text("Loading evidence…", color = muted)
        evidenceCard.addView(placeholder)
        evidenceCard.addView(text("Evidence status: " + (data.evidence?.optString("status")?.ifBlank { "Unavailable" } ?: "Unavailable"), color = muted))
        val details = card("Approval details", right)
        fun value(key: String) = data.value(key).ifBlank { data.evidence?.optString(key).orEmpty() }
        for ((label, key) in listOf("Evidence ID" to "evidence_id", "Submission ID" to "submission_id", "Activity ID" to "activity_id")) {
            val id = value(key)
            val row = LinearLayout(host).apply { gravity = Gravity.CENTER_VERTICAL }
            row.addView(text(label + "\n" + id.ifBlank { "Unavailable" }, 12f),
                LinearLayout.LayoutParams(0, -2, 1f))
            if (id.isNotBlank()) row.addView(button("Copy") { copy(label, id) }.apply {
                contentDescription = "Copy " + label
            }, LinearLayout.LayoutParams(dp(64), dp(48)).apply { leftMargin = dp(8) })
            details.addView(row)
        }
        details.addView(text("Task Code: " + value("task_code").ifBlank { "Unavailable" }, 12f))
        details.addView(text("Category: " + value("category").ifBlank { "Unavailable" }, 12f))
        details.addView(text("Device: " + data.value("Device", "device", "source_device_name").ifBlank { "Unavailable" }, 12f))
        val timestamp = event.optString("timestamp").ifBlank { data.value("Timestamp") }
        details.addView(text("Timestamp: " + displayTime(timestamp, data.timezone) + "\nTimezone: " + data.timezone, 12f))
        val activityDetails = runCatching { JSONObject(data.activity?.optString("details_json", "{}") ?: "{}") }.getOrNull()
        activityDetails?.optString("description")?.takeIf { it.isNotBlank() }?.let {
            card("Activity description").addView(text(it))
        }
        val ai = data.ai
        card("AI Validation – Taufa AI").apply {
            val score = ai?.opt("score")?.takeUnless { it == JSONObject.NULL }
            val max = ai?.opt("max_score")?.takeUnless { it == JSONObject.NULL }
            addView(text(if (score == null) "Score unavailable" else "Score: " + score + (if (max != null) " / " + max else ""), 17f, bold = true))
            for ((label, key) in listOf("Qualifies for Points" to "qualifies_for_points", "Category Match" to "category_match",
                "Evidence Match" to "evidence_match", "Description Match" to "description_match")) {
                val raw = ai?.opt(key)
                val state = when (raw) {
                    true -> "YES"; false -> "NO"
                    is String -> when (raw.lowercase()) { "true", "yes" -> "YES"; "false", "no" -> "NO"; else -> raw }
                    else -> "Unavailable"
                }
                addView(text((if (state == "YES") "✓  " else "•  ") + label + ": " + state,
                    color = if (state == "YES") green else if (state == "NO") amber else muted))
            }
            addView(text("Validator Status: " + (ai?.optString("validator_status")?.ifBlank { "Unavailable" } ?: "Unavailable")))
            ai?.optString("reason")?.takeIf { it.isNotBlank() }?.let { addView(text(it, color = muted)) }
        }
        card("Audit provenance").apply {
            for ((label, keys) in listOf("Action" to arrayOf("action"), "Reviewed By" to arrayOf("reviewed_by"),
                "Approval Source" to arrayOf("approval_source"), "Admin" to arrayOf("Admin", "admin", "approved_by"))) {
                addView(text(label + ": " + data.value(*keys).ifBlank { "Unavailable" }, 13f))
            }
            if (data.provenanceWarning) addView(text("Audit metadata requires review", color = amber))
        }
        technical(event)
        io.execute {
            val bitmap = runCatching {
                evidenceBytes(data)?.let {
                    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                    BitmapFactory.decodeByteArray(it, 0, it.size, bounds)
                    var sample = 1
                    while (bounds.outWidth / sample > 2048 || bounds.outHeight / sample > 2048) sample *= 2
                    BitmapFactory.decodeByteArray(it, 0, it.size, BitmapFactory.Options().apply { inSampleSize = sample })
                }
            }.getOrNull()
            host.runOnUiThread {
                if (!dialog.isShowing) return@runOnUiThread
                placeholder.text = if (bitmap == null) "Evidence attachment unavailable" else "Tap to expand submitted evidence"
                if (bitmap != null) {
                    val image = ImageView(host).apply {
                        setImageBitmap(bitmap); scaleType = ImageView.ScaleType.FIT_CENTER
                        contentDescription = "Submitted evidence. Tap to expand."
                        setOnClickListener {
                            viewer = Dialog(host).apply {
                                val container = column()
                                container.addView(ImageView(host).apply {
                                    setImageBitmap(bitmap); scaleType = ImageView.ScaleType.FIT_CENTER
                                }, LinearLayout.LayoutParams(-1, 0, 1f))
                                container.addView(button("Close evidence") { dismiss() })
                                setContentView(container); show()
                                window?.setLayout(-1, (host.resources.displayMetrics.heightPixels * .9f).toInt())
                            }
                        }
                    }
                    evidenceCard.addView(image, 1, LinearLayout.LayoutParams(-1, dp(200)))
                }
            }
        }
    }
    private fun technical(event: JSONObject) {
        val panel = card("Technical Audit Data")
        val holder = column().apply { visibility = View.GONE }
        panel.addView(button("Expand / collapse technical data") {
            if (holder.visibility == View.VISIBLE) { holder.visibility = View.GONE } else {
                holder.visibility = View.VISIBLE
                if (holder.childCount == 0) {
                    holder.addView(text("Formatting complete event…", color = muted))
                    io.execute {
                        val raw = event.toString(2)
                        val tokens = Regex("\"(?:\\\\.|[^\"\\\\])*\"\\s*:|\"(?:\\\\.|[^\"\\\\])*\"|\\b(?:true|false|null|-?\\d+(?:\\.\\d+)?)\\b")
                        val lines = raw.lineSequence().map { line ->
                            SpannableString(line).apply {
                                tokens.findAll(line).forEach { match ->
                                    setSpan(ForegroundColorSpan(
                                        if (match.value.trimEnd().endsWith(":")) Color.rgb(185, 155, 255) else Color.rgb(115, 211, 178)),
                                        match.range.first, match.range.last + 1, 0)
                                }
                            }
                        }.toList()
                        host.runOnUiThread {
                            if (!dialog.isShowing) return@runOnUiThread
                            holder.removeAllViews()
                            val code = ListView(host).apply {
                                divider = null
                                adapter = object : BaseAdapter() {
                                    override fun getCount() = lines.size
                                    override fun getItem(position: Int) = lines[position]
                                    override fun getItemId(position: Int) = position.toLong()
                                    override fun getView(position: Int, recycled: View?, parent: android.view.ViewGroup): View =
                                        ((recycled as? TextView) ?: text("", 11f).apply {
                                            typeface = Typeface.MONOSPACE; setHorizontallyScrolling(false)
                                        }).apply { text = lines[position] }
                                }
                            }
                            holder.addView(code, LinearLayout.LayoutParams(-1, dp(240)))
                            holder.addView(button("Copy complete JSON") { copy("Technical audit", raw) })
                        }
                    }
                }
            }
        })
        panel.addView(holder)
    }
    private fun displayTime(raw: String, zone: String): String = runCatching {
        val input = raw.replace('T', ' ')
        val pattern = when {
            input.endsWith("Z") || Regex("[+-]\\d\\d:\\d\\d$").containsMatchIn(input) ->
                if (input.contains('.')) "yyyy-MM-dd HH:mm:ss.SSSXXX" else "yyyy-MM-dd HH:mm:ssXXX"
            else -> "yyyy-MM-dd HH:mm:ss"
        }
        val source = SimpleDateFormat(pattern, Locale.US).apply { timeZone = TimeZone.getTimeZone(zone); isLenient = false }
        val date = source.parse(input) ?: error("Invalid timestamp")
        SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).apply { timeZone = TimeZone.getTimeZone(zone) }.format(date)
    }.getOrDefault(raw.ifBlank { "Unavailable" })

    private fun evidenceBytes(data: ProofApprovalReceiptData): ByteArray? {
        val evidence = data.evidence
        val id = evidence?.optString("evidence_id").orEmpty().ifBlank { data.value("evidence_id") }
        if (id.isBlank()) return null
        val registration = db.getDeviceRegistration()
        val credentialHash = MessageDigest.getInstance("SHA-256").digest(registration?.optString("device_token").orEmpty().toByteArray())
            .joinToString("") { "%02x".format(it) }
        val key = registration?.optString("account_id").orEmpty() + "|" + credentialHash + "|" + id + "|" + evidence?.optString("sha256_hash").orEmpty()
        synchronized(images) { images.get(key) }?.let { return it }
        val local = evidence?.optString("file_path")?.takeIf { it.isNotBlank() }?.let { File(it).canonicalFile }
        val privateRoots = listOf(host.filesDir.canonicalFile, host.cacheDir.canonicalFile)
        val bytes = if (local != null && privateRoots.any { local.path.startsWith(it.path + File.separator) } &&
            local.isFile && local.length() in 1..(10L * 1024 * 1024)) local.readBytes() else {
            if (registration == null) return null
            val endpoint = registration.optString("server_endpoint").trimEnd('/')
            if (!endpoint.startsWith("http://") && !endpoint.startsWith("https://")) return null
            fun encoded(s: String) = URLEncoder.encode(s, "UTF-8")
            val connection = URL(endpoint + "/api/device/proof_evidence?device_id=" +
                encoded(registration.optString("device_id")) + "&evidence_id=" + encoded(id)).openConnection() as HttpURLConnection
            try {
                connection.instanceFollowRedirects = false
                connection.connectTimeout = 8000; connection.readTimeout = 12000
                connection.setRequestProperty("Authorization", "Bearer " + registration.optString("device_token"))
                if (connection.responseCode != 200 || !connection.contentType.orEmpty().startsWith("image/")) return null
                connection.inputStream.use { input ->
                    val out = java.io.ByteArrayOutputStream()
                    val buffer = ByteArray(8192)
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        if (out.size() + count > 10 * 1024 * 1024) return null
                        out.write(buffer, 0, count)
                    }
                    out.toByteArray()
                }
            } finally { connection.disconnect() }
        }
        val expected = evidence?.optString("sha256_hash").orEmpty()
        if (expected.isNotBlank()) {
            val actual = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
            if (!actual.equals(expected, true)) return null
        }
        synchronized(images) { images.put(key, bytes) }
        return bytes
    }
}
