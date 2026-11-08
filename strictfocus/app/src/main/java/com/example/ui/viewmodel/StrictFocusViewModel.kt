package com.example.ui.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.data.model.AppRestriction
import com.example.data.model.ConnectedDevice
import com.example.data.model.FocusSession
import com.example.data.model.HourlyCheckSlot
import com.example.data.model.RewardPrivilege
import com.example.data.model.RobotMotionMode
import com.example.data.model.SlotStatus
import com.example.data.model.SupervisorState
import com.example.voice.HumanSpeechLibrary
import com.example.voice.HumanSpeechQuote
import com.example.voice.StrictBotVoiceManager
import com.example.voice.VoicePersona
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

enum class AppTab(val label: String) {
    HOME("Home"),
    TASKS("Tasks"),
    FOCUS("Focus"),
    REWARDS("Rewards"),
    DEVICES("Devices")
}

data class StrictFocusUiState(
    val currentTab: AppTab = AppTab.HOME,
    val streakDays: Int = 12,
    val streakDaysTotalGoal: Int = 14,
    val todayScore: Int = 80,
    val maxScore: Int = 100,
    val isStrictActive: Boolean = true,
    val nextHourlyWindow: String = "16:00 – 17:00",
    val countdownSeconds: Int = 23 * 60 + 27, // 23:27
    val digitalTime: String = "09:30:00 PM",
    val digitalTime24: String = "21:30:00",
    val currentDate: String = "Sat, Oct 10",
    val selectedHour: Int = 9,          // 1 - 12
    val selectedMinute: Int = 30,       // 0 - 59
    val selectedIsAm: Boolean = false,  // false = PM, true = AM
    val selectedDay: Int = 10,          // 1 - 31
    val selectedMonth: Int = 10,        // 1 - 12
    val selectedYear: Int = 2026,       // 2024 - 2030
    val isTimelineOverride: Boolean = false,
    val isArmingFocus: Boolean = false,
    val activeFocusSession: FocusSession = FocusSession(),
    val robotMotionMode: RobotMotionMode = RobotMotionMode.HOVER,
    val isSpeakingVoice: Boolean = false,
    val activeSpokenPhrase: String? = null,
    val voiceAmplitudes: List<Float> = listOf(0.15f, 0.2f, 0.15f, 0.2f, 0.15f),
    val selectedVoicePersona: VoicePersona = VoicePersona.COACH,
    val customSpeechInput: String = "Lok, stay disciplined! We have goals to crush today.",
    val appRestrictions: List<AppRestriction> = listOf(
        AppRestriction(id = "youtube", appName = "YouTube", iconType = "youtube", isLocked = true, unlockCostPoints = 50),
        AppRestriction(id = "instagram", appName = "Instagram", iconType = "instagram", isLocked = true, unlockCostPoints = 40),
        AppRestriction(id = "tiktok", appName = "TikTok", iconType = "tiktok", isLocked = true, unlockCostPoints = 60),
        AppRestriction(id = "reddit", appName = "Reddit", iconType = "reddit", isLocked = true, unlockCostPoints = 30)
    ),
    val devices: List<ConnectedDevice> = listOf(
        ConnectedDevice(
            id = "phone",
            name = "Lok's Phone",
            subtitle = "Pixel 8 Pro • Strict v3.2",
            isLockedDown = true,
            pingMs = 18,
            lastSyncText = "Strict lockdown sync active"
        ),
        ConnectedDevice(
            id = "pc",
            name = "Windows PC",
            subtitle = "Alienware R16 • Daemon v2.8",
            isLockedDown = true,
            pingMs = 24,
            lastSyncText = "12 domains & 4 games blocked"
        ),
        ConnectedDevice(
            id = "airpods",
            name = "AirPods Pro 2",
            subtitle = "Spatial Presence Telemetry",
            isLockedDown = true,
            pingMs = 9,
            lastSyncText = "Ear-detection: In-ear focused"
        )
    ),
    val hourlySlots: List<HourlyCheckSlot> = listOf(
        HourlyCheckSlot(
            id = "slot_1",
            timeRange = "08:00 – 09:00",
            title = "Morning Wake-Up Protocol",
            points = 20,
            status = SlotStatus.VERIFIED,
            verificationType = "Cold Water Splash & Desk Scan",
            verifiedAtTime = "08:14",
            proofSummary = "StrictBot approved daylight optical proof"
        ),
        HourlyCheckSlot(
            id = "slot_2",
            timeRange = "12:00 – 13:00",
            title = "Midday Sprint Recalibration",
            points = 20,
            status = SlotStatus.VERIFIED,
            verificationType = "Clean Desk & Water Intake Log",
            verifiedAtTime = "12:08",
            proofSummary = "Zero distraction telemetry verified"
        ),
        HourlyCheckSlot(
            id = "slot_3",
            timeRange = "16:00 – 17:00",
            title = "Next Hourly Check",
            points = 20,
            status = SlotStatus.UPCOMING,
            verificationType = "Camera Proof + AirPods Handshake"
        ),
        HourlyCheckSlot(
            id = "slot_4",
            timeRange = "20:00 – 21:00",
            title = "Evening Reflection & Review",
            points = 20,
            status = SlotStatus.PENDING,
            verificationType = "Daily Metrics & Log Signature"
        ),
        HourlyCheckSlot(
            id = "slot_5",
            timeRange = "23:00 – 06:00",
            title = "Hard Cyber Sleep Lock",
            points = 20,
            status = SlotStatus.PENDING,
            verificationType = "Hardware Bedside Docking"
        )
    ),
    val rewardPrivileges: List<RewardPrivilege> = listOf(
        RewardPrivilege(
            id = "rew_yt",
            title = "30m YouTube Pass",
            description = "Unlocks YouTube on Phone + PC for educational or entertainment recovery",
            cost = 50,
            iconName = "smart_display",
            category = "Media",
            isUnlocked = false
        ),
        RewardPrivilege(
            id = "rew_ig",
            title = "15m Instagram Window",
            description = "Quick feed scan & DM reply window with strict auto-lock timer",
            cost = 40,
            iconName = "photo_camera",
            category = "Social",
            isUnlocked = false
        ),
        RewardPrivilege(
            id = "rew_game",
            title = "45m Gaming Protocol",
            description = "Unlocks Steam and Discord on Windows PC for one ranked match",
            cost = 80,
            iconName = "sports_esports",
            category = "Entertainment",
            isUnlocked = false
        ),
        RewardPrivilege(
            id = "rew_cheat",
            title = "Cheat Meal Authorization",
            description = "Supervisor AI approved guilt-free nutritional cheat pass",
            cost = 120,
            iconName = "restaurant",
            category = "Lifestyle",
            isUnlocked = false
        )
    ),
    val userPointsBank: Int = 340,
    val activeDialog: String? = null, // "RULES", "SUPERVISOR", "WAKEUP", "TRACK", "VERIFY"
    val snackbarMessage: String? = null
)

