package com.example.ui.screens

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Air
import androidx.compose.material.icons.filled.Alarm
import androidx.compose.material.icons.filled.Bathtub
import androidx.compose.material.icons.filled.CalendarToday
import androidx.compose.material.icons.filled.CenterFocusStrong
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.DirectionsCar
import androidx.compose.material.icons.filled.DirectionsWalk
import androidx.compose.material.icons.filled.FitnessCenter
import androidx.compose.material.icons.filled.FlightTakeoff
import androidx.compose.material.icons.filled.HourglassTop
import androidx.compose.material.icons.filled.LocalFireDepartment
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.MilitaryTech
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.RecordVoiceOver
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.SmartDisplay
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.WbSunny
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.R
import com.example.data.model.RobotMotionMode
import com.example.data.model.SupervisorState
import com.example.ui.components.LivingStrictBot
import com.example.ui.components.StrictHeader
import com.example.ui.theme.BgCanvas
import com.example.ui.theme.BorderSubtle
import com.example.ui.theme.JetBrainsMono
import com.example.ui.theme.Manrope
import com.example.ui.theme.OnPrimaryFixed
import com.example.ui.theme.PinkAccent
import com.example.ui.theme.PrimaryNeon
import com.example.ui.theme.SecondaryCyan
import com.example.ui.theme.SpaceGrotesk
import com.example.ui.theme.StatusDanger
import com.example.ui.theme.StatusLocked
import com.example.ui.theme.StatusWarning
import com.example.ui.theme.SurfaceCard
import com.example.ui.theme.SurfaceContainerHigh
import com.example.ui.theme.SurfaceElevated
import com.example.ui.theme.TextMuted
import com.example.ui.theme.TextPrimary
import com.example.ui.theme.TextSecondary
import com.example.ui.viewmodel.AppTab
import com.example.ui.viewmodel.StrictFocusUiState
import com.example.ui.viewmodel.StrictFocusViewModel

@Composable
fun DashboardScreen(
    uiState: StrictFocusUiState,
    viewModel: StrictFocusViewModel,
    modifier: Modifier = Modifier
) {
    val scrollState = rememberScrollState()

    // Formatted countdown mm:ss
    val minutes = uiState.countdownSeconds / 60
    val seconds = uiState.countdownSeconds % 60
    val countdownFormatted = String.format("%02d:%02d", minutes, seconds)

    // Pulse animation for live indicators
    val infiniteTransition = rememberInfiniteTransition(label = "dash_pulse")
    val pulseAlpha by infiniteTransition.animateFloat(
        initialValue = 0.4f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(1000, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "pulse_alpha"
    )

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(BgCanvas)
    ) {
        // Sticky/Top Header Bar
        StrictHeader(
            title = "DASHBOARD",
            statusBadge = if (uiState.isStrictActive) "STRICT ACTIVE" else "IDLE",
            streakDays = uiState.streakDaysTotalGoal,
            digitalTime = uiState.digitalTime24,
            onAvatarClick = { viewModel.showDialog("SUPERVISOR") }
        )

        // Main Scrollable Body
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(scrollState)
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            // 1. Supervisor Mascot & Context Greeting Header
            SupervisorHeroCard(
                currentDate = uiState.currentDate,
                digitalTime = uiState.digitalTime,
                uiState = uiState,
                viewModel = viewModel,
                onChronoClick = { viewModel.showDialog("CHRONO") },
                onMascotClick = { viewModel.showDialog("SUPERVISOR") }
            )

            // Digital Clock Telemetry HUD with Chrono Sliders access
            DigitalClockTelemetryCard(
                digitalTime = uiState.digitalTime,
                digitalTime24 = uiState.digitalTime24,
                isOverride = uiState.isTimelineOverride,
                onChronoClick = { viewModel.showDialog("CHRONO") }
            )

            // 2. Key Metrics Grid (Streak & Daily Score)
            KeyMetricsGrid(
                streakDays = uiState.streakDays,
                todayScore = uiState.todayScore,
                maxScore = uiState.maxScore
            )

            // 3. Next Hourly Check Banner
            HourlyCheckBanner(
                timeWindow = uiState.nextHourlyWindow,
                countdownText = countdownFormatted,
                digitalTime = uiState.digitalTime,
                pulseAlpha = pulseAlpha,
                onClick = { viewModel.showDialog("VERIFY") }
            )

            // 4. Quick Actions Grid: Wake Up, Task, Gym, Bath
            QuickActionsSection(
                onWakeUpClick = { viewModel.showDialog("WAKEUP") },
                onTaskClick = { viewModel.selectTab(AppTab.TASKS) },
                onGymClick = { viewModel.showDialog("GYM") },
                onBathClick = { viewModel.showDialog("BATH") }
            )

            // 6. Active Restrictions Card
            ActiveRestrictionsCard(
                onViewRulesClick = { viewModel.showDialog("RULES") },
                onAppClick = { appId -> viewModel.unlockAppTemporary(appId) }
            )

            // 7. Connected Devices Telemetry Card
            ConnectedDevicesSummaryCard(
                onClick = { viewModel.selectTab(AppTab.DEVICES) }
            )

            Spacer(modifier = Modifier.height(16.dp))
        }
    }
}

