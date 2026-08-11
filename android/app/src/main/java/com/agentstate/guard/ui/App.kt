package com.agentstate.guard.ui

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.Difference
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.agentstate.guard.R
import com.agentstate.guard.ui.link.DeviceLinkUiAdapter
import com.agentstate.guard.ui.link.NoopDeviceLinkUiAdapter
import com.agentstate.guard.ui.link.PairingPhase
import com.agentstate.guard.ui.link.PairingUiState
import com.agentstate.guard.ui.link.pairingFailureReasonCode
import com.agentstate.guard.ui.screens.AiMonitorScreen
import com.agentstate.guard.ui.screens.ChangesScreen
import com.agentstate.guard.ui.screens.CheckpointsScreen
import com.agentstate.guard.ui.screens.DevicesScreen
import com.agentstate.guard.ui.screens.EnvironmentScreen
import com.agentstate.guard.ui.screens.HomeScreen
import com.agentstate.guard.ui.screens.LanDiscoveryScreen
import com.agentstate.guard.ui.screens.ManualAddressScreen
import com.agentstate.guard.ui.screens.MoreDestination
import com.agentstate.guard.ui.screens.MoreScreen
import com.agentstate.guard.ui.screens.RecoveryScreen
import com.agentstate.guard.ui.screens.SasScreen
import com.agentstate.guard.ui.screens.ScanConnectScreen
import com.agentstate.guard.ui.screens.SettingsScreen
import com.agentstate.guard.ui.screens.SupervisionScreen
import com.agentstate.guard.ui.state.AiMonitorUiState
import com.agentstate.guard.ui.state.ChangesUiState
import com.agentstate.guard.ui.state.CheckpointsUiState
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.EnvironmentUiState
import com.agentstate.guard.ui.state.HomeUiState
import com.agentstate.guard.ui.state.LinkedDesktop
import com.agentstate.guard.ui.state.RecoveryUiState
import com.agentstate.guard.ui.state.SupervisionUiState
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private enum class Tab(val route: String, val labelRes: Int) {
    Home("home", R.string.nav_home),
    Environment("environment", R.string.nav_environment),
    Changes("changes", R.string.nav_changes),
    Supervision("supervision", R.string.nav_supervision),
    More("more", R.string.nav_more),
}

private val IN_FLIGHT_PHASES = setOf(
    PairingPhase.PAIRING_CREATED,
    PairingPhase.WAITING_FOR_DESKTOP,
    PairingPhase.SAS_PENDING,
)

/**
 * Product shell: five bottom tabs (Home / Environment / Changes /
 * Supervision / More); More hosts Checkpoints, Recovery, AI Monitor,
 * Devices, and Settings. All data flows through the injected
 * DeviceLinkUiAdapter; the default noop adapter keeps every surface in an
 * honest no-data state.
 */
