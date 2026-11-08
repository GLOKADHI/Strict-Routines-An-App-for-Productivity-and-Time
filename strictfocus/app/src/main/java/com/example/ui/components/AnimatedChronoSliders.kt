package com.example.ui.components

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
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
import androidx.compose.material.icons.filled.AccessTime
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.RestartAlt
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Speed
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import com.example.ui.theme.BgSurface
import com.example.ui.theme.BorderSubtle
import com.example.ui.theme.JetBrainsMono
import com.example.ui.theme.Manrope
import com.example.ui.theme.OnPrimaryFixed
import com.example.ui.theme.PrimaryNeon
import com.example.ui.theme.SecondaryCyan
import com.example.ui.theme.SpaceGrotesk
import com.example.ui.theme.StatusWarning
import com.example.ui.theme.SurfaceCard
import com.example.ui.theme.SurfaceElevated
import com.example.ui.theme.TextMuted
import com.example.ui.theme.TextPrimary
import com.example.ui.theme.TextSecondary
import com.example.ui.viewmodel.StrictFocusUiState
import com.example.ui.viewmodel.StrictFocusViewModel
import kotlin.math.roundToInt

val MonthNames = listOf(
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
)

val ShortMonthNames = listOf(
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
)

@Composable
fun AnimatedChronoSlidersSheet(
    uiState: StrictFocusUiState,
    viewModel: StrictFocusViewModel,
    onDismiss: () -> Unit
) {
    Dialog(onDismissRequest = onDismiss) {
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .padding(4.dp)
                .testTag("chrono_sliders_dialog"),
            shape = RoundedCornerShape(24.dp),
            colors = CardDefaults.cardColors(containerColor = SurfaceCard),
            border = androidx.compose.foundation.BorderStroke(1.5.dp, PrimaryNeon.copy(alpha = 0.5f))
        ) {
            Column(
                modifier = Modifier
                    .padding(18.dp)
                    .verticalScroll(rememberScrollState())
            ) {
                // Header Bar
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
                                .size(34.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .background(SurfaceElevated),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(
                                imageVector = Icons.Default.Tune,
                                contentDescription = "Chrono Sliders",
                                tint = PrimaryNeon,
                                modifier = Modifier.size(20.dp)
                            )
                        }
                        Column {
                            Text(
                                text = "CHRONO SLIDERS RIG",
                                fontFamily = SpaceGrotesk,
                                fontWeight = FontWeight.Bold,
                                fontSize = 16.sp,
                                color = TextPrimary
                            )
                            Text(
                                text = "Interactive Time & Calendar Matrix",
                                fontFamily = Manrope,
                                fontSize = 11.sp,
                                color = SecondaryCyan
                            )
                        }
                    }

                    IconButton(onClick = onDismiss, modifier = Modifier.size(28.dp)) {
                        Icon(Icons.Default.Close, contentDescription = "Close", tint = TextMuted)
                    }
                }

                Spacer(modifier = Modifier.height(14.dp))

                // Animated Digital Clock & Calendar Preview Banner
                AnimatedTimeCalendarPreview(
                    digitalTime = uiState.digitalTime,
                    currentDate = uiState.currentDate,
                    year = uiState.selectedYear,
                    isOverride = uiState.isTimelineOverride
                )

                Spacer(modifier = Modifier.height(18.dp))

                // SECTION 1: TIME SLIDERS (Hour, Minute, AM/PM)
                SectionHeader(
                    icon = Icons.Default.AccessTime,
                    title = "Time Sliders",
                    subtitle = "Hour • Minute • AM/PM"
                )

                Spacer(modifier = Modifier.height(8.dp))

                // Hour Slider (1 to 12)
                ChronoSliderItem(
                    label = "Hour",
                    valueDisplay = String.format("%02d", uiState.selectedHour),
                    unit = "HR",
                    value = uiState.selectedHour.toFloat(),
                    range = 1f..12f,
                    steps = 10,
                    accentColor = PrimaryNeon,
                    onValueChange = { viewModel.updateHour(it.roundToInt()) }
                )

                Spacer(modifier = Modifier.height(10.dp))

                // Minute Slider (0 to 59)
                ChronoSliderItem(
                    label = "Minute",
                    valueDisplay = String.format("%02d", uiState.selectedMinute),
                    unit = "MIN",
                    value = uiState.selectedMinute.toFloat(),
                    range = 0f..59f,
                    steps = 58,
                    accentColor = SecondaryCyan,
                    onValueChange = { viewModel.updateMinute(it.roundToInt()) }
                )

                Spacer(modifier = Modifier.height(10.dp))

                // AM / PM Animated Segmented Slider Switch
                AmPmSegmentedSlider(
                    isAm = uiState.selectedIsAm,
                    onSelectAm = { viewModel.updateAmPm(true) },
                    onSelectPm = { viewModel.updateAmPm(false) }
                )

                Spacer(modifier = Modifier.height(20.dp))

                // SECTION 2: CALENDAR SLIDERS (Date, Month, Year)
                SectionHeader(
                    icon = Icons.Default.CalendarMonth,
                    title = "Calendar Sliders",
                    subtitle = "Day • Month • Year"
                )

                Spacer(modifier = Modifier.height(8.dp))

                // Day / Date Slider (1 to 31)
                ChronoSliderItem(
                    label = "Day / Date",
                    valueDisplay = String.format("%02d", uiState.selectedDay),
                    unit = "DAY",
                    value = uiState.selectedDay.toFloat(),
                    range = 1f..31f,
                    steps = 29,
                    accentColor = PrimaryNeon,
                    onValueChange = { viewModel.updateDay(it.roundToInt()) }
                )

                Spacer(modifier = Modifier.height(10.dp))

                // Month Slider (1 to 12)
                val monthIndex = (uiState.selectedMonth - 1).coerceIn(0, 11)
                ChronoSliderItem(
                    label = "Month",
                    valueDisplay = "${ShortMonthNames[monthIndex]} (${uiState.selectedMonth})",
                    unit = MonthNames[monthIndex],
                    value = uiState.selectedMonth.toFloat(),
                    range = 1f..12f,
                    steps = 10,
                    accentColor = StatusWarning,
                    onValueChange = { viewModel.updateMonth(it.roundToInt()) }
                )

                Spacer(modifier = Modifier.height(10.dp))

                // Year Slider (2024 to 2030)
                ChronoSliderItem(
                    label = "Year",
                    valueDisplay = "${uiState.selectedYear}",
                    unit = "YR",
                    value = uiState.selectedYear.toFloat(),
                    range = 2024f..2030f,
                    steps = 5,
                    accentColor = SecondaryCyan,
                    onValueChange = { viewModel.updateYear(it.roundToInt()) }
                )

                Spacer(modifier = Modifier.height(20.dp))

                // Bottom Action Buttons
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    // Sync to Live System Time Button
                    Button(
                        onClick = { viewModel.syncToLiveClock() },
                        modifier = Modifier
                            .weight(1f)
                            .height(48.dp)
                            .testTag("sync_live_time_btn"),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = SurfaceElevated,
                            contentColor = TextPrimary
                        ),
                        border = androidx.compose.foundation.BorderStroke(1.dp, BorderSubtle),
                        shape = RoundedCornerShape(9999.dp)
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            Icon(
                                imageVector = Icons.Default.Sync,
                                contentDescription = null,
                                tint = SecondaryCyan,
                                modifier = Modifier.size(16.dp)
                            )
                            Text(
                                text = "Live Sync",
                                fontFamily = SpaceGrotesk,
                                fontWeight = FontWeight.Bold,
                                fontSize = 13.sp
                            )
                        }
                    }

                    // Apply / Done Button
                    Button(
                        onClick = onDismiss,
                        modifier = Modifier
                            .weight(1f)
                            .height(48.dp)
                            .testTag("apply_chrono_btn"),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = PrimaryNeon,
                            contentColor = OnPrimaryFixed
                        ),
                        shape = RoundedCornerShape(9999.dp)
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            Icon(
                                imageVector = Icons.Default.Check,
                                contentDescription = null,
                                tint = OnPrimaryFixed,
                                modifier = Modifier.size(16.dp)
                            )
                            Text(
                                text = "Lock In",
                                fontFamily = SpaceGrotesk,
                                fontWeight = FontWeight.Bold,
                                fontSize = 14.sp
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun AnimatedTimeCalendarPreview(
    digitalTime: String,
    currentDate: String,
    year: Int,
    isOverride: Boolean
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .testTag("animated_chrono_preview"),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = SurfaceElevated),
        border = androidx.compose.foundation.BorderStroke(1.dp, BorderSubtle)
    ) {
        Column(
            modifier = Modifier.padding(14.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(5.dp)
                ) {
                    Box(
                        modifier = Modifier
                            .size(7.dp)
                            .background(if (isOverride) StatusWarning else PrimaryNeon, CircleShape)
                    )
                    Text(
                        text = if (isOverride) "MANUAL SLIDER RIG" else "LIVE SYSTEM SYNC",
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 10.sp,
                        color = if (isOverride) StatusWarning else PrimaryNeon
                    )
                }

                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(9999.dp))
                        .background(SurfaceCard)
                        .padding(horizontal = 8.dp, vertical = 2.dp)
                ) {
                    Text(
                        text = "$year",
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 11.sp,
                        color = SecondaryCyan
                    )
                }
            }

            Spacer(modifier = Modifier.height(10.dp))

            // Animated Digital Time with slide animation
            AnimatedContent(
                targetState = digitalTime,
                transitionSpec = {
                    (slideInVertically { height -> height } + fadeIn()) togetherWith
                            (slideOutVertically { height -> -height } + fadeOut())
                },
                label = "digital_time_anim"
            ) { targetTime ->
                Text(
                    text = targetTime,
                    fontFamily = JetBrainsMono,
                    fontWeight = FontWeight.Bold,
                    fontSize = 26.sp,
                    letterSpacing = 1.sp,
                    color = PrimaryNeon,
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center
                )
            }

            Spacer(modifier = Modifier.height(4.dp))

            // Animated Date
            AnimatedContent(
                targetState = "$currentDate, $year",
                transitionSpec = { fadeIn() togetherWith fadeOut() },
                label = "calendar_date_anim"
            ) { targetDate ->
                Text(
                    text = targetDate,
                    fontFamily = SpaceGrotesk,
                    fontWeight = FontWeight.SemiBold,
                    fontSize = 13.sp,
                    color = TextSecondary,
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center
                )
            }
        }
    }
}

