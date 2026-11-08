package com.strictrecords.app

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.text.Editable
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.EditText
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import java.util.UUID
import java.util.concurrent.Executors

/** Chat uses existing authenticated services. It neither schedules tasks nor grants local admin roles. */
class StrictBotChatController(
    private val activity: Activity,
    private val db: StrictRecordsLocalDb,
    private val onNavigate: (String) -> Unit,
    private val onConfirmedOperation: () -> Unit,
    private val onMotion: (String) -> Unit,
    private val onTypography: (ViewGroup) -> Unit
) {
    companion object { const val MICROPHONE_REQUEST = 890 }

    private val client = StrictBotClient(activity.applicationContext, db)
    private val ui = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private val preferences = activity.getSharedPreferences("strictbot_chat", Activity.MODE_PRIVATE)
    private var scope = ""
    private var conversationId = ""
    private var messages = JSONArray()
    private var pending: JSONObject? = null
    private var actions = JSONArray()
    private var contextActions = JSONArray()
    private var proposals = JSONArray()
    private var dialog: AlertDialog? = null
    private var body: LinearLayout? = null
    private var transcript: LinearLayout? = null
    private var contextLabel: TextView? = null
    private var statusLabel: TextView? = null
    private var modeLabel: TextView? = null
    private var voiceButton: TextView? = null
    private var sendButton: TextView? = null
    private var listenButton: TextView? = null
    private var retryButton: TextView? = null
    private var input: EditText? = null
    private var scroll: ScrollView? = null
    private var expanded = false
    private var busy = false
    private var destroyed = false
    private var refreshQueued = false
    private var lastLocalSignature = ""
    private var lastRefreshAt = 0L
    private var mode = "UNKNOWN"
    private var state = "UNKNOWN"
    private var contextText = "Loading current state…"
    private var contextDetails = JSONObject()
    private var contextTopic = "next"
    private var networkError = ""
    private var speaking = false
    private var listening = false
    private var voiceMuted = preferences.getBoolean("voice_muted", true)
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var ttsStarting = false
    private var queuedSpeech: Pair<String, String>? = null
    private val spokenEvents = LinkedHashSet<String>()
    private var recognizer: SpeechRecognizer? = null
    private var recognitionActive = false
    private var currentUtteranceId: String? = null
    private var speechGeneration = 0

    val isVisible: Boolean get() = dialog?.isShowing == true

    fun show(prefill: String? = null) {
        if (destroyed) return
        ensureScope()
        if (dialog?.isShowing == true) {
            renderTranscript(); renderStatus()
            prefill?.let { input?.setText(it); input?.setSelection(it.length) }
            return
        }
        val panel = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(12), dp(14), dp(14))
            background = background(MainActivity.CLR_MODAL, MainActivity.CLR_BORDER_FOCUS)
        }
        body = panel
        val top = row()
        top.addView(ImageView(activity).apply {
            setImageResource(R.drawable.strict_bot_mascot)
            scaleType = ImageView.ScaleType.FIT_CENTER
        }, LinearLayout.LayoutParams(dp(28), dp(28)).apply { rightMargin = dp(8) })
        val heading = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        heading.addView(label("STRICTBOT", 14f, MainActivity.CLR_GREEN, true))
        modeLabel = label("Connecting to authenticated PC…", 10f, MainActivity.CLR_TEXT_SECONDARY)
        heading.addView(modeLabel)
        top.addView(heading, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        top.addView(compactButton("✕", MainActivity.CLR_TEXT_SECONDARY, minH = 28, padH = 8, padV = 4, txtSize = 13f) { dialog?.dismiss() })
        panel.addView(top)

        statusLabel = label("Text input is ready", 10.5f, MainActivity.CLR_TEXT_SECONDARY).apply {
            setPadding(0, dp(2), 0, dp(4))
            maxLines = 2; ellipsize = android.text.TextUtils.TruncateAt.END
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        panel.addView(statusLabel)
        val controls = row().apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(4)
            }
        }
        voiceButton = compactButton(if (voiceMuted) "Voice off" else "Voice on", minH = 30, padH = 6, padV = 4, txtSize = 11f) {
            voiceMuted = !voiceMuted
            preferences.edit().putBoolean("voice_muted", voiceMuted).apply()
            if (voiceMuted) stopSpeech()
            else messages.optJSONObject(messages.length() - 1)?.let { latest ->
                if (latest.optString("role") == "assistant") speak(latest.optString("text"), "toggle:${latest.optString("id")}")
            }
            renderStatus()
        }
        controls.addView(voiceButton, weighted(4))
        controls.addView(compactButton("Stop", minH = 30, padH = 6, padV = 4, txtSize = 11f) { stopSpeech(); stopListening(); renderStatus() }, weighted(4))
        controls.addView(compactButton(if (expanded) "Compact" else "Expand", minH = 30, padH = 6, padV = 4, txtSize = 11f) {
            expanded = !expanded
            (controls.getChildAt(2) as? TextView)?.text = if (expanded) "Compact" else "Expand"
            resizeDialog()
        }, weighted(0))
        panel.addView(controls)

        scroll = ScrollView(activity).apply { isFillViewport = false }
        transcript = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        scroll?.addView(transcript)
        panel.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f).apply {
            topMargin = dp(4); bottomMargin = dp(4)
        })

        val suggestedPromptsScroll = HorizontalScrollView(activity).apply {
            isHorizontalScrollBarEnabled = false
            isHorizontalFadingEdgeEnabled = true
            setFadingEdgeLength(dp(16))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(2); bottomMargin = dp(4)
            }
        }
        val quickRow = row()
        listOf(
            "Next task" to "What should I do next?",
            "Today" to "Summarize what I did today.",
            "Check permissions" to "Explain my phone permission status.",
            "Change plan" to null
        ).forEach { (title, message) ->
            quickRow.addView(compactButton(title, accent = MainActivity.CLR_CYAN, minH = 28, padH = 8, padV = 4, txtSize = 10.5f) {
                if (message != null) send(message) else {
                    input?.setText("I need to change my schedule for ")
                    input?.setSelection(input?.text?.length ?: 0)
                    input?.requestFocus()
                }
            }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { rightMargin = dp(6) })
        }
        suggestedPromptsScroll.addView(quickRow)
        panel.addView(suggestedPromptsScroll)

        retryButton = compactButton("⚠️ Retry pending operation", MainActivity.CLR_AMBER, minH = 28, padH = 8, padV = 4, txtSize = 11f) {
            pending?.let { performRequest(it) }
        }.apply {
            visibility = if (pending != null) View.VISIBLE else View.GONE
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(2); bottomMargin = dp(4)
            }
        }
        panel.addView(retryButton)

        // Consolidated, docked input composer row: [ EditText ] [ Mic ] [ Send ]
        val composerRow = row().apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(2)
            }
        }
        input = EditText(activity).apply {
            hint = "Ask StrictBot, or describe a change of plan…"
            textSize = 13f
            setTextColor(Color.parseColor(MainActivity.CLR_TEXT_PRIMARY))
            setHintTextColor(Color.parseColor(MainActivity.CLR_TEXT_DIM))
            setPadding(dp(10), dp(8), dp(10), dp(8))
            background = background(MainActivity.CLR_SURFACE_LOW, MainActivity.CLR_BORDER)
            minLines = 1
            maxLines = 3
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE or android.text.InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            filters = arrayOf(android.text.InputFilter.LengthFilter(2000))
            addTextChangedListener(object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) { if (count > 0 && speaking) stopSpeech() }
                override fun afterTextChanged(s: Editable?) {}
            })
        }
        composerRow.addView(input, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = dp(5) })
        prefill?.let { input?.setText(it); input?.setSelection(it.length) }

        listenButton = compactButton("Mic", MainActivity.CLR_CYAN, minH = 34, padH = 8, padV = 5, txtSize = 11f) {
            startListening()
        }
        composerRow.addView(listenButton, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { rightMargin = dp(4) })

        var lastSendMs = 0L
        sendButton = compactButton("Send", MainActivity.CLR_GREEN, minH = 34, padH = 10, padV = 5, txtSize = 11f) {
            val now = System.currentTimeMillis()
            if (now - lastSendMs < 600L || busy) return@compactButton
            val message = input?.text?.toString()?.trim().orEmpty()
            if (message.isNotBlank()) {
                lastSendMs = now
                input?.setText("")
                send(message)
            }
        }
        composerRow.addView(sendButton, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        panel.addView(composerRow)
        onTypography(panel)
        dialog = AlertDialog.Builder(activity).setView(panel).create().apply {
            setOnDismissListener {
                stopSpeech(); stopListening()
                dialog = null
                transcript = null; input = null; body = null; scroll = null
                statusLabel = null; modeLabel = null; contextLabel = null
            }
            show()
            window?.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            window?.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        }
        resizeDialog()
        renderTranscript()
        renderStatus()
        refreshHistoryAndContext()
    }

    fun dismiss(): Boolean {
        if (dialog?.isShowing != true) return false
        dialog?.dismiss()
        return true
    }

    private fun ensureScope() {
        val current = client.accountScope()
        if (scope == current) return
        stopSpeech(); stopListening()
        scope = current
        val savedScope = preferences.getString("account_scope", "")
        conversationId = if (savedScope == current) preferences.getString("conversation_id", "").orEmpty() else ""
        messages = if (savedScope == current) runCatching { JSONArray(preferences.getString("history", "[]")) }.getOrDefault(JSONArray()) else JSONArray()
        pending = if (savedScope == current) runCatching { JSONObject(preferences.getString("pending", "").orEmpty()) }.getOrNull() else null
        actions = JSONArray(); contextActions = JSONArray(); proposals = JSONArray(); mode = "UNKNOWN"; state = "UNKNOWN"
        contextText = "Loading current state…"; contextDetails = JSONObject(); contextTopic = "next"; networkError = ""
        lastLocalSignature = ""
        persist()
    }

    private fun persist() {
        val recent = JSONArray()
        for (index in maxOf(0, messages.length() - 60) until messages.length()) recent.put(messages.optJSONObject(index))
        messages = recent
        preferences.edit().putString("account_scope", scope).putString("conversation_id", conversationId)
            .putString("history", messages.toString()).putString("pending", pending?.toString().orEmpty()).apply()
    }

    private fun append(role: String, text: String, status: String, id: String = UUID.randomUUID().toString()) {
        if (text.isBlank()) return
        messages.put(JSONObject().apply {
            put("role", role); put("text", text); put("status", status); put("id", id); put("created_at", db.nowIso())
        })
        persist()
        renderTranscript()
    }

    private fun send(message: String) {
        if (busy || destroyed) return
        if (pending != null) {
            input?.setText("")
            append("user", message, "USER_REPORTED")
            busy = true; renderStatus()
            val requestScope = scope
            worker.execute {
                val answer = "An earlier PC operation is awaiting its confirmed response. Retry it before starting another remote operation.\n\n" + client.localReply(message)
                ui.post {
                    if (destroyed || requestScope != client.accountScope()) { busy = false; ensureScope(); return@post }
                    busy = false
                    append("assistant", answer, "LOCAL_READ_ONLY")
                    renderStatus()
                }
            }
            return
        }
        stopSpeech(); stopListening()
        input?.setText("")
        contextTopic = topicFor(message)
        val id = UUID.randomUUID().toString()
        append("user", message, "USER_REPORTED", id)
        if (!client.isRegistered()) {
            busy = true; renderStatus()
            val requestScope = scope
            worker.execute {
                val answer = client.localReply(message)
                ui.post {
                    if (destroyed || requestScope != scope) return@post
                    busy = false; mode = "LOCAL_READ_ONLY"; state = "UNKNOWN"
                    append("assistant", answer, "LOCAL_READ_ONLY")
                    actions = localActions(); renderTranscript(); renderStatus()
                }
            }
            return
        }
        val request = JSONObject().apply {
            put("kind", "chat"); put("message", message); put("request_id", id); put("conversation_id", conversationId)
        }
        pending = request; persist()
        performRequest(request)
    }

    private fun performRequest(request: JSONObject) {
        if (busy || destroyed) return
        ensureScope()
        busy = true; networkError = ""; stopSpeech(); renderStatus()
        onMotion("REVIEW")
        val requestScope = scope
        val requestId = request.optString("request_id")
        worker.execute {
            val result = if (request.optString("kind") == "action")
                client.action(request.optString("action"), request.optJSONObject("inputs") ?: JSONObject(), requestId, requestScope)
            else client.chat(request.optString("message"), request.optString("conversation_id"), requestId, requestScope)
            val fallback = if (result.offline && request.optString("kind") == "chat") client.localReply(request.optString("message")) else ""
            ui.post {
                if (destroyed || requestScope != client.accountScope()) { busy = false; ensureScope(); return@post }
                busy = false
                if (result.success) {
                    pending = null
                    applyResponse(result.data!!)
                    append("assistant", result.data.optString("reply", "The PC returned no explanation."), state, "response:$requestId")
                    speak(result.data.optString("reply"), requestId)
                    onMotion(if (state == "COMPLETED") "SUCCESS" else if (state in setOf("FAILED", "UNKNOWN")) "WARNING" else "IDLE")
                    if (request.optString("kind") == "action" && state == "COMPLETED") onConfirmedOperation()
                } else {
                    networkError = result.error
                    state = if (result.retryable) "UNKNOWN" else "FAILED"
                    contextDetails = JSONObject()
                    if (!result.retryable) pending = null
                    // The request ID is retained: a timeout can happen after the service committed it.
                    val failedId = "error:$requestId"
                    if ((0 until messages.length()).none { messages.optJSONObject(it)?.optString("id") == failedId })
                        append("assistant", result.error + if (fallback.isBlank()) "" else "\n\n$fallback", "FAILED", failedId)
                    contextActions = localActions()
                    onMotion("WARNING")
                }
                persist(); renderTranscript(); renderStatus()
                if (refreshQueued) { refreshQueued = false; refreshContext() }
            }
        }
    }

    private fun applyResponse(response: JSONObject, contextOnly: Boolean = false) {
        response.optString("conversation_id").takeIf { it.isNotBlank() }?.let { conversationId = it }
        // A local PIN/frontend role is never used to select administrator mode.
        mode = response.optString("mode", "UNKNOWN")
        state = response.optString("state", "UNKNOWN")
        if (contextOnly) contextActions = response.optJSONArray("actions") ?: JSONArray()
        else {
            val previousActions = actions
            actions = response.optJSONArray("actions") ?: JSONArray()
            if ((response.optJSONArray("proposals")?.length() ?: 0) == 0 && proposals.length() > 0) {
                // An unrelated read reply does not discard an awaiting confirmation card.
                for (index in 0 until previousActions.length()) previousActions.optJSONObject(index)?.let {
                    if (it.optString("type") == "confirm") actions.put(it)
                }
            }
        }
        response.optJSONArray("proposals")?.takeIf { it.length() > 0 }?.let { proposals = it }
        response.optString("reply").takeIf { it.isNotBlank() }?.let { contextText = it }
        contextDetails = response.optJSONObject("context") ?: JSONObject()
        networkError = ""
    }

    private fun refreshHistoryAndContext() {
        if (busy || destroyed) { refreshQueued = true; return }
        if (!client.isRegistered()) { refreshContext(); return }
        busy = true; renderStatus()
        val requestScope = scope
        worker.execute {
            val history = client.history(conversationId, requestScope)
            val current = client.context(contextTopic, requestScope)
            val local = if (current.success) "" else client.localReply(if (contextTopic == "next") "What should I do next?" else contextTopic)
            ui.post {
                if (destroyed || requestScope != client.accountScope()) { busy = false; ensureScope(); return@post }
                busy = false
                if (history.success) {
                    history.data!!.optString("conversation_id").takeIf { it.isNotBlank() }?.let { conversationId = it }
                    val serverHistory = history.data.optJSONArray("history")
                    if (serverHistory != null && pending == null) messages = serverHistory
                    if (history.data.has("actions")) actions = history.data.optJSONArray("actions") ?: JSONArray()
                    if (history.data.has("proposals")) proposals = history.data.optJSONArray("proposals") ?: JSONArray()
                }
                if (current.success) applyResponse(current.data!!, contextOnly = true)
                else { contextText = local; contextDetails = JSONObject(); networkError = current.error; mode = "LOCAL_READ_ONLY"; state = "UNKNOWN"; contextActions = localActions() }
                persist(); renderTranscript(); renderStatus()
                lastRefreshAt = System.currentTimeMillis()
                drainRefreshQueue()
            }
        }
    }

    /** Invoked by existing state/sync/permission events; there is no assistant polling loop. */
    fun onApplicationStateChanged(signature: String) {
        if (destroyed) return
        if (signature == lastLocalSignature) return
        lastLocalSignature = signature
        if (dialog?.isShowing == true) {
            if (busy) refreshQueued = true
            else {
                ui.removeCallbacks(refreshRunnable)
                ui.postDelayed(refreshRunnable, maxOf(150L, 1000L - (System.currentTimeMillis() - lastRefreshAt)))
            }
        }
    }

    private val refreshRunnable = Runnable { refreshContext() }

    fun recheckPermissions() {
        if (dialog?.isShowing == true) { ensureScope(); refreshContext() }
    }

    private fun refreshContext() {
        if (busy || destroyed) { refreshQueued = true; return }
        busy = true; renderStatus()
        val requestScope = scope
        worker.execute {
            val result = client.context(contextTopic, requestScope)
            val local = if (result.success) "" else client.localReply(if (contextTopic == "next") "What should I do next?" else contextTopic)
            ui.post {
                if (destroyed || requestScope != client.accountScope()) { busy = false; ensureScope(); return@post }
                busy = false
                if (result.success) applyResponse(result.data!!, contextOnly = true)
                else {
                    contextText = local; networkError = result.error; mode = "LOCAL_READ_ONLY"; state = "UNKNOWN"
                    contextDetails = JSONObject()
                    contextActions = localActions()
                }
                lastRefreshAt = System.currentTimeMillis()
                renderTranscript(); renderStatus()
                drainRefreshQueue()
            }
        }
    }

    private fun drainRefreshQueue() {
        if (refreshQueued) {
            refreshQueued = false
            ui.removeCallbacks(refreshRunnable)
            ui.postDelayed(refreshRunnable, 350L)
        }
    }

    private fun localActions() = JSONArray().apply {
        for ((target, title) in listOf("TASKS" to "Open Tasks", "WAKEUP" to "Wake-up checks", "DEVICES" to "Reconnect PC", "PERMISSIONS" to "Manage Permissions")) {
            put(JSONObject().apply { put("type", "navigate"); put("target", target); put("label", title) })
        }
    }

    private fun topicFor(message: String): String {
        val text = message.lowercase(Locale.US)
        return when {
            "permission" in text || "microphone" in text || "voice" in text -> "permissions"
            "wake" in text -> "wakeup"
            "evidence" in text || "proof" in text || "reject" in text || "ocr" in text -> "evidence"
            "summary" in text || "summarize" in text || "accomplish" in text -> "summary"
            "schedule" in text || "travel" in text || "routine" in text -> "schedule"
            "sync" in text || "device" in text || "connect" in text -> "devices"
            "score" in text || "point" in text || "reward" in text || "privilege" in text || "block" in text -> "privileges"
            else -> "next"
        }
    }

    private fun renderTranscript() {
        val container = transcript ?: return
        container.removeAllViews()
        val snapshot = card(MainActivity.CLR_BORDER)
        snapshot.addView(label("CURRENT CONTEXT • $state", 10f, MainActivity.CLR_CYAN, true))
        contextLabel = label(contextText, 12f, MainActivity.CLR_TEXT_SECONDARY)
        snapshot.addView(contextLabel)
        contextDetails.optJSONObject("next_action")?.let { next ->
            snapshot.addView(label("NEXT ACTION\n${next.optString("title")}\nDue: ${next.optString("window_end", "unknown")}\nEvidence: ${next.optString("proof_requirements", "not supplied")}\nStatus: ${next.optString("status", "unknown")}", 12f, MainActivity.CLR_TEXT_PRIMARY))
        }
        contextDetails.optJSONObject("wakeup")?.optJSONObject("gate")?.let { gate ->
            snapshot.addView(label("ROUTINE GATE • ${gate.optString("state", "UNKNOWN")}\nHourly attendance eligible: ${gate.optBoolean("active", false)}\nSchedule version: ${gate.optInt("schedule_version", 0)}\nMissing checks: ${formatValue(gate.opt("missing"))}", 12f, MainActivity.CLR_TEXT_SECONDARY))
        }
        contextDetails.optJSONObject("permissions")?.let { permissionState ->
            snapshot.addView(label("PHONE PERMISSIONS • ${permissionState.optString("state", "UNKNOWN")}\nObserved: ${permissionState.optString("observed_at", "unknown")}\nStale: ${permissionState.optBoolean("stale", true)}", 11f, MainActivity.CLR_CYAN, true))
            permissionState.optJSONObject("capabilities")?.let { capabilities ->
                capabilities.keys().asSequence().filter { it !in setOf("source", "checked_at") }.forEach { key ->
                    capabilities.optJSONObject(key)?.let { capability ->
                        val status = capability.optString("status").ifBlank {
                            "Supported: ${capability.optBoolean("supported", false)}; microphone allowed: ${capability.optBoolean("microphone_granted", false)}"
                        }
                        snapshot.addView(label("${key.replace('_', ' ')} • $status • ${if (capability.optBoolean("required")) "required for the associated feature" else "optional"}", 12f, MainActivity.CLR_TEXT_SECONDARY))
                    }
                }
            }
        }
        contextDetails.optJSONArray("evidence")?.let { reviews ->
            for (index in 0 until minOf(reviews.length(), 6)) reviews.optJSONObject(index)?.let { review ->
                snapshot.addView(label("SAVED EVIDENCE RESULT\n${formatValue(review)}", 12f, MainActivity.CLR_TEXT_SECONDARY))
            }
        }
        container.addView(snapshot)
        if (pending != null) container.addView(label("Pending response retained. Retry uses the same operation ID; no new change is created.", 11f, MainActivity.CLR_AMBER))
        for (index in 0 until messages.length()) {
            val item = messages.optJSONObject(index) ?: continue
            val user = item.optString("role") == "user"
            val message = card(if (user) MainActivity.CLR_BORDER else MainActivity.CLR_BLUE_DEEP)
            message.addView(label(if (user) "YOU • ${item.optString("status", "USER_REPORTED")}" else "STRICTBOT • ${item.optString("status", "KNOWN")}", 10f, if (user) MainActivity.CLR_CYAN else MainActivity.CLR_GREEN, true))
            message.addView(label(item.optString("text"), 14f, MainActivity.CLR_TEXT_PRIMARY).apply { setTextIsSelectable(true) })
            item.optString("created_at").takeIf { it.isNotBlank() }?.let { message.addView(label(it, 10f, MainActivity.CLR_TEXT_DIM)) }
            container.addView(message)
        }
        for (index in 0 until proposals.length()) proposals.optJSONObject(index)?.let { proposal ->
            val proposalCard = card(MainActivity.CLR_AMBER)
            proposalCard.addView(label("ROUTINE CHANGE • ${proposal.optString("status", "PENDING")}", 11f, MainActivity.CLR_AMBER, true))
            proposalCard.addView(label("${proposal.optString("reason", "Schedule request")}\nEvent: ${proposal.optString("event_start", "not supplied")}\nSix-hour deadline: ${proposal.optString("deadline", "unknown")}\nNotice eligible: ${if (proposal.has("notice_eligible")) proposal.optBoolean("notice_eligible").toString() else "unknown"}", 12f, MainActivity.CLR_TEXT_PRIMARY))
            proposalCard.addView(label("BEFORE\n${formatValue(proposal.opt("before"))}\n\nAFTER\n${formatValue(proposal.opt("after"))}", 12f, MainActivity.CLR_TEXT_SECONDARY))
            proposalCard.addView(label("Affected tasks\n${formatValue(proposal.opt("affected_tasks"))}\n\nAffected windows\n${formatValue(proposal.opt("affected_windows"))}", 12f, MainActivity.CLR_TEXT_SECONDARY))
            proposal.optString("points_explanation").takeIf { it.isNotBlank() }?.let { proposalCard.addView(label(it, 12f, MainActivity.CLR_AMBER)) }
            proposalCard.addView(label("Only service confirmation changes the schedule. Historical proofs and scores remain subject to existing policy.", 11f, MainActivity.CLR_TEXT_DIM))
            container.addView(proposalCard)
        }
        val availableActions = JSONArray()
        val seenActions = mutableSetOf<String>()
        for (source in listOf(actions, contextActions)) for (index in 0 until source.length()) {
            source.optJSONObject(index)?.let { action ->
                val key = "${action.optString("type")}:${action.optString("target")}:${action.optString("action")}:${action.optJSONObject("inputs")}"
                if (seenActions.add(key)) availableActions.put(action)
            }
        }
        if (availableActions.length() > 0) {
            container.addView(label("AVAILABLE ACTIONS", 10f, MainActivity.CLR_CYAN, true))
            for (index in 0 until availableActions.length()) availableActions.optJSONObject(index)?.let { action ->
                container.addView(button(action.optString("label", "Review action"), MainActivity.CLR_GREEN) { handleAction(action) },
                    LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(6) })
            }
        }
        onTypography(container)
        scroll?.post { scroll?.fullScroll(View.FOCUS_DOWN) }
    }

    private fun formatValue(value: Any?): String = when (value) {
        null, JSONObject.NULL -> "Not supplied by the service"
        is JSONObject -> value.keys().asSequence().joinToString("\n") { key -> "${key.replace('_', ' ')}: ${formatValue(value.opt(key))}" }
        is JSONArray -> if (value.length() == 0) "None" else (0 until value.length()).joinToString("\n") { "• ${formatValue(value.opt(it))}" }
        else -> value.toString()
    }

    private fun handleAction(action: JSONObject) {
        stopSpeech(); stopListening()
        when (action.optString("type")) {
            "navigate" -> {
                val target = action.optString("target").uppercase(Locale.US)
                if (target in setOf("TASKS", "WAKEUP", "DEVICES", "SCHEDULE", "PRIVILEGES", "REPORTS", "PERMISSIONS", "ADMIN", "EVIDENCE")) {
                    dialog?.dismiss(); onNavigate(target)
                } else { networkError = "This action has no supported Android screen."; renderStatus() }
            }
            "request" -> {
                val supplied = action.optJSONObject("inputs")?.optString("message").orEmpty()
                val message = supplied.ifBlank { action.optString("target").takeIf { it.isNotBlank() } ?: action.optString("label") }
                send(message)
            }
            "confirm" -> {
                if (busy || pending != null) { networkError = "Wait for the pending operation's confirmed result first."; renderStatus(); return }
                val actionName = action.optString("action")
                if (actionName.isBlank()) { networkError = "The PC supplied an incomplete action."; renderStatus(); return }
                val inputs = action.optJSONObject("inputs") ?: JSONObject()
                AlertDialog.Builder(activity).setTitle(action.optString("label", "Confirm operation"))
                    .setMessage("Review the proposal above. Submit this authorized request to the PC?\n\n${formatValue(inputs)}\n\nA completed result must be confirmed by the service.")
                    .setNegativeButton("Cancel", null).setPositiveButton("Confirm") { _, _ ->
                        val request = JSONObject().apply {
                            put("kind", "action"); put("action", actionName); put("inputs", inputs)
                            put("request_id", UUID.randomUUID().toString())
                        }
                        pending = request; persist()
                        append("user", "Confirmed request: ${action.optString("label", actionName)}", "PENDING", request.optString("request_id"))
                        performRequest(request)
                    }.show()
            }
            else -> { networkError = "Unsupported action type. No operation was sent."; renderStatus() }
        }
    }

    private fun renderStatus() {
        modeLabel?.text = when (mode) {
            "ADMINISTRATOR_PA" -> "Administrator PA • authenticated PC role"
            "USER_INSTRUCTOR" -> "User instructor • authenticated device"
            "LOCAL_READ_ONLY" -> "Local read-only • PC state unavailable"
            else -> "Authentication/state not confirmed"
        }
        statusLabel?.text = when {
            listening -> "Listening now • tap Mic to stop"
            speaking -> "Speaking • Stop interrupts audio"
            busy -> "Reading PC state / awaiting service result…"
            networkError.isNotBlank() -> networkError
            pending != null -> "Pending response • retry to check the recorded result"
            else -> "Text input ready • $state"
        }
        statusLabel?.setTextColor(Color.parseColor(if (networkError.isNotBlank()) MainActivity.CLR_AMBER else MainActivity.CLR_TEXT_SECONDARY))
        voiceButton?.text = if (voiceMuted) "Voice off" else "Voice on"
        listenButton?.text = if (listening) "Stop mic" else "Mic"
        listenButton?.isEnabled = !busy
        sendButton?.isEnabled = !busy
        retryButton?.visibility = if (pending != null) View.VISIBLE else View.GONE
        retryButton?.isEnabled = !busy
    }

    fun onPermissionResult(requestCode: Int, grantResults: IntArray): Boolean {
        if (requestCode != MICROPHONE_REQUEST) return false
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) startListening()
        else { networkError = "Microphone permission was denied. Type your message; voice input is optional."; renderStatus() }
        return true
    }

    private fun startListening() {
        if (listening) { stopListening(); return }
        if (!SpeechRecognizer.isRecognitionAvailable(activity)) {
            networkError = "Speech recognition is unavailable on this phone. Text conversation remains available."
            renderStatus(); return
        }
        if (activity.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            activity.requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), MICROPHONE_REQUEST)
            return
        }
        stopSpeech(); networkError = ""
        try {
            if (recognizer == null) recognizer = SpeechRecognizer.createSpeechRecognizer(activity).apply {
                setRecognitionListener(object : RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) { if (recognitionActive) { listening = true; renderStatus() } }
                    override fun onBeginningOfSpeech() {}
                    override fun onRmsChanged(rmsdB: Float) {}
                    override fun onBufferReceived(buffer: ByteArray?) {}
                    override fun onEndOfSpeech() { listening = false; renderStatus() }
                    override fun onError(error: Int) {
                        if (!recognitionActive) return
                        recognitionActive = false
                        listening = false
                        networkError = when (error) {
                            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Microphone permission is required for voice input. Text remains available."
                            SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "No speech was recognized. Try again or type your message."
                            SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "The phone's speech service could not connect. Type your message."
                            else -> "The phone's speech service stopped (code $error). Text remains available."
                        }
                        renderStatus()
                    }
                    override fun onResults(results: Bundle?) {
                        if (!recognitionActive) return
                        recognitionActive = false
                        listening = false
                        results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.let { text ->
                            input?.setText(text); input?.setSelection(text.length)
                            networkError = "Voice text is ready. Review it and tap Send."
                        }
                        renderStatus()
                    }
                    override fun onPartialResults(partialResults: Bundle?) {}
                    override fun onEvent(eventType: Int, params: Bundle?) {}
                })
            }
            recognitionActive = true; listening = true; renderStatus()
            recognizer?.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag())
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
            })
        } catch (_: Exception) { listening = false; networkError = "Voice input could not start. Text remains available."; renderStatus() }
    }

    private fun stopListening() {
        recognitionActive = false; listening = false
        runCatching { recognizer?.cancel() }
        renderStatus()
    }

    fun speakUserRequested(text: String) = speak(text, "manual:${UUID.randomUUID()}", userInitiated = true)

    private fun speak(text: String, eventId: String, userInitiated: Boolean = false) {
        if (destroyed || text.isBlank() || (!userInitiated && voiceMuted) || !spokenEvents.add(eventId)) return
        while (spokenEvents.size > 50) spokenEvents.remove(spokenEvents.first())
        // Synthesis is explicitly requested after a result, never during a render/context refresh.
        val generation = speechGeneration
        ui.post {
            if (destroyed || generation != speechGeneration) return@post
            if (ttsReady) speakReady(text, eventId)
            else {
                queuedSpeech = text to eventId
                if (ttsStarting) return@post
                ttsStarting = true
                try {
                    tts = TextToSpeech(activity.applicationContext) { result ->
                        ui.post initializationReady@ {
                            if (destroyed) return@initializationReady
                            ttsStarting = false
                            ttsReady = result == TextToSpeech.SUCCESS
                            if (ttsReady) {
                                val languageResult = tts?.setLanguage(Locale.getDefault())
                                if (languageResult == TextToSpeech.LANG_MISSING_DATA || languageResult == TextToSpeech.LANG_NOT_SUPPORTED) tts?.language = Locale.US
                                tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                                    override fun onStart(utteranceId: String?) { ui.post { if (!destroyed && utteranceId == currentUtteranceId) { speaking = true; onMotion("SPEAK"); renderStatus() } } }
                                    override fun onDone(utteranceId: String?) { ui.post { if (!destroyed && utteranceId == currentUtteranceId) { currentUtteranceId = null; speaking = false; onMotion("IDLE"); renderStatus() } } }
                                    @Deprecated("Required compatibility callback")
                                    override fun onError(utteranceId: String?) { ui.post { if (!destroyed && utteranceId == currentUtteranceId) { currentUtteranceId = null; speaking = false; networkError = "Speech output failed. The text response is still available."; onMotion("IDLE"); renderStatus() } } }
                                })
                                queuedSpeech?.let { speakReady(it.first, it.second) }; queuedSpeech = null
                            } else { queuedSpeech = null; networkError = "Speech output is unavailable. Text remains available."; renderStatus() }
                        }
                    }
                } catch (_: Exception) { ttsStarting = false; queuedSpeech = null; networkError = "Speech output could not start. Text remains available."; renderStatus() }
            }
        }
    }

    private fun speakReady(text: String, eventId: String) {
        currentUtteranceId = eventId
        runCatching { tts?.speak(text.take(3900), TextToSpeech.QUEUE_FLUSH, null, eventId) }
            .onFailure { networkError = "Speech output failed. Read the text response."; renderStatus() }
    }

    fun stopSpeech() {
        speechGeneration++
        currentUtteranceId = null
        queuedSpeech = null
        runCatching { tts?.stop() }
        if (speaking) onMotion("IDLE")
        speaking = false; renderStatus()
    }

    fun pause() { stopSpeech(); stopListening() }

    fun destroy() {
        destroyed = true
        dialog?.dismiss()
        ui.removeCallbacks(refreshRunnable)
        stopSpeech(); stopListening()
        runCatching { recognizer?.destroy() }; recognizer = null
        runCatching { tts?.shutdown() }; tts = null
        worker.shutdownNow()
    }

    fun onConfigurationChanged() { if (dialog?.isShowing == true) resizeDialog() }

    private fun resizeDialog() {
        val dm = activity.resources.displayMetrics
        val width = dm.widthPixels
        val height = dm.heightPixels
        val density = dm.density
        val widthDp = width / density
        val isTablet = widthDp >= 600f
        val isLandscape = width > height

        val dialogWidth: Int
        val dialogHeight: Int

        if (isTablet || isLandscape) {
            dialogWidth = dp(460).coerceAtMost((width * 0.70f).toInt())
            dialogHeight = (height * if (expanded) 0.82f else 0.65f).toInt().coerceAtMost(dp(560))
        } else {
            // Compact mobile portrait: keep healthy margins (16dp each side), never bleed into edges
            dialogWidth = (width - dp(32)).coerceAtMost(dp(420))
            dialogHeight = (height * if (expanded) 0.80f else 0.62f).toInt().coerceAtMost(dp(520)).coerceAtLeast(dp(360))
        }

        dialog?.window?.apply {
            setLayout(dialogWidth, dialogHeight)
            setGravity(Gravity.CENTER)
        }
    }

    private fun dp(value: Int) = (value * activity.resources.displayMetrics.density).toInt()
    private fun row() = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
    private fun weighted(rightMarginDp: Int = 4) = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = dp(rightMarginDp) }
    private fun label(text: String, size: Float, color: String, bold: Boolean = false) = TextView(activity).apply {
        this.text = text; textSize = size; setTextColor(Color.parseColor(color))
        if (bold) setTypeface(typeface, Typeface.BOLD)
        setPadding(0, dp(4), 0, dp(4))
    }
    private fun button(title: String, accent: String = MainActivity.CLR_CYAN, callback: () -> Unit) = compactButton(title, accent, minH = 34, padH = 8, padV = 6, txtSize = 11.5f, callback = callback)
    private fun compactButton(title: String, accent: String = MainActivity.CLR_CYAN, minH: Int = 30, padH: Int = 8, padV: Int = 4, txtSize: Float = 11f, callback: () -> Unit) = TextView(activity).apply {
        text = title; textSize = txtSize; gravity = Gravity.CENTER; minHeight = dp(minH)
        maxLines = 1; ellipsize = android.text.TextUtils.TruncateAt.END
        setTextColor(Color.parseColor(accent)); setTypeface(typeface, Typeface.BOLD)
        setPadding(dp(padH), dp(padV), dp(padH), dp(padV))
        background = background(MainActivity.CLR_SURFACE_LOW, MainActivity.CLR_BORDER)
        isClickable = true; isFocusable = true
        setOnClickListener { callback() }
    }
    private fun card(border: String) = LinearLayout(activity).apply {
        orientation = LinearLayout.VERTICAL; setPadding(dp(12), dp(8), dp(12), dp(10))
        background = background(MainActivity.CLR_SURFACE, border)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(8) }
    }
    private fun background(fill: String, border: String) = GradientDrawable().apply {
        shape = GradientDrawable.RECTANGLE; setColor(Color.parseColor(fill)); cornerRadius = dp(14).toFloat()
        setStroke(dp(1).coerceAtLeast(1), Color.parseColor(border))
    }
}
