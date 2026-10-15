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
import com.agentstate.guard.ui.components.EmptyStateCard
import com.agentstate.guard.ui.state.LinkedDesktop
import com.agentstate.guard.ui.theme.Spacing

/** Devices: the linked desktop if any; otherwise the honest unpaired note. */
@Composable
fun DevicesScreen(linked: LinkedDesktop?) {
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
            EmptyStateCard(
                title = linked.displayName,
                detail = linked.id,
            )
        }
    }
}
