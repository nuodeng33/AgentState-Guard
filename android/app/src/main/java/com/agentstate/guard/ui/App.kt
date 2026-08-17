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
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import androidx.navigation.NavType
import com.agentstate.guard.R
import com.agentstate.guard.ui.link.DeviceLinkUiAdapter
import com.agentstate.guard.ui.link.PairingPhase
import com.agentstate.guard.ui.link.PairingUiState
import com.agentstate.guard.ui.link.RepositoryDeviceLinkUiAdapter
import com.agentstate.guard.ui.link.pairingFailureReasonCode
import com.agentstate.guard.ui.link.unpairFailureReasonCode
import com.agentstate.guard.ui.screens.AiAdvisoryCard
import com.agentstate.guard.ui.screens.ChangesScreen
import com.agentstate.guard.ui.screens.CheckpointsScreen
import com.agentstate.guard.ui.screens.DevicesScreen
import com.agentstate.guard.ui.screens.EnvironmentScreen
import com.agentstate.guard.ui.screens.HomeScreen
import com.agentstate.guard.ui.screens.MoreDestination
import com.agentstate.guard.ui.screens.MoreScreen
import com.agentstate.guard.ui.screens.RecoveryScreen
import com.agentstate.guard.ui.screens.SasScreen
import com.agentstate.guard.ui.screens.ScanConnectScreen
import com.agentstate.guard.ui.screens.SettingsScreen
import com.agentstate.guard.ui.screens.EvidenceScreen
import com.agentstate.guard.ui.screens.SupervisionScreen
import com.agentstate.guard.ui.state.AiAdvisoryUiState
import com.agentstate.guard.ui.state.ChangesUiState
import com.agentstate.guard.ui.state.CheckpointsUiState
import com.agentstate.guard.ui.state.ConnectionUiState
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

/** Pairing phases where polling should keep the offer alive. */
private val IN_FLIGHT_PHASES = setOf(
    PairingPhase.PAIRING_CREATED,
    PairingPhase.WAITING_FOR_DESKTOP,
    PairingPhase.SAS_PENDING,
    PairingPhase.CONFIRMING,
)

/** Terminal pairing phases: an open SAS shell dismisses itself on these. */
private val SAS_TERMINAL_PHASES = setOf(
    PairingPhase.PAIRED,
    PairingPhase.EXPIRED,
    PairingPhase.REJECTED,
    PairingPhase.ERROR,
)

