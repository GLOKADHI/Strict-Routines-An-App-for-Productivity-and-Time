package com.example.ui.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
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
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
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
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.ChatBubble
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.DirectionsWalk
import androidx.compose.material.icons.filled.FlightTakeoff
import androidx.compose.material.icons.filled.Hearing
import androidx.compose.material.icons.filled.LocalFireDepartment
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.RecordVoiceOver
import androidx.compose.material.icons.filled.Security
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import com.example.R
import com.example.data.model.RobotMotionMode
import com.example.data.model.SupervisorState
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
import com.example.voice.HumanSpeechLibrary
import com.example.voice.VoicePersona
import kotlinx.coroutines.delay

val StrictBotQuotes = listOf(
    "Hey! Stop tapping me and stay disciplined!",
    "StrictBot AI is actively supervising your rig.",
    "14-day streak on the line. No social media!",
    "Zero distractions allowed. Focus on your deep work!",
    "I'm keeping an eye on your Windows PC telemetry.",
    "Jetpacks engaged! Thrusters at 100% discipline!",
    "Earn +20 points in the next hourly check!",
    "Discipline = Freedom. Let's conquer this session!"
)

@Composable
fun LivingStrictBot(
    modifier: Modifier = Modifier,
    size: Dp = 120.dp,
    state: SupervisorState = SupervisorState.VIGILANT,
    motionMode: RobotMotionMode = RobotMotionMode.HOVER,
    isSpeaking: Boolean = false,
    voiceAmplitudes: List<Float> = emptyList(),
    currentSpokenText: String? = null,
    showSpeechBubble: Boolean = true,
    showControls: Boolean = false,
    onMascotClick: (() -> Unit)? = null,
    onModeChange: ((RobotMotionMode) -> Unit)? = null,
    onSpeakClick: (() -> Unit)? = null
) {
    val infiniteTransition = rememberInfiniteTransition(label = "strict_bot_kinetics")

    // --- 1. HOVER KINETICS ---
    val hoverY by infiniteTransition.animateFloat(
        initialValue = -6f,
        targetValue = 6f,
        animationSpec = infiniteRepeatable(
            animation = tween(1800, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "hover_y"
    )

    val hoverTilt by infiniteTransition.animateFloat(
        initialValue = -3f,
        targetValue = 3f,
        animationSpec = infiniteRepeatable(
            animation = tween(2200, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "hover_tilt"
    )

    // --- 2. FLYING KINETICS ---
    val flyX by infiniteTransition.animateFloat(
        initialValue = -24f,
        targetValue = 24f,
        animationSpec = infiniteRepeatable(
            animation = tween(1600, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "fly_x"
    )

    val flyY by infiniteTransition.animateFloat(
        initialValue = -18f,
        targetValue = 6f,
        animationSpec = infiniteRepeatable(
            animation = tween(1200, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "fly_y"
    )

    val flyBankTilt by infiniteTransition.animateFloat(
        initialValue = -16f,
        targetValue = 16f,
        animationSpec = infiniteRepeatable(
            animation = tween(1600, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "fly_bank"
    )

    val turboFlameScaleY by infiniteTransition.animateFloat(
        initialValue = 1.3f,
        targetValue = 2.4f,
        animationSpec = infiniteRepeatable(
            animation = tween(140, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "turbo_flame_y"
    )

    // --- 3. WALKING KINETICS ---
    val walkStepBob by infiniteTransition.animateFloat(
        initialValue = 10f,
        targetValue = 15f,
        animationSpec = infiniteRepeatable(
            animation = tween(380, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "walk_step_bob"
    )

    val walkSwagger by infiniteTransition.animateFloat(
        initialValue = -6f,
        targetValue = 6f,
        animationSpec = infiniteRepeatable(
            animation = tween(380, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "walk_swagger"
    )

    val walkPatrolX by infiniteTransition.animateFloat(
        initialValue = -18f,
        targetValue = 18f,
        animationSpec = infiniteRepeatable(
            animation = tween(2400, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "walk_patrol_x"
    )

    val footAlternation by infiniteTransition.animateFloat(
        initialValue = -1f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(380, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "foot_alt"
    )

    // Standard Hover Flame
    val hoverFlameScaleY by infiniteTransition.animateFloat(
        initialValue = 0.85f,
        targetValue = 1.35f,
        animationSpec = infiniteRepeatable(
            animation = tween(320, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "hover_flame_y"
    )

    val flameAlpha by infiniteTransition.animateFloat(
        initialValue = 0.65f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(240, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "flame_alpha"
    )

    // Effective physics based on motion mode
    val effectiveOffsetX = when (motionMode) {
        RobotMotionMode.FLY -> flyX.dp
        RobotMotionMode.WALK -> walkPatrolX.dp
        RobotMotionMode.HOVER -> 0.dp
    }

    val effectiveOffsetY = when (motionMode) {
        RobotMotionMode.FLY -> flyY.dp
        RobotMotionMode.WALK -> walkStepBob.dp
        RobotMotionMode.HOVER -> hoverY.dp
    }

    val effectiveRotation = when (motionMode) {
        RobotMotionMode.FLY -> flyBankTilt
        RobotMotionMode.WALK -> walkSwagger
        RobotMotionMode.HOVER -> hoverTilt
    }

    // Interactive Tap bounce & speech state
    var isTapped by remember { mutableStateOf(false) }
    var quoteIndex by remember { mutableIntStateOf(0) }
    var bubbleVisible by remember { mutableStateOf(false) }

    val bounceScale by animateFloatAsState(
        targetValue = if (isTapped) 1.15f else 1.0f,
        animationSpec = spring(dampingRatio = 0.45f, stiffness = 400f),
        label = "bounce_scale"
    )

    // Auto-hide speech bubble if not actively speaking
    LaunchedEffect(bubbleVisible, quoteIndex, isSpeaking) {
        if (bubbleVisible && !isSpeaking) {
            delay(4000)
            bubbleVisible = false
        }
    }

    val mascotDrawable = when (state) {
        SupervisorState.VIGILANT -> R.drawable.strict_bot_mascot
        SupervisorState.SCANNING -> R.drawable.strict_bot_focus
        SupervisorState.WARNING -> R.drawable.strict_bot_alert
        SupervisorState.RESTING -> R.drawable.strict_bot_happy
    }

    Column(
        modifier = modifier.testTag("living_strict_bot_container"),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        // 1. Animated Speech Wave Visualizer & Real Human Speech Bubble
        if (showSpeechBubble) {
            val displayedText = currentSpokenText ?: if (bubbleVisible) StrictBotQuotes[quoteIndex] else null
            AnimatedVisibility(
                visible = isSpeaking || (bubbleVisible && displayedText != null),
                enter = scaleIn() + fadeIn(),
                exit = scaleOut() + fadeOut()
            ) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    modifier = Modifier.padding(bottom = 6.dp)
                ) {
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(14.dp))
                            .background(SurfaceElevated)
                            .border(
                                1.dp,
                                if (isSpeaking) SecondaryCyan else PrimaryNeon.copy(alpha = 0.6f),
                                RoundedCornerShape(14.dp)
                            )
                            .padding(horizontal = 10.dp, vertical = 6.dp)
                            .shadow(10.dp, spotColor = if (isSpeaking) SecondaryCyan else PrimaryNeon)
                    ) {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.spacedBy(4.dp)
                        ) {
                            // Audio Waveform bars if speaking
                            if (isSpeaking) {
                                Row(
                                    horizontalArrangement = Arrangement.spacedBy(3.dp),
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    val amplitudes = if (voiceAmplitudes.isNotEmpty()) voiceAmplitudes else listOf(0.4f, 0.8f, 0.6f, 0.9f, 0.3f)
                                    amplitudes.forEach { amp ->
                                        Box(
                                            modifier = Modifier
                                                .width(3.dp)
                                                .height((amp * 16f).coerceIn(4f, 18f).dp)
                                                .clip(RoundedCornerShape(2.dp))
                                                .background(SecondaryCyan)
                                        )
                                    }
                                    Spacer(modifier = Modifier.width(4.dp))
                                    Text(
                                        text = "SPEAKING",
                                        fontFamily = JetBrainsMono,
                                        fontWeight = FontWeight.Bold,
                                        fontSize = 9.sp,
                                        color = SecondaryCyan
                                    )
                                }
                            }

                            Text(
                                text = displayedText ?: "Stay disciplined!",
                                fontFamily = JetBrainsMono,
                                fontWeight = FontWeight.SemiBold,
                                fontSize = 11.sp,
                                color = if (isSpeaking) TextPrimary else PrimaryNeon,
                                textAlign = TextAlign.Center
                            )
                        }
                    }

                    // Pointer notch
                    Box(
                        modifier = Modifier
                            .size(6.dp)
                            .rotate(45f)
                            .background(SurfaceElevated)
                            .border(1.dp, if (isSpeaking) SecondaryCyan else PrimaryNeon.copy(alpha = 0.6f))
                    )
                }
            }
        }

        // 2. Mascot Body with Dynamic Flying, Walking, and Thruster Layers
        Box(
            modifier = Modifier
                .size(size)
                .offset(x = effectiveOffsetX, y = effectiveOffsetY)
                .rotate(effectiveRotation)
                .scale(bounceScale)
                .clickable(
                    interactionSource = remember { MutableInteractionSource() },
                    indication = null
                ) {
                    isTapped = true
                    if (onSpeakClick != null) {
                        onSpeakClick()
                    } else {
                        quoteIndex = (quoteIndex + 1) % StrictBotQuotes.size
                        bubbleVisible = true
                    }
                    onMascotClick?.invoke()
                }
                .testTag("living_strict_bot_avatar"),
            contentAlignment = Alignment.Center
        ) {
            // Ambient Aura Glow (Pulsing brighter if speaking or flying)
            val auraColor by animateColorAsState(
                targetValue = when {
                    isSpeaking -> SecondaryCyan.copy(alpha = 0.45f)
                    motionMode == RobotMotionMode.FLY -> PrimaryNeon.copy(alpha = 0.5f)
                    motionMode == RobotMotionMode.WALK -> StatusWarning.copy(alpha = 0.35f)
                    state == SupervisorState.WARNING -> StatusDanger.copy(alpha = 0.35f)
                    else -> PrimaryNeon.copy(alpha = 0.3f)
                },
                label = "aura_color"
            )

            Box(
                modifier = Modifier
                    .size(if (motionMode == RobotMotionMode.FLY) size * 1.3f else size * 1.15f)
                    .background(
                        Brush.radialGradient(
                            colors = listOf(auraColor, Color.Transparent)
                        )
                    )
            )

            // Dynamic Thrusters / Jet Flames (Active during HOVER and FLY)
            if (motionMode != RobotMotionMode.WALK) {
                val flameScale = if (motionMode == RobotMotionMode.FLY) turboFlameScaleY else hoverFlameScaleY
                val flameColors = if (motionMode == RobotMotionMode.FLY) {
                    listOf(
                        SecondaryCyan.copy(alpha = flameAlpha),
                        PrimaryNeon.copy(alpha = flameAlpha * 0.8f),
                        Color.Transparent
                    )
                } else {
                    listOf(
                        StatusWarning.copy(alpha = flameAlpha),
                        Color(0xFFFF4500).copy(alpha = flameAlpha * 0.7f),
                        Color.Transparent
                    )
                }

                Box(
                    modifier = Modifier
                        .size(size)
                        .align(Alignment.BottomCenter)
                ) {
                    // Left Rocket Flame
                    Box(
                        modifier = Modifier
                            .size(width = if (motionMode == RobotMotionMode.FLY) 22.dp else 18.dp, height = if (motionMode == RobotMotionMode.FLY) 48.dp else 34.dp)
                            .align(Alignment.BottomStart)
                            .offset(x = (size.value * 0.15f).dp, y = (size.value * 0.12f).dp)
                            .scale(scaleX = 1.0f, scaleY = flameScale)
                            .background(
                                Brush.verticalGradient(flameColors),
                                shape = CircleShape
                            )
                    )

                    // Right Rocket Flame
                    Box(
                        modifier = Modifier
                            .size(width = if (motionMode == RobotMotionMode.FLY) 22.dp else 18.dp, height = if (motionMode == RobotMotionMode.FLY) 48.dp else 34.dp)
                            .align(Alignment.BottomEnd)
                            .offset(x = -(size.value * 0.15f).dp, y = (size.value * 0.12f).dp)
                            .scale(scaleX = 1.0f, scaleY = flameScale)
                            .background(
                                Brush.verticalGradient(flameColors),
                                shape = CircleShape
                            )
                    )
                }
            }

            // Mechanical Walking Feet (Active during WALK mode)
            if (motionMode == RobotMotionMode.WALK) {
                val leftFootLift = if (footAlternation > 0) (footAlternation * 6f).dp else 0.dp
                val rightFootLift = if (footAlternation < 0) (-footAlternation * 6f).dp else 0.dp

                Box(
                    modifier = Modifier
                        .size(size)
                        .align(Alignment.BottomCenter)
                ) {
                    // Left Walking Foot Pad
                    Box(
                        modifier = Modifier
                            .size(width = 20.dp, height = 8.dp)
                            .align(Alignment.BottomStart)
                            .offset(x = (size.value * 0.18f).dp, y = (size.value * 0.12f).dp - leftFootLift)
                            .clip(RoundedCornerShape(4.dp))
                            .background(SurfaceElevated)
                            .border(1.dp, SecondaryCyan, RoundedCornerShape(4.dp))
                    )

                    // Right Walking Foot Pad
                    Box(
                        modifier = Modifier
                            .size(width = 20.dp, height = 8.dp)
                            .align(Alignment.BottomEnd)
                            .offset(x = -(size.value * 0.18f).dp, y = (size.value * 0.12f).dp - rightFootLift)
                            .clip(RoundedCornerShape(4.dp))
                            .background(SurfaceElevated)
                            .border(1.dp, SecondaryCyan, RoundedCornerShape(4.dp))
                    )

                    // Ground Contact Dust Rings
                    Box(
                        modifier = Modifier
                            .size(width = 26.dp, height = 4.dp)
                            .align(Alignment.BottomStart)
                            .offset(x = (size.value * 0.15f).dp, y = (size.value * 0.16f).dp)
                            .alpha(if (leftFootLift == 0.dp) 0.7f else 0.2f)
                            .background(SecondaryCyan.copy(alpha = 0.4f), CircleShape)
                    )
                    Box(
                        modifier = Modifier
                            .size(width = 26.dp, height = 4.dp)
                            .align(Alignment.BottomEnd)
                            .offset(x = -(size.value * 0.15f).dp, y = (size.value * 0.16f).dp)
                            .alpha(if (rightFootLift == 0.dp) 0.7f else 0.2f)
                            .background(SecondaryCyan.copy(alpha = 0.4f), CircleShape)
                    )
                }
            }

            // Wind streaks when flying
            if (motionMode == RobotMotionMode.FLY) {
                Box(
                    modifier = Modifier
                        .width(size * 1.4f)
                        .height(2.dp)
                        .offset(y = (-size.value * 0.2f).dp)
                        .background(
                            Brush.horizontalGradient(
                                listOf(Color.Transparent, SecondaryCyan.copy(alpha = 0.6f), Color.Transparent)
                            )
                        )
                )
                Box(
                    modifier = Modifier
                        .width(size * 1.2f)
                        .height(2.dp)
                        .offset(y = (size.value * 0.1f).dp)
                        .background(
                            Brush.horizontalGradient(
                                listOf(Color.Transparent, PrimaryNeon.copy(alpha = 0.5f), Color.Transparent)
                            )
                        )
                )
            }

            // StrictBot Mascot Sprite Image
            Image(
                painter = painterResource(id = mascotDrawable),
                contentDescription = "StrictBot AI Supervisor",
                modifier = Modifier.size(size)
            )

            // Visor speech pulse overlay when talking
            if (isSpeaking) {
                Box(
                    modifier = Modifier
                        .size(width = size * 0.45f, height = size * 0.18f)
                        .offset(y = (-size.value * 0.08f).dp)
                        .clip(RoundedCornerShape(8.dp))
                        .background(
                            SecondaryCyan.copy(alpha = 0.25f)
                        )
                )
            }
        }

        // 3. Ground Shadow (Tighter on WALK, blurred and distant on FLY)
        val shadowScaleX = when (motionMode) {
            RobotMotionMode.FLY -> 0.5f
            RobotMotionMode.WALK -> 1.15f
            RobotMotionMode.HOVER -> 0.85f
        }
        val shadowAlpha = when (motionMode) {
            RobotMotionMode.FLY -> 0.25f
            RobotMotionMode.WALK -> 0.75f
            RobotMotionMode.HOVER -> 0.5f
        }

        Box(
            modifier = Modifier
                .padding(top = 4.dp)
                .size(width = (size.value * 0.7f).dp, height = 7.dp)
                .scale(scaleX = shadowScaleX, scaleY = 1.0f)
                .alpha(shadowAlpha)
                .background(Color.Black.copy(alpha = 0.8f), CircleShape)
        )

        // 4. Quick Action Pills (Fly, Walk, Speak, Hover)
        if (showControls) {
            Spacer(modifier = Modifier.height(10.dp))
            Row(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                RobotModePill(
                    icon = Icons.Default.FlightTakeoff,
                    label = "Fly",
                    isActive = motionMode == RobotMotionMode.FLY,
                    activeColor = SecondaryCyan,
                    onClick = { onModeChange?.invoke(RobotMotionMode.FLY) }
                )
                RobotModePill(
                    icon = Icons.Default.DirectionsWalk,
                    label = "Walk",
                    isActive = motionMode == RobotMotionMode.WALK,
                    activeColor = StatusWarning,
                    onClick = { onModeChange?.invoke(RobotMotionMode.WALK) }
                )
                RobotModePill(
                    icon = Icons.AutoMirrored.Filled.VolumeUp,
                    label = "Speak",
                    isActive = isSpeaking,
                    activeColor = PrimaryNeon,
                    onClick = { onSpeakClick?.invoke() }
                )
                RobotModePill(
                    icon = Icons.Default.Air,
                    label = "Hover",
                    isActive = motionMode == RobotMotionMode.HOVER,
                    activeColor = TextPrimary,
                    onClick = { onModeChange?.invoke(RobotMotionMode.HOVER) }
                )
            }
        }
    }

    LaunchedEffect(isTapped) {
        if (isTapped) {
            delay(150)
            isTapped = false
        }
    }
}

@Composable
private fun RobotModePill(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    isActive: Boolean,
    activeColor: Color,
    onClick: () -> Unit
) {
    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(9999.dp))
            .background(if (isActive) activeColor.copy(alpha = 0.2f) else SurfaceElevated)
            .border(1.dp, if (isActive) activeColor else BorderSubtle, RoundedCornerShape(9999.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp)
    ) {
        Icon(
            imageVector = icon,
            contentDescription = label,
            tint = if (isActive) activeColor else TextMuted,
            modifier = Modifier.size(12.dp)
        )
        Text(
            text = label,
            fontFamily = SpaceGrotesk,
            fontWeight = FontWeight.Bold,
            fontSize = 10.sp,
            color = if (isActive) activeColor else TextMuted
        )
    }
}

@Composable
fun StrictBotCompanionHoloDialog(
    uiState: StrictFocusUiState,
    viewModel: StrictFocusViewModel,
    onDismiss: () -> Unit,
    onStartFocus: () -> Unit = {}
) {
    var customText by remember { mutableStateOf(uiState.customSpeechInput) }

    Dialog(onDismissRequest = onDismiss) {
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .padding(4.dp)
                .testTag("strict_bot_holo_dialog"),
            shape = RoundedCornerShape(24.dp),
            colors = CardDefaults.cardColors(containerColor = SurfaceCard),
            border = androidx.compose.foundation.BorderStroke(1.5.dp, PrimaryNeon.copy(alpha = 0.6f))
        ) {
            Column(
                modifier = Modifier
                    .padding(18.dp)
                    .verticalScroll(rememberScrollState()),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                // Header with Close
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(10.dp)
                                .background(PrimaryNeon, CircleShape)
                        )
                        Column {
                            Text(
                                text = "STRICTBOT COMMAND LAB",
                                fontFamily = SpaceGrotesk,
                                fontWeight = FontWeight.Bold,
                                fontSize = 15.sp,
                                color = TextPrimary
                            )
                            Text(
                                text = "Physics Engine • Real Human Voice",
                                fontFamily = JetBrainsMono,
                                fontSize = 10.sp,
                                color = SecondaryCyan
                            )
                        }
                    }

                    IconButton(onClick = onDismiss, modifier = Modifier.size(28.dp)) {
                        Icon(
                            imageVector = Icons.Default.Close,
                            contentDescription = "Close",
                            tint = TextMuted
                        )
                    }
                }

                Spacer(modifier = Modifier.height(14.dp))

                // The Living StrictBot in Action with current mode & audio sync
                LivingStrictBot(
                    size = 135.dp,
                    state = SupervisorState.SCANNING,
                    motionMode = uiState.robotMotionMode,
                    isSpeaking = uiState.isSpeakingVoice,
                    voiceAmplitudes = uiState.voiceAmplitudes,
                    currentSpokenText = uiState.activeSpokenPhrase,
                    showSpeechBubble = true,
                    showControls = true,
                    onModeChange = { viewModel.setRobotMotionMode(it) },
                    onSpeakClick = { viewModel.speakRandomHumanQuote() }
                )

                Spacer(modifier = Modifier.height(14.dp))

                // MOTION MODE SELECTOR BUTTONS
                Text(
                    text = "ROBOT MOTION PROTOCOL",
                    fontFamily = SpaceGrotesk,
                    fontWeight = FontWeight.Bold,
                    fontSize = 11.sp,
                    letterSpacing = 0.6.sp,
                    color = TextSecondary,
                    modifier = Modifier.align(Alignment.Start)
                )

                Spacer(modifier = Modifier.height(6.dp))

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    // Fly Mode Button
                    MotionSelectionCard(
                        icon = Icons.Default.FlightTakeoff,
                        title = "Turbo Fly",
                        subtitle = "Thrusters • Banking",
                        isActive = uiState.robotMotionMode == RobotMotionMode.FLY,
                        accentColor = SecondaryCyan,
                        modifier = Modifier.weight(1f),
                        onClick = { viewModel.setRobotMotionMode(RobotMotionMode.FLY) }
                    )

                    // Walk Mode Button
                    MotionSelectionCard(
                        icon = Icons.Default.DirectionsWalk,
                        title = "Patrol Walk",
                        subtitle = "Steps • Pacing",
                        isActive = uiState.robotMotionMode == RobotMotionMode.WALK,
                        accentColor = StatusWarning,
                        modifier = Modifier.weight(1f),
                        onClick = { viewModel.setRobotMotionMode(RobotMotionMode.WALK) }
                    )

                    // Hover Mode Button
                    MotionSelectionCard(
                        icon = Icons.Default.Air,
                        title = "Hover Idle",
                        subtitle = "Sine Physics",
                        isActive = uiState.robotMotionMode == RobotMotionMode.HOVER,
                        accentColor = PrimaryNeon,
                        modifier = Modifier.weight(1f),
                        onClick = { viewModel.setRobotMotionMode(RobotMotionMode.HOVER) }
                    )
                }

                Spacer(modifier = Modifier.height(16.dp))

                // REAL HUMAN VOICE SYNTHESIS SECTION
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
                            imageVector = Icons.Default.RecordVoiceOver,
                            contentDescription = null,
                            tint = PrimaryNeon,
                            modifier = Modifier.size(16.dp)
                        )
                        Text(
                            text = "SPEAK LIKE A REAL HUMAN",
                            fontFamily = SpaceGrotesk,
                            fontWeight = FontWeight.Bold,
                            fontSize = 12.sp,
                            letterSpacing = 0.5.sp,
                            color = PrimaryNeon
                        )
                    }

                    if (uiState.isSpeakingVoice) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp)
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(6.dp)
                                    .background(StatusDanger, CircleShape)
                            )
                            Text(
                                text = "TTS Live Audio",
                                fontFamily = JetBrainsMono,
                                fontSize = 10.sp,
                                color = StatusDanger
                            )
                        }
                    }
                }

                Spacer(modifier = Modifier.height(8.dp))

                // Voice Personas Selector
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    VoicePersona.entries.forEach { persona ->
                        val isSelected = uiState.selectedVoicePersona == persona
                        Box(
                            modifier = Modifier
                                .weight(1f)
                                .clip(RoundedCornerShape(8.dp))
                                .background(if (isSelected) PrimaryNeon.copy(alpha = 0.2f) else SurfaceElevated)
                                .border(
                                    1.dp,
                                    if (isSelected) PrimaryNeon else BorderSubtle,
                                    RoundedCornerShape(8.dp)
                                )
                                .clickable {
                                    viewModel.setVoicePersona(persona)
                                    viewModel.speakHumanVoice("Voice persona switched to ${persona.title}. Ready!")
                                }
                                .padding(vertical = 7.dp, horizontal = 2.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                text = persona.title.split(" ").first(),
                                fontFamily = Manrope,
                                fontWeight = FontWeight.SemiBold,
                                fontSize = 10.sp,
                                color = if (isSelected) PrimaryNeon else TextMuted,
                                textAlign = TextAlign.Center
                            )
                        }
                    }
                }

                Spacer(modifier = Modifier.height(10.dp))

                // Preset Voice Lines Grid
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    HumanSpeechLibrary.take(3).forEach { quote ->
                        VoiceQuoteChip(
                            category = quote.category,
                            text = quote.text,
                            onPlay = {
                                viewModel.speakHumanVoice(quote.text)
                                if (quote.actionCommand == "FLY") viewModel.setRobotMotionMode(RobotMotionMode.FLY)
                                if (quote.actionCommand == "WALK") viewModel.setRobotMotionMode(RobotMotionMode.WALK)
                            }
                        )
                    }
                }

                Spacer(modifier = Modifier.height(12.dp))

                // Custom Speech Input: Type anything and StrictBot speaks it
                OutlinedTextField(
                    value = customText,
                    onValueChange = {
                        customText = it
                        viewModel.updateCustomSpeechInput(it)
                    },
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag("custom_speech_input"),
                    label = {
                        Text(
                            "Type anything for StrictBot to say aloud",
                            fontFamily = Manrope,
                            fontSize = 11.sp,
                            color = TextMuted
                        )
                    },
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = PrimaryNeon,
                        unfocusedBorderColor = BorderSubtle,
                        focusedTextColor = TextPrimary,
                        unfocusedTextColor = TextPrimary,
                        focusedContainerColor = SurfaceElevated,
                        unfocusedContainerColor = SurfaceElevated
                    ),
                    shape = RoundedCornerShape(12.dp),
                    trailingIcon = {
                        if (uiState.isSpeakingVoice) {
                            IconButton(onClick = { viewModel.stopVoice() }) {
                                Icon(Icons.Default.Stop, contentDescription = "Stop", tint = StatusDanger)
                            }
                        } else {
                            IconButton(
                                onClick = {
                                    if (customText.isNotBlank()) {
                                        viewModel.speakHumanVoice(customText)
                                    }
                                }
                            ) {
                                Icon(Icons.Default.PlayArrow, contentDescription = "Speak", tint = PrimaryNeon)
                            }
                        }
                    }
                )

                Spacer(modifier = Modifier.height(14.dp))

                // Bottom Action Buttons
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Button(
                        onClick = { viewModel.speakRandomHumanQuote() },
                        modifier = Modifier
                            .weight(1f)
                            .height(46.dp)
                            .testTag("random_human_speech_btn"),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = SurfaceElevated,
                            contentColor = TextPrimary
                        ),
                        border = androidx.compose.foundation.BorderStroke(1.dp, BorderSubtle),
                        shape = RoundedCornerShape(9999.dp)
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp)
                        ) {
                            Icon(Icons.Default.Bolt, contentDescription = null, tint = StatusWarning, modifier = Modifier.size(16.dp))
                            Text("Random Voice", fontFamily = SpaceGrotesk, fontWeight = FontWeight.Bold, fontSize = 12.sp)
                        }
                    }

                    Button(
                        onClick = {
                            onDismiss()
                        },
                        modifier = Modifier
                            .weight(1f)
                            .height(46.dp)
                            .testTag("dismiss_bot_dialog_btn"),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = PrimaryNeon,
                            contentColor = OnPrimaryFixed
                        ),
                        shape = RoundedCornerShape(9999.dp)
                    ) {
                        Text("Acknowledge", fontFamily = SpaceGrotesk, fontWeight = FontWeight.Bold, fontSize = 13.sp)
                    }
                }
            }
        }
    }
}

