package com.example.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CalendarToday
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.MilitaryTech
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.ui.theme.BgSurface
import com.example.ui.theme.Manrope
import com.example.ui.theme.PrimaryNeon
import com.example.ui.theme.SurfaceCard
import com.example.ui.theme.TextMuted
import com.example.ui.theme.TextSecondary
import com.example.ui.viewmodel.AppTab

@Composable
fun CyberBottomNavBar(
    selectedTab: AppTab,
    onTabSelected: (AppTab) -> Unit
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .shadow(16.dp, ambientColor = Color.Black, spotColor = Color.Black)
            .background(BgSurface.copy(alpha = 0.96f))
            .navigationBarsPadding()
            .height(64.dp)
            .testTag("cyber_bottom_nav_bar")
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(64.dp)
                .padding(horizontal = 8.dp),
            horizontalArrangement = Arrangement.SpaceAround,
            verticalAlignment = Alignment.CenterVertically
        ) {
            // Home Tab
            NavTabItem(
                label = "Home",
                isSelected = selectedTab == AppTab.HOME,
                testTag = "nav_home",
                icon = {
                    Icon(
                        imageVector = Icons.Default.Dashboard,
                        contentDescription = "Home",
                        tint = if (selectedTab == AppTab.HOME) PrimaryNeon else TextMuted,
                        modifier = Modifier.size(22.dp)
                    )
                },
                onClick = { onTabSelected(AppTab.HOME) }
            )

            // Tasks Tab
            NavTabItem(
                label = "Tasks",
                isSelected = selectedTab == AppTab.TASKS,
                testTag = "nav_tasks",
                icon = {
                    Icon(
                        imageVector = Icons.Default.CalendarToday,
                        contentDescription = "Tasks",
                        tint = if (selectedTab == AppTab.TASKS) PrimaryNeon else TextMuted,
                        modifier = Modifier.size(22.dp)
                    )
                },
                onClick = { onTabSelected(AppTab.TASKS) }
            )

            // Rewards Tab
            NavTabItem(
                label = "Rewards",
                isSelected = selectedTab == AppTab.REWARDS,
                testTag = "nav_rewards",
                icon = {
                    Icon(
                        imageVector = Icons.Default.MilitaryTech,
                        contentDescription = "Rewards",
                        tint = if (selectedTab == AppTab.REWARDS) PrimaryNeon else TextMuted,
                        modifier = Modifier.size(22.dp)
                    )
                },
                onClick = { onTabSelected(AppTab.REWARDS) }
            )

            // Devices Tab
            NavTabItem(
                label = "Devices",
                isSelected = selectedTab == AppTab.DEVICES,
                testTag = "nav_devices",
                icon = {
                    Icon(
                        imageVector = Icons.Default.Devices,
                        contentDescription = "Devices",
                        tint = if (selectedTab == AppTab.DEVICES) PrimaryNeon else TextMuted,
                        modifier = Modifier.size(22.dp)
                    )
                },
                onClick = { onTabSelected(AppTab.DEVICES) }
            )
        }
    }
}

@Composable
private fun NavTabItem(
    label: String,
    isSelected: Boolean,
    testTag: String,
    icon: @Composable () -> Unit,
    onClick: () -> Unit
) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
        modifier = Modifier
            .clip(CircleShape)
            .clickable(onClick = onClick)
            .padding(horizontal = 8.dp, vertical = 6.dp)
            .testTag(testTag)
    ) {
        icon()
        Text(
            text = label,
            fontFamily = Manrope,
            fontWeight = if (isSelected) FontWeight.Bold else FontWeight.SemiBold,
            fontSize = 11.sp,
            color = if (isSelected) PrimaryNeon else TextMuted,
            modifier = Modifier.padding(top = 2.dp)
        )
    }
}
