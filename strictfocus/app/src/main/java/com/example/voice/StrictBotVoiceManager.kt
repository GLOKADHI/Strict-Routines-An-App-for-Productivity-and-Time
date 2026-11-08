package com.example.voice

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.Locale
import java.util.UUID

enum class VoicePersona(val title: String, val pitch: Float, val speechRate: Float) {
    COACH("Empathetic Coach", 1.05f, 1.0f),
    STRICT("Strict Disciplinarian", 0.95f, 1.05f),
    BUDDY("Cyber Wingman", 1.15f, 1.02f),
    ENERGETIC("High Octane Hype", 1.25f, 1.12f)
}

data class HumanSpeechQuote(
    val category: String,
    val text: String,
    val actionCommand: String = "SPEAK"
)

val HumanSpeechLibrary = listOf(
    HumanSpeechQuote(
        category = "Discipline",
        text = "Hey Lok! I'm watching your discipline score. Looking sharp today! Let's get this deep work crushed without any distractions."
    ),
    HumanSpeechQuote(
        category = "Flight",
        text = "Thrusters at maximum velocity! Whooooosh! Keep your momentum going, freedom is on the other side of hard work!",
        actionCommand = "FLY"
    ),
    HumanSpeechQuote(
        category = "Walk",
        text = "Stepping down for perimeter patrol. Left, right, left, right. Everything is locked down and secure. Keep going, buddy!",
        actionCommand = "WALK"
    ),
    HumanSpeechQuote(
        category = "Motivation",
        text = "You're twelve days into this streak! Don't you dare slack off now. One focused hour at a time, we build greatness."
    ),
    HumanSpeechQuote(
        category = "Empathetic",
        text = "Take a breath, roll your shoulders back, and drink some water. I've got your back on this journey."
    ),
    HumanSpeechQuote(
        category = "Strict Alert",
        text = "Warning! Distraction sensors detect wandering thoughts. Eyes on the prize, soldier! Back to the mission!"
    ),
    HumanSpeechQuote(
        category = "Celebration",
        text = "Boom! That's another hour locked in! You're operating on pure discipline right now, and I respect that!"
    )
)

class StrictBotVoiceManager(private val context: Context) : TextToSpeech.OnInitListener {

    private var tts: TextToSpeech? = null
    private var isInitialized = false

    private val _isSpeaking = MutableStateFlow(false)
    val isSpeaking: StateFlow<Boolean> = _isSpeaking.asStateFlow()

    private val _currentSpokenText = MutableStateFlow<String?>(null)
    val currentSpokenText: StateFlow<String?> = _currentSpokenText.asStateFlow()

    private val _waveAmplitudes = MutableStateFlow(listOf(0.2f, 0.4f, 0.3f, 0.5f, 0.2f))
    val waveAmplitudes: StateFlow<List<Float>> = _waveAmplitudes.asStateFlow()

    private var waveAnimationJob: Job? = null
    private val scope = CoroutineScope(Dispatchers.Main)

    init {
        try {
            tts = TextToSpeech(context.applicationContext, this)
        } catch (e: Exception) {
            Log.e("StrictBotVoice", "Failed to initialize TTS", e)
        }
    }

    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            val result = tts?.setLanguage(Locale.US)
            if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
                tts?.setLanguage(Locale.getDefault())
            }
            tts?.setPitch(1.08f)
            tts?.setSpeechRate(1.02f)
            isInitialized = true

            tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {
                    _isSpeaking.value = true
                    startWaveAnimation()
                }

                override fun onDone(utteranceId: String?) {
                    _isSpeaking.value = false
                    _currentSpokenText.value = null
                    stopWaveAnimation()
                }

                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String?) {
                    _isSpeaking.value = false
                    _currentSpokenText.value = null
                    stopWaveAnimation()
                }

                override fun onError(utteranceId: String?, errorCode: Int) {
                    _isSpeaking.value = false
                    _currentSpokenText.value = null
                    stopWaveAnimation()
                }
            })
        }
    }

    fun speak(text: String, pitch: Float = 1.08f, speechRate: Float = 1.02f) {
        _currentSpokenText.value = text
        if (!isInitialized || tts == null) {
            // Emulate speaking state for UI visualization if device TTS is busy or initializing
            _isSpeaking.value = true
            startWaveAnimation()
            scope.launch {
                delay(3500)
                _isSpeaking.value = false
                _currentSpokenText.value = null
                stopWaveAnimation()
            }
            return
        }

        try {
            tts?.setPitch(pitch)
            tts?.setSpeechRate(speechRate)
            val utteranceId = UUID.randomUUID().toString()
            tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, utteranceId)
            _isSpeaking.value = true
            startWaveAnimation()
        } catch (e: Exception) {
            Log.e("StrictBotVoice", "TTS speak failed", e)
        }
    }

    fun stop() {
        try {
            tts?.stop()
        } catch (e: Exception) {
            // ignore
        }
        _isSpeaking.value = false
        _currentSpokenText.value = null
        stopWaveAnimation()
    }

    private fun startWaveAnimation() {
        waveAnimationJob?.cancel()
        waveAnimationJob = scope.launch {
            while (_isSpeaking.value) {
                _waveAmplitudes.update {
                    listOf(
                        (0.2f..0.9f).randomFloat(),
                        (0.3f..1.0f).randomFloat(),
                        (0.2f..0.85f).randomFloat(),
                        (0.4f..1.0f).randomFloat(),
                        (0.15f..0.7f).randomFloat()
                    )
                }
                delay(100)
            }
            _waveAmplitudes.value = listOf(0.15f, 0.2f, 0.15f, 0.2f, 0.15f)
        }
    }

    private fun stopWaveAnimation() {
        waveAnimationJob?.cancel()
        _waveAmplitudes.value = listOf(0.15f, 0.2f, 0.15f, 0.2f, 0.15f)
    }

    fun shutdown() {
        try {
            tts?.stop()
            tts?.shutdown()
        } catch (e: Exception) {
            // ignore
        }
    }

    private fun ClosedFloatingPointRange<Float>.randomFloat(): Float {
        return (start + Math.random() * (endInclusive - start)).toFloat()
    }
}