class StrictFocusViewModel(application: Application) : AndroidViewModel(application) {

    private val _uiState = MutableStateFlow(StrictFocusUiState())
    val uiState: StateFlow<StrictFocusUiState> = _uiState.asStateFlow()

    private val voiceManager = StrictBotVoiceManager(application)

    init {
        startGlobalTickers()
        observeVoiceState()
    }

    private fun observeVoiceState() {
        viewModelScope.launch {
            voiceManager.isSpeaking.collect { speaking ->
                _uiState.update { it.copy(isSpeakingVoice = speaking) }
            }
        }
        viewModelScope.launch {
            voiceManager.currentSpokenText.collect { phrase ->
                _uiState.update { it.copy(activeSpokenPhrase = phrase) }
            }
        }
        viewModelScope.launch {
            voiceManager.waveAmplitudes.collect { waves ->
                _uiState.update { it.copy(voiceAmplitudes = waves) }
            }
        }
    }

    private fun startGlobalTickers() {
        val timeFormat = SimpleDateFormat("hh:mm:ss a", Locale.getDefault())
        val timeFormat24 = SimpleDateFormat("HH:mm:ss", Locale.getDefault())
        val dateFormat = SimpleDateFormat("EEE, MMM d", Locale.getDefault())

        // Initial sync of real-time clock
        val initialDate = Date()
        val initialCal = Calendar.getInstance()
        val initHour = initialCal.get(Calendar.HOUR).let { if (it == 0) 12 else it }
        val initMin = initialCal.get(Calendar.MINUTE)
        val initIsAm = initialCal.get(Calendar.AM_PM) == Calendar.AM
        val initDay = initialCal.get(Calendar.DAY_OF_MONTH)
        val initMonth = initialCal.get(Calendar.MONTH) + 1
        val initYear = initialCal.get(Calendar.YEAR)

        _uiState.update { state ->
            state.copy(
                digitalTime = timeFormat.format(initialDate),
                digitalTime24 = timeFormat24.format(initialDate),
                currentDate = dateFormat.format(initialDate),
                selectedHour = initHour,
                selectedMinute = initMin,
                selectedIsAm = initIsAm,
                selectedDay = initDay,
                selectedMonth = initMonth,
                selectedYear = initYear
            )
        }

        // Live 1-second system clock & countdown ticker
        viewModelScope.launch {
            while (true) {
                delay(1000)
                val now = Date()
                val formattedTime = timeFormat.format(now)
                val formatted24 = timeFormat24.format(now)
                val formattedDate = dateFormat.format(now)

                _uiState.update { state ->
                    val newCount = if (state.countdownSeconds > 0) state.countdownSeconds - 1 else 3600
                    if (!state.isTimelineOverride) {
                        val cal = Calendar.getInstance()
                        val h = cal.get(Calendar.HOUR).let { if (it == 0) 12 else it }
                        val m = cal.get(Calendar.MINUTE)
                        val isAm = cal.get(Calendar.AM_PM) == Calendar.AM
                        val d = cal.get(Calendar.DAY_OF_MONTH)
                        val mo = cal.get(Calendar.MONTH) + 1
                        val y = cal.get(Calendar.YEAR)

                        state.copy(
                            countdownSeconds = newCount,
                            digitalTime = formattedTime,
                            digitalTime24 = formatted24,
                            currentDate = formattedDate,
                            selectedHour = h,
                            selectedMinute = m,
                            selectedIsAm = isAm,
                            selectedDay = d,
                            selectedMonth = mo,
                            selectedYear = y
                        )
                    } else {
                        state.copy(countdownSeconds = newCount)
                    }
                }
            }
        }

        // Active Focus session timer ticker
        viewModelScope.launch {
            while (true) {
                delay(1000)
                _uiState.update { state ->
                    if (state.activeFocusSession.isActive && state.activeFocusSession.remainingSeconds > 0) {
                        val updatedRemaining = state.activeFocusSession.remainingSeconds - 1
                        val session = state.activeFocusSession.copy(remainingSeconds = updatedRemaining)
                        if (updatedRemaining == 0) {
                            // Session completed! Award points
                            val newScore = (state.todayScore + 10).coerceAtMost(100)
                            val newBank = state.userPointsBank + 15
                            state.copy(
                                activeFocusSession = session.copy(isActive = false),
                                todayScore = newScore,
                                userPointsBank = newBank,
                                snackbarMessage = "Focus Session Completed! +10 Score, +15 Points"
                            )
                        } else {
                            state.copy(activeFocusSession = session)
                        }
                    } else {
                        state
                    }
                }
            }
        }
    }

