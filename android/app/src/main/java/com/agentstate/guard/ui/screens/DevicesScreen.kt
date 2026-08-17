package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.components.EmptyStateCard
import com.agentstate.guard.ui.components.FreshnessCaption
import com.agentstate.guard.ui.components.SectionHeader
import com.agentstate.guard.ui.state.ConnectionUiState
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.LinkedDesktop
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.Surface as SurfaceColor
import com.agentstate.guard.ui.theme.TextSecondary

/**
 * Devices — one bound desktop, connection facts, and the unpair action.
 *
 * Authenticated unpair first revokes the durable desktop binding, then deletes
 * this device's local binding and KeyStore identity. A failure preserves local
 * trust for retry; a success requires a full new QR + SAS pairing.
 */
@Composable
fun DevicesScreen(
    linked: LinkedDesktop?,
    connection: ConnectionUiState?,
    isUnpairing: Boolean = false,
    unpairFailureReasonCode: String? = null,
    onUnpair: () -> Unit = {},
) {
    var confirmUnpair by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.more_devices),
            style = MaterialTheme.typography.headlineSmall,
        )
        if (linked == null) {
            EmptyStateCard(title = stringResource(R.string.home_not_connected))
        } else {
            // Connected-computer facts, verbatim from the local binding.
            SectionHeader(stringResource(R.string.devices_connected_computer))
            Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(Spacing.l),
                    verticalArrangement = Arrangement.spacedBy(Spacing.xs),
                ) {
                    FactRowText(stringResource(R.string.about_bound_uuid), linked.desktopUuid)
                    FactRowText(
                        stringResource(R.string.about_signing_fingerprint),
                        linked.signingFingerprint,
                    )
                    FactRowText(stringResource(R.string.about_endpoint), linked.endpoint)
                    connection?.let { state -> ConnectionFacts(state) }
                }
            }

            SectionHeader(stringResource(R.string.unpair_section_title))
            OutlinedButton(
                onClick = { confirmUnpair = true },
                enabled = !isUnpairing,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    if (isUnpairing) stringResource(R.string.state_loading)
                    else stringResource(R.string.unpair_action),
                )
            }
            unpairFailureReasonCode?.let { code ->
                Text(
                    stringResource(R.string.action_failed) + ": " + code,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
    }

    if (confirmUnpair) {
        AlertDialog(
            onDismissRequest = { confirmUnpair = false },
            title = { Text(stringResource(R.string.unpair_confirm_title)) },
            text = { Text(stringResource(R.string.unpair_confirm_body)) },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmUnpair = false
                        onUnpair()
                    },
                ) {
                    Text(stringResource(R.string.unpair_action))
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmUnpair = false }) {
                    Text(stringResource(R.string.action_cancel))
                }
            },
        )
    }
}

/** Connection facts that change liveness, shown below the durable ones. */
@Composable
private fun ConnectionFacts(state: ConnectionUiState) {
    val liveState = when {
        state.online -> stringResource(R.string.connection_live)
        state.phase == DataPhase.UNREACHABLE || state.phase == DataPhase.EMPTY ->
            stringResource(R.string.connection_offline)
        else -> stringResource(R.string.connection_unreachable)
    }
    Text(
        stringResource(R.string.about_connection_state) + ": " + liveState,
        style = MaterialTheme.typography.bodySmall,
        color = TextSecondary,
    )
    FreshnessCaption(
        lastKnown = state.lastKnown,
        observedAt = state.observedAt,
        syncedAtEpochMs = state.syncedAtEpochMs,
    )
    if (state.authRequired) {
        Text(
            stringResource(R.string.connection_auth_required),
            style = MaterialTheme.typography.bodySmall,
            color = TextSecondary,
        )
    }
    FactRowText(
        stringResource(R.string.about_reason_code),
        state.reasonCode,
    )
}

@Composable
private fun FactRowText(label: String, value: String?) {
    if (value != null) {
        Row(modifier = Modifier.fillMaxWidth()) {
            Text(
                label,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.weight(1f),
            )
            Text(
                value,
                style = MaterialTheme.typography.labelSmall,
                color = TextSecondary,
            )
        }
    }
}
