package com.example.ui.screens

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.Headphones
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.LockOpen
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.data.model.ConnectedDevice
import com.example.ui.components.StrictHeader
import com.example.ui.theme.BgCanvas
import com.example.ui.theme.BorderSubtle
import com.example.ui.theme.JetBrainsMono
import com.example.ui.theme.Manrope
import com.example.ui.theme.PrimaryNeon
import com.example.ui.theme.SecondaryCyan
import com.example.ui.theme.SpaceGrotesk
import com.example.ui.theme.StatusDanger
import com.example.ui.theme.SurfaceCard
import com.example.ui.theme.SurfaceElevated
import com.example.ui.theme.TextMuted
import com.example.ui.theme.TextPrimary
import com.example.ui.theme.TextSecondary
import com.example.ui.viewmodel.StrictFocusUiState
import com.example.ui.viewmodel.StrictFocusViewModel

@Composable
fun DevicesScreen(
    uiState: StrictFocusUiState,
    viewModel: StrictFocusViewModel,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .background(BgCanvas)
    ) {
        StrictHeader(
            title = "DEVICE TELEMETRY",
            statusBadge = "2/2 STRICT SYNC",
            streakDays = uiState.streakDaysTotalGoal,
            digitalTime = uiState.digitalTime24,
            onAvatarClick = { viewModel.showDialog("SUPERVISOR") }
        )

        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            // Heartbeat status banner
            item {
                DeviceSyncOverviewCard()
            }

            item {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "Paired Hardware Nodes",
                        fontFamily = SpaceGrotesk,
                        fontWeight = FontWeight.Bold,
                        fontSize = 16.sp,
                        color = TextPrimary
                    )
                    Text(
                        text = "Encrypted TLS",
                        fontFamily = JetBrainsMono,
                        fontSize = 11.sp,
                        color = SecondaryCyan
                    )
                }
            }

            items(uiState.devices, key = { it.id }) { device ->
                ConnectedDeviceItemCard(
                    device = device,
                    onToggle = { viewModel.toggleDeviceLock(device.id) }
                )
            }

            item {
                StrictSecuritySettingsCard()
            }

            item {
                Spacer(modifier = Modifier.height(24.dp))
            }
        }
    }
}

@Composable
private fun DeviceSyncOverviewCard() {
    Card(
        modifier = Modifier.fillMaxWidth().testTag("device_sync_overview"),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = SurfaceCard),
        border = androidx.compose.foundation.BorderStroke(1.dp, BorderSubtle)
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Box(
                    modifier = Modifier
                        .size(42.dp)
                        .clip(RoundedCornerShape(10.dp))
                        .background(SurfaceElevated),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        imageVector = Icons.Default.Sync,
                        contentDescription = "Sync",
                        tint = PrimaryNeon,
                        modifier = Modifier.size(22.dp)
                    )
                }

                Column {
                    Text(
                        text = "Strict Daemon Telemetry",
                        fontFamily = SpaceGrotesk,
                        fontWeight = FontWeight.Bold,
                        fontSize = 15.sp,
                        color = TextPrimary
                    )
                    Text(
                        text = "Real-time enforcement across nodes",
                        fontFamily = Manrope,
                        fontSize = 12.sp,
                        color = TextMuted
                    )
                }
            }

            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(9999.dp))
                    .background(SurfaceElevated)
                    .padding(horizontal = 10.dp, vertical = 4.dp)
            ) {
                Text(
                    text = "18ms Latency",
                    fontFamily = JetBrainsMono,
                    fontWeight = FontWeight.Bold,
                    fontSize = 11.sp,
                    color = PrimaryNeon
                )
            }
        }
    }
}

@Composable
private fun ConnectedDeviceItemCard(
    device: ConnectedDevice,
    onToggle: () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .testTag("device_item_${device.id}"),
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
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Box(
                    modifier = Modifier
                        .size(40.dp)
                        .clip(RoundedCornerShape(10.dp))
                        .background(SurfaceElevated),
                    contentAlignment = Alignment.Center
                ) {
                    val icon = when (device.id) {
                        "phone" -> Icons.Default.PhoneAndroid
                        "pc" -> Icons.Default.Computer
                        else -> Icons.Default.Headphones
                    }
                    Icon(
                        imageVector = icon,
                        contentDescription = null,
                        tint = SecondaryCyan,
                        modifier = Modifier.size(20.dp)
                    )
                }

                Column {
                    Text(
                        text = device.name,
                        fontFamily = SpaceGrotesk,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 14.sp,
                        color = TextPrimary
                    )
                    Text(
                        text = device.subtitle,
                        fontFamily = Manrope,
                        fontSize = 11.sp,
                        color = TextSecondary,
                        modifier = Modifier.padding(top = 1.dp)
                    )
                    Text(
                        text = device.lastSyncText,
                        fontFamily = JetBrainsMono,
                        fontSize = 10.sp,
                        color = if (device.isLockedDown) PrimaryNeon else TextMuted,
                        modifier = Modifier.padding(top = 2.dp)
                    )
                }
            }

            Switch(
                checked = device.isLockedDown,
                onCheckedChange = { onToggle() },
                colors = SwitchDefaults.colors(
                    checkedThumbColor = PrimaryNeon,
                    checkedTrackColor = PrimaryNeon.copy(alpha = 0.25f),
                    uncheckedThumbColor = TextMuted,
                    uncheckedTrackColor = SurfaceElevated
                )
            )
        }
    }
}

@Composable
private fun StrictSecuritySettingsCard() {
    Card(
        modifier = Modifier.fillMaxWidth().testTag("strict_settings_card"),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = SurfaceCard),
        border = androidx.compose.foundation.BorderStroke(1.dp, BorderSubtle)
    ) {
        Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(
                text = "Tamper Prevention Protocols",
                fontFamily = SpaceGrotesk,
                fontWeight = FontWeight.Bold,
                fontSize = 14.sp,
                color = TextPrimary
            )

            SecurityOptionRow(title = "Bypass Prevention", subtitle = "Blocks uninstalling & safe mode without 24h delay", isEnabled = true)
            SecurityOptionRow(title = "Emergency Friend Override", subtitle = "Requires trusted peer approval to disable", isEnabled = true)
            SecurityOptionRow(title = "Hourly Check Alarm Enforcer", subtitle = "Loud alarm sounds if check missed by 15m", isEnabled = true)
        }
    }
}

@Composable
private fun SecurityOptionRow(
    title: String,
    subtitle: String,
    isEnabled: Boolean
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(SurfaceElevated)
            .padding(10.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(title, fontFamily = Manrope, fontWeight = FontWeight.SemiBold, fontSize = 12.sp, color = TextPrimary)
            Text(subtitle, fontFamily = Manrope, fontSize = 10.sp, color = TextMuted)
        }
        Icon(
            imageVector = Icons.Default.Shield,
            contentDescription = null,
            tint = if (isEnabled) PrimaryNeon else TextMuted,
            modifier = Modifier.size(16.dp)
        )
    }
}
