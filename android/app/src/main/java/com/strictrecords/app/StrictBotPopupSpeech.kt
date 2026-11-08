package com.strictrecords.app

import android.app.KeyguardManager
import android.app.NotificationManager
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale
import java.util.UUID

/** One short utterance per popup event, with view-owned cancellation and no microphone. */
internal class StrictBotPopupSpeech(
    context: Context,
    private val onSpeaking: (Boolean) -> Unit,
    private val onStatus: (String) -> Unit,
    private val allowWhileLocked: Boolean = false
) {
    companion object {
        private val policy = BotAnnouncementPolicy()
        private var active: StrictBotPopupSpeech? = null
        private const val PREFS = "strictbot_popup"
        fun isVoiceEnabled(context: Context): Boolean {
            val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            // Honour an explicitly saved chat mute when the new popup setting is unset.
            val chat = context.getSharedPreferences("strictbot_chat", Context.MODE_PRIVATE)
            val default = !chat.getBoolean("voice_muted", false)
            return prefs.getBoolean("voice_enabled", default)
        }
        fun setVoiceEnabled(context: Context, enabled: Boolean) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("voice_enabled", enabled).apply()
            if (!enabled) active?.stop()
        }
    }

    private val app = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private val audio = app.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private var engine: TextToSpeech? = null
    private var ready = false
    private var starting = false
    private var closed = false
    private var queued: String? = null
    private var utterance: String? = null
    private var generation = 0L
    private var deadline = 0L
    private val focusListener = AudioManager.OnAudioFocusChangeListener { change ->
        if (change < 0) main.post { stop() }
    }
    private val guard = object : Runnable {
        override fun run() {
            if (closed || active !== this@StrictBotPopupSpeech) return
            if (SystemClock.uptimeMillis() >= deadline || !safeToSpeak()) {
                stop()
                onStatus("Voice paused · text remains available")
                return
            }
            main.postDelayed(this, 400L)
        }
    }

    private fun safeToSpeak(): Boolean = runCatching {
        val notifications = app.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val keyguard = app.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        audio.mode == AudioManager.MODE_NORMAL && audio.ringerMode == AudioManager.RINGER_MODE_NORMAL &&
            notifications.currentInterruptionFilter == NotificationManager.INTERRUPTION_FILTER_ALL &&
            (allowWhileLocked || !keyguard.isKeyguardLocked) && !WakeUpPlaybackService.isPlaybackActive()
    }.getOrDefault(false)

    fun speak(text: String, eventKey: String, manual: Boolean = false) {
        if (closed || text.isBlank()) return
        val safe = safeToSpeak() && (manual || !audio.isMusicActive)
        if (!policy.claim(eventKey, SystemClock.uptimeMillis(), isVoiceEnabled(app), safe, manual)) return
        active?.takeIf { it !== this }?.stop()
        active = this
        queued = text.take(350)
        deadline = SystemClock.uptimeMillis() + 25_000L
        main.removeCallbacks(guard)
        main.post(guard)
        if (ready) { speakReady(); return }
        if (starting) return
        starting = true
        onStatus("Preparing voice…")
        val expected = generation
        try {
            runCatching { engine?.shutdown() }
            engine = TextToSpeech(app) { result -> main.post {
                if (closed || expected != generation) return@post
                starting = false
                if (result != TextToSpeech.SUCCESS) { fail(); return@post }
                val tts = engine ?: return@post
                try {
                val language = tts.setLanguage(Locale.getDefault())
                if (language == TextToSpeech.LANG_MISSING_DATA || language == TextToSpeech.LANG_NOT_SUPPORTED) {
                    val fallback = tts.setLanguage(Locale.US)
                    if (fallback == TextToSpeech.LANG_MISSING_DATA || fallback == TextToSpeech.LANG_NOT_SUPPORTED) {
                        fail(); return@post
                    }
                }
                tts.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                tts.setSpeechRate(0.95f)
                tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(id: String?) { main.post {
                        if (!closed && id == utterance && active === this@StrictBotPopupSpeech) {
                            onSpeaking(true); onStatus("StrictBot is speaking")
                        }
                    } }
                    override fun onDone(id: String?) = completed(id)
                    override fun onStop(id: String?, interrupted: Boolean) = completed(id)
                    @Deprecated("Required by Android TTS")
                    override fun onError(id: String?) { main.post { if (id == utterance) fail() } }
                    override fun onError(id: String?, errorCode: Int) { main.post { if (id == utterance) fail() } }
                })
                ready = true
                if (queued != null && active === this) speakReady()
                } catch (_: Exception) { fail() }
            } }
        } catch (_: Exception) { starting = false; fail() }
    }

    @Suppress("DEPRECATION")
    private fun speakReady() {
        val text = queued ?: return
        if (closed || active !== this || !safeToSpeak() || !isVoiceEnabled(app)) { stop(); return }
        queued = null
        if (audio.requestAudioFocus(focusListener, AudioManager.STREAM_MUSIC,
                AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK) != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) {
            fail(); return
        }
        utterance = "popup-${UUID.randomUUID()}"
        if (runCatching { engine?.speak(text, TextToSpeech.QUEUE_FLUSH, null, utterance) }
                .getOrDefault(TextToSpeech.ERROR) != TextToSpeech.SUCCESS) fail()
    }

    private fun completed(id: String?) { main.post {
        if (!closed && id != null && id == utterance) { stop(); onStatus("Tap Speak to hear again") }
    } }
    private fun fail() { stop(); onStatus("Voice unavailable · text remains available") }

    @Suppress("DEPRECATION")
    fun stop() {
        generation++
        starting = false
        queued = null
        utterance = null
        main.removeCallbacks(guard)
        runCatching { engine?.stop() }
        runCatching { audio.abandonAudioFocus(focusListener) }
        if (active === this) active = null
        onSpeaking(false)
    }

    fun close() {
        closed = true
        stop()
        runCatching { engine?.shutdown() }
        engine = null
        ready = false
    }
}