/**
 * Product shell: five bottom tabs (Home / Environment / Changes /
 * Supervision / More); More hosts Checkpoints, Recovery, Devices, and
 * Settings. All data flows through the injected DeviceLinkUiAdapter;
 * production binds the real repository adapter, the noop adapter stays
 * reserved for previews and tests. AI advice is rendered as context cards
 * only — there is no standalone AI product surface on Android.
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
    DisposableEffect(linkAdapter, lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
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
    val aiState by produceState(AiAdvisoryUiState(DataPhase.LOADING), linkAdapter, dataVersion) {
        value = linkAdapter.aiAdvisoryState()
    }
    val connectionState by produceState<ConnectionUiState?>(null, linkAdapter, dataVersion) {
        value = linkAdapter.connectionState()
    }

    // ---- supervision mutation machine -----------------------------------------
    var unpairInProgress by remember { mutableStateOf(false) }
    var unpairResultReason by remember { mutableStateOf<String?>(null) }
    var actionBusy by remember { mutableStateOf<String?>(null) }
    var actionResultReason by remember { mutableStateOf<String?>(null) }
    fun runSupervisionAction(
        sessionId: String,
        actionRef: String,
        approve: Boolean,
    ) {
        if (actionBusy != null) return
        actionBusy = sessionId
        actionResultReason = null
        scope.launch {
            val outcome = try {
                if (approve) linkAdapter.approveOnce(sessionId, actionRef)
                else linkAdapter.rejectSupervision(sessionId, actionRef)
            } catch (unsupported: Exception) {
                actionResultReason = pairingFailureReasonCode(unsupported)
                null
            }
            // A null outcome can only come from the noop/preview adapter throwing
            // before it finished the callback; map it to the generic suppression
            // failure so the chip clears cleanly on poll.
            actionResultReason = outcome?.reasonCode ?: actionResultReason
            actionBusy = null
            // Projections are stale after any authoritative mutation: force a
            // refresh of every surface once the round trip completes.
            linkAdapter.refresh()
            dataVersion++
        }
    }

    // ---- pairing shell state ----
    var pairing by remember { mutableStateOf<PairingUiState?>(null) }
    var nowEpochMs by remember { mutableLongStateOf(System.currentTimeMillis()) }

    // Pairing SAS terminal sync: when a pairing lands with a SAS payload,
    // navigate into the SAS shell once; when a running SAS shell observes a
    // terminal phase (paired/failed/expired/rejected), close it automatically
    // so stale confirmations never linger.
    LaunchedEffect(pairing, currentRoute) {
        val state = pairing
        when {
            state == null -> Unit
            state.pairingId != null && state.sasCode != null &&
                currentRoute == "connect/scan" -> navController.navigate("connect/sas")
            state.phase in SAS_TERMINAL_PHASES && currentRoute == "connect/sas" ->
                navController.popBackStack()
        }
    }

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
                    aiAdvisory = aiState,
                    onScanQr = { navController.navigate("connect/scan") },
                )
            }
            composable(Tab.Environment.route) { EnvironmentScreen(environmentState) }
            composable(Tab.Changes.route) {
                ChangesScreen(
                    state = changesState,
                    onOpenEvidence = { eventId ->
                        navController.navigate("evidence/${android.net.Uri.encode(eventId)}")
                    },
                )
            }
            composable(Tab.Supervision.route) {
                SupervisionScreen(
                    state = supervisionState,
                    onApprove = { sessionId, actionRef ->
                        runSupervisionAction(sessionId, actionRef, approve = true)
                    },
                    onReject = { sessionId, actionRef ->
                        runSupervisionAction(sessionId, actionRef, approve = false)
                    },
                    actionInProgressSessionId = actionBusy?.takeIf { actionResultReason == null },
                    actionResultReasonCode = actionResultReason,
                )
            }
            composable(Tab.More.route) { MoreScreen(onOpen = { navController.navigate(it.route) }) }

            composable(MoreDestination.Checkpoints.route) { CheckpointsScreen(checkpointsState) }
            composable(MoreDestination.Recovery.route) { RecoveryScreen(recoveryState) }
            composable(MoreDestination.Devices.route) {
                DevicesScreen(
                    linked = linked.second,
                    connection = connectionState,
                    isUnpairing = unpairInProgress,
                    unpairFailureReasonCode = unpairResultReason,
                    onUnpair = {
                        if (!unpairInProgress) {
                            unpairResultReason = null
                            unpairInProgress = true
                            scope.launch {
                                try {
                                    linkAdapter.unpair()
                                } catch (error: Exception) {
                                    unpairResultReason = unpairFailureReasonCode(error)
                                }
                                unpairInProgress = false
                                dataVersion++
                            }
                        }
                    },
                )
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

            composable(
                route = "evidence/{event_id}",
                arguments = listOf(navArgument("event_id") { type = NavType.StringType }),
            ) { entry ->
                val eventId = entry.arguments?.getString("event_id") ?: ""
                val detail by produceState<com.agentstate.guard.ui.state.EvidenceUiState?>(
                    initialValue = null,
                    eventId,
                    linkAdapter,
                    dataVersion,
                ) {
                    value = linkAdapter.evidenceState(eventId)
                }
                EvidenceScreen(
                    eventId = eventId,
                    state = detail,
                    onBack = { navController.popBackStack() },
                )
            }

            composable("connect/scan") {
                ScanConnectScreen(
                    onBack = { navController.popBackStack() },
                    onPayloadDetected = { payloadText ->
                        scope.launch {
                            pairing = try {
                                linkAdapter.beginScanPairing(payloadText)
                            } catch (unsupported: Exception) {
                                PairingUiState(
                                    PairingPhase.ERROR,
                                    reasonCode = pairingFailureReasonCode(unsupported),
                                )
                            }
                        }
                    },
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