@Composable
private fun SupervisorHeroCard(
    currentDate: String,
    digitalTime: String,
    uiState: StrictFocusUiState,
    viewModel: StrictFocusViewModel,
    onChronoClick: () -> Unit = {},
    onMascotClick: () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .testTag("supervisor_hero_card"),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = SurfaceCard),
        elevation = CardDefaults.cardElevation(defaultElevation = 4.dp)
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp)
        ) {
            // Ambient Cyber Background Glows
            Box(
                modifier = Modifier
                    .size(160.dp)
                    .align(Alignment.TopEnd)
                    .background(
                        Brush.radialGradient(
                            colors = listOf(PrimaryNeon.copy(alpha = 0.15f), Color.Transparent)
                        )
                    )
            )

            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    // Left Column: Date badge, digital clock badge, greeting, subtitle, and live AI badge
                    Column(
                        modifier = Modifier.weight(1f),
                        verticalArrangement = Arrangement.spacedBy(4.dp)
                    ) {
                        // Badges: Date & Digital Time
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            // Date Badge - clickable to adjust calendar sliders
                            Row(
                                modifier = Modifier
                                    .clip(RoundedCornerShape(9999.dp))
                                    .background(SurfaceElevated)
                                    .clickable { onChronoClick() }
                                    .padding(horizontal = 9.dp, vertical = 3.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(4.dp)
                            ) {
                                Icon(
                                    imageVector = Icons.Default.CalendarToday,
                                    contentDescription = "Calendar Sliders",
                                    tint = SecondaryCyan,
                                    modifier = Modifier.size(12.dp)
                                )
                                Text(
                                    text = currentDate,
                                    fontFamily = JetBrainsMono,
                                    fontWeight = FontWeight.Medium,
                                    fontSize = 11.sp,
                                    letterSpacing = 0.4.sp,
                                    color = TextSecondary
                                )
                            }

                            // Digital Clock Pill - clickable to adjust time sliders
                            Row(
                                modifier = Modifier
                                    .clip(RoundedCornerShape(9999.dp))
                                    .background(SurfaceElevated)
                                    .border(1.dp, PrimaryNeon.copy(alpha = 0.35f), RoundedCornerShape(9999.dp))
                                    .clickable { onChronoClick() }
                                    .padding(horizontal = 9.dp, vertical = 3.dp)
                                    .testTag("digital_clock_pill"),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(4.dp)
                            ) {
                                Icon(
                                    imageVector = Icons.Default.Schedule,
                                    contentDescription = "Digital Clock Sliders",
                                    tint = PrimaryNeon,
                                    modifier = Modifier.size(12.dp)
                                )
                                Text(
                                    text = digitalTime,
                                    fontFamily = JetBrainsMono,
                                    fontWeight = FontWeight.Bold,
                                    fontSize = 11.sp,
                                    letterSpacing = 0.5.sp,
                                    color = PrimaryNeon
                                )
                            }
                        }

                        Spacer(modifier = Modifier.height(2.dp))

                        Text(
                            text = "Good afternoon",
                            fontFamily = SpaceGrotesk,
                            fontWeight = FontWeight.Bold,
                            fontSize = 22.sp,
                            letterSpacing = (-0.5).sp,
                            color = TextPrimary
                        )

                        Text(
                            text = "Let’s stay disciplined today!",
                            fontFamily = Manrope,
                            fontWeight = FontWeight.Normal,
                            fontSize = 13.sp,
                            color = TextMuted
                        )

                        Spacer(modifier = Modifier.height(4.dp))

                        // Subtle AI Supervisor Live Status
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(7.dp)
                                    .background(PrimaryNeon, CircleShape)
                            )
                            Text(
                                text = "StrictBot AI active • Synced",
                                fontFamily = Manrope,
                                fontWeight = FontWeight.SemiBold,
                                fontSize = 11.sp,
                                color = TextSecondary
                            )
                        }
                    }

                    // Right Column: Living StrictBot with Hover, Fly, Walk & Real Human Voice
                    LivingStrictBot(
                        size = 116.dp,
                        state = SupervisorState.VIGILANT,
                        motionMode = uiState.robotMotionMode,
                        isSpeaking = uiState.isSpeakingVoice,
                        voiceAmplitudes = uiState.voiceAmplitudes,
                        currentSpokenText = uiState.activeSpokenPhrase,
                        showSpeechBubble = true,
                        showControls = false,
                        onMascotClick = onMascotClick,
                        onModeChange = { viewModel.setRobotMotionMode(it) },
                        onSpeakClick = { viewModel.speakRandomHumanQuote() },
                        modifier = Modifier.padding(start = 2.dp)
                    )
                }

                // Quick Action Bar: Fly, Walk, Speak (Human Voice), Hover
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .background(SurfaceElevated)
                        .border(1.dp, BorderSubtle, RoundedCornerShape(12.dp))
                        .padding(horizontal = 8.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    QuickKineticsButton(
                        icon = Icons.Default.FlightTakeoff,
                        label = "Fly",
                        isActive = uiState.robotMotionMode == RobotMotionMode.FLY,
                        activeColor = SecondaryCyan,
                        onClick = { viewModel.setRobotMotionMode(RobotMotionMode.FLY) }
                    )
                    QuickKineticsButton(
                        icon = Icons.Default.DirectionsWalk,
                        label = "Walk",
                        isActive = uiState.robotMotionMode == RobotMotionMode.WALK,
                        activeColor = StatusWarning,
                        onClick = { viewModel.setRobotMotionMode(RobotMotionMode.WALK) }
                    )
                    QuickKineticsButton(
                        icon = Icons.AutoMirrored.Filled.VolumeUp,
                        label = "Speak",
                        isActive = uiState.isSpeakingVoice,
                        activeColor = PrimaryNeon,
                        onClick = { viewModel.speakRandomHumanQuote() }
                    )
                    QuickKineticsButton(
                        icon = Icons.Default.Air,
                        label = "Hover",
                        isActive = uiState.robotMotionMode == RobotMotionMode.HOVER,
                        activeColor = TextPrimary,
                        onClick = { viewModel.setRobotMotionMode(RobotMotionMode.HOVER) }
                    )
                }
            }
        }
    }
}