    fun selectTab(tab: AppTab) {
        _uiState.update { it.copy(currentTab = tab) }
    }

    fun startFocusSessionFromDashboard() {
        viewModelScope.launch {
            _uiState.update { it.copy(isArmingFocus = true) }
            delay(900)
            _uiState.update { state ->
                state.copy(
                    isArmingFocus = false,
                    currentTab = AppTab.FOCUS,
                    activeFocusSession = state.activeFocusSession.copy(
                        isActive = true,
                        supervisorState = SupervisorState.VIGILANT
                    ),
                    snackbarMessage = "Strict Focus Rig Armed! Distractions suppressed."
                )
            }
        }
    }

    fun toggleFocusSession() {
        _uiState.update { state ->
            val wasActive = state.activeFocusSession.isActive
            val newSession = state.activeFocusSession.copy(
                isActive = !wasActive,
                supervisorState = if (!wasActive) SupervisorState.VIGILANT else SupervisorState.RESTING
            )
            state.copy(
                activeFocusSession = newSession,
                snackbarMessage = if (!wasActive) "Focus session running" else "Focus session paused"
            )
        }
    }

    fun setFocusPreset(minutes: Int) {
        val seconds = minutes * 60
        _uiState.update { state ->
            state.copy(
                activeFocusSession = state.activeFocusSession.copy(
                    remainingSeconds = seconds,
                    totalSeconds = seconds,
                    presetLabel = "${minutes}m Session",
                    isActive = false
                )
            )
        }
    }