@Composable
private fun SectionHeader(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    title: String,
    subtitle: String
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = PrimaryNeon,
            modifier = Modifier.size(18.dp)
        )
        Column {
            Text(
                text = title,
                fontFamily = SpaceGrotesk,
                fontWeight = FontWeight.Bold,
                fontSize = 14.sp,
                color = TextPrimary
            )
            Text(
                text = subtitle,
                fontFamily = Manrope,
                fontSize = 10.sp,
                color = TextMuted
            )
        }
    }
}

@Composable
private fun ChronoSliderItem(
    label: String,
    valueDisplay: String,
    unit: String,
    value: Float,
    range: ClosedFloatingPointRange<Float>,
    steps: Int,
    accentColor: Color,
    onValueChange: (Float) -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = SurfaceElevated),
        border = androidx.compose.foundation.BorderStroke(1.dp, BorderSubtle)
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = label,
                    fontFamily = Manrope,
                    fontWeight = FontWeight.SemiBold,
                    fontSize = 12.sp,
                    color = TextSecondary
                )
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp)
                ) {
                    Text(
                        text = valueDisplay,
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 14.sp,
                        color = accentColor
                    )
                    Text(
                        text = unit,
                        fontFamily = JetBrainsMono,
                        fontSize = 10.sp,
                        color = TextMuted
                    )
                }
            }

            Slider(
                value = value,
                onValueChange = onValueChange,
                valueRange = range,
                steps = steps,
                colors = SliderDefaults.colors(
                    thumbColor = accentColor,
                    activeTrackColor = accentColor,
                    inactiveTrackColor = SurfaceCard
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag("slider_${label.lowercase().replace(" ", "_").replace("/", "_")}")
            )
        }
    }
}

