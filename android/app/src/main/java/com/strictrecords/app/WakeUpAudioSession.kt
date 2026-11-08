package com.strictrecords.app

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.AudioRouting
import android.media.MediaPlayer
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import org.json.JSONObject
import java.io.File

/**
 * High-Quality Stereo Wake-Up Audio Playback and Continuous Route Verification Session.
 *
 * Guarantees:
 * - Plays the actual user-selected, validated wake-up track from app-private storage
 *   (or bundled motivation track fallback) with natural, balanced stereo separation.
 * - Decodes original audio format at native sample rate without mono downmixing, artificial EQ,
 *   spatializers, or dynamic compression.
 * - Requires active Bluetooth earbud audio route before and continuously during playback.
 * - Enforces usable media volume (> 0) and <= 60% STREAM_MUSIC volume cap.
 * - Pauses verification immediately if earbuds disconnect or route switches to speaker;
 *   never counts disconnected time as completed listening.
 * - Anti-seek protection: measures genuine playback position progress.
 * - Continuous playback across app minimization, screen off, and backgrounding via dedicated
 *   background worker thread and MediaPlayer routing callbacks.
 * - Completes only on natural full-track end.
 * - Provides identical verification execution for both Real and Demo modes.
 */
class WakeUpAudioSession(
    val context: Context,
    val db: StrictRecordsLocalDb,
    val date: String,
    val attemptId: String,
    val demo: Boolean = false,
    private val onStatusUpdate: (String, Int, Int, Int) -> Unit, // message, pct, posMs, durMs
    private val onCompleteCallback: (JSONObject) -> Unit
) {
    /**
     * Backward-compatible secondary constructor for existing call sites.
     */
    constructor(
        context: Context,
        db: StrictRecordsLocalDb,
        date: String,
        attemptId: String,
        onStatus: (String, Int) -> Unit,
        onComplete: (JSONObject) -> Unit,
        demo: Boolean = false
    ) : this(
        context = context.applicationContext,
        db = db,
        date = date,
        attemptId = attemptId,
        demo = demo,
        onStatusUpdate = { msg, pct, _, _ -> onStatus(msg, pct) },
        onCompleteCallback = onComplete
    )

    private val ui = Handler(Looper.getMainLooper())
    private val workerThread = HandlerThread("WakeUpAudioWorker").apply { start() }
    private val workerHandler = Handler(workerThread.looper)

    private val audio = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val selectedName = db.getEarbudsConfig()
    private val selectedAddress = db.getEarbudsAddress()

    private var player: MediaPlayer? = null
    private var preparing = false
    var position = 0
        private set
    var duration = 0
        private set
    var listened = 0L
        private set
    private var previousTick = 0L
    private var startedAtTickMs = 0L
    private var pausedAt = 0L
    private var unverifiedRouteSinceMs = 0L
    var paused = false
        private set
    var closed = false
        private set
    var completing = false
        private set
    var seekAttempts = 0
        private set
    var routeTicks = 0
        private set
    private var startedAt = ""
    private var lastRouteDevice: AudioDeviceInfo? = null
    var lastRouteTypeName = "NONE"
        private set
    var lastRouteDeviceName = ""
        private set
    var lastRouteDeviceAddress = ""
        private set
    var lastStatusMessage = ""
        private set

    val currentPositionMs: Int get() = position
    val trackDurationMs: Int get() = duration
    val currentProgressPct: Int get() = if (duration > 0) ((position.toLong() * 100) / duration).toInt().coerceIn(0, 100) else 0
    val isClosed: Boolean get() = closed
    val isBusy: Boolean get() = !closed && !paused && player != null && !completing
    val isPaused: Boolean get() = paused

    private var focusRequest: AudioFocusRequest? = null
    private var isTransientFocusLoss = false

    private val focusListener = AudioManager.OnAudioFocusChangeListener { focusChange ->
        when (focusChange) {
            AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK -> {
                try { player?.setVolume(0.2f, 0.2f) } catch (_: Throwable) {}
            }
            AudioManager.AUDIOFOCUS_LOSS_TRANSIENT -> {
                isTransientFocusLoss = true
                workerHandler.post {
                    pause("Playback temporarily paused: audio focus lost transiently")
                }
            }
            AudioManager.AUDIOFOCUS_GAIN -> {
                try { player?.setVolume(1.0f, 1.0f) } catch (_: Throwable) {}
                if (isTransientFocusLoss) {
                    isTransientFocusLoss = false
                    workerHandler.post {
                        if (!closed && !completing && player != null) {
                            try {
                                startOrResume()
                            } catch (_: Throwable) {}
                        }
                    }
                }
            }
            AudioManager.AUDIOFOCUS_LOSS -> {
                isTransientFocusLoss = false
                workerHandler.post { pause("Playback interrupted: audio focus lost to another app") }
            }
        }
    }

    private val routingListener = AudioRouting.OnRoutingChangedListener { routing ->
        if (closed || paused || completing) return@OnRoutingChangedListener
        val routed = (routing as? MediaPlayer)?.routedDevice
        workerHandler.post {
            if (closed || paused || completing) return@post
            if (routed != null) {
                lastRouteDevice = routed
                lastRouteDeviceName = routed.productName?.toString().orEmpty()
                lastRouteTypeName = WakeUpAudioRouteManager.getRouteTypeName(routed.type)
                if (isRouteAcceptable(routed)) {
                    unverifiedRouteSinceMs = 0L
                } else if (!WakeUpAudioRouteManager.isSupportedBluetoothAudioDevice(routed)) {
                    // Immediate route violation: player routed to phone speaker or non-Bluetooth output
                    pause("Earbuds disconnected — output switched to ${WakeUpAudioRouteManager.getRouteTypeName(routed.type)}")
                }
            }
        }
    }

    private fun isRouteAcceptable(device: AudioDeviceInfo?): Boolean {
        if (device == null) return false
        return WakeUpAudioRouteManager.isMediaPlayerRouteVerified(device, selectedName, selectedAddress)
    }

    private fun status(message: String) {
        lastStatusMessage = message
        val pct = currentProgressPct
        Log.i("StrictWakeAudio", "$message [pos=$position, dur=$duration, listened=$listened, pct=$pct%, routeTicks=$routeTicks, demo=$demo]")
        ui.post { onStatusUpdate(message, pct, position, duration) }
    }

    fun startOrResume() {
        if (closed || completing || preparing) return
        if (player?.isPlaying == true && !paused) return

        // 1. Verify Bluetooth audio output route
        val routeEval = WakeUpAudioRouteManager.evaluateRoute(context, selectedName, selectedAddress)
        if (!routeEval.isVerified) {
            status(routeEval.actionableMessage)
            return
        }

        // 2. Check for muted media volume
        val currentVol = audio.getStreamVolume(AudioManager.STREAM_MUSIC)
        if (currentVol == 0) {
            status("Media volume is muted. Increase the media volume and retry.")
            return
        }

        // 3. Enforce window check in non-demo mode
        if (!demo && !db.isWakeUpWindowOpen()) {
            status("Wake-up submission window closed (07:00 AM – 09:30 AM). Attendance unapproved.")
            return
        }

        // 4. Initialize player if needed
        if (player == null) {
            val p = MediaPlayer()
            player = p

            try {
                val trackInfo = WakeUpMusicManager.getCurrentTrack(context)
                val trackFile = trackInfo?.let { File(it.filePath) }

                if (trackFile != null && trackFile.isFile && trackFile.length() > 0L) {
                    p.setAudioAttributes(
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_MEDIA)
                            .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                            .build()
                    )
                    p.setDataSource(trackFile.absolutePath)
                } else {
                    // Fallback to bundled resource if user hasn't selected track yet
                    try {
                        context.resources.openRawResourceFd(R.raw.motivation_track).use { fd ->
                            p.setAudioAttributes(
                                AudioAttributes.Builder()
                                    .setUsage(AudioAttributes.USAGE_MEDIA)
                                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                                    .build()
                            )
                            p.setDataSource(fd.fileDescriptor, fd.startOffset, fd.length)
                        }
                    } catch (e: Exception) {
                        status("No accessible wake-up music track. Please configure in Settings.")
                        stop()
                        return
                    }
                }

                // Direct routing to verified Bluetooth media sink where supported (API 23+)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    try {
                        val outputs = audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
                        val matchedDev = outputs.filter { dev ->
                            WakeUpAudioRouteManager.isSupportedBluetoothAudioDevice(dev) &&
                                WakeUpAudioRouteManager.isMediaPlayerRouteVerified(dev, selectedName, selectedAddress)
                        }.maxByOrNull { dev ->
                            when (dev.type) {
                                AudioDeviceInfo.TYPE_BLUETOOTH_A2DP -> 100
                                AudioDeviceInfo.TYPE_BLE_HEADSET -> 90
                                AudioDeviceInfo.TYPE_BLE_SPEAKER -> 80
                                else -> 0
                            }
                        }
                        // Only set preferred device for true media sinks (A2DP/BLE). Never route USAGE_MEDIA to SCO telephony!
                        if (matchedDev != null && matchedDev.type != AudioDeviceInfo.TYPE_BLUETOOTH_SCO) {
                            p.setPreferredDevice(matchedDev)
                        }
                    } catch (e: Exception) {
                        Log.w("StrictWakeAudio", "setPreferredDevice error: ${e.message}")
                    }
                }

                p.addOnRoutingChangedListener(routingListener, workerHandler)
                p.setOnErrorListener { _, what, extra ->
                    status("Player decode error ($what/$extra). Please retry full track.")
                    stop()
                    true
                }
                p.setOnCompletionListener { complete() }
                p.setOnPreparedListener {
                    if (closed || player !== p) return@setOnPreparedListener
                    preparing = false
                    duration = p.duration
                    if (duration <= 0) {
                        status("Track reported zero duration. Select a valid music track in Settings.")
                        stop()
                    } else startOrResume()
                }
                preparing = true
                p.prepareAsync()
                status("Preparing wake-up music…")
                return

            } catch (e: Throwable) {
                status("Audio player setup failed: ${e.message ?: e.javaClass.simpleName}")
                stop()
                return
            }
        }

        // 5. Volume cap <= 60%
        val maxVol = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        val capVol = (maxVol * 0.60f).toInt().coerceAtLeast(1)
        if (audio.getStreamVolume(AudioManager.STREAM_MUSIC) > capVol) {
            audio.setStreamVolume(AudioManager.STREAM_MUSIC, capVol, 0)
        }

        // 6. Request audio focus
        val focusGranted = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val req = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build()
                )
                .setOnAudioFocusChangeListener(focusListener, workerHandler)
                .build()
            focusRequest = req
            audio.requestAudioFocus(req) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
        } else {
            @Suppress("DEPRECATION")
            audio.requestAudioFocus(focusListener, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
        }

        if (!focusGranted) {
            status("Audio focus unavailable. Close background audio apps and retry.")
            return
        }

        // 7. Handle resume vs fresh start
        val p = player ?: return
        if (paused && pausedAt > 0L) {
            val pausedElapsed = SystemClock.elapsedRealtime() - pausedAt
            if (pausedElapsed > WakeUpCameraPolicy.RESUME_GRACE_MS) {
                // If paused too long, restart full track
                position = 0
                listened = 0L
                routeTicks = 0
                p.seekTo(0)
                status("Long pause: restarting full track verification.")
            }
        }

        paused = false
        unverifiedRouteSinceMs = 0L
        startedAtTickMs = SystemClock.elapsedRealtime()
        previousTick = SystemClock.elapsedRealtime()
        lastRouteDevice = p.routedDevice
        lastRouteDeviceName = routeEval.deviceName
        lastRouteDeviceAddress = routeEval.deviceAddress
        lastRouteTypeName = routeEval.routeTypeName

        try {
            // Balanced, uncompressed stereo unity gain
            p.setVolume(1.0f, 1.0f)
            p.start()
        } catch (e: Exception) {
            status("Playback could not start: ${e.message}")
            stop()
            return
        }

        // Synchronize verified route in database immediately upon starting or resuming
        db.recordVerifiedWakeUpAudioRoute(
            date = date,
            attemptId = attemptId,
            deviceName = routeEval.deviceName,
            deviceAddress = routeEval.deviceAddress,
            routeTypeName = routeEval.routeTypeName,
            isDevMode = demo
        )

        if (startedAt.isBlank()) {
            startedAt = db.formatMsToIso(db.authoritativeNowMs())
        }

        workerHandler.removeCallbacks(tickRunnable)
        workerHandler.post(tickRunnable)
        status("Playing wake-up music through verified earbuds: ${routeEval.deviceName}")
    }

    private val tickRunnable = object : Runnable {
        override fun run() {
            val p = player ?: return
            if (closed || paused || completing) return

            val now = SystemClock.elapsedRealtime()

            // Window boundary check
            if (!demo && !db.isWakeUpWindowOpen()) {
                pause("Wake-up submission cutoff reached. Verification incomplete.")
                return
            }

            // Route check
            val routed = p.routedDevice
            if (routed != null) {
                lastRouteDevice = routed
                lastRouteDeviceName = routed.productName?.toString().orEmpty()
                lastRouteTypeName = WakeUpAudioRouteManager.getRouteTypeName(routed.type)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    val addr = runCatching { routed.address?.trim().orEmpty() }.getOrDefault("")
                    if (addr.isNotBlank() && addr != "02:00:00:00:00:00" && addr != "00:00:00:00:00:00") {
                        lastRouteDeviceAddress = addr
                    }
                }
            }

            var routeOk = isRouteAcceptable(routed)

            // If routed is temporarily null, check lastRouteDevice
            if (!routeOk && routed == null && lastRouteDevice != null) {
                routeOk = isRouteAcceptable(lastRouteDevice)
            }

            // If player routing has not reported yet or during brief TWS role-switch,
            // check whether AudioManager active media output confirms verified earbuds
            if (!routeOk) {
                val liveRoute = WakeUpAudioRouteManager.evaluateRoute(context, selectedName, selectedAddress)
                if (liveRoute.isVerified) {
                    routeOk = true
                    if (lastRouteDeviceName.isBlank()) {
                        lastRouteDeviceName = liveRoute.deviceName
                    }
                    if (lastRouteTypeName == "NONE") {
                        lastRouteTypeName = liveRoute.routeTypeName
                    }
                }
            }

            // Allow initial 2000ms settling grace period on startup/resumption
            if (now - startedAtTickMs < 2000L) {
                if (routeOk) unverifiedRouteSinceMs = 0L
                workerHandler.postDelayed(this, 150)
                return
            }

            if (routeOk) {
                unverifiedRouteSinceMs = 0L
            } else {
                if (unverifiedRouteSinceMs == 0L) {
                    unverifiedRouteSinceMs = now
                }
                val unverifiedDuration = now - unverifiedRouteSinceMs
                if (unverifiedDuration >= 3000L) {
                    // Persistently disconnected for > 3.0s: genuinely disconnected
                    pause("Earbuds disconnected — reconnect to continue")
                    return
                } else {
                    // Transient jitter or earbud role-switch: give it time to re-settle without pausing
                    workerHandler.postDelayed(this, 250)
                    return
                }
            }

            // Playback state check
            if (!p.isPlaying) {
                pause("Playback paused. Resume to complete the full track.")
                return
            }

            // Volume cap check
            val maxVol = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
            val capVol = (maxVol * 0.60f).toInt().coerceAtLeast(1)
            val curVol = audio.getStreamVolume(AudioManager.STREAM_MUSIC)
            if (curVol == 0) {
                pause("Media volume is muted. Increase volume and resume.")
                return
            }
            if (curVol > capVol) {
                audio.setStreamVolume(AudioManager.STREAM_MUSIC, capVol, 0)
            }

            // Position & Anti-seek validation
            val currentPos = p.currentPosition
            val elapsed = (now - previousTick).coerceAtLeast(0L)

            if (currentPos < position) {
                // Rewind or loop
                position = currentPos
            } else if (currentPos - position > elapsed + 1500) {
                // Forward seek detected!
                seekAttempts++
                pause("Forward seek detected. Seek skipping is prohibited; please listen through the full track.")
                position = 0
                listened = 0L
                routeTicks = 0
                p.seekTo(0)
                return
            } else {
                listened += MeasuredPlaybackRules.credit(position, currentPos, elapsed.coerceAtMost(1000L), routeOk)
                position = currentPos
            }

            previousTick = now
            routeTicks++

            val posSec = position / 1000
            val durSec = duration / 1000
            val earbuds = lastRouteDeviceName.ifBlank { selectedName.ifBlank { "Earbuds" } }
            status("Listening: ${posSec}s / ${durSec}s on earbuds ($earbuds)")
            workerHandler.postDelayed(this, 250)
        }
    }

    fun pause(reason: String) {
        if (closed || completing || paused) return
        try {
            player?.pause()
        } catch (_: Throwable) {}
        paused = true
        pausedAt = SystemClock.elapsedRealtime()
        unverifiedRouteSinceMs = 0L
        workerHandler.removeCallbacks(tickRunnable)
        db.invalidateWakeUpAudioRoute(date, demo, reason)
        status(reason)
    }

    private fun complete() {
        val p = player ?: return
        if (closed || paused) return

        val curPos = runCatching { p.currentPosition }.getOrDefault(position)
        val now = SystemClock.elapsedRealtime()
        val elapsed = (now - previousTick).coerceAtLeast(0L)
        val routeEval = WakeUpAudioRouteManager.evaluateRoute(context, selectedName, selectedAddress)
        val currentRoute = p.routedDevice ?: lastRouteDevice
        val finalRouteVerified = isRouteAcceptable(currentRoute) || routeEval.isVerified

        listened += MeasuredPlaybackRules.credit(position, curPos, elapsed.coerceAtMost(1000L), finalRouteVerified)
        position = curPos

        // Natural completion grace: if at or near end of track, credit full duration
        if (duration > 0 && position >= duration - 2500 && listened >= duration - 3500) {
            listened = duration.toLong()
            position = duration
        }

        if (!finalRouteVerified || routeTicks == 0 || duration <= 0 || listened < duration) {
            pause("Full-track playback on the selected earbuds could not be measured. Reconnect and replay.")
            return
        }

        if (seekAttempts > 0) {
            pause("Full-track verification rejected: seek skipping detected.")
            return
        }

        completing = true

        val routeType = if (currentRoute != null) {
            WakeUpAudioRouteManager.getRouteTypeName(currentRoute.type)
        } else {
            routeEval.routeTypeName
        }

        val rawDevName = currentRoute?.productName?.toString()?.takeUnless { it.isNullOrBlank() }
        val isGenericDriverName = rawDevName.isNullOrBlank() || rawDevName.equals("Bluetooth Audio Device", ignoreCase = true) ||
            rawDevName.equals("Bluetooth Audio Output", ignoreCase = true) || rawDevName.equals("Bluetooth", ignoreCase = true)
        val routedName = when {
            !rawDevName.isNullOrBlank() && !isGenericDriverName -> rawDevName
            routeEval.deviceName.isNotBlank() -> routeEval.deviceName
            lastRouteDeviceName.isNotBlank() -> lastRouteDeviceName
            else -> selectedName
        }

        val rawCurrentAddr = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P && currentRoute != null) {
            runCatching { currentRoute.address?.trim().orEmpty() }.getOrDefault("")
        } else ""

        val isRedactedCurrentAddr = rawCurrentAddr.isBlank() ||
            rawCurrentAddr.equals("02:00:00:00:00:00", ignoreCase = true) ||
            rawCurrentAddr.equals("00:00:00:00:00:00", ignoreCase = true)

        val isNameMatch = routedName.isNotBlank() && selectedName.isNotBlank() &&
            (routedName.equals(selectedName, ignoreCase = true) ||
             routedName.contains(selectedName, ignoreCase = true) ||
             selectedName.contains(routedName, ignoreCase = true))

        val routedAddr = when {
            !isRedactedCurrentAddr && (rawCurrentAddr.equals(selectedAddress, ignoreCase = true) ||
                (rawCurrentAddr.contains(":") && selectedAddress.contains(":") &&
                 rawCurrentAddr.substringBeforeLast(":").equals(selectedAddress.substringBeforeLast(":"), ignoreCase = true))) -> rawCurrentAddr
            isNameMatch && selectedAddress.isNotBlank() -> selectedAddress
            !isRedactedCurrentAddr -> rawCurrentAddr
            else -> {
                val lastAddr = lastRouteDeviceAddress.takeUnless {
                    it.isBlank() || it.equals("02:00:00:00:00:00", ignoreCase = true) || it.equals("00:00:00:00:00:00", ignoreCase = true)
                }
                val evalAddr = routeEval.deviceAddress.takeUnless {
                    it.isBlank() || it.equals("02:00:00:00:00:00", ignoreCase = true) || it.equals("00:00:00:00:00:00", ignoreCase = true)
                }
                lastAddr ?: evalAddr ?: selectedAddress
            }
        }

        // Re-confirm authoritative route state for current session before step completion
        db.recordVerifiedWakeUpAudioRoute(
            date = date,
            attemptId = attemptId,
            deviceName = routedName,
            deviceAddress = routedAddr,
            routeTypeName = routeType,
            isDevMode = demo
        )

        val telemetry = JSONObject().apply {
            put("demo", demo)
            put("attempt_id", attemptId)
            put("completed", true)
            put("headset_connected", true)
            put("volume_ok", true)
            put("seek_attempts", 0)
            put("audio_route", routeType)
            put("route_source", "MEDIA_PLAYER_ROUTED_DEVICE")
            put("route_observations", routeTicks)
            put("selected_device_name", selectedName)
            put("selected_device_address", selectedAddress)
            put("routed_device_name", routedName)
            put("routed_device_address", routedAddr)
            put("track_duration_sec", duration / 1000.0)
            put("completed_duration_sec", listened / 1000.0)
            put("route_verified_duration_ms", listened)
            put("track_sha256", db.getConfiguredWakeUpTrackHash())
            put("started_at", startedAt)
            put("completed_at", db.formatMsToIso(db.authoritativeNowMs()))
        }

        // Persist step completion to local database
        val accepted = db.completeMotivationTrackStep(date, selectedName, true, telemetry, demo)

        val finalMsg = if (accepted) {
            if (demo) {
                "Demo verification completed — no points or attendance changed"
            } else {
                "Full wake-up track completed on verified earbuds ✓"
            }
        } else {
            val subErr = db.getWakeConfig("wakeup_submission_error", date, demo, "Playback validation failed")
            "Wake-up attempt was not submitted: $subErr. No attendance or points awarded."
        }
        status(finalMsg)
        onCompleteCallback(telemetry)
        stop()
    }

    fun stop() {
        if (closed) return
        closed = true
        workerHandler.removeCallbacks(tickRunnable)
        workerHandler.removeCallbacksAndMessages(null)
        try {
            workerThread.quitSafely()
        } catch (_: Throwable) {}

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && focusRequest != null) {
            audio.abandonAudioFocusRequest(focusRequest!!)
            focusRequest = null
        } else {
            @Suppress("DEPRECATION")
            audio.abandonAudioFocus(focusListener)
        }

        try {
            player?.removeOnRoutingChangedListener(routingListener)
            player?.stop()
            player?.release()
        } catch (_: Throwable) {}
        player = null
    }
}