@Composable
private fun MotionSelectionCard(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    title: String,
    subtitle: String,
    isActive: Boolean,
    accentColor: Color,
    modifier: Modifier = Modifier,
    onClick: () -> Unit
) {
    Box(
        modifier = modifier
            .clip(RoundedCornerShape(12.dp))
            .background(if (isActive) accentColor.copy(alpha = 0.18f) else SurfaceElevated)
            .border(
                1.dp,
                if (isActive) accentColor else BorderSubtle,
                RoundedCornerShape(12.dp)
            )
            .clickable(onClick = onClick)
            .padding(vertical = 10.dp, horizontal = 6.dp),
        contentAlignment = Alignment.Center
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(2.dp)
        ) {
            Icon(
                imageVector = icon,
                contentDescription = title,
                tint = if (isActive) accentColor else TextMuted,
                modifier = Modifier.size(20.dp)
            )
            Text(
                text = title,
                fontFamily = SpaceGrotesk,
                fontWeight = FontWeight.Bold,
                fontSize = 11.sp,
                color = if (isActive) accentColor else TextPrimary
            )
            Text(
                text = subtitle,
                fontFamily = JetBrainsMono,
                fontSize = 9.sp,
                color = if (isActive) accentColor.copy(alpha = 0.8f) else TextMuted
            )
        }
    }
}

@Composable
private fun VoiceQuoteChip(
    category: String,
    text: String,
    onPlay: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(SurfaceElevated)
            .border(1.dp, BorderSubtle, RoundedCornerShape(10.dp))
            .clickable(onClick = onPlay)
            .padding(horizontal = 10.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = category.uppercase(),
                fontFamily = JetBrainsMono,
                fontWeight = FontWeight.Bold,
                fontSize = 9.sp,
                color = SecondaryCyan
            )
            Text(
                text = text,
                fontFamily = Manrope,
                fontSize = 11.sp,
                color = TextPrimary,
                maxLines = 1
            )
        }
        Box(
            modifier = Modifier
                .size(28.dp)
                .clip(CircleShape)
                .background(PrimaryNeon.copy(alpha = 0.2f)),
            contentAlignment = Alignment.Center
        ) {
            Icon(
                imageVector = Icons.Default.PlayArrow,
                contentDescription = "Speak",
                tint = PrimaryNeon,
                modifier = Modifier.size(16.dp)
            )
        }
    }
}

