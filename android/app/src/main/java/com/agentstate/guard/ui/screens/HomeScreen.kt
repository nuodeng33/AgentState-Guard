package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

data class HomeState(
    val desktopConnected: Boolean = false,
    val desktopName: String = "",
    val health: String = "Unknown",
    val changes: Int = 0,
    val lastCheckpoint: String = "None",
    val aiStatus: String = "Not configured",
)

@Composable
fun HomeScreen(state: HomeState = HomeState()) {
    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text("🛡️ AgentState Guard", style = MaterialTheme.typography.headlineSmall)

        StatusCard("Connected Computer",
            if (state.desktopConnected) state.desktopName else "Not connected",
            state.desktopConnected)

        StatusCard("Health", state.health, state.health == "PASS")

        StatusCard("Changes", if (state.changes > 0) "$state.changes" else "0",
            state.changes == 0)

        StatusCard("Last Checkpoint", state.lastCheckpoint, state.lastCheckpoint != "None")

        StatusCard("AI Monitor", state.aiStatus, state.aiStatus != "Not configured")
    }
}

@Composable
private fun StatusCard(label: String, value: String, positive: Boolean) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.padding(16.dp).fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(label, style = MaterialTheme.typography.titleSmall)
            Text(value, color = if (positive)
                MaterialTheme.colorScheme.primary
            else MaterialTheme.colorScheme.error)
        }
    }
}
