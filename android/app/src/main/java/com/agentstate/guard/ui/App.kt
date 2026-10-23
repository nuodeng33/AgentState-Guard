package com.agentstate.guard.ui

import androidx.activity.compose.BackHandler
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
import androidx.compose.runtime.DisposableEffect
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
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.agentstate.guard.R
import com.agentstate.guard.ui.link.DeviceLinkUiAdapter
import com.agentstate.guard.ui.link.PairingPhase
import com.agentstate.guard.ui.link.PairingUiState
import com.agentstate.guard.ui.link.RepositoryDeviceLinkUiAdapter
import com.agentstate.guard.ui.link.pairingFailureReasonCode
import com.agentstate.guard.ui.link.supervisionFailureReasonCode
import com.agentstate.guard.ui.screens.AiMonitorScreen
import com.agentstate.guard.ui.screens.ChangesScreen
import com.agentstate.guard.ui.screens.CheckpointsScreen
import com.agentstate.guard.ui.screens.DevicesScreen
import com.agentstate.guard.ui.screens.EnvironmentScreen
import com.agentstate.guard.ui.screens.EvidenceScreen
import com.agentstate.guard.ui.screens.HomeScreen
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
import com.agentstate.guard.ui.state.ConnectionUiState
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.EnvironmentUiState
import com.agentstate.guard.ui.state.EvidenceUiState
import com.agentstate.guard.ui.state.SupervisionActionUiResult
import com.agentstate.guard.ui.state.HomeUiState
import com.agentstate.guard.ui.state.LinkedDesktop
import com.agentstate.guard.ui.state.RecoveryUiState
import com.agentstate.guard.ui.state.SupervisionUiState
import kotlinx.coroutines.CancellationException
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
    PairingPhase.CONFIRMING,
)

/**
 * Product shell: five bottom tabs (Home / Environment / Changes /
 * Supervision / More); More hosts Checkpoints, Recovery, AI Monitor,
 * Devices, and Settings. All data flows through the injected
 * DeviceLinkUiAdapter; production binds the real repository adapter, the
 * noop adapter stays reserved for previews and tests.
 */
