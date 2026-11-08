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
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.LockOpen
import androidx.compose.material.icons.filled.MilitaryTech
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.Restaurant
import androidx.compose.material.icons.filled.SmartDisplay
import androidx.compose.material.icons.filled.SportsEsports
import androidx.compose.material.icons.filled.Stars
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.data.model.RewardPrivilege
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
import com.example.ui.theme.StatusWarning
import com.example.ui.theme.SurfaceCard
import com.example.ui.theme.SurfaceElevated
import com.example.ui.theme.TextMuted
import com.example.ui.theme.TextPrimary
import com.example.ui.theme.TextSecondary
import com.example.ui.viewmodel.StrictFocusUiState
import com.example.ui.viewmodel.StrictFocusViewModel

@Composable
fun RewardsScreen(
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
            title = "REWARDS & VAULT",
            statusBadge = "${uiState.userPointsBank} PTS READY",
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
            // Points Balance Hero Card
            item {
                PointsBalanceHeroCard(
                    balance = uiState.userPointsBank,
                    streak = uiState.streakDays
                )
            }

            item {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "Unlockable Privilege Passes",
                        fontFamily = SpaceGrotesk,
                        fontWeight = FontWeight.Bold,
                        fontSize = 16.sp,
                        color = TextPrimary
                    )
                    Text(
                        text = "Strict Authorization",
                        fontFamily = JetBrainsMono,
                        fontSize = 11.sp,
                        color = SecondaryCyan
                    )
                }
            }

            items(uiState.rewardPrivileges, key = { it.id }) { reward ->
                RewardItemCard(
                    reward = reward,
                    canAfford = uiState.userPointsBank >= reward.cost,
                    onRedeem = { viewModel.redeemReward(reward.id) }
                )
            }

            item {
                StreakMilestonesCard(streakDays = uiState.streakDays)
            }

            item {
                Spacer(modifier = Modifier.height(24.dp))
            }
        }
    }
}

@Composable
private fun PointsBalanceHeroCard(
    balance: Int,
    streak: Int
) {
    Card(
        modifier = Modifier.fillMaxWidth().testTag("points_balance_card"),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = SurfaceCard),
        border = androidx.compose.foundation.BorderStroke(1.dp, BorderSubtle)
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "AVAILABLE VAULT BALANCE",
                    fontFamily = Manrope,
                    fontWeight = FontWeight.Bold,
                    fontSize = 11.sp,
                    letterSpacing = 0.5.sp,
                    color = TextMuted
                )
                Row(
                    verticalAlignment = Alignment.Bottom,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    modifier = Modifier.padding(top = 4.dp)
                ) {
                    Text(
                        text = "$balance",
                        fontFamily = SpaceGrotesk,
                        fontWeight = FontWeight.Bold,
                        fontSize = 34.sp,
                        color = PrimaryNeon
                    )
                    Text(
                        text = "POINTS",
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 13.sp,
                        color = SecondaryCyan,
                        modifier = Modifier.padding(bottom = 6.dp)
                    )
                }
            }

            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(12.dp))
                    .background(SurfaceElevated)
                    .padding(horizontal = 12.dp, vertical = 8.dp)
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    Icon(
                        imageVector = Icons.Default.MilitaryTech,
                        contentDescription = "Rank",
                        tint = StatusWarning,
                        modifier = Modifier.size(18.dp)
                    )
                    Text(
                        text = "TIER II • GUARDIAN",
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 11.sp,
                        color = StatusWarning
                    )
                }
            }
        }
    }
}

@Composable
private fun RewardItemCard(
    reward: RewardPrivilege,
    canAfford: Boolean,
    onRedeem: () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .testTag("reward_item_${reward.id}"),
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
                modifier = Modifier.weight(1f),
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
                    val iconVector = when (reward.iconName) {
                        "smart_display" -> Icons.Default.SmartDisplay
                        "photo_camera" -> Icons.Default.PhotoCamera
                        "sports_esports" -> Icons.Default.SportsEsports
                        else -> Icons.Default.Restaurant
                    }
                    val iconTint = when (reward.iconName) {
                        "smart_display" -> PrimaryNeon
                        "photo_camera" -> PinkAccent
                        "sports_esports" -> SecondaryCyan
                        else -> StatusWarning
                    }
                    Icon(
                        imageVector = iconVector,
                        contentDescription = null,
                        tint = iconTint,
                        modifier = Modifier.size(20.dp)
                    )
                }

                Column {
                    Text(
                        text = reward.title,
                        fontFamily = SpaceGrotesk,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 14.sp,
                        color = TextPrimary
                    )
                    Text(
                        text = reward.description,
                        fontFamily = Manrope,
                        fontSize = 11.sp,
                        color = TextMuted,
                        maxLines = 1,
                        modifier = Modifier.padding(top = 2.dp)
                    )
                }
            }

            if (reward.isUnlocked) {
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(9999.dp))
                        .background(PrimaryNeon.copy(alpha = 0.2f))
                        .padding(horizontal = 10.dp, vertical = 6.dp)
                ) {
                    Text(
                        text = "ACTIVE",
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 11.sp,
                        color = PrimaryNeon
                    )
                }
            } else {
                Button(
                    onClick = onRedeem,
                    enabled = canAfford,
                    modifier = Modifier
                        .height(36.dp)
                        .testTag("redeem_btn_${reward.id}"),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (canAfford) PrimaryNeon else SurfaceElevated,
                        contentColor = if (canAfford) OnPrimaryFixed else TextMuted
                    ),
                    shape = RoundedCornerShape(9999.dp)
                ) {
                    Text(
                        text = "${reward.cost} PTS",
                        fontFamily = JetBrainsMono,
                        fontWeight = FontWeight.Bold,
                        fontSize = 12.sp
                    )
                }
            }
        }
    }
}

@Composable
private fun StreakMilestonesCard(streakDays: Int) {
    Card(
        modifier = Modifier.fillMaxWidth().testTag("milestones_card"),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = SurfaceCard),
        border = androidx.compose.foundation.BorderStroke(1.dp, BorderSubtle)
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text(
                text = "Streak Milestones",
                fontFamily = SpaceGrotesk,
                fontWeight = FontWeight.Bold,
                fontSize = 14.sp,
                color = TextPrimary
            )
            Spacer(modifier = Modifier.height(10.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                MilestoneBadge(label = "7 Days", title = "Recruit", isUnlocked = streakDays >= 7)
                MilestoneBadge(label = "14 Days", title = "Enforcer", isUnlocked = streakDays >= 14)
                MilestoneBadge(label = "30 Days", title = "Iron Will", isUnlocked = streakDays >= 30)
            }
        }
    }
}

@Composable
private fun MilestoneBadge(
    label: String,
    title: String,
    isUnlocked: Boolean
) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .clip(RoundedCornerShape(10.dp))
            .background(SurfaceElevated)
            .padding(horizontal = 14.dp, vertical = 8.dp)
    ) {
        Icon(
            imageVector = Icons.Default.Stars,
            contentDescription = null,
            tint = if (isUnlocked) PrimaryNeon else TextMuted,
            modifier = Modifier.size(20.dp)
        )
        Text(
            text = label,
            fontFamily = JetBrainsMono,
            fontWeight = FontWeight.Bold,
            fontSize = 11.sp,
            color = if (isUnlocked) PrimaryNeon else TextSecondary,
            modifier = Modifier.padding(top = 4.dp)
        )
        Text(
            text = title,
            fontFamily = Manrope,
            fontSize = 10.sp,
            color = TextMuted
        )
    }
}