@Composable
private fun QuickKineticsButton(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    isActive: Boolean,
    activeColor: Color,
    onClick: () -> Unit
) {
    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(9999.dp))
            .background(if (isActive) activeColor.copy(alpha = 0.22f) else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp)
    ) {
        Icon(
            imageVector = icon,
            contentDescription = label,
            tint = if (isActive) activeColor else TextMuted,
            modifier = Modifier.size(13.dp)
        )
        Text(
            text = label,
            fontFamily = SpaceGrotesk,
            fontWeight = FontWeight.Bold,
            fontSize = 11.sp,
            color = if (isActive) activeColor else TextMuted
        )
    }
}

@Composable
private fun DigitalClockTelemetryCard(
    digitalTime: String,
    digitalTime24: String,
    isOverride: Boolean = false,
    onChronoClick: () -> Unit = {}
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onChronoClick() }
            .testTag("digital_clock_telemetry_card"),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = SurfaceCard),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 14.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Box(
                    modifier = Modifier
                        .size(38.dp)
                        .clip(RoundedCornerShape(10.dp))
                        .background(SurfaceElevated),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        imageVector = Icons.Default.Schedule,
                        contentDescription = "Digital Clock",
                        tint = PrimaryNeon,
                        modifier = Modifier.size(20.dp)
                    )
                }

                Column {
                    Text(
                        text = "SYSTEM CLOCK (DIGITAL)",
                        fontFamily = Manrope,
                        fontWeight = FontWeight.Bold,
                        fontSize = 10.sp,
                        letterSpacing = 0.6.sp,
                        color = TextMuted
                    )
                    Text(
                        text = digitalTime,
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 17.sp,
                        color = TextPrimary
                    )
                }
            }

            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Column(
                    horizontalAlignment = Alignment.End
                ) {
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(9999.dp))
                            .background(SurfaceElevated)
                            .padding(horizontal = 8.dp, vertical = 3.dp)
                    ) {
                        Text(
                            text = "24H: $digitalTime24",
                            fontFamily = JetBrainsMono,
                            fontWeight = FontWeight.Bold,
                            fontSize = 11.sp,
                            color = SecondaryCyan
                        )
                    }
                    Text(
                        text = if (isOverride) "⚡ Custom Chrono" else "● Strict sync",
                        fontFamily = JetBrainsMono,
                        fontSize = 10.sp,
                        color = if (isOverride) StatusWarning else PrimaryNeon,
                        modifier = Modifier.padding(top = 3.dp)
                    )
                }

                IconButton(
                    onClick = onChronoClick,
                    modifier = Modifier
                        .size(36.dp)
                        .clip(CircleShape)
                        .background(SurfaceElevated)
                ) {
                    Icon(
                        imageVector = Icons.Default.Tune,
                        contentDescription = "Adjust Sliders",
                        tint = PrimaryNeon,
                        modifier = Modifier.size(18.dp)
                    )
                }
            }
        }
    }
}

