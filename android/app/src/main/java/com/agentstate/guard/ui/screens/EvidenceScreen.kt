package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.state.EvidenceUiState
import com.agentstate.guard.ui.theme.Spacing

/** Exact-event read only. Unavailable responses never render authoritative details. */
@Composable
fun EvidenceScreen(state: EvidenceUiState, onBack: () -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m)) {
        Text(stringResource(R.string.evidence_title)); Text(state.status ?: state.phase.name)
        state.reasonCode?.let { Text(it) }
        state.eventId?.let { Text("${stringResource(R.string.evidence_event_id)}: $it") }
        if (state.status == "AVAILABLE") {
            state.eventType?.let { Text("${stringResource(R.string.evidence_event_type)}: $it") }
            state.result?.let { Text("${stringResource(R.string.evidence_result)}: $it") }
            state.observedAt?.let { Text("${stringResource(R.string.evidence_observed_at)}: $it") }
            state.checkpointId?.let { Text("${stringResource(R.string.evidence_checkpoint)}: $it") }
        }
        OutlinedButton(onClick = onBack) { Text(stringResource(R.string.action_back)) }
    }
}
