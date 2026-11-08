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
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.R
import com.example.data.model.SupervisorState
import com.example.ui.components.LivingStrictBot
import com.example.ui.components.StrictHeader
import com.example.ui.theme.BgCanvas
import com.example.ui.theme.BorderSubtle
import com.example.ui.theme.JetBrainsMono
import com.example.ui.theme.Manrope
import com.example.ui.theme.OnPrimaryFixed
import com.example.ui.theme.PrimaryNeon
import com.example.ui.theme.SecondaryCyan
import com.example.ui.theme.SpaceGrotesk
import com.example.ui.theme.StatusDanger
import com.example.ui.theme.StatusWarning
import com.example.ui.theme.SurfaceCard
import com.example.ui.theme.SurfaceElevated
import com.example.ui.theme.TextMuted
import com.example.ui.theme.TextPrimary
import com.example.ui.theme.TextSecondary
import com.example.ui.viewmodel.StrictFocusUiState
import com.example.ui.viewmodel.StrictFocusViewModel

@Composable
fun FocusScreen(
    uiState: StrictFocusUiState,
    viewModel: StrictFocusViewModel,
    modifier: Modifier = Modifier
) {
    val session = uiState.activeFocusSession
    val minutes = session.remainingSeconds / 60
    val seconds = session.remainingSeconds % 60
    val timeFormatted = String.format("%02d:%02d", minutes, seconds)
    val progress = (session.remainingSeconds.toFloat() / session.totalSeconds.toFloat()).coerceIn(0f, 1f)

    val infiniteTransition = rememberInfiniteTransition(label = "focus_glow")
    val glowScale by infiniteTransition.animateFloat(
        initialValue = 0.95f,
        targetValue = 1.05f,
        animationSpec = infiniteRepeatable(
            animation = tween(1500, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "glow_scale"
    )

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(BgCanvas)
    ) {
        StrictHeader(
            title = "FOCUS RIG",
            statusBadge = if (session.isActive) "LOCKDOWN ARMED" else "STANDBY",
            streakDays = uiState.streakDaysTotalGoal,
            digitalTime = uiState.digitalTime24,
            onAvatarClick = { viewModel.showDialog("SUPERVISOR") }
        )

        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // Top Vigilance Banner
            Card(
                modifier = Modifier.fillMaxWidth().testTag("vigilance_card"),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = SurfaceCard),
                border = androidx.compose.foundation.BorderStroke(1.dp, BorderSubtle)
            ) {
                Row(
                    modifier = Modifier.padding(14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    LivingStrictBot(
                        size = 76.dp,
                        state = if (session.isActive) SupervisorState.SCANNING else SupervisorState.VIGILANT,
                        motionMode = uiState.robotMotionMode,
                        isSpeaking = uiState.isSpeakingVoice,
                        voiceAmplitudes = uiState.voiceAmplitudes,
                        currentSpokenText = uiState.activeSpokenPhrase,
                        showSpeechBubble = false,
                        onMascotClick = { viewModel.showDialog("SUPERVISOR") }
                    )

                    Column {
                        Text(
                            text = if (session.isActive) "StrictBot Supervisor Active" else "Focus Rig In Standby",
                            fontFamily = SpaceGrotesk,
                            fontWeight = FontWeight.Bold,
                            fontSize = 15.sp,
                            color = TextPrimary
                        )
                        Text(
                            text = if (session.isActive) "Distractions suppressed across Phone & PC" else "Select a duration and engage focus lockdown",
                            fontFamily = Manrope,
                            fontSize = 12.sp,
                            color = if (session.isActive) PrimaryNeon else TextMuted
                        )
                        Text(
                            text = "SYSTEM CLOCK: ${uiState.digitalTime}",
                            fontFamily = JetBrainsMono,
                            fontWeight = FontWeight.Bold,
                            fontSize = 11.sp,
                            color = SecondaryCyan,
                            modifier = Modifier.padding(top = 2.dp)
                        )
                    }
                }
            }

            // Big Circular Timer Rig
            Box(
                modifier = Modifier
                    .size(240.dp)
                    .padding(12.dp),
                contentAlignment = Alignment.Center
            ) {
                // Background Track
                CircularProgressIndicator(
                    progress = { 1f },
                    modifier = Modifier.fillMaxSize(),
                    color = SurfaceElevated,
                    strokeWidth = 14.dp
                )

                // Active Gauge
                CircularProgressIndicator(
                    progress = { progress },
                    modifier = Modifier
                        .fillMaxSize()
                        .scale(if (session.isActive) glowScale else 1f),
                    color = if (session.isActive) PrimaryNeon else SecondaryCyan,
                    strokeWidth = 14.dp
                )

                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center
                ) {
                    Text(
                        text = timeFormatted,
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 38.sp,
                        color = TextPrimary
                    )
                    Text(
                        text = session.presetLabel,
                        fontFamily = SpaceGrotesk,
                        fontWeight = FontWeight.Medium,
                        fontSize = 13.sp,
                        color = SecondaryCyan,
                        modifier = Modifier.padding(top = 2.dp)
                    )
                }
            }

            // Preset Buttons
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                listOf(15, 25, 50, 90).forEach { mins ->
                    val isSelected = session.totalSeconds == mins * 60
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(10.dp))
                            .background(if (isSelected) PrimaryNeon.copy(alpha = 0.2f) else SurfaceCard)
                            .border(
                                1.dp,
                                if (isSelected) PrimaryNeon else BorderSubtle,
                                RoundedCornerShape(10.dp)
                            )
                            .clickable { viewModel.setFocusPreset(mins) }
                            .padding(vertical = 10.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            text = "${mins}m",
                            fontFamily = JetBrainsMono,
                            fontWeight = FontWeight.Bold,
                            fontSize = 13.sp,
                            color = if (isSelected) PrimaryNeon else TextMuted
                        )
                    }
                }
            }

            // Ambient Sound Selector
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { viewModel.cycleSoundMode() }
                    .testTag("sound_mode_card"),
                shape = RoundedCornerShape(14.dp),
                colors = CardDefaults.cardColors(containerColor = SurfaceCard),
                border = androidx.compose.foundation.BorderStroke(1.dp, BorderSubtle)
            ) {
                Row(
                    modifier = Modifier.padding(14.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        Icon(
                            imageVector = Icons.Default.GraphicEq,
                            contentDescription = "Audio Rig",
                            tint = SecondaryCyan,
                            modifier = Modifier.size(20.dp)
                        )
                        Column {
                            Text(
                                text = "Ambient Focus Rig",
                                fontFamily = Manrope,
                                fontWeight = FontWeight.SemiBold,
                                fontSize = 13.sp,
                                color = TextPrimary
                            )
                            Text(
                                text = session.soundMode,
                                fontFamily = JetBrainsMono,
                                fontSize = 11.sp,
                                color = SecondaryCyan
                            )
                        }
                    }

                    Text(
                        text = "CYCLE",
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 11.sp,
                        color = PrimaryNeon
                    )
                }
            }

            // Primary Control Actions (Start/Pause & Emergency Stop)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Button(
                    onClick = { viewModel.toggleFocusSession() },
                    modifier = Modifier
                        .weight(1f)
                        .height(52.dp)
                        .testTag("focus_toggle_btn"),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (session.isActive) SurfaceElevated else PrimaryNeon,
                        contentColor = if (session.isActive) TextPrimary else OnPrimaryFixed
                    ),
                    shape = RoundedCornerShape(9999.dp)
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        Icon(
                            imageVector = if (session.isActive) Icons.Default.Pause else Icons.Default.PlayArrow,
                            contentDescription = null,
                            modifier = Modifier.size(20.dp)
                        )
                        Text(
                            text = if (session.isActive) "Pause Rig" else "Engage Lockdown",
                            fontFamily = SpaceGrotesk,
                            fontWeight = FontWeight.Bold,
                            fontSize = 15.sp
                        )
                    }
                }

                if (session.isActive) {
                    Button(
                        onClick = { viewModel.emergencyStopFocus() },
                        modifier = Modifier
                            .height(52.dp)
                            .testTag("emergency_stop_btn"),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = StatusDanger.copy(alpha = 0.15f),
                            contentColor = StatusDanger
                        ),
                        border = androidx.compose.foundation.BorderStroke(1.dp, StatusDanger),
                        shape = RoundedCornerShape(9999.dp)
                    ) {
                        Text(
                            text = "Abort",
                            fontFamily = SpaceGrotesk,
                            fontWeight = FontWeight.Bold,
                            fontSize = 14.sp
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(20.dp))
        }
    }
}