@Composable
private fun KeyMetricsGrid(
    streakDays: Int,
    todayScore: Int,
    maxScore: Int
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        // Streak Card
        Card(
            modifier = Modifier
                .weight(1f)
                .testTag("streak_metric_card"),
            shape = RoundedCornerShape(16.dp),
            colors = CardDefaults.cardColors(containerColor = SurfaceCard),
            elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(14.dp)
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "CURRENT STREAK",
                        fontFamily = Manrope,
                        fontWeight = FontWeight.Bold,
                        fontSize = 10.sp,
                        letterSpacing = 0.5.sp,
                        color = TextMuted
                    )
                    Box(
                        modifier = Modifier
                            .size(28.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(SurfaceContainerHigh),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            imageVector = Icons.Default.LocalFireDepartment,
                            contentDescription = "Fire Streak",
                            tint = StatusWarning,
                            modifier = Modifier.size(18.dp)
                        )
                    }
                }

                Spacer(modifier = Modifier.height(10.dp))

                Row(
                    verticalAlignment = Alignment.Bottom,
                    horizontalArrangement = Arrangement.spacedBy(4.dp)
                ) {
                    Text(
                        text = streakDays.toString(),
                        fontFamily = SpaceGrotesk,
                        fontWeight = FontWeight.Bold,
                        fontSize = 28.sp,
                        color = TextPrimary
                    )
                    Text(
                        text = "days",
                        fontFamily = Manrope,
                        fontSize = 12.sp,
                        color = TextMuted,
                        modifier = Modifier.padding(bottom = 4.dp)
                    )
                }

                Spacer(modifier = Modifier.height(10.dp))

                // Progress Bar
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(6.dp)
                        .clip(RoundedCornerShape(9999.dp))
                        .background(SurfaceContainerHigh)
                ) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth(0.85f)
                            .height(6.dp)
                            .clip(RoundedCornerShape(9999.dp))
                            .background(StatusWarning)
                    )
                }
            }
        }

        // Score Card
        Card(
            modifier = Modifier
                .weight(1f)
                .testTag("score_metric_card"),
            shape = RoundedCornerShape(16.dp),
            colors = CardDefaults.cardColors(containerColor = SurfaceCard),
            elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(14.dp)
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "TODAY'S SCORE",
                        fontFamily = Manrope,
                        fontWeight = FontWeight.Bold,
                        fontSize = 10.sp,
                        letterSpacing = 0.5.sp,
                        color = TextMuted
                    )
                    Box(
                        modifier = Modifier
                            .size(28.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(SurfaceContainerHigh),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            imageVector = Icons.Default.Star,
                            contentDescription = "Score Star",
                            tint = SecondaryCyan,
                            modifier = Modifier.size(18.dp)
                        )
                    }
                }

                Spacer(modifier = Modifier.height(10.dp))

                Row(
                    verticalAlignment = Alignment.Bottom,
                    horizontalArrangement = Arrangement.spacedBy(4.dp)
                ) {
                    Text(
                        text = todayScore.toString(),
                        fontFamily = SpaceGrotesk,
                        fontWeight = FontWeight.Bold,
                        fontSize = 28.sp,
                        color = TextPrimary
                    )
                    Text(
                        text = "/ $maxScore",
                        fontFamily = Manrope,
                        fontSize = 12.sp,
                        color = TextMuted,
                        modifier = Modifier.padding(bottom = 4.dp)
                    )
                }

                Spacer(modifier = Modifier.height(10.dp))

                // Progress Bar
                val fraction = (todayScore / maxScore.toFloat()).coerceIn(0f, 1f)
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(6.dp)
                        .clip(RoundedCornerShape(9999.dp))
                        .background(SurfaceContainerHigh)
                ) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth(fraction)
                            .height(6.dp)
                            .clip(RoundedCornerShape(9999.dp))
                            .background(PrimaryNeon)
                    )
                }
            }
        }
    }
}

