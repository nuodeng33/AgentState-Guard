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
import com.agentstate.guard.ui.components.FreshnessCaption
import com.agentstate.guard.ui.state.CheckpointsUiState
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.Surface as SurfaceColor
import com.agentstate.guard.ui.theme.TextSecondary

/** Checkpoints: adapter-supplied list plus an honest empty state. */
@Composable
fun CheckpointsScreen(state: CheckpointsUiState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.more_checkpoints),
            style = MaterialTheme.typography.headlineSmall,
        )
        FreshnessCaption(
            lastKnown = state.lastKnown,
            observedAt = state.observedAt,
            syncedAtEpochMs = state.syncedAtEpochMs,
        )
        DataStateHost(
            phase = state.phase,
            loadingText = stringResource(R.string.state_loading),
            emptyText = stringResource(R.string.empty_checkpoints),
            lastKnown = state.lastKnown,
            reasonCode = state.reasonCode,
        ) {
            state.items.forEach { checkpoint ->
                Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(Spacing.l),
                    ) {
                        Text("#${checkpoint.id} ${checkpoint.label}", style = MaterialTheme.typography.titleSmall)
                        Text(
                            checkpoint.createdAt,
                            style = MaterialTheme.typography.bodySmall,
                            color = TextSecondary,
                        )
                    }
                }
            }
        }
    }
}
