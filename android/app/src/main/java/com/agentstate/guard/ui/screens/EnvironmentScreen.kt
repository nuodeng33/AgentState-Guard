package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.components.DataStateHost
import com.agentstate.guard.ui.components.StatusBadge
import com.agentstate.guard.ui.state.EnvironmentUiState
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.Surface as SurfaceColor
import com.agentstate.guard.ui.theme.TextSecondary
import com.agentstate.guard.ui.theme.toneForMachineState

/**
 * Environment. Fully state-driven: items come from the adapter projection,
 * statuses are machine tokens rendered verbatim, and the empty phase is an
 * honest "no environment data" — never a compiled-in fake list.
 */
@Composable
fun EnvironmentScreen(state: EnvironmentUiState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.nav_environment),
            style = MaterialTheme.typography.headlineSmall,
        )
        DataStateHost(
            phase = state.phase,
            loadingText = stringResource(R.string.state_loading),
            emptyText = stringResource(R.string.empty_environment),
        ) {
            state.items.forEach { item ->
                Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = Spacing.l, vertical = Spacing.m),
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(item.name, style = MaterialTheme.typography.titleSmall)
                            Text(
                                item.version ?: "—",
                                style = MaterialTheme.typography.bodySmall,
                                color = TextSecondary,
                            )
                        }
                        StatusBadge(label = item.status, tone = toneForMachineState(item.status))
                    }
                }
            }
        }
    }
}