    fun cycleSoundMode() {
        val modes = listOf("Binaural Focus 40Hz", "Cyber Rain Synth", "White Noise Core", "Mute / Pure Silence")
        _uiState.update { state ->
            val currentIdx = modes.indexOf(state.activeFocusSession.soundMode).takeIf { it >= 0 } ?: 0
            val nextMode = modes[(currentIdx + 1) % modes.size]
            state.copy(
                activeFocusSession = state.activeFocusSession.copy(soundMode = nextMode),
                snackbarMessage = "Audio Rig: $nextMode"
            )
        }
    }

    fun emergencyStopFocus() {
        _uiState.update { state ->
            state.copy(
                activeFocusSession = state.activeFocusSession.copy(
                    isActive = false,
                    supervisorState = SupervisorState.WARNING
                ),
                snackbarMessage = "Session terminated. StrictBot logged interruption."
            )
        }
    }

    fun performHourlyVerification(slotId: String) {
        _uiState.update { state ->
            val updatedSlots = state.hourlySlots.map { slot ->
                if (slot.id == slotId) {
                    slot.copy(
                        status = SlotStatus.VERIFIED,
                        verifiedAtTime = "16:02",
                        proofSummary = "StrictBot AI verified optical desk integrity"
                    )
                } else slot
            }
            val newScore = (state.todayScore + 20).coerceAtMost(100)
            val newBank = state.userPointsBank + 25
            state.copy(
                hourlySlots = updatedSlots,
                todayScore = newScore,
                userPointsBank = newBank,
                activeDialog = null,
                snackbarMessage = "Verification Approved! +20 Daily Score, +25 Bank Points"
            )
        }
    }

    fun unlockAppTemporary(appId: String) {
        val app = _uiState.value.appRestrictions.find { it.id == appId } ?: return
        if (_uiState.value.userPointsBank < app.unlockCostPoints) {
            _uiState.update { it.copy(snackbarMessage = "Insufficient points! Complete hourly checks first.") }
            return
        }

        _uiState.update { state ->
            val updatedApps = state.appRestrictions.map {
                if (it.id == appId) it.copy(isLocked = false, minutesAllowanceRemaining = 30) else it
            }
            state.copy(
                appRestrictions = updatedApps,
                userPointsBank = state.userPointsBank - app.unlockCostPoints,
                snackbarMessage = "Unlocked ${app.appName} for 30 minutes! (-${app.unlockCostPoints} pts)"
            )
        }
    }

    fun toggleAppLock(appId: String) {
        _uiState.update { state ->
            val updated = state.appRestrictions.map {
                if (it.id == appId) it.copy(isLocked = !it.isLocked) else it
            }
            state.copy(appRestrictions = updated)
        }
    }