@Composable
private fun HourlyCheckBanner(
    timeWindow: String,
    countdownText: String,
    digitalTime: String,
    pulseAlpha: Float,
    onClick: () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .testTag("hourly_check_banner"),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = SurfaceCard),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            // Left: Alarm Icon + Time Window
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Box(
                    modifier = Modifier
                        .size(44.dp)
                        .clip(RoundedCornerShape(12.dp))
                        .background(SurfaceElevated),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        imageVector = Icons.Default.Alarm,
                        contentDescription = "Alarm Clock",
                        tint = StatusWarning,
                        modifier = Modifier.size(24.dp)
                    )

                    // Urgent Ping Dot in upper right
                    Box(
                        modifier = Modifier
                            .size(10.dp)
                            .align(Alignment.TopEnd)
                            .padding(2.dp)
                            .background(StatusWarning, CircleShape)
                    )
                }

                Column {
                    Text(
                        text = "NEXT HOURLY CHECK",
                        fontFamily = Manrope,
                        fontWeight = FontWeight.Bold,
                        fontSize = 11.sp,
                        letterSpacing = 0.6.sp,
                        color = TextSecondary
                    )
                    Text(
                        text = timeWindow,
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 16.sp,
                        color = TextPrimary
                    )
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(4.dp),
                        modifier = Modifier.padding(top = 2.dp)
                    ) {
                        Text(
                            text = "NOW:",
                            fontFamily = JetBrainsMono,
                            fontSize = 10.sp,
                            color = TextMuted
                        )
                        Text(
                            text = digitalTime,
                            fontFamily = JetBrainsMono,
                            fontWeight = FontWeight.Bold,
                            fontSize = 10.sp,
                            color = SecondaryCyan
                        )
                    }
                }
            }

            // Right: Opens In + Countdown Pill
            Column(
                horizontalAlignment = Alignment.End
            ) {
                Text(
                    text = "OPENS IN",
                    fontFamily = Manrope,
                    fontWeight = FontWeight.Bold,
                    fontSize = 10.sp,
                    color = TextMuted
                )

                Spacer(modifier = Modifier.height(2.dp))

                Row(
                    modifier = Modifier
                        .clip(RoundedCornerShape(9999.dp))
                        .background(SurfaceElevated)
                        .padding(horizontal = 10.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(5.dp)
                ) {
                    Box(
                        modifier = Modifier
                            .size(6.dp)
                            .background(PrimaryNeon.copy(alpha = pulseAlpha), CircleShape)
                    )
                    Text(
                        text = countdownText,
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 12.sp,
                        color = PrimaryNeon
                    )
                }
            }
        }
    }
}