@Composable
fun AgentStateApp(adapter: DeviceLinkUiAdapter? = null) {
    val context = LocalContext.current
    val linkAdapter = adapter ?: remember {
        RepositoryDeviceLinkUiAdapter.provideAdapter(context.applicationContext)
    }
    val navController = rememberNavController()
    val navBackStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = navBackStackEntry?.destination?.route
    val scope = rememberCoroutineScope()

    // ---- adapter projections (LOADING until the first answer) ----
    // Foreground refresh: once at start and again on every lifecycle resume
    // (single-flight inside the adapter). dataVersion re-reads projections so
    // a resume-triggered refresh updates every surface without any polling.
    var dataVersion by remember { mutableLongStateOf(0L) }

    LaunchedEffect(linkAdapter) {
        linkAdapter.refresh()
        dataVersion++
    }
    val lifecycleOwner = LocalLifecycleOwner.current
    var foreground by remember { mutableStateOf(lifecycleOwner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) }
    DisposableEffect(linkAdapter, lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_START || event == Lifecycle.Event.ON_RESUME) foreground = true
            if (event == Lifecycle.Event.ON_STOP) foreground = false
            if (event == Lifecycle.Event.ON_RESUME) {
                scope.launch {
                    linkAdapter.refresh()
                    dataVersion++
                }
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    val linked by produceState<Pair<Boolean, LinkedDesktop?>>(false to null, linkAdapter, dataVersion) {
        value = true to linkAdapter.linkedDesktop()
    }
    val homeState by produceState(HomeUiState(DataPhase.LOADING), linkAdapter, dataVersion) {
        value = linkAdapter.homeState()
    }
    val environmentState by produceState(EnvironmentUiState(DataPhase.LOADING), linkAdapter, dataVersion) {
        value = linkAdapter.environmentState()
    }
    val changesState by produceState(ChangesUiState(DataPhase.LOADING), linkAdapter, dataVersion) {
        value = linkAdapter.changesState()
    }
    val supervisionState by produceState(SupervisionUiState(DataPhase.LOADING), linkAdapter, dataVersion) {
        value = linkAdapter.supervisionState()
    }
    val checkpointsState by produceState(CheckpointsUiState(DataPhase.LOADING), linkAdapter, dataVersion) {
        value = linkAdapter.checkpointsState()
    }
    val recoveryState by produceState(RecoveryUiState(DataPhase.LOADING), linkAdapter, dataVersion) {
        value = linkAdapter.recoveryState()
    }
    val aiState by produceState(AiMonitorUiState(DataPhase.LOADING), linkAdapter, dataVersion) {
        value = linkAdapter.aiMonitorState()
    }
    val connectionState by produceState<ConnectionUiState?>(null, linkAdapter, dataVersion) {
        value = linkAdapter.connectionState()
    }

    // One visible pairing attempt; every asynchronous reply is epoch-bound.
    var pairing by remember { mutableStateOf<PairingUiState?>(null) }
    var pairingBusy by remember { mutableStateOf(false) }
    val pairingEpoch = remember { longArrayOf(0L) }
    var nowEpochMs by remember { mutableLongStateOf(System.currentTimeMillis()) }
    var actionBusy by remember { mutableStateOf(false) }
    var actionResult by remember { mutableStateOf<SupervisionActionUiResult?>(null) }
    var localFailure by remember { mutableStateOf<String?>(null) }
    var evidenceId by remember { mutableStateOf<String?>(null) }

    fun showPairing(next: PairingUiState) {
        pairing = next
        if (next.phase == PairingPhase.PAIRED) {
            dataVersion++
            navController.navigate(Tab.Home.route) {
                popUpTo(Tab.Home.route)
                launchSingleTop = true
            }
        }
    }
    fun launchPairing(action: suspend () -> PairingUiState) {
        if (pairingBusy) return
        pairingBusy = true
        val epoch = ++pairingEpoch[0]
        scope.launch {
            try {
                val next = action()
                if (epoch == pairingEpoch[0]) {
                    showPairing(next)
                    if (next.phase == PairingPhase.SAS_PENDING) navController.navigate("connect/sas") { launchSingleTop = true }
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                if (epoch == pairingEpoch[0]) pairing = PairingUiState(PairingPhase.ERROR, reasonCode = pairingFailureReasonCode(error))
            } finally {
                if (epoch == pairingEpoch[0]) pairingBusy = false
            }
        }
    }
    fun abandonPairing() {
        if (pairingBusy) return
        val id = pairing?.pairingId
        pairingEpoch[0]++
        pairing = null
        navController.navigate(Tab.Home.route) { popUpTo(Tab.Home.route); launchSingleTop = true }
        if (id != null) scope.launch { linkAdapter.cancelPairing(id) }
    }
    fun refresh() {
        if (actionBusy) return
        actionBusy = true; localFailure = null
        scope.launch {
            try { linkAdapter.refresh(); dataVersion++ }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { localFailure = "DEVICE_LINK_OFFLINE" }
            finally { actionBusy = false }
        }
    }
    fun openEvidence(id: String) {
        evidenceId = id
        navController.navigate("evidence") { launchSingleTop = true }
    }
    BackHandler(enabled = currentRoute?.startsWith("connect/") == true) { abandonPairing() }

    LaunchedEffect(pairing?.expiresAtEpochMs, pairing?.phase) {
        if (pairing?.phase in IN_FLIGHT_PHASES) while (true) {
            delay(1000); nowEpochMs = System.currentTimeMillis()
        }
    }
    // Repeated equal states still poll; pausing the app stops new network requests.
    LaunchedEffect(pairing?.pairingId, pairing?.phase, linkAdapter) {
        val id = pairing?.pairingId
        if (id != null && pairing?.phase in IN_FLIGHT_PHASES) while (true) {
            delay(800)
            if (!foreground || pairingBusy) continue
            val epoch = pairingEpoch[0]
            pairingBusy = true
            try {
                val next = linkAdapter.pollPairing(id)
                if (epoch == pairingEpoch[0]) showPairing(next)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                if (epoch == pairingEpoch[0]) pairing = PairingUiState(PairingPhase.ERROR, reasonCode = pairingFailureReasonCode(error))
            } finally {
                if (epoch == pairingEpoch[0]) pairingBusy = false
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
                )
            }
            composable(Tab.Environment.route) { EnvironmentScreen(environmentState) }
            composable(Tab.Changes.route) { ChangesScreen(changesState, onEvidence = ::openEvidence) }
            composable(Tab.Supervision.route) {
                SupervisionScreen(supervisionState, busy = actionBusy, result = actionResult,
                    onRefresh = ::refresh, onEvidence = ::openEvidence,
                    onDecision = { id, actionRef, approve ->
                        if (!actionBusy) {
                            actionBusy = true; actionResult = null
                            scope.launch {
                                try {
                                    actionResult = if (approve) linkAdapter.approveOnce(id, actionRef)
                                        else linkAdapter.rejectSupervision(id, actionRef)
                                    dataVersion++
                                } catch (cancelled: CancellationException) { throw cancelled }
                                catch (error: Exception) { actionResult = SupervisionActionUiResult(false, null, supervisionFailureReasonCode(error)) }
                                finally { actionBusy = false }
                            }
                        }
                    })
            }
            composable(Tab.More.route) { MoreScreen(onOpen = { navController.navigate(it.route) }) }

            composable(MoreDestination.Checkpoints.route) { CheckpointsScreen(checkpointsState) }
            composable(MoreDestination.Recovery.route) { RecoveryScreen(recoveryState) }
            composable(MoreDestination.AiMonitor.route) { AiMonitorScreen(aiState) }
            composable(MoreDestination.Devices.route) {
                DevicesScreen(linked.second, connectionState ?: ConnectionUiState.EMPTY,
                    busy = actionBusy || pairingBusy, failureCode = localFailure,
                    onRefresh = ::refresh,
                    onScanQr = { navController.navigate("connect/scan") },
                    onUnpair = {
                        if (!actionBusy && !pairingBusy) {
                            actionBusy = true; localFailure = null
                            scope.launch {
                                try { linkAdapter.unpair(); pairingEpoch[0]++; pairing = null; dataVersion++ }
                                catch (cancelled: CancellationException) { throw cancelled }
                                catch (_: Exception) { localFailure = "UNPAIR_FAILED" }
                                finally { actionBusy = false }
                            }
                        }
                    })
            }
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
                ScanConnectScreen(
                    onBack = ::abandonPairing,
                    busy = pairingBusy,
                    failureCode = pairing?.reasonCode,
                    onPayload = { payload ->
                        if (linked.second == null) launchPairing { linkAdapter.beginScanPairing(payload) }
                    },
                )
            }
            composable("connect/sas") {
                val current = pairing
                if (current != null) {
                    SasScreen(
                        state = current,
                        secondsLeft = current.expiresAtEpochMs?.let { maxOf(0L, (it - nowEpochMs) / 1000) },
                        busy = pairingBusy,
                        onConfirm = {
                            val id = current.pairingId
                            if (id != null && current.phase == PairingPhase.SAS_PENDING)
                                launchPairing { linkAdapter.confirmSas(id) }
                        },
                        onReject = {
                            val id = current.pairingId
                            if (id != null) launchPairing { linkAdapter.rejectSas(id) }
                        },
                        onCancel = ::abandonPairing,
                    )
                } else {
                    LaunchedEffect(Unit) { navController.popBackStack() }
                }
            }
            composable("evidence") {
                val detail by produceState(EvidenceUiState(DataPhase.LOADING), linkAdapter, evidenceId) {
                    value = evidenceId?.let { linkAdapter.evidenceState(it) } ?: EvidenceUiState(DataPhase.EMPTY)
                }
                EvidenceScreen(detail) { navController.popBackStack() }
            }
        }
    }
}
