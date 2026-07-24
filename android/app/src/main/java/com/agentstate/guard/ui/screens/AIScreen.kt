package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

data class AIState(
    val status: String = "Not configured",
    val severity: String = "low",
    val summary: String = "Configure an AI provider on Desktop to enable analysis.",
    val possibleCauses: List<String> = emptyList(),
    val recommendedChecks: List<String> = emptyList(),
)

@Composable
fun AIScreen(state: AIState = AIState()) {
    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text("AI Monitor", style = MaterialTheme.typography.headlineSmall)
        Text("Status: ${state.status}", style = MaterialTheme.typography.titleSmall,
            color = when(state.status) {
                "Not configured" -> MaterialTheme.colorScheme.outline
                "ok" -> MaterialTheme.colorScheme.primary
                else -> MaterialTheme.colorScheme.error
            })

        Card(modifier = Modifier.fillMaxWidth()) {
            Text(state.summary, modifier = Modifier.padding(16.dp))
        }

        if (state.recommendedChecks.isNotEmpty()) {
            Text("Recommended Checks", style = MaterialTheme.typography.titleSmall)
            state.recommendedChecks.forEach { check ->
                Text("• $check", style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(start = 8.dp))
            }
        }
    }
}
