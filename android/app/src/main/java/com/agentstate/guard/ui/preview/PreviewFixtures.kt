package com.agentstate.guard.ui.preview

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview
import com.agentstate.guard.ui.link.PairingPhase
import com.agentstate.guard.ui.link.PairingUiState
import com.agentstate.guard.ui.screens.EnvironmentScreen
import com.agentstate.guard.ui.screens.HomeScreen
import com.agentstate.guard.ui.screens.SasScreen
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.EnvironmentItemUi
import com.agentstate.guard.ui.state.EnvironmentUiState
import com.agentstate.guard.ui.state.HomeUiState
import com.agentstate.guard.ui.theme.AgentStateTheme

/**
 * Preview/demo fixtures. These values exist ONLY for Compose previews and
 * tests — they are never referenced from production state paths.
 */
object PreviewFixtures {
    val demoEnvironment = EnvironmentUiState(
        phase = DataPhase.CONNECTED,
        items = listOf(
            EnvironmentItemUi("Claude Code", "2.1.217", "OK"),
            EnvironmentItemUi("Node.js", "v22.23.1", "OK"),
            EnvironmentItemUi("Python", "3.11.2", "OK"),
            EnvironmentItemUi("Docker", null, "UNREACHABLE"),
            EnvironmentItemUi("Git", "2.39.5", "OK"),
        ),
    )

    val demoHome = HomeUiState(
        phase = DataPhase.CONNECTED,
        desktopName = "DESKTOP-DEMO",
        overallStatus = "AVAILABLE",
        runtimeSummary = "3",
        agentsSummary = "1",
        pendingSupervision = 0,
        changesCount = 2,
        lastCheckpoint = "cp-demo",
        aiStatus = "Not configured",
        recoveryStatus = "R0",
    )

    val demoSas = PairingUiState(
        phase = PairingPhase.SAS_PENDING,
        pairingId = "preview-pairing",
        desktopName = "DESKTOP-DEMO",
        sasCode = "123456",
    )
}

@Preview(showBackground = true, backgroundColor = 0xFF0F172A)
@Composable
private fun HomeUnpairedPreview() {
    AgentStateTheme {
        HomeScreen(paired = false, state = null, onScanQr = {})
    }
}

@Preview(showBackground = true, backgroundColor = 0xFF0F172A)
@Composable
private fun HomeConnectedPreview() {
    AgentStateTheme {
        HomeScreen(paired = true, state = PreviewFixtures.demoHome, onScanQr = {})
    }
}

@Preview(showBackground = true, backgroundColor = 0xFF0F172A)
@Composable
private fun EnvironmentPreview() {
    AgentStateTheme {
        EnvironmentScreen(PreviewFixtures.demoEnvironment)
    }
}

@Preview(showBackground = true, backgroundColor = 0xFF0F172A)
@Composable
private fun SasPreview() {
    AgentStateTheme {
        SasScreen(state = PreviewFixtures.demoSas, secondsLeft = 240, onConfirm = {}, onReject = {})
    }
}
