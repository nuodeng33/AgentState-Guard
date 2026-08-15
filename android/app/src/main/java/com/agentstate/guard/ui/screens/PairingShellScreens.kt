package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.components.EmptyStateCard
import com.agentstate.guard.ui.theme.Spacing

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