    fun redeemReward(rewardId: String) {
        val reward = _uiState.value.rewardPrivileges.find { it.id == rewardId } ?: return
        if (_uiState.value.userPointsBank < reward.cost) {
            _uiState.update { it.copy(snackbarMessage = "Need ${reward.cost - it.userPointsBank} more points to redeem!") }
            return
        }

        _uiState.update { state ->
            val updatedList = state.rewardPrivileges.map {
                if (it.id == rewardId) it.copy(isUnlocked = true) else it
            }
            state.copy(
                rewardPrivileges = updatedList,
                userPointsBank = state.userPointsBank - reward.cost,
                snackbarMessage = "Redeemed ${reward.title}! Strict authorization granted."
            )
        }
    }

    fun toggleDeviceLock(deviceId: String) {
        _uiState.update { state ->
            val updated = state.devices.map {
                if (it.id == deviceId) it.copy(isLockedDown = !it.isLockedDown) else it
            }
            state.copy(
                devices = updated,
                snackbarMessage = "Device sync protocol updated"
            )
        }
    }

    fun updateHour(hour: Int) {
        _uiState.update { state ->
            val updated = state.copy(
                selectedHour = hour.coerceIn(1, 12),
                isTimelineOverride = true
            )
            recalculateCustomChronoState(updated)
        }
    }

    fun updateMinute(minute: Int) {
        _uiState.update { state ->
            val updated = state.copy(
                selectedMinute = minute.coerceIn(0, 59),
                isTimelineOverride = true
            )
            recalculateCustomChronoState(updated)
        }
    }

    fun updateAmPm(isAm: Boolean) {
        _uiState.update { state ->
            val updated = state.copy(
                selectedIsAm = isAm,
                isTimelineOverride = true
            )
            recalculateCustomChronoState(updated)
        }
    }

    fun updateDay(day: Int) {
        _uiState.update { state ->
            val updated = state.copy(
                selectedDay = day.coerceIn(1, 31),
                isTimelineOverride = true
            )
            recalculateCustomChronoState(updated)
        }
    }

    fun updateMonth(month: Int) {
        _uiState.update { state ->
            val updated = state.copy(
                selectedMonth = month.coerceIn(1, 12),
                isTimelineOverride = true
            )
            recalculateCustomChronoState(updated)
        }
    }

    fun updateYear(year: Int) {
        _uiState.update { state ->
            val updated = state.copy(
                selectedYear = year.coerceIn(2024, 2030),
                isTimelineOverride = true
            )
            recalculateCustomChronoState(updated)
        }
    }

    fun syncToLiveClock() {
        val cal = Calendar.getInstance()
        val curHour = cal.get(Calendar.HOUR).let { if (it == 0) 12 else it }
        val curMinute = cal.get(Calendar.MINUTE)
        val curIsAm = cal.get(Calendar.AM_PM) == Calendar.AM
        val curDay = cal.get(Calendar.DAY_OF_MONTH)
        val curMonth = cal.get(Calendar.MONTH) + 1
        val curYear = cal.get(Calendar.YEAR)

        val timeFormat = SimpleDateFormat("hh:mm:ss a", Locale.getDefault())
        val timeFormat24 = SimpleDateFormat("HH:mm:ss", Locale.getDefault())
        val dateFormat = SimpleDateFormat("EEE, MMM d", Locale.getDefault())
        val now = Date()

        _uiState.update { state ->
            state.copy(
                isTimelineOverride = false,
                selectedHour = curHour,
                selectedMinute = curMinute,
                selectedIsAm = curIsAm,
                selectedDay = curDay,
                selectedMonth = curMonth,
                selectedYear = curYear,
                digitalTime = timeFormat.format(now),
                digitalTime24 = timeFormat24.format(now),
                currentDate = dateFormat.format(now),
                snackbarMessage = "Synchronized to live system clock"
            )
        }
    }

