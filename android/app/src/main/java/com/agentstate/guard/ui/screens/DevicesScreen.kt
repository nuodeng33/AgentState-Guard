package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
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
import com.agentstate.guard.ui.state.ConnectionUiState
import com.agentstate.guard.ui.state.LinkedDesktop
import com.agentstate.guard.ui.theme.Spacing

@Composable
fun DevicesScreen(
    linked: LinkedDesktop?,
    connection: ConnectionUiState = ConnectionUiState.EMPTY,
    busy: Boolean = false,
    failureCode: String? = null,
    onRefresh: () -> Unit = {},
    onUnpair: () -> Unit = {},
    onScanQr: () -> Unit = {},
) {
    var confirming by remember { mutableStateOf(false) }
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(stringResource(R.string.more_devices), style = MaterialTheme.typography.headlineSmall)
        if (linked == null) {
            EmptyStateCard(title = stringResource(R.string.home_not_connected))
            OutlinedButton(onClick = onScanQr, enabled = !busy) { Text(stringResource(R.string.action_scan_qr)) }
        } else {
            Text(linked.displayName)
            Text(connection.desktopUuid ?: linked.id)
            connection.endpoint?.let { Text(it) }
            connection.signingFingerprint?.let { Text(it) }
            Text(stringResource(when {
                connection.authRequired -> R.string.connection_auth_required
                connection.online -> R.string.connection_live
                else -> R.string.connection_offline
            }))
            if (connection.lastKnown) Text(stringResource(R.string.connection_last_known))
            connection.reasonCode?.let { Text(it) }
            connection.observedAt?.let { Text(stringResource(R.string.connection_observed_at, it)) }
            connection.syncedAtEpochMs?.let { Text(stringResource(R.string.connection_last_synced, it.toString())) }
            OutlinedButton(onClick = { confirming = true }, enabled = !busy) { Text(stringResource(R.string.unpair_action)) }
        }
        failureCode?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        OutlinedButton(onClick = onRefresh, enabled = !busy) { Text(stringResource(R.string.action_refresh)) }
    }
    if (confirming && linked != null) AlertDialog(
        onDismissRequest = { if (!busy) confirming = false },
        title = { Text(stringResource(R.string.unpair_confirm_title)) },
        text = { Text(stringResource(R.string.unpair_confirm_body)) },
        confirmButton = { TextButton(enabled = !busy, onClick = { confirming = false; onUnpair() }) { Text(stringResource(R.string.action_confirm)) } },
        dismissButton = { TextButton(enabled = !busy, onClick = { confirming = false }) { Text(stringResource(R.string.action_cancel)) } },
    )
}
