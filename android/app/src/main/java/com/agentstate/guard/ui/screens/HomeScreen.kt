package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.components.FreshnessCaption
import com.agentstate.guard.ui.components.SectionHeader
import com.agentstate.guard.ui.components.StatusBadge
import com.agentstate.guard.ui.state.AiAdvisoryUiState
import com.agentstate.guard.ui.state.HomeUiState
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.Surface as SurfaceColor
import com.agentstate.guard.ui.theme.TextSecondary
import com.agentstate.guard.ui.theme.toneForMachineState

/**
 * Home. Unpaired: brand + the scan-QR connection entry. Paired: the desktop
 * projection rendered verbatim — when nothing has arrived yet the card says
 * "no data yet / waiting for computer state" instead of fabricating data.
 */
@Composable
fun HomeScreen(
    paired: Boolean?,
    state: HomeUiState?,
    aiAdvisory: AiAdvisoryUiState? = null,
    onScanQr: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.app_name),
            style = MaterialTheme.typography.headlineSmall,
        )

        when (paired) {
            null -> Text(
                stringResource(R.string.state_loading),
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )
            false -> UnpairedHome(onScanQr = onScanQr)
            true -> ConnectedHome(state)
        }

        // Advisory only — it is not an authority and never a standalone product.
        aiAdvisory?.let { advisory ->
            AiAdvisoryCard(state = advisory)
        }
    }
}

@Composable
private fun UnpairedHome(onScanQr: () -> Unit) {
    Text(
        stringResource(R.string.home_tagline),
        style = MaterialTheme.typography.bodyMedium,
        color = TextSecondary,
    )
    Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(Spacing.l),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                stringResource(R.string.home_not_connected),
                style = MaterialTheme.typography.titleMedium,
            )
        }
    }
    Button(onClick = onScanQr, modifier = Modifier.fillMaxWidth()) {
        Text(stringResource(R.string.action_scan_qr))
    }
    Text(
        stringResource(R.string.pairing_first_hint),
        style = MaterialTheme.typography.bodySmall,
        color = TextSecondary,
    )
}

@Composable
private fun ConnectedHome(state: HomeUiState?) {
    SectionHeader(stringResource(R.string.home_overall))
    Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
        Column(modifier = Modifier.padding(Spacing.l)) {
            Text(
                stringResource(R.string.state_connected_computer),
                style = MaterialTheme.typography.titleSmall,
            )
            Text(
                state?.desktopName ?: stringResource(R.string.state_no_data),
                style = MaterialTheme.typography.bodyMedium,
            )
            val overall = state?.overallStatus
            if (overall != null) {
                StatusBadge(label = overall, tone = toneForMachineState(overall))
            } else {
                Text(
                    stringResource(R.string.state_waiting_desktop),
                    style = MaterialTheme.typography.bodySmall,
                    color = TextSecondary,
                )
            }
            if (state != null && (state.lastKnown || state.observedAt != null)) {
                FreshnessCaption(
                    lastKnown = state.lastKnown,
                    observedAt = state.observedAt,
                    syncedAtEpochMs = state.syncedAtEpochMs,
                )
            }
        }
    }
    SummaryRow(stringResource(R.string.home_runtime), state?.runtimeSummary)
    SummaryRow(stringResource(R.string.home_agents), state?.agentsSummary)
    SummaryRow(
        stringResource(R.string.home_pending_supervision),
        state?.pendingSupervision?.toString(),
    )
    SummaryRow(stringResource(R.string.home_changes), state?.changesCount?.toString())
    SummaryRow(stringResource(R.string.home_checkpoints), state?.lastCheckpoint)
    SummaryRow(stringResource(R.string.home_ai_supervisor), state?.aiStatus)
    SummaryRow(stringResource(R.string.home_recovery_status), state?.recoveryStatus)
}

@Composable
private fun SummaryRow(label: String, value: String?) {
    Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = Spacing.l, vertical = Spacing.m),
        ) {
            Text(label, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            Text(
                value ?: stringResource(R.string.state_no_data),
                style = MaterialTheme.typography.bodySmall,
                color = if (value == null) TextSecondary else MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}
