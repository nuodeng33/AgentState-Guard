package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
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
import com.agentstate.guard.ui.state.ChangesUiState
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.Surface as SurfaceColor
import com.agentstate.guard.ui.theme.TextSecondary

/** Changes: adapter-supplied list plus an honest empty state. */
@Composable
fun ChangesScreen(state: ChangesUiState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.nav_changes),
            style = MaterialTheme.typography.headlineSmall,
        )
        DataStateHost(
            phase = state.phase,
            loadingText = stringResource(R.string.state_loading),
            emptyText = stringResource(R.string.empty_changes),
        ) {
            state.items.forEach { change ->
                Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(Spacing.l),
                    ) {
                        Text(change.file, style = MaterialTheme.typography.titleSmall)
                        Text(
                            "${change.changeType} · ${change.whenText}",
                            style = MaterialTheme.typography.bodySmall,
                            color = TextSecondary,
                        )
                    }
                }
            }
        }
    }
}