@Composable
private fun QuickActionsSection(
    onWakeUpClick: () -> Unit,
    onTaskClick: () -> Unit,
    onGymClick: () -> Unit,
    onBathClick: () -> Unit
) {
    Column(
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "Quick Actions",
                fontFamily = SpaceGrotesk,
                fontWeight = FontWeight.Bold,
                fontSize = 15.sp,
                color = TextPrimary
            )
            Text(
                text = "Daily Routine",
                fontFamily = Manrope,
                fontWeight = FontWeight.Medium,
                fontSize = 11.sp,
                color = TextMuted
            )
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            // Action 1: Wake Up
            QuickActionItem(
                title = "Wake Up",
                icon = Icons.Default.WbSunny,
                iconTint = StatusWarning,
                testTag = "action_wake_up",
                onClick = onWakeUpClick,
                modifier = Modifier.weight(1f)
            )

            // Action 2: Task
            QuickActionItem(
                title = "Task",
                icon = Icons.Default.CheckCircle,
                iconTint = PrimaryNeon,
                testTag = "action_task",
                onClick = onTaskClick,
                modifier = Modifier.weight(1f)
            )

            // Action 3: Gym
            QuickActionItem(
                title = "Gym",
                icon = Icons.Default.FitnessCenter,
                iconTint = PinkAccent,
                testTag = "action_gym",
                onClick = onGymClick,
                modifier = Modifier.weight(1f)
            )

            // Action 4: Bath
            QuickActionItem(
                title = "Bath",
                icon = Icons.Default.Bathtub,
                iconTint = SecondaryCyan,
                testTag = "action_bath",
                onClick = onBathClick,
                modifier = Modifier.weight(1f)
            )
        }
    }
}

@Composable
private fun QuickActionItem(
    title: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    iconTint: Color,
    testTag: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Card(
        modifier = modifier
            .clickable(onClick = onClick)
            .testTag(testTag),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = SurfaceCard),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 10.dp, horizontal = 2.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            Box(
                modifier = Modifier
                    .size(36.dp)
                    .clip(CircleShape)
                    .background(SurfaceElevated),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    imageVector = icon,
                    contentDescription = title,
                    tint = iconTint,
                    modifier = Modifier.size(18.dp)
                )
            }

            Spacer(modifier = Modifier.height(6.dp))

            Text(
                text = title,
                fontFamily = Manrope,
                fontWeight = FontWeight.Bold,
                fontSize = 11.sp,
                color = TextPrimary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
    }
}

