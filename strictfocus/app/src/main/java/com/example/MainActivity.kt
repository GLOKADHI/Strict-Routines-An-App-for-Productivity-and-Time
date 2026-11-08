package com.example

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.example.ui.components.AnimatedChronoSlidersSheet
import com.example.ui.components.CyberBottomNavBar
import com.example.ui.components.StrictBotCompanionHoloDialog
import com.example.ui.components.StrictBotSupervisorDialog
import com.example.ui.components.StrictRulesDialog
import com.example.ui.components.VerificationCheckInDialog
import com.example.ui.screens.DashboardScreen
import com.example.ui.screens.DevicesScreen
import com.example.ui.screens.FocusScreen
import com.example.ui.screens.RewardsScreen
import com.example.ui.screens.TasksScreen
import com.example.ui.theme.StrictFocusTheme
import com.example.ui.viewmodel.AppTab
import com.example.ui.viewmodel.StrictFocusViewModel

class MainActivity : ComponentActivity() {

    private val viewModel: StrictFocusViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            StrictFocusTheme {
                val uiState by viewModel.uiState.collectAsStateWithLifecycle()
                val snackbarHostState = remember { SnackbarHostState() }

                // Snackbar listener
                LaunchedEffect(uiState.snackbarMessage) {
                    uiState.snackbarMessage?.let { msg ->
                        snackbarHostState.showSnackbar(msg)
                        viewModel.clearSnackbar()
                    }
                }

                // Handle Android back button
                BackHandler(enabled = uiState.currentTab != AppTab.HOME) {
                    viewModel.selectTab(AppTab.HOME)
                }

                Scaffold(
                    modifier = Modifier.fillMaxSize(),
                    bottomBar = {
                        CyberBottomNavBar(
                            selectedTab = uiState.currentTab,
                            onTabSelected = { viewModel.selectTab(it) }
                        )
                    },
                    snackbarHost = {
                        SnackbarHost(hostState = snackbarHostState)
                    }
                ) { innerPadding ->
                    Box(modifier = Modifier.fillMaxSize().padding(bottom = innerPadding.calculateBottomPadding())) {
                        AnimatedContent(
                            targetState = uiState.currentTab,
                            transitionSpec = { fadeIn() togetherWith fadeOut() },
                            label = "tab_transition"
                        ) { tab ->
                            when (tab) {
                                AppTab.HOME -> DashboardScreen(
                                    uiState = uiState,
                                    viewModel = viewModel
                                )
                                AppTab.TASKS -> TasksScreen(
                                    uiState = uiState,
                                    viewModel = viewModel
                                )
                                AppTab.FOCUS -> FocusScreen(
                                    uiState = uiState,
                                    viewModel = viewModel
                                )
                                AppTab.REWARDS -> RewardsScreen(
                                    uiState = uiState,
                                    viewModel = viewModel
                                )
                                AppTab.DEVICES -> DevicesScreen(
                                    uiState = uiState,
                                    viewModel = viewModel
                                )
                            }
                        }

                        // Dialogs
                        when (uiState.activeDialog) {
                            "RULES" -> StrictRulesDialog(
                                onDismiss = { viewModel.dismissDialog() }
                            )
                            "SUPERVISOR" -> StrictBotCompanionHoloDialog(
                                uiState = uiState,
                                viewModel = viewModel,
                                onDismiss = { viewModel.dismissDialog() }
                            )
                            "VERIFY", "WAKEUP", "GYM", "BATH", "TRAVEL" -> {
                                val slotTitle = when (uiState.activeDialog) {
                                    "WAKEUP" -> "Wake-Up Protocol"
                                    "GYM" -> "Gym & Workout Protocol"
                                    "BATH" -> "Bath & Hygiene Protocol"
                                    "TRAVEL" -> "Travel & Commute Protocol"
                                    else -> uiState.nextHourlyWindow
                                }
                                VerificationCheckInDialog(
                                    slotTitle = slotTitle,
                                    onVerify = {
                                        viewModel.performHourlyVerification("slot_3")
                                    },
                                    onDismiss = { viewModel.dismissDialog() }
                                )
                            }
                            "CHRONO" -> AnimatedChronoSlidersSheet(
                                uiState = uiState,
                                viewModel = viewModel,
                                onDismiss = { viewModel.dismissDialog() }
                            )
                        }
                    }
                }
            }
        }
    }
}
