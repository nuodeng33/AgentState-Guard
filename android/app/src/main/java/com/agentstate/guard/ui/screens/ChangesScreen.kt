package com.agentstate.guard.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.components.DataStateHost
import com.agentstate.guard.ui.components.FreshnessCaption
import com.agentstate.guard.ui.components.StatusBadge
import com.agentstate.guard.ui.state.ChangeUi
import com.agentstate.guard.ui.state.ChangesUiState
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.Surface as SurfaceColor
import com.agentstate.guard.ui.theme.TextSecondary
import com.agentstate.guard.ui.theme.toneForMachineState

/**
 * Changes: adapter-supplied verified activities. Items with a server-issued
 * [ChangeUi.eventId] can drill into sanitized evidence (navigation handled
 * by App.kt); items without one render inert — no fabricated details.
 */
@Composable
fun ChangesScreen(
    state: ChangesUiState,
    onOpenEvidence: (eventId: String) -> Unit = {},
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.nav_changes),
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
            emptyText = stringResource(R.string.empty_changes),
        ) {
            state.items.forEach { change ->
                ChangeRow(change = change, onOpenEvidence = onOpenEvidence)
            }
        }
    }
}

@Composable
private fun ChangeRow(change: ChangeUi, onOpenEvidence: (String) -> Unit) {
    val cardModifier = Modifier
        .fillMaxWidth()
        .let { base ->
            when (val eventId = change.eventId) {
                null -> base
                else -> base.clickable { onOpenEvidence(eventId) }
            }
        }
    Card(
        modifier = cardModifier,
        colors = CardDefaults.cardColors(containerColor = SurfaceColor),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(Spacing.l),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(change.file, style = MaterialTheme.typography.titleSmall)
                    Text(
                        "${change.changeType} · ${change.whenText}",
                        style = MaterialTheme.typography.bodySmall,
                        color = TextSecondary,
                    )
                }
                change.result?.let { result ->
                    StatusBadge(label = result, tone = toneForMachineState(result))
                }
            }
            change.reasonCode?.let { code ->
                Text(
                    code,
                    style = MaterialTheme.typography.labelSmall,
                    color = TextSecondary,
                )
            }
            if (change.eventId != null) {
                Text(
                    stringResource(R.string.action_view_evidence),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
        }
    }
}