@Composable
private fun ActiveRestrictionsCard(
    onViewRulesClick: () -> Unit,
    onAppClick: (String) -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .testTag("restrictions_card"),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = SurfaceCard),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    Icon(
                        imageVector = Icons.Default.Lock,
                        contentDescription = "Restrictions Active",
                        tint = StatusDanger,
                        modifier = Modifier.size(18.dp)
                    )
                    Text(
                        text = "Restrictions Active",
                        fontFamily = Manrope,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 13.sp,
                        color = TextPrimary
                    )
                }

                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(9999.dp))
                        .background(SurfaceElevated)
                        .padding(horizontal = 8.dp, vertical = 2.dp)
                ) {
                    Text(
                        text = "2 Apps Locked",
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 11.sp,
                        color = StatusDanger
                    )
                }
            }

            // Locked App Tiles (YouTube & Instagram)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                // YouTube Item
                Row(
                    modifier = Modifier
                        .weight(1f)
                        .clip(RoundedCornerShape(10.dp))
                        .background(SurfaceElevated)
                        .clickable { onAppClick("youtube") }
                        .padding(horizontal = 10.dp, vertical = 10.dp)
                        .testTag("app_locked_youtube"),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(24.dp)
                                .clip(RoundedCornerShape(6.dp))
                                .background(StatusDanger.copy(alpha = 0.2f)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(
                                imageVector = Icons.Default.SmartDisplay,
                                contentDescription = "YouTube",
                                tint = StatusDanger,
                                modifier = Modifier.size(15.dp)
                            )
                        }
                        Text(
                            text = "YouTube",
                            fontFamily = Manrope,
                            fontWeight = FontWeight.Medium,
                            fontSize = 12.sp,
                            color = TextPrimary
                        )
                    }

                    Icon(
                        imageVector = Icons.Default.Lock,
                        contentDescription = "Locked",
                        tint = StatusLocked,
                        modifier = Modifier.size(16.dp)
                    )
                }

                // Instagram Item
                Row(
                    modifier = Modifier
                        .weight(1f)
                        .clip(RoundedCornerShape(10.dp))
                        .background(SurfaceElevated)
                        .clickable { onAppClick("instagram") }
                        .padding(horizontal = 10.dp, vertical = 10.dp)
                        .testTag("app_locked_instagram"),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(24.dp)
                                .clip(RoundedCornerShape(6.dp))
                                .background(PinkAccent.copy(alpha = 0.2f)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(
                                imageVector = Icons.Default.PhotoCamera,
                                contentDescription = "Instagram",
                                tint = PinkAccent,
                                modifier = Modifier.size(15.dp)
                            )
                        }
                        Text(
                            text = "Instagram",
                            fontFamily = Manrope,
                            fontWeight = FontWeight.Medium,
                            fontSize = 12.sp,
                            color = TextPrimary
                        )
                    }

                    Icon(
                        imageVector = Icons.Default.Lock,
                        contentDescription = "Locked",
                        tint = StatusLocked,
                        modifier = Modifier.size(16.dp)
                    )
                }
            }

            // Bottom prompt and "View Rules" link
            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 2.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "Earn +20 points in next check to unlock 30m window",
                    fontFamily = Manrope,
                    fontSize = 11.sp,
                    color = TextMuted,
                    modifier = Modifier.weight(1f)
                )

                Row(
                    modifier = Modifier
                        .clickable(onClick = onViewRulesClick)
                        .testTag("view_rules_btn"),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(2.dp)
                ) {
                    Text(
                        text = "View Rules",
                        fontFamily = Manrope,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 12.sp,
                        color = PrimaryNeon
                    )
                    Icon(
                        imageVector = Icons.Default.ChevronRight,
                        contentDescription = null,
                        tint = PrimaryNeon,
                        modifier = Modifier.size(14.dp)
                    )
                }
            }
        }
    }
}

@Composable
private fun ConnectedDevicesSummaryCard(
    onClick: () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .testTag("connected_devices_card"),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = SurfaceCard),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Box(
                    modifier = Modifier
                        .size(38.dp)
                        .clip(RoundedCornerShape(10.dp))
                        .background(SurfaceElevated),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        imageVector = Icons.Default.Devices,
                        contentDescription = "Devices",
                        tint = SecondaryCyan,
                        modifier = Modifier.size(20.dp)
                    )
                }

                Column {
                    Text(
                        text = "Lok's Phone + Windows PC",
                        fontFamily = Manrope,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 13.sp,
                        color = TextPrimary
                    )
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(5.dp),
                        modifier = Modifier.padding(top = 2.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(6.dp)
                                .background(PrimaryNeon, CircleShape)
                        )
                        Text(
                            text = "Both in strict lockdown sync",
                            fontFamily = JetBrainsMono,
                            fontSize = 10.sp,
                            color = TextMuted
                        )
                    }
                }
            }

            Text(
                text = "2/2 ON",
                fontFamily = JetBrainsMono,
                fontWeight = FontWeight.Bold,
                fontSize = 11.sp,
                color = TextMuted
            )
        }
    }
}
