package com.strictrecords.app

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.SoundPool
import android.os.Build
import android.os.Handler
import android.os.Looper

/** Short original bundled effects; opt-in, no volume changes, no background playback. */
class FarmSoundController(private val context: Context) {
    private val audio = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val attrs = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
    private val pool = SoundPool.Builder().setMaxStreams(1).setAudioAttributes(attrs).build()
    private val loaded = mutableSetOf<Int>()
    private val handler = Handler(Looper.getMainLooper())
    private val focusListener = AudioManager.OnAudioFocusChangeListener { if (it <= 0) stop() }
    private val request = if (Build.VERSION.SDK_INT >= 26) AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK).setAudioAttributes(attrs).setOnAudioFocusChangeListener(focusListener).build() else null
    private val rain: Int
    private val storm: Int
    private val grow: Int
    private var stream = 0
    private var released = false
    init {
        pool.setOnLoadCompleteListener { _, id, status -> if (status == 0) loaded.add(id) }
        rain = pool.load(context, R.raw.farm_rain, 1)
        storm = pool.load(context, R.raw.farm_storm, 1)
        grow = pool.load(context, R.raw.farm_growth, 1)
    }
    /** Delay the one thunder cue until after the shared timeline's lightning flash. No ambient loop. */
    fun schedule(cue: FarmWeatherModel.SoundCue, enabled: Boolean, foreground: Boolean, level:()->Float) {
        stop()
        val reduced=android.provider.Settings.Global.getFloat(context.contentResolver,android.provider.Settings.Global.ANIMATOR_DURATION_SCALE,1f)==0f
        if(!FarmWeatherModel.soundAllowed(enabled,foreground,audio.ringerMode==AudioManager.RINGER_MODE_NORMAL,
                audio.getStreamVolume(AudioManager.STREAM_MUSIC)>0,reduced) || released)return
        val id=if(cue.thunder)storm else grow
        handler.postDelayed({
            if(released || audio.ringerMode!=AudioManager.RINGER_MODE_NORMAL || audio.getStreamVolume(AudioManager.STREAM_MUSIC)==0 || id !in loaded)return@postDelayed
            val granted=if(Build.VERSION.SDK_INT>=26)audio.requestAudioFocus(request!!) else @Suppress("DEPRECATION") audio.requestAudioFocus(focusListener,AudioManager.STREAM_MUSIC,AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
            if(granted!=AudioManager.AUDIOFOCUS_REQUEST_GRANTED)return@postDelayed
            val volume=.20f*cue.strength.coerceIn(0f,1f)
            stream=pool.play(id,volume,volume,1,0,1f)
            val started=android.os.SystemClock.elapsedRealtime()
            val fade=object:Runnable {
                override fun run() {
                    val age=android.os.SystemClock.elapsedRealtime()-started
                    if(age>=1300 || released || stream==0 || audio.ringerMode!=AudioManager.RINGER_MODE_NORMAL || audio.getStreamVolume(AudioManager.STREAM_MUSIC)==0) {stop();return}
                    val intensity=if(cue.thunder)level().coerceIn(0f,1f) else cue.strength
                    val gain=.20f*intensity*(1f-age/1300f)
                    pool.setVolume(stream,gain,gain);handler.postDelayed(this,60)
                }
            }
            handler.post(fade)
        },cue.delayMs)
    }
    fun stop() {
        handler.removeCallbacksAndMessages(null)
        if (!released && stream != 0) pool.stop(stream)
        stream = 0
        if (Build.VERSION.SDK_INT >= 26) request?.let { audio.abandonAudioFocusRequest(it) }
        else @Suppress("DEPRECATION") audio.abandonAudioFocus(focusListener)
    }
    fun close() { if (!released) { stop(); released = true; pool.release() } }
}
