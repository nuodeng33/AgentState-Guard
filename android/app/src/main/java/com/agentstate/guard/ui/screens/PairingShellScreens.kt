package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.components.EmptyStateCard
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.TextSecondary

/**
 * Scan shell: the real camera scanner lands with the Device Link stage.
 * This screen only holds the entry state; it never parses QR payloads.
 */
@Composable
fun ScanConnectScreen(onBack: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.pairing_scan_title),
            style = MaterialTheme.typography.headlineSmall,
        )
        EmptyStateCard(
            title = stringResource(R.string.pairing_scan_title),
            detail = stringResource(R.string.pairing_scan_shell),
        )
        OutlinedButton(onClick = onBack, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.action_back))
        }
    }
}

/** LAN discovery shell: the discovery transport is a later Device Link stage. */
@Composable
fun LanDiscoveryScreen(onBack: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.pairing_lan_title),
            style = MaterialTheme.typography.headlineSmall,
        )
        EmptyStateCard(
            title = stringResource(R.string.pairing_waiting),
            detail = stringResource(R.string.pairing_lan_shell),
        )
        OutlinedButton(onClick = onBack, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.action_back))
        }
    }
}

/**
 * Manual address shell: collects an address string and hands it to the
 * adapter. The UI performs no connection itself.
 */
@Composable
fun ManualAddressScreen(onSubmit: (String) -> Unit, onBack: () -> Unit) {
    var address by remember { mutableStateOf("") }
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.pairing_manual_title),
            style = MaterialTheme.typography.headlineSmall,
        )
        Text(
            stringResource(R.string.pairing_manual_hint),
            style = MaterialTheme.typography.bodySmall,
            color = TextSecondary,
        )
        OutlinedTextField(
            value = address,
            onValueChange = { address = it },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            onClick = { if (address.isNotBlank()) onSubmit(address.trim()) },
            enabled = address.isNotBlank(),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(stringResource(R.string.pairing_manual_connect))
        }
        OutlinedButton(onClick = onBack, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.action_back))
        }
    }
}