@Composable
fun AgentStateApp(adapter: DeviceLinkUiAdapter? = null) {
    val linkAdapter = adapter ?: remember { NoopDeviceLinkUiAdapter() }
    val navController = rememberNavController()
    val navBackStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = navBackStackEntry?.destination?.route
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    // ---- adapter projections (LOADING until the first answer) ----
    val linked by produceState<Pair<Boolean, LinkedDesktop?>>(false to null, linkAdapter) {
        value = true to linkAdapter.linkedDesktop()
    }
    val homeState by produceState(HomeUiState(DataPhase.LOADING), linkAdapter) {
        value = linkAdapter.homeState()
    }
    val environmentState by produceState(EnvironmentUiState(DataPhase.LOADING), linkAdapter) {
        value = linkAdapter.environmentState()
    }
    val changesState by produceState(ChangesUiState(DataPhase.LOADING), linkAdapter) {
        value = linkAdapter.changesState()
    }
    val supervisionState by produceState(SupervisionUiState(DataPhase.LOADING), linkAdapter) {
        value = linkAdapter.supervisionState()
    }
    val checkpointsState by produceState(CheckpointsUiState(DataPhase.LOADING), linkAdapter) {
        value = linkAdapter.checkpointsState()
    }
    val recoveryState by produceState(RecoveryUiState(DataPhase.LOADING), linkAdapter) {
        value = linkAdapter.recoveryState()
    }
    val aiState by produceState(AiMonitorUiState(DataPhase.LOADING), linkAdapter) {
        value = linkAdapter.aiMonitorState()
    }

    // ---- pairing shell state ----
    var pairing by remember { mutableStateOf<PairingUiState?>(null) }
    var nowEpochMs by remember { mutableLongStateOf(System.currentTimeMillis()) }

    LaunchedEffect(pairing?.expiresAtEpochMs) {
        if (pairing?.expiresAtEpochMs != null) {
            while (true) {
                delay(1000)
                nowEpochMs = System.currentTimeMillis()
            }
        }
    }

    LaunchedEffect(pairing, linkAdapter) {
        val current = pairing
        if (current?.pairingId != null && current.phase in IN_FLIGHT_PHASES) {
            delay(800)
            pairing = try {
                linkAdapter.pollPairing(current.pairingId)
            } catch (unsupported: Exception) {
                PairingUiState(
                    PairingPhase.ERROR,
                    reasonCode = pairingFailureReasonCode(unsupported),
                )
            }
        }
    }

    var languagePreference by remember { mutableStateOf(LocaleController.loadPreference(context)) }

    Scaffold(
        bottomBar = {
            NavigationBar {
                Tab.entries.forEach { tab ->
                    NavigationBarItem(
                        icon = {
                            when (tab) {
                                Tab.Home -> Icon(Icons.Default.Home, null)
                                Tab.Environment -> Icon(Icons.Default.Build, null)
                                Tab.Changes -> Icon(Icons.Default.Difference, null)
                                Tab.Supervision -> Icon(Icons.Default.Visibility, null)
                                Tab.More -> Icon(Icons.Default.Menu, null)
                            }
                        },
                        label = { Text(stringResource(tab.labelRes)) },
                        selected = currentRoute == tab.route ||
                            (tab == Tab.More && currentRoute?.startsWith("more/") == true),
                        onClick = {
                            if (currentRoute != tab.route) {
                                navController.navigate(tab.route) {
                                    popUpTo(Tab.Home.route) { saveState = true }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            }
                        },
                    )
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = Tab.Home.route,
            modifier = Modifier.padding(padding),
        ) {
            composable(Tab.Home.route) {
                HomeScreen(
                    paired = if (linked.first) linked.second != null else null,
                    state = homeState,
                    onScanQr = { navController.navigate("connect/scan") },
                    onFindLan = { navController.navigate("connect/lan") },
                    onManualAddress = { navController.navigate("connect/manual") },
                )
            }
            composable(Tab.Environment.route) { EnvironmentScreen(environmentState) }
            composable(Tab.Changes.route) { ChangesScreen(changesState) }
            composable(Tab.Supervision.route) { SupervisionScreen(supervisionState) }
            composable(Tab.More.route) { MoreScreen(onOpen = { navController.navigate(it.route) }) }

            composable(MoreDestination.Checkpoints.route) { CheckpointsScreen(checkpointsState) }
            composable(MoreDestination.Recovery.route) { RecoveryScreen(recoveryState) }
            composable(MoreDestination.AiMonitor.route) { AiMonitorScreen(aiState) }
            composable(MoreDestination.Devices.route) { DevicesScreen(linked.second) }
            composable(MoreDestination.Settings.route) {
                SettingsScreen(
                    preference = languagePreference,
                    onPreferenceChange = { preference ->
                        LocaleController.savePreference(context, preference)
                        languagePreference = preference
                        (context as? android.app.Activity)?.recreate()
                    },
                )
            }

            composable("connect/scan") {
                ScanConnectScreen(onBack = { navController.popBackStack() })
            }
            composable("connect/lan") {
                LanDiscoveryScreen(onBack = { navController.popBackStack() })
            }
            composable("connect/manual") {
                ManualAddressScreen(
                    onSubmit = { address ->
                        scope.launch {
                            try {
                                pairing = linkAdapter.beginManualPairing(address)
                                navController.navigate("connect/sas")
                            } catch (unsupported: Exception) {
                                // No Device Link transport in this build: stay on the shell.
                            }
                        }
                    },
                    onBack = { navController.popBackStack() },
                )
            }
            composable("connect/sas") {
                val current = pairing
                if (current?.sasCode != null) {
                    SasScreen(
                        state = current,
                        secondsLeft = current.expiresAtEpochMs?.let {
                            maxOf(0L, (it - nowEpochMs) / 1000)
                        },
                        onConfirm = {
                            val id = current.pairingId ?: return@SasScreen
                            pairing = current.copy(phase = PairingPhase.CONFIRMING)
                            scope.launch {
                                pairing = try {
                                    linkAdapter.confirmSas(id)
                                } catch (unsupported: Exception) {
                                    PairingUiState(
                                        PairingPhase.ERROR,
                                        reasonCode = pairingFailureReasonCode(unsupported),
                                    )
                                }
                            }
                        },
                        onReject = {
                            val id = current.pairingId ?: return@SasScreen
                            scope.launch {
                                pairing = try {
                                    linkAdapter.rejectSas(id)
                                } catch (unsupported: Exception) {
                                    PairingUiState(
                                        PairingPhase.ERROR,
                                        reasonCode = pairingFailureReasonCode(unsupported),
                                    )
                                }
                                navController.popBackStack()
                            }
                        },
                    )
                } else {
                    // No adapter-supplied SAS: never fabricate one.
                    LaunchedEffect(Unit) { navController.popBackStack() }
                }
            }
        }
    }
}
