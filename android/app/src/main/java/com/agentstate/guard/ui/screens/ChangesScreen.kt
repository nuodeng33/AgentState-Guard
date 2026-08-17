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
            lastKnown = state.lastKnown,
            reasonCode = state.reasonCode,
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
                    val summary = listOfNotNull(
                        change.changeKind,
                        change.changeType,
                        change.whenText,
                    ).filter { it.isNotBlank() }.joinToString(" · ")
                    if (summary.isNotBlank()) {
                        Text(
                            summary,
                            style = MaterialTheme.typography.bodySmall,
                            color = TextSecondary,
                        )
                    }
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
            ChangeDetail(R.string.change_actor, change.actor)
            ChangeDetail(R.string.evidence_checkpoint, change.checkpointId)
            ChangeDetail(R.string.evidence_verification, change.verificationSummary)
            ChangeDetail(R.string.fact_execution_domain, change.executionDomainId)
            ChangeDetail(R.string.fact_attribution, change.attribution)
            ChangeDetail(R.string.fact_change_kind, change.changeKind)
            ChangeDetail(R.string.fact_coverage_before, change.coverageBefore)
            ChangeDetail(R.string.fact_coverage_after, change.coverageAfter)
            ChangeDetail(R.string.fact_recovery_disposition, change.recoveryDisposition)
            ChangeDetail(R.string.fact_workspace, change.workspaceId)
            ChangeDetail(
                R.string.evidence_affected_objects,
                change.affectedObjects?.takeIf { it.isNotEmpty() }?.joinToString(" · "),
            )
            ChangeDetail(
                R.string.evidence_related_refs,
                change.evidenceRefs?.takeIf { it.isNotEmpty() }?.joinToString(" · "),
            )
                Text(
                    stringResource(R.string.action_view_evidence),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
        }
    }
}

@Composable
private fun ChangeDetail(labelRes: Int, value: String?) {
    value ?: return
    Text(
        stringResource(labelRes) + ": " + value,
        style = MaterialTheme.typography.labelSmall,
        color = TextSecondary,
    )
}