    private fun recalculateCustomChronoState(state: StrictFocusUiState): StrictFocusUiState {
        val hour24 = if (state.selectedIsAm) {
            if (state.selectedHour == 12) 0 else state.selectedHour
        } else {
            if (state.selectedHour == 12) 12 else state.selectedHour + 12
        }

        val digitalTime = String.format(Locale.getDefault(), "%02d:%02d:00 %s", state.selectedHour, state.selectedMinute, if (state.selectedIsAm) "AM" else "PM")
        val digitalTime24 = String.format(Locale.getDefault(), "%02d:%02d:00", hour24, state.selectedMinute)

        val cal = Calendar.getInstance()
        cal.set(Calendar.YEAR, state.selectedYear)
        cal.set(Calendar.MONTH, (state.selectedMonth - 1).coerceIn(0, 11))
        val maxDays = cal.getActualMaximum(Calendar.DAY_OF_MONTH)
        val clampedDay = state.selectedDay.coerceIn(1, maxDays)
        cal.set(Calendar.DAY_OF_MONTH, clampedDay)

        val dateFormat = SimpleDateFormat("EEE, MMM d", Locale.getDefault())
        val currentDate = dateFormat.format(cal.time)

        return state.copy(
            selectedDay = clampedDay,
            digitalTime = digitalTime,
            digitalTime24 = digitalTime24,
            currentDate = currentDate
        )
    }

    fun showDialog(dialogName: String) {
        _uiState.update { it.copy(activeDialog = dialogName) }
    }

    fun dismissDialog() {
        _uiState.update { it.copy(activeDialog = null) }
    }

    fun clearSnackbar() {
        _uiState.update { it.copy(snackbarMessage = null) }
    }

    fun setRobotMotionMode(mode: RobotMotionMode) {
        _uiState.update { it.copy(robotMotionMode = mode) }
        when (mode) {
            RobotMotionMode.FLY -> {
                voiceManager.speak(
                    "Thrusters at maximum velocity! Whooooosh! Freedom is on the other side of hard work!",
                    _uiState.value.selectedVoicePersona.pitch,
                    _uiState.value.selectedVoicePersona.speechRate
                )
            }
            RobotMotionMode.WALK -> {
                voiceManager.speak(
                    "Stepping down for perimeter patrol. Left, right, left, right. Everything is locked down and secure.",
                    _uiState.value.selectedVoicePersona.pitch,
                    _uiState.value.selectedVoicePersona.speechRate
                )
            }
            RobotMotionMode.HOVER -> {
                voiceManager.speak(
                    "Hover thrusters stabilized. AI supervisor standing by.",
                    _uiState.value.selectedVoicePersona.pitch,
                    _uiState.value.selectedVoicePersona.speechRate
                )
            }
        }
    }

    fun speakHumanVoice(text: String) {
        voiceManager.speak(
            text,
            _uiState.value.selectedVoicePersona.pitch,
            _uiState.value.selectedVoicePersona.speechRate
        )
    }

    fun speakRandomHumanQuote() {
        val quote = HumanSpeechLibrary.random()
        if (quote.actionCommand == "FLY") {
            _uiState.update { it.copy(robotMotionMode = RobotMotionMode.FLY) }
        } else if (quote.actionCommand == "WALK") {
            _uiState.update { it.copy(robotMotionMode = RobotMotionMode.WALK) }
        }
        voiceManager.speak(
            quote.text,
            _uiState.value.selectedVoicePersona.pitch,
            _uiState.value.selectedVoicePersona.speechRate
        )
    }

    fun stopVoice() {
        voiceManager.stop()
    }

    fun setVoicePersona(persona: VoicePersona) {
        _uiState.update { it.copy(selectedVoicePersona = persona) }
    }

    fun updateCustomSpeechInput(text: String) {
        _uiState.update { it.copy(customSpeechInput = text) }
    }

    override fun onCleared() {
        super.onCleared()
        voiceManager.shutdown()
    }
}
