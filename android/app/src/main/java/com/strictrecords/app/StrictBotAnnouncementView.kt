package com.strictrecords.app

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/** Shared moving/speaking character for restriction and scheduled-task popups. */
internal class StrictBotAnnouncementView(
    context: Context,
    message: String,
    private var eventKey: String,
    private val allowWhileLocked: Boolean = false
) : LinearLayout(context) {
    val bot = DisciplineBotWalkingView(context)
    private var spokenMessage = message
    private var speech: StrictBotPopupSpeech? = null
    private var announceOnShow = true
    private val messageLabel = TextView(context)
    private val status = TextView(context)
    private val voiceButton = Button(context)
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    init {
        orientation = VERTICAL
        setPadding(dp(10), dp(6), dp(10), dp(8))
        background = GradientDrawable().apply {
            setColor(Color.parseColor("#0B2028")); cornerRadius = dp(12).toFloat()
            setStroke(dp(1), Color.parseColor("#20566B"))
        }
        val row = LinearLayout(context).apply { orientation = HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        bot.contentDescription = "StrictBot assistant"
        bot.setPresentation(scale = 1.15f, stationary = true)
        row.addView(bot, LayoutParams(dp(112), dp(96)))
        row.addView(LinearLayout(context).apply {
            orientation = VERTICAL
            addView(TextView(context).apply {
                text = "StrictBot"; textSize = 14f; setTextColor(Color.parseColor("#56DCED"))
                setTypeface(typeface, Typeface.BOLD)
            })
            status.textSize = 11f
            status.setTextColor(Color.parseColor("#B1C7D3"))
            status.text = "Your routine companion"
            addView(status)
        }, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        addView(row, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
        messageLabel.text = message
        messageLabel.textSize = 13f
        messageLabel.setTextColor(Color.WHITE)
        messageLabel.setPadding(0, 0, 0, dp(6))
        addView(messageLabel, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
        val controls = LinearLayout(context).apply { orientation = HORIZONTAL }
        fun style(button: Button) {
            button.textSize = 11f; button.isAllCaps = false; button.minWidth = 0
            button.minimumWidth = 0; button.minimumHeight = dp(44)
            button.setPadding(dp(8), dp(4), dp(8), dp(4))
            button.setTextColor(Color.parseColor("#59E4C2"))
            button.background = GradientDrawable().apply {
                setColor(Color.parseColor("#102F35")); cornerRadius = dp(8).toFloat()
            }
        }
        controls.addView(Button(context).apply {
            style(this); text = "Speak"
            contentDescription = "Hear this StrictBot message"
            setOnClickListener {
                StrictBotPopupSpeech.setVoiceEnabled(context, true)
                updateVoiceLabel()
                ensureSpeech().speak(spokenMessage, eventKey, manual = true)
            }
        }, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(6) })
        style(voiceButton)
        voiceButton.setOnClickListener {
            val enabled = !StrictBotPopupSpeech.isVoiceEnabled(context)
            StrictBotPopupSpeech.setVoiceEnabled(context, enabled)
            updateVoiceLabel()
            if (enabled) ensureSpeech().speak(spokenMessage, eventKey, manual = true)
            else { pauseSpeech(); status.text = "Voice muted" }
        }
        controls.addView(voiceButton, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        updateVoiceLabel()
        addView(controls, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
    }

    private fun updateVoiceLabel() {
        voiceButton.text = if (StrictBotPopupSpeech.isVoiceEnabled(context)) "Mute" else "Voice off"
        voiceButton.contentDescription = if (StrictBotPopupSpeech.isVoiceEnabled(context)) "Mute popup speech" else "Enable popup speech"
    }
    private fun ensureSpeech(): StrictBotPopupSpeech = speech ?: StrictBotPopupSpeech(context,
        onSpeaking = { bot.setSpeaking(it) }, onStatus = { status.text = it },
        allowWhileLocked = allowWhileLocked).also { speech = it }

    fun setMessage(message: String, key: String = eventKey) {
        if (message != spokenMessage || key != eventKey) pauseSpeech()
        spokenMessage = message; eventKey = key; messageLabel.text = message
        // Score polling updates text; it does not repeatedly narrate the popup.
    }
    fun pauseSpeech() { speech?.stop(); bot.setSpeaking(false) }
    fun setAutoSpeak(enabled: Boolean) {
        announceOnShow = enabled
        if (!enabled) pauseSpeech() else if (isAttachedToWindow) announce()
    }
    fun releaseSpeech() { speech?.close(); speech = null; bot.setSpeaking(false) }
    private fun announce() {
        updateVoiceLabel()
        if (announceOnShow && isShown) ensureSpeech().speak(spokenMessage, eventKey)
    }
    override fun onAttachedToWindow() { super.onAttachedToWindow(); bot.startAnimation(); post { if (isAttachedToWindow) announce() } }
    override fun onWindowVisibilityChanged(visibility: Int) {
        super.onWindowVisibilityChanged(visibility)
        if (visibility != View.VISIBLE) { pauseSpeech(); bot.stopAnimation() }
        else if (isAttachedToWindow) { bot.startAnimation(); announce() }
    }
    override fun onDetachedFromWindow() {
        releaseSpeech()
        super.onDetachedFromWindow()
    }
}