@Composable
private fun AmPmSegmentedSlider(
    isAm: Boolean,
    onSelectAm: () -> Unit,
    onSelectPm: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = SurfaceElevated),
        border = androidx.compose.foundation.BorderStroke(1.dp, BorderSubtle)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "AM / PM Selector",
                fontFamily = Manrope,
                fontWeight = FontWeight.SemiBold,
                fontSize = 12.sp,
                color = TextSecondary
            )

            // Sliding Toggle Pill
            Row(
                modifier = Modifier
                    .clip(RoundedCornerShape(9999.dp))
                    .background(SurfaceCard)
                    .border(1.dp, BorderSubtle, RoundedCornerShape(9999.dp))
                    .padding(3.dp),
                horizontalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                // AM Button
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(9999.dp))
                        .background(if (isAm) PrimaryNeon else Color.Transparent)
                        .clickable { onSelectAm() }
                        .padding(horizontal = 16.dp, vertical = 6.dp)
                        .testTag("ampm_select_am"),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        text = "AM",
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 12.sp,
                        color = if (isAm) OnPrimaryFixed else TextMuted
                    )
                }

                // PM Button
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(9999.dp))
                        .background(if (!isAm) PrimaryNeon else Color.Transparent)
                        .clickable { onSelectPm() }
                        .padding(horizontal = 16.dp, vertical = 6.dp)
                        .testTag("ampm_select_pm"),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        text = "PM",
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 12.sp,
                        color = if (!isAm) OnPrimaryFixed else TextMuted
                    )
                }
            }
        }
    }
}
