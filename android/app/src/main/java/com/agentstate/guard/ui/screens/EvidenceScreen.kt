package com.agentstate.guard.ui.screens

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
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.components.DataStateHost
import com.agentstate.guard.ui.components.EmptyStateCard
import com.agentstate.guard.ui.components.SectionHeader
import com.agentstate.guard.ui.components.StatusBadge
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.EvidenceUiState
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.Surface as SurfaceColor
import com.agentstate.guard.ui.theme.TextSecondary
import com.agentstate.guard.ui.theme.toneForMachineState

/**
 * Evidence detail for one ledger event. Everything shown is verbatim from
 * the backend sanitized DTO; missing fields stay invisible — no raw payload,
 * no raw ledger, nothing reconstructed or guessed. NOT_FOUND, UNAVAILABLE,
 * and DEGRADED stay explicit.
 */
@Composable
fun EvidenceScreen(eventId: String, state: EvidenceUiState?, onBack: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.evidence_title),
            style = MaterialTheme.typography.headlineSmall,
        )
        when {
            state == null -> Text(
                stringResource(R.string.state_loading),
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )
            state.phase == DataPhase.EMPTY || state.status == "NOT_FOUND" -> EmptyStateCard(
                title = stringResource(R.string.evidence_not_found),
                detail = listOfNotNull(
                    state.reasonCode,
                    state.eventId ?: eventId,
                ).joinToString(" · "),
            )
            state.phase == DataPhase.ERROR || state.status == "UNAVAILABLE" -> EmptyStateCard(
                title = stringResource(R.string.evidence_unavailable),
                detail = state.reasonCode,
            )
            else -> DataStateHost(
                phase = state.phase,
                loadingText = stringResource(R.string.state_loading),
                emptyText = stringResource(R.string.evidence_not_found),
                reasonCode = state.reasonCode,
            ) {
                state.status?.let { status ->
                    StatusBadge(label = status, tone = toneForMachineState(status))
                }
                FactCard(state)
                val hasWorkspaceFacts = listOf(
                    state.executionDomainId,
                    state.attribution,
                    state.changeKind,
                    state.coverageBefore,
                    state.coverageAfter,
                    state.recoveryDisposition,
                    state.workspaceId,
                ).any { it != null }
                if (hasWorkspaceFacts) {
                    SectionHeader(stringResource(R.string.evidence_workspace_facts))
                    state.executionDomainId?.let {
                        DetailRow(R.string.fact_execution_domain, it)
                    }
                    state.attribution?.let { DetailRow(R.string.fact_attribution, it) }
                    state.changeKind?.let { DetailRow(R.string.fact_change_kind, it) }
                    state.coverageBefore?.let { DetailRow(R.string.fact_coverage_before, it) }
                    state.coverageAfter?.let { DetailRow(R.string.fact_coverage_after, it) }
                    state.recoveryDisposition?.let {
                        DetailRow(R.string.fact_recovery_disposition, it)
                    }
                    state.workspaceId?.let { DetailRow(R.string.fact_workspace, it) }
                }
                if (!state.verificationSummary.isNullOrEmpty()) {
                    SectionHeader(stringResource(R.string.evidence_verification))
                    state.verificationSummary.forEach { line ->
                        Text(line, style = MaterialTheme.typography.bodySmall)
                    }
                }
                if (!state.affectedObjects.isNullOrEmpty()) {
                    SectionHeader(stringResource(R.string.evidence_affected_objects))
                    Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
                        Column(modifier = Modifier.padding(Spacing.l)) {
                            state.affectedObjects.forEach { obj ->
                                Text(
                                    obj,
                                    style = MaterialTheme.typography.labelSmall,
                                    color = TextSecondary,
                                )
                            }
                        }
                    }
                }
                state.relatedEvidenceRefs?.takeIf { it.isNotEmpty() }?.let { refs ->
                    SectionHeader(stringResource(R.string.evidence_related_refs))
                    refs.forEach { ref ->
                        Text(
                            ref,
                            style = MaterialTheme.typography.labelSmall,
                            color = TextSecondary,
                        )
                    }
                }
                SectionHeader(stringResource(R.string.evidence_times))
                state.observedAt?.let { DetailRow(R.string.evidence_observed_at, it) }
                state.recordedAt?.let { DetailRow(R.string.evidence_recorded_at, it) }
                SectionHeader(stringResource(R.string.evidence_references))
                state.chainRef?.let { DetailRow(R.string.evidence_chain_ref, it) }
                state.checkpointId?.let { DetailRow(R.string.evidence_checkpoint, it) }
                state.changeId?.let { DetailRow(R.string.evidence_change_id, it) }
                state.reasonCode?.let { DetailRow(R.string.evidence_reason_code, it) }
            }
        }
        OutlinedButton(onClick = onBack, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.action_done))
        }
    }
}

/** The small set of fields that identify the event itself. */
@Composable
private fun FactCard(state: EvidenceUiState) {
    Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(Spacing.l),
            verticalArrangement = Arrangement.spacedBy(Spacing.xs),
        ) {
            state.eventId?.let { DetailRow(R.string.evidence_event_id, it) }
            state.eventType?.let { DetailRow(R.string.evidence_event_type, it) }
            state.source?.let { DetailRow(R.string.evidence_source, it) }
            state.subject?.let { DetailRow(R.string.evidence_subject, it) }
            state.result?.let {
                Text(
                    "${stringResource(R.string.evidence_result)}: $it",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}

/** One "label value" line; values render verbatim and can wrap. */
@Composable
private fun DetailRow(labelRes: Int, value: String) {
    Row(modifier = Modifier.fillMaxWidth()) {
        Text(
            stringResource(labelRes),
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
