package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
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
import com.agentstate.guard.ui.state.RecoveryUiState
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.Surface as SurfaceColor
import com.agentstate.guard.ui.theme.toneForMachineState

/** Recovery: shows the projected recovery level verbatim, or an honest empty. */
@Composable
fun RecoveryScreen(state: RecoveryUiState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.more_recovery),
            style = MaterialTheme.typography.headlineSmall,
        )
        DataStateHost(
            phase = state.phase,
            loadingText = stringResource(R.string.state_loading),
            emptyText = stringResource(R.string.state_no_data),
        ) {
            Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
                Column(modifier = Modifier.padding(Spacing.l)) {
                    Text(
                        stringResource(R.string.home_recovery_status),
                        style = MaterialTheme.typography.titleSmall,
                    )
                    state.recoveryLevel?.let { level ->
                        StatusBadge(label = level, tone = toneForMachineState(level))
                    }
                    state.reasonCode?.let { code ->
                        Text(code, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
    }
}
