package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.components.DataStateHost
import com.agentstate.guard.ui.components.EmptyStateCard
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.SupervisionUiState
import com.agentstate.guard.ui.theme.Spacing

/**
 * Supervision: pending-count projection only. Sessions stay on the desktop;
 * this surface never invents pending work.
 */
@Composable
fun SupervisionScreen(state: SupervisionUiState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.nav_supervision),
            style = MaterialTheme.typography.headlineSmall,
        )
        DataStateHost(
            phase = state.phase,
            loadingText = stringResource(R.string.state_loading),
            emptyText = stringResource(R.string.empty_supervision),
        ) {
            if (state.phase == DataPhase.CONNECTED && state.pendingCount == 0) {
                EmptyStateCard(title = stringResource(R.string.empty_supervision))
            } else {
                Text(
                    "${stringResource(R.string.home_pending_supervision)}: ${state.pendingCount}",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
    }
}
